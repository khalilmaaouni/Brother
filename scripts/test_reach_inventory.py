#!/usr/bin/env python3
"""OUT1.a slice 2 FROZEN harness: the full capability inventory and the door's route for a plain goal. Written by the
integrator before the inventory and door.recommend exist; never edited by whoever builds them (HARN1).

RE-FROZEN under HARN1 option B (owner ruling, 2026-11-02): OUT1.n made the door admit a route only from an identified
install, so the command line case, which ran the checkout's door, could no longer reach a route. This revision was
authored by an independent harness author, dispatched by the train driver, who built neither OUT1.n nor the first
attempt at this fix, from the OUT1.a and OUT1.n contracts alone. Every earlier assertion is kept; the command line
now runs an installed copy of the door (scratch HOME, plugin cache layout), the checkout's door is asserted to admit
nothing, and the scratch install is asserted to be a copy that reaches neither the checkout nor the real HOME.

    python3 -B scripts/test_reach_inventory.py Inventory     every capability group has a disposition and five facts
    python3 -B scripts/test_reach_inventory.py Journey       a plain goal reaches execution, or says it cannot

The contract (1.2.0 spec, OUT1.a): reconcile every entry point to a consumer route or a reviewed internal, obsolete,
pending or experimental disposition; include Vault, BrotherDS, MDM, mobile and brother.loop; record code existence,
shipping, routing, installed execution and user benefit separately; an unsupported capability never masquerades as
delivered; the user never copies a command. Mutation the journey must catch: replace a route with help, or remove its
installed entry point.

RED BY ASSERTION: a missing function or field is asserted first with a message naming it. Python 3.9, standard library,
hermetic (scratch HOME and temp dirs)."""
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
DOOR = os.path.join(HERE, "door.py")
REGISTRY = os.path.join(ROOT, "docs", "plan", "REACH.json")
SKILL = os.path.join(ROOT, "bundle", "skills", "using-brother", "SKILL.md")
ADMISSION = os.path.join(ROOT, "docs", "plan", "ADMISSION.json")
RUNTIME = os.path.join(ROOT, "bundle", "runtime")
FACTS = ("code_exists", "shipped", "routed", "installed_execution", "user_benefit")
DISPOSITIONS = ("consumer", "pending", "experimental", "internal", "obsolete")
#: the families the contract names, each matched against an inventory row's id prefix
FAMILIES = {"vault": "vault-", "brotherds": "claims-", "mdm": "mdm-", "mobile": "mobile-", "brother.loop": "loop-"}
APP = ("fix the invoice total rounding bug in the app", "correct the order total calculation in the app",
       "the app shows the wrong invoice total, repair it")
ANALYTICS = ("fix the duplicate keys in the sales report", "correct the analytics query that double counts customers",
             "the dashboard counts some orders twice, repair the dedupe")
UNSUPPORTED = ("please frobnicate the widget registry", "teleport the database to mars")


def registry():
    with open(REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


def run_check(path):
    p = subprocess.run([sys.executable, "-B", CHECK, "--registry", path], capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, HOME=tempfile.mkdtemp(prefix="inv-home-")))
    return p.returncode, p.stdout + p.stderr


