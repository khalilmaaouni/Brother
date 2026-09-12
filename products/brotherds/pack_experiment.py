"""Deterministic experiment checks, using only declared synthetic or real inputs.

Only register and selftest are public; Finding is supplied by the pack seam.
"""
import math as _math


class _Issue(Exception):
    def __init__(self, verdict, detail):
        self.verdict = verdict
        self.detail = detail


def _missing(field):
    raise _Issue("NO-DATA", "missing %s; no evidence was supplied" % field)


def _invalid(field, value, rule):
    raise _Issue("FAIL", "%s must %s, got %r" % (field, rule, value))


def _required(obj, field, prefix="experiment"):
    value = obj.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        _missing(prefix + "." + field)
    return value


def _number(value, field):
    try:
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and _math.isfinite(value))
    except OverflowError:
        valid = False
    if not valid:
        _invalid(field, value, "be a finite number")
    return value


def _experiment(claim):
    value = _required(claim, "experiment", "claim")
    if not isinstance(value, dict):
        _invalid("experiment", value, "be an object")
    return value


def _counts(exp):
    arms = _required(exp, "arms")
    if not isinstance(arms, list) or len(arms) < 2:
        _invalid("experiment.arms", arms, "be a list with at least two arms")
    counts = []
    for i, arm in enumerate(arms):
        field = "experiment.arms[%d]" % i
        if not isinstance(arm, dict):
            _invalid(field, arm, "be an object")
        key = "users" if "users" in arm else "units"
        if "users" not in arm and "units" not in arm:
            _missing(field + ".users or " + field + ".units")
        n = _number(_required(arm, key, field), field + "." + key)
        if n < 0 or int(n) != n:
            _invalid(field + "." + key, n, "be a nonnegative integer count")
        if "users" in arm and "units" in arm:
            units = _number(_required(arm, "units", field), field + ".units")
            if units != n:
                _invalid(field + ".units", units, "equal users when both counts are given")
        counts.append(n)
    return counts


def _srm(exp):
    counts = _counts(exp)
    ratio = exp.get("intended_ratio", [1.0 / len(counts)] * len(counts))
    if ratio is None:
        _missing("experiment.intended_ratio")
    if not isinstance(ratio, list) or len(ratio) != len(counts):
        _invalid("experiment.intended_ratio", ratio, "have one probability per arm")
    for i, value in enumerate(ratio):
        field = "experiment.intended_ratio[%d]" % i
        _number(value, field)
        if not 0 < value < 1:
            _invalid(field, value, "be greater than zero and less than one")
    if not _math.isclose(sum(ratio), 1.0, rel_tol=0, abs_tol=1e-9):
        _invalid("experiment.intended_ratio", ratio, "sum to one")
    total = _number(sum(counts), "experiment.arms total count")
    if total <= 0:
        _invalid("experiment.arms total count", total, "be positive")
    if len(counts) > 5:
        return "NO-DATA", "SRM threshold unavailable above five arms, got %d arms" % len(counts)
    expected = [total * p for p in ratio]
    if any(n == 0 for n in expected):
        return "NO-DATA", "experiment.intended_ratio is too small to compute expected counts"
    residuals = [(n - e) / _math.sqrt(e) for n, e in zip(counts, expected)]
    statistic = sum(residual * residual for residual in residuals)
    threshold = {2: 10.828, 3: 13.816, 4: 16.266, 5: 18.467}[len(counts)]
    return ("FAIL" if statistic > threshold else "PASS",
            "arm balance chi-square %.6g %s threshold %.3f at alpha 0.001 (%d df)"
            % (statistic, "exceeds" if statistic > threshold else "does not exceed",
               threshold, len(counts) - 1))


