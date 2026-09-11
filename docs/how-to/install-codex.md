# Install Brother on Codex

Use this guide for Codex. Do not copy Claude's slash-command flow: Codex does not expose Brother that way.

## Prerequisites

Codex CLI/app authenticated; `git` and `python3`; a Brother checkout for repository scripts such as the hook installer.

## Add Brother

Use the current Codex marketplace/plugin commands supported by your installed Codex version and the Brother repository. Confirm `brother@brother` appears in the intended plugin/marketplace state.

## Wire managed hooks

Plugin presence and hook trust are separate facts. From a Brother checkout:

```bash
python3 scripts/codex_hooks_install.py --codex-home ~/.codex --allow-default-home --trust
```

Trust the wiring only when the installer itself reports that Codex sees the expected Brother hooks trusted/enabled. Do not hardcode an old hook count in automation.

## Run Brother

At repository-engine level:

```bash
python3 scripts/brother_run.py "<outcome>" --cwd <repo>
```

An installed plugin can expose the equivalent through its skill/runtime root. Prefer the current installed skill's routing instructions.

## Verify the result

Confirm plugin presence, hook trust, durable receipt creation, target-repository write boundaries, and that the receipt remains readable after process exit.

## Uninstall

Use the hook installer's supported `--uninstall` path before plugin removal so only Brother-owned hook registrations are removed from a shared host config.
