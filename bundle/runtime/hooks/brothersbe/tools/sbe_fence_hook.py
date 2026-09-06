#!/usr/bin/env python3
"""BrotherSBE PreToolUse fence hook: the point where L13, "one writer per file",
stops being a line in a markdown registry and becomes an enforcement boundary.

WHY THIS EXISTS
  L13 (references/laws-parallel-writers.md) says a fence line is written BEFORE
  the writer starts, names the exact files that writer may touch, and is closed
  only by appending its evidence. Until this file, nothing checked it at the
  moment of the write. `sbe_score.py` scores fence HYGIENE (is the line tagged,
  is the registry stale) and `sbe_telemetry.py fence-lint` prints live fences as
  a DISPATCH AID, but both of those run beside the work, never in front of it.
  L13 says so itself: "The rest of the fence discipline is human review, because
  nothing here computes it ... queueing rather than running in parallel when two
  writers overlap in file scope (no check compares scopes)". This file compares
  scopes, at the only moment where comparing them can still stop a collision.

  Claude Code's PreToolUse hook is the mechanism. It receives the tool name and
  its input as JSON on stdin and may return a deny decision that stops the call
  before the tool runs. The exact stdin fields this file reads and the exact deny
  object it emits are quoted in docs/HOOKS.md, which is the document to update if
  that contract moves.

THE THREE RULES THIS FILE OBEYS

  1. FAIL OPEN, LOUDLY. This hook sits in front of every edit the operator makes.
     A hook that failed closed on its own bug would brick editing entirely, so
     every failure path here (no registry configured, registry absent, registry
     unreadable, registry undecodable, a fence line with no readable file scope,
     an unimportable helper, an unparseable payload, no session_id, any
     unexpected exception) ALLOWS the write and prints the reason to stderr. A
     refusal from this file always means a real ownership conflict and never
     means this file is broken.

     This is a DELIBERATE DIVERGENCE from `sbe_score.check_fence_hygiene`, which
     FAILs over an unreadable registry because a broken record is not an absent
     one. That is right for a scorer, whose output is a verdict a human reads. It
     is wrong for a gate in front of the keyboard, whose output is a refusal that
     stops work. Same evidence, opposite safe direction, both stated.

     ONE NAMED EXCEPTION: Codex's apply_patch. Every other write tool names its
     target in a structured argument this hook either reads or fails open over.
     apply_patch's targets live only inside free-form patch text, parsed by this
     file itself (`apply_patch_targets`), so a patch that does not parse, or a
     parsed target that does not resolve inside the declared worktree, is the
     one shape where "could not tell what this touches" must not default to
     "allowed". Both are DENIED, not fail-open, and both are named as exactly
     that in the refusal so the reader is never told a fence conflict happened
     when the real reason was an unparseable or out-of-worktree patch.

  2. STDOUT IS THE DECISION CHANNEL AND NOTHING ELSE. Claude Code parses stdout
     as JSON. Every diagnostic goes to stderr. That is why this file has exactly
     two output funnels (_out, _warn) and no bare print anywhere.

  3. ONE PARSE, NEVER A SECOND COPY. The rule for what counts as a live fence,
     for stripping HTML comments, and for discovering registries behind a denied
     directory all come from the modules that already own them (`sbe_checks.py`,
     and the shape `sbe_score.py` and `sbe_telemetry.py` read). This file holds
     no private near-copy of any of them. When a shared helper cannot be
     imported, this hook FAILS OPEN and says so, rather than enforcing with a
     parse that might have drifted from the project's own. A second copy is how
     the fence the hook refuses over and the fence the operator wrote stop being
     the same fence, and that failure would be silent.

  Path handling follows from rule 3's spirit: every target path is realpath'd and
  expressed root-relative before comparison, because comparing unresolved strings
  is bypassed by '..', by a symlink, by a relative path typed from a
  subdirectory, or by case on a case-insensitive filesystem.

IDENTITY
  BrotherSBE's fence line names its writer in plain text: "(sole writer, session
  <id>)". That is not a weakness here and no token file is needed, because the
  session id this hook compares against is the one the HARNESS puts in the hook
  payload, not one the model types. A model cannot write its own session_id field
  into a PreToolUse payload, so reading the declared id out of STATE.md and
  claiming to be it buys nothing. The residual limit (a human who edits the
  registry can hand themselves any fence) is stated in docs/HOOKS.md, because a
  registry an operator owns is a registry an operator may rewrite, and that is
  the design, not a hole.

TWO REGISTRY SHAPES, ONE OVERLAP RULE
  This hook reads BOTH of BrotherSBE's fence registries, not because there are
  two competing formats, but because there are two different WRITERS. A human
  or an orchestrator hand-writes a fence line into STATE.md, and `sbe task
  open` (`src/brothersbe/tasks.py`) writes a structured record into
  `.sbe/tasks.json`. Both name a sole writer and a set of owned paths, and
  until this build only the first one was read: an `sbe task open` fence was
  invisible here, so L13 never actually bound the registry BrotherSBE's own
  CLI produces. The fix adds a SECOND reader beside the first one, never a
  replacement, so a project that only ever hand-writes STATE.md keeps working
  exactly as before, unchanged, if `src/brothersbe/tasks.py` cannot be found
  or imported at all (a FAIL-OPEN for that one source, named on stderr, per
  rule 1; the markdown source is unaffected and still enforces).

  `.sbe/tasks.json` is read through `src/brothersbe/tasks.py`'s own
  `load_registry` and `open_tasks`, imported by path exactly the way
  `tools/sbe_authority_hook.py` (LT-402) already imports it, rather than a
  private re-parse of the JSON shape: rule 3 again, one reader of one format.

  IDENTITY DIVERGES BETWEEN THE TWO SHAPES, AND IT IS DELIBERATE. A markdown
  fence with no declared session is, per the IDENTITY section above, "still a
  fence somebody opened" and refuses. A `.sbe/tasks.json` record can carry no
  `session` field at all for an ordinary, honest reason: every registry this
  project has ever written before this build predates the field, and
  `src/brothersbe/tasks.py` only records one when `CLAUDE_SESSION_ID` or
  `SBE_SESSION_ID` was set in the environment `sbe task open` ran in. Treating
  that absence as "somebody else, refuse" would turn every pre-existing open
  task, and every `sbe task open` run without either variable set, into a
  refusal nobody asked for, on the very release that was supposed to make
  refusals MEAN something. So for a `.sbe/tasks.json` record specifically, an
  absent session is not a match for anybody and not a conflict with anybody:
  it is treated exactly like the "a live fence declares no readable `files:`
  scope" case already in this file, a FAIL-OPEN named on stderr, because this
  hook cannot prove a conflict and does not invent one to deny with. Only a
  task record that DOES carry a session, and whose session is not this one,
  produces a genuine refusal. The markdown reader is untouched by any of this.

Python 3.9, standard library only, cross-platform, no network, no subprocess.
No em or en dashes anywhere in this file, its comments, or its output.
"""
import fnmatch
import json
import os
import posixpath
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Output funnels. Two of them, on purpose: stdout carries the decision JSON and
# nothing else, stderr carries every diagnostic. Anything written to stdout that
# is not the decision object corrupts the hook protocol. Deliberately not
# `print`: the honesty meta-test's report-print lint exists because interpolated
# print sites are a channel a value can climb through, and this file has no need
# of one.
# ---------------------------------------------------------------------------

def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


class OpenFail(Exception):
    """Raised anywhere a decision cannot be made SAFELY.

    Always caught at the top of decide(); always produces an ALLOW plus a stderr
    line naming the reason. A named exception rather than a returned sentinel, so
    a new code path cannot forget to check the sentinel and accidentally deny."""


# ---------------------------------------------------------------------------
# Shared helpers, loaded by path.
#
# tools/ is not a package and the hook is invoked by Claude Code with an
# arbitrary cwd, so a plain `import sbe_checks` would resolve against sys.path
# and could pick up a different checkout. Deferred into a function so an import
# failure is a FAIL-OPEN printed to stderr rather than a traceback that Claude
# Code would surface as a broken hook in front of every edit.
# ---------------------------------------------------------------------------

_CHECKS = None
_CHECKS_ERROR = None


_RS = None
_RS_ERROR = None


_BP = None
_BP_ERROR = None


def load_brother_paths():
    """Import tools/brother_paths.py beside this file, or return None and
    record why. Never raises: the same fail-open contract the two loaders
    below use, so an install missing the C3 helper degrades to the pre-C3
    literal ~/.claude rather than breaking every edit."""
    global _BP, _BP_ERROR
    if _BP is not None or _BP_ERROR is not None:
        return _BP
    try:
        import importlib.util
        path = os.path.join(HERE, "brother_paths.py")
        spec = importlib.util.spec_from_file_location("brother_paths", path)
        if spec is None or spec.loader is None:
            _BP_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("brother_paths", mod)
        _BP = mod
        return _BP
    except Exception as e:
        _BP_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


def load_repo_scope_module():
    """Import tools/sbe_repo_scope.py beside this file, or return None and
    record why. Never raises: E76 per-repository hook scoping degrades to
    active (default) when this returns None, the same fail-open contract
    load_checks_module uses right below."""
    global _RS, _RS_ERROR
    if _RS is not None or _RS_ERROR is not None:
        return _RS
    try:
        import importlib.util
        path = os.path.join(HERE, "sbe_repo_scope.py")
        spec = importlib.util.spec_from_file_location("sbe_repo_scope", path)
        if spec is None or spec.loader is None:
            _RS_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("sbe_repo_scope", mod)
        _RS = mod
        return _RS
    except Exception as e:
        _RS_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


