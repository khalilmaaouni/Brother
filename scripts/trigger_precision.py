#!/usr/bin/env python3
"""TRIGGER-05: measure how often each repeat-guard lesson fires, and silence
the ones that fire too often to be precise.

WHAT THIS MEASURES, stated plainly: a FIRE RATE, not precision. Precision
needs every firing labelled right or wrong, and no such labels exist. The
proxy is still decisive in one direction: a lesson about a rare mistake that
fires on more than 5 percent of real tool calls is firing on correct work, so
it cannot meet the Codex Q2 bar (at most 5 percent false interventions). A
lesson under the bar is NOT thereby proven precise; it is unrefuted.

Matching reuses the live hook's own matching_lessons(), so this tool can
never disagree with what the guard actually does.

Failure direction: an unreadable transcript is skipped and counted; zero
replayed calls is NO-DATA (exit 2), never a clean bill. --apply writes only
by atomic replace, and only ever ADDS a "silent" field; it never deletes a
lesson or un-silences one (requalifying is a deliberate hand edit).

Usage:
  python3 scripts/trigger_precision.py [--limit 30] [--bar 0.05] [--apply]
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
import time

HOOK = os.path.expanduser("~/.claude/hooks/repeat_guard.py")
TRANSCRIPTS = os.path.expanduser("~/.claude/projects/*/*.jsonl")
MIN_CALLS = 200  # below this a rate is noise, reported as NO-DATA per lesson


def load_guard(path=None):
    spec = importlib.util.spec_from_file_location("repeat_guard", path or HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lesson_key(rec):
    return (str(rec.get("trigger")), str(rec.get("note"))[:120])


def tool_texts(path):
    """The same text the guard matches: a Bash command, or a Write/Edit's
    path plus its first 8000 characters of content."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"tool_use"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            content = (ev.get("message") or {}).get("content") if isinstance(ev, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                name = b.get("name")
                if name == "Bash":
                    yield str(inp.get("command", ""))
                elif name in ("Edit", "Write", "NotebookEdit"):
                    body = str(inp.get("content") or inp.get("new_string") or "")[:8000]
                    yield "%s %s\n%s" % (name, inp.get("file_path", ""), body)


def measure(guard, paths):
    fires, calls, skipped, silent = {}, 0, 0, set()
    for p in paths:
        try:
            for text in tool_texts(p):
                calls += 1
                for rec in guard.matching_lessons(text):
                    k = lesson_key(rec)
                    fires[k] = fires.get(k, 0) + 1
                    if rec.get("silent"):
                        silent.add(k)
        except OSError:
            skipped += 1
    return fires, calls, skipped, silent


def apply_silence(lessons_path, flagged, stamp):
    with open(lessons_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out, changed = [], 0
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            out.append(line)  # never drop a line this tool cannot read
            continue
        k = lesson_key(rec)
        if k in flagged and not rec.get("silent"):
            rec["silent"] = "%s fire rate %.1f%% over the bar" % (stamp, 100 * flagged[k])
            changed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    tmp = lessons_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, lessons_path)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--bar", type=float, default=0.05)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hook", default=HOOK)
    ap.add_argument("--transcripts", default=TRANSCRIPTS)
    a = ap.parse_args(argv)
    guard = load_guard(a.hook)
    paths = sorted(glob.glob(a.transcripts), key=os.path.getmtime, reverse=True)[:a.limit]
    fires, calls, skipped, silent = measure(guard, paths)
    print("trigger-precision: %d transcripts, %d unreadable, %d tool calls replayed"
          % (len(paths), skipped, calls))
    if calls == 0:
        print("trigger-precision: NO-DATA, nothing replayed")
        return 2
    if calls < MIN_CALLS:
        print("trigger-precision: NO-DATA, %d calls is under the %d needed for a rate"
              % (calls, MIN_CALLS))
        return 2
    flagged = {}
    for k, n in sorted(fires.items(), key=lambda kv: -kv[1]):
        rate = n / calls
        over = rate > a.bar and k not in silent
        if over:
            flagged[k] = rate
        label = "SILENT" if k in silent else ("OVER" if over else "ok")
        print("%-6s %5d fires %5.1f%%  %r" % (label, n, 100 * rate, k[0]))
    print("trigger-precision: %d delivered lesson(s) over the %.0f%% bar, %d already silent "
          "(fire-rate proxy, not labelled precision)" % (len(flagged), 100 * a.bar, len(silent)))
    if a.apply and flagged:
        n = apply_silence(str(guard.LESSONS), flagged, time.strftime("%Y-%m-%d"))
        print("trigger-precision: silenced %d lesson(s) in %s" % (n, guard.LESSONS))
    return 1 if flagged and not a.apply else 0


if __name__ == "__main__":
    sys.exit(main())
