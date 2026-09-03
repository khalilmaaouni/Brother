# BrotherSBE metric rubric (TEMPLATE. Ratify your own version with your team,
# then FREEZE it: changes only by team decision, never by drift. The weekly
# review scores against this. Baselines below are examples; measure your own.)

Floor gates (pass/fail, NEVER tradeable for scores; a fail voids the week's scores):
- Safety floor executed on every write session (ground map, fence registration, git status).
- No invented numbers anywhere ("not measured" is legal, fiction is not).
- Bad news reported before summaries.

## The 9 metrics: what a 10 means, and the evidence that counts
1. SELF-LEARNING (baseline 3): zero ledger gaps over 14 days (every qualifying session
   has a telemetry line), 2 consecutive weekly reviews executed with one amendment
   commit or explicit no-change each, >= 5 sealed predictions scored, proportionality
   flags reviewed. Evidence: outcomes.jsonl coverage, reviews.jsonl, skill git log,
   prediction-audit output.
2. TOKEN ECONOMY (baseline 4): zero "not measured" runs, PRIMARY SIGNAL actual
   spend vs the tier declared in each brief and fence (T1/T2/T3, `references/laws-tier-and-artifacts.md` L1 and `references/laws-parallel-writers.md` L13),
   fleet budgets enforced via the Workflow engine, spend per shipped surface
   trending down across 3 weekly reviews, zero waste incidents. Evidence: ledger
   math, sbe_score budget-vs-tier check, incident lines in OUTCOMES.md.
3. SPEED (baseline 6): fleet wall-clock per landed loop down 30 percent vs your
   first measured baseline (ledger duration_h per shipped surface), zero
   infra-death retries per commit train. Evidence: ledger + train logs.
4. OPERATOR ALIGNMENT (baseline 5): measured primarily on outcomes an engineer
   can verify, not on how the output felt. Signals: review outcome on assisted
   PRs (approved versus changes-requested trend), the deploy and incident record
   on assisted changes (change-failure rate not worse than baseline), zero
   repeat-class corrections for 2 consecutive weeks. Felt ratings are still
   collected and scored (the felt-outcome-ratings check in `tools/sbe_score.py`,
   gathered per the weekly-review felt-outcome batch): what is inadmissible is a
   felt impression outranking or substituting for the mechanical verdict, by
   design, so charm must not outrank correctness. Evidence: PR review states,
   change-failure telemetry, correction-log delta, ratings.jsonl.
5. MEMORY WRITE-BACK (baseline 6): one canonical machine ledger, zero stale fence
   lines at weekly registry-check, session log every work session, vault hygiene
   pass monthly. Evidence: registry-check output, Sessions folder.
6. VERIFICATION HONESTY (baseline 7): every claim carries its calibration, every
   "pending human review" closed or re-nagged within 7 days, refuter verdicts carry
   executed falsifications. Evidence: weekly sampled audit of session logs.
7. COORDINATION (baseline 8): zero collisions AND zero baton drops across 2
   consecutive multi-writer weeks, fence lines flip to LANDED in landing commits,
   RECOVERY sub-signal: killed sessions resume by session id (never respawned
   while a transcript exists), zero redone work. Evidence: OUTCOMES incident
   lines, registry-check, resume-vs-respawn count at weekly review.
8. DELIVERY (baseline 8): two consecutive releases or trains with zero
   reviewer-surprise items, release mechanics codified as runbooks or tools.
   Evidence: review outcome on trains, runbook existence.
9. CACHE EFFICIENCY (baseline: your first measured ratio):
   sustained warm-read ratio >= 90 percent across 2 consecutive weekly reviews
   AND zero broken-prefix incidents (mid-task model/effort/MCP flips, worktree
   sprawl). Fully mechanical. Evidence: scorecard metric 9, sbe_score cache check.

Scoring law: self-scores cap at 8; 9 and 10 require the named external evidence
above. A metric holds its score only after 2 consecutive weekly reviews at that
level (the skill's own two-clean-rounds law applies to the meta-loop even though
run-level ceremony defaults to one round).
