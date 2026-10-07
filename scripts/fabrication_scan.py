#!/usr/bin/env python3
"""LAW 0: an artifact may not assert what is not there. This is the check behind it.

usage (repo root):
  python3 -B scripts/fabrication_scan.py <file ...>        scan named artifacts
  python3 -B scripts/fabrication_scan.py --selftest
Exit 0 clean, 1 when a fabrication signal is found, 2 when an artifact cannot be read (NO-DATA, never a pass).

WHY IT EXISTS, and the measurement that produced it. On 2026-09-21 the one system wiring audit, an artifact the
estate had been reading as evidence, was measured to be mostly invention: 35 of its 68 findings named paths that
have NEVER existed in ANY branch. Proved per path with `git log --all --oneline -- <path>` returning 0 commits,
against a control on a real module returning 5, because a sweep returning zero everywhere is an extractor defect
until a control says otherwise. 22 of those rows carried a confident ORPHAN verdict about code that was never
written. The names ran in synthetic sequences: facade_1 to facade_13, helper_1 to helper_4, assurance_1 to
assurance_6, mdm_1_facade to mdm_5_facade. Its own frontmatter carried a commit_sha of forty zeroes and a
midnight generated_at, and claimed 68 non test and 55 test files where the tree held 126 and 126.

NOTHING CAUGHT ANY OF IT. The unit's done check asked only whether each row was FILLED IN, and 53 spec tests
passed on the document throughout. A check whose verdict is not a property of the thing being judged is how a
fabricated artifact ships with a green tick.

THE FOUR SIGNALS, each one taken from that real artifact rather than imagined:
  PATH      a backticked file path that exists nowhere in the tree, by any basename
  SEQUENCE  three or more names differing only by an incrementing integer, the shape a model emits when it is
            filling a quota rather than reading a tree
  ZEROSHA   a commit sha of all zeroes, or a provenance timestamp at exact midnight
  COUNT     a stated count of files in a directory that disagrees with the real count

WHAT IT IS NOT. It cannot prove an artifact TRUE; it can only catch the cheap lies. A clean scan means no signal
fired, never "this document is honest", and it is reported in exactly those words. Treating a clean scan as a
certificate would recreate the error it exists to catch."""
import argparse
import collections
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Backticked paths AND bare paths in markdown table cells. The table form is not an afterthought: the
# audit that motivated this check carried all 35 of its fabricated findings as TABLE CELLS, so a scanner
# reading only backticks would have found one stray path and missed every row that mattered. Measured
# when this was written: backticks alone caught 1 signal in that file, table cells caught the rest.
PATH_RE = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|sh|json|md|yml|yaml|txt))`")
CELL_RE = re.compile(r"(?:^|[|\s])([A-Za-z0-9_][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*\.(?:py|sh|json|md|yml|yaml|txt))(?=$|[|\s,;)])", re.M)
SEQ_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*?)(\d{1,3})\b")
ZERO_SHA = re.compile(r"\b0{32,40}\b")
MIDNIGHT = re.compile(r"T00:00:00Z?(?![0-9])")


def tracked(root=ROOT):
    """Every tracked path, plus a basename index, so a bare basename is imprecise rather than fabricated.
    Raises on a git failure: an unreadable index must never read as an empty tree, which would flag everything."""
    r = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "git ls-files failed")
    paths = set(r.stdout.split())
    if not paths:
        raise OSError("the index lists no files, which is not a real tree")
    return paths, {os.path.basename(p) for p in paths}


def bad_paths(text, paths, basenames):
    """Paths named in backticks that resolve nowhere. A bare basename that matches a real file is NOT reported:
    it is imprecise, and reporting it would bury the real signal under style complaints."""
    out = []
    named = list(dict.fromkeys(PATH_RE.findall(text) + CELL_RE.findall(text)))
    for m in named:
        if m in paths or os.path.basename(m) in basenames or os.path.exists(os.path.join(ROOT, m)):
            continue
        # A PATH DECLARED AS NOT YET BUILT IS HONEST, and flagging it is how a scanner teaches people to ignore
        # it. Real example from the regenerated wiring audit: "`scripts/audit_one_system_wiring.sh` NEW:
        # follow-on automation, not built here." That sentence is the opposite of a fabrication: it says plainly
        # that the file does not exist. The claim this check exists to catch is a path asserted AS PRESENT.
        for hit in re.finditer(re.escape(m), text):
            around = text[max(0, hit.start() - 60):hit.end() + 120]
            if re.search(r"\bNEW\b|not built|does not exist|to be (built|written|created)|planned|follow.on",
                         around, re.I):
                break
        else:
            out.append(m)
    return out


def sequences(names, floor=3):
    """Names differing only by an incrementing integer, three or more of them. This is the shape of a quota being
    filled. Two is a coincidence (v1 and v2 are ordinary); three or more in one artifact is a pattern."""
    groups = collections.defaultdict(set)
    for n in names:
        m = SEQ_RE.fullmatch(n.replace(".py", "").replace(".md", ""))
        if m:
            groups[m.group(1)].add(int(m.group(2)))
    return {stem: sorted(v) for stem, v in groups.items() if len(v) >= floor}


def scan_text(text, paths, basenames):
    """[(signal, detail)] for one artifact's text."""
    found = []
    missing = bad_paths(text, paths, basenames)
    for p in missing:
        found.append(("PATH", "%s is named but exists nowhere in the tree" % p))
    seqs = sequences([os.path.basename(p) for p in missing])
    for stem, nums in seqs.items():
        found.append(("SEQUENCE", "%s%s: %d names differing only by a number, all of them absent"
                      % (stem, nums, len(nums))))
    if ZERO_SHA.search(text):
        found.append(("ZEROSHA", "a commit sha of all zeroes stands in for real provenance"))
    if MIDNIGHT.search(text):
        found.append(("ZEROSHA", "a provenance timestamp at exact midnight is a placeholder, not a clock read"))
    return found


def selftest():
    paths = {"scripts/real_one.py", "scripts/real_two.py"}
    names = {"real_one.py", "real_two.py"}
    honest = "See `scripts/real_one.py` and `scripts/real_two.py`, generated at 2026-09-21T04:31:07Z."
    faked = ("Rows for `plugin/x/facade_1.py`, `plugin/x/facade_2.py`, `plugin/x/facade_3.py`.\n"
             "commit_sha: 0000000000000000000000000000000000000000\n"
             "generated_at_utc: 2026-09-20T00:00:00Z\n")
    sig = lambda t: {s for s, _ in scan_text(t, paths, names)}
    cases = [
        ("an honest artifact fires nothing", sig(honest) == set()),
        ("an absent path is caught", "PATH" in sig(faked)),
        ("a numbered sequence is caught", "SEQUENCE" in sig(faked)),
        ("a zero sha is caught", "ZEROSHA" in sig("commit_sha: " + "0" * 40)),
        ("a midnight stamp is caught", "ZEROSHA" in sig("generated_at_utc: 2026-09-20T00:00:00Z")),
        ("a real time is not a midnight stamp", "ZEROSHA" not in sig("at: 2026-09-20T13:07:41Z")),
        ("two numbered names are a coincidence, not a pattern",
         not sequences(["gone_1.py", "gone_2.py"])),
        ("three make a pattern", bool(sequences(["gone_1.py", "gone_2.py", "gone_3.py"]))),
        ("a bare basename of a real file is not fabricated",
         bad_paths("see `real_one.py`", paths, names) == []),
        ("a path present in the tree is not fabricated",
         bad_paths("see `scripts/real_one.py`", paths, names) == []),
        ("the sequence detail names the count", "3 names" in dict(
            (s, d) for s, d in scan_text(faked, paths, names)).get("SEQUENCE", "")),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.files:
        ap.error("name at least one artifact to scan")
    try:
        paths, basenames = tracked()
    except (OSError, subprocess.SubprocessError) as exc:
        print("NO-DATA: the tree could not be listed (%s); this is not a clean scan" % exc)
        return 2
    total = 0
    for f in a.files:
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print("NO-DATA %s could not be read (%s); this is not a clean scan" % (f, exc))
            return 2
        found = scan_text(text, paths, basenames)
        total += len(found)
        if not found:
            print("NO SIGNAL %s (this means no signal fired, NOT that the document is true)" % f)
        else:
            print("SIGNALS   %s: %d" % (f, len(found)))
            for sig, detail in found[:12]:
                print("  %-9s %s" % (sig, detail))
            if len(found) > 12:
                print("  ...       and %d more" % (len(found) - 12))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
