#!/usr/bin/env python3
"""Tests for wip_status.py (ACC3, finish-first limits). Every reading is injected, so no case touches GitHub, the
process table or the machine slots. One fixture per limit, at the limit and one over it, plus the NO-DATA paths.

Run: python3 -B scripts/loop/test_wip_status.py
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wip_status as W  # noqa: E402

NOW = 1_800_000_000.0
HOUR = 3600.0


def pr(n, draft=False, idle_h=1.0):
    return {"number": n, "isDraft": draft, "updatedAt": W.iso(NOW - idle_h * HOUR)}


class Readings(object):
    """A healthy baseline; each case changes exactly one reading."""

    def __init__(self, **over):
        self.prs = [pr(n) for n in range(1, 4)]
        self.ahead = (2, NOW - 2 * HOUR)       # commits ahead of main, time of the oldest of them
        self.sessions = 2
        self.heavy = 1
        for k, v in over.items():
            setattr(self, k, v)

    def run(self):
        return W.evaluate(prs=lambda: self.prs, ahead=lambda: self.ahead,
                          sessions=lambda: self.sessions, heavy=lambda: self.heavy, now=NOW)


def verdicts(rows):
    return {r[0]: r[1] for r in rows}


class TheLimits(unittest.TestCase):
    def test_a_healthy_estate_is_within_every_limit(self):
        rows, code = Readings().run()
        self.assertEqual(code, 0)
        self.assertEqual(set(verdicts(rows).values()), {"PASS"})

    def test_open_prs_at_the_limit_pass_and_one_more_is_over(self):
        self.assertEqual(Readings(prs=[pr(n) for n in range(8)]).run()[1], 0)
        rows, code = Readings(prs=[pr(n) for n in range(9)]).run()
        self.assertEqual((verdicts(rows)["open-prs"], code), ("OVER", 1))

    def test_draft_prs_are_not_counted_as_open_work(self):
        prs = [pr(n) for n in range(8)] + [pr(99, draft=True)]
        self.assertEqual(Readings(prs=prs).run()[1], 0)

    def test_an_idle_pr_past_24_hours_is_over(self):
        self.assertEqual(verdicts(Readings(prs=[pr(1, idle_h=23.9)]).run()[0])["oldest-idle-pr"], "PASS")
        rows, code = Readings(prs=[pr(1, idle_h=24.5)]).run()
        self.assertEqual((verdicts(rows)["oldest-idle-pr"], code), ("OVER", 1))

    def test_the_loop_line_over_5_commits_is_over(self):
        self.assertEqual(Readings(ahead=(5, NOW - HOUR)).run()[1], 0)
        rows, code = Readings(ahead=(6, NOW - HOUR)).run()
        self.assertEqual((verdicts(rows)["loop-line-ahead"], code), ("OVER", 1))

    def test_the_loop_line_older_than_24_hours_is_over(self):
        rows, code = Readings(ahead=(1, NOW - 25 * HOUR)).run()
        self.assertEqual((verdicts(rows)["loop-line-ahead"], code), ("OVER", 1))

    def test_sessions_past_4_are_over(self):
        self.assertEqual(Readings(sessions=4).run()[1], 0)
        self.assertEqual(Readings(sessions=5).run()[1], 1)

    def test_heavy_jobs_past_4_are_over(self):
        self.assertEqual(Readings(heavy=4).run()[1], 0)
        self.assertEqual(Readings(heavy=5).run()[1], 1)


class TheUnknowns(unittest.TestCase):
    def test_an_unreadable_reading_is_no_data_never_a_pass(self):
        rows, code = Readings(sessions=None).run()
        self.assertEqual((verdicts(rows)["interactive-sessions"], code), ("NO-DATA", 2))

    def test_over_wins_over_no_data(self):
        rows, code = Readings(sessions=None, heavy=9).run()
        self.assertEqual(code, 1)

    def test_a_reader_that_raises_is_no_data(self):
        def boom():
            raise OSError("gh not signed in")
        rows, code = W.evaluate(prs=boom, ahead=lambda: (0, None), sessions=lambda: 1, heavy=lambda: 0, now=NOW)
        self.assertEqual((verdicts(rows)["open-prs"], verdicts(rows)["oldest-idle-pr"], code), ("NO-DATA", "NO-DATA", 2))

    def test_no_commits_ahead_passes_without_an_age(self):
        self.assertEqual(verdicts(Readings(ahead=(0, None)).run()[0])["loop-line-ahead"], "PASS")


class TheReaders(unittest.TestCase):
    def test_session_count_skips_headless_and_helper_processes(self):
        # (executable path, full command) per process, as ps reports comm and args separately: the real CLI lives
        # under "Application Support", whose space broke a split of the joined command line (live read, 2026-09-27)
        cli = "/Users/u/Library/Application Support/Claude/claude-code/2.1.281/claude.app/Contents/MacOS/claude"
        procs = [
            ("/Applications/Claude.app/Contents/MacOS/Claude", "/Applications/Claude.app/Contents/MacOS/Claude"),
            (cli, cli + " --output-format stream-json"),                       # an interactive session
            (cli, cli + " -p --model sonnet"),                                 # headless: not interactive
            ("/Users/u/.local/bin/claude", "/Users/u/.local/bin/claude"),        # an interactive session
            ("/usr/bin/grep", "grep claude"),
        ]
        self.assertEqual(W.count_sessions(procs), 2)

    def test_the_repository_of_record_comes_from_the_main_refs_remote(self):
        self.assertEqual(W.repo_from_url("https://github.com/khalil/brother-hub.git"), "khalil/brother-hub")
        self.assertEqual(W.repo_from_url("git@github.com:khalil/brother-hub.git"), "khalil/brother-hub")
        self.assertEqual(W.repo_from_url("https://github.com/khalil/brother-hub"), "khalil/brother-hub")
        self.assertIsNone(W.repo_from_url("not a url"))

    def test_prs_parse_from_gh_json(self):
        prs = W.parse_prs('[{"number": 1, "isDraft": false, "updatedAt": "2027-01-15T08:00:00Z"}]')
        self.assertEqual(prs[0]["number"], 1)
        with self.assertRaises(ValueError):
            W.parse_prs("not json")


class TheMeasureEntry(unittest.TestCase):
    def test_main_routes_through_measure(self):
        with mock.patch.object(W, "measure", return_value=([], 0)) as m:
            code = W.main(["--loop-ref", "L", "--main-ref", "hub/main"])
            self.assertEqual(code, 0)
            m.assert_called_once_with("L", "hub/main", None)

    def test_measure_passes_refs_and_repo_to_readers(self):
        calls = {}
        def fake_read_prs(repo):
            calls['read_prs'] = repo
            return []
        def fake_read_ahead(loop_ref, main_ref):
            calls['read_ahead'] = (loop_ref, main_ref)
            return (0, None)
        def fake_read_sessions():
            calls['read_sessions'] = True
            return 0
        def fake_read_heavy():
            calls['read_heavy'] = True
            return 0
        def fake_repo_of(main_ref):
            calls['repo_of'] = main_ref
            return "OWNER/NAME"
        with mock.patch.object(W, "read_prs", side_effect=fake_read_prs), \
             mock.patch.object(W, "read_ahead", side_effect=fake_read_ahead), \
             mock.patch.object(W, "read_sessions", side_effect=fake_read_sessions), \
             mock.patch.object(W, "read_heavy", side_effect=fake_read_heavy), \
             mock.patch.object(W, "repo_of", side_effect=fake_repo_of):
            W.measure("L", "hub/main")
            self.assertEqual(calls['read_ahead'], ("L", "hub/main"))
            self.assertEqual(calls['read_prs'], "OWNER/NAME")
            self.assertEqual(calls['repo_of'], "hub/main")
            calls.clear()
            W.measure("L", "hub/main", repo="EXPLICIT")
            self.assertEqual(calls['read_prs'], "EXPLICIT")
            self.assertNotIn('repo_of', calls)

    def test_measure_missing_loop_ref_is_no_data(self):
        with mock.patch.object(W, "read_prs", return_value=[pr(1)]), \
             mock.patch.object(W, "read_sessions", return_value=1), \
             mock.patch.object(W, "read_heavy", return_value=1), \
             mock.patch.object(W, "repo_of", return_value="OWNER/NAME"):
            rows, code = W.measure(None, "hub/main")
            self.assertEqual(code, 2)
            self.assertEqual(verdicts(rows)["loop-line-ahead"], "NO-DATA")

    def test_measure_empty_loop_ref_raises(self):
        with self.assertRaises(ValueError):
            W.measure("", "hub/main")

    def test_measure_empty_main_ref_raises(self):
        with self.assertRaises(ValueError):
            W.measure("L", "")

    def test_measure_none_main_ref_raises(self):
        with self.assertRaises(ValueError):
            W.measure("L", None)

    def test_measure_wrong_type_loop_ref_raises(self):
        with self.assertRaises(ValueError):
            W.measure(123, "hub/main")

    def test_measure_matches_evaluate_on_healthy_readings(self):
        r = Readings()
        with mock.patch.object(W, "read_prs", return_value=r.prs), \
             mock.patch.object(W, "read_ahead", return_value=r.ahead), \
             mock.patch.object(W, "read_sessions", return_value=r.sessions), \
             mock.patch.object(W, "read_heavy", return_value=r.heavy), \
             mock.patch.object(W, "repo_of", return_value="OWNER/NAME"):
            rows_m, code_m = W.measure("L", "hub/main", now=NOW)
        rows_e, code_e = r.run()
        self.assertEqual(rows_m, rows_e)
        self.assertEqual(code_m, code_e)



class TheHeavyRowReadsFromEveryLayout(unittest.TestCase):
    """The real entry point, run as the loop runs it, with no PYTHONPATH: once from the checkout (scripts/loop) and
    once from a flat copy of scripts/loop, the deployed bin layout. On 2026-10-03 both read the heavy-jobs row as
    NO-DATA "No module named 'heavy_slot'", because heavy_slot lives in scripts/, which neither layout puts on
    sys.path. gh is kept off PATH so no case touches GitHub; the slot directory is a private one."""

    REPO = os.path.dirname(os.path.dirname(HERE))

    def heavy_row(self, script):
        import subprocess, tempfile
        scratch = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(scratch, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as slots:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE")}
            env.update(PATH="/usr/bin:/bin", BROTHER_SLOT_DIR=slots, LOCAL_SLOTS="2")
            proc = subprocess.run([sys.executable, "-B", script, "--main-ref", "HEAD"], cwd=self.REPO, env=env,
                                  capture_output=True, text=True, timeout=120)
        rows = [l for l in proc.stdout.splitlines() if l.startswith("heavy-jobs")]
        self.assertEqual(len(rows), 1, proc.stdout + proc.stderr)
        return rows[0]

    def test_a_heavy_slot_from_another_path_is_refused(self):
        import types
        fake = types.ModuleType("heavy_slot")
        fake.__file__ = "/elsewhere/heavy_slot.py"
        with mock.patch.dict(sys.modules, {"heavy_slot": fake}):
            with self.assertRaisesRegex(ImportError, "not the code root's"):
                W._heavy_slot()

    def test_checkout_layout_reads_the_slots(self):
        row = self.heavy_row(os.path.join(HERE, "wip_status.py"))
        self.assertRegex(row, r"^heavy-jobs\s+PASS\s+0, limit 4$")

    def test_deployed_flat_bin_reads_the_slots(self):
        import shutil, tempfile
        scratch = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        with tempfile.TemporaryDirectory(dir=scratch) as flat:
            for name in os.listdir(HERE):
                if name.endswith(".py") and not name.startswith("test_"):
                    shutil.copy2(os.path.join(HERE, name), os.path.join(flat, name))
            row = self.heavy_row(os.path.join(flat, "wip_status.py"))
        self.assertRegex(row, r"^heavy-jobs\s+PASS\s+0, limit 4$")


if __name__ == "__main__":
    unittest.main()
