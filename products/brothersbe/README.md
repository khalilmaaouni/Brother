# BrotherSBE

**A passing check can still leave the risky part untouched. BrotherSBE makes missing evidence visible before a person accepts the result.**

## Install through Brother

The root [Brother install](../../README.md#install) is the public route:

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

Success looks like this:

```text
Successfully added marketplace
Successfully installed plugin
```

The public source for this install is the [Brother repository](https://github.com/khalilmaaouni/Brother).

Then describe the risky outcome through `/brother`. The door routes the request without asking you to know a product name.

## The pain it answers

Backend and data work can look green while the important evidence is absent. A forward migration may run while its reverse never does. A headline number may have one derivation. A test may pass before the related code exists. A receipt may say nothing about who wrote the check or what was never examined.

BrotherSBE keeps three answers separate:

- PASS means the named evidence supports the claim.
- FAIL means the named evidence contradicts the claim.
- NO-DATA means the evidence did not establish either answer.

NO-DATA is not a pass and not an automatic block. It tells a reviewer exactly where human attention is still needed.

## The benefit

For one risky change, BrotherSBE gives the engineer and reviewer one record of intent, risk, checks, results, and human approval. It can examine migrations, numbers, executed checks, required approvals, and stated behavior. Each verdict names what it opened and what it did not.

The result is a receipt a reviewer can challenge. A passing line has a scope. A missing receipt stays visible. Human approval and merge remain outside the tool.

## Prove it from this tree

Run the product tests from this directory:

```bash
python3 tools/test_sbe.py
python3 evals/run_evals.py
```

The first checks the working tools. The second drives the verdict rules through their recorded cases and reports any regression.

Verify the shipped files against their checksum manifest:

```bash
sh scripts/checksums.sh CHECKSUMS.sha256
bash scripts/verify-install.sh
```

The first command refreshes the manifest for the current tree. The second refuses a missing, extra, or changed shipped file.

## A reviewer path

1. Ask `/brother review this change before I accept it`.
2. Read every PASS, FAIL, and NO-DATA line.
3. Open the evidence named by the verdict.
4. Rerun the check that matters most.
5. Decide whether the evidence covers the real risk.

For a migration, do not treat the existence of a reverse file as proof that it ran. For a number, do not treat the same calculation written twice as an independent derivation. For a security-sensitive change, do not let a clean summary replace specialist review.

## Limits

- BrotherSBE checks the evidence it is given. It does not guarantee that the right check was chosen.
- NO-DATA does not fail a run by itself. A person must decide whether that unknown matters for this change.
- The tool does not approve, merge, release, or deploy.
- It does not replace engineering, security, data, or quality review.
- Platform-specific behavior needs evidence from the platform where it will run.
- The current public tag is unsigned.

The honest handoff is simple: here is what passed, here is what failed, here is what remains unknown, and here are the commands that produced those answers.

## License

MIT. See [LICENSE](LICENSE).
