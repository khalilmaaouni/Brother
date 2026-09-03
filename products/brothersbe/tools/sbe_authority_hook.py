#!/usr/bin/env python3
"""BrotherSBE PreToolUse authority-file guard: LT-402.

WHY THIS EXISTS
  `tools/sbe_instruction_surface.py` (LT-401) proves, AFTER a commit lands,
  whether an authority-bearing file (CLAUDE.md, .claude/**, .mcp.json,
  .claude-plugin/**, hooks/**, agents/*.md, skills/*/SKILL.md, CODEOWNERS,
  .github/workflows/**) changed outside a declared, reviewed scope. That is a
  git-history check: it names the defect once the commit already exists. This
  file is the same question asked BEFORE the write, at the one moment a
  PreToolUse hook can still refuse it: a dispatched worker editing one of
  those files without having declared it in the task it was given would be
  quietly widening its own authority, and the after-the-fact check would only
  ever learn about it once that was already true.

  This is deliberately narrower than `tools/sbe_fence_hook.py`. The fence hook
  enforces "one writer per file" against a hand-written markdown registry for
  EVERY file a fence names. This file enforces "an authority-bearing file may
  only move inside a task's own declared `ownedPaths`" against the structured
  wave-5 task registry (`.sbe/tasks.json`, `src/brothersbe/tasks.py`), and
  ONLY for the nine authority-surface families. It does not replace the fence
  hook and is not replaced by it; the two run side by side, named as separate
  entries in `hooks/hooks.json`.

THE SAME LAW THE FENCE HOOK STATES, WITH ONE NAMED EXCEPTION
  Every failure path in this file (an unreadable or corrupt task registry, an
  unimportable helper, an unparseable payload, no target path, any unexpected
  exception) FAILS OPEN, allows the write, and prints why. That part is
  identical to `tools/sbe_fence_hook.py` and for the identical reason: a hook
  that fails closed on its own bug stops the operator's own work, which is
  worse than no hook at all.

  The one exception, and it is the whole point of this file: when the target
  is POSITIVELY IDENTIFIED as an authority-bearing surface, the registry was
  read successfully, and no OPEN task's declared `ownedPaths` covers it, this
  is not an error. It is the rule doing its job, and the rule fails CLOSED:
  the write is refused. Nothing else in this file ever denies a write.

REUSE, NOT A SECOND COPY
  The nine-family authority-surface list lives in exactly one place,
  `tools/sbe_instruction_surface.py::_matched_surface`, and this file imports
  it rather than re-typing it, so the check that runs after a commit and the
  guard that runs before a write can never drift into two different lists.
  The write-tool surface, target-path extraction, path canonicalization, and
  the case-insensitive-filesystem confirmation are the same story a second
  time: all four are `tools/sbe_fence_hook.py`'s, imported here rather than
  copied. The task-scope reader (`load_registry`, `open_tasks`) is
  `src/brothersbe/tasks.py`'s own, imported for the same reason: the registry
  shape a human writes with `sbe task open` and the registry shape this hook
  reads must be the one shape, not two that happen to agree today.

  Every one of those imports is by file path, deferred into a function, never
  a bare `import` at the top of this file. `tools/` is not a package and this
  hook is invoked by Claude Code with an arbitrary working directory, so a
  bare `import sbe_fence_hook` would resolve against sys.path and could pick
  up a different checkout entirely. An import failure at any of the three is
  a FAIL-OPEN printed to stderr, never a traceback Claude Code would surface
  as a broken hook in front of every edit.

THE WORKER-CONTEXT SIGNAL, STATED AS A LIMIT RATHER THAN A PROMISE
  When an authority file is undeclared and NO open task exists at all (an
  absent or empty registry), refusing every such edit unconditionally would
  block the ordinary case this project runs in every day: a human operator,
  in an interactive session, editing CLAUDE.md directly, with no task
  registry in play at all. So this file only refuses an UNDECLARED authority
  edit when a "worker context" is detectable, and states plainly what that
  detection is and is not: `_worker_context` below, and
  `docs/KNOWN-LIMITS.md`. This is a heuristic, not a proof, and the file says
  so everywhere the heuristic decides something.

Python 3.9, standard library only, cross-platform, no network, no subprocess.
No em or en dashes anywhere in this file, its comments, or its output.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)


# ---------------------------------------------------------------------------
# Output funnels. Stdout carries the decision JSON and nothing else; every
# diagnostic goes to stderr. Mirrors tools/sbe_fence_hook.py exactly, for the
# identical reason: Claude Code parses stdout as JSON, so a bare `print`
# anywhere in this file would be a channel a value could climb through.
# ---------------------------------------------------------------------------

def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


class OpenFail(Exception):
    """Raised anywhere a decision cannot be made SAFELY.

    Always caught at the top of decide(); always produces an ALLOW plus a
    stderr line naming the reason. A named exception rather than a returned
    sentinel, so a new code path cannot forget to check the sentinel and
    accidentally deny."""


# ---------------------------------------------------------------------------
# Shared helpers, loaded by path. See the module docstring for why this is
# never a bare `import`.
# ---------------------------------------------------------------------------

_FENCE_MOD = None
_FENCE_MOD_ERROR = None
_SURFACE_MOD = None
_SURFACE_MOD_ERROR = None
_TASKS_MOD = None
_TASKS_MOD_ERROR = None


class _LoadResult(object):
    """The outcome of loading one sibling module by path: `mod`, or `error`
    naming why not. An object rather than a (mod, error) tuple, for the same
    reason `Decision` and `StdinPayload` below are: this project's honesty
    meta-test reads a 2-tuple return as a possible verdict source, and a
    module-or-reason is not one."""

    def __init__(self, mod, error):
        self.mod = mod
        self.error = error


def _load_by_path(mod_name, filename):
    """One `.py` file beside (or below) this one, loaded under a private
    module name so it can never collide with a same-named module already on
    sys.path from a different checkout. Never raises itself."""
    try:
        import importlib.util
        path = os.path.join(HERE, filename)
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return _LoadResult(None, "no import spec for %s" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(mod_name, mod)
        return _LoadResult(mod, None)
    except Exception as e:
        return _LoadResult(None, "%s: %s" % (type(e).__name__, e))


def load_fence_module():
    """`tools/sbe_fence_hook.py`, for WRITE_TOOLS, extract_targets,
    canonical_target, paths_overlap, normalize_claim and
    _same_entry_case_insensitive. Cached; never raises."""
    global _FENCE_MOD, _FENCE_MOD_ERROR
    if _FENCE_MOD is not None or _FENCE_MOD_ERROR is not None:
        return _FENCE_MOD
    result = _load_by_path("sbe_fence_hook_for_authority_hook", "sbe_fence_hook.py")
    _FENCE_MOD, _FENCE_MOD_ERROR = result.mod, result.error
    return _FENCE_MOD


def load_surface_module():
    """`tools/sbe_instruction_surface.py`, for `_matched_surface` and the
    three literal tuples it is built from. Cached; never raises."""
    global _SURFACE_MOD, _SURFACE_MOD_ERROR
    if _SURFACE_MOD is not None or _SURFACE_MOD_ERROR is not None:
        return _SURFACE_MOD
    result = _load_by_path(
        "sbe_instruction_surface_for_authority_hook", "sbe_instruction_surface.py")
    _SURFACE_MOD, _SURFACE_MOD_ERROR = result.mod, result.error
    return _SURFACE_MOD


def load_guard_module():
    """`tools/sbe_bash_write_guard.py`, for CONTROL_PLANE_PATTERNS: the one
    list of grant-and-control files (task registry, break-glass, approvals,
    the guard tools themselves, STATE.md). Reused by path so the Bash channel
    and this structured-write channel can never protect two different lists;
    a hostile refuter proved the gap by granting itself CLAUDE.md through a
    tasks.json rewrite this hook allowed. Cached; never raises."""
    global _GUARD_MOD, _GUARD_MOD_ERROR
    if _GUARD_MOD is not None or _GUARD_MOD_ERROR is not None:
        return _GUARD_MOD
    result = _load_by_path(
        "sbe_bash_write_guard_for_authority_hook", "sbe_bash_write_guard.py")
    _GUARD_MOD, _GUARD_MOD_ERROR = result.mod, result.error
    return _GUARD_MOD


_GUARD_MOD = None
_GUARD_MOD_ERROR = None


def control_plane_surface(fence_mod, guard_mod, root, rel):
    """The control-plane pattern `rel` falls under, or "" for none. Matching
    is fence_mod.paths_overlap, the same comparison the Bash guard uses for
    the same list, so the two channels cannot drift."""
    if guard_mod is None:
        return ""
    for pattern in getattr(guard_mod, "CONTROL_PLANE_PATTERNS", ()):
        if fence_mod.paths_overlap(rel, pattern, root):
            return "control-plane file (%s)" % pattern
    return ""


def load_tasks_module():
    """`src/brothersbe/tasks.py`, for `load_registry`, `open_tasks`,
    `registry_path` and `RegistryUnusable`. Cached; never raises."""
    global _TASKS_MOD, _TASKS_MOD_ERROR
    if _TASKS_MOD is not None or _TASKS_MOD_ERROR is not None:
        return _TASKS_MOD
    path = os.path.join(ROOT_DIR, "src", "brothersbe", "tasks.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "brothersbe_tasks_for_authority_hook", path)
        if spec is None or spec.loader is None:
            _TASKS_MOD_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("brothersbe_tasks_for_authority_hook", mod)
        _TASKS_MOD = mod
        return _TASKS_MOD
    except Exception as e:
        _TASKS_MOD_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


def require_fence_module():
    mod = load_fence_module()
    if mod is None:
        raise OpenFail(
            "tools/sbe_fence_hook.py could not be imported (%s), and this hook holds no "
            "private copy of its write-tool surface or path rules on purpose, so it cannot "
            "tell which paths a write tool targets" % _FENCE_MOD_ERROR)
    return mod


def require_surface_module():
    mod = load_surface_module()
    if mod is None:
        raise OpenFail(
            "tools/sbe_instruction_surface.py could not be imported (%s), and this hook "
            "holds no private copy of the authority-surface list on purpose, so it cannot "
            "tell which paths are authority-bearing" % _SURFACE_MOD_ERROR)
    return mod


def require_tasks_module():
    mod = load_tasks_module()
    if mod is None:
        raise OpenFail(
            "src/brothersbe/tasks.py could not be imported (%s), and this hook holds no "
            "private copy of the task-registry reader on purpose, so it cannot tell what "
            "scope any task has declared" % _TASKS_MOD_ERROR)
    return mod


# ---------------------------------------------------------------------------
# The disable switch. Same shape as sbe_fence_hook.py's own: set, and the
# guard says so on stderr on every write it would otherwise have checked, so
# the bypass is never silent.
# ---------------------------------------------------------------------------

DISABLE_ENV = "BROTHERSBE_AUTHORITY_HOOK_OFF"


# ---------------------------------------------------------------------------
# Case-insensitive filesystem hazard for the authority-surface match itself.
#
# `_matched_surface` compares exact spellings on purpose (CLAUDE.md is not
# claude.md; that is the surface list's own design, not a bug). But a write
# aimed at "claude.md" on a case-insensitive volume (the macOS default) can
# land on the SAME on-disk entry as an existing "CLAUDE.md" regardless of
# what string the tool call spelled, and a check that only ever compares
# strings would miss it: exactly the hazard tools/sbe_fence_hook.py's own
# `paths_overlap` already handles for scope overlap, and the technique is
# reused here rather than re-derived (`_same_entry_case_insensitive`, which
# confirms two spellings name one entry by inode where both exist, or by
# probing the volume itself when they do not, and never trusts a case-folded
# STRING match alone).
# ---------------------------------------------------------------------------

_KNOWN_SEGMENTS = None


def _known_segments(surface_mod):
    """{lowercased path segment: its canonical spelling}, built once from the
    literal tuples `_matched_surface` itself is built from, plus the fixed
    literals its docstring names (CLAUDE.md, agents, skills, SKILL.md,
    .github, workflows) that do not live in a named tuple. Covers exactly the
    segments the nine detected families are spelled with; a case-folded
    hazard in a segment none of the nine families ever name is out of scope
    by the same logic `_matched_surface` itself uses to decide what counts."""
    global _KNOWN_SEGMENTS
    if _KNOWN_SEGMENTS is not None:
        return _KNOWN_SEGMENTS
    seg = {}
    for d in surface_mod.AUTHORITY_TOP_DIRS:
        seg[d.lower()] = d
    for f in surface_mod.AUTHORITY_ROOT_FILES:
        seg[f.lower()] = f
    for p in surface_mod.AUTHORITY_CODEOWNERS_PATHS:
        for part in p.split("/"):
            seg[part.lower()] = part
    seg["claude.md"] = "CLAUDE.md"
    seg["agents"] = "agents"
    seg["skills"] = "skills"
    seg["skill.md"] = "SKILL.md"
    seg[".github"] = ".github"
    seg["workflows"] = "workflows"
    _KNOWN_SEGMENTS = seg
    return seg


def _case_fold_candidate(surface_mod, rel):
    """`rel` with every path segment that case-insensitively matches a known
    authority-surface segment replaced by that segment's canonical spelling,
    or "" when no segment needed replacing (nothing to fold)."""
    seg = _known_segments(surface_mod)
    parts = rel.split("/")
    changed = False
    fixed = []
    for p in parts:
        canon = seg.get(p.lower())
        if canon is not None and canon != p:
            changed = True
            fixed.append(canon)
        else:
            fixed.append(p)
    if not changed:
        return ""
    return "/".join(fixed)


def confirmed_surface(fence_mod, surface_mod, root, rel):
    """The authority surface `rel` matches, exact spelling first. Failing
    that, the case-folded candidate spelling IF the filesystem confirms the
    two name one entry, never on the string fold alone. Returns "" when
    neither is an authority surface."""
    surface = surface_mod._matched_surface(rel)
    if surface:
        return surface
    candidate = _case_fold_candidate(surface_mod, rel)
    if not candidate or candidate == rel:
        return ""
    candidate_surface = surface_mod._matched_surface(candidate)
    if not candidate_surface:
        return ""
    if fence_mod._same_entry_case_insensitive(root, rel, candidate):
        return candidate_surface
    return ""


# ---------------------------------------------------------------------------
# Worker-context detection. Read the module docstring's "WORKER-CONTEXT
# SIGNAL" section before changing anything below; this is a heuristic and it
# says so at every call site.
# ---------------------------------------------------------------------------

def _worker_context(root, live_tasks):
    """Best-effort evidence that this session is a dispatched worker rather
    than an ad hoc interactive one, so an UNDECLARED authority-file edit is
    worth refusing at all. Two independent signals, EITHER is sufficient:

      1. At least one task is open in the registry, regardless of whether it
         covers the file in question: evidence the wave-5 task-registry
         workflow (`sbe task open`) is in active use for this repository
         right now.
      2. `root` itself is a linked git worktree: its `.git` entry is a FILE
         (`gitdir: <path>`) rather than a directory, the shape `git worktree
         add` leaves behind and the shape this project's own dispatch model
         uses to isolate a worker (`sbe task open --worktree`).

    NEITHER IS PROOF. A worker sharing the primary tree, with zero currently
    open task records (its own task already closed, or it never ran `sbe
    task open` at all), is indistinguishable from a human editing directly by
    either signal, and an undeclared authority-file edit in that shape is
    ALLOWED, not refused. This is named in full, with its consequence, in
    docs/KNOWN-LIMITS.md rather than implied by this function's behavior
    alone."""
    if live_tasks:
        return True
    git_entry = os.path.join(root, ".git")
    try:
        return os.path.isfile(git_entry)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# The decision.
# ---------------------------------------------------------------------------

def deny_payload(reason):
    """The deny object, shaped exactly as the PreToolUse contract requires
    and exactly as tools/sbe_fence_hook.py already emits it, because the two
    hooks share one wire contract."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def refusal_reason(target, surface, live_tasks):
    """The refusal. Names the task state (an open task exists but does not
    declare this path, or none is open at all), the path, and a recovery
    action that actually works."""
    if live_tasks:
        names = ", ".join(sorted(str(t.get("id") or "(unnamed task)") for t in live_tasks))
        task_state = ("%d open task(s) exist (%s) and none declares %s in its ownedPaths"
                      % (len(live_tasks), names, target))
    else:
        task_state = "no task is open"
    return (
        "BrotherSBE authority-file guard: %s matches a detected authority surface (%s). %s. "
        "Editing an authority-bearing file with no declared task would let this session "
        "widen its own authority silently; tools/sbe_instruction_surface.py catches that "
        "after the commit lands, and this hook stops it before the write instead.\n"
        "Recovery: open a task record naming this path, or edit outside a worker context.\n"
        "Example: sbe task open --id <id> --agent <agent> --role writer "
        "--base $(git rev-parse HEAD) --verify <command> --owns %s"
        % (target, surface, task_state, target))


