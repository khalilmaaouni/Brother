#!/usr/bin/env python3
"""Fixtures for the vault-scoping fix. Run: python3 tools/test_sbe_vault_scope.py

Two defects, found by stranger walks of journeys 2 and 3:

J2-F9 (env set): with BROTHERSBE_VAULT pointing at a real vault carrying
ledger content, `sbe verify` on a toy dossier minted DECISION.md packages
about session-practice checks (cache-economy, correction-latency, and here
schema-2-uniform, the simplest to force red without a dated 7-day-window
fixture) INTO the change dossier's decisions/ directory. Session-practice
grades do not belong in a change record.

J3-F1 (env unset): even with the variable unset, `sbe review` probed the
hardcoded default os.path.expanduser("~/BrotherSBEVault"): each check
honestly read NO-DATA, but the probe itself contradicted `sbe doctor`'s own
promise ("BROTHERSBE_VAULT is unset, so telemetry, session logs and resume
briefs have nowhere durable to go") and reached an absolute path outside the
repository, inside the operator's real home directory.

THE FIX under test, in two parts:
  - `tools/sbe_telemetry.py`: an unset BROTHERSBE_VAULT resolves VAULT to an
    explicit NO_VAULT_SENTINEL, never to `os.path.expanduser("~/BrotherSBEVault")`.
  - `src/brothersbe/cli.py`: `_record_decisions` (by way of the new
    `_repo_scoped_lines`) mints a decision package only for a check whose
    declared evidence resolves inside the directory being checked, reusing
    `tools/sbe_score.py`'s own `CHECKS` registry and
    `_resolved_sources`/`_reads_this_tree` rather than a hand-typed list of
    check names.

Every scenario runs the real `bin/sbe` (and, for the direct-tool half of (b),
the real `tools/sbe_score.py`) in a throwaway git repository, the same
pattern `tools/test_sbe_review_record.py` uses.
"""
import io
import json
import io, os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(HERE, "..", "bin", "sbe")
REAL_HOME_VAULT_DEFAULT = os.path.expanduser("~/BrotherSBEVault")


