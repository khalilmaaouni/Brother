#!/usr/bin/env python3
"""T1.4 (docs/plan/specs/T1.md, RQ-08 and RQ-09): every gate subprocess is
launched with the hook's git environment stripped, and an environment that
still carries a hook git variable refuses at NO-DATA rather than running in
the wrong repository.

No gate really launches here: _run (and, for one case, hook_safe_env) is
patched to record the environment a sweep would have been handed, so this
suite proves WHAT reaches a gate without paying for, or depending on, the
real cleanse.sh and its siblings. Every temp tree, HOME included, is this
suite's own and is removed after each test.

Run: python3 -B scripts/test_export_hook_env.py
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402

#: The six names the specification lists, typed out here on purpose so a
#: name dropped from (or added to) EP.HOOK_GIT_VARS is caught, not copied.
SPEC_HOOK_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

#: What a pre-push hook in the hub hands every process it starts.
HOOK_VALUES = {
    "GIT_DIR": "/hub/.git",
    "GIT_WORK_TREE": "/hub",
    "GIT_INDEX_FILE": "/hub/.git/index",
    "GIT_OBJECT_DIRECTORY": "/hub/.git/objects",
    "GIT_COMMON_DIR": "/hub/.git",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/hub/.git/objects",
}

#: Names that are not hook git variables and must keep their values.
KEPT = {"PATH": "/usr/bin:/bin", "LANG": "C", "BROTHER_PRIVATE_TERMS": "/t",
        "GIT_AUTHOR_NAME": "Fixture Author"}

#: A candidate tree with every run_gates gate's program present; contents
#: are inert stand-ins, nothing here is ever launched.
CANDIDATE_FILES = {
    "scripts/cleanse.sh": "exit 0\n",
    "docs/codex/HOOKS-MAPPING.md": "stand-in\n",
    "products/fakeprod/tools/test_all.py": "print('inventory ok')\n",
}

#: Inputs that are not a mapping of str names to str values.
HOSTILE_ENVS = (
    None, "GIT_DIR=/hub/.git", b"GIT_DIR", ["GIT_DIR"], ("GIT_DIR",),
    {"GIT_DIR"}, 42, True, 1.5, float("nan"), object(),
    {1: "x"}, {None: "x"}, {("GIT_DIR",): "x"}, {"PATH": None},
    {"PATH": True}, {"PATH": 1}, {"PATH": float("nan")},
    {"PATH": ["/bin"]}, {"GIT_DIR": None}, {"GIT_DIR": b"/hub/.git"},
)


class _UnreadableEnv(dict):
    """A mapping whose items cannot be read."""

    def items(self):
        raise RuntimeError("items refused")


def _write(root, files):
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class _Proc(object):
    returncode = 0
    stdout = "PASS: stub"
    stderr = ""


class _EnvCase(unittest.TestCase):
    """A fresh HOME and temp tree, and a recorder for every launch."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hook-env-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patched = mock.patch.dict(os.environ, {"HOME": home})
        patched.start()
        self.addCleanup(patched.stop)
        for name in SPEC_HOOK_VARS:
            os.environ.pop(name, None)
        self.tree = os.path.join(self.tmp, "candidate")
        _write(self.tree, CANDIDATE_FILES)
        self.identity = os.path.join(self.tmp, "identity")
        os.makedirs(self.identity)

    def gate(self, env, name="sweep"):
        """run_gate with _run recording the env it would have launched
        with. Returns (ok, line, envs)."""
        envs = []

        def fake_run(cmd, cwd, env=None, timeout=120):
            envs.append(env)
            return _Proc()

        with mock.patch.object(EP, "_run", fake_run):
            ok, line = EP.run_gate(["true"], self.tree, name, env=env,
                                   timeout=5, load15=0.0)
        return ok, line, envs

    def gates_under_hook(self):
        """run_gates with this process's environment set the way a hook
        sets it, _run recording every gate's env and the shadow seam off.
        Returns (lines, envs)."""
        envs = []

        def fake_run(cmd, cwd, env=None, timeout=120):
            envs.append(env)
            return _Proc()

        with mock.patch.dict(os.environ, HOOK_VALUES), \
                mock.patch.object(EP, "_run", fake_run), \
                mock.patch.object(EP, "jev_checks", None):
            _, lines = EP.run_gates(self.tree, self.identity)
        return lines, envs


