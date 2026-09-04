#!/usr/bin/env python3
"""C7/B-10: `sbe doctor` never inspected hooks/hooks.json at all, so a tampered
or stripped hook (autosave and SessionStart silently not firing) was invisible
to the one command a tester runs to ask "is this environment sound". `doctor`
now carries a `hooks-wiring` check; this pins the shapes it must never blur.

`SBE_HOOKS_JSON` is the check's own override for "the installed copy to audit"
(see `_installed_hooks_json_path` in `src/brothersbe/cli.py`), which is what
every fixture here uses to hand the check a fabricated install without a real
marketplace install on this machine. Real subprocess, real `bin/sbe`, nothing
mocked, the same rule `TestDoctorProjectInitCheck` in `test_sbe.py` holds to
(that class is this file's closest sibling and its template)."""
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
ROOT = os.path.dirname(HERE)
SBE = os.path.join(ROOT, "bin", "sbe")
SHIPPED_HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")

#: The same citation shape the check itself resolves scripts with
#: (`_PLUGIN_ROOT_CITATION` in cli.py) and the shape
#: `test_every_hook_command_points_at_a_file_that_exists` in test_sbe.py
#: checks the shipped file against.
_CITATION = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)")


class TestDoctorHooksWiringCheck(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with io.open(SHIPPED_HOOKS_JSON, encoding="utf-8") as fh:
            self.shipped = json.load(fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture construction ------------------------------------------

    def _make_install(self, hooks_data, missing_scripts=()):
        """A fabricated install directory: `<dir>/hooks/hooks.json` holding
        `hooks_data`, plus an empty stub file under `<dir>/tools/` for every
        ${CLAUDE_PLUGIN_ROOT}-cited script `hooks_data` references, except
        those named in `missing_scripts`. Returns the hooks.json path.

        Mirrors a real install's own layout (hooks/ and tools/ siblings under
        one root) because the check resolves a cited script relative to that
        root, exactly as the runtime substitutes CLAUDE_PLUGIN_ROOT; a fixture
        holding only the JSON file would make every script-existence check
        fail for a reason that has nothing to do with the scenario under
        test."""
        install = tempfile.mkdtemp(dir=self.tmp)
        os.makedirs(os.path.join(install, "hooks"))
        with io.open(os.path.join(install, "hooks", "hooks.json"), "w",
                     encoding="utf-8") as fh:
            json.dump(hooks_data, fh)
        for blocks in hooks_data.get("hooks", {}).values():
            for block in blocks:
                for hook in block.get("hooks", []):
                    for cited in _CITATION.findall(hook.get("command", "")):
                        if cited in missing_scripts:
                            continue
                        stub = os.path.join(install, cited)
                        if not os.path.exists(os.path.dirname(stub)):
                            os.makedirs(os.path.dirname(stub))
                        io.open(stub, "w", encoding="utf-8").close()
        return os.path.join(install, "hooks", "hooks.json")

    def _doctor_json(self, hooks_json_path):
        env = dict(os.environ)
        env["SBE_HOOKS_JSON"] = hooks_json_path
        out = subprocess.run([sys.executable, SBE, "doctor", "--json"], cwd=ROOT,
                             capture_output=True, text=True, env=env)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            data = None
        return out.returncode, data, out.stdout + out.stderr

    @staticmethod
    def _check(data, name):
        for c in data["checks"]:
            if c["name"] == name:
                return c
        return None

    def _hooks_wiring(self, hooks_json_path):
        code, data, text = self._doctor_json(hooks_json_path)
        self.assertIsNotNone(data, "doctor --json did not parse: %s" % text)
        check = self._check(data, "hooks-wiring")
        self.assertIsNotNone(check, "doctor carries no hooks-wiring check: %s" % text)
        return code, check, text

    # -- calibration -----------------------------------------------------

    def test_unparseable_installed_copy_is_named(self):
        bad = os.path.join(self.tmp, "not-json.json")
        with io.open(bad, "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json")
        code, check, text = self._hooks_wiring(bad)
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn("does not parse", check["detail"], text)
        self.assertEqual(code, 1, text)

    def test_missing_installed_copy_is_named(self):
        gone = os.path.join(self.tmp, "no-such-dir", "hooks.json")
        code, check, text = self._hooks_wiring(gone)
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn("no file at", check["detail"], text)
        self.assertIn(gone, check["detail"], text)
        self.assertEqual(code, 1, text)

    def test_untampered_copy_passes(self):
        install = self._make_install(copy.deepcopy(self.shipped))
        code, check, text = self._hooks_wiring(install)
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn(install, check["detail"], text)

    def test_stripped_matcher_is_named(self):
        """The complaint's own acceptance test: delete one hook entry
        (the PreToolUse "Bash" matcher block) and confirm doctor NAMES it,
        rather than reporting the tampered install as clean."""
        data = copy.deepcopy(self.shipped)
        data["hooks"]["PreToolUse"] = [b for b in data["hooks"]["PreToolUse"]
                                       if b.get("matcher") != "Bash"]
        install = self._make_install(data)
        code, check, text = self._hooks_wiring(install)
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn("PreToolUse", check["detail"], text)
        self.assertIn("matcher 'Bash'", check["detail"], text)
        self.assertIn("shipped file declares", check["detail"], text)
        self.assertEqual(code, 1, text)

    def test_hollow_command_string_is_named(self):
        data = copy.deepcopy(self.shipped)
        data["hooks"]["Stop"][0]["hooks"][0]["command"] = "   "
        install = self._make_install(data)
        code, check, text = self._hooks_wiring(install)
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn("hollow command", check["detail"], text)
        self.assertIn("Stop", check["detail"], text)
        self.assertEqual(code, 1, text)

    def test_nonexistent_script_path_is_named(self):
        data = copy.deepcopy(self.shipped)
        data["hooks"]["SessionEnd"][0]["hooks"][0]["command"] = (
            'python3 "${CLAUDE_PLUGIN_ROOT}/tools/sbe_telemetry_missing.py" '
            'outcomes-append')
        install = self._make_install(data, missing_scripts=("tools/sbe_telemetry_missing.py",))
        code, check, text = self._hooks_wiring(install)
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn("does not exist", check["detail"], text)
        self.assertIn("tools/sbe_telemetry_missing.py", check["detail"], text)
        self.assertEqual(code, 1, text)

    def test_hooks_wiring_check_appears_in_a_real_doctor_run(self):
        """No SBE_HOOKS_JSON override: doctor examines the shipped file at
        minimum and never simply omits the check."""
        env = dict(os.environ)
        env.pop("SBE_HOOKS_JSON", None)
        out = subprocess.run([sys.executable, SBE, "doctor", "--json"], cwd=ROOT,
                             capture_output=True, text=True, env=env)
        data = json.loads(out.stdout)
        check = self._check(data, "hooks-wiring")
        self.assertIsNotNone(check, out.stdout + out.stderr)
        self.assertIn(check["result"], ("PASS", "FAIL", "NO-DATA"), out.stdout)

    # -- SBE_INSTALLED_PLUGINS_JSON: the book-replay fixture knob ---------
    #
    # docs/plan/FINDING-book-replay-version-coupling-2026-08-28.md (Brother
    # umbrella repo): the recorded book chapters show hooks-wiring reading
    # NO-DATA, "no installed copy is discoverable". That is only true on a
    # machine with no matching brothersbe install in its own
    # ~/.claude/plugins/installed_plugins.json cache. On a machine that DOES
    # have one (this project's own author's, routinely), the exact same
    # replay silently starts reading PASS instead, with no book change and
    # no commit involved. These two tests pin both branches to a fixture
    # instead of this machine's real install state, and prove the override
    # is read at all (not merely tolerated) by making it flip the verdict
    # both ways from the same real subprocess call.

    def _installed_plugins_json(self, plugins):
        path = os.path.join(self.tmp, "installed_plugins.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump({"plugins": plugins}, fh)
        return path

    def _doctor_json_with_installed_plugins_record(self, record_path):
        env = dict(os.environ)
        env.pop("SBE_HOOKS_JSON", None)
        env["SBE_INSTALLED_PLUGINS_JSON"] = record_path
        out = subprocess.run([sys.executable, SBE, "doctor", "--json"], cwd=ROOT,
                             capture_output=True, text=True, env=env)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            data = None
        self.assertIsNotNone(data, "doctor --json did not parse: %s" % (out.stdout + out.stderr))
        check = self._check(data, "hooks-wiring")
        self.assertIsNotNone(check, "doctor carries no hooks-wiring check: %s"
                             % (out.stdout + out.stderr))
        return check, out.stdout + out.stderr

    def test_installed_plugins_json_override_with_no_entries_reports_no_data(self):
        """A fixture record with zero brothersbe entries: NO-DATA, the exact
        book-recorded scenario, regardless of what this machine actually has
        installed via the marketplace."""
        record = self._installed_plugins_json({})
        check, text = self._doctor_json_with_installed_plugins_record(record)
        self.assertEqual(check["result"], "NO-DATA", text)
        self.assertIn("no installed copy is discoverable", check["detail"], text)
        self.assertIn(record, check["detail"], text)

    def test_installed_plugins_json_override_with_a_matching_entry_is_used(self):
        """A fixture record naming a real, untampered install at THIS
        checkout's own shipped version: PASS. Proves the override is
        actually consulted, not silently ignored, by flipping the verdict
        from the prior test using only the fixture, nothing else on this
        machine changed between the two calls."""
        with io.open(os.path.join(ROOT, ".claude-plugin", "plugin.json"),
                     encoding="utf-8") as fh:
            current_version = json.load(fh)["version"]
        install_hooks_json = self._make_install(copy.deepcopy(self.shipped))
        install_root = os.path.dirname(os.path.dirname(install_hooks_json))
        record = self._installed_plugins_json({
            "brothersbe@example": [{"version": current_version, "installPath": install_root}],
        })
        check, text = self._doctor_json_with_installed_plugins_record(record)
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn(install_hooks_json, check["detail"], text)

    def test_sbe_hooks_json_still_wins_over_installed_plugins_json(self):
        """Both overrides set: SBE_HOOKS_JSON is the more specific one (a
        single file, not a record to search) and must still win, so a
        caller already using it for something else is never silently
        redirected by this newer knob."""
        install = self._make_install(copy.deepcopy(self.shipped))
        env = dict(os.environ)
        env["SBE_HOOKS_JSON"] = install
        env["SBE_INSTALLED_PLUGINS_JSON"] = self._installed_plugins_json({})
        out = subprocess.run([sys.executable, SBE, "doctor", "--json"], cwd=ROOT,
                             capture_output=True, text=True, env=env)
        data = json.loads(out.stdout)
        check = self._check(data, "hooks-wiring")
        self.assertEqual(check["result"], "PASS", out.stdout + out.stderr)
        self.assertIn(install, check["detail"], out.stdout)


class TestHooksWiringUnderACodexClient(unittest.TestCase):
    """C3: the same gate, run under a simulated Codex client.

    WHY THIS EXISTS. Codex 0.153.0-alpha.5 has hooks (`codex features list`
    prints "hooks stable true") but plugin-delivered hooks were REMOVED from it
    ("plugin_hooks removed false" in the same listing), and the canonical
    plugin validator refuses a manifest that declares them. So an installed
    Brother Codex plugin ships no hooks.json at all, and the shipped-versus-
    installed comparison this check runs has nothing to compare. A gate that
    kept printing PASS there would be reporting on a Claude install while a
    reader took it as a statement about the environment in front of them.

    DRIVEN BOTH WAYS on purpose, because a guard that fires unconditionally
    would satisfy a one-sided test: the Codex run must read NO-DATA and the
    Claude run over the SAME fixture must still read PASS. The client is
    simulated with BROTHER_CLIENT, brother_paths' own explicit override, so
    neither run needs Codex or Claude installed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with io.open(SHIPPED_HOOKS_JSON, encoding="utf-8") as fh:
            self.shipped = json.load(fh)
        maker = TestDoctorHooksWiringCheck("test_unparseable_installed_copy_is_named")
        maker.tmp = self.tmp
        self.install = maker._make_install(copy.deepcopy(self.shipped))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wiring_under(self, client, codex_home=None):
        env = dict(os.environ)
        env["SBE_HOOKS_JSON"] = self.install
        env["BROTHER_CLIENT"] = client
        # brother_paths resolves a non-Claude client's config directory from
        # CODEX_HOME, so pinning it to a directory this test owns is what
        # keeps the Codex runs from reading whatever the machine's real
        # ~/.codex happens to hold today.
        env["CODEX_HOME"] = codex_home or os.path.join(self.tmp, "empty-codex-home")
        env.pop("BROTHER_CONFIG_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        out = subprocess.run([sys.executable, SBE, "doctor", "--json"], cwd=ROOT,
                             capture_output=True, text=True, env=env)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            self.fail("doctor --json did not parse under BROTHER_CLIENT=%s: %s"
                      % (client, out.stdout + out.stderr))
        for check in data["checks"]:
            if check["name"] == "hooks-wiring":
                return check, out.stdout + out.stderr
        self.fail("doctor carries no hooks-wiring check: %s" % out.stdout)

    def test_codex_client_with_nothing_wired_reads_no_data_never_a_pass(self):
        check, text = self._wiring_under("codex")
        self.assertEqual(check["result"], "NO-DATA", text)
        self.assertIn("plugin_hooks removed", check["detail"], text)
        self.assertIn("HOOKS-MAPPING.md", check["detail"], text)

    def test_the_same_fixture_still_passes_under_claude(self):
        """The positive control. Without this, a guard that returned NO-DATA
        for every client would pass the test above."""
        check, text = self._wiring_under("claude")
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn(self.install, check["detail"], text)


class TestHooksWiringUnderACodexClientThatWiredItsOwnHooks(
        TestHooksWiringUnderACodexClient):
    """C3, the other half: Codex has no plugin hook delivery, but it DOES read
    a user-scope hooks file, and `scripts/codex_hooks_install.py` writes one.
    Once it is wired the gate has a real subject again, so NO-DATA would then
    be under-reporting exactly as a PASS was over-reporting before.

    DRIVEN BOTH WAYS, like its parent: a wired file reads PASS, the SAME file
    with one command removed reads FAIL and names it. Inheriting the parent
    class keeps its two runs (nothing wired reads NO-DATA, and Claude over
    the same fixture still reads PASS) executing beside these, which is what
    stops a guard that simply always PASSes under Codex from satisfying this
    file."""

    def _codex_home_wiring(self, drop=None):
        """A Codex home holding the hooks file `codex_hooks_install.py` would
        write for this fixture: the shipped events, `${CLAUDE_PLUGIN_ROOT}`
        expanded to the install root, `timeout` renamed `timeoutSec`. When
        `drop` is given, the command whose text contains it is left out, which
        is the tampering the FAIL run must catch.

        Translated here rather than imported from scripts/ on purpose: a
        product's own tests must not depend on the umbrella checkout, and a
        second independent implementation of the translation is a check on
        the first."""
        install_root = os.path.dirname(os.path.dirname(self.install))
        hooks = {}
        for event, blocks in self.shipped.get("hooks", {}).items():
            translated = []
            for block in blocks:
                entries = []
                for hook in block.get("hooks", []):
                    command = hook["command"].replace(
                        "${CLAUDE_PLUGIN_ROOT}", install_root)
                    if drop and drop in command:
                        continue
                    entry = {"type": "command", "command": command,
                             "async": False}
                    if hook.get("timeout") is not None:
                        entry["timeoutSec"] = hook["timeout"]
                    entries.append(entry)
                if not entries:
                    continue
                translated_block = {"hooks": entries}
                if block.get("matcher"):
                    translated_block["matcher"] = block["matcher"]
                translated.append(translated_block)
            if translated:
                hooks[event] = translated
        home = tempfile.mkdtemp(dir=self.tmp)
        with io.open(os.path.join(home, "hooks.json"), "w",
                     encoding="utf-8") as handle:
            handle.write(json.dumps({"description": "fixture", "hooks": hooks}))
        return home

    def test_a_wired_codex_hooks_file_reads_pass(self):
        check, text = self._wiring_under("codex", self._codex_home_wiring())
        self.assertEqual(check["result"], "PASS", text)
        self.assertIn("hooks.json", check["detail"], text)

    def test_a_codex_hooks_file_missing_one_command_reads_fail(self):
        home = self._codex_home_wiring(drop="sbe_fence_hook.py")
        check, text = self._wiring_under("codex", home)
        self.assertEqual(check["result"], "FAIL", text)
        self.assertIn("sbe_fence_hook.py", check["detail"], text)


if __name__ == "__main__":
    unittest.main()
