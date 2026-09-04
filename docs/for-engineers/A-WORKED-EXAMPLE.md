# A small task from request to acceptance

Small tasks become risky when the proof is vague and the worker can quietly change your checkout.

## Ask for one bounded outcome

Start from a throwaway repository containing a simple `add()` function. Ask:

```text
/brother make add() refuse non-numeric input with a clear error and cover it with a test
```

Brother writes an intent screen before the work. It names each unit, the files that unit may touch, and the command that will decide it. For risk that changes the acceptance choice, use the interactive path and answer the screen yourself.

## Let the work stay separate

Each unit runs away from your checkout. Integration happens one unit at a time after its check runs again against the advancing result.

Prove the isolation and serial integration:

```bash
python3 scripts/test_worktree_lane.py
python3 scripts/test_integrate.py
```

## Read the receipt

The receipt tells you what changed, which checks ran, who wrote them, which engine revision ran them, and which cost fields are known. A check that already passed before the work or still passes after its dependency is reverted becomes NO-DATA.

Prove that rule:

```bash
python3 scripts/test_receipt_door.py
```

Then read the acceptance screen. Accept or hold is your answer, not the tool's.

Prove that boundary:

```bash
python3 scripts/test_fable_authority.py
```

## What you gained

You can hand the result to a reviewer without asking them to trust the worker's summary. They can rerun the checks and see where the evidence stops.

## Limits

This example does not prove that every planned check is strong. It does not cover production, security, performance, or every numeric type. The public example has no recorded human acceptance. Use it to learn the receipt, not as a release certificate.

For the audited transcript, read [A-REAL-RUN.md](A-REAL-RUN.md).
