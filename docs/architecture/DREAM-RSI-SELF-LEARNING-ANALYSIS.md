# Dream-RSI applied to Brother 1.1.0: self-learning analysis

Drafted 2026-09-20 from the paper's full text and this tree (provenance log kept locally under ~/.claude/evidence/dream-rsi/). Orchestrator verification, same day: the three code claims in section A were confirmed by grep (dispatch reconciles actual_cost to the estimate; jev_decide never consults the dispatch quarantine; J064 is a choice question while that type is quarantined). The Jev ledger count moves fast: 104, 232, then 409 decisions within about an hour, 12 graded throughout, caused by test and gate runs writing to the live ledger (fixed in scripts/required_fast.sh the same day). Units D0 to D15 are in docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json. Done-checks below are proposed, none has been run.

**A. FIDELITY CHECK**

Sources read: the paper's full text and the reference README (local copies under ~/.claude/evidence/dream-rsi/, not shipped), and the repository files cited below. Nothing was written to disk. Implementation checks in the WBS are proposed, not executed.

1. **The evolving object is executable exploration-policy code.** The discovery agent, policy-development agent, evaluator, underlying models, and execution interfaces remain fixed. This is not weight training or permission for the system to rewrite its grader. **Paper §3.**

2. **The policy controls exploration allocation.** It chooses starting nodes, parallel batches, branch continuation, and stopping. The policy stays fixed during an online rollout; revisions happen between rollouts. **Paper §3, shared interface and online rollout.**

3. **A discovery node represents an actual generation and evaluation attempt.** It has a primary parent identifying the saved workspace resumed, inherited observations, resulting artifacts, diagnostics, and a task score. A chronological event log is not automatically such a tree. **Paper §3, discovery trees.**

4. **Replay exposes recorded outcomes deterministically.** It begins with the root, reveals continuations of selected branches, and opens recorded root branches in creation order. It does not generate an outcome for an unrecorded branch or reconstruct what another model would have produced. **Paper §§2, 3, offline evaluation.**

5. **Policies observe only the revealed prefix during replay.** Appendix B permits legal-action metadata and structural information, but prohibits unrevealed scores, hardcoded winning identifiers, and reading internal traces inside policy execution. The development agent may inspect feedback between revisions. **Paper §3; Appendix B.2.**

6. **The main-text objective combines best attained quality, an attempt-count penalty, and a parallelism reward.** Parallelism means average represented attempts per decision round, not measured monetary savings or actual elapsed-time speedup. **Paper §3, replay objective.**

7. **Appendix B specifies a materially different operational objective.** Its prompt ranks a beta sweep using `pareto.reward = pareto.auc - lambda * parallel_penalty`. The AUC rewards attainment with fewer probes; the penalty accounts for effective sequential rounds. It also requires `plan_grid(context)` before a live episode. Item 1 of the brief therefore needs qualification: its formula describes the main text, not the complete implementation specification. **Appendix B.2.**

8. **Including the incumbent guarantees only non-decreasing average replay score on the fixed evaluation history.** It does not guarantee improvement on every historical tree, higher quality alone, lower actual spending, or better future online outcomes. **Paper §3, policy improvement and selection.**

9. **The prose-guidance finding is narrower than “memory does not work.”** On the reported ConvDiv comparison, explicit directional guidance underperformed the corresponding unguided variants under equivalent budgets. The exploration prompt still requires reading historical proposals and measured results. Item 1 is directionally right, but overgeneralizes if applied to all textual memory. **Paper §5.1; Appendix B.1.**

10. **Failure interpretation and exploration patience are part of the policy.** Appendix B distinguishes repairable implementation failures from exhausted directions, preserves successful historical anchors, and prevents a later recoverable failure from permanently starving a branch. Its benchmark-specific success semantics must not replace Brother’s evidence semantics. **Appendix B.2.**

11. **The paper reports empirical gains, not universal convergence or safety guarantees.** Its evolving effort example supports adaptation across discovery rounds. It does not establish secure policy execution, privacy preservation, trustworthy monetary accounting, or production workflow safety. **Paper §§4, 5.2, 7.**

12. **The supplied reference repository is not an available implementation to vendor.** Its README lists the full codebase and reproduction scripts as being prepared. Build from the published specification, and explicitly resolve the main-text versus appendix differences. **Reference README, Release plan.**

Corrections to the repository baseline:

- `docs/plan/journal.jsonl` contains **3 events**, all with empty `parent_ids` in this checkout. That verifies the count, not the existence of a usable discovery tree.
- The read-only snapshot of `~/.brother/jev/ledger/decisions.jsonl` contained **232 decisions**, not 104. Its companion `outcomes.jsonl` contained **12 outcomes**. `jev_calibration.join()` returned **12 joined**, **220 unmatched**, no reported duplicate/orphan/corrupt-record anomalies, and `reliable: true`. These are snapshot counts, not a stationary dataset.
- `openrouter_dispatch.dispatch()` calls `reconcile(actual_cost=estimated_cost)`. The existing `/tmp/brother-or-dispatch-state/openrouter-ledger.jsonl` therefore cannot establish actual spending merely because a field says `actual_cost`.
- There is already an execution workflow: scheduling, exclusive claims, isolated work, checks, repair, and serial integration. The missing component is a measured policy-improvement loop across it.
- Lane advice is not executable routing. `LaneWorker.run()` checks `lane_router.route_lane()`, but `_run()` still launches its configured worker command. `run_node()` also omits `content_class` and `checker` from its constructed unit.
- Jev has divergent control paths. `jev_decide.decide()` accepts the configured question types without invoking the shared dispatch quarantine. `data/jev-registry.json` defines J064 as `choice`, while `model_capability_profile.py` quarantines that type.

