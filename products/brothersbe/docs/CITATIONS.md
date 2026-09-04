# Citation inventory

One entry per external URL cited anywhere in this repository's shipped
documentation. The scanned set is DERIVED rather than curated: when the root
ships a CHECKSUMS.sha256 manifest, that manifest IS the shipped-file list and
its markdown entries are the set; otherwise every .md under the root is walked,
hidden directories skipped. It was once the fixed tuple README.md, SKILL.md and
docs/, and under that tuple a citation planted in a shipped top-level page such
as PROJECT.md or SECURITY.md was never opened and so never checked.
Every entry carries four fields, and all four are required: the claim this
repository rests on the page, the population the claim measured, the date or
version it belongs to, and the limit a reader should carry with it. The
`citation-inventory` check in tools/sbe_score.py fails a strict run when a URL
appears in those documents without an entry here, when an entry is missing or
padding any of the four fields, or when an entry names a URL no document still
cites. The check verifies structure and coverage offline; it never opens the
network, so nothing here is a claim that a page still says today what it said
when its entry was recorded. Each entry's date field says when its content was
captured.

## https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/backfill.html
- claim: the orchestrator's own documentation warns that backfill can reprocess already-completed dates
- population: Apache Airflow stable documentation, backfill concept page
- date: current stable docs, captured July 2026
- limit: vendor documentation describing behavior, not a measurement

## https://arxiv.org/abs/2305.00418
- claim: over 80 percent coverage on a curated benchmark against under 2 percent on a realistic one
- population: the test-generation evaluation reported in that paper
- date: 2023 arXiv paper
- limit: figures as carried by this repository from the paper's headline result, one study

## https://arxiv.org/abs/2504.16833
- claim: LLM extraction covered 48.85 percent more missed entities than developer-provided specs
- population: the OpenAPI extraction evaluation reported in that paper
- date: April 2025 arXiv paper
- limit: single source, one evaluation setup

## https://arxiv.org/abs/2510.15494
- claim: LLM-proposed optimizations underperform human ones on real tasks
- population: the optimization tasks evaluated in that paper
- date: October 2025 arXiv paper
- limit: only the direction of the finding is carried here, one study

## https://arxiv.org/abs/2601.08778
- claim: annotation error rates of 52.8 percent in BIRD Mini-Dev and 62.8 percent in Spider 2.0-Snow; rankings track the full dev set at Spearman 0.85 but the corrected subset at 0.32 with p=0.23, not significant
- population: BIRD Mini-Dev and Spider 2.0-Snow gold labels under expert re-examination, one error rate per benchmark family
- date: preprint submitted 13 January 2026
- limit: not peer reviewed; the two correlations are different comparisons, not one measurement moving

## https://arxiv.org/abs/2606.03363
- claim: 15.9 percent accuracy on enterprise SQL with internal conventions
- population: the enterprise SQL benchmark reported in that paper
- date: June 2026 arXiv paper
- limit: single source, one enterprise setting

## https://arxiv.org/html/2405.15729v1
- claim: 29 percent of OpenAPI completions were correct while 68 percent were merely valid documents
- population: the OpenAPI completion evaluation reported in that paper
- date: May 2024 arXiv paper, v1
- limit: single source, one evaluation setup

## https://arxiv.org/html/2509.05303
- claim: generated IaC passes TFLint and Checkov while still doing the wrong thing
- population: the generated IaC samples studied in that paper
- date: September 2025 arXiv paper
- limit: qualitative direction carried here, one study

## https://arxiv.org/html/2607.07744v1
- claim: agents under an optimization harness produce evaluator-specific shortcut speedups, correctness regressions, and gains that are measurement artifacts
- population: the agent optimization harness studied in that paper
- date: July 2026 arXiv preprint, v1
- limit: preprint, one harness

## https://bird-bench.github.io/
- claim: human 92.96 against best system 81.95 on BIRD
- population: the BIRD leaderboard's published human baseline and top system entry
- date: figures as captured at the doc's writing, July 2026
- limit: self-submitted scores on a moving leaderboard; the numbers change as entries land

