#!/usr/bin/env python3
"""MB3.a: every mobile module is classified once, and the classification
matches what really ships.

The map is the additive `modules` key of
docs/architecture/MOBILE-CAPABILITY-MAP.json. Each entry is SHIP (in the
bundle/runtime closure), INTERNAL (used by the repository and its checks,
not shipped, a reason required, optionally `delete_proposed` for the owner
to decide) or DELETE (the file and its test are already gone). The shipped
set is read from scripts/bundle_runtime.py compute_closure(), never typed
by hand, so a module that starts or stops shipping without the map saying
so fails here. Reads the real tree; writes nothing.
"""
import ast
import json
import os
import sys
import unittest
from typing import Dict, List, Set

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bundle_runtime as B  # noqa: E402

MAP_PATH = os.path.join(REPO, "docs", "architecture", "MOBILE-CAPABILITY-MAP.json")
CLASSES = ("SHIP", "INTERNAL", "DELETE")
ORIGINAL_KEYS = ("schema", "chain", "router", "passport", "receipt",
                 "capabilities", "adapter_policy", "invariants")
EXTRA_MODULES = ("context_action_adapter",)
PROPOSED = ("context_action_adapter", "mobile_product_claims")


def mobile_modules(scripts_dir: str) -> List[str]:
    """Sorted basenames (no .py) of every mobile module under scripts_dir,
    tests excluded, plus the extra module the audit counts with them."""
    names = {f[:-3] for f in os.listdir(scripts_dir)
             if f.startswith("mobile_") and f.endswith(".py")}
    names.update(m for m in EXTRA_MODULES
                 if os.path.isfile(os.path.join(scripts_dir, m + ".py")))
    return sorted(names)


def _python_files(repo: str):
    for top in ("scripts", "products"):
        for root, dirs, files in os.walk(os.path.join(repo, top)):
            dirs[:] = [d for d in dirs if d not in ("bundle", "__pycache__")]
            for f in files:
                if f.endswith(".py") and not f.startswith("test_"):
                    yield os.path.join(root, f)


_REFS: Dict[str, Set[str]] = {}


def _refs(path: str) -> Set[str]:
    """Every top level module name `path` imports (absolute imports only)
    plus every bare string constant it holds, read once per file."""
    if path not in _REFS:
        with open(path, encoding="utf-8") as fh:
            try:
                tree = ast.parse(fh.read(), filename=path)
            except SyntaxError as exc:
                raise AssertionError("cannot parse %s: %s" % (path, exc))
        refs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                refs.update("import:" + a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    refs.add("import:" + node.module.split(".")[0])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                refs.add("const:" + node.value)
        _REFS[path] = refs
    return _REFS[path]


def production_callers(scripts_dir: str, module: str) -> Set[str]:
    """Repository-relative non test .py files under scripts/ and products/
    (bundle skipped) that import `module` or hold `module.py` as a bare
    string constant, the closure walk's own rule. A comment is never a
    caller: comments are not in the AST."""
    repo = os.path.dirname(os.path.abspath(scripts_dir))
    own = os.path.join(os.path.abspath(scripts_dir), module + ".py")
    found = set()
    for path in _python_files(repo):
        if os.path.abspath(path) == own:
            continue
        refs = _refs(path)
        if "import:" + module in refs or "const:" + module + ".py" in refs:
            found.add(os.path.relpath(path, repo))
    return found


def shipped_mobile(closure: List[str], modules: List[str]) -> Set[str]:
    """The mobile modules the runtime closure really carries."""
    shipped = {name[:-3] for name in closure if name.endswith(".py")}
    return shipped & set(modules)


def load_entries() -> List[dict]:
    try:
        with open(MAP_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssertionError("cannot read %s: %s" % (MAP_PATH, exc))
    entries = doc.get("modules")
    if not isinstance(entries, list) or not entries:
        raise AssertionError("%s has no non empty `modules` list" % MAP_PATH)
    return entries


def by_class(entries: List[dict], cls: str) -> Set[str]:
    return {e["module"] for e in entries if e.get("class") == cls}


class MobileShipMapTest(unittest.TestCase):
    def setUp(self):
        self.entries = load_entries()
        self.modules = mobile_modules(B.SCRIPTS_DIR)

    def test_every_module_is_classified_exactly_once(self):
        names = [e.get("module") for e in self.entries]
        self.assertEqual(len(names), len(set(names)), "duplicate module entries")
        self.assertEqual(set(names), set(self.modules) | by_class(self.entries, "DELETE"),
                         "unclassified or extra modules")
        self.assertEqual(set(self.modules) - set(names), set())
        for e in self.entries:
            self.assertIn(e.get("class"), CLASSES, e)
            self.assertIsInstance(e.get("reason"), str, e)
            if "delete_proposed" in e:
                self.assertIs(type(e["delete_proposed"]), bool, e)
                self.assertEqual(e["class"], "INTERNAL", e)

    def test_the_shipped_set_is_the_ship_set(self):
        shipped = shipped_mobile(B.compute_closure(), self.modules)
        ship = by_class(self.entries, "SHIP")
        self.assertEqual(shipped - ship, set(), "shipped but not classed SHIP")
        self.assertEqual(ship - shipped, set(), "classed SHIP but not in the closure")

    def test_every_ship_module_is_in_the_bundle_and_current(self):
        for name in sorted(by_class(self.entries, "SHIP")):
            src = os.path.join(B.SCRIPTS_DIR, name + ".py")
            dst = os.path.join(B.RUNTIME_DIR, name + ".py")
            self.assertTrue(os.path.isfile(dst), "%s missing from bundle/runtime" % name)
            with open(src, "rb") as a, open(dst, "rb") as b:
                self.assertEqual(a.read(), b.read(), "%s bundle copy is stale" % name)

    def test_a_module_with_no_production_caller_is_internal_with_a_reason(self):
        roots = {n[:-3] for n in (B.ENTRY,) + tuple(B.SUPPORT_ENTRIES)}
        entry = {e["module"]: e for e in self.entries}
        orphans = set()
        for name in self.modules:
            if name in roots or production_callers(B.SCRIPTS_DIR, name):
                continue
            orphans.add(name)
            self.assertEqual(entry[name]["class"], "INTERNAL", name)
        for name in self.modules:
            if entry[name]["class"] == "INTERNAL":
                self.assertTrue(entry[name]["reason"].strip(), "%s INTERNAL with no reason" % name)
        proposed = {n for n, e in entry.items() if e.get("delete_proposed") is True}
        self.assertEqual(proposed, set(PROPOSED))
        self.assertEqual(proposed - orphans, set(), "a delete proposal names a module with a caller")

    def test_a_delete_entry_means_the_file_is_gone(self):
        for name in by_class(self.entries, "DELETE"):
            self.assertFalse(os.path.exists(os.path.join(B.SCRIPTS_DIR, name + ".py")), name)
            self.assertFalse(os.path.exists(os.path.join(B.SCRIPTS_DIR, "test_" + name + ".py")), name)

    def test_the_existing_map_is_unchanged(self):
        with open(MAP_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        for key in ORIGINAL_KEYS:
            self.assertIn(key, doc)
        self.assertEqual(doc["schema"], "brother-mobile-architecture-v1")
        self.assertEqual(len(doc["capabilities"]), 7)
        self.assertIn("one native lease", doc["invariants"])

    def test_the_battery_registers_the_test(self):
        with open(os.path.join(HERE, "check_all.sh"), encoding="utf-8") as fh:
            lines = [l for l in fh if not l.lstrip().startswith("#")]
        self.assertTrue(any("scripts/test_mobile_ship_map.py" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
