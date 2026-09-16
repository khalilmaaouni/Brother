#!/usr/bin/env python3
"""WBS-30.04 / EPIC M1.04 Mobile plan compiler.

Consumes ONE validated mobile-journey-contract-v1 record (docs/schema/
mobile-journey-contract-v1.json, WBS-30.01) and emits normal Brother units:
plain dicts in the shape scripts/work_record.py's check_units() validates
(id, title, done_check, owns, depends_on) and scripts/loop_bridge.py's
run_node() dispatches (id, name/title, done_check, owns, depends_on) --
confirmed from both files, not invented. scripts/door.py's normalize_unit()
already bridges the objective/writes/deps prompt-facing spelling to this
same title/owns/depends_on shape, so every unit below carries both spellings
and needs no bridging step. There is no separate "mobile worker pool" here,
per the roadmap: these are the same units the estate already runs.

WHY EVERY UNIT NAMES ITS SOURCE FIELD, not just its category: the Muse
critique of this exact roadmap row (docs/plan/1.0.17/
WAVE-2-DESIGN-CRITIQUES-2026-09-13.md, "Mobile plan compiler (WBS-30.04)")
attacks a generated task breakdown that looks complete while silently
picking an implementation strategy (e.g. "reversible: true" compiled into a
Redux-undo-stack choice nobody decided). This compiler never does that: it
emits no implementation strategy at all, only a category (domain/state,
view/navigation, network/persistence, accessibility, localization, tests,
instrumentation) plus the exact contract text that put the unit there. The
architectural decision the roadmap says a human must own stays a human
decision inside the generated unit's own scope, not something the compiler
picked for them.

CATEGORY -> CONTRACT FIELD MAPPING, stated here rather than buried in code,
so the mapping itself is auditable:
  domain/state         <- entry_state, exit_state, interruption_obligations
  view/navigation      <- human_outcome, visual_reference_ids,
                          human_acceptance_items
  network/persistence  <- network_state_obligations, privacy_constraints
  accessibility        <- accessibility_obligations
  localization         <- locales
  tests                <- required_native_tests
  instrumentation      <- post_release_claims, performance_budgets

EMPTY IS NOT SKIPPED: an optional array (accessibility_obligations,
network_state_obligations, interruption_obligations, privacy_constraints,
visual_reference_ids, human_acceptance_items, post_release_claims,
performance_budgets) that is empty still produces its unit. The unit's
objective states the gap in plain words ("no X declared ... confirm this is
deliberate, not an oversight") instead of the category silently vanishing
from the output with no trace of why.

EPIC M1.04 (2026-09-15) SPLIT, per docs/plan/MOBILE-EPIC-M1-UNITS-CANONICAL.md
(read that file before touching this one's unit numbering again): this
compiler used to hardcode Swift filenames and "SwiftUI" wording straight
into its one function, so every non-iOS adopter would have inherited a
false Swift assumption on day one. It is now two layers:

  compile_semantic_plan(journey_contract, schema=None) -> {"journey_id",
      "units": [...]}
      Platform-neutral. Every unit carries a category (one of the seven
      above), the contract field/values it traces to, and an
      "artifact_kind" slug (e.g. "domain", "view", "localization") -- no
      filename, no file extension, no framework name anywhere in this
      structure. This is the layer the roadmap's M1 exit criterion means by
      "semantic plan with zero project-name conditionals in core."

  compile_project_plan(semantic_plan, project_profile=None, adapter=None)
      Resolves each semantic unit's artifact_kind into a real filename via
      one PROJECT ADAPTER (see ADAPTERS below), producing the same
      dispatchable unit shape this module always emitted (id, title,
      done_check, owns, writes, depends_on, deps, role, risk_class). The
      adapter is picked from `adapter` if given, else from
      `project_profile`'s mobile-project-profile-v1 "platforms" list (see
      scripts/mobile_project_profile.py, M1.01/M1.02), else the default
      iOS/SwiftUI adapter -- never refused: an unrecognized platform or a
      missing profile is a detection gap, not a reason to block a plan.

  compile_plan(journey_contract, schema=None)
      Unchanged entrypoint, kept for every existing caller
      (scripts/canary_pipeline_smoke.py included): compile_semantic_plan()
      immediately fed into compile_project_plan() with the default iOS
      adapter. Same unit ids, same "owns" paths, same filenames as before
      this split.

A full multi-platform adapter registry (Android/RN/Flutter) is NOT in scope
here (M1.06 covers an adapter conformance suite); ADAPTERS holds exactly one
entry today, sized to prove the split rather than to speculatively cover
every stack the roadmap eventually wants.

Python 3.9, standard library only. No network.
"""
import argparse
import json
import os
import posixpath
import shlex
import sys

