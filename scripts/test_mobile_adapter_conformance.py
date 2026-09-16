#!/usr/bin/env python3
"""EPIC M1.06 -- adapter conformance suite for mobile_plan_compiler.py.

Read docs/plan/MOBILE-EPIC-M1-UNITS-CANONICAL.md before touching this
file's unit numbering: this is M1.06, "adapter conformance suite: one
frozen semantic fixture through all adapters."

One hand-written, frozen mobile-journey-contract-v1 fixture (journey_id
"generic-mobile-flow" -- a generic placeholder, never a real product or
app name, per this repo's own rule that the founder's app is never
named inside Brother) is run through compile_semantic_plan() and then
through compile_project_plan() for every adapter currently registered
in mobile_plan_compiler.ADAPTERS.

HONEST SCOPE OF THE WORD "CONFORMANCE" HERE (review of this file,
2026-09-16): mobile_plan_compiler.ADAPTERS holds exactly ONE registered
entry today (ios-swiftui), and that is a deliberate product decision,
not an oversight -- the compiler module's own docstring says a full
multi-platform registry (Android/RN/Flutter) is out of scope for this
epic. So "every registered adapter" is, today, one adapter, and a
suite that stopped there would be proving a property of ios-swiftui's
hardcoded stems rather than a property of the adapter CONTRACT.

Rather than inventing a second registry entry (which would advertise a
platform this product does not ship: ADAPTERS feeds main()'s
--adapter choices and _PLATFORM_TO_ADAPTER_ID), this suite drives a
second adapter through the documented, already-supported caller-
supplied-adapter-dict path that resolve_adapter() exists to serve
("an adapter dict supplied directly by the caller, for a stack not yet
in the registry"). SECOND_ADAPTER below is a test-only fixture with
deliberately different stems AND different extensions, so every
conformance property here is re-proven against an adapter whose shape
shares nothing with ios-swiftui's. It is NOT registered and must never
be added to ADAPTERS.

Muse adversarial review (OpenRouter, 2026-09-15) of an earlier draft of
this file found six real gaps, all fixed below:
  - the "no field disappears" check only compared unit id sets, so a
    project layer that kept every id but clobbered category/objective/
    contract_field/contract_values would still have passed -> now every
    carried-through field is compared, not just presence.
  - the platform-conditional check only scanned five hand-picked
    functions with a plain substring search (false positives on words
    like "scenarios"/"opportunity", false negatives on any new helper
    not added to the list by hand) -> now every module-defined function
    not on the small, explicit allow-list is discovered dynamically and
    checked two ways: an AST scan for any comparison against a string
    literal (the structural shape of a hardcoded platform/project
    branch, zero false positives) plus a word-boundary token scan for
    defense in depth.
  - the path-containment check only ever fed the registered, hardcoded-
    safe ios-swiftui stems through the fixture, so it could not have
    caught a real escape even if compile_project_plan had one -> a
    dedicated test below feeds a malicious adapter stem and a malicious
    journey_id through the real function. This gap was found and flagged
    here first (verified empirically, out of M1.06's own scope to
    patch), then closed directly in mobile_plan_compiler.py's
    compile_project_plan() (see _owns_path()): the test now proves
    refusal, not escape.
  - the expected artifact_kind set was derived from ADAPTERS itself, so
    if the registered adapter ever lost a kind the fixture-coverage
    test would shrink to match it and still pass -> now hardcoded
    independently from the module docstring's own category list.
  - the "unsupported dimension falls back, never disappears" test only
    exercised one of twelve kinds -> now loops over all twelve, and
    looks its unit up by an assertion of uniqueness rather than a
    dict build that would silently pick the wrong unit under a
    duplicate artifact_kind.

Four properties are checked, matching the M1.06 brief exactly:
  1. no semantic field silently disappears in the project-plan output
     (same unit count, same id set, AND the same category/objective/
     contract_field/contract_values/title carried through, for every
     registered adapter AND for the caller-supplied SECOND_ADAPTER, so
     the property is shown to belong to the contract rather than to
     ios-swiftui's particular stems).
  2. unsupported dimensions come back as NO-DATA rather than being
     omitted. No adapter registered today is partial (ios-swiftui
     covers all twelve known artifact_kinds -- checked directly, so a
     future adapter that ships an actual gap is caught here), so this
     suite also drives a synthetic partial adapter, once per kind, to
     pin the real, already-tested (test_mobile_plan_compiler.py's
     test_partial_custom_adapter_falls_back_per_missing_kind) fallback
     behavior: a missing artifact_kind resolves to DEFAULT_ADAPTER_ID's
     own mapping rather than dropping the unit. There is no literal
     "NO-DATA" marker anywhere in this module -- this suite pins the
     real behavior instead of asserting a string that does not exist.
  3. adapter output paths stay within the project's own root
     (mobile/<journey_id>/...) for every REGISTERED adapter against the
     realistic frozen fixture (their hardcoded stems are all
     traversal-free today, so this is a real, non-vacuous assertion:
     it would catch a future bad entry landing in ADAPTERS). A second,
     explicit test then proves -- rather than assumes -- what a
     malicious adapter stem or a malicious journey_id actually does:
     compile_project_plan() now refuses both with PlanCompilerError
     (mobile_plan_compiler.py's _owns_path(), added after this suite
     first caught the gap; journey_id's schema still has no pattern
     restriction of its own -- the containment check lives at the one
     place both the journey_id and the adapter-stem paths join, not in
     the schema).
  4. no project-name/platform conditional exists anywhere in the
     compiler's platform-neutral CORE -- every module-defined function
     except the small, explicit allow-list (resolve_adapter,
     compile_project_plan, main: exactly where the module's own
     docstring says a platform check belongs) is checked.
"""
import ast
import inspect
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract_check as CC  # noqa: E402
import mobile_journey_contract as MJC  # noqa: E402
import mobile_plan_compiler as MPC  # noqa: E402
# The ownership resolver (EPIC M1.05) is the layer that turns this
# module's adapter stems into real files on disk, so its own adversarial
# case (a case-insensitive collision) belongs in this suite too: see
# test_case_insensitive_collision_is_never_called_a_new_file.
import mobile_ownership_resolver as MOR  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

