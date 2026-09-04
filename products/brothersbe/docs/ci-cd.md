# CI/CD

BrotherSBE should normally be introduced to CI/CD in stages.

Do not make every check blocking on the first day.

The exact step order the shipped workflow runs, and why gates come first, is documented in [CI-ORDER.md](CI-ORDER.md). The workflow that runs them lives with this project's own CI wiring and is not part of the published tree; it guards nothing until the same steps run in your own repository.

## Stage 1: Advisory

Run BrotherSBE without strict enforcement.

Example:

```bash
sbe impact . --base origin/main
sbe verify design/my-change
```

Use this period to measure:

```text
useful findings
false positives
missing checks
NO-DATA frequency
unmeasured paths
workflow overhead
```

## Stage 0: The reporter (safe to add on day one)

`tools/sbe_report.py` is reporting-only. It never fails the build: on every
change it exits 0, always, whether the change is clean, broken, or the
reporter itself hits an internal error. Add it before Stage 1's advisory
checks, before any gate exists, because it cannot block anything.

It reuses the reviewer one-pager (`tools/sbe_onepager.py`) by import and adds
one SUMMARY line up front that counts `PASS`, `FAIL`, `NO-DATA`, and
`unverified` so the first line of a CI log already says which of those four
buckets the change landed in. The full page beneath it names, per task, what
changed, which command proved it, and what was not checked and why (missing
receipt, empty receipt, corrupt receipt, or an explicit `NO-DATA` verdict).

Paste this step into your workflow. It is reporting-only: `continue-on-error`
is not even needed, because the script's own exit code is always 0.

```yaml
      - name: BrotherSBE report (reporting-only, cannot fail the build)
        run: python3 tools/sbe_report.py . --out sbe-report.txt
      - name: Upload BrotherSBE report
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: brothersbe-report
          path: sbe-report.txt
```

### A page you can forward as your own work

`python3 tools/sbe_report.py . --html sbe-report.html` writes the same packet
(the SUMMARY line plus the A1 one-pager, nothing re-derived) as ONE
self-contained HTML file: every style inline, zero external references, no
network call of any kind. Open it by double-clicking with the network off.
That is the point: an artifact requiring an account or a link cannot be
pasted into somebody's own wiki or sent onward under their name, so it reads
as a request rather than a gift. `--out` and `--html` can be combined in the
same run; the exit code stays 0 either way.

## Stage 2: Register the checks that matter

Your team must define which commands count as proof.

Examples:

```text
unit test suite
API compatibility test
Snowflake reconciliation
migration rehearsal
data-quality query
integration test
```

Register each one in `.sbe/checks.yml`, naming its executable, arguments, working directory and the files it is evidence for. Then run it by id through the evidence wrapper:

```bash
sbe evidence run --check reconcile-orders --out .sbe/evidence/reconcile-orders.json
```

The registry entry defines what runs; nothing on the command line replaces it. A free-form run (`sbe evidence run --out <receipt> -- <command>`) mints advisory evidence only and satisfies no required policy check.

## Stage 3: Review verdict quality

BrotherSBE distinguishes:

```text
PASS
FAIL
NO-DATA
```

CI systems often reduce results to green or red.

Do not lose the `NO-DATA` information in that reduction.

Read the BrotherSBE verdict block. A required CI check compares a name to a conclusion; it has no opinion about whether anything was examined. A job skipped by a condition still reports success to a merge rule.

## Stage 4: Strict selected controls

After a check has proven useful, selected controls can become enforcing.

Example:

```bash
sbe impact . --base origin/main --strict --intake design/my-change/00-intake.json
```

Strict mode should be introduced deliberately, and it moves only by a human editing the CI workflow, visible in the diff. A session instruction never waives a hard gate.

## What BrotherSBE does not provide

BrotherSBE does not replace:

```text
branch protection
repository merge checks
deployment approvals
cloud IAM
production credentials policy
release management
```

BrotherSBE produces verdicts and evidence.

Your engineering platform decides what those verdicts are allowed to block. On GitHub that is branch protection or rulesets; on Bitbucket it is branch permissions and merge checks; the same applies to any host.

One wiring note for the approval gate: it passes only on a signature the CI host verified. A CI agent with no reviewer public keys imported produces an unverifiable signature and the gate reports NO-DATA, not PASS.

## Recommended PR sequence

```text
PR opened
  |
BrotherSBE impact
  |
design checks
  |
registered project tests
  |
evidence receipts
  |
specialist review
  |
BrotherSBE verify/status
  |
branch policy
  |
human merge
```

## Important rule

A CI job being green does not automatically mean:

```text
the intended check executed
```

or:

```text
the requirement was proven
```

BrotherSBE exists to make that distinction visible.

## Running the consumer controls on Bitbucket

