#!/usr/bin/env python3
"""The run ledger writer (FX-06.1), under scripts/loop/ beside the driver that calls it.

One word of run state, one receipt per stage attempt and an append only event
list, all inside <run dir>/ledger/. This module is the only writer of that
directory. It is NOT scripts/loop/proof_ledger.py's run_ledger_paths, which
globs the per run OpenRouter money ledgers; the two are unrelated and neither
imports the other.

Exit codes: 0 done or OFF, 1 refused by a rule, 2 corrupt or unknown input,
3 NO-DATA and 4 a write that could not happen. A value the writer cannot answer
is recorded as "NO-DATA: <why>", never as a zero and never as a missing key.
"""
import argparse
import datetime
import errno
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from typing import List, Optional

#: Seconds the flock poll waits for the ledger lock before the verb exits 4.
LOCK_WAIT_S = 10
SCHEMA = "run-ledger-stage-v1"
STAGE_NAMES = {
    "00": "intake",
    "01": "lint",
    "02": "parity",
    "03": "run",
    "04": "change",
    "05": "end",
    "06": "finisher",
    "07": "final-check",
    "08": "report",
}
REQUIRED_STAGES = ("03", "05", "06", "07", "08")
PREDECESSOR = {"05": "03", "06": "05", "07": "06", "08": "07"}
END_STATES = ("DONE", "FAILED", "SKIPPED")
STATE_VOCAB = frozenset((
    "RUNNING", "ENDED", "FAILED", "ABANDONED", "CLOSED",
    "INTAKE", "LINTED", "PARITY", "PAUSED", "CHANGING",
    "FINISHED", "CHECKED", "REPORTED",
))
DETAIL_LIMIT = 2000
_RECEIPT_KEYS = (
    "schema", "run_id", "stage", "name", "attempt", "state", "started_at",
    "ended_at", "pid", "pid_start", "command", "tool", "argv", "exit_code",
    "log_path", "log_sha256", "points_at", "reason", "deadline_at", "mode",
    "next",
)
_RECEIPT_DEFAULTS = {
    "started_at": "NO-DATA: no begin was recorded",
    "ended_at": "NO-DATA: not ended",
    "pid": "NO-DATA: not supplied by the caller",
    "pid_start": "NO-DATA: not supplied by the caller",
    "command": "NO-DATA: not supplied by the caller",
    "tool": "NO-DATA: not supplied by the caller",
    "argv": "NO-DATA: not supplied by the caller",
    "exit_code": "NO-DATA: not ended",
    "log_path": "NO-DATA: not supplied by the caller",
    "log_sha256": "NO-DATA: no log supplied",
    "points_at": "NO-DATA: not supplied by the caller",
    "reason": "NO-DATA: not supplied by the caller",
    "deadline_at": "NO-DATA: no deadline",
    "mode": "NO-DATA: no mode",
    "next": "NO-DATA: not supplied by the caller",
}
_NOTE_EMITTED = False


class _LockHeld(Exception):
    """Private: the ledger lock could not be taken inside the bound."""


def _need_str(value, name):
    if not isinstance(value, str):
        raise ValueError("%s must be a str" % name)
    return value


def _need_opt_str(value, name):
    if value is not None and not isinstance(value, str):
        raise ValueError("%s must be a str or None" % name)
    return value


