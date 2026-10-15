#!/usr/bin/env python3
"""LC1.a: one claim store. A builder a session dispatches writes a runner shaped folder under the pool's runs
directory, so alive_runners() counts it and busy_paths() marks that sub unit's spec files busy, and the pool never
starts the same sub unit twice. A claim whose pid is dead is reconciled like a dead runner; a claim record that
cannot be read holds the sub unit and never frees it."""
import fcntl
import glob
import json
import os
import re
import sys
import time

DEFAULT_RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
CLAIM_FILE = "CLAIM"

_FOLDER_RE = re.compile(r"^[^/]+-[0-9]{6}$")
_FIELDS = ("state", "sub", "builder", "pid", "branch", "at")
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


def claim(sub, builder, pid, branch, runs_dir=DEFAULT_RUNS, now=None):
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
        record = {"state": "CLAIMED", "sub": sub, "builder": builder, "pid": pid, "branch": branch, "at": at}
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
