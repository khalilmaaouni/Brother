#!/usr/bin/env python3
"""BrotherSBE Stop hook and CI backstop: the AUTHORITATIVE half of the write
boundary (release blocker 1 items 1.4 and 1.5, BR-1014).

WHY THIS EXISTS
  `tools/sbe_bash_write_guard.py` reads command TEXT. Text cannot show what a
  generated script, a compiled tool, a here-document, a process substitution
  or a path computed at run time will do, and the spec says so in its own
  words: "Do not attempt to solve this only by parsing shell syntax ... The
  authoritative fix must inspect the actual repository changes, not only the
  command text."

  This file inspects the changes. It runs as the Claude Code `Stop` hook, so
  it sees the working tree as the session is trying to finish, and it runs in
  CI as `sbe scope verify --base <ref> --head HEAD --strict`, so a committed
  change is judged by the same rules whether or not Claude Code was ever
  installed on the machine that made it.

WHICH DIRECTION THIS FILE FAILS, AND WHY IT IS THE OPPOSITE OF ITS SIBLINGS
  `sbe_fence_hook.py` and `sbe_authority_hook.py` sit in front of the
  keyboard, so they fail OPEN: a hook that failed closed on its own bug would
  stop the operator from working at all.

  This one runs at the END. Failing open here means a session ends with an
  undeclared protected change sitting in the tree and nothing said, which is
  the exact defect the blocker names. So this file FAILS CLOSED on the four
  conditions spec 1.4 item 9 lists:

      1. A protected path was positively identified and is undeclared.
      2. The task registry is unreadable.
      3. The baseline is missing or corrupt.
      4. The repository root changed.

  Blocking on a missing baseline has one carve-out, and it is stated here
  rather than discovered later: when the working tree holds NO change at all
  relative to HEAD, there is nothing to attribute and nothing to be out of
  scope, so a missing baseline allows and says so on stderr. A missing
  baseline with a dirty tree blocks. That keeps "the agent deleted the
  baseline" from becoming permission (spec 1.4, last paragraph) without
  blocking every session that starts on a machine where SessionStart never
  ran and nothing was changed.

WHAT COUNTS AS DECLARED
  Three things, and nothing else:
    1. An open task in `.sbe/tasks.json` whose `ownedPaths` covers the path.
    2. A LIVE fence in the fence registry (STATE.md) naming THIS session as
       sole writer and covering the path.
    3. A valid record in `.sbe/break-glass.json`: a reason, an owner, an
       expiry that has not passed, a named approver, and the path listed.
       This is spec 1.4 item 10's "explicit reviewed break glass record". It
       is reported as BREAK GLASS in the output, never as a clean result.

THE NAMED LIMIT
  A NON-protected path outside every declaration is a violation only when the
  session is operating under a declaration at all (at least one open task).
  With an empty task registry, an ordinary interactive session has declared
  nothing, is outside nothing, and is not blocked for editing its own source
  files. Protected paths block regardless. This is the same worker-context
  reasoning `tools/sbe_authority_hook.py::_worker_context` states for the
  write-time guard, applied at the end of the session instead of the start of
  a write, and it is a HEURISTIC about intent, never a proof.

REPEAT SUPPRESSION (2026-08-29 amendment, measured in a live session)
  A Stop hook that blocks does not end the turn: the model receives the reason
  and continues, and the next Stop runs this hook again. When the finding
  cannot be resolved by this session (a peer's legitimate work in a shared
  tree, with none of the three recoveries usable), an unchanged finding
  therefore re-blocked every turn, dozens of times, each block identical to
  the last. The documented Stop contract even names this failure mode
  (`stop_hook_active` exists to break such loops).

  So the HOOK path remembers its last full report per session, in a small
  state file OUTSIDE the repository (a state file inside the tree would itself
  become a finding). An identical violation set inside the cooldown is not
  re-blocked; it is noted in one stderr line and the session may stop. The
  full block fires on the FIRST occurrence, on ANY change to the set (a path
  added or cleared, or a rule changed), after the cooldown, and for every
  fail-closed Unusable condition, which this suppression never touches. A
  clean stop deletes the memory, so a finding resolved and later re-made is
  new information and blocks in full. Any error reading or writing the state
  fails toward blocking. `verify` and `report` never suppress.

Python 3.9, standard library only, no network. Subprocess is used for git and
nothing else. No em or en dashes anywhere in this file or its output.
"""
import datetime
import hashlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
ROOT_DIR = os.path.dirname(HERE)

