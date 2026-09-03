# Coordination, for the three streams

Read this before adding a command, an agent, a hook, or a state vocabulary.

## Which plan is current, and it is not this one

**The architecture of record is `docs/plan/ADR-2026-08-23-one-brother-repository.md`
in the BrotherModeUp repository.** It was decided by the founder on 2026-08-22,
scored four options against nine measurable criteria, and rests on a measured
inventory of all three trees (`docs/plan/MERGER-INVENTORY-2026-08-23.md`). The
sequencing of the work lives beside it in `ROADMAP-2026-08-23-REPLAN.md`.

This file does not decide architecture. It exists so the umbrella repository
and the three streams cannot drift apart, and it defers to that ADR wherever
the two disagree.

**Two things this file used to say that the ADR overrides, corrected here rather
than quietly deleted:**

1. **The surface caps are WITHDRAWN.** An earlier version of this page told both
   streams that no new skill, command, agent or hook could survive unless the
   combined surface fell from 79 to about 24, and `tests/test_surface.py`
   enforced it. That was wrong. The ADR's criterion C4 freezes the tool surface
   (no skill or command renamed, no new public command) and its criterion C3
   keeps each product installable alone. Its inventory found the reason:
   **five colliding skill names across the three trees.** Collapsing to one
   namespace would require renaming them, which C4 forbids. Any work deleted on
   the strength of the old caps was deleted for a reason that no longer holds.
2. **"One product, one plugin" is rejected.** The chosen shape is Option B: ONE
   REPOSITORY, THREE PLUGINS, ONE MARKETPLACE. One manifest would make the three
   a single install unit, so someone wanting assurance alone would be made to
   take execution provenance with it.

## The end state, from the ADR

```
Brother/
  .claude-plugin/marketplace.json   three plugins: ./plugins/brothermode, brothersbe, brotherds
  plugins/<product>/                each tree at a named commit, each with its own manifest,
                                    VERSION, CHANGELOG and tag prefix
  contracts/                        the passport and the handoff package, shared, owned by neither
  gates/                            master copies of the delivery scans
  scripts/local-gates.sh            one runner, one receipt per plugin plus one for the merged surface
```

Three VERSION files, three CHANGELOGs, three tag prefixes (`brothermode-v`,
`brothersbe-v`, `brotherds-v`). Products move at their own pace.

## When the merge happens: SIX conditions, not two

An earlier version of this page named two gates. The ADR's timing gate names
six, and it supersedes. Summarised, with its own checks named there:

1. Phase 1 rows closed and the current release installed by the team, with the
   two lead reviewers' retest recorded.
2. Passport conformance green on all three sides on the SAME fixture bytes.
3. The claims product's internal context separated and its pull request 1
   resolved.
4. The PRODUCT-DIRECTION amendment naming the merge landed by the founder.
5. and 6. as recorded in the ADR.

Nothing moves out of any repository until that gate reads green. Every
preparation item is valuable on its own if the merge never happens, which is
what makes "do now, merge later" safe.

## What this umbrella is, today

Stage 0 only: a marketplace whose plugin sources are the two public
repositories where they stand, a charter, this file, a push gate, and the
programme page. **No code has moved and none will before the timing gate.** The
marketplace here points at GitHub sources; the ADR's layout points at local
`./plugins/...` paths. Both are correct at their own stage, and the switch is
part of phase 4, not a contradiction to resolve now.

## Do not build twice

- **No fourth state vocabulary.** Four already coexist: a task lifecycle, fence
  states, queue item states, and the chain stages. Reusing the wrong one by
  string matching is how they corrupt each other.
- **Every backlog item names the chain stage it serves.** Already true in both
  streams; it is what makes the merge mechanical rather than archaeological.
- **Anything crossing product lines belongs in `contracts/`**, not in one
  product's tree. A contract with two consumers has no single owner among them.
- **The change passport SURVIVES.** It stops being a cross-repository contract
  and becomes the internal module boundary. Only the ratification ceremony
  around it dies. Do not delete it in anticipation.

## The collision found on 2026-08-22, and its resolution

The provenance stream's queue carries **eleven queued items naming
`verified-reality`**. The assurance product's north star already sequences that
same stage as its work. The claims product's Verified Claim Rate is a
measurement at that node. Three streams, one chain node.

**Resolved by the founder the same day: the stage is OCCUPIED PER UNIT, not
owned.** The three are not asking the same question there.

| Product | Unit | The question at `verified-reality` |
|---|---|---|
| provenance | a session's work | was the work done as described |
| assurance | a change | did the change actually work in production |
| claims | a claim | did the number turn out to be true |

Neither implements another's mechanism. That extends the rule the pair already
applies to provenance and assurance one node earlier, rather than reopening it.

**ANSWERED the same day, and it found one duplicate.** The provenance stream
read its thirteen queued and blocked `verified-reality` items against the table
above. Twelve sit cleanly on the work-was-done side: R1 to R6 are its reality
record store's own integrity, O6, O8 and O16 re-check its published claims
against its tree, H1 computes its own queue numbers, G2 is its adoption
evidence.

**H5 was the duplicate.** "Nothing records what happened after merge" names the
sibling's files and computes whether a CHANGE worked in production. That is the
change unit, and it is already the assurance stream's own WBS row S5. It is now
a cross-reference there rather than work, so one implementation was avoided
before it was built twice. That is the concrete saving of this coordination
round, and it was found by reading a list against a table, not by arguing about
ownership.

