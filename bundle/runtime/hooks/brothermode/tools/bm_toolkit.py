#!/usr/bin/env python3
"""Enumerate what capability is installed on this machine, mechanically.

WHY THIS EXISTS
  BrotherMode's Toolkit is a capability BROKER, not a marketplace (see
  docs/plan/TOOLKIT-PLAN-2026-08-12.md section 3, function F1). No command
  today answers "what capability is installed on this machine" across
  plugins, skills, hooks, settings layers and MCP servers at once. Claude
  Code enumerates plugins one at a time (`claude plugin details`) but never
  aggregates across them, and never covers skills, settings layers or CLIs.
  This tool is that aggregation, read straight from artifacts: plugin
  manifests, hooks.json files, settings.json, the skills directory, and
  .claude.json. What a tool registers is observable without running it.

THE CONTRACT
  Five verbs. `inventory` (human readable, the default) and `json` (one
  JSON object, schema 1, for later loops such as conflict detection to
  consume rather than re-parse). See the top-level JSON shape in the plan
  brief; `build_inventory()` below is the single place that shape is built.
  `conflicts` (human readable) reads that same inventory plus a table of
  conflict classes and emits verdicts, per docs/plan/TOOLKIT-PLAN-2026-08-12.md
  section F2. `capabilities` (human readable) reads that same inventory and
  emits capability records, per section F3 and section 4 below. `route`
  (human readable) reads that same inventory plus a routing table and names
  the capability a task class should use, per section F3 below.

ROUTING (F3, section 3 of the plan)
  `route <task-class>` answers "which existing capability should do this
  task", never which team member should do it (that is delegation, a
  separate ladder, and routing composes with it rather than replacing it).
  The table is DATA, not prose: ROUTES below is the module constant the
  founder's own six examples are seeded into (decision 34's pattern,
  identical in shape to DEFAULT_CONFLICT_CLASSES): TDD to Superpowers,
  product strategy to Compound Engineering or BMAD, long phase planning to
  a GSD-class capability, security check to Security Guidance, PR review to
  Anthropic Code Review, historical recall to a claude-mem-class capability.
  tools/toolkit_routes.json next to this file ships as a founder-editable
  copy of that same table; present and valid it REPLACES the defaults
  entirely, present and malformed it is refused BY NAME and the run falls
  back to the shipped defaults, absent it is silence, never an error, the
  same three-way contract load_conflict_classes already uses.

  A task class carries one or more routes, tried in the order listed. Each
  route names a `capability`, the `reason` it was chosen, its
  `flip_condition` (what would change the choice), a `degrade_path` (what
  to do when the capability is absent from THIS machine), and `enabled`.
  A route with `enabled: false` is never silently hidden: the command
  prints it under its own DISABLED status carrying its `disabled_reason`,
  same visibility discipline as a HAND-ASSERTED capability record. The
  BMAD route ships DISABLED with reason "on-disk footprint unconfirmed"
  (section 5b of the plan) until somebody confirms it against a real
  repository tree; that is a decision recorded in data, not commented out.

  Presence is checked against `build_inventory()`, the exact same artifact
  read every other verb uses, via each route's `match` ({"kind": "plugin"
  or "skill" or "mcp_server", "name": "..."}); nothing here runs the
  capability or asks it anything. A capability present in the inventory
  resolves the route and prints its reason. A capability absent from the
  inventory prints its `degrade_path` instead of guessing a substitute.
  When the whole inventory could not be read, `route` reports NO-DATA at
  exit 2, the same as every other verb: an absent capability is a real
  answer, an unreadable inventory is not one. A task class this table does
  not carry is refused BY NAME, listing every known class, never guessed
  and never silently routed to the nearest string.

CAPABILITY RECORDS (F3, section 4 of the plan)
  A capability record's reach is DECLARED, never measured by running
  anything: it is read from the same artifacts `build_inventory()` already
  parses (a plugin manifest, its hooks.json, a skill's hooks.json, the
  mcpServers key of .claude.json). Every DERIVED record names the exact
  artifact paths it was built from in its `source_files` list, which is
  never empty: the capability's own directory is always the first entry,
  because a directory existing is itself an artifact, and the manifest or
  hooks.json paths are appended when they were actually read.

  A record can also be HAND-ASSERTED: typed by a human into an optional
  override file (tools/toolkit_capability_overrides.json next to this
  file, or an explicit --overrides PATH) for a capability this inventory
  cannot derive from artifacts on its own, such as an external CLI. A
  hand-asserted record carries `hand_asserted: true`, an empty
  `source_files` (nothing was read to build it), and is rendered under its
  own visibly separate heading with a [HAND-ASSERTED] marker on every line,
  in `json` output and in the human-readable `capabilities` report alike,
  so it can never be mistaken for something the inventory derived. The
  override file is optional the same way the conflict classes override is:
  absent is silence, not an error; present and malformed is refused BY
  NAME (path and reason printed) and the run falls back to zero overrides,
  never classless-versus-crash, matching decision 34's shape for conflict
  classes.

  No persistence: a capability record is built fresh from the inventory at
  read time, on every run, and is never written to the store. The plan's
  F3 done-check ("a record is DERIVED from artifacts; a hand-asserted
  record is visibly marked") holds without a schema change, because the
  inventory this file already reads IS the artifact evidence; storing a
  second copy would just be a cache with its own staleness to manage.

CONFLICT CLASSES (F2)
  Ten classes, defined once in DEFAULT_CONFLICT_CLASSES below (decision 34:
  the shipped defaults live IN this file as a module constant, so a
  packaged install is never classless). tools/toolkit_conflict_classes.json
  next to this file is an OPTIONAL override: present and valid, it replaces
  the defaults entirely; present and malformed, it is refused BY NAME (the
  path and the reason are printed) and the defaults are used instead;
  absent, that is not an error, only silence, same as every other optional
  surface in this file.

  Each class carries a `detect` description and a `threshold`. A class this
  module knows how to check mechanically against the inventory fires when
  its measured count reaches `threshold`, at the severity the class (or its
  override) declares: info, warn, or fail, and nothing else. A class this
  module has no mechanical detector for reports NO-DATA, named, rather than
  going silent: silence would read as "checked, found nothing," which is
  not true of a surface this file never looked at.

  Every surface that cannot be read (permission error, not-a-directory,
  invalid JSON) is appended to `unreadable` with its path and reason; it
  never crashes the run and never silently vanishes. Absence is NOT
  unreadable: a marketplaces cache directory, a skills directory, or an
  enabledPlugins/mcpServers key that simply does not exist reports zero
  items, not an error, because "nothing here" is a real, readable answer.

  If EVERY one of the four core surfaces (marketplaces cache, user
  settings.json, skills directory, .claude.json) could not be read, the
  tool prints a line starting with the literal "NO-DATA:" and exits 2.
  Otherwise: 0 read something, 2 could not tell at all. There is no exit 1
  in this verb; nothing here is a failure verdict, only an inventory.

EFFECT CLASS: pure_read
  This tool writes NOT ONE BYTE anywhere, ever: no file writes, no
  directories created, no store touched. It never constructs a writable
  bm_store.Store (doing so is itself a write). bm_store is loaded only to
  borrow its resolve_root() for best-effort --root resolution, and that
  call is wrapped so a failure there degrades to os.getcwd(), never a
  crash and never a write.

BE SILENT-SAFE
  Any unexpected exception anywhere in this module is caught by `main` and
  reported as NO-DATA at exit 2, carrying the exception's type and
  message, rather than raising.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_toolkit.py inventory    [--home PATH] [--root PATH]
  python3 tools/bm_toolkit.py json         [--home PATH] [--root PATH]
  python3 tools/bm_toolkit.py conflicts    [--home PATH] [--root PATH] [--classes PATH]
  python3 tools/bm_toolkit.py capabilities [--home PATH] [--root PATH] [--overrides PATH]
  python3 tools/bm_toolkit.py route TASK-CLASS [--home PATH] [--root PATH] [--routes PATH]

`inventory` is also the default when no verb is given. `--classes` overrides
the conflict class table for `conflicts` only; `--overrides` names the
hand-asserted capability record file for `capabilities` only; `--routes`
overrides the routing table for `route` only; every other verb ignores
whichever of the three it is not given. `route` requires exactly one
positional argument, the task class, given before any flags.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_VERBS = ("inventory", "json", "conflicts", "capabilities", "route")


# ---------------------------------------------------------------------------
# Root resolution (best effort only; see module docstring). Mirrors the
# --root pattern in tools/bm_progress_check.py and tools/bm_idle.py, but a
# failure here never aborts the run: the project root only feeds the
# project/project-local settings_layers rows, never the home-anchored
# surfaces that make up the bulk of the inventory.
# ---------------------------------------------------------------------------

def _load_bm_store():
    """Load bm_store.py by PATH, the same shape the sibling tools use: this
    tool is invoked with an arbitrary cwd, and a plain `import bm_store`
    would depend on whichever sys.path the caller happened to have. Never
    raises: an unimportable module degrades to (None, reason)."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_toolkit", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_store.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def resolve_home(explicit_home):
    """Return (home, None) or (None, reason). `explicit_home` (--home)
    always wins; otherwise the real user home. Unlike root, a bad home is
    fatal: nearly every surface this tool reads lives under it."""
    raw = explicit_home if explicit_home else "~"
    candidate = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(candidate):
        return None, "no such directory: %s" % candidate
    return candidate, None


