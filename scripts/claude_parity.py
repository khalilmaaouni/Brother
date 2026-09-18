#!/usr/bin/env python3
"""claude_parity: DOM-50.04, the Claude host's own report against the
adapter conformance contract every other host is measured by.

WHY THIS EXISTS. scripts/adapter_conformance.py already runs the same
twelve steps against whatever adapter scripts/provider_adapter.py returns,
and scripts/host_capability.py already carries the fourteen-fact Host
Capability Receipt for Claude, Codex and Cursor, read from documents this
estate has already measured. Neither file reduces its own output to the
nine behaviours the WBS names for this unit (install, upgrade, uninstall,
hooks, enforcement, resume, subagents, evidence, receipts), and nothing
before this file told apart a category this repository has never measured
from one it measured and found green. This module is that reduction, for
the Claude host only, built on top of both without restating either.

THE DECIDING PROPERTY (docs/plan/ORCH-1020-WBS.json, DOM-50.04): each of
install, upgrade, uninstall, hooks, enforcement, resume, subagents,
evidence and receipts reports PASS, FAIL or NO-DATA for the Claude host
against the same conformance contract as every other host; an unobserved
behaviour is NO-DATA, never PASS. This module's whole job is to keep that
sentence true of the code, not to invent an extra measurement that
adapter_conformance.py and host_capability.py do not already make.

WHERE EACH CATEGORY'S ANSWER COMES FROM.

- install, upgrade, uninstall, resume: read straight from
  scripts/adapter_conformance.py's own twelve-step run for provider
  "claude" (its steps named "install", "upgrade", "uninstall" and
  "resume"), because those are live measurements taken this run, not a
  table entry that ages between runs.
- evidence: adapter_conformance's "baseline-red" and "changed-files" steps
  together, the two steps that prove a real fix happened this run (the
  check was red before, the files actually differ after): FAIL if either
  FAILed, NO-DATA if neither FAILed but either is NO-DATA, PASS only when
  both PASSed.
- receipts: adapter_conformance's "receipt-evidence" step alone, since
  that step already is the receipt-and-per-file-evidence check.
- hooks, enforcement: scripts/host_capability.py's own CAPABILITY_TABLE
  for host "claude", fields "pre_tool_hook" and "enforceable_deny". That
  table already writes host_capability.NODATA for anything this estate
  has not measured, so reducing its text to PASS/FAIL/NO-DATA is a
  straight read, never a new claim: text starting with the table's own
  NO-DATA marker stays NO-DATA, text starting with "yes" (the table's own
  affirmative word) is PASS, and anything else is FAIL, because a table
  entry that is neither an admitted gap nor a plain yes is a defect in
  the table, not a silent pass.
- subagents: nothing in this checkout measures a Claude subagent hook or
  capability today. adapter_conformance's twelve steps never touch a
  subagent, and host_capability.py's CAPABILITY_TABLE carries no
  "subagents" field for any host. This category is therefore NO-DATA
  unconditionally, naming that absence: this file does not invent a
  thirteenth conformance step on its own authority to close that gap.

Python 3.9, standard library only. No network of its own; the reused
modules make their own subprocess calls (installer verbs, brother_run.py),
this module makes none.

No em or en dashes anywhere in this file or its output.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import adapter_conformance  # noqa: E402
import host_capability  # noqa: E402
import orchestrator_invariants as OI  # noqa: E402

# Reused literally from adapter_conformance.py rather than retyped: that
# module already names this estate's PASS/FAIL/NO-DATA convention, and the
# assertion below proves it still agrees with orchestrator_invariants.py's
# frozen VERDICTS and with host_capability.py's own NO-DATA marker, rather
# than trusting three independently spelled copies to stay in step.
PASS = adapter_conformance.PASS
FAIL = adapter_conformance.FAIL
NODATA = adapter_conformance.NODATA
assert {PASS, FAIL, NODATA} == set(OI.VERDICTS), (
    "claude_parity's PASS/FAIL/NODATA no longer match "
    "orchestrator_invariants.VERDICTS: %r vs %r"
    % ({PASS, FAIL, NODATA}, set(OI.VERDICTS)))
assert NODATA == host_capability.NODATA, (
    "claude_parity's NODATA no longer matches host_capability.NODATA: "
    "%r vs %r" % (NODATA, host_capability.NODATA))

PROVIDER = "claude"

#: The nine behaviours DOM-50.04's deciding property names, in that order.
CATEGORIES = ("install", "upgrade", "uninstall", "hooks", "enforcement",
             "resume", "subagents", "evidence", "receipts")

#: category -> the adapter_conformance.py step name(s) it is read from.
_STEP_CATEGORIES = {
    "install": ("install",),
    "upgrade": ("upgrade",),
    "uninstall": ("uninstall",),
    "resume": ("resume",),
    "receipts": ("receipt-evidence",),
    "evidence": ("baseline-red", "changed-files"),
}

#: category -> host_capability.CAPABILITY_TABLE["claude"] field it is
#: read from.
_TABLE_CATEGORIES = {
    "hooks": "pre_tool_hook",
    "enforcement": "enforceable_deny",
}

#: categories with no measurement anywhere in this checkout today: always
#: NO-DATA, the reason naming why rather than pretending a check ran.
_UNMEASURED = {
    "subagents": ("no step in scripts/adapter_conformance.py and no field "
                 "in scripts/host_capability.py's CAPABILITY_TABLE "
                 "measures a Claude subagent hook or capability in this "
                 "checkout"),
}

assert (set(_STEP_CATEGORIES) | set(_TABLE_CATEGORIES) | set(_UNMEASURED)
       == set(CATEGORIES)), "every category must have exactly one source"


def _verdict_for_steps(step_results, names):
    """Combine one or more adapter_conformance.Step verdicts into one:
    FAIL if any named step FAILed, NO-DATA if none FAILed but any is
    NO-DATA, PASS only when every named step PASSed. A name this run's
    step list has nothing for (this module falling out of step with
    adapter_conformance.STEP_ORDER) is NO-DATA rather than silently
    skipped: a category this function cannot find evidence for is exactly
    the unobserved case the deciding property calls NO-DATA, never PASS.

    Confirmed by Muse adversarial review of the DeepSeek draft (finding
    12's spirit, verified against this integrated code rather than the
    draft): a step whose own verdict is none of PASS, FAIL or NODATA
    (a typo, a future STEP_ORDER addition this module was never updated
    for) must not fall through to the final `return PASS` branch. It
    raises instead, per the worker contract's rule 2, unknown input
    raises rather than reading as the safe case."""
    by_name = {r.name: r for r in step_results}
    missing = [n for n in names if n not in by_name]
    if missing:
        return NODATA, ("adapter_conformance.py produced no step named %s"
                        % ", ".join(repr(m) for m in missing))
    found = [by_name[n] for n in names]
    for s in found:
        if s.verdict not in (PASS, FAIL, NODATA):
            raise ValueError(
                "step %r reports verdict %r, none of %s" %
                (s.name, s.verdict, (PASS, FAIL, NODATA)))
    failed = [s for s in found if s.verdict == FAIL]
    if failed:
        return FAIL, "; ".join("%s: %s" % (s.name, s.reason) for s in failed)
    nodata = [s for s in found if s.verdict == NODATA]
    if nodata:
        return NODATA, "; ".join("%s: %s" % (s.name, s.reason)
                                 for s in nodata)
    return PASS, "; ".join("%s: %s" % (s.name, s.reason) for s in found)


def _verdict_for_table_field(text):
    """Reduce one host_capability.CAPABILITY_TABLE free-text entry to
    PASS/FAIL/NO-DATA. The table already writes its own NO-DATA marker for
    anything unmeasured, so that marker is read through unchanged rather
    than re-derived. A missing or non-string entry is NO-DATA (never
    invented as a pass); text starting with the table's own NO-DATA marker
    stays NO-DATA; text starting with the table's affirmative WORD "yes"
    is PASS; anything else is FAIL, since a table entry that is neither an
    admitted gap nor a plain yes is a defect in the table itself, not a
    silent pass.

    Two fixes from Muse adversarial review of the DeepSeek draft, verified
    against this integrated code and both real here (findings 1, 4, 5):
    the text is stripped before either prefix check, so leading
    whitespace never turns a real "yes" or NO-DATA into a FAIL; and the
    "yes" check matches the whole first word (via a following colon,
    comma, space or end of string), so a word like "yesterday" reads as
    FAIL rather than being mistaken for the affirmative "yes". The
    NO-DATA marker match stays case-sensitive and exact, since it is read
    from this module's own host_capability.NODATA constant, not free text
    typed by a person."""
    if not isinstance(text, str) or not text.strip():
        return NODATA, "host_capability.py carries no text for this field"
    stripped = text.strip()
    if stripped.startswith(host_capability.NODATA):
        return NODATA, text
    first_word = stripped.split(None, 1)[0].rstrip(":,")
    if first_word.lower() == "yes":
        return PASS, text
    return FAIL, ("host_capability.py's entry is neither %r nor a plain "
                 "yes: %s" % (host_capability.NODATA, text))


def claude_parity(evidence_dir=None, offline=False):
    """{category: (verdict, reason)} for every entry in CATEGORIES, for
    the Claude host only.

    Runs scripts/adapter_conformance.py's own twelve steps for provider
    "claude" exactly once (never a second, competing conformance run),
    reads scripts/host_capability.py's CAPABILITY_TABLE for the two
    table-backed categories, and reports NO-DATA outright for
    "subagents", which neither module measures today.

    Never raises on its own account: the reused conformance runner already
    turns a step that could not even attempt its work into a NO-DATA or
    FAIL Step rather than propagating an exception, and this function adds
    nothing past that beyond the module-load-time assertions above."""
    base = evidence_dir or os.path.join(
        adapter_conformance.DEFAULT_EVIDENCE_ROOT, PROVIDER)
    step_results = adapter_conformance.run_conformance(
        PROVIDER, base, offline)
    out = {}
    for category, names in _STEP_CATEGORIES.items():
        out[category] = _verdict_for_steps(step_results, names)
    table = host_capability.CAPABILITY_TABLE.get(PROVIDER, {})
    for category, field in _TABLE_CATEGORIES.items():
        out[category] = _verdict_for_table_field(table.get(field))
    for category, reason in _UNMEASURED.items():
        out[category] = (NODATA, reason)
    return out


def overall_verdict(results):
    """FAIL beats NO-DATA beats PASS, the same priority
    adapter_conformance.py's own _verdict_for uses: one bad category is
    enough to sink the whole report, and one unmeasured category is enough
    to keep it from reading as a clean PASS.

    Confirmed by Muse adversarial review of the DeepSeek draft (findings
    2 and 3, the same defect shape in this integrated code): an empty or
    incomplete `results` mapping, or one carrying a verdict string outside
    PASS/FAIL/NO-DATA, falls through neither the FAIL nor the NO-DATA
    check and must not silently read as PASS. Both raise ValueError,
    naming the problem, rather than a caller's bug quietly becoming a
    clean roll-up."""
    if set(results) != set(CATEGORIES):
        raise ValueError(
            "overall_verdict() needs exactly the categories %r, got %r"
            % (set(CATEGORIES), set(results)))
    verdicts = {v for v, _ in results.values()}
    unknown = verdicts - {PASS, FAIL, NODATA}
    if unknown:
        raise ValueError("unrecognised verdict(s) in results: %r" % unknown)
    if FAIL in verdicts:
        return FAIL
    if NODATA in verdicts:
        return NODATA
    return PASS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--offline", action="store_true",
                   help="skip every conformance step that would install, "
                        "upgrade or uninstall for real; those categories "
                        "read NO-DATA")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    results = claude_parity(args.evidence_dir, args.offline)
    for category in CATEGORIES:
        verdict, reason = results[category]
        print("%-12s %-8s %s" % (category, verdict, reason))
    verdict = overall_verdict(results)
    print("verdict=%s" % verdict)
    if verdict == FAIL:
        return 1
    if verdict == NODATA:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
