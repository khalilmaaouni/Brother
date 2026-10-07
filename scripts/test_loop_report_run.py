"""loop_report.py --run reports ONE driver run or says NO-DATA with the reason (owner finding 2026-09-26).

The deployed report ignored --run, so every proof run's done check printed the last 24 hours and exited 0. The
driver now writes dated RUN START and RUN END lines; this suite pins the contract of the window read from them:
  - start and end come from those lines, never from file times;
  - a log with no RUN START, with two RUN START lines (the driver named logs by HH:MM only, and two logs were found
    holding two runs each), or whose RUN END precedes its RUN START is NO-DATA with a reason, never a bare None;
  - the CLI refuses unknown flags and a NO-DATA window with exit 2, and --run with --since-hours;
  - rows(until=) drops records after the window, and the silent-failure lint over scripts/loop stays clean.
Run from the repository root: python3 -B scripts/test_loop_report_run.py
"""
import datetime, io, json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LR_PATH = os.path.join(ROOT, "scripts", "loop", "loop_report.py")
sys.path.insert(0, os.path.join(ROOT, "scripts", "loop"))   # names the loop copy, so the parity gate can read it
import loop_report as LR  # noqa: E402


def _log(d, name, body):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


class RunWindow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lr-run-")

    def test_window_is_read_from_dated_lines(self):
        p = _log(self.d, "loop-until-0632.log", "LOOP UNTIL 08:00\nRUN START 2026-09-26T06:32:57\n===== pass 1 at 06:33:00 (lanes 8) =====\n"
                 "RUN END 2026-09-26T07:04:56\n")
        s, e, label = LR.run_window(p)
        self.assertEqual(s, datetime.datetime(2026, 9, 26, 6, 32, 57).timestamp())
        self.assertEqual(e, datetime.datetime(2026, 9, 26, 7, 4, 56).timestamp())
        self.assertIn("loop-until-0632.log", label)

    def test_no_run_start_is_no_data_with_a_reason(self):
        p = _log(self.d, "loop-until-0041.log", "LOOP UNTIL 08:00\n===== pass 1 at 01:06:25 (lanes 6) =====\n")
        w = LR.run_window(p)
        self.assertIsInstance(w, tuple, "a bare None hides why the window is unknown")
        self.assertIsNone(w[0])
        self.assertIn("RUN START", w[2])

    def test_two_runs_in_one_log_are_refused(self):
        p = _log(self.d, "loop-until-0803.log", "RUN START 2026-09-22T08:03:00\nRUN END 2026-09-22T08:06:00\n"
                 "RUN START 2026-09-23T08:03:00\nRUN END 2026-09-23T09:37:00\n")
        w = LR.run_window(p)
        self.assertIsNone(w[0], "two runs in one log must not become one window")
        self.assertIn("2 runs", w[2])

    def test_end_before_start_is_refused(self):
        p = _log(self.d, "x.log", "RUN START 2026-09-26T07:00:00\nRUN END 2026-09-26T06:00:00\n")
        w = LR.run_window(p)
        self.assertIsNone(w[0])
        self.assertIn("before", w[2])

    def test_unreadable_log_is_no_data_with_a_reason(self):
        w = LR.run_window(os.path.join(self.d, "missing.log"))
        self.assertIsNone(w[0])
        self.assertTrue(w[2])

    def test_rows_until_drops_later_records(self):
        p = os.path.join(self.d, "s.jsonl")
        with open(p, "w") as f:
            f.write('{"at": 100, "id": "early"}\n{"at": 500, "id": "in"}\n{"at": 900, "id": "late"}\n')
        got, _ = LR.rows(p, since=200, until=800)
        self.assertEqual([r["id"] for r in got], ["in"])


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, HOME=tempfile.mkdtemp(prefix="lr-home-"))
        return subprocess.run([sys.executable, "-B", LR_PATH] + list(args), capture_output=True, text=True, timeout=120, env=env)

    def test_unknown_flag_exit_2(self):
        r = self.run_cli("--no-such-flag")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_run_without_start_exit_2_no_data(self):
        d = tempfile.mkdtemp(prefix="lr-cli-")
        p = _log(d, "loop-until-0041.log", "LOOP UNTIL 08:00\n")
        r = self.run_cli("--run", p)
        self.assertEqual(r.returncode, 2)
        self.assertIn("NO-DATA", r.stdout + r.stderr)

    def test_run_with_since_hours_exit_2(self):
        d = tempfile.mkdtemp(prefix="lr-cli-")
        p = _log(d, "l.log", "RUN START 2026-09-26T06:00:00\n")
        self.assertEqual(self.run_cli("--run", p, "--since-hours", "3").returncode, 2)

    def test_run_header_names_the_run(self):
        d = tempfile.mkdtemp(prefix="lr-cli-")
        p = _log(d, "loop-until-0632.log", "RUN START 2026-09-26T06:32:57\nRUN END 2026-09-26T07:04:56\n")
        r = self.run_cli("--run", p, "--plan", os.path.join(d, "no-plan.json"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("loop-until-0632.log", r.stdout)
        self.assertNotIn("last 24 hour", r.stdout)


class ReportTruth(unittest.TestCase):
    """Findings 2, 9 and 10 of 2026-09-27, each read from report() or the functions it prints."""

    def _plan(self, subs, evidence):
        d = tempfile.mkdtemp(prefix="lr-truth-")
        return _log(d, "plan.json", json.dumps({"units": [{"id": "A", "sub_units": subs, "evidence": evidence}]}))

    def test_a_longer_id_never_lands_its_prefix(self):
        self.assertEqual(LR.progress(self._plan(["A.1", "A.10"], "A.10 landed 2026-09-27."))["subs_done"], 1)

    def test_a_negated_landing_is_not_counted(self):
        self.assertEqual(LR.progress(self._plan(["A.1"], "A.1 not landed: gate refused."))["subs_done"], 0)

    def test_evidence_that_is_not_text_is_no_data_never_a_count(self):
        self.assertIsNone(LR.progress(self._plan(["A.1"], ["A.1 landed"])))

    def test_overlapping_processes_agree_between_yield_and_utilisation(self):
        rs = [dict(at=0, stage="model", event="enter", sub="A.1", pid=1),
              dict(at=10, stage="model", event="enter", sub="A.1", pid=2),
              dict(at=20, stage="model", event="leave", sub="A.1", pid=2, ok=True),
              dict(at=100, stage="model", event="leave", sub="A.1", pid=1, ok=True)]
        self.assertEqual(LR.utilisation(rs)["model"][:2], (2, 110))
        self.assertEqual(LR.stage_yield(rs)["model"]["secs"], 110)

    def test_discarded_effort_is_the_failed_durations_never_a_fraction_of_the_total(self):
        d = tempfile.mkdtemp(prefix="lr-truth-")
        rs = [dict(at=100, stage="grade", event="enter", sub="A", pid=1),
              dict(at=101, stage="grade", event="leave", sub="A", pid=1, ok=False),
              dict(at=101, stage="grade", event="enter", sub="B", pid=1),
              dict(at=200, stage="grade", event="leave", sub="B", pid=1, ok=True)]
        paths = [_log(d, n, "".join(json.dumps(r) + "\n" for r in rows)) for n, rows in (("s.jsonl", rs), ("f.jsonl", []), ("l.jsonl", []))]
        keep = (LR.STAGES, LR.FAILURES, LR.LEDGER)
        LR.STAGES, LR.FAILURES, LR.LEDGER = paths
        try:
            buf = io.StringIO()
            LR.report(plan_path=os.path.join(d, "no-plan.json"), window=(100, 200, "fixture"), out=buf)
        finally:
            LR.STAGES, LR.FAILURES, LR.LEDGER = keep
        line = next((l for l in buf.getvalue().splitlines() if "effort discarded:" in l), "")
        self.assertIn("(1%)", line, buf.getvalue())


class MoneyRows(unittest.TestCase):
    """X1 finding 4 (2026-09-27): the report read the money ledger with json.loads, which keeps the LAST of repeated
    members, so a RECONCILE carrying actual_cost 9 then 0 printed a measured zero and exited 0. Every row is read by
    proof_ledger.loads, the strict parser: a refused row is a corrupt money row, warned, and the exit is 3.
    One guard per fixture: the parser alone, the exit alone, then the finding end to end and its control."""
    RESERVE = '{"type":"RESERVE","reservation_id":"r","at":1,"estimated_cost":9}\n'

    def report(self, ledger):
        d = tempfile.mkdtemp(prefix="lr-money-")
        paths = [_log(d, "s.jsonl", ""), _log(d, "f.jsonl", ""), _log(d, "l.jsonl", ledger)]
        keep = (LR.STAGES, LR.FAILURES, LR.LEDGER)
        LR.STAGES, LR.FAILURES, LR.LEDGER = paths
        try:
            buf = io.StringIO()
            rc = LR.report(plan_path=os.path.join(d, "no-plan.json"), window=(0, 10, "fixture"), out=buf)
        finally:
            LR.STAGES, LR.FAILURES, LR.LEDGER = keep
        return rc, buf.getvalue()

    def test_rows_refuse_a_repeated_member_as_a_corrupt_line(self):
        d = tempfile.mkdtemp(prefix="lr-money-")
        p = _log(d, "l.jsonl", self.RESERVE + '{"type":"RECONCILE","reservation_id":"r","at":2,"actual_cost":9,"actual_cost":0}\n')
        got, bad = LR.rows(p)
        self.assertEqual(([r["type"] for r in got], bad), (["RESERVE"], 1))

    def test_a_corrupt_money_line_exits_3(self):
        rc, out = self.report(self.RESERVE + '{"type":"RECONCILE","reservation_id":"r","at":2,"actual_co\n')
        self.assertEqual(rc, 3, out)
        self.assertIn("corrupt line(s) skipped in ledger", out)

    def test_a_repeated_cost_member_is_never_a_measured_zero(self):
        rc, out = self.report(self.RESERVE + '{"type":"RECONCILE","reservation_id":"r","at":2,"actual_cost":9,"actual_cost":0}\n')
        self.assertEqual(rc, 3, out)
        self.assertIn("corrupt line(s) skipped in ledger", out)
        self.assertNotIn("over 1 reconciled call(s)", out, "the refused row is never counted as a call")

    def test_the_same_row_with_one_cost_is_measured_and_exits_0(self):
        rc, out = self.report(self.RESERVE + '{"type":"RECONCILE","reservation_id":"r","at":2,"actual_cost":9}\n')
        self.assertEqual(rc, 0, out)
        self.assertIn("spend: 9.00 USD over 1 reconciled call(s)", out)


class Lint(unittest.TestCase):
    def test_no_silent_failure_in_scripts_loop(self):
        tool = os.path.join(ROOT, "products", "brothersbe", "tools", "sbe_score.py")
        r = subprocess.run([sys.executable, tool, os.path.join(ROOT, "scripts", "loop"), "--repo-only", "--strict"],
                           capture_output=True, text=True, timeout=300)
        line = next((l for l in r.stdout.splitlines() if l.startswith("silent-failure-lints")), "")
        self.assertIn("PASS", line, line[:300])


import time
from unittest import mock


class WipSection(unittest.TestCase):
    """R-WIP-9: the status answer shows every limit with its source, says NO-DATA for a missing or
    stale reading, and ranks finishing first when over. One condition per test."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lr-wip-")
        self.record = os.path.join(self.d, "wip.json")
        self.stages = os.path.join(self.d, "stages.jsonl")
        self.failures = os.path.join(self.d, "failures.jsonl")
        self.ledger = os.path.join(self.d, "ledger.jsonl")
        self.missing_plan = os.path.join(self.d, "no-plan.json")
        for p in (self.stages, self.failures, self.ledger):
            open(p, "w", encoding="utf-8").close()

    def _write(self, obj):
        with open(self.record, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def _report(self, wip_record):
        buf = io.StringIO()
        with mock.patch.object(LR, "STAGES", self.stages), \
                mock.patch.object(LR, "FAILURES", self.failures), \
                mock.patch.object(LR, "LEDGER", self.ledger):
            rc = LR.report(since_hours=1, plan_path=self.missing_plan, out=buf, wip_record=wip_record)
        return rc, buf.getvalue()

    def test_1_over_record_shows_over_row_and_source(self):
        now = time.time()
        self._write({"at": now, "code": 1, "mode": "report",
                     "rows": [["loop-line-ahead", "OVER", "217 commits ahead"]]})
        _, text = self._report(self.record)
        self.assertIn("finish-first limits: OVER", text)
        self.assertIn("loop-line-ahead", text)
        self.assertIn("217 commits ahead", text)
        self.assertIn(self.record, text)

    def test_2_finish_item_ranks_above_brief_item(self):
        now = time.time()
        self._write({"at": now, "code": 1, "mode": "report",
                     "rows": [["loop-line-ahead", "OVER", "217 commits ahead"]]})
        with open(self.failures, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now, "class": "SUITE", "detail": "x"}) + "\n")
        _, text = self._report(self.record)
        self.assertIn("NEXT STEPS, ranked, first one first\n", text)
        tail = text.split("NEXT STEPS, ranked, first one first\n", 1)[1]
        first = tail.strip().splitlines()[0]
        self.assertIn("Finish the oldest work", first)
        self.assertIn("Teach the brief about SUITE", tail)

    def test_3_missing_record_is_no_data_and_same_exit_as_code_0(self):
        rc_missing, text_missing = self._report(self.record)
        self.assertIn("NO-DATA", text_missing)
        self.assertIn("Not a pass", text_missing)
        self.assertNotIn("Finish the oldest work", text_missing)
        self._write({"at": time.time(), "code": 0, "mode": "report", "rows": []})
        rc_zero = self._report(self.record)[0]
        self.assertEqual(rc_missing, rc_zero)

    def test_4_stale_record_is_no_data_naming_age(self):
        now = time.time()
        self._write({"at": now - 2000, "code": 1, "mode": "report",
                     "rows": [["loop-line-ahead", "OVER", "217 commits ahead"]]})
        _, text = self._report(self.record)
        self.assertIn("NO-DATA", text)
        self.assertIn("old", text)
        self.assertIn("2000", text)

    def test_5_code_0_returns_pass(self):
        now = time.time()
        self._write({"at": now, "code": 0, "mode": "report", "rows": []})
        lines, code = LR.wip_section(self.record, now)
        self.assertEqual(code, 0)
        self.assertTrue(any("finish-first limits: PASS" in ln for ln in lines), lines)

    def test_6_a_directory_where_a_file_belongs_is_no_data_not_a_crash(self):
        lines, code = LR.wip_section(self.d, time.time())
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", lines[0])

    def test_7_hostile_arguments_raise_value_error(self):
        now = time.time()
        for bad_path in (0, "", [], {}, b"/tmp"):
            with self.assertRaises(ValueError):
                LR.wip_section(bad_path, now)
        for bad_now in (True, False, "x", None, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                LR.wip_section(None, bad_now)


class WipRecordParity(unittest.TestCase):
    """R-WIP-9 parity: the report's reader and the pool's reader agree on the same seven records,
    and both defaults point at the same path under the same HOME."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lr-parity-")
        self.now = 1000000.0
        self.path = os.path.join(self.d, "wip.json")
        self.home = os.path.join(self.d, "home")
        os.makedirs(self.home)

    def _load_pool(self):
        import importlib.util
        pool_path = os.path.join(ROOT, "scripts", "loop", "runner_pool.py")
        if not os.path.isfile(pool_path):
            self.skipTest("runner_pool.py is not present in this tree")
        spec = importlib.util.spec_from_file_location("runner_pool_acc3c_parity", pool_path)
        if spec is None or spec.loader is None:
            self.skipTest("runner_pool.py could not be loaded")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_parity_with_pool_reader(self):
        old_home = os.environ.get("HOME")
        old_cwd = os.getcwd()
        os.environ["HOME"] = self.home
        try:
            os.chdir(self.home)
            try:
                pool = self._load_pool()
            finally:
                os.chdir(old_cwd)
            if not hasattr(pool, "read_wip_record") or not hasattr(pool, "WIP_RECORD"):
                self.skipTest("runner_pool.py has no wip record reader in this tree")
            fixtures = [
                ("good code 0", {"at": self.now, "code": 0, "mode": "report", "rows": []}),
                ("good code 1", {"at": self.now, "code": 1, "mode": "report", "rows": [["open-prs", "OVER", "9"]]}),
                ("missing", None),
                ("corrupt JSON", "{not json"),
                ("future dated", {"at": self.now + 61, "code": 0, "mode": "report", "rows": []}),
                ("stale", {"at": self.now - 1801, "code": 0, "mode": "report", "rows": []}),
                ("rows malformed", {"at": self.now, "code": 0, "mode": "report", "rows": [["a", "b"]]}),
            ]
            for name, obj in fixtures:
                if obj is None:
                    if os.path.exists(self.path):
                        os.remove(self.path)
                elif isinstance(obj, str):
                    with open(self.path, "w", encoding="utf-8") as fh:
                        fh.write(obj)
                else:
                    with open(self.path, "w", encoding="utf-8") as fh:
                        json.dump(obj, fh)
                pool_none = pool.read_wip_record(self.path, self.now)[0] is None
                lines, code = LR.wip_section(self.path, self.now)
                report_nodata = code == 2 and any("NO-DATA" in ln for ln in lines)
                self.assertEqual(pool_none, report_nodata, name)
            self.assertEqual(pool.WIP_RECORD, os.path.expanduser("~/.claude/evidence/wip-status.json"))
            no_lines, no_code = LR.wip_section(None, self.now)
            self.assertEqual(no_code, 2)
            self.assertIn(os.path.expanduser("~/.claude/evidence/wip-status.json"), no_lines[0])
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main(verbosity=1)
