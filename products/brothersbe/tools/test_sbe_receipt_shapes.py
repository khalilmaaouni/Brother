#!/usr/bin/env python3
"""The defect: `sbe evidence run` and `sbe gate ran` used to read two
different receipt shapes. `evidence run` writes `argv`, `exitCode`,
`durationSeconds` at the top level. `gate ran` wanted a file literally named
`ran-receipt.json` holding a top-level `checks` list. A receipt minted by
`sbe evidence run --out ran-receipt.json -- <command>` satisfied neither
name's promise: `sbe gate ran` reported NO-DATA ("no 'checks' list") on
evidence this same product had just minted. See docs/cards/CARD-technical-qa.md
and docs/deliver/DELIVER-technical-qa.md for the trap as two strangers found it.

Run: python3 tools/test_sbe_receipt_shapes.py

Every test here builds a real temporary git repository, mints a receipt
through the real `sbe evidence run` wrapper (or, for the one test that needs a
receipt the wrapper itself cannot produce, writes one by hand), and runs the
real `tools/sbe_gate.py ran` against it. Nothing is mocked, because the
defect lived exactly at the seam between what one tool writes and what
another tool reads, and a mocked seam would test the mock.

TWO THINGS THIS FILE PROVES, matching the two fixtures below:
  1. THE REPRODUCTION: a receipt `sbe evidence run` actually wrote now
     satisfies `sbe gate ran`, end to end, with a PASS verdict.
  2. THE FIX DID NOT LOOSEN THE GATE: a receipt wearing the same recognised
     shape (the same `generator` string, the same `argv` list) but that never
     actually completed a run -- no `exitCode` was ever recorded -- still
     FAILs. Recognising the shape is not the same as trusting it; the
     exit-code and duration rules gate_ran has always applied still apply.
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
GATE = os.path.join(HERE, "sbe_gate.py")


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


def write_json(cwd, rel, obj):
    return write(cwd, rel, json.dumps(obj, indent=2, sort_keys=True))


def verdict_line(text, name):
    """The one report line for a named check, or a sentence saying it is
    absent. Deliberately not a (verdict, evidence) pair, matching every other
    fixture in this project's own test suite: a two-value return under
    `tools/` reads as a possible verdict to `evals/test_no_data_class.py`.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return line
    return "NO VERDICT LINE for %r in:\n%s" % (name, text)


def verdict_of(text, name):
    line = verdict_line(text, name)
    parts = line.split()
    return parts[1] if len(parts) >= 2 and parts[0] == name else line


#: What one `sbe_gate.py` invocation produced. Named rather than a bare
#: `(code, text)` tuple: see `gate_ran_with` below for why a two-value return
#: in this tree is read as a verdict pair by the honesty meta-test's lint.
GateRun = collections.namedtuple("GateRun", "code text")

#: What one `sbe evidence verify` invocation produced. Named for the same
#: reason `GateRun` above is: a bare `(verdict, text)` return under `tools/`
#: reads as a verdict pair to `evals/test_no_data_class.py`, whose lint then
#: cannot prove the first element is never "PASS".
VerifyRun = collections.namedtuple("VerifyRun", "verdict text")


