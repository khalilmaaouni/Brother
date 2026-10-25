"""Frozen harness for LAT0H (1.1.8): the ratchet floors that forced more hooks are removed, and PERFORMANCE.md is retired.

Split out of scripts/test_hook_latency.py on 2026-10-24: a landing runs every touched test FILE whole (land_apply.py
suite_cmd), so LAT0H's classes sitting beside LAT0's kept LAT0's harness red after LAT0.a landed, and the landing held
the build as an environment defect (run-20261024-003110, HOLD LAT0.a BLOCKED-ENVIRONMENT). The 2026-10-02 lesson, a sub
unit's done check naming a later sub unit's test drops every build at landing, is the same shape. One harness file per
unit that a landing touches; LAT0H is the session's hand work, so no loop landing touches this file.

Both classes are red on the base BY AN ASSERTION. Run: python3 -B scripts/test_hook_floors.py Floors CheckDoc (LAT0H)
"""
import ast
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLS = os.path.join(REPO, "products", "brothermode", "tools")
FLOOR_FILES = [os.path.join(TOOLS, n) for n in ("test_bm_consent.py", "test_bm_hookperf.py", "test_bm_hookbench.py")]


def floor_constants(path):
    """The ratchet floor constants (MIN_WIRED_*) assigned anywhere in a Python file, sorted; an unreadable file raises."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        names |= {t.id for t in targets if isinstance(t, ast.Name) and t.id.startswith("MIN_WIRED_")}
    return sorted(names)


def performance_doc_present(root):
    return os.path.isfile(os.path.join(root, "products", "brothermode", "docs", "PERFORMANCE.md"))


class Floors(unittest.TestCase):
    """LAT0H.a (LAT0.c before the split; by the session, council C1): the ratchet floors that forced more hooks are removed; the three files still pass."""

    def test_no_floor_constants(self):
        self.assertEqual({os.path.basename(p): floor_constants(p) for p in FLOOR_FILES},
                         {os.path.basename(p): [] for p in FLOOR_FILES})

    def test_the_product_tests_still_pass(self):
        for p in FLOOR_FILES:
            r = subprocess.run([sys.executable, "-B", p], cwd=os.path.dirname(TOOLS), capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, "%s: %s" % (os.path.basename(p), (r.stdout + r.stderr)[-400:]))


class CheckDoc(unittest.TestCase):
    """LAT0H.b (LAT0.d before the split; by the session, council C1): PERFORMANCE.md is retired; the product's current pages (README.md and
    docs/limits/CURRENT.md) no longer link it. Historical evidence and handover notes keep their text."""

    def test_the_doc_is_retired(self):
        self.assertFalse(performance_doc_present(REPO), "products/brothermode/docs/PERFORMANCE.md still exists")
        for rel in ("README.md", os.path.join("docs", "limits", "CURRENT.md")):
            with open(os.path.join(REPO, "products", "brothermode", rel), encoding="utf-8") as fh:
                self.assertNotIn("PERFORMANCE.md", fh.read(), rel)


if __name__ == "__main__":
    unittest.main()
