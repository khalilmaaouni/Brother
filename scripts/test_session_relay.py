#!/usr/bin/env python3
"""XS1.a edges: scripts/session_relay.py, one fixture per refusal so each guard is the only one that can refuse it.

The frozen contract (test_session_relay_contract.py) pins the five design edges; this suite pins every other guard
and the CLI entry point, where the exit codes live. Run: python3 -B scripts/test_session_relay.py
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session_relay as sr  # noqa: E402

H1, H2 = "a" * 40, "b" * 40
RELAY = os.path.join(HERE, "session_relay.py")


class _Ledger(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="xs1-edges-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "relay.json")

    def put(self, session, **event):
        return sr.append(self.path, event, session=session)

    def raw(self, data):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def good(self, seq=1, **over):
        return dict(dict(schema=1, seq=seq, kind="built", session="S", pr=7, head=H1), **over)


class AppendRefuses(_Ledger):
    def refused(self, session="S", **event):
        with self.assertRaises(ValueError):
            sr.append(self.path, event, session=session)
        self.assertFalse(os.path.exists(self.path), "a refused event must write nothing")

    def test_unknown_kind(self):
        self.refused(kind="landed", pr=7, head=H1)

    def test_empty_session(self):
        self.refused(session=" ", kind="built", pr=7, head=H1)

    def test_missing_field(self):
        self.refused(kind="built", pr=7)

    def test_pr_must_be_a_whole_number(self):
        self.refused(kind="built", pr=True, head=H1)
        self.refused(kind="built", pr="7", head=H1)
        self.refused(kind="built", pr=0, head=H1)

    def test_head_must_be_a_full_sha(self):
        self.refused(kind="built", pr=7, head=H1[:12])
        self.refused(kind="built", pr=7, head=H1.upper())

    def test_verdict_is_pass_or_fail(self):
        self.refused(kind="reviewed", pr=7, head=H1, verdict="ok")

    def test_dispatched_needs_unit_and_worker(self):
        self.refused(kind="dispatched", unit="", worker="W")

    def test_handover_epochs_are_whole_and_named(self):
        self.refused(kind="handover", from_epoch=0, to_epoch=1, to_session="C2")
        self.refused(kind="handover", from_epoch=1, to_epoch=2, to_session="")

    def test_seq_is_the_ledgers_not_the_callers(self):
        sr.append(self.path, dict(kind="built", pr=7, head=H1, seq=99, schema=5, session="forged"), session="S")
        got = sr.read(self.path)
        self.assertEqual((1, 1, "S"), (got[0]["seq"], got[0]["schema"], got[0]["session"]))


class ReadRefuses(_Ledger):
    def unreadable(self):
        with self.assertRaises(sr.LedgerUnreadable):
            sr.read(self.path)

    def test_missing_ledger_is_empty(self):
        self.assertEqual([], sr.read(self.path))

    def test_empty_file_is_not_empty_ledger(self):
        self.raw("")
        self.unreadable()

    def test_not_a_list(self):
        for data in ({"events": []}, "5", "null", '"abc"'):
            self.raw(data)
            self.unreadable()

    def test_seq_gap(self):
        self.raw([self.good(1), self.good(3)])
        self.unreadable()

    def test_wrong_schema(self):
        self.raw([self.good(1, schema=2)])
        self.unreadable()

    def test_true_is_not_seq_one_nor_schema_one(self):
        for over in (dict(seq=True), dict(schema=True)):
            self.raw([dict(self.good(1), **over)])
            self.unreadable()

    def test_stored_handover_chain_that_breaks(self):
        def hand(seq, session, a, to):
            return dict(schema=1, seq=seq, kind="handover", session=session, from_epoch=a, to_epoch=a + 1,
                        to_session=to)
        self.raw([hand(1, "C1", 3, "C2"), hand(2, "C3", 4, "C4")])
        self.unreadable()

    def test_stored_event_of_unknown_kind(self):
        self.raw([self.good(1, kind="landed")])
        self.unreadable()

    def test_unreadable_path(self):
        os.mkdir(self.path)
        self.unreadable()

    def test_good_ledger_reads(self):
        self.raw([self.good(1), self.good(2, head=H2)])
        self.assertEqual(2, len(sr.read(self.path)))


class Concurrent(_Ledger):
    def test_two_processes_never_lose_an_event(self):
        procs = [subprocess.Popen([sys.executable, "-B", RELAY, "record", "--ledger", self.path, "--session",
                                   "S%d" % i, "--kind", "built", "--field", "pr=%d" % (i + 1), "--field",
                                   "head=" + H1], stdout=subprocess.DEVNULL) for i in range(8)]
        self.assertEqual([0] * 8, [p.wait() for p in procs])
        got = sr.read(self.path)
        self.assertEqual(list(range(1, 9)), [e["seq"] for e in got])
        self.assertEqual(set(range(1, 9)), {e["pr"] for e in got})


class Provenance(_Ledger):
    def events(self):
        return sr.read(self.path)

    def test_unknown_pull_request_has_no_builders(self):
        self.put("S", kind="built", pr=8, head=H1)
        with self.assertRaises(KeyError):
            sr.builders(self.events(), 7)
        self.assertFalse(sr.may_review(self.events(), 7, "R").ok)
        self.assertFalse(sr.may_merge(self.events(), 7, H1, "R").ok)

    def test_other_pull_requests_do_not_count(self):
        self.put("S", kind="built", pr=7, head=H1)
        self.put("R", kind="built", pr=8, head=H2)
        self.assertEqual(frozenset(("S",)), sr.builders(self.events(), 7))
        self.assertTrue(sr.may_review(self.events(), 7, "R").ok)

    def test_no_reviewer_named(self):
        self.put("S", kind="built", pr=7, head=H1)
        self.assertFalse(sr.may_review(self.events(), 7, "").ok)


class Merge(_Ledger):
    def setUp(self):
        super().setUp()
        self.put("S", kind="built", pr=7, head=H1)

    def merge(self, actor="R", head=H1):
        return sr.may_merge(sr.read(self.path), 7, head, actor)

    def test_reviewed_pass_by_a_non_builder_allows(self):
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        self.assertTrue(self.merge().ok)

    def test_the_builder_cannot_merge_even_with_a_pass(self):
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        self.assertFalse(self.merge(actor="S").ok)

    def test_no_review_refuses(self):
        self.assertFalse(self.merge().ok)

    def test_a_later_fail_overrides_an_earlier_pass(self):
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        self.put("R2", kind="reviewed", pr=7, head=H1, verdict="FAIL")
        self.assertFalse(self.merge().ok)

    def test_a_builders_fail_does_not_override_a_pass(self):
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        self.put("S", kind="reviewed", pr=7, head=H1, verdict="FAIL")
        self.assertTrue(self.merge().ok)

    def test_already_merged_refuses(self):
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        self.put("R", kind="merged", pr=7, head=H1)
        self.assertFalse(self.merge().ok)

    def test_a_pass_on_another_pull_request_does_not_count(self):
        self.put("R", kind="reviewed", pr=8, head=H1, verdict="PASS")
        self.assertFalse(self.merge().ok)

    def test_no_actor_named(self):
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        self.assertFalse(self.merge(actor="").ok)


class Brief(unittest.TestCase):
    STATE = dict(repo="r", line_tip="c" * 40, open_prs=[], disk_free_gib=6.8,
                 measured_utc="2026-11-01T00:00:00Z", session="C1")

    def test_every_required_field_refuses_when_missing_or_unmeasured(self):
        for field in sr.REQUIRED_STATE:
            missing = dict(self.STATE)
            del missing[field]
            for broken in (missing, dict(self.STATE, **{field: None}), dict(self.STATE, **{field: "NO-DATA"}),
                           dict(self.STATE, **{field: "  "})):
                with self.assertRaises(sr.BriefRefused, msg=field):
                    sr.brief(broken, "Task.")

    def test_empty_task_refuses(self):
        with self.assertRaises(sr.BriefRefused):
            sr.brief(self.STATE, " ")

    def test_state_not_an_object_refuses(self):
        for bad in (["repo"], 5, None):
            with self.assertRaises(sr.BriefRefused):
                sr.brief(bad, "Task.")

    def test_empty_pull_request_list_is_a_measurement(self):
        text = sr.brief(self.STATE, "Task.")
        self.assertIn("- open_prs: none", text)
        self.assertIn("- open_prs: 980, 981", sr.brief(dict(self.STATE, open_prs=[980, 981]), "Task."))

    def test_state_comes_before_the_task(self):
        text = sr.brief(dict(self.STATE, extra="x"), "Task.")
        self.assertTrue(text.startswith("STATE"))
        self.assertLess(text.index("- extra: x"), text.index("TASK"))


class HandoverChain(_Ledger):
    """A handover continues the last one: from the epoch it handed to, recorded by the session it handed to."""
    def hand(self, session, a, to):
        return self.put(session, kind="handover", from_epoch=a, to_epoch=a + 1, to_session=to)

    def refused(self, session, a, to):
        before = sr.read(self.path)
        with self.assertRaises(ValueError):
            self.hand(session, a, to)
        self.assertEqual(before, sr.read(self.path), "a refused handover must write nothing")

    def test_the_chain(self):
        self.hand("C1", 3, "C2")
        self.refused("C3", 3, "C4")   # a second coordinator handing over the same epoch
        self.refused("C3", 4, "C4")   # the right epoch, but C3 was never handed the authority
        self.refused("C2", 9, "C3")   # a gap
        self.refused("C2", 1, "C3")   # a rewind
        self.hand("C2", 4, "C3")
        self.refused("C2", 4, "C3")   # C2 handed its authority away: the chain is read from the LAST handover
        self.hand("C3", 5, "C4")
        self.assertEqual([4, 5, 6], [e["to_epoch"] for e in sr.read(self.path)])

    def test_other_events_do_not_break_the_chain(self):
        self.hand("C1", 3, "C2")
        self.put("W", kind="built", pr=7, head=H1)
        self.hand("C2", 4, "C3")


class Handover(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual("CONTINUE", sr.handover_due(799, 1000, 100))
        self.assertEqual("HANDOVER", sr.handover_due(800, 1000, 100))
        self.assertEqual("CONTINUE", sr.handover_due(599, 1000, 400))
        self.assertEqual("HANDOVER", sr.handover_due(600, 1000, 400))
        self.assertEqual("HANDOVER", sr.handover_due(999.5, 1000, 0))
        self.assertEqual("STOP", sr.handover_due(1500, 1000, 100))

    def test_bad_readings_raise(self):
        for bad in ((math.nan, 1000, 1), (1, math.inf, 1), (1, 1000, -1), (1, -5, 0), ("1", 1000, 1),
                    (1, 1000, None), (1, 1000, 2000)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                sr.handover_due(*bad)


class Gate(unittest.TestCase):
    def test_keeps_a_held_slot_and_its_other_variables(self):
        argv, env = sr.gate_command(["true"], {"BROTHER_HEAVY_SLOT_HELD": "0", "LOCAL_SLOTS": "4",
                                               "BROTHER_HEAVY_SLOT_WEIGHT": "3", "PATH": "/bin"})
        self.assertEqual(sys.executable, argv[0])
        self.assertEqual(os.path.join(HERE, "heavy_slot.py"), argv[1])
        self.assertEqual(("0", "1", "1", "/bin", "4"), (env["BROTHER_HEAVY_SLOT_HELD"], env["LOCAL_SLOTS"],
                                                       env["BROTHER_HEAVY_SLOT_WEIGHT"], env["PATH"],
                                                       env["BROTHER_HEAVY_SLOT_CHILD_SLOTS"]))

    def test_the_gate_child_sees_the_callers_slot_count(self):
        """heavy_slot queues the gate on one slot, but a grader inside the gate must see the caller's count: with
        the queue's count of 1 it tries slot 0 only, which its own parent holds, and waits out the slot bound."""
        tmp = tempfile.mkdtemp(prefix="xs1-gate-")
        self.addCleanup(shutil.rmtree, tmp, True)
        load = os.path.join(tmp, "load")
        with open(load, "w") as fh:
            fh.write("0.0")
        probe = ("import os, sys; sys.path.insert(0, %r); import heavy_slot as h; "
                 "held = h.try_acquire(h.slot_dir(os.environ), h.slot_count(os.environ), label=False); "
                 "print(os.environ.get('LOCAL_SLOTS'), [i for i, _ in held])" % HERE)
        base = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_HEAVY_SLOT") and k != "LOCAL_SLOTS"}
        base.update(BROTHER_SLOT_DIR=os.path.join(tmp, "slots"), BROTHER_LOAD_READING_FILE=load,
                    BROTHER_HEAVY_SLOT_WAIT="10")
        for caller, want in (({"LOCAL_SLOTS": "4"}, "4 [1]"), ({}, "None")):
            argv, env = sr.gate_command([sys.executable, "-c", probe], dict(base, **caller))
            p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertTrue(p.stdout.startswith(want), (want, p.stdout, p.stderr))

    def test_switch_off_is_dropped(self):
        self.assertNotIn("BROTHER_HEAVY_SLOT", sr.gate_command(["true"], {"BROTHER_HEAVY_SLOT": "off"})[1])

    def test_empty_command_refuses(self):
        for bad in ([], [""], "sh", None):
            with self.assertRaises(ValueError):
                sr.gate_command(bad, {})


