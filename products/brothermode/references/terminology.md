# Beginner terminology map

LOAD WHEN: writing anything the user will read (chat replies, status, cards, docs), or deciding whether a word is internal machinery or plain language.

The rule (roadmap section 1): the user never needs the internal terms below to
use this product. In every user-facing sentence, use the plain wording in the
right column. The internal term may appear only when the user has explicitly
asked for the advanced view (see references/status-view.md).

| Internal term | Plain wording used in ALL user-facing output |
|---|---|
| fence, file claim | "only one worker edits a file at a time" (the mechanism is not named) |
| work record, lifecycle identifier | "task history" (never shown by default) |
| store | "your project's records" |
| retrieval run | never surfaced at all |
| receipt | "your recorded approval" |
| worktree, branch | "a separate draft workspace" |
| model routing, model tiers | "picking the right helper for the job" |
| token accounting, token budgets | "work budget", stated as token ranges with a plain gloss on first use (for example "about 25k-45k tokens of model work"); "tokens" itself is allowed in ranges, matching every status and forecast example |
| hook, hook payload | "automatic session records" (never shown by default) |
| runtime adapter | "support for other AI coding tools" |
| vault | "project memory" |
| telemetry | "session records (written by the machine, not the model)" |
| orchestrator | "coordinator" |
| triage | "sizing up the task" |
| insight ledger | "the record of what was decided, why, and what proved it" |
| evidence class | "how a claim was checked", said as one of four plain labels: "verified by command", "measured", "verified by inspection", or "my reasoning, not verified" |
| briefing | "a short catch-up on where the work stands"; the command that asks for one is named `/brothermode:brief`, and the catch-up's own text says catch-up |
| handback | "handing this back to you", written as the option itself ("Hand this back to me: I take this decision and the work under it") and never as a noun |
| active minutes | "time actually spent working on your project, not time on the clock" |
| watchdog | "the automatic check that offers you a catch-up once enough work has happened"; the word watchdog belongs to the pages that disclose it (SECURITY.md and docs/KNOWN-LIMITS.md), never to a status line |
| handover pack | "the handover pages": one folder another person can pick the project up from |
| trace tag | "the record id at the end of a line", shown on the handover pages so any claim can be traced back to the record it came from, and not shown in the default status view |
| live project view | "the page that shows where your project stands": one file called PROJECT-VIEW.html at the top of your project folder, written from your records. It is a picture of your records at the moment it was written, not a live screen, and the page says that about itself |
| insight box | "what I now believe, what proved it, and what would change it": the short block used whenever something is learned, always including the line offering to hand it back to you |
| alert rung | "how much attention something needs", said as one of four plain labels: "needs you", "at risk", "for info", or "settled". The word rung belongs to the machinery pages, never to a line you read |
| empty state | "the short note in a section nothing has filled in yet", saying what will be there and naming the one thing that fills it |
| fingerprint | "a short code that changes when your records change", printed on the page so an old tab is visibly older than a fresh one; never called a hash in anything you read |

The eight rows from "insight ledger" to "trace tag" were added for the founder
mode work of 2026-08-05 (docs/program/absolute-lead/DESIGN-L04.md, section
15.3), and the five below them for the visual surface of the same day
(docs/program/absolute-lead/DESIGN-visual-surface.md, section 12.3). Both sets
landed before any of those terms was allowed into a sentence a user reads. The
rows above them are unchanged, and the plain wording in the right column is what
user facing output says: the internal term on the left is for the advanced view
and for the machinery pages that have to name the thing they are disclosing.

Three working rules:

1. Plain wording is not a euphemism. It must state the same fact. "Only one
   worker edits a file at a time" is the whole truth of the fence; nothing
   about the rule is weakened by saying it plainly.
2. When a user asks what a plain phrase means, explain it in one sentence,
   still without the internal term, unless they ask for the machinery itself.
3. Advanced view is opt-in per request, never sticky by assumption. After
   answering an advanced question, the next default output returns to plain
   wording.

This map is the shared vocabulary for every command, card, status view, and
document in this project. A new internal term gets a row here before it is
allowed to exist in user-facing text.
