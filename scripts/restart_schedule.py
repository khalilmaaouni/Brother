#!/usr/bin/env python3
"""R25.3: the dynamic restart scheduler. Generalizes the existing launchd
arm (~/Library/LaunchAgents/com.brother.usage-restart.plist, previously
hardcoded to fire daily at 05:50) to fire ONCE at a measured limit's own
reset time plus a safety margin, so the same mechanism serves the five_hour
and seven_day classes alike.

Keeps the existing flag-file-inert design unchanged: the plist always calls
~/.claude/brother-restart/restart.sh, which does nothing unless
armed.flag exists (see limit_watch.py's arm()). This module only ever
rewrites WHEN the plist fires, never what it does when it fires.

launchd's StartCalendarInterval has no Year key, so Month+Day+Hour+Minute
is the closest to a true one-shot: it fires on that calendar date this
year, then not again until the same date next year, which the flag-file
design already renders harmless (no flag, no action). Refuses (NO-DATA)
when resets_at is null: a class with no reset time cannot be scheduled by
waiting (see limit_watch.py's "fallback-model" and "monthly-spend"
classes). No em or en dashes.
"""

import os
import subprocess
import sys
import time

DEFAULT_PLIST_PATH = os.path.expanduser(
    "~/Library/LaunchAgents/com.brother.usage-restart.plist")
DEFAULT_LABEL = "com.brother.usage-restart"
DEFAULT_RESTART_SCRIPT = os.path.expanduser(
    "~/.claude/brother-restart/restart.sh")
DEFAULT_MARGIN = 120

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%(label)s</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>%(restart_script)s</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Month</key><integer>%(month)d</integer>
    <key>Day</key><integer>%(day)d</integer>
    <key>Hour</key><integer>%(hour)d</integer>
    <key>Minute</key><integer>%(minute)d</integer>
  </dict>
</dict>
</plist>
"""


def fire_epoch(resets_at, margin=DEFAULT_MARGIN, now=None):
    """resets_at + margin, or margin seconds from now when resets_at has
    already passed. None in, None out: never guess a time for a class that
    carries no reset."""
    if resets_at is None:
        return None
    now = time.time() if now is None else now
    candidate = resets_at + margin
    if candidate <= now:
        candidate = now + margin
    return candidate


def render_plist(epoch, label=DEFAULT_LABEL, restart_script=DEFAULT_RESTART_SCRIPT,
                  localtime_fn=time.localtime):
    t = localtime_fn(epoch)
    return PLIST_TEMPLATE % {
        "label": label, "restart_script": restart_script,
        "month": t.tm_mon, "day": t.tm_mday, "hour": t.tm_hour,
        "minute": t.tm_min,
    }


def _reload_launchd(plist_path, label=DEFAULT_LABEL):
    # unload first and ignore its failure: it fails harmlessly when the
    # label was never loaded, matching this codebase's own
    # `launchctl load|unload` convention (scripts/night_tick.py).
    subprocess.run(["launchctl", "unload", plist_path], capture_output=True)  # sbe: allow-silent unload fails harmlessly when the label was never loaded; only the load result decides
    return subprocess.run(["launchctl", "load", plist_path],
                          capture_output=True, text=True)


def schedule(resets_at, margin=DEFAULT_MARGIN, plist_path=DEFAULT_PLIST_PATH,
             label=DEFAULT_LABEL, restart_script=DEFAULT_RESTART_SCRIPT,
             now=None, reload_fn=_reload_launchd, localtime_fn=time.localtime):
    """Rewrites plist_path to fire once at resets_at + margin and reloads
    it via reload_fn. Pass reload_fn=None to skip the launchctl call
    (tests always do this, and always pass a plist_path outside the real
    LaunchAgents directory, so a test never touches the live schedule)."""
    epoch = fire_epoch(resets_at, margin=margin, now=now)
    if epoch is None:
        return {"scheduled": False,
                "error": "NO-DATA: resets_at is null, nothing to schedule"}
    content = render_plist(epoch, label=label, restart_script=restart_script,
                            localtime_fn=localtime_fn)
    parent = os.path.dirname(plist_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(content)
    if reload_fn is not None:
        reload_fn(plist_path, label=label)
    t = localtime_fn(epoch)
    return {"scheduled": True, "plist_path": plist_path, "fire_epoch": epoch,
            "fire_local": time.strftime("%Y-%m-%d %H:%M", t)}


def main(argv):
    resets_at = None
    margin = DEFAULT_MARGIN
    plist_path = DEFAULT_PLIST_PATH
    no_reload = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--resets-at" and i + 1 < len(argv):
            resets_at = float(argv[i + 1]); i += 1
        elif a == "--margin" and i + 1 < len(argv):
            margin = int(argv[i + 1]); i += 1
        elif a == "--plist-path" and i + 1 < len(argv):
            plist_path = argv[i + 1]; i += 1
        elif a == "--no-reload":
            no_reload = True
        i += 1

    if resets_at is None:
        print("restart-schedule: NO-DATA: --resets-at is required")
        return 2

    result = schedule(resets_at, margin=margin, plist_path=plist_path,
                      reload_fn=None if no_reload else _reload_launchd)
    if not result.get("scheduled"):
        print("restart-schedule: %s" % result["error"])
        return 2
    print("restart-schedule: scheduled %s to fire at %s (local)"
         % (result["plist_path"], result["fire_local"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