class ReceiptShapeFixture(unittest.TestCase):
    """A fresh repository per test, one commit in it, so `_current_head` and
    the commit-binding check both have something real to compare a receipt
    against."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "commit.gpgsign", "false")
        write(self.repo, "README.md", "base\n")
        write(self.repo, "src/service.py", "def handle():\n    return 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "the work this receipt will cover")
        self.head = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def mint(self, receipt_rel, command):
        """Run `command` through the real `sbe evidence run` wrapper, writing
        the receipt at `receipt_rel` INSIDE the repository (not outside it,
        the way test_sbe_evidence.py's own fixture keeps receipts, because
        `sbe gate ran` walks the repository tree to find `ran-receipt.json`
        and a receipt written elsewhere is a receipt this gate never sees).
        Returns (receipt path, mint exit code, mint output)."""
        out_path = os.path.join(self.repo, receipt_rel)
        argv = [sys.executable, SBE, "evidence", "run", "--out", out_path,
                "--cwd", self.repo, "--covers", "src/service.py", "--"] + list(command)
        proc = subprocess.run(argv, capture_output=True, text=True)
        return out_path, proc.returncode, proc.stdout + proc.stderr

    def run_gate_ran(self):
        proc = subprocess.run([sys.executable, GATE, "ran", self.repo],
                              capture_output=True, text=True)
        return proc.stdout + proc.stderr

    def gate_ran_with(self, *flags):
        """A GateRun for `sbe_gate.py ran <repo> [flags]`.

        `run_gate_ran` above discards the exit code, which is fine for every
        test that asserts on a verdict word. It is not fine for
        `--strict-producer`, whose whole contract is that the DEFAULT reports
        (exit 0) and the flag blocks (exit 1): that is a claim about the exit
        code and cannot be proven from the report text.

        A NAMED RESULT rather than a bare `(code, text)` pair, for the reason
        `verdict_line` above already gives about itself: a two-value return
        under `tools/` reads as a possible verdict to
        `evals/test_no_data_class.py`, whose lint then cannot prove the first
        element is never "PASS". This helper's first version was a bare pair
        and that lint named it. Attributes also read better at the call site.
        """
        proc = subprocess.run([sys.executable, GATE, "ran", self.repo] + list(flags),
                              capture_output=True, text=True)
        return GateRun(proc.returncode, proc.stdout + proc.stderr)

    def verify_receipt(self, path):
        """A VerifyRun for the REAL `sbe evidence verify` over one receipt.

        The seal is only meaningful through the command that reads it, so the
        seal tests below assert a verdict this wrapper produced rather than
        the membership of a field name in a module tuple."""
        proc = subprocess.run([sys.executable, SBE, "evidence", "verify", path,
                               "--cwd", self.repo, "--json"],
                              capture_output=True, text=True)
        text = proc.stdout + proc.stderr
        try:
            verdict = json.loads(proc.stdout)["verdict"]
        except (ValueError, KeyError) as exc:
            raise AssertionError("sbe evidence verify wrote no JSON verdict (%s): %s"
                                 % (exc, text))
        return VerifyRun(verdict, text)

    def load_receipt(self, path):
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def save_receipt(self, path, data):
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))

    def reseal(self, data):
        """Recompute the seal, so a fixture that deliberately rewrites a
        receipt's own schema version isolates ONE control instead of tripping
        the seal on the rewrite it did not mean to test. Mirrors
        `tools/test_sbe_evidence.py`'s helper of the same name."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            data["runId"] = mod.compute_seal(data)
        finally:
            sys.path.pop(0)
        return data


class TestTheReproduction(ReceiptShapeFixture):
    """The bug as the two strangers found it, reproduced end to end and then
    proven fixed: mint a receipt with `sbe evidence run --out <path> --
    <command>`, then run `sbe gate ran <dir>` against exactly that file."""

    def test_a_receipt_evidence_run_actually_wrote_satisfies_gate_ran(self):
        receipt, mint_code, mint_out = self.mint(
            "evidence/ran-receipt.json",
            ["python3", "-c", "print('the suite ran')"])
        self.assertEqual(mint_code, 0, "sbe evidence run itself failed: %s" % mint_out)
        self.assertTrue(os.path.exists(receipt), "no receipt was written at all")

        # BEFORE the fix, this receipt's own top level held no 'checks' list
        # (it holds argv/exitCode/durationSeconds instead), and gate_ran's
        # _items(d, "checks") found nothing there, so this asserts the exact
        # NO-DATA line the card records did NOT survive the fix, not merely
        # that some other verdict appears.
        with io.open(receipt, encoding="utf-8") as fh:
            recorded = json.load(fh)
        self.assertNotIn("checks", recorded,
                         "fixture drifted: sbe evidence run now writes a 'checks' list, so this "
                         "receipt shape is no longer the one the defect was reported against")
        self.assertIn("argv", recorded)
        self.assertIn("exitCode", recorded)
        self.assertIn("durationSeconds", recorded)

        report = self.run_gate_ran()
        line = verdict_line(report, "ran")
        self.assertEqual(verdict_of(report, "ran"), "PASS",
                         "sbe gate ran did not PASS a receipt sbe evidence run just wrote: %s"
                         % line)
        self.assertIn("recorded check(s)", line)
        self.assertNotIn("no 'checks' list", line,
                         "the exact NO-DATA sentence the card reproduced is still being printed "
                         "over a receipt this product's own wrapper minted")


