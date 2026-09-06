#!/usr/bin/env python3
"""bm_vault_ledger: read and audit the answer ledger (VB2-05).

WHY THIS EXISTS. A Purview-grade audit needs to answer, after the fact: what did the AI
actually read to produce THIS answer, under whose identity, and is that source still what
it was then? bm_vault.py's own `recall` command now appends one JSON line per recall to
the ledger (tools/bm_vault.py's `_append_ledger`, written from the served hits only --
never a withheld or superseded note, because those never reached the reader). This module
is the READ side of the LEDGER: it never writes a ledger row, it only reports on one. VB6-03
adds `outcome`, the one exception: this module is the sole writer of the separate telemetry
outcome file (bm_vault_outcomes.jsonl), never the ledger itself, so the "never a second
writer" guarantee below still holds for the ledger's own format.

  show --last N     the N most recent recall rows, oldest of the tail first.
  replay --ts X      picks one ledger row (X is the row's exact `ts` string, a UNIQUE ts
                     prefix, or an integer index with Python list semantics: 0 oldest, -1
                     most recent), then re-reads each served hit's `path` off disk RIGHT
                     NOW and compares its hash to the `content_sha256` recorded at recall
                     time: MATCHES, CHANGED SINCE, or GONE. This is what makes a past
                     answer auditable to its exact sources, not just to a query string.
  census            row count, timestamp span, distinct identities seen.
  outcome --event-id ID --result TEXT
                     VB6-03: append one telemetry outcome record to the outcome file
                     (bm_vault_outcomes.jsonl, sitting beside the ledger), referencing an
                     answer-event id minted by bm_vault.py's recall. Append-only, same
                     O_APPEND atomic-write contract as the ledger itself.
  join --event-id ID
                     VB6-03: resolve one ledger row and every outcome recorded against it,
                     by event_id ALONE -- this never compares a timestamp between the two
                     stores, so a synthetic clock skew between them changes nothing about
                     whether the join resolves. NO-DATA when the id is in neither store.

REPORT-ONLY ON ERASURE (mirrors bm_vault_retention.py's Failures-Index treatment; see that
module's own docstring for the symmetric half of this decision). This tool never deletes
or edits a ledger row, even one that cites a note the retention tool has just erased from
the retrieval index. The ledger's entire value is being an honest record of what a past
answer actually read AT THE TIME; rewriting a row after the fact to match a later deletion
would make the record lie about history instead of report it. bm_vault_retention.py's
`propagate` names the ledger as a MANUAL follow-up for exactly this reason: a human decides
whether a historical citation of a now-gone note still deserves to stand, this tool never
decides that on its own.

NO-DATA on an absent or empty ledger file, never a pass: an audit tool that reports a clean
answer for "nothing has ever been written here" would hide the difference between "verified
clean" and "never checked."

Exit codes: 0 clean/found (join: outcome-less but ledger row present), 1 findings (replay
found a change/gone source; join found outcomes but no ledger row; outcome write failed),
2 NO-DATA (or a bad flag). Python 3.9, standard library only. Never writes the ledger:
bm_vault.py's recall path is its only writer, so this module cannot become a second writer
disagreeing about its format; `outcome` writes only the separate outcome file above.
"""
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402

LEDGER_PATH = brother_paths.config_path("bm_vault_answers.jsonl")
# VB6-03: the telemetry outcome file, sitting beside the ledger it references by
# event_id. A separate file, same as the access audit is separate from the ledger: an
# outcome ("the founder rejected this answer") is a different kind of record from what
# was read to produce it, and the two must stay independently appendable.
OUTCOME_PATH = os.path.join(os.path.dirname(LEDGER_PATH), "bm_vault_outcomes.jsonl")


