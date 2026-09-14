#!/usr/bin/env python3
"""Gate a JBEQ-MDM unseen qualification set on its own blind audit.

WHY THIS EXISTS (row M8, ~/.claude/evidence/reflection-measures-2026-09-07.md):
two qualification sets (U4, U5) carried answer-level seed defects that were
found only at the run, after prompts had already been written and an engine
scored against them. A set's `expected` values are only trustworthy once an
independent blind audit has checked them and every correction it demanded has
actually landed in the seed. This script is the one place that fact is
checked, so `scripts/jbeq_mdm.py`'s `prompts` and `score` subcommands can
refuse to run against an unseen-* seed until it passes (or the caller says
--regression).

THE CONTRACT. Given a seed path benchmarks/jbeq/mdm/unseen-N-DATE.json, the
gate reads the sibling record benchmarks/jbeq/mdm/unseen-N-DATE-RECORD.md and
looks for a "## Blind audit" section (the heading this estate's two oldest
unseen RECORDs, unseen-2026-09-06-RECORD.md and
unseen-4-2026-09-06-RECORD.md, do not actually share verbatim; neither carries
a section built for a mechanical gate, so this heading is DEFINED here and
used from this row forward). That section must carry:

  * the auditor's scratch hash: a lowercase hex string of 32 (md5) or 64
    (sha256) characters, written and locked before the seed was opened. The
    row's own wording said "a 64 hex string" (the sha256 shape every audit
    from U4 onward actually uses); U1 through U3's audits locked their
    scratch file with md5 instead, so 32 hex characters is accepted too
    rather than manufacturing a hash that was never taken.
  * an agreement line of the shape "N of 40".
  * a corrections section whose answer-level items (a line correcting the
    case's `expected` field) are each marked APPLIED. A correction to any
    other field (input, question, rationale, critical_class) is not an
    answer-level item and is not checked here.

PASS names the hash and the agreement; FAIL names the missing or unmet
piece; NO-DATA is only for a RECORD file that does not exist, or exists but
carries no "## Blind audit" section at all (an audit that never happened, or
one whose hash never reached the required shape).
"""
import argparse
import os
import re
import sys

HEADING = "## Blind audit"
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b|\b[0-9a-f]{32}\b")
AGREEMENT_RE = re.compile(r"(\d+)\s+of\s+40\b")
EXPECTED_CORRECTION_RE = re.compile(r"\bexpected\s*,", re.IGNORECASE)
# The exact bracketed marker a correction line must carry to count as
# applied. A bare substring check for "APPLIED" would also match "NOT
# APPLIED" or "PENDING, not applied" (both contain "APPLIED"), so the
# marker is the parenthesised word, upper-cased, nothing looser.
APPLIED_MARKER = "(APPLIED)"

PASS = "PASS"
FAIL = "FAIL"
NODATA = "NO-DATA"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NODATA = 2


def record_path_for(seed_path):
    """benchmarks/jbeq/mdm/unseen-4-2026-09-06.json ->
    benchmarks/jbeq/mdm/unseen-4-2026-09-06-RECORD.md, its sibling record.
    None if `seed_path` does not end in .json."""
    if not seed_path.endswith(".json"):
        return None
    stem = seed_path[: -len(".json")]
    return stem + "-RECORD.md"


def _section(text, heading):
    """The lines of `text` from `heading` (matched as a whole stripped
    line) up to the next "## " heading or the end of the file. None if
    `heading` never appears as its own line."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def gate(seed_path):
    """Return (status, message): status is PASS, FAIL or NO-DATA."""
    record_path = record_path_for(seed_path)
    if not record_path:
        return NODATA, "%s does not end in .json, no RECORD can be derived" % seed_path
    if not os.path.isfile(record_path):
        return NODATA, "no RECORD file at %s" % record_path
    try:
        with open(record_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return NODATA, "cannot read %s: %s" % (record_path, exc)

    section = _section(text, HEADING)
    if section is None:
        return NODATA, "%s carries no %r section" % (record_path, HEADING)

    hash_match = HASH_RE.search(section)
    if not hash_match:
        return FAIL, ("%s: %r section carries no 32 or 64 hex character "
                      "scratch hash" % (record_path, HEADING))

    agreement_match = AGREEMENT_RE.search(section)
    if not agreement_match:
        return FAIL, ("%s: %r section carries no agreement line "
                      "('N of 40')" % (record_path, HEADING))

    unapplied = []
    for line in section.splitlines():
        stripped = line.strip()
        # Any Markdown bullet marker can introduce a correction item.
        if not stripped.startswith(("-", "*", "+")):
            continue
        if not EXPECTED_CORRECTION_RE.search(stripped):
            continue
        if APPLIED_MARKER not in stripped.upper():
            unapplied.append(stripped)
    if unapplied:
        return FAIL, ("%s: unapplied answer-level (expected) "
                      "correction(s): %s" % (record_path, "; ".join(unapplied)))

    return PASS, ("%s: scratch hash %s, agreement %s of 40"
                  % (record_path, hash_match.group(0), agreement_match.group(1)))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check an unseen-* seed's RECORD carries a passing blind audit section.")
    parser.add_argument("seed_path")
    args = parser.parse_args(argv)
    status, message = gate(args.seed_path)
    print("%s %s" % (status, message))
    if status == PASS:
        return EXIT_PASS
    if status == FAIL:
        return EXIT_FAIL
    return EXIT_NODATA


if __name__ == "__main__":
    sys.exit(main())
