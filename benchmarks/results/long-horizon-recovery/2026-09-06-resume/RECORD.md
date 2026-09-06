# Long-Horizon Recovery: the temporal arm resumed, 2026-09-06, corrected after audit

Status: **PARTIAL**, superseding this same directory's own first version of
this record. An opus evidence audit
(`~/.claude/evidence/audit-383-lhr-2026-09-06.md`) of that first version
found its PASS held only with seven named limits and was not mergeable as
written: two measures overclaimed what the driver actually checked, one
backdated a lease the engine did not need backdated, and drift detection was
never really exercised. This record is a full rerun with a corrected
`driver.py` (the 2026-09-05-checkpoint's own driver, lines 1-~200, is
untouched; every fix below sits in the resume phases added on top of it),
never a patch of the old numbers.

## Route taken (unchanged from the first version)

The 2026-09-05-checkpoint's own fixture repository and runs root were built
with `tempfile.mkdtemp()` inside that session and do not survive between
sessions. The honest route is to **replay** the checkpoint's own frozen
phases (build the fixture, start `brother_run.py`, wait for A1 to integrate,
wait for A2 to be durably claimed, capture canonical state, SIGKILL the
process group, capture the post-kill continuity capsule) into a **fresh**
mkdtemp using the identical stub seam, then run the resume phases against
that fresh crash. `driver.py` beside this record is that replay.

## The seven fixes, each keyed to the audit's own finding

1. **Killed run captured before it can be overwritten.** The audit: "the
   killed run's own `run.log` was not captured and did not survive: the
   resumed process rewrote `run.log` in place." Cause, read from the code:
   `brother_run.py`'s `RunLog._flush` opens `run.log` with `"w"` on every
   invocation, and a fresh process starts with an empty line buffer, so the
   resumed process's first flush replaces the killed run's log from line 1.
   Fix: `driver.py` now `shutil.copytree`s the whole run directory into
   `killed_run/` immediately after the kill, before the resume can touch
   it. Verified in this rerun: `killed_run/run.log` ends mid-run ("round 1
   done, 1 of 2 piece(s) finished, 1 to go"), while `resumed_run/run.log`
   starts fresh with the resume's own first line. `journal.jsonl` is
   append-only (`journal.append` uses `O_APPEND`) so it never needed this
   fix, but is captured into `killed_run/` too for a complete pre-kill
   snapshot.

2. **No lease backdating.** The audit: "the crash-detection path was
   bypassed... the harness backdated the lease... the backdating was not
   even necessary for a same-host dead pid." Cause, read from
   `scripts/claim_store.py` near line 289: `dead_reason()` reclaims a claim
   when EITHER its lease expired OR its owning pid is gone on this host,
   checked as an OR, never gated on the lease first. Fix: `driver.py` no
   longer calls `_edit_expires_at`; it does nothing at all between the
   drift commit and the resume beyond waiting on the drift commit's own
   git operations. Verified in this rerun, with no wait and no lease edit:
   `resumed_run/run.log` reads `"abandoned   A2         owner pid 20525 is
   dead on this host, with 1138s still left on the lease while still in
   state cl[aimed]"`, and `resumed_run/journal.jsonl` carries a
   `claim.acquired` event for A2 with `"reclaimed_from": "brother-run-20525"`
   (the dead owner) and a fresh attempt number 2. The dead-pid path fires
   unaided, exactly as the audit said it should.

3. **False claims compared against git's own merges, and a report-parse
   miss is NO-DATA.** The audit: "the genuinely independent comparator
   exists and was not used: `product_acceptance.py:778-785` (area 11)
   compares the report to `_merge_ids()`, git's own merges... a report-parse
   miss turns into an empty list, i.e. a PASS from no input." Fix:
   `false_claims_after_recovery` is now `sorted(reported_ids -
   set(_merge_ids(repo)))`, never a comparison against `claims.json`; a
   report that carries neither a per-unit "verified by" line nor an
   "integrated (N):" header now yields the string `NO-DATA: could not parse
   an integrated-units line from the delivery report` instead of `[]`. This
   rerun's report parsed fine (`reported={A1, A2}`, `git_merges={A1, A2}`),
   so this path was not exercised as NO-DATA; the exact code that would
   produce it is in `driver.py` and covered by `scripts/test_lhr_resume_record.py`.
   Second half of the same finding: audit item D named a shape the old
   measure could not see, a unit printed as "verified by" while its own
   verdict reads NO-DATA. `driver.py` now parses the report's per-unit
   verdict lines and flags exactly that. **It fired in this rerun**:
   `false_claims_after_recovery` reads
   `["A2 (printed as verified by, but its own verdict reads NO-DATA)"]`.
   This is the SAME pre-existing, unrelated-to-crash-recovery property the
   first version's own `known_limit_found` field named (A2's `done_check`,
   `test -f two.txt`, still passes with A1's change reverted, so the
   engine's own independence check cannot credit it) -- now surfaced as a
   named false-claims entry per the directive's own words, rather than a
   separate prose paragraph the measure could not see.

4. **`recovery_time_seconds` now measures recovery, not acknowledgment.**
   The audit: "times Popen to the first stdout line matching
   `"resuming"`/`"unfinished run"`. That is time to acknowledgment... not
   'recovery'." Fix: `recovery_time_seconds` is now the time from issuing
   the resume subprocess to A2's own `integrate.merged` journal event (read
   back from `resumed_run/journal.jsonl`, which survives the resume, unlike
   `run.log`) -- the moment the resumed unit's own check re-ran and passed
   on the new canonical. The old acknowledgment-line measurement is kept as
   a separate field, `resume_acknowledgment_seconds`. This rerun:
   `recovery_time_seconds = 2.686s`, `resume_acknowledgment_seconds =
   0.055s` -- a 49x difference between "the process printed a line" and
   "the work is actually verified again."

