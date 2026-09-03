"""The receipt door, driven both ways.

Option A of the door redesign decided 2026-08-31: every delivery ends with
its own proof in plain words, the machinery keeps its records in the log, and
the release and acceptance screens are computed from receipts rather than
written by a model. These pin all four pieces, each one driven backwards as
well as forwards, because a receipt that only ever passes is decoration.
"""
import io
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
import brother_run as _br  # noqa: E402
import receipt_door as RD  # noqa: E402


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


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


# The same seam test_brother_run.py uses: a "model" that reads its declared
# write scope off the prompt and writes those files.
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

#: Internal vocabulary a person must never be handed. Each one really is
#: printed by the engine somewhere: "loop_bridge" is this script's own round
#: line, "CLAIMED (" is loop_bridge's claim announcement, "isolation:" is its
#: worktree report. The test below asserts they are ABSENT from stdout AND
#: PRESENT in the log, so "the machinery moved" cannot be satisfied by
#: deleting it.
MACHINERY = ("loop_bridge", "CLAIMED (", "isolation:")


class ARealRunEndsWithItsOwnProof(unittest.TestCase):
    """One real fixture run, through the real door, the real loop_bridge and
    a stub model, checked for what it printed and what it kept."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="receipt-door-")
        cls.repo = make_repo(cls.tmp)
        decomposer = write_stub(cls.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
            ]))
        """)
        model = write_stub(cls.tmp, "writer_model.py", WRITER_MODEL)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        cls.proc = sh([sys.executable, BROTHER_RUN, "one file exists",
                       "--cwd", cls.repo, "--runs-root", cls.tmp], env=env)
        cls.out = cls.proc.stdout + cls.proc.stderr
        runs = os.path.join(cls.tmp, "docs", "plan", "runs")
        cls.run_dir = os.path.join(runs, sorted(os.listdir(runs))[0])
        with open(os.path.join(cls.run_dir, "run.log"), encoding="utf-8") as fh:
            cls.log = fh.read()

    def test_the_run_delivered(self):
        self.assertEqual(self.proc.returncode, 0, self.out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")))

    def test_the_surface_carries_the_receipt_and_the_verdict_sentence(self):
        self.assertIn("what this run proved, one line per piece of work:",
                      self.out)
        self.assertIn("A1 delivered: the check test -f one.txt was run and "
                      "exited 0", self.out)
        self.assertIn("run.log", self.out)
        self.assertIn(RD.SCOPING_SENTENCE, self.out)

    def test_the_surface_carries_plain_progress_and_the_governor_line(self):
        self.assertIn("brother_run: working out what", self.out)
        self.assertIn("1 piece(s) of work, none finished yet", self.out)
        self.assertIn("not knowable in advance", self.out)
        self.assertIn("brother_run: round 1 done, 1 of 1 piece(s) finished, "
                      "0 to go", self.out)

    def test_the_machinery_left_the_surface_and_is_all_in_the_log(self):
        for phrase in MACHINERY:
            self.assertNotIn(phrase, self.out,
                             "%r is engine vocabulary and reached the "
                             "person:\n%s" % (phrase, self.out))
            self.assertIn(phrase, self.log,
                          "%r was DELETED rather than moved to the log; the "
                          "log must keep everything verbatim:\n%s"
                          % (phrase, self.log))

    def test_the_log_really_holds_the_output_the_receipt_points_at(self):
        """The receipt sentence promises the check's full output is in the
        run log. A promise like that is worth nothing unless the output is
        actually there, so this reads the block rather than the sentence."""
        self.assertIn("---- A1: test -f one.txt exited 0 ----", self.log)

    def test_the_acceptance_screen_is_rendered_beside_the_run(self):
        page = os.path.join(self.run_dir, "screens", "acceptance-screen.html")
        self.assertTrue(os.path.isfile(page), os.listdir(self.run_dir))
        with open(page, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("Accept this delivery", body)
        self.assertIn("test -f one.txt", body)
        self.assertIn(RD.SCOPING_SENTENCE, body)
        self.assertIn("your acceptance screen: %s" % page, self.out)
        # A plain change: no risk class named, so no release screen at all.
        self.assertFalse(os.path.isfile(
            os.path.join(self.run_dir, "screens", "release-screen.html")))
        self.assertIn("no release screen: a plain change", self.out)


class TheScopingSentenceItselfIsPinned(unittest.TestCase):
    """Every other test here asserts RD.SCOPING_SENTENCE, the symbol, never
    its words. A green suite that never read the word (this estate's own
    named failure) proved nothing: an edit that deleted a load-bearing
    clause from the product sentence still passed all of them, because the
    assertion mutated right along with the product. This test pins the
    literal text as shipped, so a deleted clause fails here even if every
    RD.SCOPING_SENTENCE assertion elsewhere still passes."""

    def test_the_literal_sentence_carries_both_load_bearing_clauses(self):
        self.assertEqual(
            RD.SCOPING_SENTENCE,
            "exit 0 means no check failed. It does not mean everything is "
            "proven: only the checks named above ran, and a check nobody "
            "wrote cannot fail.")
        self.assertIn("exit 0 means no check failed", RD.SCOPING_SENTENCE)
        self.assertIn("a check nobody wrote cannot fail",
                      RD.SCOPING_SENTENCE)


class TheSixRiskClasses(unittest.TestCase):
    """The trigger function is a pattern match over what the units declared,
    so it is testable, and it is tested on all six and on a plain change."""

    CASES = {
        "encoding": {"objective": "fix the utf-8 decoding of imported names"},
        "auth": {"objective": "refresh the login token on expiry"},
        "migration": {"objective": "backfill the new column",
                      "done_check": "python3 migrate.py --check"},
        "money": {"objective": "round the invoice total correctly"},
        "irreversibility": {"objective": "delete the abandoned lanes"},
        "public API": {"objective": "add a field to the public API response",
                       "owns": ["src/api/v1/handler.py"]},
    }

    def test_each_class_fires_on_its_own_words(self):
        for name, row in self.CASES.items():
            unit = dict({"id": "U1", "done_check": "true", "owns": []}, **row)
            hits = RD.risk_triggers([unit])
            self.assertIn(name, [h[0] for h in hits],
                          "%s did not fire on %r (fired: %r)"
                          % (name, row, hits))

    def test_a_plain_change_fires_nothing(self):
        self.assertEqual(RD.risk_triggers([
            {"id": "U1", "objective": "add a retry to the fetch helper",
             "done_check": "python3 -m pytest tests/test_fetch.py",
             "owns": ["src/fetch.py"]}]), [])

    def test_a_word_that_merely_contains_a_trigger_does_not_fire(self):
        """'author' contains 'auth' and 'deleted' is not 'delete the'. The
        boundaries matter: a screen that cries wolf on every commit is a
        screen nobody opens twice."""
        hits = RD.risk_triggers([
            {"id": "U1", "objective": "name the author of each note",
             "done_check": "true", "owns": ["docs/authors.md"]}])
        self.assertEqual([h[0] for h in hits], [], hits)

    def test_the_hit_names_the_unit_and_the_words(self):
        hits = RD.risk_triggers([
            {"id": "U9", "objective": "drop table old_sessions",
             "done_check": "true", "owns": []}])
        self.assertEqual(hits[0][0], "irreversibility")
        self.assertEqual(hits[0][1], "U9")
        self.assertIn("drop table", hits[0][2])


class MarksAreFactsNotJudgement(unittest.TestCase):
    def test_the_table_is_the_whole_of_it(self):
        self.assertEqual(RD.MARK_TABLE,
                         {"verified": 10.0, "refused": 0.0, "no-data": None})

    def test_each_state_maps_by_the_table(self):
        for state, expected in (("verified", 10.0), ("refused", 0.0),
                                ("no-data", None)):
            mark, why = RD.mark_for({"state": state, "reason": "r"})
            self.assertEqual(mark, expected, state)
            self.assertTrue(why)

    def test_no_data_is_unmarked_on_the_screen_not_a_zero(self):
        receipts = [{"id": "U1", "objective": "o", "state": "no-data",
                     "command": "", "exit_code": None, "reason": "nothing ran",
                     "output_location": "x.log"}]
        spec = RD.acceptance_spec({"outcome": "o", "rows": []}, receipts)
        self.assertEqual(spec["options"][0]["scores"], {})
        _c, _n, scored, _close = __import__("decide").rank(spec)
        self.assertEqual(scored[0]["unmarked"], [spec["criteria"][0]["label"]])
        self.assertEqual(scored[0]["total"], 0.0)

    def test_a_verified_unit_scores_the_full_mark_and_the_page_shows_it(self):
        receipts = [{"id": "U1", "objective": "create one", "state": "verified",
                     "command": "test -f one.txt", "exit_code": 0,
                     "reason": "", "output_location": "run.log"}]
        spec = RD.acceptance_spec({"outcome": "one file", "rows": []}, receipts)
        self.assertEqual(spec["options"][0]["scores"], {"U1": 10.0})
        _c, _n, scored, _close = __import__("decide").rank(spec)
        self.assertEqual(scored[0]["total"], 10.0)

    def test_a_release_spec_names_what_it_tripped_on(self):
        rows = [{"id": "U1", "objective": "rotate the oauth credentials",
                 "done_check": "true", "owns": []}]
        receipts = RD.receipts_for({"rows": rows}, {}, [], "run.log")
        triggers = RD.risk_triggers(rows)
        spec = RD.release_spec({"outcome": "o", "rows": rows}, receipts,
                               triggers)
        self.assertIn("auth", spec["plain_summary"])
        self.assertIn("Release", spec["title"])


class ReceiptsComeFromTheEvidence(unittest.TestCase):
    def test_a_refused_unit_prints_its_reason_in_the_receipt(self):
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "false"}]}
        report, _integ, refused = _br.build_report(
            record, {"U1": {"state": "failed"}}, "a", "b", changed=[],
            log_path="/tmp/run.log")
        self.assertEqual(len(refused), 1)
        self.assertIn("U1 was refused:", report)
        self.assertIn(RD.SCOPING_SENTENCE, report)

    def test_an_integrated_unit_with_no_captured_exit_code_is_no_data(self):
        """Not a pass. A claim store row saying "done" with no evidence
        behind it is exactly the shape _verify_evidence exists to refuse, and
        the receipt must not launder it into a proof."""
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "true",
                            "status": "DONE"}]}
        report, integ, _ref = _br.build_report(
            record, {"U1": {"state": "done"}}, "a", "b", changed=[],
            log_path="/tmp/run.log")
        self.assertEqual(integ, ["U1"])
        self.assertIn("U1 is NO-DATA:", report)

    def test_the_receipt_names_the_command_the_exit_code_and_the_log(self):
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "true",
                            "status": "DONE", "check_passed_before": False,
                            "files_changed_by_unit": ["x.txt"]}]}
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "python3 -m pytest tests/test_x.py",
            "exit_code": 0, "output": "1 passed", "canonical_rev": "abc"}}}
        report, _i, _r = _br.build_report(record, claims, "a", "b",
                                          changed=[], log_path="/tmp/run.log")
        self.assertIn("python3 -m pytest tests/test_x.py", report)
        self.assertIn("exited 0", report)
        self.assertIn("/tmp/run.log", report)


