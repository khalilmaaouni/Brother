#!/usr/bin/env python3
"""rulebook_inventory: ACC8.a, the measured inventory of the owner's standing
rules file, carrying counts, line numbers and hashes only.

WHY THIS EXISTS (unit ACC8, sub unit ACC8.a). The standing rules file is read
at every session start and had grown past 80 KB. Before any line retires, the
file is measured: how many rule units it holds, which class each unit falls
in, and which units are largest. The owner reads the counts and the held diff
and says what retires. This module never writes the rules file.

THE UNIT. One top level bullet (a line starting "- ") with every line that
continues it, indented or not, up to the next bullet, heading or blank line.
A heading is never a unit; the unit remembers the line number of the heading
above it, or None when no heading precedes it.

THE CLASSES, exactly one per unit, decided in this order:

  DUPLICATE        its normalised text equals an earlier unit's exactly
  SUPERSEDED-TEXT  its own text says, in the passive, that it is rescinded,
                   retired, withdrawn or superseded (an active rule that
                   rescinds another, and a negation, both stay KEEP)
  DEAD-REF         it cites a script path that is not on disk
  UNENFORCED       it says UNENFORCED (this wins over ENFORCED-INDEX: a rule
                   is never indexed away while its text says nothing
                   enforces it)
  ENFORCED-INDEX   it says ENFORCED, cites an enforcing path that exists, and
                   says nothing that qualifies the status: PARTLY ENFORCED,
                   NOT built, stated discipline, candidate control all keep
                   the unit whole, because the pointer would read as an
                   unqualified ENFORCED (defect caught by the owner 2026-10-03)
  KEEP             everything else

A SUPERSEDED-TEXT unit is not dropped whole. Its sentences are split, the
sentence carrying the supersession is the marker, and every other sentence
that no other unit restates survives into the diet (the owner's 2026-10-03
review: "Superseded by X. Surviving idea: ..." carried a live requirement).
surviving_sentences is the one place that decides it.

PRIVACY. The rules are private. INVENTORY.json carries counts, line numbers,
byte counts and sha256 digests, never a unit's text. The suite asserts that
with a fixture: no run of twenty characters of any fixture rule may appear in
the written JSON.

Python 3.9 compatible, standard library only. No em or en dashes in this file.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

INVENTORY_NAME = "INVENTORY.json"
DIET_NAME = "DIET.diff"
DEFAULT_RULES = os.path.join("~", ".claude", "CLAUDE.md")
DEFAULT_OUT = os.path.join("~", ".claude", "evidence", "rulebook-diet")
DIET_HEADER_RE = re.compile(r"^# rulebook-diet source_bytes=(\d+) sha256=([0-9a-f]{64})")

CLASSES = ("DUPLICATE", "SUPERSEDED-TEXT", "DEAD-REF", "UNENFORCED", "ENFORCED-INDEX", "KEEP")

#: A script path: an optional home or absolute prefix, at least one directory
#: and a .py or .sh basename. Prose words and markdown paths never match.
PATH_RE = re.compile(
    r"(?:~|\$HOME|/Users/[A-Za-z0-9._-]+)?(?:/?[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|sh)\b")
HEADING_RE = re.compile(r"^#{1,6} ")
BULLET_RE = re.compile(r"^- ")
_RETIRED = r"(?:rescinded|retired|withdrawn|superseded)"
_SUBJECT = r"(?:rule|law|line|section|clause|directive|order|paragraph)"
#: The passive forms that say the unit itself is gone. The unit's first word
#: ("Superseded by ...", "Rescinded ...") or a sentence whose subject is this
#: rule. "rescinds", "supersedes" (active) never match, and "is not withdrawn"
#: never matches because the verb must sit directly before the participle.
SUPERSEDED_RE = re.compile(
    r"^\W*" + _RETIRED + r"\b"
    r"|\bthis " + _SUBJECT + r"\b[^.;:]{0,40}?\b(?:is|was|has been|now|stands)\s+" + _RETIRED + r"\b"
    r"|^\W*(?:it|this)\s+(?:is|was|has been)\s+" + _RETIRED + r"\b",
    re.IGNORECASE)
UNENFORCED_RE = re.compile(r"\bUNENFORCED\b")
ENFORCED_RE = re.compile(r"\bENFORCED\b")
#: Anything that qualifies an ENFORCED claim. A unit matching this is never
#: replaced by a bare "ENFORCED by" pointer.
QUALIFIED_RE = re.compile(
    r"\bPARTLY ENFORCED\b|\bNOT built\b|\bnot built\b|\bstated discipline\b|\bcandidate control\b",
    re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rule_units(text: str) -> List[Dict[str, object]]:
    """Split the rules text into units. Each unit carries heading_line (None
    when no heading precedes it), line (1-based, the bullet line), end_line,
    bytes (its lines joined with newlines, trailing newline included), norm,
    paths, sha256 and text. The text field is for the diet and the hash; the
    inventory never writes it."""
    if not isinstance(text, str):
        raise ValueError("rules text must be str")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    units = []  # type: List[Dict[str, object]]
    heading_line = None  # type: Optional[int]
    current = None  # type: Optional[List[int]]

    def close(upto: int) -> None:
        if current is None:
            return
        start = current[0]
        body = lines[start - 1:upto]
        raw = "\n".join(body) + "\n"
        data = raw.encode("utf-8")
        units.append({
            "heading_line": current[1],
            "line": start,
            "end_line": upto,
            "bytes": len(data),
            "norm": re.sub(r"\s+", " ", raw.lower()).strip(),
            "paths": sorted(set(PATH_RE.findall(raw))),
            "sha256": _sha256(data),
            "text": raw,
        })

    for number, line in enumerate(lines, start=1):
        if HEADING_RE.match(line):
            close(number - 1)
            current = None
            heading_line = number
        elif BULLET_RE.match(line):
            close(number - 1)
            current = [number, heading_line]
        elif line.strip() == "":
            close(number - 1)
            current = None
        # any other line continues the open unit, whatever its indent
    close(len(lines))
    return units


def _negated(text: str, start: int) -> bool:
    before = text[max(0, start - 8):start].lower()
    return bool(re.search(r"\b(?:not|never|no)\s*$", before))


def classify_unit(unit: Dict[str, object], seen: Dict[str, int], existing_paths: List[str]) -> str:
    """Return exactly one class. seen maps a norm to the first line that
    carried it and is updated here, so a caller walks the units in order with
    one shared dict."""
    norm = str(unit["norm"])
    if norm in seen:
        return "DUPLICATE"
    seen[norm] = int(unit["line"])
    text = str(unit["text"])
    match = SUPERSEDED_RE.search(text)
    if match is not None and not _negated(text, match.start()):
        return "SUPERSEDED-TEXT"
    paths = list(unit["paths"])
    existing = set(existing_paths)
    if any(p not in existing for p in paths):
        return "DEAD-REF"
    if UNENFORCED_RE.search(text):
        return "UNENFORCED"
    if (ENFORCED_RE.search(text) and any(p in existing for p in paths)
            and not QUALIFIED_RE.search(text)):
        return "ENFORCED-INDEX"
    return "KEEP"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def sentences(unit_text: str) -> List[str]:
    """The unit's sentences, bullet marker stripped, line ends folded."""
    body = re.sub(r"^\s*-\s+", "", unit_text.strip())
    body = re.sub(r"\s*\n\s*", " ", body)
    return [s for s in SENTENCE_RE.split(body) if s.strip()]