5. **`lost_decisions` now compares the decision record by content.** The
   audit: "the only decision the run recorded, the intent resolution, was
   RE-TAKEN after the resume by default... and the measure cannot see it."
   Fix: `driver.py` now reads the `"brother_run: intent resolved: ..."`
   line from `killed_run/run.log` (pre-kill) and from `resumed_run/run.log`
   (post-resume) and reports `lost_decisions = 1` when the SAME kind of
   resolution line appears in both -- proof that the decision was taken
   again from scratch, not carried forward, because there is nothing in
   this engine that persists an intent resolution across process
   invocations. This rerun: **it fired**. `lost_decisions = 1`.
   `pre_kill_intent_lines` and `post_resume_intent_lines` in `result.json`
   both read `"brother_run: intent resolved: chose 'Proceed as decomposed',
   recorded by brother_run (recorded default: the top-ranked option)"` --
   textually identical, independently re-derived, with no record of the
   first decision surviving anywhere the resumed process reads from.

6. **Drift detection is honestly NO-DATA.** The audit: "nothing the engine
   printed or journalled names the repository change... what is proven is
   drift TOLERANCE, not detection." Fix: `driver.py` searches
   `resumed_run/run.log` for the drift commit's own hash or literal
   "changed since" wording, EXCLUDING the exact readback shapes the audit
   named (`"Rollback: %s"` -- `brother_run.py`'s own HEAD readback at
   intent-screen time -- and `"canonical revision before/after:"`), because
   those print whatever HEAD happens to be at render time whether or not
   anything drifted. Writing this check surfaced a SECOND false positive
   the audit had no occasion to find: `journal.jsonl`'s own
   `integrate.merged` event carries `"onto"`/`"canonical"` fields that are
   exactly as mechanical (every merge logs whatever it built on), so the
   journal is excluded from this search entirely; only `run.log`'s free
   text is searched. Result in this rerun, honestly: `drift_detection =
   "NO-DATA: no engine output or journal event names the repository
   change. The run proves drift tolerance, that the resumed unit built on
   the new HEAD, not drift detection."` -- unchanged from what the audit
   predicted, now backed by a check that survived two rounds of trying to
   break it rather than a single grep run by hand.

