#!/usr/bin/env python3
"""Calibration for tools/bm_vault_policy.py, WBS row VB2-01.

The property under test is the row's own sentence: recall trims by querying
identity BEFORE the model sees content. Identity A provably sees a note that
identity B provably does not, B's output carries NEITHER the title NOR the
path of the withheld note (naming what someone may not see is itself a leak),
the count line is correct, and a vault with NO policy behaves byte-for-byte
exactly as before this seam existed.

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")

sys.path.insert(0, HERE)
import bm_vault_policy as pol  # noqa: E402


class TheDecisionContract(unittest.TestCase):
    """decide() directly: no filesystem, no subprocess."""

    def test_no_matching_rule_falls_to_the_default_allow(self):
        p = {"rules": [{"identity": "bob", "path": "50-Private/*", "action": "deny"}]}
        self.assertEqual(pol.decide(p, "alice", "50-Private/x.md"), "allow")

    def test_a_matching_deny_denies(self):
        p = {"rules": [{"identity": "bob", "path": "50-Private/*", "action": "deny"}]}
        self.assertEqual(pol.decide(p, "bob", "50-Private/x.md"), "deny")

    def test_deny_wins_on_tie(self):
        """One matching deny beats any number of matching allows, whatever
        their order in the rule list."""
        p = {"rules": [
            {"identity": "bob", "path": "*", "action": "allow"},
            {"identity": "bob", "path": "50-Private/*", "action": "deny"},
            {"identity": "*", "path": "50-Private/*", "action": "allow"}]}
        self.assertEqual(pol.decide(p, "bob", "50-Private/x.md"), "deny")
        p_reversed = {"rules": list(reversed(p["rules"]))}
        self.assertEqual(pol.decide(p_reversed, "bob", "50-Private/x.md"), "deny")

    def test_default_deny_denies_when_no_rule_matches(self):
        p = {"default": "deny",
             "rules": [{"identity": "alice", "path": "*", "action": "allow"}]}
        self.assertEqual(pol.decide(p, "alice", "x.md"), "allow")
        self.assertEqual(pol.decide(p, "bob", "x.md"), "deny")

    def test_anonymous_matches_only_wildcard_rules(self):
        p = {"rules": [{"identity": "alice", "path": "*", "action": "deny"}]}
        self.assertEqual(pol.decide(p, None, "x.md"), "allow")
        p2 = {"rules": [{"identity": "*", "path": "*", "action": "deny"}]}
        self.assertEqual(pol.decide(p2, None, "x.md"), "deny")

    def test_anonymous_is_denied_under_default_deny(self):
        self.assertEqual(pol.decide({"default": "deny", "rules": []}, None, "x.md"),
                         "deny")

    def test_group_membership_matches(self):
        p = {"groups": {"team": ["alice"]},
             "rules": [{"group": "team", "path": "50-Private/*", "action": "deny"}]}
        self.assertEqual(pol.decide(p, "alice", "50-Private/x.md"), "deny")
        self.assertEqual(pol.decide(p, "bob", "50-Private/x.md"), "allow")


class TheCheckNamesEveryProblem(unittest.TestCase):
    def test_a_clean_policy_validates_clean(self):
        p = {"default": "deny", "groups": {"team": ["a"]},
             "rules": [{"group": "team", "path": "*", "action": "allow"}]}
        self.assertEqual(pol.validate(p), [])

    def test_unknown_keys_bad_globs_and_unreachable_rules_are_named(self):
        p = {"defualt": "deny",
             "rules": [
                 {"identity": "a", "path": "x/*", "action": "deny", "why": "no"},
                 {"identity": "a", "path": "", "action": "allow"},
                 {"identity": "a", "group": "g", "path": "y", "action": "read"},
                 {"identity": "a", "path": "x/*", "action": "deny"}]}
        problems = "\n".join(pol.validate(p))
        self.assertIn("unknown top-level key 'defualt'", problems)
        self.assertIn("unknown key 'why'", problems)
        self.assertIn("non-empty glob", problems)
        self.assertIn("exactly one of identity or group", problems)
        self.assertIn("action must be allow, deny or require_approval", problems)
        self.assertIn("unreachable", problems)

    def test_an_undeclared_group_is_named(self):
        p = {"rules": [{"group": "ghost", "path": "*", "action": "deny"}]}
        self.assertIn("group 'ghost' not declared", "\n".join(pol.validate(p)))

    def test_an_absent_policy_is_no_data(self):
        self.assertEqual(pol.cmd_check("/nowhere/at/all.json"), 2)

    def test_load_refuses_a_broken_file_audibly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        try:
            policy, problems = pol.load(path)
            self.assertIsNone(policy)
            self.assertTrue(problems, "a broken policy must be a named problem")
        finally:
            os.unlink(path)

    def test_load_of_an_absent_file_is_the_opt_in_state(self):
        self.assertEqual(pol.load("/nowhere/at/all.json"), (None, []))


def run(argv, env, tool=TOOL):
    """tool defaults to the real bm_vault.py; VB3-04's fail-closed calibration passes
    a COPY's own bm_vault.py instead (see _copy_tools_dir below), same shape as
    test_bm_vault_principals.py's own run() helper."""
    p = subprocess.run([sys.executable, tool] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8", "replace"), \
        p.stderr.decode("utf-8", "replace")


def _copy_tools_dir(tmp):
    """A full copy of tools/ (minus tests and __pycache__) so a calibration can edit
    or delete ONE sibling module's source and have bm_vault.py's own by-path dynamic
    loaders (which always resolve against THEIR OWN directory) pick that up, every
    other dependency present unmodified. Never touches the real tools/ directory.
    Mirrors test_bm_vault_principals.py's own helper of the same name and shape."""
    dest = os.path.join(tmp, "tools")
    shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns("test_*.py", "__pycache__"))
    return dest


