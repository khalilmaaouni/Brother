#!/usr/bin/env python3
"""C3: what scripts/codex_hooks_install.py must never get wrong.

Every case here runs the real module against the real shipped hooks files.
Nothing that needs the Codex binary is asserted here: the binary is the
subject of the end-to-end evidence in docs/codex/HOOKS-MAPPING.md, and a unit
suite that shelled out to an app bundle would be NO-DATA on any machine
without it, which is worse than being narrow on purpose.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import codex_hooks_install as chi  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

ROOT = os.path.dirname(HERE)
BROTHERMODE = os.path.join(ROOT, "products", "brothermode")
BROTHERSBE = os.path.join(ROOT, "products", "brothersbe")


class TestTranslation(unittest.TestCase):

    def setUp(self):
        with io.open(os.path.join(BROTHERSBE, "hooks", "hooks.json"),
                     encoding="utf-8") as handle:
            self.shipped = json.load(handle)

    def test_the_plugin_root_placeholder_is_expanded(self):
        """A user-scope hooks file is not inside a plugin, so nothing would
        substitute ${CLAUDE_PLUGIN_ROOT} at run time. A file that shipped it
        unexpanded would register hooks that cannot run."""
        result = chi.translate(self.shipped, "/somewhere/brothersbe")
        blob = json.dumps(result["hooks"])
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", blob)
        self.assertIn("/somewhere/brothersbe/tools/sbe_fence_hook.py", blob)

    def test_claude_timeout_becomes_codex_timeout_sec(self):
        result = chi.translate(self.shipped, "/somewhere/brothersbe")
        entries = [hook for blocks in result["hooks"].values()
                   for block in blocks for hook in block["hooks"]]
        self.assertTrue(entries)
        self.assertNotIn("timeout", set().union(*(set(e) for e in entries)))
        self.assertTrue(any("timeoutSec" in e for e in entries))

    def test_every_hook_is_synchronous(self):
        """Brother's PreToolUse hooks are refusals. A refusal that runs
        asynchronously cannot refuse anything, so `async` is never left to a
        default."""
        result = chi.translate(self.shipped, "/somewhere/brothersbe")
        for blocks in result["hooks"].values():
            for block in blocks:
                for hook in block["hooks"]:
                    self.assertIs(hook["async"], False, hook)

    def test_an_event_codex_does_not_know_is_skipped_and_named(self):
        """NO-DATA, never a silent drop and never a guessed rename."""
        shipped = {"hooks": {"NotAnEventCodexHas": [
            {"hooks": [{"type": "command", "command": "echo x"}]}]}}
        result = chi.translate(shipped, "/somewhere")
        self.assertEqual(result["hooks"], {})
        self.assertEqual([event for event, _why in result["skipped"]],
                         ["NotAnEventCodexHas"])

    def test_both_products_merge_into_one_document(self):
        built = chi.build([BROTHERMODE, BROTHERSBE])
        self.assertEqual(built["problems"], [])
        commands = [hook["command"]
                    for blocks in built["document"]["hooks"].values()
                    for block in blocks for hook in block["hooks"]]
        self.assertEqual(len(commands), 18, commands)
        self.assertTrue(any("bm_fence_hook.py" in c for c in commands))
        self.assertTrue(any("sbe_fence_hook.py" in c for c in commands))

    def test_a_product_with_no_hooks_file_is_a_named_failure(self):
        built = chi.build([os.path.join(ROOT, "no-such-product")])
        self.assertTrue(built["problems"])
        self.assertIn("no-such-product", built["problems"][0])


class TestHomeResolution(unittest.TestCase):

    def test_the_real_codex_home_is_refused_by_default(self):
        """A hooks file can refuse every edit on a machine, so writing the
        founder's own Codex home is never the default."""
        resolved = chi.resolve_home(os.path.join("~", ".codex"), False)
        self.assertIsNone(resolved["path"])
        self.assertIn("refusing to write", resolved["problem"])

    def test_the_real_codex_home_is_allowed_when_meant(self):
        """The positive control: without it, a refusal that fired for every
        path would satisfy the test above."""
        resolved = chi.resolve_home(os.path.join("~", ".codex"), True)
        self.assertIsNotNone(resolved["path"])
        self.assertIsNone(resolved["problem"])

    def test_a_symlinked_home_resolves_the_way_codex_canonicalizes_it(self):
        """The 2026-09-04 defect: /tmp and /var are symlinks on macOS and
        Codex canonicalizes CODEX_HOME, so a hooks file written under the
        un-resolved spelling is read back under the resolved one, matches no
        sourcePath, and every hook stays untrusted with 0 loaded."""
        real = tempfile.mkdtemp()
        link = os.path.join(tempfile.mkdtemp(), "codex-link")
        os.symlink(real, link)
        try:
            resolved = chi.resolve_home(link, False)
            self.assertIsNone(resolved["problem"])
            self.assertEqual(resolved["path"], os.path.realpath(real))
        finally:
            os.unlink(link)
            shutil.rmtree(real, ignore_errors=True)

    def test_no_home_at_all_is_a_named_failure(self):
        saved = os.environ.pop("CODEX_HOME", None)
        try:
            resolved = chi.resolve_home(None, False)
        finally:
            if saved is not None:
                os.environ["CODEX_HOME"] = saved
        self.assertIsNone(resolved["path"])
        self.assertIn("CODEX_HOME", resolved["problem"])


