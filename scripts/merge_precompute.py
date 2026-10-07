#!/usr/bin/env python3
"""The pre-computation: every listed PR is gated in list order, in the background, as soon as pins are read (MG1.c).

WHY THIS EXISTS. One `required_fast` run took 354 s under load, and the owner's merge waited for it. Here the pins are
read from GitHub by the tool (never typed), the list is written, and a detached worker gates each PR in order on the
CHAINED tree (main plus PR 1, then plus PR 2), one gate at a time, in a scratch worktree, under a run scoped TMPDIR,
waiting on scripts/heavy_slot.py's load rules before each gate. The owner's merge then finds every row present.

WHAT IT PROVES, AND WHAT IT NEVER DOES. Rows are written only through merge_gate.run_gate (R-MG-2): this module never
calls record_row and types no rc. A PR whose `mergeable` is still being computed never enters the list (NO-DATA, exit
2); CONFLICTING is a refusal (exit 1). The worker stops at the first FAIL and marks the rest BLOCKED-BY, re-fetches hub
main after each gate and restarts the chain from a moved main at most three times (then STALE-BASE), and skips a
chained tree that already has a valid PASS row. `wait_ready` returns NO-DATA on timeout, never a pass.

STDLIB ONLY, Python 3.9 floor. This module is a declared command runner: it spawns the detached worker and asks `ps`
which command a lock's pid runs. Every other process goes through the runner the caller injects, and the default
runner is subprocess.run with stdin closed.

Usage:
  merge_precompute.py pins <clone> <list> <pr>...   read the pins from GitHub, write the list, start the worker
  merge_precompute.py start <clone> <list>          start (or find) the background worker for a written list
  merge_precompute.py worker <clone> <list>         the foreground worker gate_merge_seq.sh execs into
  merge_precompute.py status <clone> <list>         print the progress file as JSON
  merge_precompute.py wait <clone> <tree> <seconds> wait for the tree's row: exit 0 PASS, 1 FAIL, 2 NO-DATA
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402

try:
    import heavy_slot as _heavy_slot
except ImportError:  # no load rules readable: the gate still runs, one at a time, and says so
    _heavy_slot = None

SCHEMA = 1
PRECOMPUTE_DIR = "precompute"
REMOTE = "hub"
BRANCH = "main"
MAX_RESTARTS = 3
POLL_S = 1.0
WORKER_NAMES = ("merge_precompute", "gate_merge_seq")
GATE_ARGV = ["sh", merge_gate.GATE_SCRIPT]
PIN_FIELDS = "state,baseRefName,headRefName,mergeable,headRefOid"
USAGE = ("usage: merge_precompute.py pins <clone> <list> <pr>... | start <clone> <list> | worker <clone> <list> | "
         "status <clone> <list> | wait <clone> <tree> <seconds>")

MergeGateError = merge_gate.MergeGateError
MergeNoData = merge_gate.MergeNoData
_as_str = merge_gate._as_str
_as_env = merge_gate._as_env
TREE_RE = merge_gate.TREE_RE


class PinRefused(MergeGateError):
    """A PR that must never enter the list: not OPEN, another base, or CONFLICTING (exit 1)."""


def default_runner(argv, cwd=None, env=None):
    """subprocess.run with stdin closed and text output; the one process path of this module besides the spawn."""
    return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          errors="replace")


def _runner(runner):
    if runner is None:
        return default_runner
    if not callable(runner):
        raise MergeGateError("runner must be callable, not %s" % type(runner).__name__)
    return runner


def _call(runner, argv, cwd=None, env=None):
    return merge_gate._call(runner, argv, cwd, env)


def _git(runner, clone, argv):
    return merge_gate._git_text(runner, clone, argv).strip()


def _sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        raise MergeNoData("the list at %s cannot be read (%s)" % (path, exc))


def _pr_number(value):
    value = _as_str(value, "pr").strip()
    if not value.isdigit():
        raise MergeGateError("a pull request number is digits, not %r" % value[:40])
    return value


# ---------------------------------------------------------------------------------------------------------------------
# pins: read from GitHub, never typed
# ---------------------------------------------------------------------------------------------------------------------
def read_pins(prs, repo, runner=None, base=BRANCH):
    """One pin per PR, in list order, from `gh pr view --json`. A PR the tool cannot read is NO-DATA for the WHOLE
    step (never a partial list); `mergeable` other than MERGEABLE stops it too: UNKNOWN (still computing) is NO-DATA,
    CONFLICTING is a refusal naming the PR. A PR that is not OPEN, or whose base is not `base`, is refused."""
    if isinstance(prs, (str, bytes)) or not isinstance(prs, (list, tuple)):
        raise MergeGateError("prs must be a list of pull request numbers, not %s" % type(prs).__name__)
    numbers = [_pr_number(pr) for pr in prs]
    repo = _as_str(repo, "repo")
    base = _as_str(base, "base")
    runner = _runner(runner)
    pins = []
    for pr in numbers:
        code, out, err = _call(runner, ["gh", "pr", "view", pr, "-R", repo, "--json", PIN_FIELDS])
        if code != 0:
            raise MergeNoData("#%s cannot be read (gh exited %d: %s)" % (pr, code, (err or out).strip()[:200]))
        try:
            info = json.loads(out)
        except ValueError:
            raise MergeNoData("#%s: gh printed no JSON" % pr)
        if not isinstance(info, dict):
            raise MergeNoData("#%s: gh printed no object" % pr)
        for key in ("state", "baseRefName", "headRefName", "mergeable", "headRefOid"):
            if not isinstance(info.get(key), str) or not info[key]:
                raise MergeNoData("#%s: gh gave no %s" % (pr, key))
        if info["state"] != "OPEN":
            raise PinRefused("#%s: state is %s" % (pr, info["state"]))
        if info["baseRefName"] != base:
            raise PinRefused("#%s: base is %s, not %s" % (pr, info["baseRefName"], base))
        if info["mergeable"] == "CONFLICTING":
            raise PinRefused("#%s: mergeable is CONFLICTING" % pr)
        if info["mergeable"] != "MERGEABLE":
            raise MergeNoData("#%s: mergeable is %s, not yet known to merge" % (pr, info["mergeable"]))
        if not merge_gate.TREE_RE.match(info["headRefOid"]):
            raise MergeNoData("#%s: headRefOid is not a 40 hex sha" % pr)
        pins.append({"pr": pr, "head": info["headRefOid"], "head_ref": info["headRefName"],
                     "base_ref": info["baseRefName"], "state": info["state"], "mergeable": info["mergeable"]})
    return pins


def write_list(path, pins):
    """The list merge_verified.sh reads: one `<pr> <sha>` line per pin, in order."""
    path = _as_str(path, "path")
    lines = "".join("%s %s\n" % (pin["pr"], pin["head"]) for pin in pins)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(lines)
    except OSError as exc:
        raise MergeNoData("the list at %s cannot be written (%s)" % (path, exc))


def parse_list(path):
    """[{pr, head}] from a written list. A line with no sha, or a sha that is not 40 hex, refuses the list."""
    path = _as_str(path, "path")
    text = merge_gate._read_text(path)
    pins = []
    for number, line in enumerate(text.splitlines(), 1):
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) < 2 or not TREE_RE.match(fields[1]):
            raise MergeNoData("%s line %d carries no pinned sha" % (path, number))
        pins.append({"pr": _pr_number(fields[0]), "head": fields[1]})
    return pins


# ---------------------------------------------------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------------------------------------------------
def _merge_tree_runner(runner):
    """merged_tree reads stdout alone; a failed merge-tree explains itself on stderr, so that is folded in."""
    def run(argv, cwd=None):
        proc = runner(argv, cwd=cwd, env=None)
        if proc.returncode != 0 and not (proc.stdout or "").strip():
            return Proc(proc.returncode, proc.stderr or "", proc.stderr or "")
        return proc
    return run


class Proc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _chain_step(runner, clone, base, pin):
    """(tree, next base): the tree `base` plus the pin's head would make, and a commit of it to chain the next pin."""
    tree = merge_gate.merged_tree(clone, base, pin["head"], runner=_merge_tree_runner(runner))
    chained = _git(runner, clone, ["commit-tree", tree, "-p", base, "-m", "precompute #%s" % pin["pr"]])
    return tree, chained


