# BrotherSBE

**The only Claude Code plugin that tells you what it could not prove.**

Bring any agent. Use any tool. Prove every change.

Most tools report what passed. This one also reports, in the same breath and without being asked, what it never looked at. A green result here names its own scope, absent evidence is reported as NO-DATA rather than counted as success, and a waiver is listed rather than hidden because a waiver is not a pass.

That is the whole claim, and it is deliberately an uncomfortable one to make. A report listing only what went well reads as marketing. A report that volunteers what it failed to establish is the only kind a reviewer can act on, because it tells them where to spend their attention.

BrotherSBE is the assurance layer for high-risk backend, data, infrastructure, integration, and technical QA changes. It gives each change one shared record of intent, risk, evidence, and human approval, across any agent and any toolchain. Design before code, evidence before done.

It owns assurance and borrows execution: your editors, agents, tests, CI, and platform tools keep doing the work. BrotherSBE tells you what evidence this change actually needs, gathers what ran, keeps PASS, FAIL, and honest NO-DATA distinct, and hands the reviewer one page of proof instead of an afternoon of re-derivation.

BrotherSBE does not replace your engineers, QA, or CI/CD. It gives them a structured workflow and evidence they can inspect. The full identity, boundaries, and roadmap live in [Direction](docs/DIRECTION.md).

## You do not have to start anything

Install it and keep working. When you are changing a warehouse model, a
service contract, a migration, a test, or a specification, the guidance for
that situation arrives on its own, in your words, naming the two commands that
answer it. Nothing to launch, no session to open, no command to memorise. The
guided entry point below is for people who want it, not a toll gate for people
who do not.

**When this is not the tool.** If you want a feature written quickly, use your
coding agent directly; neither this product nor its sibling is a faster way to
generate code, and neither claims to be. BrotherSBE steps in when a change is
risky enough that somebody will have to trust it: a migration, a contract, a
pipeline, a production path. On a low-risk change its correct answer is one
line saying your existing checks are enough, so it costs you nothing to leave
installed.

**Which product, when.** BrotherSBE is the assurance layer for one change:
what it is, what it risks, what was proven, who approved it. Its sibling
product is the orchestration layer for a whole session: roles, delegation, and
who is writing which file. BrotherSBE works completely on its own and needs
nothing else running; when the orchestration layer is present the two compose,
with BrotherSBE still owning the evidence and the record.

## Start engineering

Install BrotherSBE:

```bash
claude plugin marketplace add khalilmaaouni/BrotherSBE
claude plugin install brothersbe@brothersbe
```

Open Claude Code inside your project and run:

```text
/brothersbe:start
```

That is the main entry point.

BrotherSBE inspects the repository, understands whether you are starting or resuming work, and recommends the next action.

**[Run your first real change ->](docs/getting-started.md)**

**[See the complete workflow ->](docs/workflow-map.md)**

## Find your situation

| You are | Start here |
| --- | --- |
| New to BrotherSBE | [Getting Started](docs/getting-started.md), one real change from install to verification |
| Adding it to an existing repository | Run `/brothersbe:adopt`, which is a dry run by default, then [Adoption](docs/ADOPTION.md) |
| Upgrading an existing install | `claude plugin update brothersbe`, restart to apply, then [CHANGELOG.md](CHANGELOG.md) and [Migration](docs/MIGRATION.md) |
| Wiring it into CI | [CI/CD](docs/ci-cd.md), then [CI-ORDER.md](docs/CI-ORDER.md) for the exact step order |
| Just looking around first | [A worked engagement](docs/guides/05-a-worked-engagement.md), one system designed end to end with real output |

## The engineering loop

```text
START
  |
INTAKE
  |
DESIGN
  |
IMPLEMENT
  |
REVIEW
  |
VERIFY
  |
STATUS
  |
HUMAN MERGE
```

The amount of process scales with risk. A small change should stay small. A change affecting money, production, data models, contracts, migrations, or multiple consumers should carry more design and evidence.

## Why BrotherSBE?

AI can generate implementation faster than most teams can verify it.

BrotherSBE focuses on the parts that become harder as AI output increases:

