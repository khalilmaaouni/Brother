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

## https://claude.ai/code/artifact/12edaca1-252d-407b-8eff-f5c493747496
- claim: none about the outside world; this is where this project's own progress board is published, cited by PROJECT.md so a reader can reach the current state of the plan
- population: this repository's own GANTT.html, republished to that one URL; it covers this project and nothing else
- date: refreshed per session; the page carries its own refresh stamp, and the stamp on the page is the only date that governs it
- limit: SELF PUBLISHED by this project, so it is a record rather than evidence and must never be cited as corroboration; a box on it is ticked only when a check ran after the last edit and its output is quoted beside it. FOUR board links before this one have ceased to exist, the most recent being 8d3b4d6d which this entry replaced on 2026-08-18, so verify the link resolves before repeating it to anyone. A claude.ai artifact is also private to its owner unless separately shared, so a reader arriving from this now-public repository should expect no access rather than a live board.

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

## https://api.github.com
- claim: the REST endpoint the sbe pr verify spec names as its only transport, GET requests with a bearer token held in memory
- population: one URL, the API root the specced client builds its requests against; no live call is made by any doc or test on this machine without a token
- date: spec written July 2026 against the GitHub REST API as published at that time
- limit: an endpoint reference rather than a measured fact; API shapes drift, and the spec's fixtures pin canned response shapes, not the live service

## https://github.com/khalilmaaouni/BrotherSBE
- claim: this repository's own clone location, which the publish checklist expects to return HTTP 200 once published
- population: one URL, a repository link rather than a measurement; the checklist checks availability with its own curl command
- date: current repository, recorded July 2026
- limit: self-reference carrying no evidence weight, and an availability target rather than a factual claim; the checklist re-checks it at publish time and this inventory does not

## https://github.com/khalilmaaouni/BrotherSBE.git
- claim: the clone URL install.sh uses and the book's install chapter shows, the git suffix form of the entry above
- population: one URL, a repository link rather than a measurement; install.sh reaches it with git rather than curl
- date: current repository, recorded July 2026
- limit: self-reference carrying no evidence weight; whether a clone succeeds depends on the reader's network and credentials, which nothing here checks

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

## https://brothersbe.dev/install
- claim: the master plan proposes this as the future one-command universal installer endpoint for macOS and Linux
- population: the delivery blueprint in program/MASTER-PLAN.md, section 7.2
- date: plan dated 2026-07-31
- limit: planned endpoint only; the domain serves nothing today and no installer exists, which the plan itself gates behind wave 3

## https://brothersbe.dev/install.ps1
- claim: the master plan proposes this as the future one-command universal installer endpoint for Windows
- population: the delivery blueprint in program/MASTER-PLAN.md, section 7.2
- date: plan dated 2026-07-31
- limit: planned endpoint only; the domain serves nothing today and no installer exists, which the plan itself gates behind wave 3

## https://claude.com/claude-code
- claim: the product page a tester follows to obtain the Claude Code client this plugin requires
- population: the vendor's own product landing page for Claude Code
- date: linked from TESTERS.md, captured 2026-08-06
- limit: vendor page naming its own product, not a measurement, and its install instructions may change without notice here

## https://github.com/khalilmaaouni/BrotherSBE/issues/new?template=first-run.yml
- claim: the issue form testers file findings through, prefilled with this repository's first-run report template
- population: this repository's own GitHub issue tracker, first-run report template
- date: template added 2026-08-06, merged with the former tester-report.md template 2026-08-08
- limit: a link into this repository's own tracker, so it carries no external evidence at all, and it resolves only while that template file exists

## https://claude.com/plugins
- claim: Claude's public plugin browser exists as a distribution surface the plan targets
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: interface and marketplace requirements can change; adapter tests, not this link, are the compatibility truth

## https://github.com/MoonshotAI/kimi-code
- claim: Kimi Code is a coding host the plan targets for a tier 1 adapter
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: integration target only; no adapter exists and no compatibility is claimed

## https://github.com/QwenLM/qwen-code
- claim: Qwen Code is a coding host the plan targets for a tier 1 adapter
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: integration target only; no adapter exists and no compatibility is claimed

## https://github.com/anthropics/claude-plugins-official
- claim: the official Claude plugin directory is the plan's primary distribution target
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: submission and acceptance are external decisions this repository cannot time or guarantee

## https://github.com/google-gemini/gemini-cli
- claim: Gemini CLI is a coding host the plan targets for a tier 1 adapter
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: integration target only; no adapter exists and no compatibility is claimed

## https://openai.com/codex/
- claim: OpenAI Codex is a coding host the plan targets for a tier 1 adapter
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: integration target only; no adapter exists and no compatibility is claimed

