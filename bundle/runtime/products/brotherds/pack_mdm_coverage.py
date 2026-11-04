"""Master data coverage gates M27 through M30.

Applicability: these four gates apply only when claim["master_data"] is a dict containing an "audit" key whose value is a dict; otherwise the registered function returns [] so the gates do not apply, print nothing, and cannot turn a claim about something else into NO-DATA.
"""
import os
import sys
import math
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdm_eval
import mdm_audit

GATE27 = "M27.segment_disparity"
GATE28 = "M28.coverage_split"
GATE29 = "M29.hierarchy_integrity"
GATE30 = "M30.agreement_bound"


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x)))


def _rate(x):
    return _is_num(x) and 0.0 <= float(x) <= 1.0


def _fmt(x):
    return "%.3f" % x if x is not None else "NO-DATA"


def _audit_dict(md):
    a = md.get("audit")
    if a is not None and not isinstance(a, dict):
        raise ValueError("audit must be a dict")
    return a or {}


def _evaluation_dict(md):
    e = md.get("evaluation")
    if e is not None and not isinstance(e, dict):
        raise ValueError("evaluation must be a dict")
    return e or {}


def _gate_m27(md):
    A = _audit_dict(md)
    segs = None
    if "segments" in A and A.get("segments") is not None:
        segs = A.get("segments")
    else:
        comp = A.get("computed")
        if comp is not None:
            if not isinstance(comp, dict):
                return "FAIL", "computed"
            if "segments" in comp and comp.get("segments") is not None:
                segs = comp.get("segments")

    if segs is None or (isinstance(segs, list) and len(segs) == 0):
        return "NO-DATA", "no segment breakdown"
    if not isinstance(segs, list):
        return "FAIL", "segments"

    normalized = []
    for entry in segs:
        if not isinstance(entry, dict):
            return "FAIL", "segment entry"
        seg = entry.get("segment")
        if not isinstance(seg, str):
            return "FAIL", "segment field"
        n = entry.get("n")
        if not _is_int(n) or n < 0:
            return "FAIL", "segment %s field n" % seg
        matched = entry.get("matched")
        if not _is_int(matched) or matched < 0 or matched > n:
            return "FAIL", "segment %s field matched" % seg
        normalized.append((seg, n, matched))

    gap = A.get("material_gap", 0.10)
    if not _rate(gap):
        return "FAIL", "material_gap"
    min_n = A.get("min_segment_n", 30)
    if not _is_int(min_n) or min_n < 0:
        return "FAIL", "min_segment_n"

    threshold = max(min_n, 1)
    eligible = [x for x in normalized if x[1] >= threshold]
    small = [x for x in normalized if x[1] < threshold]
    k = len(eligible)
    if k < 2:
        return "NO-DATA", "fewer than two segments with at least min_n records"

    z = statistics.NormalDist().inv_cdf(1 - 0.05 / (2 * k))
    total_m = sum(x[2] for x in normalized)
    total_n = sum(x[1] for x in normalized)
    below = []
    above = []

    for seg, n1, m1 in eligible:
        m2 = total_m - m1
        n2 = total_n - n1
        if n2 == 0:
            continue
        p1 = m1 / n1
        p2 = m2 / n2
        l1, u1 = mdm_eval.wilson_interval(m1, n1, z)
        l2, u2 = mdm_eval.wilson_interval(m2, n2, z)
        d = p1 - p2
        lower = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
        upper = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
        if upper < -gap:
            below.append(seg)
        if lower > gap:
            above.append(seg)

    parts = []
    parts.append("k eligible segment(s), Bonferroni z = %.3f" % z)
    parts.append("below: " + (", ".join(sorted(below)) if below else "none"))
    parts.append("above: " + (", ".join(sorted(above)) if above else "none"))
    if small:
        parts.append("%d small segment(s) NO-DATA (under min_n)" % len(small))
    detail = "; ".join(parts)

    if below and A.get("segment_disclosure") is not True:
        return "FAIL", "a single headline rate hides segment(s) materially below the rest; " + detail
    return "PASS", detail


