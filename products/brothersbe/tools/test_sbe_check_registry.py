#!/usr/bin/env python3
"""Fixtures for the registered check registry (`.sbe/checks.yml`) and the
binding between a receipt and the check it claims to be.
Run: python3 tools/test_sbe_check_registry.py

THE DEFECT THESE EXIST FOR. `sbe evidence run` proves a process started, was
timed and exited. It did not prove WHICH check ran, so `-- true` minted a clean,
sealed, commit-bound receipt and `--kind`, a word the caller typed beside it,
was the only thing naming the obligation it answered. Every test below is
written against that: the first two prove `true` cannot buy a migration
rehearsal, and the rest prove that the registered check it must be instead stays
bound to the registry, the runner bytes and the covered files.

Every test builds a real git repository in a temporary directory, writes a real
registry into it, runs the real CLI and reads the receipt that came out. Nothing
is mocked, because the defect lives exactly at the seam between a command and a
claim about it and a mocked subprocess would test the mock. Receipts are written
OUTSIDE the repository under test, so minting one never dirties the tree it
covers.

CALIBRATION. Each test names, in its own docstring, what it saw before the
control existed: with `checkId` outside the seal and `checkKinds` accepted as
identity, the receipts built here verified and satisfied policy. The sentences
recorded in each docstring are the observed red state, not a prediction.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import unittest

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
SHIPPED_POLICY = os.path.join(ROOT, ".sbe", "policy.yml")

#: The fixture registry. Two registered checks so a receipt can be offered for
#: the WRONG one, and a third whose runner refuses to start when a variable
#: outside the allowlist reached it, which is how the environment filter is
#: proven rather than asserted.
REGISTRY = """schemaVersion: "1.0"

checks:
  migration-rehearsal:
    kind: "migration"
    why: "a schema change nobody can reverse in an hour once it has run"
    command:
      executable: "python3"
      arguments:
        - "scripts/rehearse-migration.py"
      cwd: "."
    covers:
      - "migrations/**"
    runnerFiles:
      - "scripts/rehearse-migration.py"
    protectedEvidence: true

  migration-reconciliation:
    kind: "numbers"
    why: "the row counts either reconcile after the migration or the migration is not done"
    command:
      executable: "python3"
      arguments:
        - "scripts/reconcile-migration.py"
      cwd: "."
    covers:
      - "migrations/**"
    runnerFiles:
      - "scripts/reconcile-migration.py"
    protectedEvidence: true

  environment-probe:
    kind: "ran"
    why: "proves the registered path drops what the caller exported"
    command:
      executable: "python3"
      arguments:
        - "scripts/environment-probe.py"
      cwd: "."
    covers:
      - "migrations/**"
    runnerFiles:
      - "scripts/environment-probe.py"
    protectedEvidence: true

unregistered:
  - id: "claude-plugin-e2e"
    why: "not built in this fixture, listed so a policy naming it reports MISSING honestly"
