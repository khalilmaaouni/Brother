#!/usr/bin/env python3
"""BrotherDS: every number that reaches a decision carries its proof.

A claim is one number that reaches a decision. This tool re-derives it, applies
the refusals, and writes a receipt. It never decides that a claim is true; it
decides whether the claim is PROVEN, DISPROVEN, or UNEXAMINED, and reality
decides the rest later via `score`.

    python3 bds.py new      RGM-014 claims/rgm-014.json   scaffold a claim
    python3 bds.py author   claims/rgm-014.json           write one from key=value stdin
    python3 bds.py check    claims/rgm-014.json           run the ten gates
    python3 bds.py receipt  claims/rgm-014.json [out.md]  the page a human reads
    python3 bds.py register claims/rgm-014.json           make its metric definition the reference
    python3 bds.py score    claims/rgm-014.json 8123 "ops lead" 2026-09-30 [cause] [lesson]
    python3 bds.py ledger   claims/ [--propose-lessons]   the verified claim rate, band coverage,
                                                          and the gates the team keeps failing
    python3 bds.py mdm-eval review review.csv --merge-threshold 0.8
                                                          derive precision and recall from a review
    python3 bds.py mdm-eval clusters gold.csv             gold-subset pairwise and B-cubed (M10)
    python3 bds.py mdm-eval drift ref.csv cur.csv         match-score drift as PSI (M13)
    python3 bds.py mdm-audit audit results.csv --plan plan.csv
                                                          audit a matching run, write the review plan
    python3 bds.py mdm-audit evaluate plan-labelled.csv --results results.csv
                                                          derive per-pathway and unmatched evidence
    python3 bds.py chain                                  the stages, and what each stands in
    python3 bds.py stage    outcome                       is this a stage an item may serve
    python3 bds.py passport [.sbe/passport.json]          consume BrotherMode's change passport
    python3 bds.py handoff  [.sbe/handoff.json]           consume BrotherSBE's handoff package
    python3 bds.py backlog  [docs/plan/QUEUE.json]        every item against the chain
    python3 bds.py selftest

Verdicts are PASS, FAIL, NO-DATA, matching the BrotherSBE tuple exactly.
NO-DATA is never a pass and never a block.

Requires: duckdb, only for claims whose origin is SYSTEM. Every other origin
runs on the standard library alone.
"""
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import vault_bridge  # sibling module, advisory only; no gate ever reads it
import forecast_score  # sibling module: weighted interval score and band coverage
try:
    import lessons  # sibling module: gates the team keeps failing, as lesson text
except ImportError:  # the ledger reports the section as NO-DATA without it
    lessons = None
import packs  # sibling module: the EXPERIMENT/DETECTION/MASTER_DATA/PIPELINE
              # pack registry; packs.py never imports bds, so no circular
              # import

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

# The five origins. A claim that does not name one cannot be checked, because
# the questions that interrogate a warehouse query are not the questions that
# interrogate an expert's judgement.
ORIGINS = {
    "SYSTEM": "a query against a governed source system",
    "THIRD_PARTY": "an external or vendor dataset, carrying its own provenance",
    "ELICITED": "expert judgement, captured through a structured protocol",
    "ASSUMPTION": "stated and unverified, requiring sensitivity analysis",
    "HYPOTHESIS": "explicitly untested; may inform a test, may not alone decide",
}

# Words that assert one thing made another thing happen. Any of these in a
# statement demands a design that can license the assertion.
CAUSAL_TOKENS = (
    "caused", "causes", "drove", "drives", "driven by", "led to", "leads to",
    "resulted in", "results in", "incremental", "incrementality", "lift",
    "uplift", "boosted", "boosts", "impact of", "effect of", "due to",
    "because of", "attributable", "thanks to", "responsible for",
)

# One deterministic, tense-matched replacement per CAUSAL_TOKENS entry, used by
# safe_wording() below. Never a paraphrase engine: every token maps to exactly
# one phrase, so the same statement always rewrites to the same safe wording.
CAUSAL_MAP = {
    "caused": "coincided with",
    "causes": "coincides with",
    "drove": "coincided with",
    "drives": "coincides with",
    "driven by": "alongside",
    "led to": "coincided with",
    "leads to": "coincides with",
    "resulted in": "coincided with",
    "results in": "coincides with",
    "incremental": "higher",
    "incrementality": "higher",
    "lift": "higher",
    "uplift": "higher",
    "boosted": "coincided with",
    "boosts": "coincides with",
    "impact of": "alongside",
    "effect of": "alongside",
    "due to": "during",
    "because of": "during",
    "attributable": "alongside",
    "thanks to": "during",
    "responsible for": "alongside",
}

# Designs that can license a causal claim, each with the assumption it rests on.
CAUSAL_DESIGNS = {
    "rct": "treatment assignment is random, so groups differ only by chance",
    "randomised_holdout": "the held-out group was randomly chosen and untreated",
    "geo_experiment": "treated and control geographies were randomly assigned",
    "switchback": "treatment periods were randomised and carryover is bounded",
    "difference_in_differences": "treated and control trends were parallel before treatment",
    "synthetic_control": "the weighted donor pool tracks the treated unit pre-treatment",
    "event_study": "no other event coincides with the studied event",
    "regression_discontinuity": "units cannot precisely manipulate the running variable",
    "instrumental_variable": "the instrument affects the outcome only through the treatment",
}

# The seven claim types a pack (packs.py) can be written against. Declaring
# one outside this set is a hard FAIL (gate_claim_type below). Inferring one
# is deliberately narrow: only CAUSAL, FORECAST and MASTER_DATA can ever be
# inferred from a claim's own content; DESCRIPTIVE, EXPERIMENT, DETECTION and
# PIPELINE require a person to say so, never silence.
CLAIM_TYPES = ("DESCRIPTIVE", "FORECAST", "CAUSAL", "MASTER_DATA",
               "EXPERIMENT", "DETECTION", "PIPELINE")


def infer_claim_type(claim):
    """Deterministic and conservative: at most one rule fires, in this fixed
    order, and only these three types are ever inferred. Returns None when
    nothing about the claim's own content says which of the three it is."""
    text = (claim.get("statement") or "").lower()
    if any(t in text for t in CAUSAL_TOKENS):
        return "CAUSAL"
    if claim.get("accuracy"):
        return "FORECAST"
    m = claim.get("match")
    if isinstance(m, dict) and any(k in m for k in ("precision", "recall", "threshold")):
        return "MASTER_DATA"
    return None


def effective_claim_type(claim):
    """(value_or_None, declared) after inference, never after validation: a
    bad declared value still comes back here unchanged. gate_claim_type is
    the only place that FAILs it; this is what check() hands to the packs."""
    declared = claim.get("claim_type")
    if _answered(declared):
        return declared, True
    return infer_claim_type(claim), False