import contract_check as CC
import mobile_journey_contract as MJC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOBILE_ROOT = "mobile"


class PlanCompilerError(Exception):
    """The journey contract failed its own mobile-journey-contract-v1
    validation. Raised before any unit is generated: compile_semantic_plan()
    never returns a partial plan for an invalid contract, since a plan built
    on an unvalidated record would trace its units back to a record nobody
    can trust."""


# Every character below, plus the two-character ".." sequence, is refused in
# a journey_id or an adapter stem/ext by _validate_safe_component(). This is
# the ONE shared choke point for both untrusted values named by the M1.05
# adversarial review (2026-09-15, PR #717 follow-up): journey_id (no schema
# "pattern", docs/schema/mobile-journey-contract-v1.json) and an adapter's
# stem/ext (resolve_adapter() returns a caller-supplied dict verbatim). The
# prior fix (_owns_path()'s escape check) caught only the one place both
# already-joined values meet; this validator runs where each value ENTERS
# the module instead, so a sibling consumer (mobile_ownership_resolver.py's
# own stem/ext join) inherits the fix without a second patch. Path
# separators close the traversal class; the shell metacharacters close the
# class of a value later reaching a shell=True done_check string
# (scripts/fast_path.py, scripts/model_worker.py).
_UNSAFE_COMPONENT_CHARS = frozenset("/\\;|&$`\n\r()<>*?~\x00")


def _validate_safe_component(value, what):
    """Refuse `value` (a journey_id, or one half of an adapter stem/ext
    pair) unless it is a non-empty string with no path separator, no ".."
    segment, no NUL byte, and no shell metacharacter. Raises
    PlanCompilerError naming `what` on refusal; never sanitizes or
    truncates a bad value, since a silently-repaired value is exactly the
    "normalizes rather than refuses" gap this validator exists to close."""
    if not isinstance(value, str) or not value:
        raise PlanCompilerError(
            "%s %r is not a non-empty string -- refused" % (what, value))
    if value.find("..") >= 0 or any(ch in _UNSAFE_COMPONENT_CHARS for ch in value):
        raise PlanCompilerError(
            "%s %r contains a path separator, '..', or a shell "
            "metacharacter -- refused" % (what, value))


def _gap_objective(journey_id, category, field_name):
    return ("%s for journey %s: no %s declared in this journey contract -- "
           "confirm this is deliberate, not an oversight"
           % (category, journey_id, field_name))


def _list_objective(journey_id, category, field_name, values):
    return ("%s for journey %s, from %s: %s"
           % (category, journey_id, field_name, "; ".join(values)))


def _semantic_unit(unit_id, objective, category, artifact_kind,
                   contract_field, contract_values, depends_on=None):
    return {
        "id": unit_id,
        "title": objective,
        "objective": objective,
        "category": category,
        "artifact_kind": artifact_kind,
        "contract_field": contract_field,
        "contract_values": list(contract_values),
        "depends_on": list(depends_on or []),
        "role": "builder",
        "risk_class": "normal",
    }


def _obligations_semantic_unit(unit_id, journey_id, category, artifact_kind,
                               field_name, values, depends_on=None):
    """One semantic unit for one array-shaped obligation field. Empty is a
    stated gap, never a skip -- see module docstring."""
    if values:
        objective = _list_objective(journey_id, category, field_name, values)
    else:
        objective = _gap_objective(journey_id, category, field_name)
    return _semantic_unit(unit_id, objective, category,
                          artifact_kind, field_name, values,
                          depends_on=depends_on)


