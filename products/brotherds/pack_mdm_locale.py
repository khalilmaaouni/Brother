"""MASTER_DATA normalization gate for the brotherds pack.

Checks a claim's declared normalization layer before it reaches a decision.
Python 3.9 standard library only.
"""

import sys

import mdm_normalize

GATE = "M22.normalization"

REQUIRED_STEPS = mdm_normalize.REQUIRED_STEPS

_JA_MISSING_NOTE = (
    "without them width, kana and variant-kanji differences split one customer into several"
)

_NO_DATA_DETAIL = (
    "no normalization declared; a match rate over unnormalized keys cannot be compared"
)

_PROBE_KINDS = ("company", "person", "phone")


def check_normalization(claim, Finding):
    """Return the list of findings for the M22.normalization gate."""
    if not isinstance(claim, dict):
        return []
    md = claim.get("master_data")
    if not isinstance(md, dict):
        return []
    if "normalization" not in md:
        return [Finding(GATE, "NO-DATA", _NO_DATA_DETAIL)]
    norm = md["normalization"]
    if norm is None:
        return [Finding(GATE, "NO-DATA", _NO_DATA_DETAIL)]
    if not isinstance(norm, dict):
        return [Finding(GATE, "FAIL", "normalization: must be an object")]

    locale = norm.get("locale")
    if locale not in REQUIRED_STEPS:
        return [Finding(
            GATE, "FAIL",
            "normalization.locale: unknown locale: " + repr(locale),
        )]

    steps = norm.get("steps")
    if not isinstance(steps, list):
        return [Finding(GATE, "FAIL", "normalization.steps: must be a list")]
    for idx, step in enumerate(steps):
        if not isinstance(step, str):
            return [Finding(
                GATE, "FAIL",
                "normalization.steps: step at index " + str(idx) + " must be a string",
            )]

    required = REQUIRED_STEPS[locale]
    missing = [name for name in required if name not in steps]
    if missing:
        detail = (
            "normalization.steps: missing required steps for " + locale + ": "
            + ", ".join(missing)
        )
        if locale == "ja":
            detail += "; " + _JA_MISSING_NOTE
        return [Finding(GATE, "FAIL", detail)]

    probes = norm.get("probes")
    if probes is None:
        probes = {}
    if not isinstance(probes, dict):
        return [Finding(GATE, "FAIL", "normalization.probes: must be an object")]

    totals = []
    for kind in _PROBE_KINDS:
        if kind not in probes:
            continue
        pairs = probes[kind]
        if not isinstance(pairs, list):
            return [Finding(
                GATE, "FAIL",
                "normalization.probes." + kind + ": must be a list",
            )]
        for idx, pair in enumerate(pairs):
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not isinstance(pair[1], str)
            ):
                return [Finding(
                    GATE, "FAIL",
                    "normalization.probes." + kind + ": probe at index "
                    + str(idx) + " must be a pair of strings",
                )]
        result = mdm_normalize.probe_pairs(list(pairs), kind, locale)
        unequal = result["n"] - result["equal"]
        if unequal:
            return [Finding(
                GATE, "FAIL",
                "normalization.probes." + kind + ": " + str(unequal)
                + " of " + str(result["n"])
                + " probe pairs are unequal under the reference keys",
            )]
        totals.append((kind, result["n"], result["equal"]))

    step_text = ", ".join(steps) if steps else "none"
    if totals:
        total_n = sum(n for _, n, _ in totals)
        per_kind = ", ".join(
            kind + " " + str(equal) + "/" + str(n)
            for kind, n, equal in totals
        )
        probe_text = str(total_n) + " pairs (" + per_kind + ")"
    else:
        probe_text = "none"
    detail = "locale " + locale + "; steps: " + step_text + "; probes: " + probe_text
    return [Finding(GATE, "PASS", detail)]


def register(packs_module, Finding):
    """Register the MASTER_DATA gate through the pack contract, once."""
    packs_module.register("MASTER_DATA", check_normalization)


