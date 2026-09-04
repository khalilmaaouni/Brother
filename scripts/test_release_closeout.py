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


if __name__ == "__main__":
    unittest.main(verbosity=2)
