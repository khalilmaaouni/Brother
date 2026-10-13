#!/usr/bin/env python3
"""Tests for the intervention event of scripts/safe_unwatched_time.py (SUT1.a).

The property under test: an operator intervention is written through the one
append only journal writer as a closed vocabulary of three fields, so nothing
a person typed (a prompt body, a path, a token) can be stored, and anything
outside that vocabulary is refused and leaves the journal as it was.

Every fixture writes through the real writer (journal.append) into a temp run
folder. No network, no subprocess.
"""
import datetime
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import journal  # noqa: E402
import safe_unwatched_time as sut  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

GOOD = {"actor_class": "human", "action_kind": "scope_fix",
        "effect": "changed_run"}


def journal_bytes(run_dir):
    path = os.path.join(run_dir, journal.JOURNAL_FILENAME)
    if not os.path.exists(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


def lines(run_dir):
    return [json.loads(x) for x in
            journal_bytes(run_dir).decode("utf-8").splitlines() if x.strip()]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sut-interventions-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = os.path.join(self.tmp, "run-1")
        os.makedirs(self.run_dir)
        # One earlier event, so "the earlier lines are untouched" has a line.
        journal.append(self.run_dir, sut.EV_RUN_OPENED, payload={"units": 1})

    def quiet(self, fn, *args, **kwargs):
        with redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


class TheVocabulary(unittest.TestCase):
    def test_the_closed_tuples_are_spelled_as_the_spec_says(self):
        self.assertEqual(sut.EV_OPERATOR_INTERVENTION, "operator.intervened")
        self.assertEqual(sut.EV_CAPTURE_ON, "intervention.capture_on")
        self.assertEqual(sut.ACTOR_CLASSES, ("human", "automation"))
        self.assertEqual(sut.ACTION_KINDS, (
            "status_read", "scope_fix", "edit_file", "restart",
            "answer_prompt", "release_decision", "other"))
        self.assertEqual(sut.EFFECTS, ("none", "changed_run"))

    def test_every_combination_inside_the_vocabulary_is_accepted(self):
        for actor in sut.ACTOR_CLASSES:
            for action in sut.ACTION_KINDS:
                for effect in sut.EFFECTS:
                    ok, why = sut.valid_intervention(
                        {"actor_class": actor, "action_kind": action,
                         "effect": effect})
                    expected = not (action == "status_read"
                                    and effect == "changed_run")
                    self.assertEqual(ok, expected, (actor, action, effect, why))
                    self.assertEqual(why == "", ok)


class ValidIntervention(unittest.TestCase):
    """REQ-SUT1-02, 03, 04 and the corrupt shapes."""

    def refused(self, payload):
        ok, why = sut.valid_intervention(payload)
        self.assertFalse(ok, payload)
        self.assertTrue(why.strip(), "a refusal must say why")
        return why

    def test_an_extra_key_is_refused_whatever_it_carries(self):
        for extra in ({"prompt": "please fix the build for me"},
                      {"path": "/Users/someone/secret/notes.txt"},
                      {"token": "sk-not-a-real-token"},
                      {"note": "free text"}):
            payload = dict(GOOD, **extra)
            why = self.refused(payload)
            # The refusal never echoes what the extra key carried.
            self.assertNotIn(list(extra.values())[0], why)

    def test_a_missing_key_is_refused(self):
        for name in ("actor_class", "action_kind", "effect"):
            payload = dict(GOOD)
            del payload[name]
            self.assertIn(name, self.refused(payload))

    def test_a_value_outside_each_vocabulary_is_refused_naming_the_field(self):
        for name, bad in (("actor_class", "robot"),
                          ("action_kind", "statusRead"),
                          ("effect", "altered")):
            why = self.refused(dict(GOOD, **{name: bad}))
            self.assertIn(name, why)
            self.assertNotIn(bad, why.replace(name, ""))

    def test_empty_strings_are_refused(self):
        for name in ("actor_class", "action_kind", "effect"):
            self.assertIn(name, self.refused(dict(GOOD, **{name: ""})))

    def test_a_non_string_value_is_refused(self):
        self.refused(dict(GOOD, effect=None))
        self.refused(dict(GOOD, actor_class=["human"]))

    def test_a_status_read_cannot_claim_it_changed_the_run(self):
        why = self.refused({"actor_class": "human",
                            "action_kind": "status_read",
                            "effect": "changed_run"})
        self.assertIn("status_read", why)

    def test_a_payload_that_is_not_a_dict_is_refused(self):
        for payload in (None, "human scope_fix changed_run",
                        ["human", "scope_fix", "changed_run"], 3):
            self.refused(payload)


class RecordIntervention(Base):
    """REQ-SUT1-01, 02, 05, 06 through the real writer."""

    def test_the_written_line_carries_exactly_the_three_fields(self):
        before = datetime.datetime.now(datetime.timezone.utc)
        event_id, why = sut.record_intervention(
            self.run_dir, "human", "scope_fix", "changed_run",
            session_id="s-1")
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertTrue(event_id)
        self.assertEqual(why, "")
        last = lines(self.run_dir)[-1]
        self.assertEqual(last["event_id"], event_id)
        self.assertEqual(last["type"], sut.EV_OPERATOR_INTERVENTION)
        self.assertEqual(last["payload"], GOOD)
        self.assertEqual(last["session_id"], "s-1")
        # Time and run id are the journal's own, never the caller's.
        self.assertEqual(last["run_id"], "run-1")
        stamp = datetime.datetime.fromisoformat(last["at"])
        self.assertTrue(before <= stamp <= after, last["at"])

    def test_the_caller_cannot_supply_a_time(self):
        params = set(inspect.signature(sut.record_intervention).parameters)
        self.assertEqual(params, {"run_dir", "actor_class", "action_kind",
                                  "effect", "session_id"})
        with self.assertRaises(TypeError):
            sut.record_intervention(self.run_dir, "human", "scope_fix",
                                    "changed_run",
                                    at="2000-01-01T00:00:00+00:00")
        self.assertEqual(len(lines(self.run_dir)), 1)

    def test_it_writes_through_the_journal_writer(self):
        calls = []
        real = sut.journal.append

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        sut.journal.append = spy
        try:
            sut.record_intervention(self.run_dir, "human", "restart",
                                    "changed_run")
        finally:
            sut.journal.append = real
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1], sut.EV_OPERATOR_INTERVENTION)

    def test_a_refused_payload_writes_nothing(self):
        before = journal_bytes(self.run_dir)
        for args in (("robot", "scope_fix", "changed_run"),
                     ("human", "rm -rf", "changed_run"),
                     ("human", "status_read", "changed_run"),
                     ("", "", "")):
            event_id, why = sut.record_intervention(self.run_dir, *args)
            self.assertIsNone(event_id)
            self.assertTrue(why.strip())
        self.assertEqual(journal_bytes(self.run_dir), before)

    def test_earlier_lines_are_byte_identical_afterwards(self):
        before = journal_bytes(self.run_dir)
        self.assertTrue(before)
        sut.record_intervention(self.run_dir, "human", "edit_file",
                                "changed_run")
        sut.record_intervention(self.run_dir, "automation", "status_read",
                                "none")
        after = journal_bytes(self.run_dir)
        self.assertTrue(after.startswith(before))
        self.assertEqual(len(lines(self.run_dir)), 3)

    def test_an_unwritable_run_folder_returns_none_and_a_reason(self):
        a_file = os.path.join(self.tmp, "not-a-folder")
        with open(a_file, "w") as fh:
            fh.write("x")
        event_id, why = self.quiet(sut.record_intervention, a_file,
                                   "human", "scope_fix", "changed_run")
        self.assertIsNone(event_id)
        self.assertTrue(why.strip())
        missing = os.path.join(self.tmp, "no-such-run")
        event_id, why = self.quiet(sut.record_intervention, missing,
                                   "human", "scope_fix", "changed_run")
        self.assertIsNone(event_id)
        self.assertTrue(why.strip())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root ignores folder permissions")
    def test_a_read_only_run_folder_returns_none_and_does_not_raise(self):
        ro = os.path.join(self.tmp, "ro")
        os.makedirs(ro)
        os.chmod(ro, 0o500)
        self.addCleanup(os.chmod, ro, 0o700)
        event_id, why = self.quiet(sut.record_intervention, ro,
                                   "human", "scope_fix", "changed_run")
        self.assertIsNone(event_id)
        self.assertTrue(why.strip())

    def test_the_same_action_twice_is_two_interventions(self):
        a, _ = sut.record_intervention(self.run_dir, "human", "restart",
                                       "changed_run")
        b, _ = sut.record_intervention(self.run_dir, "human", "restart",
                                       "changed_run")
        self.assertTrue(a and b and a != b)
        kinds = [e["type"] for e in lines(self.run_dir)]
        self.assertEqual(kinds.count(sut.EV_OPERATOR_INTERVENTION), 2)

    def test_two_hundred_interventions_stay_valid_lines(self):
        for i in range(200):
            sut.record_intervention(self.run_dir, "human", "status_read",
                                    "none")
        rows = lines(self.run_dir)
        self.assertEqual(len(rows), 201)
        for row in rows[1:]:
            self.assertTrue(sut.valid_intervention(row["payload"])[0])

    def test_two_concurrent_writers_do_not_interleave(self):
        def burst(actor):
            for _ in range(40):
                sut.record_intervention(self.run_dir, actor, "other", "none")

        threads = [threading.Thread(target=burst, args=(a,))
                   for a in sut.ACTOR_CLASSES]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = lines(self.run_dir)  # json.loads raises on an interleaved line
        self.assertEqual(len(rows), 81)


