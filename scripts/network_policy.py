#!/usr/bin/env python3
"""DOM-40.03: a pure decision function for outbound network access.

WHY THIS EXISTS. scripts/host_capability.py's receipt carries a
network_control field that reads NO-DATA for every host tonight
("not measured by this receipt"): nothing in this tree has ever decided,
in one place, whether a given outbound host is reachable under a
declared policy. This module is that one place. It decides; it makes no
real network call anywhere, and it does not by itself stop a caller that
never asks it: like scripts/filesystem_enforcement.py (its sibling unit,
DOM-40.02, and the pattern this module deliberately mirrors), this is a
control-plane decision, not a sandbox.

THE DECIDING PROPERTY (the unit's own words): denied, allowlisted and
unrestricted are three distinct modes, never conflated with one another,
and high-autonomy (unattended, unsupervised) work can never silently end
up with unrestricted access. An unknown or unreadable mode is always
denied, never unrestricted: the fail direction on anything this module
cannot positively recognize is always the most restrictive outcome.

THE THREE MODES, read from config["mode"]:
  denied        every host refused, unconditionally, whatever else config
                or high_autonomy say.
  allowlisted   a host is allowed only when it matches an entry in
                config["allowlist"]; everything else, including an empty
                or missing allowlist, is refused (an allowlisted mode with
                nothing on the list refuses everything, it does not fall
                back to permissive).
  unrestricted  every syntactically valid host is allowed, EXCEPT: a
                high_autonomy caller is refused unless config also carries
                an explicit, separate acknowledgement,
                config["unrestricted_ack"], present and truthy. A human-
                supervised (high_autonomy=False) caller needs no such
                acknowledgement, because the supervision unattended work
                lacks is exactly what the ack stands in for.
Anything other than exactly one of those three strings (a typo, a number,
None, a mode this module has never heard of) is refused as an unknown
mode, named distinctly from "config is not a mapping at all" and from
"config has no mode key", so a caller can tell the three failures apart.

ALLOWLIST MATCHING. Case-insensitive, and tolerant of surrounding
whitespace on each entry. An exact match ("example.com" matches
"example.com" or "Example.com") is one form; the only other form
supported is a wildcard-subdomain prefix written "*.example.com", which
matches "anything.example.com" and "deeper.anything.example.com" but
never matches the bare parent "example.com" itself (list that separately
if it should also be reachable). No richer glob or regex matching exists
here on purpose: a wildcard entry is deliberately narrow so a reader can
tell what it reaches by reading it, without a regex engine.

HOST VALIDATION runs first, before mode is even inspected: None, an
empty or whitespace-only string, or any non-string host is refused as
"invalid-host" under every mode, including unrestricted, because there is
nothing to allow or deny about a host that was never actually named.

CHECK ORDER (named here because two problems can be present in the same
call, and the rule name returned depends on which one is checked first):
  1. host validity (invalid-host)
  2. config shape: is it a mapping at all (config-not-a-mapping), does it
     have a "mode" key (config-missing-mode)
  3. mode recognition (unknown-mode)
  4. mode-specific logic (denied / allowlisted matching)
  5. for unrestricted only, last: the high-autonomy acknowledgement rule
     (unrestricted-requires-ack-for-high-autonomy)
Any error this function did not anticipate while walking that order (a
config that raises on indexing, a non-iterable allowlist, anything else)
is caught by a last-resort handler and refused as "internal-error",
matching filesystem_enforcement.decide()'s own "ambiguity always refuses"
posture: this function never raises out to its caller and never resolves
an unexpected shape to ALLOW.

CONTINGENCY AND EDGES, named here rather than after an incident:
  empty string / whitespace host          invalid-host, every mode.
  config is None / a list / a string      config-not-a-mapping.
  config is a dict with no "mode" key     config-missing-mode.
  mode is not one of the three strings    unknown-mode.
  allowlisted, "allowlist" key missing    allowlist-empty-or-unreadable
  allowlisted, "allowlist" not iterable   (same rule, one reason for both:
                                           nothing on the list to match
                                           against reads the same as an
                                           empty list from the caller's
                                           side, and both refuse).
  allowlisted, allowlist has entries,     not-allowlisted.
  host matches none of them
  unrestricted, high_autonomy=False       allowed, no ack required.
  unrestricted, high_autonomy undeclared  treated as True: undeclared
  (None, the default)                     autonomy never reads as supervised.
  unrestricted, high_autonomy=True,       unrestricted-requires-ack-for-
  no unrestricted_ack (or falsy)          high-autonomy.
  unrestricted, high_autonomy=True,       allowed, rule names the ack.
  unrestricted_ack truthy
  denied, any high_autonomy, any          mode-denied, unconditionally.
  allowlist content

WHAT THIS MODULE DELIBERATELY DOES NOT COVER: it holds no state between
calls and makes no real network request, so "a concurrent second actor",
"already done" and "expired or stale" do not apply to a pure function.
It cannot stop a caller that never calls decide() and opens a socket
directly; wiring a real caller through this module is a later unit's job
(scripts/execution_boundary.py, DOM-40.01, names a network provider in
its own interface), and is explicitly out of this unit's scope.

Python 3, standard library only. No network, no subprocess, no file
mutation of any kind by this module itself (the CLI below only reads a
JSON file the caller names).
"""
import argparse
import json
import sys

