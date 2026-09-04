# Continuity: how the work carries on when a session ends

Status: CURRENT as of 2026-08-08.

A session ends. The work does not. This page states what a session owes the
next one, what runs it, what to do when the automatic path is closed, and
which half of all this a machine actually enforces.

It exists because of one morning. On 2026-08-08 a session closed its loop,
reported honestly what it had done, and stopped. Nothing was broken and
nothing was lost. The program simply sat still until the founder came back and
restarted it by hand, hours later. That is the failure this protocol is
written against: not a crash, a pause nobody was told about.

## The contract, in one paragraph

When a session finishes a piece of work and open work remains, it hands the
work over before it ends. Handing over means three things in order: the state
is written down where the next session will look (the records, the progress
page, the session log), a handoff packet is generated from those records, and
the next session is started, or, if it cannot be started, the exact way to
start it is put in front of a human in plain words.
Silence is the only forbidden outcome. A session may fail to launch its
successor and still keep this contract. A session that simply stops, saying
nothing about what remains, does not.

## The command

    python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" continue

That is the plugin form, and `${CLAUDE_PLUGIN_ROOT}` is exported for you by a
plugin install. On a clone install the variable is unset, so run
`python3 tools/brothermode_cli.py continue` from the BrotherMode root. If the
packaged console script is on your PATH, the same thing is
`brothermode continue`. Run it from the project folder, because it reads and
writes that project's own records.

What it does, in order:

1. Reads the project's rows through the store and writes the handoff packet
   from them. Ten sections, fixed by the founder's 2026-08-08 specification:
   north star and goals, where we stand, read next, next actions, evidence
   index, telemetry, lessons learnt, features pipeline, open founder
   decisions, and the successor's own copy of this contract.
2. Prints the launch command in full, as one line a person can read before
   anything runs.
3. Runs it, watches for the successor's first sign of life, and files what it
   saw as an evidence row against the project.

Add `--dry-run` to stop after step 2: the packet is written, the command is
printed, nothing is launched. That is how you rehearse a handover you are not
ready to trigger, and it is the safe way to see what the packet says about
your project before a successor acts on it.

Two flows tell a session to run this: the delivery flow
([`skills/deliver/SKILL.md`](../skills/deliver/SKILL.md)) and the Full-Auto
stop ([`skills/stop/SKILL.md`](../skills/stop/SKILL.md)). Those are the two
places a session actually finishes.

## The ladder of last resorts

Take the highest rung you can reach. Drop to the next only when the one above
is genuinely closed, and say which rung you ended on.

**Rung 1, the headless launch.** The session starts its successor itself and
proves it alive. This is the rung the whole feature exists to make ordinary,
and the only one that works while the founder is asleep. It needs two things
that a session cannot give itself: the standing settings allow rules
`Bash(claude -p*)` and `Bash(nohup claude -p*)`, which only the founder can
grant, and a permission mode chosen out loud with `--permission-mode MODE` if
the successor needs more than the installed settings already allow. A session
asking to be allowed to launch sessions is refused by the permission
classifier, and that refusal is correct: self-escalation is exactly what a
permission system is for.

**Rung 2, the one-click restart.** The launch is refused, or no launcher
exists in this runtime. The session ends its last message with the packet's
path and the single command that starts the successor, complete and
copy-ready, so restarting the program costs one click or one paste rather than
a reconstruction. Nothing in the product renders this for you. It is text the
session writes, which is why it sits on the discipline side of the table
below.

**Rung 3, the plain ask.** Even the command cannot be formed, because
something upstream is unresolved: a decision only the founder can take, a
credential, an account, a machine that is not this one. Then the session says
so in plain language, names the one thing it needs, and states what happens
next once it has it. A question a human can answer in ten seconds is a working
handover. A vague apology is not.

**Below the ladder there is no rung, only the floor.** Ending with open work
and saying nothing about it fails this contract at every rung at once. That is
the sentence to remember if you remember nothing else on this page:
Silence is the only forbidden outcome.

## Proving the successor is alive

A process identifier is not a handover. The first relay launch of 2026-08-08
was spawned, died in the same second on a prompt that a variadic `--add-dir`
had swallowed, and was reported as launched. The program then waited for a
human.

