"""land_queue: the hub PR lander as a product script, not five loose shell files.

WHY (row M6, docs/plan/READINESS-ROADMAP-2026-08-29.json). On 2026-09-06 21:24
JST a switch restarted the serial consumer twice, seven gates ran at once, the
pre-push gate's own 60 second test timeouts painted gates red under that load,
and a PR was refused for load rather than for a real defect. The loose scripts
(~/.claude/evidence/serial-queue.sh, land_one.sh, regate.sh, merge_if_green.sh,
parallel-land.sh) have no pid lock (nothing stopped a second consumer), no
concurrency ceiling (parallel-land.sh's MAXJ was a manual override, not a
default), and no timeout class (a load timeout and a real gate failure printed
the same way). This file is those three controls around the SAME gate and
merge commands, unchanged.

HOW TO REPLACE THE LOOSE SCRIPTS WITHOUT CHANGING WHAT LANDS. Nothing about
regate.sh or merge_if_green.sh changes today; only what drives them does.

  1. Same queue file: ~/.claude/evidence/land-queue.txt, one PR number per
     line, unchanged format.
  2. Same log file: ~/.claude/evidence/serial-land.log gets the same
     "merge <n>: ..." and "LAND-<n>-END" lines it already has; a session
     reading that log does not need to know which driver wrote a given line.
  3. Point --gate and --merge at the existing scripts, with "{n}" standing in
     for the PR number (this file substitutes it, so do not redirect their
     output yourself; land_queue.py captures it and writes
     ~/.claude/evidence/gate-regate<n>.log itself, the same path
     merge_if_green.sh already reads):

       python3 scripts/land_queue.py run \\
         --queue ~/.claude/evidence/land-queue.txt \\
         --log   ~/.claude/evidence/serial-land.log \\
         --gate  "bash ~/.claude/evidence/regate.sh {n}" \\
         --merge "bash ~/.claude/evidence/merge_if_green.sh {n}" \\
         --concurrency 2

  4. Retire serial-queue.sh, land_one.sh and parallel-land.sh once this has
     run clean for a few PRs; regate.sh and merge_if_green.sh keep doing the
     gate-detail and merge-decision work they already do, "until those move
     too" per the row.

THE THREE CONTROLS.
  - A pid lock file (default <queue>.lock) refuses a second `run` while a
    live one holds it; a lock naming a dead pid is reclaimed and the reclaim
    is written to --log before work starts.
  - `--concurrency N` bounds how many gate commands run at once with a
    semaphore, not a batch: a slot frees the moment one gate finishes, so the
    next queued number starts immediately rather than waiting for a whole
    batch of N to land together.
  - A gate whose captured output carries "TimeoutExpired" or "timed out" and
    no "required_fast exit 0" line is a load timeout, not a verdict on the
    change: it is re-gated ALONE, after every other gate in this run has
    finished, before its result can be used to refuse anything.

Python 3, standard library only. No network calls of its own; --gate and
--merge are opaque shell commands this file merely runs and logs.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

TIMEOUT_MARKERS = ("TimeoutExpired",)
TIMEOUT_SUBSTRING_CI = "timed out"
REQUIRED_FAST_GREEN = "required_fast exit 0"


# ---------------------------------------------------------------- the lock --

def read_lock(lock_path):
    try:
        with open(lock_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_lock(lock_path, data):
    tmp = lock_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, lock_path)


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else: still alive
    except OSError:
        return False
    return True


def acquire_lock(lock_path):
    """Returns (ok, note). note is a reclaim message when a dead lock was
    replaced, empty otherwise. ok is False when a live pid already holds it."""
    held = read_lock(lock_path)
    note = ""
    if held:
        other = held.get("pid")
        if other != os.getpid() and pid_alive(other):
            return False, "land_queue: REFUSED to start, pid %s already holds %s" % (other, lock_path)
        if other and not pid_alive(other):
            note = "land_queue: reclaimed a dead lock (pid %s) at %s" % (other, lock_path)
    write_lock(lock_path, {"pid": os.getpid(), "since": time.time(), "inflight": []})
    return True, note


def release_lock(lock_path, expected_pid):
    held = read_lock(lock_path)
    if held and held.get("pid") == expected_pid:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


# --------------------------------------------------------------- the queue --

def pop_one(queue_path):
    """Head line, rewrite atomically: the same contract serial-queue.sh
    keeps. Returns the first token of the first non-blank line, or None."""
    if not os.path.exists(queue_path):
        return None
    with open(queue_path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if line.strip():
            n = line.split()[0]
            rest = lines[:i] + lines[i + 1:]
            tmp = queue_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(rest)
            os.replace(tmp, queue_path)
            return n
    return None


def queue_length(queue_path):
    if not os.path.exists(queue_path):
        return 0
    with open(queue_path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


# ------------------------------------------------------------- gate/merge --

def run_cmd(template, n):
    """Run a shell command template with {n} substituted for the PR number,
    capturing combined stdout+stderr. Returns (exit_code, text)."""
    cmd = template.format(n=n)
    proc = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout or ""


def is_timeout_red(text):
    has_timeout = any(m in text for m in TIMEOUT_MARKERS) or TIMEOUT_SUBSTRING_CI in text.lower()
    return has_timeout and REQUIRED_FAST_GREEN not in text


def append_log(log_path, text):
    if not text.endswith("\n"):
        text += "\n"
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(text)


def write_gate_log(gate_dir, n, text):
    path = os.path.join(gate_dir, "gate-regate%s.log" % n)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ------------------------------------------------------------------- run --

def run(queue_path, log_path, gate_tpl, merge_tpl, concurrency=1, lock_path=None):
    lock_path = lock_path or (queue_path + ".lock")
    gate_dir = os.path.dirname(os.path.abspath(log_path)) or "."
    stop_path = lock_path + ".stop"

    ok, note = acquire_lock(lock_path)
    if not ok:
        print(note)
        return 3
    if note:
        append_log(log_path, note)
        print(note)

    io_lock = threading.Lock()
    inflight = set()

    def publish_inflight():
        with io_lock:
            data = read_lock(lock_path) or {"pid": os.getpid()}
            data["inflight"] = sorted(inflight, key=str)
            write_lock(lock_path, data)

    results = {}
    order = []
    sem = threading.Semaphore(max(1, concurrency))
    threads = []

    def worker(n):
        try:
            code, text = run_cmd(gate_tpl, n)
            results[n] = (code, text)
            write_gate_log(gate_dir, n, text)
        finally:
            inflight.discard(n)
            publish_inflight()
            sem.release()

    try:
        while not os.path.exists(stop_path):
            n = pop_one(queue_path)
            if n is None:
                break
            order.append(n)
            sem.acquire()
            inflight.add(n)
            publish_inflight()
            t = threading.Thread(target=worker, args=(n,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        # A load timeout is not a refusal until it fails ALONE: re-gate every
        # TIMEOUT-RED number by itself, after everything else in this run has
        # finished, before its result can decide a merge.
        for n in order:
            code, text = results.get(n, (1, ""))
            if is_timeout_red(text):
                append_log(
                    log_path,
                    "TIMEOUT-RED %s: load timeout, not a verdict, re-gating alone" % n,
                )
                code2, text2 = run_cmd(gate_tpl, n)
                results[n] = (code2, text2)
                write_gate_log(gate_dir, n, text2)

        # Merge in queue order; the merge command itself decides refusal vs
        # merge from the gate log this run just wrote, unchanged from today.
        for n in order:
            _, mtext = run_cmd(merge_tpl, n)
            append_log(log_path, mtext)
            append_log(log_path, "LAND-%s-END" % n)
    finally:
        try:
            if os.path.exists(stop_path):
                os.remove(stop_path)
        except FileNotFoundError:
            pass
        release_lock(lock_path, os.getpid())
    return 0


# ------------------------------------------------------------------- CLI --

def cmd_run(args):
    return run(args.queue, args.log, args.gate, args.merge, args.concurrency, args.lock)


def cmd_status(args):
    lock_path = args.lock or (args.queue + ".lock")
    qlen = queue_length(args.queue)
    held = read_lock(lock_path)
    if not held:
        print("land_queue status: no lock holder, queue length %d" % qlen)
        return 0
    pid = held.get("pid")
    alive = pid_alive(pid)
    inflight = held.get("inflight") or []
    print(
        "land_queue status: pid %s (%s), in-flight %s, queue length %d"
        % (pid, "running" if alive else "dead (stale lock)",
           ",".join(str(x) for x in inflight) or "none", qlen)
    )
    return 0


def cmd_stop(args):
    lock_path = args.lock or (args.queue + ".lock")
    held = read_lock(lock_path)
    if not held or not pid_alive(held.get("pid")):
        print("land_queue stop: no live holder to stop")
        return 1
    with open(lock_path + ".stop", "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    print("land_queue stop: asked pid %s to finish its current gate and exit" % held.get("pid"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="land_queue: one pid-locked consumer for the hub PR landing "
                     "queue, a gate concurrency ceiling, and a timeout class that "
                     "re-gates alone before it can refuse a PR (row M6)."
    )
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="drain the queue: gate, classify, merge")
    r.add_argument("--queue", required=True)
    r.add_argument("--log", required=True)
    r.add_argument("--gate", required=True, help='shell command template, "{n}" is the PR number')
    r.add_argument("--merge", required=True, help='shell command template, "{n}" is the PR number')
    r.add_argument("--concurrency", type=int, default=1)
    r.add_argument("--lock", default=None, help="default: <queue>.lock")

    s = sub.add_parser("status", help="lock holder, in-flight gates, queue length")
    s.add_argument("--queue", required=True)
    s.add_argument("--lock", default=None)

    p = sub.add_parser("stop", help="ask the holder to finish its current gate and exit")
    p.add_argument("--queue", required=True)
    p.add_argument("--lock", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
