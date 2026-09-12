"""Deterministic PIPELINE evidence gates, registered through packs.py.

Only register() and selftest() are public. Findings are supplied by the
caller, so this module never imports the command-line engine.
"""
import datetime as _datetime
import math as _math
import re as _re


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return _math.isfinite(value)
    except OverflowError:
        return False


def _block(pipeline, key, Finding, gate, absent):
    value = pipeline.get(key)
    if _missing(value):
        return None, [Finding(gate, "NO-DATA", absent)]
    if not isinstance(value, dict):
        return None, [Finding(gate, "FAIL", "pipeline.%s must be an object" % key)]
    return value, []


def _contract(pipeline, Finding):
    gate = "P1.contract"
    block, findings = _block(pipeline, "contract", Finding, gate,
                            "no data contract names an owner or a schema for this number")
    if block is None:
        return findings
    missing = [k for k in ("version", "owner", "schema_hash") if _missing(block.get(k))]
    invalid = [k for k in ("version", "owner", "schema_hash")
               if k not in missing and not _text(block[k])]
    if missing or invalid:
        detail = []
        if missing:
            detail.append("pipeline.contract missing " + ", ".join(missing))
        if invalid:
            detail.append("pipeline.contract needs non-empty text for " + ", ".join(invalid))
        return [Finding(gate, "FAIL", "; ".join(detail))]
    return [Finding(gate, "PASS", "contract %s names owner %s and schema %s"
                    % (block["version"], block["owner"], block["schema_hash"]))]


def _checks(pipeline, Finding):
    gate = "P2.checks"
    checks = pipeline.get("checks")
    if _missing(checks) or checks == []:
        return [Finding(gate, "NO-DATA", "pipeline.checks has no quality-check results")]
    if not isinstance(checks, list):
        return [Finding(gate, "FAIL", "pipeline.checks must be a list")]
    invalid, missing, strong, weak = [], [], [], []
    for index, check in enumerate(checks):
        path = "pipeline.checks[%d]" % index
        if not isinstance(check, dict):
            invalid.append(path + " must be an object")
            continue
        for key in ("id", "name", "severity", "result"):
            if _missing(check.get(key)):
                missing.append(path + "." + key)
            elif not _text(check[key]):
                invalid.append(path + "." + key + " must be text")
        for key, allowed in (("severity", ("strong", "weak")),
                             ("result", ("pass", "fail", "skipped"))):
            if not _missing(check.get(key)) and check[key] not in allowed:
                invalid.append("%s.%s has invalid value %r" % (path, key, check[key]))
        if check.get("result") in ("fail", "skipped"):
            label = "%s (%s): %s" % (check.get("id", path), check.get("name", "unnamed"),
                                      check["result"])
            if check.get("severity") == "strong":
                strong.append(label)
            elif check.get("severity") == "weak":
                weak.append(label)
    detail = []
    if strong:
        detail.append("strong checks failed or skipped: %s; the claim must not proceed"
                      % ", ".join(strong))
    detail.extend(invalid)
    if missing:
        detail.append("missing " + ", ".join(missing))
    if weak:
        detail.append("weak checks need attention: " + ", ".join(weak))
    suite = pipeline.get("check_suite_id")
    if _missing(suite):
        detail.append("pipeline.check_suite_id NO-DATA")
    elif not _text(suite):
        invalid.append("pipeline.check_suite_id must be text")
        detail.append(invalid[-1])
    else:
        detail.append("check suite " + suite)
    passed = sum(check.get("result") == "pass" for check in checks if isinstance(check, dict))
    detail.insert(0, "%d/%d checks passed" % (passed, len(checks)))
    verdict = "FAIL" if strong or invalid else "NO-DATA" if missing else "PASS"
    return [Finding(gate, verdict, "; ".join(detail))]