def _stable(text):
    # masks per-run answer identity, never content
    return "\n".join(line for line in text.split("\n")
                     if not line.startswith("event: ")
                     and not line.startswith("derived-from-vault: "))


PRIVATE_TITLE = "zorbly private ledger"
PRIVATE_STEM = "zorbly-private-ledger"


class IdentityTrimsRealRecall(unittest.TestCase):
    """VB2-01's done_check: with a fixture policy, identity A sees the note
    and identity B provably does not; B's output names neither the title nor
    the path of the withheld note; the count line is correct; and a vault
    with NO policy behaves byte-for-byte as before. Own corpus, same reason
    every sibling recall suite has its own: a shared index would let one
    suite's fixtures answer another's query."""

    QUERY = ["recall", "--query", "zorbly payroll ledger", "--limit", "5", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-policy-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "50-Private"))
        os.makedirs(os.path.join(cls.vault, "99-System"))

        def note(name, body):
            return "---\nname: %s\ntype: reference\n---\n\n%s\n" % (name, body)

        with open(os.path.join(cls.vault, "zorbly-public-ledger.md"), "w") as f:
            f.write(note("zorbly public ledger",
                         "The zorbly payroll ledger's public monthly summary."))
        with open(os.path.join(cls.vault, "50-Private", PRIVATE_STEM + ".md"),
                  "w") as f:
            f.write(note(PRIVATE_TITLE,
                         "The zorbly payroll ledger's private per-person figures."))
        cls.policy_path = os.path.join(cls.vault, "99-System", "access-policy.json")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env.pop("BM_IDENTITY", None)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out, cls.index_err = run(
            ["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _write_policy(self, policy):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(policy, fh)

    def _remove_policy(self):
        if os.path.exists(self.policy_path):
            os.unlink(self.policy_path)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out + self.index_err)

    def test_02_no_policy_behaves_exactly_as_before(self):
        """Byte-compare: with no policy file, a recall carrying an identity is
        byte-identical to a recall carrying none, and both name the private
        note. Adoption is opt-in; nothing breaks."""
        self._remove_policy()
        code_a, out_a, _ = run(self.QUERY + ["--identity", "alice"], self.env)
        code_b, out_b, _ = run(self.QUERY, self.env)
        self.assertEqual(code_a, 0, out_a)
        self.assertEqual(code_b, 0, out_b)
        self.assertEqual(_stable(out_a), _stable(out_b))
        self.assertIn(PRIVATE_TITLE, out_a)

    def test_03_a_sees_and_b_provably_does_not(self):
        """The row's done_check, both halves in one place."""
        self._write_policy({"rules": [
            {"identity": "bob", "path": "50-Private/*", "action": "deny"}]})
        try:
            code_a, out_a, err_a = run(self.QUERY + ["--identity", "alice"], self.env)
            code_b, out_b, err_b = run(self.QUERY + ["--identity", "bob"], self.env)
        finally:
            self._remove_policy()
        self.assertEqual(code_a, 0, out_a + err_a)
        self.assertEqual(code_b, 0, out_b + err_b)
        self.assertIn(PRIVATE_TITLE, out_a)
        self.assertNotIn("withheld by access policy", out_a)
        # B: neither the title nor the path, anywhere in the output. Naming
        # what someone may not see is itself a leak.
        self.assertNotIn(PRIVATE_TITLE, out_b)
        self.assertNotIn(PRIVATE_STEM, out_b)
        self.assertNotIn("50-Private", out_b)
        self.assertIn("1 note(s) withheld by access policy", out_b)
        # The public note stays served to both.
        self.assertIn("zorbly public ledger", out_a)
        self.assertIn("zorbly public ledger", out_b)

    def test_04_bm_identity_env_is_the_fallback(self):
        self._write_policy({"rules": [
            {"identity": "bob", "path": "50-Private/*", "action": "deny"}]})
        env = dict(self.env)
        env["BM_IDENTITY"] = "bob"
        try:
            code, out, err = run(self.QUERY, env)
        finally:
            self._remove_policy()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn(PRIVATE_TITLE, out)
        self.assertIn("1 note(s) withheld by access policy", out)

    def test_05_default_deny_denies_the_anonymous_caller(self):
        self._write_policy({"default": "deny", "rules": [
            {"identity": "alice", "path": "*", "action": "allow"}]})
        try:
            code, out, err = run(self.QUERY, self.env)
        finally:
            self._remove_policy()
        # Everything matched was withheld, so recall honestly reports NO-DATA
        # (exit 1), plus the count: an anonymous caller under default deny
        # learns that notes exist, never which ones.
        self.assertEqual(code, 1, out + err)
        self.assertNotIn(PRIVATE_TITLE, out)
        self.assertNotIn("zorbly public ledger", out)
        self.assertIn("2 note(s) withheld by access policy", out)

    def test_06_a_broken_policy_fails_closed_not_open(self):
        with open(self.policy_path, "w") as fh:
            fh.write("{not json")
        try:
            code, out, _ = run(self.QUERY + ["--identity", "alice"], self.env)
        finally:
            self._remove_policy()
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA access policy", out)
        self.assertNotIn(PRIVATE_TITLE, out)

    def test_07_scratch_policy_by_flag_never_touches_the_vault(self):
        """--policy points recall at a policy OUTSIDE the vault, the same
        read-only route the real-vault proof uses."""
        scratch = os.path.join(self.tmp, "scratch-policy.json")
        with open(scratch, "w", encoding="utf-8") as fh:
            json.dump({"rules": [
                {"identity": "bob", "path": "50-Private/*", "action": "deny"}]}, fh)
        code, out, err = run(self.QUERY + ["--identity", "bob", "--policy", scratch],
                             self.env)
        self.assertEqual(code, 0, out + err)
        self.assertNotIn(PRIVATE_TITLE, out)
        self.assertIn("1 note(s) withheld by access policy", out)


class TheRequireApprovalContract(unittest.TestCase):
    """decide() and decide_dual() directly: require_approval sits between allow and
    deny in precedence, and the dual-principal intersection is most-restrictive-wins."""

    def test_require_approval_verdict_from_a_fixture(self):
        p = {"rules": [{"identity": "*", "path": "40-Legal/*",
                        "action": "require_approval"}]}
        self.assertEqual(pol.decide(p, "alice", "40-Legal/x.md"), "require_approval")

    def test_require_approval_beats_allow_but_loses_to_deny(self):
        p = {"rules": [
            {"identity": "a", "path": "*", "action": "allow"},
            {"identity": "a", "path": "x/*", "action": "require_approval"}]}
        self.assertEqual(pol.decide(p, "a", "x/y.md"), "require_approval")
        p2 = dict(p, rules=p["rules"] + [{"identity": "a", "path": "x/*", "action": "deny"}])
        self.assertEqual(pol.decide(p2, "a", "x/y.md"), "deny")

    def test_require_approval_is_never_a_valid_default(self):
        problems = pol.validate({"default": "require_approval", "rules": []})
        self.assertIn("default must be allow or deny, got 'require_approval'", problems)

    def test_decide_dual_with_no_agent_is_decide_alone(self):
        p = {"rules": [{"identity": "bob", "path": "*", "action": "deny"}]}
        self.assertEqual(pol.decide_dual(p, "bob", None, None, "x.md"),
                         pol.decide(p, "bob", "x.md"))

    def test_decide_dual_intersection_is_most_restrictive_of_both(self):
        p = {"rules": [{"identity": "bob-agent", "path": "*", "action": "deny"}]}
        # human alone: allow (no rule against "bob"). agent alone: deny. Intersection: deny.
        self.assertEqual(pol.decide_dual(p, "bob", "bob-agent", None, "x.md"), "deny")
        # widen: no rule denies the agent anymore -> intersection reverts to the
        # human's own (allow), proving the AGENT'S rule was what was withholding it.
        self.assertEqual(pol.decide_dual({"rules": []}, "bob", "bob-agent", None, "x.md"),
                         "allow")


class DualPrincipalIntersectionRealRecall(unittest.TestCase):
    """VB3-04's own done_check, driven backwards through a real recall: an agent
    scoped narrower than its human cannot retrieve the human's broader set; widening
    ONLY the agent's own rule (the human's access never changes) makes the
    previously-withheld note reappear, proving the intersection -- not the human's
    own access -- was the thing withholding it."""

    QUERY = ["recall", "--query", "quixomel finance memo", "--limit", "5", "--fast"]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-dual-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.vault, "50-Private"))
        os.makedirs(os.path.join(cls.vault, "99-System"))
        with open(os.path.join(cls.vault, "50-Private", "quixomel-finance-memo.md"),
                  "w") as f:
            f.write("---\nname: quixomel finance memo\ntype: reference\n---\n\n"
                    "The quixomel finance memo's private figures.\n")
        cls.policy_path = os.path.join(cls.vault, "99-System", "access-policy.json")
        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp
        cls.env["BROTHERMODE_ROOT"] = cls.tmp
        cls.env["BM_VAULT_ROOT"] = cls.vault
        cls.env["BM_FRESHNESS_ROOTS"] = cls.tmp
        cls.env["BM_FRESHNESS_STATE"] = os.path.join(cls.tmp, "freshness_state.sqlite3")
        cls.env.pop("BM_IDENTITY", None)
        cls.env.pop("BM_AGENT_IDENTITY", None)
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        cls.index_code, cls.index_out, cls.index_err = run(
            ["index", "--vault", cls.vault], cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _write_policy(self, policy):
        with open(self.policy_path, "w", encoding="utf-8") as fh:
            json.dump(policy, fh)

    def _remove_policy(self):
        if os.path.exists(self.policy_path):
            os.unlink(self.policy_path)

    def test_01_the_corpus_indexed(self):
        self.assertEqual(self.index_code, 0, self.index_out + self.index_err)

    def test_02_narrow_agent_withholds_then_widening_reveals(self):
        self._write_policy({"rules": [
            {"identity": "bob-agent", "path": "50-Private/*", "action": "deny"}]})
        try:
            code_narrow, out_narrow, err_narrow = run(
                self.QUERY + ["--identity", "bob", "--agent-identity", "bob-agent"],
                self.env)
        finally:
            self._remove_policy()
        self.assertEqual(code_narrow, 1, out_narrow + err_narrow)
        self.assertNotIn("quixomel finance memo", out_narrow)
        self.assertIn("1 note(s) withheld by access policy", out_narrow)

        # Widen: same human, same agent name, only the AGENT's own rule now allows.
        self._write_policy({"rules": [
            {"identity": "bob-agent", "path": "50-Private/*", "action": "allow"}]})
        try:
            code_wide, out_wide, err_wide = run(
                self.QUERY + ["--identity", "bob", "--agent-identity", "bob-agent"],
                self.env)
        finally:
            self._remove_policy()
        self.assertEqual(code_wide, 0, out_wide + err_wide)
        self.assertIn("quixomel finance memo", out_wide)

        # The human alone was never what gated this: unaffected throughout.
        code_human, out_human, err_human = run(self.QUERY + ["--identity", "bob"],
                                                self.env)
        self.assertEqual(code_human, 0, out_human + err_human)
        self.assertIn("quixomel finance memo", out_human)


class RequireApprovalRealRecall(unittest.TestCase):
    """The REQUIRE_APPROVAL verdict wired through a real recall: withheld exactly
    like DENY until the note carries a clean canonical promotion -- the existing
    bm_vault_promotions/pane approval ceremony, never a second approval store."""

    QUERY = ["recall", "--query", "vexbrite legal opinion", "--limit", "5", "--fast"]

    # Each test builds its OWN fresh vault and indexes it exactly once (never edits an
    # already-indexed note's content and reindexes): a real, pre-existing FTS5
    # external-content bug in bm_vault.py's _upsert_note corrupts the index on that
    # exact sequence (confirmed against a pristine checkout, unrelated to VB3-04,
    # flagged separately). One-shot-index-per-test sidesteps it entirely rather than
    # relying on a fix this row does not own.

    def _build(self, tmp, approved):
        vault = os.path.join(tmp, "vault")
        os.makedirs(os.path.join(vault, "40-Legal"))
        os.makedirs(os.path.join(vault, "99-System"))
        with open(os.path.join(vault, "99-System", "access-policy.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"rules": [
                {"identity": "*", "path": "40-Legal/*", "action": "require_approval"}]}, fh)
        promo = ("promotion: canonical\npromoted_by: khalil\npromoted_at: 2026-08-30\n"
                 if approved else "")
        with open(os.path.join(vault, "40-Legal", "vexbrite-legal-opinion.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\nname: vexbrite legal opinion\ntype: reference\n%s---\n\n"
                    "The vexbrite legal opinion's full text.\n" % promo)
        env = dict(os.environ)
        env["HOME"] = tmp
        env["BROTHERMODE_ROOT"] = tmp
        env["BM_VAULT_ROOT"] = vault
        env["BM_FRESHNESS_ROOTS"] = tmp
        env["BM_FRESHNESS_STATE"] = os.path.join(tmp, "freshness_state.sqlite3")
        env.pop("BM_IDENTITY", None)
        os.makedirs(os.path.join(tmp, ".claude"))
        code, out, err = run(["index", "--vault", vault], env)
        self.assertEqual(code, 0, out + err)
        return vault, env

    def test_01_unapproved_is_withheld_like_deny(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-approval-no-")
        try:
            _vault, env = self._build(tmp, approved=False)
            code, out, err = run(self.QUERY + ["--identity", "alice"], env)
            self.assertEqual(code, 1, out + err)
            self.assertNotIn("vexbrite legal opinion", out)
            self.assertIn("1 note(s) withheld by access policy", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_02_a_clean_canonical_promotion_serves_it(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-approval-yes-")
        try:
            _vault, env = self._build(tmp, approved=True)
            code, out, err = run(self.QUERY + ["--identity", "alice"], env)
            self.assertEqual(code, 0, out + err)
            self.assertIn("vexbrite legal opinion", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EnterpriseFailClosed(unittest.TestCase):
    """VB3-04's fail-closed contract, driven backwards on a COPY of the whole tools
    directory (never the real one, per the row's own instruction): in enterprise mode
    a policy module that cannot be imported, or whose decision crashes, must never be
    read as "nothing is restricted". Restricted-flagged content stays withheld,
    unrestricted content still serves, and the failure lands in the access audit.
    Off (single-machine mode, no BROTHERMODE_ENTERPRISE), today's fail-open-with-a-
    warning behavior is unchanged."""

    RESTRICTED_TITLE = "zenthrax breach report"
    OPEN_TITLE = "zenthrax public advisory"

    def _fixture(self, tmp):
        vault = os.path.join(tmp, "vault")
        os.makedirs(vault)
        with open(os.path.join(vault, "breach.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: %s\ntype: reference\nrestricted: true\n---\n\n"
                    "The %s, in full.\n" % (self.RESTRICTED_TITLE, self.RESTRICTED_TITLE))
        with open(os.path.join(vault, "advisory.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: %s\ntype: reference\n---\n\nThe %s, in full.\n"
                    % (self.OPEN_TITLE, self.OPEN_TITLE))
        env = dict(os.environ)
        env["HOME"] = tmp
        env["BROTHERMODE_ROOT"] = tmp
        env["BM_VAULT_ROOT"] = vault
        env["BM_FRESHNESS_ROOTS"] = tmp
        env["BM_FRESHNESS_STATE"] = os.path.join(tmp, "freshness_state.sqlite3")
        env.pop("BM_IDENTITY", None)
        os.makedirs(os.path.join(tmp, ".claude"))
        return vault, env

    def test_01_module_missing_in_enterprise_mode_withholds_restricted_only(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-failclosed-missing-")
        try:
            broken_tools = _copy_tools_dir(tmp)
            os.unlink(os.path.join(broken_tools, "bm_vault_policy.py"))
            shutil.rmtree(os.path.join(broken_tools, "__pycache__"), ignore_errors=True)
            vault, env = self._fixture(tmp)
            broken_tool = os.path.join(broken_tools, "bm_vault.py")
            code, out, err = run(["index", "--vault", vault], env, tool=broken_tool)
            self.assertEqual(code, 0, out + err)

            env_ent = dict(env)
            env_ent["BROTHERMODE_ENTERPRISE"] = "1"
            code, out, err = run(["recall", "--query", "zenthrax", "--limit", "5",
                                  "--fast"], env_ent, tool=broken_tool)
            self.assertEqual(code, 0, out + err)
            self.assertIn(self.OPEN_TITLE, out)
            self.assertNotIn(self.RESTRICTED_TITLE, out)
            self.assertIn("NOTE:", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_02_single_machine_mode_is_unchanged_when_the_module_is_missing(self):
        """Off (no BROTHERMODE_ENTERPRISE): today's behavior -- a missing policy
        module degrades to "not trimmed", both notes serve."""
        tmp = tempfile.mkdtemp(prefix="bm-vault-failclosed-single-")
        try:
            broken_tools = _copy_tools_dir(tmp)
            os.unlink(os.path.join(broken_tools, "bm_vault_policy.py"))
            shutil.rmtree(os.path.join(broken_tools, "__pycache__"), ignore_errors=True)
            vault, env = self._fixture(tmp)
            broken_tool = os.path.join(broken_tools, "bm_vault.py")
            code, out, err = run(["index", "--vault", vault], env, tool=broken_tool)
            self.assertEqual(code, 0, out + err)

            code, out, err = run(["recall", "--query", "zenthrax", "--limit", "5",
                                  "--fast"], env, tool=broken_tool)
            self.assertEqual(code, 0, out + err)
            self.assertIn(self.OPEN_TITLE, out)
            self.assertIn(self.RESTRICTED_TITLE, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_03_crashed_decide_in_enterprise_mode_withholds_restricted_and_is_audited(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-failclosed-crash-")
        try:
            broken_tools = _copy_tools_dir(tmp)
            target = os.path.join(broken_tools, "bm_vault_policy.py")
            with open(target, encoding="utf-8") as f:
                src = f.read()
            marker = ("del purpose  # accepted for the caller's audit record only; "
                      "see docstring")
            broken = src.replace(
                marker, 'raise RuntimeError("CALIBRATION: decide_dual crashed")')
            self.assertNotEqual(broken, src, "seam text not found to disable")
            with open(target, "w", encoding="utf-8") as f:
                f.write(broken)
            shutil.rmtree(os.path.join(broken_tools, "__pycache__"), ignore_errors=True)

            vault, env = self._fixture(tmp)
            os.makedirs(os.path.join(vault, "99-System"))
            with open(os.path.join(vault, "99-System", "access-policy.json"),
                      "w", encoding="utf-8") as fh:
                json.dump({"rules": [{"identity": "*", "path": "*", "action": "allow"}]},
                         fh)
            broken_tool = os.path.join(broken_tools, "bm_vault.py")
            code, out, err = run(["index", "--vault", vault], env, tool=broken_tool)
            self.assertEqual(code, 0, out + err)

            env_ent = dict(env)
            env_ent["BROTHERMODE_ENTERPRISE"] = "1"
            code, out, err = run(["recall", "--query", "zenthrax", "--limit", "5",
                                  "--fast", "--as", "carol"], env_ent, tool=broken_tool)
            self.assertEqual(code, 0, out + err)
            self.assertIn(self.OPEN_TITLE, out)
            self.assertNotIn(self.RESTRICTED_TITLE, out)

            audit_path = os.path.join(tmp, ".claude", "bm_vault_audit.jsonl")
            with open(audit_path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(rows), 1, rows)
            self.assertIn("degraded", rows[0], rows[0])
            self.assertEqual(rows[0]["principal"], "carol")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
