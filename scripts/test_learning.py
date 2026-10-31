#!/usr/bin/env python3
"""LEARN1's FROZEN harness: a ledger rule is applied at admission and named in the stage row, the same induced failure
recurs with memory off and not with memory on, and a worker's brief carries the vault recall for its files. Written by
the session before the code; never edited by whoever builds it (HARN1).

    python3 -B scripts/test_learning.py Applied      LEARN1.a
    python3 -B scripts/test_learning.py Prevented    LEARN1.b
    python3 -B scripts/test_learning.py Recalled     LEARN1.c

RED BY ASSERTION, one condition per case, hermetic (scratch HOME, scratch ledgers). Python 3.9, standard library."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
sys.path.insert(0, HERE)
EVAL = os.path.join(HERE, "learn_eval.py")
FIXTURE = os.path.join(HERE, "learn_eval_fixture.json")


def need(case, mod, name, what):
    case.assertTrue(hasattr(mod, name), "%s defines no %s" % (what, name))
    return getattr(mod, name)


def ledger(rows):
    d = tempfile.mkdtemp(prefix="learn-ledger-")
    path = os.path.join(d, "brother-failures.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


class Applied(unittest.TestCase):
    """LEARN1.a"""

    def test_a_matching_rule_is_named_at_admission(self):
        """REQ-LEARN1A-01."""
        import failure_ledger as FL
        lessons_for = need(self, FL, "lessons_for", "failure_ledger")
        path = ledger([{"unit": "U1", "sub": "U1.a", "class": "import:missing", "rule": "R-IMPORT-1",
                        "lesson": "name the module in the brief", "ts": 1}])
        got = lessons_for("U1", "U1.a", path)
        self.assertEqual(["R-IMPORT-1"], [r.get("rule") for r in got], "the matching rule was not returned: %r" % (got,))
        import stage_log as SL
        enter = need(self, SL, "enter", "stage_log")
        import inspect
        self.assertIn("lesson", inspect.signature(enter).parameters, "stage_log.enter takes no lesson")

    def test_no_match_writes_no_lesson(self):
        """REQ-LEARN1A-02. No matching rule means an empty lesson, never the first rule in the file."""
        import failure_ledger as FL
        lessons_for = need(self, FL, "lessons_for", "failure_ledger")
        path = ledger([{"unit": "OTHER", "sub": "OTHER.a", "class": "import:missing", "rule": "R-IMPORT-1", "ts": 1}])
        self.assertEqual([], lessons_for("U1", "U1.a", path))

    def test_an_unreadable_ledger_is_nodata_not_a_hold(self):
        import failure_ledger as FL
        lessons_for = need(self, FL, "lessons_for", "failure_ledger")
        got = lessons_for("U1", "U1.a", os.path.join(tempfile.mkdtemp(), "none.jsonl"))
        self.assertEqual([], got)


class Prevented(unittest.TestCase):
    """LEARN1.b"""

    def setUp(self):
        self.assertTrue(os.path.isfile(EVAL), "scripts/learn_eval.py does not exist yet")
        self.assertTrue(os.path.isfile(FIXTURE), "scripts/learn_eval_fixture.json (the frozen failure fixture) does not exist yet")
        import importlib.util
        spec = importlib.util.spec_from_file_location("learn_eval", EVAL)
        self.LE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.LE)
        need(self, self.LE, "evaluate", "learn_eval")

    def test_the_lesson_prevents_the_second_failure(self):
        """REQ-LEARN1B-01."""
        got = self.LE.evaluate(FIXTURE, memory=True, attempts=2)
        self.assertEqual("PASS", got.get("verdict"), "memory on did not prevent the recurrence: %r" % (got,))
        self.assertFalse(got.get("recurred"), got)
        self.assertTrue(got.get("lesson"), "no lesson was named")
        self.assertIsInstance(got.get("cost"), dict)

    def test_memory_off_repeats_it(self):
        """REQ-LEARN1B-02. The control: without memory the failure recurs."""
        got = self.LE.evaluate(FIXTURE, memory=False, attempts=2)
        self.assertTrue(got.get("recurred"), "the control did not recur, so the fixture proves nothing: %r" % (got,))

    def test_a_run_that_cannot_start_is_nodata(self):
        """REQ-LEARN1B-03."""
        d = tempfile.mkdtemp(prefix="learn-bad-")
        bad = os.path.join(d, "fixture.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        got = self.LE.evaluate(bad, memory=True, attempts=2)
        self.assertEqual("NO-DATA", got.get("verdict"), got)


class Recalled(unittest.TestCase):
    """LEARN1.c"""

    def vault(self, lessons):
        root = tempfile.mkdtemp(prefix="learn-vault-")
        for name, text in lessons.items():
            with open(os.path.join(root, name + ".md"), "w", encoding="utf-8") as fh:
                fh.write(text)
        return root

    def test_the_brief_recalls_a_lesson_for_a_touched_file(self):
        """REQ-LEARN1C-01."""
        import build_brief as BB
        recall_block = need(self, BB, "recall_block", "build_brief")
        root = self.vault({"a-lesson": "---\nfiles: [scripts/loop/x.py]\n---\nNever write the park folder from a worker.\n"})
        block = recall_block(["scripts/loop/x.py"], root)
        self.assertIn("RECALLED FOR THESE FILES", block)
        self.assertIn("Never write the park folder", block)
        self.assertIn("untrusted", block.lower())

    def test_an_unreadable_vault_says_nodata(self):
        """REQ-LEARN1C-02."""
        import build_brief as BB
        recall_block = need(self, BB, "recall_block", "build_brief")
        block = recall_block(["scripts/loop/x.py"], os.path.join(tempfile.mkdtemp(), "none"))
        self.assertIn("NO-DATA", block)

    def test_no_lesson_writes_the_heading_with_none(self):
        import build_brief as BB
        recall_block = need(self, BB, "recall_block", "build_brief")
        block = recall_block(["scripts/loop/x.py"], self.vault({}))
        self.assertIn("RECALLED FOR THESE FILES", block)
        self.assertIn("none", block)


if __name__ == "__main__":
    unittest.main()