def _gate_m28(md):
    A = _audit_dict(md)
    E = _evaluation_dict(md)
    ur = A.get("unmatched_review")
    if ur is None:
        return "NO-DATA", "no labelled sample of unmatched records"
    if not isinstance(ur, dict):
        return "FAIL", "unmatched_review"

    pop = ur.get("population")
    sampled = ur.get("sampled")
    ra = ur.get("reference_absent")
    mm = ur.get("matchable_missed")
    for name, val in (("population", pop), ("sampled", sampled), ("reference_absent", ra), ("matchable_missed", mm)):
        if not _is_int(val) or val < 0:
            return "FAIL", name

    if ra + mm != sampled:
        return "FAIL", "every sampled unmatched record must be labelled reference absent or matchable (%d + %d != %d)" % (ra, mm, sampled)
    expected_population = None
    for source in (A.get("computed"), A.get("reported")):
        if isinstance(source, dict):
            source = source.get("reconciliation", source)
            counts = source.get("status_counts", {}) if isinstance(source, dict) else {}
            if isinstance(counts, dict) and "NO_MATCH" in counts:
                expected_population = counts["NO_MATCH"]
                break
    try:
        coverage = mdm_audit.review_coverage(
            ur, ("reference_absent", "matchable_missed"), expected_population)
    except ValueError as exc:
        return "FAIL", str(exc)
    if coverage and coverage["coverage_state"] == "NO-DATA":
        return "NO-DATA", "unlabelled strata cover %d unmatched records" % coverage["unreviewed_population"]
    if sampled == 0:
        return "NO-DATA", "the unmatched sample is empty"

    strata = ur.get("strata")
    if strata is not None and not isinstance(strata, list):
        return "FAIL", "strata"

    filtered = []
    total_pop = 0
    if isinstance(strata, list):
        for s in strata:
            if not isinstance(s, dict):
                return "FAIL", "stratum entry"
            spop = s.get("population")
            ssamp = s.get("sampled")
            smm = s.get("matchable_missed")
            sra = s.get("reference_absent")
            if not _is_int(spop) or spop < 0:
                return "FAIL", "stratum population"
            if not _is_int(ssamp) or ssamp < 0:
                return "FAIL", "stratum sampled"
            if not _is_int(smm) or smm < 0:
                return "FAIL", "stratum matchable_missed"
            if not _is_int(sra) or sra < 0:
                return "FAIL", "stratum reference_absent"
            if smm + sra != ssamp:
                return "FAIL", "stratum labels sum mismatch"
            if ssamp >= 1:
                filtered.append({
                    "population": spop,
                    "sampled": ssamp,
                    "positive": smm,
                    "reference_absent": sra,
                })
                total_pop += spop

    q = None
    q_lo = None
    q_hi = None
    pop_for_bound = pop
    strata_note = ""

    if filtered:
        est = mdm_eval.stratified_estimate(filtered)
        q = est.get("estimate")
        q_lo = est.get("lo")
        q_hi = est.get("hi")
        pop_for_bound = total_pop
        strata_note = "weighted over %d unmatched strata" % len(filtered)
    else:
        q = mm / sampled
        q_lo, q_hi = mdm_eval.wilson_interval(mm, sampled)
        pop_for_bound = pop

    if filtered:
        a_est = mdm_eval.stratified_estimate([
            dict(row, positive=row["reference_absent"]) for row in filtered])
        a, a_lo, a_hi = a_est["estimate"], a_est["lo"], a_est["hi"]
    else:
        a = ra / sampled
        a_lo, a_hi = mdm_eval.wilson_interval(ra, sampled)

    M = None
    comp = A.get("computed")
    if comp is not None:
        if not isinstance(comp, dict):
            return "FAIL", "computed"
        rec = comp.get("reconciliation")
        if rec is not None:
            if not isinstance(rec, dict):
                return "FAIL", "reconciliation"
            sc = rec.get("status_counts")
            if sc is not None:
                if not isinstance(sc, dict):
                    return "FAIL", "status_counts"
                if "MATCH" in sc:
                    M = sc.get("MATCH")

    if M is None:
        rep = A.get("reported")
        if rep is not None:
            if not isinstance(rep, dict):
                return "FAIL", "reported"
            sc = rep.get("status_counts")
            if sc is not None:
                if not isinstance(sc, dict):
                    return "FAIL", "status_counts"
                if "MATCH" in sc:
                    M = sc.get("MATCH")

    if M is not None and (not _is_int(M) or M < 0):
        return "FAIL", "MATCH"

    claimed = E.get("claimed_recall")
    if claimed is not None and not _rate(claimed):
        return "FAIL", "claimed_recall"

    bound = None
    if M is not None and q_lo is not None:
        denom = M + pop_for_bound * q_lo
        if denom > 0:
            bound = M / denom
        else:
            bound = 0.0

    if claimed is not None and M is None:
        return "NO-DATA", "cannot bound recall without the matched count"

    if claimed is not None and bound is not None and claimed > bound + 1e-12:
        return "FAIL", "claimed recall %.3f exceeds %.3f, the most the unmatched sample allows: %d matched" % (claimed, bound, M)

    detail = "reference absent %.3f [%.3f, %.3f]; matchable but missed %.3f [%.3f, %.3f]" % (a, a_lo, a_hi, q, q_lo, q_hi)
    if strata_note:
        detail += "; " + strata_note
    if bound is not None:
        detail += "; recall among matchable records at most %.3f" % bound
    return "PASS", detail


