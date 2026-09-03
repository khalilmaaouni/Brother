#!/usr/bin/env python3
"""The bi-temporal contract: when a fact was true, and when we found out.

WHY THIS EXISTS. Benchmark row D09, measured 2026-08-29: none of the five
temporal fields appears on any of 802 notes. `created:` is a write date, and a
write date answers neither of the two questions that matter after a decision
goes wrong: what was true in the world at the time, and what did we know at the
time. Those are different clocks, which is the whole of bi-temporality, and a
vault without them cannot answer "what did we believe in June" at all.

THE FIVE FIELDS, world clock first, then our clock:
  valid_from    the fact became true in the world
  valid_to      it stopped being true (absent means still true)
  observed_at   we first saw it
  ingested_at   it entered this vault
  verified_at   somebody checked it against its source

All dates, ISO YYYY-MM-DD, in frontmatter. Date-level on purpose: this corpus
records decisions and lessons, not ticks, and a precision the writers will not
maintain is a precision the readers cannot trust.

WHAT A MISSING WINDOW MEANS, stated rather than guessed. An unmigrated note
carries none of these fields. It is treated as PRESENT in current truth (the
status quo before this contract existed) and EXCLUDED from any as-of comparison,
counted and named rather than silently included, because inventing a window for
it would manufacture exactly the false history this row exists to prevent.

A MALFORMED WINDOW IS REPORTED, NEVER SILENTLY CURRENT. A note whose valid_to
precedes its valid_from, or whose date does not parse, is a defect the check
names. Treating it as current would hide the corruption behind a working lookup,
which is the duplicate-id failure wearing different clothes.

Exit 0 clean, 1 findings, 2 NO-DATA. Python 3.9 floor, stdlib only, writes
nothing anywhere: this is the reading half of the contract.
"""
import argparse
import datetime
import os
import re
import sys

FIELDS = ("valid_from", "valid_to", "observed_at", "ingested_at", "verified_at")
SKIP_DIRS = {".git", ".trash", ".obsidian"}
_FIELD_RES = {f: re.compile(r"^%s:\s*(\S+)\s*$" % f, re.M) for f in FIELDS}


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _parse_date(raw):
    """A date or None. Quotes tolerated, garbage refused rather than coerced."""
    try:
        return datetime.date.fromisoformat(raw.strip().strip('"').strip("'"))
    except ValueError:  # sbe: allow-silent garbage refused rather than coerced; the caller already records this as a reported 'unparseable date' problem
        return None


def parse(text):
    """{field: date|None} plus problems: [(field, reason)]. A note with no
    temporal field at all returns ({}, []) so callers can tell 'unmigrated'
    from 'declared and empty', which are different states."""
    block = _frontmatter(text)
    window, problems = {}, []
    for f in FIELDS:
        m = _FIELD_RES[f].search(block)
        if not m:
            continue
        d = _parse_date(m.group(1))
        if d is None:
            problems.append((f, "unparseable date %r" % m.group(1)))
        else:
            window[f] = d
    vf, vt = window.get("valid_from"), window.get("valid_to")
    if vf and vt and vt < vf:
        problems.append(("valid_to", "window inverted: valid_to %s precedes valid_from %s"
                         % (vt, vf)))
    return window, problems


def in_truth(window, problems, when):
    """Is a note part of the truth at date `when`?

    Returns True, False, or None for CANNOT SAY (no window, or a malformed
    one). None is never collapsed into either boolean: a malformed window
    treated as current hides corruption, and treated as absent it erases a
    note that may well still be true. The caller says what None becomes,
    visibly.
    """
    if problems:
        return None
    if not window or "valid_from" not in window:
        return None
    if when < window["valid_from"]:
        return False
    vt = window.get("valid_to")
    if vt is not None and when >= vt:
        return False
    return True


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def scan(vault):
    """[(relpath, window, problems)] for every note."""
    out = []
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("bm_vault_temporal: WARNING, could not read %s (%s); "
                             "excluded from this scan\n" % (path, exc))
            continue
        window, problems = parse(text)
        out.append((os.path.relpath(path, vault), window, problems))
    return out


def cmd_check(vault):
    rows = scan(vault)
    tracked = [r for r in rows if r[1]]
    broken = [r for r in rows if r[2]]
    print("vault: %s" % vault)
    print("notes: %d" % len(rows))
    print("carrying at least one temporal field: %d" % len(tracked))
    print("with a MALFORMED window: %d" % len(broken))
    for rel, _w, problems in broken:
        for field, reason in problems:
            print("  %s: %s: %s" % (rel, field, reason))
    return 1 if broken else 0


def cmd_asof(vault, when):
    """Every note whose truth-membership at `when` DIFFERS from today, which is
    the observable D09 promises: history you can actually ask."""
    today = datetime.date.today()
    rows = scan(vault)
    unknown = 0
    changed = []
    for rel, window, problems in rows:
        then = in_truth(window, problems, when)
        now = in_truth(window, problems, today)
        if then is None or now is None:
            unknown += 1
            continue
        if then != now:
            changed.append((rel, then, now))
    print("as of %s versus today %s" % (when, today))
    print("notes whose truth membership differs: %d" % len(changed))
    for rel, then, now in changed:
        print("  %s: %s at %s, %s today"
              % (rel, "in truth" if then else "not yet true", when,
                 "in truth" if now else "no longer true"))
    print("excluded, window unknown or malformed: %d (excluded and counted, "
          "never silently included)" % unknown)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check", "asof"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--date", help="for asof: YYYY-MM-DD")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_temporal: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    if args.command == "check":
        return cmd_check(args.vault)
    when = _parse_date(args.date or "")
    if when is None:
        print("bm_vault_temporal: asof needs --date YYYY-MM-DD", file=sys.stderr)
        return 2
    return cmd_asof(args.vault, when)


if __name__ == "__main__":
    sys.exit(main())