class Decision(object):
    """The hook's answer. An object, not a (verdict, evidence) tuple, for the
    same reason tools/sbe_fence_hook.py's own Decision is: this project's
    honesty meta-test reads a 2-tuple return as a possible verdict source,
    and a permission decision is not a check verdict.

    `payload` is None for ALLOW and for FAIL-OPEN, deliberately the same
    value, so no failure path can produce a deny by accident."""

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

        try:
            fence_mod = require_fence_module()
        except OpenFail:
            # For every tool but one, an unimportable fence module fails open
            # like every other failure here. apply_patch is the one tool whose
            # targets live in text only that module can parse, so "could not
            # tell" must not become "allowed" merely because the parser is
            # missing: that would let a broken checkout read as a quieter one.
            if tool_name == "apply_patch":
                return Decision(deny_payload(
                    "this apply_patch call is denied rather than assumed safe: "
                    "tools/sbe_fence_hook.py could not be imported, so this "
                    "guard cannot establish which files the patch touches, and "
                    "it holds no patch parser of its own on purpose. Repair "
                    "the checkout, or use Edit/Write, whose targets are "
                    "structured."), notes)
            raise

        if not isinstance(tool_name, str) or tool_name not in fence_mod.WRITE_TOOLS:
            # Not a file-writing tool. Silent, matching sbe_fence_hook.py: the
            # common case is every Read, Grep, Bash and TodoWrite, and a
            # stderr line per call would be noise the operator learns to
            # ignore.
            return Decision(None, [])

        if os.environ.get(DISABLE_ENV, "").strip() not in ("", "0"):
            return Decision(None, [
                "sbe_authority_hook: %s is set, so the authority-file guard was NOT checked "
                "and this write is allowed. Unset it to restore enforcement." % DISABLE_ENV])

        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            raise OpenFail("tool_input for %s was not a JSON object" % tool_name)

        # Targets come from the fence module's ONE dispatch, targets_for, so
        # this hook and the fence hook can never disagree about which files a
        # tool touches. apply_patch is the tool that forced this: its targets
        # live in free-form patch text, extract_targets returns nothing for
        # it, and until 2026-08-11 that nothing fell into the fail-open below,
        # leaving authority files invisible to this guard on exactly the path
        # the fence hook had already learned to parse. For apply_patch,
        # "could not tell" must not mean "allowed": a patch the shared parser
        # cannot fully read, or a fence module too old to carry the dispatch,
        # is a DENY here, mirroring the fence hook's own one deliberate
        # fail-closed exception.
        targets_dispatch = getattr(fence_mod, "targets_for", None)
        if targets_dispatch is None:
            if tool_name == "apply_patch":
                return Decision(deny_payload(
                    "this apply_patch call is denied rather than assumed safe: "
                    "tools/sbe_fence_hook.py in this checkout carries no "
                    "targets_for dispatch, so this guard cannot establish which "
                    "files the patch touches, and it holds no patch parser of "
                    "its own on purpose. Update the checkout so both hooks "
                    "share one parser."), notes)
            raw_targets = fence_mod.extract_targets(tool_input)
        else:
            try:
                raw_targets = targets_dispatch(tool_name, tool_input)
            except Exception as e:
                # ANY failure to extract apply_patch targets is a deny, not
                # just the parser's own PatchDeny class: a hostile or broken
                # fence module raising TypeError, or a PatchDeny from a
                # different module object, must not slide into the generic
                # fail-open below. Exception identity is not a security
                # boundary; the tool name is.
                if tool_name == "apply_patch":
                    return Decision(deny_payload(
                        "this apply_patch call is denied rather than assumed "
                        "safe: %s: %s. The authority guard cannot protect "
                        "authority-bearing files inside a patch it cannot "
                        "fully read." % (type(e).__name__, e)), notes)
                raise
        if not raw_targets:
            raise OpenFail(
                "no target path found in tool_input for %s (keys present: %s)"
                % (tool_name, ", ".join(sorted(str(k) for k in tool_input)) or "none"))

        cwd = payload.get("cwd")
        cwd = cwd.strip() if isinstance(cwd, str) and cwd.strip() else None
        root = payload.get("project_dir")
        root = root.strip() if isinstance(root, str) and root.strip() else (
            cwd or os.getcwd())
        # A payload naming a project but no cwd resolves relative paths
        # against the PROJECT, never against wherever this hook process
        # happens to be running: the process cwd is an accident of the
        # harness, and resolving against it let a relative patch path land
        # outside the checks entirely.
        if cwd is None:
            cwd = root

        surface_mod = require_surface_module()
        tasks_mod = require_tasks_module()
        guard_mod = load_guard_module()
        if guard_mod is None:
            notes.append(
                "sbe_authority_hook: tools/sbe_bash_write_guard.py could not "
                "be imported (%s), so control-plane files are NOT checked on "
                "this call; the authority surface is still enforced."
                % _GUARD_MOD_ERROR)

        try:
            data = tasks_mod.load_registry(root)
        except tasks_mod.RegistryUnusable as e:
            raise OpenFail(
                "the task registry could not be read (%s), so this session's declared scope "
                "is unknown" % e)
        live = tasks_mod.open_tasks(data)
        worker = _worker_context(root, live)

        for raw in raw_targets:
            rel = fence_mod.canonical_target(root, raw, cwd)
            if rel is None:
                # Outside the project root. This hook guards a project, not
                # the filesystem, same as the fence hook.
                continue
            surface = confirmed_surface(fence_mod, surface_mod, root, rel)
            if not surface:
                surface = control_plane_surface(fence_mod, guard_mod, root, rel)
            if not surface:
                continue
            covered = any(
                fence_mod.paths_overlap(rel, claim, root)
                for t in live for claim in (t.get("ownedPaths") or []))
            if covered:
                continue
            if not worker:
                notes.append(
                    "sbe_authority_hook: %s is an authority-bearing file (%s) with no open "
                    "task declaring it, but no worker context was detected under %s (no open "
                    "task at all, and it is not a linked git worktree), so the write is "
                    "ALLOWED. See docs/KNOWN-LIMITS.md for what this signal can and cannot "
                    "detect." % (rel, surface, root))
                continue
            return Decision(deny_payload(refusal_reason(rel, surface, live)), notes)
        return Decision(None, notes)
    except OpenFail as e:
        notes.append(
            "sbe_authority_hook: FAILING OPEN, the write is allowed and the authority-file "
            "guard was NOT checked. Reason: %s" % e)
        return Decision(None, notes)
    except Exception as e:
        # The blanket catch is the point, not laziness: an unforeseen bug in
        # this file must never become a refusal in front of the operator's
        # editing. Type and message, no traceback, so the stderr line stays
        # one readable sentence and the failure is still named rather than
        # swallowed.
        notes.append(
            "sbe_authority_hook: FAILING OPEN after an unexpected error, the write is "
            "allowed and the authority-file guard was NOT checked. Reason: %s: %s"
            % (type(e).__name__, e))
        return Decision(None, notes)


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------

