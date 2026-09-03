#!/usr/bin/env python3
"""tools/test_bm_e2e_pins.py: pins for scripts/bench_e2e_lifecycle.py (FENCE
V10, BrotherModeUp-handovers/v3/WAVE-CD-BRIEFS.md, agent e2e-canary).

WHAT THIS IS
  A deterministic, `claude`-binary-free pin suite: it imports
  scripts/bench_e2e_lifecycle.py BY PATH and asserts its STEP_NAMES,
  NINE_CANONICAL_SKILLS, DELIVER_REFUSAL_MARKER and V2_TAG stay exactly
  what the live canary needs them to be, without ever running the live
  canary itself (which needs a real `claude` binary, a real `git clone`,
  and several seconds of real subprocess work). This is the fast,
  always-green half of the pair; `python3 scripts/bench_e2e_lifecycle.py`
  is the slow, real half. This file is registered wherever the ORCHESTRATOR
  wires suite discovery; the live canary stays unregistered on purpose (a
  machine with no `claude` binary must not turn a suite red for a reason
  outside its control), matching how tools/test_all.py never runs
  scripts/benchmark_comparative.py's own probe_installed directly either.

WHAT EACH PIN PROTECTS AGAINST
  - STEP_NAMES: a step added, removed, reordered or renamed in the live
    canary without a matching, deliberate edit here. The count is pinned
    (eighteen) and so is the exact wording of the negative-path step and
    the migration-instruction step, the two steps the FENCE V10 brief
    names by name. A regression test also pins the exact ordering bug
    this suite's own construction hit once for real (a task seeded
    before the project row it references existed, an sqlite3
    IntegrityError): 'brothermode_cli start' must precede 'ready-state
    task seeded', which must precede 'brothermode_cli status'.
  - NINE_CANONICAL_SKILLS: drift from the actual public skill surface.
    Cross-checked against the real skills/ directory tree minus
    V3-FREEZE-2026-08-07.md ruling B5's own hidden-skill list, and
    against docs/brand/IDENTITY-CONTRACT.md's own canonical-skill line,
    never retyped as a third, independent guess.
  - DELIVER_REFUSAL_MARKER: drift from tools/bm_project.py's own refusal
    wording. Cross-checked by grepping that file's actual source for the
    marker, so a wording change there breaks THIS test before it can
    silently break the live canary's own assertion.
  - V2_TAG / V2_PLUGIN_NAME / V2_EXPECTED_VERSION: the upgrade leg's
    pinned historical release must still exist as a real, resolvable git
    tag in this repository, and that tag's own plugin.json must still
    name the plugin identity the live canary expects to install.
  - no live `claude -p` session: the FENCE V10 brief is explicit that
    this canary "drives the CLI boundary and the plugin CLI, staying
    deterministic" and that live claude -p turns are not required beyond
    what scripts/benchmark_comparative.py's probe_installed already
    proves. This test greps the live canary's own source and fails if a
    headless-prompt invocation of the claude binary (-p / --print) is
    ever added to it.

Python 3.9, standard library only. No `claude` binary anywhere in this
file. `git` is used read-only (rev-parse, show) and every test that needs
it skips, never fails, when no `git` binary is on PATH, matching this
project's own posture for an absent optional precondition.

No em or en dashes anywhere in this file or its output.
"""

import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIFECYCLE_PY = os.path.join(ROOT, "scripts", "bench_e2e_lifecycle.py")
BM_PROJECT_PY = os.path.join(ROOT, "tools", "bm_project.py")
IDENTITY_CONTRACT = os.path.join(ROOT, "docs", "brand", "IDENTITY-CONTRACT.md")

EXPECTED_STEP_COUNT = 18

_LIFECYCLE_MODULE = None