**B. MAPPING TABLE**

`T` below means an immutable **revealed tree view**, including previously completed history, current observations, legal-action metadata, and recorded resource state. `L` means the legal actions supplied by deterministic code. `B` means externally enforced limits. These are proposed pure interfaces, not existing functions.

“Partially” means records support some observed decisions, not arbitrary counterfactuals. Runtime filename patterns below are existing writer contracts; their presence in an arbitrary historical run is not assumed.

| Existing policy: file, function, rule | Decision and proposed interface | Outcome that grades it; cost signal | Replay support today |
|---|---|---|---|
| `scripts/graph_loop.py::plan`: sort by shipping membership, descending downstream weight, estimated hours, identifier | Ready-unit priority: `rank_ready(T, L) -> tuple[UnitId, ...]` | Verified delivery of the declared workload, dependency unlock time, starvation and integration failures; elapsed time and complete attempt cost | **Partially.** `docs/plan/claims.json`, `docs/plan/journal.jsonl`; neither preserves each ready set or alternative integration order |
| `scripts/graph_loop.py::machine_capacity/plan`; `scripts/loop_bridge.py::run/rolling_run`: hardware/load bands, estate cap and `MAX_IN_FLIGHT` both 3, serial fallback without isolation | Requested batch width: `choose_batch(T, L, B) -> tuple[Action, ...]` | Same verified work completed, conflicts, overload and makespan; resource occupancy and paid attempts | **Partially.** Loop results expose effective cap; no complete historical resource timeline. Explicit `slots` currently overwrites derived capacity, so learned requests must not use it as an authority bypass |
| `scripts/lane_router.py::_route_lane_deterministic/_route_adversarial_review`: public/generalized noncritical drafting goes to DeepSeek with Muse review; private/critical and explicitly reserved reviews remain restricted | Choose among authorized worker/reviewer pairs: `choose_lane(T, L) -> LaneId` | Independent check result, rework and accepted review findings; actual tokens, money and elapsed time | **No** for alternative-model outcomes. `scripts/unit_trace.py` and loop usage sidecars offer partial observations, but the loop does not bind lane advice to actual execution |
| `scripts/model_worker.py::_model_argv/_default_argv/_timeout_s/run_model`: explicit command/environment routing and bounded timeout | Choose an approved executable profile: `choose_profile(T, L, B) -> ProfileId` | Actual requested versus observed model, useful result, timeout, verification; usage and duration | **Partially** for recorded execution; **no** counterfactual response from another profile |
| `scripts/loop_bridge.py::run_node/LaneWorker.run`; `products/brothermode/tools/bm_repair.py::repair`: repair after a failed check, refuse initial NO-DATA, bounded attempts and remaining time | Continue a safe repair, stop unresolved, or escalate: `choose_repair(T, L, B) -> RepairAction` | Subsequent verified repair and canonical revalidation; all failed and successful attempts | **Partially.** Repair returns attempt summaries, but the loop collapses them and retains incomplete per-attempt evidence and usage |
| `scripts/loop_bridge.py::Breaker`: open after 3 eligible failures, cooldown 120 seconds, at most 10 failovers | Defer or escalate earlier: `choose_deferral(T, L, B) -> Deferral` | Avoided futile calls without losing useful completions; delay and failed-call cost | **Partially.** Failures are classified, but breaker transitions are largely transient/log text. Existing limits stay immutable |
| `scripts/claim_store.py::effective_ttl/BackgroundRenewal/dead_reason/reconcile`: default lease 20 minutes, renewal at half the lease, same-host dead-owner detection | Lease/recovery behavior is **not an initial learned action**: `suggest_recovery(T, L) -> Advisory` | Duplicate ownership, stale completion rejection, recovery delay; occupancy and rework | **Partially.** Claim events exist. Exclusive ownership and retry-safety decisions remain deterministic |
| `plugin/runtime/brother/core/openrouter_dispatch.py::dispatch`; `openrouter_strict.py::enforce_floors`: default cap $20, 4 slots, timeout floor 300 seconds, token floor supplied by caller | Pick a smaller approved call allocation: `choose_call_budget(T, L, B) -> Allocation` | Verified usefulness and truncation/failure; actual provider cost, tokens and latency | **Partially.** Existing temporary ledger has reservations and reconciliation, but estimated cost is mislabeled as actual |
| `plugin/runtime/brother/core/or_fanout.py::main/run_job`: default 12 workers, 32,000 tokens, high effort, estimated cost $0.05; worker count passed as dispatch slot cap | Batch approved jobs and select supported profiles: `batch_jobs(T, L, B) -> Batch` | Useful checked results, queue latency, errors; returned `seconds` plus corrected usage | **Partially.** Caller-selected result files preserve job status, model and duration. Untried token limits and contention effects are unknown |
| `scripts/jev_seam.py::_resolve_mode/consult`; `data/jev-seams.json`: unspecified entries off; J030/J064/J063/J102/J117 shadow | Recommend optional observation allocation: `select_observations(T, L, B) -> tuple[SeamId, ...]` | Independently resolved questions and useful findings; call cost and delay | **Partially.** Jev decisions/outcomes exist; off, refused, dropped and unfinished calls are not uniformly durable. Mode authority is not learnable |
| `scripts/jev_cascade.py::route/_promotion_gate`: precision targets 0.90/0.95/0.98; critical never acts; `MIN_SAMPLES=20`; signed, model-specific promotion | Advisory escalation priority: `rank_escalations(T, L) -> tuple[CaseId, ...]` | Correctly resolved cases, unresolved cases and response delay; review cost | **Partially.** Calibration ledger exists. Thresholds, founder authorization and escalation exhaustion are fixed constraints |
| `scripts/jev_calibration.py::report/threshold`: fixed confidence bands, minimum sample count, Wilson lower bound and anomaly rejection | Candidate may consume a frozen calibration result: `use_calibration(T, L) -> Advisory` | Calibration against matching outcomes; labeling cost | **Yes, narrowly**, for recalculating recorded prediction accuracy. **No** for proving another action’s downstream outcome. Current reports do not isolate model versions |
| `scripts/jev_seam.py::_abstain_reason/_audit_flag/_run_seam_job`: abstain band 0.2 to 0.8; audit rate 0.05; reordered choice probe near threshold within 0.1 | Allocate **additional** audits: `extra_audits(T, L, B) -> tuple[DecisionId, ...]` | Independently discovered errors and confidence calibration; audit expense | **Partially.** Audit flags are recorded, but labels are sparse. Mandatory auditing and abstention cannot be reduced |
| `scripts/jev_seam.py::_hard_deadline/_max_inflight/_daily_budget/_breaker_admission`: default wait 2 seconds, 32 in flight, 2,000 calls/day, breaker after 5 failures with 600-second cooldown | Schedule optional calls within fixed limits: `schedule_observations(T, L, B) -> Schedule` | Completed, dropped, timed-out and useful observations; call count and latency | **Partially.** Budget state persists; drops and breaker state are substantially process-local. The wait deadline does not terminate the underlying call |
| `scripts/required_fast.sh::run_check` and its ordered invocation list | Order independent mandatory checks: `order_checks(T, L) -> tuple[CheckId, ...]` | Time to first authoritative failure, identical final obligations; check duration | **Partially.** Durations and exit codes print, but the code file is deleted and complete durable check histories are absent |
| `plugin/runtime/brother/core/registry.py::compose_run`: caller order, serial invocation | Order explicitly independent capability calls: `order_capabilities(T, L) -> tuple[CallId, ...]` | Per-capability evidence and final acceptance; duration and attributable cost | **Partially.** Returned domain results and receipt exist; chronological causal execution events do not |
| `scripts/forecast.py::hours/main`: base 1.1 hours, sample 10, optimism multipliers 1.5 and 3.0, fixed difficulty/coupling assumptions | Advisory effort forecast: `forecast_remaining(T) -> Interval` | Prospective interval coverage and error; actual working time | **No** for a reliable forecast backtest. Existing constants are documented assumptions, not a dataset of preregistered forecasts and realized outcomes |

