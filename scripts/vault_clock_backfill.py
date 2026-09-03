#!/usr/bin/env python3
"""VB4-03: backfill verified_at on vault notes that have never declared it.

WBS done-check: unverified-no-clock (bm_vault_staleness.py's census) drops
below 100, every stamp derived from evidence actually read, never invented;
the remainder carry the "no-derivable-date" marker (see
BrotherModeUp/tools/bm_vault_staleness.py NO_DERIVABLE_DATE) so the census
tells examined apart from unexamined.

EVIDENCE TIERS, in order, first hit wins, no invented dates ever:
  1. the note's own frontmatter `created:` field
  2. a leading YYYY-MM-DD in the note's filename
  3. the note's own git history in the vault repo: earliest commit date
     that touched this path (--follow across renames)
If none of the three yield a parseable date, the note gets
`verified_at: no-derivable-date` instead: examined, nothing found.

SAFETY (the 601-opener lesson): the new line is inserted immediately before
the frontmatter's closing "---" delimiter, so every other byte of the file
(including the exact newline the closing delimiter sits on) is untouched.
Notes with no frontmatter at all are skipped and reported, never patched.
This script never touches git in the vault: read-only `git log` for tier 3,
nothing else. Stdlib only, Python 3.9 floor.

USAGE:
  vault_clock_backfill.py plan  --vault PATH                 (dry run, prints every planned stamp)
  vault_clock_backfill.py apply --vault PATH --start N --end N  (writes one batch, half-open [start,end))
  vault_clock_backfill.py spotcheck --vault PATH --n 10 [--seed N]
"""
import argparse
import datetime
import os
import random
import re
import subprocess
import sys

SKIP_DIRS = {".git", ".trash", ".obsidian"}
TYPE_RE = re.compile(r"^type:\s*(.+)$", re.M)
VERIFIED_RE = re.compile(r"^verified_at:\s*(\S+)\s*$", re.M)
CREATED_RE = re.compile(r"^created:\s*(\S+)\s*$", re.M)
FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[-_]")
EXEMPT_TYPES = {"session-log"}
NO_DERIVABLE_DATE = "no-derivable-date"


def frontmatter(text):
    if not text.startswith("---"):
        return None, None, None
    end = text.find("\n---", 3)
    if end == -1:
        return None, None, None
    return text[3:end], 3, end


def note_type(block):
    m = TYPE_RE.search(block)
    return m.group(1).strip().strip('"').strip("'").lower() if m else ""


def parse_date(raw):
    try:
        return datetime.date.fromisoformat(raw.strip().strip('"').strip("'"))
    except (ValueError, AttributeError):
        return None


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def git_first_commit_date(vault, relpath):
    """Earliest commit date (YYYY-MM-DD) that touched relpath, or None.
    Read-only: log only, never mutates the vault repo."""
    try:
        out = subprocess.run(
            ["git", "-C", vault, "log", "--follow", "--format=%ad", "--date=short",
             "--", relpath],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    if not lines:
        return None
    return parse_date(lines[-1])  # git log is newest-first; last line is earliest


def derive(vault, relpath, block, text):
    """(value, evidence_line) where value is an ISO date string or the
    NO_DERIVABLE_DATE sentinel. evidence_line describes what was read."""
    m = CREATED_RE.search(block)
    if m:
        d = parse_date(m.group(1))
        if d is not None:
            return d.isoformat(), "frontmatter created: %s" % m.group(1).strip()
    fn = os.path.basename(relpath)
    m = FILENAME_DATE_RE.match(fn)
    if m:
        d = parse_date(m.group(1))
        if d is not None:
            return d.isoformat(), "filename date prefix: %s" % fn
    d = git_first_commit_date(vault, relpath)
    if d is not None:
        return d.isoformat(), "git log --follow earliest commit: %s" % d.isoformat()
    return NO_DERIVABLE_DATE, "no created:, no filename date, no git history: examined, undatable"


def candidates(vault):
    """[(relpath, end_idx, text, value, evidence)] for every note that is
    unverified_no_clock today: has frontmatter, is not an exempt type, and
    declares no verified_at at all."""
    out = []
    for path in walk(vault):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        block, _start, end = frontmatter(text)
        relpath = os.path.relpath(path, vault)
        if block is None:
            continue  # no frontmatter: skip, never patched
        if note_type(block) in EXEMPT_TYPES:
            continue
        if VERIFIED_RE.search(block):
            continue  # already has a verified_at (fresh/stale/malformed): leave it
        value, evidence = derive(vault, relpath, block, text)
        out.append((relpath, end, text, value, evidence))
    return out


def cmd_plan(vault):
    rows = candidates(vault)
    stamped = [r for r in rows if r[3] != NO_DERIVABLE_DATE]
    marked = [r for r in rows if r[3] == NO_DERIVABLE_DATE]
    print("vault: %s" % vault)
    print("candidates (unverified_no_clock today, frontmatter present): %d" % len(rows))
    print("would stamp with a derived date: %d" % len(stamped))
    print("would mark no-derivable-date: %d" % len(marked))
    for relpath, _end, _text, value, evidence in rows:
        print("  %s -> %s  [%s]" % (relpath, value, evidence))
    return 0


def apply_one(vault, relpath, end, text, value):
    new_text = text[:end] + "\nverified_at: %s" % value + text[end:]
    path = os.path.join(vault, relpath)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)


def cmd_apply(vault, start, end_idx):
    rows = candidates(vault)
    batch = rows[start:end_idx]
    print("batch [%d:%d) of %d candidates" % (start, end_idx, len(rows)))
    for relpath, end, text, value, evidence in batch:
        apply_one(vault, relpath, end, text, value)
        print("  wrote %s -> %s  [%s]" % (relpath, value, evidence))
    print("batch done: %d notes written" % len(batch))
    return 0


def cmd_spotcheck(vault, n, seed):
    already = []
    for path in walk(vault):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        block, _s, _e = frontmatter(text)
        if block is None:
            continue
        m = VERIFIED_RE.search(block)
        if m:
            already.append((os.path.relpath(path, vault), m.group(1).strip()))
    rng = random.Random(seed)
    sample = rng.sample(already, min(n, len(already)))
    for relpath, value in sample:
        path = os.path.join(vault, relpath)
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        block, _s, _e = frontmatter(text)
        _v, evidence = derive(vault, relpath, block, text)
        print("%s | verified_at: %s | re-derived evidence: %s" % (relpath, value, evidence))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("plan", "apply", "spotcheck"))
    ap.add_argument("--vault", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=1 << 30)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)
    if not os.path.isdir(args.vault):
        print("vault_clock_backfill: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    if args.command == "plan":
        return cmd_plan(args.vault)
    if args.command == "apply":
        return cmd_apply(args.vault, args.start, args.end)
    return cmd_spotcheck(args.vault, args.n, args.seed)


if __name__ == "__main__":
    sys.exit(main())
