"""bm_queue_numbers: recompute the five numbers that decide whether any of this worked.

HOLE H1, from the 2026-08-15 lifecycle sweep: "no command computes the five
queue numbers that decide whether any of this worked; hand counted once, cannot
be recomputed."

WHY THIS IS THE MOST IMPORTANT SMALL TOOL IN THE ESTATE, and the reason is in the
adopting team's own words rather than ours. Their delivery lead named these five
as the agreed measure of success, and added that no gate verdict replaces them.
Everything else this estate computes is a proxy. These are the thing itself.

They were hand counted once from a 7 August report:

    41  waiting on development
    22  waiting on test resource
    23  waiting on the QC lead
    11  in Testing (the QC lead)
    48  with a TBD end date

A number counted by hand once cannot be compared with anything. A before and
after would be a second hand count, which measures the counter as much as the
queue. So the smallest close, named in the plan in these words, is one command
that reads a tracker export and prints each number it can compute, reporting
NO-DATA by name for any the export cannot supply.

WHAT IT REFUSES TO DO, and each refusal is the point rather than a limitation:

  IT NEVER GUESSES A COLUMN. Tracker exports differ, and a tool that quietly
  picks the closest looking header will one day count the wrong field and report
  a confident number. A column it cannot find is named in the output and the
  numbers depending on it are NO-DATA.

  A MISSING COLUMN IS NEVER ZERO. Zero waiting on development is a triumph;
  "I could not find the status column" is a broken export. They must never print
  the same way, and this estate has been bitten by exactly that confusion.

  IT DOES NOT SHIP THE DATA. No export lives in this repository and none should:
  it is the team's own tracker content. The fixtures here are synthetic, use
  role words rather than names, and carry no client term.

  IT REVEALS, IT DOES NOT DECIDE. The plan says so plainly: the decision is the
  team's. This prints five numbers and no verdict.

Python 3, standard library only. No network.
"""
import argparse
import csv
import json
import os
import re
import sys

NODATA = "NO-DATA"

#: The five, in the order the team stated them. `status` counters match a value
#: in the status column; `blank` counters count rows whose date column is empty
#: or says TBD. Patterns are matched case-insensitively against the whole cell,
#: with surrounding whitespace ignored, and are overridable from a config file
#: because another team's tracker will spell its states differently.
FIVE = (
    {"key": "waiting_on_development", "label": "waiting on development",
     "kind": "status", "pattern": r"waiting.*develop"},
    {"key": "waiting_on_test_resource", "label": "waiting on test resource",
     "kind": "status", "pattern": r"waiting.*test\s*resource"},
    {"key": "waiting_on_qc_lead", "label": "waiting on the QC lead",
     "kind": "status", "pattern": r"waiting.*(qc|quality)"},
    {"key": "in_testing", "label": "in Testing (the QC lead)",
     "kind": "status", "pattern": r"^\s*in\s+testing\s*$|^\s*testing\s*$"},
    {"key": "tbd_end_date", "label": "with a TBD end date",
     "kind": "blank", "pattern": r"^\s*(tbd|t\.b\.d\.?|to be determined|n/?a|-)?\s*$"},
)

#: Header spellings this tool will accept for each role, longest first so a more
#: specific header wins. Anything else is reported unfound rather than guessed.
HEADERS = {
    "status": ("status", "state", "workflow state", "current status", "stage"),
    "end_date": ("end date", "target end date", "due date", "finish date",
                 "planned end", "end"),
}


def find_column(fieldnames, role):
    """The column for a role, or None. Exact-ish match only, never fuzzy.

    Returns the FIRST header whose normalised name equals one of the accepted
    spellings. A near miss is a miss: a tool that settles for the closest
    looking header eventually counts the wrong field and says it confidently."""
    if not fieldnames:
        return None
    # Separators normalise to a space, they do not vanish. Written first as a
    # deletion, which turned "End-Date" into "enddate" and missed a spelling
    # real trackers actually export. This is NOT a step toward fuzzy matching:
    # "Status Category" still normalises to "status category" and is still a
    # miss, because a near miss must stay a miss.
    def _n(f):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (f or "").lower())).strip()
    norm = {_n(f): f for f in fieldnames}
    for want in HEADERS.get(role, ()):
        if want in norm:
            return norm[want]
    return None


