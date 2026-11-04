#!/usr/bin/env python3
"""OUT1.g integrator cases beside the frozen LoopAccess contract (test_workflows.py, the pack harness).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: each loop
row (status, handoff, start, pause, resume, stop, close) runs the EXISTING tool and reads its own exit code and line;
a launch is never a delivery (a start that launched a driver answers NO-DATA, delivered false); a start never picks a
seat, a model or a plan for the person and never invents the owner's words for a hold; a value that is not valid, a
missing approval or a missing prerequisite starts nothing; a tool's own refusal is relayed; the capability list the
door can run, the admission groups, the reach check and the shipped runtime all name the same seven ids.

Hermetic: every tool is a temp file, every run a scripted fake, no loop, daemon or worker is ever started.

Run: python3 -B scripts/test_loop_door_ops.py
"""
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import door  # noqa: E402
import reach_check  # noqa: E402

IDS = {"loop.status", "loop.handoff", "loop.start", "loop.pause", "loop.resume", "loop.stop", "loop.close"}
AUTHORITY = {"loop.status": "read", "loop.handoff": "read", "loop.start": "approve", "loop.pause": "write",
             "loop.resume": "write", "loop.stop": "approve", "loop.close": "write"}
DEADLINE = "%02d:%02d" % (7, 0)
START = {"deadline": DEADLINE, "budget_usd": "40"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loop-door-")
        for rel in set(door.LOOP_TOOLS.values()):
            path = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# scripted stand in; the fake run below answers instead\n")
        self.ev = os.path.join(self.tmp, "evidence")  # the door's evidence folder: empty unless a test plants a launch record
        os.makedirs(self.ev)
        self.calls = []
        self.reply = (0, "")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_run(self, argv, timeout, env=None):
        self.calls.append((argv, env))
        return self.reply

    def ask(self, capability, typed=None, allow=None, outcome=None):
        invoke = lambda h, i: door.loop_invoke(h, i, run=self.fake_run, root=self.tmp, evidence=self.ev)  # noqa: E731
        return door.run_loop_capability(capability, outcome, typed, allow, invoke=invoke)

    def argv(self):
        self.assertEqual(1, len(self.calls), self.calls)
        return [os.path.basename(a) if os.path.isabs(a) else a for a in self.calls[0][0]]


class TheListIsOneList(unittest.TestCase):
    """Omit a supported capability from discovery and one of these goes red."""

    def test_the_door_runs_exactly_the_seven_and_each_authority_is_as_designed(self):
        self.assertEqual(IDS, {c["id"] for c in door.LOOP_CAPABILITIES})
        for row in door.LOOP_CAPABILITIES:
            self.assertEqual([AUTHORITY[row["id"]]], row["required_authority"], row["id"])
            self.assertTrue(row["handler"].startswith("loop_existing_"), row)

    def test_every_one_is_in_an_admission_group_and_in_the_reach_check(self):
        with open(os.path.join(ROOT, "docs", "plan", "ADMISSION.json"), encoding="utf-8") as fh:
            groups = json.load(fh)["entries"]
        listed = set(groups["loop-close"]["capabilities"]) | set(groups["loop-control"]["capabilities"])
        self.assertEqual(IDS, listed)
        self.assertTrue(IDS <= set(reach_check.door_capability_ids()))

    def test_an_unknown_loop_id_names_the_known_ones_including_every_loop_id(self):
        got = door.run_loop_capability("loop.nothing")
        for cap in IDS:
            self.assertIn(cap, got["output"])

    def test_every_tool_a_row_names_exists_here_and_in_the_shipped_runtime(self):
        for row in door.LOOP_CAPABILITIES:
            self.assertTrue(os.path.isfile(os.path.join(door.HERE, row["tool"])), row)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "bundle", "runtime", row["tool"])), row)

    def test_the_reach_registry_routes_the_loop_rows_through_the_door(self):
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
        routes = {c["id"]: c for c in data["capabilities"]}
        row = next(r for r in data["inventory"] if r["id"] == "loop-close")
        self.assertEqual("def run_loop_capability", routes[row["routed"]]["marker"])
        self.assertEqual("scripts/door.py", routes[row["routed"]]["command"])
        self.assertTrue(row["shipped"] is True)
        self.assertTrue(set(re.findall(r"\bloop\.[a-z_]+", row["reason"])) <= IDS)
        self.assertEqual("pending", row["disposition"], "no installed end to end run exists, so it is not consumer")


