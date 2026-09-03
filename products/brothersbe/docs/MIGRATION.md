# Migration: from a cloned skill to an installed plugin

BrotherSBE used to be installed by cloning this repository into
`~/.claude/skills/brothersbe/` and hand-editing `~/.claude/settings.json` to wire four hooks.
That still works. It is no longer the recommended way, and it is not a shape a team can
version.

This document says what changed, what did not, and how to move without losing anything.

## What changed

| Before | Now |
|---|---|
| clone into `~/.claude/skills/brothersbe/` | install as a plugin, or point Claude Code at this directory |
| hand-edit `~/.claude/settings.json` for four hooks | `hooks/hooks.json` ships with the plugin and resolves its own paths |
| one skill, invoked `/brothersbe` | six namespaced skills: `/brothersbe:kickoff`, `:design`, `:verify`, `:review`, `:learn`, `:adopt` |
| the reviewer lenses lived in prose | seven read-only agents in `agents/` |
| the version lived only in `VERSION` | `VERSION` and `.claude-plugin/plugin.json` must agree, and a test fails when they do not |

## What did not change

Nothing was moved or rewritten. `SKILL.md`, `references/`, `tables/`, `templates/`, `tools/`,
`evals/` and `docs/` are exactly where they were, at the same paths, with the same behavior.
Every law citation, every eval and every documented command still resolves. That was a
deliberate constraint on this conversion: packaging is not a licence to move the law.

The gates are unchanged and were re-run after the conversion. Nothing was weakened to make a
manifest validate.

## Installing as a plugin

From this repository, on the machine that will use it:

```
claude plugin validate /path/to/BrotherSBE
```

That must exit cleanly before you install anything. Then either add the repository as a
marketplace source (`claude plugin marketplace add khalilmaaouni/BrotherSBE`, then
`claude plugin install brothersbe@brothersbe`), which is the persistent install, or point a
session at the directory with `--plugin-dir`, which loads it for that session only. The public
repository itself is the marketplace source today, verified working; a signed release pinned in
a directory listing is still ahead, see [docs/ROLLOUT.md](ROLLOUT.md).

## If you already installed the old way

1. Keep your clone until the plugin install is working. Nothing in this conversion needs you to
   delete anything.
2. Remove the four BrotherSBE hook entries from `~/.claude/settings.json`, or you will run each
   hook twice: once from your settings and once from the plugin. Double-running is not harmful
   (every hook exits 0 and the telemetry append is idempotent) but the session-start injection
   will be duplicated in your context, which wastes the budget it is careful about.
3. Your memory vault does not move. Everything the tool writes still goes to the vault
   directory named in `docs/SETUP.md`, never into the plugin directory.

## What this conversion does not fix

The packaging blocker is closed. The control gaps are not. Evidence can still be hand-authored,
the tier is still computed from answers rather than from the diff, approvals are still not
resolved against a review platform, and the write fence still fails open and does not gate
Bash. Those are named, with their consequences, in `docs/KNOWN-LIMITS.md`, and each one has a
wave of work behind it. Do not read a validating plugin manifest as a statement about any of
them.
