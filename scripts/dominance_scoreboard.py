#!/usr/bin/env python3
"""DOM-00.03: the one composite the Dominance rows (DOM-10 through DOM-50)
report into, and the guard that keeps it honest.

WHY THIS EXISTS, and the correction it carries. The founder's own Dominance
WBS listed thirty-one metrics. Averaged naively, thirty-one metrics nobody
acts on are just the same 71 NO-DATA lines this estate already fought once
(parity_gate.py, 2026-08-29, the directive with numbers nobody could trace)
wearing a new coat. The correction: every metric must name, AT BIRTH, the
decision it changes. A metric with no named decision is not admitted at all,
the same way feature_admission.py refuses a roadmap row with no measurable
outcome. This module is not a second copy of that admission gate; it is the
next stage, the one that turns a pile of already-admitted metric readings
into one number, or refuses to.

THE ESCAPE FOUND IN REVIEW, and why the fix moved outside the sweep. The
first cut of this module let each metric record declare its own
"mandatory": true/false, and gated the composite on whatever the sweep
itself claimed. That is not a gate, it is a suggestion box: a sweep gets
itself scored by simply OMITTING a mandatory metric's record entirely (an
absent record cannot be caught by scanning the records that ARE present),
or by writing "mandatory": false on its own record. Neither move is visible
from inside the sweep, so the authority for which metrics are mandatory
cannot live inside it either. evaluate() now takes mandatory_ids as a
second, required argument: the authoritative set, declared once, from
OUTSIDE the sweep (a registry file, via --mandatory on the command line).
An id in that set with no record in the sweep is a gap row, named and
blocking, exactly like an id whose record is simply unmeasured. A record
whose own id IS in that set is refused outright if it declares
"mandatory": false: a record may describe itself, it may not overrule the
registry that admitted it.

THE PROPERTY THIS MODULE ENFORCES (its deciding_property, unchanged from the
WBS row): no composite score is produced while any MANDATORY metric is
unmeasured, where "mandatory" now means "named in the caller's own
mandatory_ids", never "self-declared by the metric". A weighted average
that quietly treats a missing mandatory metric as zero, drops it from the
denominator, or lets the sweep vote itself out of the mandatory set, is
exactly the shape of the bug this module exists to prevent: it would let an
incomplete or self-serving Dominance sweep read as a finished, honest one.
Instead this module reports the GAP, one row per unmeasured mandatory
metric naming its decision when a decision is known, and refuses the
composite outright. See evaluate() below.

A metric counts as MEASURED only when it carries both a numeric value and a
non-empty evidence string. A value with no evidence is not a measurement,
it is an assertion (parity_gate.py's credit() drew the same line on
2026-08-29); this module treats it exactly like a missing value rather than
inventing a partial-trust tier for it.

Optional metrics (ids not named in mandatory_ids) may be unmeasured without
blocking the composite: they are reported in a separate, non-blocking gap
so nobody has to hunt for why the composite excluded them, but a run
missing only optional metrics still scores.

Python 3.9 floor, standard library only, no network. This module reads no
file of its own accord: main()'s --source and --mandatory arguments are the
only paths it opens, so a caller (or a test) supplies both explicitly
rather than this module assuming either exists under docs/plan.
"""
import argparse
import json
import sys

NODATA = "NO-DATA"
SCORED = "SCORED"

#: A metric record may carry exactly these keys. An unrecognised key means
#: the record was not built against this schema, and a scoreboard cannot
#: tell whether a stray key was meant to matter, so it refuses rather than
#: silently ignoring it (mirrors feature_admission.ALLOWED_KEYS).
ALLOWED_KEYS = frozenset((
    "metric_id", "decision", "mandatory", "weight", "value", "evidence",
    "label",
))

#: "mandatory" is deliberately absent here: it is an optional field on a
#: record now, never a required one, because a record's own claim about
#: itself is no longer what decides gating. See validate_metric().
REQUIRED_KEYS = ("metric_id", "decision")

DEFAULT_WEIGHT = 1.0


class DominanceError(Exception):
    """A metric record, a mandatory set, or the file either came from,
    could not be read as valid. Never raised for a metric that is simply
    unmeasured, or for a mandatory id with no record at all; those are
    NO-DATA, not a schema defect, and are reported in the gap rather than
    stopping the run."""


