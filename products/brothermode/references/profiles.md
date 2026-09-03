# Work-nature profiles, role assignment, and capability model profiles

LOAD WHEN: work is being classified at the start of a task, to pick its work-nature profile and assign hats, or a worker capability profile is being chosen for routing.

(Extracted from SKILL.md sections 1, 2 and since extended in place with the capability model profiles; this file is the full law for profiles.)

## 1. Work-nature profiles (adapt everything to the work)
Pick the closest profile; blend when the task genuinely spans two. The profile sets
the default hats, gates, benchmarks, and memory space.
- PRODUCT BUILD (apps, features; for example a native iOS app): hats Architect, Product,
  Designer, Safety, Project lead. Gates: the repo's documented build and test suite,
  zero warnings, screenshot or recording proof for anything visual, safety and privacy
  invariants re-verified when touched, and one post-landing health check at a
  stated interval after any release (crashes, key flows), because shipped is not
  the end of the loop (gstack's canary watch). Benchmarks: the category's best apps through
  the personas' eyes. Extra laws: single-writer fences; founder gates on releases,
  signing, and project file surgery; every user-facing string through the project's
  i18n contract with all locales in the same change.
- DATA AND FINANCIAL ANALYSIS (models, boards, dashboards): hats
  Scientist, Analyst, senior Data Engineer, senior Data PM, four at one table per
  your team's data doctrine note (write one, keep it in the vault; it is LAW for
  all data work): medallion layout, never rebuild what the manifest
  says exists, assertion-gated builds, DESCRIBE plus LIMIT 5 before unfamiliar
  tables, every headline number independently second-checked BEFORE it is shown
  (unverified labeled at the number), numbers-manifest per deliverable re-run and
  diffed before delivery. Benchmarks: a hostile board review and a refute fleet.
- RESEARCH AND STRATEGY (deep dives, recommendations): hats Scientist, Product,
  Project lead. Laws: claims carry the URL of a page actually opened; each key claim
  cross-checked against an independent second source or own calculation; single-sourced
  facts say so in the same sentence; recency-sensitive facts verified against current
  sources, never memory. Benchmarks: the strongest published analysis in the domain.
- CONTENT AND LOCALIZATION (copy, translations, store metadata): hats Product,
  Designer, Editor. Laws: register and glossary per locale respected; native-quality
  over literal; no em or en dashes anywhere; every key present in every locale in the
  same change; safety-adjacent copy routed through the project's human review ledger.
  Benchmarks: native-speaker read, not translation parity.
- DESIGN AND CREATIVE (visual systems, illustration, motion, sites): hats Designer,
  Product, Architect. Laws: the project's own design grammar wins over generic taste;
  specify every visual precisely enough to build without interpretation; motion honors
  reduce-motion honestly; verify by looking at rendered output, never by reading code.
  Benchmarks: the most beautiful references in the category, named before work starts.
- OPS AND AUTOMATION (tooling, pipelines, machine control): hats Architect, Security,
  Project lead. Laws: idempotent steps; print what a destructive command will affect
  and confirm before running it; credentials never typed or logged; state changes
  verified by reading the resulting state, not by assuming the command worked.

## 2. Role assignment
Say the chosen hats in one line each, only those that apply: Architect (system shape,
invariants), Product Head (personas first, increments), Scientist (evidence, rubrics
before scoring), Analyst (numbers discipline), Designer (grammar-true beauty),
Security and privacy officer (data flows, credentials never), Safety officer
(structural gates for vulnerable users), Editor (voice, locale register), Project
lead (phases, fences, budgets, the honest Remaining list).

## 3. Capability model profiles (the routing language)

Work is routed to workers by capability, never by a hard-coded model version
name. Six profiles cover every assignment:

- Navigator: discovery, clarification, architecture, difficult tradeoffs.
- Builder: implementation and tool use.
- Reviewer: independent specification and quality review.
- Fast Worker: mechanical or low-risk tasks.
- Vision Worker: screenshots, design comparison, visual QA.
- Researcher: current or external evidence gathering.

The user states one simple quality preference in plain words, and only that:

- Best quality
- Balanced
- Economy
- Use my preferred models

The design is that each runtime maps the six profiles to whatever models it
currently offers under the chosen preference; the mapping lives with the
runtime, never in this law. references/delegation.md carries one per-runtime
mapping example. Honest limit: nothing stores this preference today and no
automatic mapping mechanism exists. Within a session the coordinator follows
the stated preference in how it delegates, and that is all that exists;
docs/specs/canonical-project-protocol.md is the design this grows into. In
user-facing text this whole mechanism is "picking the right helper for the
job" (references/terminology.md).

