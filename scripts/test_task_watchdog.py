#!/usr/bin/env python3
"""Tests for the task watchdog. Every condition proven to FIRE and proven to
stay quiet, because a watchdog that has never been seen firing proves
nothing (the stall detector v2 wrote that law first). Pure examine() takes
its inputs directly, so no subprocess and no live registry is involved.
No em or en dashes."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

# Importable both from scripts/ and as scripts.test_task_watchdog from the
# repository root: the watchdog's own first live run caught the root form
# BLOCKED (sibling import off sys.path), which is this line's receipt.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_watchdog import (examine, owns, age_hours, verify_runnable,
                           apply_quarantine, check_lane_cap,
                           audit_idempotency, triage_lines,
                           ready_set_summary, format_ready_summary,
                           save_quarantine_state, save_triage_offset,
                           _parse_float_arg)

NOW = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc)
OLD = "2026-08-22T21:00:00Z"
FRESH = "2026-08-28T05:30:00Z"


def task(tid, paths, opened=FRESH, verify="git status --porcelain"):
    return {"id": tid, "ownedPaths": paths, "openedAt": opened,
            "verifyCommand": verify}


class TestConditions(unittest.TestCase):

    def test_stale_open_fires_on_old_clean_task(self):
        found = examine([task("t1", ["docs/a.md"], opened=OLD)], [], now=NOW)
        self.assertEqual(len(found), 1)
        self.assertIn("STALE-OPEN t1", found[0])
        self.assertIn("sbe task close t1", found[0])

    def test_fresh_clean_task_is_silent(self):
        found = examine([task("t1", ["docs/a.md"], opened=FRESH)], [], now=NOW)
        self.assertEqual(found, [])

    def test_long_dirty_fires_and_names_the_verify(self):
        found = examine([task("t1", ["docs/a.md"], opened=OLD,
                              verify="bash scripts/cleanse.sh")],
                        ["docs/a.md"], now=NOW)
        self.assertTrue(any("LONG-DIRTY t1" in f for f in found))
        self.assertTrue(any("scripts/cleanse.sh" in f for f in found))

    def test_drift_fires_only_for_unowned_paths(self):
        tasks = [task("t1", ["docs/a.md"])]
        found = examine(tasks, ["docs/a.md", "scripts/loose.py"], now=NOW)
        drift = [f for f in found if f.startswith("DRIFT")]
        self.assertEqual(len(drift), 1)
        self.assertIn("scripts/loose.py", drift[0])
        self.assertNotIn("docs/a.md", drift[0])

    def test_owned_dirty_path_is_not_drift(self):
        found = examine([task("t1", ["docs/a.md"])], ["docs/a.md"], now=NOW)
        self.assertEqual([f for f in found if f.startswith("DRIFT")], [])

    def test_blocked_fires_when_live_verify_fails(self):
        found = examine([task("t1", ["docs/a.md"],
                              verify="git status --porcelain")],
                        ["docs/a.md"], now=NOW,
                        run_verify=lambda cmd: (1, "boom"))
        blocked = [f for f in found if f.startswith("BLOCKED")]
        self.assertEqual(len(blocked), 1)
        self.assertIn("boom", blocked[0])

    def test_verify_never_runs_for_clean_tasks(self):
        calls = []

        def spy(cmd):
            calls.append(cmd)
            return (1, "should never happen")

        examine([task("t1", ["docs/a.md"], opened=FRESH)], [],
                now=NOW, run_verify=spy)
        self.assertEqual(calls, [])

    def test_directory_ownership_uses_prefix_semantics(self):
        t = task("t1", ["docs/plan"])
        self.assertTrue(owns(t, "docs/plan/deep/file.md"))
        self.assertFalse(owns(t, "docs/planner.md"))

    def test_unreadable_timestamp_never_fires_stale(self):
        found = examine([task("t1", ["docs/a.md"], opened="garbage")],
                        [], now=NOW)
        self.assertEqual(found, [])

    def test_allowlist_refuses_side_effect_commands(self):
        self.assertFalse(verify_runnable("rm -rf /"))
        self.assertFalse(verify_runnable("git push origin main"))
        self.assertTrue(verify_runnable("bash scripts/cleanse.sh"))
        self.assertTrue(verify_runnable("test -f STATE.md"))

    def test_age_hours_reads_the_registry_stamp_format(self):
        self.assertAlmostEqual(age_hours(OLD, NOW), 129.0, places=1)


class TestQuarantine(unittest.TestCase):
    """WD-01, SQS dead-letter shape: a task stuck 3 consecutive runs stops
    retrying and escalates once, to the founder, instead of repeating its
    unlock forever."""

    def test_escalates_on_third_consecutive_run(self):
        tasks = [task("t1", ["docs/a.md"], opened=OLD)]
        findings = ["STALE-OPEN t1: open 129 hours; every owned path clean. "
                    "UNLOCK: sbe task close t1"]
        state = {}
        out1 = apply_quarantine(findings, tasks, state)
        self.assertEqual(out1, findings)
        self.assertEqual(state["t1"], 1)

        out2 = apply_quarantine(findings, tasks, state)
        self.assertEqual(out2, findings)
        self.assertEqual(state["t1"], 2)

        out3 = apply_quarantine(findings, tasks, state)
        self.assertEqual(len(out3), 1)
        self.assertTrue(out3[0].startswith("QUARANTINE t1"))
        self.assertIn("3 consecutive", out3[0])
        self.assertIn("docs/a.md", out3[0])
        self.assertNotIn("sbe task close", out3[0])

    def test_resets_when_the_task_goes_quiet(self):
        tasks = [task("t1", ["docs/a.md"])]
        state = {"t1": 2}
        out = apply_quarantine([], tasks, state)
        self.assertEqual(out, [])
        self.assertNotIn("t1", state)

    def test_multiple_findings_for_one_task_collapse_to_one_line(self):
        tasks = [task("t1", ["docs/a.md"], opened=OLD)]
        findings = ["LONG-DIRTY t1: open 129 hours, still dirty. UNLOCK: x",
                    "BLOCKED t1: its own verify fails. UNLOCK: y"]
        state = {"t1": 2}
        out = apply_quarantine(findings, tasks, state)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("QUARANTINE t1"))


class TestPerTaskThreshold(unittest.TestCase):
    """WD-02, Airflow per-task SLA shape: expectedHours on the task record
    overrides the flat staleness default, in either direction."""

    def test_expected_hours_can_lower_the_threshold(self):
        t = task("t1", ["docs/a.md"], opened="2026-08-27T20:00:00Z")  # ~10h
        t["expectedHours"] = 5
        found = examine([t], [], now=NOW)
        self.assertTrue(any("STALE-OPEN t1" in f for f in found))

    def test_expected_hours_can_raise_the_threshold(self):
        t = task("t1", ["docs/a.md"], opened=OLD)  # ~129h, past flat default
        t["expectedHours"] = 200
        found = examine([t], [], now=NOW)
        self.assertEqual(found, [])

    def test_absent_expected_hours_keeps_flat_default(self):
        found = examine(
            [task("t1", ["docs/a.md"], opened="2026-08-27T20:00:00Z")],  # ~10h
            [], now=NOW)
        self.assertEqual(found, [])


class TestLaneCap(unittest.TestCase):
    """WD-03, Temporal worker-concurrency shape: the two-lane cap read off
    docs/plan/LIVE-STATE.json day_plan rows as a mechanical condition."""

    def _write(self, rows):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        json.dump({"day_plan": {"rows": rows}}, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_flags_cap_breach_over_two_in_flight(self):
        path = self._write([
            {"id": "a", "status": "IN-FLIGHT"},
            {"id": "b", "status": "IN-FLIGHT"},
            {"id": "c", "status": "IN-FLIGHT"},
        ])
        found = check_lane_cap(path)
        self.assertTrue(any(f.startswith("LANE-CAP-BREACH") for f in found))

    def test_flags_idle_lane_when_zero_in_flight_and_a_row_is_ready(self):
        path = self._write([
            {"id": "done1", "status": "DONE"},
            {"id": "ready1", "status": "SCHEDULED", "depends_on": ["done1"]},
        ])
        found = check_lane_cap(path)
        self.assertTrue(any(f.startswith("IDLE-LANE") for f in found))
        self.assertIn("ready1", found[0])

    def test_silent_when_two_in_flight_and_none_ready(self):
        path = self._write([
            {"id": "a", "status": "IN-FLIGHT"},
            {"id": "b", "status": "IN-FLIGHT"},
            {"id": "c", "status": "SCHEDULED", "depends_on": ["missing"]},
        ])
        found = check_lane_cap(path)
        self.assertEqual(found, [])

    def test_no_data_on_missing_file_never_a_pass_and_never_a_crash(self):
        found = check_lane_cap("/nonexistent/path/LIVE-STATE.json")
        self.assertTrue(any(f.startswith("NO-DATA") for f in found))


class TestIdempotencyAudit(unittest.TestCase):
    """WD-05, Temporal's idempotency discipline: a done-check is safe to
    re-run only when its command is read-only by the allowlist; everything
    else is named, never trusted."""

    def test_allowlisted_check_is_silent(self):
        out = audit_idempotency(
            [task("t1", ["a"], verify="bash scripts/cleanse.sh")])
        self.assertEqual(out, [])

    def test_off_allowlist_check_is_named_unproven(self):
        out = audit_idempotency(
            [task("t1", ["a"], verify="sh scripts/local-gates.sh --post")])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("UNPROVEN t1"))
        self.assertIn("local-gates.sh --post", out[0])

    def test_empty_check_is_named_no_check(self):
        out = audit_idempotency([task("t1", ["a"], verify="")])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("NO-CHECK t1"))

    def test_a_write_shaped_check_never_passes_silently(self):
        out = audit_idempotency(
            [task("t1", ["a"], verify="git push origin main")])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("UNPROVEN t1"))


class TestTriageLines(unittest.TestCase):
    """--triage: one line per open task naming age, DUE/NOT-DUE, why its
    verify did or did not run, and its blocker."""

    def test_budget_exhausted_reports_not_run_never_a_failure(self):
        """A session-start hook must never stall, so once triage's time
        budget is spent the remaining verifies report NOT-RUN with that
        reason. It must not read as a verify failure, which would invent a
        blocker that was never measured."""
        t = task("t1", ["docs/a.md"], opened=OLD,
                 verify="git status --porcelain")
        lines = triage_lines([t], now=NOW, run_verify=lambda c: (None, ""))
        self.assertEqual(len(lines), 1)
        self.assertIn("NOT-RUN", lines[0])
        self.assertIn("budget", lines[0])
        self.assertNotIn("FAILS", lines[0])
        self.assertNotIn("verify failure", lines[0])

    def test_due_task_with_passing_verify(self):
        t = task("t1", ["docs/a.md"], opened=OLD,
                  verify="git status --porcelain")
        lines = triage_lines([t], now=NOW, run_verify=lambda c: (0, "clean"))
        self.assertEqual(len(lines), 1)
        self.assertIn("t1", lines[0])
        self.assertIn("DUE", lines[0])
        self.assertNotIn("NOT-DUE", lines[0])
        self.assertIn("PASS", lines[0])
        self.assertIn("needs its owner's report", lines[0])

    def test_not_due_task_quotes_first_failing_line_only(self):
        t = task("t1", ["docs/a.md"], opened=FRESH,
                  verify="git status --porcelain")
        lines = triage_lines([t], now=NOW,
                             run_verify=lambda c: (1, "boom first"))
        self.assertIn("NOT-DUE", lines[0])
        self.assertIn("boom first", lines[0])
        self.assertIn("verify failure", lines[0])

    def test_not_run_when_verify_off_allowlist(self):
        t = task("t1", ["docs/a.md"], verify="curl evil.example.com")
        lines = triage_lines([t], now=NOW, run_verify=lambda c: (0, "never"))
        self.assertIn("NOT-RUN", lines[0])
        self.assertIn("allowlist", lines[0])

    def test_not_run_when_no_verify_command(self):
        t = task("t1", ["docs/a.md"], verify="")
        lines = triage_lines([t], now=NOW)
        self.assertIn("NOT-RUN", lines[0])
        self.assertIn("no verify command", lines[0])

    def test_blocker_names_quarantine_over_plain_failure(self):
        t = task("t1", ["docs/a.md"], opened=OLD,
                  verify="git status --porcelain")
        lines = triage_lines([t], now=NOW, run_verify=lambda c: (1, "boom"),
                             quarantine_state={"t1": 3})
        self.assertIn("quarantined", lines[0])
        self.assertIn("3 consecutive", lines[0])

    def test_no_open_tasks_is_empty(self):
        self.assertEqual(triage_lines([], now=NOW), [])


class TestReadySetSummary(unittest.TestCase):
    """--triage's ready-set glance: READY, IN-FLIGHT, EVENT-WAIT counts off
    docs/plan/LIVE-STATE.json day_plan rows, mirroring
    gen_command_center.ready_state's classification."""

    def test_classifies_ready_inflight_and_event_wait(self):
        rows = [
            {"id": "a", "status": "IN-FLIGHT"},
            {"id": "b", "status": "DONE"},
            {"id": "c", "status": "SCHEDULED", "depends_on": ["b"]},
            {"id": "d", "status": "SCHEDULED", "event": "founder rules"},
            {"id": "e", "status": "SCHEDULED", "depends_on": ["missing"]},
        ]
        summary = ready_set_summary(rows)
        self.assertEqual(summary["ready"], ["c"])
        self.assertEqual(summary["in_flight"], ["a"])
        self.assertEqual(summary["event_wait"], [("d", "founder rules")])

    def test_format_names_the_event_text(self):
        rows = [{"id": "d", "status": "SCHEDULED", "event": "founder rules"}]
        line = format_ready_summary(rows)
        self.assertIn("0 READY", line)
        self.assertIn("0 IN-FLIGHT", line)
        self.assertIn("1 EVENT-WAIT", line)
        self.assertIn("d (founder rules)", line)

    def test_no_data_when_rows_missing(self):
        line = format_ready_summary(None)
        self.assertIn("NO-DATA", line)


