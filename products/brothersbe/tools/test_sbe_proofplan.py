"""Adversarial fixtures for src/brothersbe/proofplan.py (BAND F, F3).

Written so the mandatory failure cases are each red-then-green: run this
file against a tree with no proofplan.py at all, and every case below is red
on an ImportError, none on a fixture bug. The five cases this file owns:

  4. a behavior with no proof surfaces as NO-DATA
  5. a proof pointing at a nonexistent check surfaces as NO-DATA
  6. a check that exists but never ran (no evidence receipt) surfaces as
     NO-DATA
  8. a proof recorded against an old head, with the tree now moved on,
     surfaces as NO-DATA (naming STALE), never a silent pass

Every helper returns the house 3-tuple (`verdict`, `evidence`, `problems`)
or `proofplan.evaluate_plan`'s own `(rows, empty_note)` pair, never a bare
two-value verdict pair: the same "no function outside a check registry
returns a two-value (verdict, evidence) shape" rule `tools/
test_sbe_decisions.py`'s own module docstring states for itself, so this
file's own helpers stay out of that trap too.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import proofplan  # noqa: E402
from brothersbe import evidence as evidence_mod  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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


def _run_git(args, cwd):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def _git_repo(path):
    _run_git(["init", "-q"], path)
    _run_git(["config", "user.email", "e@e"], path)
    _run_git(["config", "user.name", "T"], path)
    with io.open(os.path.join(path, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    _run_git(["add", "-A"], path)
    _run_git(["commit", "-qm", "seed"], path)
    return _run_git(["rev-parse", "HEAD"], path)


def _write_check(cwd, rel_path):
    full = os.path.join(cwd, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with io.open(full, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n# a stub check\n")
    return rel_path


def _write_receipt(cwd, rel_path, covers=("seed.txt",), argv=None):
    """A receipt that genuinely satisfies `evidence.verify()`, built through
    evidence.py's own `generate()`/`write_receipt()` writer and sealer
    rather than hand-typed JSON.

    F-2b: this fixture used to hand-type a plausible-looking dict
    (`schemaVersion`, `generator`, `headCommit`, `exitCode` and nothing
    else), which satisfied `evidence.load()` (JSON-parse plus stat) and, by
    extension, the old `proof_verdict`, while `evidence.verify()` over the
    identical file returned FAIL. `covers` is passed explicitly rather than
    derived from a diff, so an honest, PASS-worthy receipt can be minted in
    a single-commit fixture repository with no base commit to diff against:
    `seed.txt`, written by `_git_repo`, is present and unchanged, so it
    hashes and stats clean.

    `argv`, when given, replaces the default `python -c pass`: the caller
    that wants a receipt BOUND to a real check (`_receipt_names_check`,
    proofplan.py) passes the command that actually runs it; the default
    stays the unrelated no-op, which is exactly what the bypass fixture
    below needs to demonstrate what `evidence.verify()` alone does not
    catch.
    """
    receipt = evidence_mod.generate(cwd, list(argv) if argv else [sys.executable, "-c", "pass"],
                                    covers=list(covers))
    full = os.path.join(cwd, rel_path)
    evidence_mod.write_receipt(receipt, full)
    return rel_path


class TestProofVerdict(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-proofplan-")
        self.head = _git_repo(self.tmp)

    # Case 4: a behavior with no proof surfaces as NO-DATA.
    def test_no_proof_at_all_is_no_data(self):
        entry = {"behavior": "the widget renders"}
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn(verdict, proofplan.PROOF_VERDICTS)
        self.assertTrue(problems, "a NO-DATA verdict must name at least one problem")
        self.assertIn("names no proof", evidence)

    def test_empty_proof_dict_is_also_no_data(self):
        entry = {"behavior": "the widget renders", "proof": {}}
        verdict, _evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(problems)

    # Case 5: proof points to a check file that does not exist on disk.
    def test_proof_points_to_nonexistent_check(self):
        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": "tools/test_does_not_exist_at_all.py",
                      "evidenceReceipt": "evidence/whatever.json"},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("does not exist", evidence)
        self.assertTrue(problems)

    # Case 6: the named check file exists, but nothing shows it ever ran.
    def test_check_exists_but_never_ran(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check.py")
        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("not a check that RAN", evidence)
        self.assertTrue(problems)

    def test_check_exists_but_receipt_path_unreadable(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_2.py")
        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path,
                      "evidenceReceipt": "evidence/nothing-here.json"},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("could not be read", evidence)
        self.assertTrue(problems)

    # Case 8: the receipt is real and readable, but it was recorded against
    # a head this tree has since moved past.
    def test_stale_proof_against_an_old_head(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_3.py")
        receipt_path = _write_receipt(self.tmp, "evidence/stale.json")
        # Move the tree on: a second commit changes HEAD out from under the
        # receipt that was minted against the first one.
        with io.open(os.path.join(self.tmp, "seed.txt"), "a", encoding="utf-8") as fh:
            fh.write("more\n")
        _run_git(["add", "-A"], self.tmp)
        _run_git(["commit", "-qm", "second"], self.tmp)

        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": receipt_path},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("STALE", evidence)
        self.assertTrue(problems)

    # The passing arm: check exists, receipt exists, readable, and bound to
    # the CURRENT head -- AND, F-3 below, the receipt's own recorded run
    # actually invokes the named check. Proven here alongside the failure
    # cases so a future change that made everything degrade to NO-DATA would
    # also be caught.
    def test_check_identified_when_everything_lines_up(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_4.py")
        # Committed, not merely written: `evidence.generate()` records the
        # real working-tree state, and `evidence.verify()` (now required for
        # CHECK_IDENTIFIED, see F-2b) reads that recorded state rather than
        # re-checking git itself, so a dirty tree at mint time would make
        # this receipt genuinely NO-DATA rather than a passing fixture.
        _run_git(["add", "-A"], self.tmp)
        _run_git(["commit", "-qm", "add the stub check"], self.tmp)
        # F-3: the receipt's argv ACTUALLY RUNS check_path, not an unrelated
        # no-op. A receipt bound to a check by a stray `--covers` or by
        # running something else entirely must never read as CHECK_IDENTIFIED
        # (see test_receipt_not_naming_the_check_is_no_data_not_check_identified
        # below), so the honest passing fixture has to earn the verdict the
        # same way a real run would.
        receipt_path = _write_receipt(self.tmp, "evidence/fresh.json",
                                      argv=[sys.executable, check_path])
        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": receipt_path},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "CHECK_IDENTIFIED", (verdict, evidence, problems))
        self.assertEqual(problems, ())
        self.assertIn(check_path, evidence)

    # F-3, THE BAND F BLOCKER: a receipt that is genuine, sealed, PASSes
    # `evidence.verify()`, and is bound to the current head, but whose OWN
    # RECORDED RUN never touched the named check (minted from `python -c
    # pass`, covering only `seed.txt`, attached here to a DIFFERENT
    # committed check file) used to satisfy CHECK_IDENTIFIED anyway: nothing
    # compared the receipt's argv or coveredFiles against the checkPath the
    # entry declares. Mandatory failure case 6 restated one layer deeper:
    # "the check exists but never ran" is not just "no receipt is named",
    # it is also "a receipt is named and it is real, but it is not evidence
    # of THIS check running".
    def test_receipt_not_naming_the_check_is_no_data_not_check_identified(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_unbound.py")
        _run_git(["add", "-A"], self.tmp)
        _run_git(["commit", "-qm", "add the stub check"], self.tmp)
        # Genuine receipt, minted through evidence.py's own writer and
        # sealer, verify()-PASSing and current-head-bound -- but its argv is
        # `python -c pass` and it covers only seed.txt, never check_path.
        receipt_path = _write_receipt(self.tmp, "evidence/unbound.json")
        verify_result = evidence_mod.verify(os.path.join(self.tmp, receipt_path), cwd=self.tmp)
        self.assertEqual(verify_result["verdict"], "PASS", verify_result)

        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": receipt_path},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA", (verdict, evidence, problems))
        self.assertIn(check_path, evidence)
        self.assertIn("never names this check", evidence)
        self.assertTrue(problems)

    # F-2b: a one-key hand-typed receipt (just `headCommit`) used to satisfy
    # every check the old `proof_verdict` ran itself (file exists, JSON
    # parses, head matches) while `evidence.verify()` over the identical
    # file returned FAIL. Requiring `verify()` to PASS closes that gap.
    def test_forged_one_key_receipt_is_no_data_not_a_pass(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_forged.py")
        current_head = _run_git(["rev-parse", "HEAD"], self.tmp)
        receipt_rel = os.path.join("evidence", "forged.json")
        full_receipt = os.path.join(self.tmp, receipt_rel)
        os.makedirs(os.path.dirname(full_receipt), exist_ok=True)
        with io.open(full_receipt, "w", encoding="utf-8") as fh:
            json.dump({"headCommit": current_head}, fh)

        # The gap the finding names, demonstrated directly: evidence.py's
        # own verification of this exact file is FAIL, on its own terms.
        verify_result = evidence_mod.verify(full_receipt, cwd=self.tmp)
        self.assertEqual(verify_result["verdict"], "FAIL")
        self.assertIn("schemaVersion", " ".join(verify_result["reasons"]))

        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": receipt_rel},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA", (verdict, evidence, problems))
        self.assertIn("evidence.verify()", evidence)
        self.assertTrue(problems)

    def test_human_judgment_required_when_a_human_is_named(self):
        entry = {"behavior": "is this UI actually pleasant to use",
                 "proof": {"kind": "human", "human": "Khalil Maaouni"}}
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "HUMAN_JUDGMENT_REQUIRED")
        self.assertEqual(problems, ())
        self.assertIn("Khalil Maaouni", evidence)

    def test_human_proof_naming_no_human_is_no_data_not_a_pass(self):
        entry = {"behavior": "is this UI actually pleasant to use",
                 "proof": {"kind": "human", "human": ""}}
        verdict, _evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(problems)

    def test_unknown_proof_kind_is_no_data(self):
        entry = {"behavior": "the export never drops a row",
                 "proof": {"kind": "vibes"}}
        verdict, _evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertTrue(problems)

    # F-2a: a typed-wrong checkPath or evidenceReceipt (an int, a list) used
    # to crash `os.path.isabs()` with a TypeError, which propagated out of
    # `proof_verdict` entirely rather than surfacing as this entry's own
    # NO-DATA row.
    def test_int_checkpath_is_no_data_not_a_crash(self):
        entry = {"behavior": "the typed-wrong row", "proof": {"kind": "check", "checkPath": 7}}
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("int", evidence)
        self.assertTrue(problems)

    def test_list_checkpath_is_also_no_data_not_a_crash(self):
        entry = {"behavior": "the typed-wrong row",
                 "proof": {"kind": "check", "checkPath": ["a", "b"]}}
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("list", evidence)
        self.assertTrue(problems)

    def test_int_evidencereceipt_is_also_no_data_not_a_crash(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_typed_wrong.py")
        entry = {"behavior": "the typed-wrong row",
                 "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": 7}}
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("int", evidence)
        self.assertTrue(problems)

    def test_every_verdict_is_a_known_member(self):
        cases = [
            {"behavior": "a"},
            {"behavior": "b", "proof": {"kind": "human", "human": "Someone"}},
            {"behavior": "c", "proof": {"kind": "check", "checkPath": "nope.py"}},
        ]
        for entry in cases:
            verdict, _e, _p = proofplan.proof_verdict(entry, self.tmp)
            self.assertIn(verdict, proofplan.PROOF_VERDICTS)


class TestEvaluatePlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-proofplan-plan-")
        self.head = _git_repo(self.tmp)

    def test_empty_plan_is_its_own_arm_never_all_covered(self):
        rows, empty_note = proofplan.evaluate_plan([], self.tmp)
        self.assertEqual(rows, ())
        self.assertIsNotNone(empty_note, "an empty plan must carry a named note, never a "
                             "silent (), which is the shape that lets a caller's all() over "
                             "zero rows read as \"everything covered\"")
        self.assertIn("zero behaviors", empty_note)

    def test_non_list_plan_is_also_the_empty_arm(self):
        rows, empty_note = proofplan.evaluate_plan(None, self.tmp)
        self.assertEqual(rows, ())
        self.assertIsNotNone(empty_note)

    def test_nonempty_plan_carries_one_row_per_entry_and_no_empty_note(self):
        entries = [
            {"behavior": "a", "proof": {"kind": "human", "human": "Someone"}},
            {"behavior": "b"},
        ]
        rows, empty_note = proofplan.evaluate_plan(entries, self.tmp)
        self.assertIsNone(empty_note)
        self.assertEqual(len(rows), 2)
        behaviors = [row[0] for row in rows]
        self.assertEqual(behaviors, ["a", "b"])
        verdicts = [row[1] for row in rows]
        self.assertEqual(verdicts, ["HUMAN_JUDGMENT_REQUIRED", "NO-DATA"])

    # F-2a: `evaluate_plan` builds every row in one `tuple(...)` generator,
    # so a `proof_verdict` call that RAISES for one entry used to destroy
    # every row, not just the bad one. This is the exact repro named in the
    # finding: one well-formed row plus one typed-wrong row must return two
    # rows, the second NO-DATA, no exception, other rows unaffected.
    def test_typed_wrong_checkpath_row_is_its_own_no_data_row(self):
        entries = [
            {"behavior": "a well formed row", "proof": {"kind": "human", "human": "Someone"}},
            {"behavior": "the typed-wrong row", "proof": {"kind": "check", "checkPath": 7}},
        ]
        rows, empty_note = proofplan.evaluate_plan(entries, self.tmp)
        self.assertIsNone(empty_note)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "HUMAN_JUDGMENT_REQUIRED")
        self.assertEqual(rows[1][1], "NO-DATA")

    def test_typed_wrong_checkpath_as_a_list_is_also_its_own_no_data_row(self):
        entries = [
            {"behavior": "a well formed row", "proof": {"kind": "human", "human": "Someone"}},
            {"behavior": "the typed-wrong row",
             "proof": {"kind": "check", "checkPath": ["x", "y"]}},
        ]
        rows, empty_note = proofplan.evaluate_plan(entries, self.tmp)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "HUMAN_JUDGMENT_REQUIRED")
        self.assertEqual(rows[1][1], "NO-DATA")

    def test_typed_wrong_evidencereceipt_is_also_its_own_no_data_row(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_plan_typed_wrong.py")
        entries = [
            {"behavior": "a well formed row", "proof": {"kind": "human", "human": "Someone"}},
            {"behavior": "the typed-wrong row",
             "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": 7}},
        ]
        rows, empty_note = proofplan.evaluate_plan(entries, self.tmp)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "HUMAN_JUDGMENT_REQUIRED")
        self.assertEqual(rows[1][1], "NO-DATA")


class TestIntegratedHostileReviewFindingB(unittest.TestCase):
    """Finding B, CRITICAL, of the integrated hostile review: `proof_verdict`
    returned CHECK_IDENTIFIED for a behavior whose named check RAN AND
    FAILED. Measured against the module as it stood: a receipt with
    `exitCode: 0` for an all-skipped check and a receipt with `exitCode: 1`
    for a genuinely failing check both reached CHECK_IDENTIFIED, because
    nothing here ever read the field. Fixed by gating CHECK_IDENTIFIED on
    `exitCode == 0`; a nonzero or missing `exitCode` now degrades to
    NO-DATA, naming the failing (or unrecorded) run, the same way a stale or
    unverifiable receipt already does.

    Case 1 below is the finding's own repro, minted for real: a genuinely
    failing check, run for real through `evidence.py`'s own writer, whose
    receipt still PASSes `evidence.verify()` (a failed run is not a forged
    receipt). Case 2 covers the finding's other named fixture, "exitCode
    absent": `evidence.verify()` already refuses a receipt with no recorded
    `exitCode` under the CURRENT schema (`exitCode` is a `REQUIRED_FIELDS`
    member), so that fixture cannot be minted honestly through today's
    writer; it is built directly and `evidence.verify()` is mocked to PASS
    regardless, proving `proof_verdict`'s OWN gate refuses the absence even
    when nothing upstream already would have. Case 3 is the calibration: an
    honest, genuinely PASSing run still reaches CHECK_IDENTIFIED, so this
    fix is a gate, never a new blanket refusal.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-proofplan-exitcode-")
        self.head = _git_repo(self.tmp)

    # 1, THE FINDING, RED THEN GREEN: a genuinely FAILING run, minted for
    # real, must never reach CHECK_IDENTIFIED -----------------------------
    def test_a_receipt_for_a_check_that_ran_and_failed_is_no_data_not_check_identified(self):
        check_path = "tools/test_stub_failing_check.py"
        full_check = os.path.join(self.tmp, check_path)
        os.makedirs(os.path.dirname(full_check), exist_ok=True)
        with io.open(full_check, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
        _run_git(["add", "-A"], self.tmp)
        _run_git(["commit", "-qm", "add a failing stub check"], self.tmp)

        receipt_path = _write_receipt(self.tmp, "evidence/failing.json",
                                      argv=[sys.executable, check_path])
        verify_result = evidence_mod.verify(os.path.join(self.tmp, receipt_path), cwd=self.tmp)
        self.assertEqual(verify_result["verdict"], "PASS", verify_result)
        loaded = evidence_mod.load(os.path.join(self.tmp, receipt_path))
        self.assertEqual(loaded["exitCode"], 1, loaded)

        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": receipt_path},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA", (verdict, evidence, problems))
        self.assertIn("FAILED", evidence)
        self.assertIn("1", evidence)
        self.assertTrue(problems)

    # 2, THE FINDING'S OTHER NAMED FIXTURE: no exitCode recorded at all is
    # also NO-DATA, absence is not success -------------------------------
    def test_a_receipt_recording_no_exitcode_at_all_is_no_data_not_check_identified(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_no_exitcode.py")
        _run_git(["add", "-A"], self.tmp)
        _run_git(["commit", "-qm", "add the stub check"], self.tmp)
        current_head = _run_git(["rev-parse", "HEAD"], self.tmp)

        receipt = {
            "headCommit": current_head,
            "argv": [sys.executable, check_path],
            "coveredFiles": [],
            # exitCode deliberately absent: this is the exact document
            # `evidence.verify()` already refuses under the current schema
            # (REQUIRED_FIELDS), so verify() is mocked to PASS regardless,
            # proving this module's OWN gate, not evidence.py's.
        }
        with mock.patch.object(evidence_mod, "load", return_value=receipt), \
             mock.patch.object(evidence_mod, "verify",
                               return_value={"verdict": "PASS", "reasons": []}):
            entry = {
                "behavior": "the export never drops a row",
                "proof": {"kind": "check", "checkPath": check_path,
                          "evidenceReceipt": "evidence/never-actually-read.json"},
            }
            verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "NO-DATA", (verdict, evidence, problems))
        self.assertIn("exitCode", evidence)
        self.assertTrue(problems)

    # 3, CALIBRATION: an honest, genuinely PASSing run still reaches
    # CHECK_IDENTIFIED -- this is a gate, not a new blanket refusal --------
    def test_a_receipt_for_a_check_that_ran_and_passed_still_reaches_check_identified(self):
        check_path = _write_check(self.tmp, "tools/test_stub_check_ok.py")
        _run_git(["add", "-A"], self.tmp)
        _run_git(["commit", "-qm", "add the stub check"], self.tmp)
        receipt_path = _write_receipt(self.tmp, "evidence/ok.json",
                                      argv=[sys.executable, check_path])
        loaded = evidence_mod.load(os.path.join(self.tmp, receipt_path))
        self.assertEqual(loaded["exitCode"], 0, loaded)

        entry = {
            "behavior": "the export never drops a row",
            "proof": {"kind": "check", "checkPath": check_path, "evidenceReceipt": receipt_path},
        }
        verdict, evidence, problems = proofplan.proof_verdict(entry, self.tmp)
        self.assertEqual(verdict, "CHECK_IDENTIFIED", (verdict, evidence, problems))
        self.assertEqual(problems, ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
