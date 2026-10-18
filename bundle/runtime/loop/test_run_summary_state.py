#!/usr/bin/env python3
"""R33.a: the run summary is truthful about money and about what there is to review.

loop_report.py --summary LOG keeps deciding its state word from the run's log alone, with the existing STATES. Beside
it the summary now carries a cash line read from the run's own money ledger (the MONEY ROOT the log names), which
reads UNKNOWN when that ledger is absent, unreadable, torn or holds a cost that is not a number, never 0; per role
cost rows taken through the Claude and Codex adapters' own cost, NOT_MEASURED kept apart from a measured 0.0; and a
next action that never asks for a review when the run landed nothing.
Run: python3 -B scripts/loop/test_run_summary_state.py
"""
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scratch_prune  # noqa: E402  (the loop's copy of the one scratch root rule, shipped beside this test)
_SCRATCH = scratch_prune.DEFAULT_ROOT   # BROTHER_SCRATCH when set and non empty, else ~/.claude/brother-scratch
os.makedirs(_SCRATCH, exist_ok=True)
_HOME = tempfile.mkdtemp(prefix="run-summary-state-home-", dir=_SCRATCH)
os.environ["HOME"] = _HOME          # the module reads HOME at import: never this machine's evidence
os.environ.pop("BROTHER_WBS", None)
sys.path.insert(0, HERE)
import loop_report as LR  # noqa: E402

START = "RUN START %s\n" % datetime.datetime(2026, 10, 16, 1).isoformat()
END = "RUN END %s\n" % datetime.datetime(2026, 10, 16, 2).isoformat()
DEAD = (lambda pid: False)
RECONCILE = '{"type": "RECONCILE", "reservation_id": "r%d", "actual_cost": %s, "at": 1760000000}\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="run-summary-state-", dir=_HOME)
        self.money = os.path.join(self.d, "run-20261016-010000-1", "money")
        os.makedirs(self.money)
        self.ledger = os.path.join(self.money, "openrouter-ledger.jsonl")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, path, body, mode="w"):
        with open(path, mode) as fh:
            fh.write(body)
        return path

    def log(self, body, money_line=True):
        head = START + "RUN PID 4242\n"
        if money_line:
            head += "MONEY ROOT %s: this run's own ledger, 5 USD of OpenRouter until 07h00\n" % self.money
        return self.write(os.path.join(self.d, "loop-until-20261016-0100.log"), head + body)


