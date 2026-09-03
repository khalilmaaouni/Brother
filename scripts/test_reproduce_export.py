#!/usr/bin/env python3
"""Drive reproduce_export backwards: a matching file reproduces, a tampered
one is caught as a mismatch, and unreadable inputs are NO-DATA, never a pass.
The git and export internals are stubbed so the test is deterministic and
touches no real repository."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reproduce_export as R  # noqa: E402


def _seed_export(tmp, files):
    """Write files into a fake export tree and return a build_export_tree
    stub that reports them as copied."""
    for rel, data in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
    return list(files)


class ReproduceExport(unittest.TestCase):
    def _run(self, generated, tag_bytes, allowlist=("scripts",),
              source_rev=None, tag="v9.9.9"):
        saved = (R.EP.load_allowlist, R.source_tree, R.EP.build_export_tree,
                 R.tag_file_bytes, R.os.path.exists)
        self.tmp = tempfile.mkdtemp(prefix="repro-test-")
        exp = os.path.join(self.tmp, "export")
        os.makedirs(exp)
        copied = _seed_export(exp, generated)
        try:
            R.EP.load_allowlist = lambda p=None: list(allowlist)
            R.source_tree = lambda rev, root=None: os.path.join(self.tmp, "src")
            R.EP.build_export_tree = lambda dest, al, root=None: (
                [dest.__setitem__ if False else None],
                _copy_into(exp, dest), copied)[2]
            R.tag_file_bytes = lambda tag, rel, public=None: tag_bytes.get(rel)
            R.os.path.exists = lambda p: True
            argv = ["--tag", tag, "--public", self.tmp]
            if source_rev is not None:
                argv += ["--source-rev", source_rev]
            return R.main(argv)
        finally:
            (R.EP.load_allowlist, R.source_tree, R.EP.build_export_tree,
             R.tag_file_bytes, R.os.path.exists) = saved

    def test_matching_bytes_reproduce_exit_0(self):
        gen = {"scripts/a.py": b"one", "scripts/b.py": b"two"}
        tag = {"scripts/a.py": b"one", "scripts/b.py": b"two"}
        self.assertEqual(self._run(gen, tag), 0)

    def test_a_tampered_file_is_caught_exit_1(self):
        gen = {"scripts/a.py": b"one"}
        tag = {"scripts/a.py": b"TAMPERED"}
        self.assertEqual(self._run(gen, tag), 1)

    def test_a_file_absent_from_the_tag_is_out_of_scope_not_a_mismatch(self):
        # generated but not in the tag (added after the cut): still exit 0 as
        # long as at least one comparable file reproduced.
        gen = {"scripts/a.py": b"one", "scripts/new.py": b"added later"}
        tag = {"scripts/a.py": b"one"}
        self.assertEqual(self._run(gen, tag), 0)

    def test_nothing_comparable_is_no_data_exit_2(self):
        gen = {"scripts/new.py": b"only new files"}
        tag = {}
        self.assertEqual(self._run(gen, tag), 2)

    def test_a_stamped_release_record_reproduces_from_its_pre_stamp_rev(self):
        # A commit cannot contain its own hash, so the hub tree checked out
        # at --source-rev necessarily holds the release record BEFORE the
        # exporter stamped it. The tag carries the STAMPED version. Build
        # the expected tag bytes with the exporter's own stamp function, so
        # this test fails if apply_release_stamp ever drifts from it.
        rev = "abc123deadbeef"
        notes = tempfile.NamedTemporaryFile(delete=False, suffix=".md")
        notes.write(b"# Brother 9.9.9\n\nBody text.\n")
        notes.close()
        R.EP.stamp_source_revision(notes.name, "9.9.9", rev)
        with open(notes.name, "rb") as fh:
            stamped = fh.read()
        os.unlink(notes.name)

        rel = "docs/releases/9.9.9.md"
        gen = {rel: b"# Brother 9.9.9\n\nBody text.\n"}
        tag = {rel: stamped}
        self.assertEqual(self._run(gen, tag, allowlist=("docs",),
                                    source_rev=rev), 0)

    def test_a_tag_stamped_with_a_different_revision_still_mismatches(self):
        # The recorded --source-rev is "rev", but the tag's own record was
        # stamped naming "other-rev": that is a genuinely tampered or
        # mis-stamped record, and applying rev's own stamp must never be
        # rewritten to silently agree with it.
        rev = "abc123deadbeef"
        other_rev = "ffffffff000000"
        notes = tempfile.NamedTemporaryFile(delete=False, suffix=".md")
        notes.write(b"# Brother 9.9.9\n\nBody text.\n")
        notes.close()
        R.EP.stamp_source_revision(notes.name, "9.9.9", other_rev)
        with open(notes.name, "rb") as fh:
            stamped_with_other_rev = fh.read()
        os.unlink(notes.name)

        rel = "docs/releases/9.9.9.md"
        gen = {rel: b"# Brother 9.9.9\n\nBody text.\n"}
        tag = {rel: stamped_with_other_rev}
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self._run(gen, tag, allowlist=("docs",), source_rev=rev)
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH: %s differs between the regenerated "
                       "export and v9.9.9" % rel, out.getvalue())

    def test_no_allowlist_is_no_data(self):
        saved = R.EP.load_allowlist
        try:
            R.EP.load_allowlist = lambda p=None: None
            self.assertEqual(R.main(["--tag", "v1", "--public", "/x"]), 2)
        finally:
            R.EP.load_allowlist = saved


def _copy_into(src_dir, dest_dir):
    # shutil.copytree makes each directory level as it descends, so it
    # never needs a single deep os.makedirs() call. That matters here: the
    # test's own os.path.exists patch (see _run) is global, and a deep
    # makedirs() would misread a not-yet-created parent as already there.
    import shutil
    shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
    return None


if __name__ == "__main__":
    unittest.main()
