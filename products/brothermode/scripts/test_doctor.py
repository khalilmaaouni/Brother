#!/usr/bin/env python3
"""Suite for products/brothermode/scripts/doctor.py's DOC-0 six-row status
table (--status), added beside the existing fifteen-check surface which had
no dedicated test file of its own before this one.

WHY SUBPROCESS, NOT IMPORT: every test here drives the real command,
`python3 products/brothermode/scripts/doctor.py --status`, against a
throwaway HOME, CLAUDE_CONFIG_DIR and CODEX_HOME, the same reasoning
products/brothermode/tools/test_bm_consent.py already states for doctor.py's
other surface: a gate exercised only in-process is not exercised at the
layer a founder's terminal actually uses. -B (PYTHONDONTWRITEBYTECODE)
is passed so a test process never leaves a __pycache__ behind that a
naive "did this write a file" snapshot would misread as the status path's
own doing.

scripts/bundle_runtime.py, scripts/codex_hooks_install.py and
scripts/capability_probe.py are read from the REAL checkout this test file
sits in, exactly as a real invocation would; only CODEX_HOME and the Claude
config directory (via CLAUDE_CONFIG_DIR) are faked per test, which is
enough to drive rows 3, 4 and 5 through every state named in the DOC-0
brief without touching ~/.claude or ~/.codex.

Never imports doctor.py, codex_hooks_install.py or capability_probe.py into
this test process except where a fixture needs codex_hooks_install's own
build()/dump() to generate BYTE-IDENTICAL hooks.json content (loaded by file
path via importlib.util, never sys.path, mirroring doctor.py's own
_load_top_level_module) -- that load runs no subprocess and touches no real
config directory, so it carries none of the risk this row's own design
note (in doctor.py, above _status_codex_hooks) measured for actually
asking Codex.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# HERE is products/brothermode/scripts (this file's own directory, same
# as doctor.py's); three more dirname() calls reach the repo root, the
# same distance doctor.py's own _brother_repo_root() walks from its
# __file__ (one dirname further, since that one starts from the file
# itself rather than from HERE).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DOCTOR = os.path.join(HERE, "doctor.py")
CODEX_HOOKS_INSTALL = os.path.join(REPO_ROOT, "scripts", "codex_hooks_install.py")

assert os.path.isfile(DOCTOR), "doctor.py not found at %s" % DOCTOR
assert os.path.isfile(CODEX_HOOKS_INSTALL), (
    "scripts/codex_hooks_install.py not found at %s" % CODEX_HOOKS_INSTALL)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded once: pure functions only (build/dump/TRUST_BEGIN/TRUST_END), no
# subprocess, no real Codex home touched by importing this.
_chi = _load("codex_hooks_install_for_test", CODEX_HOOKS_INSTALL)


def _expected_hooks_document():
    products = [os.path.join(_chi.repo_root(), rel) for rel in _chi.DEFAULT_PRODUCTS]
    built = _chi.build(products)
    assert not built["problems"], built["problems"]
    return built["document"]


def _expected_hook_count(document):
    return sum(len(block["hooks"]) for blocks in document["hooks"].values()
               for block in blocks)


def _snapshot(root):
    """Every file under `root`, as a sorted list of (relative path, size,
    mtime_ns) triples. Missing `root` snapshots as an empty list, so a
    fixture that a test never populates still compares equal before and
    after. mtime is included (hostile case 7) so a status run that
    rewrites a file's bytes back to themselves, leaving the path list
    unchanged but the mtime bumped, is still caught."""
    if not os.path.isdir(root):
        return []
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            st = os.stat(path)
            out.append((os.path.relpath(path, root), st.st_size, st.st_mtime_ns))
    return sorted(out)


def _run_status(env_overrides, extra_args=(), cwd=None):
    """Runs `doctor.py --status` as a real subprocess under a fully
    isolated environment (only PATH and PYTHONDONTWRITEBYTECODE survive
    from the real process env, plus whatever `env_overrides` names), and
    returns (returncode, stdout). -B / PYTHONDONTWRITEBYTECODE keeps a
    __pycache__ out of the fixture tree so a snapshot diff never mistakes
    ordinary bytecode caching for a status-path write. `cwd`, when given,
    lets a test drive the git isolation row (D4) against a throwaway
    repository rather than whatever repo this test process itself happens
    to run inside."""
    env = {"PATH": os.environ.get("PATH", ""),
          "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(env_overrides)
    r = subprocess.run(
        ["python3", "-B", DOCTOR, "--status"] + list(extra_args),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, timeout=180, env=env, cwd=cwd)
    return r.returncode, r.stdout


def _git(args, cwd):
    """Runs `git <args>` in `cwd`, quiet, raising on any non-zero exit --
    fixture setup for D2 and D4 below has no use for a git failure that
    silently leaves a half-built throwaway repository behind."""
    r = subprocess.run(["git"] + list(args), cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError("git %s failed in %s:\n%s"
                             % (" ".join(args), cwd, r.stdout))
    return r.stdout


class CodexHooksRowTests(unittest.TestCase):
    """Row 4 (Codex safety hooks), all under a throwaway CODEX_HOME."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-codex-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.codex_home = os.path.join(self.tmp, "codex_home")
        os.makedirs(self.codex_home)
        self.claude_config = os.path.join(self.tmp, "claude_config")
        # No settings.json written here on purpose for these tests: the
        # Codex row does not depend on it, and an absent Claude config
        # directory keeps row 3 a quiet N/A instead of noise in stdout.
        self.env = {"HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.claude_config,
                   "CODEX_HOME": self.codex_home}

    def _row_line(self, stdout, label):
        lines = stdout.splitlines()
        for i, line in enumerate(lines):
            if line.startswith(label):
                return line, lines[i + 1:i + 4]
        self.fail("row %r not found in:\n%s" % (label, stdout))

    def test_hooks_missing_is_no_data_or_fail_never_pass(self):
        # codex_home starts empty: no hooks.json at all.
        rc, out = _run_status(self.env)
        header, _rest = self._row_line(out, "Codex safety hooks")
        status = header.split("Codex safety hooks", 1)[1].strip()
        self.assertIn(status, ("NO-DATA", "FAIL"))
        self.assertNotEqual(status, "PASS")

    def test_hooks_present_but_untrusted_needs_trust(self):
        document = _expected_hooks_document()
        with io.open(os.path.join(self.codex_home, "hooks.json"), "w",
                    encoding="utf-8") as fh:
            fh.write(_chi.dump(document))
        # No config.toml at all: never trusted.
        rc, out = _run_status(self.env)
        header, rest = self._row_line(out, "Codex safety hooks")
        status = header.split("Codex safety hooks", 1)[1].strip()
        self.assertEqual(status, "NEEDS TRUST")
        repair_line = next((ln for ln in rest if ln.strip().startswith("repair:")), "")
        self.assertIn("--trust", repair_line)
        self.assertIn("codex_hooks_install.py", repair_line)

    def test_unrelated_user_hooks_preserved_row_unaffected(self):
        document = _expected_hooks_document()
        count = _expected_hook_count(document)
        with io.open(os.path.join(self.codex_home, "hooks.json"), "w",
                    encoding="utf-8") as fh:
            fh.write(_chi.dump(document))
        # A config.toml carrying: (a) a FOREIGN [hooks.state.] table
        # outside Brother's own marker pair (some other tool's own trust
        # decision), and (b) Brother's own marker pair with one real entry
        # per hook command, matching write_trust's own format exactly.
        lines = ['[hooks.state."someone-elses-hook"]', "enabled = true",
                'trusted_hash = "deadbeef"', "", _chi.TRUST_BEGIN]
        for i in range(count):
            lines.append('[hooks.state."fake-key-%d"]' % i)
            lines.append("enabled = true")
            lines.append('trusted_hash = "hash-%d"' % i)
        lines.append(_chi.TRUST_END)
        with io.open(os.path.join(self.codex_home, "config.toml"), "w",
                    encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        rc, out = _run_status(self.env)
        header, rest = self._row_line(out, "Codex safety hooks")
        status = header.split("Codex safety hooks", 1)[1].strip()
        # Every hook command IS represented inside Brother's own marker
        # block, so this reaches the "trust recorded" branch (still
        # NO-DATA rather than PASS by this row's own stated design, see
        # doctor.py's WHY CODEX TRUST IS NEVER CONFIRMED note), never
        # NEEDS TRUST -- the foreign table before the markers must not
        # have been counted as if it were one of Brother's own, and it
        # must not have been dropped either.
        self.assertEqual(status, "NO-DATA")
        message = " ".join(rest)
        self.assertIn("%d trusted hook(s)" % count, message)
        with io.open(os.path.join(self.codex_home, "config.toml"),
                    encoding="utf-8") as fh:
            self.assertIn("someone-elses-hook", fh.read())


class ClaudeHooksRowTests(unittest.TestCase):
    """Row 3 (Claude safety hooks), all under a throwaway CLAUDE_CONFIG_DIR."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-claude-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.claude_config = os.path.join(self.tmp, "claude_config")
        self.env = {"HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.claude_config,
                   "CODEX_HOME": os.path.join(self.tmp, "codex_home")}

    def _status_of(self, out, label):
        for i, line in enumerate(out.splitlines()):
            if line.startswith(label):
                return line.split(label, 1)[1].strip()
        self.fail("row %r not found in:\n%s" % (label, out))

    def test_no_config_dir_is_n_slash_a(self):
        # claude_config is never created: N/A, not NO-DATA and not FAIL.
        rc, out = _run_status(self.env)
        self.assertEqual(self._status_of(out, "Claude safety hooks"), "N/A")

    def test_config_dir_present_no_settings_is_fail_never_pass(self):
        os.makedirs(self.claude_config)
        rc, out = _run_status(self.env)
        status = self._status_of(out, "Claude safety hooks")
        self.assertIn(status, ("FAIL", "NO-DATA"))
        self.assertNotEqual(status, "PASS")


class VaultRowTests(unittest.TestCase):
    """Row 5 (Vault): capability_probe's own NO-DATA must propagate as this
    row's own NO-DATA, tested by injecting a stand-in capability_probe
    module directly at the doctor._load_top_level_module seam rather than
    faking a file on disk -- capability_probe.probe() only returns its own
    top-level NO-DATA when a capability names zero alternatives, and this
    is the most direct way to prove that specific propagation without
    reaching into capability_probe.py's own suite."""

    def test_capability_probe_no_data_propagates_as_no_data(self):
        doctor = _load("doctor_for_vault_test", DOCTOR)

        class _FakeCapabilityProbe:
            PRESENT, MISSING, NODATA = "PRESENT", "MISSING", "NO-DATA"
            CAPABILITIES = ({"name": "vault-recall", "why": "test",
                             "alternatives": []},)

            @staticmethod
            def probe(capability):
                return _FakeCapabilityProbe.NODATA, "", []

        original = doctor._load_top_level_module

        def _fake_loader(name, filename):
            if filename == "capability_probe.py":
                return _FakeCapabilityProbe, None
            return original(name, filename)

        doctor._load_top_level_module = _fake_loader
        try:
            status, message, repair = doctor._status_vault(None)
        finally:
            doctor._load_top_level_module = original
        self.assertEqual(status, "NO-DATA")
        self.assertIsNone(repair)


class SecondRunAndNoWriteTests(unittest.TestCase):
    """A second run prints the same table, and the whole run writes no
    file anywhere under its fixture (snapshotted before and after)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-idem-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.claude_config = os.path.join(self.tmp, "claude_config")
        self.codex_home = os.path.join(self.tmp, "codex_home")
        os.makedirs(self.claude_config)
        os.makedirs(self.codex_home)
        document = _expected_hooks_document()
        with io.open(os.path.join(self.codex_home, "hooks.json"), "w",
                    encoding="utf-8") as fh:
            fh.write(_chi.dump(document))
        self.env = {"HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.claude_config,
                   "CODEX_HOME": self.codex_home}

    def test_second_run_identical_and_writes_no_file(self):
        before = _snapshot(self.tmp)
        rc1, out1 = _run_status(self.env)
        after_first = _snapshot(self.tmp)
        rc2, out2 = _run_status(self.env)
        after_second = _snapshot(self.tmp)
        self.assertEqual(before, after_first,
                         "the first --status run wrote a file under its "
                         "own fixture tree")
        self.assertEqual(after_first, after_second,
                         "the second --status run wrote a file under its "
                         "own fixture tree")
        self.assertEqual(out1, out2,
                         "two consecutive --status runs printed different "
                         "output for the same fixture")
        self.assertEqual(rc1, rc2)


class StatusCombinationTests(unittest.TestCase):
    """D1: --status combined with --json or --strict is refused outright
    (exit 2, one-line reason on stderr/stdout), rather than silently
    ignoring the extra flag as before this repair."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-combo-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"HOME": self.tmp,
                   "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude_config"),
                   "CODEX_HOME": os.path.join(self.tmp, "codex_home")}

    def test_status_with_json_is_refused(self):
        rc, out = _run_status(self.env, extra_args=("--json",))
        self.assertEqual(rc, 2, out)
        self.assertIn("--status", out)
        self.assertIn("--json", out)
        self.assertNotIn("Brother runtime", out)

    def test_status_with_strict_is_refused(self):
        rc, out = _run_status(self.env, extra_args=("--strict",))
        self.assertEqual(rc, 2, out)
        self.assertIn("--status", out)
        self.assertIn("--strict", out)
        self.assertNotIn("Brother runtime", out)

    def test_status_alone_still_works(self):
        rc, out = _run_status(self.env)
        self.assertIn("Brother runtime", out)


