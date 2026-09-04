# A real run and the receipt it had to correct

The dangerous result is not a visible failure. It is a green check that never depended on the work.

## The request

A throwaway repository began with an `add()` function and one passing test. The request was:

```text
/brother make add() refuse non-numeric input with a clear error and cover it with a test
```

The run separated that outcome into a guard and a test. It worked in isolated checkouts, integrated two units, and printed a receipt.

## The first receipt

These are the important report lines. Machine paths are omitted because they do not help another reader rerun the check.

```text
files changed (2): mathlib.py, test_mathlib.py
integrated (2): guard, test
refused (0)

guard delivered: the named check was run and exited 0. Check written by the planning model, harness 015760192728. verdict: PASS
test delivered: the named check was run and exited 0. Check written by the planning model, harness 015760192728. verdict: PASS

exit 0 means no check failed. It does not mean everything is proven: only the checks named above ran, and a check nobody wrote cannot fail.

cost:
  tokens_in: NO-DATA: no worker in this run recorded tokens_in
  tokens_out: NO-DATA: no worker in this run recorded tokens_out
  tokens_cached: NO-DATA: no worker in this run recorded tokens_cached
```

The receipt gives a reviewer concrete handles: files, checks, authorship, engine revision, verdicts, and missing cost data.

## The audit challenged the green

The guard check failed before the work and passed after it. That supports the guard claim.

The test check did not. It already passed without the guard, and it still passed when the guard was removed. A passing command existed, but it did not discriminate between the old and new behavior. That unit should not have been called proven.

The current receipt rule reports this shape as NO-DATA. A piece that changes no file, a check that passed before the work, or a test that still passes after the related code is reverted cannot become a delivery PASS.

Run the rule directly:

```bash
python3 scripts/test_receipt_door.py
```

Run the full report checks:

```bash
python3 scripts/test_brother_run.py
```

## The benefit

The receipt can be disagreed with. That is a feature. A reviewer can rerun the named command, inspect what changed, check who wrote the proof, and reject a green result whose evidence does not depend on the work.

## The limits

- The checks in this run were written by the same model that produced the work.
- This was a local run, not an independent security review.
- The loaded-machine run took about 1,517 seconds; a quiet-machine run took about 499 seconds. Each worker was bounded to 900 seconds and three attempts.
- The example has no recorded human acceptance.
- The receipt establishes only the named checks. It does not establish all input types, production behavior, or security.

The lesson is narrow: a receipt makes a result inspectable, but a person still judges whether the proof is the right proof.
