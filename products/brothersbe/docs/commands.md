# Command Reference

Use this page when you know what you want to do and need the exact command.

If you do not know which command is next:

```text
/brothersbe:start
```

This is the quick reference. The complete CLI surface, with every flag and exit code, is in [CLI.md](CLI.md).

## Guided commands

| Command | Use it when |
| --- | --- |
| `/brothersbe:start` | Starting or resuming BrotherSBE work |
| `/brothersbe:adopt` | Installing BrotherSBE into a repository, or checking an existing install is wired |
| `/brothersbe:next` | You want exactly one recommended next action |
| `/brothersbe:status` | You want the current engineering state |
| `/brothersbe:help` | You need BrotherSBE guidance |
| `/brothersbe:review` | A diff or PR should be reviewed against the design |
| `/brothersbe:verify` | Work is about to be called done or important evidence is required |
| `/brothersbe:handover` | Ownership needs to move to another named human |
| `/brothersbe:learn` | A repeated correction or incident lesson should become a proposed team rule |

Kickoff, design and work are not typed on purpose: `/brothersbe:start` routes into each once the
work reaches it (kickoff maps the ground and scores the intake, design runs once that intake
exists, work runs once a plan is ready), so nothing has to be memorised to get there. Each still
answers to its own name (`/brothersbe:kickoff`, `/brothersbe:design`, `/brothersbe:work`) for
someone who wants to reach it directly.

## Core CLI commands

| Command | Purpose |
| --- | --- |
| `sbe doctor` | Check the installation and environment |
| `sbe intake design/<change>` | Score change risk and write intake |
| `sbe design design/<change>` | Run design completeness checks |
| `sbe impact . --base origin/main` | Compare actual diff with declared risk |
| `sbe review-route design/<change> --base origin/main` | Route specialist review; naming the dossier honours its declared tier, omitting it derives a tier from the diff alone and says which intakes it skipped |
| `sbe evidence run --check <id> --out <receipt>` | Run the check registered under that id in `.sbe/checks.yml`; the registry defines what runs |
| `sbe evidence run --out <receipt> -- <command>` | Run a free-form command as advisory evidence; satisfies no required policy check |
| `sbe gate <gate> <directory>` | Run one hard gate (`numbers`, `migration`, `approval`, `ran`) |
| `sbe status` | Show blockers and evidence state |
| `sbe converge design/<change> --base <sha> --head HEAD` | Check whether the code still matches the dossier |
| `sbe fences` | Show current write ownership and fences |
| `sbe handover` | Prepare, inspect, accept, or reject explicit handover |
| `sbe lineage <artifact>` | Follow evidence and decision history for an artifact |
| `sbe explain` | Explain a decision or gate package |

Every command above (and every one it does not list) also accepts `--cwd <repo>`: run it from
anywhere and name the repository it operates on, instead of `cd`-ing there first. See
[CLI.md's `--cwd` section](CLI.md#--cwd-the-flag-every-subcommand-shares) for exactly how it
combines with a command's own path argument.

If `sbe` is not on your PATH, run `bin/sbe` from a clone or the plugin cache; see [Getting Started](getting-started.md).

## Normal command order

```text
/brothersbe:start

mkdir -p design/my-change
sbe intake design/my-change

sbe design design/my-change            (design fires on its own once start reaches it)

                                        (work fires on its own once a plan is ready)

/brothersbe:review
sbe review-route --base origin/main

/brothersbe:verify

/brothersbe:status
sbe status
```

You do not need to run every command for every change.

Risk decides how much workflow the change needs.
