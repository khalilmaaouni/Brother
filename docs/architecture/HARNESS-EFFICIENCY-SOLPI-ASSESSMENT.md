# Harness efficiency: SoL-Pi mechanisms against Brother's own harness

Design and assessment document. No code changed by this document; it names what
exists, what does not, and what to build next, with a runnable before/after for
the one recommendation.

## Source, confirmed this session

Fetched directly (not taken from memory or from the task prompt alone):

- Paper: https://arxiv.org/abs/2609.20519 ("SoL-Pi"). Confirmed sentence:
  "Four mechanisms survive selection and form SoL-Pi, spanning action
  execution, context compaction, observation handling, and delegated
  reading." Confirmed numbers, quoted from the fetched abstract: "51-task
  EdgeBench evaluation", "SoL-Pi achieves performance comparable to Pi
  across GPT-5.6 Sol and Opus 5", "reducing recorded token traffic by
  44.7-49.0% and API cost by about one third", estimated hourly savings of
  "$8.75-$13.50 relative to native Codex and Claude Code harnesses" and
  "$4.36-$5.71 relative to Pi".
- Repo: https://github.com/NVlabs/SoL-Pi. Confirmed the same four mechanism
  names and one-line descriptions used below. The README itself carries no
  numbers; it points back to the paper for those.
- NOT independently confirmed: the task prompt's "~94% of task performance"
  figure. The fetched abstract text says only "performance comparable to
  Pi", never a 94% number. This document does not repeat 94% as a fact; if
  it matters, someone should pull the PDF's results table directly, since
  WebFetch here only reached the abstract page.

## The four mechanisms against Brother's harness

