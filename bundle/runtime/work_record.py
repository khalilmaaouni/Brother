"""work_record: an outcome somebody typed becomes units the scheduler can read.

PARITY BLOCKER, and the one that unlocks the most: five of the nine cells below
the team adoption gate all wait on this single piece. The dispatch machinery is
complete and the product path stops before it, because nothing turns a sentence
a person typed into units the scheduler accepts. The only document in that shape
today is a hand maintained roadmap.

WHAT THIS DOES NOT DO, said first because the opposite claim would be the
easiest lie on this board: IT DOES NOT DECOMPOSE ENGLISH. Turning "add rate
limiting to the invoice poster" into correct dependency-aware units is a
judgement, and a script that pretended to make it would produce confident
nonsense with a done_check attached, which is worse than refusing. The units
arrive from whoever can make that judgement, a model or a person.

WHAT IT DOES is the half that is missing and IS deterministic: the CONTRACT.
Every unit that reaches the scheduler has been checked against what the rest of
the pipeline needs, and a unit that would break something downstream is refused
at creation rather than discovered mid-run.

THE CONTRACT, and each clause exists because something downstream needs it:

  A DONE-CHECK, runnable. The verifier has nothing to verify without one, and a
  unit with no check closes on somebody's opinion.

  A WRITE SCOPE. The scope audit compares the real diff against what was
  declared, and a unit declaring nothing produces NO-DATA, which correctly
  blocks integration. So an undeclared unit should never be dispatched at all,
  and this is where that is cheapest to say.

  DEPENDENCIES THAT EXIST AND DO NOT CYCLE. The ready set is computed from the
  graph; a dangling edge silently drops a unit from consideration forever and a
  cycle means nothing is ever ready. Both are invisible at run time and obvious
  here.

  A UNIQUE ID. Claims are keyed by it. Two units sharing an id means two workers
  fighting over one lease.

REFUSAL IS THE FEATURE. A Work record that accepts anything is a file format,
not a contract, and the estate already has enough of those.

Python 3, standard library only. No network.

origin: two confirmed callers of create(), both ultimately a direct CLI
invocation. (1) A human or session runs this module's own CLI directly:
`python3 scripts/work_record.py "<outcome>" --units units.json`, whose
main() parses args and calls create(). (2) scripts/door.py imports this
module (`import work_record as WR`, door.py line 35) and its own main()
hands a model's decomposition straight to `WR.create(...)` (door.py line
190) after driving a decomposer command until the answer passes this
module's own validation; door.py is itself run as a CLI. Confirmed by grep:
of the other files naming work_record (model_worker.py, brother_run.py,
various tests), only door.py actually imports and calls into it; the rest
only mention it in comments or docstrings.

PRODUCER: this module is the sole producer of its own Work JSON files under
docs/plan/work/. The write happens inside create(), above, at the `with
open(path, "w", encoding="utf-8") as fh: json.dump(record, fh, ...)` call
(lines 153-156 of this file), reached only after check_units() has found no
problems, so a record is written only once the contract is satisfied.
"""
import argparse
import json
import os
import sys

NODATA = "NO-DATA"
STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "docs", "plan", "work")


def check_units(units):
    """Every problem, not the first. A caller fixing one at a time learns the
    contract slowly and by being refused repeatedly."""
    problems = []
    seen = set()
    ids = {str(u.get("id") or "") for u in units}
    if not units:
        problems.append("no units at all, so there is nothing to schedule. An "
                        "outcome with no decomposition is not Work yet")
    for i, u in enumerate(units):
        uid = str(u.get("id") or "").strip()
        where = uid or "unit %d" % (i + 1)
        if not uid:
            problems.append("%s has no id, and claims are keyed by id" % where)
        elif uid in seen:
            problems.append("%s appears twice, which would put two workers on "
                            "one lease" % where)
        seen.add(uid)
        if not str(u.get("done_check") or "").strip():
            problems.append("%s has no done_check, so the verifier would have "
                            "nothing to verify and it would close on an opinion"
                            % where)
        if not (u.get("owns") or []):
            problems.append("%s declares no write scope, so the scope audit "
                            "would return NO-DATA and block its integration. An "
                            "undeclared unit should never be dispatched" % where)
        for path in (u.get("owns") or []):
            p = str(path)
            if os.path.isabs(p) or os.path.normpath(p).split(os.sep)[0] == os.pardir:
                problems.append("%s declares a write scope escaping the "
                                "repository (%r). The scope audit reads git "
                                "status, which cannot see a write outside the "
                                "tree, so an escaping scope would be invisible "
                                "to every control downstream: refused at "
                                "declaration, the only place it is visible"
                                % (where, p))
        for dep in (u.get("depends_on") or []):
            if str(dep) not in ids:
                problems.append("%s depends on %r, which is not a unit of this "
                                "Work. A dangling edge drops a unit from the "
                                "ready set forever, silently" % (where, dep))
    problems.extend(_cycles(units))
    return problems


