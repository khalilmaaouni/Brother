#!/usr/bin/env python3
"""guarded_strip: never delete rows from a real file without counting them first.

Row M1 of the 2026-09-07 reflection. A cleanup filter meant for 7 residue
rows dropped 1460 historical rows of the founder's hook log, because a
deletion filter with an OR clause matched the history and nothing counted
the match before deleting. This tool refuses that shape structurally: it
always prints every row it is about to remove and the count, and it refuses
to write anything when that count disagrees with --expect. A dry run (no
--apply) never writes regardless of the count. An --apply run backs the
file up before it writes, so a wrong --expect that still matches its own
guess cannot lose the original either.

Selection is one of two ways, mutually exclusive:
  --match PYEXPR   a python expression evaluated per line with `row` bound
                    to that line's parsed JSON object (so a JSONL log's
                    rows can be matched by field, e.g. "row.get('kind')
                    == 'residue'"). A line that is not valid JSON is never
                    selected by --match: it is kept and reported.
  --line-range A-B  an inclusive 1-indexed line range, no JSON parsing.

Exit codes: 0 selection matched --expect (written if --apply, else a dry
run); 2 a usage problem (bad args, missing file, a --match expression that
raised on a well-formed row); 3 the selected count did not equal --expect,
refused, nothing written.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import shutil
import sys
import time


class Usage(Exception):
    """A problem with the request itself, never a reason to guess."""


def _parse_line_range(text):
    parts = text.split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise Usage("--line-range must look like A-B (got %r)" % text)
    lo, hi = int(parts[0]), int(parts[1])
    if lo < 1 or hi < lo:
        raise Usage("--line-range %r is not a valid inclusive range" % text)
    return lo, hi


def select_rows(lines, match_expr=None, line_range=None):
    """Return (selected, malformed): selected is [(1-based line no, line)],
    malformed is line numbers --match could not parse as JSON (kept, not
    selected). line_range never parses JSON, so malformed is always empty
    for it."""
    selected, malformed = [], []
    if line_range is not None:
        lo, hi = line_range
        if hi > len(lines):
            raise Usage("--line-range end %d exceeds the file's %d line(s)"
                        % (hi, len(lines)))
        for i, line in enumerate(lines, start=1):
            if lo <= i <= hi:
                selected.append((i, line))
        return selected, malformed

    code = compile(match_expr, "<--match>", "eval")
    for i, line in enumerate(lines, start=1):
        stripped = line.rstrip("\n").rstrip("\r")
        if not stripped.strip():
            continue
        try:
            row = json.loads(stripped)
        except ValueError:
            malformed.append(i)
            continue
        try:
            # eval is the tool's own contract (--match takes a python
            # expression, by design): the operator running this CLI supplies
            # it themselves, __builtins__ is stripped, and the only name
            # bound is `row`, the already-parsed JSON of one line.
            hit = eval(code, {"__builtins__": {}}, {"row": row})  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            raise Usage("--match raised on line %d (%r): %s" % (i, row, exc))
        if hit:
            selected.append((i, line))
    return selected, malformed


def make_backup(path, backup_dir, clock=None):
    clock = clock or time.time
    backup_dir = backup_dir or os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(clock()))
    name = "%s.%s.bak" % (os.path.basename(path), stamp)
    backup_path = os.path.join(backup_dir, name)
    # never overwrite an earlier same-second backup of the same file
    suffix = 1
    while os.path.exists(backup_path):
        backup_path = os.path.join(backup_dir, "%s.%d" % (name, suffix))
        suffix += 1
    shutil.copy2(path, backup_path)
    return backup_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", required=True, help="the JSON-lines file to strip rows from")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--match", help="python expression over `row` (parsed JSON)")
    group.add_argument("--line-range", help="inclusive 1-indexed A-B, no JSON parsing")
    ap.add_argument("--expect", type=int, required=True,
                    help="the exact row count the caller believes will be selected")
    ap.add_argument("--backup-dir", help="where --apply writes the backup (default: the file's own directory)")
    ap.add_argument("--apply", action="store_true", help="without this, nothing is ever written")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if not os.path.isfile(args.file):
        print("guarded_strip: no such file: %s" % args.file, file=sys.stderr)
        return 2

    with open(args.file, encoding="utf-8") as fh:
        lines = fh.readlines()

    try:
        line_range = _parse_line_range(args.line_range) if args.line_range else None
        selected, malformed = select_rows(lines, match_expr=args.match, line_range=line_range)
    except Usage as exc:
        print("guarded_strip: %s" % exc, file=sys.stderr)
        return 2

    for i, line in selected:
        print("%d: %s" % (i, line.rstrip("\n").rstrip("\r")))
    for i in malformed:
        print("line %d: malformed JSON, kept, not evaluated" % i, file=sys.stderr)
    print("selected %d row(s) of %d line(s), %d malformed and kept"
          % (len(selected), len(lines), len(malformed)))

    if len(selected) != args.expect:
        print("REFUSED: selected %d row(s), expected %d; nothing written"
              % (len(selected), args.expect), file=sys.stderr)
        return 3

    if not args.apply:
        print("dry run (no --apply): nothing written")
        return 0

    backup_path = make_backup(args.file, args.backup_dir)
    selected_idx = {i for i, _ in selected}
    kept = [line for i, line in enumerate(lines, start=1) if i not in selected_idx]
    with open(args.file, "w", encoding="utf-8") as fh:
        fh.writelines(kept)
    print("backup: %s" % backup_path)
    print("before: %d line(s), after: %d line(s)" % (len(lines), len(kept)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
