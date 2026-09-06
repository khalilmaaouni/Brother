#!/usr/bin/env python3
"""BrotherMode autosave (Phase 2, ratified design 2026-07-26): mechanical,
verifiable work-preservation. Replaces tools/bm_autosave.sh, removed in this
same change (docs/superpowers/specs/2026-07-26-phase2-recovery-design.md).

WHY THIS EXISTS
  Every "never lose work" rule in the constitution is PROSE the model must
  remember to run, at exactly the moment (running out of tokens, failing
  repeatedly) it is least able to remember anything. This module closes that
  hole mechanically: the harness fires it, not the model. Autosave is the
  component a founder reaches for when everything else has already gone
  wrong, which is why a backup that can silently be empty, or that deletes a
  file on restore, is worse than no backup: it replaces caution with false
  confidence. Seven such defects were reproduced by executing the V1 shell
  script (see the spec's table); this module closes all seven at once.

WHY PYTHON, NOT SHELL (deliberate, ratified)
  Windows support is ratified scope and shell scripts do not run there.

WHY SUBPROCESS IS ALLOWED IN THIS MODULE ONLY (deliberate, documented
exception, NOT a silent widening; tools/test_bm.py's no-network gate bans
`subprocess` in every other tools/*.py on purpose)
  git is the one external binary this project needs to snapshot a working
  tree, and there is no stdlib way to drive it. Every subprocess call in
  this file invokes `git` directly (argv list, never a shell string) with a
  fixed, reviewed argument shape; none of them is push, fetch, pull, clone,
  or remote, so the audited "no network, ever" property still holds: every
  call is local. SECURITY.md should record this same exception (out of this
  change's fence; flagged, not silently assumed).

WHAT CHANGED FROM V1 (closes FA, FC, FD, FE, FI, F2b, and the writer half
of J; see the spec for the full defect table)
  - One fixed ref (refs/brothermode/autosave) is now a NAMESPACE per
    worktree and per session, so linked worktrees of one repository can
    never overwrite each other's only backup (FA).
  - The temporary index is seeded from HEAD (`git read-tree HEAD`) before
    working-tree changes are staged, so a tracked file that secret-exclusion
    skips still carries its last committed content into the snapshot
    instead of vanishing (FC's root cause). Recovery never restores INTO the
    live working tree at all: it checks out the snapshot into a brand new
    `git worktree`, so the old delete-on-restore path cannot exist (FC).
  - A clean working tree (matches HEAD) clears this worktree's "latest"
    pointer instead of leaving it aimed at old, deliberately-discarded WIP
    (FD).
  - Every git call's return code is checked before its output is trusted; a
    failure aborts the whole snapshot BEFORE anything touches the existing
    latest pointer, and the universal empty-tree sha is refused explicitly
    as a second, independent guard (FE).
  - Every environment variable this module reads is parsed defensively:
    unset, zero, negative, non-numeric, and absurdly large all fall back to
    the default with one warning line, and every entrypoint exits 0 no
    matter what (FI).
  - `git rev-parse --show-toplevel` is resolved FIRST, and every git call
    after that runs with `-C <toplevel>`, so a snapshot triggered from a
    subdirectory still covers the whole repository instead of silently
    scoping the "." pathspec to that subdirectory (F2b).
  - A successful snapshot writes one row into the store's autosave_receipts
    table (schema shipped in Phase 1, tools/bm_store.py), giving a separate
    "is this actually saved" reader something true to check instead of
    assuming (the writer half of J; the reader half, the compact-hint
    consumer in tools/bm_telemetry.py, is a different fenced change).

NOTE ON worktree_id (a documented deviation from one literal phrasing in the
design draft, verified by direct execution rather than assumed)
  worktree_id is a short hash of THIS worktree's own resolved toplevel path,
  not of `git rev-parse --git-common-dir`. Common-dir was tried first and
  rejected: it resolves to the SAME shared main .git directory for every
  linked worktree of one repository (confirmed by running `git worktree add`
  and comparing --git-common-dir from both checkouts), so hashing it alone
  cannot be what distinguishes two worktrees, and using it as the sole input
  would reproduce FA rather than close it. The toplevel path is exactly what
  differs between two worktrees of the same repository, and it is stable for
  the life of that worktree, which is what a per-worktree ref namespace and
  "latest" pointer both need.

Python 3.9, standard library only (the one documented subprocess exception
above). No em or en dashes anywhere in this file, its comments, or output.
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Configuration and constants.
# ---------------------------------------------------------------------------
VAULT = os.environ.get("BROTHERMODE_VAULT", os.path.expanduser("~/BrotherModeVault"))
TEL_DIR = os.path.join(VAULT, "99-System", "telemetry")

REF_NAMESPACE = "refs/brothermode/autosave"
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

DEFAULT_TICK_EVERY = 20
DEFAULT_RUNAWAY_AT = 600
DEFAULT_RETAIN = 10

# Secret-shaped paths: excluded from the STAGE step only, never from the
# seed (requirement 5, FC).
#
# FINDING 16 (external audit, 2026-07-27): this used to be EXACTLY the ten
# globs inherited verbatim from the deleted V1 shell script, and enumeration
# alone was the whole boundary. Ten hand-listed shapes cannot enumerate the
# credential formats a real machine carries: id_ed25519 (the modern default
# ssh key, so the DEFAULT key type of every recent OpenSSH install walked
# straight into snapshots while the 2005-era id_rsa was blocked), .npmrc,
# .netrc, cloud credential files, kube configs, and service-account JSON
# were all captured. Autosaved content becomes a git object and can outlive
# ref deletion until garbage collection, so a miss here is not a near miss.
#
# The boundary is now three layers, not one:
#   1. git's OWN ignore status. `git add -A -- .` never stages a path that
#      .gitignore, .git/info/exclude, or the global excludesFile already
#      ignores, so every project's existing secret hygiene is inherited for
#      free. This layer was always true and never tested; it is pinned by a
#      test now, because an untested layer is an assumption.
#   2. The materially expanded default denylist below, pinned by
#      test_bm_autosave.py so it can never silently narrow again.
#   3. BROTHERMODE_AUTOSAVE_EXCLUDE, the founder's own additions, for the
#      shapes no list will ever enumerate (see _extra_exclude_pathspecs).
# On top of those, every snapshot WARNS with the untracked files it just
# captured (see _warn_new_untracked), so what entered a snapshot is visible
# at the moment it happens rather than discoverable afterward.
#
# Every entry below was verified by execution against a scratch repository
# holding one file of each shape (2026-07-27): the staged set was exactly
# the non-secret files, and the deliberately-kept cases (a source directory
# named credentials/, a file named config outside .ssh and .kube) stayed
# captured, because over-exclusion silently loses real work.
SECRET_EXCLUDE_PATHSPECS = (
    # The original ten, kept VERBATIM and first: the protected set may grow,
    # never narrow.
    ":(exclude,glob)**/.env", ":(exclude).env", ":(exclude,glob)**/.env.*",
    ":(exclude,glob)**/*.pem", ":(exclude,glob)**/*.key", ":(exclude,glob)**/*.p12",
    ":(exclude,glob)**/*.keystore", ":(exclude,glob)**/id_rsa", ":(exclude,glob)**/id_dsa",
    ":(exclude,glob)**/*.pfx",
    # SSH private keys the old list missed, id_ed25519 above all (the
    # default key type OpenSSH has generated since 7.8).
    ":(exclude,glob)**/id_ecdsa", ":(exclude,glob)**/id_ecdsa_sk",
    ":(exclude,glob)**/id_ed25519", ":(exclude,glob)**/id_ed25519_sk",
    # Whole credential directories. A leading "**/" matches at depth zero
    # too (verified by execution), so this covers ./.ssh and a/b/.ssh alike.
    ":(exclude,glob)**/.ssh/**", ":(exclude,glob)**/.gnupg/**",
    ":(exclude,glob)**/.aws/**", ":(exclude,glob)**/.kube/**",
    ":(exclude,glob)**/.docker/**",
    # Token-bearing dotfiles.
    ":(exclude,glob)**/.npmrc", ":(exclude,glob)**/.netrc", ":(exclude,glob)**/_netrc",
    ":(exclude,glob)**/.pgpass", ":(exclude,glob)**/.git-credentials",
    ":(exclude,glob)**/.htpasswd", ":(exclude,glob)**/.pypirc",
    # Cloud and service-account credential files. The '?' in
    # *service?account*.json matches both the hyphen and the underscore
    # spelling without matching a longer word.
    ":(exclude,glob)**/credentials.json",
    ":(exclude,glob)**/*service?account*.json",
    ":(exclude,glob)**/application_default_credentials.json",
    # Remaining key and secret-store container formats.
    ":(exclude,glob)**/*.p8", ":(exclude,glob)**/*.jks", ":(exclude,glob)**/*.ppk",
    ":(exclude,glob)**/*.kdbx", ":(exclude,glob)**/*.ovpn",
    ":(exclude,glob)**/*.asc", ":(exclude,glob)**/*.gpg",
    # Infrastructure variable and secret files.
    ":(exclude,glob)**/*.tfvars", ":(exclude,glob)**/secrets.yaml",
    ":(exclude,glob)**/secrets.yml",
)

# The env var that makes this list extensible instead of frozen: a
# separated list of extra globs (comma, colon, or newline), each wrapped in
# the same exclude magic the defaults use. Parsed defensively like every
# other env var this module reads (requirement 10, FI): a value that cannot
# be used warns once and is skipped, and nothing here ever raises.
EXTRA_EXCLUDE_ENV = "BROTHERMODE_AUTOSAVE_EXCLUDE"

# The same shapes, as plain globs, for the informational captured/excluded
# counts on the receipt (best-effort only; the pathspecs above are the real,
# enforced boundary, not this classifier). Derived from
# SECRET_EXCLUDE_PATHSPECS rather than hand-copied a second time (prerelease
# fix round): a git pathspec of the form ":(exclude[,glob])<pattern>" always
# carries the real glob as everything after its last ')', so stripping the
# magic-word prefix is the whole derivation; a second, hand-maintained list
# is exactly the kind of copy that silently drifts from the one git actually
# enforces.
SECRET_GLOBS = tuple(p.rsplit(")", 1)[-1] for p in SECRET_EXCLUDE_PATHSPECS)

_UNSAFE_REF_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class SnapshotAborted(Exception):
    """Raised internally when a git step fails partway through building a
    snapshot, so the caller stops before update-ref ever touches the
    existing latest pointer (FE). Always caught inside snapshot(); never
    escapes this module, because autosave is advisory and must never block
    a session."""


def _warn(msg):
    """The one place this module writes a warning. Never raises: a full
    disk or a closed stderr must not turn an advisory warning into a crash."""
    try:
        sys.stderr.write("bm_autosave: WARNING: %s\n" % msg)
    except Exception:  # sbe: allow-silent the one warning helper must never itself crash on a full disk or closed stderr; there is nowhere left to report to
        pass


# ---------------------------------------------------------------------------
# Defensive environment parsing (requirement 10, FI): every env var this
# module reads goes through here. Unset, zero, negative, non-numeric, and
# absurdly large all fall back to `default` with exactly one warning line
# (unset is the normal, expected path and warns nothing); nothing here ever
# raises. The old shell script did `$((n % TICK_EVERY))`, a hard crash the
# moment TICK_EVERY was 0 or not a number.
# ---------------------------------------------------------------------------
def _parse_int_env(name, default, minimum=1, maximum=10_000_000):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        n = int(raw.strip())
    except ValueError:
        _warn("%s=%r is not an integer; using the default (%d)" % (name, raw, default))
        return default
    if n < minimum:
        _warn("%s=%d is below the minimum (%d); using the default (%d)"
              % (name, n, minimum, default))
        return default
    if n > maximum:
        _warn("%s=%d is unreasonably large (over %d); using the default (%d)"
              % (name, n, maximum, default))
        return default
    return n


# ---------------------------------------------------------------------------
# git plumbing: the one wrapper every call in this module goes through.
# ---------------------------------------------------------------------------

#: THE ENVIRONMENT PREFIX A CHILD OF THIS MODULE MAY NOT INHERIT
#: (CROSS-FAMILY REFUTATION finding 3, HIGH, DATA DESTRUCTION; the same
#: rule tools/bm_controller.py ships, mirrored here rather than imported so
#: neither module can be broken by an edit to the other).
#:
#: `-C <toplevel>` below does NOT beat GIT_DIR and GIT_WORK_TREE: git
#: honours those over both cwd and -C. Measured here on git 2.50.1 (Apple
#: Git-155) in two throwaway repositories P and Q:
#:
#:     plain:    git -C P rev-parse --show-toplevel  ->  .../P
#:     poisoned: GIT_DIR=.../Q/.git GIT_WORK_TREE=.../Q
#:               git -C P rev-parse --show-toplevel  ->  .../Q
#:
#: So an autosave fired from an environment carrying those names (a git
#: hook, a wrapper script, another repository's tooling) resolved a
#: DIFFERENT repository as its toplevel and ran the whole pipeline there:
#: the founder's unsaved work was never captured, an unrelated working tree
#: was committed into refs nobody asked for, that repository's own
#: snapshots became eligible for this run's retention prune, and the
#: receipt recorded success. In the one module whose entire job is not
#: losing work, that is false confidence, which is worse than no backup.
#:
#: A PREFIX rather than a name list, and that is the whole point: the names
#: were enumerated on this machine as the union of git(1)'s ENVIRONMENT
#: VARIABLES section, git-config(1)'s environment section, and `strings`
#: over the shipped binary (205 distinct GIT_ names), and thirteen of the
#: ones that really move where git operates appear in git(1)'s environment
#: section NOWHERE. The full enumeration and its reasoning live with the
#: shipped rule in tools/bm_controller.py's own _GIT_ENV_PREFIX block and
#: in docs/program/absolute-lead/evidence/L03/FIX-crossfamily-report.md;
#: they are not repeated here. A hand-kept list would ship with those
#: holes and grow another the next time git adds a name.
#:
#: WHAT THIS COSTS HERE, stated rather than buried: a snapshot commit is
#: attributed to git's own configured (or implicit) identity instead of an
#: inherited GIT_AUTHOR_NAME or GIT_COMMITTER_EMAIL. Measured on the same
#: git: `commit-tree` with no identity in the environment AND no
#: user.email configured still exits 0 using git's implicit ident, so the
#: strip cannot turn a working snapshot into a failed one on that path.
#: Autosave commits are throwaway backups nobody blames, so borrowing
#: whatever identity the invoking process exported was never worth being
#: redirectable by the same class of variable.
_GIT_ENV_PREFIX = "GIT_"


def _sanitised_env(environ=None):
    """The environment every git call this module makes really gets: a copy
    of the caller's, minus the whole git redirection class.

    Takes `environ` so the rule can be exercised on a plain dictionary,
    which is what makes it testable without a process at all. Everything
    else passes through untouched, deliberately: PATH and HOME are what
    make git runnable, and an empty environment would break every snapshot
    for reasons that have nothing to do with safety."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items()
            if not k.startswith(_GIT_ENV_PREFIX)}


