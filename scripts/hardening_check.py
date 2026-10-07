#!/usr/bin/env python3
"""Run every declared subunit check for one hardening unit, failing closed."""
import argparse
from pathlib import Path
import re
import shlex
import subprocess
import sys

SPEC = Path(__file__).resolve().parents[1] / "docs/plan/specs/H.md"


def check(unit, spec, cwd=None):
    """Execute every matching section; missing or ambiguous evidence fails."""
    if not re.fullmatch(r"H[1-9][0-9]*", unit):
        print("FAIL invalid hardening unit")
        return 1
    headings = list(re.finditer(r"^## (H[0-9]+\.[a-zA-Z0-9]+)\b[^\n]*$", spec, re.M))
    sections = [(m.group(1), spec[m.end():headings[i+1].start() if i+1 < len(headings) else len(spec)])
                for i, m in enumerate(headings) if m.group(1).split(".")[0] == unit]
    if not sections:
        print("FAIL %s has no declared subunit checks" % unit)
        return 1
    seen = set(); failed = False
    for sid, body in sections:
        commands = re.findall(r"^Done check:[ \t]*\n\s*```text\n([^`]+)\n```", body, re.M)
        if sid in seen or len(commands) != 1:
            print("FAIL %s missing or ambiguous done check" % sid); failed = True; continue
        seen.add(sid)
        command = commands[0].strip()
        if not re.fullmatch(r"python3 (?:-B )?(?:scripts/(?:loop/)?test_[\w]+\.py|scripts/loop/[\w]+\.py --selftest)(?: [A-Za-z_][\w.]*)?(?: -v)?", command):
            print("FAIL %s unparseable done check: %s" % (sid, command)); failed = True; continue
        argv = shlex.split(command); argv[0] = sys.executable
        print("CHECK %s: %s" % (sid, shlex.join(argv)), flush=True)
        try:
            code = subprocess.run(argv, cwd=cwd, timeout=900).returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("FAIL %s check could not finish: %s" % (sid, type(exc).__name__)); failed = True; continue
        print("%s %s exit %d" % ("PASS" if code == 0 else "FAIL", sid, code), flush=True)
        failed = failed or code != 0
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unit")
    args = parser.parse_args(argv)
    try:
        spec = SPEC.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print("FAIL hardening spec unreadable: %s" % type(exc).__name__)
        return 1
    return check(args.unit, spec, cwd=SPEC.parents[3])


if __name__ == "__main__":
    sys.exit(main())
