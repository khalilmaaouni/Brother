# Verdict reference

## PASS

The named evidence supports the named claim. It does not mean the whole feature is correct, production is safe, acceptance occurred, every risk was tested, an independent reviewer agreed, or the release should ship.

## FAIL

The named evidence contradicts the named claim. Preserve the decisive command/output and enough context to reproduce it when possible.

## NO-DATA

The evidence did not establish either answer.

Common causes:

- check already green before work;
- check could not execute;
- evidence file missing;
- check did not exercise the changed dependency;
- safety property could not be established;
- tool returned no cost/token data;
- memory impact has not been measured;
- requested proof is outside installed capability.

`NO-DATA` prevents missing evidence from inheriting the emotional meaning of green.

## Human decisions

Acceptance/release are not synonyms for PASS. A human can reject passing evidence as insufficient or accept a known risk explicitly.

## Authority

Current runtime evidence modules/tests and generated `SYSTEM.md` inventory.
