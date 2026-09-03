# Vendored seam: BrotherModeUp's served vault, tenancy and policy modules

WHY THESE FILES ARE HERE. VB3-03 (RequestContext and tenancy) and VB3-04
(policy fails closed, dual principals) both landed and MERGED in the
BrotherModeUp repository, not in this hub: the hub's `editions/` tree was
seeded from the PUBLIC main tip at the 2026-08-30 migration
(docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md), and these two rows never
shipped to the public repository. scripts/readiness_gate.py's own rows for
"tenancy-leakage-zero" and "fail-closed-policy" say so directly: "VB3-03
landed in BrotherModeUp (PR 159)" / "VB3-04 landed in BrotherModeUp (PR 160);
the Brother-side evidence suite that proves it from this repository is
queued." This directory is that evidence suite's fixture: a frozen, read-only
copy of the four files the served recall boundary actually needs, so
scripts/test_tenancy_isolation.py and scripts/test_policy_fail_closed.py can
invoke the REAL product boundary (the served HTTP endpoint and the CLI
recall command) as a buyer would, without this repository depending on a
BrotherModeUp checkout existing on the machine that runs the check.

SOURCE, exact commits (BrotherModeUp repository):
  - bm_vault_serve.py, bm_vault_context.py: `git show 9ea9196:tools/<file>`
    (PR 159, "VB3-03: every served request knows who, where and as-of when,
    and two tenants never mix"), reachable through the later merge below.
  - bm_vault_policy.py: `git show f69777e:tools/bm_vault_policy.py`
    (PR 160, "VB3-04: policy knows both principals, returns explicit
    verdicts, and fails closed"). f69777e's branch contains 9ea9196
    (verified: `git merge-base --is-ancestor 9ea9196 f69777e` exits 0), so
    this single commit carries both landed rows together, consistent with
    each other.
  - bm_vault.py: `git show f69777e:tools/bm_vault.py`, same commit, so the
    recall command these two modules plug into matches the same point in
    history.
  - bm_freshness.py: `git show f69777e:tools/bm_freshness.py`, same commit.
    NOT optional: bm_vault.py's _print_hits calls _load_bm_freshness()
    with no guard at all (confirmed by running recall without it first --
    it crashed with an uncaught FileNotFoundError, exit 1, no results),
    unlike bm_vault_authority.py, bm_vault_staleness.py, bm_store.py,
    bm_vault_lifecycle.py and bm_vault_audit.py, which all degrade to a
    stated stderr line and keep going. This file corrects that: it IS
    vendored, and the paragraph below only describes the modules that
    really are optional.

WHAT IS DELIBERATELY NOT VENDORED. bm_vault.py dynamically loads several
sibling contract modules (bm_vault_audit.py, bm_vault_principals.py,
bm_vault_lifecycle.py, vault_recall_hook.py, and others) by
path, and every one of those load sites is guarded: an absent file degrades
to a stated stderr warning and unchanged recall behavior (never a crash,
never silent broad access), read and confirmed line by line before this
fixture set was frozen. None of them is on the path the tenancy or
fail-closed proofs exercise, so none is vendored; each proof script's own
docstring says which guarded degradation it relies on.

DO NOT EDIT THESE FILES to make a test pass. They are frozen evidence of
what actually merged; a proof that needs the product to behave differently
belongs against a NEWER vendored snapshot with its own commit SHA recorded
above, never a hand edit here.