ALLOW = "ALLOW"
REFUSE = "REFUSE"

_MODES = ("denied", "allowlisted", "unrestricted")


class Verdict(object):
    """The whole return value of decide(): which way it went, which named
    rule decided it, and a human-readable reason. Equality and repr exist
    so a test can assert on the tuple shape without reaching into private
    fields."""

    __slots__ = ("decision", "rule", "reason")

    def __init__(self, decision, rule, reason):
        assert decision in (ALLOW, REFUSE)
        self.decision = decision
        self.rule = rule
        self.reason = reason

    @property
    def allowed(self):
        return self.decision == ALLOW

    def __eq__(self, other):
        if not isinstance(other, Verdict):
            return NotImplemented
        return (self.decision, self.rule) == (other.decision, other.rule)

    def __repr__(self):
        return "Verdict(%s, rule=%r, reason=%r)" % (
            self.decision, self.rule, self.reason)


def _refuse(rule, reason):
    return Verdict(REFUSE, rule, reason)


def _allow(rule, reason):
    return Verdict(ALLOW, rule, reason)


def _normalize_host(host):
    """The lowercased, whitespace-stripped host, or None when `host` is
    not a usable string at all (not a string, empty, or whitespace-only).
    None means "invalid-host", never "matches everything"."""
    if not isinstance(host, str):
        return None
    stripped = host.strip()
    if not stripped:
        return None
    return stripped.lower()


def _matches_allowlist(host_norm, allowlist):
    """True when host_norm (already normalized) matches one entry of
    `allowlist`. Every entry is normalized the same way host_norm was.
    A wildcard entry "*.example.com" matches any host that ends with
    ".example.com" and has something before it; it never matches
    "example.com" alone. Any entry that fails to normalize to a usable
    string is skipped, not treated as a match."""
    for entry in allowlist:
        if not isinstance(entry, str):
            continue
        norm = entry.strip().lower()
        if not norm:
            continue
        if norm.startswith("*."):
            suffix = norm[1:]  # keeps the leading dot: ".example.com"
            if host_norm.endswith(suffix) and len(host_norm) > len(suffix):
                return True
        elif host_norm == norm:
            return True
    return False


