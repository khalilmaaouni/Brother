"""priority: order the board by what a real person actually asked for.

The scheduler in graph_loop.py orders correctly for the dimensions it can see:
ship membership, downstream weight, cost, and write-set conflict. It has no
dimension for whether anyone WANTS the thing. That is not a flaw in it, it is a
question its inputs never carried, and until the team's complaints reached the
board there was no data to answer it with.

Now there is. Thirteen complaints, each verified against current code, each
ADDRESSED, PARTIAL or NOT-ADDRESSED. That turns "is this worth doing next" from
a judgement into an arithmetic question: does this node close something a person
actually complained about, and how badly is that thing still broken.

THE FINDING THAT PROMPTED THIS, and it is the reason the tool exists rather than
a one-off note. On the day it was written, almost none of the open board nodes
closed any open complaint. The board was full of genuinely good machinery and
nearly empty of the things the people who tried the product said were wrong.
That is exactly the failure an outside review had named the same week: growing
stronger internally faster than it grows better for anyone using it.

HOW IT SCORES, and every part is deliberate:

  * A node that closes a NOT-ADDRESSED complaint scores higher than one closing
    a PARTIAL, because untouched is worse than half done.
  * A node closing several complaints outranks one closing a single complaint.
  * Ties break toward the CHEAPER node, so a coverage score cannot be used to
    justify a quarter of work over an afternoon of it.
  * A node closing NOTHING is not forbidden and not hidden. It is ranked last
    and REPORTED as internal, because some internal work is load bearing and
    pretending otherwise would just push the dishonesty somewhere else.

WHAT IT REFUSES TO DO: invent a link. A node covers a complaint only when the
board SAYS SO, in a closes_complaint list on the node itself. An unstated link
is not a link, and a tool that guessed at them by keyword would manufacture
coverage, which is the most expensive possible error here because it would
report the team's problems as handled while nobody was working on them.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")

#: How much worse untouched is than half done. Not a tuned constant: a
#: NOT-ADDRESSED complaint has had NOTHING done about it, which is a different
#: kind of fact from one that is partly answered, and the ordering should say so
#: without letting a single untouched item outrank two partial ones by itself.
WEIGHT = {"NOT-ADDRESSED": 3, "PARTIAL": 2, "NO-DATA": 2, "ADDRESSED": 0}

CLOSED = ("DONE", "SUPERSEDED")


def hours(node):
    return (node.get("effort_hours") or node.get("estimate_hours")
            or node.get("hours") or 0)


def open_nodes(doc):
    return [n for n in doc.get("rows", []) + doc.get("features", [])
            if (n.get("status") or "").upper() not in CLOSED]


def complaints(doc):
    """id -> verdict, for every complaint on the board."""
    tc = doc.get("team_complaints") or {}
    series = tc.get("P_series_verified_2026_08_29") or {}
    return {k: (v.get("verdict") or "NO-DATA") for k, v in series.items()}


def score(node, verdicts):
    """(coverage, detail). Coverage is 0 for a node that closes nothing."""
    named = node.get("closes_complaint") or []
    hit = [(c, verdicts[c]) for c in named if c in verdicts]
    unknown = [c for c in named if c not in verdicts]
    total = sum(WEIGHT.get(v, 0) for _c, v in hit)
    return total, {"claims": named, "matched": hit, "unknown": unknown}


def rank(doc):
    """Every open node, best first. Pure, so the order is reproducible."""
    verdicts = complaints(doc)
    scored = []
    for n in open_nodes(doc):
        total, detail = score(n, verdicts)
        scored.append({"id": n.get("id"), "hours": hours(n), "coverage": total,
                       "status": (n.get("status") or "").upper(),
                       "in_ship": bool(n.get("in_ship_v1") or n.get("in_ship")),
                       "name": n.get("name") or n.get("ships") or "",
                       "detail": detail})
    # coverage first, then the ship commitment, then CHEAPEST, then id so the
    # order never depends on dictionary insertion.
    scored.sort(key=lambda s: (-s["coverage"], not s["in_ship"], s["hours"], s["id"]))
    return scored, verdicts


def uncovered(doc):
    """Complaints no open node claims to close. The gap that matters most."""
    verdicts = complaints(doc)
    claimed = set()
    for n in open_nodes(doc):
        claimed.update(n.get("closes_complaint") or [])
    return sorted((c for c, v in verdicts.items()
                   if v != "ADDRESSED" and c not in claimed),
                  key=lambda x: (len(x), x))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--roadmap", default=ROADMAP)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.roadmap, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: could not read %s: %s" % (args.roadmap, exc),
              file=sys.stderr)
        return 2

    scored, verdicts = rank(doc)
    if not verdicts:
        print("NO-DATA: the board carries no verified complaints, so nothing "
              "can be ordered by what anyone asked for", file=sys.stderr)
        return 2

    gap = uncovered(doc)
    if args.json:
        print(json.dumps({"ranked": scored, "uncovered": gap}, indent=2))
        return 0

    covering = [s for s in scored if s["coverage"] > 0]
    internal = [s for s in scored if s["coverage"] == 0]

    print("ORDERED BY WHAT SOMEBODY ASKED FOR (%d open node(s))" % len(scored))
    for s in covering:
        names = ", ".join("%s:%s" % (c, v) for c, v in s["detail"]["matched"])
        print("  %-8s coverage %-3d %4sh  %s" % (s["id"], s["coverage"], s["hours"],
                                                 s["name"][:44]))
        print("           closes %s" % names)
    if not covering:
        print("  NONE. Not one open node claims to close a complaint anybody made.")

    print()
    print("INTERNAL ONLY (%d), ranked last and named rather than hidden:" % len(internal))
    for s in internal:
        print("  %-8s %4sh  %s" % (s["id"], s["hours"], s["name"][:52]))

    print()
    if gap:
        print("UNCOVERED COMPLAINTS (%d): no open node claims to close these." % len(gap))
        for c in gap:
            print("  %-5s %s" % (c, verdicts[c]))
        print("An uncovered complaint is not a scheduling problem, it is a plan "
              "that does not contain the work.")
    else:
        print("Every unaddressed complaint is claimed by some open node.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
