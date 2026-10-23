#!/usr/bin/env python3
"""LC1.a: one claim store. A builder a session dispatches writes a runner shaped folder under the pool's runs
directory, so alive_runners() counts it and busy_paths() marks that sub unit's spec files busy, and the pool never
starts the same sub unit twice. A claim whose pid is dead is reconciled like a dead runner; a claim record that
cannot be read holds the sub unit and never frees it."""
import fcntl
import glob
import hashlib
import hmac
import json
import os
import re
import sys
import time

DEFAULT_RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
CLAIM_FILE = "CLAIM"

_FOLDER_RE = re.compile(r"^[^/]+-[0-9]{6}$")
_FIELDS = ("state", "sub", "builder", "pid", "branch", "at", "deadline", "deadline_seal")
_DEADLINE_SEAL_KEY = "i22.a.deadline.v1"
_MK_TRIES = 60
_OLD_S = 6 * 3600


def _text(value, name):
    """The value when it is a non-empty string, else ValueError. A bool, a number, bytes, None and a container are
    all refused here, before any path is built and before any file is opened."""
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty string, not %r" % (name, value))
    return value


def _sub_name(value):
    """A sub unit id that can be one folder name: a non-empty string with no path separator, never '.' or '..'."""
    _text(value, "sub")
    if os.sep in value or (os.altsep is not None and os.altsep in value) or value in (".", "..") or "\x00" in value:
        raise ValueError("sub must not name a path, not %r" % (value,))
    return value


def _whole_pid(value):
    """A positive integer pid: a bool is refused before an int, so a hostile flag never reads as a pid."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("pid must be a positive integer, not %r" % (value,))
    return value


def _finite(value, name):
    """The number as a float, else ValueError: a bool, a string, NaN and both infinities are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a finite number, not %r" % (name, value))
    if value != value or value == float("inf") or value == float("-inf"):
        raise ValueError("%s must be a finite number, not %r" % (name, value))
    return float(value)


def _pid_alive(pid):
    """A pid the kernel refuses to signal (EPERM) reads as alive, since alive and unknown read the same; every other
    OSError reads as dead. The pid is already a positive integer, from _whole_pid or from a parsed PID file."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return isinstance(exc, PermissionError)
    return True


def _read_claim(path):
    """(record, "") for the claim record at path, else (None, why). Every unreadable case answers a reason, so a
    record that cannot be read HOLDS and never frees."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "it cannot be read: %s" % exc
    if not raw:
        return None, "the file is empty"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "it is not valid utf-8: %s" % exc
    try:
        record = json.loads(text)
    except ValueError as exc:
        return None, "it is truncated or not JSON: %s" % exc
    if not isinstance(record, dict):
        return None, "the JSON is not an object"
    for field in _FIELDS:
        if field not in record:
            return None, "the field %r is missing" % (field,)
    if record["state"] != "CLAIMED":
        return None, "the state is %r, not CLAIMED" % (record["state"],)
    if not isinstance(record["sub"], str) or not record["sub"]:
        return None, "the sub unit is not a non-empty string"
    if not isinstance(record["builder"], str) or not record["builder"]:
        return None, "the builder is not a non-empty string"
    if isinstance(record["pid"], bool) or not isinstance(record["pid"], int) or record["pid"] <= 0:
        return None, "the pid is not a positive integer"
    if not isinstance(record["branch"], str) or not record["branch"]:
        return None, "the branch is not a non-empty string"
    at = record["at"]
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at != at or at == float("inf") or at == float("-inf"):
        return None, "the claim time is not a finite number"
    return record, ""


def _pid_file_pid(path):
    """(True, pid) for a PID file that names a positive integer, else (False, 0). An unreadable or half written file
    is not a pid, so it never counts as the record's own."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return False, 0
    try:
        pid = int(raw.decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError):
        return False, 0
    if pid <= 0:
        return False, 0
    return True, pid


def _pid_agrees(path, pid):
    """(True, "") when the PID file at path names exactly this pid, else (False, why)."""
    found, got = _pid_file_pid(path)
    if not found:
        return False, "the PID file is missing, empty or not a positive integer"
    if got != pid:
        return False, "the PID file names %d, the record names %d" % (got, pid)
    return True, ""


def _write_text(path, text):
    """Write text to path through a sibling temporary file and os.replace, so no reader sees a torn record."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _clear(folder):
    """Remove every file this module writes in folder and then the folder, ignoring anything already gone."""
    for name in (CLAIM_FILE, "PID", CLAIM_FILE + ".tmp", "PID.tmp"):
        try:
            os.unlink(os.path.join(folder, name))
        except OSError:  # sbe: allow-silent a file already gone is the state this cleanup wants
            pass
    try:
        os.rmdir(folder)
    except OSError:  # sbe: allow-silent a folder already gone or not empty is left for the next sweep; cleanup never raises
        pass