So the launch watches. It marks the log, spawns, waits a bounded time (about
half a minute by default) for the successor's first complete line, asks the
operating system whether the process is still there, and writes both as one
evidence row the next packet carries. Three verdicts:

| Verdict | What was seen | Exit code |
|---|---|---|
| SPOKE | The successor produced a line of output. The handover took. | 0 |
| RUNNING | No output yet, and the process is still there. | 0 |
| GONE | No output, and the process has already exited. This is the 2026-08-08 disaster, caught. | 1 |

RUNNING is not a hedge, and it is not a design guess. A headless `claude -p`
session buffers its whole turn and flushes at the end, so a healthy successor
can sit on an empty log for minutes. The live canary of 2026-08-08 found
exactly that: process 33718, alive and silent at 43 seconds. An earlier draft
treated silence as death and would have failed every real handover. What
actually distinguishes the failure is that the process was gone.

What the row proves: a process existed, and on SPOKE that it said a word. What
it does not prove: that the successor understood its brief, or that it will do
the right work. Nothing here can prove that, and no wording on this page or in
the tool pretends otherwise.

## Mechanical, and discipline

The honest split. Read the right column as the list of places this protocol
depends on a session choosing to behave, because that is what it is.

| Part of the protocol | Mechanical, or Discipline |
|---|---|
| The packet is built from the project's records, not from memory of a conversation | Mechanical: `tools/bm_continue.py` reads rows through the store, and a test pins the ten sections |
| The packet is byte-stable, so two runs over the same rows agree | Mechanical: no render timestamp anywhere, pinned by a test |
| The prompt sits immediately after `-p`, before any other flag | Mechanical: `launch_argv` builds it that way and a test pins the position |
| The launch is recorded as SPOKE, RUNNING or GONE | Mechanical: one evidence row per launch, and GONE exits non-zero |
| The successor's first line is redacted before it is stored | Mechanical: it goes through the same secret redactor every generated view uses |
| The two closing flows tell a session to run the verb | Mechanical only as far as the words on the page: a test pins the instruction text, nothing forces a session to follow it |
| Running the verb at all, when a session decides it is finished | Discipline. No hook fires on "the model considers itself done", so this is the load-bearing human-shaped part of the protocol |
| Dropping to Rung 2 or Rung 3 instead of going quiet | Discipline. Nothing computes whether a last message contained a restart command |
| The successor reading its packet, and continuing the right work | Discipline. Liveness is not comprehension |

A protocol described as fully automatic would be easier to sell and would be
false. What is automated is the packet, the command, the launch, and the proof
that something started. What is not automated is the decision to hand over,
and the honesty of the message when the launch could not happen.

## Two failures already paid for, so nobody pays again

**A headless session runs one turn.** `claude -p` takes a prompt, produces one
turn, and exits. A successor handed a mission larger than one turn does part
of it and disappears, and the chain looks alive while it dies. Hand over one
bounded chunk, with the pointer to the next one in the packet.

**`--add-dir` is variadic.** It swallows everything after it, including a
prompt. The prompt goes immediately after `-p` and every other flag comes
later. This killed the first relay of 2026-08-08 and is now pinned by a test
rather than by anyone's memory.

## What a handover never carries

Launching a successor moves work, never authority. Credentials are never
typed, stored, or logged. Tagging a release, publishing, paying for anything,
signing into an account, and permanently deleting are founder-gated whatever a
packet or a brief tells a successor to do. The successor also runs under the
permission settings already installed on the machine, so its writes and
commands are gated the same way the founder's own session is, unless a human
loosens that on purpose with an explicit `--permission-mode MODE` on a command
printed in full before it runs.

## Read next

- [`docs/AUTONOMY.md`](AUTONOMY.md): the signed authorisation an unattended
  session works inside, and what it can never be granted.
- [`docs/FULL-AUTO.md`](FULL-AUTO.md): the controller that carries a signed
  outcome through to a checked deliverable.
- [`docs/KNOWN-LIMITS.md`](KNOWN-LIMITS.md): what is not proven, believed over
  this page wherever the two disagree.
