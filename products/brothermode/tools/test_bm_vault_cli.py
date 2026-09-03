#!/usr/bin/env python3
"""Tests for bm_vault_cli, the vault's one front door.

Each routed verb gets one pass and one fail case, on a small fixture vault
(never the real one), proving the child's own exit code survives the router
unchanged. doctor gets its own fixture: an isolated HOME (moves
~/.claude/bm_vault.json and the retrieval index, the same technique
test_bm_vault.py already uses) so its "vault path resolution" and "gate
state" sections read the fixture, never the real machine.

Run: python3 tools/test_bm_vault_cli.py      (unittest output, exit 0 or 1)
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "bm_vault_cli.py")
BM_VAULT = os.path.join(HERE, "bm_vault.py")
CATALOG = os.path.join(HERE, "bm_vault_catalog.py")

# Loaded by path, the same way test_bm_vault.py loads bm_store.py, so the signal-exit
# test below can call _run() directly instead of going through main()'s VERBS dict
# (the stub fixture is not, and must never become, a real routed verb).
_cli_spec = importlib.util.spec_from_file_location("bm_vault_cli", CLI)
bm_vault_cli = importlib.util.module_from_spec(_cli_spec)
_cli_spec.loader.exec_module(bm_vault_cli)


def run(argv, env=None):
    p = subprocess.run([sys.executable, CLI] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    return p.returncode, p.stdout + p.stderr


def _note(id_="n-0123456789abcdef", type_="reference", status="standing",
          created="2026-08-01", body="\n# a clean note\n\nordinary content.\n"):
    return ("---\nid: %s\ntype: %s\nstatus: %s\ncreated: %s\n---\n%s"
            % (id_, type_, status, created, body))


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _clean_vault(root):
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "one.md"), _note())


def _broken_link_vault(root):
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "one.md"), _note(body="\nSee [[does-not-exist]].\n"))


def _missing_status_vault(root):
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "one.md"), _note().replace("status: standing\n", ""))


def _git(vault, *args):
    return subprocess.run(["git", "-C", vault] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def _head(vault):
    return _git(vault, "rev-parse", "HEAD").stdout.strip()


def _init_git_vault(root):
    """A real, committed git repo (never the real Kay Vault) with one clean note
    under 10-Projects/demo/, so bake and the bm_vault_graph.py gate both have a
    project to work on. The commit verb under test runs plain git subprocesses
    against this fixture's own working directory, never a worktree or the repo
    this test file itself lives in."""
    os.makedirs(os.path.join(root, "10-Projects", "demo"), exist_ok=True)
    _write(os.path.join(root, "10-Projects", "demo", "one.md"), _note())
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")


def _scratch_env(home):
    """HOME redirected moves ~/.claude/bm_vault.json and the retrieval index
    (~/.claude/bm_vault_index.sqlite3), so a subprocess run with this env can
    never touch the real machine's config or index. Same technique
    test_bm_vault.py's setUpClass already relies on."""
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("BM_VAULT_ROOT", None)
    env.pop("BROTHERMODE_VAULT", None)
    return env


class CheckVerb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-check-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_pass_exits_zero(self):
        vault = os.path.join(self.tmp, "v")
        _clean_vault(vault)
        code, out = run(["check", "--vault", vault])
        self.assertEqual(code, 0, out)

    def test_check_fail_exits_with_childs_own_code(self):
        vault = os.path.join(self.tmp, "v")
        _broken_link_vault(vault)
        code, out = run(["check", "--vault", vault])
        self.assertEqual(code, 2, out)
        self.assertIn("does-not-exist", out)


class MeasureVerb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-measure-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_measure_pass_exits_zero(self):
        vault = os.path.join(self.tmp, "v")
        _clean_vault(vault)
        code, out = run(["measure", "--vault", vault])
        self.assertEqual(code, 0, out)
        self.assertIn("notes: 1", out)

    def test_measure_no_data_exits_with_childs_own_code(self):
        vault = os.path.join(self.tmp, "empty")
        os.makedirs(vault)
        code, out = run(["measure", "--vault", vault])
        self.assertEqual(code, 3, out)


