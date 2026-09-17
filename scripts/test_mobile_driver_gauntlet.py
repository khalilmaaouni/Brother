#!/usr/bin/env python3
"""EPIC M3.08 Driver Conformance Gauntlet: run one adversarial contract
against EVERY registered driver adapter, uniformly.

WHY THIS EXISTS, and what it is NOT. Each adapter already ships its own
suite (test_mobile_native_ios_adapter.py, test_mobile_appium_adapter.py,
test_mobile_visual_fallback_adapter.py, and test_mobile_maestro_adapter.py
on its own unmerged branch). Those prove each driver does its own job. None
of them can prove that a FIFTH adapter, added later, honours the same
guards, because each is hand-written against one module. This file closes
exactly that gap: it DISCOVERS adapters from the filesystem
(scripts/mobile_*_adapter.py) instead of naming them, so a driver added
tomorrow is put through the same gauntlet with no edit here. A driver that
skips one of the shared guards fails here even when its own suite is green.

It is NOT a second copy of the per-adapter suites, and it does not
re-prove what mobile_canonical_action.py's own tests already prove about
the vocabulary. It proves ONE thing: every driver routes through the shared
guards rather than around them.

NO DEVICE IS TOUCHED, AND THAT IS ENFORCED, NOT ASSUMED. Every adapter's
execute_action() takes its per-driver device/session handle as the second
positional argument (a simulator UDID for native-ios-simctl, a capabilities
dict for appium, a screenshot path for visual-fallback, an app id for
maestro -- the call-shape gap mobile_hybrid_action_router.py documents).
The gauntlet passes a _Tripwire in that position: an object that RECORDS
and raises on any real use. Every case below drives a record the driver
must refuse structurally, so a conformant driver never reaches the handle.
A driver that validated after opening a session trips the wire. The
recorded-touch list is asserted directly rather than relying on the
exception escaping, because mobile_appium_adapter.execute_action has a
deliberate last-resort `except Exception` that would otherwise absorb it.

The frozen journey of the M3.08 unit brief (launch, tap, type, scroll,
assert, capture, recover-from-missing-element, timeout, teardown) is
FROZEN_JOURNEY below, and test_frozen_journey_is_answered_uniformly runs
all nine verbs through every driver.
"""
import glob
import importlib
import inspect
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import contract_check as CC
# The shared fixtures this gauntlet reuses rather than reinventing: one
# valid record per canonical action, already maintained beside the
# vocabulary itself. Importing them means a new action added to the
# vocabulary (and to VALID_BY_ACTION) is immediately driven through every
# driver here, with no second fixture table to keep in step.
from test_mobile_canonical_action import VALID_BY_ACTION, record_for

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

#: Every driver adapter in this repo is scripts/mobile_<name>_adapter.py.
#: Discovery by glob, never a hand-typed module list, is the whole point of
#: this file: a fifth adapter is gauntleted automatically.
ADAPTER_GLOB = "mobile_*_adapter.py"

#: The two refusal statuses every driver must use with exactly these
#: spellings. A driver's SUCCESS-path statuses are deliberately NOT pinned
#: here: mobile_visual_fallback_adapter legitimately reports GROUNDED /
#: LOW_CONFIDENCE / NOT_ATTEMPTED from mobile-visual-grounding-v1, while
#: the device-driving adapters report PASS. Refusal, unlike success, has to
#: read the same from every driver, or a caller cannot tell a structural
#: refusal from a driver-specific outcome.
STATUS_INVALID_RECORD = "FAIL"
STATUS_NOT_SUPPORTED = "UNSUPPORTED"

#: Every spelling that means "the step happened". No driver may report one
#: of these for an input it was given no way to perform correctly. PASS is
#: the device-driving adapters' success; GROUNDED and LOW_CONFIDENCE are
#: mobile-visual-grounding-v1's (LOW_CONFIDENCE is included because it too
#: asserts a real target was produced from a real call).
SUCCESS_STATUSES = frozenset({"PASS", "GROUNDED", "LOW_CONFIDENCE"})