## https://opencode.ai/docs
- claim: OpenCode is a provider-neutral coding host the plan targets for a tier 1 adapter
- population: the plan's ecosystem reference list, section 15
- date: checked by the plan's author on 2026-07-31
- limit: integration target only; no adapter exists and no compatibility is claimed

## https://code.claude.com/docs/en/plugins
- claim: the official plugin directory is curated by Anthropic with no application process, and the community marketplace accepts submissions through a web form gated on plugin validation and automated safety screening
- population: the plugins page of the official Claude Code documentation, opened and quoted in the directory submission packet
- date: opened by the research pass on 2026-08-01
- limit: process and coverage as stated on that date; Anthropic can change either without notice, and this repository controls neither

## https://platform.claude.com/plugins/submit
- claim: the community marketplace submission form for individual accounts
- population: the submission entry point the documentation names for individuals
- date: opened by the research pass on 2026-08-01
- limit: entry point only; nothing was submitted through it and no acceptance is implied

## https://claude.ai/admin-settings/directory/submissions/plugins/new
- claim: the community marketplace submission form for Team and Enterprise organizations
- population: the submission entry point the documentation names for organizations
- date: named by the documentation opened on 2026-08-01; requires an organization login this research did not hold
- limit: entry point only, recorded from the documentation rather than from a page this machine could open; nothing was submitted and no acceptance is implied

## https://agilealliance.org/glossary/information-radiators/
- claim: the term "information radiator" (interchangeable with "Big Visible Chart") names any handwritten, printed or electronic display placed where the team and passersby see it at a glance, tracing to Kent Beck's 1999 coinage and Alistair Cockburn's 2001 term
- population: docs/TEAM-PLAYBOOK.md, in the section on keeping status visible without a status meeting
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: a glossary entry defining a term, not a measurement that this repository's own displays work as described; recorded from the research file's summary, not a page this machine reopened

## https://basecamp.com/shapeup/2.2-chapter-08
- claim: Basecamp's Shape Up "betting table" is a small, fixed-membership ritual held once per six-week cycle, reviews only pitches that were written up with no running backlog, rarely runs past one to two hours, and its decision is final
- population: docs/TEAM-PLAYBOOK.md's description of the periodic prioritization ritual it borrows the shape from
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: describes one company's named process, not evidence that adopting the shape produces the same outcome here; recorded from the research file's summary, not a page this machine reopened

## https://businessmap.io/kanban-resources/getting-started/what-is-wip
- claim: work-in-progress limits are what convert a board into a pull system, with a practical starting formula of team-member-count plus one
- population: docs/TEAM-PLAYBOOK.md's WIP-limit guidance
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: a vendor how-to page's own framing and rule of thumb, not a measured outcome for this repository's board; recorded from the research file's summary, not a page this machine reopened

## https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance
- claim: Google Cloud's own blog states the "Elite" DORA performance tier stopped appearing in DORA's clustering after the 2022 report
- population: design/team-operating-model/07-verification.md and docs/TEAM-PLAYBOOK.md, both citing it alongside dora.dev when describing the current DORA metric set
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md, cross-checked against dora.dev)
- limit: one vendor blog's framing of another organization's research; it does not establish that this repository's own delivery reaches, or should target, any DORA tier

## https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- claim: Michael Nygard's original 2011 post defines ADRs as small, numbered, append-only decision records that are superseded rather than edited
- population: design/team-operating-model/03-adr.md and design/team-operating-model/05-data-model.md, both citing it for the append-only ADR pattern this repository adopts
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md)
- limit: describes the pattern's original definition, not a claim that this repository's own ADR practice matches it in every particular

## https://confluence.atlassian.com/doc/page-properties-macro-184550024.html
- claim: Atlassian's own documentation states Page Properties macro metadata "is not possible to reference... from within the page, or anywhere else," meaning it is not exposed as queryable data through the REST API
- population: design/team-operating-model/03-adr.md and design/team-operating-model/05-data-model.md, both citing it to justify not relying on Page Properties for structured evidence storage
- date: opened by the research pass on 2026-08-01 (team-research/r1-jira-confluence.md)
- limit: vendor documentation of a current product limitation Atlassian could change without notice; not independently reverified by this machine

## https://cucumber.io/blog/bdd/five-roles-in-a-healthy-mob/
- claim: a healthy mob has five distinct roles (Navigator, Driver, Facilitator, Scout, Housekeeper), and the Facilitator's job is explicitly not technical: keep time, manage rotation, enforce breaks, prompt reflection, watch for kindness under stress
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/02-process.md, both citing it for the facilitator role definition
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: one blog's role taxonomy for mob programming, not a controlled study; adopting the taxonomy does not guarantee the described dynamics

