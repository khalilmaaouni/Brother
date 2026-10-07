#!/usr/bin/env python3
"""One refusal definition: a documented refusal is a ValueError subclass, and the landing fuzz reads a ValueError as a
refusal (FX-08.1).

WHY. On 2026-09-28 a build that refused correctly (L5d.a) was dropped at landing with 15 "crashes": its module raised
gate_order.NoDataError, which derived from plain Exception, and the fuzz in scripts/loop/land_apply.py only accepts a
ValueError, LookupError, SystemExit or a class defined in the fuzzed module itself. Three modules had hand patched the
same missing base class with a local shim. gate_order's two classes now derive from ValueError, and this file keeps it
that way: it asserts the bases, fuzzes a module that imports and raises each class through the real classifier, and
lints every refusal named class (NoData, Unreadable, CorruptLog) that derives from Exception, RuntimeError or OSError
directly. Such a class must sit in ALLOW with its reason; an ALLOW entry whose class is gone or is now a ValueError
fails too, so the list cannot rot (same rule as EXEMPT in scripts/test_battery_registration.py).
"""
import ast
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# written out one by one, never a loop over names: scripts/test_loop_tool_parity.py reads each insert to know WHICH copy of
# land_apply this file imports (the loop's), and a loop variable reads as undetermined
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))

import gate_order  # noqa: E402
import land_apply  # noqa: E402  (scripts/loop, the real fuzz classifier)
# THE PUBLIC EXPORT DOES NOT SHIP THE DREAM MODULES (hermetic gate, 2026-09-29): there the import fails and the ALLOW
# entries naming them read stale. In the repository (.brother-edition present) both stay strict: a missing module raises
# and a stale entry fails. Outside it, only what is not shipped is left out, named in the skip reason.
IN_REPOSITORY = os.path.isfile(os.path.join(ROOT, ".brother-edition"))
try:
    from plugin.runtime.brother.core import dream_grade  # noqa: E402
except ImportError:
    if IN_REPOSITORY:
        raise
    dream_grade = None
NOT_SHIPPED = "not shipped in this tree: plugin/runtime/brother/core/dream_grade.py; the check runs in the repository"

ROOTS = ("scripts", "plugin/runtime", "products", "tools")
NAME = re.compile(r"NoData|Unreadable|CorruptLog")
PLAIN = frozenset({"Exception", "RuntimeError", "OSError"})
_OWN = "own module only: raised and caught inside its own module, accepted by the fuzz's own module rule"
ALLOW = {  # repository relative path:class name -> reason it may stay on a plain base
    "plugin/runtime/brother/core/dream_propose.py:NoData": "crosses modules (caught by scripts/dream_bridge.py:390,412); "
        "converting needs each caller audited, out of FX-08",
    "plugin/runtime/brother/core/dream_promote.py:NoData": "crosses modules (caught by scripts/dream_bridge.py:442,659,732); "
        "converting needs each caller audited, out of FX-08",
    "scripts/contract_check.py:NoData": "crosses modules (caught by scripts/brother_run.py:518); converting needs its caller "
        "audited, out of FX-08",
    "scripts/roadmap_merge.py:NoData": _OWN,
    "scripts/jbeq_e2e_check.py:NoData": _OWN,
    "scripts/evidence_freshness.py:_NoDataError": _OWN,
    "scripts/orchestrator_boundary.py:BoundaryUnreadable": _OWN,
    "scripts/brother_pass.py:Unreadable": _OWN,
    "scripts/brief_lint.py:Unreadable": _OWN,
    "scripts/activation_audit.py:Unreadable": _OWN,
    "scripts/orchestrator_authority.py:AuthorityUnreadable": _OWN,
    "scripts/lesson_repeat_trial.py:_CaptureUnreadable": _OWN,
    "scripts/spec_precheck.py:Unreadable": _OWN,
    "scripts/bridge_default_model.py:BridgeUnreadable": _OWN,
    "scripts/split_check.py:NoData": _OWN + " (caught by its own main)",
    "scripts/donecheck_L5b.py:_NoData": _OWN + " (caught by its own main; landed by the loop 2026-09-29)",
    "products/brothersbe/tools/sbe_instruction_surface.py:_DeclarationUnreadable": _OWN,
    "products/brothersbe/src/brothersbe/checks.py:RegistryUnreadable": _OWN,
    "products/brothersbe/src/brothersbe/policy.py:PolicyUnreadable": _OWN,
    "products/brothersbe/src/brothersbe/book.py:SourceUnreadable": _OWN,
    "products/brothersbe/src/brothersbe/evidence.py:ReceiptUnreadable": _OWN,
}