class StdinPayload(object):
    """The parsed hook payload, or the reason there is none. An object rather
    than a (payload, error) pair, for the reason stated on Decision: a
    2-tuple return in this project reads as a possible check verdict, and
    this is a parse result."""

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
        _warn("sbe_authority_hook: FAILING OPEN, the write is allowed and the authority-file "
              "guard was NOT checked. Reason: %s" % parsed.error)
        return 0
    decision = decide(parsed.payload)
    for n in decision.notes:
        _warn(n)
    if decision.payload is not None:
        _out(json.dumps(decision.payload))
    return 0


def cmd_surfaces(argv):
    """Diagnostics: what this hook can see right now, and what it cannot.
    Everything on stderr and nothing on stdout, matching sbe_fence_hook.py's
    own `fences` subcommand, because stdout is the decision channel and a
    diagnostic there would corrupt the protocol if this ever ran in the hook
    slot by mistake."""
    if argv and argv[0].startswith("-"):
        _warn(AUTHORITY_HOOK_USAGE)
        _warn("sbe_authority_hook surfaces: unrecognized flag %r; refusing rather than "
              "reading it as a directory" % argv[0])
        return 2
    root = argv[0] if argv else os.getcwd()
    try:
        require_fence_module()
        require_surface_module()
        tasks_mod = require_tasks_module()
    except OpenFail as e:
        _warn("this hook cannot enforce anything here, so every authority-file write would "
              "be ALLOWED. Reason: %s" % e)
        return 0
    try:
        data = tasks_mod.load_registry(root)
    except tasks_mod.RegistryUnusable as e:
        _warn("the task registry could not be read (%s); every authority-file write would "
              "be ALLOWED" % e)
        return 0
    live = tasks_mod.open_tasks(data)
    worker = _worker_context(root, live)
    _warn("registry: %s" % tasks_mod.registry_path(root))
    _warn("worker context detected: %s" % worker)
    if live:
        for t in live:
            _warn("OPEN task %s | agent %s | owns %s"
                  % (t.get("id"), t.get("agent"),
                     ", ".join(t.get("ownedPaths") or []) or "(none)"))
    _warn("%d open task(s) considered from %s" % (len(live), root))
    return 0