def read_rows(path):
    """(rows, fieldnames, problem). CSV or JSON list of objects."""
    if not os.path.isfile(path):
        return None, None, "%s does not exist" % path
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            head = fh.read(2048)
            fh.seek(0)
            if head.lstrip().startswith(("[", "{")):
                data = json.load(fh)
                rows = data if isinstance(data, list) else data.get("rows") or []
                names = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
                return rows, names, ""
            reader = csv.DictReader(fh)
            rows = list(reader)
            return rows, list(reader.fieldnames or []), ""
    except (OSError, ValueError) as exc:
        return None, None, "could not read %s: %s" % (path, exc)


def count(rows, columns, spec):
    """(number, note). A number, or None with the reason it could not be one."""
    role = "status" if spec["kind"] == "status" else "end_date"
    col = columns.get(role)
    if not col:
        return None, ("no %s column was found in this export, so this number is "
                      "%s rather than zero. Accepted spellings: %s"
                      % (role.replace("_", " "), NODATA,
                         ", ".join(HEADERS[role])))
    rx = re.compile(spec["pattern"], re.I)
    n = sum(1 for r in rows if rx.search(str(r.get(col, "") or "").strip()))
    return n, ""


def compute(rows, fieldnames, five=FIVE):
    columns = {role: find_column(fieldnames, role) for role in HEADERS}
    out = []
    for spec in five:
        n, note = count(rows, columns, spec)
        out.append({"key": spec["key"], "label": spec["label"],
                    "value": n, "note": note})
    return out, columns


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("export", nargs="?",
                    help="a tracker export, CSV or JSON list of objects")
    ap.add_argument("--expect", help="five comma separated numbers to compare against, "
                                     "for example 41,22,23,11,48")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if not args.export:
        print("%s: no export was given, so none of the five numbers was computed. "
              "That is not zero and it is not a pass.\n"
              "  usage: bm_queue_numbers.py <export.csv>\n"
              "  The five: %s"
              % (NODATA, "; ".join(s["label"] for s in FIVE)), file=sys.stderr)
        return 2

    rows, fieldnames, problem = read_rows(args.export)
    if rows is None:
        print("%s: %s" % (NODATA, problem), file=sys.stderr)
        return 2

    results, columns = compute(rows, fieldnames)
    if args.json:
        print(json.dumps({"rows": len(rows), "columns": columns,
                          "numbers": results}, indent=2, sort_keys=True))
    else:
        print("%d row(s) read from %s" % (len(rows), args.export))
        for r in results:
            if r["value"] is None:
                print("  %-32s %s" % (r["label"], NODATA))
                print("      %s" % r["note"])
            else:
                print("  %-32s %d" % (r["label"], r["value"]))

    missing = [r["label"] for r in results if r["value"] is None]
    if args.expect:
        want = [w.strip() for w in args.expect.split(",")]
        got = [r["value"] for r in results]
        diffs = [(r["label"], w, g) for r, w, g in zip(results, want, got)
                 if g is None or str(g) != w]
        print("")
        if diffs:
            for label, w, g in diffs:
                print("  DIFFERS  %-30s expected %s, computed %s"
                      % (label, w, NODATA if g is None else g), file=sys.stderr)
            return 1
        print("  all five reproduce the expected numbers")
    if missing:
        print("\n%s: %d of the five could not be computed from this export: %s. "
              "The plan's own check accepts this branch: name which of the five "
              "it cannot supply." % (NODATA, len(missing), "; ".join(missing)),
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