## The release-blocking subset, the answer to the question that had no date

Named by the provenance stream on 2026-08-22, from its own queue. Everything
else in its 81 queued items is product quality debt that phase 1 sequences and
the merge does not wait on.

MERGE-P3 (the passport's second-consumer sentence), MERGE-P5 (passport
conformance, one digest on three sides), MERGE-P16 (relocatable batteries: the
assurance runner hardcodes a trusted ref and a root-relative path, so a fresh
extraction refuses and no rehearsal can run), MERGE-P17 (root ceremony and board
paths resolve from the working directory), MERGE-P18 (the front door design),
MERGE-P19 (the context budget), MERGE-P1 with M13 (one fence owner, and owner
recognition until it lands), MERGE-M1 to MERGE-M5 (the rehearsal and move
mechanism), MERGE-G7 (the claim-unit gate, tracked there as a gate row with no
work on that side).

Plus one non-queue blocker in the founder's own hand: the PRODUCT-DIRECTION
amendment naming the merge, which is timing-gate condition 4.

**This is what turns "merge when the backlogs finish" into a list somebody can
burn down.** It had been the open question of this programme since it began.

## What Brother asks of each stream

**Provenance stream:** confirm the eleven `verified-reality` items sit on your
side of the table above. Your ADR and roadmap are the plan of record for the
merge, and this repository follows them.

**Assurance stream: ANSWERED 2026-08-22, and it corrected this page.**

Its pull request queue is at two (48 and the pointer 58), and it is BLOCKED ON A
NAMED REASON rather than idle: the founder tags 3.3.0, then 48 merges, because
48 carries its own 3.3.0 release commit and must land after the tag, in one
lane, battery green at the merge commit. Recorded here as blocked on "founder
tags 3.3.0, then 48 and 58 merge", which is what an honest gate reading looks
like. Least rework holds either way: 48 lands BEFORE any file moves, so nobody
rebases 19,617 additions across a moved tree.

**The version claim on this page was wrong and is withdrawn.** It said three
surfaces disagreed (tag v3.2.0, manifest 3.2.1, tree 3.3.0). The stream checked
its own tree: VERSION, both manifests and the CHANGELOG top entry all read
3.3.0 uniformly. The 3.2.1 figure was stale when I quoted it. The only gap is
the newest git TAG, because 3.3.0 is delivered on main and untagged, and tagging
is the founder's own act. That is a PENDING TAG, not a migration defect, and the
difference matters: one is somebody's decision outstanding, the other is a
latent bug.

**Both:** the pointer pull requests (BrotherModeUp 47, BrotherSBE 58) add one
README section and nothing else. The assurance stream has accepted 58 into its
tag-gated flow.

**Both grants confirmed on the assurance side, 2026-08-22:** the third
read-only consumer of the change passport is authorised in its seam
specification, and the five-item handoff wire format will be written there as
`contracts/handoff-package.v1.json`, versioned, with a fixture and a test.
Neither adds a command, an agent or a hook.

**Assurance stream reports a capability, 2026-08-24: host portability, at
3.4.1.** Reported here rather than rebuilt here, which is the whole point of a
router: `.claude-plugin/marketplace.json` already resolves `brothersbe` to that
repository at 3.4.1 and `bundle` already depends on `brothersbe@^3.4.1`, so
this capability reaches a Brother install with no file moved and no second
implementation. (Both pins moved to 3.4.2 on 2026-08-24 when the leaf
released; the caret range still carries this capability. The 3.4.1 figures
above are left as written because this is a dated report of when the
capability arrived, not a live statement of what the pins say today.) What the row in the README now names:

- **The assurance gates reach a verdict on Bitbucket, not only GitHub.** Eight
  capabilities are at parity and each was proven by running it, listed with its
  proof in that repository's `docs/plans/2026-08-18-bitbucket-parity-remaining.md`.
  The flagship approval gate was checked on a clone pulled back DOWN from
  Bitbucket rather than on the working copy that made it.
- **The hooks no longer need a POSIX shell.** Both shell hooks are Python, so
  the undeclared Git Bash dependency is gone, and the autosave self-deadlines
  loudly as NO-DATA instead of being killed in silence.

Two limits travel with that capability, stated here so the umbrella does not
inherit a claim wider than the evidence:

- **Windows itself is UNVERIFIED.** No session has run this on a Windows
  machine. `tools/test_sbe_windows_sim.py` recreates the conditions that make
  Windows different on whatever host runs it, which is the mechanism and not
  the platform, and `docs/WINDOWS-CHECK.md` is the protocol for a person with a
  real box.
- **The Bitbucket pipeline has never executed on a real workspace.**
  `ci/bitbucket-pipelines.yml` parses and has never run: no session holds a
  Bitbucket API credential, and an SSH key can neither enable Pipelines nor
  call the REST API. The four steps that close it are in that same parity
  document, and the free plan's fifty build minutes a month make it one
  deliberate run rather than a habit.

This entry adds no command, agent, skill or hook to Brother, so the surface
caps in `tests/test_surface.py` are untouched.

## Brother page

https://claude.ai/code/artifact/687e8f36-3551-4a7e-9b29-f0a9ac2848af