def _fmt(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        # A whole-numbered float is a quantity, not a measurement. Rendering
        # 1050000.0 as 1.05e+06 in a receipt a person reads is a defect.
        if v == int(v) and abs(v) >= 1000:
            return "{:,}".format(int(v))
        return ("%.6g" % v)
    if isinstance(v, int):
        return "{:,}".format(v)
    return str(v)


def _expand(p):
    return Path(os.path.expanduser(str(p)))


class Finding(object):
    """One gate result. `gate` names the rule, `verdict` is the tuple value."""

    def __init__(self, gate, verdict, detail):
        self.gate = gate
        self.verdict = verdict
        self.detail = detail

    def line(self):
        return "%-8s %-26s %s" % (self.verdict, self.gate, self.detail)


# ---------------------------------------------------------------- source state

def source_state(path):
    """Identity of the data a claim was computed against.

    Size and mtime, not a content hash: hashing a 191MB warehouse on every check
    costs seconds for a property that size-and-mtime already detects in practice.
    ponytail: swap in a content hash if a source is ever rewritten in place at
    identical size, which is the one case this misses.
    """
    p = _expand(path)
    if not p.exists():
        return None
    st = p.stat()
    return {"path": str(p), "bytes": st.st_size, "mtime": int(st.st_mtime)}


# ---------------------------------------------------------------- derivations

def run_sql(db_path, sql):
    import duckdb  # imported lazily so non-SYSTEM claims need no dependency
    con = duckdb.connect(str(_expand(db_path)), read_only=True)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return row[0]


def evaluate_derivations(claim):
    """Run every derivation and return (value_paths, findings).

    A derivation either RECOMPUTES THE CLAIMED VALUE or it is a supporting fact.
    Only the first kind may be compared, and conflating them is how a month count
    ends up being checked against a percentage. Mark a supporting query with
    "computes_value": false; the default is true.

    Two independent paths to the same number is the whole point. One path is
    NO-DATA on independent re-derivation, not a pass: it proves the query runs,
    not that the number is right.
    """
    findings = []
    ev = claim.get("evidence") or {}
    derivations = ev.get("derivations") or []
    db = ev.get("source")

    if not derivations:
        findings.append(Finding("G5.rederivation", NODATA,
                                "no derivation given; nothing was recomputed"))
        return [], findings

    if db and source_state(db) is None:
        findings.append(Finding("G5.rederivation", FAIL,
                                "source does not exist: %s" % db))
        return [], findings

    value_paths, supporting = [], []
    for d in derivations:
        try:
            v = run_sql(db, d["sql"])
        except Exception as exc:  # a broken query is a FAIL, never a skip
            findings.append(Finding("G5.rederivation", FAIL,
                                    "derivation '%s' raised %s: %s"
                                    % (d.get("name", "?"), type(exc).__name__, exc)))
            return value_paths, findings
        pair = (d.get("name", "?"), v)
        (value_paths if d.get("computes_value", True) else supporting).append(pair)

    for n, v in supporting:
        findings.append(Finding("G5.supporting", PASS,
                                "%s = %s (context, not compared)" % (n, _fmt(v))))

    if not value_paths:
        findings.append(Finding("G5.rederivation", NODATA,
                                "no derivation recomputes the claimed value; "
                                "every query given is supporting context"))
    elif len(value_paths) == 1:
        findings.append(Finding("G5.rederivation", NODATA,
                                "one derivation only ('%s'); the number was "
                                "recomputed but not independently corroborated"
                                % value_paths[0][0]))
    else:
        distinct = set(_fmt(v) for _, v in value_paths)
        if len(distinct) == 1:
            findings.append(Finding("G5.rederivation", PASS,
                                    "%d independent derivations agree at %s"
                                    % (len(value_paths), _fmt(value_paths[0][1]))))
        else:
            expl = claim.get("expected_derivation_gap")
            detail = "derivations disagree: " + ", ".join(
                "%s=%s" % (n, _fmt(v)) for n, v in value_paths)
            if expl:
                findings.append(Finding("G5.rederivation", NODATA,
                                        detail + " | gap declared expected: " + expl))
            else:
                findings.append(Finding("G5.rederivation", FAIL, detail))
    return value_paths, findings


def evaluate_comparison(claim, value_paths):
    """The dual run: a SYSTEM derivation paired with a THIRD_PARTY or ELICITED
    comparison against the incumbent artifact somebody already trusts.

    This answers a different question than G5.rederivation. Rederivation asks
    "is this internally reproducible" by running two SQL paths against one
    governed source. A comparison cannot be SQL against that source by
    definition, since the whole point is a second, independent, non-system
    read (a vendor panel, an expert's number, a spreadsheet). So this is not a
    third SQL path: it is the claim's own recomputed value, held up against a
    value that was never a query at all.

    No comparison block: this function returns nothing and the SQL-only path
    is exactly as it was before this existed.
    """
    ev = claim.get("evidence") or {}
    comp = ev.get("comparison")
    if not comp:
        return []

    cls = comp.get("class")
    if cls not in ("THIRD_PARTY", "ELICITED"):
        return [Finding("G5.comparison", FAIL,
                        "comparison.class must be THIRD_PARTY or ELICITED, got %r"
                        % cls)]

    missing = [f for f in ("incumbent", "incumbent_value", "tolerance")
               if not _answered(comp.get(f))]
    if missing:
        return [Finding("G5.comparison", NODATA,
                        "comparison missing %s" % ", ".join(missing))]

    proto_required = PROTOCOL_REQUIRED.get(cls, [])
    proto = comp.get("protocol") or {}
    proto_missing = [k for k in proto_required if not _answered(proto.get(k))]
    if proto_missing:
        return [Finding("G5.comparison", NODATA,
                        "%s comparison protocol incomplete, missing: %s"
                        % (cls, ", ".join(proto_missing)))]

    if not value_paths:
        return [Finding("G5.comparison", NODATA,
                        "the system side recomputed no value; nothing to "
                        "compare the incumbent against")]
    system_value = value_paths[0][1]

    try:
        drift = abs(float(system_value) - float(comp["incumbent_value"]))
        tol = float(comp["tolerance"])
    except (TypeError, ValueError):
        return [Finding("G5.comparison", FAIL,
                        "incumbent_value and tolerance must be numeric, got "
                        "%r and %r" % (comp.get("incumbent_value"), comp.get("tolerance")))]

    detail = ("system %s vs %s incumbent (%s) %s, drift %s, tolerance %s"
             % (_fmt(system_value), cls, comp["incumbent"],
                _fmt(comp["incumbent_value"]), _fmt(drift), _fmt(tol)))
    if drift <= tol:
        return [Finding("G5.comparison", PASS, "agrees: " + detail)]
    return [Finding("G5.comparison", FAIL, "disagrees: " + detail)]


# ---------------------------------------------------------------------- gates

def gate_origin(claim):
    o = claim.get("origin")
    if o not in ORIGINS:
        return Finding("G2.origin", FAIL,
                       "origin must be one of %s, got %r"
                       % (", ".join(sorted(ORIGINS)), o))
    return Finding("G2.origin", PASS, "%s (%s)" % (o, ORIGINS[o]))


# Boilerplate that says nothing a reviewer could act on. The list must be
# non-empty (checked below); this refuses the non-empty list that still
# says nothing.
BOILERPLATE_NOT_ESTABLISHED = (
    "none", "n/a", "not applicable", "standard caveats apply",
    "usual limitations", "tbd",
)


def gate_not_established(claim):
    """Field four. It may never be empty, and it may never be boilerplate.
    A claim asserting that nothing is unexamined is the precise lie this
    whole product exists to prevent."""
    ne = claim.get("not_established")
    if not ne:
        return Finding("G1.not_established", FAIL,
                       "empty; every claim has limits and they must be written down")
    if not isinstance(ne, list):
        return Finding("G1.not_established", FAIL, "must be a list of statements")
    for item in ne:
        if str(item).strip().lower() in BOILERPLATE_NOT_ESTABLISHED:
            return Finding("G1.not_established", FAIL,
                           "boilerplate limit declared (%r); this says nothing "
                           "a reviewer could act on" % item)
    return Finding("G1.not_established", PASS,
                   "%d limit(s) declared" % len(ne))


def _ne_reason(u):
    """The NOT_ESTABLISHED explanation: 'why' if given, else 'reason'. Both
    names are accepted so an existing claim written against 'why' and a new
    one written against 'reason' both carry their explanation the same way."""
    return u.get("why") if _answered(u.get("why")) else u.get("reason")


def _quantile_pairs(q):
    """Sorted (probability, value) pairs from an uncertainty.quantiles map,
    or raises ValueError naming what is wrong. Shared by G3.uncertainty so
    the gate's own wording is the single place this shape is validated;
    score() re-reads the same map independently since a claim can be scored
    without ever going through check().
    """
    if not isinstance(q, dict) or not q:
        raise ValueError("kind=quantiles requires 'quantiles', a map of "
                         "probability to value")
    missing = [p for p in ("0.1", "0.5", "0.9") if p not in q]
    if missing:
        raise ValueError("quantiles must include %s" % ", ".join(missing))
    try:
        pairs = sorted((float(p), float(v)) for p, v in q.items())
    except (TypeError, ValueError):
        raise ValueError("every quantile probability and value must be numeric")
    for (p1, v1), (p2, v2) in zip(pairs, pairs[1:]):
        if v2 < v1:
            raise ValueError(
                "quantiles are not monotone non-decreasing: %s=%s then %s=%s"
                % (_fmt(p1), _fmt(v1), _fmt(p2), _fmt(v2)))
    return pairs


def gate_uncertainty(claim):
    u = claim.get("uncertainty")
    if not isinstance(u, dict) or "kind" not in u:
        return Finding("G3.uncertainty", FAIL,
                       "missing; give an interval and its method, or state "
                       "kind=NOT_ESTABLISHED with a reason")
    kind = u["kind"]
    if kind == "NOT_ESTABLISHED":
        explanation = _ne_reason(u)
        if not _answered(explanation):
            return Finding("G3.uncertainty", FAIL,
                           "NOT_ESTABLISHED requires 'why' (or 'reason')")
        return Finding("G3.uncertainty", NODATA,
                       "not established: %s" % explanation)
    if kind == "quantiles":
        try:
            pairs = _quantile_pairs(u.get("quantiles"))
        except ValueError as exc:
            return Finding("G3.uncertainty", FAIL, str(exc))
        return Finding("G3.uncertainty", PASS,
                       "quantiles " + ", ".join(
                           "%s=%s" % (_fmt(p), _fmt(v)) for p, v in pairs))
    if "interval" not in u or "method" not in u:
        return Finding("G3.uncertainty", FAIL,
                       "kind=%s requires 'interval' and 'method'" % kind)
    return Finding("G3.uncertainty", PASS,
                   "%s %s by %s" % (kind, u["interval"], u["method"]))


def gate_causal(claim):
    text = (claim.get("statement") or "").lower()
    hits = [t for t in CAUSAL_TOKENS if t in text]
    if not hits:
        return Finding("G4.causal", PASS, "no causal assertion in the statement")

    if claim.get("origin") == "HYPOTHESIS":
        return Finding("G4.causal", NODATA,
                       "causal wording (%s) permitted because the claim is "
                       "declared a HYPOTHESIS; it may not alone reach a decision"
                       % ", ".join(hits))

    design = claim.get("design") or {}
    kind = design.get("kind")
    if kind not in CAUSAL_DESIGNS:
        return Finding("G4.causal", FAIL,
                       "statement asserts causation (%s) but names no valid "
                       "design; one of: %s"
                       % (", ".join(hits), ", ".join(sorted(CAUSAL_DESIGNS))))
    if not design.get("assumption_test"):
        return Finding("G4.causal", FAIL,
                       "design '%s' rests on: %s. No assumption_test given, so "
                       "the assumption is asserted, not checked"
                       % (kind, CAUSAL_DESIGNS[kind]))
    return Finding("G4.causal", PASS,
                   "%s; assumption '%s' tested by %s"
                   % (kind, CAUSAL_DESIGNS[kind], design["assumption_test"]))


def gate_value_matches(claim, values, computed_metrics=None):
    """Does the number the claim states still come back?

    For an accuracy claim the stated value IS the metric, so the thing to
    compare against is the metric this tool just computed, not a warehouse
    query. Getting this wrong is what made an earlier version check a WAPE
    against a month count.
    """
    recorded = claim.get("value")
    if recorded is None:
        return Finding("G7.value", NODATA, "claim records no value")

    acc = claim.get("accuracy") or {}
    named = acc.get("reported_metric")
    if named and computed_metrics and named in computed_metrics:
        got, source = computed_metrics[named], "recomputed %s" % named
    elif values:
        got, source = values[0][1], "warehouse"
    else:
        return Finding("G7.value", NODATA, "nothing recomputed to compare against")

    tol = claim.get("tolerance", 0)
    try:
        drift = abs(float(got) - float(recorded))
        ok = drift <= float(tol)
    except (TypeError, ValueError):
        ok = (_fmt(got) == _fmt(recorded))
        drift = None
    if ok:
        return Finding("G7.value", PASS,
                       "recorded %s reproduces (%s)" % (_fmt(recorded), source))
    return Finding("G7.value", FAIL,
                   "recorded %s, %s returns %s%s"
                   % (_fmt(recorded), source, _fmt(got),
                      "" if drift is None else " (drift %s)" % _fmt(drift)))


def _norm_definition(text):
    """Whitespace and case are not definition differences. Anything else is."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


# metric.definition may be a STRING (free prose) or an OBJECT shaped like the
# Chinese 口径 (Alibaba OneData): {base, modifiers, window}. A structured
# definition compares field by field instead of as one blob of text, so a
# single differing modifier names itself instead of just "the text differs".
def _fmt_structured_definition(d):
    """One line: 'base; modifiers; window', for the compact card and for a
    G10 mismatch detail."""
    if not isinstance(d, dict):
        return _fmt(d)
    modifiers = ", ".join(str(x) for x in (d.get("modifiers") or []))
    return "%s; %s; %s" % (d.get("base", ""), modifiers, d.get("window", ""))


def _definition_diff(a, b):
    """Field names where two structured definitions disagree: base, the
    sorted modifier list (order never matters), and window."""
    diffs = []
    if _norm_definition(a.get("base")) != _norm_definition(b.get("base")):
        diffs.append("base")
    am = sorted(_norm_definition(x) for x in (a.get("modifiers") or []))
    bm = sorted(_norm_definition(x) for x in (b.get("modifiers") or []))
    if am != bm:
        diffs.append("modifiers")
    if _norm_definition(a.get("window")) != _norm_definition(b.get("window")):
        diffs.append("window")
    return diffs


def _definitions_equal(a, b):
    """True when two metric.definition values (string or structured) say the
    same thing. A string compared against a structured object is never
    equal here; the caller that needs to tell 'different' apart from
    'incomparable shapes' is gate_definition, which reports that case as its
    own NO-DATA rather than folding it into this boolean."""
    if isinstance(a, dict) and isinstance(b, dict):
        return not _definition_diff(a, b)
    if isinstance(a, dict) or isinstance(b, dict):
        return False
    return _norm_definition(a) == _norm_definition(b)


def definition_registry(path):
    p = _expand(path)
    if not p.exists():
        return {}
    with open(p) as fh:
        return json.load(fh)


def gate_definition(claim, registry_path):
    """Do two people mean the same thing by the same word?

    This exists because reproduction was never the hard problem. A number can
    re-derive perfectly from its own query and still disagree with the number
    somebody else computed under the same name, because the two definitions
    differ. Gates G5 and G7 cannot see that; only a registry across claims can.

    First use of a metric name is NO-DATA, not PASS: one claim defines nothing,
    it only proposes. Disagreement is a FAIL that names the claim it collides
    with, so the argument is about the definition rather than about the numbers.
    """
    m = claim.get("metric")
    if not m or not m.get("name"):
        return Finding("G10.definition", NODATA,
                       "no metric name declared, so this number cannot be "
                       "compared with anyone else's number of the same name")
    name = m["name"]
    text = m.get("definition")
    if not text:
        return Finding("G10.definition", FAIL,
                       "metric '%s' is named but not defined; a name without a "
                       "definition is exactly what drifts between teams" % name)

    reg = definition_registry(registry_path)
    prior = reg.get(name)

    if prior is None:
        return Finding("G10.definition", NODATA,
                       "first recorded use of metric '%s'; nothing to disagree "
                       "with yet. Run 'bds register' to make this the reference"
                       % name)

    prior_def = prior.get("definition")
    mine_structured = isinstance(text, dict)
    prior_structured = isinstance(prior_def, dict)

    if mine_structured != prior_structured:
        return Finding("G10.definition", NODATA,
                       "metric '%s' cannot be compared: one side is "
                       "structured and the other is prose" % name)

    if mine_structured:
        diffs = _definition_diff(text, prior_def)
        if not diffs:
            return Finding("G10.definition", PASS,
                           "metric '%s' matches the structured definition "
                           "registered by %s"
                           % (name, prior.get("claim_id", "?")))
        return Finding("G10.definition", FAIL,
                       "metric '%s' is defined differently here than in "
                       "claim %s, differing on: %s. Registered: %s. This "
                       "claim: %s"
                       % (name, prior.get("claim_id", "?"), ", ".join(diffs),
                          _fmt_structured_definition(prior_def),
                          _fmt_structured_definition(text)))

    mine = _norm_definition(text)
    if _norm_definition(prior_def) == mine:
        return Finding("G10.definition", PASS,
                       "metric '%s' matches the definition registered by %s"
                       % (name, prior.get("claim_id", "?")))
    return Finding("G10.definition", FAIL,
                   "metric '%s' is defined differently here than in claim %s. "
                   "Two numbers under one name. Registered: %r. This claim: %r"
                   % (name, prior.get("claim_id", "?"), prior_def, text))


def register_definition(claim, registry_path):
    m = claim.get("metric") or {}
    if not m.get("name") or not m.get("definition"):
        print("claim declares no named-and-defined metric; nothing to register")
        return 1
    p = _expand(registry_path)
    reg = definition_registry(p)
    name = m["name"]
    prior = reg.get(name)
    if prior and not _definitions_equal(prior.get("definition"), m["definition"]):
        print("REFUSED. Metric %r is already registered by claim %s with a "
              "different definition.\n  registered: %s\n  yours:      %s\n"
              "Resolve which definition is correct with the other author. "
              "Overwriting silently is the drift this registry exists to stop."
              % (name, prior.get("claim_id", "?"), prior.get("definition"),
                 m["definition"]))
        return 1
    reg[name] = {"definition": m["definition"],
                 "claim_id": claim.get("id"),
                 "source": m.get("definition_source", "not stated")}
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as fh:
        json.dump(reg, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    print("registered %r from claim %s in %s" % (name, claim.get("id"), p))
    return 0


def gate_grain(claim):
    """At what level was this computed, and is that level actually unique?

    A number without its grain cannot be interpreted, and a grain that is not
    unique in the source is how a join fans out. This gate exists because the
    first analysis run against the pilot warehouse grouped by product_id alone,
    and product_id turned out to cover 24 different pack sizes.
    """
    g = claim.get("grain")
    if not g:
        return [Finding("G9.grain", FAIL,
                        "no grain declared; state the level this number was "
                        "computed at (per order line, per venue, per month)")]
    out = [Finding("G9.grain", PASS, str(g))]

    sql = claim.get("grain_uniqueness_sql")
    if not sql:
        out.append(Finding("G9.uniqueness", NODATA,
                           "grain is declared but not verified; give "
                           "grain_uniqueness_sql returning the number of "
                           "duplicate key groups"))
        return out
    db = (claim.get("evidence") or {}).get("source")
    try:
        dupes = run_sql(db, sql)
    except Exception as exc:
        out.append(Finding("G9.uniqueness", FAIL,
                           "grain check raised %s: %s" % (type(exc).__name__, exc)))
        return out
    if dupes:
        out.append(Finding("G9.uniqueness", FAIL,
                           "%s key group(s) are not unique at the declared "
                           "grain; any join on this key fans out" % _fmt(dupes)))
    else:
        out.append(Finding("G9.uniqueness", PASS,
                           "declared key is unique in the source"))
    return out


# What each non-SYSTEM origin's protocol must answer. Shared by gate_origin_
# protocol (the claim's own origin) and evaluate_comparison (a comparison's
# origin class), because the questions that interrogate a vendor dataset are
# the same questions whether the vendor dataset IS the claim or is standing
# next to a SYSTEM claim as its incumbent comparison.
PROTOCOL_REQUIRED = {
    "THIRD_PARTY": ["provider", "collection_method", "coverage", "known_biases"],
    "ELICITED": ["expert_role", "elicitation_protocol", "calibration_question",
                 "seed_score"],
    "ASSUMPTION": ["stated_by", "sensitivity_range", "sensitivity_result"],
    "HYPOTHESIS": ["test_that_would_settle_it", "cost_of_being_wrong"],
    "SYSTEM": [],
}


def gate_origin_protocol(claim):
    """Each origin is interrogated by its own question set. A missing answer is
    NO-DATA, which is honest, not a failure. A wrong-shaped answer is a FAIL."""
    o = claim.get("origin")
    p = claim.get("protocol") or {}
    required = PROTOCOL_REQUIRED.get(o, [])

    if not required:
        return Finding("G8.protocol", PASS, "SYSTEM claims are interrogated by "
                                            "re-derivation, not by protocol")
    missing = [k for k in required if not p.get(k)]
    if missing:
        return Finding("G8.protocol", NODATA,
                       "%s protocol incomplete, missing: %s"
                       % (o, ", ".join(missing)))
    return Finding("G8.protocol", PASS,
                   "%s protocol complete (%s)" % (o, ", ".join(required)))


def gate_claim_type(claim):
    """G15. A declared value outside CLAIM_TYPES is a FAIL naming it. An
    inferred value is still NO-DATA (it was not declared); an undeclared,
    uninferrable claim is NO-DATA with the bare contract line the compact
    card prints verbatim."""
    declared = claim.get("claim_type")
    if _answered(declared):
        if declared not in CLAIM_TYPES:
            return Finding("G15.claim_type", FAIL,
                           "claim_type must be one of %s, got %r"
                           % (", ".join(CLAIM_TYPES), declared))
        return Finding("G15.claim_type", PASS, "%s (declared)" % declared)
    inferred = infer_claim_type(claim)
    if inferred:
        return Finding("G15.claim_type", NODATA,
                       "not declared; inferred %s from the claim's own content"
                       % inferred)
    return Finding("G15.claim_type", NODATA, "(not declared)")


# ----------------------------------------------------------------- accuracy

def choose_metric(actual):
    """Refuse MAPE where it is undefined or dominated by small denominators.

    Hyndman and Koehler: percentage errors are undefined at zero and put a
    heavier penalty on over-forecasting than under-forecasting. The standard
    replacement when the series contains near-zero values is WAPE (also called
    MAD-Mean), which divides total absolute error by total actual.
    """
    mean = sum(abs(a) for a in actual) / float(len(actual))
    smallest = min(abs(a) for a in actual)
    if smallest == 0:
        return "WAPE", "MAPE is undefined: the actual series contains a zero"
    if smallest < 0.1 * mean:
        return "WAPE", ("MAPE refused: smallest actual (%s) is under 10%% of the "
                        "mean (%s), so a few small denominators would dominate "
                        "the average" % (_fmt(smallest), _fmt(mean)))
    return "MAPE", "no near-zero denominators; MAPE is safe here"


def errors(actual, predicted):
    n = len(actual)
    ae = [abs(a - p) for a, p in zip(actual, predicted)]
    mae = sum(ae) / float(n)
    rmse = math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / float(n))
    wape = sum(ae) / float(sum(abs(a) for a in actual))
    out = {"n": n, "MAE": mae, "RMSE": rmse, "WAPE": wape}
    if min(abs(a) for a in actual) > 0:
        out["MAPE"] = sum(abs((a - p) / float(a)) for a, p in zip(actual, predicted)) / float(n)
    return out


def naive_baseline(history, horizon):
    """Last observed value, carried forward. The floor any forecast must beat.

    Not seasonal naive: that needs at least two full cycles of history, and
    claiming it on less is the error this product exists to catch.
    """
    return [history[-1]] * horizon


def gate_accuracy(claim):
    """Returns (findings, computed_metrics). Every metric is reported, not just
    the one the claim happens to name, because the gap between them is often
    the finding."""
    a = claim.get("accuracy")
    if not a:
        return [Finding("G6.accuracy", PASS, "not a predictive claim")], {}
    actual, predicted = a.get("actual"), a.get("predicted")
    if not actual or not predicted or len(actual) != len(predicted):
        return [Finding("G6.accuracy", NODATA,
                        "predictive claim with no matched actual/predicted pairs; "
                        "accuracy is asserted, not measured")], {}

    findings = []
    metric, why = choose_metric(actual)
    e = errors(actual, predicted)
    reported = a.get("reported_metric", "")
    shown = ", ".join("%s=%.1f%%" % (k, 100 * e[k])
                      for k in ("WAPE", "MAPE") if k in e)
    if metric == "WAPE" and "MAPE" in reported:
        findings.append(Finding("G6.accuracy", FAIL,
                                "claim reports MAPE. %s Use WAPE = %.1f%%"
                                % (why, 100 * e["WAPE"])))
    else:
        findings.append(Finding("G6.accuracy", PASS,
                                "%s (safe metric here: %s, %s)" % (shown, metric, why)))

    hist = a.get("history")
    if not hist:
        findings.append(Finding("G6.baseline", NODATA,
                                "no history given, so no baseline could be "
                                "computed; an accuracy figure without a baseline "
                                "says nothing about skill"))
    else:
        base = naive_baseline(hist, len(actual))
        be = errors(actual, base)
        ratio = e["MAE"] / be["MAE"] if be["MAE"] else float("inf")
        if ratio < 1:
            findings.append(Finding("G6.baseline", PASS,
                                    "beats naive: MASE-style ratio %.2f "
                                    "(model MAE %s vs naive %s)"
                                    % (ratio, _fmt(e["MAE"]), _fmt(be["MAE"]))))
        else:
            findings.append(Finding("G6.baseline", FAIL,
                                    "does NOT beat carrying the last value "
                                    "forward: ratio %.2f (model MAE %s vs naive "
                                    "%s). The forecast has no demonstrated skill"
                                    % (ratio, _fmt(e["MAE"]), _fmt(be["MAE"]))))

    if hist and len(hist) < 24:
        findings.append(Finding("G6.seasonality", NODATA,
                                "%d periods of history: fewer than two full "
                                "yearly cycles, so seasonality cannot be "
                                "established and no seasonal claim may be made"
                                % len(hist)))
    return findings, e


# --------------------------------------------------------------------- check

# --------------------------------------------------------- master data

# Wording that can only mean entity resolution. Deliberately tight: "match" on
# its own is far too common a word to hang a refusal on, so only phrases that
# cannot mean anything else are listed.
MDM_WORDING = re.compile(
    r"\b(duplicates?|duplicated|dedup\w*|deduplicat\w*|golden record|"
    r"master record|entity resolution|record linkage|survivorship|"
    r"single customer view|"
    r"match(?:ing|ed)?\s+(?:record|entity|entities|customer|product)\w*|"
    r"merg\w+\s+(?:record|entity|entities|customer)\w*)\b", re.I)

MERGE_WORDING = re.compile(
    r"\b(merg\w+|survivorship|golden record|master record)\b", re.I)

# Where the labelled pairs were drawn from. This is the whole difference
# between a recall figure and a guess: a sample drawn from the candidate set a
# blocker produced cannot contain the pairs that blocker never proposed, so
# recall measured on it is an upper bound being reported as a fact.
FRAMES = ("full_cross_product", "stratified_sample", "candidate_set")


def _num01(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0


def gate_master_data(claim):
    """G11 to G14, the refusals master data work earns.

    A dedupe or match claim that says how many records it resolved, and nothing
    about how many it resolved WRONGLY, is the master data overclaim. It is the
    same shape as an accuracy figure with no baseline, and it is more dangerous,
    because the errors here are not symmetric: merging two distinct customers is
    usually irreversible and visible to that customer, while missing a duplicate
    is cheap and caught on the next run. One blended score hides exactly that.
    """
    text = " ".join(str(claim.get(k, "")) for k in
                    ("statement", "question", "decision"))
    m = claim.get("match")
    if not MDM_WORDING.search(text) and not m:
        return []           # not a master data claim; these gates say nothing

    out = []
    if not isinstance(m, dict) or not m:
        out.append(Finding("G11.match_quality", FAIL,
                           "the statement asserts entity resolution but the "
                           "claim carries no match block, so how much was "
                           "resolved wrongly is unstated"))
        return out

    # G11: the count is not the claim. The error rates are.
    missing = [k for k in ("precision", "recall", "threshold")
               if m.get(k) is None]
    if missing:
        out.append(Finding("G11.match_quality", FAIL,
                           "match block missing %s. A resolved-record count "
                           "without both error rates, at a stated operating "
                           "point, is the master data overclaim"
                           % ", ".join(missing)))
    elif not (_num01(m["precision"]) and _num01(m["recall"])):
        out.append(Finding("G11.match_quality", FAIL,
                           "precision and recall must be proportions between "
                           "0 and 1; got %r and %r"
                           % (m.get("precision"), m.get("recall"))))
    else:
        out.append(Finding("G11.match_quality", PASS,
                           "precision %.3f, recall %.3f at threshold %s"
                           % (m["precision"], m["recall"], m["threshold"])))

    # G12: the two errors do not cost the same, so they are never blended.
    blended = [k for k in ("f1", "f_measure", "accuracy") if m.get(k) is not None]
    have_both = m.get("precision") is not None and m.get("recall") is not None
    if blended and not have_both:
        out.append(Finding("G12.error_asymmetry", FAIL,
                           "%s reported without precision and recall. A single "
                           "blended score averages a false merge against a "
                           "missed duplicate, which are not the same event"
                           % ", ".join(blended)))
    else:
        costs = [k for k in ("false_positive_cost", "false_negative_cost")
                 if not _answered(m.get(k))]
        if costs:
            out.append(Finding("G12.error_asymmetry", FAIL,
                               "missing %s. Naming what each error costs is "
                               "what makes an operating point a decision "
                               "rather than a preference" % ", ".join(costs)))
        else:
            out.append(Finding("G12.error_asymmetry", PASS,
                               "both error costs named separately"))

    # G13: recall is only as honest as the frame its sample was drawn from.
    ls = m.get("labelled_sample")
    if not isinstance(ls, dict) or not ls:
        out.append(Finding("G13.sample_frame", FAIL,
                           "no labelled sample, so precision and recall were "
                           "computed against nothing that was checked by hand"))
    else:
        gaps = [k for k in ("n", "frame", "labelled_by", "labelled_on")
                if not _answered(ls.get(k))]
        if gaps:
            out.append(Finding("G13.sample_frame", FAIL,
                               "labelled sample missing %s" % ", ".join(gaps)))
        elif ls.get("frame") not in FRAMES:
            out.append(Finding("G13.sample_frame", FAIL,
                               "frame %r is not one of %s"
                               % (ls.get("frame"), ", ".join(FRAMES))))
        elif ls.get("frame") == "candidate_set":
            out.append(Finding("G13.sample_frame", NODATA,
                               "the sample was drawn from the candidate set, "
                               "which cannot contain the pairs blocking never "
                               "proposed. Precision survives this; RECALL does "
                               "not, and is an upper bound rather than a rate"))
        else:
            out.append(Finding("G13.sample_frame", PASS,
                               "%s labelled pairs drawn from the %s"
                               % (_fmt(ls["n"]), ls["frame"])))

    # G14: a merge is a one-way door unless somebody says otherwise.
    if MERGE_WORDING.search(text) or m.get("kind") == "merge":
        gaps = []
        if not _answered(m.get("survivorship")):
            gaps.append("survivorship")
        if m.get("reversible") is None:
            gaps.append("reversible")
        if gaps:
            out.append(Finding("G14.survivorship", FAIL,
                               "a merge claim must state %s. Which value wins "
                               "decides what is destroyed, and whether it can "
                               "be undone decides how bad a wrong merge is"
                               % " and ".join(gaps)))
        elif m.get("reversible") is False:
            out.append(Finding("G14.survivorship", PASS,
                               "survivorship stated, and the claim says the "
                               "merge is NOT reversible: a wrong merge here "
                               "cannot be undone"))
        else:
            out.append(Finding("G14.survivorship", PASS,
                               "survivorship stated and the merge is reversible"))
    return out


# Pack modules land here one at a time, each fully optional today: a missing
# module is NO-DATA, not an error, and this import block is the only place
# bds.py ever changes again to pick one up. Each present module registers its
# own finding functions against packs.py via register(packs, Finding).
_PACK_MODULE_NAMES = ("pack_experiment", "pack_detection", "pack_mdm",
                      "pack_mdm_science", "pack_mdm_decision",
                      "pack_mdm_calibration", "pack_mdm_locale",
                      "pack_mdm_audit", "pack_mdm_coverage", "pack_mdm_identity",
                      "pack_pipeline")
_PACK_MODULES = {}
_PACK_STATUS = {}
for _pack_name in _PACK_MODULE_NAMES:
    try:
        _pack_mod = __import__(_pack_name)
    except ImportError as _pack_exc:
        _PACK_STATUS[_pack_name] = "NO-DATA: %s" % _pack_exc
    else:
        _pack_mod.register(packs, Finding)
        _PACK_MODULES[_pack_name] = _pack_mod
        _PACK_STATUS[_pack_name] = "OK"


GATES = (gate_origin, gate_not_established, gate_uncertainty,
         gate_causal, gate_origin_protocol, gate_claim_type)


DEFAULT_REGISTRY = "definitions.json"


def check(claim, registry_path=DEFAULT_REGISTRY):
    findings = []
    for g in GATES:
        findings.append(g(claim))
    findings.append(gate_definition(claim, registry_path))
    findings.extend(gate_grain(claim))
    findings.extend(gate_master_data(claim))
    values = []
    if claim.get("origin") == "SYSTEM":
        values, dfind = evaluate_derivations(claim)
        findings.extend(dfind)
        findings.extend(evaluate_comparison(claim, values))
    acc_findings, metrics = gate_accuracy(claim)
    findings.append(gate_value_matches(claim, values, metrics))
    findings.extend(acc_findings)

    claim_type, _declared = effective_claim_type(claim)
    findings.extend(packs.run_packs(claim_type, claim, Finding))

    if any(f.verdict == FAIL for f in findings):
        verdict = FAIL
    elif any(f.verdict == NODATA for f in findings):
        verdict = NODATA
    else:
        verdict = PASS
    return verdict, findings, values


def print_check(claim, verdict, findings):
    """The page `check` prints, shared with `author` so a newly authored claim
    is shown its own G-gate result rather than a second, drifting copy of this
    text."""
    print("claim   %s" % claim.get("id"))
    print("        %s" % claim.get("statement"))
    print("")
    for f in findings:
        print("  " + f.line())
    print("")
    print("VERDICT %s" % verdict)
    if verdict == NODATA:
        print("        NO-DATA is not a pass and not a block. Something the "
              "claim needs was never measured.")


# --------------------------------------------------------------- safe wording

def safe_wording(statement, findings):
    """Rewrite causal language into an honest association, deterministically.

    Only rewrites when the G4.causal gate did not PASS (FAIL, NO-DATA, or
    simply absent from `findings`) AND the statement carries a CAUSAL_TOKENS
    phrase. A PASSing causal gate, or a statement with no causal token,
    returns the statement byte-identical: this is a rewrite of wording that
    was not earned, never a general paraphraser.

    Longest tokens are replaced first so "uplift" is not partially consumed
    by the shorter "lift" rule. Every digit sequence in the statement must
    survive unchanged; this rewrites wording, never numbers, and asserts
    that before returning.
    """
    g4 = next((f for f in findings if f.gate == "G4.causal"), None)
    if g4 is not None and g4.verdict == PASS:
        return statement

    lower = statement.lower()
    if not any(t in lower for t in CAUSAL_TOKENS):
        return statement

    out = statement
    for token in sorted(CAUSAL_TOKENS, key=len, reverse=True):
        out = re.sub(r"\b%s\b" % re.escape(token), CAUSAL_MAP[token],
                     out, flags=re.I)
    out = out + " (observed difference, not a proven effect)"

    digits = lambda s: re.findall(r"\d[\d,]*(?:\.\d+)?", s)
    assert digits(statement) == digits(out), (
        "safe_wording altered a digit sequence: %r -> %r" % (statement, out))
    return out


# ----------------------------------------------------------------- the card

def _card_finding(findings, prefix):
    """The worst finding whose gate is `prefix` or starts with `prefix.`, or
    None if the claim was never evaluated on that gate at all. Grouping by
    prefix is what lets one field (Grain, Re-derivation) speak for a gate
    family (G9.grain + G9.uniqueness, G5.rederivation + G5.comparison) while
    the exact-name gates (G4.causal, G3.uncertainty, G10.definition,
    G7.value) still resolve to themselves."""
    matches = [f for f in findings
              if f.gate == prefix or f.gate.startswith(prefix + ".")]
    if not matches:
        return None
    return min(matches, key=lambda f: _RANK[f.verdict])


def _reality_state(claim):
    """OPEN until an outcome has been scored; then the score's own state."""
    o = claim.get("outcome")
    if not o or o.get("actual") is None:
        return "OPEN"
    return o.get("state", "OPEN")


# ------------------------------------------------------------------- receipt

def receipt(claim, verdict, findings, dest=None):
    L = []
    A = L.append
    A("# Claim receipt: %s" % claim.get("id", "(no id)"))
    A("")
    statement = claim.get("statement", "")
    A("**%s**" % statement)
    A("")

    A("## Compact card")
    A("")
    A("- **Claim:** %s" % statement)

    def card_field(label, prefix):
        f = _card_finding(findings, prefix)
        v = f.verdict if f else NODATA
        d = f.detail if f else "not evaluated"
        A("- **%s:** %s  %s" % (label, v, d))

    card_field("Claim type", "G15.claim_type")
    card_field("Number", "G7.value")
    card_field("Definition", "G10.definition")
    _mdef = (claim.get("metric") or {}).get("definition")
    if isinstance(_mdef, dict):
        A("- **Metric definition:** %s" % _fmt_structured_definition(_mdef))
    card_field("Grain", "G9")
    card_field("Re-derivation", "G5")

    independence = (claim.get("evidence") or {}).get("independence")
    if isinstance(independence, dict) and _answered(independence.get("level")):
        A("- **Independence:** %s  %s"
          % (independence["level"], independence.get("why", "")))
    else:
        A("- **Independence:** NO-DATA")

    if any(t in statement.lower() for t in CAUSAL_TOKENS):
        card_field("Causality", "G4.causal")
    else:
        A("- **Causality:** NOT CLAIMED")

    card_field("Uncertainty", "G3.uncertainty")

    # The page a customer master PM or a founder reads: the four lines that
    # decide whether a merge figure can be trusted, near the top of the card.
    if any(f.gate[:1] == "M" and f.gate[1:2].isdigit() for f in findings):
        A("- **Master data evidence:**")
        for _g in ("M7.precision_evidence", "M8.recall_evidence",
                   "M9.blocking_ceiling", "M22.normalization"):
            _f = next((f for f in findings if f.gate == _g), None)
            A("  - %s %s: %s" % (_g, _f.verdict if _f else NODATA,
                                 _f.detail if _f else "not evaluated"))
    A("- **What this does not establish:**")
    for item in (claim.get("not_established") or ["(nothing declared, which is itself a failure)"]):
        A("  - %s" % item)

    A("- **Safe wording (recommended):** %s" % safe_wording(statement, findings))
    A("- **Reality:** %s" % _reality_state(claim))
    # Recall for what went wrong, not only for the topic: the gates this claim
    # failed are the words a past lesson about the same mistake will carry.
    _failed = [f.gate for f in findings if f.verdict == FAIL]
    vault_ctx = vault_bridge.recall_context(
        statement + ((" failed gates: " + " ".join(_failed)) if _failed else ""))
    if vault_ctx.get("state") == "OK":
        A("- **Vault context:** %d lessons surfaced, 0 treated as evidence"
          % vault_ctx.get("count", 0))
        for _title in vault_ctx.get("titles") or []:
            A("  - %s" % _title)
    else:
        A("- **Vault context:** NO-DATA: %s" % vault_ctx.get("why", "unknown"))
    A("- **Receipt:** %s" % (dest if dest else "(not written to a file)"))
    A("")

    A("| field | value |")
    A("|---|---|")
    A("| Value | %s %s |" % (_fmt(claim.get("value")), claim.get("unit", "")))
    A("| Origin | %s |" % claim.get("origin"))
    A("| Question it answers | %s |" % claim.get("question", "NOT STATED"))
    A("| Decision it feeds | %s |" % claim.get("decision", "NOT STATED"))
    A("| Verdict | **%s** |" % verdict)
    A("")
    ev = claim.get("evidence") or {}
    if ev.get("source"):
        st = source_state(ev["source"])
        A("## Source")
        if st:
            A("`%s`  \nbytes %s, mtime %s" % (st["path"], _fmt(st["bytes"]), st["mtime"]))
        else:
            A("`%s` (MISSING)" % ev["source"])
        A("")
    if ev.get("derivations"):
        A("## Derivations")
        for d in ev["derivations"]:
            A("**%s**" % d.get("name", "?"))
            A("```sql")
            A(d["sql"].strip())
            A("```")
        A("")
    A("## Gates")
    A("```")
    for f in findings:
        A(f.line())
    A("```")
    A("")
    A("## What was NOT established")
    for item in (claim.get("not_established") or ["(nothing declared, which is itself a failure)"]):
        A("- %s" % item)
    A("")
    if claim.get("known_defects_in_source"):
        A("## Known defects in the source")
        for item in claim["known_defects_in_source"]:
            A("- %s" % item)
        A("")
    if claim.get("how_it_was_found"):
        A("## How this was found")
        A(claim["how_it_was_found"])
        A("")
    o = claim.get("outcome")
    A("## Verified against reality")
    if o and o.get("actual") is not None:
        A("Actual: %s. Recorded %s. Error %s."
          % (_fmt(o["actual"]), _fmt(claim.get("value")),
             _fmt(abs(float(o["actual"]) - float(claim.get("value"))))))
        A("")
        A("Verified on %s by %s." % (o.get("observed_on", "?"), o.get("observed_by", "?")))
    else:
        A("NOT YET. This claim has not been scored against what actually happened. "
          "A gate verdict is a statement about proof; only an outcome is a "
          "statement about the world.")
    return "\n".join(L)


# ------------------------------------------------------------------ selftest

def selftest():
    """Every gate must be able to reach a verdict, and must refuse what it
    claims to refuse. A gate that cannot fail is not a control."""
    ok = True

    def expect(cond, msg):
        if not cond:
            print("SELFTEST FAIL: %s" % msg)
        return bool(cond)

    base = {"id": "T", "statement": "Volume was 100 units.", "value": 100,
            "origin": "SYSTEM", "uncertainty": {"kind": "NOT_ESTABLISHED", "why": "census"},
            "not_established": ["nothing checked"]}

    # G1 refuses an empty not_established
    c = dict(base); c["not_established"] = []
    ok &= expect(gate_not_established(c).verdict == FAIL, "G1 must fail on empty")
    ok &= expect(gate_not_established(base).verdict == PASS, "G1 must pass when declared")

    # G1 refuses boilerplate that says nothing a reviewer could act on
    for _phrase in BOILERPLATE_NOT_ESTABLISHED:
        c = dict(base); c["not_established"] = [_phrase]
        ok &= expect(gate_not_established(c).verdict == FAIL,
                     "G1 must fail boilerplate entry %r" % _phrase)

    # G2 refuses an unknown origin
    c = dict(base); c["origin"] = "VIBES"
    ok &= expect(gate_origin(c).verdict == FAIL, "G2 must fail unknown origin")

    # G3 refuses a bare number
    c = dict(base); c.pop("uncertainty")
    ok &= expect(gate_uncertainty(c).verdict == FAIL, "G3 must fail missing uncertainty")
    ok &= expect(gate_uncertainty(base).verdict == NODATA, "G3 NOT_ESTABLISHED is NO-DATA")

    # G3 also accepts 'reason' beside 'why', additive and legacy safe
    c = dict(base)
    c["uncertainty"] = {"kind": "NOT_ESTABLISHED", "reason": "stated via reason, not why"}
    gf3 = gate_uncertainty(c)
    ok &= expect(gf3.verdict == NODATA, "G3 must accept 'reason' as NO-DATA, same as 'why'")
    ok &= expect("stated via reason, not why" in gf3.detail,
                 "G3 detail must carry the 'reason' text when 'why' is absent")
    c2 = dict(base)
    c2["uncertainty"] = {"kind": "NOT_ESTABLISHED"}
    ok &= expect(gate_uncertainty(c2).verdict == FAIL,
                 "G3 must still fail NOT_ESTABLISHED with neither 'why' nor 'reason'")

    # G3 also accepts kind=quantiles: a probability-to-value map that must be
    # monotone non-decreasing and must carry the median.
    cq = dict(base)
    cq["uncertainty"] = {"kind": "quantiles",
                         "quantiles": {"0.1": 80, "0.5": 100, "0.9": 130}}
    ok &= expect(gate_uncertainty(cq).verdict == PASS,
                 "G3 must pass a well formed, monotone quantile set")
    cq_flat = dict(base)
    cq_flat["uncertainty"] = {"kind": "quantiles",
                              "quantiles": {"0.1": 100, "0.5": 100, "0.9": 100}}
    ok &= expect(gate_uncertainty(cq_flat).verdict == PASS,
                 "G3 must pass a flat (non-decreasing, not strictly increasing) quantile set")
    cq_bad = dict(base)
    cq_bad["uncertainty"] = {"kind": "quantiles",
                             "quantiles": {"0.1": 100, "0.5": 90, "0.9": 150}}
    gfq_bad = gate_uncertainty(cq_bad)
    ok &= expect(gfq_bad.verdict == FAIL,
                 "G3 must fail a non-monotone quantile set")
    cq_no_median = dict(base)
    cq_no_median["uncertainty"] = {"kind": "quantiles",
                                   "quantiles": {"0.1": 80, "0.9": 130}}
    gfq_nomed = gate_uncertainty(cq_no_median)
    ok &= expect(gfq_nomed.verdict == FAIL and "0.5" in gfq_nomed.detail,
                 "G3 must fail quantiles missing the median, naming '0.5'")

    # G4 refuses causal wording with no design, permits it under HYPOTHESIS
    c = dict(base); c["statement"] = "The banner drove incremental orders."
    ok &= expect(gate_causal(c).verdict == FAIL, "G4 must fail undesigned causal claim")
    c2 = dict(c); c2["origin"] = "HYPOTHESIS"
    ok &= expect(gate_causal(c2).verdict == NODATA, "G4 hypothesis is NO-DATA not FAIL")
    c3 = dict(c); c3["design"] = {"kind": "difference_in_differences",
                                 "assumption_test": "pre-trend test"}
    ok &= expect(gate_causal(c3).verdict == PASS, "G4 must pass a designed claim")
    c4 = dict(c); c4["design"] = {"kind": "difference_in_differences"}
    ok &= expect(gate_causal(c4).verdict == FAIL, "G4 must fail untested assumption")

    # G6 refuses MAPE on near-zero denominators
    m, _ = choose_metric([100.0, 100.0, 0.5])
    ok &= expect(m == "WAPE", "choose_metric must refuse MAPE near zero")
    m, _ = choose_metric([100.0, 110.0, 90.0])
    ok &= expect(m == "MAPE", "choose_metric must allow MAPE when safe")

    # G6 must catch a forecast that loses to the naive baseline
    c = dict(base)
    c["accuracy"] = {"history": [100.0] * 12, "actual": [100.0, 100.0],
                     "predicted": [130.0, 70.0]}
    fs, metrics = gate_accuracy(c)
    ok &= expect(any(f.gate == "G6.baseline" and f.verdict == FAIL for f in fs),
                 "G6 must fail a forecast worse than naive")

    # G6 must refuse seasonality on short history
    ok &= expect(any(f.gate == "G6.seasonality" and f.verdict == NODATA for f in fs),
                 "G6 must refuse seasonality under 24 periods")

    # G7 must check a stated accuracy figure against the recomputed metric.
    # Regression fixture: SKU-FCST-003 originally claimed MAPE 0.47 when the true
    # value was 0.6322, because only the first month had been computed by hand.
    c7 = {"value": 0.47, "accuracy": {"reported_metric": "MAPE",
                                      "actual": [35460.0, 33078.0, 37779.0],
                                      "predicted": [52209.3, 57785.5, 63361.6]}}
    _, m7 = gate_accuracy(c7)
    ok &= expect(abs(m7["MAPE"] - 0.6322) < 1e-3, "MAPE fixture must be 0.6322")
    ok &= expect(gate_value_matches(c7, [], m7).verdict == FAIL,
                 "G7 must fail a stated metric that does not match the computed one")
    c7ok = dict(c7); c7ok["value"] = m7["MAPE"]
    ok &= expect(gate_value_matches(c7ok, [], m7).verdict == PASS,
                 "G7 must pass when the stated metric matches")

    # G5.comparison: the dual run, a SYSTEM derivation paired with a
    # THIRD_PARTY or ELICITED comparison against the incumbent artifact.
    # value_paths is passed in directly rather than run through evaluate_
    # derivations, so this needs no duckdb and no database file, exactly the
    # promise that non-SYSTEM machinery costs nothing in dependencies.
    def cmp_claim(**overrides):
        c = {"evidence": {"comparison": dict(
            {"class": "THIRD_PARTY", "incumbent": "the ops team's spreadsheet",
             "incumbent_value": 101, "tolerance": 5,
             "protocol": {"provider": "regional distributor", "coverage": "national",
                          "collection_method": "monthly manual export",
                          "known_biases": "excludes cash sales"}},
            **overrides)}}
        return c

    # the old SQL-only claim (no comparison block at all) is unaffected
    ok &= expect(evaluate_comparison({"evidence": {"source": "x"}}, [("q", 100)]) == [],
                 "no comparison block: G5.comparison says nothing, the SQL-only "
                 "path is unchanged")

    # agrees within tolerance: PASS
    f = evaluate_comparison(cmp_claim(), [("q", 100)])
    ok &= expect(len(f) == 1 and f[0].gate == "G5.comparison" and f[0].verdict == PASS,
                 "G5.comparison must pass when system and incumbent agree "
                 "within the stated tolerance")

    # disagrees beyond tolerance: FAIL
    f = evaluate_comparison(cmp_claim(incumbent_value=500), [("q", 100)])
    ok &= expect(len(f) == 1 and f[0].verdict == FAIL,
                 "G5.comparison must fail when system and incumbent disagree "
                 "beyond the stated tolerance")

    # missing tolerance: NO-DATA naming the field
    no_tol = cmp_claim(); no_tol["evidence"]["comparison"].pop("tolerance")
    f = evaluate_comparison(no_tol, [("q", 100)])
    ok &= expect(len(f) == 1 and f[0].verdict == NODATA and "tolerance" in f[0].detail,
                 "G5.comparison must refuse a missing tolerance as NO-DATA, "
                 "naming the field")

    # missing incumbent description: NO-DATA naming the field
    no_inc = cmp_claim(); no_inc["evidence"]["comparison"].pop("incumbent")
    f = evaluate_comparison(no_inc, [("q", 100)])
    ok &= expect(len(f) == 1 and f[0].verdict == NODATA and "incumbent" in f[0].detail,
                 "G5.comparison must refuse a missing incumbent description as "
                 "NO-DATA, naming the field")

    # missing protocol field for the comparison's own class: NO-DATA naming it
    no_proto = cmp_claim(); no_proto["evidence"]["comparison"]["protocol"].pop("provider")
    f = evaluate_comparison(no_proto, [("q", 100)])
    ok &= expect(len(f) == 1 and f[0].verdict == NODATA and "provider" in f[0].detail,
                 "G5.comparison must refuse an incomplete comparison protocol "
                 "as NO-DATA, naming the missing field")

    # invalid class: FAIL, not silently accepted
    bad_cls = cmp_claim(**{"class": "VIBES"})
    f = evaluate_comparison(bad_cls, [("q", 100)])
    ok &= expect(len(f) == 1 and f[0].verdict == FAIL,
                 "G5.comparison must fail an unknown comparison class")

    # no system value to compare against yet: NO-DATA, not a silent skip
    f = evaluate_comparison(cmp_claim(), [])
    ok &= expect(len(f) == 1 and f[0].verdict == NODATA,
                 "G5.comparison must refuse to compare against nothing "
                 "recomputed on the system side")

    # `check` itself must reach a verdict on the pairing, not refuse the non-
    # SQL path. A SYSTEM claim with one SQL derivation and a comparison that
    # agrees must show both G5.rederivation (single path, NO-DATA) and
    # G5.comparison (PASS) rather than the comparison being invisible.
    dual = dict(base)
    dual["grain"] = "one row per month"
    dual["evidence"] = {"derivations": [{"name": "warehouse rollup",
                                         "sql": "select 100"}],
                        "comparison": {"class": "ELICITED",
                                      "incumbent": "the regional lead's estimate",
                                      "incumbent_value": 100, "tolerance": 0,
                                      "protocol": {"expert_role": "regional lead",
                                                  "elicitation_protocol": "structured interview",
                                                  "calibration_question": "known benchmark quantity",
                                                  "seed_score": 0.8}}}
    dual["value"] = None  # SQL derivation is stubbed by the test below, not run
    dual_findings = evaluate_comparison(dual, [("stub", 100)])
    ok &= expect(dual_findings[0].verdict == PASS,
                 "a SYSTEM claim's ELICITED comparison must reach PASS, "
                 "proving the pairing is checkable end to end")

    # G9 must refuse a claim with no declared grain
    c9 = dict(base)
    ok &= expect(gate_grain(c9)[0].verdict == FAIL, "G9 must fail missing grain")
    c9["grain"] = "one row per month"
    gf = gate_grain(c9)
    ok &= expect(gf[0].verdict == PASS, "G9 must pass a declared grain")
    ok &= expect(gf[1].verdict == NODATA, "G9 unverified grain is NO-DATA")

    # errors() arithmetic, checked by hand
    e = errors([100.0, 200.0], [110.0, 180.0])
    ok &= expect(abs(e["MAE"] - 15.0) < 1e-9, "MAE must be 15")
    ok &= expect(abs(e["WAPE"] - 30.0 / 300.0) < 1e-9, "WAPE must be 0.1")

    # G10 must refuse a named metric with no definition, and must catch two
    # different definitions under one name. This is the gate that answers
    # "a definition is not a file"; reproduction gates cannot see it.
    import tempfile
    tmp = tempfile.mkdtemp()
    reg = os.path.join(tmp, "definitions.json")
    ok &= expect(gate_definition({}, reg).verdict == NODATA,
                 "G10 with no metric is NO-DATA")
    ok &= expect(gate_definition({"metric": {"name": "gmv"}}, reg).verdict == FAIL,
                 "G10 must fail a named metric with no definition")
    ca = {"id": "A", "metric": {"name": "gmv", "definition": "gross, before cancellations"}}
    ok &= expect(gate_definition(ca, reg).verdict == NODATA,
                 "G10 first use is NO-DATA, not PASS")
    register_definition(ca, reg)
    ok &= expect(gate_definition(ca, reg).verdict == PASS,
                 "G10 must pass an identical definition")
    ok &= expect(gate_definition({"id": "A", "metric": {"name": "gmv",
                 "definition": "GROSS,  Before Cancellations"}}, reg).verdict == PASS,
                 "G10 must ignore case and whitespace")
    cb = {"id": "B", "metric": {"name": "gmv", "definition": "net, after cancellations"}}
    ok &= expect(gate_definition(cb, reg).verdict == FAIL,
                 "G10 must fail two definitions under one name")
    ok &= expect(register_definition(cb, reg) == 1,
                 "register must refuse to overwrite a conflicting definition")

    # G10 also compares STRUCTURED definitions ({base, modifiers, window},
    # the Chinese 口径 shape, Alibaba OneData) field by field instead of as
    # one text blob, so a single differing modifier names itself.
    struct_a = {"id": "SA", "metric": {"name": "conv_rate",
                "definition": {"base": "orders placed", "window": "trailing 7 days",
                               "modifiers": ["excludes test accounts"]}}}
    ok &= expect(gate_definition(struct_a, reg).verdict == NODATA,
                 "G10 first use of a structured definition is NO-DATA")
    register_definition(struct_a, reg)
    struct_same = {"id": "SB", "metric": {"name": "conv_rate",
                   "definition": {"base": "orders placed", "window": "trailing 7 days",
                                  "modifiers": ["excludes test accounts"]}}}
    ok &= expect(gate_definition(struct_same, reg).verdict == PASS,
                 "G10 must pass an identical structured definition")
    struct_reordered = {"id": "SC", "metric": {"name": "conv_rate",
                        "definition": {"base": "Orders Placed", "window": "Trailing 7 Days",
                                       "modifiers": ["Excludes Test Accounts"]}}}
    ok &= expect(gate_definition(struct_reordered, reg).verdict == PASS,
                 "G10 must ignore case and whitespace in a structured definition")
    struct_diff_mod = {"id": "SD", "metric": {"name": "conv_rate",
                       "definition": {"base": "orders placed", "window": "trailing 7 days",
                                      "modifiers": ["excludes test accounts",
                                                   "excludes refunded orders"]}}}
    gf_mod = gate_definition(struct_diff_mod, reg)
    ok &= expect(gf_mod.verdict == FAIL and "modifiers" in gf_mod.detail,
                 "G10 must fail a modifier difference and name 'modifiers'")
    struct_diff_base = {"id": "SE", "metric": {"name": "conv_rate",
                        "definition": {"base": "orders shipped", "window": "trailing 7 days",
                                       "modifiers": ["excludes test accounts"]}}}
    gf_base = gate_definition(struct_diff_base, reg)
    ok &= expect(gf_base.verdict == FAIL and "base" in gf_base.detail,
                 "G10 must fail a base difference and name 'base'")

    mixed = {"id": "SF", "metric": {"name": "conv_rate",
             "definition": "orders placed, trailing 7 days"}}
    gf_mixed = gate_definition(mixed, reg)
    ok &= expect(gf_mixed.verdict == NODATA and
                 "one side is structured and the other is prose" in gf_mixed.detail,
                 "G10 must refuse to compare a structured definition against "
                 "a prose one, naming the shape mismatch")
    ok &= expect(register_definition(mixed, reg) == 1,
                 "register must refuse a prose definition over a registered "
                 "structured one for the same metric")

    # score: a claim with no stated interval can never be HELD, only UNSCOREABLE.
    # This is what keeps the north star from rewarding people for saying nothing.
    sp = os.path.join(tmp, "s.json")
    cs = {"id": "S", "value": 100, "uncertainty": {"kind": "interval", "interval": [90, 110]},
          "not_established": ["x"]}
    score(dict(cs), sp, 95, "t", "d")
    ok &= expect(json.load(open(sp))["outcome"]["state"] == "HELD", "score inside is HELD")

    import io as _io
    import contextlib as _ctx
    _had_vault = os.environ.pop("BROTHERDS_VAULT", None)
    miss_buf = _io.StringIO()
    with _ctx.redirect_stdout(miss_buf):
        score(dict(cs), sp, 200, "t", "d")
    if _had_vault is not None:
        os.environ["BROTHERDS_VAULT"] = _had_vault
    ok &= expect(json.load(open(sp))["outcome"]["state"] == "MISSED", "score outside is MISSED")
    ok &= expect("lesson candidate: NO-DATA: vault not configured" in miss_buf.getvalue(),
                 "a MISSED score with BROTHERDS_VAULT unset must print the "
                 "NO-DATA lesson candidate line")
    written = json.load(open(sp))
    ok &= expect(written["review"]["cause"] == "NO-DATA: cause not recorded" and
                 written["review"]["lesson"] == "NO-DATA: lesson not recorded",
                 "score with no cause/lesson given must record both as NO-DATA")
    ok &= expect(written["review"]["result"] == {"actual": 200, "state": "MISSED"},
                 "the review's result must carry the actual value and the state")
    ok &= expect(written["review"]["goal"]["interval"] == [90, 110],
                 "the review's goal must carry the stated interval")

    # BROTHERDS_VAULT set to a vault the intake tool accepts: the capture
    # actually runs, through the one intake door, and the printed id is
    # non-empty.
    import tempfile as _tempfile
    _vault_dir = _tempfile.mkdtemp()
    os.environ["BROTHERDS_VAULT"] = _vault_dir
    set_buf = _io.StringIO()
    with _ctx.redirect_stdout(set_buf):
        score(dict(cs), sp, 300, "t", "d")
    if _had_vault is not None:
        os.environ["BROTHERDS_VAULT"] = _had_vault
    else:
        os.environ.pop("BROTHERDS_VAULT", None)
    set_out = set_buf.getvalue()
    ok &= expect("lesson candidate: OK id=" in set_out,
                 "a MISSED score with BROTHERDS_VAULT set to an accepted "
                 "vault must capture and print a non-empty id: %r" % set_out)
    _id_line = next((ln for ln in set_out.splitlines()
                     if ln.startswith("lesson candidate: OK id=")), "")
    ok &= expect(_id_line.strip() != "lesson candidate: OK id=",
                 "the captured lesson id must be non-empty")

    # Neither this file nor vault_bridge.py may open a path under the vault
    # directly: every write goes through the one intake door, a subprocess
    # call, never a local open(). Needle assembled from pieces, same as the
    # far-side-of-the-seam scan above, so this scan can never match itself.
    _inbox_needle = "00-" + "Inbox"
    _bds_own_src = open(os.path.abspath(__file__)).read()
    _vb_src = open(os.path.abspath(vault_bridge.__file__.rstrip("c"))).read()
    ok &= expect(_inbox_needle not in _bds_own_src,
                 "bds.py may not open a path under the vault directly")
    ok &= expect(_inbox_needle not in _vb_src,
                 "vault_bridge.py may not open a path under the vault directly")

    cu = {"id": "U", "value": 100,
          "uncertainty": {"kind": "NOT_ESTABLISHED", "why": "census"}}
    score(dict(cu), sp, 95, "t", "d")
    ok &= expect(json.load(open(sp))["outcome"]["state"] == "UNSCOREABLE",
                 "a claim stating no interval is UNSCOREABLE, never HELD")

    # score() also handles uncertainty.kind == "quantiles": HELD inside the
    # 0.1-0.9 band, MISSED outside, and the review records the band and the
    # relative width ((0.9 value - 0.1 value) / 0.5 value; NO-DATA when the
    # median is zero), so a later WIS score has something to read.
    csq = {"id": "SQ", "value": 100,
          "uncertainty": {"kind": "quantiles",
                          "quantiles": {"0.1": 80, "0.5": 100, "0.9": 130}},
          "not_established": ["x"]}
    spq = os.path.join(tmp, "sq.json")
    score(dict(csq), spq, 110, "t", "d")
    wq = json.load(open(spq))
    ok &= expect(wq["outcome"]["state"] == "HELD",
                 "a quantile score inside the 0.1-0.9 band is HELD")
    ok &= expect(wq["review"]["result"]["band"] == [80.0, 130.0],
                 "the review result must carry the 0.1/0.9 band")
    ok &= expect(abs(wq["review"]["result"]["relative_width"] - 0.5) < 1e-9,
                 "relative width must be (0.9 value - 0.1 value) / 0.5 value")

    spq2 = os.path.join(tmp, "sq2.json")
    score(dict(csq), spq2, 200, "t", "d")
    wq2 = json.load(open(spq2))
    ok &= expect(wq2["outcome"]["state"] == "MISSED",
                 "a quantile score outside the 0.1-0.9 band is MISSED")

    czero = {"id": "SZ", "value": 0,
             "uncertainty": {"kind": "quantiles",
                             "quantiles": {"0.1": -10, "0.5": 0, "0.9": 10}},
             "not_established": ["x"]}
    spz = os.path.join(tmp, "sz.json")
    score(dict(czero), spz, 5, "t", "d")
    wz = json.load(open(spz))
    ok &= expect(wz["review"]["result"]["relative_width"] ==
                 "NO-DATA: the 0.5 quantile is zero",
                 "relative width must be NO-DATA, not a crash, when the "
                 "0.5 quantile is zero")

    # the ledger prints the median relative interval width across resolved
    # quantile claims, and only when at least one exists.
    lqdir = os.path.join(tmp, "ledger-quantile")
    os.makedirs(lqdir)
    with open(os.path.join(lqdir, "q1.json"), "w") as fh:
        json.dump(wq, fh)
    with open(os.path.join(lqdir, "q2.json"), "w") as fh:
        json.dump(wq2, fh)
    buf3 = _io.StringIO()
    with _ctx.redirect_stdout(buf3):
        ledger(lqdir)
    out3 = buf3.getvalue()
    ok &= expect("median relative interval width: 0.5" in out3,
                 "ledger must print the median relative interval width "
                 "across resolved quantile claims")

    ldir3 = os.path.join(tmp, "ledger-no-quantile")
    os.makedirs(ldir3)
    with open(os.path.join(ldir3, "held.json"), "w") as fh:
        json.dump({"id": "H2", "statement": "s", "outcome": {"state": "HELD"}}, fh)
    buf4 = _io.StringIO()
    with _ctx.redirect_stdout(buf4):
        ledger(ldir3)
    out4 = buf4.getvalue()
    ok &= expect("median relative interval width" not in out4,
                 "ledger must print nothing about relative width when no "
                 "quantile claim has resolved")

    # a whole-numbered float must render as a quantity, not in scientific notation
    ok &= expect(_fmt(1050000.0) == "1,050,000", "_fmt must not print 1.05e+06")

    # The ledger must never drop a claim silently. A rate computed over an
    # unstated subset is the failure this whole product refuses.
    ldir = os.path.join(tmp, "ledger")
    os.makedirs(ldir)
    with open(os.path.join(ldir, "good.json"), "w") as fh:
        json.dump({"id": "G", "statement": "fine"}, fh)
    with open(os.path.join(ldir, "broken.json"), "w") as fh:
        fh.write("{not json at all")
    import io as _io
    import contextlib as _ctx
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        ledger(ldir)
    out = buf.getvalue()
    ok &= expect("UNREADABLE" in out and "broken.json" in out,
                 "ledger must name a claim file it could not read")
    ok &= expect("1 of 2 claim files" in out,
                 "ledger must state how many claims the rate actually covers")
    ok &= expect("VERIFIED CLAIM RATE  NO-DATA" in out,
                 "ledger with zero resolved claims must never print a percent")
    ok &= expect("reason: no scoreable claim has resolved yet" in out,
                 "ledger must name why the rate is NO-DATA, not just say NO-DATA")

    # Fewer than 10 resolved claims: the rate prints, but with a caveat that
    # a sample this small proves nothing about performance.
    ldir2 = os.path.join(tmp, "ledger-small")
    os.makedirs(ldir2)
    with open(os.path.join(ldir2, "held.json"), "w") as fh:
        json.dump({"id": "H1", "statement": "s", "outcome": {"state": "HELD"}}, fh)
    buf2 = _io.StringIO()
    with _ctx.redirect_stdout(buf2):
        ledger(ldir2)
    out2 = buf2.getvalue()
    ok &= expect("VERIFIED CLAIM RATE  100%  (1 held of 1 resolved)" in out2,
                 "ledger must compute the rate correctly over one resolved claim")
    ok &= expect("sample too small for any performance conclusion (n=1)" in out2,
                 "ledger must caveat a rate computed over fewer than 10 resolved claims")

    # `author`: a claim reaches disk only as a side effect of the CLI, never
    # by hand-editing JSON. Each assertion below is a refusal it must make.
    adir = os.path.join(tmp, "authored")
    os.makedirs(adir)
    full_input = (
        "id=RGM-014\n"
        "statement=Weekly orders for wholesaler segment A were 8123.\n"
        "value=8123\n"
        "unit=orders\n"
        "origin=ASSUMPTION\n"
        "question=What is this week's order volume for the incentive model?\n"
        "decision=Sets the base the incentive payout is a percentage of.\n"
        "grain=one row per calendar week, whole segment\n"
        "uncertainty.kind=NOT_ESTABLISHED\n"
        "uncertainty.why=stated by the ops lead, not yet reconciled to a system\n"
        "not_established=No reconciliation was run against the order system.\n"
        "not_established=Returns after the week close were not checked.\n"
    )
    ap = os.path.join(adir, "rgm-014.json")
    buf = _io.StringIO()
    with _ctx.redirect_stdout(buf):
        rc = author(ap, full_input)
    ok &= expect(rc == 0, "author must accept a complete claim: %s" % buf.getvalue())
    ok &= expect(os.path.exists(ap), "author must write the claim file")
    written = json.load(open(ap))
    ok &= expect(written["value"] == 8123 and isinstance(written["value"], int),
                 "author must coerce a whole-numbered value to an int")
    ok &= expect(written["not_established"] ==
                 ["No reconciliation was run against the order system.",
                  "Returns after the week close were not checked."],
                 "repeated not_established= lines must become an ordered list")
    ok &= expect(written["uncertainty"] ==
                 {"kind": "NOT_ESTABLISHED",
                  "why": "stated by the ops lead, not yet reconciled to a system"},
                 "dotted keys must nest exactly what was written, nothing hand-added")
    v, findings, _ = check(written, os.path.join(adir, DEFAULT_REGISTRY))
    ok &= expect(v != FAIL, "a claim `author` accepts must not FAIL `check`: %s"
                 % "; ".join(f.line() for f in findings))

    buf2 = _io.StringIO()
    with _ctx.redirect_stdout(buf2):
        rc2 = author(os.path.join(adir, "missing-unit.json"),
                     full_input.replace("unit=orders\n", ""))
    ok &= expect(rc2 == 2, "author must refuse a claim missing a required field")
    ok &= expect("unit" in buf2.getvalue(),
                 "author must name the missing field, not just refuse silently")
    ok &= expect(not os.path.exists(os.path.join(adir, "missing-unit.json")),
                 "author must write nothing when validation fails")

    # G11 to G14, master data. Each assertion is a refusal the gate must make;
    # a gate that cannot fail is not a control.
    def mdm(**kw):
        c = dict(base); c["statement"] = "We removed 12,000 duplicate customers."
        if kw: c["match"] = kw
        return c

    def verd(c, gate):
        for f in gate_master_data(c):
            if f.gate.startswith(gate): return f.verdict
        return None

    ok &= expect(gate_master_data(base) == [],
                 "the master data gates say nothing about a non-master-data claim")
    ok &= expect(verd(mdm(), "G11") == FAIL,
                 "G11 must refuse entity-resolution wording with no match block")
    full = dict(precision=0.94, recall=0.87, threshold=0.85,
                false_positive_cost="two distinct customers merged",
                false_negative_cost="a duplicate survives to the next run",
                labelled_sample=dict(n=500, frame="full_cross_product",
                                     labelled_by="data steward", labelled_on="2026-08-20"))
    ok &= expect(verd(mdm(**full), "G11") == PASS, "G11 passes a complete match block")
    c = dict(full); c.pop("recall")
    ok &= expect(verd(mdm(**c), "G11") == FAIL, "G11 must refuse a count with no recall")
    c = dict(full); c["precision"] = 94
    ok &= expect(verd(mdm(**c), "G11") == FAIL,
                 "G11 must refuse a precision that is not a proportion")
    c = dict(full); c.pop("precision"); c.pop("recall"); c["f1"] = 0.90
    ok &= expect(verd(mdm(**c), "G12") == FAIL,
                 "G12 must refuse a blended F1 standing in for both error rates")
    c = dict(full); c.pop("false_positive_cost")
    ok &= expect(verd(mdm(**c), "G12") == FAIL,
                 "G12 must refuse an operating point with no cost named for each error")
    c = dict(full); c.pop("labelled_sample")
    ok &= expect(verd(mdm(**c), "G13") == FAIL,
                 "G13 must refuse error rates computed against nothing hand-checked")
    c = dict(full); c["labelled_sample"] = dict(full["labelled_sample"], frame="candidate_set")
    ok &= expect(verd(mdm(**c), "G13") == NODATA,
                 "G13 reads a candidate-set frame as NO-DATA: blocking's misses are invisible to it")
    c = dict(full); c["labelled_sample"] = dict(full["labelled_sample"], frame="vibes")
    ok &= expect(verd(mdm(**c), "G13") == FAIL, "G13 must refuse an unknown sample frame")
    merge = dict(base); merge["statement"] = "Merging these customer records is safe."
    merge["match"] = dict(full)
    ok &= expect(verd(merge, "G14") == FAIL,
                 "G14 must refuse a merge that states neither survivorship nor reversibility")
    merge["match"] = dict(full, survivorship="most recent non-null per field", reversible=False)
    ok &= expect(verd(merge, "G14") == PASS,
                 "G14 passes an irreversible merge that SAYS it is irreversible")

    # The chain is a control only if it refuses something. A stage the chain
    # does not hold is a hard error; the stage a person owns is refused by name.
    ok &= expect(stage_check("outcome").verdict == PASS, "a real stage passes")
    ok &= expect(stage_check("shipping").verdict == FAIL, "an unknown stage fails")
    ok &= expect(stage_check("decision-taken").verdict == FAIL,
                 "the stage a person takes may not be served by an item")
    ok &= expect(stage_check("").verdict == NODATA, "an unnamed stage is NO-DATA")
    ok &= expect(all(occ for name, occ in CHAIN if name not in UNSERVABLE),
                 "every servable stage must stand in a shared stage")
    ok &= expect(dict(CHAIN)["verified-reality"] == ("verified-reality",),
                 "the chain must end in the shared stage nobody else owns")

    # The document and the code may not disagree about the chain. This mirrors
    # BrotherMode's own schema-versus-doc anti-drift test rather than trusting
    # two copies of one contract to stay equal by attention.
    _doc = os.path.join(os.path.dirname(os.path.abspath(__file__)), CHAIN_DOC)
    try:
        ok &= expect(chain_from_doc(_doc) == CHAIN,
                     "%s and CHAIN must not drift" % CHAIN_DOC)
    except (IOError, OSError, ValueError) as exc:
        ok &= expect(False, "chain document unreadable: %s" % exc)

    # The passport consumer. Absence is NO-DATA, padding is a FAIL, and 0 and
    # False are real answers rather than emptiness.
    pp = os.path.join(tmp, "passport.json")
    ok &= expect(read_passport(os.path.join(tmp, "nothing.json"))[0] == NODATA,
                 "an undeposited passport is NO-DATA")
    full = dict((f, "stated") for f in PASSPORT_FIELDS)
    with open(pp, "w") as fh:
        json.dump(full, fh)
    ok &= expect(read_passport(pp)[0] == PASS, "a complete passport passes")
    padded = dict(full); padded["whatWasRun"] = ""
    with open(pp, "w") as fh:
        json.dump(padded, fh)
    ok &= expect(read_passport(pp)[0] == FAIL,
                 "a field padded to look filled is a FAIL, not an absence")
    partial = dict(full); partial.pop("whatWasDone")
    with open(pp, "w") as fh:
        json.dump(partial, fh)
    ok &= expect(read_passport(pp)[0] == NODATA,
                 "an honestly omitted field is NO-DATA, not a FAIL")
    ok &= expect(_answered(0) and _answered(False),
                 "0 and False are answers; only absence is absence")
    ok &= expect(not _answered("   ") and not _answered([]),
                 "whitespace and an empty list read as absence")

    # MERGE-P5: the canonical passport fixture, byte-identical across all
    # three repositories, pins to one recorded sha256. A byte drifting in the
    # copy must redden this suite, not pass silently.
    _fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "examples", "change-passport.v1.canonical.json")
    _fixture_sha = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "examples", "change-passport.v1.canonical.sha256")
    with open(_fixture, "rb") as fh:
        _fixture_bytes = fh.read()
    _digest = hashlib.sha256(_fixture_bytes).hexdigest()
    print("passport canonical fixture sha256 %s" % _digest[:8])
    with open(_fixture_sha) as fh:
        _recorded = fh.read().strip()
    ok &= expect(_digest == _recorded,
                 "examples/change-passport.v1.canonical.json drifted from the "
                 "recorded digest in examples/change-passport.v1.canonical.sha256")
    ok &= expect(read_passport(_fixture)[0] == PASS,
                 "bds.py passport must parse the canonical fixture")

    # The handoff package. It may never certify a shape nobody ratified.
    hp = os.path.join(tmp, "handoff.json")
    ok &= expect(read_handoff(os.path.join(tmp, "none.json"))[0] == NODATA,
                 "an undelivered handoff package is NO-DATA")
    pkg = {"dataset": {"grain": "one row per order line", "contract": "c",
                       "snapshot_id": "s"},
           "evaluation_harness": {"split": "time-based"},
           "metric_definitions": [{"name": "gmv", "formula": "sum(x)"}],
           "labelled_holdout": {"labelled_by": "ops lead", "labelled_on": "2026-08-01"},
           "open_questions": []}
    with open(hp, "w") as fh:
        json.dump(pkg, fh)
    ok &= expect(read_handoff(hp)[0] == NODATA,
                 "a complete package with no ratified shape is still NO-DATA")
    pkg["ratified"] = True
    with open(hp, "w") as fh:
        json.dump(pkg, fh)
    ok &= expect(read_handoff(hp)[0] == PASS,
                 "a complete package passes once its shape is ratified")
    short = json.loads(json.dumps(pkg)); short["dataset"].pop("snapshot_id")
    with open(hp, "w") as fh:
        json.dump(short, fh)
    ok &= expect(read_handoff(hp)[0] == NODATA,
                 "an item carried without its required parts is NO-DATA")

    # The backlog is what turns the chain from a document into a control. Until
    # something reads a queue and refuses an item serving no stage, the stage
    # vocabulary is a discipline. Each assertion below is a refusal it must make.
    bq = os.path.join(tmp, "queue.json")
    ok &= expect(read_backlog(os.path.join(tmp, "noqueue.json"))[0] == NODATA,
                 "an absent backlog is NO-DATA, not a pass")
    good = [{"id": "A", "title": "t", "state": "queued", "stage": "outcome",
             "check": "bds.py ledger claims/"}]
    with open(bq, "w") as fh:
        json.dump(good, fh)
    ok &= expect(read_backlog(bq)[0] == PASS, "a well formed backlog passes")
    for bad, why in (
            ({"id": "B", "state": "queued", "stage": "shipping", "check": "c"},
             "an item serving a stage the chain lacks is refused"),
            ({"id": "C", "state": "queued", "stage": "decision-taken", "check": "c"},
             "an item claiming the stage a person takes is refused"),
            ({"id": "D", "state": "queued", "stage": "outcome"},
             "an item naming no check is refused"),
            ({"id": "E", "state": "done-ish", "stage": "outcome", "check": "c"},
             "an item in a state this queue does not have is refused")):
        with open(bq, "w") as fh:
            json.dump([bad], fh)
        ok &= expect(read_backlog(bq)[0] == FAIL, why)
    with open(bq, "w") as fh:
        json.dump([], fh)
    ok &= expect(read_backlog(bq)[0] == NODATA, "an empty backlog is NO-DATA")

    # The project's own queue must satisfy the rule the engine enforces on any
    # other. A control its author's own file cannot pass is a control nobody
    # will keep.
    _own_queue = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              DEFAULT_QUEUE)
    ok &= expect(read_backlog(_own_queue)[0] == PASS,
                 "this project's own %s must pass the backlog check" % DEFAULT_QUEUE)

    # The isolation law as a control rather than a discipline: this file may not
    # name any path on the far side of the seam. Reaching across to fill a
    # missing field is the failure the seam exists to prevent, and a rule with
    # no file behind it is not a control.
    #
    # The needles are assembled from pieces rather than written whole, because a
    # scan whose own pattern appears in the text it scans reports a hit it
    # created. That exact mistake cost this project a false credential alarm on
    # its first night.
    _src = open(os.path.abspath(__file__)).read()
    for _needle in ("." + "brothermode", ".sbe/" + "tasks", ".sbe/" + "evidence",
                    "store." + "sqlite3"):
        ok &= expect(_needle not in _src,
                     "this file may not name %r: it is on the far side of the "
                     "seam" % _needle)

    # The integration document names the fields this code reads. If the code
    # grows a field and the document does not, the document is stale and says
    # something untrue about the seam.
    _seam_doc = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "docs/TRIUMVIRATE-INTEGRATION.md")
    try:
        _seam = open(_seam_doc).read()
        for _name in PASSPORT_FIELDS + tuple(i for i, _ in HANDOFF_ITEMS):
            ok &= expect(_name in _seam,
                         "the integration document must name %s" % _name)
    except (IOError, OSError) as exc:
        ok &= expect(False, "integration document unreadable: %s" % exc)

    # safe_wording and the compact card. A causal claim with no design must
    # FAIL G4.causal, and G7.value is NO-DATA because a non-SYSTEM claim with
    # no derivation recomputes nothing to compare the recorded value against.
    causal_claim = {
        "id": "CAUSAL-T", "origin": "ASSUMPTION",
        "statement": "The spring campaign drove an 8.4 percent increase in weekly sales.",
        "value": 8.4, "unit": "percent",
        "grain": "one row per week, whole retail network",
        "uncertainty": {"kind": "NOT_ESTABLISHED",
                        "why": "no control period was held out"},
        "not_established": ["Seasonality was not separated from the campaign.",
                            "No control period exists to compare against."],
        "protocol": {"stated_by": "marketing lead",
                    "sensitivity_range": "5 to 12 percent",
                    "sensitivity_result": "still positive across the range"},
    }
    unused_reg = os.path.join(tmp, "unused-registry.json")
    cv, cf, _ = check(causal_claim, unused_reg)
    ok &= expect(cv == FAIL, "a causal overclaim with no design must FAIL check()")
    g4f = next(f for f in cf if f.gate == "G4.causal")
    ok &= expect(g4f.verdict == FAIL, "G4.causal must be FAIL on the causal overclaim")
    g7f = next(f for f in cf if f.gate == "G7.value")
    ok &= expect(g7f.verdict == NODATA,
                 "G7.value is NO-DATA when nothing was recomputed to compare against")

    sw = safe_wording(causal_claim["statement"], cf)
    ok &= expect(sw != causal_claim["statement"],
                 "safe_wording must rewrite an undesigned causal statement")
    ok &= expect("drove" not in sw.lower(), "safe_wording must strip every causal token")
    ok &= expect(sw.endswith("(observed difference, not a proven effect)"),
                 "safe_wording must append the hedge")
    before_digits = re.findall(r"\d[\d,]*(?:\.\d+)?", causal_claim["statement"])
    after_digits = re.findall(r"\d[\d,]*(?:\.\d+)?", sw)
    ok &= expect(before_digits == after_digits,
                 "safe_wording must preserve every digit sequence")

    designed = dict(causal_claim)
    designed["design"] = {"kind": "difference_in_differences",
                          "assumption_test": "pre-trend test"}
    _, df, _ = check(designed, unused_reg)
    ok &= expect(safe_wording(designed["statement"], df) == designed["statement"],
                 "safe_wording must return a designed, PASSing causal claim unchanged")

    card = receipt(causal_claim, FAIL, cf)
    ok &= expect("- **Reality:** OPEN" in card,
                 "a claim with no outcome must show Reality: OPEN on the card")
    ok &= expect("- **Independence:** NO-DATA" in card,
                 "a claim with no evidence.independence must show NO-DATA on the card")
    ok &= expect("- **Causality:** FAIL" in card,
                 "a causal statement's card must show the real G4 verdict, not NOT CLAIMED")

    _, bf, _ = check(dict(base), unused_reg)
    no_causal_card = receipt(dict(base), NODATA, bf)
    ok &= expect("- **Causality:** NOT CLAIMED" in no_causal_card,
                 "a claim with no causal token must render Causality: NOT CLAIMED")

    # Independence is printed when a claim states one (NO-DATA-when-absent is
    # already covered above via causal_claim, which states none).
    indep_claim = dict(base)
    indep_claim["evidence"] = {"independence": {"level": "INDEPENDENT_SOURCE",
                                                 "why": "a different team's own count"}}
    v_indep, indep_findings, _ = check(indep_claim, unused_reg)
    indep_card = receipt(indep_claim, v_indep, indep_findings)
    ok &= expect("- **Independence:** INDEPENDENT_SOURCE  a different team's own count"
                 in indep_card,
                 "a claim with evidence.independence must print its level and why")

    # Vault recall is advisory, computed only inside receipt() after check()
    # has already reached its verdict. A gate must never be able to see it.
    _orig_recall = vault_bridge.recall_context
    vault_bridge.recall_context = lambda statement: {"state": "NO-DATA",
                                                      "why": "forced for test"}
    v_nodata, f_nodata, _ = check(dict(base), unused_reg)
    vault_bridge.recall_context = lambda statement: {"state": "OK", "count": 2,
                                                      "titles": ["lesson one", "lesson two"]}
    v_ok, f_ok, _ = check(dict(base), unused_reg)
    ok &= expect(v_nodata == v_ok and
                 [(f.gate, f.verdict, f.detail) for f in f_nodata] ==
                 [(f.gate, f.verdict, f.detail) for f in f_ok],
                 "check() must reach an identical verdict and identical findings "
                 "whether the vault bridge is forced to NO-DATA or to a fake OK "
                 "result: no gate may ever read vault recall")

    vault_bridge.recall_context = lambda statement: {"state": "NO-DATA",
                                                      "why": "forced for test"}
    card_nodata = receipt(dict(base), v_nodata, f_nodata)
    ok &= expect("- **Vault context:** NO-DATA: forced for test" in card_nodata,
                 "the card must show the vault bridge's own NO-DATA reason")
    vault_bridge.recall_context = lambda statement: {"state": "OK", "count": 2,
                                                      "titles": ["lesson one", "lesson two"]}
    card_ok = receipt(dict(base), v_ok, f_ok)
    ok &= expect("- **Vault context:** 2 lessons surfaced, 0 treated as evidence" in card_ok,
                 "the card must show the surfaced-but-not-evidence count")
    ok &= expect("  - lesson one" in card_ok and "  - lesson two" in card_ok,
                 "the card must list each surfaced title as a sub-bullet")
    vault_bridge.recall_context = _orig_recall

    # G15.claim_type: declared, the three inferable types, silence, and an
    # invalid value. Never DESCRIPTIVE/EXPERIMENT/DETECTION/PIPELINE from
    # silence, only from a person saying so.
    c15 = dict(base); c15["claim_type"] = "CAUSAL"
    gf15 = gate_claim_type(c15)
    ok &= expect(gf15.verdict == PASS and "CAUSAL" in gf15.detail,
                 "G15 must pass a declared claim_type and name it")

    causal_text = dict(base)
    causal_text["statement"] = "The banner drove incremental orders."
    ok &= expect(infer_claim_type(causal_text) == "CAUSAL",
                 "claim_type must infer CAUSAL from causal wording in the statement")
    forecast_claim = dict(base)
    forecast_claim["accuracy"] = {"actual": [1.0, 2.0], "predicted": [1.1, 1.9]}
    ok &= expect(infer_claim_type(forecast_claim) == "FORECAST",
                 "claim_type must infer FORECAST from an accuracy object")
    mdm_claim = dict(base)
    mdm_claim["match"] = {"precision": 0.9, "recall": 0.8, "threshold": 0.5}
    ok &= expect(infer_claim_type(mdm_claim) == "MASTER_DATA",
                 "claim_type must infer MASTER_DATA from precision/recall/threshold")
    ok &= expect(infer_claim_type(base) is None,
                 "claim_type must infer nothing from a plain descriptive claim")

    gf15_silent = gate_claim_type(base)
    ok &= expect(gf15_silent.verdict == NODATA and gf15_silent.detail == "(not declared)",
                 "an undeclared, uninferrable claim_type must be NO-DATA (not declared)")
    _, silent_findings, _ = check(dict(base), unused_reg)
    silent_card = receipt(dict(base), NODATA, silent_findings)
    ok &= expect("- **Claim type:** NO-DATA  (not declared)" in silent_card,
                 "the card must print the exact contract line for a silent claim_type")

    for _never in ("DESCRIPTIVE", "EXPERIMENT", "DETECTION", "PIPELINE"):
        ok &= expect(infer_claim_type(base) != _never,
                     "claim_type must never infer %s from silence" % _never)

    c15_bad = dict(base); c15_bad["claim_type"] = "VIBES"
    ok &= expect(gate_claim_type(c15_bad).verdict == FAIL,
                 "G15 must fail a claim_type outside the seven")

    # The pack seam itself (acceptance test A20): a dummy pack registered for
    # EXPERIMENT runs on an EXPERIMENT claim and never on a DESCRIPTIVE one.
    def _dummy_pack(claim, Finding):
        return [Finding("PACK.dummy_experiment", PASS, "dummy pack ran")]
    packs.register("EXPERIMENT", _dummy_pack)
    try:
        exp_claim = dict(base); exp_claim["claim_type"] = "EXPERIMENT"
        _, exp_findings, _ = check(exp_claim, unused_reg)
        ok &= expect(any(f.gate == "PACK.dummy_experiment" for f in exp_findings),
                     "a pack registered for EXPERIMENT must run on an EXPERIMENT claim")
        desc_claim = dict(base); desc_claim["claim_type"] = "DESCRIPTIVE"
        _, desc_findings, _ = check(desc_claim, unused_reg)
        ok &= expect(not any(f.gate == "PACK.dummy_experiment" for f in desc_findings),
                     "a pack registered for EXPERIMENT must never run on a "
                     "DESCRIPTIVE claim (A20)")
    finally:
        packs.PACKS["EXPERIMENT"].remove(_dummy_pack)

    # The two statistics libraries fold their own selftests in too.
    import mdm_eval
    for _lib, _errs in (("mdm_eval", mdm_eval._selftest()),
                        ("forecast_score", forecast_score._run_selftest())):
        ok &= expect(not _errs, "%s selftest must pass: %s" % (_lib, _errs[:3]))

    # Present pack modules fold their own selftest into this one.
    for _pack_name in sorted(_PACK_MODULES):
        ok &= expect(_PACK_MODULES[_pack_name].selftest(expect),
                     "%s.selftest() must pass" % _pack_name)

    print("SELFTEST PASS" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


# ------------------------------------------------------------------- the chain

# BrotherDS's own stage vocabulary, and the shared north-star stage each one
# occupies. Four stage and state vocabularies already coexist across the other
# two products, and reusing the wrong one by string-matching corrupts data
# silently, so this list is BrotherDS's own and is never compared to another
# product's enum by name.
#
# The right-hand column is what makes the integration native rather than
# adjacent. Every BrotherDS stage names the shared stage it STANDS IN, so the
# third product occupies the same chain the other two occupy, for a different
# unit. It adds no stage to the chain.
#
# docs/NORTH-STAR-CHAIN.md carries this same table, and selftest refuses to let
# the document and the code disagree.
CHAIN = (
    ("question",         ("intent",)),
    ("design",           ("method",)),
    ("provenance",       ("provenance",)),
    ("receipt",          ("passport",)),
    ("refusals",         ("required-proof", "evidence-integrity")),
    ("human-decision",   ("human-decision",)),
    ("decision-taken",   ()),
    ("outcome",          ("production-observation",)),
    ("verified-reality", ("verified-reality",)),
)

# A person takes the decision, exactly as a host performs the release. No
# backlog item of this product may claim to serve it. BrotherMode leaves
# `release` out of its servable stages for that reason; this is the same
# refusal, for the claim rather than for the change.
UNSERVABLE = ("decision-taken",)

STAGES = tuple(s for s, _ in CHAIN)
SERVABLE = tuple(s for s in STAGES if s not in UNSERVABLE)

CHAIN_DOC = "docs/NORTH-STAR-CHAIN.md"


def chain_from_doc(path):
    """Parse the ```chain block out of the chain document.

    Returns the same shape as CHAIN. Raises if the block is missing, because a
    document that lost its own contract is a defect, not a NO-DATA.
    """
    text = open(_expand(path)).read()
    lines = text.splitlines()
    try:
        start = lines.index("```chain")
    except ValueError:
        raise ValueError("no ```chain block in %s" % path)
    rows = []
    for line in lines[start + 1:]:
        if line.strip() == "```":
            return tuple(rows)
        if not line.strip():
            continue
        ours, _, shared = line.partition("->")
        shared = shared.strip()
        occupies = () if shared == "NONE" else tuple(
            s.strip() for s in shared.split(",") if s.strip())
        rows.append((ours.strip(), occupies))
    raise ValueError("unterminated ```chain block in %s" % path)


def stage_check(name):
    """An item naming a stage the chain does not hold is a hard error, never a
    silent pass. An item naming the stage a person owns is refused by name."""
    if not name:
        return Finding("stage", NODATA, "no stage named; the item does not say "
                                        "which part of the chain it serves")
    if name in UNSERVABLE:
        return Finding("stage", FAIL,
                       "%s is taken by a person, not by this product. No item "
                       "may serve it." % name)
    if name not in STAGES:
        return Finding("stage", FAIL, "%r is not a stage of this chain. Known: %s"
                       % (name, ", ".join(SERVABLE)))
    occupies = dict(CHAIN)[name]
    return Finding("stage", PASS, "%s, standing in the shared stage %s"
                   % (name, ", ".join(occupies)))


# --------------------------------------------------------------------- seams

# BrotherMode's hollow-value rule, adopted verbatim rather than reinvented:
# an empty string, a whitespace-only string, an empty list or null all read as
# ABSENCE on the consuming side. 0 and False are real answers.
def _answered(v):
    if v is None:
        return False
    if isinstance(v, bool) or isinstance(v, int) or isinstance(v, float):
        return True
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict)):
        return len(v) > 0
    return True


