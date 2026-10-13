# Scheduled runs prompt for permission every time

**Source:** [GitHub issue #47180](https://github.com/anthropics/claude-code/issues/47180), opened 2026-10-12: open, created 2026-04-13, 47 reactions, 38 comments. It reports repeated folder and tool permission prompts for scheduled tasks.

**Example:** A scheduled job waits for an approval that nobody is present to give.

**Type:**

```text
/brother check readiness before starting this scheduled task and explain any refusal
```

```bash
python3 -B scripts/loop/loop_intake.py --selftest
```

**What Brother does:** `scripts/loop/loop_intake.py` checks launch readiness. Its intake refuses before launch when a required capability is unavailable, and names the cause rather than leaving a job waiting for an answer.

**Evidence left:** The intake result records whether the run is ready or why it was refused.

**Limit:** This is a partial fit. Intake can assess the capabilities it knows about; it cannot ensure every host permission is configured or that the host will stay available.

**What it does NOT do:** Brother does not grant permissions or answer prompts on a person's behalf.

Verify it yourself: run the self-test command and review its readiness and refusal cases.
