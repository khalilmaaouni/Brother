"""Tests for capability_negotiation.py. Stdlib unittest only, importing the
real capability_precheck.py and host_capability.py from this same scripts
directory (no fakes, no mocks): the deciding property this module holds
only means something if it is proven against the real vocabulary and the
real precheck() rule, not a stand-in that could quietly drift from either.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import capability_negotiation as cn
import capability_precheck as cp

ENFORCED = cp.ENFORCED
ADVISORY = cp.ADVISORY


class RouteSelectionTests(unittest.TestCase):
    def test_full_route_selected_when_everything_enforced(self):
        reported = {"enforceable_deny": ENFORCED, "workspace_isolation": ENFORCED}
        route_name, reason = cn.negotiate(cn.ROUTES, reported)
        self.assertEqual(route_name, cn.AUTONOMOUS)
        self.assertIn("enforceable_deny", reason)

    def test_middle_route_selected_when_only_one_capability_enforced(self):
        reported = {"enforceable_deny": ADVISORY, "workspace_isolation": ENFORCED}
        route_name, _reason = cn.negotiate(cn.ROUTES, reported)
        self.assertEqual(route_name, cn.SUPERVISED)

    def test_all_requirements_unmet_falls_to_restricted(self):
        reported = {"enforceable_deny": ADVISORY, "workspace_isolation": ADVISORY}
        route_name, _reason = cn.negotiate(cn.ROUTES, reported)
        self.assertEqual(route_name, cn.RESTRICTED)

    def test_unmeasured_capability_routes_to_most_restrictive_path(self):
        # workspace_isolation is never mentioned at all: UNKNOWN under
        # precheck's own rule, never a grant, so it cannot satisfy
        # AUTONOMOUS or SUPERVISED.
        reported = {"enforceable_deny": ENFORCED}
        route_name, _reason = cn.negotiate(cn.ROUTES, reported)
        self.assertEqual(route_name, cn.RESTRICTED)

    def test_renaming_host_does_not_change_routing(self):
        # Same measurements, a different (and irrelevant) host label
        # riding alongside them: negotiate() never reads a host field, so
        # the extra key changes nothing about which route comes back.
        reported_claude = {
            "host": "claude",
            "enforceable_deny": ENFORCED,
            "workspace_isolation": ENFORCED,
        }
        reported_codex = {
            "host": "codex",
            "enforceable_deny": ENFORCED,
            "workspace_isolation": ENFORCED,
        }
        route_claude, _ = cn.negotiate(cn.ROUTES, reported_claude)
        route_codex, _ = cn.negotiate(cn.ROUTES, reported_codex)
        self.assertEqual(route_claude, route_codex)
        self.assertEqual(route_claude, cn.AUTONOMOUS)

    def test_routed_capabilities_match_pinned_expected_set(self):
        routed = set()
        for _route_name, required in cn.ROUTES:
            routed.update(required)
        self.assertEqual(routed, {"enforceable_deny", "workspace_isolation"})


class StructuralValidationTests(unittest.TestCase):
    def test_empty_routes_raises(self):
        with self.assertRaises(ValueError):
            cn.negotiate((), {})

    def test_duplicate_route_name_raises(self):
        routes = (("same", ()), ("same", ()))
        with self.assertRaises(ValueError) as caught:
            cn.negotiate(routes, {})
        self.assertIn("same", str(caught.exception))

    def test_last_route_with_requirement_raises(self):
        routes = (("first", ()), ("last", ("enforceable_deny",)))
        with self.assertRaises(ValueError):
            cn.negotiate(routes, {"enforceable_deny": ENFORCED})

    def test_unknown_capability_name_raises(self):
        routes = (("bogus", ("warp_drive",)), ("fallback", ()))
        with self.assertRaises(ValueError) as caught:
            cn.negotiate(routes, {"warp_drive": ENFORCED})
        self.assertIn("warp_drive", str(caught.exception))

    def test_non_dict_reported_still_resolves_to_fallback(self):
        route_name, _reason = cn.negotiate(cn.ROUTES, None)
        self.assertEqual(route_name, cn.RESTRICTED)


if __name__ == "__main__":
    unittest.main()
