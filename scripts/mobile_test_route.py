#!/usr/bin/env python3
"""EPIC M2.03 Mobile Test Route: a read-only loader/validator for
mobile-test-route-v1 records (docs/schema/mobile-test-route-v1.json),
plus a developer-documentation generator for a record that already
validates clean.

THIS IS A DESIGN/CONTRACT, NOT A LIVE IMPLEMENTATION: it defines the
shape a test-only deep-link/route record must have and checks a record
against that shape. It never opens a network port, never modifies any
real app's navigation code, and never executes anything against a
device or simulator -- that is entirely out of scope for this unit,
unlike M2.02's device-touching state-reset adapters. The "app" any
record here describes is always the abstract target project via
scripts/mobile_project_profile.py's profile, never a specific real
repository (standing never-name-the-app rule).

THE CENTRAL RULE THIS MODULE ENFORCES: a route record must never
validate as safe while actually carrying no real build/config fence.
The schema alone gets partway there (build_fence is a required object,
and release_build_excluded is pinned to const:true so it cannot be
silently omitted or left false); hand_rules() below closes the rest --
a fence object whose fence_identifier or verification_note is empty,
whitespace, or an obvious placeholder is refused, not accepted, even
though the schema's keyword subset (no minLength) would let an empty
string through on type alone. THIS MODULE CANNOT EXECUTE A TARGET
PROJECT'S OWN BUILD SYSTEM: it can only refuse an obviously hollow
claim, never confirm a true one. A route record that passes here is a
well-formed DECLARATION that a fence exists and was reviewed, not
independent proof the fence actually holds in a real build -- exactly
parallel to how a passing mobile-state-fixture-v1 record is a
well-formed declaration of desired state, not yet an achieved one
(see scripts/mobile_state_fixture.py's own docstring for that
precedent).

Sibling modules: scripts/mobile_project_profile.py and
scripts/mobile_toolchain_probe.py are GENERATORS that detect real
things; scripts/mobile_state_fixture.py is a VALIDATOR that checks an
author-written fixture. This module is a VALIDATOR (check/hand_rules,
same shape) plus a DOCUMENTATION GENERATOR (render_route_docs): the
generator is a pure function that turns an already-valid record into
the Markdown a real project's own developers would need to wire the
pattern in safely, and it refuses to run on a record with any
unresolved problem (see render_route_docs's own docstring) so a
generated guide can never describe a route that has not passed the
fence checks above.

Exit contract, mirroring scripts/contract_check.py's and
scripts/mobile_state_fixture.py's own:
  0  PASS      the route record satisfies the schema and the hand rules
  1  FAIL      one or more problems, each printed on its own line
  2  NO-DATA   the record or the schema could not be read as JSON

NO-DATA IS NOT A PASS: a record this module could not open or parse is
"could not look", never "looked and found nothing wrong".
"""
import argparse
import json
import os
import re
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "mobile-test-route-v1.json")

#: Values that look like an author left the field unfilled rather than
#: stating a real verification method or a real production risk. A
#: cheap, non-exhaustive backstop (same honesty as
#: scripts/mobile_state_fixture.py's secret-shape regex): it catches
#: the obvious placeholder, it cannot confirm a filled-in value is
#: actually true.
_PLACEHOLDER_VALUES = {
    "todo", "tbd", "t.b.d.", "n/a", "na", "none", "unknown",
    "not verified", "not yet verified", "none yet", "no check",
    "no check yet", "-", "?", "...",
}

#: Reassurance phrases that read as "trust me, this is fine to leave
#: reachable" rather than naming a real, checkable fence -- the exact
#: phrases render_route_docs's own commitment block below refuses to
#: ever emit as output. Checked here too (substring, not exact match)
#: because a value carrying one of these reaches render_route_docs
#: verbatim (build_fence.verification_note is interpolated unescaped
#: into the generated guide), so refusing it only on output was never
#: enough: a hollow reassurance accepted on input becomes exactly the
#: hollow-sounding-safe output the commitment block exists to prevent.
_REASSURANCE_PHRASES = (
    "safe to ship", "harmless in production", "inactive by default",
    "debug-only", "we'll remove it later", "disabled in release",
    "behind a flag", "internal only", "not reachable by users",
    "nobody will guess", "only testers know the url",
    "obscure scheme", "not linked from the ui", "no user impact",
    "low risk", "just a test hook",
)

