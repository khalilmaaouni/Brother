"""Evidence envelope for Brother Core.

The Evidence dataclass captures the outcome of a capability claim:
a fixed, immutable record of what was tested, how, on what revision,
and what the result was (PASS, FAIL, or NO-DATA).

CRITICAL INVARIANT: PASS/FAIL/NO-DATA are never redefined by any domain.
NO-DATA is never a pass, never a fail, and is never silently dropped.
This invariant is enforced in code, not just in comments.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# Allowed values for domain and status, enforced at construction.
ALLOWED_DOMAINS = {"mode", "assurance", "data", "mobile", "vault", "core"}
ALLOWED_STATUSES = {"PASS", "FAIL", "NO-DATA"}


class _FrozenDict(dict):
    """A dict that refuses edits but still copies and serializes like one.
    MappingProxyType was the obvious choice and broke dataclasses.asdict
    (it cannot be deep-copied), which no unit test noticed: only serializing
    a real record did."""

    def _refuse(self, *args, **kwargs):
        raise TypeError("this evidence field is read-only")

    __setitem__ = __delitem__ = _refuse
    update = pop = popitem = clear = setdefault = _refuse

    def __deepcopy__(self, memo):
        import copy
        return {copy.deepcopy(k, memo): copy.deepcopy(v, memo) for k, v in self.items()}


@dataclass(frozen=True)
class Evidence:
    """Immutable evidence record for a capability claim.

    Fields:
        schema: Version identifier for this evidence format (default: "brother-evidence-v1").
        evidence_id: Unique identifier for this evidence record.
        run_id: Identifier linking this evidence to a run session.
        unit_id: Identifier for the unit (test, check, gate) that produced this evidence.
        domain: One of "mode", "assurance", "data", "mobile", "vault", "core".
        capability: Name of the capability being tested or verified.
        claim: Natural language statement of what is being claimed.
        status: One of "PASS", "FAIL", "NO-DATA".
        producer: Dict with "kind" (e.g. "test", "gate", "manual") and "identity" (tool/script name).
        method: How the evidence was gathered (e.g. "unittest", "integration_test", "human_review").
        candidate_revision: Git revision (hash or branch) being tested.
        artifacts: List of artifact references (logs, screenshots, files) produced during the test.
        created_at: ISO 8601 timestamp of when this evidence was created.
        limitations: List of known limitations or caveats of this evidence.
    """

    schema: str = "brother-evidence-v1"
    evidence_id: str = ""
    run_id: str = ""
    unit_id: str = ""
    domain: str = ""
    capability: str = ""
    claim: str = ""
    status: str = ""
    producer: Dict[str, str] = field(default_factory=dict)
    method: str = ""
    candidate_revision: str = ""
    artifacts: List[str] = field(default_factory=list)
    created_at: str = ""
    limitations: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate domain and status against their allowed sets.

        Raises ValueError if domain or status is not in the allowed set.
        This is the one place the brief says no domain may redefine these terms,
        so it must be enforced in code.
        """
        # No truthiness guard: "if self.status and ..." skipped exactly the
        # unknown case, so Evidence(status="") was neither PASS, FAIL nor
        # NO-DATA and every is_* helper answered False (proven 2026-09-20).
        # An unstated status is NO-DATA and must be SAID, never left blank.
        if self.domain not in ALLOWED_DOMAINS:
            raise ValueError(
                f"domain must be one of {ALLOWED_DOMAINS}, got: {self.domain}"
            )
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(
                f"status must be one of {ALLOWED_STATUSES}, got: {self.status}"
            )
        # frozen=True froze the attributes, not what they point at: a caller
        # could still append to artifacts or edit producer after the fact.
        object.__setattr__(self, "producer", _FrozenDict(self.producer))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "limitations", tuple(self.limitations))


def is_pass(evidence: Evidence) -> bool:
    """Return True if the evidence status is PASS, False otherwise.

    This function exists so callers never compare evidence.status == "PASS" by hand
    in five different places. Fix at the source: one place every caller routes through.

    Args:
        evidence: An Evidence instance.

    Returns:
        True if status is PASS, False otherwise.
    """
    return evidence.status == "PASS"


def is_fail(evidence: Evidence) -> bool:
    """Return True if the evidence status is FAIL, False otherwise.

    This function exists so callers never compare evidence.status == "FAIL" by hand
    in five different places. Fix at the source: one place every caller routes through.

    Args:
        evidence: An Evidence instance.

    Returns:
        True if status is FAIL, False otherwise.
    """
    return evidence.status == "FAIL"


def is_no_data(evidence: Evidence) -> bool:
    """Return True if the evidence status is NO-DATA, False otherwise.

    This function exists so callers never compare evidence.status == "NO-DATA" by hand
    in five different places. Fix at the source: one place every caller routes through.

    Args:
        evidence: An Evidence instance.

    Returns:
        True if status is NO-DATA, False otherwise.
    """
    return evidence.status == "NO-DATA"