def resolve_root(explicit_root):
    """Return a best-effort project root, always a string, never raising.
    An explicit --root is used as given (even if it turns out not to
    exist; the settings_layers rows built from it will just report
    exists=False, which is itself a true answer). Otherwise bm_store's
    resolve_root() is borrowed; any failure there falls back to cwd."""
    if explicit_root:
        return os.path.realpath(os.path.expanduser(explicit_root))
    mod, _err = _load_bm_store()
    if mod is not None:
        try:
            root, _source = mod.resolve_root()
            if root:
                return root
        except Exception:  # sbe: allow-silent documented: any failure in bm_store's resolve_root falls back to cwd on the next line
            pass
    return os.getcwd()


# ---------------------------------------------------------------------------
# Pure filesystem helpers shared by every surface below.
# ---------------------------------------------------------------------------

def _is_safe_child(home_real, path):
    """True if the symlink at `path` resolves to somewhere inside
    `home_real`. Used to refuse walking a symlink that escapes home."""
    try:
        target_real = os.path.realpath(path)
    except OSError:
        return False
    return target_real == home_real or target_real.startswith(home_real + os.sep)


def _list_dir_entries(dir_path, home_real, unreadable):
    """Return the sorted list of (name, full_path) directory children of
    `dir_path`, or None if `dir_path` itself could not be listed (recorded
    into `unreadable`). A symlinked child that escapes home_real is
    skipped and recorded rather than followed. Non-directory children are
    silently skipped: this walks capability directories, not files."""
    try:
        names = sorted(os.listdir(dir_path))
    except OSError as exc:
        unreadable.append({"path": dir_path,
                            "reason": "%s: %s" % (type(exc).__name__, exc)})
        return None
    result = []
    for name in names:
        full = os.path.join(dir_path, name)
        if os.path.islink(full) and not _is_safe_child(home_real, full):
            unreadable.append({"path": full,
                                "reason": "symlink escapes home directory, skipped"})
            continue
        if os.path.isdir(full):
            result.append((name, full))
    return result


def _safe_listdir(dir_path, home_real, unreadable):
    """(entries, ok). entries is always a list, never None. A directory
    that simply does not exist is a valid empty answer (ok=True, zero
    items): absence is not unreadable. ok is False only when the path
    exists but could not be listed (permission error, not a directory)."""
    if not os.path.exists(dir_path):
        return [], True
    entries = _list_dir_entries(dir_path, home_real, unreadable)
    if entries is None:
        return [], False
    return entries, True


def _describe_unreadable_file(path):
    """Reason string for a path that was expected to be a readable file
    but is not: distinguishes "never existed" from "exists as something
    else" (a directory, most commonly), so the unreadable entry is honest
    about what was actually found on disk."""
    if not os.path.exists(path):
        return "missing file"
    return "not a regular file"