def load_checks_module():
    """Import tools/sbe_checks.py beside this file, or return None and record
    why. Never raises: an unimportable helper is a fail-open condition, handled
    by require_checks_module() at the one place that needs it."""
    global _CHECKS, _CHECKS_ERROR
    if _CHECKS is not None or _CHECKS_ERROR is not None:
        return _CHECKS
    try:
        import importlib.util
        path = os.path.join(HERE, "sbe_checks.py")
        spec = importlib.util.spec_from_file_location("sbe_checks", path)
        if spec is None or spec.loader is None:
            _CHECKS_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("sbe_checks", mod)
        _CHECKS = mod
        return _CHECKS
    except Exception as e:
        # Blanket by design: any import failure at all must degrade to fail-open,
        # and the reason travels with it rather than being swallowed.
        _CHECKS_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


def require_checks_module():
    """The shared helpers, or OpenFail naming why they are missing.

    There is no local substitute on purpose (rule 3). Enforcing a fence with a
    private copy of the project's comment-stripping and glob-discovery rules
    would mean the hook could refuse over a fence shape the rest of BrotherSBE
    has stopped recognizing, and nothing would say so. Refusing to enforce, out
    loud, is the honest failure."""
    mod = load_checks_module()
    if mod is None:
        raise OpenFail(
            "tools/sbe_checks.py could not be imported (%s), and this hook holds "
            "no private copy of the project's registry-reading rules on purpose, "
            "so it cannot tell which fences exist" % _CHECKS_ERROR)
    return mod


#: `src/brothersbe/tasks.py`, the reader of the SECOND registry shape (see the
#: module docstring's "TWO REGISTRY SHAPES" section): `load_registry`,
#: `open_tasks`, `registry_path`, `RegistryUnusable`. Loaded by path, deferred,
#: and cached exactly like `load_checks_module` above, and for the identical
#: reason: `src/` is not on this process's import path by default, and a bare
#: `import` would risk resolving against a different checkout entirely.
#:
#: UNLIKE `require_checks_module`, there is no "require" counterpart that
#: raises OpenFail: an unimportable tasks.py degrades this hook to reading
#: ONLY the markdown STATE.md registry, named on stderr, rather than failing
#: open on EVERY registry (including the one that was working fine). A
#: project that has never touched `sbe task open` at all must not lose its
#: STATE.md enforcement because a sibling file it never uses is missing.
_TASKS = None
_TASKS_ERROR = None


def load_tasks_module():
    """`src/brothersbe/tasks.py`, or None with `_TASKS_ERROR` set. Never
    raises. Mirrors `tools/sbe_authority_hook.py::load_tasks_module` (LT-402),
    which imports this exact file by this exact path already; the two hooks
    read the SAME sibling module rather than each holding their own copy of
    how to find it."""
    global _TASKS, _TASKS_ERROR
    if _TASKS is not None or _TASKS_ERROR is not None:
        return _TASKS
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(HERE), "src", "brothersbe", "tasks.py")
        spec = importlib.util.spec_from_file_location(
            "brothersbe_tasks_for_fence_hook", path)
        if spec is None or spec.loader is None:
            _TASKS_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("brothersbe_tasks_for_fence_hook", mod)
        _TASKS = mod
        return _TASKS
    except Exception as e:
        # Blanket by design, matching load_checks_module: any import failure
        # degrades to "this one source is unavailable", named on stderr by
        # the one caller (read_fences) that checks for it.
        _TASKS_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# The tool surface this hook governs.
# ---------------------------------------------------------------------------

#: Tools that write a file through a structured, parseable path argument.
#:
#: Bash is DELIBERATELY ABSENT. A shell command can write any file, and no
#: reliable parse of arbitrary shell exists, so pretending to gate it would be a
#: guarantee this file cannot keep. It is stated as a known gap in docs/HOOKS.md
#: rather than papered over here. The skill's own sentence about this hook says
#: it does not gate Bash, and that sentence stays true because of this line.
#:
#: apply_patch IS PRESENT, unlike Bash, because it carries a reliably parseable
#: grammar (Codex's own patch format: `*** Begin Patch` / `*** Add File:` /
#: `*** Update File:` / `*** Delete File:` / `*** Move to:` / `*** End Patch`),
#: not arbitrary shell. Its targets do not live in any PATH_KEYS field, so
#: decide() reads and parses `tool_input.command` for this one tool through
#: `apply_patch_targets` instead of `extract_targets`, and treats a patch this
#: hook cannot fully read as a DENY rather than the fail-open every other
#: failure path in this file uses: a write tool whose targets are free-form
#: text is the one shape where "could not tell" must not default to "allowed".
WRITE_TOOLS = frozenset((
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "CreateDirectory",
    "Delete",
    "apply_patch",
))

#: Keys inside tool_input that carry a filesystem path, collected across the
#: built-in write tools. Unknown keys are ignored; a write tool that carries none
#: of these produces zero targets, which is a FAIL-OPEN (see decide) and never a
#: silent allow.
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath", "target_file")

#: The environment variable BrotherSBE already uses to name its fence registries
#: (tools/sbe_score.py, tools/sbe_telemetry.py fence-lint, docs/SETUP.md).
#: Colon-separated glob patterns. One name, read the same way in all three.
REGISTRIES_ENV = "BROTHERSBE_REGISTRIES"

#: The per-project registry every BrotherSBE project carries, named in SKILL.md
#: step 5 and shipped as STATE.template.md.
PROJECT_REGISTRY = "STATE.md"

#: Escape hatch for a session that has deliberately decided to write across a
#: fence and does not want to edit the registry first. Set it and the hook says
#: so on stderr on every write, so the bypass is never silent.
DISABLE_ENV = "BROTHERSBE_FENCE_HOOK_OFF"

#: Overrides the session identity this hook compares against, for a manual run
#: or a test. Never invents one: an invented id would own nothing and would deny
#: the operator out of their own work.
SESSION_ENV = "BROTHERSBE_FENCE_SESSION"

#: The shortest declared session token this hook will treat as an identity. A
#: one or two character token would prefix-match half the UUIDs on the machine
#: and hand a fence to the wrong session.
MIN_SESSION_TOKEN = 4


# ---------------------------------------------------------------------------
# COMPANION DEFERENCE.
#
# docs/adr/2026-08-12-where-the-shared-machinery-lives.md, ACCEPTED, decided
# that one-writer-per-file is the companion's job, not this file's, because a
# write collision is a property of concurrent sessions rather than of a
# change's assurance. This project's tool keeps deciding when the companion
# cannot be found, and steps back, out loud, the moment it can.
#
# THE SIGNAL. This file reads exactly the files Claude Code itself reads to
# decide which hooks run this session: the global settings.json and
# settings.local.json under CLAUDE_CONFIG_DIR (default ~/.claude), the
# project's own .claude/settings.json and .claude/settings.local.json, and,
# for a companion installed as a plugin rather than pasted by hand, the
# enabled plugin's own cached hooks/hooks.json, resolved through the
# harness's own installed_plugins.json. That is the same pair of install
# paths docs/INTEROPERABILITY.md guarantee 4 already documents for THIS
# hook's own installation, so the check mirrors a boundary this project
# already treats as authoritative rather than inventing a new one. No other
# channel exists: CLAUDE_PLUGIN_ROOT names only the CALLING plugin's own
# root, never a sibling plugin's, so a settings/plugin-registry read is the
# only honest way to ask "would the harness actually invoke the companion's
# fence hook for this session."
#
# WHAT IT MISSES, NAMED RATHER THAN HIDDEN: a companion wired through a
# project settings file outside cwd/project_dir; a config directory this
# process cannot find because CLAUDE_CONFIG_DIR was not propagated into the
# hook's own environment; a companion fork that renamed its fence hook script
# away from COMPANION_HOOK_BASENAME; a plugin installed by a path that never
# wrote an installed_plugins.json record. Every one of those degrades to
# ABSENT (this hook keeps deciding, exactly as it does standalone), never to
# a guess that the companion is present.
#
# THE SAFETY RULE: "could not tell" is its own state, NO_DATA, and it is
# handled exactly like ABSENT (this hook keeps deciding) rather than like
# PRESENT. The only difference from a plain ABSENT is that NO_DATA is a real
# anomaly (a settings file that exists and will not parse) worth a stderr
# line, while an ordinary ABSENT is silent, exactly as this hook is silent
# today for every session that never installed the companion at all.
# ---------------------------------------------------------------------------

#: The exact basename of the companion's PreToolUse fence hook script, named
#: in the ratified decision this section implements: "tools/sbe_fence_hook.py
#: here, and bm_fence_hook.py there". This is the one fact about the
#: companion this file is allowed to know, and it is quoted, not invented.
COMPANION_HOOK_BASENAME = "bm_fence_hook.py"

#: Where Claude Code keeps user-level configuration, overridable for a
#: non-default install and for tests that must never depend on the real
#: machine's ~/.claude.
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"


class CompanionSignal(object):
    """Whether the companion's own fence hook is wired into THIS session for
    PreToolUse, and the evidence for the answer.

    Three states, not two: "I looked and found nothing" (ABSENT) and "I could
    not tell" (NO_DATA) are different claims, and rule 3 of this section
    forbids conflating them, because NO_DATA must never default to PRESENT.
    Only PRESENT changes this hook's behavior; ABSENT and NO_DATA both leave
    it deciding exactly as it does with nothing installed."""
    PRESENT = "present"
    ABSENT = "absent"
    NO_DATA = "no-data"

    def __init__(self, status, detail):
        self.status = status
        self.detail = detail


