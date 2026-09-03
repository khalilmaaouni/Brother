#!/usr/bin/env python3
"""Run every task's OWN verifyCommand and produce real verdicts.

THE DEFECT THIS EXISTS FOR, measured 2026-08-26 across a 166 task estate:

    tasks                              166
    declaring a verifyCommand          166
    commands that are actually runnable 152
    CARRYING AN EVIDENCE RECEIPT          0
    closed                              140, of which 92 marked forced

Every task declared how it could be checked. Nothing ever ran the check. A
register where 100 percent of rows name a proof and 0 percent hold one is not a
register of verified work, it is a register of intentions, and closing 140 of
them (92 by force) did not make any of them true.

WHAT THIS DOES. Runs each task's declared command, captures the command's OWN
exit code, and writes a verdict per task. It never edits the task file: producing
a receipt is a separate act from closing a row, and this tool deliberately cannot
close anything.

SAFETY, because these commands come from a DATA FILE. Nothing runs unless it
matches the read-only allowlist below. A command that writes, deletes, pushes,
merges or installs is REFUSED and reported as such, never executed. That is not
caution theatre: a register is exactly where a destructive string would hide.

THREE VERDICTS, and the third is the one most tools get wrong:

    PASS     the command ran and exited 0
    FAIL     the command ran and exited non-zero
    NO-DATA  it could not be run, or its exit code carries no verdict

NO-DATA IS NEVER A PASS. It covers a refused command, a prose "command", a
timeout (which measures the machine, not the task), and the always-exit-zero
shapes (git status, git log, git ls-files, git ls-remote, git branch) whose
condition lives in their OUTPUT rather than their code.

    python3 scripts/verify_task_estate.py <path-to-tasks.json> [--limit N]

Exit 0 when every runnable check passed or was NO-DATA, 1 if any FAILED.
"""
import json
import pathlib
import re
import shlex
import subprocess
import sys

ALLOW = (
    re.compile(r"^git (status|log|ls-files|ls-remote|branch|rev-parse|cat-file|diff|show|describe)\b"),
    re.compile(r"^gh (pr view|pr list|api|repo view|release list)\b"),
    re.compile(r"^(python3|/usr/bin/python3) -m unittest\b"),
    re.compile(r"^(python3|/usr/bin/python3) (tools|scripts|evals)/[A-Za-z0-9_./-]+\.py\b"),
    # `bin/sbe` with a READ-ONLY verb only. Enumerated from the estate rather
    # than assumed: the verbs actually used are task list, task check, doctor
    # and book --check. open, close, force-close, delete, set and add stay
    # REFUSED, because a register is exactly where a writing command would hide.
    re.compile(r"^(python3|/usr/bin/python3) [A-Za-z0-9_./-]*bin/sbe (task (list|check)|doctor|book)\b"),
    re.compile(r"^(sh|bash) (tools|scripts)/[A-Za-z0-9_./-]+\.sh\b"),
    re.compile(r"^test -[edfsr]\b"),
    re.compile(r"^ls\b"),
    re.compile(r"^grep\b"),
)
# Their exit code is 0 whatever they print, so the condition is in the OUTPUT.
ALWAYS_ZERO = ("git status", "git log", "git ls-files", "git ls-remote", "git branch")
# A span carrying any of these is a sentence or a pipeline, not a lone command.
UNRUNNABLE = ("|", ">", "<", "&&", "||", "$(", "`", ";")


def classify(cmd):
    cmd = (cmd or "").strip()
    if not cmd:
        return "no-command", None
    if any(ch in cmd for ch in UNRUNNABLE):
        return "needs-shell", None
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return "unparseable", None
    if not parts or len(parts) > 14:
        return "unparseable", None
    if not any(p.match(cmd) for p in ALLOW):
        return "not-read-only", None
    if cmd.startswith(ALWAYS_ZERO):
        return "always-zero", parts
    return "runnable", parts


def main():
    if len(sys.argv) < 2:
        print("usage: verify_task_estate.py <tasks.json> [--limit N]")
        return 2
    src = pathlib.Path(sys.argv[1])
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    root = src.parent.parent if src.parent.name == ".sbe" else src.parent

    with src.open(encoding="utf-8") as fh:
        d = json.load(fh)
    tasks = d if isinstance(d, list) else (d.get("tasks") or d)
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    tasks = [t for t in tasks if isinstance(t, dict)]
    if limit:
        tasks = tasks[:limit]

    tally = {"PASS": 0, "FAIL": 0, "NO-DATA": 0}
    reasons = {}
    rows = []
    for t in tasks:
        tid = str(t.get("id", "?"))[:26]
        status = str(t.get("status", "?"))
        cmd = str(t.get("verifyCommand", ""))
        kind, parts = classify(cmd)
        if kind != "runnable" and kind != "always-zero":
            tally["NO-DATA"] += 1
            reasons[kind] = reasons.get(kind, 0) + 1
            rows.append((tid, status, "NO-DATA", kind, ""))
            continue
        if kind == "always-zero":
            tally["NO-DATA"] += 1
            reasons["always-zero"] = reasons.get("always-zero", 0) + 1
            rows.append((tid, status, "NO-DATA", "exit code carries no verdict", ""))
            continue
        try:
            p = subprocess.run(parts, capture_output=True, text=True, timeout=60, cwd=str(root))
            code = p.returncode
            first = (p.stdout or p.stderr or "").strip().splitlines()[:1]
            detail = first[0][:56] if first else ""
        except subprocess.TimeoutExpired:
            tally["NO-DATA"] += 1
            reasons["timeout"] = reasons.get("timeout", 0) + 1
            rows.append((tid, status, "NO-DATA", "timed out: measures the machine, not the task", ""))
            continue
        except Exception as exc:
            tally["NO-DATA"] += 1
            reasons["could-not-run"] = reasons.get("could-not-run", 0) + 1
            rows.append((tid, status, "NO-DATA", type(exc).__name__, ""))
            continue
        verdict = "PASS" if code == 0 else "FAIL"
        tally[verdict] += 1
        rows.append((tid, status, verdict, "exit %d" % code, detail))

    print("%-28s %-7s %-8s %s" % ("TASK", "STATUS", "VERDICT", "DETAIL"))
    for r in rows:
        print("%-28s %-7s %-8s %s %s" % (r[0], r[1][:7], r[2], r[3], r[4]))
    print()
    print("tasks %d   PASS %d   FAIL %d   NO-DATA %d"
          % (len(rows), tally["PASS"], tally["FAIL"], tally["NO-DATA"]))
    if reasons:
        print("NO-DATA reasons: " + ", ".join("%s=%d" % kv for kv in sorted(reasons.items())))
    print("NO-DATA IS NEVER A PASS. A refused command is refused, not satisfied.")
    print("This tool produces VERDICTS. It does not close rows: closing is a separate act.")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