#: "I could not find out": the driver's own external tool was unreachable
#: (no Appium server, no maestro binary on this machine). Honest, and not
#: success, but it proves nothing about the driver's validation, so a case
#: that lands here is scored as non-success only, never as a pass of the
#: guard it was aimed at. Treating it as a pass is how a population of
#: NO-DATA composes into a green suite that measured nothing.
STATUS_NO_DATA = "NO-DATA"

#: No driver may sleep longer than this for one action. 120s is the
#: deadline mobile_workflow.invoke() already enforces on every subprocess
#: an adapter runs, so an in-process sleep that outlasts it is a hole in
#: the same guarantee. Asserted against a patched time.sleep, so a driver
#: that ignores its own cap fails instantly instead of hanging this suite.
MAX_SLEEP_SECONDS = 120.0

#: The M3.08 unit brief's frozen journey, verb by verb. Each entry is
#: (step label, canonical action).
FROZEN_JOURNEY = [
    ("launch", "OPEN_APP"),
    ("tap", "TAP_TARGET"),
    ("type", "TYPE_TEXT"),
    ("scroll", "SCROLL_TO"),
    ("assert", "ASSERT_VISIBLE"),
    ("capture", "CAPTURE"),
    ("recover-from-missing-element", "TAP_TARGET"),
    ("timeout", "WAIT_FOR"),
    ("teardown", "FINISH"),
]


class TripwireTouched(AssertionError):
    """Raised the moment a driver actually uses the device/session handle."""


class _Tripwire(object):
    """Stands in for a driver's device/session handle. Records every real
    use in .touches and raises, so a driver that reaches for the device
    before refusing a structurally invalid record is caught either way.

    Only operations that amount to USING the handle are trapped. Guards a
    driver runs ON the handle before deciding anything are not uses and
    stay silent: isinstance() and identity checks (no dunder at all), and
    truthiness, which is why __bool__ answers True rather than tripping.
    mobile_appium_adapter's `isinstance(capabilities, dict)` and
    mobile_maestro_adapter's `if not effective_app_id` are both exactly
    this, and an earlier draft of this class wrongly reported the second
    one as a device touch. __len__ and __contains__ are deliberately absent
    for the same reason: Python falls back to __len__ for truthiness."""

    def __init__(self, driver_id):
        object.__setattr__(self, "driver_id", driver_id)
        object.__setattr__(self, "touches", [])

    def _trip(self, how):
        self.touches.append(how)
        raise TripwireTouched(
            "%s used its device/session handle (%s) while handling a record it "
            "should have refused structurally first" % (self.driver_id, how))

    # __getattr__ only fires for names not found on the class or instance,
    # so _trip/driver_id/touches resolve normally and never recurse.
    def __getattr__(self, name):
        self._trip("attribute %r" % name)

    def __call__(self, *a, **k):
        self._trip("call")

    def __getitem__(self, key):
        self._trip("item %r" % (key,))

    def __iter__(self):
        self._trip("iteration")

    def __bool__(self):
        return True  # a handle WAS supplied; see the class docstring

    def __str__(self):
        self._trip("str")

    def __fspath__(self):
        self._trip("fspath")


def discover_adapters():
    """Every scripts/mobile_*_adapter.py, imported. Returns
    (loaded, failures) where loaded is [(module_name, module)] sorted by
    name and failures is [(module_name, exception)]. An adapter that will
    not import is a failure reported by its own test below, never a silent
    omission that would shrink the gauntlet to a vacuous pass."""
    loaded, failures = [], []
    for path in sorted(glob.glob(os.path.join(SCRIPTS_DIR, ADAPTER_GLOB))):
        name = os.path.basename(path)[:-len(".py")]
        try:
            loaded.append((name, importlib.import_module(name)))
        except Exception as exc:  # any import-time error, not only ImportError
            failures.append((name, exc))
    return loaded, failures


ADAPTERS, ADAPTER_IMPORT_FAILURES = discover_adapters()

#: The canonical vocabulary's own action enum, read from the schema rather
#: than re-typed, so "did this driver invent a verb" is answered against
#: the real vocabulary.
_ACTION_SCHEMA = CC.load_json(ACT.DEFAULT_SCHEMA, "canonical action schema")
_DRIVER_SCHEMA = CC.load_json(DC.DEFAULT_SCHEMA, "driver contract schema")
VOCABULARY = set(_ACTION_SCHEMA["properties"]["action"]["enum"])


