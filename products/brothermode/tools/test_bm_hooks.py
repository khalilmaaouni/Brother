#!/usr/bin/env python3
"""The hook contract: every automatic hook must be runnable on a machine
with python3 and nothing else.

Standard library only. Run: python3 tools/test_bm_hooks.py

WHY THIS SUITE EXISTS (the Windows lane, 2026-08-17). hooks/hooks.json
carried one `sh <script>` entry and two `sh -c` chains for weeks after
scripts/doctor.py grew a check that FAILED on Windows naming them, because
nothing refused the wiring itself: the doctor told the user, and no gate
told the author. This suite is that gate, the same contract the sibling
repository holds in its own hook suite (TestHookContract): every wired
command starts with python3, no command anywhere contains an `sh -c` or
`bash -c` wrapper, and the heavy PreCompact entry declares a timeout. A
future hook that reaches for a shell fails HERE, by name, before any user
installs it.

Stated plainly, because claiming otherwise would be the overclaim this
repository lints against: a python3-only manifest makes the hooks RUNNABLE
on Windows in principle, and Windows behavior remains UNVERIFIED until a
real Windows machine executes them. docs/WINDOWS-CHECK.md is the protocol
for closing that; this suite closes only the wiring half.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import json
import os
import re
import shlex
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")

_spec = importlib.util.spec_from_file_location(
    "bm_hookchain_for_contract", os.path.join(HERE, "bm_hookchain.py"))
hookchain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hookchain)


def _commands():
    """(event, command, timeout) for every wired hook entry, file order."""
    with io.open(HOOKS_JSON, encoding="utf-8") as fh:
        manifest = json.load(fh)
    rows = []
    for event, groups in manifest.get("hooks", {}).items():
        for group in groups or ():
            for hook in group.get("hooks", ()):
                rows.append((event, hook.get("command", ""),
                             hook.get("timeout")))
    return rows


class TestHookContract(unittest.TestCase):

    def test_the_manifest_wires_at_least_the_six_known_events(self):
        events = set(event for event, _cmd, _t in _commands())
        for expected in ("SessionStart", "SessionEnd", "Stop", "PreCompact",
                         "PreToolUse", "PostToolUse"):
            self.assertIn(expected, events,
                          "hooks/hooks.json no longer wires %s" % expected)

    def test_every_hook_command_is_python3(self):
        offenders = []
        for event, command, _timeout in _commands():
            words = shlex.split(command)
            if not words or os.path.basename(words[0]) != "python3":
                offenders.append("%s: %s" % (event, command))
        self.assertEqual(
            offenders, [],
            "a hook command's interpreter is not python3 (%s). Neither "
            "cmd.exe nor PowerShell can run a POSIX shell, so any other "
            "interpreter here is a hook that silently does nothing on "
            "Windows while Claude Code reports it installed."
            % "; ".join(offenders))

    def test_no_hook_command_wraps_a_shell(self):
        offenders = []
        for event, command, _timeout in _commands():
            if "sh -c" in command or "bash -c" in command:
                offenders.append("%s: %s" % (event, command))
        self.assertEqual(
            offenders, [],
            "a hook command carries a shell wrapper (%s); the chain driver "
            "tools/bm_hookchain.py exists so no hook ever needs one"
            % "; ".join(offenders))

    def test_the_heavy_precompact_entry_declares_a_timeout(self):
        precompact = [(cmd, timeout) for event, cmd, timeout in _commands()
                      if event == "PreCompact"]
        self.assertTrue(precompact, "no PreCompact hook is wired at all")
        for command, timeout in precompact:
            self.assertIsNotNone(
                timeout,
                "the PreCompact entry (%s) declares no timeout; it runs the "
                "autosave, the heaviest hook in the manifest, and an entry "
                "with no budget hangs the compaction instead of degrading"
                % command)
            self.assertGreaterEqual(timeout, 60)

    def test_every_wired_script_exists_in_this_tree(self):
        missing = []
        for event, command, _timeout in _commands():
            for word in shlex.split(command):
                if "${CLAUDE_PLUGIN_ROOT}" not in word:
                    continue
                path = word.replace("${CLAUDE_PLUGIN_ROOT}", ROOT)
                if not os.path.isfile(path):
                    missing.append("%s: %s" % (event, word))
        self.assertEqual(missing, [],
                         "a hook command names a script that does not exist "
                         "in this tree: %s" % "; ".join(missing))


class TestTheTwoHostLaw(unittest.TestCase):
    """CLAUDE.md's two-host law, made mechanical.

    THE LAW, quoted: "the engine speaks plain git only (never gh, never a
    host API, in tools/ or hooks/)". It was written on 2026-08-16 and until
    now it lived entirely in prose, which means it was enforced by whoever
    happened to remember it. This class is the check. A module under tools/
    or a command under hooks/ that reaches for a host API fails HERE, by
    name, rather than being noticed by an adopter whose remote is on the
    other host.

    WHAT COUNTS AS A VIOLATION is narrower than a keyword, on purpose: a
    host NAME in a documentation string, a canonical repository URL, or a
    comment about a redaction pattern breaks nothing and blocks nobody. What
    breaks an adopter is CODE that calls a host: an import of a client
    library, an invocation of `gh`, or a request against a host API
    endpoint. Matching on mentions instead would produce a check everybody
    learns to work around by rephrasing a comment, which is worse than no
    check.

    THE EXEMPTION TABLE IS THE OTHER HALF. One file legitimately speaks a
    host API, and it is named here with its reason rather than excluded by a
    pattern that would silently cover the next one too."""

    #: file -> why it may speak a host API. Anything not listed may not.
    HOST_API_EXEMPT = {
        "bm_bbstatus.py": (
            "the gate runner's reporting arm (2026-08-17). It posts one "
            "Bitbucket build status after a battery finishes, is invoked "
            "only by scripts/local-gates.sh, and is wired into no hook and "
            "no session path. Reporting a verdict is the runner's work; the "
            "ENGINE the law protects is what runs during a session, and "
            "nothing in that path imports this file. It is additionally "
            "covered by tools/test_bm.py's zero-network allow-list, per "
            "file and per module, so it cannot widen quietly."),
        "bm_connectors.py": (
            "the MCP connector catalog and live-probe tool (2026-08-30, "
            "founder order: 'you need all the connectors'). Its job is "
            "reachability across six external platforms (Snowflake, "
            "Databricks, Azure, Teams, Bitbucket, GitHub), not the two-host "
            "GIT ENGINE this law protects; the github probe's gh calls are "
            "what this file IS, not incidental coupling smuggled into the "
            "engine. It is invoked only by its own explicit subcommands "
            "(list/check/print-add/conformance), is never wired into a "
            "hook or the automatic git engine (SECURITY.md's network-reads "
            "section states this), and is separately gated by "
            "tools/test_bm.py's per-file network allow-list, so the "
            "allowance cannot widen quietly."),
    }

    #: Code shapes that actually reach a host, as opposed to naming one.
    HOST_CALLS = (
        (re.compile(r"^\s*(?:import|from)\s+(?:github|atlassian|requests)\b"),
         "imports a host client library"),
        (re.compile(r"""["'\s]gh\s+(?:api|pr|repo|release|auth)\b"""),
         "invokes the gh command line"),
        (re.compile(r"https?://api\.(?:github\.com|bitbucket\.org)"),
         "names a host API endpoint"),
        (re.compile(r"\bos\.environ(?:\.get)?\(\s*[\"'](?:GITHUB|BITBUCKET)_"),
         "reads a host CI environment variable"),
    )

    def test_no_module_under_tools_speaks_a_host_api(self):
        offenders = []
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            if name in self.HOST_API_EXEMPT:
                continue
            with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    for pattern, why in self.HOST_CALLS:
                        if pattern.search(line):
                            offenders.append("%s:%d %s" % (name, number, why))
                            break
        self.assertEqual(
            offenders, [],
            "CLAUDE.md's two-host law says the engine speaks plain git only "
            "and never a host API inside tools/ or hooks/, but %s. Move the "
            "host-facing part to scripts/, where the runner lives, or add a "
            "named exemption here with the reason the adapter boundary "
            "cannot own it." % "; ".join(offenders))

    def test_no_hook_command_speaks_a_host_api(self):
        offenders = []
        for event, command, _timeout in _commands():
            for pattern, why in self.HOST_CALLS:
                if pattern.search(command):
                    offenders.append("%s %s" % (event, why))
        self.assertEqual(offenders, [],
                         "a hook command reaches a host API: %s"
                         % "; ".join(offenders))

    def test_every_exempted_file_exists_and_carries_a_reason(self):
        """A stale exemption is a hole nobody can see. Both halves are
        checked: the file must still be there, and the reason must be a real
        sentence rather than a placeholder."""
        for name, reason in sorted(self.HOST_API_EXEMPT.items()):
            self.assertTrue(os.path.isfile(os.path.join(HERE, name)),
                            "%s is exempted from the two-host law but no "
                            "longer exists; delete the entry" % name)
            self.assertGreater(len(reason.strip()), 80,
                               "%s carries no real reason" % name)

    def test_calibrated_the_lint_catches_a_planted_host_call(self):
        """The vacuous-pass guard: an equality against an empty list proves
        nothing unless the scan can produce a non-empty one. Each shape is
        driven against a line that must be reported."""
        planted = (
            "import requests",
            '    subprocess.run("gh api /repos/x/y")',
            '    url = "https://api.bitbucket.org/2.0/repositories"',
            '    token = os.environ.get("GITHUB_TOKEN")',
        )
        for line in planted:
            self.assertTrue(
                any(pattern.search(line) for pattern, _why
                    in self.HOST_CALLS),
                "the two-host lint no longer catches %r" % line)

    def test_calibrated_the_lint_spares_a_mention(self):
        """The other direction, and the reason the patterns are narrow: a
        documentation URL, a canonical repository address and a comment
        about a redaction pattern all NAME a host and reach none, and a
        check that flagged them would be rephrased away rather than
        obeyed."""
        spared = (
            'REPO_URL = "https://github.com/khalilmaaouni/BrotherModeUp.git"',
            '     "https://docs.github.com/en/copilot/how-tos"',
            "# GITHUB_ghp_... and nid_123-45-6789 all went to disk",
            "    # a throwaway local repository can stand in for github.com",
        )
        for line in spared:
            self.assertFalse(
                any(pattern.search(line) for pattern, _why
                    in self.HOST_CALLS),
                "the two-host lint flags a mere mention, which makes it a "
                "check people rephrase around: %r" % line)


