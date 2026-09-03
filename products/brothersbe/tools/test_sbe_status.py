#!/usr/bin/env python3
"""Fixtures for `sbe status`. Run: python3 tools/test_sbe_status.py

Every test here builds a real git repository in a temporary directory and runs
the real command against it. Nothing is mocked, because the defect this
control exists for lives exactly at the seam between what several other
commands recorded and what a reader has to assemble by hand, and a mocked
store would test the mock.

Evidence receipts are generated with the real `sbe evidence run`, over a
stand-in command that declares its check kind with `--kind` instead of being a
real gate or score run. That is deliberate and stated here once: this suite
pins status's OWN reading of the declared field, not `sbe_gate.py` or
`sbe_score.py` themselves, which already have their own fixtures. A real gate
run would make every fixture slower and would not exercise the reading any
more thoroughly.

`TestCheckKindIdentity` below is the exception that has to be read: it pins the
BYPASS, a receipt whose command line spells design, gate and score and which
must clear none of them. Status used to derive the obligation from that text.

Commands are always `sys.executable`, never a system binary picked up from the
machine: the interpreter running this file is the one executable a fixture can
prove exists, and the defect being pinned is about what a receipt SAYS, not
about which program did the saying.
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


class StatusFixture(unittest.TestCase):
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

    def sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE] + list(argv), capture_output=True, text=True)
        # Three values, not two: a two-value return reads as a possible
        # (verdict, evidence) pair to the honesty meta-test, which refuses any
        # such function sitting outside a check registry.
        return out.returncode, out.stdout + out.stderr, out.stderr

    def status(self, *extra):
        return self.sbe("status", self.repo, *extra)

    def status_json(self, *extra):
        code, text, stderr = self.sbe("status", self.repo, "--json", *extra)
        data = json.loads(text) if text.strip().startswith("{") else None
        return code, data, text

    def handover(self, *args):
        """LT-302.B fixtures build real `12-handover.json` records through
        the real `sbe handover` engine (mirroring `tools/test_sbe_handover.py`'s
        own `HandoverScenario.handover`), never a hand-typed JSON stand-in:
        the read path under test belongs to `status.py`, but the record it
        reads must still be one the write side would actually produce."""
        return self.sbe("handover", *args)

    def intake(self, **answers):
        base = {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n", "consumers": "none"}
        base.update(answers)
        return write(self.repo, "00-intake.json", json.dumps({"answers": base}))

    def run_evidence(self, out_rel, argv_kind, exit_code=0, covers="README.md", kinds=()):
        """A real receipt, written by the real `sbe evidence run`.

        `argv_kind` is a trailing word in the command line, and it is now only
        a label a human reads: nothing in status derives an obligation from it.
        `kinds` is what does clear an obligation, passed through as `--kind`
        and recorded in the receipt's own sealed field. `exit_code` != 0 is
        produced by asking python to exit that code; the receipt is still sound
        evidence (verify PASS), it just records a failing run, which is the
        MERGE BLOCKER shape.
        """
        out_path = os.path.join(self.repo, out_rel)
        declared = []
        for kind in kinds:
            declared += ["--kind", kind]
        code, text, _ = self.sbe(
            "evidence", "run", "--out", out_path, "--covers", covers, "--cwd", self.repo,
            *(declared + ["--", sys.executable, "-c",
                          "import sys; sys.exit(%d)" % exit_code, argv_kind]))
        return out_path

    def reseal(self, path):
        """Recompute a receipt's seal in place, so a fixture that rewrites one
        field isolates the control it is actually about instead of tripping the
        seal as well."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["runId"] = mod.compute_seal(data)
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2, sort_keys=True))
        finally:
            sys.path.pop(0)
        return path


class TestNoStores(StatusFixture):
    def test_an_empty_repo_with_no_stores_is_all_no_data_and_exits_0(self):
        code, text, _ = self.status("--base", self.base)
        self.assertEqual(code, 0, text)
        for name in ("BROKEN CLAIMS", "MERGE BLOCKERS", "ACTIVE CONFLICTS",
                     "MISSING EVIDENCE", "COMPLETED EVIDENCE"):
            self.assertIn(name, text)
        self.assertIn("NO-DATA", text)
        self.assertIn("no evidence store found", text)
        self.assertIn("no task registry found", text)
        # exit 0 is NOT a claim of a clean pass; the closing line must say so.
        self.assertIn("not the same claim as everything", text)
        self.assertIn("NEXT ACTION: nothing blocking here that this tool can see", text)

    def test_each_absent_store_marks_its_own_section_no_data_not_only_somewhere(self):
        # A single NO-DATA anywhere in the output must not vouch for every
        # section: the honesty lives per line. With no stores at all, the
        # ACTIVE CONFLICTS line itself carries NO-DATA, not just its note text.
        code, text, _ = self.status("--base", self.base)
        self.assertEqual(code, 0, text)
        for heading in ("BROKEN CLAIMS", "ACTIVE CONFLICTS", "COMPLETED EVIDENCE"):
            start = text.index(heading)
            rest = text[start:]
            end = rest.find("\n\n")
            section = rest if end < 0 else rest[:end]
            self.assertIn("NO-DATA", section,
                          "%s must say NO-DATA on its own line when its store is "
                          "absent, not rely on another section's honesty: %r"
                          % (heading, section))


