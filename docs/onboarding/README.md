<!-- doc-assurance: allow-missing mathlib.py Created by the reader in the disposable tutorial repository. -->
<!-- doc-assurance: allow-missing test_mathlib.py Created by the reader in the disposable tutorial repository. -->

# Start here

This is the shortest useful Brother path. Follow it in order in a disposable
repository. The goal is one small change that another person can inspect.

## 1. Install and check the basics

Use the supported plugin installer for your coding host. Keep the Brother
checkout at a stable path, and confirm the host, Git, and Python 3.9 or later
are available:

```bash
git --version
python3 --version
```

Start a fresh host session after installation. Open a terminal and make a
disposable project:

```bash
mkdir brother-first-change
cd brother-first-change
git init
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install pytest
```

Create `mathlib.py`:

```python
def add(a, b):
    return a + b
```

Create `test_mathlib.py`:

```python
from mathlib import add


def test_add_ints():
    assert add(1, 2) == 3


def test_add_floats():
    assert add(1.5, 2) == 3.5
```

Run the clean baseline and record it in Git:

```bash
python3 -m pytest -q
git add mathlib.py test_mathlib.py
git commit -m "Add first-change baseline"
git status --short
```

Expect `2 passed` and no status output. If Git asks for an identity, configure
your own author identity.

## 2. Ask for one bounded change

In the fresh host session, ask the installed Brother skill:

```text
Use Brother. Make add() reject strings and None with a clear TypeError,
preserve integer and float addition, and test all of those examples. Change
only mathlib.py and test_mathlib.py. Show the scope and deciding check before
execution. Do not deploy or change dependencies.
```

The new rejection test should fail against the baseline. That red result links
the later green result to the change. Keep the declared file scope narrow.

## 3. Read the receipt

Wait for the run to finish, then find the line that starts with:

```text
brother_run: receipt:
```

Open the path printed on that line. The receipt is a JSON file at the run's
`receipt/receipt.json` path. For every changed file, check the exact command,
exit code, and evidence it records. A process exit of zero is not enough. A
`NO-DATA` entry means the claim still needs evidence or a human decision.

Rerun the relevant checks in the project:

```bash
python3 -m pytest -q
git diff HEAD -- mathlib.py test_mathlib.py
git log -3 --oneline
```

Accept the change only if the receipt and the rerun support the behavior you
asked for. The receipt does not approve a release or deployment.

## 4. Choose what comes next

For another small task, repeat the same pattern: state the outcome, bound the
files, require a discriminating check, and read the receipt. For an existing
repository, first read [Add Brother to an existing repository](../how-to/add-brother-to-an-existing-repo.md).
For a teammate, use [Hand off work to a teammate](../how-to/hand-off-to-a-teammate.md).

## More detail

Continue with the [first verified change tutorial](../tutorials/first-verified-change.md),
[install matrix](../reference/install-matrix.md), [review a receipt](../how-to/review-a-receipt.md),
or [recover from failure](../how-to/recover-from-failure.md).
