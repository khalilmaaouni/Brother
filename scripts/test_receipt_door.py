"""The receipt door, driven both ways.

Option A of the door redesign decided 2026-08-31: every delivery ends with
its own proof in plain words, the machinery keeps its records in the log, and
the release and acceptance screens are computed from receipts rather than
written by a model. These pin all four pieces, each one driven backwards as
well as forwards, because a receipt that only ever passes is decoration.
"""
import html
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

# Minor fix (opus-review-seams-g1-g3.md): redirect jev_seam's own
# machine-level state root to a throwaway temp dir BEFORE jev_seam is
# ever imported in this process, so no test here reads (or could ever
# write) the real ~/.brother/jev -- must happen before any import below
# that could reach jev_seam.py: JEV_STATE_DIR is a module-level constant
# jev_seam.py computes once, at its own import time.
os.environ.setdefault("BROTHER_JEV_STATE_DIR",
                       tempfile.mkdtemp(prefix="brother-jev-state-test-"))

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
import accept_delivery as _ad  # noqa: E402
import brother_run as _br  # noqa: E402
import claim_store  # noqa: E402
import journal  # noqa: E402
import journal_projection  # noqa: E402
import decide  # noqa: E402
import receipt_door as RD  # noqa: E402
import jev_seam  # noqa: E402
import jev_g1_seam_cache  # noqa: E402
import jev_checks  # noqa: E402

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

