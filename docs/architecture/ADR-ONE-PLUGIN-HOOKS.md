# ADR: the one plugin carries its own hooks, guarded against double firing

Status: accepted 2026-09-30 (owner order the same morning: one Brother plugin
end to end, the three product entries retire at the 1.1.0 cut, WBS U8).
Amended 2026-10-03 by OP1.a (`docs/plan/specs/OP1.md` section 5.1, council
findings against the landed guard): a blocking event is wrapped and always
RUN, and the guard trusts fewer inputs (Decision 3 and Failure direction).

## Context

Until 1.1.0 the `brother` bundle declared `brothermode`, `brothersbe` and
`brotherds` as plugin dependencies and shipped its merged hooks under
`bundle/hooks/union.json`, a name Claude Code does not auto load. The hooks
reached Claude Code only through the dependency plugins. Commit `fccd6ce2f`
(2026-09-13) chose that shape on purpose: when the merged file sat at the
conventional `hooks/hooks.json`, Claude Code enabled the dependencies too and
every shared hook fired twice. Retiring the three entries turns that shape
into a plugin with no fence, no clock guard and no session memory at all
(architecture review 2026-09-30, findings F1 and F16).

## Decision

1. `scripts/bundle_runtime.py` writes the merged hooks to
   `bundle/hooks/hooks.json` (`HOOKS_JSON_NAME`), the name Claude Code auto
   loads. `union.json` is now the retired name and `check_hooks()` refuses
   it, exactly as it refused `hooks.json` before.
2. Every generated command is wrapped by one guard (the generator refuses
   an event name Claude Code does not document, since the event is
   interpolated into the shell command),
   `${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py <product> <event> "--matcher=<matcher>" <command...>`,
   the matcher being the hook's own matcher group (empty when it has none).
   The guard's source is `scripts/hook_guard.py`; the generator mirrors it
   into `bundle/runtime/hooks/` and lists it in `HOOKS-MANIFEST.json`, so a
   hand edited copy fails `--check` like any other mirrored file.
3. The guard yields (exits 0, prints nothing) PER HOOK, only on a non
   blocking event, in exactly two cases, and otherwise execs the wrapped
   command with stdin, stdout and exit code untouched. A blocking event
   (`PreToolUse`, `Stop`) is wrapped and always RUN, whatever the registries
   say: a legacy copy that is registered but crashes at runtime would
   otherwise leave the action unchecked, and two identical decisions are one
   decision (amended 2026-10-03; before, a yield there handed the decision
   to the old plugin's identical hook). The two cases:
   - the old product plugin for `<product>`, under its full key
     `<product>@brother` (the one marketplace the bundle ships under, so a
     same named plugin from another marketplace never counts), is enabled by
     the effective `enabledPlugins` (user, shared project, project local and
     managed settings merged per key, higher level wins, per the settings
     precedence page read 2026-09-30), is installed at the `installPath`
     recorded in `plugins/installed_plugins.json`, and its own
     `hooks/hooks.json` registers the SAME hook, whose script exists inside
     that `installPath` (the command split as a shell splits it, so a quoted
     path with a space is one token): the same event, a matcher
     group that COVERS this hook's matcher (the old group is empty or `*`,
     or both are plain `A|B` alternations and the old one holds every name
     in the bundle's), and a command naming the same script basename WITH THE
     SAME ARGUMENTS after the script (F6, third attack: an old hook running
     the script as `--audit-only` is a different hook, so the bundle fires;
     flags before the script, such as `python3 -B`, are not compared). An
     old group NARROWER than the bundle's (Edit|Write against a matcher
     that adds Bash) does not cover it, so the bundle fires; yielding there
     would leave Bash with the hook zero times (second attack, F5). Matchers
     are compared as Claude Code evaluates them (code.claude.com/docs/en/hooks,
     matcher patterns, read 2026-09-30): exact, case sensitive tool names,
     so `bash` never covers `Bash`, and anything with other characters
     (`Foo\|Bash`, `.*`) is a regex, which the guard treats as unknown.
     The install counts only where it loads: `scope` user everywhere,
     `project` or `local` only when `projectPath` is this project by
     realpath; any other scope, or a project install without a
     `projectPath`, does not count. Measured 2026-09-30 on this
     machine: brothermode 3.4.5 registers no clock guard, so a plugin-wide
     yield silenced `bm_clock_guard` entirely; the per-hook rule keeps every
     hook the old plugin lacks firing from the bundle.
   - a second enabled, installed `brother@brother` root registers the same
     hook and sorts before this one: only the first root fires when the
     same bundle is installed twice. The guard's own root is read from its
     own path (`<root>/runtime/hooks/hook_guard.py`), never from the
     environment.