def fixture(mutate):
    """A copy of the shipped registry with one change, written to a temp file; returns its path."""
    data = registry()
    mutate(data)
    d = tempfile.mkdtemp(prefix="inv-")
    path = os.path.join(d, "REACH.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def door():
    import importlib
    return importlib.import_module("door")


class Inventory(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.isfile(REGISTRY), "docs/plan/REACH.json does not exist")
        self.rows = registry().get("inventory")
        self.assertIsInstance(self.rows, list, "REACH.json has no inventory list")
        self.assertTrue(self.rows, "the inventory is empty")

    def test_every_named_family_is_inventoried(self):
        ids = [r.get("id", "") for r in self.rows]
        for family, prefix in FAMILIES.items():
            self.assertTrue(any(i.startswith(prefix) for i in ids), "no inventory row for %s (id prefix %r)" % (family, prefix))
        self.assertEqual(len(ids), len(set(ids)), "inventory ids repeat")
        self.assertGreaterEqual(len(ids), 25, "the inventory lists only %d capability groups" % len(ids))

    def test_every_row_records_the_five_facts_and_a_disposition(self):
        for r in self.rows:
            for f in FACTS:
                self.assertIn(f, r, "%s does not record %s" % (r.get("id"), f))
            self.assertIn(r.get("disposition"), DISPOSITIONS, "%s disposition %r" % (r.get("id"), r.get("disposition")))
            self.assertIn(r.get("installed_execution"), ("PASS", "FAIL", "NO-DATA"), r.get("id"))
            self.assertTrue(isinstance(r.get("user_benefit"), str) and r["user_benefit"].strip(), r.get("id"))

    def test_a_consumer_row_names_a_reachable_route(self):
        caps = {c["id"] for c in registry()["capabilities"]}
        consumer = [r for r in self.rows if r.get("disposition") == "consumer"]
        self.assertTrue(consumer, "no capability group is a consumer route")
        for r in consumer:
            self.assertIn(r.get("routed"), caps, "%s is consumer but routes to %r" % (r.get("id"), r.get("routed")))
        for r in self.rows:
            if r.get("disposition") in ("pending", "experimental", "internal", "obsolete"):
                self.assertTrue(isinstance(r.get("reason"), str) and r["reason"].strip(), "%s has no reviewed reason" % r.get("id"))
            if r.get("disposition") == "pending":
                self.assertTrue(str(r.get("unit", "")).startswith("OUT1."), "%s is pending with no owning unit" % r.get("id"))

    def test_the_shipped_registry_checks_clean(self):
        code, out = run_check(REGISTRY)
        self.assertEqual(0, code, out[-800:])

    def test_a_code_claim_with_no_file_fails(self):
        def m(d):
            row = next(r for r in d["inventory"] if r.get("code_exists"))
            row["source_owners"] = ["scripts/no_such_capability_file.py"]
        code, out = run_check(fixture(m))
        self.assertEqual(1, code, "a code_exists claim with a missing file passed: %s" % out[-400:])
        self.assertIn("no_such_capability_file", out)

    def test_a_consumer_row_with_no_route_fails(self):
        def m(d):
            row = next(r for r in d["inventory"] if r.get("disposition") == "consumer")
            row["routed"] = None
        code, out = run_check(fixture(m))
        self.assertEqual(1, code, "a consumer row with no route passed: %s" % out[-400:])

    def test_a_missing_family_fails(self):
        def m(d):
            d["inventory"] = [r for r in d["inventory"] if not r.get("id", "").startswith("vault-")]
        code, out = run_check(fixture(m))
        self.assertEqual(1, code, "an inventory without Vault passed: %s" % out[-400:])
        self.assertIn("vault", out.lower())

    def test_an_unreviewed_disposition_fails(self):
        def m(d):
            row = next(r for r in d["inventory"] if r.get("disposition") != "consumer")
            row["reason"] = ""
        code, out = run_check(fixture(m))
        self.assertEqual(1, code, "a non consumer row with no reason passed: %s" % out[-400:])


class Journey(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """An installed door: the shipped runtime copied into a plugin cache folder under a scratch HOME, the way a
        client installs it, then the source door and registry copied over it so what is tested is this tree's code.
        The scratch folder's removal is registered BEFORE the first copy, so it runs when the copy fails halfway."""
        cls.scratch = os.path.realpath(tempfile.mkdtemp(prefix="reach-installed-"))
        cls.addClassCleanup(shutil.rmtree, cls.scratch, ignore_errors=True)
        cls.home = os.path.join(cls.scratch, "home")
        cls.runtime = os.path.join(cls.home, ".claude", "plugins", "cache", "brother", "brother", "9.9.9", "runtime")
        shutil.copytree(RUNTIME, cls.runtime, ignore=shutil.ignore_patterns("__pycache__"))
        data = os.path.join(cls.runtime, "data")
        shutil.copyfile(DOOR, os.path.join(cls.runtime, "door.py"))
        shutil.copyfile(REGISTRY, os.path.join(data, "REACH.json"))
        shutil.copyfile(ADMISSION, os.path.join(data, "ADMISSION.json"))
        for jid in ("J1", "J2", "J4"):  # the shipped walk records name the machine they were walked on
            path = os.path.join(data, "JOURNEY-%s.json" % jid)
            with open(path, encoding="utf-8") as fh:
                walk = json.load(fh)
            walk["host"] = door()._os_host()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(walk, fh)

    @classmethod
    def child(cls, *argv):
        """Run Python in a child with an isolated interpreter, the scratch HOME as its only home and as its working
        folder, and no inherited environment, so no host variable or real HOME reaches it."""
        return subprocess.run([sys.executable, "-I", "-B"] + list(argv), cwd=cls.home, capture_output=True, text=True,
                              timeout=120, env={"HOME": cls.home, "PATH": "/usr/bin:/bin"})

    @classmethod
    def installed(cls, *args):
        return cls.child(os.path.join(cls.runtime, "door.py"), *args)

    def setUp(self):
        self.assertTrue(hasattr(door(), "recommend"), "door.py defines no recommend")
        self.recommend = door().recommend

    def answer(self, goal, path=None):
        got = self.recommend(goal, registry=path) if path else self.recommend(goal)
        self.assertIsInstance(got, dict, "recommend(%r) returned %r" % (goal, got))
        for k in ("route", "verb", "action", "delivered"):
            self.assertIn(k, got, "recommend(%r) has no %s: %r" % (goal, k, got))
        return got

    def test_three_phrasings_of_an_app_correction_reach_execution(self):
        for goal in APP:
            got = self.answer(goal)
            self.assertTrue(got["delivered"], "%r was not delivered: %r" % (goal, got))
            self.assertEqual("start", got["verb"], "%r routed to %r" % (goal, got))

    def test_three_phrasings_of_an_analytics_correction_reach_execution(self):
        for goal in ANALYTICS:
            got = self.answer(goal)
            self.assertTrue(got["delivered"], "%r was not delivered: %r" % (goal, got))
            self.assertEqual("start", got["verb"], "%r routed to %r" % (goal, got))

    def test_the_action_never_asks_the_person_to_copy_a_command(self):
        for goal in APP + ANALYTICS:
            action = self.answer(goal)["action"]
            self.assertTrue(isinstance(action, str) and action.strip(), goal)
            for token in ("python3", "scripts/", "--", "brother-run"):
                self.assertNotIn(token, action, "the action for %r asks the person to copy %r: %s" % (goal, token, action))

    def test_an_unsupported_goal_is_not_delivered(self):
        for goal in UNSUPPORTED:
            got = self.answer(goal)
            self.assertFalse(got["delivered"], "%r masquerades as delivered: %r" % (goal, got))
            self.assertNotEqual("start", got["verb"], goal)

    def test_a_route_replaced_by_help_breaks_the_journey(self):
        route = self.answer(APP[0])["route"]
        self.assertTrue(route, "the app correction names no route")

        def m(d):
            next(c for c in d["capabilities"] if c["id"] == route)["verb"] = "help"
        got = self.answer(APP[0], fixture(m))
        self.assertFalse(got["delivered"] and got["verb"] == "start", "a route mutated to help still delivered: %r" % got)

    def test_a_missing_entry_point_breaks_the_journey(self):
        route = self.answer(ANALYTICS[0])["route"]
        self.assertTrue(route, "the analytics correction names no route")

        def m(d):
            next(c for c in d["capabilities"] if c["id"] == route)["command"] = "scripts/no_such_entry_point.py"
        got = self.answer(ANALYTICS[0], fixture(m))
        self.assertFalse(got["delivered"], "a route whose entry point is gone still delivered: %r" % got)

    def test_the_cli_answers_with_its_exit_code(self):
        ok = self.installed("--route", APP[0])
        self.assertEqual(0, ok.returncode, ok.stdout + ok.stderr)
        no = self.installed("--route", UNSUPPORTED[0])
        self.assertEqual(2, no.returncode, no.stdout + no.stderr)
        self.assertIn("NO-DATA", no.stdout + no.stderr)

    def test_every_phrasing_of_an_app_correction_reaches_execution_from_the_installed_door(self):
        for goal in APP:
            got = self.installed("--route", goal)
            self.assertEqual(0, got.returncode, "%r: %s" % (goal, got.stdout + got.stderr))
            self.assertIn("route: app-correction", got.stdout, goal)
            for token in ("python3", "scripts/", "--", "brother-run"):
                self.assertNotIn(token, got.stdout, "the installed door asks for %r on %r" % (token, goal))

    def test_the_installed_door_is_a_scratch_copy_that_reaches_neither_the_checkout_nor_the_real_home(self):
        probe = self.child("-c", "import os; print(os.path.realpath(os.path.expanduser('~'))); print(os.path.realpath(os.getcwd()))")
        self.assertEqual(0, probe.returncode, probe.stderr)
        child_home, child_cwd = probe.stdout.split("\n")[:2]
        self.assertEqual(self.home, child_home, "the child's HOME is not the scratch home")
        self.assertEqual(self.home, child_cwd, "the child runs outside the scratch home")
        real_home = os.path.realpath(os.path.expanduser("~"))
        self.assertNotEqual(real_home, child_home, "the scratch home is the real home")
        for outside in (ROOT,):  # not the real home: the estate's gates put TMPDIR under it, and the child's HOME is pinned above
            self.assertFalse((self.scratch + os.sep).startswith(os.path.realpath(outside) + os.sep),
                             "the scratch install sits inside %s" % outside)
        installed = os.path.realpath(os.path.join(self.runtime, "door.py"))
        self.assertTrue(installed.startswith(self.scratch + os.sep), "the installed door is not in the scratch home")
        self.assertNotEqual(os.path.realpath(DOOR), installed, "the installed door is the checkout's door")
        self.assertFalse(os.path.islink(os.path.join(self.runtime, "door.py")), "the installed door is a link")
        with open(DOOR, "rb") as a, open(installed, "rb") as b:
            self.assertEqual(a.read(), b.read(), "the installed door is not this tree's door")
        got = self.installed("--available")
        self.assertEqual(0, got.returncode, got.stdout + got.stderr)
        view = json.loads(got.stdout)
        self.assertEqual("claude", view["host"], "the scratch copy was not identified as an install: %r" % view)
        self.assertIn("app-correction", view["routes"], view)

    def test_the_checkouts_own_door_admits_no_route(self):
        """OUT1.n: only an identified install admits a route, and a source checkout is not one."""
        got = self.child(DOOR, "--route", APP[0])
        self.assertEqual(2, got.returncode, "the checkout's door delivered %r: %s" % (APP[0], got.stdout + got.stderr))
        self.assertIn("route: none", got.stdout)
        view = json.loads(self.child(DOOR, "--available").stdout)
        self.assertEqual([], view["routes"], "the checkout's door lists routes: %r" % view)
        self.assertIsNone(view["host"], "the checkout's door named a host: %r" % view)

    def test_removing_the_installed_entry_point_breaks_the_installed_journey(self):
        route = self.answer(APP[0])["route"]
        with open(REGISTRY, encoding="utf-8") as fh:
            command = next(c for c in json.load(fh)["capabilities"] if c["id"] == route)["command"]
        entry = os.path.join(self.runtime, os.path.basename(command))
        self.assertTrue(os.path.isfile(entry), "the installed copy carries no %s" % command)
        os.rename(entry, entry + ".gone")
        self.addCleanup(os.rename, entry + ".gone", entry)
        got = self.installed("--route", APP[0])
        self.assertEqual(2, got.returncode, "a route whose installed entry point is gone still delivered: %s" % got.stdout)
        self.assertIn("NO-DATA", got.stdout)

    def test_the_skill_routes_through_the_door_and_keeps_its_one_question(self):
        with open(SKILL, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("door.py --route", text, "using-brother SKILL.md never routes a plain goal through door.py --route")
        self.assertIn("Ask one question", text, "the bare one-question rule was lost")


if __name__ == "__main__":
    unittest.main()