def _run_git(toplevel, *args, env=None):
    """Every git call in this module runs through here, always with
    `-C <toplevel>` (requirement 2, F2b): resolving the repository root
    once and rooting every subsequent call there, instead of relying on the
    caller's cwd, is what makes a snapshot triggered from a subdirectory
    still cover the whole repository rather than silently scoping the '.'
    pathspec to that subdirectory. Returns a CompletedProcess-shaped result
    even when git itself cannot be executed, so every caller's return-code
    check works uniformly.

    This is the ONE place in this module that starts a process, pinned
    structurally by tools/test_bm_autosave.py's _execution_sites, so a git
    call added later inherits the SANITISED environment by construction
    rather than by remembering to ask for it. `toplevel` is simply the path
    git is rooted at: the resolved repository root for every call except
    resolve_toplevel's own, which is where that root comes from.

    `env` is the module's own deliberate override (GIT_INDEX_FILE, the
    throwaway index a snapshot stages onto) and is applied AFTER the strip,
    so stripping the inherited class never eats the one this module means."""
    e = _sanitised_env()
    if env:
        e.update(env)
    try:
        return subprocess.run(
            ["git", "-C", toplevel] + list(args),
            capture_output=True, text=True, env=e)
    except OSError as ex:
        return subprocess.CompletedProcess(args, 127, "", str(ex))


def _checked(result, step):
    """The ONE gate every git call's return code passes through before its
    output is trusted (requirement 4, FE): a non-zero return code raises
    SnapshotAborted naming the step, so a caller can never accidentally
    continue past a failure the way the old shell script did (a failed
    `git add` was ignored there, and an empty tree replaced a good
    snapshot). Deliberately its own named function, not an inline `if`, so
    a reinjection test can prove calibration by monkeypatching exactly this
    name back to a pass-through and watching the guarantee break."""
    if result.returncode != 0:
        raise SnapshotAborted(
            "%s failed (rc=%s): %s" % (step, result.returncode, (result.stderr or "").strip()))
    return result


