# Install Brother on Codex

Use this guide for Codex. Do not copy Claude's slash-command flow: Codex does not expose Brother that way.

## Prerequisites

Authenticated Codex with plugin support, Git, Python 3.9 or later, and a Brother checkout. The installer defaults to `/Applications/ChatGPT.app/Contents/Resources/codex` on macOS. For another installation, append `--codex-bin` with the actual absolute binary path to each installer/hook command below. Confirm that binary supports `plugin --help` first.

## Add Brother

Use the current Codex marketplace/plugin commands supported by your installed Codex version. The repository lifecycle tool below drives those commands and verifies the resulting state. The default Codex home is `~/.codex`.

This example pins v1.0.13 for reproducibility, not as a promise that it will always be the latest. Review [releases](https://github.com/khalilmaaouni/Brother/releases) before choosing a different tag.

```bash
git clone --branch v1.0.13 https://github.com/khalilmaaouni/Brother.git
cd Brother
python3 scripts/brother_install.py install --ref v1.0.13 --codex-home "$HOME/.codex" --allow-default-home
python3 scripts/brother_install.py status --codex-home "$HOME/.codex" --allow-default-home --json
```

These commands modify your real Codex plugin configuration. For a separate configured home, substitute its directory. Never use your home directory itself. The installer repairs the Brother end state and can remove superseded Brother registrations; review its output.

Status reports the plugin, marketplace ref, stale hooks, and standalone skill directories. Confirm the intended tag and `brother@brother`. Plugin presence alone does not establish hook trust or enforcement.

## Wire managed hooks

Set `TARGET_REPO` to your target repository's absolute path. First inspect wiring without changing it, from the Brother checkout:

```bash
python3 scripts/codex_hooks_install.py --check --codex-home "$HOME/.codex" --allow-default-home --cwd "$TARGET_REPO"
```

If wiring/trust is missing, inspect the hooks before authorizing them. To install and trust them:

```bash
python3 scripts/codex_hooks_install.py --codex-home "$HOME/.codex" --allow-default-home --trust --cwd "$TARGET_REPO"
python3 scripts/codex_hooks_install.py --check --codex-home "$HOME/.codex" --allow-default-home --cwd "$TARGET_REPO"
```

Require the check's own PASS for expected trusted/enabled wiring, not merely a successful plugin install. Do not hardcode a hook count. This wiring references checkout product files: keep that checkout at a stable path. Also inspect the run's effective safety mode; [hooks are not complete sandboxing](../reference/safety-boundaries.md).

## Run Brother

Start a fresh Codex session so it discovers the skill. Ask: “Use Brother to reject non-numeric input in add(), preserve valid addition, and show the deciding checks and receipt.” Follow the [first-change tutorial](../tutorials/first-verified-change.md).

Inside a coding session, the installed skill supplies the engine's plan/contract route. A bare engine invocation is not a universal substitute and may require a separately configured model worker.

## Verify the result

Confirm plugin presence, hook trust, durable receipt creation, target-repository write boundaries, and that the receipt remains readable after process exit.

## Uninstall

To remove separately installed managed hook wiring before plugin removal, or leave the plugin installed without that wiring, use the hook installer's supported route:

```bash
python3 scripts/codex_hooks_install.py --uninstall --codex-home "$HOME/.codex" --allow-default-home
```

Use the supported lifecycle rather than deleting shared configuration:

```bash
python3 scripts/brother_install.py uninstall --codex-home "$HOME/.codex" --allow-default-home
```

Recheck status and restart the host. Preserve run records and Vault knowledge you still need.

## Upgrade and rollback

Inspect status first. Set `FROM_REF` to the actual installed tag and `TO_REF` to the selected release:

```bash
python3 scripts/brother_install.py upgrade --from-ref "$FROM_REF" --ref "$TO_REF" --codex-home "$HOME/.codex" --allow-default-home
```

Upgrade creates a snapshot. Restore the newest snapshot with:

```bash
python3 scripts/brother_install.py rollback --codex-home "$HOME/.codex" --allow-default-home
```

Rollback restores saved configuration. Review intervening host configuration changes before using it. Recheck plugin status and hook trust after any lifecycle change. For interrupted work rather than install failures, use [recovery](recover-from-failure.md).