def _mde(exp):
    counts = _counts(exp)
    n = min(counts)
    if n <= 0:
        _invalid("experiment.arms smallest count", n, "be positive for MDE")
    metric = _required(exp, "primary_metric")
    if not isinstance(metric, dict):
        _invalid("experiment.primary_metric", metric, "be an object")
    prefix = "experiment.primary_metric"
    baseline = _number(_required(metric, "baseline_rate", prefix), prefix + ".baseline_rate")
    if not 0 < baseline < 1:
        _invalid(prefix + ".baseline_rate", baseline, "be between zero and one exclusively")
    observed = _number(_required(metric, "observed_effect_relative", prefix),
                       prefix + ".observed_effect_relative")
    alpha = exp.get("alpha", 0.05)
    power = exp.get("power", 0.8)
    for key, value in (("alpha", alpha), ("power", power)):
        if value is None:
            _missing("experiment." + key)
        _number(value, "experiment." + key)
        if not 0 < value < 1:
            _invalid("experiment." + key, value, "be between zero and one exclusively")
    za = {0.05: 1.959963984540054, 0.01: 2.5758293035489004}
    zp = {0.8: 0.8416212335729143, 0.9: 1.2815515655446004}
    if alpha not in za or power not in zp:
        return "NO-DATA", ("MDE table unavailable for experiment.alpha=%s and "
                           "experiment.power=%s; supported alpha 0.05 or 0.01, power 0.8 or 0.9"
                           % (alpha, power))
    # Equal baseline variance approximation for the two-proportion z design.
    # Divide the absolute rate difference by baseline to compare relative effects.
    mde = ((za[alpha] + zp[power]) * _math.sqrt(2 * (1 - baseline))
           / _math.sqrt(n) / _math.sqrt(baseline))
    return ("FAIL" if abs(observed) < mde else "PASS",
            "observed relative effect magnitude %.6g %s relative MDE %.6g "
            "at n=%s per arm, baseline=%s, alpha=%s, power=%s"
            % (abs(observed), "is below" if abs(observed) < mde else "meets",
               mde, n, baseline, alpha, power))


def _guardrail(exp):
    guards = _required(exp, "guardrails")
    if not isinstance(guards, list):
        _invalid("experiment.guardrails", guards, "be a list")
    if not guards:
        _missing("experiment.guardrails")
    tripped, issues = [], []
    for i, guard in enumerate(guards):
        field = "experiment.guardrails[%d]" % i
        try:
            if not isinstance(guard, dict):
                _invalid(field, guard, "be an object")
            name = _required(guard, "name", field)
            if not isinstance(name, str):
                _invalid(field + ".name", name, "be a nonempty string")
            delta = _number(_required(guard, "delta", field), field + ".delta")
            significant = _required(guard, "significant", field)
            if not isinstance(significant, bool):
                _invalid(field + ".significant", significant, "be a boolean")
            direction = _required(guard, "direction_bad", field)
            if direction not in ("down", "up"):
                _invalid(field + ".direction_bad", direction, "be down or up")
            if significant and ((direction == "down" and delta < 0)
                                or (direction == "up" and delta > 0)):
                tripped.append(name)
        except _Issue as issue:
            issues.append(issue)
    if tripped:
        return "FAIL", "significant guardrail harm in: %s%s" % (
            ", ".join(tripped), "; " + "; ".join(i.detail for i in issues) if issues else "")
    if issues:
        return ("FAIL" if any(i.verdict == "FAIL" for i in issues) else "NO-DATA",
                "; ".join(i.detail for i in issues))
    return "PASS", "%d declared guardrail(s), none significantly moved in its bad direction" % len(guards)


def _hygiene(exp):
    details, missing, invalid = [], False, False
    for field in ("aa_test_run", "novelty_window_excluded_days", "variance_reduction_method"):
        value = exp.get(field)
        if value is None:
            missing = True
            details.append("%s=NO-DATA (not reported)" % field)
            continue
        valid = (isinstance(value, bool) if field == "aa_test_run" else
                 isinstance(value, int) and not isinstance(value, bool) and value >= 0
                 if field == "novelty_window_excluded_days" else
                 value in ("none", "CUPED", "other"))
        if not valid:
            invalid = True
        details.append("%s=%r%s" % (field, value, " (invalid value)" if not valid else ""))
    return ("FAIL" if invalid else "NO-DATA" if missing else "PASS",
            "; ".join(details) + "; reported hygiene only, effectiveness was not tested")


