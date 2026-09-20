"""test_jev_frontdoor_hooks.py: A1.03 (J014). Drives both halves of the
front-door skill-selection seam as real subprocesses over stdin JSON,
the same way the harness actually invokes a hook -- never by importing
and calling their main() in-process, since the property under test is
the JSON-in/exit-0-out CONTRACT itself, not just the Python logic behind it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SIDECAR = os.path.join(HERE, "jev_frontdoor_sidecar.py")
CLASSIFY = os.path.join(HERE, "jev_frontdoor_classify.py")


def _run(script, payload, env_extra=None, tmp_home=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    if tmp_home is not None:
        env["TMPDIR"] = tmp_home
    proc = subprocess.run(
        [sys.executable, script],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=15, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


class FrontDoorHooksContract(unittest.TestCase):
    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="jev-frontdoor-test-")
        self.addCleanup(shutil.rmtree, self.tmp_home, ignore_errors=True)
        self.sidecar_dir = os.path.join(self.tmp_home, "brother-jev-j014-sidecar")

    def test_malformed_stdin_never_raises_and_exits_zero(self):
        proc = subprocess.run([sys.executable, SIDECAR], input="not json",
                              capture_output=True, text=True, timeout=15,
                              env=dict(os.environ, TMPDIR=self.tmp_home))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc2 = subprocess.run([sys.executable, CLASSIFY], input="not json",
                               capture_output=True, text=True, timeout=15,
                               env=dict(os.environ, TMPDIR=self.tmp_home))
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

    def test_no_prompt_field_writes_no_sidecar_file(self):
        rc, _out, err = _run(SIDECAR, {"session_id": "s1"}, tmp_home=self.tmp_home)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.isdir(self.sidecar_dir))

    def test_prompt_is_cached_then_consumed_exactly_once(self):
        rc, _out, err = _run(SIDECAR, {"session_id": "s1", "prompt": "use openrouter for lanes"},
                             tmp_home=self.tmp_home)
        self.assertEqual(rc, 0, err)
        sidecar_path = os.path.join(self.sidecar_dir, "s1.json")
        self.assertTrue(os.path.isfile(sidecar_path))
        with open(sidecar_path) as fh:
            self.assertEqual(json.load(fh)["prompt"], "use openrouter for lanes")

        rc2, _out2, err2 = _run(
            CLASSIFY,
            {"session_id": "s1", "tool_name": "Skill", "tool_input": {"skill": "brother"}},
            tmp_home=self.tmp_home,
        )
        self.assertEqual(rc2, 0, err2)
        # ONE prompt, ONE classification: the sidecar is gone after this,
        # so a second, unrelated Skill call in the same session never
        # reuses a stale cached prompt.
        self.assertFalse(os.path.isfile(sidecar_path))

    def test_non_skill_tool_is_ignored(self):
        _run(SIDECAR, {"session_id": "s2", "prompt": "run the tests"}, tmp_home=self.tmp_home)
        sidecar_path = os.path.join(self.sidecar_dir, "s2.json")
        self.assertTrue(os.path.isfile(sidecar_path))
        rc, _out, err = _run(
            CLASSIFY, {"session_id": "s2", "tool_name": "Bash", "tool_input": {"command": "ls"}},
            tmp_home=self.tmp_home,
        )
        self.assertEqual(rc, 0, err)
        # A non-Skill PostToolUse call must never consume or clear the
        # cached prompt: a later, real Skill call in the same session
        # still needs it.
        self.assertTrue(os.path.isfile(sidecar_path))

    def test_classify_with_no_cached_prompt_is_a_silent_no_op(self):
        rc, _out, err = _run(
            CLASSIFY,
            {"session_id": "never-cached", "tool_name": "Skill", "tool_input": {"skill": "brother"}},
            tmp_home=self.tmp_home,
        )
        self.assertEqual(rc, 0, err)

    def test_sessions_never_cross_read_each_others_prompt(self):
        _run(SIDECAR, {"session_id": "alice", "prompt": "alice's request"}, tmp_home=self.tmp_home)
        _run(SIDECAR, {"session_id": "bob", "prompt": "bob's request"}, tmp_home=self.tmp_home)
        alice_path = os.path.join(self.sidecar_dir, "alice.json")
        bob_path = os.path.join(self.sidecar_dir, "bob.json")
        with open(alice_path) as fh:
            self.assertEqual(json.load(fh)["prompt"], "alice's request")
        with open(bob_path) as fh:
            self.assertEqual(json.load(fh)["prompt"], "bob's request")
        _run(CLASSIFY, {"session_id": "alice", "tool_name": "Skill",
                        "tool_input": {"skill": "brother"}}, tmp_home=self.tmp_home)
        self.assertFalse(os.path.isfile(alice_path))
        self.assertTrue(os.path.isfile(bob_path))  # bob's own prompt is untouched


class DestructiveVeto(unittest.TestCase):
    """_looks_destructive() is pure and has no side effects, same reasoning
    as BucketMapping below for importing it directly rather than a
    subprocess round trip per case."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import jev_frontdoor_classify as C
        self.C = C
        self.addCleanup(sys.path.remove, HERE)

    def test_rm_rf_is_destructive(self):
        self.assertTrue(self.C._looks_destructive("please rm -rf the build dir"))

    def test_force_push_is_destructive(self):
        self.assertTrue(self.C._looks_destructive("just git push --force to main"))

    def test_drop_table_is_destructive(self):
        self.assertTrue(self.C._looks_destructive("DROP TABLE users; then reseed"))

    def test_ordinary_request_is_not_destructive(self):
        self.assertFalse(self.C._looks_destructive("fix the auth timeout bug"))

    def test_empty_or_missing_text_is_not_destructive(self):
        self.assertFalse(self.C._looks_destructive(""))
        self.assertFalse(self.C._looks_destructive(None))

    def test_case_insensitive_match(self):
        self.assertTrue(self.C._looks_destructive("RM -RF /tmp/whatever"))


class BucketMapping(unittest.TestCase):
    """Imports jev_frontdoor_classify directly for this part only: _bucket()
    is pure and has no side effects, so a subprocess round trip per case
    would be needless overhead for what is really a small lookup table."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import jev_frontdoor_classify as C
        self.C = C
        self.addCleanup(sys.path.remove, HERE)

    def test_bare_brother_is_intake(self):
        self.assertEqual(self.C._bucket("brother"), "intake")

    def test_status_like_names_map_to_status(self):
        self.assertEqual(self.C._bucket("brothermode:status"), "status")

    def test_review_like_names_map_to_review(self):
        self.assertEqual(self.C._bucket("brothersbe:verify"), "review")

    def test_handover_like_names_map_to_handover(self):
        self.assertEqual(self.C._bucket("brothermode:handover-pack"), "handover")

    def test_unrecognized_name_is_none_of_the_above_never_a_guess(self):
        self.assertEqual(self.C._bucket("some-totally-unrelated-skill"), "none-of-the-above")

    def test_empty_or_missing_name_is_unknown(self):
        self.assertEqual(self.C._bucket(""), "unknown")
        self.assertEqual(self.C._bucket(None), "unknown")


if __name__ == "__main__":
    unittest.main()
