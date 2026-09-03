"""Two reading paths, and a reader on the delivery path never meets the
enterprise material.

Archetype D (a delivery team under deadline pressure) must never meet roles,
sign-off mechanics, merge controls, or governance vocabulary. This walks
every link reachable from docs/paths/path-delivery.md to a depth of one and
asserts none of the pages reached carry that vocabulary. Archetype G (a
governed estate) legitimately does meet it, so the same walk is run against
docs/paths/path-governed.md and asserted to go RED: if that check cannot
fail, the clean result on the delivery path proves nothing.

Also asserts every link in all three docs/paths/*.md files resolves to a
file that exists on disk, the same way tools/test_sbe.py's
TestEveryShippedLinkResolves does for the whole documentation tree.
"""
import io
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATHS_DIR = os.path.join(ROOT, "docs", "paths")

# The exact word list the task specifies. Case-insensitive, substring match,
# so "Approval" and "APPROVAL" both count.
FORBIDDEN_WORDS = [
    "approval",
    "approver",
    "governance",
    "CODEOWNERS",
    "branch protection",
    "exception owner",
    "waiver",
]
FORBIDDEN_RE = re.compile(
    "|".join(re.escape(w) for w in FORBIDDEN_WORDS), re.IGNORECASE
)

# Same relative-link pattern tools/test_sbe.py uses for the whole tree:
# markdown links, excluding bare fragments and anything that looks like a URL.
LINK_RE = re.compile(r"\]\(([^)#:]+?)(?:#[^)]*)?\)")

THE_THREE_FILES = ("README.md", "path-delivery.md", "path-governed.md")


def _read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _links(path):
    body = _read(path)
    out = []
    for m in LINK_RE.finditer(body):
        target = m.group(1).strip()
        if not target or target.startswith(("http", "mailto:", "<")):
            continue
        out.append(target)
    return out


def _resolve(from_path, target):
    return os.path.normpath(os.path.join(os.path.dirname(from_path), target))


def _vocab_hits(text):
    return FORBIDDEN_RE.findall(text)


def _depth_one_pages(entry_path):
    """entry_path itself, plus every page it links to that exists on disk."""
    pages = [entry_path]
    for target in _links(entry_path):
        full = _resolve(entry_path, target)
        if os.path.isfile(full):
            pages.append(full)
    return pages


def _vocab_report(entry_path):
    """Map of relpath -> forbidden-word hits, for every page depth-one from entry_path."""
    hits = {}
    for page in _depth_one_pages(entry_path):
        found = _vocab_hits(_read(page))
        if found:
            hits[os.path.relpath(page, ROOT)] = found
    return hits


class TestReadingPathFilesExist(unittest.TestCase):
    def test_the_three_reading_path_files_exist(self):
        for name in THE_THREE_FILES:
            full = os.path.join(PATHS_DIR, name)
            self.assertTrue(os.path.isfile(full), "missing: %s" % full)


class TestReadingPathLinksResolve(unittest.TestCase):
    """A link a reader clicks in any of the three files must land somewhere."""

    def test_every_link_in_all_three_files_resolves_to_a_file_that_exists(self):
        broken = []
        checked = 0
        for name in THE_THREE_FILES:
            full = os.path.join(PATHS_DIR, name)
            for target in _links(full):
                checked += 1
                if not os.path.exists(_resolve(full, target)):
                    broken.append("%s -> %s" % (name, target))
        self.assertGreater(
            checked, 5,
            "too few links were examined (%d) for a clean result to mean anything"
            % checked,
        )
        self.assertEqual(
            broken, [],
            "%d link(s) resolve to nothing: %s" % (len(broken), "; ".join(broken)),
        )


class TestDeliveryPathNeverMeetsEnterpriseVocabulary(unittest.TestCase):
    """The done-check this loop exists for."""

    def test_delivery_path_depth_one_walk_is_clean(self):
        entry = os.path.join(PATHS_DIR, "path-delivery.md")
        hits = _vocab_report(entry)
        self.assertEqual(
            hits, {},
            "the delivery path reaches enterprise vocabulary at depth one: %r" % (hits,),
        )

    def test_calibration_the_same_walk_on_the_governed_path_goes_red(self):
        """Proves the checker above can actually fail.

        Run unmodified, this assertion checks that path-governed.md's
        depth-one walk DOES contain the forbidden words (it legitimately
        should: reviewers, sign-off, and merge-control content belong
        there). If this ever finds nothing, the vocabulary check on the
        delivery path is not testing anything and must not be trusted.
        """
        entry = os.path.join(PATHS_DIR, "path-governed.md")
        hits = _vocab_report(entry)
        self.assertNotEqual(
            hits, {},
            "calibration failed: the governed path's depth-one walk found no "
            "enterprise vocabulary, so a clean result on the delivery path "
            "would prove nothing",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
