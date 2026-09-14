#!/usr/bin/env python3
"""WBS-30.04 Mobile plan compiler.

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
emits no implementation strategy at all, only a category (state/domain,
view, persistence/network, accessibility, localization, tests,
instrumentation) plus the exact contract text that put the unit there. The
architectural decision the roadmap says a human must own stays a human
decision inside the generated unit's own scope, not something the compiler
picked for them.

CATEGORY -> CONTRACT FIELD MAPPING, stated here rather than buried in code,
so the mapping itself is auditable:
  state/domain logic  <- entry_state, exit_state, interruption_obligations
  SwiftUI view         <- human_outcome, visual_reference_ids,
                          human_acceptance_items
  persistence/network  <- network_state_obligations, privacy_constraints
  accessibility        <- accessibility_obligations
  localization          <- locales
  tests                  <- required_native_tests
  instrumentation        <- post_release_claims, performance_budgets

EMPTY IS NOT SKIPPED: an optional array (accessibility_obligations,
network_state_obligations, interruption_obligations, privacy_constraints,
visual_reference_ids, human_acceptance_items, post_release_claims,
performance_budgets) that is empty still produces its unit. The unit's
objective states the gap in plain words ("no X declared ... confirm this is
deliberate, not an oversight") instead of the category silently vanishing
from the output with no trace of why.

Python 3.9, standard library only. No network.
"""
import argparse
import json
import os
import sys

import contract_check as CC
import mobile_journey_contract as MJC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PlanCompilerError(Exception):
    """The journey contract failed its own mobile-journey-contract-v1
    validation. Raised before any unit is generated: compile_plan() never
    returns a partial list for an invalid contract, since a plan built on an
    unvalidated record would trace its units back to a record nobody can
    trust."""


def _owns(journey_id, filename):
    """One generated relative path per unit, scoped under mobile/<journey>/.
    This compiler has no view of the real native project layout (none is
    given in a journey contract, and Brother never names the target app by
    name), so the path is a placeholder scope for the unit, not a claim
    about the app's actual file tree. Relative and inside the repo, which is
    what work_record.check_units() requires of owns."""
    return ["mobile/%s/%s" % (journey_id, filename)]


def _unit(unit_id, journey_id, objective, filename, category, contract_field,
         contract_values, depends_on=None):
    owns = _owns(journey_id, filename)
    return {
        "id": unit_id,
        "title": objective,
        "objective": objective,
        "done_check": "test -f %s" % owns[0],
        "owns": owns,
        "writes": owns,
        "depends_on": list(depends_on or []),
        "deps": list(depends_on or []),
        "category": category,
        "contract_field": contract_field,
        "contract_values": list(contract_values),
        "role": "builder",
        "risk_class": "normal",
    }


def _gap_objective(journey_id, category, field_name):
    return ("%s for journey %s: no %s declared in this journey contract -- "
           "confirm this is deliberate, not an oversight"
           % (category, journey_id, field_name))


def _list_objective(journey_id, category, field_name, values):
    return ("%s for journey %s, from %s: %s"
           % (category, journey_id, field_name, "; ".join(values)))


def _obligations_unit(unit_id, journey_id, category, filename, field_name,
                      values, depends_on=None):
    """One unit for one array-shaped obligation field. Empty is a stated gap,
    never a skip -- see module docstring."""
    if values:
        objective = _list_objective(journey_id, category, field_name, values)
    else:
        objective = _gap_objective(journey_id, category, field_name)
    return _unit(unit_id, journey_id, objective, filename, category,
                field_name, values, depends_on=depends_on)


