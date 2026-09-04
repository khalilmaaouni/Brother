# Work doctrines: the practical field guide

This is the day-to-day guide across the BrotherSBE spine, arranged the way the
work actually arrives. It assumes you have read `SKILL.md` (the law) and want to
know what happens when a stack trace lands, a metric is disputed, a partner feed
starts delivering nulls, or a Terraform plan needs a second reader.

Two rules sit under every doctrine below, so they are stated once here:

- **The check installs before the work.** The spine of the whole system is that
  an agent earns trust in exact proportion to how mechanically its output can be
  checked. So the verification is decided at plan time, not bolted on after. If
  no check can be named, the output is a draft a named human owns, labeled
  UNVERIFIED next to the item itself.
- **Four failure classes are silent, and they carry HARD gates.** A wrong result
  looks exactly like a right one and detection latency runs from minutes to
  never. Those four classes (numbers, migrations, money and partner paths,
  unexecuted checks) each have a mechanical gate in `tools/sbe_gate.py`. Ungated
  output ships labeled UNVERIFIED. A HARD gate is never waived by a session
  instruction (`references/laws-overrides-and-waivers.md` L16).

Every gate command in this doc is real. `tools/sbe_gate.py` inspects the current
directory (or a directory you pass, and exactly that one) for the receipt that
proves a check RAN,
and prints PASS, FAIL, or NO-DATA per class. Run it advisory in a session; run it
`--strict` in CI, where it exits nonzero and stops the merge. NO-DATA is never a
pass.

```
python3 tools/sbe_gate.py                 # all four classes, advisory
python3 tools/sbe_gate.py numbers         # one class
python3 tools/sbe_gate.py numbers --strict  # CI: nonzero exit on FAIL
```

The four gates are regression-tested against the exact defects the operating
record produced: `evals/run_evals.py` plants each failure as a fixture and
asserts the gate catches it (every case in `evals/run_evals.py`, release blocked on any regression). The
worked examples below reuse those fixture shapes, so what you copy is what the
evals prove.

---

## The debugging loop (highest frequency, cheapest entry)

**Trigger.** A stack trace, a failing test, or a confusing log line. Daily. This
is the highest-frequency use in the estate and the cheapest first test for a
skeptic.

**Drafts.** Paste the symptom; get ranked candidate causes, each with the
evidence it would leave. That is the entire loop: symptom in, ranked hypotheses
out, each one falsifiable against a reproduction.

