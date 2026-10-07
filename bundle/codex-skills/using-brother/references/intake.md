# Intake, one record

Read this when the ask is an intake: a new outcome, a decision to put to a
person, or a plan to accept. Loaded on demand, never at session start
(tests/test_context_budget.py caps the door at 6,000 bytes).

An intake writes exactly ONE record: the outcome contract. Its schema ships
at `runtime/outcome-contract-v1.json` under the plugin root
(`docs/schema/outcome-contract-v1.json` in a checkout of the Brother
repository; 2026-09-08 debate judgment, merging on branch
u4-contract-schema). Check a record with:

    python3 "$BROTHER_PLUGIN_ROOT/runtime/contract_check.py" <record>.json

(`scripts/contract_check.py` in a checkout of the Brother repository.)

Exit 0 PASS, 1 FAIL naming every violation, 2 NO-DATA if the record or
schema cannot be read as JSON. One write path, one read path, one schema.

## While the record is still being drafted

The shipped bundle carries no separate mid-draft file or promotion step.
Write the draft directly OUTSIDE the target repository (a record inside it
dirties the tree, and the engine refuses a dirty tree), for example
`"${CODEX_HOME:-$HOME/.codex}/brother/contracts/<slug>.json"` (under Claude
Code `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/brother/contracts/<slug>.json"`),
and re-run the check above against it as it grows; there is one file from
the first line written, never two. Pass it to the engine as `--contract`.

## Every source carries a receipt

The shipped bundle carries no resolver that checks a `file:`/`evidence:`/
`url:` ref against what is actually on disk. `runtime/contract_check.py`
still enforces the grammar every receipt must clear: `receipts[].ref` must
start with `file:`, `evidence:`, or `url:`, and every
`must_answer.receipt_id` must name a real entry in `receipts`. A source
that fails either check, or that carries no receipt at all, renders
UNVERIFIED wherever the record is shown.

## Corrections

A correction made once is a fact from then on:

    python3 "$BROTHER_PLUGIN_ROOT/runtime/annotations_store.py" add <record>.json

(`scripts/annotations_store.py` in a checkout of the Brother repository;
also `list`, `remove <id>`.) It holds the correction at
`docs/decisions/annotations.json`: per user, under `~/.claude/brother-run`,
for an installed plugin (never in the plugin folder, which an update
replaces), and in the repository itself in a checkout. It is keyed on the
option and criterion it corrects, never on which record it was first typed
into.