All numeric defaults in this table come from the named source files. Serialization limits and lock-poll intervals are implementation controls, not proposed optimization targets.

**C. WHERE THE ANALOGY BREAKS**

| Failure of the analogy | Concrete guard |
|---|---|
| Brother does not have one comparable quality score | Preserve an evidence vector: correctness, required obligations, scope, integration, acceptance and unresolved evidence. Optimize cost or time only within an admissible cohort. A cheap incomplete delivery cannot beat a complete one |
| A journal is not a discovery tree | Record explicit primary attempt parent, workspace revision, dependency parents, decision input and context fingerprint. Never infer these from adjacent events or matching unit identifiers |
| Untried branches have no outcomes | Separate `legal` from `recorded_supported`. An unsupported action terminates that comparison with NO-DATA. Do not score the candidate’s remaining supported prefix as a complete successful episode |
| Recorded outcomes depend on context | Match workspace, prompt/context hash, worker profile, grader and environment. Different ordering can change available context or canonical revision; mark such transitions unsupported |
| Parallel replay is not a timing simulator | Initially report work and batch structure separately from elapsed time. Claim makespan savings only after live measurements or validation of an explicitly labeled timing model |
| Tiny samples and correlated observations | Split by complete run, workload and time, not individual events. Keep development and sealed evaluation histories separate. Insufficient independent episodes leave the incumbent active |
| Models, prices and graders change | Pin model identity, pricing source/version, grader digest and environment. Invalidate promotion eligibility on drift. Unknown actual model or price gives NO-DATA for the affected comparison |
| Repeated policy search overfits replay | Include the incumbent, limit proposal/evaluation budgets, use sealed holdouts, rename identifiers, perturb irrelevant metadata, and report support coverage alongside score |
| A policy games its objective | Freeze obligations and workload denominators before execution. Charge all attempts, proposal calls, reviews and failures. Stopping unresolved preserves unresolved work in the score |
| A learned policy loosens safety | It receives legal action handles, not gate configuration, shell commands or mutable verdicts. Authority, privacy, leases, scope, spending and founder decisions are checked again at execution |
| Model output becomes its own truth label | Grade only the exact proposition independently established. A passing unit check does not automatically prove that every sentence in a completion note matches its diff |
| Logging failures create selectively favorable history | Write action intent before learned execution, then terminal observations. Missing or corrupt links quarantine the episode. During a canary, recording failure immediately disables learned actions |
| Rollback is confused with undoing effects | Roll back the active policy for subsequent decisions. Preserve all evidence and charged spending. Resolve already-started work through existing claim, stop and integration controls |

