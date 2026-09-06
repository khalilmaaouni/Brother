#!/usr/bin/env python3
"""BrotherMode cross-runtime adapters: emit per-runtime instruction files.

WHY THIS EXISTS
  BrotherMode's engine is a handful of Python files driven from a shell. Nothing
  in bm_store.py, bm_threads.py, bm_learn.py or bm_telemetry.py is specific to
  Claude Code. What IS specific to Claude Code is the wiring: the hooks, and the
  file the runtime reads to learn the law. Every other coding runtime has its own
  instruction file convention and its own hook story, and a founder who works in
  two runtimes should not have to hand transcribe the law into each one and then
  watch the copies drift.

  So this file generates them. One registry of runtimes, one renderer, one
  command. Regenerating is cheap, which is the point: a generated file that
  drifts is a bug the gate catches, a hand written one is a bug nobody sees.

WHAT THIS FILE REFUSES TO DO
  It does not guess. Every destination path in the registry below was read off
  the runtime vendor's own documentation page on the date recorded beside it, and
  the URL travels with the fact: into the generated file's header, and into
  docs/RUNTIMES.md. A convention that could not be verified ships marked GENERIC
  with the reason attached, rather than shipping as a confident path that sends
  the founder's law into a file nothing reads.

  It also does not overwrite anything the founder owns. Emission writes into a
  STAGING directory (docs/runtimes by default, which is COMMITTED on purpose so
  the adapter files are readable on the repository page) plus the capability
  table, and prints an install map. AGENTS.md at a repository root is frequently
  a file with real content in it already; a generator that clobbers it would be
  worse than no generator. Every target is checked before anything is written:
  a file that exists and does NOT carry this generator's marker line is treated
  as founder owned, and the whole emit refuses rather than writing part of it.
  --out moves the capability table too, so a redirected emit touches nothing
  outside the directory the founder named.

THE HONEST LIMIT, STATED ONCE HERE AND AGAIN IN EVERY FILE IT WRITES
  Two different questions get confused constantly, so they are kept apart:
    1. does this runtime have hook points at all
    2. do BROTHERMODE's hooks run there
  Question 1 has a verified answer per runtime. Question 2 has exactly one
  verified yes, Claude Code, because bm_fence_hook.py and bm_telemetry.py's hook
  targets parse Claude Code's hook JSON contract (documented in docs/HOOKS.md).
  Another runtime having a PreToolUse event does NOT mean it hands that event the
  same JSON shape. Until somebody runs the adapter against the real runtime and
  records the payload, the correct entry is "hook points exist, BrotherMode hook
  compatibility unverified", and that is what the table says.

  One runtime has now been measured rather than assumed: OpenAI Codex CLI, on
  2026-08-05, on codex-cli 0.146.0. The measured answer is a NO, not a yes and
  not an unknown: Codex reports every file write as tool_name Bash running
  apply_patch, so BrotherMode's Edit/Write matcher never fires. A registry entry
  carrying a `measured` block replaces UNVERIFIED with what was observed, in
  both directions, because UNVERIFIED reads as "might work".

  The retrieval and store commands are NOT the opposite case, which is the
  mistake this file used to ship. They are ordinary processes, so the CODE is
  runtime neutral; the EXPERIENCE is not. They live in the BrotherMode checkout,
  so a project that is not the checkout must call them by absolute path, and a
  runtime that sandboxes its shell read-only cannot create the store at all.
  Both failures were measured under Codex on 2026-08-05, and both are now stated
  in every file this generator writes.

Python 3.9, standard library only. No network, no subprocess. The verification
this file's registry depends on happened OUT OF BAND, by a human or an agent
opening the vendor page and recording the URL and the date; this module never
fetches anything at runtime, and it never could.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import ast
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The date every URL in the registry below was actually opened and read. It is
# rendered into every generated file so a reader can judge for themselves how
# stale the claim is, instead of trusting a document with no date on it.
VERIFIED_ON = "2026-07-29"


def verified_on(entry):
    """The date THIS entry's sources were opened, not the registry's.

    A single shared date was honest while every entry landed in one sitting.
    The moment one entry is re-read later, that date either understates the
    new entry or, if bumped, silently claims the older ones were re-checked
    too. Both are wrong in the direction this project refuses, so an entry
    may carry its own `verified_on` and everything rendered about it uses
    that. Entries without one keep the registry-wide date, unchanged.
    """
    return entry.get("verified_on", VERIFIED_ON) if isinstance(entry, dict) else VERIFIED_ON

GENERATED_MARKER = "GENERATED BY BrotherMode tools/bm_runtimes.py"

# Where emitted adapters land by default. docs/runtimes is COMMITTED on purpose
# rather than hidden under .brothermode: this is a public product, and somebody
# deciding whether BrotherMode fits their Codex or Qwen setup should be able to
# read the exact adapter file on the repository page without cloning and running
# anything. It also means `check` can gate the committed copies against the
# registry in the normal suite, which is what stops the capability table and the
# adapter files from drifting apart.
DEFAULT_STAGING = os.path.join("docs", "runtimes")

# THE CHECKOUT VERSUS THE PROJECT. This is the distinction that broke the
# product. `tools/bm_store.py` is relative to the directory BrotherMode itself
# was cloned into, and the generated files used to print it under the sentence
# "Run them from the project root. Paths below are relative to that root". In a
# user's own project that path does not exist, so the FIRST instruction a Codex
# agent obeyed failed with [Errno 2] (reproduced by two independent lanes on
# 2026-08-05, and again in
# docs/program/absolute-lead/evidence/RED-codex-runtime.txt).
#
# When emit runs from OUTSIDE the checkout, tools_reference() already returns an
# absolute path and the generated file is literally runnable. When it runs from
# inside the checkout (which is how the committed adapters are produced, and it
# must stay machine independent so `check` passes in any clone) the path is
# relative, and it is rendered under this placeholder instead of bare, so it can
# never be mistaken for something to paste.
CHECKOUT_TOKEN = "<checkout>"

# A fictional but well formed absolute path, shown once so the reader has a
# shape to copy. Deliberately not a real path on any machine: a placeholder that
# looks like a real install is a placeholder somebody runs by accident.
ABSOLUTE_EXAMPLE = "/Users/you/code/brothermode"

# Section headings that other code and the suite both key on, so a rename
# cannot silently detach a guard from the section it guards.
FIRST_RUN_HEADING = "## The first command in a new project"
REQUIREMENTS_HEADING = ("## What this runtime needs before any BrotherMode "
                        "command works")


# ---------------------------------------------------------------------------
# The BrotherMode surface that works in ANY runtime with a shell.
#
# THE COMMAND LISTS ARE READ OUT OF THE TOOLS, NEVER TYPED HERE. They used to be
# hand maintained tuples and they drifted, exactly as a hand maintained list
# always does: on 2026-08-05 the shipped Codex adapter named 11 bm_store
# commands where the tool dispatches 13, 7 bm_learn commands where the tool
# dispatches 40, and did not mention bm_project.py at all, which hid the whole
# beginner lifecycle (start, status, next, task, forecast, review) from every
# reader of that file. A generated list cannot drift: the moment a tool grows a
# command, the committed adapters stop matching the render and `check` fails.
#
# FLAGS ARE DELIBERATELY ABSENT. The generated files tell the agent to run the
# command with --help and read the real flags off the tool. That is one extra
# step for the agent and it removes the entire class of "the instruction file
# taught the agent a flag that was never there".
# ---------------------------------------------------------------------------
_CLI_MODULES = (
    ("bm_store.py",
     "the transactional store: ownership, fences, handover digests"),
    ("bm_project.py",
     "the project lifecycle: start a project, see status, be told what is next, "
     "move tasks, forecast, review"),
    ("bm_threads.py",
     "thread mode: one persistent thread per feature, one chief coordinating"),
    ("bm_learn.py",
     "correction learning: capture, approval, and retrieval of founder rules"),
    ("bm_telemetry.py",
     "session telemetry and the nine metric scorecard"),
)

# The module level names a BrotherMode tool uses for its dispatch table.
_DISPATCH_NAMES = ("COMMANDS", "_COMMANDS")
# The local variable a tool compares against when it dispatches with an
# if/elif chain instead of a dict. bm_telemetry.py is that shape.
_DISPATCH_VARS = ("cmd", "command", "subcommand")


def _str_const(node):
    """The string a literal node carries, or None. One helper so the 3.9
    spelling of a string literal lives in exactly one place."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dispatch_dict(tree):
    """The module level dispatch dict node, or None."""
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in _DISPATCH_NAMES:
                return node.value
    return None


