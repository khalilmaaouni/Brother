## 1. What this is for

Use this control plane to keep an unattended run recoverable and leave a morning handoff that distinguishes completed work, unfinished work and missing evidence. It separates process supervision, authority, admission, review and reporting.

The supervisor keeps orchestrator processes available. Leases identify who may act. The drain records what cannot finish. Closeout turns supplied run state into a human handoff and a machine-readable record. These files provide components, not a standalone overnight launcher. Your driver must connect them before you leave the session unattended.

## 2. What it refuses to do

These refusals apply to work routed through the controls:

- It never merges on an orchestrator's or supervisor's authority. Canonical writes belong to the separately designated integrator.
- It never marks a unit DONE by judgement. A promising answer, a live process or a worker's confidence is not completion evidence.
- It never treats an absence as a pass. Missing evidence remains NO-DATA, even where the applicable obligation allows progress.
- It never lets one unit have two live authorities in the authority store. Each acquisition or transfer advances an epoch; an old process cannot reuse its former authority through the guarded path.
- It never runs a destructive action on its own authority. Actions classified RED are refused and queued for a human, including when queue writing fails.
- It never interprets prose or an empty response as a valid action. Actions must pass structured validation. Policy refusals are not retried.

A routing recommendation grants no authority. Acquire the lease, hold the worker claim and check the current epoch before acting. An unreadable authority store is not a free scope.

## 3. Running a night

First establish the driver. `night_supervisor.supervise()` performs one tick and requires supplied process adapters and a launcher. It neither schedules its next tick nor supplies a default launcher. The driver must also enforce admission, deliver stop requests, persist work state and invoke closeout. Do not assume importing these modules supplies that wiring.

Prepare a supervisor manifest with `run_id`, `authority_store`, `state_path`, `journal_path`, `hard_stop` and `scopes`. Use an ISO 8601 hard stop with an explicit timezone. For each scope set `scope`, `orchestrator`, `instance`, `ttl_seconds`, `heartbeat_stale_s`, `max_restarts` and `backoff_base_s`.

Choose positive lease durations and heartbeat thresholds that accommodate normal slow work. Defaults are 60 seconds for `startup_grace_s`, 3,600 seconds for `backoff_cap_s` and 900 seconds for `graceful_stop_lead_s`. The launcher must give the process the newly issued epoch. Processes must renew their leases and write heartbeats; the file adapter reads a JSON `at` value in epoch seconds.

Keep the run journal, claims and Work document durable. The resume reader actually uses `orchestrator-resume/authority.json` and `orchestrator-resume/control.json` beneath the run root. Align the authority store with the supervisor configuration; the resume reader inspects scope `night-run`. Populate the control snapshot with `hard_stop_jst`, required gates, capacity, ready work, reviews and decision queues.

Before walking away, inspect a fresh resume capsule. Resolve unreadable sources needed for dispatch and verify the run identity and held authority. A missing source produces unknown fields, not empty collections. Rebuild this capsule after a restart instead of reconstructing state from a conversation.

Declare task class, risk and evidence obligations before dispatch. Supply measured health for both roles. High and critical risk routing requests review by the other role. For council review, supply the true author and the complete criteria set: nomination excludes the author, and high or critical risk requires two distinct declared model labels. Outside content must pass its gate.

Expect bounded recovery. A confirmed dead process can be restarted with a fresh epoch and exponential backoff. Inconclusive liveness becomes NO-DATA, not permission to restart. The restart counter persists across ticks, includes the initial launch and does not reset after healthy periods. Reaching its bound leaves the scope down for operator recovery.

Set `drain_start` no later than `hard_stop`, and coordinate it with the supervisor's graceful-stop lead. These are separate controls. The supervisor records stop requests for live processes and avoids restarting dead ones near the deadline; the driver must deliver those requests.

Before the drain, admission is OPEN. During DRAINING, new work needs the exact estimated cost `SHORT`; missing or unfamiliar costs are refused. Fresh repairs are refused regardless of duration. Allow bounded verification to finish, pass already-ready work to the integrator and park unfinished work with its prior state, attempts, last failure and next action.

At the hard stop, STOPPED admits nothing new. Persist the drain result and check `assert_no_ambiguous_state()`. Every unit must be DONE, PARKED, EXHAUSTED, AWAITING-HUMAN or CANCELLED. A parking failure or a remaining RUNNING unit means closure is incomplete.

Finally, supply closeout with the actual revisions, unit states, throughput, evidence checks and decision queues. Every non-DONE unit needs a next action. Supply `delivery_evidence` and `receipt_run_dir` when a persisted delivery receipt is required.

## 4. Reading the morning

Open `MORNING-HANDOFF.md` and `orchestration/final-state.json` in the configured output directory. Check that their run identities agree with the night you intended to inspect.

