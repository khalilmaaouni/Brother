#!/usr/bin/env python3
"""HX119.b: the frozen harness for PAGE1, written pages are well formed.

Written by the session before PAGE1 is built, never by its builder (HARN1), and RED ON THE BASE BY AN
ASSERTION. One sub unit closes on this file: PAGE1.a, class Wellformed.

THE DEFECT IT HOLDS. bm_store's generic absolute path pattern masks a slash that does not follow a word
character. A closing HTML tag is exactly that shape, so `</h1>` reads as a path and a generated page
loses its tag. The fix adds `<` to the lookbehind, which is why the first requirement is checked against
the pattern's own source: on the base the lookbehind is (?<![A-Za-z0-9_])/ and the page breaks.

The behavioural requirements import bm_store, which is a large module, so they do it LAZILY and only
after the pattern assertion has passed. On the base that assertion fails first, so the import never runs
and the red stays an assertion rather than becoming an import error.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORE = os.path.join(ROOT, "products/brothermode/tools/bm_store.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"
FIXED_LOOKBEHIND = "(?<![A-Za-z0-9_<])/"


def defines(path, name):
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
               for n in tree.body)


def source(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def store_module():
    """bm_store loaded by path, the way this project's tools load a sibling. Returns None when it cannot
    be loaded, which is a failure for the caller to assert on, never a skip."""
    import importlib.util
    if not os.path.isfile(STORE):
        return None
    spec = importlib.util.spec_from_file_location("bm_store_under_test", STORE)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001  the module is the thing under test; a load failure is the finding
        return None
    return module


class Wellformed(unittest.TestCase):
    """PAGE1.a: one character in the lookbehind, and real paths still masked."""

    def setUp(self):
        if not os.path.isfile(STORE):
            self.fail(ABSENT % os.path.relpath(STORE, ROOT))

    def test_a_closing_tag_survives(self):
        """REQ-PAGE1A-01: a closing tag is never masked. On the base `<h1>T</h1>` becomes
        `<h1>T<[PATH WITHHELD]>`, because the lookbehind does not exclude a preceding `<`."""
        text = source(STORE)
        self.assertIsNotNone(text, "bm_store.py could not be read")
        self.assertIn(FIXED_LOOKBEHIND, text,
                      "the generic path pattern's lookbehind does not exclude a preceding `<`, so a "
                      "closing HTML tag reads as an absolute path and every generated page loses it")

    def test_real_paths_stay_masked(self):
        """REQ-PAGE1A-02: a real absolute path in prose and inside a code element is still masked, so
        the fix narrows the pattern without opening a leak."""
        text = source(STORE)
        self.assertIsNotNone(text, "bm_store.py could not be read")
        self.assertIn(FIXED_LOOKBEHIND, text, "the lookbehind is not fixed yet, so this cannot be judged")
        self.assertTrue(defines(STORE, "mask_absolute_paths"),
                        "bm_store does not define mask_absolute_paths, the funnel every page passes")
        store = store_module()
        self.assertIsNotNone(store, "bm_store could not be loaded, so masking cannot be exercised")
        for sample in ("see /Users/someone/secret/notes.md for detail",
                       "<code>/Users/someone/secret/notes.md</code>"):
            masked = store.mask_absolute_paths(sample)
            self.assertNotIn("/Users/someone", masked,
                             "a real absolute path survived the mask in: %s" % sample)

    def test_every_written_page_parses(self):
        """REQ-PAGE1A-03: a page the writer produces parses with balanced tags after masking."""
        text = source(STORE)
        self.assertIsNotNone(text, "bm_store.py could not be read")
        self.assertIn(FIXED_LOOKBEHIND, text, "the lookbehind is not fixed yet, so this cannot be judged")
        store = store_module()
        self.assertIsNotNone(store, "bm_store could not be loaded, so masking cannot be exercised")
        from html.parser import HTMLParser

        class Balance(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []

            def handle_starttag(self, tag, attrs):
                if tag not in ("br", "img", "meta", "link", "hr", "input"):
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()
                else:
                    self.stack.append("UNBALANCED:" + tag)

        page = "<html><body><h1>Title</h1><p>at /Users/someone/x</p></body></html>"
        parser = Balance()
        parser.feed(store.mask_absolute_paths(page))
        self.assertEqual(parser.stack, [], "the masked page does not parse with balanced tags")


if __name__ == "__main__":
    unittest.main(verbosity=1)
