"""Identifier and supplied audit metadata checks for master data claims.

These gates check declared evidence consistency. A digest and a seed in JSON
are not an independently executed replay or proof of authentic provenance.
"""
import copy
import re

GATE31 = "M31.identifier_validity"
GATE32 = "M32.audit_provenance"
KINDS = ("corporate_number", "invoice_number")
COUNTERS = ("n", "empty", "valid", "invalid", "duplicates", "matched_invalid")


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _gate_m31(audit):
    computed = audit.get("computed")
    if computed is None:
        return "NO-DATA", "no identifier columns in the audited results"
    if not isinstance(computed, dict):
        return "FAIL", "computed must be an object"
    identifiers = computed.get("identifiers")
    if identifiers is None or identifiers == {}:
        return "NO-DATA", "no identifier columns in the audited results"
    if not isinstance(identifiers, dict):
        return "FAIL", "computed.identifiers must be an object"
    if any(kind not in KINDS for kind in identifiers):
        return "FAIL", "computed.identifiers contains an unsupported identifier kind"
    if "identifiers_acknowledged" in audit and not isinstance(audit["identifiers_acknowledged"], bool):
        return "FAIL", "identifiers_acknowledged must be a boolean"
    n_rows = computed.get("n_rows")
    if "n_rows" in computed and not _integer(n_rows):
        return "FAIL", "computed.n_rows must be a nonnegative integer"
    problems, clauses = [], []
    populations = set()
    for kind in sorted(identifiers):
        block = identifiers[kind]
        if not isinstance(block, dict):
            return "FAIL", kind + " must be an object"
        for field in COUNTERS:
            if not _integer(block.get(field)):
                return "FAIL", "%s.%s must be a nonnegative integer" % (kind, field)
        n, empty, valid, invalid = (block[f] for f in ("n", "empty", "valid", "invalid"))
        populations.add(n)
        if empty + valid + invalid != n:
            return "FAIL", kind + ".n must equal empty + valid + invalid"
        if n_rows is not None and n != n_rows:
            return "FAIL", kind + ".n does not match computed.n_rows"
        if block["matched_invalid"] > invalid:
            return "FAIL", kind + ".matched_invalid exceeds invalid"
        if block["duplicates"] > max(valid - 1, 0):
            return "FAIL", kind + ".duplicates exceeds non-empty population"
        reasons = block.get("reasons")
        if not isinstance(reasons, dict):
            return "FAIL", kind + ".reasons must be an object"
        for reason, count in reasons.items():
            if not isinstance(reason, str) or not reason.strip() or not _integer(count):
                return "FAIL", kind + ".reasons requires named nonnegative integer counts"
        if sum(reasons.values()) != invalid:
            return "FAIL", kind + ".reasons counts do not sum to invalid"
        if block["matched_invalid"]:
            problems.append("%d MATCH row(s) carry a %s that fails its format or check digit; "
                            "a match anchored on an invalid identifier is not an anchor"
                            % (block["matched_invalid"], kind))
        if invalid and audit.get("identifiers_acknowledged") is not True:
            reason_text = ", ".join("%s=%d" % (reason, reasons[reason]) for reason in sorted(reasons))
            problems.append("%d of %d non-empty %s value(s) are invalid (reasons: %s)"
                            % (invalid, n - empty, kind, reason_text))
        clauses.append("%s: %d valid, %d empty, %d duplicate(s)" %
                       (kind, valid, empty, block["duplicates"]))
    if len(populations) != 1:
        return "FAIL", "identifier n totals disagree across columns"
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", "; ".join(clauses) + " (duplicates are expected where several outlets belong to one company)"


def _gate_m32(audit):
    computed = audit.get("computed")
    if computed is None or computed == {}:
        return "NO-DATA", "no computed audit"
    if not isinstance(computed, dict):
        return "FAIL", "computed must be an object"
    problems = []
    digest = computed.get("input_sha256")
    if digest is None:
        problems.append("input_sha256: the audit was not run from a file, so its input cannot be tied to an export")
    elif not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        problems.append("input_sha256 must contain exactly 64 hexadecimal characters")
    reviewed = any(key in audit for key in ("pathway_review", "unmatched_review"))
    for key in ("pathway_review", "unmatched_review"):
        if key in audit and not isinstance(audit[key], dict):
            problems.append(key + " must be an object")
    seed = audit.get("plan_seed")
    if reviewed or "plan_seed" in audit:
        if not isinstance(seed, int) or isinstance(seed, bool):
            problems.append("plan_seed: the review plan seed is not recorded as an integer")
    provenance = computed.get("provenance")
    present, missing = [], []
    if provenance is None:
        problems.append("provenance: reference_snapshot is not affirmatively recorded")
    elif not isinstance(provenance, dict):
        problems.append("provenance must be an object")
    else:
        for field in ("present", "missing"):
            names = provenance.get(field)
            if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
                problems.append("provenance.%s must be a list of nonempty names" % field)
                continue
            if len(set(names)) != len(names):
                problems.append("provenance.%s contains duplicate names" % field)
            if field == "present":
                present = names
            else:
                missing = names
        if set(present) & set(missing):
            problems.append("provenance.present and provenance.missing contradict each other")
        if "reference_snapshot" not in present or "reference_snapshot" in missing:
            problems.append("reference_snapshot is not affirmatively recorded on the results")
    verifier = audit.get("verifier")
    if verifier is not None and not isinstance(verifier, dict):
        problems.append("verifier must be an object")
    elif isinstance(verifier, dict):
        kind = verifier.get("kind")
        if kind is not None and not isinstance(kind, str):
            problems.append("verifier.kind must be text")
        elif isinstance(kind, str) and kind.strip().lower() in ("llm", "model"):
            if "model_version" not in present or "model_version" in missing:
                problems.append("model_version: the verifier model version is not affirmatively recorded")
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", ("supplied metadata consistent: input sha256 %s, plan seed %s, provenance present: %s; "
                    "independent replay and authenticity not checked" %
                    (digest[:12], seed if reviewed else "not used", ", ".join(present)))


