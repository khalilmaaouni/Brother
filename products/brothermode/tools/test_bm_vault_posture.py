#!/usr/bin/env python3
"""Calibration for tools/bm_vault_posture.py, WBS row VB8-01: the encryption posture
census.

Driven backwards, per the module's own honesty rule (never claim encrypted from
anything but the OS's own answer): a fake diskutil answer is injected through the
module's own test seams (apfs_list_fn, device_fn), and the verdict is asserted to
follow the injected answer in BOTH directions on the SAME real temp directory --
proof the code reads the injected OS answer, not this machine's real disk state
(which, on a FileVault-enabled Mac, would otherwise always read "encrypted" and
mask a bug that ignores the injected value).

No em or en dashes anywhere in this file.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_posture as posture  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def _fake_device_fn(device_id):
    def fn(path):
        return device_id, None
    return fn


def _fake_apfs_list_fn(text):
    def fn(diskutil_bin):
        return text, None
    return fn


ENCRYPTED_FIXTURE = """\
Container disk3
+-> Volume disk9s9 AAAA-BBBB
|   ---------------------------------------------------
|   APFS Volume Disk (Role):   disk9s9 (Data)
|   Name:                      Fixture Data
|   Mount Point:               /System/Volumes/Data
|   FileVault:                 Yes (Unlocked)
"""

PLAINTEXT_FIXTURE = """\
Container disk3
+-> Volume disk9s9 AAAA-BBBB
|   ---------------------------------------------------
|   APFS Volume Disk (Role):   disk9s9 (Data)
|   Name:                      Fixture Data
|   Mount Point:               /System/Volumes/Data
|   FileVault:                 No
"""

# Regression fixture for the bug this module shipped with once and caught before
# release: a "Snapshot Mount Point:" line contains "Mount Point:" as a substring,
# and a naive `.search()` for that label matched it, silently overwriting the
# volume's real mount point. This fixture carries both lines in the shape
# diskutil actually prints them; the assertion is on the parsed mount_point, not
# on any encryption verdict.
SNAPSHOT_LINE_FIXTURE = """\
Container disk3
+-> Volume disk9s9 AAAA-BBBB
|   ---------------------------------------------------
|   APFS Volume Disk (Role):   disk9s9 (System)
|   Name:                      Fixture System
|   Mount Point:               /System/Volumes/Update/mnt1
|   FileVault:                 Yes (Unlocked)
|   |
|   Snapshot Mount Point:      /
"""

UNRECOGNIZED_FIXTURE = """\
Container disk3
+-> Volume disk9s9 AAAA-BBBB
|   Mount Point:               /System/Volumes/Data
|   FileVault:                 Maybe
"""


class TheParser(unittest.TestCase):
    def test_parses_device_mount_point_and_filevault(self):
        volumes = posture._parse_apfs_volumes(ENCRYPTED_FIXTURE)
        self.assertIn("disk9s9", volumes)
        self.assertEqual(volumes["disk9s9"]["mount_point"], "/System/Volumes/Data")
        self.assertEqual(volumes["disk9s9"]["filevault"], "Yes (Unlocked)")

    def test_snapshot_mount_point_never_overwrites_the_real_one(self):
        volumes = posture._parse_apfs_volumes(SNAPSHOT_LINE_FIXTURE)
        self.assertEqual(volumes["disk9s9"]["mount_point"], "/System/Volumes/Update/mnt1")


class TheVerdictFollowsTheInjectedAnswer(unittest.TestCase):
    """The core driven-backwards proof: same real temp directory, two different
    injected diskutil answers, two different verdicts -- never the real machine's
    own FileVault state leaking through."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-posture-")

    def tearDown(self):
        os.rmdir(self.tmp)

    def test_injected_encrypted_answer_reports_encrypted(self):
        verdict, detail = posture.storage_state(
            self.tmp, apfs_list_fn=_fake_apfs_list_fn(ENCRYPTED_FIXTURE),
            device_fn=_fake_device_fn("disk9s9"))
        self.assertEqual(verdict, "encrypted")
        self.assertIn("disk9s9", detail)

    def test_a_fixture_on_a_plaintext_temp_dir_reports_plaintext(self):
        verdict, detail = posture.storage_state(
            self.tmp, apfs_list_fn=_fake_apfs_list_fn(PLAINTEXT_FIXTURE),
            device_fn=_fake_device_fn("disk9s9"))
        self.assertEqual(verdict, "plaintext")
        self.assertIn("disk9s9", detail)

    def test_unrecognized_filevault_value_is_no_data_never_a_guess(self):
        verdict, _detail = posture.storage_state(
            self.tmp, apfs_list_fn=_fake_apfs_list_fn(UNRECOGNIZED_FIXTURE),
            device_fn=_fake_device_fn("disk9s9"))
        self.assertEqual(verdict, "NO-DATA")

    def test_device_absent_from_apfs_list_is_no_data(self):
        verdict, _detail = posture.storage_state(
            self.tmp, apfs_list_fn=_fake_apfs_list_fn(ENCRYPTED_FIXTURE),
            device_fn=_fake_device_fn("disk0s1"))
        self.assertEqual(verdict, "NO-DATA")

    def test_device_fn_failure_is_no_data(self):
        verdict, detail = posture.storage_state(
            self.tmp, apfs_list_fn=_fake_apfs_list_fn(ENCRYPTED_FIXTURE),
            device_fn=lambda path: (None, "df -P failed to run: boom"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("boom", detail)

    def test_apfs_list_failure_is_no_data(self):
        verdict, detail = posture.storage_state(
            self.tmp, device_fn=_fake_device_fn("disk9s9"),
            apfs_list_fn=lambda b: (None, "diskutil apfs list exited 1: boom"))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("boom", detail)


class ThePlatformAndToolGuards(unittest.TestCase):
    def test_unsupported_platform_is_no_data_never_a_guess(self):
        orig = posture.sys.platform
        posture.sys.platform = "linux"
        try:
            verdict, detail = posture.storage_state("/tmp")
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("platform", detail)
        finally:
            posture.sys.platform = orig

    def test_missing_diskutil_is_no_data(self):
        orig = posture.shutil.which
        posture.shutil.which = lambda name: None
        try:
            verdict, detail = posture.storage_state("/tmp")
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("diskutil", detail)
        finally:
            posture.shutil.which = orig


class TheDerivedStoreList(unittest.TestCase):
    """Every store's path is read off the module that owns it, never re-typed here,
    so this test can only pass if the census cannot drift from the real resolution."""

    def test_every_labeled_store_matches_its_owning_module(self):
        stores = dict(posture._derived_stores())
        bm_vault = posture._load("bm_vault.py")
        bm_vault_audit = posture._load("bm_vault_audit.py")
        bm_vault_ledger = posture._load("bm_vault_ledger.py")
        vault_recall_hook = posture._load("vault_recall_hook.py")
        self.assertEqual(stores["sqlite retrieval index"], bm_vault.INDEX_PATH)
        self.assertEqual(stores["audit file"], bm_vault_audit.AUDIT_PATH)
        self.assertEqual(stores["answer ledger"], bm_vault.LEDGER_PATH)
        self.assertEqual(stores["outcomes"], bm_vault_ledger.OUTCOME_PATH)
        self.assertEqual(stores["query cache (recall hook SEEN cache)"],
                          vault_recall_hook.SEEN)
        self.assertIsNone(stores["serve logs"])


class TheRealVaultReadOnlyProof(unittest.TestCase):
    """No fixture, no test seam: the real diskutil and df on this machine, over a
    real path. Read-only (storage_state never writes anything); this is the proof
    the module works end to end, not just against a canned fixture."""

    @unittest.skipUnless(sys.platform == "darwin", "macOS-only tool")
    def test_report_runs_against_a_real_path_and_returns_a_verdict(self):
        verdict, detail = posture.storage_state(os.path.expanduser("~"))
        self.assertIn(verdict, ("encrypted", "plaintext", "NO-DATA"))
        self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
