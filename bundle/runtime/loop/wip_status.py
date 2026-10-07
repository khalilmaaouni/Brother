#!/usr/bin/env python3
"""Work in progress against the finish-first limits (ACC3): finish the oldest work before starting new work.

WHY. On 2026-09-26 the estate held 50 open pull requests with a median age of 13.5 days, and the loop line ran
more than a hundred commits ahead of main while new lanes kept opening. Each new unit made every older one harder
to land. The limit is only a control when one reader answers it the same way every time, so admission (the loop's
runner pool, later) and the status answer can both ask it.

THE LIMITS (ACC3 row of docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json):
  open-prs              open, non-draft pull requests      at most 8
  oldest-idle-pr        hours since the oldest open non-draft PR was updated   at most 24
  loop-line-ahead       commits the loop line is ahead of main at most 5, and its oldest such commit at most 24 h old
  interactive-sessions  Claude Code sessions a person drives (headless -p runs and the desktop shell excluded)  at most 4
  heavy-jobs            heavy_slot slots held right now    at most 4

VERDICTS, one row each: PASS within, OVER past the limit, NO-DATA when a reading cannot be taken (never a pass).
Exit 0 every row PASS; 1 any row OVER (an OVER wins over a NO-DATA: the estate is known to be over); 2 otherwise
when a row is NO-DATA. Nothing is written; no network except the one read-only `gh pr list`.

usage: python3 scripts/loop/wip_status.py [--loop-ref REF --main-ref REF] [--repo OWNER/NAME]
Without --loop-ref the loop-line row is NO-DATA: which branch is the loop line is the caller's to say.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LIMITS = {"open-prs": 8, "oldest-idle-pr": 24.0, "loop-line-ahead": 5, "loop-line-age": 24.0,
          "interactive-sessions": 4, "heavy-jobs": 4}
HOUR = 3600.0


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(text):
    return datetime.datetime.strptime(text.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp()


def parse_prs(text):
    """gh pr list --json number,isDraft,updatedAt output to a list of dicts; ValueError when it is not that."""
    data = json.loads(text)
    if not isinstance(data, list) or not all(isinstance(p, dict) and "updatedAt" in p for p in data):
        raise ValueError("gh pr list did not return a list of pull requests")
    return data


def count_sessions(procs):
    """Interactive Claude Code sessions among (executable path, full command) pairs: the executable's basename is
    exactly 'claude', and the command carries neither -p nor --print. The executable comes from ps's comm column,
    never from splitting the command line: the real CLI lives under "Application Support", and that space made a
    split read 0 sessions while one was live (2026-09-27). The desktop app's own 'Claude' process is not counted."""
    n = 0
    for exe, args in procs:
        if os.path.basename(exe.rstrip()) != "claude":
            continue
        tail = args[len(exe):].split() if args.startswith(exe) else args.split()[1:]
        if "-p" in tail or "--print" in tail:
            continue
        n += 1
    return n


