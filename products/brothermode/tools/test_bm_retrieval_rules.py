#!/usr/bin/env python3
"""Tests for docs/RETRIEVAL-RULES.md, the retrieval contract page (VB11-02).

WHY THIS EXISTS. The page names six served-path rules and pins each one to a
named existing test with a PIN line: "PIN: slug -> file::Class::method". A
pin is a claim; nothing enforces the claim stays true unless something
re-checks it. This suite IS that check: it parses every PIN line out of the
page, confirms all six required rule slugs are present, and confirms every
named test file, class, and method actually exists (an AST parse of the
named file, never a regex match against its text, so a method mentioned
only in a comment or a docstring does not count as pinned).

NO-DATA DISCIPLINE, stated because this suite is the one place it matters
most: a MISSING page is a FAILURE here, never NO-DATA and never a silent
pass. The page is this suite's whole subject; a suite that shrugs at an
absent subject is not testing anything.

Run: python3 tools/test_bm_retrieval_rules.py      (unittest output, exit 0 or 1)
"""
import ast
import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "docs", "RETRIEVAL-RULES.md")

REQUIRED_SLUGS = frozenset([
    "staged-retrieval-order",
    "identity-trim",
    "staleness-demotion",
    "restriction-withholding",
    "echo-exclusion",
    "audit-on-serve",
])

PIN_RE = re.compile(r"^PIN:\s*([a-z0-9-]+)\s*->\s*(\S+)::(\S+)::(\S+)\s*$", re.M)


def _pins_from_text(text):
    """slug -> (test_file, class_name, method_name) for every PIN line.

    Raises AssertionError, never silently drops, when the same slug is
    pinned twice: two conflicting claims about one rule is a page defect,
    not a thing to resolve by picking one."""
    pins = {}
    dups = set()
    for match in PIN_RE.finditer(text):
        slug, test_file, cls, method = match.groups()
        if slug in pins:
            dups.add(slug)
        pins[slug] = (test_file, cls, method)
    if dups:
        raise AssertionError("duplicate PIN line(s) for: %s" % sorted(dups))
    return pins


def _verify_pin_target(test_file, cls, method):
    """(ok, problem or None) for one pin's target.

    AST-based on purpose: a regex search for the method name would also
    match it appearing in a comment or a docstring, which proves nothing
    about whether the test actually exists. The file must live beside this
    suite (tools/), parse as valid Python, declare a class named cls, and
    that class must declare a method named method directly in its body."""
    path = os.path.join(HERE, test_file)
    if not os.path.isfile(path):
        return False, "%s does not exist" % test_file
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        return False, "%s could not be parsed (%s)" % (test_file, e)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for member in node.body:
                if (isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and member.name == method):
                    return True, None
            return False, "%s::%s has no method %s" % (test_file, cls, method)
    return False, "%s has no class %s" % (test_file, cls)


def check_page(path):
    """(ok, problems) for the retrieval-rules page at path.

    A missing file, an unreadable file, a duplicate PIN slug, a missing
    required slug, or a pin whose target does not actually exist are all
    FAILURES (ok is False, problems is non-empty). Nothing here returns a
    silent pass for an absent or malformed page."""
    if not os.path.isfile(path):
        return False, ["%s does not exist" % path]
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return False, ["%s could not be read (%s)" % (path, e)]
    try:
        pins = _pins_from_text(text)
    except AssertionError as e:
        return False, [str(e)]
    problems = []
    missing = REQUIRED_SLUGS - set(pins)
    if missing:
        problems.append("missing PIN line(s) for: %s" % sorted(missing))
    for slug, target in sorted(pins.items()):
        ok, problem = _verify_pin_target(*target)
        if not ok:
            problems.append("%s: %s" % (slug, problem))
    return (not problems), problems