class TestCheckAndTrust(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_unwired_home_is_no_data_never_a_pass(self):
        built = chi.build([BROTHERSBE])
        verdict, detail = chi.check(self.tmp, built["document"])
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("not wired", detail)

    def test_a_written_home_checks_pass_and_a_tampered_one_fails(self):
        built = chi.build([BROTHERSBE])
        self.assertIsNone(chi.write_hooks_json(self.tmp, built["document"])["problem"])
        self.assertEqual(chi.check(self.tmp, built["document"])[0], "PASS")
        path = chi.hooks_json_path(self.tmp)
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        data["hooks"].pop("PreToolUse")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data))
        self.assertEqual(chi.check(self.tmp, built["document"])[0], "FAIL")

    def test_the_trust_section_is_rewritten_in_place_not_appended_twice(self):
        entries = [{"sourcePath": "/h/hooks.json", "key": "k1",
                    "currentHash": "sha256:aaa"}]
        first = chi.trust_block(entries, "/h/hooks.json")
        self.assertIsNone(chi.write_trust(self.tmp, first)["problem"])
        entries[0]["currentHash"] = "sha256:bbb"
        second = chi.trust_block(entries, "/h/hooks.json")
        self.assertIsNone(chi.write_trust(self.tmp, second)["problem"])
        with io.open(os.path.join(self.tmp, "config.toml"),
                     encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count(chi.TRUST_BEGIN), 1, text)
        self.assertIn("sha256:bbb", text)
        self.assertNotIn("sha256:aaa", text)

    def test_a_foreign_hook_state_table_is_refused_rather_than_rewritten(self):
        path = os.path.join(self.tmp, "config.toml")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write('[hooks.state."someone-elses"]\nenabled = true\n')
        block = chi.trust_block([], "/h/hooks.json")
        problem = chi.write_trust(self.tmp, block)["problem"]
        self.assertIsNotNone(problem)
        self.assertIn("by hand", problem)

    def test_unrelated_config_lines_survive_a_trust_write(self):
        path = os.path.join(self.tmp, "config.toml")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write('model = "gpt-5"\n')
        entries = [{"sourcePath": "/h/hooks.json", "key": "k1",
                    "currentHash": "sha256:aaa"}]
        self.assertIsNone(chi.write_trust(
            self.tmp, chi.trust_block(entries, "/h/hooks.json"))["problem"])
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('model = "gpt-5"', text)
        self.assertIn("sha256:aaa", text)


class TestForeignHookScoping(unittest.TestCase):
    """C3 amendment, 2026-09-05: with the brothermode plugin installed
    (commit 07179111), hooks/list also returns that plugin's own
    hooks/hooks.json entries, marked "source": "plugin" and forever
    untrusted. A warning about that file (Codex clamping its SessionEnd
    timeout) is not a reason to fail our read-back, and those entries are not
    a reason to fail our trust verdict: both must be scoped to the file this
    script itself writes."""

    OWN = "/home/hooks.json"

    def test_a_warning_naming_a_plugin_cache_path_is_a_note_not_a_problem(self):
        warnings = ["clamping SessionEnd hook timeout to 3s in "
                    "/home/plugins/cache/brother/brothermode/3.4.4/hooks/hooks.json"]
        own, foreign = chi.split_warnings(warnings, self.OWN)
        self.assertEqual(own, [])
        self.assertEqual(foreign, warnings)

    def test_a_warning_naming_our_own_hooks_json_is_a_problem(self):
        warnings = ["something is wrong in %s" % self.OWN]
        own, foreign = chi.split_warnings(warnings, self.OWN)
        self.assertEqual(own, warnings)
        self.assertEqual(foreign, [])

    def test_entries_from_two_source_paths_partition_correctly(self):
        entries = [
            {"sourcePath": self.OWN, "trustStatus": "trusted"},
            {"sourcePath": self.OWN, "trustStatus": "trusted"},
            {"sourcePath": "/home/plugins/cache/brother/brothermode/3.4.4/"
                           "hooks/hooks.json",
             "trustStatus": "untrusted", "pluginId": "brothermode@brother"},
        ]
        own, foreign = chi.partition_entries(entries, self.OWN)
        self.assertEqual(len(own), 2)
        self.assertEqual(len(foreign), 1)
        self.assertTrue(all(e["sourcePath"] == self.OWN for e in own))
        self.assertEqual(foreign[0]["pluginId"], "brothermode@brother")

    def test_the_verdict_over_own_entries_is_trusted_despite_foreign_untrusted(self):
        entries = [
            {"sourcePath": self.OWN, "trustStatus": "trusted"},
            {"sourcePath": self.OWN, "trustStatus": "trusted"},
            {"sourcePath": "/home/plugins/cache/brother/brothermode/3.4.4/"
                           "hooks/hooks.json",
             "trustStatus": "untrusted", "pluginId": "brothermode@brother"},
        ]
        own, foreign = chi.partition_entries(entries, self.OWN)
        states = [e["trustStatus"] for e in own]
        self.assertTrue(states and all(s == "trusted" for s in states))
        foreign_states = [e["trustStatus"] for e in foreign]
        self.assertTrue(foreign_states and all(s == "untrusted"
                                                for s in foreign_states))


