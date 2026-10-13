# iOS simulator tooling breaks, and screenshots can leave the machine

**Source:** [GitHub issue #79991](https://github.com/anthropics/claude-code/issues/79991), opened 2026-10-12: closed, created 2026-07-22, 0 reactions, 3 comments. The issue reports simulator tooling failing after framework locations changed. The [iOS simulator documentation](https://code.claude.com/docs/en/desktop-ios-simulator.md), read for the research note on 2026-10-11 and not reopened, says screenshots are sent under conversation retention. Do not use real accounts on simulated devices.

**Example:** An Xcode update moves a framework and breaks a simulator run, while a captured screen contains account information.

**Type:**

```text
/brother run the native iOS checks on a simulator and leave a receipt
```

```bash
python3 scripts/mobile_workflow.py run --repo /path/to/app --profile /path/to/profile.json --reference /path/to/evidence/reference.json --out /path/to/evidence/fresh-run
```

**What Brother does:** `scripts/mobile_workflow.py` runs the selected native test profile on a simulator, captures a screenshot, and writes a receipt with commands, exit codes, runtime details, and screenshot hash evidence.

**Evidence left:** A run directory holds the receipt, logs, captured image, and input hashes. Human acceptance remains `NO-DATA` until a person reviews the result.

**Limit:** Evidence integrity does not establish application quality or human acceptance. The workflow does not install on a physical phone.

**What it does NOT do:** It does not establish visual acceptance, physical device behavior, or safety of data shown in screenshots. Use simulated devices without real accounts.

Verify it yourself: check the run receipt for the simulator runtime and screenshot hash, then inspect the captured image.
