"""host_adapter_spec: the vocabulary a host adapter's capability report is
graded against, and the one rule that keeps it honest.

DOM-50.01. The deciding property this file exists to hold: each capability
reports exactly one of supported, unsupported, advisory or enforced, and a
host cannot claim enforced without the evidence that proves it. A missing
or unknown level reads as unsupported (NO-DATA), never as enforced. That
sentence is the whole module; everything below is the arithmetic of it.

WHY A FOURTH LEVEL. scripts/capability_precheck.py already grades a
REDUCED report against ENFORCED and ADVISORY (two levels, ambiguity
collapsed into "not ENFORCED"), because a dispatch precheck only ever
cares whether a requirement is met. This file sits one step earlier: it is
the reduction a host adapter's raw self-report has to pass through before
precheck ever sees it, so a bare "the host has this feature" (supported)
is told apart from "the host offers this as a guarantee" (enforced), and
from "the host offers nothing here at all" (unsupported). Nothing in this
file is reimplemented from capability_precheck.py: that module's levels
are uppercase and two-valued on purpose, for a different, later question
(does a MET requirement authorize dispatch); this module's levels are the
four the deciding property names, for an earlier question (what level did
the evidence actually earn). A caller that needs precheck's shape reduces
this module's four-valued output itself, the same way precheck's own
docstring already asks every caller to reduce host_capability_receipt's
free text before calling it.

Python 3, standard library only. No I/O, no subprocess, no clock: every
fact this module grades is passed in by the caller, exactly like
capability_precheck.py.
"""

UNSUPPORTED = "unsupported"
SUPPORTED = "supported"
ADVISORY = "advisory"
ENFORCED = "enforced"

#: The four levels, in ascending order of how much a caller may rely on
#: them. A raw claim outside this tuple is never believed (see
#: capability_level below).
LEVELS = (UNSUPPORTED, SUPPORTED, ADVISORY, ENFORCED)

NODATA = "NO-DATA"


def capability_level(name, report):
    """(level, reason) for one capability's raw self-report.

    name: the capability's own name, used only so the reason string can
    say which capability it is about.

    report: the host's raw self-report for this one capability, expected
    to be a mapping carrying a "claim" entry (meant to be one of LEVELS)
    and an optional "evidence" entry (a string naming the proof behind an
    ENFORCED claim, or empty/absent for the other three levels).

    A missing report (None, or anything that is not a mapping) is never a
    pass: this returns UNSUPPORTED with a NODATA reason, per the estate
    law that an unreadable input blocks rather than reading as the safe
    case.

    A "claim" that is absent, not a string, or not one of LEVELS (wrong
    case, a typo, an empty string, a made-up word) is graded the same
    way: UNSUPPORTED with a NODATA reason quoting the bad value, because
    a level this function cannot place on the ladder is not evidence the
    ladder rung is real.

    A claim of ENFORCED is believed only when "evidence" is a non-empty,
    non-whitespace string. Without that, the claim is downgraded to
    ADVISORY rather than dropped to UNSUPPORTED: the host is still
    claiming some behavior, it simply has not proven the strongest one.
    This is the deciding property's own sentence, spelled out as code: a
    host cannot claim enforced without the evidence that proves it.

    A claim of UNSUPPORTED, SUPPORTED or ADVISORY passes through
    unchanged; evidence is never required for those three."""
    if not isinstance(report, dict):
        return UNSUPPORTED, ("%s: capability %r report is missing or not a "
                             "mapping (%r)" % (NODATA, name, report))

    claim = report.get("claim")
    if not isinstance(claim, str) or claim not in LEVELS:
        return UNSUPPORTED, (
            "%s: capability %r claims %r, not one of %s"
            % (NODATA, name, claim, ", ".join(LEVELS)))

    if claim == ENFORCED:
        evidence = report.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            return ADVISORY, (
                "capability %r claimed enforced with no evidence named; "
                "downgraded to advisory, a guarantee with nothing behind "
                "it is not a guarantee" % name)
        return ENFORCED, ("capability %r claimed enforced, evidence: %s"
                          % (name, evidence.strip()))

    return claim, "capability %r reported %s" % (name, claim)


def capability_levels(reports):
    """{capability_name: (level, reason)} for every entry in `reports`,
    each graded by capability_level above.

    reports: a mapping of capability name to that capability's raw
    self-report (the same shape capability_level takes). An empty mapping
    returns an empty dict. A mapping with one entry returns one result; a
    mapping with many returns one result per entry, independently graded,
    so one malformed claim never affects another capability's grade.

    reports itself being missing or not a mapping is graded the same way
    a single bad report is: this is not a per-capability fact, so it is
    returned as a single NODATA entry under the key "reports" rather than
    silently becoming an empty dict, which would read as "zero
    capabilities were ever claimed" instead of "the whole input could not
    be read".

    Merging several sources into one `reports` mapping is the caller's
    job; a capability name appearing in more than one source has already
    been resolved to one entry before it reaches this function, so there
    is nothing here to deduplicate."""
    if not isinstance(reports, dict):
        return {"reports": (UNSUPPORTED, (
            "%s: capability reports input is missing or not a mapping "
            "(%r)" % (NODATA, reports)))}
    return {name: capability_level(name, report)
            for name, report in reports.items()}
