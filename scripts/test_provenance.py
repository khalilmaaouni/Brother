#!/usr/bin/env python3
"""provenance's own suite. Hermetic: every fixture is built in a temp directory, nothing reads the live plan,
nothing writes the real journal, and it passes with an empty HOME.

FIXTURES ARE ORTHOGONAL ON PURPOSE. A fixture that trips two guards at once proves neither of them, so each
case below breaks exactly one property: the actor tests never touch a path, the journal tests never touch the
environment, and the unwritable path case is a path that cannot exist rather than one that is also malformed."""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import provenance as P  # noqa: E402


class TheStampCarriesTheFourFacts(unittest.TestCase):
    """A DONE appeared at 21:25:12 with no author. These four keys are what that write was missing."""

    def test_all_four_keys_are_present(self):
        self.assertEqual(set(P.stamp("close_unit.py", because="x.py exit 0")),
                         {"changed_by", "changed_via", "changed_at", "changed_because"})

    def test_the_timestamp_is_a_real_parseable_reading_with_an_offset(self):
        at = P.stamp("close_unit.py")["changed_at"]
        parsed = datetime.datetime.fromisoformat(at)          # a narrative time would not survive this
        self.assertIsNotNone(parsed.utcoffset(), "a reading with no offset cannot be compared across machines")

    def test_the_timestamp_is_the_clock_now_not_a_constant(self):
        # The estate's law came from timestamps written from a session's SENSE of elapsed time, four hours out.
        drift = abs((datetime.datetime.now().astimezone()
                     - datetime.datetime.fromisoformat(P.stamp("v")["changed_at"])).total_seconds())
        self.assertLess(drift, 120, "changed_at is not being read from the clock")

    def test_the_mechanism_is_the_callers_word_not_a_guess(self):
        self.assertEqual(P.stamp("land_batch.py")["changed_via"], "land_batch.py")

    def test_the_reason_is_kept_verbatim_because_it_must_be_re_runnable(self):
        self.assertEqual(P.stamp("close_unit.py", because="donecheck_bl.py exit 0")["changed_because"],
                         "donecheck_bl.py exit 0")


class TheActorIsResolvedNeverAccepted(unittest.TestCase):
    """58 sessions were live on this machine. An actor field that is blank, or that looks like a name nobody
    chose, is worse than one that admits it found nothing."""

    def test_an_explicit_actor_wins(self):
        self.assertEqual(P.resolve_actor("bl-qa", environ={"CLAUDE_CODE_SESSION_ID": "abc"}), "bl-qa")

    def test_an_absent_actor_resolves_to_hand_and_never_crashes(self):
        self.assertEqual(P.resolve_actor(None, environ={}), "hand")

    def test_an_empty_environment_yields_no_finding_rather_than_a_guess(self):
        self.assertIsNone(P.env_actor(environ={}))

    def test_an_env_id_is_recorded_with_the_variable_that_produced_it(self):
        # Never silently a wrong name: a bare uuid could be read back as a person's identifier, so the field
        # says WHICH variable it came from and asserts nothing more than was actually known.
        got = P.resolve_actor(None, environ={"CLAUDE_CODE_SESSION_ID": "16e07b1a"})
        self.assertEqual(got, "CLAUDE_CODE_SESSION_ID=16e07b1a")

    def test_the_most_distinguishing_id_wins_when_several_are_set(self):
        # AI_AGENT is identical in every session on this machine, so it can never separate two writers.
        got = P.resolve_actor(None, environ={"AI_AGENT": "claude-code_agent", "CLAUDE_CODE_SESSION_ID": "u1"})
        self.assertEqual(got, "CLAUDE_CODE_SESSION_ID=u1")

    def test_a_blank_env_value_is_not_an_identity(self):
        self.assertEqual(P.resolve_actor(None, environ={"CLAUDE_CODE_SESSION_ID": "   "}), "hand")

    def test_a_blank_explicit_actor_falls_through_instead_of_recording_nothing(self):
        self.assertEqual(P.resolve_actor("   ", environ={}), "hand")


class ApplyKeepsTheRecord(unittest.TestCase):
    """apply() runs on live plan units whose other fields ARE the work."""

    def test_existing_keys_survive(self):
        unit = {"id": "BL2", "state": "DONE", "evidence": "long paragraph", "sub_units": ["a", "b"]}
        out = P.apply(unit, "close_unit.py")
        self.assertEqual(out["id"], "BL2")
        self.assertEqual(out["evidence"], "long paragraph")
        self.assertEqual(out["sub_units"], ["a", "b"])
        self.assertEqual(out["state"], "DONE")

    def test_the_stamp_is_added(self):
        self.assertEqual(P.apply({"id": "BL2"}, "close_unit.py")["changed_via"], "close_unit.py")

    def test_a_non_dict_is_refused_loudly_rather_than_losing_the_stamp(self):
        with self.assertRaises(TypeError):
            P.apply(["not", "a", "record"], "close_unit.py")