def _lifecycle_module():
    """scripts/bench_e2e_lifecycle.py, imported once by PATH and cached:
    the same importlib.util.spec_from_file_location technique
    tools/test_bm_plugin_install.py already uses for scripts/install.py,
    reused here so this suite reads the SAME constants the live canary
    itself defines, never a second, independently retyped copy of them."""
    global _LIFECYCLE_MODULE
    if _LIFECYCLE_MODULE is None:
        spec = importlib.util.spec_from_file_location(
            "bm_e2e_pins_lifecycle", LIFECYCLE_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LIFECYCLE_MODULE = mod
    return _LIFECYCLE_MODULE


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


_ADJACENT_STRING_LITERALS = re.compile(r'"\s*\n\s*"')


def _concat_adjacent_string_literals(text):
    """A crude but sufficient approximation of Python's own adjacent
    string-literal concatenation, applied to raw SOURCE TEXT rather than a
    parsed AST: collapses a closing double-quote, the whitespace (newline
    plus indentation) between two adjacent string literals, and the
    following opening double-quote, into nothing. tools/bm_project.py
    spells its deliver refusal as two literals across two lines (each one
    independently readable, but only continuous once Python concatenates
    them at compile time); this lets a plain substring search find the
    marker the way the RUNTIME string actually reads, without executing
    or evaluating any of that file's code."""
    return _ADJACENT_STRING_LITERALS.sub("", text)


def _skill_root_entries():
    skills_root = os.path.join(ROOT, "skills")
    return sorted(d for d in os.listdir(skills_root)
                 if os.path.isdir(os.path.join(skills_root, d)))


class TestTheLiveCanaryFileExists(unittest.TestCase):
    """Runs unconditionally: no `claude` binary, no network, no install.
    Also the guarantee that this suite always runs at least a handful of
    real tests even on a machine with nothing else installed, the same
    posture tools/test_bm_plugin_install.py's own class A documents."""

    def test_the_file_exists_and_imports_cleanly(self):
        self.assertTrue(os.path.isfile(LIFECYCLE_PY), LIFECYCLE_PY)
        mod = _lifecycle_module()
        for attr in ("STEP_NAMES", "TOTAL_STEPS", "NINE_CANONICAL_SKILLS",
                    "DELIVER_REFUSAL_MARKER", "V2_TAG", "V2_PLUGIN_NAME",
                    "V2_MARKETPLACE_NAME", "V2_PLUGIN_SPEC",
                    "V2_EXPECTED_VERSION", "run_lifecycle", "main",
                    "Fail", "Skip", "_installed_cli_bin",
                    "_installed_plugin_root"):
            self.assertTrue(hasattr(mod, attr),
                           "scripts/bench_e2e_lifecycle.py has no %s" % attr)

    def test_fail_is_a_distinct_exception_from_skip(self):
        """H-4 (v3 gap-closure Claude-family review): SKIP and FAIL must be
        two different words for two different facts, never the same
        exception wearing two names."""
        mod = _lifecycle_module()
        self.assertTrue(issubclass(mod.Fail, Exception))
        self.assertFalse(issubclass(mod.Fail, mod.Skip))
        self.assertFalse(issubclass(mod.Skip, mod.Fail))


class TestStepListIsPinned(unittest.TestCase):
    def test_step_count(self):
        mod = _lifecycle_module()
        self.assertEqual(EXPECTED_STEP_COUNT, len(mod.STEP_NAMES),
                         mod.STEP_NAMES)
        self.assertEqual(mod.TOTAL_STEPS, len(mod.STEP_NAMES))

    def test_step_names_are_unique(self):
        mod = _lifecycle_module()
        self.assertEqual(
            len(mod.STEP_NAMES), len(set(mod.STEP_NAMES)),
            "STEP_NAMES has a duplicate entry: %r" % (mod.STEP_NAMES,))

    def test_the_migration_instruction_step_is_named_exactly(self):
        """The FENCE V10 brief's own words: 'document the uninstall-v2-
        first instruction the canary encodes.' Pinned verbatim so a
        rewording here is a deliberate edit, not an accident."""
        mod = _lifecycle_module()
        self.assertIn(
            "v2 uninstalled first (documented migration instruction: "
            "uninstall v2, then install v3)", mod.STEP_NAMES)

    def test_the_negative_deliver_step_is_named_exactly(self):
        """The FENCE V10 brief's own words: 'prove incomplete evidence
        blocks deliver (negative path, expect the refusal).'"""
        mod = _lifecycle_module()
        self.assertIn(
            "brothermode_cli deliver refused (incomplete evidence blocks "
            "delivery)", mod.STEP_NAMES)

    def test_start_precedes_task_seeding_precedes_status(self):
        """Regression pin for the exact bug this file's own construction
        hit once for real: seeding a task before the project row it
        references existed raised sqlite3.IntegrityError (FOREIGN KEY
        constraint failed) out of tools/bm_store.py's create_task. 'start'
        must run before the task it seeds can reference a project, and
        the seeded task must exist before 'status'/'next' read it back."""
        mod = _lifecycle_module()
        names = mod.STEP_NAMES
        self.assertIn("brothermode_cli start", names)
        self.assertIn("ready-state task seeded", names)
        self.assertIn("brothermode_cli status", names)
        i_start = names.index("brothermode_cli start")
        i_seed = names.index("ready-state task seeded")
        i_status = names.index("brothermode_cli status")
        self.assertLess(i_start, i_seed,
                        "start must run before the task it seeds can "
                        "reference a project (regression: sqlite3 "
                        "IntegrityError, FOREIGN KEY constraint failed)")
        self.assertLess(i_seed, i_status,
                        "the task must be seeded before status/next read "
                        "it back")

    def test_uninstall_v2_step_precedes_v3_install_step(self):
        """Ruling B4 (V3-FREEZE-2026-08-07.md): uninstall v2 BEFORE
        installing v3, so the two plugins' hooks are never both wired."""
        mod = _lifecycle_module()
        names = mod.STEP_NAMES
        i_v2_un = next(i for i, n in enumerate(names)
                      if n.startswith("v2 uninstalled first"))
        i_v3_in = next(i for i, n in enumerate(names)
                      if n == "v3 installed the shipped way")
        self.assertLess(
            i_v2_un, i_v3_in,
            "ruling B4's migration instruction (uninstall v2, THEN "
            "install v3) must be encoded in that order")

    def test_single_hook_chain_step_follows_the_v3_install(self):
        mod = _lifecycle_module()
        names = mod.STEP_NAMES
        i_v3_in = names.index("v3 installed the shipped way")
        i_single = next(i for i, n in enumerate(names)
                       if n.startswith("single hook chain proven"))
        self.assertLess(i_v3_in, i_single)


class TestNineCanonicalSkillsIsPinned(unittest.TestCase):
    def test_nine_entries_no_more_no_less(self):
        mod = _lifecycle_module()
        self.assertEqual(9, len(mod.NINE_CANONICAL_SKILLS),
                         mod.NINE_CANONICAL_SKILLS)

    def test_matches_the_skills_directory_minus_the_hidden_set(self):
        """V3-FREEZE-2026-08-07.md ruling B5: the public surface is the
        nine canonical skills; the full-auto trio (auto, auto-status,
        stop), the founder-mode quartet (brief, decisions, handback,
        handover-pack), and brotherme itself are hidden internal skills,
        preserved on disk but off the public slash surface. This test
        derives the expected nine STRUCTURALLY from skills/ rather than
        retyping a second independent list, so a skill added or removed
        from disk without a matching edit here fails loudly."""
        hidden = {"auto", "auto-status", "stop",
                 "brief", "decisions", "handback", "handover-pack",
                 "brotherme",
                 # Cursor compatibility mode (2026-08-10): planner and
                 # executor skills for the Fable-to-Cursor harness. On
                 # disk for Claude Code and Cursor, off the nine-skill
                 # beginner slash surface.
                 "cursor-dispatch", "cursor-execute"}
        entries = set(_skill_root_entries())
        expected = tuple(sorted(entries - hidden))
        mod = _lifecycle_module()
        self.assertEqual(
            expected, tuple(sorted(mod.NINE_CANONICAL_SKILLS)),
            "skills/ minus the ruling B5 hidden set is %s, but "
            "NINE_CANONICAL_SKILLS is %s"
            % (expected, tuple(sorted(mod.NINE_CANONICAL_SKILLS))))

    def test_matches_the_identity_contract_doc(self):
        self.assertTrue(os.path.isfile(IDENTITY_CONTRACT), IDENTITY_CONTRACT)
        text = _read(IDENTITY_CONTRACT)
        self.assertIn("nine v3 canonical skills", text,
                      "docs/brand/IDENTITY-CONTRACT.md no longer names "
                      "'nine v3 canonical skills'; section 8 may have "
                      "been reworded")
        mod = _lifecycle_module()
        missing = [name for name in mod.NINE_CANONICAL_SKILLS
                  if (":%s" % name) not in text]
        self.assertEqual(
            [], missing,
            "docs/brand/IDENTITY-CONTRACT.md's own canonical-skill line "
            "is missing %s (or the doc's ':name' wording changed)"
            % missing)


class TestDeliverRefusalMarkerIsPinned(unittest.TestCase):
    def test_marker_is_a_substring_of_bm_projects_own_refusal_text(self):
        self.assertTrue(os.path.isfile(BM_PROJECT_PY), BM_PROJECT_PY)
        text = _concat_adjacent_string_literals(_read(BM_PROJECT_PY))
        mod = _lifecycle_module()
        self.assertIn(
            mod.DELIVER_REFUSAL_MARKER, text,
            "tools/bm_project.py no longer contains the exact refusal "
            "substring %r the live canary keys its negative-path "
            "assertion on; cmd_deliver's non-terminal-task refusal text "
            "may have been reworded" % mod.DELIVER_REFUSAL_MARKER)


class TestUpgradeLegTagIsPinned(unittest.TestCase):
    """Read-only `git` calls only (rev-parse, show); skips, never fails,
    when no `git` binary is on PATH, matching scripts/benchmark_comparative
    .py's own _git helper's posture for the same absent precondition."""

    def _git(self, *args):
        if shutil.which("git") is None:
            raise unittest.SkipTest("no git binary on PATH")
        return subprocess.run(["git"] + list(args), cwd=ROOT,
                             capture_output=True, text=True, timeout=30)

    def test_v2_tag_exists_and_resolves(self):
        mod = _lifecycle_module()
        r = self._git("rev-parse", "--verify", "refs/tags/%s" % mod.V2_TAG)
        self.assertEqual(
            0, r.returncode,
            "git tag %r does not resolve in this repository: %s"
            % (mod.V2_TAG, r.stdout + r.stderr))

    def test_v2_tags_own_manifest_matches_the_pinned_identity(self):
        mod = _lifecycle_module()
        r = self._git("show",
                      "%s:.claude-plugin/plugin.json" % mod.V2_TAG)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        try:
            manifest = json.loads(r.stdout)
        except ValueError as exc:
            self.fail("git show %s:.claude-plugin/plugin.json is not "
                     "valid JSON: %s" % (mod.V2_TAG, exc))
        self.assertEqual(mod.V2_PLUGIN_NAME, manifest.get("name"), manifest)
        self.assertEqual(mod.V2_EXPECTED_VERSION, manifest.get("version"),
                         manifest)
        self.assertEqual(
            mod.V2_PLUGIN_SPEC,
            "%s@%s" % (mod.V2_PLUGIN_NAME, mod.V2_MARKETPLACE_NAME))


class TestNoLiveClaudePromptSession(unittest.TestCase):
    """The FENCE V10 brief's own words: 'Live claude -p turns are NOT
    required beyond what probe_installed already proves; the lifecycle
    canary drives the CLI boundary and the plugin CLI, staying
    deterministic.' This test pins that scope decision structurally: the
    live canary's own source must never invoke the claude binary with a
    headless-prompt flag (-p / --print)."""

    HEADLESS_PROMPT_FLAG = re.compile(r'["\']-p["\']|--print\b')

    def test_the_live_canary_never_invokes_claude_dash_p(self):
        text = _read(LIFECYCLE_PY)
        offenders = [ln.strip() for ln in text.splitlines()
                    if self.HEADLESS_PROMPT_FLAG.search(ln)]
        self.assertEqual(
            [], offenders,
            "scripts/bench_e2e_lifecycle.py invokes claude with a "
            "headless-prompt flag, which the FENCE V10 brief says this "
            "canary must not do (that proof already exists in "
            "scripts/benchmark_comparative.py's probe_installed): %s"
            % offenders)


class TestInstalledCliResolution(unittest.TestCase):
    """Codex finding 6 (v3 gap-closure cross-family review): the CLI
    boundary steps must drive the INSTALLED plugin's own
    tools/brothermode_cli.py, resolved from Claude Code's own
    plugins/installed_plugins.json (measured 2026-08-08 against a real
    throwaway `claude plugin install`), never the checkout's copy and
    never a guessed cache-directory path. Every test here builds its own
    synthetic CLAUDE_CONFIG_DIR under a temp directory; no `claude`
    binary is invoked anywhere in this class."""

    def setUp(self):
        self.mod = _lifecycle_module()
        self.tmp = tempfile.mkdtemp(prefix="bm-e2e-pins-cfgdir-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write_registry(self, spec, install_path):
        plugins_dir = os.path.join(self.tmp, "plugins")
        os.makedirs(plugins_dir, exist_ok=True)
        with io.open(os.path.join(plugins_dir, "installed_plugins.json"),
                     "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "plugins": {
                spec: [{"scope": "user", "installPath": install_path,
                       "version": "0.0.0-test"}]}}, fh)

    def test_resolves_the_installpath_the_real_registry_names(self):
        install_root = os.path.join(self.tmp, "installed-copy")
        os.makedirs(os.path.join(install_root, "tools"))
        cli = os.path.join(install_root, "tools", "brothermode_cli.py")
        with io.open(cli, "w", encoding="utf-8") as fh:
            fh.write("# stub\n")
        self._write_registry(self.mod.BC.PROBE_PLUGIN_SPEC, install_root)
        self.assertEqual(
            install_root,
            self.mod._installed_plugin_root(self.tmp, self.mod.BC.PROBE_PLUGIN_SPEC))
        self.assertEqual(cli, self.mod._installed_cli_bin(self.tmp))

    def test_missing_registry_file_resolves_to_none(self):
        self.assertIsNone(
            self.mod._installed_plugin_root(self.tmp, self.mod.BC.PROBE_PLUGIN_SPEC))
        self.assertIsNone(self.mod._installed_cli_bin(self.tmp))

    def test_registry_naming_a_different_spec_resolves_to_none(self):
        install_root = os.path.join(self.tmp, "installed-copy")
        os.makedirs(os.path.join(install_root, "tools"))
        io.open(os.path.join(install_root, "tools", "brothermode_cli.py"),
               "w").close()
        self._write_registry("brotherme@brotherme-marketplace", install_root)
        self.assertIsNone(
            self.mod._installed_plugin_root(self.tmp, self.mod.BC.PROBE_PLUGIN_SPEC))

    def test_installpath_naming_no_cli_file_resolves_cli_to_none(self):
        """The installed plugin's root exists (the registry entry is real)
        but carries no tools/brothermode_cli.py: the exact 'the installed
        plugin exposes no CLI entry' case codex finding 6 names."""
        install_root = os.path.join(self.tmp, "installed-copy-no-cli")
        os.makedirs(os.path.join(install_root, "tools"))
        self._write_registry(self.mod.BC.PROBE_PLUGIN_SPEC, install_root)
        self.assertEqual(
            install_root,
            self.mod._installed_plugin_root(self.tmp, self.mod.BC.PROBE_PLUGIN_SPEC))
        self.assertIsNone(self.mod._installed_cli_bin(self.tmp))

    def test_installpath_naming_a_directory_that_does_not_exist_is_skipped(self):
        self._write_registry(self.mod.BC.PROBE_PLUGIN_SPEC,
                             os.path.join(self.tmp, "does-not-exist"))
        self.assertIsNone(
            self.mod._installed_plugin_root(self.tmp, self.mod.BC.PROBE_PLUGIN_SPEC))

    def test_malformed_registry_json_resolves_to_none_not_a_crash(self):
        plugins_dir = os.path.join(self.tmp, "plugins")
        os.makedirs(plugins_dir)
        with io.open(os.path.join(plugins_dir, "installed_plugins.json"),
                     "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        self.assertIsNone(
            self.mod._installed_plugin_root(self.tmp, self.mod.BC.PROBE_PLUGIN_SPEC))


