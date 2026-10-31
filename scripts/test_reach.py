#!/usr/bin/env python3
"""OUT1's FROZEN harness: a capability is reached from a plain ask through the one door, and the outcome text the intake
recorded is the outcome the receipt and the status verb show. Written by the session before scripts/reach_check.py,
docs/plan/REACH.json and the receipt's outcome field exist; never edited by whoever builds them (HARN1).

    python3 -B scripts/test_reach.py Reach        OUT1.a
    python3 -B scripts/test_reach.py Preserved    OUT1.b

RED BY ASSERTION: a missing file or function is asserted first with a message that names it; nothing here ERRORS or SKIPS.
Every case runs the thing (the checker on a fixture registry, the receipt writer on a fixture run) and judges what it did.
Python 3.9, standard library, hermetic (scratch HOME and temp dirs)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHECK = os.path.join(HERE, "reach_check.py")
REGISTRY = os.path.join(ROOT, "docs", "plan", "REACH.json")


def run_check(registry, env=None):
    p = subprocess.run([sys.executable, "-B", CHECK, "--registry", registry], capture_output=True, text=True,
                       timeout=300, env=dict(os.environ, HOME=env or tempfile.mkdtemp(prefix="reach-home-")))
    return p.returncode, p.stdout + p.stderr


def registry_fixture(rows):
    d = tempfile.mkdtemp(prefix="reach-")
    path = os.path.join(d, "REACH.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"capabilities": rows}, fh)
    return d, path


class Reach(unittest.TestCase):
    """OUT1.a"""

    def setUp(self):
        self.assertTrue(os.path.isfile(CHECK), "scripts/reach_check.py does not exist yet, so nothing can be routed")
        self.assertTrue(os.path.isfile(REGISTRY), "docs/plan/REACH.json does not exist yet, so no capability is registered")

    def test_a_paraphrase_reaches_the_same_verb(self):
        """REQ-OUT1A-01. The shipped registry: every capability's ask and both paraphrases route to its verb."""
        code, out = run_check(REGISTRY)
        self.assertEqual(0, code, "the shipped registry does not reach every capability: %s" % out[-600:])
        self.assertIn("PASS", out)
        self.assertNotIn("FAIL", out)
        with open(REGISTRY, encoding="utf-8") as fh:
            rows = json.load(fh)["capabilities"]
        self.assertGreaterEqual(len(rows), 6, "fewer than the six delivered capabilities are registered")
        for row in rows:
            self.assertGreaterEqual(len(row.get("paraphrases") or []), 2, "%s has fewer than two paraphrases" % row.get("id"))

    def test_an_unreachable_capability_fails(self):
        """REQ-OUT1A-02. A registered capability whose ask routes to help is FAIL, never silently absent."""
        _d, path = registry_fixture([{"id": "ghost", "ask": "please frobnicate the widget registry",
                                      "paraphrases": ["frobnicate widgets", "widget frobnication now"],
                                      "verb": "start", "command": "scripts/brother_run.py", "marker": "RESULT"}])
        code, out = run_check(path)
        self.assertEqual(1, code, "an unreachable capability did not FAIL: %s" % out[-400:])
        self.assertIn("ghost", out)
        self.assertIn("FAIL", out)

    def test_an_unreadable_registry_is_nodata(self):
        """REQ-OUT1A-03."""
        d = tempfile.mkdtemp(prefix="reach-bad-")
        bad = os.path.join(d, "REACH.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        code, out = run_check(bad)
        self.assertEqual(2, code, "an unreadable registry was not NO-DATA: %s" % out[-300:])
        self.assertIn("NO-DATA", out)
        code, out = run_check(os.path.join(d, "missing.json"))
        self.assertEqual(2, code)

    def test_an_empty_registry_is_nodata(self):
        _d, path = registry_fixture([])
        code, out = run_check(path)
        self.assertEqual(2, code, "an empty registry passed: %s" % out[-300:])


class Preserved(unittest.TestCase):
    """OUT1.b"""

    def setUp(self):
        sys.path.insert(0, HERE)
        import receipt_door as RD
        self.RD = RD
        self.assertTrue(hasattr(RD, "outcome_of"), "receipt_door defines no outcome_of yet")

    def fixture_run(self, outcome):
        d = tempfile.mkdtemp(prefix="reach-run-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "receipt"))
        with open(os.path.join(d, "intake.json"), "w") as fh:
            json.dump({"outcome": outcome}, fh)
        return d

    def test_the_receipt_carries_the_intake_outcome(self):
        """REQ-OUT1B-01."""
        d = self.fixture_run("make the login screen stop crashing")
        written = self.RD.write_receipt(d, changed_files=[], checks=[]) if hasattr(self.RD, "write_receipt") else None
        self.assertIsNotNone(written, "receipt_door has no write_receipt(run_dir, changed_files, checks) to call")
        with open(os.path.join(d, "receipt", "receipt.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual("make the login screen stop crashing", doc.get("outcome"), "the receipt does not carry the intake outcome")
        self.assertEqual("make the login screen stop crashing", self.RD.outcome_of(d))

    def test_a_drifted_outcome_refuses_delivery(self):
        """REQ-OUT1B-02. accept_delivery refuses a run whose receipt outcome is not its intake outcome."""
        import accept_delivery as AD
        d = self.fixture_run("make the login screen stop crashing")
        self.RD.write_receipt(d, changed_files=[], checks=[])
        with open(os.path.join(d, "receipt", "receipt.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["outcome"] = "something else entirely"
        with open(os.path.join(d, "receipt", "receipt.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        self.assertTrue(hasattr(AD, "outcome_drift"), "accept_delivery defines no outcome_drift")
        why = AD.outcome_drift(d)
        self.assertTrue(why, "a drifted outcome was not refused")
        self.assertIn("something else entirely", why)

    def test_an_intake_without_an_outcome_is_refused_at_intake(self):
        d = self.fixture_run("")
        with self.assertRaises(Exception):
            self.RD.write_receipt(d, changed_files=[], checks=[])


if __name__ == "__main__":
    unittest.main()
