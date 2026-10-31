#!/usr/bin/env python3
"""J2's D.end-card-six-lines: the card a stranger actually sees, measured.

Until this existed the row carried `measured_by: CARD1` and no check, so it read "the
row declares no check, so nothing measured it" forever and CARD1 could never close.
A row with no check is a unit that cannot land.

WHAT THE BAR IS, from CARD1's own spec (docs/plan/specs/1.1.9/CARD1.md): the end card
printed after the last check is at most 6 LINES and 720 BYTES, plus 120 bytes per parked
piece, one line per piece and never one per file. `render_short` returns that card,
brother_run prints it by default, and the 19 row card moves behind --full.

WHICH TEXT THIS MEASURES, and why it is not the CLI's output. The card a stranger sees
by default is `render_short` once CARD1 lands, and `render_text` until then. So this asks
the INSTALLED module for render_short and falls back to render_text, rather than running
`proof_card.py <run_dir>`, whose own default this unit's spec does not change. Measuring
the CLI would keep reading the long card after CARD1 landed and FAIL a unit that was
built correctly.

MEASURED 2026-10-29 on this tree: 20 lines and 534 bytes, so the row FAILS on lines and
passes on bytes. The row's own text says "Measured by hand on 1.1.7: 120 lines"; this
disagrees and 20 is what the code gives, because render_text is the title plus one line
per CARD_ORDER entry and CARD_ORDER holds 19. The 120 is not reproduced here and is not
repeated as if it were.

NO PARKED PIECES IN THIS MEASUREMENT. The run directory handed to the renderer is empty,
which is a stranger's first run and the strictest reading of the bar: 6 lines and the
base 720 bytes, with no per-piece allowance to hide behind.

    python3 -B scripts/journey/check_end_card.py
    python3 -B scripts/journey/check_end_card.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import importlib.util
import os
import sys
import tempfile

TOOL = "proof_card.py"
MAX_LINES = 6
MAX_BYTES = 720
BYTES_PER_PARKED = 120


def installed_tool(config):
    """The installed card renderer's path, or None. Found BY NAME under the sandbox's
    config dir, the same rule the other journey checks use."""
    for base, dirs, files in os.walk(config):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if TOOL in files:
            return os.path.join(base, TOOL)
    return None


def judge(card, parked=0):
    """(exit code, one line) for a rendered card. Pure, so the selftest judges THIS and
    not a copy of it.

    A card that could not be rendered is NO-DATA: a card nobody could look at has not
    been shown to be short. Both bars are reported every time, whichever one failed, so
    a reader never has to re-measure to find out which."""
    if card is None:
        return 2, ("NO-DATA: the installed card renderer could not be asked for a card, "
                   "so nothing has been shown about what a stranger sees")
    lines = card.splitlines()
    nbytes = len(card.encode("utf-8"))
    allowed = MAX_BYTES + BYTES_PER_PARKED * parked
    over = []
    if len(lines) > MAX_LINES:
        over.append("%d lines against a bar of %d" % (len(lines), MAX_LINES))
    if nbytes > allowed:
        over.append("%d bytes against a bar of %d" % (nbytes, allowed))
    if over:
        return 1, ("FAIL: the end card a stranger sees by default is %s, so the first "
                   "thing it says after the last check is a wall rather than a result"
                   % " and ".join(over))
    return 0, ("PASS: the end card a stranger sees by default is %d line(s) and %d "
               "byte(s), inside the bar of %d lines and %d bytes"
               % (len(lines), nbytes, MAX_LINES, allowed))


def default_card(tool):
    """The text a stranger sees by default, or None.

    render_short when the installed module offers it, render_text otherwise: that is the
    default this unit moves, and asking the module rather than the CLI is what keeps this
    row honest after CARD1 lands."""
    if tool is None:
        return None
    try:
        directory = os.path.dirname(tool)
        if directory not in sys.path:
            sys.path.insert(0, directory)
        spec = importlib.util.spec_from_file_location("installed_proof_card", tool)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run_dir = tempfile.mkdtemp(prefix="end-card-run-")
        fields = mod.build_card(run_dir)
        render = getattr(mod, "render_short", None) or getattr(mod, "render_text", None)
        if render is None:
            return None
        return render(fields)
    except Exception:            # an unloadable renderer is NO-DATA, never a pass
        return None


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    short = "RESULT ok\nproven 3\nNO-DATA 0\nreceipt r.json"
    long_card = "\n".join("row %d" % i for i in range(20))
    case("a four line card is PASS", judge(short)[0], 0)
    case("a twenty line card is FAIL", judge(long_card)[0], 1)
    case("exactly six lines is PASS", judge("\n".join("r" for _ in range(6)))[0], 0)
    case("seven lines is FAIL", judge("\n".join("r" for _ in range(7)))[0], 1)
    case("a short card over 720 bytes is FAIL", judge("x" * 721)[0], 1)
    case("exactly 720 bytes is PASS", judge("x" * 720)[0], 0)
    case("a parked piece raises the byte bar", judge("x" * 800, parked=1)[0], 0)
    case("one parked piece does not raise it twice",
         judge("x" * 900, parked=1)[0], 1)
    case("an unrenderable card is NO-DATA", judge(None)[0], 2)
    # A card that is short AND small must not be failed by the line bar counting a
    # trailing newline as a line: splitlines does not, and this pins it.
    case("a trailing newline is not a seventh line",
         judge("\n".join("r" for _ in range(6)) + "\n")[0], 0)

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
    if not args.config or not os.path.isdir(args.config):
        print("NO-DATA: CLAUDE_CONFIG_DIR %r is not a directory, so no installed plugin "
              "can be read and nothing has been shown about the end card" % args.config)
        return 2
    tool = installed_tool(args.config)
    if tool is None:
        print("FAIL: no %s in the installed plugin, so a stranger who finishes a run is "
              "shown no end card at all" % TOOL)
        return 1
    code, line = judge(default_card(tool))
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
