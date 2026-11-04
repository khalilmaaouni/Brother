"""
Forecast scoring and calibration for quantile claims.

References:
- Gneiting and Raftery 2007, Strictly Proper Scoring Rules, Prediction, and Estimation.
- Bracher, Ray, Gneiting and Reich 2021, PLOS Computational Biology, Evaluating epidemic forecasts in an interval format.

Why WIS: A plain inside-or-outside check only records whether reality fell in a band.
WIS is a proper score that rewards sharp and calibrated bands, penalizing width,
underprediction, and overprediction in a decomposed way.
"""

import json
import math
import sys


def pinball_loss(tau, q, y):
    """Quantile loss: (1{y < q} - tau) * (q - y)."""
    try:
        tau = float(tau)
    except (TypeError, ValueError):
        raise ValueError("tau must be in (0,1)")
    if not (0.0 < tau < 1.0):
        raise ValueError("tau must be in (0,1)")
    ind = 1.0 if y < q else 0.0
    return (ind - tau) * (q - y)


def interval_score(lo, hi, y, alpha):
    """Gneiting and Raftery 2007 interval score."""
    if lo > hi:
        raise ValueError("lo must be less than or equal to hi")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0,1)")
    score = hi - lo
    if y < lo:
        score += (2.0 / alpha) * (lo - y)
    elif y > hi:
        score += (2.0 / alpha) * (y - hi)
    return score


def _find_tau(parsed, target):
    """Find a parsed tau equal or very close to target."""
    if target in parsed:
        return target
    best = None
    best_diff = None
    for tau in parsed:
        diff = abs(tau - target)
        if diff <= 1e-12:
            if best is None or diff < best_diff:
                best = tau
                best_diff = diff
    return best


def wis(quantiles, y):
    """Weighted interval score, Bracher, Ray, Gneiting and Reich 2021."""
    if not isinstance(quantiles, dict):
        raise ValueError("quantiles must be a dict")

    parsed = {}
    tau_to_key = {}

    for key, value in quantiles.items():
        try:
            tau = float(key)
        except (TypeError, ValueError):
            raise ValueError(f"non-numeric quantile key: {key!r}")
        if not (0.0 < tau < 1.0):
            raise ValueError(f"probability outside (0,1): {key!r}")
        try:
            q = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"non-numeric quantile value for key {key!r}: {value!r}")
        if tau in parsed:
            raise ValueError(f"duplicate probability key: {key!r}")
        parsed[tau] = q
        tau_to_key[tau] = key

    if 0.5 not in parsed:
        raise ValueError("missing required median key 0.5")

    sorted_taus = sorted(parsed)
    prev = None
    for tau in sorted_taus:
        q = parsed[tau]
        if prev is not None and q < prev:
            raise ValueError("quantile values must be non-decreasing with probability")
        prev = q

    m = parsed[0.5]

    used = set()
    pairs = []
    for tau in sorted_taus:
        if tau == 0.5 or tau in used:
            continue
        if tau < 0.5:
            partner_target = 1.0 - tau
            partner = _find_tau(parsed, partner_target)
            if partner is not None and partner > 0.5 and partner not in used:
                pairs.append((tau, partner))
                used.add(tau)
                used.add(partner)

    ignored_taus = []
    for tau in sorted_taus:
        if tau == 0.5:
            continue
        if tau not in used:
            ignored_taus.append(tau)
    ignored = sorted([tau_to_key[tau] for tau in ignored_taus])

    alphas = sorted([2.0 * tau for tau, _ in pairs])
    K = len(pairs)
    denom = K + 0.5
    w0 = 0.5

    disp_sum = 0.0
    under_sum = 0.0
    over_sum = 0.0

    for tau, partner in pairs:
        alpha = 2.0 * tau
        w = alpha / 2.0
        lo = parsed[tau]
        hi = parsed[partner]
        disp_sum += w * (hi - lo)
        under_sum += w * (2.0 / alpha) * max(y - hi, 0.0)
        over_sum += w * (2.0 / alpha) * max(lo - y, 0.0)

    under_sum += w0 * max(y - m, 0.0)
    over_sum += w0 * max(m - y, 0.0)

    dispersion = disp_sum / denom
    underprediction = under_sum / denom
    overprediction = over_sum / denom
    wis_value = dispersion + underprediction + overprediction

    if m == 0.0:
        relative_wis = "NO-DATA: the 0.5 quantile is zero"
    else:
        relative_wis = wis_value / abs(m)

    return {
        "wis": wis_value,
        "dispersion": dispersion,
        "underprediction": underprediction,
        "overprediction": overprediction,
        "n_intervals": K,
        "alphas": alphas,
        "ignored": ignored,
        "relative_wis": relative_wis,
    }