## https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report
- claim: the 2025 report reverses the 2024 throughput direction while the stability penalty persists
- population: DORA's annual survey of professionals in technical roles, 2025 edition
- date: 2025 DORA report announcement
- limit: self-report survey with modeled associations, not telemetry

## https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report
- claim: AI adoption associated with an estimated 1.5 percent decrease in delivery throughput and 7.2 percent decrease in delivery stability
- population: DORA's annual survey of professionals in technical roles, 2024 edition
- date: 2024 DORA report announcement
- limit: self-report survey with modeled associations, not telemetry; the page's 25 percent adoption increment attaches to its positive findings, not to these two figures, and the 2025 report reverses the throughput direction

## https://datahub.com/blog/extracting-column-level-lineage-from-sql/
- claim: lineage parsers on one corpus ranged from 88 percent column coverage down to 29 to 38 percent
- population: SQL lineage parsers over one corpus chosen by the vendor
- date: vendor blog, captured July 2026
- limit: the winning vendor's own benchmark

## https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026
- claim: same questions and models scored 64.5 percent on raw third-normal-form schemas, 90.0 modelled, 98.2 through a semantic layer
- population: eleven questions across three schema treatments
- date: 2026 vendor study
- limit: vendor study, n=11, points to be read loosely; the mechanism, not the point estimates, is what this repository relies on

## https://docs.getdbt.com/docs/build/snapshots
- claim: CDC snapshot keys that merely look unique record nothing wrong at the time and lose history permanently
- population: dbt snapshot documentation
- date: current docs, captured July 2026
- limit: vendor documentation describing behavior, not a measurement

## https://engineering.fb.com/2024/06/24/data-infrastructure/leveraging-ai-for-efficient-incident-response/
- claim: 42 percent one-shot root-cause accuracy
- population: one company's first-party production incident response system
- date: June 2024
- limit: vendor figure, single source

## https://github.com/khalilmaaouni/BrotherModeUp
- claim: the general orchestrator sibling whose chassis this skill adapts
- population: a repository link, not a measurement
- date: current repository
- limit: self-reference to a sibling project, carries no evidence weight

## https://github.com/khalilmaaouni/BrotherSBE
- claim: this repository's own clone location, which the publish checklist expects to return HTTP 200 once published
- population: one URL, a repository link rather than a measurement; the checklist checks availability with its own curl command
- date: current repository, recorded July 2026
- limit: self-reference carrying no evidence weight, and an availability target rather than a factual claim; the checklist re-checks it at publish time and this inventory does not

## https://github.com/oasdiff/oasdiff
- claim: a breaking-change differ that can be wired into CI
- population: a tool repository, not a measurement
- date: current repository, captured July 2026
- limit: tool reference, no figure rests on it

## https://github.com/stoplightio/spectral
- claim: a spec linter that can be wired into CI
- population: a tool repository, not a measurement
- date: current repository, captured July 2026
- limit: tool reference, no figure rests on it

## https://grafana.com/press/2026/03/18/grafana-labs-4th-annual-observability-survey-reveals-a-field-at-a-crossroads-ai-economics-complexity-and-the-enduring-power-of-open-source/
- claim: alert engagement drops roughly 15 percent past 50 alerts per channel per week
- population: respondents to one vendor's fourth annual observability survey
- date: March 2026
- limit: vendor survey, self-report

## https://incidentdatabase.ai/cite/1424/
- claim: an agent-driven Terraform destroy took out a production estate including database snapshots off a stale state file
- population: one recorded incident, id 1424
- date: incident dated 26 February 2026
- limit: a single incident record; evidence of blast radius, not of frequency

## https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pipeline_chain_operators
- claim: the `&&` and `||` pipeline chain operators were introduced in PowerShell 7, so a step using them cannot parse in Windows PowerShell 5.1
- population: the PowerShell language reference for pipeline chain operators
- date: page as captured 2026-08-18
- limit: vendor documentation describing its own language, not a measurement on a real 5.1 host; nobody on this project has a Windows machine to run it on, which is why docs/WINDOWS-CHECK.md exists