## https://dev.to/hiclab/push-vs-pull-in-task-assignment-lfg
- claim: push assignment (a lead hands out tickets) creates silos and uneven load, while pull assignment (anyone takes the next queue item within their skill) forces shared understanding of the whole queue
- population: docs/TEAM-PLAYBOOK.md's task-assignment guidance
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: an opinion blog post's argument, not a measured comparison of the two assignment styles

## https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-restrictions/
- claim: Confluence's content restrictions API (GET, PUT, POST, DELETE on /content/{id}/restriction, restriction types read and update) is the mechanism for making a generated page read-only to everyone except the publishing identity
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/04-technology-map.md and design/team-operating-model/07-verification.md, all citing it for the read-only publish guarantee
- date: opened by the research pass on 2026-08-01 (team-research/r1-jira-confluence.md)
- limit: vendor API documentation of current behavior; this repository's own use of the endpoint is not verified against a live Confluence instance by this citation

## https://developer.atlassian.com/cloud/jira/software/integrate-jsw-cloud-with-onpremises-tools/
- claim: Jira's Development Information API bridge is designed for on-prem tools to push data one-way outbound to Jira Cloud without opening inbound firewall ports
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/03-adr.md and design/team-operating-model/04-technology-map.md, all citing it for the outbound-only export design
- date: opened by the research pass on 2026-08-01 (team-research/r1-jira-confluence.md)
- limit: vendor documentation of an API's intended use case, not a working integration this repository has built and tested against it

## https://developers.asana.com/docs/webhooks
- claim: Asana's webhook filters can scope by resource_type, resource_subtype and action, and for "changed" actions can whitelist specific fields, though higher-level resources (Workspace, Team, Portfolio) do not support the fields restriction
- population: design/team-operating-model/03-adr.md's discussion of two-way sync built on Asana webhooks
- date: opened by the research pass on 2026-08-01 (team-research/r2-asana-teams.md)
- limit: vendor API documentation of current filtering options, not a built integration verified against a live Asana workspace

## https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds
- claim: GitHub artifact attestations sign build provenance through GitHub-run Sigstore, verified with `gh attestation verify`
- population: design/team-operating-model/04-technology-map.md's AttestationSigner row
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md, cross-checked against a second GitHub-authored page)
- limit: vendor documentation of the mechanism, not evidence that this repository's own release pipeline has attestation wired up and verified

## https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects
- claim: GitHub Projects v2 has a 50-field cap, ships table, board and roadmap views with built-in workflows, and exposes GraphQL plus Actions for custom automation beyond those workflows
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/04-technology-map.md, both citing it for the ProjectsBoard automation approach
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md)
- limit: vendor documentation of current product limits and capabilities, which GitHub can change without notice

## https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- claim: GitHub's merge queue builds merge groups on temporary branches that test each pull request plus everything ahead of it, and requires workflows to declare the merge_group trigger to run in the queue
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/04-technology-map.md's MergeQueue row
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md)
- limit: vendor documentation of the mechanism, not confirmation that this repository's own CI workflows already declare the trigger

## https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- claim: GitHub branch protection can require approval from someone other than the most recent pusher and dismiss stale approvals on new commits, and GitHub's own docs describe rulesets as the forward-looking alternative to it
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/06-diagrams.md, both citing it for the branch-protection baseline
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md and team-research/r3-github-enterprise.md)
- limit: vendor documentation of current settings, not evidence of which of the two overlapping systems this repository has actually configured

## https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository
- claim: GitHub rulesets target branches or tags by fnmatch pattern, can apply org-wide by name, topic or repository property, carry a bypass list, and have Active, Evaluate (dry-run) and Disabled enforcement states
- population: docs/TEAM-PLAYBOOK.md's ruleset guidance
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md)
- limit: vendor documentation of current ruleset mechanics, not confirmation of which rules this repository has actually turned on

## https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- claim: GitHub CODEOWNERS routes review to designated owners, any one listed owner's approval satisfies the requirement (OR semantics, never AND), and the last matching pattern in the file wins
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/07-verification.md, both citing it to state the OR-only limit of CODEOWNERS routing
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md and team-research/r3-github-enterprise.md)
- limit: vendor documentation of current matching rules, not a count of how many reviewers this repository's own CODEOWNERS file actually requires