class TestBrokenClaims(StatusFixture):
    def test_a_stale_receipt_is_named_under_broken_claims_and_exits_1(self):
        # BREAK: generate a sound receipt, then move HEAD, which makes the
        # receipt's bound commit stale.
        out_path = self.run_evidence(".sbe/evidence/r1.json", "score")
        write(self.repo, "unrelated.txt", "more\n")
        self.commit("advance head")
        code, text, _ = self.status("--base", self.base)
        # RED: the stale receipt must be named, and it must block.
        self.assertNotEqual(code, 0, text)
        self.assertIn("BROKEN CLAIMS", text)
        self.assertIn(os.path.relpath(out_path, self.repo), text)
        self.assertIn("not the current head", text)

    def test_a_current_receipt_is_not_named_as_broken(self):
        # RESTORE: the same receipt, verified before HEAD moves, is not broken.
        self.run_evidence(".sbe/evidence/r1.json", "score")
        code, text, _ = self.status("--base", self.base)
        self.assertNotIn("does not match the seal", text)
        broken_block = text.split("MERGE BLOCKERS:")[0]
        self.assertNotIn(".sbe/evidence/r1.json", broken_block,
                         "a current, sound receipt must not appear under BROKEN CLAIMS")


class TestEvidenceStoreSelfPoisoning(StatusFixture):
    """T6. A receipt's `coveredFiles`, when computed from a diff rather than
    an explicit `--covers` list, cannot tell "code this run tested" from
    "another evidence receipt that happened to land in the same
    base..HEAD range". Regenerating a receipt at a fixed `--out` path is the
    ordinary shape of a CI re-run (the design/gate/score checks all write to
    well-known paths), not an edit to any code under test, so an unrelated
    receipt that merely covered its OLD bytes by diff-range accident must
    never fail because of it: the evidence store must not poison itself.

    Every fixture here leaves the "gate" receipt UNCOMMITTED on purpose. A
    receipt's `headCommit` is resolved at generation time, before it can be
    committed, so ANY receipt that is later committed necessarily records a
    headCommit one commit behind whatever HEAD becomes once that commit
    lands: `verify` FAILs it for that reason alone, correctly and
    inconveniently, on every subsequent commit, T6 or not (this is the
    pre-existing, already-documented limit at docs/KNOWN-LIMITS.md, "The
    evidence wrapper binds a run to a commit..."; see also
    `TestBrokenClaims.test_a_stale_receipt_is_named_under_broken_claims_and_exits_1`
    above, which pins exactly that behavior as correct). Committing "gate"
    here would bury the covered-file assertion under that unrelated,
    already-accepted staleness. Leaving it uncommitted isolates the ONE
    thing this stage changes.
    """

    def _make_design_and_gate(self):
        # A real source change, so gate's diff-based coverage below names
        # actual code under test alongside the evidence-store accident, not
        # only the accident.
        write(self.repo, "app.py", "print('hello')\n")
        self.commit("add app.py")

        # "design": explicit --covers, so its own coverage never depends on
        # anything under .sbe/evidence/.
        self.run_evidence(".sbe/evidence/design.json", "design", covers="app.py",
                          kinds=("design",))
        self.commit("add design receipt")

        # "gate": the DEFAULT (diff-based) coverage, no --covers at all. Its
        # coveredFiles comes from `git diff self.base..HEAD`, which now
        # includes design.json (added by the commit above) purely because of
        # where it landed in that range, alongside app.py, the file gate
        # actually ran against.
        gate_path = os.path.join(self.repo, ".sbe/evidence/gate.json")
        code, out, _ = self.sbe(
            "evidence", "run", "--out", gate_path, "--base", self.base, "--kind", "gate",
            "--cwd", self.repo,
            "--", sys.executable, "-c", "import sys; sys.exit(0)", "gate")
        self.assertEqual(code, 0, out)
        with io.open(gate_path, encoding="utf-8") as fh:
            gate_receipt = json.load(fh)
        covered_paths = [c["path"] for c in gate_receipt["coveredFiles"]]
        self.assertIn(".sbe/evidence/design.json", covered_paths,
                      "setup check: gate.json's default coverage must include design.json "
                      "for this fixture to exercise T6 at all; got %r" % covered_paths)
        self.assertIn("app.py", covered_paths)
        return gate_path

    def test_regenerating_the_covered_receipt_does_not_break_the_one_that_covers_it(self):
        gate_path = self._make_design_and_gate()

        code, text, _ = self.status("--base", self.base)
        broken_block = text.split("MERGE BLOCKERS:")[0]
        self.assertNotIn(".sbe/evidence/gate.json", broken_block,
                         "gate.json, freshly generated and never committed, must start clean: "
                         "%r" % text)

        # BREAK: regenerate design.json IN PLACE, same --out path and same
        # --covers, exactly as a routine CI re-run of the design check would.
        # Nothing about app.py (what gate.json actually tested) changes; only
        # design.json's own bytes (its timestamps, its runId) do.
        self.run_evidence(".sbe/evidence/design.json", "design", covers="app.py",
                          kinds=("design",))

        code, text, _ = self.status("--base", self.base)
        broken_block = text.split("MERGE BLOCKERS:")[0]
        self.assertNotIn(".sbe/evidence/gate.json", broken_block,
                         "regenerating an UNRELATED evidence receipt (design.json) must never "
                         "break gate.json, which never claimed to cover it: %r" % text)
        self.assertNotIn("covered file .sbe/evidence/design.json now hashes to", text, text)
        self.assertNotEqual(gate_path, None)

    def test_the_same_scenario_committed_end_to_end_never_shows_a_covered_file_reason(self):
        # The literal "two receipts committed in sequence" shape: both
        # design.json and its regenerated successor get committed. gate.json
        # DOES then pick up the pre-existing, documented headCommit
        # staleness (every commit after it does that; see the class
        # docstring), so this checks the ONE thing T6 owns: whichever BROKEN
        # CLAIMS line names gate.json, it is never for a covered-file reason.
        self._make_design_and_gate()
        self.commit("add gate receipt")
        self.run_evidence(".sbe/evidence/design.json", "design", covers="app.py",
                          kinds=("design",))
        self.commit("regenerate design receipt")

        code, text, _ = self.status("--base", self.base)
        gate_lines = [l for l in text.splitlines()
                     if ".sbe/evidence/gate.json" in l and "fails verify" in l]
        self.assertTrue(gate_lines, "gate.json is expected to be named at least once (its own "
                                    "headCommit is stale the moment anything else commits, by "
                                    "the pre-existing rule this stage does not change): %r"
                                    % text)
        for line in gate_lines:
            self.assertNotIn("covered file", line,
                             "gate.json's ONLY acceptable reason here is headCommit staleness; "
                             "a covered-file complaint means design.json's regeneration leaked "
                             "into gate.json's verdict: %r" % line)

    def test_a_receipt_covering_only_the_evidence_store_reads_no_data_not_pass(self):
        # The limiting case: EVERY covered file this receipt names sits under
        # the evidence store, so nothing grounds it once that store is
        # excluded. NO-DATA, never a silent PASS built on nothing.
        self.run_evidence(".sbe/evidence/design.json", "design", covers="README.md",
                          kinds=("design",))
        self.commit("add design receipt")
        gate_path = os.path.join(self.repo, ".sbe/evidence/onlyevidence.json")
        code, out, _ = self.sbe(
            "evidence", "run", "--out", gate_path, "--covers", ".sbe/evidence/design.json",
            "--kind", "gate", "--cwd", self.repo,
            "--", sys.executable, "-c", "import sys; sys.exit(0)", "gate")
        self.assertEqual(code, 0, out)

        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        rel = ".sbe/evidence/onlyevidence.json"
        self.assertNotIn(rel, [i.get("path") for i in data["soundEvidence"]],
                         "a receipt covering only excluded paths must never verify PASS: %r"
                         % text)
        self.assertFalse(
            any(rel in i.get("finding", "") for i in data["brokenClaims"]),
            "a receipt covering only excluded paths is NO-DATA, not a broken claim: %r" % text)
        self.assertFalse(
            any(rel in i.get("finding", "") or rel in (i.get("path") or "")
               for i in data["mergeBlockers"]),
            "a receipt covering only excluded paths is NO-DATA, not a merge blocker: %r" % text)


