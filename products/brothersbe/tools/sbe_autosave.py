#!/usr/bin/env python3
"""BrotherSBE autosave: mechanical work-preservation. Python port of the
retired sh autosave hook, same modes, same log sentences, same invariants.

WHY A PORT (2026-08-17): the Windows engineer's first-round report showed the
sh implementation dying on Windows in the worst possible way: process spawn
costs ~24 ms there against ~1.5 ms on Linux, the scan forked several processes
per candidate file, so a 5,974-file repo projected to ~573 s against the
harness's default hook timeout (60 s on the engineer's build; current docs
say 600 s, which that repo would still exceed with margin to spare on a
larger tree), and the kill was SILENT: no snapshot, no log line, nothing. sh itself is also not on the Windows PATH; the hook only
ran because Claude Code spawned it through Git Bash. This port scans
in-process (zero forks per file), adds a self-deadline that fails LOUDLY as
NO-DATA, and removes the sh dependency. The sibling BrotherModeUp made the
same move earlier for the same reason (PARITY.md, autosave row).

WHAT IT DOES
  Takes a snapshot of the ENTIRE working tree, including untracked (new)
  files, and stores it as a git commit reachable only from a private ref:
  refs/brothersbe/autosave/<worktree-id>, id derived from the worktree's own
  path. It never touches your branch, your index, or your working tree, and
  it NEVER pushes anywhere.

WHAT IT DOES NOT DO
  Every candidate file's CONTENT is read before `git add` runs; a file whose
  content matches a secret shape, or that is too large or too binary to scan,
  is kept out and written down in the exclusion log with its reason. That is
  pattern matching, not a guarantee. The ref is local, which is not the same
  as private.

INVARIANTS (do not break these)
  - Never blocks work: every path exits 0, always.
  - No network: runs git locally only, never `git push`, never a remote.
  - Non-invasive: a throwaway temp index (GIT_INDEX_FILE) so the real index
    and working tree are never modified; only a custom ref is written.
  - Never silent: every skip, refusal, or deadline writes a log line naming
    what was not done. A scan that cannot finish is NO-DATA, not a pass.

MODES
  precompact   from the PreCompact hook (the token-death moment).
  tick         from the PostToolUse hook, OPT-IN via BROTHERSBE_AUTOSAVE.
  recover      print exactly how to get the saved work back.
"""
import json
import os
import re
import stat as stat_mod
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone

VAULT = os.environ.get("BROTHERSBE_VAULT",
                       os.path.join(os.path.expanduser("~"), "BrotherSBEVault"))
TEL_DIR = os.path.join(VAULT, "99-System", "telemetry")
AUTOSAVE_NS = "refs/brothersbe/autosave"
EXCL_LOG = os.path.join(TEL_DIR, "autosave-exclusions.log")


def _int_env(name, default):
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() else default


TICK_EVERY = _int_env("BROTHERSBE_AUTOSAVE_EVERY", 20)
RUNAWAY_AT = _int_env("BROTHERSBE_RUNAWAY_AT", 600)
MAX_BYTES = _int_env("BROTHERSBE_AUTOSAVE_MAX_BYTES", 1048576)
MAX_EXCLUSIONS = _int_env("BROTHERSBE_AUTOSAVE_MAX_EXCLUSIONS", 200)
try:
    DEADLINE_S = float(os.environ.get("BROTHERSBE_AUTOSAVE_DEADLINE_S", "45"))
except ValueError:
    DEADLINE_S = 45.0