class TestCliRunAndDriversUseTheInstalledBin(unittest.TestCase):
    """Structural pins: _cli_run and every _drive_* function must take a
    cli_bin argument (so a caller cannot accidentally revert to a single,
    checkout-rooted default), and run_lifecycle's own source must resolve
    it through _installed_cli_bin, never through a bare ROOT-joined path."""

    def test_cli_run_requires_a_cli_bin_argument(self):
        mod = _lifecycle_module()
        params = list(inspect.signature(mod._cli_run).parameters)
        self.assertIn("cli_bin", params,
                     "_cli_run no longer takes a cli_bin argument; codex "
                     "finding 6's fix threads the installed plugin's own "
                     "CLI path through every caller explicitly")

    def test_every_drive_function_takes_cli_bin(self):
        mod = _lifecycle_module()
        for name in ("_drive_start", "_drive_status", "_drive_next",
                    "_drive_view", "_drive_deliver_refused", "_drive_doctor"):
            fn = getattr(mod, name)
            params = list(inspect.signature(fn).parameters)
            self.assertIn("cli_bin", params,
                         "%s no longer takes cli_bin" % name)

    def test_run_lifecycle_resolves_the_installed_bin(self):
        text = _read(LIFECYCLE_PY)
        self.assertIn("_installed_cli_bin(claude_config_dir)", text,
                     "run_lifecycle no longer resolves the CLI boundary "
                     "against the installed plugin's own path")


