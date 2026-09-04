#!/usr/bin/env python3
"""journal_projection: the delivery report, receipts-bound and a live
status fold, each rebuilt from <run_dir>/journal.jsonl (scripts/journal.py)
instead of from claims.json, proven byte for byte against the writer that
computes the same fact today. Row E60 under the founder's ruling of
2026-09-03 19:08 (docs/decisions/canonical-state-journal-first-2026-09-03
.json): "one journal per run first, projections proven, absorption second".

WHAT THIS DOES NOT DO, following journal.py's own docstring exactly: "the
claim store still owns claims.json, the Work document is still the
scheduler's truth, the run log still carries the engine's narration."
Nothing here stops reading the Work document (record) directly, nor the git
before/after/changed range, nor the loop's captured text: none of those was
ever duplicated into a journal payload (the payloads are deliberately terse,
per journal.py's PIPE_BUF discussion), so none of them can or should be
"read from the journal" instead. The one fact build_report and
receipts_bound each read from claims.json that the journal ALSO now
answers, from claim.acquired/claim.released events alone, is which state a
unit's claim ended in. That is the one read this module replaces.

Python 3, standard library only. No network.
"""
import argparse
import difflib
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_run  # noqa: E402
import journal  # noqa: E402

NODATA = "NO-DATA"


def claims_from_journal(events):
    """{unit_id: {"state": ..., "attempt": ..., "evidence": {"exit_code":
    ...}}}, rebuilt from claim.acquired/claim.released events alone.

    build_report reads `claims` two ways, not one: directly, for
    `held.get("state")` when a unit was never integrated; and indirectly,
    by handing the whole dict to receipt_door.receipts_for() (build_report's
    own call, line ~1114) for the "verified by" / NO-DATA sentence per
    delivered unit. receipts_for reads `claim["evidence"]["exit_code"]`
    (falling back to `row["done_check"]` for the command itself when
    evidence carries none, which is exactly what happens here). claim.
    released's own payload already carries `check_exit` -- the one field of
    the claim's evidence a journal line is small enough to hold -- so this
    is not new data, only reshaping what the event already recorded into
    the shape receipts_for expects. The full evidence (output, the
    command text) is NEVER reconstructed: it was never journaled (see the
    module docstring), and receipts_for does not need it to reach the same
    verdict.

    A unit released more than once (a resumed run re-claiming after an
    abandon) keeps its LAST released state, the same "latest wins"
    claims.json itself already gives a repeated acquire/release pair on one
    unit id. A unit acquired but never released (abandoned mid-run, the
    crash claim_store.reconcile() later finds) is left in state "claimed",
    matching claim_store.acquire()'s own initial stamp, with no evidence.
    A unit with no claim event at all is simply absent, matching a fresh
    claims.json that never held it."""
    out = {}
    for event in events or ():
        uid = event.get("unit_id")
        if uid is None:
            continue
        etype = event.get("type")
        payload = event.get("payload") or {}
        if etype == "claim.acquired":
            out[uid] = {"state": "claimed", "attempt": payload.get("attempt")}
        elif etype == "claim.released":
            entry = {"state": payload.get("state"),
                    "attempt": payload.get("attempt")}
            if payload.get("check_exit") is not None:
                entry["evidence"] = {"exit_code": payload.get("check_exit")}
            out[uid] = entry
    return out


def build_report_from_journal(record, run_dir, before, after, changed=None,
                              log_path=None, loop_text="", cost_block=None):
    """(report, integrated, refused): brother_run.build_report(record,
    claims, ...), except `claims` is rebuilt from <run_dir>/journal.jsonl
    instead of read from claims.json, so a caller with no claims.json at
    all (deleted, or a run made by a harness that never wrote one) still
    gets the same report a claims.json-backed call would have produced. The
    other parameters are exactly build_report's own: they are not
    journal-derived (see the module docstring) and every existing caller
    supplies them the same way.

    A run with no journal at all (predates row E59, or the journal could
    not be written) rebuilds claims as {}, the same empty starting point
    claims.json itself would give a run nothing has claimed yet."""
    events = journal.read(run_dir)
    claims = claims_from_journal(events if events is not None else ())
    return brother_run.build_report(record, claims, before, after, changed,
                                    log_path=log_path, loop_text=loop_text,
                                    cost_block=cost_block)


def last_event_per_unit(run_dir):
    """{unit_id: last_event}, the fold a live status line needs while a run
    is still going: not every event about a unit, only the most recent, in
    the journal's own (file) order -- which is also arrival order, since
    journal.append is a plain O_APPEND. None (NO-DATA) when the run has no
    journal at all, distinct from {} (a run with a journal that has not
    named a unit yet, e.g. still inside door's decomposition)."""
    events = journal.read(run_dir)
    if events is None:
        return None
    out = {}
    for event in events:
        uid = event.get("unit_id")
        if uid is None:
            continue
        out[uid] = event
    return out


