#!/usr/bin/env python3
"""Calibration for tools/bm_vault_entity.py, benchmark row D14.

The property under test is the row's own sentence: something can be said
ABOUT a thing (a system, a metric) rather than only about a document. The
guard is its shadow: a relation edge pointing at a DOCUMENT must fail the
check, because document-to-document edges wearing relation names is exactly
the state D14 measured as FAIL.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_entity as ent  # noqa: E402

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


def note(entity=None, extra=()):
    lines = ["---", "type: reference", "status: standing"]
    if entity is not None:
        lines.append("entity: %s" % entity)
    lines.extend(extra)
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


class Fixture(unittest.TestCase):
    """A small real tree: a tool measures a metric, a system depends on a
    system, and one plain document sits beside them describing things."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-entity-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "30-Entities"))
        self._write("the-overview.md", note())
        self._write("30-Entities/kay-vault.md",
                    note("system", ["described_by: \"[[the-overview]]\""]))
        self._write("30-Entities/brothermode.md",
                    note("system", ["depends_on: \"[[kay-vault]]\""]))
        self._write("30-Entities/vault-benchmark.md",
                    note("tool", ["measures: \"[[proven-score]]\"",
                                  "depends_on: \"[[kay-vault]]\""]))
        self._write("30-Entities/proven-score.md",
                    note("metric", ["derives_from: \"[[kay-vault]]\""]))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.vault, rel)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class TheRowsOwnSentence(Fixture):
    def test_a_question_about_an_entity_returns_entities_and_typed_edges(self):
        """What depends on the Kay Vault? Two entities, each with its own
        type, through a named relation. Not a document list."""
        entities, _, findings = ent.load(self.vault)
        self.assertEqual(findings, [])
        hits = ent.incoming(entities, "kay-vault")
        self.assertIn(("brothermode", "depends_on"), hits)
        self.assertIn(("vault-benchmark", "depends_on"), hits)
        self.assertEqual(entities["brothermode"]["etype"], "system")

    def test_what_measures_a_metric_is_answerable(self):
        entities, _, _ = ent.load(self.vault)
        self.assertEqual(ent.incoming(entities, "proven-score"),
                         [("vault-benchmark", "measures")])

    def test_the_clean_fixture_checks_clean(self):
        self.assertEqual(ent.cmd_check(self.vault), 0)

    def test_query_prints_the_entity_not_a_document(self):
        self.assertEqual(ent.cmd_query(self.vault, "kay-vault"), 0)
        # NO-DATA convention, not an ordinary findings exit: no such entity
        # is absence of the thing asked about, exactly like an empty vault.
        self.assertEqual(ent.cmd_query(self.vault, "no-such-thing"), 2)


class TheGuardIsItsShadow(Fixture):
    def test_a_relation_edge_at_a_DOCUMENT_fails_the_check(self):
        """The D14 FAIL state must stay a failure here: an edge wearing a
        relation name but pointing at a page is not an ontology."""
        self._write("30-Entities/graph-loop.md",
                    note("system", ["part_of: \"[[the-overview]]\""]))
        self.assertEqual(ent.cmd_check(self.vault), 1)

    def test_a_relation_edge_at_NOTHING_fails_the_check(self):
        self._write("30-Entities/graph-loop.md",
                    note("system", ["part_of: \"[[never-written]]\""]))
        self.assertEqual(ent.cmd_check(self.vault), 1)

    def test_an_unknown_entity_type_is_a_finding_not_a_guess(self):
        self._write("30-Entities/oops.md",
                    note("servce", ["depends_on: \"[[kay-vault]]\""]))
        self.assertEqual(ent.cmd_check(self.vault), 1)
        _, _, findings = ent.load(self.vault)
        self.assertIn("servce", findings[0][1])

    def test_an_isolated_entity_is_a_finding(self):
        self._write("30-Entities/lonely.md", note("tool"))
        self.assertEqual(ent.cmd_check(self.vault), 1)

    def test_broken_provenance_is_a_finding(self):
        self._write("30-Entities/kay-vault.md",
                    note("system", ["depends_on: \"[[brothermode]]\"",
                                    "described_by: \"[[never-written]]\""]))
        self.assertEqual(ent.cmd_check(self.vault), 1)


