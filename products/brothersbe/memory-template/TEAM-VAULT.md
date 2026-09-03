# The team vault pattern

The shipped vault template assumes one person. This file is the pattern for a
team sharing one vault, and it is deliberately short on enthusiasm: most of what
follows is a constraint, and the constraints are the useful part.

Everything here is about the KNOWLEDGE plane. Code lives in git. Truth lives in
the ledger and the receipts. The vault holds working memory: what a project is,
what is open, what bit us before, what was decided, and the handover that lets a
stranger continue tomorrow.

## Law one: the vault is async

There is no live co-editing. Obsidian's own collaboration help page states plainly
that it "does not yet support collaborative live editing on the same file"
(https://obsidian.md/help/sync/collaborate). No shared cursors, no presence, no
operational transform. An edit appears when the other device has synced, and not
before.

Note that the product's own Sync marketing page uses the phrase "real-time note
updates across team devices" (https://obsidian.md/sync). The two official pages
are in tension and the specific one wins. Read "real-time" as "propagates fast
once synced", never as "two people can type in the same note".

Three consequences, all of them load-bearing:

1. **Design for eventual sync.** Never build a process step that depends on a
   teammate seeing your edit within a bounded time. If it has to be seen now, say
   it in chat and record it in the vault afterwards.
2. **One writer per note.** The fence discipline the repository uses for files
   applies to notes. Two people editing the same sentence is a human coordination
   failure that no merge tool will save you from, and the merge settings below
   deliberately make it worse rather than better in exchange for making the
   common case painless.
3. **Never make the vault the only home for anything.** A vault sync that has not
   landed yet is indistinguishable from a vault that never had the note.

## Sharing pattern: git, with union merge on markdown

Two paths are widely used. Paid Sync invites up to 20 collaborators to one shared
vault, and every collaborator needs their own active paid subscription
(https://obsidian.md/help/sync/collaborate). Git is the other, and it is the one
this pattern assumes, because the team already has git, already reviews changes in
it, and already has history.

Plain git merges markdown line by line with no note-aware logic, so two people
appending different bullets to the same daily note produces a conflict that is
pure noise. The practitioner fix is a union merge driver on markdown plus
ignoring the local config directory
(https://forum.obsidian.md/t/team-colaboration/69608).

Put this in `.gitattributes` at the vault root:

```text
*.md merge=union
```

And this in `.gitignore` at the vault root:

```text
.obsidian/
99-System/telemetry/
```

What each line buys, and what it costs:

- `*.md merge=union` keeps BOTH sides of a conflicting hunk instead of raising a
  conflict. Two people adding different lines to the same log is now a non-event.
  The cost is real and must be understood: when two people edit the SAME sentence,
  union merge silently keeps both versions rather than asking. Nothing warns you.
  This trade is correct for notes that are mostly additive (session logs, meeting
  notes, open-item lists) and wrong for a note two people might rewrite. For those,
  one writer per note is the only control, and it is a human one.
- `.obsidian/` ignored keeps per-person plugin state, themes, hotkeys and workspace
  layout out of the shared repository. Without this, every teammate's window layout
  fights every other teammate's on every pull.
- `99-System/telemetry/` ignored keeps machine-local session counts and message
  excerpts out of a shared repository, which is the same rule the single-user
  template already states.

If you use the obsidian-git plugin to drive this from inside the app, read its own
warnings first: the maintainer flags mobile support as highly unstable, with no
SSH auth on mobile, no rebase, no submodules, and the app able to crash or hang on
clone or pull with large repositories
(https://github.com/Vinzent03/obsidian-git). Desktop is the supported case.

## The frontmatter schema is fixed

Every note carries exactly these five properties. Not more by default, and never
fewer.

```text
---
type: session
project: team-operating-model
status: open
owner: the-driver-handle
date: 2026-08-01
---
```

- **type**: what kind of note this is (session, decision, failure, overview,
  open-items, handover, reference).
- **project**: which project folder it belongs to. One value, never a list.
- **status**: open, closed, or superseded. Three values, chosen so a dashboard can
  group by it without a formula.
- **owner**: one person. Not a team. An owner field holding a team is the same
  defect as an approver field holding a team.
- **date**: ISO date. The day the note was written, not the day it was last
  touched.

This is a schema, not a scratchpad, and the reason is mechanical rather than
aesthetic. Inconsistent property names or types (a string `todo` in one note, a
list `[todo]` in another) is the single most common reason team dashboards
silently miss rows
(https://www.dsebastien.net/the-complete-guide-to-obsidian-properties/). A
dashboard that misses rows is worse than no dashboard, because it reads as
complete.

Enforce it with a template that pre-fills the property block rather than trusting
people to type keys freehand. Freeform metadata is refused here on purpose.

## Bases and Dataview: which one, when

Both exist, they overlap, and picking per view rather than per vault is the
practical answer.

- **Bases** is a first-party core plugin, no install needed. It turns a filtered
  set of notes into database-style views (table, cards, list, and a map view)
  driven entirely by frontmatter properties, saved as a `.base` file or an embedded
  code block (https://docs.obsidian.md/plugins/guides/bases-view). Use it for the
  no-code views everyone on the team should be able to build and change: open items
  by owner, sessions this week, failures by project.
- **Dataview** is a third-party community plugin with a query language and a
  JavaScript API (https://github.com/blacksmithgu/obsidian-dataview). It is the
  scripted layer, notably stronger at aggregating tasks and computing derived
  values across notes. Use it where a view needs arithmetic, task rollups, or
  logic a filter cannot express.

One warning that matters at team scale: an unfiltered Base "will provide an entry
for every file in the vault", and the official developer guide tells view authors
to virtualize rendering for exactly that reason
(https://docs.obsidian.md/plugins/guides/bases-view). Every shared Base starts with
a filter. A Base with no filter on a large vault is a performance incident waiting
for a slow laptop.

Build one canonical schema and many lenses over it, rather than one list per view.
The same notes become a per-person workload list, a per-project table, and a
board, by filtering and grouping differently.

## One fact, one home

If a fact lives in two places, one of them is already wrong and nobody knows
which.

- Code lives in git. Never paste code into the vault.
- Truth lives in the ledger and the receipts. The vault may LINK to a receipt. It
  never restates the receipt's verdict as prose, because prose does not re-run.
- A decision lives in the dossier's decision record. The vault's decision note
  points at it and records the story around it (who was in the room, what almost
  won), which is the part the record deliberately does not carry.
- The org-facing copy is the published mirror, not the vault. Publish outward;
  never let people edit the mirror and expect it to come back.

## Secrets never

No tokens, no keys, no passwords, no customer identifiers, no personal data. Not
in a note, not in frontmatter, not in a code block, not "temporarily".

This is not a style rule. It follows directly from the limits in the next section:
the vault has no access control to fall back on, so anything written into it is
readable by every person who holds the vault, forever, including in git history
after you delete it.

## Honest enterprise limits

State these to the organization before adopting, not after.

- **No single sign-on and no SCIM.** Access is not centrally provisioned or
  deprovisioned. A leaver keeps their clone.
- **No audit log.** There is no record of who read what, or when.
- **No per-folder or per-file permissions.** Sync collaborators get the same
  permissions as the owner, with one exception: only the owner can invite
  (https://obsidian.md/help/sync/collaborate). There is no read-only member and no
  restricted folder.
- **No native comments and no mentions.** Review happens somewhere else
  (https://ravoid.com/blog/obsidian-vs-confluence-knowledge-stack-decision/).
- **Access control is vault scope, and nothing finer.** One vault per team.
  Material that needs finer control lives in a system that has it. This is the
  whole mitigation, and it is a boundary rather than a feature.
- **Performance degrades on large vaults.** Community reports document unusable
  link autocomplete, slow cache loading and slow search somewhere in the range of
  a thousand to forty thousand notes, worse with Sync on mobile
  (https://forum.obsidian.md/t/slow-performance-with-large-vaults/16633). No
  vendor benchmark exists, so treat these as reports rather than thresholds. The
  community's own fix is splitting into purpose-specific vaults rather than
  growing one.
- **No large-organization proof point exists.** The largest documented internal
  use found in research is the vendor's own seven-person team, running planning,
  requirements documents and roadmaps in one shared vault alongside GitHub for
  code review and separate chat software (https://eu.36kr.com/en/p/3755031628005892).
  Nobody has published a hundred-person case study, in either direction. Claims
  about what breaks at that scale, including the ones on this page, are inference.

The conclusion the design draws from all of that: a regulated organization keeps
Confluence, or its equivalent, as the governed publish layer, and treats the vault
as the team's working memory. That is not a compromise reached reluctantly. It is
what each tool is actually good at.

## The handover note, which is the point of all this

The vault's most valuable note type is the handover, and it is the one with a
hard rule attached. A handover is two parts, never one: the written package plus
an explicit acknowledgment from the person taking it over.

The written half carries four sections and nothing else: DONE with the evidence
that proves it, IN FLIGHT with the exact stopping point, NOT STARTED, and OPEN
QUESTIONS. Every open item names an owner and a deadline. A bare status with
neither is not an open item, it is a worry.

The acknowledgment half is a message that ends in the receiver confirming. Until
it lands, the outgoing owner still owns the work, and the note's `status` stays
`open`. This is the part teams skip, and skipping it is how work falls into the
gap between two people who each believed the other had it.
