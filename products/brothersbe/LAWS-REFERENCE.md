# Laws reference: the expanded text behind each DIGEST.md line

This file is NOT injected anywhere and enforces nothing. It exists because the
injected digest must fit the session-start cap named in
`tools/sbe_sessionstart.py`, so each law there is one line, and the full
qualifications that line compresses live here. The law itself is `SKILL.md`
together with the `references/` files its routing table names (`SKILL.md` keeps
the three unconditional laws, L6, L11 and L14, and routes the other sixteen and
all six phases); where this file and those disagree, they win. Each entry keeps
the enforcement marker of its digest line: [checked: tool] means a script decides
it, [human] means nothing computes it.

This file is not a third copy of the law. It compresses nothing that the
`references/` files hold verbatim; it expands the digest lines, which is a
different job, and it is the only one of the three that enforces nothing.

## Phases

Phases in order, each gating the next: purpose (01-purpose.md), process
(02-process.md), architecture (03-adr.md, plus 04-technology-map.md at T3 only),
data (05-data-model.md), expression (06-diagrams.md), verification
(07-verification.md). Verification is last, never the theme. [checked:
sbe_design.py artifacts, for existence at the tier]

## L1 tier before work

Five intake answers to 00-intake.json, first match wins. The four yes/no answers
are a JSON boolean or one of y/yes/true/n/no/false, the fifth is none/some/many,
and any other value is refused by name rather than read for truthiness.
Sensitive or not reversible in an hour is T3 (all seven artifacts); contract
change or many consumers is T2 (01 02 03 05 06 07); one boundary or some
consumers is T1 (01); otherwise T0 (no dossier at all). [checked: sbe_intake.py
compute_tier, re-derived and compared by sbe_design.py artifacts]

## L15 overrides

An override sets both a tier and a reason, in the `override` and
`override_reason` fields, and they must agree with each other. A tier moved with
a null reason, with a null `override`, with a reason under three words and
twelve characters, or with a reason that names the absence of one (tbd, n/a), is
an edit and FAILs, naming BOTH fields, the written tier, the computed tier, and
the direction. Only the reason used to be enforced, so the half-declared
override was the path every file sbe_intake.py writes starts on. An `override`
recording the tier the answers already compute moved nothing, and the evidence
says so. Whether a reviewable reason is a GOOD one is enforced nowhere: the law
used to say every override surfaces at the weekly review, no step did that, and
the claim was withdrawn rather than dressed up. [checked: sbe_design.py
artifacts, for the threshold only]

## Design checks (L2 to L5 and placeholder)

tools/sbe_design.py, advisory in session, --strict blocks in CI, run over every
directory holding a 00-intake.json OR any of 01 through 07, so deleting the
intake file does not make a dossier invisible; the walk always runs, including
when the search root is itself a dossier, because one stray 00-intake.json in a
repository root used to hide every dossier below it at exit 0; somebody else's
code is pruned by a marker INSIDE a directory (pyvenv.cfg, .dist-info,
CACHEDIR.TAG, site-packages under lib/pythonX.Y, node_modules package metadata)
and never by a directory's name alone, and a pruned directory that holds
evidence a check reads is named in that check's evidence line; a dossier without
its intake FAILs naming it, and one carrying .sbe-exempt that NAMES the checks
it waives and gives a reason meeting the tier-override threshold is reported as
a WAIVER naming the directory and every check it waives, while one that names no
checks, names an unknown one, or gives no reviewable reason does not exempt at
all and FAILs.

- L2 artifacts: every artifact the tier requires exists, carries content of its
  own, and names subject matter the rest of the dossier also names, so neither a
  zero-byte file, nor a file of headings with nothing under them, nor a purpose
  about bananas beside a data model about refunds clears a tier; a tier that
  requires no artifact reports NO-DATA rather than passing over nothing.
- L3 adr: two DISTINCT rejected alternatives, each carrying at least two words
  and eight characters of its own text, whether that text says why the option
  lost being human review rather than a check, written as bullets, a numbered
  list, one sub-heading each, or one paragraph each, under a heading containing
  any of rejected, alternatives, options, roads not taken, not taken, not
  chosen, ruled out, discarded, dropped, declined, did not pick, did not choose,
  why not, so MADR's own `## Considered options` is read; plus criteria,
  decision, consequences, flip condition.
