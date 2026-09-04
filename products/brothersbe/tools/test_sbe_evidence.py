#!/usr/bin/env python3
"""Fixtures for `sbe evidence`. Run: python3 tools/test_sbe_evidence.py

Every test here builds a real git repository in a temporary directory, runs a
real command through the real wrapper, and verifies the receipt that came out.
Nothing is mocked, because the defect this control exists for lives exactly at
the seam between a command and a claim about it, and a mocked subprocess would
test the mock.

Receipts are written OUTSIDE the repository under test, on purpose. A receipt
written into the tree it covers makes that tree dirty, and every later run then
reports NO-DATA for a reason that has nothing to do with the work.

The fixture set follows the shape the brief asks for: the defect (a hand-written
receipt), the sound case, a stale commit, a stale file, a dirty tree, malformed
receipts, a vacuous required field, and the assertion that raw output never
reaches the file.
"""
import io
import json
import os
import shutil
import subprocess
import platform
import sys
import tempfile
import time
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


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class EvidenceFixture(unittest.TestCase):
    """A fresh repository per test, one base commit and one change commit in it,
    plus a scratch directory outside the repository for receipts."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "README.md", "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "src/service.py", "def handle():\n    return 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "the work this receipt will cover")
        self.head = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def receipt_path(self, name="receipt.json"):
        return os.path.join(self.out, name)

    def run_sbe(self, *argv, **kwargs):
        """The CompletedProcess, not a pair.

        Deliberately not `(code, text)`: `evals/test_no_data_class.py` reads
        every two-value return in `tools/` as a possible (verdict, evidence)
        pair it cannot prove is never PASS, and it is right to. Returning the
        process object says what this is.
        """
        env = dict(os.environ)
        env.update(kwargs.get("env") or {})
        return subprocess.run([sys.executable, SBE] + list(argv), capture_output=True,
                              text=True, env=env, timeout=kwargs.get("timeout"))

    @staticmethod
    def output(proc):
        return proc.stdout + proc.stderr

    def generate(self, command=None, out=None, extra=(), env=None):
        """Run a real command through the wrapper and return (path, code, text)."""
        out = out or self.receipt_path()
        command = command or ["python3", "-c", "print('the suite ran')"]
        argv = ["evidence", "run", "--out", out, "--cwd", self.repo,
                "--base", self.base] + list(extra) + ["--"] + list(command)
        proc = self.run_sbe(*argv, env=env)
        return out, proc.returncode, self.output(proc)

    def register_check(self, check_id="fixture-suite"):
        """Write a one-check `.sbe/checks.yml` into the fixture repository, with
        its runner, and COMMIT both so the tree stays clean.

        A registered run is the only shape that can reach CI-CLAIMED since the
        check registry landed, so any fixture here that needs protected evidence
        needs a registry to resolve the command from.
        """
        write(self.repo, os.path.join("scripts", "fixture-suite.py"),
              "import sys\nsys.stdout.write('the registered suite ran\\n')\n")
        write(self.repo, os.path.join(".sbe", "checks.yml"),
              'schemaVersion: "1.0"\n\n'
              'checks:\n'
              '  %s:\n'
              '    kind: "ran"\n'
              '    command:\n'
              '      executable: "python3"\n'
              '      arguments:\n'
              '        - "scripts/fixture-suite.py"\n'
              '      cwd: "."\n'
              '    covers:\n'
              '      - "src/**"\n'
              '    runnerFiles:\n'
              '      - "scripts/fixture-suite.py"\n'
              '    protectedEvidence: true\n' % check_id)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "register the fixture check")
        self.head = git(self.repo, "rev-parse", "HEAD")
        return check_id

    def verify(self, path=None, extra=()):
        path = path or self.receipt_path()
        proc = self.run_sbe("evidence", "verify", path, "--cwd", self.repo, "--json", *extra)
        text = self.output(proc)
        blob = text[text.index("{"):text.rindex("}") + 1] if "{" in text else "{}"
        return proc.returncode, json.loads(blob), text

    def load(self, path=None):
        with io.open(path or self.receipt_path(), encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data, path=None):
        path = path or self.receipt_path()
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
        return path

    def reseal(self, data):
        """Recompute the seal so a fixture can isolate ONE control.

        Without this, every edit to a receipt trips the seal as well as the
        thing being tested, and a fixture that fires two controls proves neither.
        """
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            data["runId"] = mod.compute_seal(data)
        finally:
            sys.path.pop(0)
        return data


class TestTheDefect(EvidenceFixture):
    """THE DEFECT THIS CONTROL EXISTS FOR: a receipt can be typed by the same
    agent whose work it verifies, and a fabricated duration, exit code and
    digest satisfy the schema, so a gate PASSes on a run nobody performed."""

    def _plausible(self):
        return {
            "schemaVersion": "1.0",
            "generator": "sbe evidence run",
            "generatorVersion": "1.0.0-rc.2",
            "repository": {"remote": None, "remoteNote": "none", "root": self.repo},
            "baseCommit": self.base,
            "headCommit": self.head,
            "argv": ["pytest", "-q"],
            "startedAt": "2026-07-29T09:00:00Z",
            "endedAt": "2026-07-29T09:00:12Z",
            "startedAtEpoch": 1785315600.0,
            "endedAtEpoch": 1785315612.0,
            "durationSeconds": 12.0,
            "exitCode": 0,
            "toolVersions": {"python": "3.9.6", "sbe": "1.0.0-rc.2"},
            "environment": "macOS-26.5.2-arm64-arm-64bit",
            "stdoutSha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b785"
                            "2b855",
            "stderrSha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b785"
                            "2b855",
            "stdoutBytes": 0,
            "stderrBytes": 0,
            "workingTreeDirty": False,
            "workingTreeDetail": "0 uncommitted path(s)",
            "ciRunId": None,
            "coveredFiles": [{"path": "src/service.py", "sha256": "0" * 64, "note": None}],
            "coveredFilesSource": "the diff",
        }

    def test_a_hand_written_receipt_carrying_no_seal_cannot_pass(self):
        """The receipt is plausible in every field a schema can check. It was
        never produced by a wrapper, and no command ever ran."""
        self.save(self._plausible())
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertEqual(code, 1, "a receipt nobody's command produced must not exit 0")
        self.assertIn("runId", json.dumps(data["reasons"]), text)

    def test_a_hand_written_receipt_with_an_invented_seal_cannot_pass(self):
        """The forger knows a runId is expected and types one that looks like a
        digest. Guessing a SHA256 is not writing one."""
        forged = self._plausible()
        forged["runId"] = "f" * 64
        self.save(forged)
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertIn("does not match the seal", json.dumps(data["reasons"]), text)

    def test_editing_a_real_receipts_exit_code_breaks_its_seal(self):
        """The likeliest real-world forgery: take a receipt from a run that
        failed and change the one field the gate reads."""
        self.generate(command=["python3", "-c", "import sys; sys.exit(3)"])
        doctored = self.load()
        self.assertEqual(doctored["exitCode"], 3)
        doctored["exitCode"] = 0
        self.save(doctored)
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertIn("does not match the seal", json.dumps(data["reasons"]), text)

    def test_the_wrapper_exit_code_is_the_commands_own(self):
        """A failing command must not be laundered into a passing evidence step
        by the fact that a receipt got written about it."""
        _path, code, text = self.generate(
            command=["python3", "-c", "import sys; sys.exit(3)"])
        self.assertEqual(code, 1, text)
        self.assertEqual(self.load()["exitCode"], 3, text)


class TestTheSoundCase(EvidenceFixture):
    def test_a_generated_receipt_verifies_pass_at_the_same_commit(self):
        """The control has to be silent on honest work, or engineers switch it
        off, which is the failure mode that costs the most and shows the least."""
        _path, code, text = self.generate()
        self.assertEqual(code, 0, text)
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)
        self.assertIn("src/service.py",
                      json.dumps(self.load()["coveredFiles"]),
                      "the default coverage is the files changed between base and head")

    def test_the_receipt_records_what_the_run_actually_did(self):
        receipt = self.load(self.generate()[0])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertEqual(receipt["headCommit"], self.head)
        self.assertEqual(receipt["baseCommit"], self.base)
        self.assertGreater(receipt["durationSeconds"], 0,
                           "a wrapper that ran the command knows how long it took")
        self.assertTrue(receipt["startedAt"].endswith("Z"), receipt["startedAt"])
        self.assertTrue(receipt["endedAt"].endswith("Z"), receipt["endedAt"])
        # platform.python_version(), not "%d.%d.%d" % sys.version_info[:3].
        # The two agree on every FINAL release and disagree on every
        # pre-release: the receipt writer records platform.python_version(),
        # which carries the suffix ("3.14.0rc2"), while the hand-built triple
        # drops it ("3.14.0"). Measured 2026-08-18 on 3.14.0rc2, where this
        # was the ONLY suite in the tree that failed on a newer interpreter,
        # and it failed for the test's reason rather than the product's.
        # A floor this project promises to exceed ("3.9 or newer") cannot be
        # checked against a release candidate by a suite that refuses to run
        # on one.
        self.assertEqual(receipt["toolVersions"]["python"], platform.python_version())
        self.assertTrue(receipt["environment"].strip())

    def test_show_names_the_trust_level_every_time(self):
        path, _code, _text = self.generate()
        proc = self.run_sbe("evidence", "show", path)
        text = self.output(proc)
        self.assertEqual(proc.returncode, 0, text)
        self.assertIn("LOCAL-ADVISORY", text,
                      "a local receipt read as authoritative is the reader being misled by "
                      "the layout rather than the content")
        self.assertIn("trust", text)

    def test_a_ci_run_id_on_a_clean_tree_reads_as_protected(self):
        """A CI run id and a clean tree are necessary and, since the check
        registry landed, no longer sufficient: the run must also BE a registered
        check. So this mints one, which is the only shape that can reach
        CI-CLAIMED now."""
        self.register_check()
        path = self.receipt_path("registered.json")
        proc = self.run_sbe("evidence", "run", "--check", "fixture-suite", "--out", path,
                            "--cwd", self.repo, env={"SBE_CI_RUN_ID": "gha-4471"})
        self.assertEqual(proc.returncode, 0, self.output(proc))
        self.assertEqual(self.load(path)["ciRunId"], "gha-4471")
        text = self.output(self.run_sbe("evidence", "show", path))
        self.assertIn("CI-CLAIMED", text, text)

    def test_a_free_form_command_under_a_ci_run_id_stays_advisory(self):
        """The calibration for the test above, and a control in its own right:
        `-- true` in CI on a clean tree used to read CI-CLAIMED, which handed
        the strongest label this project mints to a command nobody registered."""
        path, _code, _text = self.generate(env={"SBE_CI_RUN_ID": "gha-4471"})
        self.assertEqual(self.load(path)["ciRunId"], "gha-4471")
        text = self.output(self.run_sbe("evidence", "show", path))
        self.assertIn("LOCAL-ADVISORY", text, text)
        self.assertIn("free-form", text, text)


class TestStaleness(EvidenceFixture):
    def test_a_later_commit_touching_no_covered_file_carries_the_receipt_forward(self):
        """THE BOUNDARY MOVED HERE, deliberately, in roadmap row E83, and this
        test pins the new line rather than the old one.

        It used to read: a receipt counts only for the commit it was generated
        against, so ANY later commit staled it. That made evidence unable to
        survive being merged, which is the only way evidence ever reaches
        anybody: every receipt in this estate's own release dossier read as
        broken for exactly that reason. The rule now is the one `policy.py`
        already applied before it called `verify` at all: the bound commit is
        an ancestor of this head, and nothing the receipt COVERS changed in
        between. The commit here touches a file the receipt does not cover, so
        the receipt still describes every file it speaks for.

        What it must NOT be read as saying is that the command would still
        pass today. The note says so in the receipt's own words, and the two
        tests below hold the two edges that still refuse."""
        self.generate()
        write(self.repo, "docs/notes.md", "unrelated\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "a later commit")
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)
        blob = json.dumps(data["reasons"])
        self.assertIn("is an ancestor of the current head", blob, text)
        self.assertIn("it says nothing about code it never covered", blob, text)

    def test_a_later_commit_that_changes_a_covered_file_still_fails(self):
        """THE EDGE THAT STILL REFUSES. The covered file moves in the same
        commit, so the receipt is evidence about bytes that are gone."""
        self.generate()
        write(self.repo, "src/service.py", "def handle():\n    return 99\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "a later commit over covered code")
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertEqual(code, 1, text)
        self.assertIn("not the current head", json.dumps(data["reasons"]), text)

    def test_a_head_commit_this_repository_does_not_hold_is_no_data(self):
        """NO-DATA, never a pass and never a FAIL that names a cause it did not
        read. A commit nobody here has was never compared against anything, and
        `verify` may not dress that up as either verdict."""
        self.generate()
        data = self.load()
        data["headCommit"] = "0" * 40
        self.save(self.reseal(data))
        code, verdict, text = self.verify()
        self.assertEqual(verdict["verdict"], "NO-DATA", text)
        self.assertNotEqual(verdict["verdict"], "PASS", text)
        self.assertIn("does not hold", json.dumps(verdict["reasons"]), text)
        self.assertEqual(code, 0, text)

    def test_a_covered_file_changed_after_the_run_fails(self):
        self.generate()
        time.sleep(0.01)
        write(self.repo, "src/service.py", "def handle():\n    return 2\n")
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertEqual(code, 1, text)
        blob = json.dumps(data["reasons"])
        self.assertIn("src/service.py", blob, text)
        self.assertIn("after the evidence was made", blob, text)

    def test_a_deleted_covered_file_fails_rather_than_vanishing(self):
        self.generate()
        os.remove(os.path.join(self.repo, "src", "service.py"))
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertIn("no longer exists", json.dumps(data["reasons"]), text)


class TestDirtyTree(EvidenceFixture):
    def test_a_receipt_made_on_a_dirty_tree_is_no_data_and_never_a_pass(self):
        """A dirty-tree receipt covers a state that was never committed and
        nobody else can reproduce. Advisory is NO-DATA here, not a pass."""
        write(self.repo, "src/service.py", "def handle():\n    return 99\n")
        self.generate()
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "NO-DATA", text)
        self.assertNotEqual(data["verdict"], "PASS")
        self.assertTrue(self.load()["workingTreeDirty"], text)
        self.assertIn("dirty", json.dumps(data["reasons"]), text)

    def test_strict_makes_that_no_data_block(self):
        write(self.repo, "src/service.py", "def handle():\n    return 99\n")
        self.generate()
        code, data, text = self.verify(extra=("--strict",))
        self.assertEqual(data["verdict"], "NO-DATA", text)
        self.assertEqual(code, 1,
                         "under --strict a NO-DATA must block, or protected CI is protecting "
                         "nothing")

    def test_a_ci_run_id_does_not_promote_a_dirty_run(self):
        write(self.repo, "src/service.py", "def handle():\n    return 99\n")
        path, _code, _text = self.generate(env={"SBE_CI_RUN_ID": "gha-9001"})
        text = self.output(self.run_sbe("evidence", "show", path))
        self.assertIn("LOCAL-ADVISORY", text,
                      "a CI job over uncommitted edits is a local run wearing a badge")


class TestMalformed(EvidenceFixture):
    def test_a_receipt_that_is_not_json_fails(self):
        self.save_raw = write(self.out, "receipt.json", "{not json at all")
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertEqual(code, 1, text)
        self.assertIn("does not parse", json.dumps(data["reasons"]), text)

    def test_an_unknown_schema_version_fails_rather_than_being_ignored(self):
        self.generate()
        data = self.load()
        data["schemaVersion"] = "9.9"
        self.reseal(data)
        self.save(data)
        code, out, text = self.verify()
        self.assertEqual(out["verdict"], "FAIL", text)
        self.assertIn("not one this build reads", json.dumps(out["reasons"]), text)

    def test_a_missing_receipt_is_reported_and_never_a_pass(self):
        code, data, text = self.verify(path=os.path.join(self.out, "nothing-here.json"))
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertIn("absent evidence is NO-DATA, never a pass", json.dumps(data["reasons"]),
                      text)

    def test_a_json_array_is_not_a_receipt(self):
        self.save_raw = write(self.out, "receipt.json", "[1, 2, 3]")
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertIn("a receipt is a JSON object", json.dumps(data["reasons"]), text)


class TestVacuousFields(EvidenceFixture):
    def test_a_vacuous_required_field_fails_and_names_the_field(self):
        """Resealed on purpose, so the required-field control is the only one
        that can fire and the fixture proves that control rather than the seal."""
        self.generate()
        data = self.load()
        data["environment"] = "   "
        self.reseal(data)
        self.save(data)
        code, out, text = self.verify()
        self.assertEqual(out["verdict"], "FAIL", text)
        blob = json.dumps(out["reasons"])
        self.assertIn("environment", blob, text)
        self.assertNotIn("does not match the seal", blob,
                         "the seal was recomputed, so only the vacuity control should fire")

    def test_a_placeholder_is_not_an_answer(self):
        self.generate()
        data = self.load()
        data["generator"] = "TODO"
        self.reseal(data)
        self.save(data)
        code, out, text = self.verify()
        self.assertEqual(out["verdict"], "FAIL", text)
        self.assertIn("generator", json.dumps(out["reasons"]), text)

    def test_a_zero_exit_code_is_an_answer_and_not_an_absence(self):
        """The direction of error that would break honest work: `0` and `False`
        record something, and a vacuity test using truthiness would reject every
        passing run."""
        self.generate()
        data = self.load()
        self.assertEqual(data["exitCode"], 0)
        self.assertIs(data["workingTreeDirty"], False)
        code, out, text = self.verify()
        self.assertEqual(out["verdict"], "PASS", text)


class TestNoRawOutputLeak(EvidenceFixture):
    SECRET = "sk-live-DEADBEEFCAFE0123456789"

    def _printer(self, stream="stdout"):
        """A command that prints the secret WITHOUT carrying it in its argv.

        The first version of this fixture passed the secret on the command line
        and failed, correctly: at the time, `argv` was recorded fully verbatim,
        so a credential typed into a command landed in the receipt no matter
        what the output policy said. `redact_argv()` now narrows that (see the
        fixtures below and `docs/KNOWN-LIMITS.md`), but the narrowing only
        catches KNOWN secret shapes, so this fixture still keeps its secret out
        of argv entirely rather than depending on the pattern list catching it.
        This fixture tests the thing the output policy actually promises: what
        the command PRINTS.
        """
        holder = write(self.out, "held-secret.txt", self.SECRET)
        return write(self.out, "print_%s.py" % stream,
                     "import io, sys\n"
                     "body = io.open(%r, encoding='utf-8').read()\n"
                     "sys.%s.write(body)\n" % (holder, stream))

    def test_a_secret_printed_by_the_command_never_reaches_the_receipt(self):
        """A receipt is the one artifact everybody is encouraged to share, so a
        token printed by the command must not be persisted into it. Digests
        prove the same bytes came back and carry none of them."""
        path, _code, _text = self.generate(command=["python3", self._printer("stdout")])
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn(self.SECRET, raw,
                         "the receipt persisted the command's raw output, which ships the "
                         "secret to everybody the receipt is shown to")
        self.assertNotIn("DEADBEEF", raw, raw)
        import hashlib
        expected = hashlib.sha256(self.SECRET.encode("utf-8")).hexdigest()
        self.assertEqual(self.load(path)["stdoutSha256"], expected,
                         "the digest must still prove which bytes came back")
        self.assertEqual(self.load(path)["stdoutBytes"], len(self.SECRET))

    def test_stderr_is_digested_too(self):
        path, _code, _text = self.generate(command=["python3", self._printer("stderr")])
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn(self.SECRET, raw, "stderr leaked into the receipt")
        import hashlib
        self.assertEqual(self.load(path)["stderrSha256"],
                         hashlib.sha256(self.SECRET.encode("utf-8")).hexdigest())
        self.assertEqual(self.load(path)["stdoutSha256"],
                         hashlib.sha256(b"").hexdigest(),
                         "an empty stream still gets a digest, so silence and 'not recorded' "
                         "cannot be confused")

    def test_a_secret_shaped_argv_token_is_redacted_not_recorded_verbatim(self):
        """FLIPPED (was `test_argv_is_recorded_verbatim_which_is_a_limit_not_a_leak_to_ignore`,
        which pinned the opposite of this on purpose, as a stated limit). `argv`
        used to be recorded fully verbatim, so a credential passed ON the
        command line was persisted whole. `redact_argv()`
        (`src/brothersbe/evidence.py`) now masks any token matching a shape
        `tools/sbe_telemetry.py`'s own `SECRET_PATTERNS` already knows, before
        the receipt is written, and names WHICH shape matched rather than
        printing a bare "[REDACTED]"."""
        path, _code, _text = self.generate(
            command=["python3", "-c", "pass", self.SECRET])
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn(self.SECRET, raw,
                         "a secret-shaped argv token reached the receipt: redact_argv did not "
                         "catch a shape its own mirrored pattern list knows")
        self.assertIn("[REDACTED:api-key]", raw,
                      "the marker names the shape that matched, not a bare [REDACTED]")
        data = self.load(path)
        self.assertEqual(data["argvRedactions"], 1, data["argv"])
        self.assertNotIn(self.SECRET, json.dumps(data["argv"]), data["argv"])

    def test_a_receipt_with_redactions_still_verifies_pass_on_an_untouched_tree(self):
        """Redaction changes what `argv` records; it must not change what the
        seal covers or what `verify` decides. `argvRedactions` sits in
        `SEALED_FIELDS` beside `argv` itself, so a redacted receipt seals and
        verifies exactly like any other."""
        self.generate(command=["python3", "-c", "pass", self.SECRET])
        code, data, text = self.verify()
        self.assertEqual(data["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)

    def test_zero_redactions_keeps_argv_byte_identical_to_what_ran(self):
        """The common case: no secret-shaped token anywhere in argv. The recorded
        `argv` must be byte-identical to what actually ran, not just equal in
        meaning, and `argvRedactions` names that plainly as 0 rather than
        leaving a reader to infer verbatim-ness from an empty diff."""
        command = ["python3", "-c", "print('the suite ran')"]
        path, _code, _text = self.generate(command=command)
        data = self.load(path)
        self.assertEqual(data["argvRedactions"], 0, data["argv"])
        self.assertEqual(data["argv"], command,
                         "zero redactions must mean argv is exactly what ran, not merely "
                         "unmarked")


class TestAccessAndTimeout(EvidenceFixture):
    """DEFECT: `verify()` opened a receipt path with no access check, so a FIFO
    where a receipt was expected hung the command forever with no verdict in
    either mode; and `generate()`'s subprocess.run carried no timeout, so a
    command that hung, hung the wrapper. Both fixtures bound their own
    subprocess call from the OUTSIDE (`run_sbe(..., timeout=N)`), so a
    regression shows up as this test failing on TimeoutExpired rather than as
    the whole suite hanging."""

    def test_a_fifo_receipt_is_refused_in_bounded_time_json_mode(self):
        fifo_path = self.receipt_path("ran-receipt.json")
        os.mkfifo(fifo_path)
        try:
            proc = self.run_sbe("evidence", "verify", fifo_path, "--cwd", self.repo,
                                "--json", timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("sbe evidence verify --json hung on a FIFO instead of refusing it")
        text = self.output(proc)
        blob = text[text.index("{"):text.rindex("}") + 1] if "{" in text else "{}"
        data = json.loads(blob)
        self.assertEqual(data["verdict"], "FAIL", text)
        self.assertNotEqual(proc.returncode, 0, text)
        self.assertIn("not a regular file", json.dumps(data["reasons"]), text)

    def test_a_fifo_receipt_is_refused_in_bounded_time_text_mode(self):
        fifo_path = self.receipt_path("ran-receipt.json")
        os.mkfifo(fifo_path)
        try:
            proc = self.run_sbe("evidence", "verify", fifo_path, "--cwd", self.repo,
                                timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("sbe evidence verify hung on a FIFO instead of refusing it")
        text = self.output(proc)
        self.assertTrue(text.startswith("FAIL"), text)
        self.assertNotEqual(proc.returncode, 0, text)
        self.assertIn("not a regular file", text, text)

    def test_run_with_timeout_over_a_slow_command_produces_no_pass_receipt(self):
        """The child is killed, no exit code was ever observed, and no receipt
        is written that a later `verify` could read as PASS. There is no
        default: this test passes `--timeout` explicitly, the way an operator
        would have to."""
        out = self.receipt_path()
        argv = ["evidence", "run", "--out", out, "--cwd", self.repo, "--base", self.base,
                "--timeout", "2", "--", "python3", "-c", "import time; time.sleep(30)"]
        try:
            proc = self.run_sbe(*argv, timeout=15)
        except subprocess.TimeoutExpired:
            self.fail("sbe evidence run --timeout did not bound the child; the wrapper hung")
        text = self.output(proc)
        self.assertNotEqual(proc.returncode, 0, text)
        self.assertFalse(os.path.exists(out),
                         "a timed-out run must not leave a receipt behind to verify later")
        self.assertIn("timeout", text.lower(), text)

    def test_run_without_timeout_is_unaffected(self):
        """No default: an ordinary run with no --timeout at all must behave
        exactly as it always has."""
        path, code, text = self.generate()
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(path), text)


class TestTheInvariant(EvidenceFixture):
    """One sentence, and the whole control is built to keep it true: a receipt
    only counts as evidence for the commit it was generated against, by a
    wrapper that ran the command itself."""

    def test_there_is_no_way_to_hand_the_wrapper_a_duration_or_an_exit_code(self):
        proc = self.run_sbe("evidence", "run", "--out", self.receipt_path(),
                            "--duration", "12", "--", "python3", "-c", "pass")
        self.assertNotEqual(proc.returncode, 0,
                            "a flag that lets the caller state a duration would reopen the "
                            "whole defect")

    def test_run_without_a_command_refuses_rather_than_writing_an_empty_receipt(self):
        proc = self.run_sbe("evidence", "run", "--out", self.receipt_path(),
                            "--cwd", self.repo)
        self.assertEqual(proc.returncode, 2, self.output(proc))
        self.assertFalse(os.path.exists(self.receipt_path()),
                         "a refused run must not leave a receipt behind")

    def test_the_seal_covers_the_fields_a_forger_would_choose(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            for field in ("argv", "argvRedactions", "exitCode", "durationSeconds",
                          "headCommit", "stdoutSha256", "coveredFiles", "workingTreeDirty"):
                self.assertIn(field, mod.SEALED_FIELDS,
                              "%s is forgeable without breaking the seal" % field)
        finally:
            sys.path.pop(0)


class TestRedactArgv(unittest.TestCase):
    """Direct, subprocess-free coverage of `redact_argv()`: no git repo needed,
    because the property under test lives entirely in the function's own
    input/output, not in anything a run touches."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from brothersbe import evidence as mod
        import sbe_telemetry
        self.mod = mod
        self.telemetry = sbe_telemetry

    def tearDown(self):
        sys.path.pop(0)
        sys.path.pop(0)

    def test_returns_three_values_not_two(self):
        """Two values here would read as a possible (verdict, evidence) pair to
        `evals/test_no_data_class.py`, the honesty meta-test, and it is right to
        refuse that shape from anything not registered as a verdict source."""
        result = self.mod.redact_argv(["python3"])
        self.assertEqual(len(result), 3, result)
        redacted, count, note = result
        self.assertIsInstance(redacted, list)
        self.assertIsInstance(count, int)
        self.assertIsInstance(note, str)
        self.assertTrue(note, "the third value must say something, not stand in as padding")

    def test_mirrors_the_telemetry_pattern_list_rather_than_a_second_one(self):
        """Imported, not reinvented: the same list object the telemetry
        redactor scans an operator's own messages with."""
        self.assertIs(self.mod.SECRET_PATTERNS, self.telemetry.SECRET_PATTERNS,
                      "evidence.py holds a copy of the pattern list rather than the imported "
                      "one; a pattern added to tools/sbe_telemetry.py would silently not apply "
                      "here")
        self.assertEqual(len(self.mod.ARGV_REDACTION_SHAPES), len(self.mod.SECRET_PATTERNS),
                         "one shape name per imported pattern, or a marker would name the "
                         "wrong shape")

    def test_a_plain_token_with_no_secret_shape_is_untouched(self):
        redacted, count, _note = self.mod.redact_argv(["pytest", "-q", "tests/"])
        self.assertEqual(redacted, ["pytest", "-q", "tests/"])
        self.assertEqual(count, 0)

    def test_each_known_shape_produces_its_own_named_marker(self):
        planted = {
            "api-key": "sk-live-DEADBEEFCAFE0123456789",
            "aws-key-id": "AKIAABCDEFGHIJKLMNOP",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0",
        }
        for shape, token in planted.items():
            redacted, count, _note = self.mod.redact_argv([token])
            self.assertEqual(count, 1, "%s: %r" % (shape, redacted))
            self.assertNotIn(token, redacted[0], "%s: %r" % (shape, redacted))
            self.assertIn("[REDACTED:%s]" % shape, redacted[0],
                          "%s matched but not under its own name: %r" % (shape, redacted))

    def test_matches_per_argument_not_across_a_joined_command_line(self):
        """A shape split across two argv entries by ordinary shell word-splitting
        must not be reassembled and caught: that would be matching a boundary no
        shell ever produced. Each half here is, on its own, too short for the
        api-key pattern (which needs 12+ characters after `sk-`); only their
        concatenation would satisfy it."""
        redacted, count, _note = self.mod.redact_argv(["sk-AAAAAA", "AAAAAA"])
        self.assertEqual(count, 0,
                         "each half is too short alone to match the shape; a nonzero count "
                         "would mean the scan joined the two arguments before matching")
        self.assertEqual(redacted, ["sk-AAAAAA", "AAAAAA"])