## https://docs.github.com/en/rest/orgs/rule-suites
- claim: bypass and pass-or-fail auditing for rulesets lives in the rule-suites API, whose result is pass, fail or bypass, and this data does not appear in GitHub's general audit log
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/01-purpose.md, design/team-operating-model/04-technology-map.md and design/team-operating-model/07-verification.md, all citing it for the bypass-visibility design
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md, cross-checked against a GitHub changelog post)
- limit: vendor API documentation of current behavior, not confirmation that this repository has a scheduled job actually reading that endpoint today

## https://docs.obsidian.md/plugins/guides/bases-view
- claim: Obsidian's own developer guide for the Bases plugin warns that an unfiltered Base "will provide an entry for every file in the vault," and tells view authors to virtualize rendering for that reason
- population: docs/TEAM-PLAYBOOK.md and memory-template/TEAM-VAULT.md, both citing it to justify scoping every shared Base with a filter
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: vendor documentation of the plugin's current behavior, not a measurement of this repository's own vault size or render time

## https://dora.dev/guides/dora-metrics/
- claim: DORA's own current guide states its metric set is now five metrics (throughput plus stability), not the original four
- population: design/team-operating-model/07-verification.md's DORA metric reference
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md, cross-checked against Google Cloud's blog and a second source on the 4-to-5 change)
- limit: describes DORA's own current framework, not a measurement of this repository's delivery performance against it

## https://engineering.squarespace.com/blog/2019/the-power-of-yes-if
- claim: Squarespace's engineering RFC process names specific approvers whose sign-off is required before implementation starts
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/02-process.md, both citing it for the named-approver pattern
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md)
- limit: one company's blog post describing its own process, not a study of whether named approvers improve outcomes generally

## https://eu.36kr.com/en/p/3755031628005892
- claim: the Obsidian company (7 full-time staff at the time reported) runs its own internal task planning, PRDs, roadmaps and checklists from one shared Obsidian vault, alongside GitHub for code review and separate chat software for day-to-day talk
- population: memory-template/TEAM-VAULT.md's framing of the vault as a planning and knowledge layer, not a replacement for issue tracking or chat
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md)
- limit: a single company's reported practice at one point in time, not a claim about how any other team should run its vault

## https://forum.asana.com/t/mark-task-as-approval-via-api/798803
- claim: an Asana community moderator confirms on the developer forum that converting a task to an approval via the API requires setting resource_subtype to "approval" in the update body; setting approval_status alone does not convert it
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/04-technology-map.md and design/team-operating-model/07-verification.md, all citing it for the approval-conversion verification step
- date: opened by the research pass on 2026-08-01 (team-research/r2-asana-teams.md)
- limit: a forum post from a community moderator, not Asana's own formal API reference; treated as corroborating rather than primary documentation

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

## https://github.blog/changelog/2026-02-17-required-reviewer-rule-is-now-generally-available/
- claim: GitHub's required-reviewer ruleset rule (per-path minimum approvals from up to 15 named teams, with gitignore-style negation) reached general availability in February 2026
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/01-purpose.md and design/team-operating-model/07-verification.md, all citing it as the only mechanism that gives an approval count, unlike CODEOWNERS
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md, cross-checked against the preview announcement for the GA timeline)
- limit: a changelog entry describing a shipped feature, not confirmation that this repository's own rulesets have the rule turned on

## https://github.blog/engineering/infrastructure/ship-code-faster-safer-feature-flags/
- claim: GitHub's own account of running feature flags on itself describes shipping every potentially risky change behind a flag, then enabling it for everyone or a percentage of actors, with the ability to disable in seconds rather than rolling back a deployment that takes minutes
- population: docs/TEAM-PLAYBOOK.md's feature-flag rollout guidance
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md)
- limit: one company's account of its own practice, not evidence that this repository's own deployments use feature flags today

## https://github.blog/enterprise-software/devsecops/enhance-build-security-and-reach-slsa-level-3-with-github-artifact-attestations/
- claim: GitHub artifact attestations reach SLSA Build Level 3 through separated signing hardware plus ephemeral build runners
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/07-verification.md, both citing it for the SLSA level the platform claims
- date: opened by the research pass on 2026-08-01 (team-research/r3-github-enterprise.md, cross-checked against a second GitHub-authored page)
- limit: vendor documentation of the platform's own claimed compliance level, not an independent SLSA audit of this repository's release artifacts

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

## https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/overview
- claim: a working Approve/Reject button that calls a service requires a real Teams bot using Adaptive Card Universal Actions (Action.Execute), which sends an adaptiveCard/action Invoke activity to the bot and supersedes Action.Submit for Teams
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/03-adr.md, both citing it for the Teams approval-button mechanism
- date: opened by the research pass on 2026-08-01 (team-research/r2-asana-teams.md)
- limit: vendor documentation of current card mechanics, not a working bot this repository has built and tested against a live Teams tenant

