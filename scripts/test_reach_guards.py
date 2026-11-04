#!/usr/bin/env python3
"""OUT1.a slice 2 integrator cases: one fixture per inventory rule in reach_check.check_inventory and per refusal in
door.recommend, so deleting any single guard turns exactly its case red. Written by the integrator after the
implementer's mutation probe left twelve guards that neither scripts/test_reach_inventory.py nor
scripts/test_reach_check.py could see. Each fixture is otherwise valid (the control case proves it), so only the guard
under test can refuse it.

    python3 -B scripts/test_reach_guards.py

Python 3.9, standard library, hermetic (scratch HOME and temp dirs)."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
CHECK = os.path.join(HERE, "reach_check.py")
REGISTRY = os.path.join(ROOT, "docs", "plan", "REACH.json")

CAP = {"id": "fixture-build", "verb": "start", "command": "scripts/brother_run.py", "marker": "receipt",
       "ask": "build me a login screen", "paraphrases": ["add a dark mode toggle to the app", "implement the export feature"]}


def row(rid, **over):
    r = {"id": rid, "user_benefit": "A person gets this outcome.", "source_owners": ["scripts/reach_check.py"],
         "code_exists": True, "shipped": False, "routed": None, "installed_execution": "NO-DATA",
         "disposition": "pending", "unit": "OUT1.d", "reason": "Exposed by its own unit."}
    r.update(over)
    return r


def inventory():
    return [row("vault-recall"), row("claims-proof"), row("mdm-identity"), row("mobile-plan"),
            row("loop-close", disposition="consumer", routed="fixture-build", reason="", unit="")]


def run(inv, caps=None):
    d = tempfile.mkdtemp(prefix="reach-guard-")
    try:
        path = os.path.join(d, "REACH.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": caps or [dict(CAP)], "inventory": inv}, fh)
        p = subprocess.run([sys.executable, "-B", CHECK, "--registry", path], capture_output=True, text=True,
                           timeout=300, env=dict(os.environ, HOME=d))
        return p.returncode, p.stdout + p.stderr
    finally:
        shutil.rmtree(d, True)


def changed(index, **over):
    inv = inventory()
    inv[index].update(over)
    return inv


class InventoryRules(unittest.TestCase):

    def refused(self, inv, words):
        code, out = run(inv)
        self.assertEqual(1, code, out[-500:])
        self.assertIn(words, out)

    def test_the_valid_fixture_passes(self):
        code, out = run(inventory())
        self.assertEqual(0, code, out[-500:])
        self.assertIn("5 inventory rows reconciled", out)

    def test_a_repeated_id_fails(self):
        inv = inventory()
        inv.append(copy.deepcopy(inv[0]))
        self.refused(inv, "id repeats")

    def test_a_missing_fact_fails(self):
        inv = inventory()
        del inv[0]["shipped"]
        self.refused(inv, "does not record shipped")

    def test_a_blank_user_benefit_fails(self):
        self.refused(changed(0, user_benefit="  "), "user_benefit is not a plain sentence")

    def test_an_unknown_installed_execution_fails(self):
        self.refused(changed(0, installed_execution="YES"), "installed_execution 'YES'")

    def test_an_unknown_disposition_fails(self):
        self.refused(changed(0, disposition="shipped"), "disposition 'shipped'")

    def test_a_route_to_an_unregistered_capability_fails(self):
        self.refused(changed(0, routed="no-such-capability"), "routes to 'no-such-capability'")

    def test_a_pending_row_with_no_owning_unit_fails(self):
        self.refused(changed(0, unit="LOOP1.a"), "pending with no owning OUT1 unit")

    def test_an_experimental_row_with_no_label_fails(self):
        self.refused(changed(0, disposition="experimental"), "experimental with no label")

    def test_an_experimental_row_with_a_label_passes(self):
        code, out = run(changed(0, disposition="experimental", label="Experimental: off unless asked."))
        self.assertEqual(0, code, out[-500:])

    def test_a_code_claim_with_no_owners_fails(self):
        self.refused(changed(0, source_owners=[]), "code_exists with no source_owners")

    def test_an_owner_outside_the_repository_fails(self):
        self.refused(changed(0, source_owners=["/etc/hosts"]), "is outside the repository")

    def test_an_owner_that_does_not_exist_fails(self):
        self.refused(changed(0, source_owners=["scripts/no_such_owner_file.py"]), "does not exist")

    def test_code_not_claimed_needs_no_owner(self):
        code, out = run(changed(0, code_exists=False, source_owners=["scripts/no_such_owner_file.py"]))
        self.assertEqual(0, code, out[-500:])


class DoorRefusals(unittest.TestCase):

    def recommend(self, goal, caps):
        import door
        d = tempfile.mkdtemp(prefix="door-guard-")
        try:
            path = os.path.join(d, "REACH.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"capabilities": caps, "inventory": inventory()}, fh)
            return door.recommend(goal, registry=path)
        finally:
            shutil.rmtree(d, True)

    def test_the_valid_capability_is_delivered(self):
        got = self.recommend("build me a login screen", [dict(CAP)])
        self.assertTrue(got["delivered"], got)
        self.assertEqual(("fixture-build", "start"), (got["route"], got["verb"]))

    def test_a_command_without_its_marker_is_not_delivered(self):
        got = self.recommend("build me a login screen", [dict(CAP, marker="no-such-marker-anywhere-7f3a")])
        self.assertFalse(got["delivered"], got)

    def test_a_command_outside_the_repository_is_not_delivered(self):
        got = self.recommend("build me a login screen", [dict(CAP, command="/etc/hosts", marker="localhost")])
        self.assertFalse(got["delivered"], got)

    def test_a_help_route_is_never_delivered(self):
        helpful = dict(CAP, id="fixture-help", verb="help", command="scripts/door.py", marker="def route",
                       ask="explain the widget registry", paraphrases=["explain widgets", "help with the widget registry"])
        # the fixture's entry point is real and carries its marker, so only the help refusal can stop this route
        import door
        self.assertIsNotNone(door.repo_path(helpful["command"]))
        with open(door.repo_path(helpful["command"]), encoding="utf-8") as fh:
            self.assertIn(helpful["marker"], fh.read())
        got = self.recommend("explain the widget registry", [helpful])
        self.assertFalse(got["delivered"], got)
        self.assertEqual("help", got["verb"], got)


class ShippedMarkers(unittest.TestCase):

    def test_no_shipped_marker_is_a_weak_substring(self):
        """Review of #968 (LOW): status, step, board and version match almost any file, so they proved nothing."""
        with open(REGISTRY, encoding="utf-8") as fh:
            caps = json.load(fh)["capabilities"]
        for cap in caps:
            self.assertNotIn(cap.get("marker"), ("status", "step", "board", "version"), cap.get("id"))
            self.assertGreaterEqual(len(cap.get("marker") or ""), 6, "%s marker %r is too short" % (cap.get("id"), cap.get("marker")))


if __name__ == "__main__":
    unittest.main()