class ListFormEdges(Fixture):
    """Obsidian writes a multi-value field as a YAML list, not a repeated
    inline scalar. A note carrying one inline edge and one list-form edge
    must surface both, never silently drop the list one."""

    def test_inline_and_list_form_edges_both_appear(self):
        self._write("30-Entities/mixed.md",
                    "\n".join([
                        "---", "type: reference", "status: standing",
                        "entity: system",
                        "depends_on: \"[[kay-vault]]\"",
                        "part_of:",
                        "  - \"[[brothermode]]\"",
                        "  - \"[[vault-benchmark]]\"",
                        "---", "", "# mixed"]) + "\n")
        entities, _, findings = ent.load(self.vault)
        self.assertEqual(findings, [])
        edges = entities["mixed"]["edges"]
        self.assertEqual(edges.get("depends_on"), ["kay-vault"])
        self.assertEqual(edges.get("part_of"), ["brothermode", "vault-benchmark"])

    def test_breaking_list_parsing_makes_this_fail(self):
        """Calibration: if _field_values stopped reading list items (fell
        back to the single-line-only behavior), the list-form edge above
        would vanish rather than raise, which is exactly the silent drop
        this suite exists to catch. Assert the shape directly against the
        raw parser so a future regression cannot pass by accident."""
        block = "\n".join(["type: reference", "part_of:",
                           "  - \"[[brothermode]]\"", "  - \"[[kay-vault]]\""])
        self.assertEqual(ent._targets(ent._field_values(block, "part_of")),
                         ["brothermode", "kay-vault"])
        # the old single-line-only accessor's \s* eats the newline and
        # leading indent, then (.*) grabs only the FIRST list line, so the
        # second edge is silently gone: exactly the drop this suite guards.
        self.assertEqual(ent._targets(ent._field(block, "part_of")),
                         ["brothermode"])


class FrontmatterEdgeCases(unittest.TestCase):
    def test_a_horizontal_rule_opener_with_no_closing_fence_is_not_frontmatter(self):
        """A note that opens with a bare "---" for visual style, and never
        closes one, has no frontmatter at all: mirrors ids.frontmatter,
        which returns None rather than a same-looking empty string for the
        identical shape."""
        text = "---\nJust a rule up top, not YAML, entity: rogue\n"
        self.assertIsNone(ent._frontmatter(text))


class AbsenceIsNeverAPass(unittest.TestCase):
    def test_zero_entities_is_NODATA_exit_2_not_a_clean_pass(self):
        tmp = tempfile.mkdtemp(prefix="bm-entity-empty-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "doc.md"), "w", encoding="utf-8") as fh:
            fh.write(note())
        self.assertEqual(ent.cmd_check(tmp), 2)

    def test_a_missing_vault_is_NODATA_exit_2(self):
        self.assertEqual(ent.main(["check", "--vault", "/no/such/dir"]), 2)

    def test_check_subcommand_smoke_on_an_empty_vault_is_NODATA(self):
        """The 10 real entity notes in this vault are covered by the
        benchmark's D14/D06 probes, not by a machine-coupled test here (the
        estate's recorded lesson: a test that consults machine state is not
        testing the checkout). This is the substitute: a smoke test of the
        actual `check` subcommand, through main(), against an empty vault."""
        tmp = tempfile.mkdtemp(prefix="bm-entity-cli-empty-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "doc.md"), "w", encoding="utf-8") as fh:
            fh.write(note())
        self.assertEqual(ent.main(["check", "--vault", tmp]), 2)

    def test_query_on_a_malformed_only_vault_prints_findings_then_NODATA(self):
        tmp = tempfile.mkdtemp(prefix="bm-entity-malformed-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "oops.md"), "w", encoding="utf-8") as fh:
            fh.write(note("servce"))
        entities, _, findings = ent.load(tmp)
        self.assertEqual(entities, {})
        self.assertTrue(findings)
        self.assertEqual(ent.cmd_query(tmp, "anything"), 2)


class PathsCollapseToStems(unittest.TestCase):
    def test_a_pathful_wikilink_names_the_same_entity_as_a_bare_one(self):
        self.assertEqual(ent._targets('"[[30-Entities/kay-vault]], [[x]]"'),
                         ["kay-vault", "x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
