#!/usr/bin/env python3
"""Fixtures for `tools/sbe_decision_record.py`, the write side of the pair
`tools/sbe_decision_verify.py` was, before this file, the only side of
(verification-only, called only from tests: see that tool's own module
docstring). Run: python3 tools/test_sbe_decision_record.py

Every test here builds a real git repository in a temporary directory and
runs the real tool against it, the same "nothing is mocked" rule `tools/
test_sbe_tasks.py`'s own module docstring states: the defect this control
exists for is that a human decision had nowhere reachable to land, and a
mocked filesystem or a mocked git would test the mock, not the landing.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
TOOL = os.path.join(HERE, "sbe_decision_record.py")
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import decisions as decisions_mod  # noqa: E402


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


class RecorderFixture(unittest.TestCase):
    """A fresh, committed repository per test, with no `origin` remote: the
    recorder must still write an answered `origin` field (a `local:` fallback)
    rather than a hollow one, and this fixture is what proves it never has to
    reach for a remote that is not there."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        with io.open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.head = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def record(self, *argv):
        out = subprocess.run([sys.executable, TOOL, "--cwd", self.repo] + list(argv),
                             capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr

    def load(self, change_id, slug):
        base = os.path.join(self.repo, ".sbe", "human-decisions", change_id, slug)
        with io.open(os.path.join(base, "packet.json"), encoding="utf-8") as fh:
            packet = json.load(fh)
        with io.open(os.path.join(base, "decision.json"), encoding="utf-8") as fh:
            decision = json.load(fh)
        return packet, decision


class TestRoundTrip(RecorderFixture):
    def test_a_release_decision_round_trips_and_bind_human_decision_authorizes(self):
        code, text = self.record(
            "--change-id", "CHG-1", "--who", "the accountable engineer",
            "--what", "ship the fix now",
            "--alternative", "HOLD:wait for the missing observation",
            "--flip-condition", "the missing observation lands",
            "--decision", "RELEASE")
        self.assertEqual(code, 0, text)
        self.assertIn("PASS", text)
        packet, decision = self.load("CHG-1", "ship-the-fix-now")
        # THE POINT OF THIS TEST: never trust the tool's own printed verdict.
        # Read the pair back off disk and hand it to the exact function this
        # whole tool exists to stop being verification-only for.
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            decision, packet, self.head)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.assertTrue(evidence.authorizes, "a RELEASE that binds must authorize release")

    def test_a_hold_decision_binds_but_never_authorizes(self):
        code, text = self.record(
            "--change-id", "CHG-2", "--who", "the accountable engineer",
            "--what", "hold the release",
            "--alternative", "RELEASE:ship now",
            "--flip-condition", "the missing observation lands",
            "--decision", "HOLD")
        self.assertEqual(code, 0, text)
        self.assertIn("PASS", text)
        self.assertIn("does NOT authorize", text)
        packet, decision = self.load("CHG-2", "hold-the-release")
        verdict, evidence, _problems = decisions_mod.bind_human_decision(
            decision, packet, self.head)
        self.assertEqual(verdict, "PASS")
        self.assertFalse(evidence.authorizes, "a HOLD must never authorize release")

    def test_the_written_documents_validate_on_their_own_face(self):
        code, text = self.record(
            "--change-id", "CHG-3", "--who", "someone",
            "--what", "do the thing",
            "--alternative", "do nothing",
            "--flip-condition", "the risk materializes",
            "--decision", "RELEASE")
        self.assertEqual(code, 0, text)
        packet, decision = self.load("CHG-3", "do-the-thing")
        from brothersbe import contracts as contracts_mod
        p_verdict, p_evidence, _ = contracts_mod.validate("decision-packet", packet)
        self.assertEqual(p_verdict, "PASS", p_evidence)
        d_verdict, d_evidence, _ = contracts_mod.validate("human-decision", decision)
        self.assertEqual(d_verdict, "PASS", d_evidence)

    def test_a_bare_text_alternative_is_labeled_rather_than_dropped(self):
        code, text = self.record(
            "--change-id", "CHG-4", "--who", "someone",
            "--what", "proceed anyway",
            "--alternative", "wait a week",
            "--flip-condition", "the metric recovers",
            "--decision", "RELEASE")
        self.assertEqual(code, 0, text)
        packet, _decision = self.load("CHG-4", "proceed-anyway")
        self.assertEqual(packet["options"], [{"label": "option 1", "text": "wait a week"}])
        self.assertIn("wait a week", packet["notEstablished"][0])


class TestNoData(RecorderFixture):
    def test_no_alternative_is_refused_and_writes_nothing(self):
        code, text = self.record(
            "--change-id", "CHG-5", "--who", "someone", "--what", "ship it",
            "--flip-condition", "the risk materializes", "--decision", "RELEASE")
        self.assertEqual(code, 3, text)
        self.assertIn("NO-DATA", text)
        self.assertFalse(
            os.path.isdir(os.path.join(self.repo, ".sbe", "human-decisions")),
            "a refused recording must leave no half-written package behind")

    def test_a_directory_with_no_git_repository_is_no_data_not_a_traceback(self):
        empty = tempfile.mkdtemp()
        try:
            out = subprocess.run(
                [sys.executable, TOOL, "--cwd", empty, "--change-id", "CHG-6",
                 "--who", "someone", "--what", "ship it", "--alternative", "wait",
                 "--flip-condition", "x", "--decision", "RELEASE"],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("NO-DATA", out.stdout + out.stderr)
            self.assertNotIn("Traceback", out.stdout + out.stderr)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_a_nonexistent_cwd_is_no_data(self):
        # `self.record` always injects a real --cwd; a nonexistent one is
        # exercised directly rather than through that helper.
        out = subprocess.run(
            [sys.executable, TOOL, "--cwd", os.path.join(self.repo, "does-not-exist"),
             "--change-id", "CHG-7", "--who", "someone", "--what", "ship it",
             "--alternative", "wait", "--flip-condition", "x", "--decision", "RELEASE"],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("NO-DATA", out.stdout + out.stderr)


class TestUnclaimedName(unittest.TestCase):
    """`sbe decide` is `sbe_decide.py`, an unrelated architecture-scoring
    table (the exact P4 defect this tool exists to route around). This pins
    that `sbe record` is a DIFFERENT, reachable command rather than a second
    name for the same one."""

    def test_sbe_record_delegates_to_this_tool_not_to_sbe_decide(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from brothersbe import cli as cli_mod
        names = dict((name, help_) for name, help_, _run in cli_mod.COMMANDS)
        self.assertIn("record", names)
        self.assertIn("decide", names)
        self.assertNotEqual(names["record"], names["decide"])
        self.assertIn("record", cli_mod.PASSTHROUGH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
