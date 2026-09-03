#!/usr/bin/env python3
"""Calibration for tools/bm_vault_crosswalk.py, benchmark row D06.

The property under test is the row's own sentence: source-IDs from different
systems all denote one entity, so a lookup by any of its names finds the
thing. The guards are its shadows: a dangling vault reference must fail by
name, a declaration on a plain document must fail, and an empty crosswalk
must read NO-DATA, never clean.

No em or en dashes anywhere in this file.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_crosswalk as xw  # noqa: E402


def note(entity=None, note_id=None, source_ids=None):
    lines = ["---", "type: reference", "status: standing"]
    if note_id:
        lines.append("id: %s" % note_id)
    if entity:
        lines.append("entity: %s" % entity)
    if source_ids:
        lines.append("source_ids: [%s]" % ", ".join(source_ids))
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


def run(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue() + err.getvalue()


class Fixture(unittest.TestCase):
    """A small real tree: one repository entity named three ways, one metric
    named once, and a plain document beside them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-crosswalk-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "30-Entities"))
        self._write("the-overview.md", note(note_id="n-00000000000000aa"))
        self._write("30-Entities/bmu-repo.md",
                    note("repository", "n-00000000000000bb",
                         ["github:khalilmaaouni/BrotherModeUp",
                          "path:~/Documents/BrotherModeUp",
                          "vault:n-00000000000000bb"]))
        self._write("30-Entities/proven-score.md",
                    note("metric", "n-00000000000000cc",
                         ["vault:n-00000000000000cc"]))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.vault, rel)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class TestParseSourceIds(unittest.TestCase):

    def test_quoted_comma_survives_split(self):
        # Old code split on every comma before honouring quotes, tearing a
        # quoted value with an embedded comma in half: a truncated id was
        # silently recorded AND a bogus no-colon finding was raised for the
        # stray fragment.
        entries, problems = xw.parse_source_ids(
            '["path:/tmp/My Docs, Old", github:a/b]')
        self.assertEqual(problems, [])
        pairs = {(e["system"], e["ident"]) for e in entries}
        self.assertIn(("path", "/tmp/My Docs, Old"), pairs)
        self.assertIn(("github", "a/b"), pairs)

    def test_undated_entry_is_open_same_as(self):
        # Backward compatibility: every mapping already on disk has no
        # metadata at all, and must keep reading as an open-interval
        # same_as mapping, unchanged.
        entries, problems = xw.parse_source_ids("[github:a/b]")
        self.assertEqual(problems, [])
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertIsNone(e["valid_from"])
        self.assertIsNone(e["valid_to"])
        self.assertEqual(e["relationship"], "same_as")
        self.assertEqual(e["recorded_at"], "")

    def test_dated_entry_parses_all_fields(self):
        entries, problems = xw.parse_source_ids(
            "[vault:n-1;valid_from=2020-01-01;valid_to=2022-06-01;"
            "relationship=renamed_from;recorded_at=2026-08-30]")
        self.assertEqual(problems, [])
        e = entries[0]
        self.assertEqual(e["valid_from"], xw.datetime.date(2020, 1, 1))
        self.assertEqual(e["valid_to"], xw.datetime.date(2022, 6, 1))
        self.assertEqual(e["relationship"], "renamed_from")
        self.assertEqual(e["recorded_at"], "2026-08-30")

    def test_unknown_field_is_a_problem(self):
        entries, problems = xw.parse_source_ids("[vault:n-1;bogus=x]")
        self.assertEqual(entries, [])
        self.assertIn("unknown field", problems[0])

    def test_malformed_date_is_a_problem(self):
        entries, problems = xw.parse_source_ids("[vault:n-1;valid_from=not-a-date]")
        self.assertEqual(entries, [])
        self.assertIn("invalid valid_from date", problems[0])

    def test_malformed_valid_to_date_is_a_problem(self):
        # valid_to shares _parse_date with valid_from but had no dedicated
        # coverage of its own.
        entries, problems = xw.parse_source_ids("[vault:n-1;valid_to=not-a-date]")
        self.assertEqual(entries, [])
        self.assertIn("invalid valid_to date", problems[0])

    def test_valid_from_after_valid_to_is_a_problem(self):
        entries, problems = xw.parse_source_ids(
            "[vault:n-1;valid_from=2022-01-01;valid_to=2020-01-01]")
        self.assertEqual(entries, [])
        self.assertIn("after valid_to", problems[0])

    def test_unknown_relationship_is_a_problem(self):
        entries, problems = xw.parse_source_ids("[vault:n-1;relationship=acquired]")
        self.assertEqual(entries, [])
        self.assertIn("unknown relationship", problems[0])