class TestSchemaCompat(EvidenceFixture):
    """A receipt is judged by its own version's contract.

    Schema "1.0" predates argvRedactions and "1.1" predates the declared check
    kind. An honest receipt of either version must not FAIL for missing a field
    its version never defined, and a receipt of the version that DID define it
    must. The transforms below (rewrite the version, drop the fields that
    version postdates, reseal) are the entire difference between the versions,
    which is what makes these fixtures a statement about version handling and
    nothing else.
    """

    def _as_v10(self):
        self.generate()
        data = self.load()
        data["schemaVersion"] = "1.0"
        del data["argvRedactions"]
        del data["checkKinds"]
        del data["checkKindsSource"]
        self.save(self.reseal(data))

    def _as_v11(self):
        """A legacy receipt exactly as the previous build wrote them: everything
        1.1 defined, and nothing 1.2 added."""
        self.generate()
        data = self.load()
        data["schemaVersion"] = "1.1"
        del data["checkKinds"]
        del data["checkKindsSource"]
        self.save(self.reseal(data))

    def test_a_1_0_receipt_is_not_failed_for_a_field_its_version_never_had(self):
        self._as_v10()
        _code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "PASS", text)
        self.assertNotIn("argvRedactions", text)

    def test_a_1_1_receipt_missing_the_redaction_count_fails_by_name(self):
        self.generate()
        data = self.load()
        del data["argvRedactions"]
        self.save(self.reseal(data))
        _code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "FAIL", text)
        self.assertIn("argvRedactions", text)

    def test_a_1_1_receipt_is_not_failed_for_the_check_kind_field_it_predates(self):
        """The forward-only promise, in the direction that costs something: the
        1.2 bump must not turn every receipt already on disk into a FAIL."""
        self._as_v11()
        _code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "PASS", text)
        self.assertNotIn("checkKindsSource", json.dumps(blob["reasons"]), text)

    def test_a_1_2_receipt_missing_the_kind_provenance_fails_by_name(self):
        self.generate()
        data = self.load()
        del data["checkKindsSource"]
        self.save(self.reseal(data))
        _code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "FAIL", text)
        self.assertIn("checkKindsSource", text)

    def test_new_receipts_stamp_the_evidence_version(self):
        self.generate()
        self.assertEqual(self.load()["schemaVersion"], "1.4")
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            self.assertEqual(mod.EVIDENCE_SCHEMA_VERSION, "1.4")
            self.assertEqual(mod.KNOWN_SCHEMA_VERSIONS[-1], "1.4",
                             "the newest version must be last: _fields_for reads this tuple "
                             "as the version timeline")
            for older in ("1.0", "1.1", "1.2", "1.3"):
                self.assertIn(older, mod.KNOWN_SCHEMA_VERSIONS,
                              "a bump that stops reading an older receipt is a rewrite, not a "
                              "forward-only bump")
        finally:
            sys.path.pop(0)