class TestChainTable(unittest.TestCase):
    """The chain contents moved out of the command strings and into ONE
    table, tools/bm_hookchain.py CHAINS. These pins keep that table from
    quietly losing a program, which is how the shell era shipped an ungated
    second program on the PreCompact line."""

    def test_both_chains_exist_and_kept_their_lengths(self):
        self.assertGreaterEqual(len(hookchain.CHAINS["stop"]), 4,
                                "the Stop chain lost a program; four were "
                                "wired when the shell chain was ported")
        self.assertGreaterEqual(len(hookchain.CHAINS["precompact"]), 2,
                                "the PreCompact chain lost a program; two "
                                "were wired when the shell chain was ported")

    def test_every_chained_module_exists_beside_the_driver(self):
        missing = []
        for name, rows in sorted(hookchain.CHAINS.items()):
            for module, _args in rows:
                if not os.path.isfile(os.path.join(HERE, module)):
                    missing.append("%s: %s" % (name, module))
        self.assertEqual(missing, [], "the chain table names a module that "
                         "does not exist in tools/: %s" % "; ".join(missing))

    def test_the_wired_chain_names_are_exactly_the_tables(self):
        wired = set()
        for event, command, _timeout in _commands():
            words = shlex.split(command)
            if len(words) >= 3 and words[1].endswith("bm_hookchain.py"):
                wired.add(words[2])
        self.assertEqual(
            wired, set(hookchain.CHAINS),
            "hooks/hooks.json and bm_hookchain.py CHAINS disagree about "
            "which chains exist; a chain wired but undefined dies at every "
            "firing, and a chain defined but unwired silently never runs")

    def test_an_unknown_chain_name_is_a_usage_refusal(self):
        self.assertEqual(hookchain.main(["no-such-chain"]), 2)
        self.assertEqual(hookchain.main([]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