class Cash(Base):
    def test_an_unreadable_money_ledger_is_not_zero(self):
        # the red case: a ledger holding a torn line reads UNKNOWN in the summary, never a figure
        self.write(self.ledger, RECONCILE % (1, "0.25") + '{"type": "RECONCILE", "reservation_id": "r2", "actual_c')
        s = LR.run_summary(self.log("LANDED  A1.a | log\nALARM DEADLINE: reached\n" + END), pid_alive=DEAD)
        self.assertTrue(s["cash"].startswith("cash UNKNOWN"), s["cash"])
        self.assertEqual(s["state"], "DELIVERED", "money never decides the state")
        # every other shape of unreadable money is the same word, never 0
        torn_mid = RECONCILE % (1, "0.25") + '{"type": "RECONC\n' + RECONCILE % (2, "0.5")
        bad = {
            "absent": None,
            "a directory": "DIR",
            "a torn line in the middle": torn_mid,
            "a cost that is text": RECONCILE % (1, '"banana"'),
            "a cost of NaN": RECONCILE % (1, "NaN"),
            "a null cost": RECONCILE % (1, "null"),
            "a row that is not an object": RECONCILE % (1, "0.1") + "[1, 2]\n",
            "a repeated member": '{"type": "RECONCILE", "reservation_id": "r", "actual_cost": 9, "actual_cost": 0}\n',
            "costs past the float range": RECONCILE % (1, "1e308") + RECONCILE % (2, "1e308"),
            "an abandoned hold": '{"type": "RESERVE", "reservation_id": "a", "estimated_cost": 0.3, "at": 1}\n'
                                 '{"type": "ABANDONED", "reservation_id": "a", "at": 2}\n',
        }
        for why, body in bad.items():
            path = os.path.join(self.d, "case.jsonl")
            if os.path.isdir(path):
                os.rmdir(path)
            elif os.path.exists(path):
                os.unlink(path)
            if body == "DIR":
                os.mkdir(path)
            elif body is not None:
                self.write(path, body)
            line = LR.cash_line(path)
            self.assertTrue(line.startswith("cash UNKNOWN"), "%s: %s" % (why, line))
            self.assertNotIn(" 0.0", line, why)
        bin_path = self.write(os.path.join(self.d, "binary.jsonl"), b"\xff\xfe\x00" * 40, mode="wb")
        self.assertTrue(LR.cash_line(bin_path).startswith("cash UNKNOWN"))
        for named in (None, ""):
            self.assertTrue(LR.cash_line(named).startswith("cash UNKNOWN"), named)

    def test_a_readable_ledger_sums_its_reconciled_rows(self):
        self.assertEqual(LR.cash_line(self.write(self.ledger, "")), "cash 0.0000 USD", "readable and empty is a measured 0")
        self.assertEqual(LR.cash_line(self.write(self.ledger, RECONCILE % (1, "0.125"))), "cash 0.1250 USD")
        many = "".join(RECONCILE % (i, "0.01") for i in range(100))
        many += '{"type": "RESERVE", "reservation_id": "hold", "estimated_cost": 7.0, "at": 1}\n'   # a hold is not spend
        self.assertEqual(LR.cash_line(self.write(self.ledger, many)), "cash 1.0000 USD")
        big = '{"type": "RECONCILE", "reservation_id": "big", "actual_cost": 0.5, "note": "%s"}\n' % ("x" * (1 << 20))
        self.assertEqual(LR.cash_line(self.write(self.ledger, big)), "cash 0.5000 USD", "a 1 MB row is still a row")

    def test_the_summary_reads_the_money_root_its_own_log_names(self):
        self.write(self.ledger, RECONCILE % (1, "0.75"))
        s = LR.run_summary(self.log("ALARM DEADLINE: reached\n" + END), pid_alive=DEAD)
        self.assertEqual(s["cash"], "cash 0.7500 USD")
        # a log naming no money root, or two different ones, cannot say whose money it is: UNKNOWN
        s = LR.run_summary(self.log("ALARM DEADLINE: reached\n" + END, money_line=False), pid_alive=DEAD)
        self.assertTrue(s["cash"].startswith("cash UNKNOWN"), s["cash"])
        other = os.path.join(self.d, "run-other", "money")
        os.makedirs(other)
        self.write(os.path.join(other, "openrouter-ledger.jsonl"), RECONCILE % (1, "0.5"))   # readable, still not ours
        s = LR.run_summary(self.log("MONEY ROOT %s: another run's ledger\nALARM DEADLINE: reached\n" % other + END),
                           pid_alive=DEAD)
        self.assertTrue(s["cash"].startswith("cash UNKNOWN"), s["cash"])
        # two summaries of one run agree
        a = LR.run_summary(self.log("ALARM DEADLINE: reached\n" + END), pid_alive=DEAD)
        b = LR.run_summary(self.log("ALARM DEADLINE: reached\n" + END), pid_alive=DEAD)
        self.assertEqual(a, b)

    def test_the_cli_writes_cash_and_role_costs(self):
        self.write(self.ledger, RECONCILE % (1, "0.5") + '{"torn')
        p = self.log("LANDED  A1.a | log\nALARM DEADLINE: reached\n" + END)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = LR.main(["loop_report.py", "--summary", p])
        self.assertEqual(rc, 0)
        self.assertIn("\ncash UNKNOWN: ", buf.getvalue())
        self.assertIn("\nrole costs: NO-DATA", buf.getvalue())
        with open(os.path.join(self.d, "loop-until-20261016-0100.summary.json")) as fh:
            s = json.load(fh)
        self.assertTrue(s["cash"].startswith("cash UNKNOWN"), s)
        self.assertEqual(s["role_costs"], [])


class Review(Base):
    def test_no_review_without_a_landing(self):
        for state in LR.STATES:
            self.assertNotIn("review", LR.next_action(state, []).lower(), state)
        self.assertEqual(LR.next_action("FINISHED_NO_WORK", ["A1.a"]), LR.NEXT_ACTIONS["FINISHED_NO_WORK"])
        self.assertEqual(LR.next_action("DELIVERED", ["A1.a"]), "review the landed units", "a delivered night keeps it")
        s = LR.run_summary(self.log("ALARM FINISHED: every unit in scope is DONE\n" + END), pid_alive=DEAD)
        self.assertEqual(s["state"], "FINISHED_NO_WORK")
        self.assertNotIn("review", s["next_action"].lower())
        s = LR.run_summary(self.log("LANDED  A1.a | log\nALARM FINISHED: every unit in scope is DONE\n" + END),
                           pid_alive=DEAD)
        self.assertEqual(s["next_action"], LR.NEXT_ACTIONS["FINISHED_NO_WORK"])
        s = LR.run_summary(self.log("READY   1 to land:\nALARM DEADLINE: reached\n" + END), pid_alive=DEAD)
        self.assertEqual(s["state"], "PROGRESSED_UNLANDED")
        self.assertNotIn("review", s["next_action"].lower())


