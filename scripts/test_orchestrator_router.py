"""What the deterministic router must keep true.

This module exists for two shapes of failure above all others.

The first: a router that just returns the same orchestrator for every task
class would pass any test that only checks "route() returned something".
AtLeastTwoRolesAppear sweeps the real TASK_CLASSES frozenset (never a
hand-typed list, so a class added later is exercised automatically) and
asserts both role names show up, then checks every single class against the
stated preference table by name.

The second: a Recommendation that could be mistaken for authorization would
let a caller skip the lease this whole build exists to enforce.
RecommendationCarriesNoAuthority checks the returned object is a plain
tuple with no method whose name could be read as "go ahead and act".
"""
import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchestrator_authority as authority  # noqa: E402
import orchestrator_invariants as invariants  # noqa: E402
import orchestrator_router as router  # noqa: E402


def healthy():
    return {"primary": "HEALTHY", "secondary": "HEALTHY"}


def task(task_id="T1", task_class="implementation", risk_class="medium", **extra):
    body = {"id": task_id, "task_class": task_class, "risk_class": risk_class}
    body.update(extra)
    return body


class EveryTaskClassRoutes(unittest.TestCase):
    """Rule 1: every real TASK_CLASSES member routes without raising, using
    the frozenset itself so a class added later is covered automatically."""

    def test_every_task_class_routes_without_raising(self):
        for task_class in invariants.TASK_CLASSES:
            rec = router.route(
                task(task_class=task_class), leases=None, health=healthy(), now=1000.0)
            self.assertIn(rec.orchestrator, router.ROLES, msg=task_class)


class AtLeastTwoRolesAppear(unittest.TestCase):
    """Rule (bad-state guard): a router recommending one orchestrator for
    everything is the specific bad state a shallow green run would also
    pass. This asserts the sweep is not degenerate AND that each class
    lands on the exact role the stated table promises."""

    EXPECTED = {
        "planning": "primary",
        "architecture": "primary",
        "research": "primary",
        "verification": "primary",
        "review": "primary",
        "implementation": "secondary",
        "test": "secondary",
        "repair": "secondary",
        "fault-injection": "secondary",
        "packaging": "secondary",
        "integration-prep": "secondary",
        "documentation": "primary",
        "benchmark": "secondary",
    }

    def test_expected_table_covers_every_real_task_class(self):
        self.assertEqual(frozenset(self.EXPECTED), invariants.TASK_CLASSES)

    def test_sweep_is_not_degenerate_and_matches_the_table(self):
        seen = set()
        for task_class, expected_role in self.EXPECTED.items():
            rec = router.route(
                task(task_class=task_class), leases=None, health=healthy(), now=1000.0)
            self.assertEqual(rec.orchestrator, expected_role, msg=task_class)
            seen.add(rec.orchestrator)
        self.assertEqual(seen, router.ROLES,
                          "a router that names only one role across the whole "
                          "sweep would still pass a test that ignores this")


class UnknownTaskClassIsRefused(unittest.TestCase):
    """Rule 2: unknown class raises, never silently reads as implementation."""

    def test_unknown_class_raises(self):
        with self.assertRaises(ValueError):
            router.route(task(task_class="teleportation"), leases=None,
                         health=healthy(), now=1000.0)

    def test_unknown_class_never_becomes_implementation(self):
        try:
            router.route(task(task_class="teleportation"), leases=None,
                         health=healthy(), now=1000.0)
            self.fail("expected ValueError")
        except ValueError:
            pass


class HealthValidation(unittest.TestCase):
    def test_missing_role_in_health_raises(self):
        with self.assertRaises(ValueError):
            router.route(task(), leases=None, health={"primary": "HEALTHY"}, now=1000.0)

    def test_unknown_health_state_raises(self):
        bad = {"primary": "HEALTHY", "secondary": "ASLEEP"}
        with self.assertRaises(ValueError):
            router.route(task(), leases=None, health=bad, now=1000.0)

    def test_unknown_role_key_in_health_raises(self):
        bad = {"primary": "HEALTHY", "secondary": "HEALTHY", "tertiary": "HEALTHY"}
        with self.assertRaises(ValueError):
            router.route(task(), leases=None, health=bad, now=1000.0)