Two existing implementation weaknesses deserve explicit treatment:

- `Evidence` permits empty defaults; the learning boundary must require populated identity, status, revision and artifact references.
- `jev_calibration.threshold()` finds an eligible confidence band, while routing admits confidence above the returned threshold. A safety claim about that admitted range needs evidence for the admitted population, with model and framing isolation. Do not interpret the existing function as a universal certification.

**D. ARCHITECTURE**

The smallest real system is a durable recorder, a replay interpreter, a constrained policy runner, a deterministic grader, a bounded proposer, and a promotion controller. It does not need another scheduler, claim store, verdict vocabulary or public command.

New modules, all **NEW**:

| Module | One responsibility |
|---|---|
| `plugin/runtime/brother/core/dream_record.py` | Validate and durably record decision lifecycle events and their artifact references |
| `plugin/runtime/brother/core/dream_world.py` | Build supported historical worlds and expose only their revealed prefixes |
| `plugin/runtime/brother/core/dream_policy.py` | Execute restricted pure policy code and validate its proposed actions against immutable constraints |
| `plugin/runtime/brother/core/dream_grade.py` | Compare incumbent and candidates on frozen evidence, objectives and evaluation cohorts |
| `plugin/runtime/brother/core/dream_propose.py` | Prepare sanitized proposal inputs and collect bounded candidate code through existing dispatch |
| `plugin/runtime/brother/core/dream_promote.py` | Manage shadow, canary, promotion, expiry and rollback records |
| `scripts/dream_bridge.py` | Connect script entry points to the same core recorder and policy interface in source and packaged installs |

Reuse:

- `scripts/journal.py` as the run’s event index.
- `scripts/claim_store.py` as exclusive execution ownership.
- `scripts/journal_projection.py` for derived reports.
- `plugin/runtime/brother/core/context.py` for context provenance and prompt hashing.
- `plugin/runtime/brother/core/evidence.py` for evidence vocabulary.
- `scripts/jev_calibration.py` for prediction/outcome joins.
- `scripts/mutation_probe.py` and `scripts/mutation_gate.py` for adversarial test sensitivity.
- Existing dispatch, privacy, resource and integration controls as execution authorities.

The current journal truncates oversized payloads and continues after append failure. Consequently, full learning records must not be stuffed directly into its bounded payload.

Use one logical event schema, stored as a durable artifact, with a compact reference in the existing journal envelope:

```text
LearningEvent {
  schema
  event_id, run_id, session_id, unit_id
  type
  at, monotonic_elapsed
  parent_ids
  episode_id, decision_id, attempt_id, batch_id
  primary_attempt_parent, dependency_parents
  capability, seam_id

  identity {
    engine_revision, policy_hash, policy_mode,
    grader_hash, environment_hash,
    requested_model, actual_model, price_version
  }

  input {
    revealed_prefix_hash, context_manifest_ref,
    workspace_revision, legal_actions_ref,
    limits_hash, content_class
  }

  decision {
    proposed_action, effective_action,
    incumbent_action, selection_probability,
    authorization_ref, refusal_reason
  }

  observation {
    execution_state, evidence_refs,
    failure_class, output_revision,
    verdict, unresolved_obligations
  }

  cost {
    reservation_id, estimated_money, actual_money,
    tokens_in, tokens_out, cache_tokens,
    queue_seconds, execution_seconds,
    source, measurement_kind
  }
}
```

This is a schema description, not an example populated with invented observations. Unavailable values use an explicit NO-DATA value with a reason. Selection probability is meaningful only when the assignment mechanism actually defines it.

Event types cover episode opening, decision intent, action start, action completion/refusal, independent grading, episode closing and policy transitions. They share this schema.

Full records live under the run directory in **NEW** `learning/events/<event_id>.json`; retained artifacts and immutable policy versions also live under **NEW** run-owned learning storage. Journal entries contain only the event reference and digest. A derived index can be rebuilt; it is not another authority.

Recording hooks:

