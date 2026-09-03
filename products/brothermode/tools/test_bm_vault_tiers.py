#!/usr/bin/env python3
"""Tests for bm_vault_tiers, the severity-tiered write gate with quarantine
(WBS VB10-01). Drives the three contract claims backwards, end to end,
through the real bm_vault_cli.py commit command against a throwaway git
fixture (never the real Kay Vault):

  1. a NEW note with a broken link refuses the fixture commit
  2. the same defect on a PRE-EXISTING note queues as WARN, never blocking
  3. a --quarantine divert lands the note in the inbox as a restricted
     candidate and lets the rest of the commit proceed

Plus unit coverage for classify()/TIER_TABLE (the progressive rule, pure,
no I/O) and append_queue() (the WARN queue write). The real-vault clause of
this row's done-check (the real vault pre-commit still passes on a clean
commit) is proved separately, by hand, against the real Kay Vault: `git diff
--cached --name-only` is empty there, so run_gate() itself reports "nothing
staged to gate" at exit 0, and scripts/bm_vault_precommit_hook.py's own
warning_for() (read-only, never writes) returns None with no foreign lock
held. This file never touches the real Kay Vault, only throwaway fixtures.

Run: python3 tools/test_bm_vault_tiers.py     (unittest output, exit 0 or 1)
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "bm_vault_cli.py")
TIERS = os.path.join(HERE, "bm_vault_tiers.py")

_spec = importlib.util.spec_from_file_location("bm_vault_tiers", TIERS)
tiers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tiers)


def _note(id_="n-0123456789abcdef", type_="reference", status="standing",
          created="2026-08-01", body="\n# a clean note\n\nordinary content.\n"):
    return ("---\nid: %s\ntype: %s\nstatus: %s\ncreated: %s\n---\n%s"
            % (id_, type_, status, created, body))


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _git(vault, *args):
    return subprocess.run(["git", "-C", vault] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def _head(vault):
    return _git(vault, "rev-parse", "HEAD").stdout.strip()


def _init_git_vault(root):
    """One clean, committed note under 10-Projects/demo/, matching
    test_bm_vault_cli.py's own fixture shape."""
    os.makedirs(os.path.join(root, "10-Projects", "demo"), exist_ok=True)
    _write(os.path.join(root, "10-Projects", "demo", "one.md"), _note())
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")


