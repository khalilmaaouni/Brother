# A/B/C data plan, 2026-09-23 (every element, its meter, its validation)

Owner orders: equal slots to 18:00, random order (seed `brother-abc-2026-09-23` gave A, C, B), same size matched sample,
"do not miss any data point", "100% fair", "low MAPE". Arm C (native Claude Code) is the control.

## Unit of comparison
8 sub units, first open sub unit of 8 different units outside the Phase 2 scope, spec 9+, nearest the median size
(files named + section length), each with a done check that EXISTS and is RED on the base (validate_sample.py).
Every arm starts from the same BASE commit (BASE.txt), own worktree, own hub branch, own unit-runs folder.

## Delivery elements (per arm, per sub unit)
| element | meter | validation |
|---|---|---|
| delivered | score_arms.py: done check GREEN on the arm tip under python3 AND /usr/bin/python3, EMPTY HOME, real clone | red on base proven by validate_sample.py |
| reliability | mutation_sweep.py on changed non test modules the section names, done check as the check: survivors / total | NO-DATA when no module, never a pass |
| time to deliver | first arm commit naming the sub unit, minus the arm start (start-<a>.txt) | loop: plan evidence LANDED line cross checked |
| extra sub units | commits on the arm branch beyond the sample (later sub units of the same units) | reported apart, not in the primary |
| units closed | CLOSED lines (loop) / unit done check green at tip (all arms) | same done check for every arm |
| not delivered in time | READY builds not landed, uncommitted edits at slot end (uncommitted-c.txt) | counted as NOT delivered for every arm alike |

## Cost elements (per arm window)
| element | meter | validation / known gap |
|---|---|---|
| OpenRouter, everything on the key (builds, probes, Jev, repair, council, killed stragglers) | or_meter.py: the account's cumulative usage at arm start and end | proven: moves with a known call; the key must have NO other consumer during an arm (no bridge calls by the orchestrator, no other loop) |
| OpenRouter attributed | dispatcher ledger sum for the window | gap to the meter = unattributed (killed calls, other consumers), reported |
| Claude, loop children (builds, planner, advisor, checker) | claude_window.py --only model-call --calls: tokens by model and type, 1 h and 5 min cache writes apart | calls with no usage record (killed) counted from claude-calls.jsonl, ESTIMATED at the per call mean, labelled |
| Claude, arm C session and its subagents | claude_window.py --only <arm C worktree folder> (recursive), plus stream-json per message usage | same killed call rule for the last in flight message |
| Orchestrator (this session) | claude_window.py --session <orchestrator id> over each arm window | the loop's own overhead; target near zero for a sleeping owner |
| Codex (Luna documenter) | not run inside any slot: documentation is not a delivery element of any arm | if run, metered separately and excluded from all arms alike |
| USD | tokens x a price table checked against a current source, applied after the arms | the table and its source are written beside the result |
| wall time, interventions | slot start/end files; every orchestrator action during an arm logged in INTERVENTIONS.md | target 0 interventions |

## Contamination log (anything that touches a meter outside an arm's own work)
| time | what | which meter | handling |
|---|---|---|---|
| 14:1x | model_call selftest runs wrote 3 claude-x rows to claude-calls.jsonl | claude calls | excluded by registry id filter; source fix queued |
| 14:27-14:28 | or_meter validation: one deepseek 7+5 call | OpenRouter key, Phase 2 window | Phase 2 only; excluded by time |
| 14:3x | Sonnet build latency probe (one real brief, trimmed flags) | Claude loop children, Phase 2 window | Phase 2 only; excluded by time |
| 14:2x | Opus 5.5 and Sonnet 7+5 probes | Claude | before any arm |

## Decisions recorded while building the instrument
| time | decision | evidence | flip condition |
|---|---|---|---|
| 14:4x | "Delivered" is the SPEC section's done check, never the builder's own check | dry run on Phase 2: D14.5 landed by the loop (grader PASS, probes clean, its own 26 tests) but the spec's `test_dream_repair.WiringTest` does not exist: NOT DELIVERED | none: the spec is the deliverable (delivery laws, 2026-09-21) |
| 14:4x | The landing gate is NOT changed before the arms; gating on the spec check is the first post-test fix | an unmeasured gate could zero out A and B; the loop is measured as it is, the same bar for every arm | the post-test measure of how often spec checks are runnable |
| 14:3x | Arm C is a scripted human developer (owner order), standard setup (project settings, no MCP, no user plugins) | standard Opus 5.5 prefix measured 69 k tokens, 0.3858 USD per one line call; this machine's full prefix 235 k | owner |
| 14:3x | Human review time is a SENSITIVITY (0, 2, 5 min per turn), not simulated with sleeps | a human who is not there cannot be measured; 0 is the pace most favourable to manual work | owner names a pace |
| 14:2x | Sample: 8 first open sub units nearest the median size, each with a runnable spec done check RED on the base | M1.2 was green on the base (already done): dropped | none |
| 14:5x | FINDING: the run budget counts OpenRouter only; Phase 2's Claude calls cost 24.02 USD recorded + 1.37 estimated killed in 22 min while BUDGET said 2.60 | dryrun-phase2/claude-a.json priced with the validated table | post-test fix: the run budget reads Claude usage too |
| 14:5x | FINDING: orchestrator (this Opus 5.5 session) cost 10.53 USD over the same 22 min | dryrun-phase2/claude-orch-a.json | silence during arms; reported as its own line |
| 14:59 | EV gate landing value set to 5 USD for BOTH loop arms; arm A restarted clean with 60 min slots | at the 1 USD default every all Sonnet round was refused before round 0 (J1.a: EV 0.333 < cost 0.80) | owner names another value |
| 18:0x | FINDING: arm A measures a harness defect, not Sonnet: 280 of 283 Sonnet builds NO-DATA; in J1.a round 0, 7 of 8 answers rejected "answer is not valid JSON: Expecting value: line 1 column 1 (char 0)"; builds took 293 to 391 s against the 150 s straggler cut | ab/unit-runs-arm-a J1.a-171147/round0/results.json | post-test fix: extract JSON from fenced or lead-in answers at the parse step, then a fair A rerun on the owner order |
| 18:0x | unit_ledger DuckDB rollup crashes on arm A rows: cost_usd inferred as JSON (Claude builds carry no per build cost) | BinderException sum(JSON) | post-test fix: cast cost_usd to DOUBLE |
| 18:20 | model conformance: 13 of 13 PASS through the deployed fan out (12 builders + Jev) | ab/conformance/CONFORMANCE.json | limit: small prompt only; full brief per family and a per model straggler cut are the next fixes |
