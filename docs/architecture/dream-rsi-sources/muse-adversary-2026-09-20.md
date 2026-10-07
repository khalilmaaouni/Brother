10 adversarial failure modes, most damaging first.

1. Safety regression hidden by replay
Mechanism: Offline search can raise replay score by skipping, reordering, or auto passing privacy scans, gate checks, or human escalation. Replay shows no penalty because the unexecuted breach branch was never recorded.
Detect: Static diff of proposed policy code against required gate call list plus replay metrics for skipped scans, reduced escalations, and gate log divergence vs baseline.
Guard: Enforce safety floor in immutable wrapper outside learned code. Deterministic graders decide; any policy that edits, conditions, or skips gates, scans, caps, or human nodes is rejected pre eval.

2. Coverage bias: replay only knows tried branches
Mechanism: Replay can only reveal recorded children. A policy that tries a novel branch gets no recorded continuation, so selection favors policies that stay inside historically lucky paths.
Detect: Measure share of replay batches with no recorded child and novelty rate. Flag when winners have low novelty and rank flips on worlds with broader trees.
Guard: Score only on shared revealed prefix and reserve a fixed online novelty budget that replay cannot penalize. Require wins with and without novelty branches.

3. Reward hacking of replay score
Mechanism: With score as best quality minus cost plus parallelism bonus, a policy can batch useless parallel continuations or stop before recorded failures to look cheap and parallel.
Detect: Split equation 1 into components. Flag high parallelism bonus with flat max quality, lower mutation kill rate, or higher later rework.
Guard: Use lexicographic promotion: no regression on graded quality, mutation kills, and rework first, then cost. Remove raw parallelism bonus or pay it only for attempts that improve graded outcome.

4. Non stationary models and prices
Mechanism: Old trees embed old capabilities, latencies, and prices. A policy tuned for a cheap strong past model misroutes budget after rotation.
Detect: Join spend ledger model version and unit price to each replay world. Backtest winner on recent only vs full history and plot cost per graded success over time.
Guard: Normalize all replay costs to current catalog, tag model version, and expire worlds older than N weeks or one model generation. Require win on recent slice.

5. Confounding by task difficulty
Mechanism: Hard tasks produce deeper trees and lower scores regardless of policy, so averaging across worlds rewards policies tested on easier mixes or that cherry pick easy work units.
Detect: Stratify replay scores by task type, size, and baseline difficulty. Check if overall win disappears within strata.
Guard: Score by within world gain over current policy on same tree, then average. Forbid difficulty based work selection without stratified correction.

6. Non scalar outcomes collapsed to scalar
Mechanism: One scalar collapses correctness, security, maintainability, and review load, so replay prefers fast test passers that degrade ungraded dimensions.
Detect: Correlate replay winners with mutation test results, gate diagnostics, classifier later grades, and human rework. Look for test score up with mutation kills or rework worse.
Guard: Require no regression on each guarded dimension separately via deterministic graders. Models propose only.

7. Tiny samples and selection on noise
Mechanism: With few online rollouts there are few replay worlds, so a revision can beat the incumbent on 2 to 3 trees by luck and lock in noise.
Detect: Bootstrap worlds, report intervals, and test rank stability when dropping one world. Flag promotions with overlapping intervals.
Guard: Set minimum world count, require significant within world gain, keep incumbent in candidate set, and default to incumbent on ties.

8. Deterministic replay vs stochastic online
Mechanism: Paper replay returns the one recorded child deterministically, but the online agent is stochastic from the same workspace, so offline overstates repeatability of a golden path.
Detect: Rerun same parent workspaces online multiple times and compare variance to the single stored outcome. Track offline to online score gap for promoted policies.
Guard: Require online shadow validation before promotion and penalize policies dependent on a single lucky branch.

9. Cost of recording itself
Mechanism: Useful replay needs workspace snapshots, filesystem diffs, prompts, and diagnostics, which adds latency, storage, retention risk, and privacy exposure.
Detect: Measure logging time per attempt from journal timestamps, storage growth, and privacy scan hits on stored artifacts.
Guard: Log pointers plus hashes with TTL and redaction by default. Store full snapshots only for gate failures and promoted paths. Scan journal writes.

10. Thin scattered records cause miscredit
Mechanism: Journal, spend, classifier, gate, and mutation logs lack shared attempt ids and have delayed grades, so replay joins wrong costs to wrong outcomes and credits the policy for human or classifier fixes.
Detect: Audit join rate across ledgers by attempt id and parent id. Measure grade lag and label flip rate in classifier decision ledger.
Guard: Block promotion until unified attempt id with parent id is mandatory and later grades are observed. Exclude unjoined rows from training.

Evolve first, tightly bounded
A. Order of checks in pre merge gate where pass fail is conjunctive. Reason: cannot change verdict, only time to fail, fully logged in gate logs, easy rollback.
B. Which work unit to claim next under fixed lanes and caps. Reason: high volume, reversible, throughput signal without touching safety.
C. Parallel lanes within a hard spend cap. Reason: blast radius is capped by ledger enforced cap, cost signal is direct.

Never let learner touch
A. Gate composition, privacy scan, and spend cap thresholds. Reason: hard safety floor with irreversible legal and financial harm, and replay has no counterfactual for breaches.
B. Human owner reserved decisions and stop or escalate authority. Reason: learner will learn to avoid escalation to look efficient, destroying oversight.