class TestTheFixDidNotLoosenTheGate(ReceiptShapeFixture):
    """Recognising the wrapper's shape must not become a rubber stamp: a
    receipt wearing the same recognised identity (`generator` says `sbe
    evidence run`, `argv` is a list) but that never recorded an exit code --
    because the run it claims never actually completed -- must still FAIL,
    exactly as a hand-written `checks` entry missing `exit_code` already did
    before this fix touched anything.

    CALIBRATED RED: this receipt is written BY HAND rather than minted,
    because the real wrapper cannot produce one missing `exitCode` (the field
    always comes from an observed `subprocess.run`, per evidence.py's own
    "THE COMMAND IS RUN HERE" law) -- that is exactly the gap a forged or
    truncated receipt could exploit if shape alone were trusted. Run without
    the `tools/sbe_gate.py` change (i.e. against the pre-fix `gate_ran`, which
    never looks past `checks` at all) this fixture's receipt reports NO-DATA,
    not FAIL, because the shape was not recognised yet; the assertion below
    demands the stronger, post-fix answer.
    """

    def test_a_wrapper_shaped_receipt_that_never_recorded_an_exit_code_still_fails(self):
        receipt = write_json(self.repo, "evidence/ran-receipt.json", {
            "schemaVersion": "1.3",
            "generator": "sbe evidence run",
            "argv": ["python3", "-c", "print('claims to have run')"],
            "headCommit": self.head,
            # No exitCode. No durationSeconds. This is the shape a truncated
            # write, or a hand-typed forgery copying the generator string
            # without the run that goes with it, would produce.
        })
        report = self.run_gate_ran()
        line = verdict_line(report, "ran")
        self.assertEqual(verdict_of(report, "ran"), "FAIL",
                         "a receipt claiming the wrapper's identity but recording no exit code "
                         "was not failed: %s" % line)
        self.assertIn("no exit code recorded", line)

    def test_a_wrapper_receipt_for_a_command_that_actually_exited_nonzero_still_fails(self):
        """The same forced-red shape, but produced by the REAL wrapper this
        time: the command genuinely ran and genuinely failed, so the receipt
        is honest and the gate must still refuse it, not read "a receipt
        exists in the recognised shape" as "the check passed"."""
        receipt, mint_code, mint_out = self.mint(
            "evidence/ran-receipt.json",
            ["python3", "-c", "import sys; sys.exit(1)"])
        # `sbe evidence run` itself exits with the covered command's own exit
        # code (evidence.main propagates receipt["exitCode"]), so a mint of a
        # command that exits 1 is a successful mint that exits 1, not a
        # failed mint. The receipt getting written at all is confirmed below.
        self.assertEqual(mint_code, 1, "sbe evidence run did not propagate the command's exit "
                         "code as its own: %s" % mint_out)
        self.assertTrue(os.path.exists(receipt), "no receipt was written despite the command "
                        "having run and exited: %s" % mint_out)
        with io.open(receipt, encoding="utf-8") as fh:
            recorded = json.load(fh)
        self.assertEqual(recorded.get("exitCode"), 1,
                         "fixture drifted: the minted receipt did not record the nonzero exit "
                         "this test depends on")

        report = self.run_gate_ran()
        line = verdict_line(report, "ran")
        self.assertEqual(verdict_of(report, "ran"), "FAIL",
                         "a receipt recording a nonzero exit code was not failed: %s" % line)
        self.assertIn("exited nonzero", line)


