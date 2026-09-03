#!/usr/bin/env python3
"""Calibration for tools/bm_vault_enrich.py, WBS VB10-04: the LLM enrichment lane.

The property under test is the row's own observable: a machine draft lands as
a lifecycle candidate, marked machine-drafted with its model recorded, never
auto-published; canonical-only recall withholds it until a named human runs
the EXISTING promotions ceremony (tools/bm_vault_promotions.py, never
reimplemented here); a credential-bearing or unattributed draft refuses at
the gate, writing nothing; and a drafting model can never corroborate its
own draft.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_enrich as enrich    # noqa: E402
import bm_vault_lifecycle as lc     # noqa: E402
import bm_vault_promotions as promo  # noqa: E402


def note(body="a target note", promotion=None):
    lines = ["---", "type: reference", "status: open"]
    if promotion:
        lines.append("promotion: %s" % promotion)
    lines += ["---", "", "# %s" % body]
    return "\n".join(lines) + "\n"


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-enrich-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._write("target.md", note())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _text(self, relpath):
        with open(os.path.join(self.vault, relpath), encoding="utf-8") as fh:
            return fh.read()

    def _draft(self, **overrides):
        kwargs = dict(vault=self.vault, note_ident="target.md", field="description",
                      value="a machine-drafted description of the target note",
                      model="test-model-x")
        kwargs.update(overrides)
        return enrich.file_draft(**kwargs)


class ADraftLandsAsACandidateAndIsInvisibleUntilPromoted(VaultFixture):
    def test_draft_is_written_as_candidate_with_model_recorded(self):
        ok, message, rel = self._draft()
        self.assertTrue(ok, message)
        self.assertTrue(rel.startswith("00-Inbox/"), rel)
        text = self._text(rel)
        state, _record, problems = lc.read_promotion(text)
        self.assertEqual(state, "candidate")
        self.assertEqual(problems, [])
        self.assertIn("drafter_kind: machine", text)
        self.assertIn("drafting_model: test-model-x", text)
        self.assertIn("target_note: target.md", text)

    def test_draft_carries_author_of_record_for_separation_of_duties(self):
        # V14.1: promo.cmd_promote refuses fail-closed when a candidate has
        # no author: field. The drafting model is this module's one
        # never-empty identity, so it must land as author: too.
        ok, message, rel = self._draft()
        self.assertTrue(ok, message)
        text = self._text(rel)
        self.assertIn("author: test-model-x", text)
        self.assertEqual(promo._read_author(text), "test-model-x")

    def test_canonical_only_recall_does_not_serve_a_bare_candidate(self):
        ok, _message, _rel = self._draft()
        self.assertTrue(ok)
        rows = enrich.list_drafts(self.vault, "target.md", canonical_only=True)
        self.assertEqual(rows, [])
        # It IS visible to an ordinary (non-canonical-only) listing.
        rows_all = enrich.list_drafts(self.vault, "target.md")
        self.assertEqual(len(rows_all), 1)

    def test_after_promotion_it_is_served_and_the_promoter_is_recorded(self):
        ok, _message, rel = self._draft()
        self.assertTrue(ok)
        rc = promo.cmd_promote(self.vault, rel, "validated", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 0)
        rc = promo.cmd_promote(self.vault, rel, "canonical", "khalil",
                                "2026-08-30", apply_changes=True)
        self.assertEqual(rc, 0)
        rows = enrich.list_drafts(self.vault, "target.md", canonical_only=True)
        self.assertEqual(len(rows), 1)
        served_rel, state, model, kind = rows[0]
        self.assertEqual(served_rel, rel)
        self.assertEqual(state, "canonical")
        self.assertEqual(model, "test-model-x")
        self.assertEqual(kind, "machine")
        _state, record, _problems = lc.read_promotion(self._text(rel))
        self.assertEqual(record["promoted_by"], "khalil")


class CALIBRATION_canonical_only_actually_filters(VaultFixture):
    """Break counts_as_canonical to accept everything and watch the
    invisible-until-promoted test fail, proving that test is not a tautology."""

    def test_breaking_counts_as_canonical_serves_the_unpromoted_draft(self):
        ok, _message, _rel = self._draft()
        self.assertTrue(ok)
        real = lc.counts_as_canonical
        lc.counts_as_canonical = lambda state, problems: True
        try:
            rows = enrich.list_drafts(self.vault, "target.md", canonical_only=True)
            self.assertEqual(len(rows), 1, "with the guard broken the bare "
                              "candidate slips through, proving the real test bites")
        finally:
            lc.counts_as_canonical = real


class ACredentialBearingDraftRefusesAtTheGate(VaultFixture):
    def test_a_planted_credential_shape_refuses_and_writes_nothing(self):
        # Assembled at runtime, never a literal secret-shaped string in this
        # file's own source.
        planted = "sk-" + "x" * 20
        before = set(os.listdir(os.path.join(self.vault, "00-Inbox"))) \
            if os.path.isdir(os.path.join(self.vault, "00-Inbox")) else set()
        ok, message, rel = self._draft(value="see credential %s in the logs" % planted)
        self.assertFalse(ok)
        self.assertIn("credential", message)
        self.assertIsNone(rel)
        after_dir = os.path.join(self.vault, "00-Inbox")
        after = set(os.listdir(after_dir)) if os.path.isdir(after_dir) else set()
        self.assertEqual(before, after, "a refused draft must not write anything")


class CALIBRATION_the_credential_gate_actually_gates(VaultFixture):
    """Swap the reused hard gate for one that always passes; the credential
    test above must then fail, proving it exercises the real gate."""

    def test_breaking_hard_gate_lets_the_credential_through(self):
        real = enrich.intake.hard_gate
        enrich.intake.hard_gate = lambda text, deny_list: (True, None)
        try:
            planted = "sk-" + "y" * 20
            ok, _message, rel = self._draft(value="token %s" % planted)
            self.assertTrue(ok, "with the gate broken the credential-bearing "
                             "draft must be admitted, proving the real gate bites")
            self.assertIsNotNone(rel)
        finally:
            enrich.intake.hard_gate = real


class ADraftWithNoModelIdRefuses(VaultFixture):
    def test_empty_model_refuses_and_writes_nothing(self):
        ok, message, rel = self._draft(model="")
        self.assertFalse(ok)
        self.assertIn("REFUSED", message)
        self.assertIsNone(rel)

    def test_none_model_refuses(self):
        ok, message, rel = self._draft(model=None)
        self.assertFalse(ok)
        self.assertIsNone(rel)

    def test_unresolvable_note_is_no_data(self):
        ok, message, rel = self._draft(note_ident="no-such-note.md")
        self.assertFalse(ok)
        self.assertTrue(message.startswith("NO-DATA"), message)
        self.assertIsNone(rel)

    def test_unknown_field_refuses(self):
        ok, message, rel = self._draft(field="summary")
        self.assertFalse(ok)
        self.assertIsNone(rel)

    def test_empty_value_refuses(self):
        ok, message, rel = self._draft(value="   ")
        self.assertFalse(ok)
        self.assertIsNone(rel)


class TheSelfEchoCheckRefusesACorroborationFromTheDraftingModelItself(unittest.TestCase):
    def test_the_same_model_never_corroborates_itself(self):
        self.assertFalse(enrich.corroborates("model-a", "model-a"))

    def test_two_different_models_can_corroborate(self):
        self.assertTrue(enrich.corroborates("model-a", "model-b"))

    def test_a_missing_model_id_never_corroborates(self):
        self.assertFalse(enrich.corroborates(None, "model-b"))
        self.assertFalse(enrich.corroborates("model-a", None))
        self.assertFalse(enrich.corroborates("", ""))


class ThePropose_editCommandUsesTheSameGates(VaultFixture):
    """propose-edit is the serve surface's one write action; it must run the
    exact same hard gate and land in the exact same lifecycle shape as
    enrich-draft, never a second, looser path."""

    def test_propose_edit_admits_a_clean_draft_as_a_candidate(self):
        ap = enrich._build_parser()
        args = ap.parse_args(["propose-edit", "--vault", self.vault,
                               "--note", "target.md", "--field", "description",
                               "--value", "a proposed edit", "--model", "test-model-x"])
        rc = enrich.cmd_file(args)
        self.assertEqual(rc, 0)
        rows = enrich.list_drafts(self.vault, "target.md")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "candidate")

    def test_propose_edit_refuses_a_planted_credential(self):
        ap = enrich._build_parser()
        planted = "sk-" + "z" * 20
        args = ap.parse_args(["propose-edit", "--vault", self.vault,
                               "--note", "target.md", "--field", "description",
                               "--value", "leaked %s" % planted,
                               "--model", "test-model-x"])
        rc = enrich.cmd_file(args)
        self.assertEqual(rc, 1)
        rows = enrich.list_drafts(self.vault, "target.md")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
