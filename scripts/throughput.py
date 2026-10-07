#!/usr/bin/env python3
"""Sub units landed per hour in a window, measured the SAME WAY on both sides of a comparison.

usage (repo root):
  python3 -B scripts/throughput.py --since "2026-09-20 22:00" --until "2026-09-21 12:00"
  python3 -B scripts/throughput.py --since ... --until ... --json out.json
  python3 -B scripts/throughput.py --selftest
Exit 0 with a measurement, 2 when git cannot be read (NO-DATA, never a zero presented as a fact).

WHY IT EXISTS. On 2026-09-21 the owner asked whether a set of changes made delivery faster and cheaper. There
was no instrument, so "before" was a remembered figure and "after" would have been a different remembered
figure. A comparison whose two sides are measured by different methods is not a comparison. This reads the one
durable record of a landing, the commit that performed it, so both windows are counted by identical rules.

A ZERO IS NOT A FAILURE AND NOT A SUCCESS, IT IS A NUMBER WITH A CAUSE. The window of 2026-09-21 11:22 to 12:00
reads 0.0 an hour and the cause was a reboot that destroyed the spend ledger, so no lane was funded for half of
it. The tool therefore reports wall hours and landing commits beside the rate, so a rate can never be quoted
without the evidence that says whether the system was even running."""
import argparse
import datetime
import json
import re
import subprocess
import sys

LAND = re.compile(r"^(?P<subs>.+?)\s+lands?:\s", re.I)


def subs_of(subject):
    """The sub units a landing commit names. 'D1.4 and D6.2 and L2.3 land: ...' -> three ids.
    A subject that is not a landing returns nothing, so an ordinary commit never inflates the count."""
    m = LAND.match(subject or "")
    if not m:
        return []
    return [p.strip() for p in m.group("subs").split(" and ") if p.strip()]


def commits(since, until, repo="."):
    """(iso, subject) for every commit in the window. Raises on a git failure rather than returning empty,
    because an unreadable history must read NO-DATA and never as a window in which nothing happened."""
    r = subprocess.run(["git", "-C", repo, "log", "--since", since, "--until", until,
                        "--pretty=%aI|%s"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "git log failed")
    out = []
    for line in r.stdout.splitlines():
        if "|" in line:
            iso, subject = line.split("|", 1)
            out.append((iso, subject))
    return out


def measure(since, until, rows):
    """The window's record. Hours come from the REQUESTED window, not from the first and last commit: measuring
    only between the commits that exist would hide an idle stretch and flatter every rate."""
    landings = [(iso, subs_of(s)) for iso, s in rows]
    landings = [(iso, subs) for iso, subs in landings if subs]
    n_subs = sum(len(s) for _, s in landings)
    a = datetime.datetime.fromisoformat(since)
    b = datetime.datetime.fromisoformat(until)
    hours = max((b - a).total_seconds() / 3600.0, 0.0)
    return {"since": since, "until": until, "wall_hours": round(hours, 2),
            "landing_commits": len(landings), "sub_units_landed": n_subs,
            "sub_units_per_hour": round(n_subs / hours, 2) if hours else None,
            "sub_units": sorted(s for _, subs in landings for s in subs)}


def selftest():
    rows = [("2026-09-21T00:35:00+09:00", "C0.5 and D6.4 land: unit runner builds, grader PASS"),
            ("2026-09-21T01:41:00+09:00", "R4.1 land: unit runner builds"),
            ("2026-09-21T02:00:00+09:00", "docs: SYSTEM.md follows the tree"),
            ("2026-09-21T03:00:00+09:00", "fix: something unrelated that mentions land: in prose")]
    m = measure("2026-09-21 00:00", "2026-09-21 04:00", rows)
    cases = [
        ("a landing naming several sub units counts each one", m["sub_units_landed"] == 4),
        ("an ordinary commit is not a landing", m["landing_commits"] == 3),
        ("the rate uses the REQUESTED window, not the commit span",
         abs(m["wall_hours"] - 4.0) < 1e-9 and abs(m["sub_units_per_hour"] - 1.0) < 1e-9),
        ("an empty window is zero, with its hours stated",
         measure("2026-09-21 00:00", "2026-09-21 02:00", [])["sub_units_landed"] == 0),
        ("a zero length window yields no rate rather than a division",
         measure("2026-09-21 00:00", "2026-09-21 00:00", [])["sub_units_per_hour"] is None),
        ("a subject with no land verb yields nothing", subs_of("board: refresh the page") == []),
        ("the plural form is read too", subs_of("D1.1 lands: whatever") == ["D1.1"]),
        ("sub unit ids are not split on internal spaces", subs_of("L3b-01 land: x") == ["L3b-01"]),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--label", default="")
    ap.add_argument("--json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.since and a.until):
        ap.error("--since and --until are required")
    try:
        rows = commits(a.since, a.until)
    except (OSError, subprocess.SubprocessError) as exc:
        print("NO-DATA: git history could not be read (%s); this is not a measurement of zero" % exc)
        return 2
    m = measure(a.since, a.until, rows)
    m["label"] = a.label
    print("WINDOW  %s  %s to %s" % (a.label or "(unlabelled)", a.since, a.until))
    print("WALL    %.2f h" % m["wall_hours"])
    print("LANDED  %d sub unit(s) in %d landing commit(s)" % (m["sub_units_landed"], m["landing_commits"]))
    print("RATE    %s sub unit(s) per hour"
          % ("NO-DATA (zero length window)" if m["sub_units_per_hour"] is None else m["sub_units_per_hour"]))
    if m["sub_units"]:
        print("SUBS    %s" % ", ".join(m["sub_units"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=2)
        print("WROTE   %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
