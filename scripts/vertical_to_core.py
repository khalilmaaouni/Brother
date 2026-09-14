#!/usr/bin/env python3
"""WBS-60.05 Vault convergence, Vertical-to-Core learning.

Roadmap text (BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14.md, WBS-60.05,
"Vertical-to-Core learning"):

    For every mobile/MDM lesson ask:

    > Does this lesson represent a reusable Core failure mode?

    Examples:

    Mobile:
    - stale build artifact -> Core artifact identity lesson.

    MDM:
    - high-consequence merge without rollback -> Core reversibility lesson.

    This is how vertical investment compounds.

This module is a small registry answering exactly that question: given a
vertical domain (mobile or mdm) and a named vertical failure mode, ask()
returns the known Core lesson it maps to, or NO-DATA when no mapping is
registered yet. It never fabricates a mapping: an unmapped lesson is an
open question the roadmap poses ("does this represent a reusable Core
failure mode?"), not evidence that the answer is no. register() lets a
caller record a new mapping once a human (or a reviewed process) has
actually answered that question for a new lesson; it is append-only and
refuses to silently overwrite an existing mapping with a different
answer, since re-answering an already-answered question without saying so
is exactly the kind of quiet drift this Vault convergence work exists to
prevent.

The two examples the roadmap names are seeded here so the registry is
never empty on a fresh checkout.
"""
import argparse
import json
import sys

VERTICAL_DOMAINS = ("mobile", "mdm")

#: (vertical_domain, vertical_failure_mode) -> core_failure_mode, seeded
#: with the roadmap's own two named examples, verbatim.
_SEED_MAPPINGS = {
    ("mobile", "stale_build_artifact"): "core_artifact_identity",
    ("mdm", "high_consequence_merge_without_rollback"): "core_reversibility",
}


def new_registry():
    """A fresh, independent copy of the seed mappings, so a caller can
    register() without mutating every other caller's view."""
    return dict(_SEED_MAPPINGS)


def ask(vertical_domain, vertical_failure_mode, registry=None):
    """(verdict, payload) answering the roadmap's own question for one
    lesson. verdict is 'MAPPED' with payload the known core_failure_mode
    string, or 'NO-DATA' with payload a reason string. `registry`
    defaults to the seed mappings; pass one from new_registry() (plus
    register() calls) to ask against an extended set."""
    if registry is None:
        registry = _SEED_MAPPINGS
    if vertical_domain not in VERTICAL_DOMAINS:
        return "NO-DATA", ("%r is not a vertical this registry covers (%s); "
                            "the roadmap's question applies to mobile/MDM "
                            "lessons" % (vertical_domain, VERTICAL_DOMAINS))
    key = (vertical_domain, vertical_failure_mode)
    if key in registry:
        return "MAPPED", registry[key]
    return "NO-DATA", (
        "no known Core mapping for domain=%r failure_mode=%r; the "
        "roadmap's question ('does this represent a reusable Core "
        "failure mode?') is unanswered here, not answered no -- register "
        "one once a human has actually decided"
        % (vertical_domain, vertical_failure_mode))


def register(registry, vertical_domain, vertical_failure_mode, core_failure_mode):
    """Add one new mapping to `registry` in place. Refuses a domain
    outside VERTICAL_DOMAINS, and refuses silently overwriting an
    existing mapping with a different core_failure_mode (re-registering
    the same answer is a no-op, not a problem). Returns a list of
    problems, empty on success."""
    problems = []
    if vertical_domain not in VERTICAL_DOMAINS:
        problems.append("vertical_domain: %r is not one of %s"
                         % (vertical_domain, VERTICAL_DOMAINS))
        return problems
    if not vertical_failure_mode or not core_failure_mode:
        problems.append("vertical_failure_mode and core_failure_mode must both be non-empty")
        return problems
    key = (vertical_domain, vertical_failure_mode)
    existing = registry.get(key)
    if existing is not None and existing != core_failure_mode:
        problems.append(
            "%s already maps to %r; registering a different mapping %r "
            "without removing the old one first is the quiet drift this "
            "registry exists to prevent" % (key, existing, core_failure_mode))
        return problems
    registry[key] = core_failure_mode
    return problems


def run_selftest():
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("%s: expected %r, got %r" % (name, want, got))

    expect("mobile stale build artifact maps to core artifact identity",
           ask("mobile", "stale_build_artifact"),
           ("MAPPED", "core_artifact_identity"))
    expect("mdm merge without rollback maps to core reversibility",
           ask("mdm", "high_consequence_merge_without_rollback"),
           ("MAPPED", "core_reversibility"))
    verdict, _reason = ask("mobile", "never_seen_before_failure_mode")
    expect("an unmapped lesson is NO-DATA, never fabricated",
           verdict, "NO-DATA")
    verdict, _reason = ask("brotherds", "anything")
    expect("a non-mobile/mdm domain is NO-DATA",
           verdict, "NO-DATA")

    reg = new_registry()
    expect("register a brand new mapping succeeds", register(
        reg, "mobile", "locale_not_reset_between_tests", "core_test_isolation"), [])
    expect("the new mapping is now answerable", ask(
        "mobile", "locale_not_reset_between_tests", registry=reg),
        ("MAPPED", "core_test_isolation"))
    expect("re-registering the identical mapping is a no-op, not a problem",
           register(reg, "mobile", "locale_not_reset_between_tests", "core_test_isolation"), [])
    expect("registering a conflicting mapping over an existing one is refused",
           bool(register(reg, "mobile", "locale_not_reset_between_tests", "core_something_else")),
           True)
    expect("a conflicting registration attempt does not silently overwrite",
           reg[("mobile", "locale_not_reset_between_tests")], "core_test_isolation")
    expect("registering outside mobile/mdm is refused",
           bool(register(reg, "brotherds", "x", "y")), True)

    for msg in failures:
        print("selftest:", msg)
    return not failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", choices=VERTICAL_DOMAINS)
    ap.add_argument("--failure-mode")
    ap.add_argument("--selftest", action="store_true",
                     help="run the built-in self check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        if run_selftest():
            print("vertical_to_core.py selftest: PASS")
            return 0
        print("vertical_to_core.py selftest: FAIL", file=sys.stderr)
        return 1

    if not args.domain or not args.failure_mode:
        ap.error("--domain and --failure-mode are both required unless --selftest")
    verdict, payload = ask(args.domain, args.failure_mode)
    print(json.dumps({"verdict": verdict, "payload": payload}, sort_keys=True))
    return 0 if verdict == "MAPPED" else 2


if __name__ == "__main__":
    sys.exit(main())