def discover_commands(path):
    """Every command the module at `path` actually dispatches, sorted.

    Read from the SOURCE with ast: no import, no execution, no subprocess, so
    discovering a tool's surface can never run a tool's side effects. Two real
    shapes are supported because both exist in this repository:

      1. a module level COMMANDS or _COMMANDS dict (bm_store, bm_project,
         bm_threads, bm_learn)
      2. an if/elif chain on a dispatch variable inside main() (bm_telemetry,
         which has no dispatch dict at all and prints no command list of its
         own, so nothing else could have kept it honest)

    Raises ValueError when neither shape is found, rather than returning an
    empty tuple that would render an adapter with a command list of nothing."""
    try:
        src = io.open(path, encoding="utf-8").read()
    except OSError as e:
        raise ValueError("cannot read %s: %s" % (path, e))
    tree = ast.parse(src, filename=path)

    node = _dispatch_dict(tree)
    if node is not None:
        names = set()
        for key in node.keys:
            s = _str_const(key)
            if s:
                names.add(s)
        if names:
            return tuple(sorted(names))

    names = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name != "main":
            continue
        for cmp_node in ast.walk(fn):
            if not isinstance(cmp_node, ast.Compare):
                continue
            if not isinstance(cmp_node.left, ast.Name):
                continue
            if cmp_node.left.id not in _DISPATCH_VARS:
                continue
            if not all(isinstance(op, ast.Eq) for op in cmp_node.ops):
                continue
            for comparator in cmp_node.comparators:
                s = _str_const(comparator)
                if s:
                    names.add(s)
    if names:
        return tuple(sorted(names))
    raise ValueError(
        "no dispatch table found in %s: expected a module level %s dict or an "
        "if/elif chain on %s inside main()"
        % (path, " or ".join(_DISPATCH_NAMES), " or ".join(_DISPATCH_VARS)))


def discover_deprecated(path):
    """The subset of discovered commands whose handler declares itself
    DEPRECATED in its own docstring.

    The adapters mark those rather than dropping them, because a command that
    still exists and still runs should still be findable; what must not happen
    is an instruction file teaching a deprecated command as an equal. Lane D
    found exactly that failure surviving a version update on 2026-08-05:
    `bm_learn.py relevant` exits 2 with a DEPRECATED banner.

    KNOWN LIMIT, stated rather than hidden: this reads the DOCSTRING only. A
    command deprecated without saying so in its docstring is not detected."""
    try:
        src = io.open(path, encoding="utf-8").read()
    except OSError:
        return frozenset()
    tree = ast.parse(src, filename=path)
    node = _dispatch_dict(tree)
    if node is None:
        return frozenset()
    handlers = {}
    for key, value in zip(node.keys, node.values):
        s = _str_const(key)
        if s and isinstance(value, ast.Name):
            handlers[value.id] = s
    out = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name in handlers:
            doc = ast.get_docstring(fn) or ""
            if "DEPRECATED" in doc:
                out.add(handlers[fn.name])
    return frozenset(out)


def _build_cli_surface():
    """(module, purpose, commands) for every tool the adapters teach, with the
    commands discovered rather than typed. Built once at import so every
    renderer and every test sees the same surface, and so rendering parses no
    Python at all: bm_store.py alone is 16000 lines, and rendering happens once
    per runtime per emit and many times over in the suite."""
    surface = []
    for mod, purpose in _CLI_MODULES:
        surface.append((mod, purpose, discover_commands(os.path.join(HERE, mod))))
    return tuple(surface)


CLI_SURFACE = _build_cli_surface()

# Same reason: discovered once, at import.
DEPRECATED_BY_MODULE = dict(
    (mod, discover_deprecated(os.path.join(HERE, mod)))
    for mod, _purpose in _CLI_MODULES)