def _base_name(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def refusal_classes(root=ROOT, roots=ROOTS):
    """{"path:Class": [base names]} for every refusal named class in a non test module under roots."""
    found = {}
    for top in roots:
        if not os.path.isdir(os.path.join(root, top)):
            raise FileNotFoundError("lint root is missing, the walk would see nothing: " + top)
        for d, dirs, files in os.walk(os.path.join(root, top)):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for f in sorted(files):
                if not f.endswith(".py") or f.startswith("test_") or f.endswith("_test.py"):
                    continue
                path = os.path.join(d, f)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), path)  # a module that does not parse fails here, never skipped
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and NAME.search(node.name):
                        found[rel + ":" + node.name] = [_base_name(b) for b in node.bases]
    return found


def lint(found, allow):
    """(unlisted plain based refusal classes, stale ALLOW entries)."""
    plain = {k for k, bases in found.items() if PLAIN & set(bases)}
    return sorted(plain - set(allow)), sorted(k for k in allow if k not in plain)


def fuzz_through(exc_class, *args):
    """land_apply.fuzz_module's verdict on a module that IMPORTED exc_class and lets it propagate (the row 51 shape)."""
    m = types.ModuleType("fx08_importer")
    m.__dict__["Refusal"] = exc_class
    m.__dict__["ARGS"] = args
    exec("def per_check_seconds(value):\n    raise Refusal(*ARGS)\n", m.__dict__)
    lines = []
    crashes, returned = land_apply.fuzz_module(m, "fx08_importer", [None, 0, "x"], report=lines.append)
    return crashes, returned, lines


class OneRefusalDefinition(unittest.TestCase):
    def test_gate_order_no_data_is_a_value_error(self):
        self.assertTrue(issubclass(gate_order.NoDataError, ValueError))

    def test_gate_order_corrupt_log_is_a_value_error(self):
        self.assertTrue(issubclass(gate_order.CorruptLogError, ValueError))

    @unittest.skipUnless(dream_grade, NOT_SHIPPED)
    def test_dream_grade_no_data_is_a_value_error(self):
        self.assertTrue(issubclass(dream_grade.NoDataError, ValueError))

    def test_gate_order_refusal_from_an_importing_module_is_a_refusal(self):
        self.assertEqual(fuzz_through(gate_order.NoDataError, "no data"), (0, 0, []))

    def test_gate_order_corrupt_log_from_an_importing_module_is_a_refusal(self):
        self.assertEqual(fuzz_through(gate_order.CorruptLogError, "bad line"), (0, 0, []))

    @unittest.skipUnless(dream_grade, NOT_SHIPPED)
    def test_dream_grade_refusal_from_an_importing_module_is_a_refusal(self):
        self.assertEqual(fuzz_through(dream_grade.NoDataError, ("x",)), (0, 0, []))

    def test_the_classifier_still_counts_a_plain_exception_from_elsewhere(self):
        # the contrast that proves fuzz_through can fail: a plain Exception class defined in another module is a crash
        crashes, returned, lines = fuzz_through(type("ElsewhereNoData", (Exception,), {"__module__": "elsewhere"}), "x")
        self.assertEqual((crashes, returned, len(lines)), (3, 0, 3))


class RefusalClassLint(unittest.TestCase):
    def test_no_unlisted_plain_refusal_class_and_no_stale_entry(self):
        allow = ALLOW if IN_REPOSITORY else {k: v for k, v in ALLOW.items()
                                             if os.path.isfile(os.path.join(ROOT, k.split(":")[0]))}
        unlisted, stale = lint(refusal_classes(), allow)
        self.assertEqual(unlisted, [], "a refusal class on a plain base: derive it from ValueError, or add it to ALLOW "
                                       "with its reason")
        self.assertEqual(stale, [], "ALLOW names a class that is gone or is now a ValueError: remove the entry")

    def test_every_allow_entry_has_a_reason(self):
        self.assertEqual([k for k, v in ALLOW.items() if not v.strip()], [])

    def test_lint_refuses_a_new_plain_refusal_class(self):
        found = {"scripts/new.py:FooNoData": ["Exception"], "scripts/ok.py:NoData": ["ValueError"]}
        self.assertEqual(lint(found, {}), (["scripts/new.py:FooNoData"], []))

    def test_lint_refuses_a_stale_allow_entry(self):
        self.assertEqual(lint({"scripts/ok.py:NoData": ["ValueError"]}, {"scripts/ok.py:NoData": "why", "scripts/gone.py:X": "why"}),
                         ([], ["scripts/gone.py:X", "scripts/ok.py:NoData"]))

    def test_the_walk_sees_the_tree(self):
        found = refusal_classes()
        self.assertEqual(found.get("scripts/gate_order.py:NoDataError"), ["ValueError"])
        if IN_REPOSITORY or os.path.isfile(os.path.join(ROOT, "plugin/runtime/brother/core/dream_propose.py")):
            self.assertIn("plugin/runtime/brother/core/dream_propose.py:NoData", found)


if __name__ == "__main__":
    unittest.main()