def band_hit(quantiles, y, lo_key="0.1", hi_key="0.9"):
    """Return True when y is inside the lo_key to hi_key band, else False or None."""
    if lo_key not in quantiles or hi_key not in quantiles:
        return None
    try:
        lo = float(quantiles[lo_key])
    except (TypeError, ValueError):
        raise ValueError(f"non-numeric quantile value for key {lo_key!r}: {quantiles[lo_key]!r}")
    try:
        hi = float(quantiles[hi_key])
    except (TypeError, ValueError):
        raise ValueError(f"non-numeric quantile value for key {hi_key!r}: {quantiles[hi_key]!r}")
    try:
        y_val = float(y)
    except (TypeError, ValueError):
        raise ValueError(f"non-numeric actual value: {y!r}")
    return lo <= y_val <= hi


def coverage(hits, nominal=0.8, z=1.96):
    """Wilson score interval for a list of boolean band hits."""
    for i, h in enumerate(hits):
        if not isinstance(h, bool):
            raise ValueError(f"hits[{i}] is not a bool")
    n = len(hits)
    hit_count = sum(1 for h in hits if h)

    if n == 0:
        cov = None
        lo = None
        hi = None
    else:
        cov = hit_count / n
        z2 = z * z
        denom = 1.0 + z2 / n
        center = (cov + z2 / (2.0 * n)) / denom
        margin = z * math.sqrt((cov * (1.0 - cov) / n) + (z2 / (4.0 * n * n))) / denom
        lo = max(0.0, center - margin)
        hi = min(1.0, center + margin)

    if n < 5:
        verdict = "NO-DATA: fewer than 5 resolved quantile claims"
    elif hi < nominal:
        verdict = "over-confident"
    elif lo > nominal:
        verdict = "under-confident"
    else:
        verdict = "calibrated"

    return {
        "n": n,
        "hits": hit_count,
        "coverage": cov,
        "lo": lo,
        "hi": hi,
        "nominal": nominal,
        "verdict": verdict,
    }


