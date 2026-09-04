#!/usr/bin/env python3
"""A3, owed checks: every check the behaviour table promised must appear in
the evidence.

THE DEFECT THIS CLOSES, as the adopting team reported it: evidence containing
one check passes exactly as green as evidence containing all of them. The
forward direction already existed (a verification plan citing a behaviour id
that no longer exists FAILs, in `tools/sbe_design.py`). The reverse direction,
a Proof column naming a check that no receipt records, did not.

Run: python3 tools/test_sbe_proof_gate.py

WHY THIS FILE AND NOT tools/test_sbe.py, which is where the design contract
put it: `tools/test_sbe.py` was owned by another session's open writer task
(`zero-network-allowlist`) for the whole of this work, and `sbe task open`
refused the claim. That session gave written permission to take the file and
offered to force-close its own claim, then reached the end of its context
before doing so. Force-closing a live peer's claim to place one test is the
move this project already has a finding about: every forced close mints a
decision package nothing declares. So the test lives beside the gate it
tests, in the same shape `tools/test_sbe_receipt_shapes.py` already uses for
gate work, and the contract's function name is preserved verbatim below so
the two can be matched up by anyone reading the contract.

Every test builds a real directory and runs the real `tools/sbe_gate.py proof`
against it. Nothing is mocked: the defect lives at the seam between what a
dossier promises and what a receipt records, and a mocked seam tests the mock.
"""
import collections
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "sbe_gate.py")

#: What one `proof` gate invocation produced. Named rather than a bare
#: `(code, line)` tuple: see `run_proof` for why a two-value return here is
#: read as a verdict pair by the honesty meta-test's lint.
ProofRun = collections.namedtuple("ProofRun", "code line")

HEADER = ("| ID | Starting point | Trigger | Required outcome | Proof |\n"
          "|---|---|---|---|---|\n")


def table(*rows):
    """A behaviour table with the exact header `_behaviour_rows` matches. Any
    other header is a table this gate does not read, which is a different test
    from the ones here."""
    return HEADER + "".join(rows)


def row(rid, proof):
    return "| %s | a placed order | the batch runs | the ledger balances | %s |\n" % (rid, proof)


class ProofGateFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rel, body):
        path = os.path.join(self.dir, rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def receipt(self, *names):
        return self.write("ran-receipt.json", json.dumps({"checks": [
            {"name": n, "exit_code": 0, "duration_ms": 812} for n in names]}))

    def run_proof(self, *flags):
        """A ProofRun carrying the exit code and the `proof` gate's own report
        LINE, not the whole report: every assertion here is about what this
        gate said, and asserting against the full report would let another
        gate's sentence satisfy a test about this one.

        A NAMED RESULT rather than a bare `(code, line)` pair. A two-value
        return under `tools/` is read as a possible (verdict, evidence) pair by
        the lint in `evals/test_no_data_class.py`, which then cannot prove the
        first element is never "PASS" and names the function. This helper's
        first version was a bare pair and that lint named it, alongside two
        others this session introduced.
        """
        proc = subprocess.run([sys.executable, GATE, "proof", self.dir] + list(flags),
                              capture_output=True, text=True)
        text = proc.stdout + proc.stderr
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "proof":
                return ProofRun(proc.returncode, line)
        return ProofRun(proc.returncode, "NO VERDICT LINE for 'proof' in:\n%s" % text)

    def verdict(self, line):
        parts = line.split()
        return parts[1] if len(parts) >= 2 and parts[0] == "proof" else line


class TestAProofMustBeBackedByAReceipt(ProofGateFixture):

    def test_a_proof_citing_a_check_no_receipt_ran_fails_by_row_id(self):
        """The contract's own named test. The row id comes FIRST in the
        message and the cited check follows in parentheses, because the person
        acting on this needs to find the row before they need the check."""
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
        self.receipt("other")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "FAIL", run.line)
        self.assertIn("B1 (reconcile)", run.line)
        self.assertIn("a claim, not a proof", run.line)
        self.assertEqual(run.code, 1, "a FAIL under --strict did not block: %s" % run.line)

    def test_a_cited_check_a_receipt_does_record_passes(self):
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
        self.receipt("reconcile")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "PASS", run.line)
        self.assertEqual(run.code, 0, run.line)

    def test_the_pass_sentence_admits_it_matched_names_rather_than_runs(self):
        """The ceiling, asserted rather than left in a docstring. A receipt
        entry naming a check satisfies the row whether or not anything ran, so
        a PASS that read as coverage would be this project's own headline
        defect class wearing a new gate's clothes."""
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
        self.receipt("reconcile")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "PASS", run.line)
        self.assertIn("matches NAMES, not runs", run.line)

    def test_a_hand_written_receipt_entry_satisfies_the_row_which_is_the_ceiling(self):
        """The ceiling DEMONSTRATED, not merely described: this receipt was
        typed here, nothing ran, and the gate is green. A future reader who
        tries to make this gate mean more than naming will find this test
        failing and will know exactly which promise they changed."""
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
        self.write("ran-receipt.json", json.dumps({"checks": [
            {"name": "reconcile", "exit_code": 0, "duration_ms": 1}]}))
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "PASS",
                         "the ceiling moved: a hand-written entry no longer satisfies a cited "
                         "row, which is an improvement that must be described where the ceiling "
                         "is documented rather than discovered here: %s" % run.line)
        self.assertEqual(run.code, 0, run.line)

    def test_every_missing_row_is_named_not_only_the_first(self):
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`"),
                                            row("B4", "run `settle-drill`")))
        self.receipt("other")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "FAIL", run.line)
        self.assertIn("B1 (reconcile)", run.line)
        self.assertIn("B4 (settle-drill)", run.line)
        self.assertIn("2 behaviour row(s)", run.line)


class TestProseProofsReportRatherThanFail(ProofGateFixture):
    """NOT A SOFTENING, A CORRECTNESS REQUIREMENT. The shipped template's own
    Proof cells are prose, so a gate that failed a Proof naming no backticked
    check would fail every existing dossier on the day it shipped. That is not
    a control, it is an outage."""

    def test_a_table_whose_proofs_are_all_prose_is_no_data_never_fail(self):
        self.write("08-behaviour.md", table(row(
            "B1", "Integration test: submit checkout, assert the order row exists")))
        self.receipt("other")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "NO-DATA",
                         "a dossier whose Proofs are prose was given a verdict; prose names a "
                         "check to a person and nothing to this gate: %s" % run.line)
        self.assertEqual(run.code, 0, "NO-DATA blocked a strict run, which it must never do: %s" % run.line)

    def test_the_shipped_template_itself_does_not_fail_this_gate(self):
        """The regression that matters most for adoption, run against the REAL
        shipped file rather than a copy of it, because a copy drifts and the
        whole point is that the file teams start from stays green."""
        shipped = os.path.join(os.path.dirname(HERE), "templates", "dossier", "08-behaviour.md")
        self.assertTrue(os.path.exists(shipped), "the shipped template moved: %s" % shipped)
        with io.open(shipped, encoding="utf-8") as fh:
            self.write("08-behaviour.md", fh.read())
        run = self.run_proof("--strict")
        self.assertNotEqual(self.verdict(run.line), "FAIL",
                            "the template every adopting team starts from now FAILs this gate, "
                            "so the gate's first act on any new estate is to block it: %s" % run.line)
        self.assertEqual(run.code, 0, run.line)

    def test_a_prose_row_beside_a_backticked_row_is_reported_and_not_owed(self):
        """The mixed table, which is what a team looks like halfway through
        adopting backticks. The backticked row is owed; the prose row is not,
        and the sentence says so rather than staying silent about it."""
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`"),
                                            row("B2", "Manual check: ask the warehouse")))
        self.receipt("reconcile")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "PASS", run.line)
        self.assertIn("1 row(s) state their Proof as prose", run.line)
        self.assertIn("B2", run.line)


class TestTheThreeAbsenceStatesStayApart(ProofGateFixture):
    """An absent table, an unreadable one and an empty Proof cell are three
    different findings. Collapsing any two is how a gate ends up reporting an
    absence as a pass, or a broken claim as an absence."""

    def test_no_table_at_all_is_no_data(self):
        self.receipt("reconcile")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "NO-DATA", run.line)
        self.assertIn("no 08-behaviour.md", run.line)
        self.assertEqual(run.code, 0, run.line)

    def test_a_table_that_exists_and_cannot_be_read_is_fail_not_no_data(self):
        """A broken claim, not an absence. Skipped rather than silently passed
        where the filesystem cannot express it: running as root, or on a mount
        that ignores the mode bit, makes the file readable anyway, and a test
        that quietly proves nothing is worse than one that says it was skipped.
        """
        path = self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
        os.chmod(path, 0o000)
        try:
            if os.access(path, os.R_OK):
                self.skipTest("this filesystem or user ignores the mode bit, so an unreadable "
                              "file cannot be created here and this case cannot be exercised")
            run = self.run_proof("--strict")
            self.assertEqual(self.verdict(run.line), "FAIL",
                             "a table that exists and cannot be read was reported as an absence, "
                             "so a chmod hides an obligation: %s" % run.line)
            self.assertIn("broken claim, not an absent one", run.line)
            self.assertEqual(run.code, 1, run.line)
        finally:
            os.chmod(path, 0o644)

    def test_a_row_with_an_empty_proof_cell_fails_by_row_id(self):
        self.write("08-behaviour.md", table(row("B7", "")))
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "FAIL", run.line)
        self.assertIn("B7", run.line)
        self.assertIn("no Proof", run.line)
        self.assertEqual(run.code, 1, run.line)

    def test_a_placeholder_proof_is_not_a_proof(self):
        """`answered()` rather than a truth test, matching what
        `check_behaviour` already does to the same column: "TBD" in a Proof
        cell is not a proof any more than it is a source."""
        self.write("08-behaviour.md", table(row("B8", "TBD")))
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "FAIL",
                         "a placeholder was accepted as a Proof: %s" % run.line)
        self.assertIn("B8", run.line)

    def test_a_malformed_row_is_fail_rather_than_a_row_nobody_saw(self):
        self.write("08-behaviour.md", HEADER + "| B1 | only | three |\n")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "FAIL",
                         "a row this gate could not parse was dropped in silence: %s" % run.line)
        self.assertIn("could not be read", run.line)