BREAK_GLASS_REL = ".sbe/break-glass.json"

#: How long an identical, already-reported finding set stays suppressed before
#: the full block is asserted again. Long enough to end the every-turn loop,
#: short enough that a standing violation is never silent for a whole session.
REPEAT_COOLDOWN_S = 1800

#: Override for tests: where the per-session repeat state lives.
STATE_DIR_ENV = "BROTHERSBE_RECONCILE_STATE_DIR"


def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


class Unusable(Exception):
    """A fail-CLOSED condition: something this file needed to make a decision
    could not be read, so no decision can be made and the session is blocked
    with the reason. Named rather than returned so no code path can forget to
    check a sentinel and accidentally allow."""


# ---------------------------------------------------------------------------
# Shared helpers, loaded by path. Never a bare import: tools/ is not a package
# and this hook runs with an arbitrary cwd. Same construction and same reason
# as tools/sbe_authority_hook.py and tools/sbe_bash_write_guard.py.
# ---------------------------------------------------------------------------

_LOADED = {}


def _load(alias, path):
    if alias in _LOADED:
        return _LOADED[alias]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            raise ImportError("no import spec for %s" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(alias, mod)
        _LOADED[alias] = mod
    except Exception as e:
        raise Unusable(
            "%s could not be imported (%s: %s), and this reconciler holds no private "
            "copy of it on purpose, so it cannot judge anything"
            % (path, type(e).__name__, e))
    return _LOADED[alias]


def baseline_mod():
    return _load("sbe_session_baseline_for_reconcile",
                 os.path.join(HERE, "sbe_session_baseline.py"))


def guard_mod():
    return _load("sbe_bash_write_guard_for_reconcile",
                 os.path.join(HERE, "sbe_bash_write_guard.py"))


def fence_mod():
    return _load("sbe_fence_hook_for_reconcile",
                 os.path.join(HERE, "sbe_fence_hook.py"))


def surface_mod():
    return _load("sbe_instruction_surface_for_reconcile",
                 os.path.join(HERE, "sbe_instruction_surface.py"))


def authority_mod():
    return _load("sbe_authority_hook_for_reconcile",
                 os.path.join(HERE, "sbe_authority_hook.py"))


def tasks_mod():
    return _load("brothersbe_tasks_for_reconcile",
                 os.path.join(ROOT_DIR, "src", "brothersbe", "tasks.py"))


def family_of(root, rel):
    """The protected family this path belongs to, or "".

    One function, in the Bash guard, used by both halves of the boundary. The
    write-time refusal and the end-of-session refusal must never disagree
    about what protected means, and two lists is how they would."""
    return guard_mod().protected_family(
        fence_mod(), surface_mod(), authority_mod(), root, rel)


# ---------------------------------------------------------------------------
# Declarations.
# ---------------------------------------------------------------------------

class BreakGlass(object):
    """The break-glass records, valid and invalid, kept apart.

    An invalid record is NAMED in the report and never honored. A record that
    is missing its owner or whose expiry has passed is not a weaker exception,
    it is no exception, and reporting it as merely absent would let an expired
    waiver look like a clean tree."""

    def __init__(self, valid, refused, note):
        self.valid = valid
        self.refused = refused
        self.note = note


def _today():
    return datetime.datetime.utcnow().date()


def read_break_glass(root):
    path = os.path.join(root, BREAK_GLASS_REL.replace("/", os.sep))
    if not os.path.exists(path):
        return BreakGlass([], [], "")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as e:
        # NOT a fail-closed condition and NOT an exemption: an unreadable
        # break-glass file grants nothing, which is the safe direction here,
        # and the reason is printed so it cannot be mistaken for absence.
        return BreakGlass([], [], "%s exists and could not be read (%s: %s), so it "
                                  "grants nothing" % (BREAK_GLASS_REL, type(e).__name__, e))
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return BreakGlass([], [], "%s carries no `records` list, so it grants nothing"
                          % BREAK_GLASS_REL)
    valid, refused = [], []
    for rec in records:
        if not isinstance(rec, dict):
            refused.append("a record that is not a JSON object")
            continue
        missing = [k for k in ("reason", "owner", "expiry")
                   if not str(rec.get(k) or "").strip()]
        approval = rec.get("approval") if isinstance(rec.get("approval"), dict) else {}
        if not str(approval.get("approver") or "").strip():
            missing.append("approval.approver")
        if not isinstance(rec.get("paths"), list) or not rec.get("paths"):
            missing.append("paths")
        if missing:
            refused.append("record %r is missing %s"
                           % (rec.get("id") or "(unnamed)", ", ".join(sorted(set(missing)))))
            continue
        try:
            expiry = datetime.datetime.strptime(
                str(rec.get("expiry")).strip(), "%Y-%m-%d").date()
        except ValueError:
            refused.append("record %r has an expiry that is not YYYY-MM-DD (%r)"
                           % (rec.get("id") or "(unnamed)", rec.get("expiry")))
            continue
        if expiry < _today():
            refused.append("record %r expired on %s"
                           % (rec.get("id") or "(unnamed)", expiry.isoformat()))
            continue
        valid.append(rec)
    return BreakGlass(valid, refused, "")


class Declarations(object):
    """Everything that can legitimately cover a changed path, read once."""

    def __init__(self, root, session_id, live_tasks, fences, break_glass):
        self.root = root
        self.session_id = session_id
        self.live_tasks = live_tasks
        self.fences = fences
        self.break_glass = break_glass

    def covering_rule(self, rel):
        """The declaration that covers this path, or "" when none does. The
        STRING is the answer, because the report has to name which rule let a
        change through as precisely as it names which rule refused one."""
        fh = fence_mod()
        for t in self.live_tasks:
            for claim in t.get("ownedPaths") or []:
                if fh.paths_overlap(rel, claim, self.root):
                    return "open task %s ownedPaths %s" % (t.get("id") or "(unnamed)", claim)
        for fence in self.fences:
            if not fh.same_session(fence.session, self.session_id):
                continue
            for claim in fence.files:
                if fh.paths_overlap(rel, claim, self.root):
                    return "a live fence in %s naming this session, files %s" % (
                        os.path.basename(fence.registry), claim)
        for rec in self.break_glass.valid:
            for claim in rec.get("paths") or []:
                if fh.paths_overlap(rel, str(claim), self.root):
                    return ("BREAK GLASS record %s (owner %s, expiry %s, approver %s), "
                            "which is an exception and is never a clean result"
                            % (rec.get("id") or "(unnamed)", rec.get("owner"),
                               rec.get("expiry"),
                               (rec.get("approval") or {}).get("approver")))
        return ""


def read_declarations(root, session_id):
    tm = tasks_mod()
    try:
        data = tm.load_registry(root)
    except tm.RegistryUnusable as e:
        raise Unusable(
            "the task registry could not be read (%s). This is a fail-closed condition: "
            "a registry that does not parse still records somebody's declared scope, and "
            "treating it as empty would turn a broken control into permission." % e)
    live = tm.open_tasks(data)
    try:
        fences = fence_mod().read_fences(root, root).fences
    except Exception:
        # No enforceable fence registry is not a failure here: the task
        # registry is this project's structured declaration and the fence
        # registry is the hand-written one. Absence of the second narrows what
        # can be declared; it never widens what is allowed.
        fences = []
    return Declarations(root, session_id, live, fences, read_break_glass(root))


# ---------------------------------------------------------------------------
# Attribution: which surviving changes belong to THIS session.
# ---------------------------------------------------------------------------

class SessionChange(object):
    """One surviving change attributed to this session, and why it was
    attributed rather than written off as pre-session dirt."""

    def __init__(self, path, status, kind, why):
        self.path = path
        self.status = status
        self.kind = kind
        self.why = why


def attribute(current, baseline):
    """The subset of `current` this session is responsible for.

    Three rules, and the second is the one spec fixtures 11 and 12 are about:

      1. A path git does not report is not a surviving change at all. A file
         that was dirty at session start and has since been RESTORED to HEAD
         no longer appears here, which is correct: restoring the file is one
         of the three recovery paths, and a recovery that still counted as a
         violation would be no recovery.
      2. A path that was already dirty at session start is pre-session dirt
         ONLY while its status, its file kind and its CONTENT DIGEST are all
         unchanged. Change any of the three and it is this session's, which is
         how "a preexisting dirty file changed again during the session is
         attributed to the session" is decided by content rather than by a
         name being on a list.
      3. Everything else is this session's.
    """
    prior = {}
    for entry in baseline.get("initialChangedPaths") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            prior[entry["path"]] = entry
    out = []
    for entry in current:
        was = prior.get(entry.path)
        if was is None:
            out.append(SessionChange(entry.path, entry.status, entry.kind,
                                     "not changed at session start"))
        elif (was.get("digest") == entry.digest and was.get("status") == entry.status
                and was.get("kind") == entry.kind):
            continue
        else:
            detail = []
            if was.get("digest") != entry.digest:
                detail.append("content changed since session start (%s -> %s)"
                              % (str(was.get("digest"))[:12], str(entry.digest)[:12]))
            if was.get("status") != entry.status:
                detail.append("git status changed (%r -> %r)"
                              % (was.get("status"), entry.status))
            if was.get("kind") != entry.kind:
                detail.append("file kind changed (%s -> %s)"
                              % (was.get("kind"), entry.kind))
            out.append(SessionChange(entry.path, entry.status, entry.kind,
                                     "; ".join(detail)))
        if entry.renamed_from:
            # Both sides of a rename are surviving changes: the old name is
            # gone and the new name is new, and naming only one of them would
            # let "modify a protected file and then rename it" report half the
            # story (spec fixture 6).
            out.append(SessionChange(entry.renamed_from, entry.status, "absent",
                                     "renamed to %s during this session" % entry.path))
    return out


# ---------------------------------------------------------------------------
# The judgement.
# ---------------------------------------------------------------------------

class Violation(object):
    def __init__(self, path, rule, detail):
        self.path = path
        self.rule = rule
        self.detail = detail

    def line(self):
        return "%s | rule: %s | %s" % (self.path, self.rule, self.detail)


class Outcome(object):
    """The reconciler's answer. An object, not a pair, for the reason every
    sibling states: this project's honesty meta-test reads a 2-tuple return as
    a possible verdict source, and this is not a check verdict.

    `blocked` is the decision. `reason` is why, in full, for a human. Both are
    set together and neither is ever set alone."""

    def __init__(self, blocked, reason, violations, notes):
        self.blocked = blocked
        self.reason = reason
        self.violations = violations
        self.notes = notes


def judge(root, session_id, changes, declarations, source):
    """Turn a set of attributed changes into an Outcome."""
    violations, notes = [], []
    scoped = bool(declarations.live_tasks)
    for change in changes:
        if change.path.replace(os.sep, "/") == BREAK_GLASS_REL:
            # The break-glass record itself is exempt from being a violation,
            # and it is NEVER silent: an exception that flagged itself could
            # not be used, and one nobody printed would be an exception nobody
            # reviewed.
            notes.append("%s changed during this session; every break-glass record it "
                         "carries is listed below." % BREAK_GLASS_REL)
            continue
        family = family_of(root, change.path)
        covered = declarations.covering_rule(change.path)
        if family and not covered:
            violations.append(Violation(
                change.path,
                "protected control-plane or authority surface (%s) changed with no open "
                "task, no fence for this session, and no valid break-glass record" % family,
                change.why))
            continue
        if family and covered:
            notes.append("%s is protected (%s) and is declared by %s"
                         % (change.path, family, covered))
            continue
        if scoped and not covered:
            violations.append(Violation(
                change.path,
                "outside every open task's declared ownedPaths",
                change.why))
            continue
        if not scoped and not covered:
            notes.append(
                "%s changed and is not protected; no task is open, so this session "
                "declared no scope for it to be outside of. This is a heuristic about "
                "intent, not a proof: see the module docstring." % change.path)
    for refused in declarations.break_glass.refused:
        notes.append("break-glass record REFUSED and granting nothing: %s" % refused)
    if declarations.break_glass.note:
        notes.append(declarations.break_glass.note)
    if not violations:
        return Outcome(False, "", [], notes)
    return Outcome(True, blocking_reason(violations, source), violations, notes)


def blocking_reason(violations, source):
    listed = "\n".join("  %d. %s" % (i + 1, v.line()) for i, v in enumerate(violations[:40]))
    more = ("\n  ... and %d more" % (len(violations) - 40)) if len(violations) > 40 else ""
    return (
        "BrotherSBE scope reconciliation: %d change(s) in %s are outside the scope this "
        "session declared. Every one is named with the rule it violated:\n%s%s\n"
        "This is the authoritative write boundary: it reads the repository changes that "
        "actually survived, not the commands that produced them, so a generated script, "
        "an interpreter, a rename, a deletion or a symlink reaches it the same way an "
        "edit does.\n"
        "Three recoveries exist, and nothing else does:\n"
        "  1. RESTORE the file: git restore -- <path> (or git checkout -- <path>) for a "
        "tracked change, rm for an undeclared new file. A path restored to its HEAD "
        "content stops being a surviving change and this hook stops naming it.\n"
        "  2. DECLARE the path: sbe task open --id <id> --agent <agent> --role writer "
        "--base $(git rev-parse HEAD) --verify <command> --owns <path>, or open a live "
        "fence in STATE.md naming this session as sole writer for it.\n"
        "  3. BREAK GLASS, for a deliberate reviewed exception: add a record to "
        ".sbe/break-glass.json carrying a reason, an owner, an expiry (YYYY-MM-DD, in "
        "the future), a named approver, and the exact paths. It is reported as BREAK "
        "GLASS in every run, never as a clean result."
        % (len(violations), source, listed, more))


# ---------------------------------------------------------------------------
# Working-tree mode: the Stop hook.
# ---------------------------------------------------------------------------

def reconcile_worktree(cwd, session_id):
    """The Outcome for the current working tree. Raises Unusable for the three
    fail-closed conditions that are not per-path violations."""
    bl = baseline_mod()
    try:
        root = bl.repo_root(cwd)
    except bl.BaselineUnavailable as e:
        # Not a repository at all. Nothing to reconcile and nothing to claim:
        # this is the one place an absence is genuinely NOT a control failure,
        # because BrotherSBE governs a repository and there is none.
        return Outcome(False, "", [], [
            "sbe_session_reconcile: %s, so there is no repository to reconcile. Nothing "
            "was checked and nothing is claimed." % e])
    try:
        current = bl.changed_entries(root)
    except bl.BaselineUnavailable as e:
        raise Unusable("the current changed-path set could not be computed (%s), so no "
                       "change could be judged" % e)

    try:
        baseline = bl.read_baseline(cwd, session_id)
    except bl.BaselineUnavailable as e:
        if not current:
            return Outcome(False, "", [], [
                "sbe_session_reconcile: %s, and the working tree holds no change relative "
                "to HEAD, so there is nothing to attribute and nothing to be out of "
                "scope. Nothing was checked and nothing is claimed." % e])
        raise Unusable(
            "%s, and the working tree holds %d changed path(s). Without the baseline "
            "there is no way to tell which of them this session made, so none of them "
            "can be cleared. A missing or deleted baseline is a fail-closed condition, "
            "never permission.\n"
            "Recovery: restore the working tree to a clean state, or declare the changed "
            "paths in .sbe/tasks.json and record a break-glass entry in "
            ".sbe/break-glass.json explaining why this session ran without a baseline."
            % (e, len(current)))

    recorded_root = str(baseline.get("repositoryRoot") or "")
    if os.path.realpath(recorded_root) != os.path.realpath(root):
        raise Unusable(
            "the baseline was written for repository root %r and this session is "
            "finishing in %r. A control that measured one tree cannot clear another, so "
            "this is a fail-closed condition." % (recorded_root, root))

    declarations = read_declarations(root, session_id)

    registry_file = tasks_mod().registry_path(root)
    if (baseline.get("openTasks") and not os.path.exists(registry_file)):
        raise Unusable(
            "the session opened with %d open task(s) recorded in the baseline and %s no "
            "longer exists. Deleting the registry that declares scope is not a way to "
            "have no scope; this is a fail-closed condition."
            % (len(baseline.get("openTasks") or []), registry_file))

    changes = attribute(current, baseline)
    outcome = judge(root, session_id, changes, declarations, "the working tree")
    outcome.notes.insert(0,
                         "sbe_session_reconcile: %d changed path(s) in the working tree, "
                         "%d attributed to this session after subtracting pre-session dirt."
                         % (len(current), len(changes)))
    return outcome


# ---------------------------------------------------------------------------
# Range mode: the CI backstop, `sbe scope verify --base <ref> --head HEAD`.
# ---------------------------------------------------------------------------

def range_changes(root, base, head):
    """Every path a commit range touched, read null delimited, renames
    detected so both names are judged.

    `git diff --name-status -z -M` emits STATUS then PATH as separate
    NUL-terminated fields, and a rename or copy emits two paths. The parse
    below is the same shape as the porcelain reader in
    tools/sbe_session_baseline.py and for the same reason: a path holding a
    newline must not become two paths that do not exist."""
    bl = baseline_mod()
    for ref in (base, head):
        probe = bl.git(["rev-parse", "--verify", "%s^{commit}" % ref], root)
        if probe.code != 0:
            raise Unusable(
                "NO-DATA: %r does not resolve to a commit in %s (%s). Nothing was "
                "compared, so this run is neither a pass nor a block; give a base that "
                "exists." % (ref, root, probe.err or "no such revision"))
    r = bl.git(["diff", "--name-status", "-z", "-M", base, head], root)
    if r.code != 0:
        raise Unusable("NO-DATA: git diff %s..%s failed in %s (%s); nothing was compared"
                       % (base, head, root, r.err))
    fields = [f for f in r.out.split("\0")]
    out, i = [], 0
    while i < len(fields):
        status = fields[i].strip()
        i += 1
        if not status:
            continue
        if status[0] in ("R", "C"):
            if i + 1 >= len(fields):
                break
            old, new = fields[i], fields[i + 1]
            i += 2
            out.append(SessionChange(new, status, "file",
                                     "%s from %s in %s..%s" % (status, old, base, head)))
            out.append(SessionChange(old, status, "absent",
                                     "%s to %s in %s..%s" % (status, new, base, head)))
            continue
        if i >= len(fields):
            break
        path = fields[i]
        i += 1
        out.append(SessionChange(path, status, "file", "%s in %s..%s" % (status, base, head)))
    return out


def verify_range(root, base, head):
    """The Outcome for a commit range. Same judgement, same declarations, no
    baseline: CI must not depend on Claude Code having run locally, so nothing
    here reads a session baseline or a session id."""
    changes = range_changes(root, base, head)
    declarations = read_declarations(root, "")
    outcome = judge(root, "", changes, declarations, "%s..%s" % (base, head))
    outcome.notes.insert(0, "sbe scope verify: %d changed path(s) between %s and %s."
                         % (len(changes), base, head))
    return outcome


# ---------------------------------------------------------------------------
# Repeat suppression: the Stop hook's memory of its own last full report.
# Module docstring, REPEAT SUPPRESSION section, carries the design and the
# reasons. Everything here fails toward blocking: an unreadable state never
# suppresses, an unwritable state merely means the next identical finding
# blocks in full again.
# ---------------------------------------------------------------------------

def _repeat_state_dir():
    override = os.environ.get(STATE_DIR_ENV, "").strip()
    if override:
        return override
    return brother_paths.config_path("sbe-reconcile-state")


def _repeat_state_path(session_id):
    digest = hashlib.sha256(("session:%s" % session_id).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_repeat_state_dir(), "%s.json" % digest)


def finding_fingerprint(violations):
    """One stable name for a violation SET: the sorted (path, rule) pairs.

    The pairs and not the details, deliberately: a detail carries content
    digests that churn while a peer keeps editing the same file, and a finding
    is unchanged in every way that matters while the same paths violate the
    same rules."""
    key = json.dumps(sorted((v.path, v.rule) for v in violations))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def repeat_verdict(session_id, violations):
    """(suppress, note): whether this exact finding set was already reported
    in full this session and is still inside the cooldown. When it was not,
    the state is updated to say a full report is being made now."""
    fingerprint = finding_fingerprint(violations)
    state_path = _repeat_state_path(session_id)
    state = {}
    try:
        with io.open(state_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        state = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        state = {}
    try:
        last_full = float(state.get("lastFullReport") or 0.0)
    except (TypeError, ValueError):
        last_full = 0.0
    now = time.time()
    if (state.get("fingerprint") == fingerprint and last_full > 0.0
            and 0.0 <= now - last_full < REPEAT_COOLDOWN_S):
        return True, (
            "scope reconciliation UNCHANGED: %d path(s) are still outside declared "
            "scope, identical to the finding already reported in full this session. "
            "Not re-blocking an unchanged finding; the full block returns the moment "
            "any path is added or cleared, or after the cooldown (%d s remaining). "
            "Paths: %s"
            % (len(violations), int(REPEAT_COOLDOWN_S - (now - last_full)),
               ", ".join(sorted(v.path for v in violations)[:10])))
    try:
        state_dir = os.path.dirname(state_path)
        os.makedirs(state_dir, exist_ok=True)
        tmp = state_path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"sessionId": session_id, "fingerprint": fingerprint,
                       "lastFullReport": now,
                       "paths": sorted(v.path for v in violations)[:40]}, fh)
        os.replace(tmp, state_path)
        # ponytail: prune-on-write keeps the state directory bounded; a
        # scheduled cleaner is a second moving part this does not need.
        for name in os.listdir(state_dir):
            old = os.path.join(state_dir, name)
            if name.endswith(".json") and now - os.path.getmtime(old) > 7 * 86400:
                os.remove(old)
    except OSError as e:
        _warn("sbe_session_reconcile: the repeat-suppression state could not be "
              "written (%s: %s). Safe direction: without it the next identical "
              "finding simply blocks in full again." % (type(e).__name__, e))
    return False, ""