# ---------------------------------------------------------------------------
# THE RUNTIME REGISTRY
#
# Fields:
#   key            the name the CLI takes
#   title          human name, used in headings
#   staging_name   the file this generator writes into the staging directory.
#                  Deliberately prefixed by runtime key: two runtimes can share
#                  a DESTINATION (several read AGENTS.md) and staging must not
#                  collide.
#   install_file   the bare path the founder installs to, for the one line
#                  capability table cell. It exists as its OWN field because the
#                  first version derived it by splitting destinations[0] on ".",
#                  which truncated at the dot inside the filename: Copilot's cell
#                  rendered EMPTY and two others lost their extension. A prose
#                  sentence is not a path and must never be parsed as one.
#                  _check_registry ties it back to the verified prose so it
#                  cannot drift into an invented path.
#   destinations   where the founder copies the file to, verbatim from the
#                  vendor page named in `sources`. Prose, possibly several
#                  sentences: rendered whole, never sliced.
#   verified       True only when a vendor page was opened and the destination
#                  read off it. False renders a GENERIC banner plus `reason`.
#   reason         why it could not be verified. Required when verified is False.
#   sources        ((label, url), ...) pages actually opened on VERIFIED_ON
#   hooks          None when no hook mechanism was found on the pages opened,
#                  else dict(config, events, source)
#   notes          honest caveats specific to this runtime
# ---------------------------------------------------------------------------
RUNTIMES = (
    {
        "key": "generic",
        "title": "Generic AGENTS.md",
        "staging_name": "generic.AGENTS.md",
        "install_file": "AGENTS.md",
        "destinations": ("AGENTS.md at the repository root. In a monorepo, a "
                         "nested AGENTS.md may sit in each subproject and the "
                         "closest one to the edited file wins.",),
        "verified": True,
        "reason": "",
        "sources": (("AGENTS.md, the open format", "https://agents.md"),),
        "hooks": None,
        "notes": (
            "This is the widest reach for the least work. The format page lists "
            "OpenAI Codex, GitHub Copilot, Gemini CLI, Cursor, VS Code, Zed, "
            "Aider, goose, Devin, Windsurf and JetBrains Junie among the tools "
            "that read it, so one file covers runtimes this registry does not "
            "name individually.",
            "AGENTS.md is a plain instruction file. It carries no hook "
            "mechanism of its own, in any runtime.",
        ),
    },
    {
        "key": "codex",
        "title": "OpenAI Codex CLI",
        "staging_name": "codex.AGENTS.md",
        "install_file": "AGENTS.md",
        "destinations": (
            "AGENTS.md, starting at the project root (typically the git root) "
            "and in any directory down to the working directory.",
            "AGENTS.override.md takes priority over AGENTS.md in the same "
            "directory.",
            "Global scope: the Codex home directory, which defaults to "
            "~/.codex and follows CODEX_HOME when that is set.",
        ),
        "verified": True,
        "reason": "",
        "sources": ((
            "Custom instructions with AGENTS.md",
            "https://learn.chatgpt.com/docs/agent-configuration/agents-md"),),
        "hooks": {
            "config": ("~/.codex/hooks.json or an inline [hooks] table in "
                       "~/.codex/config.toml for user scope; "
                       "<repo>/.codex/hooks.json or an inline [hooks] table in "
                       "<repo>/.codex/config.toml for project scope."),
            "events": ("SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
                       "PermissionRequest", "PreCompact", "PostCompact",
                       "UserPromptSubmit", "SubagentStart", "SubagentStop",
                       "Stop"),
            "source": ("Codex hooks", "https://learn.chatgpt.com/docs/hooks"),
        },
        # What this runtime needs before ANY BrotherMode command works. Both
        # were measured, and both were dead ends a first time user hit before
        # anything else could go right.
        "requirements": (
            "A WRITABLE SANDBOX. Codex runs model driven shell commands in a "
            "read-only sandbox by default, and BrotherMode's store has to "
            "CREATE `.brothermode/store.sqlite3`. On the default, `init` dies "
            "with `bm_store: unexpected error: PermissionError(1, 'Operation "
            "not permitted')`, and every command after it then reports no "
            "project root, which points you back at the command that just "
            "failed. Start Codex with `-s workspace-write`. That spelling was "
            "read off `codex --help` on codex-cli 0.146.0 on 2026-08-05, whose "
            "accepted values are read-only, workspace-write and "
            "danger-full-access. With that flag, everything below runs.",
            "YOUR OWN OpenAI CREDENTIALS. Codex keeps them per user, in the "
            "Codex home directory. A fresh CODEX_HOME answers `401 "
            "Unauthorized` and exits 1 before any of this matters. BrotherMode "
            "itself needs no account and no network; the runtime carrying it "
            "does.",
        ),
        # The capability table's CLI cell. "yes" on its own was true of the
        # code and false of the experience.
        "cli_note": ("yes, with two caveats measured on 2026-08-05: call the "
                     "tools by their absolute path in the checkout, and start "
                     "Codex with `-s workspace-write`"),
        # THE ONE RUNTIME WHERE SOMEBODY ACTUALLY RAN THIS. Everything here
        # comes from a live capture, and the answer is neither "yes" nor
        # "unknown". Both halves matter: the primitive exists, and the fence
        # does not transfer.
        "measured": {
            "captured_on": "2026-08-05",
            "runtime_version": "codex-cli 0.146.0",
            "cell": ("MEASURED 2026-08-05 on codex-cli 0.146.0, updated L06 "
                     "2026-08-06. Payloads are Claude shaped and a PreToolUse "
                     "deny really does block, so the primitive exists. As "
                     "measured, the one writer fence did not transfer: every "
                     "write arrives as tool_name Bash running apply_patch, "
                     "and the old Edit/Write matcher never fired. L06 widened "
                     "the matcher to Bash and bm_fence_hook.py now parses "
                     "apply_patch envelopes through the same fence path, "
                     "proven in process against the captured payload. THE "
                     "LIVE HALF IS NOW MEASURED, AND IT IS A NO: on "
                     "2026-08-07 a real codex exec run overwrote a file "
                     "another session had claimed, twice, and a marker probe "
                     "proved the PreToolUse hook never executed at all. "
                     "Config syntax (strict-config), project trust and "
                     "--dangerously-bypass-hook-trust were all ruled out "
                     "(docs/mistakes/M19-the-codex-fence-does-not-fire-in-exec-mode.md). So under Codex exec, BrotherMode "
                     "is the law in writing plus a working command line, and "
                     "NOT an enforcement layer: do not rely on the fence "
                     "here. SessionEnd is clamped to 3s."),
            "findings": (
                "THE PRIMITIVE EXISTS. Codex accepts Claude Code's hook "
                "configuration shape and Claude Code's output object. A hook "
                "returning permissionDecision deny, in BrotherMode's exact "
                "JSON shape, blocked the command live: `error=Command blocked "
                "by PreToolUse hook: BrotherMode fence: probe deny`. deny is "
                "the only permission decision Codex honours, and it is the "
                "only one BrotherMode ever emits.",
                "THE ONE WRITER FENCE DID NOT TRANSFER AS MEASURED, AND L06 "
                "CLOSED THE MATCHER HALF. Codex has no Edit, Write, MultiEdit "
                "or NotebookEdit tool. File writes arrive as tool_name Bash "
                "with an apply_patch heredoc inside tool_input.command, so "
                "the matcher shipped on 2026-08-05 never fired and "
                "bm_fence_hook.py exited 0 in silence. On 2026-08-06 (L06) "
                "the matcher widened to Bash and the hook now parses "
                "apply_patch envelopes, checking every named path through "
                "the same fence code the Edit tools use; the captured "
                "payload, fed through the real executable entry against a "
                "foreign fence, is denied. That proof is in process: no "
                "live wired Codex session has rehearsed the deny since the "
                "widening, so the end to end claim stays UNVERIFIED.",
                "HOOKS ARE SILENTLY INERT UNTIL THE PROJECT IS TRUSTED. With a "
                "valid hooks file and no trust record, no hook ran, no warning "
                "printed, and the command executed unguarded. How a trust is "
                "granted was never reached, so that flow stays UNVERIFIED.",
                "${CLAUDE_PLUGIN_ROOT} EXPANDS TO EMPTY. Codex runs hook "
                "commands through the shell, so every command in BrotherMode's "
                "shipped hooks file becomes a path starting at the filesystem "
                "root and fails. A hand installed hooks file needs absolute "
                "paths.",
                "SESSION TELEMETRY RECORDS NOTHING, SILENTLY. Codex's rollout "
                "JSONL is not Claude Code's transcript format, so "
                "outcomes-append parses zero messages and drops the session "
                "under its activity floor, exit 0. A session that looks "
                "recorded and is not.",
                "THE SessionEnd TIMEOUT IS CLAMPED TO 3 SECONDS whatever the "
                "configuration says. BrotherMode declares 30. It fits on an "
                "empty store (0.061s measured) and nobody has measured a "
                "loaded one.",
                "FIVE OF THE ELEVEN EVENTS were never observed firing: "
                "PermissionRequest, PreCompact, PostCompact, SubagentStart and "
                "SubagentStop. Their shapes are known from the binary's own "
                "embedded schemas; their live behaviour stays UNVERIFIED.",
            ),
            "evidence": (
                "Lane C of the 2026-08-05 Codex lifecycle rehearsal, which "
                "drove codex-cli 0.146.0 with a local stand-in model provider "
                "so Codex itself built every payload, and whose report is "
                "`BrotherModeUp-handovers/2026-08-05-codex-lifecycle/"
                "LANE-C-hooks.md`. No authenticated model turn was ever made, "
                "so whether a real model CHOOSES to obey the law is a "
                "different question and is still open."),
        },
        "notes": (
            "Codex builds its instruction chain once when it starts, so editing "
            "an AGENTS.md mid session does not reload it.",
            "The event NAMES overlap heavily with Claude Code's. The trap "
            "turned out to be subtler than expected: the names AND the payload "
            "fields AND the output contract all match, and the tool vocabulary "
            "underneath does not. See the measured findings in the hooks "
            "section above.",
            "This file arrives as a user message, below Codex's own much "
            "larger system prompt. It is context, not policy, which is another "
            "way of saying an instruction file is persuasion.",
        ),
    },
    {
        "key": "copilot",
        "title": "GitHub Copilot",
        "staging_name": "copilot.copilot-instructions.md",
        "install_file": ".github/copilot-instructions.md",
        "destinations": (
            ".github/copilot-instructions.md at the repository root, for "
            "repository wide instructions.",
            "Copilot also reads AGENTS.md files stored within the repository, "
            "nearest file first. Support for AGENTS.md outside the workspace "
            "root is off by default.",
            "Copilot CLI additionally reads $HOME/.copilot/copilot-instructions.md.",
        ),
        "verified": True,
        "reason": "",
        "sources": ((
            "Adding repository custom instructions in your IDE",
            "https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide"),
            ("Adding custom instructions for GitHub Copilot CLI",
             "https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions")),
        "hooks": None,
        "notes": (
            "Neither page opened describes any hook or lifecycle event "
            "mechanism. Instructions take effect on save and are added to "
            "requests; nothing runs an external program on an event.",
            "That makes Copilot a retrieval and CLI runtime for BrotherMode. "
            "The fence is advisory here: BrotherMode can tell Copilot the law "
            "and Copilot can run the store commands, but no PreToolUse gate "
            "stands in front of a write, so the one writer promise is a "
            "coordination ledger in this runtime, not a boundary.",
        ),
    },
    {
        "key": "antigravity",
        "title": "Google Antigravity",
        "staging_name": "antigravity.brothermode.md",
        "install_file": ".agents/rules/brothermode.md",
        "destinations": (
            "A markdown rules file inside the .agents/rules folder in the "
            "workspace or git root. Older installs use .agent/rules. The "
            "FOLDER is what the documentation specifies; the filename is "
            "yours, and brothermode.md is this generator's choice.",
            "Global rules live at ~/.gemini/GEMINI.md.",
        ),
        "verified": True,
        "reason": "",
        "sources": ((
            "Antigravity docs, Rules and Workflows",
            "https://antigravity.google/docs/rules-workflows"),),
        "hooks": {
            "config": ("hooks.json in the customization directory: .agents/ in "
                       "the workspace, or ~/.gemini/config/ globally."),
            "events": ("PreToolUse", "PostToolUse", "PreInvocation",
                       "PostInvocation", "Stop"),
            "source": ("Antigravity docs, Hooks",
                       "https://antigravity.google/docs/hooks"),
        },
        "notes": (
            "The rules page opened here does NOT mention AGENTS.md. Third "
            "party guides claim Antigravity reads it; that claim is "
            "UNVERIFIED and this generator does not act on it. Install the "
            "rules file at the documented path rather than relying on it.",
            "Third party guides also report a 12000 character cap per rules "
            "file. The official page opened does not state a limit, so the cap "
            "is UNVERIFIED. This generator keeps the Antigravity file short "
            "regardless, because a short file is correct either way.",
            "Antigravity enables auto continue by default in recent versions, "
            "which means long unattended chains. Explicit deny rules matter "
            "more here than in a runtime that pauses.",
        ),
    },
    {
        "key": "qwen",
        "title": "Qwen Code",
        "staging_name": "qwen.QWEN.md",
        "install_file": "QWEN.md",
        "destinations": (
            "QWEN.md at the project root, shared with the team through version "
            "control.",
            "~/.qwen/QWEN.md for instructions that follow you across projects.",
            ".qwen/QWEN.local.md for project specific personal instructions.",
            "Qwen also reads an existing AGENTS.md if the repository has one.",
        ),
        "verified": True,
        "reason": "",
        "sources": ((
            "Qwen Code docs, Memory",
            "https://qwenlm.github.io/qwen-code-docs/en/users/features/memory/"),),
        "hooks": {
            "config": ("a hooks object in Qwen Code settings, typically "
                       ".qwen/settings.json. disableAllHooks true at the top "
                       "level switches every hook off."),
            "events": ("SessionStart", "SessionEnd", "PreToolUse", "PostToolUse",
                       "PostToolUseFailure", "UserPromptSubmit", "PreCompact",
                       "PostCompact", "SubagentStart", "SubagentStop", "Stop",
                       "StopFailure", "Notification", "PermissionRequest",
                       "TodoCreated", "TodoCompleted"),
            "source": ("Qwen Code docs, Hooks",
                       "https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/"),
        },
        "notes": (
            "The context filename is configurable through the context.fileName "
            "setting, so a project that has changed it needs the file under "
            "that name instead.",
            "Qwen's hook event list is the closest match to Claude Code's of "
            "any runtime here, including SessionStart and SessionEnd, which are "
            "the two BrotherMode's telemetry actually targets. That makes it "
            "the most promising adapter target and still not a verified one.",
        ),
    },
    {
        "key": "iflow",
        "title": "iFlow CLI",
        "staging_name": "iflow.IFLOW.md",
        "install_file": "IFLOW.md",
        "destinations": (
            "IFLOW.md at the project root.",
            "~/.iflow/IFLOW.md for global scope.",
            "IFLOW.md inside a subdirectory, which takes priority over the "
            "project root file for work in that subdirectory.",
        ),
        "verified": True,
        "reason": "",
        "sources": ((
            "iFlow CLI configuration, Memory",
            "https://platform.iflow.cn/en/cli/configuration/iflow"),),
        "hooks": None,
        "notes": (
            "contextFileName is configurable and accepts a list, so a project "
            "can point iFlow at AGENTS.md instead of, or alongside, IFLOW.md.",
            "The page opened describes memory COMMANDS (/init, /memory show, "
            "/memory add, /memory refresh) and no hook mechanism. A slash "
            "command the founder types is not a hook point: nothing runs an "
            "external program on a tool call or at session end, so the "
            "autosave, the fence gate and the session telemetry have nowhere "
            "to attach in this runtime.",
        ),
    },
    {
        "key": "cursor",
        "title": "Cursor",
        "verified_on": "2026-08-07",
        "staging_name": "cursor.AGENTS.md",
        "install_file": "AGENTS.md",
        "destinations": (
            "AGENTS.md at the project root, which Cursor reads directly as an "
            "alternative to .cursor/rules, and also from subdirectories "
            "nearer the file being edited.",
            ".cursor/rules/brothermode.mdc, an .mdc file inside the "
            ".cursor/rules folder at the project root, if you prefer "
            "Cursor's own rules system instead. Cursor's docs say a plain "
            ".md file placed there is ignored because it carries no "
            "frontmatter; this generator writes plain markdown with none, so "
            "add frontmatter yourself, at minimum alwaysApply: true, before "
            "relying on a copy placed here. The AGENTS.md destination above "
            "needs no such extra step.",
            "The legacy single .cursorrules file at the project root still "
            "works, though Cursor's docs mark .cursor/rules as the current "
            "standard.",
        ),
        "verified": True,
        "reason": "",
        "sources": ((
            "Cursor Docs, Rules",
            "https://cursor.com/docs/context/rules"),),
        "hooks": {
            "config": ("hooks.json: ~/.cursor/hooks.json for user scope, "
                       "<project root>/.cursor/hooks.json for project scope. "
                       "Fixed enterprise scoped paths also exist per OS."),
            "events": ("sessionStart", "sessionEnd", "preToolUse",
                       "postToolUse", "postToolUseFailure", "subagentStart",
                       "subagentStop", "beforeShellExecution",
                       "afterShellExecution", "beforeMCPExecution",
                       "afterMCPExecution", "beforeReadFile", "afterFileEdit",
                       "beforeTabFileRead", "afterTabFileEdit",
                       "beforeSubmitPrompt", "preCompact", "stop",
                       "afterAgentResponse", "afterAgentThought",
                       "workspaceOpen"),
            "source": ("Cursor Docs, Hooks", "https://cursor.com/docs/hooks"),
        },
        "notes": (
            "Sources opened and read 2026-08-07, this entry's own "
            "verified_on date, which is what every line generated "
            "about it reports. The registry-wide date stays where "
            "it was: bumping it would have claimed the older entries "
            "were re-read today when they were not.",
            "Cursor's rules page names a global User Rules scope, described "
            "only as global to your Cursor environment, with no on-disk path "
            "stated. Third party sources claim ~/.cursor/rules as that path; "
            "that claim is UNVERIFIED against the vendor page opened here "
            "and this generator does not act on it.",
            "Checked on this machine (2026-08-07): ~/.cursor exists, holding "
            "agents, plugins, projects, extensions and a skills-cursor "
            "directory, but no rules directory and no hooks.json. Nothing "
            "BrotherMode would generate has been installed there yet.",
            "Event names here are lower camel case (preToolUse, "
            "postToolUse) rather than Claude Code's PascalCase (PreToolUse, "
            "PostToolUse), one more reason payload compatibility is "
            "unverified rather than assumed.",
            "Community reports, not the vendor page opened here, describe "
            "Cursor CLI builds accepting a narrower hooks.json shape than "
            "the IDE: a flat top level version 1 with direct command "
            "entries, rather than the matcher grouped shape Claude Code and "
            "Codex use. Named because it matters if this is ever wired by "
            "hand, not acted on because it was not confirmed on the page "
            "opened here.",
            "COMPAT MODE (2026-08-10): scripts/install_cursor.py installs "
            "BrotherMode under ~/.cursor/brothermode, wires a native "
            "hooks.json through tools/bm_cursor_hook.py, and "
            "tools/bm_cursor.py provides manage plus a local "
            "Fable-to-Cursor mailbox harness. Fence enforcement remains "
            "ADVISORY until a live Cursor Agent canary is recorded. See "
            "docs/CURSOR-COMPAT.md.",
        ),
    },
    {
        "key": "gemini",
        "title": "Gemini CLI",
        "verified_on": "2026-08-07",
        "staging_name": "gemini.GEMINI.md",
        "install_file": "GEMINI.md",
        "destinations": (
            "GEMINI.md at the project root, and in any subdirectory nearer "
            "the file being edited, all concatenated into Gemini CLI's "
            "hierarchical memory and sent to the model with every prompt.",
            "~/.gemini/GEMINI.md for global scope, loaded first and applied "
            "to every project.",
            "The context filename is configurable through the "
            "context.fileName setting in settings.json, which accepts a "
            "list of filenames, so a project can point Gemini CLI at "
            "AGENTS.md instead of, or alongside, GEMINI.md. GEMINI.md "
            "remains the default; Gemini CLI does not read AGENTS.md unless "
            "configured to.",
        ),
        "verified": True,
        "reason": "",
        "sources": (
            ("Gemini CLI docs, Provide context with GEMINI.md files",
             "https://geminicli.com/docs/cli/gemini-md/"),
            ("Gemini CLI docs, Configuration",
             "https://geminicli.com/docs/reference/configuration/"),
        ),
        "hooks": {
            "config": ("a hooks object inside settings.json: "
                       "~/.gemini/settings.json for user scope, "
                       "<project root>/.gemini/settings.json for project "
                       "scope."),
            "events": ("SessionStart", "SessionEnd", "BeforeAgent",
                       "AfterAgent", "BeforeModel", "BeforeToolSelection",
                       "AfterModel", "BeforeTool", "AfterTool", "PreCompress",
                       "Notification"),
            "source": ("Gemini CLI docs, Hooks reference",
                       "https://geminicli.com/docs/hooks/reference/"),
        },
        "notes": (
            "Sources opened and read 2026-08-07, this entry's own "
            "verified_on date, which is what every line generated "
            "about it reports. The registry-wide date stays where "
            "it was: bumping it would have claimed the older entries "
            "were re-read today when they were not.",
            "Event names overlap Claude Code's on concept, a before and an "
            "after hook around a tool call, but not on spelling: BeforeTool "
            "and AfterTool here against PreToolUse and PostToolUse in Claude "
            "Code, and PreCompress here where Claude Code has PreCompact and "
            "PostCompact. Nobody has run BrotherMode's hooks against this "
            "runtime and recorded the payload, so compatibility is "
            "UNVERIFIED, the same as every runtime in this registry except "
            "Claude Code.",
            "The @file.md import syntax lets one GEMINI.md pull in other "
            "files by relative or absolute path, a second place a founder's "
            "law can hide from a reader who only opens the top level file.",
            "Google Antigravity, the antigravity entry above, reads its own "
            "global rules from this same path, ~/.gemini/GEMINI.md, per its "
            "own docs. The two are separate products that happen to share a "
            "home directory and a global filename, not one reader with two "
            "names.",
            "Checked on this machine (2026-08-07): ~/.gemini exists but "
            "holds only Antigravity state (antigravity, antigravity-backup, "
            "antigravity-ide and config directories); no GEMINI.md and no "
            "settings.json for Gemini CLI itself were found there.",
        ),
    },
)


