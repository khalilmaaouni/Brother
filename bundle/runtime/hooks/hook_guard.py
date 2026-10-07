#!/usr/bin/env python3
"""hook_guard.py: run a bundled hook command once, never twice, never zero.

Usage (as the generator writes it into bundle/hooks/hooks.json):

    python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py" <product> \\
        <event> "--matcher=<matcher>" <command> [args...]

Design: docs/architecture/ADR-ONE-PLUGIN-HOOKS.md. The guard YIELDS (exit 0,
no output) only on a non blocking event, and only when the SAME HOOK (same
event, same script basename, the same arguments after the script, and a
matcher that covers this hook's matcher) is already registered by an old
standalone plugin installed under the full key <product>@brother that is both
enabled, by the effective enabledPlugins across every settings layer Claude
Code reads, and installed with its own hooks/hooks.json and the script that
hook names; or by a second enabled, installed brother root that sorts before
this one. Otherwise it execs the command, so stdin, stdout and the exit code
are the command's own.
Measured 2026-09-30: the installed brothermode 3.4.5 registers no clock
guard, so under a plugin-wide yield bm_clock_guard fired zero times; the
per-hook rule keeps it firing.

FAILURE DIRECTION: anything unreadable, missing, malformed, or any exception
at all inside the decision means RUN. A matcher that is not empty, "*" or a
plain alternation of tool names (A|B|C) is unknown, and an unknown matcher
on either side, or a missing --matcher argument, means RUN: a duplicate
fire is safe, a hook fired zero times on a tool is not. An install whose
scope is project or local counts only when its projectPath is this project
(realpath); an unknown scope, or such an install without a projectPath,
does not count, so the hook runs. The guard never skips a fence on a
registry it cannot read. A blocking event (PreToolUse, Stop) never yields,
whatever the registries say: a legacy copy that is registered but crashes
would leave the action unchecked (OP1.a, 2026-10-03). A settings layer that
exists but cannot be read or parsed, is not a JSON object, or carries an
enabledPlugins that is not an object makes the whole answer unknown, so the
hook runs; the managed layer is known only on macOS (MANAGED_PLATFORM). An
old hook counts only when its script exists inside the old root, its command
split as a shell splits it (shlex; an unparsable command means RUN). A
config or project directory, the defaults included, that is not an existing
directory owned by this user, or that a group or other user can write, means
RUN; so does a CLAUDE_PLUGIN_ROOT that is not the root this guard ships in
(<root>/runtime/hooks/hook_guard.py, read from the guard's own path). A
wrapped command that cannot be executed exits 2 with the reason on stderr
for a blocking event, so a fence never disappears silently, and 1 otherwise.

Settings precedence (code.claude.com/docs/en/settings, read 2026-09-30),
lowest to highest: user ~/.claude/settings.json, shared project
.claude/settings.json, project local .claude/settings.local.json, managed
settings. A key set at a higher level wins; a key omitted keeps the lower
value. `--settings` on the command line is not visible to a hook process
and is not read here.

Python 3, standard library only, no network. Runs on 3.9 and 3.13.
"""
import json
import os
import re
import shlex
import stat
import sys
from collections.abc import Mapping

#: The bundle's own plugin name, used for the second-root rule.
SELF = "brother"
#: The one marketplace the bundle ships under: a plugin counts only under its
#: full key <name>@brother, never a same named one from another marketplace.
MARKETPLACE = "brother"
#: Events whose exit 2 blocks the action in Claude Code; the guard never
#: yields on one.
BLOCKING_EVENTS = ("PreToolUse", "Stop")
#: Managed settings, the highest level, on this platform family.
MANAGED_SETTINGS = "/Library/Application Support/ClaudeCode/managed-settings.json"
#: The one platform MANAGED_SETTINGS is the path for; on any other the
#: managed layer is unknown, which is RUN.
MANAGED_PLATFORM = "darwin"
#: The generator passes the hook group's matcher as this argument.
MATCHER_FLAG = "--matcher="
#: matcher_names() answer for a matcher that fires on every tool.
ALL = "ALL"


