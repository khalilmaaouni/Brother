"""brother_run.py's GAP 2 fix (2026-08-30 head-to-head recommendation): a
PLAIN outcome invocation (no --continue, no --resume) against a --cwd that
already holds an unfinished run must never silently start a second,
competing Work. It must resume the unfinished run when the new outcome
matches the recorded one, and otherwise start fresh while printing a
warning naming the unfinished run's outcome.

Mirrors scripts/test_brother_run_continue.py's own structure (same
fixtures, same reused crash rig) since this is the same discovery
machinery (find_unfinished_runs) routed from a different entry point: a
bare invocation instead of the explicit --continue flag.
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as br  # noqa: E402
import test_brother_run as tbr  # noqa: E402
import product_acceptance as pa  # noqa: E402

BROTHER_RUN = tbr.BROTHER_RUN


def _run_dirs_under(runs_root):
    runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
    if not os.path.isdir(runs_dir):
        return []
    return [n for n in os.listdir(runs_dir)
            if os.path.isdir(os.path.join(runs_dir, n))]


def _leave_unfinished(scratch, runs_root, cwd, outcome, unit_id, filename):
    """Same fixture as test_brother_run_continue.py's own helper: a door
    success whose one unit never reaches DONE because the model always
    fails. Cheap and hermetic; the crash MECHANICS are covered separately
    by the real kill rig below."""
    decomposer_body = """
        import json, sys
        sys.stdin.read()
        print(json.dumps([
            {"id": %r, "objective": "create a file",
             "done_check": "test -f %s", "writes": [%r], "deps": []},
        ]))
    """ % (unit_id, filename, filename)
    env = dict(os.environ)
    env["DOOR_MODEL_CMD"] = "%s %s" % (
        sys.executable, tbr.write_stub(scratch, "decomposer.py", decomposer_body))
    env["MODEL_WORKER_CMD"] = "%s %s" % (
        sys.executable, tbr.write_stub(scratch, "failing_model.py", tbr.FAILING_MODEL))
    return tbr.sh([sys.executable, BROTHER_RUN, outcome,
                  "--cwd", cwd, "--runs-root", runs_root], env=env)


class OutcomesMatchHelper(unittest.TestCase):
    """The pure matching rule the routing depends on: exact after whitespace
    normalization, or containment either way. No fuzzy scoring."""

    def test_exact_after_whitespace_and_case_normalization(self):
        self.assertTrue(br._outcomes_match(
            "  Two Files Exist  ", "two   files exist"))

    def test_containment_either_direction_counts_as_a_match(self):
        self.assertTrue(br._outcomes_match(
            "the report ships", "the report ships to finance"))
        self.assertTrue(br._outcomes_match(
            "the report ships to finance", "the report ships"))

    def test_genuinely_different_outcomes_do_not_match(self):
        self.assertFalse(br._outcomes_match(
            "the report ships", "the invoice is archived"))

    def test_empty_outcome_never_matches(self):
        self.assertFalse(br._outcomes_match("", "the report ships"))
        self.assertFalse(br._outcomes_match("the report ships", ""))


class BareInvocationResumesACrashedRun(unittest.TestCase):
    """The spec scenario, driven with NO --continue flag at all: kill
    brother_run mid-second-unit, simulate the lease elapsing, then a bare
    invocation with the SAME outcome sentence resumes instead of starting a
    second, competing run."""

    def test_bare_invocation_with_the_same_outcome_resumes_the_crashed_run(self):
        rig, fail_verdict, fail_evidence = pa._run_and_kill_mid_second_unit(
            "test-bare-resume-crash-")
        self.assertIsNotNone(rig, fail_evidence)

        # Same lease-elapse surgery test_brother_run_continue.py uses:
        # simulate the real 20-minute TTL having passed.
        pa._edit_expires_at(rig["claims_path"], "A2", time.time() - 5)

        run_dirs_before = set(_run_dirs_under(rig["tmp"]))

        proc = tbr.sh([sys.executable, BROTHER_RUN,
                      "two files exist, the second after the first",
                      "--cwd", rig["repo"], "--runs-root", rig["tmp"]],
                     env=rig["env"])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("an unfinished run already covers", out, out)
        self.assertIn("resuming it", out, out)

        self.assertTrue(os.path.exists(os.path.join(rig["repo"], "one.txt")), out)
        self.assertTrue(os.path.exists(os.path.join(rig["repo"], "two.txt")), out)
        self.assertEqual(pa._merge_ids(rig["repo"]), ["A1", "A2"])

        # NO DOUBLE-CLAIM: exactly one run directory (hence one claim
        # store, one Work document) exists for this repository, before and
        # after the bare invocation.
        run_dirs_after = set(_run_dirs_under(rig["tmp"]))
        self.assertEqual(run_dirs_before, run_dirs_after,
                         "a bare invocation with a matching outcome must "
                         "resume the existing run, never create a new one: "
                         "%r -> %r" % (run_dirs_before, run_dirs_after))
        self.assertEqual(len(run_dirs_after), 1, run_dirs_after)


class BareInvocationWithADifferentOutcomeStartsFreshAndWarns(unittest.TestCase):
    def test_a_different_outcome_starts_a_new_run_and_names_the_old_one(self):
        runs_root = tempfile.mkdtemp(prefix="bare-diff-runs-")
        scratch = tempfile.mkdtemp(prefix="bare-diff-scratch-")
        repo = tbr.make_repo(tempfile.mkdtemp(prefix="bare-diff-repo-"))

        proc1 = _leave_unfinished(scratch, runs_root, repo,
                                  "the original unfinished outcome",
                                  "F1", "one-a.txt")
        self.assertNotEqual(proc1.returncode, 0, proc1.stdout + proc1.stderr)
        self.assertEqual(len(_run_dirs_under(runs_root)), 1)

        env = dict(os.environ)
        env["MODEL_WORKER_CMD"] = "%s %s" % (
            sys.executable, tbr.write_stub(scratch, "failing2.py", tbr.FAILING_MODEL))
        proc2 = _leave_unfinished(scratch, runs_root, repo,
                                  "a completely unrelated second outcome",
                                  "F2", "one-b.txt")
        out = proc2.stdout + proc2.stderr

        # Never silent: the second, unrelated outcome must name the first
        # run's outcome in a warning, plainly, without ever naming a run id
        # or directory in that line.
        self.assertIn("an unfinished run exists for this repository", out, out)
        self.assertIn("the original unfinished outcome", out, out)
        self.assertIn("starting a NEW, separate run", out, out)

        # A genuinely different outcome still starts fresh: two run
        # directories now exist, not a silent resume of the first.
        self.assertEqual(len(_run_dirs_under(runs_root)), 2,
                         _run_dirs_under(runs_root))


if __name__ == "__main__":
    unittest.main()