## https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook
- claim: the Workflows app (Power Automate) replacement for incoming webhooks generates a webhook URL to POST JSON to, caps message size at 28 KB, and states Workflows-based webhooks support Adaptive Cards and Message Card format but not button rendering
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/03-adr.md, design/team-operating-model/04-technology-map.md and design/team-operating-model/06-diagrams.md, all citing it for the Workflows-based notifier design
- date: opened by the research pass on 2026-08-01 (team-research/r2-asana-teams.md)
- limit: vendor documentation of current limits, not confirmation that this repository's own notifier stays under the 28 KB cap in practice

## https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors
- claim: Office 365/Microsoft 365 connectors and their incoming webhook creation flow are being retired, with a final rollout window of May 18 to May 22, 2026 to disable them, and webhooks/connectors are unavailable in GCC High, DoD and 21Vianet-operated Teams
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/03-adr.md, both citing it for the legacy-connector retirement date
- date: opened by the research pass on 2026-08-01 (team-research/r2-asana-teams.md, cross-checked against a Microsoft 365 developer blog post)
- limit: a retirement schedule as published on the date checked; Microsoft has already extended this deadline more than once and could move it again

## https://learn.microsoft.com/en-us/microsoftteams/teams-app-permission-policies
- claim: Teams admin center app permission policies (or the newer app-centric management) gate whether a custom Teams app or bot can run for a given user independent of whether the app itself works, and policy changes can take a few hours to propagate
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/04-technology-map.md and design/team-operating-model/07-verification.md, all citing it for the per-tenant governance step before a bot launch
- date: opened by the research pass on 2026-08-01 (team-research/r2-asana-teams.md)
- limit: vendor documentation of current admin controls, not confirmation of any specific tenant's actual policy configuration

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

## https://schemas.getdbt.com/dbt/manifest/v12.json
- claim: none; the URL is a schema-version identifier used as a literal string value inside an illustrative JSON `manifest.json` example in the book text, not a claim the prose rests on
- population: docs/book/14-the-data-engineers-deep-dive.md's worked example of dbt manifest shape
- date: not opened by the 2026-08-01 research pass; no team-research file traces this URL, and this machine did not reopen it during this citation pass
- limit: this entry records the string's presence and use as sample data only; it establishes nothing about what page currently lives at that address or whether v12 is dbt's current schema version

## https://sre.google/sre-book/incident-document/
- claim: Google's SRE incident document is a single live, collaboratively-edited state document, not a chat log, naming the Incident Commander, Operations Lead, Planning Lead and Communications Lead, tracking a running TODO list with bug numbers, and required to be updated at least every four hours and at Comms Lead handoff
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/02-process.md, both citing it for the incident-document shape
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: describes Google's own published SRE practice, not a claim that this repository runs incidents at the same scale or with the same staffing

## https://sre.google/sre-book/managing-incidents/
- claim: Google SRE's command handoff is a verbal ritual with a mandatory explicit acknowledgment, where the outgoing commander states "You're now the incident commander, okay?" and does not disconnect until the incoming commander confirms, built on the fire-service Incident Command System
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/02-process.md, both citing it for the explicit handoff-acknowledgment requirement
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md and team-research/r6-facilitation-handover.md)
- limit: describes Google's own published SRE practice, not a claim that this repository has run an incident using it

## https://support.atlassian.com/cloud-automation/docs/configure-the-incoming-webhook-trigger-in-atlassian-automation/
- claim: Jira Automation's incoming webhook trigger fires a flow from an external POST, auto-generates a URL plus a secret token verified via a header or a URL-path secret, and the token never expires unless manually regenerated
- population: design/team-operating-model/03-adr.md's discussion of the inbound automation trigger
- date: opened by the research pass on 2026-08-01 (team-research/r1-jira-confluence.md)
- limit: vendor documentation of current trigger behavior, not confirmation that this repository has configured and tested such a trigger

## https://support.atlassian.com/jira-service-management-cloud/docs/designate-your-approvers/
- claim: Jira Service Management's IT Service project ships a change-management workflow with change types Standard, Normal and Emergency, an Approvers field, and Change Advisory Board approval steps attached to a workflow status
- population: design/team-operating-model/01-purpose.md and design/team-operating-model/03-adr.md, both citing it for the change-approval workflow shape
- date: opened by the research pass on 2026-08-01 (team-research/r1-jira-confluence.md)
- limit: vendor documentation of a Jira Service Management feature, not confirmation that any specific project in this repository's scope has that workflow configured