#: The four validation.* fields, and which parameter type each one is
#: valid for. Used both to require the right fields for a type and to
#: forbid the wrong ones (a stale allowed_values left on a 'string'
#: parameter after an author changed its type is exactly the kind of
#: silently-wrong record this module exists to refuse).
_VALIDATION_FIELD_OWNER = {
    "allowed_values": "enum",
    "max_length": "string",
    "min_value": "integer",
    "max_value": "integer",
}

#: Same shape as _VALIDATION_FIELD_OWNER above, for the same reason: an
#: entry_mechanism.identifier is required to be a non-empty string by
#: hand_rules already, but nothing tied its SHAPE to the declared
#: mechanism, so a 'universal-link-path' mechanism could carry "/*"
#: (claims the whole domain's link space, which is no fence at all) or
#: a 'deep-link-url-scheme' mechanism could carry something that is not
#: shaped like a URL scheme at all. Regex, not a full URL parser: cheap
#: shape check only, same honesty as _PLACEHOLDER_VALUES above.
_ENTRY_MECHANISM_IDENTIFIER_SHAPE = {
    "deep-link-url-scheme": re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://\S+$"),
    "universal-link-path": re.compile(r"^/\S+$"),
}

#: An identifier claiming this exact value under 'universal-link-path'
#: names the whole domain's link space, not one test route.
_WILDCARD_LINK_PATHS = {"/", "/*"}

#: Shell/injection-shaped characters no legitimate scheme, path, menu
#: item, launch argument, or env var name needs; present regardless of
#: mechanism, they mark garbage rather than a real identifier.
_INJECTION_CHARS = set(";|&`$<>\n\r")


def _check_entry_mechanism_shape(problems, mechanism, identifier):
    if not isinstance(identifier, str) or not identifier.strip():
        return  # already reported by _require_nonempty
    value = identifier.strip()
    if any(ch in _INJECTION_CHARS for ch in value):
        problems.append(
            "entry_mechanism.identifier: %r contains shell-metacharacter-"
            "shaped content, not a real identifier" % value)
        return
    if mechanism == "universal-link-path" and value in _WILDCARD_LINK_PATHS:
        problems.append(
            "entry_mechanism.identifier: %r claims the whole domain's "
            "link space for mechanism 'universal-link-path' -- name one "
            "specific test route's path" % value)
        return
    shape = _ENTRY_MECHANISM_IDENTIFIER_SHAPE.get(mechanism)
    if shape is not None and not shape.match(value):
        problems.append(
            "entry_mechanism.identifier: %r is not shaped like a real "
            "identifier for mechanism %r" % (value, mechanism))


def load_route(path):
    """Read and JSON-parse a route record file. Raises CC.NoData (never
    a bare exception) on a missing file or invalid JSON, exactly as
    CC.load_json already does for every other schema-backed record in
    this repo -- reused rather than reimplemented."""
    return CC.load_json(path, "mobile-test-route-v1 record")


def _require_nonempty(problems, path, value):
    if not isinstance(value, str) or not value.strip():
        problems.append("%s: required (non-empty string)" % path)
        return False
    return True


def _reject_placeholder(problems, path, value):
    if not isinstance(value, str):
        return
    normalized = value.strip().lower()
    if normalized in _PLACEHOLDER_VALUES:
        problems.append(
            "%s: %r reads as a placeholder, not a real value -- state the "
            "actual mechanism/reviewer/risk, or leave this route unbuilt "
            "until you can" % (path, value.strip()))
        return
    for phrase in _REASSURANCE_PHRASES:
        if phrase in normalized:
            problems.append(
                "%s: %r reads as reassurance ('%s'), not a real, checkable "
                "fence -- name the actual mechanism/identifier/check, or "
                "leave this route unbuilt until you can" % (path, value.strip(), phrase))
            return