def compile_plan(journey_contract, schema=None):
    """Validate journey_contract against mobile-journey-contract-v1, refuse
    with PlanCompilerError if invalid, else return the list of generated
    unit dicts. schema defaults to docs/schema/mobile-journey-contract-v1.json
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
    units = []

    # state/domain logic <- entry_state, exit_state, interruption_obligations
    domain_id = "%s-domain" % jid
    domain_objective = (
        "State/domain logic for journey %s: entry_state=%r, exit_state=%r"
        % (jid, journey_contract["entry_state"], journey_contract["exit_state"]))
    units.append(_unit(
        domain_id, jid, domain_objective, "Domain.swift", "state/domain logic",
        "entry_state,exit_state",
        [journey_contract["entry_state"], journey_contract["exit_state"]]))
    interruption_id = "%s-interruption" % jid
    units.append(_obligations_unit(
        interruption_id, jid, "state/domain logic (interruption handling)",
        "Interruption.swift", "interruption_obligations",
        journey_contract["interruption_obligations"], depends_on=[domain_id]))

    # SwiftUI view <- human_outcome, visual_reference_ids, human_acceptance_items
    view_id = "%s-view" % jid
    view_objective = ("SwiftUI view for journey %s, human outcome: %s"
                      % (jid, journey_contract["human_outcome"]))
    units.append(_unit(
        view_id, jid, view_objective, "View.swift", "SwiftUI view",
        "human_outcome", [journey_contract["human_outcome"]],
        depends_on=[domain_id]))
    visual_id = "%s-visual-reference" % jid
    units.append(_obligations_unit(
        visual_id, jid, "SwiftUI view (visual reference)", "Visuals.swift",
        "visual_reference_ids", journey_contract["visual_reference_ids"],
        depends_on=[view_id]))
    acceptance_id = "%s-human-acceptance" % jid
    units.append(_obligations_unit(
        acceptance_id, jid, "SwiftUI view (human acceptance)",
        "HumanAcceptance.md", "human_acceptance_items",
        journey_contract["human_acceptance_items"], depends_on=[view_id]))

    # persistence/network <- network_state_obligations, privacy_constraints
    network_id = "%s-network" % jid
    units.append(_obligations_unit(
        network_id, jid, "persistence/network", "Network.swift",
        "network_state_obligations",
        journey_contract["network_state_obligations"], depends_on=[domain_id]))
    privacy_id = "%s-privacy" % jid
    units.append(_obligations_unit(
        privacy_id, jid, "persistence/network (privacy)", "Privacy.swift",
        "privacy_constraints", journey_contract["privacy_constraints"],
        depends_on=[domain_id]))

    # accessibility <- accessibility_obligations
    a11y_id = "%s-accessibility" % jid
    units.append(_obligations_unit(
        a11y_id, jid, "accessibility", "Accessibility.swift",
        "accessibility_obligations",
        journey_contract["accessibility_obligations"], depends_on=[view_id]))

    # localization <- locales
    locales = journey_contract["locales"]
    locale_id = "%s-localization" % jid
    locale_objective = ("localization for journey %s, locales: %s"
                        % (jid, "; ".join(locales)))
    units.append(_unit(
        locale_id, jid, locale_objective, "Localizable.strings",
        "localization", "locales", locales, depends_on=[view_id]))

    # tests <- required_native_tests
    tests = journey_contract["required_native_tests"]
    tests_id = "%s-tests" % jid
    tests_objective = ("native tests for journey %s, required tests: %s"
                       % (jid, "; ".join(tests)))
    units.append(_unit(
        tests_id, jid, tests_objective, "JourneyTests.swift", "tests",
        "required_native_tests", tests,
        depends_on=[domain_id, view_id, network_id]))

    # instrumentation <- post_release_claims, performance_budgets
    claims_id = "%s-instrumentation" % jid
    units.append(_obligations_unit(
        claims_id, jid, "instrumentation", "Instrumentation.swift",
        "post_release_claims", journey_contract["post_release_claims"],
        depends_on=[domain_id]))
    perf_values = [
        "%s: %s" % (b.get("metric"), b.get("budget"))
        for b in journey_contract["performance_budgets"]
    ]
    perf_id = "%s-performance-budgets" % jid
    units.append(_obligations_unit(
        perf_id, jid, "instrumentation (performance budgets)",
        "PerformanceBudgets.swift", "performance_budgets", perf_values,
        depends_on=[domain_id]))

    return units


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to the mobile-journey-contract JSON record")
    ap.add_argument("--schema", default=MJC.DEFAULT_SCHEMA)
    ap.add_argument("--out", help="write the unit list as JSON to this path "
                    "instead of stdout")
    args = ap.parse_args(argv)
    try:
        record = CC.load_json(args.record, "journey contract record")
        schema = CC.load_json(args.schema, "journey contract schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc)
        return 2
    try:
        units = compile_plan(record, schema=schema)
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
