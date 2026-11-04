#!/usr/bin/env python3
"""BrotherDS gate packs: the seam for EXPERIMENT, DETECTION, MASTER_DATA and
PIPELINE claim types.

bds.py owns claim_type (declared or inferred) and the shared Finding class.
This module owns nothing about either: it is a plain registry so four pack
modules (pack_experiment.py, pack_detection.py, pack_mdm.py, pack_pipeline.py)
can be built in parallel, each in its own file, without ever touching bds.py
again. Finding cannot be imported here without a circular import (bds.py
imports this module), so it is never imported; every call site hands it in.

THE CONTRACT A PACK MODULE MUST MEET, so the next four builders read it here
and nowhere else:

    register(packs_module, Finding) -> None
        Called once by bds.py at import time, passing THIS module as
        packs_module and bds.Finding as Finding. A pack module must call
        packs_module.register(CLAIM_TYPE, fn) once per finding function it
        owns, where CLAIM_TYPE is one of "EXPERIMENT", "DETECTION",
        "MASTER_DATA", "PIPELINE" and fn has the signature below.

    fn(claim, Finding) -> list[Finding]
        A pack's own finding function. Takes the claim dict and the Finding
        class (never import it), returns a list of Finding instances built
        the same way bds.py's own gates build them: Finding(gate, verdict,
        detail) with verdict one of PASS, FAIL, NO-DATA (the exact strings
        "PASS", "FAIL", "NO-DATA", matching bds.PASS/FAIL/NODATA). Name each
        gate uniquely (e.g. "G20.something") so it prints its own line and
        never collides with a gate from bds.py or another pack.

    selftest(expect) -> bool
        Called once by bds.py's own selftest(). `expect` is the same
        assertion helper bds.py's selftest() uses internally: call it as
        expect(cond, msg) for every assertion; it prints "SELFTEST FAIL: msg"
        and returns bool(cond) on failure. selftest() must itself return
        True only when every expect() call it made returned True (fold them
        with `ok &= expect(...)`, the same pattern bds.py's own selftest
        uses), so a caller can do `ok &= mod.selftest(expect)`.

A pack registered for one claim_type NEVER runs for another (acceptance test
A20 in the design): run_packs looks up PACKS[claim_type] only.
"""

PACKS = {"EXPERIMENT": [], "DETECTION": [], "MASTER_DATA": [], "PIPELINE": []}


def register(claim_type, fn):
    """Called by a pack module's own register(), once per finding function.
    Refuses a claim_type this registry does not carry, so a typo in a pack
    module fails loudly at import time rather than silently going nowhere."""
    if claim_type not in PACKS:
        raise ValueError("unknown claim_type for a pack: %r (known: %s)"
                          % (claim_type, ", ".join(sorted(PACKS))))
    PACKS[claim_type].append(fn)


def run_packs(claim_type, claim, Finding):
    """Every finding from every pack registered under claim_type, in
    registration order. A claim_type with no packs registered, or one that
    matches no key at all (an undeclared or invalid claim_type), returns []:
    this is a lookup, never a refusal of its own."""
    findings = []
    for fn in PACKS.get(claim_type, []):
        findings.extend(fn(claim, Finding))
    return findings
