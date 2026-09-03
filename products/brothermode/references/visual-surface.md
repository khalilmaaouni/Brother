# The visual surface: four places to say a thing, seven shapes, four alert levels

LOAD WHEN: rendering anything the user looks at rather than reads as a sentence (the live project view, a drawing, an alert, an insight box, an empty section), or deciding where a given piece of information belongs.

This is the register for the visual vocabulary. It is binding on every user
facing surface, and the suites named at the bottom test it. The design behind it
is `docs/program/absolute-lead/DESIGN-visual-surface.md`, and where this page
and that design differ, the design is the record and this page is the defect.

## The one law above the others

Every word, number, node and bar on any surface is computed from the project's
own records. Nothing here is hand drawn, hand maintained, or written once and
kept in step by somebody remembering to. A picture nobody generates is a picture
that is wrong the first time the records move, which is exactly why `CANVAS.md`
is generated too.

## The four surfaces, and the rule for choosing one

| # | Surface | What it is | Its one job | Always available |
|---|---|---|---|---|
| S1 | the generated file | `PROJECT-VIEW.html` at the top of the user's project folder, one self contained page | the durable answer to "where does this project stand", readable with no plan, no sign in and no network | yes |
| S2 | the published page | the same bytes published to one private page, updated in place | the thing the user keeps open in a browser beside the terminal while work runs | no, and the conditions are in `docs/KNOWN-LIMITS.md` |
| S3 | the terminal text | plain text blocks printed into the chat | to answer the question the user just asked, where he asked it, and carry the one recommended next action | yes |
| S4 | the native windows | the decision window, and the interrupting alert on session stop | to make the user ACT: a decision he must answer, an alert he must not miss | yes |

The choosing rule is first match, in this order, so two runs on the same records
always agree:

1. **The user must act before anything can move.** An open key decision, a
   failed hard check, setup consent not given, a step that is his to take, a
   budget ceiling reached. Goes to S4, plus one line in S3, plus a pinned panel
   in S1. Never only in the page: a page he has not opened is not a channel.
2. **The user asked a question in the chat just now.** Goes to S3, in the shape
   `references/status-view.md` fixes, with one link line to the page when the
   page holds more.
3. **It is state rather than an ask, and it is worth returning to.** Progress,
   the pipeline, the checks, who holds the pen, the insight history, the
   timeline, the standing offer to take over. Goes to S1, and to S2 when
   publishing is available.
4. **It is a picture.** Goes to S1 only, and S1 must actually be produced: the
   terminal's text form is an ECHO of the same facts, never a substitute for
   generating the page. Where a running session has no `PROJECT-VIEW.html`
   pipeline wired for this specific decision (an ad hoc kickoff choice, not a
   standing project view) but does have artifact-publishing capability, that
   publish satisfies S1. Printing only the text form and skipping S1 is the
   failure this line exists to name: it happened live on 2026-08-29 (Brother
   INTAKE-LIVE, scored 1 of 5), see the vault note
   `intake-options-default-to-terminal-text-instead-of-an-artifact`.
5. **Anything else is not shown.** An item that matches no branch has no job.

## Two levels, and one expander

The chat is level one. The page is level two. Inside the page exactly one depth
of disclosure is allowed: an expander may hold detail, and an expander inside an
expander is a defect. The one place raw output may ever appear is the "show me
exactly what happened" expander on a failure, and it is pulled, never pushed.

## Colour is the third carrier, never the first

Every status is recoverable from a word first, a non colour mark second, and
colour third. Drawings carry no colour of their own; all colour lives in one
stylesheet block, so deleting that block and still being able to read every
status is a check rather than a hope. Labels sit on the drawing, never in a
separate key, because matching one colour in two distant places is hard for
readers who see colour differently and pointless for everyone else. Prose that
introduces a drawing never says "the red one": it names the thing and where it
is.

Completed things are quiet and uncoloured. Colour is spent on what still needs
action.

## The seven shapes, and nothing else is ever drawn

One model, two renderings: the same shape prints as plain text in the terminal
and as a drawing in the page. A shape that works in only one of the two does not
enter this list. An eighth shape is refused where it is built, not discouraged
in prose.

| Shape | The question it answers | Form | Caps |
|---|---|---|---|
| pipeline | where are we | 3 to 7 stages in a line, exactly one marked NOW | 7 stages, 7 nodes, 6 edges |
| gates | why is this not shipped yet | one column of ordered checks, each passed, failing or not run | 9 nodes, 8 edges |
| lanes | who holds the pen | 2 to 4 lanes, one per owner, one direction throughout | 4 lanes, 9 nodes, 12 edges |
| fork | what am I being asked | one question, two or three outcomes, the last always the handback | 4 nodes, 3 edges, 3 outcomes |
| counts | how much, against what limit | labelled bars with the raw numbers written on them | 6 rows |
| timeline | how is this progressing, lane by lane | one bar per item inside its own lane, the fill showing how far that item has moved through the recorded lifecycle stages | 4 lanes, 9 nodes, no edges |
| gantt | where does the whole programme stand, phase by phase | one bar per piece of work, grouped into the phase its own record names, drawn against dates where the records carry them and as a lifecycle fill where they do not | 12 lanes, 40 nodes, no edges |

The last two were each paid for once. `timeline` draws how far a piece has moved
through its lifecycle, grouped by who owns it; `gantt` draws how long a piece
took, grouped by the phase it belongs to. They were kept apart rather than fused
because one shape whose bars mean two different things depending on the data is
worse than two that each mean one.

`gantt` is the one shape no question routes to. The closed question-to-shape map
in `tools/bm_visual.py` covers the other six, and the gantt is drawn as a section
of the page rather than in reply to a sentence someone typed.

Rules that bite on all seven:

- At most four drawings on one page. The counts row is not a drawing and does
  not count against that.
- Node labels are a noun phrase plus a status word. "Build, NOW", never
  "wr_8813 in_progress". Record ids, hashes and paths are allowed in the page's
  text and never inside a drawn node.
- Every label carries its status as a WORD, never as a colour alone and never as
  a bare symbol.
- Past a cap the generator aggregates and says so in the caption ("3 of 14
  checks shown, the 3 that are not passing"). It never shrinks the text.
- Every drawing carries a title, a description, and a caption stating the same
  content in prose a non expert can read without decoding the picture. The prose
  is what makes the picture optional.
- Anything else asked for gets no drawing at all. There is no pie, no diagram
  of the code or the data, and no shape with no record behind it. The pie is
  the worked example rather than a figure of speech: `tools/test_bm_visual.py`
  asks for one and expects the refusal.

## The four alert levels

A level is defined by how it is delivered first, then by its name, then by how
it looks.

| Level | Means | Delivery | Where | Cap |
|---|---|---|---|---|
| NEEDS YOU | work is stopped until a person acts; names one action and one actor | interrupts | S4, S3, S1 | at most ONE on screen, a second queues |
| AT RISK | nothing is stopped, but something is likely to stop or to be wrong; names what would settle it | polite, in the next catch-up and in the page | S3, S1 | at most 3 shown, the rest counted |
| FOR INFO | true, worth knowing, needs nothing | silent, page only, plus a count line in chat | S1 | 6 listed, then counted |
| SETTLED | something that was open is now closed, with what closed it | silent, and deliberately low contrast | S1 | no cap, collapsed |

Four rules, and they are the whole anti noise policy:

1. Only NEEDS YOU interrupts. Everything below it is available, not thrown.
2. One NEEDS YOU at a time, and at most two levels in any one chat message.
3. No promotion by age. An AT RISK never becomes a NEEDS YOU because it was
   ignored, only because its blocking condition became true.
4. One interrupt per catch-up window per cause. A repeat inside the window is
   suppressed and counted.

There is no fifth level, no TIP and no CAUTION. A tip is documentation and
belongs in the page's help section. Alerts are computed from the records every
time and never stored, because a stored alert is a lie the moment its condition
clears.

The terminal form is ASCII only. No box drawing characters and no emoji: this
project runs a Windows job, and non ASCII in terminal output is a failure it has
already paid for once.

    [!!] NEEDS YOU  ...
    [! ] AT RISK    ...
    [ i] FOR INFO   ...
    [  ] SETTLED    ...

## The insight box: six slots, none optional

One box renders exactly one recorded insight, in this order, and a five slot box
is a defect rather than a shorter box, because the slot people drop is always
the fourth and that is the one that makes the claim falsifiable.

    1  CLAIM        one sentence, the thing now believed
    2  BECAUSE      the evidence, with how it was checked said as a word
    3  INSTEAD OF   the alternative considered, and why not
    4  CHANGES IF   what would flip it, in plain language
    5  CONFIDENCE   high, moderate or low, with its basis
    6  YOUR MOVE    the offer to take it back, always present, always last

Slot 6 is the shared wording from the founder mode module, byte for byte, never
paraphrased.

How a claim was checked is printed by the renderer, never trusted to whoever
wrote the claim. A claim that rests on reasoning rather than on something that
ran carries the words "my reasoning, not verified" and the words "not tested"
beside it, and it never renders with a level bar. Two independent markings, on
purpose.

An insight never carries a level above FOR INFO on its own. If the same fact
needs action, an alert is emitted separately and points at it.

## Empty states: a section with no rows is designed, not blank

Every section that can have no rows has a written empty state with exactly four
fields, and a section that renders blank is a defect.

    title      a positive statement about what will be here
    body       one sentence, at most 140 characters, saying what fills it
    action     one action, in verb plus noun form
    command    the exact command that action runs

Five rules:

1. Exactly one action. Two is a defect.
2. The title never contains "no", "none", "nothing" or "empty".
3. The command names something already offered to this user. In the first
   fifteen minutes that is three commands, so an empty state cannot teach a
   command he has not met.
4. No internal term from the left column of `references/terminology.md`.
5. Loading, error and genuinely empty are three different states with three
   different texts. An error is the failure block below, never an empty state.

## When something fails, no log is printed

The user cannot read logs and should never have to. A failure reaches him as
five parts and nothing else:

    1  one line of context: what was being attempted, in his words
    2  the actual thing, shown as it happened
    3  one hint naming the cause in plain language, not the system's terms
    4  one suggested next action, phrased as something he can say
    5  ONE expander, "show me exactly what happened", holding the raw output

Every refusal the engine can emit has a plain counterpart in one map, and a test
enumerates the refusals and fails on a missing entry. A refusal never reaches
the user in the engine's own words.

## The page states what it is, and does not pretend to be live

The page is a snapshot. It says the newest record it was built from and a short
fingerprint of its own content, and it never prints its own render clock:
two regenerations with nothing recorded in between produce the same bytes, and a
tab open on an older fingerprint is visibly older. Both facts are properties of
the records, which is what lets those two rules hold at once.

## What tests this

`tools/test_bm_visual.py` holds the shapes, the caps, the levels, the six slots,
the colour rules and the plain text forms. `tools/test_bm_view.py` holds the
page: every section renders, every empty state is designed, exactly one
recommended next action, one depth of disclosure, one file with nothing fetched
from outside it, and every claim on the page traced back to the record it came
from. `tools/test_bm_docs.py` holds this page's own copy rules, and holds the
shape table to the shapes the code actually allows: the count in the title and
the heading, one table row per shape, every cap the code enforces stated in its
row, and nothing named as refused that the code in fact draws.