def _cycles(units):
    """Every cycle, named by the units in it. A cycle means nothing is ever
    ready, and at run time that is indistinguishable from having no work."""
    graph = {str(u.get("id")): [str(d) for d in (u.get("depends_on") or [])]
             for u in units if u.get("id")}
    problems, state = [], {}

    def walk(node, path):
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            cut = path[path.index(node):] if node in path else path
            problems.append("a dependency cycle: %s. Nothing in it can ever "
                            "become ready" % " -> ".join(cut + [node]))
            return
        state[node] = "open"
        for nxt in graph.get(node, []):
            if nxt in graph:
                walk(nxt, path + [node])
        state[node] = "done"

    for node in sorted(graph):
        walk(node, [])
    return sorted(set(problems))


def create(outcome, units, work_id=None, store=None):
    """(record, problems). Refuses rather than storing something unschedulable."""
    problems = check_units(units)
    if not str(outcome or "").strip():
        problems.insert(0, "no outcome was given, so nothing says what this Work "
                           "is for or how anybody would know it succeeded")
    if problems:
        return None, problems
    wid = work_id or ("W-" + "".join(c for c in str(outcome).lower()
                                     if c.isalnum() or c == " ").replace(" ", "-")[:40])
    record = {
        "work_id": wid,
        "outcome": outcome,
        # The scheduler reads `rows` and `features` alike, so units land in
        # `rows`: the shape is the existing contract rather than a new one, and
        # a second shape would need a second scheduler.
        "rows": [{"id": str(u["id"]),
                  "title": u.get("title") or u.get("name") or str(u["id"]),
                  "status": u.get("status") or "SCHEDULED",
                  "depends_on": [str(d) for d in (u.get("depends_on") or [])],
                  "owns": list(u.get("owns") or []),
                  "done_check": u["done_check"],
                  "evidence": u.get("evidence", "")}
                 for u in units],
        "features": [],
    }
    if store:
        os.makedirs(store, exist_ok=True)
        path = os.path.join(store, wid + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
            fh.write("\n")
        record["path"] = path
    return record, []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("outcome", nargs="?", help="what should be true when this is done")
    ap.add_argument("--units", help="a JSON file or literal: the decomposition")
    ap.add_argument("--store", default=STORE)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if not args.outcome:
        print("%s: no outcome was given.\n"
              "  usage: work_record.py \"<what should be true>\" --units units.json\n"
              "  This does NOT decompose the outcome for you: the units come from\n"
              "  whoever can make that judgement. What it enforces is the contract\n"
              "  every unit must meet before the scheduler will accept it."
              % NODATA, file=sys.stderr)
        return 2
    if not args.units:
        print("%s: no units were given, so this outcome is not Work yet. An\n"
              "  outcome with no decomposition cannot be scheduled, and inventing\n"
              "  one here would produce confident nonsense with a done_check\n"
              "  attached." % NODATA, file=sys.stderr)
        return 2

    raw = args.units
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as fh:
            raw = fh.read()
    try:
        units = json.loads(raw)
    except ValueError as exc:
        print("%s: the units could not be read as JSON: %s" % (NODATA, exc),
              file=sys.stderr)
        return 2

    record, problems = create(args.outcome, units, store=args.store)
    if problems:
        print("REFUSED: %d problem(s) that would break something downstream:"
              % len(problems), file=sys.stderr)
        for p in problems:
            print("  * %s" % p, file=sys.stderr)
        return 1
    print("Work %s created with %d unit(s): %s"
          % (record["work_id"], len(record["rows"]),
             ", ".join(r["id"] for r in record["rows"])))
    print("  stored at %s" % record.get("path", "(not stored)"))
    print("  the scheduler reads it with: python3 scripts/loop_bridge.py "
          "--dry-run   (after pointing graph_loop at this file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
