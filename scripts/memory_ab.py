#!/usr/bin/env python3
"""Memory ON versus OFF: the same tasks, run both ways, differences recorded.

WHY THIS EXISTS. Benchmark row D03, and the directive's P0-10: every memory
product claims it helps and almost none can show it. The estate's strongest
prior evidence is a 56-match recall result, which proves memory SURFACED and
says nothing about whether it HELPED. This harness is the instrument that can
say the second thing, and until it runs, D03 and D04 stay NO-DATA on the
benchmark rather than quietly assumed.

WHAT IT IS, mechanically. A task set is DATA (a JSON file), a runner is a
COMMAND, and the harness's whole job is bookkeeping: run every task twice, once
with BM_MEMORY=off and once with BM_MEMORY=on, score each output with the
task's own check command, and record the six measures the directive names. The
model invocation lives inside the runner, outside this file, so the harness is
deterministic and testable without spending a token.

THE RUNNER CONTRACT. argv from --runner, run once per task per mode:
  stdin   the task's own JSON object
  env     BM_MEMORY=on|off  (the only thing that differs between the passes)
  stdout  a JSON object; "output" (str) is required, "tokens_out" (int) optional
  exit 0  the task ran; nonzero is recorded as a failed run, never hidden

THE CHECK CONTRACT. Each task carries "check": an argv list. It receives the
runner's "output" on stdin; exit 0 means the task SUCCEEDED. The check is the
task's own bar, so the harness never interprets output itself.

THE SIX MEASURES, and where each honestly comes from:
  task success        the check's exit code, mechanical
  time                wall duration of the runner call, mechanical
  tokens/cost         the runner's tokens_out, NO-DATA when it reports none
  repeat mistakes     NO-DATA until a mistake ledger is wired to compare against
  unsupported claims  NO-DATA until a claim verifier is wired
  human corrections   NO-DATA, requires a human pass by definition
A measure that cannot be taken is the string "NO-DATA: <reason>", never zero,
because zero is a measurement and absence is not.

RUN ORDER IS FIXED AND STATED: for each task, OFF then ON. Any warm cache
therefore favours the ON pass; a lift smaller than the warming effect is not
distinguishable by this harness, and the report says so rather than burying it.

Exit 0 the harness ran every task both ways. Exit 2 NO-DATA, inputs unreadable.
Task failures live in the rows, not the exit code: this measures, it does not
gate. Python 3.9 floor, standard library only.

origin: a human running this script's own CLI directly, `python3
scripts/memory_ab.py run --tasks ... --runner ... --out ...` (see main(), the
"run" subcommand, below). Nothing else in this repo imports memory_ab or
calls its run()/main() to produce this file: grep -rl memory_ab scripts
bundle/runtime finds only this module's own test (test_memory_ab.py) and two
downstream readers, make_benchmark_bundle.py (globs for memory_ab's own
output files by filename pattern) and vault_benchmark_v2.py (names
memory_ab.py in a list of known module names), neither of which calls into
this module to create the file.

PRODUCER: this module is the sole producer of the rows file named by --out.
The write happens at `with open(args.out, "w", encoding="utf-8") as fh:
json.dump({"rows": rows}, fh, indent=2)` inside main()'s "run" branch, a few
lines below the call to run().
"""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

NODATA_TOKENS = "NO-DATA: the runner reported no tokens_out"
NODATA_REPEAT = "NO-DATA: no mistake ledger is wired to compare against"
NODATA_CLAIMS = "NO-DATA: no claim verifier is wired"
NODATA_HUMAN = "NO-DATA: requires a human pass"


