# BrotherSBE and the tools around it: an honest map

Last reviewed: 2026-08-28

Scope of this review: claims naming a version, path, or capability on this
machine were re-checked against the installed plugin cache and this repo's
own files and history. This pass verified: the reviewer agent count (8,
matching `ls agents/`), that every external URL on this page has a citation
entry in docs/CITATIONS.md (5 of 5), and that docs/RUNTIMES.md still names
Claude Code as the only runtime with verified enforcement. Claims about
external projects (their behavior, adoption, or figures this machine cannot
query) were left untouched and are carried forward unverified.

**Short answer for someone in a hurry.** BrotherSBE is an assurance layer for
backend and data engineering work done with AI assistants. It does not
transform your data, orchestrate your pipelines, catalog your tables, or
review your prose. It decides whether a claim about work is backed by
evidence, and it refuses to call something done when it is not. If that is not
the problem you have, one of the tools below is a better answer than this one,
and this page says which.

Every tool named here is good at what it does. Nothing on this page is a
takedown. The comparison worth making is not "which is better" but "which
problem is yours today", and most estates end up running several of these
together.

## Why this page exists, stated plainly

Two reasons, both honest. First, people evaluating BrotherSBE deserve to know
what it is not, before they install it, rather than after. Second, this
project's own measured weakness is that almost nobody has heard of it, so
being findable next to the tools people already search for is a legitimate
goal. What that does NOT license is inflating our claims or shading theirs.
Every number on this page names where it came from and when, and where we have
no measurement we say so instead of implying one.

## The one-sentence difference

Most tools in this space help you DO the work. BrotherSBE checks the CLAIMS
made about work that was done, and it is built so that absent evidence reads
as NO-DATA rather than as a pass.

That distinction is the whole product. A gate that cannot tell whether a
migration was rehearsed reports that it cannot tell, and a check that opened
no file says so rather than printing "clean".

## Where each tool fits

### Agent methodology frameworks