#: The teaching manifest P7's own brief names: products/brothersbe/docs/
#: for-engineers/examples/data-warehouse/numbers-manifest.json, which the
#: constraint "do not write into the examples" keeps read-only here; every
#: test below copies it into a temp root instead of editing it in place.
EXAMPLE_MANIFEST = os.path.join(
    os.path.dirname(HERE), "products", "brothersbe", "docs", "for-engineers",
    "examples", "data-warehouse", "numbers-manifest.json")


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
        env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(decomposer))
        env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(model))
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

    def test_the_run_prints_its_receipt_at_the_end_and_writes_it_beside_it(self):
        """E72.3: the same real run, read for the receipt. Printed at the
        end of the report a person actually sees, written into the run
        directory, and every one of the eight questions on both."""
        page = os.path.join(self.run_dir, "screens", "delivery-receipt.html")
        self.assertTrue(os.path.isfile(page), os.listdir(
            os.path.join(self.run_dir, "screens")))
        record_path = os.path.splitext(page)[0] + ".json"
        self.assertTrue(os.path.isfile(record_path))
        with open(record_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        for field in ("scope", "intent", "evidence", "unproven",
                      "repair_history", "attention", "containment",
                      "continuity"):
            self.assertIn(field, spec["receipt_record"])
        self.assertIn("the page: %s" % page, self.out)
        self.assertIn("the receipt for this delivery, in eight answers:",
                      self.out)
        for _key, question in RD.RECEIPT_QUESTIONS:
            self.assertIn(question, self.out)
        # AT THE END: after the screens the report already named, not
        # somewhere in the middle of the run's own progress lines.
        self.assertGreater(
            self.out.index("the receipt for this delivery"),
            self.out.index("your acceptance screen:"))
        # The run's own facts, not a template: this run changed one.txt and
        # proved it with the check it declared.
        self.assertIn("one.txt", self.out)
        self.assertIn("A1 ran 'test -f one.txt'", self.out)

    def test_the_receipt_needed_no_new_flag(self):
        """The row's own words: no new visible command. --help is read for
        an option that mentions the receipt, and there must not be one."""
        proc = sh([sys.executable, BROTHER_RUN, "--help"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        options = [word for line in proc.stdout.splitlines()
                   for word in line.split() if word.startswith("--")]
        self.assertTrue(options, proc.stdout)
        self.assertEqual([o for o in options if "receipt" in o.lower()], [])


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


class TheThreeDataScienceForcingClasses(unittest.TestCase):
    """P10 (persona integration plan 2026-09-04, row P10): three more risk
    classes, read from scripts/packs/data-science.json's own forcing_classes
    through door.load_pack rather than typed here. A hit parks the unit
    before it is claimed exactly as the base six do (loom.py reads the same
    function); tested here the same way as TheSixRiskClasses above, one
    matching objective and one non-matching per class.

    ARMED BY THE INFERRED LENS since the eleven persona packs landed: every
    call below names data-science, because these three classes belong to
    that pack and a run that did not infer it must not fire them."""

    LENSES = ["data-science"]

    MATCHING = {
        "promotion": "model promotion to production after the review board",
        "threshold_change": "threshold change for the fraud decision boundary",
        "training_data_refresh": "training data refresh for this quarter's "
                                  "new customers",
    }
    NON_MATCHING = {
        # word-bounded: "promotional" must not fire "promotion" (the id's
        # own word ends at a boundary "promotional" never reaches).
        "promotion": "use a promotional banner for the launch",
        "threshold_change": "adjust the threshold slightly for display",
        "training_data_refresh": "refresh the training materials for "
                                  "onboarding",
    }

    def test_each_class_fires_on_its_matching_objective(self):
        for name, objective in self.MATCHING.items():
            unit = {"id": "U1", "objective": objective, "done_check": "true",
                    "owns": []}
            hits = RD.risk_triggers([unit], self.LENSES)
            self.assertIn(name, [h[0] for h in hits],
                          "%s did not fire on %r (fired: %r)"
                          % (name, objective, hits))

    def test_each_class_does_not_fire_on_its_non_matching_objective(self):
        for name, objective in self.NON_MATCHING.items():
            unit = {"id": "U1", "objective": objective, "done_check": "true",
                    "owns": []}
            hits = RD.risk_triggers([unit], self.LENSES)
            self.assertNotIn(name, [h[0] for h in hits],
                             "%s wrongly fired on %r (fired: %r)"
                             % (name, objective, hits))

    def test_the_three_classes_are_armed_by_their_own_lens(self):
        names = [name for name, _pattern
                 in RD._lens_forcing_triggers(self.LENSES)]
        for name in self.MATCHING:
            self.assertIn(name, names)

    def test_they_are_not_armed_for_a_run_that_inferred_no_lens(self):
        """The always-armed set is the six base classes and core's, which
        repeat those six ids: a pack's classes reach a run only through the
        lenses that run inferred."""
        names = [name for name, _pattern in RD.RISK_TRIGGERS]
        for name in self.MATCHING:
            self.assertNotIn(name, names)

    def test_an_unreadable_pack_reads_no_data_and_the_base_six_still_apply(
            self):
        tmp = tempfile.mkdtemp(prefix="receipt-door-ds-pack-nodata-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        stderr = io.StringIO()
        real_stderr, sys.stderr = sys.stderr, stderr
        try:
            triggers = RD._pack_forcing_triggers("data-science",
                                                  packs_dir=tmp)
        finally:
            sys.stderr = real_stderr
        self.assertEqual(triggers, ())
        self.assertIn("NO-DATA", stderr.getvalue())
        # the six base classes are unaffected by a missing DS pack: a plain
        # money-shaped objective still fires "money" with no DS pack loaded.
        hits = RD.risk_triggers([
            {"id": "U1", "objective": "round the invoice total correctly",
             "done_check": "true", "owns": []}])
        self.assertIn("money", [h[0] for h in hits])


class APacksClassesAreArmedByInferenceNotByInstallation(unittest.TestCase):
    """The defect this closes, measured on this branch before the fix: with
    eleven persona packs installed, every pack's forcing classes were armed
    on every run, so "add a retry to the fetch helper" parked on the backend
    pack's "retry" class in a repository that is not a service, and any
    backfill parked on the data engineering pack's "backfill" class. The
    classes are real; arming them everywhere is what made them noise.

    Selection is already compositional (persona doc 5.2): the risk surface
    is the union over the lenses a run INFERRED, plus core."""

    RETRY_UNIT = {"id": "U1", "objective": "add a retry to the fetch helper",
                  "done_check": "python3 -m pytest tests/test_fetch.py",
                  "owns": ["src/fetch.py"]}
    PLAIN_UNIT = {"id": "U2", "objective": "rename a local variable in the "
                                          "fetch helper",
                  "done_check": "python3 -m pytest tests/test_fetch.py",
                  "owns": ["src/fetch.py"]}

    def test_the_backend_class_fires_under_its_own_lens(self):
        hits = RD.risk_triggers([self.RETRY_UNIT], ["backend-senior"])
        self.assertIn("retry", [h[0] for h in hits], hits)

    def test_it_does_not_fire_under_that_lens_without_the_words(self):
        hits = RD.risk_triggers([self.PLAIN_UNIT], ["backend-senior"])
        self.assertEqual([h[0] for h in hits], [], hits)

    def test_no_pack_class_fires_when_no_lens_was_inferred(self):
        """The regression itself: this list was [('retry', 'U1', 'retry')]
        before the fix and is empty after it."""
        self.assertEqual(RD.risk_triggers([self.RETRY_UNIT]), [])
        self.assertEqual(RD.risk_triggers([self.RETRY_UNIT], []), [])

    def test_the_base_six_still_fire_with_no_lens(self):
        """Scoping the pack classes must not disarm the base classes: they
        are the ones that do not belong to any persona."""
        hits = RD.risk_triggers([
            {"id": "U3", "objective": "round the invoice total correctly",
             "done_check": "true", "owns": []}])
        self.assertIn("money", [h[0] for h in hits], hits)

    def test_a_composed_run_arms_every_lens_it_inferred(self):
        hits = RD.risk_triggers(
            [self.RETRY_UNIT,
             {"id": "U4", "objective": "model promotion to production",
              "done_check": "true", "owns": []}],
            ["backend-senior", "data-science"])
        fired = [h[0] for h in hits]
        self.assertIn("retry", fired, hits)
        self.assertIn("promotion", fired, hits)

    def test_record_lenses_reads_the_composed_list_then_the_single_lens(self):
        self.assertEqual(RD.record_lenses(
            {"lens_inferred": {"lens": "backend-senior", "lenses": [
                {"lens": "backend-senior", "matched_paths": ["openapi.yaml"]},
                {"lens": "core", "matched_paths": []}]}}),
            ["backend-senior", "core"])
        self.assertEqual(RD.record_lenses(
            {"lens_inferred": {"lens": "qa-engineer", "matched_paths": []}}),
            ["qa-engineer"])
        self.assertEqual(RD.record_lenses({}), [])
        self.assertEqual(RD.record_lenses({"lens_inferred": None}), [])

    def test_an_unknown_lens_name_is_no_data_and_arms_the_rest(self):
        """A record naming a pack this checkout does not carry must not cost
        the other lenses their classes, and must say so rather than pass in
        silence."""
        stderr = io.StringIO()
        real_stderr, sys.stderr = sys.stderr, stderr
        try:
            hits = RD.risk_triggers([self.RETRY_UNIT],
                                    ["no-such-pack", "backend-senior"])
        finally:
            sys.stderr = real_stderr
        self.assertIn("retry", [h[0] for h in hits], hits)
        self.assertIn("NO-DATA", stderr.getvalue())


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


class EvidenceFamilyOracleSourceAndIndependenceOnTheReceipt(unittest.TestCase):
    """P1 (persona integration plan, doc 6.2): a PASS from an oracle the
    same work also generated must not read with the confidence of an
    independent one. work_record.py's independence_for is generated_from_
    impl -> circular_risk, everything else named -> independent, nothing
    named -> unverified; receipts_for and receipt_sentence must carry that
    straight through to the one line a skeptic reads."""

    def _record(self, extra_row_fields):
        row = {"id": "U1", "done_check": "true", "status": "DONE",
               "check_passed_before": False,
               "files_changed_by_unit": ["x.txt"]}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_generated_from_impl_reads_circular_risk_on_the_receipt(self):
        receipts = RD.receipts_for(
            self._record({"evidence_family": "E18",
                          "oracle_source": "generated_from_impl"}),
            self._claims(), [])
        self.assertEqual(receipts[0]["independence"], "circular_risk")
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("circular_risk", sentence)
        self.assertIn("family E18", sentence)
        self.assertIn("oracle generated_from_impl", sentence)

    def test_independent_query_reads_independent_on_the_receipt(self):
        receipts = RD.receipts_for(
            self._record({"evidence_family": "E18",
                          "oracle_source": "independent_query"}),
            self._claims(), [])
        self.assertEqual(receipts[0]["independence"], "independent")
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("independent", sentence)
        self.assertNotIn("circular_risk", sentence)

    def test_no_oracle_source_at_all_reads_unverified_not_a_clean_pass(self):
        receipts = RD.receipts_for(self._record({}), self._claims(), [])
        self.assertEqual(receipts[0]["independence"], "unverified")
        self.assertEqual(receipts[0]["evidence_family"], RD.NODATA)
        self.assertEqual(receipts[0]["oracle_source"], RD.NODATA)
        self.assertIn("unverified", RD.receipt_sentence(receipts[0]))


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


class TheRedBeforeGreenWitness(unittest.TestCase):
    """U5, 2026-09-09: receipt_door could already tell that a check passed
    before the change or survives its dependency's reversal, but nothing
    recorded that the unit's OWN done_check was ever observed failing
    before the implementation existed (Superpowers' TDD discipline, GSD's
    add-tests naming RED then GREEN). row["change_kind"] == "behaviour"
    is the opt-in this gate reads, the same shape e18_gap reads
    evidence_family == "E18": a row that declares no change_kind at all
    (every row this estate has ever written before this feature) is not
    this gate's business and stays verified exactly as before."""

    def _record(self, extra_row_fields):
        row = {"id": "U1", "done_check": "true", "status": "DONE",
               "check_passed_before": False,
               "files_changed_by_unit": ["mathlib.py"]}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_a_behaviour_changing_unit_with_no_red_check_is_no_data(self):
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour"}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("red check never observed", receipts[0]["reason"])

    def test_a_red_check_that_exited_0_is_no_data(self):
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour",
                          "red_check": {"command": "true",
                                       "exit_code": 0,
                                       "output_location": "/tmp/red.log",
                                       "revision": "deadbeef",
                                       "pre_implementation": True}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("check passed before the work", receipts[0]["reason"])

    def test_a_recorded_non_zero_red_check_stays_verified(self):
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour",
                          "red_check": {"command": "true",
                                       "exit_code": 1,
                                       "output_location": "/tmp/red.log",
                                       "revision": "deadbeef",
                                       "pre_implementation": True}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        red = receipts[0]["red_check"]
        self.assertEqual(red["exit_code"], 1)
        self.assertEqual(red["revision"], "deadbeef")
        self.assertTrue(red["pre_implementation"])

    def test_a_documentation_only_unit_is_exempt_and_says_so(self):
        receipts = RD.receipts_for(
            self._record({"change_kind": "documentation",
                          "files_changed_by_unit": ["README.md"]}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertTrue(receipts[0]["red_check"]["exempt"])
        self.assertIn("documentation-only",
                     receipts[0]["red_check"]["reason"])

    def test_a_generated_file_only_unit_is_exempt_and_says_so(self):
        receipts = RD.receipts_for(
            self._record({"change_kind": "generated",
                          "files_changed_by_unit": ["docs/plan/manifest.json"]}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertTrue(receipts[0]["red_check"]["exempt"])
        self.assertIn("generated-file-only",
                     receipts[0]["red_check"]["reason"])

    def test_a_documentation_claim_over_a_code_file_is_refused(self):
        """U5 review finding 4, 2026-09-09: a unit can declare
        change_kind "documentation" while its own files_changed_by_unit
        names a .py file. The exemption is refused rather than trusted,
        naming the offending path, and the row is then held to the same
        red-before-green standard a "behaviour" row would be (no-data,
        for want of a red_check, exactly like the first test above)."""
        receipts = RD.receipts_for(
            self._record({"change_kind": "documentation"}),  # mathlib.py
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertFalse(receipts[0]["red_check"]["exempt"])
        self.assertIn("mathlib.py", receipts[0]["red_check"]["exemption_refused"])
        self.assertIn("mathlib.py", receipts[0]["reason"])

    def test_a_generated_claim_over_a_code_file_is_refused(self):
        receipts = RD.receipts_for(
            self._record({"change_kind": "generated"}),  # mathlib.py
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertFalse(receipts[0]["red_check"]["exempt"])
        self.assertIn("mathlib.py", receipts[0]["red_check"]["exemption_refused"])

    def test_a_row_with_no_change_kind_at_all_is_unaffected(self):
        """Backward compatibility, driven directly: the entire pre-U5
        corpus never wrote change_kind, and none of it must read
        differently now."""
        receipts = RD.receipts_for(self._record({}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertFalse(receipts[0]["red_check"]["exempt"])

    def test_a_red_check_run_on_a_different_command_is_no_data(self):
        """U5 review finding 2, 2026-09-09: red_check_gap read only
        exit_code, so a red_check copied from a DIFFERENT unit's check
        (or simply mistyped) satisfied the witness as long as its exit
        code was non-zero. The command must be THIS row's own
        done_check."""
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour",
                          "red_check": {"command": "pytest -k unrelated",
                                       "exit_code": 1,
                                       "output_location": "/tmp/red.log",
                                       "revision": "deadbeef",
                                       "pre_implementation": True}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("not this unit's own done_check", receipts[0]["reason"])

    def test_a_red_check_with_no_output_location_is_no_data(self):
        """U5 review finding 2: exit_code and command alone are not a
        witness a reader can go verify; the capture has to be findable."""
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour",
                          "red_check": {"command": "true",
                                       "exit_code": 1,
                                       "revision": "deadbeef",
                                       "pre_implementation": True}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no output location", receipts[0]["reason"])

    def test_a_red_check_not_marked_pre_implementation_is_no_data(self):
        """U5 review finding 3, 2026-09-09: pre_implementation was stored
        and never consulted, so a red_check recorded AFTER the fix (or
        never confirmed to precede it) still satisfied the witness."""
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour",
                          "red_check": {"command": "true",
                                       "exit_code": 1,
                                       "output_location": "/tmp/red.log",
                                       "revision": "deadbeef",
                                       "pre_implementation": False}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("pre_implementation was not True", receipts[0]["reason"])

    def test_a_red_check_with_pre_implementation_unrecorded_is_no_data(self):
        """The other shape of the same gap: a revision recorded with no
        pre_implementation flag at all reads None, not True, and a
        red_check whose revision could just as easily be one taken AFTER
        the work must not be trusted by default."""
        receipts = RD.receipts_for(
            self._record({"change_kind": "behaviour",
                          "red_check": {"command": "true",
                                       "exit_code": 1,
                                       "output_location": "/tmp/red.log",
                                       "revision": "afterrev"}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("pre_implementation was not True", receipts[0]["reason"])


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
        # E79: this sha is not a real git object anywhere, so harness_label
        # cannot resolve it against the public remote and labels it
        # private rather than guessing it is safe to look up; the
        # resolvable case is pinned separately below in HarnessLabel.
        record = {"outcome": "o", "work_id": "w", "rows": [self._row()],
                 "harness_revision": "abcdef0123456789fedcba"}
        receipts = RD.receipts_for(record, self._claims(), [])
        self.assertEqual(receipts[0]["harness_revision"],
                         "abcdef0123456789fedcba")
        sentence = RD.receipt_sentence(receipts[0])
        # The clause after the sha depends on the checkout's remotes (E101):
        # a hub checkout reads "private hub revision", a public clone with
        # no remote reads "public remote NO-DATA". Only the twelve-hex cut
        # is this test's claim.
        self.assertIn("harness abcdef012345 (", sentence)
        self.assertTrue("(private hub revision)." in sentence
                        or "(public remote NO-DATA)." in sentence, sentence)
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
        env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(decomposer))
        env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(model))
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


class E18EvidenceFileReadByTheEngine(unittest.TestCase):
    """P6 (doc E18/12.6): brother_run.py's own reader, _read_e18_evidence,
    the one place the evidence file this whole feature depends on is
    actually opened and parsed. Driven directly, apart from receipt_door's
    rendering below, so a defect in the file contract and a defect in the
    receipt's rendering can never hide behind each other."""

    def test_a_full_file_carries_all_five_fields_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "evidence"))
            with open(os.path.join(tmp, "evidence", "U1.json"), "w",
                     encoding="utf-8") as fh:
                json.dump({"metric": "auc", "value": 0.91, "baseline": 0.88,
                          "seed": 7, "holdout_id": "h-2026-08"}, fh)
            result = _br._read_e18_evidence(tmp, "U1")
        self.assertEqual(result, {"metric": "auc", "value": 0.91,
                                  "baseline": 0.88, "seed": 7,
                                  "holdout_id": "h-2026-08"})

    def test_a_missing_file_names_its_own_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _br._read_e18_evidence(tmp, "U1")
        self.assertIn("no metric recorded", result["missing_reason"])
        self.assertIn("U1.json", result["missing_reason"])

    def test_malformed_json_is_refused_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "evidence"))
            with open(os.path.join(tmp, "evidence", "U1.json"), "w",
                     encoding="utf-8") as fh:
                fh.write("{not json")
            result = _br._read_e18_evidence(tmp, "U1")
        self.assertIn("no metric recorded", result["missing_reason"])

    def test_a_json_array_instead_of_an_object_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "evidence"))
            with open(os.path.join(tmp, "evidence", "U1.json"), "w",
                     encoding="utf-8") as fh:
                json.dump([1, 2, 3], fh)
            result = _br._read_e18_evidence(tmp, "U1")
        self.assertIn("no metric recorded", result["missing_reason"])
        self.assertIn("not a JSON object", result["missing_reason"])

    def test_a_missing_field_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "evidence"))
            with open(os.path.join(tmp, "evidence", "U1.json"), "w",
                     encoding="utf-8") as fh:
                json.dump({"metric": "auc", "value": 0.91, "baseline": 0.88,
                          "seed": 7}, fh)
            result = _br._read_e18_evidence(tmp, "U1")
        self.assertIn("no metric recorded", result["missing_reason"])
        self.assertIn("holdout_id", result["missing_reason"])

    def test_an_empty_string_field_counts_as_missing_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "evidence"))
            with open(os.path.join(tmp, "evidence", "U1.json"), "w",
                     encoding="utf-8") as fh:
                json.dump({"metric": "", "value": 0.91, "baseline": 0.88,
                          "seed": 7, "holdout_id": "h-2026-08"}, fh)
            result = _br._read_e18_evidence(tmp, "U1")
        self.assertIn("metric", result["missing_reason"])


class StatisticalEvidenceOnAnE18Receipt(unittest.TestCase):
    """P6 (doc E18/12.6): a green check on a unit whose evidence_family is
    E18 does not by itself prove ITS OWN claim, a metric against a
    baseline, unless its check also wrote the evidence file
    _read_e18_evidence reads. Driven both ways at receipt_door's own
    layer, the same shape CheckDiscriminationRefusesACheckThatAlreadyPassed
    above already uses: the file present and holding every field renders
    verified with the number in the sentence, and the file missing (or a
    field within it missing) renders no-data 'no metric recorded',
    whatever the check's own exit code said."""

    def _record(self, extra_row_fields):
        row = {"id": "U1", "done_check": "true", "status": "DONE",
               "check_passed_before": False,
               "files_changed_by_unit": ["train.py"],
               "evidence_family": "E18"}
        row.update(extra_row_fields)
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_five_fields_present_renders_verified_with_the_metric_in_the_sentence(self):
        receipts = RD.receipts_for(
            self._record({"e18_evidence": {
                "metric": "auc", "value": 0.91, "baseline": 0.88,
                "seed": 7, "holdout_id": "h-2026-08"}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("auc 0.91 against baseline 0.88, seed 7, "
                      "holdout h-2026-08.", sentence)

    def test_the_evidence_file_missing_renders_no_data_no_metric_recorded(self):
        receipts = RD.receipts_for(
            self._record({"e18_evidence": {
                "missing_reason": "no metric recorded: no evidence file at "
                                  "/run/evidence/U1.json"}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no metric recorded", receipts[0]["reason"])
        self.assertIn("U1 is NO-DATA: no metric recorded",
                      RD.receipt_sentence(receipts[0]))

    def test_no_e18_evidence_stamped_on_the_row_at_all_is_the_same_refusal(self):
        """A row that never got an e18_evidence key at all: a harness older
        than P6, or a run whose check never wrote the file so
        _read_e18_evidence never had a dict to hand back in the first
        place."""
        receipts = RD.receipts_for(self._record({}), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no metric recorded", receipts[0]["reason"])

    def test_one_field_missing_from_an_otherwise_full_file_still_refuses(self):
        receipts = RD.receipts_for(
            self._record({"e18_evidence": {
                "metric": "auc", "value": 0.91, "baseline": 0.88,
                "seed": 7, "holdout_id": ""}}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no metric recorded", receipts[0]["reason"])
        self.assertIn("holdout_id", receipts[0]["reason"])

    def test_a_non_e18_unit_ignores_e18_evidence_entirely(self):
        """evidence_family E1 (or any family that is not E18) never even
        looks at e18_evidence; a row that happens to carry a stray key
        still verifies on the ordinary rule."""
        receipts = RD.receipts_for(
            self._record({"evidence_family": "E1", "e18_evidence": None}),
            self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertNotIn("against baseline", RD.receipt_sentence(receipts[0]))


class NumbersManifestEvidenceOnAnE8OrE2Receipt(unittest.TestCase):
    """P7 (persona plan section 2, the verify stage): a unit whose
    evidence_family is E8 or E2 names a numbers-manifest.json path in its
    P6 evidence file (<run_dir>/evidence/<unit_id>.json, key
    "numbers_manifest"); receipt_door imports products/brothersbe/tools/
    sbe_gate.py's own gate_numbers and prints its verdict as the unit's
    evidence. Driven both ways, the same shape
    StatisticalEvidenceOnAnE18Receipt above already uses for E18: the
    example manifest gates to a PASS naming "zero drift", and a manifest
    with a TODO snapshot_id gates to a FAIL, both in the gate's own words,
    never a copy of its logic."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(os.path.join(self.run_dir, "evidence"))
        self.prior_run_dir = os.environ.get(journal.RUN_DIR_ENV_VAR)
        os.environ[journal.RUN_DIR_ENV_VAR] = self.run_dir
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.prior_run_dir is None:
            os.environ.pop(journal.RUN_DIR_ENV_VAR, None)
        else:
            os.environ[journal.RUN_DIR_ENV_VAR] = self.prior_run_dir

    def _write_manifest(self, name, figures):
        manifest_dir = os.path.join(self.tmp, name)
        os.makedirs(manifest_dir)
        path = os.path.join(manifest_dir, "numbers-manifest.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"figures": figures}, fh)
        return path

    def _write_evidence_file(self, unit_id, manifest_path):
        with open(os.path.join(self.run_dir, "evidence", "%s.json" % unit_id),
                  "w", encoding="utf-8") as fh:
            json.dump({"numbers_manifest": manifest_path}, fh)

    def _record(self, family):
        row = {"id": "U1", "done_check": "true", "status": "DONE",
               "check_passed_before": False,
               "files_changed_by_unit": ["report.sql"],
               "evidence_family": family}
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}

    def test_the_teaching_manifest_gates_to_zero_drift_and_verifies(self):
        dest = os.path.join(self.tmp, "data-warehouse")
        os.makedirs(dest)
        dest_manifest = os.path.join(dest, "numbers-manifest.json")
        shutil.copy(EXAMPLE_MANIFEST, dest_manifest)
        self._write_evidence_file("U1", dest_manifest)
        receipts = RD.receipts_for(self._record("E8"), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("zero drift", sentence)

    def test_a_todo_snapshot_id_gates_to_no_data_in_the_gates_own_words(self):
        manifest = self._write_manifest("etl", [{
            "label": "row_count", "snapshot_id": "TODO",
            "query": "select count(*) from t",
            "second_derivation": "select sum(1) from t",
            "rerun": {"ran": True, "primary": 1, "secondary": 1}}])
        self._write_evidence_file("U1", manifest)
        receipts = RD.receipts_for(self._record("E2"), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no snapshot_id recorded", receipts[0]["reason"])
        self.assertIn("FAIL", receipts[0]["reason"])
        self.assertIn("no snapshot_id recorded",
                      RD.receipt_sentence(receipts[0]))

    def test_no_numbers_manifest_key_in_the_evidence_file_is_no_data(self):
        with open(os.path.join(self.run_dir, "evidence", "U1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({}, fh)
        receipts = RD.receipts_for(self._record("E8"), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no metric recorded", receipts[0]["reason"])

    def test_no_evidence_file_at_all_is_no_data(self):
        receipts = RD.receipts_for(self._record("E2"), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("no evidence file at", receipts[0]["reason"])

    def test_a_non_e8_e2_unit_ignores_numbers_manifest_entirely(self):
        """evidence_family E1 (or any family outside E8/E2) never even
        looks for a numbers_manifest key; a row with none still verifies on
        the ordinary rule, and the sentence never mentions the gate."""
        receipts = RD.receipts_for(self._record("E1"), self._claims(), [])
        self.assertEqual(receipts[0]["state"], "verified")
        self.assertNotIn("Numbers manifest", RD.receipt_sentence(receipts[0]))

    def test_an_absent_product_reports_no_data_naming_the_import(self):
        """sbe_gate.py unreachable (a moved or absent product) is a fact
        this receipt reports, never a crash: _sbe_gate_numbers is pointed
        at a tools/ directory holding no sbe_gate.py at all."""
        manifest = self._write_manifest("etl", [{
            "label": "x", "snapshot_id": "s1", "query": "select 1",
            "second_derivation": "select sum(1)",
            "rerun": {"ran": True, "primary": 1, "secondary": 1}}])
        self._write_evidence_file("U1", manifest)
        real_tools = RD._SBE_TOOLS
        real_path = list(sys.path)
        RD._SBE_TOOLS = os.path.join(self.tmp, "no-such-tools")
        sys.path = [p for p in sys.path if p != real_tools]
        sys.modules.pop("sbe_gate", None)
        try:
            receipts = RD.receipts_for(self._record("E8"), self._claims(), [])
        finally:
            RD._SBE_TOOLS = real_tools
            sys.path = real_path
        self.assertEqual(receipts[0]["state"], "no-data")
        self.assertIn("sbe_gate.py could not be imported",
                      receipts[0]["reason"])


class HarnessLabelNamesTheRemoteThatCanResolveTheSha(unittest.TestCase):
    """E79: the toy delivery's own harness_revision did not resolve in the
    public clone, and the receipt said nothing about that. harness_label
    is driven against a throwaway repo it fully controls, never the real
    origin remote, so the test is deterministic and offline."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def test_a_sha_that_is_an_ancestor_of_the_public_ref_prints_bare(self):
        sha = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        label = RD.harness_label(sha, repo=self.repo, public_ref="HEAD")
        self.assertEqual(label, "harness %s" % sha[:12])
        self.assertNotIn("private hub revision", label)

    def test_a_sha_not_reachable_from_the_public_ref_is_labeled_private(self):
        # A syntactically valid sha this repo never committed.
        fake = "1234567890abcdef1234567890abcdef12345678"
        label = RD.harness_label(fake, repo=self.repo, public_ref="HEAD")
        self.assertEqual(label,
                         "harness 1234567890ab (private hub revision)")

    def test_a_repo_that_is_not_a_git_checkout_at_all_is_labeled_private(self):
        not_a_repo = os.path.join(self.tmp, "not-a-repo")
        os.makedirs(not_a_repo)
        label = RD.harness_label("deadbeefcafe1234", repo=not_a_repo,
                                 public_ref="HEAD")
        self.assertEqual(label, "harness deadbeefcafe (private hub revision)")

    def test_no_revision_recorded_prints_plain_no_data(self):
        self.assertEqual(RD.harness_label(""), "harness NO-DATA")
        self.assertEqual(RD.harness_label(None), "harness NO-DATA")
        self.assertEqual(RD.harness_label(RD.NODATA), "harness NO-DATA")


def make_bare(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sh(["git", "init", "-q", "--bare", "-b", "main", path])
    return path


class ThePublicRemoteIsFoundByUrlNotByTheNameOrigin(unittest.TestCase):
    """E101: the public ref was the literal string "origin/main", but origin
    is the PRIVATE hub in ~/brother-hub and the PUBLIC repository in the
    dual-remote lane checkouts, so one commit read as publicly resolvable in
    one tree and private in the other (measured 2026-09-04: the README
    honesty gate was green on the hub checkout and red in every lane
    worktree). Both shapes, and the reverse naming that states the trap
    outright, are built here as real repositories on disk and driven
    offline: the remote URLs decide, never the names."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.public = make_bare(os.path.join(self.tmp, "khalilmaaouni",
                                             "Brother.git"))
        self.hub = make_bare(os.path.join(self.tmp, "khalilmaaouni",
                                          "brother-hub.git"))
        source = make_repo(self.tmp)
        # One commit the public repository carries, and one only the hub
        # ever sees: the two cases the label distinguishes.
        self.public_sha = sh(["git", "rev-parse", "HEAD"],
                             cwd=source).stdout.strip()
        sh(["git", "push", "-q", self.public, "main"], cwd=source)
        with open(os.path.join(source, "hub-only.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("hub\n")
        sh(["git", "add", "-A"], cwd=source)
        sh(["git", "commit", "-q", "-m", "hub only"], cwd=source)
        self.hub_sha = sh(["git", "rev-parse", "HEAD"],
                          cwd=source).stdout.strip()
        sh(["git", "push", "-q", self.hub, "main"], cwd=source)

    def _checkout(self, name, remotes):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        sh(["git", "init", "-q", "-b", "main"], cwd=path)
        for remote, url in remotes:
            sh(["git", "remote", "add", remote, url], cwd=path)
            self.assertEqual(sh(["git", "fetch", "-q", remote],
                                cwd=path).returncode, 0, remote)
        return path

    def test_every_checkout_shape_labels_one_commit_the_same_way(self):
        shapes = {
            # The lane worktrees: origin IS the public repository.
            "lane": [("origin", self.public), ("hub", self.hub)],
            # ~/brother-hub: origin is the private hub.
            "hub-tree": [("origin", self.hub), ("public", self.public)],
            # The reverse naming, the trap stated outright: the remote
            # called "hub" is the public repository.
            "reversed": [("origin", self.hub), ("hub", self.public)],
        }
        for name, remotes in shapes.items():
            repo = self._checkout(name, remotes)
            self.assertEqual(RD.harness_label(self.public_sha, repo=repo),
                             "harness %s" % self.public_sha[:12], name)
            self.assertEqual(RD.harness_label(self.hub_sha, repo=repo),
                             "harness %s (private hub revision)"
                             % self.hub_sha[:12], name)

    def test_the_ref_name_comes_from_the_remote_whose_url_matched(self):
        repo = self._checkout("named", [("origin", self.hub),
                                        ("upstream", self.public)])
        self.assertEqual(RD.public_remote_ref(repo=repo), "upstream/main")

    def test_no_public_remote_reads_no_data_and_never_private(self):
        repo = self._checkout("only-hub", [("origin", self.hub)])
        self.assertEqual(RD.public_remote_ref(repo=repo), RD.NODATA)
        label = RD.harness_label(self.hub_sha, repo=repo)
        self.assertEqual(label, "harness %s (public remote NO-DATA)"
                         % self.hub_sha[:12])
        self.assertNotIn("private hub revision", label)

    def test_a_directory_that_is_not_a_checkout_reads_no_data(self):
        not_a_repo = os.path.join(self.tmp, "not-a-repo")
        os.makedirs(not_a_repo)
        self.assertEqual(RD.public_remote_ref(repo=not_a_repo), RD.NODATA)

    def test_the_url_forms_of_the_same_repository_all_match(self):
        matches = ("git@github.com:khalilmaaouni/Brother.git",
                   "https://github.com/khalilmaaouni/Brother",
                   "https://github.com/khalilmaaouni/Brother.git/",
                   "/somewhere/local/khalilmaaouni/Brother.git")
        misses = ("https://github.com/khalilmaaouni/brother-hub.git",
                  "https://github.com/someone-else/Brother.git",
                  "https://github.com/khalilmaaouni/BrotherModeUp.git")
        for i, url in enumerate(matches):
            repo = self._checkout("m%d" % i, [])
            sh(["git", "remote", "add", "x", url], cwd=repo)
            self.assertEqual(RD.public_remote_ref(repo=repo), "x/main", url)
        for i, url in enumerate(misses):
            repo = self._checkout("n%d" % i, [])
            sh(["git", "remote", "add", "x", url], cwd=repo)
            self.assertEqual(RD.public_remote_ref(repo=repo), RD.NODATA, url)

    def test_an_explicit_ref_still_overrides_the_url_search(self):
        repo = self._checkout("override", [("origin", self.public)])
        self.assertEqual(
            RD.harness_label(self.public_sha, repo=repo,
                             public_ref="refs/does/not/exist"),
            "harness %s (private hub revision)" % self.public_sha[:12])


class PerFileChecksNameTheCommandBehindEachChangedFile(unittest.TestCase):
    """E79: the README named two changed files and two PASS units and
    printed no check command; per_file_checks is the function that turns a
    run's own receipts into exactly the table a skeptic asked for, one row
    per changed file, never per unit."""

    def _record(self):
        return {"outcome": "o", "work_id": "w", "rows": [
            {"id": "guard", "done_check": "true", "status": "DONE",
             "check_passed_before": False,
             "files_changed_by_unit": ["mathlib.py"]},
            {"id": "test", "done_check": "true", "status": "DONE",
             "check_passed_before": True,
             "files_changed_by_unit": ["test_mathlib.py"]},
        ]}

    def _claims(self):
        return {
            "guard": {"state": "done", "evidence": {
                "check_command": "python3 -c \"assert True\"",
                "exit_code": 0, "output": "", "canonical_rev": "abc"}},
            "test": {"state": "done", "evidence": {
                "check_command": "python3 -m pytest -q",
                "exit_code": 0, "output": "", "canonical_rev": "abc"}},
        }

    def test_one_entry_per_changed_file_carrying_the_command_and_exit(self):
        record = self._record()
        receipts = RD.receipts_for(record, self._claims(), [],
                                   log_path="/tmp/run.log")
        checks = RD.per_file_checks(record, receipts)
        by_file = {c["file"]: c for c in checks}
        self.assertEqual(set(by_file), {"mathlib.py", "test_mathlib.py"})
        self.assertEqual(by_file["mathlib.py"]["check_command"],
                         "python3 -c \"assert True\"")
        self.assertEqual(by_file["mathlib.py"]["exit_code"], 0)
        self.assertEqual(by_file["mathlib.py"]["output_location"],
                         "/tmp/run.log")
        # The before-and-after discrimination: guard's check was measured
        # to fail before the work and pass after (proves the work); test's
        # was measured to already pass before it (proves nothing).
        self.assertTrue(by_file["mathlib.py"]["check_passed_before"] is
                        False)
        self.assertEqual(by_file["mathlib.py"]["state"], "verified")
        self.assertTrue(by_file["test_mathlib.py"]["check_passed_before"])
        self.assertEqual(by_file["test_mathlib.py"]["state"], "no-data")
        self.assertIn("already passed before the work",
                      by_file["test_mathlib.py"]["reason"])

    def test_a_unit_with_no_recorded_files_contributes_no_entry(self):
        record = {"outcome": "o", "work_id": "w",
                  "rows": [{"id": "U1", "done_check": "true",
                            "status": "DONE"}]}
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": ""}}}
        receipts = RD.receipts_for(record, claims, [])
        self.assertEqual(RD.per_file_checks(record, receipts), [])


class RequirePerFileChecksRefusesAnEmptyOrMalformedList(unittest.TestCase):
    """The row's own done_check, verbatim: 'scripts/test_receipt_door.py
    refuses a record with no per-file check'. Driven both ways."""

    def test_an_empty_list_is_refused_by_name(self):
        ok, reason = RD.require_per_file_checks([])
        self.assertFalse(ok)
        self.assertIn("no per-file checks", reason)

    def test_none_is_refused_by_name(self):
        ok, reason = RD.require_per_file_checks(None)
        self.assertFalse(ok)
        self.assertIn("no per-file checks", reason)

    def test_an_entry_missing_check_command_is_refused_naming_the_field(self):
        ok, reason = RD.require_per_file_checks(
            [{"file": "mathlib.py"}])
        self.assertFalse(ok)
        self.assertIn("check_command", reason)
        self.assertIn("mathlib.py", reason)

    def test_a_well_formed_list_is_accepted(self):
        ok, reason = RD.require_per_file_checks([
            {"file": "mathlib.py", "check_command": "python3 -c 1",
             "exit_code": 0, "output_location": "/tmp/run.log"}])
        self.assertTrue(ok)
        self.assertEqual(reason, "")


class ADeliveryRecordWithNoPerFileChecksIsRefused(unittest.TestCase):
    """Wires require_per_file_checks into accept_delivery.record() (E79):
    a delivery record that claims per-file evidence and carries none is
    refused before it is written, and one that carries real evidence is
    accepted and stored verbatim, so a stranger reading docs/deliveries/
    finds the check that decided every changed file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _checks(self):
        return [
            {"file": "mathlib.py", "unit": "guard",
             "check_command": "python3 -c \"assert True\"",
             "exit_code": 0, "output_location": "/tmp/run.log",
             "check_passed_before": False, "state": "verified"},
            {"file": "test_mathlib.py", "unit": "test",
             "check_command": "python3 -m pytest -q",
             "exit_code": 0, "output_location": "/tmp/run.log",
             "check_passed_before": True, "state": "no-data",
             "reason": "the check already passed before the work began"},
        ]

    def test_a_record_with_an_empty_checks_list_is_refused_by_name(self):
        ok, reason = _ad.record(
            "toy delivery", "sha-e79-a", "Khalil Maaouni",
            "2026-09-04T02:00:00+09:00", "person", directory=self.tmp,
            checks=[])
        self.assertFalse(ok)
        self.assertIn("no per-file checks", reason)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_a_record_with_real_per_file_checks_is_accepted_and_stored(self):
        ok, path = _ad.record(
            "toy delivery", "sha-e79-b", "Khalil Maaouni",
            "2026-09-04T02:00:00+09:00", "person", directory=self.tmp,
            checks=self._checks())
        self.assertTrue(ok)
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual(len(stored["checks"]), 2)
        files = {c["file"] for c in stored["checks"]}
        self.assertEqual(files, {"mathlib.py", "test_mathlib.py"})
        self.assertEqual(stored["checks"][0]["check_command"],
                         self._checks()[0]["check_command"])

    def test_omitting_checks_entirely_still_records_a_plain_acceptance(self):
        # Backward compatibility: a human accepting a bare PR reference
        # (this tool's original shape) is unaffected by E79, the acceptance
        # is still written. E94 changed what the FIELD says: an omitted
        # "checks" made a record that proves nothing the same shape on disk
        # as one that never claimed to, so it now reads NO-DATA with the
        # reason rather than being absent.
        ok, path = _ad.record(
            "a PR with no run behind it", "khalilmaaouni/Brother#1",
            "Khalil Maaouni", "2026-09-04T02:00:00+09:00", "person",
            directory=self.tmp)
        self.assertTrue(ok)
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual(stored["checks"], "NO-DATA")
        self.assertIn("--run-dir", stored["checks_reason"])


class ReceiptIdentityFieldsRendered(unittest.TestCase):
    """P9 (persona integration plan 2026-09-04, row P9; doc 12.6 code,
    environment and model identity; doc F14 reproducibility failure):
    receipts_for stamps target_revision, env_lock and data_identity (each
    computed by build_report, never re-derived here) onto every receipt, and
    receipt_sentence prints all three on the one line a person reads."""

    def _rows(self):
        return [{"id": "U1", "done_check": "true"}]

    def test_receipts_for_defaults_all_three_to_no_data(self):
        receipts = RD.receipts_for({"rows": self._rows()}, {}, [])
        self.assertEqual(receipts[0]["target_revision"], "NO-DATA")
        self.assertEqual(receipts[0]["env_lock"], "NO-DATA")
        self.assertEqual(receipts[0]["data_identity"], "NO-DATA")

    def test_receipts_for_stamps_the_three_given_values(self):
        receipts = RD.receipts_for(
            {"rows": self._rows()}, {}, [], target_revision="deadbeef",
            env_lock="a" * 64, data_identity_by_id={"U1": {"train.csv": "b" * 64}})
        self.assertEqual(receipts[0]["target_revision"], "deadbeef")
        self.assertEqual(receipts[0]["env_lock"], "a" * 64)
        self.assertEqual(receipts[0]["data_identity"],
                         {"train.csv": "b" * 64})

    def test_a_unit_missing_from_data_identity_by_id_reads_no_data(self):
        receipts = RD.receipts_for(
            {"rows": self._rows()}, {}, [], target_revision="deadbeef",
            env_lock="a" * 64, data_identity_by_id={})
        self.assertEqual(receipts[0]["data_identity"], "NO-DATA")

    def test_receipt_sentence_renders_all_three_fields(self):
        receipts = RD.receipts_for(
            {"rows": self._rows()}, {}, [], target_revision="deadbeef",
            env_lock="a" * 64, data_identity_by_id={"U1": {"train.csv": "b" * 64}})
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("Target revision deadbeef,", sentence)
        self.assertIn("environment lock %s," % ("a" * 64), sentence)
        self.assertIn("data identity train.csv=%s." % ("b" * 64), sentence)

    def test_receipt_sentence_renders_no_data_defaults(self):
        receipts = RD.receipts_for({"rows": self._rows()}, {}, [])
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("Target revision NO-DATA, environment lock NO-DATA, "
                      "data identity NO-DATA.", sentence)

    def test_a_no_data_data_identity_reason_renders_verbatim(self):
        receipts = RD.receipts_for(
            {"rows": self._rows()}, {}, [],
            data_identity_by_id={
                "U1": "NO-DATA: this unit declares no data_inputs"})
        sentence = RD.receipt_sentence(receipts[0])
        self.assertIn("data identity NO-DATA: this unit declares no "
                      "data_inputs.", sentence)


class AppliedMemoryPrintsTheTypeBesideTheSlug(unittest.TestCase):
    """P11 (persona plan 2026-09-04, row P11): a recalled note's type: field
    (data_semantic, test_oracle, or whatever the vault carries) rides along
    with its slug in every section of the applied-memory partition, exactly
    as vault_recall_hook.py's lesson_states() records it -- this function
    only reports what the hook already decided, never recomputes it."""

    def test_an_applied_data_semantic_note_carries_its_type(self):
        section = RD.applied_memory([
            {"slug": "churn-definition-2026-q2", "path": "x.md",
             "state": "applied", "line": None, "note_type": "data_semantic"},
        ])
        self.assertEqual(section["applied"],
                         [{"slug": "churn-definition-2026-q2",
                           "type": "data_semantic"}])

    def test_an_unverified_test_oracle_note_carries_its_type_and_reason(self):
        reason = ("recall: UNVERIFIED unapproved-oracle: human_approved "
                  "false: a drafted lesson nobody has approved does not "
                  "override current evidence")
        section = RD.applied_memory([
            {"slug": "unapproved-oracle", "path": "y.md", "state": "unverified",
             "line": reason, "note_type": "test_oracle"},
        ])
        self.assertEqual(section["unverified"],
                         [{"slug": "unapproved-oracle", "type": "test_oracle",
                           "line": reason}])

    def test_a_note_with_no_declared_type_carries_no_type_key(self):
        # Backward compatible: an ordinary lesson (the vault's existing
        # shape) never gets an invented type key.
        section = RD.applied_memory([
            {"slug": "plain-lesson", "path": "z.md", "state": "applied",
             "line": None, "note_type": None},
        ])
        self.assertEqual(section["applied"], [{"slug": "plain-lesson"}])


class AppliedMemoryCarriesTheMutationSeamMarker(unittest.TestCase):
    """Row P0-M (security finding, 2026-09-06): a rec carrying a
    "mutation" key (vault_recall_hook.py's lesson_states() attaches one to
    every record while a BM_VAULT_DISABLE_* seam is active) is never
    turned into a fourth MEMORY_STATES value; it stays exactly where its
    own state already put it, with a "mutation" field added, and the
    section as a whole gains a `.mutation` ATTRIBUTE (never a same-shaped
    dict key, night run 2026-09-07: a "mutation" string sibling of
    otherwise list-valued dict keys is exactly the shape that crashed
    scripts/brother_run.py's own blind `.values()` walk) reading
    "MUTATION SEAM ACTIVE (...)" so a reader of the section never has to
    scan every entry for the field."""

    def test_no_mutation_key_with_no_seam(self):
        section = RD.applied_memory([
            {"slug": "plain-lesson", "path": "z.md", "state": "applied",
             "line": None, "note_type": None},
        ])
        self.assertNotIn("mutation", section)
        self.assertIsNone(section.mutation)
        self.assertNotIn("mutation", section["applied"][0])

    def test_an_entry_carrying_mutation_keeps_its_own_state_and_gains_the_field(self):
        section = RD.applied_memory([
            {"slug": "seam-hit", "path": "y.md", "state": "unverified",
             "line": "recall: UNVERIFIED", "note_type": None,
             "mutation": {"disabled": ["BM_VAULT_DISABLE_ANCHOR_CHECK"]}},
        ])
        self.assertEqual(section["unverified"],
                         [{"slug": "seam-hit", "line": "recall: UNVERIFIED",
                           "mutation": {"disabled": ["BM_VAULT_DISABLE_ANCHOR_CHECK"]}}])
        self.assertEqual(section["applied"], [])
        self.assertEqual(section["stale"], [])

    def test_the_section_gains_a_top_level_banner_naming_every_disabled_seam(self):
        section = RD.applied_memory([
            {"slug": "a", "path": "a.md", "state": "unverified", "line": None,
             "note_type": None,
             "mutation": {"disabled": ["BM_VAULT_DISABLE_ANCHOR_CHECK"]}},
            {"slug": "b", "path": "b.md", "state": "stale", "line": None,
             "note_type": None,
             "mutation": {"disabled": ["BM_VAULT_DISABLE_LIFECYCLE_GATE"]}},
        ])
        self.assertEqual(
            section.mutation,
            "MUTATION SEAM ACTIVE (vault protections disabled: "
            "BM_VAULT_DISABLE_ANCHOR_CHECK, BM_VAULT_DISABLE_LIFECYCLE_GATE)")


class AppliedMemoryPartitionsNoDataSeparatelyFromApplied(unittest.TestCase):
    """S4 (2026-09-08 VN1 fix): "no-data" is MEMORY_STATES' fifth value, the
    same way "policy-conflict" was added as the fourth -- a lesson
    vault_recall_hook.py tombstoned outright (its own revalidator crashed
    or a contract module was unavailable) lands in its OWN bucket, never
    silently counted as applied and never dropped as an unrecognized
    state."""

    def test_a_no_data_entry_lands_in_its_own_bucket_never_applied(self):
        section = RD.applied_memory([
            {"slug": "seam-unavailable", "path": "z.md", "state": "no-data",
             "line": "recall: NO-DATA seam-unavailable: seam module "
                     "unavailable, mutation state unknown", "note_type": None},
        ])
        self.assertEqual(
            section["no-data"],
            [{"slug": "seam-unavailable",
              "line": "recall: NO-DATA seam-unavailable: seam module "
                      "unavailable, mutation state unknown"}])
        self.assertEqual(section["applied"], [])

    def test_no_data_is_a_named_memory_state(self):
        self.assertIn("no-data", RD.MEMORY_STATES)

    def test_a_no_data_entry_alongside_an_applied_one_keeps_both_separate(self):
        section = RD.applied_memory([
            {"slug": "plain-lesson", "path": "a.md", "state": "applied",
             "line": None, "note_type": None},
            {"slug": "tombstoned", "path": "b.md", "state": "no-data",
             "line": "recall: NO-DATA tombstoned: revalidation unavailable",
             "note_type": None},
        ])
        self.assertEqual(section["applied"], [{"slug": "plain-lesson"}])
        self.assertEqual(
            section["no-data"],
            [{"slug": "tombstoned",
              "line": "recall: NO-DATA tombstoned: revalidation unavailable"}])


def _seed_two_unit_run(case, run_dir):
    """One unit repaired once then delivered (A), one zero-change unit
    (B, files_changed_by_unit stays empty): claim_store.acquire/release
    writes the real claim.acquired/claim.released journal events, and an
    attempt.traced event is appended by hand for the second attempt,
    exactly the shape brother_run.py's own _write_attempt_trace writes."""
    claims_path = os.path.join(run_dir, "claims.json")
    claim, problem = claim_store.acquire(claims_path, "A", "workerA")
    case.assertTrue(claim, problem)
    claim_store.release(claims_path, "A", "workerA", state="active",
                        evidence={})
    claim, problem = claim_store.acquire(claims_path, "A", "workerA")
    case.assertTrue(claim, problem)
    journal.append(run_dir, "attempt.traced",
                   parent_ids=journal.previous(run_dir), unit_id="A",
                   payload={"attempt": 2, "claim_state": "active"})
    claim_store.release(claims_path, "A", "workerA", state="done",
                        evidence={"exit_code": 0})
    claim, problem = claim_store.acquire(claims_path, "B", "workerB")
    case.assertTrue(claim, problem)
    claim_store.release(claims_path, "B", "workerB", state="done",
                        evidence={"exit_code": 0})
    record = {"outcome": "ship the thing", "work_id": "w1", "rows": [
        {"id": "A", "objective": "fix the bug", "done_check": "true",
         "owns": ["mathlib.py"], "status": "DONE",
         "check_passed_before": False,
         "files_changed_by_unit": ["mathlib.py"]},
        {"id": "B", "objective": "no-op unit", "done_check": "true",
         "owns": ["other.py"], "status": "DONE",
         "check_passed_before": False,
         "files_changed_by_unit": []},
    ]}
    with open(os.path.join(run_dir, "W-w1.json"), "w",
             encoding="utf-8") as fh:
        json.dump(record, fh)
    return record


def _receipts_for(run_dir, record):
    events = journal.read(run_dir)
    claims = journal_projection.claims_from_journal(events)
    return RD.receipts_for(record, claims, [], log_path="/tmp/run.log")


class TheReceiptRecordAnswersTheEightAcceptanceQuestions(unittest.TestCase):
    """E72.1 (docs/plan/STEERING-TRUSTED-DELEGATION-2026-09-03.md section
    16, WORKSTREAM A): receipt_record() built from a real journal, a real
    claim store and a real Work document, never hand-typed claim dicts,
    the same seam test_continuity.py already uses for the same reason."""

    def _seed(self, run_dir):
        return _seed_two_unit_run(self, run_dir)

    def _receipts(self, run_dir, record):
        return _receipts_for(run_dir, record)

    def test_every_acceptance_question_has_its_own_field(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            rec = RD.receipt_record(run_dir, receipts)
            # The doc's own eight questions (lines 1536-1543), one field
            # per question, per this function's own docstring table.
            for field in ("scope", "intent", "evidence", "unproven",
                         "repair_history", "attention", "containment",
                         "continuity"):
                self.assertIn(field, rec)

    def test_a_zero_change_unit_reads_no_data_never_a_mark(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            rec = RD.receipt_record(run_dir, receipts)
            unproven_by_id = {e["id"]: e for e in rec["unproven"]}
            self.assertIn("B", unproven_by_id)
            self.assertIsNone(unproven_by_id["B"]["mark"])
            self.assertEqual(unproven_by_id["B"]["state"], "no-data")
            self.assertIn("no file changed", unproven_by_id["B"]["reason"])
            # And the delivered unit is proof, not absence of it.
            verified_ids = {e["id"] for e in rec["evidence"]}
            self.assertEqual(verified_ids, {"A"})
            self.assertEqual(rec["evidence"][0]["mark"], 10.0)

    def test_repair_history_names_the_unit_that_needed_a_second_attempt(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            rec = RD.receipt_record(run_dir, receipts)
            repaired = {e["unit"]: e for e in rec["repair_history"]}
            self.assertIn("A", repaired)
            self.assertEqual(repaired["A"]["attempts"], 2)
            self.assertNotIn("B", repaired)

    def test_containment_reads_clean_when_every_change_is_declared(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            rec = RD.receipt_record(run_dir, receipts)
            self.assertTrue(rec["containment"]["contained"])
            self.assertEqual(rec["containment"]["boundary_crossings"], [])

    def test_a_file_outside_the_declared_scope_is_a_boundary_crossing(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            record["rows"][0]["files_changed_by_unit"] = [
                "mathlib.py", "unrelated/secret.py"]
            with open(os.path.join(run_dir, "W-w1.json"), "w",
                     encoding="utf-8") as fh:
                json.dump(record, fh)
            receipts = self._receipts(run_dir, record)
            rec = RD.receipt_record(run_dir, receipts)
            crossed = {c["file"] for c in
                      rec["containment"]["boundary_crossings"]}
            self.assertIn("unrelated/secret.py", crossed)
            self.assertFalse(rec["containment"]["contained"])

    def test_continuity_folds_the_real_capsule(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            rec = RD.receipt_record(run_dir, receipts)
            self.assertEqual(rec["continuity"]["problem"], "")
            self.assertEqual(rec["continuity"]["capsule"]["buckets"]
                             ["integrated"], ["A", "B"])

    def test_a_record_given_directly_with_no_run_dir_still_answers(self):
        record = {"outcome": "o", "work_id": "w", "rows": [
            {"id": "U1", "objective": "x", "done_check": "true",
             "status": "DONE", "check_passed_before": False,
             "files_changed_by_unit": ["f.py"], "owns": ["f.py"]}]}
        claims = {"U1": {"state": "done",
                         "evidence": {"exit_code": 0, "output": ""}}}
        receipts = RD.receipts_for(record, claims, [])
        rec = RD.receipt_record(record, receipts)
        self.assertEqual(rec["evidence"][0]["id"], "U1")
        self.assertIn("NO-DATA", rec["continuity"]["problem"])
        self.assertIsNone(rec["continuity"]["capsule"])


class TheReceiptSaysWhetherTheRevertRanAndWhichEngineRanIt(unittest.TestCase):
    """E115, the two questions the frozen contract (docs/plan/
    DELIVERY-RECEIPT-V1.md) found the receipt could not answer.

    QUESTION 6. dependency_note() built the sentence and receipts_for()
    stamped it, and receipt_record() never copied it onto the record, so the
    only trace a dependency revert left on the FILE was one sided: a revert
    that passed, and therefore disproved the check, wrote a refusal into
    unproven[].reason, while a revert that RAN AND CORRECTLY FAILED wrote
    nothing at all and read exactly like a revert nobody ever made. The
    first case below is that indistinguishability, driven directly: two
    verified units, one with a failed revert behind it and one with no
    dependency at all, which must not read the same.

    QUESTION 10. harness_version and harness_revision were measured for the
    cost block and reached the file only as prose inside `report`.

    Every case here reads the RECORD receipt_record() returns, never the
    in-memory receipt, because the in-memory receipt already carried the
    dependency note before this row and carrying it there was never the
    gap."""

    def _record(self, extra_row_fields=None):
        row = {"id": "test", "objective": "cover the guard",
               "done_check": "python3 -m pytest -k type", "status": "DONE",
               "check_passed_before": False, "owns": ["test_mathlib.py"],
               "files_changed_by_unit": ["test_mathlib.py"]}
        row.update(extra_row_fields or {})
        return {"outcome": "o", "work_id": "w", "rows": [row]}

    def _claims(self):
        return {"test": {"state": "done", "evidence": {
            "check_command": "python3 -m pytest -k type", "exit_code": 0,
            "output": "", "canonical_rev": "abc"}}}

    def _stamp(self, exit_code):
        return [{"unit": "guard", "files": ["mathlib.py"],
                 "revision": "abc", "exit_code": exit_code, "note": ""}]

    def _entry(self, record, refused=()):
        receipts = RD.receipts_for(record, self._claims(), list(refused))
        rec = RD.receipt_record(record, receipts)
        entries = rec["evidence"] + rec["unproven"]
        self.assertEqual(1, len(entries), entries)
        return rec, entries[0]

    def test_a_revert_that_ran_and_failed_is_not_a_revert_that_never_ran(self):
        """THE GAP ITSELF. Both units below are verified, both write one
        file, both carry no reason, and before this row their record entries
        agreed on every field. One had its dependency's change reverted and
        went red, which is what coverage looks like; the other never declared
        a dependency at all. A receipt that cannot separate those two is not
        answering question 6, it is silent about it."""
        _rec_a, ran = self._entry(self._record(
            {"depends_on": ["guard"], RD.CHECK_WITHOUT_FIELD: self._stamp(1)}))
        _rec_b, never = self._entry(self._record())
        self.assertEqual("verified", ran["state"])
        self.assertEqual("verified", never["state"])
        self.assertNotEqual(ran["dependency_check"],
                            never["dependency_check"])
        self.assertEqual("re-run with guard's files reverted: exit 1",
                         ran["dependency_check"])
        self.assertEqual("no dependency declared: this check proves its own "
                         "change only", never["dependency_check"])

    def test_a_declared_dependency_never_re_run_says_so_on_the_record(self):
        """The third state, and the one a NO-DATA rule exists for: a
        dependency was declared and the revert was never made. Absent from
        the stamp is not absent from the receipt."""
        _rec, entry = self._entry(self._record({"depends_on": ["guard"]}))
        self.assertEqual("declared dependency on guard, never re-run with it "
                         "reverted", entry["dependency_check"])

    def test_the_field_is_the_notes_own_words_never_a_second_judgement(self):
        """receipt_record() copies dependency_note()'s sentence and forms no
        opinion of its own. Asserted against the function itself, so the two
        cannot drift into disagreeing about the same row."""
        record = self._record({"depends_on": ["guard"],
                               RD.CHECK_WITHOUT_FIELD: self._stamp(1)})
        _rec, entry = self._entry(record)
        self.assertEqual(RD.dependency_note(record["rows"][0]),
                         entry["dependency_check"])

    def test_an_unproven_entry_carries_it_too(self):
        """The unproven half. A revert that PASSED disproves the check, so
        this unit is no-data and its reason already said so; the field has
        to be there as well, or a reader looking for one answer finds it on
        some entries and not others."""
        record = self._record({"depends_on": ["guard"],
                               RD.CHECK_WITHOUT_FIELD: self._stamp(0)})
        rec, entry = self._entry(record)
        self.assertEqual([], rec["evidence"])
        self.assertEqual("no-data", entry["state"])
        self.assertEqual("re-run with guard's files reverted: exit 0",
                         entry["dependency_check"])

    def test_a_refused_entry_carries_it_too(self):
        """And the refused half, whose receipt never reaches the dependency
        rules at all: the field is stamped by receipts_for before any state
        is decided, so a refusal does not swallow it."""
        record = self._record({"depends_on": ["guard"],
                               RD.CHECK_WITHOUT_FIELD: self._stamp(1)})
        rec, entry = self._entry(record, refused=[("test", "worker died")])
        self.assertEqual("refused", entry["state"])
        self.assertEqual("worker died", entry["reason"])
        self.assertEqual("re-run with guard's files reverted: exit 1",
                         entry["dependency_check"])

    def test_a_receipt_built_from_older_receipts_reads_no_data(self):
        """The boundary: a receipt dict from before this field existed. The
        key is still written, carrying NO-DATA, because an absent key is the
        exact failure this row closed and a fabricated sentence would be
        worse than both."""
        record = self._record()
        receipts = RD.receipts_for(record, self._claims(), [])
        receipts[0].pop("dependency_note")
        rec = RD.receipt_record(record, receipts)
        entry = rec["evidence"][0]
        self.assertIn("dependency_check", entry)
        self.assertEqual(RD.NODATA, entry["dependency_check"])

    def test_the_record_names_the_engine_that_ran_it(self):
        """Question 10. Both facts at the top level, and both equal to
        brother_run's own two measurements, which are the same calls
        build_cost_block is handed, so the field and the report prose the
        receipt also carries cannot disagree."""
        _rec, _entry = self._entry(self._record())
        rec = _rec
        self.assertIn("harness_version", rec)
        self.assertIn("harness_revision", rec)
        self.assertEqual(_br._harness_version(), rec["harness_version"])
        self.assertEqual(_br._harness_revision(), rec["harness_revision"])

    def test_the_engine_identity_is_a_real_string_or_says_why_not(self):
        """Never empty, never None, never a fabricated sha: either a real
        value or a NO-DATA sentence naming what could not be read."""
        _rec, _entry = self._entry(self._record())
        for field in ("harness_version", "harness_revision"):
            value = _rec[field]
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip(), "%s is empty" % field)

    def test_the_identity_survives_an_engine_that_cannot_be_measured(self):
        """Driven backwards: with both measurements refusing, the record
        still carries both keys and says why, rather than dropping them or
        inventing a value."""
        record = self._record()
        receipts = RD.receipts_for(record, self._claims(), [])
        gap = "%s: not a git checkout" % RD.NODATA
        with mock.patch.object(_br, "_harness_version", return_value=gap), \
                mock.patch.object(_br, "_harness_revision", return_value=gap):
            rec = RD.receipt_record(record, receipts)
        self.assertEqual(gap, rec["harness_version"])
        self.assertEqual(gap, rec["harness_revision"])


class TheHumanViewIsRenderedFromTheSameRecord(unittest.TestCase):
    """E72.2 (docs/plan/READINESS-ROADMAP-2026-08-29.json row E72): the page
    a person opens is built from receipt_record() and from nothing else, so
    the two cannot disagree. Driven on the same real journal, real claim
    store and real Work document the E72.1 class uses, never on a hand-typed
    view, because a page that renders a fixture proves nothing about the
    record the engine actually produces."""

    def _seed(self, run_dir):
        return _seed_two_unit_run(self, run_dir)

    def _receipts(self, run_dir, record):
        return _receipts_for(run_dir, record)

    def test_every_field_of_the_machine_view_reaches_the_page(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            view = RD.receipt_record(run_dir, receipts)
            spec = RD.receipt_spec(view, record, log_path="/tmp/run.log",
                                   record_path="/tmp/receipt.json")
            page = decide.render(spec)
            # One criterion per field of the record, and the answer itself
            # on the page rather than only the field name.
            keys = [c["key"] for c in spec["criteria"]]
            self.assertEqual(sorted(keys), sorted(view.keys()))
            for answer in RD.receipt_answers(view, "/tmp/run.log",
                                             "/tmp/receipt.json"):
                self.assertIn(answer["key"], spec["options"][0]["score_basis"])
                self.assertIn(html.escape(answer["answer"]), page,
                              answer["key"])

    def test_the_answers_carry_the_records_own_facts(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            view = RD.receipt_record(run_dir, receipts)
            by_key = {a["key"]: a["answer"]
                      for a in RD.receipt_answers(view, "/tmp/run.log")}
            self.assertIn("mathlib.py", by_key["scope"])
            self.assertIn("ship the thing", by_key["intent"])
            self.assertIn("A ran", by_key["evidence"])
            self.assertIn("/tmp/run.log", RD.receipt_answers(
                view, "/tmp/run.log")[2]["where"])
            self.assertIn("B", by_key["unproven"])
            self.assertIn("2 attempt(s)", by_key["repair_history"])
            self.assertIn("yes", by_key["containment"])

    def test_a_no_data_field_renders_as_no_data_never_as_a_blank(self):
        # No run directory at all, so continuity.capsule has nothing to fold
        # and receipt_record's continuity field reads NO-DATA. The page must
        # SAY so, and must not score the question.
        record = {"outcome": "o", "work_id": "w", "rows": [
            {"id": "U1", "objective": "x", "done_check": "true",
             "status": "DONE", "check_passed_before": False,
             "files_changed_by_unit": ["f.py"], "owns": ["f.py"]}]}
        claims = {"U1": {"state": "done",
                         "evidence": {"exit_code": 0, "output": ""}}}
        receipts = RD.receipts_for(record, claims, [])
        with mock.patch.dict(os.environ,
                             {journal.RUN_DIR_ENV_VAR: ""}, clear=False):
            view = RD.receipt_record(record, receipts)
        self.assertIsNone(view["continuity"]["capsule"])
        spec = RD.receipt_spec(view, record)
        self.assertNotIn("continuity", spec["options"][0]["scores"])
        self.assertIn(RD.NODATA, spec["options"][0]["score_basis"]
                      ["continuity"])
        page = decide.render(spec)
        # decide.py prints an unmarked criterion as NO-DATA in the mark
        # column and names it in its own warning; a blank cell would be the
        # defect this case exists to catch.
        self.assertIn(RD.NODATA, page)
        self.assertIn("Can I reproduce this, or continue it? (continuity)",
                      page)

    def test_the_screen_is_written_beside_the_run_with_its_own_record(self):
        with tempfile.TemporaryDirectory() as run_dir:
            record = self._seed(run_dir)
            receipts = self._receipts(run_dir, record)
            with mock.patch.dict(os.environ,
                                 {journal.RUN_DIR_ENV_VAR: run_dir},
                                 clear=False):
                path, view, text = RD.render_receipt_screen(
                    record, receipts, run_dir, log_path="/tmp/run.log")
            self.assertTrue(os.path.isfile(path), path)
            self.assertEqual(os.path.dirname(path),
                             os.path.join(run_dir, "screens"))
            with open(os.path.splitext(path)[0] + ".json",
                      encoding="utf-8") as fh:
                written = json.load(fh)
            # THE WHOLE POINT OF THE ROW: the machine view travels beside the
            # page, and it is the same dict the page was rendered from.
            self.assertEqual(written["receipt_record"], view)
            self.assertIn("the receipt for this delivery", text)
            for _key, question in RD.RECEIPT_QUESTIONS:
                self.assertIn(question, text)
#: The seeded diff E75's own done_check names: a middleware change, a
#: generated file and a new dependency, plus a fourth file whose check
#: proved nothing, so all four sections have something to say. Built as a
#: record plus a claim dict and pushed through receipts_for, the same seam
#: the E72.1 tests above use, so nothing here is a hand-typed receipt.
def _seeded_diff():
    record = {"outcome": "seeded", "work_id": "w", "rows": [
        {"id": "M", "objective": "tighten the request middleware",
         "done_check": "true", "status": "DONE", "check_passed_before": False,
         "owns": ["src/", "requirements.txt", "docs/"],
         "files_changed_by_unit": ["src/middleware/rate_limit.py",
                                   "requirements.txt",
                                   "docs/generated/api-index.html"]},
        {"id": "N", "objective": "a check that proves nothing",
         "done_check": "true", "status": "DONE", "check_passed_before": True,
         "owns": ["src/copy.py", "src/untouched.py"],
         "files_changed_by_unit": ["src/copy.py"]},
    ]}
    claims = {"M": {"state": "done", "evidence": {"exit_code": 0,
                                                  "output": ""}},
              "N": {"state": "done", "evidence": {"exit_code": 0,
                                                  "output": ""}}}
    return record, RD.receipts_for(record, claims, [], "run.log")


class ThePathClassifierOrdersReviewerAttention(unittest.TestCase):
    """E75.1: which changed files a reviewer must read first, derived from
    the diff, the declared scope and each receipt's own state, never from
    prose and never from a model."""

    def test_the_seeded_diff_lands_in_the_four_buckets(self):
        order = RD.reading_order(*_seeded_diff())
        first = {e["path"] for e in order[RD.REVIEW_FIRST]}
        # The middleware (an auth-class path) and the dependency manifest
        # are read first; the generated file is not.
        self.assertIn("src/middleware/rate_limit.py", first)
        self.assertIn("requirements.txt", first)
        self.assertNotIn("docs/generated/api-index.html", first)
        mechanical = {e["path"] for e in order[RD.LOW_RISK_MECHANICAL]}
        self.assertEqual(mechanical, {"docs/generated/api-index.html"})
        # Unit N's check passed before the work, so it proved nothing.
        self.assertEqual({e["path"] for e in order[RD.NOT_PROVEN]},
                         {"src/copy.py"})
        # A path unit N declared and never touched: nothing to re-read.
        self.assertEqual({e["path"] for e in order[RD.NO_NEED_TO_RE_READ]},
                         {"src/untouched.py"})

    def test_every_section_is_present_even_when_it_is_empty(self):
        order = RD.reading_order({"outcome": "o", "rows": []}, [])
        self.assertEqual(sorted(order), sorted(RD.READING_SECTIONS))
        self.assertTrue(all(v == [] for v in order.values()))

    def test_a_file_outside_the_declared_scope_is_read_first(self):
        record, _ = _seeded_diff()
        record["rows"][1]["files_changed_by_unit"] = ["src/elsewhere.py"]
        claims = {"M": {"state": "done", "evidence": {"exit_code": 0}},
                  "N": {"state": "done", "evidence": {"exit_code": 0}}}
        order = RD.reading_order(
            record, RD.receipts_for(record, claims, [], "run.log"))
        first = {e["path"]: e["why"] for e in order[RD.REVIEW_FIRST]}
        self.assertIn("src/elsewhere.py", first)
        self.assertIn("outside the scope", first["src/elsewhere.py"])

    def test_the_word_bound_holds_both_ways(self):
        # Driven backwards: a path that merely CONTAINS a risk word is not
        # a risk path, or the section cries wolf on every commit.
        self.assertEqual(RD.path_risk("src/author.py"), "")
        self.assertEqual(RD.path_risk("docs/monetary.md"), "")
        self.assertEqual(RD.path_risk("docs/package.json.md"), "")
        self.assertEqual(RD.path_risk("src/auth/login.py"), "auth")
        self.assertEqual(RD.path_risk("package.json"), "dependency manifest")


class TheFourSectionsAreOnTheReceipt(unittest.TestCase):
    """E75.2: the sections the classifier computed, rendered on the screen
    a person actually opens."""

    def test_the_screen_carries_all_four_headings_with_their_own_paths(self):
        # ACC-0: the screen now also carries a fifth, closing section (the
        # suggested decision), so this checks READING_SECTIONS is a
        # SUBSET of what is on the screen rather than the whole of it.
        record, receipts = _seeded_diff()
        spec = RD.acceptance_spec(record, receipts)
        by_heading = {s["heading"]: s["items"] for s in spec["sections"]}
        for heading in RD.READING_SECTIONS:
            self.assertIn(heading, by_heading)
        joined = " ".join(by_heading[RD.REVIEW_FIRST])
        self.assertIn("src/middleware/rate_limit.py", joined)
        self.assertIn("requirements.txt", joined)
        self.assertNotIn("docs/generated/api-index.html", joined)
        # ACC-0: LOW-RISK MECHANICAL now collapses to one count line rather
        # than one line per file, so the path itself no longer has to
        # appear on the first screen for a proven, risk-free file.
        self.assertEqual(len(by_heading[RD.LOW_RISK_MECHANICAL]), 1)
        self.assertIn("1 file", by_heading[RD.LOW_RISK_MECHANICAL][0])
        self.assertIn("expand in the receipt page",
                      by_heading[RD.LOW_RISK_MECHANICAL][0])
        html = __import__("decide").render(spec)
        for heading in RD.READING_SECTIONS:
            self.assertIn(heading, html)
        self.assertIn("Suggested decision:", html)
        self.assertIn("src/middleware/rate_limit.py", html)

    def test_an_empty_section_is_shown_as_empty_not_dropped(self):
        spec = RD.acceptance_spec({"outcome": "o", "rows": []}, [])
        html = __import__("decide").render(spec)
        for heading in RD.READING_SECTIONS:
            self.assertIn(heading, html)
        self.assertEqual(html.count(RD.EMPTY_SECTION),
                         len(RD.READING_SECTIONS))



#: ACC-0's own multi-file fixture (docs/plan/NIGHT-RUN-2026-09-09-
#: ACCEPTANCE-PROOF.md, contract test (a)): one review-first file, one
#: not-proven file, three low-risk files, two no-need-to-re-read files, so
#: all four buckets AND the collapsing rule have something real to say.
def _acc0_fixture():
    rows = [
        {"id": "R", "objective": "harden the auth check",
         "done_check": "true", "status": "DONE",
         "check_passed_before": False, "owns": ["src/auth/"],
         "files_changed_by_unit": ["src/auth/login.py"]},
        {"id": "L", "objective": "regenerate the docs",
         "done_check": "black --check docs/gen", "status": "DONE",
         "check_passed_before": False, "owns": ["docs/gen/"],
         "files_changed_by_unit": ["docs/gen/a.html", "docs/gen/b.html",
                                   "docs/gen/c.html"]},
        {"id": "P", "objective": "a check that proves nothing",
         "done_check": "true", "status": "DONE",
         "check_passed_before": True, "owns": ["src/copy.py"],
         "files_changed_by_unit": ["src/copy.py"]},
        {"id": "U", "objective": "declared but never touched",
         "done_check": "true", "status": "DONE",
         "check_passed_before": False,
         "owns": ["src/unused1.py", "src/unused2.py"],
         "files_changed_by_unit": []},
    ]
    claims = {rid: {"state": "done",
                    "evidence": {"exit_code": 0, "output": ""}}
             for rid in ("R", "L", "P", "U")}
    record = {"outcome": "acceptance fixture", "work_id": "w", "rows": rows}
    return record, RD.receipts_for(record, claims, [], "run.log")


#: Recorded before ACC-0's rendering change, from this exact fixture
#: through the unmodified acceptance_spec()/decide.render() (git stash of
#: the ACC-0 commit reproduces it): the first screen ran to this many
#: lines with one row per LOW-RISK MECHANICAL and NO NEED TO RE-READ file.
#: Test (f) below asserts the new screen is shorter than this measured
#: number, never eyeballed.
ACC0_BEFORE_LINE_COUNT = 173


class TheFirstScreenIsDecisiveWithoutHidingAnything(unittest.TestCase):
    """ACC-0 (docs/plan/NIGHT-RUN-2026-09-09-ACCEPTANCE-PROOF.md): REVIEW
    FIRST and NOT PROVEN stay expanded with their reason, proof and
    uncertainty; LOW-RISK MECHANICAL and NO NEED TO RE-READ collapse to a
    count line; a NO-DATA row is never folded into that count; a suggested
    decision closes the screen as a labelled machine suggestion; empty
    sections still print "0 files"; the screen is shorter than before, by
    a number, not a guess."""

    def test_a_review_first_and_a_not_proven_file_render_expanded(self):
        record, receipts = _acc0_fixture()
        spec = RD.acceptance_spec(record, receipts)
        by_heading = {s["heading"]: s["items"] for s in spec["sections"]}
        review_first = by_heading[RD.REVIEW_FIRST]
        self.assertEqual(len(review_first), 1)
        line = review_first[0]
        self.assertIn("src/auth/login.py", line)
        self.assertIn("the path names auth", line)
        self.assertIn("Proof:", line)
        self.assertIn("Uncertainty:", line)
        not_proven = by_heading[RD.NOT_PROVEN]
        self.assertEqual(len(not_proven), 1)
        line = not_proven[0]
        self.assertIn("src/copy.py", line)
        self.assertIn("Proof:", line)
        self.assertIn("Uncertainty:", line)
        self.assertIn("the check already passed before the work began",
                      line)

    def test_the_two_low_value_sections_collapse_to_one_count_line_each(
            self):
        record, receipts = _acc0_fixture()
        spec = RD.acceptance_spec(record, receipts)
        by_heading = {s["heading"]: s["items"] for s in spec["sections"]}
        low_risk = by_heading[RD.LOW_RISK_MECHANICAL]
        self.assertEqual(len(low_risk), 1)
        self.assertIn(RD.LOW_RISK_MECHANICAL, low_risk[0])
        self.assertIn("3 files", low_risk[0])
        self.assertIn("proof:", low_risk[0])
        self.assertIn("expand in the receipt page", low_risk[0])
        no_need = by_heading[RD.NO_NEED_TO_RE_READ]
        self.assertEqual(len(no_need), 1)
        self.assertIn(RD.NO_NEED_TO_RE_READ, no_need[0])
        self.assertIn("2 files", no_need[0])
        self.assertIn("expand in the receipt page", no_need[0])
        # Every path that collapsed is still real evidence, just not on
        # the first screen: the receipt page (JSON sibling) still carries
        # every one of them, in the classifier's own order (untouched).
        order = RD.reading_order(record, receipts)
        self.assertEqual(
            {e["path"] for e in order[RD.LOW_RISK_MECHANICAL]},
            {"docs/gen/a.html", "docs/gen/b.html", "docs/gen/c.html"})
        self.assertEqual(
            {e["path"] for e in order[RD.NO_NEED_TO_RE_READ]},
            {"src/unused1.py", "src/unused2.py"})

    def test_a_no_data_row_inside_a_collapsed_section_still_prints_alone(
            self):
        # This exact combination (a per-file check that did not verify,
        # sitting inside a section built to collapse) cannot come out of
        # reading_order() today: LOW-RISK MECHANICAL only ever holds a
        # verified entry (E75.1's own `proven` gate). That is exactly why
        # this is tested directly against the collapsing helper itself,
        # as the defensive invariant the contract names: a collapsed
        # section must NEVER quietly fold an unproven row into its count,
        # whatever reading_order() does or ever comes to do.
        items = [{"path": "src/ok_one.py", "unit": "X", "why": "fine"},
                {"path": "src/ok_two.py", "unit": "X", "why": "fine"},
                {"path": "src/no_data_row.py", "unit": "X",
                 "why": "no risk class in the path"}]
        lookup = {
            ("X", "src/ok_one.py"): {"check_command": "true",
                                     "exit_code": 0, "state": "verified",
                                     "check_passed_before": False},
            ("X", "src/ok_two.py"): {"check_command": "true",
                                     "exit_code": 0, "state": "verified",
                                     "check_passed_before": False},
            ("X", "src/no_data_row.py"): {"check_command": "true",
                                          "exit_code": None,
                                          "state": "no-data",
                                          "reason": "nothing ran"},
        }
        lines = RD._collapsed_lines(RD.LOW_RISK_MECHANICAL, items, lookup,
                                    "each file's own check, all verified")
        self.assertEqual(len(lines), 2)
        self.assertIn("2 files", lines[0])
        self.assertNotIn("src/no_data_row.py", lines[0])
        self.assertIn("src/no_data_row.py", lines[1])
        self.assertIn("nothing ran", lines[1])

    def test_the_suggested_decision_reads_reject_with_a_not_proven_file(
            self):
        # ACC-0: folded into the existing footer (one more sentence,
        # zero new HTML structure) rather than a fifth section, so the
        # collapsing above actually shrinks the screen instead of only
        # relocating its length. It still closes the screen: the footer
        # is the last thing decide.render() prints.
        record, receipts = _acc0_fixture()
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: REJECT", spec["footer"])
        self.assertIn("machine suggestion", spec["footer"].lower())
        self.assertNotIn("accepted", spec["footer"].lower())
        html = __import__("decide").render(spec)
        self.assertIn("Suggested decision: REJECT", html)
        # The footer really is the last section decide.render() writes.
        self.assertGreater(html.rindex("Suggested decision:"),
                           html.rindex(RD.NO_NEED_TO_RE_READ))

    def test_the_suggested_decision_reads_proceed_with_nothing_unproven(
            self):
        rows = [{"id": "L", "objective": "regenerate the docs",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["docs/gen/"],
                "files_changed_by_unit": ["docs/gen/a.html"]}]
        claims = {"L": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "all clean", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: PROCEED", spec["footer"])
        self.assertIn("machine suggestion", spec["footer"].lower())

    def test_empty_sections_still_print_their_heading_with_0_files(self):
        spec = RD.acceptance_spec({"outcome": "o", "rows": []}, [])
        by_heading = {s["heading"]: s for s in spec["sections"]}
        for heading in RD.READING_SECTIONS:
            self.assertEqual(by_heading[heading]["items"], [])
            self.assertIn("0 files", by_heading[heading]["empty"])
        html = __import__("decide").render(spec)
        self.assertEqual(html.count("0 files"), len(RD.READING_SECTIONS))

    def test_the_receipt_screens_own_json_is_untouched_by_this_change(self):
        # E72.2's delivery receipt (the eight-answer terminal screen) is a
        # wholly separate rendering path (receipt_record/receipt_spec)
        # from the acceptance screen's reading-order sections: ACC-0
        # touches the acceptance side only, so the receipt's own JSON
        # shape (its keys, and none of ACC-0's new vocabulary) must read
        # exactly as it did before this change.
        record, receipts = _seeded_diff()
        with mock.patch.dict(os.environ,
                             {journal.RUN_DIR_ENV_VAR: ""}, clear=False):
            view = RD.receipt_record(record, receipts)
        spec = RD.receipt_spec(view, record)
        self.assertEqual(sorted(spec), sorted([
            "title", "eyebrow", "stamp", "plain_summary", "question",
            "criteria", "options", "would_change", "footer"]))
        self.assertNotIn("sections", spec)
        dumped = json.dumps(spec, sort_keys=True, default=str)
        self.assertNotIn("Suggested decision", dumped)
        self.assertNotIn("expand in the receipt page", dumped)

    def test_the_first_screen_is_shorter_than_it_was_before_acc0(self):
        record, receipts = _acc0_fixture()
        spec = RD.acceptance_spec(record, receipts)
        html = __import__("decide").render(spec)
        after = len(html.splitlines())
        self.assertLess(after, ACC0_BEFORE_LINE_COUNT,
                        "first screen grew: %d lines, was %d"
                        % (after, ACC0_BEFORE_LINE_COUNT))


class RepairA1AVerifiedStateWithNoRealProofIsStillUnproven(
        unittest.TestCase):
    """A1 (adversarial review of fd04e557/068c53c1): a per-file entry can
    carry state == "verified" while its own check_command is empty or its
    exit_code is None, i.e. nothing a stranger could re-run actually
    decided it. _is_unproven_entry must catch that, and a collapsed
    section must render it as its own expanded line rather than folding
    it into the count."""

    def test_verified_with_empty_check_command_is_unproven(self):
        entry = {"check_command": "", "exit_code": 0, "state": "verified"}
        self.assertTrue(RD._is_unproven_entry(entry))

    def test_verified_with_none_exit_code_is_unproven(self):
        entry = {"check_command": "true", "exit_code": None,
                 "state": "verified"}
        self.assertTrue(RD._is_unproven_entry(entry))

    def test_verified_with_a_real_command_and_exit_code_stays_proven(self):
        entry = {"check_command": "true", "exit_code": 0,
                 "state": "verified"}
        self.assertFalse(RD._is_unproven_entry(entry))

    def test_such_a_row_prints_expanded_not_folded_into_the_count(self):
        items = [{"path": "src/ok.py", "unit": "X", "why": "fine"},
                {"path": "src/hollow.py", "unit": "X",
                 "why": "no risk class in the path"}]
        lookup = {
            ("X", "src/ok.py"): {"check_command": "true", "exit_code": 0,
                                 "state": "verified",
                                 "check_passed_before": False},
            ("X", "src/hollow.py"): {"check_command": "", "exit_code": None,
                                     "state": "verified",
                                     "check_passed_before": False},
        }
        lines = RD._collapsed_lines(RD.LOW_RISK_MECHANICAL, items, lookup,
                                    "each file's own check, all verified")
        self.assertEqual(len(lines), 2)
        self.assertIn("1 file", lines[0])
        self.assertNotIn("src/hollow.py", lines[0])
        self.assertIn("src/hollow.py", lines[1])


class RepairA2ADuplicateKeyNeverHidesAnUnprovenEntry(unittest.TestCase):
    """A2: _check_lookup is keyed by (unit, file); when per_file_checks()
    yields two entries for the same key (a unit that lists the same file
    twice, or a malformed record), a later verified entry must never
    silently overwrite an earlier unproven one, and an unproven entry
    arriving after a verified one must still win. Driven both ways by
    reversing the order of the two duplicate entries."""

    def test_a_later_verified_entry_never_overwrites_an_earlier_unproven(
            self):
        unproven = {"file": "src/x.py", "unit": "X", "check_command": "",
                   "exit_code": None, "state": "no-data", "reason": "flaky"}
        proven = {"file": "src/x.py", "unit": "X", "check_command": "true",
                 "exit_code": 0, "state": "verified"}
        with mock.patch.object(RD, "per_file_checks",
                               return_value=[unproven, proven]):
            lookup = RD._check_lookup({}, [])
        self.assertTrue(RD._is_unproven_entry(lookup[("X", "src/x.py")]))

    def test_an_unproven_entry_arriving_second_still_wins(self):
        unproven = {"file": "src/x.py", "unit": "X", "check_command": "",
                   "exit_code": None, "state": "no-data", "reason": "flaky"}
        proven = {"file": "src/x.py", "unit": "X", "check_command": "true",
                 "exit_code": 0, "state": "verified"}
        with mock.patch.object(RD, "per_file_checks",
                               return_value=[proven, unproven]):
            lookup = RD._check_lookup({}, [])
        self.assertTrue(RD._is_unproven_entry(lookup[("X", "src/x.py")]))

    def test_two_proven_entries_at_the_same_key_stay_proven(self):
        proven_a = {"file": "src/x.py", "unit": "X", "check_command": "true",
                   "exit_code": 0, "state": "verified"}
        proven_b = {"file": "src/x.py", "unit": "X", "check_command": "true",
                   "exit_code": 0, "state": "verified"}
        with mock.patch.object(RD, "per_file_checks",
                               return_value=[proven_a, proven_b]):
            lookup = RD._check_lookup({}, [])
        self.assertFalse(RD._is_unproven_entry(lookup[("X", "src/x.py")]))


class RepairA3ACollapsedHintNeverNamesOneCheckForDifferingProofs(
        unittest.TestCase):
    """A3: _proof_hint must not name a single check_command for a
    collapsed section unless every quiet entry in it shares that exact
    command; when two or more entries carry different commands, the line
    says the proofs differ instead of picking one and implying it covers
    the rest."""

    def test_a_single_shared_command_is_still_named(self):
        items = [{"path": "a", "unit": "X"}, {"path": "b", "unit": "X"}]
        lookup = {("X", "a"): {"check_command": "true"},
                 ("X", "b"): {"check_command": "true"}}
        hint = RD._proof_hint(items, lookup, "fallback")
        self.assertEqual(hint, "true")

    def test_two_distinct_commands_never_pick_one_and_say_proofs_differ(
            self):
        items = [{"path": "a", "unit": "X"}, {"path": "b", "unit": "X"}]
        lookup = {("X", "a"): {"check_command": "true"},
                 ("X", "b"): {"check_command": "pytest -q"}}
        hint = RD._proof_hint(items, lookup, "fallback")
        self.assertNotIn("true", hint)
        self.assertNotIn("pytest", hint)
        self.assertEqual(hint, "proofs differ, see the receipt page")

    def test_no_command_at_all_falls_back_to_the_passed_in_sentence(self):
        items = [{"path": "a", "unit": "X"}]
        lookup = {("X", "a"): {"check_command": ""}}
        hint = RD._proof_hint(items, lookup, "fallback")
        self.assertEqual(hint, "fallback")


class RepairA5SuggestedDecisionCountsEveryReviewFirstReason(
        unittest.TestCase):
    """A5 (repair round 2, second adversarial review of the rendered
    screen): _suggested_decision_line only ever counted REVIEW FIRST
    files whose own check also failed to verify, so any other reason a
    file lands in REVIEW FIRST (out-of-scope write, no declared scope, a
    confirmed reviewer finding, a risk-class path) was invisible to it
    and the footer could print PROCEED, or the false "every file ... has
    a check that actually verified it" claim, over a screen that plainly
    needed a human look. Three cases from the review, each driven test
    first."""

    def test_t3_an_out_of_scope_but_proven_file_still_rejects(self):
        # A file the unit wrote OUTSIDE its own declared scope, with a
        # check that genuinely passed (a real command, exit 0, measured
        # against a False precheck): the old code's
        # `_is_unproven_entry(...)` filter saw a proven entry and never
        # counted it, so REVIEW FIRST held one file (the scope-drift
        # reason) while the footer still read PROCEED.
        rows = [{"id": "S", "objective": "touch a file outside scope",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["src/scope_ok/"],
                "files_changed_by_unit": ["src/elsewhere/thing.py"]}]
        claims = {"S": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "scope drift fixture", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        order = RD.reading_order(record, receipts)
        self.assertEqual(len(order[RD.REVIEW_FIRST]), 1)
        self.assertEqual(len(order[RD.NOT_PROVEN]), 0)
        lookup = RD._check_lookup(record, receipts)
        entry = lookup.get(("S", "src/elsewhere/thing.py"))
        self.assertFalse(RD._is_unproven_entry(entry),
                         "fixture must be a genuinely proven file, or "
                         "this case proves nothing new over A1")
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: REJECT", spec["footer"])
        self.assertNotIn("Suggested decision: PROCEED", spec["footer"])

    def test_t1_zero_files_reads_no_data_never_proceed(self):
        record = {"outcome": "empty run", "rows": []}
        receipts = RD.receipts_for(record, {}, [], "run.log")
        order = RD.reading_order(record, receipts)
        self.assertEqual(sum(len(v) for v in order.values()), 0)
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: NO-DATA", spec["footer"])
        self.assertNotIn("Suggested decision: PROCEED", spec["footer"])
        self.assertNotIn("Suggested decision: REJECT", spec["footer"])
        self.assertIn("nothing on this screen was checked",
                      spec["footer"].lower())

    def test_t2_only_no_need_to_re_read_files_never_claims_a_check_ran(
            self):
        # A unit that declared a path and never touched it: the only
        # section with anything in it is NO NEED TO RE-READ, whose files
        # carry no check at all (declared_untouched() never runs one).
        # The old PROCEED sentence ("every file ... has a check that
        # actually verified it") was false for this screen.
        rows = [{"id": "U", "objective": "declared but never touched",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False,
                "owns": ["src/never_touched.py"],
                "files_changed_by_unit": []}]
        claims = {"U": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "untouched-only fixture", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        order = RD.reading_order(record, receipts)
        self.assertEqual(len(order[RD.NO_NEED_TO_RE_READ]), 1)
        self.assertEqual(len(order[RD.REVIEW_FIRST]), 0)
        self.assertEqual(len(order[RD.NOT_PROVEN]), 0)
        self.assertEqual(len(order[RD.LOW_RISK_MECHANICAL]), 0)
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: PROCEED", spec["footer"])
        self.assertNotIn("has a check that actually verified it",
                         spec["footer"])
        self.assertIn("declared and never touched", spec["footer"])

    def test_review_first_and_not_proven_still_reject_as_before(self):
        # Guard: the existing REJECT case (a NOT PROVEN file, from the
        # ACC-0 fixture) must still reject after this round's rewrite.
        record, receipts = _acc0_fixture()
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: REJECT", spec["footer"])

    def test_all_low_risk_mechanical_still_proceeds_with_the_check_claim(
            self):
        # Guard: the pre-existing PROCEED case (every file verified by
        # its own check, nothing declared-and-untouched on the screen)
        # must keep the original sentence, since it is literally true
        # there.
        rows = [{"id": "L", "objective": "regenerate the docs",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["docs/gen/"],
                "files_changed_by_unit": ["docs/gen/a.html"]}]
        claims = {"L": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "all clean", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: PROCEED", spec["footer"])
        self.assertIn("has a check that actually verified it",
                      spec["footer"])


class RepairA6TheCollapsedFlagQuietSplitStaysHelperOnly(unittest.TestCase):
    """A6 (repair round 2, evaluated against the same item's own
    condition A4 used: keep only what a real record can actually
    produce). `_collapsed_lines`' flagged/quiet split (a file inside a
    COLLAPSIBLE section whose own entry is still `_is_unproven_entry`)
    was checked in round 1 directly against the helper
    (`test_a_no_data_row_inside_a_collapsed_section_still_prints_alone`,
    `RepairA1...test_such_a_row_prints_expanded_not_folded_into_the_count`)
    because `reading_order()` itself was believed to never produce that
    combination. This class pins down WHY, against `receipts_for()`
    itself rather than a belief about it, so the claim stays true if
    `receipts_for` ever changes: the one line that sets
    `state = "verified"` (`receipt_door.py`, inside `receipts_for`) sits
    behind `exit_code == 0 and command`, so every receipt this function
    ever hands out is either not "verified", or carries a non-empty
    command and a real 0 exit code -- exactly the two facts
    `_is_unproven_entry` checks. No record built by `receipts_for` can
    ever put an unproven entry in a bucket `_is_unproven_entry` calls
    proven, so the flagged branch stays reachable through the helper's
    own direct tests only, never through `render`."""

    def test_no_real_receipt_is_verified_with_no_real_proof(self):
        rows = [
            {"id": "A", "objective": "a normal proven change",
             "done_check": "true", "status": "DONE",
             "check_passed_before": False, "owns": ["src/a.py"],
             "files_changed_by_unit": ["src/a.py"]},
            {"id": "B", "objective": "a check that fails",
             "done_check": "false", "status": "DONE",
             "check_passed_before": False, "owns": ["src/b.py"],
             "files_changed_by_unit": ["src/b.py"]},
            {"id": "C", "objective": "no check ever recorded",
             "status": "DONE", "check_passed_before": False,
             "owns": ["src/c.py"], "files_changed_by_unit": ["src/c.py"]},
        ]
        claims = {
            "A": {"state": "done",
                 "evidence": {"exit_code": 0, "output": ""}},
            "B": {"state": "done",
                 "evidence": {"exit_code": 1, "output": ""}},
            "C": {"state": "done", "evidence": {"output": ""}},
        }
        record = {"outcome": "invariant sweep", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        checked_any_verified = False
        for entry in RD.per_file_checks(record, receipts):
            if entry.get("state") == "verified":
                checked_any_verified = True
                self.assertTrue(entry.get("check_command"))
                self.assertEqual(entry.get("exit_code"), 0)
                self.assertFalse(RD._is_unproven_entry(entry))
        self.assertTrue(checked_any_verified,
                        "fixture must produce at least one verified "
                        "entry, or this proves nothing")

    def test_a_never_touched_declared_path_has_no_lookup_entry_either(
            self):
        # NO NEED TO RE-READ's own case: declared_untouched() never runs
        # a check, so the lookup entry is always None there, which
        # _is_unproven_entry also reads as "not flagged" (a proof never
        # owed is not a proof that failed).
        rows = [{"id": "U", "objective": "declared, never touched",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["src/never.py"],
                "files_changed_by_unit": []}]
        claims = {"U": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "untouched", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        lookup = RD._check_lookup(record, receipts)
        self.assertIsNone(lookup.get(("U", "src/never.py")))
        self.assertFalse(RD._is_unproven_entry(
            lookup.get(("U", "src/never.py"))))



def _hollow_verified_receipt(uid, objective):
    """A receipt shaped like receipts_for()'s own output, except for the
    one field receipts_for() can never actually produce (RepairA6, the
    round 2 invariant): state == "verified" with an empty command, so
    _is_unproven_entry still calls it unproven even though
    reading_order()'s own cruder `state == "verified"` gate does not.
    exit_code is a real 0, never None: receipt_sentence()'s "verified"
    sentence formats exit_code with %d, so a None there would crash
    acceptance_spec() itself (building the options list) rather than
    exercise the bug this repair fixes."""
    return {"id": uid, "objective": objective, "command": "",
           "exit_code": 0, "state": "verified", "output_location": "run.log",
           "reason": "", "author": "the planning model",
           "harness_revision": RD.NODATA, "dependency_note": RD.NODATA,
           "evidence_family": RD.NODATA, "oracle_source": RD.NODATA,
           "independence": RD.NODATA, "target_revision": RD.NODATA,
           "env_lock": RD.NODATA, "data_identity": RD.NODATA}


class RepairA7AHollowVerifiedEntryNeverReadsProceed(unittest.TestCase):
    """A7 (repair round 3, driven by command): a per-file entry can carry
    state == "verified" while its own check_command is empty (or its
    exit_code is None), the exact hollow shape A1 already named
    _is_unproven_entry to catch. RepairA6 proved receipts_for() itself
    can never produce that combination, so every test here drives it
    through the real render pipeline (acceptance_spec()/decide.render())
    with a hand-built receipt -- the render-level counterpart to A1's
    helper-level test (test_such_a_row_prints_expanded_not_folded_
    into_the_count, which calls _collapsed_lines directly). Before this
    repair: the row printed honestly (Proof named the missing command,
    Uncertainty said "none recorded"), landed inside LOW-RISK MECHANICAL
    because reading_order()'s own `proven` gate only checks the bare
    state string, and the closing line still read "Suggested decision:
    PROCEED ... every file ... has a check that actually verified it" --
    a claim this exact file makes false. _suggested_decision_line and
    _uncertainty_text decided by section membership and by the bare
    state string, never through _is_unproven_entry."""

    def test_the_hollow_entry_lands_in_low_risk_mechanical(self):
        # Confirms the bug's own premise still holds after this repair:
        # reading_order()'s classification is untouched (this repair
        # fixes the rendering that reads the classified sections, not
        # the classifier itself), so the hollow entry still sits in
        # LOW-RISK MECHANICAL rather than moving to REVIEW FIRST or NOT
        # PROVEN.
        rows = [{"id": "L", "objective": "regenerate the docs",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["docs/gen/"],
                "files_changed_by_unit": ["docs/gen/a.html"]}]
        record = {"outcome": "hollow verified fixture", "rows": rows}
        receipts = [_hollow_verified_receipt("L", "regenerate the docs")]
        order = RD.reading_order(record, receipts)
        self.assertEqual(len(order[RD.LOW_RISK_MECHANICAL]), 1)
        self.assertEqual(order[RD.LOW_RISK_MECHANICAL][0]["path"],
                         "docs/gen/a.html")
        lookup = RD._check_lookup(record, receipts)
        entry = lookup.get(("L", "docs/gen/a.html"))
        self.assertTrue(RD._is_unproven_entry(entry),
                        "fixture must be unproven, or this test proves "
                        "nothing new over A1")

    def test_the_only_file_on_the_screen_reads_no_data_not_proceed(self):
        rows = [{"id": "L", "objective": "regenerate the docs",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["docs/gen/"],
                "files_changed_by_unit": ["docs/gen/a.html"]}]
        record = {"outcome": "hollow verified fixture", "rows": rows}
        receipts = [_hollow_verified_receipt("L", "regenerate the docs")]
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: NO-DATA", spec["footer"])
        self.assertNotIn("Suggested decision: PROCEED", spec["footer"])
        self.assertNotIn("has a check that actually verified it",
                         spec["footer"])
        html = decide.render(spec)
        self.assertIn("Suggested decision: NO-DATA", html)
        self.assertNotIn("Suggested decision: PROCEED", html)

    def test_the_row_itself_names_no_proof_recorded_not_none_recorded(
            self):
        rows = [{"id": "L", "objective": "regenerate the docs",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["docs/gen/"],
                "files_changed_by_unit": ["docs/gen/a.html"]}]
        record = {"outcome": "hollow verified fixture", "rows": rows}
        receipts = [_hollow_verified_receipt("L", "regenerate the docs")]
        spec = RD.acceptance_spec(record, receipts)
        by_heading = {s["heading"]: s["items"] for s in spec["sections"]}
        low_risk = by_heading[RD.LOW_RISK_MECHANICAL]
        self.assertEqual(len(low_risk), 1)
        line = low_risk[0]
        self.assertIn("docs/gen/a.html", line)
        self.assertIn("Uncertainty: no proof recorded", line)
        self.assertNotIn("none recorded", line)
        html = decide.render(spec)
        self.assertIn("no proof recorded", html)

    def test_a_second_genuinely_proven_file_makes_the_screen_reject(self):
        # Mixed screen: one real, proven file plus the hollow one. total
        # is now 2, so the NO-DATA-only-file carve-out does not apply --
        # the screen has something else to weigh the hollow file
        # against, and a human still needs to look at it, so this
        # rejects rather than proceeding or reading NO-DATA.
        rows = [
            {"id": "L", "objective": "regenerate the docs",
             "done_check": "true", "status": "DONE",
             "check_passed_before": False, "owns": ["docs/gen/"],
             "files_changed_by_unit": ["docs/gen/a.html"]},
            {"id": "M", "objective": "a real, proven change",
             "done_check": "true", "status": "DONE",
             "check_passed_before": False, "owns": ["src/real.py"],
             "files_changed_by_unit": ["src/real.py"]},
        ]
        claims = {"M": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "mixed fixture", "rows": rows}
        # receipts_for() only ever produces a real receipt for row "M"
        # here (row "L" carries no claim); RepairA6 already proved
        # receipts_for() can never itself hand out a hollow "verified"
        # receipt, so "L"'s receipt is spliced in by hand instead of
        # asking receipts_for() to produce what it cannot.
        real_receipts = RD.receipts_for(record, claims, [], "run.log")
        receipts = ([_hollow_verified_receipt("L", "regenerate the docs")]
                    + [r for r in real_receipts if r["id"] == "M"])
        order = RD.reading_order(record, receipts)
        self.assertEqual(len(order[RD.LOW_RISK_MECHANICAL]), 2)
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: REJECT", spec["footer"])
        self.assertNotIn("Suggested decision: PROCEED", spec["footer"])
        self.assertNotIn("Suggested decision: NO-DATA", spec["footer"])
        self.assertIn("marked verified with no real proof", spec["footer"])

    def test_the_verified_proceed_sentence_still_needs_every_file_real(
            self):
        # Guard: unchanged behaviour when nothing is hollow -- the exact
        # pre-existing PROCEED case (round 2's own guard test) still
        # reads PROCEED with the "has a check that actually verified it"
        # claim.
        rows = [{"id": "L", "objective": "regenerate the docs",
                "done_check": "true", "status": "DONE",
                "check_passed_before": False, "owns": ["docs/gen/"],
                "files_changed_by_unit": ["docs/gen/a.html"]}]
        claims = {"L": {"state": "done",
                       "evidence": {"exit_code": 0, "output": ""}}}
        record = {"outcome": "all clean", "rows": rows}
        receipts = RD.receipts_for(record, claims, [], "run.log")
        spec = RD.acceptance_spec(record, receipts)
        self.assertIn("Suggested decision: PROCEED", spec["footer"])
        self.assertIn("has a check that actually verified it",
                      spec["footer"])


def _j100_entry():
    return {
        "id": "J100", "role": "second_opinion", "risk": "low", "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {
            "type": "noul",
            "instructions": "did this revert break something else?",
        },
    }


def _noul_runner(prob):
    """A scripted bridge runner answering a noul question at `prob`, no
    network or subprocess: the same shape ScriptedRunner in
    test_jev_seam.py uses, reimplemented here so this file needs no
    import of that test module."""
    def fn(argv, stdin_text):
        payload = json.loads(stdin_text)
        answers = {qid: {"noul": prob, "confidence": prob}
                   for qid in payload["questions"]}
        response = {"model": "typesafe/jev-1.13-test", "answers": answers,
                    "usage": {"cost": 0.001}}
        return 0, json.dumps(response), ""
    return fn


class JevSeamJ100SecondOpinion(unittest.TestCase):
    """J100 (revert-broke-something check), wired into
    revert_broke_check() via jev_seam.consult(). Mode ships off in the
    real data/jev-seams.json, so (a) needs no mocking; (b)-(d) mock
    jev_seam.load_seams_config/load_registry to force shadow mode for
    this one test only."""

    STDERR = "Traceback (most recent call last):\nImportError: no module named x"


    def setUp(self):
        # jev_g1_seam_cache caches the resolved mode per entry_id
        # (module docstring: at most one jev_seam.load_seams_config()
        # call per second) -- a call in an EARLIER test could still be
        # "fresh" here, so reset before every test rather than rely on
        # the freshness window happening to have elapsed.
        jev_g1_seam_cache.reset()
        # The consult runs bridge_content_gate, which FAILS CLOSED when its
        # forbidden-terms list is missing, and the default list lives outside
        # every repository, in the operator's home. Tests b and d therefore
        # passed on the machine that wrote them and failed on any clean one:
        # measured 2026-09-20, the public release pull request's required-fast
        # run read fail 2 (receipt-door, export-public) on these four tests
        # while the same tree read fail 0 locally. The fixture list below
        # makes the gate's input part of the test instead of the machine.
        import json as _json
        import shutil as _shutil
        import tempfile as _tempfile
        import coe_outside_gate as _coe_gate
        _terms_dir = _tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, _terms_dir, ignore_errors=True)
        _terms = os.path.join(_terms_dir, "terms.json")
        with open(_terms, "w", encoding="utf-8") as _fh:
            _json.dump({"vendor-fixture": ["ACMEWIDGET"]}, _fh)
        _patch = mock.patch.object(_coe_gate, "DEFAULT_TERMS_PATH", _terms)
        _patch.start()
        self.addCleanup(_patch.stop)
    def test_a_mode_off_makes_zero_calls_and_output_is_byte_identical(self):
        def boom(argv, stdin_text):
            raise AssertionError("mode off must never invoke the runner")

        before = RD.revert_broke_check(self.STDERR)
        after = RD.revert_broke_check(self.STDERR, jev_runner=boom)
        self.assertTrue(before)
        self.assertEqual(before, after)

    def test_b_shadow_mode_output_identical_and_one_ledger_row_written(self):
        expected = RD.revert_broke_check(self.STDERR)  # real config: off
        ledger_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger_dir, ignore_errors=True)
        cfg = {"modes": {"J100": "shadow"}}
        jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j100_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
            after = RD.revert_broke_check(self.STDERR, jev_runner=_noul_runner(0.05))
        self.assertEqual(after, expected)
        # A0.8: shadow hands the call to a background worker and returns
        # at once, before the ledger row exists. drain() waits for the
        # worker to finish so the row is actually there to count.
        jev_seam.drain(timeout=5)
        decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
        self.assertTrue(os.path.isfile(decisions_path))
        with open(decisions_path, encoding="utf-8") as fh:
            rows = [ln for ln in fh if ln.strip()]
        self.assertEqual(len(rows), 1)

    def test_c_seam_path_exception_leaves_output_identical(self):
        expected = RD.revert_broke_check(self.STDERR)
        cfg = {"modes": {"J100": "shadow"}}
        jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry",
                                   side_effect=RuntimeError("registry unreadable")):
            after = RD.revert_broke_check(self.STDERR, jev_runner=_noul_runner(0.05))
        self.assertEqual(after, expected)

    def test_d_red_proof_wiring_that_trusts_jevs_raw_answer_breaks_test_b(self):
        current_answer = RD.revert_broke_check(self.STDERR)  # True
        ledger_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger_dir, ignore_errors=True)
        cfg = {"modes": {"J100": "shadow"}}
        jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j100_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
            result = jev_seam.consult(
                "J100", {"stderr": self.STDERR}, current_answer,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
                runner=_noul_runner(0.05),
            )
            # A0.8: shadow never returns Jev's answer (result.jev is
            # always None); the eventual answer only lands in the
            # ledger, once the background worker finishes. drain() first,
            # then read it there.
            jev_seam.drain(timeout=5)
            decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
            with open(decisions_path, encoding="utf-8") as fh:
                rows = [json.loads(ln) for ln in fh if ln.strip()]
            self.assertEqual(len(rows), 1)
            wrongly_wired = bool(rows[0]["answer"] >= 0.5)
            safely_wired = result.answer
        self.assertFalse(wrongly_wired)
        self.assertNotEqual(wrongly_wired, current_answer)
        self.assertEqual(safely_wired, current_answer)

    def test_e_a1_call_site_never_reads_a_patched_consults_return(self):
        """A1 (opus-review-g1-round2-pkg-decide.md): test_d proves
        consult()'s two fields disagree, but it never touches the CALL
        SITE's own wiring -- revert_broke_check() is never invoked inside
        the mocked block there. This one does: jev_seam.consult ITSELF
        is patched to return an ACT-mode SeamResult whose .answer is
        0.05 (a float, the wrong TYPE for this call site's own bool
        answer), mode live via a patched config, and asserts
        revert_broke_check() still returns its own local verdict, never
        0.05."""
        cfg = {"modes": {"J100": "shadow"}}
        jev_g1_seam_cache.reset()
        wrong = jev_seam.SeamResult(0.05, {"answer": 0.05}, jev_seam.ACT,
                                    None, False, "mutation-probe")
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j100_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", tempfile.mkdtemp()), \
                mock.patch.object(jev_seam, "consult", return_value=wrong):
            after = RD.revert_broke_check(self.STDERR)
        self.assertTrue(after)
        self.assertNotEqual(after, wrong.answer)


class TestJ099ReceiptRiskTriggerNeverChangesTheHits(unittest.TestCase):
    """J099, wave-2 ('Receipt risk-trigger phrase scan'): risk_triggers()'s
    own real regex matches are what the caller always receives -- see
    receipt_door.py's risk_triggers() docstring and _consult_j099()'s own
    docstring. C1: whatever the seam says, risk_triggers() keeps returning
    its own real hits."""

    #: The consult loads data/jev-registry.json before it calls the check.
    #: The public export ships scripts/ whole and data/ not at all (the
    #: registry names machine level hooks, which stay private), so in an
    #: export tree the load raises, the advisory except swallows it and the
    #: check is never reached. Tests a and b assert the check WAS reached;
    #: without the registry they cannot run, and they say so as NO-DATA
    #: instead of failing. Measured 2026-09-20: this refused the 1.0.21 push
    #: on the export tree's own fast gate (pass 33, fail 1, receipt-door).
    #: Tests c onward need no registry and run everywhere.
    _REGISTRY = os.path.join(os.path.dirname(HERE), "data", "jev-registry.json")
    _needs_registry = unittest.skipUnless(
        os.path.isfile(_REGISTRY),
        "NO-DATA: data/jev-registry.json is not in this tree (the public "
        "export does not ship data/), so the J099 consult cannot be reached "
        "here")

    def setUp(self):
        self._real_check = jev_checks.check_receipt_risk_trigger

    def tearDown(self):
        jev_checks.check_receipt_risk_trigger = self._real_check

    @_needs_registry
    def test_a_normal_call_consults_the_seam_and_keeps_the_real_hits(self):
        seen = []
        def fake(unit_text, current_answer, **k):
            seen.append((unit_text, current_answer))
            return "false"  # an adversarial-looking Jev answer, still discarded
        jev_checks.check_receipt_risk_trigger = fake
        rows = [{"id": "u1", "objective": "delete the old backup table"}]
        hits = RD.risk_triggers(rows)
        self.assertTrue(hits)  # the real regex DOES fire ("delete"/"table")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], True)  # current_answer: this row DID hit

    @_needs_registry
    def test_b_a_clean_row_reports_false_as_current_answer(self):
        seen = []
        def fake(unit_text, current_answer, **k):
            seen.append(current_answer)
            return "true"  # an adversarial-looking Jev answer, still discarded
        jev_checks.check_receipt_risk_trigger = fake
        rows = [{"id": "u1", "objective": "rename a local variable"}]
        hits = RD.risk_triggers(rows)
        self.assertEqual(hits, [])
        self.assertEqual(seen, [False])

    def test_c_jev_checks_unreachable_still_returns_the_real_hits(self):
        """Simulates jev_checks being unimportable (an installed copy of
        scripts/ with jev_checks.py missing or broken): risk_triggers()
        must still return its own real hits. Setting sys.modules['jev_checks']
        to None is the standard way to force the next `import jev_checks`
        to raise ImportError without touching the real file on disk."""
        real_module = sys.modules.get("jev_checks")
        sys.modules["jev_checks"] = None
        try:
            rows = [{"id": "u1", "objective": "delete the old backup table"}]
            hits = RD.risk_triggers(rows)
        finally:
            if real_module is not None:
                sys.modules["jev_checks"] = real_module
            else:
                del sys.modules["jev_checks"]
        self.assertTrue(hits)

    def test_d_a_raising_seam_never_loses_the_real_hits(self):
        jev_checks.check_receipt_risk_trigger = \
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        rows = [{"id": "u1", "objective": "delete the old backup table"}]
        hits = RD.risk_triggers(rows)
        self.assertTrue(hits)


class JevSeamPerfCacheNeverHitsDiskAfterWarmup(unittest.TestCase):
    """Coordinator directive (measured regression in another wave-1
    group: a seam in OFF mode made a hot call site 52x slower by
    re-reading data/jev-seams.json on every call): after a first call
    warms jev_seam's in-process cache, 1,000 further OFF-mode calls to
    revert_broke_check() must touch the filesystem zero times and return
    the identical answer every time."""


    def setUp(self):
        # jev_g1_seam_cache caches the resolved mode per entry_id
        # (module docstring: at most one jev_seam.load_seams_config()
        # call per second) -- a call in an EARLIER test could still be
        # "fresh" here, so reset before every test rather than rely on
        # the freshness window happening to have elapsed.
        jev_g1_seam_cache.reset()
    def test_1000_off_mode_calls_after_warmup_touch_no_filesystem(self):
        stderr_text = "clean re-run, nothing broke"
        first = RD.revert_broke_check(stderr_text)  # warms jev_seam's config cache
        self.assertFalse(first)

        calls = {"stat": 0, "exists": 0, "open": 0}
        real_stat, real_exists, real_open = os.stat, os.path.exists, open

        def counting_stat(*a, **kw):
            calls["stat"] += 1
            return real_stat(*a, **kw)

        def counting_exists(*a, **kw):
            calls["exists"] += 1
            return real_exists(*a, **kw)

        def counting_open(*a, **kw):
            calls["open"] += 1
            return real_open(*a, **kw)

        outputs = set()
        with mock.patch("os.stat", counting_stat), \
                mock.patch("os.path.exists", counting_exists), \
                mock.patch("builtins.open", counting_open):
            for _ in range(1000):
                outputs.add(RD.revert_broke_check(stderr_text))

        self.assertEqual(calls, {"stat": 0, "exists": 0, "open": 0},
                         "an off-mode call must never touch the filesystem "
                         "once jev_seam's config cache is warm")
        self.assertEqual(outputs, {False})


if __name__ == "__main__":
    unittest.main()
