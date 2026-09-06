#!/usr/bin/env python3
"""Shared reader for the BM_VAULT_DISABLE_* mutation seams (row P0-M security
finding, 2026-09-06). Every one of these environment variables is a
fail-open test hook: it exists only so a mutation test or the memory-
poisoning gauntlet can prove one protection is load-bearing, by turning it
off one at a time and watching the row it protects flip. A fail-open hook
read straight off os.environ by each consumer, with no shared record of
which ones are live, is exactly the finding this file exists to close: any
process with one set silently runs recall or admission with a protection
off, and nothing downstream can tell without grepping every module for
os.environ.get calls by hand.

Mirrors scripts/jbeq_decide.py's own mutation-seam contract (hub PR 386,
2026-09-06): LOUD (every consumer says so, in the same words) and CLOSED (a
committed record, or a real vault write, must refuse to happen while a seam
is live). This module is the LOUD half's one source of truth: every
consumer below imports active_seams()/banner() from here instead of reading
os.environ directly, so the stderr banner (bm_vault.py's check), the hit
markers (_print_hits), the lesson_states records (vault_recall_hook.py),
the gauntlet's own "mutation" field, and receipt_door.py's memory marker
all read the exact same set from the exact same call.

NEVER set any of SEAM_VARS in production. Each one is documented at its own
call site (bm_vault.py, bm_vault_intake.py, vault_recall_hook.py,
bm_vault_contradiction.py) with the single protection it turns off.
"""
import os

#: Every BM_VAULT_DISABLE_* seam this estate defines. Add a new mutation
#: hook's env var name here so every consumer (the stderr banner, the hit
#: markers, the gauntlet's mutation report, receipt_door.py's memory
#: marker, and bm_vault_intake.py's admission refusal) sees it without a
#: second edit anywhere else.
SEAM_VARS = (
    "BM_VAULT_DISABLE_SAFETY_PRECEDENCE",
    "BM_VAULT_DISABLE_APPROVAL_FORGERY_CHECK",
    "BM_VAULT_DISABLE_EVIDENCE_LOCATOR_CHECK",
    "BM_VAULT_DISABLE_ANCHOR_CHECK",
    "BM_VAULT_DISABLE_CREDENTIAL_GATE",
    "BM_VAULT_DISABLE_DENYLIST_GATE",
    "BM_VAULT_DISABLE_LIFECYCLE_GATE",
)


def active_seams():
    """Sorted tuple of the SEAM_VARS names currently set to a truthy env
    value (any non-empty string -- the same truthiness every existing
    os.environ.get(...) call site in this estate already used). An empty
    tuple means production shape: nothing disabled."""
    return tuple(sorted(v for v in SEAM_VARS if os.environ.get(v)))


#: The one banner text every consumer prints or records, so a caller
#: reading any one artifact (stderr, a hit line, a lesson_states record, a
#: gauntlet's mutation field, a receipt's memory section) recognizes the
#: same words the others use.
BANNER_FMT = "MUTATION SEAM ACTIVE (vault protections disabled: %s)"


def banner(seams=None):
    """The exact banner text, built from `seams` (defaults to a fresh
    active_seams() read) so a caller that already computed the set once
    does not have to read the environment a second time. Returns "" when
    no seam is active, so a caller can safely do `if banner(): ...`."""
    seams = active_seams() if seams is None else seams
    if not seams:
        return ""
    return BANNER_FMT % ", ".join(seams)


def demo():
    """ponytail: the one runnable self-check for this module's only real
    logic (which vars count, and the banner's exact shape). No test
    framework: this is the whole surface."""
    assert active_seams() == ()
    assert banner() == ""
    os.environ["BM_VAULT_DISABLE_LIFECYCLE_GATE"] = "1"
    try:
        assert active_seams() == ("BM_VAULT_DISABLE_LIFECYCLE_GATE",)
        assert banner() == (
            "MUTATION SEAM ACTIVE (vault protections disabled: "
            "BM_VAULT_DISABLE_LIFECYCLE_GATE)")
        assert banner(("BM_VAULT_DISABLE_ANCHOR_CHECK",)) == (
            "MUTATION SEAM ACTIVE (vault protections disabled: "
            "BM_VAULT_DISABLE_ANCHOR_CHECK)")
    finally:
        del os.environ["BM_VAULT_DISABLE_LIFECYCLE_GATE"]
    print("bm_vault_seams: demo OK")


if __name__ == "__main__":
    demo()
