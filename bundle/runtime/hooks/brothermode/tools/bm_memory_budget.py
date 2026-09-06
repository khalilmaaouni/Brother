#!/usr/bin/env python3
"""bm_memory_budget.py: catches an auto-memory MEMORY.md before it silently truncates.

WHY THIS EXISTS
  Claude's per-project auto-memory index (~/.claude/projects/<project>/memory/MEMORY.md)
  loads at session start under a byte budget of roughly 24.4 KB, measured 2026-08-28
  against a 27,147 byte file that silently dropped its newest lines with no warning
  anywhere. Nothing on this machine watched that ceiling, so a session could add a
  pointer and never learn that the memory now most likely to be cut is its own.

WHAT IT DOES
  Scans every ~/.claude/projects/*/memory/MEMORY.md (or --root), prints each file's
  byte size against --budget and line count against --max-lines (published cap:
  first 200 lines or 25KB, whichever comes first, so either alone can silently
  drop the newest lines), and exits:
    0   every file found is under both budgets
    2   at least one file is over either budget (offenders listed, budget named)
    3   zero MEMORY.md files were found under root, NO-DATA: a scan that saw nothing
        must never report success

Python 3.9, standard library only.
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402

DEFAULT_ROOT = brother_paths.config_path("projects")
DEFAULT_BUDGET = 24000
DEFAULT_MAX_LINES = 200

EXIT_OK = 0
EXIT_OVER_BUDGET = 2
EXIT_NO_DATA = 3


def find_memory_files(root):
    pattern = os.path.join(root, "*", "memory", "MEMORY.md")
    return sorted(glob.glob(pattern))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="directory holding <project>/memory/MEMORY.md (default: %s)"
                        % DEFAULT_ROOT)
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                   help="byte ceiling per file (default: %d)" % DEFAULT_BUDGET)
    p.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES,
                   help="line ceiling per file (default: %d)" % DEFAULT_MAX_LINES)
    args = p.parse_args(argv)

    files = find_memory_files(args.root)
    if not files:
        print("NO-DATA: no MEMORY.md files found under %s" % args.root)
        return EXIT_NO_DATA

    offenders = []
    for f in files:
        try:
            size = os.path.getsize(f)
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = len(fh.readlines())
        except OSError as e:
            print("SKIP %s (%s)" % (f, e))
            continue
        breaches = []
        if size > args.budget:
            breaches.append("bytes")
        if lines > args.max_lines:
            breaches.append("lines")
        verdict = "OVER" if breaches else "OK"
        print("%s %6d bytes %5d lines %s" % (verdict, size, lines, f))
        if breaches:
            offenders.append((f, size, lines, breaches))

    if offenders:
        print("%d file(s) over budget:" % len(offenders))
        for f, size, lines, breaches in offenders:
            detail = []
            if "bytes" in breaches:
                detail.append("%d bytes, %d over" % (size, size - args.budget))
            if "lines" in breaches:
                detail.append("%d lines, %d over" % (lines, lines - args.max_lines))
            print("  %s (%s) [%s]" % (f, "; ".join(detail), ", ".join(breaches)))
        return EXIT_OVER_BUDGET
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
