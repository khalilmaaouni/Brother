#!/usr/bin/env python3
"""ORCH-13 of the 1.0.20 orchestration control plane: the hard-stop drain.

WHY THIS EXISTS. A run has a hard stop, a wall-clock instant past which
nothing may keep running unattended. The wrong shape for that rule is
"kill everything at the clock": a unit mid-verification loses its result,
a unit that already finished green never lands, and a unit that was merely
running when the clock struck is left exactly where night_supervisor.py's
own docstring names the failure this module exists to remove: readable as
RUNNING forever, with nobody able to say whether it half-happened. That
ambiguity is worse than a failure, because a failure is at least legible in
the morning (see docs/plan/ORCH-1020-WORKER-CONTRACT.md, INVARIANTS "no
transcript-only state").

So there are three phases, not two. OPEN is business as usual. DRAINING is
the window before the stop: stop admitting new long-running work, keep
letting bounded in-flight verification finish, keep letting already-green
work integrate, refuse a fresh risky repair even though it might be short,
and start recording exact parked state for anything that will not make it.
STOPPED is at and after the hard stop: nothing new is admitted, full stop.
assert_no_ambiguous_state() is the property the whole unit exists to prove:
once a run reaches STOPPED, every unit it named must read DONE, PARKED,
EXHAUSTED or AWAITING-HUMAN, the four states orchestrator_invariants.py
already calls terminal. Nothing else is acceptable, because nothing else
can be resumed OR abandoned with confidence.

THE BAD STATE A GREEN RUN WOULD ALSO PASS: a drain() that parks every unit
the instant DRAINING opens, never calling integrate() at all. That drain
would satisfy assert_no_ambiguous_state() on every run, forever, while
throwing away every piece of in-flight work a real drain exists to save.
test_orchestrator_hard_stop.py's test_drain_lands_green_work_not_just_parks_it
is the test that would catch it: it asserts integrate() is actually called
for ready units and that PARKED work is exactly the unfinished work, never
the whole set.

WHAT SEPARATES A LONG UNIT FROM A SHORT ONE, and why this is two rules, not
one. Duration and risk are different axes: a repair can be short in wall
clock time and still risky (it changes state on a base that is about to be
frozen), and a unit can be long and still perfectly safe to keep running if
it is a bounded, already-started verification. So:
  - COST decides ordinary admission during DRAINING: `estimated_cost` must
    be the literal string "SHORT" to be admitted; every other value,
    including "LONG", an unrecognised string, or nothing declared at all,
    is treated as LONG and refused. This is deliberate, not a "no
    permissive default" violation of the general rule in
    ORCH-1020-WORKER-CONTRACT.md: that rule guards against an unknown value
    being read as safe-to-proceed. Here the unknown value is read as
    unsafe-to-proceed (refused), which is the direction this whole module
    is built to fail toward, and the WBS row for this unit names this exact
    behaviour by name ("an unknown or undeclared cost is treated as LONG
    and refused"). `task_class`, by contrast, IS validated against
    orchestrator_invariants.TASK_CLASSES and raises on anything outside it,
    because there the unknown value has no safe reading at all.
  - RISK is a second, independent veto: any unit whose task_class is
    "repair" is refused during DRAINING regardless of its declared cost.
    A drain window exists to let a run land cleanly on what it already has,
    not to start a fresh attempt to change what "already has" means.

CONTINGENCY, how this module fails and which direction.
  On any doubt, a unit is PARKED rather than left running: a needless park
  costs one resume; an ambiguous RUNNING costs a morning of forensics
  reconstructing what a unit was doing from logs nobody trusts. So
  admits() defaults refusals wherever cost or risk is unclear, and drain()
  parks anything it cannot prove is both terminal and safely landed.
  If the injected `park()` callable itself raises, drain() lets that
  exception propagate unchanged rather than swallowing it and reporting a
  park that never actually landed: a swallowed park failure is exactly the
  ambiguity this unit exists to remove, now hidden one layer deeper where
  nobody would think to look for it. If the injected `integrate()` callable
  raises for a unit that declared itself ready, that is a normal domain
  outcome (the unit turned out not to be safe to land after all), and
  drain() catches it and parks the unit instead, with the failure recorded
  as its last_failure.
  assert_no_ambiguous_state() raises HardStopError naming every offending
  unit id and its actual state (or, for a state this module does not even
  recognise, that fact), so an operator sees the whole list in one message
  rather than fixing one and re-running to find the next.
  OPERATOR RECOVERY: a HardStopError from assert_no_ambiguous_state names
  every unit still non-terminal; resolve each by hand (resume it fresh on
  the next run, or mark it AWAITING-HUMAN) before the run is considered
  closed. A HardStopError from phase() or drain() names the misconfiguration
  directly (drain_start after hard_stop, or drain() called while phase is
  still OPEN) and is fixed by correcting the caller, not by retrying.
  WHO IS TOLD: this module writes nothing on its own (no log, no journal,
  no file); a caller wires its return values into whatever this run's own
  closeout and evidence trail already use. It reports, it never announces.

WHAT THIS DOES NOT DO. It does not read the wall clock (every `now` is
injected) and it does not decide what actually counts as "ready to
integrate" for a live brother_run.py unit: that translation is ORCH-10's
wiring job, named in the WBS row for this unit ("ORCH-10 is the sole owner
of the wiring into brother_run, and this unit supplies the interface it
calls"). It does not duplicate night_supervisor.py's near-hard-stop
handling of PROCESS liveness (whether to restart a dead orchestrator, or
ask a live one to stop): that module's near_hard_stop boolean answers "is a
process near enough to the stop that it should not be restarted or should
be asked to wind down", one gate, at one instant. This module answers a
different, three-valued question over the WORK ITEMS a process is running
("what may still be admitted, and what happens to what is left"), which
night_supervisor.py does not attempt (its own docstring lists "choose a
decomposition" and "mark a unit DONE" among the judgements it must never
make). The two share a boundary in wall-clock time, not an implementation.

Imports orchestrator_invariants for TASK_STATES, TERMINAL_STATES and
is_terminal(): the terminal set is defined there once, and this module
never restates it, per ORCH-1020-WORKER-CONTRACT.md.

Python 3.9 floor, standard library only, no network, no clock reads: every
`now` is a parameter, and the test suite drives all of it with a fake
clock, no sleeps.
"""

