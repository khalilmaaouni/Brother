# Brother in a founder's week

When you ship every day, the danger is not a missing ceremony. It is losing twenty minutes to setup and still receiving a receipt that cannot answer one hard question.

## Monday: prove the install

Use the [one install command](../../README.md#install), then type `/brother` in a disposable repository. The first answer should include `no unfinished run found`.

Run one small task whose expected behavior you understand. Read [A-WORKED-EXAMPLE.md](A-WORKED-EXAMPLE.md) for the shortest path.

## Tuesday: use it where a mistake costs more

Choose a task involving money, access, customer data, a public interface, or a migration. State the outcome in plain words through `/brother`. Read the intent screen before the workers begin.

The benefit is contained work with a rerunnable receipt. Prove the containment and receipt rules:

```bash
python3 scripts/test_worktree_lane.py
python3 scripts/test_receipt_door.py
```

## Wednesday: make cost visible

Read the cost block in the report. It shows real token fields when returned and NO-DATA with a reason when they are absent.

Prove that behavior:

```bash
python3 scripts/test_brother_run.py
```

## Thursday: hand the receipt to a reviewer

Ask `/brother review this before I accept it`. The reviewer should rerun the named checks and challenge any check that passed before the work or still passes after its dependency is removed.

Acceptance stays with you. Prove that boundary:

```bash
python3 scripts/test_fable_authority.py
```

## Friday: save one useful lesson

Put a durable failure or constraint in the Vault only if it should change later work. Brother can recall it before a related edit.

Prove recall:

```bash
python3 products/brothermode/tools/test_vault_recall_hook.py
```

## Limits

Skip Brother for an obvious edit you can safely make and check in under a minute. Hooks run in every supported session on the machine, with no per-repository opt-out yet. The current tag is unsigned. The effect of recall on repeat mistakes has not yet been measured. Brother prepares evidence; it does not accept or release for you.
