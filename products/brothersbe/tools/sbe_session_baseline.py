#!/usr/bin/env python3
"""BrotherSBE session baseline: what the working tree looked like BEFORE this
session touched it (release blocker 1 item 1.2, BR-1014).

WHY THIS EXISTS
  `tools/sbe_session_reconcile.py`, the Stop hook, is the authoritative half
  of the write boundary: it judges the repository changes that actually
  survived a session rather than the commands that were typed. To do that it
  has to answer one question the working tree cannot answer on its own:

      Which of these changes did THIS SESSION make?

  A repository is routinely dirty before a session starts. Attributing that
  dirt to the session would refuse every session that opened on an unfinished
  branch, and the reconciler would be useless within a day. Ignoring a
  pre-existing dirty file forever would be the opposite mistake: an agent
  could hide a protected write inside a file that happened to be dirty
  already. So the baseline records not only WHICH paths were dirty but WHAT
  THEY CONTAINED, as a digest, and the reconciler compares content rather
  than a path list. That is the difference between spec fixture 11 (a dirty
  file left untouched is not attributed) and fixture 12 (the same file
  changed again IS attributed), and it is why this file hashes.

WHERE IT LIVES, AND WHY THERE
  `<git-dir>/brothersbe/sessions/<session-id>/baseline.json`, where <git-dir>
  is `git rev-parse --absolute-git-dir`, so a linked worktree gets its own
  baseline under `.git/worktrees/<name>/` instead of sharing the primary
  tree's. Inside the git directory rather than the working tree on purpose:
  it is not a source file, it must never appear in `git status` (a control
  that dirties the tree it is measuring is a control that reports itself),
  and it is out of the way of every glob this project ships.

  A baseline the agent can delete is a control the agent can remove. This
  file cannot prevent that, and does not pretend to: `sbe_session_reconcile`
  treats an ABSENT baseline as a fail-closed condition rather than as
  permission, which is the half of the problem that is solvable here.

NULL DELIMITED GIT OUTPUT, EVERYWHERE
  Every git invocation that returns paths uses `-z`. A path containing a
  newline is legal on every filesystem this project supports, and a
  line-oriented reader would split one changed file into two paths that do
  not exist, which is exactly the shape a hostile write would take to hide.

Python 3.9, standard library only, no network. Subprocess is used for git and
nothing else. No em or en dashes anywhere in this file or its output.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)

SCHEMA_VERSION = "1.0"

#: Beyond this, a file's content is not digested and the entry records why.
#: A baseline that tried to hash a multi-gigabyte artifact would stall
#: SessionStart, which must be fast and must always exit 0.
MAX_DIGEST_BYTES = 64 * 1024 * 1024

#: Session ids from the harness are UUID shaped. Anything else is still
#: accepted, because refusing to write a baseline is worse than writing one
#: under a safe name, but it is sanitized so it can never escape the
#: directory it belongs in.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


class GitResult(object):
    """One git invocation. An object rather than a tuple, because this
    project's honesty meta-test reads a 2-tuple return as a possible verdict
    source and a subprocess result is not one."""

    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err


def git(args, cwd):
    """Run git and hand back its result. Never raises: a machine with no git
    on PATH is a baseline that cannot be written, which every caller here
    reports rather than crashing on."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True)
    except OSError as e:
        return GitResult(127, "", "git could not be run (%s: %s)" % (type(e).__name__, e))
    return GitResult(p.returncode,
                     p.stdout.decode("utf-8", "surrogateescape"),
                     p.stderr.decode("utf-8", "surrogateescape").strip())


class BaselineUnavailable(Exception):
    """The baseline cannot be written or read at all, with the reason. Named
    rather than returned, so no caller can forget to check a sentinel."""


def repo_root(cwd):
    r = git(["rev-parse", "--show-toplevel"], cwd)
    if r.code != 0 or not r.out.strip():
        raise BaselineUnavailable(
            "%s is not inside a git repository (%s)" % (cwd, r.err or "no toplevel"))
    return os.path.realpath(r.out.strip())