def _run(argv, cwd=None, env=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: a two-value return reads as a possible
    # (verdict, evidence) pair to the honesty meta-test, which refuses any
    # such function sitting outside a check registry (the same convention
    # tools/test_sbe_review_record.py already uses for this exact helper).
    return out.returncode, out.stdout + out.stderr, out.stderr


def _decision_texts(repo):
    """Every DECISION.md body under this throwaway repo's repository-level
    store. The repos these scenarios check carry no 00-intake.json, so
    `decisions.package_location` always picks `.sbe/decisions/` under the
    repo, never a dossier's own `decisions/`."""
    texts = []
    store = os.path.join(repo, ".sbe", "decisions")
    if os.path.isdir(store):
        for name in sorted(os.listdir(store)):
            path = os.path.join(store, name, "DECISION.md")
            if os.path.isfile(path):
                texts.append(io.open(path, encoding="utf-8").read())
    return texts


class VaultScopeScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-vault-scope-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "fixture")
        io.open(os.path.join(self.repo, "seed.txt"), "w").write("seed\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text

    def _base_env(self):
        # Isolate every `sbe` call in this file from the real machine: no
        # real vault or registries, and the citation check scans the
        # throwaway repo rather than defaulting to this installation's own
        # tree. Mirrors tools/test_sbe_review_record.py's ReviewScenario.setUp.
        env = dict(os.environ)
        for k in ("BROTHERSBE_VAULT", "BROTHERSBE_REGISTRIES", "SBE_LINT_ROOT",
                  "SBE_CITATION_ROOT"):
            env.pop(k, None)
        env["SBE_CITATION_ROOT"] = self.repo
        return env

    def _fixture_vault(self, ledger_rows):
        """A real vault directory, off in the throwaway tmp tree (never
        under any $HOME), carrying one outcomes.jsonl with `ledger_rows`."""
        vault = os.path.join(self.tmp, "vault")
        tel = os.path.join(vault, "99-System", "telemetry")
        os.makedirs(tel)
        with io.open(os.path.join(tel, "outcomes.jsonl"), "w") as f:
            for row in ledger_rows:
                f.write(json.dumps(row) + "\n")
        return vault

    # -----------------------------------------------------------------
    # (a) env unset: no path under the real home-directory default is
    # stat'd or created, and `sbe review`'s check bodies still see NO-DATA
    # rather than a crash.
    # -----------------------------------------------------------------

    def test_env_unset_vault_resolves_to_a_sentinel_not_the_home_default(self):
        """CALIBRATED RED: before the fix, `sbe_telemetry.VAULT` computed
        `os.environ.get("BROTHERSBE_VAULT", os.path.expanduser("~/BrotherSBEVault"))`,
        so with the variable unset (and HOME pointed at a throwaway
        directory, to keep this test from depending on the real machine's
        actual home) VAULT equalled `<fake HOME>/BrotherSBEVault` exactly:
        this assertion read RED, printing that same path back. After the
        fix VAULT is the module's own NO_VAULT_SENTINEL and VAULT_CONFIGURED
        is False; neither is derived from HOME at all."""
        env = self._base_env()
        fake_home = os.path.join(self.tmp, "fake-home")
        os.makedirs(fake_home)
        env["HOME"] = fake_home
        code, out, err = _run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import sbe_telemetry as t; "
             "print(t.VAULT); print(t.VAULT_CONFIGURED); print(t.NO_VAULT_SENTINEL)"
             % os.path.join(ROOT, "tools")],
            env=env)
        self.assertEqual(0, code, "probe script failed: %s" % (out + err))
        lines = out.strip().splitlines()
        self.assertEqual(3, len(lines), out)
        vault_seen, configured_seen, sentinel_seen = lines
        fake_default = os.path.join(fake_home, "BrotherSBEVault")
        self.assertNotEqual(fake_default, vault_seen,
                            "VAULT still resolved to expanduser('~/BrotherSBEVault') under "
                            "the fake HOME this test set: %r" % out)
        self.assertNotIn(fake_home, vault_seen,
                         "VAULT still derives from HOME with BROTHERSBE_VAULT unset: %r"
                         % out)
        self.assertEqual("False", configured_seen)
        self.assertEqual(sentinel_seen, vault_seen,
                         "VAULT unset did not resolve to NO_VAULT_SENTINEL: %r" % out)

    def test_env_unset_verify_never_creates_the_real_home_vault(self):
        """CALIBRATED RED (behavioral proxy): running the real `sbe verify`
        against a toy repository, BROTHERSBE_VAULT unset, HOME pointed at a
        throwaway, empty directory. Before the fix this created (or at
        least stat'd, via os.path.isfile/glob, the equivalent evidence of
        having reached) `<HOME>/BrotherSBEVault`; the read paths never
        WRITE anything there, so the strong proxy is that the directory
        never comes to exist at all, which is what this asserts."""
        env = self._base_env()
        fake_home = os.path.join(self.tmp, "fake-home-2")
        os.makedirs(fake_home)
        env["HOME"] = fake_home
        code, out, _err = _run([sys.executable, SBE, "verify", self.repo], env=env)
        fake_vault = os.path.join(fake_home, "BrotherSBEVault")
        self.assertFalse(os.path.exists(fake_vault),
                         "sbe verify created %s: an unset BROTHERSBE_VAULT reached the "
                         "home-directory default. Output: %s" % (fake_vault, out))

    def test_doctor_env_unset_still_names_the_switch(self):
        """(c) sbe doctor's own honest line is untouched by this fix: it
        reads os.environ directly and never imports sbe_telemetry.VAULT."""
        env = self._base_env()
        code, out, _err = _run([sys.executable, SBE, "doctor", "--cwd", self.repo], env=env)
        self.assertIn(
            "BROTHERSBE_VAULT is unset, so telemetry, session logs and resume briefs "
            "have nowhere durable to go", out, out)

    # -----------------------------------------------------------------
    # (b) env set to a fixture vault with ledger content: dossier verify
    # mints no telemetry decision into the dossier, while the direct score
    # path still reads that fixture vault.
    # -----------------------------------------------------------------

    def test_verify_all_mints_no_telemetry_decision_but_still_prints_the_verdict(self):
        """CALIBRATED RED: before the J2-F9 fix, `_record_decisions` minted a
        DECISION.md for every FAIL line any delegate printed, with no
        scoping at all. One ledger row missing "schema": 2 makes
        `schema-2-uniform` FAIL (tools/sbe_score.py check_schema_uniform);
        pre-fix this assertion found "check: schema-2-uniform" inside a
        written DECISION.md, exactly J2-F9. After that fix the FAIL still
        prints (the run really did read the fixture vault), the run
        discloses the exclusion, and no package is written for it.

        `--all` was added later (E2.2/E2.3, 2026-08-31): the default `sbe
        verify` no longer runs a vault-fed check at all (see the sibling
        test below), so this scenario now needs `--all` to opt back into
        seeing schema-2-uniform run, exactly the way a maintainer who wants
        the session-practice checks would ask for them. The MINT-scoping
        behaviour under test here is unchanged; only how you reach it is."""
        vault = self._fixture_vault([{"session_id": "sess-0001"}])
        env = self._base_env()
        env["BROTHERSBE_VAULT"] = vault
        code, out, _err = _run([sys.executable, SBE, "verify", "--all", self.repo], env=env)
        self.assertIn("schema-2-uniform", out, out)
        self.assertIn("FAIL", out, out)
        self.assertIn("excluded from decision packages", out,
                      "no disclosure that a vault-fed check was scoped out of minting: %s"
                      % out)
        for text in _decision_texts(self.repo):
            self.assertNotIn("check: schema-2-uniform", text,
                             "a decision package was minted for a vault-fed, session-"
                             "practice check:\n%s" % text)

    def test_verify_default_never_runs_the_vault_fed_check_at_all(self):
        """E2.2/E2.3 (2026-08-31): the finding was not only that a decision
        package could be minted for a vault-fed check (J2-F9, fixed above);
        it was that the DEFAULT `sbe verify` run executed and printed 14 of
        15 scored checks about the vendor's own machine, one of which
        printed the vendor's absolute home path into a stranger's output.
        The fix is `--repo-only`, which `sbe verify` now passes to
        `sbe_score.py` unless `--all` is given: schema-2-uniform's FAIL
        (forced by the same fixture vault as the sibling test) must not
        even print, because the check must never RUN, not merely be
        filtered out of the decision mint after the fact."""
        vault = self._fixture_vault([{"session_id": "sess-0001"}])
        env = self._base_env()
        env["BROTHERSBE_VAULT"] = vault
        code, out, _err = _run([sys.executable, SBE, "verify", self.repo], env=env)
        # The check's NAME is allowed to appear once, in the disclosure line
        # naming what was skipped (so a reader knows how to opt in); what
        # must never appear is a VERDICT LINE for it, i.e. a line that is
        # actually about schema-2-uniform's own result.
        verdict_lines = [l for l in out.splitlines()
                         if l.strip().startswith("schema-2-uniform")]
        self.assertEqual(
            verdict_lines, [],
            "a vault-fed check printed its own verdict line on the default, unscoped "
            "`sbe verify` invocation: %s" % verdict_lines)
        self.assertIn("CHECKS SKIPPED, NOT RUN (--repo-only)", out,
                      "no disclosure that vault-fed checks were skipped by default: %s"
                      % out)
        self.assertIn("schema-2-uniform", out,
                      "the skip disclosure did not even name the check it skipped: %s"
                      % out)

    def test_direct_score_path_still_reads_the_fixture_vault(self):
        """The other half of (b): `tools/sbe_score.py`, run directly (not
        through `sbe verify`), is unaffected by the cli.py-only scoping fix
        and still reads the fixture vault and returns a real FAIL for
        schema-2-uniform: only the MINT is scoped, never the check body."""
        vault = self._fixture_vault([{"session_id": "sess-0001"}])
        env = self._base_env()
        env["BROTHERSBE_VAULT"] = vault
        code, out, _err = _run(
            [sys.executable, os.path.join(ROOT, "tools", "sbe_score.py"), self.repo],
            env=env)
        self.assertIn("schema-2-uniform", out, out)
        self.assertIn("FAIL", out, out)


