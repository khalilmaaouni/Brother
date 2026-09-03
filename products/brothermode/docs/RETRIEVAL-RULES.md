# The retrieval contract

WBS row VB11-02. This page names the rules that govern what `bm_vault.py
recall` (and its siblings on the served path: `bm_vault_cli.py recall`,
`bm_vault_serve.py`) actually returns, in the order they run against a real
query. It exists so a change to any one rule is a change someone can find,
read, and re-verify, rather than a fact that only lives in a comment three
modules deep.

Each section below ends with one `PIN:` line, in this exact machine-readable
shape:

```
PIN: <rule-slug> -> <test_file>::<TestClass>::<test_method>
```

`tools/test_bm_retrieval_rules.py` PARSES these lines mechanically: it checks
that all six rule slugs are present, that every named test file, class, and
method actually exists, and that deleting a rule line fails the check. A pin
is a claim that the named test, today, actually exercises the rule described
above it. **Editing a rule's behavior means updating its `PIN:` line in the
SAME change** (a new test, a renamed test, or a different existing test that
now covers it); a pin left pointing at a test that no longer proves the rule
is a false claim, not a passing one.

## 1. Staged retrieval order

A query is answered by the cheapest signals first. An exact anchor match
(the query names a file or symbol some note already references) and a
lexical BM25 match on wording both run in well under a second. The dense
embedding signal is STAGED behind them: it costs 30-75 seconds on this
machine (a subprocess that loads a real embedding model from scratch), so it
runs only when the cheap signals leave a real gap, nothing found, fewer
results than the requested `--limit`, or the two cheap signals disagree on
their top results entirely. `--fast` always skips it. Every signal's hits
are fused by reciprocal rank fusion, never averaged into one blended score.

Module: `tools/bm_vault.py` (`_search`).

PIN: staged-retrieval-order -> test_bm_vault.py::VaultRetrieval::test_07_the_dense_stage_is_skipped_when_lexical_signals_already_answer

## 2. Identity trim

A recall carrying `--identity` (or the `BM_IDENTITY` fallback) is trimmed
against the access policy (vault-relative `99-System/access-policy.json`)
before any denied note's body is read for ranking, so a forbidden note never
participates in authority sorting or link expansion. Deny wins on any tie
with a matching allow. No policy file at all means everything stays
readable, trimming is opt-in, not a default that could surprise an estate
that never configured it. A broken policy file fails the recall closed,
never open.

Module: `tools/bm_vault_policy.py`, applied from `tools/bm_vault.py`
(`_policy_deny`, `_search`).

PIN: identity-trim -> test_bm_vault_policy.py::IdentityTrimsRealRecall::test_03_a_sees_and_b_provably_does_not

## 3. Staleness demotion

A note authority-declares itself (for example `source_of_record`), but that
declaration can go stale: once its `verified_at` date crosses its note
type's staleness horizon, it is demoted one authority step below what it
declares, never below casual, so a FRESH note at the same declared
authority now outranks it, while it still outranks an ordinary casual note.
An absent staleness module degrades to no demotion, audibly, never a crash.

Module: `tools/bm_vault_staleness.py`, applied from `tools/bm_vault.py`
(`_authority_sort`, inside `_search`).

PIN: staleness-demotion -> test_bm_vault_staleness.py::StalenessDemotesAuthorityInRealRecall::test_02_the_fresh_source_of_record_outranks_the_stale_one

## 4. Restriction withholding

Once a note is denied, by the identity trim above, by a revoked principal,
or by a default-deny policy with no matching allow rule, the served output
never names it: not its title, not its path, not its content, anywhere in
the printed result. The caller learns only a count ("N note(s) withheld by
access policy"), even in the extreme case where every candidate the query
would otherwise return is withheld and the honest answer is that nothing
can be shown. Naming what someone may not see is itself a leak, so the
withholding is a count, never a name.

Module: `tools/bm_vault_policy.py` / `tools/bm_vault.py` (the `_denied`
bookkeeping inside `_search`, and `cmd_recall`'s summary line).

PIN: restriction-withholding -> test_bm_vault_policy.py::IdentityTrimsRealRecall::test_05_default_deny_denies_the_anonymous_caller

## 5. Echo exclusion

Content that is a CONFIRMED echo of an answer this same estate already
served, matched against the self-echo provenance marker `bm_vault_audit.py`
prints on every served recall, and the source event id recorded alongside
it, is excluded at intake, before it can be admitted back into the vault as
if it were independent evidence. Without this, a served answer could be
re-ingested from wherever it was pasted and inflate its own authority the
next time the same question is asked, a note agreeing with itself rather
than a second independent source.

Module: `tools/bm_vault_intake.py`, reading the marker written by
`tools/bm_vault_audit.py`.

PIN: echo-exclusion -> test_bm_vault_intake.py::SelfEchoProvenance::test_a_served_answer_reingested_is_classified_echo_with_its_source_event_id

## 6. Audit-on-serve

Every recall that actually appends a fresh answer (not a duplicate retry of
an already-recorded event id, not a refused id collision) records one
access-audit row: the accountable principal (`--as`, else the literal string
`NO-DATA`, never a guess), the served note ids ONLY (never a title or a
path), the withheld count, and the same event id the answer ledger recorded
for this exact answer. A policy-driven refusal is recorded as a refusal,
not silently folded into an ordinary withholding count.

Module: `tools/bm_vault_audit.py`, called from `tools/bm_vault.py`
(`_append_audit`, `cmd_recall`).

PIN: audit-on-serve -> test_bm_vault_audit.py::TwoPrincipalsRecallingOnARealPolicy::test_02_alice_and_bob_each_leave_a_correct_record
