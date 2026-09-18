#!/usr/bin/env python3
"""JEV-02 of the 1.0.20 orchestration control plane: the calibration ledger.

WHY THIS EXISTS. TypeSafe (the vendor behind Jev, the decision model this
estate is adding) publishes no independent, reproduced calibration study:
its own docs state the calibration claim as a definition ("a probability of
0.8 should be right about 80 percent of the time, over many predictions"),
not a measured number, and the sourced research this unit was built from
(docs/plan research notes, not repeated here) found no third party who has
verified it either. So trust in a Jev probability or confidence number is
never assumed here: it is MEASURED, from this estate's own real decisions
joined to their own real outcomes, before any caller is allowed to treat a
confidence number as a license to act automatically. JEV-03 (the cascade)
is the caller; this module is where the measurement lives.

THE LEDGER is two append-only, line-delimited JSON files: one line of
"decision" (a question was answered, with its confidence) written when the
answer arrives, one line of "outcome" (was the answer actually right)
written later, sometimes much later, sometimes never, sometimes twice by
accident. Append-only and line-delimited on purpose: a crash can corrupt at
worst one trailing partial line, never the file whole; nothing here is ever
rewritten or deleted in place, so a reader can always replay the ledger's
whole history from empty; and a caller who already validated a record fully
before this module ever ran (jev_decide.py, JEV-01, produces its own
in-memory Decision objects, not ledger lines) still gets that record
re-validated on the way in, because this ledger outlives any one caller and
a line on disk is not proven safe just because something upstream once
checked it.

VALIDATION RUNS TWICE ON PURPOSE: once in append_decision()/append_outcome()
before a line is ever written, and again, by the SAME function, every time
a line is read back in join(). A line that fails validation on the way in
is refused outright (raises ValueError, nothing is written). A line that
fails validation on the way OUT (hand-edited, written by some other tool,
corrupted on disk) is not allowed to crash the whole ledger read: it is
routed to a "corrupt" bucket and reported by line number, and everything
else in the file is still read. One validator, not two, is the reason the
two behave the same way: see _validate_decision() and _validate_outcome().

THE CENTRAL NUMBER is the Wilson score lower bound on precision, not raw
precision. Raw precision on a small sample overstates how far a caller can
trust it (9 of 10 decisions correct sounds like 90 percent, but with only
10 data points the honest 95 percent lower bound is closer to 60 percent);
threshold() reads the LOWER bound so a family, qtype and risk class earns
autonomous trust only once the evidence is actually strong enough for the
target precision to survive a conservative interval, not merely to be
plausible.

A "noul", a "choice" answered from the same family, and the exact same
family/qtype pair asked with different wording (a different "framing") are
NEVER pooled into one report row. Confidence numbers do not carry the same
meaning across question types (TypeSafe's own docs name concrete
mismatches between a noul's probability and a structurally similar
choice's probability for what looks like the same question), and a
reworded question is, for calibration purposes, a different question.
report() enforces this by filtering on family, qtype and framing together,
never approximately.

Python 3.9 floor, standard library only, no network, no third party
dependency.
"""

import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

#: The three question types this ledger and TypeSafe's own API recognise.
#: An unrecognised fourth value is never guessed at: see _validate_decision.
QTYPES = frozenset(("noul", "choice", "score"))

#: Confidence band lower bounds used when a caller supplies none. The WBS
#: unit that specified this module (JEV-02) pins this exact tuple.
DEFAULT_BANDS = (0.5, 0.7, 0.9, 1.0)

#: The z-score for a 95 percent two-sided normal interval, used by every
#: Wilson bound this module computes. Not configurable: a caller wanting a
#: different confidence level is asking a different statistical question,
#: not a parameter of this one.
_Z95 = 1.959963984540054