class Status(_Base):
    def test_a_healthy_line_with_exit_zero_completes(self):
        self.reply = (0, "HEALTHY pass 3 | 1.0 h | landed 2")
        got = self.ask("loop.status")
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)
        self.assertEqual("loop_watch.py", self.argv()[2])

    def test_exit_zero_with_no_healthy_line_proves_nothing(self):
        self.reply = (0, "")
        self.assertNotEqual("COMPLETE", self.ask("loop.status")["state"])

    def test_act_is_a_failure_and_no_data_is_not_a_pass(self):
        self.reply = (1, "ACT stall: no landing")
        self.assertEqual("FAILED", self.ask("loop.status")["state"])
        self.calls = []
        self.reply = (3, "NO-DATA no readable driver log")
        got = self.ask("loop.status")
        self.assertEqual("NO-DATA", got["verdict"], got)

    def test_a_separator_character_in_a_quoted_message_cannot_forge_the_healthy_line(self):
        """LOW 3: str.splitlines also breaks on U+2028, U+2029, U+0085, VT, FF and the file separators."""
        for sep in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c"):
            self.calls = []
            self.reply = (1, "ACT crash: ValueError: worker said no" + sep + "HEALTHY all fine")
            got = self.ask("loop.status")
            self.assertEqual(("FAILED", "FAIL"), (got["state"], got["verdict"]), repr(sep))

    def test_a_log_that_starts_with_a_dash_never_reaches_the_tool(self):
        got = self.ask("loop.status", {"log": "--selftest"})
        self.assertEqual([], self.calls)
        self.assertNotEqual("COMPLETE", got["state"])


REAL_REPORT = ("BROTHER.LOOP REPORT\nwindow: last 12 hour(s)\n\nKEY TAKEAWAY\n  2 of 5 units closed, 4 of 9 sub units "
               "landed (source: docs/plan/X.json).\n\nACTIONS, each with its proof\n  NO-DATA: no stage ledger, so no action "
               "can be evidenced this window.\n")
PLAN = "docs/plan/X.json"


