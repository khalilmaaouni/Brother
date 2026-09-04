"""P10 (pilot team feedback, `docs/adoption/2026-08-15-pilot-team-feedback-
adjustment-plan.md`, "The step called PROVE checks the paperwork, not the
work"): `sbe verify` used to run design completeness, the gates and the
score, and never `sbe converge`, the actual built-versus-designed
comparison. At tier T2 and above `sbe verify` now also runs converge's
VERIFICATION dimension (`src/brothersbe/cli.py`'s `_verify_converge_delegate`)
and folds its verdict into the aggregate exit code; below T2 nothing changes.

Fixture conventions mirrored from `tools/test_sbe_converge.py`'s
`ConvergeScenario` (a real git repo per test, `sbe` run as a real
subprocess, three-value process helpers) and from `templates/dossier`,
whose shipped `.md` files already satisfy `sbe_design.py --strict`'s
structural checks once the `SBE-TEMPLATE-UNFILLED` marker comment is
stripped (`evals/run_evals.py`'s own `unedited-copied-template-caught`
case pins that the marker, not the prose, is what `check_placeholder`
reads).
"""
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
TEMPLATES = os.path.join(ROOT, "templates", "dossier")

_MARKER_START = "<!-- SBE-TEMPLATE-UNFILLED"


def _strip_marker(text):
    """The shipped `SBE-TEMPLATE-UNFILLED` HTML comment, removed; the rest
    of the template's structure (headings, tables) is left untouched, since
    that structure is exactly what `sbe_design.py`'s checks are built to
    read."""
    out = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if _MARKER_START in line:
            skipping = True
        if skipping:
            if "-->" in line:
                skipping = False
            continue
        out.append(line)
    return "".join(out)


#: A verification row with a REAL backticked command, replacing the
#: template's own 07-verification.md (whose shipped rows carry no code span
#: at all, so `sbe plan` would derive zero verificationCommands from them).
VERIFICATION_BODY = (
    "# 07. Verification plan\n\n"
    "| Claim this design makes | The check that proves it | When it runs |\n"
    "|---|---|---|\n"
    "| the widget service answers | `python3 -c \"print('WIDGET-OK')\"` | CI |\n"
)

VERIFY_COMMAND_ARGV = ["python3", "-c", "print('WIDGET-OK')"]

#: 08-behaviour.md, written OUTRIGHT rather than copied from the template
#: (like 07-verification.md above), for two reasons documented on
#: `sbe_design.py`'s own `check_behaviour`: (1) 08 became a T1-and-above
#: required artifact at commit 9bee153, ahead of when these fixtures were
#: first written, so `_build` never wrote one and every fixture FAILed the
#: `artifacts` gate on "missing: 08-behaviour.md" regardless of what
#: `_verify_converge_delegate` did -- the P10 wiring under test never ran
#: at all, on any tree, until this file existed; (2) `check_behaviour`
#: refuses a dossier whose rows are STILL the shipped template's own
#: example rules (`_shipped_example_rules`), so copying 08-behaviour.md
#: verbatim (marker stripped, like the other five) would FAIL that refusal
#: rather than pass it. This body's rows are about the widget domain the
#: rest of this fixture already invented (`src/widget.py`, `WIDGET-OK`),
#: not the template's checkout/warehouse example, so they read as this
#: dossier's own behaviour.
BEHAVIOUR_BODY = (
    "# 08. Behaviour table\n\n"
    "## Rules\n"
    "| ID | Starting point | Trigger | Required outcome | Proof |\n"
    "|---|---|---|---|---|\n"
    "| B1 | The widget service is deployed | Something calls `widget()` | It returns "
    "the string ok | Unit test: call `widget()`, assert the return value equals ok |\n"
)

#: T2: `changes_contract` and `touches_sensitive` both "n" would compute
#: T0/T1 (see `tools/sbe_intake.py:compute_tier`); `consumers: many` alone
#: crosses the T2 line without also requiring a contract-compatibility
#: section `sbe plan` would otherwise demand of the ADR.
T2_ANSWERS = {"changes_contract": "n", "crosses_boundary": "n",
             "reversible_under_hour": "y", "touches_sensitive": "n",
             "consumers": "many"}
