# Headless sign-in lapses silently

**Source:** [GitHub issue #50743](https://github.com/anthropics/claude-code/issues/50743), opened 2026-10-12: closed, created 2026-04-19, 0 reactions, 3 comments. Also opened 2026-10-12: [#38813](https://github.com/anthropics/claude-code/issues/38813) (closed, created 2026-03-25, 1 reaction, 3 comments, scheduled jobs silently skipped), [#79685](https://github.com/anthropics/claude-code/issues/79685) (closed, created 2026-07-21, 0 reactions, 1 comment, command line runs stopped until noticed), and [#28827](https://github.com/anthropics/claude-code/issues/28827) (closed, created 2026-02-26, 0 reactions, 4 comments).

**Example:** A nightly job stops because its session is signed out, and nobody notices until the expected result is missing.

**Type:**

```text
/brother check whether this unattended run can start and explain any sign-in problem
```

```bash
python3 -B scripts/loop/model_reachability.py --selftest
```

**What Brother does:** `scripts/loop/loop_intake.py` checks readiness before a run. The model reachability check can identify a signed-out command line session and give a named warning instead of a bare start failure.

**Evidence left:** The intake result reports readiness or the cause that prevents a run from starting.

**Limit:** A readiness check describes the state it observes. It cannot ensure a later session remains signed in or that an unattended host stays available.

**What it does NOT do:** Brother does not renew credentials or guarantee that a scheduled run will complete.

Verify it yourself: run the documented self-test command and inspect the intake result for a named sign-in cause.