def chain_trees(clone, base, pins, runner=None):
    """The tree each pin makes on top of the one before it, in list order: main plus PR 1, then plus PR 2."""
    clone = _as_str(clone, "clone")
    base = _as_str(base, "base")
    if isinstance(pins, (str, bytes)) or not isinstance(pins, (list, tuple)):
        raise MergeGateError("pins must be a list, not %s" % type(pins).__name__)
    runner = _runner(runner)
    trees = []
    for pin in pins:
        if not isinstance(pin, dict) or not isinstance(pin.get("head"), str):
            raise MergeGateError("every pin is an object with a head")
        tree, base = _chain_step(runner, clone, base, pin)
        trees.append(tree)
    return trees


def _fetch_main(runner, clone):
    code, out, err = _call(runner, ["git", "fetch", "--quiet", REMOTE, BRANCH], clone)
    if code != 0:
        raise MergeNoData("cannot fetch %s/%s: %s" % (REMOTE, BRANCH, (err or out).strip()[:200]))
    return _git(runner, clone, ["rev-parse", "FETCH_HEAD"])


def _write_status(path, status):
    if path is None:
        return
    status["updated"] = merge_gate._iso(time.time())
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(status, handle, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        raise MergeNoData("the status at %s cannot be written (%s)" % (path, exc))


def _take_slot(env):
    """Wait on heavy_slot's load rules; the held slots, released by _release_slot. No rules readable: run unqueued."""
    if _heavy_slot is None or env.get(_heavy_slot.HELD_ENV) or env.get(_heavy_slot.SWITCH_ENV, "").lower() == "off":
        return []
    return _heavy_slot.acquire(env)


def _release_slot(held):
    for _index, handle in held:
        try:
            handle.seek(0)
            handle.truncate()
        except OSError:
            pass  # sbe: allow-silent the label is informational; closing releases the lock
        handle.close()


def _gate_one(runner, clone, scratch, env, tree, chained, pin, base, take_slot):
    """One gate in a scratch worktree at the CHAINED commit (the tree main will have: main plus every earlier pin
    plus this one), never at the pin's own head, which lacks the earlier pins; the row is written by
    merge_gate.run_gate alone, which itself refuses a worktree whose tree is not `tree`."""
    worktree = os.path.join(scratch, "wt-%s" % pin["pr"])
    code, out, err = _call(runner, ["git", "worktree", "add", "--quiet", "--detach", worktree, chained], clone)
    if code != 0:
        raise MergeNoData("cannot make a scratch worktree at %s: %s" % (chained, (err or out).strip()[:200]))
    held = take_slot(env) if take_slot is not None else []
    try:
        merge_gate.run_gate(clone, worktree, tree, pin["pr"], pin["head"], base, env, runner=runner)
    finally:
        _release_slot(held)
        _call(runner, ["git", "worktree", "remove", "--force", worktree], clone)


def run_precompute(clone, pins, env=None, runner=None, status_path=None, take_slot=None):
    """Gate every pin in list order on the chained tree. Returns the final status object (also written to
    `status_path` after every step when given): status DONE, FAILED, STALE-BASE or NO-DATA, and one entry per pin
    with result PASS, FAIL, SKIPPED-ALREADY-GATED, BLOCKED-BY #n, CONFLICT or NO-DATA."""
    clone = _as_str(clone, "clone")
    if isinstance(pins, (str, bytes)) or not isinstance(pins, (list, tuple)):
        raise MergeGateError("pins must be a list, not %s" % type(pins).__name__)
    for pin in pins:
        if not isinstance(pin, dict) or not isinstance(pin.get("pr"), str) or not isinstance(pin.get("head"), str):
            raise MergeGateError("every pin is an object with pr and head")
    environment = _as_env(env)
    runner = _runner(runner)
    scratch = tempfile.mkdtemp(prefix="merge-precompute-", dir=environment.get("TMPDIR") or None)
    environment["TMPDIR"] = scratch   # run scoped: the gate child inherits it through run_gate's allowlist
    status = {"schema": SCHEMA, "pid": os.getpid(), "started": merge_gate._iso(time.time()), "restarts": 0,
              "status": "RUNNING", "base": "", "entries": []}
    try:
        base = _fetch_main(runner, clone)
        while True:
            status["base"] = base
            status["entries"] = [{"pr": p["pr"], "head": p["head"], "tree": "", "result": "PENDING"} for p in pins]
            _write_status(status_path, status)
            current, blocked_by, restarted = base, None, False
            for index, pin in enumerate(pins):
                entry = status["entries"][index]
                if blocked_by is not None:
                    entry["result"] = "BLOCKED-BY #%s" % blocked_by
                    _write_status(status_path, status)
                    continue
                try:
                    tree, chained = _chain_step(runner, clone, current, pin)
                except merge_gate.MergeConflict as exc:
                    entry["result"] = "CONFLICT: %s" % exc
                    blocked_by = pin["pr"]
                    _write_status(status_path, status)
                    continue
                entry["tree"] = tree
                verdict, why = merge_gate.verify_tree(clone, tree, environment, runner)
                if verdict == "PASS":
                    entry["result"] = "SKIPPED-ALREADY-GATED"
                else:
                    try:
                        _gate_one(runner, clone, scratch, environment, tree, chained, pin, current, take_slot)
                    except MergeGateError as exc:
                        entry["result"] = "NO-DATA: %s" % exc
                        status["status"] = "NO-DATA"
                        _write_status(status_path, status)
                        return status
                    verdict, why = merge_gate.verify_tree(clone, tree, environment, runner)
                    entry["result"] = verdict if verdict in ("PASS", "FAIL") else "NO-DATA: %s" % why
                    if verdict != "PASS":
                        blocked_by = pin["pr"]   # no tree is recorded on top of a failing one
                _write_status(status_path, status)
                if blocked_by is not None:
                    continue
                current = chained
                fresh = _fetch_main(runner, clone)
                if fresh != base:
                    status["restarts"] += 1
                    if status["restarts"] > MAX_RESTARTS:
                        status["status"] = "STALE-BASE"
                        _write_status(status_path, status)
                        return status
                    base, restarted = fresh, True
                    break
            if restarted:
                continue
            status["status"] = "FAILED" if blocked_by is not None else "DONE"
            _write_status(status_path, status)
            return status
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------------
# the background worker: one per list, found again by its lock
# ---------------------------------------------------------------------------------------------------------------------
def _precompute_dir(clone, env):
    directory = os.path.join(os.path.dirname(merge_gate.ledger_path(clone, env)), PRECOMPUTE_DIR)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise MergeNoData("the precompute directory %s cannot be made (%s)" % (directory, exc))
    return directory


def status_path(clone, list_path, env=None):
    """`<ledger dir>/precompute/<list sha256>.json`, the progress file for this exact list text."""
    return os.path.join(_precompute_dir(clone, env), _sha256_file(list_path) + ".json")


def _pid_is_worker(pid, runner):
    """True when `ps -o command= -p <pid>` names this worker, False when ps shows no row (exit 1, nothing printed: a
    dead pid) or a row naming another command (a reused pid), and None when ps CANNOT ANSWER (it cannot be started,
    the landing sandbox denies it, or it exits any other way). None is unknown liveness: the caller blocks on it,
    never reclaims and never spawns beside a holder it cannot see."""
    try:
        code, out, err = _call(runner, ["ps", "-o", "command=", "-p", str(pid)])
    except MergeGateError:
        return None
    if code == 0:
        return any(name in out for name in WORKER_NAMES)
    if code == 1 and not out.strip() and not err.strip():
        return False
    return None


def _read_lock(path):
    try:
        with open(path, "rb") as handle:
            lock = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(lock, dict) or isinstance(lock.get("pid"), bool) or not isinstance(lock.get("pid"), int):
        return None
    return lock


def start_background(clone, list_path, env=None, runner=None):
    """Spawn the detached worker for the list (through gate_merge_seq.sh, which execs into `worker`) and return its
    pid. A second start while the lock's pid is alive AND runs this worker returns the same pid (idempotent). A lock
    whose pid is dead, or alive under another command, is reclaimed: the same actor again after a crash."""
    clone = _as_str(clone, "clone")
    list_path = _as_str(list_path, "list_path")
    environment = _as_env(env)
    runner = _runner(runner)
    directory = _precompute_dir(clone, environment)
    digest = _sha256_file(list_path)
    lock_path = os.path.join(directory, digest + ".lock")
    guard = os.open(lock_path + ".flock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(guard, fcntl.LOCK_EX)
        lock = _read_lock(lock_path)
        if lock is not None and lock.get("list_sha256") == digest:
            alive = _pid_is_worker(lock["pid"], runner)
            if alive is None:
                raise MergeNoData("the lock at %s names pid %d and ps cannot say whether it still runs this worker; "
                                  "nothing is reclaimed and no second worker is started" % (lock_path, lock["pid"]))
            if alive:
                return lock["pid"]
        log_path = os.path.join(directory, digest + ".log")
        # the CLONE's own worker, never this module's neighbour: gate_merge_seq.sh derives its clone from its own
        # location, so a worker taken from elsewhere would gate another repository and write under its git dir
        tool = os.path.join(clone, "scripts", "gate_merge_seq.sh")
        if not os.path.isfile(tool):
            raise MergeNoData("the clone %s ships no scripts/gate_merge_seq.sh to run as its worker" % clone)
        try:
            log = open(log_path, "ab")
        except OSError as exc:
            raise MergeNoData("the worker log %s cannot be opened (%s)" % (log_path, exc))
        try:
            child = subprocess.Popen(["/bin/sh", tool, list_path], cwd=clone, env=environment, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)
        except OSError as exc:
            raise MergeNoData("the worker cannot be started (%s)" % exc)
        finally:
            log.close()
        tmp = lock_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"pid": child.pid, "list_sha256": digest, "started": merge_gate._iso(time.time())}, handle)
        os.replace(tmp, lock_path)
        return child.pid
    finally:
        os.close(guard)