def trusted_dir(path):
    """Is `path` an existing directory owned by the current user that no
    group or other user can write? Anything else, or any error, is False."""
    if not isinstance(path, str) or not path:
        return False
    try:
        st = os.stat(path)
        return (stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
                and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    except (OSError, ValueError, AttributeError):
        return False


def config_dir(environ=None):
    """CLAUDE_CONFIG_DIR, else ~/.claude; None (RUN) when that is not a
    trusted_dir, so a steered, dangling or shared value never yields."""
    environ = os.environ if environ is None else environ
    if not isinstance(environ, Mapping):
        return None
    cfg = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return cfg if trusted_dir(cfg) else None


def project_dir(environ=None):
    """CLAUDE_PROJECT_DIR, else the working directory; None (RUN) when that
    is not a trusted_dir."""
    environ = os.environ if environ is None else environ
    if not isinstance(environ, Mapping):
        return None
    try:
        project = environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    except OSError:
        return None
    return project if trusted_dir(project) else None


def own_root():
    """The plugin root this guard ships in, read from its own path
    (<root>/runtime/hooks/hook_guard.py, by realpath); None when it lives
    anywhere else, such as a checkout's scripts/."""
    hooks = os.path.dirname(os.path.realpath(__file__))
    runtime = os.path.dirname(hooks)
    if os.path.basename(hooks) != "hooks" or os.path.basename(runtime) != "runtime":
        return None
    return os.path.dirname(runtime)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None  # a RecursionError reaches should_yield's own catch


def _layer_enabled(path):
    """One settings layer's enabledPlugins: {} when the file is absent, None
    (unknown) when it exists but cannot be read or parsed, is not a JSON
    object, or carries an enabledPlugins that is not an object."""
    if not os.path.lexists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(doc, dict):
        return None
    enabled = doc.get("enabledPlugins", {})
    return enabled if isinstance(enabled, dict) else None


def effective_enabled(cfg, project, managed=None):
    """enabledPlugins merged per key, lowest layer first so a higher layer
    overrides. A missing layer contributes nothing; a layer that exists but
    cannot be read, or has the wrong shape, makes the whole answer None
    (unknown: it may disable the old plugin). Without `managed`, the managed
    layer is MANAGED_SETTINGS on MANAGED_PLATFORM and unknown elsewhere."""
    if not isinstance(cfg, str) or not isinstance(project, str):
        return None
    if managed is None:
        if sys.platform != MANAGED_PLATFORM:
            return None  # the managed layer lives at a path not known here
        managed = MANAGED_SETTINGS
    if not isinstance(managed, str):
        return None
    layers = (
        os.path.join(cfg, "settings.json"),
        os.path.join(project, ".claude", "settings.json"),
        os.path.join(project, ".claude", "settings.local.json"),
        managed,
    )
    merged = {}
    for path in layers:
        enabled = _layer_enabled(path)
        if enabled is None:
            return None  # an existing layer that may disable it is unread
        merged.update(enabled)
    return merged


def hook_id(command):
    """(script basename, arguments after the script) for a hook argv, the
    script being its FIRST token ending in .py; None when there is none.
    Flags before the script (python3 -B, env) are not part of the
    identity; arguments after it are (F6)."""
    for i, token in enumerate(command):
        if isinstance(token, str) and token.endswith(".py"):
            return os.path.basename(token), tuple(command[i + 1:])
    return None


#: Alternation of plain tool names, the only matcher shape compared.
_NAMES = re.compile(r"[A-Za-z0-9_]+(\|[A-Za-z0-9_]+)*\Z")


def matcher_names(matcher):
    """ALL for an empty or "*" matcher, the set of tool names for a plain
    alternation, None (unknown) for anything else."""
    if matcher in ("", "*"):
        return ALL
    if isinstance(matcher, str) and _NAMES.match(matcher):
        return frozenset(matcher.split("|"))
    return None


def covers(old, mine):
    """Does matcher `old` fire on every tool matcher `mine` fires on?
    Unknown on either side is False (RUN)."""
    old, mine = matcher_names(old), matcher_names(mine)
    if old is None or mine is None:
        return False
    if old is ALL:
        return True
    return mine is not ALL and mine <= old


def script_under(root, argv):
    """Does the script `argv` names (its first .py token, the one hook_id
    reads) exist as a file inside `root`? ${CLAUDE_PLUGIN_ROOT} reads as
    `root` and a relative path is taken from `root`; a path resolving outside
    `root` is not the old plugin's script."""
    for token in argv:
        if isinstance(token, str) and token.endswith(".py"):
            base = os.path.realpath(root)
            path = os.path.realpath(os.path.join(
                root, token.replace("${CLAUDE_PLUGIN_ROOT}", root)))
            return path.startswith(base + os.sep) and os.path.isfile(path)
    return False


def registers_same_hook(root, event, matcher, hook):
    """Does <root>/hooks/hooks.json register `event`, under a matcher group
    covering `matcher`, with a command whose hook_id is `hook` and whose
    script exists under `root`? The command is split as a shell splits it,
    so a quoted path with a space is one token. Anything unreadable or
    unparsable is False (RUN)."""
    if not isinstance(root, str) or not root or not isinstance(event, str):
        return False
    if not isinstance(hook, tuple):
        return False
    doc = _read_json(os.path.join(root, "hooks", "hooks.json"))
    if not isinstance(doc, dict):
        return False
    groups = doc.get("hooks", {})
    groups = groups.get(event) if isinstance(groups, dict) else None
    if not isinstance(groups, list):
        return False
    for group in groups:
        hooks = group.get("hooks") if isinstance(group, dict) else None
        if not covers(group.get("matcher", "") if isinstance(group, dict)
                      else None, matcher):
            continue
        for handler in hooks if isinstance(hooks, list) else []:
            command = (handler.get("command") if isinstance(handler, dict)
                       else None)
            if not isinstance(command, str):
                continue
            try:
                argv = shlex.split(command)
            except ValueError:
                return False  # a command the shell could not split: RUN
            if hook_id(argv) == hook and script_under(root, argv):
                return True
    return False


def in_scope(entry, project):
    """Does this install load in `project`? user (or absent, the v1 shape)
    everywhere; project and local only where projectPath is this project;
    anything else never (RUN)."""
    scope = entry.get("scope", "user")
    if scope == "user":
        return True
    if scope not in ("project", "local"):
        return False
    where = entry.get("projectPath")
    return (isinstance(where, str) and bool(where)
            and os.path.realpath(where) == os.path.realpath(project))


def active_hook_roots(name, event, matcher, hook, cfg, project):
    """Sorted install roots of plugin `name` under its full key
    <name>@brother (MARKETPLACE) that are enabled by the effective settings
    AND installed AND register the same hook. Empty on any doubt (that is
    the RUN answer), an unknown effective_enabled included."""
    if not all(isinstance(v, str) for v in (name, event, cfg, project)):
        return []
    installed = _read_json(os.path.join(cfg, "plugins", "installed_plugins.json"))
    plugins = installed.get("plugins") if isinstance(installed, dict) else None
    if not isinstance(plugins, dict):
        return []
    enabled = effective_enabled(cfg, project)
    if enabled is None:
        return []
    roots = []
    for key, on in enabled.items():
        if on is not True or not isinstance(key, str):
            continue
        if key != name + "@" + MARKETPLACE:
            continue
        entries = plugins.get(key)
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            root = entry.get("installPath") if isinstance(entry, dict) else None
            if not isinstance(root, str) or not root:
                continue
            if not in_scope(entry, project):
                continue
            if registers_same_hook(root, event, matcher, hook):
                roots.append(os.path.realpath(root))
    return sorted(set(roots))


def should_yield(product, event, matcher, command, plugin_root, cfg, project):
    """True when this invocation must stay quiet. Never on a blocking event,
    never with a config or project directory config_dir or project_dir did
    not trust (None), never with a `plugin_root` that is not the root this
    guard ships in. Never raises: any exception is the RUN answer."""
    try:
        if event in BLOCKING_EVENTS:
            return False  # a fence is never skipped, whatever the registries say
        hook = hook_id(command)
        if hook is None or matcher is None:
            return False
        if not isinstance(cfg, str) or not isinstance(project, str):
            return False
        if not (plugin_root is None or isinstance(plugin_root, str)):
            return False
        if plugin_root:
            mine = own_root()
            if mine is None or os.path.realpath(plugin_root) != mine:
                return False  # a steered or foreign CLAUDE_PLUGIN_ROOT
        if product != SELF and active_hook_roots(product, event, matcher, hook,
                                                 cfg, project):
            return True
        if not plugin_root:
            return False
        others = active_hook_roots(SELF, event, matcher, hook, cfg, project)
        return mine in others and others[0] != mine
    except BaseException:  # any failure inside the decision means RUN
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3:
        print("hook_guard: usage: hook_guard.py <product> <event> "
              "--matcher=<matcher> <command> [args...]", file=sys.stderr)
        return 2
    product, event, command = argv[0], argv[1], argv[2:]
    matcher = None  # absent: unknown, so RUN
    if command[0].startswith(MATCHER_FLAG):
        matcher, command = command[0][len(MATCHER_FLAG):], command[1:]
    if not command:
        print("hook_guard: no command after %s" % MATCHER_FLAG, file=sys.stderr)
        return 2
    if should_yield(product, event, matcher, command, os.environ.get("CLAUDE_PLUGIN_ROOT"),
                    config_dir(), project_dir()):
        return 0
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print("hook_guard: cannot run %s for %s: %s" % (command[0], event, exc),
              file=sys.stderr)
        return 2 if event in BLOCKING_EVENTS else 1


if __name__ == "__main__":
    sys.exit(main())
