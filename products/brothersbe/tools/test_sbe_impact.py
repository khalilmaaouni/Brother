#!/usr/bin/env python3
"""Fixtures for `sbe impact`. Run: python3 tools/test_sbe_impact.py

Every test here builds a real git repository in a temporary directory and runs
the real command against it. Nothing is mocked, because the defect this control
exists for lives exactly at the seam between a diff and a claim, and a mocked
diff would test the mock.

The fixture set follows the shape the improvement brief asks for: the defect,
the sound case, hollow evidence, malformed evidence, a deletion bypass, a
staleness case, and a regression assertion that the control never does the one
thing it must never do.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class ImpactFixture(unittest.TestCase):
    """A fresh repository per test, with one base commit already in it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "README.md", "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def commit(self, message="change"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def intake(self, **answers):
        """Write an intake declaring a tier through its five answers."""
        base = {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n", "consumers": "none"}
        base.update(answers)
        return write(self.repo, "00-intake.json", json.dumps({"answers": base}))

    def run_impact(self, *extra):
        argv = [sys.executable, SBE, "impact", self.repo, "--base", self.base, "--json"]
        out = subprocess.run(argv + list(extra), capture_output=True, text=True)
        data = json.loads(out.stdout) if out.stdout.strip().startswith("{") else None
        return out.returncode, data, out.stdout + out.stderr


class TestTheDefect(ImpactFixture):
    def test_a_contract_change_declared_T0_cannot_pass(self):
        """THE DEFECT THIS CONTROL EXISTS FOR. Five 'no' answers make an API
        contract rewrite a T0, and every gate downstream then agrees the change
        owes no evidence at all."""
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\npaths:\n  /orders: {}\n")
        self.commit()
        intake = self.intake()  # every answer no: T0
        code, data, text = self.run_impact("--intake", intake)
        self.assertEqual(data["humanTier"], "T0", text)
        self.assertEqual(data["proposedTier"], "T2", text)
        self.assertEqual(data["verdict"], "REVIEW-REQUIRED", text)
        self.assertEqual(code, 1, "a declared tier contradicted by the diff must not exit 0")
        self.assertTrue([d for d in data["disagreements"] if d["detector"] == "openapi"], text)

    def test_a_destructive_migration_declared_reversible_cannot_pass(self):
        write(self.repo, "migrations/003_drop.sql", "DROP TABLE orders;\n")
        self.commit()
        intake = self.intake(reversible_under_hour="y")
        code, data, text = self.run_impact("--intake", intake)
        self.assertEqual(data["proposedTier"], "T3", text)
        self.assertEqual(code, 1, text)

    def test_a_money_path_declared_insensitive_cannot_pass(self):
        write(self.repo, "src/billing/refund.py", "def refund():\n    pass\n")
        self.commit()
        intake = self.intake(touches_sensitive="n")
        code, data, text = self.run_impact("--intake", intake)
        self.assertEqual(data["proposedTier"], "T3", text)
        self.assertEqual(code, 1, text)


class TestTheSoundCase(ImpactFixture):
    def test_a_documentation_change_stays_quiet(self):
        """The control has to be silent on safe work or engineers switch it off,
        which is the failure mode that costs the most and is the hardest to see."""
        write(self.repo, "README.md", "base\nmore words\n")
        self.commit()
        intake = self.intake()
        code, data, text = self.run_impact("--intake", intake)
        self.assertEqual(data["proposedTier"], "T0", text)
        self.assertEqual(data["disagreements"], [], text)
        self.assertEqual(data["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)

    def test_a_contract_change_correctly_declared_passes(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        self.commit()
        intake = self.intake(changes_contract="y")
        code, data, text = self.run_impact("--intake", intake)
        self.assertEqual(data["humanTier"], "T2", text)
        self.assertEqual(data["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)


class TestHollowAndMalformed(ImpactFixture):
    def test_an_empty_diff_is_no_data_and_never_a_pass(self):
        code, data, text = self.run_impact()
        self.assertEqual(data["verdict"], "NO-DATA", text)
        self.assertIn("consumers", json.dumps(data["unmeasured"]), text)

    def test_a_malformed_intake_fails_rather_than_reading_as_absent(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        write(self.repo, "00-intake.json", "{not json at all")
        self.commit()
        code, data, text = self.run_impact("--intake", os.path.join(self.repo,
                                                                    "00-intake.json"))
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertEqual(code, 1, text)

    def test_an_unreadable_intake_answer_fails(self):
        """The intake vocabulary refuses values it cannot read. An answer of
        'probably' must not be guessed at in either direction."""
        write(self.repo, "00-intake.json", json.dumps({"answers": {
            "changes_contract": "probably", "crosses_boundary": "n",
            "reversible_under_hour": "y", "touches_sensitive": "n", "consumers": "none"}}))
        self.commit()
        code, data, text = self.run_impact("--intake", os.path.join(self.repo,
                                                                    "00-intake.json"))
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertEqual(code, 1, text)

    def test_a_file_type_with_no_detector_is_unmeasured_not_clean(self):
        write(self.repo, "service/handler.rs", "fn main() {}\n")
        self.commit()
        code, data, text = self.run_impact("--intake", self.intake())
        measured = [u["file"] for u in data["unmeasured"] if u["file"]]
        self.assertIn("service/handler.rs", measured,
                      "a language nothing here reads must be reported as unread, never "
                      "folded into a clean result")


class TestBypasses(ImpactFixture):
    def test_deleting_the_intake_does_not_produce_a_pass(self):
        """The cheapest bypass of any file-reading control: remove the file it
        reads. Absence must not become agreement."""
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        self.commit()
        code, data, text = self.run_impact("--intake",
                                           os.path.join(self.repo, "00-intake.json"))
        self.assertIsNone(data["humanTier"], text)
        self.assertNotEqual(data["verdict"], "PASS",
                            "a missing intake must not read as agreement with the diff")
        strict = subprocess.run([sys.executable, SBE, "impact", self.repo, "--base", self.base,
                                 "--strict"], capture_output=True, text=True)
        self.assertEqual(strict.returncode, 1,
                         "under --strict a NO-DATA must block, or protected CI is protecting "
                         "nothing")

    def test_a_disposition_from_another_commit_resolves_nothing(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        head = self.commit()
        disp = write(self.repo, "disposition.json", json.dumps([{
            "detector": "openapi", "decision": "keep-lower", "reason": "it is internal only",
            "who": "someone", "head": "0" * 40}]))
        code, data, text = self.run_impact("--intake", self.intake(), "--disposition", disp)
        self.assertEqual(data["verdict"], "REVIEW-REQUIRED",
                         "a disposition decided against a different diff must not resolve "
                         "this one")
        self.assertIn("different head commit", json.dumps(data["unmeasured"]), text)
        self.assertNotEqual(head, "0" * 40)

    def test_a_current_disposition_resolves_the_disagreement_and_stays_visible(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        head = self.commit()
        disp = write(self.repo, "disposition.json", json.dumps([{
            "detector": "openapi", "decision": "keep-lower",
            "reason": "the file is a fixture, not a published contract",
            "who": "the engineer", "head": head}]))
        code, data, text = self.run_impact("--intake", self.intake(), "--disposition", disp)
        self.assertEqual(code, 0, text)
        self.assertTrue(data["disagreements"], "the disagreement must remain visible in the "
                                               "report even once it is dispositioned")
        self.assertEqual(data["disagreements"][0]["disposition"], "recorded", text)

    def test_a_disposition_missing_its_reason_or_author_resolves_nothing(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        head = self.commit()
        disp = write(self.repo, "disposition.json", json.dumps([
            {"detector": "openapi", "decision": "keep-lower", "reason": "", "who": "x",
             "head": head}]))
        code, data, text = self.run_impact("--intake", self.intake(), "--disposition", disp)
        self.assertEqual(code, 1, "an unreasoned disposition is an off switch, not a decision")

    def test_a_removed_payment_line_does_not_classify_as_touching_money(self):
        """Content detectors read ADDED lines only. Deleting a line that
        mentions a card number is not a change that touches money, and a
        detector reading the whole diff would say it was."""
        write(self.repo, "src/checkout.py", "card_number = None\nx = 1\n")
        self.commit("add")
        base_two = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "src/checkout.py", "x = 1\n")
        self.commit("remove the field")
        out = subprocess.run([sys.executable, SBE, "impact", self.repo, "--base", base_two,
                              "--json"], capture_output=True, text=True)
        data = json.loads(out.stdout)
        self.assertEqual([h for h in data["detected"] if h["detector"] == "pii-field"], [],
                         "a deletion was classified as adding personal data")


class TestStrictOverAbsence(ImpactFixture):
    """--strict grades what the tool holds, never what is merely absent. The
    defect this class pins: every docs-or-data pull request went red in the
    consumer workflow, because a NO-DATA whose derived answers were all at
    their lowest values still exited 1 under --strict. NO-DATA never decides
    an exit code; what blocks is undeclared evidence or a broken read."""

    def strict(self, *extra):
        out = subprocess.run([sys.executable, SBE, "impact", self.repo, "--base",
                              self.base, "--json", "--strict"] + list(extra),
                             capture_output=True, text=True)
        data = json.loads(out.stdout) if out.stdout.strip().startswith("{") else None
        return out.returncode, data, out.stdout + out.stderr

    def test_a_docs_and_data_only_diff_exits_zero_under_strict(self):
        write(self.repo, "README.md", "base\nmore words\n")
        write(self.repo, "notes.yaml", "key: value\n")
        write(self.repo, "page.html", "<p>hello</p>\n")
        write(self.repo, "tests/test_page.py", "def test_page():\n    pass\n")
        self.commit()
        code, data, text = self.strict()
        self.assertEqual(data["verdict"], "NO-DATA", text)
        self.assertEqual(data["detected"], [], text)
        self.assertEqual(data["proposedTier"], "T0", text)
        self.assertEqual(code, 0,
                         "a NO-DATA whose derived answers are all at their lowest values "
                         "must not fail a --strict run; absence is reported, never graded")

    def test_an_empty_diff_exits_zero_under_strict(self):
        code, data, text = self.strict()
        self.assertEqual(data["verdict"], "NO-DATA", text)
        self.assertEqual(code, 0,
                        "an empty diff proposes nothing; failing it under --strict "
                        "manufactures a failure out of absence")

    def test_a_detector_hit_with_no_intake_still_blocks_under_strict(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        self.commit()
        code, data, text = self.strict()
        self.assertEqual(data["verdict"], "NO-DATA", text)
        self.assertEqual(data["proposedTier"], "T2", text)
        self.assertEqual(code, 1,
                         "a tier-raising diff nobody declared must still block a --strict "
                         "run; the exemption is for absence, not for evidence")

    def test_an_unreadable_diff_still_blocks_under_strict(self):
        not_git = tempfile.mkdtemp()
        try:
            out = subprocess.run([sys.executable, SBE, "impact", not_git, "--strict"],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 1,
                             "a diff that could not be read is a broken control, not an "
                             "absence, and must keep blocking under --strict")
            self.assertIn("NO-DATA", out.stdout + out.stderr)
            relaxed = subprocess.run([sys.executable, SBE, "impact", not_git],
                                     capture_output=True, text=True)
            self.assertEqual(relaxed.returncode, 0, relaxed.stdout + relaxed.stderr)
        finally:
            shutil.rmtree(not_git, ignore_errors=True)


class TestTheInvariant(ImpactFixture):
    """One sentence, and the whole control is built to keep it true:
    this tool may raise a declared tier, and may never lower one."""

    def test_it_never_lowers_a_declared_tier(self):
        write(self.repo, "README.md", "base\nwords\n")
        self.commit()
        intake = self.intake(touches_sensitive="y")  # a human-declared T3
        code, data, text = self.run_impact("--intake", intake)
        self.assertEqual(data["humanTier"], "T3", text)
        self.assertEqual(data["proposedTier"], "T0",
                         "the diff genuinely shows nothing, which is the whole point of this "
                         "fixture")
        self.assertEqual(data["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)
        blob = json.dumps(data)
        self.assertNotIn('"tier": "T0"', blob)
        self.assertIn("floor, never a ceiling", blob,
                      "the report must say in its own words that the proposal cannot lower "
                      "anything")

    def test_the_tier_rule_is_imported_rather_than_reimplemented(self):
        """Two tier ladders would drift apart on the first change to either. The
        proposal is computed by running derived answers through the SAME
        compute_tier the human's answers go through, and this test fails if that
        import is ever replaced by a local copy."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import impact as mod
            import sbe_intake
            self.assertIs(mod.compute_tier, sbe_intake.compute_tier)
        finally:
            sys.path.pop(0)


class TestConsequenceDrivenProofBurden(ImpactFixture):
    """F2: proof burden is driven by CONSEQUENCE, never by how the diff is
    shaped. A tiny SQL change that also touches money raises it regardless
    of its size; a large refactor covered by an existing test suite lowers
    it. Neither the tier ladder nor `proposedTier` moves because of this --
    `consequence` is an added field, never a replacement for the tier."""

    def test_a_tiny_sql_change_touching_money_raises_the_burden(self):
        write(self.repo, "billing/adjust_balance.sql",
              "UPDATE payments SET amount = amount + 1;\n")
        self.commit()
        code, data, text = self.run_impact("--intake", self.intake())
        self.assertEqual(data["consequence"]["proofBurden"], "raised", text)
        self.assertTrue(data["consequence"]["moneyTouched"], text)
        self.assertLessEqual(data["consequence"]["diffLines"], 20, text)

    def test_a_large_refactor_covered_by_an_existing_suite_lowers_the_burden(self):
        for i in range(12):
            write(self.repo, "src/module_%d.py" % i,
                  "\n".join("def f_%d_%d(): return %d" % (i, j, j) for j in range(20)) + "\n")
        write(self.repo, "tests/test_module.py",
              "\n".join("def test_%d(): assert True" % j for j in range(20)) + "\n")
        self.commit()
        code, data, text = self.run_impact("--intake", self.intake())
        self.assertEqual(data["consequence"]["proofBurden"], "lowered", text)
        self.assertTrue(data["consequence"]["testsTouched"], text)
        self.assertGreaterEqual(data["consequence"]["diffLines"], 200, text)
        self.assertFalse(data["consequence"]["moneyTouched"], text)

    def test_neither_condition_alone_moves_anything(self):
        """A small, ordinary, non-money change stays `standard`, and a
        docs-only diff never inflates the burden either."""
        write(self.repo, "README.md", "base\nmore words\n")
        self.commit()
        code, data, text = self.run_impact("--intake", self.intake())
        self.assertEqual(data["consequence"]["proofBurden"], "standard", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