# The content-scan shapes, copied verbatim in intent from the sh version
# (grep -E and grep -Ei become one case-sensitive and one case-insensitive
# compiled pattern; POSIX [[:space:]] becomes \s).
SECRET_SHAPES = re.compile(
    r"(sk|rk)[-_][A-Za-z0-9_-]{12,}|gh[oprsu]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN[ A-Z]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
SECRET_ASSIGNMENTS = re.compile(
    r"(pass(word|wd|phrase)?|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential)s?\s*[:=]\s*.?[A-Za-z0-9/+_=-]{8,}"
    r"|bearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)

# The name patterns: one list feeds both `git add` (as exclude pathspecs) and
# secret_named() (for the record), so a name excluded from the snapshot is a
# name the record explains.
STATIC_EXCLUDE_PATHSPECS = (
    ":(exclude,glob)**/.env", ":(exclude).env", ":(exclude,glob)**/.env.*",
    ":(exclude,glob)**/.envrc", ":(exclude).envrc",
    ":(exclude,glob)**/.netrc", ":(exclude).netrc",
    ":(exclude,glob)**/.npmrc", ":(exclude).npmrc",
    ":(exclude,glob)**/*.pem", ":(exclude,glob)**/*.key",
    ":(exclude,glob)**/*.p12", ":(exclude,glob)**/*.keystore",
    ":(exclude,glob)**/*.jks", ":(exclude,glob)**/*.ppk",
    ":(exclude,glob)**/id_rsa", ":(exclude,glob)**/id_dsa",
    ":(exclude,glob)**/id_ecdsa", ":(exclude,glob)**/id_ed25519",
    ":(exclude,glob)**/*.pfx",
)
_SECRET_BASENAMES = {".env", ".envrc", ".netrc", ".npmrc",
                     "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".keystore", ".jks", ".ppk", ".pfx")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LINE_BREAKS = re.compile("[\r\n\v\f\x85\u2028\u2029]+")


def one_line(text):
    """Every stdout report line is flattened through this choke point, because
    several lines here interpolate git output, refs and paths, and a value
    carrying a line break or a cursor escape would forge a second report line.
    Mirrors tools/sbe_checks.py::one_line (the class version: control, format
    and surrogate characters become visible escapes, a tab becomes a space)
    rather than importing it, under the same one-directional-dependency rule
    tools/sbe_gate.py names for GIT_SSH_VERIFY_FLOOR: a hook stays
    import-light. Interior spaces are kept, because recover() prints an
    indented block an operator reads and copies."""
    out = []
    for ch in _LINE_BREAKS.sub(" \\n ", str(text)):
        if ch == "\t":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            n = ord(ch)
            out.append("\\x%02x" % n if n <= 0xFF else
                       "\\u%04x" % n if n <= 0xFFFF else "\\U%08x" % n)
        else:
            out.append(ch)
    return "".join(out)


def say(text):
    sys.stdout.write(one_line(text) + "\n")
    sys.stdout.flush()


def _git(args, cwd=None, env_extra=None, data=None):
    """Run git with the inherited GIT_* environment stripped, so a harness or
    session that exported GIT_INDEX_FILE or GIT_DIR cannot make this hook
    corrupt the wrong repository (a lesson the sibling's port learned first).
    Returns (returncode, stdout). Never raises; a git that cannot run at all
    reads as (127, "")."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, env=env, input=data,
                           capture_output=True, text=True)
        return p.returncode, p.stdout
    except OSError:
        return 127, ""


def _fallback_log_path(cwd):
    rc, out = _git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc != 0 or not out.strip():
        return None
    gd = out.strip()
    if not os.path.isabs(gd):
        gd = os.path.join(cwd or os.getcwd(), gd)
    return os.path.join(gd, "brothersbe-autosave-fallback.log")


def _append(path, line, mode=None):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass  # sbe: allow-silent the line is already on disk; a refused chmod
            # changes the file's mode, never its content, and this hook must not
            # fail a session over a permission bit it could not tighten


def log_line(msg, cwd=None):
    """Best-effort append to the vault log; falls back beside the repo's git
    metadata when the vault itself cannot be written, so the reason for a
    skip is not lost outright. Never raises."""
    line = "%s %s\n" % (_now(), msg)
    try:
        os.makedirs(TEL_DIR, exist_ok=True)
        _append(os.path.join(TEL_DIR, "autosave.log"), line)
        return
    except OSError:
        pass  # sbe: allow-silent the second destination below is this failure's
        # handler, and when that one fails too it reports on stderr
    try:
        fb = _fallback_log_path(cwd)
        if fb:
            _append(fb, "%s (vault unwritable) %s\n" % (_now(), msg), mode=0o600)
    except OSError as e:
        # Last resort, and it used to be silence. Both destinations are
        # unwritable, so this line is the only place an operator can learn that
        # an autosave event went unrecorded. stderr, not a raise: the hook's
        # contract is exit 0, and a lost log line must not cost the snapshot it
        # was describing.
        sys.stderr.write("sbe_autosave: could not record a log line anywhere "
                         "(%s: %s). The event still happened; only its record "
                         "was lost: %s\n" % (type(e).__name__, e, msg))


def excl_record(msg, cwd=None):
    """The exclusion record. Paths and reasons only, never the matched
    content. Owner-only, best effort, never fatal."""
    line = "%s %s\n" % (_now(), msg)
    try:
        os.makedirs(TEL_DIR, exist_ok=True)
        fresh = not os.path.exists(EXCL_LOG)
        _append(EXCL_LOG, line, mode=0o600 if fresh else None)
        return
    except OSError:
        pass  # sbe: allow-silent the second destination below is this failure's
        # handler, and when that one fails too it reports on stderr
    try:
        fb = _fallback_log_path(cwd)
        if fb:
            _append(fb, "%s (vault unwritable) %s\n" % (_now(), msg), mode=0o600)
    except OSError as e:
        # Last resort, and it used to be silence. Both destinations are
        # unwritable, so this line is the only place an operator can learn that
        # an autosave event went unrecorded. stderr, not a raise: the hook's
        # contract is exit 0, and a lost log line must not cost the snapshot it
        # was describing.
        sys.stderr.write("sbe_autosave: could not record a log line anywhere "
                         "(%s: %s). The event still happened; only its record "
                         "was lost: %s\n" % (type(e).__name__, e, msg))


def secret_named(path):
    base = os.path.basename(path)
    if base in _SECRET_BASENAMES or base.startswith(".env."):
        return True
    return base.endswith(_SECRET_SUFFIXES)


def is_binary(path):
    """A NUL byte in the first 4096 bytes. Raises OSError to the caller,
    which treats an unreadable file as never-scanned (the sh header's own
    rule: a file the scanner could not open must never be treated as scanned
    and clean)."""
    with open(path, "rb") as f:
        return b"\0" in f.read(4096)


def _autosave_ref(top):
    rc, wid = _git(["hash-object", "--stdin"], cwd=top, data=top)
    wid = wid.strip()
    return "%s/%s" % (AUTOSAVE_NS, wid) if rc == 0 and wid else AUTOSAVE_NS


def _legacy_autosave_ref(top):
    """The id an OLDER sh version derived (POSIX cksum CRC-32 of the same
    path). Read-only: recover consults it so an old snapshot is found rather
    than orphaned. cksum ships on every POSIX box and in Git Bash; where it
    is genuinely absent the legacy lookup is skipped, same as the sh version
    when cksum failed."""
    try:
        p = subprocess.run(["cksum"], input=top.encode(), capture_output=True)
        wid = p.stdout.decode(errors="replace").split()[0] if p.returncode == 0 else ""
    except (OSError, IndexError):
        wid = ""
    return "%s/%s" % (AUTOSAVE_NS, wid) if wid else AUTOSAVE_NS


def production_repo(top):
    """A repository the operator has DECLARED production. Returns the marker
    it read, or None."""
    cls = os.environ.get("BROTHERSBE_REPO_CLASS", "").lower()
    if cls in ("production", "prod"):
        return "BROTHERSBE_REPO_CLASS=%s" % os.environ.get("BROTHERSBE_REPO_CLASS")
    marker = os.path.join(top, ".brothersbe-production")
    if os.path.isfile(marker):
        return marker
    return None


def _candidates(top):
    """Tracked plus untracked-unignored paths, NUL-delimited so a newline or
    quote in a file name arrives literally instead of quoted (the sh version
    had to reject such paths as unopenable; this port can scan them)."""
    rc, out = _git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                   cwd=top)
    if rc != 0:
        return None
    return [p for p in out.split("\0") if p]


def snapshot(repo, reason):
    """Snapshot the working tree of `repo` into its per-worktree autosave
    ref. Returns silently on any problem; the caller always exits 0."""
    if not os.path.isdir(repo):
        return
    rc, _ = _git(["rev-parse", "--git-dir"], cwd=repo)
    if rc != 0:
        log_line("skip (not a git repo): %s" % repo, cwd=repo)
        return
    # THE SNAPSHOT COVERS THE WORKTREE THE REF NAMES, or it fails loudly.
    rc, out = _git(["rev-parse", "--show-toplevel"], cwd=repo)
    top = out.strip()
    if rc != 0 or not top:
        log_line("SKIPPED (cannot resolve the worktree top from %s): nothing was saved" % repo,
                 cwd=repo)
        return
    if not os.path.isdir(top):
        log_line("SKIPPED (cannot enter the worktree top %s): nothing was saved" % top,
                 cwd=repo)
        return

    # OPT-IN IN A PRODUCTION REPOSITORY. The skip names both knobs.
    marker = production_repo(top)
    if marker and os.environ.get("BROTHERSBE_AUTOSAVE_PRODUCTION", "").lower() \
            not in ("1", "true", "yes", "on"):
        log_line("SKIPPED (%s): %s is declared a production repository by %s, "
                 "where autosave is opt-in; nothing was saved. Set "
                 "BROTHERSBE_AUTOSAVE_PRODUCTION=1 to enable it there"
                 % (reason, top, marker), cwd=top)
        return

    # THE CONTENT SCAN RUNS BEFORE ANY GIT OBJECT EXISTS: a file rejected
    # here is never turned into a blob. In-process, zero forks per file,
    # which is the whole Windows fix.
    cand = _candidates(top)
    if cand is None:
        log_line("SKIPPED (%s): could not list candidate files in %s; nothing was saved"
                 % (reason, top), cwd=top)
        return
    scanned = 0
    excluded = 0
    excl_args = []
    excl_record("scan (%s) at %s:" % (reason, top), cwd=top)
    start = time.monotonic()

    def reject(path, why):
        nonlocal excluded
        excluded += 1
        excl_args.append(":(exclude,literal)%s" % path)
        excl_record("  excluded %s :: %s" % (path, why), cwd=top)

    for f in cand:
        # The self-deadline (2026-08-17, the Windows engineer's finding): a
        # scan that cannot finish inside the hook window says so and saves
        # nothing, instead of being killed in silence by the harness timeout.
        if scanned % 200 == 0 and time.monotonic() - start > DEADLINE_S:
            log_line("SKIPPED (%s): content scan passed its %ss deadline after %d of %d "
                     "candidate files in %s; nothing was saved, and that is NO-DATA, "
                     "not a pass. Raise BROTHERSBE_AUTOSAVE_DEADLINE_S or reduce the "
                     "candidate set" % (reason, DEADLINE_S, scanned, len(cand), top),
                     cwd=top)
            return
        full = os.path.join(top, f)
        if os.path.islink(full):
            continue  # a symlink is stored as its target string, not as content
        if not os.path.isfile(full):
            continue
        scanned += 1
        if secret_named(f):
            reject(f, "the file name is one of the secret-shaped names")
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            reject(f, "its size could not be read, so its content was never scanned")
            continue
        if size > MAX_BYTES:
            reject(f, "%d bytes, past the %d byte limit, so its content was never scanned"
                   % (size, MAX_BYTES))
            continue
        try:
            if is_binary(full):
                reject(f, "binary content, which this scanner cannot read for secret shapes")
                continue
            with open(full, "rb") as fh:
                text = fh.read(MAX_BYTES).decode("utf-8", errors="replace")
        except OSError:
            reject(f, "it could not be opened, so its content was never scanned")
            continue
        if SECRET_SHAPES.search(text) or SECRET_ASSIGNMENTS.search(text):
            reject(f, "content matched a secret shape")
            continue
    excl_record("  scanned %d candidate file(s), excluded %d" % (scanned, excluded),
                cwd=top)
    if excluded > MAX_EXCLUSIONS:
        # Fail closed, same sentence and reason as the sh version.
        log_line("SKIPPED (%s): the content scan excluded %d of %d candidate file(s) in %s, "
                 "past the %d this script will pass to git in one command; nothing was "
                 "saved. Read %s, then raise BROTHERSBE_AUTOSAVE_MAX_EXCLUSIONS if those "
                 "exclusions are expected"
                 % (reason, excluded, scanned, top, MAX_EXCLUSIONS, EXCL_LOG), cwd=top)
        return

    # A THROWAWAY index: reserve a unique path, let git build a fresh index
    # there itself (git rejects a zero-byte index file).
    fd, tmpidx = tempfile.mkstemp(prefix="sbe-autosave-idx-")
    os.close(fd)
    os.unlink(tmpidx)
    idx_env = {"GIT_INDEX_FILE": tmpidx}
    try:
        rc, out = _git(["rev-parse", "--verify", "-q", "HEAD"], cwd=top)
        parent = out.strip() if rc == 0 else ""
        # Seed the temp index from HEAD first, so an excluded TRACKED file
        # rides at its last-committed state instead of vanishing.
        if parent:
            _git(["read-tree", "HEAD"], cwd=top, env_extra=idx_env)
        add_args = (["add", "-A", "--", "."]
                    + list(STATIC_EXCLUDE_PATHSPECS) + excl_args)
        _git(add_args, cwd=top, env_extra=idx_env)
        rc, out = _git(["write-tree"], cwd=top, env_extra=idx_env)
        tree = out.strip() if rc == 0 else ""

        # Skip if nothing changed since HEAD, and SAY SO.
        if parent and tree:
            rc, out = _git(["rev-parse", "-q", "--verify", "HEAD^{tree}"], cwd=top)
            if rc == 0 and out.strip() == tree:
                log_line("no snapshot (%s): the worktree at %s matches HEAD, "
                         "nothing unlanded to save" % (reason, top), cwd=top)
                return
        commit = ""
        if tree:
            args = ["commit-tree", tree, "-m", "brothersbe autosave: %s" % reason]
            if parent:
                args[2:2] = ["-p", parent]
            rc, out = _git(args, cwd=top)
            commit = out.strip() if rc == 0 else ""
    finally:
        try:
            os.unlink(tmpidx)
        except OSError:
            pass  # sbe: allow-silent the throwaway index lives in the system temp
            # directory and is rebuilt from HEAD every run, so a leftover costs
            # one stale file and a raise here would cost the snapshot itself
    if not commit:
        log_line("SKIPPED (%s): could not write a snapshot commit in %s" % (reason, top),
                 cwd=top)
        return
    # --create-reflog: the ref is single-slot; with a reflog every superseded
    # snapshot stays reachable (git reflog <ref>).
    ref = _autosave_ref(top)
    rc, _ = _git(["update-ref", "--create-reflog", ref, commit], cwd=top)
    if rc == 0:
        log_line("saved %s (%s) at %s covering the whole worktree %s, minus %d of %d "
                 "scanned file(s) the content scan rejected (each named with its reason "
                 "in %s)" % (commit, reason, ref, top, excluded, scanned, EXCL_LOG),
                 cwd=top)


def _writable_telemetry():
    try:
        os.makedirs(TEL_DIR, exist_ok=True)
        probe = os.path.join(TEL_DIR, ".writable.%d" % os.getpid())
        with open(probe, "w"):
            pass
        os.unlink(probe)
        return True
    except OSError:
        return False


def tick(repo, sid):
    """OPT-IN continuous autosave. Serialized read-modify-write on the tick
    counter behind a mkdir lock; every skip writes a log line; a count
    nothing measured never decides a snapshot."""
    if not os.environ.get("BROTHERSBE_AUTOSAVE"):
        return
    safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
    ctr = os.path.join(TEL_DIR, ".autosave-tick-%s" % safe_sid)
    if not _writable_telemetry():
        log_line("tick skipped for %s: the telemetry directory %s cannot be created or "
                 "written, so the tick counter cannot be kept and no snapshot decision "
                 "is made from a number nothing measured. Continuous autosave is OFF "
                 "for this session until that directory is writable" % (sid, TEL_DIR),
                 cwd=repo)
        return
    lock = ctr + ".lock"
    stamp = ctr + ".waitstamp.%d" % os.getpid()
    # NOT best-effort, whatever its shape suggests. This stamp is the only
    # thing that tells a LIVE lock from a dead one below: absent, the
    # os.path.exists(stamp) test reads False, lock_live reads False, and this
    # tick BREAKS a lock whose holder may still be running, which puts two
    # writers in the section. A stamp that could not be written is therefore
    # NO-DATA about the lock's age, and the answer to NO-DATA is never the
    # more destructive branch.
    stamped = True
    stamp_error = ""
    try:
        with open(stamp, "w"):
            pass
    except OSError as e:
        stamped = False
        stamp_error = "%s: %s" % (type(e).__name__, e)
    got_lock = False
    tries = 0
    while True:
        try:
            os.mkdir(lock)
            got_lock = True
            break
        except OSError:
            tries += 1
            if tries >= 40:
                if not stamped:
                    log_line("tick skipped for %s: the wait stamp could not be "
                             "written (%s), so nothing here can tell a live lock "
                             "from a dead one, and a tick that cannot tell does not "
                             "break %s; the count was not incremented"
                             % (sid, stamp_error, lock), cwd=repo)
                    return
                # A lock created DURING this wait is live and contended;
                # breaking a live holder puts two writers in the section.
                try:
                    lock_live = (os.path.exists(stamp)
                                 and os.path.getmtime(lock) > os.path.getmtime(stamp))
                except OSError:
                    lock_live = False
                if lock_live:
                    _cleanup(stamp)
                    log_line("tick skipped for %s: %s is live and contended (it was "
                             "created during this tick's wait, so its holder is not "
                             "dead); the count was not incremented" % (sid, lock),
                             cwd=repo)
                    return
                try:
                    os.rmdir(lock)
                except OSError:
                    pass  # sbe: allow-silent a removal that failed leaves the lock
                    # in place, and the os.mkdir below then fails too and IS
                    # handled, with its own skipped-tick line
                log_line("broke a stale lock for %s: %s predates this tick's whole wait "
                         "and its holder is presumed dead; if that presumption is ever "
                         "wrong this line is the evidence" % (sid, lock), cwd=repo)
                try:
                    os.mkdir(lock)
                    got_lock = True
                except OSError:
                    _cleanup(stamp)
                    log_line("tick skipped for %s: could not take %s; the count was "
                             "not incremented" % (sid, lock), cwd=repo)
                    return
                break
            time.sleep(0.05)
    _cleanup(stamp)
    warn = 0
    n = 0
    try:
        # Counter content is untrusted input: validated before arithmetic,
        # named reset on empty or non-numeric, never a crash.
        raw = ""
        existed = os.path.exists(ctr)
        try:
            with open(ctr, encoding="utf-8", errors="replace") as f:
                raw = f.read().strip()
        except OSError as e:
            # An unreadable counter is not an empty one. Swallowed, this fell
            # through to the raw == "" branch below and logged "a writer died
            # mid-write; count reset to 0", which names a cause nothing
            # observed and throws the real count away. A count that EXISTS and
            # cannot be read is NO-DATA, and no snapshot decision is made from
            # a number nothing measured.
            #
            # The absent file is the ordinary case, not this one: the first
            # tick of a session has no counter yet and starts at 0, which is
            # what `existed` separates. Written after the test for the
            # interval snapshot caught the wider version refusing every first
            # tick on a FileNotFoundError.
            if existed:
                log_line("tick skipped for %s: the counter %s exists and could not "
                         "be read (%s: %s); it was NOT reset, and the count was not "
                         "incremented" % (sid, ctr, type(e).__name__, e), cwd=repo)
                return
        if raw == "":
            if existed:
                log_line("counter for %s was empty (a writer died mid-write); count "
                         "reset to 0" % sid, cwd=repo)
            n = 0
        elif not raw.isdigit():
            log_line("counter for %s held non-numeric content; count reset to 0" % sid,
                     cwd=repo)
            n = 0
        else:
            n = int(raw)
        n += 1
        try:
            with open(ctr, "w", encoding="utf-8") as f:
                f.write(str(n))
        except OSError:
            log_line("tick skipped for %s: could not write the counter %s; the count "
                     "was not recorded, so no snapshot decision is made from a number "
                     "nothing measured. Continuous autosave is OFF for this session "
                     "until the counter is writable" % (sid, ctr), cwd=repo)
            return
        if n >= RUNAWAY_AT and not os.path.exists(ctr + ".warned"):
            try:
                with open(ctr + ".warned", "w"):
                    pass
                warn = 1
            except OSError:
                pass  # sbe: allow-silent the marker exists only to stop the runaway
                # warning repeating; unwritten, the warning fires again next
                # tick, which is noisier than intended and never quieter
    finally:
        if got_lock:
            try:
                os.rmdir(lock)
            except OSError:
                pass  # sbe: allow-silent a lock left behind is exactly the stale
                # lock the wait above detects and breaks by its own mtime rule,
                # so the next tick recovers without anyone being told twice
    if n % TICK_EVERY == 0:
        snapshot(repo, "tick %d" % n)
    if warn:
        say(json.dumps({"systemMessage":
            "BrotherSBE: this session has made %d tool calls, which can signal an "
            "unbounded loop. Consider whether a circuit breaker (section 7) should "
            "fire. Your work is autosaved under %s/ (python3 tools/sbe_autosave.py recover prints "
            "the path)." % (n, AUTOSAVE_NS)}))


def _cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass  # sbe: allow-silent best-effort removal is this function's whole
        # contract; every caller passes a stamp file whose absence is already
        # the state it wants, and no caller reads a result


def recover(repo):
    """Get the saved work back WITHOUT touching the live working tree: the
    snapshot is checked out into a NEW detached worktree at a temporary
    path; you copy back what you want, by hand."""
    if not os.path.isdir(repo):
        say("sbe_autosave: cannot enter %s" % repo)
        return
    rc, out = _git(["rev-parse", "--show-toplevel"], cwd=repo)
    top = out.strip() if rc == 0 else repo
    ref = _autosave_ref(top if top else repo)
    rc, out = _git(["rev-parse", "-q", "--verify", ref], cwd=repo)
    sha = out.strip() if rc == 0 else ""
    if not sha:
        legacy = _legacy_autosave_ref(top if top else repo)
        rc, out = _git(["rev-parse", "-q", "--verify", legacy], cwd=repo)
        if rc == 0 and out.strip():
            sha, ref = out.strip(), legacy
    if not sha:
        rc, out = _git(["rev-parse", "-q", "--verify", AUTOSAVE_NS], cwd=repo)
        if rc == 0 and out.strip():
            sha, ref = out.strip(), AUTOSAVE_NS
    if not sha:
        rc, out = _git(["for-each-ref", "--format=%(refname) -> %(objectname)",
                        AUTOSAVE_NS], cwd=repo)
        others = out.strip() if rc == 0 else ""
        if others:
            say("sbe_autosave: no autosave under this worktree's current id (ref %s "
                "is empty)," % ref)
            print("  but this repository DOES hold autosave snapshot(s) under other "
                  "id(s), which is")
            print("  what a moved or renamed worktree looks like (the id derives from "
                  "the path):")
            for line in others.splitlines():
                say("    %s" % line)
            print("  Inspect one:  git log --oneline -1 <ref>")
            print("  Recover one:  git worktree add --detach <new-empty-dir> <ref>")
            return
        say("sbe_autosave: no autosave found in %s (ref %s is empty, and nothing "
            "else exists under %s/)." % (repo, ref, AUTOSAVE_NS))
        return
    tmpdir = tempfile.mkdtemp()  # created 0700 (owner-only) by mkdtemp
    try:
        os.chmod(tmpdir, 0o700)
    except OSError:
        pass  # sbe: allow-silent mkdtemp already created this directory owner-only,
        # so this call is belt and braces; a refusal leaves the mode mkdtemp
        # set, never a wider one
    rc, _ = _git(["worktree", "add", "--detach", tmpdir, sha], cwd=repo)
    if rc != 0:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass  # sbe: allow-silent the worktree add already failed and is reported
            # below; a leftover empty directory in the system temp area is the
            # cheaper of the two outcomes and the OS reclaims it
        say("sbe_autosave: could not create a recovery worktree for %s." % sha)
        return
    try:
        perms = stat_mod.filemode(os.stat(tmpdir).st_mode)
    except OSError:
        perms = "?"
    # Native CPython returns a native path from mkdtemp on every platform, so
    # the MSYS-spelling translation the sh version needed (cygpath) is not
    # needed here; normpath keeps the separators native.
    say("sbe_autosave: recovered snapshot %s into a NEW worktree at:" % sha)
    say("  %s" % os.path.normpath(tmpdir))
    say("  permissions: %s (owner-only intended; this line reports what the platform "
        "gave, it does not promise enforcement on platforms that ignore POSIX modes)"
        % perms)
    say("  Your live working tree at %s was never touched. Inspect the folder" % repo)
    print("  above, copy back what you need, then remove it with:")
    say("  git -C %s worktree remove %s" % (repo, tmpdir))
    say("  Older superseded snapshots, if any, are listed by: git -C %s reflog %s"
        % (repo, ref))
    if os.path.isfile(EXCL_LOG):
        print("  What the snapshot does NOT hold: every file the content scan rejected "
              "is named,")
        say("  with its reason, in %s. Those files were never copied anywhere; they "
            "are" % EXCL_LOG)
        print("  still in your working tree, and any unsaved edit to one of them is "
              "only there.")
    else:
        say("  No exclusion record exists at %s, so no snapshot in this vault has"
            % EXCL_LOG)
        print("  rejected a file yet, or the record was removed.")


def _load_sbe_repo_scope():
    """Load sbe_repo_scope.py by path, works from any cwd and never
    raises. E76 per-repository hook scoping."""
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "sbe_repo_scope_for_autosave", os.path.join(here, "sbe_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def _read_hook_payload():
    """The hook's JSON stdin payload, parsed once, {} on any failure. Split
    out of hook_cwd() (E76) so a caller that also needs session_id, or the
    repo-scope gate, can read stdin exactly once rather than racing
    hook_cwd()'s own read."""
    try:
        raw = sys.stdin.read()
    except OSError:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def hook_cwd(payload=None):
    """The cwd the hook was invoked for, from its JSON stdin payload (read
    now if not already handed in); falls back to the process cwd."""
    if payload is None:
        payload = _read_hook_payload()
    cwd = payload.get("cwd", "") if isinstance(payload, dict) else ""
    return cwd or os.getcwd()


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "precompact":
        payload = _read_hook_payload()
        _rs = _load_sbe_repo_scope()
        if _rs is not None and _rs.hooks_off(payload=payload):
            return
        snapshot(hook_cwd(payload), "precompact")
    elif mode == "tick":
        tick(hook_cwd(), argv[2] if len(argv) > 2 else "session")
    elif mode == "recover":
        recover(argv[2] if len(argv) > 2 else os.getcwd())
    else:
        print("usage: sbe_autosave.py {precompact|tick <session_id>|recover [repo]}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:  # never blocks work; the reason is logged, not eaten
        log_line("autosave internal error, nothing was saved: %r" % e)
    sys.exit(0)