def compile_semantic_plan(journey_contract, schema=None):
    """Validate journey_contract against mobile-journey-contract-v1, refuse
    with PlanCompilerError if invalid, else return a platform-neutral
    semantic plan: {"journey_id": str, "units": [semantic unit dict, ...]}.
    No filename, file extension, or framework name appears anywhere in the
    returned structure -- see the M1.04 note in the module docstring.
    schema defaults to docs/schema/mobile-journey-contract-v1.json
    (MJC.DEFAULT_SCHEMA); pass one in to test against a different schema
    file without touching disk state."""
    schema = schema if schema is not None else CC.load_json(
        MJC.DEFAULT_SCHEMA, "journey contract schema")
    problems = MJC.check(journey_contract, schema)
    if problems:
        raise PlanCompilerError(
            "journey contract %r fails mobile-journey-contract-v1 "
            "validation (%d problem(s), first: %s)"
            % (journey_contract.get("journey_id"), len(problems), problems[0]))

    jid = journey_contract["journey_id"]
    _validate_safe_component(jid, "journey_id")
    units = []

    # domain/state <- entry_state, exit_state, interruption_obligations
    domain_id = "%s-domain" % jid
    domain_objective = (
        "domain/state for journey %s: entry_state=%r, exit_state=%r"
        % (jid, journey_contract["entry_state"], journey_contract["exit_state"]))
    units.append(_semantic_unit(
        domain_id, domain_objective, "domain/state", "domain",
        "entry_state,exit_state",
        [journey_contract["entry_state"], journey_contract["exit_state"]]))
    interruption_id = "%s-interruption" % jid
    units.append(_obligations_semantic_unit(
        interruption_id, jid, "domain/state (interruption handling)",
        "interruption", "interruption_obligations",
        journey_contract["interruption_obligations"], depends_on=[domain_id]))

    # view/navigation <- human_outcome, visual_reference_ids, human_acceptance_items
    view_id = "%s-view" % jid
    view_objective = ("view/navigation for journey %s, human outcome: %s"
                      % (jid, journey_contract["human_outcome"]))
    units.append(_semantic_unit(
        view_id, view_objective, "view/navigation", "view",
        "human_outcome", [journey_contract["human_outcome"]],
        depends_on=[domain_id]))
    visual_id = "%s-visual-reference" % jid
    units.append(_obligations_semantic_unit(
        visual_id, jid, "view/navigation (visual reference)",
        "visual_reference", "visual_reference_ids",
        journey_contract["visual_reference_ids"], depends_on=[view_id]))
    acceptance_id = "%s-human-acceptance" % jid
    units.append(_obligations_semantic_unit(
        acceptance_id, jid, "view/navigation (human acceptance)",
        "human_acceptance", "human_acceptance_items",
        journey_contract["human_acceptance_items"], depends_on=[view_id]))

    # network/persistence <- network_state_obligations, privacy_constraints
    network_id = "%s-network" % jid
    units.append(_obligations_semantic_unit(
        network_id, jid, "network/persistence", "network",
        "network_state_obligations",
        journey_contract["network_state_obligations"], depends_on=[domain_id]))
    privacy_id = "%s-privacy" % jid
    units.append(_obligations_semantic_unit(
        privacy_id, jid, "network/persistence (privacy)", "privacy",
        "privacy_constraints", journey_contract["privacy_constraints"],
        depends_on=[domain_id]))

    # accessibility <- accessibility_obligations
    a11y_id = "%s-accessibility" % jid
    units.append(_obligations_semantic_unit(
        a11y_id, jid, "accessibility", "accessibility",
        "accessibility_obligations",
        journey_contract["accessibility_obligations"], depends_on=[view_id]))

    # localization <- locales
    locales = journey_contract["locales"]
    locale_id = "%s-localization" % jid
    locale_objective = ("localization for journey %s, locales: %s"
                        % (jid, "; ".join(locales)))
    units.append(_semantic_unit(
        locale_id, locale_objective, "localization", "localization",
        "locales", locales, depends_on=[view_id]))

    # tests <- required_native_tests
    tests = journey_contract["required_native_tests"]
    tests_id = "%s-tests" % jid
    tests_objective = ("tests for journey %s, required tests: %s"
                       % (jid, "; ".join(tests)))
    units.append(_semantic_unit(
        tests_id, tests_objective, "tests", "tests",
        "required_native_tests", tests,
        depends_on=[domain_id, view_id, network_id]))

    # instrumentation <- post_release_claims, performance_budgets
    claims_id = "%s-instrumentation" % jid
    units.append(_obligations_semantic_unit(
        claims_id, jid, "instrumentation", "instrumentation",
        "post_release_claims", journey_contract["post_release_claims"],
        depends_on=[domain_id]))
    perf_values = [
        "%s: %s" % (b.get("metric"), b.get("budget"))
        for b in journey_contract["performance_budgets"]
    ]
    perf_id = "%s-performance-budgets" % jid
    units.append(_obligations_semantic_unit(
        perf_id, jid, "instrumentation (performance budgets)",
        "performance_budgets", "performance_budgets", perf_values,
        depends_on=[domain_id]))

    return {"journey_id": jid, "units": units}