def _folders(runs_dir, sub):
    """Every <sub>-HHMMSS folder under runs_dir, sorted, whatever it holds."""
    return sorted(glob.glob(os.path.join(runs_dir, glob.escape(sub) + "-[0-9][0-9][0-9][0-9][0-9][0-9]")))


def _live_runner(runs_dir, sub):
    """A reason when an open folder of this sub unit already holds a live pid, else "". An unreadable PID file is
    not a live runner, and the record's own state decides the rest."""
    for folder in _folders(runs_dir, sub):
        if not os.path.isdir(folder) or os.path.exists(os.path.join(folder, "STATUS")):
            continue
        found, pid = _pid_file_pid(os.path.join(folder, "PID"))
        if found and _pid_alive(pid):
            return "a runner is alive in %s (pid %d)" % (folder, pid)
    return ""


def _make_folder(runs_dir, sub, at):
    """The path of a fresh <sub>-HHMMSS folder, made with os.mkdir: at most 60 tries, each one second after the
    last, so a taken second moves to the next."""
    for step in range(_MK_TRIES):
        when = time.localtime(at + step)
        folder = os.path.join(runs_dir, "%s-%02d%02d%02d" % (sub, when.tm_hour, when.tm_min, when.tm_sec))
        try:
            os.mkdir(folder)
        except FileExistsError:  # sbe: allow-silent a folder that exists means this second is taken; the next second is tried, and the bound ends in a refusal
            continue
        except OSError as exc:
            raise ValueError("the claim folder %r cannot be made: %s" % (folder, exc))
        return folder
    raise ValueError("no free claim folder name for %s after %d tries" % (sub, _MK_TRIES))


def _unreadable(folder, why):
    """The one NO-DATA line an unreadable claim record answers, with its remedy."""
    return ("NO-DATA claim record in %s is unreadable (%s); remedy: python3 scripts/loop/builder_claim.py release %s"
            % (folder, why, folder))


def _box_minutes(value):
    """75.0 when value is None, else the finite number greater than zero as a float. A bool, a string, a list, NaN,
    either infinity, zero and a negative value are refused here, before any deadline is written."""
    if value is None:
        return 75.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("box_minutes must be a finite number greater than 0, not %r" % (value,))
    if value != value or value == float("inf") or value == float("-inf") or value <= 0:
        raise ValueError("box_minutes must be a finite number greater than 0, not %r" % (value,))
    return float(value)


def _seal_deadline(record):
    """The hexadecimal sha256 digest over the canonical JSON of this record's sub, pid, at and deadline plus the
    module key. A record with any of those fields missing and a deadline that is not finite is a ValueError, so a
    record this module would not write is never sealed."""
    if not isinstance(record, dict):
        raise ValueError("a deadline seal needs a record object")
    payload = {}
    for field in ("sub", "pid", "at", "deadline"):
        if field not in record:
            raise ValueError("the field %r is missing, so no deadline seal can be written" % (field,))
        payload[field] = record[field]
    deadline = payload["deadline"]
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise ValueError("the deadline must be a finite number, not %r" % (deadline,))
    if deadline != deadline or deadline == float("inf") or deadline == float("-inf"):
        raise ValueError("the deadline must be a finite number, not %r" % (deadline,))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((_DEADLINE_SEAL_KEY + canonical).encode("utf-8")).hexdigest()


def _verify_seal(record):
    """True only when the record carries the seal this module would write for it. A missing, non-string, unencodable
    or altered seal is False and never an exception, so a rewritten deadline reads as unsealed, never as open."""
    if not isinstance(record, dict):
        return False
    seal = record.get("deadline_seal")
    if not isinstance(seal, str) or not seal:
        return False
    try:
        expected = _seal_deadline(record)
    except ValueError:
        return False
    try:
        given = seal.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(given, expected.encode("ascii"))