def _gate_m29(md):
    A = _audit_dict(md)
    h = A.get("hierarchy")
    if h is None:
        return "NO-DATA", "no hierarchy snapshot"
    if not isinstance(h, dict):
        return "FAIL", "hierarchy"

    edges = h.get("edges")
    if not isinstance(edges, list):
        return "FAIL", "edges"
    for e in edges:
        if not isinstance(e, list) or len(e) != 2 or not isinstance(e[0], str) or not isinstance(e[1], str):
            return "FAIL", "edges"

    retired = h.get("retired", [])
    if retired is None:
        retired = []
    if not isinstance(retired, list) or not all(isinstance(x, str) for x in retired):
        return "FAIL", "retired"

    synthesized = h.get("synthesized", [])
    if synthesized is None:
        synthesized = []
    if not isinstance(synthesized, list) or not all(isinstance(x, str) for x in synthesized):
        return "FAIL", "synthesized"

    linkages = h.get("linkages", [])
    if linkages is None:
        linkages = []
    if not isinstance(linkages, list):
        return "FAIL", "linkages"
    for l in linkages:
        if not isinstance(l, list) or len(l) != 2 or not isinstance(l[0], str) or not isinstance(l[1], str):
            return "FAIL", "linkages"

    child_parents = {}
    adj = {}
    all_nodes = set()
    for child, parent in edges:
        all_nodes.add(child)
        all_nodes.add(parent)
        child_parents.setdefault(child, set()).add(parent)
        adj.setdefault(child, set()).add(parent)

    problems = []
    for child in sorted(child_parents):
        parents = child_parents[child]
        if len(parents) > 1:
            problems.append("node %s has more than one parent (%s)" % (child, ", ".join(sorted(parents))))

    state = {}
    stack = []
    cycles = []

    def dfs(node):
        state[node] = 1
        stack.append(node)
        for nxt in sorted(adj.get(node, ())):
            st = state.get(nxt, 0)
            if st == 0:
                dfs(nxt)
            elif st == 1:
                idx = stack.index(nxt)
                cyc = frozenset(stack[idx:])
                cycles.append(cyc)
        stack.pop()
        state[node] = 2

    for node in sorted(all_nodes):
        if state.get(node, 0) == 0:
            dfs(node)

    seen = set()
    for cyc in cycles:
        if cyc not in seen:
            seen.add(cyc)
            problems.append("cycle through %s" % ", ".join(sorted(cyc)))

    retired_set = set(retired)
    bad_links = 0
    for _, entity in linkages:
        if entity in retired_set:
            bad_links += 1
    if bad_links:
        problems.append("%d linkage(s) point to retired entities; must be zero" % bad_links)

    if problems:
        return "FAIL", "; ".join(problems)

    n = len(all_nodes)
    e = len(edges)
    s = len(synthesized)
    share = (s / n) if n > 0 else 0.0
    return "PASS", "%d node(s), %d edge(s), %d synthesized (share %.3f)" % (n, e, s, share)


def _gate_m30(md):
    A = _audit_dict(md)
    lc = A.get("labeller_confusion")
    if lc is None:
        return "NO-DATA", "no model versus human confusion counts"
    if not isinstance(lc, dict):
        return "FAIL", "labeller_confusion"

    a = lc.get("both_match")
    b = lc.get("model_only")
    c = lc.get("human_only")
    d = lc.get("both_nonmatch")
    for name, val in (("both_match", a), ("model_only", b), ("human_only", c), ("both_nonmatch", d)):
        if not _is_int(val) or val < 0:
            return "FAIL", name

    n = a + b + c + d
    if n < 50:
        return "FAIL", "fewer than 50 overlapping labels (%d)" % n

    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    if pe == 1:
        return "FAIL", "kappa undefined: both labellers gave one constant label"

    kappa = (po - pe) / (1 - pe)
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    lower = kappa - 1.645 * se

    pos_den = 2 * a + b + c
    neg_den = 2 * d + b + c
    pos = (2 * a / pos_den) if pos_den != 0 else None
    neg = (2 * d / neg_den) if neg_den != 0 else None

    mk = A.get("min_kappa_lower", 0.6)
    if not _is_num(mk) or not -1.0 <= float(mk) <= 1.0:
        return "FAIL", "min_kappa_lower"

    pos_s = _fmt(pos)
    neg_s = _fmt(neg)
    if lower < mk:
        return "FAIL", "kappa %.3f with one-sided 95 percent lower bound %.3f is below %.3f; positive agreement %s, negative agreement %s" % (kappa, lower, mk, pos_s, neg_s)
    return "PASS", "kappa %.3f, one-sided 95 percent lower bound %.3f, positive agreement %s, negative agreement %s, n = %d" % (kappa, lower, pos_s, neg_s, n)


