# Contributing to BrotherMode

Every claim on these pages names a file or a command you can run. Hold your
change to the same standard: a change that breaks that trace is a bug even
if the code itself is correct. Read this before your first change.

## Set up a development copy

Do not clone the tagged release for development work. The tagged clone (run
`python3 tools/bm_project_facts.py --field install_target_tag` to see which
tag) is the immutable, pinned install target real users point at; working
against it means your edits sit on a snapshot that is not going anywhere.
Use the development command instead, which tracks the moving `main` branch
on purpose and installs into its own directory so the two can never be
confused:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/BrotherModeUp.git ~/.claude/skills/brothermode-dev
```

That is the exact text `python3 tools/bm_project_facts.py --field
install_command_dev` prints, read out of the tree rather than typed by hand,
so it cannot go stale the way a copied line in a document can.

Requirements: Claude Code (CLI or desktop app) with skills enabled, Python
3.9 or newer using the standard library only, and git. Nothing else to
install.

## Run the gate

```bash
python3 tools/test_all.py
```

This is the actual gate this project runs on, not a suggestion. It runs
every suite serially, one process each, and ends with a line reading `ALL
GREEN` and exit code 0. It takes several minutes; that is the cost of the
isolation the header of `tools/test_all.py` explains, not a hang. A single
suite still runs on its own while you are working on one of them (for
example `python3 tools/test_bm.py`), but passing one suite is not the gate.
Do not propose a change on a red gate, and do not propose a change without
having run the gate at all.

## The laws a contributor has to obey

These are not house style. Several are enforced by a test that fails the
build; the rest are enforced by the fact that the project reads its own
tree rather than trusting what a page says about it.

**One writer per file.** `tools/bm_fence_hook.py` runs on the `PreToolUse`
hook and refuses a write to a file another active claim covers, for the
write tools it can see (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`, and a
readable `apply_patch` envelope on the `Bash` leg). It is the one hook that
can say no, and `tools/test_bm_fence_hook.py` holds that behavior in place.
Claim a file before you write it; do not start a second parallel effort on a
file another effort already owns.

**No edits to tracked files while the gate runs.** The gate lock
`tools/test_all.py` takes now doubles as a battery-in-progress announcement,
and the same fence hook refuses a write to any git-tracked file while a live
gate lock covers the checkout, for every session including the one that
started the run: an edit mid-run invalidates the baseline the gate is
measuring. Untracked scratch files stay writable. The refusal names the
holder and every door that opens; docs/HOOKS.md has the full state table.

**No em or en dashes, anywhere.** Not in this file, not in code comments,
not in commit messages, not in generated output. Use a comma, a colon, or a
period instead. The test that enforces it is
`tools/test_bm_docs.py::test_no_em_or_en_dash_in_the_active_pages_or_this_toolchain`,
which sweeps a named list of active documentation pages (`README.md`,
`SKILL.md`, and the core pages under `docs/`) and this toolchain's own
source for the literal Unicode characters at code points U+2013 (en dash)
and U+2014 (em dash), and fails if either appears. Sibling tests hold the
same line for the generated documentation pack, the identity and capability
registers, and the standalone whitepaper page. This contributor pack is not
on that swept list, so check it yourself: an unscoped sweep of the whole
tree also flags quoted third-party source under `docs/evidence/`, so scope
the check to the files your change touches, for example:

```bash
git diff --name-only main... | xargs python3 -c "
import sys
EN, EM = chr(0x2013), chr(0x2014)
for p in sys.argv[1:]:
    for i, line in enumerate(open(p, encoding='utf-8'), 1):
        if EN in line or EM in line:
            print('%s:%d' % (p, i))
"
```

Expect no output. This uses only the standard library, so it runs the same
way on every platform; do not rely on a shell `grep` flag for this, since
Unicode-aware regex flags are not the same across `grep` builds.

**A claim needs a command run after the last edit.** Never write "done",
"fixed", or "works" in a commit message, a pull request, or a code comment
unless a verifying command run after your final edit actually passed, and
name that command. This project's own `tools/test_bm_docs.py` enforces the
same rule against its documentation: a page that pins a stale test count, a
stale version, or a dated record with no status fails the build. Hold your
own pull request description to the same standard you are being held to.

**Docs ship in the same change as the code.** Two of the surfaces a
contributor is most likely to touch are generated, not hand-written, and
handling them the wrong way fails the gate:

- The capability table in `README.md` and the status block in
  `docs/ROADMAP.md` are both rendered from `capabilities.status.json`. If
  your change adds or changes a capability, edit that register file, never
  the blocks themselves, then regenerate them:
  ```bash
  python3 tools/bm_docs.py capability-status --write
  python3 tools/bm_docs.py roadmap-status --write
  ```
  `tools/test_bm_docs.py` fails a block that was hand-edited instead of
  regenerated, and fails a block that disagrees with the register.

- `docs/brotherme-explained.html` and any other page under `docs/` that
  describes a feature you changed are hand-written, not generated. Update
  them yourself, in the same commit as the code, not a follow-up one.
  `tools/test_bm_docs.py` refuses a page that disagrees with the tree it
  describes (a hook count the installer does not actually wire, a version
  other than what `VERSION` holds), so a change that updates behavior
  without updating the page it is described on will not pass the gate.

**Never invent a path, a flag, or a command.** Confirm a file exists with
`ls` or `grep` before you cite it; confirm a CLI invocation with `--help` or
by reading the script before you write it into a document. This project's
own pages hold themselves to exactly this standard (see the "Evidence"
section of `README.md`).

## How to propose a change

1. **Branch.** Work on a branch, not on `main` directly.
2. **Run the gate.** `python3 tools/test_all.py` must end `ALL GREEN`, exit
   0, on your branch after your last edit, before you propose anything.
3. **Do not touch `CHECKSUMS.sha256`.** It is a release artifact, rebuilt
   last, over the release tree, after the gate, as one step in the
   founder-gated release sequence in `docs/RELEASE.md`. `tools/test_bm_docs.py`
   checks the manifest against the tagged release, not against your branch,
   so a pull request has nothing to regenerate it against. Leave the file
   alone.
4. **Pull request.** Open the pull request against `main`. Describe what
   changed and why, name the exact gate command you ran and its result, and
   name every documentation page you updated to match. Do not claim a test
   count in the pull request description; counts move with every change and
   a reader who sees a mismatch cannot tell a stale claim from a broken
   build (`README.md`'s "Evidence" section explains why this project does
   not quote counts on its own pages either).

Cutting and publishing an actual release is a separate, founder-gated
sequence described in `docs/RELEASE.md`. Nothing in a normal contribution
touches a git tag, pushes a tag, or publishes a release artifact; those
steps are explicitly reserved for the founder and a contribution that
attempts them will be rejected on that basis alone.

## What gets a change rejected

- A red gate. `python3 tools/test_all.py` not ending `ALL GREEN` on your
  branch, or a pull request that does not state having run it after the
  last edit.
- Any em or en dash in a file the dash sweep covers.
- A code change that touches a documented feature or capability without
  updating the page that describes it in the same change.
- A path, flag, command, or test count typed from memory rather than
  confirmed against the tree.
- `CHECKSUMS.sha256` touched or regenerated in a pull request. It is a
  release artifact, never a contribution's to change.
- A change that writes to a file another active claim owns, bypassing the
  fence instead of coordinating around it.
- Anything touching `git tag`, a tag push, or a release publish step. Those
  are founder-gated in `docs/RELEASE.md` and a contribution is never the
  place for them.
- A claim of "done", "fixed", or "works" with no verifying command named,
  or with a command that was run before rather than after the final edit.