class TestFoundByTheHonestyMetaTest(ProofGateFixture):
    """Both defects below were in the first version of this gate and were found
    by `evals/test_no_data_class.py`, not by the tests above it. They are kept
    here as named regressions because both are shapes this project has already
    closed once elsewhere, which is exactly why they came back."""

    def test_a_name_on_an_unsound_entry_does_not_satisfy_a_cited_row(self):
        """24 meta-test scenarios, one shape: the first version read only
        `name`, so a row citing `reconcile` was satisfied by an entry whose
        exit code was the string "TODO" and whose duration was Infinity. The
        proof gate printed PASS over a receipt `gate_ran` FAILs on the same
        run, which is two gates disagreeing about one receipt."""
        for label, entry in (
                ("a placeholder exit code", {"name": "reconcile", "exit_code": "TODO",
                                             "duration_ms": 812}),
                ("no exit code at all", {"name": "reconcile", "duration_ms": 812}),
                ("a nonzero exit", {"name": "reconcile", "exit_code": 1, "duration_ms": 812}),
                ("a zero duration", {"name": "reconcile", "exit_code": 0, "duration_ms": 0}),
                ("a boolean masquerading as a duration",
                 {"name": "reconcile", "exit_code": 0, "duration_ms": True}),
        ):
            with self.subTest(entry=label):
                self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
                self.write("ran-receipt.json", json.dumps({"checks": [entry]}))
                run = self.run_proof("--strict")
                self.assertEqual(self.verdict(run.line), "FAIL",
                                 "a cited row was satisfied by an entry with %s, so a name on a "
                                 "broken entry counts as a run: %s" % (label, run.line))
                self.assertIn("B1 (reconcile)", run.line)

    def test_a_fifo_where_the_table_belongs_reaches_a_verdict_rather_than_hanging(self):
        """The first version wrapped `open()` in a try/except, which does not
        help: `open()` on a FIFO BLOCKS, so the gate hung for the meta-test's
        full 90 seconds and printed no verdict in either mode. A check that
        vanishes is worse than one that fails. `evidence_problem` already
        refused this by its stat and was not being asked."""
        if not hasattr(os, "mkfifo"):
            self.skipTest("this platform has no mkfifo, so the hanging shape cannot be built")
        os.mkfifo(os.path.join(self.dir, "08-behaviour.md"))
        proc = subprocess.run([sys.executable, GATE, "proof", self.dir, "--strict"],
                              capture_output=True, text=True, timeout=60)
        text = proc.stdout + proc.stderr
        line = next((ln for ln in text.splitlines()
                     if ln.split()[:1] == ["proof"]), "NO VERDICT LINE in:\n%s" % text)
        self.assertEqual(self.verdict(line), "FAIL",
                         "a FIFO where the behaviour table belongs did not produce a FAIL: %s"
                         % line)
        self.assertIn("broken claim, not an absent one", line)


class TestTheGateReadsDeclaredStructureNotProse(ProofGateFixture):
    """The rule that a check is matched against a DECLARED structure and never
    against a word appearing in text. Six controls in this estate already fire
    on prose that merely mentions their subject."""

    def test_a_check_name_appearing_as_bare_prose_is_not_read_as_a_citation(self):
        """`reconcile` unbackticked is a word in a sentence. If this gate read
        it as a citation, a Proof could be satisfied, or an obligation
        invented, by ordinary English."""
        self.write("08-behaviour.md", table(row("B1", "we reconcile the ledger by hand")))
        self.receipt("reconcile")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "NO-DATA",
                         "a bare word in prose was read as a declared check citation: %s" % run.line)

    def test_a_receipt_naming_a_check_in_prose_does_not_satisfy_a_citation(self):
        """The mirror direction: the receipt records a check called
        'we ran reconcile today', which CONTAINS the cited name. A substring
        matcher would call that satisfied."""
        self.write("08-behaviour.md", table(row("B1", "run `reconcile`")))
        self.receipt("we ran reconcile today")
        run = self.run_proof("--strict")
        self.assertEqual(self.verdict(run.line), "FAIL",
                         "a receipt whose check name merely CONTAINS the cited name satisfied "
                         "the citation, so this gate is a substring matcher: %s" % run.line)
        self.assertIn("B1 (reconcile)", run.line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