def _safe_run_dir(run_dir):
    """The normalized absolute run folder when it is a non-empty string strictly under DEFAULT_RUNS, else
    ValueError("run_dir is outside the pool"). Nothing is read from a folder this refuses."""
    if isinstance(run_dir, bool) or not isinstance(run_dir, str) or not run_dir:
        raise ValueError("run_dir is outside the pool")
    real_dir = os.path.realpath(run_dir)
    real_pool = os.path.realpath(DEFAULT_RUNS)
    if not real_dir.startswith(real_pool + os.sep):
        raise ValueError("run_dir is outside the pool")
    return real_dir


def claim(sub, builder, pid, branch, runs_dir=DEFAULT_RUNS, now=None, box_minutes=None):
    """Write a runner shaped claim for `sub` under runs_dir and answer the folder path.

    The folder is <sub>-HHMMSS, holds CLAIM (this record) and PID (the builder's pid and a newline), and carries no
    STATUS: alive_runners() counts exactly that shape, so busy_paths() marks the sub unit's spec files busy with no
    change to either function. The lock <runs_dir>/<sub>.lock is taken exclusively and non blockingly for the whole
    call, the same lock a runner holds for its life, so two starters of one sub unit refuse. Every value is
    validated before any path is built; a write failure removes what this call wrote and refuses."""
    _sub_name(sub)
    _text(builder, "builder")
    _whole_pid(pid)
    _text(branch, "branch")
    _text(runs_dir, "runs_dir")
    at = time.time() if now is None else _finite(now, "now")
    box = _box_minutes(box_minutes)
    try:
        os.makedirs(runs_dir, exist_ok=True)
    except OSError as exc:
        raise ValueError("the runs directory %r cannot be made: %s" % (runs_dir, exc))
    lock_path = os.path.join(runs_dir, sub + ".lock")
    try:
        handle = open(lock_path, "a")
    except OSError as exc:
        raise ValueError("the lock %r cannot be opened: %s" % (lock_path, exc))
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("the lock %r is held by a live runner or another claim: %s" % (lock_path, exc))
        blocked = claim_hold(runs_dir, sub)
        if blocked:
            raise ValueError(blocked)
        live = _live_runner(runs_dir, sub)
        if live:
            raise ValueError(live)
        folder = _make_folder(runs_dir, sub, at)
        record = {"state": "CLAIMED", "sub": sub, "builder": builder, "pid": pid, "branch": branch, "at": at,
                  "deadline": at + box * 60.0}
        record["deadline_seal"] = _seal_deadline(record)
        try:
            _write_text(os.path.join(folder, CLAIM_FILE), json.dumps(record))
            _write_text(os.path.join(folder, "PID"), "%d\n" % pid)
        except OSError as exc:
            _clear(folder)
            raise ValueError("the claim in %r could not be written: %s" % (folder, exc))
        return folder
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:  # sbe: allow-silent the lock dies with the handle closed on the next line; an unlock error changes nothing
            pass
        handle.close()


def claim_deadline(run_dir):
    """The deadline a claim folder records, as a float of seconds since the epoch.

    A run folder outside the pool, a missing or unreadable CLAIM, a record with no finite deadline and a record whose
    seal does not match all refuse with ValueError. A missing or rewritten deadline never reads as no box, and the 75
    minute default is never substituted here."""
    real_dir = _safe_run_dir(run_dir)
    record, why = _read_claim(os.path.join(real_dir, CLAIM_FILE))
    if record is None or why:
        raise ValueError(why or "claim record unreadable")
    deadline = record.get("deadline")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise ValueError("claim record has no finite deadline")
    if deadline != deadline or deadline == float("inf") or deadline == float("-inf"):
        raise ValueError("claim record has no finite deadline")
    if "deadline_seal" not in record or not _verify_seal(record):
        raise ValueError("claim record has no valid deadline seal")
    return float(deadline)


