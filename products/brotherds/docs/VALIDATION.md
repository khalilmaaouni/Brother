# What validates this project, and what does not

Status: DECIDED by the session of 2026-08-16 under delegated judgement
("go ahead I trust your judgement"), and REVERSIBLE. The founder can overturn
any line of it. It exists because a session ran BrotherSBE against this
repository, got a green exit code, and had to work out whether that meant
anything. It did not, and nowhere said so.

## The rule this file exists to enforce

A control that cannot reach a verdict reads like a control that passed. That
failure is in the founder's index twice already. So every control that touches
this project is listed here with what it actually examines, and every NO-DATA is
either explained as correct or named as a gap.

## The controls that really check this code

| Control | What it examines | Verdict today |
|---|---|---|
| `bds.py selftest` | every gate, the chain vocabulary, both seam readers, the backlog, the isolation scan | PASS, 61 assertions |
| `sbe score` silent-failure lints | `bds.py` only, for swallowed errors | PASS, and silent about the other 35 files by its own admission |
| `bds.py backlog` | every queue item names a stage of the chain and a check that closes it | PASS |
| the push gates | secrets, attribution, dashes over the full pushed range | run per push, output quoted in the session log |

**The selftest is this project's primary control.** Not `sbe verify`. If the two
ever disagree about whether this repository is sound, the selftest is the one
that opened the files.

## Why `sbe verify` returns NO-DATA here, and why that is correct

`sbe verify` ran on 2026-08-16 at 01:47 JST. Of fifteen checks, ONE opened a
file in this repository. The four hard gates all returned NO-DATA. That is the
right answer, not a hole to paper over:

- **numbers manifest.** The gate asks for the second derivation behind a
  decision figure this change presents. Night two presented none: it added a
  vocabulary, two readers and three documents. When the Verified Claim Rate
  becomes a real number it WILL be a decision figure and it will owe a manifest.
  Today there is nothing to manifest, which is why the north star reads NO-DATA.
- **migration receipt.** There is no schema migration. There is no schema.
- **approval.** Nothing here touches money or a partner path.
- **ran receipt.** There is no SQL or pipeline change in this repository. The SQL
  this product runs lives inside claims, and the gates that check it are
  BrotherDS's own.

The remaining ten checks are fed by the machine's vault and session ledger. They
say true things about how this machine is being worked; they say nothing about
this code, and `sbe verify` prints that distinction itself.

## The decision: no design dossier, and why

BrotherSBE's design check wants a dossier: `00-intake.json` plus seven numbered
artifacts. Scored honestly, this project's intake lands on **T3**, which owes all
seven, because the engine reads a real production warehouse holding commercial
data.

**It is not being built, deliberately.** The seven artifacts would re-express
`SPEC.md`, `docs/NORTH-STAR-CHAIN.md`, `docs/TRIUMVIRATE-INTEGRATION.md`,
`docs/BOUNDARY-STUDY-2026-08-16.md`, `OPTIONS.md` and the founder's scope
directive into a second format. **Two copies of one decision is the drift this
triumvirate exists to prevent**, and it is the same failure gate G10 was built to
catch one level down: two names for one thing, diverging quietly.

What is lost by not building it: the design completeness check stays NO-DATA
forever here, so it never tells this project that an artifact is missing. That is
a real cost and it is accepted knowingly rather than overlooked.

**What would flip this.** If BrotherDS ever hands its design to somebody who
reads dossiers rather than this repository, the dossier becomes the interface and
should be built, once, as the only copy, with these documents folded into it
rather than beside it.

## What is still unvalidated, plainly

- **No claim has been scored against a real outcome.** The north star reports
  NO-DATA and will until claims resolve. Nothing in this file changes that.
- **Authoring cost is unmeasured.** It is one of the three named tests that
  would disprove the product, and it is due at week three.
- The silent-failure lint covers `.py` and skips the 22 markdown and 9 JSON
  files, so nothing mechanical reads the documents for the claims they make.
- Nobody outside this machine has run any of it.