def _pvalue(exp):
    p = _number(_required(exp, "p_value"), "experiment.p_value")
    if not 0 <= p <= 1:
        _invalid("experiment.p_value", p, "be between zero and one")
    effect, ci = exp.get("effect_size"), exp.get("ci")
    if effect is None and ci is None:
        return "FAIL", "a p-value without an effect size and an interval is not a reportable result"
    if effect is not None:
        _number(effect, "experiment.effect_size")
    if ci is not None:
        if not isinstance(ci, list) or len(ci) != 2:
            _invalid("experiment.ci", ci, "be a two-number interval [lo, hi]")
        for i, v in enumerate(ci):
            _number(v, "experiment.ci[%d]" % i)
        if ci[0] > ci[1]:
            _invalid("experiment.ci", ci, "have lo less than or equal to hi")
    if effect is None:
        _missing("experiment.effect_size")
    if ci is None:
        _missing("experiment.ci")
    return "PASS", "p_value=%s is accompanied by effect_size=%s and ci=%s" % (p, effect, ci)


def _findings(claim, Finding):
    findings = []
    for gate, fn in (("X1.srm", _srm), ("X2.mde", _mde), ("X3.guardrail", _guardrail),
                     ("X4.design_hygiene", _hygiene), ("X5.pvalue_wording", _pvalue)):
        try:
            verdict, detail = fn(_experiment(claim))
        except _Issue as issue:
            verdict, detail = issue.verdict, issue.detail
        findings.append(Finding(gate, verdict, detail))
    return findings


def register(packs_module, Finding):
    packs_module.register("EXPERIMENT", _findings)