## https://metr.org/blog/2025-06-05-recent-reward-hacking/
- claim: o3 gamed the grading harness on 30.4 percent of RE-Bench runs, 39 of 128, against 0.7 percent on HCAST, 8 of 1,087
- population: o3 runs on RE-Bench and HCAST, from a table captioned for o3's behavior
- date: 5 June 2025
- limit: single source; one model's figures, and the page's other models are not these numbers

## https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/
- claim: 16 experienced developers on 246 real issues were measured 19 percent slower with AI after forecasting 24 percent faster, and still believed afterwards they had been 20 percent faster
- population: 16 experienced developers from large mature open-source repositories, 246 issues
- date: early-2025 study, published 10 July 2025
- limit: one randomized trial on experienced developers in mature repositories; METR's February 2026 follow-up must be read beside it

## https://metr.org/blog/2026-02-24-uplift-update/
- claim: an estimated speedup of minus 18 percent, interval minus 38 to plus 9 percent, for the 10 returning developers, and minus 4 percent, interval minus 15 to plus 9, for 47 newly recruited developers
- population: 10 developers returning from the original study plus 47 newly recruited ones
- date: 24 February 2026
- limit: METR's own page calls the new data an unreliable signal, and states that non-participation by developers unwilling to work without AI likely biases its speedup estimate downward

## https://pganalyze.com/blog/index-advisor-v3
- claim: a deterministic index advisor exists for query tuning against production-copy benchmarks
- population: one vendor's tool documentation
- date: advisor v3, captured July 2026
- limit: tool reference, vendor documentation, no figure rests on it

## https://proceedings.neurips.cc/paper_files/paper/2024/hash/f26b29298ae8acd94bd7e839688e329b-Abstract-Datasets_and_Benchmarks_Track.html
- claim: 19.36 percent pass@1 on Terraform against 86.6 percent on Python for the best model on IaC-Eval
- population: models evaluated on the IaC-Eval benchmark
- date: NeurIPS 2024 Datasets and Benchmarks track
- limit: benchmark figures for the best model at publication, not for current models

## https://spider2-sql.github.io/
- claim: GPT-4o scored 10.1 percent on Spider 2.0 against 86.6 percent on Spider 1.0 when Spider 2.0 was published; purpose-built agents have since pushed the Spider 2.0-Snow leaderboard past 96 percent
- population: GPT-4o in the 2024 Spider 2.0 paper, and the site's live Snow leaderboard entries
- date: paper 2024; leaderboard as read July 2026
- limit: the leaderboard is live and moving, and the site itself notes scores may change as evaluation metrics are re-checked; neither number is evidence about any particular warehouse

## https://tianpan.co/blog/2026-04-10-text-to-sql-failure-modes-production
- claim: join fan-out returns revenue several times too high with no error raised
- population: production text-to-SQL failure modes described by one practitioner
- date: April 2026
- limit: single source for the framing, practitioner blog

## https://www.anavsan.com/blog/snowflake-warehouse-optimization-beyond-auto-suspend/
- claim: credit rates double per warehouse size step, so a claimed saving from downsizing is an arithmetic identity silent on workload completion
- population: Snowflake warehouse sizing arithmetic
- date: vendor blog, captured July 2026
- limit: vendor source; the arithmetic is checkable, the framing is theirs

## https://www.anomalo.com/blog/chapter-5-making-data-quality-monitoring-models-work-in-the-real-world/
- claim: no commercial data quality product publishes a false-positive rate; this vendor's own book defines the metrics and publishes neither
- population: one vendor's published book chapter
- date: captured July 2026
- limit: a negative claim resting on one vendor's own text; absence of publication, not a measured rate