def _dedupe(items):
    """`items` in order, first occurrence only. Small and local rather than a
    dependency, matching registry_patterns' own inline dedupe just below."""
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _read_json_file(path):
    """(doc, existed, ok) for a JSON file at `path`.

    `existed` is False for an ordinary missing path, never an error. `ok` is
    False only when the path exists and could not be opened or decoded as
    JSON: that is the one shape this section treats as NO-DATA rather than
    ABSENT. A file that was never written and a file that exists but will not
    parse are different claims about the world, and this function is the
    single place that tells them apart, so no caller has to re-derive it."""
    if not os.path.isfile(path):
        return None, False, True
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:  # sbe: allow-silent NOT swallowed: the failure becomes ok=False,
        # which this function exists to give callers. Its docstring calls itself the
        # single place that tells existed from readable apart, and all three callers
        # branch on that flag rather than assuming a read succeeded.
        return None, True, False
    try:
        return json.loads(raw), True, True
    except ValueError:  # sbe: allow-silent same contract as the OSError above: a file
        # that exists but does not parse returns ok=False, so the caller reports what
        # it could not read instead of treating unparseable JSON as an absent file.
        return None, True, False


def _pretooluse_commands(hooks_doc):
    """Every `command` string a PreToolUse entry declares, in a document
    shaped either like a settings.json or a plugin's own hooks/hooks.json
    (the two shapes are identical at this key). Malformed shapes yield no
    commands rather than raising: a document that is not hook-shaped is not
    evidence of anything, and this helper never decides ABSENT vs NO-DATA on
    its own, its caller already resolved that from whether the file could be
    opened and parsed at all."""
    out = []
    if not isinstance(hooks_doc, dict):
        return out
    hooks = hooks_doc.get("hooks")
    if not isinstance(hooks, dict):
        return out
    entries = hooks.get("PreToolUse")
    if not isinstance(entries, list):
        return out
    for group in entries:
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks") or []:
            if isinstance(h, dict):
                cmd = h.get("command")
                if isinstance(cmd, str):
                    out.append(cmd)
    return out


def _enabled_plugin_ids(settings_docs):
    """Every plugin id `settings.json`'s own `enabledPlugins` marks `true`,
    across every settings document this section read successfully."""
    ids = set()
    for doc in settings_docs:
        if not isinstance(doc, dict):
            continue
        enabled = doc.get("enabledPlugins")
        if not isinstance(enabled, dict):
            continue
        for k, v in enabled.items():
            if v is True:
                ids.add(k)
    return ids


def _plugin_pretooluse_commands(config_dir, plugin_ids):
    """(commands, ok) of every PreToolUse command an enabled plugin's own
    cached hooks/hooks.json declares, resolved through installed_plugins.json
    the same way Claude Code itself resolves a plugin id to its install path.

    `ok` is False only when installed_plugins.json itself exists and could
    not be read or parsed: that is a genuine NO-DATA for the WHOLE plugin
    path, because without it no enabled plugin id can be resolved to
    anything. One PLUGIN's own hooks.json being missing or unreadable is not
    the same failure: `enabledPlugins` and installed_plugins.json were both
    readable, so this function still answers the question for every other
    enabled plugin, and a single bad plugin is simply skipped."""
    if not plugin_ids:
        return [], True
    installed_path = os.path.join(config_dir, "plugins", "installed_plugins.json")
    installed, existed, ok = _read_json_file(installed_path)
    if not existed:
        return [], True
    if not ok:
        return [], False
    plugins = installed.get("plugins") if isinstance(installed, dict) else None
    if not isinstance(plugins, dict):
        return [], True
    commands = []
    for pid in plugin_ids:
        entries = plugins.get(pid)
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            install_path = e.get("installPath")
            if not isinstance(install_path, str) or not install_path:
                continue
            doc, hexisted, hok = _read_json_file(
                os.path.join(install_path, "hooks", "hooks.json"))
            if not hexisted or not hok:
                continue
            commands.extend(_pretooluse_commands(doc))
    return commands, True


def detect_companion(cwd, root):
    """A CompanionSignal for whether COMPANION_HOOK_BASENAME is wired into a
    PreToolUse hook for this session. See the section docstring above this
    function for the signal, why it was chosen, and what it misses."""
    # C3: brother_paths reads CLAUDE_CONFIG_DIR first (so this is the
    # same directory it always was under Claude) and only diverges to
    # a Codex home on a positive Codex identification. Loaded by path,
    # fail-open: a hook must not die because a sibling is missing.
    paths = load_brother_paths()
    if paths is None:
        config_dir = os.environ.get(CLAUDE_CONFIG_DIR_ENV, "").strip() \
            or os.path.join(os.path.expanduser("~"), ".claude")
    else:
        config_dir = paths.config_dir()

    candidates = [os.path.join(config_dir, "settings.json"),
                  os.path.join(config_dir, "settings.local.json")]
    for base in _dedupe([b for b in (cwd, root) if b]):
        candidates.append(os.path.join(base, ".claude", "settings.json"))
        candidates.append(os.path.join(base, ".claude", "settings.local.json"))
    candidates = _dedupe(candidates)

    evidence, unreadable, settings_docs = [], [], []
    for path in candidates:
        doc, existed, ok = _read_json_file(path)
        if not existed:
            continue
        if not ok:
            unreadable.append(path)
            continue
        settings_docs.append(doc)
        for cmd in _pretooluse_commands(doc):
            if COMPANION_HOOK_BASENAME in cmd:
                evidence.append("a PreToolUse hook command in %s names %s"
                                 % (path, COMPANION_HOOK_BASENAME))

    plugin_ids = _enabled_plugin_ids(settings_docs)
    plugin_commands, plugin_ok = _plugin_pretooluse_commands(config_dir, plugin_ids)
    if not plugin_ok:
        unreadable.append(os.path.join(config_dir, "plugins", "installed_plugins.json"))
    for cmd in plugin_commands:
        if COMPANION_HOOK_BASENAME in cmd:
            evidence.append(
                "a PreToolUse hook command in an enabled plugin's own "
                "hooks/hooks.json names %s" % COMPANION_HOOK_BASENAME)

    if evidence:
        return CompanionSignal(CompanionSignal.PRESENT, "; ".join(sorted(set(evidence))))
    if unreadable and not settings_docs:
        return CompanionSignal(
            CompanionSignal.NO_DATA,
            "every source checked for a companion hook could not be read or "
            "parsed (%s), so presence could not be determined"
            % ", ".join(sorted(set(unreadable))))
    if unreadable:
        return CompanionSignal(
            CompanionSignal.NO_DATA,
            "%d source(s) named no companion hook, but %s could not be read "
            "or parsed, so absence is not certain"
            % (len(settings_docs), ", ".join(sorted(set(unreadable)))))
    return CompanionSignal(
        CompanionSignal.ABSENT,
        "no PreToolUse hook command naming %s was found in %s"
        % (COMPANION_HOOK_BASENAME, ", ".join(candidates)))


# ---------------------------------------------------------------------------
# Registry parsing. The shape is BrotherSBE's own, read from STATE.template.md
# and enforced by L13, NOT the sibling project's SQLite claims table.
#
#   - agent: <id> (sole writer, session <id>) | tier T1 | TTL <date> |
#     objective: ... | files: a.py, b.py | output: ... | boundaries: ... |
#     termination: ... | check: ... |
#
# and a fence is CLOSED by appending LANDED or ADOPTED to it.
# ---------------------------------------------------------------------------

def is_live_fence(s):
    """A live fence line, by BrotherSBE's own rule.

    The rule is `sbe_score._is_live_fence`, which is the BROADER of the two
    parses this project ships: it accepts both markdown bullets, while the two
    copies inside `sbe_telemetry.py` accept only `- `. The broader one is the
    right one to enforce with, because the narrow parse misses a real fence
    written with an asterisk bullet, and a missed fence is an unprotected file.
    The divergence inside BrotherSBE is real and is recorded in docs/HOOKS.md.

    Read off the owning module rather than re-typed, so the rule cannot drift
    into a second spelling here."""
    if not isinstance(s, str):
        return False
    return live_fence_rule()(s.strip())


_LIVE_FENCE_FN = None


def live_fence_rule():
    """`sbe_score._is_live_fence`, loaded by path exactly once.

    sbe_score.py reads BROTHERSBE_REGISTRIES at import time to build its own
    module-level REGISTRIES list, which is harmless (this hook never reads that
    list) but is why the import is deferred and cached rather than done at the
    top of the file: it must happen after the environment is settled."""
    global _LIVE_FENCE_FN
    if _LIVE_FENCE_FN is not None:
        return _LIVE_FENCE_FN
    try:
        import importlib.util
        path = os.path.join(HERE, "sbe_score.py")
        spec = importlib.util.spec_from_file_location("sbe_score_for_fence", path)
        if spec is None or spec.loader is None:
            raise ImportError("no import spec for %s" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "_is_live_fence", None)
        if not callable(fn):
            raise ImportError("sbe_score.py defines no _is_live_fence")
        _LIVE_FENCE_FN = fn
        return fn
    except Exception as e:
        raise OpenFail(
            "the live-fence rule could not be read from tools/sbe_score.py (%s: "
            "%s), and this hook holds no private copy of it on purpose, so it "
            "cannot tell which fence lines are still open"
            % (type(e).__name__, e))


