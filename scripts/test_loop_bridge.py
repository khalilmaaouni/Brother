"""What the bridge must keep true.

One property matters more than everything else here: only plan()['batch'] may be
dispatched. Every node the scheduler put in deferred or blocked was refused for
a reason that is still true at dispatch time, and a bridge that widens the batch
by one node throws away the entire admission decision W5 exists to make. The
failure that produces is the one the founder named: two agents discovering a
shared file by corrupting it.

So most of this file is written against widening rather than against the happy
path.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_bridge as B  # noqa: E402


def node(nid, done_check="exit 0", owns=None, name=None):
    return {"id": nid, "name": name or nid, "done_check": done_check,
            "owns": owns if owns is not None else []}


PLAN = {
    "batch": [node("A"), node("B")],
    "deferred": [(node("C"), "FOUNDER-GATED: rendered in his lane"),
                 (node("D"), "NO DECLARED SCOPE: owns is absent"),
                 (node("E"), "write set overlaps A"),
                 (node("F"), "no free slot: capacity is 2")],
    "blocked": [(node("G"), ["W1"]), (node("H"), ["W1", "W2"])],
}


class Worker(object):
    def __init__(self, status="returned"):
        self.seen, self.status = [], status

    def run(self, unit):
        self.seen.append(unit["unit_id"])
        return {"worker_claim": "", "artifacts": [], "status": self.status,
                "cost": {"tokens": 0, "minutes": 0}}


class FakeVerify(object):
    PASS, FAIL, NO_DATA = "PASS", "FAIL", "NO-DATA"

    def __init__(self, verdict="PASS"):
        self.verdict = verdict

    def verify(self, unit, cwd=None):
        return {"verdict": self.verdict, "reason": "fake"}

    def is_pass(self, result):
        return result.get("verdict") == "PASS"


class FakeRepair(object):
    def __init__(self):
        self.called = []

    def repair(self, unit, verdict, worker, cwd=None, max_attempts=3):
        self.called.append(unit["unit_id"])
        return {"outcome": "REPAIRED", "attempts": [1],
                "final_verdict": {"verdict": "PASS"}, "reason": "fixed"}


def parts(verdict="PASS"):
    return {"spawn": None, "verify": FakeVerify(verdict), "repair": FakeRepair()}


class OnlyTheBatchIsEverDispatched(unittest.TestCase):
    """The load-bearing property."""

    def test_exactly_the_batch_and_nothing_else(self):
        w = Worker()
        B.run(PLAN, parts(), w)
        self.assertEqual(w.seen, ["A", "B"])

    def test_no_deferred_node_is_ever_dispatched(self):
        w = Worker()
        B.run(PLAN, parts(), w)
        for nid in ("C", "D", "E", "F"):
            self.assertNotIn(nid, w.seen,
                             "%s was deferred by the scheduler and dispatched "
                             "anyway" % nid)

    def test_no_blocked_node_is_ever_dispatched(self):
        w = Worker()
        B.run(PLAN, parts(), w)
        for nid in ("G", "H"):
            self.assertNotIn(nid, w.seen)

    def test_a_founder_gated_node_is_never_dispatched(self):
        """Its reason is still true at dispatch time: no session pulls his lane."""
        w = Worker()
        B.run(PLAN, parts(), w)
        self.assertNotIn("C", w.seen)

    def test_a_node_with_no_declared_write_set_is_never_dispatched(self):
        """Absent is not read-only. Nothing can prove it does not collide."""
        w = Worker()
        B.run(PLAN, parts(), w)
        self.assertNotIn("D", w.seen)

    def test_an_empty_batch_dispatches_nothing_at_all(self):
        w = Worker()
        got = B.run({"batch": [], "deferred": PLAN["deferred"],
                     "blocked": PLAN["blocked"]}, parts(), w)
        self.assertEqual(w.seen, [])
        self.assertEqual(got["dispatched"], [])

    def test_dispatchable_returns_the_batch_unchanged_and_unsorted(self):
        """Not filtered, not re-sorted, not topped up. Any future widening has
        to happen in that function, in front of its docstring."""
        self.assertEqual([n["id"] for n in B.dispatchable(PLAN)], ["A", "B"])


class WhatWasNotDoneIsSaidOutLoud(unittest.TestCase):
    """Silent truncation reads as full coverage: a deferred node looks identical
    to a node nobody had."""

    def test_every_refused_node_is_accounted_for_with_its_reason(self):
        got = dict(B.refused(PLAN))
        self.assertEqual(sorted(got), ["C", "D", "E", "F", "G", "H"])
        self.assertIn("FOUNDER-GATED", got["C"])
        self.assertIn("BLOCKED-BY W1", got["G"])

    def test_the_run_record_carries_them_too(self):
        got = B.run(PLAN, parts(), Worker())
        self.assertEqual(len(got["not_dispatched"]), 6)

    def test_a_blocked_node_names_every_dependency_it_waits_on(self):
        got = dict(B.refused(PLAN))
        self.assertIn("W1", got["H"])
        self.assertIn("W2", got["H"])


class RedGoesToRepairAndGreenDoesNot(unittest.TestCase):
    def test_a_passing_node_is_never_sent_to_repair(self):
        p = parts("PASS")
        B.run(PLAN, p, Worker())
        self.assertEqual(p["repair"].called, [])

    def test_a_failing_node_is_sent_to_repair(self):
        p = parts("FAIL")
        B.run(PLAN, p, Worker())
        self.assertEqual(p["repair"].called, ["A", "B"])

    def test_a_NO_DATA_node_is_still_handed_to_repair_which_refuses_it_itself(self):
        """The bridge does not second-guess the refusal: bm_repair owns the
        decision that NO-DATA is not repairable, and one owner per decision is
        why there is no copy of that rule here."""
        p = parts("NO-DATA")
        B.run(PLAN, p, Worker())
        self.assertEqual(p["repair"].called, ["A", "B"])

    def test_the_repaired_verdict_replaces_the_original_in_the_record(self):
        got = B.run({"batch": [node("A")], "deferred": [], "blocked": []},
                    parts("FAIL"), Worker())
        self.assertEqual(got["dispatched"][0]["verdict"], "PASS")
        self.assertEqual(got["dispatched"][0]["repair"]["outcome"], "REPAIRED")


class TheUnitHandedToTheWorkerIsWellFormed(unittest.TestCase):
    def test_it_carries_the_node_s_own_done_check_and_write_scope(self):
        seen = {}

        class _W(object):
            def run(self, unit):
                seen.update(unit)
                return {"worker_claim": "", "artifacts": [], "status": "returned",
                        "cost": {"tokens": 0, "minutes": 0}}

        B.run({"batch": [node("A", done_check="pytest -q", owns=["a.py"])],
               "deferred": [], "blocked": []}, parts(), _W())
        self.assertEqual(seen["done_check"], "pytest -q")
        self.assertEqual(seen["write_scope"], ["a.py"])
        self.assertEqual(seen["unit_id"], "A")


class RealUsageReachesTheRecordAndTheSidecar(unittest.TestCase):
    """T1 follow-up: a worker that reports real tokens_in/tokens_out/
    tokens_cached (bm_worker_spawn's additive "usage" key, sourced from
    model_worker.py reading the claude CLI's own answer) must reach the
    dispatched record, and the sidecar helpers that carry it to
    brother_run.py must round-trip it faithfully. The backwards case
    (no usage at all, today's ordinary worker) must never manufacture one."""

    def test_a_worker_that_reports_usage_puts_it_on_the_record(self):
        class UsageWorker(object):
            def run(self, unit, cwd=None):
                return {"worker_claim": "", "artifacts": [], "status": "returned",
                        "cost": {"tokens_in": 100, "tokens_out": 40,
                                 "tokens_cached": 25},
                        "usage": {"tokens_in": 100, "tokens_out": 40,
                                  "tokens_cached": 25}}

        got = B.run({"batch": [node("A")], "deferred": [], "blocked": []},
                    parts(), UsageWorker())
        self.assertEqual(got["dispatched"][0]["usage"],
                         {"tokens_in": 100, "tokens_out": 40,
                          "tokens_cached": 25})

    def test_a_worker_with_no_usage_leaves_the_key_off_the_record(self):
        """Today's ordinary worker (the Worker stub used everywhere else in
        this file) never reports usage. The record must not carry a
        fabricated {} or zeros for it: absent means not reported."""
        got = B.run(PLAN, parts(), Worker())
        self.assertNotIn("usage", got["dispatched"][0])

    def test_usage_sidecar_path_sits_beside_the_claim_store(self):
        self.assertEqual(B.usage_sidecar_path("/x/claims.json"),
                         "/x/claims_usage.json")

    def test_a_missing_sidecar_reads_as_empty_not_a_crash(self):
        self.assertEqual(B.read_usage_sidecar("/no/such/sidecar.json"), {})

    def test_the_sidecar_round_trips_what_was_written(self):
        tmp = tempfile.mkdtemp(prefix="usage-sidecar-")
        path = os.path.join(tmp, "claims_usage.json")
        data = {"A": {"tokens_in": 10, "tokens_out": 2, "tokens_cached": 1}}
        B.write_usage_sidecar(path, data)
        self.assertEqual(B.read_usage_sidecar(path), data)


class TheSiblingSeamIsNoDataAndNotACrash(unittest.TestCase):
    """Those three modules live in the other repository. An agent already
    searched one repository today for a file that lives in the other and
    reported a true claim as false, so this seam says where it looked."""

    def test_a_missing_tools_directory_is_NO_DATA_and_names_the_path(self):
        got, problem = B.load_parts("/no/such/tools/dir")
        self.assertIsNone(got)
        self.assertIn("/no/such/tools/dir", problem)

    def test_the_CLI_exits_NO_DATA_rather_than_pretending(self):
        self.assertEqual(B.main(["--tools", "/no/such/tools/dir"]), 2)

    def test_the_real_sibling_actually_loads(self):
        """If this ever fails, the loop has no moving parts, and that is worth
        knowing here rather than at dispatch time."""
        got, problem = B.load_parts()
        self.assertIsNotNone(got, problem)
        for key in ("spawn", "verify", "repair"):
            self.assertIn(key, got)


class TheDryRunClaimsNothing(unittest.TestCase):
    def test_dry_run_exits_zero_and_touches_no_worker(self):
        self.assertEqual(B.main(["--dry-run"]), 0)


class TheProofSliceIsAProofAndNotADemo(unittest.TestCase):
    """W9.5. A slice that can only pass proves nothing, so most of this is about
    making it fail."""

    def test_it_closes_end_to_end_across_a_seeded_failure(self):
        ok, trace = B.prove_slice()
        self.assertTrue(ok, trace)
        self.assertEqual([t["step"] for t in trace],
                         ["ready", "claimed", "dispatched", "verified",
                          "failed", "repaired", "reverified", "closed"])

    def test_the_seeded_failure_really_happens(self):
        """If the first attempt passed, the run would prove the pieces are
        connected and nothing about repair, which is the whole difficulty."""
        ok, trace = B.prove_slice()
        verified = [t for t in trace if t["step"] == "verified"][0]
        self.assertIn("FAIL", verified["detail"])

    def test_the_file_the_unit_was_asked_to_create_really_exists_afterwards(self):
        """The done_check is `test -f`, so a fake worker cannot satisfy it by
        claiming success."""
        ok, trace = B.prove_slice()
        closed = [t for t in trace if t["step"] == "closed"][0]
        self.assertIn("True", closed["detail"])

    def test_a_missing_sibling_makes_the_proof_FAIL_not_pass(self):
        ok, trace = B.prove_slice(tools="/no/such/tools/dir")
        self.assertFalse(ok)

    def test_the_CLI_exits_zero_only_when_it_actually_closed(self):
        self.assertEqual(B.main(["--prove-slice", "--assert-unattended"]), 0)
        self.assertEqual(B.main(["--prove-slice", "--tools", "/no/such/dir"]), 1)


class UnattendedIsProvenMechanically(unittest.TestCase):
    """A model turn cannot happen inside one uninterrupted process: it would
    have to return to the caller first. So one pid across every transition IS
    the claim, rather than a sentence asserting it."""

    def test_one_pid_across_every_transition_passes(self):
        ok, why = B.assert_unattended(
            [{"step": s, "pid": 7} for s in
             ["ready", "claimed", "dispatched", "verified", "failed",
              "repaired", "reverified", "closed"]])
        self.assertTrue(ok, why)

    def test_a_SECOND_pid_anywhere_fails_the_proof(self):
        """This is the assertion that would catch a step which waited for a
        human: it would have to return to a caller, and the chain would break."""
        trace = [{"step": s, "pid": 7} for s in
                 ["ready", "claimed", "dispatched", "verified", "failed",
                  "repaired", "reverified", "closed"]]
        trace[4]["pid"] = 9
        ok, why = B.assert_unattended(trace)
        self.assertFalse(ok)
        self.assertIn("returned to a caller", why)

    def test_a_MISSING_transition_fails_the_proof(self):
        """A slice that skipped the repair step would otherwise pass by being
        shorter."""
        ok, why = B.assert_unattended(
            [{"step": s, "pid": 7} for s in
             ["ready", "claimed", "dispatched", "verified", "closed"]])
        self.assertFalse(ok)
        self.assertIn("sequence", why)

    def test_the_transitions_must_be_IN_ORDER(self):
        ok, why = B.assert_unattended(
            [{"step": s, "pid": 7} for s in
             ["ready", "claimed", "verified", "dispatched", "failed",
              "repaired", "reverified", "closed"]])
        self.assertFalse(ok)


class TheRuntimeIsResolvedNotHardcoded(unittest.TestCase):
    """A peer review found this file hardcoding one developer's home directory
    on the day it was written. That works on exactly one machine: a stranger who
    installs Brother has no such path and never will, and the failure they would
    see is a cryptic import error rather than a sentence telling them what was
    expected."""

    def test_the_installed_locations_are_tried_BEFORE_the_development_one(self):
        """The ordinary case must be the one that works without configuration."""
        order = B.runtime_candidates({})
        self.assertEqual(order[-1], B.DEV_CANDIDATE,
                         "the development checkout must be last, not first")
        self.assertGreater(len(order), 1, "there must be installed candidates")

    def test_an_explicit_override_wins_over_everything(self):
        order = B.runtime_candidates({B.RUNTIME_ENV_VAR: "/opt/brother"})
        self.assertEqual(order[0], "/opt/brother/tools")

    def test_an_override_that_already_names_tools_is_not_doubled(self):
        order = B.runtime_candidates({B.RUNTIME_ENV_VAR: "/opt/brother/tools"})
        self.assertEqual(order[0], "/opt/brother/tools")

    def test_a_real_versioned_install_is_matched(self):
        """EVAD run 5 trial 2: the plugin cache is versioned
        (cache/brother/brothermode/<version>/tools) and the unversioned
        candidate never matches it, so a real install was reported absent.
        The resolver must find the versioned layout the installer actually
        writes, newest version first, still ahead of the development path."""
        td = tempfile.TemporaryDirectory(prefix="lb-home-")
        self.addCleanup(td.cleanup)
        home = td.name
        for ver in ("1.2.3", "1.10.0"):
            os.makedirs(os.path.join(home, ".claude", "plugins", "cache",
                                     "brother", "brothermode", ver, "tools"))
        order = B.runtime_candidates({"HOME": home})
        newest = os.path.join(home, ".claude", "plugins", "cache", "brother",
                              "brothermode", "1.10.0", "tools")
        older = os.path.join(home, ".claude", "plugins", "cache", "brother",
                             "brothermode", "1.2.3", "tools")
        self.assertIn(newest, order, order)
        self.assertIn(older, order, order)
        self.assertLess(order.index(newest), order.index(older),
                        "1.10.0 must outrank 1.2.3 numerically, not "
                        "lexicographically")
        self.assertLess(order.index(newest), order.index(B.DEV_CANDIDATE))

    def test_no_developer_home_path_is_the_only_way_to_find_the_runtime(self):
        """The regression that matters: if every installed candidate vanished
        and only the development path remained, this file would be back to
        working on one machine."""
        installed = [c for c in B.runtime_candidates({}) if c != B.DEV_CANDIDATE]
        self.assertTrue(installed, "every candidate is a development path")

    def test_finding_nothing_NAMES_every_place_it_looked(self):
        """A reader with none of these needs to know what was expected, not
        merely that something was missing."""
        got, problem = B.load_parts(env={B.RUNTIME_ENV_VAR: "/no/such/root"})
        if got is None:
            self.assertIn("/no/such/root", problem)
            self.assertIn(B.RUNTIME_ENV_VAR, problem)
        else:
            self.skipTest("a real runtime resolved on this machine, so the "
                          "not-found path cannot be exercised here")


class DispatchIsActuallyConcurrent(unittest.TestCase):
    """Until 2026-08-29 run() was a list comprehension: spawn, wait, spawn,
    wait. The admission decision that PERMITS parallelism sat one layer up,
    unused, and the loop was as slow as the sum of its parts."""

    def test_the_batch_really_overlaps_in_time(self):
        """Measured rather than asserted: three workers that each sleep must
        finish in well under three times one sleep."""
        import threading
        import time
        started, lock = [], threading.Lock()

        class Slow(object):
            def run(self, unit):
                with lock:
                    started.append(unit["unit_id"])
                time.sleep(0.30)
                return {"worker_claim": "", "artifacts": [],
                        "status": "returned", "cost": {"tokens": 0, "minutes": 0}}

        plan = {"batch": [node("A"), node("B"), node("C")],
                "deferred": [], "blocked": []}
        t0 = time.time()
        B.run(plan, parts(), Slow(), max_in_flight=3)
        elapsed = time.time() - t0
        self.assertEqual(len(started), 3)
        self.assertLess(elapsed, 0.75,
                        "three 0.30s workers took %.2fs, which is serial" % elapsed)

    def test_results_come_back_in_the_BATCH_order_not_completion_order(self):
        """So a reader can line them up against the plan they came from."""
        import time

        class Uneven(object):
            def run(self, unit):
                time.sleep(0.2 if unit["unit_id"] == "A" else 0.01)
                return {"worker_claim": "", "artifacts": [],
                        "status": "returned", "cost": {"tokens": 0, "minutes": 0}}

        plan = {"batch": [node("A"), node("B"), node("C")],
                "deferred": [], "blocked": []}
        got = B.run(plan, parts(), Uneven(), max_in_flight=3)
        self.assertEqual([r["id"] for r in got["dispatched"]], ["A", "B", "C"])

    def test_one_worker_that_RAISES_does_not_take_the_batch_down(self):
        """One bad unit must not cost the other two."""
        class Explodes(object):
            def run(self, unit):
                if unit["unit_id"] == "B":
                    raise RuntimeError("boom")
                return {"worker_claim": "", "artifacts": [],
                        "status": "returned", "cost": {"tokens": 0, "minutes": 0}}

        plan = {"batch": [node("A"), node("B"), node("C")],
                "deferred": [], "blocked": []}
        got = B.run(plan, parts(), Explodes(), max_in_flight=3)
        self.assertEqual(len(got["dispatched"]), 3)
        bad = [r for r in got["dispatched"] if r["id"] == "B"][0]
        self.assertEqual(bad["verdict"], "NO-DATA")
        self.assertIn("boom", bad["reason"])

    def test_the_cap_is_a_RESOURCE_limit_and_is_reported_as_one(self):
        """The batch arrives conflict-free from the scheduler, so this cap is
        about what the machine can run, never about collision avoidance. Field
        research found merge-conflict risk given as a reason in zero vendor
        documents."""
        plan = {"batch": [node("A")], "deferred": [], "blocked": []}
        got = B.run(plan, parts(), Worker(), max_in_flight=2)
        self.assertEqual(got["in_flight_cap"], 2)

    def test_a_cap_of_zero_dispatches_nothing_rather_than_hanging(self):
        w = Worker()
        got = B.run({"batch": [node("A")], "deferred": [], "blocked": []},
                    parts(), w, max_in_flight=0)
        self.assertEqual(w.seen, [])
        self.assertEqual(got["dispatched"], [])

    def test_an_empty_batch_is_still_fine_under_concurrency(self):
        got = B.run({"batch": [], "deferred": [], "blocked": []}, parts(), Worker())
        self.assertEqual(got["dispatched"], [])


class AnUndeclaredWriteIsNotIntegrableHoweverGreenItIs(unittest.TestCase):
    """Parity blocker P0.3's acceptance test, from the directive: a worker
    deliberately writes one undeclared file, and the expected result is
    QUARANTINE with zero canonical integration.

    The property being pinned is the uncomfortable one. The unit's OWN
    verification passes. It still must not reach canonical, because the thing
    that passed is not the thing that was authorised, and a worker saying it
    only touched X is a claim while the diff is evidence."""

    def _repo(self):
        import subprocess as sp
        d = tempfile.mkdtemp(prefix="canon-")
        run = lambda *a: sp.run(["git"] + list(a), cwd=d, capture_output=True, text=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "a@b.c")
        run("config", "user.name", "t")
        for n in ("declared.txt", "other.txt"):
            with open(os.path.join(d, n), "w", encoding="utf-8") as fh:
                fh.write("base\n")
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        return d

    def _run(self, repo, sneaky):
        import subprocess as sp

        class Worker(object):
            def run(self, unit):
                with open(os.path.join(repo, "declared.txt"), "a",
                          encoding="utf-8") as fh:
                    fh.write("work\n")
                if sneaky:
                    with open(os.path.join(repo, "other.txt"), "a",
                              encoding="utf-8") as fh:
                        fh.write("undeclared\n")
                sp.run(["git", "add", "-A"], cwd=repo, capture_output=True)
                sp.run(["git", "commit", "-q", "-m", "w"], cwd=repo,
                       capture_output=True)
                return {"status": "ok"}

        class Verify(object):
            @staticmethod
            def verify(unit, cwd=None):
                return {"verdict": "PASS", "reason": "its own check passed"}

            @staticmethod
            def is_pass(v):
                return v.get("verdict") == "PASS"

        class Repair(object):
            @staticmethod
            def repair(*a, **k):
                return {"outcome": "n/a", "attempts": [], "reason": "",
                        "final_verdict": {}}

        node = {"id": "U1", "name": "a unit", "done_check": "x",
                "owns": ["declared.txt"]}
        return B.run_node(node, {"verify": Verify, "repair": Repair},
                                    Worker(), cwd=repo)

    def test_a_unit_inside_its_scope_is_clean_and_integrable(self):
        rec = self._run(self._repo(), sneaky=False)
        self.assertEqual(rec["scope"]["verdict"], "CLEAN")
        self.assertTrue(rec["integrable"])

    def test_one_undeclared_file_quarantines_it(self):
        rec = self._run(self._repo(), sneaky=True)
        self.assertEqual(rec["scope"]["verdict"], "QUARANTINE")

    def test_it_is_not_integrable_even_though_its_own_verdict_is_PASS(self):
        """The whole point. A green self-verdict does not buy integration."""
        rec = self._run(self._repo(), sneaky=True)
        self.assertEqual(rec["verdict"], "PASS")
        self.assertFalse(rec["integrable"])

    def test_the_block_names_the_undeclared_path(self):
        rec = self._run(self._repo(), sneaky=True)
        self.assertIn("other.txt", rec["integration_block"])

    def test_no_baseline_is_NO_DATA_and_also_not_integrable(self):
        """An unauditable change reaching canonical is the failure this exists
        to prevent, so unknown must block exactly as a violation does."""
        scope = B._audit_scope({"write_scope": ["a"]}, None, None)
        self.assertEqual(scope["verdict"], "NO-DATA")


if __name__ == "__main__":
    unittest.main()
