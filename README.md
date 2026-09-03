# Brother

**When AI says done, Brother gives you proof.**

Brother turns a plain-language outcome into checked work, a rerunnable receipt, useful local memory, and a human acceptance decision.

## Install

Brother runs inside Claude Code, so sign in there first. Keep `python3` and `git` on the machine, plus `pytest` for the toy delivery below, whose second check runs `python3 -m pytest`.

Run this in a terminal from any directory:

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

This is the picture of a successful install:

```text
Successfully added marketplace
Successfully installed plugin
```

Open your repository and type:

```text
/brother
```

With no earlier run to resume, the first answer includes:

```text
no unfinished run found
```

Brother asks for the outcome in ordinary words. There is no command menu.

## What success looks like

Brother separates your result into bounded pieces, runs and checks them away from your checkout, integrates them one at a time, and gives you a receipt. The receipt names changed files, each check, its author, the engine revision, and returned cost data.

Brother starts each piece as a headless model session in permission mode `acceptEdits`: edits are auto-accepted in that piece's own worktree, never in your checkout.

A finished run does not approve itself. You read the intent screen before risky work and the acceptance screen after delivery. This repository stores its acceptance records under `docs/deliveries`. Each says whether a person or an agent under a named delegation typed it. Only a person's recorded acceptance counts.

The Vault carries lessons into later work as local Markdown you can read and edit. A remembered lesson can warn a later run but cannot overrule current evidence or a person.

## One small delivery

In a throwaway repository, start with an `add()` function and one passing test. Then ask:

```text
/brother make add() refuse non-numeric input with a clear error and cover it with a test
```

The real run behind this example produced two units, changed `mathlib.py` and `test_mathlib.py`, and printed a receipt for each check. The important part of the report looked like this:

```text
guard delivered: the check was run and exited 0. Check written by the planning model, harness 015760192728. verdict: PASS
test delivered: the check was run and exited 0. Check written by the planning model, harness 015760192728. verdict: PASS

exit 0 means no check failed. It does not mean everything is proven: only the checks named above ran, and a check nobody wrote cannot fail.

cost:
  tokens_in: NO-DATA: no worker in this run recorded tokens_in
  tokens_out: NO-DATA: no worker in this run recorded tokens_out
  tokens_cached: NO-DATA: no worker in this run recorded tokens_cached
```

That report was not the last word. A later audit removed the guard and found that the test still passed. The guard check proved its unit because it failed before the work and passed after it. The test check did not prove its unit. Brother now reports that shape as NO-DATA instead of letting a green command claim more than it established.

Run the check that holds this rule in place:

```bash
python3 scripts/test_receipt_door.py
```

## The problems Brother answers

### A green check can prove nothing

A command that passed before the work, a unit that changed no file, or a test that still passes after its dependency is reverted does not prove the delivery. Brother reports NO-DATA for those cases. Prove the rule with `python3 scripts/test_receipt_door.py`.

### A receipt can be impossible to trace

Every receipt names who wrote the check and which engine revision ran it. That lets a reviewer separate evidence from the confidence of the worker that produced it. Prove the report shape with `python3 scripts/test_brother_run.py`.

### Work can reach your checkout before you agree

Each piece runs in its own isolated checkout. Integration is serial, and the acceptance screen is for a person to answer. Prove the isolation and integration rules with `python3 scripts/test_worktree_lane.py` and `python3 scripts/test_integrate.py`.

### The same failed approach can return

Before a known file is edited, Brother can recall a recorded lesson from the Vault. A third use of the same failed technique is refused and sends the run to gather new information. Prove those controls with `python3 scripts/test_attempt_ledger.py`, `python3 scripts/test_attempt_hook.py`, and `python3 scripts/test_find_out.py`.

### A release can be hard to verify

Each installed product carries a checksum manifest and a verifier. In each product directory, run `sh scripts/checksums.sh CHECKSUMS.sha256` and then `bash scripts/verify-install.sh`. The verifier checks the bytes you received against the manifest.

### The cost can be missing

The receipt prints input, output, and cached token fields when the worker returns them. When it does not, each field says NO-DATA and why. Prove the cost block with `python3 scripts/test_brother_run.py`.

### A battery can hide its red lines

Expected failures are named one by one with review dates. NO-DATA never counts as a pass. Prove that reading with `python3 scripts/test_battery_verdict.py`.

## Limits you should know first

The install registers hooks in every Claude Code session on the machine. They run at session start and end, when a turn stops, before compaction, before edits and shell commands, and after shell commands. They call `git` and `python3` locally and read or write their own state files under your Claude config directory, inside the open repository, and in the Vault after you set it up. Before compaction, they snapshot the working tree into a private local git ref. They can refuse an edit or shell command that crosses a file fence, refuse a shell command that would start a fifth concurrent session, and hold a session open at stop until writes outside its declared scope are reconciled. They do not use the network or push. There is no per-repository opt-out yet.

- The public toy delivery has no recorded human acceptance. Brother never fills the acceptance answer on your behalf.
- The link between recalling a lesson and avoiding the same mistake has not yet been measured on a comparison run. The recall and refusal mechanisms exist; their real effect remains NO-DATA.
- The current public tag is unsigned. `git tag -v v1.0.0` will not produce a verified signature.
- Installing the bundle from its own release tag is guaranteed only from 1.0.1 onward. The 1.0.0 release did not establish that guarantee.
- A receipt proves only the checks it names. Weak checks remain weak, and missing checks remain invisible.
- Brother does not merge, release, delete, spend money, or accept a result for you.

For maintainers: There is no headcount cap on commands, skills, hooks, or workers. The public surface still changes only by deliberate human choice.

## Choose your path

| You are | Start here |
| --- | --- |
| An engineer who must satisfy a security reviewer | [Engineer start](docs/for-engineers/00-START-HERE.md) |
| A founder who writes and ships the code | [A startup week](docs/for-engineers/STARTUP-WEEK.md) |
| An analyst or non-engineer who defines what success means | [Analyst start](docs/for-analysts/00-START-HERE.md) |
| Anyone deciding what the Vault should remember | [What the Vault is](docs/explanation/VAULT.md) and [how to use it](docs/how-to/USE-THE-VAULT.md) |

## License

MIT. See [LICENSE](LICENSE).
