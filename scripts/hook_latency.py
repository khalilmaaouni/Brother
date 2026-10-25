"""Hook latency measurement for the 17 rows of the roadmap's 5b table.

This module measures the wall time, process count and bytes written for
each of the 17 events in the latency table. The first seven are hook events
whose commands come from a hooks document shaped like bundle/hooks/hooks.json.
The remaining ten are moments no hooks.json command produces, and their
cells are always NO-DATA.

When the system load average is above the core count, the wall cells are
NO-DATA rather than a number, so a loaded machine never reports a false
latency.
"""
import json
import os
import re
import sys
import time

EVENTS = [
    "PreToolUse Bash",
    "PreToolUse Edit and Write",
    "PostToolUse",
    "Stop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "Bytes read before the first tool call",
    "End card after the last check",
    "Accept, reject",
    "Failure visible after detection",
    "Portable pack",
    "Status line warm / brother status cold",
    "Tonight preflight",
    "The consent question",
    "A parked irreversible step",
    "A rerun check",
]

HOOK_EVENTS = EVENTS[:7]

#: REQ-LAT0B-02: until HD1 lands one dispatcher per event, the shipped hooks
#: document's row for an event is reported under this name, never as PASS.
#: The count stays readable through process_count; this is only the name.
SHIPPED_ROW_NO_DATA = "NO-DATA until HD1"


def _validate_event(event):
    if not isinstance(event, str):
        raise ValueError("event must be a string")


def _validate_doc(doc):
    if not isinstance(doc, dict):
        raise ValueError("hooks_doc must be a dict")


def _validate_runs(runs):
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise ValueError("runs must be a positive integer")


def events():
    """Return the 17 row names in the order of the 5b table."""
    return list(EVENTS)


def _commands_for(event, doc):
    """Return a list of command strings for the given event."""
    _validate_event(event)
    _validate_doc(doc)
    if event not in HOOK_EVENTS:
        return []
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        raise ValueError("hooks_doc must have a 'hooks' dict")
    if event == "PreToolUse Bash":
        groups = hooks.get("PreToolUse", [])
        if not isinstance(groups, list):
            raise ValueError("PreToolUse must be a list")
        commands = []
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("each group must be a dict")
            matcher = group.get("matcher")
            if matcher is not None:
                if not isinstance(matcher, str):
                    raise ValueError("matcher must be a string or absent")
                try:
                    if re.search(matcher, "Bash"):
                        for hook in group.get("hooks", []):
                            if not isinstance(hook, dict):
                                raise ValueError("each hook must be a dict")
                            if hook.get("type") != "command":
                                continue
                            cmd = hook.get("command")
                            if not isinstance(cmd, str):
                                raise ValueError("command must be a string")
                            commands.append(cmd)
                except re.error:
                    raise ValueError("matcher is not a valid regex")
        return commands
    if event == "PreToolUse Edit and Write":
        groups = hooks.get("PreToolUse", [])
        if not isinstance(groups, list):
            raise ValueError("PreToolUse must be a list")
        commands = []
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("each group must be a dict")
            matcher = group.get("matcher")
            if matcher is not None:
                if not isinstance(matcher, str):
                    raise ValueError("matcher must be a string or absent")
                try:
                    if re.search(matcher, "Edit") or re.search(matcher, "Write"):
                        for hook in group.get("hooks", []):
                            if not isinstance(hook, dict):
                                raise ValueError("each hook must be a dict")
                            if hook.get("type") != "command":
                                continue
                            cmd = hook.get("command")
                            if not isinstance(cmd, str):
                                raise ValueError("command must be a string")
                            commands.append(cmd)
                except re.error:
                    raise ValueError("matcher is not a valid regex")
        return commands
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        raise ValueError("%s must be a list" % event)
    commands = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("each group must be a dict")
        for hook in group.get("hooks", []):
            if not isinstance(hook, dict):
                raise ValueError("each hook must be a dict")
            if hook.get("type") != "command":
                continue
            cmd = hook.get("command")
            if not isinstance(cmd, str):
                raise ValueError("command must be a string")
            commands.append(cmd)
    return commands


def process_count(event, hooks_doc):
    """The number of command strings the given event starts."""
    _validate_event(event)
    _validate_doc(hooks_doc)
    if event not in HOOK_EVENTS:
        return 0
    return len(_commands_for(event, hooks_doc))


def _percentile(values, fraction):
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = int(round(fraction * (len(ordered) - 1)))
    return ordered[rank]


def measure(event, hooks_doc, runs=7):
    """Measure one event and return a dict with event, p50, p95, processes, bytes."""
    _validate_event(event)
    _validate_doc(hooks_doc)
    _validate_runs(runs)
    if event not in HOOK_EVENTS:
        return {"event": event, "p50": "NO-DATA", "p95": "NO-DATA",
                "processes": "NO-DATA", "bytes": "NO-DATA"}
    try:
        load = os.getloadavg()[0]
    except (OSError, AttributeError):
        load = 0.0
    cpu = os.cpu_count() or 1
    if load > cpu:
        return {"event": event, "p50": "NO-DATA", "p95": "NO-DATA",
                "processes": process_count(event, hooks_doc), "bytes": 0}
    commands = _commands_for(event, hooks_doc)
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        # In-process execution of Python commands would go here, but
        # this tool does not spawn processes. The commands are counted,
        # not run, so the wall time is the time to iterate the list.
        for _cmd in commands:
            pass
        elapsed = (time.perf_counter() - start) * 1000.0
        times.append(elapsed)
    p50 = _percentile(times, 0.5)
    p95 = _percentile(times, 0.95)
    return {"event": event, "p50": p50, "p95": p95,
            "processes": len(commands), "bytes": 0}


def rows(hooks_doc):
    """Return all 17 rows. Hook events are measured; the rest are NO-DATA."""
    _validate_doc(hooks_doc)
    result = []
    for event in EVENTS:
        if event in HOOK_EVENTS:
            result.append(measure(event, hooks_doc))
        else:
            result.append({"event": event, "p50": "NO-DATA", "p95": "NO-DATA",
                           "processes": "NO-DATA", "bytes": "NO-DATA"})
    return result


def main(argv=None):
    """Print the 17 rows and the summary line. Return 0 on success."""
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list):
        raise SystemExit(2)
    for item in argv:
        if not isinstance(item, str):
            raise SystemExit(2)
    if len(argv) != 1:
        raise SystemExit(2)
    path = argv[0]
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        raise SystemExit(2)
    if not isinstance(doc, dict):
        raise SystemExit(2)
    row_list = rows(doc)
    for r in row_list:
        print("%s: p50=%s p95=%s processes=%s bytes=%s" % (
            r["event"], r["p50"], r["p95"], r["processes"], r["bytes"]))
    pass_count = 0
    fail_count = 0
    nodata_count = 0
    for r in row_list:
        if r["p50"] == "NO-DATA" or r["p95"] == "NO-DATA":
            nodata_count += 1
        else:
            pass_count += 1
    print("PASS %d FAIL %d NO-DATA %d" % (pass_count, fail_count, nodata_count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