def _run_once(task, runner_argv, memory_on, timeout_s):
    """One task, one mode. Returns the row dict; never raises for a task-level
    failure, because one broken task must not cost the other measurements."""
    env = dict(os.environ)
    env["BM_MEMORY"] = "on" if memory_on else "off"
    row = {
        "task_id": task["id"],
        "memory": "on" if memory_on else "off",
        "success": False,
        "duration_s": None,
        "tokens_out": NODATA_TOKENS,
        "repeat_mistakes": NODATA_REPEAT,
        "unsupported_claims": NODATA_CLAIMS,
        "human_corrections": NODATA_HUMAN,
        "error": None,
    }
    t0 = time.monotonic()
    try:
        proc = subprocess.run(runner_argv, input=json.dumps(task).encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        row["duration_s"] = round(time.monotonic() - t0, 3)
        row["error"] = "runner timed out at %ds" % timeout_s
        return row
    except OSError as exc:
        row["error"] = "runner could not start: %s" % exc
        return row
    row["duration_s"] = round(time.monotonic() - t0, 3)
    if proc.returncode != 0:
        row["error"] = ("runner exited %d: %s"
                        % (proc.returncode, proc.stderr.decode("utf-8", "replace")[:300]))
        return row
    try:
        result = json.loads(proc.stdout.decode("utf-8", "replace"))
        output = result["output"]
    except (ValueError, KeyError, TypeError) as exc:
        row["error"] = "runner printed no usable JSON with an output field: %r" % exc
        return row
    row["output_sha"] = hashlib.sha256(output.encode("utf-8")).hexdigest()[:16]
    if isinstance(result.get("tokens_out"), int):
        row["tokens_out"] = result["tokens_out"]
    try:
        check = subprocess.run(task["check"], input=output.encode("utf-8"),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout_s)
        row["success"] = check.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        row["error"] = "check could not run: %s" % exc
    return row


def run(tasks, runner_argv, timeout_s=300):
    """Every task, OFF then ON. The order is part of the record."""
    rows = []
    for task in tasks:
        rows.append(_run_once(task, runner_argv, False, timeout_s))
        rows.append(_run_once(task, runner_argv, True, timeout_s))
    return rows


def pair(rows):
    """{task_id: {"off": row, "on": row}}, refusing a task missing either half:
    a one-sided pair is not a comparison, and averaging around it would be."""
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task_id"], {})[r["memory"]] = r
    return {tid: modes for tid, modes in by_task.items()
            if "on" in modes and "off" in modes}


def report(rows, out=sys.stdout):
    """The comparison, with NO-DATA propagated rather than coerced. Returns the
    summary dict so tests read structure instead of scraping prose."""
    pairs = pair(rows)
    gained = [t for t, m in pairs.items() if m["on"]["success"] and not m["off"]["success"]]
    lost = [t for t, m in pairs.items() if m["off"]["success"] and not m["on"]["success"]]
    same = [t for t, m in pairs.items() if m["on"]["success"] == m["off"]["success"]]
    tok_deltas = []
    for t, m in pairs.items():
        a, b = m["off"]["tokens_out"], m["on"]["tokens_out"]
        if isinstance(a, int) and isinstance(b, int):
            tok_deltas.append(b - a)
    print("memory ON versus OFF over %d paired task(s)" % len(pairs), file=out)
    print("  succeeded only with memory ON : %d  %s" % (len(gained), sorted(gained)), file=out)
    print("  succeeded only with memory OFF: %d  %s" % (len(lost), sorted(lost)), file=out)
    print("  no difference in success      : %d" % len(same), file=out)
    if tok_deltas:
        print("  token delta (on minus off)    : %+d total over %d task(s)"
              % (sum(tok_deltas), len(tok_deltas)), file=out)
    else:
        print("  token delta                   : NO-DATA, no pair reported tokens", file=out)
    print("  repeat mistakes / unsupported claims / human corrections: NO-DATA, "
          "not yet wired; absence is stated, never counted as zero", file=out)
    if not gained and not lost:
        print("  VERDICT: no measured difference. This is a real answer, and it is "
              "not evidence FOR memory.", file=out)
    print("  caveat: each task ran OFF then ON in that fixed order, so any warm "
          "cache favours ON; a lift smaller than that warming is invisible here.", file=out)
    return {"pairs": len(pairs), "gained": sorted(gained), "lost": sorted(lost),
            "same": len(same), "token_delta": (sum(tok_deltas) if tok_deltas else None)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("run", "report"))
    ap.add_argument("--tasks", help="JSON file: {\"tasks\": [...]}")
    ap.add_argument("--runner", help="the runner command, shell-quoted")
    ap.add_argument("--out", help="where run writes its rows JSON")
    ap.add_argument("--rows", help="rows JSON for report")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)
    if args.command == "run":
        if not (args.tasks and args.runner and args.out):
            print("memory_ab: run needs --tasks, --runner and --out", file=sys.stderr)
            return 2
        try:
            with open(args.tasks, encoding="utf-8") as fh:
                tasks = json.load(fh)["tasks"]
        except (OSError, ValueError, KeyError) as exc:
            print("memory_ab: NO-DATA, cannot read tasks: %s" % exc, file=sys.stderr)
            return 2
        rows = run(tasks, shlex.split(args.runner), args.timeout)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows}, fh, indent=2)
        print("ran %d task(s) both ways; rows at %s" % (len(tasks), args.out))
        return 0
    if not args.rows:
        print("memory_ab: report needs --rows", file=sys.stderr)
        return 2
    try:
        with open(args.rows, encoding="utf-8") as fh:
            rows = json.load(fh)["rows"]
    except (OSError, ValueError, KeyError) as exc:
        print("memory_ab: NO-DATA, cannot read rows: %s" % exc, file=sys.stderr)
        return 2
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