class TestDeclaredCheckKind(EvidenceFixture):
    """THE DEFECT THIS FIELD EXISTS FOR, stated here where it is generated and
    pinned at the consumer in `tools/test_sbe_status.py`: which check a receipt
    is evidence FOR used to be worked out by substring-matching the recorded
    command line, so `/bin/cat tests/test_design_of_gate_score.txt` cleared the
    design, gate and score obligations at once. The kind is now declared, and
    sealed so it cannot be added to a receipt after the fact."""

    def test_a_run_without_kind_declares_nothing_and_says_so(self):
        path, code, text = self.generate()
        self.assertEqual(code, 0, text)
        data = self.load(path)
        self.assertEqual(data["checkKinds"], [],
                         "a run that declared no kind must record an empty list, not a guess "
                         "derived from its own command line")
        self.assertIn("no --kind was given", data["checkKindsSource"])
        self.assertIn("clears no design, gate or score obligation", text,
                      "the operator must be told at the moment of writing, not only when a "
                      "later command reports a missing obligation: %s" % text)

    def test_a_declared_kind_is_recorded_and_survives_verify(self):
        path, code, text = self.generate(extra=("--kind", "design"))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.load(path)["checkKinds"], ["design"])
        code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "PASS", text)
        self.assertEqual(code, 0, text)

    def test_repeated_kinds_are_recorded_sorted_and_deduplicated(self):
        path, _code, text = self.generate(
            extra=("--kind", "score", "--kind", "design", "--kind", "score"))
        self.assertEqual(self.load(path)["checkKinds"], ["design", "score"], text)

    def test_the_command_line_is_never_read_as_a_declaration(self):
        """The exact shape of the old bypass, at the generator: a command whose
        argv spells all three obligations declares none of them."""
        named = write(self.out, "test_design_of_gate_score.txt", "not a check\n")
        path, _code, text = self.generate(command=["/bin/cat", named])
        data = self.load(path)
        self.assertEqual(data["checkKinds"], [],
                         "the wrapper inferred a check kind from the words in the command "
                         "line, which is the defect this field replaced: %r" % data["argv"])

    def test_an_unknown_kind_is_refused_and_writes_no_receipt(self):
        out = self.receipt_path("unknown-kind.json")
        proc = self.run_sbe("evidence", "run", "--out", out, "--cwd", self.repo,
                            "--kind", "smoke", "--", "python3", "-c", "pass")
        text = self.output(proc)
        self.assertEqual(proc.returncode, 2, text)
        self.assertFalse(os.path.exists(out),
                         "a refused kind must not leave a receipt behind")

    def test_adding_a_kind_to_an_existing_receipt_breaks_the_seal(self):
        """The forgery this field would otherwise invite: take a real receipt
        for a command that checked nothing, and type the obligation into it."""
        self.generate()
        doctored = self.load()
        self.assertEqual(doctored["checkKinds"], [])
        doctored["checkKinds"] = ["design", "gate", "score"]
        self.save(doctored)
        code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "FAIL", text)
        self.assertEqual(code, 1, text)
        self.assertIn("does not match the seal", json.dumps(blob["reasons"]), text)

    def test_a_kind_this_build_does_not_know_fails_by_name(self):
        """Resealed on purpose, so the kind control is the only one that can
        fire and this fixture proves that control rather than the seal."""
        self.generate()
        data = self.load()
        data["checkKinds"] = ["deployment"]
        self.save(self.reseal(data))
        code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "FAIL", text)
        blob_text = json.dumps(blob["reasons"])
        self.assertIn("deployment", blob_text, text)
        self.assertNotIn("does not match the seal", blob_text,
                         "the seal was recomputed, so only the kind control should fire")

    def test_a_kind_field_that_is_not_a_list_fails_rather_than_being_ignored(self):
        self.generate()
        data = self.load()
        data["checkKinds"] = "design"
        self.save(self.reseal(data))
        _code, blob, text = self.verify()
        self.assertEqual(blob["verdict"], "FAIL", text)
        self.assertIn("JSON array", json.dumps(blob["reasons"]), text)

    def test_show_names_the_declared_kind_and_its_provenance(self):
        path, _code, _text = self.generate(extra=("--kind", "gate"))
        text = self.output(self.run_sbe("evidence", "show", path))
        self.assertIn("check kinds", text)
        self.assertIn("gate", text)
        self.assertIn("never a proof", text,
                      "show must not let a declared kind read as a verified one: %s" % text)

    def test_the_seal_covers_the_declared_kind(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            for field in ("checkKinds", "checkKindsSource"):
                self.assertIn(field, mod.SEALED_FIELDS,
                              "%s is forgeable without breaking the seal" % field)
        finally:
            sys.path.pop(0)

    def test_the_reader_never_infers_a_kind_from_argv(self):
        """`declared_kinds` is the ONE reader of the field, so this pins the
        property at the function every consumer goes through."""
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            spelled = {"argv": ["/bin/cat", "tests/test_design_of_gate_score.txt"]}
            self.assertEqual(mod.declared_kinds(spelled), set(),
                             "a receipt with no checkKinds field must yield no kind, however "
                             "its command line reads")
            self.assertIsNotNone(mod.kind_declaration_gap(spelled),
                                 "a legacy receipt must carry a sentence saying why it clears "
                                 "nothing, never a silent empty set")
            self.assertEqual(mod.declared_kinds({"checkKinds": ["gate"],
                                                 "argv": ["/bin/cat", "design.txt"]}),
                             set(["gate"]),
                             "the declared field is the only source; argv must not add to it")
        finally:
            sys.path.pop(0)


class TestWriteReceiptAndMintDefault(EvidenceFixture):
    """`write_receipt`, `mint_default` and `mint_default_many`: the writer
    path CR-08 gave `sbe verify`'s default evidence minting
    (`cli.py:_mint_evidence`), added here rather than in `cli.py` because the
    ADR that settled CR-08 (`design/lifecycle-blockers/03-adr.md`) requires
    minting to route through this module's own writer, never a parallel one.
    These are the receipt-contract tests these three functions did not have
    before: `TestTheSoundCase` above pins the same shape (a generated
    receipt verifies PASS at the same commit, and records what the run
    actually did) for `generate()` reached through the CLI; this class pins
    it for these three reached directly, the way `_mint_evidence` reaches
    them.
    """

    def _mod(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        try:
            from brothersbe import evidence as mod
            return mod
        finally:
            sys.path.pop(0)

    def test_write_receipt_creates_missing_parent_directories(self):
        mod = self._mod()
        nested = os.path.join(self.out, "a", "b", "c", "receipt.json")
        self.assertFalse(os.path.isdir(os.path.dirname(nested)))
        mod.write_receipt({"hello": "world"}, nested)
        self.assertTrue(os.path.exists(nested))
        with io.open(nested, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"hello": "world"})

    def test_mint_default_writes_to_the_fixed_per_kind_path(self):
        mod = self._mod()
        evidence_dir = os.path.join(self.out, ".sbe", "evidence")
        path = mod.mint_default(self.repo, "gate",
                                ["python3", "-c", "print('the suite ran')"], evidence_dir,
                                base=self.base)
        self.assertEqual(path, os.path.join(evidence_dir, "gate.json"))
        self.assertTrue(os.path.exists(path))

    def test_mint_default_receipt_verifies_pass_at_the_same_commit(self):
        """The receipt contract `TestTheSoundCase.test_a_generated_receipt_
        verifies_pass_at_the_same_commit` pins for `generate()` through the
        CLI, pinned here for the path `sbe verify`'s default minting uses."""
        mod = self._mod()
        evidence_dir = os.path.join(self.out, ".sbe", "evidence")
        path = mod.mint_default(self.repo, "design",
                                ["python3", "-c", "print('the suite ran')"], evidence_dir,
                                base=self.base)
        result = mod.verify(path, cwd=self.repo)
        self.assertEqual(result["verdict"], "PASS", result["reasons"])

    def test_mint_default_records_what_the_run_actually_did(self):
        """The same contract `TestTheSoundCase.test_the_receipt_records_what_
        the_run_actually_did` pins for `generate()`, pinned here."""
        mod = self._mod()
        evidence_dir = os.path.join(self.out, ".sbe", "evidence")
        path = mod.mint_default(self.repo, "score", ["python3", "-c", "import sys; sys.exit(7)"],
                                evidence_dir, base=self.base)
        receipt = mod.load(path)
        self.assertEqual(receipt["exitCode"], 7)
        self.assertEqual(receipt["headCommit"], self.head)
        self.assertEqual(receipt["checkKinds"], ["score"])
        self.assertGreaterEqual(receipt["durationSeconds"], 0)

    def test_mint_default_raises_on_an_unknown_kind_rather_than_writing_a_false_declaration(self):
        mod = self._mod()
        evidence_dir = os.path.join(self.out, ".sbe", "evidence")
        with self.assertRaises(mod.ReceiptUnreadable):
            mod.mint_default(self.repo, "not-a-real-kind", ["python3", "-c", "pass"],
                             evidence_dir, base=self.base)
        self.assertFalse(os.path.exists(os.path.join(evidence_dir, "not-a-real-kind.json")),
                         "a refused mint must not leave a receipt behind")

    def test_mint_default_many_generates_every_pair_before_writing_any(self):
        """The regression this class exists to pin. `evidence_dir` sits
        INSIDE `self.repo` here, on purpose (unlike the other tests above,
        which write outside the repo): that is what exposes the defect a
        naive generate-then-write loop has. The first receipt written
        becomes a new untracked file in `self.repo`, and if the SECOND
        receipt's dirty check runs after that write, it reads the tree as
        dirty because of the FIRST receipt's own file, not because of
        anything the caller did. `self.repo` is clean at `self.head`
        (`EvidenceFixture.setUp` commits everything), so both receipts must
        read `workingTreeDirty: False` when minted together correctly."""
        mod = self._mod()
        evidence_dir = os.path.join(self.repo, ".sbe", "evidence")
        pairs = [("design", ["python3", "-c", "pass"]), ("gate", ["python3", "-c", "pass"])]
        written, failures = mod.mint_default_many(self.repo, pairs, evidence_dir, base=self.base)
        self.assertEqual(failures, [], failures)
        self.assertEqual(len(written), 2, written)
        for path in written:
            receipt = mod.load(path)
            self.assertIs(receipt["workingTreeDirty"], False,
                         "%s read dirty, which means one receipt minted in this batch was "
                         "read as the reason for another one's dirty tree: %r"
                         % (path, receipt["workingTreeDetail"]))

    def test_mint_default_many_isolates_one_failure_from_the_others(self):
        mod = self._mod()
        evidence_dir = os.path.join(self.out, ".sbe", "evidence")
        pairs = [("design", ["python3", "-c", "pass"]),
                 ("gate", ["not-a-real-executable-on-purpose"])]
        written, failures = mod.mint_default_many(self.repo, pairs, evidence_dir, base=self.base)
        self.assertEqual(len(written), 1, written)
        self.assertTrue(written[0].endswith("design.json"), written)
        self.assertEqual(len(failures), 1, failures)
        self.assertEqual(failures[0][0], "gate")
        self.assertFalse(os.path.exists(os.path.join(evidence_dir, "gate.json")),
                         "a failed pair must not leave a receipt behind")

    def test_run_subcommand_still_reports_a_write_failure_after_the_write_receipt_refactor(self):
        """`main`'s `run` subcommand now calls `write_receipt` instead of
        writing inline; this pins that the CLI still reports an unwritable
        `--out` rather than losing the message in the refactor."""
        blocked = os.path.join(self.out, "not-a-directory")
        with io.open(blocked, "w", encoding="utf-8") as fh:
            fh.write("occupying this path so it cannot become a directory\n")
        _path, code, text = self.generate(out=os.path.join(blocked, "receipt.json"))
        self.assertNotEqual(code, 0, text)
        self.assertIn("could not be written", text, text)


class TestHelpMeansHelp(unittest.TestCase):
    """`sbe evidence ... -h` printed the right usage and exited 2: the module
    caught argparse's SystemExit(0) for help and folded it into exit_usage,
    and a LEADING `-h` (`sbe evidence -h`) never even reached this module,
    refused by the top-level parser instead. Help is not an error. Calibrated
    by reinjecting the bare `except SystemExit: return exit_usage` and the
    argparse-first dispatch in cli.py: each fixture here failed on a
    returncode of 2 where 0 was asserted, before the fix was restored,
    the restore verified against the pre-recorded `git hash-object` of the
    fixed files."""

    def run_sbe(self, *argv):
        out = subprocess.run([sys.executable, SBE] + list(argv),
                             capture_output=True, text=True,
                             cwd=tempfile.gettempdir())
        return out.returncode, out.stdout, out.stderr

    def test_help_exits_0_with_usage_on_every_evidence_surface(self):
        for argv in (("evidence", "-h"), ("evidence", "--help"),
                     ("evidence", "run", "-h"), ("evidence", "verify", "-h"),
                     ("evidence", "show", "-h")):
            code, stdout, stderr = self.run_sbe(*argv)
            self.assertEqual(code, 0, "sbe %s exited %d: %s"
                             % (" ".join(argv), code, stdout + stderr))
            self.assertIn("usage", (stdout + stderr).lower(),
                          "sbe %s printed no usage: %r" % (" ".join(argv), stdout + stderr))

    def test_help_on_run_writes_no_receipt(self):
        """`evidence run -h` must answer and stop; before anything, no receipt
        file may appear where --out points."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        receipt = os.path.join(tmp, "receipt.json")
        code, stdout, stderr = self.run_sbe("evidence", "run", "--out", receipt, "-h")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertFalse(os.path.exists(receipt),
                         "evidence run -h wrote the receipt it was only asked to describe")

    def test_a_genuinely_bad_flag_still_exits_2(self):
        for argv in (("evidence", "--bogus"), ("evidence", "run", "--bogus"),
                     ("evidence", "verify", "--bogus")):
            code, stdout, stderr = self.run_sbe(*argv)
            self.assertEqual(code, 2, "sbe %s exited %d, not the usage error 2: %s"
                             % (" ".join(argv), code, stdout + stderr))


# ---------------------------------------------------------------------------
# The evidence side of the two-machine defect: docs/adr/2026-08-12-handover-
# across-two-machines.md. `headCommit` self-invalidates the identical way
# `handover.py`'s `headSha` does (see `docs/KNOWN-LIMITS.md`), for a receipt
# committed INSIDE the repository it covers -- deliberately not
# `EvidenceFixture`, which keeps receipts outside the repo on purpose so most
# fixtures never have to think about the tree's own dirtiness.
# ---------------------------------------------------------------------------

class TestReceiptSurvivesItsOwnIntroducingCommit(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "app.py", "x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "seed")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _run_sbe(self, *argv):
        proc = subprocess.run([sys.executable, SBE] + list(argv), cwd=self.repo,
                              capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def _in_repo_receipt(self, out_rel=os.path.join(".sbe", "evidence", "r.json")):
        code, text = self._run_sbe("evidence", "run", "--out", out_rel, "--cwd", self.repo,
                                   "--covers", "app.py", "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, text)
        return out_rel

    def test_verifying_a_committed_receipt_after_committing_only_the_receipt(self):
        """THE REPRODUCTION. Must FAIL before the fix, PASS after."""
        out_rel = self._in_repo_receipt()
        status = git(self.repo, "status", "--porcelain", "-uall")
        touched = sorted(l[3:] for l in status.splitlines() if l.strip())
        self.assertEqual(touched, [".sbe/evidence/r.json"],
                         "only the receipt should be uncommitted: %s" % touched)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "receipt only")

        code, text = self._run_sbe("evidence", "verify", os.path.join(self.repo, out_rel),
                                   "--cwd", self.repo)
        self.assertEqual(code, 0, "a receipt committed alone must still verify PASS: %s" % text)
        self.assertIn("PASS", text)

    def test_receipt_committed_with_other_work_still_fails_as_stale(self):
        """THE GUARD: refuses as stale identically before and after the fix."""
        out_rel = self._in_repo_receipt()
        write(self.repo, "app.py", "x = 1\nw = 4\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "receipt plus more real work")

        code, text = self._run_sbe("evidence", "verify", os.path.join(self.repo, out_rel),
                                   "--cwd", self.repo)
        self.assertNotEqual(code, 0, "a commit bundling the receipt with real work must "
                                     "still fail as stale: %s" % text)
        self.assertIn("is not the current head", text)


class TestReceiptFreshnessAcrossCommitsAndScopes(unittest.TestCase):
    """ROADMAP ROW E83: the product read its own freshly minted receipts as
    stale. Three separate defects produced that one symptom, and each test
    below drives ONE of them backwards. Every test here fails on the code as it
    stood before the row and passes after.

    The repository is laid out the way this estate's own release dossier is: a
    dossier directory with its own `.sbe/evidence` store inside it, so the
    scoped read (`sbe status <dossier>`, whose cwd is the dossier and not the
    repository root) is a real case here rather than a described one."""

    DOSSIER = os.path.join("docs", "dossiers", "v1")

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "fixture")
        write(self.repo, "app.py", "x = 1\n")
        write(self.repo, os.path.join(self.DOSSIER, "01-purpose.md"), "why this exists\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "seed")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _run_sbe_at(self, *argv, **kwargs):
        proc = subprocess.run([sys.executable, SBE] + list(argv), cwd=kwargs.get("cwd",
                              self.repo), capture_output=True, text=True, timeout=180)
        return proc.returncode, proc.stdout + proc.stderr

    def _mint(self, name, covers, cwd=None, out=None):
        """One receipt, committed on its own, exactly the way `sbe evidence run`
        then a commit leaves it. `covers` is spelled relative to `cwd`, the same
        way the wrapper records it. `out` writes the receipt outside the
        repository instead, for the cases that must survive a branch switch."""
        cwd = cwd or self.repo
        full = out or os.path.join(self.repo, self.DOSSIER, ".sbe", "evidence",
                                   name + ".json")
        code, text = self._run_sbe_at("evidence", "run", "--out", full, "--cwd", cwd,
                                   "--covers", covers, "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, text)
        if out is None:
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-qm", "mint %s" % name)
        return full

    def test_a_receipt_verified_from_the_dossier_it_lives_in_is_not_stale(self):
        """DEFECT 1: the digest exemption was spelled relative to the caller's
        cwd while the digest itself is spelled from the repository root, so
        every scoped read exempted a name no digest contained and read every
        receipt as stale."""
        receipt = self._mint("design", "01-purpose.md", cwd=os.path.join(self.repo,
                                                                        self.DOSSIER))
        scope = os.path.join(self.repo, self.DOSSIER)
        code, text = self._run_sbe_at("evidence", "verify", receipt, "--cwd", scope, cwd=scope)
        self.assertEqual(code, 0, "a receipt read from the dossier it lives in must not be "
                                  "stale: %s" % text)
        self.assertIn("PASS", text)

    def test_a_root_relative_covered_path_read_from_the_dossier_still_resolves(self):
        """DEFECT 1 AGAIN, in the other reader. A receipt minted at the
        repository root records its covered paths from the root; a dossier
        scoped read joins them onto the dossier instead, and the file it points
        at exists nowhere. The product reported a file that had never been
        touched as one that "no longer exists" and threw the receipt away."""
        receipt = self._mint("design", "docs/dossiers/v1/01-purpose.md")
        scope = os.path.join(self.repo, self.DOSSIER)
        code, text = self._run_sbe_at("evidence", "verify", receipt, "--cwd", scope, cwd=scope)
        self.assertNotIn("no longer exists", text)
        self.assertEqual(code, 0, "a root relative covered path must resolve for a scoped "
                                  "reader too: %s" % text)

    def test_a_covered_path_that_resolves_nowhere_is_still_reported_missing(self):
        """THE GUARD on the test above: the repository root is tried second,
        not treated as an answer. A path that exists in neither place is still
        a broken claim."""
        receipt = self._mint("design", "docs/dossiers/v1/01-purpose.md")
        os.remove(os.path.join(self.repo, self.DOSSIER, "01-purpose.md"))
        scope = os.path.join(self.repo, self.DOSSIER)
        code, text = self._run_sbe_at("evidence", "verify", receipt, "--cwd", scope, cwd=scope)
        self.assertNotEqual(code, 0, "a covered file that exists nowhere must fail: %s" % text)
        self.assertIn("no longer exists", text)

    def test_a_sibling_receipts_commit_does_not_stale_the_first_one(self):
        """DEFECT 2: only the receipt's OWN path was exempt, so minting three
        receipts left at most the last one fresh. Another receipt is not the
        code under test, which this product already says for coverage."""
        covers = "docs/dossiers/v1/01-purpose.md"
        first = self._mint("design", covers)
        self._mint("gate", covers)
        self._mint("score", covers)
        code, text = self._run_sbe_at("evidence", "verify", first, "--cwd", self.repo)
        self.assertEqual(code, 0, "a sibling receipt's own commit must not stale this "
                                  "one: %s" % text)
        self.assertIn("PASS", text)

    def test_a_covered_file_rewritten_with_the_same_bytes_is_not_a_finding(self):
        """DEFECT 3: a covered file whose mtime moved past the run was a
        problem even when its digest still matched. Every checkout, merge, pull
        and new worktree rewrites files that way, so a merged receipt failed on
        a fact about the filesystem rather than about the code."""
        receipt = self._mint("design", "docs/dossiers/v1/01-purpose.md")
        time.sleep(0.01)
        write(self.repo, os.path.join(self.DOSSIER, "01-purpose.md"), "why this exists\n")
        code, text = self._run_sbe_at("evidence", "verify", receipt, "--cwd", self.repo)
        self.assertEqual(code, 0, "identical bytes rewritten after the run are the bytes "
                                  "the receipt recorded: %s" % text)
        self.assertNotIn("was written after the run ended", text)

    def test_a_covered_file_rewritten_with_different_bytes_still_fails(self):
        """THE GUARD on the test above: the digest, not the timestamp, is what
        speaks for the bytes, and it still refuses."""
        receipt = self._mint("design", "docs/dossiers/v1/01-purpose.md")
        write(self.repo, os.path.join(self.DOSSIER, "01-purpose.md"), "rewritten\n")
        code, text = self._run_sbe_at("evidence", "verify", receipt, "--cwd", self.repo)
        self.assertNotEqual(code, 0, "changed bytes must still fail: %s" % text)
        self.assertIn("the code changed after the evidence was made", text)

    def test_a_receipt_on_a_history_this_head_never_descended_from_is_stale(self):
        """THE ANCESTOR REQUIREMENT is doing real work, not decoration. The
        covered file is byte identical on both branches, so the covered-files
        question answers "nothing changed"; the receipt was still made on a
        history this head never came from, and carrying evidence FORWARD is the
        only thing the rule licenses."""
        trunk = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        git(self.repo, "checkout", "-q", "-b", "side")
        write(self.repo, "side-only.py", "y = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "a commit only this branch has")
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        receipt = self._mint("design", "docs/dossiers/v1/01-purpose.md",
                             out=os.path.join(outside, "design.json"))
        git(self.repo, "checkout", "-q", trunk)
        write(self.repo, "app.py", "x = 2\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "work on the trunk the receipt never saw")
        code, text = self._run_sbe_at("evidence", "verify", receipt, "--cwd", self.repo)
        self.assertNotEqual(code, 0, "a receipt from a history this head never descended "
                                     "from must not carry forward: %s" % text)
        self.assertIn("is not the current head", text)


class TestVerifyReleaseReceiptForReleaseArtifactDigest(unittest.TestCase):
    """`verify_release_receipt_for_release`'s optional `expected_artifact_sha256`
    keyword. Before this argument existed, this function checked a release
    receipt's `releasedCommit` against the commit a caller expected, but had no
    way at all to also check the ARTIFACT the receipt names, so a caller could
    read `PASS` for a released commit whose receipt carried a completely
    different binary. Closed by calling the same shared rule `contracts.
    release_binds` already applies to its own argument of the same name
    (`contracts._artifact_digest_problem`, `contracts._artifact_note`), not a
    second copy of it: see both functions' own docstrings for the three arms.

    Direct, subprocess-free coverage, the same reason `TestRedactArgv` above
    needs none: a release receipt is a JSON document on disk, verified on its
    own face, no git repository required to build or check one.
    """

    CHANGE_ID = "CHG-2026-08-21-artifact-digest"
    COMMIT = "b1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3d4"
    ARTIFACT_SHA256 = "cd" * 32
    OTHER_ARTIFACT_SHA256 = "ef" * 32
    ORIGIN = "git@example.invalid:acme/thing.git"

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from brothersbe import evidence as mod
        self.mod = mod
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        sys.path.pop(0)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _a_receipt(self, **over):
        base = dict(
            change_id=self.CHANGE_ID, released_commit=self.COMMIT,
            artifact_sha256=self.ARTIFACT_SHA256, run_id="run-0001",
            trust_class="ci-run", source_detail="a real pipeline run",
            producer="release-bot", producer_class="pipeline", origin=self.ORIGIN)
        base.update(over)
        return self.mod.build_release_receipt(**base)

    def _write(self, name, receipt):
        path = os.path.join(self.tmp, name)
        self.mod.write_receipt(receipt, path)
        return path

    def test_a_mismatched_artifact_digest_reaches_fail_naming_both_digests(self):
        path = self._write("receipt.json", self._a_receipt())
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256=self.OTHER_ARTIFACT_SHA256)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn(self.ARTIFACT_SHA256, evidence)
        self.assertIn(self.OTHER_ARTIFACT_SHA256, evidence)

    def test_a_matching_artifact_digest_reaches_pass_checked_and_matching(self):
        path = self._write("receipt.json", self._a_receipt())
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256=self.ARTIFACT_SHA256)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.assertIn("checked against the expected digest and matching", evidence)

    def test_an_expectation_answering_nothing_is_refused_by_name(self):
        path = self._write("receipt.json", self._a_receipt())
        for hollow in ("", "   ", "TODO"):
            with self.subTest(hollow=hollow):
                verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
                    path, self.COMMIT, expected_artifact_sha256=hollow)
                self.assertEqual(verdict, "FAIL", (evidence, problems))
                self.assertIn("an empty expectation checks nothing", evidence)

    def test_no_expectation_given_the_pass_evidence_reads_recorded_not_verified(self):
        path = self._write("receipt.json", self._a_receipt())
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.assertIn("recorded, not verified (no expected digest was given to check it "
                      "against)", evidence)

    def test_a_fixture_trust_class_refuses_first_even_when_the_digest_matches(self):
        path = self._write(
            "fixture-receipt.json",
            self._a_receipt(trust_class="fixture", source_detail="unit test fixture"))
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256=self.ARTIFACT_SHA256)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn("fixture", evidence)

    def test_a_wrong_released_commit_refuses_first_even_when_the_digest_matches(self):
        path = self._write("receipt.json", self._a_receipt())
        other_commit = "9" * 40
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, other_commit, expected_artifact_sha256=self.ARTIFACT_SHA256)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn(self.COMMIT, evidence)
        self.assertIn(other_commit, evidence)

    # THE SHAPE GUARD (adversarial review finding on `contracts._artifact_
    # digest_problem`): equality-only comparison let two equal strings that
    # were never digests at all reach a `PASS` that claimed one was checked.
    # The reviewer's own reproduction: `artifact_sha256="banana"` paired
    # with `expected_artifact_sha256="banana"`. This is that caller's share
    # of the fix; `TestArtifactDigestShapeGuard` in `tools/
    # test_sbe_contracts.py` is `release_binds`'s.

    def test_the_reviewers_equal_but_malformed_case_does_not_reach_a_false_pass(self):
        path = self._write("receipt.json", self._a_receipt(artifact_sha256="banana"))
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertNotIn("checked against the expected digest and matching", evidence)

    def test_a_malformed_recorded_digest_is_named_a_defective_document(self):
        path = self._write("receipt.json", self._a_receipt(artifact_sha256="banana"))
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256=self.ARTIFACT_SHA256)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn("defective document", evidence)

    def test_a_malformed_expected_digest_is_named_a_defective_caller(self):
        path = self._write("receipt.json", self._a_receipt())
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn("defective caller", evidence)

    def test_boundary_lengths_and_a_non_hex_character_are_refused(self):
        too_short = "a" * 63
        too_long = "a" * 65
        one_non_hex_char = "g" + "a" * 63
        for malformed in (too_short, too_long, one_non_hex_char):
            with self.subTest(malformed=malformed, length=len(malformed)):
                path = self._write("receipt-%d.json" % len(malformed),
                                   self._a_receipt(artifact_sha256=malformed))
                verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
                    path, self.COMMIT, expected_artifact_sha256=self.ARTIFACT_SHA256)
                self.assertEqual(verdict, "FAIL", (evidence, problems))
                self.assertIn("defective document", evidence)

    def test_uppercase_hex_is_refused_even_though_the_strings_are_equal(self):
        path = self._write("receipt.json", self._a_receipt(artifact_sha256="F" * 64))
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256="F" * 64)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn("defective document", evidence)

    # ORDERING: the fixture-trust-class and wrong-commit refusals happen
    # BEFORE this function ever calls `_artifact_digest_problem` (see that
    # function's own docstring, "THE DIGEST CHECK RUNS LAST"), so a
    # malformed digest must never surface INSTEAD of an earlier refusal,
    # exactly as a matching one already does not, above.

    def test_a_fixture_trust_class_refuses_first_even_when_the_digest_is_malformed(self):
        path = self._write(
            "fixture-receipt.json",
            self._a_receipt(trust_class="fixture", source_detail="unit test fixture",
                            artifact_sha256="banana"))
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, self.COMMIT, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn("fixture", evidence)
        self.assertNotIn("defective", evidence)

    def test_a_wrong_released_commit_refuses_first_even_when_the_digest_is_malformed(self):
        path = self._write("receipt.json", self._a_receipt(artifact_sha256="banana"))
        other_commit = "9" * 40
        verdict, evidence, problems = self.mod.verify_release_receipt_for_release(
            path, other_commit, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertIn(self.COMMIT, evidence)
        self.assertIn(other_commit, evidence)
        self.assertNotIn("defective", evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
