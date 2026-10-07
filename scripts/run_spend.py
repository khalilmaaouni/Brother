#!/usr/bin/env python3
"""run_spend: what a run actually spent, and what cannot be known about it.

"Never walk away and come back to a surprise bill." Two halves answer that,
and this file is the SECOND one. The first is the ceiling itself, which lives
in scripts/model_worker.py (budget_ceiling / BUDGET_ENV) because that is the
one place that builds the vendor argv; this file only reports it.

WHAT IT READS, and nothing else. loop_bridge.py writes each round's real
per-unit usage to a sidecar beside the claim store, named by its own
usage_sidecar_path(): a store at `<dir>/claims.json` gets
`<dir>/claims_usage.json`. That file is `{unit_id: {tokens_in: N, ...}}`,
carrying whichever of model_worker.USAGE_FIELD_MAP's four names the worker
actually reported. This walks for those sidecars and sums them per run.

THE FOUR NAMES ARE ASSERTED AGAINST model_worker.USAGE_FIELD_MAP by the test
beside this file rather than merely retyped here, so a rename in the one
module that produces them cannot leave this reader summing a field nobody
writes any more.

THE TRAP THIS FILE DELIBERATELY DOES NOT FALL INTO. loop_bridge.py has
several places that return a hardcoded cost dict of {"tokens": 0,
"minutes": 0} for a worker that never ran. Those keys are inert only because
they are NOT the usage field names: an aggregator that read a key called
"tokens" would report a confident, tidy ZERO for a run that really did spend
money, which is the worst answer a spend report can give. So this file sums
the four named usage fields and NOTHING else, and a field no run reported
comes back NO-DATA rather than 0. Absent is not zero.

WHY THERE IS NO DOLLAR TOTAL, ever, and why that is the honest answer rather
than a missing feature. No adapter in this product returns a dollar figure.
model_worker.py forwards the claude CLI's own --output-format json usage
object, which carries input_tokens, output_tokens, cache_read_input_tokens
and cache_creation_input_tokens: counts, no price. The Codex adapter carries
fewer still. To print dollars this file would have to multiply those counts
by a price table checked into this repository, and a price table in a
repository goes stale silently: the number would keep printing, keep looking
measured, and quietly stop being true the day a price changed. A fabricated
number presented as a measurement is worse than NO-DATA, so dollars are
NO-DATA with the reason attached, every run, until an adapter really returns
one.

Exit 0  at least one run's usage was read, and the report printed.
Exit 2  NO-DATA: no usage sidecar was found under the root, so nothing is
        known about this estate's spend. Never reported as zero spend.

Python 3.9 floor, standard library only. No network.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model_worker  # noqa: E402

REPO_ROOT = os.path.dirname(HERE)
#: Where loop_bridge's default claim store, and every run directory beside
#: it, actually live (scripts/loop_bridge.py's own default store is
#: <repo>/docs/plan/claims.json). --root points this somewhere else, which is
#: what an installed plugin writing under ~/.claude/brother-run needs.
DEFAULT_ROOT = os.path.join(REPO_ROOT, "docs", "plan")
#: loop_bridge.usage_sidecar_path()'s naming rule, as a suffix to look for.
SIDECAR_SUFFIX = "_usage.json"

NODATA = "NO-DATA"
EXIT_OK = 0
EXIT_NODATA = 2

#: Display order. The MEMBERSHIP is asserted against
#: model_worker.USAGE_FIELD_MAP by the test beside this file, so this tuple
#: cannot silently drift out of step with the module that writes the counts;
#: only the order is this file's own opinion.
FIELDS = ("tokens_in", "tokens_out", "tokens_cached", "tokens_cache_write")

DOLLARS_REASON = (
    "%s: no adapter in this product returns a dollar figure, so no dollar "
    "amount can be reported. model_worker.py forwards the model CLI's own "
    "usage object, which carries token counts and no price, and multiplying "
    "those counts by a price table checked into this repository would print "
    "a number that goes stale silently, which is a fabrication wearing the "
    "clothes of a measurement." % NODATA)


def find_sidecars(root):
    """Every usage sidecar under `root`, sorted. Dot directories are skipped
    so an object store or a nested checkout is never walked into."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(SIDECAR_SUFFIX):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def read_sidecar(path):
    """({unit_id: usage}, problem_or_None). Never raises: an unreadable
    sidecar is reported by name as NO-DATA, and is never folded in as a run
    that spent nothing."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        return {}, "could not be read: %s" % exc
    except ValueError as exc:
        return {}, "is not valid JSON: %s" % exc
    if not isinstance(data, dict):
        return {}, "is a %s, not an object of units" % type(data).__name__
    return data, None


def sum_usage(units):
    """{field: total} over the four named fields, carrying ONLY the fields at
    least one unit really reported. A field absent from the returned dict was
    never reported by anybody, and the caller prints NO-DATA for it rather
    than a zero (see the module docstring's trap paragraph)."""
    totals = {}
    for usage in (units or {}).values():
        if not isinstance(usage, dict):
            continue
        for field in FIELDS:
            val = usage.get(field)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                totals[field] = totals.get(field, 0) + val
    return totals


def totals_under(root):
    """({field: total} across every readable sidecar under root, runs_read).
    One walk, one summing rule, so the text report and the --json report can
    never disagree about a number."""
    grand = {}
    runs_read = 0
    for path in find_sidecars(root):
        units, problem = read_sidecar(path)
        if problem:
            continue
        totals = sum_usage(units)
        if not totals:
            continue
        runs_read += 1
        for field, val in totals.items():
            grand[field] = grand.get(field, 0) + val
    return grand, runs_read


def _cell(totals, field):
    return str(totals[field]) if field in totals else NODATA


def _ceiling_line(env=None):
    usd, enforced, why = model_worker.budget_ceiling(env)
    if enforced:
        return "ceiling: $%g per worker, ENFORCED: %s" % (usd, why)
    if usd is not None:
        return ("ceiling: $%g was asked for but is NOT ENFORCED: %s"
                % (usd, why))
    return ("ceiling: %s: none in force, %s. Set %s to a positive amount and "
            "every claude worker this machine starts carries that ceiling."
            % (NODATA, why, model_worker.BUDGET_ENV))


def build_report(root, env=None):
    """(lines, runs_read). The walk is the only I/O, so a test can drive this
    against a tree it built itself."""
    lines = []
    sidecars = find_sidecars(root)
    grand = {}
    runs_read = 0

    if not sidecars:
        lines.append("%s: no usage sidecar (*%s) was found under %s, so "
                     "nothing at all is known about what this estate spent. "
                     "That is not the same as having spent nothing."
                     % (NODATA, SIDECAR_SUFFIX, root))
    else:
        lines.append("RUNS WITH RECORDED USAGE, under %s" % root)
        lines.append("")
        for path in sidecars:
            name = os.path.basename(os.path.dirname(path)) or path
            units, problem = read_sidecar(path)
            if problem:
                lines.append("  %-42s %s: the sidecar %s"
                             % (name, NODATA, problem))
                continue
            totals = sum_usage(units)
            if not totals:
                lines.append("  %-42s %s: the sidecar holds no recognised "
                             "usage field" % (name, NODATA))
                continue
            runs_read += 1
            for field, val in totals.items():
                grand[field] = grand.get(field, 0) + val
            lines.append("  %-42s %s"
                         % (name, "  ".join("%s %s" % (f, _cell(totals, f))
                                            for f in FIELDS)))
        lines.append("")
        lines.append("  %-42s %s"
                     % ("TOTAL, %d run(s)" % runs_read,
                        "  ".join("%s %s" % (f, _cell(grand, f))
                                  for f in FIELDS)))

    lines.append("")
    lines.append("dollars_spent: %s" % DOLLARS_REASON)
    lines.append("")
    lines.append(_ceiling_line(env))
    return lines, runs_read


def build_json(root, env=None):
    """The same figures as one object, computed by the same helpers the text
    report uses, so the two can never disagree."""
    grand, runs_read = totals_under(root)
    usd, enforced, why = model_worker.budget_ceiling(env)
    out = {f: (grand[f] if f in grand
               else "%s: no run under %s recorded %s" % (NODATA, root, f))
           for f in FIELDS}
    out["runs_read"] = runs_read
    out["dollars_spent"] = DOLLARS_REASON
    out["ceiling_usd"] = usd if usd is not None else "%s: %s" % (NODATA, why)
    out["ceiling_enforced"] = enforced
    out["ceiling_reason"] = why
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="where to look for usage sidecars "
                         "(default: %s)" % DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true",
                    help="the same figures as one JSON object")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.json:
        payload = build_json(args.root)
        print(json.dumps(payload, sort_keys=True, indent=1))
        return EXIT_OK if payload["runs_read"] else EXIT_NODATA

    lines, runs_read = build_report(args.root)
    print("\n".join(lines))
    return EXIT_OK if runs_read else EXIT_NODATA


if __name__ == "__main__":
    sys.exit(main())
