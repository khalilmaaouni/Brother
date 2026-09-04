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

The first runs the working tools and exits nonzero on any failure. The second drives the verdict rules through their recorded cases and reports any regression. A check that needs a file this public export deliberately does not carry reports NO-DATA and names that file, which is not a pass.

Check an installed or downloaded copy against the manifest that shipped with it. This is the verifier, and it is the command to run first:

```bash
bash scripts/verify-install.sh
```

It re-hashes the files the manifest names and reports how many matched, mismatched, were missing, were extra, or were not regular files, and it counts entries under excluded paths separately. Read the verdict for exactly what it claims: the files it examined agree with the manifest it was handed. It does not authenticate that manifest, and it says nothing about a path it excluded.

The other command is the manifest WRITER, and only a maintainer preparing a release tree runs it:

```bash
sh scripts/checksums.sh CHECKSUMS.sha256
```

It rewrites CHECKSUMS.sha256 from whatever is on disk at that moment. Running it before verifying replaces the reference you meant to check against, so a modified tree would then verify clean against its own fresh manifest.

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
- The work code path never runs a git merge, rebase, push, or deploy. `TestNoMergeLaw` in `tools/test_sbe_work.py` parses `src/brothersbe/work.py` and fails on any git argument vector whose first word is one of those four, and on any argument head that scan cannot read statically. Approval and release are a design limit, not a proven one: no check here establishes them.
- It does not replace engineering, security, data, or quality review.
- Platform-specific behavior needs evidence from the platform where it will run.
- The current public tag is unsigned.

The honest handoff is simple: here is what passed, here is what failed, here is what remains unknown, and here are the commands that produced those answers.

## License

MIT. See [LICENSE](LICENSE).