def git_dir(cwd):
    r = git(["rev-parse", "--absolute-git-dir"], cwd)
    if r.code != 0 or not r.out.strip():
        raise BaselineUnavailable(
            "the git directory for %s could not be resolved (%s)" % (cwd, r.err))
    return r.out.strip()


def head_commit(cwd):
    """The commit the session opened on, or "" in a repository with no commit
    yet. Empty is honest here and is recorded as empty: a repository before
    its first commit has no HEAD, and inventing one would make every later
    comparison a comparison against a lie."""
    r = git(["rev-parse", "HEAD"], cwd)
    if r.code != 0:
        return ""
    return r.out.strip()


def safe_session_id(session_id):
    """A session id usable as one directory name. An id that is not obviously
    safe is replaced by a digest of itself, so two different unusual ids can
    never collide onto one baseline."""
    sid = (session_id or "").strip()
    if not sid:
        return ""
    if _SAFE_ID.match(sid):
        return sid
    return "sha256-" + hashlib.sha256(sid.encode("utf-8", "surrogateescape")).hexdigest()[:32]


def baseline_dir(cwd, session_id):
    sid = safe_session_id(session_id)
    if not sid:
        raise BaselineUnavailable("no session id was given, so no baseline can be named")
    return os.path.join(git_dir(cwd), "brothersbe", "sessions", sid)


def baseline_path(cwd, session_id):
    return os.path.join(baseline_dir(cwd, session_id), "baseline.json")


# ---------------------------------------------------------------------------
# The changed-path set, read null delimited.
# ---------------------------------------------------------------------------

class ChangedEntry(object):
    """One path git reports as changed, with everything needed to tell later
    whether it changed AGAIN."""

    def __init__(self, path, status, kind, digest, renamed_from):
        self.path = path
        self.status = status
        self.kind = kind
        self.digest = digest
        self.renamed_from = renamed_from

    def as_dict(self):
        return {"path": self.path, "status": self.status, "kind": self.kind,
                "digest": self.digest, "renamedFrom": self.renamed_from}


def entry_kind(root, rel):
    """file, symlink, directory, or absent. Recorded because a path that
    stops being a file and starts being a symlink is a change no content
    digest alone would name, and spec 1.4 item 4 asks for file type changes
    by name."""
    full = os.path.join(root, rel.replace("/", os.sep))
    try:
        if os.path.islink(full):
            return "symlink"
        if not os.path.lexists(full):
            return "absent"
        if os.path.isdir(full):
            return "directory"
        return "file"
    except OSError:
        return "unreadable"