# Same minimal, generic outcome-contract-v1 fixture shape the sibling
# M1.04 test file uses (scripts/test_mobile_plan_compiler.py's
# VALID_OUTCOME) -- mobile-journey-contract-v1 requires
# outcome_contract_ref to resolve to a real file that itself validates.
VALID_OUTCOME = {
    "schema_version": "outcome-contract-v1", "project": {
        "project_id": "p", "name": "p",
        "provenance": {"project_id": "ask", "name": "ask"}},
    "language": "en", "question": "q", "success_checks": [
        {"id": "s", "command": "true", "expect": "exit-0"}],
    "must_answer": [], "affected_products": ["brother"], "ticket": None,
    "audit": {"required": False, "manifest": None}, "persona": "developer",
    "state": "contracted", "receipts": [], "questions": [], "history": [],
    "decision": None,
}

# The frozen fixture: one hand-written, fully-populated journey with a
# generic placeholder journey_id and non-trivial content in every array,
# so every one of the twelve artifact_kinds compile_semantic_plan emits
# is exercised with real-looking data, not an empty-gap stub.
FROZEN_JOURNEY = {
    "schema_version": "mobile-journey-contract-v1",
    "journey_id": "generic-mobile-flow",
    "human_outcome": "the user reaches the confirmation screen for their request",
    "entry_state": "form_started",
    "exit_state": "request_confirmed",
    "supported_device_classes": ["phone", "tablet"],
    "supported_os_range": "16-18",
    "locales": ["en", "fr"],
    "accessibility_obligations": [
        "a screen reader announces the confirmation before the next "
        "action is enabled",
    ],
    "network_state_obligations": [
        "a submit retried after a dropped connection never double-submits",
    ],
    "interruption_obligations": [
        "the form state survives the app being backgrounded mid-entry",
    ],
    "privacy_constraints": [
        "the entered contact details are never logged in plaintext",
    ],
    "performance_budgets": [
        {"metric": "time_to_confirmation_screen", "budget": "400ms"},
    ],
    "visual_reference_ids": ["generic-design-board-1"],
    "required_native_tests": ["GenericFlowTests.testHappyPath"],
    "human_acceptance_items": [
        "a human confirms the confirmation screen text reads correctly "
        "in both locales",
    ],
    "post_release_claims": ["claim-completion-rate"],
}

# The twelve artifact_kinds compile_semantic_plan() is documented (module
# docstring's CATEGORY -> CONTRACT FIELD MAPPING) to emit. Hardcoded here
# independently of MPC.ADAPTERS on purpose (Muse review): deriving the
# expected set from the adapter under test would let the adapter losing a
# kind shrink the expectation to match it and still pass.
ALL_ARTIFACT_KINDS = frozenset({
    "domain", "interruption", "view", "visual_reference", "human_acceptance",
    "network", "privacy", "accessibility", "localization", "tests",
    "instrumentation", "performance_budgets",
})