This project's own Bitbucket pipeline definition is not a copy of this
repository's test suite. It runs the ten steps documented in
[CI-ORDER.md](CI-ORDER.md), "The consumer CI order", against an adopting
team's own repository and their own change: install verification, dossier
checks, impact reconciliation, plan validation, task-scope validation, hard
gates, evidence verification, convergence, GitHub approval verification, and
final status, each with the command CI-ORDER.md names for it. What is
GitHub-specific there is only the runner configuration, the YAML that tells a
CI service which commands to run and in what order; the underlying commands
are plain Python (and one small `sh` script) and call no GitHub API except
where the documented step itself is GitHub's approval check (step 9 below).

To point a Bitbucket repository at it:

1. Copy `ci/bitbucket-pipelines.yml` to the root of your Bitbucket
   repository as `bitbucket-pipelines.yml` (Bitbucket only reads that file
   from the repository root, not from a subfolder).
2. Set the pipeline variables the file documents in its own header comment:
   your dossier path, your base and head commits, the open task ids and
   evidence receipts your change carries, and, if the pull request lives on
   GitHub, its number and `owner/name`.
3. Commit and push it. Bitbucket Pipelines picks it up automatically once
   Pipelines is turned on for that repository (repository settings,
   Pipelines, enable).
4. Set the branch permissions or merge checks you want the gate result to
   block, the same way you would set branch protection on GitHub. BrotherSBE
   produces the pass or fail result; your host decides what that result is
   allowed to block.
5. Before relying on step 9a below, give the Bitbucket runner the trusted
   signing keys it needs to verify an `Approved-by` signature: either import
   the approvers' GPG public keys into the runner's keyring (`gpg --import`,
   the same step [SETUP.md](SETUP.md) documents for the GitHub workflow), or,
   for SSH-signed commits, set `git config gpg.ssh.allowedSignersFile <path>`
   to a file listing each approver's identity and public key, and make sure
   that file (and the `git config` call setting it) runs as part of the
   pipeline, on the same runner, before step 9a. A stock runner starts with
   neither, and step 9a below explains what that means for it.

Step 9, GitHub approval verification, needs a token with read permission on a
GitHub repository. A Bitbucket-hosted pull request has no GitHub evidence to
read, so on a Bitbucket repository that step reports NO-DATA and exits
nonzero: an approval nobody could verify is not a pass, and the pipeline does
not wire that step to continue on error to hide it.

This file has not been run on a real Bitbucket workspace. It cannot be, from
inside this environment: that needs a Bitbucket workspace and credentials
that are deliberately not available here. Treat it as reviewed and ready to
try, not as proven.

### The signed approval path already works on Bitbucket, but only once the runner trusts a key

The signed `Approved-by` trailer approval path does not depend on GitHub at
all. It is verified by checking a Git commit signature locally, on whatever
machine runs the check, using keys that machine already trusts. No call to
GitHub's servers, or to any other host's servers, is involved. That means it
already works on Bitbucket today, with no change needed: the same command,
run inside `ci/bitbucket-pipelines.yml` or on a laptop, checks the same
signature the same way.

"Using keys that machine already trusts" is a prerequisite, not a detail. A
stock Bitbucket Pipelines runner starts every build in a fresh container with
no keys imported at all. Before step 9a can ever report the approval gate's
verdict as `PASS`, the pipeline (or the image it runs from) must first put the
team's trusted signing keys on that runner, by name, one of:

- **GPG-signed commits**: import each approver's public key into the runner's
  GPG keyring, for example `gpg --import` fed the team's public key material
  (the same step [SETUP.md](SETUP.md), "Signer keys, for the approval gate,"
  documents for the GitHub workflow).
- **SSH-signed commits**: set the git config `gpg.ssh.allowedSignersFile` to
  point at an allowed-signers file listing each approver's email and public
  key, so `git`'s own signature check (`%G?`) has something to verify against.

Skip this and the step still runs, but it can never return `PASS`: with no
imported key, `git` reports the signature as unverifiable, `sbe_gate.py`
reports that as `NO-DATA` ("signature present but this host could not verify
it"), and step 9a's JSON check requires the verdict to be exactly `PASS`, so
it blocks. That block is correct, not a bug: an approval nobody could verify
is not a pass, and a runner with no trusted keys cannot verify anything, no
matter how thoroughly the change was actually reviewed. It also means every
team's approved change is blocked until this is configured, so configure it
before turning step 9a into a required check, not after the first false
block is reported as a defect.

### Two commands stay GitHub only

Two things in the full BrotherSBE picture genuinely need GitHub and have no
Bitbucket equivalent shipped yet:

- The pull request sign-off check, which reads PR review approvals through
  GitHub's own API.
- Branch protection, which is a GitHub repository setting, not a command
  BrotherSBE runs. On Bitbucket the equivalent is branch permissions and
  merge checks, configured directly in Bitbucket, as already noted above in
  "What BrotherSBE does not provide."

Everything else in the gate list runs the same way on both hosts.
