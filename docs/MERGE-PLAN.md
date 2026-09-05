# The Brother Merge Plan

This is the plan behind the umbrella, the record of every decision the founder made on 2026-08-22 to reach it, the council positions that shaped those decisions, and an honest account of what this stage did and did not do. `docs/CHARTER.md` holds the vocabulary this plan assumes; read that first if a term here is unfamiliar.

## The staged plan

**Stage 0, now.** This repository exists, public, as the marketplace and the router. It carries the charter, this plan, the coordination notes to the other two streams, and the surface caps a future file move must pass. No product code moves. BrotherMode and BrotherSBE keep shipping in their own repositories.

**Stage 1, conditional on two gates.** Both gates below have to clear. When they do, BrotherMode and BrotherSBE physically merge into this repository as a modular monolith (core, provenance, assurance, claims, surface), archived read only in their old locations with a pointer back here, and Brother earns 1.0.0.

**Stage 2, last.** BrotherDS joins by a clean extraction of its shippable files, after the Stage 1 surface cut is proven to hold in practice, never by carrying its git history, because that history holds names from real client engagements that must never reach a public repository.

## The two gates for Stage 1

Decision 3 sets one merge gate with two parts; both are required, neither substitutes for the other.

- **Gate one, BrotherDS.** One claim has been scored against a real outcome. This proves the claims domain works end to end, on a real number, not only as a specification in `docs/CHARTER.md`.
- **Gate two, BrotherSBE.** Its open pull request queue is drained to zero, where a DRAFT does not count (founder ruling, 2026-08-24: a draft is its author saying not ready, so it is not work in flight a merge would have to rebase). This means a physical merge does not have to rebase roughly thirty two thousand added lines of work in flight, most of it in PR #48 alone.

Neither gate carries a date. Both carry a check: gate one resolves the moment a claim's outcome is scored, gate two resolves the moment `gh pr list` against BrotherSBE returns no non-draft rows. Measured 2026-08-24 at 11:32 +07: one row, number 62, isDraft true, so it is clear.

## Decisions recorded, 2026-08-22

Twenty four decisions, all made in one sitting, all dated 2026-08-22, recorded below in the order the founder gave them. Where a decision names a number, a repository, or a file, that name is copied from the founder's own words or from the ground facts verified the same day; nothing below is a restatement dressed up as a new fact.

1. Shape: staged. An umbrella repository now, marketplace and router, code stays where it is; a physical merge only after the evidence gates above clear.

2. Unit: a claim vocabulary. One claim vocabulary and one verdict tuple across three domain stores with different lifetimes, not one runtime store, and not three unrelated units either.

3. The merge gate for Stage 1: one BrotherDS claim scored against a real outcome, and BrotherSBE's open pull request queue drained to zero. Both required. (AMENDED 2026-08-24 and annotated rather than rewritten, because this list is a dated record of what was decided on 2026-08-22: a DRAFT does not count against that queue. The live definition, with its check, is in 'The two gates for Stage 1' above.)

4. BrotherDS timing: last, after the Stage 1 surface cut is proven to hold; joins by a clean extraction of files, never a copy of history.

5. Name: Brother.

6. The old repositories, after Stage 1: archived read only, with a pointer README pointing here.

7. Version: 0.x now; 1.0.0 is earned at Stage 1 completion, not assigned ahead of it.

8. Visibility: public from the first commit, with an absolute cleanse gate holding from commit zero: no name but Khalil Maaouni, no client terms, no AI-tool attribution watermarks.

9. Cut target: roughly 79 surfaces today, cut to about 24. Arithmetic note: decision 11 (keep all thirteen agents) sets the real floor at 31, which is still a cut of about 61 percent.

10. Commands: four, status, next, verify, help, each taking a lens argument.

11. Agents: keep all thirteen, whole, across the three products.

12. Enforcement of the cut: a selftest count assertion lands before any file move, not after.

13. Entry point: one bootstrap skill, `using-brother`, injected by one SessionStart hook, routing by skill descriptions rather than a menu.

14. Growth control: a written refusal policy in the README, in the same style as the largest reference marketplace measured for this plan.

15. Reference material: a lean core plus a separate reference pack, installed on demand, kept out of the core surface count.

