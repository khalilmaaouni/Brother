#!/usr/bin/env python3
"""Calibration for tools/bm_vault_principals.py, WBS row VB7-05: the principal registry
and the offboarding proof.

The property under test is the row's own done_check: an active fixture principal recalls
normally, the same principal revoked gets zero results with the refusal recorded in the
access audit, reactivation restores it, and all three transitions are exercised against a
real fixture vault and a real bm_vault.py recall, not a mock. A CALIBRATION class drives
the revocation guard backwards (neuters the registry consult in a COPY of the whole tools
directory, never the real one) and watches the revoked-gets-zero-results property fail,
proving the real guard bites rather than a tautology.

A second half proves the folded-in finding this row also closes: bm_vault_serve.py used to
forward a client's declared identity only into --as (the audit principal), never into
--identity (the VB2-01/VB7-05 policy trim), so a revoked caller's recall over the wire
never actually withheld anything on their behalf even though the audit correctly named
them. Withholding must now differ per identity over the wire exactly as it does locally,
and a calibration on a pre-fix copy of bm_vault_serve.py proves the old shape actually had
this gap.

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")
SERVE = os.path.join(HERE, "bm_vault_serve.py")
LINT = os.path.join(HERE, "bm_vault_lint.py")

sys.path.insert(0, HERE)
import bm_vault_principals as principals  # noqa: E402

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


def run(argv, tool=TOOL, env=None):
    p = subprocess.run([sys.executable, tool] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8", "replace"), \
        p.stderr.decode("utf-8", "replace")


def _fixture_env(tmp, vault):
    env = dict(os.environ)
    env["HOME"] = tmp
    env["BROTHERMODE_ROOT"] = tmp
    env["BM_VAULT_ROOT"] = vault
    env["BM_FRESHNESS_ROOTS"] = tmp
    env["BM_FRESHNESS_STATE"] = os.path.join(tmp, "freshness_state.sqlite3")
    env.pop("BM_IDENTITY", None)
    os.makedirs(os.path.join(tmp, ".claude"), exist_ok=True)
    return env


def _copy_tools_dir(tmp):
    """A full copy of tools/ (minus tests and __pycache__) so a calibration can edit ONE
    sibling module's source and have bm_vault.py's own by-path dynamic loaders (which
    always resolve against THEIR OWN directory) pick up the edited copy, with every other
    dependency present unmodified. Never touches the real tools/ directory."""
    dest = os.path.join(tmp, "tools")
    shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns("test_*.py", "__pycache__"))
    return dest


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-principals-")
        self.path = os.path.join(self.tmp, "principals.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TheRegistryContract(RegistryFixture):
    """load/status_of/registry_path directly: no subprocess."""

    def test_absent_file_is_the_opt_in_state(self):
        self.assertEqual(principals.load(self.path), (None, []))

    def test_a_broken_file_is_a_named_problem_not_a_silent_pass(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        registry, problems = principals.load(self.path)
        self.assertIsNone(registry)
        self.assertTrue(problems)

    def test_status_of_absent_name_or_absent_registry_is_none(self):
        registry = {"principals": {"alice": {"status": "active"}}}
        self.assertIsNone(principals.status_of(registry, "bob"))
        self.assertIsNone(principals.status_of(None, "alice"))

    def test_status_of_known_name_reads_its_status(self):
        registry = {"principals": {"alice": {"status": "revoked"}}}
        self.assertEqual(principals.status_of(registry, "alice"), "revoked")

    def test_registry_path_mirrors_policy_path_shape(self):
        self.assertEqual(principals.registry_path("/v", None),
                         os.path.join("/v", "99-System", "principals.json"))
        self.assertEqual(principals.registry_path("/v", "/override.json"), "/override.json")
        self.assertIsNone(principals.registry_path(None, None))


class IdentityNormalizationClosesTheCaseBypass(RegistryFixture):
    """Review MAJOR 1: status_of used to be an exact-string lookup, so revoking "alice"
    left "Alice" (or "alice " or a fullwidth variant) still active. normalize_identity is
    now the one owner of matching, applied at both add and lookup time."""

    FULLWIDTH_ALICE = u"ａｌｉｃｅ"  # NFKC-normalizes to "alice"

    def test_normalize_identity_folds_case_whitespace_and_fullwidth_to_one_form(self):
        target = principals.normalize_identity("alice")
        self.assertEqual(principals.normalize_identity("Alice"), target)
        self.assertEqual(principals.normalize_identity("alice "), target)
        self.assertEqual(principals.normalize_identity(self.FULLWIDTH_ALICE), target)

    def test_revoking_alice_denies_Alice_alice_with_space_and_fullwidth_variant(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-30", True)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        for variant in ("Alice", "alice ", self.FULLWIDTH_ALICE):
            self.assertEqual(principals.status_of(registry, variant), "revoked",
                             "variant %r must resolve to the revoked alice entry" % variant)

    def test_add_refuses_a_name_that_collides_with_an_existing_one_after_normalization(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_add(self.path, "Alice", "human", "khalil", "2026-08-30", True)
        self.assertEqual(rc, 1, "a colliding name must refuse at add, named, never silent")
        registry, _ = principals.load(self.path)
        self.assertEqual(sorted(registry["principals"]), ["alice"],
                         "no second entry may be created for the colliding name")

    def test_reactivate_of_a_case_variant_finds_the_real_entry(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-30", True)
        rc = principals.cmd_reactivate(self.path, "ALICE", "khalil", "2026-08-31", True)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["status"], "active")

    def test_CALIBRATION_reverting_to_exact_lookup_reopens_the_case_bypass(self):
        """Driven backwards: patch status_of's _find_key call back into an exact-string
        dict lookup (module-level, restored in a finally) and watch "Alice" read as None
        (unaffected) even though "alice" is revoked, reproducing the original defect."""
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-30", True)
        registry, _ = principals.load(self.path)
        self.assertEqual(principals.status_of(registry, "Alice"), "revoked",
                         "sanity: the real fix must resolve this before we break it")

        real_find_key = principals._find_key

        def exact_only(principals_dict, name):
            return name if name in principals_dict else None

        principals._find_key = exact_only
        try:
            self.assertIsNone(principals.status_of(registry, "Alice"),
                              "with lookup reverted to exact-string matching, the case "
                              "bypass must reproduce: Alice reads as unaffected")
        finally:
            principals._find_key = real_find_key


class EveryMutationIsARecordedAct(RegistryFixture):
    """add/revoke/reactivate mirror bm_vault_promotions.py's own posture: dry run by
    default, --by required, never a silent write."""

    def test_add_needs_by(self):
        rc = principals.cmd_add(self.path, "alice", "human", None, "2026-08-30", True)
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.path))

    def test_add_is_dry_run_by_default(self):
        rc = principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", False)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.path))

    def test_add_then_add_again_is_refused_add_is_not_an_update_path(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        self.assertEqual(rc, 1)

    def test_revoke_of_an_unknown_name_is_no_data(self):
        rc = principals.cmd_revoke(self.path, "ghost", "khalil", "2026-08-30", True)
        self.assertEqual(rc, 2)

    def test_revoke_needs_by_and_writes_nothing_without_it(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_revoke(self.path, "alice", None, "2026-08-30", True)
        self.assertEqual(rc, 2)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["status"], "active")

    def test_revoke_twice_is_a_no_op_the_second_time(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-30", True)
        rc = principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-31", True)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["recorded_at"], "2026-08-30",
                         "a same-state retry must not rewrite the recorded date")

    def test_revoke_never_deletes_the_entry_it_only_flips_status(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-30", True)
        registry, _ = principals.load(self.path)
        self.assertIn("alice", registry["principals"])
        self.assertEqual(registry["principals"]["alice"]["status"], "revoked")
        self.assertEqual(registry["principals"]["alice"]["added_by"], "khalil",
                         "added_at/added_by must survive a later mutation")

    def test_reactivate_restores_active_and_records_who(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        principals.cmd_revoke(self.path, "alice", "khalil", "2026-08-30", True)
        rc = principals.cmd_reactivate(self.path, "alice", "khalil", "2026-08-31", True)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["status"], "active")
        self.assertEqual(registry["principals"]["alice"]["recorded_at"], "2026-08-31")

    def test_list_reports_every_principal_and_filters_by_status(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        principals.cmd_add(self.path, "bob", "agent", "khalil", "2026-08-30", True)
        principals.cmd_revoke(self.path, "bob", "khalil", "2026-08-30", True)
        self.assertEqual(principals.cmd_list(self.path, None), 0)
        self.assertEqual(principals.cmd_list(self.path, "revoked"), 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(sorted(registry["principals"]), ["alice", "bob"])


class RoleIsRecordedPerPrincipal(RegistryFixture):
    """VB10-03: a role field (reader, editor, steward, owner), record-only here (Entra
    enforces it only at service mode). add defaults a new principal to reader; set-role
    is the recorded-mutation path for an existing one, same dry-run/--by/no-op shape as
    revoke/reactivate, and the same tamper tripwire still applies since it stamps the
    identical recorded_at/recorded_by fields."""

    def test_add_defaults_role_to_reader(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["role"], "reader")

    def test_add_can_set_a_role_explicitly(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True,
                            "owner")
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["role"], "owner")

    def test_add_refuses_an_unknown_role(self):
        rc = principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True,
                                 "superuser")
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.path))

    def test_set_role_changes_an_existing_principal_added_before_the_field_existed(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_set_role(self.path, "alice", "steward", "khalil",
                                     "2026-08-31", True)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["role"], "steward")
        self.assertEqual(registry["principals"]["alice"]["recorded_at"], "2026-08-31")

    def test_set_role_is_dry_run_by_default(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_set_role(self.path, "alice", "owner", "khalil",
                                     "2026-08-31", False)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["role"], "reader")

    def test_set_role_refuses_without_by(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_set_role(self.path, "alice", "owner", None, "2026-08-31", True)
        self.assertEqual(rc, 2)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["role"], "reader")

    def test_set_role_no_op_when_already_that_role(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_set_role(self.path, "alice", "reader", "khalil",
                                     "2026-08-31", True)
        self.assertEqual(rc, 0)
        registry, _ = principals.load(self.path)
        self.assertEqual(registry["principals"]["alice"]["recorded_at"], "2026-08-30",
                         "a same-role retry must not rewrite the recorded date")

    def test_set_role_on_unknown_principal_is_no_data(self):
        rc = principals.cmd_set_role(self.path, "ghost", "owner", "khalil",
                                     "2026-08-30", True)
        self.assertEqual(rc, 2)

    def test_set_role_refuses_an_unknown_role_value(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        rc = principals.cmd_set_role(self.path, "alice", "superuser", "khalil",
                                     "2026-08-30", True)
        self.assertEqual(rc, 2)

    def test_list_shows_role_and_unset_for_a_legacy_entry_missing_it(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        registry, _ = principals.load(self.path)
        registry["principals"]["legacy"] = {
            "kind": "human", "status": "active",
            "added_at": "2026-08-30", "added_by": "khalil",
            "recorded_at": "2026-08-30", "recorded_by": "khalil",
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(registry, f)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = principals.cmd_list(self.path, None)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        alice_line = [ln for ln in out.splitlines() if ln.strip().startswith("alice:")][0]
        legacy_line = [ln for ln in out.splitlines() if ln.strip().startswith("legacy:")][0]
        self.assertIn("role=reader", alice_line)
        self.assertIn("role=UNSET", legacy_line,
                      "a role never recorded must never be invented at read time")

    def test_a_tamper_suspect_entry_with_a_role_still_reads_as_revoked(self):
        """Regression: adding the role field must not weaken the existing tamper
        tripwire (_is_tamper_suspect only ever looks at recorded_at/recorded_by)."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"principals": {"mallory": {
                "kind": "human", "status": "active", "role": "owner",
                "added_at": "2026-08-30", "added_by": "khalil"}}}, f)
        registry, _ = principals.load(self.path)
        self.assertEqual(principals.status_of(registry, "mallory"), "revoked",
                         "a tamper-suspect entry must still fail closed with a role set")