| Exact existing call site | Record |
|---|---|
| `graph_loop.plan()`, before and after filtering/ranking | Candidate set, exclusions, dependency state, resource readings and selected batch |
| `loop_bridge.main()` before claiming; `rolling_run.plan_ready()` and `rolling_dispatch()` before starting | Effective scheduling decision, live work and requested/effective capacity |
| `claim_store.acquire()/renew()/release()/reconcile()` | Claim identity and lifecycle, using explicit causal references |
| `lane_router.route_lane()/route_decision()` | Advisory route and its inputs |
| `loop_bridge.run_node()` around `worker.run()`, `verify.verify()` and `repair.repair()` | Initial attempt, exact check evidence, scope result and repair entry |
| `LaneWorker.run()` around `_run()`; `model_worker.run_model()` around the actual invocation | Effective executable profile, remaining budget, response identity, duration and failure |
| `bm_repair.repair()` around each retry and verifier call | Each repair child and its outcome, not only the collapsed final summary |
| `integrate.integrate_one()` around canonical checking and before returning | Actual base, candidate revision, changed files, revalidation and integration disposition |
| `openrouter_dispatch.dispatch()` around reservation, strict execution and reconciliation | Requested/actual model, usage provenance, errors and retained liabilities |
| `or_fanout.run_job()` and `main()` | Job identity, batch membership, queue/elapsed time and returned result |
| `jev_seam.consult()` at every admission/refusal; `_run_seam_job()` on completion | Stable decision identity, mode, budget/breaker refusal, asynchronous completion and prediction |
| `jev_decide.decide()` around its bridge call | Content/type gate disposition and actual typed response |
| `jev_calibration.append_outcome()` after validation | Exact independent label and its evidence source |
| `required_fast.sh::run_check()` immediately after capturing the command’s exit code | Command, revision, verdict, duration and output digest |
| `registry.register()/get()/compose_run()` around capability invocation | Coverage identity, invocation, result and exceptions, without double-counting nested wrappers |
| `forecast.hours()` and its reporting caller | Prospective forecast, input cohort and later outcome linkage |

A coverage manifest must distinguish **wired**, **unobserved**, **unsupported**, and **not applicable**. Every registered capability and configured seam gets an explicit state. Declaring a seam in `data/jev-registry.json` does not prove its call site executes.

Policy execution contract:

```python
decide(
    revealed: RevealedTree,
    legal: tuple[ActionHandle, ...],
    limits: ImmutableLimits,
    state: PolicyState,
) -> tuple[Decision, PolicyState]
```

`PolicyState` is explicit, bounded and reset between episodes. The runner interprets a restricted Python subset over immutable values. It does not execute unrestricted model-generated Python with an import blacklist. No filesystem, network, environment, reflection, process creation or access to unrevealed records is exposed. Instruction and allocation limits enforce termination.

The executor rechecks dependencies, claims, scope, privacy, resource ceilings and authorization immediately before acting. Requested parallelism cannot overwrite machine capacity. Requested spending cannot overwrite a cap.

Promotion:

1. Freeze the incumbent, policy interface, grading contract and development/evaluation manifests.
2. Produce candidate code through a bounded proposal run. Deterministic validation rejects illegal code and actions.
3. Replay candidate and incumbent on the same supported worlds. Report per-world results, aggregate objective, unsupported episodes and missing measurements.
4. Run shadow decisions beside live incumbent decisions. Shadow disagreements are observations, not counterfactual outcome evidence.
5. Create a founder-visible canary record containing policy digest, exact eligible workload, spending/run/time limits, assignment mechanism, incumbent, expiry and rollback rule.
6. Execute only within that authorized canary. Promotion requires independent outcome evidence and a recorded acceptance decision.
7. Automatically restore the incumbent for subsequent decisions on a hard-control violation, incomplete causal recording, identity drift, breached canary limit, or a preregistered outcome regression. Missing evidence prevents expansion and ends the canary at expiry.

Numerical canary size and improvement thresholds are **NO-DATA** until recording supplies an adequate baseline. They must be specified before admission, not inferred from the candidate’s results.

Roles:

- **DeepSeek:** proposes bounded policy code from sanitized development histories.
- **Muse:** proposes alternative code and adversarial mutations; findings need deterministic reproduction.
- **Jev:** supplies typed probabilistic observations through the existing control plane. It neither authors the authoritative score nor approves policy promotion. Quarantined question types remain unavailable; changing a question creates a new framing and needs new evidence.
- **Deterministic graders:** decide policy legality, replay support, evidence validity, test sensitivity and comparison results.
- **Orchestrator:** maintains the frozen experiment contract, runs checks and presents the founder-visible decision record. Founder-only authority remains outside learning.

**E. WBS**

The existing launch file uses exactly:

```text
id, title, objective, worker, checker, depends_on, wave, owns, done_check
```

Every unit below preserves those keys and their order. Difficulty and release scope therefore appear inside `objective`; the named mutation appears in `checker`.

All `dream_*` and `test_dream_*` files below, `scripts/test_mutation_probe.py`, and `docs/architecture/DREAM-LEARNING.md` are **NEW**. Existing paths were checked. `bundle/runtime` is an existing generated ownership scope; receipts must enumerate its actual changed files. Generated files are regenerated, never hand-edited.

Wave numbers and unit identifiers are proposed plan ordinals. Difficulties are estimates. Done checks become runnable when their named NEW test files land; no future check is represented as already passing.

**Required for 1.1.0: D0 through D13.** This supplies recording, independently useful reporting, offline evolution, shadow operation and a bounded first canary mechanism. Enabling learned behavior remains conditional on evidence and authorization.

**Follow-on for 1.1.x: D14 and D15.** Repair and scheduling alter execution trajectories more substantially and require richer recorded support.