def register(packs_module, Finding):
    def fn(claim, Finding):
        if not isinstance(claim, dict):
            return []
        master = claim.get("master_data")
        if not isinstance(master, dict) or not isinstance(master.get("audit"), dict):
            return []
        findings = []
        for gate, check in ((GATE31, _gate_m31), (GATE32, _gate_m32)):
            try:
                verdict, detail = check(master["audit"])
            except Exception as exc:
                verdict, detail = "FAIL", "malformed input: %s" % exc
            findings.append(Finding(gate, verdict, detail))
        return findings
    packs_module.register("MASTER_DATA", fn)


def selftest(expect):
    class Finding:
        def __init__(self, gate, verdict, detail):
            self.gate, self.verdict, self.detail = gate, verdict, detail

    class Registry:
        def __init__(self):
            self.calls = []
        def register(self, name, fn):
            self.calls.append((name, fn))

    registry = Registry()
    register(registry, Finding)
    ok = expect(len(registry.calls) == 1 and registry.calls[0][0] == "MASTER_DATA", "register once")
    fn = registry.calls[0][1]
    for claim in ({}, {"master_data": None}, {"master_data": {}},
                  {"master_data": {"audit": None}}, {"master_data": {"audit": []}}):
        ok &= expect(fn(claim, Finding) == [], "nonapplicable claim")
    empty = fn({"master_data": {"audit": {}}}, Finding)
    ok &= expect([f.gate for f in empty] == [GATE31, GATE32], "gate order")
    ok &= expect(all(f.verdict == "NO-DATA" for f in empty), "empty audit has no data")
    block = {"n": 10, "empty": 1, "valid": 9, "invalid": 0,
             "duplicates": 2, "matched_invalid": 0, "reasons": {}}
    audit = {"computed": {"identifiers": {"corporate_number": block},
                         "input_sha256": "a" * 64,
                         "provenance": {"present": ["reference_snapshot"], "missing": []}}}
    ok &= expect(_gate_m31(audit)[0] == "PASS", "valid identifiers")
    ok &= expect(_gate_m32(audit)[0] == "PASS", "consistent supplied provenance")
    for field in COUNTERS:
        broken = copy.deepcopy(audit)
        broken["computed"]["identifiers"]["corporate_number"][field] = True
        ok &= expect(_gate_m31(broken)[0] == "FAIL", "boolean counter " + field)
    for digest in (None, "", "f" * 63, "g" * 64, True):
        broken = copy.deepcopy(audit)
        broken["computed"]["input_sha256"] = digest
        ok &= expect(_gate_m32(broken)[0] == "FAIL", "invalid digest")
    for seed in (True, 1.5, "7", None):
        broken = copy.deepcopy(audit)
        broken.update(pathway_review={}, plan_seed=seed)
        ok &= expect(_gate_m32(broken)[0] == "FAIL", "strict seed")
    broken = copy.deepcopy(audit)
    broken["verifier"] = {"kind": "model"}
    ok &= expect(_gate_m32(broken)[0] == "FAIL", "model version required")
    broken["computed"]["provenance"]["present"].append("model_version")
    ok &= expect(_gate_m32(broken)[0] == "PASS", "model metadata present")
    broken = copy.deepcopy(audit)
    broken["computed"]["identifiers"]["corporate_number"].update(valid=8, invalid=1, matched_invalid=1, reasons={"check digit": 1})
    broken["identifiers_acknowledged"] = True
    ok &= expect(_gate_m31(broken)[0] == "FAIL", "invalid anchor cannot be acknowledged")
    ok &= expect("independent replay and authenticity not checked" in _gate_m32(audit)[1], "claim limited to metadata")
    return bool(ok)


if __name__ == "__main__":
    def expect(condition, message):
        if not condition:
            print("SELFTEST FAIL: " + message)
        return bool(condition)
    if selftest(expect):
        print("SELFTEST PASS")
    else:
        raise SystemExit(1)
