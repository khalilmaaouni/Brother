#!/usr/bin/env python3
"""intervention_events: a place to record when a person had to step in.

WHY THIS EXISTS. benchmarks/SAFE-UNWATCHED-TIME.md says plainly what is
missing: "No event kind in this estate records an operator input scoped
to a run", so safe_unwatched_time.py treats every recorded run as fully
unattended and calls its own numbers an upper bound. The same gap is
named again in benchmarks/gauntlets/long-horizon-recovery.json, which
reports HUMAN INTERVENTIONS as NO INSTRUMENT YET and says what one would
require: "A counter of operator inputs scoped to one run, written into
the journal as its own event kind so the count is read off the record
rather than remembered." This module is that event kind. It is measured
new: scripts/run_journal_chain.py and scripts/side_effect_ledger.py cover
tamper evidence and external side effects, neither records that a human
stepped in and why, and journal.py itself is a generic carrier with no
opinion on what a human-intervention payload must contain.

WHAT IT DOES NOT DO. It does not add a new store. Every event still lands
in the one journal journal.py already owns
(<run_dir>/journal.jsonl), through journal.append(), under the type
"human.intervention". This module is a validating front door onto that
existing log, not a second log beside it. Wiring safe_unwatched_time.py
to treat this event kind as a fifth break condition is a separate change;
this file only makes the event kind exist and record honestly.

THE DECIDING PROPERTY. Every human intervention is recorded with its
reason and whether the work could have continued without it. An
intervention with no reason, or with a category outside the fixed set
below, is refused by raising ValueError rather than recorded under a
default: a silently-invented reason or a silently-guessed category would
be exactly the fabrication this estate's own rules forbid. The record
itself is append-only because journal.append() already is: an atomic
O_APPEND write of one whole line, so no event already written is ever
rewritten or removed by a later call here.

THE SIX CATEGORIES, closed, not open to a caller's own string. Each one
names a reason a run cannot supply what it needs from itself:

  credential            a person had to supply or unlock a secret,
                        password, key or token the automation does not
                        and should not hold itself.
  authorization         a person had to make a call policy reserves for
                        a human: an irreversible action, a purchase,
                        publishing something publicly, raising a spending
                        limit, deleting data, or changing a standing rule.
  clarification         the run hit a genuine ambiguity in its own
                        instructions it could not resolve alone, and a
                        person answered a question before it could go on.
  correction            a person fixed a mistake the run made, or was
                        about to make, before it went further.
  manual_continuation   a person had to re-issue, resume or re-run the
                        work after it stopped, for any reason. Matches
                        the intervention definition already used by hand
                        in docs/plan/HEAD-TO-HEAD-PROTOCOL-2026-08-30.md:
                        "every keystroke after the outcome sentence".
  external_action       a step needed a human hand for something the run
                        cannot perform itself: a physical action, a click
                        a policy requires a human to make, or a browser
                        action gated to human control.

An unrecognised category is exactly the "unknown input" this estate's
rules say must raise, never fall back to a permissive default.

Python 3, standard library only. No network.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import journal  # noqa: E402

#: The event type this module is the sole producer of on journal.jsonl.
EVENT_TYPE = "human.intervention"

#: Closed set. See the module docstring above for what each one means and
#: why it is a category a run cannot resolve from inside itself.
INTERVENTION_CATEGORIES = frozenset((
    "credential",
    "authorization",
    "clarification",
    "correction",
    "manual_continuation",
    "external_action",
))

#: The three fields every intervention payload must carry. Named once so
#: record_intervention's collision check and a test's pinned expected set
#: both read from the same list rather than restating it.
REQUIRED_FIELDS = ("category", "reason", "could_continue_without_it")


def record_intervention(run_dir, category, reason, could_continue_without_it,
                        **extra_fields):
    """Append one human.intervention event to run_dir's journal. Returns
    the event id journal.append() gives back (or None if that underlying
    write itself failed; see journal.py's own availability rule for why
    that failure is never raised).

    Every check below runs and can raise BEFORE journal.append() is ever
    called, so a refused call never appends a partial or invented record:

      category must be one of INTERVENTION_CATEGORIES exactly (no case
        folding, no stripping, no partial match). Anything else raises
        ValueError naming the bad category, never falls back to a default.
      reason must be a non-empty string once stripped of surrounding
        whitespace. Missing, empty or whitespace-only raises ValueError:
        this module never invents a placeholder reason.
      could_continue_without_it must be an actual bool. A truthy stand-in
        (1, 0, "yes", None) raises ValueError; type(x) is not bool catches
        these because bool is a subclass of int in Python and `is not
        bool` is the only check that will not silently accept 1 or 0.
      extra_fields may carry any additional JSON-serializable detail (for
        example which unit was running). They can never collide with
        category, reason or could_continue_without_it: those three are
        named parameters, not part of **extra_fields, so Python itself
        raises TypeError before this body ever runs if a caller passes
        one of those three names twice (once positionally or by name,
        once again as a stray keyword). Nothing here has to re-check a
        collision the call signature already makes impossible.
    """
    if category not in INTERVENTION_CATEGORIES:
        raise ValueError("unknown intervention category: %r" % (category,))
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("intervention reason must be a non-empty string")
    if type(could_continue_without_it) is not bool:
        raise ValueError(
            "could_continue_without_it must be a bool, got %r"
            % (could_continue_without_it,))
    payload = dict(zip(REQUIRED_FIELDS,
                       (category, reason.strip(), could_continue_without_it)))
    payload.update(extra_fields)
    return journal.append(run_dir, EVENT_TYPE, payload=payload)


def read_interventions(run_dir):
    """Every human.intervention event this run's journal holds, oldest
    first, as full event dicts (event_id, at, payload and the rest of
    journal.append()'s own shape, not just the payload).

    None when the run has no journal at all (journal.read()'s own
    NO-DATA), distinct from an empty list, which means the journal exists
    but no intervention was ever recorded in it: this module does not
    fold "never measured" into "measured zero", the same distinction
    journal.read() itself already draws."""
    events = journal.read(run_dir)
    if events is None:
        return None
    return [event for event in events
            if isinstance(event, dict) and event.get("type") == EVENT_TYPE]
