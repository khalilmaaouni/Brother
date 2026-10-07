"""Tests for mobile_state_reset.py (EPIC M2.02).

Like test_mobile_workflow.py, these drive a fake xcrun/simctl on PATH: they
prove dispatch, honesty (PASS/FAIL/NO-DATA routing) and partial-failure
isolation, not Xcode/simctl compatibility. Real-tool verification against a
booted iOS Simulator was run by hand separately (see the PR description);
this suite is the always-available regression gate. Every fixture here is a
synthetic, throwaway value: no real account, project, or app name is read
or referenced anywhere in this file.
"""
import contextlib
import http.server
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_state_reset as R  # noqa: E402
import mobile_state_fixture as F  # noqa: E402
import contract_check as CC  # noqa: E402
from mobile_workflow import Refusal  # noqa: E402

DEVICE = "11111111-2222-3333-4444-555555555555"
BUNDLE = "com.example.fixture-app"

# A fake xcrun that tracks "installed" state on disk under FIXTURE_ROOT, so
# tests can exercise real installed/not-installed transitions the same way
# the real simctl get_app_container/uninstall/install round trip behaves.
TOOL = r'''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
marker = root / "installed_marker"
container = root / "container"

if args[:2] == ["simctl", "get_app_container"]:
    if marker.exists():
        print(str(container))
        sys.exit(0)
    sys.exit(2)
if args[:2] == ["simctl", "uninstall"]:
    if marker.exists():
        marker.unlink()
    if container.exists():
        shutil.rmtree(container)
    sys.exit(0)
if args[:2] == ["simctl", "install"]:
    marker.write_text("yes")
    container.mkdir(exist_ok=True)
    (container / "seed.txt").write_text("seed")
    sys.exit(0)
if args[:2] == ["simctl", "keychain"]:
    (root / "keychain-reset-called").write_text("yes")
    sys.exit(0)
if args[:2] == ["simctl", "launch"]:
    (root / "launch-env").write_text(os.environ.get("SIMCTL_CHILD_NEW_ONBOARDING", ""))
    sys.exit(0)
print("unexpected fixture command", args)
sys.exit(99)
'''


def _valid_fixture(**overrides):
    fixture = {
        "schema_version": "mobile-state-fixture-v1",
        "fixture_id": "test-journey-fresh-install",
        "account_data_ref": "fixtures/accounts/synthetic-001.json",
        "backend_dataset": {"id": "synthetic-dataset", "version": "1"},
        "app_storage": {"reset_mode": "clean"},
        "keychain_policy": {"mode": "clean"},
        "feature_flags": {"new_onboarding": True},
        "clock": {"mode": "real"},
        "randomness_seed": None,
        "locale": "en-US",
        "timezone": "Asia/Tokyo",
        "network_profile": {"mode": "online"},
        "permissions": {"location": "not-determined"},
        "location": {"mode": "disabled"},
        "notifications": {"mode": "disabled"},
        "external_services": [],
        "cleanup_policy": {"required": False},
    }
    fixture.update(overrides)
    return fixture


def _ctx(**overrides):
    base = dict(platform="ios", device=DEVICE, bundle_id=BUNDLE, checkpoints_dir=None,
                app_path=None, fixture_api_url=None, db_fixture_script=None, backend_reset_url=None)
    base.update(overrides)
    import argparse
    return argparse.Namespace(**base)


class MobileStateResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-state-reset-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Fake tools must not contend with a real machine-wide lease: point
        # the lease lockfile at this test's own temp dir (same technique as
        # test_mobile_workflow.py's own setUp).
        self.lease_patch = patch("mobile_workflow.tempfile.gettempdir", return_value=str(self.root))
        self.lease_patch.start(); self.addCleanup(self.lease_patch.stop)
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.out = self.root / "evidence"
        self.bin = self.root / "bin"; self.bin.mkdir()
        (self.bin / "xcrun").write_text(TOOL); (self.bin / "xcrun").chmod(0o755)
        self.environment = patch.dict(os.environ, {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "FIXTURE_ROOT": str(self.root),
        })
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.stages = []

    # -- _app_container ---------------------------------------------------

    def test_app_container_not_installed_returns_none(self):
        self.assertIsNone(R._app_container(DEVICE, BUNDLE, self.repo, self.out_dir(), self.stages))
        self.assertEqual(self.stages[-1]["exit_code"], 2)

    def test_app_container_installed_returns_path(self):
        self.install_app_on_disk()
        path = R._app_container(DEVICE, BUNDLE, self.repo, self.out_dir(), self.stages)
        self.assertEqual(path, str(self.root / "container"))

    # -- uninstall_app / install_app ---------------------------------------

    def test_uninstall_not_installed_is_a_real_noop_pass(self):
        r = R.uninstall_app(_ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("already absent", r["detail"])

    def test_uninstall_installed_runs_real_command(self):
        self.install_app_on_disk()
        r = R.uninstall_app(_ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertFalse((self.root / "installed_marker").exists())

    def test_uninstall_non_ios_platform_is_no_data(self):
        r = R.uninstall_app(_ctx(platform="android"), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_uninstall_without_bundle_id_is_no_data(self):
        r = R.uninstall_app(_ctx(bundle_id=None), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_install_without_app_path_is_no_data(self):
        r = R.install_app(_ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_install_with_missing_app_dir_fails(self):
        r = R.install_app(_ctx(app_path=str(self.root / "nope.app")), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    def test_install_real_command_marks_installed(self):
        app = self.root / "built.app"; app.mkdir()
        r = R.install_app(_ctx(app_path=str(app)), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertTrue((self.root / "installed_marker").exists())

    # -- reset_app_storage ---------------------------------------------------

    def test_app_storage_clean_non_ios_is_no_data(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "clean"})
        r = R.reset_app_storage(fixture, _ctx(platform="android"), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_app_storage_clean_no_bundle_id_is_no_data(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "clean"})
        r = R.reset_app_storage(fixture, _ctx(bundle_id=None), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_app_storage_clean_not_installed_is_pass_already_clean(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "clean"})
        r = R.reset_app_storage(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("already absent", r["detail"])

    def test_app_storage_clean_installed_uninstalls_and_flags_reinstall(self):
        self.install_app_on_disk()
        fixture = _valid_fixture(app_storage={"reset_mode": "clean"})
        r = R.reset_app_storage(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertTrue(r["reinstall_required"])
        self.assertFalse((self.root / "installed_marker").exists())

    def test_app_storage_preserve_named_keys_is_always_no_data(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "preserve-named-keys", "preserved_keys": ["a", "b"]})
        r = R.reset_app_storage(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")
        self.assertIn("2 preserved_keys", r["detail"])

    def test_app_storage_unknown_mode_fails(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "clean"})
        fixture["app_storage"]["reset_mode"] = "not-a-real-mode"
        r = R.reset_app_storage(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    def test_app_storage_restore_checkpoint_whitespace_ref_fails(self):
        # MAJOR 2 (2026-09-15 review): a plain falsiness test let a
        # whitespace-only checkpoint_ref sail past this guard, the exact
        # bug class PR #709's fix removed at the schema layer.
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "   "})
        r = R.reset_app_storage(fixture, _ctx(checkpoints_dir=str(self.root / "cps")), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("no checkpoint_ref", r["detail"])

    def test_app_storage_restore_checkpoint_missing_dir_is_no_data(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "cp1"})
        r = R.reset_app_storage(fixture, _ctx(checkpoints_dir=None), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_app_storage_restore_checkpoint_missing_file_fails(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "cp1"})
        cpdir = self.root / "checkpoints"; cpdir.mkdir()
        r = R.reset_app_storage(fixture, _ctx(checkpoints_dir=str(cpdir)), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    def test_app_storage_restore_checkpoint_not_installed_is_no_data(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "cp1"})
        cpdir = self.root / "checkpoints"; cpdir.mkdir()
        (cpdir / "cp1.tar").write_bytes(b"")
        r = R.reset_app_storage(fixture, _ctx(checkpoints_dir=str(cpdir)), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_app_storage_restore_checkpoint_real_round_trip(self):
        self.install_app_on_disk()
        container = self.root / "container"
        cpdir = self.root / "checkpoints"; cpdir.mkdir()
        # Capture the seeded container, then dirty it, then restore.
        R.capture_checkpoint(DEVICE, BUNDLE, "cp1", str(cpdir), self.repo, self.out_dir(), self.stages)
        (container / "seed.txt").unlink()
        (container / "dirty.txt").write_text("should be removed by restore")
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "cp1"})
        r = R.reset_app_storage(fixture, _ctx(checkpoints_dir=str(cpdir)), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertTrue((container / "seed.txt").is_file())
        self.assertFalse((container / "dirty.txt").exists())

    # A checkpoint tar is a trust boundary. Python below 3.12 has no filter="data", so the restore must refuse
    # what that filter refuses, on every interpreter, BEFORE the container is cleared. One member per guard.
    def _restore_refuses(self, member, payload=b"x"):
        self.install_app_on_disk()
        container = self.root / "container"
        cpdir = self.root / "checkpoints"; cpdir.mkdir()
        with tarfile.open(cpdir / "cp1.tar", "w") as tf:
            good = tarfile.TarInfo("seed.txt"); good.size = 2
            tf.addfile(good, io.BytesIO(b"ok"))
            if member.isfile():
                member.size = len(payload)
                tf.addfile(member, io.BytesIO(payload))
            else:
                tf.addfile(member)
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "cp1"})
        r = R.reset_app_storage(fixture, _ctx(checkpoints_dir=str(cpdir)), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL", r)
        self.assertIn("unsafe member", r["detail"])
        self.assertEqual((container / "seed.txt").read_text(), "seed")   # refused before the container was cleared
        return r

    def test_restore_refuses_a_traversal_member(self):
        self._restore_refuses(tarfile.TarInfo("../escaped.txt"))
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_restore_refuses_an_absolute_member(self):
        self._restore_refuses(tarfile.TarInfo(str(self.root / "absolute.txt")))
        self.assertFalse((self.root / "absolute.txt").exists())

    def test_restore_refuses_a_symlink_out_of_the_container(self):
        link = tarfile.TarInfo("out"); link.type = tarfile.SYMTYPE; link.linkname = "../../outside"
        self._restore_refuses(link)

    def test_restore_refuses_a_hardlink_out_of_the_container(self):
        link = tarfile.TarInfo("hard"); link.type = tarfile.LNKTYPE; link.linkname = "../outside"
        self._restore_refuses(link)

    def test_restore_refuses_a_device_member(self):
        dev = tarfile.TarInfo("dev"); dev.type = tarfile.CHRTYPE
        self._restore_refuses(dev)

    # -- reset_keychain -------------------------------------------------------

    def test_keychain_preserve_runs_no_command_and_is_a_noop(self):
        # CRITICAL 1 (2026-09-15 review): preserve genuinely runs nothing,
        # so it must not report PASS (which apply_fixture's aggregation
        # would otherwise read as "a real action happened").
        fixture = _valid_fixture(keychain_policy={"mode": "preserve"})
        r = R.reset_keychain(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-OP")
        self.assertFalse((self.root / "keychain-reset-called").exists())

    def test_keychain_seeded_is_no_data(self):
        fixture = _valid_fixture(keychain_policy={"mode": "seeded", "seeded_items": ["a"]})
        r = R.reset_keychain(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")
        self.assertIn("1 seeded_items", r["detail"])

    def test_keychain_clean_runs_real_device_wide_reset(self):
        fixture = _valid_fixture(keychain_policy={"mode": "clean"})
        r = R.reset_keychain(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["scope"], "device-wide")
        self.assertTrue((self.root / "keychain-reset-called").exists())

    def test_keychain_clean_no_device_is_no_data(self):
        # Minor 4 (2026-09-15 review): every sibling mechanism checks
        # ctx.device before use; keychain clean did not.
        fixture = _valid_fixture(keychain_policy={"mode": "clean"})
        r = R.reset_keychain(fixture, _ctx(device=None), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_keychain_clean_non_ios_is_no_data(self):
        fixture = _valid_fixture(keychain_policy={"mode": "clean"})
        r = R.reset_keychain(fixture, _ctx(platform="android"), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_keychain_unknown_mode_fails(self):
        fixture = _valid_fixture(keychain_policy={"mode": "clean"})
        fixture["keychain_policy"]["mode"] = "not-a-real-mode"
        r = R.reset_keychain(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    # -- apply_feature_flags ----------------------------------------------

    def test_feature_flags_empty_is_noop_nothing_to_apply(self):
        # CRITICAL 1 (2026-09-15 review): an empty flag map runs no live
        # command, so it must not report PASS on its own.
        fixture = _valid_fixture(feature_flags={})
        r = R.apply_feature_flags(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-OP")
        self.assertEqual(r["env"], {})

    def test_feature_flags_non_ios_is_no_data_but_computes_env(self):
        fixture = _valid_fixture(feature_flags={"x": True})
        r = R.apply_feature_flags(fixture, _ctx(platform="android"), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")
        self.assertEqual(r["env"], {"SIMCTL_CHILD_X": "1"})

    def test_feature_flags_missing_device_is_no_data(self):
        fixture = _valid_fixture(feature_flags={"x": True})
        r = R.apply_feature_flags(fixture, _ctx(device=None), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_feature_flags_not_installed_is_no_data(self):
        fixture = _valid_fixture(feature_flags={"new_onboarding": True})
        r = R.apply_feature_flags(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_feature_flags_live_apply_launches_with_real_env(self):
        self.install_app_on_disk()
        fixture = _valid_fixture(feature_flags={"new_onboarding": True})
        r = R.apply_feature_flags(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual((self.root / "launch-env").read_text(), "1")

    def test_feature_flags_illegal_env_var_name_refuses(self):
        # MAJOR 3 / minor m1 (2026-09-15 review, PR #725): a flag name
        # containing "=" passes M2.01 (feature_flags keys carry no pattern
        # constraint there) and must be refused at this validation layer,
        # not left to crash `subprocess` later with "ValueError: illegal
        # environment variable name". Reused by mobile_deterministic_hooks
        # via the shared feature_flags_env() this mechanism now calls.
        # apply_feature_flags itself does not catch this (same convention
        # as combined_env's own collision check): the real CLI path routes
        # every mechanism through _run_mechanism, which turns any Refusal
        # into a clean FAIL result in the receipt (proven for the
        # collision case by test_apply_...; this is the shape-validation
        # sibling of that same guard).
        fixture = _valid_fixture(feature_flags={"mobile_journey_seed=999": True})
        with self.assertRaises(Refusal):
            R.apply_feature_flags(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        r = R._run_mechanism("feature_flags", R.apply_feature_flags, fixture, _ctx(),
                              self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    # -- fixture_api_hook / backend_namespace_reset (real local HTTP) -----

    def test_fixture_api_hook_no_url_is_no_data(self):
        fixture = _valid_fixture()
        r = R.fixture_api_hook(fixture, _ctx())
        self.assertEqual(r["status"], "NO-DATA")

    def test_fixture_api_hook_real_success(self):
        with self.http_server(200) as url:
            r = R.fixture_api_hook(_valid_fixture(), _ctx(fixture_api_url=url))
        self.assertEqual(r["status"], "PASS")

    def test_fixture_api_hook_real_error_status_is_fail_not_no_data(self):
        # Regression: urllib raises HTTPError (a URLError subclass) for a
        # real bad status; it must be reported as FAIL, never NO-DATA.
        with self.http_server(500) as url:
            r = R.fixture_api_hook(_valid_fixture(), _ctx(fixture_api_url=url))
        self.assertEqual(r["status"], "FAIL")

    def test_fixture_api_hook_unreachable_is_no_data(self):
        r = R.fixture_api_hook(_valid_fixture(), _ctx(fixture_api_url="http://127.0.0.1:1/"))
        self.assertEqual(r["status"], "NO-DATA")

    def test_backend_namespace_reset_real_success(self):
        with self.http_server(200) as url:
            r = R.backend_namespace_reset(_valid_fixture(), _ctx(backend_reset_url=url))
        self.assertEqual(r["status"], "PASS")

    def test_backend_namespace_reset_real_error_status_is_fail(self):
        with self.http_server(503) as url:
            r = R.backend_namespace_reset(_valid_fixture(), _ctx(backend_reset_url=url))
        self.assertEqual(r["status"], "FAIL")

    def test_backend_namespace_reset_no_url_names_dataset(self):
        r = R.backend_namespace_reset(_valid_fixture(), _ctx())
        self.assertIn("synthetic-dataset@1", r["detail"])

    # -- local_database_fixture --------------------------------------------

    def test_local_database_no_script_is_no_data(self):
        r = R.local_database_fixture(_valid_fixture(), _ctx())
        self.assertEqual(r["status"], "NO-DATA")

    def test_local_database_missing_script_fails(self):
        r = R.local_database_fixture(_valid_fixture(), _ctx(db_fixture_script=str(self.root / "nope.sh")))
        self.assertEqual(r["status"], "FAIL")

    def test_local_database_real_script_success(self):
        script = self.root / "apply.sh"
        script.write_text("#!/bin/sh\nexit 0\n"); script.chmod(0o755)
        r = R.local_database_fixture(_valid_fixture(), _ctx(db_fixture_script=str(script)))
        self.assertEqual(r["status"], "PASS")

    def test_local_database_real_script_failure(self):
        script = self.root / "apply.sh"
        script.write_text("#!/bin/sh\necho boom 1>&2\nexit 3\n"); script.chmod(0o755)
        r = R.local_database_fixture(_valid_fixture(), _ctx(db_fixture_script=str(script)))
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("boom", r["detail"])

    # -- apply_fixture: aggregation and partial-failure isolation -----------

    def test_apply_refuses_when_output_already_exists(self):
        self.out.mkdir()
        with self.assertRaises(Refusal):
            R.apply_fixture(self.write_fixture(_valid_fixture()), F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)

    def test_apply_refuses_invalid_fixture_and_writes_nothing(self):
        bad = _valid_fixture(); bad["schema_version"] = "wrong"
        with self.assertRaises(Refusal):
            R.apply_fixture(self.write_fixture(bad), F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)
        self.assertFalse(self.out.exists())

    def test_apply_wrong_typed_fixture_field_fails_cleanly_not_crash(self):
        # CRITICAL 2 (2026-09-15 review): a type-confused fixture
        # (app_storage given as an int) used to reach hand_rules's
        # unguarded secret-scan block and raise AttributeError, escaping
        # main()'s handler as a raw traceback instead of the module's own
        # promised "mobile_state_reset: FAIL: ..." verdict line. Exercised
        # through the real CLI entry point end to end.
        bad = _valid_fixture(); bad["app_storage"] = 5
        fixture_path = self.write_fixture(bad)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = R.main(["apply", "--fixture", fixture_path, "--repo", str(self.repo),
                            "--out", str(self.out), "--device", DEVICE, "--bundle-id", BUNDLE])
        self.assertEqual(code, 1)
        self.assertIn("mobile_state_reset: FAIL:", buf.getvalue())
        self.assertFalse(self.out.exists())

    def test_apply_fixture_check_crash_is_wrapped_as_clean_fail(self):
        # CRITICAL 2 part b, defense in depth: apply_fixture must not
        # trust F.check to never raise beyond what validate() itself
        # guards against. Force a raise to prove the wider guard around
        # the F.check call turns it into a clean Refusal(FAIL), not a
        # bare exception escaping apply_fixture.
        with patch.object(F, "check", side_effect=TypeError("simulated future regression")):
            with self.assertRaises(Refusal) as raised:
                R.apply_fixture(self.write_fixture(_valid_fixture()), F.DEFAULT_SCHEMA,
                                 _ctx(), self.repo, self.out)
        self.assertEqual(getattr(raised.exception, "status", "FAIL"), "FAIL")
        self.assertIn("simulated future regression", str(raised.exception))
        self.assertFalse(self.out.exists())

    def test_apply_overall_pass_when_at_least_one_real_mechanism_passes(self):
        report, receipt = R.apply_fixture(
            self.write_fixture(_valid_fixture(keychain_policy={"mode": "preserve"})),
            F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(receipt.is_file())

    def test_apply_overall_no_data_when_nothing_can_be_exercised(self):
        fixture = _valid_fixture(app_storage={"reset_mode": "preserve-named-keys", "preserved_keys": ["a"]},
                                  keychain_policy={"mode": "seeded", "seeded_items": ["a"]},
                                  feature_flags={"new_onboarding": True})
        report, _ = R.apply_fixture(self.write_fixture(fixture), F.DEFAULT_SCHEMA,
                                     _ctx(platform="android"), self.repo, self.out)
        self.assertEqual(report["status"], "NO-DATA")

    def test_apply_overall_no_data_never_pass_from_android_preserve_and_empty_flags(self):
        # CRITICAL 1 (2026-09-15 review), the exact fabricated-PASS
        # reproduction: android has no adapter on this machine, keychain
        # preserve runs no command, and an empty feature_flags map applies
        # nothing. Before the fix, all three mechanisms' own PASS/NO-DATA
        # statuses aggregated to overall PASS even though nothing real
        # happened to any device anywhere.
        fixture = _valid_fixture(app_storage={"reset_mode": "clean"},
                                  keychain_policy={"mode": "preserve"},
                                  feature_flags={})
        report, _ = R.apply_fixture(self.write_fixture(fixture), F.DEFAULT_SCHEMA,
                                     _ctx(platform="android"), self.repo, self.out)
        self.assertEqual(report["status"], "NO-DATA")
        self.assertEqual(report["mechanisms"]["keychain"]["status"], "NO-OP")
        self.assertEqual(report["mechanisms"]["feature_flags"]["status"], "NO-OP")

    def test_apply_overall_fail_when_a_mechanism_fails(self):
        # A malformed reset_mode never reaches mechanism dispatch: M2.01's
        # own schema validation refuses the whole fixture first (proven by
        # test_apply_refuses_invalid_fixture_and_writes_nothing). A FAIL at
        # the mechanism level has to come from a schema-valid fixture that
        # fails for a real, runtime reason instead: a restore-checkpoint
        # whose checkpoint file genuinely does not exist on disk.
        fixture = _valid_fixture(app_storage={"reset_mode": "restore-checkpoint", "checkpoint_ref": "missing-cp"},
                                  keychain_policy={"mode": "preserve"})
        report, _ = R.apply_fixture(
            self.write_fixture(fixture), F.DEFAULT_SCHEMA,
            _ctx(checkpoints_dir=str(self.root / "cps-empty")), self.repo, self.out)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["mechanisms"]["app_storage"]["status"], "FAIL")
        self.assertEqual(report["mechanisms"]["app_storage"]["status"], "FAIL")

    def test_apply_isolates_a_crashing_mechanism_and_still_writes_full_receipt(self):
        # The exact failure mode named in review: one mechanism's real
        # command breaking must never hide or skip the rest, and must never
        # leave the evidence receipt unwritten.
        with patch.object(R, "reset_app_storage", side_effect=RuntimeError("simulated real crash")):
            report, receipt = R.apply_fixture(
                self.write_fixture(_valid_fixture(keychain_policy={"mode": "preserve"})),
                F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("simulated real crash", report["mechanisms"]["app_storage"]["detail"])
        self.assertEqual(report["mechanisms"]["keychain"]["status"], "NO-OP")
        self.assertTrue(receipt.is_file())

    def test_apply_distinguishes_device_error_from_programming_bug(self):
        # MAJOR 3 (2026-09-15 review): a bare `except Exception` wrote any
        # exception into the receipt identically, so a real Python bug
        # (TypeError) read exactly like a real device failure to an
        # operator. error_class now tells them apart without changing the
        # partial-failure-isolation guarantee: both still FAIL, both still
        # let every other mechanism run and the receipt still gets written.
        with patch.object(R, "reset_app_storage", side_effect=TypeError("None has no attribute foo")):
            report, receipt = R.apply_fixture(
                self.write_fixture(_valid_fixture(keychain_policy={"mode": "preserve"})),
                F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)
        self.assertEqual(report["mechanisms"]["app_storage"]["status"], "FAIL")
        self.assertEqual(report["mechanisms"]["app_storage"]["error_class"], "bug")
        self.assertTrue(receipt.is_file())

        shutil.rmtree(self.out)
        with patch.object(R, "reset_app_storage", side_effect=OSError("disk full")):
            report, _ = R.apply_fixture(
                self.write_fixture(_valid_fixture(keychain_policy={"mode": "preserve"})),
                F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)
        self.assertEqual(report["mechanisms"]["app_storage"]["status"], "FAIL")
        self.assertEqual(report["mechanisms"]["app_storage"]["error_class"], "device")

    def test_apply_all_mechanisms_present_in_every_receipt(self):
        report, _ = R.apply_fixture(
            self.write_fixture(_valid_fixture(keychain_policy={"mode": "preserve"})),
            F.DEFAULT_SCHEMA, _ctx(), self.repo, self.out)
        self.assertEqual(set(report["mechanisms"]), {
            "app_storage", "keychain", "feature_flags", "fixture_api", "local_database", "backend_namespace"})

    # -- capture_checkpoint / run_* wrappers --------------------------------

    def test_capture_checkpoint_not_installed_is_no_data(self):
        with self.assertRaises(Refusal) as raised:
            R.capture_checkpoint(DEVICE, BUNDLE, "cp1", str(self.root / "cps"), self.repo, self.out_dir(), self.stages)
        self.assertEqual(raised.exception.status, "NO-DATA")

    def test_capture_checkpoint_failed_write_leaves_no_partial_and_allows_retry(self):
        # Minor 5 (2026-09-15 review): a mid-write failure must not leave
        # a corrupt file sitting at the real target name, which would
        # otherwise permanently block every retry via the require() check
        # that refuses to overwrite an existing checkpoint.
        self.install_app_on_disk()
        cpdir = self.root / "cps"
        target = cpdir / "cp1.tar"
        with patch("tarfile.TarFile.add", side_effect=OSError("simulated disk full")):
            with self.assertRaises(OSError):
                R.capture_checkpoint(DEVICE, BUNDLE, "cp1", str(cpdir), self.repo, self.out_dir(), self.stages)
        self.assertFalse(target.exists())
        self.assertEqual(list(cpdir.glob("*.partial")), [])
        # A retry with the real tool now succeeds.
        result = R.capture_checkpoint(DEVICE, BUNDLE, "cp1", str(cpdir), self.repo, self.out_dir(), self.stages)
        self.assertTrue(result.is_file())

    def test_capture_checkpoint_real_tar_round_trip(self):
        self.install_app_on_disk()
        cpdir = self.root / "cps"
        target = R.capture_checkpoint(DEVICE, BUNDLE, "cp1", str(cpdir), self.repo, self.out_dir(), self.stages)
        self.assertTrue(target.is_file())
        with tarfile.open(target) as tf:
            self.assertIn("./seed.txt", tf.getnames())

    def test_run_uninstall_writes_receipt(self):
        report, receipt = R.run_uninstall(_ctx(), self.repo, self.out)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(receipt.is_file())

    def test_run_install_writes_receipt(self):
        app = self.root / "built.app"; app.mkdir()
        report, receipt = R.run_install(_ctx(app_path=str(app)), self.repo, self.out)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(receipt.is_file())

    def test_run_capture_checkpoint_writes_receipt(self):
        self.install_app_on_disk()
        report, receipt = R.run_capture_checkpoint(_ctx(checkpoints_dir=str(self.root / "cps")), self.repo, self.out, "cp1")
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(receipt.is_file())

    # -- main() CLI ----------------------------------------------------------

    def test_main_apply_end_to_end_exit_code(self):
        fixture_path = self.write_fixture(_valid_fixture(keychain_policy={"mode": "preserve"}))
        code = R.main(["apply", "--fixture", fixture_path, "--repo", str(self.repo),
                        "--out", str(self.out), "--device", DEVICE, "--bundle-id", BUNDLE])
        self.assertEqual(code, 0)

    def test_main_apply_missing_fixture_file_is_no_data_exit(self):
        code = R.main(["apply", "--fixture", str(self.root / "nope.json"), "--repo", str(self.repo),
                        "--out", str(self.out)])
        self.assertEqual(code, 2)

    # -- helpers --------------------------------------------------------------

    def out_dir(self):
        self.out.mkdir(parents=True, exist_ok=True)
        return self.out

    def install_app_on_disk(self):
        (self.root / "installed_marker").write_text("yes")
        container = self.root / "container"; container.mkdir(exist_ok=True)
        (container / "seed.txt").write_text("seed")

    def write_fixture(self, fixture):
        path = self.root / "fixture.json"
        path.write_text(json.dumps(fixture))
        return str(path)

    def http_server(self, status):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]

        class _Ctx:
            def __enter__(self_inner):
                return "http://127.0.0.1:%d/" % port

            def __exit__(self_inner, *exc):
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        return _Ctx()


if __name__ == "__main__":
    unittest.main()
