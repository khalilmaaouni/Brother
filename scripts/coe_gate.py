#!/usr/bin/env python3
"""COE-07 of the Council of Experts subsystem: the scorecard becomes a
selection row's done check.

WHY THIS EXISTS. Every problem in this estate is meant to get a METHODOLOGY
SELECTION row before it gets a BUILD row. That ordering is a sentence in a
plan until something refuses to let a build row open while its selection is
still open, or lets one open on a recorded ceiling with nobody named as
having decided to proceed. Without a gate, "selection before build" is
advisory, and this estate has already written down what an advisory control
is worth: nothing, because nobody has to obey it. This module is that gate.

It composes coe_loop.LoopResult (COE-06's own terminal outcomes: PASS,
CEILING, REJECTED, ABANDONED) and turns them into a durable, on-disk
SelectionRecord per selection row, then answers exactly one question for a
build row: may it open. It never scores anything, never nominates a
council, never runs a round. It is not a second copy of coe_loop's
termination logic; see _OUTCOME_HANDLERS below, which is a lookup, not a
re-derivation, of what each of coe_loop.OUTCOMES means to a build row.

THREE STATES A SELECTION ROW CAN BE IN, and this module is the reason a
morning reader can always tell which:

  MISSING   the row_id is not a key in the selections mapping (or store) at
            all. Nobody has started it. This is a data-entry / planning
            gap, not a loop in progress.
  OPEN      the row_id IS a key, but its value is None: a selection was
            started (mark_open(), or an orchestrator's own board write) and
            has not yet closed with a coe_loop.LoopResult. The council loop
            may still be running rounds.
  CLOSED    the row_id's value is a SelectionRecord: coe_loop finished and
            close_selection() recorded the outcome. May be PASS, CEILING,
            REJECTED or ABANDONED; only PASS, and CEILING-with-a-named-owner-
            decision, open a build row.

Conflating MISSING with OPEN is exactly the failure the worker brief calls
out by name: "one says nobody started, the other says somebody did and has
not finished, and a morning reader needs to know which." The mechanism here
is a Python-level sentinel distinguishing "key absent" from "key present,
value None" (see _MISSING in may_open), and on disk the same distinction is
a JSON `null` (OPEN) versus a JSON object (CLOSED) versus the key not
appearing in the file at all (MISSING).

THE CEILING DECISION is supplied by the caller opening the build row, on
the build_row itself (build_row["ceiling_decision"] = {"owner": ...,
"reason": ...}), not persisted on the SelectionRecord. This is a deliberate
simplification, not an oversight:
# ponytail: a ceiling decision lives on the build row that used it, not on
# the shared selection record; upgrade to a persisted per-selection decision
# log if a second build row ever needs to reuse the same owner's call
# without restating it. Until then, persisting it globally on the selection
# would let one owner's decision for one build row silently authorize a
# different build row's opening later, which is the same "a scan that
# prints a count and proceeds is not a gate" shape this whole subsystem
# exists to refuse, just one level up.

CONTINGENCY, so a caller and a hook both know which way this fails:
  On any doubt, the build row is REFUSED. A needless refusal costs a
  conversation; a wrongly opened build row costs the work this whole
  process exists to prevent. Every branch in may_open() that cannot prove
  PASS or a validly-decided CEILING returns False.
  A malformed on-disk store (bad JSON, wrong shape, an entry that fails
  validation) RAISES GateRefused. It is never read as an empty store: an
  empty store (no file at all) is the safe, deliberate default for "no
  selections exist yet"; a store that exists but cannot be trusted is a
  different, worse thing, and treating it as empty would silently discard
  whatever CLOSED records it actually held.
  A person legitimately proceeds past a recorded CEILING by naming an owner
  and a reason on the build row's own ceiling_decision when requesting to
  open it; this module does not gate WHO may be named there (that is an
  organisational fact this estate keeps in the founder-decision record, not
  in this file), only THAT a name and a reason are both present.
  WHO IS TOLD: every refusal main() ever hands to a hook is appended to a
  durable log file next to the store (see _refusal_log_path()), and
  count_refusals() answers "how many, ever" from that log. Nothing here
  emails or pages anyone; the log is the durable, countable record the
  worker brief asked for, so this gate is never the next one nobody could
  recalibrate because it wrote its refusals nowhere.

EDGE LIST, walked explicitly (docs/plan/ORCH-1020-WORKER-CONTRACT.md's
standing law):
  - a build row that is its own selection row: no special case. The lookup
    is keyed on selection_row_id alone; row_id and selection_row_id being
    equal changes nothing about how the value is resolved or checked.
    test_build_row_can_be_its_own_selection_row proves this.
  - a selection closed for a DIFFERENT problem than the build row names:
    refused. Compared only when BOTH sides name a problem (build_row.get
    ("problem") and the record's .problem are both non-None); if either
    side never named one, there is nothing to contradict, so it is not
    treated as a mismatch (see _problem_mismatch()).
  - a store file that is missing: _load_store() returns {} (nothing is
    OPEN, nothing is CLOSED; every row_id reads as MISSING). This is the
    safe direction for exactly the CONTINGENCY reason above: absence of
    data must never be read as evidence of a pass.
  - a store file that is malformed: _load_store() raises GateRefused. Never
    coerced to empty; see CONTINGENCY.
  - a selection closed twice with different outcomes: close_selection()
    raises GateRefused rather than silently overwriting a prior decision
    with a new one (see close_selection's own docstring).
  - a ceiling decision naming an owner but no reason (or vice versa, or
    neither, or an empty string for either): rejected by
    _valid_ceiling_decision(), which requires both to be non-empty strings.
  - a build row id that is empty: refused in may_open() with a reason that
    does not guess which row this is, since it cannot be named.
  - a selection whose loop result has no criteria at all (an ABANDONED
    LoopResult with rounds=(), e.g. a pre-loop nomination refusal or
    max_rounds=0): handled with no special code. close_selection() only
    ever reads loop_result.outcome, .final_score, .capping_criterion and
    .reason; it never inspects .rounds or any round's per-criterion matrix,
    so an empty-rounds LoopResult closes exactly as honestly as a populated
    one (as ABANDONED, which never opens a build row either way).

THE BAD STATE A GREEN RUN WOULD ALSO PASS, NAMED SO IT IS NEVER SHIPPED
ACCIDENTALLY: a gate that refuses every build row, always, for any reason
or none. Such a gate would pass every "is this refused" test in this file
and would look, from the outside, exactly as safe as this one, while
stopping all work forever, which is precisely the "advisory control nobody
can act on" failure this estate is trying to stop building, just moved from
"never enforced" to "never passable." test_pass_selection_opens_build_row
below is the one test that a universal-refuser cannot pass: it asserts a
selection closed PASS DOES open its build row.

Python 3.9 floor, standard library only, no network, no subprocess, no
sleep. This module does not schedule, dispatch, merge, score, arbitrate or
mark a build unit done; it reads a council's already-closed verdict and
answers one question about one build row.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import coe_loop

SCHEMA_VERSION = "coe-gate-selections-v1"

#: The four outcomes close_selection() will ever see, mapped explicitly so
#: an outcome string outside this set raises rather than silently guessing
#: which side of "may this open" it belongs on. Sourced from coe_loop, never
#: restated as a second list (worker contract: "never restate a list it
#: already holds").
_KNOWN_LOOP_OUTCOMES = coe_loop.OUTCOMES


class GateRefused(Exception):
    """Something this gate will not do without a human, or cannot trust:
    a malformed store, a conflicting re-close of an already-closed
    selection, an unrecognised loop outcome, or a selections value that is
    neither None (open) nor a SelectionRecord (closed). Never raised for an
    ordinary business refusal of a build row (missing selection, open
    selection, unresolved ceiling, wrong problem): those are the (False,
    reason) return of may_open(), which a caller is expected to see on
    every call, not only the ones that go wrong. See the module docstring's
    CONTINGENCY section.
    """


@dataclass(frozen=True)
class SelectionRecord(object):
    """One closed selection row, as read from or written to the store.

    row_id             the selection row's own id.
    problem            the problem id it was scored for, or None if the
                       caller never named one.
    outcome            one of coe_loop.OUTCOMES: PASS, CEILING, REJECTED,
                       ABANDONED.
    final_score        the resolved numeric score (int) that decided the
                       outcome, or None (mirrors coe_loop.LoopResult).
    capping_criterion  the criterion that produced final_score, or None.
    reason             coe_loop's own plain-language reason for the outcome.
    closed_at          ISO-8601 timestamp string, when close_selection()
                       wrote this record.
    """

    row_id: str
    problem: Optional[str]
    outcome: str
    final_score: Optional[int]
    capping_criterion: Optional[str]
    reason: str
    closed_at: str


def _now_iso(now=None):
    if now is not None:
        return now.isoformat() if hasattr(now, "isoformat") else str(now)
    return datetime.now(timezone.utc).isoformat()


def _refusal_log_path(store_path):
    return str(store_path) + ".refusals.log"


def _log_refusal(reason, *, store_path):
    """Append one refusal to the durable, countable log beside the store.

    This is the fix the worker brief names directly: "tonight this estate's
    load gate refused ten commands and wrote them nowhere, so nobody can say
    whether it has ever prevented a real problem." Called only from main(),
    the hook-facing entry point: may_open() itself stays a pure function
    (no I/O) so it is fast and trivial to unit test, and every real refusal
    still gets logged because a hook is expected to reach a refusal only
    through main(), never by importing may_open() directly and discarding
    the answer.
    """
    log_path = _refusal_log_path(store_path)
    record = {"at": _now_iso(), "reason": reason}
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        raise GateRefused(
            "could not write the refusal log at %r: %s: %s"
            % (log_path, type(exc).__name__, exc)
        ) from exc


def count_refusals(store_path):
    """How many refusals main() has ever logged for this store. NO-DATA is
    represented as 0 with no file, since "never refused" and "no log yet"
    are the same fact for a store nothing has ever been checked against;
    a store with a log that cannot be read (permissions, corrupt lines) is a
    GateRefused, never silently reported as 0, so a broken log is never
    mistaken for a clean record.
    """
    log_path = _refusal_log_path(store_path)
    if not os.path.exists(log_path):
        return 0
    count = 0
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                json.loads(line)  # validate, never coerce a bad line to 0
                count += 1
    except (OSError, json.JSONDecodeError) as exc:
        raise GateRefused(
            "refusal log at %r could not be read: %s: %s"
            % (log_path, type(exc).__name__, exc)
        ) from exc
    return count


def _record_to_json(record):
    return {
        "problem": record.problem,
        "outcome": record.outcome,
        "final_score": record.final_score,
        "capping_criterion": record.capping_criterion,
        "reason": record.reason,
        "closed_at": record.closed_at,
    }


def _record_from_json(row_id, data):
    if not isinstance(data, dict):
        raise GateRefused(
            "store entry for %r is neither null (open) nor an object "
            "(closed): %r" % (row_id, data)
        )
    required = {"outcome", "reason", "closed_at"}
    missing = required - set(data.keys())
    if missing:
        raise GateRefused(
            "store entry for %r is missing keys %s" % (row_id, sorted(missing))
        )
    outcome = data["outcome"]
    if outcome not in _KNOWN_LOOP_OUTCOMES:
        raise GateRefused(
            "store entry for %r carries outcome %r, which is not one of %s"
            % (row_id, outcome, sorted(_KNOWN_LOOP_OUTCOMES))
        )
    final_score = data.get("final_score")
    if final_score is not None and (
        isinstance(final_score, bool) or not isinstance(final_score, int)
    ):
        raise GateRefused(
            "store entry for %r has a non-integer final_score: %r"
            % (row_id, final_score)
        )
    problem = data.get("problem")
    if problem is not None and not isinstance(problem, str):
        raise GateRefused(
            "store entry for %r has a non-string problem: %r" % (row_id, problem)
        )
    capping_criterion = data.get("capping_criterion")
    if capping_criterion is not None and not isinstance(capping_criterion, str):
        raise GateRefused(
            "store entry for %r has a non-string capping_criterion: %r"
            % (row_id, capping_criterion)
        )
    if not isinstance(data["reason"], str) or not isinstance(data["closed_at"], str):
        raise GateRefused(
            "store entry for %r has a non-string reason or closed_at" % (row_id,)
        )
    return SelectionRecord(
        row_id=row_id,
        problem=problem,
        outcome=outcome,
        final_score=final_score,
        capping_criterion=capping_criterion,
        reason=data["reason"],
        closed_at=data["closed_at"],
    )


def load_store(store_path):
    """Dict[row_id, Optional[SelectionRecord]] read from disk.

    A missing file returns {} (see the module docstring's CONTINGENCY and
    EDGE LIST: absence of a store is the safe default for "nothing has
    closed yet," never for "everything passed"). A present-but-unreadable
    or wrong-shaped file raises GateRefused; it is never silently treated
    as {} instead, because a store that exists but cannot be trusted may
    still hold real CLOSED records this function has no business discarding.
    """
    if not os.path.exists(store_path):
        return {}
    try:
        with open(store_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise GateRefused(
            "selection store at %r could not be read as JSON: %s: %s"
            % (store_path, type(exc).__name__, exc)
        ) from exc

    if not isinstance(raw, dict) or "selections" not in raw:
        raise GateRefused(
            "selection store at %r is not the expected shape (a JSON object "
            "with a 'selections' key)" % (store_path,)
        )
    selections_raw = raw["selections"]
    if not isinstance(selections_raw, dict):
        raise GateRefused(
            "selection store at %r has a non-object 'selections' value"
            % (store_path,)
        )

    result: Dict[str, Optional[SelectionRecord]] = {}
    for row_id, value in selections_raw.items():
        if not isinstance(row_id, str) or not row_id:
            raise GateRefused(
                "selection store at %r has a non-string or empty row id key: "
                "%r" % (store_path, row_id)
            )
        if value is None:
            result[row_id] = None
        else:
            result[row_id] = _record_from_json(row_id, value)
    return result


def _write_store(store_path, mapping):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "selections": {
            row_id: (None if record is None else _record_to_json(record))
            for row_id, record in mapping.items()
        },
    }
    tmp_path = str(store_path) + ".tmp"
    try:
        directory = os.path.dirname(os.path.abspath(store_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, store_path)
    except OSError as exc:
        raise GateRefused(
            "could not write selection store at %r: %s: %s"
            % (store_path, type(exc).__name__, exc)
        ) from exc


def _validate_problem(problem, where):
    """A problem must be a string or absent, at WRITE time.

    2026-09-18, found by the chain check (scripts/test_coe_chain.py): the
    reader (_record_from_json) refuses a non-string problem, but neither
    writer checked, so a caller passing the problem MAPPING it already had
    wrote a store that every later load_store() refused. The store could be
    written once and never read again, and each side was self-consistent, so
    no per-unit suite could see it. The rule now lives where the write
    happens, which is the only place that can prevent it."""
    if problem is not None and not isinstance(problem, str):
        raise GateRefused(
            "%s was given a non-string problem (%s); pass the problem's id, "
            "not the problem mapping, or the store this writes can never be "
            "read back" % (where, type(problem).__name__))


def mark_open(row_id, *, store_path, problem=None):
    """Record that row_id's selection has started but not yet closed.

    Not one of the four required exports, but the on-disk counterpart of
    the OPEN state may_open() and load_store() both know how to read: a
    caller (typically the orchestrator, the moment it launches coe_loop for
    a row) writes this once so a later, separate process checking
    may_open() can tell "started, not finished" apart from "never started."
    Refuses (GateRefused) to reopen a row that is already CLOSED: this gate
    does not undo a recorded decision as a side effect of a status write.
    Idempotent against an already-OPEN row (returns without writing again).
    """
    if not isinstance(row_id, str) or not row_id:
        raise GateRefused("row_id is empty or not a string: %r" % (row_id,))
    _validate_problem(problem, "mark_open")
    mapping = load_store(store_path)
    existing = mapping.get(row_id, _ABSENT)
    if isinstance(existing, SelectionRecord):
        raise GateRefused(
            "selection row %r is already closed (%s); mark_open() never "
            "reopens a closed selection" % (row_id, existing.outcome)
        )
    if existing is None:
        return  # already open, nothing to do
    mapping[row_id] = None
    _write_store(store_path, mapping)


_ABSENT = object()  # sentinel: distinguishes "key not in mapping" from None


def status(row_id, *, store_path):
    """The CLOSED SelectionRecord for row_id, or None.

    None covers BOTH missing and open: this is the minimum public query the
    unit's contract names, and it deliberately does not distinguish the two
    (that distinction belongs to may_open(), which is given the full
    mapping so it can tell them apart in its refusal reason). A caller that
    needs to tell missing from open calls load_store() directly and checks
    membership itself, the way may_open() does.
    """
    if not isinstance(row_id, str) or not row_id:
        raise GateRefused("row_id is empty or not a string: %r" % (row_id,))
    mapping = load_store(store_path)
    value = mapping.get(row_id, _ABSENT)
    if value is _ABSENT or value is None:
        return None
    return value


def close_selection(row_id, loop_result, *, store_path, problem=None, now=None):
    """Close row_id's selection with loop_result, and persist it.

    loop_result must carry .outcome in coe_loop.OUTCOMES (an outcome this
    function does not recognise raises GateRefused: rule 5's "map every one
    explicitly, never default either way"), plus .final_score,
    .capping_criterion and .reason, which is exactly coe_loop.LoopResult's
    own shape (duck-typed so a test double with the same four attributes
    works too).

    IDEMPOTENT: closing the same row_id twice with a loop_result carrying
    the same (problem, outcome, final_score, capping_criterion) returns the
    existing record unchanged and writes nothing new (rule 7). Closing the
    same row_id twice with any of those four different raises GateRefused:
    a second, conflicting close is a decision this module refuses to make
    silently by overwriting the first (see the EDGE LIST). `reason` is
    compared loosely (it is prose commentary, not decision-bearing data);
    only the four fields above decide whether two closes agree.
    """
    _validate_problem(problem, "close_selection")
    if not isinstance(row_id, str) or not row_id:
        raise GateRefused("row_id is empty or not a string: %r" % (row_id,))

    outcome = getattr(loop_result, "outcome", None)
    if outcome not in _KNOWN_LOOP_OUTCOMES:
        raise GateRefused(
            "loop_result.outcome %r is not one of coe_loop's known outcomes "
            "%s; refusing to guess whether this selection passed"
            % (outcome, sorted(_KNOWN_LOOP_OUTCOMES))
        )

    proposed = SelectionRecord(
        row_id=row_id,
        problem=problem,
        outcome=outcome,
        final_score=getattr(loop_result, "final_score", None),
        capping_criterion=getattr(loop_result, "capping_criterion", None),
        reason=getattr(loop_result, "reason", ""),
        closed_at=_now_iso(now),
    )

    mapping = load_store(store_path)
    existing = mapping.get(row_id, _ABSENT)
    if isinstance(existing, SelectionRecord):
        identity = (
            existing.problem,
            existing.outcome,
            existing.final_score,
            existing.capping_criterion,
        )
        new_identity = (
            proposed.problem,
            proposed.outcome,
            proposed.final_score,
            proposed.capping_criterion,
        )
        if identity == new_identity:
            return existing  # idempotent: same close, no new record written
        raise GateRefused(
            "selection row %r is already closed as %r; this close disagrees "
            "(%r); a conflicting re-close is never applied silently"
            % (row_id, identity, new_identity)
        )

    mapping[row_id] = proposed
    _write_store(store_path, mapping)
    return proposed


def _problem_mismatch(build_problem, record_problem):
    """True only when BOTH sides name a problem and they differ. Either
    side leaving it unnamed is not treated as a contradiction (there is
    nothing on that side to disagree with), per the EDGE LIST.
    """
    return build_problem is not None and record_problem is not None and build_problem != record_problem


def _valid_ceiling_decision(decision):
    if not isinstance(decision, dict):
        return False
    owner = decision.get("owner")
    reason = decision.get("reason")
    return (
        isinstance(owner, str) and owner.strip() != ""
        and isinstance(reason, str) and reason.strip() != ""
    )


def may_open(build_row, *, selections, now=None):
    """(bool, reason) for whether build_row may open.

    build_row is a mapping carrying at least "row_id" and
    "selection_row_id", optionally "problem" (compared against the closed
    selection's own .problem, see _problem_mismatch) and
    "ceiling_decision" ({"owner": str, "reason": str}, required only when
    the named selection closed as CEILING).

    selections is Dict[selection_row_id, Optional[SelectionRecord]]: absent
    key means MISSING, a present key mapped to None means OPEN, a present
    key mapped to a SelectionRecord means CLOSED. Pass load_store()'s own
    return value here for the real, on-disk gate; a plain dict literal is
    enough for a test, which is the whole reason this function takes data
    rather than a store_path (see the module docstring: main() is where
    real I/O and refusal logging happen; this function stays pure and fast).

    now is accepted for the same reason coe_loop's own functions accept
    injectable collaborators: reserved for a future staleness check (a PASS
    recorded long enough ago needing re-verification) that a caller could
    add without changing this signature.
    # ponytail: `now` is unused today; add an expiry comparison against
    # SelectionRecord.closed_at when a real staleness rule exists.

    Never raises for an ordinary business refusal (missing, open, no
    ceiling decision, wrong problem): those all return False. Raises
    GateRefused only when `selections` itself is untrustworthy input (a
    value that is neither None nor a SelectionRecord), which is a caller
    bug, not a business state this gate is meant to arbitrate.
    """
    if not isinstance(build_row, dict):
        raise GateRefused("build_row is not a mapping: %r" % (build_row,))

    row_id = build_row.get("row_id")
    if not isinstance(row_id, str) or not row_id:
        return False, (
            "build row id is empty or missing: refusing rather than "
            "guessing which build row this decision is for"
        )

    selection_row_id = build_row.get("selection_row_id")
    if not isinstance(selection_row_id, str) or not selection_row_id:
        return False, (
            "build row %r names no selection row at all: a selection row "
            "must close before a build row can open" % (row_id,)
        )

    build_problem = build_row.get("problem")

    value = selections.get(selection_row_id, _ABSENT)

    if value is _ABSENT:
        return False, (
            "selection row %r for build row %r has not been started: it is "
            "MISSING, not merely open, so nobody has run the council on "
            "this problem yet" % (selection_row_id, row_id)
        )

    if value is None:
        return False, (
            "selection row %r for build row %r is OPEN: a council loop has "
            "started and has not yet closed with a verdict" % (selection_row_id, row_id)
        )

    if not isinstance(value, SelectionRecord):
        raise GateRefused(
            "selections[%r] is neither None (open) nor a SelectionRecord "
            "(closed): %r" % (selection_row_id, value)
        )

    if _problem_mismatch(build_problem, value.problem):
        return False, (
            "selection row %r closed for problem %r, but build row %r "
            "names problem %r: refusing rather than opening a build row "
            "against the wrong selection"
            % (selection_row_id, value.problem, row_id, build_problem)
        )

    outcome = value.outcome
    if outcome == coe_loop.OUTCOME_PASS:
        return True, (
            "selection row %r closed PASS (score %r); build row %r may "
            "open" % (selection_row_id, value.final_score, row_id)
        )

    if outcome == coe_loop.OUTCOME_CEILING:
        decision = build_row.get("ceiling_decision")
        if _valid_ceiling_decision(decision):
            return True, (
                "selection row %r closed CEILING at criterion %r; build "
                "row %r may open on the recorded owner decision by %r: %r"
                % (
                    selection_row_id,
                    value.capping_criterion,
                    row_id,
                    decision["owner"],
                    decision["reason"],
                )
            )
        return False, (
            "selection row %r closed CEILING at criterion %r: proceeding "
            "needs a build_row['ceiling_decision'] naming a non-empty "
            "'owner' and 'reason'; none was given (or it was incomplete)"
            % (selection_row_id, value.capping_criterion)
        )

    if outcome == coe_loop.OUTCOME_REJECTED:
        return False, (
            "selection row %r closed REJECTED: green withdrew this "
            "methodology; build row %r may not open against it"
            % (selection_row_id, row_id)
        )

    if outcome == coe_loop.OUTCOME_ABANDONED:
        return False, (
            "selection row %r closed ABANDONED: the loop ran out of rounds "
            "without a verdict, which is never read as a pass; build row "
            "%r may not open" % (selection_row_id, row_id)
        )

    # Unreachable in practice: close_selection() and _record_from_json()
    # both already refuse any outcome outside _KNOWN_LOOP_OUTCOMES before a
    # SelectionRecord can exist at all. Kept anyway (rule 5: unknown input
    # raises, never defaults either way) in case a future SelectionRecord
    # is ever constructed by a path that skips that validation.
    raise GateRefused(
        "selection row %r closed with outcome %r, which this gate does not "
        "recognise: refusing to guess whether that means the build row may "
        "open" % (selection_row_id, outcome)
    )


def _build_loop_result_from_args(args):
    return coe_loop.LoopResult(
        outcome=args.outcome,
        rounds=(),
        final_score=args.final_score,
        capping_criterion=args.capping_criterion,
        exploits_accepted=(),
        reason=args.reason or ("closed via coe_gate.py CLI as %s" % args.outcome),
    )


def _cmd_check(args):
    selections = load_store(args.store)
    build_row: Dict[str, Any] = {
        "row_id": args.row_id,
        "selection_row_id": args.selection_row_id,
        "problem": args.problem,
    }
    if args.ceiling_owner or args.ceiling_reason:
        build_row["ceiling_decision"] = {
            "owner": args.ceiling_owner or "",
            "reason": args.ceiling_reason or "",
        }
    allowed, reason = may_open(build_row, selections=selections)
    if allowed:
        print("OPEN: %s" % reason)
        return 0
    print("REFUSED: %s" % reason, file=sys.stderr)
    _log_refusal(reason, store_path=args.store)
    return 1


def _cmd_close(args):
    loop_result = _build_loop_result_from_args(args)
    record = close_selection(
        args.row_id, loop_result, store_path=args.store, problem=args.problem
    )
    print(json.dumps(_record_to_json(record), indent=2, sort_keys=True))
    return 0


def _cmd_status(args):
    record = status(args.row_id, store_path=args.store)
    if record is None:
        print("NOT-CLOSED (missing or open)")
        return 0
    print(json.dumps(_record_to_json(record), indent=2, sort_keys=True))
    return 0


def _cmd_mark_open(args):
    mark_open(args.row_id, store_path=args.store, problem=args.problem)
    print("OPEN: %s" % args.row_id)
    return 0


def _cmd_count_refusals(args):
    print(count_refusals(args.store))
    return 0


def main(argv):
    """CLI entry point, non-zero exactly when a build row is refused (or
    the gate's own machinery cannot trust its input), so a hook can wire
    its own pass/fail on this process's exit code alone. Every refusal from
    the `check` subcommand is logged via _log_refusal() before this
    returns, per the worker brief's rule 6.
    """
    parser = argparse.ArgumentParser(prog="coe_gate.py")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="may this build row open")
    check.add_argument("--row-id", required=True)
    check.add_argument("--selection-row-id", required=True)
    check.add_argument("--store", required=True)
    check.add_argument("--problem", default=None)
    check.add_argument("--ceiling-owner", default=None)
    check.add_argument("--ceiling-reason", default=None)
    check.set_defaults(func=_cmd_check)

    close = sub.add_parser("close", help="close a selection row")
    close.add_argument("--row-id", required=True)
    close.add_argument("--store", required=True)
    close.add_argument("--outcome", required=True, choices=sorted(_KNOWN_LOOP_OUTCOMES))
    close.add_argument("--problem", default=None)
    close.add_argument("--final-score", type=int, default=None)
    close.add_argument("--capping-criterion", default=None)
    close.add_argument("--reason", default=None)
    close.set_defaults(func=_cmd_close)

    stat = sub.add_parser("status", help="show a selection row's closed record")
    stat.add_argument("--row-id", required=True)
    stat.add_argument("--store", required=True)
    stat.set_defaults(func=_cmd_status)

    mark = sub.add_parser("mark-open", help="record that a selection started")
    mark.add_argument("--row-id", required=True)
    mark.add_argument("--store", required=True)
    mark.add_argument("--problem", default=None)
    mark.set_defaults(func=_cmd_mark_open)

    count = sub.add_parser("count-refusals", help="count logged refusals")
    count.add_argument("--store", required=True)
    count.set_defaults(func=_cmd_count_refusals)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GateRefused as exc:
        print("REFUSED (gate machinery): %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