7. **The crash lands mid-flight, not 20ms after the claim.** The audit:
   "A2 was claimed at 1788651042.0256 and SIGKILLed at 1788651042.045, a 20
   ms exposure window with no worker ever spawned. Zero repeated work was
   near-certain by construction." Fix: `driver.py` now sleeps 1.0 second
   after observing A2's claim before capturing canonical state and killing,
   landing solidly inside `slow_model_body`'s 2.0 second sleep. This
   rerun's `exposure_window_seconds = 1.008` (claim to kill), with the
   worker genuinely started and mid-sleep when SIGKILL landed.
   `repeated_work = 0` in this rerun means what it says: the engine did not
   redo the work, not that redoing it was never possible.

## Limits named by the 2026-09-06 audit

The seven sentences the audit required, adjusted to what this rerun (with
the corrected driver) actually shows:

1. The killed run's own `run.log` and `journal.jsonl` are now captured into
   `killed_run/` before the resume can overwrite `run.log`, closing the gap
   the audit found in the first version of this record.
2. Repository drift detection is NO-DATA: no engine output or journal event
   names the repository change. The run proves drift tolerance, that the
   resumed unit built on the new HEAD, not drift detection.
3. A2's reclaim now fires on the dead owner's pid, with no lease backdating
   and no wait: `resumed_run/run.log` carries `"abandoned A2 owner pid
   ... is dead on this host"` and `resumed_run/journal.jsonl` carries a
   `claim.acquired` event with `"reclaimed_from"` set to the dead owner, so
   the crash-detection path IS exercised in this rerun, unlike the first
   version.
4. `human_interventions = 1` is still the driver's own counter, not an
   engine record, but it now counts exactly the one documented resume
   re-invocation and nothing else: no lease backdating and no other
   claim-store write happens anywhere in this driver, so there is nothing
   further the gauntlet's fairness note would require counting.
5. `recovery_time_seconds = 2.686s` is time to the resumed unit's own
   `integrate.merged` journal event, the recovered and re-verified state;
   time to the resume's first acknowledging stdout line is reported
   separately as `resume_acknowledgment_seconds = 0.055s`.
6. `false_claims_after_recovery` is compared against git's own merges
   (`_merge_ids`), not `claims.json`, and a report-parse miss would read
   NO-DATA rather than an empty list; in this rerun it also flags, by name,
   the one unit (A2) the report prints as "verified by" while its own
   per-unit verdict reads NO-DATA.
7. The crash landed 1.008 seconds after A2 was claimed, with the worker
   genuinely spawned and mid-sleep inside its 2.0 second window, so
   `repeated_work = 0` reflects that repeating the work was actually
   possible, not that the window was too narrow for it to happen.

## Pinned harness

Executed from the `wbs/lhr-resume-2026-09-06` worktree. Nothing under
`scripts/` was modified to make this run happen; `driver.py` is the whole
driver, same as the checkpoint's own for phases 1-7.

## Command (documented, copied verbatim)

Start (replay) and resume are both the identical bare invocation:

```
<PYTHON3> <BROTHER_SCRIPTS>/brother_run.py "two files exist, the second after the first" --cwd <SCRATCH_ROOT>/lhr-fixture-.../repo --runs-root <SCRATCH_ROOT>/lhr-fixture-...
```

The resume is copied verbatim from 2026-09-05-checkpoint/MANIFEST.json's own
`"resume_command"` field (a bare re-invocation, same outcome sentence, no
`--resume`, no `--continue`), per the gauntlet's own `fairness_notes` ("the
operator re-runs the same sentence... anything else... is counted and
named") and matching the gauntlet's own "context loss" seeded condition.

