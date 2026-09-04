#!/usr/bin/env python3
"""Drive reproduce_export backwards: a matching file reproduces, a tampered
one is caught as a mismatch, and unreadable inputs are NO-DATA, never a pass.
The git and export internals are stubbed so the test is deterministic and
touches no real repository, EXCEPT the --verify-tree cases (E80), which build
a throwaway git repository in a temp directory on purpose: that mode's whole
claim is that a stranger with only a clone can run it, so its reading path is
driven against real git, never a stub."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reproduce_export as R  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

    def test_source_tree_is_a_git_checkout_the_exporter_can_ask(self):
        # The exporter selects files with `git ls-files`; an extracted
        # archive has no index, so the reproduction copied nothing and read
        # NO-DATA on the real v1.0.1 proof. A detached worktree answers.
        src = R.source_tree("HEAD")
        self.assertIsNotNone(src)
        self.assertTrue(os.path.exists(os.path.join(src, ".git")))
        proc = subprocess.run(["git", "-C", src, "ls-files", "scripts"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/reproduce_export.py", proc.stdout)

    def test_no_allowlist_is_no_data(self):
        saved = R.EP.load_allowlist
        try:
            R.EP.load_allowlist = lambda p=None: None
            self.assertEqual(R.main(["--tag", "v1", "--public", "/x"]), 2)
        finally:
            R.EP.load_allowlist = saved


class ManifestShape(unittest.TestCase):
    """The manifest is pure: same files in, same bytes out, and the one
    prefix it must not cover really is not covered."""

    def test_lines_are_sha_two_spaces_path_sorted_by_path(self):
        text = R.manifest_text([("b.txt", b"two"), ("a.txt", b"one")])
        paths = [l.split("  ", 1)[1] for l in text.splitlines()]
        self.assertEqual(paths, ["a.txt", "b.txt"])
        self.assertEqual(text.splitlines()[0].split("  ", 1)[0],
                          R._sha(b"one"))

    def test_the_release_notes_directory_is_excluded(self):
        # The note carries the digest, so a digest over the note would have
        # to contain its own hash.
        text = R.manifest_text([("docs/releases/9.9.9.md", b"note"),
                                 ("scripts/a.py", b"one")])
        self.assertEqual([l.split("  ", 1)[1] for l in text.splitlines()],
                          ["scripts/a.py"])

    def test_one_changed_byte_changes_the_digest(self):
        a = R.manifest_digest(R.manifest_text([("a.txt", b"one")]))
        b = R.manifest_digest(R.manifest_text([("a.txt", b"onf")]))
        self.assertNotEqual(a, b)

    def test_a_malformed_manifest_parses_to_none_never_an_empty_pass(self):
        self.assertIsNone(R.parse_manifest("not a manifest line\n"))
        self.assertIsNone(R.parse_manifest("deadbeef  short-sha.txt\n"))
        self.assertIsNone(R.parse_manifest(""))

    def test_a_well_formed_manifest_parses(self):
        text = R.manifest_text([("a.txt", b"one")])
        self.assertEqual(R.parse_manifest(text), [(R._sha(b"one"), "a.txt")])


VERSION = "9.9.9"
TAG = "v9.9.9"


def _make_public_repo(tmp, files, tamper=None, manifest_override=None,
                      note_digest=None, drop_manifest=False):
    """A real git repository carrying an export, its manifest and its note,
    tagged. Real git on purpose: --verify-tree's whole claim is that a
    STRANGER can run it against a clone, so the reading path (git show at a
    tag) is exercised, not stubbed.

    tamper: {path: bytes} written AFTER the manifest is computed, which is
    exactly what a tampered release looks like from the outside."""
    for rel, data in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
    manifest = (manifest_override if manifest_override is not None
                else R.manifest_text(files.items()))
    digest = note_digest or R.manifest_digest(manifest)
    os.makedirs(os.path.join(tmp, "docs", "releases"), exist_ok=True)
    if not drop_manifest:
        with open(os.path.join(tmp, R.manifest_path_for(VERSION)), "w",
                  encoding="utf-8") as fh:
            fh.write(manifest)
    with open(os.path.join(tmp, "docs", "releases", "%s.md" % VERSION), "w",
              encoding="utf-8") as fh:
        fh.write("# Brother %s\n\nExport manifest digest `%s` over %d "
                 "exported file(s).\n" % (VERSION, digest,
                                          len(manifest.splitlines())))
    for rel, data in (tamper or {}).items():
        with open(os.path.join(tmp, rel), "wb") as fh:
            fh.write(data)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.com"],
                ["git", "config", "user.name", "T"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "export"],
                ["git", "tag", TAG]):
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("fixture git failed: %s: %s"
                                 % (cmd, proc.stderr))
    return tmp


class VerifyTreeFromAPublicClone(unittest.TestCase):
    """E80's done check, driven both ways: a clone at the tag reproduces the
    release with no hub access, and every way of breaking it is caught."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="verify-tree-")
        self.files = {"scripts/a.py": b"print('one')\n",
                       "bundle/runtime/brother_run.py": b"# runtime\n",
                       "products/x/CHECKSUMS.sha256": b"abc  y\n"}

    def _verify(self, **kw):
        _make_public_repo(self.tmp, self.files, **kw)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = R.main(["--verify-tree", "--tag", TAG,
                            "--public", self.tmp])
        return code, out.getvalue()

    def test_an_untouched_tag_passes(self):
        code, out = self._verify()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS:", out)
        self.assertIn("3 exported file(s)", out)

    def test_a_one_byte_tamper_in_a_shipped_file_is_caught(self):
        code, out = self._verify(
            tamper={"bundle/runtime/brother_run.py": b"# runtimf\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("MISMATCH: bundle/runtime/brother_run.py", out)
        self.assertIn("FAIL:", out)

    def test_a_file_the_manifest_names_but_the_tag_drops_is_caught(self):
        # The manifest is computed over all three files, then one is never
        # committed: a release that ships less than it claims.
        for rel, data in self.files.items():
            full = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(data)
        manifest = R.manifest_text(self.files.items())
        os.unlink(os.path.join(self.tmp, "scripts/a.py"))
        smaller = {k: v for k, v in self.files.items() if k != "scripts/a.py"}
        _make_public_repo(self.tmp, smaller, manifest_override=manifest)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = R.main(["--verify-tree", "--tag", TAG, "--public", self.tmp])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("MISSING: scripts/a.py", out.getvalue())

    def test_a_manifest_rewritten_to_match_a_tampered_file_is_still_caught(self):
        # The attacker's obvious next move: tamper the file AND rewrite the
        # manifest. The note's stated digest no longer matches the manifest.
        tampered = dict(self.files)
        tampered["bundle/runtime/brother_run.py"] = b"# tampered\n"
        honest_digest = R.manifest_digest(R.manifest_text(self.files.items()))
        _make_public_repo(self.tmp, tampered, note_digest=honest_digest)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = R.main(["--verify-tree", "--tag", TAG, "--public", self.tmp])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("the release note claims", out.getvalue())

    def test_no_manifest_in_the_tag_is_no_data_never_a_pass(self):
        code, out = self._verify(drop_manifest=True)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_a_note_with_no_stated_digest_is_no_data(self):
        _make_public_repo(self.tmp, self.files)
        note = os.path.join(self.tmp, "docs", "releases", "%s.md" % VERSION)
        with open(note, "w", encoding="utf-8") as fh:
            fh.write("# Brother 9.9.9\n\nNo digest here.\n")
        # check=True on all three: these build the fixture the assertion below
        # reads, so a git that failed here would leave the previous tag in
        # place and the test would measure the wrong tree while still passing.
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, capture_output=True,
                       check=True)
        subprocess.run(["git", "commit", "-q", "-m", "strip"], cwd=self.tmp,
                       capture_output=True, check=True)
        subprocess.run(["git", "tag", "-f", TAG], cwd=self.tmp,
                       capture_output=True, check=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = R.main(["--verify-tree", "--tag", TAG, "--public", self.tmp])
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("states no export manifest digest", out.getvalue())

    def test_an_explicit_expect_that_disagrees_fails(self):
        _make_public_repo(self.tmp, self.files)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = R.main(["--verify-tree", "--tag", TAG, "--public", self.tmp,
                            "--expect", "0" * 64])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("FAIL:", out.getvalue())


#: A note in the shape release_note_from_tree.py writes: a stamp line naming
#: the hub commit and the describe string, prose, and the manifest digest
#: sentence. The two %s-bearing fields are the only ones a cut cannot state
#: about the commit that carries it.
_NOTE = ("# Brother %s\n\n## Source revision\n\n"
         "Cut from hub commit `%%s` (hub, private; "
         "`git describe --tags --always`: `v0.9-1-g%%s`).\n"
         "Prose a reader checks, which has to match exactly.\n\n"
         "Export manifest digest `%%s` over 1 exported file(s).\n" % VERSION)


class SelfNamingFiles(unittest.TestCase):
    """The release note and the export manifest each NAME the revision they
    were generated at, so neither can rebuild byte for byte from the revision
    the note itself names: that made this check structurally red for every
    honest cut (v1.0.3: 1312 of 1314, and the two that differed were exactly
    these). They are compared for what they can prove, and these three cases
    fix the boundary of that rule in both directions."""

    REL_MANIFEST = R.manifest_path_for(VERSION)
    REL_NOTE = "docs/releases/%s.md" % VERSION

    def _fixture(self, tag_file=b"one", gen_file=b"one",
                 tag_manifest=None, tag_note_rev="b" * 40):
        tag_manifest = (tag_manifest if tag_manifest is not None
                        else R.manifest_text([("scripts/a.py", tag_file)]))
        gen = {"scripts/a.py": gen_file,
               # a STALE manifest, the one the source revision's tree carries:
               # never compared against the tag, which is the whole point.
               self.REL_MANIFEST: R.manifest_text(
                   [("scripts/a.py", b"the tree before the cut")]).encode(),
               self.REL_NOTE: (_NOTE % ("a" * 40, "a" * 8,
                                        "1" * 64)).encode()}
        tag = {"scripts/a.py": tag_file,
               self.REL_MANIFEST: tag_manifest.encode(),
               self.REL_NOTE: (_NOTE % (tag_note_rev, tag_note_rev[:8],
                                        R.manifest_digest(tag_manifest))
                               ).encode()}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ReproduceExport._run(self, gen, tag, allowlist=("scripts",))
        return code, out.getvalue()

    def test_only_the_self_naming_stamps_differ_so_it_reproduces(self):
        code, out = self._fixture()
        self.assertEqual(code, 0, out)
        self.assertIn("reproduced byte-for-byte 1, self-naming files 2 "
                      "compared by content", out)

    def test_a_real_allowlisted_file_differing_is_not_reproducible(self):
        # the note and the manifest are both fine; scripts/a.py is not.
        code, out = self._fixture(tag_file=b"TAMPERED", gen_file=b"one")
        self.assertEqual(code, 1, out)
        self.assertIn("MISMATCH: scripts/a.py", out)

    def test_a_manifest_naming_a_wrong_hash_is_not_reproducible(self):
        # the tag's manifest names a hash that is not the shipped file's, so
        # recomputing it over the tag's own bytes cannot agree with it.
        wrong = "%s  scripts/a.py\n" % ("0" * 64)
        code, out = self._fixture(tag_manifest=wrong)
        self.assertEqual(code, 1, out)
        self.assertIn("MISMATCH: %s" % self.REL_MANIFEST, out)

    def test_the_allowlist_is_read_from_the_source_revision_not_this_tree(self):
        # Found on the real v1.0.3 proof: hub/main had dropped one path from
        # the allowlist since the cut, so rebuilding with THIS checkout's
        # copy exported one file fewer than the release did and the tag's own
        # manifest named a file the rebuild never generated.
        seen = []
        saved = (R.source_tree, R.EP.load_allowlist)
        src = tempfile.mkdtemp(prefix="allowlist-src-")
        try:
            R.source_tree = lambda rev, root=None: src
            R.EP.load_allowlist = lambda p=None: seen.append(p) or None
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(R.main(["--tag", TAG, "--public", "/x"]), 2)
        finally:
            (R.source_tree, R.EP.load_allowlist) = saved
        self.assertEqual(seen, [os.path.join(
            src, os.path.relpath(R.EP.DEFAULT_ALLOWLIST, R.EP.ROOT))])

    def test_the_note_still_has_to_match_outside_the_two_masked_fields(self):
        # masking the stamp is not masking the note: change one word of the
        # prose and the note is a mismatch again.
        gen_note = _NOTE % ("a" * 40, "a" * 8, "1" * 64)
        tag_note = gen_note.replace("a reader checks", "nobody checks")
        ok, why = R.compare_release_note(gen_note.encode(), tag_note.encode())
        self.assertFalse(ok, why)


class E118TheDenylistIsReadFromTheSourceRevision(unittest.TestCase):
    """The sibling of test_the_allowlist_is_read_from_the_source_revision_
    not_this_tree above, for the other filter, driven through real git
    rather than a stub: a rebuild reads the DENYLIST of the revision it is
    rebuilding, not the one the current checkout carries.

    Measured by lane X7-FIX: build_export_tree called load_denylist() with
    no argument, so the file it read was always this checkout's, whatever
    root it had been handed. A path withheld today was therefore stripped
    out of a rebuild of a release that shipped it, and reproduce_export
    reported a mismatch the release never had."""

    def _fixture_hub(self, tmp):
        """A hub with two revisions: the older one withholds withheld.md by
        its own denylist, the newer one withholds nothing. Returns the
        older revision's sha."""
        for rel, text in (
                ("products/myproduct/tracked.md", "ships\n"),
                ("products/myproduct/withheld.md", "withheld at the cut\n"),
                ("docs/plan/EXPORT-DENYLIST.txt",
                 "products/myproduct/withheld.md\n")):
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@example.com"],
                    ["git", "config", "user.name", "T"],
                    ["git", "add", "-A", "-f"],
                    ["git", "commit", "-q", "-m", "the cut"]):
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True,
                                  text=True)
            if proc.returncode != 0:
                raise AssertionError("fixture git failed: %s: %s"
                                     % (cmd, proc.stderr))
        rev = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        # The newer revision drops the withholding, so the two disagree.
        with open(os.path.join(tmp, "docs", "plan", "EXPORT-DENYLIST.txt"),
                  "w", encoding="utf-8") as fh:
            fh.write("# nothing withheld any more\n")
        for cmd in (["git", "-C", tmp, "add", "-A", "-f"],
                    ["git", "-C", tmp, "commit", "-q", "-m", "since"]):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("fixture git failed: %s: %s"
                                     % (cmd, proc.stderr))
        return rev

    def test_the_older_revision_rebuilds_with_its_own_withholdings(self):
        tmp = tempfile.mkdtemp(prefix="denylist-hub-")
        dest = tempfile.mkdtemp(prefix="denylist-dest-")
        rev = self._fixture_hub(tmp)
        src = R.source_tree(rev, root=tmp)
        self.assertIsNotNone(src, "could not materialise the fixture rev")
        with contextlib.redirect_stdout(io.StringIO()):
            copied = R.EP.build_export_tree(dest, ["products/myproduct"],
                                            root=src)
        self.assertEqual(copied, ["products/myproduct"])
        self.assertTrue(os.path.isfile(os.path.join(
            dest, "products", "myproduct", "tracked.md")))
        self.assertFalse(
            os.path.exists(os.path.join(
                dest, "products", "myproduct", "withheld.md")),
            "the rebuilt revision's own denylist named this path and it "
            "was rebuilt anyway, so the denylist came from some other tree")


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
