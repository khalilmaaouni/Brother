# Say it, get a simulator run

**Source:** [GitHub issue #79991](https://github.com/anthropics/claude-code/issues/79991), opened 2026-10-12: closed, created 2026-07-22, 0 reactions, 3 comments. The issue reports simulator tooling failures after framework locations changed.

**Example:** You want a native app change checked on a named simulator, with the tests and captured result tied to the candidate.

**Type:**

```text
/brother check my change on iPhone 16 and leave a receipt
```

```bash
python3 scripts/mobile_workflow.py run --repo /path/to/app --profile /path/to/profile.json --reference /path/to/evidence/reference.json --out /path/to/evidence/fresh-run
```

**What Brother does:** The native mobile route starts with `doctor`, then `profile-init`, `reference-init` at the last good clean commit, and `run`. `scripts/mobile_workflow.py` runs the selected test profile on a simulator, verifies the installed app identity and captures a screenshot. It writes a receipt and keeps output local. The [native mobile guide](../how-to/native-mobile-workflow.md) documents the route and the [mobile skill reference](../../bundle/skills/using-brother/references/native-mobile.md) gives the installed sequence.

**Evidence left:** A run directory contains the receipt, logs and screenshot. The receipt records command results and screenshot hash; a technical PASS does not mean visual or human acceptance.

**Limit:** This uses simulated devices only. A profile needs exact tests or a prior result bundle, and the screenshot stays local unless someone chooses to share it. The issue's report is a snapshot of GitHub as opened 2026-10-12.

**What it does NOT do:** It does not install on a physical phone, establish visual acceptance, or prove behavior on every device.

Verify it yourself: inspect the run receipt for the selected simulator, test results and screenshot hash, then inspect the local capture.
