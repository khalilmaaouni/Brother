"""Brother Core receipt module.

Provides receipt building and validation for run outcomes.
A receipt is the terminal verdict of a run, with per-domain evidence summary.
"""

import json

from plugin.runtime.brother.core.evidence import ALLOWED_STATUSES


def _domain_has_real_evidence(content, key=None):
    """A domain counts as evidence only when it carries a checkable
    signal, recursively: a number, a bool, or a status string from this
    repo's own vocabulary (ALLOWED_STATUSES), at any nesting depth.

    Free-text prose ("trust me, it works") never counts, whatever it is
    wrapped in (a dict value, a list item, a nested dict): wrapping a
    comment in a list does not make it evidence. A status string DOES
    count, since PASS/FAIL/NO-DATA is this repo's own evidence
    vocabulary (plugin/runtime/brother/core/evidence.py) and the normal
    way real evidence is encoded; being a string is not by itself a
    reason to reject a value, only being unrecognized free text is. A
    string under a key literally named "artifacts" also counts (it
    matches Evidence.artifacts' own field name: a path/URI reference is
    a real, checkable pointer, not prose).
    """
    if content is None:
        return False
    if isinstance(content, bool):
        return True
    if isinstance(content, (int, float)):
        return True
    if isinstance(content, str):
        if content in ALLOWED_STATUSES:
            return True
        return key == "artifacts" and bool(content)
    if isinstance(content, dict):
        return any(
            _domain_has_real_evidence(v, key=k) for k, v in content.items()
        )
    if isinstance(content, list):
        return any(_domain_has_real_evidence(v, key=key) for v in content)
    return False


def assert_closeout_sound(receipt):
    """Enforce the invariant: PASS verdict requires at least one domain with evidence.

    A receipt with verdict PASS but every domain entry empty is a false green,
    forbidden by brief section 40: a terminal headline can never be stronger than
    required unresolved evidence.

    Raises ValueError if the receipt is unsound.
    """
    if receipt.get("verdict") != "PASS":
        return

    domains = receipt.get("domains", {})
    if not domains:
        raise ValueError(
            "Receipt verdict is PASS but domains is empty or missing. "
            "PASS requires at least one domain with evidence."
        )

    # Check if every domain entry is empty or prose-only (a named false
    # green: a domain that is truthy but carries nothing checkable).
    has_evidence = False
    for domain, content in domains.items():
        if _domain_has_real_evidence(content):
            has_evidence = True
            break

    if not has_evidence:
        raise ValueError(
            "Receipt verdict is PASS but all domain entries are empty or "
            "prose-only (a note or comment, no count, status, or artifact). "
            "PASS requires at least one domain with real, checkable evidence."
        )


def build_receipt(run_id, product_version, verdict, domains):
    """Build a receipt for a run outcome.

    Args:
        run_id: Unique identifier for the run
        product_version: Version string of the product being tested
        verdict: One of "PASS", "FAIL", "NO-DATA" (raises ValueError otherwise)
        domains: Dict keyed by domain name with arbitrary per-domain content.
                 Valid domain names: "mode", "assurance", "data", "mobile", "vault"

    Returns:
        dict: Receipt JSON with product, version, run_id, verdict, domains,
              and acceptance_required=True

    Raises:
        ValueError: If verdict is invalid or receipt fails invariant check
    """
    valid_verdicts = ("PASS", "FAIL", "NO-DATA")
    if verdict not in valid_verdicts:
        raise ValueError(
            f"verdict must be one of {valid_verdicts}, got {verdict!r}"
        )

    receipt = {
        "product": "Brother",
        "version": product_version,
        "run_id": run_id,
        "verdict": verdict,
        "domains": domains,
        "acceptance_required": True,
    }

    assert_closeout_sound(receipt)
    return receipt
