"""floor_score: is Brother behind on the ordinary mechanics, measured where anyone measured.

THE CONSTRAINT THIS SERVES, from section 6 of the switching strategy: a moat is
worthless if developers experience Brother as worse on ordinary engineering
mechanics. So the strategy asks for a Competitive Floor Score kept SEPARATE
from the differentiation score, against one rule:

  no material category more than 0.15 behind the best current competitor

and it makes parity MANDATORY, not aspirational, for release, recovery, Git
safety, runtime safety, install and CI.

WHY THIS IS A TOOL AND NOT A TABLE, which is the same argument scripts/
parity_gate.py already won and this file only extends to a wider board. The
feature-war board of section 16 lists 26 capabilities with a competitive target
each. It would be trivial to fill that board with percentages, and a percentage
nobody can trace is the most flatterable object on a status page. So here:

  * a BROTHER score is granted only by named evidence, a roadmap row whose
    done-check output its own evidence quotes, a level in
    PARITY-2026-08-29.json, or a round of a head-to-head this estate ran;
  * a COMPETITOR score exists only where a head-to-head record this estate
    already holds carries a column that speaks to the capability, and the
    derivation from that column is written into the file beside the number.
    Everything else is NO-DATA, in the file's own words, "not measured on this
    estate". A number read off a vendor page never becomes a cell here.

NO-DATA IS NOT A PASS, and that is the whole difference between this and a
board somebody fills in. An unmeasured capability lowers both scores instead of
quietly inheriting an optimistic one, and it is named again in its own list so
nobody mistakes a small denominator for a good result.

EVIDENCE MUST RESOLVE, or the cell is refused rather than believed. A Brother
cite shaped like a roadmap row id is evidence the board carries, and
test_floor_score checks that every cited id is a row that exists. Any other
cite is a path and has to be in the tree. A cell that cites no row at all has
to name a file in its basis, because a basis naming neither a row nor a file is
an assertion wearing the clothes of evidence. A refused cell scores 0.0 and is
named on its own NO-DATA line, never dropped from the denominator.

EXIT CODES, and the seam between them is deliberate:
  0  no MUST MATCH capability reads BEHIND. The printed verdict is still
     NO-DATA, never PASS, while any mandatory capability has no measured
     competitor to be compared against.
  1  a MUST MATCH capability reads BEHIND the floor, or a capability on the
     mandatory_parity list does. Parity is a floor for every MUST MATCH row;
     the mandatory list names the six section 6 calls mandatory, two of which
     are DOMINATE rows, and it keeps its own meaning on top of the role. An
     earlier version of this file decided the exit code off that list alone,
     so a MUST MATCH capability three quarters of the way behind the best
     measured competitor printed BEHIND and exited 0.
  2  the evidence file is absent or malformed.

IT REPORTS, IT DOES NOT DECIDE. What to build next off the back of it is the
founder's call.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "docs", "plan", "FLOOR-2026-09-05.json")
NODATA = "NO-DATA"

#: Section 6's own number. A capability further than this below the best
#: measured competitor is BEHIND, whatever else is true of it.
FLOOR_GAP = 0.15

ROLES = ("MUST MATCH", "DOMINATE")

#: A roadmap row id as the board writes them: E7, X0, S32, R11.
ROW_ID = re.compile(r"^[A-Z]{1,3}[0-9]+$")

#: A file this estate could hold, as a basis names one in prose.
PATH_TOKEN = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:json|py|sh|md|html|txt|yml)")

#: Where a bare filename is looked for, so a basis may name
#: PARITY-2026-08-29.json without spelling out its directory.
LOOKUP_DIRS = ("", os.path.join("docs", "plan"), "scripts")


class Malformed(Exception):
    """The evidence file cannot be read as a scoring board."""


def _number(value, where):
    """A score is a number in 0..1, or the exception says which cell was not."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None if value is None else _bad(where, value)
    if not 0.0 <= float(value) <= 1.0:
        _bad(where, value)
    return float(value)


def _bad(where, value):
    raise Malformed("%s is %r, which is not a score between 0.0 and 1.0"
                    % (where, value))