_RANK = {FAIL: 0, NODATA: 1, PASS: 2}


def _worst(verdicts):
    return min(verdicts, key=lambda v: _RANK[v]) if verdicts else NODATA


# The five fields of the change passport, exact spelling from the producing
# side. BrotherDS reads this deposit and nothing else under .sbe/: no tasks,
# no evidence directory, no store. Reaching across the seam to fill a field is
# the failure the seam exists to prevent, and a field this deposit does not
# carry is a defect in the passport, never a licence to read execution state.
PASSPORT_FIELDS = ("whatWasDone", "whoDidIt", "whatWasRun",
                   "whatWasNotEstablished", "whereItCameFrom")


def read_passport(path=".sbe/passport.json"):
    """Consume the change passport. Absence is NO-DATA; padding is a FAIL."""
    p = _expand(path)
    if not p.exists():
        return NODATA, [Finding("passport", NODATA,
                                "no deposit at %s. Nothing was handed over." % path)]
    try:
        with open(p) as fh:
            deposit = json.load(fh)
    except Exception as exc:
        return FAIL, [Finding("passport", FAIL,
                              "deposit at %s exists and cannot be read: %s"
                              % (path, exc))]
    findings = []
    for field in PASSPORT_FIELDS:
        if field not in deposit:
            findings.append(Finding("passport." + field, NODATA,
                                    "absent. The producing side omits what it "
                                    "cannot establish honestly."))
        elif not _answered(deposit[field]):
            findings.append(Finding("passport." + field, FAIL,
                                    "present but empty. A field padded to look "
                                    "filled breaks the producing side's own rule."))
        else:
            findings.append(Finding("passport." + field, PASS, "carried"))
    return _worst([f.verdict for f in findings]), findings


