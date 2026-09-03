#!/usr/bin/env python3
"""prevented_word_gate: the word "prevented" stays off every surface until a real
prevented_fraction run has printed a number and is cited by its run id.

WHY THIS EXISTS. docs/plan/PREVENTION-CONTROL-ARM-DESIGN.md section 7 said no
board, README, benchmark or release note may use the word until a run of the
prevention design prints a number. The independent adversarial review of
2026-09-03 pointed out that this was a discipline with no mechanism: nothing
scanned the surfaces, so the rule rested on author vigilance. This is the
mechanism, in the shape of the push gates that already scan for attribution and
private terms. A surface may use a prevent-word ONLY when a prevented_fraction
run id is cited on the same line or the line before, so the claim always points
at the number that backs it. Until such a run exists, no citation is possible
and the word cannot appear.

MATCH RULE. The predicate is the word "prevent" followed by word characters
(prevent, prevents, prevented, prevention, preventing), case-insensitive, as a
whole word. A CITATION is the token "prevented_fraction run <id>" where <id> is
one or more of [A-Za-z0-9._-], on the matching line or the line immediately
above it. A prevent-word inside this gate's own source or inside the design
document that defines the rule is exempt, because a file that DEFINES the rule
necessarily names the word; the exemption is by explicit path, never by content.

NO-DATA is never a pass: a file that cannot be read is reported as NO-DATA and
fails the gate under --strict, exactly like the estate's other scanners.

Python 3, standard library only. No em or en dashes anywhere.
"""
import argparse
import os
import re
import sys

WORD = re.compile(r"\bprevent\w*\b", re.IGNORECASE)
CITE = re.compile(r"prevented_fraction\s+run\s+[A-Za-z0-9._-]+", re.IGNORECASE)

# Files that DEFINE or TEST the rule name the word by necessity. Exempt by path.
EXEMPT_BASENAMES = {
    "prevented_word_gate.py",
    "test_prevented_word_gate.py",
    "PREVENTION-CONTROL-ARM-DESIGN.md",
}


def scan_text(text):
    """Return a list of (lineno, line) for prevent-words with no citation on the
    line or the line above. An empty list means the text is clean."""
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines):
        if not WORD.search(line):
            continue
        above = lines[i - 1] if i > 0 else ""
        if CITE.search(line) or CITE.search(above):
            continue
        hits.append((i + 1, line.strip()))
    return hits


def is_exempt(path):
    return os.path.basename(path) in EXEMPT_BASENAMES


def scan_file(path):
    """Return ('CLEAN', hits) or ('NO-DATA', reason). Exempt files are CLEAN."""
    if is_exempt(path):
        return ("CLEAN", [])
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeError) as exc:
        return ("NO-DATA", "could not read %s: %s" % (path, exc))
    return ("CLEAN", scan_text(text))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="files to scan")
    ap.add_argument("--strict", action="store_true",
                    help="treat NO-DATA (unreadable file) as a failure")
    args = ap.parse_args(argv)
    if not args.paths:
        print("prevented-word-gate: no paths given, nothing to scan")
        return 0
    failed = False
    nodata = False
    for path in args.paths:
        verdict, payload = scan_file(path)
        if verdict == "NO-DATA":
            print("NO-DATA: %s" % payload)
            nodata = True
            continue
        for lineno, line in payload:
            print("BLOCK %s:%d uses a prevent-word with no prevented_fraction run "
                  "cited: %s" % (path, lineno, line))
            failed = True
    if failed:
        print("prevented-word-gate: REFUSED, the word is used with no number behind it")
        return 1
    if nodata and args.strict:
        print("prevented-word-gate: NO-DATA under --strict is a failure")
        return 2
    print("prevented-word-gate: CLEAN, no unbacked prevent-word on any surface scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
