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

## What a pull request must carry

- The exact done-check command you ran, and its output quoted in the pull
  request description, from a run after your last edit. A claim of
  "done" or "fixed" with no command and no quoted output is not accepted.
- No em or en dashes anywhere: commit messages, code comments, and the
  pull request body. Use commas, colons, or parentheses instead.
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

`docs/plan/GOOD-FIRST-ISSUES-2026-09-05.md` lists five small, real gaps
with a title, why it matters, the files it touches, a done-check, and a
rough time estimate. Pick one, open an issue if none is already labelled
`good-first-issue` for it, and link your pull request to that issue.