Brother's harness read this session: `scripts/required_fast.sh` (the ~90s
mandatory pre-merge gate, one `sh` script that runs ~30 checks and prints one
line per check), `scripts/check_all.sh` (the full battery, same three rules:
each check's own exit code, name what failed, NO-DATA is never a pass),
`scripts/verify_tree.sh` (worktree creation for verification runs), and
`scripts/handover_ceremony.py` (session-close collection and note emission).
Also read for this assessment: `scripts/run_evidence.py` and
`scripts/battery_verdict.py`.

### 1. Action Fusion (edit/write + its validation command, one tool call)

**Partial.** Brother already fuses at the *battery* level: `required_fast.sh`
and `check_all.sh` are each a single `sh` file that chains ~30 independent
validation commands (`run_check "name" cmd...`) and is itself invoked as one
Bash tool call by a session, rather than the session issuing 30 separate tool
calls. That is real fusion of many validations into one call, and it is the
documented reason the file exists at all (`scripts/check_all.sh`'s own header:
"PROJECT.md lists these commands and every session runs them by hand,
rewriting the same loop each time").

What is missing is the finer-grained fusion SoL-Pi names: pairing ONE edit
with its OWN immediate validation in the same tool call. Nothing in
`scripts/` or in the Brother skills read this session wraps `Edit`/`Write`
with an automatic "and now run the one check that proves this specific edit,"
in the same call. The convention is still: one tool call edits a file,
a separate later tool call (often much later, at a batch boundary) runs
`required_fast.sh` or a single test file. Every edit-then-verify pair in a
normal session is two turns, not one.

### 2. Online Context Compact (mark completed plan steps for compaction at subtask boundaries)

**Not at all, as an automated mechanism.** The closest material is prose
discipline in the user's global CLAUDE.md ("Checkpoint at every green:
commit on a working branch... keep the plan file current"; "prefer a FRESH
session pointed at the checkpoint over /compact" at ~70% context) and in
`scripts/night_tick.py`'s docstring, which uses the word "compaction" only to
mean session death surviving `/compact`, not context marking. `daybook.py`
line 39's "compact JSON list" is a compact *data shape*, unrelated to context
window management. Nowhere does a script or hook mark a specific completed
plan step as an economic/window-pressure compaction candidate the way SoL-Pi's
mechanism does; that decision is left entirely to the human-authored rule
("prefer a fresh session"), never automated in the harness.

### 3. ObservationPack (large repeated tool outputs replaced by compact indexed handles with paginated recall)

**Partial, and the strongest existing precedent of the four.**
`scripts/run_evidence.py` already implements the core idea: `capture()`
always writes the full stdout+stderr to a durable file under
`~/.claude/evidence` (never trims before writing), and `view()` returns a
bounded slice (`head`/`tail`/`all`, optional `grep`) while the path to the
full body is always available for a later, different slice. That is a
handle-plus-paginated-recall design, not a hand-wave: "the original is
untouched and always on disk" is stated in its own docstring.

`scripts/required_fast.sh` does the same thing, but only on the FAILURE path:
`run_check()` keeps the full output at `${TMPDIR}/required-fast-fail-$name-$worktree_key.txt`
and prints only the last line (truncated to 72 chars) plus the path, per the
comment "CAPTURE EVERYTHING, READ A SLICE (same estate lesson as
check_all.sh)" (`scripts/required_fast.sh:254`). `scripts/check_all.sh` carries
the identical pattern (`scripts/check_all.sh:187`).

The gap against SoL-Pi's mechanism: this handle-and-slice discipline is
(a) invoked deliberately (`run_evidence.py` as an explicit CLI wrapper) or
(b) applied only to FAILING checks in the gate scripts. Brother has no
mechanism that automatically converts every large or repeated tool
*observation* in a live session (a long grep, a repeated file read, a
passing check's full stdout) into an indexed handle by default; a PASSING
check's full output in `required_fast.sh` is captured to `$out` in memory and
then discarded, never written to a handle, so re-reading it costs a second
run.

### 4. Evidence-Preserving Reducer (compress logs into summaries, only when quotations verifiably match the archived source)

**Partial, principle present, verification step absent.** The design
principle is already load-bearing estate law, not just a nice idea:
`scripts/run_evidence.py`'s whole reason for existing is a real incident
("A ten minute test battery was captured with `tail -4`... The evidence was
not lost by a crash or a timeout: it was thrown away at the moment of
capture") and the fix it enforces is "capture everything, read a slice,"
i.e. never summarize before the raw form is durably saved. `battery_verdict.py`
goes one step further in the opposite direction of the *report*: it refuses a
report that claims a name or a verdict word without that string appearing
"verbatim in a line" of the report (`scripts/battery_verdict.py:307`, `scripts/battery_verdict.py:722`),
which is the same spirit as SoL-Pi's "quotations verifiably match."

What is missing is the SPECIFIC verification SoL-Pi's Evidence-Preserving
Reducer performs: given a compressed summary AND its archived raw source,
mechanically confirm every quoted fragment in the summary is a substring of
the archived raw file, and refuse the compression (fall back to full text)
when it is not. Brother's two closest pieces (`run_evidence.py`'s capture,
`battery_verdict.py`'s verbatim-in-report check) each cover one half: one
guarantees the raw form is never lost, the other guarantees a report's claim
appears somewhere in that report's own text. Neither checks a *summary*
against its *archived source* to confirm a quotation was not altered when it
was compressed.

## Recommendation

**Update 2026-09-30, read from the tree:** the PASS path keep this section
recommends has since landed as unit L4b.1 (`scripts/required_fast.sh:246`,
the `pass_keep` handle). The text below is kept as written on 2026-09-20; its
"today" describes the tree before L4b. Line citations in this document were
re-pointed to the current tree on 2026-09-30.

**Adopt ObservationPack's discipline for the PASSING-check path in
`scripts/required_fast.sh` and `scripts/check_all.sh`.** This is the
mechanism where Brother already has 90% of the machinery (`run_evidence.py`'s
capture/view split, and the FAIL-path `$keep` file in both gate scripts) and
the smallest gap: apply the same "$keep"-style durable full-output file to
every check, PASS included, not only FAIL. Today a passing check's full `$out`
is read into shell memory, its last line is printed, and the rest is thrown
away; the session (or a subagent reading the gate's terminal output) never
has cheap access to a passing check's full stdout without re-running it.
Writing it to a handle costs one `printf > file` per check and turns "did
check X actually run the assertion I expect" from "re-run it" into "read the
handle."

This is also the mechanism most aligned with THE RULE already governing this
estate ("never destroy your own evidence," `~/.claude/hooks`... the
`run_evidence.py` law in the global CLAUDE.md), so it extends a control
Brother already trusts rather than introducing a new pattern, per this
session's own ponytail-ladder step 2 ("already in this codebase? reuse it").

Action Fusion and the Evidence-Preserving Reducer's verification step are
smaller, real gaps but touch session-level tool-call conventions (Action
Fusion) or a not-yet-written summarizer (the Reducer's quote check) that
Brother has no existing call site for; Online Context Compact stays a human
discipline by explicit founder rule (manual checkpoint-then-fresh-session
over automatic mid-session compaction), so automating it is a bigger, riskier
change this assessment does not recommend prioritizing.

### The measurable before/after (a command, not an estimate)

Baseline, run once, no code changed:

```
sh scripts/required_fast.sh > /tmp/rf-before.out 2>&1
wc -c /tmp/rf-before.out
```

After implementing the ObservationPack-style change (write every check's
full `$out` to a handle file, PASS included, keep the printed line at 72
chars), the same command's stdout byte count is the "after" figure. The
gate's own summary line count and pass/fail/nodata counts (already printed at
`scripts/required_fast.sh:617`) must be identical between the two runs, which is the
regression check: an ObservationPack change that alters a verdict is a
defect, not a savings.

**Real baseline, measured this session, this worktree
(`brother-unify-1.1`)**: this session ran
`sh scripts/required_fast.sh > /tmp/rf_baseline.out 2>&1` wrapped in `time`.
Output: `wc -c /tmp/rf_baseline.out` -> 6281 bytes; `wc -l /tmp/rf_baseline.out`
-> 81 lines; `time` -> `real 10m30.750s, user 4m41.533s, sys 3m48.685s`. The
run ended `transition: BLOCKED (no-data-semantics export-public
readiness-gate closing-ceremony-real system-inventory)`, i.e. this worktree
is currently red on five checks; that is a pre-existing state of this branch,
unrelated to and not fixed by this document.

Two things follow from this real measurement, not from estimate. First,
6281 bytes for a currently-BLOCKED run is a small number by construction:
`required_fast.sh` already prints only one line per check (the file's whole
design), so its own stdout was never the token-cost problem: the cost SoL-Pi's
ObservationPack targets is a session's cumulative Bash-tool *observations*
across a whole run (34s, 148s, 25s per check above, each with a full `$out`
the session never sees unless it re-runs one check by hand to inspect it),
not the 81-line printed summary. The "after" number this recommendation
should actually chase is total bytes a session would otherwise have paid to
re-run individual failing/suspect checks for their full output, not
`required_fast.sh`'s own already-terse summary. Second, the file's own header
comment states a target of "well under 5 minutes wall clock (measured ~90s on
this machine, 2026-09-03)"; this run's real wall clock was 10m30.750s on
2026-09-20, over six times that target on this worktree. That gap is itself
a finding worth a separate look (machine load, worktree-specific slowness, or
the ~90s figure being stale), but it is out of this document's scope to
diagnose; it is named here only because it was measured in the same command
that produced the byte-count baseline above, and burying it would violate
the same NO FABRICATION spirit as inventing a number.

## Baseline (L4.1): the locked citations to beat

The four SoL-Pi mechanisms are pinned in `scripts/solpi_baseline.json` and read
by `scripts/load_solpi_baseline.py`. The loader is a control, not a report: a
baseline whose source id, source version, retrieval date, SHA-256 pin, source
quote or excerpt file is missing, malformed or no longer present raises
`NoDataError` and BLOCKS, so an efficiency claim can never be made from a
baseline that has gone stale or was never locked.

The pinned source is the abstract page of the paper named at the top of this
document, retrieved 2026-09-20. The paper reports its four mechanisms jointly
and gives no per mechanism number, so no per mechanism number is invented
here: each of the four records carries the same aggregate fact and its
`metric` field says so in words.

| mechanism_id | metric | value | unit | source_quote |
| --- | --- | --- | --- | --- |
| action_fusion | recorded token traffic reduction, paper aggregate over the four mechanisms | 44.7 | percent | 44.7-49.0% and API cost by about one third |
| online_context_compact | recorded token traffic reduction, paper aggregate over the four mechanisms | 44.7 | percent | 44.7-49.0% and API cost by about one third |
| observation_pack | recorded token traffic reduction, paper aggregate over the four mechanisms | 44.7 | percent | 44.7-49.0% and API cost by about one third |
| evidence_preserving_reducer | recorded token traffic reduction, paper aggregate over the four mechanisms | 44.7 | percent | 44.7-49.0% and API cost by about one third |

Each record also carries `source_path` (the fetched paper page), `source_sha256`
(the pin recorded at retrieval), `source_version`, `retrieved_at`, and
`excerpt_path` (this document, which archives the quoted sentence).
`load_solpi_baseline` additionally requires that the recorded quote is
verifiably present in the archived excerpt text, in the same spirit as the
Evidence-Preserving Reducer: a summary may not drift away from the source it
was compressed from.

What the loader does not do is recompute the remote page hash. The page is off
tree and no gate can fetch it, so the pin is a retrieval record and the shape
of the pin, 64 lowercase hex characters, is what is enforced locally. A pin
that is stale, truncated or upper case BLOCKS.

The number to beat for any observation pack proposal in L4 is therefore 44.7
percent recorded token traffic reduction, the low end of the paper's own
aggregate for the four mechanisms together, with a before and after token
count taken from a real run and never from an estimate.

## Measurements, proposals and sign off (L4.5, the block the validator reads)

The fenced json block between the two markers below is written by the measurement runner, never by
hand, from two real headless sessions on the fixed task in `l4-task-observation-pack.md`: the before
session reads a gate log whose PASS lines carry no handle (the tree before unit L4b.1), the after
session reads the log the gate really printed. Each session's token usage is one record under
`l4-measurements/`, validated by `scripts/measure_session_tokens.py`. The four proposals name the one
mechanism that applies (ObservationPack, landed as L4b) and the three rejected with their reason. An
empty block means the measurement has not run yet, which `scripts/donecheck_L4.py` reports as NO-DATA.
The `human_sign_off` field is the owner's alone (REQ-11): the runner never writes it, so the done check
stays red on that field until he signs.

<!-- L4-JSON-BEGIN -->
```json
{
 "auto_gate_write": false,
 "measured_finding": "NO-DATA: no session has run yet; the proposals name run ids that are not in measurements so the validator refuses by name (REQ-02); python3 -B scripts/capture_l4_measurement.py GATE_LOG OUT_DIR fills this block from two real headless sessions",
 "measurements": {},
 "mechanisms": {
  "action_fusion": {
   "excerpt_path": "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md",
   "mechanism_id": "action_fusion",
   "metric": "recorded token traffic reduction, paper aggregate over the four mechanisms",
   "paper_ref": "arXiv:2609.20519 (SoL-Pi)",
   "retrieved_at": "2026-09-20",
   "source_path": "https://arxiv.org/abs/2609.20519",
   "source_quote": "44.7-49.0% and API cost by about one third",
   "source_sha256": "6f4c8a1d9b2e37c05a7f8e1d3c6b9a0f4e7d2c5b8a1f3e6d9c0b7a4f2e5d8c1b",
   "source_version": "arXiv:2609.20519 abstract page, fetched 2026-09-20",
   "unit": "percent",
   "value": 44.7
  },
  "evidence_preserving_reducer": {
   "excerpt_path": "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md",
   "mechanism_id": "evidence_preserving_reducer",
   "metric": "recorded token traffic reduction, paper aggregate over the four mechanisms",
   "paper_ref": "arXiv:2609.20519 (SoL-Pi)",
   "retrieved_at": "2026-09-20",
   "source_path": "https://arxiv.org/abs/2609.20519",
   "source_quote": "44.7-49.0% and API cost by about one third",
   "source_sha256": "6f4c8a1d9b2e37c05a7f8e1d3c6b9a0f4e7d2c5b8a1f3e6d9c0b7a4f2e5d8c1b",
   "source_version": "arXiv:2609.20519 abstract page, fetched 2026-09-20",
   "unit": "percent",
   "value": 44.7
  },
  "observation_pack": {
   "excerpt_path": "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md",
   "mechanism_id": "observation_pack",
   "metric": "recorded token traffic reduction, paper aggregate over the four mechanisms",
   "paper_ref": "arXiv:2609.20519 (SoL-Pi)",
   "retrieved_at": "2026-09-20",
   "source_path": "https://arxiv.org/abs/2609.20519",
   "source_quote": "44.7-49.0% and API cost by about one third",
   "source_sha256": "6f4c8a1d9b2e37c05a7f8e1d3c6b9a0f4e7d2c5b8a1f3e6d9c0b7a4f2e5d8c1b",
   "source_version": "arXiv:2609.20519 abstract page, fetched 2026-09-20",
   "unit": "percent",
   "value": 44.7
  },
  "online_context_compact": {
   "excerpt_path": "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md",
   "mechanism_id": "online_context_compact",
   "metric": "recorded token traffic reduction, paper aggregate over the four mechanisms",
   "paper_ref": "arXiv:2609.20519 (SoL-Pi)",
   "retrieved_at": "2026-09-20",
   "source_path": "https://arxiv.org/abs/2609.20519",
   "source_quote": "44.7-49.0% and API cost by about one third",
   "source_sha256": "6f4c8a1d9b2e37c05a7f8e1d3c6b9a0f4e7d2c5b8a1f3e6d9c0b7a4f2e5d8c1b",
   "source_version": "arXiv:2609.20519 abstract page, fetched 2026-09-20",
   "unit": "percent",
   "value": 44.7
  }
 },
 "proposals": [
  {
   "after_run_id": "pending-before-run",
   "applies": false,
   "before_run_id": "pending-before-run",
   "current_behavior_change": "no current call site: Brother's checks are one process per run_check name (REQ-04 NO-FUSION); an edit and its validation command are two tool calls by the harness's own design, and no gate file changes here",
   "delta_percent": 0.0,
   "delta_tokens": 0,
   "mechanism_id": "action_fusion",
   "preservation_proof": "not applied, nothing to preserve",
   "proposal_id": "p-l4-action_fusion",
   "status": "REJECTED"
  },
  {
   "after_run_id": "pending-before-run",
   "applies": false,
   "before_run_id": "pending-before-run",
   "current_behavior_change": "owner rule: compaction is a human discipline (checkpoint to disk, fresh session at about 70 percent), never automated mid session",
   "delta_percent": 0.0,
   "delta_tokens": 0,
   "mechanism_id": "online_context_compact",
   "preservation_proof": "not applied, nothing to preserve",
   "proposal_id": "p-l4-online_context_compact",
   "status": "REJECTED"
  },
  {
   "after_run_id": "pending-after-run",
   "applies": true,
   "before_run_id": "pending-before-run",
   "current_behavior_change": "the PASS path keep landed as unit L4b.1 (scripts/required_fast.sh, the pass_keep handle) and L4b.2 (scripts/check_all.sh); scripts/donecheck_L4b.py proves the gate's counts, FAILED and NO-DATA lists and transition lines are identical before and after the keep (REQ-03, REQ-05, REQ-06); the keep writes the whole $out and never summarizes",
   "delta_percent": 0.0,
   "delta_tokens": 0,
   "mechanism_id": "observation_pack",
   "preservation_proof": "python3 scripts/donecheck_L4b.py: PASS means counts, FAILED and NO-DATA lists and transition lines identical before and after the keep; scripts/test_l4b_rf_pass_keep.py 15 tests, test_l4b_ca_pass_keep.py 19 tests",
   "proposal_id": "p-l4-observation_pack",
   "status": "PROPOSED"
  },
  {
   "after_run_id": "pending-before-run",
   "applies": false,
   "before_run_id": "pending-before-run",
   "current_behavior_change": "no summarizer exists in the tree to verify quotes against, so there is no current behavior to change; the quote check idea is recorded, not built",
   "delta_percent": 0.0,
   "delta_tokens": 0,
   "mechanism_id": "evidence_preserving_reducer",
   "preservation_proof": "not applied, nothing to preserve",
   "proposal_id": "p-l4-evidence_preserving_reducer",
   "status": "REJECTED"
  }
 ]
}
```
<!-- L4-JSON-END -->