def _check_route_parameter(problems, index, param):
    path = "route_parameters[%d]" % index
    if not isinstance(param, dict):
        problems.append("%s: must be an object" % path)
        return
    _require_nonempty(problems, "%s.name" % path, param.get("name"))
    _require_nonempty(problems, "%s.description" % path, param.get("description"))

    ptype = param.get("type")
    validation = param.get("validation")
    if not isinstance(validation, dict):
        # Already reported as a structural problem by CC.validate
        # (validation is a required field); nothing more to check.
        return

    # Forbid every validation.* field the parameter's own type does not
    # own -- independent of whether the type is one of the four known
    # values, so a mistyped/invalid type still gets every irrelevant
    # field flagged rather than silently skipped.
    for field, owner_type in _VALIDATION_FIELD_OWNER.items():
        if field in validation and ptype != owner_type:
            problems.append(
                "%s.validation.%s: only allowed when type is %r, got type %r"
                % (path, field, owner_type, ptype))

    if ptype == "enum":
        allowed = validation.get("allowed_values")
        if not isinstance(allowed, list) or not allowed:
            problems.append(
                "%s.validation.allowed_values: required (non-empty list) "
                "when type is 'enum'" % path)
        else:
            seen = set()
            for i, v in enumerate(allowed):
                if not isinstance(v, str) or not v.strip():
                    problems.append(
                        "%s.validation.allowed_values[%d]: must be a "
                        "non-empty string" % (path, i))
                elif v in seen:
                    problems.append(
                        "%s.validation.allowed_values: %r is duplicated"
                        % (path, v))
                else:
                    seen.add(v)
    elif ptype == "string":
        max_length = validation.get("max_length")
        if not isinstance(max_length, int) or isinstance(max_length, bool) \
                or max_length <= 0:
            problems.append(
                "%s.validation.max_length: required (positive integer) "
                "when type is 'string'" % path)
    elif ptype == "integer":
        min_value = validation.get("min_value")
        max_value = validation.get("max_value")
        min_ok = isinstance(min_value, int) and not isinstance(min_value, bool)
        max_ok = isinstance(max_value, int) and not isinstance(max_value, bool)
        if not min_ok:
            problems.append(
                "%s.validation.min_value: required (integer) when type is "
                "'integer' -- an unbounded integer parameter could reach "
                "arbitrary internal state by trial and error" % path)
        if not max_ok:
            problems.append(
                "%s.validation.max_value: required (integer) when type is "
                "'integer' -- an unbounded integer parameter could reach "
                "arbitrary internal state by trial and error" % path)
        if min_ok and max_ok and min_value > max_value:
            problems.append(
                "%s.validation: min_value (%r) must be <= max_value (%r)"
                % (path, min_value, max_value))
    elif ptype == "boolean":
        pass  # no validation.* field is owned by 'boolean'; the forbid
        # loop above already flags any that are present.
    # An invalid ptype is already reported by CC.validate (enum check on
    # 'type'); nothing more to add here.


def hand_rules(route):
    """The rules the schema's keyword subset cannot express: non-empty
    strings (no minLength in the enforced subset), per-parameter-type
    validation shape (no if/then), and the cross-field rule tying a
    'bypasses-verification' classification to an explicit risk
    acceptance. Returns every problem found, never stopping at the
    first (matching mobile_project_profile.hand_rules /
    mobile_toolchain_probe.hand_rules / mobile_state_fixture.hand_rules)."""
    problems = []

    _require_nonempty(problems, "route_id", route.get("route_id"))
    _require_nonempty(problems, "description", route.get("description"))

    target_state = route.get("target_state")
    if isinstance(target_state, dict):
        _require_nonempty(problems, "target_state.description",
                           target_state.get("description"))

    entry_mechanism = route.get("entry_mechanism")
    if isinstance(entry_mechanism, dict):
        identifier = entry_mechanism.get("identifier")
        _require_nonempty(problems, "entry_mechanism.identifier", identifier)
        _check_entry_mechanism_shape(
            problems, entry_mechanism.get("mechanism"), identifier)

    # THE FENCE. This is the rule the whole unit exists for: a
    # build_fence object that is present (the schema already requires
    # the key) but whose actual content is empty/whitespace/a
    # placeholder must still be refused, exactly as if the whole
    # object were missing -- never let a hollow fence look safe.
    build_fence = route.get("build_fence")
    if isinstance(build_fence, dict):
        fence_identifier = build_fence.get("fence_identifier")
        verification_note = build_fence.get("verification_note")
        if _require_nonempty(problems, "build_fence.fence_identifier",
                              fence_identifier):
            _reject_placeholder(problems, "build_fence.fence_identifier",
                                 fence_identifier)
        if _require_nonempty(problems, "build_fence.verification_note",
                              verification_note):
            _reject_placeholder(problems, "build_fence.verification_note",
                                 verification_note)
        # release_build_excluded is pinned to const:true by the schema,
        # so a false/missing value is already a structural problem from
        # CC.validate; nothing to re-check here.

    params = route.get("route_parameters")
    if isinstance(params, list):
        for i, param in enumerate(params):
            _check_route_parameter(problems, i, param)
        seen_names = set()
        for i, param in enumerate(params):
            if not isinstance(param, dict):
                continue
            name = param.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            if name in seen_names:
                problems.append(
                    "route_parameters[%d].name: %r is duplicated -- two "
                    "parameters with the same name can declare "
                    "contradictory types/bounds with no way to tell which "
                    "applies" % (i, name))
            else:
                seen_names.add(name)

    exercises_real_path = route.get("exercises_real_path")
    classification = None
    if isinstance(exercises_real_path, dict):
        classification = exercises_real_path.get("classification")
        _require_nonempty(problems, "exercises_real_path.rationale",
                           exercises_real_path.get("rationale"))

    production_exposure_review = route.get("production_exposure_review")
    residual_risk_accepted = None
    if isinstance(production_exposure_review, dict):
        residual_risk_accepted = production_exposure_review.get(
            "residual_risk_accepted")
        _require_nonempty(problems, "production_exposure_review.reviewer",
                           production_exposure_review.get("reviewer"))
        _require_nonempty(
            problems, "production_exposure_review.risk_if_shipped",
            production_exposure_review.get("risk_if_shipped"))

    # A route that bypasses verification is never allowed to pass
    # silently: the residual risk of shipping it must be explicitly
    # accepted, not merely present as an unread boolean default.
    if classification == "bypasses-verification" and residual_risk_accepted is not True:
        problems.append(
            "production_exposure_review.residual_risk_accepted: must be "
            "true when exercises_real_path.classification is "
            "'bypasses-verification' -- a route that proves nothing about "
            "the real path cannot ship without an explicit, recorded risk "
            "acceptance")

    return problems


