#!/usr/bin/env python3
"""FLOW1.b: every stage of a unit is counted in the relay ledger, and a stalled train is woken.

Tests run the relay the way a caller does: `python3 scripts/session_relay.py record ...` writes the ledger and
`report` reads it, so the exit codes and the printed lines are what is asserted. Two classes:
  EveryStageCounted  each stage, its tokens and minutes (NO-DATA where none), PASS and FAIL gates alike, how many
                     gates ran at once, gates per merged unit and merged units per hour over a window.
  StallIsWoken       the worklist is the plan's ready set, never the sessions in flight: three units parked on the
                     owner and two ready plan units with no merge in the window is a STALL (exit 3, naming the ready
                     units); a merge, a fresh driver heartbeat or an empty ready set is not; an unreadable plan or
                     line is NO-DATA (exit 2), never an empty ready set. Plan files merge by id, so a small overlay
                     holds a unit for the owner or marks one landed.
FLOW1.e adds three more:
  StallReadsRealHeartbeat  driver liveness is loop_heartbeat's own rule (25 minute staleness, 2 minute skew, ended
                     states), never a file's mtime: a touched file or a stamp ahead of the clock is not running.
  LandingsAreSubjects  only commit SUBJECT lines land a unit, parenthesized remarks excluded; bodies never do.
  StallCallerIsWired   scripts/loop/brother_night_tick.py runs the report each tick and turns exit 3 into a finding.
Fixtures (ledger, plans, git logs) live in the folder flow1b under the one scratch root (export_public.scratch_root) and are removed after.
Run: python3 -B scripts/test_stage_ledger.py
"""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import gate_ledger  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "loop"))
import loop_heartbeat  # noqa: E402
import session_relay as sr  # noqa: E402

RELAY = os.path.join(HERE, "session_relay.py")
NOW = 1700000000.0
HOUR = 3600.0


def gitlog(*subjects, body=()):
    """`git log --format='%H %s%n%b'` text: a full sha then the subject, then any body lines (which never land a unit)."""
    lines = ["%040x %s" % (i + 1, subject) for i, subject in enumerate(subjects)]
    return "\n".join(lines + list(body)) + "\n"


def _scratch():
    root = os.path.join(export_public.scratch_root(), "flow1b")
    try:
        os.makedirs(root, exist_ok=True)
        return tempfile.mkdtemp(prefix="stage-", dir=root)
    except OSError:     # a read only home: fall back to the system default rather than skip
        return tempfile.mkdtemp(prefix="stage-")