# EPIC M1.04: one project adapter per known stack, keyed by artifact_kind.
# Each entry maps a semantic unit's artifact_kind to (filename_stem,
# extension). This is the ONLY place a filename or file extension is named
# in this module -- the semantic layer above never sees one. A full adapter
# registry (Android/RN/Flutter) is M1.06's job; today there is exactly one
# adapter, reproducing this compiler's pre-split Swift/SwiftUI output.
DEFAULT_ADAPTER_ID = "ios-swiftui"
ADAPTERS = {
    "ios-swiftui": {
        "domain": ("Domain", "swift"),
        "interruption": ("Interruption", "swift"),
        "view": ("View", "swift"),
        "visual_reference": ("Visuals", "swift"),
        "human_acceptance": ("HumanAcceptance", "md"),
        "network": ("Network", "swift"),
        "privacy": ("Privacy", "swift"),
        "accessibility": ("Accessibility", "swift"),
        "localization": ("Localizable", "strings"),
        "tests": ("JourneyTests", "swift"),
        "instrumentation": ("Instrumentation", "swift"),
        "performance_budgets": ("PerformanceBudgets", "swift"),
    },
}

# Which mobile-project-profile-v1 "platforms" entry picks which adapter.
# Only "ios" is wired since ADAPTERS holds only one adapter today; a
# platform with no entry here falls through to DEFAULT_ADAPTER_ID.
_PLATFORM_TO_ADAPTER_ID = {"ios": "ios-swiftui"}


