#!/usr/bin/env python3
"""WBS-40.09 Survivorship lineage: produce the per-field winner decision
scripts/reversibility_gate.py already consumes (a
{field: {"value": ..., "winner_source_id": ...}} decision it explicitly
does NOT compute itself, "per the roadmap's explicit warning not to
conflate entity matching with attribute survivorship"), plus a full,
auditable lineage record explaining why.

Generic Core capability: usable for any entity type and any set of source
systems, never entity-specific and never a named client's own data. This is
the survivorship_policy_ref target docs/schema/golden-master-contract-v1.json
names on the parent Golden Master contract.

Real design input checked in the main lane before this was built (per
docs/plan/1.0.17/WAVE-2-DESIGN-CRITIQUES-2026-09-13.md's "Survivorship
lineage (WBS-40.09)" section, two independent generic AI passes, secondary
input not a verdict):

  KEPT from the Deepseek draft: one decision per golden field per merge,
  naming chosen value, chosen source, losing source(s)/value(s), rule
  name/version, and a decision timestamp; append-only, never overwrite
  lineage when the golden record changes.

  KEPT from the Muse hostile review, the strongest failure mode named --
  "decoupled instrumentation": lineage emitted by a separate
  wrapper/logger that reads rule config independently of the engine, so a
  hotfix to the comparison logic can leave the logger still claiming the
  old rule. The fix here is structural, not procedural: resolve_field()
  below reads rule_name/rule_version directly off the same SurvivorshipRule
  object whose .compare() method just ran the comparison -- there is no
  second, independently-maintained rule-identity string anywhere in this
  module (see test_survivorship_lineage.py's
  test_resolve_field_reads_rule_identity_from_the_rule_parameter, which
  inspects resolve_field's own source to prove it). Each rule also carries
  a logic_fingerprint (a hash of its compare callable's own source text),
  so a hotfix that changes what a rule actually does but forgets to bump
  its declared version is still visible in the lineage record -- the
  fingerprint changes even when the version string does not. Whether to
  fail a build on a fingerprint drift (the design doc's "replay
  verification job" idea) is a separate, out-of-scope decision for a CI or
  batch job to make from this field; this module only makes the drift
  observable.

  CHANGED / NOT BUILT: no database schema, no event sourcing, no
  comparison-matrix/DQ-score/approval-UI/ontology-graph fields the design
  doc calls "commonly over-engineered." Entity matching (which records are
  the same real-world entity) is a distinct decision this module never
  touches -- it only resolves, per field, which already-matched source's
  value survives into the golden record.
"""
import dataclasses
import datetime
import hashlib
import inspect
from typing import Any, Callable, Dict, List, Optional


@dataclasses.dataclass
class Comparison:
    """What one rule's compare() call decided, for one field, over one set
    of source candidates."""

    winner_source_id: str
    winner_value: Any
    reason: str
    sources_compared: List[Dict[str, Any]]


@dataclasses.dataclass
class SurvivorshipRule:
    """A named, versioned comparison rule. `compare` is the real callable
    that makes the decision; name/version/logic_fingerprint are read
    straight off this same object by resolve_field(), never copied into a
    second place that could drift out of sync with what compare() actually
    does."""

    name: str
    version: str
    compare: Callable[[Dict[str, Dict[str, Any]]], Comparison]

    @property
    def logic_fingerprint(self) -> str:
        """sha256 of the compare callable's own source text. Two rules can
        declare the same name/version and still be caught disagreeing here
        if one was hotfixed without a version bump -- see
        test_survivorship_lineage.py's
        test_changing_rule_logic_without_bumping_version_changes_the_fingerprint."""
        try:
            source = inspect.getsource(self.compare)
        except (OSError, TypeError):
            source = repr(self.compare)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _require_candidates(candidates: Dict[str, Dict[str, Any]]) -> None:
    if not candidates:
        raise ValueError("no candidates to compare")


# ---------------------------------------------------------------------------
# Rule 1: most-recent-source-wins, by a timestamp each source carries.
# candidates: {source_id: {"value": ..., "timestamp": ISO-8601 str}}
# ---------------------------------------------------------------------------
def _most_recent_compare(candidates: Dict[str, Dict[str, Any]]) -> Comparison:
    _require_candidates(candidates)
    ranked = sorted(candidates.items(), key=lambda item: item[1].get("timestamp") or "", reverse=True)
    winner_id, winner_data = ranked[0]
    sources_compared = [
        {
            "source_id": sid,
            "value": data.get("value"),
            "timestamp": data.get("timestamp"),
            "outcome": "chosen" if sid == winner_id else "lost",
        }
        for sid, data in candidates.items()
    ]
    if len(ranked) > 1:
        runner_up_id, runner_up_data = ranked[1]
        reason = "source '%s' dated %s is more recent than source '%s' dated %s" % (
            winner_id,
            winner_data.get("timestamp"),
            runner_up_id,
            runner_up_data.get("timestamp"),
        )
    else:
        reason = "source '%s' dated %s is the only candidate" % (winner_id, winner_data.get("timestamp"))
    return Comparison(winner_id, winner_data.get("value"), reason, sources_compared)


MOST_RECENT_SOURCE_WINS = SurvivorshipRule("most_recent_source_wins", "1.0.0", _most_recent_compare)


