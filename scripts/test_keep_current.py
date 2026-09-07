#!/usr/bin/env python3
"""Tests for scripts/keep_current.py: link classification from canned
strings, the stop-at-first-non-PASS rule, and the install gate.

No em or en dashes.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keep_current as kc  # noqa: E402


class SignatureTests(unittest.TestCase):
    def test_pass_on_good_signature(self):
        v, _m = kc.classify_signature(0, "gpg: Good signature from ...")
        self.assertEqual(v, "PASS")

    def test_fail_on_bad_signature(self):
        v, _m = kc.classify_signature(1, "gpg: BAD signature from ...")
        self.assertEqual(v, "FAIL")

    def test_no_data_on_unsigned_tag(self):
        v, _m = kc.classify_signature(1, "error: no signature found")
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_command_missing(self):
        v, _m = kc.classify_signature(None, "command not found: git")
        self.assertEqual(v, "NO-DATA")


class ReproductionTests(unittest.TestCase):
    def test_pass_on_pass_line(self):
        v, _m = kc.classify_reproduction(0, "PASS: every allowlisted path reproduces byte for byte")
        self.assertEqual(v, "PASS")

    def test_fail_on_nonzero_no_pass_line(self):
        v, _m = kc.classify_reproduction(1, "FAIL: file.py digests differently")
        self.assertEqual(v, "FAIL")

    def test_no_data_on_no_data_line(self):
        v, _m = kc.classify_reproduction(2, "NO-DATA: manifest not found")
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_script_missing(self):
        v, _m = kc.classify_reproduction(None, "command not found")
        self.assertEqual(v, "NO-DATA")


class ManifestTests(unittest.TestCase):
    def test_no_data_when_manifest_absent(self):
        v, _m = kc.classify_manifest(False, "note text", "manifest text")
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_note_absent(self):
        v, _m = kc.classify_manifest(True, None, "manifest text")
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_note_states_no_digest(self):
        v, _m = kc.classify_manifest(True, "no digest here", "manifest text")
        self.assertEqual(v, "NO-DATA")

    def test_pass_when_digest_matches(self):
        text = "some manifest bytes"
        import hashlib
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        note = "Export manifest digest `%s` over 3 file(s)" % digest
        v, _m = kc.classify_manifest(True, note, text)
        self.assertEqual(v, "PASS")

    def test_fail_when_digest_mismatches(self):
        note = "Export manifest digest `%s` over 3 file(s)" % ("a" * 64)
        v, _m = kc.classify_manifest(True, note, "different bytes")
        self.assertEqual(v, "FAIL")


class ConformanceTests(unittest.TestCase):
    def test_no_data_when_summary_absent(self):
        v, _m = kc.classify_conformance(None, None, 1000)
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_stale(self):
        v, _m = kc.classify_conformance("provider=codex verdict=PASS", 100, 1000)
        self.assertEqual(v, "NO-DATA")

    def test_pass_when_fresh_and_pass(self):
        v, _m = kc.classify_conformance("provider=codex verdict=PASS", 2000, 1000)
        self.assertEqual(v, "PASS")

    def test_fail_when_verdict_fail(self):
        v, _m = kc.classify_conformance("provider=codex verdict=FAIL", 2000, 1000)
        self.assertEqual(v, "FAIL")


# Verbatim header and closing reason lines from the real
# ~/.claude/evidence/closeout-1.0.9/RUN.log, measured 2026-09-07: X7 FAIL,
# X1 and X6 NO-DATA, the rest PASS. This is the exact log shape link 5's
# old presence-only check read as an all-PASS.
V109_RUN_LOG = """closeout from 50b7de1804d66d54383e7817a971441cd41c1802 tag v1.0.9

== X1 virgin-codex   NO-DATA
   NO-DATA: virgin-unit-proof needs a local checkout of https://github.com/khalilmaaouni/Brother, which was not one

== X2 upgrade-codex   PASS
   PASS: upgrade v1.0.8 to v1.0.9 kept seeded state and moved both the version and the hashes

== X3 reinstall-idempotent   PASS
   PASS: the second add left the installed tree byte identical (ee312d821911f9e6)

== X4 uninstall-reinstall   PASS
   PASS: uninstall emptied the tree and the reinstall reproduced it byte for byte (ee312d821911f9e6)

== X5 negatives   PASS
   PASS: all four negatives were refused: missing marketplace, malformed plugin.json, unsupported hooks key, offline

== X7 public-artifact   FAIL
   FAIL: the published tag does NOT reproduce from the hub revision its note names (2d731ced4e00ab8aa3f8895626e6f13e762c08e4): reproduce_export.py exited 1

== X6 claude-side   NO-DATA
   NO-DATA: 1 leg(s) could not run: no --actions-run-id given, so the Linux Actions run is unreported here