_COMMANDS = {
    "hook": cmd_hook,
    "surfaces": cmd_surfaces,
}

AUTHORITY_HOOK_USAGE = (
    "usage: sbe_authority_hook.py [hook|surfaces [directory]]\n"
    "  hook (or no subcommand): the Claude Code PreToolUse hook; reads one JSON\n"
    "    payload from stdin, prints its decision to stdout, and FAILS OPEN except for the\n"
    "    one refusal this file exists to make.\n"
    "  surfaces [directory]: diagnostics on stderr; the open tasks and worker-context\n"
    "    signal this hook would enforce from the directory (default: the current one).\n"
    "  flags:\n"
    "    -h, --help        print this and exit 0 without reading stdin"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        # Help is not an error and exits 0 without reading stdin, on stderr
        # like every other diagnostic here, because stdout is the decision
        # channel and a usage text there would corrupt the protocol if this
        # ever ran in the hook slot with a stray flag.
        _warn(AUTHORITY_HOOK_USAGE)
        return 0
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    if argv and not argv[0].startswith("-"):
        _warn("sbe_authority_hook: unknown command %r; expected one of: %s"
              % (argv[0], ", ".join(sorted(_COMMANDS))))
        return 2
    # No subcommand is the HOOK invocation, because that is how Claude Code
    # calls it: a bare command with a JSON payload on stdin.
    return cmd_hook(argv)


if __name__ == "__main__":
    sys.exit(main())
