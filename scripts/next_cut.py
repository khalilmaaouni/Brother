#!/usr/bin/env python3
"""Print the next release cut: the date, the version it would be, and the
closeout command that follows the tag. Row S29 (docs/plan/READINESS-
ROADMAP-2026-08-29.json): a fixed cadence needs a named weekday in
docs/plan/RELEASE-POLICY.md before any cut can claim to run on a schedule.
This script is that read, not a cutter: it never bumps a manifest, never
tags, never runs the closeout matrix. Reading the policy stays a plain grep
away, never invented on the day.

WHERE THE TWO INPUTS COME FROM. The weekday: a line in the policy file that
mentions "cadence" or "weekday" and names one of the seven days (case
insensitive), the way scripts/refresh_cut.py's own NO-DATA / CLEAR /
REFUSED convention reads a real file rather than assuming a shape.
RELEASE-POLICY.md as it stands on 2026-09-05 names no such line, which is
exactly the NO-DATA case below and not a bug in this script. The version:
scripts/cut_v1.0.0.sh writes the cut version into
bundle/.claude-plugin/plugin.json's own "version" key (see its step 1); the
version this script prints is that file's current value with the patch
component bumped by one, since a fixed-cadence cut is a routine patch cut
until the policy says otherwise.

Exit codes: 0 printed, 3 NO-DATA (no cut weekday named, or no version could
be read). NO-DATA is never a pass. Python 3.9, standard library only.
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_POLICY = os.path.join(ROOT, "docs", "plan", "RELEASE-POLICY.md")
DEFAULT_MANIFEST = os.path.join(ROOT, "bundle", ".claude-plugin", "plugin.json")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
WEEKDAY_RE = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE)

EXIT_OK = 0
EXIT_NODATA = 3


def find_cadence_weekday(text):
    """The first line naming both a cadence cue ('cadence' or 'weekday')
    and a day name wins; returns the day capitalized, or None."""
    for line in text.splitlines():
        low = line.lower()
        if "cadence" not in low and "weekday" not in low:
            continue
        m = WEEKDAY_RE.search(line)
        if m:
            return m.group(1).capitalize()
    return None


def next_weekday_date(today, weekday_name):
    """The next date on or after `today` that falls on `weekday_name`."""
    target = WEEKDAYS.index(weekday_name)
    delta = (target - today.weekday()) % 7
    return today + datetime.timedelta(days=delta)


def bump_patch(version):
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--policy", default=DEFAULT_POLICY,
                     help="path to RELEASE-POLICY.md (default: the hub's own)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                     help="path to the plugin manifest cut_v1.0.0.sh writes "
                          "the version into (default: bundle/.claude-plugin/"
                          "plugin.json)")
    ap.add_argument("--today", default=None,
                     help="fix today's date as YYYY-MM-DD, for tests; "
                          "default: the real date")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.today:
        try:
            today = datetime.datetime.strptime(args.today, "%Y-%m-%d").date()
        except ValueError:
            print("NO-DATA: --today %r is not YYYY-MM-DD" % args.today)
            return EXIT_NODATA
    else:
        today = datetime.date.today()

    try:
        with open(args.policy, "r", encoding="utf-8") as fh:
            policy_text = fh.read()
    except OSError as exc:
        print("NO-DATA: could not read %s (%s)" % (args.policy, exc))
        return EXIT_NODATA

    weekday = find_cadence_weekday(policy_text)
    if not weekday:
        print("NO-DATA: RELEASE-POLICY.md names no cut weekday (S29, founder)")
        return EXIT_NODATA

    try:
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        current_version = manifest["version"]
    except (OSError, ValueError, KeyError) as exc:
        print("NO-DATA: could not read a version from %s (%s)"
              % (args.manifest, exc))
        return EXIT_NODATA

    cut_date = next_weekday_date(today, weekday)
    version = bump_patch(current_version)

    print("next cut weekday: %s" % weekday)
    print("next cut date: %s" % cut_date.isoformat())
    print("next cut version: %s" % version)
    print("closeout command: python3 scripts/release_closeout.py all "
          "--version %s" % version)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
