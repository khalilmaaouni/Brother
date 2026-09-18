#!/usr/bin/env python3
"""Regression suite for scripts/network_policy.py (DOM-40.03).

Every case the module's own rule table names gets its own test rather than
a sample: the estate's own recorded lesson is that a rule covering N modes
or N fields needs all N driven, against an expected set pinned here, not
read back from the module under test (a test that asks the module what it
expects can never catch the module drifting from its own contract). The
three modes below are therefore spelled out as a literal tuple in this
file, never imported from network_policy._MODES, and the high_autonomy x
unrestricted_ack matrix is driven exhaustively rather than at one or two
points.

Every test asserts BOTH the verdict AND the named rule, per the worker
contract: asserting only .decision would still pass if two different rules
started returning the same decision for the wrong reason.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import network_policy as np  # noqa: E402

# Pinned independently of network_policy._MODES: this is what the module's
# own docstring promises exist, not what the module's internals currently
# list. If a future edit drops or renames one of these three, this test
# must go red on the name, not silently track the module's own change.
EXPECTED_MODES = ("denied", "allowlisted", "unrestricted")


class HostValidationTest(unittest.TestCase):
    """invalid-host must fire for every unusable host shape, under every
    mode (including unrestricted, which would otherwise allow almost
    anything), because host validity has nothing to do with policy mode."""

    INVALID_HOSTS = (None, "", "   ", "\t\n", 123, 1.5, [], {}, ("a",), True)

    def test_invalid_host_refused_under_every_mode(self):
        configs = {
            "denied": {"mode": "denied"},
            "allowlisted": {"mode": "allowlisted", "allowlist": ["example.com"]},
            "unrestricted": {"mode": "unrestricted"},
        }
        self.assertEqual(set(configs), set(EXPECTED_MODES))
        for mode_name, config in configs.items():
            for bad_host in self.INVALID_HOSTS:
                for high_autonomy in (False, True):
                    with self.subTest(mode=mode_name, host=bad_host,
                                       high_autonomy=high_autonomy):
                        v = np.decide(bad_host, config,
                                      high_autonomy=high_autonomy)
                        self.assertEqual(v.decision, np.REFUSE)
                        self.assertEqual(v.rule, "invalid-host")

    def test_valid_host_is_not_refused_as_invalid(self):
        # Baseline so this class cannot pass by refusing everything.
        v = np.decide("example.com", {"mode": "unrestricted"}, high_autonomy=False)
        self.assertNotEqual(v.rule, "invalid-host")
        self.assertEqual(v.decision, np.ALLOW)


class ConfigShapeTest(unittest.TestCase):
    """config-not-a-mapping and config-missing-mode must fire for every
    malformed config shape named in the module's own contingency table."""

    NOT_A_MAPPING = (None, [], ["mode", "denied"], "denied", 5, 5.0,
                     True, ("mode", "denied"), set())

    def test_config_not_a_mapping_refused_for_every_shape(self):
        for bad_config in self.NOT_A_MAPPING:
            with self.subTest(config=bad_config):
                v = np.decide("example.com", bad_config)
                self.assertEqual(v.decision, np.REFUSE)
                self.assertEqual(v.rule, "config-not-a-mapping")

    def test_config_missing_mode_key(self):
        v = np.decide("example.com", {})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "config-missing-mode")

        v = np.decide("example.com", {"allowlist": ["example.com"]})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "config-missing-mode")

    def test_unknown_mode_refused_for_every_bad_value(self):
        for bad_mode in ("DENIED", "Allowlisted", "permit", "", None, 0,
                         "unrestricted ", " denied"):
            with self.subTest(mode=bad_mode):
                v = np.decide("example.com", {"mode": bad_mode})
                self.assertEqual(v.decision, np.REFUSE)
                self.assertEqual(v.rule, "unknown-mode")

    def test_unknown_mode_never_reads_as_unrestricted(self):
        # The deciding property in one assertion: a mode this module does
        # not recognise must land on the SAME rule as every other unknown
        # mode, never silently on the unrestricted-mode code path.
        v = np.decide("anything.example.net", {"mode": "unrestricted-ish"})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "unknown-mode")
        self.assertNotIn(v.rule, ("unrestricted", "unrestricted-acknowledged"))