# ---------------------------------------------------------------------------
# Rule 2: most-complete-value-wins: non-empty/non-null beats empty. Ties
# among multiple non-empty candidates keep the first in the given order
# (candidates is an ordered dict -- callers control tie-break by ordering).
# candidates: {source_id: {"value": ...}}
# ---------------------------------------------------------------------------
def _most_complete_compare(candidates: Dict[str, Dict[str, Any]]) -> Comparison:
    _require_candidates(candidates)
    winner_id = None
    for sid, data in candidates.items():
        if not _is_empty(data.get("value")):
            winner_id = sid
            break
    if winner_id is None:
        winner_id = next(iter(candidates))  # every candidate empty: keep first in given order
    winner_data = candidates[winner_id]
    sources_compared = [
        {
            "source_id": sid,
            "value": data.get("value"),
            "is_empty": _is_empty(data.get("value")),
            "outcome": "chosen" if sid == winner_id else "lost",
        }
        for sid, data in candidates.items()
    ]
    other_non_empty = [
        sid for sid in candidates if sid != winner_id and not _is_empty(candidates[sid].get("value"))
    ]
    if _is_empty(winner_data.get("value")):
        reason = "every candidate value was empty or null; source '%s' kept as first in given order" % winner_id
    elif other_non_empty:
        reason = (
            "source '%s' value %r is non-empty and was the first non-empty candidate in the given order "
            "(source(s) %s were also non-empty but listed later, tie-break is candidate order)"
            % (winner_id, winner_data.get("value"), ", ".join("'%s'" % s for s in other_non_empty))
        )
    else:
        reason = "source '%s' value %r is the only non-empty candidate" % (winner_id, winner_data.get("value"))
    return Comparison(winner_id, winner_data.get("value"), reason, sources_compared)


MOST_COMPLETE_VALUE_WINS = SurvivorshipRule("most_complete_value_wins", "1.0.0", _most_complete_compare)


# ---------------------------------------------------------------------------
# Rule 3: explicit-priority-list: a caller-supplied ordered list of source
# system names, highest priority first. Needs config, so it is a factory
# rather than a module-level constant like the two rules above.
# candidates: {source_id: {"value": ...}}
# ---------------------------------------------------------------------------
def make_explicit_priority_rule(priority: List[str], version: str = "1.0.0") -> SurvivorshipRule:
    priority = list(priority)

    def _priority_compare(candidates: Dict[str, Dict[str, Any]]) -> Comparison:
        _require_candidates(candidates)
        winner_id = None
        for sid in priority:
            if sid in candidates:
                winner_id = sid
                break
        if winner_id is None:
            raise ValueError(
                "none of the priority-list sources %r are present among candidates %r"
                % (priority, sorted(candidates))
            )
        winner_data = candidates[winner_id]
        rank = priority.index(winner_id)
        sources_compared = [
            {
                "source_id": sid,
                "value": data.get("value"),
                "priority_rank": priority.index(sid) if sid in priority else None,
                "outcome": "chosen" if sid == winner_id else "lost",
            }
            for sid, data in candidates.items()
        ]
        reason = (
            "source '%s' is rank %d in the explicit priority list %r, the highest-ranked source present "
            "among candidates %r" % (winner_id, rank, priority, sorted(candidates))
        )
        return Comparison(winner_id, winner_data.get("value"), reason, sources_compared)

    return SurvivorshipRule("explicit_priority_list", version, _priority_compare)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def resolve_field(field_name: str, candidates: Dict[str, Dict[str, Any]], rule: SurvivorshipRule):
    """Apply exactly one named rule to candidate values for one field.

    Returns (decision, lineage):

      decision is EXACTLY the {"value": ..., "winner_source_id": ...} shape
      scripts/reversibility_gate.py's build_reversible_action() already
      consumes as one entry of its survivorship_decisions dict.

      lineage is the full audit record: rule name/version/logic_fingerprint
      read directly off the SAME `rule` object whose .compare() method made
      the decision (never a second, independently-typed string), every
      source compared (not just the winner), a timestamp, and a reason
      string generated from the rule's own comparison output (real values
      substituted in, never a static canned string).
    """
    if not isinstance(rule, SurvivorshipRule):
        raise TypeError("rule must be a SurvivorshipRule instance, got %r" % (rule,))

    comparison = rule.compare(candidates)

    decision = {"value": comparison.winner_value, "winner_source_id": comparison.winner_source_id}

    lineage = {
        "field_name": field_name,
        "rule_name": rule.name,
        "rule_version": rule.version,
        "rule_logic_fingerprint": rule.logic_fingerprint,
        "winner_source_id": comparison.winner_source_id,
        "winner_value": comparison.winner_value,
        "sources_compared": comparison.sources_compared,
        "reason": comparison.reason,
        "decided_at": _now_iso(),
    }
    return decision, lineage


def _demo() -> None:
    print("most_recent_source_wins:")
    decision, lineage = resolve_field(
        "email",
        {
            "crm": {"value": "j.doe@example.test", "timestamp": "2026-01-05T00:00:00Z"},
            "pos": {"value": "jane.doe@example.test", "timestamp": "2026-03-10T00:00:00Z"},
        },
        MOST_RECENT_SOURCE_WINS,
    )
    print("  decision:", decision)
    print("  reason:", lineage["reason"])

    print("most_complete_value_wins:")
    decision, lineage = resolve_field(
        "phone",
        {"crm": {"value": ""}, "pos": {"value": "03-1234-5678"}},
        MOST_COMPLETE_VALUE_WINS,
    )
    print("  decision:", decision)
    print("  reason:", lineage["reason"])

    print("explicit_priority_list:")
    rule = make_explicit_priority_rule(["erp", "pos", "crm"])
    decision, lineage = resolve_field(
        "customer_name",
        {"pos": {"value": "Acme Test Corp"}, "crm": {"value": "Acme Test Corp Ltd."}},
        rule,
    )
    print("  decision:", decision)
    print("  reason:", lineage["reason"])


if __name__ == "__main__":
    _demo()