## Fault sequence, phase by phase

1. **start real work**: `brother_run.py` launched on the two-unit outcome
   (A1 create `one.txt`, A2 depends on A1, creates `two.txt`), stub
   decomposer/model exactly as the checkpoint used.
2. **checkpoint**: A1 reached `DONE` (first integration); A2 was durably
   claimed; the driver then waits **1.0 second** (fix 7) so the worker is
   genuinely mid-flight inside its 2.0 second sleep before capturing
   canonical state (`canonical_state_before_kill/`).
3. **kill process**: `SIGKILL` of the whole process group, landing with A2
   `state: claimed`. `killed_run/` (fix 1) and
   `continuity_capsule_after_kill.json` / `continuity_screen_after_kill.txt`
   captured immediately after.
4. **wait**: a real 60 second wall-clock sleep, named as exactly that (the
   24-72 hour arm of workload family 8 stays NO-DATA; see "Route taken").
5. **change repository state**: one commit, `repo-drift-2026-09-06.txt`,
   landed on the fixture's canonical branch. `continuity_capsule_before_resume.json`,
   re-read live from disk after this commit, already reports
   `canonical_revision` equal to the drift commit
   (`drift_seen_live_before_resume: true`).
6. **resume**: the bare re-invocation above, with no lease backdating and
   no wait (fix 2). First stdout line: `"brother_run: an unfinished run
   already covers 'two files exist, the second after the first'; resuming
   it instead of starting a new one"` (`receipt.txt`).
7. **detect drift**: NO-DATA (fix 6, see "Limits" above). What IS proven:
   `claims.json` after resume shows A2 reclaimed from the dead owner
   (`reclaimed_from` set, new attempt number 2); `final_git_log.txt` shows
   A2's integration merge (`2068204`) landing ON TOP OF the drift commit
   (`408bd53`), not before it -- the resumed unit ran against the changed
   tree.
8. **recover canonical objective**: `continuity_screen_after_resume.txt`
   answers the win condition's checklist from disk; `SAFE NEXT ACTION`:
   "done: every unit in this run is integrated; nothing is left to
   resume."
9. **continue safely**: the run exits 0, the delivery report lists both
   units integrated, and a receipt is written to disk (`receipt.txt`).

## Measures

| Measure | Value | How read |
|---|---|---|
| repeated work | **0** | `final_git_log.txt`: A1 merged exactly once (`4cd78db`), A2 exactly once (`2068204`); `_merge_ids()` returns exactly `["A1", "A2"]`, no duplicates. This time the exposure window was 1.008s, not 20ms (fix 7), so a zero here means something. |
| lost decisions | **1** | The intent-resolution line appears, textually identical, in both `killed_run/run.log` (pre-kill) and `resumed_run/run.log` (post-resume): re-taken from scratch, not carried forward (fix 5). |
| lost evidence | **0** | A1's attempt-1 receipt directory (`attempts/A1-.../attempt-1/{claim,engine_output,tree_state}`) is present in `canonical_state_before_kill/` and unchanged after the resume. Scope limit, carried from the first version: this only checks the directory exists, not its contents. |
| wrong resumed state | **false** (i.e. correct) | A2's post-resume `evidence.canonical_rev` descends from the drift commit (`git merge-base --is-ancestor`, exit 0), and A1's claim is untouched. `result.json`: `"ran_on_drifted_tree": true`, `"wrong_resumed_state": false`. |
| human interventions | **1** | The one documented resume re-invocation, and nothing else: no lease backdating (fix 2), no other claim-store write of any kind. |
| recovery time | **2.686 seconds** | Time from issuing the resume subprocess to A2's own `integrate.merged` journal event (fix 4). Acknowledgment time (first stdout line matching the crash-acknowledging pattern) is reported separately: `resume_acknowledgment_seconds = 0.055s`. |
| false claims after recovery | **1 named entry** | Compared against git's own merges (`_merge_ids`), never `claims.json` (fix 3): both units the report names as integrated (`A1`, `A2`) DID merge, so no unit is falsely claimed integrated. But A2 is printed as "verified by" while its own per-unit verdict reads NO-DATA, flagged by name: `["A2 (printed as verified by, but its own verdict reads NO-DATA)"]`. |