def repo_from_url(url):
    """OWNER/NAME from a GitHub remote URL (https or ssh form), or None."""
    m = re.match(r"^(?:https?://[^/]+/|git@[^:]+:)([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def _reading(fn):
    try:
        return fn(), None
    except Exception as exc:  # sbe: allow-silent every failure becomes a named NO-DATA row below, never a pass
        return None, "%s: %s" % (type(exc).__name__, exc)


def evaluate(prs, ahead, sessions, heavy, now=None):
    """([(row, verdict, detail)], exit code) from four reader callables."""
    now = __import__("time").time() if now is None else now
    rows = []
    open_prs, err = _reading(prs)
    if open_prs is None:
        why = err or "no pull request list"
        rows += [("open-prs", "NO-DATA", why), ("oldest-idle-pr", "NO-DATA", why)]
    else:
        live = [p for p in open_prs if not p.get("isDraft")]
        n = len(live)
        rows.append(("open-prs", "PASS" if n <= LIMITS["open-prs"] else "OVER", "%d open non-draft, limit %d" % (n, LIMITS["open-prs"])))
        if not live:
            rows.append(("oldest-idle-pr", "PASS", "no open non-draft pull request"))
        else:
            oldest = min(live, key=lambda p: epoch(p["updatedAt"]))
            idle = (now - epoch(oldest["updatedAt"])) / HOUR
            rows.append(("oldest-idle-pr", "PASS" if idle <= LIMITS["oldest-idle-pr"] else "OVER",
                         "#%s idle %.1f h, limit %d h" % (oldest.get("number"), idle, LIMITS["oldest-idle-pr"])))
    line, err = _reading(ahead)
    if line is None:
        rows.append(("loop-line-ahead", "NO-DATA", err or "no loop line named"))
    else:
        count, oldest_ts = line
        age = None if oldest_ts is None else (now - oldest_ts) / HOUR
        over = count > LIMITS["loop-line-ahead"] or (count > 0 and age is not None and age > LIMITS["loop-line-age"])
        rows.append(("loop-line-ahead", "OVER" if over else "PASS",
                     "%d commit(s) ahead%s, limits %d commits and %d h" % (
                         count, "" if age is None else ", oldest %.1f h" % age,
                         LIMITS["loop-line-ahead"], LIMITS["loop-line-age"])))
    for name, fn in (("interactive-sessions", sessions), ("heavy-jobs", heavy)):
        value, err = _reading(fn)
        if value is None:
            rows.append((name, "NO-DATA", err or "unreadable"))
        else:
            rows.append((name, "PASS" if value <= LIMITS[name] else "OVER", "%d, limit %d" % (value, LIMITS[name])))
    verdicts = {r[1] for r in rows}
    return rows, (1 if "OVER" in verdicts else 2 if "NO-DATA" in verdicts else 0)


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise OSError("%s exited %d: %s" % (cmd[0], proc.returncode, (proc.stderr or proc.stdout).strip()[:200]))
    return proc.stdout


def read_prs(repo=None):
    cmd = ["gh", "pr", "list", "--state", "open", "--limit", "500", "--json", "number,isDraft,updatedAt"]
    return parse_prs(_run(cmd + (["-R", repo] if repo else [])))


def read_ahead(loop_ref, main_ref):
    if not loop_ref:
        return None
    count = int(_run(["git", "rev-list", "--count", "%s..%s" % (main_ref, loop_ref)]).strip())
    times = _run(["git", "log", "--reverse", "--format=%ct", "%s..%s" % (main_ref, loop_ref)]).split()
    return count, (float(times[0]) if times else None)


def read_sessions():
    comm = dict(line.strip().split(None, 1) for line in _run(["ps", "-axo", "pid=,comm="]).splitlines() if len(line.split(None, 1)) == 2)
    args = dict(line.strip().split(None, 1) for line in _run(["ps", "-axo", "pid=,args="]).splitlines() if len(line.split(None, 1)) == 2)
    return count_sessions([(comm[pid], args.get(pid, comm[pid])) for pid in comm])


def repo_of(main_ref):
    """The repository of record: the GitHub repository behind main_ref's remote (hub/main names remote hub)."""
    remote = main_ref.split("/", 1)[0] if "/" in main_ref else None
    if not remote:
        return None
    return repo_from_url(_run(["git", "remote", "get-url", remote]))


def _heavy_slot():
    """scripts/heavy_slot.py through the code root (model_router.code_root: the frozen candidate in a proof, the
    checkout otherwise), refused unless the module imported is that file. A bare import found nothing: this reader
    lives in scripts/loop, which is all the deployed bin holds, and heavy_slot lives in scripts/, which neither the
    checkout run nor the bin run puts on sys.path, so the heavy-jobs row read NO-DATA in every pass (2026-10-03).
    sys.path is restored exactly afterwards, undoing heavy_slot's own insert of scripts/, so no scripts copy
    outranks a loop copy later."""
    import model_router
    scripts = os.path.join(model_router.code_root(), "scripts")
    wanted = os.path.realpath(os.path.join(scripts, "heavy_slot.py"))
    saved = list(sys.path)
    sys.path.insert(0, scripts)
    try:
        import heavy_slot
    finally:
        sys.path[:] = saved
    if os.path.realpath(getattr(heavy_slot, "__file__", None) or "") != wanted:
        raise ImportError("heavy_slot was imported from %s, not the code root's %s"
                          % (getattr(heavy_slot, "__file__", None), wanted))
    return heavy_slot


def read_heavy():
    """Slots held right now, found by trying each slot's lock without blocking."""
    heavy_slot = _heavy_slot()
    env = dict(os.environ)
    directory, n = heavy_slot.slot_dir(env), heavy_slot.slot_count(env)
    held = 0
    for i in range(n):
        path = os.path.join(directory, "slot-%d" % i)
        if not os.path.exists(path):
            continue
        with open(path, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                held += 1
                continue
            fcntl.flock(fh, fcntl.LOCK_UN)
    return held


def measure(loop_ref: Optional[str], main_ref: str, repo: Optional[str] = None, now: Optional[float] = None) -> Tuple[List[Tuple[str, str, str]], int]:
    if not isinstance(main_ref, str) or not main_ref:
        raise ValueError("main_ref must be a non-empty string")
    if loop_ref is not None:
        if not isinstance(loop_ref, str) or not loop_ref:
            raise ValueError("loop_ref must be None or a non-empty string")
    if repo is not None:
        if not isinstance(repo, str) or not repo:
            raise ValueError("repo must be None or a non-empty string")
    prs = lambda: read_prs(repo or repo_of(main_ref))
    ahead = lambda: read_ahead(loop_ref, main_ref)
    return evaluate(prs=prs, ahead=ahead, sessions=read_sessions, heavy=read_heavy, now=now)


def main(argv=None):
    ap = argparse.ArgumentParser(description="work in progress against the finish-first limits (ACC3)")
    ap.add_argument("--loop-ref", default=None, help="the loop line, for example hub/fix/run-readiness-2026-09-27")
    ap.add_argument("--main-ref", default="origin/main", help="main as this checkout names it (default origin/main)")
    ap.add_argument("--repo", default=None, help="OWNER/NAME for gh (default: the repository behind --main-ref's remote)")
    args = ap.parse_args(argv)
    rows, code = measure(args.loop_ref, args.main_ref, args.repo)
    for name, verdict, detail in rows:
        print("%-21s %-8s %s" % (name, verdict, detail))
    over = sum(1 for r in rows if r[1] == "OVER")
    nodata = sum(1 for r in rows if r[1] == "NO-DATA")
    print("WIP: %d over, %d no-data: %s" % (over, nodata, {0: "within every limit, new work may start",
                                                          1: "OVER: finish the oldest work before starting new work",
                                                          2: "NO-DATA: a limit could not be read, which is not a pass"}[code]))
    return code


if __name__ == "__main__":
    sys.exit(main())