class TestTheGateReadsTheSameFreshnessRuleAsTheReceiptReader(ReceiptShapeFixture):
    """ROADMAP ROW E83. This gate kept its own copy of the staleness rule,
    exact equality against HEAD, and said in its own docstring that this was
    "the same mismatch src/brothersbe/evidence.py's own _check_commit already
    treats as a broken claim". It stopped being the same rule the moment the
    reader learned that a receipt survives the commit that carries it, and the
    two then disagreed out loud: `sbe evidence verify` called a receipt sound
    and `sbe gate ran` failed the same file in the same tree.

    Both directions are driven here, because a gate that agrees by always
    saying yes is not agreeing with anything."""

    def test_a_receipt_the_reader_calls_sound_is_not_failed_by_the_gate(self):
        receipt, code, text = self.mint("evidence/ran-receipt.json",
                                        [sys.executable, "-c", "pass"])
        self.assertEqual(code, 0, text)
        write(self.repo, "docs/notes.md", "a later commit over nothing this covers\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "later work outside the coverage")
        reader = self.verify_receipt(receipt)
        self.assertIn("PASS", reader.text, "fixture drifted: the reader must call this "
                                           "receipt sound for the disagreement to be "
                                           "testable: %s" % reader.text)
        report = self.run_gate_ran()
        self.assertEqual(verdict_of(report, "ran"), "PASS",
                         "the gate must not fail a receipt the reader calls sound: %s"
                         % verdict_line(report, "ran"))

    def test_a_receipt_the_reader_calls_stale_is_still_failed_by_the_gate(self):
        """THE GUARD. The later commit moves the file the receipt covers, so
        both the reader and the gate must refuse it."""
        receipt, code, text = self.mint("evidence/ran-receipt.json",
                                        [sys.executable, "-c", "pass"])
        self.assertEqual(code, 0, text)
        write(self.repo, "src/service.py", "def handle():\n    return 2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "later work over the covered code")
        report = self.run_gate_ran()
        self.assertEqual(verdict_of(report, "ran"), "FAIL",
                         "a receipt whose covered code moved must still fail: %s"
                         % verdict_line(report, "ran"))
        self.assertIn("is not the current head", verdict_line(report, "ran"))


class TestRequireHeadcommitFlag(ReceiptShapeFixture):
    """P6: `docs/KNOWN-LIMITS.md`'s "A hard-gate receipt with no headCommit
    still passes unbound" names the gap this closes for an opt-in caller.
    `_commit_problem` never judges a receipt naming no `headCommit` at all
    (it cannot tell "written before the field existed" from "chose not to
    record one"), so every shipped example in this repository stays a
    silent PASS by default -- that default is `T1` in the brief this lane
    was cut against. `--require-headcommit` is the `T2`-and-above opt-in:
    once a caller states every receipt it accepts must name the commit it
    covers, an unbound receipt is NO-DATA, never a silent PASS and never a
    FAIL nothing here actually observed.

    CALIBRATED RED, quoted in the first test below: before this lane,
    `--require-headcommit` was not a recognised flag at all (`sbe_gate.py`
    exits 2, "unrecognized flag"), and even reading the flag as a no-op
    left the unbound receipt PASSing exactly as it does today under `ran`
    with no flag.
    """

    def _unbound_ran_receipt(self):
        """A `ran-receipt.json` that is sound in every field `gate_ran`
        otherwise checks (one check, zero exit, a positive duration) and
        carries no `headCommit` at all -- the exact shape `_commit_problem`
        already declines to judge either way, see its own docstring."""
        return write_json(self.repo, "evidence/ran-receipt.json", {
            "checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 5}],
        })

    def test_the_red_an_unbound_receipt_passes_by_default_with_no_flag(self):
        """T1 stays as it is: not passing `--require-headcommit` at all
        leaves today's behaviour byte-identical, PASS over a receipt that
        never names the commit it covers."""
        self._unbound_ran_receipt()
        run = self.gate_ran_with()
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "PASS",
                         "T1 (no flag) must be unchanged by this lane: %s" % line)

    def test_require_headcommit_reports_no_data_for_an_unbound_receipt(self):
        """The fix, driven red first: an unbound receipt under
        `--require-headcommit` ('T2 and above' in the brief's own words)
        must read NO-DATA, never PASS and never a FAIL nothing here
        observed."""
        self._unbound_ran_receipt()
        run = self.gate_ran_with("--require-headcommit")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "NO-DATA",
                         "an unbound receipt under --require-headcommit must be NO-DATA, "
                         "never a silent PASS and never a FAIL: %s" % line)
        self.assertIn("no headCommit is recorded", line)
        self.assertIn("--require-headcommit", line)

    def test_require_headcommit_still_passes_a_bound_receipt(self):
        """Every existing bound-receipt case stays PASS: a receipt minted by
        the real wrapper (which always records `headCommit`, see
        evidence.py) is unaffected by the new flag."""
        receipt, mint_code, mint_out = self.mint(
            "evidence/ran-receipt.json",
            ["python3", "-c", "print('the suite ran')"])
        self.assertEqual(mint_code, 0, "sbe evidence run itself failed: %s" % mint_out)
        recorded = self.load_receipt(receipt)
        self.assertTrue(recorded.get("headCommit"),
                        "fixture drifted: the minted receipt no longer binds a headCommit, "
                        "so this case no longer proves what it claims to")
        run = self.gate_ran_with("--require-headcommit")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "PASS",
                         "a receipt that DOES bind headCommit must still PASS under "
                         "--require-headcommit: %s" % line)

    def test_require_headcommit_still_fails_a_stale_bound_receipt(self):
        """The mismatch case is untouched by this flag either way: a receipt
        bound to a commit that is no longer HEAD FAILs, not NO-DATA,
        whether or not `--require-headcommit` is passed."""
        receipt = write_json(self.repo, "evidence/ran-receipt.json", {
            "checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 5}],
            "headCommit": self.head,
        })
        write(self.repo, "src/service.py", "def handle():\n    return 2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "move head on past the bound receipt")
        run = self.gate_ran_with("--require-headcommit")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "FAIL",
                         "a stale bound receipt must still FAIL under --require-headcommit, "
                         "not read as NO-DATA: %s" % line)
        self.assertIn("is not the current head", line)


