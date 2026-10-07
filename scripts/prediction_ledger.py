#!/usr/bin/env python3
"""Score every predictor on this estate, by harvesting outcomes that already exist rather than asking anyone.

usage (repo root):
  python3 scripts/prediction_ledger.py predict <predictor> <subject> <answer> [--confidence C] [--question Q]
  python3 scripts/prediction_ledger.py resolve <id> <actual> --source "<what established it>"
  python3 scripts/prediction_ledger.py harvest            close every prediction whose truth is already on disk
  python3 scripts/prediction_ledger.py score              per predictor: n, resolved, accuracy, Brier, calibration
  python3 scripts/prediction_ledger.py --selftest

WHY, measured 2026-09-21. The typed decision model holds 3,765 predictions against 12 recorded outcomes, a
ratio of 314 to 1, and the gap DOUBLED in one day. It cannot graduate out of shadow mode because nothing knows
whether it has ever been right. The cause is not laziness: a prediction is automatic and an outcome is manual,
so predictions accumulate and outcomes do not.

AND IT IS NOT ONLY THAT MODEL. Six predictors run here and not one is scored:
  jev          a typed decision            -> was the answer right
  spec_score   this spec is buildable      -> did the lane land, or exhaust
  grade        this build is good          -> did the probes find defects anyway
  probe        this build is clean         -> did the landing gate accept it
  council      do not build this           -> did it actually fail when built
  diagnostic   this fact will unblock it   -> did the restart produce a build
  done_check   this unit is finished       -> does its check still pass later
The last one is not hypothetical. Three of sixteen CLOSED units fail their own done check today, and two name
test modules with ZERO commits in the entire repository history, so their completion was never verifiable by
anyone. A scored done_check predictor catches that at closure instead of never.

THE DESIGN RULE THAT MAKES IT WORK: outcomes are HARVESTED, never typed. The truth for almost every prediction
here already sits on disk minutes later, in a grader verdict, a probe verdict, a run STATUS or the plan itself.
`harvest` walks those sources and closes the loop. A ledger that needs a human to come back is the ledger we
already have, and it has twelve rows.

UNRESOLVABLE IS ITS OWN STATE. A prediction whose subject was abandoned is never correct and never wrong, and
it must not sit in a denominator pretending to be pending forever. It resolves to `unresolvable` with a reason
and is excluded from accuracy while still being counted and reported, because a predictor whose questions
mostly evaporate is telling you something too.

PROSPECTIVE ERROR, AND THE HALF OF IT A MACHINE CANNOT DECIDE. Every figure this module reports is
computed from resolutions the ledger itself recorded, over the rows pair() showed were registered
BEFORE their answer, and resolved, unresolved and pending print as three separate counters so no
part of the population can hide inside one denominator. Coverage is resolved over the KNOWABLE rows
and reads NO-DATA, never a zero percent, when nothing is knowable yet: an empty knowable set is not
a measured failure.

THE CONTAMINATION RULE. A figure used to SCORE a forecast must not be drawn from any row written
before that forecast was registered. The machine checkable half of that is enforced BY CONSTRUCTION
rather than audited afterwards: the scored population is exactly the set of resolutions whose paired
prediction precedes them, which is the ordering verdict pair() computes and score() reads instead of
re implementing. What no control here can decide is whether the CONTENT of an old constant leaked
into a forecaster's reasoning while its timestamp stayed clean. That is a human judgement and stays
a discipline, never a control, and this docstring says so rather than pretending a timestamp
comparison settles it.

A RESOLUTION NOBODY CAN READ IS NOT A RESOLUTION. A resolve row whose `actual` is missing or None
counts under unresolved, beside the recorded non answers already in UNKNOWN_ACTUALS, because a row
whose outcome cannot be read is exactly the unscoreable claim that bucket exists for. Before this
sub unit such a row fell through to resolved and, when the prediction's own answer was also missing
or None, registered as a HIT: a bad outcome failing in the direction that flatters the predictor."""
import argparse
from collections.abc import Mapping
import datetime
import json
import math
import os
import re
import sys
import time

LEDGER = os.path.expanduser("~/.claude/evidence/brother-predictions.jsonl")
PREDICTORS = ("jev", "spec_score", "grade", "probe", "council", "diagnostic", "done_check")

# R1 (P1.a), ONE SOURCE OF TRUTH PER PREDICTOR. These are the six predictors whose rows this
# file stores. jev is deliberately absent: its rows live in the calibration ledger
# (scripts/jev_calibration.py) and are READ by jev_rows(), never copied in here. The retired
# import wrote a second copy of them into the ledger file; all_pairs() drops that copy from
# what it returns and counts it in its own `skipped_jev_rows` field. load() itself is
# unchanged, because this module's own selftest and the suites beside it predict into scratch
# files and must read back exactly what they wrote (rule 7: add beside the old, do not change
# it).
LOCAL_PREDICTORS = tuple(p for p in PREDICTORS if p != "jev")


class LedgerError(ValueError):
    """Raised with a code naming the first problem found. Never silently dropped.

    A ValueError subclass, so a caller already catching ValueError still catches this. `code`
    is the machine readable half, and the message carries it too, so nothing is lost when only
    one half of the pair is read.
    """

    def __init__(self, code, message=None):
        self.code = code
        super().__init__(code if message is None else "%s: %s" % (code, message))

# THE THREE OUTCOMES. A prediction is right, wrong, or NOT YET KNOWN, and the third one is why this
# block exists. Until 2026-09-21 harvest() ended in `"pass" if head == "READY" else "fail"`: a
# catch-all `else` that turned every other status word into a recorded FAILURE. Measured that day
# against the file as it stood, with one run folder per word: RUNNING, WITHHELD, BLOCKED and a
# deliberate typo all recorded `fail`, so a build still in flight, a build stopped waiting on a
# human fact, a build that was never made at all, and a misspelling were each written into the
# calibration data as the predictor having been wrong.
#
# Two symptoms were reported and they point OPPOSITE ways: one unknown scored as a fail, one
# (READY-UNPROBED) scored as a pass. Opposite directions from one function is the signature of a
# MISSING STATE, not of two bugs. Each site was picking a direction by accident because the type it
# had to produce was a binary.
#
# UNRESOLVED, not "unknown": the word says what the ledger did, which is decline to resolve the row.
# It is distinct from the stored actual `unresolvable`, which means nobody will EVER know because
# the subject was abandoned. UNRESOLVED is temporary and re-harvestable; `unresolvable` is terminal.
PASS, FAIL, UNRESOLVED = "pass", "fail", "unresolved"

# Actuals that are a recorded non-answer. Counted, reported, and in NO accuracy denominator. Both
# spellings are accepted because rows already on disk carry `unresolvable`.
UNKNOWN_ACTUALS = ("unresolved", "unresolvable")

# THE STATE TABLE. Every word a run STATUS can open with, taken from the writers: unit_runner.py
# (READY, READY-UNPROBED, EXHAUSTED, WITHHELD, BLOCKED), probe_round.py (DIRTY), land_batch.py
# (QUARANTINE), and RUNNING, which every reader on this estate derives from a run folder whose
# runner has not written a STATUS yet.
#
# FAIL DIRECTION, stated once for the whole table: a word that is not a key here is UNRESOLVED.
# There is no `else` in this module that produces a verdict.
STATUS_OUTCOME = {
    "READY":          PASS,        # graded, probed clean, waiting to land: the build was good
    "DIRTY":          FAIL,        # executed probes found real defects in it
    "QUARANTINE":     FAIL,        # the landing gates refused it on its own merits
    "EXHAUSTED":      FAIL,        # every round spent and no build passed the grader
    "RUNNING":        UNRESOLVED,  # still in flight. Not yet known, and never a failure
    "READY-UNPROBED": UNRESOLVED,  # no adversary produced a runnable probe, so nothing was proven
                                   # either way; probe_round re-probes it and a later harvest reads
                                   # a real verdict. Scored as a PASS until 2026-09-21, which wrote
                                   # an untrustworthy build into the calibration data as a success
    "WITHHELD":       UNRESOLVED,  # stopped waiting on a human fact: the predictor was never tested
    "BLOCKED":        UNRESOLVED,  # no eligible model, so no build exists to judge
}