== X8 founder   FOUNDER
   FOUNDER: needs a signed-in Codex session; runbook /private/tmp/bh-rev-main/docs/codex/SMOKE-RUNBOOK.md
"""

# Verbatim header and closing reason line from the real
# ~/.claude/evidence/closeout-1.0.9/RUN-X1.log, a later single-gate rerun of
# just X1 that reads PASS.
V109_RUN_X1_RERUN = """release_closeout: version 1.0.9

== X1 virgin-codex   PASS
   PASS: install, skills and one stubbed invocation with a receipt, in an isolated home, and one unit closed through the exported bundle alone (virgin-unit-proof)
X1 EXIT 0
"""


class GateVerdictParsingTests(unittest.TestCase):
    def test_parses_all_eight_headers_from_the_real_run_log(self):
        verdicts = kc.parse_gate_verdicts(V109_RUN_LOG)
        self.assertEqual(verdicts["X1"][0], "NO-DATA")
        self.assertEqual(verdicts["X2"][0], "PASS")
        self.assertEqual(verdicts["X7"][0], "FAIL")
        self.assertIn("does NOT reproduce", verdicts["X7"][1])
        self.assertEqual(verdicts["X8"][0], "FOUNDER")


class CloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="keep-current-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_classify_no_data_when_no_gates_seen(self):
        v, _m = kc.classify_closeout({})
        self.assertEqual(v, "NO-DATA")

    def test_classify_pass_when_all_seven_pass(self):
        gate_verdicts = dict()
        for g in kc.CLOSEOUT_GATES:
            gate_verdicts[g] = ("PASS", "ok")
        v, _m = kc.classify_closeout(gate_verdicts)
        self.assertEqual(v, "PASS")

    def test_classify_fail_when_any_gate_fails(self):
        gate_verdicts = dict()
        for g in kc.CLOSEOUT_GATES:
            gate_verdicts[g] = ("PASS", "ok")
        gate_verdicts["X7"] = ("FAIL", "does not reproduce")
        v, msg = kc.classify_closeout(gate_verdicts)
        self.assertEqual(v, "FAIL")
        self.assertIn("X7", msg)

    def test_no_data_when_directory_has_no_run_log(self):
        # A closeout directory can exist (gate subdirectories present) with
        # no RUN.log in it: this must never read as a pass.
        os.makedirs(os.path.join(self.tmpdir, "X1"))
        (v, _m), _dir = kc.link_closeout("1.0.9", evidence_dir=self.tmpdir)
        self.assertEqual(v, "NO-DATA")

    def test_fail_naming_x7_on_the_real_v109_run_log(self):
        self._write("RUN.log", V109_RUN_LOG)
        (v, msg), _dir = kc.link_closeout("1.0.9", evidence_dir=self.tmpdir)
        self.assertEqual(v, "FAIL")
        self.assertIn("X7", msg)

    def test_run_x1_rerun_flips_x1_only(self):
        self._write("RUN.log", V109_RUN_LOG)
        self._write("RUN-X1.log", V109_RUN_X1_RERUN)
        gate_verdicts = kc.read_closeout_verdicts(self.tmpdir)
        self.assertEqual(gate_verdicts["X1"][0], "PASS")
        # every other gate keeps the verdict RUN.log gave it
        self.assertEqual(gate_verdicts["X7"][0], "FAIL")
        self.assertEqual(gate_verdicts["X2"][0], "PASS")
        self.assertEqual(gate_verdicts["X6"][0], "NO-DATA")
        # the overall link still stops the chain at FAIL: X7 never flipped
        (v, msg), _dir = kc.link_closeout("1.0.9", evidence_dir=self.tmpdir)
        self.assertEqual(v, "FAIL")
        self.assertIn("X7", msg)


class CiTests(unittest.TestCase):
    def test_no_data_when_no_log(self):
        v, _m = kc.classify_ci(None)
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_no_run_id_mentioned(self):
        v, _m = kc.classify_ci("some ordinary log text")
        self.assertEqual(v, "NO-DATA")

    def test_pass_when_run_id_recorded(self):
        v, _m = kc.classify_ci("virgin-install run_id: 123456789")
        self.assertEqual(v, "PASS")


class SmokeTests(unittest.TestCase):
    def test_no_data_when_summary_absent(self):
        v, _m = kc.classify_smoke("v1.0.8", None)
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_tag_line_wrong(self):
        v, _m = kc.classify_smoke("v1.0.8", "tag=v1.0.7\nB6 PASS\nB8 PASS\nB9 PASS\n")
        self.assertEqual(v, "NO-DATA")

    def test_pass_when_all_three_legs_pass(self):
        v, _m = kc.classify_smoke("v1.0.8", "tag=v1.0.8\nB6 PASS something\nB8 PASS something\nB9 PASS something\n")
        self.assertEqual(v, "PASS")

    def test_fail_when_a_leg_fails(self):
        v, _m = kc.classify_smoke("v1.0.8", "tag=v1.0.8\nB6 FAIL receipt not durable\nB8 NO-DATA\nB9 PASS\n")
        self.assertEqual(v, "FAIL")

    def test_no_data_when_a_leg_is_no_data_and_none_fail(self):
        v, _m = kc.classify_smoke("v1.0.8", "tag=v1.0.8\nB6 PASS\nB8 NO-DATA\nB9 PASS\n")
        self.assertEqual(v, "NO-DATA")


class OrchestrationTests(unittest.TestCase):
    def test_stops_at_first_non_pass(self):
        calls = []

        def fake_link_signature(clone_dir, tag):
            calls.append("signature")
            return "NO-DATA", "unsigned"

        orig = kc.link_signature
        kc.link_signature = fake_link_signature
        try:
            results = kc.run_links("v1.0.8", "url", "/tmp/does-not-matter", "/tmp/clone")
        finally:
            kc.link_signature = orig

        self.assertEqual([r[0] for r in results], ["signature"])
        self.assertEqual(results[0][1], "NO-DATA")

    def test_runs_all_seven_when_every_link_passes(self):
        patched = {
            "link_signature": lambda clone_dir, tag: ("PASS", "ok"),
            "link_reproduction": lambda clone_dir, tag: ("PASS", "ok"),
            "link_manifest": lambda clone_dir, version: ("PASS", "ok"),
            "link_conformance": lambda tag_commit_epoch: ("PASS", "ok"),
            "link_closeout": lambda version, evidence_dir=None: (("PASS", "ok"), "/tmp/closeout"),
            "link_ci": lambda closeout_dir: ("PASS", "ok"),
            "link_smoke": lambda tag: ("PASS", "ok"),
        }
        originals = {name: getattr(kc, name) for name in patched}
        for name, fn in patched.items():
            setattr(kc, name, fn)
        try:
            results = kc.run_links("v1.0.8", "url", "/tmp/evidence", "/tmp/clone")
        finally:
            for name, fn in originals.items():
                setattr(kc, name, fn)

        self.assertEqual(len(results), 7)
        self.assertTrue(all(v == "PASS" for _n, v, _m in results))


class InstallGateTests(unittest.TestCase):
    def test_never_installs_when_a_link_is_not_pass(self):
        installed = {"called": False}

        def fake_install(args):
            installed["called"] = True
            return 0

        def fake_run_links(*a, **k):
            return [("signature", "FAIL", "bad signature")]

        orig_run_links = kc.run_links
        orig_prepare = kc.prepare_clone
        kc.run_links = fake_run_links
        kc.prepare_clone = lambda evidence_dir, tag, url: ("/tmp/clone", "")
        try:
            rc = kc.main(["--tag", "v1.0.8", "--install"], install_fn=fake_install)
        finally:
            kc.run_links = orig_run_links
            kc.prepare_clone = orig_prepare

        self.assertEqual(rc, 0)
        self.assertFalse(installed["called"], "install must never run when a link is not PASS")

    def test_installs_when_all_seven_pass(self):
        installed = {"called": False}

        def fake_install(args):
            installed["called"] = True
            return 0

        def fake_run_links(*a, **k):
            return [(name, "PASS", "ok") for name in kc.LINK_NAMES]

        orig_run_links = kc.run_links
        orig_prepare = kc.prepare_clone
        kc.run_links = fake_run_links
        kc.prepare_clone = lambda evidence_dir, tag, url: ("/tmp/clone", "")
        try:
            kc.main(["--tag", "v1.0.8", "--install"], install_fn=fake_install)
        finally:
            kc.run_links = orig_run_links
            kc.prepare_clone = orig_prepare

        self.assertTrue(installed["called"])

    def test_never_installs_without_the_flag_even_if_all_pass(self):
        installed = {"called": False}

        def fake_install(args):
            installed["called"] = True
            return 0

        def fake_run_links(*a, **k):
            return [(name, "PASS", "ok") for name in kc.LINK_NAMES]

        orig_run_links = kc.run_links
        orig_prepare = kc.prepare_clone
        kc.run_links = fake_run_links
        kc.prepare_clone = lambda evidence_dir, tag, url: ("/tmp/clone", "")
        try:
            kc.main(["--tag", "v1.0.8"], install_fn=fake_install)
        finally:
            kc.run_links = orig_run_links
            kc.prepare_clone = orig_prepare

        self.assertFalse(installed["called"])


if __name__ == "__main__":
    unittest.main()
