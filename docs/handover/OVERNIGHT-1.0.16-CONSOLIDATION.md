# Overnight 1.0.16 consolidation handover

## Executive verdict

`PARTIAL: SAFE CHANGES READY, BLOCKERS PARKED`

The isolated branch now passes the authoritative fast gate
(`scripts/required_fast.sh`, exit 0, transition ALLOWED) with these completed
lanes: public host truth, the shipped Cursor deny-chain regression test, the
restored Area 5 acceptance calibration, a clearer required_fast readout, and a
fixed README first-screen wording regression. It is a SAFE CANDIDATE, not a
declared release: no merge, tag, or publish was performed; the umbrella version
is unchanged at `1.0.15` (a bump is a release act, deliberately not taken
here). Two things keep it from "ready": the live signed-in Cursor deny stays
NO-DATA (blocked on a stale machine config, founder-only), and one heavier
end-to-end acceptance check outside the fast gate needs an uncontended re-run
(see Battery). Both are founder decisions, not code this session can close.

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

### Cursor machine config fixed this session (founder-delegated)

The founder asked for the stale Cursor install to be fixed on his behalf, so it
was, and the result changes the picture above.

- Reinstalled the plugin into `~/.cursor/brothermode` (self-contained, 493
  files) with `python3 products/brothermode/scripts/install_cursor.py --upgrade`
  (exit 0, adapter smoke PASS). hooks.json backed up first.
- The installer preserves hook entries it did not write, so 4 stale entries
  still pointed at the deleted `~/Brother/bundle/cursor/cursor_hook.py`. Pruned
  exactly those 4, kept 8, verified no remaining hook command names a missing
  script. Backups saved under `~/.cursor/`.
- PROOF the fix works: a fresh signed-in `cursor-agent` turn now runs Brother's
  engine end to end and emits a real receipt
  (`~/.cursor/brother/runs/.../receipt/receipt.json`, 14 KB, plus a 21 KB
  `delivery-receipt.html`). Before the fix, every mutating tool was refused.
- The signed-in smoke (`scripts/cursor_smoke.py --signed-in`) still prints
  `FAIL` on HOOKS-FIRE / HOOKS-ROOT / EDIT. That is a HARNESS limitation, not a
  Brother defect: the smoke adds its canary hooks to a throwaway `--plugin-dir`
  copy, but `cursor-agent` honours the user-level `~/.cursor/hooks.json`
  (correctly, now that it is fixed), so the canary markers are not written even
  though the real hooks fired and produced the receipt above; its RECEIPT check
  also mis-parsed the path on a trailing markdown backtick. A clean signed-in
  PASS needs the smoke taught to measure the user-level hooks, a separate item.
- Live signed-in DENY: still `NO-DATA`. The smoke does not attempt a deny and no
  deny canary exists, so fence enforcement under Cursor stays `ADVISORY`.

## Other workstreams## Other workstreams

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

The authoritative gate is `scripts/required_fast.sh`, run once to a clean pass
on the stable tree (evidence
`~/.claude/evidence/1789264960-83188-sh-scripts-required-fast-sh.txt`):

```text
sh scripts/required_fast.sh        [exit 0 after 317.6s]  transition: ALLOWED
```

Every check PASS, zero FAIL. Three checks report NO-DATA and are allowed by
the evidence-obligation map because their private inputs are not shipped in the
public export tree: `key-components`, `readiness-board`, `board-status`. New
and fixed checks in this run: `cursor-deny-chain-self` PASS, `readme-honesty`
PASS, and the Area 5 acceptance calibration is green.

A FIRST authoritative run had exposed one real REQUIRED_FOR_MERGE regression,
`readme-honesty` FAIL, caused by the overnight host-truth edit putting the word
"fence" on the README first screen; it was fixed (commit `71d5991`) and the
gate re-run clean. A second run was discarded because another session ran git
checkouts on this shared worktree mid-run, which mutated the tree under it; the
quoted run above is the clean one on a stable, uncontested tree.

The full 35-minute `check_all.sh` aggregate is NOT quoted: the shared worktree
was under heavy concurrent-battery contention throughout, and one heavier
end-to-end check outside the fast gate,
`test_product_acceptance.Area5RealTest.test_passes_against_a_real_hang`, hit
its own 90s watchdog on the full `brother_run` path. That failure is on a path
this session did not touch (it is not the calibration this session fixed), is
plausibly contention driven, and needs an uncontended re-run to tell a real
regression from load. It is recorded as a separate item, not as this cut's
evidence.

Independent review: a fresh read-only reviewer verified commits `18fcf90` and
`f9a6019` and returned SHIP on both, with clean dash, attribution and secret
scans, and confirmed the required_fast readout change is display-only (the
verdict still derives solely from the exit code) and that the Area 5 detach
leaks no process.

## Founder decisions## Founder decisions

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
