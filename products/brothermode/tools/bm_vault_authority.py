#!/usr/bin/env python3
"""The authority contract: a source of record outranks a casual note.

WHY THIS EXISTS. Benchmark row D08, measured 2026-08-29: zero of 802 notes
declare authority, so ranking is similarity only and a stray session log can
outrank an approved decision whenever its wording is a closer match. In an
enterprise that is a governance failure, not a relevance tweak: the whole point
of keeping a validated decision is that it WINS against a passing remark.

THE VOCABULARY, three levels and no more:
  source_of_record  an approved decision, a signed-off definition, a ruling
  derived           computed or distilled from sources, correct until they move
  casual            a working note, a session log, an observation in passing

Three on purpose. Every extra level is a boundary two writers will draw
differently, and a vocabulary nobody applies consistently ranks nothing.

THE COMPARATOR IS LEXICOGRAPHIC, and that is the whole design: authority first,
similarity second. A source_of_record note with LOWER similarity outranks a
casual note with HIGHER similarity, always, because blending the two into one
weighted score is exactly how similarity smuggles itself back on top whenever
the weight is tuned for recall.

AN UNKNOWN VALUE IS A FINDING, never a rank. Mapping a typo silently to the
lowest rank buries a source of record; mapping it to the highest promotes
garbage. Both are wrong in different directions, so an unknown value refuses to
rank at all and the check names it. An ABSENT declaration ranks as casual,
stated openly: that is what 812 undeclared notes are today, and pretending
otherwise would invent authority nobody granted.

Exit 0 clean, 1 findings, 2 NO-DATA. Stdlib only, writes nothing anywhere.
"""
import argparse
import os
import re
import sys

LEVELS = ("casual", "derived", "source_of_record")   # ascending rank
_RANK = {name: i for i, name in enumerate(LEVELS)}
AUTHORITY_RE = re.compile(r"^authority:\s*(\S+)\s*$", re.M)
SKIP_DIRS = {".git", ".trash", ".obsidian"}


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def read_authority(text):
    """(level, problem). Absent is ("casual", None), stated in the module
    docstring; a value outside the vocabulary is (None, reason)."""
    m = AUTHORITY_RE.search(_frontmatter(text))
    if not m:
        return "casual", None
    value = m.group(1).strip().strip('"').strip("'")
    if value in _RANK:
        return value, None
    return None, "unknown authority %r, not in %s" % (value, "/".join(LEVELS))


def rank_key(level, similarity):
    """The sort key: authority strictly first, similarity second.

    Raises on an unknown level rather than guessing a rank, because both
    guesses are wrong in different directions. Callers filter findings out
    before ranking, visibly.
    """
    if level not in _RANK:
        raise ValueError("unrankable authority %r" % level)
    return (_RANK[level], similarity)


def outranks(a_level, a_sim, b_level, b_sim):
    """Does note A beat note B? The D08 observable in one function."""
    return rank_key(a_level, a_sim) > rank_key(b_level, b_sim)


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def cmd_check(vault):
    counts = {name: 0 for name in LEVELS}
    declared = 0
    findings = []
    total = 0
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print("bm_vault_authority: warning: skipping unreadable %s (%s); "
                  "not counted in notes or authority tallies" % (path, exc),
                  file=sys.stderr)
            continue
        total += 1
        rel = os.path.relpath(path, vault)
        level, problem = read_authority(text)
        if problem:
            findings.append((rel, problem))
            continue
        if AUTHORITY_RE.search(_frontmatter(text)):
            declared += 1
        counts[level] += 1
    print("vault: %s" % vault)
    print("notes: %d" % total)
    print("declaring authority: %d (absent ranks as casual, stated rather than implied)"
          % declared)
    for name in reversed(LEVELS):
        print("  %-17s %d" % (name, counts[name]))
    if findings:
        print("UNRANKABLE, a named finding each, never silently ranked: %d" % len(findings))
        for rel, problem in findings:
            print("  %s: %s" % (rel, problem))
    return 1 if findings else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_authority: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    return cmd_check(args.vault)


if __name__ == "__main__":
    sys.exit(main())