- **Design before implementation**: purpose, architecture, contracts, data grain, systems of record, and verification are made explicit first.
- **Scoped implementation**: human and AI workers get clear ownership, paths, dependencies, acceptance criteria, and verification commands.
- **Specialist review**: data, backend, migration, security, architecture, and QA review are routed from the actual change, not chosen by a model.
- **Evidence before done**: important claims are backed by checks that actually ran, recorded as receipts.
- **Three evidence states**: `PASS`, `FAIL`, and `NO-DATA`. Missing evidence is never silently treated as success.
- **Human control**: BrotherSBE does not merge, approve, or deploy.

## Where it helps

### Snowflake and ELT

Make data engineering assumptions explicit before SQL is generated: grain, keys, system of record, cardinality, incremental logic, backfill and migration strategy, reconciliation, downstream consumers, verification.

For important numbers, use a pinned warehouse state and a genuinely different second derivation rather than validating a query by running the same logic twice.

**[Snowflake and ELT guide ->](docs/snowflake-elt.md)**

### Technical QA

```text
Requirement
  |
Acceptance criteria
  |
Executable check
  |
Execution
  |
Evidence
```

Technical QA challenges negative paths, retries, timeouts, duplicates, partial failures, schema drift, recovery, and whether the test actually proves the requirement.

**[Technical QA guide ->](docs/technical-qa.md)**

### CI/CD

Start with BrotherSBE in advisory mode. Learn what it catches and what it misses.

Only move selected checks into strict mode after the team trusts them.

Your branch protection, repository permissions, and CI/CD remain the enforcement layer.

**[CI/CD guide ->](docs/ci-cd.md)**

## Documentation

- **[Getting Started](docs/getting-started.md)**: install BrotherSBE and run one real change from start to verification.
- **[Workflow Map](docs/workflow-map.md)**: command order, purpose, output, and verification point for each stage.
- **[Command Reference](docs/commands.md)**: quick reference for guided commands and CLI commands. The full CLI surface is in [docs/CLI.md](docs/CLI.md).
- **[Snowflake and ELT](docs/snowflake-elt.md)**: practical data engineering workflow and validation examples.
- **[Technical QA](docs/technical-qa.md)**: requirement-to-evidence workflow for QA and validation.
- **[CI/CD](docs/ci-cd.md)**: advisory rollout, evidence, and strict enforcement. The exact CI step order is in [docs/CI-ORDER.md](docs/CI-ORDER.md).
- **[A worked engagement](docs/guides/05-a-worked-engagement.md)**: one system designed end to end, with the real commands and the real output.
- **[The sandbox](docs/guides/00-sandbox.md)**: rehearse the loop on a disposable dossier before touching real work.
- **[The booklet](docs/fieldbook/BrotherSBE-Booklet.html)**: the full story for a team deciding whether to adopt: outcomes, personas, the team operating model, and the trust mechanics.
- **[The engineering reference](docs/ENGINEERING-REFERENCE.md)**: the complete documentation of the method, the gates, the laws, and every install path. Nothing was cut when this README was shortened; it all lives there.

## First team test

Do not start with a toy project.

Pick one real change such as:

- Snowflake transformation
- ELT pipeline change
- data reconciliation problem
- migration or backfill
- backend API change
- partner integration
- CI/CD change
- technical QA problem

Run it through the workflow and tell us:

What was useful? What was confusing? What was too heavy? What did BrotherSBE miss? Where would you still not trust it?

## Status

BrotherSBE is an early engineering system, tested on itself.

The controls and commands are covered by the test suites in this repository. They run LOCALLY through `scripts/local-gates.sh` on a real machine, which posts the one required commit status; GitHub Actions is disabled on every repository in this estate by founder decision of 2026-08-16, so no cloud leg runs and there is no green cloud badge to trust. The retired Windows CI leg is kept in the record for what it was: it skipped the shell scripts by name, so it was green while the two shell hooks were in fact broken on Windows (a real Windows install found this on 2026-08-17; both hooks are now Python, and [docs/KNOWN-LIMITS.md](docs/KNOWN-LIMITS.md) records the miss). Windows itself is therefore UNVERIFIED until a real machine runs [docs/WINDOWS-CHECK.md](docs/WINDOWS-CHECK.md) end to end, stated rather than implied by a passing simulation. Platform-specific patterns such as Snowflake integration still need real-world validation against actual engineering estates.

Every limit the checks cannot cover is written down in [docs/KNOWN-LIMITS.md](docs/KNOWN-LIMITS.md). The goal is not to claim more than the evidence supports.