# ---------------------------------------------------------------------------
# Output funnel. Everything this module prints goes through these two, for the
# same reason bm_learn.py funnels its output: one place to audit.
# ---------------------------------------------------------------------------
def _out(msg=""):
    sys.stdout.write(msg + "\n")


def _err(msg):
    sys.stderr.write(msg + "\n")


def _load_bm_store():
    """Load bm_store.py by PATH, the same shape bm_telemetry.py and bm_learn.py
    use: this command runs from arbitrary working directories and must not
    depend on cwd being on sys.path. Only resolve_root is used, so a failure
    here degrades to an explicit refusal rather than a wrong root."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_runtimes", os.path.join(HERE, "bm_store.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:
        return None, repr(e)


def resolve_project_root(explicit=None):
    """Return (root, source) or (None, reason). Never guesses cwd silently."""
    if explicit:
        p = os.path.realpath(os.path.expanduser(explicit))
        if not os.path.isdir(p):
            return None, "no such directory: %s" % p
        return p, "explicit"
    mod, err = _load_bm_store()
    if mod is None:
        return None, ("could not load bm_store.py to resolve the project root "
                      "(%s); pass --root explicitly" % err)
    root, src = mod.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here. Run "
                      "bm_store.py init, set BROTHERMODE_ROOT, or pass --root")
    return root, src


def tools_reference(root):
    """How the generated files should spell the path to the tools directory.

    Relative when the tools live inside the project (the normal installed
    shape, and it survives the repository being moved or cloned elsewhere),
    absolute otherwise. Either way it is a REAL path computed here, not a
    placeholder the founder has to remember to substitute."""
    real_root = os.path.realpath(root)
    real_tools = os.path.realpath(HERE)
    if real_tools == real_root or real_tools.startswith(real_root + os.sep):
        return os.path.relpath(real_tools, real_root)
    return real_tools


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def command_root(tools_path):
    """How a generated file must spell the directory the tools live in.

    Absolute when emit ran outside the checkout, because then the rendered
    command is literally runnable and a placeholder would be a downgrade.
    Placeholder otherwise, because the alternative (a bare `tools/...`) is the
    exact instruction that failed with [Errno 2] in every project that is not
    the checkout itself."""
    p = tools_path.replace(os.sep, "/")
    if os.path.isabs(tools_path):
        return p
    return "%s/%s" % (CHECKOUT_TOKEN, p)


def _example_root(tools_path):
    """The directory used in the one worked absolute example."""
    if os.path.isabs(tools_path):
        return tools_path.replace(os.sep, "/")
    return "%s/tools" % ABSOLUTE_EXAMPLE


def _which_directory_block(tools_path):
    """The block that stops the highest damage defect this generator shipped.

    An agent reads the adapter and types what it says. It has to know that the
    path belongs to the BrotherMode checkout and not to the repository it is
    working in, and it has to recognize the error it gets when it forgets."""
    root = command_root(tools_path)
    L = []
    L.append("Two directories are involved, and confusing them is how a first "
             "run fails.")
    L.append("")
    L.append("- THE BROTHERMODE CHECKOUT is the directory BrotherMode itself "
             "was cloned into. It is the ONLY place these tools exist. If you "
             "followed the install page it is `~/.claude/skills/brothermode`, "
             "whatever runtime you use: that directory name is historical and "
             "does not mean Claude Code has to be installed or running.")
    L.append("- YOUR PROJECT is the repository you are actually working in. It "
             "has no `tools/` directory of its own, and it does not need one.")
    L.append("")
    if os.path.isabs(tools_path):
        L.append("The paths below are already absolute, because this file was "
                 "generated from outside the checkout. They are runnable as "
                 "printed.")
    else:
        L.append("Below, `%s` stands for that checkout directory. Substitute "
                 "the real path before running anything: it is the one thing "
                 "in this file only you know." % CHECKOUT_TOKEN)
    L.append("")
    L.append("Run the commands FROM your project, and call them BY the "
             "checkout's absolute path. The store finds your project by itself, "
             "from the working directory, so the only path you supply is the "
             "one to the tool.")
    L.append("")
    L.append("    WRONG in your own project:   python3 tools/bm_store.py dashboard")
    L.append("    what that actually gives you: can't open file "
             "'<your project>/tools/bm_store.py': [Errno 2] No such file or directory")
    L.append("")
    L.append("    RIGHT (substituting your own checkout path):")
    L.append("")
    L.append("        python3 %s/bm_store.py dashboard" % _example_root(tools_path))
    L.append("")
    L.append("So every command below reads `python3 %s/<tool> <command>`."
             % root)
    L.append("")
    return L


def _first_run_block(tools_path):
    """The session start advice used to be `dashboard` and `verify`, which both
    REFUSE on a project that has never been initialized. Measured 2026-08-05 in
    all three project shapes, exit 2 every time. `init` was named only inside a
    comma separated list of subcommands.

    EVERY COMMAND LINE IN THIS BLOCK MUST RUN EXACTLY AS PRINTED. The first
    version of this block ended with a bare `bm_project.py start`, which needs
    flags and exits 2 without them, so the ordered list this file gives closed
    on an instruction that fails on first use: the same defect the block above
    it exists to fix, one line further down. It was caught by review rather than
    by the suite, which is why
    test_bm_runtimes.py::TestTheFirstRunSequenceActuallyRuns now executes this
    list, in printed order, in a throwaway project.

    Naming `start`'s flags inline instead was not available: the adapters are
    gated by test_generated_files_teach_no_flag_names, which allows only --help
    and --runtime, on the grounds that a flag copied into an instruction file is
    a flag that goes stale. So the list ends with the command that makes the
    TOOL print its own flags, which cannot go stale."""
    root = command_root(tools_path)
    L = []
    L.append(FIRST_RUN_HEADING)
    L.append("")
    L.append("Run `init` FIRST. The dashboard and verify commands read the "
             "store, and a project that has never been initialized has no store "
             "to read, so they refuse rather than inventing one:")
    L.append("")
    L.append("    refused (no-store): no store exists at "
             "<project>/.brothermode/store.sqlite3; run `python3 "
             "%s/bm_store.py init` to create one" % root)
    L.append("")
    L.append("In a directory that is not a git repository the refusal is "
             "`refused (no-root): no BrotherMode project root found` instead. "
             "Both exit 2, and both were measured on 2026-08-05.")
    L.append("")
    L.append("So, in order, from inside your project:")
    L.append("")
    L.append("    python3 %s/bm_store.py init        (once per project)" % root)
    L.append("    python3 %s/bm_store.py dashboard   (what is owned right now)" % root)
    L.append("    python3 %s/bm_store.py verify      (is the store healthy)" % root)
    L.append("    python3 %s/bm_project.py start --help   (the flags `start` "
             "needs, printed by the tool itself)" % root)
    L.append("")
    L.append("EVERY line above runs exactly as printed, in that order, with "
             "nothing added and nothing substituted except the checkout path. "
             "That is a gate rather than a hope: BrotherMode's own suite "
             "extracts this list and runs it in a throwaway project, and fails "
             "if any line exits nonzero.")
    L.append("")
    L.append("The last line ends in `--help` on purpose. `start` takes required "
             "flags (a project id, a name, and who is acting), and typing "
             "`start` bare gets you its usage line and exit 2, which is the "
             "tool being careful rather than the tool being broken. This file "
             "names no flag but `--help`, because a flag copied into an "
             "instruction file is a flag that goes stale, and the tool's own "
             "output cannot. Read the flags off it, run `start` with them, then "
             "`next` tells you what to work on.")
    L.append("")
    L.append("One more refusal worth recognizing, because it is the other thing "
             "a first run hits: `refused (git-exposed-store)` means git does "
             "not ignore `.brothermode`, so the store could be committed in "
             "cleartext. The refusal names the exact line to add and the "
             "command to confirm it with. Do what it says and run `init` again.")
    L.append("")
    return L


def _requirements_block(rt):
    """Runtime specific preconditions. Rendered only when a runtime has them,
    so no adapter carries an empty section that reads like a missing one."""
    reqs = rt.get("requirements") or ()
    if not reqs:
        return []
    L = [REQUIREMENTS_HEADING, ""]
    for r in reqs:
        L.append("- %s" % r)
    L.append("")
    return L


def _measured_hook_block(rt):
    """The hook section for a runtime somebody actually captured.

    UNVERIFIED is the right word until a payload exists, and the wrong word
    afterwards in BOTH directions: it reads as "might work" when the measured
    answer is "does not", and it hides that the capture happened at all."""
    m = rt["measured"]
    L = []
    L.append("MEASURED %s, on %s. Somebody did run BrotherMode's hooks against "
             "this runtime and captured every payload. The answer is neither "
             "yes nor unknown, and both halves matter:"
             % (m["captured_on"], m["runtime_version"]))
    L.append("")
    for f in m["findings"]:
        L.append("- %s" % f)
    L.append("")
    L.append("BrotherMode's hook targets (tools/bm_fence_hook.py, and the "
             "telemetry hook subcommands) parse Claude Code's hook JSON "
             "contract, documented in docs/HOOKS.md. This runtime matches that "
             "contract on field names and on the decision object, and diverges "
             "on the tool vocabulary underneath it, which is enough to break "
             "the one thing BrotherMode most needs.")
    L.append("")
    L.append("SO: wiring here is possible since L06 (2026-08-06) and it is "
             "not yet rehearsed. The matcher half is built: the fence hook "
             "parses apply_patch envelopes out of Bash payloads and refuses a "
             "foreign-fenced path through the same code the Edit tools use, "
             "proven in process against the captured payload. What has not "
             "happened is one live wired Codex session showing that deny end "
             "to end, so treat the wiring as UNVERIFIED until you rehearse "
             "it: hooks file with ABSOLUTE paths, a trusted project, then one "
             "throwaway apply_patch against a fenced file must be blocked "
             "before you rely on it. Until your own rehearsal passes, treat "
             "the fence here as ADVISORY: the store still refuses an "
             "overlapping claim, so two sessions cannot both be recorded as "
             "owning a file. Claim before you edit anyway. The law is the "
             "point; the hook is the enforcement of it.")
    L.append("")
    L.append("Evidence: %s" % m["evidence"])
    L.append("")
    return L


def _hook_compat_block(rt):
    """The paragraph that keeps 'has hook points' apart from 'BrotherMode's
    hooks run here'. It is rendered for EVERY runtime, including the ones with
    no hooks at all, because the confusion is the same in both directions."""
    lines = []
    lines.append("## Hooks in this runtime")
    lines.append("")
    h = rt["hooks"]
    if h is None:
        lines.append("This runtime has NO hook mechanism on the documentation "
                     "opened for it. That is a capability statement, not a "
                     "complaint: it means the autosave, the pre write fence "
                     "gate and the session end telemetry have nowhere to "
                     "attach here, and BrotherMode in this runtime is the "
                     "instruction file plus the commands above.")
        lines.append("")
        lines.append("The fence is therefore ADVISORY here. The store still "
                     "refuses an overlapping claim, so two sessions cannot "
                     "both be recorded as owning a file. What is missing is "
                     "the gate that stands in front of an actual write, so an "
                     "agent that ignores the law can still edit an unclaimed "
                     "file. Do not describe the fence as enforced in this "
                     "runtime.")
    else:
        lines.append("This runtime HAS hook points.")
        lines.append("")
        lines.append("Configuration: %s" % h["config"])
        lines.append("")
        lines.append("Events: %s." % ", ".join(h["events"]))
        lines.append("")
        lines.append("Verified %s from %s <%s>." % (
            verified_on(rt), h["source"][0], h["source"][1]))
        lines.append("")
        if rt.get("measured"):
            lines.extend(_measured_hook_block(rt))
            return lines
        lines.append("WHAT THAT DOES NOT MEAN. BrotherMode's hook targets "
                     "(tools/bm_fence_hook.py, and the telemetry hook "
                     "subcommands) parse Claude Code's hook JSON contract, "
                     "documented in docs/HOOKS.md. A runtime that has an event "
                     "with the same NAME is not known to hand that event the "
                     "same JSON, or to read the same decision object back off "
                     "stdout. Nobody has run BrotherMode's hooks against this "
                     "runtime and recorded the payload, so compatibility is "
                     "UNVERIFIED. Wiring them blind risks a gate that fails "
                     "OPEN while looking installed, which is worse than no "
                     "gate, because a fence you believe in is the one you stop "
                     "double checking.")
        lines.append("")
        lines.append("If you want to close that: capture one real payload from "
                     "each event, compare it against docs/HOOKS.md, and write "
                     "the adapter. Until that evidence exists, run the "
                     "commands above by hand and treat the fence as advisory.")
    lines.append("")
    return lines


def render_runtime_file(rt, tools_path):
    """Render one runtime's instruction file. Pure: same inputs, same bytes."""
    L = []
    L.append("<!-- %s. Do not hand edit: regenerate instead. -->" % GENERATED_MARKER)
    L.append("<!-- Runtime: %s -->" % rt["title"])
    for label, url in rt["sources"]:
        L.append("<!-- Convention read %s from %s: %s -->" % (verified_on(rt), label, url))
    L.append("")
    L.append("# BrotherMode for %s" % rt["title"])
    L.append("")

    if not rt["verified"]:
        L.append("> STATUS: GENERIC, NOT VERIFIED.")
        L.append(">")
        L.append("> The instruction file convention for this runtime could not "
                 "be confirmed against vendor documentation. Reason: %s"
                 % rt["reason"])
        L.append(">")
        L.append("> Treat the destination below as a guess. Confirm it against "
                 "the runtime's own documentation before relying on this file, "
                 "and do not assume the runtime is reading it.")
        L.append("")

    L.append("## Where this file goes")
    L.append("")
    for d in rt["destinations"]:
        L.append("- %s" % d)
    L.append("")
    L.append("Sources opened on %s:" % verified_on(rt))
    L.append("")
    for label, url in rt["sources"]:
        L.append("- %s: <%s>" % (label, url))
    L.append("")

    L.append("## What BrotherMode is")
    L.append("")
    L.append("BrotherMode is a small set of Python tools that keep multi "
             "session and multi agent work honest: one transactional store "
             "records who owns which files, refuses an overlapping claim, and "
             "carries a handover digest so the next session starts from "
             "evidence rather than from memory. It also captures founder "
             "corrections and can retrieve the relevant ones before you act.")
    L.append("")
    L.append("It is standard library Python 3.9. No network, no service, no "
             "API key. Everything below is a local process.")
    L.append("")

    L.append("## The law")
    L.append("")
    L.append("1. ONE WRITER PER FILE. Before editing any file, claim it. If the "
             "store refuses because another record owns it, stop and report "
             "the conflict. Do not edit around a refusal.")
    L.append("2. REPRODUCE BEFORE FIXING. A defect gets a failing test, a hand "
             "run command, or an observed behaviour first. No blind patching.")
    L.append("3. VERIFY AFTER THE LAST EDIT. Any claim that something works "
             "names the command that proved it and quotes the output. A test "
             "that was never shown failing is not evidence.")
    L.append("4. HONEST LIMITS. Anything unverified, sampled, or dropped for "
             "time gets said out loud, not omitted.")
    L.append("5. NEVER PUSH without the founder asking. Commit locally, report "
             "the branch.")
    L.append("")

    L.extend(_requirements_block(rt))

    L.append("## Commands")
    L.append("")
    L.append("These are ordinary local processes, so they run in any runtime "
             "that can run a shell command. What is NOT runtime neutral is "
             "where they live.")
    L.append("")
    L.extend(_which_directory_block(tools_path))
    root = command_root(tools_path)
    for mod, purpose, cmds in CLI_SURFACE:
        deprecated = DEPRECATED_BY_MODULE.get(mod, frozenset())
        shown = ["%s (deprecated)" % c if c in deprecated else c for c in cmds]
        L.append("`python3 %s/%s <command>` : %s" % (root, mod, purpose))
        L.append("")
        L.append("    commands: %s" % ", ".join(shown))
        L.append("")
    L.append("Those lists are GENERATED from the tools themselves, so they "
             "cannot drift away from what the tools dispatch. Anything marked "
             "deprecated still runs today and will be removed; use what its "
             "own message points you at instead.")
    L.append("")
    L.append("FLAGS ARE NOT LISTED HERE ON PURPOSE. Run the command with "
             "`--help` and read the real flags off the tool. An instruction "
             "file that lists flags is an instruction file that teaches a flag "
             "that got renamed six months ago.")
    L.append("")
    L.append("Once the project is initialized, start every session by running "
             "the store's `dashboard` and `verify` to see what is owned and "
             "whether the store is healthy. Before acting on anything the "
             "founder has corrected you on before, run bm_learn.py `lookup` to "
             "read the rules without recording anything, or `apply` when the "
             "work is substantial enough that the retrieval itself should be "
             "recorded (it needs a work identity; run it with `--help` for the "
             "exact form).")
    L.append("")

    L.extend(_first_run_block(tools_path))

    L.extend(_hook_compat_block(rt))

    if rt["notes"]:
        L.append("## Notes specific to %s" % rt["title"])
        L.append("")
        for n in rt["notes"]:
            L.append("- %s" % n)
        L.append("")

    L.append("## Regenerating")
    L.append("")
    L.append("    python3 %s/bm_runtimes.py emit --runtime %s"
             % (command_root(tools_path), rt["key"]))
    L.append("")
    L.append("This file is generated. Edits to it are lost on the next "
             "regeneration. Change the registry in tools/bm_runtimes.py "
             "instead, and change it only with a vendor documentation URL in "
             "hand.")
    L.append("")
    return "\n".join(L)


