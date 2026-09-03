"""forecast: how long, from a base rate this estate actually produced.

THE QUESTION, asked 2026-08-29: at this pace, how long to reach parity, and how
long to finish the readiness board.

THE ANSWER IS NOT A DIVISION, and the tempting division is why this is a file
rather than a sentence. Three parity capabilities landed six minutes apart
tonight. Dividing the remaining work by that rate gives about forty minutes to
parity, which is a fantasy, and it is the exact fantasy that reference class
forecasting exists to prevent: an estimate made from inside a problem is
systematically optimistic, and the inside view here is a stretch of WIRING work
being read as though it predicted BUILDING work.

WHY THE SIX MINUTES IS NOT THE BASE RATE. Those three capabilities connected
pieces that already existed and already had tests: the scope audit had twelve,
the dispatch loop had thirty nine, the verifier and repair loop were both on a
sibling project's main. Connecting tested things is fast. Two of the remaining
cells have nothing at all behind them.

THE BASE RATE THIS USES INSTEAD, from a real sample rather than a feeling: ten
modules were built from zero tonight with tests, calibration and a landed commit,
across about eleven working hours. That is roughly one hour and ten minutes each,
n equals ten, measured on this estate by this operator on this kind of work. It
is the closest reference class available and it is still optimistic, because
tonight was one uninterrupted stretch.

WHAT THE MODEL ADDS ON TOP, and each is arguable on purpose:

  DIFFICULTY, as a multiplier per piece of work rather than a flat rate. Serial
  integration is not one hour of work in any world.

  COUPLING, which is the finding that changes the answer most. Seven of the nine
  cells below the gate are not seven pieces of work. Five of them are waiting on
  the SAME two things, so they move together when those land, and counting them
  separately triples the estimate.

  A RANGE, never a number. The spread is the honest part.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import parity_gate as PG  # noqa: E402

NODATA = "NO-DATA"

#: Measured, not chosen: ten modules built from zero tonight with tests,
#: calibration and a landed commit, over about eleven working hours.
BASE_HOURS_PER_MODULE = 1.1
BASE_SAMPLE = 10

#: The optimism correction. Software estimates from inside a task run about two
#: to three times short in the published literature and in this estate's own
#: slip log, so the range is the base rate multiplied by these.
OPTIMISM_LOW, OPTIMISM_HIGH = 1.5, 3.0

#: The work that actually stands between here and the gate, with what each one
#: unlocks. This is the part worth arguing with: change a multiplier or move a
#: cell between pieces and the arithmetic follows.
PIECES = (
    {"name": "An outcome becomes canonical Work",
     "difficulty": 2.5,
     "why": ("The product path stops at a plan that already exists. Nothing "
             "turns a sentence a person typed into units the scheduler can "
             "read, which is the first link of the acceptance test."),
     "unlocks": ["Live autonomous execution", "Physical isolation",
                 "Scope auditing", "Parallel scheduling",
                 "Verification and repair"]},
    {"name": "Serial canonical integration, with advancing-base revalidation",
     "difficulty": 5.0,
     "why": ("Nothing exists. It has to apply a worker's result against the "
             "CURRENT canonical revision and re-verify there, because a clean "
             "merge is not semantic compatibility. The hardest piece on the "
             "list and the one most likely to be underestimated."),
     "unlocks": ["Serial integration"]},
    {"name": "Crash and resume proof",
     "difficulty": 2.5,
     "why": ("The claim store already reconciles and reports abandoned claims, "
             "so the state exists. What is missing is the proof: kill a "
             "controller with workers running, restart, and show no duplicate "
             "claim, no lost result and no rerun closed unit."),
     "unlocks": ["Crash recovery"]},
    {"name": "A real model worker end to end from a clean install",
     "difficulty": 3.0,
     "why": ("A seeded Python worker proves the wiring and earns no parity "
             "credit by the directive's own rule. Until a real coding worker "
             "does real multi-file work, several L2 cells cannot honestly "
             "become L3 whatever else is wired."),
     "unlocks": ["Adaptive planning", "Decomposition"]},
)


def hours(low_mult=OPTIMISM_LOW, high_mult=OPTIMISM_HIGH, pieces=PIECES,
          levels=None):
    """(low, high, rows). Working hours, not calendar hours.

    When ``levels`` (capability name -> level, read from the parity evidence)
    is given, a piece whose EVERY unlocked cell already reads level 3 or
    better is LANDED: it contributes zero hours and its row says so. This is
    the 2026-09-01 recalibration: the table had kept billing pieces the
    parity evidence said were built (FINDING-canonical-work-gate-already-
    landed-2026-09-01.md), so the status is now derived from the same file
    parity_gate grants levels from, never asserted here by hand.
    """
    rows, lo, hi = [], 0.0, 0.0
    for p in pieces:
        landed = bool(levels) and bool(p["unlocks"]) and all(
            (levels.get(c) or 0) >= 3 for c in p["unlocks"])
        base = 0.0 if landed else BASE_HOURS_PER_MODULE * p["difficulty"]
        a, b = base * low_mult, base * high_mult
        lo += a
        hi += b
        rows.append(dict(p, low=a, high=b, landed=landed))
    return lo, hi, rows


def board_remaining(doc):
    """What is left on the board, counted rather than characterised."""
    import board_status as BS
    out = []
    for sec in BS.sections(doc):
        c = sec["counts"]
        out.append({"section": sec["label"], "open": c["open"] + c["in_flight"],
                    "done": c["done"], "total": sec["total"]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours-per-day", type=float, default=6.0,
                    help="working hours actually available per day")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(PG.SOURCE, encoding="utf-8") as fh:
            pdoc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: %s" % (NODATA, exc), file=sys.stderr)
        return 2
    caps = pdoc.get("capabilities") or []
    pct, _rows, blocking = PG.score(caps)
    levels = {c.get("capability"): c.get("level") for c in caps}
    lo, hi, pieces = hours(levels=levels)

    if args.json:
        print(json.dumps({"parity": pct, "low_hours": lo, "high_hours": hi,
                          "pieces": pieces}, indent=2, sort_keys=True))
        return 0

    print("PARITY NOW: %.0f%%, with %d critical capability(ies) below the gate"
          % (pct, len(blocking)))
    print("")
    print("THE BASE RATE, measured rather than chosen: %d modules were built from"
          % BASE_SAMPLE)
    print("zero tonight with tests, calibration and a landed commit, in about")
    print("eleven working hours. That is %.1f hours each. It is the closest"
          % BASE_HOURS_PER_MODULE)
    print("reference class available and it is still optimistic, because tonight")
    print("was one uninterrupted stretch.")
    print("")
    open_pieces = [p for p in pieces if not p.get("landed")]
    landed_pieces = [p for p in pieces if p.get("landed")]
    if open_pieces:
        print("WHAT STANDS BETWEEN HERE AND THE GATE, and the coupling is the")
        print("part that changes the answer: cells that wait on ONE piece are")
        print("counted once, so counting them separately would inflate this.")
    else:
        print("WHAT STANDS BETWEEN HERE AND THE GATE: nothing this table still")
        print("models. Every piece below is LANDED, granted by the same parity")
        print("evidence file the gate reads levels from, never asserted here.")
    print("")
    for p in pieces:
        if p.get("landed"):
            print("  %-52s LANDED, 0 h" % p["name"][:52])
            print("      every cell it unlocks reads L3+ in %s"
                  % os.path.basename(PG.SOURCE))
        else:
            print("  %-52s %4.0f to %3.0f h"
                  % (p["name"][:52], p["low"], p["high"]))
        print("      unlocks: %s" % ", ".join(p["unlocks"]))
    print("")
    print("  %-52s %4.0f to %3.0f h" % ("TOTAL WORKING HOURS TO THE GATE", lo, hi))
    print("  %-52s %4.0f to %3.0f days at %.0f h/day"
          % ("", lo / args.hours_per_day, hi / args.hours_per_day,
             args.hours_per_day))
    print("")
    print("WHAT WOULD MAKE THIS WRONG, in the direction it is most likely to be:")
    if any(p["name"].startswith("Serial canonical integration")
           for p in open_pieces):
        print("  * Serial integration is the piece nobody estimates correctly. If")
        print("    it alone doubles, the high end moves by about %.0f hours."
              % (BASE_HOURS_PER_MODULE * 5.0 * OPTIMISM_HIGH))
    if any(p["name"].startswith("A real model worker") for p in open_pieces):
        print("  * The gate needs a REAL coding worker, not the seeded one. If")
        print("    that turns out to need its own harness, it is a piece of its")
        print("    own.")
    if landed_pieces:
        print("  * A LANDED row above is only as good as the evidence behind its")
        print("    parity levels; if a level was granted on evidence that later")
        print("    fails, the piece reopens and these hours return.")
    print("  * Nothing here counts the parity BENCHMARK against the incumbents,")
    print("    which the directive requires before inviting anyone, because that")
    print("    is measurement rather than building and has no base rate here.")
    print("    It is %s, not zero." % NODATA)

    # THE SECOND QUESTION: the whole board, not just the gate.
    try:
        import board_status as BS
        with open(BS.SOURCE, encoding="utf-8") as fh:
            bdoc = json.load(fh)
        secs = BS.sections(bdoc)
    except Exception as exc:  # noqa: BLE001
        print("\n%s: the board could not be read (%s), so nothing is forecast "
              "for it" % (NODATA, exc), file=sys.stderr)
        return 2

    print("")
    print("=" * 66)
    print("AND THE WHOLE BOARD, which is a different question with a worse answer")
    print("")
    total_open = 0
    for sec in secs:
        c = sec["counts"]
        open_n = c["open"] + c["in_flight"]
        total_open += open_n
        print("  %-22s %3d open of %3d   (%d done)"
              % (sec["label"], open_n, sec["total"], c["done"]))
    print("  %-22s %3d open" % ("TOTAL", total_open))
    print("")
    # THE OVERLAP MATTERS AND IS SUBTRACTED. Several board items ARE the parity
    # pieces above; counting both would bill the same work twice.
    overlap = 9
    net = total_open - overlap
    print("Roughly %d of those open items ARE the parity pieces already counted"
          % overlap)
    print("above, so billing both would charge the same work twice. Net: %d." % net)
    print("")
    blo = net * BASE_HOURS_PER_MODULE * OPTIMISM_LOW
    bhi = net * BASE_HOURS_PER_MODULE * OPTIMISM_HIGH
    print("  %-40s %5.0f to %4.0f h" % ("EVERYTHING ELSE ON THE BOARD", blo, bhi))
    print("  %-40s %5.0f to %4.0f days at %.0f h/day"
          % ("", blo / args.hours_per_day, bhi / args.hours_per_day,
             args.hours_per_day))
    print("")
    print("  %-40s %5.0f to %4.0f h" % ("GATE PLUS BOARD", lo + blo, hi + bhi))
    print("  %-40s %5.0f to %4.0f weeks at %.0f h/day, 5 days"
          % ("", (lo + blo) / (args.hours_per_day * 5),
             (hi + bhi) / (args.hours_per_day * 5), args.hours_per_day))
    print("")
    print("READ THAT SECOND NUMBER WITH MORE SUSPICION THAN THE FIRST. The gate")
    print("estimate rests on four named pieces whose shape is known. This one")
    print("multiplies a COUNT by an average, which assumes the remaining items")
    print("resemble the ones already done, and the easy ones go first, always.")
    print("Nine of the open items are holes in a sibling repository that were")
    print("found by a sweep and never scoped at all.")
    print("")
    print("THE NUMBER THAT MATTERS IS NEITHER OF THESE. Finishing the board is")
    print("not the goal and never was: the gate is the price of admission and")
    print("the north star is accepted external verified deliveries per week,")
    print("which still reads zero. A finished board with that number at zero")
    print("would be a completed plan and a failed product.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
