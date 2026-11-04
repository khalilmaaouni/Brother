#!/usr/bin/env python3
"""KEY1.e FROZEN harness: every module the key-marking path rule names is inventoried and ships.

Contract (1.2.0 plan, KEY1.e): "Every module KEY-MARKING.json's path_rule names (handover_record among them)
appears in docs/plan/REACH.json under its assigned key group and ships in the bundle. A path_rule module absent from
either is a refusal naming it, not a warning." Mutation to refuse: drop handover_record from REACH.json; the test
names it and refuses.

The path rule lives in the plan pack, which is not in this repository, so it is copied below as a literal table.

How the two facts are decided here (the existing mechanisms, not new ones):
  - INVENTORIED: docs/plan/REACH.json "inventory" is a list of capability-group rows; a row's "id" is the key group
    and its "source_owners" lists repository paths. A module is under its group when that group's row lists a path
    whose file name is <module>.py (and the path exists, which scripts/reach_check.py check_inventory already
    demands of every code_exists row).
  - SHIPS IN THE BUNDLE: bundle/runtime/RUNTIME-MANIFEST.json "files" (scripts modules, path "<module>.py") and
    bundle/runtime/hooks/HOOKS-MANIFEST.json "files" (hook tools, path "<product>/tools/<module>.py"), both written
    by scripts/bundle_runtime.py, plus the file itself present under bundle/runtime.

`model_worker (including budget_ceiling)`: budget_ceiling is a function inside scripts/model_worker.py, not a module
of its own, so the module checked is model_worker and its function is asserted present in that file.

Every case isolates one condition; the synthetic cases start from the real registry shape so a green control proves
the check can pass and each red case proves it can refuse. Python 3.9, standard library, hermetic (read-only)."""
import copy
import glob
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTRY = os.path.join(ROOT, "docs", "plan", "REACH.json")
RUNTIME_MANIFEST = os.path.join(ROOT, "bundle", "runtime", "RUNTIME-MANIFEST.json")
HOOKS_MANIFEST = os.path.join(ROOT, "bundle", "runtime", "hooks", "HOOKS-MANIFEST.json")

#: KEY-MARKING.json path_rule, copied from the plan pack: key group -> modules.
PATH_RULE = {
    "execution-outcome": ("loop_bridge", "model_worker", "receipt_door", "continuity", "handover_record",
                          "bm_controller"),
    "assurance-control": ("bm_fence_hook", "sbe_authority_hook", "sbe_fence_hook", "sbe_bash_write_guard"),
    "vault-recall": ("vault_recall_hook",),
    "vault-conflicts": ("bm_vault_contradiction",),
}
MODULES = [(m, g) for g, ms in PATH_RULE.items() for m in ms]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def repo_source(module):
    """The repository path of <module>.py: scripts/ first, then a product's tools/. None when it is nowhere."""
    hits = [os.path.join("scripts", module + ".py")] if os.path.isfile(
        os.path.join(ROOT, "scripts", module + ".py")) else []
    hits += sorted(os.path.relpath(p, ROOT) for p in glob.glob(
        os.path.join(ROOT, "products", "*", "tools", module + ".py")))
    return hits[0] if hits else None


def shipped_names(runtime_manifest, hooks_manifest):
    """Every file name the bundle manifests list (the module name with .py)."""
    names = set()
    for manifest in (runtime_manifest, hooks_manifest):
        for entry in manifest.get("files") or []:
            names.add(os.path.basename(str(entry.get("path"))))
    return names


def refusals(registry, runtime_manifest, hooks_manifest, rule=PATH_RULE):
    """One line per path_rule module that is absent from its assigned REACH.json group or from the bundle. Empty
    means every module is inventoried and ships. Each line names the module and the group."""
    rows = {r.get("id"): r for r in (registry.get("inventory") or []) if isinstance(r, dict)}
    shipped = shipped_names(runtime_manifest, hooks_manifest)
    bad = []
    for group, modules in rule.items():
        owners = [os.path.basename(p) for p in (rows.get(group) or {}).get("source_owners") or []]
        for module in modules:
            if group not in rows:
                bad.append("REACH.json has no key group %s, so path_rule module %s is not inventoried" % (group, module))
            elif module + ".py" not in owners:
                bad.append("path_rule module %s is missing from REACH.json group %s" % (module, group))
            if module + ".py" not in shipped:
                bad.append("path_rule module %s (group %s) does not ship in the bundle manifests" % (module, group))
    return bad


def covered_registry():
    """The real registry with every path_rule module added under its group: a fully covered table."""
    reg = copy.deepcopy(load(REGISTRY))
    for group, modules in PATH_RULE.items():
        row = next(r for r in reg["inventory"] if r.get("id") == group)
        for module in modules:
            src = repo_source(module)
            if src and src not in row["source_owners"]:
                row["source_owners"].append(src)
    return reg


