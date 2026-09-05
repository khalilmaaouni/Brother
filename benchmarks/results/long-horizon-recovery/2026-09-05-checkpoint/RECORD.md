# Long-Horizon Recovery: morning partial run, 2026-09-05

Status: **PARTIAL, PENDING TEMPORAL ARM**, per `benchmarks/gauntlets/long-horizon-recovery.json`
and section 37 of `~/.claude/evidence/MORNING-STEERING-2026-09-05.md`:
"The full 24-72 hour test cannot finish before 10:00. Do not fake it.
Instead: start run, capture canonical state, perform first integration,
inject interruption, record exact recovery artifacts, freeze state. Resume
later after the required gap." This record follows that phase list exactly,
in order, and stops there.

## Pinned harness

Worktree `wbs/lhr-start` off `hub/main` at `a4f55007e` (`git log --oneline -1
hub/main`), clean before the run. This record is a checkpoint of a run
against a throwaway fixture repository, not a change to the estate's own
tree; nothing under `scripts/` was modified to make this run happen.

## Command

The real engine, `scripts/brother_run.py`, invoked through `scripts/door.py`,
`scripts/loop_bridge.py` and `scripts/integrate.py` exactly as
`scripts/product_acceptance.py`'s own docstring names as the public entry
point. The decomposer and the model worker are stubs pointed at by
`DOOR_MODEL_CMD`/`MODEL_WORKER_CMD`, the exact seam
`scripts/test_brother_run.py` and `scripts/product_acceptance.py` already use
to stand in for the real `claude` CLI (`TWO_DEPENDENT_DECOMPOSER` and
`slow_model_body(2.0, "two.txt")` from `product_acceptance.py`, reused
verbatim, not reimplemented). This is an honest choice, stated plainly: this
run exercises the ENGINE's crash/recovery mechanics (claims, journal,
continuity capsule, integration), not a real model's output quality, and no
credentialed model call was available to this lane. `driver.py` (frozen
alongside this record) is the whole driver.

```
<PYTHON3> <BROTHER_SCRIPTS>/brother_run.py "two files exist, the second after the first" --cwd <THROWAWAY_REPO> --runs-root <SCRATCH_ROOT>
```

Two units: A1 (create `one.txt`, no deps), A2 (create `two.txt`, deps
`[A1]`). The stub model sleeps 2s before writing `two.txt` specifically so an
external kill lands while A2 is durably claimed and its owner is still
"alive" from the claim store's point of view, mirroring
`scripts/product_acceptance.py::_run_and_kill_mid_second_unit`, driven here
by hand with full pid/timestamp logging rather than through that helper, so
the driver log carries what the raw-artifacts list requires.

## Injected fault

Workload family 7, "crash during execution". Followed
`benchmarks/gauntlets/long-horizon-recovery.json`'s own
`seeded_conditions_note` verbatim: **"a crash is a SIGKILL of the whole run's
process group, the shape
docs/plan/runs/live-autonomous-adversity-2026-09-04/ already drove"**.
`driver.py` started `brother_run.py` in its own process group
(`os.setsid`), waited for A1 to reach `DONE` (the first integration), waited
for A2 to be durably claimed, then `os.killpg(pgid, SIGKILL)`'d the whole
group. `driver.log` (frozen, scrubbed of the scratch paths and this
machine's hostname) carries the pid, the pgid and the kill timestamp.

## Observed recovery state (captured, not yet exercised)

- A1 reached `DONE` before the kill: **the first integration happened**
  (`killed_run/run.log`; `killed_run/claims.json` shows `A1` `state: done`,
  `evidence.exit_code: 0`, `evidence.files_changed: ["one.txt"]`).
- A2 was claimed (`state: claimed`, `owner: brother-run-<pid>`, lease
  `expires_at` ~1200s in the future) at kill time and remained `claimed`
  after the kill, i.e. **the crash landed mid-unit as staged**
  (`killed_run/claims.json`, `canonical_state_before_kill/claims.json`
  which is the same state captured immediately before the kill for
  comparison).
- The continuity capsule, regenerated fresh from disk AFTER the kill via
  `python3 scripts/continuity.py <run_dir> --json` (raw_artifacts.contents:
  "the continuity capsule as printed after the kill"), correctly buckets A1
  as `integrated`, A2 as `abandoned` (dead owner, live lease), and names a
  safe `next_action`: "resume: A2's claim expired while still marked
  claimed (owner pid ... is dead on this host, ... a resume can safely
  re-run it)" (`continuity_capsule_after_kill.json`,
  `continuity_screen_after_kill.txt`).