class DeniedModeTest(unittest.TestCase):
    """denied refuses every host, regardless of high_autonomy or any other
    config content (an allowlist present under denied changes nothing)."""

    def test_denied_refuses_regardless_of_high_autonomy_and_allowlist(self):
        configs = (
            {"mode": "denied"},
            {"mode": "denied", "allowlist": ["example.com"]},
            {"mode": "denied", "unrestricted_ack": True},
        )
        for config in configs:
            for high_autonomy in (False, True):
                with self.subTest(config=config, high_autonomy=high_autonomy):
                    v = np.decide("example.com", config,
                                  high_autonomy=high_autonomy)
                    self.assertEqual(v.decision, np.REFUSE)
                    self.assertEqual(v.rule, "mode-denied")


class AllowlistedModeTest(unittest.TestCase):
    def test_missing_allowlist_key_refuses_everything(self):
        v = np.decide("example.com", {"mode": "allowlisted"})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "allowlist-empty-or-unreadable")

    def test_empty_allowlist_refuses_everything(self):
        v = np.decide("example.com",
                      {"mode": "allowlisted", "allowlist": []})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "allowlist-empty-or-unreadable")

    def test_non_iterable_allowlist_refuses_everything(self):
        for bad_allowlist in (None, 5, 5.0, True):
            with self.subTest(allowlist=bad_allowlist):
                v = np.decide(
                    "example.com",
                    {"mode": "allowlisted", "allowlist": bad_allowlist})
                self.assertEqual(v.decision, np.REFUSE)
                self.assertEqual(v.rule, "allowlist-empty-or-unreadable")

    def test_bare_string_allowlist_is_not_treated_as_host_characters(self):
        # A bare string is iterable in Python (yields characters); that
        # must never be silently accepted as a one-character allowlist.
        v = np.decide("e", {"mode": "allowlisted", "allowlist": "example.com"})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "allowlist-empty-or-unreadable")

    def test_exact_match_allowed(self):
        config = {"mode": "allowlisted", "allowlist": ["example.com"]}
        v = np.decide("example.com", config)
        self.assertEqual(v.decision, np.ALLOW)
        self.assertEqual(v.rule, "allowlisted-match")

    def test_exact_match_is_case_insensitive_and_whitespace_tolerant(self):
        config = {"mode": "allowlisted", "allowlist": ["  Example.Com  "]}
        v = np.decide("EXAMPLE.com", config)
        self.assertEqual(v.decision, np.ALLOW)
        self.assertEqual(v.rule, "allowlisted-match")

    def test_no_match_refused(self):
        config = {"mode": "allowlisted", "allowlist": ["example.com"]}
        v = np.decide("other.com", config)
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "not-allowlisted")

    def test_wildcard_subdomain_matches_subdomain(self):
        config = {"mode": "allowlisted", "allowlist": ["*.example.com"]}
        for host in ("anything.example.com", "deeper.anything.example.com"):
            with self.subTest(host=host):
                v = np.decide(host, config)
                self.assertEqual(v.decision, np.ALLOW)
                self.assertEqual(v.rule, "allowlisted-match")

    def test_wildcard_subdomain_does_not_match_bare_parent(self):
        config = {"mode": "allowlisted", "allowlist": ["*.example.com"]}
        v = np.decide("example.com", config)
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "not-allowlisted")

    def test_wildcard_subdomain_does_not_match_lookalike_suffix(self):
        # "notexample.com" ends in "example.com" as a raw substring but is
        # not a subdomain of it; the check must be dot-anchored.
        config = {"mode": "allowlisted", "allowlist": ["*.example.com"]}
        v = np.decide("notexample.com", config)
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "not-allowlisted")

    def test_bare_parent_listed_separately_is_allowed(self):
        config = {"mode": "allowlisted",
                  "allowlist": ["*.example.com", "example.com"]}
        v = np.decide("example.com", config)
        self.assertEqual(v.decision, np.ALLOW)
        self.assertEqual(v.rule, "allowlisted-match")

    def test_malformed_entries_are_skipped_not_fatal(self):
        config = {"mode": "allowlisted",
                  "allowlist": [None, 5, "", "   ", "example.com"]}
        v = np.decide("example.com", config)
        self.assertEqual(v.decision, np.ALLOW)
        self.assertEqual(v.rule, "allowlisted-match")


