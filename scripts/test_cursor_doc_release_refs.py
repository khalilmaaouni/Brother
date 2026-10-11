"""The Cursor twin of scripts/test_codex_doc_release_refs.py (CX1.a): what
docs/how-to/install-cursor.md must keep true so it cannot go stale when the
manifest moves. It names no release version at all, and no prose line calls
any version the current or latest one.

The Codex page carries more guards (its task sentence, its placeholder, its
internal objects); those are about that page's own shape. The two below are
the release-reference rule both client pages share, which client_parity.py
requires each Codex check to have on the Cursor side.

Each guard is a function returning findings. The real page must give none,
and each guard is fired on a seeded copy that breaks only its own condition,
so one deleted guard turns exactly one case red.

Python 3.9, standard library only. Run: python3 -B scripts/test_cursor_doc_release_refs.py
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE_REL = "docs/how-to/install-cursor.md"
VERSION_RE = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?![\w-])")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
CURRENT_WORD = re.compile(r"\b(current|latest)\b", re.I)


def literal_versions(text):
    """Every release version the page names outside link targets."""
    return ["line %d: %s" % (n, m.group(0)) for n, line in enumerate(text.splitlines(), 1)
            for m in VERSION_RE.finditer(LINK_TARGET.sub("](", line))]


def current_claims(text):
    """Every line that names a version and calls something current or latest."""
    return ["line %d" % n for n, line in enumerate(text.splitlines(), 1)
            if CURRENT_WORD.search(line) and VERSION_RE.search(LINK_TARGET.sub("](", line))]


def page():
    with open(os.path.join(ROOT, PAGE_REL), encoding="utf-8") as fh:
        return fh.read()


class TheRealPageIsClean(unittest.TestCase):
    def test_names_no_release_version(self):
        self.assertEqual(literal_versions(page()), [])

    def test_no_line_calls_a_version_current(self):
        self.assertEqual(current_claims(page()), [])


class EachGuardCatchesItsOwnSeed(unittest.TestCase):
    def test_a_literal_version_in_prose(self):
        seeded = page() + "\nInstall Brother 1.1.2 from the marketplace.\n"
        self.assertEqual(len(literal_versions(seeded)), 1)

    def test_a_version_called_current(self):
        seeded = page() + "\nThe current release is v1.1.2.\n"
        self.assertEqual(len(current_claims(seeded)), 1)

    def test_a_version_inside_a_link_target_is_not_named(self):
        seeded = page() + "\nSee [the releases](https://example.invalid/tag/v1.1.2).\n"
        self.assertEqual(literal_versions(seeded), [])


if __name__ == "__main__":
    unittest.main()
