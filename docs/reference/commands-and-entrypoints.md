# Commands and entry points

Host runtimes differ. This page separates public surfaces from engine entry points.

## Claude Code

Install:

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

Upgrade:

```bash
claude plugin marketplace update brother && claude plugin update brother@brother
```

Uninstall:

```bash
claude plugin uninstall brother@brother && claude plugin marketplace remove brother
```

Use lowercase marketplace name `brother`.

## Codex

Codex does not expose Brother through Claude-style slash commands. Follow [Install on Codex](../how-to/install-codex.md).

From a Brother checkout, the repository engine entry is:

```bash
python3 scripts/brother_run.py "<outcome>" --cwd <repo>
```

An installed plugin may expose the equivalent under its runtime path. Prefer the current installed skill/plugin root over hardcoded user-home paths.

## Continue

At engine level:

```bash
python3 <brother-runtime>/brother_run.py --continue --cwd <repo>
```

The user-facing door should discover unfinished work first.

## Advanced inputs

```text
--plan <plan.json>
--contract <outcome-contract-record.json>
```

Keep plan files outside the target repository. The current in-session plan route expects a contract unless a deliberate alternate decomposer path is chosen.

## Authority

Routing: `bundle/skills/using-brother/SKILL.md`.

Engine: current `scripts/brother_run.py`/bundled runtime and its tests.