class TestUninstall(unittest.TestCase):
    """--uninstall removes Brother's own commands and nothing else. Release
    1.0.2 had no uninstall route at all, so the README told a reader to delete
    the whole Codex hooks file, which takes unrelated hooks with it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.built = chi.build([BROTHERMODE, BROTHERSBE])
        self.commands = chi.brother_commands(self.built["document"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_installed(self, with_foreign=False):
        document = json.loads(json.dumps(self.built["document"]))
        if with_foreign:
            document["hooks"].setdefault("SessionStart", []).append(
                {"hooks": [{"type": "command", "command": "/opt/other/hi.sh",
                            "async": False}]})
        self.assertIsNone(chi.write_hooks_json(self.tmp, document)["problem"])
        entries = [{"sourcePath": chi.hooks_json_path(self.tmp), "key": "k1",
                    "currentHash": "sha256:aaa"}]
        self.assertIsNone(chi.write_trust(
            self.tmp, chi.trust_block(entries, chi.hooks_json_path(self.tmp))
        )["problem"])

    def _run(self):
        buffer = io.StringIO()
        saved = sys.stdout
        sys.stdout = buffer
        try:
            code = chi.uninstall(self.tmp, self.commands)
        finally:
            sys.stdout = saved
        return code, buffer.getvalue()

    def test_a_foreign_hook_survives_and_brothers_are_gone(self):
        self._write_installed(with_foreign=True)
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("removed 18 Brother hook command(s)", output)
        with io.open(chi.hooks_json_path(self.tmp), encoding="utf-8") as handle:
            left = json.load(handle)
        remaining = [hook["command"] for blocks in left["hooks"].values()
                     for block in blocks for hook in block["hooks"]]
        self.assertEqual(remaining, ["/opt/other/hi.sh"])

    def test_brothers_trust_section_goes_and_other_config_stays(self):
        self._write_installed()
        path = os.path.join(self.tmp, "config.toml")
        with io.open(path, encoding="utf-8") as handle:
            self.assertIn(chi.TRUST_BEGIN, handle.read())
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("removed Brother's trust section", output)
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(chi.TRUST_BEGIN, text)
        self.assertNotIn("sha256:aaa", text)

    def test_a_file_holding_only_brothers_hooks_is_removed_outright(self):
        self._write_installed()
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertFalse(os.path.exists(chi.hooks_json_path(self.tmp)), output)

    def test_a_second_uninstall_is_no_data_never_a_removal(self):
        self._write_installed(with_foreign=True)
        self._run()
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("NO-DATA", output)
        self.assertIn("nothing of Brother's to remove", output)

    def test_an_untouched_home_is_no_data(self):
        code, output = self._run()
        self.assertEqual(code, 0, output)
        self.assertIn("NO-DATA", output)

    def test_uninstall_refuses_the_default_home_like_install_does(self):
        """The refusal lives in resolve_home, so it must be reached on the
        uninstall route too: an uninstall in the founder's own ~/.codex is as
        unasked-for as an install there."""
        buffer = io.StringIO()
        saved = sys.stdout
        sys.stdout = buffer
        try:
            code = chi.main(["codex_hooks_install.py", "--uninstall",
                             "--codex-home", os.path.join("~", ".codex")])
        finally:
            sys.stdout = saved
        self.assertEqual(code, 1)
        self.assertIn("refusing to write", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