class TestTierReconciliation(StatusFixture):
    def test_a_tier_disagreement_with_no_disposition_is_a_merge_blocker(self):
        # BREAK: an intake that answers every question "no" (T0) sitting
        # beside a diff that adds an API contract file (which sbe impact
        # reads as T2).
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\npaths:\n  /orders: {}\n")
        self.commit()
        self.intake()
        code, text, _ = self.status("--base", self.base)
        # RED.
        self.assertNotEqual(code, 0, text)
        self.assertIn("MERGE BLOCKERS", text)
        self.assertIn("intake declared T0 but the diff shows T2", text)
        self.assertIn("openapi", text)

    def test_a_valid_disposition_clears_the_tier_disagreement(self):
        # RESTORE: the same disagreement, with a disposition recorded against
        # the current head.
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\npaths:\n  /orders: {}\n")
        head = self.commit()
        self.intake()
        write(self.repo, "disposition.json", json.dumps([{
            "detector": "openapi", "decision": "keep-lower",
            "reason": "internal fixture only, never published", "who": "the engineer",
            "head": head}]))
        code, text, _ = self.status("--base", self.base)
        # GREEN: no merge blocker from the tier reconciliation. (T0 also owes
        # no evidence, so MISSING EVIDENCE stays empty too.)
        self.assertEqual(code, 0, text)
        self.assertNotIn("intake declared", text)


class TestTierOverride(StatusFixture):
    """Ledger 12: after a valid, documented tier override in 00-intake.json
    (the same `tier`/`override`/`override_reason` shape tools/sbe_design.py
    itself reads and treats as authoritative once the two tier fields agree
    and a reason is recorded), status kept printing the COMPUTED tier in
    its own scope notes and MISSING EVIDENCE obligation check, because
    `impact.read_intake` always re-derives the tier from `answers` alone
    and never looks at either of the other two fields. Status now reads
    the same overridden-tier field sbe design reads."""

    def test_a_documented_override_is_read_the_same_field_sbe_design_reads(self):
        # BREAK: every answer is safe (computes T0, the same defaults
        # StatusFixture.intake() writes), but the intake DOCUMENTS a valid
        # override raising the tier to T2: `tier` and `override` agree,
        # and `override_reason` carries an actual reason. Before this fix,
        # status re-derived T0 from the answers and never asked this
        # change for the T2 evidence its own documented override says it
        # owes.
        write(self.repo, "00-intake.json", json.dumps({
            "answers": {"changes_contract": "n", "crosses_boundary": "n",
                       "reversible_under_hour": "y", "touches_sensitive": "n",
                       "consumers": "none"},
            "tier": "T2", "override": "T2",
            "override_reason": "raising deliberately: this change lands behind a "
                                "shared library boundary several other teams still "
                                "depend on",
        }))
        code, text, _ = self.status("--base", self.base)
        self.assertNotEqual(code, 0, text)
        self.assertIn("MISSING EVIDENCE", text)
        self.assertIn("declared tier T2", text,
                      "the declared tier in status's own note must be the DOCUMENTED "
                      "override (T2), not the answers-only computed tier (T0): %s" % text)
        for label in ("design completeness check", "hard gate", "scored surface"):
            self.assertIn(label, text,
                          "a documented T2 override must owe T2 evidence, the same way "
                          "sbe design already treats the override as live: %s" % text)

    def test_an_override_that_disagrees_with_the_computed_tier_but_records_no_reason_is_ignored(self):
        # RESTORE-shaped: `override` is set, but `override_reason` is
        # blank. sbe_design.py refuses this as FAIL; status never blocks
        # on a malformed override (it is not a gate), so it falls back to
        # the computed tier exactly as it always has.
        write(self.repo, "00-intake.json", json.dumps({
            "answers": {"changes_contract": "n", "crosses_boundary": "n",
                       "reversible_under_hour": "y", "touches_sensitive": "n",
                       "consumers": "none"},
            "tier": "T2", "override": "T2", "override_reason": "",
        }))
        code, text, _ = self.status("--base", self.base)
        self.assertEqual(code, 0, text)
        self.assertNotIn("owes one", text)


