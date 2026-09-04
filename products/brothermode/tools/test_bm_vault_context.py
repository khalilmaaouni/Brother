#!/usr/bin/env python3
"""Calibration for tools/bm_vault_context.py, WBS row VB3-03.

Unit-level only: the request id is immutable and carries no tenant string, the
enterprise-mode field check names exactly what is missing, and tenant_env refuses a
tenant that is not a clean, pre-provisioned single-segment name -- never a guess, never a
silent fallback to the shared environment. The end-to-end request-id-in-ledger, enterprise
refusal and two-tenant leakage properties are calibrated against the real HTTP server in
test_bm_vault_serve.py, which is where a served answer's shape actually lives.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_context as ctx  # noqa: E402

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


class NewRequestId(unittest.TestCase):
    def test_is_a_bare_hex32_uuid4_carrying_nothing_else(self):
        rid = ctx.new_request_id()
        self.assertRegex(rid, r"^[0-9a-f]{32}$")

    def test_two_calls_never_collide(self):
        self.assertNotEqual(ctx.new_request_id(), ctx.new_request_id())

    def test_no_tenant_string_can_ever_appear_inside_it(self):
        # The whole point of a uuid4 mint: nothing fed to this module ever reaches the
        # id, because nothing is fed to it at all.
        rid = ctx.new_request_id()
        for tenant in ("tenant-a", "tenant-b", "acme-corp"):
            self.assertNotIn(tenant, rid)


class MissingEnterpriseFields(unittest.TestCase):
    def test_both_present_is_clean(self):
        self.assertEqual(ctx.missing_enterprise_fields("tenant-a", "alice"), [])

    def test_both_absent_names_both_in_order(self):
        self.assertEqual(ctx.missing_enterprise_fields(None, None),
                         ["tenant", "principal"])

    def test_blank_and_whitespace_count_as_absent(self):
        self.assertEqual(ctx.missing_enterprise_fields("", "   "),
                         ["tenant", "principal"])

    def test_wrong_type_counts_as_absent_never_a_crash(self):
        self.assertEqual(ctx.missing_enterprise_fields(5, ["alice"]),
                         ["tenant", "principal"])

    def test_only_principal_missing_names_only_principal(self):
        self.assertEqual(ctx.missing_enterprise_fields("tenant-a", None),
                         ["principal"])


class TenantEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-context-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "tenant-a")
        os.makedirs(os.path.join(self.home, "vault"))
        os.makedirs(os.path.join(self.home, ".claude"))

    def test_no_tenants_root_configured_refuses(self):
        env, err = ctx.tenant_env(None, "tenant-a")
        self.assertIsNone(env)
        self.assertIn("tenants-root", err)

    def test_unsafe_tenant_string_refuses_without_touching_disk(self):
        for bad in ("../escape", "a/b", "with space", ""):
            env, err = ctx.tenant_env(self.tmp, bad)
            self.assertIsNone(env, "tenant %r should have been refused" % bad)
            self.assertIsNotNone(err)

    def test_unprovisioned_tenant_refuses(self):
        env, err = ctx.tenant_env(self.tmp, "never-provisioned")
        self.assertIsNone(env)
        self.assertIn("not provisioned", err)

    def test_provisioned_tenant_resolves_home_and_vault(self):
        env, err = ctx.tenant_env(self.tmp, "tenant-a")
        self.assertIsNone(err)
        self.assertEqual(env["HOME"], self.home)
        self.assertEqual(env["BM_VAULT_ROOT"], os.path.join(self.home, "vault"))

    def test_missing_only_state_dir_still_refuses(self):
        home2 = os.path.join(self.tmp, "tenant-b")
        os.makedirs(os.path.join(home2, "vault"))  # no .claude
        env, err = ctx.tenant_env(self.tmp, "tenant-b")
        self.assertIsNone(env)
        self.assertIn("not provisioned", err)


class TenantRegexShape(unittest.TestCase):
    def test_matches_letters_digits_dash_underscore_only(self):
        for ok in ("tenant-a", "TENANT_1", "abc123"):
            self.assertRegex(ok, ctx.TENANT_RE.pattern)

    def test_rejects_path_separators_and_dots(self):
        for bad in ("../x", "a/b", "a.b", "a b", ""):
            self.assertIsNone(ctx.TENANT_RE.match(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