class TaskValidation(unittest.TestCase):
    def test_missing_id_raises(self):
        with self.assertRaises(ValueError):
            router.route({"task_class": "test", "risk_class": "low"},
                         leases=None, health=healthy(), now=1000.0)

    def test_unknown_risk_class_raises(self):
        with self.assertRaises(ValueError):
            router.route(task(risk_class="catastrophic"), leases=None,
                         health=healthy(), now=1000.0)

    def test_non_bool_allow_cross_raises(self):
        with self.assertRaises(ValueError):
            router.route(task(allow_cross_orchestrator_takeover="yes"),
                         leases=None, health=healthy(), now=1000.0)


class UnavailableOrStoppedFallsBack(unittest.TestCase):
    """Rule 3: preferred role UNAVAILABLE or STOPPED routes to the other
    role, and the reason says so."""

    def test_unavailable_preferred_falls_back(self):
        health = {"primary": "UNAVAILABLE", "secondary": "HEALTHY"}
        rec = router.route(task(task_class="review"), leases=None,
                           health=health, now=1000.0)
        self.assertEqual(rec.orchestrator, "secondary")
        self.assertIn("unavailable", rec.reason.lower() + rec.reason)

    def test_stopped_preferred_falls_back(self):
        health = {"primary": "STOPPED", "secondary": "HEALTHY"}
        rec = router.route(task(task_class="architecture"), leases=None,
                           health=health, now=1000.0)
        self.assertEqual(rec.orchestrator, "secondary")
        self.assertIn("STOPPED", rec.reason)

    def test_reason_names_the_fallen_back_from_role(self):
        health = {"primary": "UNAVAILABLE", "secondary": "HEALTHY"}
        rec = router.route(task(task_class="planning"), leases=None,
                           health=health, now=1000.0)
        self.assertIn("primary", rec.reason)
        self.assertIn("falls back", rec.reason)


class DegradedStillTakesWork(unittest.TestCase):
    """Rule 4, the judgement call: DEGRADED is not UNAVAILABLE or STOPPED,
    so the preferred role keeps the task."""

    def test_degraded_preferred_keeps_the_task(self):
        health = {"primary": "DEGRADED", "secondary": "HEALTHY"}
        rec = router.route(task(task_class="verification"), leases=None,
                           health=health, now=1000.0)
        self.assertEqual(rec.orchestrator, "primary")

    def test_degraded_secondary_keeps_execution_tasks_too(self):
        health = {"primary": "HEALTHY", "secondary": "DEGRADED"}
        rec = router.route(task(task_class="test"), leases=None,
                           health=health, now=1000.0)
        self.assertEqual(rec.orchestrator, "secondary")


class ActiveConflictingLease(unittest.TestCase):
    """Rule 5: a live lease held by the OTHER role blocks the recommendation
    entirely, naming holder and epoch, never proposing a takeover."""

    def test_lease_held_by_the_other_role_blocks_recommendation(self):
        lease = authority.Lease(
            run_id="R1", scope="T1", orchestrator="secondary",
            instance="sec-a", epoch=3, acquired_at=900.0, expires_at=2000.0)
        rec = router.route(task(task_class="review"), leases=lease,
                           health=healthy(), now=1000.0)
        self.assertIsNone(rec.orchestrator)
        self.assertIsNone(rec.second_choice)
        self.assertIn("secondary", rec.reason)
        self.assertIn("epoch 3", rec.reason)
        self.assertIn("3", rec.reason)

    def test_lease_held_by_the_candidate_itself_is_not_a_conflict(self):
        lease = authority.Lease(
            run_id="R1", scope="T1", orchestrator="primary",
            instance="prim-a", epoch=1, acquired_at=900.0, expires_at=2000.0)
        rec = router.route(task(task_class="review"), leases=lease,
                           health=healthy(), now=1000.0)
        self.assertEqual(rec.orchestrator, "primary")

    def test_expired_lease_is_not_a_conflict(self):
        """now is past expires_at: the router re-checks liveness itself
        rather than trusting a stale caller-supplied lease object."""
        lease = authority.Lease(
            run_id="R1", scope="T1", orchestrator="secondary",
            instance="sec-a", epoch=3, acquired_at=100.0, expires_at=500.0)
        rec = router.route(task(task_class="review"), leases=lease,
                           health=healthy(), now=1000.0)
        self.assertEqual(rec.orchestrator, "primary")

    def test_lease_as_a_plain_dict_is_accepted(self):
        lease = {"orchestrator": "secondary", "instance": "sec-a", "epoch": 5,
                  "expires_at": 2000.0}
        rec = router.route(task(task_class="review"), leases=lease,
                           health=healthy(), now=1000.0)
        self.assertIsNone(rec.orchestrator)
        self.assertIn("epoch 5", rec.reason)

    def test_unknown_lease_holder_role_raises(self):
        lease = {"orchestrator": "tertiary", "epoch": 1, "expires_at": 2000.0}
        with self.assertRaises(ValueError):
            router.route(task(task_class="review"), leases=lease,
                         health=healthy(), now=1000.0)


