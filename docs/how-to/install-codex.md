# Install Brother on Codex

Use this guide for Codex. Do not copy Claude's slash-command flow: Codex does not expose Brother that way.

## Prerequisites

Authenticated Codex with plugin support, Git, Python 3.9 or later, and a Brother checkout. The default Codex binary on macOS is `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`. For another installation, append `--codex-bin` with the actual absolute binary path to each installer/hook command below. Confirm that binary supports `plugin --help` first.

## Add Brother

Use the current Codex marketplace/plugin commands supported by your installed Codex version. The repository lifecycle tool below drives those commands and verifies the resulting state. The default Codex home is `~/.codex`.

This example uses 1.1.0, the current release. Review [releases](https://github.com/khalilmaaouni/Brother/releases) before choosing a tag; this example does not track the latest release automatically.

```bash
python3 scripts/brother_install.py status --codex-home "$HOME/.codex" --allow-default-home
```

Clone the repository at the v1.1.0 tag with `git clone --branch v1.1.0 https://github.com/khalilmaaouni/Brother.git`, change into it with `cd Brother`, then install with `python3 scripts/brother_install.py install --ref v1.1.0 --codex-home "$HOME/.codex" --allow-default-home`. These commands modify your real Codex plugin configuration.

These commands modify your real Codex plugin configuration. For a separate configured home, substitute its directory. Never use your home directory itself. The installer repairs the Brother end state and can remove superseded Brother registrations; review its output.

Status reports the plugin, marketplace ref, stale hooks, and standalone skill directories. Confirm the intended tag and `brother@brother`. Plugin presence alone does not establish hook trust or enforcement.

## Wire managed hooks

Set `TARGET_REPO` to your target repository's absolute path. From the Brother checkout, inspect wiring without changing it by running `python3 scripts/codex_hooks_install.py --check --codex-home "$HOME/.codex" --allow-default-home --cwd "$TARGET_REPO"`. If wiring or trust is missing, inspect the hooks before authorizing them. Then install and trust them with `python3 scripts/codex_hooks_install.py --codex-home "$HOME/.codex" --allow-default-home --trust --cwd "$TARGET_REPO"`. Run the `--check` command again after installation and require its own PASS for expected trusted and enabled wiring, not merely a successful plugin install. Do not hardcode a hook count. This wiring references checkout product files: keep that checkout at a stable path. Also inspect the run's effective safety mode; [hooks are not complete sandboxing](../reference/safety-boundaries.md).

Require the check's own PASS for expected trusted/enabled wiring, not merely a successful plugin install. Do not hardcode a hook count. This wiring references checkout product files: keep that checkout at a stable path. Also inspect the run's effective safety mode; [hooks are not complete sandboxing](../reference/safety-boundaries.md).

## Run Brother

Start a fresh Codex session so it discovers the skill. Ask: “Use Brother to reject non-numeric input in add(), preserve valid addition, and show the deciding checks and receipt.” Follow the [first-change tutorial](../tutorials/first-verified-change.md).

Inside a coding session, the installed skill supplies the engine's plan/contract route. A bare engine invocation is not a universal substitute and may require a separately configured model worker.

## Verify the result

Confirm plugin presence, hook trust, durable receipt creation, target-repository write boundaries, and that the receipt remains readable after process exit.

## Prove the hooks fire on a live host

The steps only a signed in human can do, with the output to expect, are in [host live proof](host-live-proof.md).

## Upgrade

From the Brother checkout, run the status command above to find your installed tag. Choose a newer tag from the releases page. Replace both placeholders below with those tags before running:

```bash
python3 scripts/brother_install.py status --codex-home "$HOME/.codex" --allow-default-home
```

Run the upgrade with `python3 scripts/brother_install.py upgrade --from-ref '<installed-tag>' --ref '<new-tag>' --codex-home "$HOME/.codex" --allow-default-home`.

The tool saves a snapshot before installing the new version. Read its result, rerun status, and repeat the managed-hook setup and check above. Keep the checkout used by those hooks at the chosen version. Start a fresh Codex session and try a small task before relying on the upgrade.

## Uninstall

From the same Brother checkout, remove the managed hooks before plugin removal. Continue with plugin removal only if that succeeds:

```bash
python3 scripts/brother_install.py status --codex-home "$HOME/.codex" --allow-default-home
```

From the same Brother checkout, remove the managed hooks with `python3 scripts/codex_hooks_install.py --codex-home "$HOME/.codex" --allow-default-home --uninstall`. Continue with plugin removal only if that succeeds, using `python3 scripts/brother_install.py uninstall --codex-home "$HOME/.codex" --allow-default-home`.

The hook command removes only Brother-owned registrations. The lifecycle tool removes Brother's plugins and marketplaces. Read its retained-data notices, then restart Codex.

## BrotherMode and BrotherSBE on Codex

Codex has skills, not Claude-style slash commands. The Brother umbrella package
now exposes namespaced routes in its `skills/` directory so the product
surfaces are visible in one install:

- `brothermode-*` exposes BrotherMode execution, delivery, recovery and native
  workflow capabilities.
- `brothersbe-*` exposes assurance, review, design, verification and handoff
  capabilities.
- `brotherme-*` exposes the existing compatibility command names as Codex
  skills, while routing to the canonical BrotherMode skill.

The aliases are generated from the product skills and command inventory. They
use the same Brother engine and receipt contract; the Claude source skills keep
their client-specific invocation controls.