def resolve_toplevel(start_dir):
    """git rev-parse --show-toplevel from start_dir, resolved to a real
    path (requirement 2, MUST run first). Returns None when start_dir is
    not inside a git repository: a non-git project is a clean, silent
    no-op, since advisory surfaces fail open.

    Goes through _run_git like every other call (CROSS-FAMILY REFUTATION
    finding 3). This one decides which repository EVERY later call touches,
    so it is the call an inherited GIT_DIR redirects most cheaply: leaving
    it as its own subprocess with its own inherited environment would have
    left the whole pipeline redirectable while the strip below it looked
    like it was doing the job. _run_git already answers rc=127 rather than
    raising when git cannot be executed, which is the OSError path this
    used to catch itself."""
    r = _run_git(start_dir, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        return None
    top = r.stdout.strip()
    if not top:
        return None
    return os.path.realpath(top)


def worktree_id_for(toplevel):
    """A short, stable hash of THIS worktree's own checkout path. See the
    module header's NOTE for why this is the toplevel path and not
    `git rev-parse --git-common-dir` (that value is identical across every
    linked worktree of one repository, verified by direct execution, and so
    cannot be what distinguishes them)."""
    return hashlib.sha256(os.path.realpath(toplevel).encode("utf-8", "replace")).hexdigest()[:12]


def _safe_ref_component(s):
    """Sanitize a caller-supplied string (a hook's session_id) before it
    becomes part of a git ref path: git ref components reject some
    characters outright, and a rejected ref name must never surface as a
    crash (every path here still exits 0), only as an honest warning from
    _checked's caller."""
    s = _UNSAFE_REF_CHARS.sub("_", s or "")
    s = s.strip(".") or "unknown"
    return s


def snapshot_ref(worktree_id, session_id, stamp):
    return "%s/%s/%s/%s" % (REF_NAMESPACE, worktree_id, _safe_ref_component(session_id), stamp)


def latest_ref(worktree_id):
    return "%s/%s/latest" % (REF_NAMESPACE, worktree_id)


def _stamp():
    """A lexically sortable, effectively-unique snapshot identifier: a
    zero-padded microsecond epoch (so string sort order equals chronological
    order, which retention pruning relies on) plus a short random suffix so
    two snapshots requested in the same microsecond can never collide."""
    return "%020d-%s" % (int(time.time() * 1_000_000), uuid.uuid4().hex[:6])


# ---------------------------------------------------------------------------
# PURE git-ref reading (FINDING 1). No subprocess, deliberately.
#
# The consumer of the safety claim is tools/bm_telemetry.py's compact hint,
# and that file carries a HARD CONSTRAINT of its own: it never runs a
# subprocess (SECURITY.md's "no network calls" claim is gated by a test that
# bans the import, and the audited property is that the telemetry half shells
# out to nothing). If the re-resolution below ran `git rev-parse`, the hint
# would inherit a subprocess through this module and quietly break that
# property. So the ref is re-resolved by reading git's OWN ref storage: the
# loose ref file, then packed-refs, following a symref if it finds one.
#
# Why re-resolving the ref is enough to justify the claim: a ref is a
# garbage-collection ROOT. If the ref still resolves to the recorded commit,
# that commit and its whole tree are reachable and gc cannot drop them. If
# the ref is gone, the objects may be alive today and gone after the next
# gc, which is precisely the state the tool must refuse to call "saved".
# ---------------------------------------------------------------------------
_SHA_RE = re.compile(r"\A[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")  # sha1, or sha256


def resolve_git_dir(toplevel):
    """The git directory belonging to THIS worktree, resolved by reading
    <toplevel>/.git: a directory in a normal checkout, and a file holding
    "gitdir: <path>" in a linked worktree. Returns None whenever that cannot
    be established, which callers treat as "unknown", never as "safe"."""
    dot = os.path.join(toplevel, ".git")
    try:
        if os.path.isdir(dot):
            return os.path.realpath(dot)
        with open(dot, "r", errors="replace") as f:
            line = f.read().strip()
    except OSError:  # sbe: allow-silent an unresolvable .git file returns None, the callers' own unknown sentinel
        return None
    if not line.startswith("gitdir:"):
        return None
    p = line.split(":", 1)[1].strip()
    if not p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(toplevel, p)
    return os.path.realpath(p) if os.path.isdir(p) else None


def resolve_common_dir(toplevel):
    """Where SHARED refs live. In a linked worktree the per-worktree git dir
    holds a `commondir` file pointing at the main repository's git dir, and
    refs/ (everything except HEAD, refs/bisect, and friends) lives there.
    Snapshot refs are under refs/, so this, not the worktree git dir, is what
    a ref read must start from."""
    gd = resolve_git_dir(toplevel)
    if gd is None:
        return None
    try:
        with open(os.path.join(gd, "commondir"), "r", errors="replace") as f:
            rel = f.read().strip()
    except OSError:
        return gd
    if not rel:
        return gd
    p = rel if os.path.isabs(rel) else os.path.join(gd, rel)
    return os.path.realpath(p) if os.path.isdir(p) else gd


def _ref_is_safe(ref):
    """A ref name is turned into a filesystem path below, so refuse anything
    that could climb out of the ref store. Product code only ever builds
    these from _safe_ref_component, but a reader must not depend on its
    caller's discipline."""
    if not ref or ref.startswith("/") or "\\" in ref:
        return False
    parts = ref.split("/")
    return all(p and p not in (".", "..") for p in parts)


def resolve_ref_sha(toplevel, ref, _depth=0):
    """Re-resolve `ref` to an object id RIGHT NOW, by reading git's own ref
    storage. Returns the sha, or None when the ref does not exist. Pure: no
    subprocess, so tools/bm_telemetry.py can call it and keep its
    no-subprocess property (see this section's header)."""
    if not _ref_is_safe(ref) or _depth > 5:
        return None
    common = resolve_common_dir(toplevel)
    if common is None:
        return None
    try:
        with open(os.path.join(common, *ref.split("/")), "r", errors="replace") as f:
            val = f.read().strip()
    except OSError:
        val = ""
    if val.startswith("ref:"):
        return resolve_ref_sha(toplevel, val.split(":", 1)[1].strip(), _depth + 1)
    if _SHA_RE.match(val):
        return val
    try:
        with open(os.path.join(common, "packed-refs"), "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].strip() == ref and _SHA_RE.match(parts[0]):
                    return parts[0]
    except OSError:  # sbe: allow-silent a missing or unreadable packed-refs is normal; the ref is simply not packed
        pass
    return None


def list_snapshot_refs(toplevel, worktree_id):
    """Every snapshot ref this worktree currently has, newest LAST, as
    (ref, sha) pairs, read the same pure way as resolve_ref_sha and sorted
    on _snapshot_sort_key (the stamp alone; see that function for why the
    whole ref name is the wrong key). The "latest" pointer is excluded: this
    is the enumeration recovery falls back to precisely when that pointer is
    the thing that failed (FINDING 13)."""
    common = resolve_common_dir(toplevel)
    if common is None:
        return []
    prefix = "%s/%s/" % (REF_NAMESPACE, worktree_id)
    found = {}
    base = os.path.join(common, *prefix.rstrip("/").split("/"))
    for dirpath, _dirnames, filenames in os.walk(base):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            ref = prefix + os.path.relpath(full, base).replace(os.sep, "/")
            if ref.endswith("/latest"):
                continue
            sha = resolve_ref_sha(toplevel, ref)
            if sha:
                found[ref] = sha
    try:
        with open(os.path.join(common, "packed-refs"), "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                parts = line.split(" ", 1)
                if len(parts) != 2:
                    continue
                ref = parts[1].strip()
                if ref.startswith(prefix) and not ref.endswith("/latest") \
                        and _SHA_RE.match(parts[0]):
                    found.setdefault(ref, parts[0])
    except OSError:  # sbe: allow-silent a missing or unreadable packed-refs is normal; the ref enumeration just stays partial
        pass
    return [(ref, found[ref]) for ref in sorted(found, key=_snapshot_sort_key)]


# ---------------------------------------------------------------------------
# Per-worktree lock (FINDING 13). Two snapshots of one worktree used to run
# the whole read-tree/add/write-tree/commit/publish pipeline concurrently and
# publish in whatever order they finished, so a CLEAN snapshot could delete
# the pointer a DIRTY one had just written, or an older commit could land on
# "latest" after a newer one.
#
# O_CREAT|O_EXCL rather than fcntl.flock: Windows is ratified scope (it is
# the reason this module is Python and not shell), and flock does not exist
# there. A lock file whose holder died is bounded by an age check, so a
# crashed process cannot wedge autosave forever.
# ---------------------------------------------------------------------------
DEFAULT_LOCK_WAIT = 10
STALE_LOCK_SECONDS = 600


def _bm_dir(toplevel):
    """<git dir>/brothermode, owner-only, holding this module's own
    bookkeeping: the lock, the snapshot events, and the cleared-pointer
    marker. Inside the git directory on purpose. It is the one place that is
    per-worktree, is never staged by `git add` (so bookkeeping can never
    dirty the tree it is describing, nor end up inside a snapshot of it),
    and dies with `git worktree remove` exactly when the refs it describes
    stop being reachable."""
    gd = resolve_git_dir(toplevel)
    if gd is None:
        return None
    d = os.path.join(gd, "brothermode")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:  # sbe: allow-silent an unmakeable bookkeeping dir returns None; every caller already treats None as unknown/advisory-off
        return None
    _chmod_best_effort(d, 0o700)
    return d


class _WorktreeLock:
    """Serializes the snapshot pipeline per worktree. acquire() returns
    False rather than raising when the lock cannot be taken: autosave is
    advisory, so losing the race means warning and standing down, never
    blocking the session and never publishing on top of the winner."""

    def __init__(self, toplevel, worktree_id, wait=None):
        d = _bm_dir(toplevel)
        name = "autosave-%s.lock" % worktree_id
        self.path = os.path.join(d, name) if d else os.path.join(tempfile.gettempdir(), "bm-" + name)
        self.wait = DEFAULT_LOCK_WAIT if wait is None else wait
        self.held = False

    def _try_once(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            return False
        try:
            os.write(fd, ("%d %f\n" % (os.getpid(), time.time())).encode("utf-8"))
        except OSError:  # sbe: allow-silent the lock file's pid/time payload is diagnostic only; the O_CREAT|O_EXCL above is what actually holds the lock
            pass
        finally:
            os.close(fd)
        return True

    def _clear_if_stale(self):
        try:
            age = time.time() - os.stat(self.path).st_mtime
        except OSError:
            return
        if age > STALE_LOCK_SECONDS:
            _warn("removing a stale autosave lock (%s, %d seconds old); the "
                  "process holding it is gone" % (self.path, int(age)))
            try:
                os.remove(self.path)
            except OSError:  # sbe: allow-silent clearing a stale lock is itself best-effort; a race losing this remove just leaves acquire() to retry
                pass

    def acquire(self):
        deadline = time.time() + max(self.wait, 0)
        while True:
            if self._try_once():
                self.held = True
                return True
            self._clear_if_stale()
            if time.time() >= deadline:
                return False
            time.sleep(0.05)

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            os.remove(self.path)
        except OSError:  # sbe: allow-silent lock release is advisory; a failed remove leaves a lock STALE_LOCK_SECONDS will clear later
            pass


# ---------------------------------------------------------------------------
# The snapshot EVENT record (FINDING 1). One shot, present tense.
#
# THE DEFECT, exactly as the audit reproduced it: has_receipt() asked a
# HISTORICAL question ("did this session ever snapshot?"), and the compact
# hint printed "Your files are autosaved" from the answer. So:
#     10:00 snapshot succeeds, receipt written
#     10:30 more files change
#     11:00 the next PreCompact snapshot FAILS
#     11:05 the session resumes, a receipt for this session still exists
#           -> the tool says the files are autosaved. The newest work is not.
# A best-effort receipt deletion means a pruned snapshot could keep reporting
# safe for the same reason.
#
# The fix answers the PRESENT-TENSE question instead. Every snapshot attempt
# opens an event in state "pending" BEFORE it touches git, and only the
# publish path that has written the ref AND read it back marks it "saved".
# The hint consumes that event once and re-resolves the ref to the recorded
# sha before it will make any claim. A failed attempt therefore OVERWRITES
# the previous success, which is the whole point: the newest attempt is what
# the founder is being told about.
#
# WHAT THIS STILL CANNOT SEE, stated rather than papered over: files edited
# after the last successful snapshot, with no attempt since, leave the event
# "saved" and truthful about that snapshot. Nothing short of inspecting the
# working tree at hint time (a git call, banned in the hint) can catch that,
# so the verified message names the snapshot's own capture time and lets the
# founder judge, instead of implying "everything you have is safe".
#
# WHY A FILE AND NOT A ROW (a fence limit, reported rather than worked
# around): autosave_receipts (tools/bm_store.py, outside this change's
# fence) has no column for an event id, a ref name, or a success state, and
# every column is NOT NULL, so a "pending" row cannot be expressed there at
# all. A store may also legitimately not exist. The event therefore lives
# beside the refs it describes, in the git directory, and the store receipt
# is unchanged.
# ---------------------------------------------------------------------------
EVENT_SCHEMA = 1
EVENT_PENDING = "pending"
EVENT_SAVED = "saved"
EVENT_FAILED = "failed"
EVENT_CLEAN = "clean"


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _event_path(toplevel, worktree_id, session_id):
    d = _bm_dir(toplevel)
    if d is None:
        return None
    safe = _safe_ref_component(session_id)
    if len(safe) > 60:  # keep the filename sane; still deterministic on both sides
        safe = safe[:40] + hashlib.sha256(safe.encode("utf-8", "replace")).hexdigest()[:12]
    return os.path.join(d, "snapshot-event-%s-%s.json" % (worktree_id, safe))


def _write_text_atomic(path, text):
    """The ONE file-writing primitive this module's bookkeeping uses (the
    event record and the cleared-pointer marker), so tools/write_sites.json
    has one reviewed site to audit instead of a pair per caller, and so a
    reader can never see a half-written file: write a temp file beside the
    target, tighten its mode, then os.replace. Returns True on success and
    never raises, because every caller is advisory."""
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w") as f:
            f.write(text)
        _chmod_best_effort(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:  # sbe: allow-silent cleanup of the failed atomic-write's own tmp file is best-effort; the function already returns False
            pass
        return False


def _write_event(path, event):
    """Atomic replace, so a reader never sees a half-written event."""
    try:
        payload = json.dumps(event)
    except (TypeError, ValueError) as e:
        _warn("could not serialize the snapshot event (%r)" % (e,))
        return False
    if _write_text_atomic(path, payload):
        return True
    _warn("could not record the snapshot event at %s; the compact hint will "
          "make no claim about this session" % path)
    return False


def read_snapshot_event(toplevel, worktree_id, session_id):
    """The event for this worktree and session, or None. Never raises: a
    truncated or hand-edited file reads as None, which callers treat as
    "nothing is known", never as "safe"."""
    path = _event_path(toplevel, worktree_id, session_id)
    if path is None:
        return None
    try:
        with open(path, "r", errors="replace") as f:
            event = json.load(f)
    except (OSError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def consume_snapshot_event(toplevel, worktree_id, session_id):
    """Read the event and DELETE it in the same call: it describes ONE
    compaction, and a second resume must not be told about the first one's
    snapshot as though it were current."""
    event = read_snapshot_event(toplevel, worktree_id, session_id)
    path = _event_path(toplevel, worktree_id, session_id)
    if path:
        try:
            os.remove(path)
        except OSError:  # sbe: allow-silent deleting a consumed event file is best-effort; a leftover stale file is harmless, never re-read as current
            pass
    return event


def begin_snapshot_event(toplevel, worktree_id, session_id, reason):
    """Open (or RESET) this session's event to "pending", before any git
    call runs. Resetting is what makes a later failure override an earlier
    success instead of hiding behind it."""
    event = {
        "schema": EVENT_SCHEMA,
        "event_id": uuid.uuid4().hex,
        "state": EVENT_PENDING,
        "worktree_id": worktree_id,
        "session_id": session_id,
        "reason": reason,
        "ref": "",
        "sha": "",
        "tree": "",
        "detail": "",
        "started_at": _iso_now(),
        "updated_at": _iso_now(),
    }
    path = _event_path(toplevel, worktree_id, session_id)
    if path is None:
        return None
    return event if _write_event(path, event) else None


def finish_snapshot_event(toplevel, event, state, ref="", sha="", tree="", detail=""):
    """Close the event with its real outcome. Refuses to write over an event
    a NEWER attempt has since opened (matched by event_id), so a slow loser
    can never downgrade a fresh winner's result."""
    if not event:
        return
    path = _event_path(toplevel, event.get("worktree_id"), event.get("session_id"))
    if path is None:
        return
    current = read_snapshot_event(toplevel, event.get("worktree_id"), event.get("session_id"))
    if current is not None and current.get("event_id") != event.get("event_id"):
        return
    event = dict(event)
    event.update({"state": state, "ref": ref, "sha": sha, "tree": tree,
                  "detail": detail, "updated_at": _iso_now()})
    _write_event(path, event)


def verify_recoverable(toplevel, worktree_id, session_id, consume=True):
    """Answer the PRESENT-TENSE question: is this session's work recoverable
    RIGHT NOW? Pure (no subprocess), so the compact hint can call it.

    Returns a dict whose "state" is exactly one of:
      "verified"   a snapshot exists and its ref still resolves to the sha
                   this session recorded. "ref", "sha", "short" and
                   "captured_at" are filled in, so the claim can NAME what
                   it is claiming.
      "unverified" something is known and it is not good: the attempt
                   failed, was interrupted, captured nothing, or its ref no
                   longer resolves to the recorded sha. "detail" says which.
      "none"       no attempt was ever recorded for this session.
      "unknown"    the git directory could not be read, so nothing can be
                   established either way. Never a claim, in either
                   direction.
    """
    if resolve_git_dir(toplevel) is None:
        return {"state": "unknown",
                "detail": "no readable git directory at %s" % toplevel}
    event = (consume_snapshot_event if consume else read_snapshot_event)(
        toplevel, worktree_id, session_id)
    if event is None:
        return {"state": "none", "detail": "no snapshot attempt was recorded for this session"}

    state = event.get("state")
    if state == EVENT_CLEAN:
        return {"state": "unverified",
                "detail": "the working tree matched the last commit when autosave "
                          "last ran, so there was nothing to snapshot"}
    if state == EVENT_PENDING:
        return {"state": "unverified",
                "detail": "the last autosave attempt never reported a result, so it "
                          "was interrupted partway"}
    if state != EVENT_SAVED:
        return {"state": "unverified",
                "detail": "the last autosave attempt FAILED (%s)"
                          % (event.get("detail") or "no reason recorded")}

    ref, sha = event.get("ref") or "", event.get("sha") or ""
    if not ref or not sha:
        return {"state": "unverified",
                "detail": "the recorded snapshot names no ref or commit to check"}
    now = resolve_ref_sha(toplevel, ref)
    if now is None:
        return {"state": "unverified", "ref": ref, "sha": sha,
                "detail": "the snapshot ref %s no longer exists" % ref}
    if now != sha:
        return {"state": "unverified", "ref": ref, "sha": sha,
                "detail": "the snapshot ref %s now points at %s, not the %s this "
                          "session recorded" % (ref, now[:12], sha[:12])}
    return {"state": "verified", "ref": ref, "sha": sha, "short": sha[:12],
            "captured_at": event.get("updated_at") or event.get("started_at") or "",
            "detail": ""}


def _glob_to_regex(g):
    """Translate ONE git pathspec glob into a compiled regex with git's own
    path semantics, which fnmatch does not have.

    The old classifier used fnmatch twice (once on the basename against the
    glob's last segment, once on the whole path), and both halves misread
    the directory shapes FINDING 16 added: fnmatch's '*' happily crosses a
    '/', so "**/.ssh/**" reduced to "match anything", which would have
    labelled EVERY changed file secret-shaped and made the receipt's counts
    meaningless. Three rules is the whole translation:
      "**/" at a segment boundary  -> any number of leading directories
      a trailing "/**"             -> everything beneath that directory
      "*" and "?"                  -> within one path segment only
    """
    out = []
    i, n = 0, len(g)
    while i < n:
        if g.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif g.startswith("/**", i) and i + 3 == n:
            out.append("/.*")
            i += 3
        elif g.startswith("**", i) and i + 2 == n:
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_SECRET_MATCHERS = tuple(_glob_to_regex(g) for g in SECRET_GLOBS)


def _is_secret_shaped(rel_path):
    """Best-effort classifier for the receipt's informational
    captured/excluded counts and for the captured-untracked warning ONLY.
    The real, enforced boundary is SECRET_EXCLUDE_PATHSPECS, passed straight
    to git; this never gates anything, it only labels a number and a name a
    human might read later."""
    # A "./" PREFIX only. str.lstrip takes a character SET, so lstrip("./")
    # would eat the leading dot of ".env" and make the most important entry
    # in the whole denylist unmatchable.
    rel_path = rel_path.replace("\\", "/")
    while rel_path.startswith("./"):
        rel_path = rel_path[2:]
    for rx in _SECRET_MATCHERS:
        if rx.match(rel_path):
            return True
    return False


def _extra_exclude_pathspecs():
    """FINDING 16: the founder's own additions to the denylist, so the
    protected set is extensible instead of frozen at whatever this file
    happened to enumerate. BROTHERMODE_AUTOSAVE_EXCLUDE holds globs
    separated by commas, colons, or newlines; each becomes the same
    ":(exclude,glob)" pathspec the defaults use.

    Defensive like every other env read here (requirement 10, FI): entries
    that are empty, or that would be read by git as pathspec magic rather
    than as a path (a leading ':'), are skipped with one warning line, and
    nothing in here can raise."""
    raw = os.environ.get(EXTRA_EXCLUDE_ENV)
    if raw is None or not raw.strip():
        return ()
    out = []
    for piece in re.split(r"[,\n:]", raw):
        pat = piece.strip()
        if not pat:
            continue
        if pat.startswith(":"):
            _warn("%s entry %r starts with ':', which git reads as pathspec "
                  "magic rather than a path; skipping it" % (EXTRA_EXCLUDE_ENV, pat))
            continue
        out.append(":(exclude,glob)%s" % pat)
    return tuple(out)


def _exclude_pathspecs():
    """The full exclusion set actually handed to git: the pinned defaults
    plus the founder's additions. One function so the snapshot pipeline and
    any test read the same thing."""
    return SECRET_EXCLUDE_PATHSPECS + _extra_exclude_pathspecs()


def _warn_new_untracked(toplevel):
    """FINDING 16's visibility half: name the UNTRACKED files this snapshot
    is about to capture, because those are exactly the paths no reviewer has
    ever looked at and no .gitignore rule has claimed. A denylist can only
    block shapes someone thought of; this line lets the founder see what
    actually entered a snapshot, at the moment it entered one, instead of
    discovering it later in a git object.

    Read-only and best-effort: `git status` only, never the temp index this
    snapshot is building, and any failure is silence rather than a crash."""
    r = _run_git(toplevel, "status", "--porcelain", "-z", "--untracked-files=all")
    if r.returncode != 0:
        return
    names = []
    for entry in r.stdout.split("\x00"):
        if not entry.startswith("?? "):
            continue
        path = entry[3:]
        if path and not _is_secret_shaped(path):
            names.append(path)
    if not names:
        return
    shown = sorted(names)[:20]
    more = "" if len(names) <= len(shown) else " (and %d more)" % (len(names) - len(shown))
    _warn("this snapshot captures %d untracked file(s) that no commit has "
          "ever reviewed: %s%s. Anything secret in that list belongs in "
          ".gitignore or in %s."
          % (len(names), ", ".join(shown), more, EXTRA_EXCLUDE_ENV))


def _count_paths(toplevel):
    """Informational captured/excluded counts for the receipt row: how many
    changed paths (tracked edits + untracked new files) the real working
    tree currently has, split by whether they look secret-shaped. Read-only
    (`git status`); never touches the temp index this snapshot is building."""
    r = _run_git(toplevel, "status", "--porcelain", "-z", "--untracked-files=all")
    if r.returncode != 0:
        return 0, 0
    entries = [e for e in r.stdout.split("\x00") if e]
    captured = excluded = 0
    for entry in entries:
        path = entry[3:] if len(entry) > 3 else entry
        if _is_secret_shaped(path):
            excluded += 1
        else:
            captured += 1
    return captured, excluded


def _update_ref_cas(toplevel, ref, new_sha, expected_old):
    """Move a ref with COMPARE-AND-SWAP (FINDING 13). git's update-ref takes
    an OLD-VALUE operand that the old code simply did not pass, so the last
    writer to finish always won, whatever it was carrying. An empty
    `expected_old` means "this ref must not exist yet" (verified by
    execution against git 2.50.1).

    Named, not inlined, so a reinjection test can patch exactly this symbol
    back to the old last-writer-wins shape and watch the guarantee break."""
    return _run_git(toplevel, "update-ref", ref, new_sha, expected_old)


def _delete_ref_cas(toplevel, ref, expected_old):
    """Delete a ref only while it still holds the value this process read
    (FINDING 13). Without the old value, a clean snapshot's delete could
    remove a pointer that a concurrent dirty snapshot had just published."""
    return _run_git(toplevel, "update-ref", "-d", ref, expected_old)


def _cleared_marker_path(toplevel, worktree_id):
    d = _bm_dir(toplevel)
    return None if d is None else os.path.join(d, "latest-cleared-%s" % worktree_id)


def _record_latest_cleared(toplevel, worktree_id, stamp):
    """FD and FINDING 13 pull in opposite directions on one path, and this
    marker is what reconciles them.

    FD says a CLEAN tree must clear "latest", so deliberately discarded WIP
    can never present itself as current. FINDING 13 says recovery must fall
    back to the newest snapshot ref whenever "latest" is missing, so a lost
    pointer never means lost work. Applied naively, the fallback would
    resurrect exactly the WIP FD threw away.

    So the clear is RECORDED, with the stamp at the moment it happened.
    Recovery only ever falls back to snapshots NEWER than that stamp:
    deliberately discarded work stays discarded, while a pointer lost to a
    racing writer still recovers. _stamp() is a zero-padded microsecond
    epoch, so this comparison is a plain string comparison."""
    path = _cleared_marker_path(toplevel, worktree_id)
    if path is None:
        return
    # Advisory: if this cannot be written, the fallback simply offers more
    # snapshots, never fewer.
    _write_text_atomic(path, stamp)


def _latest_cleared_stamp(toplevel, worktree_id):
    path = _cleared_marker_path(toplevel, worktree_id)
    if path is None:
        return ""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _clear_latest_if_present(toplevel, worktree_id):
    """FD: when the working tree matches HEAD, this worktree's "latest"
    pointer is cleared rather than left aimed at old, deliberately-discarded
    WIP, so a stale snapshot can never present itself as current.

    FINDING 13: the delete now carries the OLD VALUE it expects
    (`update-ref -d <ref> <oldvalue>`), so a clean snapshot can only ever
    delete the pointer it actually read. Before this, an unconditional
    delete raced a concurrent dirty snapshot and could remove a pointer to a
    NEWER snapshot that had just been published, leaving the founder with no
    "latest" at all while real work sat in a ref nothing pointed to."""
    ref = latest_ref(worktree_id)
    check = _run_git(toplevel, "rev-parse", "-q", "--verify", ref)
    if check.returncode != 0:
        return
    old = check.stdout.strip()
    _record_latest_cleared(toplevel, worktree_id, _stamp())
    deleted = _delete_ref_cas(toplevel, ref, old)
    if deleted.returncode != 0:
        _warn("did not clear the latest pointer for this worktree: it moved to a "
              "newer snapshot while this clean check was running (%s). The newer "
              "snapshot is kept, deliberately." % (deleted.stderr or "").strip())


def _snapshot_sort_key(ref):
    """The retention sort key for one snapshot ref: the STAMP ALONE (the
    last '/'-separated component), never the whole ref name. A ref looks
    like <namespace>/<worktree>/<session>/<stamp>, so sorting the full
    string ranks the session id above the timestamp: a snapshot from a
    session whose id sorts early is treated as older than every snapshot
    from a session whose id sorts late, no matter when either was taken.
    Reproduced 2026-07-26: ten snapshots from session zzz-old, then the
    newest snapshot from session aaa-new, and the whole-refname sort
    deleted THE NEWEST while keeping all ten older ones. A separate,
    named function (not an inline lambda) so a reinjection test can
    monkeypatch exactly this symbol back to the old, whole-refname shape
    and prove the calibration (GATE A, prerelease fix round)."""
    return ref.rsplit("/", 1)[-1]


def _prune_old_snapshots(toplevel, worktree_id, retain=None):
    """Requirement 8 (retention): keep the last `retain` snapshot refs per
    worktree (default 10), never the only one. Sorts on _snapshot_sort_key
    (the stamp alone), never the whole ref name: see that function's
    docstring for the reproduced defect this closes. In a module whose
    entire purpose is not losing work, sorting by the wrong key destroys
    exactly the snapshot the founder would reach for.

    GATE F (prerelease fix round): a receipt row must never outlive the
    ref that made it true, or has_receipt() keeps answering yes for a
    snapshot that is actually gone. Every pruned ref's commit sha is
    captured BEFORE it is deleted, and the matching receipt rows are
    deleted in the same call, best-effort like every other receipt
    operation in this module."""
    if retain is None:
        retain = _parse_int_env("BROTHERMODE_AUTOSAVE_RETAIN", DEFAULT_RETAIN)
    prefix = "%s/%s/" % (REF_NAMESPACE, worktree_id)
    r = _run_git(toplevel, "for-each-ref", "--format=%(refname)", prefix)
    if r.returncode != 0:
        return
    refs = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    snap_refs = sorted(
        (ref for ref in refs if not ref.endswith("/latest")),
        key=_snapshot_sort_key)
    if len(snap_refs) <= 1:
        return  # never prune the only snapshot
    excess = snap_refs[: max(0, len(snap_refs) - max(retain, 1))]
    pruned_shas = []
    for ref in excess:
        sha = _run_git(toplevel, "rev-parse", "-q", "--verify", ref).stdout.strip()
        _run_git(toplevel, "update-ref", "-d", ref)
        if sha:
            pruned_shas.append(sha)
    if pruned_shas:
        _delete_receipts_for_shas(toplevel, worktree_id, pruned_shas)


# ---------------------------------------------------------------------------
# Receipts (requirement 9, the writer half of J): best-effort, advisory.
# Reuses tools/bm_store.py's own Store class (loaded by path, the same
# importlib pattern bm_store.py itself uses for bm_telemetry) rather than
# reimplementing schema or connection handling; this module never writes
# SQL DDL of its own and never changes bm_store.py.
#
# Every warning below fires every time it applies, deliberately NOT
# deduplicated across calls: unlike bm_store.py's long-lived Store object
# (many renders in one process, where a repeated warning really would be
# spam), a real invocation of this module is its own fresh, short-lived
# process per hook fire, so "once" and "every time" already coincide there.
# ---------------------------------------------------------------------------
def _load_bm_store():
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_autosave", os.path.join(HERE, "bm_store.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        _warn("could not load bm_store.py (%r); the snapshot still counts, "
              "but no receipt was recorded" % (e,))
        return None


def _resolve_receipt_root(toplevel):
    """Shared root resolution PLUS the cross-project guard (prerelease fix
    round) for every receipt operation: resolve_root(toplevel) can walk
    UP PAST this snapshot's own git toplevel and find a DIFFERENT
    project's BrotherMode marker or .git further up the tree (a repo
    nested inside a larger BrotherMode-enabled directory), in which case
    writing or reading a receipt there would touch ANOTHER project's
    store for THIS worktree's snapshot. Returns (bm_store module, root)
    only when the resolved root IS this snapshot's own toplevel;
    otherwise (None, None), the same "advisory, never blocks" shape every
    other helper in this section uses."""
    bs = _load_bm_store()
    if bs is None:
        return None, None
    try:
        root, _source = bs.resolve_root(toplevel)
    except Exception:
        root = None
    if root is None:
        _warn("no BrotherMode store root found from %s; the snapshot still "
              "counts, but no receipt was recorded" % toplevel)
        return None, None
    if os.path.realpath(root) != os.path.realpath(toplevel):
        _warn("resolved BrotherMode root %s is not this snapshot's own "
              "worktree %s; refusing to touch another project's store "
              "(the snapshot still counts)" % (root, toplevel))
        return None, None
    return bs, root


def _open_store_for_receipt(toplevel):
    """A WRITABLE store, for the receipt WRITER and the GATE F pruner
    below; both need to mutate autosave_receipts."""
    bs, root = _resolve_receipt_root(toplevel)
    if bs is None:
        return None, None
    try:
        store = bs.Store(root, create=False)
    except Exception as e:
        _warn("could not open the BrotherMode store (%r); the snapshot still "
              "counts, but no receipt was recorded" % (e,))
        return None, None
    return bs, store


def _open_readonly_store_for_receipt(toplevel):
    """The READ-ONLY store, for has_receipt (the wiring item, prerelease
    fix round): has_receipt is answering a read question, and opening the
    writable Store class to do it can create the very store it claims to
    check (side effects on open: WAL sidecars, git-exclude bookkeeping).
    A store that genuinely does not exist yet is an honest, silent False,
    not a warning: that is the normal state for a project that has never
    run `bm_store.py init`."""
    bs, root = _resolve_receipt_root(toplevel)
    if bs is None:
        return None, None
    try:
        store = bs.ReadOnlyStore(root)
    except bs.OwnershipRefused:  # sbe: allow-silent OwnershipRefused on a read-only store open is the normal race of another writer holding it; has_receipt already treats no-store as False
        return None, None
    except Exception as e:
        _warn("could not open the BrotherMode store read-only (%r)" % (e,))
        return None, None
    return bs, store


def _write_receipt(toplevel, worktree_id, session_id, snapshot_sha, tree_sha,
                    source_head, captured, excluded):
    """Advisory only: a missing or refusing store must never fail a
    snapshot that already succeeded (requirement 9's own text: "warn once
    and continue").

    Delegates the actual INSERT to Store.write_autosave_receipt (the
    wiring item, prerelease fix round): this used to run hand-written
    BEGIN IMMEDIATE / INSERT / COMMIT directly on the store's own
    connection, bypassing bm_store._exec, the one function that tells a
    merely-busy database apart from a genuinely corrupt one. Giving the
    store its own method means a receipt write gets the exact same
    busy/corrupt handling every other mutation in that file gets, instead
    of a second, weaker copy of it here."""
    bs, store = _open_store_for_receipt(toplevel)
    if store is None:
        return
    try:
        store.write_autosave_receipt(worktree_id, session_id, snapshot_sha,
                                      tree_sha, source_head, captured, excluded)
    except Exception as e:
        _warn("could not write an autosave receipt (%r); the snapshot still "
              "counts, but is not recorded" % (e,))
    finally:
        try:
            store.close()
        except Exception:  # sbe: allow-silent closing a store handle after the receipt write already succeeded or already warned is best-effort cleanup
            pass


def _delete_receipts_for_shas(toplevel, worktree_id, shas):
    """GATE F (prerelease fix round): a receipt row must never outlive the
    snapshot ref that made it true. Reproduced: thirteen sessions,
    retention ten, and the pruned session's receipt row survived, so
    has_receipt reported safety for work whose ref was gone. Called from
    _prune_old_snapshots in the SAME call that deletes the refs. Advisory
    and best-effort like every other receipt operation here: a missing or
    refusing store must never fail pruning itself."""
    bs, store = _open_store_for_receipt(toplevel)
    if store is None:
        return
    try:
        store.delete_autosave_receipts(worktree_id, shas)
    except Exception as e:
        _warn("could not delete pruned autosave receipt(s) (%r); has_receipt "
              "may report a pruned snapshot as still safe until this is "
              "retried" % (e,))
    finally:
        try:
            store.close()
        except Exception:  # sbe: allow-silent closing a store handle after the receipt delete already succeeded or already warned is best-effort cleanup
            pass


def _receipt_snapshot_is_live(toplevel, worktree_id, snapshot_shas):
    """Does any of these recorded snapshot commits still have a ref pointing
    at it? This is the guard the old has_receipt did not have at all: it
    trusted the row and returned True unconditionally.

    A separate, named function (not an inline set comprehension) on purpose,
    the same way _snapshot_sort_key and _checked are: a reinjection test can
    monkeypatch exactly this symbol back to the old "trust the row" shape
    and prove the calibration.

    An unreadable ref store enumerates as empty and therefore answers False.
    That is the deliberate direction: callers read a True as "there is a
    snapshot", and being unable to confirm one is not evidence that one
    exists."""
    live = {sha for _ref, sha in list_snapshot_refs(toplevel, worktree_id)}
    return any(sha in live for sha in snapshot_shas)


def has_receipt(toplevel, worktree_id, session_id):
    """True when this session has a receipt whose snapshot IS STILL THERE.

    FINDING 1 (external audit, 2026-07-27) landed on this function: it used
    to be `SELECT 1 ... LIMIT 1`, a bare existence probe. No ORDER BY, no
    created_at, no read of the row's own snapshot_sha, and no check that the
    snapshot it describes still exists. Since receipt deletion is
    best-effort, a pruned or hand-deleted snapshot kept answering yes, and
    the compact hint turned that yes into "Your files are autosaved".

    It now reads the newest row's own snapshot_sha and re-resolves it
    against the refs that actually exist, so a receipt whose ref is gone is
    a False. Still a HISTORICAL question ("does this session have a
    surviving snapshot?"), which is the right question for a diagnostic;
    the PRESENT-TENSE question the compact hint must ask instead
    ("is the newest work recoverable right now?") is verify_recoverable.

    Pure on the git side (resolve_ref_sha reads ref storage directly, no
    subprocess) and READ-ONLY on the store side (see
    _open_readonly_store_for_receipt): a diagnostic read must never be able
    to create the thing it is checking."""
    bs, store = _open_readonly_store_for_receipt(toplevel)
    if store is None:
        return False
    try:
        rows = store.conn.execute(
            "SELECT snapshot_sha FROM autosave_receipts WHERE worktree_id=? AND "
            "session_id=? ORDER BY created_at DESC, id DESC", (worktree_id, session_id)).fetchall()
    except Exception:
        return False
    finally:
        try:
            store.close()
        except Exception:  # sbe: allow-silent closing a read-only store handle after the query already ran is best-effort cleanup
            pass
    if not rows:
        return False
    return _receipt_snapshot_is_live(
        toplevel, worktree_id, [row["snapshot_sha"] for row in rows])


# ---------------------------------------------------------------------------
# The core operation.
# ---------------------------------------------------------------------------
def snapshot(toplevel, session_id, reason):
    """Snapshot the working tree of `toplevel` into a namespaced ref.
    Never raises past this function: every caller (a hook or the CLI) must
    exit 0 regardless (never-block). Returns a small dict describing what
    happened, for tests and for anyone who wants to log it.

    Two things wrap the pipeline itself, both from the 2026-07-27 audit:

    FINDING 13, the per-worktree LOCK. The whole pipeline is serialized per
    worktree, so a clean snapshot and a dirty one can no longer interleave
    and publish out of order. Losing the lock is a loud stand-down, never a
    silent overwrite and never a block: autosave is advisory.

    FINDING 1, the EVENT. A "pending" event is opened before any git call
    and closed with the real outcome, so the compact hint can ask whether
    the CURRENT work is recoverable instead of whether this session ever
    once snapshotted. A failed attempt therefore replaces an earlier
    success, which is exactly the case that used to print a false
    all-clear."""
    worktree_id = worktree_id_for(toplevel)
    lock = _WorktreeLock(toplevel, worktree_id)
    if not lock.acquire():
        _warn("another autosave is already snapshotting this worktree (lock %s); "
              "standing down rather than racing it. Nothing was published."
              % lock.path)
        return {"ok": False, "reason": "locked"}
    event = begin_snapshot_event(toplevel, worktree_id, session_id, reason)
    try:
        return _snapshot_locked(toplevel, worktree_id, session_id, reason, event)
    finally:
        lock.release()


def _snapshot_locked(toplevel, worktree_id, session_id, reason, event):
    """The snapshot pipeline proper, always under the worktree lock.

    In order (requirement 5, FC): seed a throwaway temp index from HEAD when
    HEAD exists, stage the current working tree onto it (secret-shaped paths
    excluded from this stage only, never from the seed), write the resulting
    tree, and either commit it (dirty case) or clear the latest pointer
    (clean case, FD). Every git call's return code is checked (FE) before
    the next step runs, and the empty tree sha is refused explicitly as a
    second, independent guard."""
    tmp_fd, index_path = tempfile.mkstemp(prefix="bm-autosave-index-")
    os.close(tmp_fd)
    try:
        os.remove(index_path)  # git rejects a zero-byte index; let it build a fresh one
    except OSError:  # sbe: allow-silent removing the fresh mkstemp file before building the git index is expected; git rejects a zero-byte index and needs the path free
        pass
    try:
        head = _run_git(toplevel, "rev-parse", "-q", "--verify", "HEAD")
        has_head = head.returncode == 0
        idx_env = {"GIT_INDEX_FILE": index_path}

        if has_head:
            _checked(_run_git(toplevel, "read-tree", "HEAD", env=idx_env), "read-tree HEAD")

        _checked(
            _run_git(toplevel, "add", "-A", "--", ".", *_exclude_pathspecs(), env=idx_env),
            "add")

        tree = _checked(_run_git(toplevel, "write-tree", env=idx_env), "write-tree").stdout.strip()
        if not tree or tree == EMPTY_TREE_SHA:
            _warn("write-tree produced the empty tree; refusing to publish an "
                  "empty snapshot over any existing one")
            finish_snapshot_event(toplevel, event, EVENT_FAILED,
                                  detail="write-tree produced the empty tree")
            return {"ok": False, "reason": "empty-tree"}

        if has_head:
            head_tree = _run_git(toplevel, "rev-parse", "-q", "--verify", "HEAD^{tree}").stdout.strip()
            if tree == head_tree:
                _clear_latest_if_present(toplevel, worktree_id)
                finish_snapshot_event(toplevel, event, EVENT_CLEAN, tree=tree)
                return {"ok": True, "reason": "clean", "tree": tree}

        parent = head.stdout.strip() if has_head else ""
        msg = "brothermode autosave: %s" % reason
        if parent:
            commit_result = _run_git(toplevel, "commit-tree", tree, "-p", parent, "-m", msg)
        else:
            commit_result = _run_git(toplevel, "commit-tree", tree, "-m", msg)
        commit = _checked(commit_result, "commit-tree").stdout.strip()
        if not commit:
            _warn("commit-tree produced no sha; aborting without touching the latest pointer")
            finish_snapshot_event(toplevel, event, EVENT_FAILED,
                                  detail="commit-tree produced no sha")
            return {"ok": False, "reason": "no-commit"}

        # FINDING 13: both writes below carry the old value they expect, so
        # a losing writer FAILS instead of silently winning. The snapshot ref
        # is created with an empty old value, which git reads as "this ref
        # must not exist yet"; the shared latest pointer moves only from the
        # exact sha this process read a moment ago.
        sref = snapshot_ref(worktree_id, session_id, _stamp())
        _checked(_update_ref_cas(toplevel, sref, commit, ""), "update-ref snapshot")

        prev = _run_git(toplevel, "rev-parse", "-q", "--verify", latest_ref(worktree_id))
        prev_sha = prev.stdout.strip() if prev.returncode == 0 else ""
        published = _update_ref_cas(toplevel, latest_ref(worktree_id), commit, prev_sha)
        if published.returncode != 0:
            # The work is safe (its own ref exists and is checked below);
            # only the convenience pointer lost a race. Loud, because the
            # old code could not even tell this had happened.
            _warn("the shared latest pointer moved while this snapshot was being "
                  "written, so it was NOT overwritten (%s). This snapshot is kept "
                  "at %s and `recover` enumerates snapshot refs, so nothing is lost."
                  % ((published.stderr or "").strip(), sref))

        _prune_old_snapshots(toplevel, worktree_id)

        # FINDING 1: the event turns "saved" only after the ref has been
        # written AND READ BACK, through the very same reader the compact
        # hint will use, and only after retention pruning has had its turn
        # (nothing that could delete a ref runs after this point). A write
        # whose result cannot be read back is not a save, and must never be
        # reported as one.
        written = resolve_ref_sha(toplevel, sref)
        if written != commit:
            _warn("wrote snapshot ref %s but read back %r instead of %s; refusing "
                  "to record this as saved" % (sref, written, commit))
            finish_snapshot_event(toplevel, event, EVENT_FAILED,
                                  detail="the snapshot ref did not read back as written")
            return {"ok": False, "reason": "readback-mismatch", "ref": sref, "commit": commit}

        finish_snapshot_event(toplevel, event, EVENT_SAVED, ref=sref, sha=commit, tree=tree)

        _warn_new_untracked(toplevel)
        captured, excluded = _count_paths(toplevel)
        _write_receipt(toplevel, worktree_id, session_id, commit, tree, parent, captured, excluded)

        return {"ok": True, "reason": reason, "commit": commit, "tree": tree, "ref": sref,
                "latest_published": published.returncode == 0}
    except SnapshotAborted as e:
        _warn(str(e))
        finish_snapshot_event(toplevel, event, EVENT_FAILED, detail=str(e))
        return {"ok": False, "reason": "aborted", "error": str(e)}
    finally:
        try:
            os.remove(index_path)
        except OSError:  # sbe: allow-silent cleanup of the temp index file in the pipeline's own finally is best-effort; the snapshot outcome was already decided above
            pass


# ---------------------------------------------------------------------------
# Consent gate (I1, external review, Loop 3/5). Reproduced by direct
# execution: cmd_precompact wrote a namespaced git ref, a snapshot commit
# (including untracked files), and a JSON event file with NO
# ~/.brotherme/config.json present at all -- the one write-capable entry
# point under tools/*.py that had no consent gate, while
# tools/bm_sessionstart.sh and tools/bm_telemetry.py already refused to
# write a single byte before the founder had run scripts/setup.py.
#
# scripts/setup.py is the ONE place that schema is read or written (its own
# docstring says so); this loads it by path, the SAME technique
# tools/bm_telemetry.py's own _load_bm_setup/_get_bm_setup/_consented use
# (see tools/bm_telemetry.py:592), so this module can never carry a second,
# drifting copy of what "consented" means. Loading a module that itself
# imports subprocess (setup.py's own doctor invocation, never reached from
# the pure read_config/is_consented path used here) does not add a
# subprocess import to THIS file for the no-subprocess-in-tools/ audit's
# purposes (tools/test_bm.py scans source LINES of tools/*.py; no `import
# subprocess`-shaped line for setup.py is ever written here, only a load by
# path, and this file's own documented subprocess use stays git-only).
# ---------------------------------------------------------------------------
def _load_bm_setup():
    """Load scripts/setup.py by path. Never raises: an unloadable setup
    module is a fail-CLOSED condition for _consented(), not a crash."""
    try:
        import importlib.util
        root = os.path.dirname(HERE)
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_autosave", os.path.join(root, "scripts", "setup.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent an unloadable setup.py is the documented fail-CLOSED path for _consented(); no consent without it
        return None


_bm_setup_cache = []


def _get_bm_setup():
    if not _bm_setup_cache:
        _bm_setup_cache.append(_load_bm_setup())
    return _bm_setup_cache[0]


def _consented():
    """True only when scripts/setup.py's own is_consented() says so. Fails
    CLOSED (not consented) on any load error, missing config, or a corrupt
    one, matching tools/bm_telemetry.py's own _consented exactly (same
    schema, same reader, same failure direction), so the write-capable entry
    points in this file and in bm_telemetry.py can never disagree about
    whether the founder has said yes. config_path() (inside scripts/setup.py)
    honors BROTHERME_CONFIG, so a test can point this at a throwaway file the
    same way it already does for bm_telemetry.py."""
    mod = _get_bm_setup()
    if mod is None:
        return False
    try:
        cfg, _err = mod.read_config()
        return bool(mod.is_consented(cfg))
    except Exception:
        return False


_CONSENT_REQUIRED_LINE = (
    "bm_autosave: setup is not complete yet; run: python3 scripts/setup.py")


# ---------------------------------------------------------------------------
# Hook and CLI entrypoints. Every one of these exits 0 no matter what
# (requirement 10): main() wraps the whole dispatch in a bare except.
#
# I1's gate applies to every entry point below that can WRITE, enumerated
# here once so its scope is a decision, not an omission:
#   cmd_precompact  WRITES (a namespaced git ref, a commit, the event file,
#                   a receipt row). Fires AUTOMATICALLY from
#                   hooks/hooks.json's PreCompact entry, with no founder
#                   action in between. GATED on _consented(), first thing.
#   cmd_tick        WRITES the same way as cmd_precompact, PLUS its own tick
#                   counter file under the vault's telemetry directory, on
#                   every qualifying tool call once BROTHERMODE_AUTOSAVE is
#                   set. Also fires automatically (a PostToolUse hook, where
#                   wired), and setting an opt-in shell variable is not the
#                   same act as running scripts/setup.py. GATED on
#                   _consented(), checked BEFORE the opt-in check itself, so
#                   even the tick counter file never gets created
#                   pre-consent.
#   cmd_recover     Reads EXISTING snapshot refs and checks out a NEW git
#                   worktree OUTSIDE this repository's working tree (never
#                   touches the live tree; see BLOCKER 1 above); it creates
#                   no new project content. NOT gated: it is explicit and
#                   founder-invoked from a terminal, never fired by a hook.
#                   With cmd_precompact and cmd_tick both gated, any
#                   snapshot it could find either predates this fix or was
#                   made by a session that had already consented. Refusing
#                   to hand back a founder's own, already-saved work because
#                   consent was later withdrawn would be a second, unrelated
#                   defect: recovery is the one operation this module must
#                   never block on anything (requirement 6's own promise).
# ---------------------------------------------------------------------------
def _read_hook_payload():
    """Best-effort JSON parse of stdin, matching bm_telemetry.py's own hook
    readers. Never raises: a hook that sends garbage, or nothing, must not
    crash an advisory tool."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def cmd_precompact():
    """PreCompact hook target: snapshot once, unconditionally once consent
    is on record. Fired right before the token-death moment. Returns the
    snapshot() result, or None on a clean no-op OR a pre-consent refusal, so
    a caller, including a test, can inspect what happened; the hook itself
    ignores the return value.

    I1: pre-consent this prints one plain sentence naming
    python3 scripts/setup.py and returns immediately, before
    _read_hook_payload, before resolve_toplevel, before any git call:
    nothing is written, not a ref, not a commit object, not the event
    file."""
    if not _consented():
        print(_CONSENT_REQUIRED_LINE)
        return None
    payload = _read_hook_payload()
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id") or "unknown"
    toplevel = resolve_toplevel(cwd)
    if toplevel is None:
        return None  # not a git repo here: clean no-op, advisory surfaces fail open
    return snapshot(toplevel, session_id, "precompact")


def _tick_counter_path(session_id):
    safe = _safe_ref_component(session_id)
    return os.path.join(TEL_DIR, ".autosave-tick-%s" % safe)


def cmd_tick():
    """PostToolUse hook target, opt-in via BROTHERMODE_AUTOSAVE: snapshot
    every N tool calls, and warn once on a runaway session.

    I1: gated on consent FIRST, before even the BROTHERMODE_AUTOSAVE opt-in
    check, so setting that shell variable without ever running
    scripts/setup.py cannot write the tick counter file either (see
    cmd_precompact's docstring above for the full finding)."""
    if not _consented():
        print(_CONSENT_REQUIRED_LINE)
        return
    if not os.environ.get("BROTHERMODE_AUTOSAVE"):
        return
    payload = _read_hook_payload()
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id") or "unknown"
    toplevel = resolve_toplevel(cwd)
    if toplevel is None:
        return
    every = _parse_int_env("BROTHERMODE_AUTOSAVE_EVERY", DEFAULT_TICK_EVERY)
    runaway_at = _parse_int_env("BROTHERMODE_RUNAWAY_AT", DEFAULT_RUNAWAY_AT)

    ctr_path = _tick_counter_path(session_id)
    try:
        os.makedirs(TEL_DIR, exist_ok=True)
    except OSError:  # sbe: allow-silent the telemetry dir is best-effort; a failed makedirs just means the tick counter read below falls back to n=0
        pass
    try:
        with open(ctr_path, "r") as f:
            n = int((f.read() or "0").strip())
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        with open(ctr_path, "w") as f:
            f.write(str(n))
    except OSError:  # sbe: allow-silent the tick counter write is advisory; PreCompact hooks must never block a session over a full disk
        pass

    if n % every == 0:
        snapshot(toplevel, session_id, "tick %d" % n)

    if n >= runaway_at:
        warned_path = ctr_path + ".warned"
        if not os.path.exists(warned_path):
            try:
                open(warned_path, "w").close()
            except OSError:  # sbe: allow-silent the runaway-warned marker is best-effort; worst case is the same warning printing again next tick
                pass
            try:
                sys.stdout.write(json.dumps({
                    "systemMessage":
                        "BrotherMode: this session has made %d tool calls, which can "
                        "signal an unbounded loop. Run `python3 %s recover` to see "
                        "what is autosaved." % (n, os.path.abspath(__file__))
                }) + "\n")
            except Exception:  # sbe: allow-silent the runaway systemMessage write is a hook stdout write that must never crash the never-block hook
                pass


def _chmod_best_effort(path, mode):
    """Best-effort permission tightening, the same shape bm_store.py's own
    _chmod_best_effort uses (duplicated here on purpose rather than
    importing bm_store.py for one line: this module's git-snapshot half
    and the store's persistence half stay independent, and a founder who
    has read one BrotherMode module can read the other). Windows ACLs make
    a POSIX mode a courtesy at best (os.chmod there can only toggle the
    read-only bit), so a failure, or a silent no-op on a platform that will
    not honor it, is expected and never escalated: this function does not
    claim a guarantee the platform will not keep."""
    try:
        os.chmod(path, mode)
    except OSError:  # sbe: allow-silent documented: a chmod failure or silent no-op on a platform that will not honor POSIX modes is expected and never escalated
        pass


def _fallback_snapshot(toplevel, worktree_id):
    """FINDING 13's recovery half: when the shared "latest" pointer is gone
    or does not resolve, ENUMERATE this worktree's snapshot refs and offer
    the newest one that still resolves, so a lost pointer never means lost
    work. Returns (ref, sha), or ("", "") when there is nothing to offer.

    The one exclusion is deliberate and is FD: snapshots older than the last
    recorded clean-tree check are NOT offered, because that pointer was
    cleared on purpose when the founder returned to a clean tree, and
    resurrecting deliberately discarded WIP is its own kind of data
    accident. See _record_latest_cleared for the reconciliation."""
    cleared = _latest_cleared_stamp(toplevel, worktree_id)
    for ref, sha in reversed(list_snapshot_refs(toplevel, worktree_id)):
        if cleared and _snapshot_sort_key(ref) <= cleared:
            continue
        return ref, sha
    return "", ""


def _print_snapshot_inventory(toplevel, worktree_id, limit=5):
    """Never end a failed recovery on "nothing found" alone: print what DOES
    exist (newest first) so the founder can check out an older snapshot by
    hand, including the ones the FD rule declines to offer automatically."""
    refs = list_snapshot_refs(toplevel, worktree_id)
    if not refs:
        print("  No snapshot refs exist for this worktree at all.")
        return
    print("  %d snapshot ref(s) do exist for this worktree. The newest:" % len(refs))
    for ref, sha in list(reversed(refs))[:limit]:
        print("    %s  %s" % (sha[:12], ref))
    print("  Check one out without touching your live tree with:")
    print("    git -C %s worktree add --detach /tmp/bm-look <sha>" % toplevel)


def _prepare_recovery_worktree_dir():
    """The directory `git worktree add` will check the recovery snapshot
    out into, owner-only (0700) from the instant it exists to the instant
    anything is checked out into it (BLOCKER 1, release-blockers spec,
    2026-07-26, VERIFIED BY ORCHESTRATOR).

    tempfile.mkdtemp() already creates its directory at 0700. The OLD code
    then called os.rmdir() on that directory, reasoning (in a comment) that
    `git worktree add` needs a path that does not exist yet. That reasoning
    was never actually true (verified by hand: `git worktree add --detach
    <an-existing-empty-dir> <sha>` succeeds and leaves the directory's own
    mode untouched), and the rmdir meant git recreated the path itself, at
    the process umask (typically 0755 under the common 022 default)
    instead of 0700. On a shared machine with a world-writable temp
    directory, that made every recovered worktree, and any untracked
    private file inside it, readable by any local account (reproduced:
    drwxr-xr-x on the directory, -rw-r--r-- on an untracked secret file
    inside it).

    The fix is what this function does NOT do: it never discards mkdtemp's
    own directory, so there is no window where a less-restrictive mode
    could apply before this returns. The chmod below is defense in depth
    (correct even if a platform's mkdtemp default or umask handling were
    ever looser than expected), not the load-bearing half of the fix."""
    d = tempfile.mkdtemp(prefix="bm-autosave-recover-")
    _chmod_best_effort(d, 0o700)
    return d


def cmd_recover(argv):
    """Print exactly how to get saved work back, and DO it: create a NEW
    git worktree at a temporary path checked out at the latest snapshot,
    and print its location. Never writes into the live working tree
    (requirement 6): the old in-place `git restore --worktree .` path,
    which could delete a tracked file the snapshot never captured (FC), is
    gone, not merely warned about.

    BLOCKER 1 (release-blockers spec, 2026-07-26): the recovery directory
    is owner-only from the moment it exists (see
    _prepare_recovery_worktree_dir), and the mode actually achieved is
    printed, not merely assumed, so the founder can see what protection a
    given platform actually gave the recovered work."""
    start = argv[0] if argv else os.getcwd()
    toplevel = resolve_toplevel(start)
    if toplevel is None:
        print("bm_autosave: %s is not inside a git repository" % start)
        return
    worktree_id = worktree_id_for(toplevel)
    ref = latest_ref(worktree_id)
    r = _run_git(toplevel, "rev-parse", "-q", "--verify", ref)
    sha = r.stdout.strip() if r.returncode == 0 else ""
    if not sha:
        ref, sha = _fallback_snapshot(toplevel, worktree_id)
        if not sha:
            print("bm_autosave: no autosave found for %s (the latest pointer is "
                  "empty and no snapshot ref is newer than the last clean check)."
                  % toplevel)
            _print_snapshot_inventory(toplevel, worktree_id)
            return
        print("bm_autosave: the latest pointer is missing, so this fell back to "
              "the newest snapshot ref that still resolves:")
        print("  %s" % ref)
    tmp_dir = _prepare_recovery_worktree_dir()
    wt = _run_git(toplevel, "worktree", "add", "--detach", tmp_dir, sha)
    if wt.returncode != 0:
        try:
            os.rmdir(tmp_dir)
        except OSError:  # sbe: allow-silent cleanup of the failed worktree add's own empty tmp dir is best-effort; the failure is already reported below
            pass
        print("bm_autosave: could not create a recovery worktree (%s)"
              % (wt.stderr or "").strip())
        return
    mode = stat.S_IMODE(os.stat(tmp_dir).st_mode)
    print("bm_autosave: recovered snapshot %s into a NEW worktree at:" % sha)
    print("  %s" % tmp_dir)
    print("  permissions: %04o (owner-only; best-effort only on platforms, "
          "e.g. Windows, that do not fully enforce a POSIX mode)" % mode)
    print("  Your live working tree at %s was never touched. Inspect the folder "
          "above, copy back what you need, then run" % toplevel)
    print("  `git -C %s worktree remove %s` when you are done with it."
          % (toplevel, tmp_dir))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    mode = argv[0] if argv else ""
    try:
        if mode == "precompact":
            cmd_precompact()
        elif mode == "tick":
            cmd_tick()
        elif mode == "recover":
            cmd_recover(argv[1:])
        else:
            print("usage: bm_autosave.py {precompact|tick|recover [repo]}")
    except Exception as e:
        # The absolute backstop (requirement 10): nothing this module does
        # is allowed to block a session, so even a bug here is swallowed and
        # reported, never raised.
        _warn("swallowed error (never blocks): %r" % (e,))
    sys.exit(0)


if __name__ == "__main__":
    main()