import orchestrator_invariants as invariants

PHASES = ("OPEN", "DRAINING", "STOPPED")

# The one recognised "this is definitely short" token. Anything else,
# including the string "LONG", is refused during DRAINING; see the module
# docstring section on cost versus risk for why this is not the same kind
# of unknown-value handling the rest of this estate raises on.
_SHORT_COST = "SHORT"

# task_class values this module treats as inherently risky to START during
# a drain, regardless of declared cost. Deliberately narrow: the WBS row
# for this unit names exactly one case ("a fresh risky repair is NOT
# started, even though a repair is short"). A future risky task_class is
# added here explicitly, never inferred.
_RISKY_TASK_CLASSES = frozenset(("repair",))


class HardStopError(Exception):
    """A misconfigured hard-stop schedule, a misuse of drain() outside the
    window it exists for, or (from assert_no_ambiguous_state) a unit still
    non-terminal after the run's hard stop. The message always names the
    exact offending value(s), never just "invalid state"."""


class DrainResult:
    """The outcome of one drain() call. `integrated` and `parked` are lists
    of unit ids; `already_terminal` is the list of ids that were terminal
    before drain() touched anything; `units` maps every id seen to its
    final state after this call, which is exactly the mapping
    assert_no_ambiguous_state() expects to check next."""

    def __init__(self):
        self.integrated = []
        self.parked = []
        self.already_terminal = []
        self.units = {}
        self.park_records = {}

    def __repr__(self):
        return ("DrainResult(integrated=%r, parked=%r, already_terminal=%r)"
                % (self.integrated, self.parked, self.already_terminal))