## https://support.atlassian.com/jira/kb/jira-software-rest-api-essential-parameters-for-custom-field-creation/
- claim: a dedicated URL-typed custom field ships out of the box in Jira (type com.atlassian.jira.plugin.system.customfieldtypes:url, created via POST /rest/api/2/field), the natural home for an evidence link back to an external system
- population: docs/TEAM-PLAYBOOK.md, design/team-operating-model/04-technology-map.md and design/team-operating-model/06-diagrams.md, all citing it for the evidence-link field
- date: opened by the research pass on 2026-08-01 (team-research/r1-jira-confluence.md)
- limit: the exact type and searcherKey pairing is documented for Data Center; the research file notes it is the same field type shipped on Cloud but this was not independently reverified by this machine

## https://toolkitx.com/blogsdetails.aspx?title=Shift-handover%3A-a-practical-guide-to-doing-it-right-in-PTW
- claim: high-hazard industrial shift handover requires seven mandatory fields (personnel and roles, operational status, open permits, isolation and lockout-tagout state, alarms and deviations, residual hazards and active controls, and pending actions with a named owner and deadline), and uses a hybrid format because verbal-only handover loses information while written-only handover loses context
- population: design/team-operating-model/02-process.md's handover-content requirements
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md)
- limit: describes a different industry's (process-plant, permit-to-work) safety practice, adapted rather than a claim that software delivery carries the same hazard profile

## https://www.atlassian.com/blog/confluence/unlocking-the-secrets-to-outstanding-teamwork-in-2025
- claim: Atlassian's State of Teams research (12,000 knowledge workers) found high-performing teams use meetings to make decisions rather than to report status, and most respondents say a meeting is the only reliable way to get colleagues to decide something as a group
- population: docs/TEAM-PLAYBOOK.md's argument for decision-oriented meetings
- date: opened by the research pass on 2026-08-01 (team-research/r6-facilitation-handover.md, cross-checked against a second source on the reported percentage)
- limit: a vendor-commissioned survey of self-reported behavior, not an independent measurement of this repository's own meetings

## https://www.dsebastien.net/the-complete-guide-to-obsidian-properties/
- claim: inconsistent frontmatter property names or types (a string in one note, a list in another) is the single most common reason team dashboards silently miss rows, so properties should be treated as a schema enforced through a template rather than typed freehand
- population: memory-template/TEAM-VAULT.md's guidance on enforcing property schemas
- date: opened by the research pass on 2026-08-01 (team-research/r4-obsidian-teams.md, alongside a second practical guide making the same point)
- limit: practitioner guidance rather than a measured failure rate; the "single most common reason" framing is the source's own claim, not independently counted by this repository

## https://www.harness.io/harness-devops-academy/sox-compliance-for-software-delivery-explained
- claim: SOX-style control requires the person who develops a change not be the person who deploys it, with an automated pipeline accepted as a compensating control provided the pipeline enforces the separation
- population: docs/TEAM-PLAYBOOK.md's mapping of its four approval gates onto SOX-style review expectations
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md, cross-checked against GitHub's own control documentation)
- limit: vendor content (Harness sells a delivery platform) describing a compliance requirement, not legal advice and not confirmation that this repository is itself in scope for SOX

## https://www.microsoft.com/en-us/research/publication/the-space-of-developer-productivity-theres-more-to-it-than-you-think/
- claim: the SPACE framework explicitly states developer productivity "cannot be measured by a single metric or dimension"
- population: docs/TEAM-PLAYBOOK.md and design/team-operating-model/07-verification.md, both citing it against relying on any single velocity number
- date: opened by the research pass on 2026-08-01 (team-research/r5-enterprise-sdlc.md); note the underlying ACM Queue page returned HTTP 403 on fetch, so the Microsoft Research abstract page is the primary text actually read
- limit: the five per-dimension SPACE definitions come from secondary summaries cross-referencing the paper, not the full paper text, per the research file's own note