class TheCheckHasAnAuthorNamedOnTheReceipt(unittest.TestCase):
    """check-authorship-v1 (docs/decisions/check-authorship-v1-2026-09-03.
    json), Option A: every receipt names WHO WROTE the check it reruns.
    Nothing in this estate builds a live edit path yet, so the only way a
    row ever carries check_author == "the person" today is a test setting
    it directly, exactly as this class does; that is the whole of the data
    contract check_author_v1 asks for beyond the screen and the field."""

    def test_the_default_author_is_the_planning_model(self):
        rows = [{"id": "U1", "done_check": "true", "status": "DONE"}]
        receipts = RD.receipts_for({"rows": rows}, {"U1": {"state": "done"}},
                                   [])
        self.assertEqual(receipts[0]["author"], "the planning model")

    def test_a_row_marked_person_edited_carries_that_author_instead(self):
        rows = [{"id": "U1", "done_check": "true", "status": "DONE",
                 "check_author": "the person"}]
        receipts = RD.receipts_for({"rows": rows}, {"U1": {"state": "done"}},
                                   [])
        self.assertEqual(receipts[0]["author"], "the person")

    def test_receipt_sentence_names_the_author_before_the_verdict_word(self):
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "true",
                            "status": "DONE", "check_passed_before": False,
                            "files_changed_by_unit": ["x.txt"]}]}
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}
        report, _i, _r = _br.build_report(record, claims, "a", "b",
                                          changed=["x.txt"],
                                          log_path="/tmp/run.log")
        self.assertIn(
            "Check written by the planning model, harness NO-DATA. "
            "verdict: PASS", report)

    def test_a_person_edited_check_reads_by_the_person_on_the_report_line(self):
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "true",
                            "status": "DONE", "check_author": "the person",
                            "check_passed_before": False,
                            "files_changed_by_unit": ["x.txt"]}]}
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}
        report, _i, _r = _br.build_report(record, claims, "a", "b",
                                          changed=["x.txt"],
                                          log_path="/tmp/run.log")
        self.assertIn("Check written by the person, harness NO-DATA. "
                     "verdict: PASS", report)

    def test_a_refused_unit_still_names_an_author(self):
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "false"}]}
        report, _i, refused = _br.build_report(
            record, {"U1": {"state": "failed"}}, "a", "b", changed=[],
            log_path="/tmp/run.log")
        self.assertEqual(len(refused), 1)
        self.assertIn("Check written by the planning model, harness NO-DATA.",
                     report)