class TestFailSkipSplit(unittest.TestCase):
    """H-4 (v3 gap-closure Claude-family review): each of the three named
    product-safety assertions must raise Fail, not Skip, when it is
    actually CHECKED and found wrong; a mechanical problem that prevents
    the check from running at all must still raise Skip. Every test here
    monkeypatches the module's own _run (never a live claude/subprocess
    call) so this stays deterministic and claude-binary-free."""

    def setUp(self):
        self.mod = _lifecycle_module()

    class _FakeProc(object):
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def test_verify_single_plugin_fails_on_two_plugins_registered(self):
        two = json.dumps([{"id": self.mod.BC.PROBE_PLUGIN_SPEC},
                          {"id": "brotherme@brotherme-marketplace"}])
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(0, two)):
            with self.assertRaises(self.mod.Fail):
                self.mod._verify_single_plugin("claude", {})

    def test_verify_single_plugin_skips_on_unparseable_output(self):
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(0, "not json")):
            with self.assertRaises(self.mod.Skip):
                self.mod._verify_single_plugin("claude", {})

    def test_verify_single_plugin_passes_on_exactly_one(self):
        one = json.dumps([{"id": self.mod.BC.PROBE_PLUGIN_SPEC}])
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(0, one)):
            self.mod._verify_single_plugin("claude", {})  # must not raise

    def test_uninstall_verify_fails_on_a_remaining_plugin(self):
        with mock.patch.object(
                self.mod, "_run",
                side_effect=[self._FakeProc(0, ""), self._FakeProc(0, ""),
                            self._FakeProc(0, json.dumps(
                                [{"id": self.mod.BC.PROBE_PLUGIN_SPEC}]))]):
            with self.assertRaises(self.mod.Fail):
                self.mod._uninstall_v3_and_verify_clean(
                    "claude", {}, os.path.join(tempfile.gettempdir(),
                                               "bm-e2e-pins-nonexistent"))

    def test_uninstall_verify_fails_on_a_settings_json_remnant(self):
        tmp = tempfile.mkdtemp(prefix="bm-e2e-pins-cfgdir2-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with io.open(os.path.join(tmp, "settings.json"), "w",
                    encoding="utf-8") as fh:
            json.dump({"enabledPlugins": {"brothermode": True}}, fh)
        with mock.patch.object(
                self.mod, "_run",
                side_effect=[self._FakeProc(0, ""), self._FakeProc(0, ""),
                            self._FakeProc(0, "[]")]):
            with self.assertRaises(self.mod.Fail):
                self.mod._uninstall_v3_and_verify_clean("claude", {}, tmp)

    def test_uninstall_verify_skips_on_a_mechanical_uninstall_failure(self):
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(1, "", "boom")):
            with self.assertRaises(self.mod.Skip):
                self.mod._uninstall_v3_and_verify_clean(
                    "claude", {}, os.path.join(tempfile.gettempdir(),
                                               "bm-e2e-pins-nonexistent2"))

    def test_deliver_refused_fails_when_deliver_accepts_instead(self):
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(0, "delivered")):
            with self.assertRaises(self.mod.Fail):
                self.mod._drive_deliver_refused({}, ".", "cli.py")

    def test_deliver_refused_fails_when_the_refusal_wording_is_missing(self):
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(1, "", "some "
                                                              "other error")):
            with self.assertRaises(self.mod.Fail):
                self.mod._drive_deliver_refused({}, ".", "cli.py")

    def test_deliver_refused_passes_on_the_real_refusal(self):
        out = "deliver refused: tasks have not reached the terminal state"
        with mock.patch.object(self.mod, "_run",
                               return_value=self._FakeProc(1, out)):
            self.mod._drive_deliver_refused({}, ".", "cli.py")  # no raise