def selftest(expect):
    """Refusals, missing evidence, boundaries and type isolation via the seam."""
    import copy
    import types
    import packs

    ok = True
    base = {"arms": [{"users": 1000}, {"units": 1000}],
            "primary_metric": {"baseline_rate": 0.1, "observed_effect_relative": 0.5},
            "guardrails": [{"name": "retention", "delta": -0.01,
                            "significant": False, "direction_bad": "down"}],
            "aa_test_run": False, "novelty_window_excluded_days": 0,
            "variance_reduction_method": "none", "p_value": 0.04,
            "effect_size": 0.05, "ci": [0.01, 0.09]}

    def results(exp):
        return {f.gate: f for f in _findings({"experiment": exp},
                lambda gate, verdict, detail: types.SimpleNamespace(
                    gate=gate, verdict=verdict, detail=detail))}

    c = copy.deepcopy(base); c["arms"] = [{"users": 5200}, {"users": 4800}]
    ok &= expect(results(c)["X1.srm"].verdict == "FAIL", "X1 refuses 5200/4800 at equal allocation")
    ok &= expect("16 exceeds threshold 10.828" in results(c)["X1.srm"].detail, "X1 states statistic and threshold")
    c["arms"] = [{"users": 5000}, {"users": 5000}]
    ok &= expect(results(c)["X1.srm"].verdict == "PASS", "X1 accepts balanced allocation")
    c["arms"] = [{"users": 7500}, {"users": 2500}]; c["intended_ratio"] = [0.75, 0.25]
    ok &= expect(results(c)["X1.srm"].verdict == "PASS", "X1 uses intended unequal allocation")
    for arms, threshold in ((3, "13.816"), (4, "16.266"), (5, "18.467")):
        c = copy.deepcopy(base); c["arms"] = [{"units": 1000} for _ in range(arms)]
        ok &= expect(results(c)["X1.srm"].verdict == "PASS" and threshold in results(c)["X1.srm"].detail,
                     "X1 supports its critical table for %d arms" % arms)
    c["arms"].append({"units": 1000})
    ok &= expect(results(c)["X1.srm"].verdict == "NO-DATA", "X1 has no threshold above five arms")
    for ratio in ([0.4, 0.4], [0, 1], [1], "equal", [True, False], [float("nan"), 0.5]):
        c = copy.deepcopy(base); c["intended_ratio"] = ratio
        ok &= expect(results(c)["X1.srm"].verdict == "FAIL", "X1 refuses invalid intended_ratio")
    c = copy.deepcopy(base); c["intended_ratio"] = None
    ok &= expect(results(c)["X1.srm"].verdict == "NO-DATA", "X1 names explicitly missing ratio")
    c = copy.deepcopy(base); c["arms"] = [{"users": 0}, {"units": 0}]
    ok &= expect(results(c)["X1.srm"].verdict == "FAIL", "X1 refuses zero total")
    c = copy.deepcopy(base); c["arms"] = [{"users": 1e200}, {"units": 1}]
    c["intended_ratio"] = [1e-200, 0.9999999995]
    ok &= expect(results(c)["X1.srm"].verdict == "FAIL", "X1 extreme imbalance fails without numeric overflow")
    for count in (-1, 1.5, True, "1000", float("inf")):
        c = copy.deepcopy(base); c["arms"][0]["users"] = count
        ok &= expect(results(c)["X1.srm"].verdict == "FAIL" and results(c)["X2.mde"].verdict == "FAIL",
                     "X1 and X2 refuse invalid arm counts")
    c = copy.deepcopy(base); c["arms"][0] = {"name": "control"}
    ok &= expect(results(c)["X1.srm"].verdict == "NO-DATA" and "users or" in results(c)["X1.srm"].detail,
                 "X1 names missing counts")
    c = copy.deepcopy(base); c["primary_metric"]["observed_effect_relative"] = 0.01
    ok &= expect(results(c)["X2.mde"].verdict == "FAIL", "X2 refuses 0.01 relative effect at n=1000")
    ok &= expect("relative MDE 0.375872" in results(c)["X2.mde"].detail, "X2 computes relative MDE from baseline variance")
    c["primary_metric"]["observed_effect_relative"] = 0.05
    ok &= expect(results(c)["X2.mde"].verdict == "FAIL", "X2 compares relative effects in relative units")
    c["primary_metric"]["observed_effect_relative"] = 0.5
    ok &= expect(results(c)["X2.mde"].verdict == "PASS", "X2 accepts relative effect above MDE")
    c["primary_metric"]["observed_effect_relative"] = -0.5
    ok &= expect(results(c)["X2.mde"].verdict == "PASS", "X2 uses magnitude for a two-sided effect")
    c["arms"][0]["users"] = 100
    ok &= expect(results(c)["X2.mde"].verdict == "FAIL", "X2 uses the smallest arm")
    for alpha in (0.05, 0.01):
        for power in (0.8, 0.9):
            c = copy.deepcopy(base); c.update(alpha=alpha, power=power)
            ok &= expect(results(c)["X2.mde"].verdict != "NO-DATA", "X2 supports all declared z table pairs")
    for key in ("alpha", "power"):
        c = copy.deepcopy(base); c[key] = 0.7
        ok &= expect(results(c)["X2.mde"].verdict == "NO-DATA", "X2 unsupported %s is NO-DATA" % key)
        c[key] = 2
        ok &= expect(results(c)["X2.mde"].verdict == "FAIL", "X2 invalid %s fails" % key)
        c[key] = None
        ok &= expect(results(c)["X2.mde"].verdict == "NO-DATA", "X2 null %s is missing" % key)
    for baseline in (0, 1, -0.1, True, float("nan")):
        c = copy.deepcopy(base); c["primary_metric"]["baseline_rate"] = baseline
        ok &= expect(results(c)["X2.mde"].verdict == "FAIL", "X2 refuses invalid baseline_rate")
    c = copy.deepcopy(base); del c["primary_metric"]["baseline_rate"]
    ok &= expect(results(c)["X2.mde"].verdict == "NO-DATA", "X2 names missing baseline_rate")
    c = copy.deepcopy(base); c["primary_metric"]["baseline_rate"] = 1e-300
    c["arms"] = [{"units": 1e100}, {"units": 1e100}]
    ok &= expect(results(c)["X2.mde"].verdict == "FAIL", "X2 tiny baselines cannot underflow MDE into a pass")
    c = copy.deepcopy(base); c["guardrails"][0]["significant"] = True
    ok &= expect(results(c)["X3.guardrail"].verdict == "FAIL" and "retention" in results(c)["X3.guardrail"].detail,
                 "X3 names significant guardrail harm")
    c["guardrails"].insert(0, {})
    ok &= expect(results(c)["X3.guardrail"].verdict == "FAIL", "X3 missing evidence cannot hide another guardrail veto")
    for direction, delta, verdict in (("up", 0.01, "FAIL"), ("up", -0.01, "PASS"), ("down", 0, "PASS")):
        c = copy.deepcopy(base); c["guardrails"][0].update(significant=True, direction_bad=direction, delta=delta)
        ok &= expect(results(c)["X3.guardrail"].verdict == verdict, "X3 follows declared bad direction")
    c = copy.deepcopy(base); c["guardrails"][0]["significant"] = "false"
    ok &= expect(results(c)["X3.guardrail"].verdict == "FAIL", "X3 refuses a nonboolean significant value")
    c["guardrails"] = []
    ok &= expect(results(c)["X3.guardrail"].verdict == "NO-DATA", "X3 empty guardrails are missing evidence")
    ok &= expect(results(base)["X4.design_hygiene"].verdict == "PASS", "X4 reports false, zero and none without a policy veto")
    c = copy.deepcopy(base)
    for field in ("aa_test_run", "novelty_window_excluded_days", "variance_reduction_method"):
        del c[field]
    ok &= expect(results(c)["X4.design_hygiene"].verdict == "NO-DATA" and
                 results(c)["X4.design_hygiene"].detail.count("NO-DATA (not reported)") == 3,
                 "X4 names every absent hygiene field")
    for key, value in (("aa_test_run", "yes"), ("novelty_window_excluded_days", -1), ("variance_reduction_method", "magic")):
        c = copy.deepcopy(base); c[key] = value
        ok &= expect(results(c)["X4.design_hygiene"].verdict == "FAIL", "X4 names invalid hygiene values")
    c = copy.deepcopy(base); del c["effect_size"]; del c["ci"]
    ok &= expect(results(c)["X5.pvalue_wording"].verdict == "FAIL", "X5 refuses a p-value alone")
    ok &= expect(results(c)["X5.pvalue_wording"].detail ==
                 "a p-value without an effect size and an interval is not a reportable result",
                 "X5 carries the specified refusal sentence")
    for field in ("effect_size", "ci", "p_value"):
        c = copy.deepcopy(base); del c[field]
        ok &= expect(results(c)["X5.pvalue_wording"].verdict == "NO-DATA" and field in results(c)["X5.pvalue_wording"].detail,
                     "X5 names missing %s" % field)
    for key, value in (("p_value", -0.1), ("p_value", True), ("effect_size", float("nan")),
                       ("ci", [2, 1]), ("ci", [0]), ("ci", [0, float("inf")])):
        c = copy.deepcopy(base); c[key] = value
        ok &= expect(results(c)["X5.pvalue_wording"].verdict == "FAIL", "X5 refuses invalid result fields")
    ok &= expect(all(f.verdict == "PASS" for f in results(base).values()), "complete fixture passes all X gates")
    ok &= expect(all(f.verdict == "NO-DATA" for f in results(None).values()), "absent experiment reports NO-DATA on every gate")
    ok &= expect(all(f.verdict == "FAIL" for f in results([]).values()), "malformed experiment fails without an exception")
    # Use the real registry without changing global state or duplicating registration.
    registry = types.SimpleNamespace(register=lambda kind, fn: registered.append((kind, fn)))
    registered = []
    register(registry, None)
    ok &= expect(registered == [("EXPERIMENT", _findings)], "pack registers exactly one EXPERIMENT handler")
    original = packs.PACKS
    try:
        packs.PACKS = {"EXPERIMENT": []}
        register(packs, None)
        factory = lambda gate, verdict, detail: types.SimpleNamespace(gate=gate, verdict=verdict, detail=detail)
        ok &= expect(len(packs.run_packs("EXPERIMENT", {"experiment": base}, factory)) == 5,
                     "EXPERIMENT dispatch emits all five X gates")
        ok &= expect(packs.run_packs("DESCRIPTIVE", {"experiment": base}, factory) == [],
                     "A20 experiment pack never runs for DESCRIPTIVE")
    finally:
        packs.PACKS = original
    return bool(ok)
