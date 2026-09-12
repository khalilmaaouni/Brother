# Overnight 1.0.16 consolidation handover

## Executive verdict

`PARTIAL: SAFE CHANGES READY, BLOCKERS PARKED`

This candidate is not ready for a release cut. The isolated branch contains
one completed P0 lane. No merge, push, tag, or publish was performed.

## Baseline

- Starting release: `v1.0.15`
- Starting SHA: `66e7c261d165cbd986b41b77e5aa2f8d87ea2b79`
- Candidate branch: `overnight/1.0.16-consolidation`
- Candidate commit: `f9bc52e`
- Python: `3.13.14`
- Git: `2.50.1`
- Claude CLI: available at `/Users/khalil.maaouni/.local/bin/claude`
- Codex CLI: available at `/Users/khalil.maaouni/.local/bin/codex`
- Cursor CLI: `NO-DATA`, no executable `cursor-agent` found
- Exact controller and worker model IDs: `NO-DATA`, not exposed in this checkout

## Completed work

### Public host truth

- Commit: `f9bc52e`
- Files: `README.md`, `docs/README.md`, `docs/reference/install-matrix.md`,
  `scripts/check_all.sh`, `scripts/public_host_truth.py`,
  `scripts/test_public_host_truth.py`, generated `SYSTEM.md`
- Result: `PASS`
- Shipped hosts derived from existing manifests: Claude Code, Codex, Cursor
- Cursor wording remains advisory until a live signed-in deny canary passes
- Backward tests prove failure when Cursor disappears from docs and when an
  invented install-matrix host is added
- Review status: focused independent rerun completed in the same checkout;
  a separate fresh review context was unavailable, so this remains a review
  limitation

Deciding commands and results:

```text
python3 scripts/test_public_host_truth.py -v   OK, 3 tests
python3 scripts/public_host_truth.py            PASS: public host truth: Claude Code, Codex, Cursor
python3 scripts/doc_assurance.py                PASS: 7 check(s) clear, 0 NO-DATA
python3 scripts/test_client_parity.py -v        OK, 2 tests
python3 scripts/test_cursor_plugin.py            OK, 12 tests
python3 scripts/test_cursor_hook_run.py -v      OK, 10 tests
python3 scripts/test_cursor_smoke.py -v          OK, 9 tests
python3 scripts/test_cursor_battery.py -v       OK, 6 tests
python3 scripts/system_doc.py --check            exit 0
git diff --check                                 exit 0
```

## Cursor enforcement

- Signed in available: `NO-DATA`
- Forbidden action attempted in live Cursor: `NO-DATA`
- Host honored a deny: `NO-DATA`
- Forbidden file unchanged: `NO-DATA`
- Founder Cursor witness: unchanged in deterministic signed-out fixtures
- Final enforcement status: `ADVISORY`

The checked-in adapter and deterministic deny translation tests pass. They do
not establish that a live signed-in Cursor Agent honors the refusal.

## Other workstreams

- One user-facing Brother door: `NO-DATA`, no implementation landed
- Context Capsule: `NO-DATA`, no implementation landed
- Reversible autonomous rulings: existing ledger tests ran, no new policy landed
- Normalized capabilities: `NO-DATA`, parked to avoid an execution refactor
- Current competitive race: `NO-DATA`, not run
- Acceptance Time: `NO-DATA: human trial not executed`
- Unwired system parts: `NO-DATA`, classification not completed

## Battery

The registered command was started:

```text
sh scripts/check_all.sh
```

It reached the long product suites but was stopped after 28 minutes because
other concurrent `check_all.sh` runs were already executing in the shared
worktree. The captured run reported new host-truth checks as PASS and exposed
pre-existing or environmental failures including missing roadmap fixtures,
generated-state drift, acceptance fixture failures, and missing Cursor
capability. The run did not produce a final summary line, so aggregate counts
are `NO-DATA`.

## Founder decisions

1. Run the live signed-in Cursor deny canary and decide whether advisory status
   may change.
2. Decide whether to repair the pre-existing full-battery fixture failures as
   part of 1.0.16 or carry them into a separate repair cut.
3. Approve fresh benchmark controls and real human Acceptance Time reviewers
   before any comparative or acceptance claim is made.

## Receipt and residue

The change is reviewable in commit `f9bc52e`. Test-generated residue was moved
to `/tmp/brother-overnight-residue.KpPySb` when safe to move; concurrent
batteries later recreated `.brothermode` directories, which were not staged.
No user configuration, credentials, production data, default branch, tag, or
remote was changed.