# The five-item handoff package BrotherSBE contracted on 2026-08-11. The CONTENT
# is decided; no wire format was ever ratified. This reader therefore proposes a
# shape and can never certify it, which is why `ratified` caps the verdict below.
HANDOFF_ITEMS = (
    ("dataset", ("grain", "contract", "snapshot_id")),
    ("evaluation_harness", ("split",)),
    ("metric_definitions", ()),
    ("labelled_holdout", ("labelled_by", "labelled_on")),
    ("open_questions", ()),
)


def read_handoff(path=".sbe/handoff.json"):
    """Consume the BrotherSBE handoff package. The shape is PROPOSED, so the
    best verdict this reader can reach is NO-DATA until a shape is ratified."""
    p = _expand(path)
    if not p.exists():
        return NODATA, [Finding("handoff", NODATA,
                                "no package at %s. Anything not in the package "
                                "is not handed over." % path)]
    try:
        with open(p) as fh:
            pkg = json.load(fh)
    except Exception as exc:
        return FAIL, [Finding("handoff", FAIL,
                              "package at %s exists and cannot be read: %s"
                              % (path, exc))]
    findings = []
    for item, required in HANDOFF_ITEMS:
        if item not in pkg:
            findings.append(Finding("handoff." + item, NODATA, "absent"))
            continue
        # open_questions is the one field where an explicit empty list is a real
        # answer (none were open), so the hollow rule is waived for it by name.
        if item == "open_questions":
            if not isinstance(pkg[item], list):
                findings.append(Finding("handoff." + item, FAIL,
                                        "must be a list, stated rather than guessed"))
            else:
                findings.append(Finding("handoff." + item, PASS,
                                        "%d stated" % len(pkg[item])))
            continue
        if not _answered(pkg[item]):
            findings.append(Finding("handoff." + item, FAIL, "present but empty"))
            continue
        missing = [k for k in required
                   if not _answered((pkg[item] or {}).get(k)
                                    if isinstance(pkg[item], dict) else None)]
        if missing:
            findings.append(Finding("handoff." + item, NODATA,
                                    "carried, but without %s" % ", ".join(missing)))
        else:
            findings.append(Finding("handoff." + item, PASS, "carried"))
    verdict = _worst([f.verdict for f in findings])
    if verdict == PASS and not pkg.get("ratified"):
        verdict = NODATA
        findings.append(Finding("handoff.ratified", NODATA,
                                "every item is carried, but no wire format for "
                                "this package has been ratified. This reader "
                                "will not certify a contract nobody signed."))
    return verdict, findings


