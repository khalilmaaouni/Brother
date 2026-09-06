#!/usr/bin/env python3
"""Tests for scripts/keep_current.py: link classification from canned
strings, the stop-at-first-non-PASS rule, and the install gate.

No em or en dashes.
"""
import sys
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


class CloseoutTests(unittest.TestCase):
    def test_no_data_when_dir_absent(self):
        v, _m = kc.classify_closeout(False, [])
        self.assertEqual(v, "NO-DATA")

    def test_no_data_when_gate_missing(self):
        v, _m = kc.classify_closeout(True, ["X1", "X2", "X3", "X4", "X5", "X6"])
        self.assertEqual(v, "NO-DATA")

    def test_pass_when_all_gates_present(self):
        v, _m = kc.classify_closeout(True, list(kc.CLOSEOUT_GATES))
        self.assertEqual(v, "PASS")


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