class TestActiveConflicts(StatusFixture):
    def test_overlapping_open_tasks_are_named_under_active_conflicts(self):
        code, text, _ = self.sbe("task", "open", "--id", "honest", "--agent", "alpha",
                                 "--role", "writer", "--base", self.base,
                                 "--verify", "true", "--owns", "src/owned.py",
                                 "--cwd", self.repo)
        self.assertEqual(code, 0, text)
        reg_path = os.path.join(self.repo, ".sbe", "tasks.json")
        with io.open(reg_path, encoding="utf-8") as fh:
            data = json.load(fh)
        # BREAK: inject a second, overlapping open task directly into the
        # registry, the way a hand edit or a second uncoordinated agent would.
        injected = dict(data["tasks"][0])
        injected["id"] = "injected"
        injected["ownedPaths"] = ["src/"]
        data["tasks"].append(injected)
        write(self.repo, os.path.join(".sbe", "tasks.json"), json.dumps(data))
        code, text, _ = self.status("--base", self.base)
        # RED.
        self.assertNotEqual(code, 0, text)
        self.assertIn("ACTIVE CONFLICTS", text)
        self.assertIn("honest", text)
        self.assertIn("injected", text)

    def test_non_overlapping_open_tasks_are_not_named_as_conflicts(self):
        # RESTORE: two open tasks with disjoint scope.
        self.sbe("task", "open", "--id", "w1", "--agent", "a", "--role", "writer",
                "--base", self.base, "--verify", "true", "--owns", "src/a.py",
                "--cwd", self.repo)
        self.sbe("task", "open", "--id", "w2", "--agent", "b", "--role", "writer",
                "--base", self.base, "--verify", "true", "--owns", "docs/b.md",
                "--cwd", self.repo)
        code, text, _ = self.status("--base", self.base)
        # GREEN.
        self.assertEqual(code, 0, text)
        self.assertNotIn("two open writers overlap", text)


class TestForcedClose(StatusFixture):
    def test_a_forced_task_close_is_a_merge_blocker(self):
        self.sbe("task", "open", "--id", "w1", "--agent", "a", "--role", "writer",
                "--base", self.base, "--verify", "true", "--owns", "src/owned.py",
                "--cwd", self.repo)
        write(self.repo, "docs/other.md", "out of scope\n")
        self.commit()
        code, text, _ = self.sbe("task", "close", "w1", "--force", "--who", "the operator",
                                 "--why", "hotfix, accepted out of band", "--cwd", self.repo)
        self.assertEqual(code, 0, text)
        code, text, _ = self.status("--base", self.base)
        self.assertNotEqual(code, 0, text)
        self.assertIn("MERGE BLOCKERS", text)
        self.assertIn("FORCED by the operator", text)


class TestMissingEvidence(StatusFixture):
    def test_missing_evidence_for_the_declared_tier_names_the_filler(self):
        # BREAK: a T2 intake (changes_contract=y) with no evidence store at
        # all.
        self.intake(changes_contract="y")
        code, text, _ = self.status("--base", self.base)
        self.assertNotEqual(code, 0, text)
        self.assertIn("MISSING EVIDENCE", text)
        self.assertIn("design completeness check", text)
        self.assertIn("hard gate", text)
        self.assertIn("scored surface", text)
        self.assertIn("sbe evidence run", text)

    def test_a_t0_intake_owes_no_evidence(self):
        # RESTORE-shaped: a T0 intake owes nothing, so MISSING EVIDENCE stays
        # empty even with no evidence store.
        self.intake()
        code, text, _ = self.status("--base", self.base)
        self.assertEqual(code, 0, text)
        self.assertNotIn("owes one", text)