class TestStateSaveFailureIsReported(unittest.TestCase):
    """save_quarantine_state and save_triage_offset used to swallow a write
    failure through a bare `except OSError: pass`. A caller believed the
    state was persisted while it was not. This proves the failure is now
    named on stderr, by path, rather than dropped silently, without the
    function raising (it runs from a SessionStart hook and must not stall
    a session)."""

    def _unwritable_path(self, tmp):
        # A file where the write needs a DIRECTORY makes os.makedirs fail
        # with NotADirectoryError, an OSError subclass, without touching
        # real permissions (which root or a CI runner can bypass).
        blocker = os.path.join(tmp, "blocker")
        with open(blocker, "w") as f:
            f.write("not a directory")
        return os.path.join(blocker, "sub", "state.json")

    def test_save_quarantine_state_names_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._unwritable_path(tmp)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                save_quarantine_state({"t1": "quarantined"}, path=path)
            self.assertIn("could not save quarantine state", buf.getvalue())
            self.assertIn(path, buf.getvalue())

    def test_save_triage_offset_names_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._unwritable_path(tmp)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                save_triage_offset(3, path=path)
            self.assertIn("could not save triage offset", buf.getvalue())
            self.assertIn(path, buf.getvalue())


class TestParseFloatArg(unittest.TestCase):
    """--stale-hours and --budget used to fall back to their default through
    a bare `except ValueError: pass`, so a typo'd flag value read as set
    when the run silently used the default. This proves a bad value is
    named on stderr and the default still comes back, and a good value
    passes through quietly."""

    def test_bad_value_warns_and_falls_back(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = _parse_float_arg("not-a-number", "--stale-hours", 24.0)
        self.assertEqual(result, 24.0)
        self.assertIn("--stale-hours", buf.getvalue())
        self.assertIn("not-a-number", buf.getvalue())
        self.assertIn("24.0", buf.getvalue())

    def test_good_value_is_quiet(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = _parse_float_arg("12.5", "--budget", 900.0)
        self.assertEqual(result, 12.5)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()


class TestTriageRotation(unittest.TestCase):
    """Founder ask 2026-08-28 night run: watchdogs for EVERY task. The
    triage budget is a real constraint (it fires from a SessionStart hook),
    but a FIXED run order under a fixed budget starves the same tail tasks
    on every run: on the night this was written, three of eleven reported
    NOT-RUN 'budget was spent before this one', and would have reported it
    forever. Rotating where the run starts turns a permanent blind spot
    into a bounded delay."""

    def _budgeted(self, allowance):
        """A runner that executes `allowance` verifies, then reports the
        budget as spent exactly like the live one does."""
        state = {"ran": [], "left": allowance}

        def run(cmd, tid=None):
            if state["left"] <= 0:
                return (None, "")
            state["left"] -= 1
            return (0, "")
        return run, state

    def _tasks(self, n):
        return [task("t%d" % i, ["docs/%d.md" % i]) for i in range(n)]

    def _verified(self, lines):
        return {ln.split()[1].rstrip(":") for ln in lines if "PASS `" in ln}

    def test_offset_moves_which_tasks_get_verified(self):
        tasks = self._tasks(6)
        run0, _ = self._budgeted(2)
        at0 = self._verified(triage_lines(tasks, now=NOW, run_verify=run0,
                                          start_offset=0))
        run2, _ = self._budgeted(2)
        at2 = self._verified(triage_lines(tasks, now=NOW, run_verify=run2,
                                          start_offset=2))
        self.assertEqual(at0, {"t0", "t1"})
        self.assertEqual(at2, {"t2", "t3"})

    # Keep this name short after the underscore. The repository's own
    # secret scan matches the two letters ending 'task' plus an
    # underscore plus 16 or more word characters, so a long verb
    # phrase glued straight onto 'task_' reads as a credential and
    # blocks every future push over this range. An earlier draft of
    # this very test did exactly that.
    def test_no_task_is_starved(self):
        tasks = self._tasks(6)
        seen, offset = set(), 0
        for _ in range(3):
            run, _ = self._budgeted(2)
            lines = triage_lines(tasks, now=NOW, run_verify=run,
                                 start_offset=offset)
            seen |= self._verified(lines)
            offset = (offset + 2) % len(tasks)
        self.assertEqual(seen, {"t%d" % i for i in range(6)})

    def test_lines_stay_in_registry_order_whatever_the_offset(self):
        tasks = self._tasks(4)
        run, _ = self._budgeted(2)
        lines = triage_lines(tasks, now=NOW, run_verify=run, start_offset=3)
        self.assertEqual([ln.split()[1].rstrip(":") for ln in lines],
                         ["t0", "t1", "t2", "t3"])


class TestAutonomyDialRealCallSite(unittest.TestCase):
    """DIAL-01: the DRIFT finding is a real call site for
    autonomy_dial.gate(), not a demo. Same DRIFT input, two different dial
    settings (BROTHER_AUTONOMY_DIAL, the one place the dial is read from),
    two different DIAL: lines in the finding examine() already returns."""

    def setUp(self):
        self._prior = os.environ.pop("BROTHER_AUTONOMY_DIAL", None)

    def tearDown(self):
        if self._prior is None:
            os.environ.pop("BROTHER_AUTONOMY_DIAL", None)
        else:
            os.environ["BROTHER_AUTONOMY_DIAL"] = self._prior

    def test_permissive_dial_lets_drift_execute(self):
        os.environ["BROTHER_AUTONOMY_DIAL"] = "A0"
        found = examine([task("t1", ["docs/a.md"])],
                        ["docs/a.md", "scripts/loose.py"], now=NOW)
        drift = [f for f in found if f.startswith("DRIFT")][0]
        self.assertIn("DIAL: execute_then_check", drift)

    def test_stricter_dial_makes_drift_ask(self):
        os.environ["BROTHER_AUTONOMY_DIAL"] = "A2"
        found = examine([task("t1", ["docs/a.md"])],
                        ["docs/a.md", "scripts/loose.py"], now=NOW)
        drift = [f for f in found if f.startswith("DRIFT")][0]
        self.assertIn("DIAL: ask_one_blocking_question", drift)
