# BrotherSBE: design document

The why and the what: what the job actually is, the order the work runs in, and
how each phase is held to something mechanical. For the machinery (the dossier,
the tools, the gates, the evolution loop) see [HOW-IT-WORKS.md](HOW-IT-WORKS.md).
To install, see [SETUP.md](SETUP.md). For one system designed end to end with real
commands and real output, read
[the worked engagement](guides/05-a-worked-engagement.md) first.

**Khalil Maaouni, Founder.** Identity in five words: realistic, SOTA, best
practices driven, proven, trustable.

---

## 1. The job is a promise system

Strip the ticket queue away and a senior backend engineer's job is keeping
promises, most of which they did not personally make. An API contract is a promise
to a partner. A schema is a promise to every consumer that reads it. A migration is
a promise that the data on the far side still means what it meant. A dashboard
figure is a promise to whoever will act on it. The estate is a lattice of such
promises, and the real work, whatever the ticket says, is discovering which
promises a change touches and proving they still hold afterward.

That view decides what a useful colleague is. Not output volume, which is
gameable. Useful means the touched promises stay provable: the contract test that
still passes, the reconciliation query that still returns zero drift, the reverse
migration that actually ran against a restored copy.

It also decides where the expensive mistakes live. They are made while deciding
what to build, how the process runs, what shape the system takes, and how the data
is modeled. A check at the end catches none of those. So BrotherSBE is a design
system first and a verification system last, in that order, and the order is the
whole design.

Two rules sit under everything:

1. **Design comes before verification.** Six phases, each gating the next.
2. **An agent earns trust in exact proportion to how mechanically its output can
   be checked.** Not by fluency, not by model quality. Every law in
   [SKILL.md](../SKILL.md) and in the [`references/`](../references/) files its routing
   table names states the thing that enforces it, and a rule that cannot name one is
   advice, filed in [PRACTICES.md](../PRACTICES.md), which says so.

## 2. The order of operations

| Phase | Question | Artifact | Held by |
|---|---|---|---|
| Purpose | What is this for, and what breaks if it is wrong | `01-purpose.md` | L2, artifacts check |
| Process | Who does what, triggered by what, failing how | `02-process.md` | L2 |
| Architecture | What shape, decided against what criteria | `03-adr.md`, `04-technology-map.md` | L3, adr check |
| Data | What exists, how it relates, where it is mastered | `05-data-model.md` | L4, datamodel check |
| Expression | What the system looks like, in code that diffs | `06-diagrams.md` | L5, diagrams check |
| Verification | What proves each claim, and when | `07-verification.md` | L7 to L11, the gates CI runs under `--strict` |

**Purpose.** No design starts while the purpose is unstated. The problem without a
solution inside it, the users and what they do today instead, observable success
criteria, explicit non-goals, and the blast radius named. What breaks if it is
wrong is what sizes everything downstream, including the tier.

**Process.** The workflow as it exists and as it will exist, before any
architecture. An architecture is a machine for running a process, so the process is
drawn first. Every step names an actor, a trigger, and what happens when it fails.
Every handoff names both sides and the contract between them.

**Architecture.** Shape decided against named criteria, not preference, and
recorded with its rejected alternatives and the condition that would reopen it.
Beside the decision, a technology map: per component, the technology, the owner,
the failure mode, and the recovery path, plus the source systems and the recovery
objectives with the drill that proves them.

**Data.** Conceptual, then logical, then physical, never in another order. The
gate between logical and physical is mechanical, and it is section 5 below.

**Expression.** Diagrams as code, committed with the design, diffed in review.
Every node named, every edge saying what flows, and every element traceable to
something the dossier defines.

**Verification.** Last, and only then, the gates. Section 9.

## 3. Sizing the work: the tier model

The reason a design system does not turn every one line fix into a paper exercise
is that the size of the dossier is computed, not chosen. Five objective questions,
one tier, first match wins:

| If | Tier | Required artifacts |
|---|---|---|
| touches money, partner data, personal data, or production state, or is not reversible in under an hour | T3 | 01 to 07 |
| changes a data model, an API contract, or a file interface, or many consumers break | T2 | 01, 02, 03, 05, 06, 07 |
| crosses a service, system, or team boundary, or some consumers break | T1 | 01 |
| otherwise | T0 | none |