class TestMainExitCodes(unittest.TestCase):
    """main()'s own exception-to-exit-code mapping, exercised for real by
    monkeypatching run_lifecycle (never a live canary run): Fail must exit
    2, Skip must exit 1, distinctly, per H-4's acceptance condition."""

    def setUp(self):
        self.mod = _lifecycle_module()

    def _run_main_captured(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.mod.main([])
        return code, out.getvalue()

    def test_fail_exits_2(self):
        with mock.patch.object(self.mod, "run_lifecycle",
                               side_effect=self.mod.Fail("a real regression")):
            code, printed = self._run_main_captured()
        self.assertEqual(2, code)
        self.assertIn("FAIL: a real regression", printed)

    def test_skip_exits_1(self):
        with mock.patch.object(self.mod, "run_lifecycle",
                               side_effect=self.mod.Skip("no claude binary")):
            code, printed = self._run_main_captured()
        self.assertEqual(1, code)
        self.assertIn("SKIP: no claude binary", printed)

    def test_pass_exits_0(self):
        with mock.patch.object(self.mod, "run_lifecycle",
                               return_value=[("step", "detail")]):
            code, printed = self._run_main_captured()
        self.assertEqual(0, code)
        self.assertIn("PASSED", printed)


class TestVerdictLineHonesty(unittest.TestCase):
    """v3 gap-closure G2 (verdict lines built from constants rather than
    parsed measurements): step 7's own printed count must come from what
    _verify_v3_identity actually found in `claude plugin details`'s own
    Skills(...) line, not from NINE_CANONICAL_SKILLS compared against
    itself (which would read N/N regardless of what was measured)."""

    def test_verify_v3_identity_returns_a_measured_found_count(self):
        mod = _lifecycle_module()
        sig = inspect.signature(mod._verify_v3_identity)
        self.assertEqual(["claude_bin", "env"], list(sig.parameters))

    def test_run_lifecycle_does_not_print_the_same_constant_twice(self):
        """Regression pin for the exact bug this fixes: the old call site
        read '(version, len(NINE_CANONICAL_SKILLS),
        len(NINE_CANONICAL_SKILLS))', printing the identical constant as
        both the numerator and the denominator regardless of measurement."""
        text = _read(LIFECYCLE_PY)
        offender = ("len(NINE_CANONICAL_SKILLS), len(NINE_CANONICAL_SKILLS)")
        self.assertNotIn(
            offender, text,
            "run_lifecycle prints the same NINE_CANONICAL_SKILLS constant "
            "as both the numerator and the denominator of the canonical "
            "skills count, which is not a measurement")


if __name__ == "__main__":
    unittest.main()
