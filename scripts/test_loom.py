"""The loom, driven both ways.

The committed second step of the door redesign (option A of
docs/decisions/door-redesign-2026-08-31.json): the receipt door proves what a
run did, and the loom is what a person answers. Three things are pinned here,
each one forwards and backwards, because a gate that only ever opens is
decoration and an answer nobody could refuse is not an answer.

  Parking holds a risky unit BEFORE it runs, and the proof is the file the
  parked unit would have written not being there.
  The answer is recorded and never generated: no default acceptor, no
  defaulted time, no second answer over the first, and no mark on the
  person's own decision.
  Release moves the work and hold does not, and a run continues to green
  after the release with nothing else changed.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
LOOM = os.path.join(HERE, "loom.py")
import loom  # noqa: E402
import receipt_door as RD  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=600)


def write_stub(tmpdir, name, body):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)
    return path


def make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        sh(["git"] + args, cwd=repo)
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "R0"], cwd=repo)
    return repo


# The same seam test_brother_run.py and test_receipt_door.py use: a "model"
# that reads its declared write scope off the prompt and writes those files.
WRITER_MODEL = """
    import re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""

#: One plain unit and one whose own words name an irreversible act. Both
#: declare their own write scope, so the only thing separating them is what
#: they said they were for.
DOC = {
    "work_id": "W-fixture",
    "outcome": "the archive is tidy",
    "rows": [
        {"id": "A1", "title": "create file one", "status": "SCHEDULED",
         "depends_on": [], "owns": ["one.txt"],
         "done_check": "test -f one.txt", "evidence": ""},
        {"id": "A2", "title": "delete the retired rows from the archive",
         "status": "SCHEDULED", "depends_on": [], "owns": ["two.txt"],
         "done_check": "test -f two.txt", "evidence": ""},
    ],
    "features": [],
}


