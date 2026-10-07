"""Tests for mobile_deterministic_hooks.py (EPIC M2.04).

Like test_mobile_state_reset.py, these drive a fake xcrun/simctl on PATH:
they prove dispatch, honesty (PASS/FAIL/NO-DATA routing), the structural
no-op contract for clock.mode=='real' and randomness_seed=None, and the
single-relaunch fix for the feature-flags overwrite hazard -- not
Xcode/simctl compatibility. Every fixture here is a synthetic, throwaway
value: no real account, project, or app name is read or referenced
anywhere in this file.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_deterministic_hooks as H  # noqa: E402
import mobile_state_fixture as F  # noqa: E402
import mobile_state_reset as R  # noqa: E402
from mobile_workflow import Refusal  # noqa: E402

DEVICE = "11111111-2222-3333-4444-555555555555"
BUNDLE = "com.example.fixture-app"

# A fake xcrun: tracks "installed" state on disk under FIXTURE_ROOT, and for
# simctl launch records exactly which SIMCTL_CHILD_ env vars it received --
# the real evidence this suite needs for the "does one relaunch carry
# everything" regression test.
TOOL = r'''#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
marker = root / "installed_marker"
if args[:2] == ["simctl", "get_app_container"]:
    if marker.exists():
        print(str(root / "container"))
        sys.exit(0)
    sys.exit(2)
if args[:2] == ["simctl", "launch"]:
    (root / "launch-clock").write_text(os.environ.get("SIMCTL_CHILD_MOBILE_CLOCK_FIXED_VALUE", ""))
    (root / "launch-seed").write_text(os.environ.get("SIMCTL_CHILD_MOBILE_JOURNEY_SEED", ""))
    (root / "launch-flag").write_text(os.environ.get("SIMCTL_CHILD_NEW_ONBOARDING", ""))
    (root / "launch-called").write_text("yes")
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
    import argparse
    base = dict(platform="ios", device=DEVICE, bundle_id=BUNDLE)
    base.update(overrides)
    return argparse.Namespace(**base)


class ClockHookTests(unittest.TestCase):
    def test_real_mode_is_explicit_structural_no_op(self):
        r = H.clock_hook(_valid_fixture(clock={"mode": "real"}))
        self.assertEqual(r["env"], {})
        self.assertFalse(r["applies"])
        self.assertIn("no-op", r["note"])
        self.assertIn("real", r["note"])

    def test_fixed_mode_injects_reserved_var(self):
        r = H.clock_hook(_valid_fixture(clock={"mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"}))
        self.assertEqual(r["env"], {H.CLOCK_ENV_VAR: "2026-01-01T00:00:00Z"})
        self.assertTrue(r["applies"])

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            H.clock_hook(_valid_fixture(clock={"mode": "not-a-real-mode"}))

    def test_whitespace_only_fixed_value_refused(self):
        # MAJOR 2 regression: clock_hook is a defense-in-depth layer of its
        # own, independent of M2.01's schema-layer check, since it is also
        # reachable directly (as this test does) without ever routing
        # through F.check() first.
        with self.assertRaises(Refusal):
            H.clock_hook(_valid_fixture(clock={"mode": "fixed", "fixed_value": "   "}))

    def test_non_iso8601_fixed_value_refused(self):
        with self.assertRaises(Refusal):
            H.clock_hook(_valid_fixture(clock={"mode": "fixed", "fixed_value": "not-a-timestamp"}))

    def test_invalid_calendar_date_fixed_value_refused(self):
        with self.assertRaises(Refusal):
            H.clock_hook(_valid_fixture(clock={"mode": "fixed", "fixed_value": "2026-13-45T99:99:99Z"}))


class RandomnessHookTests(unittest.TestCase):
    def test_null_seed_is_explicit_structural_no_op(self):
        r = H.randomness_hook(_valid_fixture(randomness_seed=None))
        self.assertEqual(r["env"], {})
        self.assertFalse(r["applies"])
        self.assertIn("no-op", r["note"])

    def test_seed_injects_reserved_var_as_string(self):
        r = H.randomness_hook(_valid_fixture(randomness_seed=42))
        self.assertEqual(r["env"], {H.SEED_ENV_VAR: "42"})
        self.assertTrue(r["applies"])

    def test_seed_zero_is_not_treated_as_falsy_none(self):
        # 0 is a valid seed and must not be confused with "no seed" -- only
        # `is None` may gate the no-op branch.
        r = H.randomness_hook(_valid_fixture(randomness_seed=0))
        self.assertEqual(r["env"], {H.SEED_ENV_VAR: "0"})
        self.assertTrue(r["applies"])


class FeatureFlagsEnvTests(unittest.TestCase):
    def test_mirrors_apply_feature_flags_convention(self):
        env = H.feature_flags_env(_valid_fixture(feature_flags={"new_onboarding": True, "old_thing": False}))
        self.assertEqual(env, {"SIMCTL_CHILD_NEW_ONBOARDING": "1", "SIMCTL_CHILD_OLD_THING": "0"})

    def test_empty_flags_is_empty_dict(self):
        self.assertEqual(H.feature_flags_env(_valid_fixture(feature_flags={})), {})

    def test_is_the_same_shared_function_as_mobile_state_reset(self):
        # MAJOR 3 regression: this module no longer keeps its own hand
        # copy of the flag-to-env-var mapping -- it imports the single
        # shared one mobile_state_reset.py defines, so the two modules
        # can never quietly diverge again.
        self.assertIs(H.feature_flags_env, R.feature_flags_env)

    def test_duplicate_case_flag_names_collapse_identically_in_both_modules(self):
        # The known low-priority gap PR #719's review named (two
        # differently-cased flag names collide into one env var) behaves
        # identically through both modules now, by construction: they run
        # the exact same function, not two copies that could drift.
        fixture = _valid_fixture(feature_flags={"debug_mode": True, "DEBUG_MODE": False})
        via_hooks_module = H.feature_flags_env(fixture)
        via_reset_module = R.feature_flags_env(fixture)
        self.assertEqual(via_hooks_module, via_reset_module)
        self.assertEqual(set(via_hooks_module), {"SIMCTL_CHILD_DEBUG_MODE"})

    def test_illegal_env_var_name_refused_cleanly(self):
        # minor m1: a flag name containing "=" passes M2.01 (feature_flags
        # keys carry no pattern constraint there) and must be refused here,
        # at this validation layer, rather than crashing subprocess later
        # with "ValueError: illegal environment variable name".
        fixture = _valid_fixture(feature_flags={"mobile_journey_seed=999": True})
        with self.assertRaises(Refusal):
            H.feature_flags_env(fixture)
        with self.assertRaises(Refusal):
            R.feature_flags_env(fixture)


class CombinedEnvTests(unittest.TestCase):
    def test_merges_all_three_sources(self):
        fixture = _valid_fixture(
            feature_flags={"new_onboarding": True},
            clock={"mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"},
            randomness_seed=42,
        )
        env = H.combined_env(fixture)
        self.assertEqual(env, {
            "SIMCTL_CHILD_NEW_ONBOARDING": "1",
            H.CLOCK_ENV_VAR: "2026-01-01T00:00:00Z",
            H.SEED_ENV_VAR: "42",
        })

    def test_no_op_fixture_merges_to_empty(self):
        fixture = _valid_fixture(feature_flags={}, clock={"mode": "real"}, randomness_seed=None)
        self.assertEqual(H.combined_env(fixture), {})

    def test_flag_named_to_collide_with_clock_var_refuses(self):
        # SIMCTL_CHILD_ + "mobile_clock_fixed_value".upper() collides exactly
        # with H.CLOCK_ENV_VAR.
        fixture = _valid_fixture(
            feature_flags={"mobile_clock_fixed_value": True},
            clock={"mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"},
        )
        with self.assertRaises(Refusal):
            H.combined_env(fixture)

    def test_flag_named_to_collide_with_seed_var_refuses(self):
        fixture = _valid_fixture(feature_flags={"mobile_journey_seed": True}, randomness_seed=7)
        with self.assertRaises(Refusal):
            H.combined_env(fixture)

    def test_no_collision_when_hook_does_not_apply(self):
        # A flag named like the seed var is harmless when randomness_seed
        # is null (nothing reserved is actually being injected).
        fixture = _valid_fixture(feature_flags={"mobile_journey_seed": True}, randomness_seed=None,
                                  clock={"mode": "real"})
        env = H.combined_env(fixture)
        self.assertEqual(env, {"SIMCTL_CHILD_MOBILE_JOURNEY_SEED": "1"})


class ApplyHooksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-deterministic-hooks-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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

    def out_dir(self):
        self.out.mkdir(parents=True, exist_ok=True)
        return self.out

    def install_app_on_disk(self):
        (self.root / "installed_marker").write_text("yes")
        (self.root / "container").mkdir(exist_ok=True)

    def test_empty_env_is_no_op_not_pass_no_launch(self):
        # Regression test for CRITICAL 1: a fixture that names nothing to
        # apply must never report PASS (a real reset action that never
        # happened), the exact bug class PR #719 fixed for
        # mobile_state_reset.py's own "nothing to apply" mechanisms and
        # this module had reintroduced at its own top level.
        fixture = _valid_fixture(feature_flags={}, clock={"mode": "real"}, randomness_seed=None)
        r = H.apply_hooks(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-OP")
        self.assertIn("nothing to apply", r["detail"])
        self.assertFalse((self.root / "launch-called").exists())

    def test_empty_env_on_android_with_no_device_is_still_no_op(self):
        # The reviewer's exact adversarial case: platform=android, no
        # adapter, no device, empty env. Nothing needed to happen regardless
        # of platform, so NO-OP (not a platform-blamed NO-DATA, and never
        # PASS) is the honest result, and zero simctl stages run.
        fixture = _valid_fixture(feature_flags={}, clock={"mode": "real"}, randomness_seed=None)
        r = H.apply_hooks(fixture, _ctx(platform="android", device=None, bundle_id=None),
                           self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-OP")
        self.assertEqual(self.stages, [])

    def test_non_ios_platform_is_no_data_but_computes_env(self):
        fixture = _valid_fixture(clock={"mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"})
        r = H.apply_hooks(fixture, _ctx(platform="android"), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")
        self.assertEqual(r["env"][H.CLOCK_ENV_VAR], "2026-01-01T00:00:00Z")

    def test_missing_device_is_no_data(self):
        fixture = _valid_fixture(randomness_seed=1)
        r = H.apply_hooks(fixture, _ctx(device=None), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_not_installed_is_no_data(self):
        fixture = _valid_fixture(randomness_seed=1)
        r = H.apply_hooks(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "NO-DATA")

    def test_collision_is_fail_not_a_crash(self):
        fixture = _valid_fixture(feature_flags={"mobile_journey_seed": True}, randomness_seed=7)
        r = H.apply_hooks(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    def test_invalid_clock_fixed_value_is_fail_not_a_crash(self):
        # MAJOR 2 + minor m2 combined regression: clock_hook is called
        # directly inside apply_hooks's own try, so an invalid fixed_value
        # must surface as a clean FAIL result here too, not only when
        # clock_hook is called standalone (ClockHookTests above).
        fixture = _valid_fixture(clock={"mode": "fixed", "fixed_value": "not-a-timestamp"})
        r = H.apply_hooks(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")
        self.assertFalse((self.root / "launch-called").exists())

    def test_unknown_clock_mode_is_fail_not_an_uncaught_crash(self):
        # minor m2: an unknown clock.mode used to raise ValueError past
        # apply_hooks's own public boundary uncaught.
        fixture = _valid_fixture(clock={"mode": "not-a-real-mode"})
        r = H.apply_hooks(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "FAIL")

    def test_real_live_apply_carries_all_three_sources_in_one_relaunch(self):
        # The regression test for the overwrite hazard this unit's brief
        # asked to be hunted for: one relaunch, all three env vars present
        # together, proving a caller does not need a second relaunch that
        # would drop one set in favor of the other.
        self.install_app_on_disk()
        fixture = _valid_fixture(
            feature_flags={"new_onboarding": True},
            clock={"mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"},
            randomness_seed=42,
        )
        r = H.apply_hooks(fixture, _ctx(), self.repo, self.out_dir(), self.stages)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual((self.root / "launch-clock").read_text(), "2026-01-01T00:00:00Z")
        self.assertEqual((self.root / "launch-seed").read_text(), "42")
        self.assertEqual((self.root / "launch-flag").read_text(), "1")


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-deterministic-hooks-cli-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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

    def write_fixture(self, fixture):
        path = self.root / "fixture.json"
        path.write_text(json.dumps(fixture))
        return str(path)

    def test_main_end_to_end_exit_code_and_evidence(self):
        (self.root / "installed_marker").write_text("yes")
        (self.root / "container").mkdir(exist_ok=True)
        fixture_path = self.write_fixture(_valid_fixture(
            clock={"mode": "fixed", "fixed_value": "2026-01-01T00:00:00Z"}, randomness_seed=1))
        code = H.main(["--fixture", fixture_path, "--repo", str(self.repo), "--out", str(self.out),
                        "--device", DEVICE, "--bundle-id", BUNDLE])
        self.assertEqual(code, 0)
        self.assertTrue(self.out.is_dir())

    def test_main_invalid_fixture_refuses_and_writes_no_out_dir(self):
        bad = _valid_fixture(); bad["schema_version"] = "wrong"
        fixture_path = self.write_fixture(bad)
        code = H.main(["--fixture", fixture_path, "--repo", str(self.repo), "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertFalse(self.out.exists())

    def test_main_missing_fixture_file_is_no_data_exit(self):
        code = H.main(["--fixture", str(self.root / "nope.json"), "--repo", str(self.repo), "--out", str(self.out)])
        self.assertEqual(code, 2)

    def test_main_no_op_fixture_never_exits_zero(self):
        # CRITICAL 1's exact end-to-end proof: a fixture with nothing to
        # apply must exit non-zero through the real CLI entry point, the
        # same way NO-DATA does, never the 0 a real PASS gets.
        fixture_path = self.write_fixture(_valid_fixture(
            feature_flags={}, clock={"mode": "real"}, randomness_seed=None))
        code = H.main(["--fixture", fixture_path, "--repo", str(self.repo), "--out", str(self.out),
                        "--platform", "android"])
        self.assertNotEqual(code, 0)
        self.assertEqual(code, 2)


class SchemaSanityTest(unittest.TestCase):
    """Confirms the fixture helper this file uses actually satisfies M2.01's
    real schema, so every test above is exercising a genuinely valid
    fixture shape, not a fixture that happens to work by accident."""

    def test_valid_fixture_helper_passes_real_m201_validation(self):
        schema_obj = json.loads(Path(F.DEFAULT_SCHEMA).read_text())
        problems = F.check(_valid_fixture(), schema_obj)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
