# Your first day with Brother

The first risk is not installation. It is mistaking a successful install for a proven delivery.

## Install

Run the one public install command in a terminal:

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

Success looks like this:

```text
Successfully added marketplace
Successfully installed plugin
```

## Ask for the first answer

Open a repository and type:

```text
/brother
```

With nothing to resume, the answer includes:

```text
no unfinished run found
```

Brother then asks what you are trying to do. Give it a small, reversible task with a check you understand, for example:

```text
/brother make add() refuse non-numeric input with a clear error and cover it with a test
```

## Read the result

The run ends with one line per piece of work. Each line carries PASS, FAIL, or NO-DATA.

| Verdict | Meaning |
| --- | --- |
| PASS | The named evidence supports the claim. |
| FAIL | The named evidence contradicts the claim. |
| NO-DATA | The evidence did not establish either answer. |

A command exiting 0 is not enough by itself. Brother also checks whether the command passed before the work, whether the piece changed a file, and whether the test still passes after the related code is reverted.

Prove this behavior from the repository root:

```bash
python3 scripts/test_receipt_door.py
```

## Check the safety boundary

The work runs in a separate checkout and integrates one piece at a time. Prove that path with:

```bash
python3 scripts/test_worktree_lane.py
python3 scripts/test_integrate.py
```

The acceptance screen is yours to answer. Prove that the tool treats acceptance as a human decision with:

```bash
python3 scripts/test_fable_authority.py
```

## Limits for day one

- The workers can accept edits inside their separate checkouts. They do not edit your checkout directly.
- The check may be written by the same model that writes the code. The receipt names that fact; it does not turn the check into an independent review.
- Hooks run in every supported session on the machine. There is no per-repository opt-out yet.
- Cost fields say NO-DATA when the worker returns no token figures.
- Brother never accepts, merges, releases, or deploys for you.

Next, read [A-REAL-RUN.md](A-REAL-RUN.md) for the receipt and the audit that challenged it.