## https://www.anthropic.com/engineering/building-effective-agents
- claim: the orchestrator-workers pattern is justified only when it demonstrably beats a simpler shape, and cost is a first-class design constraint of agentic systems
- population: Anthropic engineering post on agent design patterns
- date: post captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
- claim: multi-agent systems add roughly 3 to 10 times token overhead and pay off only for parallel independent work, context isolation, or specialization; verification must check environment state, not the agent's transcript claim
- population: Anthropic blog post on when to use multi-agent systems
- date: post captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://www.anthropic.com/engineering/built-multi-agent-research-system
- claim: an Opus lead with Sonnet subagents beat single-agent Opus by 90.2 percent on their internal eval; early failures included spawning 50 subagents for simple queries, fixed by embedded scaling rules
- population: Anthropic engineering post on their research system, including its internal evaluation
- date: post captured 2026-08-06, spot-checked verbatim by the orchestrator the same day
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://code.claude.com/docs/en/agent-sdk/subagents
- claim: only the prompt string crosses the parent-to-subagent boundary, and a dead or partial agent return is a distinguishable failure signal
- population: Claude Agent SDK documentation, subagents page
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- claim: subagents should return condensed structured summaries near 1,000 to 2,000 tokens while persistent files carry long-lived state
- population: Anthropic engineering post on context engineering
- date: post captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://code.claude.com/docs/en/best-practices
- claim: a fresh-context reviewer judges the diff on its own terms and must be scoped to correctness or it manufactures findings
- population: Claude Code best-practices documentation
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- claim: judge each dimension with an isolated judge and require an explicit insufficient-information verdict
- population: Anthropic engineering post on agent evals
- date: post captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://code.claude.com/docs/en/agent-sdk/agent-loop
- claim: the SDK ships hard-stop primitives (max_turns, max_budget_usd) so caps live in configuration, not judgment
- population: Claude Agent SDK documentation, agent loop page
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://code.claude.com/docs/en/agent-sdk/cost-tracking
- claim: SDK cost fields are client-side estimates, not billing truth
- population: Claude Agent SDK documentation, cost tracking page
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://platform.claude.com/docs/en/build-with-claude/usage-cost-api
- claim: the usage and cost admin API attributes real spend by key and workspace
- population: Anthropic platform documentation, usage and cost API
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://reference.langchain.com/python/langgraph-sdk/schema/Config/recursion_limit
- claim: LangGraph enforces a small hard recursion limit as an exception, not an alert
- population: LangGraph SDK reference, recursion_limit
- date: reference captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://developers.openai.com/api/docs/guides/spend-limits
- claim: alerts notify while caps stop traffic; the two words name different controls
- population: OpenAI platform documentation on spend limits
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://docs.datadoghq.com/llm_observability/monitoring/metrics/
- claim: production LLM observability records tokens by type, cost, duration and errors per span at full sampling
- population: Datadog LLM observability documentation
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://docs.langchain.com/langsmith/cost-tracking
- claim: cost attribution collapses without a stable per-task identity carried on every run
- population: LangSmith cost-tracking documentation
- date: docs captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://opentelemetry.io/blog/2026/genai-observability/
- claim: OpenTelemetry's GenAI semantic conventions standardize token and model field names
- population: OpenTelemetry blog post on GenAI observability
- date: post captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://tianpan.co/blog/2025-10-19-llm-routing-production
- claim: never route work to a cheaper model without quality measurement in place first
- population: practitioner blog post on LLM routing in production
- date: post captured 2026-08-06; single practitioner source, the weakest tier in this batch
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://kanban.university/kanban-guide/
- claim: limiting the work allowed to enter the system is the key lever against delay, and stop starting, start finishing is the cultural core of a pull system
- population: the official Kanban guide
- date: guide captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://waux.io/stop-starting-start-finishing/
- claim: delivery moves at the speed of its bottleneck, so intake is sized to the constraint
- population: practitioner essay applying theory of constraints to delivery
- date: post captured 2026-08-06; single practitioner source
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://basecamp.com/shapeup/1.2-chapter-03
- claim: estimates start with a design and end with a number; appetites start with a number and end with a design
- population: Shape Up, chapter 3, the online book
- date: captured 2026-08-06, spot-checked verbatim by the orchestrator the same day
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://basecamp.com/shapeup/3.5-chapter-14
- claim: the circuit breaker cancels overrunning work by default instead of extending it
- population: Shape Up, chapter 14, the online book
- date: captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://itrevolution.com/articles/kata/
- claim: the andon cord actually stops the line without asking permission
- population: IT Revolution article on kata and the andon cord
- date: article captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## http://leanmagazine.net/lean/cost-of-delay-don-reinertsen/
- claim: sequence eligible work by cost of delay against effort, weighted shortest job first
- population: interview with Don Reinertsen in Lean Magazine
- date: interview captured 2026-08-06; secondary source for Reinertsen's own book
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://workingbackwards.com/resources/working-backwards-pr-faq/
- claim: the PR-FAQ forces the customer and the press release before any code, killing ideas that serve no named customer
- population: Working Backwards resource page on the PR-FAQ
- date: page captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://pre-commit.com/
- claim: a governance tool that states its scope in one narrow sentence beats a broad pitch
- population: pre-commit's own homepage self-description
- date: page captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://www.conftest.dev/
- claim: conftest scopes its value proposition to one line about testing structured configuration
- population: conftest's own homepage self-description
- date: page captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://github.com/danger/danger/blob/master/README.md
- claim: Danger frames its value as codifying team norms so humans think about harder problems
- population: the Danger project README
- date: README captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

