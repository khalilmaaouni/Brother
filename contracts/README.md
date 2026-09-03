# Shared contracts

The change passport and the handoff package are the two contracts
`docs/CHARTER.md` names as the seam between the products (see its "Glossary"
and "The chain", stage 4). This directory is the root each one is read from,
per that charter.

## What lives here, and what each file mirrors

- `change-passport.v1.json` mirrors `products/brothermode/schema/change-passport.v1.json`.
  BrotherMode is the producer of the change passport; this is its own
  authored schema, copied here byte for byte. `scripts/test_contracts_root.py`
  asserts the two stay identical.
- `handoff-package.v1.json` mirrors `products/brothersbe/contracts/handoff-package.v1.json`.
  BrotherSBE is the producer of the handoff package; same rule, same test.

## The rule

The copy in this directory and the product file it mirrors must always
match, byte for byte. `scripts/test_contracts_root.py` is the check; a
change to either side without the other is a defect the test catches.

## The Python-module side, not a byte copy

Neither product's schema file is loaded at runtime. `bm_passport_validator.py`
(BrotherMode) and `sbe_passport.py` (BrotherSBE) each say why in their own
docstrings: a standalone reader with no `jsonschema` dependency needs the
rules re-implemented by hand, so both hand-roll the change passport's field
list in Python rather than parsing the schema document. That is a second,
code-shaped copy of the same contract, and `scripts/test_contracts_root.py`
checks it the way the task that built this directory anticipated: not
byte-identity (it is code, not data), but that the field set both modules
check (`whatWasDone`, `whoDidIt`, `whatWasRun`, `whatWasNotEstablished`,
`whereItCameFrom`) and the `"change-passport/v1"` schema marker both modules
require match what `change-passport.v1.json` in this directory names.