def _blank_action_id(action):
    """A record valid in every respect except a whitespace-only action_id.
    The one corruption that applies uniformly to all 20 canonical actions,
    so every driver can be driven to a refusal without knowing which verbs
    it implements."""
    return record_for(action, action_id="   ")


class GauntletPopulationTests(unittest.TestCase):
    """Guards against the failure mode where this whole file passes because
    it found nothing to check."""

    def test_at_least_one_adapter_was_discovered(self):
        self.assertTrue(
            ADAPTERS,
            "no scripts/%s found: the gauntlet would pass vacuously" % ADAPTER_GLOB)

    def test_every_discovered_adapter_imports(self):
        self.assertEqual(
            [], ["%s: %r" % (n, e) for n, e in ADAPTER_IMPORT_FAILURES],
            "an adapter that will not import cannot be gauntleted")

    def test_driver_ids_are_unique_across_adapters(self):
        seen = {}
        for name, module in ADAPTERS:
            driver_id = module.describe()["driver_id"]
            self.assertTrue(
                isinstance(driver_id, str) and driver_id.strip(),
                "%s declares a blank driver_id" % name)
            self.assertNotIn(
                driver_id, seen,
                "driver_id %r is claimed by both %s and %s; the router selects by "
                "driver_id, so a duplicate makes selection ambiguous"
                % (driver_id, seen.get(driver_id), name))
            seen[driver_id] = name


class GauntletContractTests(unittest.TestCase):
    """Per-driver checks that read only describe() and the module surface."""

    def test_self_description_validates_against_the_driver_contract(self):
        for name, module in ADAPTERS:
            with self.subTest(adapter=name):
                problems = DC.check(module.describe(), _DRIVER_SCHEMA)
                self.assertEqual(
                    [], problems,
                    "%s's describe() violates mobile-driver-contract-v1" % name)

    def test_supported_actions_are_real_vocabulary_verbs(self):
        for name, module in ADAPTERS:
            with self.subTest(adapter=name):
                supported = set(module.describe()["supported_actions"])
                self.assertTrue(supported, "%s declares no supported actions" % name)
                self.assertEqual(
                    set(), supported - VOCABULARY,
                    "%s declares actions outside mobile-canonical-action-v1" % name)

    def test_execute_action_has_the_shared_call_shape(self):
        """mobile_hybrid_action_router.py can only select a driver_id
        because the second positional argument differs per driver; the
        FIRST FOUR positions are still a shared contract, and the router's
        caller depends on it."""
        for name, module in ADAPTERS:
            with self.subTest(adapter=name):
                params = list(inspect.signature(module.execute_action).parameters)
                self.assertGreaterEqual(len(params), 4, "%s: too few parameters" % name)
                self.assertEqual(
                    ["record", "evidence_root", "stages"],
                    [params[0], params[2], params[3]],
                    "%s: execute_action must be (record, <device handle>, "
                    "evidence_root, stages, ...)" % name)