def phase(now, *, drain_start, hard_stop):
    """OPEN, DRAINING or STOPPED for `now` against this run's boundaries.

    Raises HardStopError if drain_start is after hard_stop: that is a
    misconfiguration (someone swapped the two, or computed one wrong), and
    silently reordering them would hide it behind a schedule that runs
    backwards.

    Boundaries, each one deliberate and each tested at the instant and one
    tick either side:
      - now < drain_start: OPEN. Ordinary admission, no drain rules yet.
      - drain_start <= now < hard_stop: DRAINING. Inclusive at drain_start:
        the moment the window opens, drain rules already apply; there is no
        OPEN tick left at that exact instant.
      - now >= hard_stop: STOPPED. Inclusive at hard_stop: rule 4 says
        nothing is admitted AT the stop, not only strictly after it.
    drain_start == hard_stop is a valid, deliberate zero-width drain window:
    OPEN right up to that instant, then STOPPED, with no `now` ever reading
    DRAINING. That is what "no drain window configured" should mean, not an
    error.
    """
    if drain_start > hard_stop:
        raise HardStopError(
            "drain_start %r is after hard_stop %r: refusing rather than "
            "silently reordering a backwards schedule" % (drain_start, hard_stop))
    if now < drain_start:
        return "OPEN"
    if now < hard_stop:
        return "DRAINING"
    return "STOPPED"


def _check_phase(value):
    if value not in PHASES:
        raise ValueError("unknown phase: %r" % (value,))


def _check_task_class(unit):
    if "task_class" not in unit:
        raise ValueError("unit %r has no task_class" % (unit.get("id"),))
    task_class = unit["task_class"]
    if task_class not in invariants.TASK_CLASSES:
        raise ValueError("unknown task_class: %r" % (task_class,))
    return task_class


def admits(unit, phase, *, estimated_cost=None):
    """(bool, reason) for whether `unit` may be newly admitted (started)
    right now, given the run's current `phase`.

    `unit` must be a dict carrying a "task_class" from
    orchestrator_invariants.TASK_CLASSES; an unrecognised or missing
    task_class raises ValueError, there is no safe default for it.
    `phase` must be one of the three strings phase() returns; anything
    else raises ValueError.

    OPEN admits everything. STOPPED admits nothing, at all, unconditionally
    (rule 4). DRAINING is the interesting case: a repair is refused outright
    (risk, not duration, per the module docstring), and everything else is
    admitted only when estimated_cost is exactly "SHORT"; any other value,
    including an undeclared one, is refused as LONG.
    """
    _check_phase(phase)
    task_class = _check_task_class(unit)

    if phase == "OPEN":
        return True, "phase OPEN: ordinary admission"
    if phase == "STOPPED":
        return False, "phase STOPPED: at or past the hard stop, nothing new is admitted"

    # phase == "DRAINING"
    if task_class in _RISKY_TASK_CLASSES:
        return False, (
            "task_class %r is risky and refused during DRAINING regardless "
            "of declared cost" % (task_class,))
    if estimated_cost == _SHORT_COST:
        return True, "declared cost SHORT: bounded verification admitted during DRAINING"
    return False, (
        "declared cost %r is not SHORT: treated as LONG and refused during "
        "DRAINING" % (estimated_cost,))


def _park_record(unit, *, now, reason):
    """Exact state for a parked unit, per rule 6: what it was doing, its
    attempt count, its last failure if any, and what the next action would
    be. A record carrying only a label is the ambiguity this unit exists
    to remove, wearing a terminal state as a disguise."""
    task_class = unit.get("task_class")
    if task_class in _RISKY_TASK_CLASSES:
        next_action = ("resume: attempt the repair again on the next run, "
                        "as attempt %d" % (unit.get("attempt_count", 0) + 1))
    else:
        next_action = ("resume: re-evaluate this unit from state %r before "
                        "re-dispatching it on the next run" % (unit["state"],))
    return {
        "unit_id": unit.get("id"),
        "state_at_park": unit["state"],
        "task_class": task_class,
        "attempt_count": unit.get("attempt_count", 0),
        "last_failure": unit.get("last_failure"),
        "next_action": next_action,
        "parked_at": now,
        "reason": reason,
    }


