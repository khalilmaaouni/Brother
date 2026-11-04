"""MDM and entity resolution claim science pack (gates M7 to M15).

This pack checks data-science claims about master data management and
entity resolution before they reach a business decision. Every gate treats
an unsupported number as NO-DATA, names the offending field on malformed
input, and never raises.

References:
  Bagga and Baldwin 1998 for B-cubed cluster metrics.
  Christen 2012 for blocking reduction ratio and pair completeness.
  Fellegi and Sunter 1969 for m and u likelihood weights in linkage.
  Cochran 1977 for stratified sampling and ratio estimates.
  The PSI bands 0.10 (minor) and 0.25 (major) from industry practice.

All numerics are described to three decimals unless a gate states otherwise.
"""

import mdm_eval


_KNOWN_QUALITY = (
    "completeness",
    "validity",
    "uniqueness",
    "consistency",
    "timeliness",
    "accuracy",
)


def _md(claim):
    if not isinstance(claim, dict):
        return None
    md = claim.get("master_data")
    if not isinstance(md, dict):
        return None
    return md


def _eval(md):
    if not isinstance(md, dict):
        return {}
    ev = md.get("evaluation")
    if not isinstance(ev, dict):
        return {}
    return ev


def _num(x):
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    return None


def _claimed_precision(claim, md):
    v = _num(_eval(md).get("claimed_precision"))
    if v is not None:
        return v
    if isinstance(claim, dict):
        m = claim.get("match")
        if isinstance(m, dict):
            return _num(m.get("precision"))
    return None


def _claimed_recall(claim, md):
    v = _num(_eval(md).get("claimed_recall"))
    if v is not None:
        return v
    if isinstance(claim, dict):
        m = claim.get("match")
        if isinstance(m, dict):
            return _num(m.get("recall"))
    return None


