"""Run identity and outcome contract for Brother Core.

Provides deterministic run IDs, child IDs for units and claims, and the
outcome contract shape used to report a run's results.
"""

import secrets
from datetime import datetime
from typing import Optional


def new_run_id(prefix: str = "br") -> str:
    """Generate a new run ID.

    Args:
        prefix: The prefix for the run ID (default: "br").

    Returns:
        A string like "br-20260919-a1b2c3d4" (prefix, date YYYYMMDD, 8 hex chars).
    """
    date_str = datetime.now().strftime("%Y%m%d")
    random_suffix = secrets.token_hex(4)
    return f"{prefix}-{date_str}-{random_suffix}"


def new_child_id(kind: str, parent_run_id: str) -> str:
    """Generate a new child ID (unit, lane, claim, review, evidence, or receipt).

    Args:
        kind: One of: "unit_id", "lane_id", "claim_id", "review_id",
              "evidence_id", "receipt_id".
        parent_run_id: The parent run ID (unused in current implementation,
                       kept for future extensibility).

    Returns:
        A string like "unit-a1b2c3d4" (kind prefix, 8 hex chars).

    Raises:
        ValueError: If kind is not one of the six valid kinds.
    """
    valid_kinds = {"unit_id", "lane_id", "claim_id", "review_id",
                   "evidence_id", "receipt_id"}
    if kind not in valid_kinds:
        raise ValueError(f"Invalid kind '{kind}'. Must be one of: "
                         f"{', '.join(sorted(valid_kinds))}")

    # Map full kind names to short prefixes for the ID
    kind_prefixes = {
        "unit_id": "unit",
        "lane_id": "lane",
        "claim_id": "claim",
        "review_id": "review",
        "evidence_id": "evidence",
        "receipt_id": "receipt",
    }

    prefix = kind_prefixes[kind]
    random_suffix = secrets.token_hex(4)
    return f"{prefix}-{random_suffix}"


def outcome_contract(
    run_id: str,
    product: str = "Brother",
    product_version: str = "1.1.0-rc.1",
    capabilities: Optional[list] = None
) -> dict:
    """Generate the outcome contract JSON shape for a run.

    Args:
        run_id: The run ID from new_run_id().
        product: Product name (default: "Brother").
        product_version: Product version (default: "1.1.0-rc.1").
        capabilities: List of capabilities or None (default: empty list).

    Returns:
        A dict with keys: run_id, product, product_version, capabilities.
    """
    if capabilities is None:
        capabilities = []

    return {
        "run_id": run_id,
        "product": product,
        "product_version": product_version,
        "capabilities": capabilities,
    }
