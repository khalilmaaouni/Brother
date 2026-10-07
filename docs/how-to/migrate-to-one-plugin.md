# Migrate to the Brother plugin

From release 1.1.0 the catalog lists one plugin, `brother`. The three older plugins, `brothermode`, `brothersbe` and `brotherds`, are retired from the catalog. If you installed `brothermode` or `brothersbe`, the move is one sentence: install brother@brother, then uninstall this plugin. Their own help says the same.

One exception: `brotherds`. The one plugin does not carry claim verification in 1.1.0. If you use `brotherds`, install brother@brother beside it and keep your `brotherds` copy; do not uninstall it.

What stays the same: a pinned install of an old plugin keeps resolving, and its old slash commands keep working on that old copy. Nothing breaks on the day you update. What changes: new work arrives only in `brother`. After migrating, use the one door: type `/brother` and describe the outcome.

## Claude Code

Install brother@brother, then uninstall this plugin (the old one).

1. Install `brother@brother` using the [Claude Code install guide](install-claude-code.md).
2. Then uninstall the old plugin using the uninstall steps in that guide.

## Codex

Install `brother`, then uninstall the old plugin.

1. From a Brother checkout, follow the [Codex install guide](install-codex.md) to install `brother`. Its installer can remove superseded Brother registrations.
2. To remove the old plugin, follow the uninstall steps in that guide. It removes Brother-owned managed hooks before removing Brother plugins and marketplaces.

## Cursor

Install `brother`, then uninstall the old plugin.

1. From a Brother checkout, follow the [Cursor install guide](install-cursor.md) to install `brother`.
2. Use that guide's uninstall command to remove the old plugin.

## Antigravity

Antigravity is experimental and unverified in 1.1.0. Follow the [Antigravity install guide](install-antigravity.md). It describes copying the plugin directory and removing it from the scratch workspace, but does not document a command to install `brother` or remove an old plugin.

## Check the migration

On Claude Code or Cursor, open a repository and invoke `/brother`. On Codex, start a fresh session and use the installed Brother skill. Follow the host's install guide to verify the result.