def _read_json_file(path):
    """(data, None) or (None, reason). The only place this module opens a
    JSON file; every surface below goes through here."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle), None
    except FileNotFoundError:  # sbe: allow-silent already returns a diagnostic (None, reason) tuple, not a bare None; nothing is hidden from the caller
        return None, "missing file"
    except (OSError, ValueError) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _parse_hook_events(hooks_path, unreadable):
    """The SET (sorted list) of capitalised event names a hooks.json file
    registers, or [] if the file is absent (not an error: hooks are
    optional). The top level is either the event map directly, or an
    object with a "hooks" key holding it; only capitalised keys count as
    event names, per the plan brief's exact list (SessionStart,
    PreToolUse, and so on). A malformed file is recorded into
    `unreadable` and treated as zero events, never a crash."""
    if not os.path.isfile(hooks_path):
        return []
    data, err = _read_json_file(hooks_path)
    if err:
        unreadable.append({"path": hooks_path, "reason": err})
        return []
    if not isinstance(data, dict):
        unreadable.append({"path": hooks_path,
                            "reason": "hooks.json root is not a JSON object"})
        return []
    event_map = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    events = sorted(
        key for key in event_map.keys()
        if isinstance(key, str) and key[:1].isupper()
    )
    return events


# ---------------------------------------------------------------------------
# The surfaces. Each returns its slice of the inventory plus appends any
# read failures to the shared `unreadable` list. None of these raise: a
# failure inside one surface degrades that surface, never the whole run.
# ---------------------------------------------------------------------------

def _collect_plugins(home, home_real, unreadable):
    """(marketplaces, plugins, ok). ok is False only if the marketplaces
    cache directory itself exists but could not be listed."""
    cache_dir = os.path.join(home, ".claude", "plugins", "cache")
    marketplace_entries, ok = _safe_listdir(cache_dir, home_real, unreadable)

    marketplaces = []
    plugins = []
    for mkt_name, mkt_path in marketplace_entries:
        plugin_entries, _ok = _safe_listdir(mkt_path, home_real, unreadable)
        marketplaces.append({"name": mkt_name, "plugin_count": len(plugin_entries)})

        for plugin_name, plugin_path in plugin_entries:
            version_entries, _ok = _safe_listdir(plugin_path, home_real, unreadable)
            for version_name, version_path in version_entries:
                manifest_path = os.path.join(
                    version_path, ".claude-plugin", "plugin.json")
                manifest_present = os.path.isfile(manifest_path)
                manifest_data = None
                if manifest_present:
                    manifest_data, m_err = _read_json_file(manifest_path)
                    if m_err:
                        unreadable.append({"path": manifest_path, "reason": m_err})
                    if not isinstance(manifest_data, dict):
                        manifest_data = None

                name = (manifest_data or {}).get("name") or plugin_name
                version = (manifest_data or {}).get("version") or version_name
                description = (manifest_data or {}).get("description") \
                    if manifest_data else None
                hooks_path = os.path.join(version_path, "hooks", "hooks.json")
                hook_events = _parse_hook_events(hooks_path, unreadable)

                plugins.append({
                    "name": name,
                    "marketplace": mkt_name,
                    "version": version,
                    "path": version_path,
                    "enabled": None,  # filled in by _apply_enabled() below
                    "hook_events": hook_events,
                    "manifest_present": manifest_present,
                    "description": description,
                })
    return marketplaces, plugins, ok


def _collect_skills(home, home_real, unreadable):
    """(skills, ok). ok is False only if the skills directory itself
    exists but could not be listed."""
    skills_dir = os.path.join(home, ".claude", "skills")
    entries, ok = _safe_listdir(skills_dir, home_real, unreadable)
    skills = []
    for skill_name, skill_path in entries:
        has_skill_md = os.path.isfile(os.path.join(skill_path, "SKILL.md"))
        hooks_path = os.path.join(skill_path, "hooks", "hooks.json")
        hook_events = _parse_hook_events(hooks_path, unreadable)
        skills.append({
            "name": skill_name,
            "path": skill_path,
            "has_skill_md": has_skill_md,
            "hook_events": hook_events,
        })
    return skills, ok


def _collect_mcp_servers(home, unreadable):
    """(mcp_servers, ok). Only `mcpServers`' KEYS are kept; the rest of
    .claude.json is discarded immediately, since that file is large and
    carries unrelated keys (module docstring, hard constraint). A missing
    or unparsable file is unreadable (ok=False); a valid file that simply
    lacks the mcpServers key is zero servers, not an error."""
    path = os.path.join(home, ".claude.json")
    if not os.path.isfile(path):
        unreadable.append({"path": path,
                            "reason": _describe_unreadable_file(path)})
        return [], False
    data, err = _read_json_file(path)
    if err:
        unreadable.append({"path": path, "reason": err})
        return [], False
    servers_map = data.get("mcpServers") if isinstance(data, dict) else None
    data = None  # drop the rest of the parsed file now that the key is taken
    if isinstance(servers_map, dict):
        return [{"name": key} for key in sorted(servers_map.keys())], True
    return [], True


def _load_user_settings(home, unreadable):
    """(settings_data_or_None, ok). settings_data is None whenever the
    file could not be read at all (missing or invalid), which is exactly
    the condition under which plugin `enabled` must report null rather
    than false, per the contract."""
    path = os.path.join(home, ".claude", "settings.json")
    if not os.path.isfile(path):
        unreadable.append({"path": path,
                            "reason": _describe_unreadable_file(path)})
        return None, False
    data, err = _read_json_file(path)
    if err:
        unreadable.append({"path": path, "reason": err})
        return None, False
    return data, True


def _apply_enabled(plugins, settings_data):
    """Fill each plugin's "enabled" field in place. null (None) means
    settings.json could not be read at all (could-not-tell); false means
    it WAS read and the plugin's "<name>@<marketplace>" key is simply
    absent from enabledPlugins (a fact). These two are never conflated."""
    if settings_data is None:
        for plugin in plugins:
            plugin["enabled"] = None
        return
    enabled_map = settings_data.get("enabledPlugins")
    if not isinstance(enabled_map, dict):
        enabled_map = {}
    for plugin in plugins:
        key = "%s@%s" % (plugin["name"], plugin["marketplace"])
        plugin["enabled"] = bool(enabled_map.get(key, False))


def _user_hook_commands(settings_data):
    """{event: count of hook commands}, read from user settings.json's
    "hooks" key, shape {event: [ {..., "hooks": [ {"command": ...}, ...] },
    ...]}. Empty dict if settings_data is None or carries no hooks key."""
    counts = {}
    if not isinstance(settings_data, dict):
        return counts
    hooks_key = settings_data.get("hooks")
    if not isinstance(hooks_key, dict):
        return counts
    for event, matchers in hooks_key.items():
        if not isinstance(matchers, list):
            continue
        count = 0
        for matcher in matchers:
            if isinstance(matcher, dict) and isinstance(matcher.get("hooks"), list):
                count += len(matcher["hooks"])
        counts[event] = count
    return counts


def _settings_layers(home, root):
    user_path = os.path.join(home, ".claude", "settings.json")
    project_path = os.path.join(root, ".claude", "settings.json")
    project_local_path = os.path.join(root, ".claude", "settings.local.json")
    return [
        {"path": user_path, "kind": "user", "exists": os.path.isfile(user_path)},
        {"path": project_path, "kind": "project",
         "exists": os.path.isfile(project_path)},
        {"path": project_local_path, "kind": "project-local",
         "exists": os.path.isfile(project_local_path)},
    ]


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------

def build_inventory(home, root):
    """(data, overall_ok). `data` always has every documented top-level
    key, even when overall_ok is False, so a caller can still inspect
    `unreadable` for the reasons. overall_ok is False only when all four
    core surfaces (marketplaces cache, user settings.json, skills
    directory, .claude.json) could not be read; a missing-but-legitimately-
    empty surface never counts against it."""
    home_real = os.path.realpath(home)
    unreadable = []

    marketplaces, plugins, ok_marketplaces = _collect_plugins(
        home, home_real, unreadable)
    skills, ok_skills = _collect_skills(home, home_real, unreadable)
    mcp_servers, ok_mcp = _collect_mcp_servers(home, unreadable)
    settings_data, ok_settings = _load_user_settings(home, unreadable)

    _apply_enabled(plugins, settings_data)
    user_hook_commands = _user_hook_commands(settings_data)
    settings_layers = _settings_layers(home, root)

    data = {
        "schema": 1,
        "home": home,
        "root": root,
        "marketplaces": marketplaces,
        "plugins": plugins,
        "skills": skills,
        "mcp_servers": mcp_servers,
        "settings_layers": settings_layers,
        "user_hook_commands": user_hook_commands,
        "unreadable": unreadable,
    }
    overall_ok = ok_marketplaces or ok_settings or ok_skills or ok_mcp
    return data, overall_ok


# ---------------------------------------------------------------------------
# Human readable rendering for the `inventory` verb.
# ---------------------------------------------------------------------------

def render_inventory(data):
    lines = []

    marketplaces = data["marketplaces"]
    lines.append("MARKETPLACES (%d)" % len(marketplaces))
    for mkt in marketplaces:
        lines.append("  %s (%d plugins)" % (mkt["name"], mkt["plugin_count"]))

    plugins = data["plugins"]
    lines.append("")
    lines.append("PLUGINS (%d)" % len(plugins))
    for plugin in plugins:
        if plugin["enabled"] is True:
            status = "ENABLED"
        elif plugin["enabled"] is False:
            status = "NOT-ENABLED"
        else:
            status = "ENABLED-UNKNOWN"
        events = ",".join(plugin["hook_events"]) if plugin["hook_events"] else "none"
        lines.append("  %s @ %s v%s [%s] hooks: %s" % (
            plugin["name"], plugin["marketplace"], plugin["version"],
            status, events))

    skills = data["skills"]
    lines.append("")
    lines.append("SKILLS (%d)" % len(skills))
    for skill in skills:
        md = "has SKILL.md" if skill["has_skill_md"] else "no SKILL.md"
        events = ",".join(skill["hook_events"]) if skill["hook_events"] else "none"
        lines.append("  %s [%s] hooks: %s" % (skill["name"], md, events))

    mcp_servers = data["mcp_servers"]
    lines.append("")
    lines.append("MCP SERVERS (%d)" % len(mcp_servers))
    for server in mcp_servers:
        lines.append("  %s" % server["name"])

    lines.append("")
    lines.append("SETTINGS LAYERS")
    for layer in data["settings_layers"]:
        state = "present" if layer["exists"] else "absent"
        lines.append("  %s: %s [%s]" % (layer["kind"], layer["path"], state))
    hook_counts = data["user_hook_commands"]
    if hook_counts:
        counts_text = ", ".join(
            "%s=%d" % (event, hook_counts[event]) for event in sorted(hook_counts))
    else:
        counts_text = "none"
    lines.append("  user hook commands: %s" % counts_text)

    unreadable = data["unreadable"]
    lines.append("")
    lines.append("UNREADABLE (%d)" % len(unreadable))
    for entry in unreadable:
        lines.append("  %s: %s" % (entry["path"], entry["reason"]))

    not_enabled = sum(1 for p in plugins if p["enabled"] is False)
    lines.append("")
    lines.append(
        "SUMMARY: %d marketplaces, %d plugins (%d not enabled), %d skills, "
        "%d mcp servers, %d unreadable surfaces." % (
            len(marketplaces), len(plugins), not_enabled, len(skills),
            len(mcp_servers), len(unreadable)))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Conflict detection (F2). See the module docstring's CONFLICT CLASSES
# section for the override contract. The default table below is the exact
# content of tools/toolkit_conflict_classes.json, embedded as JSON text and
# parsed once at import time: decision 34 requires the shipped defaults to
# live IN this file, and parsing the real JSON text (rather than hand
# transcribing it into a Python literal) is the one way to guarantee the
# two never drift apart by a typo.
# ---------------------------------------------------------------------------

_DEFAULT_CONFLICT_CLASSES_JSON = r"""
{
  "schema": 1,
  "_comment": "The conflict classes tools/bm_toolkit.py checks for, their severities, and a real fixture for each. FOUNDER-EDITABLE BY DESIGN (decision 6 of the toolkit ratification): severity is a judgement about how a particular team works, not a property of the code, so it lives here where a change is a one-line diff rather than an edit to a checker. Three severities and nothing else: info, warn, fail. A class whose surface cannot be read reports NO-DATA and is never silently omitted, because a check that cannot tell must not read as a pass. Every fixture below was MEASURED on the founder's machine on 2026-08-12 by walking ~/.claude/plugins/cache/*/*/*/hooks/hooks.json and ~/.claude/settings.json; none is invented, and a fixture that stops reproducing is a finding about the machine rather than a reason to delete the class.",
  "severities": {
    "info": "Worth knowing. Two capabilities overlap in a way that is usually deliberate.",
    "warn": "Worth acting on. Two capabilities can produce different answers to the same question, and nothing decides which wins.",
    "fail": "Worth stopping for. Two capabilities claim the same authority over state, so one of them is silently losing."
  },
  "classes": [
    {
      "id": "multi-stop-hook",
      "title": "More than one capability registers a Stop hook",
      "severity": "warn",
      "why": "A Stop hook decides what happens when the model tries to finish. Several of them run, in an order nothing here documents, and each may block, warn, or rewrite the ending. A user who sees one of them fire has no way to know which capability spoke.",
      "detect": "count distinct plugins whose hooks.json declares the Stop event",
      "threshold": 2,
      "fixture_2026_08_12": {
        "count": 7,
        "members": ["brotherme@brotherme-marketplace", "brothersbe@brothersbe", "hookify@claude-plugins-official", "ralph-loop@claude-plugins-official", "security-guidance@claude-plugins-official", "slop-gate@claude-community", "ultrapowers@claude-community"]
      },
      "remedy_shape": "claude plugin disable <plugin>@<marketplace>, for the ones whose Stop behaviour you do not want"
    },
    {
      "id": "multi-pretooluse-blocker",
      "title": "More than one capability can refuse a tool call",
      "severity": "warn",
      "why": "A PreToolUse hook can block. When several can, a refusal arrives with no indication of which one refused, and two of them disagreeing is undefined behaviour rather than a decision. This is the class where the machine-wide settings layer matters as much as the plugins: the founder's own spend guard and session cap sit here too, and they are meant to.",
      "detect": "count plugin registrations of PreToolUse, plus hook commands under PreToolUse in every settings layer, and report the two counts separately",
      "threshold": 2,
      "fixture_2026_08_12": {
        "plugin_registrations": 8,
        "distinct_plugins": 7,
        "settings_commands": 2,
        "total_layers": 10,
        "note": "8 registrations from 7 distinct plugins because brothersbe is cached at two versions; the 2 settings commands are the founder's spend guard and session cap, which are deliberate"
      },
      "remedy_shape": "read the layers in order and decide which are deliberate; the settings-layer ones usually are"
    },
    {
      "id": "multi-version-cached",
      "title": "One capability is present at more than one version",
      "severity": "fail",
      "why": "Version is part of identity. Two versions of one plugin cached together means any statement about what that plugin does is ambiguous, and a capability record describing one of them does not describe the other. This is the class behind the failure where a command line tool ran two major versions behind what was installed, and the missing command was reported as a product defect when it was an install defect.",
      "detect": "group cached plugin directories by marketplace and plugin name; more than one version directory is a finding",
      "threshold": 2,
      "fixture_2026_08_12": {
        "count": 1,
        "members": ["brothersbe@brothersbe at 1.0.0-rc.1 and 1.0.0-rc.38"]
      },
      "remedy_shape": "remove the stale version directory, or reinstall to pin one"
    },
    {
      "id": "multi-memory-authority",
      "title": "More than one capability claims to be the memory",
      "severity": "fail",
      "why": "Two systems that both believe they hold the session's history will disagree, and nothing arbitrates. Worse, at least one known memory plugin documents assuming exclusive hook access, so its correctness depends on being alone. BrotherMode's own position is settled: the store is the sole authority for delivery state and the Obsidian vault is the knowledge backbone, so any other memory system is a guest that must be routed read-only.",
      "detect": "capabilities registering three or more of SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd AND declaring a persistent store of their own",
      "threshold": 2,
      "known_members": ["claude-mem registers five hooks and keeps its own SQLite through a local worker service, and its documentation assumes exclusive hook access", "claude-cortex writes into the same per-project memory directory tree the harness's own auto-memory uses"],
      "fixture_2026_08_12": {
        "count": 0,
        "note": "neither is installed on this machine today; the class ships with named members so it fires the day one arrives, rather than being written after the collision"
      },
      "remedy_shape": "pick one authority for delivery state and route the others read-only"
    },
    {
      "id": "state-namespace-collision",
      "title": "Two capabilities own overlapping project state directories",
      "severity": "warn",
      "why": "A capability that owns a directory of project state expects to be the only writer of it. Two of them in one repository produce a state file whose history has two authors and no merge rule.",
      "detect": "compare each installed capability's declared state namespace against the others and against the project tree",
      "threshold": 2,
      "known_namespaces": {
        "gsd": ".planning/ holding STATE.md, ROADMAP.md, REQUIREMENTS.md, plus .gsd-backups/",
        "arc": ".arc/log.md and docs/arc/",
        "compound-engineering": "docs/solutions/ and docs/plans/, relocatable",
        "brothermode": ".brothermode/ and STATE.md",
        "bmad": "UNCONFIRMED, which is why the route to it ships disabled until a repository-tree read settles it"
      },
      "remedy_shape": "relocate the movable one, or scope one capability to a different project"
    },
    {
      "id": "definition-of-done-collision",
      "title": "More than one capability defines when work is finished",
      "severity": "warn",
      "why": "This is the one that matters most to a delivery product, because it is the question BrotherMode exists to answer. Several installed capabilities carry their own completion rule, and a user following two of them can satisfy one while failing the other. BrotherMode's position is settled and is not a compromise: its own rerun after the last edit closes the work, and every other definition is recorded as claimed output.",
      "detect": "count capabilities shipping a completion or verification gate",
      "threshold": 2,
      "fixture_2026_08_12": {
        "count": 5,
        "members": ["superpowers verification-before-completion", "ultrapowers verification-before-completion", "brothermode definition-of-done", "brothersbe evidence receipts", "slop-gate premature-completion detection"]
      },
      "remedy_shape": "none needed inside BrotherMode: its rerun is the closing authority and the others are recorded, not obeyed. The finding exists so a user knows why two tools disagree."
    },
    {
      "id": "duplicate-planning-workflow",
      "title": "Several capabilities offer a planning workflow",
      "severity": "info",
      "why": "Overlap here is usually deliberate and often useful; a person picks the planner that fits the work. It is info rather than warn because nothing breaks, and the cost is only a user wondering which to use.",
      "detect": "count capabilities exposing a plan or spec authoring command",
      "threshold": 3,
      "fixture_2026_08_12": {
        "count": 5,
        "members": ["superpowers writing-plans", "ultrapowers planner", "ultraplan", "mattpocock to-spec", "feature-dev"]
      },
      "remedy_shape": "none; the routing table names which one a task class prefers"
    },
    {
      "id": "installed-not-enabled",
      "title": "A capability is cached but not enabled",
      "severity": "info",
      "why": "Its files are on disk and its hooks are not running. Harmless, and worth reporting because it is the most common explanation for a capability that somebody believes is active and is not.",
      "detect": "a cached plugin directory whose name@marketplace is absent from enabledPlugins in the user settings",
      "threshold": 1,
      "remedy_shape": "claude plugin enable <plugin>@<marketplace>, or remove the cache directory"
    },
    {
      "id": "scope-split",
      "title": "The same capability is configured at more than one scope",
      "severity": "warn",
      "why": "A capability enabled globally and configured per project can behave differently depending on which directory a session started in, which is the hardest class of surprise to diagnose because the tool is behaving correctly in both.",
      "detect": "compare capability presence across the user settings layer, the project settings layer, and the project local layer",
      "threshold": 2,
      "remedy_shape": "pick one scope; state which in the project README so a new person does not rediscover it"
    },
    {
      "id": "undeclared-network-or-credential-reach",
      "title": "A capability reaches the network or a credential without saying so",
      "severity": "fail",
      "why": "Two proven incidents on this machine: a local proxy defaulted to listening on every network interface with no authentication when its token was empty, which was the default; and a token-saving hook returned allow on everything it wrapped, which would have auto-approved reading any file including private keys and environment files. Neither was a bug. Each was a tool behaving as documented, trusted more than it had earned.",
      "detect": "static scan of a capability's own scripts for network and credential-shaped references, compared against what its capability record declares",
      "threshold": 1,
      "limits": "DECLARED reach only. This class reads what a capability's files say, never what a running process does. It cannot see syscalls, traffic, or reads that leave no artifact, and any claim otherwise about this checker is wrong.",
      "remedy_shape": "declare the reach in the capability record, or quarantine the capability until somebody does"
    }
  ]
}
"""

DEFAULT_CONFLICT_DATA = json.loads(_DEFAULT_CONFLICT_CLASSES_JSON)
DEFAULT_CONFLICT_CLASSES = DEFAULT_CONFLICT_DATA["classes"]

_REQUIRED_CLASS_KEYS = ("id", "title", "severity", "detect", "threshold")
_VALID_SEVERITIES = ("info", "warn", "fail")


def _validate_classes_shape(data):
    """(True, None) or (False, reason). Checked before an override JSON is
    trusted: a malformed override must be refused BY NAME, never trusted
    half-parsed and never silently substituted for a class this module
    knows how to render."""
    if not isinstance(data, dict):
        return False, "root is not a JSON object"
    classes = data.get("classes")
    if not isinstance(classes, list) or not classes:
        return False, '"classes" key is missing or not a non-empty list'
    for entry in classes:
        if not isinstance(entry, dict):
            return False, "a classes entry is not a JSON object"
        missing = [key for key in _REQUIRED_CLASS_KEYS if key not in entry]
        if missing:
            return False, "classes entry %r missing keys: %s" % (
                entry.get("id", "?"), ", ".join(missing))
        if entry["severity"] not in _VALID_SEVERITIES:
            return False, "classes entry %s has unknown severity %r" % (
                entry["id"], entry["severity"])
    return True, None


def load_conflict_classes(explicit_path):
    """(classes, source, override_error). `source` is "default" or the path
    actually used. An explicit --classes path that is missing or malformed
    is refused BY NAME: `override_error` names the path and the reason, and
    `classes` still falls back to DEFAULT_CONFLICT_CLASSES so the run never
    goes classless (decision 34). The shipped override path
    (tools/toolkit_conflict_classes.json next to this file) is optional:
    its plain absence, with no explicit --classes given, is not an error,
    only silence, same as every other optional surface in this module."""
    path = explicit_path or os.path.join(HERE, "toolkit_conflict_classes.json")
    if not os.path.isfile(path):
        if explicit_path:
            return DEFAULT_CONFLICT_CLASSES, "default", "%s: missing file" % path
        return DEFAULT_CONFLICT_CLASSES, "default", None
    data, err = _read_json_file(path)
    if err:
        return DEFAULT_CONFLICT_CLASSES, "default", "%s: %s" % (path, err)
    ok, reason = _validate_classes_shape(data)
    if not ok:
        return DEFAULT_CONFLICT_CLASSES, "default", "%s: %s" % (path, reason)
    return data["classes"], path, None


# --- per-class detectors ----------------------------------------------------
# Each detector takes (inventory_data, class_def) and returns
# (count, detail, ok, reason). ok=False means the surface this class needs
# could not be read (NO-DATA for this class alone, never the whole run);
# `reason` names why. A class id with no entry in CLASS_DETECTORS below has
# no mechanical detector at all (its `detect` field needs an artifact this
# module does not inventory, e.g. a static code scan or cross-repository
# state), which is also reported as NO-DATA, named, rather than dropped.

def _detect_multi_stop_hook(data, cls):
    members = sorted(set(
        "%s@%s" % (p["name"], p["marketplace"])
        for p in data["plugins"] if "Stop" in p["hook_events"]))
    return len(members), {"members": members}, True, None


def _detect_multi_pretooluse_blocker(data, cls):
    registrants = [p for p in data["plugins"] if "PreToolUse" in p["hook_events"]]
    plugin_registrations = len(registrants)
    distinct_plugins = len(set(p["name"] for p in registrants))
    settings_commands = data["user_hook_commands"].get("PreToolUse", 0)
    detail = {
        "plugin_registrations": plugin_registrations,
        "distinct_plugins": distinct_plugins,
        "settings_commands": settings_commands,
        "members": sorted(set(
            "%s@%s" % (p["name"], p["marketplace"]) for p in registrants)),
    }
    return plugin_registrations + settings_commands, detail, True, None


def _detect_multi_version_cached(data, cls):
    # `count` here is the WORST offender's version count (max versions
    # cached for any one plugin), not the number of offending plugins: the
    # class's own detect text ("more than one version directory is a
    # finding") and its 2026-08-12 fixture (one offending plugin, severity
    # fail, no "wait for a second one" language) both treat a single
    # two-version plugin as already worth firing. Any offender therefore
    # has at least 2 versions, so it reaches threshold 2 the moment it
    # exists, matching that intent.
    groups = {}
    for p in data["plugins"]:
        groups.setdefault((p["marketplace"], p["name"]), []).append(p["version"])
    members = []
    worst = 0
    for (mkt, name), versions in sorted(groups.items()):
        if len(versions) > 1:
            members.append("%s@%s at %s" % (name, mkt, " and ".join(sorted(versions))))
            worst = max(worst, len(versions))
    return worst, {"members": members}, True, None


def _detect_installed_not_enabled(data, cls):
    members = sorted(
        "%s@%s" % (p["name"], p["marketplace"])
        for p in data["plugins"] if p["enabled"] is False)
    return len(members), {"members": members}, True, None


# ponytail: multi-memory-authority's own `detect` text needs "declaring a
# persistent store of their own", which no surface in this inventory
# records (nothing here reads what a plugin's code does with disk). A
# hook-event-breadth stand-in was tried and dropped: on the real machine it
# threw FAIL on five plugins that keep no store at all, and a wrong FAIL at
# "worth stopping for" severity is worse than an honest NO-DATA. It falls
# through to the no-detector path below on purpose. Upgrade path: TK3
# capability records, if they end up declaring storage explicitly.

CLASS_DETECTORS = {
    "multi-stop-hook": _detect_multi_stop_hook,
    "multi-pretooluse-blocker": _detect_multi_pretooluse_blocker,
    "multi-version-cached": _detect_multi_version_cached,
    "installed-not-enabled": _detect_installed_not_enabled,
}


def build_conflicts(inventory_data, classes):
    """[result, ...], one per class in `classes`, in the given order.
    Each result: {id, title, severity, status, count, threshold, detail,
    reason}. status is one of "fired" (count >= threshold), "clear" (count
    < threshold), or "no_data" (reason names why: either the detector
    itself could not read its surface, or this class has no detector)."""
    results = []
    for cls in classes:
        detector = CLASS_DETECTORS.get(cls.get("id"))
        if detector is None:
            results.append({
                "id": cls.get("id"), "title": cls.get("title"),
                "severity": cls.get("severity"), "status": "no_data",
                "count": None, "threshold": cls.get("threshold"),
                "detail": None,
                "reason": "no mechanical detector for this class; detect: %s"
                          % cls.get("detect", "(not stated)"),
            })
            continue
        count, detail, ok, reason = detector(inventory_data, cls)
        if not ok:
            results.append({
                "id": cls.get("id"), "title": cls.get("title"),
                "severity": cls.get("severity"), "status": "no_data",
                "count": None, "threshold": cls.get("threshold"),
                "detail": None, "reason": reason,
            })
            continue
        threshold = cls.get("threshold", 1)
        status = "fired" if count >= threshold else "clear"
        results.append({
            "id": cls.get("id"), "title": cls.get("title"),
            "severity": cls.get("severity"), "status": status,
            "count": count, "threshold": threshold,
            "detail": detail, "reason": None,
        })
    return results


def render_conflicts(results):
    fired = [r for r in results if r["status"] == "fired"]
    clear = [r for r in results if r["status"] == "clear"]
    no_data = [r for r in results if r["status"] == "no_data"]

    lines = []
    lines.append("CONFLICTS FOUND (%d)" % len(fired))
    for r in fired:
        lines.append("  [%s] %s: %s (count=%s threshold=%s)" % (
            r["severity"].upper(), r["id"], r["title"], r["count"], r["threshold"]))
        members = (r["detail"] or {}).get("members") or []
        for member in members:
            lines.append("    - %s" % member)

    lines.append("")
    lines.append("NO-DATA (%d)" % len(no_data))
    for r in no_data:
        lines.append("  %s: %s" % (r["id"], r["reason"]))

    lines.append("")
    lines.append("CLEAR (%d)" % len(clear))
    for r in clear:
        lines.append("  %s (count=%s threshold=%s)" % (
            r["id"], r["count"], r["threshold"]))

    lines.append("")
    lines.append(
        "SUMMARY: %d classes checked, %d fired, %d clear, %d no-data." % (
            len(results), len(fired), len(clear), len(no_data)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Capability records (F3). See the module docstring's CAPABILITY RECORDS
# section for the shape and the derived-versus-hand-asserted contract.
# ---------------------------------------------------------------------------

_REQUIRED_OVERRIDE_KEYS = ("name", "kind")


def _validate_overrides_shape(data):
    """(True, None) or (False, reason). Checked before an override JSON is
    trusted, same discipline as _validate_classes_shape: a malformed
    override must be refused BY NAME, never trusted half-parsed."""
    if not isinstance(data, dict):
        return False, "root is not a JSON object"
    entries = data.get("records")
    if not isinstance(entries, list):
        return False, '"records" key is missing or not a list'
    for entry in entries:
        if not isinstance(entry, dict):
            return False, "a records entry is not a JSON object"
        missing = [key for key in _REQUIRED_OVERRIDE_KEYS if key not in entry]
        if missing:
            return False, "records entry %r missing keys: %s" % (
                entry.get("name", "?"), ", ".join(missing))
    return True, None


def load_capability_overrides(explicit_path):
    """(overrides, source, override_error). `overrides` is the raw list of
    dicts a human typed into tools/toolkit_capability_overrides.json (or an
    explicit --overrides path): hand-asserted capability claims for
    something this inventory cannot derive from artifacts on its own.
    Unlike conflict classes there is no shipped default table here: a
    hand-asserted record only exists when a human wrote one, so plain
    absence (no explicit path given, the shipped path missing) is silence,
    not an error, same as every other optional surface in this module. An
    explicit --overrides path that is missing or malformed is refused BY
    NAME and the run falls back to zero overrides, never crashes and never
    trusts a half-parsed file."""
    path = explicit_path or os.path.join(
        HERE, "toolkit_capability_overrides.json")
    if not os.path.isfile(path):
        if explicit_path:
            return [], "default", "%s: missing file" % path
        return [], "default", None
    data, err = _read_json_file(path)
    if err:
        return [], "default", "%s: %s" % (path, err)
    ok, reason = _validate_overrides_shape(data)
    if not ok:
        return [], "default", "%s: %s" % (path, reason)
    return data["records"], path, None


def build_capability_records(data, overrides):
    """[record, ...]: one DERIVED record per plugin, per skill, per MCP
    server in `data` (built straight from the same fields build_inventory
    already parsed, no new artifact reads), followed by one HAND-ASSERTED
    record per entry in `overrides`. Each record: {id, kind, name,
    marketplace, version, declared_reach, source_files, hand_asserted}.
    `source_files` for a derived record is never empty: the capability's
    own directory (or, for an MCP server, .claude.json) is always first.
    `source_files` for a hand-asserted record is always empty: nothing was
    read to build it, which is exactly what hand-asserted means."""
    records = []

    for p in data["plugins"]:
        source_files = [p["path"]]
        if p["manifest_present"]:
            source_files.append(
                os.path.join(p["path"], ".claude-plugin", "plugin.json"))
        hooks_path = os.path.join(p["path"], "hooks", "hooks.json")
        if os.path.isfile(hooks_path):
            source_files.append(hooks_path)
        records.append({
            "id": "%s@%s" % (p["name"], p["marketplace"]),
            "kind": "plugin",
            "name": p["name"],
            "marketplace": p["marketplace"],
            "version": p["version"],
            "declared_reach": {"hook_events": p["hook_events"]},
            "source_files": source_files,
            "hand_asserted": False,
        })

    for s in data["skills"]:
        source_files = [s["path"]]
        if s["has_skill_md"]:
            source_files.append(os.path.join(s["path"], "SKILL.md"))
        hooks_path = os.path.join(s["path"], "hooks", "hooks.json")
        if os.path.isfile(hooks_path):
            source_files.append(hooks_path)
        records.append({
            "id": "skill:%s" % s["name"],
            "kind": "skill",
            "name": s["name"],
            "marketplace": None,
            "version": None,
            "declared_reach": {"hook_events": s["hook_events"]},
            "source_files": source_files,
            "hand_asserted": False,
        })

    if data["mcp_servers"]:
        mcp_source = os.path.join(data["home"], ".claude.json")
        for m in data["mcp_servers"]:
            records.append({
                "id": "mcp:%s" % m["name"],
                "kind": "mcp_server",
                "name": m["name"],
                "marketplace": None,
                "version": None,
                "declared_reach": {"registered": True},
                "source_files": [mcp_source],
                "hand_asserted": False,
            })

    for entry in overrides:
        kind = entry.get("kind")
        name = entry.get("name")
        records.append({
            "id": entry.get("id") or "%s:%s" % (kind, name),
            "kind": kind,
            "name": name,
            "marketplace": entry.get("marketplace"),
            "version": entry.get("version"),
            "declared_reach": entry.get("declared_reach") or {},
            "source_files": [],
            "hand_asserted": True,
        })

    return records


def render_capabilities(records):
    def _reach_text(record):
        reach = record["declared_reach"] or {}
        if not reach:
            return "none"
        return ", ".join(
            "%s=%s" % (key, reach[key]) for key in sorted(reach))

    derived = [r for r in records if not r["hand_asserted"]]
    hand_asserted = [r for r in records if r["hand_asserted"]]

    lines = []
    lines.append("DERIVED CAPABILITY RECORDS (%d)" % len(derived))
    for r in derived:
        lines.append("  %s [%s] declared_reach: %s" % (
            r["id"], r["kind"], _reach_text(r)))
        lines.append("    source: %s" % ", ".join(r["source_files"]))

    lines.append("")
    lines.append("HAND-ASSERTED CAPABILITY RECORDS (%d)" % len(hand_asserted))
    for r in hand_asserted:
        lines.append("  [HAND-ASSERTED] %s [%s] declared_reach: %s" % (
            r["id"], r["kind"], _reach_text(r)))

    lines.append("")
    lines.append(
        "SUMMARY: %d records, %d derived, %d hand-asserted." % (
            len(records), len(derived), len(hand_asserted)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routing (F3). See the module docstring's ROUTING section for the shape
# and the enabled/disabled/degrade contract. The default table below is the
# exact content of tools/toolkit_routes.json, embedded as JSON text and
# parsed once at import time, the identical decision-34 shape
# DEFAULT_CONFLICT_CLASSES already uses above, for the same reason: parsing
# the real JSON text is the one way to guarantee the shipped file and the
# in-code fallback never drift apart by a typo.
# ---------------------------------------------------------------------------

_DEFAULT_ROUTES_JSON = r"""
{
  "schema": 1,
  "_comment": "The routing table tools/bm_toolkit.py route reads: which existing capability does a task class, and what to do when it is absent from this machine. Seeded from the founder's own six examples (docs/plan/TOOLKIT-PLAN-2026-08-12.md section F3, ratified 2026-08-12). FOUNDER-EDITABLE BY DESIGN, decision 34's pattern: this file, present and valid, REPLACES the module's embedded default entirely; a change here is a one-line diff rather than an edit to the router. Every route names a reason and a flip condition so a later reader can see why the choice was made and what would change it, never a bare capability name. Routing composes with the delegation ladder rather than replacing it: this table picks the CAPABILITY, delegation still picks the TIER.",
  "classes": [
    {
      "id": "tdd",
      "title": "Test-driven implementation",
      "routes": [
        {
          "capability": "Superpowers (TDD workflow)",
          "match": {"kind": "plugin", "name": "superpowers"},
          "reason": "Superpowers ships a red-green-refactor TDD skill. BrotherMode's own native floor is discipline plus the gate battery, deliberately not a TDD engine (decision 22), so TDD is routed rather than built.",
          "flip_condition": "Routing friction against Superpowers is MEASURED, not felt (decision 22); only then does a minimal native TDD core get built.",
          "degrade_path": "Fall back to the native floor: the gate battery (tools/test_all.py) alone, with no automated red-green cycling and nothing that enforces writing a test before the code it covers; that discipline is on the operator, not the machine.",
          "enabled": true
        }
      ]
    },
    {
      "id": "product-strategy",
      "title": "Product strategy and PRD elaboration",
      "routes": [
        {
          "capability": "Compound Engineering",
          "match": {"kind": "plugin", "name": "compound-engineering"},
          "reason": "Compound Engineering's product-elaboration commands turn an idea into a PRD. BrotherMode absorbs only its compounding-learning ESSENCE (bm_learn plus the vault), never the tool itself.",
          "flip_condition": "The plugin is removed from this machine, or its state namespace collides with another installed capability's (state-namespace-collision).",
          "degrade_path": "Fall back to the native floor: the store's own goal, scope, success_criteria and non_goals fields, elaborated by hand.",
          "enabled": true
        },
        {
          "capability": "BMAD-METHOD",
          "match": {"kind": "plugin", "name": "bmad"},
          "reason": "BMAD's nine personas and PRD flow are a second route for the same task class, for when Compound Engineering is absent or a persona-driven flow fits the work better.",
          "flip_condition": "Somebody confirms BMAD's on-disk state footprint against a real repository tree, per the state-namespace-collision conflict class.",
          "degrade_path": "Fall back to Compound Engineering if present, otherwise the native floor: the store's own goal, scope, success_criteria and non_goals fields, elaborated by hand.",
          "enabled": false,
          "disabled_reason": "on-disk footprint unconfirmed"
        }
      ]
    },
    {
      "id": "long-phase-planning",
      "title": "Long, multi-phase project planning",
      "routes": [
        {
          "capability": "GSD-class planning",
          "match": {"kind": "plugin", "name": "gsd"},
          "reason": "GSD owns .planning/ with machine-verifiable acceptance criteria per task. BrotherMode absorbs the essence, criterion-linked verification folded into T5, without absorbing the tool.",
          "flip_condition": "GSD's .planning/ namespace collides with a project already using that path for something else.",
          "degrade_path": "Fall back to the native floor: bm_project.py tasks with acceptance checks, the plan law of files plus done-checks.",
          "enabled": true
        }
      ]
    },
    {
      "id": "security-check",
      "title": "Security review of a change",
      "routes": [
        {
          "capability": "Security Guidance",
          "match": {"kind": "plugin", "name": "security-guidance"},
          "reason": "Security Guidance provides review depth beyond the native floor's dash scan and write-site inventory.",
          "flip_condition": "The plugin is disabled or removed from this machine.",
          "degrade_path": "Fall back to the native floor: dash scan, write-site inventory and effect classes, all already in the gate. Credential leak scanning is NOT part of that floor: no suite in this project scans the tree for leaked credentials.",
          "enabled": true
        }
      ]
    },
    {
      "id": "pr-review",
      "title": "Pull request or diff review",
      "routes": [
        {
          "capability": "Anthropic Code Review",
          "match": {"kind": "skill", "name": "code-review"},
          "reason": "Code Review is the founder's own named example for this task class. BrotherMode's falsification-only review brief (PO-5) is borrowed into the native floor as its own core artifact (T6); review depth is routed.",
          "flip_condition": "The skill is uninstalled, or superseded by a differently named review capability.",
          "degrade_path": "Fall back to the native floor: the falsification-only review brief, PO-5, attacks executed and COULD NOT BREAK or findings stated.",
          "enabled": true
        }
      ]
    },
    {
      "id": "historical-recall",
      "title": "Recall of past session or project history",
      "routes": [
        {
          "capability": "claude-mem-class memory",
          "match": {"kind": "plugin", "name": "claude-mem"},
          "reason": "A claude-mem-class capability gives episodic session recall. BrotherMode's own position is settled (decision 23): the store is the sole delivery-state authority and the Obsidian vault is the knowledge backbone, so this route is read-only and never authoritative.",
          "flip_condition": "A claude-mem-class capability is found claiming exclusive hook access (multi-memory-authority conflict class); it is then quarantined rather than routed.",
          "degrade_path": "Fall back to the native floor: the store for delivery state, the Obsidian vault for knowledge.",
          "enabled": true
        }
      ]
    }
  ]
}
"""

DEFAULT_ROUTES_DATA = json.loads(_DEFAULT_ROUTES_JSON)
DEFAULT_ROUTES = DEFAULT_ROUTES_DATA["classes"]

_REQUIRED_ROUTE_CLASS_KEYS = ("id", "title", "routes")
_REQUIRED_ROUTE_KEYS = ("capability", "match", "reason", "flip_condition",
                         "degrade_path", "enabled")


def _validate_routes_shape(data):
    """(True, None) or (False, reason). Same discipline as
    _validate_classes_shape and _validate_overrides_shape: a malformed
    routing table must be refused BY NAME, never trusted half-parsed."""
    if not isinstance(data, dict):
        return False, "root is not a JSON object"
    classes = data.get("classes")
    if not isinstance(classes, list) or not classes:
        return False, '"classes" key is missing or not a non-empty list'
    for entry in classes:
        if not isinstance(entry, dict):
            return False, "a classes entry is not a JSON object"
        missing = [key for key in _REQUIRED_ROUTE_CLASS_KEYS if key not in entry]
        if missing:
            return False, "classes entry %r missing keys: %s" % (
                entry.get("id", "?"), ", ".join(missing))
        routes = entry.get("routes")
        if not isinstance(routes, list) or not routes:
            return False, "classes entry %s: \"routes\" is missing or not " \
                          "a non-empty list" % entry["id"]
        for route in routes:
            if not isinstance(route, dict):
                return False, "classes entry %s: a route is not a JSON " \
                              "object" % entry["id"]
            r_missing = [key for key in _REQUIRED_ROUTE_KEYS if key not in route]
            if r_missing:
                return False, "classes entry %s, route %r missing keys: " \
                              "%s" % (entry["id"], route.get("capability", "?"),
                                       ", ".join(r_missing))
            if not isinstance(route.get("match"), dict):
                return False, "classes entry %s, route %r: \"match\" is " \
                              "not a JSON object" % (
                                  entry["id"], route.get("capability"))
    return True, None


def load_routes(explicit_path):
    """(classes, source, override_error). `source` is "default" or the path
    actually used. An explicit --routes path that is missing or malformed
    is refused BY NAME: `override_error` names the path and the reason, and
    `classes` still falls back to DEFAULT_ROUTES so the run never goes
    tableless (decision 34). The shipped file (tools/toolkit_routes.json
    next to this file) is optional: its plain absence, with no explicit
    --routes given, is not an error, only silence, same as every other
    optional surface in this module."""
    path = explicit_path or os.path.join(HERE, "toolkit_routes.json")
    if not os.path.isfile(path):
        if explicit_path:
            return DEFAULT_ROUTES, "default", "%s: missing file" % path
        return DEFAULT_ROUTES, "default", None
    data, err = _read_json_file(path)
    if err:
        return DEFAULT_ROUTES, "default", "%s: %s" % (path, err)
    ok, reason = _validate_routes_shape(data)
    if not ok:
        return DEFAULT_ROUTES, "default", "%s: %s" % (path, reason)
    return data["classes"], path, None


def find_route_class(classes, task_class):
    """The classes entry whose id equals task_class, or None. Exact match
    only: an unknown task class is refused by name, never fuzzy-matched to
    the nearest string."""
    for entry in classes:
        if entry.get("id") == task_class:
            return entry
    return None


def _capability_present(inventory_data, match):
    """True if `match` ({"kind": ..., "name": ...}) names a capability this
    machine's inventory actually carries. Reads ONLY the fields
    build_inventory() already collected; it never runs the capability or
    asks it anything. An unknown "kind", or a match missing "name", is
    always absent: this function never guesses a surface to check."""
    if not isinstance(match, dict):
        return False
    kind = match.get("kind")
    name = match.get("name")
    if not name:
        return False
    if kind == "plugin":
        return any(p["name"] == name for p in inventory_data["plugins"])
    if kind == "skill":
        return any(s["name"] == name for s in inventory_data["skills"])
    if kind == "mcp_server":
        return any(m["name"] == name for m in inventory_data["mcp_servers"])
    return False


# M6 (docs/plan/QUEUE.json): "the capability detector cannot tell
# installed from absent in either direction: it reads only
# ~/.claude/skills and the plugin caches, so it reports DEGRADE for a
# class the harness already ships built in". Every route's own
# degrade_path names a fallback in prose ("Fall back to the native
# floor: ..."), and docs/NORTH-STAR-CHAIN.md settles what that means:
# "The floor is what that degrade path resolves to." The floor is not a
# plugin or a skill this machine may or may not have installed under
# ~/.claude -- it is THIS toolkit's OWN code (bm_project.py's tasks and
# acceptance checks, test_all.py's gate battery), which ships wherever
# bm_toolkit.py does. _capability_present's plugin/skill/mcp_server scan
# was never going to find it, because it was never installed anywhere;
# it is simply here. Checked against the real sibling files, never
# assumed, so a stripped or partial install (the files genuinely
# missing) still reports honestly, per the class's own done_check: "a
# class whose capability ships with the harness reports RESOLVED or
# reports NO-DATA naming what it cannot see, never a bare DEGRADE."
_NATIVE_FLOOR_FILES = ("bm_project.py", "test_all.py")


def _native_floor_present():
    """True when this toolkit's own native-floor files are on disk next
    to bm_toolkit.py itself (HERE, not `home` or `root`: the floor ships
    WITH the toolkit, regardless of which machine or which project it is
    pointed at)."""
    return all(os.path.isfile(os.path.join(HERE, f))
               for f in _NATIVE_FLOOR_FILES)


def render_route(task_class, class_entry, inventory_data):
    """Human readable report for one resolved task class: every route in
    declared order with its status (RESOLVED, DEGRADE or DISABLED), then a
    SELECTED line naming the winner. DISABLED routes are always printed,
    never hidden, per the module docstring's ROUTING section."""
    lines = []
    lines.append("TASK CLASS: %s" % task_class)
    lines.append("TITLE: %s" % class_entry.get("title", ""))
    lines.append("")

    resolved = None
    first_degrade = None
    for route in class_entry.get("routes", []):
        capability = route.get("capability")
        if not route.get("enabled", True):
            reason = route.get("disabled_reason") or route.get("reason") \
                or "(no reason given)"
            lines.append("  [DISABLED] %s: %s" % (capability, reason))
            continue
        if _capability_present(inventory_data, route.get("match")):
            lines.append("  [RESOLVED] %s: %s" % (
                capability, route.get("reason")))
            if resolved is None:
                resolved = route
        else:
            degrade = route.get("degrade_path") or "(no degrade path stated)"
            lines.append(
                "  [DEGRADE] %s: absent from this machine's inventory; "
                "degrade path: %s" % (capability, degrade))
            if first_degrade is None:
                first_degrade = route

    lines.append("")
    if resolved is not None:
        lines.append("SELECTED: %s (%s)" % (
            resolved["capability"], resolved.get("reason")))
    elif first_degrade is not None:
        # M6: no declared route resolved, but every one of their
        # degrade_path sentences names the SAME fallback -- this
        # toolkit's own native floor -- so a bare "nothing present"
        # verdict would be false the moment that floor is confirmed on
        # disk. RESOLVED to the floor when confirmed; NO-DATA, never a
        # bare DEGRADE, when it cannot be.
        if _native_floor_present():
            lines.append("  [RESOLVED] native floor: %s" %
                         first_degrade.get("degrade_path"))
            lines.append("")
            lines.append("SELECTED: native floor (%s)" %
                         first_degrade.get("degrade_path"))
        else:
            lines.append("NO-DATA: no declared route resolved, and this "
                         "toolkit's own native floor files (%s) could not "
                         "be confirmed next to bm_toolkit.py either"
                         % ", ".join(_NATIVE_FLOOR_FILES))
    else:
        lines.append("SELECTED: none; every route for this task class is "
                      "disabled")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _parse_argv(argv):
    """Return (verb, home, root, classes, overrides, routes, task_class,
    error). `error` set means stop and report a plain usage failure (exit
    2, no "NO-DATA:" prefix: that prefix is reserved for "could not
    determine the verdict", not "bad arguments"). `classes` (--classes
    PATH) is only consumed by the `conflicts` verb; `overrides`
    (--overrides PATH) only by `capabilities`; `routes` (--routes PATH)
    and `task_class` (the one positional argument) only by `route`; every
    other verb accepts and ignores whichever of these it is not given, the
    same as it would any other unused-but-recognised flag. `route` requires
    its positional task_class BEFORE any flags, refused by name if absent."""
    args = list(argv)
    verb = "inventory"
    if args and not args[0].startswith("--"):
        verb = args.pop(0)
    if verb not in _VERBS:
        return None, None, None, None, None, None, None, \
            "bm_toolkit: unknown verb: %s" % verb

    task_class = None
    if verb == "route":
        if not args or args[0].startswith("--"):
            return None, None, None, None, None, None, None, \
                "bm_toolkit: route requires a task class argument"
        task_class = args.pop(0)

    home = None
    root = None
    classes = None
    overrides = None
    routes = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--home":
            if i + 1 >= len(args):
                return None, None, None, None, None, None, None, \
                    "bm_toolkit: --home requires a value"
            home = args[i + 1]
            i += 2
        elif arg == "--root":
            if i + 1 >= len(args):
                return None, None, None, None, None, None, None, \
                    "bm_toolkit: --root requires a value"
            root = args[i + 1]
            i += 2
        elif arg == "--classes":
            if i + 1 >= len(args):
                return None, None, None, None, None, None, None, \
                    "bm_toolkit: --classes requires a value"
            classes = args[i + 1]
            i += 2
        elif arg == "--overrides":
            if i + 1 >= len(args):
                return None, None, None, None, None, None, None, \
                    "bm_toolkit: --overrides requires a value"
            overrides = args[i + 1]
            i += 2
        elif arg == "--routes":
            if i + 1 >= len(args):
                return None, None, None, None, None, None, None, \
                    "bm_toolkit: --routes requires a value"
            routes = args[i + 1]
            i += 2
        else:
            return None, None, None, None, None, None, None, \
                "bm_toolkit: unknown argument: %s" % arg
    return verb, home, root, classes, overrides, routes, task_class, None