class _Relay(unittest.TestCase):
    def setUp(self):
        self.dir = _scratch()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ledger = os.path.join(self.dir, "relay.json")

    def run_relay(self, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-B", RELAY] + list(args), capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr

    def record(self, kind, session="S", **fields):
        args = ["record", "--ledger", self.ledger, "--session", session, "--kind", kind]
        for k, v in fields.items():
            args += ["--field", "%s=%s" % (k, v)]
        rc, out, err = self.run_relay(*args)
        self.assertEqual(0, rc, out + err)

    def stage(self, unit, stage, ago_h=0.0, **fields):
        self.record("stage", unit=unit, stage=stage, at_unix=NOW - ago_h * HOUR, **fields)

    def gate(self, unit, status, start, end, gate="fast", **fields):
        self.record("gate", unit=unit, gate=gate, status=status, started_unix=NOW + start, ended_unix=NOW + end,
                    **fields)

    def write(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def heartbeat(self, age_min=1.0, name="heartbeat", **over):
        """A heartbeat record as loop_heartbeat.write makes one, stamped age_min minutes before NOW (negative: ahead)."""
        stamp = datetime.datetime.fromtimestamp(NOW - age_min * 60).strftime("%Y-%m-%d %H:%M:%S")
        rec = {"state": "PASS", "reason": "pass 3", "passes": 3, "log": "", "stamp": stamp, "landed_total": 2,
               "passes_since_landing": 0, "last_landing_stamp": stamp}
        rec.update(over)
        return self.write(name, rec)

    def report(self, window=2, driver="none", plan=None, log=None, shas=None, heartbeat=None, now=None):
        plan = plan or [self.write("plan.json", {"units": [{"id": "Z9.z", "depends_on": []}]})]
        log = log or self.write("log.txt", gitlog("Z9.z landed on the line"))
        shas = shas or self.write("shas.txt", "abc123\n")
        if heartbeat is None:     # "running" is a fresh heartbeat file, "none" is a path that does not exist
            heartbeat = self.heartbeat() if driver == "running" else os.path.join(self.dir, "no-heartbeat")
        args = ["report", "--ledger", self.ledger, "--window-hours", str(window),
                "--now", str(NOW if now is None else now), "--driver-heartbeat", heartbeat, "--log", log,
                "--shas", shas]
        for p in plan:
            args += ["--plan", p]
        return self.run_relay(*args)

    def line(self, out, prefix):
        found = [ln for ln in out.splitlines() if ln.startswith(prefix)]
        self.assertEqual(1, len(found), "%r not printed exactly once in:\n%s" % (prefix, out))
        return found[0]


class EveryStageCounted(_Relay):
    def test_each_stage_is_counted_with_its_numbers(self):
        self.stage("U1", "harness_frozen", tokens=1200, minutes=4.5, defects=1)
        self.stage("U1", "implementation_done", ago_h=0.5)           # left no number at all
        self.stage("U1", "review", ago_h=0.4, round=1, verdict="FAIL", tokens=300, minutes=2, defects=2)
        self.stage("U1", "review", ago_h=0.3, round=2, verdict="PASS", tokens=200)
        self.stage("U1", "push", ago_h=0.2)
        self.stage("U1", "merge", ago_h=0.1)
        rc, out, err = self.report()
        self.assertEqual(0, rc, out + err)
        self.assertEqual("stage harness_frozen: 1 events, tokens 1200, minutes 4.5, defects 1",
                         self.line(out, "stage harness_frozen"))
        self.assertEqual("stage implementation_done: 1 events, tokens NO-DATA, minutes NO-DATA, defects NO-DATA",
                         self.line(out, "stage implementation_done"))
        # round 2 left no minutes and no defects: a bare total would read as the whole cost
        self.assertEqual("stage review: 2 events, tokens 500, minutes NO-DATA (1 of 2 events reported, 2 so far), "
                         "defects NO-DATA (1 of 2 events reported, 2 so far), verdicts PASS 1 FAIL 1",
                         self.line(out, "stage review"))
        self.assertIn("stage push: 1 events", self.line(out, "stage push"))
        self.assertIn("stage merge: 1 events", self.line(out, "stage merge"))
        self.assertIn("stage owner_wait: 0 events, tokens NO-DATA", self.line(out, "stage owner_wait"))

    def test_a_failed_gate_is_counted_beside_a_passed_one(self):
        self.stage("U1", "merge", ago_h=0.1)
        self.gate("U1", "FAIL", -400, -300)
        self.gate("U1", "PASS", -200, -100)
        rc, out, err = self.report()
        self.assertEqual(0, rc, out + err)
        self.assertEqual("gates: 2 ran (PASS 1, FAIL 1), peak at once 1", self.line(out, "gates:"))
        self.assertEqual("gates per merged unit: 2.00", self.line(out, "gates per merged unit"))

    def test_gates_per_merged_unit_counts_only_merged_units(self):
        self.stage("U1", "merge", ago_h=0.1)
        self.stage("U2", "merge", ago_h=0.2)
        self.gate("U1", "PASS", -50, -40)
        self.gate("U1", "PASS", -30, -20)
        self.gate("U1", "FAIL", -10, -5)
        self.gate("U2", "PASS", -90, -80)
        self.gate("U3", "PASS", -70, -60)        # U3 never merged
        rc, out, err = self.report()
        self.assertEqual("gates per merged unit: 2.00", self.line(out, "gates per merged unit"))   # 4 gates, 2 units

    def test_gates_per_merged_unit_is_no_data_when_nothing_merged(self):
        self.gate("U1", "PASS", -50, -40)
        rc, out, err = self.report()
        self.assertIn("gates per merged unit: NO-DATA", self.line(out, "gates per merged unit"))

    def test_merged_units_per_hour_over_the_window(self):
        self.stage("U1", "merge", ago_h=0.5)
        self.stage("U2", "merge", ago_h=1.0)
        self.stage("U2", "merge", ago_h=1.1)     # the same unit twice is one merged unit
        self.stage("U3", "merge", ago_h=1.5)
        self.stage("U4", "merge", ago_h=3.0)     # outside a two hour window
        rc, out, err = self.report(window=2)
        self.assertEqual("merged units: 3", self.line(out, "merged units:"))
        self.assertEqual("merged units per hour: 1.50", self.line(out, "merged units per hour"))
        rc, out, err = self.report(window=4)
        self.assertEqual("merged units per hour: 1.00", self.line(out, "merged units per hour"))

    def test_gates_that_ran_at_once_are_recomputed_whatever_the_recording_order(self):
        # recorded as they finished: U1 first, although U2 and U3 ran inside it
        self.gate("U1", "PASS", -400, -300)
        self.gate("U2", "PASS", -350, -250)
        self.gate("U3", "FAIL", -340, -330)      # inside both
        self.gate("U4", "PASS", -200, -100)      # alone
        self.assertNotIn("concurrent", sr.read(self.ledger)[0], "no order dependent figure is stored")
        rc, out, err = self.report(window=1)
        self.assertEqual("gates: 4 ran (PASS 3, FAIL 1), peak at once 3", self.line(out, "gates:"))
        for unit, n in (("U1", 3), ("U2", 3), ("U3", 3), ("U4", 1)):
            self.assertEqual("gate %s fast %s at once %d" % (unit, "FAIL" if unit == "U3" else "PASS", n),
                             self.line(out, "gate %s " % unit))

    def test_gates_that_touch_ran_at_once(self):
        self.gate("U1", "PASS", -400, -300)
        self.gate("U2", "PASS", -300, -200)
        rc, out, err = self.report(window=1)
        self.assertEqual("gate U1 fast PASS at once 2", self.line(out, "gate U1 "))

    def test_a_callers_claim_rides_beside_the_recomputed_figure(self):
        self.gate("U1", "PASS", -10, 0, claimed_concurrent=4)
        self.assertEqual(4, sr.read(self.ledger)[0]["claimed_concurrent"])
        rc, out, err = self.report(window=1)
        self.assertEqual("gate U1 fast PASS at once 1", self.line(out, "gate U1 "))

    def test_malformed_stage_and_gate_events_write_nothing(self):
        bad = [
            ("stage", dict(unit="U1", stage="landed", at_unix=NOW)),                        # unknown stage
            ("stage", dict(unit="U1", stage="review", at_unix=NOW, verdict="PASS")),        # review without round
            ("stage", dict(unit="U1", stage="review", at_unix=NOW, round=1, verdict="ok")),  # verdict not PASS/FAIL
            ("stage", dict(unit="U1", stage="push", at_unix=NOW, tokens=-1)),
            ("stage", dict(unit="U1", stage="push", at_unix=NOW, minutes="soon")),
            ("stage", dict(unit="", stage="push", at_unix=NOW)),
            ("gate", dict(unit="U1", gate="fast", status="NO-DATA", started_unix=1, ended_unix=2)),
            ("gate", dict(unit="U1", gate="fast", status="PASS", started_unix=5, ended_unix=2)),
            ("gate", dict(unit="U1", gate="fast", status="PASS", started_unix=1, ended_unix=2, claimed_concurrent=0)),
        ]
        for kind, fields in bad:
            args = ["record", "--ledger", self.ledger, "--session", "S", "--kind", kind]
            for k, v in fields.items():
                args += ["--field", "%s=%s" % (k, v)]
            rc, out, err = self.run_relay(*args)
            self.assertEqual(1, rc, "%s %r should be refused: %s" % (kind, fields, out + err))
            self.assertFalse(os.path.exists(self.ledger), "a refused event wrote the ledger: %r" % (fields,))

    def test_a_stage_without_a_time_is_stamped_by_the_ledger(self):
        self.record("stage", unit="U1", stage="push")
        self.assertGreater(sr.read(self.ledger)[0]["at_unix"], NOW)

    def test_an_unreadable_ledger_is_no_data(self):
        self.write("relay.json", "{not json")
        rc, out, err = self.report()
        self.assertEqual(2, rc)
        self.assertIn("NO-DATA", err)

    def test_window_must_be_positive(self):
        for bad in ("0", "-1", "nan"):
            rc, out, err = self.run_relay("report", "--ledger", self.ledger, "--window-hours", bad,
                                          "--driver", "none", "--plan", "p", "--log", "l", "--shas", "s")
            self.assertEqual(2, rc, bad)


class GateLedgerConcurrency(unittest.TestCase):
    def test_peak_concurrency(self):
        self.assertEqual(0, gate_ledger.peak_concurrency([]))
        self.assertEqual(1, gate_ledger.peak_concurrency([(0, 1)]))
        self.assertEqual(2, gate_ledger.peak_concurrency([(0, 10), (5, 15)]))
        self.assertEqual(1, gate_ledger.peak_concurrency([(0, 1), (2, 3)]))
        self.assertEqual(2, gate_ledger.peak_concurrency([(0, 5), (5, 9)]), "touching intervals count as at once")
        self.assertEqual(3, gate_ledger.peak_concurrency([(0, 10), (1, 9), (2, 3), (20, 30)]))

    def test_concurrency_by_gate_is_order_free_and_clipped_to_each_gate(self):
        a, b, c, d = (0, 100), (50, 150), (60, 70), (200, 300)
        want = {a: 3, b: 3, c: 3, d: 1}
        for order in ([a, b, c, d], [c, d, b, a], [d, a, c, b]):
            self.assertEqual([want[i] for i in order], gate_ledger.concurrency_by_gate(order))
        # two gates that overlap a long one but not each other never ran three at once
        self.assertEqual([2, 2, 2], gate_ledger.concurrency_by_gate([(0, 100), (10, 20), (30, 40)]))
        self.assertEqual([], gate_ledger.concurrency_by_gate([]))


class _StallFixture(_Relay):
    PLAN = {"units": [
        {"id": "A1.a", "depends_on": []},                       # landed: named in the log
        {"id": "A1.b", "depends_on": ["A1.a"]},                 # ready
        {"id": "A1.c", "depends_on": ["A1.a"]},                 # ready
        {"id": "A1.d", "depends_on": ["Z9.z"]},                 # blocked: Z9.z never landed
        {"id": "A1.e", "depends_on": [], "owner_hold": True},   # waits on the owner by rule, not ready
    ]}

    def setUp(self):
        super().setUp()
        self.plan = [self.write("plan.json", self.PLAN)]
        self.log = self.write("log.txt", gitlog("fix: A1.a landed"))
        self.shas = self.write("shas.txt", "abc123\n")
        for unit in ("X1", "X2", "X3"):          # three in flight sessions, all parked on the owner
            self.record("dispatched", unit=unit, worker="W")
            self.stage(unit, "owner_wait", ago_h=3)

    def rep(self, **kw):
        kw.setdefault("plan", self.plan)
        kw.setdefault("log", self.log)
        kw.setdefault("shas", self.shas)
        return self.report(**kw)

    def overlay_plan(self, overlay):
        base = self.write("base.json", {"units": [
            {"id": "B1.a", "depends_on": []},
            {"id": "B1.b", "depends_on": ["B1.a"]},
            {"id": "B1.e", "depends_on": []},                    # waits on the owner, but only an overlay says so
            {"id": "B1.s", "depends_on": []},                    # landed under a re-made commit that omits its id
        ]})
        return [base, self.write("overlay.json", overlay)] if overlay is not None else [base]


class StallIsWoken(_StallFixture):
    def test_ready_units_with_no_merge_and_no_driver_is_a_stall(self):
        rc, out, err = self.rep()
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        self.assertEqual(3, sr.STALL_EXIT)
        stall = self.line(out, "STALL")
        self.assertIn("A1.b", stall)
        self.assertIn("A1.c", stall)
        for not_ready in ("A1.a", "A1.d", "A1.e", "X1"):
            self.assertNotIn(not_ready, stall)
        self.assertEqual("in flight: 3 (parked on the owner: 3)", self.line(out, "in flight"))

    def test_a_unit_merged_inside_the_window_is_not_a_stall(self):
        self.stage("A1.a", "merge", ago_h=1.5)
        rc, out, err = self.rep(window=2)
        self.assertEqual(0, rc, out + err)
        self.assertNotIn("STALL", out)

    def test_a_merge_outside_the_window_is_still_a_stall(self):
        self.stage("A1.a", "merge", ago_h=2.5)
        rc, out, err = self.rep(window=2)
        self.assertEqual(sr.STALL_EXIT, rc, out + err)

    def test_a_fresh_driver_heartbeat_is_not_a_stall(self):
        rc, out, err = self.rep(driver="running")
        self.assertEqual(0, rc, out + err)
        self.assertNotIn("STALL", out)
        self.assertEqual("ready: A1.b A1.c", self.line(out, "ready:"))

    def test_without_an_overlay_the_owner_and_landed_units_read_ready(self):
        rc, out, err = self.rep(plan=self.overlay_plan(None), log=self.write("l.txt", gitlog("B1.a landed")))
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        self.assertEqual("ready: B1.b B1.e B1.s", self.line(out, "ready:"))

    def test_an_overlay_holds_a_unit_for_the_owner(self):
        plan = self.overlay_plan({"units": [{"id": "B1.e", "owner_hold": "human trial gate"}]})
        rc, out, err = self.rep(plan=plan, log=self.write("l.txt", gitlog("B1.a landed")))
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        self.assertEqual("ready: B1.b B1.s", self.line(out, "ready:"))
        self.assertEqual("owner: B1.e", self.line(out, "owner:"))
        self.assertNotIn("B1.e", self.line(out, "STALL"))

    def test_a_unit_held_for_the_owner_is_never_a_stall_on_its_own(self):
        plan = self.overlay_plan({"units": [{"id": "B1.e", "owner_hold": "human trial gate"},
                                            {"id": "B1.s", "landed": "025c7fcfe"}]})
        rc, out, err = self.rep(plan=plan, log=self.write("l.txt", gitlog("B1.a B1.b landed")))
        self.assertEqual(0, rc, out + err)
        self.assertEqual("ready: none", self.line(out, "ready:"))
        self.assertEqual("owner: B1.e", self.line(out, "owner:"))

    def test_an_overlay_lands_a_unit_the_log_does_not_name(self):
        plan = self.overlay_plan({"units": [{"id": "B1.s", "landed": "025c7fcfe"}]})
        rc, out, err = self.rep(plan=plan, log=self.write("l.txt", gitlog("B1.a landed")))
        self.assertEqual("ready: B1.b B1.e", self.line(out, "ready:"))

    def test_a_later_plan_file_overrides_an_earlier_one_for_the_same_id(self):
        base = self.write("b.json", {"units": [{"id": "C1.a", "depends_on": [], "owner_hold": "x"}]})
        later = self.write("l.json", {"units": [{"id": "C1.a", "owner_hold": False}]})
        rc, out, err = self.rep(plan=[base, later])
        self.assertEqual("ready: C1.a", self.line(out, "ready:"))
        rc, out, err = self.rep(plan=[later, base])              # order matters: now the hold wins
        self.assertEqual("ready: none", self.line(out, "ready:"))
        self.assertEqual("owner: C1.a", self.line(out, "owner:"))

    def test_an_overlay_naming_an_unknown_id_is_no_data_whatever_fields_it_carries(self):
        shapes = {
            "owner_hold": {"id": "B1.eee", "owner_hold": "human trial gate"},   # B1.e misspelled
            "landed": {"id": "B1.ss", "landed": "025c7fcfe"},
            "deferred": {"id": "B1.dd", "deferred": "1.2.1"},
            "a note only": {"id": "TYPO1.x", "note": "mistyped id"},
            "nothing else": {"id": "TYPO2.x"},
        }
        for name, entry in shapes.items():
            rc, out, err = self.rep(plan=self.overlay_plan({"units": [entry]}))
            self.assertEqual(2, rc, "%s: %s" % (name, out + err))
            self.assertIn(entry["id"], err, name)
            self.assertNotIn("STALL", out, name)
            self.assertNotIn("ready:", out, name)

    def test_a_unit_the_plan_defers_is_never_ready(self):
        base = self.write("deferred.json", {"units": [{"id": "D1.a", "depends_on": []},
                                                      {"id": "D1.b", "depends_on": [], "deferred": "1.2.1"}]})
        rc, out, err = self.rep(plan=[base])
        self.assertEqual("ready: D1.a", self.line(out, "ready:"))
        self.assertEqual("deferred: D1.b", self.line(out, "deferred:"))

    def test_a_merge_stamped_a_little_ahead_of_now_counts_with_a_note(self):
        self.stage("A1.a", "merge", ago_h=-0.5)                  # half an hour after --now
        rc, out, err = self.rep(window=2)
        self.assertEqual(0, rc, out + err)
        self.assertIn("note: 1 event(s) are stamped after now", out)
        self.gate("A1.a", "PASS", 100, 600)                      # a gate that ended 600 s after --now counts too
        rc, out, err = self.rep(window=2)
        self.assertIn("gates: 1 ran", self.line(out, "gates:"))
        self.assertIn("note: 2 event(s)", out)
        self.stage("A1.b", "push", ago_h=-5)                     # further ahead than a window: left out
        rc, out, err = self.rep(window=2)
        self.assertIn("stage push: 0 events", self.line(out, "stage push"))

    def test_the_window_edge_counts_and_one_second_past_it_does_not(self):
        self.stage("A1.a", "merge", ago_h=2.0)                   # exactly one window old
        rc, out, err = self.rep(window=2)
        self.assertEqual(0, rc, out + err)
        os.unlink(self.ledger)
        self.record("stage", unit="A1.a", stage="merge", at_unix=NOW - 2 * HOUR - 1)
        rc, out, err = self.rep(window=2)
        self.assertEqual(sr.STALL_EXIT, rc, out + err)

    def test_gates_per_merged_unit_uses_the_same_window_as_the_gate_count(self):
        self.stage("U1", "merge", ago_h=0.1)
        self.gate("U1", "PASS", -3 * 3600 - 20, -3 * 3600 - 10)  # three hours ago, outside a two hour window
        self.gate("U1", "PASS", -40, -30)
        rc, out, err = self.report(window=2)
        self.assertEqual("gates: 1 ran (PASS 1, FAIL 0), peak at once 1", self.line(out, "gates:"))
        self.assertEqual("gates per merged unit: 1.00", self.line(out, "gates per merged unit"))

    def test_now_that_is_not_a_real_time_is_no_data(self):
        self.stage("A1.a", "push")
        for bad in ("nan", "inf", "-5"):
            rc, out, err = self.rep(now=bad)
            self.assertEqual(2, rc, "%s: %s" % (bad, out + err))
            self.assertNotIn("STALL", out)

    def test_a_ledger_path_that_does_not_exist_is_no_data(self):
        os.unlink(self.ledger)
        rc, out, err = self.rep()
        self.assertEqual(2, rc, out + err)
        self.assertIn("NO-DATA", err)
        self.assertNotIn("STALL", out)

    def test_an_empty_ready_set_is_not_a_stall(self):
        log = self.write("done.txt", gitlog("A1.a A1.b A1.c A1.d Z9.z all landed"))
        rc, out, err = self.rep(log=log)
        self.assertEqual(0, rc, out + err)
        self.assertEqual("ready: none", self.line(out, "ready:"))

    def test_a_unit_named_inside_a_longer_id_is_not_landed(self):
        log = self.write("longer.txt", gitlog("A1.ab and XA1.a landed"))       # neither names A1.a as a whole token
        rc, out, err = self.rep(log=log)
        self.assertEqual(sr.STALL_EXIT, rc, out + err)                  # A1.a is still unlanded, so it is ready
        self.assertEqual("ready: A1.a", self.line(out, "ready:"))

    def test_two_plan_files_make_one_plan(self):
        spec = self.write("spec.json", {"subunits": [{"id": "S1.a", "depends_on": ["A1.a"]}]})
        rc, out, err = self.rep(plan=self.plan + [spec])
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        self.assertIn("S1.a", self.line(out, "STALL"))

    def test_an_unreadable_plan_or_line_is_no_data_never_an_empty_ready_set(self):
        cases = {
            "plan file missing": dict(plan=[os.path.join(self.dir, "nope.json")]),
            "plan not json": dict(plan=[self.write("bad.json", "{[")]),
            "plan names no unit": dict(plan=[self.write("none.json", {"units": []})]),
            "plan unit has no id": dict(plan=[self.write("noid.json", {"units": [{"depends_on": []}]})]),
            "plan is not an object": dict(plan=[self.write("list.json", [])]),
            "git log empty": dict(log=self.write("empty-log.txt", "  \n")),
            "git log has no subject line": dict(log=self.write("body-log.txt", "A1.a landed with no sha\n")),
            "git log missing": dict(log=os.path.join(self.dir, "nolog.txt")),
            "sha list empty": dict(shas=self.write("empty-shas.txt", "")),
            "sha list missing": dict(shas=os.path.join(self.dir, "noshas.txt")),
        }
        for name, kw in cases.items():
            rc, out, err = self.rep(**kw)
            self.assertEqual(2, rc, "%s: %s" % (name, out + err))
            self.assertIn("NO-DATA", err, name)
            self.assertNotIn("STALL", out, name)
            self.assertNotIn("OK", out, name)

    def test_the_worklist_does_not_come_from_sessions_in_flight(self):
        # in flight: nothing at all, yet the plan has ready units, so the train is still stalled
        self.write("relay.json", "[]")
        rc, out, err = self.rep()
        self.assertEqual(sr.STALL_EXIT, rc, out + err)


class StallReadsRealHeartbeat(_StallFixture):
    """The driver is running only when loop_heartbeat's own rule says so (25 minute staleness, 2 minute skew, ended
    states): a file that was only touched, or a stamp the clock has not reached, never reads running."""
    def stalls(self, name, path):
        rc, out, err = self.rep(heartbeat=path)
        self.assertEqual(sr.STALL_EXIT, rc, "%s: %s" % (name, out + err))
        self.assertIn("driver: NOT RUNNING", out, name)
        return out

    def runs(self, name, path):
        rc, out, err = self.rep(heartbeat=path)
        self.assertEqual(0, rc, "%s: %s" % (name, out + err))
        self.assertIn("driver: RUNNING", out, name)

    def test_a_pulse_inside_the_rule_reads_running(self):
        self.runs("fresh", self.heartbeat(age_min=1))
        self.runs("24 minutes old", self.heartbeat(age_min=24, name="hb24"))
        self.runs("1.5 minutes ahead is skew", self.heartbeat(age_min=-1.5, name="hbskew"))
        self.runs("alive but landing nothing", self.heartbeat(passes_since_landing=5, name="hbquiet"))
        self.runs("just started", self.heartbeat(age_min=5, state="STARTED", name="hbstart"))

    def test_a_stale_pulse_is_not_running_whatever_the_window(self):
        self.stalls("26 minutes old", self.heartbeat(age_min=26))
        # two windows old: with a two hour window the old mtime rule would have called this alive
        self.stalls("two windows old", self.heartbeat(age_min=240, name="hb240"))

    def test_a_stamp_ahead_of_the_clock_is_not_running(self):
        out = self.stalls("3 minutes ahead", self.heartbeat(age_min=-3))
        self.assertIn("FUTURE", out)
        self.stalls("six hours ahead", self.heartbeat(age_min=-360, name="hb6h"))

    def test_a_merely_touched_file_is_not_running(self):
        self.stalls("plain text, fresh mtime", self.write("touched", "alive"))
        self.stalls("object with no stamp", self.write("nostamp", {"state": "PASS"}))
        old = self.heartbeat(age_min=240, name="oldstamp")
        os.utime(old, (NOW, NOW))                                  # touched just now, stamped four hours ago
        self.stalls("stale stamp, fresh mtime", old)

    def test_the_stamp_decides_not_the_file_time(self):
        fresh = self.heartbeat(age_min=1, name="freshstamp")
        os.utime(fresh, (NOW - 40 * HOUR, NOW - 40 * HOUR))        # an ancient file time, a fresh stamp
        self.runs("fresh stamp, old mtime", fresh)

    def test_a_missing_torn_or_unreadable_heartbeat_is_not_running(self):
        self.stalls("missing", os.path.join(self.dir, "nope"))
        self.stalls("torn", self.write("torn", "{\"state\": \"PA"))
        folder = os.path.join(self.dir, "folder")
        os.mkdir(folder)
        self.stalls("a folder", folder)

    def test_an_ended_driver_is_not_running_even_after_a_normal_end(self):
        self.stalls("FINISHED", self.heartbeat(state="FINISHED", name="fin"))
        self.stalls("BLOCKED", self.heartbeat(state="BLOCKED", name="blk"))
        self.stalls("ended and its end failed", self.heartbeat(state="STOPPED", end_failed=True, name="endfail"))

    def test_a_driver_that_never_finished_a_pass_is_not_running(self):
        self.stalls("STARTED 13 minutes ago", self.heartbeat(age_min=13, state="STARTED"))

    def test_the_stall_line_says_why_the_driver_is_not_running(self):
        out = self.stalls("stale", self.heartbeat(age_min=26))
        self.assertIn("no pulse for 26 minutes", self.line(out, "driver:"))


class LandingsAreSubjects(_StallFixture):
    """A unit lands only when a commit SUBJECT names it outside any parenthesized remark."""
    def setUp(self):
        super().setUp()
        self.plan = [self.write("land.json", {"units": [
            {"id": "X1.a", "depends_on": []}, {"id": "X1.b", "depends_on": ["X1.a"]},
            {"id": "X1.c", "depends_on": []}]})]

    def ready(self, log):
        rc, out, err = self.rep(log=self.write("l.txt", log))
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        return self.line(out, "ready:")

    def test_a_follow_up_remark_in_the_subject_lands_nothing(self):
        self.assertEqual("ready: X1.b X1.c", self.ready(gitlog("Train H: X1.a on the line (follow-up X1.c)")))

    def test_nested_and_trailing_remarks_land_nothing(self):
        self.assertEqual("ready: X1.b X1.c", self.ready(gitlog("X1.a landed (follow-up (see X1.c)) (also X1.c)")))

    def test_a_body_line_never_lands_a_unit(self):
        log = gitlog("X1.a landed", body=["owed: X1.c is next", "X1.b follows"])
        self.assertEqual("ready: X1.b X1.c", self.ready(log))

    def test_a_subject_naming_the_unit_outside_any_remark_lands_it(self):
        self.assertEqual("ready: X1.b", self.ready(gitlog("X1.a and X1.c landed (note)")))
        self.assertEqual("ready: X1.b", self.ready(gitlog("Train (note) X1.a X1.c landed")))

    def test_a_line_without_a_full_sha_is_a_body_line(self):
        short = "%039x X1.c landed\n" % 1
        self.assertEqual("ready: X1.b X1.c", self.ready(gitlog("X1.a landed") + short))

    def test_a_line_separator_in_a_body_cannot_forge_a_subject(self):
        for sep in (" ", "\u0085", " ", "\x0b", "\x0c", "\x1c"):
            forged = "an owed note" + sep + "%040x X1.c landed" % 9
            log = gitlog("X1.a landed", body=[forged])
            self.assertEqual("ready: X1.b X1.c", self.ready(log), repr(sep))


class HostileInput(_StallFixture):
    """Values that come from files are data: none of them can forge a line a reader matches on, and unreadable or
    unknown input is NO-DATA (or not running), never the safe default."""
    def stall_lines(self, out):
        return [ln for ln in out.split("\n") if ln.startswith("STALL")]

    def test_a_heartbeat_reason_cannot_forge_a_stall_line(self):
        for sep in ("\n", "\r\n", " ", "\u0085", " ", "\x0b"):
            hb = self.heartbeat(state="BLOCKED", reason="x" + sep + "STALL: ready units FORGED; y")
            rc, out, err = self.rep(heartbeat=hb)
            self.assertEqual(sr.STALL_EXIT, rc, repr(sep) + out + err)
            lines = self.stall_lines(out)
            self.assertEqual(1, len(lines), "%r: %s" % (sep, out))
            self.assertNotIn("FORGED", lines[0], repr(sep))
            self.assertEqual(1, len(self.line(out, "driver:").splitlines()), repr(sep))

    def test_a_gate_name_cannot_forge_a_stall_line(self):
        self.gate("U1", "PASS", -50, -40, gate="fast\nSTALL: ready units FORGED")
        rc, out, err = self.rep()
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        lines = self.stall_lines(out)
        self.assertEqual(1, len(lines), out)
        self.assertNotIn("FORGED", lines[0])

    def test_a_unit_id_that_is_not_one_plain_word_is_no_data(self):
        for bad in ("A1.a\nSTALL: ready units FORGED", "A1.a B1.b", "-A1.a", "", " ", "A1.a x", ".A1"):
            plan = [self.write("badid.json", {"units": [{"id": bad, "depends_on": []}]})]
            rc, out, err = self.rep(plan=plan)
            self.assertEqual(2, rc, "%r: %s" % (bad, out + err))
            self.assertEqual([], self.stall_lines(out), repr(bad))

    def test_bytes_that_are_not_utf8_are_no_data_or_not_running(self):
        def raw(name, data):
            path = os.path.join(self.dir, name)
            with open(path, "wb") as fh:
                fh.write(data)
            return path
        junk = b"\xff\xfe\x00\xc3\x28 not text"
        for what, kw in {"plan": dict(plan=[raw("p.json", junk)]), "git log": dict(log=raw("l.txt", junk)),
                         "sha list": dict(shas=raw("s.txt", junk))}.items():
            rc, out, err = self.rep(**kw)
            self.assertEqual(2, rc, "%s: %s" % (what, out + err))
        rc, out, err = self.rep(heartbeat=raw("hb", junk))
        self.assertEqual(sr.STALL_EXIT, rc, out + err)
        self.assertIn("driver: NOT RUNNING", out)

    def test_a_heartbeat_of_the_wrong_shape_is_not_running(self):
        with open(self.heartbeat(name="shape"), encoding="utf-8") as fh:
            good = json.load(fh)
        for name, body in {"a list": [], "a string": "alive", "a number": 7, "null": None,
                           "a stamp that is a number": {"state": "PASS", "stamp": 5},
                           "a counter that is text": dict(good, passes_since_landing="banana")}.items():
            rc, out, err = self.rep(heartbeat=self.write("shape-hb", body))
            self.assertEqual(sr.STALL_EXIT, rc, "%s: %s" % (name, out + err))

    def test_a_report_creates_and_changes_nothing(self):
        kept = self.heartbeat(name="hb-keep")
        before = sorted(os.listdir(self.dir)), os.path.getmtime(self.ledger)
        for kw in ({"heartbeat": kept}, {"heartbeat": os.path.join(self.dir, "nope")}):
            self.rep(**kw)
        self.assertEqual(before, (sorted(os.listdir(self.dir)), os.path.getmtime(self.ledger)))

    def test_a_heartbeat_written_by_the_real_writer_reads_running(self):
        path = os.path.join(self.dir, "real-heartbeat.json")
        loop_heartbeat.write(path, "PASS", "pass 4", passes=4, landed=1)     # the driver side's own writer, real clock
        rc, out, err = self.rep(heartbeat=path, now=time.time())
        self.assertEqual(0, rc, out + err)
        self.assertIn("driver: RUNNING", out)
        loop_heartbeat.write(path, "BLOCKED", "stuck", passes=4)
        rc, out, err = self.rep(heartbeat=path, now=time.time())
        self.assertEqual(sr.STALL_EXIT, rc, out + err)


class StallCallerIsWired(_Relay):
    """The scheduled caller: scripts/loop/brother_night_tick.py runs the report every tick, so STALL reaches the alert
    file instead of printing to nobody. Run against a scratch repository and the real relay."""
    TICK = os.path.join(HERE, "loop", "brother_night_tick.py")

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.dir, "repo")
        os.mkdir(self.repo)
        self.vcs("init", "-q")
        self.vcs("-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false", "commit", "-q",
                 "--allow-empty", "-m", "A1.a landed")
        self.plan = self.write("plan.json", {"units": [{"id": "A1.a", "depends_on": []},
                                                       {"id": "A1.b", "depends_on": ["A1.a"]}]})
        self.write("relay.json", "[]")
        self.env = {"BROTHER_NIGHTWATCH_ROOT": self.repo, "BROTHER_STALL_PLAN": self.plan,
                    "BROTHER_STALL_LEDGER": self.ledger, "BROTHER_STALL_SINCE": "2000-01-01",
                    "BROTHER_STALL_WINDOW_HOURS": "2",
                    "BROTHER_STALL_HEARTBEAT": os.path.join(self.dir, "no-heartbeat")}

    @staticmethod
    def tickrec(stall):
        """A tick whose other readings are healthy, so only the stall reading can make a finding."""
        return {"at": "x", "live_sessions": 1, "stall": stall,
                "ready": {"ready": 0, "ready_ids": [], "in_flight": 0, "in_flight_ids": [], "event_wait": 0}}

    def vcs(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args), check=True, capture_output=True)

    def tick_module(self, **env):
        """brother_night_tick loaded fresh under this fixture's environment, its relay pointed at the real one."""
        merged = dict(self.env, **env)
        saved = {k: os.environ.get(k) for k in list(merged) + ["BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE"]}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        for k, v in merged.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("BROTHER_CODE_ROOT", None)
        os.environ.pop("BROTHER_PROOF_PHASE", None)
        spec = importlib.util.spec_from_file_location("tick_under_test", self.TICK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.RELAY = RELAY
        return mod

    def test_a_stalled_train_becomes_an_alerting_finding_naming_the_ready_unit(self):
        mod = self.tick_module()
        got = mod.run_stall_report()
        self.assertEqual(3, got["exit"], got)
        self.assertIn("A1.b", got["line"])
        findings = mod.judge([self.tickrec(got)])
        stall = [f for f in findings if f.startswith("STALL-TRAIN")]
        self.assertEqual(1, len(stall), findings)
        self.assertIn("A1.b", stall[0])

    def test_a_live_driver_heartbeat_keeps_it_quiet(self):
        rec = {"state": "PASS", "reason": "r", "passes": 1, "log": "", "landed_total": 1, "passes_since_landing": 0,
               "last_landing_stamp": "never", "stamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        hb = self.write("live-heartbeat", rec)
        mod = self.tick_module(BROTHER_STALL_HEARTBEAT=hb)
        got = mod.run_stall_report()
        self.assertEqual({"exit": 0, "line": None}, got)
        self.assertEqual([], mod.judge([self.tickrec(got)]))

    def test_unarmed_is_silent_and_armed_but_unreadable_is_no_data(self):
        mod = self.tick_module(BROTHER_STALL_PLAN=None)
        self.assertIsNone(mod.run_stall_report())
        self.assertEqual([], mod.judge([self.tickrec(None)]))
        cases = {"missing ledger variable": {"BROTHER_STALL_LEDGER": None},
                 "missing window": {"BROTHER_STALL_WINDOW_HOURS": None},
                 "plan unreadable": {"BROTHER_STALL_PLAN": os.path.join(self.dir, "nope.json")},
                 "line unreadable": {"BROTHER_STALL_LINE": "no-such-ref"},
                 "ledger path missing": {"BROTHER_STALL_LEDGER": os.path.join(self.dir, "nope.json")}}
        for name, over in cases.items():
            mod = self.tick_module(**over)
            got = mod.run_stall_report()
            self.assertIsNone(got["exit"], "%s: %r" % (name, got))
            self.assertTrue(got["line"].startswith("NO-DATA"), "%s: %r" % (name, got))
            finding = mod.judge([self.tickrec(got)])
            self.assertTrue(finding and finding[0].startswith("NO-DATA"), "%s: %r" % (name, finding))

    def test_the_tick_itself_records_the_stall(self):
        mod = self.tick_module()
        mod.live_sessions = lambda: 1                      # the session counter reads the owner's transcripts: not here
        mod.run_watchdog = lambda: {"exit": None, "headline": None}
        self.assertEqual(3, mod.take_tick()["stall"]["exit"])


if __name__ == "__main__":
    unittest.main()
