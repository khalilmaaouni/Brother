# The default status view

LOAD WHEN: the user asks how the project is going, a status is due at a phase boundary, or you are about to report progress in any form.

## The default view: exactly these eight fields, in this order

```text
Goal
Direction
Progress
Time remaining
Decision needed
Risk
Evidence
Next step
```

Rules for filling them in:

- Goal: the outcome in the user's own words, one line.
- Direction: the approach currently agreed, one line.
- Progress: accepted work over planned work, in plain units ("4 of 9 tasks
  accepted"), never percentages of effort you cannot measure.
- Time remaining: always a range with confidence, never a point date
  (references/forecasting.md governs the format).
- Decision needed: the one decision waiting on the user, or "none". If there
  is one, it travels as a decision card (references/kickoff.md), not prose.
- Risk: only risks whose severity changed or that need a decision. No
  standing worry list.
- Evidence: what proves the progress claim, in plain words ("the booking test
  suite passed after the last change"). Name the check, not the log.
- Next step: exactly one recommended next action.

Every word obeys the terminology map (references/terminology.md): no fences,
records, worktrees, tokens as raw counts, or hook names in this view.

## The advanced view: only on explicit request

Only when the user explicitly asks for advanced detail (for example "show me
the advanced view", "show the machinery", "which model did this") may these
appear:

- task IDs;
- runtime and model identifiers;
- token input and output details;
- worktree paths;
- commands that were run;
- raw test output;
- hook evidence;
- store verification;
- receipt fingerprints.

None of these ever appear in the default view. Advanced is per request: the
next default status returns to the eight fields above.

## IC mode: the same records, read by the person doing the engineering

IC mode is not a second status and not a second set of records. It is the same
eight fields above, from the same collector, rendered for an engineer: the
lifecycle identifier behind the file claim, unit ids and the run's own state,
raw token and minute totals against both ceilings, the evidence class and the
exact command behind a claim, which trigger made a decision a key decision, the
contract id with its revision and state, open dispatch ids with their ages, and
the counts behind the catch-up clock. Every field the two views share carries
the same value in both, because there is one collector and two renderers.

Three rules, and the first one is what keeps this honest:

- **It is always explicit.** Two switches turn it on and nothing else does: the
  `--ic` flag on the command, and the environment variable
  `BROTHERMODE_VIEW=ic`. The flag is per request. The variable is sticky, which
  is why the next rule exists.
- **Every IC render names the switch that turned it on**, in one footer line,
  with how to turn it off. A sticky mode that does not say it is on is a mode
  the reader did not choose, which is the rule at the end of the advanced-view
  section above read for a standing setting rather than one request.
- **The default view is unchanged.** With neither switch set, what prints is
  the eight fields at the top of this page, in plain wording, and nothing else.
  IC mode and the advanced view are independent: `--ic` turns on no advanced
  item, `--advanced` turns on no IC block, and asking for one never implies the
  other.

## The page is this view, not a second status

The live project view (`PROJECT-VIEW.html`, written by `bm-view render`) does
not introduce a second status. Its header and its next step are the same eight
fields above, from the same collector, so a field that reads one way in the
chat cannot read another way on the page. What the page ADDS is what a page is
good at and a terminal is not: the drawings, the history of what was learned,
the timeline of catch-ups, the standing offer to take the work back, and the
short note in every section nothing has filled in yet. What it never does is
answer the eight questions differently. If the page and this view ever disagree
on a shared field, the page is the defect. The rules of this page (plain
wording, ranges with confidence, exactly one recommended next action, advanced
detail only on request) bind the page exactly as they bind the terminal, and
`references/visual-surface.md` is the register for the parts that exist only
there.

## The brevity budget: the page is capped, and nothing is deleted

A progress page is refreshed by one session after another, and each one wants
to explain itself. Left alone that habit appends and never removes, until the
top of the page is a wall. It is not a theory: on 2026-08-08 the live page for
this project carried an 8,083 character stamp line and a 9,217 character first
card, so a reader met roughly 22,000 characters before reaching the timeline.
The same page, rewritten to the budget below, opens in about 900.

So the top of any progress page is capped, by rule. The numbers are the rule,
not a suggestion:

- The summary line under the title: at most 300 characters. It says who
  refreshed the page, when, and the tick rule. Nothing else.
- Each card: at most 250 characters. One or two sentences. If it needs more
  than that, it is not a card.
- Each evidence line: at most 240 characters, ending at a sentence or a clause
  boundary, then an ellipsis and a pointer to the full text.
- Everything longer is MOVED, never deleted, into a collapsed section at the
  foot of the page, headed so the reader can see nothing was thrown away
  ("Full history, nothing removed").

The second half of that last rule carries as much weight as the numbers. This
project does not destroy a working file, a piece of evidence or a deliverable
in order to tidy up, and a brevity rule that deleted history would be a worse
defect than the wall of text it was written to fix. Shortening happens by
moving. The long version stays one click away, unedited, and an entry already
in that section is never rewritten: a newer note goes ahead of it.

The tick contract is unchanged and is the reason the page exists at all: a box
ticks ONLY when its done-check ran after the last edit and that command's
output is quoted in the evidence line beside it. Percentages are counts of
records, never impressions. Brevity never buys a tick: an evidence line that
has been cut to 240 characters still has to name the command and its result,
and a line with nothing left to point at is a box that should not be ticked.

`project-template/PROGRESS.html` is the canonical page, carrying this budget
as a comment at the top of the file, and every project copied from that
template starts inside the rule.

### What a machine can enforce here, and what is discipline

This project does not call a rule automatic when it is a habit, so the split
is stated rather than implied. Nothing in the toolchain enforces any row below
today: the budget currently lives in the template comment, this page, and the
reviewer's attention.

| Part of the budget | Mechanical, once built | Discipline |
| --- | --- | --- |
| The 300, 250 and 240 character caps | Yes. A counter over the rendered page can measure each region and fail. | No |
| A page over budget has a collapsed history section at its foot | Yes. Structural, and checkable by parsing the page. | No |
| An evidence line that was cut ends with an ellipsis and a pointer | Yes, as a pattern check on the cut line. | No |
| The moved text is the SAME text, with nothing edited out | Only weakly: a check can compare against the previous render, not against what was meant. | Mostly discipline |
| The short summary is a fair summary of what it replaced | No | Yes |
| The page is refreshed at all, at every closed loop | No. Nothing fires when a session decides it is finished. | Yes |
| The done-check behind a tick ran AFTER the last edit | No. Nothing watches edits. | Yes |

The last two rows are the same honest limit the tick contract already states
on the page itself, repeated here so the budget is not read as a stronger
guarantee than the contract it sits inside.