def _run(argv):
    """The real body of main, split out so `main` can wrap the whole thing
    in one try/except and guarantee NO-DATA on any unexpected exception."""
    verb, home_arg, root_arg, classes_arg, overrides_arg, routes_arg, \
        task_class, err = _parse_argv(argv)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    home, reason = resolve_home(home_arg)
    if home is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2
    root = resolve_root(root_arg)

    data, overall_ok = build_inventory(home, root)
    if not overall_ok:
        sys.stdout.write(
            "NO-DATA: every capability surface under %s was unreadable\n"
            % home)
        return 2

    if verb == "json":
        sys.stdout.write(json.dumps(data) + "\n")
    elif verb == "conflicts":
        classes, _source, override_error = load_conflict_classes(classes_arg)
        results = build_conflicts(data, classes)
        out = []
        if override_error:
            out.append(
                "OVERRIDE REFUSED: %s (using shipped default classes)"
                % override_error)
            out.append("")
        out.append(render_conflicts(results))
        sys.stdout.write("\n".join(out) + "\n")
    elif verb == "capabilities":
        overrides, _source, override_error = load_capability_overrides(
            overrides_arg)
        records = build_capability_records(data, overrides)
        out = []
        if override_error:
            out.append(
                "OVERRIDE REFUSED: %s (using zero hand-asserted records)"
                % override_error)
            out.append("")
        out.append(render_capabilities(records))
        sys.stdout.write("\n".join(out) + "\n")
    elif verb == "route":
        classes, _source, override_error = load_routes(routes_arg)
        out = []
        if override_error:
            out.append(
                "OVERRIDE REFUSED: %s (using shipped default routes)"
                % override_error)
            out.append("")
        class_entry = find_route_class(classes, task_class)
        if class_entry is None:
            known = sorted(c.get("id") for c in classes if c.get("id"))
            out.append("UNKNOWN TASK CLASS: %s" % task_class)
            out.append("KNOWN TASK CLASSES: %s" % ", ".join(known))
        else:
            out.append(render_route(task_class, class_entry, data))
        sys.stdout.write("\n".join(out) + "\n")
    else:
        sys.stdout.write(render_inventory(data) + "\n")
    return 0


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