Read ANOMALY first. A non-terminal unit means the drain did not complete cleanly; the document explicitly warns against trusting its counts as final.

OUTCOME gives the goal, base and final revisions, hard-stop information and verdicts. Identical revisions mean nothing shipped by this report's definition. WORK counts completed and terminal unfinished units. THROUGHPUT records integrations, repairs, replans, handoffs, fallbacks and cross-reviews.

EVIDENCE separates required PASS, required FAIL, required NO-DATA and optional NO-DATA:

- PASS means evidence supports the check.
- FAIL means evidence contradicts it. Fix the defect and verify again.
- NO-DATA means evidence is unavailable or insufficient. Obtain the missing evidence or repair its collection. It is neither success nor a measured failure.

A FAIL blocks progress regardless of obligation. REQUIRED_FOR_MERGE NO-DATA blocks merge and release. REQUIRED_FOR_RELEASE NO-DATA can permit merge but blocks release. OPTIONAL NO-DATA can permit progress without becoming PASS. Closeout evaluates its evidence ledger at release strength.

Council results need the same care: MISSING means no returned cell addressed a criterion; NO-DATA means it was addressed without a usable score. Either can produce CEILING. REJECT reflects an insufficient resolved score. Unanimous perfect scores demand another review round.

Read DECISIONS for provisional rulings, RED items and human questions. Read PROBLEMS for each unfinished unit's blocker, attempts, evidence and next action. Check the receipt line separately. A headline is not a substitute for these records.

## 5. When it refuses you and you think it is wrong

The gates have false positives. One refused a benign check name merely because it looked like a credential. A refusal is a reason to investigate, not proof that the proposed work was harmful.

Preserve the refusal and identify its source. Correct misleading source text or repair the detector, retaining the real check and adding a regression case for the benign input. Rerun the gate. Never obscure real sensitive content to evade detection.

For a birth-gate refusal, register the new part through a `run_check` entry in `scripts/check_all.sh`. A Tier C exemption needs a non-empty reason and is only legitimate for a part that cannot influence decisions.

For a release refusal, wire evidence for the named Tier A or UNCLASSIFIED gaps, or correct classification at its source when justified. The release gate explicitly directs missing coverage recovery through:

```sh
python3 scripts/assurance_coverage.py
```

Then rerun the release gate:

```sh
python3 scripts/wire_release_gate.py
```

For unreadable state, repair or restore the named file. After fixing a crash-loop cause, the documented recovery clears `down` and `down_reason` and resets `restart_count` to zero for that scope. Resolve ownership before changing anything involving a foreign live lease.

Never disable a gate, delete evidence to simulate a fresh run, lower risk to avoid review, manufacture PASS, steal a live lease or patch the morning report. Correct closeout input and regenerate it. Repeating identical inputs does not repair a deterministic refusal.

## 6. What it does NOT protect against

This is a control plane, not an operating system sandbox. A process that skips authorization and invokes repository operations directly defeats the boundary. Worktrees share repository administration. Restrict real process permissions separately.

The canonical guard recognizes specific command forms and hardcodes `main`. Without a working directory it does not recognize a commit onto canonical. Integrator capability issuance depends on deployment wiring, not an external identity check. The adapter's literal substring filter can both over-refuse harmless text and miss unsafe invocations.

Leases trust their inputs and clocks. A fabricated future time can make live authority appear expired. Duplicate manifests targeting the same run and scope are an undetected configuration error. Production callers must use real time and consistent identity and storage.

Heartbeats measure liveness, not correctness. Slow processes can look wedged, clock skew can make stale heartbeats look fresh, and the supervisor trusts the launcher's reported process identifier. A stop-request record does not terminate a process.

Drain trusts the caller's readiness decision and integration callback. A successful callback returning no state becomes DONE. It does not independently prove that integration happened. Its integration-exception path can also retain the old state in the returned mapping despite requesting a park, making the terminal-state check essential.

Closeout trusts the supplied ledger. An empty ledger can yield GREEN; the separately printed final required-gate verdict and missing receipt do not independently change that headline. GREEN can coexist with parked work. Confirm coverage, receipt availability and unfinished work yourself.

The release gate reads coverage rather than running tests. Staleness only warns, and matching file counts cannot establish freshness. The birth gate does not repair existing unwired parts and cannot recognize nested registration.

Council scores remain supplied evidence. Distinct model labels do not prove independent reasoning, and an objection answer is accepted mechanically when it cites the objection identifier. Neither establishes that the answer is substantively correct.

Supervisor journalling is best effort, so missing events do not prove nothing happened. Closeout files are written sequentially, not atomically as a pair. Verify both artifacts after a write error; do not mistake an older handoff for the completed night.
