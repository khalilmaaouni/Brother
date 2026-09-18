#!/usr/bin/env python3
"""Tests for scripts/activation_audit.py. Stdlib only, temp dirs, no network,
no real dispatch: every "event" is a hand-written JSONL line, never an
actual call into a dispatcher.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activation_audit as aa  # noqa: E402


class ActivationAuditBase(unittest.TestCase):
    def setUp(self):
        self.tree = tempfile.mkdtemp(prefix="activation-audit-")
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)

    def _path(self, *parts):
        return os.path.join(self.tree, *parts)

    def _touch(self, rel, content="x"):
        full = self._path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return full

    def _cap(self, cap_id, dispatcher="dispatcher.py", observable="evidence/result.txt",
              **overrides):
        cap = {
            "id": cap_id,
            "trigger": "an eligible event of some kind",
            "dispatcher": dispatcher,
            "deadline_seconds": 3600,
            "observable": observable,
        }
        cap.update(overrides)
        return cap

    def _write_events(self, rel, events):
        full = self._path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")
        return full


class TestActive(ActivationAuditBase):
    def test_real_event_and_observable_is_active(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        cap = self._cap("cap-1")
        events = [{"capability": "cap-1", "dispatcher": "dispatcher.py",
                   "kind": "real", "success": True}]
        verdict, reasons = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "ACTIVE")
        self.assertTrue(reasons)


class TestCanaryOnly(ActivationAuditBase):
    def test_canary_event_only_is_canary_only(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        cap = self._cap("cap-1")
        events = [{"capability": "cap-1", "dispatcher": "dispatcher.py",
                   "kind": "canary", "success": True}]
        verdict, reasons = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "CANARY-ONLY")

    def test_real_event_always_beats_a_canary(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        cap = self._cap("cap-1")
        events = [
            {"capability": "cap-1", "dispatcher": "dispatcher.py", "kind": "canary", "success": True},
            {"capability": "cap-1", "dispatcher": "dispatcher.py", "kind": "real", "success": True},
        ]
        verdict, _ = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "ACTIVE")


class TestRegisteredOnly(ActivationAuditBase):
    def test_todays_real_shape_passing_suite_no_caller_is_registered_only(self):
        """A part with a passing registered suite and no caller must come
        out REGISTERED-ONLY, not ACTIVE: the exact finding this whole run
        keeps rediscovering."""
        self._touch("dispatcher.py")
        # observable never created: nothing ever ran the capability
        cap = self._cap("cap-1")
        verdict, reasons = aa.audit_capability(cap, self.tree, events=[])
        self.assertEqual(verdict, "REGISTERED-ONLY")
        self.assertTrue(any("no real or canary event" in r for r in reasons))

    def test_event_reached_a_different_dispatcher_is_registered_only(self):
        self._touch("dispatcher.py")
        self._touch("other_dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        cap = self._cap("cap-1", dispatcher="dispatcher.py")
        events = [{"capability": "cap-1", "dispatcher": "other_dispatcher.py",
                   "kind": "real", "success": True}]
        verdict, reasons = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "REGISTERED-ONLY")
        self.assertTrue(any("different dispatcher" in r for r in reasons))

    def test_failed_canary_is_registered_only(self):
        self._touch("dispatcher.py")
        cap = self._cap("cap-1")
        events = [{"capability": "cap-1", "dispatcher": "dispatcher.py",
                   "kind": "canary", "success": False}]
        verdict, reasons = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "REGISTERED-ONLY")
        self.assertTrue(any("canary" in r and "failed" in r for r in reasons))


class TestNoData(ActivationAuditBase):
    def test_incomplete_declaration_is_no_data_never_active(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        cap = self._cap("cap-1")
        del cap["deadline_seconds"]
        verdict, reasons = aa.audit_capability(cap, self.tree, events=[])
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(any("deadline_seconds" in r for r in reasons))

    def test_dispatcher_does_not_exist_is_no_data(self):
        cap = self._cap("cap-1", dispatcher="nowhere.py")
        verdict, reasons = aa.audit_capability(cap, self.tree, events=[])
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(any("does not exist" in r for r in reasons))

    def test_observable_never_appears_despite_real_event_is_no_data(self):
        self._touch("dispatcher.py")
        cap = self._cap("cap-1")
        events = [{"capability": "cap-1", "dispatcher": "dispatcher.py",
                   "kind": "real", "success": True}]
        verdict, reasons = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(any("never appeared" in r for r in reasons))

    def test_empty_evidence_observable_is_no_data(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "")  # exists, zero bytes
        cap = self._cap("cap-1")
        events = [{"capability": "cap-1", "dispatcher": "dispatcher.py",
                   "kind": "real", "success": True}]
        verdict, reasons = aa.audit_capability(cap, self.tree, events)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(any("empty" in r for r in reasons))

    def test_duplicate_registration_is_no_data_for_both(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        caps = [self._cap("cap-1"), self._cap("cap-1")]
        events = [{"capability": "cap-1", "dispatcher": "dispatcher.py",
                   "kind": "real", "success": True}]
        results = aa.run_audit(caps, self.tree, events)
        self.assertEqual(len(results), 2)
        for _, verdict, reasons in results:
            self.assertEqual(verdict, "NO-DATA")
            self.assertTrue(any("more than once" in r for r in reasons))

    def test_unreadable_declarations_file_is_no_data_exit_2(self):
        bad = self._path("declarations.json")
        self._touch("declarations.json", "{not json")
        rc = aa.main(["--declarations", bad])
        self.assertEqual(rc, 2)

    def test_no_capabilities_checked_is_no_data_exit_2(self):
        decl = self._path("declarations.json")
        with open(decl, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": []}, fh)
        rc = aa.main(["--declarations", decl])
        self.assertEqual(rc, 2)

    def test_corrupt_evidence_line_is_no_data_exit_2(self):
        self._touch("dispatcher.py")
        decl = self._path("declarations.json")
        with open(decl, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": [self._cap("cap-1")]}, fh)
        events_path = self._path("events.jsonl")
        with open(events_path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        rc = aa.main(["--declarations", decl, "--events", events_path, "--tree", self.tree])
        self.assertEqual(rc, 2)


class TestMainExitCodes(ActivationAuditBase):
    def _write_decl(self, caps):
        decl = self._path("declarations.json")
        with open(decl, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": caps}, fh)
        return decl

    def test_all_active_is_exit_0(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        decl = self._write_decl([self._cap("cap-1")])
        events_path = self._write_events("events.jsonl", [
            {"capability": "cap-1", "dispatcher": "dispatcher.py", "kind": "real", "success": True},
        ])
        rc = aa.main(["--declarations", decl, "--events", events_path, "--tree", self.tree])
        self.assertEqual(rc, 0)

    def test_one_registered_only_among_actives_is_exit_1_and_reported_first(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        decl = self._write_decl([self._cap("cap-1"), self._cap("cap-2")])
        events_path = self._write_events("events.jsonl", [
            {"capability": "cap-1", "dispatcher": "dispatcher.py", "kind": "real", "success": True},
        ])
        rc = aa.main(["--declarations", decl, "--events", events_path, "--tree", self.tree])
        self.assertEqual(rc, 1)

    def test_missing_events_file_means_no_events_not_an_error(self):
        self._touch("dispatcher.py")
        decl = self._write_decl([self._cap("cap-1")])
        rc = aa.main(["--declarations", decl, "--events",
                      self._path("does-not-exist.jsonl"), "--tree", self.tree])
        self.assertEqual(rc, 1)  # REGISTERED-ONLY, not a read failure

    def test_id_filter_limits_which_capabilities_are_checked(self):
        self._touch("dispatcher.py")
        self._touch("evidence/result.txt", "ran fine")
        decl = self._write_decl([self._cap("cap-1"), self._cap("cap-2")])
        events_path = self._write_events("events.jsonl", [
            {"capability": "cap-1", "dispatcher": "dispatcher.py", "kind": "real", "success": True},
        ])
        rc = aa.main(["--declarations", decl, "--events", events_path,
                      "--tree", self.tree, "--id", "cap-1"])
        self.assertEqual(rc, 0)  # cap-2 excluded, only cap-1 (ACTIVE) checked


class HomeRelativeDispatcher(unittest.TestCase):
    """Found by running the audit against the real estate: a dispatcher
    declared as ~/... was reported missing because nothing expanded it, so
    five live hooks read as NO-DATA. A gap reported where none exists is as
    harmful as a real one missed."""

    def test_a_home_marker_in_a_dispatcher_path_is_expanded(self):
        real = os.path.expanduser("~")
        here = os.path.join(real, ".claude")
        if not os.path.isdir(here):
            self.skipTest("this machine has no ~/.claude to point at")
        self.assertEqual(aa._resolve("/nowhere", "~/.claude"), here)


if __name__ == "__main__":
    unittest.main(verbosity=2)
