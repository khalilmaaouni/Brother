#!/usr/bin/env python3
"""test_release_closeout: drives the closeout matrix's decision points in
BOTH directions, with no network and no real Codex binary.

WHAT IS TESTED HERE, and why it is these things. The gates themselves do
real work (an install, an upgrade, a clone) and their own printed verdicts
are that work's evidence. What a test can lie about, and therefore what is
driven here, is the LOGIC AROUND the work:

1. THE TABLE. `all` must exit non-zero unless every required gate is PASS.
   A population of NO-DATA composing into a PASS is a failure this estate
   has already paid for once, so the NO-DATA row is driven explicitly.
2. THE NO-DATA GUARD, both ways. An absent Codex binary is NO-DATA and
   exit 2, never 0. The positive control is an executable fake that is not
   Codex: the gate must then reach the tool and report FAIL, which proves
   the guard is not simply refusing everything.
3. THE UPGRADE'S HONEST NO-DATA. A previous tag that ships no Codex package
   must produce NO-DATA naming that fact, never an invented upgrade. Driven
   on a throwaway git repository with real tags.
4. THE HASHES AND THE READERS. tree_hash, installed_path_from and decisive
   each decide something a wrong answer would make look right.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_closeout as rc  # noqa: E402


def a_gate(gid, name, verdict, required=True):
    gate = rc.Gate(gid, name, "a gate", required)
    gate.settle(verdict, "because the test said so")
    return gate


class TheTable(unittest.TestCase):
    def test_every_required_gate_passing_exits_zero(self):
        gates = [a_gate("X1", "one", "PASS"), a_gate("X2", "two", "PASS")]
        text, code = rc.verdict_table(gates)
        self.assertEqual(code, 0)
        self.assertIn("CLOSEOUT COMPLETE", text)

    def test_a_no_data_required_gate_is_never_a_pass(self):
        gates = [a_gate("X1", "one", "PASS"), a_gate("X2", "two", "NO-DATA")]
        text, code = rc.verdict_table(gates)
        self.assertEqual(code, 1)
        self.assertIn("NO-DATA is not a pass", text)
        self.assertIn("X2(NO-DATA)", text)

    def test_a_failing_required_gate_exits_one(self):
        gates = [a_gate("X1", "one", "FAIL")]
        _text, code = rc.verdict_table(gates)
        self.assertEqual(code, 1)

    def test_a_founder_gate_is_reported_and_never_counted_against_it(self):
        gates = [a_gate("X1", "one", "PASS"),
                 a_gate("X8", "founder", "FOUNDER", required=False)]
        text, code = rc.verdict_table(gates)
        self.assertEqual(code, 0)
        self.assertIn("X8 is FOUNDER", text)

    def test_every_gate_appears_in_the_table(self):
        gates = [a_gate(gid, name, "PASS") for gid, name, _t, _r
                 in rc.GATE_ORDER]
        text, _code = rc.verdict_table(gates)
        for gid, name, _t, _r in rc.GATE_ORDER:
            self.assertIn(gid, text)
            self.assertIn(name, text)

    def test_a_word_that_is_not_a_verdict_is_refused(self):
        gate = rc.Gate("X1", "one", "a gate")
        with self.assertRaises(ValueError):
            gate.settle("OK", "OK is not one of the three words")


class TheNoDataGuard(unittest.TestCase):
    """A guard nobody drove backwards is a claim."""

    def _args(self, work, codex_bin, marketplace="."):
        return argparse.Namespace(
            marketplace=marketplace, ref=None, codex_bin=codex_bin,
            evidence_dir=os.path.join(work, "evidence"),
            work=os.path.join(work, "work"), version="0.0.0",
            public_url="https://example.invalid/none",
            public_checkout=work, actions_run_id=None, tag_checkout=None)

    def test_an_absent_binary_is_no_data_on_every_codex_gate(self):
        with tempfile.TemporaryDirectory() as work:
            args = self._args(work, "/no/such/codex/binary")
            ev = rc.Evidence(args.evidence_dir)
            for name in sorted(rc.NEEDS_CODEX):
                gate = rc.run_gate(name, args, ev)
                self.assertEqual(gate.verdict, "NO-DATA", name)
                self.assertEqual(gate.exit_code(), 2, name)
                self.assertIn("no executable Codex binary", gate.why)

    def test_a_directory_is_not_an_executable_binary(self):
        with tempfile.TemporaryDirectory() as work:
            args = self._args(work, HERE)
            ev = rc.Evidence(args.evidence_dir)
            gate = rc.run_gate("reinstall-idempotent", args, ev)
            self.assertEqual(gate.verdict, "NO-DATA")

    def test_an_executable_that_is_not_codex_reaches_the_tool_and_fails(self):
        # THE POSITIVE CONTROL. Without it the guard could refuse everything
        # and still look correct above. A fake binary that exits 3 must get
        # past the guard, be run, and produce FAIL, never NO-DATA and never
        # PASS.
        with tempfile.TemporaryDirectory() as work:
            fake = os.path.join(work, "fake-codex")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\necho 'fake codex: nothing here'\nexit 3\n")
            os.chmod(fake, 0o755)
            args = self._args(work, fake)
            ev = rc.Evidence(args.evidence_dir)
            gate = rc.run_gate("reinstall-idempotent", args, ev)
            self.assertEqual(gate.verdict, "FAIL")
            self.assertIn("exited 3", gate.why)

    def test_the_evidence_directory_holds_the_whole_output(self):
        with tempfile.TemporaryDirectory() as work:
            fake = os.path.join(work, "fake-codex")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\necho LINE-ONE\necho LINE-TWO\nexit 3\n")
            os.chmod(fake, 0o755)
            args = self._args(work, fake)
            ev = rc.Evidence(args.evidence_dir)
            rc.run_gate("reinstall-idempotent", args, ev)
            kept = []
            for base, _dirs, files in os.walk(args.evidence_dir):
                kept.extend(os.path.join(base, f) for f in files)
            self.assertTrue(kept, "no evidence file was written")
            body = ""
            for path in kept:
                with open(path, "r", encoding="utf-8") as fh:
                    body += fh.read()
            # Both lines, not a tail of one: trimming at capture time is the
            # failure this keeps out.
            self.assertIn("LINE-ONE", body)
            self.assertIn("LINE-TWO", body)


def _git(repo, *args):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("git %s failed: %s%s" % (" ".join(args),
                                                      proc.stdout,
                                                      proc.stderr))
    return proc.stdout


class TheUpgradesHonestNoData(unittest.TestCase):
    """A previous release that ships no Codex package cannot be upgraded
    FROM, and saying so is the gate's job. Inventing one would be fiction."""

    def _repo(self, work):
        repo = os.path.join(work, "repo")
        os.makedirs(repo)
        _git(repo, "init", "-q", ".")
        _git(repo, "config", "user.name", "Khalil Maaouni")
        _git(repo, "config", "user.email", "khalil@example.invalid")
        # PIN THE SIGNING, or this fixture measures the machine. With a
        # global tag.gpgSign true (this one, measured 2026-09-05) every
        # lightweight tag below dies with "fatal: no tag message?", because
        # signing forces an annotation, and all seven tests in this class go
        # red for a reason that has nothing to do with the gate under test.
        # Local config wins over global.
        for key in ("tag.gpgSign", "commit.gpgsign"):
            _git(repo, "config", key, "false")
        with open(os.path.join(repo, "README.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("old\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "old")
        _git(repo, "tag", "v1.0.1")
        for rel in ("bundle/.codex-plugin/plugin.json",
                    ".agents/plugins/marketplace.json"):
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"name": "brother"}\n')
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "codex package")
        _git(repo, "tag", "v2.0.0")
        return repo

    def test_the_previous_tag_is_read_off_the_checkout(self):
        with tempfile.TemporaryDirectory() as work:
            repo = self._repo(work)
            previous, why = rc.previous_public_tag(repo, "v2.0.0")
            self.assertEqual(previous, "v1.0.1", why)

    def test_no_older_tag_is_no_data_not_a_guess(self):
        with tempfile.TemporaryDirectory() as work:
            repo = self._repo(work)
            previous, why = rc.previous_public_tag(repo, "v0.0.1")
            self.assertIsNone(previous)
            self.assertIn("no public tag older", why)

    def test_a_tag_without_a_codex_package_is_measured_not_assumed(self):
        with tempfile.TemporaryDirectory() as work:
            repo = self._repo(work)
            carries, detail = rc.tag_carries_codex_package(repo, "v1.0.1")
            self.assertFalse(carries)
            self.assertIn("does not carry", detail)

    def test_a_tag_with_a_codex_package_reads_the_other_way(self):
        # The positive direction of the same reader.
        with tempfile.TemporaryDirectory() as work:
            repo = self._repo(work)
            carries, detail = rc.tag_carries_codex_package(repo, "v2.0.0")
            self.assertTrue(carries, detail)

    def test_the_upgrade_gate_reports_no_data_naming_the_missing_package(self):
        with tempfile.TemporaryDirectory() as work:
            repo = self._repo(work)
            args = argparse.Namespace(
                marketplace=repo, ref=None,
                codex_bin="/no/such/codex/binary",
                evidence_dir=os.path.join(work, "evidence"),
                work=os.path.join(work, "w"), version="2.0.0",
                public_url="https://example.invalid/none",
                public_checkout=repo, actions_run_id=None, tag_checkout=None)
            ev = rc.Evidence(args.evidence_dir)
            # With no binary the guard fires first, so the package fact is
            # driven through the gate function directly, which is the path
            # a real machine takes.
            gate = rc.Gate("X2", "upgrade-codex", "upgrade")
            gate.codex_bin = args.codex_bin
            rc.gate_upgrade_codex(args, ev, gate)
            self.assertEqual(gate.verdict, "NO-DATA")
            self.assertIn("ships no Codex package", gate.why)
            self.assertIn("v1.0.1", gate.why)

    def _no_package_gate(self, work, ref):
        repo = self._repo(work)
        args = argparse.Namespace(
            marketplace=repo, ref=ref, codex_bin="/no/such/codex/binary",
            evidence_dir=os.path.join(work, "evidence"),
            work=os.path.join(work, "w"), version="2.0.0",
            public_url="https://example.invalid/none",
            public_checkout=repo, actions_run_id=None, tag_checkout=None)
        gate = rc.Gate("X2", "upgrade-codex", "upgrade")
        gate.codex_bin = args.codex_bin
        rc.gate_upgrade_codex(args, rc.Evidence(args.evidence_dir), gate)
        return gate

    def test_the_revision_names_the_ref_that_was_actually_tested(self):
        # D3 (Codex lane, 2026-09-05): the upgrade honoured --ref while every
        # line the gate printed named the version's tag, so a `--ref main`
        # matrix reported "upgraded to v2.0.0" having upgraded to main.
        with tempfile.TemporaryDirectory() as work:
            gate = self._no_package_gate(work, "main")
            self.assertIn("main", gate.revision)
            self.assertNotIn("v2.0.0", gate.revision)

    def test_no_ref_still_names_the_version_s_own_tag(self):
        with tempfile.TemporaryDirectory() as work:
            gate = self._no_package_gate(work, None)
            self.assertIn("v2.0.0", gate.revision)