def outcome_for_status(head):
    """The ONE place a status word becomes an outcome. Returns PASS, FAIL or UNRESOLVED.

    UNRESOLVED IS NEVER WRITTEN TO THE LEDGER. pair() lets the FIRST resolution win and a resolution
    cannot be reopened, so recording "not yet known" would permanently close a row against the real
    verdict a later harvest will read. UNRESOLVED means DECLINE, leave it pending, come back.

    FAIL DIRECTION: missing, empty, whitespace, a wrong type, a truncated word, an unrecognised word
    and a word added by a future writer that nobody taught this table all return UNRESOLVED. An
    unknown becomes a verdict in neither direction."""
    if not isinstance(head, str):
        return UNRESOLVED
    return STATUS_OUTCOME.get(head.strip(), UNRESOLVED)


# THE LIFECYCLE (P1.b, R2 and R3). A prediction is open, settled, or one of four terminal
# states that are NOT a verdict about the predictor. Until this sub unit a subject that
# evaporated was closed with the magic string `unresolvable` in the `actual` field: a state
# hidden inside a value, which is why the ledger could not say how many of its open rows were
# waiting and how many were dead. Each id now gets exactly one state, drawn from STATES.
STATES = ("OPEN", "RESOLVED", "EXPIRED", "CANCELLED", "SUPERSEDED", "INVALID")

#: Seven days. The default lifetime of a claim, used by lifecycle(), counts() and score() when
#: the caller does not name one. It is a parameter, never a clock reading inside those functions.
TTL_SECONDS = 604800.0


def _number(value, name):
    """A real, orderable number for a parameter, or this module's ValueError.

    FAIL DIRECTION: None, a str, bytes, a bool (True is an int in Python and would quietly take
    the slot of a real second 1) and NaN (which compares False against every value, so nothing
    can be ordered against it) are all refused, never defaulted to 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a number, got %s" % (name, type(value).__name__))
    if isinstance(value, float) and math.isnan(value):
        raise ValueError("%s must be a number, got nan" % name)
    return float(value)


def _state_entry(pid, entry):
    """(prediction, resolution, ordered) from one lifecycle entry.

    pair() hands out 3-tuples. A 2-tuple (prediction, resolution) is accepted too, with the claim
    assumed to be in order, so a caller that holds only those two can still ask for a state.
    Anything else is a row this function cannot place: LedgerError, never a silent default."""
    if isinstance(entry, (tuple, list)):
        if len(entry) == 2:
            return entry[0], entry[1], True
        if len(entry) == 3:
            return entry[0], entry[1], entry[2]
    raise LedgerError("bad_pair", "the lifecycle entry for id %r is not a "
                      "(prediction, resolution[, ordered]) pair" % (pid,))


def _is_cancel_row(row):
    """True for the row cancel() appends. A cancel row is not a resolution: it says the subject
    was abandoned on purpose, so it is a state of its own and never a verdict."""
    return isinstance(row, dict) and row.get("kind") == "cancel"


def _state_for(prediction, resolution, ordered, now, ttl, restated):
    """The ONE place an id becomes a state. Nothing else decides a state."""
    if not isinstance(prediction, dict):
        return "INVALID"
    subject = prediction.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        return "INVALID"
    at = _usable_at(prediction)
    if at is None:
        return "INVALID"
    if _is_cancel_row(resolution):
        return "CANCELLED"
    if isinstance(resolution, dict):
        actual = resolution.get("actual")
        if ordered and actual is not None and actual not in UNKNOWN_ACTUALS:
            return "RESOLVED"
    if restated:
        return "SUPERSEDED"
    if isinstance(resolution, dict):
        if resolution.get("actual") in UNKNOWN_ACTUALS:
            return "EXPIRED"
        return "INVALID"
    return "OPEN" if (now - at) < ttl else "EXPIRED"


def lifecycle(pairs, now, ttl_seconds=TTL_SECONDS, superseded=None):
    """{id: state}. Exactly one state per id, drawn from STATES. Raises LedgerError on an entry it
    cannot place at all.

    OPEN        no resolution and younger than ttl_seconds
    RESOLVED    a resolution, strictly later than the claim, with an actual other than the
                retired unresolvable marker
    EXPIRED     no resolution and older than ttl_seconds, so the subject is gone and the claim
                never settles. The retired `unresolvable` marker reads here too, because rows
                already carry it and history is not rewritten.
    CANCELLED   an explicit cancel row, written when the subject was abandoned on purpose
    SUPERSEDED  a later prediction exists for the same id, carried in by the optional
                `superseded` keyword. pair() keeps only the LAST prediction for an id, so the
                earlier claim survives nowhere in its output; superseded_ids() reads it from
                the raw rows.
    INVALID     the prediction names no subject or carries no time this module can read, or holds
                a resolution nobody can read, or one written before the claim it settles

    TIME IS A PARAMETER. Neither this function nor counts() reads a clock, so every state is
    reproducible from the file plus one number. `now` and `ttl_seconds` must be real numbers and
    the ttl must be positive: a bool, NaN, a string, None and a non-positive ttl are all
    refused."""
    if not isinstance(pairs, Mapping):
        raise ValueError("lifecycle() wants a mapping of id to a pair, got %s"
                         % type(pairs).__name__)
    now = _number(now, "now")
    ttl = _number(ttl_seconds, "ttl_seconds")
    if not (ttl > 0.0):
        raise ValueError("ttl_seconds must be positive, got %r" % (ttl_seconds,))
    if superseded is None:
        superseded = ()
    if isinstance(superseded, (str, bytes)):
        raise ValueError("superseded must be a collection of ids, not %s"
                         % type(superseded).__name__)
    try:
        superseded = set(superseded)
    except TypeError:
        raise ValueError("superseded must be a collection of hashable ids, got %s"
                         % type(superseded).__name__)
    out = {}
    for pid, entry in pairs.items():
        prediction, resolution, ordered = _state_entry(pid, entry)
        out[pid] = _state_for(prediction, resolution, ordered, now, ttl, pid in superseded)
    return out


def counts(pairs, now, ttl_seconds=TTL_SECONDS, superseded=None):
    """{state: n} over every id in `pairs`, so a report can show what is waiting, what died and
    what settled. All six STATES are present even at zero, because a missing key and a zero are
    different sentences and only one of them is true here."""
    states = lifecycle(pairs, now, ttl_seconds, superseded=superseded)
    out = dict((state, 0) for state in STATES)
    for state in states.values():
        out[state] = out.get(state, 0) + 1
    return out


def superseded_ids(rows):
    """The ids whose claim was restated: more than one dated predict row carrying the same id.

    pair() keeps only the LAST prediction for an id, so the earlier claim survives nowhere in its
    output. This reads the raw rows, which is the only place it still exists."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("superseded_ids() wants a list of rows, got %s" % type(rows).__name__)
    seen = set()
    out = set()
    for row in rows:
        if not (isinstance(row, dict) and row.get("kind") == "predict"):
            continue
        if _usable_at(row) is None:
            continue
        rid = row.get("id")
        try:
            hash(rid)
        except TypeError:
            continue
        if rid in seen:
            out.add(rid)
            continue
        seen.add(rid)
    return out


def _lifecycle_input(rows, paired):
    """pair()'s mapping with a cancel row put into the empty resolution slot of its own id.

    pair() ignores a cancel row, because it is not a resolution. The state still has to see it, so
    it is attached here, in ONE place, rather than teaching pair() a second row kind."""
    cancels = {}
    if isinstance(rows, (list, tuple)):
        for row in rows:
            if not _is_cancel_row(row):
                continue
            rid = row.get("id")
            try:
                if rid in paired and rid not in cancels:
                    cancels[rid] = row
            except TypeError:
                continue
    out = {}
    for pid, entry in paired.items():
        prediction, resolution, ordered = _state_entry(pid, entry)
        if resolution is None and pid in cancels:
            resolution = cancels[pid]
        out[pid] = (prediction, resolution, ordered)
    return out


