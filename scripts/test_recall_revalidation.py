"""E74: a recalled lesson is revalidated against current truth before it is
shown as advice.

WHY THIS EXISTS. The vault recalls lessons at point of need (products/
brothermode/tools/vault_recall_hook.py) and nothing checked whether a
lesson's own claim still held in the tree, so a once-true lesson could
override current truth with confidence. A note now carries an explicit
applies_to list (a path, a symbol, or a command it depends on); this suite
seeds two lessons, one whose anchor exists in a temp tree and one whose does
not, drives the hook's own lesson_states() over that tree, and checks the
exact refusal line, the STALE heading, the unverified-anchor marking, and
scripts/receipt_door.py's applied_memory() section built from the same
records. Temp roots only; the real vault is never opened or written.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import receipt_door as RD  # noqa: E402

TOOLS_DIR = os.path.join(os.path.dirname(HERE), "products", "brothermode", "tools")
HOOK_PATH = os.path.join(TOOLS_DIR, "vault_recall_hook.py")


def load_hook():
    """The same by-path import products/brothermode/tools/
    test_vault_recall_hook.py's own load_hook uses, minus the env/consent
    knobs this suite does not need: lesson_states() and _lesson_state() are
    pure functions, exercised directly here, never through the
    consent-gated cmd_check() a different suite already covers."""
    spec = importlib.util.spec_from_file_location(
        "vault_recall_hook_for_recall_revalidation_test", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_note(vault_dir, name, applies_to=None, evidence_locator=None):
    """A minimal vault note with an optional applies_to frontmatter field,
    written to a TEMP vault directory only. applies_to, when given, is
    joined verbatim as the field's inline value (this codebase's
    established single-line frontmatter-list convention, matching
    supersedes:/contradicts: in bm_vault.py).

    evidence_locator, when given, is written in the same single-line form.
    A note needs one to read "applied" today: LL-2, the evidence tier at
    recall (vault_recall_hook.py's _lesson_state, DOWNGRADE ONLY), folds
    bm_vault_contradiction.evidence_tier into this hook's verdict, and
    since the founder's "strict everywhere" ruling of 2026-09-06 (FIX-
    DIRECTIVE-2026-09-06.md sections 3 and 4, evidence_tier step 3) a note
    declaring NEITHER evidence_locator NOR status tiers UNVERIFIED --
    unknown means WITHHOLD. path:<applies_to anchor> is the shape every
    benign note in this estate uses (_about_the_claim's own words)."""
    path = os.path.join(vault_dir, name)
    lines = ["---", "type: lesson"]
    if applies_to is not None:
        lines.append("applies_to: [%s]" % applies_to)
        lines.append("last_verified_at: 2026-09-01")
    if evidence_locator is not None:
        lines.append("evidence_locator: %s" % evidence_locator)
    lines.append("---")
    lines.append("# note body\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def two_hit_output(title_a, path_a, title_b, path_b):
    """The exact shape bm_vault.py's cmd_check prints for two ordinary hits
    (the same fixture shape products/brothermode/tools/
    test_vault_recall_hook.py's own TWO_TITLE_OUT uses), so lesson_states()
    sees exactly what the real tool would hand it."""
    return (
        "RECORDED FAILURES in the files you are about to touch:\n"
        "\n  %s  [lesson, session]\n"
        "    A lesson body line.\n"
        "    matched on: wording\n"
        "    %s\n"
        "\n  %s  [lesson, session]\n"
        "    Another lesson body line.\n"
        "    matched on: wording\n"
        "    %s\n"
        % (title_a, path_a, title_b, path_b))


class ACurrentLessonIsAppliedAndAStaleOneIsRefused(unittest.TestCase):
    def test_seeded_stale_and_current_lessons(self):
        mod = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            tree = os.path.join(tmp, "tree")
            vault = os.path.join(tmp, "vault")
            os.makedirs(tree)
            os.makedirs(vault)
            # the CURRENT lesson's anchor genuinely exists in the tree
            with open(os.path.join(tree, "still_here.py"), "w",
                     encoding="utf-8") as fh:
                fh.write("# still here\n")
            # DECISION LL-2 + "strict everywhere" (2026-09-06): a lesson
            # that declares no evidence_locator and no status now tiers
            # UNVERIFIED at recall, so the fixture for an APPLIED lesson
            # declares evidence that resolves against this same temp tree.
            # human_approved: true is deliberately NOT set here: P11's
            # narrow exemption covers only the no-signal case, and this
            # note proves itself by evidence instead (the human_approved
            # route has its own test, products/brothermode/tools/
            # test_vault_recall_hook.py::ATestOracleNoteIsGatedOnHuman
            # Approval::test_human_approved_true_with_a_resolving_anchor
            # _is_applied).
            current_path = write_note(vault, "current.md",
                                      applies_to="still_here.py",
                                      evidence_locator="path:still_here.py")
            # the STALE lesson names a path that has never existed here
            stale_path = write_note(vault, "stale.md",
                                    applies_to="gone_long_ago.py")
            out = two_hit_output("A current lesson", current_path,
                                 "A stale lesson", stale_path)

            records, out2 = mod.lesson_states(out, tree)

            by_slug = {r["slug"]: r for r in records}
            self.assertEqual(by_slug["current"]["state"], "applied")
            self.assertIsNone(by_slug["current"]["line"])
            self.assertEqual(by_slug["stale"]["state"], "stale")
            expected_line = (
                "recall: STALE stale: anchor gone_long_ago.py not found in "
                "%s; not applied" % tree)
            self.assertEqual(by_slug["stale"]["line"], expected_line)

            # the refusal line reaches the text that becomes the model's own
            # context, quoted exactly
            self.assertIn(expected_line, out2)
            # the stale lesson is shown under a STALE heading, never as
            # plain advice
            self.assertIn("STALE (not applied): A stale lesson", out2)
            # the current lesson's title line is untouched, byte for byte
            self.assertIn("  A current lesson  [lesson, session]", out2)

            section = RD.applied_memory(records)
            self.assertEqual([e["slug"] for e in section["applied"]],
                             ["current"])
            self.assertEqual([e["slug"] for e in section["stale"]],
                             ["stale"])
            self.assertEqual(section["stale"][0]["line"], expected_line)
            self.assertEqual(section["unverified"], [])
            # the stale lesson never appears in the applied section
            self.assertNotIn(
                "stale", [e["slug"] for e in section["applied"]])


class ALessonWithNoAppliesToReadsUnverified(unittest.TestCase):
    def test_no_applies_to_field_is_unverified_not_a_silent_pass(self):
        mod = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            tree = os.path.join(tmp, "tree")
            vault = os.path.join(tmp, "vault")
            os.makedirs(tree)
            os.makedirs(vault)
            path = write_note(vault, "no-anchor.md", applies_to=None)
            out = (
                "RECORDED FAILURES in the files you are about to touch:\n"
                "\n  No anchor at all  [lesson, session]\n"
                "    A lesson body line.\n"
                "    matched on: wording\n"
                "    %s\n" % path)

            records, out2 = mod.lesson_states(out, tree)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["state"], "unverified")
            # DECISION VN1 (docs/plan/VAULT-NIGHT-RUN-PROMPT-2026-09-09.md
            # section 4): the application verdict fails closed and
            # lesson_states returns an EXPLICIT reason line for every
            # unverified state. Before VN1 this case carried line=None and
            # printed nothing extra, so a reader saw the marker with no
            # stated reason; the line is now the record's own answer, and
            # its exact shape is what is pinned here.
            self.assertEqual(
                records[0]["line"],
                mod.UNVERIFIED_LINE_FMT % ("no-anchor", mod.NO_APPLIES_TO_REASON))
            self.assertIn("no applies_to anchor declared", records[0]["line"])
            self.assertIn("unverified anchor", out2.lower())
            # the reason reaches the text that becomes the model's own
            # context, not just the record
            self.assertIn(records[0]["line"], out2)

            section = RD.applied_memory(records)
            self.assertEqual([e["slug"] for e in section["unverified"]],
                             ["no-anchor"])
            self.assertEqual(section["applied"], [])
            self.assertEqual(section["stale"], [])


class ANoteWithheldByBmVaultItselfIsLeftAlone(unittest.TestCase):
    """A WITHHELD block (bm_vault.py's own supersession/candidate/staleness
    withholding, upstream of this hook) must never be re-classified by this
    row's applies_to check: it is a different mechanism over a different
    signal, and it is already refused before this hook ever sees it."""

    def test_withheld_block_carries_no_record_and_is_untouched(self):
        mod = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            tree = os.path.join(tmp, "tree")
            os.makedirs(tree)
            # DECISION M3 (2026-09-08 VN1 fix, vault_recall_hook.py's
            # _WITHHELD_MARKER_LINE and _block_is_withheld): a block counts
            # as already-withheld ONLY when it carries bm_vault.py's own
            # unforgeable marker line. Title text is author-controlled
            # frontmatter, so "WITHHELD (stale)" in a title is now a forgery
            # attempt, not a withhold (its own test:
            # products/brothermode/tools/test_vault_recall_hook.py's
            # FORGED_WITHHELD_TITLE_OUT). This fixture therefore carries the
            # marker, the same literal products/brothermode/tools/
            # test_vault_recall_hook.py's ONE_SERVED_ONE_WITHHELD_OUT uses,
            # so it is a genuinely withheld block rather than a forged one.
            out = (
                "RECORDED FAILURES in the files you are about to touch:\n"
                "\n  WITHHELD (stale)  A withheld lesson  [lesson, session]\n"
                "    reason: no cited anchor resolves\n"
                "    /tmp/withheld.md\n"
                "    \x00BM-VAULT-WITHHELD\x00\n")
            records, out2 = mod.lesson_states(out, tree)
            self.assertEqual(records, [])
            self.assertEqual(out2, out)


if __name__ == "__main__":
    unittest.main()