def load(path):
    """(doc, competitor_keys). Raises Malformed; the caller turns that into 2."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise Malformed("could not read %s: %s" % (path, exc))
    except ValueError as exc:
        raise Malformed("%s is not JSON: %s" % (path, exc))
    if not isinstance(doc, dict):
        raise Malformed("%s holds %s, not an object" % (path, type(doc).__name__))
    caps = doc.get("capabilities")
    if not isinstance(caps, list) or not caps:
        raise Malformed("%s names no capabilities, so no floor can be computed"
                        % path)
    competitors = doc.get("competitors")
    if not isinstance(competitors, dict) or not competitors:
        raise Malformed("%s names no competitors, so nothing can be compared"
                        % path)
    for cap in caps:
        if not isinstance(cap, dict):
            raise Malformed("a capability entry is %s, not an object"
                            % type(cap).__name__)
        name = cap.get("capability")
        if not str(name or "").strip():
            raise Malformed("a capability entry carries no name")
        if cap.get("role") not in ROLES:
            raise Malformed("%s has role %r, which is neither of %s"
                            % (name, cap.get("role"), " nor ".join(ROLES)))
        brother = cap.get("brother")
        if not isinstance(brother, dict):
            raise Malformed("%s carries no brother block" % name)
        got = _number(brother.get("score"), "%s: the Brother score" % name)
        if got is None:
            raise Malformed("%s: the Brother score is null. A capability with "
                            "no evidence scores 0.0 and says so, so that an "
                            "unmeasured cell lowers the score rather than "
                            "disappearing from it" % name)
        if not str(brother.get("basis") or "").strip():
            raise Malformed("%s: the Brother score names no evidence. A score "
                            "is granted by evidence, never by assertion" % name)
        cells = cap.get("competitors")
        if not isinstance(cells, dict):
            raise Malformed("%s carries no competitors block" % name)
        missing = sorted(set(competitors) - set(cells))
        if missing:
            raise Malformed("%s says nothing about %s. Every named competitor "
                            "gets a cell, even if the cell is NO-DATA"
                            % (name, ", ".join(missing)))
        for key, cell in cells.items():
            if not isinstance(cell, dict):
                raise Malformed("%s: the %s cell is %s, not an object"
                                % (name, key, type(cell).__name__))
            _number(cell.get("score"), "%s: the %s score" % (name, key))
            if cell.get("score") is None and NODATA not in _nodata_words(cell):
                raise Malformed("%s: the %s cell has no score and does not say "
                                "'not measured on this estate'" % (name, key))
    return doc, sorted(competitors)


def _nodata_words(cell):
    """NO-DATA when the cell says, in the file's own required sentence, why."""
    return (NODATA if "not measured on this estate"
            in str(cell.get("basis") or "").lower() else "")


def _resolves(ref, root):
    """A named path resolves when the tree actually holds it."""
    if "/" in ref:
        return os.path.exists(os.path.join(root, ref))
    return any(os.path.exists(os.path.join(root, d, ref)) for d in LOOKUP_DIRS)


def resolve_evidence(doc, root=None):
    """Score 0.0 every Brother cell whose evidence does not resolve on disk.

    Returns the refusals, each naming the capability and why, so the caller
    prints a NO-DATA line for it. Mutates the doc on purpose: a refused cell
    has to reach the table as 0.0, not as the number it claimed.
    """
    root = ROOT if root is None else root
    refused = []
    for cap in doc["capabilities"]:
        brother = cap["brother"]
        cites = [str(c) for c in (brother.get("cites") or [])]
        paths = [c for c in cites if not ROW_ID.match(c)]
        missing = [p for p in paths if not _resolves(p, root)]
        reason = None
        if missing:
            reason = ("cites %s, which the tree does not hold"
                      % ", ".join(missing))
        elif not cites:
            named = sorted(set(PATH_TOKEN.findall(brother.get("basis") or "")))
            if not named:
                reason = ("cites no roadmap row and its basis names no file, "
                          "so the score rests on assertion")
            elif not any(_resolves(t, root) for t in named):
                reason = ("names %s, which the tree does not hold"
                          % ", ".join(named))
        if reason:
            refused.append({"capability": cap["capability"], "reason": reason})
            brother["score"] = 0.0
            brother["refused"] = reason
    return refused


def best_measured(cap):
    """(key, score, tied) of the strongest competitor anyone actually measured.

    tied counts every measured competitor sharing that score, because a table
    that names one winner out of three at the same number invites the reader to
    think the other two were behind.
    """
    best_key, best = None, None
    for key, cell in sorted(cap["competitors"].items()):
        score = cell.get("score")
        if score is None:
            continue
        if best is None or float(score) > best:
            best_key, best = key, float(score)
    if best is None:
        return None, None, 0
    tied = sum(1 for cell in cap["competitors"].values()
               if cell.get("score") is not None
               and abs(float(cell["score"]) - best) < 1e-9)
    return best_key, best, tied