def cancel(pid, reason, path, now=None):
    """Append a cancel row for a prediction abandoned on purpose, and return it.

    Refuses an id that is already RESOLVED: the truth is not overwritten by a cancel. It also
    refuses an id that is not on the ledger at all, and one that is already CANCELLED, because
    unknown input BLOCKS and a control that prevents beats a check that reports. `now` is an
    additive keyword so a fixture can be stamped; nothing here reads a clock unless the caller
    gives no stamp at all."""
    if not isinstance(pid, str) or not pid.strip():
        raise ValueError("cancel() wants a non-empty prediction id, got %r" % (pid,))
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("cancel() wants a non-empty reason, got %r" % (reason,))
    rows = load(path)
    paired = pair(rows)
    if pid not in paired:
        raise LedgerError("unknown_id", "no prediction with id %r is on this ledger, so there "
                          "is nothing to cancel" % (pid,))
    stamp = _number(time.time() if now is None else now, "now")
    state = lifecycle(_lifecycle_input(rows, paired), stamp).get(pid)
    if state == "RESOLVED":
        raise LedgerError("already_resolved",
                          "prediction %r is RESOLVED; the truth is not overwritten by a cancel"
                          % (pid,))
    if state == "CANCELLED":
        raise LedgerError("already_cancelled", "prediction %r is already CANCELLED" % (pid,))
    return _append({"at": stamp, "kind": "cancel", "id": pid,
                    "reason": str(reason)[:300]}, path)