- L4 datamodel: entities read as bullets or as table rows under a heading whose
  name contains "entit", which is the only form an entity COUNT is asserted
  over; where no heading names entities the bullets that name an owning system
  are still read and checked, the rest are left as the prose they are, the
  verdict names what it read and how to declare entities properly, and its best
  verdict is NO-DATA rather than PASS; every entity names the system that owns
  it with a value, in any of system of record, system of truth, source of truth,
  book of record, authoritative source, mastered by, owned by, owner, SoR, on
  the bullet or as a table column heading; every relationship carries a
  cardinality as its own token, spelled out (one-to-many), as crow's foot (1:N,
  N:1) or as UML multiplicity (1..*, 0..1), and the FAIL text names the accepted
  set.
- L5 diagrams: a diagram exists inside a fenced block, and every node is either
  an entity in 05-data-model.md or a runtime component declared in
  04-technology-map.md or under a Components heading, matched on the node id OR
  on its label so `A[Customer] --> B[Order]` is read as the standard Mermaid it
  is; flowchart, graph, sequenceDiagram, erDiagram, classDiagram, stateDiagram,
  C4Context and block-beta are all read with their own grammars, a state
  diagram's states trace to states declared under a States, Status or Lifecycle
  heading and are NO-DATA rather than a failure where the dossier declares none,
  a diagram type that declares no traceable nodes and a dialect this tool has no
  parser for are both NO-DATA rather than a failure, and every token the parser
  skipped is named in the evidence line, inline edge labels included.
- placeholder: no artifact is still the shipped template.

[checked: sbe_design.py]

## L12 decision tables

tools/sbe_decide.py over the shape table in tables/architecture.json: every
recommendation carries its deciding criteria and its OWN flip condition, not one
string shared by the whole table, because a shared one named conditions that
were already true for half the recommendations and so could never fire; no
criterion contributing is NO-DATA with the recommendation suppressed; a value
matching nothing is reported as unrecognized, not ignored, including a number
outside every range a criterion scores, which used to be dropped in silence. One
table ships; integration, storage, consistency and failover are human review
until theirs land. [checked: sbe_decide.py recommend, fixtures in evals/]

## The four hard gates plus the lint (L7 to L11)

tools/sbe_gate.py, --strict blocks in CI, never waived by impatience.

- L7 numbers: a snapshot id that is an answer rather than a placeholder, a
  second derivation that still computes something once comments, case,
  whitespace and trailing punctuation are removed AND differs from the first, a
  re-run claimed by the boolean true, and zero drift between two REAL NUMBERS,
  so an infinity and a not-a-number are refused; every reduction is applied
  BEFORE the vacuity and number tests, never after, because a derivation of `#`
  folds to nothing and used to buy the strongest sentence this project prints.
- L8 migration: both legs against a restore, a rehearsal id recorded as a
  string, row counts recorded as WHOLE NON-NEGATIVE numbers and matching, so -1,
  2.5 and inf are refused while 0 is a count; a receipt with no row counts is
  NO-DATA because the gate cannot assert the half it never read, and counts
  recorded as blanks or as "unknown" are a failure rather than a matched
  comparison.
- L9 approval: a signed Approved-by trailer this host VERIFIED, naming somebody
  OTHER than the commit's author and committer, is the only thing that PASSes;
  self-approval FAILs, because a signature proves a key holder signed and cannot
  prove a second party looked; a typed name fails; an unverifiable signature is
  NO-DATA, not an approval, and so is a Reviewed-in id, which nothing resolves
  and which the agent writes, so it points a human at a review rather than
  proving one happened, and which may be written as several words, since
  requiring one whitespace-free token FAILed a commit that recorded a real
  review id; a Reviewed-in id that is a hyphen or another vacuity token FAILs.
- L10 ran: every recorded check names what ran, has a zero exit code, and
  records a POSITIVE duration, so minus five milliseconds is a broken record and
  a nameless row fails for being unidentifiable.
- L11 lints is the fifth thing CI runs under --strict, so L7 to L11 are refused,
  not waived.

[checked: sbe_gate.py, sbe_score.py]

## Receipts and the NO-DATA class