## https://www.astronomer.io/blog/state-of-airflow-2026/
- claim: 9 percent of more than 5,800 surveyed data professionals are satisfied with AI-generated pipeline definitions, 43 percent citing hallucinations and 42 percent outdated syntax
- population: more than 5,800 surveyed data professionals
- date: 2026 State of Airflow survey
- limit: vendor survey, single source, published against the vendor's own commercial interest

## https://www.cs.cmu.edu/~pavlo/blog/2025/01/2024-databases-retrospective.html
- claim: the best-known commercial autonomous database tuner is dead, and teams that delegated tuning absorbed it back on short notice
- population: the 2024 databases retrospective's account of one product's shutdown
- date: January 2025
- limit: one practitioner's blog, single source

## https://www.techtarget.com/searchdatamanagement/news/366622933/Monte-Carlo-launches-first-agents-for-data-observability
- claim: machine-recommended monitors carry a 60 percent human acceptance rate, two in five rejected on review
- population: one vendor's reported figure for its own agents
- date: trade-press article, captured July 2026
- limit: vendor claim carried by trade press, single source

## https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/
- claim: a production database was deleted during an explicitly declared code freeze, then misreported by the agent
- population: one incident on one platform, affecting one founder's project
- date: 21 July 2025
- limit: single source resting on the affected founder's own public posts; the platform had not responded within the cited article

## https://zenity.io/blog/current-events/ai-agent-database-deletion-pocketos
- claim: a production database and its volume backups were deleted in nine seconds on a standing token
- population: one reported incident
- date: page undated in this capture; entry recorded July 2026
- limit: vendor incident writeup, single source

## https://docs.obsidian.md/plugins/guides/bases-view
- claim: Obsidian's own developer guide for the Bases plugin warns that an unfiltered Base "will provide an entry for every file in the vault," and tells view authors to virtualize rendering for that reason
- population: docs/TEAM-PLAYBOOK.md and memory-template/TEAM-VAULT.md, both citing it to justify scoping every shared Base with a filter
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: vendor documentation of the plugin's current behavior, not a measurement of this repository's own vault size or render time

## https://eu.36kr.com/en/p/3755031628005892
- claim: the Obsidian company (7 full-time staff at the time reported) runs its own internal task planning, PRDs, roadmaps and checklists from one shared Obsidian vault, alongside GitHub for code review and separate chat software for day-to-day talk
- population: memory-template/TEAM-VAULT.md's framing of the vault as a planning and knowledge layer, not a replacement for issue tracking or chat
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: a single company's reported practice at one point in time, not a claim about how any other team should run its vault

## https://forum.obsidian.md/t/slow-performance-with-large-vaults/16633
- claim: community forum reports document unusable link-autocomplete latency, slow cache loading and slow search once an Obsidian vault reaches roughly the 1,000 to 40,000-plus note range
- population: memory-template/TEAM-VAULT.md's guidance against building one mega-vault
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md, cross-checked against a second forum thread)
- limit: anecdotal forum reports, not a controlled performance benchmark; note counts and device specs vary across reporters

## https://forum.obsidian.md/t/team-colaboration/69608
- claim: a `.gitattributes` union-merge driver for `*.md` files plus a gitignored `.obsidian/` folder is the practitioner-reported fix for git-synced team vault merge conflicts, appropriate for mostly-additive notes but wrong for notes where two people might edit the same sentence
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/05-data-model.md and memory-template/TEAM-VAULT.md, all citing it for the union-merge tradeoff
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: a single community forum thread's reported practice, not a formal git or Obsidian specification

## https://github.com/Vinzent03/obsidian-git
- claim: the obsidian-git community plugin auto-commits, pulls and pushes from inside Obsidian, but its own docs flag mobile support as "highly unstable," with no SSH auth on mobile, no rebase, no submodules, and warn it may crash on clone or pull on large repositories
- population: memory-template/TEAM-VAULT.md's statement that desktop is the supported case
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: a third-party community plugin's own documentation of its current limitations, which can change with future releases

