#!/usr/bin/env python3
"""autonomy_floor: the capability floor an autonomy level needs from the
host before it may run, refused rather than run degraded.

WHY THIS EXISTS, DOM-40.07. scripts/autonomy_dial.py already answers one
axis: given an action's own risk and an operator-set dial, how much
ceremony (state and continue, ask, refuse) governs it. Its own docstring
names the OTHER axis as out of scope on purpose: "sandbox (what is
POSSIBLE) and approval policy (when Brother ASKS) are independent axes.
This file is the approval axis only." Nothing in this tree answered the
sandbox axis for autonomy levels until now: a level that plans to act with
little or no human gate (A0, A1) is only as safe as the host's REAL
capability enforcement behind it, and a host that only advises on a
boundary, or cannot attest to one at all, must not let that level run on
the strength of its label alone.

THE DECIDING PROPERTY (the unit's own words, docs/plan/ORCH-1020-WBS.json):
a host that cannot provide the capabilities an autonomy level requires
refuses that level rather than running it degraded; the required
capabilities per level are pinned; an unknown level or an unreadable
capability report refuses.

THE FLOOR, PINNED, NOT DERIVED. REQUIRED_CAPABILITIES below is a fixed
table, not computed from anything: A0 (autonomy_dial's
execute_then_check, no human touchpoint before or during the action, only
a check after) needs every capability
scripts/execution_boundary.py composes (filesystem, network, credential,
process) at ENFORCED. A1 (state_interpretation_then_continue, still acts
automatically, only a stated interpretation stands between description and
effect) needs the two capabilities execution_boundary's own built-in
providers already mark ENFORCED by mechanism, not merely ADVISORY:
credential and process, the two whose own module performs the real,
irreversible effect. A2 (ask_one_blocking_question) and A3
(refuse_until_approved) both put a human between description and action
before anything runs, so this module asks nothing of the host for either:
the approval ceremony IS the safety mechanism at those two levels, exactly
the shape capability_precheck.py already established for "a unit requiring
nothing" (see its own docstring, DISPATCH unconditionally).

COMPOSITION, NOTHING HERE IS A SECOND COPY. Three modules already exist and
are called unchanged:
  scripts/autonomy_dial.py       supplies ORDER, the only place "A0".."A3"
                                  is enumerated; REQUIRED_CAPABILITIES is
                                  asserted, at import time, to cover exactly
                                  that set, so a change to ORDER there fails
                                  loudly here instead of silently going
                                  stale.
  scripts/host_adapter_spec.py   grades the host's RAW self-report (a
                                  mapping of capability name to a "claim"
                                  and optional "evidence") into one of its
                                  four honest words, downgrading an
                                  unproven ENFORCED claim to ADVISORY. This
                                  module never re-decides that grading.
  scripts/capability_precheck.py already is the one rule "a required
                                  capability must be reported ENFORCED, and
                                  ADVISORY or UNKNOWN never satisfies it".
                                  Its vocabulary is upper case and two
                                  valued (ENFORCED, ADVISORY) where
                                  host_adapter_spec's is lower case and
                                  four valued, on purpose (see
                                  capability_precheck's own docstring), so
                                  _reduce() below is the one small
                                  translation step between the two, nothing
                                  more.
scripts/execution_boundary.py supplies the CAPABILITIES vocabulary
(filesystem, network, credential, process) that REQUIRED_CAPABILITIES is
checked against, so a typo capability name in the pinned table is caught at
import time rather than silently never matching anything.

CONTINGENCY AND EDGES, named here rather than after an incident:
  unknown level (not one of REQUIRED_CAPABILITIES' four keys, including
  None, "", "a0" lower case, "A4")   REFUSE, before host_report is even
                                      looked at: a level this module does
                                      not recognise cannot be matched to a
                                      pinned floor.
  a level requiring nothing (A2, A3) RUN unconditionally, even when
                                      host_report is None or garbage: there
                                      is no capability demand on the host
                                      for this level to fail, matching
                                      capability_precheck's own precedent.
  host_report is not a mapping       REFUSE, NO-DATA, for any level that
  (None, a string, a list, a report  DOES require something: an unprobable
  that could not be produced)        host is not proof a capability is
                                      missing or proof it is present, so
                                      this never guesses in the permissive
                                      direction.
  host_report is a mapping but names REFUSE, naming the first missing
  nothing for a required capability  capability: absence of a claim is not
                                      a grant.
  a required capability's claim is   downgraded to ADVISORY by
  ENFORCED with no evidence string   host_adapter_spec.capability_level(),
                                      which never satisfies precheck's
                                      ENFORCED-only rule: this is the
                                      deciding property's own sentence,
                                      "refuses rather than running it
                                      degraded", made concrete.
  a concurrent second actor, already This module holds no state of its
  done, partially done, expired or   own: floor_verdict() is pure, reading
  stale                              only the arguments it is given, so
                                      none of these apply, the same
                                      position execution_boundary.py and
                                      capability_precheck.py each already
                                      take about themselves.

Python 3, standard library only. No network, no filesystem, no subprocess,
no clock: every fact this module needs is passed in by the caller, exactly
like the three modules it composes.
"""

import json
import sys

import autonomy_dial
import capability_precheck
import execution_boundary
import host_adapter_spec

RUN = "RUN"
REFUSE = "REFUSE"

