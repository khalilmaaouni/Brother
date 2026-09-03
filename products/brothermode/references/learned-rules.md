# Learned founder rules: command semantics and receipts

LOAD WHEN: a SUBSTANTIAL task is being planned or delivered and the founder's approved rules must be surfaced, or a bm_learn.py command, exit code, work identity, receipt, disposition, or approval needs interpreting.

(Moved verbatim from SKILL.md's founder-rules section, R2, 2026-08-07. SKILL.md keeps the unconditional law, the definition of substantial, and precedence: the constitution outranks every learned rule, and that law is stated there, not here.)

## The command

```
python3 tools/bm_learn.py apply --query "<what you are about to do>" --session <session-id> (--record <work-uuid> | --new-record <name>)
```

`apply` retrieves the rules AND records that they were surfaced, in one command
with no flag in between. That is the point: a flag is what gets forgotten, and a
forgotten flag leaves no trace that retrieval ever happened. It exits 3 with a
PARTIAL status when the rules came back but the recording did not land, so never
read a nonzero exit as "no rules": ON ANY PATH THAT REACHED RETRIEVAL the rules
are printed above that status, including a `--record` that does not resolve.

The one exit that prints no rules is exit 2, a USAGE refusal, which happens
before retrieval is attempted: the command was called wrongly and is telling you
how to call it. It is not a statement that no rules matched. Re-running is
idempotent, and re-running once you have a work record links the rows you already
wrote.

## Work identity

A WORK IDENTITY IS REQUIRED, and `--session` alone is not one. Pass exactly one
of `--record <existing-work-uuid>`, `--new-record <name>` (which creates a
provisional work record atomically with the application), or an active record
already in the environment. Session plus query text cannot tell two tasks apart:
the task part is derived from your query alone, so two different units of work in
one session phrased the same way would collapse into one history. That is why
this is a refusal rather than a warning, and why the refusal names all three ways
forward instead of just saying no.

`--new-record` is the answer when the work has no record yet. The provisional
record it creates has a durable UUID, is visible in project status, and can be
promoted to a full active record or cancelled later, keeping its linked
applications either way.

## Receipts and reporting

Name the rule IDs you applied in the loop-close report, and state plainly when a
retrieved GATE rule was not followed and why. A gate rule silently ignored is a
compliance failure, and it is the failure this whole mechanism exists to make
visible.

`python3 tools/bm_learn.py lookup --query "..."` is the read-only twin, for human
exploration and for checking whether a task warrants the recorded path. It writes
nothing, so it is NOT a substantial-work path. `relevant` is a deprecated alias
of the old combined command and says so on every run.

Close each recorded application with `disposition` and its outcome, so that "was
the rule followed" stays answerable from rows rather than from memory. `classify`
names a miss as a retrieval miss, a compliance failure, or a bad rule, and
`should-retrieve` answers whether a task shape warranted retrieval at all.

## Approval

Nothing here approves anything. A candidate is promoted into a rule only with
a human-confirmed, one-time receipt-gated answer: by running `bm_learn.py
approve` themselves, or by answering an approval question window, in which
case the orchestrator runs the command and records the founder's exact answer
as the approval reference. The receipt proves an answer was supplied for this
exact proposed rule and has not already been used; it does not
cryptographically prove which human supplied the answer. The decision is
never the orchestrator's; a window the founder did not answer approves
nothing, and automatic capture can never approve or promote its own
candidate.
