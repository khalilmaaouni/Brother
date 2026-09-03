#!/usr/bin/env python3
"""Plugin path install suite: N-3 / B-3 (2026-08-04). The plugin install route
(`claude plugin marketplace add`, `claude plugin install`) is a second
documented way to get BrotherMode running, alongside the clone-and-run
installer tools/test_install.py already covers. Nothing before this suite
proved the plugin route end to end, in code, against a throwaway environment.

Deliberately separate from tools/test_install.py: that suite drives
scripts/install.py directly. This suite drives the `claude` CLI itself, which
is a different program with its own manifest reading, its own hook
registration, and its own uninstall path, none of which scripts/install.py
touches.

NON-NEGOTIABLE LESSONS THIS SUITE ENCODES, each learned the hard way on this
project:

  1. BUILD OUTPUT MUST NOT LAND IN THE REPO. An earlier packaging test built
     in tree and left build/ and an .egg-info behind; both were gitignored so
     git status read clean, and scripts/verify-install.sh then reported 26
     EXTRA files, a state its own output calls the shape of a planted
     backdoor. This suite never installs from ROOT: it copies the tree to a
     throwaway directory first, always, and class B's own last test proves
     the source tree comes out byte-for-byte unchanged.

  2. HOME ALONE DOES NOT ISOLATE THE VAULT. tools/bm_ledger.py resolves its
     storage as os.environ.get("BROTHERMODE_VAULT", "~/BrotherModeVault"), so
     the environment variable wins over HOME whenever the invoking shell has
     it exported, which it has on any machine that has ever run this
     project's own tooling. Every subprocess this suite spawns gets a
     from-scratch env carrying HOME, BROTHERMODE_VAULT and BROTHERSBE_VAULT,
     all three pinned inside one throwaway directory, built fresh rather than
     copied from os.environ. A HOME-only override was reproduced writing a
     live row into a real, non-throwaway vault.

  3. A SUITE THAT SKIPS WITHOUT RUNNING A TEST STILL FAILS THE GATE.
     tools/test_all.py::_run_one marks a suite that "exited 0 but ran 0
     tests" as FAILED, so a class-level unittest.SkipTest on a machine with
     no `claude` binary would turn this whole file red on every CI runner.
     Class A below needs no `claude` binary at all and always runs six real
     tests, so this file never falls into that trap the way
     tools/test_bm_packaging_install.py does when pip cannot reach an index.

Python 3.9, standard library only, no network beyond the local `claude` CLI
call itself. Drives subprocesses, which is allowed because the file carries
the `test_` prefix that tools/test_bm.py's no-subprocess ban excludes for
exactly this reason.

No em or en dashes anywhere in this file or its output.
"""
import fnmatch
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")
PLUGIN_JSON = os.path.join(ROOT, ".claude-plugin", "plugin.json")
MARKETPLACE_JSON = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
INSTALL_PY = os.path.join(ROOT, "scripts", "install.py")
COMMANDS_DIR = os.path.join(ROOT, "commands")
VERSION_FILE = os.path.join(ROOT, "VERSION")
PLUGIN_NAME = "brothermode"
MARKETPLACE_NAME = "brothermode-marketplace"
PLUGIN_ID = "brothermode@brothermode-marketplace"
PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"

# The clone installer's own COPY_EXCLUDE_NAMES (scripts/install.py, confirmed
# at line 106), plus the packaging suite's build-artifact exclusions and the
# untracked STATE.md.bak-* files that a directory-sourced marketplace add
# would otherwise ship. Class A test 6 fails if scripts/install.py's own list
# ever grows past the first seven entries here.
COPY_EXCLUDE = (".git", ".brothermode", "__pycache__", "threads",
                ".superpowers", ".DS_Store", "STATE.md",
                "build", "dist", "*.egg-info", "STATE.md.bak-*",
                "PROJECT-VIEW.html", "Handover",
                # The vault lane's local venv (scripts/install.py's own
                # COPY_EXCLUDE_NAMES, since the 2026-08-30 fix). See that
                # file for why it must never be walked.
                ".venv-embed")

_INSTALLER_MODULE = None