## https://strategyn.com/jobs-to-be-done/
- claim: jobs-to-be-done separates the stable job from the changing product
- population: Strategyn's jobs-to-be-done overview page
- date: page captured 2026-08-06
- limit: read in full by a research agent on 2026-08-06 and hostile-checked by a second agent that re-opened the URL; guidance, not a measurement on this estate

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

## https://claude.ai/code/artifact/a6690cc7-5c4b-4dcb-9dd9-f5495a3dcbd6
- claim: the pilot team's review of this method, drawn as workflows, with the business cost of each finding and who owns the fix
- population: one team of five reviewers plus one live change carried through the whole process, in one estate, on 2026-08-15
- date: published 2026-08-15, republished the same day with every individual replaced by their role
- limit: one team, one estate, one week of use, and the queue figures inside it are single sourced from that team's own weekly report; it is a page this project wrote, not an independent source

## https://claude.ai/code/artifact/bc992465-99fe-4d15-bf44-aec195598bf8
- claim: the current state of this project's plan, phases and evidence, as its progress board
- population: this project only, refreshed by whichever session holds the work
- date: republished at each closed loop, most recently 2026-08-15
- limit: DEAD as of 2026-08-15 22:21 JST. Absent from all 19 owned artifacts and the fetch returns not found, which is the same evidence pattern that identified the loss of its predecessor. Kept here because the identifier is still referenced in the record of which links died, and superseded by the entry below

## https://claude.ai/code/artifact/8079cafd-df8c-417b-879b-c4a9de04f906
- claim: the proposed v1 finalize program, its two Gantt ranges, the team complaint ledger, and the timeline forecast, as a one-off planning deliverable
- population: this project only, one planning session, 2026-08-19
- date: published 2026-08-19, status PROPOSED until the founder ratifies
- limit: a page this project wrote, not an independent source; the figures on it are forecasts labeled as ranges, and it is not the live state board, which keeps its own link

## https://claude.ai/code/artifact/8d3b4d6d-6fb7-44ca-a313-161cfa9967fa
- claim: the current state of this project's plan, phases and evidence, as its progress board, and the two week plan to reach daily use by the pilot team
- population: this project only, one fortnight of planned work across 11 phases and 46 work items, refreshed by whichever session holds the work
- date: published 2026-08-15, replacing a predecessor found dead the same evening
- limit: DEAD, recorded by commit 26ce4d9 on 2026-08-17, which replaced it in PROJECT.md and whose subject records it as the fourth board link to die. That death is CARRIED from the commit rather than re-observed: no session has re-fetched this identifier since, so the entry states which record it rests on rather than claiming a check nobody ran. While it lived it was a state page this project published about itself, a record rather than evidence. Kept here because the identifier is still referenced in the record of which links died (PROJECT.md, "The published links"), and superseded by the live board entry for 12edaca1 earlier in this inventory

## https://example.invalid
- claim: none; this is the reserved placeholder host the 20 August day plan uses inside a runnable Codex-finding case (docs/plans/2026-08-20-day-plan-wbs.md), so the case can name a URL that can never resolve
- population: no page; the .invalid top-level domain is reserved by RFC 2606 and never resolves, which is the point of using it in a fixture
- date: recorded 2026-08-21 when the finding was written; entered in this inventory 2026-08-22 after a fresh manifest put that plan file in the scanned set
- limit: a fixture string, not a reference; nothing in this repository rests on any content behind it, and the citation check opens no network connection to prove that either way

## https://github.com/khalilmaaouni/Brother
- claim: the umbrella repository this product points at, carrying COORDINATION.md, the shared chain, the verdict tuple and the evidence law across the three products; the README section added by this branch sends a contributor there before they add a command, an agent, a hook or a state vocabulary
- population: one repository, public, created 2026-08-22, carrying COORDINATION.md at 9,482 bytes; read on 2026-08-23 through the provider API, 0 stars and 0 forks at that reading
- date: read 2026-08-23; the repository is under daily change, so the byte count and the counts above are a snapshot of that reading and not a standing figure
- limit: a repository this estate owns and writes, so it is a record of our own intent rather than independent evidence of anything. It is cited here because this branch's README sends a reader to it, and the check that requires this entry is about disclosing that dependency, not about endorsing the destination. Nothing in this repository's behaviour rests on its contents; the coupling is documentary only
