Status: CURRENT.

# Local verification: the process that replaced GitHub Actions

Founder direction, 2026-08-16 and 2026-08-17: Actions stay off, the work still
goes to GitHub, and verification runs here. This page is the whole process,
end to end, for BrotherMode and BrotherSBE alike.

---

## 1. Why this exists, in one paragraph

An eleven job matrix across macOS and Windows consumed a free month of GitHub
minutes in two weeks, across 886 runs nobody chose to start. macOS bills at ten
times Linux, so 2,000 free minutes is about 200 macOS minutes, roughly ten
pushes. Actions were disabled across the estate. **Verification never needed a
cloud. Only the reporting of it did**, and that is the only part that moved.

## 2. The failure this process was built out of

Turning Actions off left a control pointing at a supplier that no longer
existed. BrotherSBE's `main` required five status checks, all pinned to the
Actions app (`app_id 15368`). With Actions disabled, none could ever report.
`enforce_admins` was true, so nobody could override it. The branch was frozen:
no pull request could merge, no direct push could land, and three PRs sat open
for reasons that had nothing to do with their code.

**The lesson, worth more than the fix: a control outlives the mechanism that
satisfies it.** When you switch off a supplier, walk the list of things that
were consuming it.

## 3. The shape

    tracked tree clean ──> run the real battery ──> observe the result
                                                          │
                                    ┌─────────────────────┴─────────────┐
                                    │ pass                              │ fail
                                    ▼                                   ▼
                        post status success               post status failure
                                    │                                   │
                                    └──────────> git push ──────────────┘
                                                 (protection reads the status)

One script per repository, same name, same contract:

    scripts/local-gates.sh            run and post
    scripts/local-gates.sh --no-post  run and report locally only

## 4. What each repository actually runs

| | BrotherMode | BrotherSBE |
|---|---|---|
| Battery | `python3 tools/test_all.py`, its documented gate | 52 commands, extracted from the gates job of the workflow at run time |
| Duration | 8 to 13 minutes | long: includes install and upgrade-rollback tests |
| Posted context | `local-gates` | `local-gates` |
| Branch protection | none on main | requires `local-gates`, unpinned |

**BrotherSBE's list is never copied.** The runner extracts it from
`.github/workflows/brothersbe-gates.yml` every run, so a fork running Actions
and this machine execute the same battery by construction. A hand-copied list
is correct the day it is written and quietly wrong afterwards.

## 5. The guardrails, split honestly

A script that can post a green status is a machine for lying. These are the
things that stop it, and which of them are real.

**Mechanically enforced.** Success is reachable only from the branch where
every command executed and exited zero. Failure posts `failure` naming the
failing command. A dirty tracked tree refuses before anything runs. HEAD moving
during the run, or the run modifying tracked files, refuses the post. The
status binds to the SHA captured before the run, never a re-read HEAD.
BrotherSBE refuses if fewer than 40 commands are extracted, which is a positive
control against an extractor that silently matches nothing and would otherwise
report a green battery of zero commands. BrotherMode requires both exit 0 AND
the literal `ALL GREEN` line, because that battery refuses to report green when
a suite writes into the checkout, and that refusal is a nonzero exit with every
suite passing. `set -o pipefail` is load-bearing in BrotherSBE: one gate command
ends in `| tee`, and without it a failing gate reports tee's exit status.

**Stated discipline, not enforced.** The statuses API accepts a success from
anyone with push rights, so a hand-typed `gh api` can forge any context. The
script being the only sanctioned poster is a rule, not a control. Descriptions
carry the evidence a forgery would lack: command count, duration, host,
interpreter, and the word local. Platform coverage is whatever this machine is:
a Linux-only breakage ships unseen until someone dispatches the workflow
deliberately, and NO-DATA is the honest label for Linux on every local merge.

## 6. What did NOT change

Branch protection keeps `enforce_admins`, forbids force pushes, and forbids
deletions. Pushing to GitHub is unchanged and stays frequent.

**CORRECTED 2026-08-17, and the correction is the important part.** An earlier
version of this page said the retained workflow files "are `workflow_dispatch`
only, ubuntu only, per the no-self-firing law". That was false, and it was
written without checking. Observed: `.github/workflows/brothersbe-gates.yml`
carries `on: pull_request` and `push`, with eight references to macOS and
Windows runners, and BrotherMode's `tests.yml` carries `on: push` and
`pull_request`. Actions are disabled on both repositories, so none of it can
fire today. **That is a switch, not a fix.** One person re-enabling Actions,
for any reason, resumes the eleven job matrix that consumed a free month of
minutes in two weeks. Rewriting those triggers to `workflow_dispatch` on
`ubuntu-latest` is outstanding work and a founder decision, listed here rather
than quietly left true.

## 7. The process, step by step

1. Commit your work. The runner refuses a dirty tracked tree.
2. `scripts/local-gates.sh --no-post` while iterating. It tells you the truth
   without telling GitHub.
3. When green, `scripts/local-gates.sh`. It posts against the exact SHA.
4. `git push`. Protection reads the posted status.
5. If the gate fails, it posts `failure` and the push stays blocked. **That is
   the system working.** Fix the gate, do not route around it.

## 8. The open honesty

The first real run of this process failed, and that is the most useful thing
about it. BrotherSBE's battery stopped at command 2 of 52 on a genuine defect:
a dossier declared tier T3 carrying none of its eight required artifacts. The
gate was not broken. It was reporting an unpaid debt that a frozen branch had
been hiding. Unfreezing the branch did not make that go away, and it should
not have.
