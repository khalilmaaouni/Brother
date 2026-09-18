#!/usr/bin/env python3
"""capability_negotiation: pick a dispatch route by measured capability,
never by which host reported it.

WBS DOM-50.03, disposition EXTEND. Extends scripts/host_capability.py (the
one recognised vocabulary of capability names, its own _TABLE_FIELDS
tuple) and scripts/capability_precheck.py (the one ENFORCED/ADVISORY/
UNKNOWN ladder and its precheck() verdict) rather than restating either:
this module owns neither list, it imports both.

THE DECIDING PROPERTY this module exists to hold: behaviour is routed by
measured capability, never by host name. Two hosts with identical measured
capabilities get identical routing whatever they are called, because
negotiate() below never takes a host name as an argument at all, only a
`reported` capability mapping: there is nothing in its signature a host
name could reach even if a caller tried to pass one in. An unmeasured
capability (absent from `reported`) is UNKNOWN under capability_precheck's
own rule (absence of a claim is not a grant), never satisfies an ENFORCED
requirement, and so routing falls toward the most restrictive path, which
is why every ROUTES table this module uses must end in an unconditional
fallback route.

CONTINGENCY, PLAINLY: a route naming a capability outside
host_capability.py's own recognised vocabulary is refused before any
negotiation happens (ValueError), since a name this repository has never
measured is not something this module can reason about. A malformed
ROUTES table (empty, a duplicate route name, a fallback that itself
requires something) is a bug in the caller and is refused the same way,
before `reported` is even consulted.

Python 3, standard library only. No network, no filesystem, no subprocess:
this module reads nothing itself, it only decides among what its caller
already measured and handed it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import capability_precheck  # noqa: E402
import host_capability  # noqa: E402

#: The one recognised capability vocabulary, reused whole from
#: host_capability.py rather than restated here. A route naming a
#: capability outside this tuple is refused at negotiate() time: a name
#: this repository has never measured is not something this module can
#: route on.
KNOWN_CAPABILITIES = host_capability._TABLE_FIELDS

#: The estate's three-tier dispatch ladder, most-capable route first,
#: guaranteed fallback last. A caller that wants routing needs only pass
#: ROUTES to negotiate() alongside its own measured `reported` mapping,
#: never build a route list of its own.
AUTONOMOUS = "AUTONOMOUS"
SUPERVISED = "SUPERVISED"
RESTRICTED = "RESTRICTED"

ROUTES = (
    (AUTONOMOUS, ("enforceable_deny", "workspace_isolation")),
    (SUPERVISED, ("workspace_isolation",)),
    (RESTRICTED, ()),
)


def negotiate(routes, reported):
    """(route_name, reason): the first route in `routes` (ordered
    most-capable first) whose required capabilities are ALL reported at
    ENFORCED level, decided by capability_precheck.precheck(). `reported`
    is passed through unchanged and is the only input this function reads
    besides `routes` itself: no host identity field, however named, can
    change the outcome, because none is ever looked at.

    routes: an ordered sequence of (route_name, required_capabilities)
    pairs. Every required_capabilities name must already be one
    KNOWN_CAPABILITIES recognises, route names must be unique, and the
    LAST route must require nothing (it is the guaranteed fallback: an
    unmeasured or advisory-only capability must always have somewhere to
    land). Any of these violated raises ValueError naming the problem.
    """
    if not routes:
        raise ValueError(
            "negotiate() received an empty routes sequence: there is no "
            "fallback to refuse into")

    seen_names = set()
    for route_name, required in routes:
        if route_name in seen_names:
            raise ValueError(
                "duplicate route name %r: negotiate() cannot tell two "
                "same-named routes apart" % (route_name,))
        seen_names.add(route_name)
        for name in capability_precheck._dedupe(required):
            if name not in KNOWN_CAPABILITIES:
                raise ValueError(
                    "route %r requires capability %r, which is not in "
                    "host_capability.py's own recognised vocabulary %r: "
                    "an unmeasurable capability cannot be routed on"
                    % (route_name, name, KNOWN_CAPABILITIES))

    last_name, last_required = routes[-1]
    last_dedup = capability_precheck._dedupe(last_required)
    if last_dedup:
        raise ValueError(
            "last route %r requires capabilities %r: the last route is "
            "the guaranteed fallback and must require nothing, or there "
            "is no route left once every real requirement fails"
            % (last_name, last_dedup))

    for route_name, required in routes:
        verdict, reason = capability_precheck.precheck(required, reported)
        if verdict == capability_precheck.DISPATCH:
            return route_name, reason

    raise AssertionError(
        "unreachable: the last route requires nothing and "
        "capability_precheck.precheck() always DISPATCHes an empty "
        "requirement, so the loop above must have returned already")