T0 is the common case and it produces nothing at all. The five answers are written
to `00-intake.json`, and the artifacts check reads the tier from that file rather
than from anyone's judgment, so two engineers answering the same five questions
land on the same tier. An override is legal in either direction, and it sets both
a tier and a reason: a tier moved with either field missing is an edit, not an
override, and the design check FAILs it by name rather than trusting either value.

## 4. Deciding shape: decision tables, not preference

An architecture argument settled by seniority is unrepeatable. The shape question
(monolith, modular monolith, services, event-driven) is scored against four
criteria: independently deploying teams, the consistency requirement, operational
maturity (on-call, tracing, CI), and failure isolation. The table lives in
`tables/architecture.json` as data, so a threshold changes by editing a file in a
reviewed pull request.

Every table returns the same four things: a recommendation, up to two
alternatives, the criteria that separated them, and what would flip the decision.
Two properties matter more than the ranking. A run where no criterion contributed
returns NO-DATA with the recommendation suppressed, because a recommendation
backed by zero evidence is a guess with a table around it. And a value matching
none of a criterion's known keys is reported as unrecognized, so a typo is
distinguishable from an omission.

The thresholds shipped here were measured on one estate. They are defaults until
you re-measure them on yours; a threshold inherited from someone else's machine is
a number that reads as tested and is not.

The consultation with the operator is the intake to the table, never a replacement
for it. Where the seam actually runs between two services is judgment, and it
stays in PRACTICES.md where it is honest.

## 5. The data method

Conceptual first: entities, meanings, identity, business rules, in plain language
with no technology in it, derived from the purpose brief and the process map.

Logical second: relationships with explicit cardinality and optionality, keys and
identity strategy, attribute roles, normalization decisions with their reasons,
historization, and the source system map naming for every entity its system of
record, its refresh contract, and what happens when that source is unavailable.

Physical last: engine-specific types, indexes, partitioning, clustering,
constraints, and the migration path with its reverse.

The gate between logical and physical is mechanical: every entity names a system
of record, and every relationship carries a cardinality in any accepted
notation: the word forms (one-to-one, one-to-many, many-to-one, many-to-many),
crow's foot shorthand (1:1, 1:N, N:1, N:M), UML multiplicity (1..1, 0..1, 1..*,
0..*), prose (has many, belongs to exactly one), or an erDiagram's own symbols.
An entity with no system of record fails by name. No
entities at all is a failure, not a pass.

Three lenses apply at that gate, in this order, and they are not a review ritual:
each one changes the model.

1. **Engineer.** Can this load reliably, idempotently, at volume, and recover after
   failure? This lens is what puts an upsert key and a replay path in the model
   rather than in a runbook.
2. **Analyst.** Can the real questions be answered without heroic joins, and is
   every grain and metric unambiguous? This lens is what fixes the grain of a fact
   table before anyone builds on it, because an aggregate at the wrong grain
   returns a plausible number with no error raised.
3. **Scientist.** Is history preserved, is leakage prevented, are features
   derivable? This lens is what keeps status changes as timestamped rows instead of
   an overwritten column, which is the difference between a usable history and a
   leak.