def resolve_adapter(project_profile=None, adapter=None):
    """Pick a project adapter (a dict of artifact_kind -> (stem, ext)).
    `adapter` wins if given, as either an adapter id string (looked up in
    ADAPTERS, an unrecognized id falling back to DEFAULT_ADAPTER_ID rather
    than refusing) or an adapter dict supplied directly by the caller
    (for a stack not yet in the registry). Otherwise `project_profile`
    (a mobile-project-profile-v1 record, scripts/mobile_project_profile.py)
    picks one from its "platforms" list. No profile, no match, or no
    adapter given at all: DEFAULT_ADAPTER_ID -- an unrecognized or absent
    profile is a detection gap, never a reason to block a plan. Also never
    raises on a MALFORMED profile (wrong type, "platforms" not a list, a
    platform entry that is not a plain hashable id) -- a malformed
    detection result is the same kind of gap as a missing one (Muse
    adversarial review, EPIC M1.04).

    Returns (adapter_dict, matched). matched is True whenever the adapter
    was trusted directly (an explicit `adapter` was given) or when
    `project_profile` is None (nothing to have mismatched) or when a
    platform in project_profile really did resolve to a known adapter.
    matched is False only when project_profile was given and nothing in it
    (a real, unrecognized, or malformed "platforms" value) matched a known
    adapter -- the one case where returning DEFAULT_ADAPTER_ID silently is
    the Major-1 wrong-answer gap a caller needs to be able to report."""
    if isinstance(adapter, dict):
        # A caller-supplied adapter dict is untrusted (Muse adversarial
        # review, 2026-09-15): every (stem, ext) pair it carries is
        # validated here, the single place this whole module hands an
        # adapter dict to every consumer, so mobile_ownership_resolver.py's
        # own stem/ext join inherits this refusal without its own patch.
        # A malformed (non-2-tuple) entry is left to fail downstream as
        # before -- shape errors are out of this validator's scope.
        # This branch is PR #705's Major 2 solved at the entry point rather
        # than at the owns join. #705's own _refuse_unsafe_adapter_component
        # helper is not carried over with it: _owns_path() below already
        # refuses a strict superset of what it caught (the same separator,
        # "..", and NUL byte, plus shell metacharacters, plus journey_id,
        # plus a mobile/ root-containment check), so a third copy would be
        # dead code, never a gate anything reaches.
        for kind, pair in adapter.items():
            if isinstance(pair, (tuple, list)) and len(pair) == 2:
                stem, ext = pair
                _validate_safe_component(stem, "adapter stem for %r" % kind)
                _validate_safe_component(ext, "adapter ext for %r" % kind)
        return adapter, True
    if isinstance(adapter, str):
        return ADAPTERS.get(adapter, ADAPTERS[DEFAULT_ADAPTER_ID]), True
    if project_profile is None:
        return ADAPTERS[DEFAULT_ADAPTER_ID], True
    platforms = project_profile.get("platforms") if isinstance(project_profile, dict) else None
    try:
        for platform in platforms or []:
            try:
                adapter_id = _PLATFORM_TO_ADAPTER_ID.get(platform)
            except TypeError:  # unhashable platform entry, e.g. a dict
                continue
            if adapter_id:
                return ADAPTERS[adapter_id], True
    except TypeError:  # "platforms" present but not actually iterable
        pass
    return ADAPTERS[DEFAULT_ADAPTER_ID], False


def _owns_path(jid, stem, ext):
    """Build the "mobile/<jid>/<stem>.<ext>" owns path and refuse one that
    escapes the mobile/ project root. jid comes straight from the journey
    contract's journey_id (the schema's enforced keyword subset has no
    pattern check, docs/schema/mobile-journey-contract-v1.json); stem/ext
    come from resolve_adapter(), which accepts a caller-supplied adapter
    dict verbatim. Neither is sanitized before this point, so a
    "../../evil-journey" journey_id or a "../../../../etc/evil" adapter
    stem must be caught here, the one place both paths join, rather than
    trusted (PR #715's test_malicious_adapter_stem_or_journey_id_escape_
    the_project_root_today pinned this as a real, unfixed gap). Both
    callers (compile_semantic_plan()'s journey_id read, resolve_adapter()'s
    stem/ext) already refuse an unsafe value before this point; the
    explicit re-check below is defense in depth for a direct
    compile_project_plan()/_owns_path() caller that skips those entry
    points, and closes the MINOR finding that a jid containing '..' or '/'
    was silently normalized to a colliding path instead of refused
    outright (Muse adversarial review, 2026-09-15)."""
    _validate_safe_component(jid, "journey_id")
    _validate_safe_component(stem, "adapter stem")
    _validate_safe_component(ext, "adapter ext")
    raw = "%s/%s/%s.%s" % (MOBILE_ROOT, jid, stem, ext)
    normalized = posixpath.normpath(raw)
    if normalized != MOBILE_ROOT and not normalized.startswith(MOBILE_ROOT + "/"):
        raise PlanCompilerError(
            "owns path %r (from journey_id=%r, stem=%r, ext=%r) escapes "
            "the %s/ project root" % (raw, jid, stem, ext, MOBILE_ROOT))
    return normalized