def fixture_doc(tmp, doc=None):
    path = os.path.join(tmp, "W-fixture.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc or DOC, fh, indent=1)
    return path


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class ParkingHoldsTheRiskyUnitBeforeItRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-park-")
        self.path = fixture_doc(self.tmp)

    def test_only_the_unit_that_named_a_risk_class_is_parked(self):
        parked, sentences = loom.park_units(self.path)
        self.assertEqual(parked, ["A2"])
        self.assertEqual(len(sentences), 1)
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        self.assertEqual(rows["A2"]["status"], loom.PARKED_STATUS)
        self.assertEqual(rows["A1"]["status"], "SCHEDULED")
        self.assertNotIn("parked", rows["A1"])

    def test_the_park_names_the_class_and_the_words_it_hit_on(self):
        loom.park_units(self.path)
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        marker = rows["A2"]["parked"]
        self.assertEqual([t["trigger"] for t in marker["triggers"]],
                         ["irreversibility"])
        self.assertIn("delete", marker["triggers"][0]["words"])
        self.assertIn("irreversibility", marker["why"])
        self.assertIn("Nothing of it was claimed, run or merged",
                      marker["why"])

    def test_parking_twice_parks_nothing_the_second_time(self):
        loom.park_units(self.path)
        first = read(self.path)
        parked, _ = loom.park_units(self.path)
        self.assertEqual(parked, [])
        self.assertEqual(read(self.path), first)

    def test_a_released_unit_is_never_parked_again(self):
        """The backwards drive that matters most: re-parking a unit a person
        already released would undo their decision silently on the next
        resume."""
        loom.park_units(self.path)
        loom.apply_release(self.path, {"choice": "accept", "by": "K",
                                       "at": "2026-08-31", "words": "go"})
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        self.assertEqual(rows["A2"]["status"], loom.RELEASED_STATUS)
        parked, _ = loom.park_units(self.path)
        self.assertEqual(parked, [])
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        self.assertEqual(rows["A2"]["status"], loom.RELEASED_STATUS)
        self.assertEqual(loom.parked_ids(read(self.path)), [])

    def test_a_finished_unit_is_not_parked(self):
        doc = json.loads(json.dumps(DOC))
        doc["rows"][1]["status"] = "DONE"
        path = fixture_doc(os.path.join(self.tmp), doc)
        parked, _ = loom.park_units(path)
        self.assertEqual(parked, [])

    def test_a_plain_change_parks_nothing(self):
        doc = json.loads(json.dumps(DOC))
        doc["rows"] = [doc["rows"][0]]
        path = fixture_doc(self.tmp, doc)
        parked, sentences = loom.park_units(path)
        self.assertEqual((parked, sentences), ([], []))


class TheAnswerIsRecordedNeverGenerated(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp(prefix="loom-answer-")

    def test_an_answer_carries_who_when_and_the_evidence_it_was_posed_on(self):
        receipts = RD.receipts_for(
            DOC, {"A1": {"evidence": {"check_command": "test -f one.txt",
                                      "exit_code": 0}}}, [], "run.log")
        ok, path = loom.record_answer(self.run, "acceptance", "accept",
                                      "Khalil", "2026-08-31T23:10:00",
                                      "this is what I asked for", receipts)
        self.assertTrue(ok, path)
        entry = read(path)
        self.assertEqual(entry["by"], "Khalil")
        self.assertEqual(entry["at"], "2026-08-31T23:10:00")
        self.assertEqual(entry["words"], "this is what I asked for")
        posed = {r["id"]: r for r in entry["posed_on"]}
        self.assertEqual(posed["A1"]["exit_code"], 0)
        self.assertEqual(posed["A1"]["command"], "test -f one.txt")
        self.assertEqual(posed["A2"]["state"], "no-data")

    def test_the_decision_itself_carries_no_mark(self):
        ok, path = loom.record_answer(self.run, "acceptance", "hold", "K",
                                      "2026-08-31")
        self.assertTrue(ok, path)
        entry = read(path)
        self.assertIs(entry["scored"], False)
        self.assertIn("opinion wearing arithmetic", entry["why_not_scored"])
        # Nothing numeric anywhere in the record of the choice: a mark on a
        # human's decision is exactly what this file exists to refuse.
        for key, value in entry.items():
            if isinstance(value, bool):
                continue  # `scored: False` is the flag, not a mark
            self.assertNotIsInstance(value, (int, float),
                                     "%s scored the person's answer" % key)

    def test_no_receipts_is_no_data_not_an_empty_list(self):
        ok, path = loom.record_answer(self.run, "release", "accept", "K",
                                      "2026-08-31")
        self.assertTrue(ok, path)
        self.assertEqual(read(path)["posed_on"], loom.NODATA)

    def test_a_second_answer_is_refused_not_written_over_the_first(self):
        loom.record_answer(self.run, "release", "accept", "K", "2026-08-31")
        ok, reason = loom.record_answer(self.run, "release", "hold", "someone",
                                        "2026-08-31")
        self.assertFalse(ok)
        self.assertIn("already answered", reason)
        self.assertEqual(read(loom.answer_path(self.run, "release"))["choice"],
                         "accept")

    def test_the_refusals(self):
        for kwargs, expect in (
                (dict(screen="intent", choice="accept", by="K",
                      at="2026-08-31"), "not a screen"),
                (dict(screen="release", choice="maybe", by="K",
                      at="2026-08-31"), "not an answer"),
                (dict(screen="release", choice="accept", by="  ",
                      at="2026-08-31"), "no default acceptor"),
                (dict(screen="release", choice="accept", by="K",
                      at="yesterday"), "not a valid ISO"),
                (dict(screen="release", choice="accept", by="K", at=None),
                 "not a valid ISO")):
            ok, reason = loom.record_answer(
                self.run, kwargs["screen"], kwargs["choice"], kwargs["by"],
                kwargs["at"])
            self.assertFalse(ok, kwargs)
            self.assertIn(expect, reason)
        self.assertEqual(os.listdir(loom.answers_dir(self.run))
                         if os.path.isdir(loom.answers_dir(self.run)) else [],
                         [], "a refused answer still wrote a file")

    def test_an_unreadable_answer_is_no_answer(self):
        os.makedirs(loom.answers_dir(self.run), exist_ok=True)
        with open(loom.answer_path(self.run, "release"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(loom.read_answer(self.run, "release"))


class ReleaseMovesTheWorkAndHoldDoesNot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-release-")
        self.path = fixture_doc(self.tmp)
        loom.park_units(self.path)

    def test_accept_puts_the_unit_back_in_the_queue(self):
        released, held = loom.apply_release(
            self.path, {"choice": "accept", "by": "Khalil",
                        "at": "2026-08-31T23:00:00", "words": "ship it"})
        self.assertEqual((released, held), (["A2"], []))
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        self.assertEqual(rows["A2"]["status"], loom.RELEASED_STATUS)
        self.assertIn("released by Khalil", loom.park_reason(rows["A2"]))

    def test_hold_leaves_the_status_alone_and_keeps_the_persons_words(self):
        released, held = loom.apply_release(
            self.path, {"choice": "hold", "by": "Khalil",
                        "at": "2026-08-31", "words": "not before the backup"})
        self.assertEqual((released, held), ([], ["A2"]))
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        self.assertEqual(rows["A2"]["status"], loom.PARKED_STATUS)
        reason = loom.park_reason(rows["A2"])
        self.assertIn("Khalil held it", reason)
        self.assertIn("not before the backup", reason)
        self.assertEqual(loom.parked_ids(read(self.path)), ["A2"])

    def test_a_second_release_answer_changes_nothing(self):
        loom.apply_release(self.path, {"choice": "hold", "by": "K",
                                       "at": "2026-08-31", "words": "wait"})
        before = read(self.path)
        released, held = loom.apply_release(
            self.path, {"choice": "accept", "by": "someone else",
                        "at": "2026-08-31", "words": "let me in"})
        self.assertEqual((released, held), ([], []))
        self.assertEqual(read(self.path), before)

    def test_a_never_parked_row_reports_no_park_reason(self):
        rows = {r["id"]: r for r in read(self.path)["rows"]}
        self.assertEqual(loom.park_reason(rows["A1"]), "")


class TheCliRefusesWhatItCannotAnswer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loom-cli-")

    def run_loom(self, *args):
        return sh([sys.executable, LOOM] + list(args))

    def test_a_directory_with_no_work_document_is_no_data(self):
        proc = self.run_loom("show", "--run", self.tmp)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn(loom.NODATA, proc.stdout)
        self.assertIn("no single Work document", proc.stdout)

    def test_answering_a_screen_this_run_never_posed_is_no_data(self):
        fixture_doc(self.tmp)
        proc = self.run_loom("answer", "--run", self.tmp, "--screen",
                             "release", "--accept", "--by", "K", "--at",
                             "2026-08-31")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("posed no release screen", proc.stdout)
        self.assertFalse(os.path.isfile(loom.answer_path(self.tmp, "release")))

    def test_park_says_so_when_nothing_is_risky(self):
        doc = json.loads(json.dumps(DOC))
        doc["rows"] = [doc["rows"][0]]
        path = fixture_doc(self.tmp, doc)
        proc = self.run_loom("park", "--record", path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("nothing was parked", proc.stdout)

    def test_show_names_what_is_waiting(self):
        path = fixture_doc(self.tmp)
        loom.park_units(path)
        proc = self.run_loom("show", "--run", self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("A2: it is parked before it runs", proc.stdout)


class ARealRunParksTheRiskyPieceAndFinishesAfterTheRelease(unittest.TestCase):
    """One real run through the real door, the real loop_bridge and a stub
    model, with --park-risky. Then the release, then the same outcome again.

    THE PROOF THAT PARKING IS A GATE AND NOT A LABEL is two.txt not existing
    after the first run: the parked unit's own stub model would have written
    it the moment it was claimed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="loom-e2e-")
        cls.repo = make_repo(cls.tmp)
        decomposer = write_stub(cls.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
                {"id": "A2",
                 "objective": "delete the retired rows from the archive",
                 "done_check": "test -f two.txt", "writes": ["two.txt"],
                 "deps": []},
            ]))
        """)
        model = write_stub(cls.tmp, "writer_model.py", WRITER_MODEL)
        cls.env = dict(os.environ)
        cls.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        cls.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        cls.outcome = "the archive is tidy and one file exists"
        cls.first = sh([sys.executable, BROTHER_RUN, cls.outcome,
                        "--cwd", cls.repo, "--runs-root", cls.tmp,
                        "--park-risky"], env=cls.env)
        cls.out = cls.first.stdout + cls.first.stderr
        runs = os.path.join(cls.tmp, "docs", "plan", "runs")
        cls.run_dir = os.path.join(runs, sorted(os.listdir(runs))[0])

    def test_the_parked_piece_never_ran(self):
        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")),
                        self.out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "two.txt")),
                         "the parked unit ran anyway:\n%s" % self.out)
        self.assertEqual(self.first.returncode, 1, self.out)

    def test_the_surface_says_what_is_parked_and_on_which_words(self):
        self.assertIn("A2 is parked before it runs", self.out)
        self.assertIn("irreversibility", self.out)
        self.assertIn("delete", self.out)
        self.assertIn("waiting for your decision", self.out)
        self.assertIn("--screen release", self.out)

    def test_the_receipt_for_the_parked_piece_says_parked_not_unstarted(self):
        self.assertIn("A2 was refused: it is parked before it runs",
                      self.out)
        self.assertNotIn("A2         it was never started this run", self.out)
        self.assertIn(RD.SCOPING_SENTENCE, self.out)

    def test_the_release_screen_is_rendered_for_a_person_to_answer(self):
        page = os.path.join(self.run_dir, "screens", "release-screen.html")
        self.assertTrue(os.path.isfile(page), os.listdir(self.run_dir))
        with open(page, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("Release this change, or hold it", body)
        self.assertIn("irreversibility", body)

    def test_the_release_then_the_same_outcome_again_finishes_it(self):
        answered = sh([sys.executable, LOOM, "answer", "--run", self.run_dir,
                       "--screen", "release", "--accept", "--by", "Khalil",
                       "--at", "2026-08-31T23:30:00", "--words",
                       "the archive is backed up"])
        self.assertEqual(answered.returncode, 0,
                         answered.stdout + answered.stderr)
        self.assertIn("released and back in the queue", answered.stdout)
        entry = read(loom.answer_path(self.run_dir, "release"))
        self.assertIs(entry["scored"], False)
        self.assertEqual(entry["by"], "Khalil")

        second = sh([sys.executable, BROTHER_RUN, self.outcome, "--cwd",
                     self.repo, "--runs-root", self.tmp], env=self.env)
        out = second.stdout + second.stderr
        self.assertEqual(second.returncode, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "two.txt")),
                        out)
        self.assertIn("A2 delivered: the check test -f two.txt was run and "
                      "exited 0", out)
        self.assertIn("you answered the release screen", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
