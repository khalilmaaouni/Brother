"""Brother Core acceptance module.

Provides acceptance recording for run receipts.
Acceptance is the human gate on a receipt verdict.
"""

import json


def record_acceptance(run_id, receipt_id, decision, decider, at, override=False, reason=None):
    """Record human acceptance of a receipt.

    Args:
        run_id: Unique identifier for the run
        receipt_id: Identifier of the receipt being accepted/rejected/deferred
        decision: One of "ACCEPTED", "REJECTED", "DEFERRED" (raises ValueError otherwise)
        decider: Human or role making the decision
        at: Timestamp of the decision (ISO 8601 or similar)
        override: If True, decision overrides automated gates (default False)
        reason: Required non-empty string if override=True (raises ValueError if not)

    Returns:
        dict: Acceptance record JSON with run_id, receipt_id, decision, decider, at,
              override, and reason

    Raises:
        ValueError: If decision is invalid, or override=True with missing/empty reason
    """
    valid_decisions = ("ACCEPTED", "REJECTED", "DEFERRED")
    if decision not in valid_decisions:
        raise ValueError(
            f"decision must be one of {valid_decisions}, got {decision!r}"
        )

    if override and not reason:
        raise ValueError(
            "override=True requires a non-empty reason string. "
            "An override is explicit and must record its justification."
        )

    return {
        "run_id": run_id,
        "receipt_id": receipt_id,
        "decision": decision,
        "decider": decider,
        "at": at,
        "override": override,
        "reason": reason,
    }