def _is_non_empty_string(value):
    return isinstance(value, str) and value.strip() != ""


def _is_finite_number(value):
    """True for an int or float that is not a bool and not NaN/inf. bool is
    a subclass of int in Python, and True/False silently passing for 1/0
    would let a typo'd flag masquerade as a measured value."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def validate_metric(record):
    """Raise DominanceError naming exactly what is wrong, or return None.

    Checked regardless of whether the metric ends up measured: metric_id
    and decision are the metric's identity, not its reading, and a metric
    with no named decision must never reach evaluate() at all, per the
    correction this module exists to carry. "mandatory", when present, is
    only checked for type here; whether it may legally be False is a
    question about the caller's mandatory_ids too, so that check lives in
    evaluate(), the only place both are in scope together.
    """
    if not isinstance(record, dict):
        raise DominanceError("metric record was not a mapping: %r" % (record,))

    for key in record:
        if key not in ALLOWED_KEYS:
            raise DominanceError("unrecognized field: %s" % key)

    for field in REQUIRED_KEYS:
        if field not in record:
            raise DominanceError("missing field: %s" % field)

    if not _is_non_empty_string(record["metric_id"]):
        raise DominanceError(
            "field metric_id was missing or not a non-empty string")

    if not _is_non_empty_string(record["decision"]):
        raise DominanceError(
            "metric %s: field decision was missing or not a non-empty "
            "string; a metric must name the decision it changes at birth"
            % record.get("metric_id"))

    if "mandatory" in record and not isinstance(record["mandatory"], bool):
        raise DominanceError(
            "metric %s: field mandatory was not a bool" % record["metric_id"])

    if "weight" in record:
        w = record["weight"]
        if not _is_finite_number(w) or w <= 0:
            raise DominanceError(
                "metric %s: field weight must be a positive number, got %r"
                % (record["metric_id"], w))

    if "value" in record and record["value"] is not None:
        v = record["value"]
        if not _is_finite_number(v) or not (0.0 <= v <= 1.0):
            raise DominanceError(
                "metric %s: field value must be a number in [0.0, 1.0], "
                "got %r" % (record["metric_id"], v))

    if "evidence" in record and not isinstance(record["evidence"], str):
        raise DominanceError(
            "metric %s: field evidence must be a string" % record["metric_id"])

    return None


def is_measured(record):
    """True only when this metric carries a real value AND a named piece
    of evidence for it. A value with no evidence is an assertion, not a
    measurement, and is treated exactly like a missing value."""
    value = record.get("value")
    if value is None or not _is_finite_number(value):
        return False
    return _is_non_empty_string(record.get("evidence"))


def _normalize_mandatory_ids(mandatory_ids):
    """frozenset of the declared mandatory ids, or raise.

    None is refused rather than treated as "zero mandatory metrics": a
    caller that means "this sweep has no mandatory metrics" must say so
    explicitly with an empty iterable. None means the caller never
    consulted a mandatory set at all, which is the omission half of the
    escape this function exists to close, so it is refused exactly like a
    malformed one rather than silently read as permissive.
    """
    if mandatory_ids is None:
        raise DominanceError(
            "no mandatory set declared: evaluate() requires mandatory_ids "
            "from outside the sweep, even an empty one, rather than "
            "inferring it from the sweep's own records")
    try:
        ids = list(mandatory_ids)
    except TypeError:
        raise DominanceError(
            "mandatory_ids must be an iterable of metric id strings, got %r"
            % (mandatory_ids,))

    seen = set()
    for mid in ids:
        if not _is_non_empty_string(mid):
            raise DominanceError(
                "mandatory_ids entry was not a non-empty string: %r" % (mid,))
        if mid in seen:
            raise DominanceError("duplicate id in mandatory_ids: %s" % mid)
        seen.add(mid)
    return frozenset(seen)


def evaluate(metrics, mandatory_ids):
    """Score a whole Dominance sweep, or refuse to.

    metrics is an iterable of metric records. mandatory_ids is the
    AUTHORITATIVE set of metric ids that are mandatory, supplied by the
    caller from OUTSIDE the sweep (a registry, not the sweep's own
    "mandatory" fields): see the module docstring for why a sweep cannot
    be trusted to name its own mandatory set. There is no default; a
    caller must pass mandatory_ids explicitly, even an empty frozenset,
    because a missing argument here is exactly the unchecked-omission
    shape this module exists to refuse.

    Every record is validated first (raises DominanceError on the first
    malformed one; a malformed metric definition is a defect in the sweep
    itself, never treated as an unmeasured metric). A record whose id is
    in mandatory_ids is refused outright if it declares "mandatory": false
    on itself: a record may not demote itself out of a set it does not
    control.

    Returns a dict:

      verdict: "SCORED" or "NO-DATA"
      composite: float in [0.0, 100.0], or None when verdict is NO-DATA
      rows: one entry per metric with a record, in input order, followed
        by one synthetic row for each mandatory id that has NO record in
        the sweep at all (sorted, for determinism), carrying
        "present": False and "decision": None (there is no record to read
        a decision from)
      mandatory_gap: rows (present or not) whose id is in mandatory_ids
        and which are unmeasured; empty exactly when verdict is "SCORED"
      optional_gap: rows present in the sweep whose id is NOT in
        mandatory_ids and which are unmeasured; never blocks the composite

    Raises DominanceError if metrics is empty, or if two records share a
    metric_id: a Dominance scoreboard with no metrics, or with a duplicate
    silently blending two decisions into one line, computes nothing
    trustworthy.
    """
    mandatory_ids = _normalize_mandatory_ids(mandatory_ids)

    records = list(metrics)
    if not records:
        raise DominanceError("no metrics given: nothing to score")

    seen_ids = set()
    rows = []
    for record in records:
        validate_metric(record)
        metric_id = record["metric_id"]
        if metric_id in seen_ids:
            raise DominanceError("duplicate metric_id: %s" % metric_id)
        seen_ids.add(metric_id)

        if metric_id in mandatory_ids and record.get("mandatory") is False:
            raise DominanceError(
                "metric %s: declared mandatory by the registry, a record "
                "may not demote itself with \"mandatory\": false"
                % metric_id)

        is_mandatory = metric_id in mandatory_ids
        measured = is_measured(record)
        rows.append({
            "metric_id": metric_id,
            "decision": record["decision"],
            "mandatory": is_mandatory,
            "present": True,
            "weight": float(record.get("weight", DEFAULT_WEIGHT)),
            "measured": measured,
            "value": record.get("value") if measured else None,
            "evidence": record.get("evidence", "") if measured else "",
            "label": record.get("label", metric_id),
        })

    # An id the registry names as mandatory with no record at all in the
    # sweep: the core case this fix closes. Sorted for a deterministic
    # report; there is no input order to preserve for a row that never
    # arrived.
    for metric_id in sorted(mandatory_ids - seen_ids):
        rows.append({
            "metric_id": metric_id,
            "decision": None,
            "mandatory": True,
            "present": False,
            "weight": None,
            "measured": False,
            "value": None,
            "evidence": "",
            "label": metric_id,
        })

    mandatory_gap = [r for r in rows if r["mandatory"] and not r["measured"]]
    optional_gap = [r for r in rows if not r["mandatory"] and not r["measured"]]

    if mandatory_gap:
        return {
            "verdict": NODATA,
            "composite": None,
            "rows": rows,
            "mandatory_gap": mandatory_gap,
            "optional_gap": optional_gap,
        }

    measured_rows = [r for r in rows if r["measured"]]
    total_weight = sum(r["weight"] for r in measured_rows)
    # Reached only when mandatory_gap is empty. total_weight can still be
    # zero (every metric in the sweep is optional and unmeasured), so this
    # is not dead code: it is what keeps that case NO-DATA instead of a
    # division by zero or a false SCORED.
    composite = (100.0 * sum(r["value"] * r["weight"] for r in measured_rows)
                 / total_weight) if total_weight > 0 else None

    return {
        "verdict": SCORED if composite is not None else NODATA,
        "composite": composite,
        "rows": rows,
        "mandatory_gap": mandatory_gap,
        "optional_gap": optional_gap,
    }


def bar(pct, width=26):
    if pct is None:
        return "[%s] %s" % ("?" * width, NODATA)
    f = int(round(width * pct / 100.0))
    return "[%s%s] %3.0f%%" % ("#" * f, "." * (width - f), pct)


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError as exc:
        raise DominanceError("could not read %s: %s" % (path, exc))
    except ValueError as exc:
        raise DominanceError("could not parse %s as JSON: %s" % (path, exc))
    if not isinstance(doc, dict) or "metrics" not in doc:
        raise DominanceError(
            "%s: top level must be an object with a \"metrics\" list" % path)
    metrics = doc["metrics"]
    if not isinstance(metrics, list):
        raise DominanceError("%s: \"metrics\" must be a list" % path)
    return metrics


def _load_mandatory(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError as exc:
        raise DominanceError("could not read %s: %s" % (path, exc))
    except ValueError as exc:
        raise DominanceError("could not parse %s as JSON: %s" % (path, exc))
    if not isinstance(doc, dict) or "mandatory_ids" not in doc:
        raise DominanceError(
            "%s: top level must be an object with a \"mandatory_ids\" list"
            % path)
    ids = doc["mandatory_ids"]
    if not isinstance(ids, list):
        raise DominanceError("%s: \"mandatory_ids\" must be a list" % path)
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True,
                     help="path to a JSON file holding {\"metrics\": [...]}")
    ap.add_argument("--mandatory", default=None,
                     help="path to a JSON file holding "
                          "{\"mandatory_ids\": [...]}, the authoritative "
                          "mandatory set declared outside the sweep. "
                          "Omitting this flag is NO-DATA, not a sweep with "
                          "zero mandatory metrics; to declare that "
                          "deliberately, pass a file with an empty list.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.mandatory is None:
        reason = ("no mandatory set declared (pass --mandatory FILE, even "
                   "one naming an empty list, rather than scoring with no "
                   "registry at all)")
        if args.json:
            print(json.dumps({"verdict": NODATA, "composite": None,
                              "reason": reason}, indent=2, sort_keys=True))
        else:
            print("%s: %s" % (NODATA, reason), file=sys.stderr)
        return 1

    try:
        metrics = _load(args.source)
        mandatory_ids = _load_mandatory(args.mandatory)
        result = evaluate(metrics, mandatory_ids)
    except DominanceError as exc:
        print("%s: %s" % (NODATA, exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["verdict"] == SCORED else 1

    print("DOMINANCE SCOREBOARD")
    print("Composite: %s" % bar(result["composite"]))
    print("")
    for r in result["rows"]:
        state = ("%6.1f%%" % (100.0 * r["value"]) if r["measured"]
                  else NODATA)
        decision = r["decision"] or "(no record present in the sweep for this id)"
        print("  %-28s %-10s weight %4s  %s%s"
              % (r["label"][:28], state,
                 ("%4.1f" % r["weight"]) if r["weight"] is not None else "  --",
                 "MANDATORY" if r["mandatory"] else "optional",
                 "" if r["measured"] else "  -> %s" % decision))
    print("")
    if result["verdict"] != SCORED:
        # Two ways to land here: named mandatory metrics are unmeasured or
        # absent, or (no mandatory gap at all, e.g. every metric is
        # optional) nothing was measured, so there is nothing to average.
        # Checking verdict here rather than mandatory_gap alone matters: a
        # truthiness check on mandatory_gap reads the second case as
        # scored, printing "SCORED" and exiting 0 with a None composite, a
        # false pass this module exists to refuse.
        if result["mandatory_gap"]:
            print("NO-DATA: %d mandatory metric(s) unmeasured, so no "
                  "composite is produced. Each blocks the decision it "
                  "exists to change:" % len(result["mandatory_gap"]))
            for r in result["mandatory_gap"]:
                decision = r["decision"] or "(no record present in the sweep)"
                tag = "" if r["present"] else " [absent from the sweep]"
                print("  - %s: %s%s" % (r["metric_id"], decision, tag))
            if result["optional_gap"]:
                print("(%d optional metric(s) also unmeasured, not blocking)"
                      % len(result["optional_gap"]))
        else:
            print("NO-DATA: no metric was measured, so no composite is "
                  "produced.")
        return 1

    print("SCORED: every mandatory metric is measured.")
    if result["optional_gap"]:
        print("%d optional metric(s) unmeasured, excluded from the "
              "composite, not blocking:" % len(result["optional_gap"]))
        for r in result["optional_gap"]:
            print("  - %s: %s" % (r["metric_id"], r["decision"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