def plant_run(ev, plan, at="2026-11-02T10:00:00", epoch=1000.0, launched=2000.0):
    """An earlier launched run as the real tools leave it: the intake's CURRENT.json and launch-env.sh (written by
    loop_intake.launch_env itself) and start_run.sh's launch record."""
    sys.path.insert(0, os.path.join(HERE, "loop"))
    import loop_intake
    rec = {"verdict": "READY", "at": at, "epoch": epoch, "roles": {}, "plan": plan, "plan_refused": ""}
    os.makedirs(os.path.join(ev, "loop-intake"), exist_ok=True)
    os.makedirs(os.path.join(ev, "loop-starts"), exist_ok=True)
    with open(os.path.join(ev, "loop-intake", "CURRENT.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    with open(os.path.join(ev, "loop-intake", "launch-env.sh"), "w", encoding="utf-8") as fh:
        fh.write(loop_intake.launch_env(rec, {}))
    with open(os.path.join(ev, "loop-starts", "launch-20261102-100000-1.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": "launch-v1", "stamp": "20261102-100000-1", "driver_pid": 1, "run_dir": None, "at": launched}, fh)


class Handoff(_Base):
    def test_the_report_completes_only_with_a_takeaway_that_has_no_no_data_line(self):
        self.reply = (0, REAL_REPORT)
        got = self.ask("loop.handoff", {"since_hours": 12, "plan": PLAN})
        self.assertEqual("COMPLETE", got["state"], got)
        self.assertEqual(["--since-hours", "12", "--plan", PLAN], self.argv()[3:])

    def test_a_no_data_line_in_the_takeaway_reads_no_data_not_pass(self):
        """MEDIUM 1: loop_report always prints the heading and exits 0, even when the plan cannot be read."""
        self.reply = (0, REAL_REPORT.replace("  2 of 5 units closed, 4 of 9 sub units landed (source: docs/plan/X.json).",
                                              "  NO-DATA: the plan docs/plan/X.json could not be read, so progress is unknown."))
        got = self.ask("loop.handoff", {"plan": PLAN})
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertNotEqual("COMPLETE", got["state"])
        self.reply = (0, "KEY TAKEAWAY\nlanded 2")  # no ACTIONS heading: no readable section
        self.assertNotEqual("COMPLETE", self.ask("loop.handoff", {"plan": PLAN})["state"])
        self.reply = (0, "nothing here")
        self.assertNotEqual("COMPLETE", self.ask("loop.handoff", {"plan": PLAN})["state"])

    def test_two_windows_are_not_a_pass_and_run_nothing(self):
        self.ask("loop.handoff", {"since_hours": 4, "log": "x.log", "plan": PLAN})
        self.assertEqual([], self.calls)

    def test_no_plan_named_and_none_recorded_never_falls_back_to_the_default_plan(self):
        got = self.ask("loop.handoff")
        self.assertEqual([], self.calls)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("no plan was given", got["output"])

    def test_the_runs_own_launch_record_names_the_plan(self):
        plant_run(self.ev, "docs/plan/RUN.json")
        self.reply = (0, REAL_REPORT)
        got = self.ask("loop.handoff")
        self.assertEqual("COMPLETE", got["state"], got)
        argv = self.argv()
        self.assertEqual("docs/plan/RUN.json", argv[argv.index("--plan") + 1])
        self.assertEqual("docs/plan/RUN.json", self.calls[0][1]["BROTHER_WBS"])

    def test_a_launch_record_that_names_no_usable_plan_is_no_plan(self):
        for line in ("# BROTHER_WBS NOT exported: refused", "export BROTHER_WBS=/etc/passwd", "export BROTHER_WBS=docs/plan/../x.json",
                     "export BROTHER_WBS='docs/plan/a b.json'"):
            plant_run(self.ev, "docs/plan/RUN.json")
            path = os.path.join(self.ev, "loop-intake", "launch-env.sh")
            with open(path, encoding="utf-8") as fh:
                head = fh.read().split("\n")[0]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(head + "\n" + line + "\n")
            self.assertEqual([], self.calls)
            self.assertEqual("NO-DATA", self.ask("loop.handoff")["verdict"], line)
        self.assertEqual([], self.calls)


class TheRealReportTool(unittest.TestCase):
    """The real, read only loop_report.py on an empty fixture: no scripted reply stands in for its output."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loop-door-real-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        os.makedirs(os.path.join(self.tmp, "repo", "docs", "plan"))
        self.old = os.getcwd()
        os.chdir(os.path.join(self.tmp, "repo"))

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def handoff(self, typed):
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            os.environ.pop("BROTHER_WBS", None)
            return door.run_loop_capability("loop.handoff", None, typed, None)

    def test_an_unreadable_plan_and_ledger_read_no_data_through_the_real_tool(self):
        got = self.handoff({"plan": PLAN})
        self.assertIn("KEY TAKEAWAY", got["output"], got)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertNotEqual("COMPLETE", got["state"])

    def test_a_readable_plan_and_ledger_read_pass_through_the_real_tool(self):
        with open(os.path.join(self.tmp, "repo", PLAN), "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "U1", "state": "DONE", "sub_units": []}]}, fh)
        open(os.path.join(self.home, ".claude", "evidence", "brother-stages.jsonl"), "w").close()
        got = self.handoff({"plan": PLAN})
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)
        self.assertIn("1 of 1 units closed", got["output"])


class Start(_Base):
    def test_a_launched_driver_is_not_a_delivery(self):
        """THE MUTATION: success after launching a daemon, with nothing landed or closed."""
        self.reply = (0, "OK   driver pid 123: LOOP UNTIL x\nSTARTED: watch with: tail -f x.log")
        got = self.ask("loop.start", START, ["approve"])
        self.assertNotEqual("COMPLETE", got["state"], got)
        self.assertNotEqual("PASS", got["verdict"], got)
        self.assertIs(True, got["launched"])
        self.assertIs(False, got["delivered"])
        self.assertIn("nothing has landed or closed", got["output"])

    def test_it_asks_for_no_seat_model_plan_or_hold_the_person_did_not_name(self):
        self.reply = (0, "STARTED: x")
        self.ask("loop.start", START, ["approve"])
        self.assertEqual(["bash", "start_run.sh", "--deadline", DEADLINE, "--budget-usd", "40"], self.argv())

    def test_the_owners_words_and_a_plan_pass_through_only_when_typed(self):
        self.reply = (0, "STARTED: x")
        self.ask("loop.start", dict(START, lift_hold="owner said go", plan="docs/plan/X.json"), ["approve"])
        argv = self.argv()
        self.assertEqual("owner said go", argv[argv.index("--lift-hold") + 1])
        self.assertEqual("docs/plan/X.json", argv[argv.index("--plan") + 1])

    def test_the_launchers_own_refusal_is_relayed_and_nothing_is_claimed(self):
        self.reply = (2, "TIME tree 1\nSTART REFUSED [tree]: not inside a git checkout; cd into the launch worktree\n"
                         "a trailing line the launcher's refusal helper may print")
        got = self.ask("loop.start", START, ["approve"])
        self.assertIn("START REFUSED [tree]", got["output"])
        self.assertIn("nothing was started", got["output"])
        self.assertIs(False, got["launched"])
        self.assertNotEqual("COMPLETE", got["state"])

    def test_an_exit_zero_without_started_claims_no_run(self):
        self.reply = (0, "")
        got = self.ask("loop.start", START, ["approve"])
        self.assertIs(False, got["launched"])

    def test_a_dry_run_starts_nothing_and_says_so(self):
        self.reply = (0, "DRY  would prepare the intake and start: loop_until.sh x 240")
        got = self.ask("loop.start", dict(START, dry=True), ["approve"])
        self.assertIn("--dry", self.argv())
        self.assertIs(False, got["launched"])
        self.calls = []
        self.reply = (0, "STARTED: x")  # a dry run whose tool wrongly says STARTED still claims no launch
        self.assertIs(False, self.ask("loop.start", dict(START, dry=True), ["approve"])["launched"])

    def test_a_dry_flag_that_is_not_a_real_boolean_refuses_and_never_launches(self):
        """MEDIUM 2: --input dry=true is the TEXT true; it used to fall through to a real paid launch."""
        for value in ("true", "yes", "1", "false", "", 1, 0, "True"):
            for cap, allow, typed in (("loop.start", ["approve"], dict(START, dry=value)),
                                      ("loop.close", ["write"], {"dry": value, "unit": "U1"})):
                got = self.ask(cap, typed, allow)
                self.assertEqual("NO-DATA", got["verdict"], (cap, value, got))
                self.assertIn("dry must be true or false", got["output"], (cap, value))
        self.assertEqual([], self.calls)
        self.reply = (0, "STARTED: x")
        self.ask("loop.start", dict(START, dry=False), ["approve"])  # a real false is a real launch
        self.assertNotIn("--dry", self.argv())

    def test_the_text_dry_equals_true_through_the_command_line_parser_starts_nothing(self):
        typed = door._typed_inputs(["deadline=" + DEADLINE, "budget_usd=40", "dry=true"])
        self.assertEqual("NO-DATA", self.ask("loop.start", typed, ["approve"])["verdict"])
        self.assertEqual([], self.calls)

    def test_launched_is_the_verdicts_flag_not_a_search_of_relayed_text(self):
        self.reply = (2, "START REFUSED [driver]: the driver log says launched. and then died")
        self.assertIs(False, self.ask("loop.start", START, ["approve"])["launched"])
        self.reply = (0, "the launcher ended; a log line says launched.")
        self.assertIs(False, self.ask("loop.start", START, ["approve"])["launched"])
        self.reply = (0, "STARTED: x")
        self.assertIs(True, self.ask("loop.start", START, ["approve"])["launched"])
        self.assertIs(False, door.run_loop_capability("loop.start", None, START, ["approve"],
                                                      invoke=lambda h, i: {"verdict": "NO-DATA", "evidence_refs": [],
                                                                           "output": "launched. nothing else"})["launched"])

    def test_no_approval_no_prerequisite_and_no_valid_value_each_start_nothing(self):
        self.ask("loop.start", START)  # read alone is not approve
        self.ask("loop.start", {"deadline": DEADLINE}, ["approve"])
        self.ask("loop.start", {"budget_usd": "40"}, ["approve"])
        for bad in ({"deadline": "7am"}, {"deadline": "--dry"}, {"budget_usd": "-5"}, {"budget_usd": "0"},
                    {"budget_usd": True}, {"budget_usd": "40 --worker x"}, {"lift_hold": "--dry"}, {"plan": "-x"},
                    {"scope": "--y"}):
            self.ask("loop.start", dict(START, **bad), ["approve"])
        self.assertEqual([], self.calls)

    def test_write_does_not_stand_in_for_approve(self):
        got = self.ask("loop.start", START, ["write"])
        self.assertEqual("REFUSED", got["state"])
        self.assertEqual([], self.calls)


class PauseResumeStopClose(_Base):
    def test_pause_writes_the_pause_with_the_persons_reason(self):
        self.reply = (0, "PAUSED: the driver runs no new pass")
        got = self.ask("loop.pause", outcome="lunch", allow=["write"])
        self.assertEqual("COMPLETE", got["state"], got)
        self.assertEqual(["bash", "stop_loop.sh", "--pause", "lunch"], self.argv())

    def test_resume_only_lifts_a_pause_and_a_no_pause_is_not_a_pass(self):
        self.reply = (0, "RESUMED: the next pass runs from the state on disk")
        got = self.ask("loop.resume", allow=["write"])
        self.assertEqual("COMPLETE", got["state"], got)
        self.assertEqual(["bash", "stop_loop.sh", "--resume"], self.argv())
        self.calls = []
        self.reply = (0, "not paused: no x")
        self.assertNotEqual("COMPLETE", self.ask("loop.resume", allow=["write"])["state"])

    def test_stop_needs_approval_and_a_reason_and_survivors_are_a_failure(self):
        self.ask("loop.stop", outcome="enough", allow=["write"])
        self.ask("loop.stop", allow=["approve"])
        self.assertEqual([], self.calls)
        self.reply = (1, "STOP INCOMPLETE: 3 process(es) still alive:")
        got = self.ask("loop.stop", outcome="enough", allow=["approve"])
        self.assertEqual("FAILED", got["state"], got)
        self.assertEqual(["bash", "stop_loop.sh", "--reason", "enough"], self.argv())

    def test_stop_passes_only_on_its_stopped_line_and_an_unreadable_process_table_is_no_data(self):
        self.reply = (0, "STOPPED: nothing of the loop is alive")
        self.assertEqual("COMPLETE", self.ask("loop.stop", outcome="enough", allow=["approve"])["state"])
        self.reply = (0, "")
        self.assertNotEqual("COMPLETE", self.ask("loop.stop", outcome="enough", allow=["approve"])["state"])
        self.reply = (2, "NO-DATA: a later process-table read failed; STOPPED cannot be confirmed")
        got = self.ask("loop.stop", outcome="enough", allow=["approve"])
        self.assertEqual(("NO-DATA", "NO-DATA"), (got["state"], got["verdict"]), got)

    def test_resume_does_not_claim_a_stopped_run_continues(self):
        self.reply = (0, "RESUMED: the next pass runs from the state on disk")
        out = self.ask("loop.resume", allow=["write"])["output"]
        self.assertIn("loop.start", out)
        self.assertNotIn("starts again from the state on disk", out)

    def test_a_reason_that_is_an_option_never_reaches_stop_loop(self):
        self.ask("loop.stop", outcome="--runners-only", allow=["approve"])
        self.assertEqual([], self.calls)

    def test_close_completes_only_when_a_unit_closed(self):
        self.reply = (0, "U1    DONE: the done check passed\nCLOSED  1 unit(s): U1")
        got = self.ask("loop.close", {"unit": "U1", "plan": PLAN}, ["write"])
        self.assertEqual("COMPLETE", got["state"], got)
        self.assertEqual(["close_unit.py", "U1"], self.argv()[2:])
        self.calls = []
        self.reply = (1, "CLOSED  0 unit(s): none")
        self.assertNotEqual("COMPLETE", self.ask("loop.close", {"plan": PLAN}, ["write"])["state"])
        self.calls = []
        self.reply = (2, "NO-DATA: plan unreadable")
        self.assertNotEqual("COMPLETE", self.ask("loop.close", {"plan": PLAN}, ["write"])["state"])
        self.calls = []
        self.reply = (0, "")  # an exit 0 with no done line proves nothing
        self.assertNotEqual("COMPLETE", self.ask("loop.close", {"plan": PLAN}, ["write"])["state"])
        self.reply = (0, "CLOSED  0 unit(s): none")
        self.assertNotEqual("COMPLETE", self.ask("loop.close", {"plan": PLAN}, ["write"])["state"])

    def test_close_dry_never_completes_and_a_unit_id_is_checked(self):
        self.reply = (0, "would close U1")
        self.assertNotEqual("COMPLETE", self.ask("loop.close", {"dry": True, "plan": PLAN}, ["write"])["state"])
        self.calls = []
        self.ask("loop.close", {"unit": "U1; rm -rf x", "plan": PLAN}, ["write"])
        self.ask("loop.close", {"unit": "--dry", "plan": PLAN}, ["write"])
        self.assertEqual([], self.calls)


class TheTypedWordGuard(_Base):
    def test_every_control_and_separator_class_is_refused_in_every_typed_text(self):
        """LOW 4: C0 controls, DEL, U+0085 (Cc), U+2028 (Zl) and U+2029 (Zp) never reach a tool."""
        for ch in ("\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85", "\u2028", "\u2029"):
            self.assertIsNone(door._loop_word("ok" + ch + "words"), repr(ch))
            self.ask("loop.pause", outcome="a" + ch + "b", allow=["write"])
            self.ask("loop.stop", outcome="a" + ch + "b", allow=["approve"])
            self.ask("loop.start", dict(START, lift_hold="a" + ch + "b"), ["approve"])
        self.assertEqual([], self.calls)
        self.assertEqual("plain words", door._loop_word("plain words"))
        for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"):  # ADM1.a (L-D): a pasted dash
            self.assertIn(dash, door.LOOP_DASH_LOOKALIKES)
            self.assertIsNone(door._loop_word("a" + dash + "b"), repr(dash))
            self.ask("loop.pause", outcome="a" + dash + "b", allow=["write"])
        self.assertEqual([], self.calls)


class TheLauncherTimeout(unittest.TestCase):
    def test_an_overrunning_tool_gets_sigterm_first_so_its_own_trap_runs(self):
        """LOW 7: SIGKILL would skip start_run.sh's restore trap; SIGTERM lets it run before any kill."""
        tmp = tempfile.mkdtemp(prefix="loop-door-term-")
        try:
            marker = os.path.join(tmp, "trap-ran")
            script = os.path.join(tmp, "slow.sh")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write('trap "echo restored > %s; exit 0" TERM\nwhile true; do sleep 0.1; done\n' % shlex.quote(marker))  # a path may hold spaces
            with mock.patch.object(door, "LOOP_TERM_GRACE_S", 6), mock.patch.object(door, "LOOP_KILL_WAIT_S", 1):
                code, why = door._loop_run(["bash", script], 12)  # 5 s of work, then 6 s for the trap, a loaded machine included
            self.assertIsNone(code)
            self.assertIn("stopped", why)
            self.assertTrue(os.path.isfile(marker), "the tool's TERM trap never ran")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_tool_that_ignores_sigterm_is_killed_after_the_grace_and_the_call_stays_inside_its_deadline(self):
        """ADM1.a (L-C): the grace and the kill wait come out of the deadline, so the call never outlasts it."""
        tmp = tempfile.mkdtemp(prefix="loop-door-kill-")
        try:
            script = os.path.join(tmp, "stubborn.sh")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("trap '' TERM\nwhile true; do sleep 0.1; done\n")
            began = time.monotonic()
            with mock.patch.object(door, "LOOP_TERM_GRACE_S", 6), mock.patch.object(door, "LOOP_KILL_WAIT_S", 1):
                code, why = door._loop_run(["bash", script], 5)  # a grace longer than the deadline can leave
            took = time.monotonic() - began
            self.assertIsNone(code)
            self.assertIn("within 5 seconds", why)
            self.assertGreaterEqual(took, 3.5, "SIGTERM must be given its grace before the kill")
            self.assertLess(took, 6.5, "the call outlasted its stated 5 second deadline")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


    def test_a_grandchild_holding_the_pipe_cannot_stretch_the_call_past_its_deadline(self):
        """ADM1.a (L-C): after the kill the pipe can stay open (a child of the tool); the wait for it is what is left of
        the deadline, never a fresh allowance."""
        tmp = tempfile.mkdtemp(prefix="loop-door-pipe-")
        try:
            script = os.path.join(tmp, "holder.sh")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("trap '' TERM\nsleep 9 &\nwhile true; do sleep 0.1; done\n")
            began = time.monotonic()
            with mock.patch.object(door, "LOOP_TERM_GRACE_S", 2), mock.patch.object(door, "LOOP_KILL_WAIT_S", 1):
                code, why = door._loop_run(["bash", script], 5)
            self.assertIsNone(code)
            self.assertLess(time.monotonic() - began, 6.5, "the call outlasted its stated 5 second deadline")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EdgesOfTheRoute(_Base):
    def test_a_missing_tool_is_no_data_naming_it_and_nothing_runs(self):
        os.remove(os.path.join(self.tmp, "loop", "loop_watch.py"))
        got = self.ask("loop.status")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("loop/loop_watch.py", got["output"])
        self.assertEqual([], self.calls)

    def test_a_tool_that_cannot_start_or_times_out_is_no_data(self):
        self.reply = (None, "did not answer within 900 seconds")
        got = self.ask("loop.status")
        self.assertEqual("NO-DATA", got["verdict"], got)

    def test_a_plan_named_by_the_person_reaches_the_tool_as_the_plan_variable_only(self):
        self.reply = (0, "DONE")
        self.ask("loop.close", {"plan": "docs/plan/X.json"}, ["write"])
        self.assertEqual("docs/plan/X.json", self.calls[0][1]["BROTHER_WBS"])


class TheCommandLineEntryPoint(unittest.TestCase):
    def test_a_loop_id_a_consumer_has_not_earned_is_hidden_and_starts_nothing(self):
        view = {"candidate": "c", "host": "h", "capabilities": [], "routes": set(), "queue": []}
        with mock.patch.object(door, "consumer_admission", return_value=view), \
                mock.patch.object(door, "loop_invoke") as invoke, contextlib.redirect_stdout(io.StringIO()) as out:
            code = door.main(["--capability", "loop.stop", "--input", "reason=x", "--allow", "approve"])
        self.assertEqual(2, code)
        invoke.assert_not_called()
        self.assertEqual("BLOCKED", json.loads(out.getvalue())["state"])

    def test_an_admitted_loop_id_runs_through_the_loop_route_with_its_exit_code(self):
        view = {"candidate": "c", "host": "h", "capabilities": ["loop.status"], "routes": set(), "queue": []}
        fake = mock.Mock(return_value={"verdict": "PASS", "output": "HEALTHY ok", "evidence_refs": ["e"]})
        with mock.patch.object(door, "consumer_admission", return_value=view), \
                mock.patch.object(door, "loop_invoke", fake), contextlib.redirect_stdout(io.StringIO()) as out:
            code = door.main(["--capability", "loop.status"])
        self.assertEqual(0, code)
        fake.assert_called_once()
        self.assertEqual("COMPLETE", json.loads(out.getvalue())["state"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
