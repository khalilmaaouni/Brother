"""intake_inflight: the decision-in-progress file a turn re-reads instead of
restating the brief.

Manus's context-engineering rule (decision A, lane 2 in
docs/decisions/intake-speed-plan-v2-2026-09-06.json): keep a living file the
model re-reads each turn (recitation) rather than carrying the whole
conversation in the prompt again. This is that file's lifecycle: `open`
creates it under docs/decisions/inflight/<slug>.json, `rewrite` appends a
turn and folds in a patch, `close` promotes it to a real record under
docs/decisions/<slug>.json and removes the inflight copy, so a decision has
exactly one file at a time and never two disagreeing about where it stands.

Every write appends a stamp to the record's own `history` list rather than
overwriting it, so `close` hands a record whose own history says how it was
reached, not just what it ended up saying.

Python 3, standard library only. No network.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFLIGHT_DIR = os.path.join(ROOT, "docs", "decisions", "inflight")
DECISIONS_DIR = os.path.join(ROOT, "docs", "decisions")

NODATA = "NO-DATA"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _path(slug):
    return os.path.join(INFLIGHT_DIR, "%s.json" % slug)


def _read_draft(path):
    if not path:
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "%s: could not read %s: %s" % (NODATA, path, exc)
    if not isinstance(data, dict):
        return None, "%s: %s is not a JSON object" % (NODATA, path)
    return data, None


def open_inflight(slug, draft_path=None):
    """(record, error). Refuses to clobber an already-open slug: `open`
    starts a new decision, `rewrite` continues one, and conflating the two
    would silently discard whatever turns already happened."""
    path = _path(slug)
    if os.path.isfile(path):
        return None, "%s: %s is already open; use rewrite instead" % (NODATA, path)
    draft, err = _read_draft(draft_path)
    if err:
        return None, err
    record = dict(draft)
    record["history"] = [{"at": _now(), "turn": 1, "note": "opened"}]
    os.makedirs(INFLIGHT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    return record, None


def rewrite_inflight(slug, note, patch_path=None):
    """(record, error). Appends one stamp with the next turn number and
    merges a patch's top-level keys into the record (a shallow merge:
    exactly what a JSON PATCH-shaped follow-up naturally is here, since the
    record's own top-level fields are the only thing a turn revises)."""
    path = _path(slug)
    if not os.path.isfile(path):
        return None, "%s: %s is not open; use open first" % (NODATA, path)
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "%s: could not read %s: %s" % (NODATA, path, exc)
    patch, err = _read_draft(patch_path)
    if err:
        return None, err
    for k, v in patch.items():
        if k == "history":
            continue
        record[k] = v
    history = record.get("history") or []
    next_turn = (history[-1].get("turn", len(history)) if history else 0) + 1
    history.append({"at": _now(), "turn": next_turn, "note": note})
    record["history"] = history
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    return record, None


def close_inflight(slug):
    """(dest_path, error). Moves the inflight file to
    docs/decisions/<slug>.json. Refuses when the target already exists,
    rather than overwriting a decided record with an in-progress one."""
    src = _path(slug)
    if not os.path.isfile(src):
        return None, "%s: %s is not open" % (NODATA, src)
    dest = os.path.join(DECISIONS_DIR, "%s.json" % slug)
    if os.path.exists(dest):
        return None, "%s already exists; close refuses to overwrite a landed record" % dest
    os.makedirs(DECISIONS_DIR, exist_ok=True)
    os.replace(src, dest)
    return dest, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p_open = sub.add_parser("open")
    p_open.add_argument("slug")
    p_open.add_argument("--from", dest="from_path", help="a draft JSON to seed the record from")

    p_rewrite = sub.add_parser("rewrite")
    p_rewrite.add_argument("slug")
    p_rewrite.add_argument("--note", required=True)
    p_rewrite.add_argument("--merge", help="a JSON file whose top-level keys are folded in")

    p_close = sub.add_parser("close")
    p_close.add_argument("slug")

    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.command == "open":
        record, error = open_inflight(args.slug, args.from_path)
        if error:
            print(error, file=sys.stderr)
            return 2
        print("opened %s: turn %d" % (_path(args.slug), record["history"][-1]["turn"]))
        return 0

    if args.command == "rewrite":
        record, error = rewrite_inflight(args.slug, args.note, args.merge)
        if error:
            print(error, file=sys.stderr)
            return 2
        print("rewrote %s: turn %d" % (_path(args.slug), record["history"][-1]["turn"]))
        return 0

    if args.command == "close":
        dest, error = close_inflight(args.slug)
        if error:
            print(error, file=sys.stderr)
            return 2
        print("closed to %s" % dest)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