def release(run_dir, runs_dir=DEFAULT_RUNS):
    """Remove a claim folder this module made and answer True, or False when it is already gone.

    Only a folder whose parent is runs_dir (compared after os.path.realpath, so a symlink out is refused), whose
    name is <sub> plus six digits, that holds CLAIM and whose entries are a subset of CLAIM, PID and their .tmp
    files is removed. Anything else raises ValueError: a runner's own folder carries RUN-TAG and is never removed
    here."""
    _text(run_dir, "run_dir")
    _text(runs_dir, "runs_dir")
    real_runs = os.path.realpath(runs_dir)
    real_dir = os.path.realpath(run_dir)
    if os.path.dirname(real_dir) != real_runs:
        raise ValueError("%r is not directly under the runs directory %r" % (run_dir, runs_dir))
    name = os.path.basename(real_dir)
    if not _FOLDER_RE.match(name):
        raise ValueError("the folder name %r is not <sub>-HHMMSS" % (name,))
    if not os.path.exists(real_dir):
        return False
    if not os.path.isdir(real_dir):
        raise ValueError("%r is not a folder" % (real_dir,))
    entries = sorted(os.listdir(real_dir))
    if CLAIM_FILE not in entries:
        raise ValueError("%r holds no %s" % (real_dir, CLAIM_FILE))
    allowed = (CLAIM_FILE, "PID", CLAIM_FILE + ".tmp", "PID.tmp")
    for entry in entries:
        if entry not in allowed:
            raise ValueError("%r holds %r, which this module never writes" % (real_dir, entry))
    for entry in entries:
        try:
            os.unlink(os.path.join(real_dir, entry))
        except OSError as exc:
            raise ValueError("%r cannot be removed: %s" % (entry, exc))
    try:
        os.rmdir(real_dir)
    except OSError as exc:
        raise ValueError("%r cannot be removed: %s" % (real_dir, exc))
    return True


def claim_hold(runs_dir, sub, pid_alive=None):
    """"" or the reason this sub unit is held by a builder claim.

    Every folder named <sub>-HHMMSS under runs_dir with no STATUS that holds a CLAIM is read. A readable record
    whose state is CLAIMED, whose sub is this sub unit, whose PID file agrees with its pid and whose pid is alive
    answers 'claimed by builder <builder> (pid <n>, branch <branch>)'; a dead pid answers "" (the pool's
    reconcile_dead writes the STALLED word on the same pass, exactly a dead runner); a record that cannot be read
    answers a NO-DATA line and HOLDS. A claim older than six hours with a live pid still holds, and the reason says
    so."""
    _text(runs_dir, "runs_dir")
    _sub_name(sub)
    if pid_alive is not None and not callable(pid_alive):
        raise ValueError("pid_alive must be callable or None, not %r" % (pid_alive,))
    alive = _pid_alive if pid_alive is None else pid_alive
    for folder in _folders(runs_dir, sub):
        if not os.path.isdir(folder) or os.path.exists(os.path.join(folder, "STATUS")):
            continue
        path = os.path.join(folder, CLAIM_FILE)
        if not os.path.isfile(path):
            continue
        record, why = _read_claim(path)
        if record is None:
            return _unreadable(folder, why)
        if record["sub"] != sub:
            return _unreadable(folder, "it names the sub unit %r, not %r" % (record["sub"], sub))
        ok, why = _pid_agrees(os.path.join(folder, "PID"), record["pid"])
        if not ok:
            return _unreadable(folder, why)
        if not alive(record["pid"]):
            continue
        text = "claimed by builder %s (pid %d, branch %s)" % (record["builder"], record["pid"], record["branch"])
        if time.time() - record["at"] > _OLD_S:
            text += " older than 6 hours"
        return text
    return ""


class TimeboxError(RuntimeError):
    """A git action the timebox enforcement needs cannot be done: git cannot start, it times out, or the
    commit fails for a reason other than nothing to commit. A subclass of RuntimeError, so a caller that
    catches RuntimeError still refuses the box."""
    pass


def _bounded(text, limit=240):
    """The text as a string, cut to at most limit characters with an ellipsis tail when longer."""
    value = str(text)
    return value if len(value) <= limit else value[:limit] + "..."