def check(route, schema):
    """Routes through CC.checked() (scripts/contract_check.py) so
    hand_rules only runs once validate() found the record structurally
    sound -- see contract_check.checked()'s own docstring (the PR #709
    root-cause fix) for why hand_rules must never see a structurally
    invalid instance (a non-dict record, a dict entry where a string
    was required) on its own."""
    return CC.checked(route, schema, lambda: hand_rules(route))


#: Phrases that read as reassurance that this route is fine to leave
#: reachable, rather than as the explicit warning render_route_docs
#: exists to produce (adversarial self-review finding, 2026-09-15: the
#: fastest way for generated "developer docs" to defeat this whole
#: unit's point is to sound safe). This exact list is _REASSURANCE_
#: PHRASES above, reused rather than duplicated: hand_rules() refuses
#: any of these on input (fence_identifier/verification_note), which is
#: also what keeps them out of render_route_docs's output, since
#: verification_note is interpolated into the generated guide verbatim
#: and render_route_docs refuses to run on a record with any unresolved
#: problem. A filter that only caught the exact strings below would
#: give false confidence that some other unreviewed phrasing is fine;
#: _reject_placeholder's substring match is deliberately broader than
#: an exact match for that reason.
#:   "safe to ship", "harmless in production", "inactive by default",
#:   "debug-only", "we'll remove it later", "disabled in release",
#:   "behind a flag", "internal only", "not reachable by users",
#:   "nobody will guess", "only testers know the url",
#:   "obscure scheme", "not linked from the ui", "no user impact",
#:   "low risk", "just a test hook".


def _md_escape(value):
    """Minimal escaping for a record-supplied string before it is
    interpolated into the Markdown render_route_docs generates: a pipe
    character corrupts a table row's column count, and a newline can
    forge a heading or list item nothing in the record intended
    (adversarial review finding, PR #727 -- neither is hypothetical,
    both are plain characters a free-text field like description or
    verification_note can carry). Escapes/strips only, never refuses:
    hand_rules() above is where a record is refused; this only keeps an
    already-passing record from corrupting the doc it renders into."""
    if not isinstance(value, str):
        return value
    return (value.replace("|", "\\|")
                 .replace("\r\n", " ").replace("\n", " ").replace("\r", " "))