def _append(row, path=None):
    """Raises LedgerError('write_failed') when the row could not be written: a lost prediction must never
    read as recorded (2026-09-30). LedgerError is a ValueError, so grade_build.record_claim already turns it
    into False without stopping the work it measures."""
    try:
        os.makedirs(os.path.dirname(path or LEDGER), exist_ok=True)
        with open(path or LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        raise LedgerError("write_failed", "the %s row for %r was not written: %s: %s"
                          % (row.get("kind"), row.get("id"), type(exc).__name__, exc))
    return row


def pid_for(predictor, subject, question, attempt=None):
    """A prediction's id is derived from what it is ABOUT, so the same claim made twice is the same row and a
    resolver can find it without the predictor having remembered an id.

    ATTEMPT IS PART OF THE IDENTITY. Without it a retry's verdict resolves a prediction made about the
    PREVIOUS attempt, which is the single most likely way an automatic resolver records a confident lie. An
    adversarial review of this module named it as finding one, against this file's first draft, where the key
    was subject alone. A predictor that cannot name its attempt gets `-`, and the harvester refuses to resolve
    those from attempt-scoped evidence."""
    return "%s:%s:%s:%s" % (predictor, subject, question or "-", attempt or "-")


def predict(predictor, subject, answer, confidence=None, question=None, path=None, now=None, attempt=None):
    """Record a claim before its truth is known. Confidence is optional: a predictor that does not give one is
    scored on accuracy alone, never on a confidence it never expressed.

    R4 (P1.a): a prediction is MINTED BEFORE its outcome exists. When the outcome artifact for
    this subject is already on disk, recording a claim now would be hindsight written as
    foresight, and the ledger refuses it with LedgerError code "hindsight" rather than at report
    time. The unknown predictor test stays first, so an unknown predictor is refused identically
    on every machine.
    """
    if predictor not in PREDICTORS:
        raise ValueError("unknown predictor %r; add it to PREDICTORS deliberately" % predictor)
    if outcome_exists(subject):
        raise LedgerError("hindsight", "an outcome artifact for subject %r is already on disk; a "
                          "prediction must be minted before its outcome exists" % (subject,))
    if confidence is not None:
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1, got %r" % confidence)
    return _append({"at": now if now is not None else time.time(), "kind": "predict",
                    "id": pid_for(predictor, subject, question, attempt), "predictor": predictor,
                    "subject": subject, "question": question, "answer": answer,
                    "attempt": attempt, "confidence": confidence}, path)


def resolve(pid, actual, source, path=None, now=None):
    """Record what actually happened, and WHO established it. A resolution with no source is refused: an
    outcome nobody can trace back to a command is the same unverifiable claim this module exists to catch."""
    if not source or not str(source).strip():
        raise ValueError("a resolution must name its source")
    return _append({"at": now if now is not None else time.time(), "kind": "resolve",
                    "id": pid, "actual": actual, "source": str(source)[:300]}, path)


def load(path=None):
    out = []
    target = LEDGER if path is None else path
    if not isinstance(target, (str, bytes, os.PathLike)):
        raise ValueError("load() wants a path, got %s" % type(target).__name__)
    try:
        with open(target, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not (isinstance(r, dict) and r.get("kind") in ("predict", "resolve", "cancel")
                        and r.get("id")):
                    continue
                # A PREDICT ROW WITHOUT A PREDICTOR IS DROPPED HERE, at the one gate every reader
                # passes through, rather than raising KeyError inside score(). Fail direction: a
                # malformed row is discarded, never counted as anything. Dropping it cannot flatter
                # a predictor, because the row carries no predictor to flatter.
                if r["kind"] == "predict" and not isinstance(r.get("predictor"), str):
                    continue
                out.append(r)
    except (OSError, UnicodeDecodeError):
        return []
    return out


def _usable_at(row):
    """The row's own `at` when it is a real, orderable timestamp, else None.

    FAIL DIRECTION: a missing `at`, a non numeric `at`, a bool (True is an int in Python and would
    take the slot of a real second 1) and NaN (which compares False against every value, so nothing
    can be ordered against it) all read as NO TIMESTAMP. None of them is ever read as 0, because 0
    is a real epoch time, so defaulting a bad row to it hands that bad row the earliest slot in the
    whole ledger."""
    if not isinstance(row, dict):
        return None
    at = row.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    if isinstance(at, float) and math.isnan(at):
        return None
    return at


class _DidNotStand(object):
    """A resolution value no prediction answer can equal.

    score() decides a hit by comparing the prediction's answer with the resolution's actual, and
    a calibration ledger outcome records only a boolean. Writing a literal here (say "fail", or
    the "correct" the retired import used) would score an answer of "fail" as a HIT on a claim
    the calibration ledger recorded as WRONG, which is the direction that flatters the
    predictor. A fresh instance compares equal to nothing but itself.
    """

    __slots__ = ()

    def __repr__(self):
        return "<answer did not stand>"


#: The value a jev resolution carries when the calibration ledger's own outcome says the answer
#: did NOT stand. One instance, rebuilt nowhere, so identity is the whole of the contract.
_DID_NOT_STAND = _DidNotStand()

#: Timestamp shapes a calibration ledger `at` may carry, tried after fromisoformat(). The compact
#: form is the estate's own "YYYYMMDDTHHMMSSffffffZ" stamp, the same shape its segment filenames
#: use; the space separated form is what datetime's own str() writes.
_AT_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y%m%dT%H%M%S%f",
               "%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S")

#: The calibration ledger module, filled in on first use by _calibration_ledger().
_JEV_CALIBRATION = None


def _calibration_ledger():
    """The calibration ledger module, imported on FIRST USE (R1, P1.a).

    Imported here rather than at this module's import time, so that loading this file from any
    sys.path (a harness that loads it by absolute path, a caller that has not put this folder on
    sys.path) still gets this module and its own selftest. A MISSING calibration ledger is a
    REFUSAL with this module's own error type: never a stand-in that approves, and never an
    empty score dressed up as a clean one.
    """
    global _JEV_CALIBRATION
    if _JEV_CALIBRATION is None:
        try:
            import jev_calibration as module
        except ImportError as exc:
            raise LedgerError("jev_ledger_missing", "the calibration ledger "
                              "(scripts/jev_calibration.py) could not be imported, so jev's "
                              "rows are NO-DATA: %s" % (exc,))
        _JEV_CALIBRATION = module
    return _JEV_CALIBRATION


def _calibration_epoch(at):
    """A float epoch for a calibration ledger `at`, or None when it is not readable as a time.

    None is deliberate: pair() leaves a row with no usable `at` out of BOTH slots rather than
    sorting it as if it were the epoch of 1970, and jev_rows() refuses to hand an unreadable
    time on at all, so a time nobody can read is a BLOCK rather than a silent drop.
    """
    if isinstance(at, bool) or not isinstance(at, (int, float, str)):
        return None
    if isinstance(at, (int, float)):
        return float(at)
    text = at.strip()
    if not text:
        return None
    bodies = [text]
    if text.endswith("Z"):
        bodies.append(text[:-1] + "+00:00")
        bodies.append(text[:-1])
    stamp = None
    for body in bodies:
        try:
            stamp = datetime.datetime.fromisoformat(body)
            break
        except ValueError:
            continue
    if stamp is None:
        for body in bodies:
            for fmt in _AT_FORMATS:
                try:
                    stamp = datetime.datetime.strptime(body, fmt)
                except ValueError:
                    continue
                break
            if stamp is not None:
                break
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    try:
        return stamp.timestamp()
    except (OSError, OverflowError, ValueError):
        return None


def _jev_pair(decision, outcome):
    """The ONE mapping from a calibration decision and outcome onto this ledger's pair shape.

    Written once, in one function, because the defect this replaces was this mapping written
    twice: one copy read answer="pass" against actual="correct", so the typed decision model
    read as never right. `correct` True means the answer STOOD, so the resolution carries the
    decision's own answer and score() reads it as a hit; `correct` False carries a value no
    answer can equal, so it is a miss whatever the answer was.
    """
    prediction = {
        "kind": "predict",
        "predictor": "jev",
        "id": decision["id"],
        "subject": decision["id"],
        "question": decision.get("framing"),
        "answer": decision.get("answer"),
        "confidence": decision.get("confidence"),
        "attempt": None,
        "at": _calibration_epoch(decision.get("at")),
    }
    if prediction["at"] is None:
        raise LedgerError("jev_ledger_corrupt", "decision %r carries an `at` no clock can read, "
                          "so its row would silently leave the ledger" % (decision["id"],))
    if outcome is None:
        return (prediction, None)
    if _calibration_epoch(outcome.get("at")) is None:
        raise LedgerError("jev_ledger_corrupt", "the outcome for decision %r carries an `at` no "
                          "clock can read, so its row would silently leave the ledger"
                          % (decision["id"],))
    resolution = {
        "kind": "resolve",
        "id": decision["id"],
        "source": "jev_calibration join()",
        "actual": decision.get("answer") if outcome.get("correct") else _DID_NOT_STAND,
        "at": _calibration_epoch(outcome.get("at")),
    }
    return (prediction, resolution)


def jev_rows(decisions_path, outcomes_path):
    """Every typed decision row, read through the calibration ledger, shaped like this ledger's
    own pairs.

    Returns a list of (prediction, resolution or None) tuples whose predictor is "jev". Never
    writes. A MISSING or unreadable path returns an EMPTY LIST and is reported as NO-DATA by the
    caller, never as zero rows that happen to look clean. A ledger that is present but CORRUPT,
    or whose segments could not all be listed, BLOCKS with LedgerError: a partial view of jev's
    rows would otherwise be scored as if it were the whole of them.
    """
    for name, value in (("decisions_path", decisions_path), ("outcomes_path", outcomes_path)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("jev_rows() wants a non-empty %s, got %r" % (name, value))
    calibration = _calibration_ledger()
    try:
        joined = calibration.join(decisions_path, outcomes_path)
    except (OSError, UnicodeDecodeError):
        return []
    corrupt_decisions = joined.get("corrupt_decisions") or []
    corrupt_outcomes = joined.get("corrupt_outcomes") or []
    if not joined.get("reliable") or corrupt_decisions or corrupt_outcomes:
        raise LedgerError(
            "jev_ledger_corrupt",
            "the calibration ledger could not be read whole (%d corrupt decision line(s), %d "
            "corrupt outcome line(s), reliable=%r): jev's rows are NO-DATA, never a partial "
            "score" % (len(corrupt_decisions), len(corrupt_outcomes), joined.get("reliable")))
    out = []
    for item in joined["joined"]:
        out.append(_jev_pair(item["decision"], item["outcome"]))
    for decision in joined["unmatched_decisions"]:
        out.append(_jev_pair(decision, None))
    return out


def all_pairs(path, decisions_path, outcomes_path):
    """Pairs for the six locally stored predictors, plus jev's rows read from their own ledger.

    Returns a dict:
      local             {id: (prediction, resolution or None, ordered)} for the six predictors
                        this file stores. jev's retired copies are dropped here, and only here:
                        load() itself is unchanged, so this module's own selftest and every
                        scratch-file caller still read back exactly what they wrote.
      predictors        sorted names of the predictors actually present among those rows, which
                        is exactly the six deterministic ones once the retired copy is dropped.
      skipped_jev_rows  how many retired jev predict rows were dropped from `local`, so the copy
                        the retired import left behind is COUNTED here, not silently gone.
      jev               {id: (prediction, resolution or None, ordered)} read from the calibration
                        ledger through jev_rows().
      jev_no_data       True when jev's rows are NO-DATA: the calibration ledger is absent, or
                        jev_rows() refused a corrupt read. An unreadable ledger is never dressed
                        up as jev having no rows.
      jev_refusal       the LedgerError code when jev_rows() refused, else None.
    """
    if not isinstance(path, (str, bytes, os.PathLike)):
        raise ValueError("all_pairs() wants a ledger path, got %s" % type(path).__name__)
    rows = load(path)
    kept = []
    skipped_jev = 0
    for row in rows:
        if row.get("kind") == "predict" and row.get("predictor") == "jev":
            skipped_jev += 1
            continue
        kept.append(row)
    refusal = None
    jev_flat = []
    try:
        for prediction, resolution in jev_rows(decisions_path, outcomes_path):
            jev_flat.append(prediction)
            if resolution is not None:
                jev_flat.append(resolution)
    except LedgerError as exc:
        refusal = exc.code
        jev_flat = []
    on_disk = False
    try:
        on_disk = os.path.isfile(decisions_path) and os.path.isfile(outcomes_path)
    except (TypeError, ValueError, OSError):
        on_disk = False
    local = pair(kept)
    return {
        "local": local,
        "predictors": sorted({p["predictor"] for p, _r, _o in local.values()}),
        "skipped_jev_rows": skipped_jev,
        "jev": pair(jev_flat) if jev_flat else {},
        "jev_no_data": refusal is not None or not on_disk,
        "jev_refusal": refusal,
    }


def outcome_exists(subject):
    """True when an outcome artifact for this subject is already on disk.

    The artifact is the run STATUS harvest() already reads: a unit-runs folder named
    "<subject>-<six digits>" carrying a STATUS file. Without one, nothing on disk knows the
    answer yet, so a claim made now is still a forecast. A machine with no evidence tree, or a
    subject with no run folder, reads False: an absent artifact is an absent outcome, and
    refusing every prediction on a bare machine would fail in the wrong direction.
    """
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("outcome_exists() wants a non-empty subject string, got %r" % (subject,))
    runs = os.path.expanduser(os.path.join("~", ".claude", "evidence", "unit-runs"))
    try:
        names = sorted(os.listdir(runs))
    except (OSError, TypeError, ValueError):
        return False
    wanted = re.compile(r"^%s-\d{6}$" % re.escape(subject.strip()))
    for name in names:
        if not wanted.match(name):
            continue
        try:
            if os.path.isfile(os.path.join(runs, name, "STATUS")):
                return True
        except (OSError, TypeError, ValueError):
            continue
    return False


def pair(rows):
    """{id: (prediction, resolution or None, ordered)}. The LAST prediction and the FIRST resolution
    win, selected by the R4 sort. `ordered` is True only when a resolution exists AND its `at` is
    strictly greater than the prediction's (R1); it is left True and unused when `resolution is
    None`, since there is nothing to order yet. A resolution that exists but is not ordered is NEVER
    collapsed into `resolution=None`: doing so would make an out of order claim indistinguishable
    from no claim at all, which is the lossy shape this sub unit forbids.

    R4 runs upstream of R1: a row whose `at` is missing or unusable cannot win either slot. Such a
    row is left out of the selection entirely rather than sorted with a default of 0, so an
    undatable predict row leaves its id reading as never predicted and an undatable resolve row
    leaves its id reading as pending. Dated and undatable rows are never compared against each
    other, so a mix of the two cannot raise TypeError, and no row is ever assigned the time 0."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("pair() wants a list of rows, got %s" % type(rows).__name__)
    dated = [x for x in rows if _usable_at(x) is not None]
    preds, res = {}, {}
    for r in sorted(dated, key=_usable_at):
        rid = r.get("id")
        try:
            hash(rid)
        except TypeError:
            continue
        if r.get("kind") == "predict":
            # a predict row with no predictor is dropped here, the same way load() drops it, so
            # score() never reaches a KeyError on a malformed row
            if not isinstance(r.get("predictor"), str):
                continue
            preds[rid] = r
        elif r.get("kind") == "resolve" and rid not in res:
            res[rid] = r
    out = {}
    for k, v in preds.items():
        rv = res.get(k)
        ordered = True if rv is None else (v["at"] < rv["at"])
        out[k] = (v, rv, ordered)
    return out


def score(rows, predictor=None, now=None, ttl_seconds=TTL_SECONDS):
    """{predictor: {...}} with accuracy over RESOLVED predictions only, Brier over those carrying a confidence,
    and calibration in deciles. Unresolved rows are counted and excluded from both, never silently dropped.

    CONTRACT (D5.3): resolved, unresolved and pending are three counters that partition n, so every row
    lands in exactly one of them. accuracy and brier are computed over resolved rows only, so an
    unscoreable row moves neither figure in either direction. coverage is resolved over the KNOWABLE
    population (n minus unresolved), and is None, which the report prints as NO-DATA, when knowable is
    0. A resolution whose `actual` is missing or None is not a resolution: it counts under unresolved,
    beside the existing UNKNOWN_ACTUALS check, and can never be a hit.

    EVERY ROW LANDS IN EXACTLY ONE OF THREE COUNTERS: resolved, unresolved, pending. n is their sum,
    and that invariant is pinned in the selftest, because the defect this module was repaired for was
    a row quietly taking a fourth path into a binary.

    A RESOLUTION THE LEDGER CANNOT ORDER IS UNRESOLVED, and score() reads that verdict from the
    third element of each pair() entry (D5.2's control point, not a second copy of the comparison).
    A row whose resolution exists but is not strictly later than its prediction is counted under
    unresolved: never right, never resolved, and never pending either, because the answer is on the
    ledger and pretending it is absent would be the lossy shape this sub unit forbids."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("score() wants a list of rows, got %s" % type(rows).__name__)
    paired = pair(rows)
    if now is None:
        clock, ttl = 0.0, float("inf")
    else:
        clock, ttl = _number(now, "now"), ttl_seconds
    states = lifecycle(_lifecycle_input(rows, paired), clock, ttl)
    out = {}
    for pid, (p, r, ordered) in paired.items():
        if predictor and p["predictor"] != predictor:
            continue
        s = out.setdefault(p["predictor"], {"n": 0, "resolved": 0, "right": 0, "unresolved": 0,
                                            "pending": 0, "brier_sum": 0.0, "brier_n": 0, "buckets": {},
                                            "OPEN": 0, "RESOLVED": 0, "EXPIRED": 0,
                                            "CANCELLED": 0, "SUPERSEDED": 0, "INVALID": 0})
        s["n"] += 1
        # R2/R3 (P1.b): every id is placed in exactly one lifecycle state FIRST, and ONLY the
        # state RESOLVED reaches the accuracy and Brier denominators. The four terminal states
        # that are not RESOLVED are counted under their own uppercase names in the same summary,
        # so a subject that evaporated is counted and reported and never scored as the predictor
        # having been wrong. `now` is a parameter so a score is reproducible.
        state = states.get(pid)
        if state not in STATES:
            state = "INVALID"
        s[state] = s.get(state, 0) + 1
        if state == "OPEN":
            s["pending"] += 1
            continue
        if state != "RESOLVED":
            s["unresolved"] += 1
            continue
        s["resolved"] += 1
        hit = (p.get("answer") == r.get("actual"))
        s["right"] += 1 if hit else 0
        c = p.get("confidence")
        if isinstance(c, (int, float)):
            s["brier_sum"] += (c - (1.0 if hit else 0.0)) ** 2
            s["brier_n"] += 1
            b = min(9, int(c * 10))
            bk = s["buckets"].setdefault(b, [0, 0])
            bk[0] += 1
            bk[1] += 1 if hit else 0
    for s in out.values():
        s["accuracy"] = (s["right"] / s["resolved"]) if s["resolved"] else None
        s["brier"] = (s["brier_sum"] / s["brier_n"]) if s["brier_n"] else None
        # COVERAGE HAS A CLEAN DENOMINATOR. It was resolved/n, and n carries rows that are
        # unresolved by construction, so the figure mixed "not answered yet" with "never answerable"
        # and read as a yield no amount of harvesting could raise. The denominator is now the
        # KNOWABLE population; the excluded counts are printed beside it rather than hidden inside
        # it. An empty knowable population is None, which prints NO-DATA, never a zero percent that
        # reads like a measured failure.
        knowable = s["n"] - s["unresolved"]
        s["coverage"] = (s["resolved"] / knowable) if knowable else None
        # the base rate is how often this predictor was RIGHT, which is the naive forecast to beat
        s["base_rate"] = s["accuracy"]
        if s["brier"] is not None and s["base_rate"] is not None:
            ref = s["base_rate"] * (1.0 - s["base_rate"])        # Brier of always predicting the base rate
            s["brier_skill"] = (1.0 - s["brier"] / ref) if ref > 0 else None
        else:
            s["brier_skill"] = None
    return out


def trustworthy(s, min_resolved=30):
    """Whether a predictor's number means anything yet.

    A SAMPLE SIZE IS PART OF A VERDICT. An accuracy over 3 resolved predictions is noise, and quoting it as if
    it were a measurement is how a shadow system gets promoted on nothing. Below the floor this says NO-DATA,
    which is the honest answer and the same one every other checker here gives."""
    if not s or not s.get("resolved"):
        return False, "no resolved prediction yet"
    if s["resolved"] < min_resolved:
        return False, "only %d resolved, need %d before the number means anything" % (s["resolved"], min_resolved)
    return True, "%d resolved" % s["resolved"]


def _report_count(stats, key, predictor):
    """A lifecycle count read from one predictor's stats mapping.

    A key that is ABSENT reads as 0, because every stats dict score() builds carries all six
    lifecycle keys and an older caller that predates one of them is not corrupt. A key that IS
    present and is not a whole non-negative count is corrupt input and BLOCKS with ValueError
    naming the predictor, rather than crashing inside a format string or quietly rounding. A bool
    is refused even though it is an int subclass, because a bool where a count belongs is a
    different kind of thing wearing the right type."""
    if key not in stats:
        return 0
    value = stats[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("%s=%r for predictor %r is not a whole non-negative count"
                         % (key, value, predictor))
    return value


def render(summary, min_resolved=30):
    """One block a person reads in ten seconds: per predictor n, resolved, coverage, and either
    the Brier skill score or the words NO-DATA with what is still missing. Never a bare accuracy:
    a predictor answering a question that is true 83 percent of the time scores 83 percent by
    saying yes to everything. The four non RESOLVED lifecycle counts print beside resolved, so a
    reader who sees 3753 OPEN learns the real state of the instrument, which no percentage of the
    resolved rows would have told them."""
    if not isinstance(summary, Mapping):
        raise ValueError("render() wants a mapping of predictor to stats, got %s"
                         % type(summary).__name__)
    if isinstance(min_resolved, bool) or not isinstance(min_resolved, (int, float)):
        raise ValueError("render() wants a real number for min_resolved, got %r"
                         % (min_resolved,))
    floor = float(min_resolved)
    if floor != floor or floor < 0.0:
        raise ValueError("render() wants a non-negative real min_resolved, got %r"
                         % (min_resolved,))
    try:
        names = sorted(summary)
    except TypeError:
        raise ValueError("render() wants predictor names it can sort, got %r"
                         % (list(summary),))
    lines = []
    for predictor in names:
        stats = summary[predictor]
        if not isinstance(stats, Mapping):
            raise ValueError("render() wants a stats mapping for %r, got %s"
                             % (predictor, type(stats).__name__))
        n = _report_count(stats, "n", predictor)
        resolved = _report_count(stats, "resolved", predictor)
        expired = _report_count(stats, "EXPIRED", predictor)
        cancelled = _report_count(stats, "CANCELLED", predictor)
        superseded = _report_count(stats, "SUPERSEDED", predictor)
        invalid = _report_count(stats, "INVALID", predictor)
        coverage = stats.get("coverage")
        if coverage is None:
            cov_text = "NO-DATA"
        else:
            if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) \
                    or coverage != coverage:
                raise ValueError("render() read coverage=%r for predictor %r, which is not a "
                                 "number or NO-DATA" % (coverage, predictor))
            cov_text = "%.4f" % coverage
        ok, reason = trustworthy(stats, int(floor))
        skill = stats.get("brier_skill")
        if not ok:
            verdict = "NO-DATA: %s" % reason
        elif isinstance(skill, bool) or not isinstance(skill, (int, float)) or skill != skill:
            verdict = "NO-DATA: no confidence on any resolved prediction"
        else:
            verdict = "brier_skill=%.4f" % skill
        lines.append("%-12s n=%d resolved=%d EXPIRED=%d CANCELLED=%d SUPERSEDED=%d INVALID=%d "
                     "coverage=%s %s"
                     % (predictor, n, resolved, expired, cancelled, superseded, invalid,
                        cov_text, verdict))
    if not lines:
        lines.append("NO-DATA: no predictor on this ledger")
    return "\n".join(lines)


def board_row(summary):
    """{predictor, n, resolved, coverage, verdict} for the unit board, so the number is seen
    without being asked for. Returns a mapping from predictor name to that five key row. A
    predictor under the resolved floor, or one whose coverage is NO-DATA, carries the verdict
    NO-DATA rather than a number nobody could defend."""
    if not isinstance(summary, Mapping):
        raise ValueError("board_row() wants a mapping of predictor to stats, got %s"
                         % type(summary).__name__)
    try:
        names = sorted(summary)
    except TypeError:
        raise ValueError("board_row() wants predictor names it can sort, got %r"
                         % (list(summary),))
    out = {}
    for predictor in names:
        stats = summary[predictor]
        if not isinstance(stats, Mapping):
            raise ValueError("board_row() wants a stats mapping for %r, got %s"
                             % (predictor, type(stats).__name__))
        n = _report_count(stats, "n", predictor)
        resolved = _report_count(stats, "resolved", predictor)
        coverage = stats.get("coverage")
        if coverage is not None and (isinstance(coverage, bool)
                                     or not isinstance(coverage, (int, float))
                                     or coverage != coverage):
            raise ValueError("board_row() read coverage=%r for predictor %r, which is not a "
                             "number or NO-DATA" % (coverage, predictor))
        ok, _reason = trustworthy(stats, 30)
        if not ok or coverage is None:
            verdict = "NO-DATA"
        else:
            verdict = "OK"
        out[predictor] = {"predictor": predictor, "n": n, "resolved": resolved,
                          "coverage": coverage, "verdict": verdict}
    return out


def gate(summary, floor=0.2):
    """Exit code 0 when every wired predictor is above the coverage floor, 1 when one is below
    it and 2 on NO-DATA. A ledger that fills with claims and never closes them is the failure
    this gate exists to catch, and it is the state the ledger is in today at 0.7 percent
    coverage. It refuses on coverage, never on accuracy, because a predictor that is honestly bad
    is a finding and a predictor nobody ever scores is a lie. NO-DATA (exit 2) beats a below
    floor reading, because a population that could not be measured has not said the thing is
    broken."""
    if not isinstance(summary, Mapping):
        raise ValueError("gate() wants a mapping of predictor to stats, got %s"
                         % type(summary).__name__)
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        raise ValueError("gate() wants a real number for floor, got %r" % (floor,))
    floor_value = float(floor)
    if floor_value != floor_value:
        raise ValueError("gate() wants a real number for floor, got NaN")
    if floor_value < 0.0 or floor_value > 1.0:
        raise ValueError("gate() wants a coverage floor between 0 and 1, got %r" % (floor,))
    if not summary:
        return 2
    try:
        names = sorted(summary)
    except TypeError:
        raise ValueError("gate() wants predictor names it can sort, got %r" % (list(summary),))
    below = False
    nodata = False
    for predictor in names:
        stats = summary[predictor]
        if not isinstance(stats, Mapping):
            raise ValueError("gate() wants a stats mapping for %r, got %s"
                             % (predictor, type(stats).__name__))
        coverage = stats.get("coverage")
        if coverage is None:
            nodata = True
            continue
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) \
                or coverage != coverage:
            nodata = True
            continue
        if coverage < floor_value:
            below = True
    if nodata:
        return 2
    if below:
        return 1
    return 0


def selftest():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "pred.jsonl")
    predict("jev", "U1", "pass", confidence=0.9, question="buildable", path=p, now=1)
    predict("jev", "U2", "pass", confidence=0.8, question="buildable", path=p, now=2)
    predict("jev", "U3", "fail", confidence=0.6, question="buildable", path=p, now=3)
    resolve(pid_for("jev", "U1", "buildable"), "pass", "grader exit 0", path=p, now=4)
    resolve(pid_for("jev", "U2", "buildable"), "fail", "grader exit 1", path=p, now=5)
    predict("grade", "U9", "pass", path=p, now=6)
    resolve(pid_for("grade", "U9", None), "unresolvable", "lane abandoned", path=p, now=7)
    rows = load(p)
    s = score(rows)
    cases = [
        ("a prediction is recorded", s["jev"]["n"] == 3),
        ("only resolved ones are scored", s["jev"]["resolved"] == 2),
        ("accuracy counts the right ones", abs(s["jev"]["accuracy"] - 0.5) < 1e-9),
        ("an unresolved prediction is not counted wrong", s["jev"]["n"] - s["jev"]["resolved"] == 1),
        ("coverage exposes the gap this module exists for", abs(s["jev"]["coverage"] - 2/3) < 1e-9),
        ("brier is computed over confident predictions",
         abs(s["jev"]["brier"] - ((0.9-1)**2 + (0.8-0)**2) / 2) < 1e-9),
        ("unresolvable is its own state, never right and never wrong",
         s["grade"]["unresolved"] == 1 and s["grade"]["resolved"] == 0),
        ("an unresolvable prediction does not inflate accuracy", s["grade"]["accuracy"] is None),
        ("a small sample is NO-DATA, not a verdict", trustworthy(s["jev"])[0] is False),
        # THE ZERO RESOLVED CASE, which is a different branch of trustworthy() from the one above and
        # was untested until the registered mutation sweep survived a flip of it. A predictor with
        # 3,765 predictions and nothing resolved is the exact live shape of this ledger today, and
        # calling it trustworthy would be a hole reading as a green.
        ("a predictor with nothing resolved is never trustworthy, however many predictions it holds",
         trustworthy({"n": 3765, "resolved": 0, "unresolved": 0, "pending": 3765})[0] is False),
        ("an empty stats dict is not trustworthy either", trustworthy({})[0] is False),
        ("the floor is stated in the reason", "need 30" in trustworthy(s["jev"])[1]),
        ("a resolution with no source is refused",
         _raises(lambda: resolve("x", "y", "", path=p))),
        ("an unknown predictor is refused rather than silently added",
         _raises(lambda: predict("astrology", "U1", "yes", path=p))),
        ("a confidence outside 0 to 1 is refused",
         _raises(lambda: predict("jev", "U4", "pass", confidence=1.4, path=p))),
        ("the FIRST resolution wins, so truth is not overwritten later", True),
        # the two defects an adversarial review found in this file's first draft, each pinned so a
        # revert cannot pass quietly
        ("two attempts on one subject are DIFFERENT predictions, so a retry cannot resolve the earlier one",
         pid_for("jev", "U1", "q", attempt="r1") != pid_for("jev", "U1", "q", attempt="r2")),
        ("an attemptless prediction keeps the old identity, so nothing already recorded is orphaned",
         pid_for("jev", "U1", "q") == pid_for("jev", "U1", "q", attempt=None)),
        ("a predictor that always says the same thing has no skill, however good its accuracy looks",
         _no_skill()),
        ("skill is reported as None rather than invented when there is no confidence to score",
         score([{"kind": "predict", "id": "a", "predictor": "grade", "subject": "S", "answer": "pass",
                 "at": 1}, {"kind": "resolve", "id": "a", "actual": "pass", "at": 2}])["grade"]["brier_skill"]
         is None),
        ("an unreadable ledger reads as empty rather than raising", load("/no/such/f.jsonl") == []),
        # THE THIRD STATE. One fixture per property: a fixture that trips two guards proves neither,
        # so RUNNING, READY-UNPROBED, the unknown word and the malformed row each get their own.
        ("a build still RUNNING is not yet known, so it is neither a pass nor a fail",
         outcome_for_status("RUNNING") == UNRESOLVED),
        ("an unprobed build is not yet proven, so it is not a pass",
         outcome_for_status("READY-UNPROBED") == UNRESOLVED),
        ("a word this table never learned is UNRESOLVED, because there is no catch-all verdict",
         outcome_for_status("SOME-FUTURE-WORD") == UNRESOLVED),
        ("an empty status word is UNRESOLVED", outcome_for_status("") == UNRESOLVED),
        ("a status word of the wrong type is UNRESOLVED rather than a crash",
         outcome_for_status(None) == UNRESOLVED),
        ("READY is still a pass, so the refusals above are not a function that refuses everything",
         outcome_for_status("READY") == PASS),
        ("a build the gates refused is still a fail, so the third state did not swallow the verdicts",
         outcome_for_status("QUARANTINE") == FAIL and outcome_for_status("EXHAUSTED") == FAIL),
        ("every row lands in exactly one counter, so nothing takes a fourth path into a binary",
         _counters_partition_n()),
        ("coverage excludes rows that are unresolved by construction", _coverage_denominator()),
        ("coverage over a population with nothing knowable is NO-DATA, never zero percent",
         _all_unresolved_coverage() is None),
        ("a predict row with no predictor is dropped at load rather than crashing the score",
         _malformed_row_dropped()),
        ("a timestamp of epoch 0 is a real time, not a missing one", _epoch_zero_kept()),
        ("a second resolution with a different outcome does not overwrite the first", _first_wins()),
        ("one prediction and no resolution scores as pending, never as wrong", _single_row_pending()),
        ("an empty ledger yields no predictor rather than an invented zero", score(load("/no/x")) == {}),
        # _raises IS THE FIXTURE FOR THREE CASES ABOVE, so a version of it that always returns True
        # would make all three tautologies. The registered mutation sweep found exactly that: flipping
        # its `return False` to `return True` left every check green. This pins the negative half.
        ("_raises reports False when nothing raises, so the refusal cases are not tautologies",
         _raises(lambda: None) is False),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def _no_skill():
    """A predictor answering "fail" every time, on a population that fails 90 percent of the time, has high
    accuracy and no skill. This is the case that makes accuracy the wrong headline."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    for i in range(10):
        predict("grade", "S%d" % i, "fail", confidence=0.9, question="good", path=q, now=i)
        resolve(pid_for("grade", "S%d" % i, "good"), "fail" if i < 9 else "pass",
                "fixture", path=q, now=100 + i)
    s = score(load(q))["grade"]
    return s["accuracy"] >= 0.85 and (s["brier_skill"] is None or s["brier_skill"] <= 0.0)


def _counters_partition_n():
    """n == resolved + unresolved + pending, for a population holding one of each. THIS is the
    property the missing third state broke: a row that is not resolved had nowhere to be counted."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    predict("grade", "A", "pass", path=q, now=1)                       # will be resolved
    resolve(pid_for("grade", "A", None), "pass", "fixture", path=q, now=2)
    predict("grade", "B", "pass", path=q, now=3)                       # will be unresolved
    resolve(pid_for("grade", "B", None), "unresolved", "fixture", path=q, now=4)
    predict("grade", "C", "pass", path=q, now=5)                       # stays pending
    s = score(load(q))["grade"]
    return (s["n"] == 3 and s["resolved"] == 1 and s["unresolved"] == 1 and s["pending"] == 1
            and s["n"] == s["resolved"] + s["unresolved"] + s["pending"])


def _coverage_denominator():
    """Two predictions, one resolved and one unresolvable. Coverage is 1/1 over the knowable row,
    not 1/2 over a population that silently included a row nobody could ever resolve."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    predict("probe", "A", "pass", path=q, now=1)
    resolve(pid_for("probe", "A", None), "pass", "fixture", path=q, now=2)
    predict("probe", "B", "pass", path=q, now=3)
    resolve(pid_for("probe", "B", None), "unresolvable", "lane abandoned", path=q, now=4)
    return abs(score(load(q))["probe"]["coverage"] - 1.0) < 1e-9


def _all_unresolved_coverage():
    """Nothing knowable at all. A ratio over an empty denominator is NO-DATA, never zero percent."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    predict("probe", "A", "pass", path=q, now=1)
    resolve(pid_for("probe", "A", None), "unresolvable", "lane abandoned", path=q, now=2)
    return score(load(q))["probe"]["coverage"]


def _malformed_row_dropped():
    """A predict row carrying no predictor used to reach score() and raise KeyError there."""
    import tempfile, os as _os, json as _json
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    with open(q, "w", encoding="utf-8") as fh:
        fh.write(_json.dumps({"at": 1, "kind": "predict", "id": "x", "subject": "S",
                              "answer": "pass"}) + "\n")
        fh.write("{ not json at all\n")
        fh.write(_json.dumps({"at": 2, "kind": "predict", "id": "y", "predictor": "grade",
                              "subject": "S2", "answer": "pass"}) + "\n")
    loaded = load(q)
    return len(loaded) == 1 and score(loaded)["grade"]["n"] == 1


def _epoch_zero_kept():
    """A row stamped at epoch 0 is falsy, and a falsy timestamp read as missing is a defect that has
    already landed in a sibling module on this estate. predict() tests `now is not None`, and this
    pins that: the recorded time must be exactly 0, not a wall clock reading."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    r = predict("grade", "Z", "pass", path=q, now=0)
    loaded = load(q)
    return r["at"] == 0 and loaded and loaded[0]["at"] == 0


def _first_wins():
    """One subject resolved twice with OPPOSITE outcomes. The first stands: an outcome that can be
    reopened is an outcome a later pass can quietly flip, which is why UNRESOLVED is never written."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    predict("council", "A", "fail", path=q, now=1)
    resolve(pid_for("council", "A", None), "fail", "first", path=q, now=2)
    resolve(pid_for("council", "A", None), "pass", "second, later, contradicts", path=q, now=3)
    s = score(load(q))["council"]
    return s["resolved"] == 1 and s["right"] == 1


def _single_row_pending():
    """Exactly one row, never answered. It is pending, and accuracy over it is None, not zero."""
    import tempfile, os as _os
    q = _os.path.join(tempfile.mkdtemp(), "p.jsonl")
    predict("jev", "ONLY", "pass", path=q, now=1)
    s = score(load(q))["jev"]
    return s["n"] == 1 and s["pending"] == 1 and s["resolved"] == 0 and s["accuracy"] is None


def _raises(fn):
    try:
        fn()
        return False
    except (ValueError, TypeError):
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", default="score",
                    choices=["predict", "resolve", "harvest", "score"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--confidence", type=float, default=None)
    ap.add_argument("--question", default=None)
    ap.add_argument("--source", default=None)
    if "--selftest" in (argv or sys.argv[1:]):
        return selftest()
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.cmd == "predict":
        if len(a.rest) < 3:
            print("predict needs <predictor> <subject> <answer>"); return 2
        r = predict(a.rest[0], a.rest[1], a.rest[2], a.confidence, a.question)
        print("recorded %s" % r["id"]); return 0
    if a.cmd == "resolve":
        if len(a.rest) < 2 or not a.source:
            print("resolve needs <id> <actual> --source '<what established it>'"); return 2
        resolve(a.rest[0], a.rest[1], a.source)
        print("resolved %s" % a.rest[0]); return 0
    if a.cmd == "harvest":
        n = harvest()
        print("harvested %d outcome(s) that were already on disk" % n); return 0
    rows = load()
    if not rows:
        print("NO-DATA: no prediction has been recorded yet"); return 2
    # ACCURACY IS NOT THE HEADLINE, and printing it as one was a defect in this file's first draft that an
    # adversarial review caught. With a skewed base rate, and most builds here DO fail, a predictor that
    # always answers "fail" scores superb accuracy while carrying no information at all. The honest headline
    # is the BRIER SKILL SCORE: the Brier score against the base rate of the outcomes themselves. Positive
    # means it beats always guessing the base rate; zero or negative means it adds nothing, however good its
    # accuracy looks.
    # RESOLVED AND UNRESOLVED ARE PRINTED SEPARATELY, and every figure to their right is computed
    # over the resolved population alone. A single "n" with an accuracy beside it is exactly the
    # number this estate forbids quoting: part of the population is not a verdict, and the reader
    # cannot see how much. pending and unresolved are now their own columns, so n - resolved is
    # accounted for on the page instead of being folded into a percentage.
    print("%-11s %6s %9s %11s %8s %9s %7s %8s %7s  %s"
          % ("predictor", "n", "resolved", "unresolved", "pending", "coverage",
             "base", "brier", "skill", "trustworthy"))
    for name, s in sorted(score(rows).items()):
        ok, why = trustworthy(s)
        skill = s.get("brier_skill")
        print("%-11s %6d %9d %11d %8d %9s %7s %8s %7s  %s"
              % (name, s["n"], s["resolved"], s["unresolved"], s["pending"],
                 ("%.0f%%" % (100 * s["coverage"])) if s["coverage"] is not None else "NO-DATA",
                 ("%.2f" % s["base_rate"]) if s.get("base_rate") is not None else "n/a",
                 ("%.3f" % s["brier"]) if s["brier"] is not None else "n/a",
                 ("%+.2f" % skill) if skill is not None else "n/a",
                 ("yes, " + why) if ok else ("NO-DATA: " + why)))
    print("\nRESOLVED is the only denominator under base, brier and skill. UNRESOLVED rows were answered")
    print("\"nobody will know\"; PENDING rows have no answer yet. COVERAGE is resolved over the KNOWABLE rows")
    print("(n minus unresolved), so it measures harvesting, not abandonment.")
    print("SKILL is the Brier score against this predictor's own base rate. At or below zero it adds nothing,")
    print("whatever its accuracy reads, because always guessing the base rate would have done as well.")
    return 0


def harvest(path=None):
    """Close every prediction whose truth is already on disk. Returns how many it resolved.

    Deliberately conservative: it resolves ONLY where the artifact it reads is unambiguously the outcome of
    that exact prediction, keyed by the same subject id. Anything it is unsure about is left pending, because a
    wrong auto resolution is worse than a missing one: it poisons the calibration the ledger exists to build."""
    import glob
    import re
    rows = load(path)
    pending = {pid: p for pid, (p, r, _ordered) in pair(rows).items() if r is None}
    if not pending:
        return 0
    # the newest run STATUS per sub unit is the outcome for anything predicted ABOUT that sub unit
    runs = os.path.expanduser("~/.claude/evidence/unit-runs")
    status = {}
    # SORTED, so a tie is decided the same way twice. Two run folders for one sub unit with the
    # same mtime (a duplicated build id, which happens when a runner is restarted inside the same
    # second) used to be broken by os.scandir order, which is not stable, so the same ledger could
    # read READY on one pass and EXHAUSTED on the next. Sorting plus `>=` makes the LATER basename
    # win, which is the timestamp suffix, and is the same rule brother_pass.newest_status uses.
    for d in sorted(glob.glob(runs + "/*-*/")):
        sub = re.sub(r"-\d{6}$", "", os.path.basename(d.rstrip("/")))
        f = os.path.join(d, "STATUS")
        if not os.path.isfile(f):
            continue
        try:
            # `with`, because the bare open() this replaced leaked a descriptor per run folder and
            # this loop walks every run folder on the machine
            with open(f, encoding="utf-8") as fh:
                when, head = os.path.getmtime(d), fh.read().split()[0]
        # FAIL DIRECTION: unreadable, empty, or not decodable text. Every one of these skips the
        # folder, so the prediction stays PENDING. IndexError is the empty STATUS file (split()[0]
        # on no words) and UnicodeDecodeError is a truncated or binary one. None of them is a
        # verdict about the build.
        except (OSError, IndexError, UnicodeDecodeError):
            continue
        if sub not in status or when >= status[sub][0]:
            status[sub] = (when, head)
    n = 0
    for pid, p in pending.items():
        sub = p.get("subject")
        if sub not in status:
            continue
        # THE ATTEMPT GUARD. The newest STATUS describes the LATEST attempt. A prediction that named an
        # attempt can only be resolved by evidence from THAT attempt, and this harvester reads a per sub unit
        # status that carries no attempt, so it declines rather than guessing. Resolving attempt A's claim
        # with attempt B's verdict is the confident lie an automatic resolver most easily tells.
        if p.get("attempt"):
            continue
        head = status[sub][1]
        # THE DECISION POINT, and the only one. Every word routes through STATUS_OUTCOME, which has
        # no catch-all, so a word nobody taught this module is UNRESOLVED rather than a verdict
        # picked by whichever branch happened to be the `else`.
        #
        # FAIL DIRECTION, spelled out for each way a row can be wrong:
        #   missing STATUS file     declined above, row stays pending
        #   empty or truncated      declined above, row stays pending
        #   unrecognised word       UNRESOLVED here, row stays pending
        #   RUNNING (in flight)     UNRESOLVED here. Not yet known is NOT a failure
        #   READY-UNPROBED          UNRESOLVED here. Not yet proven is NOT a pass
        #   arrives twice           resolve() writes a second row and pair() keeps the FIRST, so an
        #                           outcome already recorded is never overwritten by a later one
        # Nothing on that list can produce a pass, and nothing on it can produce a fail.
        actual = outcome_for_status(head)
        if actual == UNRESOLVED:
            continue
        resolve(pid, actual, "newest run STATUS for %s reads %s" % (sub, head), path)
        n += 1
    return n


if __name__ == "__main__":
    sys.exit(main())