#: The pinned floor. Keys are exactly autonomy_dial.ORDER's four levels
#: (asserted below); values are tuples of execution_boundary.CAPABILITIES
#: names this module requires the host to report ENFORCED before that
#: level may run. An empty tuple means the level's own approval ceremony
#: is the whole safety mechanism, and the host is not asked for anything.
REQUIRED_CAPABILITIES = {
    "A0": ("filesystem", "network", "credential", "process"),
    "A1": ("credential", "process"),
    "A2": (),
    "A3": (),
}

if set(REQUIRED_CAPABILITIES) != set(autonomy_dial.ORDER):
    raise ValueError(
        "autonomy_floor.REQUIRED_CAPABILITIES %r does not cover exactly "
        "autonomy_dial.ORDER %r; the pinned floor has gone stale against "
        "the one place autonomy levels are enumerated"
        % (sorted(REQUIRED_CAPABILITIES), autonomy_dial.ORDER))

for _level, _caps in REQUIRED_CAPABILITIES.items():
    for _cap in _caps:
        if _cap not in execution_boundary.CAPABILITIES:
            raise ValueError(
                "autonomy_floor.REQUIRED_CAPABILITIES[%r] names %r, not one "
                "of execution_boundary.CAPABILITIES %r; a pinned capability "
                "name that matches nothing real is a typo, not a floor"
                % (_level, _cap, execution_boundary.CAPABILITIES))
del _level, _caps, _cap


def _reduce(required, graded):
    """{capability: level} in capability_precheck's own upper case, two
    valued vocabulary, built from host_adapter_spec.capability_levels()'s
    lower case, four valued grading. Only `required` names are translated:
    a capability nobody asked for is not this function's business. A name
    `graded` never mentions is left out entirely, which precheck() already
    reads as UNKNOWN (absence of a claim is not a grant); a name graded
    UNSUPPORTED or SUPPORTED is passed through UNCHANGED rather than
    dropped, so precheck's refusal quotes the true word instead of a
    manufactured "not mentioned"."""
    reduced = {}
    for name in required:
        entry = graded.get(name)
        if entry is None:
            continue
        graded_level, _reason = entry
        if graded_level == host_adapter_spec.ENFORCED:
            reduced[name] = capability_precheck.ENFORCED
        elif graded_level == host_adapter_spec.ADVISORY:
            reduced[name] = capability_precheck.ADVISORY
        else:
            reduced[name] = graded_level
    return reduced


def floor_verdict(level, host_report):
    """(verdict, reason). verdict is RUN or REFUSE, per THE DECIDING
    PROPERTY above.

    level: expected to be one of autonomy_dial.ORDER ("A0".."A3"). Any
    other value, including None or an empty string, is an unknown level:
    refused immediately, before host_report is examined at all.

    host_report: the host's RAW capability self-report, a mapping of
    capability name (from execution_boundary.CAPABILITIES) to the shape
    host_adapter_spec.capability_level() expects (at least a "claim" key,
    and an "evidence" string when the claim is enforced). Anything that is
    not a mapping means the host could not be probed at all, and is
    refused for any level that requires something; a level that requires
    nothing never looks at host_report, so an unreadable report never
    blocks a level that was never going to ask the host for anything."""
    required = REQUIRED_CAPABILITIES.get(level)
    if required is None:
        return REFUSE, (
            "unknown autonomy level %r: not one of %s; refusing rather "
            "than guessing which floor applies"
            % (level, autonomy_dial.ORDER))

    if not required:
        return RUN, (
            "level %s requires no host capability floor: its own approval "
            "ceremony (%s) is the safety mechanism"
            % (level, autonomy_dial.ACTIONS[level]))

    if not isinstance(host_report, dict):
        return REFUSE, (
            "%s: host capability report could not be read (not a mapping: "
            "%r); refusing level %s rather than running it degraded"
            % (host_adapter_spec.NODATA, host_report, level))

    graded = host_adapter_spec.capability_levels(host_report)
    reduced = _reduce(required, graded)
    precheck_verdict, reason = capability_precheck.precheck(required, reduced)
    if precheck_verdict == capability_precheck.DISPATCH:
        return RUN, "level %s: %s" % (level, reason)
    return REFUSE, "level %s refused: %s" % (level, reason)


def main(argv):
    level = None
    report_path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--level" and i + 1 < len(argv):
            level = argv[i + 1]
            i += 2
            continue
        if arg == "--json" and i + 1 < len(argv):
            report_path = argv[i + 1]
            i += 2
            continue
        i += 1

    if level is None:
        print("autonomy-floor: NO-DATA: --level is required")
        return 2

    if report_path is None:
        raw = sys.stdin.read()
    else:
        try:
            with open(report_path, "r") as handle:
                raw = handle.read()
        except OSError as exc:
            print("autonomy-floor: NO-DATA: could not read %r: %s"
                  % (report_path, exc))
            return 2

    raw = raw.strip()
    if not raw:
        host_report = {}
    else:
        try:
            host_report = json.loads(raw)
        except ValueError as exc:
            print("autonomy-floor: NO-DATA: host report is not valid JSON: %s"
                  % exc)
            return 2

    verdict, reason = floor_verdict(level, host_report)
    print("autonomy-floor: level=%s verdict=%s reason=%s"
          % (level, verdict, reason))
    return 0 if verdict == RUN else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
