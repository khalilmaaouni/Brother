import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# The folder's own import form (as test_bm_repair_d16 beside this file): the product gate, tools/test_all.py, runs
# each suite as a script from this folder, where the repository root package does not exist. The hub rows that name
# this suite by its dotted path from the repository root import the same file through this line too.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bm_clock_guard  # noqa: E402
from bm_clock_guard import find_times, is_estimate_labeled, to_minutes, minute_diff  # noqa: E402

class TestGuard(unittest.TestCase):
    def test_find_two_times(self):
        text = "first 09:41 then 23:59"
        found = find_times(text)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0][:3], ("09:41", text.index("09:41"), text.index("09:41") + 5))
        self.assertEqual(found[0][3:], (9, 41))
        self.assertEqual(found[1][:3], ("23:59", text.index("23:59"), text.index("23:59") + 5))
        self.assertEqual(found[1][3:], (23, 59))

    def test_ignore_dates_and_seconds(self):
        self.assertEqual(find_times(""), [])
        self.assertEqual(find_times("9:41"), [])
        for bad in ("24:00", "25:00", "29:59"):
            self.assertEqual(find_times(bad), [])
        for bad in ("14:60", "14:99"):
            self.assertEqual(find_times(bad), [])
        self.assertEqual(find_times("2026-09-20"), [])
        found = find_times("14:22:11")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "14:22")
        self.assertEqual(found[0][3:], (14, 22))

    def test_estimate_pass(self):
        self.assertTrue(is_estimate_labeled("meet at 14:22 estimate", 8, 13))
        self.assertTrue(is_estimate_labeled("meet at 14:22 ESTIMATED", 8, 13))
        self.assertTrue(is_estimate_labeled("approx 14:22", 7, 12))
        self.assertFalse(is_estimate_labeled("beta 14:22", 5, 10))
        self.assertFalse(is_estimate_labeled("14:22", 0, 5))
        ok = "estimate" + (" " * 24) + "14:22"
        bad = "estimate" + (" " * 25) + "14:22"
        self.assertTrue(is_estimate_labeled(ok, ok.index("14:22"), ok.index("14:22") + 5))
        self.assertFalse(is_estimate_labeled(bad, bad.index("14:22"), bad.index("14:22") + 5))

    def test_to_minutes_and_minute_diff(self):
        self.assertEqual(to_minutes(14, 22), 862)
        self.assertEqual(minute_diff(862, 863), 1)
        self.assertEqual(minute_diff(862, 864), 2)
        self.assertEqual(minute_diff(862, 865), 3)
        self.assertEqual(minute_diff(0, 1439), 1)
        self.assertEqual(minute_diff(1439, 0), 1)

    def test_hostile_input_refused(self):
        for bad in (None, 4, [], True):
            with self.assertRaises(ValueError):
                find_times(bad)
        for bad in (None, True, 1):
            with self.assertRaises(ValueError):
                is_estimate_labeled(bad, 0, 0)
        with self.assertRaises(ValueError):
            is_estimate_labeled("14:22", None, 5)
        with self.assertRaises(ValueError):
            is_estimate_labeled("14:22", 0, None)
        for bad in (None, "1", True):
            with self.assertRaises(ValueError):
                to_minutes(bad, 0)
        for bad in (None, "1", True):
            with self.assertRaises(ValueError):
                minute_diff(bad, 0)


class TestRepoScopeGate(unittest.TestCase):
    """The guard honours the same per-repository scoping every other hook
    honours (E76 hooks off, E50 scoped install), and it is proven at the
    entry point, as a real subprocess of tools/bm_clock_guard.py, never at
    a helper. Before 2026-09-30 the guard never consulted bm_repo_scope and
    printed a Stop verdict in a repository whose hooks were off and in a
    repository nobody opted in, which is what kept two inherited reds in
    tools/test_bm.py's TestBmRepoScope on main. The positive control below
    is what makes the two silence assertions worth anything: the same
    payload, unscoped, prints a verdict."""

    TOOL = os.path.abspath(bm_clock_guard.__file__)

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="bm-clock-scope-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        os.makedirs(os.path.join(self.repo, ".git"))
        self.config_dir = tempfile.mkdtemp(prefix="bm-clock-config-")
        self.addCleanup(shutil.rmtree, self.config_dir, ignore_errors=True)

    def _run(self, event):
        # A bare time and no clock evidence: with hooks on, this is NO-DATA,
        # which a PreToolUse hook denies (a Bash command carrying the time,
        # no transcript) and a Stop hook refuses in the host's spelling (the
        # last assistant message in a transcript carrying the time).
        payload = {
            "cwd": self.repo, "session_id": "scope-" + event,
            "hook_event_name": event, "tool_name": "Bash",
            "tool_input": {"command": "commit at 14:22"},
        }
        if event == "Stop":
            # At Stop only a clock that was read and contradicts the text
            # refuses (MISMATCH); NO-DATA is reported, never blocked.
            path = os.path.join(self.repo, "transcript.jsonl")
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "content": "hi"}) + "\n")
                fh.write(json.dumps({"type": "tool_result", "tool_use_id": "toolu_1",
                                     "command": "date", "stdout": "09:00"}) + "\n")
                fh.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "commit at 14:22"}]}}) + "\n")
            payload = {"cwd": self.repo, "session_id": "scope-Stop", "hook_event_name": "Stop",
                       "transcript_path": path, "stop_hook_active": False}
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.config_dir)
        env.pop("BROTHER_CONFIG_DIR", None)
        return subprocess.run(
            [sys.executable, "-B", self.TOOL], input=json.dumps(payload),
            capture_output=True, text=True, cwd=self.repo, env=env)

    def test_positive_control_prints_a_verdict_when_unscoped(self):
        for event in ("Stop", "PreToolUse"):
            r = self._run(event)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(r.stdout, "", "%s printed nothing while active" % event)
            self.assertIn("MISMATCH" if event == "Stop" else "NO-DATA", r.stdout)

    def test_hooks_off_makes_the_guard_silent(self):
        os.makedirs(os.path.join(self.repo, ".brother"))
        with io.open(os.path.join(self.repo, ".brother", "config"), "w",
                     encoding="utf-8") as fh:
            fh.write("hooks: off\n")
        for event in ("Stop", "PreToolUse"):
            r = self._run(event)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "", "%s printed with hooks off: %r" % (event, r.stdout))

    def test_unopted_repository_under_a_scoped_install_is_silent(self):
        with io.open(os.path.join(self.config_dir, "brother-hook-scope"), "w",
                     encoding="utf-8") as fh:
            fh.write("scope: repositories\n")
        for event in ("Stop", "PreToolUse"):
            r = self._run(event)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "", "%s printed in an unopted repository: %r" % (event, r.stdout))
            self.assertEqual(r.stderr, "", "%s wrote to stderr in an unopted repository: %r" % (event, r.stderr))


# Run as a script (how tools/test_all.py runs every suite) this file used to define its tests and run none of them,
# exit 0: a pass that tested nothing.
if __name__ == "__main__":
    unittest.main()