```json
[
  {
    "id": "D0",
    "title": "Record durable learning events through the existing journal",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Implement the shared event contract, strict missing-data representation, durable event artifacts, compact journal references and source/package import bridge. Ordinary recording failure remains visible; learned-action admission requires a durable intent. Recording is usable without any learner.",
    "worker": "orchestrator",
    "checker": "Deterministic event tests. Mutation M-D0-DROP-INTENT: report a successful append without persisting its event; the durability and admission assertions must fail.",
    "depends_on": [],
    "wave": 0,
    "owns": [
      "plugin/runtime/brother/core/dream_record.py",
      "plugin/runtime/brother/core/test_dream_record.py",
      "scripts/dream_bridge.py",
      "scripts/journal.py",
      "scripts/bundle_runtime.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_record -v"
  },
  {
    "id": "D1",
    "title": "Preserve causal worker, repair and integration histories",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Record ready sets, actual claims, explicit attempt parents, every retry, exact verification evidence and canonical integration results. Preserve content classification and checker metadata. Distinguish advised lane from executed worker. Keep lease and retry-safety authorities unchanged.",
    "worker": "orchestrator",
    "checker": "Deterministic execution-history tests. Mutation M-D1-LAST-ATTEMPT-ONLY: discard earlier repair children and retain only the terminal attempt; history completeness and accumulated-cost assertions must fail.",
    "depends_on": ["D0"],
    "wave": 1,
    "owns": [
      "scripts/graph_loop.py",
      "scripts/loop_bridge.py",
      "scripts/claim_store.py",
      "scripts/lane_router.py",
      "scripts/model_worker.py",
      "scripts/integrate.py",
      "products/brothermode/tools/bm_repair.py",
      "products/brothermode/CHECKSUMS.sha256",
      "plugin/runtime/brother/core/test_dream_execution.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_execution -v"
  },
  {
    "id": "D2",
    "title": "Make dispatch cost and identity observations trustworthy",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Parse supported usage envelopes with provenance. Separate estimates from actual charges, retain unknown post-dispatch liabilities, reject ambiguous model identity for learning, make reconciliation idempotent, and define explicit daily accounting with outstanding reservations preserved. Enforce deployment slot and spend ceilings independently of fanout requests.",
    "worker": "orchestrator",
    "checker": "Deterministic dispatch/accounting tests. Mutation M-D2-ESTIMATE-AS-ACTUAL: substitute the reservation estimate for missing provider usage; actual-cost eligibility must fail.",
    "depends_on": ["D1"],
    "wave": 1,
    "owns": [
      "plugin/runtime/brother/core/openrouter_dispatch.py",
      "plugin/runtime/brother/core/openrouter_ledger.py",
      "plugin/runtime/brother/core/openrouter_strict.py",
      "plugin/runtime/brother/core/or_fanout.py",
      "plugin/runtime/brother/core/test_dream_dispatch.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_dispatch -v"
  },
  {
    "id": "D3",
    "title": "Join asynchronous seam observations to independent outcomes",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Allocate stable decision identity before asynchronous submission; record refusals, drops, unfinished calls and terminal answers. Bind labels only to the exact independently settled proposition. Apply shared question-type quarantine on the direct seam path. Isolate calibration by model and framing and validate the admitted confidence population without lowering existing precision requirements.",
    "worker": "orchestrator",
    "checker": "Deterministic seam lifecycle tests. Mutation M-D3-WRONG-LABEL-PARENT: attach an outcome to another attempt with the same unit id; exact-identity and proposition checks must fail.",
    "depends_on": ["D2"],
    "wave": 1,
    "owns": [
      "scripts/jev_seam.py",
      "scripts/jev_decide.py",
      "scripts/jev_calibration.py",
      "scripts/jev_cascade.py",
      "scripts/jev_checks.py",
      "plugin/runtime/brother/core/model_capability_profile.py",
      "plugin/runtime/brother/core/test_dream_seams.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_seams -v"
  },
  {
    "id": "D4",
    "title": "Record gates and capability calls with explicit coverage",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Record authoritative check exit codes and durations, capability invocation/result/exception lifecycles, and coverage states for registered capabilities and configured seams. Cover composed and directly retrieved capabilities without double counting. Do not derive truth from a receipt headline or a model interpretation of log text.",
    "worker": "orchestrator",
    "checker": "Deterministic coverage tests. Mutation M-D4-DIRECT-CALL-GAP: bypass recording for a capability returned by get while preserving compose_run recording; direct-call coverage must fail.",
    "depends_on": ["D3"],
    "wave": 1,
    "owns": [
      "scripts/required_fast.sh",
      "plugin/runtime/brother/core/registry.py",
      "plugin/runtime/brother/core/dream_record.py",
      "plugin/runtime/brother/core/test_dream_coverage.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_coverage -v"
  },
  {
    "id": "D5",
    "title": "Ship useful recording reports and prospective forecasts",
    "objective": "Required for 1.1.0. Difficulty: medium (estimate). Project per-run work, actual versus estimated cost, retries, unresolved evidence and coverage from recorded events. Register forecasts before outcomes and report prospective error without importing old constants as training examples. Verify packaged recording with checkout access unavailable. This is the independently useful recording release boundary.",
    "worker": "deepseek",
    "checker": "Deterministic projection and package tests. Mutation M-D5-MISSING-COST-ZERO: coerce absent actual spending to zero; completeness and total provenance assertions must fail.",
    "depends_on": ["D4"],
    "wave": 1,
    "owns": [
      "scripts/journal_projection.py",
      "scripts/forecast.py",
      "scripts/bundle_runtime.py",
      "scripts/test_bundle_runtime.py",
      "plugin/runtime/brother/core/test_dream_projection.py",
      "docs/architecture/DREAM-LEARNING.md",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_projection -v"
  },
  {
    "id": "D6",
    "title": "Build historical worlds without inventing causal edges",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Construct immutable episodes from explicit attempt parents, input identities and evidence references. Keep dependency edges separate from primary attempt ancestry. Quarantine incomplete histories and expose legal versus recorded-supported actions separately. Legacy records remain useful observations without fabricated tree structure.",
    "worker": "deepseek",
    "checker": "Deterministic world-construction tests. Mutation M-D6-ADJACENCY-AS-PARENT: substitute the preceding journal event for the explicit attempt parent; interleaved-run fixtures must fail.",
    "depends_on": ["D5"],
    "wave": 2,
    "owns": [
      "plugin/runtime/brother/core/dream_world.py",
      "plugin/runtime/brother/core/test_dream_world.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_world -v"
  },
  {
    "id": "D7",
    "title": "Constrain policy code and preserve execution authority",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Implement the pure policy interface using a bounded restricted-language interpreter. Expose immutable revealed values and legal action handles only. Recheck privacy, scope, dependencies, claims, resource ceilings and founder authorization at execution. Reject cap overrides, arbitrary commands, hidden state and unbounded execution.",
    "worker": "orchestrator",
    "checker": "Deterministic adversarial policy tests. Mutation M-D7-REQUEST-OVERRIDES-CAP: replace the effective hard ceiling with the candidate-requested allocation; over-cap refusal tests must fail.",
    "depends_on": ["D6"],
    "wave": 2,
    "owns": [
      "plugin/runtime/brother/core/dream_policy.py",
      "plugin/runtime/brother/core/test_dream_policy.py",
      "scripts/dream_bridge.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_policy -v"
  },
  {
    "id": "D8",
    "title": "Replay only supported revealed transitions",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Implement deterministic reset, prefix revelation, legal batches, attempt accounting and termination. Return NO-DATA for unsupported model, context, workspace or ordering transitions. Include the incumbent. Report represented work separately from any estimated timing benefit.",
    "worker": "deepseek",
    "checker": "Deterministic replay tests. Mutation M-D8-HIDDEN-SCORE: expose unrevealed observations through policy metadata; prefix noninterference tests must fail.",
    "depends_on": ["D7"],
    "wave": 2,
    "owns": [
      "plugin/runtime/brother/core/dream_world.py",
      "plugin/runtime/brother/core/test_dream_replay.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_replay -v"
  },
  {
    "id": "D9",
    "title": "Grade candidates against frozen obligations and held-out runs",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Implement evidence-vector admissibility, objective-specific comparison, complete cost accounting, support reporting and run-level development/evaluation separation. Freeze graders and workload denominators. Incomplete evidence cannot establish improvement; unresolved work cannot disappear when a candidate stops.",
    "worker": "orchestrator",
    "checker": "Deterministic grading tests. Mutation M-D9-STOP-ERASES-WORK: remove unresolved obligations from a stopped episode before scoring; the incomplete-delivery fixture must fail.",
    "depends_on": ["D8"],
    "wave": 2,
    "owns": [
      "plugin/runtime/brother/core/dream_grade.py",
      "plugin/runtime/brother/core/test_dream_grade.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_grade -v"
  },
  {
    "id": "D10",
    "title": "Prove the learning checks reject semantic mutations",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Generalize mutation_probe scratch-copy inputs beyond its current fixed copy set. Require a passing baseline and an expected assertion failure for a valid semantic mutant. Import errors, missing files, timeouts and syntax failures cannot count as successful kills. Exercise all named mutations in this WBS.",
    "worker": "muse",
    "checker": "Deterministic mutation-harness tests. Mutation M-D10-INFRA-AS-KILL: classify a test import failure as a killed semantic mutant; harness attribution tests must fail.",
    "depends_on": ["D9"],
    "wave": 2,
    "owns": [
      "scripts/mutation_probe.py",
      "scripts/test_mutation_probe.py",
      "plugin/runtime/brother/core/test_dream_mutations.py"
    ],
    "done_check": "python3 -B -m unittest scripts.test_mutation_probe plugin.runtime.brother.core.test_dream_mutations -v"
  },
  {
    "id": "D11",
    "title": "Generate bounded candidates and run them in shadow",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Use existing gated dispatch to propose restricted policy code from sanitized development feedback. Keep sealed evaluation outcomes unavailable to proposers. Freeze code per episode, charge proposal and review work, and record incumbent-versus-candidate shadow actions without executing candidate actions.",
    "worker": "deepseek",
    "checker": "Deterministic proposal/shadow tests. Mutation M-D11-SHADOW-EXECUTES: submit a candidate action from the shadow path; execution-spy assertions must fail.",
    "depends_on": ["D10"],
    "wave": 3,
    "owns": [
      "plugin/runtime/brother/core/dream_propose.py",
      "plugin/runtime/brother/core/dream_policy.py",
      "plugin/runtime/brother/core/test_dream_shadow.py",
      "scripts/dream_bridge.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_shadow -v"
  },
  {
    "id": "D12",
    "title": "Control bounded canaries, promotion and automatic rollback",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Implement founder-visible immutable promotion records tied to policy, grader, cohort and limits. Require an authorized bounded canary before general activation. Preserve a last accepted incumbent and automatically disable candidate actions on control violation, missing recording, identity drift, exceeded limits or preregistered regression. Expiry and absent evidence prohibit expansion.",
    "worker": "orchestrator",
    "checker": "Deterministic promotion tests. Mutation M-D12-IGNORE-ROLLBACK: leave the candidate active after a recorded hard-control violation; subsequent-action selection must fail.",
    "depends_on": ["D11"],
    "wave": 3,
    "owns": [
      "plugin/runtime/brother/core/dream_promote.py",
      "plugin/runtime/brother/core/test_dream_promote.py",
      "scripts/dream_bridge.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_promote -v"
  },
  {
    "id": "D13",
    "title": "Ship the first policy for ordering mandatory checks",
    "objective": "Required for 1.1.0. Difficulty: high (estimate). Evolve ordering only among declared independent checks. Preserve the full mandatory set, each command, exit-code semantics and final evidence-obligation evaluation. Prove recording through replay, candidate selection, shadow, bounded canary and rollback in an isolated acceptance fixture. Live improvement remains NO-DATA until a real authorized canary supplies evidence.",
    "worker": "orchestrator",
    "checker": "Deterministic end-to-end tests. Mutation M-D13-OMIT-SLOW-CHECK: let the candidate remove a mandatory slow check; mandatory-set preservation must fail.",
    "depends_on": ["D12"],
    "wave": 3,
    "owns": [
      "scripts/required_fast.sh",
      "plugin/runtime/brother/core/dream_policy.py",
      "plugin/runtime/brother/core/test_dream_end_to_end.py",
      "docs/architecture/DREAM-LEARNING.md",
      "scripts/system_doc.py",
      "SYSTEM.md",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_end_to_end -v"
  },
  {
    "id": "D14",
    "title": "Evolve repair patience within existing hard limits",
    "objective": "Follow-on for 1.1.x. Difficulty: high (estimate). Use complete repair trajectories to rank another authorized repair against unresolved stop or escalation. Never repair initial NO-DATA, replay unknown side effects, increase attempt/time caps, or erase a historical successful anchor. Require supported evaluation and a separate authorized live canary.",
    "worker": "deepseek",
    "checker": "Deterministic repair-policy tests. Mutation M-D14-NODATA-REPAIR: permit a retry when the check never ran; repair-admission tests must fail.",
    "depends_on": ["D13"],
    "wave": 4,
    "owns": [
      "products/brothermode/tools/bm_repair.py",
      "products/brothermode/CHECKSUMS.sha256",
      "scripts/loop_bridge.py",
      "plugin/runtime/brother/core/dream_policy.py",
      "plugin/runtime/brother/core/test_dream_repair.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_repair -v"
  },
  {
    "id": "D15",
    "title": "Evolve ready-unit priority and bounded batch width",
    "objective": "Follow-on for 1.1.x. Difficulty: very high (estimate). Rank already-authorized ready work and request batch width within effective machine limits. Preserve conflict, dependency, founder, lease and starvation constraints. Treat changed canonical bases and unsupported contention effects as unknown. Verify that the chosen policy controls actual starts rather than merely emitting advice.",
    "worker": "orchestrator",
    "checker": "Deterministic scheduling-policy tests. Mutation M-D15-IGNORE-CHOSEN-ORDER: record the candidate ordering but start units in incumbent order; effective-dispatch assertions must fail.",
    "depends_on": ["D14"],
    "wave": 4,
    "owns": [
      "scripts/graph_loop.py",
      "scripts/loop_bridge.py",
      "plugin/runtime/brother/core/dream_policy.py",
      "plugin/runtime/brother/core/test_dream_scheduling.py",
      "bundle/runtime"
    ],
    "done_check": "python3 -B -m unittest plugin.runtime.brother.core.test_dream_scheduling -v"
  }
]
```