#: T1: `crosses_boundary: y` alone, nothing else raised.
T1_ANSWERS = {"changes_contract": "n", "crosses_boundary": "y",
             "reversible_under_hour": "y", "touches_sensitive": "n",
             "consumers": "none"}

CHANGE_DIR = "design/widget"


def _run(argv, cwd=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: see `tools/test_sbe_converge.py`'s `_run` for
    # why every runner helper in this project returns three rather than a
    # (verdict, evidence)-shaped pair the honesty meta-test would flag.
    return out.returncode, out.stdout + out.stderr, out.stderr


class VerifyConvergeScenario(unittest.TestCase):
    """A fresh git repository per test, with a dossier at `design/widget`
    that satisfies `sbe_design.py --strict` at either T1 or T2 (see
    `_build`), so any FAIL this suite pins comes from
    `_verify_converge_delegate` alone, never from an incomplete dossier
    the aggregate `sbe verify` would have FAILed anyway."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-verify-converge-")
        self.repo = os.path.join(self.tmp, "repo")
        self.doss = os.path.join(self.repo, CHANGE_DIR)
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text.strip()

    def sbe(self, *args):
        return _run([sys.executable, SBE] + list(args))

    def _write(self, rel, content):
        path = os.path.join(self.repo, rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        if isinstance(content, dict):
            content = json.dumps(content, indent=2, sort_keys=True) + "\n"
        io.open(path, "w", encoding="utf-8").write(content)

    def _build(self, answers, tier, override=None, override_reason=None):
        """A dossier at `design/widget` complete enough for tier `tier`
        (`01-purpose.md` through `07-verification.md`, plus `08-behaviour.md`,
        for T2; `01-purpose.md` and `08-behaviour.md` alone would suffice for
        T1 per `sbe_intake.REQUIRED`, but every artifact is written either
        way so the T1 and T2 fixtures differ ONLY in `00-intake.json`'s
        declared tier and answers, isolating that one variable). Templates
        are copied verbatim with only the shipped placeholder marker
        stripped, except 03-adr.md (its Decision section gets one
        backticked mention of `src/widget.py`, so `sbe plan` derives a
        writer task that owns it), 07-verification.md (replaced outright:
        the template's own rows carry no backticked command for `sbe plan`
        to derive a verificationCommand from) and 08-behaviour.md (replaced
        outright, not copied: see `BEHAVIOUR_BODY`'s own comment for why a
        stripped-marker copy of the template would FAIL `check_behaviour`
        as still-the-shipped-example).

        `override`/`override_reason` default to None, None (no override, the
        original behaviour every fixture but the overridden-up one below
        still uses): when the caller passes `tier` different from what
        `answers` computes, it must also pass a matching `override` (the
        same tier) and a reviewable `override_reason` (L15), the same shape
        `check_artifacts` requires and `design/team-operating-model`'s own
        `00-intake.json` ships.

        Commits the seed and returns the head sha, or None if `sbe plan
        --write` itself refused (a fixture bug, asserted by the caller).
        """
        for name in ("01-purpose.md", "02-process.md", "03-adr.md",
                    "05-data-model.md", "06-diagrams.md"):
            body = _strip_marker(
                io.open(os.path.join(TEMPLATES, name), encoding="utf-8").read())
            if name == "03-adr.md":
                body = body.replace(
                    "Checkout publishes each confirmed order to a queue, and the "
                    "warehouse\nsystem consumes from that queue on its own schedule.",
                    "Checkout publishes each confirmed order to a queue, and the "
                    "warehouse\nsystem consumes from that queue on its own schedule, "
                    "served by `src/widget.py`.")
            self._write(os.path.join(CHANGE_DIR, name), body)
        self._write(os.path.join(CHANGE_DIR, "07-verification.md"), VERIFICATION_BODY)
        self._write(os.path.join(CHANGE_DIR, "08-behaviour.md"), BEHAVIOUR_BODY)
        self._write(os.path.join(CHANGE_DIR, "00-intake.json"),
                    {"answers": answers, "tier": tier, "override": override,
                     "override_reason": override_reason})
        self._write("src/widget.py", "def widget():\n    return 'ok'\n")

        code, text, _ = self.sbe("plan", self.doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "fixture setup wrote a plan `sbe plan` itself "
                                  "refuses; a fixture bug, not a P10 finding: %s" % text)
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        return self.git("rev-parse", "HEAD")

    def _receipt(self, name="r1.json"):
        """A sealed receipt for `VERIFY_COMMAND_ARGV`, bound to the current
        head, written into `<dossier>/.sbe/evidence`: the SAME store
        `_mint_evidence`/`sbe verify`'s own design/gate/score receipts use
        (`tasks.evidence_dir(target)` = `<target>/.sbe/evidence`), which
        `_verify_converge_delegate` reads by calling `converge.evaluate`
        with `cwd=target=self.doss`. A receipt written under the REPO
        ROOT's `.sbe/evidence` instead (`sbe converge`'s own `--cwd`
        convention, used directly rather than through `sbe verify`) would
        silently miss: two established, different evidence roots in this
        codebase's two commands, not a bug in either alone.
        """
        out_path = os.path.join(self.doss, ".sbe", "evidence", name)
        code, text, _ = self.sbe("evidence", "run", "--out", out_path,
                                 "--cwd", self.doss, "--", *VERIFY_COMMAND_ARGV)
        self.assertEqual(code, 0, "evidence run failed: %s" % text)
        return out_path

    def _verify(self):
        return self.sbe("verify", self.doss, "--cwd", self.repo, "--no-decisions")


class TestVerifyRunsConvergeAtT2(VerifyConvergeScenario):
    def test_missing_receipt_fails_naming_converges_own_message(self):
        """Fixture 1. Before `_verify_converge_delegate` existed, `sbe
        verify` never ran `sbe converge` at all, so this exact dossier
        (complete, design/gate/score all clean) exited 0 despite the
        verification command never having run: the P10 defect. After the
        wiring, it FAILs, and the FAIL line is converge's OWN sentence
        about the missing receipt (`converge._find_receipt`'s "no receipt
        for it exists bound to..." wording), not a generic one.

        Before this fixture wrote its own `08-behaviour.md` (see `_build`
        and `BEHAVIOUR_BODY`), this test passed OVERDETERMINED: the
        `artifacts` design gate FAILed on "missing: 08-behaviour.md" for a
        reason that had nothing to do with `_verify_converge_delegate`, so
        the exit-code-1 assertion below proved nothing about the wiring
        under test. The `artifacts` assertion just below pins the isolation
        directly: this dossier's design gate PASSES on its own, so the
        exit-1 the two assertions after it check can only be coming from
        converge. Proven by reinjection too (not pinned here, since it
        requires editing `cli.py`, not this fixture): with
        `_verify_converge_delegate` stubbed to return
        `{"code": EXIT_OK, "lines": []}` immediately, this test goes RED
        (`AssertionError: 0 != 1`) while the `artifacts` line in its own
        failure output still reads PASS; restoring the delegate turns it
        GREEN again. That is the wiring being isolated, not the design gate.
        """
        head = self._build(T2_ANSWERS, "T2")
        code, text, _err = self._verify()
        # N2 (2026-08-29) made a T2 dossier's 00-intake.json binding block
        # required in a real git repo: absent one, `artifacts` now reads
        # NO-DATA rather than the PASS this fixture (which carries no
        # binding) got before N2 landed. NO-DATA still proves the isolation
        # this assertion exists for, exactly as PASS did: neither is a FAIL,
        # so the exit-1 the two assertions after it check still can only be
        # coming from converge, never from the design gate rejecting this
        # dossier outright.
        self.assertNotIn("  artifacts  FAIL", text,
                         "the design gate must not FAIL on its own so the exit code below "
                         "can only be coming from the converge wiring under test, not from "
                         "a dossier the gate would have FAILed anyway: %s" % text)
        self.assertEqual(code, 1, "a T2 dossier with an unrun verification command "
                                  "must FAIL: %s" % text)
        self.assertIn("converge-verification", text)
        self.assertIn("FAIL", text)
        self.assertIn("no receipt for it exists bound to %s" % head[:12], text,
                      "the FAIL must carry converge's own message, not a generic one: "
                      "%s" % text)
        self.assertIn(VERIFY_COMMAND_ARGV[0], text)

    def test_sealed_receipt_passes(self):
        """Fixture 2. The identical dossier, with a sealed receipt bound to
        head added before `sbe verify` runs: exit 0, and the
        `converge-verification` line itself reads PASS."""
        self._build(T2_ANSWERS, "T2")
        self._receipt()
        code, text, _err = self._verify()
        self.assertEqual(code, 0, "a T2 dossier with a sealed, head-bound receipt for "
                                  "every verification command must pass: %s" % text)
        self.assertIn("converge-verification", text)
        self.assertIn("PASS", text)
        self.assertIn("sealed receipt bound to", text)


#: A reviewable override reason (L15: at least 3 words and 12 characters,
#: `sbe_design.OVERRIDE_MIN_WORDS`/`OVERRIDE_MIN_CHARS`), the same shape
#: `design/team-operating-model/00-intake.json` ships for its own T1-to-T3
#: raise.
OVERRIDE_REASON = ("Raised deliberately: downstream consumers need the stronger "
                   "guarantee this dossier is really about.")


class TestVerifyGatesOverriddenUpTier(VerifyConvergeScenario):
    """Defect 2 repair. `T1_ANSWERS` computes T1 on its own (see the comment
    beside it above), so before `_verify_converge_delegate` read the
    EFFECTIVE tier it skipped this dossier's converge gate entirely: a
    change deliberately declared T2 by a valid override, which owes MORE
    evidence for having been raised on purpose, got LESS. This pins the
    fix in both directions: gated when the override is valid, and the
    printed tier line names the override rather than misreporting its
    source as "declared" when it is really the untouched computed value
    (`TestVerifyRunsConvergeAtT2` above pins that computed-tier wording).
    """

    def test_computed_t1_declared_t2_with_valid_override_is_gated(self):
        head = self._build(T1_ANSWERS, "T2", override="T2",
                           override_reason=OVERRIDE_REASON)
        code, text, _err = self._verify()
        # Same N2 note as TestVerifyRunsConvergeAtT2 above: this fixture
        # carries no 00-intake.json binding, so `artifacts` now reads
        # NO-DATA rather than PASS. NO-DATA is not a FAIL, so it proves the
        # same isolation PASS used to.
        self.assertNotIn("  artifacts  FAIL", text,
                         "the design gate must not FAIL on its own (same isolation as "
                         "fixture 1 above), so the FAIL below is the converge wiring "
                         "reading the override, not a dossier the gate would have FAILed "
                         "anyway: %s" % text)
        self.assertEqual(code, 1, "a dossier computed T1 but validly overridden to T2 "
                                  "owes the same converge gate a plain T2 dossier owes, "
                                  "and it has no receipt: %s" % text)
        self.assertIn("converge-verification", text)
        self.assertIn("no receipt for it exists bound to %s" % head[:12], text)
        self.assertIn("tier T2 (declared as a valid override", text,
                      "the printed line must name the override as the tier's source, "
                      "not silently pass off a re-derived computed tier as though it "
                      "were declared: %s" % text)
        self.assertIn("overriding the computed tier T1", text)

    def test_computed_t1_declared_t2_with_valid_override_and_receipt_passes(self):
        """The same override, with a sealed receipt: the gate now runs (it
        did not before this fix) and it PASSES, exactly as a plain T2
        dossier with a receipt does in fixture 2 above."""
        self._build(T1_ANSWERS, "T2", override="T2", override_reason=OVERRIDE_REASON)
        self._receipt()
        code, text, _err = self._verify()
        self.assertEqual(code, 0, text)
        self.assertIn("converge-verification", text)
        self.assertIn("PASS", text)
        self.assertIn("sealed receipt bound to", text)


class TestVerifyBelowT2IsUnchanged(VerifyConvergeScenario):
    def test_t1_dossier_never_mentions_converge(self):
        """Fixture 3. The same shape of dossier, declaring T1 instead of T2,
        with NO receipt for its verification command (the same absence
        Fixture 1 FAILs on at T2): `sbe verify` must behave exactly as it
        did before `_verify_converge_delegate` existed. `converge` is never
        invoked at all below T2, so nothing about it appears in the
        output, and the exit code is whatever design/gate/score alone
        decide (0 here, since this dossier is complete at T1 too)."""
        self._build(T1_ANSWERS, "T1")
        code, text, _err = self._verify()
        self.assertEqual(code, 0, text)
        self.assertNotIn("converge-verification", text)
        self.assertNotIn("converge's VERIFICATION dimension", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