def run_cli(argv):
    p = subprocess.run([sys.executable, CLI] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    return p.returncode, p.stdout + p.stderr


class TierTableAndClassify(unittest.TestCase):
    """Pure, no I/O: the progressive rule in one place."""

    def test_error_capable_class_on_new_note_stays_error(self):
        severity, cls = tiers.classify("broken_link", is_new=True)
        self.assertEqual(severity, "ERROR")
        self.assertEqual(cls, "broken_link")

    def test_error_capable_class_on_preexisting_note_downgrades(self):
        severity, cls = tiers.classify("bad_status_value", is_new=False)
        self.assertEqual(severity, "WARN")
        self.assertEqual(cls, "schema_violation")

    def test_missing_required_field_kinds_map_to_one_class(self):
        for kind in ("missing_status", "missing_type", "no_frontmatter"):
            severity, cls = tiers.classify(kind, is_new=True)
            self.assertEqual(cls, "missing_required_field", kind)
            self.assertEqual(severity, "ERROR", kind)

    def test_rot_and_staleness_never_reach_error_even_when_new(self):
        for kind in ("empty_note", "whitespace_only_note", "orphan_attachment",
                     "staleness"):
            severity, _cls = tiers.classify(kind, is_new=True)
            self.assertEqual(severity, "WARN", kind)

    def test_tier_table_declares_every_class_once(self):
        self.assertEqual(set(tiers.TIER_TABLE), {
            "broken_link", "schema_violation", "missing_required_field",
            "staleness", "rot"})


class AppendQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-tiers-queue-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_appends_one_line_per_finding_never_overwrites(self):
        finding = {"kind": "broken_link", "path": "one.md", "detail": "[[x]]"}
        self.assertTrue(tiers.append_queue(self.tmp, [(finding, "broken_link")]))
        self.assertTrue(tiers.append_queue(self.tmp, [(finding, "broken_link")]))
        path = os.path.join(self.tmp, tiers.QUEUE_RELPATH)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # Belt and braces (MAJOR 1): the file gets minimal valid frontmatter
        # once, on first creation, never repeated on later appends.
        self.assertEqual(text.count("---"), 2)
        self.assertIn("status: standing", text)
        self.assertIn("type: reference", text)
        entry_lines = [l for l in text.splitlines() if l.strip() and "WARN" in l]
        self.assertEqual(len(entry_lines), 2)
        for line in entry_lines:
            self.assertIn("broken_link", line)
            self.assertIn("one.md", line)
            self.assertIn("WARN", line)

    def test_new_file_frontmatter_satisfies_the_gate_itself(self):
        """The belt-and-braces frontmatter must actually be valid by the same
        rule bm_vault_graph.py's own _scoped_findings enforces (status/type
        both in ALLOWED_STATUS/ALLOWED_TYPE), not merely present."""
        graph_spec = importlib.util.spec_from_file_location(
            "bm_vault_graph", os.path.join(HERE, "bm_vault_graph.py"))
        graph = importlib.util.module_from_spec(graph_spec)
        graph_spec.loader.exec_module(graph)
        finding = {"kind": "broken_link", "path": "one.md", "detail": None}
        self.assertTrue(tiers.append_queue(self.tmp, [(finding, "broken_link")]))
        path = os.path.join(self.tmp, tiers.QUEUE_RELPATH)
        with open(path, encoding="utf-8") as f:
            body = f.read()
        block = graph._frontmatter_block(body)
        self.assertTrue(block)
        m = graph.FRONT_STATUS.search(block)
        self.assertTrue(m and m.group(1).strip() in graph.ALLOWED_STATUS)
        m = graph.FRONT_TYPE.search(block)
        self.assertTrue(m and m.group(1).strip() in graph.ALLOWED_TYPE)


class GateEndToEnd(unittest.TestCase):
    """The three contract claims, driven backwards through the real
    bm_vault_cli.py commit command."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-tiers-e2e-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_1_new_note_with_broken_link_refuses(self):
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        _write(os.path.join(vault, "10-Projects", "demo", "two.md"),
               _note(id_="n-a1a1a1a1a1a1a1a1", body="\nSee [[does-not-exist]].\n"))
        before = _head(vault)
        code, out = run_cli(["commit", "--vault", vault, "-m", "new note, refuse"])
        self.assertEqual(code, 1, out)
        self.assertIn("does-not-exist", out)
        self.assertIn("ERROR (broken_link)", out)
        self.assertEqual(before, _head(vault), out)
        # No commit landed, so nothing was queued as WARN either.
        self.assertFalse(
            os.path.exists(os.path.join(vault, tiers.QUEUE_RELPATH)), out)

    def test_2_preexisting_note_defect_queues_as_warn_without_blocking(self):
        vault = os.path.join(self.tmp, "v")
        note_path = os.path.join(vault, "10-Projects", "demo", "one.md")
        os.makedirs(os.path.dirname(note_path), exist_ok=True)
        _write(note_path, _note(body="\nSee [[does-not-exist]].\n"))
        _git(vault, "init", "-q")
        _git(vault, "config", "user.email", "test@example.com")
        _git(vault, "config", "user.name", "Test")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "initial, already has the broken link")
        # Edit the PRE-EXISTING note (same broken link, unrelated new line) so it
        # lands in the staged diff the gate scopes to; an untouched file never
        # enters `git diff --cached` at all.
        with open(note_path, "a", encoding="utf-8") as f:
            f.write("\nan unrelated edit.\n")
        before = _head(vault)
        code, out = run_cli(["commit", "--vault", vault, "-m", "should still land"])
        self.assertEqual(code, 0, out)
        self.assertNotEqual(before, _head(vault), out)
        queue_path = os.path.join(vault, tiers.QUEUE_RELPATH)
        with open(queue_path, encoding="utf-8") as f:
            queued = f.read()
        self.assertIn("broken_link", queued)
        self.assertIn("one.md", queued)
        self.assertIn("WARN", queued)

    def test_3_quarantine_divert_lands_in_inbox_and_commit_proceeds(self):
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        _write(os.path.join(vault, "10-Projects", "demo", "three.md"),
               _note(id_="n-b2b2b2b2b2b2b2b2", body="\nSee [[does-not-exist]].\n"))
        before = _head(vault)
        code, out = run_cli(["commit", "--vault", vault, "--quarantine",
                             "-m", "quarantine the offender"])
        self.assertEqual(code, 0, out)
        self.assertIn("quarantined", out)
        self.assertNotEqual(before, _head(vault), out)
        quarantine_dir = os.path.join(vault, "00-Inbox", "quarantine")
        self.assertTrue(os.path.isdir(quarantine_dir), out)
        # The content's new home is confirmed on disk FIRST (this is the
        # "before" half of MINOR 2's "assert the inbox copy exists before the
        # original is removed" contract; if this were empty the original
        # below would still be present, since _quarantine refuses to touch
        # it without a confirmed copy).
        admitted = os.listdir(quarantine_dir)
        self.assertTrue(admitted, "expected an admitted note in 00-Inbox/quarantine/")
        # MINOR 2: the diverted note was unstaged AND its working-tree
        # original removed, because the content already lives on as the
        # quarantined copy asserted above; this is a move, not a loss, so
        # the next `git add -A` cannot re-stage the original as brand new.
        original_path = os.path.join(vault, "10-Projects", "demo", "three.md")
        self.assertFalse(os.path.exists(original_path), out)
        self.assertIn("removed", out)
        committed = _git(vault, "show", "--name-only", "HEAD").stdout
        self.assertNotIn("three.md", committed, out)
        # Drive the "re-staged as new" regression backwards: a further
        # `git add -A` must see nothing left to stage for the original path.
        _git(vault, "add", "-A")
        status = _git(vault, "status", "--porcelain").stdout
        self.assertNotIn("three.md", status, status)

    def test_4_the_gate_never_blocks_on_its_own_queue_file(self):
        """MAJOR 1 regression: commit A queues a WARN (the same write
        run_gate() itself performs); commit B, an unrelated clean note,
        stages the now-untracked queue file for the first time alongside it.
        Driven backwards: reverting the TELEMETRY_PREFIX exclusion in
        run_gate() makes this fail (queue file staged as NEW, no
        frontmatter, ERROR-blocking the whole commit)."""
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        finding = {"kind": "broken_link", "path": "10-Projects/demo/one.md",
                   "detail": "[[does-not-exist]]"}
        self.assertTrue(tiers.append_queue(vault, [(finding, "broken_link")]))
        queue_path = os.path.join(vault, tiers.QUEUE_RELPATH)
        self.assertTrue(os.path.exists(queue_path))
        _write(os.path.join(vault, "10-Projects", "demo", "clean.md"),
               _note(id_="n-c3c3c3c3c3c3c3c3"))
        before = _head(vault)
        code, out = run_cli(["commit", "--vault", vault,
                             "-m", "unrelated clean note, queue file rides along"])
        self.assertEqual(code, 0, out)
        self.assertNotEqual(before, _head(vault), out)
        committed = _git(vault, "show", "--name-only", "HEAD").stdout
        self.assertIn(tiers.QUEUE_RELPATH.replace(os.sep, "/"), committed, out)

    def test_5_renamed_preexisting_note_downgrades_instead_of_blocking(self):
        """MAJOR 2 regression: a pre-existing note (already carrying a broken
        link) is renamed with a small unrelated edit, which keeps it well
        above the 50 percent similarity threshold. Driven backwards: dropping
        -M50 from staged_new_paths() makes this fail, because --diff-filter=A
        alone (under this fixture's own diff.renames=false) would report the
        renamed target as new and ERROR-block it."""
        vault = os.path.join(self.tmp, "v")
        old_path = os.path.join(vault, "10-Projects", "demo", "one.md")
        os.makedirs(os.path.dirname(old_path), exist_ok=True)
        _write(old_path, _note(body="\nSee [[does-not-exist]].\n"))
        _git(vault, "init", "-q")
        _git(vault, "config", "user.email", "test@example.com")
        _git(vault, "config", "user.name", "Test")
        # A local diff.renames=false proves -M50 (an explicit CLI flag, not
        # ambient config) is what does the work here, not the caller's config.
        _git(vault, "config", "diff.renames", "false")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "initial, already has the broken link")
        new_path = os.path.join(vault, "10-Projects", "demo", "renamed.md")
        _git(vault, "mv", "10-Projects/demo/one.md", "10-Projects/demo/renamed.md")
        with open(new_path, "a", encoding="utf-8") as f:
            f.write("\nan unrelated edit after the rename.\n")
        _git(vault, "add", "-A")
        before = _head(vault)
        code, out = run_cli(["commit", "--vault", vault,
                             "-m", "rename should downgrade, never block"])
        self.assertEqual(code, 0, out)
        self.assertNotEqual(before, _head(vault), out)
        queue_path = os.path.join(vault, tiers.QUEUE_RELPATH)
        with open(queue_path, encoding="utf-8") as f:
            queued = f.read()
        self.assertIn("broken_link", queued)
        self.assertIn("renamed.md", queued)
        self.assertIn("WARN", queued)


if __name__ == "__main__":
    unittest.main()