def digest_of(root, rel):
    """A sha256 over what this path currently holds, or a short marker saying
    why there is none.

    A symlink is digested over its TARGET, not over the file it points at: a
    symlink repointed from a harmless file to CLAUDE.md is a change to the
    symlink, and following it would have digested the destination and called
    the repoint invisible."""
    full = os.path.join(root, rel.replace("/", os.sep))
    try:
        if os.path.islink(full):
            return "symlink:" + hashlib.sha256(
                os.readlink(full).encode("utf-8", "surrogateescape")).hexdigest()
        if not os.path.exists(full):
            return "absent"
        if os.path.isdir(full):
            return "directory"
        if os.path.getsize(full) > MAX_DIGEST_BYTES:
            return "too-large"
        h = hashlib.sha256()
        with open(full, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        return "unreadable:%s" % type(e).__name__


def changed_entries(root):
    """Every path `git status --porcelain=v1 -z -uall` reports, as
    ChangedEntry objects.

    -z, so a path holding a newline or a quote arrives intact instead of
    being split into paths that do not exist. -uall, so an untracked
    directory is listed as its FILES rather than collapsed to a directory
    entry: a collapsed `dir/` would compare as a claim over the whole
    directory that nobody typed, which is `src/brothersbe/tasks.py`'s own
    stated reason for the same flag."""
    r = git(["status", "--porcelain=v1", "-z", "-uall"], root)
    if r.code != 0:
        raise BaselineUnavailable("git status failed in %s: %s" % (root, r.err))
    fields = r.out.split("\0")
    entries, i = [], 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:
            continue
        status = field[:2]
        path = field[3:]
        renamed_from = ""
        if "R" in status or "C" in status:
            # Porcelain v1 with -z emits the ORIGINAL path as its own
            # NUL-terminated field immediately after a rename or copy record.
            if i < len(fields):
                renamed_from = fields[i]
                i += 1
        entries.append(ChangedEntry(path, status, entry_kind(root, path),
                                    digest_of(root, path), renamed_from))
    return entries


# ---------------------------------------------------------------------------
# The declared scope, as it stood at session start.
# ---------------------------------------------------------------------------

def _load_by_path(alias, path):
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(alias, mod)
        return mod
    except Exception:
        # A helper that will not load costs this file a FIELD, never the
        # baseline. The reconciler re-reads both registries live anyway; these
        # fields are the record of what was declared when the session opened.
        return None


def open_task_records(root):
    """The open tasks at session start, reduced to what a later reader needs.
    An unreadable registry yields an empty list here and is NOT hidden: the
    reconciler reads the registry itself and fails closed when it cannot."""
    mod = _load_by_path("brothersbe_tasks_for_baseline",
                        os.path.join(ROOT_DIR, "src", "brothersbe", "tasks.py"))
    if mod is None:
        return []
    try:
        data = mod.load_registry(root)
        return [{"id": t.get("id"), "agent": t.get("agent"),
                 "ownedPaths": list(t.get("ownedPaths") or [])}
                for t in mod.open_tasks(data)]
    except Exception:
        return []


def fenced_paths(root):
    """Every file scope declared by a live fence in the registries the fence
    hook itself reads, so the baseline records the fences as they stood."""
    mod = _load_by_path("sbe_fence_hook_for_baseline",
                        os.path.join(HERE, "sbe_fence_hook.py"))
    if mod is None:
        return []
    try:
        found = mod.read_fences(root, root)
    except Exception:
        return []
    out = []
    for fence in found.fences:
        for claim in fence.files:
            if claim not in out:
                out.append(claim)
    return out


def protected_patterns():
    """The authority and control-plane families, taken from the Bash guard so
    the two halves of this boundary can never disagree about what protected
    means. Returns a dict with the two lists the baseline schema names."""
    guard = _load_by_path("sbe_bash_write_guard_for_baseline",
                          os.path.join(HERE, "sbe_bash_write_guard.py"))
    surface = _load_by_path("sbe_instruction_surface_for_baseline",
                            os.path.join(HERE, "sbe_instruction_surface.py"))
    if guard is None or surface is None:
        return {"authorityPaths": [], "controlPlanePaths": []}
    return {"authorityPaths": list(guard.authority_patterns(surface)),
            "controlPlanePaths": list(guard.CONTROL_PLANE_PATTERNS)}


# ---------------------------------------------------------------------------
# Writing and reading the baseline.
# ---------------------------------------------------------------------------

def build_baseline(cwd, session_id):
    root = repo_root(cwd)
    patterns = protected_patterns()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": session_id,
        "repositoryRoot": root,
        "headCommit": head_commit(root),
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "initialChangedPaths": [e.as_dict() for e in changed_entries(root)],
        "openTasks": open_task_records(root),
        "allowedOwnedPaths": sorted(set(
            p for t in open_task_records(root) for p in t.get("ownedPaths") or [])),
        "fencedPaths": fenced_paths(root),
        "authorityPaths": patterns["authorityPaths"],
        "controlPlanePaths": patterns["controlPlanePaths"],
    }