def verdict(cap):
    """(status, best_key, best, gap, tied). NO-DATA where nobody measured."""
    brother = float(cap["brother"]["score"])
    best_key, best, tied = best_measured(cap)
    if best is None:
        return NODATA, None, None, None, 0
    gap = best - brother
    if gap > FLOOR_GAP + 1e-9:
        return "BEHIND", best_key, best, gap, tied
    return "MATCH", best_key, best, gap, tied


def rows(doc):
    out = []
    for cap in doc["capabilities"]:
        status, best_key, best, gap, tied = verdict(cap)
        out.append({
            "capability": cap["capability"],
            "role": cap["role"],
            "brother": float(cap["brother"]["score"]),
            "best_competitor": best_key,
            "best_score": best,
            "best_tied_with": tied,
            "gap": gap,
            "status": status,
            "leads": (status != NODATA
                      and float(cap["brother"]["score"]) >= best - 1e-9),
        })
    return out


def scores(table):
    """Two fractions, each counting NO-DATA as not a pass, plus the name lists."""
    floor_all = [r for r in table if r["role"] == "MUST MATCH"]
    floor_pass = [r for r in floor_all if r["status"] == "MATCH"]
    floor_nodata = [r for r in floor_all if r["status"] == NODATA]
    floor_behind = [r for r in floor_all if r["status"] == "BEHIND"]

    diff_all = [r for r in table if r["role"] == "DOMINATE"]
    diff_pass = [r for r in diff_all if r["status"] != NODATA and r["leads"]]
    diff_nodata = [r for r in diff_all if r["status"] == NODATA]
    diff_behind = [r for r in diff_all
                   if r["status"] != NODATA and not r["leads"]]
    return {
        "floor": {"all": floor_all, "pass": floor_pass,
                  "nodata": floor_nodata, "behind": floor_behind,
                  "pct": 100.0 * len(floor_pass) / len(floor_all)
                  if floor_all else None},
        "differentiation": {"all": diff_all, "pass": diff_pass,
                            "nodata": diff_nodata, "behind": diff_behind,
                            "pct": 100.0 * len(diff_pass) / len(diff_all)
                            if diff_all else None},
    }