class TestAnUnclassifiableCheckRefusesToMint(unittest.TestCase):
    """The defect CI's silent-failure lint caught on this fix's own first run:
    `_repo_scoped_lines` swallowed any exception from the classifier and fell
    through to KEEPING that check, which is the mint this whole fix exists to
    prevent. A raising classifier now excludes the check and names the
    exception on stderr. Calibrated by forcing the raise, which is the only
    way this path is ever reached."""

    def test_a_raising_classifier_excludes_and_says_why(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import sbe_score
        from brothersbe import cli, decisions as decisions_mod
        original = sbe_score._reads_this_tree

        def boom(check, here):
            raise RuntimeError("forced classifier failure")

        sbe_score._reads_this_tree = boom
        err = io.StringIO()
        real_stderr = sys.stderr
        sys.stderr = err
        try:
            out = cli._repo_scoped_lines(
                ["ledger-coverage           NO-DATA  nothing opened"],
                ROOT, decisions_mod)
        finally:
            sys.stderr = real_stderr
            sbe_score._reads_this_tree = original
        self.assertIn("could not be classified", err.getvalue())
        self.assertIn("RuntimeError", err.getvalue())
        self.assertEqual(out["lines"], [],
                         "a check whose classification raised was kept, so it "
                         "would still mint: %r" % out["lines"])


if __name__ == "__main__":
    unittest.main()
