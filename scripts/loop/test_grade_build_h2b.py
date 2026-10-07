#!/usr/bin/env python3
"""H2.b: the safety screen's allowlist entry point refuses hostile input.

REQ-H-ALLOW. The import screen itself is old code; this file pins the
entry contract the specification names for H2.b: unsafe() returns a
refusal string or None, never raises, for any caller value, and a build
that carries no import or an admissible standard library import is
admitted by the allowlist.

Run: python3 -B scripts/loop/test_grade_build_h2b.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G  # noqa: E402


def _py(path, text):
    return {"path": path, "new_file_content": text}


class UnsafeRefusesHostileInput(unittest.TestCase):
    def test_unsafe_never_raises_on_a_hostile_build(self):
        for bad in (None, [], (), "x", 42, 3.14, True, b"x"):
            try:
                why = G.unsafe(bad, runners=frozenset())
            except Exception as exc:  # noqa: BLE001
                self.fail("unsafe(%r) raised %s: %s" % (bad, type(exc).__name__, exc))
            self.assertIsInstance(why, str, "unsafe(%r) accepted it" % (bad,))

    def test_unsafe_refuses_a_non_collection_allowed_imports(self):
        for bad in (None, "socket", 42, True, b"x"):
            why = G.unsafe({}, runners=frozenset(), allowed_imports=bad)
            self.assertIsInstance(why, str, "unsafe(allowed_imports=%r) accepted it" % (bad,))

    def test_unsafe_refuses_non_string_names_in_allowed_imports(self):
        for bad in ([1, 2], frozenset([None]), [b"x"], [3.14]):
            why = G.unsafe({}, runners=frozenset(), allowed_imports=bad)
            self.assertIsInstance(why, str, "unsafe(allowed_imports=%r) accepted it" % (bad,))


class AllowlistRefusesOutsideImports(unittest.TestCase):
    def test_socket_import_is_refused_by_name(self):
        why = G.unsafe({"edits": [_py("a.py", "import socket\n")]},
                       runners=frozenset(), allowed_imports=frozenset())
        self.assertIsInstance(why, str)
        self.assertIn("socket", why)

    def test_subprocess_import_is_refused_by_name(self):
        why = G.unsafe({"edits": [_py("a.py", "import subprocess\n")]},
                       runners=frozenset(), allowed_imports=frozenset())
        self.assertIsInstance(why, str)
        self.assertIn("subprocess", why)

    def test_a_plain_stdlib_import_passes(self):
        self.assertIsNone(G.unsafe({"edits": [_py("a.py", "import json\n")]},
                                   runners=frozenset(), allowed_imports=frozenset()))

    def test_a_build_with_no_imports_passes(self):
        self.assertIsNone(G.unsafe({"edits": [_py("a.py", "x = 1\n")]},
                                   runners=frozenset(), allowed_imports=frozenset()))


if __name__ == "__main__":
    unittest.main()
