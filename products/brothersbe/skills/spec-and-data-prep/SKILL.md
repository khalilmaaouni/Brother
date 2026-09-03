---
name: spec-and-data-prep
description: Use when a vague stakeholder ask needs to become a measurable specification with acceptance criteria, when requirements are being written or challenged before build, or when a dataset is being prepared and handed to a data scientist or analyst and its grain, keys, snapshot, and known limits must be declared. Fires on the work itself, with nothing started and no command learned. Never chooses, tunes, or interprets a model.
---

# Turning a vague ask into something that can be checked

Two jobs share one skill because they share one failure: work starts before
anybody agreed what would count as correct.

## When the ask is vague

Write the specification the requester would have written if they knew the
system. It is finished when every line below has an answer:

- **The outcome**, in the business's own words, and who is worse off today.
- **Acceptance criteria** a test could check. "Faster" is not one. "The daily
  file lands before 06:00 and reconciles to the source within one row" is.
- **The entities and the grain**: one row means what, exactly.
- **The systems of record**: which source wins when two disagree.
- **Out of scope**, written down, because that is where the argument returns.
- **What would make this wrong**: the assumption that, if false, sinks it.

Where a decision needs judgement the tooling does not have, name the decision,
the recommendation, the first and second alternative, and what would flip it,
then give it to the person who owns it. That is a decision record, not a
delay.

## When a dataset is being handed over

The handover is a package, not a file. It carries:

- **The prepared dataset** with its grain and the snapshot it was taken from,
  named so the same query can be run again and give the same rows.
- **Keys and joins**, with the fan-out that each join can cause stated rather
  than discovered in the numbers later.
- **Metric definitions**: for each one, the formula, the denominator, and the
  conditions under which it must refuse to be computed.
- **A labelled holdout** with its provenance, kept out of preparation.
- **A naive baseline** the real work has to beat to count as an improvement.
- **The open questions**, written plainly.

## The line that governs this work

Everything around the model, never the model. Prepare the data, provide the
methods and the infrastructure, validate the results. Choosing a model, tuning
it, and interpreting what a business result means belong to the scientist and
the owner, and this skill never takes them.

## What never happens here

No number reaches a document without the query that produced it and the
snapshot it read. Personal data stays in the environment that owns it. A
metric with no denominator declared is refused, not estimated.

## If they want more

The full card, with real captured output, is
`docs/cards/CARD-technical-ba-data-prep.md`.

## Invoking it on purpose

This skill is meant to arrive on its own, which is the whole point of it.
Invoke as /brothersbe:spec-and-data-prep. That is the deliberate way in, for somebody who wants it; it is not the way most people will meet this.
