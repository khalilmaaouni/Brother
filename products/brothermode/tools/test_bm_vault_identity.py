#!/usr/bin/env python3
"""Calibration for tools/bm_vault_identity.py, WBS row VB3-17.

The property under test is the row's own sentence: ids stay opaque and immutable,
tenant scope is never folded into one, and a golden-record merge is a recorded event
(never a rewrite) whose reversal opens a new resolution interval rather than erasing
the old one. Every test is driven backwards against that sentence: a fixture merge of
two entities must preserve both histories, resolve both to the survivor for current
queries, resolve each to itself before the merge's effective date, and unmerge without
losing the merge era as queryable history.

No em or en dashes anywhere in this file.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_identity as idn  # noqa: E402

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

TOOL = os.path.join(HERE, "bm_vault_identity.py")

A_ID = "n-00000000000000aa"
B_ID = "n-00000000000000bb"
C_ID = "n-00000000000000cc"


def note(entity, note_id, source_ids=None, extra_id_line=None):
    lines = ["---", "type: reference", "status: standing"]
    if extra_id_line is not None:
        lines.append(extra_id_line)
    else:
        lines.append("id: %s" % note_id)
    lines.append("entity: %s" % entity)
    if source_ids:
        lines.append("source_ids: [%s]" % ", ".join(source_ids))
    lines += ["---", "", "# %s" % entity]
    return "\n".join(lines) + "\n"


def run_fn(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue() + err.getvalue()


def run_cli(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


class VaultFixture(unittest.TestCase):
    """Two entities, A and B, each declaring one foreign (github:) source id
    alongside their own opaque vault: self-reference -- the shape every real
    entity note in this vault already takes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-identity-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "30-Entities"))
        self._write("30-Entities/a.md", note(
            "repository", A_ID, ["vault:%s" % A_ID, "github:orgA/repoA"]))
        self._write("30-Entities/b.md", note(
            "repository", B_ID, ["vault:%s" % B_ID, "github:orgB/repoB"]))
        self.ids_mod, self.xw_mod, self.ev_mod = idn._load_sibling("bm_vault_ids"), \
            idn._load_sibling("bm_vault_crosswalk"), idn._load_sibling("bm_vault_events")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.vault, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read(self, rel):
        with open(os.path.join(self.vault, rel), "rb") as fh:
            return fh.read()

    def _merge(self, from_id=A_ID, into_id=B_ID, rule_version="v1",
              effective="2026-01-15"):
        return run_fn(idn.cmd_merge, self.vault, from_id, into_id, rule_version,
                      effective, self.ids_mod, self.ev_mod)