class CheckDiscriminationRefusesACheckThatAlreadyPassed(unittest.TestCase):
    """The toy-repo finding, 2026-09-03: a unit titled "add a type guard to
    add()" changed zero files and still scored a verified receipt, because
    its own done_check was already true of the untouched repository. Two
    independent facts close that hole, driven here directly against
    receipts_for the same way ReceiptsComeFromTheEvidence above drives the
    ordinary cases: `check_passed_before` (brother_run.py's own
    _stamp_prechecks, run before any worker claims a unit) and
    `files_changed_by_unit` (_mark_integrated's own git-measured file list
    for the round that finished the unit). Either one, alone, refuses a
    "verified" receipt and reads NO-DATA instead, whatever the exit code
    says."""

    def _record(self, extra_row_fields):
        row = {"id": "U1", "done_check": "true", "status": "DONE"}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_a_check_that_passed_before_any_work_is_no_data_not_verified(self):
        receipts = RD.receipts_for(
            self._record({"check_passed_before": True}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("already passed before the work began",
                     receipts[0]["reason"])

    def test_that_no_data_reaches_the_report_as_the_no_data_verdict(self):
        report, integ, _r = _br.build_report(
            self._record({"check_passed_before": True}), self._claims(),
            "a", "b", changed=["x.txt"], log_path="/tmp/run.log")
        self.assertEqual(integ, ["U1"])  # still integrated: it is a plain
        # change, never a refusal; only its RECEIPT is downgraded
        self.assertIn("U1 is NO-DATA:", report)
        self.assertIn("verdicts: 0 PASS, 0 FAIL, 1 NO-DATA", report)

    def test_a_unit_that_changed_zero_files_is_no_data_not_verified(self):
        receipts = RD.receipts_for(
            self._record({"files_changed_by_unit": []}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no file changed", receipts[0]["reason"])

    def test_a_unit_with_real_changed_files_and_a_check_that_only_now_passes_stays_verified(self):
        """The backwards half: a unit whose check FAILED before the work and
        PASSES after, with real files changed, must still read verified.
        Neither new fact fires: check_passed_before is False (measured, not
        absent) and files_changed_by_unit is non-empty."""
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False,
                          "files_changed_by_unit": ["mathlib.py"]}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")

    def test_a_row_that_predates_this_feature_is_no_data_not_verified(self):
        """Neither field present at all (every row a harness older than
        5ea2305f wrote): NOT-MEASURED must never be read as PROVEN, which is
        exactly what "verified" claimed here before this fix (the
        zero-context critic's defect 1, 2026-09-03). Superseded from its
        earlier name (...is_unaffected), which asserted the laundering
        itself as correct."""
        receipts = RD.receipts_for(self._record({}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("not recorded for this unit", receipts[0]["reason"])


class ANoDataPrecheckNeverLaundersIntoVerified(unittest.TestCase):
    """Defect 1, the zero-context critic, 2026-09-03: `receipts_for` read
    check_passed_before None (the engine's own NO-DATA, when
    _check_passes_now could not even attempt the precheck) and the key being
    wholly absent (a Work document from before 5ea2305f) both as "verified".
    An unknown must read NO-DATA, never proof, whatever the exit code
    captured alongside it says."""

    def _record(self, extra_row_fields):
        row = {"id": "U1", "done_check": "true", "status": "DONE"}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_check_passed_before_none_is_no_data_not_verified(self):
        receipts = RD.receipts_for(
            self._record({"check_passed_before": None,
                          "files_changed_by_unit": ["x.py"]}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("not recorded for this unit", receipts[0]["reason"])

    def test_check_passed_before_key_absent_is_no_data_not_verified(self):
        receipts = RD.receipts_for(
            self._record({"files_changed_by_unit": ["x.py"]}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("not recorded for this unit", receipts[0]["reason"])

    def test_files_changed_by_unit_absent_is_no_data_not_verified(self):
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("were not recorded", receipts[0]["reason"])

    def test_the_no_data_verdict_word_follows_the_state(self):
        report, _i, _r = _br.build_report(
            self._record({"check_passed_before": None}), self._claims(),
            "a", "b", changed=["x.txt"], log_path="/tmp/run.log")
        self.assertIn("U1 is NO-DATA:", report)
        self.assertIn("verdicts: 0 PASS, 0 FAIL, 1 NO-DATA", report)

    def test_the_fully_recorded_good_case_still_verifies(self):
        """Both facts measured (False, non-empty), plus a real exit 0: the
        table's own positive control, so this class cannot pass by making
        NO-DATA the only reachable answer."""
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False,
                          "files_changed_by_unit": ["mathlib.py"]}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")


class HarnessRevisionNamesTheProducerOfTheReceipt(unittest.TestCase):
    """Defect 2, the zero-context critic, 2026-09-03: a receipt did not name
    the harness that produced it, so a pre-fix record could not be told from
    a post-fix one by reading the receipt alone."""

    def _row(self):
        return {"id": "U1", "done_check": "true", "status": "DONE",
               "check_passed_before": False,
               "files_changed_by_unit": ["x.py"]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_a_full_length_sha_is_cut_to_its_first_12_hex(self):
        record = {"outcome": "o", "work_id": "w", "rows": [self._row()],
                 "harness_revision": "abcdef0123456789fedcba"}
        receipts = RD.receipts_for(record, self._claims(), [])
        self.assertEqual(receipts[0]["harness_revision"],
                         "abcdef0123456789fedcba")
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("harness abcdef012345.", sentence)
        self.assertNotIn("6789fedcba", sentence)

    def test_a_record_with_no_harness_revision_prints_no_data(self):
        record = {"outcome": "o", "work_id": "w", "rows": [self._row()]}
        receipts = RD.receipts_for(record, self._claims(), [])
        self.assertEqual(receipts[0]["harness_revision"], RD.NODATA)
        self.assertIn("harness NO-DATA.", RD.receipt_sentence(receipts[0]))

    def test_the_cost_block_carries_harness_revision_beside_harness_version(self):
        block = _br.build_cost_block({}, [], "", 1.0, "v1",
                                     "deadbeefcafefeed0000")
        self.assertIn("harness_revision", _br.COST_FIELDS)
        self.assertEqual(block["harness_revision"], "deadbeefcafefeed0000")
        self.assertEqual(block["harness_version"], "v1")

    def test_the_report_prints_the_harness_revision_line_in_the_cost_block(self):
        block = _br.build_cost_block({}, [], "", 1.0, "v1", "cafe12345678")
        record = {"outcome": "o", "work_id": "w", "rows": []}
        report, _i, _r = _br.build_report(
            record, {}, "a", "b", changed=[], log_path="/tmp/run.log",
            cost_block=block)
        self.assertIn("harness_revision: cafe12345678", report)


class ABrokenCheckIsNotAFailingCheck(unittest.TestCase):
    """Rule 4, the zero-context critic, 2026-09-03: `check_passed_before`
    alone maps ANY non-zero pre-run exit to False, the same value an
    ordinary failing assertion gets, so a check that could never run at all
    (a syntax error, a missing interpreter) got no warning and no distinct
    receipt. `check_exit_before`, stamped by brother_run.py's own
    _stamp_prechecks alongside check_passed_before, is the fact that closes
    it: the SAME command exiting the SAME non-zero code before the work and
    again after it is not two data points, it is one broken check measured
    twice."""

    def _record(self, extra_row_fields):
        row = {"id": "U1", "done_check": "python3 -c '('", "status": "DONE"}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self, exit_code):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "python3 -c '('", "exit_code": exit_code,
            "output": "", "canonical_rev": "abc"}}}

    def test_the_same_non_zero_exit_before_and_after_is_no_data(self):
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False,
                          "check_exit_before": 1,
                          "files_changed_by_unit": ["x.py"]}),
            self._claims(1), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("fails the same way before and after",
                     receipts[0]["reason"])

    def test_a_different_non_zero_exit_after_is_the_ordinary_no_data_reason(self):
        """Both non-zero, but NOT the same code: this is an ordinary check
        that failed differently, never the same-broken-check finding."""
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False,
                          "check_exit_before": 1,
                          "files_changed_by_unit": ["x.py"]}),
            self._claims(2), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertNotIn("fails the same way", receipts[0]["reason"])
        self.assertIn("exited 2", receipts[0]["reason"])

    def test_a_check_that_failed_before_and_passes_after_stays_verified(self):
        """The backwards half rule 4 itself names: real work turned a really
        failing check green, with real files changed. Nothing about the
        same-broken-check comparison may catch this, because the exit codes
        are NOT the same (1 before, 0 after)."""
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False,
                          "check_exit_before": 1,
                          "files_changed_by_unit": ["mathlib.py"]}),
            self._claims(0), [])
        self.assertEqual(receipts[0]["state"], "verified")

    def test_an_absent_check_exit_before_never_triggers_the_comparison(self):
        """No before-exit-code was ever recorded (a harness older than this
        fix): stays the ordinary NO-DATA reason, never the same-broken-check
        one, since there is nothing here to compare against."""
        receipts = RD.receipts_for(
            self._record({"check_passed_before": False,
                          "files_changed_by_unit": ["x.py"]}),
            self._claims(1), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertNotIn("fails the same way", receipts[0]["reason"])


class ACheckThatSurvivesItsDependencysRemovalIsNoData(unittest.TestCase):
    """Rule 5, EVAD run 4 trial 2, 2026-09-03: the toy's test unit failed
    before and passed after its own existence, changed a real file, and
    still proved nothing about the guard it depended on, because the
    delivered test passes with the guard deleted. brother_run.py's own
    _stamp_dependency_mutations re-runs a dependent unit's check with each
    dependency's own change reverted and stamps `check_without_dependencies`;
    receipts_for reads that stamp and nothing else. Driven both ways here
    against receipts_for directly, the same way the four rules above are."""

    def _record(self, extra_row_fields):
        row = {"id": "test", "done_check": "python3 -m pytest -k type",
               "status": "DONE", "check_passed_before": False,
               "files_changed_by_unit": ["test_mathlib.py"]}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"test": {"state": "done", "evidence": {
            "check_command": "python3 -m pytest -k type", "exit_code": 0,
            "output": "", "canonical_rev": "abc"}}}

    def _stamp(self, exit_code, note="", stderr=None):
        entry = {"unit": "guard", "files": ["mathlib.py"],
                 "revision": "abc", "exit_code": exit_code, "note": note}
        if stderr is not None:
            entry["stderr"] = stderr
        return [entry]

    def test_a_check_that_still_passes_with_the_dependency_reverted_is_no_data(self):
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(0)}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        # The reason names the unit AND the file whose reversion did not
        # change the verdict, so a reader knows what the check never touched.
        self.assertIn("still passes with guard's change to mathlib.py "
                      "reverted", receipts[0]["reason"])

    def test_a_check_that_fails_with_the_dependency_reverted_stays_verified(self):
        """The backwards half: a covering check goes red without the change
        it covers, and that is exactly what verified means here."""
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(1)}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")

    def test_a_re_run_that_broke_instead_of_failing_is_no_data(self):
        """E42, run 5 critic 3, hole H3, 2026-09-03: any non-zero exit read
        as coverage, so a check whose exit 2 came from the reverted tree no
        longer parsing proved the check needs the file and nothing about the
        behaviour. The stamped stderr tells the two apart."""
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(
                              2, stderr='  File "mathlib.py", line 3\n'
                                        "    def add(a, b\n"
                                        "SyntaxError: invalid syntax")}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertEqual(receipts[0]["reason"],
                         "the check broke with guard's files reverted rather "
                         "than failing, so nothing shows it exercises them")

    def test_a_traceback_ending_in_importerror_is_no_data(self):
        """The other broken shape the critic named: the check imports a
        symbol the dependency added, so reverting it stops the check before
        any assertion runs. Read from the traceback's LAST line."""
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(
                              1, stderr="Traceback (most recent call last):\n"
                                        "  File \"test_mathlib.py\", line 1\n"
                                        "ImportError: cannot import name "
                                        "'add' from 'mathlib'")}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("broke with guard's files reverted",
                      receipts[0]["reason"])

    def test_an_ordinary_assertion_failure_on_revert_stays_verified(self):
        """The backwards half of E42, and the one that keeps the rule from
        swallowing real coverage: the check RAN with the dependency reverted
        and failed on its own assertion, which is what a covering check must
        do. It mentions ImportError in its own output, and that is not the
        traceback's last line, so it is not a broken check."""
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(
                              1, stderr="E   ImportError was expected here\n"
                                        "E   AssertionError: add('2', 3) did "
                                        "not raise TypeError\n"
                                        "1 failed, 4 passed")}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")

    def test_a_stamp_with_no_stderr_field_reads_as_it_did_before(self):
        """A record stamped by a harness older than E42 carries no stderr at
        all. Unmeasured is not "measured broken": exit 1 keeps reading as
        coverage, exactly as it did before this rule existed."""
        self.assertNotIn("stderr", self._stamp(1)[0])
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(1)}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertFalse(RD.revert_broke_check(None))
        self.assertFalse(RD.revert_broke_check(""))

    def test_a_re_run_that_could_not_be_made_is_no_data_naming_why(self):
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(
                              None, "a throwaway worktree could not be made")}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("could not be re-run", receipts[0]["reason"])
        self.assertIn("a throwaway worktree could not be made",
                      receipts[0]["reason"])

    def test_a_declared_dependency_with_no_stamp_at_all_is_no_data(self):
        """A record from before this rule, or a run whose engine never
        stamped it: unmeasured is never proof, exactly as the absent
        check_passed_before and files_changed_by_unit already read."""
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"]}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("never re-run with that change reverted",
                      receipts[0]["reason"])

    def test_a_unit_with_no_dependency_is_untouched_by_this_rule(self):
        """The positive control: no depends_on, no stamp, and the four
        older rules all satisfied, reads verified exactly as before."""
        receipts = RD.receipts_for(self._record({}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertEqual(RD.dependency_gap({"id": "x"}), "")

    def test_the_report_counts_it_as_no_data_beside_the_dependencys_pass(self):
        """The toy's run 5 shape: the guard PASSES on its own discriminating
        check and the test that depends on it reads NO-DATA, so the summary
        line says 1 PASS, 0 FAIL, 1 NO-DATA, never 2 PASS."""
        record = self._record({"depends_on": ["guard"],
                               RD.CHECK_WITHOUT_FIELD: self._stamp(0)})
        record["rows"].insert(0, {
            "id": "guard", "done_check": "python3 -c 'import mathlib'",
            "status": "DONE", "check_passed_before": False,
            "files_changed_by_unit": ["mathlib.py"]})
        claims = self._claims()
        claims["guard"] = {"state": "done", "evidence": {
            "check_command": "python3 -c 'import mathlib'", "exit_code": 0,
            "output": "", "canonical_rev": "abc"}}
        report, integ, _r = _br.build_report(
            record, claims, "a", "b", changed=["mathlib.py", "test_mathlib.py"],
            log_path="/tmp/run.log")
        self.assertEqual(integ, ["guard", "test"])
        self.assertIn("guard delivered:", report)
        self.assertIn("test is NO-DATA: the check still passes with guard's "
                      "change to mathlib.py reverted", report)
        self.assertIn("verdicts: 1 PASS, 0 FAIL, 1 NO-DATA", report)


class TheRevertReRunKeepsItsStderr(unittest.TestCase):
    """The engine half of E42: receipt_door can only tell a broken re-run
    from a failing one if the stamp actually carries the re-run's stderr, so
    this drives brother_run's own _check_without against a real repository
    and a real throwaway worktree."""

    def test_check_without_captures_the_re_runs_stderr(self):
        tmp = tempfile.mkdtemp(prefix="receipt-door-without-")
        repo = make_repo(tmp)
        with open(os.path.join(repo, "mathlib.py"), "w", encoding="utf-8") as fh:
            fh.write("def add(a, b):\n    return a + b\n")
        sh(["git", "add", "-A"], cwd=repo)
        sh(["git", "commit", "-q", "-m", "the guard"], cwd=repo)
        dep_rev = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        with open(os.path.join(repo, "test_mathlib.py"), "w", encoding="utf-8") as fh:
            fh.write("import mathlib\n")
        sh(["git", "add", "-A"], cwd=repo)
        sh(["git", "commit", "-q", "-m", "the test"], cwd=repo)
        unit_rev = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

        seen = {}
        code, note = _br._check_without(
            repo, unit_rev, dep_rev, ["mathlib.py"],
            "python3 -c \"import sys; sys.stderr.write("
            "'SyntaxError: invalid syntax\\n'); sys.exit(2)\"",
            capture=seen)
        self.assertEqual((code, note), (2, ""))
        self.assertIn("SyntaxError", seen.get("stderr", ""))
        self.assertTrue(RD.revert_broke_check(seen.get("stderr")))


class TheLogNeverKillsTheDelivery(unittest.TestCase):
    def test_an_unwritable_log_says_so_once_and_the_run_continues(self):
        log = _br.RunLog()
        err = io.StringIO()
        with __import__("contextlib").redirect_stderr(err):
            log.to(os.path.join(tempfile.mkdtemp(), "no-such-dir"))
            log.note("first")
            log.note("second")
        self.assertEqual(err.getvalue().count("could not be written"), 1,
                         err.getvalue())
        self.assertIsNone(log.path)


class TheScreenSurvivesABadPath(unittest.TestCase):
    def test_an_unwritable_screen_is_no_data_not_an_exception(self):
        spec = RD.acceptance_spec({"outcome": "o", "rows": []}, [])
        path, problem = RD.write_screen(spec, "/proc/nope/screen.html")
        self.assertIsNone(path)
        self.assertIn(RD.NODATA, problem)

    def test_the_spec_is_written_beside_the_page(self):
        tmp = tempfile.mkdtemp(prefix="screen-")
        spec = RD.acceptance_spec({"outcome": "o", "rows": []}, [])
        path, problem = RD.write_screen(spec, os.path.join(tmp, "s.html"))
        self.assertEqual(problem, "")
        with open(os.path.join(tmp, "s.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["title"], spec["title"])
        self.assertTrue(os.path.isfile(path))


# A "model" that refuses the unit whose objective carries REFUSE_ME (standing
# in for a worker that never manages to satisfy that unit's check) and, for
# everything else, behaves like the ordinary writer stub above: it writes
# whatever the prompt's declared write scope names.
MIXED_MODEL = """
    import re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    if "REFUSE_ME" in prompt:
        sys.exit(1)
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""


class ARunWithOneVerifiedAndOneRefusedUnit(unittest.TestCase):
    """The brief's own shape: one unit verifies, one is refused, and one of
    them names a risk trigger word in its own declared scope. Both screens
    must render and the report must name both paths."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="receipt-door-2u-")
        cls.repo = make_repo(cls.tmp)
        decomposer = write_stub(cls.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "U1", "objective": "create file one",
                 "done_check": "test -f u1.txt", "writes": ["u1.txt"],
                 "deps": []},
                {"id": "U2", "objective": "delete the abandoned cache file "
                 "REFUSE_ME", "done_check": "test -f u2.txt",
                 "writes": ["u2.txt"], "deps": []},
            ]))
        """)
        model = write_stub(cls.tmp, "mixed_model.py", MIXED_MODEL)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        cls.proc = sh([sys.executable, BROTHER_RUN,
                       "one file exists and one never will",
                       "--cwd", cls.repo, "--runs-root", cls.tmp], env=env)
        cls.out = cls.proc.stdout + cls.proc.stderr
        runs = os.path.join(cls.tmp, "docs", "plan", "runs")
        cls.run_dir = os.path.join(runs, sorted(os.listdir(runs))[0])

    def test_one_unit_verified_and_one_refused(self):
        self.assertIn("integrated (1):", self.out, self.out)
        self.assertIn("refused (1):", self.out, self.out)
        self.assertIn("U1", self.out)
        self.assertIn("U2", self.out)

    def test_the_risk_trigger_renders_both_screens(self):
        acc = os.path.join(self.run_dir, "screens", "acceptance-screen.html")
        rel = os.path.join(self.run_dir, "screens", "release-screen.html")
        self.assertTrue(os.path.isfile(acc), os.listdir(
            os.path.join(self.run_dir, "screens")))
        self.assertTrue(os.path.isfile(rel))
        for page in (acc, rel):
            with open(page, encoding="utf-8") as fh:
                self.assertIn(RD.SCOPING_SENTENCE, fh.read())
        self.assertIn("your acceptance screen: %s" % acc, self.out)
        self.assertIn("release screen: %s" % rel, self.out)


class TheReceiptNamesWhatTheCheckWasShownToCover(unittest.TestCase):
    """E40 (run 5 critic 3, hole H1, 2026-09-03): a receipt read PASS on a
    test unit whose check never touched the change it claimed to cover, and
    nothing on the receipt said what the check had been shown to prove.
    Two clauses, pinned here directly against receipts_for and
    receipt_sentence: a unit that declares no dependency says so and claims
    its own change only; a verified dependent unit names the dependency
    whose files were reverted and the exit code that re-run produced. Plus
    the reading the run 7 record (2026-09-03) needed: a dependency that
    changed no file is NO-DATA with exactly that reason, never a re-run."""

    def _record(self, extra_row_fields):
        row = {"id": "cover", "done_check": "python3 test_it.py",
               "status": "DONE", "check_passed_before": False,
               "files_changed_by_unit": ["test_it.py"]}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"cover": {"state": "done", "evidence": {
            "check_command": "python3 test_it.py", "exit_code": 0,
            "output": "", "canonical_rev": "abc"}}}

    def _stamp(self, files, exit_code, note=""):
        return [{"unit": "guard", "files": files, "revision": "abc",
                 "exit_code": exit_code, "note": note}]

    def test_no_dependency_declared_reads_proves_its_own_change_only(self):
        receipts = RD.receipts_for(
            self._record({"depends_on": []}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertEqual(receipts[0]["dependency_note"],
                         "no dependency declared: this check proves its own "
                         "change only")
        self.assertIn("exited 0 (no dependency declared: this check proves "
                      "its own change only), and its full output",
                      RD.receipt_sentence(receipts[0]))

    def test_a_verified_dependent_unit_names_the_reverted_dependency_and_exit(self):
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(["mathlib.py"], 1)}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("(re-run with guard's files reverted: exit 1)", sentence)
        self.assertNotIn("proves its own change only", sentence)

    def test_a_dependency_that_changed_no_file_is_no_data_with_that_exact_reason(self):
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(
                              [], None, RD.NO_FILE_DEPENDENCY % "guard")}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertEqual(receipts[0]["reason"],
                         "its dependency guard changed no file, so nothing "
                         "shows the check exercises it")
        self.assertIn("cover is NO-DATA: its dependency guard changed no "
                      "file, so nothing shows the check exercises it.",
                      RD.receipt_sentence(receipts[0]))

    def test_an_empty_file_list_for_another_reason_keeps_that_reason(self):
        """The backwards half: files [] because the dependency's revision
        was never recorded is not the same fact as a measured no-change,
        and must not borrow its sentence."""
        receipts = RD.receipts_for(
            self._record({"depends_on": ["guard"],
                          RD.CHECK_WITHOUT_FIELD: self._stamp(
                              [], None,
                              "guard's integrated revision is not recorded")}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("could not be re-run", receipts[0]["reason"])
        self.assertNotIn("changed no file", receipts[0]["reason"])


if __name__ == "__main__":
    unittest.main()
