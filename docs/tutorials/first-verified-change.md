# Tutorial: your first verified change

<!-- doc-assurance: allow-missing mathlib.py Created by the reader in the disposable tutorial repository. -->
<!-- doc-assurance: allow-missing test_mathlib.py Created by the reader in the disposable tutorial repository. -->

Learn the smallest Brother workflow worth using: a change whose check is sensitive to the behavior and whose evidence can be reviewed from a receipt.

## Prerequisites

Install Brother for [Claude Code](../how-to/install-claude-code.md) or [Codex](../how-to/install-codex.md). You need Git and Python 3.9 or later. Allow about 15 minutes, plus host/model latency. This exercise changes only a disposable repository; it does not need production access.

Create a fresh directory, then initialize it and an isolated test environment:

```bash
mkdir brother-first-change
cd brother-first-change
git init
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install pytest
```

Using your editor, create `.gitignore` containing `.venv/`, `__pycache__/`, and `.pytest_cache/`, each on its own line. Create `mathlib.py` with:

```python
def add(a, b):
    return a + b
```

Create `test_mathlib.py` with:

```python
from mathlib import add


def test_add_ints():
    assert add(1, 2) == 3


def test_add_floats():
    assert add(1.5, 2) == 3.5
```

Run and save the clean baseline:

```bash
python3 -m pytest -q
git add .gitignore mathlib.py test_mathlib.py
git commit -m "Add first-change baseline"
git status --short
```

Expect `2 passed` and no Git status output after the commit. If Git needs identity, configure your own author identity; do not copy another person's. Start the coding host from this activated environment so the worker can find pytest. If it cannot, resolve the interpreter/environment rather than removing the check.

## 1. State the outcome

```text
Use Brother. Make add() reject strings and None with a clear TypeError,
preserve integer and float addition, and test all of those examples.
Change only mathlib.py and test_mathlib.py. Show the scope and deciding
check before execution. Do not deploy or change dependencies.
```

## 2. Inspect the proposed proof before editing

On Claude Code, prefix that request with `/brother`. On Codex, invoke the installed Brother skill with the request. Let the skill supply the execution plan and outcome contract. Keep its run state outside the target repository.

The new-behavior check must fail against the starting repository. If it already passes, it cannot prove the change caused the desired behavior.

For example, `add("a", "b")` currently returns a string, so a test expecting `TypeError` should fail before the implementation changes. Ask for that recorded red-before-green evidence. The existing addition tests must remain green. Behavior for other types, including booleans or custom numeric objects, is outside this exercise unless you explicitly add it.

## 3. Keep work bounded

Implementation/test changes should stay in declared files. An unrelated config rewrite is a scope event, not “helpful cleanup.”

## 4. Read the receipt

For each changed file/unit, read the exact check and whether it actually discriminated the change. A strong receipt can honestly show one unit PASS and another NO-DATA when the second unit's check did not establish the dependency it claims.

The completed engine run prints its receipt path. Open that actual file; do not copy a path from somebody else's transcript. Inspect files, commands, results, authorship, and unresolved findings. See the [recorded README example](../../README.md#your-first-run-start-to-finish), which deliberately includes an unproven test-file claim.

Back in the target repository, rerun:

```bash
python3 -m pytest -q
git diff HEAD -- mathlib.py test_mathlib.py
git log -3 --oneline
```

Expect the valid-addition and new rejection cases to pass. The exact count depends on parametrization. Integration may have committed the changes, so an empty working diff does not imply no change: inspect the relevant commit with `git show` as well.

## 5. Make the human decision

Decide whether the named evidence is sufficient for this small change. Add stronger/independent evidence only if the risk warrants it.

## What is proven

Only the behavior discriminated by the recorded checks. This tutorial does not prove every possible input type, runtime version, or downstream caller is safe.

If the run refuses, stops, or returns NO-DATA, use [recovery](../how-to/recover-from-failure.md). Do not weaken the requirement to make the example look successful. Next, save a useful lesson in the [Vault](../how-to/use-the-vault.md) and read [safe delegation](../how-to/delegate-safely.md) before increasing autonomy.
