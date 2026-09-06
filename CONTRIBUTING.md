# Contributing to Brother

This page is for a stranger making a first contribution. If something here
is wrong or missing, that is itself a good first issue: say so.

## Clone the public repository

```bash
git clone https://github.com/khalilmaaouni/Brother.git
cd Brother
```

Keep `python3` and `git` on your machine, plus `pytest` (a few suites shell
out to `python3 -m pytest`). No network access is needed to run the checks
below.

## Run the proof suites

The first command to run, from the repository root:

```bash
sh scripts/required_fast.sh
```

This is the same small, fast, mandatory slice every merge into `main` runs
locally before it lands: well under five minutes, and it names any check
that failed or read NO-DATA rather than passed. NO-DATA is never a pass
here; it means a check could not exercise what it claims to, and gets
reported as such, never counted as green.

README.md itself carries dozens of individual proof commands, each right
next to the claim it proves, in the shape `Prove X with python3
scripts/test_Y.py`. Run the one beside the claim you are touching before
you touch it, and again after, so you can show the check failed before
your change and passes after.

For the full suite (about 35 minutes, every shipped check):

```bash
sh scripts/check_all.sh
```

## How a change is proposed

Open a pull request against `main`. The public repository carries a branch
ruleset on `main` (deletion blocked, non fast forward blocked, a pull
request required, bypass never) whose one required status check is
`required-fast`: a workflow that runs `sh scripts/required_fast.sh`
automatically on every pull request into `main` and blocks the merge on a
failure. Running GitHub Actions on every push is against this estate's own
standing rule against self-firing CI (it costs real compute minutes on
every provider that bills for it), and this workflow is the one named
exception the founder made on 2026-09-06, scoped to this one check on this
one public repository, precisely so a pull request cannot land with a
required-fast regression nobody ran locally. Nothing else in this
repository fires automatically.

## The laws every contributor meets

- NO-DATA is never a pass. A check that could not exercise what it claims
  to reports NO-DATA and is never counted as green, here or in your own
  pull request.
- No dash characters (em dash or en dash) anywhere: code comments, commit
  messages, documentation, the pull request body. Use commas, colons, or
  parentheses.
- A done check runs after your last edit, not before it, and its output is
  quoted verbatim in the pull request. A claim of "done" or "fixed" with no
  command and no quoted output is not accepted.

## What a pull request must carry

Beyond the laws above, applied to the pull request itself:

- No `Co-Authored-By` or `Generated with` trailer naming an AI model,
  assistant, or tool, in the commit message or the pull request body.
  Authorship is the person who opened the pull request.
- No client, team, or machine internals: no company or client name beyond
  what is already public in this repository, no teammate's name, no path,
  hostname, or credential that only makes sense on somebody's own machine.
  If you are unsure whether something counts, leave it out and say why in
  the pull request instead of guessing.
- A test that writes files uses a temporary directory, never this
  checkout and never a real user's data.

Before you push, run the private-terms scanner over your own change:

```bash
python3 scripts/private_terms_scan.py
```

It reads `~/.brothersbe-private-names` by default; if that file does not
exist on your machine, the scanner has nothing configured to check for and
you should say so in your pull request rather than assume it passed.

## How a receipt is read

A real Brother run writes one `receipt/receipt.json` file inside its own
run directory, and prints that file's path as its last line. Open it and
look for these fields: which files changed, the exact check command run
against each one, that check's exit code, where its full output was
saved, and whether the check discriminated (failed before the change,
passed after) or reads NO-DATA because that could not be shown. A receipt
missing any of those for a changed file is itself a bug: `python3
scripts/test_receipt_door.py` is the check that refuses to write one.

## Where to start

`docs/onboarding/good-first-issues/` holds three drafted issues, each with
a title line, why it matters, the exact files to touch, a done check
command, and the label line `label: good first issue`. Pick one, check
whether it is already open as a labelled issue on the public repository,
and link your pull request to that issue if it is. Posting these drafts as
real GitHub issues is the founder's own click, not something this
repository does for itself, so a draft with no matching open issue yet is
still a real, verified gap: open the issue yourself, carrying the same
title and body, and reference it in your pull request.

## Where the readiness board lives

`docs/plan/ROADMAP-PUBLIC.html` in your clone is Brother's public roadmap
page: what shipped, what is open, and why, regenerated from the same
generator that writes the project's private board (`scripts/gen_readiness_board.py --public`).
Open it directly in a browser from your checkout; it carries no server and
needs no build step.