def _need_int(value, name, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError("%s must be an int" % name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an int" % name)
    return value


def _need_argv(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError("argv must be None or a list or a tuple of str")
    for item in value:
        if not isinstance(item, str):
            raise ValueError("every item of argv must be a str")
    return list(value)


def _need_json_obj(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("json_obj must be None or a dict")
    for key in value:
        if not isinstance(key, str):
            raise ValueError("every key of json_obj must be a str")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("json_obj is not encodable (%s)" % exc)
    return value


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _epoch_iso(epoch):
    try:
        return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError) as exc:
        return "NO-DATA: the deadline epoch could not be read (%s)" % exc


def _collapse(text):
    return " ".join(text.split())


def _sha256_of(path):
    if not isinstance(path, str) or not os.path.isfile(path):
        return "NO-DATA: the log %s could not be read" % path
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        return "NO-DATA: the log %s could not be read (%s)" % (path, exc)
    return digest.hexdigest()


def _resolve_mode():
    raw = os.environ.get("BROTHER_RUNFLOW")
    if raw is None or raw.strip() == "":
        return "off", None
    value = raw.strip().lower()
    if value in ("off", "shadow", "on"):
        return value, None
    note = "NOTE BROTHER_RUNFLOW=%s is not off, shadow or on; read as off" % raw
    return "off", note


def mode():
    """The one place the switch is resolved (R-FX-06-1)."""
    word, _note = _resolve_mode()
    return word


def _note_once(note, stream):
    global _NOTE_EMITTED
    if note and not _NOTE_EMITTED:
        print(note, file=stream)
        _NOTE_EMITTED = True


def _off_guard():
    """True when nothing may be written; prints the OFF line once per verb."""
    word, note = _resolve_mode()
    if note:
        _note_once(note, sys.stderr)
    if word == "off":
        print("OFF: nothing recorded")
        return True
    return False


def _run_dir_problem(run_dir):
    _need_str(run_dir, "run_dir")
    if run_dir == "" or not os.path.isabs(run_dir):
        return "run_dir is empty or relative"
    if not os.path.isdir(run_dir):
        return "run_dir is not a directory"
    return None


def _ledger_dir(run_dir):
    return os.path.join(run_dir, "ledger")


def _lock_path(run_dir):
    return os.path.join(_ledger_dir(run_dir), ".lock")


def _state_path(run_dir):
    return os.path.join(_ledger_dir(run_dir), "STATE")


def _attempt_path(run_dir, stage, attempt):
    if attempt == 1:
        name = "%s-%s.json" % (stage, STAGE_NAMES[stage])
    else:
        name = "%s-%s.%d.json" % (stage, STAGE_NAMES[stage], attempt)
    return os.path.join(_ledger_dir(run_dir), name)


def _lock_wait(lock_path):
    handle = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    bound = time.time() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(handle)
                raise
            if time.time() >= bound:
                os.close(handle)
                raise _LockHeld("the ledger lock at %s is held" % lock_path)
            time.sleep(0.005)


def _atomic_write(path, data, lock_path):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle = _lock_wait(lock_path)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass  # sbe: allow-silent tmp is set only when the write above raised, and that exception is what propagates
        os.close(handle)


def _put(path, data, lock_path):
    try:
        _atomic_write(path, data, lock_path)
    except _LockHeld as exc:
        return "%s was not written: %s" % (path, exc)
    except OSError as exc:
        return "%s was not written (%s: %s)" % (path, type(exc).__name__, exc)
    return None


def _put_json(path, record, lock_path):
    try:
        data = json.dumps(record, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        return "%s could not be encoded (%s)" % (path, exc)
    return _put(path, data, lock_path)


def _append_event(run_dir, record, lock_path):
    directory = _ledger_dir(run_dir)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        return "%s could not be created (%s: %s)" % (directory, type(exc).__name__, exc)
    path = os.path.join(directory, "events.jsonl")
    try:
        line = json.dumps(record, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        return "%s could not be encoded (%s)" % (path, exc)
    try:
        handle = _lock_wait(lock_path)
    except _LockHeld as exc:
        return "%s was not written: %s" % (path, exc)
    except OSError as exc:
        return "%s was not written (%s: %s)" % (path, type(exc).__name__, exc)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        return "%s was not written (%s: %s)" % (path, type(exc).__name__, exc)
    finally:
        os.close(handle)
    return None


def _read_json(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "%s is not readable JSON (%s: %s)" % (path, type(exc).__name__, exc)
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError) as exc:
        return None, "%s is not readable JSON (%s)" % (path, exc)


def _newest_attempt(run_dir, stage):
    directory = _ledger_dir(run_dir)
    if not os.path.isdir(directory):
        return 0, None, None, None
    first = "%s-%s.json" % (stage, STAGE_NAMES[stage])
    prefix = "%s-%s." % (stage, STAGE_NAMES[stage])
    best_attempt = 0
    best_path = None
    try:
        names = os.listdir(directory)
    except OSError as exc:
        return 0, None, None, "the ledger directory could not be listed (%s: %s)" % (type(exc).__name__, exc)
    for name in names:
        attempt = 0
        if name == first:
            attempt = 1
        elif name.startswith(prefix) and name.endswith(".json"):
            middle = name[len(prefix):-len(".json")]
            if middle.isdigit():
                attempt = int(middle)
        if attempt > best_attempt:
            best_attempt = attempt
            best_path = os.path.join(directory, name)
    if best_path is None:
        return 0, None, None, None
    record, problem = _read_json(best_path)
    if problem:
        return best_attempt, None, best_path, problem
    if not isinstance(record, dict):
        return best_attempt, None, best_path, "%s does not hold an object" % best_path
    return best_attempt, record, best_path, None


def _blank_receipt(run_dir, stage, attempt, state):
    record = {}
    for key in _RECEIPT_KEYS:
        record[key] = _RECEIPT_DEFAULTS.get(key, "NO-DATA: not supplied by the caller")
    record["schema"] = SCHEMA
    record["run_id"] = os.path.basename(os.path.abspath(run_dir))
    record["stage"] = stage
    record["name"] = STAGE_NAMES[stage]
    record["attempt"] = attempt
    record["state"] = state
    record["mode"] = mode()
    if stage in ("03", "05"):
        record["points_at"] = "receipt/receipt.json"
    return record


def _full_receipt(record):
    full = dict(record)
    for key in _RECEIPT_KEYS:
        if key not in full:
            full[key] = _RECEIPT_DEFAULTS.get(key, "NO-DATA: not supplied by the caller")
    if not isinstance(full.get("schema"), str):
        full["schema"] = SCHEMA
    if not isinstance(full.get("run_id"), str):
        full["run_id"] = "NO-DATA: no run id"
    if not isinstance(full.get("mode"), str):
        full["mode"] = "NO-DATA: no mode"
    if not isinstance(full.get("points_at"), str):
        full["points_at"] = "NO-DATA: not supplied by the caller"
    return full


def _pending_stage(run_dir):
    for stage in REQUIRED_STAGES:
        _attempt, newest, _path, problem = _newest_attempt(run_dir, stage)
        if problem:
            return "NO-DATA: %s" % problem
        if newest is None or newest.get("state") not in ("DONE", "SKIPPED"):
            return "%s-%s" % (stage, STAGE_NAMES[stage])
    return "NO-DATA: every required stage is DONE or SKIPPED"


def _put_last(run_dir, lock_path):
    root = os.environ.get("BROTHER_RUNS_ROOT")
    if not root:
        root = os.path.expanduser("~/.claude/evidence/loop-runs")
    return _put(os.path.join(root, "LAST"), (run_dir + "\n").encode("utf-8"), lock_path)


def begin(stage, run_dir, pid, deadline_epoch=None, tool=None, log=None,
          argv=None, pid_start=None, command=None):
    """Start one stage attempt (R-FX-06-2). pid_start and command are the
    caller's observation; this module never reads ps (contract 1 and 2)."""
    stage = _need_str(stage, "stage")
    run_dir = _need_str(run_dir, "run_dir")
    pid = _need_int(pid, "pid")
    _need_int(deadline_epoch, "deadline_epoch", True)
    _need_opt_str(tool, "tool")
    _need_opt_str(log, "log")
    _need_opt_str(pid_start, "pid_start")
    _need_opt_str(command, "command")
    argv = _need_argv(argv)
    problem = _run_dir_problem(run_dir)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    if stage not in STAGE_NAMES:
        print("REFUSED: unknown stage %s" % stage)
        return 2
    if _off_guard():
        return 0
    attempt, newest, _found, problem = _newest_attempt(run_dir, stage)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    if newest is not None:
        word = newest.get("state")
        if word == "STARTED":
            print("REFUSED: attempt %d of stage %s is STARTED" % (attempt, stage))
            return 1
        if word in ("DONE", "SKIPPED"):
            print("REFUSED: attempt %d of stage %s already reads %s" % (attempt, stage, word))
            return 1
        if word == "FAILED" and attempt >= 2:
            print("REFUSED: two attempts of stage %s already read FAILED" % stage)
            return 1
        if word not in END_STATES:
            print("REFUSED: attempt %d of stage %s holds state %r" % (attempt, stage, word))
            return 2
    predecessor = PREDECESSOR.get(stage)
    if predecessor is not None:
        _p_attempt, p_newest, _p_path, problem = _newest_attempt(run_dir, predecessor)
        if problem:
            print("REFUSED: %s" % problem)
            return 2
        if p_newest is None or p_newest.get("state") not in ("DONE", "SKIPPED"):
            print("REFUSED: predecessor %s of stage %s is not DONE or SKIPPED" % (predecessor, stage))
            return 1
    attempt = attempt + 1
    receipt = _blank_receipt(run_dir, stage, attempt, "STARTED")
    receipt["started_at"] = _now_iso()
    receipt["pid"] = pid
    if pid_start is not None:
        receipt["pid_start"] = pid_start
    if command is not None:
        receipt["command"] = command
    if tool is not None:
        receipt["tool"] = tool
    if argv is not None:
        receipt["argv"] = argv
    if log is not None:
        receipt["log_path"] = log
    if deadline_epoch is not None:
        receipt["deadline_at"] = _epoch_iso(deadline_epoch)
    lock_path = _lock_path(run_dir)
    path = _attempt_path(run_dir, stage, attempt)
    problem = _put_json(path, receipt, lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    record = {"at": _now_iso(), "kind": "begin", "stage": stage, "attempt": attempt}
    problem = _append_event(run_dir, record, lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    if stage == "03":
        problem = _put(_state_path(run_dir), b"RUNNING\n", lock_path)
        if problem:
            print("WRITE FAILED: %s" % problem, file=sys.stderr)
            return 4
        problem = _put_last(run_dir, lock_path)
        if problem:
            print("WRITE FAILED: %s" % problem, file=sys.stderr)
            return 4
    return 0


def end(stage, run_dir, state, exit_code=None, reason=None, started_at=None,
        log=None, points_at=None):
    """Record the end of a stage attempt, once (R-FX-06-3). Never refused for
    order: an end with no begin still writes attempt 1 complete."""
    stage = _need_str(stage, "stage")
    run_dir = _need_str(run_dir, "run_dir")
    state = _need_str(state, "state")
    _need_int(exit_code, "exit_code", True)
    _need_opt_str(reason, "reason")
    _need_opt_str(started_at, "started_at")
    _need_opt_str(log, "log")
    _need_opt_str(points_at, "points_at")
    problem = _run_dir_problem(run_dir)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    if stage not in STAGE_NAMES:
        print("REFUSED: unknown stage %s" % stage)
        return 2
    if state not in END_STATES:
        print("REFUSED: unknown state %s" % state)
        return 2
    if _off_guard():
        return 0
    attempt, newest, found_path, problem = _newest_attempt(run_dir, stage)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    lock_path = _lock_path(run_dir)
    if newest is None:
        attempt = 1
        receipt = _blank_receipt(run_dir, stage, attempt, state)
        receipt["started_at"] = started_at if started_at is not None else "NO-DATA: no begin was recorded"
        receipt["ended_at"] = _now_iso()
        receipt["exit_code"] = exit_code if exit_code is not None else "NO-DATA: not supplied by the caller"
        if reason is not None:
            receipt["reason"] = reason
        if log is not None:
            receipt["log_path"] = log
            receipt["log_sha256"] = _sha256_of(log)
        if points_at is not None:
            receipt["points_at"] = points_at
        path = _attempt_path(run_dir, stage, attempt)
    else:
        word = newest.get("state")
        if word in ("DONE", "SKIPPED", "FAILED"):
            print("already ended")
            return 1
        if word != "STARTED":
            print("REFUSED: attempt %d of stage %s holds state %r" % (attempt, stage, word))
            return 2
        receipt = _full_receipt(newest)
        receipt["state"] = state
        receipt["ended_at"] = _now_iso()
        if exit_code is not None:
            receipt["exit_code"] = exit_code
        if reason is not None:
            receipt["reason"] = reason
        if started_at is not None:
            receipt["started_at"] = started_at
        if log is not None:
            receipt["log_path"] = log
            receipt["log_sha256"] = _sha256_of(log)
        if points_at is not None:
            receipt["points_at"] = points_at
        path = found_path
    problem = _put_json(path, receipt, lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    record = {"at": _now_iso(), "kind": "end", "stage": stage, "attempt": attempt, "state": state}
    problem = _append_event(run_dir, record, lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    if state == "FAILED":
        problem = _put(_state_path(run_dir), b"FAILED\n", lock_path)
    elif stage == "05" and state == "DONE":
        problem = _put(_state_path(run_dir), b"ENDED\n", lock_path)
    else:
        problem = None
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    return 0


def event(run_dir, kind, detail=None, json_obj=None, deadline_epoch=None):
    """Append one event line under the ledger lock."""
    run_dir = _need_str(run_dir, "run_dir")
    kind = _need_str(kind, "kind")
    _need_opt_str(detail, "detail")
    _need_int(deadline_epoch, "deadline_epoch", True)
    fields = _need_json_obj(json_obj)
    problem = _run_dir_problem(run_dir)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    if _off_guard():
        return 0
    if detail is not None and len(detail) > DETAIL_LIMIT:
        detail = detail[:DETAIL_LIMIT] + " [cut %d chars]" % (len(detail) - DETAIL_LIMIT)
    record = {"at": _now_iso(), "kind": kind, "detail": detail}
    if fields:
        record.update(fields)
    if deadline_epoch is not None:
        record["deadline_epoch"] = deadline_epoch
    problem = _append_event(run_dir, record, _lock_path(run_dir))
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    return 0


def state(run_dir):
    """The STATE word, or a NO-DATA string. Never raises for a readable run
    directory and never returns a word outside the vocabulary."""
    _need_str(run_dir, "run_dir")
    problem = _run_dir_problem(run_dir)
    if problem:
        return "NO-DATA: %s" % problem
    path = _state_path(run_dir)
    if not os.path.isfile(path):
        return "NO-DATA: no STATE file"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "NO-DATA: STATE could not be read (%s: %s)" % (type(exc).__name__, exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return "NO-DATA: STATE is not utf-8 (%s)" % exc
    words = text.split()
    if not words:
        return "NO-DATA: STATE is empty"
    if len(words) != 1:
        return "NO-DATA: STATE is more than one word"
    if words[0] not in STATE_VOCAB:
        return "NO-DATA: STATE reads %s, which is not in the vocabulary" % words[0]
    return words[0]


def abandon(run_dir, words):
    """The owner's decision, recorded whatever the mode (R-FX-06-4)."""
    run_dir = _need_str(run_dir, "run_dir")
    words = _need_str(words, "words")
    if not words.strip():
        print("REFUSED: an abandon needs the owner's words")
        return 1
    problem = _run_dir_problem(run_dir)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    directory = _ledger_dir(run_dir)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        print("WRITE FAILED: %s could not be created (%s: %s)" % (directory, type(exc).__name__, exc),
              file=sys.stderr)
        return 4
    number = 1
    while number < 1000:
        name = "ABANDONED.json" if number == 1 else "ABANDONED.%d.json" % number
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            break
        number += 1
    else:
        print("WRITE FAILED: too many abandon records under %s" % directory, file=sys.stderr)
        return 4
    lock_path = os.path.join(directory, ".lock")
    record = {"words": words, "at": _now_iso(), "pending": _pending_stage(run_dir)}
    problem = _put_json(path, record, lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    marker = {"at": _now_iso(), "kind": "abandon", "detail": words}
    problem = _append_event(run_dir, marker, lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    problem = _put(_state_path(run_dir), b"ABANDONED\n", lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    return 0


def liveness(pid, pid_start, command, seen_start=None, seen_command=None):
    """Returns exactly one of LIVE, DEAD, NO-DATA (contract 1, R-FX-06-5)
    and never raises. It compares the recorded values with the caller's
    observation and never reads the process table itself."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "NO-DATA"
    if not isinstance(pid_start, str) or not pid_start or pid_start.startswith("NO-DATA"):
        return "NO-DATA"
    if not isinstance(command, str) or not command or command.startswith("NO-DATA"):
        return "NO-DATA"
    if seen_start == "" and seen_command == "":
        return "DEAD"
    if not isinstance(seen_start, str) or not seen_start:
        return "NO-DATA"
    if not isinstance(seen_command, str) or not seen_command:
        return "NO-DATA"
    if _collapse(pid_start) != _collapse(seen_start):
        return "DEAD"
    if command.strip() != seen_command.strip():
        return "DEAD"
    return "LIVE"


def _receipt_path(run_dir):
    return os.path.join(run_dir, "receipt", "receipt.json")


def _last_path():
    root = os.environ.get("BROTHER_RUNS_ROOT")
    if not root:
        root = os.path.expanduser("~/.claude/evidence/loop-runs")
    return os.path.join(root, "LAST")


def _read_receipt(run_dir):
    """Return (record, problem). problem is None when the receipt reads,
    "missing" when there is no receipt file, otherwise a message naming
    the file that could not be read as JSON."""
    path = _receipt_path(run_dir)
    if not os.path.isfile(path):
        return None, "missing"
    record, problem = _read_json(path)
    if problem:
        return None, problem
    if not isinstance(record, dict):
        return None, "%s does not hold an object" % path
    return record, None


def _resolve_target(run_dir, use_last):
    if run_dir is not None:
        problem = _run_dir_problem(run_dir)
        if problem:
            return None, "REFUSED: %s" % problem
        return run_dir, None
    if use_last:
        path = _last_path()
        if not os.path.isfile(path):
            return None, "NO-DATA: LAST %s is missing" % path
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            return None, "NO-DATA: LAST %s could not be read (%s)" % (path, exc)
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            return None, "NO-DATA: LAST %s is not utf-8 (%s)" % (path, exc)
        if text == "":
            return None, "NO-DATA: LAST %s is empty" % path
        if not os.path.isdir(text):
            return None, "NO-DATA: LAST %s names a directory that does not exist" % text
        return text, None
    return None, "REFUSED: --run-dir or --last is required"


def _format_lstart(epoch):
    try:
        moment = datetime.datetime.fromtimestamp(epoch)
    except (OSError, OverflowError, ValueError, TypeError):
        return None
    return "%s %s %2d %s %d" % (
        moment.strftime("%a"), moment.strftime("%b"), moment.day,
        moment.strftime("%H:%M:%S"), moment.year)


def _ps_read(pid):
    """Read one process without running ps. Returns ("gone", None, None)
    when the pid is gone, ("ok", start, command) when it reads and
    ("unknown", None, None) for any other outcome."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "unknown", None, None
    base = "/proc/%d" % pid
    if not os.path.isdir(base):
        return "gone", None, None
    try:
        with open(os.path.join(base, "cmdline"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return "unknown", None, None
    if not raw:
        try:
            with open(os.path.join(base, "comm"), "rb") as fh:
                raw = fh.read()
        except OSError:
            return "unknown", None, None
    command = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    try:
        with open(os.path.join(base, "stat"), "rb") as fh:
            stat_text = fh.read().decode("utf-8", "replace")
    except OSError:
        return "unknown", None, None
    rparen = stat_text.rfind(")")
    if rparen < 0:
        return "unknown", None, None
    fields = stat_text[rparen + 2:].split()
    if len(fields) < 20:
        return "unknown", None, None
    try:
        start_ticks = int(fields[19])
    except ValueError:
        return "unknown", None, None
    try:
        with open("/proc/stat", "rb") as fh:
            proc_text = fh.read().decode("utf-8", "replace")
    except OSError:
        return "unknown", None, None
    btime = None
    for line in proc_text.splitlines():
        if line.startswith("btime "):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    btime = int(parts[1])
                except ValueError:
                    btime = None
            break
    if btime is None:
        return "unknown", None, None
    try:
        ticks = int(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError, AttributeError, TypeError):
        ticks = 100
    if ticks <= 0:
        ticks = 100
    start = _format_lstart(btime + start_ticks // ticks)
    if start is None:
        return "unknown", None, None
    return "ok", start, command


def _now_epoch():
    return time.time()


def _newest_deadline_epoch(run_dir, deadline_at):
    best = None
    if isinstance(deadline_at, str) and deadline_at and not deadline_at.startswith("NO-DATA"):
        try:
            best = datetime.datetime.fromisoformat(deadline_at).timestamp()
        except (ValueError, TypeError, OSError):
            best = None
    path = os.path.join(_ledger_dir(run_dir), "events.jsonl")
    if not os.path.isfile(path):
        return best
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return best
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return best
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:   # sbe: allow-silent a torn line is skipped; a missed deadline extension only ends the run earlier, never later
            continue
        if not isinstance(record, dict):
            continue
        if record.get("kind") != "deadline-change":
            continue
        value = record.get("deadline_epoch")
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if best is None or value > best:
            best = value
    return best


def _newest_abandoned(run_dir):
    directory = _ledger_dir(run_dir)
    if not os.path.isdir(directory):
        return None
    try:
        names = os.listdir(directory)
    except OSError:   # sbe: allow-silent no abandon record is the strict answer: the verdict then reads HUNG, BLOCKED or CLOSED from stages, never CLOSED-BY-ABANDON
        return None
    best = None
    for name in names:
        if name == "ABANDONED.json" or (name.startswith("ABANDONED.") and name.endswith(".json")):
            path = os.path.join(directory, name)
            record, problem = _read_json(path)
            if problem or not isinstance(record, dict):
                continue
            at = record.get("at")
            if not isinstance(at, str):
                continue
            if best is None or at > str(best.get("at", "")):
                best = record
    return best


def _newest_started_at(run_dir):
    best = None
    for stage in STAGE_NAMES:
        _attempt, newest, _path, problem = _newest_attempt(run_dir, stage)
        if problem or newest is None:
            continue
        if newest.get("state") != "STARTED":
            continue
        started = newest.get("started_at")
        if not isinstance(started, str) or not started or started.startswith("NO-DATA"):
            continue
        if best is None or started > best:
            best = started
    return best


def _stage_corruption(run_dir):
    """Return a message naming a stage receipt that does not hold readable
    JSON, or None when every present stage receipt reads."""
    directory = _ledger_dir(run_dir)
    if not os.path.isdir(directory):
        return None
    try:
        names = os.listdir(directory)
    except OSError as exc:
        return "the ledger directory could not be listed (%s: %s)" % (type(exc).__name__, exc)
    for name in names:
        if not name.endswith(".json"):
            continue
        if len(name) < 3 or name[2] != "-":
            continue
        if name[:2] not in STAGE_NAMES:
            continue
        path = os.path.join(directory, name)
        record, problem = _read_json(path)
        if problem:
            return problem
        if not isinstance(record, dict):
            return "%s does not hold an object" % path
    return None


def _stage_effective(run_dir, stage, receipt_available):
    """Return (word, reason). word is DONE, SKIPPED, FAILED, RUNNING, HUNG,
    NO-DATA or MISSING; reason is a string, or a dict for HUNG."""
    attempt, newest, _path, problem = _newest_attempt(run_dir, stage)
    if problem:
        return "NO-DATA", problem
    if newest is None:
        if stage in ("03", "05") and receipt_available:
            return "DONE", "reconstructed from receipt/receipt.json"
        return "MISSING", None
    word = newest.get("state")
    if word in ("DONE", "SKIPPED"):
        return word, None
    if word == "FAILED":
        reason = newest.get("reason")
        if isinstance(reason, str) and reason and not reason.startswith("NO-DATA"):
            return "FAILED", reason
        return "FAILED", "FAILED"
    if word != "STARTED":
        return "NO-DATA", "attempt %d of stage %s holds state %s" % (attempt, stage, word)
    pid = newest.get("pid")
    status, seen_start, seen_command = _ps_read(pid)
    if status == "gone":
        return "FAILED", "died without an end"
    if status == "unknown":
        return "NO-DATA", "the liveness of pid %s is unknown" % (pid,)
    live = liveness(pid, newest.get("pid_start"), newest.get("command"), seen_start, seen_command)
    if live == "DEAD":
        return "FAILED", "died without an end"
    if live == "NO-DATA":
        return "NO-DATA", "the liveness of pid %s is unknown" % (pid,)
    deadline = _newest_deadline_epoch(run_dir, newest.get("deadline_at"))
    if deadline is not None and _now_epoch() > deadline:
        show = newest.get("deadline_at")
        if not isinstance(show, str) or show.startswith("NO-DATA"):
            show = _epoch_iso(int(deadline))
        return "HUNG", {"pid": pid, "deadline": show}
    return "RUNNING", None


def _verdict(run_dir):
    """The table of section 5.4, returned as one line whose first token is
    NO-DATA, REFUSED, HUNG, BLOCKED, CLOSED or CLOSED-BY-ABANDON."""
    base = os.path.basename(os.path.abspath(run_dir))
    ledger_dir = _ledger_dir(run_dir)
    receipt_path = _receipt_path(run_dir)
    has_ledger = os.path.isdir(ledger_dir)
    has_receipt = os.path.isfile(receipt_path)
    if not has_ledger and not has_receipt:
        return "NO-DATA: %s has no ledger" % base
    if has_receipt:
        _record, problem = _read_receipt(run_dir)
        if problem is not None and problem != "missing":
            return "REFUSED: %s" % problem
    problem = _stage_corruption(run_dir)
    if problem is not None:
        return "REFUSED: %s" % problem
    words = {}
    hung = None
    for stage in REQUIRED_STAGES:
        word, reason = _stage_effective(run_dir, stage, has_receipt)
        words[stage] = (word, reason)
        if word == "HUNG" and hung is None:
            hung = (stage, reason)
    abandoned = _newest_abandoned(run_dir)
    newest_started = _newest_started_at(run_dir)
    if abandoned is not None and newest_started is not None:
        at = abandoned.get("at")
        if isinstance(at, str) and at > newest_started:
            return "CLOSED-BY-ABANDON %s: %s" % (base, abandoned.get("words", ""))
    if hung is not None:
        stage, info = hung
        return "HUNG: %s pid %s alive past %s" % (stage, info.get("pid"), info.get("deadline"))
    for stage in REQUIRED_STAGES:
        word, reason = words[stage]
        if word not in ("DONE", "SKIPPED"):
            if word == "MISSING":
                reason = "no %s attempt recorded" % stage
            elif word == "RUNNING":
                reason = "still running"
            elif word == "NO-DATA":
                reason = reason if reason else "NO-DATA"
            elif word == "FAILED":
                reason = reason if reason else "FAILED"
            return "BLOCKED: %s not DONE for %s, %s" % (stage, base, reason)
    return "CLOSED %s" % base


def check(run_dir=None, use_last=False):
    """The verdict of section 5.4 (R-FX-06-7). Never returns CLOSED on a
    corrupt or unreadable input and reconstructs 03 and 05 in memory from a
    valid receipt when the ledger rows are missing."""
    _need_opt_str(run_dir, "run_dir")
    if not isinstance(use_last, bool):
        raise ValueError("use_last must be a bool")
    target, problem = _resolve_target(run_dir, use_last)
    if problem is not None:
        print(problem)
        if problem.startswith("NO-DATA"):
            return 3
        return 2
    first = _verdict(target)
    token = first.split(":")[0].split()[0]
    print(first)
    directory = os.path.dirname(os.path.abspath(__file__))
    if token == "REFUSED":
        return 2
    if token == "NO-DATA":
        return 3
    if token == "CLOSED-BY-ABANDON":
        return 0
    if token == "HUNG":
        parts = first.split()
        stage = parts[1] if len(parts) > 1 else "?"
        print('stop with: bash %s/stop_loop.sh --reason "hung %s"' % (directory, stage))
        return 1
    if token == "BLOCKED":
        print("finish with: python3 %s/finish_run.py --dry" % directory)
        print("python3 %s/finish_run.py --model <the finisher chosen at intake>" % directory)
        print('abandon with: python3 %s/run_ledger.py abandon --run-dir %s --words "<your words>"'
              % (directory, target))
        return 1
    word, note = _resolve_mode()
    if note:
        _note_once(note, sys.stderr)
    if word == "off":
        print("OFF: nothing recorded")
        return 0
    lock_path = _lock_path(target)
    problem = _put(_state_path(target), b"CLOSED\n", lock_path)
    if problem:
        print("WRITE FAILED: %s" % problem, file=sys.stderr)
        return 4
    return 0


def status(run_dir=None, use_last=False):
    """One line per stage (R-FX-06-8); a missing ledger prints NO-DATA per
    stage and exits 3."""
    _need_opt_str(run_dir, "run_dir")
    if not isinstance(use_last, bool):
        raise ValueError("use_last must be a bool")
    target, problem = _resolve_target(run_dir, use_last)
    if problem is not None:
        print(problem)
        if problem.startswith("NO-DATA"):
            return 3
        return 2
    ledger_dir = _ledger_dir(target)
    if not os.path.isdir(ledger_dir):
        for stage in sorted(STAGE_NAMES):
            print("%s %s NO-DATA: no ledger" % (stage, STAGE_NAMES[stage]))
        return 3
    has_receipt = os.path.isfile(_receipt_path(target))
    for stage in sorted(STAGE_NAMES):
        label = "%s %s" % (stage, STAGE_NAMES[stage])
        attempt, newest, _path, problem = _newest_attempt(target, stage)
        if problem:
            print("%s NO-DATA: %s" % (label, problem))
            continue
        if newest is None:
            if stage in ("03", "05") and has_receipt:
                print("%s DONE reconstructed attempt 0 from receipt/receipt.json" % label)
                continue
            print("%s NO-DATA: no receipt" % label)
            continue
        word, reason = _stage_effective(target, stage, has_receipt and stage in ("03", "05"))
        if word == "HUNG":
            print("%s HUNG pid %s since %s" % (label, reason.get("pid"), reason.get("deadline")))
            continue
        if word == "RUNNING":
            print("%s RUNNING attempt %d" % (label, attempt))
            continue
        if word == "NO-DATA":
            print("%s NO-DATA: %s" % (label, reason))
            continue
        if word == "MISSING":
            print("%s NO-DATA: no receipt" % label)
            continue
        text = reason if isinstance(reason, str) else ""
        print("%s %s attempt %d %s" % (label, word, attempt, text))
    return 0


def reconstruct(log, run_dir):
    """Rebuild 03 and 05 from the driver log (R-FX-06-9). Never overwrites
    an existing row (kept is printed); a corrupt receipt refuses before
    writing."""
    log = _need_str(log, "log")
    run_dir = _need_str(run_dir, "run_dir")
    problem = _run_dir_problem(run_dir)
    if problem:
        print("REFUSED: %s" % problem)
        return 2
    if _off_guard():
        return 0
    if not os.path.isfile(log):
        print("NO-DATA: the log %s is missing" % log)
        return 3
    try:
        with open(log, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        print("NO-DATA: the log %s could not be read (%s)" % (log, exc))
        return 3
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print("NO-DATA: the log %s is not utf-8 (%s)" % (log, exc))
        return 3
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if "RUN START" in line]
    ends = [i for i, line in enumerate(lines) if "RUN END" in line]
    if len(starts) != 1:
        print("NO-DATA: the log %s holds %d RUN START lines" % (log, len(starts)))
        return 3
    if len(ends) > 1:
        print("NO-DATA: the log %s holds %d RUN END lines" % (log, len(ends)))
        return 3
    ends_after = [i for i in ends if i > starts[0]]
    if ends and not ends_after:
        print("NO-DATA: the log %s ends before it starts" % log)
        return 3
    receipt_path = _receipt_path(run_dir)
    receipt = None
    if os.path.isfile(receipt_path):
        record, problem = _read_receipt(run_dir)
        if problem is not None and problem != "missing":
            print("REFUSED: %s" % problem)
            return 2
        if record is not None:
            receipt = record
    have03 = _newest_attempt(run_dir, "03")[1] is not None
    have05 = _newest_attempt(run_dir, "05")[1] is not None
    to_write = []
    if have03:
        print("03 kept")
    else:
        record3 = _blank_receipt(run_dir, "03", 1, "DONE" if ends_after else "FAILED")
        record3["started_at"] = _now_iso()
        record3["ended_at"] = _now_iso()
        if ends_after:
            record3["reason"] = "reconstructed from %s" % log
        else:
            record3["reason"] = "the driver died without an end"
        to_write.append((_attempt_path(run_dir, "03", 1), record3))
    if have05:
        print("05 kept")
    else:
        if receipt is not None:
            record5 = _blank_receipt(run_dir, "05", 1, "DONE")
            end_state = receipt.get("end_state")
            record5["reason"] = str(end_state) if end_state else "reconstructed from the receipt"
            record5["log_path"] = log
            digest = receipt.get("driver_log_sha256")
            if isinstance(digest, str) and digest:
                record5["log_sha256"] = digest
            else:
                record5["log_sha256"] = _sha256_of(log)
        else:
            record5 = _blank_receipt(run_dir, "05", 1, "FAILED")
            record5["reason"] = "no receipt: an end that writes none failed by construction"
        record5["started_at"] = _now_iso()
        record5["ended_at"] = _now_iso()
        to_write.append((_attempt_path(run_dir, "05", 1), record5))
    if not to_write:
        return 0
    lock_path = _lock_path(run_dir)
    for path, record in to_write:
        problem = _put_json(path, record, lock_path)
        if problem:
            print("WRITE FAILED: %s" % problem, file=sys.stderr)
            return 4
        marker = {"at": _now_iso(), "kind": "reconstruct",
                  "stage": record.get("stage"), "state": record.get("state")}
        problem = _append_event(run_dir, marker, lock_path)
        if problem:
            print("WRITE FAILED: %s" % problem, file=sys.stderr)
            return 4
    if receipt is not None:
        _put(_state_path(run_dir), b"ENDED\n", lock_path)
    else:
        _put(_state_path(run_dir), b"FAILED\n", lock_path)
    return 0


def main(argv=None):
    """ValueError for an argv that is not None or a list or tuple of str
    (contract 4); otherwise an exit code, never sys.exit."""
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, (list, tuple)):
        raise ValueError("argv must be a list or a tuple of str")
    for item in argv:
        if not isinstance(item, str):
            raise ValueError("every item of argv must be a str")
    words = list(argv)
    if not words:
        print("usage: run_ledger.py <verb> ...", file=sys.stderr)
        return 2
    verb = words[0]
    if verb == "mode":
        if len(words) != 1:
            print("usage: run_ledger.py mode", file=sys.stderr)
            return 2
        word, note = _resolve_mode()
        print(word)
        if note:
            _note_once(note, sys.stdout)
        return 0
    _word, note = _resolve_mode()
    if note:
        _note_once(note, sys.stderr)
    if verb == "begin":
        parser = argparse.ArgumentParser(prog="run_ledger.py begin", add_help=False)
        parser.add_argument("--stage", required=True)
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--pid", required=True, type=int)
        parser.add_argument("--deadline-epoch", type=int)
        parser.add_argument("--tool")
        parser.add_argument("--log")
        parser.add_argument("--pid-start")
        parser.add_argument("--command")
        parser.add_argument("trailing", nargs=argparse.REMAINDER)
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        trailing = list(parsed.trailing)
        if trailing and trailing[0] == "--":
            trailing = trailing[1:]
        try:
            return begin(parsed.stage, parsed.run_dir, parsed.pid, parsed.deadline_epoch,
                         parsed.tool, parsed.log, trailing, parsed.pid_start, parsed.command)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if verb == "end":
        parser = argparse.ArgumentParser(prog="run_ledger.py end", add_help=False)
        parser.add_argument("--stage", required=True)
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--state", required=True)
        parser.add_argument("--exit-code", type=int)
        parser.add_argument("--reason")
        parser.add_argument("--started-at")
        parser.add_argument("--log")
        parser.add_argument("--points-at")
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        try:
            return end(parsed.stage, parsed.run_dir, parsed.state, parsed.exit_code,
                       parsed.reason, parsed.started_at, parsed.log, parsed.points_at)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if verb == "event":
        parser = argparse.ArgumentParser(prog="run_ledger.py event", add_help=False)
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--kind", required=True)
        parser.add_argument("--detail")
        parser.add_argument("--json")
        parser.add_argument("--deadline-epoch", type=int)
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        fields = None
        if parsed.json is not None:
            try:
                decoded = json.loads(parsed.json)
            except ValueError as exc:
                print("REFUSED: --json is not readable JSON (%s)" % exc, file=sys.stderr)
                return 2
            if not isinstance(decoded, dict):
                print("REFUSED: --json must decode to an object", file=sys.stderr)
                return 2
            fields = decoded
        try:
            return event(parsed.run_dir, parsed.kind, parsed.detail, fields, parsed.deadline_epoch)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if verb == "state":
        parser = argparse.ArgumentParser(prog="run_ledger.py state", add_help=False)
        parser.add_argument("--run-dir", required=True)
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        try:
            word = state(parsed.run_dir)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(word)
        if word.startswith("NO-DATA"):
            return 3
        return 0
    if verb == "abandon":
        parser = argparse.ArgumentParser(prog="run_ledger.py abandon", add_help=False)
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--words", required=True)
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        try:
            return abandon(parsed.run_dir, parsed.words)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if verb == "check":
        parser = argparse.ArgumentParser(prog="run_ledger.py check", add_help=False)
        parser.add_argument("--run-dir")
        parser.add_argument("--last", action="store_true")
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        try:
            return check(parsed.run_dir, parsed.last)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if verb == "status":
        parser = argparse.ArgumentParser(prog="run_ledger.py status", add_help=False)
        parser.add_argument("--run-dir")
        parser.add_argument("--last", action="store_true")
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        try:
            return status(parsed.run_dir, parsed.last)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if verb == "reconstruct":
        parser = argparse.ArgumentParser(prog="run_ledger.py reconstruct", add_help=False)
        parser.add_argument("--log", required=True)
        parser.add_argument("--run-dir", required=True)
        try:
            parsed = parser.parse_args(words[1:])
        except SystemExit:
            print("usage error", file=sys.stderr)
            return 2
        try:
            return reconstruct(parsed.log, parsed.run_dir)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    print("usage: run_ledger.py <verb> ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
