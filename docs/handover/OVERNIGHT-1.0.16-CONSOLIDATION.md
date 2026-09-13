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

- Signed in available: `YES` (cursor-agent logged in as the founder; overnight
  said NO-DATA/not found, the ground moved since)
- Deterministic deny (candidate bundle): `PASS` via
  `scripts/test_cursor_deny_chain.py` (the real hooks.json command refuses a
  cross-session write)
- Live signed-in smoke: `FAIL`, but NOT a candidate defect (see below)
- Live signed-in deny: `NO-DATA` (the smoke never reached an edit, so no deny
  was ever attempted)
- Founder Cursor witness: `UNCHANGED` (before and after hash both
  `a646765e87f51f8dfaa9fcb04c35c77b0ea808343ec070d1bb622a6dfdd7b0c9`)
- Final enforcement status: `ADVISORY` (unchanged)

The signed-in smoke was run now that cursor-agent is logged in
(`python3 scripts/cursor_smoke.py --signed-in`, evidence at
`~/.claude/evidence/1789262771-78243-*.txt`, exit 1). It reported HOOKS-FIRE
FAIL, HOOKS-ROOT FAIL, EDIT FAIL. The ROOT CAUSE is environmental, not the
1.0.16 candidate: the founder's user-level `~/.cursor/hooks.json` hardcodes
absolute commands to `/Users/khalil.maaouni/Brother/bundle/cursor/cursor_hook.py`,
a SUPERSEDED path that no longer exists (the root-level `bundle/cursor` tree
was replaced by `products/brothermode`; `~/Brother` is now on `main`, which
does not carry it). cursor-agent loaded that broken user-level hook and
refused every mutating tool with `[Errno 2] No such file or directory`, so
the turn never reached an edit or the engine.

The candidate bundle itself is clean: `grep -rn 'bundle/cursor/cursor_hook'
bundle/` returns nothing, its hooks live in `bundle/cursor-hooks/hooks.json`
under `${PLUGIN_ROOT}` relative paths, and the deterministic deny chain over
exactly that file passes. So the live signed-in requirement stays `NO-DATA`,
preserved as instructed, and it is blocked by a stale machine config, not by
the release candidate. This is a real founder-facing machine finding: the
installed Cursor plugin needs reinstalling from the current bundle
(`python3 products/brothermode/scripts/install_cursor.py`, or the equivalent
in the current tree) so `~/.cursor/hooks.json` points at a script that exists;
until then the founder's Cursor refuses every edit in every project.

## Other workstreams

- One user-facing Brother door: `NO-DATA`, no implementation landed
- Context Capsule: `NO-DATA`, no implementation landed
- Reversible autonomous rulings: existing ledger tests ran, no new policy landed
- Normalized capabilities: `NO-DATA`, parked to avoid an execution refactor
- Current competitive race: `NO-DATA`, not run
- Acceptance Time: `NO-DATA: human trial not executed`
- Unwired system parts: `NO-DATA`, classification not completed
- Founder Cursor install (machine finding, this session): `BROKEN`. The
  user-level `~/.cursor/hooks.json` points at the deleted superseded script
  `~/Brother/bundle/cursor/cursor_hook.py`, so cursor-agent refuses every
  mutating tool in every project. Fix: reinstall from the current bundle
  (`python3 products/brothermode/scripts/install_cursor.py`, see
  `docs/how-to/install-cursor.md`). Founder-only: it is his live machine
  config, outside the release repository, so it was flagged, not edited.

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

A second small fix landed this session: `scripts/required_fast.sh`'s
`run_check` readout showed a passing check's trailing adversarial
`FAIL:`/`NO-DATA:` self-test line, which reads as a failure to a skimmer. It
now prefers the unittest `OK` line on a pass and a real failure line on a
fail, mirroring `scripts/check_all.sh`'s existing `run_check`; the verdict
still comes only from the exit code. Proof:
`python3 scripts/test_required_fast.py` returns 12 tests OK, exit 0.

SYSTEM.md was regenerated as the last edit before the commit, because adding
`scripts/test_cursor_deny_chain.py` invalidated the whole-tree description:

```text
python3 scripts/system_doc.py            (regenerated SYSTEM.md)
python3 scripts/system_doc.py --check    exit 0 (after regeneration)
```

## Founder decisions

1. Reinstall the founder's Cursor plugin, then decide on advisory status.
   The signed-in smoke was run this session and FAILED, but only because the
   founder's `~/.cursor/hooks.json` points at a deleted script (see Other
   workstreams); the 1.0.16 candidate bundle is clean and its deterministic
   deny chain passes (scripts/test_cursor_deny_chain.py). So the DETERMINISTIC
   half is closed and the live signed-in half stays NO-DATA until the stale
   install is fixed and the smoke re-run. Fixing the install is one command;
   it is founder-only because it is his live machine config.
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