def surviving_sentences(unit_text: str, other_norms: List[str]) -> List[str]:
    """The sentences of a superseded unit that still carry a requirement: not
    the supersession marker itself, and not restated (normalised, final
    punctuation dropped) inside any other unit."""
    elsewhere = " | ".join(other_norms)
    out = []  # type: List[str]
    for sentence in sentences(unit_text):
        match = SUPERSEDED_RE.search(sentence)
        if match is not None and not _negated(sentence, match.start()):
            continue
        key = _norm(sentence).rstrip(".!?;:")
        if key and key in elsewhere:
            continue
        out.append(sentence)
    return out


STATUSES = ("PARTLY ENFORCED", "UNENFORCED", "ENFORCED", "NONE")


def enforcement_status(text: str) -> str:
    """The enforcement status a unit's own text claims, exactly one of
    STATUSES. PARTLY ENFORCED wins when the text says so or when it says
    ENFORCED and also qualifies it (NOT built, stated discipline, candidate
    control, or UNENFORCED in the same unit). The retention check compares
    this between a requirement and its destination."""
    enforced = bool(ENFORCED_RE.search(text))
    unenforced = bool(UNENFORCED_RE.search(text))
    if re.search(r"\bPARTLY ENFORCED\b", text) or (enforced and (unenforced or QUALIFIED_RE.search(text))):
        return "PARTLY ENFORCED"
    if unenforced:
        return "UNENFORCED"
    if enforced:
        return "ENFORCED"
    return "NONE"


def resolve_path(cited: str, home: str, repo_root: str) -> str:
    if cited.startswith("~"):
        return home + cited[1:]
    if cited.startswith("$HOME"):
        return home + cited[len("$HOME"):]
    if os.path.isabs(cited):
        return cited
    return os.path.join(repo_root, cited)


def existing_cited_paths(units: List[Dict[str, object]], home: str, repo_root: str) -> List[str]:
    found = []
    for unit in units:
        for cited in unit["paths"]:
            if cited not in found and os.path.exists(resolve_path(cited, home, repo_root)):
                found.append(cited)
    return found


