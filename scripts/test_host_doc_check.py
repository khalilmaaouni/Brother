#!/usr/bin/env python3
"""Tests for scripts/host_doc_check.py (HP1.c, spec docs/plan/specs/HP1.md section 10).

check_pages runs on seeded copies of the two install pages in a temporary checkout, one defect per copy: a dead
default path, a wrong host name, a wrong host version, a stale "current release" tag, a flag the script does not
offer, an unresolvable anchor, a missing bypass statement and a command that exits non zero. The clean copies pass, so
each refusal below is the one defect's own. The scripts in the seeded checkout are small argparse stand ins written
the way brother_install.py and codex_hooks_install.py build theirs; one test also resolves the page commands against
the real scripts of this checkout. Each test builds its own HOME in a temporary folder.

usage (repo root): python3 scripts/test_host_doc_check.py TestHostDocCheck -v
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import brother_paths  # noqa: E402
import host_doc_check as hdc  # noqa: E402

CODEX = "docs/how-to/install-codex.md"
ANTIGRAVITY = "docs/how-to/install-antigravity.md"
RELEASE = "9.8.7"
A1 = hashlib.sha256(b"antigravity pre_tool raw out").hexdigest()
A2 = hashlib.sha256(b"antigravity pre_invocation raw out").hexdigest()
C1 = hashlib.sha256(b"codex raw out").hexdigest()
NOWHERE = hashlib.sha256(b"a row nobody recorded").hexdigest()

STUB_INSTALL = '''import argparse


def build_argparser():
    parser = argparse.ArgumentParser(description="stand in")
    sub = parser.add_subparsers(dest="verb", required=True)

    def common(p):
        p.add_argument("--codex-home", default=None)
        p.add_argument("--allow-default-home", action="store_true")
        p.add_argument("--json", action="store_true")

    p_install = sub.add_parser("install")
    common(p_install)
    p_install.add_argument("--ref", required=True)
    p_rollback = sub.add_parser("rollback")
    common(p_rollback)
    p_rollback.add_argument("--to", default=None)
    p_status = sub.add_parser("status")
    common(p_status)
    return parser
'''

STUB_HOOKS_INSTALL = '''import argparse


def main(argv):
    parser = argparse.ArgumentParser(description="stand in")
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--allow-default-home", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--trust", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--cwd", default=None)
    return parser.parse_args(argv[1:])
'''

STUB_HOOK = '''TOOL_MAP = {
    "run_command": "Bash",
    "write_to_file": "Write",
    "view_file": "Read",
    "list_dir": "ListDir",
    "grep_search": "Grep",
}


def main(mode):
    if mode in ("pre_tool", "PreToolUse"):
        return 0
    return 2
'''

MATCHER = "run_command|replace_file_content|write_to_file|multi_replace_file_content"

CODEX_TEXT = """# Install Brother on Codex

The installer defaults to `DEFAULT_BIN` on macOS.

This example uses vRELEASE, the current release.

```bash
git clone --branch vRELEASE ./Brother.git
cd Brother
python3 scripts/brother_install.py install --ref vRELEASE --codex-home "$HOME/.codex" --allow-default-home
python3 scripts/brother_install.py status --codex-home "$HOME/.codex" --allow-default-home --json
```

First inspect the wiring:

```bash
python3 scripts/codex_hooks_install.py --check --codex-home "$HOME/.codex" --allow-default-home --cwd "$TARGET_REPO"
```
"""

ANTIGRAVITY_TEXT = """# Install the Antigravity hook plugin

- The Antigravity host `antigravity-ide` at version `1.107.0`. <!-- evidence: A1 -->

<!-- evidence: A1 -->
```bash
python3 scripts/brother_antigravity_hook.py pre_tool < payload.json
```

