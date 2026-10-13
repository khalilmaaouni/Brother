# A late release failure makes the whole chain run again

**Source:** [Atlassian: Rerun your failed pipeline steps](https://www.atlassian.com/blog/bitbucket/rerun-failed-pipeline-steps), published 2018-07-31, opened 2026-10-12. Brother's own 1.1.2 release, measured by the maintainer on 2026-10-11: one rehearsal spent 3,226 s in step 2b and 2,599 s in step 2d measuring the same files twice, and `scripts/required_fast.sh` ran seven times locally on the release candidate.

**Example:** A late failure means repeating checks that already passed, then hoping the release chain gets further this time.

**Type:**

```text
/brother cut 1.1.3 from this reviewed candidate, preserving completed phases if a later phase fails
```

```bash
python3 scripts/cut_driver.py run --version 1.1.3 --body <path to the reviewed release notes>
```

```bash
python3 scripts/cut_driver.py resume --version 1.1.3 --body <path>
```

**What Brother does:** `scripts/cut_driver.py` stores state after each phase. After a failure or missing evidence, resume starts at the first phase without PASS or REUSED, so a passed phase is not rerun. The fast cut records its phase results and logs for review. The owner answers the cut question in the owner's terminal; the driver never pushes or opens a pull request.

**Evidence left:** Driver state, per-phase logs and verdict lines. The final check is `python3 scripts/cut_driver.py check --version 1.1.3`.

**Limit:** The recorded 1.1.2 duration is Brother's own measurement, not a promise about another release or machine. Resume is limited to the same candidate and a recent, valid state.

**What it does NOT do:** The driver does not decide to publish, type the owner's answer, push a branch, or open a pull request.

Verify it yourself: inspect the phase lines and run the documented `check` command after all phases finish.