# -------------------------------------------------------------------- backlog

# BrotherDS's OWN queue-item vocabulary. The same four words appear in
# BrotherMode's idle checker for its own queue, and that is where the
# resemblance stops: this list is never compared to another product's enum, by
# string or otherwise. Four stage and state vocabularies already coexist across
# the triumvirate and reusing one by string-matching is how they corrupt.
ITEM_STATES = ("queued", "in_flight", "done", "blocked")

DEFAULT_QUEUE = "docs/plan/QUEUE.json"


def read_backlog(path=DEFAULT_QUEUE):
    """Every item names the stage it serves and the check that closes it.

    This is what turns the chain from a document into a control: until
    something reads a backlog and refuses an item that serves no stage of the
    chain, the stage vocabulary is a discipline rather than a rule.
    """
    p = _expand(path)
    if not p.exists():
        return NODATA, [Finding("backlog", NODATA,
                                "no queue at %s. Nothing was offered to check." % path)]
    try:
        with open(p) as fh:
            items = json.load(fh)
    except Exception as exc:
        return FAIL, [Finding("backlog", FAIL,
                              "queue at %s exists and cannot be read: %s" % (path, exc))]
    if not isinstance(items, list):
        return FAIL, [Finding("backlog", FAIL, "queue must be a list of items")]
    if not items:
        return NODATA, [Finding("backlog", NODATA, "queue is empty")]

    findings, depth = [], 0
    for i, item in enumerate(items):
        label = "backlog[%s]" % (item.get("id") if isinstance(item, dict) else i)
        if not isinstance(item, dict):
            findings.append(Finding(label, FAIL, "item is not an object"))
            continue
        state = item.get("state")
        if state not in ITEM_STATES:
            findings.append(Finding(label, FAIL, "state %r is not one of %s"
                                    % (state, ", ".join(ITEM_STATES))))
        elif state == "queued":
            depth += 1
        stage = stage_check(item.get("stage"))
        if stage.verdict != PASS:
            findings.append(Finding(label + ".stage", stage.verdict, stage.detail))
        # An item with no done-check is the item that rots. Done items are past
        # the question; blocked ones are waiting on somebody, and both still owe
        # the check that would close them.
        if not _answered(item.get("check")):
            findings.append(Finding(label, FAIL,
                                    "names no check that would close it"))
    if not findings:
        findings.append(Finding("backlog", PASS,
                                "%d item(s), every one naming a stage of this "
                                "chain and a check that closes it" % len(items)))
    findings.append(Finding("backlog.depth",
                            PASS if depth else NODATA,
                            "%d item(s) queued and unblocked" % depth))
    return _worst([f.verdict for f in findings]), findings