class Cli(_Ledger):
    def run_cli(self, *args, env=None):
        p = subprocess.run([sys.executable, "-B", RELAY] + list(args), capture_output=True, text=True, env=env)
        return p.returncode, p.stdout + p.stderr

    def test_may_merge_exit_codes(self):
        self.assertEqual(0, self.run_cli("record", "--ledger", self.path, "--session", "S", "--kind", "built",
                                         "--field", "pr=7", "--field", "head=" + H1)[0])
        rc, out = self.run_cli("may-merge", "--ledger", self.path, "--pr", "7", "--head", H1, "--actor", "R")
        self.assertEqual((1, True), (rc, out.startswith("REFUSE")))
        self.put("R", kind="reviewed", pr=7, head=H1, verdict="PASS")
        rc, out = self.run_cli("may-merge", "--ledger", self.path, "--pr", "7", "--head", H1, "--actor", "R")
        self.assertEqual((0, True), (rc, out.startswith("ALLOW")))
        rc, _ = self.run_cli("may-merge", "--ledger", self.path, "--pr", "7", "--head", H1, "--actor", "S")
        self.assertEqual(1, rc)

    def test_unreadable_ledger_is_no_data(self):
        self.raw("[")
        rc, out = self.run_cli("may-merge", "--ledger", self.path, "--pr", "7", "--head", H1, "--actor", "R")
        self.assertEqual((2, True), (rc, "NO-DATA" in out))
        rc, _ = self.run_cli("may-review", "--ledger", self.path, "--pr", "7", "--reviewer", "R")
        self.assertEqual(2, rc)

    def test_may_review_exit_codes(self):
        self.put("S", kind="built", pr=7, head=H1)
        self.assertEqual(1, self.run_cli("may-review", "--ledger", self.path, "--pr", "7", "--reviewer", "S")[0])
        self.assertEqual(0, self.run_cli("may-review", "--ledger", self.path, "--pr", "7", "--reviewer", "R")[0])

    def test_record_refuses_a_bad_event(self):
        rc, out = self.run_cli("record", "--ledger", self.path, "--session", "S", "--kind", "built",
                               "--field", "pr=7", "--field", "head=abc")
        self.assertEqual((1, False), (rc, os.path.exists(self.path)))

    def test_handover(self):
        self.assertEqual((0, "HANDOVER\n"), self.run_cli("handover", "--spent", "800", "--ceiling", "1000",
                                                          "--reserve", "100"))
        self.assertEqual(2, self.run_cli("handover", "--spent", "-1", "--ceiling", "1000", "--reserve", "1")[0])

    def test_gate_runs_through_the_slot_and_returns_the_exit_code(self):
        env = dict(os.environ, BROTHER_HEAVY_SLOT_HELD="test", BROTHER_HEAVY_SLOT="off")
        self.assertEqual(3, self.run_cli("gate", "--", sys.executable, "-c", "import sys; sys.exit(3)",
                                         env=env)[0])
        self.assertEqual(1, self.run_cli("gate")[0])

    def test_no_subcommand_is_no_data(self):
        self.assertEqual(2, self.run_cli()[0])


if __name__ == "__main__":
    unittest.main()