def _git(args, cwd):
    """Run one git command in cwd and answer (returncode, combined stdout and stderr).

    The safety screen for this build refuses the subprocess import in every file this unit writes and shows
    no command runner for the unit, so this module cannot spawn git; every call refuses with TimeboxError,
    and the dispatcher folds the refusal into the BLOCKED marker instead of crashing. When a command runner
    seat is granted, this function is the single place a subprocess.run would be added, with a literal-list
    argv, shell left unset, and the only computed path going through cwd=. A hostile args or cwd is refused
    with ValueError before anything else."""
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise ValueError("args must be a list of strings, not %r" % (args,))
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd must be a non-empty string, not %r" % (cwd,))
    raise TimeboxError("git cannot be started: subprocess is not available in this build")


def box_verdict(run_dir, now=None):
    """OPEN when the claim's sealed deadline has not passed, BLOCK when it has.

    deadline comes from claim_deadline(run_dir), whose own ValueError for a missing, torn or rewritten
    record is this function's refusal too, so a broken claim never reads as no box. now defaults to
    time.time() and a non-finite now is refused with ValueError. This function writes nothing."""
    if now is None:
        resolved = time.time()
    else:
        resolved = _finite(now, "now")
    deadline = claim_deadline(run_dir)
    return "OPEN" if resolved <= deadline else "BLOCK"


def preserve_wip(tree_dir, message):
    """Keep the builder's diff as a WIP commit and answer "WIP", or "CLEAN" when there is nothing to commit.

    git add -A runs first; a nonzero return code is a TimeboxError. The commit is called with --no-verify
    so no hook runs. When the commit returns 0 this returns "WIP"; when the commit output carries the phrase
    "nothing to commit" it returns "CLEAN"; any other commit failure raises TimeboxError naming the return
    code and a bounded tail of the output. The claim store is never touched. This build cannot start git at
    all, so _git answers TimeboxError and that is this function's refusal."""
    if not isinstance(tree_dir, str) or not tree_dir:
        raise ValueError("tree_dir must be a non-empty string, not %r" % (tree_dir,))
    if not isinstance(message, str) or not message:
        raise ValueError("message must be a non-empty string, not %r" % (message,))
    rc, out = _git(["add", "-A"], tree_dir)
    if rc != 0:
        raise TimeboxError("git add -A in %s failed (rc %d): %s" % (tree_dir, rc, _bounded(out)))
    rc, out = _git(["commit", "--no-verify", "-m", message], tree_dir)
    if rc == 0:
        return "WIP"
    if "nothing to commit" in out:
        return "CLEAN"
    raise TimeboxError("git commit in %s failed (rc %d): %s" % (tree_dir, rc, _bounded(out)))


def _read_claim_state(run_dir, allow_blocked=True):
    """(record, None) for a claim record whose state is CLAIMED, or BLOCKED when allow_blocked is true,
    else (None, why).

    The claim file is read as bytes. An unreadable, empty, non-utf8, torn or non-object record answers
    (None, why) and never raises. The state must be one of CLAIMED and BLOCKED, or CLAIMED only when
    allow_blocked is False. run_dir must be a non-empty string strictly under DEFAULT_RUNS; a hostile
    run_dir or a non-bool allow_blocked is refused with ValueError. claim_hold is not changed."""
    if allow_blocked is not True and allow_blocked is not False:
        raise ValueError("allow_blocked must be a bool, not %r" % (allow_blocked,))
    real_dir = _safe_run_dir(run_dir)
    path = os.path.join(real_dir, CLAIM_FILE)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "the claim cannot be read: %s" % (exc,)
    if not raw:
        return None, "the claim file is empty"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "the claim is not valid utf-8: %s" % (exc,)
    try:
        record = json.loads(text)
    except ValueError as exc:
        return None, "the claim is truncated or not JSON: %s" % (exc,)
    if not isinstance(record, dict):
        return None, "the claim JSON is not an object"
    allowed = ("CLAIMED", "BLOCKED") if allow_blocked else ("CLAIMED",)
    state = record.get("state")
    if state not in allowed:
        return None, "the state is %r, not one of %s" % (state, list(allowed))
    return record, None


def _write_marker(run_dir, when, reason):
    """Write the dispatcher-owned BLOCKED marker under run_dir through _write_text, so no reader sees a torn
    file."""
    _write_text(os.path.join(run_dir, "BLOCKED"),
                json.dumps({"state": "BLOCKED", "blocked_at": when, "reason": reason}, sort_keys=True))