def bullet_items(text):
    """The rendered registry text as whole markdown bullets, one string each.

    THIS IS THE ONE PLACE THIS HOOK READS MORE THAN THE PROJECT'S OWN CHECKS DO,
    and it is forced by the shape STATE.template.md actually ships. A fence there
    is a markdown bullet that CONTINUES onto indented lines:

        - agent: <id> (sole writer, session <id>) | tier T1 | TTL <date> |
          objective: ... |
          files: src/parser.py, tests/test_parser.py |
          ...
          LANDED 2026-01-15, evidence (verbatim, run after last edit):

    `sbe_score._is_live_fence` and `sbe_telemetry`'s two copies are applied to
    ONE STRIPPED LINE at a time. That works for what they measure, because the
    tier tag sits on the first line. It cannot work here: `files:` is on the
    third line and `LANDED` on the last, so a line-wise reader would find no file
    scope on any fence written the way the template writes them, and would find a
    closed fence still open. Both were observed against the shipped template.

    So the LIVENESS RULE is still the project's own, unmodified and imported
    rather than re-typed; only the UNIT it is applied to is the whole bullet
    instead of its first line. The consequence for the reader is stated in
    docs/HOOKS.md: a fence closed with LANDED on a continuation line is CLOSED to
    this hook and still reads as live to `sbe_score`, which is a hygiene false
    alarm in the scorer and never an unenforced fence here.

    A continuation is an indented, non-blank line that does not itself start a
    new bullet. A blank line ends the item, which is ordinary markdown."""
    items = []
    current = None
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        starts_bullet = stripped.startswith(("- ", "* "))
        indented = raw[:1] in (" ", "\t")
        if starts_bullet and not (indented and current is not None):
            if current is not None:
                items.append(current)
            current = stripped
        elif current is not None and stripped and indented:
            current += " " + stripped
        else:
            if current is not None:
                items.append(current)
            current = None
    if current is not None:
        items.append(current)
    return items


#: `files: <scope> |` inside a pipe-delimited fence line, or to end of line when
#: the author left the trailing pipe off. Case-insensitive because a registry is
#: hand-written prose and "Files:" is the same declaration.
_FILES_FIELD = re.compile(r"\bfiles\s*:\s*(.*?)(?:\||$)", re.I)

#: `session <id>` as STATE.template.md writes it, and `session: <id>` because
#: that is how an operator writes it half the time. The id runs to the next
#: separator: a closing paren, a pipe, a comma, or whitespace.
_SESSION_FIELD = re.compile(r"\bsession\s*:?\s*([^\s|,()]+)", re.I)

#: `agent: <id>`, for naming the owner in the refusal.
_AGENT_FIELD = re.compile(r"\bagent\s*:?\s*([^|(]+)", re.I)


def fence_files(line):
    """The declared file scope of a fence line, as a list of raw patterns, or
    None when the line declares no readable scope.

    None is load-bearing and is NOT an empty list: a fence with no `files:` field
    fences nothing this hook can compare against, so the caller fails OPEN and
    names the line, rather than treating "no scope" as "no conflict" in silence.
    """
    m = _FILES_FIELD.search(line)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    parts = [p.strip().strip("`'\"") for p in re.split(r"[,;]", raw)]
    parts = [p for p in parts if p]
    return parts or None


def fence_session(line):
    """The session id a fence line declares as its sole writer, or "" when it
    declares none. An undeclared owner is not this session, and the caller treats
    an unowned live fence as a genuine conflict, because L13's rule is one writer
    per file and a fence whose writer is anonymous is still a fence somebody else
    opened."""
    m = _SESSION_FIELD.search(line)
    if not m:
        return ""
    return m.group(1).strip().strip("`'\".,;)")


def fence_agent(line):
    """The agent id a fence line names, for the refusal message. Best effort: the
    message degrades, the refusal never does."""
    m = _AGENT_FIELD.search(line)
    if not m:
        return "(unnamed agent)"
    return m.group(1).strip().strip("`'\".,;") or "(unnamed agent)"


#: Additional identities this session answers to, comma separated, read from
#: the environment. A sibling tool that claims a fence under a DERIVED label
#: rather than the raw harness session id exports the label here, so this hook
#: recognizes the rightful owner instead of refusing them.
#:
#: This exists because of a measured failure, not a hypothetical one. A sibling
#: store claimed a fence under a label it derived from the harness id, said so
#: in its own output ("claiming under bm1-... so your own next edit is not
#: refused as a foreign writer"), and this hook refused that very session out
#: of its own fence: the label and the raw id share no prefix, so the generous
#: matching below could not see them as one session. The hook cannot verify
#: another tool's derivation, and it must not guess one, so the identity is
#: DECLARED rather than inferred, and the refusal text names this route so a
#: wrongly refused owner has an answer other than switching the hook off.
SESSION_ALIAS_ENV = "SBE_SESSION_ALIASES"


def session_aliases(env=None):
    """Every extra identity this session declares, lowercased, never empty
    strings. Absent variable means no aliases, which is the state every
    session starts in and changes nothing on its own."""
    raw = (env or os.environ).get(SESSION_ALIAS_ENV) or ""
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def same_session(declared, mine, aliases=None):
    """True when a fence line's declared session is this session.

    Prefix matching in BOTH directions, because a registry is hand-written: an
    operator abbreviates a UUID to its first eight characters as often as they
    paste the whole thing. The floor (MIN_SESSION_TOKEN) stops a one-character
    token from matching everything. Matching generously is the safe direction
    here: a false MATCH allows the write, which is this file's fail-open bias,
    while a false MISS would refuse the rightful owner out of their own fence.

    `aliases` are the other identities this session answers to (see
    SESSION_ALIAS_ENV): a derived label cannot be reached by prefix from the
    raw id it was derived from, so declaring it is the only honest way this
    hook can know the two name one session."""
    if not declared or not mine:
        return False
    d = declared.strip().lower()
    candidates = [mine] + list(aliases if aliases is not None else session_aliases())
    for candidate in candidates:
        if not candidate:
            continue
        m = candidate.strip().lower()
        if len(d) < MIN_SESSION_TOKEN or len(m) < MIN_SESSION_TOKEN:
            if d == m:
                return True
            continue
        if d == m or d.startswith(m) or m.startswith(d):
            return True
    return False


# ---------------------------------------------------------------------------
# Path canonicalization and scope comparison.
# ---------------------------------------------------------------------------

def canonical_target(root, raw, cwd=None):
    """A tool's target path as a root-relative POSIX string, symlinks resolved,
    or None when it falls outside the project root.

    os.path.realpath resolves symlinks in whatever prefix of the path already
    exists and leaves a nonexistent trailing component literal, so this behaves
    identically for Edit on an existing file and Write creating a new one.

    None means "not this project's business" (a different drive, a path above the
    root, an unusable string) and every caller treats that as allow: BrotherSBE
    fences a project, not the filesystem."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    root_real = os.path.realpath(root)
    base_dir = cwd if cwd else root_real
    try:
        base = raw if os.path.isabs(raw) else os.path.join(base_dir, raw)
        abs_real = os.path.realpath(base)
        rel = os.path.relpath(abs_real, root_real)
    except (ValueError, OSError):
        # Windows raises ValueError from relpath across drives, which is
        # definitionally outside the root.
        return None
    rel_posix = rel.replace(os.sep, "/")
    if rel_posix == ".." or rel_posix.startswith("../"):
        return None
    return posixpath.normpath(rel_posix)


def normalize_claim(pattern):
    """A declared fence path as a root-relative POSIX pattern.

    Not realpath'd: a claim may name a file that does not exist yet, and it may
    carry a wildcard, both of which realpath would mangle. Only the separators, a
    leading './', and a trailing '/' are normalized."""
    p = (pattern or "").strip().replace(os.sep, "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/")
    if not p:
        return ""
    return posixpath.normpath(p)


def _spelling_overlap(t, c):
    """The three exact-spelling overlap rules, factored out so the case-folded
    retry in `paths_overlap` can run the identical logic on lowered strings
    instead of drifting out of sync with it."""
    if t == c:
        return True
    if t.startswith(c + "/"):
        return True
    if c.startswith(t + "/"):
        # The claim is deeper than the target: writing the parent DIRECTORY of a
        # claimed file touches the claim. Only a directory-shaped target reaches
        # this, and Delete and CreateDirectory are exactly that.
        return True
    if any(ch in c for ch in "*?["):
        if fnmatch.fnmatch(t, c) and ("**" in c or c.count("/") == t.count("/")):
            return True
    return False


def _case_insensitive_probe(path):
    """Does swapping the case of this existing path's own name still find it?

    Case (in)sensitivity is a property of the volume a path lives on, not of
    any one file on it, so probing one real entry answers the question for
    every path beneath the same mount. `path` must exist; the probe reads the
    filesystem and writes nothing. Returns False on anything it cannot
    confirm, which is this hook's fail-open bias applied to the probe itself:
    an inconclusive probe must not manufacture a deny that was not there
    before this fix.
    """
    try:
        parent, name = os.path.split(os.path.realpath(path))
        swapped = name.swapcase()
        if not name or swapped == name:
            # Nothing alphabetic to flip (a name of digits or symbols): this
            # entry cannot answer the question, so it is not asked.
            return False
        candidate = os.path.join(parent, swapped)
        return os.path.exists(candidate) and os.path.samefile(path, candidate)
    except OSError:
        return False


def _same_entry_case_insensitive(root, t, c):
    """True only when the two case-variant spellings `t` and `c` name ONE
    filesystem entry, confirmed rather than assumed.

    When both spellings exist on disk already, `os.path.samefile` over their
    real paths is definitive: matching inodes mean one file no matter what the
    strings say, which is the same proof
    `test_a_case_variant_of_a_fenced_path_is_allowed_which_is_a_limit` uses on
    itself before it trusts the fixture. When one or both do not exist yet (a
    Write about to create the target, or a fence naming a file nobody wrote),
    there is nothing to samefile, so `root` itself is probed instead: if this
    project's own volume folds case on lookup, every path under it does too."""
    full_t = os.path.join(root, t.replace("/", os.sep))
    full_c = os.path.join(root, c.replace("/", os.sep))
    try:
        if os.path.exists(full_t) and os.path.exists(full_c):
            return os.path.samefile(full_t, full_c)
    except OSError:
        return False
    return _case_insensitive_probe(root)