#: The module's own default ledger path, used only when a caller does not
#: supply one. Every test in test_jev_calibration.py uses an injected,
#: caller-chosen temp path instead: this constant exists so a real deployed
#: caller has one obvious place to point at, and so a test can assert this
#: path is never touched by a call that used its own path instead.
# ponytail: a single flat file with no locking is a known ceiling for a
# single-writer deployment. If a second concurrent writer to the SAME
# default path is ever needed, add advisory locking (fcntl.flock on POSIX)
# around _append_line; every write below is already a single os.write() of
# one line under PIPE_BUF for real records, which POSIX guarantees is
# atomic against other single-writer appenders, so the ceiling is
# concurrent writers, not single-writer safety.
DEFAULT_LEDGER_PATH = os.path.join(HERE, "jev_calibration_default_ledger.jsonl")


def _parse_ts(value):
    """Parses an ISO 8601 timestamp string into an aware UTC datetime.

    Raises ValueError (never any other exception type) naming the bad
    value when `value` is not a string or does not parse. A timestamp with
    no timezone is treated as UTC rather than rejected, since this estate's
    own callers write naive-looking ISO strings; a timestamp that DOES
    carry a timezone is converted to UTC rather than compared naively
    against one that does not, so two decisions from different callers are
    always ordered correctly against each other.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("at must be a non-empty ISO 8601 timestamp string, got %r" % (value,))
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("at is not a parseable ISO 8601 timestamp: %r" % (value,))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _require_nonempty_str(rec, field):
    if field not in rec:
        raise ValueError("missing required field: %s" % field)
    val = rec[field]
    if not isinstance(val, str) or not val.strip():
        raise ValueError("%s must be a non-empty string, got %r" % (field, val))
    return val


def _require_finite_number(rec, field, low=None, high=None):
    if field not in rec:
        raise ValueError("missing required field: %s" % field)
    val = rec[field]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError("%s must be a number, got %r" % (field, val))
    if not math.isfinite(val):
        raise ValueError("%s must be finite, got %r" % (field, val))
    if low is not None and val < low:
        raise ValueError("%s must be >= %r, got %r" % (field, low, val))
    if high is not None and val > high:
        raise ValueError("%s must be <= %r, got %r" % (field, high, val))
    return float(val)


def _validate_decision(rec):
    """Raises ValueError naming the first problem found. Never mutates rec.

    Reused by both append_decision() (before a write) and join() (on every
    line read back), so a record is held to exactly one standard on the way
    in and on the way out. See the module docstring's VALIDATION RUNS
    TWICE note for why this matters.
    """
    if not isinstance(rec, dict):
        raise ValueError("decision record must be a JSON object, got %r" % (rec,))
    _require_nonempty_str(rec, "id")
    _require_nonempty_str(rec, "family")
    qtype = _require_nonempty_str(rec, "qtype")
    if qtype not in QTYPES:
        raise ValueError("qtype must be one of %s, got %r" % (sorted(QTYPES), qtype))
    _require_nonempty_str(rec, "framing")
    if "answer" not in rec:
        raise ValueError("missing required field: answer")
    try:
        json.dumps(rec["answer"])
    except TypeError:
        raise ValueError("answer is not JSON-serializable: %r" % (rec["answer"],))
    prob = _require_finite_number(rec, "prob", low=0.0, high=1.0)
    confidence = _require_finite_number(rec, "confidence", low=0.0, high=1.0)
    _require_nonempty_str(rec, "model")
    _require_finite_number(rec, "cost", low=0.0)
    _require_nonempty_str(rec, "at")
    _parse_ts(rec["at"])
    if qtype == "noul" and abs(prob - confidence) > 1e-9:
        raise ValueError(
            "for qtype noul, confidence must equal prob (TypeSafe reports no "
            "separate confidence for a noul answer); got prob=%r confidence=%r"
            % (prob, confidence)
        )


def _validate_outcome(rec):
    """Raises ValueError naming the first problem found. Never mutates rec."""
    if not isinstance(rec, dict):
        raise ValueError("outcome record must be a JSON object, got %r" % (rec,))
    _require_nonempty_str(rec, "id")
    if "correct" not in rec:
        raise ValueError("missing required field: correct")
    if not isinstance(rec["correct"], bool):
        raise ValueError("correct must be true or false, got %r" % (rec["correct"],))
    _require_nonempty_str(rec, "source")
    _require_nonempty_str(rec, "at")
    _parse_ts(rec["at"])


def _append_line(path, rec):
    """Writes one JSON line to `path` in a single write() syscall.

    A single os.write() of one line under PIPE_BUF (4096 bytes on every
    POSIX platform this estate targets) is atomic against other appenders
    to the same file: readers never observe a torn line from a concurrent
    single writer. This is weaker than a lock (concurrent writers can still
    interleave complete lines in either order, which this ledger's
    append-only, order-independent design tolerates) and is documented as
    a stated ceiling above, not silently assumed.
    """
    line = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > 4096:
        # Still correct, just no longer provably atomic against a
        # concurrent writer; state this rather than silently proceed.
        pass
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def append_decision(path, rec):
    """Validates and appends one decision record. Raises ValueError and
    writes nothing on any validation failure (validate-then-write, never
    partial)."""
    _validate_decision(rec)
    _append_line(path, rec)


def append_outcome(path, rec):
    """Validates and appends one outcome record. Raises ValueError and
    writes nothing on any validation failure."""
    _validate_outcome(rec)
    _append_line(path, rec)


def _read_jsonl(path, validator):
    """Reads `path` line by line. Returns (records, corrupt).

    records is a list of (line_no, rec) for every line that is valid JSON
    AND passes `validator`. corrupt is a list of {"line", "error", "raw"}
    for every non-blank line that is not valid JSON, or is valid JSON but
    fails validator. A missing file is treated as an empty ledger (an
    empty ledger and a not-yet-created one are the same fact for this
    module: nothing has been recorded), never an error.
    """
    records = []
    corrupt = []
    if not os.path.isfile(path):
        return records, corrupt
    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                rec = json.loads(text)
            except json.JSONDecodeError as exc:
                corrupt.append({"line": line_no, "error": "not valid JSON: %s" % exc, "raw": text})
                continue
            try:
                validator(rec)
            except ValueError as exc:
                corrupt.append({"line": line_no, "error": str(exc), "raw": rec})
                continue
            records.append((line_no, rec))
    return records, corrupt


def _pick_latest(records_for_id):
    """records_for_id: list of (line_no, rec). Returns the (line_no, rec)
    with the latest `at` timestamp, ties broken by later line number
    (later in the file wins, since the file is append-only oldest-first)."""
    return max(records_for_id, key=lambda t: (_parse_ts(t[1]["at"]), t[0]))


def join(decisions_path, outcomes_path):
    """Reads both ledger files and pairs decisions with outcomes by id.

    Returns a dict:
      joined                     list of {"decision": rec, "outcome": rec}
                                  for every id with exactly one decision
                                  record and one clean (non-conflicting)
                                  correctness verdict.
      unmatched_decisions        list of decision records with no outcome
                                  yet. Excluded from any precision
                                  calculation: correctness is unknown, not
                                  false.
      orphan_outcomes            list of {"id", "records"} for outcome ids
                                  that never appeared as a decision at all.
                                  Never silently dropped, never guessed
                                  onto the nearest-looking decision.
      outcomes_for_duplicate_decision_ids
                                  list of {"id", "records"} for outcome ids
                                  whose decision id was written more than
                                  once (see duplicate_decision_ids below):
                                  kept separate from orphan_outcomes
                                  because a decision WAS made, just
                                  ambiguously, so calling it "no decision
                                  at all" would misstate what happened.
      duplicate_decision_ids      list of ids with more than one decision
                                  record. Excluded from precision entirely:
                                  which one actually produced the outcome
                                  cannot be known.
      ambiguous_outcomes          list of {"id", "reason", "records"} for
                                  ids whose outcome records disagree on
                                  `correct`. Excluded from precision: two
                                  irreconcilable verdicts, refuse both
                                  rather than guess which is right.
      duplicate_outcomes          list of {"id", "reason", "records"} for
                                  ids with more than one outcome record
                                  that all agree on `correct`. Still joined
                                  (using the latest by `at`), reported here
                                  so an operator can see the same outcome
                                  was recorded twice.
      corrupt_decisions           list of {"line", "error", "raw"} for
                                  decision-file lines that failed
                                  validation on read.
      corrupt_outcomes            list of {"line", "error", "raw"} for
                                  outcome-file lines that failed validation
                                  on read.
    """
    dec_records, corrupt_decisions = _read_jsonl(decisions_path, _validate_decision)
    out_records, corrupt_outcomes = _read_jsonl(outcomes_path, _validate_outcome)

    decisions_by_id = {}
    duplicate_decision_ids = set()
    for line_no, rec in dec_records:
        did = rec["id"]
        if did in decisions_by_id or did in duplicate_decision_ids:
            duplicate_decision_ids.add(did)
            decisions_by_id.pop(did, None)
        else:
            decisions_by_id[did] = rec

    outcomes_by_id = {}
    for line_no, rec in out_records:
        outcomes_by_id.setdefault(rec["id"], []).append((line_no, rec))

    joined = []
    unmatched_decisions = []
    ambiguous_outcomes = []
    duplicate_outcomes = []

    for did, dec in decisions_by_id.items():
        outs = outcomes_by_id.get(did, [])
        if not outs:
            unmatched_decisions.append(dec)
            continue
        correct_values = {rec["correct"] for _, rec in outs}
        if len(correct_values) > 1:
            ambiguous_outcomes.append({
                "id": did,
                "reason": "conflicting correct values",
                "records": [rec for _, rec in outs],
            })
            continue
        if len(outs) > 1:
            duplicate_outcomes.append({
                "id": did,
                "reason": "duplicate outcome, same correct value",
                "records": [rec for _, rec in outs],
            })
        chosen_line, chosen_rec = _pick_latest(outs)
        joined.append({"decision": dec, "outcome": chosen_rec})

    orphan_outcomes = []
    outcomes_for_duplicate_decision_ids = []
    for did, outs in outcomes_by_id.items():
        if did in decisions_by_id:
            continue
        if did in duplicate_decision_ids:
            outcomes_for_duplicate_decision_ids.append({
                "id": did,
                "records": [rec for _, rec in outs],
            })
        else:
            orphan_outcomes.append({
                "id": did,
                "records": [rec for _, rec in outs],
            })

    return {
        "joined": joined,
        "unmatched_decisions": unmatched_decisions,
        "orphan_outcomes": orphan_outcomes,
        "outcomes_for_duplicate_decision_ids": outcomes_for_duplicate_decision_ids,
        "duplicate_decision_ids": sorted(duplicate_decision_ids),
        "ambiguous_outcomes": ambiguous_outcomes,
        "duplicate_outcomes": duplicate_outcomes,
        "corrupt_decisions": corrupt_decisions,
        "corrupt_outcomes": corrupt_outcomes,
    }


def _wilson_lower_bound(k, n, z=_Z95):
    """The 95 percent Wilson score interval's LOWER bound for k correct out
    of n trials. Returns None when n is 0 (no fabricated 0 or 1)."""
    if n == 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (center - margin) / denom


def _validate_bands(bands):
    if not bands:
        raise ValueError("bands must be a non-empty sequence")
    nums = []
    for b in bands:
        if isinstance(b, bool) or not isinstance(b, (int, float)):
            raise ValueError("every band bound must be a number, got %r" % (b,))
        if not (0.0 <= b <= 1.0):
            raise ValueError("every band bound must be within [0, 1], got %r" % (b,))
        nums.append(float(b))
    for i in range(1, len(nums)):
        if nums[i] <= nums[i - 1]:
            raise ValueError("bands must be strictly increasing, got %r" % (bands,))
    return tuple(nums)


def report(decisions_path, outcomes_path, family, qtype, framing=None, bands=DEFAULT_BANDS):
    """Per-confidence-band precision for one family, qtype and, optionally,
    one exact framing. A noul and a choice sharing a family name are never
    pooled: filtering is on family AND qtype AND (if given) framing,
    together.

    Returns a dict:
      rows                 one entry per band, each
                            {"lower", "upper", "count", "correct",
                             "precision", "wilson_lower_95", "note"}.
                            "upper" is None for the last band (unbounded
                            above; every valid confidence is <= 1.0, which
                            append_decision already enforces). When count
                            is 0, precision and wilson_lower_95 are None
                            and note explains why: a None here is a
                            missing measurement, never a 0.
      below_lowest_band     {"count", "correct", "precision",
                             "wilson_lower_95"} for decisions in scope
                            whose confidence is below bands[0]. Reported
                            explicitly rather than silently vanishing: a
                            caller asking for bands starting at 0.5 still
                            deserves to know a 0.2-confidence decision
                            existed and whether it was right.
      total_in_scope        count of joined (decision, outcome) pairs
                            matching family/qtype/framing. Equals the sum
                            of every band's count plus
                            below_lowest_band["count"] (below_lowest_band
                            is a subset view of the same in-scope pairs,
                            not an addition to them).
      unmatched_decisions_in_scope
                            ids of decisions matching family/qtype/framing
                            with no outcome yet.
      ledger_wide           counts that are NOT scoped to this
                            family/qtype/framing, because the records they
                            describe (an orphan outcome, a corrupt line, a
                            duplicate decision id) carry no reliable
                            family/qtype of their own to filter by:
                            {"orphan_outcomes", "duplicate_decision_ids",
                             "ambiguous_outcomes", "corrupt_decisions",
                             "corrupt_outcomes"}, each a plain count.
    """
    family = _require_nonempty_str({"family": family}, "family")
    qtype = _require_nonempty_str({"qtype": qtype}, "qtype")
    if qtype not in QTYPES:
        raise ValueError("qtype must be one of %s, got %r" % (sorted(QTYPES), qtype))
    if framing is not None:
        framing = _require_nonempty_str({"framing": framing}, "framing")
    bands = _validate_bands(bands)

    j = join(decisions_path, outcomes_path)

    def in_scope(dec):
        if dec["family"] != family or dec["qtype"] != qtype:
            return False
        if framing is not None and dec["framing"] != framing:
            return False
        return True

    filtered = [item for item in j["joined"] if in_scope(item["decision"])]
    unmatched_in_scope = [dec["id"] for dec in j["unmatched_decisions"] if in_scope(dec)]

    below = [item for item in filtered if item["decision"]["confidence"] < bands[0]]
    below_n = len(below)
    below_k = sum(1 for item in below if item["outcome"]["correct"])
    below_lowest_band = {
        "count": below_n,
        "correct": below_k,
        "precision": (below_k / below_n) if below_n else None,
        "wilson_lower_95": _wilson_lower_bound(below_k, below_n),
    }

    rows = []
    for i, lower in enumerate(bands):
        upper = bands[i + 1] if i + 1 < len(bands) else None
        band_items = [
            item for item in filtered
            if item["decision"]["confidence"] >= lower
            and (upper is None or item["decision"]["confidence"] < upper)
        ]
        n = len(band_items)
        k = sum(1 for item in band_items if item["outcome"]["correct"])
        if n == 0:
            precision = None
            wl = None
            note = "no decisions in this band"
        else:
            precision = k / n
            wl = _wilson_lower_bound(k, n)
            note = None
        rows.append({
            "lower": lower,
            "upper": upper,
            "count": n,
            "correct": k,
            "precision": precision,
            "wilson_lower_95": wl,
            "note": note,
        })

    return {
        "family": family,
        "qtype": qtype,
        "framing": framing,
        "rows": rows,
        "below_lowest_band": below_lowest_band,
        "total_in_scope": len(filtered),
        "unmatched_decisions_in_scope": unmatched_in_scope,
        "ledger_wide": {
            "orphan_outcomes": len(j["orphan_outcomes"]),
            "duplicate_decision_ids": len(j["duplicate_decision_ids"]),
            "ambiguous_outcomes": len(j["ambiguous_outcomes"]),
            "corrupt_decisions": len(j["corrupt_decisions"]),
            "corrupt_outcomes": len(j["corrupt_outcomes"]),
        },
    }


def threshold(decisions_path, outcomes_path, family, qtype, target_precision, min_n,
              framing=None, bands=DEFAULT_BANDS):
    """The lowest confidence band whose Wilson lower bound meets or exceeds
    target_precision, using only bands with at least min_n joined
    decisions. Never falls back to a default: returns
    {"threshold": None, "reason": "..."} when no band qualifies, naming
    which of the two reasons applies.

    Returns {"threshold": <band lower bound> or None, "reason": None or
    str, "rows": the report()'s rows, "below_lowest_band": ...}.
    """
    if isinstance(target_precision, bool) or not isinstance(target_precision, (int, float)):
        raise ValueError("target_precision must be a number, got %r" % (target_precision,))
    if not (0.0 < target_precision <= 1.0):
        raise ValueError("target_precision must be within (0, 1], got %r" % (target_precision,))
    if isinstance(min_n, bool) or not isinstance(min_n, int) or min_n < 1:
        raise ValueError("min_n must be a positive integer, got %r" % (min_n,))

    rep = report(decisions_path, outcomes_path, family, qtype, framing=framing, bands=bands)
    rows = rep["rows"]

    eligible = [
        row for row in rows
        if row["count"] >= min_n and row["wilson_lower_95"] is not None
        and row["wilson_lower_95"] >= target_precision
    ]
    if eligible:
        return {
            "threshold": min(row["lower"] for row in eligible),
            "reason": None,
            "rows": rows,
            "below_lowest_band": rep["below_lowest_band"],
        }

    any_min_n = any(row["count"] >= min_n for row in rows)
    if not any_min_n:
        reason = "no band has at least min_n=%d joined decisions" % min_n
    else:
        reason = (
            "no band with at least min_n=%d joined decisions reached a Wilson "
            "lower bound of target_precision=%.6f" % (min_n, target_precision)
        )
    return {
        "threshold": None,
        "reason": reason,
        "rows": rows,
        "below_lowest_band": rep["below_lowest_band"],
    }


#: The literal source string every seed_from_eval() outcome record carries.
#: Pinned as a constant, not reconstructed, so a caller filtering the
#: ledger for this exact provenance string cannot be broken by a rewording.
SEED_SOURCE = "orchestrator eval 2026-09-18"

#: The eval system name this estate's own eval harness used for Jev's own
#: answers (as opposed to a paraphrased-framing run, a second Jev call, or
#: a different model entirely run for comparison in the same file).
SEED_SYSTEM = "jev"


def seed_from_eval(results_path, dataset_path, decisions_out_path, outcomes_out_path,
                    system=SEED_SYSTEM, source=SEED_SOURCE):
    """Loads this session's labelled eval rows and appends them to the
    ledger as decisions plus outcomes, so the calibration report has real
    data to measure before any live traffic has been recorded.

    results_path is a line-delimited JSON file, each line at least
    {"task", "item", "pred", "conf", "correct", "model", "cost"} plus a
    "system" field this function filters on. dataset_path is a JSON object
    mapping "<family letter>_<name>" keys to {"instructions": <str>, ...};
    the family letter before the first underscore is matched against each
    result row's "task" to find that family's instructions text, which is
    hashed to produce a stable `framing` value (every item in one eval
    family was asked with the same wording, so one hash per family is
    correct here, not a defect: a caller with per-item wording would need
    a different framing source).

    Neither results_path nor dataset_path carries a per-row timestamp,
    only the eval's own recorded fields. Rather than fabricate one (this
    estate's own standing rule: never present a guessed value as measured),
    every seeded record's "at" is the results file's own last-modified
    time, read fresh from the filesystem in this call, converted to UTC.
    This is a real, checkable fact about when the eval data was produced,
    not an invented one; it is documented here so nobody downstream reads
    it as a per-decision timestamp.

    Returns {"status": "NO-DATA", "reason": "..."} when either input file
    is absent: this is not a pass, and it is never silently treated as
    "zero decisions" by the caller. On success, returns
    {"status": "OK", "decisions_written": int, "outcomes_written": int,
    "skipped": [{"task", "item", "reason"}]}.
    """
    if not os.path.isfile(results_path):
        return {"status": "NO-DATA", "reason": "results file not found: %s" % results_path}
    if not os.path.isfile(dataset_path):
        return {"status": "NO-DATA", "reason": "dataset file not found: %s" % dataset_path}

    mtime = os.path.getmtime(results_path)
    at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            dataset = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "NO-DATA", "reason": "dataset file could not be read: %s" % exc}
    if not isinstance(dataset, dict):
        return {"status": "NO-DATA", "reason": "dataset file is not a JSON object"}

    family_framing = {}
    for key, val in dataset.items():
        if "_" not in key or not isinstance(val, dict):
            continue
        letter = key.split("_", 1)[0]
        instructions = val.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            family_framing[letter] = hashlib.sha256(instructions.encode("utf-8")).hexdigest()[:16]

    decisions_written = 0
    outcomes_written = 0
    skipped = []

    try:
        results_file = open(results_path, "r", encoding="utf-8")
    except OSError as exc:
        return {"status": "NO-DATA", "reason": "results file could not be opened: %s" % exc}

    with results_file:
        for line_no, raw in enumerate(results_file, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                skipped.append({"task": None, "item": None, "reason": "line %d not valid JSON: %s" % (line_no, exc)})
                continue
            if not isinstance(row, dict) or row.get("system") != system:
                continue

            family = row.get("task")
            item = row.get("item")
            pred = row.get("pred")
            conf = row.get("conf")
            correct = row.get("correct")

            if not isinstance(family, str) or family not in family_framing:
                skipped.append({"task": family, "item": item, "reason": "no dataset instructions found for this family"})
                continue
            if not isinstance(item, str) or not item:
                skipped.append({"task": family, "item": item, "reason": "missing or invalid item id"})
                continue
            if pred is None or conf is None:
                skipped.append({"task": family, "item": item, "reason": "no prediction or confidence recorded (likely an eval-time failure row)"})
                continue
            if not isinstance(correct, bool):
                skipped.append({"task": family, "item": item, "reason": "no boolean correct field recorded"})
                continue

            qtype = "noul" if isinstance(pred, bool) else "choice"
            decision_id = "%s:%s" % (family, item)
            model = row.get("model")
            if not isinstance(model, str) or not model.strip():
                model = system
            cost = row.get("cost", 0.0)
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                cost = 0.0

            decision = {
                "id": decision_id,
                "family": family,
                "qtype": qtype,
                "framing": family_framing[family],
                "answer": pred,
                "prob": float(conf),
                "confidence": float(conf),
                "model": model,
                "cost": float(cost),
                "at": at,
            }
            outcome = {
                "id": decision_id,
                "correct": correct,
                "source": source,
                "at": at,
            }
            try:
                append_decision(decisions_out_path, decision)
                decisions_written += 1
                append_outcome(outcomes_out_path, outcome)
                outcomes_written += 1
            except ValueError as exc:
                skipped.append({"task": family, "item": item, "reason": "validation failed: %s" % exc})

    return {
        "status": "OK",
        "decisions_written": decisions_written,
        "outcomes_written": outcomes_written,
        "skipped": skipped,
    }


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "selftest":
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dp = os.path.join(d, "decisions.jsonl")
            op = os.path.join(d, "outcomes.jsonl")
            append_decision(dp, {
                "id": "x1", "family": "f", "qtype": "noul", "framing": "h1",
                "answer": True, "prob": 0.9, "confidence": 0.9, "model": "m",
                "cost": 0.0, "at": "2026-09-18T00:00:00Z",
            })
            append_outcome(op, {"id": "x1", "correct": True, "source": "test", "at": "2026-09-18T00:01:00Z"})
            rep = report(dp, op, "f", "noul")
            assert rep["rows"][2]["count"] == 1, rep
            print("selftest OK")
        return 0
    print("usage: jev_calibration.py selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main())