class GauntletRefusalTests(unittest.TestCase):
    """The adversarial half: every driver must refuse the same bad inputs,
    with the same status spelling, without touching its device handle."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.evidence_root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _run(self, module, record):
        """execute_action with a tripwire handle. Returns (result, touches).
        A TripwireTouched that escapes is turned into a touch record so the
        assertion message is about the driver, not the exception."""
        tripwire = _Tripwire(module.DRIVER_ID)
        try:
            result = module.execute_action(record, tripwire, self.evidence_root, [])
        except TripwireTouched:
            return None, list(tripwire.touches)
        return result, list(tripwire.touches)

    def _assert_refused(self, name, module, record, expected_status, why):
        result, touches = self._run(module, record)
        self.assertEqual([], touches, "%s: %s, but it touched the device handle" % (name, why))
        self.assertIsInstance(result, dict, "%s: %s, got %r" % (name, why, result))
        self.assertEqual(
            expected_status, result.get("status"),
            "%s: %s; got status %r, detail %r"
            % (name, why, result.get("status"), result.get("detail")))
        self.assertTrue(
            str(result.get("detail") or "").strip(),
            "%s: refused with an empty detail, so a caller learns nothing" % name)
        return result

    def test_a_non_dict_record_is_refused_not_crashed(self):
        for name, module in ADAPTERS:
            with self.subTest(adapter=name):
                result, touches = self._run(module, ["not", "a", "record"])
                self.assertEqual([], touches, "%s touched the device handle" % name)
                self.assertIsInstance(
                    result, dict,
                    "%s must return a structured result for a non-dict record" % name)
                self.assertEqual(STATUS_INVALID_RECORD, result.get("status"))

    def test_a_structurally_invalid_record_is_refused_by_every_driver(self):
        """The shared gate: a blank action_id is invalid for all 20 verbs,
        so every driver must FAIL on it whether or not it implements the
        verb. A driver that dispatched before validating shows up here."""
        for name, module in ADAPTERS:
            for action in sorted(VALID_BY_ACTION):
                with self.subTest(adapter=name, action=action):
                    self._assert_refused(
                        name, module, _blank_action_id(action), STATUS_INVALID_RECORD,
                        "a whitespace-only action_id on %s must be refused" % action)

    def test_an_unknown_action_is_refused_by_every_driver(self):
        for name, module in ADAPTERS:
            with self.subTest(adapter=name):
                record = record_for("TAP_TARGET")
                record["action"] = "FLY_TO_THE_MOON"
                self._assert_refused(
                    name, module, record, STATUS_INVALID_RECORD,
                    "an action outside the vocabulary must be refused")

    def test_nan_and_infinity_are_refused_by_every_driver(self):
        """NaN and Infinity validate clean against any range check written
        as `value < lo or value > hi`, and json.dumps emits them as tokens
        no strict JSON reader accepts. The vocabulary rejects them
        centrally; this proves no driver routes around that."""
        poisons = [
            ("SET_LOCATION", {"latitude": float("nan"), "longitude": 139.7}),
            ("SET_LOCATION", {"latitude": 35.6, "longitude": float("inf")}),
            ("SET_LOCATION", {"latitude": float("-inf"), "longitude": 139.7}),
            ("WAIT_FOR", {"duration_ms": float("nan")}),
            ("WAIT_FOR", {"duration_ms": float("inf")}),
            ("LONG_PRESS_TARGET", {"duration_ms": float("nan")}),
        ]
        for name, module in ADAPTERS:
            for action, params in poisons:
                with self.subTest(adapter=name, action=action, params=repr(params)):
                    record = record_for(action, params=params)
                    # Sanity: the poison really is non-finite, so a green
                    # result below cannot come from an accidentally valid
                    # fixture.
                    self.assertFalse(
                        all(math.isfinite(v) for v in params.values()),
                        "fixture error: %r carries no non-finite value" % (params,))
                    self._assert_refused(
                        name, module, record, STATUS_INVALID_RECORD,
                        "a non-finite %s param must never reach a driver call" % action)

    def test_a_malformed_bundle_id_never_reaches_a_device_call(self):
        """bundle_id is a param the ADAPTERS invented (the vocabulary only
        requires params.permission/state for SET_PERMISSION), so nothing
        upstream validates it. Every driver that implements SET_PERMISSION
        must validate it itself before it becomes an argv element or a
        session capability; every driver that does not must say
        UNSUPPORTED. Neither may report success, and neither may touch the
        device handle."""
        malformed = ["not a bundle id", "   ", "com.example.app\n", ["com", "example"],
                     {"id": "com.example.app"}, 7, None, ""]
        for name, module in ADAPTERS:
            supported = set(module.describe()["supported_actions"])
            for bundle_id in malformed:
                with self.subTest(adapter=name, bundle_id=repr(bundle_id)):
                    record = record_for(
                        "SET_PERMISSION",
                        params={"permission": "camera", "state": "allow",
                                "bundle_id": bundle_id})
                    result, touches = self._run(module, record)
                    self.assertEqual(
                        [], touches,
                        "%s reached its device handle with bundle_id %r"
                        % (name, bundle_id))
                    self.assertIsInstance(result, dict, "%s returned %r" % (name, result))
                    self.assertNotIn(
                        result.get("status"), SUCCESS_STATUSES,
                        "%s reported success for bundle_id %r" % (name, bundle_id))
                    if "SET_PERMISSION" in supported:
                        # FAIL is the wanted answer. NO-DATA is accepted
                        # ONLY as an honest "my own tool is not installed
                        # here" -- it is not success, but it also does not
                        # prove the driver validates bundle_id, so it is
                        # deliberately not scored as a pass of this guard.
                        # Pinning FAIL outright would make this case's
                        # verdict depend on whether a third-party binary
                        # happens to be on the machine, which is a property
                        # of the machine, not of the driver.
                        self.assertIn(
                            result.get("status"), (STATUS_INVALID_RECORD, STATUS_NO_DATA),
                            "%s implements SET_PERMISSION and must refuse bundle_id %r; "
                            "got %r" % (name, bundle_id, result.get("status")))
                    else:
                        self.assertEqual(
                            STATUS_NOT_SUPPORTED, result.get("status"),
                            "%s does not implement SET_PERMISSION and must say so; got %r"
                            % (name, result.get("status")))

    def test_no_driver_sleeps_past_the_shared_deadline(self):
        """A WAIT_FOR the vocabulary accepts (its own ceiling is one hour)
        but that no driver should honour in full. time.sleep is patched, so
        a driver ignoring its cap fails here in milliseconds rather than
        blocking this suite for the duration it asked for."""
        record = record_for("WAIT_FOR", params={"duration_ms": 3000000}, timeout_ms=1000)
        self.assertEqual(
            [], ACT.check(record, _ACTION_SCHEMA),
            "fixture error: this record must be vocabulary-valid, or the test "
            "would prove nothing about the drivers' own caps")
        for name, module in ADAPTERS:
            with self.subTest(adapter=name):
                with mock.patch("time.sleep") as slept:
                    result, touches = self._run(module, record)
                requested = [c.args[0] for c in slept.call_args_list if c.args]
                self.assertEqual([], touches, "%s touched the device handle" % name)
                self.assertTrue(
                    all(s <= MAX_SLEEP_SECONDS for s in requested),
                    "%s asked to sleep %r seconds for one action, past the shared "
                    "%ss deadline" % (name, requested, MAX_SLEEP_SECONDS))
                self.assertIsInstance(result, dict, "%s returned %r" % (name, result))

    def test_an_unsupported_action_is_structural_never_a_silent_no_op(self):
        """For every verb a driver does NOT declare, a fully valid record
        must come back UNSUPPORTED with a real reason. A driver that
        returned success here would report a step as done that never ran."""
        for name, module in ADAPTERS:
            supported = set(module.describe()["supported_actions"])
            for action in sorted(VOCABULARY - supported):
                with self.subTest(adapter=name, action=action):
                    self._assert_refused(
                        name, module, record_for(action), STATUS_NOT_SUPPORTED,
                        "%s is not in supported_actions and must be refused "
                        "structurally" % action)

    def test_frozen_journey_is_answered_uniformly(self):
        """The M3.08 exit criterion, verb by verb. Every driver answers all
        nine journey steps the same way: a verb it does not implement comes
        back UNSUPPORTED with a reason; a verb it does implement refuses a
        corrupted record with FAIL. Either way the answer is structural,
        carries a reason, and costs no device call -- which is the
        equivalent observable semantics this epic had to prove."""
        for name, module in ADAPTERS:
            supported = set(module.describe()["supported_actions"])
            for step, action in FROZEN_JOURNEY:
                with self.subTest(adapter=name, step=step, action=action):
                    if action in supported:
                        self._assert_refused(
                            name, module, _blank_action_id(action),
                            STATUS_INVALID_RECORD,
                            "journey step %r (%s) is implemented, so a corrupted "
                            "record must be refused" % (step, action))
                    else:
                        self._assert_refused(
                            name, module, record_for(action), STATUS_NOT_SUPPORTED,
                            "journey step %r (%s) is not implemented, so it must "
                            "be declared unsupported" % (step, action))


if __name__ == "__main__":
    unittest.main()