class CaptureMarker(Base):
    """REQ-SUT1-07."""

    def test_the_marker_is_one_empty_payload_event(self):
        event_id = sut.mark_capture_on(self.run_dir)
        self.assertTrue(event_id)
        last = lines(self.run_dir)[-1]
        self.assertEqual(last["type"], sut.EV_CAPTURE_ON)
        self.assertEqual(last["payload"], {})
        self.assertEqual(last["event_id"], event_id)

    def test_two_markers_are_two_lines_and_neither_disturbs_the_first(self):
        before = journal_bytes(self.run_dir)
        a = sut.mark_capture_on(self.run_dir)
        b = sut.mark_capture_on(self.run_dir)
        self.assertTrue(a and b)
        self.assertTrue(journal_bytes(self.run_dir).startswith(before))
        kinds = [e["type"] for e in lines(self.run_dir)]
        self.assertEqual(kinds.count(sut.EV_CAPTURE_ON), 2)

    def test_an_unwritable_folder_returns_none(self):
        missing = os.path.join(self.tmp, "no-such-run")
        self.assertIsNone(self.quiet(sut.mark_capture_on, missing))


class TheCommandLine(Base):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = sut.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_record_prints_the_event_id_and_exits_0(self):
        code, out, _ = self.run_main(
            [self.run_dir, "--record", "human", "scope_fix", "changed_run"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), lines(self.run_dir)[-1]["event_id"])

    def test_a_refused_record_exits_2_and_writes_nothing(self):
        before = journal_bytes(self.run_dir)
        code, out, _ = self.run_main(
            [self.run_dir, "--record", "human", "status_read", "changed_run"])
        self.assertEqual(code, 2)
        self.assertTrue(out.strip())
        self.assertEqual(journal_bytes(self.run_dir), before)

    def test_mark_capture_prints_the_event_id_and_exits_0(self):
        code, out, _ = self.run_main([self.run_dir, "--mark-capture"])
        self.assertEqual(code, 0)
        self.assertEqual(lines(self.run_dir)[-1]["type"], sut.EV_CAPTURE_ON)
        self.assertEqual(out.strip(), lines(self.run_dir)[-1]["event_id"])

    def test_mark_capture_on_an_unwritable_folder_exits_2(self):
        code, _, _ = self.run_main(
            [os.path.join(self.tmp, "nope"), "--mark-capture"])
        self.assertEqual(code, 2)

    def test_record_and_mark_capture_together_are_refused(self):
        before = journal_bytes(self.run_dir)
        code, _, _ = self.run_main(
            [self.run_dir, "--mark-capture",
             "--record", "human", "scope_fix", "changed_run"])
        self.assertEqual(code, 2)
        self.assertEqual(journal_bytes(self.run_dir), before)


if __name__ == "__main__":
    unittest.main()