"""

REHEARSAL_RUNNER = ("import sys\n"
                    "sys.stdout.write('rehearsed the migration against a restore\\n')\n")

RECONCILE_RUNNER = ("import sys\n"
                    "sys.stdout.write('row counts reconcile\\n')\n")

#: Exits 3 when a variable OUTSIDE `checks.ENV_ALLOWLIST` reached the process.
#: A registered run must therefore exit 0 with that variable exported, and a
#: free-form run of the same file must exit 3, which is the calibration: without
#: the filter both exit 3.
PROBE_RUNNER = ("import os, sys\n"
                "sys.exit(3 if os.environ.get('SBE_FIXTURE_LEAK') else 0)\n")


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class RegistryFixture(unittest.TestCase):
    """A real repository carrying the shipped policy, the fixture registry, its
    runners, and one migration committed on top of the base commit."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="sbe-checks-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        self.receipts = os.path.join(self.tmp, "receipts")
        os.makedirs(self.repo)
        os.makedirs(self.receipts)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "Registry Fixture")
        with io.open(SHIPPED_POLICY, encoding="utf-8") as fh:
            write(self.repo, os.path.join(".sbe", "policy.yml"), fh.read())
        write(self.repo, os.path.join(".sbe", "checks.yml"), REGISTRY)
        write(self.repo, os.path.join("scripts", "rehearse-migration.py"), REHEARSAL_RUNNER)
        write(self.repo, os.path.join("scripts", "reconcile-migration.py"), RECONCILE_RUNNER)
        write(self.repo, os.path.join("scripts", "environment-probe.py"), PROBE_RUNNER)
        write(self.repo, "README.md", "a repository\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, os.path.join("migrations", "001_add_orders.sql"),
              "CREATE TABLE orders (id integer primary key);\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "add a migration")
        self.head = git(self.repo, "rev-parse", "HEAD")

    # -- running the CLI ---------------------------------------------------

    def sbe(self, *argv, **kwargs):
        """The CompletedProcess, not a pair. Deliberately not `(code, text)`:
        `evals/test_no_data_class.py` reads every two-value return under tools/
        as a possible (verdict, evidence) pair it must prove is never PASS, and
        a fixture helper is not a verdict producer."""
        env = dict(os.environ)
        env.pop("SBE_CI_RUN_ID", None)
        env.update(kwargs.get("env") or {})
        return subprocess.run([sys.executable, SBE] + list(argv), cwd=self.repo,
                              capture_output=True, text=True, env=env)

    @staticmethod
    def text(proc):
        return proc.stdout + proc.stderr

    def mint_registered(self, check_id, name=None, protected=True):
        """Run a registered check and return the receipt path it wrote."""
        out_path = os.path.join(self.receipts, (name or check_id) + ".json")
        env = {"SBE_CI_RUN_ID": "gha-fixture-1"} if protected else {}
        proc = self.sbe("evidence", "run", "--check", check_id, "--out", out_path,
                        "--cwd", self.repo, env=env)
        self.assertTrue(os.path.exists(out_path),
                        "the registered run wrote no receipt: %s" % self.text(proc))
        self.assertEqual(proc.returncode, 0, self.text(proc))
        return out_path

    def mint_free_form(self, command, name="free-form", extra=(), protected=True):
        """Run a caller-supplied command and return the receipt path it wrote."""
        out_path = os.path.join(self.receipts, name + ".json")
        env = {"SBE_CI_RUN_ID": "gha-fixture-1"} if protected else {}
        argv = (["evidence", "run", "--out", out_path, "--cwd", self.repo, "--base", self.base]
                + list(extra) + ["--"] + list(command))
        proc = self.sbe(*argv, env=env)
        self.assertTrue(os.path.exists(out_path),
                        "the free-form run wrote no receipt: %s" % self.text(proc))
        return out_path

    def verify(self, path, extra=()):
        """The parsed `sbe evidence verify --json` report, with the exit code on
        it. An object, not a pair, for the reason `sbe()` states."""
        proc = self.sbe("evidence", "verify", path, "--cwd", self.repo, "--json", *extra)
        blob = proc.stdout[proc.stdout.index("{"):proc.stdout.rindex("}") + 1] \
            if "{" in proc.stdout else "{}"
        report = json.loads(blob)
        report["exitCode"] = proc.returncode
        report["stderr"] = proc.stderr
        return report

    def evaluate(self):
        """The parsed `sbe policy evaluate --json` report, with the exit code."""
        proc = self.sbe("policy", "evaluate", self.repo, "--base", self.base, "--head", "HEAD",
                        "--json", "--receipts-dir", self.receipts)
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            raise AssertionError("policy evaluate printed no JSON report: %s"
                                 % self.text(proc))
        report["exitCode"] = proc.returncode
        return report

    def state_of(self, report, requirement):
        for record in report["requirements"]:
            if record["requirement"] == requirement:
                return record["state"]
        raise AssertionError("no requirement named %r in %s"
                             % (requirement, [r["requirement"] for r in report["requirements"]]))

    def why_of(self, report, requirement):
        for record in report["requirements"]:
            if record["requirement"] == requirement:
                return record.get("why") or ""
        raise AssertionError("no requirement named %r" % requirement)

    def receipt(self, path):
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data, path):
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return path

    def reseal(self, data):
        """Recompute the seal so a fixture can isolate ONE control. Without it
        every edit trips the seal as well as the thing under test, and a fixture
        that fires two controls proves neither."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            data["runId"] = mod.compute_seal(data)
        finally:
            sys.path.pop(0)
        return data


# ---------------------------------------------------------------------------
# 1 and 2: `true` cannot buy a check, and neither can a caller-typed kind
# ---------------------------------------------------------------------------

class TheDefect(RegistryFixture):

    def test_true_cannot_satisfy_the_registered_migration_rehearsal(self):
        """Required test 1.

        RED, observed: `-- true` under a CI run id minted a receipt that
        verified, read CI-CLAIMED, and the only reason it did not clear
        migration-rehearsal was that nobody could spell that id into it. The
        receipt is real; what it is not is the registered check.
        """
        path = self.mint_free_form([sys.executable, "-c", "pass"], name="true")
        receipt = self.receipt(path)
        self.assertIsNone(receipt["checkId"],
                          "a free-form run recorded a registered check id")
        self.assertIn("FREE FORM", receipt["checkIdSource"])
        report = self.verify(path)
        self.assertEqual(report["trust"], "LOCAL-ADVISORY",
                         "a free-form command under a CI run id read as protected evidence: %s"
                         % report["trustWhy"])
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "MISSING")
        self.assertNotEqual(policy["exitCode"], 0)

    def test_a_caller_declared_kind_stays_advisory_and_clears_nothing(self):
        """Required test 2.

        RED, observed: `_offered_check_ids` accepted `checkKinds` as identity, so
        a receipt carrying the word `migration-rehearsal` in that caller-supplied
        list SATISFIED the requirement. The CLI refuses to write that word today,
        which is the first half of the fix; the second half is that a receipt
        carrying it anyway still clears nothing.
        """
        refused = self.sbe("evidence", "run", "--kind", "migration", "--out",
                           os.path.join(self.receipts, "kinded.json"), "--cwd", self.repo,
                           "--", sys.executable, "-c", "pass")
        self.assertNotEqual(refused.returncode, 0,
                            "`--kind migration` was accepted: %s" % self.text(refused))

        path = self.mint_free_form([sys.executable, "-c", "pass"], name="kinded")
        receipt = self.receipt(path)
        receipt["checkKinds"] = ["migration-rehearsal"]
        self.save(self.reseal(receipt), path)
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "MISSING")
        self.assertIn("checkKinds", self.why_of(policy, "check:migration-rehearsal"))
        self.assertNotEqual(policy["exitCode"], 0)


# ---------------------------------------------------------------------------
# 3, 9 and 10: what a registered run does earn
# ---------------------------------------------------------------------------

class WhatRegistrationEarns(RegistryFixture):

    def test_the_registered_migration_command_satisfies_the_requirement(self):
        """Required test 3."""
        self.mint_registered("migration-rehearsal")
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "SATISFIED",
                         self.why_of(policy, "check:migration-rehearsal"))

    def test_a_registered_check_that_FAILED_does_not_satisfy_its_requirement(self):
        """The other half of item 4's definition of done.

        Calibrated against the defect a hostile refuter found here: nothing read
        the receipt's exit code, so a registered check that ran and FAILED still
        came back SATISFIED. The runner is rewritten to exit 1 and the check is
        run for real, so the receipt seals correctly and the only thing wrong
        with it is the outcome it honestly records. An earlier version of this
        test edited exitCode in the finished file, which broke the seal: the
        receipt was then rejected for being tampered with rather than for
        recording a failure, so the test stayed green with the guard removed,
        which is the definition of a vacuous test."""
        write(self.repo, os.path.join("scripts", "rehearse-migration.py"),
              "import sys\n"
              "sys.stdout.write('the rehearsal did not reconcile\\n')\n"
              "sys.exit(1)\n")
        # Commit it, so the receipt is minted over a CLEAN tree. Left dirty,
        # the receipt comes back advisory and this fixture would pass on the
        # dirty-tree rule while proving nothing about the exit code, which is
        # the second way this same test managed to be vacuous.
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "make the rehearsal fail")
        out_path = os.path.join(self.receipts, "migration-rehearsal.json")
        self.sbe("evidence", "run", "--check", "migration-rehearsal", "--out", out_path,
                 "--cwd", self.repo, env={"SBE_CI_RUN_ID": "gha-fixture-1"})
        import json as _json
        self.assertTrue(os.path.exists(out_path),
                        "no receipt was written for the failing registered run; this fixture "
                        "needs one to ask the policy engine what it does with it")
        self.assertEqual(_json.load(open(out_path)).get("exitCode"), 1,
                         "the receipt does not record the failing exit code")
        policy = self.evaluate()
        self.assertNotEqual(self.state_of(policy, "check:migration-rehearsal"), "SATISFIED",
                            "a receipt recording a FAILING run satisfied the requirement: %s"
                            % self.why_of(policy, "check:migration-rehearsal"))
        self.assertNotEqual(policy["exitCode"], 0,
                            "the run exited 0 over a failing registered check")

    def test_a_receipt_with_no_exit_code_at_all_fails_closed(self):
        """An outcome nobody recorded is not an outcome anybody may assume."""
        import json as _json
        path = self.mint_registered("migration-rehearsal")
        receipt = _json.load(open(path))
        receipt.pop("exitCode", None)
        with open(path, "w") as handle:
            _json.dump(receipt, handle)
        policy = self.evaluate()
        self.assertNotEqual(self.state_of(policy, "check:migration-rehearsal"), "SATISFIED",
                            "a receipt with no exit code satisfied the requirement: %s"
                            % self.why_of(policy, "check:migration-rehearsal"))

    def _demand_protected_evidence(self):
        """Turn the protected requirement ON for this fixture only.

        The SHIPPED policy carries it off in this release, and says why in its
        own comment: the label that meant protected was removed as forgeable
        (BR-1013), so demanding a proof nothing can currently issue would block
        every governed change instead of protecting one. The mechanism is not
        gone, and these two tests are what proves that: they flip the flag on
        and assert that neither trust level this release can mint satisfies it."""
        path = os.path.join(self.repo, ".sbe", "policy.yml")
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
        assert "protectedEvidenceRequired: false" in text, text[:200]
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("protectedEvidenceRequired: false",
                                      "protectedEvidenceRequired: true"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "demand protected evidence")

    def test_a_local_advisory_registered_run_fails_a_protected_requirement(self):
        """Required test 9. The receipt is real, registered and valid; it is
        refused because the policy asks for protected evidence and a receipt any
        laptop can mint is not that. UNPROTECTED is its own word: saying INVALID
        about it would be a lie the operator would waste an hour on."""
        self._demand_protected_evidence()
        self.mint_registered("migration-rehearsal", protected=False)
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "UNPROTECTED",
                         self.why_of(policy, "check:migration-rehearsal"))
        self.assertNotEqual(policy["exitCode"], 0)

    def test_no_trust_level_this_release_can_mint_satisfies_a_protected_requirement(self):
        """BR-1013's whole point, asserted rather than described.

        CI-CLAIMED is the strongest label this release produces, and it is
        explicitly not protected: it says CI shaped metadata was recorded and
        nothing verified who recorded it. A policy that demands protected
        evidence must therefore refuse it too, not only the local one, or the
        downgrade would be a rename rather than a change in what is claimed."""
        self._demand_protected_evidence()
        self.mint_registered("migration-rehearsal", protected=True)
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "UNPROTECTED",
                         self.why_of(policy, "check:migration-rehearsal"))
        self.assertIn("CI-CLAIMED", self.why_of(policy, "check:migration-rehearsal"))
        self.assertNotEqual(policy["exitCode"], 0)

    def test_a_valid_protected_registered_run_passes(self):
        """Required test 10, and the positive that proves the other nine are
        rejecting something a real run can pass."""
        path = self.mint_registered("migration-rehearsal")
        report = self.verify(path, extra=["--strict"])
        self.assertEqual(report["verdict"], "PASS", json.dumps(report["reasons"], indent=2))
        self.assertEqual(report["exitCode"], 0)
        self.assertEqual(report["trust"], "CI-CLAIMED", report["trustWhy"])
        receipt = self.receipt(path)
        self.assertEqual(receipt["checkId"], "migration-rehearsal")
        self.assertEqual(receipt["checkKind"], "migration")
        self.assertEqual(receipt["argv"], ["python3", "scripts/rehearse-migration.py"])
        self.assertEqual([e["path"] for e in receipt["checkBinding"]["runnerFiles"]],
                         ["scripts/rehearse-migration.py"])
        self.assertEqual([e["path"] for e in receipt["coveredFiles"]],
                         ["migrations/001_add_orders.sql"])
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "SATISFIED",
                         self.why_of(policy, "check:migration-rehearsal"))


# ---------------------------------------------------------------------------
# 4, 5, 6: what invalidates a receipt that was valid a minute ago
# ---------------------------------------------------------------------------

class WhatInvalidates(RegistryFixture):

    def _edit_registry(self, old, new):
        path = os.path.join(self.repo, ".sbe", "checks.yml")
        with io.open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(old, body, "the fixture registry no longer holds %r" % old)
        write(self.repo, os.path.join(".sbe", "checks.yml"), body.replace(old, new, 1))

    def test_changing_the_check_specification_invalidates_the_old_receipt(self):
        """Required test 4. The registry edit is left UNCOMMITTED on purpose, so
        the head commit and the covered files are untouched and the only control
        that can fire is the specification digest."""
        path = self.mint_registered("migration-rehearsal")
        self.assertEqual(self.verify(path)["verdict"], "PASS")
        self._edit_registry('    covers:\n      - "migrations/**"\n',
                            '    covers:\n      - "migrations/**"\n      - "db/migrate/**"\n')
        report = self.verify(path)
        self.assertEqual(report["verdict"], "FAIL", json.dumps(report, indent=2))
        self.assertIn("no longer exists", " ".join(report["reasons"]))
        self.assertIn("redefined", " ".join(report["reasons"]))

    def test_changing_the_runner_script_invalidates_the_old_receipt(self):
        """Required test 5. A modified runner is a different check, however
        unchanged the registry entry that names it."""
        path = self.mint_registered("migration-rehearsal")
        self.assertEqual(self.verify(path)["verdict"], "PASS")
        write(self.repo, os.path.join("scripts", "rehearse-migration.py"),
              REHEARSAL_RUNNER + "# a line that changes what the check is\n")
        report = self.verify(path)
        self.assertEqual(report["verdict"], "FAIL", json.dumps(report, indent=2))
        self.assertIn("was modified after the evidence was made", " ".join(report["reasons"]))

    def test_changing_the_command_arguments_invalidates_the_old_receipt(self):
        """Required test 6."""
        path = self.mint_registered("migration-rehearsal")
        self.assertEqual(self.verify(path)["verdict"], "PASS")
        self._edit_registry('        - "scripts/rehearse-migration.py"\n'
                            '      cwd: "."\n',
                            '        - "scripts/rehearse-migration.py"\n'
                            '        - "--fast"\n'
                            '      cwd: "."\n')
        report = self.verify(path)
        self.assertEqual(report["verdict"], "FAIL", json.dumps(report, indent=2))
        self.assertIn("is not the registered command", " ".join(report["reasons"]))


# ---------------------------------------------------------------------------
# 7 and 8: applicability
# ---------------------------------------------------------------------------

class Applicability(RegistryFixture):

    def test_a_receipt_for_the_wrong_check_id_does_not_satisfy_this_one(self):
        """Required test 7. The receipt below is registered, protected, valid and
        satisfies its OWN requirement, which is what makes it the right negative:
        the only thing wrong with it against migration-rehearsal is that it is a
        different check."""
        self.mint_registered("migration-reconciliation")
        policy = self.evaluate()
        self.assertEqual(self.state_of(policy, "check:migration-reconciliation"), "SATISFIED",
                         self.why_of(policy, "check:migration-reconciliation"))
        self.assertEqual(self.state_of(policy, "check:migration-rehearsal"), "MISSING",
                         self.why_of(policy, "check:migration-rehearsal"))

    def test_a_receipt_covering_unrelated_files_fails_applicability(self):
        """Required test 8. The coverage list is rewritten to a file the
        registered check does not cover and the receipt is RESEALED, so the seal
        is not what fires: applicability is."""
        path = self.mint_registered("migration-rehearsal")
        receipt = self.receipt(path)
        receipt["coveredFiles"] = [{"path": "README.md", "sha256": "0" * 64, "note": None}]
        self.save(self.reseal(receipt), path)
        report = self.verify(path)
        self.assertEqual(report["verdict"], "FAIL", json.dumps(report, indent=2))
        self.assertIn("does not cover", " ".join(report["reasons"]))


# ---------------------------------------------------------------------------
# The registered path accepts no substitution, and inherits no environment
# ---------------------------------------------------------------------------

class NoSubstitution(RegistryFixture):

    def test_a_command_covers_or_kind_beside_check_is_refused(self):
        """Not one of the ten, and load-bearing anyway: a substitution that
        succeeds silently is the caller-supplied command the registry replaces."""
        out_path = os.path.join(self.receipts, "substituted.json")
        for extra in (["--", sys.executable, "-c", "pass"],
                      ["--covers", "README.md"],
                      ["--kind", "gate"]):
            proc = self.sbe("evidence", "run", "--check", "migration-rehearsal", "--out",
                            out_path, "--cwd", self.repo, *extra)
            self.assertNotEqual(proc.returncode, 0,
                                "%r was accepted beside --check: %s" % (extra,
                                                                        self.text(proc)))
            self.assertFalse(os.path.exists(out_path),
                             "a refused substitution still wrote a receipt")

    def test_an_unregistered_or_unknown_check_id_is_refused_by_name(self):
        for check_id, expected in (("claude-plugin-e2e", "UNREGISTERED"),
                                   ("no-such-check", "not registered")):
            proc = self.sbe("evidence", "run", "--check", check_id, "--out",
                            os.path.join(self.receipts, "x.json"), "--cwd", self.repo)
            self.assertNotEqual(proc.returncode, 0, self.text(proc))
            self.assertIn(expected, self.text(proc))

    def test_the_registered_run_drops_variables_outside_the_allowlist(self):
        """Calibrated against the free-form path in the same test: the probe
        runner exits 3 when SBE_FIXTURE_LEAK reaches it, and the free-form run
        below DOES exit 3, which is what the registered run would do too without
        the filter."""
        leaked = self.sbe("evidence", "run", "--out",
                          os.path.join(self.receipts, "leak.json"), "--cwd", self.repo,
                          "--base", self.base, "--", sys.executable,
                          os.path.join(self.repo, "scripts", "environment-probe.py"),
                          env={"SBE_FIXTURE_LEAK": "1"})
        self.assertEqual(leaked.returncode, 1,
                         "the free-form probe did not see the exported variable, so the "
                         "registered case below proves nothing: %s" % self.text(leaked))
        proc = self.sbe("evidence", "run", "--check", "environment-probe", "--out",
                        os.path.join(self.receipts, "probe.json"), "--cwd", self.repo,
                        env={"SBE_FIXTURE_LEAK": "1"})
        self.assertEqual(proc.returncode, 0,
                         "a variable outside the allowlist reached the registered check: %s"
                         % self.text(proc))
        receipt = self.receipt(os.path.join(self.receipts, "probe.json"))
        self.assertGreater(receipt["checkBinding"]["environmentDropped"], 0)
        self.assertNotIn("SBE_FIXTURE_LEAK", receipt["checkBinding"]["environmentKept"])


class TheShippedRegistry(unittest.TestCase):
    """The registry this repository actually ships, read the same way the CLI
    reads it. A registry that parses in a fixture and not in the repository is a
    registry nobody can use."""

    def test_the_shipped_registry_loads_and_every_runner_file_exists(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import checks as mod
            registry = mod.load_registry(os.path.join(ROOT, ".sbe", "checks.yml"))
        finally:
            sys.path.pop(0)
        self.assertTrue(registry["checks"], "the shipped registry registers no check")
        for check_id, spec in sorted(registry["checks"].items()):
            for rel in spec["runnerFiles"]:
                self.assertTrue(os.path.exists(os.path.join(ROOT, rel)),
                                "check %s names runner file %s, which does not exist. A "
                                "registered check nobody can run reports a broken command "
                                "where the truth is that the check does not exist"
                                % (check_id, rel))

    def test_every_check_the_shipped_policy_requires_is_registered_or_declared(self):
        """A policy naming a check the registry neither registers nor lists is a
        requirement nobody can ever satisfy and nobody wrote down. MISSING is the
        right verdict for an unbuilt check; silence is not."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import checks as checks_mod
            from brothersbe import policy as policy_mod
            registry = checks_mod.load_registry(os.path.join(ROOT, ".sbe", "checks.yml"))
            policy = policy_mod.load_policy(SHIPPED_POLICY)
        finally:
            sys.path.pop(0)
        known = set(registry["checks"]) | set(u["id"] for u in registry["unregistered"])
        for rule in policy["rules"]:
            for check_id in rule.get("requiredChecks") or []:
                self.assertIn(check_id, known,
                              "policy rule %s requires check %r and .sbe/checks.yml neither "
                              "registers it nor declares why it is unregistered"
                              % (rule["id"], check_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