def _get(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


def _f3(x):
    try:
        return "{:.3f}".format(float(x))
    except Exception:
        return str(x)


def _f2(x):
    try:
        return "{:.2f}".format(float(x))
    except Exception:
        return str(x)


def _fmt_or_na(x):
    return _f3(x) if x is not None else "n/a"


def _agree_weight(w):
    """Extract an agreement weight from whatever mdm_eval.fs_weights returns."""
    if w is None:
        return None
    if isinstance(w, dict):
        for k in ("agree", "agree_weight", "w_agree", "match", "w1",
                  "agreement", "w_match"):
            if k in w:
                v = _num(w[k])
                if v is not None:
                    return v
        return None
    if isinstance(w, (list, tuple)):
        if not w:
            return None
        return _num(w[0])
    return _num(w)


def _straddling_stratum(strata, thr):
    for s in strata:
        if not isinstance(s, dict):
            continue
        lo = _num(s.get("score_lo"))
        hi = _num(s.get("score_hi"))
        if lo is not None and hi is not None and lo < thr < hi:
            return s
    return None


def _straddle_detail(s, thr):
    lo = _num(s.get("score_lo"))
    hi = _num(s.get("score_hi"))
    band = "[{}, {}]".format(_f3(lo) if lo is not None else "n/a",
                             _f3(hi) if hi is not None else "n/a")
    return ("review stratum band {} straddles merge_threshold {}; strata must "
            "align to the threshold before precision above it or recall can be "
            "estimated".format(band, _f3(thr)))


# --------------------------------------------------------------------------
# M7
# --------------------------------------------------------------------------
def m7_precision_evidence(claim, Finding):
    gate = "M7.precision_evidence"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    strata = ev.get("review_strata")
    thr = _num(md.get("merge_threshold"))
    if not isinstance(strata, list) or not strata:
        return [Finding(gate, "NO-DATA", "no review_strata evidence")]
    if thr is None:
        return [Finding(gate, "NO-DATA", "no merge_threshold evidence")]
    bad = _straddling_stratum(strata, thr)
    if bad is not None:
        return [Finding(gate, "NO-DATA", _straddle_detail(bad, thr))]
    claimed = _claimed_precision(claim, md)
    if claimed is None:
        return [Finding(gate, "NO-DATA", "no claimed precision")]
    above = []
    for s in strata:
        if not isinstance(s, dict):
            continue
        lo = _num(s.get("score_lo"))
        if lo is not None and lo >= thr:
            above.append(s)
    if not above:
        return [Finding(gate, "NO-DATA",
                        "no review stratum with score_lo at or above merge_threshold")]
    try:
        est = mdm_eval.stratified_estimate(above)
    except ValueError as exc:
        return [Finding(gate, "FAIL",
                        "stratified_estimate rejected malformed strata: " + str(exc))]
    except Exception as exc:
        return [Finding(gate, "FAIL", "stratified_estimate error: " + str(exc))]
    point = _get(est, "estimate", "value", "p")
    lo_ci = _get(est, "lo", "lower", "ci_lo")
    hi_ci = _get(est, "hi", "upper", "ci_hi")
    n = _get(est, "n_sampled", "n", "total")
    if point is None or hi_ci is None:
        return [Finding(gate, "NO-DATA", "stratified_estimate returned no bounds")]
    detail = "claimed {}, review estimate {} [{}, {}] over n={} above {}".format(
        _f3(claimed), _f3(point), _fmt_or_na(lo_ci), _fmt_or_na(hi_ci),
        n if n is not None else "n/a", _f3(thr))
    if claimed > hi_ci:
        return [Finding(gate, "FAIL", detail)]
    return [Finding(gate, "PASS", detail)]


# --------------------------------------------------------------------------
# M8
# --------------------------------------------------------------------------
def m8_recall_evidence(claim, Finding):
    gate = "M8.recall_evidence"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    strata = ev.get("review_strata")
    thr = _num(md.get("merge_threshold"))
    if not isinstance(strata, list) or not strata:
        return [Finding(gate, "NO-DATA", "no review_strata evidence")]
    if thr is None:
        return [Finding(gate, "NO-DATA", "no merge_threshold evidence")]
    bad = _straddling_stratum(strata, thr)
    if bad is not None:
        return [Finding(gate, "NO-DATA", _straddle_detail(bad, thr))]
    claimed = _claimed_recall(claim, md)
    if claimed is None:
        return [Finding(gate, "NO-DATA", "no claimed recall")]
    try:
        rr = mdm_eval.review_recall(strata, thr)
    except ValueError as exc:
        return [Finding(gate, "FAIL",
                        "review_recall rejected malformed strata: " + str(exc))]
    except Exception as exc:
        return [Finding(gate, "FAIL", "review_recall error: " + str(exc))]
    covers = _get(rr, "covers_below_threshold", "covers")
    est = _get(rr, "recall", "estimate", "value")
    if covers is not True:
        return [Finding(gate, "FAIL",
                        "cannot estimate recall: review strata do not sample pairs "
                        "below the merge threshold")]
    if est is None:
        return [Finding(gate, "NO-DATA", "review_recall returned no estimate")]

    blocking = ev.get("blocking")
    end_to_end = None
    pc = None
    if isinstance(blocking, dict):
        try:
            bm = _call_blocking_metrics(blocking)
        except Exception:
            bm = None
        if bm is not None:
            pc = _get(bm, "pair_completeness", "completeness")
            if isinstance(pc, (int, float)) and not isinstance(pc, bool):
                end_to_end = est * float(pc)
            else:
                pc = None

    if end_to_end is not None:
        detail = ("claimed {}, end-to-end estimate {} = review recall {} among "
                  "candidate pairs x blocking pair completeness {} (tolerance 0.05 "
                  "on a point estimate)".format(
                      _f3(claimed), _f3(end_to_end), _f3(est), _f3(pc)))
        if claimed > end_to_end + 0.05:
            return [Finding(gate, "FAIL", detail)]
        return [Finding(gate, "PASS", detail)]

    detail = ("claimed recall {}, review estimate {} with tolerance 0.05 on a "
              "point estimate; recall among candidate pairs only; declare "
              "blocking to estimate end-to-end recall".format(
                  _f3(claimed), _f3(est)))
    if claimed > est + 0.05:
        return [Finding(gate, "FAIL", detail)]
    return [Finding(gate, "PASS", detail)]


# --------------------------------------------------------------------------
# M9
# --------------------------------------------------------------------------
def _call_blocking_metrics(blocking):
    """Call mdm_eval.blocking_metrics tolerating both calling conventions."""
    try:
        return mdm_eval.blocking_metrics(blocking)
    except TypeError:
        return mdm_eval.blocking_metrics(
            blocking.get("n_records"),
            blocking.get("n_candidate_pairs"),
            blocking.get("n_true_pairs"),
            blocking.get("n_true_pairs_in_candidates"))


def m9_blocking_ceiling(claim, Finding):
    gate = "M9.blocking_ceiling"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    blocking = ev.get("blocking")
    if not isinstance(blocking, dict):
        return [Finding(gate, "NO-DATA", "no blocking evidence")]
    try:
        bm = _call_blocking_metrics(blocking)
    except ValueError as exc:
        return [Finding(gate, "FAIL",
                        "blocking numbers are inconsistent so blocking recall "
                        "cannot be trusted: " + str(exc))]
    except Exception as exc:
        return [Finding(gate, "FAIL",
                        "blocking_metrics error: " + str(exc))]
    pc = _get(bm, "pair_completeness", "completeness")
    rr = _get(bm, "reduction_ratio", "reduction")
    if pc is None:
        return [Finding(gate, "NO-DATA",
                        "blocking_metrics returned no pair completeness")]
    claimed = _claimed_recall(claim, md)
    detail = "reduction ratio {}, pair completeness {}".format(
        _f3(rr) if rr is not None else "n/a", _f3(pc))
    if claimed is not None and claimed > pc + 1e-9:
        return [Finding(gate, "FAIL",
                        "end-to-end recall cannot exceed blocking recall: "
                        "claimed {} above pair completeness {}".format(
                            _f3(claimed), _f3(pc)))]
    return [Finding(gate, "PASS", detail)]


# --------------------------------------------------------------------------
# M10
# --------------------------------------------------------------------------
def m10_cluster_metrics(claim, Finding):
    gate = "M10.cluster_metrics"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    gold = ev.get("gold_clusters")
    claimed = _claimed_precision(claim, md)
    if not isinstance(gold, dict):
        return [Finding(gate, "NO-DATA", "no gold_clusters evidence")]
    if claimed is None:
        return [Finding(gate, "NO-DATA", "no claimed precision")]
    pred = gold.get("pred")
    true = gold.get("true")
    if not isinstance(pred, list) or not isinstance(true, list):
        return [Finding(gate, "NO-DATA", "gold_clusters is missing pred or true")]
    basis = ev.get("metric_basis", "pairwise")
    if basis not in ("pairwise", "bcubed"):
        basis = "pairwise"
    try:
        pm = mdm_eval.pairwise_metrics(pred, true)
        bc = mdm_eval.bcubed(pred, true)
    except ValueError as exc:
        return [Finding(gate, "FAIL",
                        "gold cluster metrics rejected malformed clusters: " + str(exc))]
    except Exception as exc:
        return [Finding(gate, "FAIL",
                        "gold cluster metrics error: " + str(exc))]
    p_pw = _get(pm, "precision")
    p_bc = _get(bc, "precision")
    detail = "claimed {}, pairwise precision {}, bcubed precision {}, basis {}".format(
        _f3(claimed), _fmt_or_na(p_pw), _fmt_or_na(p_bc), basis)
    if basis == "pairwise":
        if p_pw is None:
            return [Finding(gate, "NO-DATA",
                            "the gold subset holds no predicted pairs, so pairwise "
                            "precision is undefined; " + detail)]
        gold_val = p_pw
    else:
        if p_bc is None:
            return [Finding(gate, "NO-DATA",
                            "bcubed precision is undefined for the declared basis, "
                            "so the gold subset pairs are not scoreable; " + detail)]
        gold_val = p_bc
    if claimed > gold_val + 0.05:
        return [Finding(gate, "FAIL", detail)]
    return [Finding(gate, "PASS", detail)]


# --------------------------------------------------------------------------
# M11
# --------------------------------------------------------------------------
def m11_overmerge(claim, Finding):
    gate = "M11.overmerge"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    sizes = ev.get("cluster_sizes")
    if not isinstance(sizes, list) or not sizes:
        return [Finding(gate, "NO-DATA", "no cluster_sizes evidence")]
    try:
        prof = mdm_eval.cluster_size_profile(sizes)
    except Exception as exc:
        return [Finding(gate, "FAIL", "cluster_size_profile error: " + str(exc))]
    giant = _get(prof, "giant_clusters", "giants")
    max_size = _get(prof, "max_size", "max")
    reviewed = ev.get("giant_clusters_reviewed")
    if giant is None:
        return [Finding(gate, "NO-DATA", "cluster profile returned no giants count")]
    if isinstance(giant, (int, float)) and not isinstance(giant, bool) and giant > 0:
        if reviewed is not True:
            return [Finding(gate, "FAIL",
                            "{} giant cluster(s), max size {}, transitive closure "
                            "chaining is the usual cause".format(giant, max_size))]
    return [Finding(gate, "PASS",
                    "{} giant cluster(s), max size {}".format(giant, max_size))]


# --------------------------------------------------------------------------
# M12
# --------------------------------------------------------------------------
def _fs_weights_map(params):
    """Return a mapping field -> raw weight result across calling conventions."""
    try:
        return mdm_eval.fs_weights(params)
    except TypeError:
        pass
    out = {}
    for p in params:
        if not isinstance(p, dict):
            continue
        field = p.get("field", "?")
        out[field] = mdm_eval.fs_weights(p.get("m"), p.get("u"))
    return out


def m12_fs_parameters(claim, Finding):
    gate = "M12.fs_parameters"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    params = ev.get("fs_parameters")
    if not isinstance(params, list) or not params:
        return [Finding(gate, "NO-DATA", "no fs_parameters evidence")]
    bad = []
    for p in params:
        if not isinstance(p, dict):
            bad.append("entry not a dict")
            continue
        f = p.get("field", "?")
        m = _num(p.get("m"))
        u = _num(p.get("u"))
        ok = True
        if m is None or m <= 0.0 or m >= 1.0:
            bad.append("{} m={} outside (0,1)".format(f, p.get("m")))
            ok = False
        if u is None or u <= 0.0 or u >= 1.0:
            bad.append("{} u={} outside (0,1)".format(f, p.get("u")))
            ok = False
        if ok and m <= u:
            bad.append("{} m={} <= u={} so agreement would count against a match".format(
                f, m, u))
    if bad:
        return [Finding(gate, "FAIL",
                        "invalid Fellegi-Sunter parameters: " + "; ".join(bad))]
    try:
        weights = _fs_weights_map(params)
    except Exception as exc:
        return [Finding(gate, "FAIL", "fs_weights error: " + str(exc))]
    best_field = None
    best_w = None
    if isinstance(weights, dict):
        for f, w in weights.items():
            aw = _agree_weight(w)
            if aw is None:
                continue
            if best_w is None or aw > best_w:
                best_w = aw
                best_field = f
    if best_field is None:
        return [Finding(gate, "PASS",
                        "{} field(s) with m above u in (0,1)".format(len(params)))]
    return [Finding(gate, "PASS",
                    "{} field(s) validated, strongest field {} with agree weight "
                    "{}".format(len(params), best_field, _f2(best_w)))]


# --------------------------------------------------------------------------
# M13
# --------------------------------------------------------------------------
def m13_score_drift(claim, Finding):
    gate = "M13.score_drift"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    sd = ev.get("score_drift")
    if not isinstance(sd, dict):
        return [Finding(gate, "NO-DATA", "no score_drift evidence")]
    ref = sd.get("reference")
    cur = sd.get("current")
    if not isinstance(ref, list) or not isinstance(cur, list) or not ref or not cur:
        return [Finding(gate, "FAIL",
                        "score_drift reference or current is malformed")]
    try:
        val = mdm_eval.psi(ref, cur)
    except Exception as exc:
        return [Finding(gate, "FAIL", "psi error: " + str(exc))]
    try:
        band = mdm_eval.psi_band(val)
    except Exception as exc:
        return [Finding(gate, "FAIL", "psi_band error: " + str(exc))]
    ack = sd.get("acknowledged")
    detail = "psi {:.3f}, band {}".format(float(val), band)
    if str(band).lower() == "major" and ack is not True:
        return [Finding(gate, "FAIL", detail + ", major drift not acknowledged")]
    return [Finding(gate, "PASS", detail)]


# --------------------------------------------------------------------------
# M14
# --------------------------------------------------------------------------
def m14_quality_dimensions(claim, Finding):
    gate = "M14.quality_dimensions"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    q = ev.get("quality")
    if not isinstance(q, dict) or not q:
        return [Finding(gate, "NO-DATA", "no quality evidence")]
    problems = []
    for name, spec in q.items():
        if name not in _KNOWN_QUALITY:
            problems.append("unknown dimension " + str(name))
            continue
        if not isinstance(spec, dict):
            problems.append("{} dimension not a dict".format(name))
            continue
        v = _num(spec.get("value"))
        t = _num(spec.get("threshold"))
        if v is None or v < 0.0 or v > 1.0:
            problems.append("{} value {} outside [0,1]".format(name, spec.get("value")))
        if t is None or t < 0.0 or t > 1.0:
            problems.append("{} threshold {} outside [0,1]".format(
                name, spec.get("threshold")))
        if v is not None and t is not None and 0.0 <= v <= 1.0 and 0.0 <= t <= 1.0:
            if v < t:
                problems.append("{} value {} below threshold {}".format(
                    name, _f3(v), _f3(t)))
        if name == "accuracy":
            method = spec.get("method")
            if not isinstance(method, str) or not method.strip():
                problems.append("accuracy without a non-empty method, accuracy "
                                "needs a reference source and a rule cannot "
                                "measure it")
    if problems:
        return [Finding(gate, "FAIL", "; ".join(problems))]
    return [Finding(gate, "PASS",
                    "dimensions checked: " + ", ".join(sorted(q.keys())))]


# --------------------------------------------------------------------------
# M15
# --------------------------------------------------------------------------
def m15_golden_lineage(claim, Finding):
    gate = "M15.golden_lineage"
    md = _md(claim)
    if md is None:
        return []
    ev = _eval(md)
    gr = ev.get("golden_record")
    if not isinstance(gr, list) or not gr:
        return [Finding(gate, "NO-DATA", "no golden_record evidence")]
    problems = []
    max_cr = None
    max_attr = None
    for item in gr:
        if not isinstance(item, dict):
            problems.append("golden_record entry not a dict")
            continue
        attr = item.get("attribute", "?")
        rule = item.get("rule")
        src = item.get("source_system")
        if not isinstance(rule, str) or not rule.strip():
            problems.append("{} missing rule".format(attr))
        if not isinstance(src, str) or not src.strip():
            problems.append("{} missing source_system".format(attr))
        cr = _num(item.get("conflict_rate"))
        if cr is None or cr < 0.0 or cr > 1.0:
            problems.append("{} conflict_rate {} outside [0,1]".format(
                attr, item.get("conflict_rate")))
        else:
            if max_cr is None or cr > max_cr:
                max_cr = cr
                max_attr = attr
    if problems:
        return [Finding(gate, "FAIL", "; ".join(problems))]
    if max_attr is None:
        return [Finding(gate, "PASS",
                        "{} golden record attributes validated".format(len(gr)))]
    return [Finding(gate, "PASS",
                    "{} golden record attributes validated, highest conflict_rate "
                    "{} on {}".format(len(gr), _f3(max_cr), max_attr))]


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
def register(packs_module, Finding):
    packs_module.register("MASTER_DATA", m7_precision_evidence)
    packs_module.register("MASTER_DATA", m8_recall_evidence)
    packs_module.register("MASTER_DATA", m9_blocking_ceiling)
    packs_module.register("MASTER_DATA", m10_cluster_metrics)
    packs_module.register("MASTER_DATA", m11_overmerge)
    packs_module.register("MASTER_DATA", m12_fs_parameters)
    packs_module.register("MASTER_DATA", m13_score_drift)
    packs_module.register("MASTER_DATA", m14_quality_dimensions)
    packs_module.register("MASTER_DATA", m15_golden_lineage)


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------
def selftest(expect):
    class Finding(object):
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail
        def __repr__(self):
            return "Finding({!r}, {!r}, {!r})".format(
                self.gate, self.verdict, self.detail)

    import math as _math

    def strat_est(strata):
        for s in strata:
            pop = float(s.get("population", 0) or 0)
            smp = float(s.get("sampled", 0) or 0)
            pos = float(s.get("positive", 0) or 0)
            if smp > pop:
                raise ValueError("stratum sampled above population")
            if pos > smp:
                raise ValueError("stratum positive above sampled")
        tot = 0.0
        pop_sum = 0.0
        for s in strata:
            pop = float(s.get("population", 0) or 0)
            smp = float(s.get("sampled", 0) or 0)
            pos = float(s.get("positive", 0) or 0)
            pop_sum += pop
            if smp > 0:
                tot += pop * (pos / smp)
        est = (tot / pop_sum) if pop_sum else 0.0
        return {"estimate": est,
                "lo": max(0.0, est - 0.02),
                "hi": min(1.0, est + 0.02),
                "n_sampled": int(sum(float(s.get("sampled", 0) or 0) for s in strata))}

    def rev_rec(strata, thr):
        for s in strata:
            pop = float(s.get("population", 0) or 0)
            smp = float(s.get("sampled", 0) or 0)
            pos = float(s.get("positive", 0) or 0)
            if smp > pop:
                raise ValueError("stratum sampled above population")
            if pos > smp:
                raise ValueError("stratum positive above sampled")
        covers = any(float(s.get("score_hi", 0) or 0) < float(thr) for s in strata)
        return {"covers_below_threshold": covers, "recall": 0.90}

    def blocking_m(b):
        n = b.get("n_records")
        cp = b.get("n_candidate_pairs")
        tp = b.get("n_true_pairs")
        tic = b.get("n_true_pairs_in_candidates")
        for v in (n, cp, tp, tic):
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError("blocking field missing or non-numeric")
        total = n * (n - 1) / 2.0
        if cp > total:
            raise ValueError("n_candidate_pairs exceeds the total record pairs")
        if tic > tp:
            raise ValueError("n_true_pairs_in_candidates exceeds n_true_pairs")
        rr = (1.0 - cp / total) if total else 0.0
        pc = (tic / tp) if tp else 0.0
        return {"reduction_ratio": rr, "pair_completeness": pc}

    def _pairs(cl):
        s = set()
        for c in cl:
            ids = list(c)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    s.add((a, b) if a <= b else (b, a))
        return s

    def pw(pred, true):
        pids = set(x for c in pred for x in c)
        tids = set(x for c in true for x in c)
        if pids != tids:
            raise ValueError("record ids do not match")
        P = _pairs(pred)
        T = _pairs(true)
        inter = len(P & T)
        prec = (inter / len(P)) if P else None
        rec = (inter / len(T)) if T else None
        return {"precision": prec, "recall": rec, "f1": 0.0}

    def bc(pred, true):
        if pred == [["a", "b"], ["b", "c"]] and true == [["a", "b", "c"]]:
            raise ValueError("overlapping clusters")
        if pred == [["a", "b"], ["c"]] and true == [["a", "b"], ["c"]]:
            return {"precision": None, "recall": 1.0, "f1": None}
        return {"precision": 0.90, "recall": 0.90, "f1": 0.90}

    def psi_f(ref, cur):
        return 0.05 if ref[0] < 0 else 0.30

    def psi_b(p):
        if p < 0.10:
            return "minor"
        if p < 0.25:
            return "moderate"
        return "major"

    def fs_w(params):
        out = {}
        for p in params:
            f = p.get("field")
            m = float(p.get("m", 0.0))
            u = float(p.get("u", 1.0))
            out[f] = {"agree": _math.log2(m / u) if u > 0 else 0.0}
        return out

    def fs_w_pair(m, u):
        m = float(m)
        u = float(u)
        return {"agree": _math.log2(m / u) if u > 0 else 0.0}

    def cluster_prof(sizes):
        vals = [int(s) for s in sizes]
        giant = sum(1 for s in vals if s >= 50)
        return {"giant_clusters": giant, "max_size": max(vals) if vals else 0}

    stubs = {
        "stratified_estimate": strat_est,
        "review_recall": rev_rec,
        "blocking_metrics": blocking_m,
        "pairwise_metrics": pw,
        "bcubed": bc,
        "psi": psi_f,
        "psi_band": psi_b,
        "fs_weights": fs_w,
        "cluster_size_profile": cluster_prof,
    }
    saved = {}
    for name in stubs:
        saved[name] = getattr(mdm_eval, name, None)
    for name, fn in stubs.items():
        setattr(mdm_eval, name, fn)

    ok = True
    try:
        # ---------------- M7 ----------------
        ok &= expect(m7_precision_evidence({}, Finding) == [],
                     "M7 empty claim returns []")
        ok &= expect(m7_precision_evidence({"foo": 1}, Finding) == [],
                     "M7 no master_data returns []")
        c = {"master_data": {"merge_threshold": 0.8,
                             "evaluation": {"claimed_precision": 0.9}}}
        ok &= expect(m7_precision_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M7 missing review_strata is NO-DATA")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.9,
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 100, "sampled": 50, "positive": 45}]}}}
        ok &= expect(m7_precision_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M7 missing merge_threshold is NO-DATA")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 100, "sampled": 50, "positive": 45}]}}}
        ok &= expect(m7_precision_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M7 missing claimed precision is NO-DATA")
        c = {"master_data": {"merge_threshold": 0.95, "evaluation": {
            "claimed_precision": 0.9,
            "review_strata": [{"score_lo": 0.5, "score_hi": 0.9,
                               "population": 100, "sampled": 50, "positive": 45}]}}}
        ok &= expect(m7_precision_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M7 no stratum above threshold is NO-DATA")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_precision": 0.9,
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 200, "sampled": 200, "positive": 180}]}}}
        r = m7_precision_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "PASS",
                     "M7 claimed within interval is PASS")
        ok &= expect("over n=200 above 0.800" in r.detail,
                     "M7 detail shows real sampled count above threshold")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_precision": 0.99,
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 200, "sampled": 200, "positive": 180}]}}}
        ok &= expect(m7_precision_evidence(c, Finding)[0].verdict == "FAIL",
                     "M7 claimed above interval is FAIL")
        c = {"master_data": {"merge_threshold": 0.8,
                             "evaluation": {"review_strata": [
                                 {"score_lo": 0.9, "score_hi": 1.0,
                                  "population": 100, "sampled": 50,
                                  "positive": 45}]}},
             "match": {"precision": 0.95}}
        ok &= expect(m7_precision_evidence(c, Finding)[0].verdict in ("PASS", "FAIL"),
                     "M7 falls back to match.precision")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_precision": 0.9,
            "review_strata": [{"score_lo": 0.7, "score_hi": 0.9,
                               "population": 100, "sampled": 50, "positive": 45}]}}}
        r = m7_precision_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "NO-DATA" and "straddle" in r.detail,
                     "M7 straddling stratum is NO-DATA with straddle detail")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_precision": 0.9,
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 10, "sampled": 20, "positive": 10}]}}}
        r = m7_precision_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "sampled above population" in r.detail,
                     "M7 malformed strata is FAIL naming the error")

        # ---------------- M8 ----------------
        ok &= expect(m8_recall_evidence({}, Finding) == [],
                     "M8 empty claim returns []")
        c = {"master_data": {"evaluation": {"claimed_recall": 0.9}}}
        ok &= expect(m8_recall_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M8 missing review_strata is NO-DATA")
        c = {"master_data": {"evaluation": {
            "claimed_recall": 0.9,
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 10, "sampled": 10, "positive": 9}]}}}
        ok &= expect(m8_recall_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M8 missing merge_threshold is NO-DATA")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "review_strata": [{"score_lo": 0.9, "score_hi": 1.0,
                               "population": 10, "sampled": 10, "positive": 9}]}}}
        ok &= expect(m8_recall_evidence(c, Finding)[0].verdict == "NO-DATA",
                     "M8 missing claimed recall is NO-DATA")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.8,
            "review_strata": [{"score_lo": 0.9, "score_hi": 0.95,
                               "population": 10, "sampled": 10, "positive": 9}]}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "cannot estimate recall" in r.detail
                     and "below the merge threshold" in r.detail,
                     "M8 without below-threshold coverage is FAIL")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.8,
            "review_strata": [{"score_lo": 0.2, "score_hi": 0.5,
                               "population": 10, "sampled": 10, "positive": 9}]}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "PASS",
                     "M8 within tolerance is PASS")
        ok &= expect("recall among candidate pairs only" in r.detail
                     and "declare blocking to estimate end-to-end recall" in r.detail,
                     "M8 without blocking states candidate-pair only")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.85,
            "review_strata": [{"score_lo": 0.2, "score_hi": 0.5,
                               "population": 10, "sampled": 10, "positive": 9}],
            "blocking": {"n_records": 100, "n_candidate_pairs": 50,
                         "n_true_pairs": 10, "n_true_pairs_in_candidates": 8}}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "pair completeness" in r.detail
                     and "end-to-end estimate" in r.detail,
                     "M8 with valid blocking uses end-to-end estimate")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.8,
            "review_strata": [{"score_lo": 0.2, "score_hi": 0.5,
                               "population": 10, "sampled": 10, "positive": 9}],
            "blocking": {"n_records": 100, "n_candidate_pairs": 50,
                         "n_true_pairs": 10, "n_true_pairs_in_candidates": 11}}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "PASS" and "recall among candidate pairs only" in r.detail
                     and "pair completeness" not in r.detail,
                     "M8 invalid blocking falls back to candidate-pair only")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.99,
            "review_strata": [{"score_lo": 0.2, "score_hi": 0.5,
                               "population": 10, "sampled": 10, "positive": 9}]}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "0.05" in r.detail,
                     "M8 above tolerance mentions 0.05")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.8,
            "review_strata": [{"score_lo": 0.7, "score_hi": 0.9,
                               "population": 100, "sampled": 50, "positive": 45}]}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "NO-DATA" and "straddle" in r.detail,
                     "M8 straddling stratum is NO-DATA with straddle detail")
        c = {"master_data": {"merge_threshold": 0.8, "evaluation": {
            "claimed_recall": 0.8,
            "review_strata": [{"score_lo": 0.2, "score_hi": 0.5,
                               "population": 10, "sampled": 20, "positive": 5}]}}}
        r = m8_recall_evidence(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "sampled above population" in r.detail,
                     "M8 malformed strata is FAIL naming the error")

        # ---------------- M9 ----------------
        ok &= expect(m9_blocking_ceiling({}, Finding) == [],
                     "M9 empty claim returns []")
        c = {"master_data": {"evaluation": {"claimed_recall": 0.9}}}
        ok &= expect(m9_blocking_ceiling(c, Finding)[0].verdict == "NO-DATA",
                     "M9 missing blocking is NO-DATA")
        c = {"master_data": {"evaluation": {
            "claimed_recall": 0.9,
            "blocking": {"n_records": 1000, "n_candidate_pairs": 100000,
                         "n_true_pairs": 500,
                         "n_true_pairs_in_candidates": 400}}}}
        r = m9_blocking_ceiling(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "blocking recall" in r.detail,
                     "M9 recall above pair completeness is FAIL")
        c = {"master_data": {"evaluation": {
            "blocking": {"n_records": 1000, "n_candidate_pairs": 100000,
                         "n_true_pairs": 500,
                         "n_true_pairs_in_candidates": 400}}}}
        r = m9_blocking_ceiling(c, Finding)[0]
        ok &= expect(r.verdict == "PASS"
                     and "reduction ratio" in r.detail
                     and "pair completeness" in r.detail,
                     "M9 without claimed recall is PASS with ratios in detail")
        c = {"master_data": {"evaluation": {
            "blocking": {"n_records": 10, "n_candidate_pairs": 100,
                         "n_true_pairs": 5,
                         "n_true_pairs_in_candidates": 5}}}}
        r = m9_blocking_ceiling(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL"
                     and "n_candidate_pairs exceeds the total record pairs" in r.detail,
                     "M9 inconsistent blocking is FAIL naming the error")

        # ---------------- M10 ----------------
        ok &= expect(m10_cluster_metrics({}, Finding) == [],
                     "M10 empty claim returns []")
        c = {"master_data": {"evaluation": {"claimed_precision": 0.9}}}
        ok &= expect(m10_cluster_metrics(c, Finding)[0].verdict == "NO-DATA",
                     "M10 missing gold_clusters is NO-DATA")
        c = {"master_data": {"evaluation": {
            "gold_clusters": {"pred": [["a", "b"]], "true": [["a", "b"]]}}}}
        ok &= expect(m10_cluster_metrics(c, Finding)[0].verdict == "NO-DATA",
                     "M10 missing claimed precision is NO-DATA")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.85,
            "gold_clusters": {"pred": [["a", "b", "c"]],
                              "true": [["a", "b", "c"]]}}}}
        r = m10_cluster_metrics(c, Finding)[0]
        ok &= expect(r.verdict == "PASS" and "pairwise precision" in r.detail
                     and "bcubed precision" in r.detail,
                     "M10 PASS shows both precisions")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.9,
            "gold_clusters": {"pred": [["a", "b", "c", "d"]],
                              "true": [["a", "b"], ["c", "d"]]}}}}
        ok &= expect(m10_cluster_metrics(c, Finding)[0].verdict == "FAIL",
                     "M10 claimed above pairwise gold is FAIL")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.9,
            "metric_basis": "bcubed",
            "gold_clusters": {"pred": [["a", "b", "c", "d"]],
                              "true": [["a", "b"], ["c", "d"]]}}}}
        ok &= expect(m10_cluster_metrics(c, Finding)[0].verdict == "PASS",
                     "M10 bcubed basis passes")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.9,
            "gold_clusters": {"pred": [["a", "b"]], "true": [["c"]]}}}}
        r = m10_cluster_metrics(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "record ids do not match" in r.detail,
                     "M10 mismatched record ids is FAIL naming the error")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.9,
            "gold_clusters": {"pred": [["a"], ["b"]], "true": [["a", "b"]]}}}}
        r = m10_cluster_metrics(c, Finding)[0]
        ok &= expect(r.verdict == "NO-DATA" and "pairs" in r.detail,
                     "M10 pairwise basis with no predicted pairs is NO-DATA mentioning pairs")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.95,
            "metric_basis": "pairwise",
            "gold_clusters": {"pred": [["a", "b"], ["c"]],
                              "true": [["a", "b"], ["c"]]}}}}
        r = m10_cluster_metrics(c, Finding)[0]
        ok &= expect(r.verdict == "PASS" and "n/a" in r.detail,
                     "M10 pairwise basis ignores missing bcubed precision")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.95,
            "metric_basis": "bcubed",
            "gold_clusters": {"pred": [["a", "b"], ["c"]],
                              "true": [["a", "b"], ["c"]]}}}}
        r = m10_cluster_metrics(c, Finding)[0]
        ok &= expect(r.verdict == "NO-DATA" and "undefined" in r.detail,
                     "M10 bcubed basis with undefined precision is NO-DATA")
        c = {"master_data": {"evaluation": {
            "claimed_precision": 0.9,
            "gold_clusters": {"pred": [["a", "b"], ["b", "c"]],
                              "true": [["a", "b", "c"]]}}}}
        r = m10_cluster_metrics(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "overlapping clusters" in r.detail,
                     "M10 overlapping clusters is FAIL naming the error")

        # ---------------- M11 ----------------
        ok &= expect(m11_overmerge({}, Finding) == [],
                     "M11 empty claim returns []")
        c = {"master_data": {"evaluation": {}}}
        ok &= expect(m11_overmerge(c, Finding)[0].verdict == "NO-DATA",
                     "M11 missing cluster_sizes is NO-DATA")
        c = {"master_data": {"evaluation": {
            "cluster_sizes": [3, 4, 100]}}}
        r = m11_overmerge(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "transitive closure chaining" in r.detail,
                     "M11 unreviewed giant is FAIL")
        c = {"master_data": {"evaluation": {
            "cluster_sizes": [3, 4, 100],
            "giant_clusters_reviewed": True}}}
        ok &= expect(m11_overmerge(c, Finding)[0].verdict == "PASS",
                     "M11 reviewed giant is PASS")
        c = {"master_data": {"evaluation": {"cluster_sizes": [3, 4, 5]}}}
        ok &= expect(m11_overmerge(c, Finding)[0].verdict == "PASS",
                     "M11 no giants is PASS")

        # ---------------- M12 ----------------
        ok &= expect(m12_fs_parameters({}, Finding) == [],
                     "M12 empty claim returns []")
        c = {"master_data": {"evaluation": {}}}
        ok &= expect(m12_fs_parameters(c, Finding)[0].verdict == "NO-DATA",
                     "M12 missing fs_parameters is NO-DATA")
        c = {"master_data": {"evaluation": {"fs_parameters": []}}}
        ok &= expect(m12_fs_parameters(c, Finding)[0].verdict == "NO-DATA",
                     "M12 empty fs_parameters is NO-DATA")
        c = {"master_data": {"evaluation": {
            "fs_parameters": [{"field": "name", "m": 0.5, "u": 0.5}]}}}
        r = m12_fs_parameters(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "name" in r.detail,
                     "M12 m <= u names the field")
        c = {"master_data": {"evaluation": {
            "fs_parameters": [{"field": "name", "m": 1.5, "u": 0.1}]}}}
        r = m12_fs_parameters(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "name" in r.detail,
                     "M12 m outside (0,1) is FAIL")
        c = {"master_data": {"evaluation": {
            "fs_parameters": [{"field": "name", "m": 0.3, "u": 1.2}]}}}
        r = m12_fs_parameters(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "name" in r.detail,
                     "M12 u outside (0,1) is FAIL")
        c = {"master_data": {"evaluation": {"fs_parameters": [
            {"field": "name", "m": 0.9, "u": 0.1},
            {"field": "addr", "m": 0.7, "u": 0.2}]}}}
        r = m12_fs_parameters(c, Finding)[0]
        ok &= expect(r.verdict == "PASS" and "name" in r.detail
                     and "3.17" in r.detail,
                     "M12 valid params passes with strongest field and weight")

        # ---------------- M13 ----------------
        ok &= expect(m13_score_drift({}, Finding) == [],
                     "M13 empty claim returns []")
        c = {"master_data": {"evaluation": {}}}
        ok &= expect(m13_score_drift(c, Finding)[0].verdict == "NO-DATA",
                     "M13 missing score_drift is NO-DATA")
        c = {"master_data": {"evaluation": {"score_drift": {"reference": [1]}}}}
        r = m13_score_drift(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "malformed" in r.detail,
                     "M13 malformed lists is FAIL")
        c = {"master_data": {"evaluation": {"score_drift": {
            "reference": [1], "current": [1]}}}}
        r = m13_score_drift(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "major" in r.detail,
                     "M13 unacknowledged major drift is FAIL")
        c = {"master_data": {"evaluation": {"score_drift": {
            "reference": [1], "current": [1], "acknowledged": True}}}}
        ok &= expect(m13_score_drift(c, Finding)[0].verdict == "PASS",
                     "M13 acknowledged major drift is PASS")
        c = {"master_data": {"evaluation": {"score_drift": {
            "reference": [-1], "current": [-1]}}}}
        r = m13_score_drift(c, Finding)[0]
        ok &= expect(r.verdict == "PASS" and "minor" in r.detail,
                     "M13 minor drift is PASS")

        # ---------------- M14 ----------------
        ok &= expect(m14_quality_dimensions({}, Finding) == [],
                     "M14 empty claim returns []")
        c = {"master_data": {"evaluation": {}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "NO-DATA",
                     "M14 missing quality is NO-DATA")
        c = {"master_data": {"evaluation": {"quality": {}}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "NO-DATA",
                     "M14 empty quality is NO-DATA")
        c = {"master_data": {"evaluation": {"quality": {
            "weird": {"value": 0.9, "threshold": 0.8, "method": "scan"}}}}}
        r = m14_quality_dimensions(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "weird" in r.detail,
                     "M14 unknown dimension is FAIL")
        c = {"master_data": {"evaluation": {"quality": {
            "completeness": {"value": 0.5, "threshold": 0.8, "method": "scan"}}}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "FAIL",
                     "M14 value below threshold is FAIL")
        c = {"master_data": {"evaluation": {"quality": {
            "completeness": {"value": 1.5, "threshold": 0.8, "method": "scan"}}}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "FAIL",
                     "M14 value outside [0,1] is FAIL")
        c = {"master_data": {"evaluation": {"quality": {
            "accuracy": {"value": 0.9, "threshold": 0.8, "method": ""}}}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "FAIL",
                     "M14 accuracy without method is FAIL")
        c = {"master_data": {"evaluation": {"quality": {
            "completeness": {"value": 0.95, "threshold": 0.8, "method": "scan"}}}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "PASS",
                     "M14 valid dimension is PASS")
        c = {"master_data": {"evaluation": {"quality": {
            "accuracy": {"value": 0.95, "threshold": 0.8,
                         "method": "source registry"}}}}}
        ok &= expect(m14_quality_dimensions(c, Finding)[0].verdict == "PASS",
                     "M14 accuracy with method is PASS")

        # ---------------- M15 ----------------
        ok &= expect(m15_golden_lineage({}, Finding) == [],
                     "M15 empty claim returns []")
        c = {"master_data": {"evaluation": {}}}
        ok &= expect(m15_golden_lineage(c, Finding)[0].verdict == "NO-DATA",
                     "M15 missing golden_record is NO-DATA")
        c = {"master_data": {"evaluation": {"golden_record": []}}}
        ok &= expect(m15_golden_lineage(c, Finding)[0].verdict == "NO-DATA",
                     "M15 empty golden_record is NO-DATA")
        c = {"master_data": {"evaluation": {"golden_record": [
            {"attribute": "name", "rule": "", "source_system": "crm",
             "conflict_rate": 0.1}]}}}
        r = m15_golden_lineage(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "name" in r.detail
                     and "rule" in r.detail,
                     "M15 missing rule is FAIL")
        c = {"master_data": {"evaluation": {"golden_record": [
            {"attribute": "name", "rule": "pick longest", "source_system": "",
             "conflict_rate": 0.1}]}}}
        r = m15_golden_lineage(c, Finding)[0]
        ok &= expect(r.verdict == "FAIL" and "source_system" in r.detail,
                     "M15 missing source_system is FAIL")
        c = {"master_data": {"evaluation": {"golden_record": [
            {"attribute": "name", "rule": "pick longest", "source_system": "crm",
             "conflict_rate": 1.5}]}}}
        ok &= expect(m15_golden_lineage(c, Finding)[0].verdict == "FAIL",
                     "M15 conflict_rate outside [0,1] is FAIL")
        c = {"master_data": {"evaluation": {"golden_record": [
            {"attribute": "name", "rule": "pick longest", "source_system": "crm",
             "conflict_rate": 0.2},
            {"attribute": "addr", "rule": "most recent", "source_system": "erp",
             "conflict_rate": 0.7}]}}}
        r = m15_golden_lineage(c, Finding)[0]
        ok &= expect(r.verdict == "PASS" and "addr" in r.detail
                     and "0.700" in r.detail,
                     "M15 valid golden record names highest conflict")

        # ---------------- register ----------------
        class FakePacks(object):
            def __init__(self):
                self.calls = []
            def register(self, pack, fn):
                self.calls.append((pack, fn))

        fp = FakePacks()
        register(fp, Finding)
        names = [fn.__name__ for _, fn in fp.calls]
        ok &= expect(len(fp.calls) == 9, "register calls nine gates")
        ok &= expect(all(p == "MASTER_DATA" for p, _ in fp.calls),
                     "register uses the MASTER_DATA pack id")
        ok &= expect(names == [
            "m7_precision_evidence", "m8_recall_evidence", "m9_blocking_ceiling",
            "m10_cluster_metrics", "m11_overmerge", "m12_fs_parameters",
            "m13_score_drift", "m14_quality_dimensions", "m15_golden_lineage"],
            "register order is M7 to M15")
    finally:
        for name, orig in saved.items():
            if orig is None:
                try:
                    delattr(mdm_eval, name)
                except Exception:
                    pass
            else:
                setattr(mdm_eval, name, orig)

    return ok


if __name__ == "__main__":
    import sys

    class _Finding(object):
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    def _expect(cond, msg):
        if not cond:
            sys.stderr.write("FAIL: " + str(msg) + "\n")
        return bool(cond)

    if selftest(_expect):
        print("SELFTEST PASS")
        sys.exit(0)
    else:
        sys.exit(1)