def classify_all(text: str, home: str, repo_root: str) -> List[Dict[str, object]]:
    """rule_units plus a class on each unit, the shared walk every tool uses."""
    units = rule_units(text)
    existing = existing_cited_paths(units, home, repo_root)
    seen = {}  # type: Dict[str, int]
    for unit in units:
        unit["class"] = classify_unit(unit, seen, existing)
    for unit in units:
        if unit["class"] == "SUPERSEDED-TEXT":
            others = [str(u["norm"]) for u in units if u is not unit]
            unit["surviving"] = surviving_sentences(str(unit["text"]), others)
    return units


def unit_index(units: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """The unit_index rows INVENTORY.json records: line numbers, byte counts,
    hashes and classes, never text. rulebook_diet --apply recomputes these
    and refuses when they differ from what was measured."""
    return [{"line": u["line"], "end_line": u["end_line"], "heading_line": u["heading_line"],
             "bytes": u["bytes"], "sha256": u["sha256"], "class": u["class"]} for u in units]


def inventory(text: str, home: str, repo_root: str) -> Dict[str, object]:
    """Counts, line numbers, byte counts and hashes. Never text."""
    units = classify_all(text, home, repo_root)
    data = text.encode("utf-8")
    classes = {}  # type: Dict[str, Dict[str, int]]
    for name in CLASSES:
        classes[name] = {"count": 0, "bytes": 0}
    for unit in units:
        entry = classes[str(unit["class"])]
        entry["count"] += 1
        entry["bytes"] += int(unit["bytes"])
    largest = sorted(units, key=lambda u: (-int(u["bytes"]), int(u["line"])))[:20]
    return {
        "schema": "rulebook-inventory/1",
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bytes": len(data),
        "sha256": _sha256(data),
        "lines": len(text.splitlines()),
        "headings": sum(1 for line in text.split("\n") if HEADING_RE.match(line)),
        "units": len(units),
        "classes": classes,
        "largest": [{"heading_line": u["heading_line"], "line": u["line"], "bytes": u["bytes"]}
                    for u in largest],
        "dead_ref_lines": [int(u["line"]) for u in units if u["class"] == "DEAD-REF"],
        "unit_index": unit_index(units),
    }


def read_rules(path: str) -> Optional[str]:
    """The rules text, or None when the file is unreadable or not UTF-8
    (NO-DATA: nothing is written from a file that cannot be read whole)."""
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def check(rules_path: str, out_dir: str) -> int:
    """0 when INVENTORY.json and DIET.diff exist, are newer than the rules
    file and record its current byte count; 1 otherwise; 2 when the rules
    file is unreadable."""
    text = read_rules(rules_path)
    if text is None:
        print("NO-DATA: rules file unreadable: %s" % rules_path)
        return 2
    try:
        rules_mtime = os.stat(rules_path).st_mtime
    except OSError:
        print("NO-DATA: rules file unreadable: %s" % rules_path)
        return 2
    size = len(text.encode("utf-8"))
    failures = []
    recorded = {}
    for name in (INVENTORY_NAME, DIET_NAME):
        path = os.path.join(out_dir, name)
        try:
            stat = os.stat(path)
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            failures.append("%s missing" % name)
            continue
        if stat.st_mtime <= rules_mtime:
            failures.append("%s older than the rules file" % name)
        if name == INVENTORY_NAME:
            try:
                recorded[name] = int(json.loads(raw.decode("utf-8")).get("bytes"))
            except (ValueError, TypeError, AttributeError):
                failures.append("%s unreadable" % name)
                continue
        else:
            first = raw.decode("utf-8", "replace").split("\n", 1)[0]
            match = DIET_HEADER_RE.match(first)
            if match is None:
                failures.append("%s carries no source_bytes header" % name)
                continue
            recorded[name] = int(match.group(1))
        if recorded[name] != size:
            failures.append("%s records %d bytes, rules file is %d" % (name, recorded[name], size))
    if failures:
        for item in failures:
            print("FAIL: " + item)
        return 1
    print("PASS: inventory and diet are fresh for %d bytes" % size)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="measure the standing rules file (counts only)")
    ap.add_argument("--rules", default=os.path.expanduser(DEFAULT_RULES))
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--out", default=os.path.expanduser(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true",
                    help="exit 0 only when INVENTORY.json and DIET.diff are fresh for the rules file")
    args = ap.parse_args(argv)
    if args.check:
        return check(args.rules, args.out)
    text = read_rules(args.rules)
    if text is None:
        print("NO-DATA: rules file unreadable or not UTF-8: %s" % args.rules)
        return 2
    result = inventory(text, args.home, args.repo)
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, INVENTORY_NAME)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print("bytes=%d units=%d headings=%d" % (result["bytes"], result["units"], result["headings"]))
    for name in CLASSES:
        entry = result["classes"][name]
        print("%-16s count=%-4d bytes=%d" % (name, entry["count"], entry["bytes"]))
    print("dead_ref_lines=%s" % ",".join(str(n) for n in result["dead_ref_lines"]))
    print("wrote %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