class TestResolve(Fixture):

    def test_zero_declarations_is_nodata_at_exit_2(self):
        empty = os.path.join(self.tmp, "empty-resolve")
        os.makedirs(empty)
        with open(os.path.join(empty, "doc.md"), "w", encoding="utf-8") as fh:
            fh.write(note(note_id="n-0000000000000022"))
        rc, out = run(xw.cmd_resolve, empty, "github:x/y")
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out)

    def test_same_basename_different_folders_is_ambiguous(self):
        # Entity identity used to be keyed on basename alone, so two notes
        # named the same in different folders collapsed into one identity
        # and a bare-id resolve could silently pick one instead of refusing.
        os.makedirs(os.path.join(self.vault, "30-Entities", "a"))
        os.makedirs(os.path.join(self.vault, "30-Entities", "b"))
        self._write("30-Entities/a/twin.md",
                    note("repository", "n-00000000000000ee",
                         ["github:same/thing", "vault:n-00000000000000ee"]))
        self._write("30-Entities/b/twin.md",
                    note("repository", "n-00000000000000ff",
                         ["plugin:same/thing", "vault:n-00000000000000ff"]))
        rc, out = run(xw.cmd_resolve, self.vault, "same/thing")
        self.assertEqual(rc, 1)
        self.assertIn("AMBIGUOUS", out)
        self.assertIn("a/twin", out)
        self.assertIn("b/twin", out)

    def test_qualified_hit(self):
        rc, out = run(xw.cmd_resolve, self.vault, "github:khalilmaaouni/BrotherModeUp")
        self.assertEqual(rc, 0)
        self.assertIn("bmu-repo", out)
        self.assertIn("entity=repository", out)

    def test_bare_hit(self):
        rc, out = run(xw.cmd_resolve, self.vault, "khalilmaaouni/BrotherModeUp")
        self.assertEqual(rc, 0)
        self.assertIn("bmu-repo", out)

    def test_honest_miss(self):
        rc, out = run(xw.cmd_resolve, self.vault, "github:nobody/NoSuchRepo")
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)

    def test_ambiguous_bare_value_refuses(self):
        self._write("30-Entities/other.md",
                    note("system", "n-00000000000000dd",
                         ["plugin:khalilmaaouni/BrotherModeUp"]))
        rc, out = run(xw.cmd_resolve, self.vault, "khalilmaaouni/BrotherModeUp")
        self.assertEqual(rc, 1)
        self.assertIn("AMBIGUOUS", out)
        self.assertIn("bmu-repo", out)
        self.assertIn("other", out)


class TestCheck(Fixture):

    def test_clean_census(self):
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 0)
        self.assertIn("entities declaring a crosswalk: 2", out)
        self.assertIn("entities crossing 2+ systems: 1", out)

    def test_dangling_vault_reference_fails_by_name(self):
        self._write("30-Entities/ghost.md",
                    note("tool", "n-00000000000000ee",
                         ["vault:n-deaddeaddeaddead"]))
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("DANGLING", out)
        self.assertIn("n-deaddeaddeaddead", out)
        self.assertIn("ghost.md", out)

    def test_declaration_on_plain_document_fails(self):
        self._write("stray.md", note(source_ids=["github:x/y"]))
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("without entity:", out)

    def test_unknown_system_fails(self):
        self._write("30-Entities/odd.md",
                    note("tool", "n-00000000000000ff", ["gitlab:x/y"]))
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("unknown system", out)
        self.assertIn("gitlab", out)

    def test_duplicate_claim_fails(self):
        self._write("30-Entities/twin.md",
                    note("system", "n-00000000000000dd",
                         ["github:khalilmaaouni/BrotherModeUp"]))
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("already claimed", out)

    def test_zero_declarations_is_nodata_never_a_pass(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        with open(os.path.join(empty, "doc.md"), "w", encoding="utf-8") as fh:
            fh.write(note(note_id="n-0000000000000011"))
        rc, out = run(xw.cmd_check, empty)
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out)