def enforce_timebox(run_dir, tree_dir, now=None):
    """Record the expired box as BLOCKED in the dispatcher-owned claim state and keep the WIP.

    run_dir must be a non-empty string strictly under DEFAULT_RUNS; tree_dir must be a non-empty string; a
    non-finite now is refused with ValueError and now defaults to time.time(). The claim record is read
    through _read_claim_state. When it cannot be read, preserve_wip is still attempted (its TimeboxError is
    caught and folded into the reason) and only the separate BLOCKED marker is written. When the record
    already names state BLOCKED this call is a no-op that returns "BLOCKED". When box_verdict answers OPEN
    this call returns "OPEN" and writes nothing. When it answers BLOCK, preserve_wip runs, the claim record
    is rewritten with state BLOCKED and blocked_at now, and the marker is written; a TimeboxError from
    preserve_wip leaves the claim record alone and only writes the marker. The WIP commit is the only git
    action; nothing is pushed and no new claim file appears."""
    real_dir = _safe_run_dir(run_dir)
    if not isinstance(tree_dir, str) or not tree_dir:
        raise ValueError("tree_dir must be a non-empty string, not %r" % (tree_dir,))
    when = time.time() if now is None else _finite(now, "now")
    record, why = _read_claim_state(real_dir)
    if record is None:
        reason = "claim unreadable: %s" % (why or "no reason given",)
        try:
            preserve_wip(tree_dir, "WIP: I22 timebox expired")
        except TimeboxError as exc:
            reason = "%s; WIP commit failed: %s" % (reason, _bounded(exc))
        _write_marker(real_dir, when, reason)
        return "BLOCKED"
    if record.get("state") == "BLOCKED":
        return "BLOCKED"
    verdict = box_verdict(real_dir, when)
    if verdict == "OPEN":
        return "OPEN"
    try:
        preserve_wip(tree_dir, "WIP: I22 timebox expired")
    except TimeboxError as exc:
        _write_marker(real_dir, when, "WIP commit failed: %s" % (_bounded(exc),))
        return "BLOCKED"
    record["state"] = "BLOCKED"
    record["blocked_at"] = when
    _write_text(os.path.join(real_dir, CLAIM_FILE), json.dumps(record, sort_keys=True))
    _write_marker(real_dir, when, "timebox expired")
    return "BLOCKED"


def main(argv=None):
    """The command line the session uses: claim, release and check.

    claim <sub> <builder> <pid> <branch> prints the folder and exits 0; release <folder> prints 'released' or
    'already released' and exits 0; check <sub> prints the hold text and exits 0 when free and 1 when held. Any
    ValueError prints REFUSED: <reason> and exits 2. None reads sys.argv; anything else that is not a list of
    strings refuses with SystemExit(2) before the list is read or the first element is touched."""
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise SystemExit(2)
    runs = DEFAULT_RUNS
    try:
        if argv and argv[0] == "--enforce-timebox":
            if len(argv) != 3:
                raise ValueError("--enforce-timebox takes <run_dir> <tree_dir>")
            print(enforce_timebox(argv[1], argv[2]))
            return 0
        if argv and argv[0] == "claim":
            if len(argv) != 5:
                raise ValueError("claim takes <sub> <builder> <pid> <branch>")
            folder = claim(argv[1], argv[2], _pid_arg(argv[3]), argv[4], runs_dir=runs)
            print(folder)
            return 0
        if argv and argv[0] == "release":
            if len(argv) != 2:
                raise ValueError("release takes <folder>")
            print("released" if release(argv[1], runs_dir=runs) else "already released")
            return 0
        if argv and argv[0] == "check":
            if len(argv) != 2:
                raise ValueError("check takes <sub>")
            held = claim_hold(runs, argv[1])
            if held:
                print(held)
                return 1
            print("free")
            return 0
        raise ValueError("unknown command %r" % (argv[0] if argv else "",))
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 2


def _pid_arg(text):
    """The positive integer the command line names, else ValueError: '12x', '' and '1.5' are refused."""
    try:
        return _whole_pid(int(text))
    except ValueError:
        raise ValueError("pid must be a positive integer, not %r" % (text,))


if __name__ == "__main__":
    sys.exit(main())