class RoleCosts(Base):
    @staticmethod
    def claude(cost):
        rec = {"type": "result", "subtype": "success", "is_error": False, "result": "ok"}
        if cost is not None:
            rec["total_cost_usd"] = cost
        return {"returncode": 0, "stdout": json.dumps(rec), "stderr": ""}

    def test_not_measured_is_not_zero(self):
        rows = LR.role_costs([
            {"role": "checker", "transport": "claude", "result": self.claude(0)},
            {"role": "worker", "transport": "claude", "result": self.claude(None)},
            {"role": "documenter", "transport": "codex", "result": {"returncode": 0, "stdout": "x", "cost_usd": 0}},
            {"role": "advisor", "transport": "codex", "result": {"returncode": 0, "stdout": "x"}},
            {"role": "finisher", "transport": "claude", "result": self.claude(0.2)},
            {"role": "finisher", "transport": "codex", "result": {"returncode": 0, "stdout": "x"}},
            {"role": "planner", "transport": "codex", "result": {"returncode": 0, "stdout": "x", "cost_usd": float("nan")}},
            {"role": "probe", "transport": "smoke-signal", "result": {"cost_usd": 1.0}},
            {"role": "seat", "transport": "claude", "result": self.claude(0.1)},
            {"role": "seat", "transport": "codex", "result": {"returncode": 0, "stdout": "x", "cost_usd": 0.05}},
        ])
        by = {r["role"]: r for r in rows}
        self.assertEqual([r["role"] for r in rows], sorted(by), "one row per role, in a stable order")
        self.assertEqual((by["checker"]["cost_usd"], by["checker"]["not_measured"]), (0.0, 0), "a priced zero is 0.0")
        self.assertEqual((by["documenter"]["cost_usd"], by["documenter"]["not_measured"]), (0.0, 0))
        for role in ("worker", "advisor", "planner", "probe", "finisher"):
            self.assertIsNone(by[role]["cost_usd"], role)          # NOT_MEASURED, never folded into 0.0
            self.assertEqual(by[role]["not_measured"], 1, role)
        self.assertEqual(by["finisher"]["calls"], 2)
        self.assertAlmostEqual(by["seat"]["cost_usd"], 0.15)
        self.assertEqual(LR.role_costs([]), [])
        self.assertEqual(LR.role_costs(None), [])
        md = LR._summary_md(dict(LR.run_summary(self.log("ALARM DEADLINE: reached\n" + END), pid_alive=DEAD),
                                 role_costs=rows))
        self.assertIn("worker NOT_MEASURED", md)
        self.assertIn("checker 0.0000 USD", md)


class State(Base):
    def test_the_state_is_an_existing_word(self):
        self.assertEqual(LR.STATES, ("RUNNING", "DELIVERED", "FINISHED_NO_WORK", "PROGRESSED_UNLANDED", "WAITING",
                                     "BLOCKED_NEEDS_HUMAN", "ABORTED", "NO-DATA"))
        self.write(self.ledger, "torn")
        for body in ("", "LANDED  A1.a | log\n", "LANDED  A1.a | log\nALARM DEADLINE: r\n" + END,
                     "ALARM FINISHED: done\n" + END, "ALARM STALLED: x\n" + END, "READY   1\nALARM DEADLINE: r\n" + END,
                     "ALARM DISK: full\n" + END):
            s = LR.run_summary(self.log(body), pid_alive=DEAD)
            self.assertIn(s["state"], LR.STATES, body)
            self.assertIn(s["cash"].split(":")[0], ("cash UNKNOWN",), body)
        s = LR.run_summary(os.path.join(self.d, "missing.log"))
        self.assertEqual(s["state"], "NO-DATA")
        self.assertTrue(s["cash"].startswith("cash UNKNOWN"), s["cash"])


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
