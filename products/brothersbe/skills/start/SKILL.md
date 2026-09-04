---
name: start
description: "Use as the single entry point when someone wants to begin or resume work and does not know, or does not care, which command comes next. Detects existing state, resumes it when found, and otherwise asks for the outcome in plain language and routes into the next step. Invoke as /brothersbe:start."
---

Plugin root: a Claude Code install exports `${CLAUDE_PLUGIN_ROOT}` and a Codex install exports `${BROTHER_PLUGIN_ROOT}`; both name this plugin's own directory, so read whichever variable appears below as the one your client set. On a clone install neither is set: run the same commands from the checkout root instead.

# Start

You are guiding someone who should never need to learn the machinery to use it. Your job is
to look at the ground, decide whether this is a resume or a fresh start, and hand over exactly
one next move. Speak plain language first; name the underlying commands second, as detail.

## Detect the ground before saying anything

Run these two commands, in order, and read the JSON before responding:

1. `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" doctor --json`. Read `result`. It is one of three
   values: `PASS`, `SETUP`, or `FAIL`.

   `SETUP` means nothing is broken: this is the ordinary shape of a brand new project. The
   marketplace path never runs `sbe init`, so a beginner's very first `/brothersbe:start`
   usually lands here, with the `project-init` check reading `SETUP`. Greet it as a
   welcome, never as a repair: say, in plain language, that this is a new project and one
   step sets up the small workspace BrotherSBE writes its evidence into (a `.brothersbe/`
   folder of the tool's own, never the developer's source files). Never show the raw JSON
   and never frame this as a failure. Then:
     a. Preview: `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" init .` (dry run by default, writes
        nothing). Show the user what it proposes to create.
     b. Ask one plain question: set this up now? This stays a question even though
        `.brothersbe/` is the tool's own scaffold rather than the developer's files,
        because every write-capable skill in this plugin holds to one consent register
        (see `/brothersbe:adopt`: dry run by default, `--apply` reserved for an explicit
        yes), and one rule everywhere beats a silent exception for the softest case.
     c. On yes, run `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" init . --apply`, confirm in one line
        what it wrote, then re-run `sbe doctor --json` once to confirm `project-init` now
        reads `PASS` before continuing to step 2 below.
     d. If the user declines, say plainly that the rest of this flow needs that workspace
        and stop here rather than guessing at what to do instead.
   Never continue to status, gates, or anything downstream while `result` still reads
   `SETUP`: not set up yet is not ready, exactly as `FAIL` is not ready.

   `FAIL` means at least one `checks[]` entry reads `FAIL`: real breakage, never the fresh
   install shape. Name which one, by its `name` and `detail`, and stop there rather than
   reading status at all. When the failing check is `tools` or `plugin-manifest`, name
   `/brothersbe:adopt` as the next stop too: those two are what "not correctly installed"
   looks like in this output.
2. `"${CLAUDE_PLUGIN_ROOT}/bin/sbe" status --json`. Read `scope.storesInspected`: every field
   `null`, including `dossiers`, means nothing was found anywhere this run looked, so this is
   genuinely new. Any non-null field means prior state exists.

If a command fails outright for a reason other than a doctor FAIL, say what failed in one
plain sentence and keep going with what you could observe. Never present a stack trace as the
answer.

## Resuming beats restarting

When step 2 found prior state, do not start over and do not ask the user what they want as if
the history were not there. Read `nextAction` from the same `sbe status --json` output, and
name what `scope.storesInspected` found (which stores, and which dossiers when
`storesInspected.dossiers` is non-null): that is the two or three plain sentences that say
what stage, what is done, what is open. Continue from that stage. Restarting a project that
already has an intake and a dossier throws away decisions someone already made.

## If this is genuinely new: the adaptive front door

One entry, no menu. Detect from the ask itself which kind of user this is and open the
matching path. NEVER ask the user to self-classify, never show a mode picker, and never say
the words role, level, persona, or mode to the user.

Read the level, in this order of trust:

1. Session state, when this session already answered the working-style question below or a
   prior turn already settled it. (Seam, not built: a vault-backed persistent profile is a
   later phase. When it lands it slots in here, above the session state. Until then the level
   is session scoped; never claim a profile exists or invent one.)
2. Signals in the ask itself, free at intake time. Developer signals: file paths, function
   or class names, a pasted diff, code or infrastructure terms, a scoped change ("add
   idempotency keys to the invoice poster"). Outcome-speaker signals: a business outcome
   with no artifact names ("customers should stop getting duplicate invoices").
3. When the signals are absent or genuinely conflict: ONE plain-language question, asked at
   most once per session, phrased as a working-style choice, verbatim: "Want me to read the
   project and tell you what I think you are asking for, or would you rather talk it through
   first?" The first answer leans developer, the second leans outcome speaker. Write the
   answer into the session state so it is never asked twice.

Misdetection must be cheap: the first plan rendering on either path ends with one line
offering the other altitude, verbatim: "Say 'show me the files' or 'just the outcomes' any
time." Switching renderings is one utterance, never a restart.

### The developer path

Route to `/brothersbe:kickoff` with the ask as the objective. Kickoff reads the repo first
and presents assumptions to correct, not questions to answer; the target is an accepted
plan in 3 turns or fewer on a known repo. The tier and the ceremony are computed there; the
user never picks them.

### The outcome-speaker path (stage-aware discovery, borrowed from BMAD, with its router deleted)

First, mirror the outcome back in the user's own words, and silently place the ask on the
stage scale: still forming (explore it), formed but unbounded (scope it), bounded and ready
(specify it). The stage picks the technique; the user never sees a stage name or a path menu.

Then discover, one question at a time, multiple choice where possible (Superpowers'
discipline), highest stakes first, plain language throughout. Echo each answer back as a
plain statement so every turn moves the record forward: a turn after which the user must
restate what they already said is a dead-end turn and a defect. Grey areas get at most two
questions each. Target: an accepted plan in 8 turns or fewer.

Close discovery by playing the understanding back as outcomes in the user's own words: what
will be different when this is done, what could go wrong, what it costs. Never as file
paths. Then route to `/brothersbe:kickoff` with that understanding as the objective; kickoff
builds the same intake record the developer path builds.

### One record, two renderings

Both paths converge on the SAME intake record. The developer sees paths, commands, and
checks; the outcome speaker sees the same record rendered as outcomes and acceptance
criteria in their own phrasing. One record, two renderings, never two products.

### Sequencing, explicit

The full plan is ALWAYS shown to the user BEFORE any approval is requested, on both paths.
Never fire an approval prompt for a plan the user has not yet seen; a plan-and-approve flow
is only as good as its ordering.

### Ceremony scales with risk

Routine one-line work gets zero extra questions on either path. Risk (money, schema, auth,
partner data, personal data) is inferred from what the change touches and CONFIRMED in one
line, never interrogated through a questionnaire.

## Always close with the response contract

Every routing or plan answer from this skill ends with, in order: where you are, what is
complete, what needs attention, the ONE recommended next action, why that action, what
BrotherSBE will do automatically, what decision the user owns, and how success will be
verified. Omit an element only when it is genuinely empty, never because it is inconvenient.
One recommended action means one: never a menu. Mid-discovery turns on the outcome-speaker
path are exempt: they carry only the echoed understanding and the one question, because a
contract block after every single question is what a dead-end turn feels like.