class LoadTopLevelModuleSeamTests(unittest.TestCase):
    """D2: _load_top_level_module must refuse a path outside the
    repository's own scripts/ directory, and refuse a path inside it that
    git does not track, rather than exec'ing whatever file the join
    happens to land on."""

    def setUp(self):
        self.doctor = _load("doctor_for_d2_test", DOCTOR)

    def test_path_outside_scripts_dir_is_no_data(self):
        # GANTT.html lives at the repo root, one level above scripts/, and
        # is tracked -- proving the boundary check runs (and refuses)
        # before any existence or tracked check could otherwise let it in.
        module, err = self.doctor._load_top_level_module(
            "escaped", "../GANTT.html")
        self.assertIsNone(module)
        self.assertIn("scripts", err)

    def test_untracked_file_in_scripts_dir_is_no_data(self):
        tmp = tempfile.mkdtemp(prefix="doc0-d2-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git(["init", "-q"], tmp)
        _git(["config", "user.email", "test@example.invalid"], tmp)
        _git(["config", "user.name", "test"], tmp)
        scripts_dir = os.path.join(tmp, "scripts")
        os.makedirs(scripts_dir)
        tracked_path = os.path.join(scripts_dir, "tracked_mod.py")
        with io.open(tracked_path, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        _git(["add", "scripts/tracked_mod.py"], tmp)
        _git(["commit", "-q", "-m", "add tracked module"], tmp)
        untracked_path = os.path.join(scripts_dir, "untracked_mod.py")
        with io.open(untracked_path, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 2\n")

        original_repo_root = self.doctor._brother_repo_root

        def _fake_repo_root():
            return tmp

        self.doctor._brother_repo_root = _fake_repo_root
        try:
            module, err = self.doctor._load_top_level_module(
                "untracked", "untracked_mod.py")
            self.assertIsNone(module)
            self.assertIn("not tracked", err)

            module2, err2 = self.doctor._load_top_level_module(
                "tracked", "tracked_mod.py")
            self.assertIsNotNone(module2, err2)
            self.assertEqual(module2.VALUE, 1)
        finally:
            self.doctor._brother_repo_root = original_repo_root


class ManifestRequiredForNonCheckoutExecTests(unittest.TestCase):
    """Finding 5 (security review, 2026-09-10): outside a checkout,
    _load_top_level_module's only pin was _manifest_digest_ok, which
    returned None -- a pass-through, not a refusal -- both when a
    directory carried no checksum manifest at all and when its manifest
    simply did not list the file being loaded. So a plugin root with no
    manifest gave _exec_module (arbitrary code execution as the user) the
    moment doctor.py --status ran there. Fixed: outside a checkout, only
    a manifest that NAMES the file with a matching sha256 may pass; a
    missing manifest and an unlisted file are now refusals, in the same
    NO-DATA style the rest of this table uses for an unproven state.
    Drives _load_top_level_module directly, mirroring
    LoadTopLevelModuleSeamTests' own D2 fixture above, with _runtime_root
    faked to the "plugin_root" basis so the non-checkout branch runs
    without a subprocess."""

    def setUp(self):
        self.doctor = _load("doctor_for_finding5_test", DOCTOR)
        self.tmp = tempfile.mkdtemp(prefix="doc0-f5-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runtime_dir = os.path.join(self.tmp, "runtime")
        os.makedirs(self.runtime_dir)
        self.module_path = os.path.join(self.runtime_dir, "some_mod.py")
        with io.open(self.module_path, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")

        original_runtime_root = self.doctor._runtime_root

        def _fake_runtime_root():
            return self.tmp, "plugin_root"

        self.doctor._runtime_root = _fake_runtime_root
        self.addCleanup(setattr, self.doctor, "_runtime_root",
                        original_runtime_root)

    def _digest(self):
        with io.open(self.module_path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def test_no_manifest_at_all_is_refused_not_exec(self):
        module, err = self.doctor._load_top_level_module(
            "some_mod", "some_mod.py")
        self.assertIsNone(module)
        self.assertIsNotNone(err)
        self.assertIn("manifest", err.lower())

    def test_manifest_present_but_not_listing_file_is_refused(self):
        manifest_path = os.path.join(self.runtime_dir,
                                     "RUNTIME-MANIFEST.json")
        with io.open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"files": [
                {"path": "some_other_file.py", "sha256": "0" * 64},
            ]}, fh)
        module, err = self.doctor._load_top_level_module(
            "some_mod", "some_mod.py")
        self.assertIsNone(module)
        self.assertIsNotNone(err)

    def test_listed_file_with_matching_digest_still_execs(self):
        manifest_path = os.path.join(self.runtime_dir,
                                     "RUNTIME-MANIFEST.json")
        with io.open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"files": [
                {"path": "some_mod.py", "sha256": self._digest()},
            ]}, fh)
        module, err = self.doctor._load_top_level_module(
            "some_mod", "some_mod.py")
        self.assertIsNotNone(module, err)
        self.assertEqual(module.VALUE, 1)


class MaskHomeTextTests(unittest.TestCase):
    """Finding 6 (security review, 2026-09-10): _mask_home only masks a
    path argument leading with the home directory, so captured
    subprocess output and exception text (which can carry the home
    path anywhere in the string, not only as a prefix) reached printed
    rows unmasked at several sites (bundle_runtime --check output,
    git's own exception text, codex hooks detail, capability_probe's
    vault detail and tried list). _mask_home_text is the new helper
    those sites now run captured text through: a substring replace of
    every occurrence of the home directory, not a prefix check."""

    def setUp(self):
        self.doctor = _load("doctor_for_finding6_test", DOCTOR)

    def test_vault_row_masks_home_path_in_probe_detail(self):
        home = os.path.expanduser("~")
        captured = "checked %s/.codex/vault and %s/.claude/vault" % (
            home, home)

        class _FakeCapabilityProbe:
            PRESENT, MISSING = "PRESENT", "MISSING"
            CAPABILITIES = ({"name": "vault-recall", "why": "test",
                             "alternatives": []},)

            @staticmethod
            def probe(capability):
                return (_FakeCapabilityProbe.MISSING, captured,
                        [("path-a", "state-a", captured)])

        original = self.doctor._load_top_level_module

        def _fake_loader(name, filename):
            if filename == "capability_probe.py":
                return _FakeCapabilityProbe, None
            return original(name, filename)

        self.doctor._load_top_level_module = _fake_loader
        try:
            _status, message, _repair = self.doctor._status_vault(None)
        finally:
            self.doctor._load_top_level_module = original
        self.assertNotIn(home, message)


class CodexTrustCommentTests(unittest.TestCase):
    """D3: a commented-out trust entry (first non-space character '#')
    must not inflate the count, whether counted via tomllib (Python 3.11+)
    or the line-scan fallback used when tomllib is unavailable."""

    def test_commented_block_does_not_inflate_toml_count(self):
        block = "\n".join([
            _chi.TRUST_BEGIN,
            '[hooks.state."real-hook"]',
            "enabled = true",
            'trusted_hash = "abc"',
            "",
            '# [hooks.state."commented-out-hook"]',
            "# enabled = true",
            '# trusted_hash = "def"',
            _chi.TRUST_END,
        ]) + "\n"
        doctor = _load("doctor_for_d3_toml_test", DOCTOR)
        count = doctor._count_trust_entries_by_toml(block)
        self.assertEqual(count, 1)

    def test_commented_block_does_not_inflate_line_scan_count(self):
        block = "\n".join([
            _chi.TRUST_BEGIN,
            '[hooks.state."real-hook"]',
            "enabled = true",
            'trusted_hash = "abc"',
            "",
            '# [hooks.state."commented-out-hook"]',
            "# enabled = true",
            '# trusted_hash = "def"',
            _chi.TRUST_END,
        ]) + "\n"
        doctor = _load("doctor_for_d3_line_test", DOCTOR)
        count = doctor._count_trust_entries_by_line(block)
        self.assertEqual(count, 1)

    def test_end_to_end_status_row_not_inflated_by_comment(self):
        tmp = tempfile.mkdtemp(prefix="doc0-d3-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        codex_home = os.path.join(tmp, "codex_home")
        os.makedirs(codex_home)
        document = _expected_hooks_document()
        count = _expected_hook_count(document)
        with io.open(os.path.join(codex_home, "hooks.json"), "w",
                    encoding="utf-8") as fh:
            fh.write(_chi.dump(document))
        lines = [_chi.TRUST_BEGIN]
        for i in range(count):
            lines.append('[hooks.state."fake-key-%d"]' % i)
            lines.append("enabled = true")
            lines.append('trusted_hash = "hash-%d"' % i)
        # One extra entry, fully commented out: must not count toward the
        # trusted total.
        lines.append('# [hooks.state."fake-key-extra"]')
        lines.append("# enabled = true")
        lines.append('# trusted_hash = "hash-extra"')
        lines.append(_chi.TRUST_END)
        with io.open(os.path.join(codex_home, "config.toml"), "w",
                    encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        env = {"HOME": tmp, "CLAUDE_CONFIG_DIR": os.path.join(tmp, "claude_config"),
              "CODEX_HOME": codex_home}
        rc, out = _run_status(env)
        for i, line in enumerate(out.splitlines()):
            if line.startswith("Codex safety hooks"):
                header = line
                rest = out.splitlines()[i + 1:i + 4]
                break
        else:
            self.fail("row not found in:\n%s" % out)
        message = " ".join(rest)
        self.assertIn("%d trusted hook(s)" % count, message)
        self.assertNotIn("%d trusted hook(s)" % (count + 1), message)


class HostileCase1BothHooksMissingTests(unittest.TestCase):
    """Steering hostile case 1: no hooks file anywhere (no Codex
    hooks.json, no Claude settings.json) and no plugin cache directory
    under HOME at all. Both rows must read FAIL or NO-DATA, never PASS,
    and each row's own repair line must name the installer that would fix
    it (scripts/codex_hooks_install.py for Codex, scripts/install.py for
    Claude)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-hc1-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.claude_config = os.path.join(self.tmp, "claude_config")
        # Config directory exists (so row 3 examines it rather than
        # reporting N/A) but carries no settings.json, and no
        # ~/.claude/plugins/cache directory is created anywhere under
        # this fixture's HOME, so no loader-managed copy can be found
        # either.
        os.makedirs(self.claude_config)
        self.codex_home = os.path.join(self.tmp, "codex_home")
        os.makedirs(self.codex_home)
        self.env = {"HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.claude_config,
                   "CODEX_HOME": self.codex_home}

    def _row(self, out, label):
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if line.startswith(label):
                status = line.split(label, 1)[1].strip()
                return status, lines[i + 1:i + 4]
        self.fail("row %r not found in:\n%s" % (label, out))

    def test_both_rows_never_pass_and_name_their_installer(self):
        self.assertFalse(
            os.path.isdir(os.path.join(self.tmp, ".claude", "plugins", "cache")),
            "fixture setup grew a plugin cache directory it should not have")
        rc, out = _run_status(self.env)

        claude_status, claude_rest = self._row(out, "Claude safety hooks")
        self.assertIn(claude_status, ("FAIL", "NO-DATA"))
        self.assertNotEqual(claude_status, "PASS")
        claude_repair = " ".join(claude_rest)
        self.assertIn("install.py", claude_repair)

        codex_status, codex_rest = self._row(out, "Codex safety hooks")
        self.assertIn(codex_status, ("FAIL", "NO-DATA"))
        self.assertNotEqual(codex_status, "PASS")
        codex_repair = " ".join(codex_rest)
        self.assertIn("codex_hooks_install.py", codex_repair)


class HostileCase5PartialInstallTests(unittest.TestCase):
    """Steering hostile case 5: a partial prior install, this machine's
    own real state as of DOC-0's first run -- a plugin-cache copy exists
    and its hooks/hooks.json names the fence, but the fence file itself
    (tools/bm_fence_hook.py) is missing from that copy. The Claude row
    must FAIL, naming the missing file and the repair, never NO-DATA and
    never PASS: this is a definite, examined finding, not an absence of
    signal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-hc5-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.claude_config = os.path.join(self.tmp, "claude_config")
        os.makedirs(self.claude_config)
        # No fence entry in settings.json, so doctor() falls through to
        # loader_managed_fence_copies() -- the same path a real
        # plugin-loaded install takes.
        with io.open(os.path.join(self.claude_config, "settings.json"), "w",
                    encoding="utf-8") as fh:
            fh.write("{}\n")
        cache_dir = os.path.join(
            self.tmp, ".claude", "plugins", "cache", "test-marketplace", "brother")
        hooks_dir = os.path.join(cache_dir, "hooks")
        os.makedirs(hooks_dir)
        with io.open(os.path.join(hooks_dir, "hooks.json"), "w",
                    encoding="utf-8") as fh:
            fh.write('{"hooks": {"PreToolUse": [{"matcher": "Edit", '
                     '"hooks": [{"command": '
                     '"python3 ${CLAUDE_PLUGIN_ROOT}/tools/bm_fence_hook.py"}]}]}}\n')
        # tools/bm_fence_hook.py is deliberately never created: this is
        # the exact partial-install shape.
        self.env = {"HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.claude_config,
                   "CODEX_HOME": os.path.join(self.tmp, "codex_home")}

    def test_claude_row_fails_naming_the_missing_file_and_the_repair(self):
        tools_dir = os.path.join(
            self.tmp, ".claude", "plugins", "cache", "test-marketplace",
            "brother", "tools")
        self.assertFalse(os.path.isdir(tools_dir),
                         "fixture setup created the tools/ dir it must omit")
        rc, out = _run_status(self.env)
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Claude safety hooks"):
                status = line.split("Claude safety hooks", 1)[1].strip()
                rest = " ".join(lines[i + 1:i + 4])
                break
        else:
            self.fail("row not found in:\n%s" % out)
        self.assertEqual(status, "FAIL")
        self.assertIn("bm_fence_hook.py", rest)
        self.assertIn("install.py --upgrade", rest)



class GitIsolationCanonicalTests(unittest.TestCase):
    """D4: the git isolation row, run from a linked worktree, must also
    surface the canonical (main) worktree's own status rather than only
    describing the linked worktree it is actually running in."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-d4-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main_repo = os.path.join(self.tmp, "main")
        os.makedirs(self.main_repo)
        _git(["init", "-q"], self.main_repo)
        _git(["config", "user.email", "test@example.invalid"], self.main_repo)
        _git(["config", "user.name", "test"], self.main_repo)
        with io.open(os.path.join(self.main_repo, "a.txt"), "w",
                    encoding="utf-8") as fh:
            fh.write("one\n")
        _git(["add", "a.txt"], self.main_repo)
        _git(["commit", "-q", "-m", "initial"], self.main_repo)
        _git(["branch", "lane"], self.main_repo)
        self.lane_dir = os.path.join(self.tmp, "lane")
        _git(["worktree", "add", "-q", self.lane_dir, "lane"], self.main_repo)
        self.env = {"HOME": self.tmp,
                   "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude_config"),
                   "CODEX_HOME": os.path.join(self.tmp, "codex_home")}

    def _git_isolation_status(self, out):
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Git isolation"):
                status = line.split("Git isolation", 1)[1].strip()
                return status, lines[i + 1:i + 4]
        self.fail("row not found in:\n%s" % out)

    def test_dirty_main_surfaces_behind_a_clean_linked_worktree(self):
        # D9: dirty the canonical (main) worktree, leave the linked one
        # clean. Before D9 this row printed PASS with "DIRTY" sitting
        # inside its own message -- a verdict in the text and not in the
        # value, so the SAME repository state read PASS one directory
        # over from where it read FAIL (running from main_repo itself,
        # below). A dirty canonical checkout must FAIL from any
        # worktree; the linked-worktree fact stays in the message.
        with io.open(os.path.join(self.main_repo, "a.txt"), "w",
                    encoding="utf-8") as fh:
            fh.write("one\ntwo\n")
        rc, out = _run_status(self.env, cwd=self.lane_dir)
        status, rest = self._git_isolation_status(out)
        self.assertEqual(status, "FAIL")
        message = " ".join(rest)
        self.assertIn("linked worktree", message)
        self.assertIn("DIRTY", message)

    def test_clean_main_is_named_clean_behind_a_linked_worktree(self):
        rc, out = _run_status(self.env, cwd=self.lane_dir)
        status, rest = self._git_isolation_status(out)
        self.assertEqual(status, "PASS")
        message = " ".join(rest)
        self.assertIn("linked worktree", message)
        self.assertIn("clean", message)

    def test_running_from_canonical_root_reports_dirty_directly(self):
        # Hostile case 8: --status run with cwd at the CANONICAL checkout
        # root itself (main_repo), not a linked worktree. .git there is a
        # directory, so the row's own "else" branch runs (git status on
        # top directly) rather than the linked-worktree branch above.
        with io.open(os.path.join(self.main_repo, "a.txt"), "w",
                    encoding="utf-8") as fh:
            fh.write("one\ntwo\n")
        rc, out = _run_status(self.env, cwd=self.main_repo)
        status, rest = self._git_isolation_status(out)
        self.assertEqual(status, "FAIL")
        message = " ".join(rest)
        self.assertIn("dirty", message)
        self.assertNotIn("linked worktree", message)

    def test_running_from_canonical_root_reports_clean_directly(self):
        rc, out = _run_status(self.env, cwd=self.main_repo)
        status, rest = self._git_isolation_status(out)
        self.assertEqual(status, "PASS")
        message = " ".join(rest)
        self.assertIn("is clean", message)
        self.assertNotIn("linked worktree", message)


class StatusExitCodeTests(unittest.TestCase):
    """D5: main_status's own exit code, computed purely by
    doctor._status_exit_code from a list of (label, status, message,
    repair) rows. Fabricated rows, no environment to fake -- the whole
    point of pulling this decision out into its own function."""

    def setUp(self):
        self.doctor = _load("doctor_for_exit_code_test", DOCTOR)

    def test_any_fail_is_exit_problems(self):
        rows = [("a", "PASS", "", None), ("b", "FAIL", "", None),
               ("c", "NO-DATA", "", None)]
        self.assertEqual(self.doctor._status_exit_code(rows),
                         self.doctor.EXIT_PROBLEMS)

    def test_at_least_one_pass_and_no_fail_is_exit_ok(self):
        rows = [("a", "PASS", "", None), ("b", "NO-DATA", "", None),
               ("c", "N/A", "", None)]
        self.assertEqual(self.doctor._status_exit_code(rows), self.doctor.EXIT_OK)

    def test_no_fail_and_no_pass_is_exit_no_pass(self):
        rows = [("a", "NO-DATA", "", None), ("b", "N/A", "", None),
               ("c", "NEEDS TRUST", "", None),
               ("d", "NOT CONFIGURED", "", None)]
        self.assertEqual(self.doctor._status_exit_code(rows),
                         self.doctor.EXIT_NO_PASS)
        # D11: EXIT_NO_PASS used to share the integer 3 with
        # EXIT_UNSUPPORTED, so a caller reading only the exit code could
        # not tell "no row passed" from "wrong Python". It now names its
        # own integer, distinct from every other EXIT_ constant in the
        # file.
        self.assertEqual(self.doctor.EXIT_NO_PASS, 4)
        self.assertNotEqual(self.doctor.EXIT_NO_PASS, self.doctor.EXIT_UNSUPPORTED)


class StatusAllNoDataRegressionTests(unittest.TestCase):
    """The exact scenario the second adversarial reviewer drove: all six
    real row functions forced to NO-DATA. Before D5 this printed a
    six-row NO-DATA table and returned exit 0 (the estate's own recorded
    law failure, "a population of all NO-DATA composed into a PASS"); it
    must now return EXIT_NO_PASS (4, its own integer, distinct from
    EXIT_UNSUPPORTED since D11) and say plainly that the table measured
    nothing that passed."""

    def setUp(self):
        self.doctor = _load("doctor_for_regression_test", DOCTOR)
        self.original_rows = self.doctor._STATUS_ROWS

        def _forced_no_data(_settings_path):
            return ("NO-DATA", "forced by test", None)

        self.doctor._STATUS_ROWS = tuple(
            (label, _forced_no_data) for label, _func in self.original_rows)
        self.addCleanup(setattr, self.doctor, "_STATUS_ROWS", self.original_rows)

    def test_all_no_data_rows_exit_no_pass_never_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self.doctor.main_status("/does/not/matter/settings.json")
        out = buf.getvalue()
        self.assertEqual(code, self.doctor.EXIT_NO_PASS)
        self.assertNotEqual(code, 0)
        self.assertEqual(out.count("NO-DATA"), len(self.original_rows))
        self.assertIn("measured nothing that passed", out)


class StatusCrashedRowTests(unittest.TestCase):
    """C-3 (repair, backend review 2026-09-10): a row function that raises
    must not let the table exit 0 just because the other five rows read
    PASS. Same fixture shape as StatusAllNoDataRegressionTests above --
    _STATUS_ROWS is replaced wholesale with forced functions -- but here
    every row but one is forced to PASS and the remaining one (the
    safety-fence row, "Claude safety hooks") is forced to raise: exactly
    the shape that used to read EXIT_OK, because run_status_rows turned
    the crash into a plain NO-DATA and _status_exit_code only looked at
    FAIL and PASS."""

    def setUp(self):
        self.doctor = _load("doctor_for_crashed_row_test", DOCTOR)
        self.original_rows = self.doctor._STATUS_ROWS

        def _forced_pass(_settings_path):
            return ("PASS", "forced by test", None)

        def _forced_crash(_settings_path):
            raise RuntimeError("boom")

        rows = []
        for label, _func in self.original_rows:
            if label == "Claude safety hooks":
                rows.append((label, _forced_crash))
            else:
                rows.append((label, _forced_pass))
        self.doctor._STATUS_ROWS = tuple(rows)
        self.addCleanup(setattr, self.doctor, "_STATUS_ROWS", self.original_rows)

    def test_one_crashed_row_exits_nonzero_even_with_five_passes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self.doctor.main_status("/does/not/matter/settings.json")
        out = buf.getvalue()
        self.assertNotEqual(code, 0)
        self.assertEqual(code, self.doctor.EXIT_PROBLEMS)
        self.assertIn("this row crashed", out)

    def test_no_crash_exit_code_unchanged(self):
        # Inverse: all six rows forced PASS, none raises. The fix must not
        # touch the healthy path -- exit code stays EXIT_OK.
        def _forced_pass(_settings_path):
            return ("PASS", "forced by test", None)

        self.doctor._STATUS_ROWS = tuple(
            (label, _forced_pass) for label, _func in self.original_rows)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self.doctor.main_status("/does/not/matter/settings.json")
        self.assertEqual(code, self.doctor.EXIT_OK)


class RecoveryStoreRowTests(unittest.TestCase):
    """Row 6, D6 and D7 repairs, driven in-process against a throwaway
    docs/plan/runs tree by monkeypatching _brother_repo_root -- the same
    technique LoadTopLevelModuleSeamTests already uses above to redirect
    this row's own repo-root lookup without touching the real checkout."""

    def setUp(self):
        self.doctor = _load("doctor_for_recovery_test", DOCTOR)
        self.tmp = tempfile.mkdtemp(prefix="doc0-recovery-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runs_dir = os.path.join(self.tmp, "docs", "plan", "runs")
        os.makedirs(self.runs_dir)
        original_repo_root = self.doctor._brother_repo_root
        self.doctor._brother_repo_root = lambda: self.tmp
        self.addCleanup(setattr, self.doctor, "_brother_repo_root", original_repo_root)

    def _make_run(self, name, files, mtime=None):
        """files: {filename: content}. Every file is written first, and the
        directory's own mtime is pinned LAST when given -- writing a file
        into a directory updates that directory's own mtime, so pinning it
        any earlier would just be overwritten by the next write."""
        run_dir = os.path.join(self.runs_dir, name)
        os.makedirs(run_dir)
        for filename, content in files.items():
            with io.open(os.path.join(run_dir, filename), "w",
                        encoding="utf-8") as fh:
                fh.write(content)
        if mtime is not None:
            os.utime(run_dir, (mtime, mtime))
        return run_dir

    # D6
    def test_zero_line_journal_is_no_data_never_pass(self):
        self._make_run("run-a", {"journal.jsonl": ""})
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertEqual(status, "NO-DATA")
        self.assertNotEqual(status, "PASS")
        self.assertIn("journal.jsonl", message)
        self.assertIn("zero lines", message)

    def test_bad_last_line_journal_is_no_data_never_pass(self):
        self._make_run("run-a", {"journal.jsonl":
                                 '{"at": "2026-09-09T00:00:00+00:00"}\nnot json\n'})
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertEqual(status, "NO-DATA")
        self.assertNotEqual(status, "PASS")
        self.assertIn("journal.jsonl", message)

    def test_good_last_line_journal_is_pass(self):
        self._make_run("run-a", {"journal.jsonl":
                                 '{"at": "2026-09-09T00:00:00+00:00"}\n'})
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertEqual(status, "PASS")
        self.assertIn("journal.jsonl", message)

    # D7
    def test_newest_run_picked_by_journal_timestamp_not_mtime(self):
        # run-new is touched (mtime) FIRST and never again; run-old is
        # touched (mtime) LAST, well after run-new's own creation -- so
        # mtime alone would pick run-old. But run-new's journal carries
        # the NEWER "at" timestamp (as if appended to well after both
        # directories were first created), which is what must win.
        self._make_run("run-new", {"journal.jsonl":
                                   '{"at": "2026-09-10T12:00:00+00:00"}\n'},
                       mtime=1000)
        self._make_run("run-old", {"journal.jsonl":
                                   '{"at": "2026-09-01T00:00:00+00:00"}\n'},
                       mtime=2000)
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertEqual(status, "PASS")
        self.assertIn("run-new", message)
        self.assertNotIn("run-old", message)
        self.assertNotIn("directory modification", message)

    def test_falls_back_to_mtime_when_no_journal_has_a_timestamp(self):
        self._make_run("run-a", {"capsule.json": "{}"}, mtime=1000)
        self._make_run("run-b", {"capsule.json": "{}"}, mtime=2000)
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertEqual(status, "PASS")
        self.assertIn("run-b", message)
        self.assertNotIn("run-a", message)
        self.assertIn("directory modification", message)

    # D10
    def test_newer_journal_less_directory_beats_older_journaled_one(self):
        # Before D10, _newest_run_dir only ever considered directories
        # whose journal.jsonl carried a parseable timestamp; a run
        # directory with no journal at all was invisible, not merely
        # ranked behind. Driven case: the row reported PASS about a run
        # four days older than the newest directory actually on disk.
        # run-old carries a real journal timestamp; run-new has NO
        # journal at all but a far newer directory modification time, and
        # holds none of the three recovery files, so once it is correctly
        # picked its own row reads NO-DATA -- never hidden behind a stale
        # PASS about run-old.
        self._make_run("run-old", {"journal.jsonl":
                                   '{"at": "2026-09-01T00:00:00+00:00"}\n'},
                       mtime=1000)
        # run-new's own directory mtime must be a real, current wall-clock
        # time (not an arbitrary small epoch offset): _newest_run_dir now
        # compares a journal timestamp and a directory mtime on the SAME
        # real-calendar axis, so a tiny epoch offset would read as
        # ancient next to run-old's real 2026 journal date and defeat the
        # very case this test drives.
        self._make_run("run-new", {"unrelated.txt": "x"}, mtime=time.time())
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertEqual(status, "NO-DATA")
        # The row is about run-new, the truly newest directory, never a
        # stale PASS about run-old. Only the parenthetical note (the
        # "print ... that a newer journal-less directory exists" half of
        # the D10 fix) may still name run-old, as the journaled run
        # run-new outranked; the primary statement never does.
        primary = message.split(" (", 1)[0]
        self.assertIn("the most recent run at", primary)
        self.assertIn("run-new", primary)
        self.assertNotIn("run-old", primary)

    # D12
    def test_mixed_naive_and_aware_journal_timestamps_do_not_crash(self):
        # One journal "at" field with a UTC offset, one without. Comparing
        # an offset-aware and an offset-naive datetime directly raises
        # TypeError, which used to degrade this whole row to "this row
        # crashed". A naive timestamp is now treated as UTC before any
        # comparison happens.
        self._make_run("run-naive", {"journal.jsonl":
                                     '{"at": "2026-09-10T00:00:00"}\n'})
        self._make_run("run-aware", {"journal.jsonl":
                                     '{"at": "2026-09-01T00:00:00+00:00"}\n'})
        status, message, _repair = self.doctor._status_recovery_store(None)
        self.assertNotIn("crashed", message)
        self.assertEqual(status, "PASS")
        self.assertIn("run-naive", message)
        self.assertNotIn("run-aware", message)


class InstalledPluginNoGitTests(unittest.TestCase):
    """D13: on an installed plugin (a copy with no .git anywhere near it)
    _load_top_level_module used to require git ls-files to confirm a
    sibling script tracked, and _brother_repo_root's own four-up walk
    from doctor.py's __file__ never lands anywhere git recognises, so
    every row that loads a sibling script through that seam (Codex safety
    hooks, Vault) degraded to NO-DATA on the exact install the doctor
    exists to examine. Fixture: doctor.py itself, plus the sibling
    scripts it loads, copied into a temp directory carrying no .git at
    all, laid out the way an installed plugin's own runtime mirror is
    laid out (scripts/bundle_runtime.py's own bundle/runtime/), with
    CLAUDE_PLUGIN_ROOT pointed at that temp root -- the same environment
    variable brother_paths.plugin_root() reads first when nothing more
    specific is set, and the one Claude Code itself exports to every
    plugin hook process."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="doc0-d13-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # doctor.py's own _brother_repo_root walks four directories up
        # from its __file__: mirror that exact depth so the copy resolves
        # its own checkout root to self.tmp, same as the real one does
        # relative to the real repo root.
        self.doctor_copy_dir = os.path.join(
            self.tmp, "products", "brothermode", "scripts")
        os.makedirs(self.doctor_copy_dir)
        self.doctor_copy = os.path.join(self.doctor_copy_dir, "doctor.py")
        shutil.copyfile(DOCTOR, self.doctor_copy)
        # doctor.py imports scripts/setup.py from its own directory at
        # module load time ("same directory, deliberate", its own
        # comment says); the fixture must carry that sibling too, or the
        # copy never even starts.
        shutil.copyfile(os.path.join(HERE, "setup.py"),
                        os.path.join(self.doctor_copy_dir, "setup.py"))
        # No .git anywhere under self.tmp: this is the install fixture,
        # not a checkout.
        self.runtime_dir = os.path.join(self.tmp, "runtime")
        os.makedirs(self.runtime_dir)
        digests = {}
        for filename in ("capability_probe.py", "codex_hooks_install.py",
                         "brother_paths.py"):
            dest = os.path.join(self.runtime_dir, filename)
            shutil.copyfile(os.path.join(REPO_ROOT, "scripts", filename), dest)
            with io.open(dest, "rb") as fh:
                digests[filename] = hashlib.sha256(fh.read()).hexdigest()
        self.manifest_path = os.path.join(self.runtime_dir,
                                          "RUNTIME-MANIFEST.json")
        self._write_manifest(digests)
        self.env = {"PATH": os.environ.get("PATH", ""),
                   "PYTHONDONTWRITEBYTECODE": "1",
                   "CLAUDE_PLUGIN_ROOT": self.tmp,
                   "HOME": self.tmp,
                   "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude_config"),
                   "CODEX_HOME": os.path.join(self.tmp, "codex_home")}

    def _run(self):
        r = subprocess.run(
            ["python3", "-B", self.doctor_copy, "--status"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=180, env=self.env,
            cwd=self.tmp)
        return r.returncode, r.stdout

    def _write_manifest(self, digests):
        with io.open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump({"files": [{"path": name, "sha256": digest}
                                 for name, digest in digests.items()]}, fh)

    def test_rows_are_measured_not_no_data_from_the_loader(self):
        rc, out = self._run()
        # Neither row's own "could not load" failure message may appear:
        # that message is this seam's own load failure, distinct from a
        # legitimate measured outcome (NOT CONFIGURED, NEEDS TRUST, an
        # actual FAIL) that a row can honestly reach once loading itself
        # succeeded.
        self.assertNotIn("could not load scripts/codex_hooks_install.py", out)
        self.assertNotIn("could not load scripts/capability_probe.py", out)
        self.assertIn("Codex safety hooks", out)
        self.assertIn("Vault", out)

    def test_tampered_file_beside_a_real_manifest_is_refused(self):
        # A checksum manifest naming this file with a digest that does not
        # match its actual bytes must be refused (NO-DATA), never loaded:
        # this is the tamper case a shipped RUNTIME-MANIFEST.json exists
        # to catch.
        # Finding 5 (security review, 2026-09-10): the other two
        # sibling files keep their own correct digests, so this stays a
        # test of the ONE tampered file, not a rerun of the missing or
        # unlisted refusal the class above already covers.
        digests = {}
        for filename in ("codex_hooks_install.py", "brother_paths.py"):
            with io.open(os.path.join(self.runtime_dir, filename), "rb") as fh:
                digests[filename] = hashlib.sha256(fh.read()).hexdigest()
        digests["capability_probe.py"] = "0" * 64
        self._write_manifest(digests)
        rc, out = self._run()
        self.assertNotIn("could not load scripts/codex_hooks_install.py", out)
        vault_line_found = False
        for i, line in enumerate(out.splitlines()):
            if line.startswith("Vault"):
                vault_line_found = True
                status = line.split("Vault", 1)[1].strip()
                self.assertEqual(status, "NO-DATA")
        self.assertTrue(vault_line_found, out)


if __name__ == "__main__":
    unittest.main()
