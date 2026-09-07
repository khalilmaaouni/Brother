"""delivery_status: the delivery line. Six things must hold: a DONE step with
both ended_at and proof prints DONE and counts; a DONE step missing proof
prints CLAIM and fails the check (exit 1), the same tick contract
board_status.py already applies to the readiness rows; an IN PROGRESS step
reports minutes elapsed against its own started_at; a blocker prints as a
NEEDS YOU line with its age in minutes; the deadline line flips into the
FINAL HALF HOUR warning only inside the last thirty minutes; and a missing
file is NO-DATA (never a crash) at exit 2.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import delivery_status as DS  # noqa: E402


def make_doc():
    return {
        "goal": "Ship the delivery line",
        "owner": "test owner",
        "started_at": "2026-09-07T19:00:00+09:00",
        "deadline": "2026-09-07T22:30:00+09:00",
        "checkpoints": ["started", "blocked", "decision needed", "completed"],
        "steps": [
            {"id": "S1", "name": "Done step", "state": "DONE",
             "started_at": "2026-09-07T19:16:00+09:00",
             "ended_at": "2026-09-07T19:41:00+09:00",
             "expected_minutes": 25, "proof": "~/.claude/evidence/s1.log"},
            {"id": "S2", "name": "Claimed step", "state": "DONE",
             "started_at": "2026-09-07T19:16:00+09:00",
             "ended_at": None, "expected_minutes": 10, "proof": None},
            {"id": "S3", "name": "In progress step", "state": "IN PROGRESS",
             "started_at": "2026-09-07T19:29:00+09:00",
             "ended_at": None, "expected_minutes": 30, "proof": None},
            {"id": "S4", "name": "Not started step", "state": "NOT STARTED",
             "started_at": None, "ended_at": None, "expected_minutes": 12, "proof": None},
        ],
        "blockers": [
            {"what": "no push credential", "who": "founder",
             "since": "2026-09-07T19:00:00+09:00"},
        ],
        "decisions_needed": [],
        "next_event": "S3 finishes by 20:00 JST",
    }


NOW = datetime.datetime(2026, 9, 7, 20, 0, 0,
                        tzinfo=datetime.timezone(datetime.timedelta(hours=9)))


class ADoneStepWithProofCounts(unittest.TestCase):
    def test_prints_done_with_clock_duration_and_proof(self):
        lines, ok = DS.render_lines(make_doc(), now=NOW)
        s1 = [l for l in lines if l.startswith("S1")][0]
        self.assertEqual(s1, "S1  DONE at 19:41 (25 min)  proof: ~/.claude/evidence/s1.log")
        # the doc as a whole is not ok: S2 is a claim, checked in its own class below.
        self.assertFalse(ok)


class ADoneStepWithoutProofIsAClaim(unittest.TestCase):
    def test_prints_claim_and_fails_the_doc(self):
        lines, ok = DS.render_lines(make_doc(), now=NOW)
        s2 = [l for l in lines if l.startswith("S2")][0]
        self.assertEqual(s2, "S2  CLAIM  Claimed step  (says DONE but missing proof)")
        self.assertFalse(ok)

    def test_main_exits_1_on_a_claim(self):
        import tempfile
        import json
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(make_doc(), fh)
        fh.close()
        try:
            rc = DS.main(["--file", fh.name])
            self.assertEqual(rc, 1)
        finally:
            os.unlink(fh.name)


class AnInProgressStepReportsElapsed(unittest.TestCase):
    def test_elapsed_against_started_at(self):
        lines, _ = DS.render_lines(make_doc(), now=NOW)
        s3 = [l for l in lines if l.startswith("S3")][0]
        self.assertEqual(s3, "S3  IN PROGRESS  In progress step (31 min elapsed of 30 expected)")

    def test_not_started_shows_expected_only(self):
        lines, _ = DS.render_lines(make_doc(), now=NOW)
        s4 = [l for l in lines if l.startswith("S4")][0]
        self.assertEqual(s4, "S4  NOT STARTED  Not started step (expected 12 min)")


class ABlockerPrintsWithItsAge(unittest.TestCase):
    def test_needs_you_line_names_owner_and_age(self):
        lines, _ = DS.render_lines(make_doc(), now=NOW)
        needs = [l for l in lines if l.startswith("NEEDS YOU:")]
        self.assertEqual(len(needs), 1)
        self.assertEqual(needs[0],
                         "NEEDS YOU: blocker: no push credential (owner founder, 60 min since 2026-09-07T19:00:00+09:00)")

    def test_no_blockers_or_decisions_prints_nothing(self):
        doc = make_doc()
        doc["blockers"] = []
        lines, _ = DS.render_lines(doc, now=NOW)
        self.assertIn("NEEDS YOU: nothing", lines)


class TheDeadlineLineTracksTheFinalHalfHour(unittest.TestCase):
    def test_outside_the_last_thirty_minutes(self):
        lines, _ = DS.render_lines(make_doc(), now=NOW)  # 22:30 - 20:00 = 2h30m
        deadline_line = [l for l in lines if l.startswith("deadline:")][0]
        self.assertEqual(deadline_line, "deadline: 22:30 JST, 2h 30m left")
        self.assertFalse(any(l.startswith("FINAL HALF HOUR") for l in lines))

    def test_inside_the_last_thirty_minutes(self):
        now = NOW + datetime.timedelta(hours=2, minutes=20)  # 22:20, 10 min left
        lines, _ = DS.render_lines(make_doc(), now=now)
        deadline_line = [l for l in lines if l.startswith("deadline:")][0]
        self.assertEqual(deadline_line, "deadline: 22:30 JST, 0h 10m left")
        self.assertTrue(any(l.startswith("FINAL HALF HOUR") for l in lines))


class AMissingFileIsNoDataNeverACrash(unittest.TestCase):
    def test_load_reports_no_data(self):
        doc, err = DS.load("/no/such/delivery-file.json")
        self.assertIsNone(doc)
        self.assertIsNotNone(err)

    def test_main_exits_2(self):
        rc = DS.main(["--file", "/no/such/delivery-file.json"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