**Design before code. Evidence before done.**

**Design before code. Evidence before done.**

## Requirements

- Python 3, standard library only. There is no `pip install`, no lockfile, and no dependency to audit beyond the tree itself. The floor is 3.9 and the ceiling is measured rather than assumed: the suites were run on 3.9.23, 3.13.7 and 3.14.0rc2 on 2026-08-18 and the failure set is identical on all three, so "3.9 or newer" holds as far as 3.14. `TESTERS.md` carries the table and names what is still unmeasured.
- git (the autosave, the approval gate, and the manifest all read it).
- Claude Code with hooks, for the session wiring above. The checkers run fine without it: every tool is a plain script you can run by hand.
- No shell of any kind: every hook command is `python3`, so `python3` must resolve in whatever shell your harness spawns hooks with. On Windows that shell is Git Bash, which arrives with Git for Windows (already required above for git itself). Two notes worth the seconds they cost, both learned from a Windows install: a python.org Windows installer ships `python.exe` and a `py` launcher but no `python3.exe`, so after installing Python run `python3 --version` in Git Bash and fix the PATH if it fails; and `bash` on the Windows PATH is `C:\WINDOWS\system32\bash.exe`, which is WSL and a different filesystem, so never substitute it for a hook command (`tools/test_sbe_hooks.py` refuses any hook that tries). PowerShell is not in that path at all, because nothing this project installs runs under it, but it IS the shell most Windows testers type in: Windows PowerShell 5.1 is what Windows ships and is the baseline `docs/WINDOWS-CHECK.md` is written against. `sbe doctor` names which of the two PowerShells it was launched from, since 5.1 and 7 disagree about operators, `where`, and encoding.
- The one sh exception is the optional clone-based install path: `install.sh` is now a two line POSIX sh shim that execs `tools/install.py` (a tested Python module, like every other tool here), and its two proof scripts (`scripts/test-install-artifact.sh`, `scripts/test-upgrade-rollback.sh`) are still POSIX sh, unported. On Windows run them from Git Bash, never PowerShell or WSL bash; the marketplace pair above needs no sh at all, and this line exists so a Windows installer learns the boundary here rather than from a failing script.

## Uninstall

Removal is three deletions, and this section names what each leaves behind so nothing lingers silently:

1. Remove the hook entries from `~/.claude/settings.json` (and any project `.claude/settings.json` you added them to).
2. Delete the clone: `rm -rf ~/.claude/skills/brothersbe`.
3. Decide about your data, which uninstalling does NOT delete: the vault at `$BROTHERSBE_VAULT` (your session logs and telemetry, yours to keep or delete), the `export BROTHERSBE_VAULT` line in your shell profile, and the autosave snapshots under `refs/brothersbe/` in any repository where the hook fired (list them with `git for-each-ref refs/brothersbe/`, delete with `git update-ref -d <ref>`).

## Learn more

- [docs/guides/05-a-worked-engagement.md](docs/guides/05-a-worked-engagement.md): one system designed end to end, real commands, real output. The best place to start.
- [docs/DESIGN.md](docs/DESIGN.md): the why and what, in the real order.
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md): the mechanical half, tool by tool.
- [docs/for-engineers/](docs/for-engineers/): onboarding for backend, data, infrastructure and ETL engineers who have never seen this tool. Start at [00-READ-ME-FIRST.md](docs/for-engineers/00-READ-ME-FIRST.md); four complete worked dossiers are in [docs/for-engineers/examples/](docs/for-engineers/examples/).
- [docs/SETUP.md](docs/SETUP.md) to install, and the rest of [docs/guides/](docs/guides/) for the gates, the doctrines, and teams.
- [SKILL.md](SKILL.md) plus the [`references/`](references/) files its routing table names are the law itself; [SECURITY.md](SECURITY.md) is the data and network posture (no network calls, no analytics, no account, no server).

## License

MIT. See [LICENSE](LICENSE).

Created by Khalil Maaouni.

## Part of Brother

This product is one capability area of Brother, the umbrella that carries the
shared chain, the verdict tuple and the evidence law across all three.

Read COORDINATION.md in https://github.com/khalilmaaouni/Brother before adding
a command, an agent, a hook, or a state vocabulary: the merge enforces surface
caps and a single state vocabulary, and work that fails them will be deleted.
