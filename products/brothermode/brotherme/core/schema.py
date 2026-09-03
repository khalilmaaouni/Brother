"""The canonical project protocol, in code.

SOURCE OF TRUTH: docs/specs/canonical-project-protocol.md. Every field list,
state name, and enumerated value here restates that document, and
tools/test_bm_schema.py parses the document's own YAML blocks and state list
to assert the two cannot drift apart silently. When they disagree, the
document wins and this file is the one that is wrong.

WHAT LIVES HERE
  1. The five canonical object shapes (Project, Forecast, Task,
     AttributionEvent, Alert) with validate(), to_dict() and from_dict().
  2. The ten task lifecycle states, LEGAL_TRANSITIONS, and transition(),
     which refuses an illegal move by naming the from-state, the to-state,
     and the legal moves. `done` is not a state and is refused by name,
     because the document says so in as many words.

Storage lives in the store now, not here: tools/bm_store.py schema 12 holds
the five shapes as tables and calls into this module for validation and
legality before every write. This module used to also own an append-only
JSONL event stream (append_event, read_events); that stream is retired
(Loop 1 delete-list D3, replayed into the store with nothing left to
replay), so this module is shapes and legality only.

Python 3.9, standard library only. No network. No subprocess. No files.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import hashlib
import json


class SchemaError(ValueError):
    """A protocol object or stream violated the canonical protocol."""


# -- lifecycle states --------------------------------------------------------
#
# Section 1 of the spec, in the spec's order. The order is load-bearing:
# "state changes move forward through this list", so the tuple index IS the
# direction of travel.

STATES = (
    "planned",
    "ready",
    "active",
    "blocked",
    "awaiting review",
    "verified",
    "accepted",
    "delivered",
    "monitored",
    "closed",
)

# The word the spec bans outright. Refused by name, with the spec's reason,
# so the refusal teaches rather than merely blocks.
FORBIDDEN_STATE = "done"

# Forward moves. `blocked` is a branch off `active`, not a stage every task
# passes through: the spec lists it among the ten states but the forward
# chain a healthy task walks is planned, ready, active, awaiting review,
# verified, accepted, delivered, monitored, closed. Leaving `blocked`
# (blocked to active) is listed here even though it points backward in the
# tuple, because unblocking is the normal exit and not a review regression.
LEGAL_TRANSITIONS = {
    "planned": ("ready",),
    "ready": ("active",),
    "active": ("blocked", "awaiting review"),
    "blocked": ("active",),
    "awaiting review": ("verified",),
    "verified": ("accepted",),
    "accepted": ("delivered",),
    "delivered": ("monitored",),
    "monitored": ("closed",),
    "closed": (),
}


def _state_error(name):
    if isinstance(name, str) and name.strip().lower() == FORBIDDEN_STATE:
        return (
            "state %r is refused by name: the canonical protocol says Done "
            "is not a valid state. No object in this protocol may carry a "
            "state with that name. Use the precise stage instead, one of: %s."
            % (name, ", ".join(STATES)))
    return ("unknown state %r. A task is always in exactly one of: %s."
            % (name, ", ".join(STATES)))


def transition(task, new_state, reason=None):
    """Move `task` to `new_state`, or refuse with the reason spelled out.

    Forward moves follow LEGAL_TRANSITIONS. A backward move (any state
    earlier in STATES than the current one) is normal per the spec, but must
    be recorded with a reason, never silent: without a non-empty `reason`
    string it is refused. Recording the move itself is the caller's job,
    via an AttributionEvent on the stream; this helper only enforces that
    the reason exists at the moment of the move.

    Returns the task with its status updated. Raises SchemaError on any
    refusal, naming the from-state, the to-state, and the legal moves.
    """
    if not isinstance(task, Task):
        raise SchemaError(
            "transition() wants a Task, got %s" % type(task).__name__)
    current = task.status
    if current not in STATES:
        raise SchemaError(
            "task %r holds an illegal current state: %s"
            % (task.task_id, _state_error(current)))
    if new_state not in STATES:
        raise SchemaError(
            "cannot move task %r from %r: %s"
            % (task.task_id, current, _state_error(new_state)))
    if new_state == current:
        raise SchemaError(
            "task %r is already %r. Legal moves from %r: %s."
            % (task.task_id, current, current,
               ", ".join(LEGAL_TRANSITIONS[current]) or "(none, terminal)"))
    if new_state in LEGAL_TRANSITIONS[current]:
        task.status = new_state
        return task
    if current == "closed":
        raise SchemaError(
            "task %r is closed: the monitoring window is over and the task "
            "is finished for good (the protocol's own words). No move out "
            "of closed is legal, forward or backward, with or without a "
            "reason; work that resurfaces becomes a new task that names "
            "this one." % (task.task_id,))
    if STATES.index(new_state) < STATES.index(current):
        if not (isinstance(reason, str) and reason.strip()):
            raise SchemaError(
                "moving task %r backward from %r to %r is allowed but never "
                "silent: the protocol requires a recorded reason. Pass "
                "reason= with a real sentence. Forward moves from %r: %s."
                % (task.task_id, current, new_state, current,
                   ", ".join(LEGAL_TRANSITIONS[current]) or "(none, terminal)"))
        task.status = new_state
        return task
    raise SchemaError(
        "illegal transition for task %r: %r to %r. Legal moves from %r: %s. "
        "States advance through the lifecycle in order; skipping a stage "
        "would hide which evidence and review requirements were met."
        % (task.task_id, current, new_state, current,
           ", ".join(LEGAL_TRANSITIONS[current]) or "(none, terminal)"))


# -- the five shapes ---------------------------------------------------------

class _Shape(object):
    """Shared machinery for the five canonical objects.

    FIELDS is the normative list from the spec, in the spec's order.
    REQUIRED names the fields validate() insists on: identity, references,
    enumerated values, and timestamps of record. Everything else may be
    None (the YAML shapes declare names, not requiredness, so the required
    set is the minimum the object is meaningless without).
    """

    FIELDS = ()
    REQUIRED = ()
    ENUMS = {}
    LIST_FIELDS = ()
    BOOL_FIELDS = ()

    def __init__(self, **fields):
        unknown = sorted(set(fields) - set(self.FIELDS))
        if unknown:
            raise SchemaError(
                "%s does not carry field(s): %s. The canonical shape carries "
                "exactly: %s."
                % (type(self).__name__, ", ".join(unknown),
                   ", ".join(self.FIELDS)))
        for name in self.FIELDS:
            setattr(self, name, fields.get(name))

    def validate(self):
        """Return self, or raise one SchemaError naming EVERY missing or
        ill-typed required field, so a caller fixes the object in one round
        trip instead of one complaint at a time."""
        problems = []
        for name in self.REQUIRED:
            value = getattr(self, name)
            if name in self.BOOL_FIELDS:
                if not isinstance(value, bool):
                    problems.append(
                        "%s must be a bool, got %s"
                        % (name, type(value).__name__))
                continue
            if not isinstance(value, str) or not value.strip():
                problems.append(
                    "%s is required and must be a non-empty string, got %r"
                    % (name, value))
                continue
            if name in self.ENUMS and value not in self.ENUMS[name]:
                problems.append(
                    "%s must be one of %s, got %r"
                    % (name, " | ".join(self.ENUMS[name]), value))
        for name in self.LIST_FIELDS:
            value = getattr(self, name)
            if value is not None and not isinstance(value, list):
                problems.append(
                    "%s must be a list when set, got %s"
                    % (name, type(value).__name__))
        if problems:
            raise SchemaError(
                "invalid %s: %s" % (type(self).__name__, "; ".join(problems)))
        return self

    def to_dict(self):
        return {name: getattr(self, name) for name in self.FIELDS}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise SchemaError(
                "%s.from_dict wants a dict, got %s"
                % (cls.__name__, type(data).__name__))
        missing = [n for n in cls.FIELDS if n not in data]
        unknown = sorted(set(data) - set(cls.FIELDS))
        if missing or unknown:
            parts = []
            if missing:
                parts.append("missing field(s): %s" % ", ".join(missing))
            if unknown:
                parts.append("unknown field(s): %s" % ", ".join(unknown))
            raise SchemaError(
                "%s.from_dict refused: %s. The canonical shape carries "
                "exactly: %s."
                % (cls.__name__, "; ".join(parts), ", ".join(cls.FIELDS)))
        return cls(**data)

    def __eq__(self, other):
        return type(other) is type(self) and other.to_dict() == self.to_dict()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "%s(%s)" % (
            type(self).__name__,
            ", ".join("%s=%r" % (n, getattr(self, n)) for n in self.FIELDS))


class Project(_Shape):
    """Spec section 2.1. One per project: durable identity and direction."""

    FIELDS = (
        "project_id", "name", "goal", "user_outcome", "project_type",
        "primary_persona", "experience_level", "status", "phase",
        "scope_in", "scope_out", "success_criteria", "assumptions",
        "unknowns", "risks", "kill_criteria", "non_goals",
        "created_at", "updated_at",
    )
    REQUIRED = ("project_id", "name", "created_at", "updated_at")
    # kill_criteria and non_goals joined this shape at schema 19 (R1.1,
    # 2026-08-12). They are here rather than handled beside it on purpose.
    # The store's own contract is that this file holds THE shape, and the
    # first implementation carried them around it: popped out of the dict
    # before Project(**d) so the shape would not refuse them as unknown
    # kwargs, then re-added to LIST_FIELDS locally on the read path. That
    # worked and was tested, and it was still the wrong place, because a
    # field the canonical shape does not know about is a field every future
    # reader has to be told about separately. Adding them here is the whole
    # fix; the local compensations in bm_store.py came out in the same
    # change.
    LIST_FIELDS = ("scope_in", "scope_out", "success_criteria",
                   "assumptions", "unknowns", "risks",
                   "kill_criteria", "non_goals")


class Forecast(_Shape):
    """Spec section 2.2. Ranges with a stated confidence, never one number.
    Append a new Forecast at each reforecast; never edit an old one."""

    FIELDS = (
        "forecast_id", "project_id", "minimum_duration", "likely_duration",
        "maximum_duration", "input_token_range", "output_token_range",
        "effective_total_token_range", "confidence", "assumptions",
        "unknowns", "calculation_basis", "next_reforecast_event",
        "created_at",
    )
    REQUIRED = ("forecast_id", "project_id", "confidence", "created_at")
    ENUMS = {"confidence": ("low", "medium", "high")}
    LIST_FIELDS = ("assumptions", "unknowns")


class Task(_Shape):
    """Spec section 2.3. One unit of work. `status` holds exactly one of the
    ten lifecycle states; read_scope and write_scope are the protocol form
    of the one-writer-per-file rule."""

    FIELDS = (
        "task_id", "project_id", "title", "user_value", "reason", "status",
        "priority", "depends_on", "assigned_human", "assigned_runtime",
        "assigned_model_profile", "assignment_reason", "reviewer_runtime",
        "reviewer_model_profile", "read_scope", "write_scope",
        "expected_outputs", "acceptance_checks", "time_forecast",
        "token_forecast", "confidence", "actual_time", "actual_tokens",
        "evidence", "blockers", "started_at", "completed_at",
        # Schema 18 (Phase 5, the progress view): the phase this piece of
        # work belongs to, recorded rather than inferred from the title.
        # Optional, and absent means absent: see _TASKS_V18_COLUMN.
        "phase",
    )
    REQUIRED = ("task_id", "project_id", "title", "status")
    ENUMS = {"status": STATES}
    LIST_FIELDS = ("depends_on", "read_scope", "write_scope",
                   "expected_outputs", "acceptance_checks", "evidence",
                   "blockers")

    def validate(self):
        # The base check reports an out-of-enum status generically; the spec
        # calls `done` out by name, so the refusal does too.
        if isinstance(self.status, str) and (
                self.status.strip().lower() == FORBIDDEN_STATE):
            raise SchemaError(
                "invalid Task: %s" % _state_error(self.status))
        return _Shape.validate(self)


def criterion_id_for_check(check):
    """A stable id for ONE entry of a Task's acceptance_checks list (T5,
    criterion-linked verification, folded in with capability receipts
    because a link to the acceptance criterion a check satisfies is the
    receipt's own backbone).

    acceptance_checks stays exactly the flat list schema 12 already gave
    it: FIELDS unchanged, LIST_FIELDS unchanged, ADDITIVE ONLY, no
    reshape, so an existing task's checks keep working completely
    unchanged, plain strings and all. The id is derived from the entry's
    own content rather than stored inside it or beside it, which is what
    makes it stable without a new field on Task: the same check text
    always yields the same id, on the first read as much as the
    thousandth, with nothing new ever written to the task row itself.

    `check` is ordinarily a plain string, the only shape acceptance_checks
    entries have ever taken; a caller that stores a richer object per
    entry still gets a stable id because the value is canonicalised
    (JSON, sorted keys) before hashing. Twelve hex characters (48 bits) is
    short enough to read and type back, and long enough that two distinct
    checks on the same task colliding by accident is not a real risk."""
    text = check if isinstance(check, str) else json.dumps(check, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class AttributionEvent(_Shape):
    """Spec section 2.4. One recorded action: who or what did something,
    why, and what it produced. Lives on the append-only stream."""

    FIELDS = (
        "event_id", "project_id", "task_id", "event_type", "actor_type",
        "actor_name", "runtime", "model", "session_id", "action", "reason",
        "input_artifacts", "output_artifacts", "evidence_ref", "timestamp",
    )
    REQUIRED = ("event_id", "project_id", "event_type", "actor_type",
                "actor_name", "action", "timestamp")
    ENUMS = {"actor_type": ("human", "model", "hook", "automation")}
    LIST_FIELDS = ("input_artifacts", "output_artifacts")


class Alert(_Shape):
    """Spec section 2.5. One thing the user should know about.
    requires_human is the line between FYI and work-waiting-on-you."""

    FIELDS = (
        "alert_id", "severity", "category", "message", "why_it_matters",
        "recommended_action", "requires_human", "created_at", "resolved_at",
    )
    REQUIRED = ("alert_id", "severity", "message", "requires_human",
                "created_at")
    ENUMS = {"severity": ("info", "attention", "high", "critical")}
    BOOL_FIELDS = ("requires_human",)


SHAPES = (Project, Forecast, Task, AttributionEvent, Alert)