def write_baseline(cwd, session_id):
    """Write the baseline and hand back its path.

    Written once per session and NEVER overwritten: a second SessionStart in
    the same session (a resume, a compaction restart) must not re-baseline a
    tree the session has already been writing to, because that would launder
    every change made so far into pre-session dirt. The existing file wins and
    the caller is told so."""
    path = baseline_path(cwd, session_id)
    if os.path.exists(path):
        return path
    data = build_baseline(cwd, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        raise BaselineUnavailable(
            "the baseline could not be written to %s (%s: %s)"
            % (path, type(e).__name__, e))
    return path


def read_baseline(cwd, session_id):
    """The baseline for this session, or BaselineUnavailable naming why not.
    A file that exists and does not parse is refused with its reason and is
    NEVER replaced: a corrupt baseline is the fail-closed condition spec 1.4
    item 9.3 names, and quietly rewriting it would turn the missing control
    into permission."""
    path = baseline_path(cwd, session_id)
    if not os.path.exists(path):
        raise BaselineUnavailable("no baseline exists at %s" % path)
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as e:
        raise BaselineUnavailable(
            "%s exists and does not parse (%s: %s)" % (path, type(e).__name__, e))
    if not isinstance(data, dict):
        raise BaselineUnavailable("%s is not a JSON object" % path)
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise BaselineUnavailable(
            "%s declares schemaVersion %r, and this reader knows %r only"
            % (path, data.get("schemaVersion"), SCHEMA_VERSION))
    for key in ("sessionId", "repositoryRoot", "initialChangedPaths"):
        if key not in data:
            raise BaselineUnavailable("%s is missing the required key %r" % (path, key))
    if not isinstance(data.get("initialChangedPaths"), list):
        raise BaselineUnavailable("%s: initialChangedPaths is not a list" % path)
    return data


# ---------------------------------------------------------------------------
# Entry points. Called from tools/sbe_sessionstart.py with the SessionStart
# payload on stdin. Everything goes to stderr, because SessionStart's stdout
# is injected into the session context and a baseline path is not context.
# ---------------------------------------------------------------------------

def _stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _session_and_cwd(argv):
    """The session id and working directory, from the flags if given, else
    from the hook payload on stdin, else from the environment. Returns a dict
    rather than a pair, for the reason stated on GitResult."""
    session_id, cwd = "", ""
    rest = list(argv)
    while rest:
        a = rest.pop(0)
        if a == "--session" and rest:
            session_id = rest.pop(0)
        elif a == "--cwd" and rest:
            cwd = rest.pop(0)
    if not session_id or not cwd:
        payload = _stdin_json()
        if not session_id:
            sid = payload.get("session_id")
            session_id = sid.strip() if isinstance(sid, str) else ""
        if not cwd:
            c = payload.get("cwd")
            cwd = c.strip() if isinstance(c, str) and c.strip() else ""
    if not session_id:
        session_id = os.environ.get("BROTHERSBE_SESSION_ID", "").strip()
    if not cwd:
        cwd = os.getcwd()
    return {"session_id": session_id, "cwd": cwd}


def cmd_write(argv):
    args = _session_and_cwd(argv)
    if not args["session_id"]:
        _warn("sbe_session_baseline: no session id was available (no --session, no "
              "session_id in the payload, no BROTHERSBE_SESSION_ID), so no baseline "
              "was written. The Stop reconciler will treat that as a missing baseline "
              "and fail closed on any surviving change.")
        return 0
    try:
        path = write_baseline(args["cwd"], args["session_id"])
    except BaselineUnavailable as e:
        _warn("sbe_session_baseline: no baseline was written (%s). The Stop reconciler "
              "fails closed on a missing baseline rather than treating it as "
              "permission." % e)
        return 0
    _warn("sbe_session_baseline: baseline at %s" % path)
    return 0


def cmd_show(argv):
    args = _session_and_cwd(argv)
    try:
        data = read_baseline(args["cwd"], args["session_id"])
    except BaselineUnavailable as e:
        _warn("sbe_session_baseline: %s" % e)
        return 1
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return 0


_COMMANDS = {"write": cmd_write, "show": cmd_show}

BASELINE_USAGE = (
    "usage: sbe_session_baseline.py [write|show] [--session ID] [--cwd DIR]\n"
    "  write: record the pre-session working-tree state under\n"
    "    <git-dir>/brothersbe/sessions/<session-id>/baseline.json. Always exits 0:\n"
    "    it is called from SessionStart, which must never fail a session.\n"
    "  show:  print the baseline as JSON, or exit 1 with the reason there is none.\n"
    "  With no flags, the session id and cwd are read from the SessionStart hook\n"
    "  payload on stdin."
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        _warn(BASELINE_USAGE)
        return 0
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    if argv and not argv[0].startswith("-"):
        _warn("sbe_session_baseline: unknown command %r; expected one of: %s"
              % (argv[0], ", ".join(sorted(_COMMANDS))))
        return 2
    return cmd_write(argv)


if __name__ == "__main__":
    sys.exit(main())