## https://github.com/blacksmithgu/obsidian-dataview
- claim: Dataview is a third-party community plugin, not core, that indexes YAML frontmatter and inline key-value fields across the vault and exposes a query language plus a JavaScript API for live tables, lists and task views
- population: memory-template/TEAM-VAULT.md's description of the plugin used for the vault's live views
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: vendor documentation of the plugin's current feature set, not a measurement of this repository's own dashboard performance

## https://obsidian.md/help/sync/collaborate
- claim: Obsidian's own help page states plainly that Obsidian "does not yet support collaborative live editing on the same file," with no live cursors or presence indicators, and Sync merges offline edits automatically or falls back to version history when it cannot auto-merge
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/01-purpose.md, design/team-operating-model/05-data-model.md and memory-template/TEAM-VAULT.md, all citing it for the no-live-co-editing limit the design works around
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: vendor documentation of current product behavior, which Obsidian could change in a future release

## https://obsidian.md/sync
- claim: Obsidian's own official Sync marketing copy uses the phrase "real-time note updates across team devices," which the research file treats as "propagates fast once synced" rather than literal simultaneous editing, since it is in tension with Obsidian's own Collaboration help page
- population: memory-template/TEAM-VAULT.md's note that the two official Obsidian pages are in tension
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: marketing copy read against a second official page from the same vendor, not an independent test of sync latency

## https://ravoid.com/blog/obsidian-vs-confluence-knowledge-stack-decision/
- claim: Obsidian's collaboration features are missing standard enterprise controls outright: no native comments, no at-mentions, no per-folder or per-file permissions, no audit log, no SSO or SCIM
- population: design/team-operating-model/01-purpose.md and memory-template/TEAM-VAULT.md, both citing it for the enterprise-control gap list
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md, corroborated against a second comparison piece describing the same gaps)
- limit: a comparison blog post's characterization of a moving product, not Obsidian's own documentation of the gap list

## https://www.dsebastien.net/the-complete-guide-to-obsidian-properties/
- claim: inconsistent frontmatter property names or types (a string in one note, a list in another) is the single most common reason team dashboards silently miss rows, so properties should be treated as a schema enforced through a template rather than typed freehand
- population: memory-template/TEAM-VAULT.md's guidance on enforcing property schemas
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md, alongside a second practical guide making the same point)
- limit: practitioner guidance rather than a measured failure rate; the "single most common reason" framing is the source's own claim, not independently counted by this repository

## https://github.com/obra/superpowers
- claim: the project describes itself as a complete software development methodology for coding agents, built on composable skills, running brainstorming then design then plan then subagent-driven execution with test-driven development
- population: the project's own README on its GitHub repository landing page
- date: read 2026-08-11
- limit: this is the project's self-description, not an independent assessment of what it achieves, and a README changes without notice

## https://greatexpectations.io/
- claim: GX Core is described by its makers as the engine of the GX data quality platform, helping teams understand what to expect from their data by defining expectations, validating continuously, and acting when issues are detected
- population: the vendor's own product landing page
- date: read 2026-08-11
- limit: vendor marketing copy describing intent, not a measured comparison of data quality tools, and landing pages are rewritten frequently

## https://github.com/khalilmaaouni/Brother
- claim: the umbrella repository this product points at, carrying COORDINATION.md, the shared chain, the verdict tuple and the evidence law across the three products; the README section added by this branch sends a contributor there before they add a command, an agent, a hook or a state vocabulary
- population: one repository, public, created 2026-08-22, carrying COORDINATION.md at 9,482 bytes; read on 2026-08-23 through the provider API, 0 stars and 0 forks at that reading
- date: read 2026-08-23; the repository is under daily change, so the byte count and the counts above are a snapshot of that reading and not a standing figure
- limit: a repository this estate owns and writes, so it is a record of our own intent rather than independent evidence of anything. It is cited here because this branch's README sends a reader to it, and the check that requires this entry is about disclosing that dependency, not about endorsing the destination. Nothing in this repository's behaviour rests on its contents; the coupling is documentary only