class TestUnrelatedJsonIsUntouched(ReceiptShapeFixture):
    """A file merely named `ran-receipt.json` that carries neither the old
    `checks` shape nor the wrapper's `generator`/`argv` shape must keep
    reporting today's plain NO-DATA, not a false PASS invented by treating any
    unfamiliar object as a recognised receipt."""

    def test_a_receipt_with_neither_shape_stays_no_data(self):
        write_json(self.repo, "evidence/ran-receipt.json", {"note": "not a receipt at all"})
        report = self.run_gate_ran()
        line = verdict_line(report, "ran")
        self.assertEqual(verdict_of(report, "ran"), "NO-DATA", line)
        self.assertIn("no 'checks' list", line)


class TestStrictProducerClassifiesWhoProducedTheReceipt(ReceiptShapeFixture):
    """A2, receipt provenance. What the adopting team reported: `gate ran`
    inspects that an exit code is a whole number and a duration positive, so a
    receipt typed on a laptop passes exactly as green as one a build system
    produced. Their operating rule is to trust evidence the build system made.

    THREE STATES, and they are three because the receipt already carries
    three. A truthy `ciRunId` means something outside the machine claimed the
    run; a PRESENT-but-null `ciRunId` means the wrapper ran and no build
    system was there; an ABSENT key means nothing in the receipt speaks to its
    origin at all. The second and third are different findings and must get
    different sentences: one says a laptop, the other says silence. That
    reading is `producer_class` in `tools/sbe_checks.py`, shared with
    `tools/sbe_passport.py:origin_of` rather than mirrored, because two
    parsers of one field is the defect this project already paid for once.

    THE CEILING THESE TESTS CANNOT RAISE, asserted nowhere below because it
    cannot be: every case here is about what a receipt CLAIMS. `SBE_CI_RUN_ID`
    is an environment variable any shell can export, so the passing case is
    produced by exporting it, which is exactly the forgery this flag does not
    detect. See the comment carrying the same words in `tools/sbe_gate.py`.
    """

    def receipt_with(self, producer_fields):
        """A hand-written receipt that is sound in every respect the `ran`
        gate already checks (one named check, zero exit, positive duration,
        bound to this repository's HEAD), differing only in what it records
        about its producer. Anything this gate refuses about such a receipt is
        therefore about the producer and nothing else."""
        body = {
            "headCommit": self.head,
            "checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 812}],
        }
        body.update(producer_fields)
        return write_json(self.repo, "evidence/ran-receipt.json", body)

    def test_strict_producer_refuses_a_receipt_with_no_ci_run_id(self):
        """The key is ABSENT: the receipt predates the field, or was typed by
        hand by somebody who never knew about it. Refused by name under the
        flag, and the sentence must say the origin was never ESTABLISHED
        rather than assert a laptop, because the receipt does not say that."""
        self.receipt_with({})
        run = self.gate_ran_with("--strict", "--strict-producer")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "FAIL",
                         "a receipt recording nothing at all about its producer was not "
                         "refused under --strict-producer: %s" % line)
        self.assertIn("producer not established", line)
        self.assertIn("no ciRunId field", line)
        self.assertEqual(run.code, 1,
                         "--strict --strict-producer did not block on a FAIL: %s" % run.text)

    def test_strict_producer_names_a_receipt_produced_off_the_build_system(self):
        """The key is PRESENT and null, which is what the real wrapper writes
        on a laptop. A different situation from the one above and it gets its
        own sentence: this receipt does say where it came from."""
        self.receipt_with({"ciRunId": None})
        run = self.gate_ran_with("--strict", "--strict-producer")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "FAIL", line)
        self.assertIn("produced off the build system", line)
        self.assertNotIn("producer not established", line,
                         "a receipt that DID record its origin was described with the sentence "
                         "reserved for one that recorded nothing; the two states have collapsed")
        self.assertEqual(run.code, 1, run.text)

    def test_without_the_flag_the_same_receipt_reports_and_does_not_block(self):
        """D2, ratified: a refusal in this product ships behind an estate
        switch and defaults to reporting. The accounting must still be VISIBLE
        by default, otherwise the default is silence rather than a report."""
        self.receipt_with({"ciRunId": None})
        run = self.gate_ran_with("--strict")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "PASS",
                         "the default turned a producer finding into a blocking verdict, which "
                         "re-verdicts every estate's existing green run: %s" % line)
        self.assertIn("1 of 1 receipt(s) record no build-system producer", line)
        self.assertEqual(run.code, 0,
                         "a --strict run without --strict-producer blocked on a producer "
                         "finding: %s" % run.text)

    def test_a_receipt_recording_a_ci_run_id_passes_under_the_flag(self):
        """The green case, and the ceiling in one test: this receipt passes
        because it CLAIMS a build system, and the claim was typed here by the
        test exactly as a forger would type it."""
        self.receipt_with({"ciRunId": "run-8842", "ciRunUrl": "https://build.invalid/8842"})
        run = self.gate_ran_with("--strict", "--strict-producer")
        line = verdict_line(run.text, "ran")
        self.assertEqual(verdict_of(run.text, "ran"), "PASS", line)
        self.assertNotIn("producer", line,
                         "a receipt recording a build-system run id still drew producer "
                         "accounting, so the flag reports on receipts it accepted: %s" % line)
        self.assertEqual(run.code, 0, run.text)

    def test_the_wrapper_records_the_run_url_beside_the_run_id(self):
        """The other half of A2: the id says a build system ran it, the url is
        how a human reaches that run. Minted through the REAL wrapper with
        both variables set, because a field this test writes by hand would
        prove only that this test can write a field."""
        env = dict(os.environ, SBE_CI_RUN_ID="run-8842",
                   SBE_CI_RUN_URL="https://build.invalid/8842")
        out_path = os.path.join(self.repo, "evidence/ran-receipt.json")
        argv = [sys.executable, SBE, "evidence", "run", "--out", out_path,
                "--cwd", self.repo, "--covers", "src/service.py", "--",
                "python3", "-c", "print('ran under a build system')"]
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with io.open(out_path, encoding="utf-8") as fh:
            recorded = json.load(fh)
        self.assertEqual(recorded.get("ciRunId"), "run-8842")
        self.assertEqual(recorded.get("ciRunUrl"), "https://build.invalid/8842")

    def mint_with_a_run_id_and_no_url(self, receipt_rel):
        """The receipt a forger starts from: minted by the REAL wrapper under
        a build-system id, recording no url, because the url is the field
        about to be typed in. Returns the receipt path."""
        env = dict(os.environ, SBE_CI_RUN_ID="run-8842")
        env.pop("SBE_CI_RUN_URL", None)
        out_path = os.path.join(self.repo, receipt_rel)
        argv = [sys.executable, SBE, "evidence", "run", "--out", out_path,
                "--cwd", self.repo, "--covers", "src/service.py", "--",
                "python3", "-c", "print('ran under a build system')"]
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return out_path

    def test_a_url_typed_into_a_current_schema_receipt_breaks_the_seal(self):
        """The forgery the field would otherwise invite, asserted through the
        verdict rather than through a module tuple: take a real receipt from a
        run that recorded no url, type one in, and `sbe evidence verify` must
        refuse it because `runId` no longer matches the seal.

        BEHAVIOURAL ON PURPOSE, and the reason is a defect this test replaces.
        Its previous version asserted only that "ciRunUrl" appears in
        `SEALED_FIELDS` and in `FIELDS_INTRODUCED_IN`. Both memberships can
        hold while `compute_seal` leaves the field out of the digest it
        actually computes, and an audit mutated exactly that: the whole suite
        stayed green while the property this test is named for was gone.
        """
        path = self.mint_with_a_run_id_and_no_url("evidence/ran-receipt.json")
        receipt = self.load_receipt(path)
        self.assertEqual(receipt.get("schemaVersion"), "1.4",
                         "fixture drifted: this case is about a receipt minted at the schema "
                         "that introduced ciRunUrl, and the wrapper stamped %r"
                         % receipt.get("schemaVersion"))
        self.assertIsNone(receipt.get("ciRunUrl"),
                          "fixture drifted: the wrapper recorded a url with SBE_CI_RUN_URL "
                          "unset, so nothing below is typing one in")
        clean = self.verify_receipt(path)
        self.assertEqual(clean.verdict, "PASS",
                         "the untouched receipt did not verify, so a FAIL below would prove "
                         "nothing about the edit: %s" % clean.text)

        receipt["ciRunUrl"] = "https://build.invalid/9999-a-run-that-did-something-else"
        self.save_receipt(path, receipt)

        run = self.verify_receipt(path)
        self.assertEqual(run.verdict, "FAIL",
                         "a build-system url was typed into a 1.4 receipt and it still "
                         "verified, so the url reaches a reader without the run that minted "
                         "the receipt ever having recorded it: %s" % run.text)
        self.assertIn("does not match the seal", run.text,
                      "the receipt was refused for some reason other than the seal, so this "
                      "test is not proving the seal covers the url: %s" % run.text)

    def test_the_same_url_typed_into_an_older_receipt_still_verifies(self):
        """THE DOCUMENTED COST, asserted so no reader assumes the property
        above is wider than it is.

        `compute_seal` digests `_fields_for(declared, SEALED_FIELDS)`, and
        `FIELDS_INTRODUCED_IN` puts `ciRunUrl` at 1.4, so a receipt DECLARING
        1.3 is judged by its own version's contract, which never had the
        field. The identical edit that fails above changes no digest here and
        the receipt still verifies.

        That is deliberate, not an oversight. Sealing the field retroactively
        would move the digest of every 1.3 receipt already on disk and fail
        all of them at once, which is worse than one unchecked field on
        receipts minted before the field existed. The comment on `ciRunUrl` in
        `src/brothersbe/evidence.py` states the same ceiling in the same
        words.
        """
        path = self.mint_with_a_run_id_and_no_url("evidence/ran-receipt.json")
        older = self.load_receipt(path)
        older["schemaVersion"] = "1.3"
        del older["ciRunUrl"]
        self.save_receipt(path, self.reseal(older))
        clean = self.verify_receipt(path)
        self.assertEqual(clean.verdict, "PASS",
                         "the resealed 1.3 receipt did not verify, so the case below is not "
                         "about the version scoping: %s" % clean.text)

        older["ciRunUrl"] = "https://build.invalid/9999-a-run-that-did-something-else"
        self.save_receipt(path, older)

        run = self.verify_receipt(path)
        self.assertEqual(run.verdict, "PASS",
                         "a receipt declaring 1.3 refused a hand-typed ciRunUrl, which means "
                         "the field was sealed retroactively and every 1.3 receipt already on "
                         "disk now fails: %s" % run.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