def _read_rows():
    """All ledger rows in file order (oldest first), or None when the file is absent or
    carries no readable row. A malformed line (a partial write from a crash mid-append) is
    skipped with a stderr warning rather than aborting the whole read, so one bad line
    cannot blind every other row in the ledger."""
    if not os.path.exists(LEDGER_PATH):
        return None
    rows = []
    with open(LEDGER_PATH, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                sys.stderr.write("bm_vault_ledger: skipping malformed line %d (%s)\n" % (i, e))
    return rows or None


def cmd_show(args):
    rows = _read_rows()
    if rows is None:
        print("NO-DATA: no answer ledger at %s (no recall has written one yet)" % LEDGER_PATH)
        return 2
    n = int(args.get("last", 10))
    tail = rows[-n:] if n > 0 else rows
    start = len(rows) - len(tail)
    for i, row in enumerate(tail):
        print("\n[%d] %s  identity=%s  mode=%s"
              % (start + i, row.get("ts", "?"), row.get("identity", "?"), row.get("mode", "?")))
        print("  query: %s" % row.get("query", ""))
        hits = row.get("hits") or []
        if not hits:
            print("  (no hits served)")
        for h in hits:
            print("  hit id=%s  sha256=%s  %s"
                  % (h.get("id"), (h.get("content_sha256") or "")[:12], h.get("path")))
    return 0


def _resolve_row(rows, token):
    """(row, index) for a token that is an integer index (Python list semantics), an exact
    `ts` string, or a unique `ts` prefix -- tried in that order. (None, None) with nothing
    decided when none of the three resolves to exactly one row; the caller reports why."""
    try:
        idx = int(token)
        return rows[idx], idx % len(rows)
    except (ValueError, IndexError):
        pass
    exact = [(i, r) for i, r in enumerate(rows) if r.get("ts") == token]
    if len(exact) == 1:
        return exact[0][1], exact[0][0]
    prefix = [(i, r) for i, r in enumerate(rows) if str(r.get("ts", "")).startswith(token)]
    if len(prefix) == 1:
        return prefix[0][1], prefix[0][0]
    return None, None


def cmd_replay(args):
    token = args.get("ts")
    if not token or token is True:
        sys.stderr.write("bm_vault_ledger: replay needs --ts <timestamp-or-index>\n")
        return 2
    rows = _read_rows()
    if rows is None:
        print("NO-DATA: no answer ledger at %s" % LEDGER_PATH)
        return 2
    row, idx = _resolve_row(rows, token)
    if row is None:
        print("NO-DATA: %r does not resolve to exactly one ledger row (not a valid index, "
              "and not an exact or unique ts prefix)" % token)
        return 2
    print("replaying [%d] %s  identity=%s  mode=%s"
          % (idx, row.get("ts"), row.get("identity"), row.get("mode")))
    print("  query: %s" % row.get("query", ""))
    hits = row.get("hits") or []
    if not hits:
        print("  (no hits were served for this recall)")
        return 0
    changed = 0
    for h in hits:
        path, recorded, hid = h.get("path"), h.get("content_sha256"), h.get("id")
        if not path or not os.path.exists(path):
            print("  GONE           id=%s  %s" % (hid, path))
            changed += 1
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                now_hash = hashlib.sha256(f.read().encode("utf-8")).hexdigest()
        except OSError as e:
            print("  GONE           id=%s  %s (unreadable: %s)" % (hid, path, e))
            changed += 1
            continue
        if now_hash == recorded:
            print("  MATCHES        id=%s  %s" % (hid, path))
        else:
            print("  CHANGED SINCE  id=%s  %s" % (hid, path))
            changed += 1
    print("%d of %d source(s) changed or gone since this answer was served" % (changed, len(hits)))
    return 1 if changed else 0


def cmd_census(args):
    rows = _read_rows()
    if rows is None:
        print("NO-DATA: no answer ledger at %s" % LEDGER_PATH)
        return 2
    identities = sorted({r.get("identity", "unset") for r in rows})
    span = "%s to %s" % (rows[0].get("ts", "?"), rows[-1].get("ts", "?"))
    total_hits = sum(len(r.get("hits") or []) for r in rows)
    print("rows: %d  span: %s  identities seen: %s  total hits recorded: %d"
          % (len(rows), span, ", ".join(identities), total_hits))
    return 0


def _read_outcome_rows():
    """Same three-way contract as _read_rows above (None absent, list-or-None malformed-
    skip), for the separate outcome file."""
    if not os.path.exists(OUTCOME_PATH):
        return None
    rows = []
    with open(OUTCOME_PATH, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                sys.stderr.write("bm_vault_ledger: skipping malformed outcome line %d (%s)\n" % (i, e))
    return rows or None


def cmd_outcome(args):
    """VB6-03: append one telemetry outcome record referencing an answer-event id minted
    by bm_vault.py's recall. AVAILABILITY OVER BOOKKEEPING, the same stance the ledger's
    own writer takes: a write failure prints a warning and returns a non-zero exit rather
    than raising, since this command has no recall of its own left to protect."""
    event_id = args.get("event-id")
    result = args.get("result")
    if not isinstance(event_id, str) or not event_id or not isinstance(result, str) or not result:
        sys.stderr.write("bm_vault_ledger: outcome needs --event-id ID --result TEXT\n")
        return 2
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_id": event_id,
        "result": result,
    }
    try:
        line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        # 0o600: sits beside LEDGER_PATH and shares its "same O_APPEND atomic-write
        # contract" (module docstring above), so it shares the permission too -- a
        # rejection `result` can itself quote the sensitive query it is about.
        fd = os.open(OUTCOME_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write("bm_vault_ledger: outcome write failed (%s)\n" % e)
        return 1
    print("recorded outcome for event %s" % event_id)
    return 0


def cmd_join(args):
    """VB6-03: resolve the ledger row and every outcome recorded for one event_id. The
    join key is event_id ALONE -- neither this function nor its caller ever reads a `ts`
    field to decide the match, so a clock skew between the ledger and the outcome file
    (different writers, different moments) changes nothing about whether this resolves."""
    event_id = args.get("event-id")
    if not isinstance(event_id, str) or not event_id:
        sys.stderr.write("bm_vault_ledger: join needs --event-id ID\n")
        return 2
    ledger_row = None
    for row in (_read_rows() or []):
        if row.get("event_id") == event_id:
            ledger_row = row
            break
    outcomes = [o for o in (_read_outcome_rows() or []) if o.get("event_id") == event_id]
    if ledger_row is None and not outcomes:
        print("NO-DATA: event %s found in neither the ledger nor the outcomes" % event_id)
        return 2
    if ledger_row is None:
        print("outcome(s) found but no matching ledger row for event %s" % event_id)
        return 1
    print("ledger row: ts=%s query=%s" % (ledger_row.get("ts"), ledger_row.get("query")))
    if not outcomes:
        print("no outcome recorded yet for event %s" % event_id)
        return 0
    for o in outcomes:
        print("outcome: ts=%s result=%s" % (o.get("ts"), o.get("result")))
    return 0


def _parse(argv):
    args, key = {}, None
    for a in argv:
        if a.startswith("--"):
            key = a[2:]
            args.setdefault(key, True)
        elif key:
            args[key] = a
            key = None
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fns = {"show": cmd_show, "replay": cmd_replay, "census": cmd_census,
           "outcome": cmd_outcome, "join": cmd_join}
    if argv[0] not in fns:
        sys.stderr.write("bm_vault_ledger: unknown command %r; known: %s\n"
                         % (argv[0], ", ".join(sorted(fns))))
        return 2
    return fns[argv[0]](_parse(argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