class TestDatedResolve(Fixture):
    """VB6-07: a customer id maps to different entities before and after a
    rename, and a reused legacy id must never bleed across its boundary."""

    def setUp(self):
        super().setUp()
        # legacy-co is renamed to new-co on 2022-06-01: an entity for each
        # era, both claiming the same billing id over disjoint intervals.
        self._write("30-Entities/legacy-co.md",
                    note("customer", "n-0000000000000101",
                         ["plugin:cust-42;valid_to=2022-05-31;relationship=renamed_from"]))
        self._write("30-Entities/new-co.md",
                    note("customer", "n-0000000000000102",
                         ["plugin:cust-42;valid_from=2022-06-01;relationship=same_as"]))
        # cust-99 is a reused legacy id: entity A held it, it went dark, then
        # entity B was issued the exact same id years later.
        self._write("30-Entities/first-holder.md",
                    note("customer", "n-0000000000000103",
                         ["plugin:cust-99;valid_to=2018-12-31;relationship=reused_id"]))
        self._write("30-Entities/second-holder.md",
                    note("customer", "n-0000000000000104",
                         ["plugin:cust-99;valid_from=2020-01-01;relationship=reused_id"]))
        # bounded-co: one entry with both ends set, on an id no other entry
        # touches, so exact-boundary assertions below are unambiguous.
        self._write("30-Entities/bounded-co.md",
                    note("customer", "n-0000000000000106",
                         ["plugin:cust-77;valid_from=2021-03-01;valid_to=2021-03-10"]))

    def test_resolves_to_pre_rename_entity_before_boundary(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-42",
                      xw.datetime.date(2022, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("legacy-co", out)

    def test_resolves_to_post_rename_entity_after_boundary(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-42",
                      xw.datetime.date(2023, 1, 1))
        self.assertEqual(rc, 0)
        self.assertIn("new-co", out)

    def test_reused_id_never_bleeds_across_its_interval(self):
        before, out_before = run(xw.cmd_resolve, self.vault, "plugin:cust-99",
                                 xw.datetime.date(2017, 1, 1))
        self.assertEqual(before, 0)
        self.assertIn("first-holder", out_before)
        self.assertNotIn("second-holder", out_before)

        after, out_after = run(xw.cmd_resolve, self.vault, "plugin:cust-99",
                               xw.datetime.date(2021, 1, 1))
        self.assertEqual(after, 0)
        self.assertIn("second-holder", out_after)
        self.assertNotIn("first-holder", out_after)

    def test_gap_between_intervals_is_honest_miss(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-99",
                      xw.datetime.date(2019, 6, 1))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)

    def test_ended_mapping_still_resolves_before_its_boundary(self):
        # An ended mapping (valid_to set) is never deleted: it must still
        # answer correctly for a date before the boundary.
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-42",
                      xw.datetime.date(2022, 5, 30))
        self.assertEqual(rc, 0)
        self.assertIn("legacy-co", out)

    def test_no_as_of_is_ambiguous_across_non_overlapping_claims(self):
        # Without --as-of every entry answers regardless of interval, so a
        # reused/renamed id with no date given is an honest AMBIGUOUS, never
        # a silent pick of one era over the other.
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-42")
        self.assertEqual(rc, 1)
        self.assertIn("AMBIGUOUS", out)

    def test_check_does_not_flag_non_overlapping_reuse_as_duplicate(self):
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 0)
        self.assertNotIn("already claimed", out)

    def test_check_flags_overlapping_claim_as_duplicate(self):
        self._write("30-Entities/overlap.md",
                    note("customer", "n-0000000000000105",
                         ["plugin:cust-42;valid_from=2021-01-01;valid_to=2022-12-31"]))
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("already claimed", out)
        self.assertIn("overlapping interval", out)

    # Boundary semantics (review MAJOR): both ends are inclusive, and the
    # docstring must say so. These four pin the exact edges of one interval.

    def test_as_of_exactly_valid_to_hits(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-77",
                      xw.datetime.date(2021, 3, 10))
        self.assertEqual(rc, 0)
        self.assertIn("bounded-co", out)

    def test_as_of_day_after_valid_to_misses(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-77",
                      xw.datetime.date(2021, 3, 11))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)

    def test_as_of_exactly_valid_from_hits(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-77",
                      xw.datetime.date(2021, 3, 1))
        self.assertEqual(rc, 0)
        self.assertIn("bounded-co", out)

    def test_as_of_day_before_valid_from_misses(self):
        rc, out = run(xw.cmd_resolve, self.vault, "plugin:cust-77",
                      xw.datetime.date(2021, 2, 28))
        self.assertEqual(rc, 1)
        self.assertIn("NO-DATA", out)

    def test_check_flags_exact_shared_boundary_as_overlap(self):
        # Two intervals meeting at one shared day DO overlap under inclusive
        # ends: that day is valid under both, so the boundary itself is a
        # conflict, not a clean handoff.
        self._write("30-Entities/touching-a.md",
                    note("customer", "n-0000000000000107",
                         ["plugin:cust-88;valid_to=2022-06-01"]))
        self._write("30-Entities/touching-b.md",
                    note("customer", "n-0000000000000108",
                         ["plugin:cust-88;valid_from=2022-06-01"]))
        rc, out = run(xw.cmd_check, self.vault)
        self.assertEqual(rc, 1)
        self.assertIn("already claimed", out)
        self.assertIn("overlapping interval", out)


class TestCli(Fixture):

    def test_no_vault_is_nodata(self):
        rc, _ = run(xw.main, ["check", "--vault", os.path.join(self.tmp, "nope")])
        self.assertEqual(rc, 2)

    def test_resolve_needs_source_id(self):
        rc, _ = run(xw.main, ["resolve", "--vault", self.vault])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
