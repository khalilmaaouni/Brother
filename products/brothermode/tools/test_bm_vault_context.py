#!/usr/bin/env python3
"""Calibration for tools/bm_vault_context.py, WBS row VB3-03.

Unit-level only: the request id is immutable and carries no tenant string, the
enterprise-mode field check names exactly what is missing, tenant_env refuses a
tenant that is not a clean, pre-provisioned single-segment name -- never a guess, never a
silent fallback to the shared environment -- and classify_answer_class/
build_request_envelope stay a deterministic keyword table and a read-only-by-default
envelope, never a model call and never a silent write grant. The end-to-end
request-id-in-ledger, enterprise refusal and two-tenant leakage properties are
calibrated against the real HTTP server in test_bm_vault_serve.py, which is where a
served answer's shape actually lives.

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


class ClassifyAnswerClass(unittest.TestCase):
    def test_update_named_system_record_is_transactional(self):
        self.assertEqual(
            ctx.classify_answer_class("Update the SAP record for outlet 4021"),
            "TRANSACTIONAL_ACTION")

    def test_ambiguous_fix_never_classifies_transactional(self):
        # "fix" could mean "explain what's wrong" or "change it" -- a genuinely
        # ambiguous verb, so this must land on a non-mutating class, never
        # TRANSACTIONAL_ACTION, per the bias-to-safe rule.
        result = ctx.classify_answer_class("Fix the customer's hierarchy")
        self.assertNotEqual(result, "TRANSACTIONAL_ACTION")
        self.assertIn(result, ctx.ANSWER_CLASSES)

    def test_golden_account_mastering_question_is_master_lookup(self):
        # More specific than STANDARD_POLICY: this asks for the one authoritative
        # mastering source, not a standing rule.
        self.assertEqual(
            ctx.classify_answer_class(
                "What is the current Golden Account mastering authority?"),
            "MASTER_LOOKUP")

    def test_open_ended_why_question_is_exploratory(self):
        # Mentions a metric noun ("volume") but asks why it moved, not for its
        # certified value, so this is analysis, not OFFICIAL_METRIC.
        self.assertEqual(
            ctx.classify_answer_class("Why is volume declining at outlet 12?"),
            "EXPLORATORY_ANALYSIS")

    def test_direct_metric_value_ask_is_official_metric(self):
        self.assertEqual(
            ctx.classify_answer_class("What is the current revenue for outlet 12?"),
            "OFFICIAL_METRIC")

    def test_none_and_blank_text_default_to_exploratory_never_crash(self):
        for bad in (None, "", "   "):
            self.assertEqual(ctx.classify_answer_class(bad), "EXPLORATORY_ANALYSIS")

    def test_mutating_verb_without_a_named_target_is_not_transactional(self):
        # "update" with no record/system named: not unambiguous enough on its own.
        self.assertNotEqual(
            ctx.classify_answer_class("Update me on the outlet situation"),
            "TRANSACTIONAL_ACTION")


class BuildRequestEnvelope(unittest.TestCase):
    def test_transactional_question_without_authorization_stays_read_only(self):
        env, missing = ctx.build_request_envelope(
            "Update the SAP record for outlet 4021")
        self.assertEqual(env["answer_class"], "TRANSACTIONAL_ACTION")
        self.assertEqual(env["allowed_actions"], ["read"])

    def test_explicit_authorization_is_the_only_way_to_widen_allowed_actions(self):
        env, _ = ctx.build_request_envelope(
            "Update the SAP record for outlet 4021",
            explicit_action_authorization=["read", "write"])
        self.assertEqual(env["allowed_actions"], ["read", "write"])

    def test_no_tenant_or_principal_surfaces_missing_enterprise_fields(self):
        # Reuses missing_enterprise_fields rather than a second check: this must be
        # the exact list that function returns for the same two absent values.
        env, missing = ctx.build_request_envelope("What is the current revenue?")
        self.assertEqual(missing, ctx.missing_enterprise_fields(None, None))
        self.assertEqual(missing, ["tenant", "principal"])

    def test_tenant_and_principal_present_reports_nothing_missing(self):
        _, missing = ctx.build_request_envelope(
            "What is the current revenue?", tenant="tenant-a", principal="alice")
        self.assertEqual(missing, [])

    def test_schema_actor_and_question_shape(self):
        env, _ = ctx.build_request_envelope(
            "Why is volume declining at outlet 12?",
            human="alice", agent="bm_vault_serve", purpose="ops review",
            channel="slack", locale="en-US")
        self.assertEqual(env["schema"], ctx.REQUEST_ENVELOPE_SCHEMA)
        self.assertEqual(env["actor"],
                         {"human": "alice", "agent": "bm_vault_serve",
                          "purpose": "ops review"})
        self.assertEqual(env["channel"], "slack")
        self.assertEqual(env["locale"], "en-US")
        self.assertEqual(env["question"], {"text": "Why is volume declining at outlet 12?"})
        self.assertRegex(env["request_id"], r"^[0-9a-f]{32}$")

    def test_request_id_reuses_new_request_id_two_calls_never_collide(self):
        env1, _ = ctx.build_request_envelope("q1")
        env2, _ = ctx.build_request_envelope("q2")
        self.assertNotEqual(env1["request_id"], env2["request_id"])


class TenantRegexShape(unittest.TestCase):
    def test_matches_letters_digits_dash_underscore_only(self):
        for ok in ("tenant-a", "TENANT_1", "abc123"):
            self.assertRegex(ok, ctx.TENANT_RE.pattern)

    def test_rejects_path_separators_and_dots(self):
        for bad in ("../x", "a/b", "a.b", "a b", ""):
            self.assertIsNone(ctx.TENANT_RE.match(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