def _upstream(claim, pipeline, Finding):
    if claim.get("origin") != "SYSTEM" or _missing(pipeline.get("run_id")):
        return []
    if not _text(pipeline["run_id"]):
        return [Finding("P3.upstream", "FAIL", "pipeline.run_id must be non-empty text")]
    upstream = pipeline.get("upstream_check_id")
    if _missing(upstream):
        return [Finding("P3.upstream", "NO-DATA",
                        "the upstream check that admitted this run is not named; "
                        "missing pipeline.upstream_check_id")]
    if not _text(upstream):
        return [Finding("P3.upstream", "FAIL", "pipeline.upstream_check_id must be non-empty text")]
    return [Finding("P3.upstream", "PASS", "run %s names upstream check %s"
                    % (pipeline["run_id"], upstream))]


def _timestamp(value):
    if not _text(value):
        raise ValueError("must be ISO 8601 text")
    return _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _reconciliation(pipeline, Finding):
    gate = "P4.reconciliation"
    block, findings = _block(pipeline, "reconciliation", Finding, gate,
                            "pipeline.reconciliation is absent; no ledger comparison is recorded")
    if block is None:
        return findings
    required = ("system_of_record", "window_start", "window_end", "variance", "variance_tolerance")
    missing = [k for k in required if _missing(block.get(k))]
    invalid = []
    for key in ("variance", "variance_tolerance"):
        if key not in missing and not _number(block[key]):
            invalid.append("%s must be a finite number" % key)
    if "variance_tolerance" not in missing and _number(block["variance_tolerance"]) and block["variance_tolerance"] < 0:
        invalid.append("variance_tolerance must be non-negative")
    if "system_of_record" not in missing and not _text(block["system_of_record"]):
        invalid.append("system_of_record must be non-empty text")
    times = {}
    for key in ("window_start", "window_end"):
        if key not in missing:
            try:
                times[key] = _timestamp(block[key])
            except ValueError:
                invalid.append("%s must be an ISO 8601 date or timestamp" % key)
    if len(times) == 2:
        try:
            if times["window_start"] > times["window_end"]:
                invalid.append("window_start is after window_end")
        except TypeError:
            invalid.append("window_start and window_end must use consistent timezone information")
    resolution = block.get("resolution_path")
    if not _missing(resolution) and resolution not in ("explained", "corrected", "escalated", "open"):
        invalid.append("resolution_path has invalid value %r" % resolution)
    if invalid:
        return [Finding(gate, "FAIL", "pipeline.reconciliation: " + "; ".join(invalid))]
    # A known unresolved discrepancy remains a refusal even when context is missing.
    numeric_missing = any(k in missing for k in ("variance", "variance_tolerance"))
    if not numeric_missing:
        variance, tolerance = block["variance"], block["variance_tolerance"]
        detail = "variance %s, tolerance %s" % (variance, tolerance)
        if abs(variance) > tolerance and (_missing(resolution) or resolution == "open"):
            return [Finding(gate, "FAIL", detail + "; discrepancy remains open with no resolution")]
    if missing:
        return [Finding(gate, "NO-DATA", "pipeline.reconciliation missing " + ", ".join(missing))]
    detail += "; %s from %s to %s" % (block["system_of_record"], block["window_start"], block["window_end"])
    if abs(variance) <= tolerance:
        detail += "; within tolerance"
    else:
        detail += "; outside tolerance, resolution " + resolution
    return [Finding(gate, "PASS", detail)]


def _freshness(pipeline, Finding):
    gate = "P5.freshness"
    block, findings = _block(pipeline, "freshness", Finding, gate,
                            "pipeline.freshness is absent; arrival against the deadline is unknown")
    if block is None:
        return findings
    missing = [k for k in ("expected_by", "arrived_at") if _missing(block.get(k))]
    parsed, invalid = {}, []
    for key in ("expected_by", "arrived_at"):
        if key not in missing:
            try:
                parsed[key] = _timestamp(block[key])
            except ValueError:
                invalid.append(key)
    if invalid:
        return [Finding(gate, "FAIL", "pipeline.freshness needs ISO 8601 timestamps for " + ", ".join(invalid))]
    if missing:
        return [Finding(gate, "NO-DATA", "pipeline.freshness missing " + ", ".join(missing))]
    try:
        late = parsed["arrived_at"] > parsed["expected_by"]
    except TypeError:
        return [Finding(gate, "FAIL", "pipeline.freshness timestamps must use consistent timezone information")]
    return [Finding(gate, "FAIL" if late else "PASS", "arrived_at %s %s expected_by %s"
                    % (block["arrived_at"], "is after" if late else "is on or before", block["expected_by"]))]