class NoDataTest(VaultFixture):
    def test_check_on_a_vault_with_nothing_at_all_is_no_data(self):
        empty = os.path.join(self.tmp, "empty-vault")
        os.makedirs(empty)
        rc, out = run_fn(idn.cmd_check, empty, self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(2, rc, out)
        self.assertIn("NO-DATA", out)

    def test_resolve_of_an_unknown_source_id_is_an_honest_miss(self):
        rc, out = run_fn(idn.cmd_resolve, self.vault, "github:nobody/nothing", None,
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("NO-DATA", out)


class MergePreservesHistoryTest(VaultFixture):
    def test_merge_appends_one_event_and_leaves_both_notes_byte_identical(self):
        before_a, before_b = self._read("30-Entities/a.md"), self._read("30-Entities/b.md")
        rc, out = self._merge()
        self.assertEqual(0, rc, out)
        after_a, after_b = self._read("30-Entities/a.md"), self._read("30-Entities/b.md")
        self.assertEqual(before_a, after_a, "entity A must be untouched by a merge")
        self.assertEqual(before_b, after_b, "entity B must be untouched by a merge")

        events = idn.load_identity_events(self.vault, self.ev_mod)
        self.assertEqual(1, len(events))
        self.assertEqual("merged_into", events[0]["kind"])
        self.assertEqual(A_ID, events[0]["ref"])
        self.assertEqual(B_ID, events[0]["into"])
        self.assertEqual("v1", events[0]["rule_version"])
        self.assertEqual("2026-01-15", events[0]["effective"])

    def test_current_resolution_answers_the_survivor_for_the_opaque_id(self):
        self._merge()
        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, None,
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("survivor=%s" % B_ID, out)

    def test_current_resolution_answers_the_survivor_for_a_foreign_source_id(self):
        # Either entity's source ids resolve to the survivor, not only the
        # opaque note id: this is the two-hop crosswalk-then-merge path.
        self._merge()
        rc, out = run_fn(idn.cmd_resolve, self.vault, "github:orgA/repoA", None,
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("survivor=%s" % B_ID, out)

    def test_as_of_before_effective_answers_each_its_own(self):
        self._merge()
        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, "2026-01-01",
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("entity=%s" % A_ID, out)
        self.assertNotIn("survivor=", out)

    def test_as_of_on_the_effective_date_itself_is_inclusive(self):
        self._merge()
        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, "2026-01-15",
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("survivor=%s" % B_ID, out)


class StackedMergeRefusedTest(VaultFixture):
    def test_merging_an_already_merged_id_again_is_refused(self):
        rc, out = self._merge()
        self.assertEqual(0, rc, out)
        self._write("30-Entities/c.md", note("repository", C_ID, ["vault:%s" % C_ID]))
        rc, out = run_fn(idn.cmd_merge, self.vault, A_ID, C_ID, "v2", "2026-02-01",
                         self.ids_mod, self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("already merged", out)
        events = idn.load_identity_events(self.vault, self.ev_mod)
        self.assertEqual(1, len(events), "the refused merge must not have written anything")

    def test_merging_unknown_ids_is_refused_not_no_data(self):
        rc, out = run_fn(idn.cmd_merge, self.vault, A_ID, "n-ffffffffffffffff", "v1",
                         "2026-01-15", self.ids_mod, self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("resolves to no note", out)


class UnmergeTest(VaultFixture):
    def test_unmerge_restores_separate_resolution_and_keeps_the_era_queryable(self):
        rc, out = self._merge()
        self.assertEqual(0, rc, out)
        rc, out = run_fn(idn.cmd_unmerge, self.vault, A_ID, None, "2026-03-01",
                         self.ev_mod)
        self.assertEqual(0, rc, out)

        events = idn.load_identity_events(self.vault, self.ev_mod)
        self.assertEqual(2, len(events), "unmerge must APPEND, never edit, the merge event")
        kinds = sorted(e["kind"] for e in events)
        self.assertEqual(["merged_into", "unmerged"], kinds)

        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, None,
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("entity=%s" % A_ID, out, "current query must resolve A to itself again")

        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, "2026-02-01",
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("survivor=%s" % B_ID, out,
                      "the merge era must still resolve to the survivor as history")

        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, "2026-04-01",
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("entity=%s" % A_ID, out, "after the unmerge date, A resolves alone")

    def test_unmerge_with_no_open_merge_is_refused(self):
        rc, out = run_fn(idn.cmd_unmerge, self.vault, A_ID, None, "2026-03-01",
                         self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("no open merge", out)

    def test_after_unmerge_the_pair_can_be_merged_again(self):
        self._merge()
        run_fn(idn.cmd_unmerge, self.vault, A_ID, None, "2026-03-01", self.ev_mod)
        rc, out = run_fn(idn.cmd_merge, self.vault, A_ID, B_ID, "v2", "2026-04-01",
                         self.ids_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        rc, out = run_fn(idn.cmd_resolve, self.vault, A_ID, None,
                         self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("survivor=%s" % B_ID, out)


class OpacityGateTest(VaultFixture):
    def test_clean_fixture_passes(self):
        rc, out = run_fn(idn.cmd_check, self.vault, self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(0, rc, out)
        self.assertIn("clean", out)

    def test_planted_tenant_prefixed_note_id_fails_by_name(self):
        self._write("30-Entities/c.md", note(
            "repository", None, extra_id_line="id: tenantA-n-abc0123456789def"))
        rc, out = run_fn(idn.cmd_check, self.vault, self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("tenantA-n-abc0123456789def", out)
        self.assertIn("not opaque", out)

    def test_planted_tenant_prefixed_crosswalk_vault_id_fails_by_name(self):
        self._write("30-Entities/d.md", note(
            "repository", C_ID, ["vault:tenantB-n-0000000000000001"]))
        rc, out = run_fn(idn.cmd_check, self.vault, self.ids_mod, self.xw_mod, self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("tenantB-n-0000000000000001", out)

    def test_merge_refuses_a_non_opaque_id_outright(self):
        rc, out = run_fn(idn.cmd_merge, self.vault, "tenantA-n-abc0123456789def",
                         B_ID, "v1", "2026-01-15", self.ids_mod, self.ev_mod)
        self.assertEqual(1, rc, out)
        self.assertIn("opaque", out)


class CliSmokeTest(VaultFixture):
    """The actual CLI end to end, over real files, for the exit codes the
    done-check quotes verbatim."""

    def test_merge_then_resolve_then_unmerge_over_the_real_cli(self):
        rc, out = run_cli(["merge", "--vault", self.vault, "--from", A_ID,
                           "--into", B_ID, "--rule-version", "v1",
                           "--effective", "2026-01-15"])
        self.assertEqual(0, rc, out)

        rc, out = run_cli(["resolve", "--vault", self.vault, "--source-id", A_ID])
        self.assertEqual(0, rc, out)
        self.assertIn("survivor=%s" % B_ID, out)

        rc, out = run_cli(["unmerge", "--vault", self.vault, "--from", A_ID,
                           "--effective", "2026-03-01"])
        self.assertEqual(0, rc, out)

        rc, out = run_cli(["resolve", "--vault", self.vault, "--source-id", A_ID])
        self.assertEqual(0, rc, out)
        self.assertIn("entity=%s" % A_ID, out)

        rc, out = run_cli(["check", "--vault", self.vault])
        self.assertEqual(0, rc, out)


if __name__ == "__main__":
    unittest.main()