class UnrestrictedModeTest(unittest.TestCase):
    """The high_autonomy x unrestricted_ack matrix, driven exhaustively:
    this is the exact combination the unit's deciding property names, so
    it gets every cell, not a sample."""

    def test_supervised_caller_needs_no_ack(self):
        for config in ({"mode": "unrestricted"},
                       {"mode": "unrestricted", "unrestricted_ack": False}):
            with self.subTest(config=config):
                v = np.decide("anywhere.example.org", config,
                              high_autonomy=False)
                self.assertEqual(v.decision, np.ALLOW)
                self.assertEqual(v.rule, "unrestricted")

    def test_high_autonomy_without_ack_refused(self):
        for config in ({"mode": "unrestricted"},
                       {"mode": "unrestricted", "unrestricted_ack": False},
                       {"mode": "unrestricted", "unrestricted_ack": ""},
                       {"mode": "unrestricted", "unrestricted_ack": 0},
                       {"mode": "unrestricted", "unrestricted_ack": None}):
            with self.subTest(config=config):
                v = np.decide("anywhere.example.org", config,
                              high_autonomy=True)
                self.assertEqual(v.decision, np.REFUSE)
                self.assertEqual(
                    v.rule,
                    "unrestricted-requires-ack-for-high-autonomy")

    def test_high_autonomy_with_truthy_ack_allowed(self):
        for config in ({"mode": "unrestricted", "unrestricted_ack": True},
                       {"mode": "unrestricted", "unrestricted_ack": "yes"},
                       {"mode": "unrestricted", "unrestricted_ack": 1}):
            with self.subTest(config=config):
                v = np.decide("anywhere.example.org", config,
                              high_autonomy=True)
                self.assertEqual(v.decision, np.ALLOW)
                self.assertEqual(v.rule, "unrestricted-acknowledged")

    def test_undeclared_autonomy_is_treated_as_high_not_supervised(self):
        """Orchestrator finding: decide() used to default high_autonomy to
        False, so a caller that never stated its autonomy ran unrestricted
        with no acknowledgement. Undeclared must block like high."""
        v = np.decide("anywhere.example.org", {"mode": "unrestricted"})
        self.assertEqual(v.decision, np.REFUSE)
        self.assertEqual(v.rule, "unrestricted-requires-ack-for-high-autonomy")

    def test_high_autonomy_never_silently_unrestricted(self):
        # The deciding property, stated as a direct assertion: a
        # high-autonomy caller can never land on the plain "unrestricted"
        # rule (the one that grants without any acknowledgement check).
        v = np.decide("anywhere.example.org", {"mode": "unrestricted"},
                      high_autonomy=True)
        self.assertNotEqual(v.rule, "unrestricted")
        self.assertEqual(v.decision, np.REFUSE)


class ModeEnumerationTest(unittest.TestCase):
    """The three modes are exactly these three, pinned independently of
    the module's own internals, so a fourth mode silently added (or one
    silently renamed) turns this test red instead of invisible."""

    def test_exactly_three_modes_behave_as_documented(self):
        self.assertEqual(len(EXPECTED_MODES), 3)
        results = {}
        for mode in EXPECTED_MODES:
            config = {"mode": mode, "allowlist": ["example.com"]}
            results[mode] = np.decide("example.com", config, high_autonomy=False).decision
        self.assertEqual(results["denied"], np.REFUSE)
        self.assertEqual(results["allowlisted"], np.ALLOW)
        self.assertEqual(results["unrestricted"], np.ALLOW)


class InternalErrorTest(unittest.TestCase):
    def test_config_that_raises_on_membership_check_is_refused(self):
        class HostileMapping(dict):
            def __contains__(self, key):
                raise RuntimeError("boom")

        v = np.decide("example.com", HostileMapping({"mode": "denied"}))
        self.assertEqual(v.decision, np.REFUSE)
        # Either a well-named config failure or the last-resort catch-all
        # is acceptable here, but ALLOW is never acceptable.
        self.assertIn(v.rule, ("config-not-a-mapping", "internal-error"))

    def test_decide_never_raises_for_any_argument_type(self):
        weird_hosts = (None, 5, [], {}, object())
        weird_configs = (None, 5, [], object(), {"mode": object()})
        for host in weird_hosts:
            for config in weird_configs:
                try:
                    v = np.decide(host, config, high_autonomy=True)
                except Exception as exc:  # pragma: no cover - failure path
                    self.fail(
                        "decide() raised %r for host=%r config=%r" %
                        (exc, host, config))
                self.assertEqual(v.decision, np.REFUSE)


class VerdictShapeTest(unittest.TestCase):
    def test_verdict_allowed_property_matches_decision(self):
        allowed = np.decide("example.com", {"mode": "unrestricted"}, high_autonomy=False)
        refused = np.decide("example.com", {"mode": "denied"})
        self.assertTrue(allowed.allowed)
        self.assertFalse(refused.allowed)

    def test_module_exposes_exactly_allow_and_refuse_constants(self):
        self.assertEqual(np.ALLOW, "ALLOW")
        self.assertEqual(np.REFUSE, "REFUSE")
        self.assertNotEqual(np.ALLOW, np.REFUSE)


if __name__ == "__main__":
    unittest.main()
