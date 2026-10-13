#!/usr/bin/env python3
"""THE ONE POWER RULE, read by the launcher before the intake and by the driver before its hold (LA3.a, 2026-10-12).

WHY IT IS ONE FILE. The rule lived only inside loop_until.sh, so a battery start with a far deadline ran the whole two
minute intake and was refused by the driver four seconds after it (driver log of 2026-10-11 14:35:48). start_run.sh
now asks this module first and refuses in under a second; the driver asks the SAME module, so the two can never
disagree, and a charger pulled between the two readings is caught by the driver's own reading.

THE RULE, moved from loop_until.sh without a change of meaning (its message texts are the contract
scripts/test_loop_until_lifecycle.py reads): without AC nothing starts unless the deadline is within
BROTHER_POWER_WINDOW_S (default 1200 s, read in base ten; a value that is not a plain number leaves the default).
UNREADABLE REFUSES EXACTLY LIKE BATTERY: only 'AC Power' is known to last. NO-DATA DOES NOT BLOCK: a kernel name that
was read and is not Darwin has no pmset; on Darwin a missing or silent pmset is unreadable, and a kernel name that
cannot be read is unreadable too. BROTHER_POWER_SOURCE (when not empty) stands in for the words pmset prints between
its quotes. `uname` and `pmset` are looked up on PATH, so a test's shims answer for them.

    power_rule.py check --remaining SECONDS       the verdict for a deadline that many seconds away
    power_rule.py check --deadline "YYYY-MM-DD HH:MM"   the same for a local deadline
    power_rule.py source                          one word: ac, battery, unreadable or no-data

check prints the text and exits 0 (start) or 2 (refuse); an argument it cannot read prints
`the deadline could not be read` and exits 2: an unknown never reads as safe.
Python 3.9, standard library only. Tests: scripts/loop/test_start_run.py.
"""
import os
import re
import subprocess
import sys
import time
from typing import Callable, List, Optional, Tuple

DEFAULT_WINDOW_S = 1200
UNREADABLE_ARG = "the deadline could not be read"


def window_s(env: Optional[dict] = None) -> int:
    v = (os.environ if env is None else env).get("BROTHER_POWER_WINDOW_S", "")
    return int(v, 10) if re.fullmatch(r"[0-9]+", v or "") else DEFAULT_WINDOW_S


def _run(cmd: List[str]) -> Optional[str]:
    """stdout of cmd whatever its exit code (the shell's $(...) read it that way), None when it cannot start."""
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def power_source(env: Optional[dict] = None, run: Optional[Callable] = None) -> str:
    env = os.environ if env is None else env
    run = run or _run
    p = env.get("BROTHER_POWER_SOURCE", "") or ""
    if not p:
        kernel = (run(["uname", "-s"]) or "").strip()
        if kernel == "Darwin":
            lines = (run(["pmset", "-g", "batt"]) or "").splitlines()
            found = re.findall(r"drawing from '([^']*)'", lines[0]) if lines else []
            p = found[-1] if found else ""
        elif kernel:
            return "no-data"
    return {"AC Power": "ac", "Battery Power": "battery"}.get(p, "unreadable")


def verdict(source: str, remaining_s: int, window: int) -> Tuple[int, str]:
    if source == "ac":
        return 0, ""
    if source == "no-data":
        return 0, "POWER NO-DATA: this host is not macOS, so there is no pmset to read; the power check does not block"
    if remaining_s > window:
        if source == "battery":
            return 2, ("the machine draws from battery and the deadline is %d minutes away, more than the %d minutes a "
                       "battery start is allowed; a low battery sleep ends the run and no caffeinate holds it: plug in "
                       "the charger" % (remaining_s // 60, window // 60))
        return 2, ("the power source is unreadable (pmset named neither AC Power nor Battery Power) and the deadline is "
                   "%d minutes away, more than the %d minutes a start without AC is allowed; an unknown source is never "
                   "AC" % (remaining_s // 60, window // 60))
    return 0, "POWER %s: allowed to start because the deadline is within %d s" % (source, window)


def _remaining(argv: List[str]) -> Optional[int]:
    if len(argv) != 2:
        return None
    flag, value = argv
    if flag == "--remaining":
        return int(value, 10) if re.fullmatch(r"-?[0-9]+", value) else None
    if flag == "--deadline" and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}", value):
        try:
            return int(time.mktime(time.strptime(value, "%Y-%m-%d %H:%M")) - time.time())
        except (ValueError, OverflowError):
            return None
    return None


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["source"]:
        print(power_source())
        return 0
    if argv[:1] == ["check"]:
        remaining = _remaining(argv[1:])
        if remaining is None:
            print(UNREADABLE_ARG)
            return 2
        rc, text = verdict(power_source(), remaining, window_s())
        if text:
            print(text)
        return rc
    print("usage: power_rule.py check --remaining S | check --deadline \"YYYY-MM-DD HH:MM\" | source")
    return 2


if __name__ == "__main__":
    sys.exit(main())
