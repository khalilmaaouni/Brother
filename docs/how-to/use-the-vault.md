# Use the Vault

Use the Vault for durable lessons that should change future work, not as a transcript archive. The useful test is: can a future task retrieve this lesson, understand its limits, and check whether it still applies?

## 1. Choose a local knowledge directory

Use an existing Markdown knowledge directory or create a dedicated one. Keep it outside a target repository that must remain clean. Set `BM_VAULT_ROOT` to its absolute path in the environment that launches your coding host. `BROTHERMODE_VAULT` and recorded configuration are fallback sources; see [root selection](../reference/vault.md).

The index lives under the selected client configuration directory, not necessarily beside the notes. Indexing can also include configured project-memory sources. Inspect the reported roots and counts before using sensitive material. Do not assume the Vault directory is the only source.

## 2. Write a retrievable lesson

Create a Markdown note with your editor. This illustrative example is not a claim about your application:

```markdown
# Retry after timeout can duplicate a charge

Symptom: a customer sees two charges after a request times out.
Context: payment retry path; inspect the current provider's semantics.
Constraint: retries must preserve the same idempotency key.
Failed approach: generate a fresh key for each attempt.
Evidence: link the incident and the exact regression check here.
Recheck when: provider, retry policy, or idempotency lifetime changes.
Do not infer: every timeout means the first charge failed.
```

Replace illustrative evidence with an actual reference before relying on the note. Name relevant paths or symbols to help exact-anchor retrieval. Describe the symptom as well as the cause: a future worker may know only the symptom.

## 3. Index and ask a concrete question

From a Brother checkout, with `BM_VAULT_ROOT` set:

```bash
python3 products/brothermode/tools/bm_vault.py index --vault "$BM_VAULT_ROOT"
python3 products/brothermode/tools/bm_vault.py status
python3 products/brothermode/tools/bm_vault.py recall --query "retry timeout duplicate charge" --limit 3 --fast --explain
```

Indexing writes the local search database. `status` reports its path, note count, freshness, and retrieval limitations; it can initialize the database if absent. `--fast` skips dense retrieval. `--explain` exposes retrieval signals instead of asking you to trust ranking silently.

Expected result: the relevant note appears with enough context to identify it. Counts and ranking vary with your notes. No results means retrieval has not supplied useful context; it does not mean the project has no relevant history. Reword the symptom, name a file, inspect the note directly, or refresh the index after changing notes.

A note without evidence metadata can appear as `UNVERIFIED` with a withholding warning. That is the expected treatment of the illustrative note above, not a failure to hide. Missing optional embedding tooling can report `NO-DATA` while lexical retrieval still returns a match. Neither a match nor a zero exit code verifies the lesson's content.

## 4. Use recall without promoting it to authority

Open the matched note. Check its evidence, age, and applicability against the current code and requirement. Ask Brother to state which lesson influenced the work and what current check supports its use. If the lesson is stale, correct or deprecate it explicitly rather than silently following it.

Local storage is not a promise of offline execution: recalled text supplied to your coding host may be sent to its model provider. Do not index anything you are not permitted to expose through that workflow.

A strong lesson contains the observable symptom, context, technique/decision that failed or succeeded, learned invariant/constraint, evidence/reference, and conditions under which it should be ignored/rechecked.

Write for retrieval: phrase the symptom in words a future practitioner will observe. Keep credentials, secrets, raw customer data, and unnecessary conversation history out.

Recall relevant memory at the point of action, not as a giant session-start dump.

## Verify the result

When memory influences work, current evidence still establishes the present claim. If memory conflicts with current truth, memory loses and should be corrected/deprecated.

To evaluate value across runs, record which lesson was retrieved, whether it changed the plan, whether that change was correct, and whether the known failure recurred. A growing note count alone is not evidence that the system learns usefully.