class HookSafeEnv(_EnvCase):
    """RQ-08: every gate subprocess environment is stripped of the hook
    inherited git variables before the gate starts."""

    def test_every_hook_variable_is_removed(self):
        self.assertEqual(tuple(EP.HOOK_GIT_VARS), SPEC_HOOK_VARS)
        raw = dict(HOOK_VALUES, **KEPT)
        clean = EP.hook_safe_env(raw)
        for name in SPEC_HOOK_VARS:
            self.assertNotIn(name, clean)
        self.assertEqual(clean, KEPT)
        # The env a sweep is really handed, from a raw hook env passed in.
        ok, line, envs = self.gate(raw)
        self.assertTrue(ok, line)
        self.assertEqual(len(envs), 1, line)
        self.assertEqual(envs[0], KEPT)
        # ... and from this process's own env, when the caller passes none.
        with mock.patch.dict(os.environ, HOOK_VALUES):
            ok, line, envs = self.gate(None)
        self.assertTrue(ok, line)
        self.assertEqual(len(envs), 1, line)
        for name in SPEC_HOOK_VARS:
            self.assertNotIn(name, envs[0])
        # ... and through run_gates: every gate it launches, none excepted.
        lines, envs = self.gates_under_hook()
        self.assertGreaterEqual(len(envs), 4, lines)
        for env in envs:
            self.assertIsNotNone(env, lines)
            for name in SPEC_HOOK_VARS:
                self.assertNotIn(name, env, lines)
            self.assertEqual(env.get("BROTHER_PRIVATE_TERMS"),
                             os.environ.get("BROTHER_PRIVATE_TERMS")
                             or EP.DEFAULT_TERMS_FILE)

    def test_a_leftover_git_work_tree_is_caught(self):
        raw = {"GIT_DIR": "/hub/.git", "GIT_WORK_TREE": "/hub", "LANG": "C"}
        clean = EP.hook_safe_env(raw)
        self.assertNotIn("GIT_DIR", clean)
        self.assertNotIn("GIT_WORK_TREE", clean)
        self.assertEqual(clean, {"LANG": "C"})
        # One survivor is caught by the assertion, not ignored.
        reason = EP.env_refusal({"GIT_WORK_TREE": "/hub", "LANG": "C"})
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.startswith("NO-DATA"), reason)
        self.assertIn("GIT_WORK_TREE", reason)
        self.assertNotIn("GIT_DIR", reason)
        # A strip that drops GIT_DIR but keeps GIT_WORK_TREE never launches.
        def partial(env):
            kept = dict(env)
            kept.pop("GIT_DIR", None)
            return kept

        with mock.patch.object(EP, "hook_safe_env", partial):
            ok, line, envs = self.gate(raw, name="cleanse")
        self.assertFalse(ok, line)
        self.assertEqual(envs, [], line)
        self.assertTrue(line.startswith("cleanse: exit 2, NO-DATA"), line)
        self.assertIn("GIT_WORK_TREE", line)
        self.assertNotIn("PASS", line)

    def test_a_clean_env_is_unchanged(self):
        before = dict(KEPT)
        clean = EP.hook_safe_env(before)
        self.assertEqual(clean, KEPT)
        self.assertIsNot(clean, before)
        self.assertEqual(before, KEPT)
        self.assertIsNone(EP.env_refusal(clean))
        self.assertEqual(EP.hook_safe_env({}), {})
        self.assertIsNone(EP.env_refusal({}))
        # A git variable that is not one a hook sets is not a hook variable.
        self.assertIn("GIT_AUTHOR_NAME", clean)
        ok, line, envs = self.gate(before)
        self.assertTrue(ok, line)
        self.assertEqual(envs, [KEPT])

    def test_an_empty_value_is_still_removed(self):
        for name in SPEC_HOOK_VARS:
            raw = {name: "", "LANG": "C"}
            self.assertEqual(EP.hook_safe_env(raw), {"LANG": "C"}, name)
            reason = EP.env_refusal(raw)
            self.assertIsNotNone(reason, name)
            self.assertIn(name, reason)

    def test_a_bare_repository_or_the_clone_own_value_is_still_removed(self):
        bare = os.path.join(self.tmp, "bare.git")
        os.makedirs(bare)
        for value in (bare, self.tree, os.path.join(self.tree, ".git")):
            raw = dict((name, value) for name in SPEC_HOOK_VARS)
            raw["LANG"] = "C"
            self.assertEqual(EP.hook_safe_env(raw), {"LANG": "C"}, value)
            self.assertIn("GIT_DIR", EP.env_refusal(raw))

    def test_two_hooks_each_get_their_own_copy(self):
        first = dict(HOOK_VALUES, LANG="C")
        second = dict(HOOK_VALUES, LANG="en_US.UTF-8")
        snapshot = (dict(first), dict(second))
        results = {}
        barrier = threading.Barrier(2)

        def strip(key, env):
            barrier.wait()
            results[key] = EP.hook_safe_env(env)

        threads = [threading.Thread(target=strip, args=("a", first)),
                   threading.Thread(target=strip, args=("b", second))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(results["a"], {"LANG": "C"})
        self.assertEqual(results["b"], {"LANG": "en_US.UTF-8"})
        self.assertIsNot(results["a"], results["b"])
        # Never mutated in place: each hook's own mapping is untouched.
        self.assertEqual((first, second), snapshot)
        results["a"]["LANG"] = "changed"
        self.assertEqual(results["b"]["LANG"], "en_US.UTF-8")
        self.assertEqual(first["LANG"], "C")


class EnvRefusal(_EnvCase):
    """RQ-09: an environment that still carries a hook git variable after
    stripping refuses at NO-DATA rather than running in the wrong
    repository."""

    def test_a_leftover_git_dir_refuses(self):
        reason = EP.env_refusal({"GIT_DIR": "/hub/.git", "LANG": "C"})
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.startswith("NO-DATA"), reason)
        self.assertIn("GIT_DIR", reason)
        for name in SPEC_HOOK_VARS:
            alone = EP.env_refusal({name: HOOK_VALUES[name]})
            self.assertIsNotNone(alone, name)
            self.assertIn(name, alone)
        # A caller whose strip let the raw hook env through never launches.
        with mock.patch.object(EP, "hook_safe_env", dict):
            ok, line, envs = self.gate(dict(HOOK_VALUES, LANG="C"),
                                       name="private_terms_scan")
        self.assertFalse(ok, line)
        self.assertEqual(envs, [], line)
        self.assertTrue(
            line.startswith("private_terms_scan: exit 2, NO-DATA"), line)
        self.assertIn("GIT_DIR", line)
        self.assertNotIn("PASS", line)

    def test_every_survivor_is_named(self):
        reason = EP.env_refusal(dict(HOOK_VALUES))
        for name in SPEC_HOOK_VARS:
            self.assertIn(name, reason)


class HostileEnv(_EnvCase):
    """Hostile input is refused, never accepted and never a TypeError:
    hook_safe_env raises ValueError, env_refusal returns a NO-DATA reason
    (never None), and run_gate refuses at exit 2 without launching."""

    def test_hostile_envs_are_refused_never_accepted(self):
        for hostile in HOSTILE_ENVS + (_UnreadableEnv(PATH="/bin"),):
            label = repr(hostile)
            with self.assertRaises(ValueError, msg=label):
                EP.hook_safe_env(hostile)
            reason = EP.env_refusal(hostile)
            self.assertIsInstance(reason, str, label)
            self.assertTrue(reason.startswith("NO-DATA"), label)
            if hostile is None:
                continue  # run_gate reads None as this process's own env
            ok, line, envs = self.gate(hostile)
            self.assertFalse(ok, label)
            self.assertEqual(envs, [], label)
            self.assertTrue(line.startswith("sweep: exit 2, NO-DATA"), line)
            self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