def render_runtimes_doc(tools_path):
    """Render docs/RUNTIMES.md, the capability table.

    Generated from the SAME registry the files are generated from, and gated:
    test_bm_runtimes.py asserts the file on disk equals this render, so the
    table cannot drift away from what the generator actually emits. A hand
    maintained capability table is a table that lies within a month."""
    L = []
    L.append("<!-- %s. Do not hand edit: regenerate instead. -->" % GENERATED_MARKER)
    L.append("")
    L.append("# Running BrotherMode in other AI coding runtimes")
    L.append("")
    L.append("BrotherMode's engine is standard library Python driven from a "
             "shell. Nothing in it is specific to Claude Code. What is "
             "specific is the WIRING: which file a runtime reads to learn the "
             "law, and whether that runtime can run a program at a lifecycle "
             "event. This page states both, per runtime, with the vendor page "
             "each fact came from and the date it was read.")
    L.append("")
    L.append("Regenerate this page and every adapter file with:")
    L.append("")
    L.append("    python3 %s/bm_runtimes.py emit" % tools_path)
    L.append("")

    L.append("## The two questions, kept apart")
    L.append("")
    L.append("Almost every claim about cross runtime support blurs these, so "
             "they get separate columns:")
    L.append("")
    L.append("- **Hook points**: does the runtime run an external program at "
             "lifecycle events at all. Verified per runtime below.")
    L.append("- **BrotherMode hooks**: do BrotherMode's OWN hook targets work "
             "there. BrotherMode's hooks parse Claude Code's hook JSON "
             "contract (docs/HOOKS.md). A same named event in another runtime "
             "is not known to carry the same payload. ONE non Claude runtime "
             "has now been measured, OpenAI Codex CLI, and its answer is a "
             "measured NO rather than a yes (details in its section below). "
             "Every other non Claude answer in that column is UNVERIFIED, and "
             "UNVERIFIED means do not wire it: a fence that fails open while "
             "looking installed is worse than no fence.")
    L.append("")
    L.append("Retrieval and the store CLI need no such column, with two "
             "caveats that are true of the CODE being runtime neutral and "
             "false of the EXPERIENCE, which is the version a user meets. "
             "First, the tools live in the BrotherMode checkout, so a project "
             "that is not the checkout has to call them by absolute path: the "
             "bare relative form fails with `[Errno 2] No such file or "
             "directory`. Second, a runtime that sandboxes its shell read-only "
             "cannot create the store at all: under Codex's default sandbox "
             "`init` dies with `PermissionError(1, 'Operation not permitted')`. "
             "Both were measured on 2026-08-05. The per runtime sections say "
             "what each runtime needs.")
    L.append("")

    L.append("## Capability table")
    L.append("")
    L.append("| Runtime | Instruction file | Store and retrieval CLI | Hook points | BrotherMode hooks |")
    L.append("|---|---|---|---|---|")
    L.append("| Claude Code | CLAUDE.md and SKILL.md (native, see docs/SETUP.md) "
             "| yes | yes | YES, this is the one verified runtime |")
    for rt in RUNTIMES:
        # The registry's own field, NOT a slice of the destination prose. See
        # the install_file note in the registry header for why that matters.
        first_dest = "`%s`" % rt["install_file"]
        if not rt["verified"]:
            first_dest = "%s (GENERIC, unverified)" % first_dest
        if rt["hooks"] is None:
            hp = "none found"
            bh = "not applicable, no hook points"
        else:
            hp = "yes: %s" % ", ".join(rt["hooks"]["events"][:4])
            if len(rt["hooks"]["events"]) > 4:
                hp += ", and %d more" % (len(rt["hooks"]["events"]) - 4)
            bh = "UNVERIFIED, payload shape not captured"
        # A measured runtime replaces the UNVERIFIED cell with what was
        # actually observed, and the CLI cell stops saying a bare "yes" when
        # the tools need an absolute path and a writable sandbox to run.
        if rt.get("measured"):
            bh = rt["measured"]["cell"]
        cli = rt.get("cli_note") or "yes"
        L.append("| %s | %s | %s | %s | %s |"
                 % (rt["title"], first_dest, cli, hp, bh))
    L.append("")

    L.append("## Per runtime detail")
    L.append("")
    for rt in RUNTIMES:
        L.append("### %s" % rt["title"])
        L.append("")
        if not rt["verified"]:
            L.append("STATUS: GENERIC, NOT VERIFIED. %s" % rt["reason"])
            L.append("")
        L.append("Adapter file: `%s` (emit with `--runtime %s`)"
                 % (rt["staging_name"], rt["key"]))
        L.append("")
        L.append("Install to:")
        L.append("")
        for d in rt["destinations"]:
            L.append("- %s" % d)
        L.append("")
        L.append("Verified %s from:" % verified_on(rt))
        L.append("")
        for label, url in rt["sources"]:
            L.append("- %s: <%s>" % (label, url))
        if rt["hooks"] is not None:
            L.append("- %s: <%s>" % (rt["hooks"]["source"][0], rt["hooks"]["source"][1]))
        L.append("")
        if rt.get("requirements"):
            L.append("What this runtime needs before any BrotherMode command "
                     "works:")
            L.append("")
            for r in rt["requirements"]:
                L.append("- %s" % r)
            L.append("")
        if rt["hooks"] is None:
            L.append("Hook points: none found on the page(s) above. The fence "
                     "is advisory in this runtime.")
        else:
            L.append("Hook points: %s" % rt["hooks"]["config"])
            L.append("")
            L.append("Events: %s." % ", ".join(rt["hooks"]["events"]))
        L.append("")
        if rt.get("measured"):
            m = rt["measured"]
            L.append("BrotherMode's own hooks here: MEASURED %s on %s. The "
                     "payload was captured; the answer is a measured no, not "
                     "an unknown."
                     % (m["captured_on"], m["runtime_version"]))
            L.append("")
            for f in m["findings"]:
                L.append("- %s" % f)
            L.append("")
            L.append("Evidence: %s" % m["evidence"])
            L.append("")
        for n in rt["notes"]:
            L.append("- %s" % n)
        L.append("")

    L.append("## What is deliberately not here")
    L.append("")
    L.append("- No VERIFIED adapter for BrotherMode's hooks in any non Claude "
             "runtime. Cursor has an installable adapter "
             "(tools/bm_cursor_hook.py via scripts/install_cursor.py) whose "
             "payload shapes were read from Cursor's docs on 2026-08-10, but "
             "a live Agent canary has not been recorded, so enforcement stays "
             "ADVISORY (docs/CURSOR-COMPAT.md). Wiring any other runtime "
             "without a captured payload would be guessing at a safety "
             "boundary.")
    L.append("- No claim that installing an adapter file makes another runtime "
             "obey the law. An instruction file is persuasion. Only a pre "
             "write hook is enforcement, and only Claude Code has a verified "
             "one today.")
    L.append("- `bm_runtimes.py emit` still does not auto-install instruction "
             "files into a project. Cursor's separate lifecycle installer "
             "(scripts/install_cursor.py) writes ~/.cursor/hooks.json and "
             "optional project rules; it does not clobber a foreign AGENTS.md. "
             "AGENTS.md at a repository root usually already has content in "
             "it, and a generator that overwrote it would be a data loss bug "
             "wearing a convenience feature's clothes.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _write_text(path, text):
    """Write atomically: temp file beside the target, then os.replace. A reader
    (or a second emit) never sees a half written instruction file. THE ONLY
    place this module opens a file for writing."""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def doc_target(root, out_dir, explicit_out):
    """Where the capability table lives, for BOTH emit and check.

    It used to be hard coded to <root>/docs/RUNTIMES.md in emit and in check,
    which meant --out was a lie: the founder redirected staging to a scratch
    directory and a file OUTSIDE that directory got overwritten anyway. If the
    founder named a directory, everything this command writes goes inside it."""
    if explicit_out:
        return os.path.join(out_dir, "RUNTIMES.md")
    return os.path.join(root, "docs", "RUNTIMES.md")


def is_founder_owned(path):
    """True when a file exists at `path` and was NOT written by this generator.

    The marker line is the first line of everything this module emits. A file
    without it is a file somebody wrote by hand, and this generator will not
    overwrite it. Unreadable is treated as founder owned too: refusing costs a
    regenerate, guessing costs the file."""
    if not os.path.exists(path):
        return False
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return True
    return GENERATED_MARKER not in head


def _check_registry():
    """Structural refusals that must hold before anything is written. These are
    programming errors in the registry, not user errors, so they raise."""
    keys = [rt["key"] for rt in RUNTIMES]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate runtime key in the registry: %s" % keys)
    names = [rt["staging_name"] for rt in RUNTIMES]
    if len(set(names)) != len(names):
        raise ValueError("two runtimes emit the same staging filename: %s" % names)
    for rt in RUNTIMES:
        if not rt["sources"]:
            raise ValueError("%s has no source URL" % rt["key"])
        if not rt["verified"] and not rt["reason"]:
            raise ValueError("%s is unverified but states no reason" % rt["key"])
        if rt["verified"] and rt["reason"]:
            raise ValueError("%s is verified but carries an unverified reason"
                             % rt["key"])
        # install_file is what the capability table prints, so it is the field
        # most likely to be read and acted on without reading anything else. It
        # must be a path, and it must be grounded in the destination prose that
        # was read off the vendor page, not typed from memory.
        inst = rt.get("install_file") or ""
        if not inst.strip() or inst != inst.strip():
            raise ValueError("%s has no usable install_file: %r"
                             % (rt["key"], inst))
        if " " in inst:
            raise ValueError("%s install_file is prose, not a path: %r"
                             % (rt["key"], inst))
        dest0 = rt["destinations"][0]
        for part in (os.path.dirname(inst), os.path.basename(inst)):
            if part and part not in dest0:
                raise ValueError(
                    "%s install_file %r is not grounded in its verified "
                    "destination: %r does not appear in %r"
                    % (rt["key"], inst, part, dest0))
        # A capability table cell that contains a pipe silently eats the rest
        # of the row, so the guard is structural, not stylistic.
        for field in ("cli_note",):
            cell = rt.get(field) or ""
            if "|" in cell:
                raise ValueError("%s %s contains a pipe and would break the "
                                 "capability table: %r"
                                 % (rt["key"], field, cell))
        m = rt.get("measured")
        if m is not None:
            for field in ("captured_on", "runtime_version", "cell", "findings",
                          "evidence"):
                if not m.get(field):
                    raise ValueError("%s measured evidence has no %s"
                                     % (rt["key"], field))
            if "|" in m["cell"]:
                raise ValueError("%s measured cell contains a pipe and would "
                                 "break the capability table" % rt["key"])
            if rt["hooks"] is None:
                raise ValueError("%s carries measured hook evidence but no "
                                 "hook points" % rt["key"])
        for r in rt.get("requirements") or ():
            if not r.strip():
                raise ValueError("%s has an empty requirement" % rt["key"])


def by_key(key):
    for rt in RUNTIMES:
        if rt["key"] == key:
            return rt
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _parse(argv, known_flags):
    """Refuse unrecognized flags rather than silently treating them as values,
    the same gate bm_threads.py's start command carries. A typo that quietly
    becomes a positional argument is how an unfenced thread shipped once."""
    opts = {}
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            name = a[2:].split("=", 1)[0]
            if name not in known_flags:
                raise ValueError("unrecognized flag: --%s (known: %s)"
                                 % (name, ", ".join(sorted(known_flags))))
            if known_flags[name] == "bool":
                opts[name] = True
                i += 1
                continue
            if "=" in a:
                val = a.split("=", 1)[1]
                i += 1
            else:
                if i + 1 >= len(argv):
                    raise ValueError("flag --%s needs a value" % name)
                val = argv[i + 1]
                i += 2
            if known_flags[name] == "multi":
                opts.setdefault(name, []).append(val)
            else:
                opts[name] = val
            continue
        rest.append(a)
        i += 1
    return opts, rest


def cmd_list(argv):
    opts, rest = _parse(argv, {"json": "bool"})
    if rest:
        raise ValueError("list takes no positional arguments: %s" % rest)
    _check_registry()
    if opts.get("json"):
        rows = []
        for rt in RUNTIMES:
            rows.append({
                "key": rt["key"],
                "title": rt["title"],
                "staging_name": rt["staging_name"],
                "verified": rt["verified"],
                "reason": rt["reason"],
                "sources": [list(s) for s in rt["sources"]],
                "hook_points": rt["hooks"] is not None,
                "hook_events": list(rt["hooks"]["events"]) if rt["hooks"] else [],
                # brothermode_hooks stays inside its original two values on
                # purpose. It answers "is this verified to work", the answer is
                # still no everywhere but Claude Code, and a consumer keying on
                # it must keep reading "do not wire this". The MEASURED detail
                # is a separate field rather than a new value in that one,
                # because widening the value set would mean editing an existing
                # assertion in test_bm_runtimes.py, and a passing test is not
                # something this loop edits to make room for itself. See the
                # report in docs/program/absolute-lead/evidence for the exact
                # remedy proposed to whoever owns that decision.
                "brothermode_hooks": "unverified" if rt["hooks"] else "not applicable",
                "brothermode_hooks_measured": (
                    rt["measured"]["cell"] if rt.get("measured") else ""),
                "measured_on": (rt["measured"]["captured_on"]
                                if rt.get("measured") else ""),
                "measured_runtime_version": (
                    rt["measured"]["runtime_version"] if rt.get("measured") else ""),
                "requirements": list(rt.get("requirements") or ()),
            })
        _out(json.dumps({"verified_on": VERIFIED_ON, "runtimes": rows},
                        indent=2, sort_keys=True))
        return 0
    _out("BrotherMode runtime adapters (conventions read %s)" % VERIFIED_ON)
    _out("")
    for rt in RUNTIMES:
        mark = "" if rt["verified"] else "  [GENERIC, UNVERIFIED]"
        _out("  %-14s %s%s" % (rt["key"], rt["title"], mark))
        _out("      file: %s" % rt["staging_name"])
        if rt["hooks"] is None:
            _out("      hook points: none found. Fence is advisory here.")
        elif rt.get("measured"):
            _out("      hook points: yes (%d events). BrotherMode hooks "
                 "MEASURED %s on %s: the one writer fence does NOT transfer. "
                 "Do not wire them."
                 % (len(rt["hooks"]["events"]), rt["measured"]["captured_on"],
                    rt["measured"]["runtime_version"]))
        else:
            _out("      hook points: yes (%d events). BrotherMode hook "
                 "compatibility UNVERIFIED." % len(rt["hooks"]["events"]))
        if rt.get("requirements"):
            _out("      needs first: %d runtime requirement(s), see "
                 "docs/RUNTIMES.md" % len(rt["requirements"]))
        _out("      source: %s" % rt["sources"][0][1])
    _out("")
    _out("Store and retrieval commands work in ALL of these: they are ordinary "
         "local processes.")
    return 0


def cmd_emit(argv):
    opts, rest = _parse(argv, {
        "runtime": "multi", "out": "str", "root": "str",
        "dry-run": "bool", "no-doc": "bool",
    })
    if rest:
        raise ValueError("emit takes no positional arguments: %s" % rest)
    _check_registry()

    root, src = resolve_project_root(opts.get("root"))
    if root is None:
        _err("refused: %s" % src)
        return 2

    wanted = opts.get("runtime") or [rt["key"] for rt in RUNTIMES]
    chosen = []
    for k in wanted:
        rt = by_key(k)
        if rt is None:
            _err("refused: unknown runtime %r (known: %s)"
                 % (k, ", ".join(rt2["key"] for rt2 in RUNTIMES)))
            return 2
        chosen.append(rt)

    explicit_out = opts.get("out")
    out_dir = explicit_out or os.path.join(root, DEFAULT_STAGING)
    out_dir = os.path.realpath(os.path.expanduser(out_dir))
    tools_path = tools_reference(root)
    dry = bool(opts.get("dry-run"))

    _out("project root: %s (%s)" % (root, src))
    _out("staging dir:  %s%s" % (out_dir, "  [DRY RUN, nothing written]" if dry else ""))
    _out("")

    # Build the whole plan first, refuse on the whole plan, then write. A per
    # file check would leave a half emitted staging directory behind the moment
    # one target turned out to be founder owned.
    plan = []
    for rt in chosen:
        plan.append((rt, os.path.join(out_dir, rt["staging_name"]),
                     render_runtime_file(rt, tools_path)))

    doc_path = doc_target(root, out_dir, explicit_out)
    write_doc = not opts.get("no-doc") and len(chosen) == len(RUNTIMES)

    guarded = [p for _rt, p, _t in plan]
    if write_doc:
        guarded.append(doc_path)
    owned = [p for p in guarded if is_founder_owned(p)]
    if owned:
        _err("refused: %d target file(s) exist and were not written by this "
             "generator, so they are yours and nothing was written:"
             % len(owned))
        for p in owned:
            _err("  %s" % p)
        _err("Move or delete them, or point --out somewhere else. Every file "
             "this generator owns starts with the line: <!-- %s ... -->"
             % GENERATED_MARKER)
        return 2

    written = []
    for rt, path, text in plan:
        if not dry:
            _write_text(path, text)
        written.append((rt, path))

    if write_doc:
        if not dry:
            _write_text(doc_path, render_runtimes_doc(tools_path))
        _out("capability table: %s" % doc_path)
        _out("")
    elif not opts.get("no-doc"):
        _out("capability table NOT refreshed: it describes every runtime, so it "
             "is only rewritten on a full emit. Run without --runtime to "
             "refresh it.")
        _out("")

    _out("INSTALL MAP. Nothing was copied to the destinations below: these "
         "files are staged, so an existing AGENTS.md or rules file is never "
         "clobbered. The staging directory and the capability table above ARE "
         "written, and only ever over files this generator wrote before.")
    _out("")
    for rt, path in written:
        _out("  %s" % rt["title"])
        _out("    from: %s" % path)
        for d in rt["destinations"]:
            _out("    to:   %s" % d)
        if not rt["verified"]:
            _out("    NOTE: GENERIC and UNVERIFIED. %s" % rt["reason"])
        _out("")
    _out("Hook points are per runtime; BrotherMode's OWN hooks are verified in "
         "Claude Code only. See docs/RUNTIMES.md before wiring any hook.")
    return 0


def cmd_check(argv):
    """Is what is on disk what this generator would emit right now.

    This is the anti drift command, and it is what the suite calls: a generated
    file somebody hand edited, or a registry change nobody regenerated after,
    both show up here as a named stale file rather than as a quiet lie in a
    capability table."""
    opts, rest = _parse(argv, {"out": "str", "root": "str"})
    if rest:
        raise ValueError("check takes no positional arguments: %s" % rest)
    _check_registry()
    root, src = resolve_project_root(opts.get("root"))
    if root is None:
        _err("refused: %s" % src)
        return 2
    explicit_out = opts.get("out")
    out_dir = explicit_out or os.path.join(root, DEFAULT_STAGING)
    out_dir = os.path.realpath(os.path.expanduser(out_dir))
    tools_path = tools_reference(root)

    stale = []
    missing = []
    targets = [(os.path.join(out_dir, rt["staging_name"]),
                render_runtime_file(rt, tools_path)) for rt in RUNTIMES]
    targets.append((doc_target(root, out_dir, explicit_out),
                    render_runtimes_doc(tools_path)))
    for path, expected in targets:
        if not os.path.exists(path):
            missing.append(path)
            continue
        actual = io.open(path, encoding="utf-8").read()
        if actual != expected:
            stale.append(path)
    if missing:
        _out("MISSING (%d):" % len(missing))
        for p in missing:
            _out("  %s" % p)
    if stale:
        _out("STALE (%d), on disk differs from what the registry renders:" % len(stale))
        for p in stale:
            _out("  %s" % p)
    if missing or stale:
        _out("")
        _out("Regenerate: python3 %s/bm_runtimes.py emit" % tools_path)
        return 1
    # Names the path it actually compared rather than a hard coded
    # docs/RUNTIMES.md, which was wrong under --out.
    _out("check OK: %d adapter files and %s match the registry."
         % (len(RUNTIMES), doc_target(root, out_dir, explicit_out)))
    return 0


COMMANDS = {"list": cmd_list, "emit": cmd_emit, "check": cmd_check}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out((__doc__ or "").strip())
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0 if argv else 2
    fn = COMMANDS.get(argv[0])
    if fn is None:
        _err("unknown command: %s (commands: %s)"
             % (argv[0], ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return fn(argv[1:])
    except ValueError as e:
        _err("refused: %s" % e)
        return 2
    except OSError as e:
        _err("failed: %s" % e)
        return 1


def cli():
    """Console-script entry point for a packaged install (pipx, uv, pip).

    A packaging entry point must be callable with no arguments, and main()
    takes argv. The __main__ block below calls this same function rather
    than repeating the line, so `bm-runtimes` and
    `python3 tools/bm_runtimes.py` cannot drift apart: there is exactly one
    path into main() from a shell."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