def decide(host, config, high_autonomy=None):
    """ALLOW or REFUSE an outbound request to `host` under `config`.
    Never raises: every anticipated bad shape is caught and turned into a
    REFUSE verdict naming the rule, and an outer catch-all does the same
    for anything not anticipated, so "ambiguity always refuses" holds even
    for a defect in this function itself. See the module docstring for
    the full rule table and check order."""
    # Orchestrator fix: autonomy the caller never declared is treated as
    # HIGH, so an undeclared caller can never run unrestricted without the
    # acknowledgement. Unknown blocks; it never reads as supervised.
    if high_autonomy is None:
        high_autonomy = True
    try:
        host_norm = _normalize_host(host)
        if host_norm is None:
            return _refuse(
                "invalid-host",
                "host %r is not a usable hostname: it is missing, empty, "
                "whitespace-only, or not a string, so there is nothing to "
                "allow or deny regardless of mode" % (host,))

        if not isinstance(config, dict):
            return _refuse(
                "config-not-a-mapping",
                "config %r is not a mapping; a network policy must be "
                "declared as an object with at least a \"mode\" key, and "
                "anything else refuses rather than guessing a mode" %
                (config,))
        if "mode" not in config:
            return _refuse(
                "config-missing-mode",
                "config has no \"mode\" key; a policy with no declared "
                "mode refuses rather than defaulting to permitted")

        mode = config.get("mode")
        if mode not in _MODES:
            return _refuse(
                "unknown-mode",
                "mode %r is none of %s; an unrecognised mode is refused, "
                "never treated as the most permissive of the three" %
                (mode, _MODES))

        if mode == "denied":
            return _refuse(
                "mode-denied",
                "policy mode is \"denied\": every host is refused "
                "unconditionally, regardless of high_autonomy or any "
                "other config content")

        if mode == "allowlisted":
            raw_allowlist = config.get("allowlist")
            if isinstance(raw_allowlist, (str, bytes)):
                # A bare string is iterable in Python (it yields its own
                # characters); silently accepting it would turn a typo
                # ("allowlist": "example.com" instead of a list) into a
                # per-character allowlist that matches almost nothing on
                # purpose and something by accident. Refused, not iterated.
                allowlist = None
            else:
                try:
                    allowlist = list(raw_allowlist or [])
                except TypeError:
                    allowlist = None
            if not allowlist:
                return _refuse(
                    "allowlist-empty-or-unreadable",
                    "policy mode is \"allowlisted\" but config[\"allowlist\"] "
                    "is missing, not iterable, or empty; an allowlisted "
                    "policy with nothing on the list refuses everything, "
                    "it never falls back to permissive")
            if _matches_allowlist(host_norm, allowlist):
                return _allow(
                    "allowlisted-match",
                    "host %r matched an entry in the declared allowlist" %
                    (host,))
            return _refuse(
                "not-allowlisted",
                "host %r matched no entry in the declared allowlist" %
                (host,))

        # mode == "unrestricted"
        if high_autonomy:
            ack = config.get("unrestricted_ack")
            if not ack:
                return _refuse(
                    "unrestricted-requires-ack-for-high-autonomy",
                    "policy mode is \"unrestricted\" and the caller is "
                    "high-autonomy (unattended, unsupervised); that "
                    "combination is never silently granted, and "
                    "config[\"unrestricted_ack\"] is missing or falsy, so "
                    "the request is refused rather than assumed approved")
            return _allow(
                "unrestricted-acknowledged",
                "policy mode is \"unrestricted\" for a high-autonomy "
                "caller, and config[\"unrestricted_ack\"] is present and "
                "truthy, so the explicit acknowledgement this combination "
                "requires is satisfied")
        return _allow(
            "unrestricted",
            "policy mode is \"unrestricted\" and the caller is not "
            "high-autonomy (a supervised session), so no separate "
            "acknowledgement is required")
    except Exception as exc:  # noqa: BLE001 - last-resort fail-closed net
        return _refuse(
            "internal-error",
            "unexpected error while deciding host %r: %s" % (host, exc))


def _load_config(config_json_path):
    with open(config_json_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", required=True, help="the host to decide")
    ap.add_argument("--config-json", required=True,
                    help="path to a JSON file holding the policy config "
                         "(a \"mode\" key, and an \"allowlist\" list for "
                         "allowlisted mode)")
    ap.add_argument("--high-autonomy", dest="autonomy", action="store_const",
                    const=True, default=None,
                    help="decide as an unattended, unsupervised caller")
    ap.add_argument("--supervised", dest="autonomy", action="store_const",
                    const=False,
                    help="decide as a supervised caller; with neither flag the "
                         "caller is treated as high-autonomy")
    args = ap.parse_args(argv)

    try:
        config = _load_config(args.config_json)
    except (OSError, ValueError) as exc:
        print("network_policy: CANNOT-DECIDE, could not read config from "
              "%r: %s" % (args.config_json, exc))
        return 2

    verdict = decide(args.host, config, high_autonomy=args.autonomy)
    print("network_policy: %s, rule=%s: %s" %
          (verdict.decision, verdict.rule, verdict.reason))
    return 0 if verdict.decision == ALLOW else 1


if __name__ == "__main__":
    sys.exit(main())
