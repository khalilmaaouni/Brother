# Overnight 1.0.16 consolidation handover

## Executive verdict

`PARTIAL: SAFE CHANGES READY, BLOCKERS PARKED`

This candidate is not ready for a release cut. The isolated branch contains
two completed P0 lanes: public host truth, and the shipped Cursor deny-chain
regression test. No merge, push, tag, or publish was performed. The umbrella
version is unchanged at `1.0.15` (a bump is a release act, deliberately not
taken here).

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

### Shipped Cursor deny chain (new this session)

- Files: `scripts/test_cursor_deny_chain.py` (new), `scripts/check_all.sh`,
  `scripts/required_fast.sh` (both register the new check)
- Result: `PASS`
- What it proves, that the sibling tests did not: it takes the real
  `preToolUse` command out of `bundle/cursor-hooks/hooks.json`, resolves
  `${PLUGIN_ROOT}` to `bundle/`, and runs it as a subprocess against a
  throwaway project whose store holds a claim by ANOTHER session. It asserts
  the whole shipped chain, not a stub: exit 2, a flat Cursor deny naming the
  record and its takeover command, the fenced file left untouched, the
  owner's own write still allowed, and byte-equality between the shipped
  `bundle/runtime` copies and the `products/brothermode/tools` sources so the
  chain under test is the one that ships.
- Backward drive: the same foreign write against a project that holds NO
  claim on the path comes back allow, exit 0, so the deny is the real fence
  reading a real claim, not the adapter or the mode refusing everything.
- What it does NOT establish: that a live signed-in Cursor Agent honors the
  refusal. That stays `NO-DATA` (see Cursor enforcement below).

Deciding commands and results (this session, evidence at
`~/.claude/evidence/1789261995-63797-*.txt`):

```text
python3 scripts/test_cursor_deny_chain.py -v     OK, 6 tests, exit 0
python3 scripts/public_host_truth.py             exit 0
python3 scripts/test_public_host_truth.py        exit 0
python3 scripts/doc_assurance.py                 exit 0
python3 scripts/test_client_parity.py            exit 0
python3 scripts/test_cursor_plugin.py            exit 0
python3 scripts/test_cursor_hook_run.py          exit 0
python3 scripts/test_cursor_smoke.py             exit 0 (signed-in smoke NO-DATA: cursor-agent not logged in)
python3 scripts/test_cursor_battery.py           exit 0
python3 scripts/test_required_fast.py            OK, 12 tests, exit 0
git diff --check                                 exit 0
sh -n scripts/check_all.sh; sh -n scripts/required_fast.sh   exit 0
```

`python3 scripts/system_doc.py --check` returns exit 1 until SYSTEM.md is
regenerated for the new file; that regeneration is the last edit before the
commit and is quoted in Battery below.

## Cursor enforcement

- Signed in available: `NO-DATA`
- Forbidden action attempted in live Cursor: `NO-DATA`
- Host honored a deny: `NO-DATA`
- Forbidden file unchanged: `NO-DATA`
- Founder Cursor witness: unchanged in deterministic signed-out fixtures
- Final enforcement status: `ADVISORY`

The checked-in adapter and deterministic deny tests pass, and now the SHIPPED
deny chain (hooks.json command, real fence, real cross-session claim) is
proven end to end by `scripts/test_cursor_deny_chain.py` (see Completed work).
None of these establish that a live signed-in Cursor Agent honors the refusal;
the signed-in half stays `NO-DATA` because `cursor-agent` reports not logged in
on this machine, and per the live-Cursor requirement that value is preserved
as `NO-DATA` rather than assumed. `docs/cursor/SMOKE-RUNBOOK.md` is the runbook
that closes it.

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

The full battery aggregate is `NO-DATA` this session for the same reason as
the overnight run: the shared worktree had up to 17 concurrent
`required_fast.sh` runs from other sessions during this window, so a
whole-battery pass cannot be attributed. Rather than fight the contention,
every check the fast gate contains, plus the new deny-chain check, was run
individually with full evidence capture through `scripts/run_evidence.py` and
each returned exit 0 (quoted in Completed work). The overnight `check_all.sh`
run had already reported the new host-truth checks as PASS and exposed
pre-existing or environmental failures (missing roadmap fixtures,
generated-state drift, acceptance fixture failures, missing Cursor capability);
those are unchanged and remain founder decision 2.

SYSTEM.md was regenerated as the last edit before the commit, because adding
`scripts/test_cursor_deny_chain.py` invalidated the whole-tree description:

```text
python3 scripts/system_doc.py            (regenerated SYSTEM.md)
python3 scripts/system_doc.py --check    exit 0 (after regeneration)
```

## Founder decisions

1. Run the live signed-in Cursor deny canary (docs/cursor/SMOKE-RUNBOOK.md)
   and decide whether advisory status may change. The DETERMINISTIC half is
   now closed: scripts/test_cursor_deny_chain.py proves the shipped chain
   refuses a cross-session write end to end. Only the live signed-in witness
   remains NO-DATA, blocked on cursor-agent login, which is founder-only.
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
