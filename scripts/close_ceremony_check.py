#!/usr/bin/env python3
"""The closing ceremony law's enforcer (founder order 2026-08-30 at close).

A session close is FINISHED only when a handover pack exists that another
account's fresh session can start from. The founder's words that night:
"I need the MD file for handover to another session in another account and
the zip file as well as all the learning, mistakes made to avoid, wisdom,
and the brother readiness board html file make this the new closing
ceremony". This script decides that mechanically, so the ceremony cannot
be claimed from memory.

What a valid close pack is, checked here:
  1. The newest pack directory under the handover root contains
     01-START-HERE.md, a board HTML copy, and a session log.
  2. 01-START-HERE.md carries the four load-bearing sections by heading
     keyword: what finished, priorities, wisdom (learnings and mistakes),
     and acceleration.
  3. A zip sits beside the pack directory holding every file the
     directory holds (the one-zip law: never the pack beside its parts
     with files missing from the zip).
  4. EVERY pack under the handover root is clean of every private term
     (readiness row E35): file and directory names, every scanned text
     file's content, and every zip archive's member names and text
     members, not only the newest pack's markdown. Reuses
     scripts/handover_pack_scan.py's scanner by import, never a second
     copy of the matching rule.
  5. The pack is FRESH: newer than --max-age-hours (default 24), so a
     stale pack from last week cannot green a new close.

Exit codes, this estate's convention: 0 the newest pack satisfies the law,
1 it does not (each failure named), 2 NO-DATA (no pack root or no pack,
named, never a pass).
"""
import argparse
import os
import re
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handover_pack_scan as hps  # noqa: E402

ROOT = os.path.expanduser("~/Documents/BrotherModeUp-handovers")

SECTIONS = {
    "finished": r"(?im)^#+ .*(finished|delivered|what you are walking into)",
    "priorities": r"(?im)^#+ .*(priorit|order)",
    "wisdom": r"(?im)^#+ .*(wisdom|learning|mistake|lesson)",
    "acceleration": r"(?im)^#+ .*(acceler|first (fifteen|15) minutes|start here)",
}


def newest_pack(root):
    dirs = [os.path.join(root, d) for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))]
    return max(dirs, key=os.path.getmtime) if dirs else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--max-age-hours", type=float, default=24.0)
    ap.add_argument("--terms", default=hps.TERMS_FILE,
                    help="private terms list (default: %s)" % hps.TERMS_FILE)
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print("NO-DATA: handover root %s does not exist" % args.root)
        return 2
    pack = newest_pack(args.root)
    if pack is None:
        print("NO-DATA: no pack directory under %s" % args.root)
        return 2

    problems = []
    age_h = (time.time() - os.path.getmtime(pack)) / 3600.0
    if age_h > args.max_age_hours:
        problems.append("pack %s is %.1f hours old, over the %.0f hour "
                        "freshness bar: a stale pack cannot green a new close"
                        % (os.path.basename(pack), age_h, args.max_age_hours))

    files = sorted(os.listdir(pack))
    start = next((f for f in files if f.upper().startswith("01-START-HERE")), None)
    if start is None:
        problems.append("no 01-START-HERE.md in the pack")
    if not any(f.lower().endswith(".html") and "board" in f.lower() for f in files):
        problems.append("no readiness board HTML copy in the pack")
    if not any("session-log" in f.lower() or "session_log" in f.lower()
               or re.search(r"session.?log", f, re.I) for f in files):
        problems.append("no session log in the pack")

    if start:
        with open(os.path.join(pack, start), encoding="utf-8") as fh:
            text = fh.read()
        for name, pat in SECTIONS.items():
            if not re.search(pat, text):
                problems.append("START-HERE lacks a %s section" % name)

    # Terms cleanliness (readiness row E35): every pack under the root, not
    # only the newest pack's markdown. A pack is handed to a session on
    # ANOTHER ACCOUNT, so a private term anywhere in it (a file name, a
    # directory name, a scanned text file's content, or a zip member) is a
    # term leaving the machine. The 2026-09-03 audit found 24 such files
    # sitting in OLDER packs the old newest-pack-only check never looked at.
    # Reuses handover_pack_scan's scanner by import, never a second copy of
    # the matching rule, so the standalone dry run and this gate agree.
    hits, short_patterns, long_patterns, _stats, no_data_reason = hps.run_scan(
        args.root, args.terms)
    if hits is None:
        problems.append("private terms list unreadable, could not check any "
                        "pack (%s): a stop, not a pass" % no_data_reason)
    elif hits:
        by_pack = {}
        for _kind, relpath, _n in hits:
            # A zip-member hit's relpath is "<zip path>::<member path>", and
            # a member can itself sit inside an internal folder ("pack.zip::
            # inner/file.md"): strip the "::member" suffix BEFORE splitting
            # on os.sep, so grouping always lands on the zip (or the plain
            # file), never one level inside a zip's own internal structure.
            zip_relpath = relpath.split("::", 1)[0]
            pack_name = zip_relpath.split(os.sep, 1)[0]
            by_pack[pack_name] = by_pack.get(pack_name, 0) + 1
        for pack_name in sorted(by_pack):
            # The pack NAME itself can be the offender (the audit's file-name
            # case), so it is masked the same way handover_pack_scan.py masks
            # every path it prints, never trusted to be clean just because it
            # is being used as a label here rather than a scanned path.
            masked_name = hps.mask_path(pack_name, short_patterns, long_patterns)
            problems.append(
                "pack %s carries %d private-term hit(s) (value not "
                "printed; run scripts/handover_pack_scan.py for detail)"
                % (masked_name, by_pack[pack_name]))

    zip_path = pack + ".zip"
    if not os.path.isfile(zip_path):
        problems.append("no zip beside the pack (%s missing)" % zip_path)
    else:
        with zipfile.ZipFile(zip_path) as zf:
            inside = {os.path.basename(n) for n in zf.namelist()
                      if not n.endswith("/")}
        missing = [f for f in files if f not in inside]
        if missing:
            problems.append("zip is missing pack files: %s" % ", ".join(missing))

    if problems:
        print("FAIL: the newest close pack does not satisfy the ceremony law")
        for p in problems:
            print("  - " + p)
        return 1
    print("PASS: pack %s satisfies the closing ceremony law "
          "(start-here with all four sections, board HTML, session log, "
          "complete zip, %.1f hours old, terms clean)"
          % (os.path.basename(pack), age_h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
