#!/usr/bin/env python3
"""What in the evidence folder could be pruned, oldest and largest first. DELETES NOTHING unless told to.

usage (any cwd):
  python3 scripts/evidence_retention.py                     report only, the default
  python3 scripts/evidence_retention.py --older-days 7      restrict to runs older than a week
  python3 scripts/evidence_retention.py --delete NAME [...] remove exactly the directories you name

WHY. Every grader run, probe round, hermetic check and release rehearsal writes a directory under the evidence
folder and nothing ever prunes them. Measured 2026-09-21: 15 GB under ~/.claude with 5.6 GB of evidence, on a
data volume 97 percent full, and the disk floor gate refusing builds. Seventeen throwaway export trees had to be
removed by hand.

WHY IT REFUSES TO SWEEP. This estate's standing rule is that work is never deleted unless the owner names the
thing. So this reports, ranks, and deletes only directories named on the command line, one by one, never a
pattern and never everything older than a date. A wildcard sweep is exactly how real work disappears."""
import argparse
import os
import shutil
import sys
import time

ROOT = os.path.expanduser("~/.claude/evidence")
# Directories the loop needs to keep working, whatever their age. Everything else is a candidate to REPORT.
PROTECTED = {"unit-runs", "land-batch", "brother-loop.jsonl", "brother-failures.jsonl", "spec-council.json",
             "spec-scores.json", "brother-writer.lock"}


def size_of(path, walk=None):
    """Bytes under a path. An unreadable entry contributes what it can rather than failing the whole report."""
    total = 0
    for root, _dirs, files in (walk or os.walk)(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def candidates(root=ROOT, older_days=0, now=None, listdir=None, getmtime=None, sizer=None):
    """[(name, bytes, age_days)] for prunable directories, largest first. Protected names are never listed."""
    now = now if now is not None else time.time()
    try:
        names = (listdir or os.listdir)(root)
    except OSError:
        return []
    out = []
    for name in names:
        if name in PROTECTED:
            continue
        p = os.path.join(root, name)
        if not os.path.isdir(p) and listdir is None:
            continue
        try:
            age = (now - (getmtime or os.path.getmtime)(p)) / 86400.0
        except OSError:
            continue
        if age < older_days:
            continue
        out.append((name, (sizer or size_of)(p), age))
    return sorted(out, key=lambda r: -r[1])


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def selftest():
    fake = {"rel-1.0.13": (1_200_000_000, 30.0), "night-2026-09-09": (411_000_000, 12.0),
            "unit-runs": (900_000_000, 0.1), "probe-round": (5_000_000, 0.2)}
    now = 1_000_000.0
    got = candidates(root="/x", older_days=0, now=now, listdir=lambda r: list(fake),
                     getmtime=lambda p: now - fake[os.path.basename(p)][1] * 86400,
                     sizer=lambda p: fake[os.path.basename(p)][0])
    old = candidates(root="/x", older_days=7, now=now, listdir=lambda r: list(fake),
                     getmtime=lambda p: now - fake[os.path.basename(p)][1] * 86400,
                     sizer=lambda p: fake[os.path.basename(p)][0])
    cases = [
        ("the largest candidate comes first", got[0][0] == "rel-1.0.13"),
        ("a protected directory is never a candidate", all(n != "unit-runs" for n, _, _ in got)),
        ("an age filter excludes recent runs", [n for n, _, _ in old] == ["rel-1.0.13", "night-2026-09-09"]),
        ("an unreadable root reports nothing rather than crashing", candidates(root="/no/such/dir") == []),
        ("sizes are reported in human units", human(1_200_000_000).endswith("GB")),
        ("the live loop state is protected", {"unit-runs", "brother-loop.jsonl"} <= PROTECTED),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--older-days", type=float, default=0)
    ap.add_argument("--delete", nargs="*", default=None, help="exact directory names, never a pattern")
    ap.add_argument("--top", type=int, default=15)
    if "--selftest" in (argv or sys.argv[1:]):
        return selftest()
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.delete:
        freed = 0
        for name in args.delete:
            if name in PROTECTED:
                print("REFUSED %s is protected: the loop reads it" % name); continue
            if os.sep in name or name.startswith("."):
                print("REFUSED %s is not a plain directory name in the evidence folder" % name); continue
            p = os.path.join(ROOT, name)
            if not os.path.isdir(p):
                print("SKIP    %s is not a directory here" % name); continue
            n = size_of(p)
            shutil.rmtree(p)
            freed += n
            print("REMOVED %-34s %s" % (name, human(n)))
        print("freed %s" % human(freed))
        return 0
    rows = candidates(older_days=args.older_days)
    if not rows:
        print("NO-DATA: nothing prunable found under %s" % ROOT)
        return 1
    total = sum(b for _, b, _ in rows)
    print("%d prunable director(ies) under %s, %s in total. Nothing was deleted." % (len(rows), ROOT, human(total)))
    for name, b, age in rows[:args.top]:
        print("  %-38s %9s  %4.0f days old" % (name, human(b), age))
    print("\nTo remove some, name them exactly:")
    print("  python3 scripts/evidence_retention.py --delete %s" % " ".join(n for n, _, _ in rows[:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