class KeyPathCovered(unittest.TestCase):

    def setUp(self):
        self.registry = load(REGISTRY)
        self.runtime = load(RUNTIME_MANIFEST)
        self.hooks = load(HOOKS_MANIFEST)

    # -- the real tree -------------------------------------------------------------------------------------
    def test_every_path_rule_module_is_in_reach_json_under_its_group(self):
        bad = [b for b in refusals(self.registry, self.runtime, self.hooks) if "REACH.json" in b]
        self.assertEqual([], bad, "REACH.json does not inventory these path_rule modules:\n  " + "\n  ".join(bad))

    def test_every_path_rule_module_ships_in_the_bundle_manifests(self):
        bad = [b for b in refusals(self.registry, self.runtime, self.hooks) if "bundle" in b]
        self.assertEqual([], bad, "\n  ".join(bad))

    def test_every_path_rule_module_exists_in_source_and_in_the_bundle_tree(self):
        for module, group in MODULES:
            src = repo_source(module)
            self.assertIsNotNone(src, "path_rule module %s (group %s) has no source file" % (module, group))
            copies = glob.glob(os.path.join(ROOT, "bundle", "runtime", "**", module + ".py"), recursive=True)
            self.assertTrue(copies, "path_rule module %s (group %s) has no copy under bundle/runtime" % (module, group))

    def test_every_assigned_group_is_a_row_of_the_inventory(self):
        ids = {r.get("id") for r in self.registry.get("inventory") or []}
        for group in PATH_RULE:
            self.assertIn(group, ids, "key group %s is not an inventory row of REACH.json" % group)

    def test_budget_ceiling_is_still_inside_model_worker(self):
        with open(os.path.join(ROOT, "scripts", "model_worker.py"), encoding="utf-8") as fh:
            self.assertIn("def budget_ceiling", fh.read(), "model_worker no longer carries budget_ceiling")

    # -- the check itself can pass and can refuse (one condition each) --------------------------------------
    def test_a_fully_covered_table_passes(self):
        self.assertEqual([], refusals(covered_registry(), self.runtime, self.hooks))

    def test_dropping_handover_record_from_reach_json_names_it_and_its_group(self):
        reg = covered_registry()
        row = next(r for r in reg["inventory"] if r.get("id") == "execution-outcome")
        row["source_owners"] = [p for p in row["source_owners"] if os.path.basename(p) != "handover_record.py"]
        bad = refusals(reg, self.runtime, self.hooks)
        self.assertEqual(1, len(bad), bad)
        self.assertIn("handover_record", bad[0])
        self.assertIn("execution-outcome", bad[0])

    def test_a_module_listed_only_under_the_wrong_group_is_refused(self):
        reg = covered_registry()
        src = repo_source("handover_record")
        row = next(r for r in reg["inventory"] if r.get("id") == "execution-outcome")
        row["source_owners"] = [p for p in row["source_owners"] if os.path.basename(p) != "handover_record.py"]
        other = next(r for r in reg["inventory"] if r.get("id") == "loop-control")
        other["source_owners"].append(src)
        bad = refusals(reg, self.runtime, self.hooks)
        self.assertEqual(1, len(bad), bad)
        self.assertIn("handover_record", bad[0])

    def test_a_module_absent_from_the_bundle_manifests_is_named_even_when_inventoried(self):
        runtime = copy.deepcopy(self.runtime)
        runtime["files"] = [f for f in runtime["files"] if f.get("path") != "handover_record.py"]
        bad = refusals(covered_registry(), runtime, self.hooks)
        self.assertEqual(1, len(bad), bad)
        self.assertIn("handover_record", bad[0])
        self.assertIn("bundle", bad[0])

    def test_a_hook_tool_absent_from_the_hooks_manifest_is_named(self):
        hooks = copy.deepcopy(self.hooks)
        hooks["files"] = [f for f in hooks["files"] if not str(f.get("path")).endswith("/sbe_fence_hook.py")]
        # SHIP1.a also ships the file under runtime/products/brothersbe/ (the route closure), and a module counts as
        # shipped while any manifest names it; the mutation removes it from both, so the hooks manifest is the one tested
        runtime = copy.deepcopy(self.runtime)
        runtime["files"] = [f for f in runtime["files"] if not str(f.get("path")).endswith("/sbe_fence_hook.py")]
        bad = refusals(covered_registry(), runtime, hooks)
        self.assertEqual(1, len(bad), bad)
        self.assertIn("sbe_fence_hook", bad[0])
        self.assertIn("assurance-control", bad[0])

    def test_a_missing_key_group_row_is_refused_naming_it(self):
        reg = covered_registry()
        reg["inventory"] = [r for r in reg["inventory"] if r.get("id") != "vault-conflicts"]
        bad = refusals(reg, self.runtime, self.hooks)
        self.assertEqual(1, len(bad), bad)
        self.assertIn("vault-conflicts", bad[0])
        self.assertIn("bm_vault_contradiction", bad[0])

    def test_an_empty_inventory_is_a_refusal_not_a_pass(self):
        bad = refusals({"inventory": []}, self.runtime, self.hooks)
        self.assertEqual(len(MODULES), len([b for b in bad if "REACH.json" in b]), bad)


if __name__ == "__main__":
    unittest.main()