class LintVerb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-lint-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lint_check_pass_exits_zero(self):
        vault = os.path.join(self.tmp, "v")
        _clean_vault(vault)
        code, out = run(["lint", "check", "--vault", vault])
        self.assertEqual(code, 0, out)

    def test_lint_check_fail_exits_with_childs_own_code(self):
        vault = os.path.join(self.tmp, "v")
        _missing_status_vault(vault)
        code, out = run(["lint", "check", "--vault", vault])
        self.assertEqual(code, 1, out)
        self.assertIn("status", out)


class CensusVerb(unittest.TestCase):
    """bm_vault_retention.py census reads the retrieval index, never the
    vault's markdown directly, so each case gets its own scratch HOME."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-census-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_census_no_index_exits_with_childs_own_code(self):
        home = os.path.join(self.tmp, "home-nodata")
        os.makedirs(os.path.join(home, ".claude"))
        vault = os.path.join(self.tmp, "v")
        _clean_vault(vault)
        code, out = run(["census", "--vault", vault], env=_scratch_env(home))
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_census_pass_after_index_exits_zero(self):
        home = os.path.join(self.tmp, "home-clean")
        os.makedirs(os.path.join(home, ".claude"))
        vault = os.path.join(self.tmp, "v2")
        _clean_vault(vault)
        env = _scratch_env(home)
        p = subprocess.run([sys.executable, BM_VAULT, "index", "--vault", vault],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        code, out = run(["census", "--vault", vault], env=env)
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)


class CurateVerb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-curate-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_curate_find_pass_exits_zero(self):
        vault = os.path.join(self.tmp, "v")
        _clean_vault(vault)
        queue = os.path.join(self.tmp, "queue.json")
        code, out = run(["curate", "find", "--vault", vault, "--queue", queue])
        self.assertEqual(code, 0, out)

    def test_curate_accept_without_by_exits_with_childs_own_code(self):
        vault = os.path.join(self.tmp, "v")
        _clean_vault(vault)
        queue = os.path.join(self.tmp, "queue.json")
        code, out = run(["curate", "find", "--vault", vault, "--queue", queue])
        self.assertEqual(code, 0, out)
        code, out = run(["curate", "accept", "--vault", vault, "--queue", queue,
                         "--pair", "a,b", "--edge", "relates"])
        self.assertEqual(code, 2, out)
        self.assertIn("--by", out)


class RecallVerb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-recall-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        self.vault = os.path.join(self.tmp, "v")
        _clean_vault(self.vault)
        self.env = _scratch_env(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recall_without_query_exits_with_childs_own_code(self):
        code, out = run(["recall"], env=self.env)
        self.assertEqual(code, 2, out)
        self.assertIn("--query", out)

    def test_recall_positional_query_routes_and_exits_zero(self):
        p = subprocess.run([sys.executable, BM_VAULT, "index", "--vault", self.vault],
                           env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        code, out = run(["recall", "clean note", "--fast"], env=self.env)
        self.assertEqual(code, 0, out)


class CommitVerb(unittest.TestCase):
    """Drives the gate refusal backwards: every fail case must leave HEAD, and in
    the message-gate case the whole working tree, exactly as it was. Never the
    real Kay Vault, always a throwaway git repo under a temp dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-commit-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_vault_resolved_exits_two(self):
        """MINOR: the NO-DATA branches exit 2 (the estate's NO-DATA class),
        not a bare 1, consistent with census's own NO-DATA exit above."""
        home = os.path.join(self.tmp, "home-empty")
        os.makedirs(home)
        code, out = run(["commit", "-m", "no vault"], env=_scratch_env(home))
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_vault_not_a_directory_exits_two(self):
        code, out = run(["commit", "--vault", os.path.join(self.tmp, "no-such-dir"),
                         "-m", "no such vault"])
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_dirty_catalog_commits_clean(self):
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        _write(os.path.join(vault, "10-Projects", "demo", "Catalog.md"),
               "stale garbage, not a real catalog\n")
        before = _head(vault)
        code, out = run(["commit", "--vault", vault, "-m", "bake and land"])
        self.assertEqual(code, 0, out)
        after = _head(vault)
        self.assertNotEqual(before, after, out)
        check = subprocess.run([sys.executable, CATALOG, "check", "--vault", vault],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("OK", check.stdout)

    def test_new_note_with_broken_link_refuses_naming_the_fix_nothing_committed(self):
        """VB10-01: the tiered gate only refuses a finding on a note NEW in this
        commit's staged set, never one already sitting at HEAD untouched, so this
        vault commits a clean note first and then adds the broken-link note as a
        second, staged-but-uncommitted file, landing it in the diff the gate scopes
        to. See test_bm_vault_tiers.py for the pre-existing-note WARN downgrade and
        the --quarantine divert this same change adds."""
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        _write(os.path.join(vault, "10-Projects", "demo", "two.md"),
               _note(id_="n-fedcba9876543210", body="\nSee [[does-not-exist]].\n"))
        before = _head(vault)
        code, out = run(["commit", "--vault", vault, "-m", "should refuse"])
        self.assertEqual(code, 1, out)
        self.assertIn("does-not-exist", out)
        self.assertIn("Nothing committed", out)
        after = _head(vault)
        self.assertEqual(before, after, out)
        # MAJOR 1: the bake that ran before the gate wrote baked catalog
        # files into the working tree, and the refusal must say so plainly
        # and name the command to see them, rather than implying the tree
        # is back to how it was.
        self.assertIn("status --short", out)
        status = _git(vault, "status", "--porcelain").stdout
        self.assertNotEqual(status.strip(), "", "bake residue should remain untouched")

    def test_commit_subprocess_failure_leaves_index_unstaged(self):
        """MAJOR 2: a `git commit` subprocess failure (gate already passed,
        so add + check both succeeded and the index is staged) must not
        leave the index staged, contradicting the module's own docstring. A
        pre-commit hook that always refuses is the simplest way to force
        that specific step to fail without touching bake, add, or the
        graph gate."""
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        hook_path = os.path.join(vault, ".git", "hooks", "pre-commit")
        _write(hook_path, "#!/bin/sh\nexit 1\n")
        os.chmod(hook_path, 0o755)
        before = _head(vault)
        code, out = run(["commit", "--vault", vault, "-m", "should fail at commit"])
        self.assertEqual(code, 1, out)
        self.assertIn("git commit failed", out)
        after = _head(vault)
        self.assertEqual(before, after, out)
        staged = _git(vault, "diff", "--cached", "--name-only").stdout
        self.assertEqual(staged.strip(), "", "index must be unstaged after a commit failure: " + out)

    def test_secret_shaped_file_never_staged_or_committed(self):
        """MAJOR 3: `commit`'s `git add -A` must apply the same exclusions
        bm_autosave.py's own snapshot staging uses, not a bare `git add -A`
        that trusts the vault's .gitignore alone."""
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        _write(os.path.join(vault, ".env"), "SECRET=abc123\n")
        code, out = run(["commit", "--vault", vault, "-m", "add a secret-shaped file"])
        self.assertEqual(code, 0, out)
        committed = _git(vault, "show", "--stat", "HEAD").stdout
        self.assertNotIn(".env", committed, out)
        status = _git(vault, "status", "--porcelain").stdout
        self.assertIn(".env", status, "the secret-shaped file must stay untracked: " + status)

    def test_dry_run_writes_nothing(self):
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        _write(os.path.join(vault, "10-Projects", "demo", "Catalog.md"),
               "stale garbage, not a real catalog\n")
        before_status = _git(vault, "status", "--porcelain").stdout
        before_head = _head(vault)
        code, out = run(["commit", "--vault", vault, "-m", "dry run", "--dry-run"])
        self.assertEqual(code, 0, out)
        self.assertIn("dry run", out)
        after_status = _git(vault, "status", "--porcelain").stdout
        after_head = _head(vault)
        self.assertEqual(before_status, after_status, out)
        self.assertEqual(before_head, after_head)

    def test_message_with_em_dash_refuses(self):
        vault = os.path.join(self.tmp, "v")
        _init_git_vault(vault)
        before_head = _head(vault)
        # Written as an escape on purpose, not the literal glyph, the same
        # convention scripts/bm_commit_msg_hook.py itself uses: this repo's own
        # no-dash scan runs over every file, and a literal glyph here would fail
        # that scan on the one test whose job is planting one.
        bad_message = "bad message " + chr(0x2014) + " with a dash"
        code, out = run(["commit", "--vault", vault, "-m", bad_message])
        self.assertEqual(code, 1, out)
        self.assertIn("em dash", out)
        after_head = _head(vault)
        self.assertEqual(before_head, after_head, out)
        # The message gate runs before bake/add: a bad message leaves the
        # working tree exactly as it was, nothing baked and nothing staged.
        status = _git(vault, "status", "--porcelain").stdout
        self.assertEqual(status.strip(), "", status)


class SignalExit(unittest.TestCase):
    """_run's negative-return-code mapping: a signal-killed child must surface as
    128 + signal (143 for SIGTERM), never sys.exit()'s truncated raw value (241)."""

    STUB = (
        "import os\n"
        "import signal\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
    )

    def setUp(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        f.write(self.STUB)
        f.close()
        self.stub_path = f.name

    def tearDown(self):
        os.unlink(self.stub_path)

    def test_signal_killed_child_exits_143_not_241(self):
        rc = bm_vault_cli._run(self.stub_path, [])
        self.assertEqual(rc, 143, rc)


class UnknownVerb(unittest.TestCase):
    def test_unknown_verb_exits_two(self):
        code, out = run(["bogus"])
        self.assertEqual(code, 2, out)
        self.assertIn("unknown verb", out)

    def test_no_args_prints_usage_and_exits_zero(self):
        code, out = run([])
        self.assertEqual(code, 0, out)
        self.assertIn("verbs:", out)


class GateCounts(unittest.TestCase):
    """Drives bm_vault_cli._gate_counts backwards against both JSON shapes it
    must survive: the VB7-02 envelope bm_vault_graph.py measure --json now
    emits, and the flat dict doctor read before that change (and would read
    again from any other future caller that skips the envelope)."""

    def test_envelope_shape_reads_nested_counts(self):
        stats = {"tool": "bm_vault_graph.measure", "verdict": "PASS",
                 "counts": {"note_count": 3, "broken_count": 1},
                 "findings": [], "schema_version": 1}
        self.assertEqual(bm_vault_cli._gate_counts(stats), (3, 1))

    def test_flat_shape_without_counts_key_reads_flat(self):
        stats = {"note_count": 5, "broken_count": 0}
        self.assertEqual(bm_vault_cli._gate_counts(stats), (5, 0))

    def test_missing_keys_report_no_data_instead_of_raising(self):
        self.assertEqual(bm_vault_cli._gate_counts({}), ("NO-DATA", "NO-DATA"))
        self.assertEqual(bm_vault_cli._gate_counts({"counts": {}}),
                         ("NO-DATA", "NO-DATA"))


class DoctorVerb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-cli-doctor-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        self.vault = os.path.join(self.tmp, "v")
        _clean_vault(self.vault)
        with open(os.path.join(self.home, ".claude", "bm_vault.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"vault": self.vault}, f)
        self.env = _scratch_env(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_doctor_reports_inventory_and_gate_state_read_only(self):
        code, out = run(["doctor"], env=self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("vault path resolution", out)
        self.assertIn(self.vault, out)
        self.assertIn("tool inventory", out)
        self.assertIn("bm_vault_graph.py:", out)
        self.assertIn("gate state", out)
        self.assertIn("notes: 1", out)
        self.assertIn("broken links: 0", out)
        self.assertIn("agent rules", out)
        self.assertIn("report-only defaults", out)
        self.assertIn("docs/VAULT-TRUST-BOUNDARY.md", out)
        self.assertIn("docs/RETRIEVAL-RULES.md", out)
        # read-only: the fixture vault must be untouched
        self.assertEqual(sorted(os.listdir(self.vault)), ["one.md"])

    def test_doctor_no_data_when_nothing_configured(self):
        home = os.path.join(self.tmp, "home-empty")
        os.makedirs(home)
        code, out = run(["doctor"], env=_scratch_env(home))
        self.assertEqual(code, 0, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