def read_status(list_path, env=None, clone=None):
    """The progress object for the list, or {"status": "NO-DATA", ...} when there is none or it is truncated. The
    clone defaults to the repository this script ships in, the same default the shell tools derive."""
    list_path = _as_str(list_path, "list_path")
    clone = os.path.dirname(HERE) if clone is None else _as_str(clone, "clone")
    try:
        path = status_path(clone, list_path, env)
    except MergeGateError as exc:
        return {"status": "NO-DATA", "why": str(exc)}
    try:
        with open(path, "rb") as handle:
            status = json.loads(handle.read().decode("utf-8"))
    except FileNotFoundError:
        return {"status": "NO-DATA", "why": "no precompute status for this list at %s" % path}
    except (OSError, ValueError):
        return {"status": "NO-DATA", "why": "the status at %s is truncated or unreadable" % path}
    if not isinstance(status, dict) or status.get("schema") != SCHEMA or not isinstance(status.get("status"), str):
        return {"status": "NO-DATA", "why": "the status at %s is not a precompute record" % path}
    return status


def wait_ready(clone, tree, timeout_s, env=None, runner=None):
    """Poll the ledger for the tree until `timeout_s`: the verify_tree verdict as soon as it is PASS or FAIL, and
    ("NO-DATA", why) at the timeout, never a pass. verify_tree is verify_newest, so every row rule applies here too."""
    clone = _as_str(clone, "clone")
    tree = _as_str(tree, "tree")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s < 0 \
            or timeout_s != timeout_s:
        raise MergeGateError("timeout_s must be a non negative number of seconds")
    environment = _as_env(env)
    runner = _runner(runner)
    deadline = time.monotonic() + timeout_s
    while True:
        verdict, why = merge_gate.verify_tree(clone, tree, environment, runner)
        if verdict in ("PASS", "FAIL"):
            return verdict, why
        left = deadline - time.monotonic()
        if left <= 0:
            return "NO-DATA", "timed out after %g s waiting for a row for tree %s (%s)" % (timeout_s, tree, why)
        time.sleep(min(POLL_S, left))


