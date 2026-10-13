# Second opinions you can read

**Source:** [Stack Overflow Developer Survey 2025, AI](https://survey.stackoverflow.co/2025/ai), opened 2026-10-12. The recorded J102 readout below is a maintainer-machine measurement from 2026-10-12, not a survey result.

**Example:** A reviewer wants another view of a failure or a completion note, with a clear record that the view did not decide the result.

**Type:**

```text
/brother review this failure and completion note, show any second opinion separately from the verdict
```

```bash
python3 scripts/jev_status.py
```

```bash
python3 scripts/jev_door.py review --output /path/to/failure.txt
```

```bash
python3 scripts/jev_door.py deliver --note /path/to/done-note.txt --quotes no
```

**What Brother does:** `scripts/jev_door.py` connects review and deliver to bounded second-opinion questions. Review asks whether a failing check looks like an infrastructure flake; deliver asks whether the done claim lacks quoted verification. The deterministic result remains authoritative. `scripts/jev_status.py` reads the ledger and reports its status. Brother's Jev use guide permits only the registered use and describes the abstain band from 0.2 through 0.8.

**Evidence left:** One example readout, measured on the maintainer machine 2026-10-12: `J102 noul [shadow]: decisions 4396, abstains 304, outcomes 295, agreement 295/295 = 1.0000, Wilson lower bound 0.9871 against target 0.98 (high) bound meets target; recorded, not acted on`. It is shadow evidence, not an action.

**Limit:** Shadow calls need a bridge and a key. A missing registry prints one NO-DATA line. An unavailable or uncertain answer remains no opinion; this readout does not establish future agreement.

**What it does NOT do:** A second opinion does not change a check, receipt, exit code or delivery verdict. It does not make an unverified claim verified.

Verify it yourself: read the `scripts/jev_status.py` output and confirm any second-opinion line ends as recorded, not acted on.
