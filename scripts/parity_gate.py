"""parity_gate: is Brother yet worth a teammate's time, measured at the level the evidence supports.

THE CONSTRAINT THIS SERVES, from an outside reviewer 2026-08-29 and it is the
sharpest framing anyone has offered: the team will not seriously look at Brother
while it appears weaker than the tools they already use. So the delivery number
reads zero partly because nobody will try a thing that feels like a downgrade.
Parity is not success; it is the price of admission.

WHY THIS IS A TOOL RATHER THAN A TABLE. The directive proposed a parity board
with numbers on it: autonomous execution 82 percent, worker isolation 78. Those
numbers came from nowhere. A percentage nobody can trace is the most flatterable
object on a status page, and this estate spent tonight building a guard against
exactly that. So every cell here carries a LEVEL, and a level is only granted
when a named piece of evidence supports it.

THE LEVELS, taken from the directive because its hierarchy is the good part:

  L0 DOCUMENTED       a page describes it. NO credit.
  L1 IMPLEMENTED      code exists. Limited credit.
  L2 PROVEN IN A SLICE a controlled proof passes. Partial credit.
  L3 ON THE PRODUCT PATH  the normal /brother path does it. Parity candidate.
  L4 SURVIVES ADVERSITY   it holds through realistic failure and recovery. Full.

A cell with no evidence is NO-DATA and scores ZERO, not a guess. That is the
difference between this and the table it replaces: an unassessed capability
lowers the score instead of quietly inheriting an optimistic one.

THE WEIGHTS are the directive's own, unchanged, because they encode a judgement
worth keeping: live autonomous execution is worth nearly four times status and
evidence, so a support feature cannot move the gate the way execution does.

IT REPORTS, IT DOES NOT DECIDE. Whether to invite anyone is the founder's call.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "docs", "plan", "PARITY-2026-08-29.json")
NODATA = "NO-DATA"

#: What each level is worth. L3 is the first level that means a teammate would
#: actually meet the capability, so the curve is deliberately steep between L2
#: and L3: a proof in a controlled slice is not a product.
LEVEL_CREDIT = {0: 0.0, 1: 0.25, 2: 0.50, 3: 0.85, 4: 1.0}
LEVEL_NAME = {
    0: "documented only",
    1: "code exists",
    2: "proven in a slice",
    3: "on the product path",
    4: "survives adversity",
}


def credit(cell):
    """(fraction, note). A cell without evidence earns nothing and says why."""
    lvl = cell.get("level")
    if lvl is None:
        return 0.0, ("%s: no level assessed, so it scores zero rather than an "
                     "optimistic guess" % NODATA)
    if not str(cell.get("evidence") or "").strip():
        return 0.0, ("%s: level %s is claimed with no evidence named, so it "
                     "scores zero. A level is granted by evidence, never by "
                     "assertion" % (NODATA, lvl))
    if lvl not in LEVEL_CREDIT:
        return 0.0, ("%s: level %r is not one of 0 to 4" % (NODATA, lvl))
    return LEVEL_CREDIT[lvl], ""


def score(cells):
    """(percent, rows, blocking). Weighted by the directive's own weights."""
    total_w = sum(float(c.get("weight", 0)) for c in cells)
    if total_w <= 0:
        return None, [], []
    rows, earned = [], 0.0
    for c in cells:
        frac, note = credit(c)
        w = float(c.get("weight", 0)) / total_w
        earned += frac * w
        rows.append({"capability": c.get("capability"), "level": c.get("level"),
                     "weight": w, "credit": frac, "contributes": frac * w,
                     "note": note, "evidence": c.get("evidence", ""),
                     "critical": bool(c.get("critical")),
                     "incumbent": c.get("incumbent", "")})
    blocking = [r for r in rows
                if r["critical"] and (r["level"] is None or r["level"] < 3)]
    return 100.0 * earned, rows, blocking


def bar(pct, width=26):
    if pct is None:
        return "[%s] %s" % ("?" * width, NODATA)
    f = int(round(width * pct / 100.0))
    return "[%s%s] %3.0f%%" % ("#" * f, "." * (width - f), pct)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.source, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: could not read %s: %s" % (NODATA, args.source, exc),
              file=sys.stderr)
        return 2

    pct, rows, blocking = score(doc.get("capabilities") or [])
    if pct is None:
        print("%s: no capability carries a weight, so no gate can be computed"
              % NODATA, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"parity": pct, "rows": rows,
                          "blocking": [b["capability"] for b in blocking]},
                         indent=2, sort_keys=True))
        return 0

    print("TEAM ADOPTION GATE")
    print("Critical workflow parity: %s" % bar(pct))
    print("")
    for r in sorted(rows, key=lambda x: (x["level"] if x["level"] is not None else -1)):
        lvl = ("L%d %s" % (r["level"], LEVEL_NAME[r["level"]])
               if r["level"] in LEVEL_NAME else NODATA)
        print("  %-24s %-22s %5.1f%% weight%s"
              % (str(r["capability"])[:24], lvl, 100 * r["weight"],
                 "  CRITICAL" if r["critical"] else ""))
        if r["note"]:
            print("        %s" % r["note"])
    print("")
    if blocking:
        print("GATE: NOT READY. %d critical capability(ies) are below L3, which "
              "means a teammate would not meet them on the normal path:"
              % len(blocking))
        for b in blocking:
            print("  - %s" % b["capability"])
        print("")
        print("Parity is not success. It is the price of admission, and the "
              "ultimate measure stays accepted external verified deliveries per "
              "week.")
        return 1
    print("GATE: every critical capability is on the product path. Whether to "
          "invite anyone is the founder's decision, not this tool's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
