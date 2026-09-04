#!/usr/bin/env python3
"""B-10/C7: `sbe doctor`'s hooks-wiring check, and the shipped-vs-installed
comparison it runs.

Moved out of `src/brothersbe/cli.py` (2026-08-22) so the CLI stays plugin-
independent: this module is the one place that names `CLAUDE_PLUGIN_ROOT`
(the placeholder the runtime substitutes into a hook command, and the shape
`tools/test_sbe.py`'s `test_every_hook_command_points_at_a_file_that_exists`
checks the shipped hooks.json against), and `tools/test_sbe_interop.py`'s
`TestCLIFallbackDocumented` asserts that string appears nowhere under
`bin/sbe` or `src/brothersbe/*.py`: the CLI has to work identically whether
or not BrotherSBE is registered as a Claude Code plugin, and hooks-wiring is
inherently a question about the plugin loader, not about the CLI. Hooks-
related probes belong under `tools/` the same way every other hook lives
here; `src/brothersbe/cli.py` imports `hooks_wiring_check` by name and stays
free of the string itself.

Standard library only, matching every other file under tools/.
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: this whole check is a question about the CLAUDE plugin loader, so it
# needs to know which client is running. brother_paths is loaded from beside
# this file (tools/ is not a package) and its absence degrades to "unknown",
# which keeps the pre-C3 behaviour rather than turning a broken install into
# a spurious NO-DATA.
sys.path.insert(0, HERE)
try:
    import brother_paths  # noqa: E402
except ImportError:  # pragma: no cover, exercised only by a broken install
    brother_paths = None

#: The verdict this check returns under Codex, and the reason, quoted from the
#: measurement rather than reasoned about. Codex 0.153.0-alpha.5 DOES have a
#: hooks mechanism (`codex features list` prints "hooks stable true") but
#: hooks delivered BY A PLUGIN were removed from it (the same listing prints
#: "plugin_hooks removed false"), and the canonical plugin validator refuses a
#: manifest that declares them ("plugin.json field `hooks` is not accepted by
#: plugin validation", exit 1). So under Codex there is no installed plugin
#: copy of hooks.json to compare the shipped file against, and a PASS here
#: would be a statement about a Claude install dressed up as a statement about
#: this environment. See docs/codex/HOOKS-MAPPING.md.
CODEX_NO_DATA_DETAIL = (
    "the running client is Codex, where plugin-delivered hooks do not exist "
    "(codex features list: 'plugin_hooks removed', and the canonical plugin "
    "validator refuses a manifest carrying `hooks`), so there is no installed "
    "hooks.json for this check to compare the shipped file against; "
    "unexamined is never reported as clean. Wire them with "
    "`python3 scripts/codex_hooks_install.py --codex-home <dir> --trust`. "
    "See docs/codex/HOOKS-MAPPING.md")


def running_client():
    """"claude", "codex", or "" when nothing identifies the host."""
    if brother_paths is None:
        return ""
    return brother_paths.client()

#: The same citation shape `tools/test_sbe.py`'s
#: `test_every_hook_command_points_at_a_file_that_exists` checks the shipped
#: hooks.json against, and the shape the runtime itself substitutes
#: ${CLAUDE_PLUGIN_ROOT} into. Reused here so a script path is judged missing
#: or present by exactly one rule in this project, not two that could drift.
_PLUGIN_ROOT_CITATION = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)")


def _load_hooks_json(path):
    """A dict with named keys "data" and "problem": {"data": ..., "problem":
    None} on a clean parse, {"data": None, "problem": ...} when `path` is
    missing or does not parse as JSON. Never a bare 2-tuple: a literal (x, y)
    return reads to the honesty meta-test as an unregistered (verdict,
    evidence) pair it cannot prove is never PASS, and this helper has no
    registry entry to prove it in. Never raises: a broken hooks.json is a
    doctor FINDING, not a crash of the doctor run that would report it."""
    if not os.path.exists(path):
        return {"data": None, "problem": "no file at %s" % path}
    try:
        with io.open(path, encoding="utf-8") as fh:
            return {"data": json.load(fh), "problem": None}
    except ValueError as exc:
        return {"data": None, "problem": "%s does not parse: %s" % (path, exc)}


def _hooks_json_wiring(data):
    """A dict with named keys "events" ({event: {matcher-or-"": (command, ...)}})
    and "problems" (the field-level problems found while building it: a
    declared-but-blank matcher, a declared-but-blank command). Never a bare
    2-tuple: a literal (x, y) return reads to the honesty meta-test as an
    unregistered (verdict, evidence) pair it cannot prove is never PASS, and
    this helper has no registry entry to prove it in. `matcher` is absent on
    several real events (SessionStart, SessionEnd, PreCompact, Stop all wire
    unconditionally today), so the "" key stands for "no matcher declared",
    never for a blank one.

    # ponytail: two blocks under one event can share the same matcher (this
    # repo's own PreToolUse does: the authority hook and the fence hook both
    # match Edit|Write|...), so commands are ACCUMULATED per matcher across
    # blocks in encounter order rather than keyed one block per matcher. That
    # is enough to catch a dropped or changed command; telling a reordering of
    # two same-matcher blocks apart from a real change would need a richer
    # key, add it if that distinction is ever actually needed.
    """
    problems = []
    events = {}
    for event, blocks in (data or {}).get("hooks", {}).items():
        by_matcher = {}
        for block in blocks:
            matcher = block.get("matcher", "")
            if "matcher" in block and not matcher.strip():
                problems.append("%s declares a matcher field that is blank" % event)
            commands = []
            for hook in block.get("hooks", []):
                command = hook.get("command", "")
                if not command.strip():
                    label = ("matcher %r" % matcher) if matcher else "hook block"
                    problems.append("%s's %s has a hollow command" % (event, label))
                commands.append(command)
            by_matcher.setdefault(matcher, []).extend(commands)
        events[event] = dict((m, tuple(c)) for m, c in by_matcher.items())
    return {"events": events, "problems": problems}


def _hooks_json_missing_scripts(data, install_root):
    """Every ${CLAUDE_PLUGIN_ROOT}-cited script a command references that does
    not exist under `install_root`, resolved the same way the runtime
    substitutes CLAUDE_PLUGIN_ROOT and the same way
    test_every_hook_command_points_at_a_file_that_exists checks the shipped
    file, applied here to whichever file (shipped or installed) is in hand."""
    missing = []
    for event, blocks in (data or {}).get("hooks", {}).items():
        for block in blocks:
            for hook in block.get("hooks", []):
                for cited in _PLUGIN_ROOT_CITATION.findall(hook.get("command", "")):
                    if not os.path.exists(os.path.join(install_root, cited)):
                        missing.append("%s: %s" % (event, cited))
    return missing


def _installed_hooks_json_path(current_version):
    """Where to audit THIS machine's live wiring from: `SBE_HOOKS_JSON` when
    set (an explicit override so a test or an estate can point doctor at any
    copy without a real marketplace install), else the plugin-cache install
    this machine's own harness recorded in `installed_plugins.json` FOR THE
    VERSION this checkout currently ships (`current_version`, the caller's
    own `version()`; this module never imports `brothersbe` itself, see the
    module docstring). A dict with named keys "path", "source" and "record"
    ("record" is the installed_plugins.json-shaped file actually consulted,
    None when SBE_HOOKS_JSON short-circuited that lookup entirely, present
    even when nothing matched in it, so a caller reporting "not found" can
    still say honestly WHERE it looked rather than assume the machine
    default). {"path": None, "source": None} when neither is discoverable
    -- a state this check has to name rather than pass over, per
    `hooks_wiring_check` below. Never a bare 2-tuple: a literal (x, y)
    return reads to the honesty meta-test as an unregistered (verdict,
    evidence) pair it cannot prove is never PASS, and this helper has no
    registry entry to prove it in.

    `SBE_INSTALLED_PLUGINS_JSON`, when set, replaces the RECORD this reads to
    decide whether an installed copy exists at all (normally
    `~/.claude/plugins/installed_plugins.json`), so a caller can hand this
    function a fixture with a controlled set of entries, or none, without
    touching the SBE_HOOKS_JSON override above (which instead replaces the
    FINAL hooks.json this function would have returned, and so cannot express
    "no installed copy was found," only "read this file as if it were the
    installed one"). This exists because the book replay harness needs the
    "no installed copy" branch to be a property of the fixture it hands in,
    not of whatever the replaying machine's own install cache happens to
    hold at the moment: see `docs/plan/FINDING-book-replay-version-coupling-2026-08-28.md`
    in the Brother umbrella repo for the defect this closes.

    Matched by `current_version`, not merely by plugin name, on purpose: this
    machine's own cache holds installs of brothersbe releases this checkout
    has since moved past (found live: cache at 3.2.0 while this checkout
    shipped 3.2.1, the marketplace install cache directory literally being
    version-named). Comparing today's shipped wiring against an install of an
    OLDER release would flag every routine release lag as if it were the
    tampering B-10 exists to catch, which is a different problem doctor
    already has a check for (see `plugin-manifest` above) and would otherwise
    make this check FAIL on every ordinary dev machine running ahead of its
    own installed cache. Same-version match keeps the comparison meaningful:
    two copies of the SAME release whose wiring still differs is tampering or
    corruption, not lag.
    """
    override = os.environ.get("SBE_HOOKS_JSON")
    if override:
        return {"path": override, "source": "SBE_HOOKS_JSON", "record": None}
    record_path = os.environ.get("SBE_INSTALLED_PLUGINS_JSON") or os.path.expanduser(
        os.path.join("~", ".claude", "plugins", "installed_plugins.json"))
    loaded = _load_hooks_json(record_path)
    data, problem = loaded["data"], loaded["problem"]
    if problem:
        return {"path": None, "source": None, "record": record_path}
    for key, entries in data.get("plugins", {}).items():
        if key.split("@")[0] != "brothersbe":
            continue
        for entry in entries:
            install_path = entry.get("installPath")
            if install_path and entry.get("version") == current_version:
                return {"path": os.path.join(install_path, "hooks", "hooks.json"),
                        "source": record_path, "record": record_path}
    return {"path": None, "source": None, "record": record_path}


def _command_signature(command):
    """The part of a hook command that survives plugin-root expansion.

    A shipped command cites `${CLAUDE_PLUGIN_ROOT}/tools/x.py`; the same
    command inside a Codex hooks file carries the expanded absolute path,
    because a user-scope hooks file is not inside a plugin and nothing would
    substitute the placeholder. Everything from `tools/` onward is identical
    on both sides, so that is what the two are compared on. A command with no
    `tools/` segment compares whole, which is the conservative reading.
    """
    match = re.search(r"tools/.*$", command or "")
    return match.group(0) if match else (command or "").strip()


def _codex_hooks_json_path():
    """Codex's own user-scope hooks file for this run, or None when this
    build cannot resolve a config directory at all."""
    if brother_paths is None:
        return None
    return brother_paths.config_path("hooks.json")


def _codex_wiring_verdict(shipped_path):
    """The hooks-wiring verdict under a Codex client.

    NO-DATA when Codex's own hooks file is absent (nothing is wired, and
    unexamined is never clean), FAIL when it is unreadable or is missing a
    control the shipped file declares, PASS when every shipped event, matcher
    and command signature is present in it. Extra hooks in the Codex file are
    not a failure: that file is shared, and BrotherMode's own hooks live in
    it beside these.
    """
    codex_path = _codex_hooks_json_path()
    if codex_path is None:
        return ("hooks-wiring", "NO-DATA", CODEX_NO_DATA_DETAIL)
    if not os.path.exists(codex_path):
        return ("hooks-wiring", "NO-DATA",
                "%s; looked for a wired Codex hooks file at %s and found none"
                % (CODEX_NO_DATA_DETAIL, codex_path))

    loaded_shipped = _load_hooks_json(shipped_path)
    if loaded_shipped["problem"]:
        return ("hooks-wiring", "FAIL",
                "shipped hooks.json: %s" % loaded_shipped["problem"])
    loaded_codex = _load_hooks_json(codex_path)
    if loaded_codex["problem"]:
        return ("hooks-wiring", "FAIL",
                "Codex hooks file: %s" % loaded_codex["problem"])

    shipped_events = _hooks_json_wiring(loaded_shipped["data"])["events"]
    codex_events = _hooks_json_wiring(loaded_codex["data"])["events"]
    problems = []
    wired = 0
    for event, matchers in shipped_events.items():
        codex_matchers = codex_events.get(event)
        if codex_matchers is None:
            problems.append("%s lacks the %s event entirely, which the shipped "
                            "file declares" % (codex_path, event))
            continue
        for matcher, commands in matchers.items():
            label = ("matcher '%s'" % matcher) if matcher else "hook block"
            present = [_command_signature(c)
                       for c in codex_matchers.get(matcher, ())]
            for command in commands:
                if _command_signature(command) in present:
                    wired += 1
                else:
                    problems.append("%s's %s %s does not carry %s"
                                    % (codex_path, event, label, command))
    if problems:
        return ("hooks-wiring", "FAIL", "; ".join(problems))
    return ("hooks-wiring", "PASS",
            "the running client is Codex and its own hooks file at %s carries "
            "all %d command(s) the shipped wiring at %s declares"
            % (codex_path, wired, shipped_path))


def hooks_wiring_check(root, current_version):
    """B-10/C7: `doctor` never inspected hooks/hooks.json at all, so a
    tampered or stripped hook (autosave and SessionStart silently not firing)
    was invisible to the one command a tester runs to ask "is this
    environment sound". This reads the shipped file (the source of truth at
    `<root>/hooks/hooks.json`) and, when a live installed copy can be found,
    compares the two field by field: a missing entry, a changed matcher or a
    changed command in the install is named, not just flagged. Absence of a
    discoverable install is itself stated, per this project's own NO-DATA
    law: unexamined is never reported as clean.

    `current_version` is the caller's own `version()` (a plain string), taken
    as a parameter rather than imported: see the module docstring for why
    this file never imports `brothersbe`.
    """
    shipped_path = os.path.join(root, "hooks", "hooks.json")

    # C3: the guard, ahead of every other rung. Codex delivers no hooks with a
    # plugin install, so the Claude comparison below has nothing to compare;
    # but Codex DOES read a user-scope hooks file, and when one is wired this
    # check has a real subject again. Presence of that file decides which:
    # wired reads PASS or FAIL on its contents, absent reads NO-DATA. See
    # CODEX_NO_DATA_DETAIL above and docs/codex/HOOKS-MAPPING.md.
    if running_client() == "codex":
        return _codex_wiring_verdict(shipped_path)
    loaded_shipped = _load_hooks_json(shipped_path)
    shipped, shipped_problem = loaded_shipped["data"], loaded_shipped["problem"]
    if shipped_problem:
        return ("hooks-wiring", "FAIL", "shipped hooks.json: %s" % shipped_problem)

    shipped_wiring = _hooks_json_wiring(shipped)
    shipped_events, field_problems = shipped_wiring["events"], shipped_wiring["problems"]
    problems = ["shipped hooks.json %s" % p for p in field_problems]
    problems.extend("shipped hooks.json cites a script that does not exist: %s" % p
                     for p in _hooks_json_missing_scripts(shipped, root))

    installed_location = _installed_hooks_json_path(current_version)
    installed_path, source = installed_location["path"], installed_location["source"]
    if installed_path is None:
        record = installed_location.get("record") or os.path.expanduser(
            os.path.join("~", ".claude", "plugins", "installed_plugins.json"))
        detail = ("examined only the shipped file at %s: no installed copy is "
                  "discoverable (no brothersbe %s entry in %s, and SBE_HOOKS_JSON "
                  "is unset)" % (shipped_path, current_version, record))
        if problems:
            return ("hooks-wiring", "FAIL", "; ".join(problems) + ". " + detail)
        return ("hooks-wiring", "NO-DATA", detail)

    loaded_installed = _load_hooks_json(installed_path)
    installed, installed_problem = loaded_installed["data"], loaded_installed["problem"]
    if installed_problem:
        problems.append("installed copy declared via %s: %s" % (source, installed_problem))
        return ("hooks-wiring", "FAIL", "; ".join(problems))

    install_root = os.path.dirname(os.path.dirname(os.path.abspath(installed_path)))
    installed_wiring = _hooks_json_wiring(installed)
    installed_events = installed_wiring["events"]
    installed_field_problems = installed_wiring["problems"]
    problems.extend("installed copy at %s %s" % (installed_path, p)
                    for p in installed_field_problems)
    problems.extend("installed copy at %s cites a script that does not exist: %s"
                    % (installed_path, p)
                    for p in _hooks_json_missing_scripts(installed, install_root))

    for event, matchers in shipped_events.items():
        installed_matchers = installed_events.get(event)
        if installed_matchers is None:
            problems.append("installed copy at %s lacks the %s event entirely, which the "
                            "shipped file declares" % (installed_path, event))
            continue
        for matcher, commands in matchers.items():
            label = ("matcher '%s'" % matcher) if matcher else "hook block"
            if matcher not in installed_matchers:
                problems.append("installed copy at %s lacks the %s %s the shipped file "
                                "declares" % (installed_path, event, label))
            elif installed_matchers[matcher] != commands:
                problems.append(
                    "installed copy at %s's %s %s command changed from %s to %s"
                    % (installed_path, event, label, list(commands),
                       list(installed_matchers[matcher])))

    if problems:
        return ("hooks-wiring", "FAIL", "; ".join(problems))
    return ("hooks-wiring", "PASS",
            "installed copy at %s (found via %s) matches the shipped wiring at %s"
            % (installed_path, source, shipped_path))