**[Superpowers](https://github.com/obra/superpowers)** describes itself as "a
complete software development methodology for your coding agents, built on top
of a set of composable skills". Its approach runs brainstorming, then design,
then a plan, then subagent-driven execution with test-driven development.

**Use it when:** you want a good general process for AI-assisted development
in any language or domain, with almost no setup cost, and you do not need
domain-specific enforcement.

**How it differs from BrotherSBE:** it is domain-neutral by design and it
works by putting well-written instructions in front of the model at the right
moment. BrotherSBE ships executable checks that exit non-zero and eight
read-only reviewer agents that know what a migration rehearsal is.

**How they compose, and this is a real recipe:** run Superpowers for the
process (brainstorm, spec, plan, execute) and BrotherSBE for the gates in CI.
The methodology decides how work gets done; the gates decide whether the
claims about it are backed. Neither needs the other to be uninstalled.

**Honest scoring, measured on 2026-08-10 and quoted from
[our own benchmark](BENCHMARK-vs-superpowers.md):** on onboarding friction we
scored ourselves 5 out of 10 and them 9, because their skills fire on their
own while BrotherSBE asks a user to learn slash commands and answer an intake
first. On adoption evidence we scored 1 against their 10. Those are our
weaknesses, published here rather than hidden, because a comparison page that
only flatters its author is worth nothing to a reader.

### Data quality and testing

**[Great Expectations](https://greatexpectations.io/)** describes its core as
"the engine of the GX data quality platform", helping teams "understand what
to expect from your data" by defining expectations, validating continuously,
and acting when issues appear.

**[dbt tests](https://docs.getdbt.com/docs/build/snapshots)** live beside your
transformations and run with them.

**Use them when:** you need assertions about the DATA itself. Is this column
unique, are these values in range, did this table arrive on time. That is
their job and they do it better than anything BrotherSBE ships, because
BrotherSBE ships no data assertions at all.

**How BrotherSBE differs:** it checks assertions about the WORK, not the data.
Did the number in this decision document come from a pinned snapshot. Was the
migration rehearsed in both directions. Did the person who approved this change
also write it. A data quality tool cannot answer any of those and was never
meant to.

**How they compose:** point BrotherSBE's evidence gate at the run receipt your
data quality tool produces. Their tool proves the data is sound; ours proves
the check actually ran, exited zero, and covered the change in front of it.
The two failure modes are different: theirs catches bad data, ours catches a
check nobody ran.

### Orchestration

**[Apache Airflow](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/backfill.html)**
and comparable orchestrators run your pipelines on a schedule with
dependencies and retries.

**Use them when:** you need work to run reliably, in order, at a time.

**How BrotherSBE differs:** it never runs your pipeline. It has opinions about
what evidence a pipeline change owes before it merges.

**How they compose:** the orchestrator runs the job; BrotherSBE's gate refuses
the change that would have deployed a job whose migration had no rehearsal
receipt. One is runtime, the other is the merge path.

### Catalogs and lineage

**[DataHub](https://datahub.com/blog/extracting-column-level-lineage-from-sql/)**
and comparable catalogs answer what exists, where it came from, and who owns
it.

**Use them when:** discovery and lineage are your problem.

**How BrotherSBE differs:** it asks whether the change in front of you named
its owning system and its grain, and it fails a data model artifact where an
entity names no owner. It holds no catalog of your estate and does not want
one.

**How they compose:** the catalog is the map; BrotherSBE checks that a change
did not quietly redraw it without saying so.

## When NOT to use BrotherSBE

Written deliberately, because a page that cannot say this is marketing.

- **You work alone on low-stakes code and nothing you ship needs proving.**
  The gates will feel like paperwork, because for you they are. Use a
  methodology framework and stop there.
- **Your problem is data quality, not claim quality.** Reach for a data
  quality tool. BrotherSBE will not tell you a column went null.
- **You need it on a harness other than Claude Code today.** As of the date at
  the top of this page, enforcement is verified on Claude Code only. Other
  runtimes receive advisory instruction files, and `docs/RUNTIMES.md` states
  exactly what is verified where. If you are on a different harness and need
  enforcement rather than advice, this is not ready for you yet.
- **You need a large community, many contributors, or a support channel.**
  Measured 2026-08-10, this project had one contributor. That is a real risk
  and no amount of enforcement quality changes it.

## The end-to-end process, if you run several of these together

This is the arrangement we would recommend to a team that already uses other
tools, and it asks nobody to abandon anything.

1. **Shape the work** with a methodology framework. Brainstorm, spec, plan.
2. **Describe the change** so its risk level is explicit, which decides how
   much design documentation it owes. That is BrotherSBE's intake.
3. **Build it** in whatever way your team already builds things.
4. **Assert the data** with your data quality tool, in your warehouse, with
   your credentials. BrotherSBE emits artifacts that run there rather than
   asking for access.
5. **Prove the work** by pointing BrotherSBE's evidence gate at the receipts
   those runs produced.
6. **Gate the merge** with BrotherSBE in CI, where nobody has to adopt
   anything: a failing check explains itself and names the command that fixes
   it.
7. **Run it** on your orchestrator, catalog it in your catalog.

Step 6 is the one nobody else in that list is trying to own, which is why the
arrangement is complementary rather than competitive.

## How this page stays honest

- Every external link here has an entry in `docs/CITATIONS.md` naming the
  claim, the population, the date captured, and the limit. A link without an
  entry fails a check in CI.
- Descriptions of other tools quote what those tools say about themselves,
  captured on the date recorded in the citation entry, rather than
  characterising them in our words.
- Volatile figures are deliberately absent. Star counts and version numbers
  rot within days, and a comparison page carrying stale numbers is worse than
  one carrying none. The two figures that do appear are our own scores against
  ourselves, dated, from a benchmark in this repository.
- The `last-reviewed` date at the top of this file is checked mechanically.
  When it goes stale the scorecard says so, which is the weekly refresh
  promise made enforceable rather than remembered.

## Corrections

If you maintain one of these tools and think this page describes yours
unfairly or inaccurately, open an issue. A correction to a comparison page is
worth more to us than the ranking it might cost, and this paragraph is here so
that promise is on the record.
