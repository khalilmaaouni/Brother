# Invariants

What this system promises, written as statements a machine can check.

These exist because six external review rounds each found a real defect, and
every example-based test I wrote was authored backwards from a fix: it encoded
the same assumption that produced the gap, so it could only confirm what I
already believed. One of them literally kept a digest unchanged before testing a
retry, which presupposed the absence of the bug that was there.

A numbered promise is different. It can be checked against sequences nobody
imagined, and a review can be pointed at it instead of at a diff.

`tools/test_bm.py` contains a seeded state-machine test that runs random
sequences of operations with random write failures injected, and asserts these
after every single step.

---

## I1. Losslessness

Every handover that was checkpointed and then drained or adopted appears in the
project `STATE.md` at least once.

Losing a handover is the one failure this system exists to prevent. If a
sequence ends with a digest that was recorded but is nowhere in `STATE.md` and
nowhere still active in the registry, the promise is broken.

## I2. Exactly once, per version

A given handover version is delivered at most once. A retry of the same content
must not duplicate it, and content that has CHANGED since the last delivery must
still be delivered.

Both halves matter. Suppressing a duplicate is worthless if it also suppresses
newer work, which is exactly the defect that motivated writing this file.

## I3. Lifecycle isolation

Content produced under one lifecycle of a record id never appears in a delivery
belonging to a later lifecycle of that same id.

Thread names are reusable. A second `payments` thread is a different piece of
work and must not inherit the first one's digest, decisions, evidence or spend.

## I8. The two files agree

For every thread the mode file knows, its state there matches the state of its
record in the registry.

This is the invariant whose absence let a real defect through. A partial `off`
parked one record in the registry, releasing its fence, while the mode file
still showed it active: the dashboard said the thread was running, its files
were claimable by someone else, and the command reported that every record
stayed active. Nothing checked that the halves agreed, so nothing complained.

A lifecycle transition that touches both files is all-or-nothing. If any part
fails, neither file moves.

## I9. Working files are never silently destroyed

Re-running `start` on a live thread does not overwrite its STATE.md, inbox,
outbox or digest.

A persistent thread accumulates a working plan, directives from the chief and
an advancement history. A command whose purpose is to CREATE a thread must not
erase one.

## I4. Never block

Every command exits 0, whatever the state of the disk, the files, or the
registry. A tool that refuses to run is worse than a tool that degrades and
says so.

## I5. Single writer

No two records in the `active` state declare overlapping files.

## I6. Honest reporting

Reported success implies the on-disk state matches what was reported. If a
command says a handover was delivered, it is in `STATE.md`. If it says a record
was closed, the closed state is on disk.

The inverse is the defect this project hit three times: a write whose return
value was discarded, and success printed anyway.

## I7. State survives a failed write

A write that fails leaves the previous file intact and readable, never empty,
truncated, or half-written, and leaves no temp file behind.

---

## How these are checked

- Each invariant has at least one dedicated test.
- The state-machine test asserts I1, I2, I3, I5, I6 and I7 after every step of
  every generated sequence, across many seeds.
- I4 is asserted on every command the state machine runs.

## What this register does NOT claim

It is a list of promises, not a proof. A property holding across the generated
sequences means no counterexample was found in that space, not that none exists.
The generator covers the thread lifecycle and injected write failures; it does
not cover concurrent processes, filesystem corruption, or partial writes at the
byte level.

## Measured power of the tests, stated honestly

A test suite that has not been calibrated is decoration. The first generative
test here created 0, 0 and 1 handovers across three seeds because it picked
operations at random and almost every precondition failed. It passed while
exercising nothing, and reinjecting two known bugs did not make it fire.

Calibration, re-run whenever a test or the generator changes: reintroduce a
known defect and confirm the suite fails. Current measured result:

| Reinjected defect | Caught |
|---|---|
| Delivery identity without a content fingerprint | yes |
| Lifecycle record not reconstructed on reuse | yes |
| No exactly-once check at all | yes |
| `on` reports mode ON without checking the write | yes |
| `start` reports created without checking its files | yes |
| `checkpoint` hides a failed registry mirror | yes |
| `claim` returns success after a failed save | yes |
| A partial `off` parks some records and not others | yes |
| `start` overwrites a live thread's working files | yes |
| `off` parks threads the registry already adopted | yes |

Ten of ten.

## What each style of test is for, learned the hard way

Neither style alone was enough, and the reason is worth keeping.

**The generated walk** finds sequences nobody imagined. It found a real defect
on its own: re-running `start` on a live thread stamped a blank template over a
digest holding checkpointed work. No human wrote that case down first.

**Deterministic tests with surgical injection** are needed where a specific
write must fail while others succeed. Permissions cannot express that: making a
directory read-only fails the FIRST write in a command, so `start` never reaches
its file writes and the defect under test never gets a chance to appear. Three
report-vs-disk defects survived a permission-based test and were only caught by
patching the single function under test.

One defect stopped being observable during this work rather than being caught:
`start` overwriting an existing digest file no longer loses a handover, because
`adopt` now prefers the registry digest. Verified by execution, not assumed. The
guard on the file write stays as a second layer.


## What I8 caught on its first run

I8 was added because a partial `off` had released one fence while the mode file
still showed the thread running, and nothing checked that the two halves agreed.
On its very first run it found a SECOND, unreported disagreement: `off` stamped
"parked" over every mode entry including threads the registry had already
recorded as adopted.

That is the argument for writing invariants down rather than only fixing bugs.
The defect it found was not the one it was written for.