def render_route_docs(route, problems):
    """Render the Markdown a real project's own developers would need
    to wire this route into their app safely.

    Takes the route record AND the problems list check()/hand_rules()
    already computed for it (never recomputed here) -- and refuses to
    render anything unless that list is empty. A documentation
    generator that could produce a "how to wire this in" guide for a
    route that never proved it has a real fence would defeat the point
    of this whole unit, so that path does not exist: there is no code
    path in this module that renders docs for an unvalidated record.
    """
    if problems:
        raise ValueError(
            "render_route_docs refuses to render documentation for a "
            "route record with %d unresolved problem(s); fix them first "
            "(run check()/hand_rules() and resolve every entry): %s"
            % (len(problems), "; ".join(problems)))

    target_state = route["target_state"]
    entry_mechanism = route["entry_mechanism"]
    build_fence = route["build_fence"]
    exercises_real_path = route["exercises_real_path"]
    review = route["production_exposure_review"]

    lines = []
    lines.append("# Test route: %s" % _md_escape(route["route_id"]))
    lines.append("")
    lines.append(_md_escape(route["description"]))
    lines.append("")

    lines.append("## What this reaches")
    lines.append("")
    lines.append(_md_escape(target_state["description"]))
    if target_state.get("state_fixture_ref"):
        lines.append("")
        lines.append("State fixture: `%s`"
                      % _md_escape(target_state["state_fixture_ref"]))
    lines.append("")

    lines.append("## Entry mechanism")
    lines.append("")
    lines.append("- Mechanism: `%s`" % _md_escape(entry_mechanism["mechanism"]))
    lines.append("- Identifier: `%s`" % _md_escape(entry_mechanism["identifier"]))
    lines.append("")

    lines.append("## Build/config fence (mandatory, read before wiring this in)")
    lines.append("")
    lines.append(
        "This route must never be reachable in a build your users can "
        "install. The only acceptable evidence that it is excluded is the "
        "concrete check named below; if your build system cannot actually "
        "run that check, do not wire this route in until it can.")
    lines.append("")
    lines.append("- Fence mechanism: `%s`" % _md_escape(build_fence["fence_mechanism"]))
    lines.append("- Fence identifier: `%s`" % _md_escape(build_fence["fence_identifier"]))
    lines.append("- Excluded from release build: `true` (declared)")
    lines.append("- How that exclusion is verified: %s"
                  % _md_escape(build_fence["verification_note"]))
    lines.append("")
    lines.append(
        "Do not treat an undocumented URL scheme, a hidden menu, or "
        "\"nobody will guess this\" as a fence on its own. If the check "
        "named above stops running (a CI job is disabled, a build "
        "configuration changes), this route is production-exposed again "
        "even though nothing in this file changed.")
    lines.append("")

    params = route.get("route_parameters") or []
    lines.append("## Parameters")
    lines.append("")
    if not params:
        lines.append("This route takes no parameters.")
    else:
        lines.append("| name | type | required | validation | description |")
        lines.append("|---|---|---|---|---|")
        for p in params:
            validation = p.get("validation") or {}
            lines.append("| `%s` | %s | %s | %s | %s |" % (
                _md_escape(p["name"]), _md_escape(p["type"]), p["required"],
                _md_escape(json.dumps(validation, sort_keys=True)),
                _md_escape(p["description"])))
    lines.append("")

    lines.append("## Does this prove the real user-visible path?")
    lines.append("")
    lines.append("Classification: `%s`"
                  % _md_escape(exercises_real_path["classification"]))
    lines.append("")
    lines.append(_md_escape(exercises_real_path["rationale"]))
    if exercises_real_path["classification"] != "full-real-path":
        lines.append("")
        lines.append(
            "A journey proof that starts from this route is NOT full "
            "evidence for whatever it skips. Cite it only for the parts "
            "of the journey it actually exercises.")
    lines.append("")

    lines.append("## Production exposure review")
    lines.append("")
    lines.append("- Reviewer: %s" % _md_escape(review["reviewer"]))
    lines.append("- Risk if this ever shipped active in a release build: %s"
                  % _md_escape(review["risk_if_shipped"]))
    lines.append("- Residual risk accepted: `%s`"
                  % review["residual_risk_accepted"])
    lines.append("")
    lines.append(
        "This acceptance covers the risk as described above, at the time "
        "it was reviewed. A change to the fence mechanism, the "
        "verification method, or what this route reaches invalidates this "
        "review; re-review before relying on it again.")
    lines.append("")

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("route_path")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--render-guide", action="store_true",
        help="on a passing record, print the developer Markdown guide "
             "instead of the JSON record; refused (exit 1, no guide "
             "printed) when the record has any problem")
    args = parser.parse_args(argv)
    try:
        route = load_route(args.route_path)
        schema = CC.load_json(args.schema, "mobile-test-route-v1 schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc, file=sys.stderr)
        return 2

    problems = check(route, schema)

    if args.render_guide:
        if problems:
            print("PROBLEMS (guide not rendered):", file=sys.stderr)
            for p in problems:
                print(" - %s" % p, file=sys.stderr)
            return 1
        print(render_route_docs(route, problems))
        return 0

    print(json.dumps(route, indent=2, sort_keys=True))
    if problems:
        print("PROBLEMS:", file=sys.stderr)
        for p in problems:
            print(" - %s" % p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