# A second, TEST-ONLY adapter, driven through the same frozen fixture via
# the caller-supplied-dict path resolve_adapter() already documents. Every
# stem and every extension differs from ios-swiftui's, so a conformance
# property that only held because of ios-swiftui's particular hardcoded
# values fails here instead of passing by coincidence. Deliberately NOT
# registered in MPC.ADAPTERS: registering it would advertise a platform
# this product does not ship (ADAPTERS feeds main()'s --adapter choices).
# Covers every kind in ALL_ARTIFACT_KINDS, asserted below rather than
# trusted, so this fixture cannot silently drift out of coverage.
SECOND_ADAPTER = {
    "domain": ("domain_model", "kt"),
    "interruption": ("interruption_rules", "kt"),
    "view": ("screen", "kt"),
    "visual_reference": ("visual_refs", "kt"),
    "human_acceptance": ("human_acceptance", "txt"),
    "network": ("network_rules", "kt"),
    "privacy": ("privacy_rules", "kt"),
    "accessibility": ("accessibility_rules", "kt"),
    "localization": ("strings", "xml"),
    "tests": ("journey_tests", "kt"),
    "instrumentation": ("instrumentation", "kt"),
    "performance_budgets": ("performance_budgets", "kt"),
}

# Fields compile_project_plan() actually carries through unchanged from
# each semantic unit onto its project unit (read from the module source:
# "category": su["category"], "contract_field": su["contract_field"], ...).
# A project layer that kept every unit id but silently clobbered one of
# these would previously have passed this suite; now it cannot.
_CARRIED_THROUGH_FIELDS = (
    "category", "contract_field", "contract_values", "objective", "title",
    "role", "risk_class",
)

# Only these module-defined functions may branch on a platform or project
# id -- exactly the project/adapter layer named in the module's own
# docstring (EPIC M1.04 split). Every other function defined in
# mobile_plan_compiler.py is checked below, discovered dynamically so a
# future helper nobody added to a hand-typed list is still covered.
_ALLOWED_PLATFORM_FUNCTIONS = frozenset({
    "resolve_adapter", "compile_project_plan", "main",
})

PLATFORM_TOKENS = (
    "ios", "android", "swift", "kotlin", "flutter", "dart", "react",
    "unity", "unreal", "xcode", "objective-c", "apple", "iphone", "ipad",
    "macos", "app-store", "play-store", "appstore", "playstore",
)
_TOKEN_PATTERNS = [
    (token, re.compile(r"\b%s\b" % re.escape(token), re.IGNORECASE))
    for token in PLATFORM_TOKENS
]


def _core_functions():
    """Every function mobile_plan_compiler.py itself defines, except the
    explicit platform/adapter-layer allow-list. Discovered dynamically
    (inspect.getmembers), not a hand-maintained tuple, so a new helper
    added later is automatically covered."""
    return [
        obj for name, obj in inspect.getmembers(MPC, inspect.isfunction)
        if getattr(obj, "__module__", None) == MPC.__name__
        and name not in _ALLOWED_PLATFORM_FUNCTIONS
    ]


def _code_without_docstring(fn):
    """Source text of fn with its leading docstring removed, so the
    word-boundary token scan below does not flag prose describing
    default/legacy behavior (e.g. compile_plan()'s own docstring
    honestly says it resolves "the default iOS/SwiftUI adapter" --
    documentation, not a conditional) as if it were a platform branch.
    The AST comparison check does not need this: a docstring is an
    Expr statement, never an ast.Compare node, so it was never at risk
    of a false positive there."""
    tree = ast.parse(inspect.getsource(fn))
    func_node = tree.body[0]
    if (func_node.body and isinstance(func_node.body[0], ast.Expr)
            and isinstance(func_node.body[0].value, ast.Constant)
            and isinstance(func_node.body[0].value.value, str)):
        func_node.body = func_node.body[1:]
    return ast.unparse(tree)


def _string_literal_comparisons(fn):
    """AST-walk fn's own source for any Compare node testing equality or
    membership against a string constant -- the structural shape of a
    hardcoded `if platform == "ios"` / `if x in ("ios", "android")`
    branch. Returns a list of the offending string values found."""
    tree = ast.parse(inspect.getsource(fn))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        candidates = [node.left] + list(node.comparators)
        for cand in candidates:
            if isinstance(cand, ast.Constant) and isinstance(cand.value, str):
                found.append(cand.value)
            elif isinstance(cand, (ast.Tuple, ast.List, ast.Set)):
                for elt in cand.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.append(elt.value)
    return found


