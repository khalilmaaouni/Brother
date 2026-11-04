#!/usr/bin/env python3
"""J1's D.no-internal-names: no session start line a stranger sees names an internal product.

Until this existed the row carried only `measured_by: FRUN1.f` and no check, so it read NO-DATA however many
internal names the first screen printed (measured 2026-10-25: three, in sbe_telemetry.py).

WHAT IT READS. The text the INSTALLED SessionStart hooks print, run the way the host runs them, through the one
helper scripts/journey/check_install_paths.py uses for the same text, so the two rows can never read different
first screens. The person installed one plugin, "brother"; the products behind it (BrotherMode, BrotherSBE,
BrotherDS) are internal names (README: "the internal product names"), so any line naming one fails, a path
spelling one included, because a path is text the person reads too.

FAIL DIRECTION. No install or a start text that cannot be read: NO-DATA, exit 2, never a pass.

    python3 -B scripts/journey/check_no_internal_names.py
    python3 -B scripts/journey/check_no_internal_names.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_install_paths import installed_root, session_start_lines  # noqa: E402

INTERNAL = re.compile(r"brother(?:mode|sbe|ds)", re.IGNORECASE)


def judge(printed, why=""):
    """(exit code, one line) for the start text. Pure, so the selftest judges THIS and not a copy."""
    if printed is None:
        return 2, "NO-DATA: the session start text could not be read (%s), so nothing was shown either way" % why
    hits = [ln.strip() for ln in printed.splitlines() if INTERNAL.search(ln)]
    if hits:
        return 1, ("FAIL: %d session start line(s) name an internal product, which a stranger never installed: %s"
                   % (len(hits), " | ".join(h[:90] for h in hits[:3])))
    return 0, ("PASS: none of the %d session start line(s) names an internal product"
               % len([ln for ln in printed.splitlines() if ln.strip()]))


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    case("a clean start text is PASS", judge("Brother is ready.\n")[0], 0)
    case("an empty start text is PASS, nothing named", judge("")[0], 0)
    case("BrotherMode in a line is FAIL", judge("BrotherMode setup is not complete\n")[0], 1)
    case("an upper case product name is FAIL", judge("BROTHERSBE UPDATE: skipped\n")[0], 1)
    case("a path spelling a product is FAIL", judge("see runtime/hooks/brotherds/x\n")[0], 1)
    case("the plugin's own name is not internal", judge("brother: 1 receipt\n")[0], 0)
    case("an unreadable start text is NO-DATA", judge(None, "boom")[0], 2)
    bad = 0
    for label, want, got, ok in cases:
        bad += 0 if ok else 1
        print("%-52s want %d got %d %s" % (label, want, got, "ok" if ok else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = installed_root(args.config)
    if root is None:
        print("NO-DATA: no installed plugin under %r, so no session start text could be read" % args.config)
        return 2
    printed, why = session_start_lines(root, os.environ.get("HOME", ""))
    code, line = judge(printed, why)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