**Decides / refuses.** BrotherSBE does not decide which cause is real. You verify
each candidate against a reproduction before acting, because confident wrong
causes are the documented failure mode: 42 percent one-shot root-cause accuracy
in one company's first-party production system, published 2024
(https://engineering.fb.com/2024/06/24/data-infrastructure/leveraging-ai-for-efficient-incident-response/,
vendor figure, single source). Self-reported speedup is inadmissible: an
early-2025 randomized trial of 16 experienced developers on 246 real issues
measured them 19 percent slower with AI while they believed, afterwards, that
they had been 20 percent faster
(https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/);
METR's own February 2026 follow-up calls its newer estimates an unreliable
signal (https://metr.org/blog/2026-02-24-uplift-update/), and the belief being
wrong, not the direction, is the point here.

**Gate.** None beyond the reproduction itself. This loop earns trust first
precisely because its geometry is favorable: blast radius near zero, detection
latency seconds. You see the fix fail on the repro before it, pass after. That is
the verification, and it is free.

**Entry cost.** Two minutes on the next stack trace, zero setup. This is the one
place in the system with no verification overhead, which is why it is the
recommended first contact.

---

## Backend services: APIs and untested code

**Trigger.** A published API surface is designed or changed; untested legacy code
is about to be touched.

**Drafts.** For APIs, the OpenAPI draft is generated from the controllers, never
invented: LLM extraction covered 48.85 percent more missed entities than
developer-provided specs (https://arxiv.org/abs/2504.16833, April 2025 arXiv
paper, single source), then
the draft goes behind a linter. For untested code, a characterization net:
generated tests that pin current behavior before any change.

**Decides / refuses.** BrotherSBE refuses contract semantics outright: versioning,
pagination style, idempotency-key behavior, and structured error codes are human
decisions with long detection latency. The linter exists because valid-but-wrong
is the failure class: 29 percent of OpenAPI completions were correct while 68
percent were merely valid documents (https://arxiv.org/html/2405.15729v1, May
2024 arXiv paper, single source). Coverage theater (a number that rises while
defect detection does not) is named and hunted: over 80 percent coverage on a
curated benchmark against under 2 percent on a realistic one
(https://arxiv.org/abs/2305.00418, 2023 arXiv paper).

**Gate.** Two apply here.

- **HARD (ran, class 4):** build plus the nearest test suite runs AFTER the last
  edit, command and output pasted, CI recomputes. A fix is accepted only against a
  test that failed before it and passes after. The receipt that satisfies the ran
  gate is a `ran-receipt.json` under the directory you hand the gate:

  ```json
  {"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 812}]}
  ```

  `python3 tools/sbe_gate.py ran` passes only when every recorded check has an
  `exit_code` of 0 AND a nonzero `duration_ms`. A missing exit code reads as "was
  it actually run?"; a zero duration reads as "a check that took no time did not
  run"; a nonzero exit is a green-on-red claim caught. All three are proven by the
  evals (`unrun-check-caught`, `executed-check-passes`, `green-on-red-caught`).

- **Characterization gate:** four mechanical conditions per generated test: it
  builds; it passes five consecutive runs; it covers a line or branch not already
  covered; it FAILS when the behavior it claims to test is deliberately broken.
  That last condition is the mutation kill that ends coverage theater, at two
  lines per test.

**Entry cost.** Two minutes on the next stack trace, zero setup. Thirty minutes
once to write the exact build and test invocations into the repository context
file. Sixty minutes to wire a spec linter (https://github.com/stoplightio/spectral)
and a breaking-change differ (https://github.com/oasdiff/oasdiff) into the CI of
the API already changing this sprint, removable in one revert.

---

## Warehouse and SQL: modelling is the lever

**Trigger.** Models are drafted or refactored; a legacy SQL migration is
scheduled; a metric definition is disputed; a headline number is owed to a
decision maker.

**The accuracy record, scoped by task type, never averaged.** These are the
numbers to lead with, and they are why this doctrine carries the strictest gates:

- Single-question benchmarks flatter: human 92.96 against best system 81.95 on
  BIRD (https://bird-bench.github.io/, self-submitted scores on a moving
  leaderboard, figures as captured at this doc's writing).
- Real multi-step warehouse workflows collapse: when Spider 2.0 was published in
  2024, GPT-4o scored 10.1 percent on it against 86.6 on Spider 1.0
  (https://spider2-sql.github.io/). That gap, an academic suite flattering a
  model that collapsed on realistic warehouse work, is the single most important
  number in the doctrine; purpose-built agents have since pushed the same site's
  Spider 2.0-Snow leaderboard past 96 percent, and neither number is evidence
  about your warehouse.
- Enterprise SQL with internal conventions: 15.9 percent
  (https://arxiv.org/abs/2606.03363, June 2026 arXiv paper, single source).
- The gold labels behind such leaderboards are shaky: a January 2026 preprint
  reports annotation error rates of 52.8 percent in BIRD Mini-Dev and 62.8
  percent in Spider 2.0-Snow, and agent rankings that track the full development
  set closely (Spearman 0.85) track the corrected subset only weakly (Spearman
  0.32, p=0.23, not statistically significant)
  (https://arxiv.org/abs/2601.08778, preprint). No leaderboard score is evidence
  about your warehouse.

**The finding that changes behavior: modelling, not the model.** Same questions,
same models scored 64.5 percent on raw third-normal-form schemas, 90.0 modelled,
98.2 through a semantic layer
(https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026, 2026 vendor
study, n=11, treat the points loosely; mechanism corroborated by Spider 2.0
error analysis). The lever is the dimensional-modelling skill a senior engineer already
owns. BrotherSBE spends its effort there.

**Drafts.** SQL, staging and mart build scripts, assertion blocks, and the
numbers manifest. Four mechanical disciplines carry the doctrine: DESCRIBE (or
read the header) plus LIMIT 5 before an unfamiliar table, and only column names
seen in that output enter a query; layered, medallion-style builds where raw is
immutable and exactly one recorded pointer names the canonical database;
assertion-gated builds where every script ends in checks that reconcile row
counts to source, prove keys unique, and recompute totals from components; and a
numbers manifest mapping every figure to the query and run that produced it.

**Decides / refuses.** BrotherSBE refuses to own grain, keys, layer boundaries,
or what a metric means. You draft the SQL; the numbers gate decides whether a
figure ships.

**Gate.** Two apply.

- **HARD (numbers, class 1):** every figure that could reach a decision ships
  with a `numbers-manifest.json`. The gate wants four things per figure: a pinned
  `snapshot_id` (a live warehouse drifts, so pin the read), a `second_derivation`
  that is textually different from the first query (or it is not independent), a
  `rerun` marked ran, and zero drift between the two derivations. A sound figure:

  ```json
  {"figures": [{
    "label": "gmv",
    "snapshot_id": "snap_2026_07",
    "query": "SELECT SUM(amount) FROM orders",
    "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
    "rerun": {"ran": true, "primary": 17570, "secondary": 17570}
  }]}
  ```

  `python3 tools/sbe_gate.py numbers` passes that. It FAILs the class that anchored
  the whole gate, the filed model that overstated a five year total against its own
  components (`primary` and `secondary` disagree):

  ```json
  {"figures": [{
    "label": "five_year_total",
    "snapshot_id": "snap_2026_07",
    "query": "SELECT SUM(y) FROM plan",
    "second_derivation": "SELECT y1+y2+y3+y4+y5 FROM plan_wide",
    "rerun": {"ran": true, "primary": 1938, "secondary": 432}
  }]}
  ```

  It also FAILs a "second" derivation that is a copy of the first
  (`non-independent-derivation-caught`) and a figure with no `snapshot_id` against
  a live warehouse (`unpinned-read-caught`). All four outcomes are in the evals.

- **HARD (ran, class 4):** generated models are reviewed as COMPILED SQL, never as
  templated source, because the template hides the join. Every model over a
  one-to-many relationship carries a row-count assertion at its declared grain:
  join fan-out returns revenue five times too high with no error
  (https://tianpan.co/blog/2026-04-10-text-to-sql-failure-modes-production, single
  source for the framing). The assertion run leaves a `ran-receipt.json` (shape
  above); every migrated model is diffed row by row against what it replaces before
  the old one retires.

**Entry cost.** Honestly higher than app work, because the verification setup IS
the cost. Half a day, no new tool: three production models with a one-to-many join
and an aggregate, reproduced by the agent from schema alone, row counts and totals
diffed against production. That is evidence about your warehouse, not a
leaderboard. DESCRIBE plus LIMIT 5 costs minutes per table. Do not round the half
day down; the diffing against production is where the trust is bought.

---

## ETL and ELT: schema-first, idempotent, blast-radius-bounded backfills

**Trigger.** A partner adds a column without telling anyone; a connector quietly
starts delivering nulls; a 3am task death needs a range re-run; a backfill is
proposed.

**The record.** 9 percent of more than 5,800 surveyed data professionals are
satisfied with AI-generated pipeline definitions, with 43 percent citing
hallucinations and 42 percent outdated syntax, published by a vendor against its
own commercial interest (https://www.astronomer.io/blog/state-of-airflow-2026/,
2026 vendor survey, single source). The orchestrator documentation itself warns backfill can
reprocess completed dates
(https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/backfill.html),
so a non-idempotent task duplicates data on re-run with no error and no alert.

**Drafts.** Extraction boilerplate, reconciliation queries, and the feed
inventories nobody finishes (feeds with no owner, connectors silent a fortnight).
Partner feeds are schema-first: the delivered header diffs against a pinned
partner contract before any row loads, and the diff names every downstream model
referencing a changed column. Mutation scripts log at event time (the ledger line
appends the moment each mutation happens, never at script end), paid for by a
crash that orphaned completed but unlogged file moves.

**Decides / refuses.** BrotherSBE refuses anywhere correctness depends on
something nobody wrote down: the landing-zone contract, the replay story, the true
grain of the source, and above all CDC snapshot keys, where a key that looks
unique records nothing wrong at the time and loses history permanently
(https://docs.getdbt.com/docs/build/snapshots). Backfills produce plans, never
executions: a printed dry-run date list, approved by a human, then a row-count
reconciliation.

**Gate.** Two apply.

- **HARD (ran, classes 3 and 4):** no ingestion change lands without a row count
  and checksum reconciled against source for a known window; no landing table is
  trusted without a replay from raw reproducing the same output. Both are queries,
  and both must leave a `ran-receipt.json` the ran gate can read.
- **HARD (approval, class 2):** backfills over data in place run only after the
  printed date list is approved by a named human, and the reconciliation runs
  after. See the approval gate below for what counts as an approval.

**Entry cost.** Twenty minutes, read-only: header diff on the one partner feed
that has broken you before, plus the downstream models touching each changed
column. Fifteen minutes to attach a read-only integration on a managed scheduler.

---

## Data quality and data incident response

**Trigger.** Two dashboards carrying the same label differ by two million with no
failed job and no alert; monitors are proposed or have grown too noisy; a
blast-radius question needs answering.

**The record.** No commercial data quality product publishes a false-positive
rate; one vendor's own book defines the metrics and publishes neither
(https://www.anomalo.com/blog/chapter-5-making-data-quality-monitoring-models-work-in-the-real-world/).
Machine-recommended monitors carry a 60 percent human acceptance rate, two in five
rejected on review, a vendor-reported figure carried by trade press
(https://www.techtarget.com/searchdatamanagement/news/366622933/Monte-Carlo-launches-first-agents-for-data-observability,
single source); the review step is the product. Alert engagement drops roughly 15
percent past 50 alerts per channel per week, per a 2026 vendor survey
(https://grafana.com/press/2026/03/18/grafana-labs-4th-annual-observability-survey-reveals-a-field-at-a-crossroads-ai-economics-complexity-and-the-enduring-power-of-open-source/).
Lineage parsers on one corpus ranged from 88 percent column coverage down to 29 to
38 percent (https://datahub.com/blog/extracting-column-level-lineage-from-sql/, the
winning vendor's benchmark), so impact under-reporting arrives confidently.

**Drafts.** Expectations as code (quality rules in version control, run in CI or on
schedule, failing loudly), coverage sweeps that cross the table inventory against
the monitor inventory and the consumption graph, and the assembly work of a data
incident: a timeline with a link on every row, a hypothesis paired with a counting
query that must run, and a post-incident table stating, per incident, which
existing test would have fired and which would not.

**Decides / refuses.** A quality rule is accepted only when it fires on a crafted
violating record and passes a known-good one; a rule accepted on inspection is
class 4 by definition. Data incident response is treated as its own discipline,
distinct from service incidents, because nothing pages: the failure is a plausible
number, detection latency runs weeks, and triage begins from a discrepancy rather
than an alert. BrotherSBE assembles the timeline; a human owns the call, the
severity, and the root cause.

**Gate.**

- **HARD (ran, class 4):** any proposed monitor set replays against a past period
  containing incidents you remember, counting catches and false fires, before it
  goes live. The replay leaves a `ran-receipt.json`.
- An alert budget per channel per week is set in advance (50 is where humans
  disengage), and monitors are deleted to stay under it.
- Before any impact answer, the ten-minute hollow-node test: pick one table whose
  consumers you know by heart, ask for its downstream consumers, count what is
  missing. That is your lineage calibration reading.

**Entry cost.** Thirty minutes on incidents already written up: per incident, which
existing test would have caught it, every row checkable by opening one test file.
Plus the ten-minute lineage calibration. Higher than app work because the value is
in the replay-and-count, which is verification setup.

---

## Infrastructure: the blast-radius rule and plan-only IaC

**Trigger.** A Terraform change or review; role and grant changes; cluster or
runtime upgrades; certificate rotation; backup and DR work.

**The record, the worst in the estate.** 19.36 percent pass@1 on Terraform against
86.6 percent on Python for the best model on IaC-Eval, a NeurIPS 2024 benchmark
(https://proceedings.neurips.cc/paper_files/paper/2024/hash/f26b29298ae8acd94bd7e839688e329b-Abstract-Datasets_and_Benchmarks_Track.html),
and generated IaC passes TFLint and Checkov while still doing the wrong thing
(https://arxiv.org/html/2509.05303, September 2025 arXiv paper). The incident
record is concrete: in February 2026 an agent-driven Terraform destroy took out a
production estate, database snapshots included, off a stale state file
(https://incidentdatabase.ai/cite/1424/); in July 2025 a production database was
deleted during an explicit code freeze, then misreported by the agent, an account
resting on the affected founder's own public posts
(https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/,
single source). A policy stated in a prompt is not a control; controls live in
credentials and platform policy.

**Drafts.** Plan-only IaC is the default, no exceptions: agents draft plans, humans
apply. Inside that boundary the work is real: explaining failed runs; summarizing
plan diffs (replaced versus updated, irreversible changes, moved pins); drafting a
module from an existing sibling; writing missing policy checks; reconciling
documentation against actual resources. Grant changes arrive with the transitive
closure of who gains access, generated by query, never summarized by the agent that
wrote the change.

**Decides / refuses.** No agent holds apply rights on production state (SKILL
section 5, the blast-radius rule): blast radius follows credentials, not
capability. A production database and its volume backups were deleted in nine
seconds on a standing token
(https://zenity.io/blog/current-events/ai-agent-database-deletion-pocketos, single
source). Scope what the credential can reach; never rely on what the agent was
instructed to do. Destroy is denied by policy and deletion protection is enabled
separately, so the control survives a bad prompt.

**Gate.**

- **HARD (approval, class 2):** any change touching money or a partner path (and,
  by the blast-radius rule, production apply) carries a named human approval bound
  to more than a typed name. The approval gate accepts exactly two forms, and they
  are not equally strong: a signed commit trailer whose signature this host
  verified, which an agent cannot produce without the private key, or a recorded
  platform review id, which nothing resolves and an agent therefore CAN type. A
  typed name alone FAILS. Use the signature path where the approval has to hold
  against the agent itself; use the review id where you have a CI step that
  resolves it, or knowing it is a pointer rather than a control.

  ```
  # in the commit message body, on a GPG-signed commit:
  Approved-by: A Real Human

  # or, bound to a platform review:
  Reviewed-in: PR-4821
  ```

  `python3 tools/sbe_gate.py approval` reads HEAD. It PASSes an `Approved-by:`
  trailer only when git reports the commit signature as G (a signature this
  host verified against a trusted key); a valid-but-untrusted signature (U) is
  NO-DATA, a signature it could not check is NO-DATA, and so is a
  `Reviewed-in:` id, which nothing resolves. It FAILs an `Approved-by:`
  name on an unsigned commit, because a name in a text field is not a control
  (`typed-name-approval-caught` in the evals). An `APPROVAL` file beside the change
  declares that the change touches a money or partner path; with no APPROVAL file
  and no trailer the verdict is NO-DATA, not FAIL (`no-approval-needed-is-nodata`).
- Remote state is ground truth; a plan built on stale state is discarded, not
  reviewed. Separate state, accounts, and credentials per environment.
  Policy-as-code runs as blocking CI (it proves syntax, not intent).
- Review order is fixed: read the plan's delete and replace lines before reading
  the code. That is where the 19 percent lives.

**Entry cost.** Thirty minutes on the next Terraform PR you were reviewing anyway:
the agent reads plan plus diff and states replaced resources, irreversible changes,
and moved pins, then you review as normal; a wrong summary is a calibration
reading. About 45 minutes, read-only, for grant-inventory introspection with a
select-only role, abandonable by revoking one role.

---

## Migrations: the reverse is a receipt, not a promise

Migrations are their own silent class (class 2) wherever they appear, backend or
warehouse. A migration without a tested reverse is a one-way door.

**Drafts.** The forward change and its reversal, both rehearsed against a restored
copy of production-shaped data, with row counts captured either side.

**Decides / refuses.** BrotherSBE does not call a migration done on inspection.
Forward and reverse both run against a restore, and the reverse carries a
rehearsal run id recorded as a string. Nothing resolves that id against a job
system, so it points a human at a rehearsal rather than proving one ran.

**Gate. HARD (migration, class 2).** The receipt is a `migration-receipt.json`:

```json
{"forward": {"ran_against_restore": true},
 "reverse": {"ran_against_restore": true, "rehearsal_run_id": "job_8842"},
 "row_counts": {"before": 100, "after_reverse": 100}}
```

`python3 tools/sbe_gate.py migration` passes that. It FAILs a reverse that never
ran against a restore (`untested-reverse-caught`), a reverse with no
`rehearsal_run_id` (`unresolvable-rehearsal-id-caught`), an id that is not a string
(`non-string-rehearsal-id-is-caught`, where `true` used to satisfy a bare truthiness
test), a lossy reverse where `row_counts.before` and `after_reverse` disagree
(`lossy-reverse-caught`, 100 became 61), and a half-recorded count
(`half-a-row-count-is-caught`). A receipt with no row counts at all is NO-DATA
(`migration-with-no-row-counts-is-nodata`), because the gate cannot assert the half
it never read. All are in the evals.

---

## Performance: profile first, never guess

**Trigger.** p99 crept up; consumers lag a queue; a report query takes four
minutes; the platform bill needs explaining; a sizing or clustering change is
proposed.

**The record.** LLM-proposed optimizations underperform human ones on real tasks
(https://arxiv.org/abs/2510.15494, October 2025 arXiv paper), and agents under an
optimization harness produce evaluator-specific shortcut speedups, correctness
regressions, and gains that are measurement artifacts
(https://arxiv.org/html/2607.07744v1, July 2026 arXiv preprint): an agent
optimizes what the harness measures, so the harness is the product. The best-known
commercial autonomous database tuner is dead; teams that delegated tuning absorbed
it back on short notice, per one practitioner's January 2025 retrospective
(https://www.cs.cmu.edu/~pavlo/blog/2025/01/2024-databases-retrospective.html,
single source).
Across six-plus vendor blogs claiming 30 to 70 percent savings, none disclosed
methodology; a savings claim without a rerunnable query is an anecdote.

**Drafts.** The benchmark and the diff. Profiling comes first, always: a
deterministic profiler finds the hotspot, and the agent interprets, returning
ranked candidate causes each with its evidence in the profile. The profile, not the
prose, names the hotspot. Query tuning is deferred to deterministic advisors run
against production-copy benchmarks (https://pganalyze.com/blog/index-advisor-v3).

**Decides / refuses.** BrotherSBE refuses hotspot identification by intuition,
optimization without a before-and-after profile, any change under production load,
and the warehouse size knob as a first move: credit rates double per size step, so
a claimed 75 percent saving from downsizing is an arithmetic identity silent on
whether the workload still completes
(https://www.anavsan.com/blog/snowflake-warehouse-optimization-beyond-auto-suspend/,
vendor source).

**Gate.**

- Benchmark-carrying perf PRs: no performance change merges without its benchmark
  in the PR. The benchmark is production-shaped replayed load, with a correctness
  check the agent cannot see, re-run after the change on the same rig. Query work
  adds a plan comparison on either side.
- **HARD (numbers and ran, classes 1 and 3) for cost work:** every cost change
  carries the same workload run before and after, reporting runtime, credits, and
  queue time together, because two improving while the third degrades is this
  doctrine's classic failure. Any cost number sent upward is verified by hand
  (numbers manifest, second derivation) before it reaches anyone.

**Entry cost.** Twenty minutes: paste the top frames of a profile you already
captured plus the source file, ask for three ranked candidate causes with their
evidence in the profile, and do not ask for a patch. Fifteen minutes of rate-card
arithmetic on any model call proposed inside a pipeline, before credits are spent.

---

## The silent-failure lints (gate severity by ratified decision)

Separate from the four receipt gates, `tools/sbe_score.py` scans your source for
five code patterns that hide an error so a wrong result passes for a right one:

- bare `except:` (catches everything, hides the real error)
- except-then-`pass` (swallows the error)
- a conflict-skipping upsert (`ON CONFLICT ... DO NOTHING`) without a logged skip
  count
- a discarded `subprocess.run/call/Popen` result without `check=True` (the exit
  code is swallowed)
- Swift `try!` (force-try discards the error)

These are gate severity by ratified decision. A genuine, reviewed exemption carries
a visible marker on the line, so the exemption is auditable in the diff. One of the
real exemptions in this repository, quoted verbatim from `tools/sbe_telemetry.py` (find
every one, with its current line, by running `grep -n "sbe: allow-silent" tools/*.py`; a
line number or a count written into prose is a claim nothing recomputes, and both of the
ones this paragraph used to carry had rotted):

```python
        except OSError:  # sbe: allow-silent boundary handler in a non-blocking hook; the miss surfaces as absent data, never as a false pass
```

The two quotes either side of this one are verbatim source, and this one used to be
an invented illustration written in the same frame, which a reader takes for a
receipt.

The scan is opt-in and scoped to the tree you name, so it never fires on an
unrelated repo:

```
python3 tools/sbe_score.py /path/to/your/worktree
# or
SBE_LINT_ROOT=/path/to/your/worktree python3 tools/sbe_score.py
```

`sbe_score.py` is advisory locally (always exits 0, never blocks a session) and
`--strict` in CI (nonzero exit on any FAIL). The `silent-failure-lints` check
names each hit by file and line.

---

## No published evidence, standing down

Where the record shows agents do not help, or shows nothing at all, BrotherSBE says
"no published evidence" and stands down rather than reaching for a weaker source
(DIGEST.md, the evidence line). The standing list, as of v1:

- **Autonomous FinOps action.** BrotherSBE explains cost and drafts the arithmetic;
  it does not act on spend autonomously.
- **Self-healing tests without a correct-heal base rate.** No published rate for how
  often an auto-heal produces the correct fix, so the pattern is not adopted.
- **Agent-authored partner connectors.** No published evaluation for
  agent-authored connectors, CDC configuration, or streaming correctness.
- **Agent-authored zero-downtime migrations.** No published evidence; the migration
  gate governs the reversible cases, and the zero-downtime cutover stays human.
- **Agent-generated runbook accuracy.** No published accuracy figure, so a
  generated runbook is a draft a named human validates.
- **Agent-designed architecture end to end.** No published evidence; architecture,
  grain, and semantics stay with the senior engineer.

Absence of evidence is a reason to gate mechanically or stand down, never a gap to
paper over with confidence. One honest scope note: these guarantees are over
BrotherSBE's own laws. A vendor model or harness update can change behavior with no
change here, so the standing list is re-checked, not assumed.

---

## What the doctrines share

Every doctrine opens with its failure record, negatives first. Every gate is a file
in version control, a hook, a CI check, or a query that runs and leaves a receipt.
Every entry point sits inside work already scheduled, priced in honest units:
minutes for the debugging and backend loops, and openly higher (half a day for the
warehouse, because the diffing-against-production is the cost) for the data-platform
work where verification setup dominates. The doctrines are not a promise that agents
work. They are the conditions under which the parts that work can be trusted, and
the parts that do not are caught before they cost anything.

BrotherSBE is the domain specialist sibling of BrotherModeUp
(github.com/khalilmaaouni/BrotherModeUp), whose coordination, telemetry, and
self-improvement chassis it adapts. What this guide adds is the domain: the four
silent-failure classes held as hard gates, and the six doctrines that decide, per
task, where an agent helps and where it stands down.