Receipts have three states, not two: no receipt is NO-DATA, a receipt recording
zero items is NO-DATA and says so, and a receipt that exists and cannot be
parsed FAILs. Emptiness is a VALUE test, not a file test and not `is None`: "",
" ", null, [], {} all record nothing, while false and 0 record something,
because a zero row count and a zero exit code are answers. And a value that is
present and non-empty can still record nothing: TODO, unknown, n/a, "-" and
anything carrying no letter and no digit at all are refused by one shared list
(sbe_checks.VACUOUS_VALUES, with answered(), answered_as(), numeric(), count()
and distinct() beside it) rather than by a private constant in one tool that the
gates never imported. That list is SCOPED, in one place: it applies in full to
evidence fields, the things a person fills in to prove work happened, and minus
the words an engineer can honestly mean (none, pending, unknown, unclear,
undecided, null, nil) to domain content, the entity names, state names and node
labels the engineer authored, because `pending` is a vacuous receipt value and
the first state of every payment system in existence, and one list served both.
Every threshold counts DISTINCT items, so a figure, a rejected alternative, a
sealed prediction or a rating written twice counts once. A timestamp in the
future is a broken record and FAILs rather than reading as an age below zero.

Two claims here, and only these two: (1) the DECLARATION is enforced, since
sbe_checks.py refuses to register a check that names PASS as its empty state or
that supplies no worked example to hollow, and (2) the RUNTIME is tested, since
evals/test_no_data_class.py imports every .py file in tools/ (not a name
pattern: a registry shipped as tools/quality.py was invisible to a walk that
filtered on sbe_*), fails on a module it cannot import and on a registry it
cannot invoke, and hollows every leaf, subtree and whole receipt of each
declared example nine ways: empty, whitespace, null, the vacuous tokens TODO,
n/a and "-", and the tokens that say nothing only AFTER the tool reduces them (a
lone comment, a block comment, a lone semicolon, plus a comment carrying words
and the non-measurement "inf" over the whole fixture), requiring that none of it
produces a PASS. That is an enforced declaration plus a mechanical sweep, and it
is not a proof over all inputs; the sweep prints its own coverage, its skipped
cases and its exemptions on every run so the claim is checkable rather than
asserted. A check that crashes, or whose evidence context cannot be built, is
reported as a FAIL carrying the exception, never as a missing line. [checked:
sbe_checks.py, evals/test_no_data_class.py]

## L11 silent-failure lints

tools/sbe_score.py: bare except, except-then-pass, discarded subprocess result,
conflict-skipping upsert (read as SQL in any scanned language, so it fires on a
.sql file, which it could not before), force-try. The lint skips its own file by
PATH, not by basename, and names the skip in the evidence, because skipping by
basename hid any file called sbe_score.py in the caller's own tree. A reviewed
exemption carries a visible # sbe: allow-silent <reason>, and the reason is
READ: a bare marker waives nothing. A run that opened no file is NO-DATA naming
why, never "clean", and so is a run in which every file scanned held a match and
every one of those matches was waived. Any surviving exemption is counted and
named in the evidence, the first five of anything it lists, followed by how many
it did not name. [checked: sbe_score.py, needs a lint root]

## L13 one writer per file

Fence then dispatch, in your registry, tier-tagged, closed with an inline
evidence block. [checked: sbe_score.py fence-hygiene and budget-vs-tier, only
over registries named in BROTHERSBE_REGISTRIES (the skill's own STATE.md is no
longer added to that list) and only for fence lines containing the word "agent";
writing the fence, comparing file scopes, resuming after a kill, and the
evidence block in the close are human]

## L16 strictness is a human edit

A session instruction never waives a hard gate: --strict changes only by a human
editing the CI workflow, which is visible in the diff. Nothing here makes that
edit require a review: no CODEOWNERS and no branch protection ships, so the
review half is your repository's setting. The workflow also runs
evals/run_evals.py, evals/test_no_data_class.py and tools/test_sbe.py, and it
guards nothing until you copy it into your own repository. [checked:
.github/workflows/brothersbe-gates.yml, once copied]

## L17 the close

The run closes on disk: session log written, open items and failures index
updated, every scorecard line naming its evidence, self-score capped at 8
without external evidence, Remaining and Unverified stated rather than implied.
[checked: sbe_score.py vault-log-per-active-day, for the log only, and only
where BROTHERSBE_VAULT points at a vault, which the shipped CI does not set; the
rest of the close is human]

## Telemetry

Telemetry is hook-written and idempotent (voluntary logging collapses). Team
learning spreads only through a reviewed PR into LEARNED.md; no colleague's tool
changes behavior silently. Local telemetry never leaves the machine. [the
SessionEnd hook WRITES the ledger this feeds and decides nothing, so it is not a
check and no CI step reads it; the checks fed by it are named on their own
lines. The rest of this line is human]