class AdapterConformanceTests(unittest.TestCase):
    """EPIC M1.06: one frozen semantic-plan fixture run through every
    adapter mobile_plan_compiler.py currently registers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w", encoding="utf-8") as fh:
            json.dump(VALID_OUTCOME, fh)
        self.schema = CC.load_json(MJC.DEFAULT_SCHEMA, "schema")
        self.record = dict(FROZEN_JOURNEY, outcome_contract_ref=self.outcome_path)
        problems = MJC.check(self.record, self.schema)
        self.assertEqual(
            problems, [],
            "the frozen fixture itself must validate before it proves "
            "anything: %r" % (problems,))
        self.semantic_plan = MPC.compile_semantic_plan(
            self.record, schema=self.schema)

    # ------------------------------------------------------------------
    # the fixture actually exercises every category the compiler emits
    # ------------------------------------------------------------------
    def test_fixture_covers_every_artifact_kind(self):
        kinds = {u["artifact_kind"] for u in self.semantic_plan["units"]}
        self.assertEqual(
            kinds, ALL_ARTIFACT_KINDS,
            "the frozen fixture must exercise every category "
            "compile_semantic_plan can produce")

    # ------------------------------------------------------------------
    # every registered adapter: no field disappears, no path escapes
    # ------------------------------------------------------------------
    def _assert_adapter_conforms(self, label, adapter_map, adapter_arg):
        """The conformance body itself, run identically for a REGISTERED
        adapter (adapter_arg = its id string) and for the caller-supplied
        SECOND_ADAPTER (adapter_arg = the dict). Shared on purpose: a
        second adapter checked by a second, separately-written block would
        prove the assertions were duplicated, not that the contract holds
        for both."""
        journey_id = self.semantic_plan["journey_id"]
        semantic_units = self.semantic_plan["units"]
        semantic_by_id = {u["id"]: u for u in semantic_units}

        # An "unsupported dimension" is exactly a semantic
        # artifact_kind this adapter's own map has no entry for.
        semantic_kinds = {u["artifact_kind"] for u in semantic_units}
        missing = semantic_kinds - set(adapter_map)
        self.assertEqual(
            missing, set(),
            "%s does not cover artifact_kind(s) %r produced by "
            "the semantic plan" % (label, sorted(missing)))

        project_units = MPC.compile_project_plan(
            self.semantic_plan, adapter=adapter_arg)

        # No semantic field silently disappears: same count,
        # same id set, in the project-plan output.
        self.assertEqual(
            len(project_units), len(semantic_units),
            "%s dropped or duplicated units" % label)
        self.assertEqual(
            {u["id"] for u in project_units},
            {u["id"] for u in semantic_units},
            "%s changed the unit id set" % label)

        for unit in project_units:
            # Not just present -- every carried-through field
            # must survive unchanged (Muse review: id presence
            # alone would have missed a field silently clobbered
            # in transit).
            semantic_unit = semantic_by_id[unit["id"]]
            for field in _CARRIED_THROUGH_FIELDS:
                self.assertEqual(
                    unit[field], semantic_unit[field],
                    "%s: %s field diverged for unit %s"
                    % (label, field, unit["id"]))

            self.assertEqual(len(unit["owns"]), 1, unit["id"])
            self.assertEqual(unit["owns"], unit["writes"], unit["id"])
            path = unit["owns"][0]
            self.assertTrue(
                path.startswith("mobile/%s/" % journey_id),
                "%s: %r escaped the journey's own root"
                % (label, path))
            self.assertNotIn(
                "..", path,
                "%s: %r can escape via .." % (label, path))
            self.assertFalse(
                os.path.isabs(path),
                "%s: %r is an absolute path" % (label, path))
        return project_units

    def test_every_registered_adapter_drops_no_field_and_stays_in_project_roots(self):
        self.assertTrue(MPC.ADAPTERS, "no adapter registered to conform-test")
        for adapter_id, adapter_map in MPC.ADAPTERS.items():
            with self.subTest(adapter=adapter_id):
                self._assert_adapter_conforms(adapter_id, adapter_map, adapter_id)

    # ------------------------------------------------------------------
    # a SECOND, caller-supplied adapter: the same properties, proven on an
    # adapter whose stems and extensions share nothing with ios-swiftui's
    # ------------------------------------------------------------------
    def test_second_caller_supplied_adapter_conforms_to_the_same_contract(self):
        """The registry holds one adapter today by product decision, so
        every property above could in principle have held because of
        ios-swiftui's own hardcoded stems rather than because the adapter
        CONTRACT guarantees it. SECOND_ADAPTER re-runs the identical body
        through resolve_adapter()'s documented caller-supplied-dict path
        with entirely different stems and extensions, which is what makes
        this file's "conformance" claim mean something today without
        inventing a registry entry for a platform the product does not
        ship."""
        # The fixture must genuinely cover every kind, not be trusted to.
        self.assertEqual(
            set(SECOND_ADAPTER), set(ALL_ARTIFACT_KINDS),
            "SECOND_ADAPTER must cover exactly the twelve artifact_kinds, "
            "or it silently stops conformance-testing the ones it drops")
        # It must also actually differ, or it proves nothing beyond the
        # registered adapter already proved.
        registered = MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID]
        shared = {k for k in ALL_ARTIFACT_KINDS
                  if SECOND_ADAPTER[k] == registered.get(k)}
        self.assertEqual(
            shared, set(),
            "SECOND_ADAPTER shares (stem, ext) with the registered adapter "
            "for %r, so those kinds re-prove nothing" % sorted(shared))

        project_units = self._assert_adapter_conforms(
            "second-caller-supplied", SECOND_ADAPTER, SECOND_ADAPTER)

        # And the output really is this adapter's, not a silent fallback
        # to the default: every path must carry SECOND_ADAPTER's own stem.
        by_id = {u["id"]: u for u in self.semantic_plan["units"]}
        for unit in project_units:
            stem, ext = SECOND_ADAPTER[by_id[unit["id"]]["artifact_kind"]]
            self.assertEqual(
                unit["owns"], ["mobile/%s/%s.%s" % (
                    self.semantic_plan["journey_id"], stem, ext)],
                "unit %s did not use the caller-supplied adapter's own "
                "mapping" % unit["id"])

    # ------------------------------------------------------------------
    # a malicious adapter stem or journey_id: refused, not escaped
    # ------------------------------------------------------------------
    def test_malicious_adapter_stem_or_journey_id_are_refused(self):
        """Was test_malicious_adapter_stem_or_journey_id_escape_the_
        project_root_today: that test pinned a real, unsanitized escape
        as a known gap (compile_project_plan() built owns/writes as
        "mobile/%s/%s.%s" % (journey_id, stem, ext) with no
        sanitization of any part). PR #717 closed the one call site where
        both already-joined values met (_owns_path()); the follow-up
        adversarial review (Muse, 2026-09-15) found resolve_adapter()'s
        own stem/ext and the journey_id read in compile_semantic_plan()
        were still unvalidated at the point each value ENTERS the module,
        so mobile_plan_compiler.py now refuses both there, before a plan
        is even built. This test proves that directly, against the real
        functions, for a traversal payload in the adapter stem AND in
        journey_id."""
        malicious_adapter = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        malicious_adapter["domain"] = ("../../../../etc/evil", "swift")
        with self.assertRaises(
                MPC.PlanCompilerError,
                msg="a malicious adapter stem must be refused, not escape "
                    "mobile/<journey_id>/"):
            MPC.compile_project_plan(self.semantic_plan, adapter=malicious_adapter)

        malicious_record = dict(self.record, journey_id="../../evil-journey")
        with self.assertRaises(
                MPC.PlanCompilerError,
                msg="a malicious journey_id must be refused at "
                    "compile_semantic_plan(), before any plan is built"):
            MPC.compile_semantic_plan(malicious_record, schema=self.schema)

    # ------------------------------------------------------------------
    # M1.04's own adversarial case: path injection through the adapter
    # dict, refused where the value ENTERS the module
    # ------------------------------------------------------------------
    def test_adapter_dict_path_injection_is_refused_at_resolve_adapter(self):
        """Sourced from the real defect and fix on M1.04's own review
        (PR #705, "compile_project_plan() now refuses any resolved
        stem/ext containing a path separator, '..', or a NUL byte"), then
        moved to the value's entry point by this branch's own commit so
        a sibling consumer inherits it.

        The existing malicious-stem test above goes through
        compile_project_plan(), which proves the join is safe. This one
        drives resolve_adapter() directly, which is where a caller-
        supplied adapter dict actually enters the module, and covers the
        EXT half of the pair as well as the stem half. Without the ext
        case, an adapter of the shape ("View", "../../evil") would have
        had no test anywhere in this suite.

        The conformance angle: resolve_adapter() is documented to accept
        a caller-supplied dict for a stack not yet in the registry, so
        that dict is untrusted input on the same footing as a journey
        record, and every adapter this suite conforms is reached through
        it."""
        registered = MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID]
        payloads = (
            ("stem", "traversal", ("../../../../etc/evil", "swift")),
            ("stem", "bare dotdot", ("..", "swift")),
            ("stem", "NUL byte", ("Ev\x00il", "swift")),
            ("stem", "shell metacharacter", ("View;rm -rf /", "swift")),
            ("ext", "traversal", ("View", "../../../../etc/evil")),
            ("ext", "separator", ("View", "swift/../../evil")),
            ("ext", "shell metacharacter", ("View", "swift`id`")),
        )
        for half, label, pair in payloads:
            for kind in ("domain", "localization"):
                with self.subTest(half=half, payload=label, kind=kind):
                    malicious = dict(registered)
                    malicious[kind] = pair
                    with self.assertRaises(
                            MPC.PlanCompilerError,
                            msg="a malicious adapter %s (%s) for %r must be "
                                "refused where the adapter dict enters the "
                                "module, not sanitized and not passed on"
                                % (half, label, kind)):
                        MPC.resolve_adapter(adapter=malicious)

    def test_a_refused_adapter_dict_never_reaches_a_compiled_plan(self):
        """The refusal above is only worth anything if nothing downstream
        quietly accepts the same dict. Drives the full public path a real
        caller uses (compile_project_plan) with the same payload and
        asserts no plan is produced at all, rather than a plan built from
        a repaired value: a silently-sanitized stem would still be a
        wrong answer written to a path the caller did not ask for."""
        malicious = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        malicious["localization"] = ("Localizable", "../../../../etc/evil")
        with self.assertRaises(MPC.PlanCompilerError):
            MPC.compile_project_plan(self.semantic_plan, adapter=malicious)

    # ------------------------------------------------------------------
    # an unsupported dimension: never silently omitted, falls back visibly
    # ------------------------------------------------------------------
    def test_unsupported_dimension_never_silently_disappears(self):
        """No adapter registered today is partial, so this drives a
        synthetic incomplete adapter -- once per artifact_kind, not just
        one (Muse review) -- to prove the real behavior directly: the
        missing kind's unit is never dropped from the output, it
        resolves via the documented fallback to DEFAULT_ADAPTER_ID's own
        mapping for that one kind (already covered from the adapter-
        resolution side by test_mobile_plan_compiler.py's
        test_partial_custom_adapter_falls_back_per_missing_kind). This
        test pins the same real behavior from the conformance-suite
        side, against the frozen fixture, so a future change that
        starts dropping the unit instead is caught here."""
        semantic_units = self.semantic_plan["units"]
        default_adapter = MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID]
        for missing_kind in ALL_ARTIFACT_KINDS:
            with self.subTest(missing_kind=missing_kind):
                partial = dict(default_adapter)
                del partial[missing_kind]

                project_units = MPC.compile_project_plan(
                    self.semantic_plan, adapter=partial)

                self.assertEqual(len(project_units), len(semantic_units))
                self.assertEqual(
                    {u["id"] for u in project_units},
                    {u["id"] for u in semantic_units})

                # Look the unit up by an assertion of uniqueness, not a
                # dict build that would silently pick the wrong unit if
                # two semantic units ever shared an artifact_kind.
                matches = [u for u in semantic_units
                          if u["artifact_kind"] == missing_kind]
                self.assertEqual(
                    len(matches), 1,
                    "expected exactly one semantic unit for %r, found %d"
                    % (missing_kind, len(matches)))
                gap_unit_id = matches[0]["id"]
                gap_unit = next(u for u in project_units if u["id"] == gap_unit_id)

                default_stem, default_ext = default_adapter[missing_kind]
                expected = "mobile/%s/%s.%s" % (
                    self.semantic_plan["journey_id"], default_stem, default_ext)
                self.assertEqual(
                    gap_unit["owns"], [expected],
                    "an unsupported dimension (%r) must still resolve to a "
                    "real, visible file (today: the default adapter's own "
                    "mapping for that kind), never vanish from the output"
                    % missing_kind)

    # ------------------------------------------------------------------
    # no project-name/platform conditional in the semantic CORE
    # ------------------------------------------------------------------
    def test_core_functions_carry_no_platform_conditional(self):
        """Every module-defined function except the explicit
        resolve_adapter()/compile_project_plan()/main() allow-list
        (module docstring: exactly where a platform check belongs, per
        the EPIC M1.04 split) is discovered dynamically and checked two
        ways: an AST scan for a comparison against a string literal (the
        structural shape of a hardcoded platform/project branch -- zero
        false positives, and covers any future helper automatically),
        plus a word-boundary token scan for defense in depth."""
        core_functions = _core_functions()
        self.assertGreaterEqual(
            len(core_functions), 5,
            "expected at least the five known semantic-layer functions; "
            "the allow-list may have grown to swallow one of them")
        for fn in core_functions:
            with self.subTest(function=fn.__name__):
                literals = _string_literal_comparisons(fn)
                self.assertEqual(
                    literals, [],
                    "%s() compares against string literal(s) %r; only "
                    "resolve_adapter()/compile_project_plan()/main() may "
                    "branch on a platform or project id"
                    % (fn.__name__, literals))

                source = _code_without_docstring(fn)
                for token, pattern in _TOKEN_PATTERNS:
                    self.assertIsNone(
                        pattern.search(source),
                        "%s() carries the platform-specific token %r; only "
                        "resolve_adapter()/compile_project_plan()/main() "
                        "may branch on a platform"
                        % (fn.__name__, token))

    # ------------------------------------------------------------------
    # M1.04's own adversarial case: an unmatched platform profile is a
    # reported fallback, never a silent wrong answer
    # ------------------------------------------------------------------
    def test_unmatched_platform_profile_is_reported_not_silently_defaulted(self):
        """Sourced from the real defect and fix on M1.04's own review
        (PR #705, Major 1: "resolve_adapter() now returns (adapter,
        matched) instead of just the adapter dict, so a real
        project-profile whose platform matches no known adapter is no
        longer a silent wrong answer"). Its own suite proves the flag on
        the default adapter; this file did not cover the case at all
        until now.

        THE CONFORMANCE ANGLE, which is what this file adds over #705's
        own test: "matched" has to be a property of the RESOLUTION, not
        of ios-swiftui's particular platform mapping. A profile naming a
        platform a registered adapter really serves must report
        matched=True for EVERY registered adapter, and a profile naming
        one nothing serves must report matched=False while still handing
        back a usable fallback adapter. A future second registry entry
        that reported matched=True for a platform it does not serve is
        exactly the silent wrong answer #705 closed, reintroduced one
        adapter over, and this test fails on it.

        The fallback adapter itself is asserted usable (a full plan
        still compiles from it), because "report the gap" must not have
        been implemented as "refuse the plan": an undetected platform is
        a detection gap, never a reason to block work."""
        # Every registered adapter: a profile that really names its own
        # platform is a real match, not a fallback that happens to agree.
        self.assertTrue(MPC.ADAPTERS, "no adapter registered to conform-test")
        for platform, adapter_id in MPC._PLATFORM_TO_ADAPTER_ID.items():
            with self.subTest(platform=platform, adapter=adapter_id):
                adapter, matched = MPC.resolve_adapter(
                    project_profile={"platforms": [platform]})
                self.assertEqual(adapter, MPC.ADAPTERS[adapter_id])
                self.assertTrue(
                    matched,
                    "platform %r resolves to registered adapter %r, so this "
                    "is a real match and must not be reported as a fallback"
                    % (platform, adapter_id))

        # A declared platform no registered adapter serves: the caller
        # must be able to SEE that the answer is a fallback. "android" is
        # #705's own named case; the others are the malformed-profile
        # shapes from the same review, which are the same kind of gap.
        default = MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID]
        unmatched_profiles = (
            {"platforms": ["android"]},
            {"platforms": ["flutter", "react-native"]},
            {"platforms": []},
            {"platforms": None},
            {"platforms": [{}]},
            {},
        )
        for profile in unmatched_profiles:
            with self.subTest(profile=profile):
                adapter, matched = MPC.resolve_adapter(project_profile=profile)
                self.assertEqual(
                    adapter, default,
                    "an unmatched profile must still hand back the default "
                    "adapter: a detection gap never blocks a plan")
                self.assertFalse(
                    matched,
                    "profile %r matched no registered adapter, so returning "
                    "the default silently is the Major-1 wrong answer #705 "
                    "closed -- the caller must be able to report it"
                    % (profile,))
                # The reported fallback has to be usable, or "report the
                # gap" was quietly implemented as "refuse the plan".
                units = MPC.compile_project_plan(
                    self.semantic_plan, adapter=adapter)
                self.assertEqual(len(units), len(self.semantic_plan["units"]))

        # No profile at all is NOT a mismatch: there was nothing to
        # mismatch against. Without this, a caller wired to the flag
        # would report a detection gap on every profile-less run.
        adapter, matched = MPC.resolve_adapter()
        self.assertEqual(adapter, default)
        self.assertTrue(matched, "no profile given: nothing mismatched")

    # ------------------------------------------------------------------
    # M1.05's own adversarial case: a case-insensitive filesystem
    # collision is never reported as a brand new file
    # ------------------------------------------------------------------
    def _assert_no_case_insensitive_collision_is_called_new(self, label, pair,
                                                            adapter_arg):
        """One adapter's worth of the collision check below, run
        identically for the registered adapter and for SECOND_ADAPTER so
        the property is proven of the resolver rather than of one
        adapter's stems."""
        stem, ext = pair
        # A real, hand-written file whose name differs from the stem the
        # adapter would synthesize ONLY by case. Lowercase where the stem
        # is not already lowercase (M1.05's own "view.swift" scenario),
        # uppercase otherwise, so the collision exists for both an
        # upper-cased and a lower-cased adapter stem.
        collided = stem.lower() if stem != stem.lower() else stem.upper()
        self.assertNotEqual(
            collided, stem,
            "%s: could not build a case-variant of stem %r, so this test "
            "would prove nothing" % (label, stem))

        project_dir = tempfile.mkdtemp(dir=self.tmp)
        source_root = os.path.join(project_dir, "Sources")
        os.makedirs(source_root)

        # This collision can only happen on a case-insensitive filesystem,
        # so probe the REAL one this run is using rather than assuming
        # macOS's default. A skip is not a pass: the skip message says so.
        probe = os.path.join(source_root, "case-probe.txt")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        if not os.path.exists(os.path.join(source_root, "CASE-PROBE.txt")):
            self.skipTest("this filesystem is case-sensitive, so the "
                          "collision this test proves cannot occur on it -- "
                          "NOT a pass, the property is unproven here")
        os.remove(probe)

        human_file = os.path.join(source_root, "%s.%s" % (collided, ext))
        with open(human_file, "w", encoding="utf-8") as fh:
            fh.write("// real human-owned file\n")

        unit = next(u for u in self.semantic_plan["units"]
                    if u["artifact_kind"] == "view")
        result = MOR.resolve_unit(unit, project_dir,
                                  project_profile={"source_roots": ["Sources"]},
                                  adapter=adapter_arg)
        self.assertNotEqual(
            result["status"], "new_file",
            "%s: %r already exists on this case-insensitive filesystem, so "
            "calling %r a new file invites open(path, 'w') to destroy it: %r"
            % (label, human_file, "%s.%s" % (stem, ext), result))
        self.assertIsNot(
            result["exists"], False,
            "%s: exists=False for a path the filesystem resolves to a real "
            "file is the exact data-loss claim M1.05's CRITICAL C1 closed: "
            "%r" % (label, result))
        self.assertEqual(result["status"], "ambiguous", result)
        self.assertEqual(result["match_basis"], "case_insensitive_collision",
                         result)

    def test_case_insensitive_collision_is_never_called_a_new_file(self):
        """Sourced from the real defect and fix on M1.05's own review
        (PR #711, CRITICAL C1: the new_file branch asserted exists=False
        for a synthesized path without ever checking the real
        filesystem, so a downstream caller trusting it could destroy a
        real, human-owned file with open(path, "w")). This file gave that
        case zero coverage before now, which the M1.06 review called out:
        a conformance suite for the layer M1.05 sits under should carry
        this exact adversarial case.

        THE CONFORMANCE ANGLE: M1.05's own test drives the default
        adapter only, so its assertions could hold because of the
        particular pair ("View", "swift") -- a stem that happens to be
        capitalised, colliding with a lowercase file. Running the same
        body through SECOND_ADAPTER's ("screen", "kt") reverses the
        collision (a lowercase stem against an uppercase file) and
        changes the extension, so what is proven here is a property of
        the resolver's filesystem check, not of one adapter's casing
        convention."""
        registered_id = MPC.DEFAULT_ADAPTER_ID
        self._assert_no_case_insensitive_collision_is_called_new(
            registered_id, MPC.ADAPTERS[registered_id]["view"], registered_id)
        self._assert_no_case_insensitive_collision_is_called_new(
            "second-caller-supplied", SECOND_ADAPTER["view"], SECOND_ADAPTER)


if __name__ == "__main__":
    unittest.main()
