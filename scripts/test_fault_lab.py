"""test_fault_lab.py: proves scripts/fault_lab.py holds the one law that
makes it worth having.

THE LAW: the fault lab imports no product module. A harness that imported
door, loop_bridge, claim_store, brother_run, work_record, worktree_lane,
integrate, graph_loop, model_worker or scope_audit would recreate the exact
blind spot it exists to close (an observer sharing the implementation's own
abstraction boundary), so this is asserted mechanically from the file's own
AST rather than trusted from its docstring.

The second test is a fast, network-free smoke check: --list prints exactly
the four scenario names the roadmap names, with no install and no subprocess
fan-out, so this suite stays cheap even though the scenarios themselves
(scripts/fault_lab.py itself, registered separately) need a real `claude`
binary and take longer.
"""
import ast
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FAULT_LAB = os.path.join(HERE, "fault_lab.py")

#: Every module this estate ships that the spine (brother_run.py and what it
#: calls) is built from. Importing any of these from the harness would let
#: it drive the product's own internals instead of its public CLI.
PRODUCT_MODULES = {
    "door", "loop_bridge", "claim_store", "brother_run", "work_record",
    "worktree_lane", "integrate", "graph_loop", "model_worker", "scope_audit",
    "bm_worker_spawn", "bm_verify", "bm_repair",
}


def _imported_names(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class NoProductImport(unittest.TestCase):
    def test_fault_lab_imports_no_product_module(self):
        imported = _imported_names(FAULT_LAB)
        overlap = imported & PRODUCT_MODULES
        self.assertEqual(overlap, set(),
                         "fault_lab.py must treat the product as a "
                         "subprocess only; it imported %r directly, which "
                         "recreates the blind spot this file exists to "
                         "close" % sorted(overlap))

    def test_a_hand_added_product_import_would_fail_this_test(self):
        """The meta-test's own self-test: a name from PRODUCT_MODULES really
        would be caught, so a pass above is not merely an empty overlap by
        construction (e.g. a typo in PRODUCT_MODULES that matches nothing)."""
        fake_imports = {"door", "os", "sys"}
        overlap = fake_imports & PRODUCT_MODULES
        self.assertEqual(overlap, {"door"})


class ListSmoke(unittest.TestCase):
    def test_list_prints_exactly_the_four_scenarios(self):
        proc = subprocess.run([sys.executable, FAULT_LAB, "--list"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        names = set(proc.stdout.split())
        self.assertEqual(names, {"fail_then_repair", "crash_then_bare_invoke",
                                 "two_process_race", "kill_holding_lock"},
                         proc.stdout)


if __name__ == "__main__":
    unittest.main()