class TheJournalIsAppendOnly(unittest.TestCase):
    """Git gives file history but not semantic history, and an amend or a squash loses it."""

    def test_an_append_does_not_lose_the_previous_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            self.assertTrue(P.journal({"n": 1}, path))
            self.assertTrue(P.journal({"n": 2}, path))
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual([r["n"] for r in rows], [1, 2])

    def test_one_entry_is_always_exactly_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            P.journal({"quoted": "Ran 29 tests\nOK\nsecond line"}, path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().splitlines()), 1)

    def test_a_missing_parent_directory_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deep", "er", "j.jsonl")
            self.assertTrue(P.journal({"n": 1}, path))
            self.assertTrue(os.path.isfile(path))


class TwoProcessesBothSurvive(unittest.TestCase):
    """The measured condition, not a hypothetical: several of the 58 live sessions are on this estate, so two
    writers appending at the same moment is the normal case."""

    CHILD = ("import sys\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "import provenance as P\n"
             "pad = sys.argv[3] * 400\n"
             "for i in range(60):\n"
             "    assert P.journal({'who': sys.argv[3], 'i': i, 'pad': pad}, sys.argv[2])\n")

    def test_both_children_lines_are_all_present_and_none_is_torn(self):
        loop_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            child = os.path.join(tmp, "child.py")
            with open(child, "w", encoding="utf-8") as fh:
                fh.write(self.CHILD)
            procs = [subprocess.Popen([sys.executable, child, loop_dir, path, who]) for who in ("A", "B")]
            codes = [p.wait(timeout=120) for p in procs]
            self.assertEqual(codes, [0, 0], "a child failed to append")
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]   # a torn line raises here
            self.assertEqual(len(rows), 120)
            self.assertEqual(sorted(r["i"] for r in rows if r["who"] == "A"), list(range(60)))
            self.assertEqual(sorted(r["i"] for r in rows if r["who"] == "B"), list(range(60)))


class AFailureNeverTakesTheCallerDown(unittest.TestCase):
    """A provenance failure must never block the work it is describing: a full disk must not stop a closure."""

    def test_an_unwritable_path_returns_false_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")       # a FILE where a parent directory would have to be,
            with open(blocker, "w", encoding="utf-8") as fh:   # so makedirs cannot succeed and open cannot
                fh.write("x")
            self.assertIs(P.journal({"n": 1}, os.path.join(blocker, "sub", "j.jsonl")), False)

    def test_an_unserialisable_entry_returns_false_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            self.assertIs(P.journal({"bad": object()}, path), False)
            self.assertFalse(os.path.exists(path), "a refused entry must not leave a half written file")


class NoDataIsNeverAPass(unittest.TestCase):
    """The estate's standing rule: an absence composes into a refusal, never into a green."""

    def test_a_failed_write_is_false_not_none_so_it_cannot_read_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("x")
            verdict = P.journal({"n": 1}, os.path.join(blocker, "sub", "j.jsonl"))
            self.assertIs(verdict, False)
            self.assertFalse(bool(verdict), "a caller writing `if journal(...)` must not read a failure as done")

    def test_a_successful_write_is_true_so_the_two_verdicts_are_distinguishable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIs(P.journal({"n": 1}, os.path.join(tmp, "j.jsonl")), True)

    def test_the_three_mandatory_fields_are_never_none(self):
        # changed_because is allowed to be absent; the other three never are, or the stamp says nothing.
        st = P.stamp("close_unit.py")
        for key in ("changed_by", "changed_via", "changed_at"):
            self.assertIsInstance(st[key], str)
            self.assertTrue(st[key].strip(), "%s is blank, which is an absence dressed as an answer" % key)

    def test_an_empty_mechanism_is_named_as_unknown_rather_than_left_blank(self):
        self.assertEqual(P.stamp("   ")["changed_via"], "unknown-mechanism")


class CloseUnitIsWired(unittest.TestCase):
    """The whole point: the program that made the unattributable write now stamps and journals."""

    def test_close_unit_stamps_and_journals_on_its_closure_path(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import close_unit as C
        with open(C.__file__, encoding="utf-8") as fh:
            src = fh.read()
        # assertIn would otherwise print the whole file; the message says what is missing instead.
        self.assertTrue("provenance.apply(unit" in src, "close_unit.py no longer stamps the unit")
        self.assertTrue("provenance.journal(" in src, "close_unit.py no longer journals the transition")

    def test_the_reason_it_records_is_the_done_check_and_its_exit_code(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import close_unit as C
        with open(C.__file__, encoding="utf-8") as fh:
            self.assertTrue('because = "%s exit %d" % (cmd, r.returncode)' in fh.read(),
                            "close_unit.py no longer records the done_check and its exit code")


if __name__ == "__main__":
    unittest.main(verbosity=1)