def drain(units, *, phase, integrate, park, now):
    """Walk `units` once: land what is already green, park what is not.

    `phase` must be "DRAINING" or "STOPPED"; drain() called while phase is
    still "OPEN" raises HardStopError, because there is nothing to drain
    yet and admits() should be handling ordinary admission instead.

    Each item of `units` is a dict with at least "id" and "state" (a
    member of orchestrator_invariants.TASK_STATES; an unrecognised state
    raises ValueError from is_terminal(), not caught here, because a state
    this estate does not even recognise is a defect in the caller, not a
    parking decision this module can safely make). A unit already terminal
    is left exactly as it was, recorded in `already_terminal`, and neither
    `integrate` nor `park` is called for it.

    A non-terminal unit with `ready` true is handed to `integrate(unit)`.
    Success (no exception) lands it: its final state becomes whatever
    string `integrate` returns if that string is a member of TASK_STATES,
    or "DONE" if `integrate` returns None. A raised exception means this
    unit turned out not to be safe to land after all; it is parked instead,
    with the exception recorded as its last_failure, never left ambiguous.

    A non-terminal unit with `ready` false (or absent: the safe default is
    "not proven ready", never "assume ready") is parked unconditionally;
    that is what DRAINING and STOPPED both exist to do with unfinished
    work, which is why phase="OPEN" is refused above rather than silently
    parking a run that has not started draining yet.

    If the injected `park(unit, record)` itself raises, that exception
    propagates out of drain() unchanged: see the module docstring's
    CONTINGENCY section for why this is never swallowed.
    """
    _check_phase(phase)
    if phase == "OPEN":
        raise HardStopError(
            "drain() called while phase is OPEN: nothing to drain yet, "
            "use admits() for ordinary admission instead")

    result = DrainResult()
    for unit in units:
        unit_id = unit["id"]
        state = unit["state"]

        if invariants.is_terminal(state):
            result.already_terminal.append(unit_id)
            result.units[unit_id] = state
            continue

        if unit.get("ready"):
            try:
                landed_state = integrate(unit)
            except Exception as exc:  # domain outcome: not safe to land after all
                record = _park_record(
                    unit, now=now,
                    reason="integration failed during drain: %r" % (exc,))
                record["last_failure"] = repr(exc)
                park(unit, record)
                result.parked.append(unit_id)
                result.park_records[unit_id] = record
                result.units[unit_id] = state
                continue
            if landed_state is None:
                landed_state = "DONE"
            if landed_state not in invariants.TASK_STATES:
                raise ValueError(
                    "integrate() for unit %r returned unknown state %r"
                    % (unit_id, landed_state))
            result.integrated.append(unit_id)
            result.units[unit_id] = landed_state
            continue

        record = _park_record(
            unit, now=now,
            reason="not ready to integrate at hard-stop drain (phase %s)" % phase)
        park(unit, record)
        result.parked.append(unit_id)
        result.park_records[unit_id] = record
        result.units[unit_id] = "PARKED"

    return result


def assert_no_ambiguous_state(units):
    """Raise HardStopError naming every unit still non-terminal (or whose
    state this module does not even recognise), once. Never raises for a
    clean run: called with every unit reading DONE, PARKED, EXHAUSTED or
    AWAITING-HUMAN (or CANCELLED), it returns None silently.

    Accepts either an iterable of {"id": ..., "state": ...} dicts, or a
    mapping of id to state string (what DrainResult.units already is),
    so a caller can hand this either the raw unit list or a drain()
    result's own `.units` mapping without reshaping it first.
    """
    if isinstance(units, dict):
        items = list(units.items())
    else:
        items = [(unit["id"], unit["state"]) for unit in units]

    offenders = []
    for unit_id, state in items:
        try:
            terminal = invariants.is_terminal(state)
        except ValueError:
            offenders.append("%r (unrecognised state %r)" % (unit_id, state))
            continue
        if not terminal:
            offenders.append("%r (state %r)" % (unit_id, state))

    if offenders:
        raise HardStopError(
            "unit(s) left non-terminal after the hard stop: %s"
            % ", ".join(offenders))
