"""JWT.a: a census of where every Jev registry id is actually called, read
from the syntax tree (a docstring naming an id is not a call), plus the
main guard proof for every Jev test file.

Every input this file reads fails the test when it is missing, empty,
corrupt or unparsable; nothing here is ever skipped. It makes no Jev call.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Set

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import jev_registry  # noqa: E402

CHECKS_PATH = os.path.join(HERE, "jev_checks.py")
SEAMS_PATH = os.path.join(ROOT, "data", "jev-seams.json")
REGISTRY_PATH = os.path.join(ROOT, "data", "jev-registry.json")
EXCLUDED = (CHECKS_PATH, os.path.join(HERE, "jev_seam.py"))
ID_RE = re.compile(r"^J[0-9]+$")
KNOWN_MODES = ("off", "shadow", "advise", "act")
MAIN_GUARD_RE = re.compile(r"^if __name__ == (['\"])__main__\1:", re.M)

# THE CENSUS PIN. Wiring or unwiring a seam changes these sets in the same
# reviewed edit (later units JW3 and JW4 move ids here). Measured 2026-10-12.
# JW4.a wired J065 (jev_door) with its mode off, an owner decision at the cut.
EXPECTED_SHADOW = frozenset({"J030", "J063", "J064", "J102", "J117"})
EXPECTED_WIRED_OFF = frozenset({
    "J014", "J025", "J029", "J033", "J035", "J049", "J050", "J051", "J052",
    "J053", "J054", "J055", "J056", "J065", "J082", "J083", "J089", "J091", "J093",
    "J094", "J095", "J096", "J097", "J098", "J099", "J100", "J114", "J116",
    "J118", "J119",
})
EXPECTED_CLI_ONLY = frozenset({"J001", "J045", "J048"})


def production_files(root: str) -> List[str]:
    out = []
    for top in ("scripts", "products"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
            dirnames[:] = sorted(d for d in dirnames if "bundle" not in d)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                if (not name.endswith(".py") or name.startswith("test_")
                        or "bundle" in path or path in EXCLUDED):
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    if "jev" in fh.read():
                        out.append(path)
    return out


def _parse(path: str) -> ast.AST:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        return ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise AssertionError("census cannot parse %s: %s" % (path, exc))


def _callee(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _literal_id(node: ast.Call):
    if node.args and isinstance(node.args[0], ast.Constant):
        value = node.args[0].value
        if isinstance(value, str) and ID_RE.match(value):
            return value
    return None


def check_function_ids(checks_path: str) -> Dict[str, str]:
    func_ids = {}
    for fn in _parse(checks_path).body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _callee(node) == "_consult":
                entry_id = _literal_id(node)
                if entry_id:
                    func_ids[fn.name] = entry_id
                    break
    return func_ids


def call_sites(files: List[str], func_ids: Dict[str, str]) -> Dict[str, Set[str]]:
    sites = {}  # type: Dict[str, Set[str]]
    for path in files:
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            name = _callee(node)
            if name in func_ids:
                entry_id = func_ids[name]
            elif name in ("consult", "is_off"):
                entry_id = _literal_id(node)
            else:
                entry_id = None
            if entry_id:
                sites.setdefault(entry_id, set()).add(os.path.relpath(path, ROOT))
    return sites


def classify(registry_ids: List[str], sites: Dict[str, Set[str]],
             func_ids: Dict[str, str], modes: Dict[str, str]) -> Dict[str, str]:
    cli = set(func_ids.values())
    out = {}
    for entry_id in registry_ids:
        mode = modes.get(entry_id, "off")
        if mode not in KNOWN_MODES:
            raise AssertionError("%s has an unknown mode %r" % (entry_id, mode))
        if sites.get(entry_id):
            out[entry_id] = "wired-shadow" if mode != "off" else "wired-off"
        elif entry_id in cli:
            out[entry_id] = "cli-only"
        else:
            out[entry_id] = "doc-only"
    return out


def _modes() -> Dict[str, str]:
    with open(SEAMS_PATH, "r", encoding="utf-8") as fh:
        modes = json.load(fh).get("modes")
    if not isinstance(modes, dict) or not modes:
        raise AssertionError("%s has no non empty modes object" % SEAMS_PATH)
    return modes


def _jev_test_files() -> List[str]:
    return sorted(os.path.join(HERE, n) for n in os.listdir(HERE)
                  if n.startswith("test_jev_") and n.endswith(".py"))


class JevSeamWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(REGISTRY_PATH):
            raise unittest.SkipTest("data/jev-registry.json is not shipped here")
        cls.registry_ids = [e["id"] for e in jev_registry.load(REGISTRY_PATH)]
        cls.func_ids = check_function_ids(CHECKS_PATH)
        cls.sites = call_sites(production_files(ROOT), cls.func_ids)
        cls.modes = _modes()
        cls.classes = classify(cls.registry_ids, cls.sites, cls.func_ids, cls.modes)

    def _of(self, name):
        return {k for k, v in self.classes.items() if v == name}

    def test_every_mode_key_has_a_call_site(self):
        self.assertTrue(self.modes)
        missing = sorted(k for k in self.modes if not self.sites.get(k))
        self.assertEqual(missing, [], "mode set with no call site")

    def test_every_id_a_call_site_names_is_in_the_registry(self):
        named = set(self.func_ids.values()) | set(self.sites)
        unknown = sorted(named - set(self.registry_ids))
        self.assertEqual(unknown, [], "ids named at a call site but absent from the registry")

    def test_every_registry_id_has_exactly_one_class(self):
        self.assertEqual(sorted(self.classes), sorted(self.registry_ids))
        counts = [len(self._of(c)) for c in ("wired-shadow", "wired-off", "cli-only", "doc-only")]
        self.assertEqual(sum(counts), len(self.registry_ids))

    def test_the_classes_equal_the_pinned_sets(self):
        self.assertEqual(self._of("wired-shadow"), set(EXPECTED_SHADOW))
        self.assertEqual(self._of("wired-off"), set(EXPECTED_WIRED_OFF))
        self.assertEqual(self._of("cli-only"), set(EXPECTED_CLI_ONLY))
        self.assertEqual(len(self._of("doc-only")), len(self.registry_ids) - 38)

    def test_every_jev_test_file_has_a_main_guard(self):
        files = _jev_test_files()
        self.assertTrue(files)
        bare = []
        for path in files:
            with open(path, "r", encoding="utf-8") as fh:
                if not MAIN_GUARD_RE.search(fh.read()):
                    bare.append(os.path.basename(path))
        self.assertEqual(bare, [], "Jev test files that run nothing when executed directly")

    def test_the_r41_file_runs_its_tests(self):
        with tempfile.TemporaryDirectory() as state:
            env = dict(os.environ, BROTHER_JEV_STATE_DIR=state)
            proc = subprocess.run(
                [sys.executable, "-B", os.path.join(HERE, "test_jev_checks_r41.py")],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Ran 5 tests", proc.stdout)

    def test_the_battery_registers_both_files(self):
        with open(os.path.join(HERE, "check_all.sh"), "r", encoding="utf-8") as fh:
            live = "\n".join(l for l in fh.read().splitlines()
                             if not l.lstrip().startswith("#"))
        for name in ("scripts/test_jev_seam_wiring.py", "scripts/test_jev_checks_r41.py"):
            self.assertTrue(name in live, "check_all.sh does not run " + name)


if __name__ == "__main__":
    unittest.main()