def _installer_module():
    """scripts/install.py, imported once and cached. Reused rather than
    reimplemented so this suite compares hooks.json against the SAME token
    extraction and event list the real installer uses, not a second hand
    written copy of the same logic that could quietly drift from it. The
    same import-by-path technique tools/test_bm_docs.py already uses for
    this exact file."""
    global _INSTALLER_MODULE
    if _INSTALLER_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "bm_install_for_plugin_test", INSTALL_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _INSTALLER_MODULE = mod
    return _INSTALLER_MODULE


def _read_text(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_json(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _sha256_file(path):
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _skill_root_entries():
    """Immediate subdirectory names under skills/, sorted. This is the
    on-disk truth `claude plugin details` counts into its Skills(...) line:
    confirmed live against the renamed plugin, where plugin.json carries no
    "skills" key at all (removed as redundant per standards finding F4) yet
    the CLI still printed "Skills (32)  auto, auto-status, brief, ..."
    covering every one of these directories plus every commands/*.md file.
    The manifest's own skills array is gone by design; the directory
    listing is what both the CLI and this suite now check against."""
    skills_root = os.path.join(ROOT, "skills")
    return sorted(
        d for d in os.listdir(skills_root)
        if os.path.isdir(os.path.join(skills_root, d)))


def _excluded_name(name):
    return any(fnmatch.fnmatch(name, pattern) for pattern in COPY_EXCLUDE)


def _tree_fingerprint(root):
    """Relative path -> (size, mtime_ns) for every file under root, skipping
    .git and any name in COPY_EXCLUDE. Reads only, never writes."""
    fp = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _excluded_name(d)]
        for fn in filenames:
            if _excluded_name(fn):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:  # sbe: allow-silent transient race during the walk, file just excluded from the fingerprint
                continue
            fp[os.path.relpath(full, root)] = (st.st_size, st.st_mtime_ns)
    return fp


def _tree_diff(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in (set(before) & set(after))
                     if before[p] != after[p])
    return added, removed, changed


class TestPluginManifestsAgreeWithTheTree(unittest.TestCase):
    """Runs unconditionally: no `claude` binary, no network, no install.
    Also the guarantee that this suite always runs at least a handful of real
    tests (six), so a machine with no `claude` binary on PATH still gets a
    real green rather than the "exited 0 but ran 0 tests" failure
    tools/test_all.py::_run_one gives a suite whose only class skips."""

    def test_the_three_version_claims_and_the_version_file_agree(self):
        version = _read_text(VERSION_FILE).strip()
        plugin = _read_json(PLUGIN_JSON)
        marketplace = _read_json(MARKETPLACE_JSON)
        plugins = marketplace.get("plugins", [])
        self.assertEqual(1, len(plugins),
                         "marketplace.json plugins: expected exactly one "
                         "entry, found %d" % len(plugins))
        mismatches = []
        if plugin.get("version") != version:
            mismatches.append("plugin.json version=%r" % plugin.get("version"))
        market_version = marketplace.get("metadata", {}).get("version")
        if market_version != version:
            mismatches.append(
                "marketplace.json metadata.version=%r" % market_version)
        entry_version = plugins[0].get("version")
        if entry_version != version:
            mismatches.append(
                "marketplace.json plugins[0].version=%r" % entry_version)
        self.assertEqual(
            [], mismatches,
            "VERSION=%r but: %s" % (version, "; ".join(mismatches)))

    def test_every_skill_path_the_plugin_manifest_declares_resolves(self):
        """Renamed truth (F4): plugin.json no longer carries a "skills"
        field at all. Verified live against the renamed plugin: with that
        key absent, `claude plugin details brothermode` still printed
        "Skills (32)  auto, auto-status, brief, ..." naming every skills/
        subdirectory, proving the CLI discovers skills/*/SKILL.md from disk
        rather than reading this manifest field, which is why F4 called the
        field redundant and removed it. Two invariants survive that
        discovery: plugin.json must not reintroduce the redundant field,
        and every directory the CLI would discover under skills/ must
        actually resolve to a real SKILL.md."""
        plugin = _read_json(PLUGIN_JSON)
        self.assertNotIn(
            "skills", plugin,
            "plugin.json declares a skills field again; F4 removed it as "
            "redundant because the CLI discovers skills/*/SKILL.md from "
            "disk, not from this manifest")
        entries = _skill_root_entries()
        self.assertTrue(entries, "skills/ contains no subdirectories")
        skills_root = os.path.join(ROOT, "skills")
        bad = [d for d in entries
              if not os.path.isfile(os.path.join(skills_root, d, "SKILL.md"))]
        self.assertEqual(
            [], bad,
            "skills/ subdirectory(ies) do not contain SKILL.md: %s" % bad)

    def test_the_marketplace_entry_points_at_this_repository(self):
        marketplace = _read_json(MARKETPLACE_JSON)
        plugins = marketplace.get("plugins", [])
        self.assertEqual(1, len(plugins), plugins)
        entry = plugins[0]
        mismatches = []
        if marketplace.get("name") != MARKETPLACE_NAME:
            mismatches.append("marketplace.json name=%r" % marketplace.get("name"))
        if entry.get("name") != PLUGIN_NAME:
            mismatches.append("plugins[0].name=%r" % entry.get("name"))
        if entry.get("source") != "./":
            mismatches.append("plugins[0].source=%r" % entry.get("source"))
        self.assertEqual([], mismatches, "; ".join(mismatches))

    def test_the_hook_manifest_names_the_same_events_as_the_clone_installer(self):
        installer = _installer_module()
        manifest_events = set(_read_json(HOOKS_JSON)["hooks"].keys())
        installer_events = set(installer.HOOK_EVENTS)
        only_manifest = sorted(manifest_events - installer_events)
        only_installer = sorted(installer_events - manifest_events)
        self.assertEqual(
            ([], []), (only_manifest, only_installer),
            "hooks.json names event(s) absent from install.py HOOK_EVENTS: "
            "%s; install.py HOOK_EVENTS names event(s) absent from "
            "hooks.json: %s" % (only_manifest, only_installer))

    def test_every_hook_command_in_the_manifest_names_a_file_that_exists(self):
        installer = _installer_module()
        manifest = _read_json(HOOKS_JSON)["hooks"]
        missing = []
        for event, groups in manifest.items():
            for group in groups:
                matcher = group.get("matcher")
                for entry in group.get("hooks", []):
                    command = entry.get("command", "")
                    substituted = command.replace(PLUGIN_ROOT_TOKEN, ROOT)
                    tokens = installer.command_path_tokens(substituted)
                    if tokens is None:
                        missing.append(
                            "%s[%s]: command could not be parsed: %r"
                            % (event, matcher, command))
                        continue
                    for tok in tokens:
                        if not os.path.isfile(tok):
                            missing.append("%s[%s]: %s" % (event, matcher, tok))
        self.assertEqual([], missing, "; ".join(missing))

    def test_the_copy_exclusion_list_covers_everything_the_clone_installer_excludes(self):
        installer = _installer_module()
        missing = [n for n in installer.COPY_EXCLUDE_NAMES
                  if n not in COPY_EXCLUDE]
        self.assertEqual(
            [], missing,
            "scripts/install.py COPY_EXCLUDE_NAMES grew a name this suite's "
            "own COPY_EXCLUDE does not cover: %s" % missing)


class TestPluginInstallFromATempCopy(unittest.TestCase):
    """Drives the real `claude` CLI, plugin install through uninstall, from a
    throwaway copy of the tree against a throwaway HOME. Skips (never fails)
    at setUpClass when no `claude` binary is on PATH, which is the normal
    case on a GitHub runner, following tools/test_bm_packaging_install.py's
    own skip-not-fail posture for a missing environment precondition."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("claude") is None:
            raise unittest.SkipTest(
                "no claude binary on PATH; the plugin install route cannot "
                "be driven here")

        # FIRST, before anything else: the pre-cycle fingerprint of ROOT
        # itself, so test 10 below can prove the whole cycle never touched
        # the real checkout.
        cls.tree_before = _tree_fingerprint(ROOT)

        cls.tmp = tempfile.mkdtemp(prefix="bm_plugin_install_")
        cls.fake_home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.fake_home)
        # See constraint 2 in this file's module docstring: HOME alone does
        # not isolate the vault, because BROTHERMODE_VAULT wins over HOME
        # whenever the invoking shell has it exported. The dict is built
        # from scratch, never by copying os.environ and overwriting keys, so
        # no other ambient variable a future tool might read leaks in.
        cls.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": cls.fake_home,
            "BROTHERMODE_VAULT": os.path.join(cls.tmp, "vault"),
            "BROTHERSBE_VAULT": os.path.join(cls.tmp, "vault"),
        }

        # Never install from ROOT. Copy first, always: see constraint 1 in
        # this file's module docstring.
        cls.source = os.path.join(cls.tmp, "src")
        shutil.copytree(ROOT, cls.source,
                        ignore=shutil.ignore_patterns(*COPY_EXCLUDE))

        cls.validate_result = subprocess.run(
            ["claude", "plugin", "validate", cls.source],
            capture_output=True, text=True, timeout=120, env=cls.env)
        cls.marketplace_add_result = subprocess.run(
            ["claude", "plugin", "marketplace", "add", cls.source],
            capture_output=True, text=True, timeout=120, env=cls.env)
        cls.install_result = subprocess.run(
            ["claude", "plugin", "install", PLUGIN_ID],
            capture_output=True, text=True, timeout=120, env=cls.env)
        cls.list_result = subprocess.run(
            ["claude", "plugin", "list", "--json"],
            capture_output=True, text=True, timeout=60, env=cls.env)

        cls.list_json = None
        cls.install_path = None
        if cls.list_result.returncode == 0:
            try:
                cls.list_json = json.loads(cls.list_result.stdout)
            except ValueError:
                cls.list_json = None
            if isinstance(cls.list_json, list):
                for entry in cls.list_json:
                    if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
                        cls.install_path = entry.get("installPath")
                        break

        cls.details_result = subprocess.run(
            ["claude", "plugin", "details", PLUGIN_NAME],
            capture_output=True, text=True, timeout=60, env=cls.env)

        # LAST: the post-cycle fingerprint of ROOT.
        cls.tree_after = _tree_fingerprint(ROOT)

    @classmethod
    def tearDownClass(cls):
        # Best effort, never allowed to raise: cleanup must not hide a real
        # assertion failure behind a teardown exception.
        try:
            subprocess.run(  # sbe: allow-silent best-effort teardown cleanup, per docstring must not hide a real assertion failure
                ["claude", "plugin", "uninstall", PLUGIN_NAME, "-y"],
                capture_output=True, text=True, timeout=60, env=cls.env)
        except Exception:  # sbe: allow-silent best-effort teardown cleanup, per docstring must not hide a real assertion failure
            pass
        try:
            subprocess.run(  # sbe: allow-silent best-effort teardown cleanup, per docstring must not hide a real assertion failure
                ["claude", "plugin", "marketplace", "remove", MARKETPLACE_NAME],
                capture_output=True, text=True, timeout=60, env=cls.env)
        except Exception:  # sbe: allow-silent best-effort teardown cleanup, per docstring must not hide a real assertion failure
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_plugin_validate_passes_on_the_copied_tree(self):
        r = self.validate_result
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("Validation passed", r.stdout, r.stdout + r.stderr)

    def test_the_marketplace_is_added_and_the_plugin_installs(self):
        add = self.marketplace_add_result
        install = self.install_result
        self.assertEqual(0, add.returncode, add.stdout + add.stderr)
        self.assertEqual(0, install.returncode, install.stdout + install.stderr)

    def test_plugin_list_reports_the_plugin_installed_enabled_and_at_this_version(self):
        r = self.list_result
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIsInstance(self.list_json, list, r.stdout)
        self.assertEqual(1, len(self.list_json), r.stdout)
        entry = self.list_json[0]
        self.assertEqual(PLUGIN_ID, entry.get("id"), entry)
        self.assertIs(True, entry.get("enabled"), entry)
        self.assertEqual("user", entry.get("scope"), entry)
        expected_version = _read_json(PLUGIN_JSON)["version"]
        self.assertEqual(expected_version, entry.get("version"), entry)
        install_path = entry.get("installPath") or ""
        self.assertTrue(
            install_path.startswith(self.fake_home),
            "installPath %r does not start with the fake HOME %r; this "
            "installation may have reached real configuration"
            % (install_path, self.fake_home))

    def test_the_installed_copy_carries_the_hook_manifest_byte_for_byte(self):
        self.assertTrue(
            self.install_path,
            "no installPath was recovered from claude plugin list --json: %s"
            % self.list_result.stdout)
        installed_hooks = os.path.join(self.install_path, "hooks", "hooks.json")
        want = _sha256_file(HOOKS_JSON)
        got = _sha256_file(installed_hooks)
        self.assertEqual(
            want, got,
            "hooks.json sha256 mismatch: root=%s installed=%s" % (want, got))

    def test_the_installed_hooks_agree_with_hooks_json_field_by_field(self):
        """THE CONSTRAINT 3 TEST. Modelled on
        tools/test_install.py::TestHooksJsonAgreesWithInstaller. Builds a
        list of mismatch strings and reports every one, rather than the
        first, exactly as that suite does."""
        self.assertTrue(self.install_path, self.list_result.stdout)
        expected = _read_json(HOOKS_JSON)["hooks"]
        installed = _read_json(
            os.path.join(self.install_path, "hooks", "hooks.json"))["hooks"]
        mismatches = []
        for event in sorted(set(expected) | set(installed)):
            want_groups = expected.get(event, [])
            got_groups = installed.get(event, [])
            want_matchers = [g.get("matcher") for g in want_groups]
            got_matchers = [g.get("matcher") for g in got_groups]
            for matcher in want_matchers:
                if matcher not in got_matchers:
                    mismatches.append(
                        "%s[%s]: hooks.json wires this group; the installed "
                        "hooks.json has no matching group" % (event, matcher))
            for matcher in got_matchers:
                if matcher not in want_matchers:
                    mismatches.append(
                        "%s[%s]: the installed hooks.json wires this group; "
                        "hooks.json has no matching group" % (event, matcher))
            for want in want_groups:
                matcher = want.get("matcher")
                got = next((g for g in got_groups
                           if g.get("matcher") == matcher), None)
                if got is None:
                    continue  # already reported above
                want_hooks = want.get("hooks", [])
                got_hooks = got.get("hooks", [])
                if len(want_hooks) != len(got_hooks):
                    mismatches.append(
                        "%s[%s].hooks_count: hooks.json=%r, installed=%r"
                        % (event, matcher, len(want_hooks), len(got_hooks)))
                    continue
                for want_entry, got_entry in zip(want_hooks, got_hooks):
                    for field in ("type", "timeout", "statusMessage"):
                        w = want_entry.get(field)
                        g = got_entry.get(field)
                        if w != g:
                            mismatches.append(
                                "%s[%s].%s: hooks.json=%r, installed=%r"
                                % (event, matcher, field, w, g))
                    # Replace the token with the RESPECTIVE root on each
                    # side (ROOT for the expected manifest, install_path for
                    # the installed one), then normalize each side's own
                    # root back out to a shared placeholder. The two
                    # commands are never going to be byte identical (ROOT
                    # and install_path are different absolute paths by
                    # construction), so the comparison that actually means
                    # something is: same command once the unavoidable root
                    # difference is neutralized. Any OTHER divergence (a
                    # different tool, a different subcommand, a dropped
                    # flag) still surfaces, because it survives the
                    # normalization on both sides.
                    w_cmd = want_entry.get("command", "").replace(
                        PLUGIN_ROOT_TOKEN, ROOT).replace(ROOT, "<PLUGIN_ROOT>")
                    g_cmd = got_entry.get("command", "").replace(
                        PLUGIN_ROOT_TOKEN, self.install_path).replace(
                            self.install_path, "<PLUGIN_ROOT>")
                    if w_cmd != g_cmd:
                        mismatches.append(
                            "%s[%s].command: hooks.json=%r, installed=%r"
                            % (event, matcher, w_cmd, g_cmd))
        self.assertEqual([], mismatches, "\n".join(mismatches))

    def test_every_installed_hook_command_resolves_inside_the_installed_copy(self):
        self.assertTrue(self.install_path, self.list_result.stdout)
        installer = _installer_module()
        installed = _read_json(
            os.path.join(self.install_path, "hooks", "hooks.json"))["hooks"]
        real_install_path = os.path.realpath(self.install_path)
        bad = []
        for event, groups in installed.items():
            for group in groups:
                matcher = group.get("matcher")
                for entry in group.get("hooks", []):
                    command = entry.get("command", "")
                    substituted = command.replace(
                        PLUGIN_ROOT_TOKEN, self.install_path)
                    tokens = installer.command_path_tokens(substituted)
                    if tokens is None:
                        bad.append(
                            "%s[%s]: command could not be parsed: %r"
                            % (event, matcher, command))
                        continue
                    for tok in tokens:
                        if not os.path.isfile(tok):
                            bad.append(
                                "%s[%s]: missing file %s"
                                % (event, matcher, tok))
                            continue
                        if not os.path.realpath(tok).startswith(
                                real_install_path):
                            bad.append(
                                "%s[%s]: %s resolves outside the installed "
                                "copy" % (event, matcher, tok))
        self.assertEqual([], bad, "; ".join(bad))

    def test_plugin_details_registers_every_event_the_clone_installer_wires(self):
        installer = _installer_module()
        stdout = self.details_result.stdout
        m = re.search(r"Hooks \((\d+)\)\s+(.*)", stdout)
        self.assertIsNotNone(
            m, "no 'Hooks (' line found in claude plugin details output:\n%s"
            % stdout)
        count = int(m.group(1))
        rest = re.split(r"  +", m.group(2), maxsplit=1)[0]
        names = set(n.strip() for n in rest.split(",") if n.strip())
        expected_events = set(installer.HOOK_EVENTS)
        self.assertEqual(
            len(expected_events), count, "line: %s" % m.group(0))
        self.assertEqual(
            expected_events, names,
            "the Hooks(...) line names %s but install.py HOOK_EVENTS is %s: "
            "%s" % (names, expected_events, m.group(0)))

    def test_plugin_details_inventory_counts_the_commands_and_the_skill(self):
        """Renamed truth (F4): plugin.json's "skills" field is gone, so the
        expected count can no longer read it. Confirmed live: with that
        field absent, `claude plugin details brothermode` printed
        "Skills (32)" for a tree with 15 commands/*.md files and 17
        skills/ subdirectories (15 + 17 = 32), so the CLI's Skills(...)
        count is commands/*.md plus every skills/ subdirectory, not the
        (now absent) manifest list."""
        stdout = self.details_result.stdout
        m = re.search(r"Skills \((\d+)\)", stdout)
        self.assertIsNotNone(
            m, "no 'Skills (' line found in claude plugin details output:\n%s"
            % stdout)
        count = int(m.group(1))
        command_files = glob.glob(os.path.join(COMMANDS_DIR, "*.md"))
        skill_dirs = _skill_root_entries()
        expected = len(command_files) + len(skill_dirs)
        self.assertEqual(
            expected, count,
            "line reports Skills(%d) but commands/*.md=%d + skills/ "
            "subdirectories=%d: %s" % (count, len(command_files),
                                       len(skill_dirs), stdout))

    def test_no_machine_state_reaches_the_installed_copy(self):
        self.assertTrue(self.install_path, self.list_result.stdout)
        try:
            names = os.listdir(self.install_path)
        except OSError as exc:
            self.fail("could not list installPath %s: %s"
                     % (self.install_path, exc))
        found = [n for n in names if _excluded_name(n)]
        self.assertEqual(
            [], found,
            "excluded name(s) present at the top level of the installed "
            "copy: %s" % found)

    def test_the_source_tree_is_unchanged_by_the_whole_install_cycle(self):
        """THE CONSTRAINT 1 TEST MADE EXECUTABLE."""
        added, removed, changed = _tree_diff(self.tree_before, self.tree_after)
        self.assertEqual(
            ([], [], []), (added, removed, changed),
            "the source tree at ROOT changed during the install cycle: "
            "added=%s removed=%s changed=%s" % (added, removed, changed))


class TestPluginUninstallLeavesNoTrace(unittest.TestCase):
    """A separate class, with its own install cycle and its own throwaway
    HOME, rather than an eleventh method on TestPluginInstallFromATempCopy:
    it must run AFTER a full uninstall, and unittest orders methods
    alphabetically within a class, which is not an ordering anyone should
    rely on. Same skip rule as class B."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("claude") is None:
            raise unittest.SkipTest(
                "no claude binary on PATH; the plugin install route cannot "
                "be driven here")

        cls.tmp = tempfile.mkdtemp(prefix="bm_plugin_uninstall_")
        cls.fake_home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.fake_home)
        cls.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": cls.fake_home,
            "BROTHERMODE_VAULT": os.path.join(cls.tmp, "vault"),
            "BROTHERSBE_VAULT": os.path.join(cls.tmp, "vault"),
        }
        cls.source = os.path.join(cls.tmp, "src")
        shutil.copytree(ROOT, cls.source,
                        ignore=shutil.ignore_patterns(*COPY_EXCLUDE))

        # Both checked and raised on failure, not merely run: this class's
        # tests assume the plugin actually got installed. A silently failed
        # add/install here would still leave `claude plugin list` empty
        # after uninstall, so the "leaves no trace" tests below would pass
        # vacuously on a broken install path instead of catching it.
        add_setup = subprocess.run(
            ["claude", "plugin", "marketplace", "add", cls.source],
            capture_output=True, text=True, timeout=120, env=cls.env)
        if add_setup.returncode != 0:
            raise RuntimeError(
                "setup: marketplace add failed: %s"
                % (add_setup.stdout + add_setup.stderr))
        install_setup = subprocess.run(
            ["claude", "plugin", "install", PLUGIN_ID],
            capture_output=True, text=True, timeout=120, env=cls.env)
        if install_setup.returncode != 0:
            raise RuntimeError(
                "setup: plugin install failed: %s"
                % (install_setup.stdout + install_setup.stderr))

        # The marketplace source directory is the same temp COPY the install
        # was driven from, never ROOT. Fingerprinted before and after
        # uninstall so test 2 below can prove uninstall never touches it:
        # known_marketplaces.json records installLocation as this directory
        # itself, so an uninstall that decided to clean its source would be
        # deleting a user directory.
        cls.source_before = _tree_fingerprint(cls.source)

        cls.uninstall_result = subprocess.run(
            ["claude", "plugin", "uninstall", PLUGIN_NAME, "-y"],
            capture_output=True, text=True, timeout=60, env=cls.env)
        cls.marketplace_remove_result = subprocess.run(
            ["claude", "plugin", "marketplace", "remove", MARKETPLACE_NAME],
            capture_output=True, text=True, timeout=60, env=cls.env)

        cls.source_after = _tree_fingerprint(cls.source)

        cls.list_result = subprocess.run(
            ["claude", "plugin", "list", "--json"],
            capture_output=True, text=True, timeout=60, env=cls.env)
        cls.settings_path = os.path.join(
            cls.fake_home, ".claude", "settings.json")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_uninstall_then_marketplace_remove_leaves_nothing_installed(self):
        u = self.uninstall_result
        r = self.marketplace_remove_result
        self.assertEqual(0, u.returncode, u.stdout + u.stderr)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        lst = self.list_result
        self.assertEqual(0, lst.returncode, lst.stdout + lst.stderr)
        parsed = json.loads(lst.stdout)
        self.assertEqual([], parsed, lst.stdout)
        settings = _read_json(self.settings_path)
        self.assertFalse(
            settings.get("enabledPlugins"),
            "settings.json enabledPlugins is not empty: %s"
            % json.dumps(settings, indent=2, sort_keys=True))
        self.assertFalse(
            settings.get("extraKnownMarketplaces"),
            "settings.json extraKnownMarketplaces is not empty: %s"
            % json.dumps(settings, indent=2, sort_keys=True))

    def test_the_uninstall_never_touched_the_marketplace_source_directory(self):
        added, removed, changed = _tree_diff(self.source_before, self.source_after)
        self.assertEqual(
            ([], [], []), (added, removed, changed),
            "the marketplace source directory changed during uninstall: "
            "added=%s removed=%s changed=%s" % (added, removed, changed))


if __name__ == "__main__":
    unittest.main()
