# Engineer cheat sheet

The problem is rarely finding another command. It is knowing which answer deserves trust.

## What to type

| Situation | Command |
| --- | --- |
| Start a new outcome | `/brother describe the result you want` |
| Resume unfinished work | `/brother` |
| Ask where the work stands | `/brother show me the status of this work` |
| Ask for the next action | `/brother what should happen next?` |
| Review a risky result | `/brother review this before I accept it` |
| Ask for delivery evidence | `/brother deliver this with the receipt` |

The same door routes all six requests. You do not need an internal command name.

## What to trust

| Verdict | Read it as |
| --- | --- |
| PASS | The named evidence supports the claim. |
| FAIL | The named evidence contradicts the claim. |
| NO-DATA | The evidence did not arrive or did not prove the claim. |

Prove that NO-DATA cannot be promoted to PASS by a zero exit code:

```bash
python3 scripts/test_receipt_door.py
```

Prove that the battery does not hide expected failures or count NO-DATA as green:

```bash
python3 scripts/test_battery_verdict.py
```

## Limits

The receipt covers only the checks it names. It does not certify security, release readiness, or business acceptance. A person still decides whether the checks were sufficient and whether the result should move forward.

For a full path, read [END-TO-END.md](END-TO-END.md).
