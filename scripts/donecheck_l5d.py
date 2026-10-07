#!/usr/bin/env python3
"""L5d's done check: the gate's real wall clock time, measured on a QUIET machine, with its cause named.

usage (repo root): python3 -B scripts/donecheck_l5d.py [--evidence PATH]
Exit 0 when the audit is complete and its measurement is trustworthy, 1 when it falls short,
2 when the evidence cannot be read (NO-DATA, never a pass).

WHY IT EXISTS. L5d's recorded done_check was the sentence "score >= 8.5/10; the root cause is named with real
evidence (not 'probably load'), and a real re-measured wall-clock time is quoted". No tool can execute a
sentence, so the bar was never measured and the unit could not close however much work was done. This estate's
delivery laws name that class `prose-done-check`: the sentence is the DEFINITION and stays in done_check_prose;
the command is the MEASUREMENT.

WHAT IT REFUSES, which is the whole point. The unit exists because a gate documented at about 90 seconds
measured 10 minutes 30 on a busy worktree, and nobody could say whether that was contention, a regression or a
stale target. A timing taken while the machine is loaded cannot distinguish those, so this check REFUSES a
measurement whose recorded load average is above the quiet bar rather than accepting it as evidence. Measured
on this machine 2026-09-21: load ran 26.4 immediately after a reboot, 12.1 with six runners working and 5.8
once they drained, on 8 cores. A number taken at 26 says nothing about the code.

The fail direction is deliberate: an unreadable, incomplete or loaded measurement is NOT DONE or NO-DATA, never
a pass. A performance claim is exactly the kind that reads plausible while being worthless."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from scripts.perf_audit_classify import QUIET_LOAD  # noqa: E402  the one quiet bar, shared with the classifier and the capture wait
EVIDENCE = os.path.join(ROOT, "docs", "architecture", "PERF-AUDIT-L5D.json")
CAUSES = ("machine-contention", "code-regression", "stale-target", "mixed")
NEEDED = ("root_cause", "measured_seconds", "load_at_measurement", "cores",
          "documented_target_seconds", "commands", "finding")


def judge(ev, quiet=QUIET_LOAD):
    """(ok, [why not]). Every shortfall is collected, not just the first, so one run names the whole gap."""
    bad = []
    missing = [k for k in NEEDED if k not in ev]
    if missing:
        return False, ["names no %s" % ", ".join(missing)]
    if ev["root_cause"] not in CAUSES:
        bad.append("root_cause %r is not one of %s" % (ev["root_cause"], ", ".join(CAUSES)))
    for k in ("measured_seconds", "load_at_measurement", "cores", "documented_target_seconds"):
        v = ev.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            bad.append("%s is not a number" % k)
    cmds = ev.get("commands")
    if not isinstance(cmds, list) or len(cmds) < 2:
        bad.append("fewer than 2 commands recorded, so the measurement cannot be reproduced")
    else:
        for i, c in enumerate(cmds):
            if not (isinstance(c, dict) and str(c.get("command", "")).strip()
                    and str(c.get("output", "")).strip()):
                bad.append("command %d records no command and output pair" % i)
    if not str(ev.get("finding", "")).strip():
        bad.append("the finding is empty, and a cause with no finding is a label")
    if "probably" in str(ev.get("finding", "")).lower():
        bad.append("the finding says 'probably', which is the guess this unit exists to replace")
    if bad:
        return False, bad
    load, cores = ev["load_at_measurement"], ev["cores"]
    if load > quiet:
        return False, ["the timing was taken at load %.1f on %d core(s), above the quiet bar of %.1f, so it "
                       "measures the neighbours and not the gate" % (load, cores, quiet)]
    return True, []


def selftest():
    good = {"root_cause": "machine-contention", "measured_seconds": 95.0, "load_at_measurement": 1.2,
            "cores": 8, "documented_target_seconds": 90.0, "finding": "the gate runs at 95 s on a quiet machine",
            "commands": [{"command": "uptime", "output": "load averages: 1.2"},
                         {"command": "time bash scripts/required_fast.sh", "output": "real 1m35s"}]}
    loaded = dict(good, load_at_measurement=26.4)
    hedged = dict(good, finding="probably load")
    thin = dict(good, commands=[{"command": "uptime", "output": "1.2"}])
    cases = [
        ("a complete quiet measurement passes", judge(good)[0]),
        ("a timing taken under load is refused", not judge(loaded)[0]),
        ("the refusal names the load and the bar", "quiet bar" in " ".join(judge(loaded)[1])),
        ("a hedged finding is refused", not judge(hedged)[0]),
        ("fewer than two commands is refused", not judge(thin)[0]),
        ("a missing field is named", "names no" in " ".join(judge({"root_cause": "mixed"})[1])),
        ("an unknown cause is refused", not judge(dict(good, root_cause="vibes"))[0]),
        ("a non numeric time is refused", not judge(dict(good, measured_seconds="fast"))[0]),
        ("a boolean is not a number", not judge(dict(good, cores=True))[0]),
        ("every shortfall is collected, not just the first",
         len(judge(dict(good, root_cause="vibes", finding=""))[1]) >= 2),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--evidence", default=EVIDENCE)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    try:
        with open(a.evidence, encoding="utf-8") as fh:
            ev = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: the audit evidence could not be read (%s); this is not a pass" % exc)
        print("         write %s with: %s" % (a.evidence, ", ".join(NEEDED)))
        return 2
    ok, why = judge(ev)
    print("EVIDENCE %s" % a.evidence)
    if ok:
        print("MEASURED %.1f s against a documented %.1f s, at load %.1f on %d core(s)"
              % (ev["measured_seconds"], ev["documented_target_seconds"],
                 ev["load_at_measurement"], ev["cores"]))
        print("CAUSE    %s" % ev["root_cause"])
        print("DONE     the cause is named, and the timing was taken on a quiet machine")
        return 0
    for w in why:
        print("  SHORT   %s" % w)
    print("NOT DONE L5d needs a cause named on a measurement that can carry it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