class TheSignatureIsNotALegOfX7(unittest.TestCase):
    """1.0.5's whole matrix. Tag signing was folded into X7 as a required
    leg, and because the signing key is the founder's alone (row S5) that
    leg could only ever answer NO-DATA, so X7 settled NO-DATA and the
    release read NOT COMPLETE with every other gate PASS. X7's subject is
    REPRODUCTION of the published tree, and a founder-gated key cannot
    decide it. The signature is still read and still printed; it just does
    not vote."""

    def test_the_settle_never_sees_the_signature_verdict(self):
        # Read off the source, because the alternative is a live tag fetch
        # against the public remote inside a unit test. The assertion is
        # positional and would go red the moment the call is appended to
        # `verdicts` again, which is exactly the regression.
        import inspect
        body = inspect.getsource(rc.gate_public_artifact)
        self.assertNotIn("verdicts.append(tag_signature_verified", body)
        self.assertIn("tag_signature_verified(gate, ev, checkout, tag)", body)
        self.assertIn("INFORMATIONAL ONLY", body)

    def test_an_unsigned_tag_still_reads_no_data_naming_s5(self):
        """The reading itself is unchanged: NO-DATA, never a FAIL, and it
        names the founder's own row so a reader knows whose step is open."""
        with tempfile.TemporaryDirectory() as work:
            checkout = os.path.join(work, "tag")
            os.makedirs(checkout)
            _git(checkout, "init", "-q", ".")
            _git(checkout, "config", "user.name", "Khalil Maaouni")
            _git(checkout, "config", "user.email", "khalil@example.invalid")
            for key in ("tag.gpgSign", "commit.gpgsign"):
                _git(checkout, "config", key, "false")
            with open(os.path.join(checkout, "f.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("hi\n")
            _git(checkout, "add", "-A")
            _git(checkout, "commit", "-q", "-m", "init")
            _git(checkout, "tag", "-a", "v9.9.9", "-m", "unsigned")
            gate = rc.Gate("X7", "public-artifact", "the published tag")
            ev = rc.Evidence(os.path.join(work, "evidence"))
            verdict, why = rc.tag_signature_verified(gate, ev, checkout,
                                                     "v9.9.9")
            self.assertEqual(verdict, "NO-DATA", why)
            self.assertIn("S5, founder", why)


class TheHashesAndTheReaders(unittest.TestCase):
    def test_tree_hash_is_none_for_a_directory_that_is_not_there(self):
        self.assertIsNone(rc.tree_hash("/no/such/tree/anywhere"))

    def test_tree_hash_is_stable_and_moves_on_a_content_change(self):
        with tempfile.TemporaryDirectory() as work:
            root = os.path.join(work, "tree")
            os.makedirs(os.path.join(root, "sub"))
            path = os.path.join(root, "sub", "a.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("one")
            first = rc.tree_hash(root)
            self.assertEqual(first, rc.tree_hash(root))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("two")
            self.assertNotEqual(first, rc.tree_hash(root))

    def test_tree_hash_moves_when_only_a_name_changes(self):
        # Same bytes, different path: a rename is drift too.
        with tempfile.TemporaryDirectory() as work:
            root = os.path.join(work, "tree")
            os.makedirs(root)
            with open(os.path.join(root, "a.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("same")
            first = rc.tree_hash(root)
            os.rename(os.path.join(root, "a.txt"),
                      os.path.join(root, "b.txt"))
            self.assertNotEqual(first, rc.tree_hash(root))

    def _proc(self, out):
        return subprocess.CompletedProcess(["x"], 0, out, "")

    def test_installed_path_comes_from_the_tools_own_json(self):
        path, why = rc.installed_path_from(
            self._proc(json.dumps({"installedPath": "/cache/brother/1.0.1"})))
        self.assertEqual(path, "/cache/brother/1.0.1", why)

    def test_json_with_no_installed_path_is_a_stated_reason(self):
        path, why = rc.installed_path_from(self._proc(json.dumps({"a": 1})))
        self.assertIsNone(path)
        self.assertIn("no installedPath", why)

    def test_unparseable_output_falls_back_to_the_line_then_says_why(self):
        path, _why = rc.installed_path_from(
            self._proc('noise\n  "installedPath": "/cache/x"\nmore noise\n'))
        self.assertEqual(path, "/cache/x")
        path, why = rc.installed_path_from(self._proc("nothing useful"))
        self.assertIsNone(path)
        self.assertIn("no parseable installedPath", why)

    def test_decisive_prefers_the_needle_and_falls_back_to_the_tail(self):
        proc = subprocess.CompletedProcess(
            ["x"], 0, "alpha\nbeta MARKER here\ngamma\ndelta\n", "")
        self.assertEqual(rc.decisive(proc, ("MARKER",)),
                         ["beta MARKER here"])
        self.assertEqual(rc.decisive(proc, ("NOPE",), tail=2),
                         ["gamma", "delta"])


class TheVersionGuard(unittest.TestCase):
    def test_the_declared_version_is_read_from_the_tree_not_typed(self):
        version, why = rc.declared_version(rc.REPO)
        self.assertIsNotNone(version, why)
        self.assertRegex(version, r"^\d+\.\d+")

    def test_a_tree_that_declares_nothing_is_no_data(self):
        with tempfile.TemporaryDirectory() as work:
            version, why = rc.declared_version(work)
            self.assertIsNone(version)
            self.assertTrue(why)


class TheFounderGate(unittest.TestCase):
    def test_it_reports_founder_and_names_its_runbook(self):
        with tempfile.TemporaryDirectory() as work:
            args = argparse.Namespace(version="1.0.1", work=work)
            gate = rc.Gate("X8", "founder", "the credentialled session",
                           required=False)
            rc.gate_founder(args, rc.Evidence(work), gate)
            self.assertEqual(gate.verdict, "FOUNDER")
            self.assertIn(rc.RUNBOOK, gate.why)
            self.assertEqual(gate.exit_code(), 0)


class TheNegativesLocalBase(unittest.TestCase):
    """X5's two corruption negatives were NO-DATA for every REMOTE release,
    which is every real one. Both directions of the fix are driven here."""

    def _gate(self, work):
        gate = rc.Gate("X5", "negatives", "four refusals")
        gate.codex_bin = "/no/such/codex/binary"
        return gate, rc.Evidence(os.path.join(work, "evidence"))

    def _args(self, work, marketplace, ref=None):
        return argparse.Namespace(marketplace=marketplace, ref=ref,
                                  work=os.path.join(work, "w"))

    def test_a_local_directory_source_is_used_as_is(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            base, why = rc.local_base(self._args(work, work), gate, ev)
            self.assertEqual(base, work, why)
            self.assertEqual(why, "")

    def test_a_git_source_is_cloned_so_the_negatives_can_be_driven(self):
        # THE WHOLE POINT. A URL source used to yield NO-DATA twice; now it
        # yields a directory with the package in it.
        with tempfile.TemporaryDirectory() as work:
            repo = os.path.join(work, "repo")
            os.makedirs(repo)
            _git(repo, "init", "-q", ".")
            _git(repo, "config", "user.name", "Khalil Maaouni")
            _git(repo, "config", "user.email", "khalil@example.invalid")
            for key in ("tag.gpgSign", "commit.gpgsign"):
                _git(repo, "config", key, "false")
            path = os.path.join(repo, "bundle", ".codex-plugin", "plugin.json")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"name": "brother"}\n')
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "package")
            gate, ev = self._gate(work)
            args = self._args(work, "file://%s" % repo)
            os.makedirs(args.work, exist_ok=True)
            base, why = rc.local_base(args, gate, ev)
            self.assertIsNotNone(base, why)
            self.assertTrue(os.path.isfile(
                os.path.join(base, "bundle", ".codex-plugin", "plugin.json")),
                "the clone does not carry the package to corrupt")

    def test_a_source_that_cannot_be_cloned_is_no_data_naming_the_exit(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            args = self._args(work, "file:///no/such/repository/anywhere")
            os.makedirs(args.work, exist_ok=True)
            base, why = rc.local_base(args, gate, ev)
            self.assertIsNone(base)
            self.assertIn("could not be cloned", why)
            self.assertIn("git clone exited", why)

    def test_no_local_base_leaves_the_corruption_stated_not_silent(self):
        with tempfile.TemporaryDirectory() as work:
            bad, why = rc._corrupt_copy(None, work, lambda root: None)
            self.assertIsNone(bad)
            self.assertIn("no local copy", why)

    def test_the_defect_really_lands_in_the_copy(self):
        # The other direction: a copy that was NOT corrupted would let the
        # negative pass for the wrong reason.
        with tempfile.TemporaryDirectory() as work:
            source = os.path.join(work, "src")
            os.makedirs(os.path.join(source, "bundle", ".codex-plugin"))
            path = os.path.join(source, "bundle", ".codex-plugin",
                                "plugin.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"name": "brother"}\n')

            def break_it(root):
                with open(os.path.join(root, "bundle", ".codex-plugin",
                                       "plugin.json"), "w",
                          encoding="utf-8") as fh:
                    fh.write("{ this is not json")

            dest, why = rc._corrupt_copy(source, os.path.join(work, "c"),
                                         break_it)
            self.assertIsNotNone(dest, why)
            with open(os.path.join(dest, "bundle", ".codex-plugin",
                                   "plugin.json"), encoding="utf-8") as fh:
                body = fh.read()
            self.assertEqual(body, "{ this is not json")
            with open(path, encoding="utf-8") as fh:
                self.assertIn('"name"', fh.read(),
                              "the ORIGINAL was corrupted, not the copy")

    def test_the_four_negatives_are_named_not_counted(self):
        self.assertEqual(len(rc.NEGATIVES), 4)
        for word in ("missing marketplace", "malformed plugin.json",
                     "unsupported hooks key", "offline"):
            self.assertIn(word, rc.NEGATIVES)


class ThePlatformLegs(unittest.TestCase):
    """X6 read NO-DATA for macOS on a macOS machine. Both hosts are driven."""

    def _gate(self, work):
        return (rc.Gate("X6", "claude-side", "the Claude side"),
                rc.Evidence(os.path.join(work, "evidence")))

    def test_a_non_darwin_host_is_no_data_naming_the_host_and_the_law(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            real = rc.platform.system
            rc.platform.system = lambda: "Linux"
            try:
                verdict, why = rc.macos_leg(gate, ev)
            finally:
                rc.platform.system = real
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("Linux", why)
            self.assertIn("macOS and Windows runners never", why)

    def test_a_darwin_host_runs_the_gate_and_reports_its_own_exit(self):
        # The positive control for the guard above: on Darwin the leg must
        # REACH the command, not refuse. Driven with a one line stand-in
        # so the test costs milliseconds and the exit code is known.
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            real_system, real_gate = rc.platform.system, rc.MACOS_GATE
            rc.platform.system = lambda: "Darwin"
            rc.MACOS_GATE = ["sh", "-c", "exit 7"]
            try:
                verdict, why = rc.macos_leg(gate, ev)
            finally:
                rc.platform.system = real_system
                rc.MACOS_GATE = real_gate
            self.assertEqual(verdict, "FAIL")
            self.assertIn("exited 7", why)
            self.assertTrue(any("platform.system()" in line
                                for line in gate.lines),
                            "the host is not quoted in the evidence")

    def test_a_darwin_host_passing_the_gate_reads_pass(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            real_system, real_gate = rc.platform.system, rc.MACOS_GATE
            rc.platform.system = lambda: "Darwin"
            rc.MACOS_GATE = ["sh", "-c", "echo ok"]
            try:
                verdict, why = rc.macos_leg(gate, ev)
            finally:
                rc.platform.system = real_system
                rc.MACOS_GATE = real_gate
            self.assertEqual(verdict, "PASS")
            self.assertIn("no runner needed", why)

    def test_an_absent_windows_check_is_no_data_quoting_the_law(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            real = rc.WINDOWS_SIM
            rc.WINDOWS_SIM = os.path.join("no", "such", "windows_check.py")
            try:
                verdict, why = rc.windows_leg(gate, ev)
            finally:
                rc.WINDOWS_SIM = real
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("macOS and Windows runners never", why)

    def test_the_shipped_windows_check_is_on_disk_and_is_run(self):
        # Without this the NO-DATA above could be the only path the leg has.
        self.assertTrue(os.path.isfile(os.path.join(rc.REPO, rc.WINDOWS_SIM)),
                        "%s is the documented Windows check and is missing" %
                        rc.WINDOWS_SIM)
        self.assertTrue(os.path.isfile(os.path.join(rc.REPO,
                                                    rc.WINDOWS_PROTOCOL)),
                        "the leg names a protocol that is not on disk")

    def test_a_failing_windows_check_is_a_fail_not_a_no_data(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            fake = os.path.join(work, "fake_windows_check.py")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("import sys\nsys.exit(5)\n")
            real = rc.WINDOWS_SIM
            rc.WINDOWS_SIM = os.path.relpath(fake, rc.REPO)
            try:
                verdict, why = rc.windows_leg(gate, ev)
            finally:
                rc.WINDOWS_SIM = real
            self.assertEqual(verdict, "FAIL")
            self.assertIn("exited 5", why)


class TheInvariantLegsSubject(unittest.TestCase):
    """X6 read FAIL because it measured the WRONG TREE.

    The leg ran the hub's own release_invariant.py, whose ROOT is the hub,
    against the tag: so the first post-tag merge that touched bundle/runtime
    turned X6 red while the released artifact was perfectly consistent with
    itself (measured on 1.0.3, "2 of 34 shipped runtime file(s) differ", every
    other leg green). Both directions are driven here: a hub that has drifted
    must not decide the gate, and a tag that contradicts ITSELF still must.

    The invariant tool is stubbed on purpose. What a wrong answer would make
    look right is not the tool's arithmetic (that is release_invariant.py's
    own suite) but WHICH COPY runs and WHERE, so the stub records its own cwd
    and argv and the tests read them back.
    """

    #: Records the cwd and the arguments it was handed, then exits with the
    #: code the fixture chose. Written into <tree>/scripts/, so a leg that
    #: reached for the hub's copy instead leaves this file untouched.
    STUB = ("import os\n"
            "import sys\n"
            "here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
            "with open(os.path.join(here, 'ran.txt'), 'w', encoding='utf-8') as fh:\n"
            "    fh.write(os.getcwd() + '\\n' + ' '.join(sys.argv[1:]) + '\\n')\n"
            "print('stub release_invariant, tree ' + here)\n"
            "sys.exit({code})\n")

    def _tree(self, root, code):
        """A tree that ships its own release_invariant.py exiting `code`."""
        scripts = os.path.join(root, "scripts")
        os.makedirs(scripts, exist_ok=True)
        with open(os.path.join(scripts, "release_invariant.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(self.STUB.replace("{code}", str(code)))
        return root

    def _ran(self, root):
        """(cwd, argv) the stub in `root` recorded, or (None, None)."""
        path = os.path.join(root, "ran.txt")
        if not os.path.isfile(path):
            return None, None
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        return (lines[0] if lines else None), (lines[1] if len(lines) > 1
                                               else "")

    def _gate(self, work):
        return (rc.Gate("X6", "claude-side", "the Claude side"),
                rc.Evidence(os.path.join(work, "evidence")))

    def _line(self, gate, needle):
        for line in gate.lines:
            if needle in line:
                return line
        return ""

    def test_a_drifted_hub_leaves_the_tag_passing_and_says_so_beside_it(self):
        # THE REGRESSION. The hub disagrees with the tag; the tag agrees with
        # itself. X6's leg is PASS and the drift is printed, not counted.
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            tag = self._tree(os.path.join(work, "tag"), 0)
            hub = self._tree(os.path.join(work, "hub"), 1)
            real = rc.REPO
            rc.REPO = hub
            try:
                verdict, why = rc.invariant_leg(gate, ev, tag)
            finally:
                rc.REPO = real
            self.assertEqual(verdict, "PASS", why)
            self.assertIn(tag, why)
            drift = self._line(gate, "hub main versus the tag:")
            self.assertIn("DRIFTED", drift)
            self.assertIn("INFORMATIONAL ONLY", drift)

    def test_a_tag_that_contradicts_itself_is_still_a_fail(self):
        # The positive control for the test above: moving the subject must
        # not have made the leg unable to say FAIL about anything.
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            tag = self._tree(os.path.join(work, "tag"), 1)
            hub = self._tree(os.path.join(work, "hub"), 0)
            real = rc.REPO
            rc.REPO = hub
            try:
                verdict, why = rc.invariant_leg(gate, ev, tag)
            finally:
                rc.REPO = real
            self.assertEqual(verdict, "FAIL", why)
            self.assertIn("contradicts", why)
            self.assertIn("agrees", self._line(gate, "hub main versus the "
                                                     "tag:"))

    def test_the_tags_own_copy_runs_inside_the_tag(self):
        # Names the bad state the two verdicts above would ALSO reach if the
        # leg still ran the hub's copy against the tag: only the tag's stub
        # may have run, and it must have run with the tag as its cwd and as
        # its --public-checkout.
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            tag = self._tree(os.path.join(work, "tag"), 0)
            hub = self._tree(os.path.join(work, "hub"), 0)
            real = rc.REPO
            rc.REPO = hub
            try:
                rc.invariant_leg(gate, ev, tag)
            finally:
                rc.REPO = real
            cwd, argv = self._ran(tag)
            self.assertIsNotNone(cwd, "the tag's own copy never ran")
            self.assertEqual(os.path.realpath(cwd), os.path.realpath(tag))
            self.assertIn(tag, argv)
            hub_cwd, _ = self._ran(hub)
            self.assertIsNotNone(hub_cwd, "the informational line never ran")
            self.assertEqual(os.path.realpath(hub_cwd),
                             os.path.realpath(hub))

    def test_a_tree_shipping_no_invariant_tool_is_no_data_never_a_pass(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            bare = os.path.join(work, "tag")
            os.makedirs(bare)
            hub = self._tree(os.path.join(work, "hub"), 0)
            real = rc.REPO
            rc.REPO = hub
            try:
                verdict, why = rc.invariant_leg(gate, ev, bare)
            finally:
                rc.REPO = real
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("release_invariant.py", why)

    def test_an_exit_two_from_the_tag_is_no_data_not_a_fail(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            tag = self._tree(os.path.join(work, "tag"), 2)
            hub = self._tree(os.path.join(work, "hub"), 0)
            real = rc.REPO
            rc.REPO = hub
            try:
                verdict, _why = rc.invariant_leg(gate, ev, tag)
            finally:
                rc.REPO = real
            self.assertEqual(verdict, "NO-DATA")

    def test_the_informational_line_has_nothing_to_compare_against_itself(self):
        with tempfile.TemporaryDirectory() as work:
            gate, ev = self._gate(work)
            hub = self._tree(os.path.join(work, "hub"), 1)
            real = rc.REPO
            rc.REPO = hub
            try:
                rc.hub_versus_tag(gate, ev, hub)
            finally:
                rc.REPO = real
            line = self._line(gate, "hub main versus the tag:")
            self.assertIn("NO-DATA", line)
            cwd, _argv = self._ran(hub)
            self.assertIsNone(cwd, "it ran a comparison of a tree with "
                                   "itself")


class ThePublicArtifactsSecondSide(unittest.TestCase):
    """X7 said the release note carried no digest to compare against. It
    carries one, under another name, and the tag ships its reader."""

    NOTE = ("# Brother 1.0.2\n\nCut from hub commit `cc86283e945f5568`.\n"
            "Export manifest digest `deadbeef` over 3 file(s).\n")

    def _tag(self, work, note=None):
        checkout = os.path.join(work, "tag")
        os.makedirs(os.path.join(checkout, "docs", "releases"))
        os.makedirs(os.path.join(checkout, "scripts"))
        if note is not None:
            with open(os.path.join(checkout, "docs", "releases", "1.0.2.md"),
                      "w", encoding="utf-8") as fh:
                fh.write(note)
        return checkout

    def _reader(self, checkout, exit_code):
        path = os.path.join(checkout, "scripts", "reproduce_export.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("import sys\nprint('fake reader')\nsys.exit(%d)\n" %
                     exit_code)
        return path

    def _args(self, work):
        return argparse.Namespace(version="1.0.2",
                                  work=os.path.join(work, "w"))

    def _gate(self, work):
        return (rc.Gate("X7", "public-artifact", "the published tag"),
                rc.Evidence(os.path.join(work, "evidence")))

    def test_a_tag_with_no_reader_is_no_data_on_the_first_side(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = self._tag(work, self.NOTE)
            gate, ev = self._gate(work)
            verdict, why = rc.manifest_self_consistency(self._args(work), ev,
                                                        gate, checkout)
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("reproduce_export.py", why)

    def test_a_matching_manifest_is_a_pass(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = self._tag(work, self.NOTE)
            self._reader(checkout, 0)
            gate, ev = self._gate(work)
            verdict, why = rc.manifest_self_consistency(self._args(work), ev,
                                                        gate, checkout)
            self.assertEqual(verdict, "PASS")
            self.assertIn("export-manifest.txt", why)

    def test_a_manifest_hash_mismatch_is_a_fail_never_a_no_data(self):
        # The direction that matters: a tag whose shipped bytes disagree with
        # the manifest it ships must go RED, not quietly unmeasured.
        with tempfile.TemporaryDirectory() as work:
            checkout = self._tag(work, self.NOTE)
            self._reader(checkout, 1)
            gate, ev = self._gate(work)
            verdict, why = rc.manifest_self_consistency(self._args(work), ev,
                                                        gate, checkout)
            self.assertEqual(verdict, "FAIL")
            self.assertIn("does NOT match the export manifest", why)

    def test_an_unreadable_manifest_is_no_data_not_a_fail(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = self._tag(work, self.NOTE)
            self._reader(checkout, 2)
            gate, ev = self._gate(work)
            verdict, why = rc.manifest_self_consistency(self._args(work), ev,
                                                        gate, checkout)
            self.assertEqual(verdict, "NO-DATA")

    def test_the_source_revision_is_read_off_the_note_not_typed(self):
        with tempfile.TemporaryDirectory() as work:
            note = os.path.join(work, "note.md")
            with open(note, "w", encoding="utf-8") as fh:
                fh.write(self.NOTE)
            rev, why = rc.source_rev_from_note(note)
            self.assertEqual(rev, "cc86283e945f5568", why)

    def test_a_note_naming_no_revision_is_a_stated_reason(self):
        with tempfile.TemporaryDirectory() as work:
            note = os.path.join(work, "note.md")
            with open(note, "w", encoding="utf-8") as fh:
                fh.write("# Brother 1.0.2\n\nNothing here names a commit.\n")
            rev, why = rc.source_rev_from_note(note)
            self.assertIsNone(rev)
            self.assertIn("names no source revision", why)

    def test_a_note_that_cannot_be_read_says_so(self):
        rev, why = rc.source_rev_from_note("/no/such/release/note.md")
        self.assertIsNone(rev)
        self.assertIn("could not read", why)

    def test_no_note_leaves_the_second_side_no_data(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = self._tag(work, note=None)
            gate, ev = self._gate(work)
            verdict, why = rc.manifest_against_source(self._args(work), ev,
                                                      gate, checkout)
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("no source revision is named", why)

    def test_a_revision_this_checkout_cannot_resolve_is_no_data(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = self._tag(
                work, "Cut from hub commit `0123456789abcdef0123456789abcdef"
                      "01234567`.\n")
            gate, ev = self._gate(work)
            verdict, why = rc.manifest_against_source(self._args(work), ev,
                                                      gate, checkout)
            self.assertEqual(verdict, "NO-DATA")
            self.assertIn("does not resolve", why)
            self.assertIn("private hub", why)


class TheUpgradeRouteTheRunnerRunsIsTheOneTheReadmeDocuments(unittest.TestCase):
    """2026-09-05, row X2: the README told a reader to upgrade by adding the
    marketplace again at the new ref, and Codex refuses that: "marketplace
    'brother' is already added from a different source; remove it before
    adding this source", exit 1, measured against the app-bundled codex in an
    isolated home. The route that works removes the configured marketplace
    first. Both sides are pinned here so the page a reader copies and the leg
    the closeout runs can never drift apart again."""

    SOURCE = "https://github.com/khalilmaaouni/Brother"
    PLACEHOLDER = "<new ref>"

    def _readme_upgrade_block(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "README.md")
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            self.fail("could not read README.md: %s" % exc)
        blocks, keep, current = [], False, []
        for line in text.splitlines():
            if line.startswith("```"):
                if keep:
                    blocks.append("\n".join(current).strip())
                    current = []
                keep = not keep
                continue
            if keep:
                current.append(line)
        found = [b for b in blocks
                 if "codex plugin marketplace" in b and self.PLACEHOLDER in b]
        self.assertEqual(
            len(found), 1,
            "README.md must carry exactly one fenced Codex upgrade block "
            "naming %r; found %d" % (self.PLACEHOLDER, len(found)))
        return found[0]

    def test_the_readme_block_is_the_runners_route_verbatim(self):
        self.assertEqual(
            self._readme_upgrade_block(),
            rc.upgrade_route_shell(self.SOURCE, self.PLACEHOLDER),
            "README.md's Codex upgrade block and release_closeout.py's "
            "UPGRADE_ROUTE disagree. They are the same route: fix both.")

    def test_the_v1_0_3_wording_would_be_refused_by_codex(self):
        """The positive control: the exact line the public 1.0.3 page handed
        a reader, which exits 1 against an installed previous release."""
        refused = ("codex plugin marketplace add %s --ref %s && "
                   "codex plugin add brother@brother --json"
                   % (self.SOURCE, self.PLACEHOLDER))
        self.assertNotEqual(
            self._readme_upgrade_block(), refused,
            "README.md still documents the upgrade Codex refuses.")

    def test_the_route_removes_before_it_adds(self):
        """`codex plugin marketplace upgrade brother` exits 0 and leaves the
        installed version where it was (1.0.2 to 1.0.2, hashes identical,
        measured 2026-09-05), so a route that does not remove first cannot
        move anything."""
        steps = rc.upgrade_route_steps(self.SOURCE, "v1.0.3")
        self.assertEqual(steps[0],
                         ["plugin", "marketplace", "remove", "brother"])
        self.assertEqual(steps[1], ["plugin", "marketplace", "add",
                                    self.SOURCE, "--ref", "v1.0.3"])
        self.assertEqual(steps[2],
                         ["plugin", "add", "brother@brother", "--json"])
        self.assertEqual(len(steps), len(rc.UPGRADE_ROUTE_LABELS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
