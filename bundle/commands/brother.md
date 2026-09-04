---
name: brother
description: "The one Brother door. Say what you are trying to do, in plain language, and it routes to the right capability without you naming a product. Triggers include brother, what can brother do, start a project, check status, what's next, review this, ship this, verify this change, show the board, when will this land, what slipped, what is blocking us. Invoke as /brother."
argument-hint: "[what you are trying to do]"
---

# Brother

One door. Read `$ARGUMENTS` for what the work IS, never for a product name
the person is not expected to know. `brother-run` below means
`"${CLAUDE_PLUGIN_ROOT}/runtime/brother-run"`, which a plugin install
resolves on its own and runs from ITS OWN directory, so it works pointed at
any repository with no Brother checkout nearby; where that variable is
unset, run `python3 scripts/brother_run.py` with the same arguments.

## Bare `/brother`, or "continue"/"resume"

Follow the single authoritative decision order in
`bundle/skills/using-brother/SKILL.md`. The two steps below are this
command's mechanics for that order's rows 1 and 2, never a second rule.

**Step 1, check for unfinished work before asking anything:**
`brother-run --continue --cwd <this repository>`. None: it prints "no
unfinished run found" at exit 0, so fall through to Step 2. One: it resumes
and prints a sentence naming the OUTCOME; relay that, never the run
directory. Several: relay its numbered outcomes, re-run with `--continue N`.

**Step 2, no unfinished work: ask the one question.** Say exactly this and
nothing else: "Brother turns AI-assisted work into something checkable
instead of just trusted. What are you trying to do right now: start or check
on a project, or get a change proven safe before it ships?"

## BUILD IT: an implementation outcome runs the whole spine

When the ask is a thing to BUILD, add, fix or change in the repository this
session sits in, and Step 1 found nothing to continue, run `brother-run
"<the outcome, verbatim>" --cwd <this repository>` rather than route to a
menu. It decomposes the outcome, claims each unit, runs real workers in
isolated worktrees, audits every write against its declared scope, and
integrates serially, re-verifying each unit's check on the advancing
revision. Exit 0 means all integrated; nonzero names each refused unit and
why; relay the report plainly.

## Routing by intent

**Assurance** when the ask describes a change someone must trust: a schema
or migration, an API contract, money, personal data, a production path, a
diff to review, a number about to be claimed true. **Project** for a
project, outcome, plan or status question. Same six verbs, other column:

| Verb | Project (BrotherMode) | Assurance (BrotherSBE) |
|---|---|---|
| start | `/brothermode:brotherme-start` | `/brothersbe:start` |
| status | `/brothermode:status` | `/brothersbe:status` |
| next | `/brothermode:next` | `/brothersbe:next` |
| review | `/brothermode:review` | `/brothersbe:review` |
| deliver | `/brothermode:brotherme-deliver` | `/brothersbe:verify` |
| help | `/brothermode:help` | `/brothersbe:help` |

Pick the row from the words used, the column from Assurance vs. Project.
Names no verb: `start` if nothing is in flight, `next` if work has begun.

## handover and board

**handover** ("wrap up", "close the session") runs
`scripts/handover_ceremony.py`; **board** ("the gantt", "when will this
land", "what slipped") runs `scripts/gen_readiness_board.py` and
`scripts/track_delivery.py`. Both are this repository's own scripts: fired
from inside it, read `docs/maintainer/BROTHER-MAINTAINER-VERBS.md` and
follow it; from anywhere else, say so plainly rather than routing anywhere.

## What this never does

- Never a menu of skills or commands, at zero arguments or any other.
- Never "unknown command": a Brother-shaped ask maps to a cell above, and a
  genuinely unclear one gets Step 2's single question.
- Never claims an uninstalled capability. Claim verification (BrotherDS) is
  experimental and not shipped by this bundle; say so rather than route.
- Holds no state and makes no PASS, FAIL or NO-DATA verdict of its own.
