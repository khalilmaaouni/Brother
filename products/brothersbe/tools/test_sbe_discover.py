#!/usr/bin/env python3
"""Tests for sbe_discover.py, the read only method discovery.

Every test here asserts a rule the integration direction depends on, not just
that the code runs. The rules, and why each one has a test:

  - An absent method must produce NOTHING, not a warning and not a NO-DATA line.
    A tree with no method installed is not a tree missing evidence, and the
    difference is the second law of the north star: never be a dependency in the
    other direction.
  - The tool must never write. It is pointed at other people's repositories.
  - An unconfirmed path must stay marked as a lead. The research separated facts
    from leads and this file must not lose that distinction by rendering both the
    same way.
  - The test module's output must be found BY NAME anywhere in the tree, because
    its configured root was never confirmed. Assuming a root is the mistake this
    project has already paid for.

Calibration note, following this project's own rule: each assertion below was
checked by removing the behaviour it tests and observing the failure, not by
being written and assumed to bite.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "sbe_discover.py")
REPO_ROOT = os.path.dirname(HERE)


def session_start_command(hooks_json_path):
    """The SessionStart command hooks.json actually declares.

    Reading it from the file, rather than hardcoding a filename, is the
    point: a future rename fails this test by name (a KeyError pointing at
    hooks.json) instead of silently testing a file nobody declares anymore.
    """
    with open(hooks_json_path) as f:
        hooks = json.load(f)
    return hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]


def run(root, *args):
    p = subprocess.run([sys.executable, TOOL, root] + list(args),
                       capture_output=True, text=True)
    return p.returncode, p.stdout


class TestDiscover(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make(self, rel, body="x"):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(body)
        return p

    def test_an_empty_tree_finds_nothing_and_exits_zero(self):
        code, out = run(self.root)
        self.assertEqual(code, 0)
        self.assertIn("no development method artifact found", out)

    def test_an_absent_method_is_never_reported_as_missing_evidence(self):
        """The second law: absent is not the same sentence as missing.

        A NO-DATA line here would be wrong. NO-DATA means evidence that should
        exist and does not. A method nobody installed owes us nothing.
        """
        _code, out = run(self.root)
        self.assertNotIn("NO-DATA", out)
        for word in ("install", "recommend", "should"):
            self.assertNotIn(word, out.lower(),
                             "an empty tree must not advertise anything: %r" % word)

    def test_it_writes_nothing_into_the_tree_it_reads(self):
        self.make(os.path.join(".planning", "ROADMAP.md"))
        before = sorted(os.walk(self.root).__next__()[1] + os.walk(self.root).__next__()[2])
        run(self.root)
        after = sorted(os.walk(self.root).__next__()[1] + os.walk(self.root).__next__()[2])
        self.assertEqual(before, after, "discovery must never write into a tree it reads")

    def test_a_present_method_is_reported_with_its_path(self):
        self.make(os.path.join("docs", "superpowers", "plans", "2026-01-01-a.md"))
        code, out = run(self.root)
        self.assertEqual(code, 0)
        self.assertIn("superpowers", out)
        self.assertIn(os.path.join("docs", "superpowers", "plans"), out)

    def test_an_unconfirmed_path_is_marked_as_a_lead(self):
        """The legacy BMAD paths came from one source describing an old layout.

        Rendering them identically to a confirmed path would launder a lead into
        a fact, which is the failure class this project keeps meeting.
        """
        self.make(os.path.join("docs", "prd.md"))
        _code, out = run(self.root)
        self.assertIn("bmad-legacy", out)
        self.assertIn("[lead]", out, "an unconfirmed path must render as a lead")

    def test_the_test_module_output_is_found_by_name_at_any_depth(self):
        """Its configured root was never confirmed, so only the name is searched."""
        self.make(os.path.join("some", "unexpected", "place", "trace_output", "g.yaml"))
        _code, out = run(self.root)
        self.assertIn("bmad-tea", out)
        self.assertIn("trace_output", out)

    def test_the_gate_is_described_as_quoted_never_restated(self):
        """The consumption rule is the heart of the design, so it is asserted.

        Converting somebody else's verdict into ours is laundering; dropping it
        is ignoring. The tool must say which one it does.
        """
        self.make(os.path.join("t", "trace_output", "g.yaml"))
        _code, out = run(self.root)
        self.assertIn("QUOTED", out)
        for state in ("PASS", "CONCERNS", "FAIL", "WAIVED"):
            self.assertIn(state, out)

    def test_json_output_is_parseable_and_carries_the_lead_flag(self):
        self.make(os.path.join("docs", "prd.md"))
        code, out = run(self.root, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("artifacts", data)
        legacy = [a for a in data["artifacts"] if a["method"] == "bmad-legacy"]
        self.assertTrue(legacy, "the legacy artifact should be in the index")
        self.assertFalse(legacy[0]["verified_path"],
                         "an unconfirmed path must carry verified_path false")

    def test_brief_on_an_empty_tree_prints_absolutely_nothing(self):
        """Not a short line. Nothing.

        This mode feeds a session start injection working to a hard character
        cap, and everything competing for that cap is law the session must not
        miss. A line reporting an absence would spend the budget to tell a reader
        something that changes no next action.
        """
        code, out = run(self.root, "--brief")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "", "an absent method must cost zero characters")

    def test_brief_is_one_line_and_stays_small(self):
        self.make(os.path.join("docs", "superpowers", "plans", "2026-01-01-a.md"))
        code, out = run(self.root, "--brief")
        self.assertEqual(code, 0)
        body = out.strip()
        self.assertEqual(len(body.splitlines()), 1, "the injection is ONE line")
        self.assertLess(len(body), 600,
                        "this shares a 10000 character cap with the active-laws digest")

    def test_brief_states_the_two_rules_it_must_not_break(self):
        """The line is the only thing most sessions will read about this.

        If it does not say that external plans are the plan of record and that
        foreign verdicts are quoted rather than restated, a session can read
        "artifacts found" as an invitation to re-plan them, which is the exact
        behaviour the direction forbids.
        """
        self.make(os.path.join(".planning", "ROADMAP.md"))
        _code, out = run(self.root, "--brief")
        self.assertIn("plan of record", out)
        self.assertIn("quoted", out)

    def test_the_session_start_hook_stays_valid_shell(self):
        """The command hooks.json declares for SessionStart must actually run.

        This used to be a syntax check on a shell file, sbe_sessionstart.sh.
        That file was ported to sbe_sessionstart.py on 17 August and
        hooks/hooks.json was updated to declare the python command, but this
        test kept naming the deleted shell file, so it failed for the wrong
        reason (missing file, not a real regression) and would have kept
        passing a broken declared command forever. Reading the command from
        hooks.json rather than hardcoding a filename is what closes that
        gap: a total, silent failure of the thing that injects the laws at
        session start is worth a real execution, not a syntax check on a
        file nobody points at.
        """
        hooks_json = os.path.join(REPO_ROOT, "hooks", "hooks.json")
        command = session_start_command(hooks_json)
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=REPO_ROOT)
        p = subprocess.run(command, shell=True, cwd=REPO_ROOT, env=env,
                           input="", capture_output=True, text=True)
        self.assertEqual(p.returncode, 0,
                         "declared session start command failed: %s" % p.stderr.strip())

    def test_a_missing_directory_argument_is_reported_not_crashed(self):
        code, out = run(os.path.join(self.root, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
