#!/usr/bin/env python3
"""BrotherSBE decision tables: named criteria in, a recommendation with alternatives out.

The consultation gathers context; this turns it into a reproducible recommendation.
Thresholds are data in tables/, editable in a reviewed pull request, because a
threshold measured on someone else's estate is a default, not a law.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import say


def recommend(table, context):
    """Score every criterion supplied in context against the table and rank options.

    Returns a dict with:
      recommendation: the top-ranked option, or None when verdict is NO-DATA.
      alternatives: up to two next-ranked options (fewer if the table has fewer
          options than that), or an empty list when verdict is NO-DATA. Callers
          must not assume the list has exactly two entries.
      deciding_criteria: one human-readable line per criterion that contributed.
      evidence: the number of criteria that actually contributed, i.e.
          len(deciding_criteria).
      verdict: "NO-DATA" when no criterion contributed anything (context was
          empty or every supplied value matched no criterion), "OK" otherwise.
          A recommendation backed by zero evidence is indistinguishable from a
          guess, so when verdict is NO-DATA, recommendation and alternatives
          are suppressed rather than presented as if they were well-evidenced.
      unrecognized: one line per supplied choice criterion whose value matched
          none of that criterion's known keys, naming the criterion and the
          value received, so a typo is distinguishable from an omission.
      flip_condition: the flip condition FOR THE RECOMMENDATION, from
          table["flips"], falling back to table["flip"] where a recommendation
          has no entry. One string served the whole table, so the run that
          recommended `services` already had nine deploying teams and high
          failure isolation and was handed a flip condition naming two
          conditions that were already true. A flip condition that is already
          satisfied can never fire, and L12 promises every non-NO-DATA output
          carries one.
      scores: the raw tally per option.
    """
    tally = {opt: 0 for opt in table["options"]}
    deciding = []
    unrecognized = []
    for crit in table["criteria"]:
        val = context.get(crit["name"])
        if val is None:
            continue
        winners = []
        if crit["kind"] == "number":
            if not isinstance(val, (int, float)):
                unrecognized.append("%s=%s is not a recognized value" % (crit["name"], val))
                continue
            for opt, (lo, hi) in crit["scores"].items():
                if lo <= val <= hi:
                    winners.append(opt)
            if not winners:
                # A number outside every range this criterion scores was DROPPED,
                # silently: `deploying_teams=0` produced "no criterion was
                # answered" over a run that answered one, and beside a second
                # criterion it produced a confident recommendation with nothing
                # anywhere saying half the input had been discarded. The type
                # error two lines up was reported and this was not, which is the
                # same value in the same field getting two different honesties.
                unrecognized.append("%s=%s is outside every range this criterion scores (%s)"
                                    % (crit["name"], val,
                                       ", ".join("%s: %s to %s" % (opt, lo, hi)
                                                 for opt, (lo, hi) in crit["scores"].items())))
        else:
            key = str(val)
            if key not in crit["scores"]:
                unrecognized.append("%s=%s is not a recognized value" % (crit["name"], val))
                continue
            winners = crit["scores"][key]
        # A vote for an option the table does not declare is a TABLE defect,
        # and it was discarded in silence: `c1=yes favours gamma` counted as a
        # deciding criterion while contributing nothing to any score, so
        # `evidence: 2` was printed over one contributing criterion and
        # `unrecognized`, whose documented job is making a typo distinguishable
        # from an omission, stayed empty.
        real = [opt for opt in winners if opt in tally]
        ghosts = [opt for opt in winners if opt not in tally]
        if ghosts:
            unrecognized.append("%s=%s favours %s, which %s not among this table's options (%s); "
                                "a vote for an option the table does not declare decides nothing"
                                % (crit["name"], val, ", ".join(ghosts),
                                   "are" if len(ghosts) > 1 else "is",
                                   ", ".join(table["options"])))
        for opt in real:
            tally[opt] += 1
        if real:
            deciding.append("%s=%s favours %s" % (crit["name"], val, ", ".join(real)))
    ranked = sorted(tally, key=lambda o: (-tally[o], table["options"].index(o)))
    verdict = "OK" if deciding else "NO-DATA"
    # An exact tie used to be broken by declaration order in silence, so the
    # operator was handed a confident, specific answer the table did not
    # produce, with the loser listed beside options that scored zero. The tie
    # is its own field and main() prints it with the raw scores.
    tie = ([o for o in ranked if tally[o] == tally[ranked[0]]]
           if verdict == "OK" and len(ranked) > 1 and tally[ranked[0]] == tally[ranked[1]]
           else [])
    return {"recommendation": ranked[0] if verdict == "OK" else None,
            "alternatives": ranked[1:3] if verdict == "OK" else [],
            "deciding_criteria": deciding, "evidence": len(deciding),
            "verdict": verdict, "unrecognized": unrecognized, "tie": tie,
            "flip_condition": (table.get("flips", {}).get(ranked[0], table["flip"])
                               if verdict == "OK" else table["flip"]),
            "scores": tally}


def load_table(path, key):
    """Return (table, error). Every boundary here has a named failure path: a
    missing file, a malformed file, and a table key that does not exist each
    produce a sentence naming what is available, not a raw traceback. One table
    ships (the architecture shape table); asking for a family that has no table
    yet must say so, because a traceback reads as a broken tool rather than as
    "this decision is human review until its table ships"."""
    try:
        with open(path) as f:
            tables = json.load(f)
    except OSError as e:
        return None, "cannot read table file %s: %s" % (path, e)
    except ValueError as e:
        return None, "table file %s is not valid JSON: %s" % (path, e)
    if not isinstance(tables, dict):
        return None, "table file %s does not hold a mapping of table name to table" % path
    if key not in tables:
        return None, ("no table named %r in %s. Tables that ship: %s. Decision families with no table yet "
                      "are human review, not a tool failure." % (key, path, ", ".join(sorted(tables)) or "none"))
    # The docstring above promises a named failure path for "a malformed file",
    # and it used to mean only "not valid JSON": a file that is valid JSON and
    # not a valid TABLE (the malformed case an author actually produces) fell
    # through to the generic handler as `error KeyError('flip')`, exactly the
    # Python-repr failure mode the EOFError comment below names as the one to
    # avoid. The shape the rest of this tool requires is checked here, by name.
    t = tables[key]
    problem = ""
    if not isinstance(t, dict):
        problem = "is not a mapping"
    elif not isinstance(t.get("options"), list) or not t["options"] or \
            not all(isinstance(o, str) for o in t["options"]):
        problem = "lacks an 'options' list of option names"
    elif not isinstance(t.get("criteria"), list) or not all(
            isinstance(c, dict) and isinstance(c.get("name"), str)
            and c.get("kind") in ("number", "choice")
            and isinstance(c.get("scores"), dict) and isinstance(c.get("note"), str)
            for c in t["criteria"]):
        problem = ("lacks a 'criteria' list in which every entry carries a 'name', a 'kind' of "
                   "number or choice, a 'scores' mapping and a 'note'")
    elif not isinstance(t.get("flip"), str) or not t["flip"].strip():
        problem = "lacks a 'flip' condition (the sentence naming what would reverse the recommendation)"
    if problem:
        return None, ("table %r in %s is valid JSON and still not a decision table this tool "
                      "can read: it %s. Fix the table; a malformed table is a table defect, "
                      "not an unanswered decision" % (key, path, problem))
    return t, ""


DEFAULT_TABLE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tables", "architecture.json")


DECIDE_USAGE = (
    "usage: sbe_decide.py [table-name | table-file.json [table-name]] [key=value ...]\n"
    "  reads: the named decision table (default: tables/architecture.json,\n"
    "    table 'shape'); key=value arguments answer criteria without the\n"
    "    interview, and unanswered criteria are asked interactively.\n"
    "  writes: nothing.\n"
    "  flags:\n"
    "    -h, --help        print this and exit 0 without reading a table"
)


def main():
    # Help is not an error, and a flag is not a table name: `-h` used to be
    # read as a table called '-h' and refused with exit 1, in a tool whose law
    # is about not silently misreading an input.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        for usage_line in DECIDE_USAGE.splitlines():
            say(usage_line)
        sys.exit(0)
    for a in sys.argv[1:]:
        if a.startswith("-"):
            for usage_line in DECIDE_USAGE.splitlines():
                say(usage_line)
            say("sbe_decide: unrecognized flag %r; refusing rather than running past it" % a)
            sys.exit(2)
    # `sbe_decide.py architecture` is how SKILL.md L12's sentence reads, and the
    # first argument was treated as a file path unconditionally, so that invocation
    # printed "cannot read table file architecture: No such file or directory" and
    # named no tables at all, under a law promising that asking for a table that
    # does not exist names the tables that do. A first argument that is not a
    # readable file is read as a table NAME against the shipped file, which is
    # either the answer or a sentence listing what ships.
    path, key = DEFAULT_TABLE_FILE, "shape"
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        looks_like_a_path = os.path.isfile(arg) or os.sep in arg or arg.endswith(".json")
        if looks_like_a_path:
            # Still a path when it is meant to be one, so a mistyped file name
            # reports the file it could not read rather than being reported as an
            # unknown table.
            path = arg
            key = sys.argv[2] if len(sys.argv) > 2 else "shape"
        else:
            key = arg
    table, err = load_table(path, key)
    if err:
        say("sbe_decide: %s" % err)
        sys.exit(1)
    # key=value arguments answer criteria without the interview. They used to be
    # swallowed: every argument after the table name was discarded without a
    # word and the tool then asked the questions interactively, in a tool whose
    # law is about not silently ignoring an input it cannot read. An argument
    # that is neither a criterion nor key=value is refused by name.
    known = {crit["name"]: crit for crit in table["criteria"]}
    context = {}
    extra = sys.argv[2:] if path == DEFAULT_TABLE_FILE else sys.argv[3:]
    for a in extra:
        name, eq, raw = a.partition("=")
        if not eq or name not in known:
            say("sbe_decide: %r is not an answer this table can read; answers are given as "
                "key=value for the criteria: %s" % (a, ", ".join(known)))
            sys.exit(1)
        crit = known[name]
        context[name] = int(raw) if crit["kind"] == "number" and raw.isdigit() else raw
    for crit in table["criteria"]:
        if crit["name"] in context:
            continue
        try:
            raw = input("%s (%s): " % (crit["name"], crit["note"])).strip()
        except EOFError:
            # The named failure path for a closed stdin, exactly as sbe_intake
            # has: a Python repr here read as a broken tool rather than as
            # "nobody answered".
            print("")
            say("sbe_decide: stdin closed before %s was answered; answer the remaining "
                "criteria as key=value arguments, or run interactively." % crit["name"])
            sys.exit(1)
        if not raw:
            continue
        context[crit["name"]] = int(raw) if crit["kind"] == "number" and raw.isdigit() else raw
    r = recommend(table, context)
    # Every line below is a verdict-shaped report line, so each goes through
    # say() (the one_line choke point): a decision-table OPTION NAME carrying
    # newlines used to write byte-perfect extra Recommendation and
    # Alternatives lines into this report, indistinguishable from the real
    # ones. Flattened, the forgery arrives as visible \n escapes inside ONE
    # line a reader can see and question.
    if r["verdict"] == "NO-DATA":
        print("")
        say("NO-DATA: no criterion was answered, so no recommendation can be made.")
    else:
        print("")
        say("Recommendation: %s" % r["recommendation"])
        say("Alternatives: %s" % ", ".join(r["alternatives"]))
        if r["tie"]:
            say("Tie: %s scored equal top marks; the recommendation is the table's declared "
                "order, not a measured difference (scores: %s)"
                % (", ".join(r["tie"]),
                   ", ".join("%s=%d" % (o, r["scores"][o]) for o in r["scores"])))
        print("Decided by:")
        for d in r["deciding_criteria"]:
            say("  - %s" % d)
        say("What would flip this: %s" % r["flip_condition"])
    if r["unrecognized"]:
        print("Unrecognized values (check for typos):")
        for u in r["unrecognized"]:
            say("  - %s" % u)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Same wrapper as the other tools: a crash names itself and exits nonzero
        # rather than printing a traceback that reads as an unusable tool.
        say("sbe_decide: error %r" % (e,))
        sys.exit(1)