def compile_project_plan(semantic_plan, project_profile=None, adapter=None):
    """Resolve a compile_semantic_plan() result into real dispatchable
    Brother units: the same id/title/done_check/owns/writes/depends_on/deps/
    role/risk_class/category/contract_field/contract_values shape this
    module always emitted, satisfying scripts/work_record.py's
    check_units(). `project_profile` and `adapter` are passed straight to
    resolve_adapter() (see there for precedence). A caller-supplied adapter
    dict missing an entry for one of the twelve known artifact_kinds falls
    back to DEFAULT_ADAPTER_ID's mapping for that one kind rather than
    raising KeyError (Muse adversarial review, EPIC M1.04): a partial
    custom adapter is a detection gap for that kind, not a reason to
    refuse the whole plan. A resolved stem/ext that is not a bare filename
    component (path separator, "..", or NUL byte) refuses the whole plan
    via PlanCompilerError instead: see _owns_path."""
    jid = semantic_plan["journey_id"]
    resolved, _matched = resolve_adapter(project_profile=project_profile, adapter=adapter)
    default = ADAPTERS[DEFAULT_ADAPTER_ID]
    units = []
    for su in semantic_plan["units"]:
        kind = su["artifact_kind"]
        stem, ext = resolved[kind] if kind in resolved else default[kind]
        owns = [_owns_path(jid, stem, ext)]
        depends_on = list(su["depends_on"])
        units.append({
            "id": su["id"],
            "title": su["title"],
            "objective": su["objective"],
            # owns[0] is already refused if it carried a shell metacharacter
            # (_owns_path()'s _validate_safe_component() calls), but the
            # subprocess boundary this string eventually crosses
            # (scripts/fast_path.py, scripts/model_worker.py, shell=True)
            # gets its own explicit failure path per this estate's own
            # execution rule: defense in depth, not the only guard.
            "done_check": "test -f %s" % shlex.quote(owns[0]),
            "owns": owns,
            "writes": owns,
            "depends_on": depends_on,
            "deps": depends_on,
            "category": su["category"],
            "contract_field": su["contract_field"],
            "contract_values": list(su["contract_values"]),
            "role": su["role"],
            "risk_class": su["risk_class"],
        })
    return units


def compile_plan(journey_contract, schema=None):
    """Unchanged entrypoint for every existing caller: validates
    journey_contract (see compile_semantic_plan) then resolves it with the
    default iOS/SwiftUI adapter, producing the same unit shape, ids and
    "owns" paths this module emitted before the M1.04 split."""
    semantic_plan = compile_semantic_plan(journey_contract, schema=schema)
    return compile_project_plan(semantic_plan)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to the mobile-journey-contract JSON record")
    ap.add_argument("--schema", default=MJC.DEFAULT_SCHEMA)
    ap.add_argument("--profile", help="path to a mobile-project-profile-v1 "
                    "JSON record (scripts/mobile_project_profile.py); picks "
                    "the project adapter when --adapter is not given")
    ap.add_argument("--adapter", choices=sorted(ADAPTERS),
                    help="materialize filenames for this adapter id "
                    "(default: resolved from --profile, else %r)"
                    % DEFAULT_ADAPTER_ID)
    ap.add_argument("--out", help="write the unit list as JSON to this path "
                    "instead of stdout")
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "journey contract record")
        schema = CC.load_json(args.schema, "journey contract schema")
        profile = CC.load_json(args.profile, "project profile record") if args.profile else None
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    resolved_adapter, matched = resolve_adapter(
        project_profile=profile, adapter=args.adapter)
    if not matched:
        declared = profile.get("platforms") if isinstance(profile, dict) else profile
        print("NO-DATA: project profile declared platform(s) %r with no "
             "matching adapter -- falling back to default adapter %r"
             % (declared, DEFAULT_ADAPTER_ID), file=sys.stderr)
    try:
        semantic_plan = compile_semantic_plan(record, schema=schema)
        units = compile_project_plan(semantic_plan, adapter=resolved_adapter)
    except PlanCompilerError as exc:
        print("FAIL: %s" % exc)
        return 1
    payload = json.dumps(units, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print("PASS: wrote %d unit(s) to %s" % (len(units), args.out))
    else:
        print(payload)
        print("PASS: %d unit(s) generated" % len(units), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
