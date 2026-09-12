"""Audit library for master data matching results.

This module audits a matcher results table before anyone acts on its headline
numbers. It proves that the exported table is internally consistent (statuses,
reference ids, score ranges, duplicate source ids), that collisions and phone
key hubs are visible, and it turns the table into a sampled review plan whose
labels can be turned into an evaluation with Wilson intervals. It does not
prove precision: a similarity score is not a precision, the unmatched share
mixes coverage gaps with misses until the unmatched strata are labelled, and
the Chapman capture recapture estimate is exploratory only, never a gate.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
import unicodedata


_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mdm_eval
import mdm_validate
import audit_provenance


REQUIRED_COLUMNS = ["source_id", "reference_id", "pathway", "status", "score"]
BANDS = ["lt_0.80", "0.80_0.90", "0.90_0.95", "ge_0.95", "no_score"]
PROVENANCE_COLUMNS = ["reference_snapshot", "model_version", "normalizer_version"]
PLAN_COLUMNS = [
    "plan_id", "stratum", "source_id", "reference_id", "pathway",
    "score", "verifier", "stratum_population", "stratum_sampled", "label",
]
DIGITS = "0123456789"


def _s(v):
    if v is None:
        return ""
    return str(v).strip()


def normalize_phone(text):
    s = _s(text)
    if not s:
        return ""
    s2 = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s2 if ch in DIGITS)


def score_band(raw):
    s = _s(raw)
    if s == "":
        return "no_score"
    try:
        v = float(s)
    except (ValueError, TypeError):
        return "no_score"
    if v < 0.80:
        return "lt_0.80"
    if v < 0.90:
        return "0.80_0.90"
    if v < 0.95:
        return "0.90_0.95"
    return "ge_0.95"


class ResultsRows(list):
    """Rows retain decoding provenance without shared mutable state."""
    def __init__(self, rows, input_encoding):
        super().__init__(rows)
        self.input_encoding = input_encoding


def _read_csv_stripped(path, encoding=None):
    candidates = [encoding] if encoding is not None else ["utf-8-sig", "cp932"]
    for codec in candidates:
        try:
            with open(path, encoding=codec, newline="") as f:
                reader = csv.DictReader(f)
                header = [_s(h) for h in (reader.fieldnames or [])]
                rows = [{_s(k): _s(v) for k, v in raw.items() if k is not None}
                        for raw in reader]
            return header, ResultsRows(rows, codec)
        except (UnicodeDecodeError, LookupError):
            if encoding is not None:
                raise ValueError("could not decode %s as %s" % (path, encoding))
    raise ValueError("could not decode %s as utf-8 or cp932; pass --encoding" % path)


def read_results(path, encoding=None):
    header, rows = _read_csv_stripped(path, encoding=encoding)
    for name in REQUIRED_COLUMNS:
        if name not in header:
            raise ValueError("missing required column: " + name)
    return rows


def review_coverage(entry, outcomes, expected_population=None):
    """Validate declared strata before using their population weights.

    Empty/absent strata retain the legacy direct-sample contract. A declared
    stratification must reconcile, and unlabelled populations are NO-DATA.
    """
    fields = ("population", "sampled") + tuple(outcomes)
    for field in fields:
        value = entry.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(field + " must be a nonnegative integer")
    if entry["sampled"] > entry["population"]:
        raise ValueError("sampled exceeds population")
    if any(entry[f] > entry["sampled"] for f in outcomes):
        raise ValueError("outcome exceeds sampled")
    if len(outcomes) > 1 and sum(entry[f] for f in outcomes) != entry["sampled"]:
        raise ValueError("outcomes do not sum to sampled")
    if expected_population is not None:
        if not isinstance(expected_population, int) or isinstance(expected_population, bool) or expected_population < 0:
            raise ValueError("audited population must be a nonnegative integer")
        if entry["population"] != expected_population:
            raise ValueError("population does not match audited count")
    strata = entry.get("strata")
    if strata is not None and not isinstance(strata, list):
        raise ValueError("strata must be a list")
    if not strata:
        return None
    totals = dict.fromkeys(fields, 0)
    seen = set()
    unreviewed = 0
    for stratum in strata:
        if not isinstance(stratum, dict):
            raise ValueError("stratum must be an object")
        name = stratum.get("stratum")
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ValueError("stratum identities must be nonempty and unique")
        seen.add(name)
        review_coverage({field: stratum.get(field) for field in fields}, outcomes)
        for field in fields:
            totals[field] += stratum[field]
        if stratum["sampled"] == 0:
            unreviewed += stratum["population"]
    for field in fields:
        if totals[field] != entry[field]:
            raise ValueError("strata %s sum does not match aggregate" % field)
    return {"coverage_state": "NO-DATA" if unreviewed else "PASS",
            "reviewed_population": entry["population"] - unreviewed,
            "unreviewed_population": unreviewed}


def audit(rows, hub_min=3):
    n_rows = len(rows)
    status_counts = {"MATCH": 0, "NO_MATCH": 0}
    pathway_counts = {}
    no_match_reasons = {}
    verifier_counts = {}
    problems = []

    dup_rows = 0
    seen_source = set()
    match_no_ref = 0
    nomatch_with_ref = 0
    unknown_status = 0
    score_bad = 0

    for r in rows:
        sid = _s(r.get("source_id"))
        rid = _s(r.get("reference_id"))
        pw = _s(r.get("pathway"))
        st = _s(r.get("status")).upper()
        sc = _s(r.get("score"))

        if sid in seen_source:
            dup_rows += 1
        else:
            seen_source.add(sid)

        if st == "MATCH":
            status_counts["MATCH"] += 1
            pathway_counts[pw] = pathway_counts.get(pw, 0) + 1
            if not rid:
                match_no_ref += 1
        elif st == "NO_MATCH":
            status_counts["NO_MATCH"] += 1
            no_match_reasons[pw] = no_match_reasons.get(pw, 0) + 1
            if rid:
                nomatch_with_ref += 1
        else:
            unknown_status += 1

        v = _s(r.get("verifier")).upper()
        if v:
            verifier_counts[v] = verifier_counts.get(v, 0) + 1

        if sc:
            bad = False
            try:
                fv = float(sc)
                if not math.isfinite(fv) or fv < 0.0 or fv > 1.0:
                    bad = True
            except (ValueError, TypeError):
                bad = True
            if bad:
                score_bad += 1

    if dup_rows:
        problems.append("duplicate source_id: %d row(s)" % dup_rows)
    if match_no_ref:
        problems.append("MATCH row without reference_id: %d row(s)" % match_no_ref)
    if nomatch_with_ref:
        problems.append("NO_MATCH row with reference_id: %d row(s)" % nomatch_with_ref)
    if unknown_status:
        problems.append("unknown status: %d row(s)" % unknown_status)
    if score_bad:
        problems.append("score out of range: %d row(s)" % score_bad)
    if status_counts["MATCH"] + status_counts["NO_MATCH"] != n_rows:
        problems.append("status counts do not sum to n_rows")

    ref_counts = {}
    for r in rows:
        st = _s(r.get("status")).upper()
        rid = _s(r.get("reference_id"))
        if st == "MATCH" and rid:
            ref_counts[rid] = ref_counts.get(rid, 0) + 1
    multi = [(k, v) for k, v in ref_counts.items() if v >= 2]
    multi.sort(key=lambda kv: (-kv[1], kv[0]))
    rows_in_coll = sum(v for _, v in multi)
    collisions = {
        "reference_ids_with_multiple_sources": len(multi),
        "rows_in_collisions": rows_in_coll,
        "top": [[k, v] for k, v in multi[:10]],
    }

    if not any("key_phone" in r for r in rows):
        key_hubs = {"key": "key_phone", "state": "NO-DATA"}
    else:
        norm_counts = {}
        for r in rows:
            d = normalize_phone(r.get("key_phone"))
            if not d:
                continue
            norm_counts[d] = norm_counts.get(d, 0) + 1
        hub_keys = set(k for k, v in norm_counts.items() if v >= hub_min)
        rows_on_hubs = 0
        matched_on_hubs = 0
        for r in rows:
            d = normalize_phone(r.get("key_phone"))
            if d and d in hub_keys:
                rows_on_hubs += 1
                if _s(r.get("status")).upper() == "MATCH":
                    matched_on_hubs += 1
        top_hubs = [(k, v) for k, v in norm_counts.items() if v >= hub_min]
        top_hubs.sort(key=lambda kv: (-kv[1], kv[0]))
        key_hubs = {
            "key": "key_phone",
            "hub_min": hub_min,
            "hubs": len(hub_keys),
            "rows_on_hubs": rows_on_hubs,
            "matched_rows_on_hubs": matched_on_hubs,
            "top": [[k, v] for k, v in top_hubs[:10]],
        }

    pathway_band = {}
    for r in rows:
        if _s(r.get("status")).upper() != "MATCH":
            continue
        pw = _s(r.get("pathway"))
        d = pathway_band.setdefault(pw, dict((b, 0) for b in BANDS))
        d[score_band(r.get("score"))] += 1

    if not any("segment" in r for r in rows):
        segments = []
    else:
        seg_counts = {}
        for r in rows:
            seg = _s(r.get("segment"))
            if seg == "":
                seg = "(none)"
            d = seg_counts.setdefault(seg, [0, 0])
            d[0] += 1
            if _s(r.get("status")).upper() == "MATCH":
                d[1] += 1
        seg_list = []
        for seg, pair in seg_counts.items():
            n = pair[0]
            m = pair[1]
            rate = float(m) / float(n) if n else 0.0
            lo, hi = mdm_eval.wilson_interval(m, n)
            seg_list.append({
                "segment": seg,
                "n": n,
                "matched": m,
                "rate": rate,
                "lo": lo,
                "hi": hi,
            })
        seg_list.sort(key=lambda d: (-d["n"], d["segment"]))
        segments = seg_list

    has_a = any("hit_a" in r for r in rows)
    has_b = any("hit_b" in r for r in rows)
    if not (has_a and has_b):
        capture_recapture = {
            "state": "NO-DATA",
            "note": ("hit_a or hit_b column is absent; Chapman is exploratory "
                     "only and never a gate."),
        }
    else:
        n1 = 0
        n2 = 0
        m = 0
        for r in rows:
            a = _s(r.get("hit_a"))
            b = _s(r.get("hit_b"))
            if a == "" or b == "":
                continue
            if a not in ("0", "1"):
                raise ValueError(
                    "hit_a must be 0 or 1 for source_id " + _s(r.get("source_id")))
            if b not in ("0", "1"):
                raise ValueError(
                    "hit_b must be 0 or 1 for source_id " + _s(r.get("source_id")))
            if a == "1":
                n1 += 1
            if b == "1":
                n2 += 1
            if a == "1" and b == "1":
                m += 1
        if m == 0:
            capture_recapture = {
                "state": "NO-DATA",
                "note": ("No rows had both hit_a and hit_b set to 1. Positively "
                         "dependent methods overstate recall; this is never a gate."),
            }
        else:
            n_hat = float((n1 + 1) * (n2 + 1)) / float(m + 1) - 1.0
            capture_recapture = {
                "state": "EXPLORATORY",
                "n1": n1,
                "n2": n2,
                "m": m,
                "n_hat": n_hat,
                "note": ("Chapman estimate. Positively dependent methods overstate "
                         "recall; this number is exploratory only and never a gate."),
            }

    present = []
    missing = []
    for c in PROVENANCE_COLUMNS:
        if any(_s(r.get(c)) for r in rows):
            present.append(c)
        else:
            missing.append(c)
    provenance = {"present": present, "missing": missing}

    identifiers = {}
    for kind, checker in (("corporate_number", mdm_validate.corporate_number_check),
                          ("invoice_number", mdm_validate.invoice_number_check)):
        if any(kind in row for row in rows):
            block = mdm_validate.validate_column([row.get(kind) for row in rows], kind)
            block["matched_invalid"] = sum(
                1 for row in rows if _s(row.get("status")).upper() == "MATCH"
                and _s(row.get(kind)) and not checker(row.get(kind))["valid"])
            identifiers[kind] = block

    return {
        "identifiers": identifiers,
        "input_encoding": None,
        "n_rows": n_rows,
        "reconciliation": {
            "status_counts": status_counts,
            "pathway_counts": pathway_counts,
            "no_match_reasons": no_match_reasons,
            "verifier_counts": verifier_counts,
            "problems": problems,
            "consistent": len(problems) == 0,
        },
        "collisions": collisions,
        "key_hubs": key_hubs,
        "pathway_band": pathway_band,
        "segments": segments,
        "capture_recapture": capture_recapture,
        "provenance": provenance,
        "input_sha256": None,
    }


def audit_path(path, hub_min=3, encoding=None):
    rows, digest = _results_snapshot(path, encoding=encoding)
    result = audit(rows, hub_min=hub_min)
    result["input_sha256"] = digest
    result["input_encoding"] = rows.input_encoding
    return result


def _results_snapshot(path, encoding=None, strict=False):
    """Hash and parse the same captured export bytes."""
    with open(path, "rb") as f:
        raw = f.read()
    header, parsed, codec = audit_provenance.csv_bytes(raw, encoding, strict=strict)
    header = [_s(h) for h in header]
    if strict and (any(not h for h in header) or len(set(header)) != len(header)):
        raise ValueError("CSV header must contain unique nonempty columns")
    for name in REQUIRED_COLUMNS:
        if name not in header:
            raise ValueError("missing required column: " + name)
    rows = ResultsRows([{_s(k): _s(v) for k, v in row.items()} for row in parsed], codec)
    return rows, hashlib.sha256(raw).hexdigest()


def review_plan(rows, margin=0.10, min_n=10, max_n=400, seed=7):
    try:
        m = float(margin)
    except (TypeError, ValueError):
        raise ValueError("margin must be greater than 0")
    if not (m > 0):
        raise ValueError("margin must be greater than 0")
    target = math.ceil(1.96 * 1.96 * 0.25 / (m * m))
    target = max(int(min_n), min(int(max_n), int(target)))

    strata = {}
    for r in rows:
        st = _s(r.get("status")).upper()
        pw = _s(r.get("pathway"))
        if st == "MATCH":
            band = score_band(r.get("score"))
            name = "match:" + pw + ":" + band
        elif st == "NO_MATCH":
            name = "unmatched:" + pw
        else:
            continue
        strata.setdefault(name, []).append(r)

    plan = []
    counter = 1
    for name in sorted(strata.keys()):
        stratum_rows = sorted(strata[name], key=lambda r: _s(r.get("source_id")))
        pop = len(stratum_rows)
        n = min(pop, target)
        rng = random.Random("%s:%s" % (seed, name))
        chosen = rng.sample(stratum_rows, n)
        chosen.sort(key=lambda r: _s(r.get("source_id")))
        for r in chosen:
            plan.append({
                "plan_id": "P%04d" % counter,
                "stratum": name,
                "source_id": _s(r.get("source_id")),
                "reference_id": _s(r.get("reference_id")),
                "pathway": _s(r.get("pathway")),
                "score": _s(r.get("score")),
                "verifier": _s(r.get("verifier")),
                "stratum_population": str(pop),
                "stratum_sampled": str(n),
                "label": "",
            })
            counter += 1
    return plan


def write_plan(plan, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(PLAN_COLUMNS)
        for row in plan:
            w.writerow([_s(row.get(c)) for c in PLAN_COLUMNS])


def _pathway_from_match_stratum(name):
    rest = name[len("match:"):]
    idx = rest.rfind(":")
    if idx >= 0:
        return rest[:idx]
    return rest


def evaluate_plan(labelled_rows, results_rows=None):
    for r in labelled_rows:
        lbl = _s(r.get("label"))
        if lbl == "" or lbl in ("0", "1"):
            continue
        raise ValueError("invalid label for plan_id " + _s(r.get("plan_id")) + ": " + lbl)

    groups = {}
    for r in labelled_rows:
        name = _s(r.get("stratum"))
        if name not in groups:
            groups[name] = {
                "population": 0,
                "stratum_sampled": 0,
                "rows": [],
                "seen": False,
            }
        g = groups[name]
        metadata = {}
        for field in ("stratum_population", "stratum_sampled"):
            raw = _s(r.get(field))
            if not raw.isascii() or not raw.isdigit():
                raise ValueError(field + " must be a nonnegative integer")
            metadata[field] = int(raw)
        if metadata["stratum_sampled"] > metadata["stratum_population"]:
            raise ValueError("stratum_sampled exceeds stratum_population")
        if g["seen"] and (g["population"] != metadata["stratum_population"] or
                          g["stratum_sampled"] != metadata["stratum_sampled"]):
            raise ValueError("inconsistent stratum metadata: " + name)
        g["population"] = metadata["stratum_population"]
        g["stratum_sampled"] = metadata["stratum_sampled"]
        g["seen"] = True
        g["rows"].append(r)

    if results_rows is not None:
        populations = {}
        for row in results_rows:
            status = _s(row.get("status")).upper()
            pathway = _s(row.get("pathway"))
            if status == "MATCH":
                name = "match:" + pathway + ":" + score_band(row.get("score"))
            elif status == "NO_MATCH":
                name = "unmatched:" + pathway
            else:
                continue
            populations[name] = populations.get(name, 0) + 1
        for name, group in groups.items():
            if name not in populations or group["population"] != populations[name]:
                raise ValueError("stratum population does not match results: " + name)
        for name, population in populations.items():
            if name not in groups:
                groups[name] = {"population": population, "stratum_sampled": 0,
                                "rows": [], "seen": True}

    strata_list = []
    pathway_review = {}
    unmatched_pop = 0
    unmatched_sampled = 0
    unmatched_pos = 0
    unmatched_neg = 0
    unlabelled = 0
    unmatched_strata = []

    ver_conf = {"population": 0, "sampled": 0, "positive": 0}
    ver_rej = {"population": 0, "sampled": 0, "positive": 0}
    has_conf = False
    has_rej = False

    for name in sorted(groups.keys()):
        g = groups[name]
        if len(g["rows"]) != g["stratum_sampled"]:
            raise ValueError("plan row count differs from stratum_sampled: " + name)
        labelled = 0
        positive = 0
        for r in g["rows"]:
            lbl = _s(r.get("label"))
            if lbl == "":
                unlabelled += 1
                continue
            labelled += 1
            if lbl == "1":
                positive += 1

        strata_list.append({
            "stratum": name,
            "population": g["population"],
            "sampled": g["stratum_sampled"],
            "labelled": labelled,
            "positive": positive,
        })

        if name.startswith("match:"):
            pw = _pathway_from_match_stratum(name)
            pd = pathway_review.setdefault(
                pw, {"population": 0, "sampled": 0, "positive": 0, "strata": []})
            pd["population"] += g["population"]
            pd["sampled"] += labelled
            pd["positive"] += positive
            pd["strata"].append({
                "stratum": name,
                "population": g["population"],
                "sampled": labelled,
                "positive": positive,
            })
        elif name.startswith("unmatched:"):
            unmatched_pop += g["population"]
            unmatched_sampled += labelled
            unmatched_pos += positive
            unmatched_neg += (labelled - positive)
            unmatched_strata.append({
                "stratum": name,
                "population": g["population"],
                "sampled": labelled,
                "matchable_missed": positive,
                "reference_absent": labelled - positive,
            })

        for r in g["rows"]:
            lbl = _s(r.get("label"))
            if lbl == "":
                continue
            v = _s(r.get("verifier")).upper()
            if v == "CONFIRMED" and name.startswith("match:"):
                has_conf = True
                ver_conf["sampled"] += 1
                if lbl == "1":
                    ver_conf["positive"] += 1
            elif v == "REJECTED" and name.startswith("unmatched:"):
                has_rej = True
                ver_rej["sampled"] += 1
                if lbl == "1":
                    ver_rej["positive"] += 1

    if results_rows is not None:
        conf_pop = 0
        rej_pop = 0
        for r in results_rows:
            v = _s(r.get("verifier")).upper()
            if v == "CONFIRMED":
                conf_pop += 1
            elif v == "REJECTED":
                rej_pop += 1
        ver_conf["population"] = conf_pop
        ver_rej["population"] = rej_pop
    else:
        ver_conf["population"] = ver_conf["sampled"]
        ver_rej["population"] = ver_rej["sampled"]

    verifier = {
        "confirmed_review": ver_conf if has_conf else None,
        "rejected_review": ver_rej if has_rej else None,
    }

    weighted_input = []
    for s in unmatched_strata:
        if s["sampled"] >= 1:
            weighted_input.append({
                "population": s["population"],
                "sampled": s["sampled"],
                "positive": s["matchable_missed"],
            })
    if weighted_input:
        matchable_missed_weighted = mdm_eval.stratified_estimate(weighted_input)
    else:
        matchable_missed_weighted = None

    unmatched_review = {
        "population": unmatched_pop,
        "sampled": unmatched_sampled,
        "reference_absent": unmatched_neg,
        "matchable_missed": unmatched_pos,
        "strata": unmatched_strata,
        "matchable_missed_weighted": matchable_missed_weighted,
    }

    coverage = review_coverage(unmatched_review, ("reference_absent", "matchable_missed"))
    if coverage is None:
        coverage = {"coverage_state": "NO-DATA", "reviewed_population": 0,
                    "unreviewed_population": unmatched_pop}
    unmatched_review.update(coverage)
    absent_input = [dict(row, positive=row["sampled"] - row["positive"])
                    for row in weighted_input]
    absent_weighted = mdm_eval.stratified_estimate(absent_input) if absent_input else None
    unmatched_review["reference_absent_weighted"] = absent_weighted
    if coverage["coverage_state"] == "NO-DATA":
        unmatched_review["sampled_subpopulation_matchable_missed"] = matchable_missed_weighted
        unmatched_review["sampled_subpopulation_reference_absent"] = absent_weighted
        unmatched_review["matchable_missed_weighted"] = None
        unmatched_review["reference_absent_weighted"] = None
    for entry in pathway_review.values():
        entry.update(review_coverage(entry, ("positive",)) or {})

    return {
        "strata": strata_list,
        "pathway_review": pathway_review,
        "verifier": verifier,
        "unmatched_review": unmatched_review,
        "unlabelled": unlabelled,
    }


def build_parser():
    p = argparse.ArgumentParser(prog="mdm_audit")
    p.add_argument("--selftest", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("audit")
    a.add_argument("results")
    a.add_argument("--encoding")
    a.add_argument("--hub-min", type=int, default=3)
    a.add_argument("--plan")
    a.add_argument("--margin", type=float, default=0.10)
    a.add_argument("--min-n", type=int, default=10)
    a.add_argument("--max-n", type=int, default=400)
    a.add_argument("--seed", type=int, default=7)
    e = sub.add_parser("evaluate")
    e.add_argument("labelled")
    e.add_argument("--encoding")
    e.add_argument("--results")
    e.add_argument("--audit", help="replay against an audit JSON snapshot; requires --results")
    return p


def _emit(obj):
    sys.stdout.write(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False))
    sys.stdout.write("\n")


def _cli_audit(args):
    try:
        rows, digest = _results_snapshot(args.results, encoding=args.encoding,
                                        strict=bool(args.plan))
        result = audit(rows, hub_min=args.hub_min)
        result["input_sha256"] = digest
        result["input_encoding"] = rows.input_encoding
        if args.plan:
            params = audit_provenance.parameters(args.margin, args.min_n, args.max_n, args.seed)
            plan = review_plan(rows, **params)
            result["review_plan"] = audit_provenance.record_plan(
                plan, digest, rows.input_encoding, params, PLAN_COLUMNS)
            if os.path.realpath(args.plan) == os.path.realpath(args.results):
                raise ValueError("plan output must differ from the results input")
            if os.path.exists(args.plan) and os.path.samefile(args.plan, args.results):
                raise ValueError("plan output must differ from the results input")
            write_plan(plan, args.plan)
    except (ValueError, OSError, UnicodeError, OverflowError) as e:
        sys.stdout.write("NO-DATA: " + str(e) + "\n")
        return 2
    _emit(result)
    if args.plan:
        n_strata = len(set(p["stratum"] for p in plan))
        sys.stderr.write("plan: %d rows in %d strata written to %s\n" % (
            len(plan), n_strata, args.plan))
    return 0


def _cli_evaluate(args):
    if args.audit:
        try:
            if not args.results:
                raise ValueError("--audit requires --results")
            snapshot = audit_provenance.load_snapshot(args.audit, PLAN_COLUMNS)
            record = snapshot["review_plan"]
            rows, digest = _results_snapshot(args.results, encoding=record["input_encoding"], strict=True)
            if digest != record["input_sha256"]:
                raise ValueError("export digest differs from audit snapshot")
            with open(args.labelled, "rb") as stream:
                raw = stream.read()
            _, labelled, _ = audit_provenance.csv_bytes(
                raw, args.encoding, strict=True, expected_columns=PLAN_COLUMNS)
            plan = review_plan(rows, **record["parameters"])
            binding = audit_provenance.verify_plan(record, plan, labelled, digest)
            result = evaluate_plan(labelled, results_rows=rows)
            result["provenance_binding"] = binding
        except (ValueError, OSError, UnicodeError, OverflowError, RecursionError) as e:
            sys.stdout.write("NO-DATA: " + str(e) + "\n")
            return 2
        _emit(result)
        return 0
    try:
        labelled = _read_csv_stripped(args.labelled, encoding=args.encoding)[1]
    except (ValueError, OSError) as e:
        sys.stdout.write("NO-DATA: " + str(e) + "\n")
        return 2
    results_rows = None
    if args.results:
        try:
            results_rows = read_results(args.results, encoding=args.encoding)
        except (ValueError, OSError) as e:
            sys.stdout.write("NO-DATA: " + str(e) + "\n")
            return 2
    try:
        result = evaluate_plan(labelled, results_rows=results_rows)
    except ValueError as e:
        sys.stdout.write("NO-DATA: " + str(e) + "\n")
        return 2
    _emit(result)
    return 0


def _sample_rows():
    return [
        {"source_id": "S1", "reference_id": "R1", "pathway": "key_phone",
         "status": "MATCH", "score": "0.99", "verifier": "CONFIRMED",
         "segment": "A", "key_phone": "+1 (555) 0101", "hit_a": "1",
         "hit_b": "1", "reference_snapshot": "v1"},
        {"source_id": "S2", "reference_id": "R1", "pathway": "key_phone",
         "status": "MATCH", "score": "0.85", "verifier": "CONFIRMED",
         "segment": "A", "key_phone": "1-555-0101", "hit_a": "1",
         "hit_b": "0", "reference_snapshot": "v1", "model_version": "m1"},
        {"source_id": "S3", "reference_id": "", "pathway": "no_candidate",
         "status": "NO_MATCH", "score": "", "verifier": "SKIPPED",
         "segment": "B", "key_phone": "", "hit_a": "0", "hit_b": "0"},
        {"source_id": "S4", "reference_id": "", "pathway": "rejected_by_verifier",
         "status": "NO_MATCH", "score": "", "verifier": "REJECTED",
         "segment": "", "hit_a": "0", "hit_b": "1",
         "reference_snapshot": "v1"},
        {"source_id": "S5", "reference_id": "R5", "pathway": "vector_auto",
         "status": "MATCH", "score": "0.75", "segment": "B",
         "key_phone": "5550102"},
        {"source_id": "S6", "reference_id": "R6", "pathway": "vector_auto",
         "status": "MATCH", "score": "0.92", "segment": "A"},
    ]


def run_selftest():
    import tempfile

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    rows = _sample_rows()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "encoded.csv")
        with open(path, "w", encoding="cp932", newline="") as stream:
            stream.write("source_id,reference_id,pathway,status,score,corporate_number,invoice_number\n")
            stream.write("カナ,R1,a,MATCH,.9,123,T123\n")
        encoded = audit_path(path)
        check(encoded["input_encoding"] == "cp932", "cp932 decoding provenance")
        check(read_results(path, encoding="cp932")[0]["source_id"] == "カナ", "explicit decoding")
        check(encoded["identifiers"]["corporate_number"]["matched_invalid"] == 1,
              "invalid matched corporate number")
        check(encoded["identifiers"]["invoice_number"]["matched_invalid"] == 1,
              "invalid matched invoice number")
    weighted = evaluate_plan([
        {"plan_id": "a", "stratum": "unmatched:a", "stratum_population": "90", "stratum_sampled": "1", "label": "0"},
        {"plan_id": "b", "stratum": "unmatched:b", "stratum_population": "10", "stratum_sampled": "1", "label": "1"},
    ])["unmatched_review"]
    check(abs(weighted["reference_absent_weighted"]["estimate"] - .9) < 1e-12,
          "population weighted coverage gap")
    check(abs(weighted["reference_absent_weighted"]["estimate"] +
              weighted["matchable_missed_weighted"]["estimate"] - 1) < 1e-12,
          "weighted shares partition unmatched population")

    a = audit(rows, hub_min=2)
    check(a["n_rows"] == 6, "n_rows")
    check(a["reconciliation"]["status_counts"] == {"MATCH": 4, "NO_MATCH": 2},
          "status counts")
    check(a["reconciliation"]["pathway_counts"]["key_phone"] == 2, "pathway counts")
    check(a["reconciliation"]["no_match_reasons"]["no_candidate"] == 1,
          "no_match reasons")
    check(a["reconciliation"]["verifier_counts"].get("CONFIRMED") == 2,
          "verifier counts")
    check(a["reconciliation"]["verifier_counts"].get("SKIPPED") == 1,
          "verifier skipped count")
    check(a["collisions"]["reference_ids_with_multiple_sources"] == 1, "collisions")
    check(a["collisions"]["rows_in_collisions"] == 2, "rows in collisions")
    check(a["key_hubs"]["hubs"] == 1, "hubs")
    check(a["key_hubs"]["rows_on_hubs"] == 2, "rows on hubs")
    check(a["key_hubs"]["matched_rows_on_hubs"] == 2, "matched on hubs")
    check(a["key_hubs"]["key"] == "key_phone", "key hubs key name")
    check(a["pathway_band"]["vector_auto"]["lt_0.80"] == 1, "band lt_0.80")
    check(a["pathway_band"]["vector_auto"]["0.90_0.95"] == 1, "band 0.90_0.95")
    check(a["pathway_band"]["key_phone"]["ge_0.95"] == 1, "band ge")
    check(a["reconciliation"]["consistent"] is True, "consistent")
    check(a["capture_recapture"]["state"] == "EXPLORATORY", "cr state")
    check(a["capture_recapture"]["n1"] == 2, "cr n1")
    check(a["capture_recapture"]["n2"] == 2, "cr n2")
    check(a["capture_recapture"]["m"] == 1, "cr m")
    check("overstate" in a["capture_recapture"]["note"], "cr note overstate")
    check("never a gate" in a["capture_recapture"]["note"], "cr note gate")
    check(a["provenance"]["present"] == ["reference_snapshot", "model_version"],
          "prov present")
    check(a["provenance"]["missing"] == ["normalizer_version"], "prov missing")
    check(len(a["segments"]) == 3, "segments count")
    check(a["segments"][0]["segment"] == "A" and a["segments"][0]["n"] == 3,
          "segments sorted")
    check(a["segments"][1]["segment"] == "B" and a["segments"][1]["n"] == 2,
          "segments sorted second")
    check(a["segments"][2]["segment"] == "(none)" and a["segments"][2]["n"] == 1,
          "segments none bucket")
    check(a["input_sha256"] is None, "sha none without path")

    bad_rows = [
        {"source_id": "D1", "reference_id": "X", "pathway": "p",
         "status": "MATCH", "score": "0.5"},
        {"source_id": "D1", "reference_id": "Y", "pathway": "p",
         "status": "MATCH", "score": "2.0"},
        {"source_id": "D2", "reference_id": "Z", "pathway": "p",
         "status": "NO_MATCH", "score": ""},
        {"source_id": "D3", "reference_id": "", "pathway": "p",
         "status": "MATCH", "score": ""},
        {"source_id": "D4", "reference_id": "", "pathway": "p",
         "status": "WEIRD", "score": ""},
    ]
    ab = audit(bad_rows)
    probs = " | ".join(ab["reconciliation"]["problems"])
    check("duplicate source_id" in probs, "prob dup")
    check("MATCH row without reference_id" in probs, "prob match no ref")
    check("NO_MATCH row with reference_id" in probs, "prob nomatch ref")
    check("unknown status" in probs, "prob unknown")
    check("score out of range" in probs, "prob score")
    check("status counts do not sum to n_rows" in probs, "prob sum")
    check(ab["reconciliation"]["consistent"] is False, "not consistent")
    check(ab["key_hubs"].get("state") == "NO-DATA", "key hubs no column")
    check(ab["segments"] == [], "segments no column")
    check(ab["capture_recapture"].get("state") == "NO-DATA", "cr no column")

    check(score_band("0.5") == "lt_0.80", "band 0.5")
    check(score_band("0.85") == "0.80_0.90", "band 0.85")
    check(score_band("0.92") == "0.90_0.95", "band 0.92")
    check(score_band("0.99") == "ge_0.95", "band 0.99")
    check(score_band("1.0") == "ge_0.95", "band 1.0")
    check(score_band("") == "no_score", "band empty")
    check(score_band("abc") == "no_score", "band not a number")
    check(normalize_phone("+1 (555) 0101") == "15550101", "phone norm")
    check(normalize_phone("1-555-0101") == "15550101", "phone norm same key")
    check(normalize_phone("\uff11\uff12\uff13") == "123", "phone fullwidth")
    check(normalize_phone("") == "", "phone empty")
    check(normalize_phone("abc") == "", "phone letters only")

    plan = review_plan(rows, margin=0.10, min_n=1, max_n=10, seed=7)
    check(len(plan) == 6, "plan rows")
    check(all(p["plan_id"].startswith("P") for p in plan), "plan_id prefix")
    check(plan[0]["plan_id"] == "P0001", "first plan id")
    plan2 = review_plan(rows, margin=0.10, min_n=1, max_n=10, seed=7)
    check(plan == plan2, "plan deterministic")
    check(plan2[0]["stratum"].startswith("match:")
          or plan2[0]["stratum"].startswith("unmatched:"),
          "plan stratum name")
    check(all(p["label"] == "" for p in plan), "plan labels empty")

    test_rows = []
    for i in range(5):
        test_rows.append({"source_id": "A%d" % (i + 1), "pathway": "a",
                          "status": "MATCH", "score": "0.99"})
    for i in range(5):
        test_rows.append({"source_id": "B%d" % (i + 1), "pathway": "b",
                          "status": "MATCH", "score": "0.99"})
    plan_corr = review_plan(test_rows, margin=0.6, min_n=1, max_n=3, seed=7)
    check(len(plan_corr) == 6, "corr plan rows")
    full_a = ["A1", "A2", "A3", "A4", "A5"]
    full_b = ["B1", "B2", "B3", "B4", "B5"]
    a_chosen = [p["source_id"] for p in plan_corr
                if p["stratum"] == "match:a:ge_0.95"]
    b_chosen = [p["source_id"] for p in plan_corr
                if p["stratum"] == "match:b:ge_0.95"]
    idx_a = set(full_a.index(sid) for sid in a_chosen)
    idx_b = set(full_b.index(sid) for sid in b_chosen)
    check(idx_a != idx_b, "per-stratum seeds differ")

    try:
        review_plan(rows, margin=0)
        check(False, "margin zero should raise")
    except ValueError as e:
        check(str(e) == "margin must be greater than 0", "margin zero msg")
    try:
        review_plan(rows, margin=-0.1)
        check(False, "margin negative should raise")
    except ValueError as e:
        check(str(e) == "margin must be greater than 0", "margin negative msg")
    try:
        review_plan(rows, margin="abc")
        check(False, "margin string should raise")
    except ValueError as e:
        check(str(e) == "margin must be greater than 0", "margin string msg")

    with tempfile.TemporaryDirectory() as td:
        ppath = os.path.join(td, "plan.csv")
        write_plan(plan, ppath)
        header, out = _read_csv_stripped(ppath)
        check(header == PLAN_COLUMNS, "plan header")
        check(len(out) == 6, "plan round trip")
        for r in out:
            if r["stratum"].startswith("match:"):
                r["label"] = "1"
            else:
                r["label"] = "0"
        ev = evaluate_plan(out)
        check("strata" in ev and len(ev["strata"]) == 6, "ev strata")
        check("unmatched_review" in ev, "ev unmatched key")
        check(ev["unmatched_review"]["matchable_missed"] == 0, "ev missed")
        check(ev["unmatched_review"]["reference_absent"] == 2, "ev absent")
        check(ev["unmatched_review"]["sampled"] == 2, "ev sampled")
        check(ev["unmatched_review"]["population"] == 2, "ev unmatched pop")
        check(ev["unlabelled"] == 0, "ev unlabelled")
        check(ev["pathway_review"]["key_phone"]["positive"] == 2, "ev key_phone pos")
        check(ev["pathway_review"]["vector_auto"]["sampled"] == 2, "ev vec sampled")
        check(ev["verifier"]["confirmed_review"] is not None, "ev conf present")
        check(ev["verifier"]["rejected_review"] is not None, "ev rej present")

        check("strata" in ev["pathway_review"]["key_phone"], "ev pathway strata key")
        check(len(ev["pathway_review"]["key_phone"]["strata"]) == 2,
              "ev pathway strata len")
        check(ev["pathway_review"]["key_phone"]["strata"][0]["stratum"] ==
              "match:key_phone:0.80_0.90", "ev pathway strata order")
        check(ev["pathway_review"]["key_phone"]["strata"][1]["stratum"] ==
              "match:key_phone:ge_0.95", "ev pathway strata order ge")
        check(ev["pathway_review"]["key_phone"]["strata"][0]["sampled"] == 1,
              "ev pathway strata sampled")
        check(ev["pathway_review"]["key_phone"]["strata"][0]["positive"] == 1,
              "ev pathway strata positive")
        check(len(ev["unmatched_review"]["strata"]) == 2, "ev unmatched strata len2")
        check(ev["unmatched_review"]["strata"][0]["stratum"] ==
              "unmatched:no_candidate", "ev unmatched strata order")
        check(ev["unmatched_review"]["strata"][1]["stratum"] ==
              "unmatched:rejected_by_verifier", "ev unmatched strata order2")
        check(ev["unmatched_review"]["strata"][0]["matchable_missed"] == 0,
              "ev unmatched missed0")
        check(ev["unmatched_review"]["strata"][0]["reference_absent"] == 1,
              "ev unmatched absent")
        check(ev["unmatched_review"]["matchable_missed_weighted"] is not None,
              "ev weighted not none")
        check(isinstance(ev["unmatched_review"]["matchable_missed_weighted"], dict),
              "ev weighted dict")

        rpath = os.path.join(td, "results.csv")
        with open(rpath, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["source_id", "reference_id", "pathway", "status", "score"])
            for r in rows:
                w.writerow([
                    r.get("source_id", ""), r.get("reference_id", ""),
                    r.get("pathway", ""), r.get("status", ""), r.get("score", ""),
                ])
        aa = audit_path(rpath)
        check(len(aa["input_sha256"]) == 64, "sha256 length")
        rr = read_results(rpath)
        check(len(rr) == 6, "read_results rows")
        check("source_id" in rr[0], "read_results keys")
        check(rr[0]["source_id"] == "S1", "read_results order")

    ev3 = evaluate_plan([
        {"plan_id": "P1", "stratum": "match:p:ge_0.95",
         "stratum_population": "1", "stratum_sampled": "1", "label": "1"},
        {"plan_id": "P2", "stratum": "unmatched:q",
         "stratum_population": "1", "stratum_sampled": "1", "label": ""},
    ])
    check(ev3["unmatched_review"]["matchable_missed_weighted"] is None,
          "ev weighted none")
    check(len(ev3["unmatched_review"]["strata"]) == 1,
          "ev unmatched strata len")

    with tempfile.TemporaryDirectory() as td:
        rpath = os.path.join(td, "bad.csv")
        with open(rpath, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["source_id", "reference_id", "pathway", "status"])
        try:
            read_results(rpath)
            check(False, "should raise missing col")
        except ValueError as e:
            check("missing required column: score" in str(e), "missing col msg")

    try:
        evaluate_plan([{
            "plan_id": "P0001", "stratum": "match:p:ge_0.95",
            "stratum_population": "1", "stratum_sampled": "1", "label": "2",
        }])
        check(False, "should raise bad label")
    except ValueError as e:
        check("P0001" in str(e), "label err has plan_id")

    check(_HERE in sys.path, "module dir on path")

    if failures:
        sys.stdout.write("SELFTEST FAIL\n")
        for f in failures:
            sys.stdout.write(" - " + f + "\n")
        return 1
    sys.stdout.write("SELFTEST PASS\n")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "selftest", False):
        return run_selftest()
    if args.cmd == "audit":
        return _cli_audit(args)
    if args.cmd == "evaluate":
        return _cli_evaluate(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
