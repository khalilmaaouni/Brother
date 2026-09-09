---
name: verify
description: Use when work is about to be called done, a figure that could reach a decision has been produced, a schema migration is part of the change, the change touches money or a partner path, or a verification plan is being written. Runs the hard gates and reports PASS, FAIL or NO-DATA with the evidence each verdict actually read. Invoke as /brothersbe:verify.
---

Plugin root: a Claude Code install exports `${CLAUDE_PLUGIN_ROOT}` and a Codex install exports `${BROTHER_PLUGIN_ROOT}`; both name this plugin's own directory, so read whichever variable appears below as the one your client set. On a clone install neither is set: run the same commands from the checkout root instead.

# Verify

An agent earns trust in exact proportion to how mechanically its output can be checked.
This skill is that rule pointed at the finished work.

Read `${CLAUDE_PLUGIN_ROOT}/SKILL.md`, then
`${CLAUDE_PLUGIN_ROOT}/references/phase-verification.md` and
`${CLAUDE_PLUGIN_ROOT}/references/laws-hard-gates.md` (L7 to L10).

## Run verify once, through the command that mints its own evidence

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" verify <dir>
```

This single command already runs the design completeness check (`sbe_design.py --strict`),
the four hard gates together (`sbe_gate.py`: numbers, migration, approval, ran), and the
scored surface (`sbe_score.py --strict`), in that order, and prints every verdict line each
one produces. These four gates plus the silent-failure lints are refused rather than waived:
an operator instruction in session can override a default, never a hard gate.

It then mints one evidence receipt per delegate (design, gate, score) into `.sbe/evidence`,
the same store `sbe status` reads (CR-08, `design/lifecycle-blockers/03-adr.md`), so a clean
run leaves proof behind instead of a PASS `sbe status` cannot see. A receipt minted against a
dirty tree still reads NO-DATA, naming the dirty state: that is correct, not a bug, the first
time it is surprising.

## When the ask names a file, a migration, or a number

`verify <dir>` alone answers about the single latest commit, or a dossier's own scaffolding.
Neither answers a question phrased around a SPECIFIC file: "does migration 0007's backfill
have a test" was asked twice in the 2026-09-07 persona dogfood, and both times the run
resolved its subject from HEAD and came back with a wall of NO-DATA about a dossier the
asker had never heard of, never mentioning the migration by name (R-6).

When the ask names a file, a migration, or a number, pass it with `--path`:

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" verify <dir> --path <the file or glob the ask named>
```

Repeat `--path` for more than one file. Each match against the TRACKED tree gets one plain
line: `<path>: covered by <check-id>[, <check-id>...]` when a check registered in
`.sbe/checks.yml` covers it, or `<path>: no check covers this file` when none does. A pattern
matching no tracked file reports NO-DATA naming the pattern, rather than being dropped
silently. `--since REF` answers the sibling question, "what changed since REF", naming the
change as `REF..HEAD` instead of the single latest commit.

**On NO-DATA for a named path, dispatch the matching reviewer rather than reporting NO-DATA
alone.** "No check covers this file" is not the end of the answer the asker wanted; it is the
reason to hand the file to whichever read-only reviewer actually covers its kind: a migration
file to `migration-reviewer`, a reported figure or SQL transformation to `data-reviewer`, and
anything else that needs a coverage judgment to `qa-reviewer`. Report that reviewer's finding
alongside the NO-DATA line, in the asker's own words, never as a second NO-DATA.

For the stricter soft-finding surface `bin/sbe verify` does not itself request, also run:

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" score --strict --strict-soft <dir>
```

## What good evidence covers, before verify runs

A receipt only settles an argument if it was aimed at the right coverage.
Happy paths get written; these do not, and they are where production breaks:
the negative path (the input that is wrong, missing, malformed, or hostile),
partial failure (the call that dies halfway, and whether it is recoverable
without a human editing data), duplicates and retries (the same request or
message twice, nothing doubles), recovery (does the run after the failure
heal on its own), and boundaries (empty, one, many, and the size where it
stops being fast). Two honesty rules make any of it worth anything: a test
that cannot fail proves nothing, so force the condition, watch it go red,
then fix it and watch it go green; and absence of a result is not a pass, so
a check that opened no file reads NO-DATA, never PASS.

## How to read a verdict

- **PASS** means required evidence was inspected and met the control.
- **FAIL** means required evidence was inspected and violated it.
- **NO-DATA** means the control examined nothing, and it names why. NO-DATA is never a pass,
  and a run that opened no file reports NO-DATA rather than "clean". An all-NO-DATA run
  exits 2 and is not a pass: when every hard gate reads NO-DATA and none reads PASS or FAIL,
  `bin/sbe verify` refuses to exit 0, prints a named summary line, and exits the same code it
  uses for a usage error, because a population of all NO-DATA composed into a green run tells
  a caller nothing about the code either.

Report the verdict with the command that produced it and the evidence it names. Never
summarize a gate you did not run, and never re-word a NO-DATA into a pass because the work
looks right.

## What this run did not check

`bin/sbe verify` prints one more line after the closing caveat, on every run, PASS or FAIL:
acceptance here is anchored on numbered acceptance criteria, and those cover core functions
only. Regression, cross-device behaviour, performance, user experience and translated text are
not covered by any control above, and this line says so instead of leaving a green run to be
read as having proved more than it did. It is information, never a gate: it cannot fail, warn,
or change the exit code.

## After running, read what remains from the engine, not from memory

```
"${CLAUDE_PLUGIN_ROOT}/bin/sbe" status --json
```

Do not re-derive what is left from the verdict lines above by eye. Read `missingEvidence`: a
clean run just minted the design, gate and score receipts, so an obligation still listed there
names something this run did not clear (most often a dirty tree at mint time, or a tier that
owes a check kind no delegate above covers). Read `nextAction` for the single recommended move
afterward, and `notes` for the per-section line behind it.

## The honest limit on all of it, today

These gates check the internal shape of an evidence file. They do not yet check where that
file came from. A receipt can be written by the same agent whose work it is meant to verify,
and a fabricated duration, exit code, row count or rerun result still satisfies the schema.
Commit-bound, wrapper-generated evidence is the next thing being built. Until it lands, treat
a local PASS as advisory and read `${CLAUDE_PLUGIN_ROOT}/docs/KNOWN-LIMITS.md`.