def _disclosure(pipeline, Finding):
    block = pipeline.get("disclosure")
    keys = ("rights_restriction", "source", "cost_basis", "owner", "retention")
    if not isinstance(block, dict):
        block = {}
    present = {k: not _missing(block.get(k)) and block[k] not in ([], {}) for k in keys}
    detail = "; ".join("%s %s" % (k, "present" if present[k] else "NO-DATA") for k in keys)
    return [Finding("P6.disclosure", "PASS" if all(present.values()) else "NO-DATA", detail)]


def _node_receipts(claim, pipeline, Finding):
    statement = str(claim.get("statement") or "").lower()
    success = _re.search(r"\b(?:workflow|pipeline)\s+(?:ran|succeeded|passed|completed|was successful)\b", statement)
    receipts = pipeline.get("node_receipts")
    if _missing(receipts) or receipts == []:
        if success:
            return [Finding("P7.node_receipts", "NO-DATA",
                            "a boolean success with no per-node trace; missing pipeline.node_receipts")]
        return []
    if not isinstance(receipts, list):
        return [Finding("P7.node_receipts", "FAIL", "pipeline.node_receipts must be a list")]
    missing, invalid = [], []
    for index, node in enumerate(receipts):
        path = "pipeline.node_receipts[%d]" % index
        if not isinstance(node, dict):
            invalid.append(path + " must be an object")
            continue
        if _missing(node.get("node_id")):
            missing.append(path + ".node_id")
        elif not _text(node["node_id"]):
            invalid.append(path + ".node_id must be text")
        if all(_missing(node.get(k)) for k in ("hash", "run_id")):
            missing.append(path + ".hash or run_id")
        for key in ("hash", "run_id"):
            if not _missing(node.get(key)) and not _text(node[key]):
                invalid.append(path + "." + key + " must be text")
    if invalid:
        return [Finding("P7.node_receipts", "FAIL", "; ".join(invalid))]
    if missing:
        return [Finding("P7.node_receipts", "NO-DATA", "missing " + ", ".join(missing))]
    return [Finding("P7.node_receipts", "PASS", "%d node receipts name a node and its hash or run_id" % len(receipts))]


def _contamination(claim, pipeline, Finding):
    rate = pipeline.get("contamination_rate")
    if _missing(rate):
        statement = str(claim.get("statement") or "").lower()
        if any(word in statement for word in ("ingested", "loaded", "imported")):
            return [Finding("P8.contamination", "NO-DATA", "bulk-ingest claim missing pipeline.contamination_rate")]
        return []
    if not _number(rate) or not 0 <= rate <= 1:
        return [Finding("P8.contamination", "FAIL", "pipeline.contamination_rate must be a finite share from 0 to 1")]
    above = rate > 0.05
    return [Finding("P8.contamination", "FAIL" if above else "PASS",
                    "contamination_rate %s is %s the 5 percent refusal threshold"
                    % (rate, "above" if above else "at or below"))]


def _findings(claim, Finding):
    pipeline = claim.get("pipeline")
    if _missing(pipeline):
        pipeline = {}
    if not isinstance(pipeline, dict):
        gates = ("P1.contract", "P2.checks", "P3.upstream", "P4.reconciliation",
                 "P5.freshness", "P7.node_receipts", "P8.contamination")
        return [Finding(gate, "FAIL", "pipeline must be an object") for gate in gates] + _disclosure({}, Finding)
    return (_contract(pipeline, Finding) + _checks(pipeline, Finding)
            + _upstream(claim, pipeline, Finding) + _reconciliation(pipeline, Finding)
            + _freshness(pipeline, Finding) + _disclosure(pipeline, Finding)
            + _node_receipts(claim, pipeline, Finding) + _contamination(claim, pipeline, Finding))