For source-changing units, the normal generator and nearest existing regression checks remain required in addition to these focused done checks. Product manifest regeneration uses the existing `products/brothermode/scripts/checksums.sh`; runtime regeneration uses `scripts/bundle_runtime.py`. The named mutation must fail the behavioral check after the final source edit.

Learned model choice, token allocation, confidence thresholds and seam-mode promotion are deliberately not activated by these units. They are recorded and exposed through the common interfaces, but alternative-model outcomes and sufficient calibration evidence do not yet exist.

**F. THREE FIRST POLICIES**

| Rank | Policy | Expected measurable win | Measurement and constraint |
|---|---|---|---|
| First | **Mandatory-check ordering** | Earlier authoritative feedback on failing changes. No promised reduction in total check work | Measure elapsed time to first required failure on comparable revisions; retain every mandatory check and identical final obligations. Reordering a fully executed serial suite does not itself save compute |
| Second | **Repair patience** | Fewer unproductive paid attempts while preserving verified recovery | Compare complete per-unit repair cost, verified recovery, unresolved work and escalation burden. Replay supports earlier stopping on recorded trajectories; live canary evidence is needed for recovery preservation |
| Third | **Ready-unit priority and bounded batching** | Earlier verified delivery of dependency-unlocking work with less idle capacity | Measure verified workload completion time, unlock latency, starvation, integration rework and actual spending. Restrict replay claims to supported independent work; validate concurrency effects live |

Expected directions are hypotheses. **Improvement magnitude is NO-DATA.** The first release should make those hypotheses measurable before claiming that Brother has learned to work better.
