# The change says "done" without running the tests

**Source:** [GitHub issue #46940](https://github.com/anthropics/claude-code/issues/46940), opened 2026-10-12: closed, created 2026-04-12, 1 reaction, 5 comments. It reports a regression suite with failures described as passing.

**Example:** A change breaks an existing case, but the completion message says the suite passed and the reviewer has to run it again.

**Type:**

```text
/brother fix the regression and show which checks ran after the change
```

```bash
python3 scripts/test_brother_run.py
```

**What Brother does:** `scripts/brother_run.py` records check commands and exit codes in a run receipt. A check that did not run after the work, or does not establish the claim, remains `NO-DATA`.

**Evidence left:** The run receipt connects changed files with their checks and verdicts.

**Limit:** A receipt proves only the claims its evidence supports. A model-authored test is not automatically an independent oracle.

**What it does NOT do:** Brother does not guarantee that a test is well designed or catches the reported regression.

Verify it yourself: inspect the receipt and rerun its listed checks against the changed revision.