def _run_selftest():
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    def check_close(actual, expected, places, msg):
        if round(actual, places) != round(expected, places):
            errors.append(f"{msg}: got {actual!r}, expected {expected!r} to {places} places")

    def check_raises(exc_type, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except exc_type:
            return
        except Exception as e:
            errors.append(f"expected {exc_type.__name__} from {func.__name__}, got {type(e).__name__}: {e}")
            return
        errors.append(f"expected {exc_type.__name__} from {func.__name__}, got no exception")

    q = {"0.1": 80, "0.5": 100, "0.9": 130}
    res100 = wis(q, 100)
    check_close(res100["wis"], 3.3333, 4, "wis y=100")
    check_close(res100["dispersion"], 3.3333, 4, "dispersion y=100")
    check_close(res100["underprediction"], 0.0, 4, "underprediction y=100")
    check_close(res100["overprediction"], 0.0, 4, "overprediction y=100")
    check(res100["n_intervals"] == 1, "n_intervals y=100")
    check(res100["alphas"] == [0.2], "alphas y=100")
    check(res100["ignored"] == [], "ignored y=100")
    check_close(res100["relative_wis"], 0.03333333333333333, 10, "relative_wis y=100")

    res200 = wis(q, 200)
    check_close(res200["wis"], 83.3333, 4, "wis y=200")
    check_close(res200["dispersion"], 3.3333, 4, "dispersion y=200")
    check_close(res200["underprediction"], 80.0, 4, "underprediction y=200")
    check_close(res200["overprediction"], 0.0, 4, "overprediction y=200")
    check_close(
        res200["dispersion"] + res200["underprediction"] + res200["overprediction"],
        res200["wis"],
        9,
        "decomposition sum y=200",
    )

    check_close(interval_score(80, 130, 200, 0.2), 750.0, 4, "interval_score ref")
    check_close(pinball_loss(0.9, 130, 200), 63.0, 4, "pinball_loss ref")

    cov1 = coverage([True] * 8 + [False] * 2)
    check(cov1["verdict"] == "calibrated", "coverage calibrated verdict")
    check(cov1["n"] == 10, "coverage n")
    check(cov1["hits"] == 8, "coverage hits")
    check_close(cov1["coverage"], 0.8, 10, "coverage value")
    cov2 = coverage([True] * 5 + [False] * 15)
    check(cov2["verdict"] == "over-confident", "coverage over-confident verdict")
    cov3 = coverage([True] * 4)
    check(cov3["verdict"].startswith("NO-DATA"), "coverage no-data verdict")
    cov0 = coverage([])
    check(cov0["n"] == 0, "coverage n=0")
    check(cov0["coverage"] is None, "coverage None")
    check(cov0["lo"] is None, "coverage lo None")
    check(cov0["hi"] is None, "coverage hi None")
    check(cov0["verdict"].startswith("NO-DATA"), "coverage n=0 verdict")

    check(band_hit(q, 100) is True, "band_hit inside")
    check(band_hit(q, 200) is False, "band_hit outside")
    check(band_hit({}, 100) is None, "band_hit missing")
    check(band_hit({"0.1": "80", "0.9": "130"}, "100") is True, "band_hit numeric strings")
    check_raises(ValueError, band_hit, {"0.1": "bad", "0.9": 130}, 100)
    check_raises(ValueError, band_hit, {"0.1": 80, "0.9": 130}, "bad")

    check_raises(ValueError, pinball_loss, 0.0, 100, 100)
    check_raises(ValueError, pinball_loss, 1.0, 100, 100)
    check_raises(ValueError, pinball_loss, -0.1, 100, 100)
    check_raises(ValueError, pinball_loss, 1.1, 100, 100)
    check_raises(ValueError, interval_score, 10, 5, 7, 0.2)
    check_raises(ValueError, interval_score, 5, 10, 7, 0.0)
    check_raises(ValueError, interval_score, 5, 10, 7, 1.0)
    check_raises(ValueError, interval_score, 5, 10, 7, -0.1)
    check_raises(ValueError, interval_score, 5, 10, 7, 1.1)
    check_raises(ValueError, wis, {"bad": 80, "0.5": 100}, 100)
    check_raises(ValueError, wis, {"0.1": "bad", "0.5": 100}, 100)
    check_raises(ValueError, wis, {"0": 80, "0.5": 100}, 100)
    check_raises(ValueError, wis, {"1": 80, "0.5": 100}, 100)
    check_raises(ValueError, wis, {"-0.1": 80, "0.5": 100}, 100)
    check_raises(ValueError, wis, {"1.1": 80, "0.5": 100}, 100)
    check_raises(ValueError, wis, {"0.1": 80, "0.9": 130}, 100)
    check_raises(ValueError, wis, {"0.1": 100, "0.5": 90, "0.9": 130}, 100)
    check_raises(ValueError, wis, {"0.5": 100, "0.50": 100}, 100)
    check_raises(ValueError, wis, [("0.5", 100)], 100)

    res_zero = wis({"0.5": 0}, 10)
    check(res_zero["relative_wis"] == "NO-DATA: the 0.5 quantile is zero", "relative_wis zero")

    check_close(pinball_loss(0.1, 80, 100), 2.0, 10, "pinball_loss 0.1")
    check_close(pinball_loss(0.9, 130, 100), 3.0, 10, "pinball_loss 0.9")
    check_close(interval_score(80, 130, 100, 0.2), 50.0, 10, "interval_score inside")
    check_close(interval_score(80, 130, 70, 0.2), 150.0, 10, "interval_score below")
    check_close(interval_score(80, 130, 200, 0.2), 750.0, 10, "interval_score above")

    cov4 = coverage([True] * 9 + [False] * 1)
    check(cov4["verdict"] == "calibrated", "coverage calibrated 9/10")
    cov5 = coverage([True] * 1 + [False] * 9)
    check(cov5["verdict"] == "over-confident", "coverage over-confident 1/10")
    cov6 = coverage([True] * 10)
    check(cov6["verdict"] == "calibrated", "coverage all true 10")
    cov7 = coverage([True] * 100)
    check(cov7["verdict"] == "under-confident", "coverage under-confident 100")

    try:
        coverage([True, 1])
        errors.append("coverage non-bool should raise")
    except ValueError as e:
        if "hits[1]" not in str(e):
            errors.append("coverage non-bool message missing index")
    check_raises(ValueError, coverage, [True, None])
    check_raises(ValueError, coverage, [1, 0])

    return errors


def main(argv=None):
    if argv is None:
        argv = sys.argv

    if len(argv) >= 2 and argv[1] == "--selftest":
        errors = _run_selftest()
        if errors:
            for err in errors:
                print(f"SELFTEST FAIL: {err}")
            return 1
        print("SELFTEST PASS")
        return 0

    if len(argv) >= 2 and argv[1] == "wis":
        if len(argv) != 4:
            print("NO-DATA: usage: python3 forecast_score.py wis '<json quantiles>' <actual>")
            return 2
        try:
            quantiles = json.loads(argv[2])
        except Exception as e:
            print(f"NO-DATA: invalid JSON: {e}")
            return 2
        try:
            y = float(argv[3])
        except Exception as e:
            print(f"NO-DATA: invalid actual value: {e}")
            return 2
        try:
            result = wis(quantiles, y)
        except Exception as e:
            print(f"NO-DATA: {e}")
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print("NO-DATA: unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