16. North star: the chain to verified reality governs the whole of Brother; Verified Claim Rate is reported for the claims domain only.

17 to 20, answered together with one directive from the founder, quoted verbatim:

   > For now just prepare the ground for that merger until we finish the backlog for BrotherMode and BrotherSBE so it is seamless transition and merger, Coordinate with the other two streams so the integration happens at the right time and in the easiest way while developing what is missing for BrotherDS.

21. Create khalilmaaouni/Brother now, public, as the umbrella, with no code moved into it.

22. Coordination: `COORDINATION.md` lives in Brother, plus one pointer pull request into each sibling repository.

23. Progress page: one Brother progress page, `GANTT.html`, with a stable link republished at every closed loop.

24. The ninety day success criterion: one outside human runs Brother end to end, evidenced by a fork, an issue, or a session log from a machine that is not this one.

## The council verdicts

Four seats reviewed the shape of Stage 0 before the founder ratified the decisions above.

| Seat | Position |
| :-- | :-- |
| Architecture | One repository, modular monolith (core, provenance, assurance, claims, surface), two install targets. The claim works as shared vocabulary, forced as one runtime metric. The change passport survives as the internal module boundary. BrotherDS extraction goes last because publishing extracted files is the one irreversible day-zero act. The surface cap must be a selftest assertion landed before any file move, not a report written after one. |
| Adversary | Merge after conditions, not before. Zero stars, zero forks, zero outside users on both public repositories means a rename costs nothing today. Merging now would spend roughly thirty two thousand added lines of open pull request rebase work and promote a never-used unit to the spine of three working products. The cheapest counterproposal is exactly this umbrella repository. |
| Packaging | One bootstrap skill routes by description rather than a menu of choices. Commands cut from fifteen to four. Hook dispatch runs through one script, not one hook per concern. Agents are reserved for a real model tier or isolation boundary, never for cosmetics. The surest way to recreate the bloat this plan cuts is to give every feature its own front door. |
| Research | The largest reference marketplace measured for this plan stays small, at a fraction of a percent of its own star count in skill count, by a written refusal policy stated in its own README, not by an automated gate. Official plugin documentation already models multi-plugin composition as a marketplace catalog, which is the shape this repository takes. A well known shell-framework's plugin sprawl is the standing cautionary tale for what happens without a refusal policy. |

## What "finished" means, proposed

Decisions 17 to 20 name a condition, "until we finish the backlog," without naming a date, because the founder did not give one. This plan proposes a definition for the founder to ratify or amend; until ratified, this is a proposal, not policy.

- **For BrotherMode:** the release-blocking subset of its 67 queued backlog items, named by the BrotherMode stream itself, all at zero. Not the full 67; not the 3 already blocked; the subset that stream calls release-blocking, by name, in its own queue file.
- **For BrotherSBE:** its open pull request queue at zero, and the exit of its own fortnight-to-product-grade plan (PR #48 and its siblings), not merely a reduced count.

Stated plainly: "until the backlogs finish" has no date attached to it anywhere in this plan, and without the definition above, or the founder's own replacement for it, Stage 1 has no way to arrive, because nobody would know when either backlog has actually reached zero versus merely gotten smaller.

## What this stage did not do

Recorded here so nobody mistakes ground-laying for the merge itself.

- Did not move a single line of BrotherMode's or BrotherSBE's product code into this repository.
- Did not touch either sibling repository beyond one pointer pull request each, per decision 22.
- Did not build the claim vocabulary as a working, shared runtime; it exists in `docs/CHARTER.md` as a specification, proven today in exactly one place, BrotherDS, and not yet forced across all three.
- Did not extract BrotherDS in any form; that is Stage 2, after Stage 1, not this stage.
- Did not ratify "what finished means" above; it is a proposal awaiting the founder's decision.
- Did not build the `using-brother` bootstrap skill, its SessionStart hook, or the four commands named in decision 10 and 13; those are implementation work for after this ground is laid, not part of standing the umbrella up.
- Did not automate the refusal policy with a hook; it is written discipline in the README today, checked by `tests/test_surface.py` only at the moment a surface actually lands.
- Did not check either Stage 1 gate as passed; both are reported here as open, to be checked by hand against this plan until an owner builds the automated check.
