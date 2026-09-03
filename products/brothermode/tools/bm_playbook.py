#!/usr/bin/env python3
"""bm_playbook: promote a lesson into a reusable playbook, but only once
tools/bm_recurrence.py's receipts show it was surfaced and applied before the
first write across enough distinct work units (F7).

WHY THIS EXISTS. A playbook hand-written from a hunch is a prompt library
entry whose success is assumed, not measured -- the exact trap the roadmap's
F7 adaptation note names. This file never writes a playbook from a human's
say-so alone: promote() refuses any lesson id that bm_recurrence's own
receipts table cannot back with at least min_recurrences distinct units, each
one recorded with before_first_write true. The promotion record then embeds
the real unit ids it read, not a hand-typed count, so a promoted playbook can
always be traced back to the receipts that earned it.

This file reads bm_recurrence.py's own sqlite3 receipts table rather than
building a second store -- see bm_recurrence.py's own module docstring for
the receipt schema (unit_id, surfaced, applied, declined, reason,
before_first_write). It never writes to that table, only queries it.

Python 3.9, standard library only, no network, no subprocess.
"""
import argparse
import datetime
import importlib.util
import json
import os
import sqlite3
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

#: Below this many distinct units backing a lesson, promotion refuses. Same
#: default the roadmap's own F7.1.1 step names.
DEFAULT_MIN_RECURRENCES = 3

DEFAULT_OUT_DIR = "docs/playbooks"


def _load_bm_recurrence():
    """Dynamic import by path, the same pattern bm_freshness.py uses for
    bm_vault.py: bm_playbook.py reads bm_recurrence's receipts table and its
    default_db_path(), never a copy of either."""
    spec = importlib.util.spec_from_file_location(
        "bm_recurrence", os.path.join(_TOOLS_DIR, "bm_recurrence.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_receipts(db_path):
    """[(unit_id, applied_json, before_first_write), ...], or [] when the db
    does not exist yet or exists but has no receipts table -- both are a
    fresh/empty store, never an error, matching bm_recurrence's own upsert
    schema (CREATE TABLE IF NOT EXISTS)."""
    if not db_path or not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    try:
        try:
            return con.execute(
                "SELECT unit_id, applied, before_first_write FROM receipts").fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()


def candidates_for_promotion(db_path, min_recurrences=DEFAULT_MIN_RECURRENCES):
    """Group receipts by each id in `applied` where before_first_write is
    true, counting DISTINCT unit_ids per lesson id. Returns a list of
    {lesson_id, unit_ids (sorted), recurrences}, sorted by lesson_id, for
    every lesson id meeting min_recurrences. promote() below calls this
    (via promotion_record) rather than re-deriving the count, so the two
    paths can never disagree."""
    lesson_units = {}
    for unit_id, applied_json, before_first_write in _read_receipts(db_path):
        if not before_first_write:
            continue
        for lesson_id in json.loads(applied_json):
            lesson_units.setdefault(lesson_id, set()).add(unit_id)
    out = []
    for lesson_id in sorted(lesson_units):
        unit_ids = sorted(lesson_units[lesson_id])
        if len(unit_ids) >= min_recurrences:
            out.append({"lesson_id": lesson_id, "unit_ids": unit_ids,
                       "recurrences": len(unit_ids)})
    return out


def promotion_record(lesson_id, db_path):
    """The exact receipts backing lesson_id's eligibility (applied, before
    first write), read straight from bm_recurrence's table -- never a
    human-typed count. Returns unit_ids sorted, regardless of whether
    lesson_id clears any threshold; promote() below is the one that checks
    the threshold, so a caller can also use this to report how far short an
    ineligible lesson id is."""
    unit_ids = set()
    for unit_id, applied_json, before_first_write in _read_receipts(db_path):
        if before_first_write and lesson_id in json.loads(applied_json):
            unit_ids.add(unit_id)
    return sorted(unit_ids)


def playbook_markdown(lesson_id, recurrences_prevented, unit_ids, promoted_at):
    return (
        "---\n"
        "promoted_from: bm_recurrence receipts\n"
        "lesson_id: %s\n"
        "recurrences_prevented: %d\n"
        "unit_ids: %s\n"
        "promoted_at: %s\n"
        "---\n\n"
        "# Playbook: %s\n\n"
        "Promoted automatically: bm_recurrence.py recorded %d distinct work "
        "unit(s) where this lesson was surfaced and applied before the "
        "first write. See unit_ids above for the exact receipts.\n\n"
        "(placeholder body -- a human fills in the reusable procedure this "
        "lesson earned.)\n"
    ) % (lesson_id, recurrences_prevented, json.dumps(unit_ids), promoted_at,
        lesson_id, recurrences_prevented)


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def promote(lesson_id, db_path, out_dir=DEFAULT_OUT_DIR,
           min_recurrences=DEFAULT_MIN_RECURRENCES):
    """Refuses (ValueError) a lesson id the receipts do not back with at
    least min_recurrences distinct before-first-write units; otherwise
    writes docs/playbooks/<lesson_id>.md with the real unit ids embedded and
    returns the path written."""
    unit_ids = promotion_record(lesson_id, db_path)
    if len(unit_ids) < min_recurrences:
        raise ValueError(
            "below threshold: %d of %d recurrences required for lesson %r"
            % (len(unit_ids), min_recurrences, lesson_id))
    promoted_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    md = playbook_markdown(lesson_id, len(unit_ids), unit_ids, promoted_at)
    _ensure_dir(out_dir)
    path = os.path.join(out_dir, "%s.md" % lesson_id)
    _write_text(path, md)
    return path


def cli_candidates(args):
    br = _load_bm_recurrence()
    db_path = args.db or br.default_db_path()
    result = candidates_for_promotion(db_path, args.min_recurrences)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cli_promote(args):
    br = _load_bm_recurrence()
    db_path = args.db or br.default_db_path()
    try:
        path = promote(args.lesson, db_path, args.out_dir, args.min_recurrences)
    except ValueError as exc:
        print("bm_playbook: REFUSED: %s" % exc, file=sys.stderr)
        return 1
    print("bm_playbook: promoted %s -> %s" % (args.lesson, path))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="bm_playbook.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("candidates",
                       help="list lesson ids eligible for promotion")
    c.add_argument("--db", default=None,
                   help="bm_recurrence receipts db (default: its own "
                        "default_db_path())")
    c.add_argument("--min-recurrences", type=int, dest="min_recurrences",
                   default=DEFAULT_MIN_RECURRENCES)

    p = sub.add_parser("promote", help="write a playbook for one lesson id")
    p.add_argument("--db", default=None)
    p.add_argument("--lesson", required=True)
    p.add_argument("--out-dir", dest="out_dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--min-recurrences", type=int, dest="min_recurrences",
                   default=DEFAULT_MIN_RECURRENCES)

    args = ap.parse_args(argv)
    if args.cmd == "candidates":
        return cli_candidates(args)
    if args.cmd == "promote":
        return cli_promote(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
