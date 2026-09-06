#!/usr/bin/env python3
"""brother_install, pinned without a network or a real Codex binary: the
pure parts (hook pruning, staleness, the marketplace ref reader, the atomic
write) and the honest verbs (a second uninstall is NO-DATA at exit 0, a
rollback with no snapshot is NO-DATA) through a fake --codex-bin."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brother_install as BI  # noqa: E402

FAKE_CODEX = """#!/bin/sh
case "$*" in
  *"plugin list --json"*) echo '{"installed": []}' ;;
  *"plugin list --available --json"*) echo '{"available": []}' ;;
  *) echo '{}' ;;
esac
exit 0
"""


def write_fake_codex(tmp):
    path = os.path.join(tmp, "codex")
    with open(path, "w") as fh:
        fh.write(FAKE_CODEX)
    os.chmod(path, 0o755)
    return path


class HookPruning(unittest.TestCase):
    def doc(self, brother_cmd):
        return {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [
            {"type": "command", "command": brother_cmd},
            {"type": "command", "command": "python3 /Users/someone/other-tool.py"}]}]}}

    def test_temp_brother_hook_removed_foreign_hook_kept(self):
        out = BI.prune_brother_hooks(self.doc('python3 "/private/tmp/brother-x/tools/bm_fence_hook.py"'), only_stale=True)
        self.assertEqual(len(out["removed"]), 1)
        kept = out["document"]["hooks"]["PreToolUse"][0]["hooks"]
        self.assertEqual([h["command"] for h in kept], ["python3 /Users/someone/other-tool.py"])

    def test_missing_target_is_stale(self):
        self.assertTrue(BI.is_stale_target('python3 "/no/such/brother/tools/bm_fence_hook.py"'))

    def test_live_durable_target_is_not_stale(self):
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as home:
            tool = os.path.join(home, "brother_tool.py")
            with open(tool, "w") as fh:
                fh.write("pass\n")
            self.assertFalse(BI.is_stale_target('python3 "%s"' % tool))

    def test_non_brother_command_never_pruned_even_when_stale(self):
        out = BI.prune_brother_hooks(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 /no/such/other.py"}]}]}},
            only_stale=False)
        self.assertEqual(out["removed"], [])


class MarketplaceRef(unittest.TestCase):
    def test_reads_the_ref_of_the_named_marketplace(self):
        text = ('[marketplaces.other]\nsource_type = "git"\nref = "v9"\n\n'
                '[marketplaces.brother]\nsource_type = "git"\n'
                'source = "https://github.com/khalilmaaouni/Brother.git"\nref = "v1.0.8"\n')
        self.assertEqual(BI.read_marketplace_ref(text, "brother"), "v1.0.8")

    def test_absent_marketplace_is_none(self):
        self.assertIsNone(BI.read_marketplace_ref("[plugins.x]\nenabled = true\n", "brother"))


class AtomicWrite(unittest.TestCase):
    def test_write_lands_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hooks.json")
            BI.atomic_write(path, '{"a": 1}')
            with open(path) as fh:
                self.assertEqual(json.load(fh), {"a": 1})
            self.assertEqual(sorted(os.listdir(tmp)), ["hooks.json"])


class HonestVerbs(unittest.TestCase):
    def run_cli(self, tmp, *args):
        fake = write_fake_codex(tmp)
        home = os.path.join(tmp, "home")
        argv = [sys.executable, os.path.join(HERE, "brother_install.py")] + list(args) + [
            "--codex-home", home, "--codex-bin", fake]
        return subprocess.run(argv, capture_output=True, text=True), home

    def test_second_uninstall_is_no_data_at_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home"))
            first, home = self.run_cli(tmp, "uninstall")
            second, _ = self.run_cli(tmp, "uninstall")
            body = second.stdout + second.stderr
            self.assertEqual(second.returncode, 0, body)
            self.assertIn("NO-DATA", body)
            self.assertNotIn("verdict=PASS", body)

    def test_rollback_without_snapshot_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home"))
            proc, _ = self.run_cli(tmp, "rollback")
            self.assertIn("NO-DATA", proc.stdout + proc.stderr)

    def test_status_on_empty_home_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "home"))
            proc, _ = self.run_cli(tmp, "status")
            self.assertIn("ABSENT", proc.stdout + proc.stderr)

    def test_a_filesystem_root_or_the_users_home_is_refused_as_a_codex_home(self):
        for named in ("/", os.path.expanduser("~")):
            got = BI.resolve_home(named, True)
            self.assertIsNone(got["path"], named)
            self.assertIn("filesystem root or the user's own home", got["problem"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(BI.resolve_home(tmp, False)["path"], os.path.realpath(tmp))

    def test_real_home_refused_without_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = write_fake_codex(tmp)
            proc = subprocess.run([sys.executable, os.path.join(HERE, "brother_install.py"),
                                   "status", "--codex-bin", fake],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