def register(packs_module, Finding):
    def fn(claim, Finding):
        if not isinstance(claim, dict):
            return []
        md = claim.get("master_data")
        if not isinstance(md, dict):
            return []
        if "audit" not in md:
            return []
        if not isinstance(md.get("audit"), dict):
            return []
        findings = []
        for gate, func in ((GATE27, _gate_m27), (GATE28, _gate_m28), (GATE29, _gate_m29), (GATE30, _gate_m30)):
            try:
                verdict, detail = func(md)
            except Exception as e:
                verdict = "FAIL"
                detail = "malformed input: " + str(e)
            findings.append(Finding(gate, verdict, detail))
        return findings

    packs_module.register("MASTER_DATA", fn)


def selftest(expect):
    ok = True

    class Finding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

    class Packs:
        def __init__(self):
            self.calls = []

        def register(self, name, fn):
            self.calls.append((name, fn))

    packs = Packs()
    register(packs, Finding)
    ok &= expect(len(packs.calls) == 1, "register exactly once")
    ok &= expect(packs.calls[0][0] == "MASTER_DATA", "register name")
    fn = packs.calls[0][1]

    def get(md, gate):
        claim = {"master_data": md}
        res = fn(claim, Finding)
        for f in res:
            if f.gate == gate:
                return f
        raise AssertionError("missing gate " + gate)

    ok &= expect(fn({}, Finding) == [], "no master_data returns empty")
    ok &= expect(fn({"master_data": None}, Finding) == [], "master_data not dict returns empty")

    ok &= expect(fn({"master_data": {}}, Finding) == [], "master_data without audit returns empty")
    ok &= expect(fn({"master_data": {"audit": None}}, Finding) == [], "audit None returns empty")
    ok &= expect(fn({"master_data": {"audit": "x"}}, Finding) == [], "audit not dict returns empty")
    ok &= expect(fn({"master_data": {"audit": []}}, Finding) == [], "audit list returns empty")
    ok &= expect(len(fn({"master_data": {"audit": {}}}, Finding)) == 4, "empty audit dict gives four findings")
    ok &= expect(len(fn({"master_data": {"audit": {"x": 1}}}, Finding)) == 4, "nonempty audit dict gives four findings")

    # ----- M27 -----
    f = get({"audit": {}}, GATE27)
    ok &= expect(f.verdict == "NO-DATA", "M27 no segments NO-DATA")
    md = {"audit": {"segments": [{"segment": "A", "n": 100, "matched": 50}, {"segment": "B", "n": 100, "matched": 50}]}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "PASS", "M27 similar segments PASS")
    ok &= expect("Bonferroni z" in f.detail, "M27 detail has Bonferroni z")
    md = {"audit": {"segments": [{"segment": "A", "n": 100, "matched": 10}, {"segment": "B", "n": 100, "matched": 90}]}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "FAIL", "M27 below without disclosure FAIL")
    md = {"audit": {"segments": [{"segment": "A", "n": 100, "matched": 10}, {"segment": "B", "n": 100, "matched": 90}], "segment_disclosure": True}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "PASS", "M27 below with disclosure PASS")
    md = {"audit": {"segments": [{"segment": "A", "n": "x", "matched": 0}]}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "FAIL" and "field n" in f.detail, "M27 malformed n FAIL")
    md = {"audit": {"segments": [{"segment": "A", "n": 100, "matched": 50}, {"segment": "B", "n": 100, "matched": 50}, {"segment": "C", "n": 10, "matched": 0}], "min_segment_n": 30}}
    f = get(md, GATE27)
    ok &= expect("small segment" in f.detail, "M27 small segment note")
    md = {"audit": {"segments": [], "min_segment_n": "x"}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "NO-DATA", "M27 empty segments NO-DATA")

    # defect 1: a segment with n = 0 must never be eligible, even when min_segment_n is 0.
    md = {"audit": {"segments": [{"segment": "A", "n": 0, "matched": 0}, {"segment": "B", "n": 100, "matched": 50}], "min_segment_n": 0}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "NO-DATA", "M27 defect 1: n=0 segment not eligible (min_segment_n 0)")
    ok &= expect("fewer than two segments" in f.detail, "M27 defect 1: detail says fewer than two segments")
    md = {"audit": {"segments": [{"segment": "A", "n": 0, "matched": 0}, {"segment": "B", "n": 0, "matched": 0}], "min_segment_n": 0}}
    f = get(md, GATE27)
    ok &= expect(f.verdict == "NO-DATA", "M27 defect 1: two empty segments NO-DATA, no ZeroDivisionError")

    # ----- M28 -----
    f = get({"audit": {}}, GATE28)
    ok &= expect(f.verdict == "NO-DATA", "M28 absent NO-DATA")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 5, "matchable_missed": 5}, "computed": {"reconciliation": {"status_counts": {"MATCH": 90}}}}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "PASS", "M28 valid PASS")
    ok &= expect("reference absent" in f.detail, "M28 detail reference absent")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 5, "matchable_missed": 5}, "computed": {"reconciliation": {"status_counts": {"MATCH": 90}}}}, "evaluation": {"claimed_recall": 0.99}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "FAIL", "M28 claimed exceeds FAIL")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 5, "matchable_missed": 5}}, "evaluation": {"claimed_recall": 0.9}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "NO-DATA", "M28 claimed no M NO-DATA")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": "10", "reference_absent": 5, "matchable_missed": 5}}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "FAIL" and "sampled" in f.detail, "M28 malformed sampled FAIL")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 0, "reference_absent": 0, "matchable_missed": 0}}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "NO-DATA", "M28 sampled 0 NO-DATA")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 4, "matchable_missed": 5}}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "FAIL" and "!=" in f.detail, "M28 labels sum FAIL")

    # defect 2a: when M == 0 the bound is 0, so any claimed_recall above 0 FAILs
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 5, "matchable_missed": 5}, "computed": {"reconciliation": {"status_counts": {"MATCH": 0}}}}, "evaluation": {"claimed_recall": 0.5}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "FAIL", "M28 defect 2a: M=0 claimed_recall above 0 FAILs")
    ok &= expect("exceeds 0.000" in f.detail, "M28 defect 2a: detail shows bound 0.000")
    ok &= expect("0 matched" in f.detail, "M28 defect 2a: detail mentions 0 matched")

    # defect 2b: strata produce a weighted estimate, no crash
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 5, "matchable_missed": 5, "strata": [
        {"stratum": "s1", "population": 60, "sampled": 6, "matchable_missed": 3, "reference_absent": 3},
        {"stratum": "s2", "population": 40, "sampled": 4, "matchable_missed": 2, "reference_absent": 2},
    ]}, "computed": {"reconciliation": {"status_counts": {"MATCH": 90}}}}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "PASS", "M28 defect 2b: weighted strata PASS")
    ok &= expect("weighted over 2 unmatched strata" in f.detail, "M28 defect 2b: detail says weighted over 2 unmatched strata")
    md = {"audit": {"unmatched_review": {"population": 100, "sampled": 10, "reference_absent": 5, "matchable_missed": 5, "strata": [
        {"stratum": "s1", "population": 60, "sampled": 6, "matchable_missed": 3, "reference_absent": 3},
        {"stratum": "s2", "population": 40, "sampled": 0, "matchable_missed": 0, "reference_absent": 0},
    ]}, "computed": {"reconciliation": {"status_counts": {"MATCH": 90}}}}}
    f = get(md, GATE28)
    ok &= expect(f.verdict == "FAIL", "M28 aggregate contradicts zero-sample strata FAIL")
    ok &= expect("sum does not match aggregate" in f.detail, "M28 contradictory aggregates named")

    # ----- M29 -----
    f = get({"audit": {}}, GATE29)
    ok &= expect(f.verdict == "NO-DATA", "M29 absent NO-DATA")
    md = {"audit": {"hierarchy": {"edges": [["a", "b"], ["b", "c"]], "retired": [], "linkages": [], "synthesized": ["x"]}}}
    f = get(md, GATE29)
    ok &= expect(f.verdict == "PASS", "M29 valid PASS")
    ok &= expect("share" in f.detail, "M29 detail share")
    md = {"audit": {"hierarchy": {"edges": [["a", "b"], ["a", "c"]], "retired": [], "linkages": [], "synthesized": []}}}
    f = get(md, GATE29)
    ok &= expect(f.verdict == "FAIL" and "more than one parent" in f.detail, "M29 multiple parents FAIL")
    md = {"audit": {"hierarchy": {"edges": [["a", "b"], ["b", "a"]], "retired": [], "linkages": [], "synthesized": []}}}
    f = get(md, GATE29)
    ok &= expect(f.verdict == "FAIL" and "cycle through" in f.detail, "M29 cycle FAIL")
    md = {"audit": {"hierarchy": {"edges": [["a", "b"]], "retired": ["b"], "linkages": [["x", "b"]], "synthesized": []}}}
    f = get(md, GATE29)
    ok &= expect(f.verdict == "FAIL" and "retired" in f.detail, "M29 retired linkage FAIL")
    md = {"audit": {"hierarchy": {"edges": [["a"]], "retired": [], "linkages": [], "synthesized": []}}}
    f = get(md, GATE29)
    ok &= expect(f.verdict == "FAIL" and f.detail == "edges", "M29 malformed edges FAIL")
    md = {"audit": {"hierarchy": {"edges": [], "retired": "x", "linkages": [], "synthesized": []}}}
    f = get(md, GATE29)
    ok &= expect(f.verdict == "FAIL" and "retired" in f.detail, "M29 malformed retired FAIL")

    # ----- M30 -----
    f = get({"audit": {}}, GATE30)
    ok &= expect(f.verdict == "NO-DATA", "M30 absent NO-DATA")
    md = {"audit": {"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "PASS", "M30 valid PASS")
    ok &= expect("kappa" in f.detail, "M30 detail kappa")
    md = {"audit": {"labeller_confusion": {"both_match": 10, "model_only": 5, "human_only": 5, "both_nonmatch": 20}}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "FAIL" and "fewer than 50" in f.detail, "M30 n small FAIL")
    md = {"audit": {"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}, "min_kappa_lower": 0.9}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "FAIL" and "below" in f.detail, "M30 lower below mk FAIL")
    md = {"audit": {"labeller_confusion": {"both_match": 100, "model_only": 0, "human_only": 0, "both_nonmatch": 0}}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "FAIL" and "constant" in f.detail, "M30 pe 1 FAIL")
    md = {"audit": {"labeller_confusion": {"both_match": "x", "model_only": 0, "human_only": 0, "both_nonmatch": 0}}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "FAIL" and "both_match" in f.detail, "M30 malformed both_match FAIL")
    md = {"audit": {"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}, "min_kappa_lower": "x"}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "FAIL" and "min_kappa_lower" in f.detail, "M30 malformed min_kappa FAIL")

    # defect 3: one-sided 95 percent lower bound uses z = 1.645 and the detail says so
    md = {"audit": {"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}}}
    f = get(md, GATE30)
    ok &= expect("one-sided 95 percent lower bound" in f.detail, "M30 defect 3: PASS detail says one-sided 95 percent lower bound")
    a, b, c, d = 40, 5, 5, 50
    n = a + b + c + d
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    kappa = (po - pe) / (1 - pe)
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    lower_1_645 = kappa - 1.645 * se
    lower_1_96 = kappa - 1.96 * se
    ok &= expect(("%.3f" % lower_1_645) in f.detail, "M30 defect 3: PASS detail carries the 1.645 lower bound")
    ok &= expect(("%.3f" % lower_1_96) not in f.detail or abs(lower_1_645 - lower_1_96) < 1e-9, "M30 defect 3: 1.96 lower bound does not appear")
    md = {"audit": {"labeller_confusion": {"both_match": 40, "model_only": 5, "human_only": 5, "both_nonmatch": 50}, "min_kappa_lower": 0.9}}
    f = get(md, GATE30)
    ok &= expect(f.verdict == "FAIL", "M30 defect 3: low kappa FAILs")
    ok &= expect("one-sided 95 percent lower bound" in f.detail, "M30 defect 3: FAIL detail says one-sided 95 percent lower bound")

    return ok


if __name__ == "__main__":
    def expect(cond, msg):
        if not cond:
            print("FAIL: " + msg)
        return bool(cond)

    ok = selftest(expect)
    if ok:
        print("SELFTEST PASS")
        sys.exit(0)
    else:
        sys.exit(1)