class ForbiddenFallbackYieldsNoRecommendation(unittest.TestCase):
    """Rule 6: allow_cross_orchestrator_takeover false, preferred is down:
    the router never names the fallback, it reports no recommendation."""

    def test_forbidden_fallback_is_never_named(self):
        health = {"primary": "HEALTHY", "secondary": "UNAVAILABLE"}
        t = task(task_class="implementation", allow_cross_orchestrator_takeover=False)
        rec = router.route(t, leases=None, health=health, now=1000.0)
        self.assertIsNone(rec.orchestrator)
        self.assertIsNone(rec.second_choice)
        self.assertNotIn("orchestrator=primary", str(rec))
        self.assertIn("forbidden", rec.reason)

    def test_same_inputs_with_takeover_allowed_do_fall_back(self):
        """Sanity check that the forbidding above is really the flag's
        doing, not some other difference between the two calls."""
        health = {"primary": "HEALTHY", "secondary": "UNAVAILABLE"}
        t = task(task_class="implementation", allow_cross_orchestrator_takeover=True)
        rec = router.route(t, leases=None, health=health, now=1000.0)
        self.assertEqual(rec.orchestrator, "primary")

    def test_allow_cross_defaults_true_when_absent(self):
        health = {"primary": "HEALTHY", "secondary": "UNAVAILABLE"}
        rec = router.route(task(task_class="implementation"), leases=None,
                           health=health, now=1000.0)
        self.assertEqual(rec.orchestrator, "primary")


class CrossReviewFollowsRiskClass(unittest.TestCase):
    """Rule 7: high/critical sets cross_review to the OPPOSITE role of
    whoever is recommended; low/medium leave it unset. The reviewer is
    never the same role as the author, on a full sweep."""

    def test_low_and_medium_do_not_require_cross_review(self):
        for risk in ("low", "medium"):
            rec = router.route(task(task_class="implementation", risk_class=risk),
                               leases=None, health=healthy(), now=1000.0)
            self.assertIsNone(rec.cross_review, msg=risk)

    def test_high_and_critical_set_cross_review_to_the_opposite_role(self):
        for risk in ("high", "critical"):
            rec = router.route(task(task_class="implementation", risk_class=risk),
                               leases=None, health=healthy(), now=1000.0)
            self.assertEqual(rec.cross_review, "primary", msg=risk)
            self.assertNotEqual(rec.cross_review, rec.orchestrator, msg=risk)

    def test_cross_reviewer_is_never_the_author_across_the_full_sweep(self):
        for task_class in invariants.TASK_CLASSES:
            for risk in ("high", "critical"):
                rec = router.route(task(task_class=task_class, risk_class=risk),
                                   leases=None, health=healthy(), now=1000.0)
                if rec.cross_review is not None:
                    self.assertNotEqual(rec.cross_review, rec.orchestrator,
                                        msg="%s/%s" % (task_class, risk))