# ----------------------------------------------------------------------- cli

def load(path):
    with open(_expand(path)) as fh:
        return json.load(fh)


def print_chain():
    """The chain, and the shared stage each of its stages stands in."""
    print("BrotherDS occupies the shared north-star chain. It adds no stage.")
    print("")
    for name, occupies in CHAIN:
        if name in UNSERVABLE:
            where = "a person takes this; no item may serve it"
        else:
            where = "stands in: " + ", ".join(occupies)
        print("  %-18s %s" % (name, where))
    print("")
    print("%d stages, %d of them servable by an item of work."
          % (len(STAGES), len(SERVABLE)))
    return 0


def _report_seam(title, verdict, findings):
    print(title)
    print("")
    for f in findings:
        print("  " + f.line())
    print("")
    print("VERDICT %s" % verdict)
    if verdict == NODATA:
        print("        NO-DATA is not a pass and not a block. Something the "
              "seam should carry was never handed over.")
    return 1 if verdict == FAIL else 0


TEMPLATE = {
    "id": "CHANGE-ME-001",
    "statement": "State the claim in one sentence, in the words a decision maker would use.",
    "value": 0,
    "unit": "",
    "origin": "SYSTEM",
    "question": "What decision does this number serve? If none, it is not a claim, it is trivia.",
    "decision": "What changes depending on the answer?",
    "grain": "The level this number was computed at. One row per what?",
    "evidence": {
        "source": "~/path/to/your.duckdb",
        "derivations": [
            {"name": "first route to the number", "sql": "select ..."},
            {"name": "a genuinely independent second route", "sql": "select ..."},
            {"name": "context only", "sql": "select ...", "computes_value": False}
        ]
    },
    "uncertainty": {
        "kind": "NOT_ESTABLISHED",
        "why": "Say why there is no interval. If you can state one, replace this "
               "with kind, interval and method instead."
    },
    "not_established": [
        "What this claim does not settle. This list may never be empty.",
        "A reviewer reads it to know where to spend their attention."
    ]
}


