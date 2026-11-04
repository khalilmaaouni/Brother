#!/usr/bin/env python3
"""reach_check's own guards, one fixture each (OUT1.a integrator cases).

scripts/test_reach.py is OUT1's frozen harness and stays exempt from the battery while Preserved (OUT1.b) is red,
so the three guards it does not reach are pinned here, where the battery runs them:
a capability with fewer than two paraphrases, a command file that lacks its marker, and a command that is not a
readable file. Each fixture is otherwise reachable, so only the guard under test can refuse it.

    python3 -B scripts/test_reach_check.py

Python 3.9, standard library, hermetic (scratch HOME and temp dirs)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "reach_check.py")

# Reachable as written: the ask and both paraphrases route to start, and brother_run.py carries "receipt".
GOOD = {"id": "fixture-build", "verb": "start", "command": "scripts/brother_run.py", "marker": "receipt",
        "ask": "build me a login screen",
        "paraphrases": ["add a dark mode toggle to the app", "implement the export feature"]}


def run_rows(rows):
    d = tempfile.mkdtemp(prefix="reach-check-")
    try:
        path = os.path.join(d, "REACH.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": rows}, fh)
        p = subprocess.run([sys.executable, "-B", CHECK, "--registry", path], capture_output=True, text=True,
                           timeout=300, env=dict(os.environ, HOME=d))
        return p.returncode, p.stdout + p.stderr
    finally:
        shutil.rmtree(d, True)


class ReachCheckGuards(unittest.TestCase):

    def test_the_good_fixture_passes(self):
        """Control: without it, every refusal below could be the fixture being broken rather than the guard."""
        code, out = run_rows([dict(GOOD)])
        self.assertEqual(0, code, out[-400:])
        self.assertIn("PASS: 1 of 1", out)

    def test_a_status_ask_reaches_status_not_start(self):
        """A router that answers start for everything would pass every start fixture above."""
        code, out = run_rows([{"id": "fixture-status", "verb": "status", "command": "scripts/delivery_status.py",
                               "marker": "status", "ask": "where do things stand",
                               "paraphrases": ["show me what happened", "what is the status of the project"]}])
        self.assertEqual(0, code, out[-400:])

    def test_an_unmatched_ask_routes_to_help_never_start(self):
        code, out = run_rows([dict(GOOD, ask="please frobnicate the widget registry")])
        self.assertEqual(1, code, out[-400:])
        self.assertIn("routes to help, not start", out)

    def test_a_tie_between_verbs_routes_to_help(self):
        """"review" and "ship" score one each: a tie is help, never whichever verb happens to be listed first."""
        code, out = run_rows([{"id": "fixture-tie", "verb": "review", "command": "products/brothersbe/tools/sbe_gate.py",
                               "marker": "gate", "ask": "review and ship it",
                               "paraphrases": ["review my migration", "audit this pull request"]}])
        self.assertEqual(1, code, out[-400:])
        self.assertIn("routes to help, not review", out)

    def test_one_paraphrase_is_not_enough(self):
        code, out = run_rows([dict(GOOD, paraphrases=["add a dark mode toggle to the app"])])
        self.assertEqual(1, code, out[-400:])
        self.assertIn("needs an ask and at least two paraphrases", out)

    def test_a_command_without_its_marker_fails(self):
        code, out = run_rows([dict(GOOD, marker="no-such-marker-anywhere-7f3a")])
        self.assertEqual(1, code, out[-400:])
        self.assertIn("does not contain marker", out)

    def test_an_unreadable_command_fails(self):
        code, out = run_rows([dict(GOOD, command="scripts/no_such_entry_point.py")])
        self.assertEqual(1, code, out[-400:])
        self.assertIn("is not a readable file", out)

    def test_a_command_outside_the_repository_fails(self):
        """Review of #968 (MEDIUM): an absolute or parent-relative command was joined onto ROOT and read, so a registry
        naming /etc/hosts with marker localhost PASSed. A command must resolve under the repository."""
        for cmd, marker in (("/etc/hosts", "localhost"), ("../../../../../../etc/hosts", "localhost")):
            code, out = run_rows([dict(GOOD, command=cmd, marker=marker)])
            self.assertEqual(1, code, "%s passed: %s" % (cmd, out[-400:]))
            self.assertIn("outside the repository", out)

    def test_an_unusable_registry_is_no_data_exit_2(self):
        """Review of #968 (LOW): exit 2 was pinned nowhere in the battery."""
        d = tempfile.mkdtemp(prefix="reach-nodata-")
        try:
            bad = os.path.join(d, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            empty = os.path.join(d, "empty.json")
            with open(empty, "w", encoding="utf-8") as fh:
                json.dump({"capabilities": []}, fh)
            for path in (bad, empty, os.path.join(d, "missing.json")):
                p = subprocess.run([sys.executable, "-B", CHECK, "--registry", path], capture_output=True, text=True,
                                   timeout=300, env=dict(os.environ, HOME=d))
                self.assertEqual(2, p.returncode, "%s: %s" % (path, (p.stdout + p.stderr)[-300:]))
                self.assertIn("NO-DATA", p.stdout + p.stderr)
        finally:
            shutil.rmtree(d, True)


if __name__ == "__main__":
    unittest.main()