## Roadmap

Same finding as the 2026-09-05-checkpoint and the first version of this
record: `docs/plan/READINESS-ROADMAP-2026-08-29.json` carries no row scoped
to `benchmarks/gauntlets/long-horizon-recovery.json` itself. No roadmap row
was added or edited by this lane.

## Files

`driver.py` is the whole driver (replay phases 1-7 unchanged from the
checkpoint's own, resume phases 8+ carrying the seven audit fixes, each
marked "AUDIT FIX" at its site). `driver.log` is this run's own timeline.
`canonical_state_before_kill/` mirrors the checkpoint's own capture, for
THIS run's fresh crash, now taken 1.0 second into the worker's sleep rather
than 20ms after the claim. `killed_run/` (new since the first version of
this record) is the run directory exactly as SIGKILL left it, copied before
the resume could overwrite `run.log`. `continuity_capsule_after_kill.json`
/ `continuity_screen_after_kill.txt` / `continuity_capsule_before_resume.json`
/ `continuity_capsule_after_resume.json` / `continuity_screen_after_resume.txt`
are `continuity.py`'s own reads at each named moment. `receipt.txt` is the
resume subprocess's full stdout/stderr. `resumed_run/` holds the final
`claims.json`, `run.log`, `journal.jsonl`, `target.json`, `capsule.json` and
the Work document. `final_git_log.txt` is `git log --oneline --graph` of the
fixture repository at the end of the run. `result.json` is the driver's own
structured summary, including every measure and fix above, plus the raw
evidence lines (`reclaim_evidence_run_log`, `reclaim_evidence_journal`,
`pre_kill_intent_lines`, `post_resume_intent_lines`) this record quotes
from. `scripts/test_lhr_resume_record.py` validates this directory's
`MANIFEST.json` against the files on disk the same way
`scripts/test_lhr_checkpoint.py` already validates the checkpoint's.
Every absolute scratch path, this machine's hostname, the OS temp
directory, and the interpreter path were replaced with bracketed
placeholders throughout this directory before it was committed;
`MANIFEST.json` beside this file lists every artefact with its sha256 as
committed.

## Verdict

**PARTIAL.** The crash-during-execution arm (workload family 7) is now
measured honestly against the audit's own seven findings, with two real
findings surfacing in the rerun that the old driver could not see
(`lost_decisions = 1`, one false-claims entry for A2's printed-but-NO-DATA
shape) and one (drift detection) confirmed NO-DATA rather than a false
readback match. The 24-72 hour arm of workload family 8 stays explicitly
NO-DATA on this fixture-to-fixture measurement, exactly as
`benchmarks/gauntlets/long-horizon-recovery.json`'s own scoring rubric
requires. This record does not claim PASS: it claims that every measure the
directive named is now actually measured, with its reason stated wherever
it reads NO-DATA or reports a limit.


## Spec status, moved out of the frozen body

The temporal arm (workload family 7) was run and resumed on 2026-09-06;
PR 383 carried the first version of this record and was corrected to
PARTIAL after the audit above. That run status briefly lived in
benchmarks/gauntlets/long-horizon-recovery.json's own "status" field,
which moved the spec's pinned corpus_sha1 (scripts/gauntlet_frozen.py hashes
the whole body for a "none: cases are generated by the runner" corpus) and
tripped scripts/test_gauntlet_frozen.py. The spec's "status" field is
restored to "SPECIFIED, NOT YET RUN", its exact pre-run wording, so the
frozen body no longer carries run status by design: this record, not the
spec, is where a run of this gauntlet is stated.
