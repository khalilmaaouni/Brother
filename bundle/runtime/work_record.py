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
docs/plan/work/, and since E61 it is also the sole WRITER of a Work document
anywhere: write_record(path, doc) below is the one helper every writer calls,
brother_run.py's ten in-place stamps included. create() reaches it only after
check_units() has found no problems, so a record is written only once the
contract is satisfied; write_record() then puts it on disk atomically
(tempfile, fsync, os.replace) under the claim store's lock, so a run killed
mid-stamp leaves a whole document rather than a torn one.
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_store  # noqa: E402

NODATA = "NO-DATA"
STORE = os.path.join(os.path.dirname(HERE), "docs", "plan", "work")

#: The persona document's universal evidence library (docs/plan/PERSONA-
#: INTEGRATION-PLAN-2026-09-04.md section 2, citing doc section 6.1): the
#: plan itself never spells out eighteen full names, only ever cites the
#: codes a persona's pack accepts (E1, E3, E7, ...), so the codes are the
#: whole of the vocabulary this module can validate against.
EVIDENCE_FAMILIES = frozenset("E%d" % n for n in range(1, 19))

#: doc 6.2's oracle_source vocabulary, in the document's own words.
ORACLE_SOURCES = frozenset((
    "requirement", "business_rule", "independent_query", "reference_impl",
    "prior_release", "generated_from_impl", "human_observation", "none",
))


def write_record(path, doc):
    """The ONE way a Work document reaches disk: temp file beside the
    target, fsync, os.replace, all under the claim store's own lock.

    E61. Every writer of a Work record used to be a plain
    `open(path, "w")` plus `json.dump(doc, ...)`, eleven of them (ten in
    brother_run.py, one in create() below). A plain rewrite truncates the
    real file first, so a run killed mid-stamp leaves a TORN document:
    valid JSON is not even guaranteed, and the claim store sitting in the
    same run directory already survived exactly that case by writing
    through claim_store._write. The asymmetry was the defect.

    os.replace() is atomic on POSIX, so a reader sees either the whole
    old document or the whole new one, never a truncated one, and the
    fsync before the rename is what makes that true across a power loss
    rather than only across a kill.

    THE LOCK is claim_store's, deliberately, rather than a second lock
    discipline invented here: it is an O_CREAT|O_EXCL file lock that
    excludes across PROCESSES (two sessions, not two threads), and it
    already reclaims a lock whose owning pid is gone. It serialises the
    WRITE. A caller doing read-modify-write across processes that wants
    no lost update must hold claim_store.Lock(path) around its own read
    as well; today's callers are the single run loop that owns its own
    run directory.

    Raises (OSError, TimeoutError) rather than swallowing: a Work
    document that did not reach disk must not be reported as stamped."""
    path = os.path.abspath(path)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with claim_store.Lock(path):
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".work-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def independence_for(oracle_source):
    """A PASS proves nothing about how independent its oracle was unless
    something says so beside the verdict (doc 6.2; 15.4's circularity
    warning). generated_from_impl is the one source where the same work
    that wrote the code also wrote the check's expected value, so it alone
    reads circular_risk; no source at all is unverified, never read as a
    clean independent pass by omission."""
    src = str(oracle_source or "").strip()
    if not src:
        return "unverified"
    if src == "generated_from_impl":
        return "circular_risk"
    return "independent"


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
        fam = u.get("evidence_family")
        if fam not in (None, "") and str(fam) not in EVIDENCE_FAMILIES:
            problems.append("%s declares evidence_family %r, which is not "
                            "one of E1 to E18" % (where, fam))
        src = u.get("oracle_source")
        if src not in (None, "") and str(src) not in ORACLE_SOURCES:
            problems.append("%s declares oracle_source %r, which is not one "
                            "of the recognized oracle sources (%s)"
                            % (where, src, ", ".join(sorted(ORACLE_SOURCES))))
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


def create(outcome, units, work_id=None, store=None, lens_inferred=None,
          challenge_assumption=None, pending_challenge=None):
    """(record, problems). Refuses rather than storing something unschedulable.

    `lens_inferred`, P3 (persona integration, docs/plan/PERSONA-INTEGRATION-
    PLAN-2026-09-04.md gap P3): {"lens": <pack name>, "matched_paths": [...]}
    when door.py's own tree-signal matcher found one, else None. Stored
    verbatim, never computed here: this module validates the unit contract,
    it does not read a tree, and door.py is the sole caller that ever passes
    this argument a non-None value.

    `challenge_assumption` and `pending_challenge`, P5 (persona integration,
    gap P5): door.py's own metric search over the tree, stamped here the
    same way, unread and unvalidated. At most one of the two is ever
    non-None (door.py's compute_challenge() returns the other as None):
    `challenge_assumption` is {"lens", "path"} when the tree already
    answers the pack's challenge_question (stated, never asked);
    `pending_challenge` is {"lens", "question"} when it does not (the
    intent screen may pose it, budgeted to one question per run,
    interactive mode only). `human_decision` starts None on every record;
    brother_run.py's own intent screen is the only writer that ever fills
    it in, once a live person answers."""
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
        "lens_inferred": lens_inferred,
        "challenge_assumption": challenge_assumption,
        "pending_challenge": pending_challenge,
        "human_decision": None,
        # The scheduler reads `rows` and `features` alike, so units land in
        # `rows`: the shape is the existing contract rather than a new one, and
        # a second shape would need a second scheduler.
        "rows": [{"id": str(u["id"]),
                  "title": u.get("title") or u.get("name") or str(u["id"]),
                  "status": u.get("status") or "SCHEDULED",
                  "depends_on": [str(d) for d in (u.get("depends_on") or [])],
                  "owns": list(u.get("owns") or []),
                  "done_check": u["done_check"],
                  "evidence": u.get("evidence", ""),
                  "evidence_family": str(u.get("evidence_family") or ""),
                  "oracle_source": str(u.get("oracle_source") or ""),
                  "independence": independence_for(u.get("oracle_source"))}
                 for u in units],
        "features": [],
    }
    if store:
        os.makedirs(store, exist_ok=True)
        path = os.path.join(store, wid + ".json")
        write_record(path, record)
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