class TamperSuspectHandCanEditTheRegistryFileDirectly(RegistryFixture):
    """Review MAJOR 2: a caller with vault write access can hand-edit principals.json back
    to active with no recorded act. This module cannot stop that (stated plainly in its
    own docstring: registry integrity binds only when vault write access is itself
    controlled). What it CAN do cheaply is refuse to trust an entry whose recorded_at or
    recorded_by looks nothing like something add/revoke/reactivate would ever write."""

    def _write_raw(self, principals_dict):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"principals": principals_dict}, f)

    def test_missing_recorded_fields_are_tamper_suspect(self):
        self.assertTrue(principals._is_tamper_suspect({"status": "active"}))

    def test_malformed_recorded_at_is_tamper_suspect(self):
        rec = {"status": "active", "recorded_by": "khalil", "recorded_at": "not-a-date"}
        self.assertTrue(principals._is_tamper_suspect(rec))

    def test_blank_recorded_by_is_tamper_suspect(self):
        rec = {"status": "active", "recorded_by": "  ", "recorded_at": "2026-08-30"}
        self.assertTrue(principals._is_tamper_suspect(rec))

    def test_a_normal_add_produced_entry_is_never_tamper_suspect(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        registry, _ = principals.load(self.path)
        self.assertFalse(principals._is_tamper_suspect(registry["principals"]["alice"]))

    def test_hand_edited_active_with_no_recorded_act_fails_closed_as_revoked(self):
        """The self-reactivation surface named in MAJOR 2: hand-editing status back to
        "active" with a blank/missing recorded_by leaves no legitimate recorded act, and
        the consult path (status_of, which bm_vault.py's _policy_deny reads) must fail
        CLOSED rather than trust it."""
        self._write_raw({"mallory": {"kind": "human", "status": "active",
                                     "added_at": "2026-08-30", "added_by": "khalil"}})
        registry, _ = principals.load(self.path)
        self.assertEqual(principals.status_of(registry, "mallory"), "revoked",
                         "a tamper-suspect active entry must be denied like a real revoke")

    def test_hand_edited_revoked_with_no_recorded_act_stays_revoked(self):
        self._write_raw({"mallory": {"kind": "human", "status": "revoked",
                                     "added_at": "2026-08-30", "added_by": "khalil"}})
        registry, _ = principals.load(self.path)
        self.assertEqual(principals.status_of(registry, "mallory"), "revoked")

    def test_list_labels_a_tamper_suspect_entry_and_a_clean_one_is_unlabeled(self):
        principals.cmd_add(self.path, "alice", "human", "khalil", "2026-08-30", True)
        registry, _ = principals.load(self.path)
        registry["principals"]["mallory"] = {"kind": "human", "status": "active",
                                             "added_at": "2026-08-30", "added_by": "khalil"}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(registry, f)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = principals.cmd_list(self.path, None)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        mallory_line = [ln for ln in out.splitlines() if ln.strip().startswith("mallory:")][0]
        alice_line = [ln for ln in out.splitlines() if ln.strip().startswith("alice:")][0]
        self.assertIn("TAMPER-SUSPECT", mallory_line)
        self.assertNotIn("TAMPER-SUSPECT", alice_line)

    def test_CALIBRATION_reverting_the_fail_closed_check_lets_the_hand_edit_through(self):
        """Driven backwards: patch _is_tamper_suspect to always report clean (module-level,
        restored in a finally) and watch the hand-edited "active" mallory entry read as
        active again, reproducing MAJOR 2's self-reactivation surface undetected."""
        self._write_raw({"mallory": {"kind": "human", "status": "active",
                                     "added_at": "2026-08-30", "added_by": "khalil"}})
        registry, _ = principals.load(self.path)
        self.assertEqual(principals.status_of(registry, "mallory"), "revoked",
                         "sanity: the real fix must fail closed before we break it")

        real_check = principals._is_tamper_suspect
        principals._is_tamper_suspect = lambda rec: False
        try:
            self.assertEqual(principals.status_of(registry, "mallory"), "active",
                             "with the detective control neutered, the hand-edited "
                             "active entry must slip through, reproducing MAJOR 2")
        finally:
            principals._is_tamper_suspect = real_check


class TheRegistryFileNeverReachesTheFrontmatterLinter(unittest.TestCase):
    """VB4-04's linter walks only *.md; a JSON registry sitting in 99-System must change
    nothing it reports. Proven by byte-comparing the linter's output before and after the
    registry file exists, not merely by assuming the .endswith(".md") filter."""

    def test_the_registry_json_changes_nothing_lint_sees(self):
        tmp = tempfile.mkdtemp(prefix="bm-principals-lint-")
        try:
            vault = os.path.join(tmp, "vault")
            os.makedirs(os.path.join(vault, "99-System"))
            with open(os.path.join(vault, "a.md"), "w", encoding="utf-8") as f:
                f.write("---\nid: n-aaaabbbbccccdddd\ntype: reference\nstatus: standing\n"
                        "created: 2026-08-30\n---\n\n# a\n")
            before = run(["check", "--vault", vault], tool=LINT)
            registry_path = os.path.join(vault, "99-System", "principals.json")
            rc = principals.cmd_add(registry_path, "canary", "human", "khalil",
                                    "2026-08-30", True)
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(registry_path))
            after = run(["check", "--vault", vault], tool=LINT)
            self.assertEqual(before, after,
                             "the registry file must be invisible to the frontmatter linter")
            self.assertEqual(after[0], 0, after)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