def register(packs_module, Finding):
    packs_module.register("PIPELINE", _findings)


def selftest(expect):
    import copy
    import packs

    class _Finding:
        def __init__(self, gate, verdict, detail):
            self.gate, self.verdict, self.detail = gate, verdict, detail

    base = {
        "claim_type": "PIPELINE", "origin": "SYSTEM",
        "statement": "The pipeline succeeded and loaded 100 synthetic records.",
        "pipeline": {
            "contract": {"version": "1", "owner": "synthetic steward", "schema_hash": "synthetic-schema-v1"},
            "checks": [{"id": "C1", "name": "row count", "severity": "strong", "result": "pass"}],
            "check_suite_id": "synthetic-suite", "run_id": "synthetic-run",
            "upstream_check_id": "synthetic-admission",
            "reconciliation": {"system_of_record": "synthetic ledger", "window_start": "2026-09-10",
                               "window_end": "2026-09-11", "variance": 100, "variance_tolerance": 1000,
                               "resolution_path": "open"},
            "freshness": {"expected_by": "2026-09-11T09:00:00Z", "arrived_at": "2026-09-11T08:00:00Z"},
            "disclosure": {k: "synthetic" for k in ("rights_restriction", "source", "cost_basis", "owner", "retention")},
            "node_receipts": [{"node_id": "load", "run_id": "synthetic-run"}],
            "contamination_rate": 0.01,
        },
    }

    def result(gate, pipeline=None, claim=None):
        item = copy.deepcopy(base) if claim is None else claim
        if pipeline is not None:
            item["pipeline"] = pipeline
        return next(f for f in _findings(item, _Finding) if f.gate == gate)

    ok = True
    for finding in _findings(base, _Finding):
        ok &= expect(finding.verdict == "PASS", "%s complete pipeline must pass" % finding.gate)
    ok &= expect(result("P1.contract", {}).verdict == "NO-DATA", "P1 absent contract is NO-DATA")
    ok &= expect(result("P1.contract", {"contract": {"version": "1", "schema_hash": "x"}}).verdict == "FAIL",
                 "P1 contract without owner must fail")
    ok &= expect("owner" in result("P1.contract", {"contract": {}}).detail, "P1 names missing owner")
    ok &= expect(result("P2.checks", {}).verdict == "NO-DATA", "P2 absent checks is NO-DATA")
    for severity, verdict in (("strong", "FAIL"), ("weak", "PASS")):
        for status in ("fail", "skipped"):
            p = {"checks": [{"id": "C1", "name": "row count", "severity": severity, "result": status}]}
            f = result("P2.checks", p)
            ok &= expect(f.verdict == verdict and "C1" in f.detail and status in f.detail,
                         "P2 %s %s must be %s with annotation" % (severity, status, verdict))
            if severity == "strong":
                ok &= expect("must not proceed" in f.detail, "P2 strong refusal must stop the claim")
    p = copy.deepcopy(base["pipeline"])
    p["checks"][0]["result"] = "unknown"
    ok &= expect(result("P2.checks", p).verdict == "FAIL", "P2 invalid result must fail")
    del p["checks"][0]["result"]
    ok &= expect(result("P2.checks", p).verdict == "NO-DATA", "P2 missing result is NO-DATA")
    p = {"run_id": "synthetic-run"}
    ok &= expect(result("P3.upstream", p).verdict == "NO-DATA", "P3 SYSTEM run without upstream is NO-DATA")
    for variance in (1200, -1200):
        for resolution, verdict in (("open", "FAIL"), (None, "FAIL"), ("explained", "PASS"),
                                    ("corrected", "PASS"), ("escalated", "PASS")):
            p = copy.deepcopy(base["pipeline"])
            p["reconciliation"].update(variance=variance, resolution_path=resolution)
            f = result("P4.reconciliation", p)
            ok &= expect(f.verdict == verdict and str(variance) in f.detail and "1000" in f.detail,
                         "P4 variance %s resolution %s must be %s" % (variance, resolution, verdict))
    p["reconciliation"]["variance"] = 1000
    p["reconciliation"]["resolution_path"] = "open"
    ok &= expect(result("P4.reconciliation", p).verdict == "PASS", "P4 tolerance boundary must pass")
    for value in (float("nan"), float("inf"), True, "1200"):
        p["reconciliation"]["variance"] = value
        ok &= expect(result("P4.reconciliation", p).verdict == "FAIL", "P4 invalid variance must fail")
    p = copy.deepcopy(base["pipeline"])
    del p["reconciliation"]["window_start"]
    ok &= expect(result("P4.reconciliation", p).verdict == "NO-DATA", "P4 missing window is NO-DATA")
    p = {"freshness": {"expected_by": "2026-09-11T09:00:00Z", "arrived_at": "2026-09-11T09:00:01Z"}}
    ok &= expect(result("P5.freshness", p).verdict == "FAIL", "P5 late arrival must fail")
    p["freshness"]["arrived_at"] = "2026-09-11T11:00:00+02:00"
    ok &= expect(result("P5.freshness", p).verdict == "PASS", "P5 equal instants across offsets must pass")
    p["freshness"]["arrived_at"] = "yesterday"
    ok &= expect(result("P5.freshness", p).verdict == "FAIL", "P5 invalid timestamp must fail")
    f = result("P6.disclosure", {"disclosure": {"owner": "synthetic steward"}})
    ok &= expect(f.verdict == "NO-DATA" and "owner present" in f.detail and "retention NO-DATA" in f.detail,
                 "P6 disclosure names both present and missing fields without refusing")
    ok &= expect(result("P6.disclosure", {"disclosure": 1}).verdict == "NO-DATA", "P6 malformed disclosure never fails")
    for receipts in (None, []):
        f = result("P7.node_receipts", {"node_receipts": receipts})
        ok &= expect(f.verdict == "NO-DATA" and "a boolean success with no per-node trace" in f.detail,
                     "P7 workflow success needs node receipts")
    for receipts, verdict in (([{"node_id": "load", "hash": "synthetic-hash"}], "PASS"),
                              ([{"node_id": "load"}], "NO-DATA"), ([True], "FAIL")):
        ok &= expect(result("P7.node_receipts", {"node_receipts": receipts}).verdict == verdict,
                     "P7 validates node receipt identity and trace")
    ok &= expect(result("P8.contamination", {}).verdict == "NO-DATA", "P8 bulk ingest needs contamination_rate")
    for rate, verdict in ((0, "PASS"), (0.05, "PASS"), (0.050001, "FAIL"), (-0.1, "FAIL"),
                          (1.1, "FAIL"), (True, "FAIL"), (float("nan"), "FAIL"), (float("inf"), "FAIL")):
        ok &= expect(result("P8.contamination", {"contamination_rate": rate}).verdict == verdict,
                     "P8 contamination %s must be %s" % (rate, verdict))
    other = copy.deepcopy(base)
    other["claim_type"] = "DESCRIPTIVE"
    ok &= expect(not packs.run_packs("DESCRIPTIVE", other, _Finding), "pipeline pack never runs for DESCRIPTIVE")
    seen = []
    class _Registry:
        def register(self, claim_type, fn):
            seen.append((claim_type, fn))
    register(_Registry(), _Finding)
    ok &= expect(seen == [("PIPELINE", _findings)], "pipeline pack registers only for PIPELINE")
    before = copy.deepcopy(base)
    _findings(base, _Finding)
    ok &= expect(base == before, "pipeline checks never mutate the claim")
    return bool(ok)