def _pct(value):
    return NODATA if value is None else "%.1f%%" % value


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit 0: nothing where parity is a floor reads BEHIND. The "
               "verdict may still be NO-DATA, which is never a pass.\n"
               "exit 1: a MUST MATCH capability reads BEHIND, or a capability "
               "on the mandatory-parity list does. They are named on one line "
               "above the score.\n"
               "exit 2: the evidence file is absent, malformed, or names a "
               "mandatory capability that is on no row.")
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        doc, competitor_keys = load(args.source)
    except Malformed as exc:
        print("%s: %s" % (NODATA, exc), file=sys.stderr)
        return 2

    refused = resolve_evidence(doc)
    table = rows(doc)
    sums = scores(table)
    mandatory = [str(x) for x
                 in (doc.get("mandatory_parity") or {}).get("capabilities") or []]
    by_name = {r["capability"]: r for r in table}
    unknown = [m for m in mandatory if m not in by_name]
    if unknown:
        print("%s: the mandatory list names %s, which is on no capability row"
              % (NODATA, ", ".join(unknown)), file=sys.stderr)
        return 2
    mand_behind = [m for m in mandatory if by_name[m]["status"] == "BEHIND"]
    mand_nodata = [m for m in mandatory if by_name[m]["status"] == NODATA]
    # Parity is a floor for every MUST MATCH row, and the mandatory list keeps
    # its own meaning on top of that: two of the six names it carries are
    # DOMINATE rows, and dropping them would trade one hole for another.
    offenders = [r for r in table if r["status"] == "BEHIND"
                 and (r["role"] == "MUST MATCH"
                      or r["capability"] in mandatory)]

    if args.json:
        print(json.dumps({
            "competitive_floor_score": sums["floor"]["pct"],
            "differentiation_score": sums["differentiation"]["pct"],
            "rows": table,
            "mandatory_behind": mand_behind,
            "mandatory_no_data": mand_nodata,
            "competitors": competitor_keys,
            "floor_behind": [r["capability"] for r in offenders],
            "refused_evidence": refused,
        }, indent=2, sort_keys=True))
        return 1 if offenders else 0

    print("COMPETITIVE FLOOR, %d capabilities from the feature-war board"
          % len(table))
    print("Rule: %s" % doc.get("the_floor_rule", "(the file names no rule)"))
    for r in refused:
        print("%s: %s %s, so the cell scores 0.0 rather than what it claimed"
              % (NODATA, r["capability"], r["reason"]))
    print("")
    print("  %-30s %-10s %8s  %-14s %6s %7s  %s"
          % ("capability", "role", "brother", "best measured", "score", "gap",
             "verdict"))
    for r in table:
        best = "" if r["best_competitor"] is None else r["best_competitor"]
        if best and r["best_tied_with"] > 1:
            best = "%s +%d tied" % (best, r["best_tied_with"] - 1)
        print("  %-30s %-10s %8.2f  %-14s %6s %7s  %s%s"
              % (r["capability"][:30], r["role"], r["brother"], best or "-",
                 "-" if r["best_score"] is None else "%.2f" % r["best_score"],
                 "-" if r["gap"] is None else "%+.2f" % r["gap"],
                 r["status"],
                 "  (Brother leads)" if r["leads"] and r["status"] != NODATA
                 else ""))
    print("")
    if offenders:
        print("BEHIND the floor of %.2f, and the exit code is 1 for it: %s"
              % (FLOOR_GAP, ", ".join(r["capability"] for r in offenders)))
    print("Competitive Floor Score: %s (%d of %d MUST MATCH capabilities "
          "within %.2f of the best measured competitor)"
          % (_pct(sums["floor"]["pct"]), len(sums["floor"]["pass"]),
             len(sums["floor"]["all"]), FLOOR_GAP))
    print("Differentiation Score:   %s (%d of %d DOMINATE capabilities where "
          "Brother is the best measured)"
          % (_pct(sums["differentiation"]["pct"]),
             len(sums["differentiation"]["pass"]),
             len(sums["differentiation"]["all"])))
    print("")
    print("Neither score counts a %s as a pass. The unmeasured capabilities, "
          "named rather than averaged away:" % NODATA)
    for label, key in (("MUST MATCH", "floor"), ("DOMINATE", "differentiation")):
        names = [r["capability"] for r in sums[key]["nodata"]]
        print("  %s %s: %d of %d%s"
              % (label, NODATA, len(names), len(sums[key]["all"]),
                 (" - " + ", ".join(names)) if names else ""))
    behind = [r for r in table if r["status"] == "BEHIND"]
    if behind:
        print("")
        print("BEHIND, measured, not asserted:")
        for r in behind:
            print("  %s: Brother %.2f against %s at %.2f, %.2f behind a floor "
                  "of %.2f" % (r["capability"], r["brother"],
                               r["best_competitor"], r["best_score"], r["gap"],
                               FLOOR_GAP))
    zeros = [c for c in doc["capabilities"]
             if float(c["brother"]["score"]) == 0.0]
    if zeros:
        print("")
        print("Brother scores 0.0 here, and says why rather than guessing:")
        for c in zeros:
            print("  %s" % c["capability"])
    print("")
    if offenders:
        print("FLOOR: FAIL. BEHIND: %d of the %d capabilities where parity is "
              "a floor, which is every MUST MATCH row plus every name on the "
              "mandatory list:"
              % (len(offenders),
                 len({r["capability"] for r in sums["floor"]["all"]}
                     | set(mandatory))))
        for r in offenders:
            print("  - %s%s"
                  % (r["capability"],
                     " (mandatory parity)" if r["capability"] in mandatory
                     else ""))
        print("Parity is mandatory, not aspirational, for %s, and %d of those "
              "read BEHIND." % (", ".join(mandatory), len(mand_behind)))
        return 1
    if mand_nodata:
        print("FLOOR: %s. No mandatory-parity capability reads BEHIND, but %d "
              "of %d have no measured competitor to be compared against, so "
              "the floor is unproven rather than met: %s"
              % (NODATA, len(mand_nodata), len(mandatory),
                 ", ".join(mand_nodata)))
        print("A %s is never a pass. The exit code stays 0 because 1 is "
              "reserved for a measured BEHIND, and an unrun race is not the "
              "same finding as a lost one." % NODATA)
        return 0
    print("FLOOR: PASS. Every mandatory-parity capability has a measured "
          "competitor and none reads BEHIND.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