class RetrievalRulesPageIsPinned(unittest.TestCase):
    """The row's done_check: the real page exists, names all six rules, and
    every pin's target is a real, existing test."""

    def test_the_page_exists(self):
        self.assertTrue(os.path.isfile(PAGE),
                        "docs/RETRIEVAL-RULES.md does not exist")

    def test_all_six_rule_slugs_are_present(self):
        with open(PAGE, encoding="utf-8") as fh:
            pins = _pins_from_text(fh.read())
        missing = REQUIRED_SLUGS - set(pins)
        self.assertEqual(set(), missing,
                         "docs/RETRIEVAL-RULES.md is missing a PIN line for: %s"
                         % sorted(missing))

    def test_every_pinned_test_file_class_and_method_exist(self):
        with open(PAGE, encoding="utf-8") as fh:
            pins = _pins_from_text(fh.read())
        problems = []
        for slug, target in sorted(pins.items()):
            ok, problem = _verify_pin_target(*target)
            if not ok:
                problems.append("%s: %s" % (slug, problem))
        self.assertEqual([], problems, "; ".join(problems))

    def test_the_whole_check_passes_on_the_real_page(self):
        ok, problems = check_page(PAGE)
        self.assertTrue(ok, "; ".join(problems))


class AMissingPageIsAFailureNotNoData(unittest.TestCase):
    """The NO-DATA discipline this suite's docstring promises: an absent
    page is not skipped and not treated as a clean pass, it is refused."""

    def test_a_missing_page_fails_the_check(self):
        ok, problems = check_page(
            os.path.join(HERE, "no-such-retrieval-rules-page.md"))
        self.assertFalse(ok)
        self.assertTrue(problems)


class DeletingARuleLineFailsTheCheck(unittest.TestCase):
    """The backwards proof the row's done_check names: doctor a COPY of the
    real page with one rule's PIN line removed, run the same checker over
    it, and confirm it refuses. This is what makes the earlier whole-check
    test meaningful, rather than a check that would pass anything with a
    PIN-shaped line somewhere in it."""

    def test_removing_the_identity_trim_pin_line_fails_the_check(self):
        with open(PAGE, encoding="utf-8") as fh:
            lines = fh.readlines()
        doctored = [l for l in lines if not l.startswith("PIN: identity-trim ")]
        self.assertLess(len(doctored), len(lines),
                        "fixture drift: no identity-trim PIN line found in "
                        "docs/RETRIEVAL-RULES.md to remove")
        fd, tmp_path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(doctored)
            ok, problems = check_page(tmp_path)
        finally:
            os.unlink(tmp_path)
        self.assertFalse(ok, "the checker passed a page missing a rule's PIN line")
        self.assertTrue(any("identity-trim" in p for p in problems), problems)

    def test_a_page_with_no_pin_lines_at_all_fails_on_every_slug(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("# The retrieval contract\n\nNo pins here.\n")
            ok, problems = check_page(tmp_path)
        finally:
            os.unlink(tmp_path)
        self.assertFalse(ok)
        self.assertTrue(any("missing PIN line(s)" in p for p in problems), problems)


class ThePinTargetCheckIsASTBasedNotRegex(unittest.TestCase):
    """A method name that only APPEARS in the file (a comment, a docstring,
    a string literal) must not count as pinned; only a real method
    declaration inside the named class does."""

    def test_a_method_name_only_mentioned_in_a_docstring_does_not_count(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".py", dir=HERE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(
                    "import unittest\n\n"
                    "class Foo(unittest.TestCase):\n"
                    '    """mentions test_bar here, never defines it"""\n\n'
                    "    def test_real(self):\n"
                    "        pass\n")
            rel = os.path.relpath(tmp_path, HERE)
            ok, problem = _verify_pin_target(rel, "Foo", "test_bar")
        finally:
            os.unlink(tmp_path)
        self.assertFalse(ok)
        self.assertIn("no method", problem)

    def test_a_real_declared_method_does_count(self):
        fd, tmp_path = tempfile.mkstemp(suffix=".py", dir=HERE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(
                    "import unittest\n\n"
                    "class Foo(unittest.TestCase):\n"
                    "    def test_bar(self):\n"
                    "        pass\n")
            rel = os.path.relpath(tmp_path, HERE)
            ok, problem = _verify_pin_target(rel, "Foo", "test_bar")
        finally:
            os.unlink(tmp_path)
        self.assertTrue(ok, problem)


if __name__ == "__main__":
    unittest.main()