This is the mechanism `docs/plan/READINESS-ROADMAP-2026-08-29.json` row S13
("Strengthen the Continuity Capsule to the fifteen items zone 3 names")
already proved on a different fixture (E73); this record adds one more real
kill and freezes it for a resume that this morning's clock cannot honor.

## What was NOT done, and why

- **Not resumed.** Resuming now would prove nothing about the 24-72 hour
  drift arm (workload family 8) and the gauntlet's own scoring rubric says
  "The 24 to 72 hour arm of family 8 is reported NO-DATA until a harness
  exists for it. It is never scored from the short-crash arm." No `--resume`
  or bare re-invocation was run against this fixture; `killed_run/` is
  exactly what SIGKILL left on disk.
- **No repository drift commit applied.** The spec's `fairness_notes` for
  "starting repository" calls for "one recorded drift commit applied at the
  same point in every arm" once a real temporal arm exists; this morning's
  run has no second arm to apply it against yet, so none was added. NO-DATA,
  named here rather than invented.
- **HUMAN INTERVENTIONS and SAFE UNWATCHED DURATION metrics**: per the
  spec, `NO INSTRUMENT YET` on this estate; unaffected by this run and not
  claimed here.
- **RECOVERY TIME metric**: still `partial` per the spec
  (`scripts/test_crash_resume.py` proves recovery happens, nothing reports
  a number). This record's own kill-to-resume interval cannot be measured
  either, because there is no resume yet; the elapsed time from
  `driver.log` is between the crash and the freeze, not a recovery.

## Roadmap

Checked `docs/plan/READINESS-ROADMAP-2026-08-29.json` for a row covering
this gauntlet by name (`grep -n "long-horizon\|Long-Horizon\|long horizon"`).
Two hits, neither on point: `P13`'s `role` field uses the phrase
"the long-horizon gauntlet (trusted-delegation workstream F)" for an
unrelated data-science leakage/seed fixture, and one `category` field
mentions "Verified Long-Horizon Delegation" as a positioning tagline. Of the
five gauntlet rows S9/S11/S12/S14 (Delegation Truth, Acceptance Time, memory
recurrence, Japanese, matching sections 20.1/20.3/20.4/20.5 by name), none
is titled 20.2. S10 covers the related but distinct "Safe Unwatched Time"
metric (zone 2). S13 covers the continuity capsule's item list (zone 3),
which this run's capsule output is evidence toward, but S13 is scoped to the
capsule mechanism, not to the Long-Horizon Recovery gauntlet as a whole. No
row is scoped to `benchmarks/gauntlets/long-horizon-recovery.json` itself, so
per instruction no roadmap row was added or edited by this lane.

## Files

`killed_run/` is the run directory exactly as SIGKILL left it: `run.log`,
`journal.jsonl`, `claims.json`, `target.json`, `capsule.json` (the last
capsule the running process itself wrote, stale by definition since it
predates the kill), the Work document, `attempts/` (A1's only, since A2 never
reached round end) and `screens/` (the intent screen the door recorded).
`canonical_state_before_kill/` is the same class of files snapshotted
immediately before the kill was issued, for comparison against `killed_run/`.
`continuity_capsule_after_kill.json` and `continuity_screen_after_kill.txt`
are `continuity.py` re-run against the killed run directory, so they reflect
the crash rather than the process's own last write. `driver.log` is the
kill driver's own timeline with pids and timestamps. `driver.py` is the
driver itself, frozen for reproducibility. `result.json` is the driver's own
summary record. Every absolute scratch path, this machine's hostname, and
the interpreter path were replaced with bracketed placeholders throughout
this directory before it was committed; `MANIFEST.json` beside this file
lists every artefact with its sha256 as frozen.