class AttemptHistoryAvoidsRepeatedFailure(unittest.TestCase):
    """Rule 8: an owner that has already failed this task twice is not
    recommended again; the router falls back to the other role, or to no
    recommendation when both have failed twice."""

    def test_owner_failed_twice_falls_back_to_the_other_role(self):
        attempts = ["primary", "primary"]
        rec = router.route(task(task_class="review"), leases=None,
                           health=healthy(), attempts=attempts, now=1000.0)
        self.assertEqual(rec.orchestrator, "secondary")
        self.assertIn("failed this task twice", rec.reason)

    def test_a_single_prior_failure_does_not_trigger_the_fallback(self):
        attempts = ["primary"]
        rec = router.route(task(task_class="review"), leases=None,
                           health=healthy(), attempts=attempts, now=1000.0)
        self.assertEqual(rec.orchestrator, "primary")

    def test_both_roles_failed_twice_yields_no_recommendation(self):
        attempts = ["primary", "primary", "secondary", "secondary"]
        rec = router.route(task(task_class="review"), leases=None,
                           health=healthy(), attempts=attempts, now=1000.0)
        self.assertIsNone(rec.orchestrator)
        self.assertIn("neither", rec.reason)

    def test_attempt_entries_as_dicts_are_accepted(self):
        attempts = [{"orchestrator": "primary"}, {"orchestrator": "primary"}]
        rec = router.route(task(task_class="review"), leases=None,
                           health=healthy(), attempts=attempts, now=1000.0)
        self.assertEqual(rec.orchestrator, "secondary")

    def test_unknown_role_in_attempt_history_raises(self):
        with self.assertRaises(ValueError):
            router.route(task(task_class="review"), leases=None,
                         health=healthy(), attempts=["tertiary"], now=1000.0)

    def test_forbidden_fallback_combines_with_failed_attempts(self):
        """A task that forbids cross-orchestrator takeover must not name
        the fallback even when the reason it is being considered is two
        prior failures rather than health."""
        t = task(task_class="review", allow_cross_orchestrator_takeover=False)
        rec = router.route(t, leases=None, health=healthy(),
                           attempts=["primary", "primary"], now=1000.0)
        self.assertIsNone(rec.orchestrator)
        self.assertIsNone(rec.second_choice)


class Determinism(unittest.TestCase):
    """Rule 9: the same inputs always produce the same recommendation. No
    randomness, no dict-ordering dependence, no clock dependence except
    through `now`."""

    def test_twenty_identical_calls_produce_one_distinct_result(self):
        lease = authority.Lease(
            run_id="R1", scope="T1", orchestrator="primary",
            instance="prim-a", epoch=2, acquired_at=900.0, expires_at=2000.0)
        health = {"primary": "DEGRADED", "secondary": "HEALTHY"}
        attempts = ["secondary"]
        t = task(task_class="review", risk_class="high")
        results = set()
        for _ in range(20):
            rec = router.route(t, leases=lease, health=health,
                               attempts=list(attempts), now=1000.0)
            results.add(rec)
        self.assertEqual(len(results), 1, results)

    def test_dict_ordering_of_health_does_not_matter(self):
        a = router.route(task(task_class="implementation"), leases=None,
                         health={"primary": "HEALTHY", "secondary": "HEALTHY"},
                         now=1000.0)
        b = router.route(task(task_class="implementation"), leases=None,
                         health={"secondary": "HEALTHY", "primary": "HEALTHY"},
                         now=1000.0)
        self.assertEqual(a, b)


class RecommendationCarriesNoAuthority(unittest.TestCase):
    """The return value must never be usable as authorization: no method
    that could be mistaken for one that grants, checks or spends it."""

    def test_recommendation_is_a_plain_tuple_with_named_fields_only(self):
        rec = router.route(task(), leases=None, health=healthy(), now=1000.0)
        self.assertIsInstance(rec, tuple)
        self.assertEqual(rec._fields,
                         ("task_id", "orchestrator", "reason", "second_choice",
                          "cross_review"))
        for name in ("apply", "grant", "authorize", "check", "acquire",
                     "commit", "confirm"):
            self.assertFalse(hasattr(rec, name), msg=name)

    def test_route_takes_no_store_path_or_run_id(self):
        """This module never touches the authority store directly: route()
        has no way to be handed one."""
        params = inspect.signature(router.route).parameters
        self.assertNotIn("store_path", params)
        self.assertNotIn("run_id", params)


if __name__ == "__main__":
    unittest.main()