def selftest(expect):
    ok = True

    class Finding:
        def __init__(self, gate, verdict, detail):
            self.gate = gate
            self.verdict = verdict
            self.detail = detail

        def __repr__(self):
            return "Finding(%r, %r, %r)" % (self.gate, self.verdict, self.detail)

    def run(claim):
        return check_normalization(claim, Finding)

    ja_steps = ["nfkc", "space", "long_vowel", "itaiji", "small_ke", "corporate"]
    latin_steps = ["nfkc", "space", "fold", "corporate"]

    ok &= expect(run({}) == [], "empty claim yields no findings")
    ok &= expect(run({"other": 1}) == [], "claim without master_data yields no findings")
    ok &= expect(run({"master_data": "x"}) == [], "non-dict master_data yields no findings")

    fs = run({"master_data": {}})
    ok &= expect(len(fs) == 1, "absent normalization yields one finding")
    ok &= expect(fs[0].verdict == "NO-DATA", "absent normalization is NO-DATA")
    ok &= expect(fs[0].gate == GATE, "gate name is M22.normalization")
    ok &= expect(fs[0].detail == _NO_DATA_DETAIL, "NO-DATA detail text is exact")

    fs = run({"master_data": {"normalization": None}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "NO-DATA", "null normalization is NO-DATA")

    fs = run({"master_data": {"normalization": "nope"}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "non-object normalization is FAIL")
    ok &= expect("normalization" in fs[0].detail, "non-object normalization names the field")

    fs = run({"master_data": {"normalization": {"locale": "fr", "steps": []}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "unknown locale is FAIL")
    ok &= expect("fr" in fs[0].detail, "unknown locale names the locale")
    ok &= expect("locale" in fs[0].detail, "unknown locale names the field")

    fs = run({"master_data": {"normalization": {"locale": "ja", "steps": "nfkc"}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "steps not a list is FAIL")
    ok &= expect("steps" in fs[0].detail, "steps not a list names the field")

    fs = run({"master_data": {"normalization": {"locale": "ja", "steps": ["nfkc", 5]}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "non-string step is FAIL")
    ok &= expect("steps" in fs[0].detail, "non-string step names the field")

    fs = run({"master_data": {"normalization": {"locale": "ja", "steps": ["nfkc"]}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "missing ja steps is FAIL")
    ok &= expect("itaiji" in fs[0].detail and "small_ke" in fs[0].detail,
                 "missing ja steps are named")
    ok &= expect(
        "width, kana and variant-kanji differences split one customer into several"
        in fs[0].detail,
        "missing ja steps explains the split consequences",
    )

    fs = run({"master_data": {"normalization": {"locale": "latin", "steps": ["nfkc"]}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "missing latin steps is FAIL")
    ok &= expect("space" in fs[0].detail and "fold" in fs[0].detail,
                 "missing latin steps are named")
    ok &= expect("width, kana" not in fs[0].detail, "latin missing steps does not mention kana")

    fs = run({"master_data": {"normalization": {
        "locale": "ja", "steps": ja_steps, "probes": []}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "probes not an object is FAIL")
    ok &= expect("probes" in fs[0].detail, "probes not an object names the field")

    fs = run({"master_data": {"normalization": {
        "locale": "ja", "steps": ja_steps, "probes": {"company": "x"}}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "probe kind not a list is FAIL")
    ok &= expect("company" in fs[0].detail, "probe kind not a list names the kind")

    fs = run({"master_data": {"normalization": {
        "locale": "ja", "steps": ja_steps, "probes": {"company": [["A"]]}}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "short probe pair is FAIL")
    ok &= expect("company" in fs[0].detail, "short probe pair names the kind")

    fs = run({"master_data": {"normalization": {
        "locale": "ja", "steps": ja_steps, "probes": {"person": [["A", 5]]}}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "non-string probe is FAIL")

    fs = run({"master_data": {"normalization": {
        "locale": "latin", "steps": latin_steps,
        "probes": {"company": [["ABC", "XYZ"]]}}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "FAIL", "unequal probe is FAIL")
    ok &= expect("company" in fs[0].detail, "unequal probe names the kind")
    ok &= expect("1" in fs[0].detail, "unequal probe reports the count")

    fs = run({"master_data": {"normalization": {
        "locale": "ja", "steps": ja_steps, "probes": {
            "company": [["株式会社アイウ", "(株)アイウ"]],
            "person": [["山崎 花子", "山崎花子"]],
            "phone": [["03-1234-5678", "+81 3 1234 5678"]],
        }}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "PASS", "valid ja claim passes")
    ok &= expect("nfkc" in fs[0].detail and "corporate" in fs[0].detail,
                 "PASS lists the steps")
    ok &= expect("3 pairs" in fs[0].detail, "PASS reports the total probe count")
    ok &= expect("company 1/1" in fs[0].detail, "PASS reports company probe totals")
    ok &= expect(
        "person 1/1" in fs[0].detail and "phone 1/1" in fs[0].detail,
        "PASS reports person and phone probe totals",
    )

    fs = run({"master_data": {"normalization": {
        "locale": "latin", "steps": latin_steps}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "PASS",
                 "valid latin claim without probes passes")
    ok &= expect("none" in fs[0].detail, "PASS without probes reports no probe totals")

    fs = run({"master_data": {"normalization": {
        "locale": "latin", "steps": latin_steps,
        "probes": {"person": [["Muller", "MULLER"]]}}}})
    ok &= expect(len(fs) == 1 and fs[0].verdict == "PASS",
                 "equal latin person probe passes")
    ok &= expect("1 pairs" in fs[0].detail, "PASS reports a single probe pair count")

    return ok


if __name__ == "__main__":
    failures = []

    def expect(cond, msg):
        cond = bool(cond)
        if not cond:
            failures.append(msg)
        return cond

    ok = selftest(expect)
    if ok and not failures:
        print("SELFTEST PASS")
        sys.exit(0)
    for msg in failures:
        print("SELFTEST FAIL: " + msg)
    sys.exit(1)
