#!/usr/bin/env python3
"""Check how much CLOCK TIME is left in the current 5-hour usage block.

SCOPE CORRECTED 2026-09-18: this is NOT token headroom. It said GO with 232
minutes left on a morning the limit hit mid-block, and past hits span $175 to
$719 of block cost, so no cap is learnable from these logs. For the account's
real percent used per window, a live session calls the desktop app's
get_usage tool and passes it to scripts/limit_preempt.py (LIMIT-03). This
script stays right for what restart.sh uses it for: is a block open.

Original purpose, kept:

Built 2026-09-14 after a real failure: mid-session, a Claude Code account
usage-limit block was hit and the session was cut off, right after telling
the founder "plenty of tokens left". That number was the CONTEXT WINDOW
remaining, a completely different resource from the account-level usage
quota that actually cut the session. This script exists so that distinction
never gets silently conflated again: it reads the real usage-limit block via
`ccusage` (github.com/ryoppippi/ccusage, reads local Claude Code usage logs),
never the context-window count, and gives one plain verdict.

Usage: python3 usage_check.py [--min-remaining-minutes N]

Exit 0 with GO: the current usage block has enough time left.
Exit 1 with CAUTION or STOP: it does not, or the check itself failed, in
which case this prints NO-DATA rather than a guess. NO-DATA is not a pass.
"""
import argparse
import re
import subprocess
import sys


def parse_block_output(text):
    """Extract time-remaining minutes and burn rate from ccusage's own
    plain-text block report. Returns (minutes_remaining, tokens_per_min) or
    (None, None) if the shape does not match, which the caller reports as
    NO-DATA rather than inventing a number."""
    m = re.search(r"Time Remaining:\s*(?:(\d+)h\s*)?(\d+)m", text)
    minutes = None
    if m:
        hours = int(m.group(1)) if m.group(1) else 0
        mins = int(m.group(2))
        minutes = hours * 60 + mins
    rate = None
    r = re.search(r"Tokens/minute:\s*([\d,]+)", text)
    if r:
        rate = int(r.group(1).replace(",", ""))
    return minutes, rate


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--min-remaining-minutes", type=int, default=30,
                    help="below this, verdict is CAUTION or STOP")
    args = p.parse_args(argv)

    try:
        proc = subprocess.run(
            ["npx", "ccusage@latest", "blocks", "--active"],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        print("NO-DATA: could not run ccusage: %s" % e, file=sys.stderr)
        return 1

    if proc.returncode != 0 or not proc.stdout.strip():
        print("NO-DATA: ccusage exited %s with no usable output "
              "(no active block, or the tool itself is broken); this is not "
              "the same as 'plenty of budget', it means unknown" % proc.returncode,
              file=sys.stderr)
        return 1

    minutes, rate = parse_block_output(proc.stdout)
    if minutes is None:
        print("NO-DATA: could not parse a Time Remaining line from ccusage's "
              "own output, so no verdict is given", file=sys.stderr)
        print(proc.stdout)
        return 1

    print(proc.stdout)
    if minutes < args.min_remaining_minutes:
        print("STOP: %d minute(s) left in the current usage block, under "
              "the %d minute floor. Do not promise a deadline that assumes "
              "this session keeps running past the block boundary." %
              (minutes, args.min_remaining_minutes))
        return 1
    print("GO: %d minute(s) left in the current usage block." % minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
