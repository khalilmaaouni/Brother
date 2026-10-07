#!/usr/bin/env python3
"""Jev's seven question checklist on a spec, turned into ADVICE lines for a worker brief.
usage: jev_brief.py <spec.md> [<out.json>]        jev_brief.py --selftest
Owner 2026-09-22: "Use Jev inside of the loop to help improve yield too". Jev is a typed decision model (noul questions
here); it never decides whether a build runs. A question answered NO, or a negatively phrased one answered YES, becomes one
line the worker must address. Abstain adds nothing. Any failure of the call is NO-DATA (exit 3, empty stdout): a brief
never waits on Jev and never carries a guessed flag. Run from the repository root (jev_decide.py is versioned there)."""
import json, os, subprocess, sys
Q = {
    "every_subunit_has_a_runnable_done_check": ("noul", "Does EVERY sub-unit in this specification state a concrete, runnable done-check command?", False),
    "every_failure_states_its_direction": ("noul", "For the failures it lists, does the specification state which way each one fails (blocks, refuses, NO-DATA), never a silent pass?", False),
    "callers_are_counted": ("noul", "Does the specification name the call sites it changes and give a caller count?", False),
    "each_safety_property_has_a_named_mutation": ("noul", "Does the specification name, for each safety property, the exact mutation that must make a test go red?", False),
    "acceptance_thresholds_carry_numbers": ("noul", "Are the acceptance thresholds measurable, with actual numbers, rather than words like fast or robust?", False),
    "loosens_a_gate_scan_cap_or_human_decision": ("noul", "Does ANY requirement in this specification loosen, skip, bypass or automate away a gate, a scan, a cap or a human decision?", True),
    "leaves_a_core_requirement_vague": ("noul", "Does the specification leave any core requirement so vague that two engineers would build different things?", True),
}
ADVICE = {
    "every_subunit_has_a_runnable_done_check": "state one runnable done-check command for the sub unit and make your tests satisfy it",
    "every_failure_states_its_direction": "every failure path must BLOCK, refuse or return NO-DATA; never a silent pass",
    "callers_are_counted": "grep every caller of anything you change and update all of them in the same edit",
    "each_safety_property_has_a_named_mutation": "for each guard name the mutation that makes a test red, and add that test",
    "acceptance_thresholds_carry_numbers": "use the spec's numbers as literal assertions; where none is given, choose one and state it",
    "loosens_a_gate_scan_cap_or_human_decision": "the spec touches a gate, scan, cap or human decision: never weaken it; refuse rather than loosen",
    "leaves_a_core_requirement_vague": "a core requirement is vague: pick the strictest reading and say which you chose in the build's unknowns",
}


def flags_from_records(records):
    """The question ids whose typed answer is a flag: NO (p <= 0.2) for a positive question, YES (p >= 0.8) for a negative one.
    Unknown ids, missing or non numeric probabilities and abstentions add nothing."""
    out = []
    for d in records if isinstance(records, list) else []:
        if not isinstance(d, dict) or d.get("id") not in Q: continue
        p = d.get("probability")
        if isinstance(p, bool) or not isinstance(p, (int, float)): continue
        negative = Q[d["id"]][2]
        if (negative and p >= 0.8) or (not negative and p <= 0.2): out.append(d["id"])
    return out


def advice(flags):
    if not flags: return ""
    return "JEV FLAGS on the spec (typed checklist, advisory; address each in the build):\n" + "\n".join("- %s: %s" % (f, ADVICE[f]) for f in flags)


def ask(spec_text, runner=None, timeout=300):
    """Records from jev_decide, or None on any failure (never a partial list)."""
    payload = {"state": "A software specification for one work unit:\n\n" + spec_text[:60000],
               "questions": {k: {"type": v[0], "instructions": v[1]} for k, v in Q.items()}}
    try:
        r = (runner or subprocess.run)([sys.executable, "-B", "scripts/jev_decide.py", "--family", "loop-brief"], input=json.dumps(payload),
                                       capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip(): return None
    try:
        recs = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    except ValueError:   # sbe: allow-silent a result that is not JSON is no verdict; the caller reads None as NO-DATA, never as a pass
        return None
    return recs if len(recs) == len(Q) else None


def main():
    if "--selftest" in sys.argv: return selftest()
    if len(sys.argv) < 2: print("usage: jev_brief.py <spec.md> [<out.json>]", file=sys.stderr); return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as f: text = f.read()
    except OSError:
        print("NO-DATA: spec unreadable", file=sys.stderr); return 3
    if not text.strip(): print("NO-DATA: empty spec", file=sys.stderr); return 3
    recs = ask(text)
    if recs is None: print("NO-DATA: jev gave no complete answer", file=sys.stderr); return 3
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f: json.dump(recs, f)
    sys.stdout.write(advice(flags_from_records(recs)))
    return 0


def selftest():
    good = [{"id": k, "probability": 0.05 if v[2] else 0.95} for k, v in Q.items()]   # yes on positive, no on negative
    neg_yes = [dict(d, probability=0.9 if d["id"] == "leaves_a_core_requirement_vague" else d["probability"]) for d in good]
    pos_no = [dict(d, probability=0.1 if d["id"] == "callers_are_counted" else d["probability"]) for d in good]
    abst = [dict(d, probability=0.5) for d in good]
    junk = [{"id": "callers_are_counted", "probability": "0.1"}, {"id": "nope", "probability": 0.0}, {"id": "callers_are_counted", "probability": True}, "x"]
    fake_ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "\n".join(json.dumps(d) for d in pos_no), "")
    fake_short = lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(good[0]), "")
    fake_bad = lambda *a, **k: subprocess.CompletedProcess(a, 45, "", "NO-DATA")
    fake_junk = lambda *a, **k: subprocess.CompletedProcess(a, 0, "not json", "")
    cases = [("all yes on positive questions and no on negative ones: no flag", flags_from_records(good) == []),
             ("a negative question answered YES is a flag", flags_from_records(neg_yes) == ["leaves_a_core_requirement_vague"]),
             ("a positive question answered NO is a flag", flags_from_records(pos_no) == ["callers_are_counted"]),
             ("abstentions add nothing", flags_from_records(abst) == []),
             ("string, bool, unknown id and non dict records add nothing", flags_from_records(junk) == [] and flags_from_records(None) == [] and flags_from_records("x") == []),
             ("advice names the flag and its instruction, and is empty with no flag", "callers_are_counted" in advice(["callers_are_counted"]) and "grep every caller" in advice(["callers_are_counted"]) and advice([]) == ""),
             ("a complete answer is returned as records", len(ask("spec", fake_ok) or []) == len(Q)),
             ("an incomplete answer is None, never a partial list", ask("spec", fake_short) is None),
             ("a refused call is None", ask("spec", fake_bad) is None),
             ("unparseable output is None", ask("spec", fake_junk) is None)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(main())