The read tools `view_file`, `list_dir` and `grep_search` do not reach PreToolUse. <!-- evidence: A2 -->
"""

BYPASS_LINE = "The read tools `view_file`, `list_dir` and `grep_search` do not reach PreToolUse. <!-- evidence: A2 -->"
STATUS = 'python3 scripts/brother_install.py status --codex-home "$HOME/.codex" --allow-default-home --json'


def _rows():
    return [
        {"schema": "hp1.v1", "host": "antigravity", "host_version": "1.107.0", "raw_out_sha256": A1,
         "host_bin_realpath": "/opt/hp1c/.antigravity-ide/antigravity-ide/bin/antigravity-ide"},
        {"schema": "hp1.v1", "host": "codex", "host_version": "codex-cli 0.157.0", "raw_out_sha256": C1,
         "host_bin_realpath": "/opt/hp1c/codex-cli/bin/codex"},
        {"schema": "hp1.v1", "host": "antigravity", "host_version": "1.107.0", "raw_out_sha256": A2,
         "host_bin_realpath": "/opt/hp1c/.antigravity-ide/antigravity-ide/bin/antigravity-ide"},
    ]


class TestHostDocCheck(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hp1c-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.default = brother_paths.codex_bin({})
        self.root = os.path.join(self.tmp, "root")
        self.write("scripts/brother_install.py", STUB_INSTALL)
        self.write("scripts/codex_hooks_install.py", STUB_HOOKS_INSTALL)
        self.write("scripts/brother_antigravity_hook.py", STUB_HOOK)
        self.write_hooks(MATCHER)
        self.write(".claude-plugin/marketplace.json",
                   json.dumps({"name": "brother", "plugins": [{"name": "brother", "version": RELEASE}]}))
        self.evidence = os.path.join(self.root, "evidence.jsonl")
        self.write("evidence.jsonl", "".join(json.dumps(row) + "\n" for row in _rows()))
        self.codex_page()
        self.antigravity_page()
        self.calls = []
        self.codes = {}

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def write_hooks(self, matcher):
        self.write("bundle/.antigravity-plugin/hooks.json", json.dumps({"brother-assurance": {"PreToolUse": [
            {"matcher": matcher, "hooks": [{"type": "command",
                                            "command": "python3 scripts/brother_antigravity_hook.py pre_tool"}]}]}}))

    def codex_page(self, old=None, new=None):
        text = CODEX_TEXT.replace("DEFAULT_BIN", self.default).replace("RELEASE", RELEASE)
        self.write(CODEX, text.replace(old, new) if old else text)

    def antigravity_page(self, old=None, new=None):
        text = ANTIGRAVITY_TEXT.replace("A1", A1).replace("A2", A2)
        self.write(ANTIGRAVITY, text.replace(old, new) if old else text)

    def runner(self, argv, cwd, env):
        self.calls.append((list(argv), cwd, dict(env)))
        return self.codes.get(argv[1], 0)

    def check(self, **kw):
        kw.setdefault("runner", self.runner)
        out = hdc.check_pages(self.root, kw.pop("evidence", self.evidence), **kw)
        self.assertIsInstance(out, list)
        for line in out:
            self.assertIsInstance(line, str)
        return out

    # the clean copies and the one defect copies

    def test_clean_seeded_pages_pass_and_run_only_the_safe_list_in_a_throwaway_home(self):
        self.assertEqual(self.check(), [])
        ran = [argv[1:3] for argv, _cwd, _env in self.calls]
        self.assertEqual(ran, [["scripts/brother_install.py", "status"], ["scripts/codex_hooks_install.py", "--check"]])
        for argv, cwd, env in self.calls:
            self.assertEqual(cwd, self.root)
            self.assertNotEqual(os.path.realpath(env["HOME"]), os.path.realpath(self.home))
            self.assertEqual(argv[argv.index("--codex-home") + 1], os.path.join(env["HOME"], ".codex"))
            self.assertFalse(any("$" in word for word in argv), argv)
            self.assertFalse(os.path.exists(env["HOME"]), "the throwaway home outlived the check")

    def test_dead_default_path_is_refused(self):
        dead = os.path.join(self.tmp, "gone", "Contents", "Resources", "codex")
        self.codex_page(self.default, dead)
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(CODEX + " line 3:", out[0])
        self.assertIn(dead, out[0])
        self.assertIn(self.default, out[0])

    def test_wrong_host_version_is_refused(self):
        self.antigravity_page("`1.107.0`", "`1.0.0`")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(ANTIGRAVITY + " line 3:", out[0])
        self.assertIn("version 1.0.0", out[0])
        self.assertIn("1.107.0", out[0])

    def test_wrong_host_name_is_refused(self):
        self.antigravity_page("`antigravity-ide`", "`agy`")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(ANTIGRAVITY + " line 3:", out[0])
        self.assertIn("names host agy", out[0])
        self.assertIn("antigravity-ide", out[0])

    def test_a_fenced_host_binary_is_a_host_name(self):
        self.antigravity_page("python3 scripts/brother_antigravity_hook.py pre_tool < payload.json", "agy")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(ANTIGRAVITY + " line 7:", out[0])
        self.assertIn("names host agy", out[0])

    def test_an_antigravity_page_naming_no_host_is_refused(self):
        self.antigravity_page("- The Antigravity host `antigravity-ide` at version `1.107.0`.", "- The host plugin.")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(ANTIGRAVITY + ": names no antigravity host binary", out[0])

    def test_stale_current_release_tag_is_refused(self):
        self.codex_page("v%s, the current release" % RELEASE, "v1.0.19, the current release")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(CODEX + " line 5:", out[0])
        self.assertIn("v1.0.19", out[0])
        self.assertIn(RELEASE, out[0])
        self.codex_page("v%s, the current release" % RELEASE, "1.0.19, the current release")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("names release 1.0.19", out[0])
        self.codex_page("--branch v%s" % RELEASE, "--branch v1.0.19")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(CODEX + " line 8:", out[0])

    def test_flag_the_script_does_not_offer_is_refused(self):
        self.codex_page("--allow-default-home\npython3 scripts/brother_install.py status",
                        "--allow-default-home --frobnicate\npython3 scripts/brother_install.py status")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(CODEX + " line 10:", out[0])
        self.assertIn("--frobnicate", out[0])

    def test_a_flag_of_another_subcommand_and_an_unknown_subcommand_are_refused(self):
        self.assertEqual(hdc.resolve_command(self.root, "python3 scripts/brother_install.py rollback --to x"), [])
        out = hdc.resolve_command(self.root, "python3 scripts/brother_install.py status --to x")
        self.assertEqual(len(out), 1, out)
        self.assertIn("--to", out[0])
        out = hdc.resolve_command(self.root, "python3 scripts/brother_install.py verify --json")
        self.assertEqual(len(out), 1, out)
        self.assertIn("subcommand verify", out[0])
        out = hdc.resolve_command(self.root, "python3 scripts/brother_install.py --json")
        self.assertTrue(out)
        out = hdc.resolve_command(self.root, "python3 scripts/brother_antigravity_hook.py no_such_mode")
        self.assertEqual(len(out), 1, out)
        out = hdc.resolve_command(self.root, "python3 scripts/not_in_this_tree.py --json")
        self.assertEqual(len(out), 1, out)
        self.assertIn("not in the tree", out[0])
        outside = os.path.join(self.root, "scripts", "brother_install.py")
        for command in ("python3 %s status" % outside, "python3 ../root/scripts/brother_install.py status",
                        "python3 $HOME/scripts/brother_install.py status", "sh scripts/absent.sh"):
            out = hdc.resolve_command(self.root, command)
            self.assertEqual(len(out), 1, (command, out))

    def test_unresolvable_anchor_is_refused(self):
        self.antigravity_page("do not reach PreToolUse. <!-- evidence: %s -->" % A2,
                              "do not reach PreToolUse. <!-- evidence: %s -->" % NOWHERE)
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(ANTIGRAVITY + " line 10:", out[0])
        self.assertIn(NOWHERE, out[0])
        self.assertIn("resolves to no antigravity row", out[0])
        self.antigravity_page("do not reach PreToolUse. <!-- evidence: %s -->" % A2,
                              "do not reach PreToolUse. <!-- evidence: %s -->" % C1)
        out = self.check()
        self.assertEqual(len(out), 1, "a Codex row cannot anchor an Antigravity fact: %s" % out)
        self.antigravity_page("do not reach PreToolUse. <!-- evidence: %s -->" % A2,
                              "do not reach PreToolUse. <!-- evidence: 0f1e2d3c... -->")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("not a sha256", out[0])

    def test_missing_bypass_statement_is_refused(self):
        self.antigravity_page(BYPASS_LINE.replace("A2", A2) + "\n", "")
        out = self.check()
        self.assertEqual(len(out), 3, out)
        for tool in hdc.BYPASS_TOOLS:
            self.assertTrue(any("`%s` does not reach PreToolUse" % tool in line for line in out), (tool, out))
        self.antigravity_page("`list_dir` and `grep_search` do not", "`list_dir` do not")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("`grep_search`", out[0])
        self.antigravity_page("do not reach PreToolUse", "reach PreToolUse")
        out = self.check()
        self.assertEqual(len(out), 3, "the names alone are not the statement: %s" % out)

    def test_bypass_statement_is_checked_against_the_shipped_files(self):
        self.write_hooks(MATCHER + "|view_file")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("`view_file`", out[0])
        self.assertIn("matches it", out[0])
        self.write_hooks(MATCHER)
        self.write("scripts/brother_antigravity_hook.py", STUB_HOOK.replace('"grep_search"', '"grep"'))
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("never names it", out[0])
        self.write("scripts/brother_antigravity_hook.py", STUB_HOOK)
        os.remove(os.path.join(self.root, "bundle/.antigravity-plugin/hooks.json"))
        out = self.check()
        self.assertEqual(len(out), 3, out)
        self.assertTrue(all("NO-DATA" in line for line in out), out)
        self.write_hooks(MATCHER)
        self.antigravity_page("`grep_search` do not", "`grep_search` and `run_command` do not")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("`run_command`", out[0])

    def test_command_that_exits_non_zero_is_refused(self):
        self.codes["scripts/codex_hooks_install.py"] = 1
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(CODEX + " line 17:", out[0])
        self.assertIn("exits 1", out[0])

    def test_missing_runner_is_no_data_never_a_pass(self):
        for runner in (None, 5, "runner"):
            out = self.check(runner=runner)
            self.assertEqual(len(out), 2, (runner, out))
            self.assertTrue(all("NO-DATA" in line for line in out), out)

        def raises(argv, cwd, env):
            raise OSError("no such interpreter")

        out = self.check(runner=raises)
        self.assertEqual(len(out), 2, out)
        self.assertTrue(all("raised OSError" in line for line in out), out)
        for code in (True, None, "0", 0.0, float("nan")):
            out = self.check(runner=lambda argv, cwd, env, code=code: code)
            self.assertEqual(len(out), 2, (code, out))
            self.assertTrue(all("not an exit code" in line for line in out), out)

    def test_only_the_exact_safe_shapes_run(self):
        reworded = 'python3 scripts/brother_install.py status --json --codex-home "$HOME/.codex" --allow-default-home'
        prefixed = "BROTHER_CODEX_BIN=/x/codex " + STATUS
        redirected = STATUS + " > out.txt"
        for variant in (reworded, prefixed, redirected):
            self.calls = []
            self.codex_page(STATUS, variant)
            self.assertEqual(self.check(), [], variant)
            self.assertEqual([argv[1] for argv, _cwd, _env in self.calls], ["scripts/codex_hooks_install.py"],
                             variant)
        commands = [{"line": 1, "command": 'python3 scripts/brother_install.py install --ref v1 --codex-home "$HOME/.codex"'},
                    {"line": 2, "command": "git clone --branch v1 ./Brother.git"},
                    {"line": 3, "command": STATUS}]
        self.calls = []
        throwaway = os.path.join(self.tmp, "throwaway")
        os.makedirs(throwaway)
        self.assertEqual(hdc.run_safe_commands(self.root, throwaway, commands, runner=self.runner), [])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0][2], "status")
        self.assertEqual(self.calls[0][2]["HOME"], throwaway)

    def test_the_users_own_home_is_never_the_throwaway_home(self):
        for home in (self.home, os.path.dirname(self.home), "/", "relative/home", None, 3, ["x"]):
            self.calls = []
            out = hdc.run_safe_commands(self.root, home, [{"line": 4, "command": STATUS}], runner=self.runner)
            self.assertEqual(len(out), 1, (home, out))
            self.assertIn("did not run", out[0])
            self.assertEqual(self.calls, [], home)

    # the edges the specification names

    def test_page_with_no_fenced_block_is_refused(self):
        for text in ("", "# Install Brother on Codex\n\nNothing to run here.\n", "```json\n{\"a\": 1}\n```\n"):
            self.write(CODEX, text)
            out = self.check()
            self.assertEqual(len(out), 1, (text, out))
            self.assertIn(CODEX + ": carries no fenced command block", out[0])

    def test_line_continuation_and_environment_prefix(self):
        text = ("```bash\nBROTHER_CODEX_BIN=/x/codex python3 scripts/brother_install.py status \\\n"
                "  --codex-home \"$HOME/.codex\" \\\n  --json\n```\n")
        records = hdc.fenced_commands(text)
        self.assertEqual(len(records), 1, records)
        record = records[0]
        self.assertEqual(record["line"], 2)
        self.assertEqual(record["env"], {"BROTHER_CODEX_BIN": "/x/codex"})
        self.assertEqual(record["argv"], ["python3", "scripts/brother_install.py", "status", "--codex-home",
                                          "$HOME/.codex", "--json"])
        self.assertEqual(record["error"], "")
        self.assertEqual(hdc.resolve_command(self.root, record["command"]), [])
        bad = hdc.fenced_commands(text.replace("  --json", "  --json --bogus"))
        self.assertEqual(len(hdc.resolve_command(self.root, bad[0]["command"])), 1)
        cut = hdc.fenced_commands("```bash\npython3 scripts/brother_install.py status \\\n```\n")
        self.assertEqual(len(cut), 1)
        self.assertIn("runs past the end", cut[0]["error"])
        open_fence = hdc.fenced_commands("```bash\npython3 scripts/brother_install.py status\n")
        self.assertTrue(any("never closed" in r["error"] for r in open_fence), open_fence)
        self.write(CODEX, CODEX_TEXT.replace("DEFAULT_BIN", self.default).replace("RELEASE", RELEASE)
                   + "\n```bash\npython3 scripts/codex_hooks_install.py --check \\\n  --nope\n```\n")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(CODEX + " line 21:", out[0])
        self.assertIn("--nope", out[0])

    def test_a_version_in_a_code_span_and_again_in_prose_are_both_checked(self):
        self.antigravity_page("`1.107.0`", "`1.0.0`")
        with open(os.path.join(self.root, ANTIGRAVITY), "a", encoding="utf-8") as fh:
            fh.write("\nThe host reports version 1.0.0 when asked.\n")
        with open(os.path.join(self.root, ANTIGRAVITY), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(hdc.page_facts(text)["host_versions"], ["1.0.0", "1.0.0"])
        out = self.check()
        self.assertEqual(len(out), 2, out)
        self.assertIn(ANTIGRAVITY + " line 3:", out[0])
        self.assertIn(ANTIGRAVITY + " line 12:", out[1])
        self.antigravity_page()
        with open(os.path.join(self.root, ANTIGRAVITY), "a", encoding="utf-8") as fh:
            fh.write("\nThe host reports version 1.0.0 when asked.\n")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn(ANTIGRAVITY + " line 12:", out[0])

    def test_missing_evidence_reports_no_data_and_the_page_may_name_no_number(self):
        absent = os.path.join(self.root, "not-recorded-yet.jsonl")
        out = self.check(evidence=absent)
        self.assertFalse([line for line in out if line.startswith(CODEX)], out)
        versions = [line for line in out if "host version" in line]
        self.assertEqual(len(versions), 1, out)
        self.assertIn("NO-DATA", versions[0])
        self.assertIn("1.107.0", versions[0])
        self.assertTrue(all("NO-DATA" in line for line in out), out)
        self.antigravity_page(" at version `1.107.0`", "")
        out = self.check(evidence=absent)
        self.assertFalse([line for line in out if "version" in line], out)
        self.write("evidence.jsonl", "\n\n")
        out = self.check()
        self.assertTrue(out)
        self.assertTrue(all("NO-DATA" in line for line in out), out)

    def test_corrupt_evidence_is_refused(self):
        for body in ('{"host": "antigravity"\n', '["host", "antigravity"]\n', '{"a": 1}\nnot json\n'):
            self.write("evidence.jsonl", body)
            out = self.check()
            self.assertTrue(out[0].startswith("CORRUPT"), (body, out))

    def test_hostile_inputs_are_refused_not_raised(self):
        nan = float("nan")
        for root in (None, 7, True, ["x"], {}, nan, "", "a\x00b", os.path.join(self.tmp, "absent")):
            out = hdc.check_pages(root, self.evidence, runner=self.runner)
            self.assertTrue(out and all(isinstance(line, str) for line in out), root)
            self.assertIn("NO-DATA", out[0])
        for evidence in (None, 3, True, ["x"], {}, nan, b"evidence.jsonl"):
            out = self.check(evidence=evidence)
            self.assertTrue(out[0].startswith("REFUSED"), (evidence, out))
        for command in (None, 7, True, ["python3"], {}, nan, b"python3", "", "a\x00b"):
            out = hdc.resolve_command(self.root, command)
            self.assertTrue(out and all(isinstance(line, str) for line in out), command)
        self.assertIn("NO-DATA", hdc.resolve_command(None, STATUS)[0])
        self.assertTrue(hdc.resolve_command(self.root, 'python3 scripts/brother_install.py "status'))
        self.calls = []
        throwaway = os.path.join(self.tmp, "throwaway")
        os.makedirs(throwaway)
        for commands in (STATUS, None, 3, {"line": 1, "command": STATUS}, (STATUS,)):
            out = hdc.run_safe_commands(self.root, throwaway, commands, runner=self.runner)
            self.assertEqual(len(out), 1, commands)
        for item in ({"line": True, "command": STATUS}, {"line": 1, "command": None}, STATUS, None,
                     {"line": nan, "command": STATUS}, {"line": 0, "command": STATUS}, {"command": STATUS}):
            out = hdc.run_safe_commands(self.root, throwaway, [item], runner=self.runner)
            self.assertEqual(len(out), 1, item)
            self.assertIn("not a fenced command record", out[0])
        self.assertEqual(self.calls, [])
        for text in (None, 7, b"```bash\nls\n```", ["x"], {}, nan):
            with self.assertRaises(ValueError):
                hdc.fenced_commands(text)
            with self.assertRaises(ValueError):
                hdc.page_facts(text)
        with open(os.path.join(self.root, CODEX), "wb") as fh:
            fh.write(b"\xff\xfe not utf-8")
        out = self.check()
        self.assertEqual(len(out), 1, out)
        self.assertIn("not UTF-8", out[0])

    def test_real_scripts_resolve_the_page_commands(self):
        self.assertEqual(hdc.resolve_command(REPO, STATUS), [])
        self.assertEqual(hdc.resolve_command(REPO, "python3 scripts/brother_install.py upgrade --from-ref "
                                                   "'<installed-tag>' --ref '<new-tag>' --codex-home \"$HOME/.codex\" "
                                                   "--allow-default-home"), [])
        self.assertEqual(hdc.resolve_command(REPO, 'python3 scripts/codex_hooks_install.py --check --codex-home '
                                                   '"$HOME/.codex" --allow-default-home --cwd "$TARGET_REPO"'), [])
        self.assertEqual(hdc.resolve_command(REPO, "python3 scripts/brother_antigravity_hook.py pre_tool < x.json"), [])
        self.assertEqual(hdc.resolve_command(REPO, "git clone --branch v1.1.0 ./Brother.git"), [])
        for command in ("python3 scripts/brother_install.py status --to x",
                        "python3 scripts/brother_install.py install --frobnicate",
                        "python3 scripts/codex_hooks_install.py --frobnicate",
                        "python3 scripts/brother_antigravity_hook.py no_such_mode",
                        "python3 -c 'print(1)'"):
            self.assertEqual(len(hdc.resolve_command(REPO, command)), 1, command)

    def test_the_stale_facts_of_2026_10_03_are_each_refused(self):
        self.write(CODEX, "\n".join((
            "# Install Brother on Codex",
            "The installer defaults to `/Applications/ChatGPT.app/Contents/Resources/codex` on macOS.",
            "This example uses v1.0.19, the current release.",
            "```bash",
            "git clone --branch v1.0.19 ./Brother.git",
            "python3 scripts/brother_install.py install --ref v1.0.19 --codex-home \"$HOME/.codex\" "
            "--allow-default-home",
            "```", "")))
        self.write(ANTIGRAVITY, "\n".join((
            "# Install the Antigravity hook plugin",
            "- The Antigravity host `agy` at version `1.0.0`.",
            "<!-- evidence: 0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0 -->",
            "```bash", "agy", "```", "")))
        out = self.check()
        expected = ((CODEX + " line 2:", "codex_bin"), (CODEX + " line 3:", "v1.0.19"),
                    (CODEX + " line 5:", "v1.0.19"), (CODEX + " line 6:", "v1.0.19"),
                    (ANTIGRAVITY + " line 2:", "names host agy"), (ANTIGRAVITY + " line 2:", "version 1.0.0"),
                    (ANTIGRAVITY + " line 3:", "resolves to no antigravity row"),
                    (ANTIGRAVITY + " line 5:", "names host agy"))
        if self.default == "/Applications/ChatGPT.app/Contents/Resources/codex":
            expected = expected[1:]
        for where, what in expected:
            self.assertTrue(any(line.startswith(where) and what in line for line in out), (where, what, out))
        self.assertEqual(sum("does not reach PreToolUse" in line for line in out), 3, out)


if __name__ == "__main__":
    unittest.main()