def forget_repeat_state(session_id):
    """A clean stop clears the memory: a finding resolved and later re-made is
    new information and earns the full block again."""
    try:
        os.remove(_repeat_state_path(session_id))
    except OSError:  # sbe: allow-silent best-effort clear of a memory file whose absence IS the desired state
        pass


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------

def block_payload(reason):
    """The Stop hook's blocking decision.

    `{"decision": "block", "reason": ...}` is the documented Stop contract:
    the reason is fed back to the model so it can act on it, which is exactly
    what the three recovery paths in the reason are for. The extra
    hookSpecificOutput object carries the same facts in a machine-readable
    shape for anything that wants them; a host that does not know the key
    ignores it."""
    return {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {"hookEventName": "Stop",
                               "brothersbeControl": "scope-reconciliation"},
    }


def _load_sbe_repo_scope():
    """Load sbe_repo_scope.py by path, works from any cwd and never
    raises. E76 per-repository hook scoping, checked before the
    BROTHERSBE_RECONCILE_OFF escape hatch: a repository turned off is off
    for every hook, not just this one's own private switch."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "sbe_repo_scope_for_reconcile", os.path.join(HERE, "sbe_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def cmd_hook(argv):
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception as e:
        raw = ""
        _warn("sbe_session_reconcile: stdin could not be read (%s: %s)"
              % (type(e).__name__, e))
    payload = {}
    if raw and raw.strip():
        try:
            loaded = json.loads(raw)
            payload = loaded if isinstance(loaded, dict) else {}
        except ValueError as e:
            _warn("sbe_session_reconcile: stdin was not valid JSON (%s)" % e)
    session_id = payload.get("session_id")
    session_id = session_id.strip() if isinstance(session_id, str) else ""
    if not session_id:
        session_id = os.environ.get("BROTHERSBE_SESSION_ID", "").strip()
    cwd = payload.get("cwd")
    cwd = cwd.strip() if isinstance(cwd, str) and cwd.strip() else os.getcwd()

    _rs = _load_sbe_repo_scope()
    if _rs is not None and _rs.hooks_off(payload=payload, cwd=cwd):
        return 0

    if os.environ.get("BROTHERSBE_RECONCILE_OFF", "").strip() not in ("", "0"):
        _warn("sbe_session_reconcile: BROTHERSBE_RECONCILE_OFF is set, so the "
              "authoritative write boundary did NOT run and this session is allowed to "
              "finish unchecked. Unset it to restore enforcement.")
        return 0

    try:
        outcome = reconcile_worktree(cwd, session_id)
    except Unusable as e:
        _out(json.dumps(block_payload(
            "BrotherSBE scope reconciliation could not clear this session, so it is "
            "blocked rather than allowed. Reason: %s" % e)))
        return 0
    except Exception as e:
        # An unexpected error here is still a fail-CLOSED condition, unlike in
        # the write-time hooks: a reconciler that crashed and allowed would be
        # indistinguishable from one that ran and found nothing, which is the
        # exact confusion this file exists to remove.
        _out(json.dumps(block_payload(
            "BrotherSBE scope reconciliation failed unexpectedly (%s: %s) and therefore "
            "cleared nothing. It blocks rather than allowing, because a control that "
            "crashed has not checked anything. Re-run `sbe scope report` to see the "
            "error in full." % (type(e).__name__, e))))
        return 0
    for n in outcome.notes:
        _warn("sbe_session_reconcile: %s" % n)
    if not outcome.blocked:
        forget_repeat_state(session_id)
        return 0
    if outcome.violations:
        suppress, note = repeat_verdict(session_id, outcome.violations)
        if suppress:
            _warn("sbe_session_reconcile: %s" % note)
            return 0
    _out(json.dumps(block_payload(outcome.reason)))
    return 0


def _flag_value(argv, name, default=""):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def cmd_verify(argv):
    """The CI backstop. Exit 0 clean, 1 when --strict and a violation exists,
    2 when nothing could be compared (NO-DATA, which is neither a pass nor a
    block and must never be read as either)."""
    strict = "--strict" in argv
    base = _flag_value(argv, "--base")
    head = _flag_value(argv, "--head", "HEAD")
    path = _flag_value(argv, "--path", ".")
    if not base:
        _warn(RECONCILE_USAGE)
        _warn("sbe scope verify: --base is required; there is nothing to compare against "
              "without it")
        return 2
    try:
        root = baseline_mod().repo_root(path)
    except Exception as e:
        _warn("sbe scope verify: NO-DATA, %s. Nothing was compared, so this is neither a "
              "pass nor a block." % e)
        return 2
    try:
        outcome = verify_range(root, base, head)
    except Unusable as e:
        _warn("sbe scope verify: %s" % e)
        return 2
    for n in outcome.notes:
        _warn("sbe scope verify: %s" % n)
    if not outcome.violations:
        _warn("sbe scope verify: no change between %s and %s is outside declared scope, "
              "and no protected path changed undeclared. This says nothing about whether "
              "the change is correct; it says only that it stayed inside what was "
              "declared." % (base, head))
        return 0
    for v in outcome.violations:
        _warn("sbe scope verify: VIOLATION %s" % v.line())
    _warn("sbe scope verify: %d violation(s)%s"
          % (len(outcome.violations),
             "" if strict else "; --strict was not given, so this run exits 0 and the "
                               "violations above are advisory"))
    return 1 if strict else 0


def cmd_report(argv):
    """Diagnostics on stderr: what the Stop hook would decide right now."""
    session_id = _flag_value(argv, "--session",
                             os.environ.get("BROTHERSBE_SESSION_ID", ""))
    path = _flag_value(argv, "--path", os.getcwd())
    try:
        outcome = reconcile_worktree(path, session_id)
    except Unusable as e:
        _warn("sbe scope report: WOULD BLOCK. %s" % e)
        return 1
    for n in outcome.notes:
        _warn("sbe scope report: %s" % n)
    if outcome.blocked:
        for line in outcome.reason.splitlines():
            _warn("sbe scope report: %s" % line)
        return 1
    _warn("sbe scope report: nothing attributed to session %r is outside declared scope."
          % session_id)
    return 0


_COMMANDS = {"hook": cmd_hook, "verify": cmd_verify, "report": cmd_report}

RECONCILE_USAGE = (
    "usage: sbe_session_reconcile.py [hook|verify|report]\n"
    "  hook (or no subcommand): the Claude Code Stop hook; reads one JSON payload from\n"
    "    stdin and prints a blocking decision to stdout when a surviving change is\n"
    "    outside declared scope. FAILS CLOSED.\n"
    "  verify --base REF [--head REF] [--path DIR] [--strict]: the CI backstop over a\n"
    "    commit range. Exit 0 clean, 1 with --strict and a violation, 2 for NO-DATA.\n"
    "  report [--session ID] [--path DIR]: what the Stop hook would decide right now,\n"
    "    on stderr.\n"
    "  flags:\n"
    "    -h, --help        print this and exit 0 without reading stdin"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        _warn(RECONCILE_USAGE)
        return 0
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    if argv and not argv[0].startswith("-"):
        _warn("sbe_session_reconcile: unknown command %r; expected one of: %s"
              % (argv[0], ", ".join(sorted(_COMMANDS))))
        return 2
    if argv:
        # A flag this surface does not know is refused, never dropped and read
        # as a bare hook invocation: `sbe scope --typo` used to reach the Stop
        # hook, block on a missing baseline, and exit 0, which taught a caller
        # that a typo was a control decision.
        _warn(RECONCILE_USAGE)
        _warn("sbe_session_reconcile: unrecognized flag %r; refusing rather than reading "
              "it as a hook invocation" % argv[0])
        return 2
    return cmd_hook(argv)


if __name__ == "__main__":
    sys.exit(main())