Data work carries the strictest gates in the system for a structural reason.
Backend failure is loud: a bad deploy throws, a broken endpoint returns 500,
something pages. Data failure is silent by construction: the warehouse returns rows
for a bad query, with correct column names and plausible totals, and a wrong number
looks exactly like a right number for weeks. The benchmark record supports the
caution, dated and scoped: when Spider 2.0 was published in 2024, GPT-4o scored
10.1 percent on its realistic multi-step warehouse workflows against 86.6 percent
on the older Spider 1.0 suite, and purpose-built agents have since pushed the same
site's Spider 2.0-Snow leaderboard past 96 percent
(https://spider2-sql.github.io/), so the gap closed on the benchmark and proved
nothing about any particular warehouse. And the gold labels behind such
leaderboards are shakier than the scores they produce: a January 2026 preprint
reports annotation error rates of 52.8 percent in BIRD Mini-Dev and 62.8 percent
in Spider 2.0-Snow, and agent rankings that track the full development set
closely (Spearman 0.85) track the corrected subset only weakly (Spearman 0.32,
p=0.23, not statistically significant) (https://arxiv.org/abs/2601.08778,
preprint). No leaderboard score is evidence about your warehouse.

## 6. Diagram discipline

Diagrams are Mermaid, committed with the design, diffed in review. A set worth
writing, and human guidance rather than a gate, because nothing counts diagram
TYPES: at T2 and above a reviewer should expect a context view, a workflow or
sequence view, and the data delta, and should say so in review. This paragraph
used to state that set as a per-tier requirement, which SKILL.md withdrew as a
law claiming an enforcement nothing had.

Three rules, one of them mechanical. Every node is named. Every edge says what
flows and by what trigger or protocol. And every element that appears in a diagram
appears somewhere else in the dossier, which is the checked one: a node naming
something the dossier never defines is reported as an orphan. An undefined box on
an architecture diagram is how a picture starts describing a system that does not
exist, and it is invisible to human review because a box always looks plausible.

Documentation is brief by default, written for a human to follow in order, and
commented where a choice is non-obvious. Length is sized to the difficulty of the
task, never to the effort spent.

## 7. The register, and what it refuses

The operator is a working backend, infrastructure, or data engineer on a small team
or a strong individual contributor. The register is peer to peer: show the diff,
name the command, use the jargon, explain on request rather than by default.
Outcome first, then the proof, then the next step. Bad news first: NOT DONE,
negative ROI, and NO-DATA are first-class verdicts.

Three standing refusals, and each is structural rather than polite.

**Not autonomous.** No agent holds apply rights on production state: a live
database, an infrastructure apply, a deploy to a live environment, a partner-facing
endpoint, a money path, or any destructive operation not reversible inside an hour.
It produces the exact command, its expected effect, and the rollback, for a human
to run. The evidence for the hard line is public: in February 2026 an agent-driven
Terraform destroy took out a production estate including database snapshots off a
stale state file (https://incidentdatabase.ai/cite/1424/), and in July 2025 a
production database was deleted during an explicitly declared code freeze, then
misreported by the agent, an account resting on the affected founder's own public
posts (https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/,
single source). Blast radius follows credentials, not intentions.

**Not an oracle.** Where the published evidence is thin, "no published evidence" is
the answer rather than a weaker source. That applies to its own domain: no
published evaluation exists for agent-authored connectors, CDC configuration, or
agent-designed architecture end to end. Absence of evidence is a reason to gate
mechanically, not a gap to fill with confidence.

**Not a replacement.** The engineer owns architecture, grain, semantics, risk, and
every signature. The skill compresses the mechanical middle and widens what one
person can verify.

Self-reported speedup is inadmissible evidence anywhere in this system, including
about itself: an early-2025 randomized trial of 16 experienced developers on 246
real issues measured them 19 percent slower with AI assistance, after they forecast
24 percent faster and while they still believed, afterwards, that they had been 20
percent faster (https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/).
METR's February 2026 follow-up reports, for the 10 returning developers, an
estimated speedup of minus 18 percent on an interval from minus 38 to plus 9
percent that includes zero, and minus 4 percent on minus 15 to plus 9 for 47 newly
recruited developers, a signal METR's own page calls unreliable and, in its words,
likely biased downward by developers who declined to work without AI
(https://metr.org/blog/2026-02-24-uplift-update/). The durable finding is the
perception gap: the developers' own speed estimates were wrong before and after
the measurement.

## 8. The other half of the job: governed drafting

A backend engineer also writes RFCs, answers security questionnaires at deal
speed, argues with acceptance criteria, and turns a 2am fix into a runbook. On
those surfaces BrotherSBE drafts under the same evidence laws and nothing more: it
assembles what the repository, the tracker, and the ledgers already contain, with a
citation on every claim, and a named human signs whatever leaves the team. It does
not claim the counterparty's expertise, and it does not resolve an ambiguity: an
ambiguous input comes back as a question, because a model left alone answers
plausibly and the plausible answer hardens into an unchallenged requirement.

Three rules travel with every surface. Citation or abstain: an uncitable row is
marked unanswered, never inferred. Nothing auto-sends: not a status line, not a
questionnaire row, not a word to a customer during an incident. And a runbook is a
draft until someone who did not write it has executed it top to bottom in a
non-production environment. The per-surface detail, and the doctrines for backend,
warehouse, pipeline, quality, infrastructure, and performance work, are in
[the work doctrines guide](guides/03-work-doctrines.md).

## 9. Verification, last

Four failure classes are silent: a wrong result looks exactly like a right one, and
detection latency runs from minutes to never. For these, and only these,
verification is structural rather than advisory.

- **Numbers.** Every figure that could reach a decision ships with a second
  derivation, textually different from the first, re-run to zero drift against a
  pinned snapshot.
- **Migrations.** Forward and reverse both ran against a restored copy, the reverse
  records a rehearsal id as a string, and the row counts were recorded and match. A
  receipt with no row counts is NO-DATA: the gate reports what it compared rather
  than asserting a comparison it never made. Nothing resolves the rehearsal id.
- **Money and partner paths.** Approval bound to more than a typed name: a signed
  `Approved-by:` trailer this host verified, which an agent cannot forge, or a
  recorded platform review id, which it can, because nothing resolves the id. A
  typed name alone fails, and the gate's evidence names which of the two it got.
- **Ran.** No SQL or pipeline change is done until its check executed and left a
  receipt with a zero exit code and a nonzero duration. A check that took no time
  did not run.

Each is a subcommand of one script, advisory in a session and enforcing in CI. The
design side runs the same way. Three properties make the whole arrangement usable:

Absent evidence is NO-DATA, never PASS. A change with no figure, no migration, no
money path, and no SQL passes without receipts, so the gates do not tax work that
has nothing to prove.

Output that has not cleared its gate carries the label UNVERIFIED next to the item
itself, never in a footnote. Honest partial delivery beats blocked delivery: a
reader holding three verified numbers and one labeled UNVERIFIED is better off than
one holding four numbers of unknown standing.

Overrides exist, because a hard gate with no pressure valve gets bypassed off the
books. They are named and logged; nothing mechanically surfaces them at the weekly
review, which L15 says in the same words rather than claiming a reader nothing
schedules. They do not exist on the CI path at all: `--strict` changes only by a human editing the workflow
in a reviewed change.

The reason this section is last is the reason the document is ordered this way. A
gate catches a wrong number. It does not catch the wrong system.

## 10. How it measures itself

**Evals.** Every gate, every design check, and the tier table have fixtures with
planted defects and an assertion that the check catches each one. The suite exits
nonzero on any regression, so it doubles as the release gate for the skill itself.
A check that stops catching its defect stops the release.

**The weekly review.** Code-graded checks first, each printing PASS, FAIL, or
NO-DATA with its evidence inline; the model judges only the residue. Every
amendment to the law names the measured signal it should move, and the next review
reverts it if the signal did not move. Rejected amendments keep their reasons so
the same plausible idea does not get re-litigated every month.

**Frozen benchmarks.** The comparison set is ratified once and then frozen, because
a system that can pick its own comparators will eventually pick the ones it beats.
The published record on that is not hypothetical: in June 2025 METR measured one
model, o3, deliberately gaming the grading harness on 30.4 percent of its runs in
one task family (RE-Bench, 39 of 128) against 0.7 percent in the same evaluation's
other family (HCAST, 8 of 1,087)
(https://metr.org/blog/2025-06-05-recent-reward-hacking/, single source).

**Team learning.** A lesson becomes a law only through a reviewed pull request. If a
colleague's BrotherSBE behaves differently on Tuesday than it did on Monday, there
is a merged diff with a reviewer's name on it that says why. No colleague's tool
changes behavior silently.

## 11. Where it is weaker today

The design gates are checked against fixtures, not against a live commercial
warehouse. Until that changes, read the data-side behaviors as designed and
fixture-tested, not field-proven.

Every threshold in the tables and every baseline in [RUBRIC.md](../RUBRIC.md) was
measured on one estate. They ship as defaults with a re-measure rule attached.

Two scopes stay honest about their limits. A vendor model or harness update can
change behavior with no pull request: the guarantee is over BrotherSBE's own laws,
not the model underneath. And nothing in this repository can revoke a credential
the operator's shell already holds; the blast radius rule is enforced by the
estate's access control plus a human in the apply path, not by a script here.

Open questions carried as open rather than closed by assertion: whether gated
workflows escape what DORA's annual self-report survey associates with rising AI
adoption, an estimated 1.5 percent decrease in delivery throughput and 7.2 percent
decrease in delivery stability in the 2024 report
(https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report),
with the 2025 report reversing the throughput direction while the stability
penalty persists
(https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report),
and stability being the half a gate is for; whether a team of five absorbs the
review load the hard gates create; and what
agent-written code costs to maintain in year three, which no number in this
document prices.
