#!/usr/bin/env python3
"""rulebook_diet: ACC8.a, the proposed diet of the standing rules file as a
unified diff the owner reads. This module never writes the rules file.

WHAT THE DIET DOES, by class (see rulebook_inventory.py for the classes):
drops DUPLICATE units, drops a SUPERSEDED-TEXT unit down to the sentences no
other unit restates (those survive as one bullet), replaces each
ENFORCED-INDEX unit with a pointer to its enforcing paths that keeps every
proving sentence (a sentence carrying a backtick command or the word Prove),
and leaves KEEP, DEAD-REF and UNENFORCED units byte for byte untouched. A
dead reference is listed for the owner to repair or retire; the tool never
drops it. A unit whose enforcement is qualified (PARTLY ENFORCED, NOT built,
stated discipline) is KEEP by the classifier and never pointered.

A PROPOSAL, NEVER AN EDIT. propose_diet returns text. The diff is written only
under the evidence directory, mode 600, because a diff necessarily carries the
lines it removes and the rules are private. Applying the diet is the owner's
act at a session end, through --apply, never a session's own.

THE DIFF IS A REPORT, NEVER THE APPLICATION PATH (spec council security
finding, 2026-10-04). A unified diff frames every removed line with "-", so
a rule line beginning "-- " reads as a "--- " file header and a line
beginning "@@" reads as a hunk marker to any consumer that scans for
headers instead of counting hunk lines. No escaping fixes that for every
consumer, so nothing here applies the diff: --apply recomputes the diet in
memory from the rules file (diet_text), refuses unless the file's byte count,
sha256 and unit index equal what INVENTORY.json recorded, and writes the
result atomically (a sibling temp file, then os.replace, through a symlink's
real path). projected_bytes and the suite still read the diff, by hunk
counts only, as the independent check that the report matches the text.

--proposed FILE writes DIET.diff from the rules file to FILE instead of the
class diet: the owner's own word on what retires, held as one diff. The diff
starts with one header line recording the source byte count and sha256 so
rulebook_inventory.py --check can tell a stale diff from a fresh one; patch
skips that line as leading garbage.

Python 3.9 compatible, standard library only. No em or en dashes in this file.
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rulebook_inventory as ri  # noqa: E402

DROPPED = ("DUPLICATE", "SUPERSEDED-TEXT")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


PROVING_RE = re.compile(r"`|\bProve\b")


def proving_sentences(unit_text: str) -> List[str]:
    return [s for s in ri.sentences(unit_text) if PROVING_RE.search(s)]


def pointer_line(unit: Dict[str, object], existing_paths: List[str]) -> str:
    """The ENFORCED-INDEX replacement: the status word, the enforcing paths,
    and every proving sentence of the original, so the command that proves
    the control is never retired with the prose around it."""
    paths = [p for p in unit["paths"] if p in existing_paths]
    line = "- ENFORCEMENT: ENFORCED by %s (text retired from this file)." % ", ".join(paths)
    proving = proving_sentences(str(unit["text"]))
    if proving:
        line += " " + " ".join(proving)
    return line


def surviving_line(unit: Dict[str, object]) -> Optional[str]:
    """The bullet a SUPERSEDED-TEXT unit leaves behind, or None when every
    sentence beyond the marker is restated elsewhere."""
    surviving = list(unit.get("surviving") or [])
    if not surviving:
        return None
    return "- " + " ".join(surviving)


def diet_lines(text: str, home: str, repo_root: str) -> Tuple[List[str], List[Dict[str, object]]]:
    """The dieted file as lines (no line ends) plus the classified units."""
    units = ri.classify_all(text, home, repo_root)
    existing = ri.existing_cited_paths(units, home, repo_root)
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    by_start = {int(u["line"]): u for u in units}
    out = []  # type: List[str]
    number = 1
    while number <= len(lines):
        unit = by_start.get(number)
        if unit is None:
            out.append(lines[number - 1])
            number += 1
            continue
        cls = str(unit["class"])
        if cls == "ENFORCED-INDEX":
            out.append(pointer_line(unit, existing))
        elif cls == "SUPERSEDED-TEXT":
            kept = surviving_line(unit)
            if kept is not None:
                out.append(kept)
        elif cls not in DROPPED:
            out.extend(lines[number - 1:int(unit["end_line"])])
        number = int(unit["end_line"]) + 1
    return out, units


def _join(lines: List[str], trailing: bool) -> str:
    return "\n".join(lines) + ("\n" if trailing and lines else "")


def diet_text(text: str, home: str, repo_root: str) -> str:
    """The dieted rules text, computed in memory. This is what --apply
    writes; the diff is only ever derived from it, never the other way."""
    new_lines, _ = diet_lines(text, home, repo_root)
    return _join(new_lines, text.endswith("\n"))


def propose_diet(text: str, home: str, repo_root: str) -> str:
    """A unified diff from the rules text to its class diet. Text in, text out."""
    return unified(text, diet_text(text, home, repo_root))


def unified(old_text: str, new_text: str) -> str:
    old = old_text.split("\n")
    new = new_text.split("\n")
    if old and old[-1] == "":
        old.pop()
    if new and new[-1] == "":
        new.pop()
    diff = list(difflib.unified_diff(old, new, fromfile="a/CLAUDE.md", tofile="b/CLAUDE.md", lineterm=""))
    return "\n".join(diff) + ("\n" if diff else "")


def apply_unified(text: str, diff: str) -> str:
    """Apply a unified diff in memory. The hunk line counts decide what is a
    header and what is data; a mismatch against the text raises ValueError."""
    src = text.split("\n")
    trailing = text.endswith("\n")
    if trailing:
        src.pop()
    rows = diff.split("\n")
    k = 0
    while k < len(rows) and not (rows[k].startswith("--- ") and k + 1 < len(rows)
                                 and rows[k + 1].startswith("+++ ")):
        k += 1
    if k >= len(rows):
        if all(not HUNK_RE.match(r) for r in rows):
            return text
        raise ValueError("diff has hunks but no file header")
    k += 2
    out = []  # type: List[str]
    i = 0
    while k < len(rows):
        match = HUNK_RE.match(rows[k])
        k += 1
        if match is None:
            continue
        start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        if start - 1 < i:
            raise ValueError("hunks out of order")
        out.extend(src[i:start - 1])
        i = start - 1
        while old_count > 0 or new_count > 0:
            if k >= len(rows):
                raise ValueError("diff truncated inside a hunk")
            row = rows[k]
            k += 1
            if row.startswith("\\"):
                continue
            tag, body = row[:1], row[1:]
            if tag == " ":
                if i >= len(src) or src[i] != body:
                    raise ValueError("context mismatch at line %d" % (i + 1))
                out.append(body)
                i += 1
                old_count -= 1
                new_count -= 1
            elif tag == "-":
                if i >= len(src) or src[i] != body:
                    raise ValueError("removed line mismatch at line %d" % (i + 1))
                i += 1
                old_count -= 1
            elif tag == "+":
                out.append(body)
                new_count -= 1
            else:
                raise ValueError("unexpected diff row inside a hunk")
    out.extend(src[i:])
    return _join(out, trailing)


def projected_bytes(text: str, diff: str) -> int:
    """The byte size of the rules text after the diff, computed by applying it."""
    return len(apply_unified(text, diff).encode("utf-8"))


def diet_header(text: str) -> str:
    data = text.encode("utf-8")
    return "# rulebook-diet source_bytes=%d sha256=%s\n" % (len(data), ri._sha256(data))


def write_private(path: str, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


class RulesChanged(Exception):
    """The rules file's bytes differed, right before the replace, from the
    bytes the diet was computed from: a concurrent edit. Nothing is written."""


def write_atomic(target: str, content: str, expected_sha256: str) -> None:
    """Write content to target (an already resolved real path, never a
    symlink) through a sibling temp file and os.replace, so the file is
    either the old bytes or the new bytes, never a truncated middle. The
    target is re-read and re-hashed right before the replace; a digest other
    than expected_sha256 raises RulesChanged and the temp file is removed."""
    mode = os.stat(target).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=".rulebook-diet-", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp, mode)
        with open(target, "rb") as handle:
            current = ri._sha256(handle.read())
        if current != expected_sha256:
            raise RulesChanged("%s changed while the diet was computed" % target)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_diet(rules_path: str, out_dir: str, home: str, repo_root: str) -> int:
    """Apply the class diet to the rules file, from memory, never from the
    diff. 0 applied (or nothing to apply), 1 refused, 2 NO-DATA. Refuses
    unless INVENTORY.json is readable and records this file's byte count,
    sha256 and unit index exactly, so a file edited since it was measured,
    an enforcer that vanished since, or a second run, all stop here. The
    real path is resolved once, here, and every read, hash and write uses
    it, so a symlink retargeted midway can neither be read nor written."""
    target = os.path.realpath(rules_path)
    text = ri.read_rules(target)
    if text is None:
        print("NO-DATA: rules file unreadable or not UTF-8: %s" % rules_path)
        return 2
    inv_path = os.path.join(out_dir, ri.INVENTORY_NAME)
    try:
        with open(inv_path, "rb") as handle:
            recorded = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        print("FAIL: %s unreadable; run the inventory first" % inv_path)
        return 1
    if not isinstance(recorded, dict):
        print("FAIL: %s is not an inventory" % inv_path)
        return 1
    data = text.encode("utf-8")
    if recorded.get("bytes") != len(data) or recorded.get("sha256") != ri._sha256(data):
        print("FAIL: rules file changed since the inventory (%d bytes now, inventory records %r); "
              "re-run the inventory" % (len(data), recorded.get("bytes")))
        return 1
    new_lines, units = diet_lines(text, home, repo_root)
    if recorded.get("unit_index") != ri.unit_index(units):
        print("FAIL: unit index differs from the inventory (a cited path changed on disk); re-run the inventory")
        return 1
    new_text = _join(new_lines, text.endswith("\n"))
    if new_text == text:
        print("nothing to apply: %d bytes" % len(data))
        return 0
    try:
        write_atomic(target, new_text, ri._sha256(data))
    except RulesChanged as exc:
        print("FAIL: %s (file left as it was); re-run the inventory" % exc)
        return 1
    except OSError as exc:
        print("FAIL: could not write %s: %s (file left as it was)" % (rules_path, exc))
        return 1
    print("applied: %d bytes -> %d bytes" % (len(data), len(new_text.encode("utf-8"))))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="propose the rules file diet as a held diff")
    ap.add_argument("--rules", default=os.path.expanduser(ri.DEFAULT_RULES))
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--repo", default=ri.ROOT)
    ap.add_argument("--out", default=os.path.expanduser(ri.DEFAULT_OUT))
    ap.add_argument("--proposed", help="write DIET.diff from the rules file to this proposed file instead")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the rules file with the class diet computed in memory (never from the diff)")
    args = ap.parse_args(argv)
    if args.apply:
        return apply_diet(args.rules, args.out, args.home, args.repo)
    text = ri.read_rules(args.rules)
    if text is None:
        print("NO-DATA: rules file unreadable or not UTF-8: %s" % args.rules)
        return 2
    if args.proposed:
        proposed = ri.read_rules(args.proposed)
        if proposed is None:
            print("NO-DATA: proposed file unreadable or not UTF-8: %s" % args.proposed)
            return 2
        diff = unified(text, proposed)
    else:
        diff = propose_diet(text, args.home, args.repo)
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, ri.DIET_NAME)
    write_private(out_path, diet_header(text) + diff)
    print("source_bytes=%d projected_bytes=%d" % (len(text.encode("utf-8")), projected_bytes(text, diff)))
    print("wrote %s (mode 600)" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