def paths_overlap(target, claim, root=None):
    """True when a concrete root-relative target falls inside a declared claim.

    Three ways a claim covers a target, all of them ordinary in a hand-written
    registry: the same path, a directory prefix ("tools/" covers
    "tools/sbe_gate.py"), and a glob ("docs/*.md" covers "docs/SETUP.md").
    fnmatch handles the glob case with an explicit separator guard, because
    fnmatch's '*' happily crosses '/' and a claim of "docs/*" must not silently
    swallow "docs/guides/01-quickstart.md" that its author never named.

    CASE. The exact-spelling comparison above used to be the only one, so on a
    case-insensitive filesystem (the macOS default) a fence written for
    "docs/SETUP.md" let a second writer land on "docs/setup.md": one file, two
    spellings, and only one of them was compared. When the exact comparison
    misses, this retries case-folded, but a case-folded MATCH is never trusted
    on its own, because two honestly different files named "a.md" and "A.md"
    on a case-sensitive filesystem (the Linux default) must not false-conflict.
    `root` is what makes the retry a confirmation rather than a guess: without
    it (a caller that has no filesystem to ask) the fold is skipped and the
    exact-spelling answer stands, which is this hook's fail-open bias again."""
    t = normalize_claim(target)
    c = normalize_claim(claim)
    if not t or not c:
        return False
    if _spelling_overlap(t, c):
        return True
    if root and _spelling_overlap(t.lower(), c.lower()) and _same_entry_case_insensitive(
            root, t, c):
        return True
    return False


# ---------------------------------------------------------------------------
# Reading the registries.
# ---------------------------------------------------------------------------

class Fence(object):
    """One live fence, with everything the decision and the refusal need.

    `kind` is "markdown" (a STATE.md-style bullet) or "task" (an open record
    from `.sbe/tasks.json`). The two shapes agree on everything the overlap
    comparison needs (`files`, `session`, `agent`) but NOT on what an absent
    `session` means: see the module docstring's "TWO REGISTRY SHAPES" section.
    decide() is the one place that reads `kind` to tell them apart; every
    other consumer of a Fence (the refusal text, the `fences` diagnostic)
    treats the two identically."""

    def __init__(self, registry, line, files, session, agent, kind="markdown"):
        self.registry = registry
        self.line = line
        self.files = files
        self.session = session
        self.agent = agent
        self.kind = kind


def registry_patterns(cwd, root=None):
    """Every glob pattern that names a fence registry, in the order the rest of
    the project reads them: the project's own STATE.md first, then whatever
    BROTHERSBE_REGISTRIES declares. Mirrors `sbe_telemetry.cmd_fence_lint`, so
    the fences this hook enforces are the fences fence-lint printed to the
    operator before dispatch.

    The one addition to fence-lint's list is the PROJECT ROOT's STATE.md when it
    differs from cwd's. fence-lint is run by a human standing in the project
    root; this hook is fired by the harness on whatever cwd the session happens
    to hold, and an Edit issued from a subdirectory would otherwise find no
    registry and fail open past a fence sitting one level up. A missed registry
    is an unprotected file, so both are searched and duplicates collapse."""
    pats = [os.path.join(cwd, PROJECT_REGISTRY)]
    if root and os.path.realpath(root) != os.path.realpath(cwd):
        pats.append(os.path.join(root, PROJECT_REGISTRY))
    # SPLIT ON THE PLATFORM'S LIST SEPARATOR, NOT ON A LITERAL COLON. A Windows
    # path begins with a drive letter and a colon, so splitting `C:\work\STATE.md`
    # on ":" yields "C" and "\work\STATE.md" and the registry is never found.
    # The consequence was not a crash: the hook found no fence and ALLOWED the
    # write, so on Windows the one-writer-per-file boundary silently did not
    # bind for any registry named this way. `tools/sbe_score.py:42` already had
    # this right, which makes this drift from an existing correct pattern rather
    # than an open question, and the three tools that read this variable have to
    # agree or they disagree about what is fenced.
    pats += [p.strip() for p in os.environ.get(REGISTRIES_ENV, "").split(os.pathsep)
             if p.strip()]
    seen, out = set(), []
    for p in pats:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def task_registry_bases(cwd, root=None):
    """The directories this hook asks `src/brothersbe/tasks.py::registry_path`
    for a `.sbe/tasks.json` under: cwd first, then root when it differs from
    cwd. Mirrors `registry_patterns`' own STATE.md dual-check above, for the
    identical reason: an Edit fired from a subdirectory must still find a
    registry that lives at the project root.

    This NEVER reads `BROTHERSBE_REGISTRIES`. That variable names glob
    patterns for the markdown registry shape (`sbe_telemetry.py`,
    `sbe_score.py`, and `registry_patterns` above all read it that way); `sbe
    task open` writes exactly one file at a fixed relative path, so there is
    no pattern to configure. Discovery instead has to work from cwd/root
    alone, resolved the SAME way every other path in this hook already is
    (the payload's cwd, falling back to project_dir): `src/brothersbe/tasks.py`
    itself locates the registry by walking to the git toplevel
    (`repo_root_of`, a `git rev-parse` subprocess call), which this
    subprocess-free hook does not make (see the module docstring's closing
    line). Using cwd/root is the closest honest approximation available
    without shelling out to git, and it is the same approximation
    `tools/sbe_authority_hook.py` (LT-402) already makes for this identical
    file."""
    bases = [cwd]
    if root and os.path.realpath(root) != os.path.realpath(cwd):
        bases.append(root)
    seen, out = set(), []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def task_fence_label(task):
    """A one-line, human-readable stand-in for a task record's fence "line",
    used everywhere a markdown Fence's raw bullet text would be: the refusal
    text and the `fences` diagnostic. `.sbe/tasks.json` carries no such line
    to quote verbatim, so this renders the same handful of fields a markdown
    fence line carries (id in place of a free-text objective, agent, role,
    the owned paths) rather than dumping the JSON record."""
    return ("task %s (agent %s, role %s) owns: %s"
            % (task.get("id") or "(no id)", task.get("agent") or "(unnamed agent)",
               task.get("role") or "?", ", ".join(task.get("ownedPaths") or [])))


class FenceSet(object):
    """Every live fence this hook could read, plus what it could not.

    Deliberately an object and not a (fences, notes) 2-tuple, for the same reason
    Decision is: this project's honesty meta-test reads a 2-tuple return as a
    possible verdict source, and neither of those things is a check verdict.
    Keeping the shapes distinct keeps that lint honest rather than buying an
    allowlist entry for a function it was never meant to cover."""

    def __init__(self, fences, notes):
        self.fences = fences
        self.notes = notes


#: The note `_scan_registries` appends for a markdown fence line it recognized
#: as live but could not resolve a `files:` scope for. `cmd_audit` matches on
#: this exact substring to count unenforceable lines, rather than re-deriving
#: the condition with a second parser.
_UNENFORCEABLE_MARK = "carries a live fence line with no readable `files:` scope"


def _scan_registries(cwd, root=None):
    """The raw walk both `read_fences` and `cmd_audit` need: every fence this
    hook could parse, plus every note about one it could not, across BOTH
    registry shapes (markdown STATE.md-style bullets and `.sbe/tasks.json`).
    Never raises; `read_fences` is what turns "nothing usable" into OpenFail,
    so a caller that wants a count instead of a decision (`cmd_audit`) can
    read the same walk without inheriting that raise.

    Returns (fences, notes, opened, unreadable, denied_dirs)."""
    checks = require_checks_module()
    fences, notes, opened, unreadable, denied_dirs = [], [], 0, [], []
    for pat in registry_patterns(cwd, root):
        # glob.glob returns FEWER paths, not an error, over a directory it cannot
        # enter, so discovery has to account for what it could not see. Same
        # shared helper, same denial axis, as the scorer and fence-lint.
        hits, denied = checks.glob_with_denials(pat)
        denied_dirs.extend(denied)
        for p in sorted(hits):
            if not os.path.isfile(p):
                # A directory, a FIFO, a socket: present and not a registry.
                # Named, never skipped in silence.
                if os.path.lexists(p):
                    unreadable.append("%s (not a regular file)" % p)
                continue
            try:
                with open(p, "rb") as f:
                    raw = f.read()
            except OSError as e:
                unreadable.append("%s (%s)" % (p, type(e).__name__))
                continue
            # errors="replace" so an undecodable registry degrades to garbled
            # text rather than to an exception. A registry that decodes to
            # garbage produces zero recognizable fence lines, which is the
            # corrupt-registry fail-open below, and it is reported as such.
            text = raw.decode("utf-8", "replace")
            opened += 1
            # Rendered, not raw: a fence line inside an HTML comment is invisible
            # to the reader of the registry, so it is invisible here too. Same
            # rule, same shared helper, as every other reader in this project.
            for s in bullet_items(checks.without_comments(text)):
                if not is_live_fence(s):
                    continue
                files = fence_files(s)
                if files is None:
                    notes.append(
                        ("sbe_fence_hook: %s " + _UNENFORCEABLE_MARK + ", so this hook "
                         "cannot tell what it owns and did NOT enforce it. Line: %s")
                        % (p, s[:160]))
                    continue
                fences.append(Fence(p, s, files, fence_session(s), fence_agent(s),
                                    kind="markdown"))
    tasks_mod = load_tasks_module()
    if tasks_mod is None:
        notes.append(
            "sbe_fence_hook: src/brothersbe/tasks.py could not be imported (%s), so "
            ".sbe/tasks.json (the registry `sbe task open` writes) was NOT checked "
            "this call; markdown STATE.md-style registries are still enforced on "
            "their own." % _TASKS_ERROR)
    else:
        for base in task_registry_bases(cwd, root):
            p = tasks_mod.registry_path(base)
            if not os.path.isfile(p):
                continue
            try:
                data = tasks_mod.load_registry(base)
            except tasks_mod.RegistryUnusable as e:
                unreadable.append("%s (%s)" % (p, e))
                continue
            opened += 1
            for t in tasks_mod.open_tasks(data):
                owned = t.get("ownedPaths") or []
                if not owned:
                    notes.append(
                        "sbe_fence_hook: %s carries an open task %r with no "
                        "ownedPaths, so this hook cannot tell what it owns and did "
                        "NOT enforce it." % (p, t.get("id")))
                    continue
                fences.append(Fence(
                    p, task_fence_label(t), owned, (t.get("session") or "").strip(),
                    t.get("agent") or "(unnamed agent)", kind="task"))
    return fences, notes, opened, unreadable, denied_dirs