LEDGER_TITLE = "quorvax offboarding ledger"


class OffboardingTrimsRealRecall(unittest.TestCase):
    """VB7-05's own done_check, all three transitions against a real fixture vault and a
    real bm_vault.py recall: active recalls normally, revoked gets zero results with the
    refusal in the audit, reactivation restores it. Own corpus, same reason every sibling
    recall suite keeps one: a shared index would let one suite's fixtures answer another's
    query."""

    QUERY = ["recall", "--query", LEDGER_TITLE, "--limit", "5", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-principals-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "99-System"))
        with open(os.path.join(cls.vault, "quorvax-offboarding-ledger.md"), "w",
                 encoding="utf-8") as f:
            f.write("---\nname: %s\ntype: reference\n---\n\nThe %s, decided.\n"
                    % (LEDGER_TITLE, LEDGER_TITLE))
        cls.registry_path = os.path.join(cls.vault, "99-System", "principals.json")
        cls.env = _fixture_env(cls.tmp, cls.vault)
        cls.audit_path = os.path.join(cls.tmp, ".claude", "bm_vault_audit.jsonl")
        cls.index_code, cls.index_out, cls.index_err = run(
            ["index", "--vault", cls.vault], env=cls.env)
        principals.cmd_add(cls.registry_path, "sam", "human", "khalil", "2026-08-30", True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _audit_rows(self):
        with open(self.audit_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out + self.index_err)

    def test_02_an_active_principal_recalls_normally(self):
        code, out, err = run(self.QUERY + ["--identity", "sam", "--as", "sam"], env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn(LEDGER_TITLE, out)
        self.assertNotIn("REFUSED", out)

    def test_03_revoked_gets_zero_results_and_the_refusal_lands_in_the_audit(self):
        rc = principals.cmd_revoke(self.registry_path, "sam", "khalil", "2026-08-30", True)
        self.assertEqual(rc, 0)
        code, out, err = run(self.QUERY + ["--identity", "sam", "--as", "sam"], env=self.env)
        self.assertEqual(code, 1, out + err)  # everything withheld: honest NO-DATA
        self.assertNotIn(LEDGER_TITLE, out)
        self.assertIn("REFUSED: principal 'sam' is revoked", out)
        sam_rows = [r for r in self._audit_rows() if r["principal"] == "sam"]
        self.assertTrue(sam_rows, "revoked sam must still leave an audit row")
        last = sam_rows[-1]
        self.assertEqual(last["served_ids"], [])
        self.assertIn("revoked", last.get("refused", ""))

    def test_04_reactivation_restores_recall(self):
        rc = principals.cmd_reactivate(self.registry_path, "sam", "khalil", "2026-08-30", True)
        self.assertEqual(rc, 0)
        code, out, err = run(self.QUERY + ["--identity", "sam", "--as", "sam"], env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn(LEDGER_TITLE, out)
        reactivated_rows = [r for r in self._audit_rows() if r["principal"] == "sam"
                            and not r.get("refused")]
        self.assertTrue(reactivated_rows, "a reactivated principal's row must carry no refusal")

    def test_05_an_identity_the_registry_never_heard_of_is_unaffected(self):
        """The opt-in contract: a name absent from the registry behaves exactly as before
        this module existed, revoked sam notwithstanding."""
        code, out, err = run(self.QUERY + ["--identity", "someone-else-entirely"],
                             env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn(LEDGER_TITLE, out)


class CALIBRATION_the_revocation_guard_actually_guards(unittest.TestCase):
    """Neuter bm_vault_principals.status_of in a COPY of the whole tools directory (never
    the real one, per the row's own instruction) so it can never report "revoked", then
    watch a revoked principal slip through recall untouched. Proves the real guard in
    OffboardingTrimsRealRecall above is exercising the fix and is not a tautology.
    __pycache__ is purged in the copy before it ever runs."""

    def test_neutering_status_of_lets_a_revoked_principal_through(self):
        tmp = tempfile.mkdtemp(prefix="bm-principals-cal-")
        try:
            broken_tools = _copy_tools_dir(tmp)
            target = os.path.join(broken_tools, "bm_vault_principals.py")
            with open(target, encoding="utf-8") as f:
                src = f.read()
            broken = src.replace(
                '    return status',
                '    return None  # CALIBRATION: registry consult neutered')
            self.assertNotEqual(broken, src, "seam text not found to disable")
            with open(target, "w", encoding="utf-8") as f:
                f.write(broken)
            shutil.rmtree(os.path.join(broken_tools, "__pycache__"), ignore_errors=True)

            vault = os.path.join(tmp, "vault")
            os.makedirs(os.path.join(vault, "99-System"))
            title = "quorvax neutered offboarding ledger"
            with open(os.path.join(vault, "a.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: %s\ntype: reference\n---\n\nThe %s, decided.\n"
                        % (title, title))
            registry_path = os.path.join(vault, "99-System", "principals.json")
            # The real (unneutered) module writes the registry fine; only the READ side
            # inside the broken bm_vault.py copy is under calibration here.
            principals.cmd_add(registry_path, "pat", "human", "khalil", "2026-08-30", True)
            principals.cmd_revoke(registry_path, "pat", "khalil", "2026-08-30", True)

            env = _fixture_env(tmp, vault)
            broken_vault_tool = os.path.join(broken_tools, "bm_vault.py")
            idx_rc, idx_out, idx_err = run(["index", "--vault", vault],
                                           tool=broken_vault_tool, env=env)
            self.assertEqual(idx_rc, 0, idx_out + idx_err)
            code, out, err = run(["recall", "--query", title, "--limit", "5", "--fast",
                                  "--identity", "pat"], tool=broken_vault_tool, env=env)
            self.assertEqual(code, 0,
                             "with the guard neutered a revoked principal must slip "
                             "through, proving the real test above actually bites: "
                             + out + err)
            self.assertIn(title, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(url, data=None):
    req = urllib.request.Request(url, data=data, method=None)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def start_server(env, port, serve_path=SERVE):
    p = subprocess.Popen([sys.executable, serve_path, "--bind", "127.0.0.1",
                          "--port", str(port)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if p.poll() is not None:
            return p  # refused or crashed; caller inspects
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            s.close()
            return p
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server never came up")


def stop(p):
    if p.poll() is None:
        p.terminate()
        p.wait(timeout=10)
    for pipe in (p.stdout, p.stderr):
        if pipe:
            pipe.close()


WIRE_TITLE = "quorvax wire offboarding ledger"


class WirePathWithholdsPerIdentity(unittest.TestCase):
    """The folded-in finding this row closes: bm_vault_serve.py only forwarded a
    client-declared identity into --as (the audit principal), never into --identity (the
    policy/registry trim), so a revoked caller's recall over the wire never actually
    withheld anything on their behalf even though the audit correctly named them.
    Withholding must now differ per identity over the wire."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-principals-wire-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "99-System"))
        with open(os.path.join(cls.vault, "ledger.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: %s\ntype: reference\n---\n\nThe %s, decided.\n"
                    % (WIRE_TITLE, WIRE_TITLE))
        registry_path = os.path.join(cls.vault, "99-System", "principals.json")
        principals.cmd_add(registry_path, "wire-sam", "human", "khalil", "2026-08-30", True)
        principals.cmd_revoke(registry_path, "wire-sam", "khalil", "2026-08-30", True)
        cls.env = _fixture_env(cls.tmp, cls.vault)
        idx = subprocess.run([sys.executable, TOOL, "index", "--vault", cls.vault],
                             env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if idx.returncode != 0:
            raise RuntimeError("fixture index failed: %s" % idx.stdout.decode())
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _recall(self, identity):
        return http("http://127.0.0.1:%d/recall" % self.port,
                    data=json.dumps({"query": WIRE_TITLE, "identity": identity}).encode())

    def test_revoked_identity_is_withheld_over_the_wire(self):
        status, served = self._recall("wire-sam")
        self.assertEqual(status, 200)
        self.assertEqual(served["rows"], [])
        self.assertTrue(served.get("no_data"))

    def test_an_identity_the_registry_never_heard_of_is_served_normally(self):
        status, served = self._recall("wire-someone-else")
        self.assertEqual(status, 200)
        self.assertTrue(served["rows"], served.get("no_data"))

    def test_withholding_differs_by_identity_proving_identity_actually_forwarded(self):
        _, revoked = self._recall("wire-sam")
        _, allowed = self._recall("wire-someone-else")
        self.assertNotEqual(len(revoked["rows"]), len(allowed["rows"]))


class CALIBRATION_the_wire_fix_actually_matters(unittest.TestCase):
    """Revert bm_vault_serve.py's own fix in a COPY of the whole tools directory (drop the
    --identity forwarding, keep only the pre-fix --as) and watch withholding STOP differing
    by identity over the wire. Proves WirePathWithholdsPerIdentity above is exercising the
    real fix and is not a tautology. __pycache__ purged before the copy runs."""

    def test_reverting_identity_forwarding_makes_a_revoked_caller_served_anyway(self):
        tmp = tempfile.mkdtemp(prefix="bm-principals-wire-cal-")
        try:
            broken_tools = _copy_tools_dir(tmp)
            target = os.path.join(broken_tools, "bm_vault_serve.py")
            with open(target, encoding="utf-8") as f:
                src = f.read()
            broken = src.replace(
                'argv += ["--as", identity, "--identity", identity]',
                'argv += ["--as", identity]  # CALIBRATION: pre-fix, --identity dropped')
            self.assertNotEqual(broken, src, "seam text not found to revert")
            with open(target, "w", encoding="utf-8") as f:
                f.write(broken)
            shutil.rmtree(os.path.join(broken_tools, "__pycache__"), ignore_errors=True)

            vault = os.path.join(tmp, "vault")
            os.makedirs(os.path.join(vault, "99-System"))
            title = "quorvax wire calibration ledger"
            with open(os.path.join(vault, "a.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: %s\ntype: reference\n---\n\nThe %s, decided.\n"
                        % (title, title))
            registry_path = os.path.join(vault, "99-System", "principals.json")
            principals.cmd_add(registry_path, "cal-sam", "human", "khalil", "2026-08-30", True)
            principals.cmd_revoke(registry_path, "cal-sam", "khalil", "2026-08-30", True)

            env = _fixture_env(tmp, vault)
            idx = subprocess.run([sys.executable, os.path.join(broken_tools, "bm_vault.py"),
                                 "index", "--vault", vault],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(idx.returncode, 0, idx.stdout)
            port = free_port()
            proc = start_server(env, port, serve_path=target)
            try:
                _, revoked = http(
                    "http://127.0.0.1:%d/recall" % port,
                    data=json.dumps({"query": title, "identity": "cal-sam"}).encode())
                _, allowed = http(
                    "http://127.0.0.1:%d/recall" % port,
                    data=json.dumps({"query": title, "identity": "someone-else"}).encode())
                self.assertEqual(
                    len(revoked["rows"]), len(allowed["rows"]),
                    "with --identity dropped the revoked caller must be served exactly "
                    "like anyone else, proving the real fix is what makes them differ")
            finally:
                stop(proc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