def scaffold(claim_id, dest):
    """Write a claim skeleton. The comments live in the placeholder text itself,
    because a template whose guidance sits in a separate document gets filled in
    without the guidance."""
    t = dict(TEMPLATE)
    t["id"] = claim_id
    p = _expand(dest)
    if p.exists():
        print("refusing to overwrite %s" % p)
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as fh:
        json.dump(t, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s" % p)
    print("Fill it in, then: python3 bds.py check %s" % dest)
    print("")
    print("The five origins, pick the honest one:")
    for k, v in sorted(ORIGINS.items()):
        print("  %-12s %s" % (k, v))
    return 0


# The fields a claim cannot be checked without, per gates G1, G2, G3 and G9.
# `author` refuses to write a file missing any of these, instead of writing a
# claim that then hits its first FAIL only when someone runs `check` on it.
REQUIRED_FIELDS = ("id", "statement", "value", "unit", "origin",
                   "question", "decision", "grain")


def _parse_author_input(text):
    """key=value lines, stdlib only, no JSON typed by hand.

    A dotted key (uncertainty.kind) nests one level. The key `not_established`
    may repeat; each line becomes one list entry. The key `evidence.derivation`
    may repeat too, each line shaped `name|sql` or `name|sql|context` (the
    third field marks a supporting query that does not compute the value).
    Blank lines and lines starting with # are skipped.
    """
    claim = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("line %d has no '=': %r" % (lineno, raw))
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise ValueError("line %d has an empty key: %r" % (lineno, raw))
        if key == "not_established":
            claim.setdefault("not_established", []).append(value)
        elif key == "evidence.derivation":
            parts = [p.strip() for p in value.split("|")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise ValueError("line %d: evidence.derivation needs "
                                 "name|sql, got %r" % (lineno, raw))
            d = {"name": parts[0], "sql": parts[1]}
            if len(parts) > 2 and parts[2].lower() == "context":
                d["computes_value"] = False
            claim.setdefault("evidence", {}).setdefault("derivations", []).append(d)
        elif "." in key:
            # Nests to any depth (evidence.comparison.protocol.provider=...),
            # not just one level, because the comparison block needs three.
            parts = key.split(".")
            node = claim
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = value
        else:
            claim[key] = value
    return claim


def _author_errors(claim):
    """Validate before writing. Nothing here is silently defaulted: a missing
    or malformed field is named and the write is refused."""
    errors = []
    for field in REQUIRED_FIELDS:
        v = claim.get(field)
        if v is None or (isinstance(v, str) and not v.strip()):
            errors.append("missing required field: %s" % field)

    if claim.get("value") is not None:
        try:
            f = float(claim["value"])
        except (TypeError, ValueError):
            errors.append("value must be numeric, got %r" % claim["value"])
        else:
            claim["value"] = int(f) if f == int(f) else f

    origin = claim.get("origin")
    if origin is not None and origin not in ORIGINS:
        errors.append("origin must be one of %s, got %r"
                      % (", ".join(sorted(ORIGINS)), origin))

    # The dual-run comparison, if the author chose to authored one. Missing
    # incumbent/incumbent_value/tolerance/protocol fields are not refused here:
    # `check` reports those as NO-DATA by name, same as any other origin's
    # protocol. Only the enum and the numeric shape are hard errors, matching
    # how origin and value are treated above.
    comp = (claim.get("evidence") or {}).get("comparison")
    if comp:
        cls = comp.get("class")
        if cls is not None and cls not in ("THIRD_PARTY", "ELICITED"):
            errors.append("evidence.comparison.class must be THIRD_PARTY or "
                          "ELICITED, got %r" % cls)
        for f in ("incumbent_value", "tolerance"):
            if comp.get(f) is not None:
                try:
                    v = float(comp[f])
                except (TypeError, ValueError):
                    errors.append("evidence.comparison.%s must be numeric, "
                                  "got %r" % (f, comp[f]))
                else:
                    comp[f] = int(v) if v == int(v) else v

    ne = claim.get("not_established")
    if ne is None:
        errors.append("missing required field: not_established (repeat "
                      "'not_established=...' at least once)")
    elif not isinstance(ne, list) or not ne:
        errors.append("not_established must be a non-empty list")

    u = claim.get("uncertainty")
    if not isinstance(u, dict) or "kind" not in u:
        errors.append("missing required field: uncertainty.kind (plus "
                      "uncertainty.why, or uncertainty.interval and "
                      "uncertainty.method)")
    elif u["kind"] == "NOT_ESTABLISHED":
        if not u.get("why"):
            errors.append("uncertainty.kind=NOT_ESTABLISHED requires uncertainty.why")
    elif "interval" not in u or "method" not in u:
        errors.append("uncertainty.kind=%s requires uncertainty.interval "
                      "and uncertainty.method" % u["kind"])
    elif isinstance(u["interval"], str):
        try:
            lo, hi = (float(x) for x in u["interval"].split(","))
        except ValueError:
            errors.append("uncertainty.interval must be 'lo,hi', got %r"
                          % u["interval"])
        else:
            u["interval"] = [lo, hi]

    return errors


def author(dest, stdin_text):
    """Write a claim from key=value lines on stdin: authoring a claim as a
    side effect of doing the analysis, never by hand-editing JSON."""
    p = _expand(dest)
    if p.exists():
        print("refusing to overwrite %s" % p)
        return 1
    try:
        claim = _parse_author_input(stdin_text)
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 2

    errors = _author_errors(claim)
    if errors:
        print("REFUSED, %d problem(s), nothing written:" % len(errors))
        for e in errors:
            print("  - %s" % e)
        return 2

    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as fh:
        json.dump(claim, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s" % p)
    print("")

    registry = os.path.join(os.path.dirname(_expand(dest)) or ".", DEFAULT_REGISTRY)
    verdict, findings, _ = check(claim, registry)
    print_check(claim, verdict, findings)
    return 1 if verdict == FAIL else 0


def score(claim, path, actual, observed_by, observed_on, cause=None, lesson=None):
    """Let reality grade the claim.

    A claim whose uncertainty was NOT_ESTABLISHED can never be HELD or MISSED,
    only UNSCOREABLE. That is deliberate: the north star counts claims that
    stated what they expected and were right. Declining to state an interval
    keeps you out of the denominator, and out of the numerator too.

    A review rides alongside the outcome: goal (what was stated), result
    (what happened), cause and lesson (both optional, both honestly recorded
    as NO-DATA when the caller gives nothing). A MISSED claim also proposes
    itself as a vault lesson candidate through vault_bridge, advisory only
    and never silent about why it could not.
    """
    u = claim.get("uncertainty") or {}
    recorded = claim.get("value")
    err = None
    if recorded is not None:
        try:
            err = float(actual) - float(recorded)
        except (TypeError, ValueError):
            err = None

    kind = u.get("kind")
    quantile_result = None
    if kind == "quantiles":
        q = u.get("quantiles") if isinstance(u.get("quantiles"), dict) else {}
        try:
            pairs = _quantile_pairs(q)
            values = dict(pairs)
            lo, mid, hi = values[0.1], values[0.5], values[0.9]
            if not all(math.isfinite(v) for v in (lo, mid, hi)):
                raise ValueError("quantile values must be finite")
        except (KeyError, TypeError, ValueError):
            state = "UNSCOREABLE"
            why = ("quantiles did not carry a usable 0.1/0.5/0.9 band, so "
                   "there is nothing for reality to fall inside or outside. "
                   "This claim cannot count toward the verified claim rate.")
        else:
            inside = lo <= float(actual) <= hi
            state = "HELD" if inside else "MISSED"
            why = ("actual %s %s the 0.1-0.9 quantile band [%s, %s]"
                   % (_fmt(actual), "fell inside" if inside else "fell outside",
                      _fmt(lo), _fmt(hi)))
            rel_width = ((hi - lo) / mid) if mid != 0 else (
                "NO-DATA: the 0.5 quantile is zero")
            quantile_result = {"band": [lo, hi], "relative_width": rel_width,
                               "band_hit": inside}
            # A proper score beside the inside/outside verdict: WIS rewards a
            # band that is both sharp and calibrated (Bracher et al. 2021).
            try:
                w = forecast_score.wis(q, float(actual))
            except ValueError as exc:
                quantile_result["wis"] = "NO-DATA: %s" % exc
            else:
                quantile_result.update({
                    "wis": w["wis"], "relative_wis": w["relative_wis"],
                    "wis_dispersion": w["dispersion"],
                    "wis_underprediction": w["underprediction"],
                    "wis_overprediction": w["overprediction"]})
    elif kind == "NOT_ESTABLISHED" or "interval" not in u:
        state = "UNSCOREABLE"
        explanation = _ne_reason(u) if kind == "NOT_ESTABLISHED" else None
        why = ("no interval was stated%s, so there is nothing for reality to "
               "fall inside or outside. This claim cannot count toward the "
               "verified claim rate."
               % (" (%s)" % explanation if _answered(explanation) else ""))
    else:
        lo, hi = u["interval"][0], u["interval"][1]
        inside = float(lo) <= float(actual) <= float(hi)
        state = "HELD" if inside else "MISSED"
        why = "actual %s %s the stated interval [%s, %s]" % (
            _fmt(actual), "fell inside" if inside else "fell outside",
            _fmt(lo), _fmt(hi))

    claim["outcome"] = {"actual": actual, "observed_by": observed_by,
                        "observed_on": observed_on, "state": state,
                        "why": why, "error": err}

    interval = u.get("interval") if isinstance(u.get("interval"), (list, tuple)) else None
    result = {"actual": actual, "state": state}
    if quantile_result is not None:
        result.update(quantile_result)
    review = {
        "goal": {"statement": claim.get("statement"), "interval": interval},
        "result": result,
        "cause": cause if _answered(cause) else "NO-DATA: cause not recorded",
        "lesson": lesson if _answered(lesson) else "NO-DATA: lesson not recorded",
    }
    claim["review"] = review

    with open(_expand(path), "w") as fh:
        json.dump(claim, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("claim   %s" % claim.get("id"))
    print("        %s" % claim.get("statement"))
    print("")
    print("  recorded  %s" % _fmt(recorded))
    print("  actual    %s" % _fmt(actual))
    if err is not None:
        print("  error     %s" % _fmt(err))
    print("  observed  %s by %s" % (observed_on, observed_by))
    if quantile_result is not None:
        rw = quantile_result["relative_width"]
        print("  band      [%s, %s]" % (_fmt(quantile_result["band"][0]),
                                        _fmt(quantile_result["band"][1])))
        print("  width     %s" % (_fmt(rw) if isinstance(rw, (int, float)) else rw))
        wv = quantile_result.get("wis")
        print("  wis       %s" % (_fmt(wv) if isinstance(wv, (int, float)) else wv))
    print("")
    print("OUTCOME %s" % state)
    print("        %s" % why)

    if state == "MISSED":
        lesson_result = vault_bridge.propose_lesson(claim, review, path)
        if lesson_result.get("state") == "OK":
            print("lesson candidate: OK id=%s" % lesson_result.get("id"))
        else:
            print("lesson candidate: NO-DATA: %s" % lesson_result.get("why", "unknown"))
    return 0


def ledger(directory, propose=False, recheck=False):
    """The north star, computed. Verified claim rate is held over resolved,
    where resolved means the claim stated an interval AND an outcome arrived."""
    d = _expand(directory)
    rows, counts = [], {"HELD": 0, "MISSED": 0, "UNSCOREABLE": 0, "OPEN": 0}
    unreadable = []
    quantile_widths = []
    band_hits = []
    for p in sorted(d.glob("*.json")):
        # dotfiles are the ledger's own state (.bds-lessons-seen.json), never claims
        if p.name == DEFAULT_REGISTRY or p.name.startswith("."):
            continue
        try:
            with open(p) as fh:
                c = json.load(fh)
        except (ValueError, IOError) as exc:
            # Never skip a claim silently. A rate computed over an unstated
            # subset is the exact failure this product refuses, and it would be
            # sitting inside the function that computes the north star.
            unreadable.append((p.name, "%s: %s" % (type(exc).__name__, exc)))
            continue
        if not isinstance(c, dict):
            unreadable.append((p.name, "not a claim object (top level is %s)" % type(c).__name__))
            continue
        o = c.get("outcome") or {}
        state = o.get("state", "OPEN")
        counts[state] = counts.get(state, 0) + 1
        rows.append((c.get("id", p.stem), state, c.get("statement", "")[:64]))
        if state in ("HELD", "MISSED"):
            rw = ((c.get("review") or {}).get("result") or {}).get("relative_width")
            if isinstance(rw, (int, float)) and not isinstance(rw, bool):
                quantile_widths.append(float(rw))
            hit = ((c.get("review") or {}).get("result") or {}).get("band_hit")
            if isinstance(hit, bool):
                band_hits.append(hit)

    print("%-12s %-12s %s" % ("CLAIM", "OUTCOME", "STATEMENT"))
    for r in rows:
        print("%-12s %-12s %s" % r)
    print("")
    if unreadable:
        print("UNREADABLE, excluded from every count below:")
        for name, why in unreadable:
            print("  %-28s %s" % (name, why))
        print("  The rate that follows is computed over %d of %d claim files. "
              "Fix these before trusting it." % (len(rows), len(rows) + len(unreadable)))
        print("")
    if recheck or propose:
        _recurring(d, propose)
    else:
        print("RECURRING GATE FAILURES  NO-DATA: recomputation not requested; use --recheck")
    resolved = counts["HELD"] + counts["MISSED"]
    print("open %d, unscoreable %d, resolved %d"
          % (counts["OPEN"], counts["UNSCOREABLE"], resolved))
    if resolved == 0:
        print("")
        print("VERIFIED CLAIM RATE  NO-DATA")
        print("        reason: no scoreable claim has resolved yet")
        print("        No claim has both stated an interval and been scored "
              "against a real outcome. The north star has no numerator and no")
        print("        denominator yet. This is the honest state, not a zero.")
        return 0
    rate = counts["HELD"] / float(resolved)
    print("")
    print("VERIFIED CLAIM RATE  %.0f%%  (%d held of %d resolved)"
          % (100 * rate, counts["HELD"], resolved))
    if resolved < 10:
        print("        sample too small for any performance conclusion (n=%d)"
              % resolved)
    if quantile_widths:
        widths = sorted(quantile_widths)
        n = len(widths)
        mid = n // 2
        median_w = widths[mid] if n % 2 else (widths[mid - 1] + widths[mid]) / 2.0
        print("median relative interval width: %s" % _fmt(median_w))
    if band_hits:
        cov = forecast_score.coverage(band_hits, nominal=0.8)
        if cov["verdict"].startswith("NO-DATA"):
            print("0.1-0.9 band coverage: %s (n=%d)" % (cov["verdict"], cov["n"]))
        else:
            print("0.1-0.9 band coverage: %s [%s, %s] over %d, nominal 0.8: %s"
                  % (_fmt(cov["coverage"]), _fmt(cov["lo"]), _fmt(cov["hi"]),
                     cov["n"], cov["verdict"]))
    return 0


def _recurring(d, propose):
    """The gates the team keeps failing across the claims in this ledger. A
    habit, not an accident: with propose, each new one is filed through the
    Vault's intake door as a lesson candidate, once (keys kept beside the
    claims in .bds-lessons-seen.json). Never moves a verdict."""
    if lessons is None:
        print("")
        print("RECURRING GATE FAILURES  NO-DATA: lessons.py is not installed beside bds.py")
        return
    results = []
    for p in sorted(d.glob("*.json")):
        # dotfiles are the ledger's own state (.bds-lessons-seen.json), never claims
        if p.name == DEFAULT_REGISTRY or p.name.startswith("."):
            continue
        try:
            with open(p) as fh:
                c = json.load(fh)
            if not isinstance(c, dict):
                raise ValueError("not a claim object")
            _verdict, fs, _values = check(c)
        except Exception as exc:  # a claim that cannot be checked is named, never skipped
            print("  recurring: %s could not be checked: %s" % (p.name, exc))
            continue
        results.append({"claim_id": str(c.get("id", p.stem)),
                        "claim_type": str(c.get("claim_type") or "UNDECLARED"),
                        "findings": [{"gate": f.gate, "verdict": f.verdict,
                                      "detail": str(f.detail)} for f in fs]})
    items = lessons.recurring_failures(results, min_claims=2)
    print("")
    if not results:
        print("RECURRING GATE FAILURES  NO-DATA: no checkable claim in this ledger")
    elif not items:
        print("RECURRING GATE FAILURES  none: no gate failed on two or more of %d claims"
              % len(results))
    else:
        print("RECURRING GATE FAILURES  (a gate failed on two or more of %d claims)"
              % len(results))
        for it in items:
            print("  %-28s %d claims  %s" % (it["gate"], it["count"], ", ".join(it["claims"][:6])))
            print("      next time: %s" % lessons.guidance_for(it["gate"]))
    if propose and items:
        seen_path = d / ".bds-lessons-seen.json"
        seen = set()
        if seen_path.exists():
            try:
                with open(seen_path) as fh:
                    raw = json.load(fh)
                if not isinstance(raw, list):
                    raise ValueError("top level is %s, not a list" % type(raw).__name__)
                seen = set(str(k) for k in raw)
            except (ValueError, IOError) as exc:
                # Say it: an unreadable memory means every recurring gate is proposed again.
                print("  lesson memory %s unreadable (%s); every recurring gate counts as new"
                      % (seen_path.name, exc))
        fresh = lessons.new_items(items, seen)
        for it in fresh:
            title, body = lessons.lesson_text(it)
            r = vault_bridge.propose_recurring(title, body)
            if r.get("state") == "OK":
                seen.add(lessons.lesson_key(it))
                print("  lesson candidate: OK id=%s (%s)" % (r.get("id"), it["gate"]))
            else:
                print("  lesson candidate: NO-DATA: %s (%s)" % (r.get("why", "unknown"), it["gate"]))
        if not fresh:
            print("  lesson candidates: none new (every recurring gate was already proposed)")
        try:
            with open(seen_path, "w") as fh:
                json.dump(sorted(seen), fh, indent=1)
        except IOError as exc:
            print("  lesson memory NO-DATA: could not write %s (%s); the next run proposes these again"
                  % (seen_path.name, exc))
    print("")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]

    if cmd == "selftest":
        return selftest()

    if cmd == "new":
        if len(argv) < 3:
            print("usage: bds.py new <CLAIM-ID> [path.json]")
            return 2
        claim_id = argv[2]
        dest = argv[3] if len(argv) > 3 else "claims/%s.json" % claim_id.lower()
        return scaffold(claim_id, dest)

    if cmd == "author":
        if len(argv) < 3:
            print("usage: python3 bds.py author <dest.json>   "
                  "(reads key=value lines from stdin)")
            return 2
        return author(argv[2], sys.stdin.read())

    if cmd == "mdm-eval":
        # Recompute precision and recall from a clerical review sample, so the
        # numbers a master data claim carries are derived, never typed.
        try:
            if len(argv) > 2 and argv[2] in ("clusters", "drift"):
                # gold-subset precision (M10) and match-score drift (M13), derived
                import mdm_derive
                return mdm_derive.main(["mdm_derive.py"] + list(argv[2:]))
            import mdm_eval
            return mdm_eval._main(["mdm_eval.py"] + list(argv[2:]))
        except Exception as exc:  # the verb reports, it never tracebacks
            print("NO-DATA: %s" % exc)
            return 2
    if cmd == "mdm-audit":
        # Audit an exported matching run (counts, collisions, shared keys,
        # segments) and write the stratified plan a steward labels (M23 to M30).
        try:
            import mdm_audit
            return mdm_audit.main(list(argv[2:]))
        except SystemExit as exc:  # argparse usage errors
            return exc.code if isinstance(exc.code, int) else 2
        except Exception as exc:  # the verb reports, it never tracebacks
            print("NO-DATA: %s" % exc)
            return 2
    if cmd == "ledger":
        rest = [a for a in argv[2:] if a not in ("--propose-lessons", "--recheck")]
        return ledger(rest[0] if rest else "claims",
                      propose="--propose-lessons" in argv[2:],
                      recheck="--recheck" in argv[2:])

    if cmd == "chain":
        return print_chain()

    if cmd == "stage":
        f = stage_check(argv[2] if len(argv) > 2 else "")
        print(f.line())
        return 1 if f.verdict == FAIL else 0

    if cmd == "passport":
        return _report_seam("CHANGE PASSPORT, consumed from BrotherMode",
                            *read_passport(argv[2] if len(argv) > 2
                                           else ".sbe/passport.json"))

    if cmd == "handoff":
        return _report_seam("HANDOFF PACKAGE, consumed from BrotherSBE",
                            *read_handoff(argv[2] if len(argv) > 2
                                          else ".sbe/handoff.json"))

    if cmd == "backlog":
        return _report_seam("BACKLOG, every item against the chain",
                            *read_backlog(argv[2] if len(argv) > 2
                                          else DEFAULT_QUEUE))

    if len(argv) < 3:
        print("usage: bds.py %s <claim.json>" % cmd)
        return 2
    path = argv[2]
    claim = load(path)

    if cmd == "register":
        return register_definition(claim, os.path.join(
            os.path.dirname(_expand(path)) or ".", DEFAULT_REGISTRY))

    if cmd == "score":
        rest = argv[3:]
        if not rest:
            print("usage: bds.py score <claim.json> <actual> [observed_by] "
                  "[observed_on] [cause] [lesson]")
            return 2
        actual = float(rest[0])
        by = rest[1] if len(rest) > 1 else "not stated"
        on = rest[2] if len(rest) > 2 else "not stated"
        cause = rest[3] if len(rest) > 3 else None
        lesson = rest[4] if len(rest) > 4 else None
        return score(claim, path, actual, by, on, cause, lesson)

    registry = os.path.join(os.path.dirname(_expand(path)) or ".", DEFAULT_REGISTRY)
    verdict, findings, _ = check(claim, registry)

    if cmd == "check":
        print_check(claim, verdict, findings)
        return 1 if verdict == FAIL else 0

    if cmd == "receipt":
        dest = argv[3] if len(argv) > 3 else None
        out = receipt(claim, verdict, findings, dest)
        if dest:
            with open(_expand(dest), "w") as fh:
                fh.write(out + "\n")
            print("wrote %s" % dest)
        else:
            print(out)
        return 0

    print("unknown command %r" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