def read_fences(cwd, root=None):
    """A FenceSet across every configured registry, of BOTH shapes this hook
    reads (the module docstring's "TWO REGISTRY SHAPES" section): the
    markdown STATE.md registries `registry_patterns` names, and the
    structured `.sbe/tasks.json` `task_registry_bases` names.

    Raises OpenFail when NEITHER shape can support a decision at all (nothing
    opened anywhere, or something opened but named an unreadable path, or
    everything that opened carried no live fence). An unimportable
    `src/brothersbe/tasks.py` degrades only that one source (a note, not a
    raise): the markdown reader still runs and can still support a decision
    on its own. The notes carry the per-fence problems that do not by
    themselves stop a decision but that the operator has to know about,
    because a fence this hook could not read is a fence it cannot enforce."""
    fences, notes, opened, unreadable, denied_dirs = _scan_registries(cwd, root)
    if denied_dirs:
        # Before every other verdict, because a denied parent directory is
        # exactly how a configured registry set silently becomes smaller: the
        # files inside it never entered discovery at all, so a decision over the
        # rest would read as covering them.
        raise OpenFail(
            "%d director(y/ies) named by %s exist and cannot be entered (%s); the "
            "registry files inside never entered discovery, so any fence in them "
            "is invisible here"
            % (len(denied_dirs), REGISTRIES_ENV,
               ", ".join(sorted(set(denied_dirs))[:4])))
    if unreadable:
        raise OpenFail(
            "%d registry path(s) exist and could not be read (%s); a fence this "
            "hook cannot open is a fence it cannot enforce, and refusing on the "
            "strength of the registries it COULD read would read as covering them"
            % (len(unreadable), ", ".join(sorted(unreadable)[:4])))
    if not opened:
        raise OpenFail(
            "no fence registry was opened under %s; set %s to colon-separated "
            "glob patterns, put a STATE.md carrying a fence registry at the "
            "project root (see STATE.template.md), or open a task naming this "
            "path with `sbe task open` (see .sbe/tasks.json)" % (cwd, REGISTRIES_ENV))
    if not fences:
        # The notes ride along in the message, not just in the return value. A
        # registry whose only fence had no readable `files:` scope produces zero
        # fences AND the one note that explains why, and raising past that note
        # would print "none of them carries a live fence line" over a fence line
        # sitting right there in the file: the precise shape of a verdict
        # asserting something the tool never examined.
        raise OpenFail(
            "%d registry file(s) opened under %s and none of them carries a live "
            "fence line this hook could read, so there is no fence to enforce%s"
            % (opened, cwd,
               (". Unenforceable fence line(s) found: " + " ".join(notes))
               if notes else ""))
    return FenceSet(fences, notes)


# ---------------------------------------------------------------------------
# The decision.
# ---------------------------------------------------------------------------

