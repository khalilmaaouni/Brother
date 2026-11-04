"""C12.a: every 1.1 release note has its README "What changed" section.

release_versions lists the versions of docs/releases/1.1.*.md (the .md files
only, sorted numerically, 1.1.0 and up; 0.9.x and 1.0.x are out of scope by
name, and a name that does not parse as a version is ignored).
missing_sections names every version with no `## What changed in <version>`
heading outside a code fence. A README that is not UTF-8 fails; no release
file at all is NO-DATA, never a pass.

Spec: docs/plan/specs/C12.md, section C12.a (REQ-C12A-01 to REQ-C12A-03).
Done check: python3 -B scripts/test_readme_changed_sections.py
"""
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import private_terms_scan  # noqa: E402
from self_check_staged import MODEL_CREDIT  # noqa: E402

RELEASE = re.compile(r"1\.1\.(\d+)\.md")
HEADING = re.compile(r"## What changed in (\S+)\s*")


def release_versions(releases_dir):
    found = [int(m.group(1)) for m in map(RELEASE.fullmatch, os.listdir(releases_dir)) if m]
    return ["1.1.%d" % n for n in sorted(found)]


def _headings(readme_text):
    seen, fenced = set(), False
    for line in readme_text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else HEADING.fullmatch(line)
        if m:
            seen.add(m.group(1))
    return seen


def missing_sections(readme_text, versions):
    seen = _headings(readme_text)
    return [v for v in versions if v not in seen]


def section(readme_text, version):
    """The body under `## What changed in <version>`, up to the next ## heading."""
    out, inside = [], False
    for line in readme_text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            m = HEADING.fullmatch(line)
            inside = m is not None and m.group(1) == version
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def read_readme(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8")  # not UTF-8 raises: the check fails, never passes


def problems(releases_dir, readme_path):
    """Every reason the README falls short; empty only when every 1.1 note has its section.
    No release file is NO-DATA, a problem, never a pass."""
    versions = release_versions(releases_dir)
    if not versions:
        return ["NO-DATA: no docs/releases/1.1.*.md to check"]
    return ["no '## What changed in %s' section" % v
            for v in missing_sections(read_readme(readme_path), versions)]


class RealTree(unittest.TestCase):
    def test_the_real_readme_has_every_section(self):
        found = problems(os.path.join(ROOT, "docs", "releases"), os.path.join(ROOT, "README.md"))
        self.assertEqual(found, [], "README.md: %s" % found)

    def test_the_new_section_is_clean(self):
        body = section(read_readme(os.path.join(ROOT, "README.md")), "1.1.4")
        self.assertTrue(body.strip(), "the 1.1.4 section is empty")
        self.assertFalse(re.search("[\u2013\u2014]", body), "an em or en dash in the 1.1.4 section")
        self.assertIsNone(MODEL_CREDIT.search(body), "a model or tool credit in the 1.1.4 section")
        terms = private_terms_scan.load_terms()
        if terms is None:
            self.skipTest("NO-DATA: no private terms file, the private term half did not run")
        self.assertEqual(private_terms_scan.scan_text(body, terms), [], "a private term in the 1.1.4 section")


class Shapes(unittest.TestCase):
    """Fixtures live in memory: the check also runs in a read only sandbox with no temp folder."""

    def _fs(self, names, readme=b""):
        for p in (mock.patch("os.listdir", return_value=list(names)),
                  mock.patch("builtins.open", mock.mock_open(read_data=readme))):
            p.start()
            self.addCleanup(p.stop)

    def test_old_lines_are_out_of_scope(self):
        self._fs(["0.9.3.md", "1.0.20.md", "1.1.md", "1.1.x.md", "1.1.10.md", "1.1.2.md",
                  "1.1.2.notes.txt", "1.1.2.export-manifest.txt", "11.1.3.md"])
        self.assertEqual(release_versions("releases"), ["1.1.2", "1.1.10"])

    def test_empty_folder_lists_nothing_and_reads_no_data(self):
        self._fs([], b"## What changed in 1.1.4\n")
        self.assertEqual(release_versions("releases"), [])
        self.assertEqual(problems("releases", "README.md"), ["NO-DATA: no docs/releases/1.1.*.md to check"])

    def test_one_release_one_section(self):
        self._fs(["1.1.4.md"], b"## What changed in 1.1.5\n")
        self.assertEqual(problems("releases", "README.md"), ["no '## What changed in 1.1.4' section"])
        self._fs(["1.1.4.md"], b"## What changed in 1.1.4\n")
        self.assertEqual(problems("releases", "README.md"), [])

    def test_missing_unreadable_folder_fails(self):
        with self.assertRaises(OSError):
            release_versions(os.path.join(ROOT, "no-such-releases-dir-c12"))

    def test_missing_names_each_absent_version(self):
        text = "# x\n## What changed in 1.1.5\nbody\n## What changed in 1.1.3 \nbody\n"
        self.assertEqual(missing_sections(text, ["1.1.3", "1.1.4", "1.1.5"]), ["1.1.4"])

    def test_a_heading_in_a_code_fence_does_not_count(self):
        text = "```\n## What changed in 1.1.4\n```\n## What changed in 1.1.5\n"
        self.assertEqual(missing_sections(text, ["1.1.4", "1.1.5"]), ["1.1.4"])

    def test_a_deeper_or_prefixed_heading_does_not_count(self):
        text = "### What changed in 1.1.4\n## What changed in 1.1.40\n"
        self.assertEqual(missing_sections(text, ["1.1.4"]), ["1.1.4"])

    def test_section_reads_only_its_own_body(self):
        text = "## What changed in 1.1.5\nfive\n## What changed in 1.1.4\nfour\n## Next\nafter\n"
        self.assertEqual(section(text, "1.1.4"), "four")

    def test_a_tree_before_the_cut_owes_no_section_for_the_version_being_cut(self):
        # the required_fast.sh row: no 1.1.8 note yet, so a README without a 1.1.8 section is whole
        self._fs(["1.1.6.md", "1.1.7.md"], b"## What changed in 1.1.7\n## What changed in 1.1.6\n")
        self.assertEqual(problems("releases", "README.md"), [])

    def test_the_note_the_cut_writes_makes_its_section_owed(self):
        # cut_v1.0.0.sh has written docs/releases/1.1.8.md: the same README now falls short
        self._fs(["1.1.6.md", "1.1.7.md", "1.1.8.md"], b"## What changed in 1.1.7\n## What changed in 1.1.6\n")
        self.assertEqual(problems("releases", "README.md"), ["no '## What changed in 1.1.8' section"])

    def test_a_readme_that_is_not_utf8_fails(self):
        self._fs(["1.1.4.md"], b"\xff\xfe## What changed in 1.1.4\n")
        with self.assertRaises(UnicodeDecodeError):
            problems("releases", "README.md")


if __name__ == "__main__":
    unittest.main()