def _run_record_path(run_dir):
    wfiles = sorted(glob.glob(os.path.join(run_dir, "W-*.json")))
    return wfiles[0] if wfiles else None


def diff_report_for_run(run_dir):
    """(status, detail): status is "clean" (the journal-built report is
    byte-identical to the claims.json-built one, detail ""), "diff" (a real
    difference, detail the unified diff), or "no-data" (nothing this run
    holds lets the projection be checked at all, detail says what is
    missing).

    THE COMPARISON THIS CAN ACTUALLY MAKE, and the one it cannot: `before`,
    `after` and `changed` are never persisted anywhere a COMPLETED run can
    be re-read for (brother_run.py computes them live from git at report
    time and never stamps them onto the record; see the module docstring on
    what the journal deliberately does not duplicate), so a run directory
    found after the fact cannot recover the exact values its own original
    report used. Both sides of this diff are built with the SAME
    placeholder values instead (None, None, []), which keeps the comparison
    honest: it isolates exactly the one thing this module changes (claims
    from the journal instead of from claims.json) rather than pretending to
    reproduce a historical git range nothing recorded. `loop_text` is read
    from the run's own run.log, which RunLog.note() writes VERBATIM (its
    own docstring), so it is a legitimate re-read, not a reconstruction."""
    journal_path = os.path.join(run_dir, journal.JOURNAL_FILENAME)
    if not os.path.isfile(journal_path):
        return "no-data", ("no %s (this run predates row E59, or the "
                           "journal could not be written)"
                           % journal.JOURNAL_FILENAME)
    record_path = _run_record_path(run_dir)
    if not record_path:
        return "no-data", "no W-*.json Work document in this run directory"
    claims_path = os.path.join(run_dir, "claims.json")
    if not os.path.isfile(claims_path):
        return "no-data", "no claims.json to compare the projection against"
    try:
        with open(record_path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        return "no-data", "could not read %s: %s" % (record_path, exc)
    try:
        with open(claims_path, encoding="utf-8") as fh:
            real_claims = json.load(fh)
    except (OSError, ValueError) as exc:
        return "no-data", "could not read %s: %s" % (claims_path, exc)
    log_path = os.path.join(run_dir, brother_run.LOG_FILENAME)
    loop_text = ""
    if os.path.isfile(log_path):
        try:
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                loop_text = fh.read()
        except OSError as exc:
            return "no-data", "could not read %s: %s" % (log_path, exc)
    before = after = None
    changed = []
    real_report, _real_i, _real_r = brother_run.build_report(
        record, real_claims, before, after, changed,
        log_path=log_path, loop_text=loop_text)
    proj_report, _proj_i, _proj_r = build_report_from_journal(
        record, run_dir, before, after, changed,
        log_path=log_path, loop_text=loop_text)
    if real_report == proj_report:
        return "clean", ""
    diff = "\n".join(difflib.unified_diff(
        real_report.splitlines(), proj_report.splitlines(),
        fromfile="claims.json-based", tofile="journal-based", lineterm=""))
    return "diff", diff


def _cmd_diff(runs_root):
    """Every run directory directly under `runs_root`, diffed. Prints
    nothing for a run whose projection is byte-identical; one NO-DATA line
    naming the run and why for a run nothing can be checked against, never
    silently folded into a pass; the unified diff for a real mismatch.
    Returns 0 when at least one run was checked and every checked run was
    identical, 1 when any run showed a real diff, 2 (NO-DATA) when the root
    itself is missing, empty, or every run in it was NO-DATA."""
    if not os.path.isdir(runs_root):
        print("%s: no such runs root: %s" % (NODATA, runs_root))
        return 2
    names = sorted(name for name in os.listdir(runs_root)
                   if os.path.isdir(os.path.join(runs_root, name)))
    if not names:
        print("%s: %s holds no run directories" % (NODATA, runs_root))
        return 2
    any_diff = False
    any_clean = False
    for name in names:
        status, detail = diff_report_for_run(os.path.join(runs_root, name))
        if status == "no-data":
            print("%s: %s: %s" % (NODATA, name, detail))
        elif status == "diff":
            print("DIFF: %s" % name)
            print(detail)
            any_diff = True
        else:
            any_clean = True
    if any_diff:
        return 1
    if any_clean:
        return 0
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--diff", metavar="RUNS_ROOT",
                    help="diff the journal-built report against the "
                         "claims.json-built one for every run directory "
                         "under RUNS_ROOT")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.diff:
        return _cmd_diff(args.diff)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