def deny_payload(reason):
    """The deny object, shaped exactly as the PreToolUse contract requires:

      {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                              "permissionDecision": "deny",
                              "permissionDecisionReason": "..."}}

    Emitted on stdout with exit code 0. Exit 2 would also block, feeding stderr
    back as the reason, but it is the wrong instrument here: exit 2 means "the
    hook itself failed", and every failure THIS hook has is a fail-open."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def refusal_reason(target, fence, my_session):
    """The refusal, which must name the fence that owns the file AND an escape
    that actually works.

    All three escapes below are BrotherSBE's own declared mechanisms, not
    inventions of this file:
      1. L13: "queueing rather than running in parallel when two writers overlap
         in file scope". Report the change to the owner and let them write it.
      2. STATE.template.md: "Close a fence only by appending its evidence block:
         the exact command run and its last lines." A closed fence releases the
         path, and this hook stops refusing the moment LANDED appears on the
         line. That is the escape the test fixture demonstrates end to end.
      3. ADOPTED, the other closing marker both of this project's parsers already
         honour, for a deliberate takeover: mark the line ADOPTED and open a new
         fence naming this session as sole writer.
    """
    owner = fence.session or "(no session declared on the fence line)"
    return (
        "BrotherSBE fence (L13, one writer per file): %s is inside the file scope "
        "of a LIVE fence in %s, opened by agent %s as sole writer for session %s. "
        "This session is %s, so it is not the writer for that path.\n"
        "The fence line, verbatim:\n"
        "  %s\n"
        "Do not write across a fence. Any of these releases it, and nothing else "
        "does:\n"
        "  1. Report the change to the fence owner and let that writer make it. "
        "L13 says overlapping writers queue, they do not run in parallel.\n"
        "  2. If that work is finished, CLOSE the fence where it lives, in %s, by "
        "appending its evidence block to that line: the marker LANDED, the exact "
        "command run, and its last lines. This hook stops refusing %s the moment "
        "that line reads LANDED.\n"
        "  3. To take the fence over deliberately, append ADOPTED to that line and "
        "write a new fence line naming this session (%s) as sole writer, before "
        "you edit anything.\n"
        "  4. If that fence is ALREADY YOURS under another name, because the tool "
        "that claimed it recorded a derived label rather than this session id, "
        "declare it: export %s=%s (comma separated for several). This hook cannot "
        "verify another tool's derivation and will not guess one, so a label it "
        "was never told about reads as a foreign writer. Declaring the alias is "
        "the fix; switching this hook off is not."
        % (target, fence.registry, fence.agent, owner, my_session,
           fence.line[:400], fence.registry, target, my_session,
           SESSION_ALIAS_ENV, owner))


def patch_deny_reason(reason):
    """The refusal text for an apply_patch call this hook could not trust to
    name its own targets safely.

    Unlike refusal_reason, there is no owning fence to name and no escape to
    offer: the call is refused because this hook could not establish what it
    touches, not because another session already owns the file. Naming a
    door that does not open would be worse than naming none, so this names
    only the reason and what a trustworthy call looks like."""
    return (
        "BrotherSBE fence (L13, one writer per file): this apply_patch call is "
        "denied rather than assumed safe. Reason: %s.\n"
        "A trustworthy apply_patch call opens with '*** Begin Patch', closes "
        "with '*** End Patch', and every Add File, Update File, Delete File, "
        "and Move to header names a path that resolves inside this project's "
        "worktree." % reason)


def targets_for(tool_name, tool_input):
    """Every path `tool_name`'s input names, through the ONE dispatch both
    hooks share.

    apply_patch's targets live in free-form patch text, so it routes to
    apply_patch_targets, which raises PatchDeny rather than ever returning an
    empty or partial list; every other write tool reads PATH_KEYS via
    extract_targets. tools/sbe_authority_hook.py calls this instead of
    extract_targets so the two hooks can never again disagree about which
    files a tool touches: two parsers of one format is how the authority hook
    stayed blind to apply_patch for a night while the fence hook saw it."""
    if tool_name == "apply_patch":
        return apply_patch_targets((tool_input or {}).get("command"))
    return extract_targets(tool_input)


def extract_targets(tool_input):
    """Every path a write tool's input names, in input order, de-duplicated.

    Walks nested lists of dicts too, so a MultiEdit-shaped payload whose per-edit
    entries carry their own file_path is not silently reduced to the top-level
    path. Bounded depth and width: a hook that recursed without limit on a
    hostile payload would hang in front of every edit."""
    out, seen = [], set()

    def visit(node, depth):
        if depth > 6:
            return
        if isinstance(node, dict):
            for k in PATH_KEYS:
                v = node.get(k)
                if isinstance(v, str) and v.strip() and v not in seen:
                    seen.add(v)
                    out.append(v)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    visit(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:64]:
                visit(v, depth + 1)

    visit(tool_input, 0)
    return out


# ---------------------------------------------------------------------------
# Codex apply_patch parsing.
#
# apply_patch carries no PATH_KEYS field: every target it names, source and
# destination alike, lives inside the free-form patch text in
# tool_input.command. That text is the ONLY place decide() reads for this
# tool, per Phase 5's own requirement, and it is parsed for headers only:
# `*** Add File: <path>`, `*** Update File: <path>`, `*** Delete File: <path>`,
# and a `*** Move to: <path>` naming an Update File section's destination. The
# hunk bodies between headers (`@@`, ` `, `-`, `+` lines) are never inspected;
# this hook fences WHICH files a call may touch, not what changes inside them.
# ---------------------------------------------------------------------------

class PatchDeny(Exception):
    """Raised when an apply_patch payload cannot be trusted to name its own
    targets safely.

    Every OTHER failure path in this file fails OPEN, because a hook that
    fails closed on its own bug stops the operator's own editing, which is
    worse than no hook at all (see the module docstring's rule 1). This
    exception is the one deliberate exception to that rule, and only for
    apply_patch: its targets live in text this hook must parse itself rather
    than in a structured argument, so a patch this hook cannot fully read is
    the one shape where "could not tell" must not default to "allowed". Caught
    only inside the apply_patch branch of decide(), and always produces a
    DENY, never a fail-open."""


#: `*** Begin Patch` and `*** End Patch`, the two lines every apply_patch
#: payload must open and close with. Whitespace after `***` and around the
#: text is tolerated because being lenient here only means recognizing MORE
#: real markers, never fewer, which is the safe direction: a marker missed
#: would mean a header this hook fails to see, and a missed header is a
#: missed target.
_PATCH_BEGIN_RE = re.compile(r"^\*\*\*\s*Begin Patch\s*$")
_PATCH_END_RE = re.compile(r"^\*\*\*\s*End Patch\s*$")

#: `*** Add File: <path>`, `*** Update File: <path>`, `*** Delete File: <path>`.
#: The path runs to end of line; apply_patch paths are never pipe-delimited
#: the way a fence registry's `files:` field is.
#: Case-insensitive and whitespace-tolerant on purpose, and in the SAFE
#: direction: leniency here only means recognizing MORE header spellings,
#: never fewer. A refuter proved the strict version unsafe the other way
#: round: `*** update file: CLAUDE.md` slid past as hunk text while a decoy
#: section satisfied the saw-a-section check.
_PATCH_HEADER_RE = re.compile(
    r"^\*\*\*\s*(Add\s+File|Update\s+File|Delete\s+File)\s*:\s*(.*)$",
    re.IGNORECASE)

#: `*** Move to: <path>`, valid only immediately inside an Update File section
#: (checked by the parser below, not by this pattern), naming the destination
#: a rename-plus-edit lands the source file at.
_PATCH_MOVE_RE = re.compile(r"^\*\*\*\s*Move\s+to\s*:\s*(.*)$", re.IGNORECASE)

#: `*** End of File`, the one other `***` line the apply_patch format uses
#: (inside an Add File body, marking that the file ends without a newline).
#: Recognized so the classify-every-marker rule below does not refuse a
#: legitimate patch.
_PATCH_EOF_RE = re.compile(r"^\*\*\*\s*End of File\s*$", re.IGNORECASE)


def apply_patch_targets(command):
    """Every source and destination path an apply_patch command names, in
    patch order, de-duplicated.

    Raises PatchDeny naming why, rather than ever returning an empty or
    partial list, whenever the patch cannot be trusted: the command is not a
    non-empty string, it does not open with `*** Begin Patch` or close with
    `*** End Patch`, a header names no path, a `*** Move to:` line appears
    with no preceding `*** Update File:` header to attach to, or the body
    names no file section at all. A patch this hook cannot fully read is
    refused, never assumed to touch nothing."""
    if not isinstance(command, str) or not command.strip():
        raise PatchDeny("tool_input.command is missing, empty, or not text")

    stripped = [ln.strip() for ln in command.splitlines()]
    non_blank = [ln for ln in stripped if ln]
    if not non_blank or not _PATCH_BEGIN_RE.match(non_blank[0]):
        raise PatchDeny(
            "the command does not open with '*** Begin Patch', so this hook "
            "cannot trust that it has seen the whole patch")
    if not _PATCH_END_RE.match(non_blank[-1]):
        raise PatchDeny(
            "the command does not close with '*** End Patch', so this hook "
            "cannot trust that it has seen the whole patch")

    targets, seen = [], set()

    def add(raw_path, why):
        p = (raw_path or "").strip()
        if not p:
            raise PatchDeny("a %s header names no path" % why)
        if p.startswith("/") or p.startswith("\\") or re.match(r"^[A-Za-z]:[/\\]", p):
            # apply_patch paths are project-relative by format. An absolute
            # spelling (including the `//x` shape that resolves outside the
            # canonical root while an applier may treat it as `x`) is exactly
            # the differential this parser must not wave through.
            raise PatchDeny(
                "a %s header names an absolute path (%s); apply_patch paths "
                "are project-relative" % (why, p[:120]))
        if p not in seen:
            seen.add(p)
            targets.append(p)

    saw_section = False
    pending_update = None  # the path an Update File header most recently
                            # opened, so a following Move to line attaches to
                            # the section it belongs to and not to whichever
                            # header happened to run last.
    for line in stripped:
        if not line:
            continue
        if _PATCH_BEGIN_RE.match(line) or _PATCH_END_RE.match(line):
            pending_update = None
            continue
        m = _PATCH_HEADER_RE.match(line)
        if m:
            # Normalize the spelling before comparing: the header regex is
            # case- and whitespace-tolerant, and `update  file` must attach a
            # following Move to exactly as `Update File` does.
            kind = re.sub(r"\s+", " ", m.group(1)).title()
            path = m.group(2)
            add(path, kind)
            saw_section = True
            pending_update = path.strip() if kind == "Update File" else None
            continue
        mv = _PATCH_MOVE_RE.match(line)
        if mv:
            if pending_update is None:
                raise PatchDeny(
                    "a 'Move to' line appears with no preceding 'Update "
                    "File' section to attach it to")
            add(mv.group(1), "Move to")
            pending_update = None
            continue
        if _PATCH_EOF_RE.match(line):
            continue
        if line.startswith("***"):
            # Every `***` marker must classify as one of the five forms this
            # parser knows, or the patch is refused. A marker read past as
            # body text was the refuter's decoy-header trick: a section this
            # parser dismissed that a more tolerant applier might honor is a
            # target this hook never saw.
            raise PatchDeny(
                "a '***' marker line this parser cannot classify: %r"
                % line[:80])
        # Any other line (a hunk's @@ context marker, or a body line
        # prefixed ' ', '-', or '+') carries no path and is not a header; it
        # is read past, not parsed, exactly as this hook reads past the
        # content of an Edit or Write call.

    if not saw_section:
        raise PatchDeny(
            "the command names no Add File, Update File, or Delete File "
            "section")
    return targets


class Decision(object):
    """The hook's answer.

    Deliberately an object and not a (verdict, evidence) 2-tuple: this project's
    honesty meta-test reads a 2-tuple return as a possible verdict source, and
    this file produces a permission decision, which is a different thing from a
    check verdict. Keeping the shapes distinct keeps that lint honest rather than
    buying an allowlist entry for a function it was never meant to cover.

    `payload` is None for ALLOW and for FAIL-OPEN, deliberately the same value,
    so no failure path can produce a deny by accident."""

    def __init__(self, payload, notes):
        self.payload = payload
        self.notes = notes


def decide(payload):
    """Return a Decision. `payload` is the parsed PreToolUse hook JSON."""
    notes = []
    try:
        if not isinstance(payload, dict):
            raise OpenFail("hook payload was not a JSON object")
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or tool_name not in WRITE_TOOLS:
            # Not a file-writing tool. Silent, not loud: this is the common case
            # (every Read, Grep, Bash and TodoWrite lands here) and a stderr line
            # per call would be noise the operator learns to ignore. Bash is here
            # by design, not by omission: see WRITE_TOOLS.
            return Decision(None, [])
        if os.environ.get(DISABLE_ENV, "").strip() not in ("", "0"):
            return Decision(None, [
                "sbe_fence_hook: %s is set, so the fence was NOT checked and this "
                "write is allowed. Unset it to restore L13 enforcement."
                % DISABLE_ENV])

        # COMPANION DEFERENCE (docs/adr/2026-08-12-where-the-shared-machinery-lives.md).
        # Checked before anything else in this branch, including apply_patch's
        # own target-trust parsing below: when the companion is authoritative
        # for one-writer-per-file, this file makes no ownership judgment at
        # all, so there is nothing left here for a patch-parsing DENY to be
        # protecting. Raw, un-canonicalized cwd/project_dir are enough for
        # this: it only needs a directory to look for a .claude/settings.json
        # in, never a fenced target to compare.
        cwd_hint = payload.get("cwd")
        cwd_hint = cwd_hint.strip() if isinstance(cwd_hint, str) and cwd_hint.strip() else None
        root_hint = payload.get("project_dir")
        root_hint = root_hint.strip() if isinstance(root_hint, str) and root_hint.strip() else None
        signal = detect_companion(cwd_hint, root_hint)
        if signal.status == CompanionSignal.PRESENT:
            return Decision(None, [
                "sbe_fence_hook: DEFERRING to the companion's fence hook (%s), "
                "which is authoritative for L13 one-writer-per-file enforcement "
                "on this session per "
                "docs/adr/2026-08-12-where-the-shared-machinery-lives.md. This "
                "write was NOT checked against BrotherSBE's own STATE.md "
                "registries. Detected: %s."
                % (COMPANION_HOOK_BASENAME, signal.detail)])
        if signal.status == CompanionSignal.NO_DATA:
            notes.append(
                "sbe_fence_hook: could not determine whether the companion's "
                "fence hook is installed, so this hook keeps enforcing its own "
                "fences rather than guessing either way. Reason: %s"
                % signal.detail)

        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            raise OpenFail("tool_input for %s was not a JSON object" % tool_name)

        # apply_patch carries no PATH_KEYS field: its targets live only in the
        # free-form patch text in tool_input.command, read here and NOWHERE
        # else for this tool, per Phase 5's own requirement. A patch this hook
        # cannot fully trust is a DENY, not the fail-open every other failure
        # path in this file uses, because "could not tell" must not default
        # to "allowed" for a write tool whose targets are free-form text.
        is_patch = tool_name == "apply_patch"
        try:
            raw_targets = targets_for(tool_name, tool_input)
        except PatchDeny as e:
            return Decision(deny_payload(patch_deny_reason(str(e))), notes)

        if not raw_targets:
            if is_patch:
                return Decision(
                    deny_payload(patch_deny_reason(
                        "the command named no target path")), notes)
            raise OpenFail(
                "no target path found in tool_input for %s (keys present: %s)"
                % (tool_name,
                   ", ".join(sorted(str(k) for k in tool_input)) or "none"))

        my_session = os.environ.get(SESSION_ENV, "").strip()
        if not my_session:
            sid = payload.get("session_id")
            my_session = sid.strip() if isinstance(sid, str) else ""
        if not my_session:
            raise OpenFail(
                "the hook payload carried no session_id and %s is unset, so this "
                "session has no identity to compare against a fence line's "
                "declared writer" % SESSION_ENV)

        cwd = payload.get("cwd")
        # No cwd in the payload means relative paths resolve against the
        # PROJECT once root is known below, never against wherever this hook
        # process happens to run: the process cwd is an accident of the
        # harness.
        cwd = cwd.strip() if isinstance(cwd, str) and cwd.strip() else ""
        root = payload.get("project_dir")
        root = root.strip() if isinstance(root, str) and root.strip() else (
            cwd or os.getcwd())
        if not cwd:
            cwd = root

        if is_patch:
            # Every OTHER write tool treats a target outside the project root
            # as "not this project's business" and allows it (BrotherSBE
            # fences a project, not the machine). apply_patch is refused
            # instead: canonicalization already resolves '..' traversal, an
            # absolute path, and a symlink escape to the same None outcome
            # (canonical_target realpath's the target before comparing), and
            # for a tool whose targets are parsed from free-form text, a
            # target that does not canonicalize inside the declared worktree
            # is exactly the undecidable-write shape this hook denies rather
            # than assumes safe. Checked before the registry is even opened,
            # so this refusal does not depend on a fence being configured at
            # all: it is a boundary rule, not a fence conflict.
            for raw in raw_targets:
                if canonical_target(root, raw, cwd) is None:
                    return Decision(deny_payload(patch_deny_reason(
                        "%r does not resolve to a path inside this "
                        "project's worktree (%s); traversal, a symlink "
                        "escape, and an absolute path outside the worktree "
                        "are all refused" % (raw, root))), notes)

        found = read_fences(cwd, root)
        notes.extend(found.notes)

        for raw in raw_targets:
            rel = canonical_target(root, raw, cwd)
            if rel is None:
                # Outside the project root. BrotherSBE fences a project, not the
                # machine. (apply_patch never reaches here: it was already
                # denied above.)
                continue
            for fence in found.fences:
                if same_session(fence.session, my_session):
                    continue
                if not any(paths_overlap(rel, claim, root) for claim in fence.files):
                    continue
                if not fence.session and fence.kind == "task":
                    # ABSENT IS NOT A MATCH-ALL (module docstring, "TWO
                    # REGISTRY SHAPES"). A markdown fence with no declared
                    # session still refuses below, unchanged; a task record
                    # with no session cannot be proven to conflict with
                    # anybody, so this is a FAIL-OPEN, named, not a silent
                    # allow and not the deny every other branch here reaches.
                    notes.append(
                        "sbe_fence_hook: FAILING OPEN, the write to %s is allowed "
                        "even though it falls inside an open task's ownedPaths in "
                        "%s (%s). Reason: that task record carries no session "
                        "identity, so this hook cannot tell its holder from any "
                        "other session and will not invent one to deny with. Open "
                        "the task with CLAUDE_SESSION_ID or SBE_SESSION_ID set to "
                        "produce a refusal that actually protects it."
                        % (rel, fence.registry, fence.line[:200]))
                    continue
                return Decision(
                    deny_payload(refusal_reason(rel, fence, my_session)), notes)
        return Decision(None, notes)
    except OpenFail as e:
        notes.append(
            "sbe_fence_hook: FAILING OPEN, the write is allowed and the fence was "
            "NOT checked. Reason: %s" % e)
        return Decision(None, notes)
    except Exception as e:
        # The blanket catch is the point, not laziness: an unforeseen bug in this
        # file must never become a refusal in front of the operator's editing.
        # Type and message, no traceback, so the stderr line stays one readable
        # sentence and the failure is still named rather than swallowed.
        notes.append(
            "sbe_fence_hook: FAILING OPEN after an unexpected error, the write is "
            "allowed and the fence was NOT checked. Reason: %s: %s"
            % (type(e).__name__, e))
        return Decision(None, notes)


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------

class StdinPayload(object):
    """The parsed hook payload, or the reason there is none.

    An object rather than a (payload, error) pair, for the reason stated on
    Decision and FenceSet: a 2-tuple return in this project reads as a possible
    check verdict, and this is a parse result."""

    def __init__(self, payload, error):
        self.payload = payload
        self.error = error


def read_stdin_json():
    """A StdinPayload. Never raises: unreadable stdin is a fail-open, not a
    crash in front of the operator's editing."""
    try:
        raw = sys.stdin.read()
    except Exception as e:
        return StdinPayload(
            None, "stdin could not be read (%s: %s)" % (type(e).__name__, e))
    if not raw or not raw.strip():
        return StdinPayload(None, "stdin was empty")
    try:
        return StdinPayload(json.loads(raw), None)
    except ValueError as e:
        return StdinPayload(
            None, "stdin was not valid JSON (%s: %s)" % (type(e).__name__, e))


def cmd_hook(argv):
    parsed = read_stdin_json()
    if parsed.error is not None:
        _warn("sbe_fence_hook: FAILING OPEN, the write is allowed and the fence "
              "was NOT checked. Reason: %s" % parsed.error)
        return 0
    _rs = load_repo_scope_module()
    if _rs is not None and _rs.hooks_off(payload=parsed.payload):
        return 0
    decision = decide(parsed.payload)
    for n in decision.notes:
        _warn(n)
    if decision.payload is not None:
        _out(json.dumps(decision.payload))
    return 0


def cmd_fences(argv):
    """Diagnostics: what this hook can see right now, and what it cannot.

    Everything on stderr and nothing on stdout, because stdout is the decision
    channel and a diagnostic there would corrupt the protocol if anyone wired
    this subcommand into the hook slot by mistake."""
    if argv and argv[0].startswith("-"):
        # A flag is not a directory: `fences --bogus` used to be read as a
        # directory named --bogus and reported "no fence is enforceable",
        # exit 0, which is a silent misread of the invocation.
        _warn(FENCE_HOOK_USAGE)
        _warn("sbe_fence_hook fences: unrecognized flag %r; refusing rather than "
              "reading it as a directory" % argv[0])
        return 2
    cwd = argv[0] if argv else os.getcwd()
    _warn("registry patterns: %s" % ", ".join(registry_patterns(cwd)))
    try:
        found = read_fences(cwd)
    except OpenFail as e:
        _warn("no fence is enforceable here, so every write would be ALLOWED. "
              "Reason: %s" % e)
        return 0
    for n in found.notes:
        _warn(n)
    for f in found.fences:
        _warn("LIVE %s | agent %s | session %s | files %s"
              % (os.path.basename(f.registry), f.agent, f.session or "(none)",
                 ", ".join(f.files)))
    _warn("%d live fence line(s) enforceable from %s" % (len(found.fences), cwd))
    return 0


def cmd_audit(argv):
    """Diagnostics: how many live fence lines this hook's own grammar cannot
    parse (a live-looking bullet with no readable `files:` scope), across
    every registry `_scan_registries` reads. No second parser: this is the
    same walk `read_fences` runs, read before its raise-on-nothing-usable
    logic applies, so an all-malformed registry still reports a count instead
    of degrading to the same OpenFail a genuinely empty one would raise.

    Prints the count to stdout (the one line a script parses), the offending
    lines to stderr, and exits 0 always: this is a count, not a gate, so
    "unenforceable lines exist" is not by itself a process failure here."""
    if argv and argv[0].startswith("-"):
        _warn(FENCE_HOOK_USAGE)
        _warn("sbe_fence_hook audit: unrecognized flag %r; refusing rather than "
              "reading it as a directory" % argv[0])
        return 2
    cwd = argv[0] if argv else os.getcwd()
    fences, notes, opened, unreadable, denied_dirs = _scan_registries(cwd)
    if denied_dirs or unreadable:
        _warn("registries could not all be opened; unenforceable-line count would "
              "read as covering paths never entered: denied %s, unreadable %s"
              % (denied_dirs or "none", unreadable or "none"))
        _out("NO-DATA: %d registry path(s) could not be opened" % (len(denied_dirs) + len(unreadable)))
        return 1
    if not opened:
        _out("NO-DATA: no fence registry was opened under %s" % cwd)
        return 0
    unenforceable = [n for n in notes if _UNENFORCEABLE_MARK in n]
    for n in unenforceable:
        _warn(n)
    _out("%d unenforceable fence line(s) in %d registry file(s) read from %s"
         % (len(unenforceable), opened, cwd))
    return 0


_COMMANDS = {
    "hook": cmd_hook,
    "fences": cmd_fences,
    "audit": cmd_audit,
}

FENCE_HOOK_USAGE = (
    "usage: sbe_fence_hook.py [hook|fences [directory]|audit [directory]]\n"
    "  hook (or no subcommand): the Claude Code PreToolUse hook; reads one JSON\n"
    "    payload from stdin, prints its decision to stdout, and FAILS OPEN.\n"
    "  fences [directory]: diagnostics on stderr, the live fences enforceable\n"
    "    from the directory (default: the current one).\n"
    "  audit [directory]: counts live fence lines this hook's grammar cannot\n"
    "    parse (no readable `files:` scope), printed to stdout; NO-DATA when\n"
    "    no registry was opened.\n"
    "  flags:\n"
    "    -h, --help        print this and exit 0 without reading stdin"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        # Help is not an error and exits 0 without reading stdin. It goes to
        # stderr like every other diagnostic here, because stdout is the
        # decision channel and a usage text there would corrupt the protocol
        # if this ever ran in the hook slot with a stray flag.
        _warn(FENCE_HOOK_USAGE)
        return 0
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    if argv and not argv[0].startswith("-"):
        _warn("sbe_fence_hook: unknown command %r; expected one of: %s"
              % (argv[0], ", ".join(sorted(_COMMANDS))))
        return 2
    # No subcommand is the HOOK invocation, because that is how Claude Code calls
    # it: a bare command with a JSON payload on stdin.
    return cmd_hook(argv)


if __name__ == "__main__":
    sys.exit(main())
