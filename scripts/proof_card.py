#!/usr/bin/env python3
"""proof_card: the customer-facing Proof Card, projected from one run's own
records and nothing else (P0-D).

WHY THIS EXISTS. build_report and the acceptance screens (receipt_door.py,
brother_run.py) already say what a run proved, in the engine's own words, at
length. This is the one-screen version a customer reads first: a fixed set
of labelled rows, each one either a fact read straight off a record this run
already wrote, or the word NO-DATA. Nothing on this card is computed by a
model, and nothing on it is invented when the record it would come from is
missing: THE CARD NEVER UPGRADES UNCERTAINTY INTO SUCCESS. A field with
nothing behind it prints NO-DATA, not a flattering zero and not a guess.

WHAT IT READS, and does not write (no new persistent state; --out is the one
exception, and even then it writes a projection, never a new fact): the same
three files scripts/safe_unwatched_time.py already reads for the same
reason (journal.jsonl, claims.json, receipt/receipt.json), this run's own
Work document (W-*.json), and run.log's own printed isolation line. Every
reader here mirrors safe_unwatched_time.py's file-reading and NO-DATA
discipline (missing file: NO-DATA, not a crash; a malformed line: skipped
and counted, never fatal) rather than re-deriving it, and actually reuses
safe_unwatched_time.read_journal/read_claims and
journal_projection.claims_from_journal instead of re-parsing the same files
a second, possibly-inconsistent way.

PER-FILE PROOF VOCABULARY (addendum 2026-09-10 21:45 JST, the Codex read of
05-CODEX-ANSWER.md, binding over this file's earlier draft): each entry in
receipt.json["scope"]["changed"] (receipt_door.per_file_checks' own shape,
built at scripts/receipt_door.py:1169-1205 and serialized at 2288-2291)
reads PROVES CHANGE when its "state" is "verified" (receipts_for already
proved that exact check failed before the change and passed after, via
brother_run.py's own check_passed_before stamp, brother_run.py:3590-3598:
True means the check was already green before any work happened, False
means it was red, None/absent means never measured); GREEN ONLY when the
check passed (exit_code 0) but check_passed_before is True, so the pass is
real but proves nothing about THIS change; NO-DATA otherwise, carrying the
entry's own reason. This file never calls or mirrors
receipt_door._before_after_text (receipt_door.py:1646-1653): that helper's
own sentence maps True to "failed before", the reverse of the stamp's
meaning, and mixing the two vocabularies here would silently invert a label.
Every field is projected from the raw stamped fields instead.

codex_battery.receipt_proves_change (scripts/codex_battery.py:459) was read
per this file's own brief and is NOT called in the per-file path above: it
requires two or more scope.changed entries in one receipt before it will
call anything proven, which is a guard scoped to one specific codex battery
leg (a receipt covering a multi-file toy change), not a general per-file
discriminator, and calling it on a single file would need duplicating that
file's entry to satisfy a count check that has nothing to do with the file
itself. scripts/test_proof_card.py instead asserts the two discriminations
AGREE on a receipt that satisfies both (see TestReuseAgreesWithCodexBattery),
which is the honest form of "reuse it, do not rewrite it" once the addendum
fixed what the per-file rule actually is.

OUTCOME CLOSURE (P0-E), one function, outcome_status(), with its own tests.
A KNOWN LIMIT, stated rather than hidden (addendum, binding): the product's
own exit code (brother_run._exit_code_for, brother_run.py:2828-2857) returns
0 on any unit PASS with no refusal, and ignores the acceptance screen
entirely (brother_run.py:6102-6153). outcome_status() below does not call or
mirror that function; the two can and today do disagree, because the
process exit code answers "did unit dispatch go acceptably" while this
answers the four-way PASS/UNIT-COMPLETE/FAIL/NO-DATA question the card
prints. THE TOP-LEVEL CHECK ITSELF HAS NO WRITER YET IN THIS ESTATE: nothing
in this repository stamps a whole-run oracle result anywhere a run directory
keeps it, so for every run this estate has actually produced to date, "Top-
level oracle" reads NO-DATA and Outcome caps at UNIT-COMPLETE, never PASS.
The read below (a "oracle.recorded" journal event) is a forward-compatible
place to look, wired and tested, but nothing appends that event today.

Python 3, standard library only. No network.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import safe_unwatched_time as sut  # noqa: E402
import journal_projection  # noqa: E402

NODATA = "NO-DATA"

RECEIPT_RELPATH = os.path.join("receipt", "receipt.json")
RUN_LOG_FILENAME = "run.log"
JOURNAL_FILENAME = "journal.jsonl"
CLAIMS_FILENAME = "claims.json"

TITLE = "BROTHER VERIFIED AUTOPILOT"


# ---------------------------------------------------------------------------
# File reading. Each mirrors safe_unwatched_time.py: a missing file is
# NO-DATA, never a crash; a malformed file is treated as absent rather than
# raising through to the caller.

def _find_work_doc(run_dir):
    """The one W-*.json in `run_dir`, same rule as
    journal_projection._run_record_path (glob, sorted, first)."""
    paths = sorted(glob.glob(os.path.join(run_dir, "W-*.json")))
    return paths[0] if paths else None


def read_record(run_dir):
    """The Work document dict, or None when there is none or it will not
    parse as one."""
    path = _find_work_doc(run_dir)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def read_receipt(run_dir):
    """The receipt body at <run_dir>/receipt/receipt.json, or None when
    there is none or it will not parse as a dict."""
    path = os.path.join(run_dir, RECEIPT_RELPATH)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def read_run_log(run_dir):
    """run.log's own text, or "" when there is none. RunLog.note() writes
    this file verbatim (journal_projection.py's own docstring), so a
    re-read here is legitimate, not a reconstruction."""
    path = os.path.join(run_dir, RUN_LOG_FILENAME)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Per-file proof vocabulary (P0-D).

def file_proof_label(entry):
    """(label, reason) for one receipt.json scope.changed entry
    (receipt_door.per_file_checks' own shape: file, unit, check_command,
    exit_code, output_location, check_passed_before, state, reason).

    See the module docstring's PER-FILE PROOF VOCABULARY section: PROVES
    CHANGE when state == "verified"; GREEN ONLY when exit_code == 0 and
    check_passed_before is True; NO-DATA otherwise, carrying the entry's
    own reason. Never PASS: that word belongs to a check's own exit code,
    not to this receipt's projection of it.

    ORDER MATTERS, and this reads check_passed_before FIRST, ahead of the
    receipt's own "verified" state. That is the one place this file reads
    the addendum's label list as a set of rules rather than as a
    fallthrough, and it is deliberate: the two conditions can both hold on
    one entry. receipts_for only withholds "verified" from an already-green
    check when the unit declared change_kind "behaviour" (red_check_gap,
    receipt_door.py:525-549, scoped exactly there); a unit that declared no
    change_kind at all reaches state "verified" at receipt_door.py:1017-1018
    on exit_code 0 alone, with check_passed_before True sitting beside it.
    Taking "verified" first would then print PROVES CHANGE over a check that
    was ALREADY GREEN before any work happened, which is the precise thing
    this card exists not to do, and which this file's own brief names as a
    required test ("a check that was green before the change is counted
    GREEN ONLY not Proven"). check_passed_before True is a positive record
    that the check cannot tell the work from no work, so it caps the label
    at GREEN ONLY whatever else the entry carries. False and None never
    downgrade anything here: False is the ordinary case the receipt has
    already judged on its own, None is simply unmeasured."""
    if not isinstance(entry, dict):
        return NODATA, "not a receipt scope.changed entry"
    exit_code = entry.get("exit_code")
    exit_ok = (isinstance(exit_code, int) and not isinstance(exit_code, bool)
              and exit_code == 0)
    if exit_ok and entry.get("check_passed_before") is True:
        return "GREEN ONLY", entry.get("reason") or (
            "the check already passed before the work began, so this pass "
            "does not prove the change")
    if entry.get("state") == "verified":
        return "PROVES CHANGE", entry.get("reason") or ""
    return NODATA, entry.get("reason") or "no proof recorded for this file"


def changed_files(receipt):
    """[(label, reason, entry), ...] for every scope.changed entry the
    receipt carries, or [] when there is no receipt or it names none."""
    if not isinstance(receipt, dict):
        return []
    changed = (receipt.get("scope") or {}).get("changed")
    if not isinstance(changed, list):
        return []
    return [(label, reason, entry)
           for entry in changed
           for label, reason in (file_proof_label(entry),)]


# ---------------------------------------------------------------------------
# P0-E: outcome closure.

def outcome_status(records):
    """(outcome, top_level_oracle), both strings.

    `records` = {"rows": [Work document rows], "claims": {unit_id: claim},
    "top_level_check": {"exit_code": N} or None, "files": [(label, reason),
    ...], "quarantine_count": N}.

    NO-DATA, NO-DATA when rows or claims cannot even be read as their own
    shape (missing, empty rows, or a row/claims store that is not the shape
    this function needs) -- the "missing files, unparseable JSON" case named
    in the brief.

    Otherwise: FAIL when ANY required unit is terminal ("done" or "failed")
    without having reached "done" (which is this engine's own state for
    "merged": claim_store.release stamps the state the lane ended in, and
    only a merged lane ends "done"), or when a QUARANTINE verdict was ever
    recorded for this run (quarantine_count > 0) -- checked first and
    unconditionally, because a unit that was quarantined and later repaired
    into a clean "done" state still means this run needed a quarantine, and
    that is history a customer-facing card does not quietly drop.

    FAIL is per-unit, not "every unit finished and some did not merge": one
    unit that ended terminal and unintegrated is a definite failure of this
    run whatever its siblings are still doing, and waiting for the siblings
    before saying so would let a run in flight print something softer than
    the record already supports. This is the brief's own wording ("FAIL when
    any required unit is terminal without integration").

    NO-DATA (not FAIL, not a guess) when no unit has failed but some unit is
    simply not yet terminal: an in-progress run has not failed, and it has
    not finished either, so this refuses to call it either PASS or FAIL.

    Given every required unit terminal and integrated: PASS only when the
    top-level check is ALSO recorded with exit 0 and every changed file's
    proof status is not NO-DATA; UNIT-COMPLETE otherwise (units finished and
    merged, but the run cannot be called a full PASS, whether because no
    top-level check exists or because a file's own proof is missing).
    top_level_oracle reports PASS/NO-DATA on its own regardless of Outcome,
    since a customer reading "UNIT-COMPLETE, Top-level oracle PASS" needs to
    see the check DID pass even though a file kept the overall card short of
    PASS."""
    rows = records.get("rows")
    claims = records.get("claims")
    if not isinstance(rows, list) or not rows or not isinstance(claims, dict):
        return NODATA, NODATA
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            return NODATA, NODATA

    all_terminal = True
    any_terminal_unintegrated = False
    for row in rows:
        claim = claims.get(row["id"])
        state = claim.get("state") if isinstance(claim, dict) else None
        if state not in ("done", "failed"):
            all_terminal = False
        elif state != "done":
            any_terminal_unintegrated = True

    top = records.get("top_level_check")
    top_exit = top.get("exit_code") if isinstance(top, dict) else None
    top_ok = (isinstance(top_exit, int) and not isinstance(top_exit, bool)
             and top_exit == 0)
    oracle = "PASS" if top_ok else NODATA

    quarantine_count = records.get("quarantine_count") or 0
    if quarantine_count or any_terminal_unintegrated:
        return "FAIL", oracle
    if not all_terminal:
        return NODATA, oracle

    files = records.get("files")
    files = files if isinstance(files, list) else []
    files_ok = all(label != NODATA for label, *_rest in files)
    if top_ok and files_ok:
        return "PASS", oracle
    return "UNIT-COMPLETE", oracle


# ---------------------------------------------------------------------------
# The remaining card rows: each a small, independently NO-DATA-able reader.

def _epoch_interval(claim):
    """(start, end) aware datetimes from one claims.json entry's own
    claimed_at/released_at epoch floats, via safe_unwatched_time._epoch
    (the same conversion safe_unwatched_time.py itself uses); either half
    is None when the claim never carried it or it does not parse."""
    if not isinstance(claim, dict):
        return None, None
    return sut._epoch(claim.get("claimed_at")), sut._epoch(claim.get("released_at"))


def _max_overlap(intervals, open_end=None):
    """The largest number of `intervals` ((start, end) aware datetimes,
    end may be None for "still open") simultaneously live at any instant.
    A sweep line: +1 at each start, -1 at each end, sorted so an end at
    the same instant as a start is processed first (two intervals that
    only touch at one instant do not count as overlapping). An open
    interval (end None) is extended to `open_end` (typically the latest
    known end across every interval, or the run's own last event), never
    left unbounded."""
    events = []
    for start, end in intervals:
        if start is None:
            continue
        stop = end if end is not None else (open_end or start)
        events.append((start, 1))
        events.append((stop, -1))
    if not events:
        return None
    events.sort(key=lambda pair: (pair[0], pair[1]))
    current = peak = 0
    for _when, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def peak_concurrency(claims):
    """Max simultaneous live claims by claimed_at/released_at, or None
    when claims.json carries no unit with a usable claimed_at at all."""
    intervals = [_epoch_interval(c) for c in claims.values()]
    intervals = [(s, e) for s, e in intervals if s is not None]
    if not intervals:
        return None
    ends = [e for _s, e in intervals if e is not None]
    return _max_overlap(intervals, max(ends) if ends else None)


def units_progress(rows, claims):
    """(done, total). done counts rows whose claim state reads "done"."""
    total = len(rows)
    done = sum(1 for r in rows
              if isinstance(r, dict) and r.get("id")
              and isinstance(claims.get(r["id"]), dict)
              and claims[r["id"]].get("state") == "done")
    return done, total


def recoveries(claims, events):
    """(count, source) of units that needed a repair: either claims.json's
    own attempt > 1, or a claim.orphaned_by_kill journal event, whichever
    (or both) this run's records actually carry, named rather than
    guessed at."""
    from_claims = {uid for uid, c in claims.items()
                  if isinstance(c, dict)
                  and isinstance(c.get("attempt"), int)
                  and not isinstance(c.get("attempt"), bool)
                  and c["attempt"] > 1}
    from_kills = {e.get("unit_id") for e in (events or [])
                 if e.get("type") == "claim.orphaned_by_kill" and e.get("unit_id")}
    sources = []
    if from_claims:
        sources.append("claims attempt > 1")
    if from_kills:
        sources.append("claim.orphaned_by_kill events")
    return len(from_claims | from_kills), (", ".join(sources) or NODATA)


def integrated_units(claims):
    """Count of claims whose state is "done" -- the engine's own state for
    "this unit's lane actually merged". claim_store.release(..., state=...)
    (scripts/claim_store.py:526) stamps the state the lane ended in and never
    deletes the record, so "done" here is a merged lane and "failed" is a
    lane that closed without one."""
    return sum(1 for c in claims.values()
              if isinstance(c, dict) and c.get("state") == "done")


def integrated_serially(events):
    """True when every integrate.merged event carries a distinct
    timestamp (no two recorded merges landed at the same instant), None
    when fewer than two merges exist to compare, False when two collided.
    A proxy for "the merges never raced": git merges in this engine are
    inherently sequential (one process, one canonical branch), so two
    real merges sharing a microsecond-precision timestamp would itself be
    the anomaly worth surfacing rather than smoothing over."""
    if events is None:
        return None
    times = sorted(e["_at"] for e in events if e.get("type") == "integrate.merged")
    if len(times) < 2:
        return None
    return len(set(times)) == len(times)


def checks_rerunnable(claims):
    """Count of distinct check_command values among claims.json entries
    that also carry a captured exit_code -- a command with no exit code
    was never actually run to a verdict, so it does not count as
    rerunnable evidence."""
    commands = set()
    for c in claims.values():
        if not isinstance(c, dict):
            continue
        evidence = c.get("evidence")
        if not isinstance(evidence, dict):
            continue
        exit_code = evidence.get("exit_code")
        command = str(evidence.get("check_command") or "").strip()
        if command and isinstance(exit_code, int) and not isinstance(exit_code, bool):
            commands.add(command)
    return len(commands)


def scope_violations(events):
    """Count of integrate.refused events whose reason names a quarantine
    (case-insensitive "quarantine", the same word brother_run.PLAIN_VERDICTS
    prints and safe_unwatched_time._refusal_kind already keys on). None
    when there is no journal to read at all -- 0 is only printed when a
    journal was actually read and held none."""
    if events is None:
        return None
    return sum(1 for e in events if e.get("type") == "integrate.refused"
              and "quarantine" in str((e.get("payload") or {}).get("reason")
                                      or "").lower())


def _unit_attempt_intervals(events):
    """{unit_id: [(start, end_or_None), ...]}, pairing each claim.acquired
    with the NEXT claim.released for the same unit_id, oldest first (a
    unit reclaimed after a crash gets a second pair; an acquire with no
    matching release yet is left open, end None)."""
    open_by_unit = {}
    out = {}
    for event in events:
        uid = event.get("unit_id")
        if uid is None:
            continue
        etype = event.get("type")
        if etype == "claim.acquired":
            open_by_unit.setdefault(uid, []).append(event["_at"])
            out.setdefault(uid, [])
        elif etype == "claim.released":
            pending = open_by_unit.get(uid)
            start = pending.pop(0) if pending else None
            if start is not None:
                out.setdefault(uid, []).append((start, event["_at"]))
    for uid, starts in open_by_unit.items():
        for start in starts:
            out.setdefault(uid, []).append((start, None))
    return out


def duplicate_work(events):
    """Count of units whose OWN claim/release history (from the journal,
    not claims.json, which keeps only the latest attempt per unit) shows
    two or more overlapping live intervals: the same unit claimed twice
    at once, a race this engine's single-owner claim store should never
    allow. None when there is no journal to reconstruct that history
    from -- "0 only when provable" (the brief's own words): a run with no
    journal cannot be shown clean, so it is not reported as one."""
    if events is None:
        return None
    per_unit = _unit_attempt_intervals(events)
    ends = [e for intervals in per_unit.values()
           for _s, e in intervals if e is not None]
    open_end = max(ends) if ends else None
    return sum(1 for intervals in per_unit.values()
              if len(intervals) > 1
              and (_max_overlap(intervals, open_end) or 0) > 1)


def wall_clock(events):
    """Seconds from the first to the last journal event, or None when
    there are fewer than two (nothing to span)."""
    if not events or len(events) < 2:
        return None
    return (events[-1]["_at"] - events[0]["_at"]).total_seconds()


def sequential_estimate(claims):
    """Sum of claimed_at..released_at seconds across every claim that
    carries both, or None when none does."""
    total = 0.0
    counted = False
    for c in claims.values():
        start, end = _epoch_interval(c)
        if start is not None and end is not None:
            total += (end - start).total_seconds()
            counted = True
    return total if counted else None


def human_interrupts(events):
    """Count of journal events naming a human moment, or None. This
    estate has not yet written any event type carrying "human" in its
    name (checked against every writer in scripts/brother_run.py at the
    time this was written), so this reads NO-DATA on every run today,
    honestly, rather than a flattering zero for something nothing
    records."""
    if not events:
        return None
    count = sum(1 for e in events if "human" in str(e.get("type") or "").lower())
    return count if count else None


def read_top_level_check(events):
    """{"exit_code": N, "command": ...} from the last "oracle.recorded"
    journal event, or None. NOTHING IN THIS ESTATE WRITES THIS EVENT
    TODAY (see the module docstring): this is a forward-compatible read,
    wired and tested, waiting for a writer. Every real run this estate
    has produced reads None here, which the card renders as NO-DATA."""
    if not events:
        return None
    for event in reversed(events):
        if event.get("type") == "oracle.recorded":
            payload = event.get("payload") or {}
            exit_code = payload.get("exit_code")
            if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                return {"exit_code": exit_code,
                       "command": payload.get("command") or NODATA}
    return None


def execution_mode(events, run_log_text):
    """"AUTOPILOT xN" / "CONTROLLED xN" / "DIRECT" / None (NO-DATA).

    First a "dispatch.mode" journal event, if one is ever written (no
    writer emits this today; forward-compatible, like read_top_level_check
    above). Otherwise this falls back to what IS on disk for every real
    run: the latest dispatch.round event's own "slots" (the in-flight
    cap loop_bridge.py hands out), and the LAST "isolation: ..." line
    run.log carries verbatim (loop_bridge.py:1630 prints exactly
    "isolation: per-writer worktrees" or "isolation: NOT established...").
    Isolated with a cap reads AUTOPILOT (worktrees, unattended-safe);
    not isolated with more than one slot reads CONTROLLED (concurrency
    without per-writer isolation); not isolated with one slot reads
    DIRECT. Either signal missing (no dispatch.round, or no isolation
    line ever printed) is NO-DATA: this never guesses a mode from half a
    fact."""
    for event in reversed(events or []):
        if event.get("type") == "dispatch.mode":
            payload = event.get("payload") or {}
            mode = str(payload.get("mode") or "").strip()
            cap = payload.get("cap")
            if mode:
                return "%s x%s" % (mode, cap) if cap else mode
    cap = None
    for event in reversed(events or []):
        if event.get("type") == "dispatch.round":
            slots = (event.get("payload") or {}).get("slots")
            if isinstance(slots, int) and not isinstance(slots, bool):
                cap = slots
                break
    isolated = None
    for line in reversed((run_log_text or "").splitlines()):
        if line.startswith("isolation:"):
            isolated = "per-writer worktrees" in line
            break
    if cap is None or isolated is None:
        return None
    if isolated:
        return "AUTOPILOT x%d" % cap
    if cap > 1:
        return "CONTROLLED x%d" % cap
    return "DIRECT"


# ---------------------------------------------------------------------------
# Assembly and printing.

def _fmt(value, template="%s"):
    return NODATA if value is None else (template % value)


def build_card(run_dir):
    """{field_name: {"value": str, "source": str}}, in print order.
    Every value is a rendered string (NO-DATA where the fact is absent);
    every source names what this run's records the value came from, for
    --json's own traceability contract (T20)."""
    record = read_record(run_dir)
    rows = (record.get("rows") or record.get("units") or []) if record else []

    journal_path = os.path.join(run_dir, JOURNAL_FILENAME)
    has_journal = os.path.isfile(journal_path)
    events, skipped = sut.read_journal(run_dir)
    events_or_none = events if has_journal else None

    claims_path = os.path.join(run_dir, CLAIMS_FILENAME)
    has_claims = os.path.isfile(claims_path)
    claims = sut.read_claims(run_dir) if has_claims else {}
    claims_effective = claims if has_claims else (
        journal_projection.claims_from_journal(events) if has_journal else {})

    receipt = read_receipt(run_dir)
    receipt_path = os.path.join(run_dir, RECEIPT_RELPATH)
    run_log_text = read_run_log(run_dir)

    file_rows = changed_files(receipt)
    top_level_check = read_top_level_check(events_or_none)
    quarantine = scope_violations(events_or_none)

    records = {
        "rows": rows,
        "claims": claims_effective,
        "top_level_check": top_level_check,
        "files": [(label, reason) for label, reason, _entry in file_rows],
        "quarantine_count": quarantine or 0,
    }
    outcome, oracle = outcome_status(records) if rows else (NODATA, NODATA)

    done, total = units_progress(rows, claims_effective) if rows else (None, None)
    rec_count, rec_source = recoveries(claims_effective, events_or_none)
    integ_count = integrated_units(claims_effective)
    serial = integrated_serially(events_or_none)

    proven = sum(1 for label, _r, _e in file_rows if label == "PROVES CHANGE")
    green = sum(1 for label, _r, _e in file_rows if label == "GREEN ONLY")
    no_data_files = sum(1 for label, _r, _e in file_rows if label == NODATA)
    files_total = len(file_rows) if receipt is not None else None

    fields = {}

    def put(key, value, source):
        fields[key] = {"value": value, "source": source}

    put("outcome", outcome,
       "outcome_status() over rows in the Work document, claims.json/"
       "journal claim state, receipt.json per-file proof, and integrate."
       "refused quarantine reasons")
    put("top_level_oracle", oracle,
       "oracle.recorded journal event exit_code (no writer emits this "
       "event today, so this reads NO-DATA on every run this estate has "
       "produced)")
    put("execution", _fmt(execution_mode(events_or_none, run_log_text)),
       "dispatch.mode journal event, else the last dispatch.round slots "
       "and the last 'isolation:' line in run.log")
    put("peak_concurrency",
       _fmt(peak_concurrency(claims), "%d workers"),
       "claims.json claimed_at/released_at intervals")
    put("units", _fmt("%d/%d" % (done, total) if rows else None),
       "claim state 'done' per row, against rows in the Work document")
    put("recoveries", "%d (%s)" % (rec_count, rec_source),
       "claims attempt counts and/or claim.orphaned_by_kill journal events")
    put("integrated",
       ("%d units, serially" % integ_count if serial
        else _fmt(integ_count, "%d units")),
       "claims state 'done' count; integrate.merged event timestamps for "
       "the 'serially' qualifier")
    put("files_changed", _fmt(files_total),
       "receipt.json scope.changed")
    put("proven", _fmt(proven if receipt is not None else None),
       "receipt.json scope.changed entries labelled PROVES CHANGE")
    put("green_only", _fmt(green if receipt is not None else None),
       "receipt.json scope.changed entries labelled GREEN ONLY")
    put("no_data_files", _fmt(no_data_files if receipt is not None else None),
       "receipt.json scope.changed entries labelled NO-DATA")
    put("checks_rerunnable", _fmt(checks_rerunnable(claims), "%d rerunnable"),
       "claims.json evidence.check_command with a captured exit_code")
    put("scope_violations", _fmt(quarantine),
       "journal.jsonl integrate.refused events naming a quarantine")
    put("duplicate_work", _fmt(duplicate_work(events_or_none)),
       "journal.jsonl claim.acquired/claim.released intervals per unit")
    put("wall_clock", _fmt(wall_clock(events_or_none), "%.1f sec"),
       "first and last journal.jsonl event timestamp")
    seq = sequential_estimate(claims)
    put("sequential_est",
       _fmt(seq, "%.1f sec (measured from unit durations)"),
       "claims.json claimed_at/released_at per unit, summed")
    put("human_interrupts", _fmt(human_interrupts(events_or_none)),
       "journal.jsonl event types naming a human moment (none exist in "
       "this estate's vocabulary yet)")
    put("acceptance", "waiting for you",
       "constant: a receipt is never converted into acceptance by this card")
    put("receipt", receipt_path if os.path.isfile(receipt_path) else NODATA,
       "receipt/receipt.json under the run directory")
    return fields


CARD_ORDER = [
    ("outcome", "Outcome"),
    ("top_level_oracle", "Top-level oracle"),
    ("execution", "Execution"),
    ("peak_concurrency", "Peak concurrency"),
    ("units", "Units"),
    ("recoveries", "Recoveries"),
    ("integrated", "Integrated"),
    ("files_changed", "Files changed"),
    ("proven", "Proven"),
    ("green_only", "Green only"),
    ("no_data_files", "NO-DATA"),
    ("checks_rerunnable", "Checks"),
    ("scope_violations", "Scope violations"),
    ("duplicate_work", "Duplicate work"),
    ("wall_clock", "Wall clock"),
    ("sequential_est", "Sequential est."),
    ("human_interrupts", "Human interrupts"),
    ("acceptance", "Acceptance"),
    ("receipt", "Receipt"),
]


def render_text(fields):
    lines = [TITLE]
    width = max(len(label) for _key, label in CARD_ORDER) + 1
    for key, label in CARD_ORDER:
        lines.append("%-*s %s" % (width, label, fields[key]["value"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", help="a run directory")
    ap.add_argument("--json", action="store_true",
                    help="print the card as {field: {value, source}} JSON "
                         "instead of the plain-text card")
    ap.add_argument("--out", action="store_true",
                    help="also write proof_card.txt and proof_card.json "
                         "under the run directory")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.run_dir):
        print("%s: %s is not a directory" % (NODATA, args.run_dir))
        return 2

    fields = build_card(args.run_dir)
    text = render_text(fields)
    if args.json:
        print(json.dumps(fields, indent=1, sort_keys=True))
    else:
        print(text)
    if args.out:
        with open(os.path.join(args.run_dir, "proof_card.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(text + "\n")
        with open(os.path.join(args.run_dir, "proof_card.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(fields, fh, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