# ---------------------------------------------------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------------------------------------------------
def _refuse_env():
    reason = merge_gate.refuse_steered_env(dict(os.environ), False)
    if reason:
        sys.stderr.write("NO-DATA: %s\n" % reason)
        return 2
    return 0


def _repo_of(clone):
    """owner/repo from the clone's hub remote, the way merge_verified.sh derives it."""
    url = _git(default_runner, clone, ["config", "--get", "remote.%s.url" % REMOTE])
    for marker in ("github.com:", "github.com/"):
        if marker in url:
            return url.split(marker, 1)[1][:-4] if url.endswith(".git") else url.split(marker, 1)[1]
    return url


def main(argv=None):
    if argv is not None and not (isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv)):
        raise ValueError("main: argv must be None or a list or tuple of str, got %s" % type(argv).__name__)
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(USAGE + "\n")
        return 2
    command, rest = args[0], args[1:]
    if command not in ("pins", "start", "worker", "status", "wait"):
        sys.stderr.write(USAGE + "\n")
        return 2
    code = _refuse_env()
    if code:
        return code
    try:
        if command == "pins":
            if len(rest) < 3:
                sys.stderr.write(USAGE + "\n")
                return 2
            clone, list_path = rest[0], rest[1]
            pins = read_pins(rest[2:], _repo_of(clone))
            write_list(list_path, pins)
            for pin in pins:
                sys.stdout.write("PIN #%s %s (%s)\n" % (pin["pr"], pin["head"], pin["head_ref"]))
            sys.stdout.write("WORKER pid %d\n" % start_background(clone, list_path))
            return 0
        if command == "start":
            if len(rest) != 2:
                sys.stderr.write(USAGE + "\n")
                return 2
            sys.stdout.write("WORKER pid %d\n" % start_background(rest[0], rest[1]))
            return 0
        if command == "worker":
            if len(rest) != 2:
                sys.stderr.write(USAGE + "\n")
                return 2
            pins = parse_list(rest[1])
            status = run_precompute(rest[0], pins, status_path=status_path(rest[0], rest[1]), take_slot=_take_slot)
            for entry in status["entries"]:
                sys.stdout.write("#%s %s %s\n" % (entry["pr"], entry["tree"] or "-", entry["result"]))
            sys.stdout.write("%s: %d listed PR(s), %d restart(s)\n"
                             % (status["status"], len(status["entries"]), status["restarts"]))
            return 0 if status["status"] == "DONE" else (1 if status["status"] == "FAILED" else 2)
        if command == "status":
            if len(rest) != 2:
                sys.stderr.write(USAGE + "\n")
                return 2
            status = read_status(rest[1], clone=rest[0])
            sys.stdout.write(json.dumps(status, sort_keys=True, indent=1) + "\n")
            return 2 if status.get("status") == "NO-DATA" else 0
        if len(rest) != 3 or not rest[2].isdigit():
            sys.stderr.write(USAGE + "\n")
            return 2
        verdict, why = wait_ready(rest[0], rest[1], int(rest[2]))
    except MergeGateError as exc:
        sys.stderr.write("%s: %s\n" % ("STOP" if isinstance(exc, PinRefused) else "NO-DATA", exc))
        return 1 if isinstance(exc, PinRefused) else 2
    sys.stdout.write("%s %s\n" % (verdict, why))
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