4. `bundle/.claude-plugin/plugin.json` carries no `dependencies` key, and
   `check_hooks()` refuses one.

## Failure direction

Any unreadable, missing or malformed registry file, any entry without a
usable `installPath`, any `CLAUDE_PLUGIN_ROOT` that is not among the
recorded roots, a command whose script cannot be named, a matcher on either
side that is not empty, `*` or a plain alternation of names, a missing
`--matcher` argument, an install scoped to another project or to an
unknown scope, and ANY exception
inside the decision (a settings file nested past the recursion limit
included) all mean RUN. Amended 2026-10-03, these mean RUN as well: a
settings layer that EXISTS but cannot be read or parsed, is not a JSON
object, or carries an `enabledPlugins` that is not an object (the whole
`enabledPlugins` answer is then unknown, since that layer may disable the
old plugin; an absent layer still contributes nothing); the managed layer
on any platform but macOS, the one platform whose path the guard knows; an
old hook whose script is missing or resolves outside its install, or whose
command a shell could not split; a config or project directory (from
`CLAUDE_CONFIG_DIR` and `CLAUDE_PROJECT_DIR`, which stay read, or the
defaults `~/.claude` and the working directory) that is not an existing
directory owned by the current user, or that a group or other user can
write; and a `CLAUDE_PLUGIN_ROOT` that does not resolve to the root the
guard ships in. A guard that cannot prove the old plugin registers
the same hook never skips a fence; the worst wrong answer is one duplicate
line, never a missing check. A wrapped command that cannot be executed exits
2 with the reason on stderr for a blocking event (PreToolUse, Stop), so the
fence's absence blocks rather than passes, and 1 otherwise. Hooks whose only
output is a permission decision are on a blocking event, which never
yields, so the bundle's own hook always produces its decision; on a machine
that still has the old plugin it fires twice (loud and harmless) until the
old plugin is uninstalled. `--settings` passed on the command line is not
visible to a hook process and is the one layer not read.

## Rejected

- A marker written by the hook scripts themselves (event plus session id):
  needs every hook entry point edited, races between the two plugins' hook
  processes, and the old installed copies never get the new code.
- Neutralizing the old plugins' hooks on upgrade: the bundle cannot edit
  another plugin's install directory, and a pinned plugin never updates.
- Keeping the dependencies: the bundle ships the 15 slash commands as its
  own skills since 1.0.21, so the 2026-09-13 reason no longer holds.

## Proof

`python3 scripts/test_hook_guard.py` (guard alone fires once; beside an
enabled installed old plugin the shared hook fires once and a hook the old
plugin lacks still fires; project settings override user settings both
ways; an unreadable or absurd registry runs the command; an unrunnable
blocking hook exits 2; the 2026-10-03 hardenings, one case each: a
blocking event never yields, an unreadable existing settings layer, a
missing legacy script, another marketplace, a steered config dir, a quoted
script path with a space, a foreign `CLAUDE_PLUGIN_ROOT` and an unknown
managed settings platform all run) and `python3 scripts/test_bundle_runtime.py` (a bundle carrying
`union.json`, an unwrapped command, or a `dependencies` key fails
`check_hooks()`). Migration for an existing user: install `brother@brother`,
uninstall the three product plugins when convenient; nothing fires twice in
between.
