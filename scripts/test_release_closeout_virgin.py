#!/usr/bin/env python3
"""test_release_closeout_virgin: the S4 `virgin` verb (row S4,
READINESS-ROADMAP-2026-08-29.json), driven with no network and a fake `gh`.

WHAT IS TESTED. gate_virgin_install never touches the real GitHub API: a
fake `gh` shell script on PATH stands in for it, so this proves the LOGIC
around the dispatch (grant checked, commands printed, the fake run's id and
conclusion recorded) rather than the network call itself. The four points
the S4 brief asks for, each its own test:

1. No grant at all: NO-DATA, and a matrix built from that one gate is not
   COMPLETE (rc.verdict_table, the same table `all` already uses).
2. A grant naming a different tag: NO-DATA that names the mismatch.
3. A grant for THIS tag, without --dispatch: the two commands are printed,
   nothing runs (no fake `gh` needed for this one; PATH is untouched).
4. A grant for THIS tag, with --dispatch: the fake `gh` is invoked, and the
   run id and conclusion it reports come back on the gate.
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_closeout as rc  # noqa: E402

TAG = "v9.9.9"
VERSION = "9.9.9"
REPO = "khalilmaaouni/brother-virgin-test"


def make_args(grant, dispatch=False, poll_timeout=1.0, poll_interval=0.02):
    return argparse.Namespace(version=VERSION, grant=grant, dispatch=dispatch,
                              repo=REPO, poll_timeout=poll_timeout,
                              poll_interval=poll_interval)


def write_grant(path, tag, hours_old=0.0):
    issued = (datetime.datetime.now(datetime.timezone.utc) -
             datetime.timedelta(hours=hours_old))
    body = {
        "repo": REPO,
        "workflow": rc.VIRGIN_WORKFLOW,
        "tag": tag,
        "issued": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estimated_minutes": 20,
        "reason": "test grant",
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    return path


def write_fake_gh(bin_dir, run_id=999, conclusion="success"):
    """A `gh` that answers exactly the two shapes gate_virgin_install uses,
    never a real network call."""
    path = os.path.join(bin_dir, "gh")
    body = (
        "#!/bin/sh\n"
        "if [ \"$1\" = workflow ] && [ \"$2\" = run ]; then\n"
        "  echo dispatched\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = run ] && [ \"$2\" = list ]; then\n"
        "  echo '[{\"databaseId\": %d, \"status\": \"completed\", "
        "\"conclusion\": \"%s\"}]'\n"
        "  exit 0\n"
        "fi\n"
        "echo \"fake gh: unrecognized args: $*\" 1>&2\n"
        "exit 1\n" % (run_id, conclusion))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)
    return path


class VirginGrantHelpers(unittest.TestCase):
    """The pure functions, driven directly: a wrong answer here would make
    every gate test above look right for the wrong reason."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="virgin-grant-helpers.")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def test_repo_slug_from_url(self):
        self.assertEqual(
            rc.repo_slug_from_url("https://github.com/khalilmaaouni/Brother"),
            "khalilmaaouni/Brother")

    def test_missing_grant_file_is_no_data(self):
        grant, why = rc.read_virgin_grant(os.path.join(self.work, "nope.json"))
        self.assertIsNone(grant)
        self.assertIn("no grant at", why)

    def test_malformed_grant_file_is_no_data(self):
        path = os.path.join(self.work, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        grant, why = rc.read_virgin_grant(path)
        self.assertIsNone(grant)
        self.assertIn("not valid JSON", why)

    def test_grant_for_a_different_tag_is_refused(self):
        ok, why = rc.virgin_grant_ok({"tag": "v1.0.0"}, "v2.0.0")
        self.assertFalse(ok)
        self.assertIn("v1.0.0", why)
        self.assertIn("v2.0.0", why)

    def test_expired_grant_is_refused(self):
        old = (datetime.datetime.now(datetime.timezone.utc) -
              datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok, why = rc.virgin_grant_ok({"tag": "v2.0.0", "issued": old},
                                     "v2.0.0")
        self.assertFalse(ok)
        self.assertIn("expired", why)

    def test_fresh_grant_for_the_right_tag_is_accepted(self):
        now = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        ok, why = rc.virgin_grant_ok({"tag": "v2.0.0", "issued": now},
                                     "v2.0.0")
        self.assertTrue(ok, why)


class TheVirginGate(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="virgin-gate.")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.ev = rc.Evidence(os.path.join(self.work, "evidence"))

    def _gate(self):
        return rc.Gate("S4", "virgin-install", "a gate", True)

    def test_no_grant_reads_no_data_and_the_matrix_is_not_complete(self):
        gate = self._gate()
        rc.gate_virgin_install(
            make_args(grant=os.path.join(self.work, "absent.json")),
            self.ev, gate)
        self.assertEqual(gate.verdict, "NO-DATA")
        self.assertIn("no grant at", gate.why)
        text, code = rc.verdict_table([gate])
        self.assertEqual(code, 1)
        self.assertIn("CLOSEOUT NOT COMPLETE", text)

    def test_a_grant_for_another_tag_reads_no_data_naming_the_mismatch(self):
        grant = write_grant(os.path.join(self.work, "grant.json"),
                            tag="v1.0.0")
        gate = self._gate()
        rc.gate_virgin_install(make_args(grant=grant), self.ev, gate)
        self.assertEqual(gate.verdict, "NO-DATA")
        self.assertIn("v1.0.0", gate.why)
        self.assertIn(TAG, gate.why)

    def test_a_valid_grant_without_dispatch_prints_the_commands_and_stops(self):
        grant = write_grant(os.path.join(self.work, "grant.json"), tag=TAG)
        gate = self._gate()
        rc.gate_virgin_install(make_args(grant=grant, dispatch=False),
                               self.ev, gate)
        self.assertEqual(gate.verdict, "NO-DATA")
        self.assertIn("--dispatch", gate.why)
        joined = "\n".join(gate.lines)
        self.assertIn("gh workflow run %s --ref %s" %
                      (rc.VIRGIN_WORKFLOW, TAG), joined)
        self.assertIn("gh run list --workflow %s --branch %s" %
                      (rc.VIRGIN_WORKFLOW, TAG), joined)

    def test_with_dispatch_the_fake_run_is_recorded_with_its_conclusion(self):
        bin_dir = os.path.join(self.work, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        write_fake_gh(bin_dir, run_id=4242, conclusion="success")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bin_dir + os.pathsep + old_path
        try:
            grant = write_grant(os.path.join(self.work, "grant.json"),
                                tag=TAG)
            gate = self._gate()
            rc.gate_virgin_install(make_args(grant=grant, dispatch=True),
                                   self.ev, gate)
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual(gate.verdict, "PASS", gate.why)
        self.assertIn("4242", gate.why)
        self.assertIn(TAG, gate.why)
        joined = "\n".join(gate.lines)
        self.assertIn("run id: 4242", joined)
        self.assertIn("conclusion: success", joined)

    def test_with_dispatch_a_failed_conclusion_fails_the_gate(self):
        bin_dir = os.path.join(self.work, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        write_fake_gh(bin_dir, run_id=7, conclusion="failure")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bin_dir + os.pathsep + old_path
        try:
            grant = write_grant(os.path.join(self.work, "grant.json"),
                                tag=TAG)
            gate = self._gate()
            rc.gate_virgin_install(make_args(grant=grant, dispatch=True),
                                   self.ev, gate)
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual(gate.verdict, "FAIL")
        self.assertIn("failure", gate.why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