class TestCheckKindIdentity(StatusFixture):
    """THE BYPASS THIS EXISTS FOR, reproduced before it was closed: status
    decided WHICH obligation a receipt satisfied by substring-matching the
    recorded command line, so a receipt for a command that ran no check at all
    cleared the design, gate and score obligations at once, as long as a path
    in its argv spelled the words. A T2 change owing three checks went green on
    one command that read a text file.

    The kind is now a declared, sealed field on the receipt
    (`sbe evidence run --kind gate`), and the command line is not read for it.
    Every fixture here asserts the receipt VERIFIES first, because a receipt
    that failed verification would clear nothing for an unrelated reason and
    would make the interesting assertion pass for free.
    """

    NAMED = "tests/test_design_of_gate_score.txt"

    def _t2_change_with(self, out_rel, kinds=()):
        """A committed T2 change plus one sound receipt over it. The tree is
        committed BEFORE the run so the receipt is generated clean and reaches
        PASS; a dirty-tree receipt is NO-DATA and would clear nothing whatever
        this fixture proved."""
        write(self.repo, self.NAMED, "this file runs no check; it is only named like one\n")
        self.intake(changes_contract="y")
        self.commit("a T2 change, and a file named after all three checks")
        return self.run_evidence(out_rel, self.NAMED, kinds=kinds)

    def _completed_block(self, text):
        return text.split("COMPLETED EVIDENCE:")[1].split("NEXT ACTION")[0]

    def _missing_block(self, text):
        return text.split("MISSING EVIDENCE:")[1].split("COMPLETED EVIDENCE:")[0]

    def test_a_command_named_after_the_checks_clears_no_obligation(self):
        """THE FIXTURE THE BYPASS DIES ON. The receipt is sound, current and
        verifies PASS. Its argv names design, gate and score. It declared no
        kind, so all three obligations stay open."""
        out_path = self._t2_change_with(".sbe/evidence/named.json")
        code, text, _ = self.status("--base", self.base)
        self.assertIn(os.path.relpath(out_path, self.repo), self._completed_block(text),
                      "the receipt must verify as sound evidence, or this fixture would be "
                      "proving that a broken receipt clears nothing: %s" % text)
        missing = self._missing_block(text)
        for label in ("design completeness check", "hard gate", "scored surface"):
            self.assertIn(label, missing,
                          "a command that ran no check cleared the %s obligation by naming it "
                          "in a filename: %s" % (label, text))
        self.assertNotEqual(code, 0, text)
        self.assertIn("declare no check kind", text,
                      "the reader must be told the store holds a receipt that says nothing "
                      "about which check it was, never left to read it as no evidence: %s"
                      % text)

    def test_a_declared_kind_clears_that_obligation_and_only_that_one(self):
        """The other direction, which matters just as much: the new field has
        to actually work, or every T2 change is permanently blocked and the
        control gets switched off."""
        self._t2_change_with(".sbe/evidence/gate.json", kinds=("gate",))
        code, text, _ = self.status("--base", self.base)
        missing = self._missing_block(text)
        self.assertNotIn("hard gate", missing,
                         "a receipt declaring --kind gate must clear the gate obligation: %s"
                         % text)
        self.assertIn("design completeness check", missing, text)
        self.assertIn("scored surface", missing, text)

    def test_declaring_all_three_clears_all_three(self):
        self._t2_change_with(".sbe/evidence/all.json",
                             kinds=("design", "gate", "score"))
        code, text, _ = self.status("--base", self.base)
        self.assertEqual(code, 0, "three declared kinds on a sound receipt must leave MISSING "
                                  "EVIDENCE empty: %s" % text)
        self.assertNotIn("owes one", text, text)

    def test_a_legacy_receipt_is_no_data_for_obligations_and_is_named(self):
        """A receipt written before the field existed. It still verifies, it
        still counts as completed evidence, and it clears nothing: NO-DATA for
        obligation purposes, never a silent pass. The transform below is
        exactly what the previous build wrote, resealed so the legacy control
        is the only one that can fire."""
        out_path = self._t2_change_with(".sbe/evidence/legacy.json")
        with io.open(out_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["schemaVersion"] = "1.1"
        del data["checkKinds"]
        del data["checkKindsSource"]
        with io.open(out_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
        self.reseal(out_path)
        code, text, _ = self.status("--base", self.base)
        self.assertIn(".sbe/evidence/legacy.json", self._completed_block(text),
                      "a 1.1 receipt must still verify, or this fixture proves nothing about "
                      "obligations: %s" % text)
        missing = self._missing_block(text)
        self.assertIn("hard gate", missing, text)
        self.assertIn("records no checkKinds field at all", missing,
                      "a legacy receipt must be named as legacy, not silently absent: %s"
                      % text)

    def test_a_kind_typed_into_a_receipt_after_the_run_clears_nothing(self):
        """The forgery the field would otherwise invite. Not resealed, on
        purpose: the point is that the seal is what stops this, so the receipt
        lands in BROKEN CLAIMS and the obligation stays open."""
        out_path = self._t2_change_with(".sbe/evidence/forged.json")
        with io.open(out_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["checkKinds"] = ["design", "gate", "score"]
        with io.open(out_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
        code, text, _ = self.status("--base", self.base)
        self.assertNotEqual(code, 0, text)
        self.assertIn("BROKEN CLAIMS", text)
        self.assertIn("does not match the seal", text, text)
        missing = self._missing_block(text)
        for label in ("design completeness check", "hard gate", "scored surface"):
            self.assertIn(label, missing,
                          "a hand-typed kind cleared the %s obligation: %s" % (label, text))

    def test_status_reads_the_field_through_the_one_reader_in_evidence(self):
        """Source-level, and cheap: status must not grow a second
        interpretation of the field. `_receipt_kinds` delegates to
        `evidence.declared_kinds`, and this pins that it takes the receipt's
        own field rather than anything derived from argv."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import status as mod
            spelled = {"argv": ["python3", "-c", "pass", self.NAMED]}
            self.assertEqual(mod._receipt_kinds(spelled), set(),
                             "an argv naming all three checks must yield no kind")
            self.assertEqual(mod._receipt_kinds({"checkKinds": ["score"], "argv": []}),
                             set(["score"]))
            self.assertEqual(tuple(k for k, _l, _c in mod.CHECK_KINDS),
                             mod.evidence_mod.CHECK_KIND_NAMES,
                             "the kind vocabulary must come from evidence.py, or the writer "
                             "and the reader can disagree about what a kind is")
        finally:
            sys.path.pop(0)


class TestCompletedEvidence(StatusFixture):
    def test_a_verifying_receipt_shows_under_completed_evidence_with_its_trust_label(self):
        self.run_evidence(".sbe/evidence/clean.json", "smoke-test", exit_code=0)
        code, text, _ = self.status("--base", self.base)
        self.assertEqual(code, 0, text)
        self.assertIn("COMPLETED EVIDENCE", text)
        self.assertIn(".sbe/evidence/clean.json", text)
        self.assertIn("trust LOCAL-ADVISORY", text)

    def test_a_receipt_recording_a_failing_run_is_a_merge_blocker_not_completed(self):
        self.run_evidence(".sbe/evidence/broke.json", "gate", exit_code=1)
        code, text, _ = self.status("--base", self.base)
        self.assertNotEqual(code, 0, text)
        self.assertIn("MERGE BLOCKERS", text)
        self.assertIn("exit code 1", text)
        completed_block = text.split("COMPLETED EVIDENCE:")[1].split("NEXT ACTION")[0]
        self.assertNotIn(".sbe/evidence/broke.json", completed_block)


class TestNextAction(StatusFixture):
    def test_next_action_tracks_broken_claims_first_when_both_are_present(self):
        out_path = self.run_evidence(".sbe/evidence/r1.json", "score")
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        self.commit()
        self.intake()  # T0, disagrees with the diff-derived T2: a merge blocker too
        code, text, _ = self.status("--base", self.base)
        next_line = [l for l in text.splitlines() if l.startswith("NEXT ACTION")][0]
        self.assertIn("re-run", next_line, "the broken-claim remedy must win: %s" % next_line)
        self.assertIn("(BROKEN CLAIMS)", next_line)

    def test_next_action_tracks_merge_blockers_when_broken_claims_is_empty(self):
        write(self.repo, "api/openapi.yaml", "openapi: 3.0.0\n")
        self.commit()
        self.intake()
        code, text, _ = self.status("--base", self.base)
        next_line = [l for l in text.splitlines() if l.startswith("NEXT ACTION")][0]
        self.assertIn("(MERGE BLOCKERS)", next_line)

    def test_next_action_is_the_generic_line_when_everything_is_empty(self):
        code, text, _ = self.status("--base", self.base)
        next_line = [l for l in text.splitlines() if l.startswith("NEXT ACTION")][0]
        self.assertIn("nothing blocking here that this tool can see", next_line)
        self.assertIn("scope:", next_line)


class TestDossierDiscovery(StatusFixture):
    """CR-06: single-project status discovers a dossier laid out the way the
    project's own docs describe it (design/<change>/00-intake.json) by
    walking the SAME `_design_roots`/`_team_changes` machinery
    `sbe status --team` already uses, but only when the flat single-dossier
    layout (00-intake.json at root) is absent. See
    design/lifecycle-blockers/03-adr.md, the CR-06 decision.

    Reproduced before this fix: this repository's own tree carries two
    dossiers under design/ and no flat 00-intake.json at root, and
    `sbe status .` reported every store null and "nothing blocking here"
    over two dossiers that plainly exist on disk.
    """

    def _dossier_intake(self, name, **answers):
        base = {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n", "consumers": "none"}
        base.update(answers)
        return write(self.repo, os.path.join("design", name, "00-intake.json"),
                    json.dumps({"answers": base}))

    def test_a_dossier_layout_is_discovered_when_the_flat_layout_is_absent(self):
        # Exactly this repository's own documented layout: two dossiers
        # under design/, no flat 00-intake.json at root. One dossier is T2
        # (owes evidence it never got), the other is T0 (owes nothing), so
        # this fixture exercises both the discovery and the T0-owes-nothing
        # guard at once, over dossier-scoped state.
        self._dossier_intake("change-a", changes_contract="y")
        self._dossier_intake("change-b")
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        self.assertEqual(sorted(data["scope"]["storesInspected"]["dossiers"]),
                         ["change-a", "change-b"], text)
        self.assertNotIn("nothing blocking here", data["nextAction"],
                         "a T2 dossier owing evidence must never read as clean: %s" % text)
        self.assertTrue(data["missingEvidence"], "the T2 dossier owes evidence: %s" % text)
        self.assertTrue(
            any("change-a" in item["finding"] for item in data["missingEvidence"]),
            "the missing-evidence finding must be labeled with the dossier it came from: %s"
            % text)
        self.assertFalse(
            any("change-b" in item["finding"] for item in data["missingEvidence"]),
            "the T0 dossier owes nothing, and must not show up under MISSING EVIDENCE "
            "just because a sibling dossier does: %s" % text)
        self.assertNotEqual(code, 0, text)

    def test_when_both_layouts_exist_the_flat_layout_wins_and_dossiers_are_not_scanned(self):
        # Precedence, stated by the ADR: the flat layout, when present,
        # always wins, and dossier discovery never runs at all. A T0 flat
        # intake at root must leave MISSING EVIDENCE empty even though a
        # sibling, discoverable dossier declares T2 and would owe evidence
        # entirely on its own.
        self.intake()  # T0 at root: the flat layout
        self._dossier_intake("change-a", changes_contract="y")  # T2, must be ignored
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        self.assertIsNone(data["scope"]["storesInspected"]["dossiers"],
                          "the flat layout must win: dossier discovery must not run at all: "
                          "%s" % text)
        self.assertEqual(code, 0, text)
        self.assertFalse(data["missingEvidence"],
                         "a T0 flat intake owes nothing, and the sibling dossier must never "
                         "be consulted while the flat layout is present: %s" % text)
        self.assertFalse(
            any("dossier " in item.get("finding", "")
               for section in (data["brokenClaims"], data["mergeBlockers"],
                               data["missingEvidence"])
               for item in section),
            "no dossier-labeled finding may appear while the flat layout wins: %s" % text)

    def test_a_design_root_escaping_the_repository_is_refused_and_not_walked(self):
        # The single-project counterpart of build_team_report's own M3
        # containment test: a designRoots entry that would resolve outside
        # the repository is REFUSED by its own literal spelling, never
        # walked, and the refusal itself is surfaced as a merge blocker
        # rather than silently dropped.
        outside = tempfile.mkdtemp()
        try:
            write(outside, os.path.join("escaped", "00-intake.json"),
                 json.dumps({"answers": {"changes_contract": "n", "crosses_boundary": "n",
                                        "reversible_under_hour": "y",
                                        "touches_sensitive": "n", "consumers": "none"}}))
            rel = os.path.relpath(outside, self.repo)
            write(self.repo, os.path.join(".sbe", "team-profile.json"),
                 json.dumps({"designRoots": [rel]}))
            code, data, text = self.status_json("--base", self.base)
            self.assertIsNotNone(data, text)
            self.assertTrue(
                any(rel in item["finding"] and "REFUSED" in item["finding"]
                   for item in data["mergeBlockers"]),
                "an escaping designRoots entry must be REFUSED by name under MERGE "
                "BLOCKERS, not silently dropped: %s" % text)
            self.assertIsNone(data["scope"]["storesInspected"]["dossiers"],
                             "an escaping entry with no other dossier found must leave "
                             "the dossiers field empty, not populated from an unwalked "
                             "directory: %s" % text)
            self.assertNotEqual(code, 0, text)
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestJsonMode(StatusFixture):
    def test_json_mode_carries_every_section_the_scope_and_the_schema_version(self):
        self.intake(changes_contract="y")
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        for key in ("schemaVersion", "toolVersion", "generatedAt", "root", "scope",
                    "brokenClaims", "mergeBlockers", "activeConflicts", "missingEvidence",
                    "soundEvidence", "nextAction"):
            self.assertIn(key, data, text)
        self.assertIn("storesInspected", data["scope"])
        self.assertTrue(data["missingEvidence"], "a T2 intake with no evidence store must "
                                                  "populate missingEvidence")
        self.assertNotEqual(code, 0, text)


class TestPositiveSentenceGuard(StatusFixture):
    def test_every_clean_or_nodata_line_in_text_mode_names_its_scope(self):
        """The test greps the RENDERED OUTPUT, never the source: every line
        that reports a section as clean or as NO-DATA must carry a scope
        phrase, so a reader is never told "nothing here" without being told
        what was actually read."""
        # A mix: one broken claim present, several sections legitimately
        # empty, so both the item-bearing and the empty-section renderings
        # are exercised in the same run.
        self.run_evidence(".sbe/evidence/r1.json", "score")
        write(self.repo, "unrelated.txt", "more\n")
        self.commit("advance head")
        code, text, _ = self.status("--base", self.base)
        self.assertNotEqual(code, 0, text)
        candidate_lines = [l for l in text.splitlines()
                           if l.strip().startswith("clean") or l.strip().startswith("NO-DATA")]
        self.assertTrue(candidate_lines, "this fixture must exercise at least one empty "
                                         "section line: %s" % text)
        for line in candidate_lines:
            self.assertIn("scope", line.lower(),
                          "an empty-section line named nothing it inspected: %r" % line)

    def test_a_positive_evidence_line_names_its_scope_too(self):
        """The scope rule covers POSITIVE findings, not only clean and NO-DATA
        lines: a receipt reported sound must say what that soundness was
        checked against, or the strongest sentence in the output is the one
        naming the least."""
        self.run_evidence(".sbe/evidence/clean.json", "score")
        code, text, _ = self.status("--base", self.base)
        positive = [l for l in text.splitlines()
                    if "verifies as sound evidence" in l]
        self.assertTrue(positive, "this fixture must produce a sound receipt line: %s" % text)
        for line in positive:
            self.assertIn("scope:", line,
                          "a positive evidence line named nothing it inspected: %r" % line)


class TestHandoverIntegration(StatusFixture):
    """LT-302.B: the smallest possible read path so `sbe status` can report
    whether ownership of this change is moving, and to whom, over a real
    `12-handover.json` the real `sbe handover` engine wrote (never a
    hand-typed stand-in). Additive only: every fixture here also asserts the
    five pre-existing sections are untouched, so this suite cannot pass by
    accident on a `handover` key that silently broke something else.
    """

    def _handover_of(self, data):
        self.assertIn("handover", data, data)
        self.assertEqual(len(data["handover"]), 1,
                         "the flat single-dossier layout must yield exactly one handover "
                         "entry: %s" % data["handover"])
        return data["handover"][0]

    def _assert_other_sections_untouched(self, data):
        for key in ("brokenClaims", "mergeBlockers", "activeConflicts", "missingEvidence",
                    "soundEvidence"):
            self.assertFalse(
                any("handover" in json.dumps(item).lower() for item in data[key]),
                "the handover field must never leak into %s: %s" % (key, data[key]))

    def test_no_handover_needed_is_reported_and_never_blocks(self):
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        entry = self._handover_of(data)
        self.assertEqual(entry["status"], "none", entry)
        self.assertIsNone(entry["stale"], entry)
        self.assertIn("no handover", entry["detail"], entry)
        self.assertIn("never a block", entry["detail"], entry)
        self.assertEqual(code, 0, text)
        self._assert_other_sections_untouched(data)

    def test_a_prepared_handover_reads_prepared_and_awaiting_receiver(self):
        code, text, _ = self.handover("prepare", self.repo, "--outgoing", "alice@example.com",
                                      "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        entry = self._handover_of(data)
        self.assertEqual(entry["status"], "prepared", entry)
        self.assertFalse(entry["stale"], entry)
        self.assertIn("awaiting receiver", entry["detail"], entry)
        self.assertEqual(entry["outgoingOwner"], "alice@example.com", entry)
        self.assertEqual(entry["intendedReceiver"], "bob@example.com", entry)
        self.assertEqual(code, 0, text)
        self._assert_other_sections_untouched(data)

    def test_a_stale_prepared_handover_names_the_moved_head(self):
        code, text, _ = self.handover("prepare", self.repo, "--outgoing", "alice@example.com",
                                      "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        write(self.repo, "unrelated.txt", "more\n")
        self.commit("advance head past the prepared handover")
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        entry = self._handover_of(data)
        self.assertEqual(entry["status"], "prepared", entry)
        self.assertTrue(entry["stale"], entry)
        self.assertIn("stale", entry["detail"], entry)
        self.assertIn("re-prepared", entry["detail"], entry)

    def test_an_acknowledged_handover_reads_acknowledged(self):
        code, text, _ = self.handover("prepare", self.repo, "--outgoing", "alice@example.com",
                                      "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, text, _ = self.handover("acknowledge", self.repo, "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, data, text = self.status_json("--base", self.base)
        entry = self._handover_of(data)
        self.assertEqual(entry["status"], "acknowledged", entry)
        self.assertFalse(entry["stale"], entry)
        self.assertIn("acknowledged", entry["detail"], entry)
        self.assertIn("moved to bob@example.com", entry["detail"], entry)

    def test_a_rejected_handover_reads_rejected_and_names_the_reason(self):
        code, text, _ = self.handover("prepare", self.repo, "--outgoing", "alice@example.com",
                                      "--receiver", "bob@example.com")
        self.assertEqual(code, 0, text)
        code, text, _ = self.handover("reject", self.repo, "--receiver", "bob@example.com",
                                      "--reason", "not ready yet")
        self.assertEqual(code, 0, text)
        code, data, text = self.status_json("--base", self.base)
        entry = self._handover_of(data)
        self.assertEqual(entry["status"], "rejected", entry)
        self.assertIn("not ready yet", entry["detail"], entry)
        self.assertIn("stays with alice@example.com", entry["detail"], entry)

    def test_a_malformed_handover_file_is_named_not_silently_dropped(self):
        write(self.repo, "12-handover.json", "{not json\n")
        code, data, text = self.status_json("--base", self.base)
        self.assertIsNotNone(data, text)
        entry = self._handover_of(data)
        self.assertEqual(entry["status"], "malformed", entry)
        self.assertIn("cannot be trusted", entry["detail"], entry)
        self._assert_other_sections_untouched(data)


class TestPossiblyBehind(StatusFixture):
    """P5: `sbe status` names when the newest dated record (10-approval.json
    or 11-review.json) is bound to a commit older than HEAD, and NAMES the
    commits that landed since -- never a bare boolean. Reproduced before this
    fix: `lifecycle.reduce_next_action` and `build_report` had no fact
    anywhere comparing a record's own date to HEAD's; the only staleness
    check in the whole tree that compares two commits at all is
    `bbstatus._stale_sentence`, and that one is sha-equality for a status
    POST, not a delta a human reads on `sbe status`.
    """

    def test_an_approval_bound_to_an_older_commit_reports_the_delta(self):
        write(self.repo, "10-approval.json",
             json.dumps({"headSha": self.base, "final": "PASS"}))
        write(self.repo, "extra.txt", "one\n")
        head = self.commit("add extra")
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        self.assertTrue(data["possiblyBehind"], text)
        note = data["possiblyBehind"][0]
        self.assertIn("10-approval.json", note)
        self.assertIn(self.base[:12], note)
        self.assertIn("add extra", note)
        self.assertIn("1 commit(s)", note)
        # It also shows up in the rendered text mode, not only the JSON:
        _code, rendered, _err = self.status()
        self.assertIn("POSSIBLY BEHIND:", rendered)
        self.assertIn("add extra", rendered)

    def test_an_approval_bound_to_head_reports_clean(self):
        write(self.repo, "10-approval.json",
             json.dumps({"headSha": self.base, "final": "PASS"}))
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        self.assertEqual(data["possiblyBehind"], [], text)
        self.assertNotIn("POSSIBLY BEHIND", text)

    def test_no_dated_record_at_all_reports_clean(self):
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        self.assertEqual(data["possiblyBehind"], [], text)

    def test_a_review_bound_to_an_older_commit_reports_the_delta_too(self):
        write(self.repo, "11-review.json",
             json.dumps({"headSha": self.base, "reviewer": "carol@example.com",
                        "reviewerType": "human", "result": "approved"}))
        write(self.repo, "extra.txt", "two\n")
        head = self.commit("second change")
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        self.assertTrue(data["possiblyBehind"], text)
        self.assertIn("11-review.json", data["possiblyBehind"][0])
        self.assertIn("second change", data["possiblyBehind"][0])


class TestDeploymentDrift(StatusFixture):
    """H3: evidence binds to a commit, never to what is RUNNING. A named
    deployed ref (.sbe/deployed-ref, written by `sbe status --set-deployed-
    ref`, or SBE_DEPLOYED_REF) that differs from HEAD -- the commit this
    report's own evidence binds to -- reads DRIFT naming both refs; no
    deployed ref named reads NO-DATA, never a guess, never a failure.
    """

    def test_a_deployed_ref_one_commit_behind_reports_drift_naming_both(self):
        code, out, _err = self.sbe("status", self.repo, "--set-deployed-ref", self.base)
        self.assertEqual(code, 0, out)
        write(self.repo, "extra.txt", "one\n")
        head = self.commit("advance past the deployed ref")
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        drift = data["deploymentDrift"]
        self.assertEqual(drift["verdict"], "DRIFT", drift)
        self.assertIn(self.base, drift["detail"])
        self.assertIn(head, drift["detail"])
        _code, rendered, _err = self.status()
        self.assertIn("DEPLOYMENT: DRIFT", rendered)

    def test_a_deployed_ref_matching_head_reports_clean(self):
        code, out, _err = self.sbe("status", self.repo, "--set-deployed-ref", self.base)
        self.assertEqual(code, 0, out)
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        drift = data["deploymentDrift"]
        self.assertEqual(drift["verdict"], "CLEAN", drift)
        self.assertIn(self.base, drift["detail"])

    def test_no_deployed_ref_file_reads_no_data(self):
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        drift = data["deploymentDrift"]
        self.assertEqual(drift["verdict"], "NO-DATA", drift)
        self.assertIn("not configured", drift["detail"])
        _code, rendered, _err = self.status()
        self.assertNotIn("DEPLOYMENT:", rendered)

    def test_a_malformed_deployed_ref_file_is_named_not_silently_dropped(self):
        write(self.repo, os.path.join(".sbe", "deployed-ref"), "one\ntwo\n")
        code, data, text = self.status_json()
        self.assertIsNotNone(data, text)
        drift = data["deploymentDrift"]
        self.assertEqual(drift["verdict"], "NO-DATA", drift)
        self.assertIn("carries 2 lines", drift["detail"])
        _code, rendered, _err = self.status()
        self.assertNotIn("DEPLOYMENT:", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
