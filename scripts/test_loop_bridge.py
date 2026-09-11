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
import claim_store as C  # noqa: E402

#: D5: a real dev checkout of the sibling tools, so a full B.main() call can
#: load_parts() for real rather than needing a third fake stood up for it.
#: run() itself is stood in for below, so nothing here ever spawns a worker.
TOOLS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "products", "brothermode", "tools"))

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


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

    def test_a_relocated_CLAUDE_CONFIG_DIR_install_is_matched(self):
        """Root cause of run 33770056113 (virgin-install.yml, ubuntu-latest,
        v1.0.1): bundle-install-smoke.sh installs into a throwaway
        CLAUDE_CONFIG_DIR, which is where the claude CLI actually places an
        installed plugin's cache. This resolver used to build every
        installed candidate from bare HOME, so a plugin cache under a
        CLAUDE_CONFIG_DIR that differs from $HOME/.claude (a sandboxed
        install, a CI runner, clean_install_e2e.sh's own throwaway HOME
        beside a throwaway config dir) was invisible: 'no worker adapter
        could be loaded, so no worker ran'. It only ever looked right on a
        developer's own machine because a real brothermode install already
        sat under the real $HOME/.claude from ordinary daily use, which
        masked the bug rather than proving the resolution correct (measured:
        an isolated HOME with no CLAUDE_CONFIG_DIR override reproduced the
        exact CI failure locally)."""
        home_td = tempfile.TemporaryDirectory(prefix="lb-home-")
        self.addCleanup(home_td.cleanup)
        config_td = tempfile.TemporaryDirectory(prefix="lb-config-")
        self.addCleanup(config_td.cleanup)
        home = home_td.name
        config_dir = config_td.name
        # HOME's own .claude carries nothing: a virgin machine, or any
        # install where CLAUDE_CONFIG_DIR relocates the config root away
        # from the default.
        tools = os.path.join(config_dir, "plugins", "cache", "brother",
                             "brothermode", "3.4.4", "tools")
        os.makedirs(tools)
        order = B.runtime_candidates({"HOME": home,
                                      "CLAUDE_CONFIG_DIR": config_dir})
        self.assertIn(tools, order, order)
        self.assertLess(order.index(tools), order.index(B.DEV_CANDIDATE),
                        "the relocated install must outrank the dev checkout")
        # And the same fixture under bare HOME must NOT be found: proves this
        # candidate is really reading CLAUDE_CONFIG_DIR, not silently
        # matching by coincidence.
        home_tools = os.path.join(home, ".claude", "plugins", "cache",
                                  "brother", "brothermode", "3.4.4", "tools")
        self.assertNotIn(home_tools, order, order)

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

    def test_a_codex_home_install_is_matched(self):
        """Codex has no dependency resolution, so the brothermode plugin is
        installed by name into CODEX_HOME's own plugin cache. The resolver
        must find it there under BROTHER_CLIENT=codex, ranked ahead of the
        development checkout, and must not be fooled by a Claude-shaped
        cache sitting under the same HOME."""
        home_td = tempfile.TemporaryDirectory(prefix="lb-home-")
        self.addCleanup(home_td.cleanup)
        codex_td = tempfile.TemporaryDirectory(prefix="lb-codex-home-")
        self.addCleanup(codex_td.cleanup)
        home = home_td.name
        codex_home = codex_td.name
        tools = os.path.join(codex_home, "plugins", "cache", "brother",
                             "brothermode", "3.4.4", "tools")
        os.makedirs(tools)
        env = {"HOME": home, "CODEX_HOME": codex_home,
              B.brother_paths.CLIENT_ENV: B.brother_paths.CODEX}
        order = B.runtime_candidates(env)
        self.assertIn(tools, order, order)
        self.assertLess(order.index(tools), order.index(B.DEV_CANDIDATE),
                        "the Codex install must outrank the dev checkout")
        claude_shaped = os.path.join(home, ".claude", "plugins", "cache",
                                     "brother", "brothermode", "3.4.4",
                                     "tools")
        self.assertNotIn(claude_shaped, order, order)

    def test_the_dead_candidates_are_gone(self):
        """BUNDLED_CANDIDATE (bundle/tools) never exists anywhere, in the repo
        or in any real install, and the unversioned cache candidate
        (.../brother/brothermode/tools with no version segment) never
        matches a real install either. Both were dead weight; HUB_CANDIDATE
        replaces the bundle one and must rank ahead of anything under a
        plugin cache."""
        order = B.runtime_candidates({})
        bundle_tools = os.path.join("bundle", "tools")
        unversioned = os.path.join("brother", "brothermode", "tools")
        for candidate in order:
            self.assertFalse(candidate.endswith(bundle_tools), candidate)
            self.assertFalse(candidate.endswith(unversioned), candidate)
        self.assertIn(B.HUB_CANDIDATE, order, order)
        plugin_candidates = [c for c in order if "plugins" in c]
        for c in plugin_candidates:
            self.assertLess(order.index(B.HUB_CANDIDATE), order.index(c),
                            "HUB_CANDIDATE must come before every plugin "
                            "cache candidate")

    def test_not_found_names_the_sibling_plugin_install(self):
        """The NO-DATA sentence must not just say what is missing, it must
        say the next command to run."""
        msg = B.not_found_message(["/a/tools", "/b/tools"], [])
        self.assertIn("/a/tools", msg)
        self.assertIn("/b/tools", msg)
        self.assertIn(B.RUNTIME_ENV_VAR, msg)
        self.assertIn("brothermode@brother", msg)
        self.assertIn("codex plugin add", msg)


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


class ARenewedLeaseSurvivesAWorkerLongerThanItsLease(unittest.TestCase):
    """D5, first half. brother_run.run_loop guards its one blocking call into
    loop_bridge.main() with claim_store.BackgroundRenewal, started right
    before the call and stopped right after. Run standalone (the estate's
    own documented `python3 scripts/loop_bridge.py --cwd <dir> --worker-cmd
    <cmd>`), nothing guarded the equivalent wait inside main() itself, so a
    unit whose worker outlived the lease read abandoned under a still-live
    run. This exercises the fix through B.main() itself: only graph_loop and
    run() are stood in for; the claim, the renewal and the release are real.
    """

    def test_a_batch_slower_than_the_lease_is_not_read_as_abandoned(self):
        import threading
        import time
        old_ttl = os.environ.get(C.TTL_ENV_VAR)
        os.environ[C.TTL_ENV_VAR] = "0.3"
        started = threading.Event()
        allow_finish = threading.Event()

        def fake_run(plan, parts, worker, cwd=None, max_attempts=3,
                    max_in_flight=None, lanes=None):
            started.set()
            allow_finish.wait(5)
            return {"dispatched": [{"id": "U1", "verdict": "PASS",
                                    "scope": {"verdict": "OK"}}],
                   "isolation": {}}

        old_load, old_plan, old_run = B.graph_loop.load, B.graph_loop.plan, B.run
        result = {}
        seen = {}
        try:
            with tempfile.TemporaryDirectory() as d:
                store = os.path.join(d, "claims.json")
                fake_plan = {"batch": [node("U1")], "deferred": [], "blocked": []}
                B.graph_loop.load = lambda *a, **k: {}
                B.graph_loop.plan = lambda *a, **k: fake_plan
                B.run = fake_run
                argv = ["--claims", store, "--cwd", d, "--tools", TOOLS_DIR,
                       "--owner", "owner-a", "--null-worker", "--plan", "x"]
                t = threading.Thread(
                    target=lambda: result.__setitem__("code", B.main(argv)))
                t.start()
                self.assertTrue(started.wait(5), "run() never started")
                # Well past the 0.3s lease: if nothing renews it, the claim
                # below reads abandoned.
                time.sleep(0.7)
                found, why = C.reconcile(store)
                seen["found"], seen["why"] = found, why
                allow_finish.set()
                t.join(timeout=5)
        finally:
            B.graph_loop.load, B.graph_loop.plan, B.run = old_load, old_plan, old_run
            if old_ttl is None:
                os.environ.pop(C.TTL_ENV_VAR, None)
            else:
                os.environ[C.TTL_ENV_VAR] = old_ttl

        found = seen["found"]
        self.assertIsNotNone(found, seen["why"])
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["status"], "in-flight",
                         "the claim read %r past its lease while the worker "
                         "was still running: renewal did not hold it"
                         % (found[0],))
        self.assertEqual(result.get("code"), 0)


class RenewalStopsWhenTheWorkerFinishes(unittest.TestCase):
    """D5, second half. The background renewal must stop the moment run()
    (standing in for the worker batch) returns, so a claim whose owner then
    genuinely dies is reclaimable on the ordinary lease and dead-pid rules
    rather than kept alive forever by a thread nobody ever stopped."""

    def test_renewal_does_not_outlive_the_run_call(self):
        import threading
        import time
        old_ttl = os.environ.get(C.TTL_ENV_VAR)
        os.environ[C.TTL_ENV_VAR] = "0.2"
        calls = []
        lock = threading.Lock()
        real_renew_owned = C.renew_owned

        def counting_renew_owned(*a, **k):
            with lock:
                calls.append(1)
            return real_renew_owned(*a, **k)

        def fake_run(plan, parts, worker, cwd=None, max_attempts=3,
                    max_in_flight=None, lanes=None):
            time.sleep(0.5)  # several renewal intervals (0.1s each)
            return {"dispatched": [{"id": "U1", "verdict": "PASS",
                                    "scope": {"verdict": "OK"}}],
                   "isolation": {}}

        old_load, old_plan, old_run = B.graph_loop.load, B.graph_loop.plan, B.run
        C.renew_owned = counting_renew_owned
        try:
            with tempfile.TemporaryDirectory() as d:
                store = os.path.join(d, "claims.json")
                fake_plan = {"batch": [node("U1")], "deferred": [], "blocked": []}
                B.graph_loop.load = lambda *a, **k: {}
                B.graph_loop.plan = lambda *a, **k: fake_plan
                B.run = fake_run
                argv = ["--claims", store, "--cwd", d, "--tools", TOOLS_DIR,
                       "--owner", "owner-b", "--null-worker", "--plan", "x"]
                code = B.main(argv)
                self.assertEqual(code, 0)
                with lock:
                    count_at_return = len(calls)
                self.assertGreater(count_at_return, 0,
                                   "renewal never ran during the slow batch")
                time.sleep(0.5)  # long enough for 2+ more cycles if still looping
                with lock:
                    count_after_wait = len(calls)
        finally:
            C.renew_owned = real_renew_owned
            B.graph_loop.load, B.graph_loop.plan, B.run = old_load, old_plan, old_run
            if old_ttl is None:
                os.environ.pop(C.TTL_ENV_VAR, None)
            else:
                os.environ[C.TTL_ENV_VAR] = old_ttl

        self.assertEqual(count_after_wait, count_at_return,
                         "renewal kept calling renew_owned after run() (the "
                         "worker) had already finished: %d calls by the time "
                         "main() returned, %d after a further wait"
                         % (count_at_return, count_after_wait))


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


class TheLaneBranchNameHasOneImplementation(unittest.TestCase):
    """C6 REPAIR consolidation round: worktree_lane.branch_for is now the
    ONE place that turns a unit id into a lane branch name. Before this,
    loop_bridge.main() carried its own byte-for-byte copy of the same
    character rule (the exact dict comprehension a 2026-09-09 adversarial
    review flagged) and integrate._lane_worktree_path_by_slug derived its
    slug by slicing whatever branch string it was handed rather than
    asking worktree_lane. This pins that all three surfaces still agree,
    on a unit id that actually exercises the rule (a slash and a colon,
    characters the sanitizer must fold to '-')."""

    UID = "some/odd:id"

    def test_loop_bridge_lane_branches_matches_worktree_lane_branch_for(self):
        """B._lane_branches is the exact call loop_bridge.main() makes to
        build the lane_branches dict integrate() and _reclaim_unmerged_lanes
        both consume; this is that real code path, not a reimplementation
        copied into the test for comparison."""
        import worktree_lane as W
        iso = {"lanes": {self.UID: "/does/not/matter"}}
        got = B._lane_branches(iso)
        self.assertEqual(got[self.UID], W.branch_for(self.UID))

    def test_integrates_stray_lookup_finds_the_directory_worktree_lane_built(self):
        """The real regression this consolidation guards against: a lane
        whose `checkout -b` failed (acquire() returns branch=None, a real
        detached worktree left on disk under the SANITIZED unit id) must
        still be found by integrate.cleanup_lane's fallback lookup, even
        for a unit id with characters the sanitizer has to fold. Before
        this consolidation that fallback re-derived its own slug by
        slicing the branch string loop_bridge computed; now it asks
        worktree_lane.branch_for directly for the same unit id
        cleanup_lane already carries."""
        import worktree_lane as W
        import integrate as I
        import subprocess as sp
        repo = tempfile.mkdtemp(prefix="c7-sanitizer-repo-")
        run = lambda *a: sp.run(["git"] + list(a), cwd=repo,
                                capture_output=True, text=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "a@b.c")
        run("config", "user.name", "t")
        with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        real = sp.run

        lane_root = tempfile.mkdtemp(prefix="c7-sanitizer-lanes-")
        expected_branch = W.branch_for(self.UID)
        lane_path = os.path.join(lane_root, expected_branch[len("lane/"):])

        def checkout_b_fails(cmd, **kw):
            if "checkout" in cmd and "-b" in cmd:
                class _F:
                    returncode, stdout, stderr = (
                        1, "", "monkeypatched checkout -b failure")
                return _F()
            if "--git-dir" in cmd:
                return real(cmd, capture_output=True, text=True, cwd=lane_path)
            return real(cmd, capture_output=True, text=True, cwd=repo)

        path, branch, problem = W.acquire(repo, self.UID, root=lane_root,
                                          runner=checkout_b_fails)
        self.assertIsNone(branch, "the scenario needs acquire's own "
                                  "branch=None path: %r" % problem)
        self.assertTrue(os.path.isdir(path))

        # loop_bridge's OWN computed name for this unit id: the exact value
        # B._lane_branches (and therefore main()) would hand cleanup_lane,
        # never acquire()'s real (None) return.
        removed, detail = I.cleanup_lane(repo, expected_branch, self.UID)
        self.assertFalse(removed,
                         "a worktree never checked out to its own branch "
                         "must be retained, not reported removed: %s"
                         % detail)
        self.assertIn("found by directory name instead", detail)
        self.assertTrue(os.path.isdir(path),
                        "the stray worktree the by-slug lookup found must "
                        "not have been touched")

    def test_crash_orphaned_claim_never_re_derives_a_branch_name(self):
        """C12 (2026-09-09 adversarial review, round 3): the reviewer
        named worktree_lane._crash_orphaned_claim as a possible second
        re-derivation of the lane branch name, hedged "if it re-derives".
        Inspected: it takes only unit_id, reads the journal by unit_id
        alone, and builds no branch string of any kind. Pinned by source
        rather than trusted from memory, so an edit that starts
        concatenating BRANCH_PREFIX or repeating the sanitizer's
        character rule inside it is caught here."""
        import inspect
        import worktree_lane as W
        src = inspect.getsource(W._crash_orphaned_claim)
        self.assertNotIn("BRANCH_PREFIX", src)
        self.assertNotIn("isalnum", src)

    def test_loop_bridge_builds_no_lane_branch_name_of_its_own(self):
        """C12: the reviewer's other named surface. B._lane_branches
        (pinned above as byte-for-byte worktree_lane.branch_for) is
        loop_bridge.py's only lane-branch construction; this confirms no
        OTHER spot in the file still concatenates the lane prefix onto a
        unit id by hand instead of calling branch_for."""
        with open(B.__file__, encoding="utf-8") as fh:
            src = fh.read()
        offending = [line for line in src.splitlines()
                    if ('"lane/" +' in line or "'lane/' +" in line
                        or '"lane/"+' in line or "'lane/'+" in line)]
        self.assertEqual(offending, [], offending)


class OrphanedLanesAreRetiredWhenIsolationFails(unittest.TestCase):
    """CONT-0, U3: found live -- when worktree_lane.Lanes.isolated is False
    for a batch (even one unit's lane failing turns off the whole batch's
    isolation, by W5's fail-closed rule), the integrate() call in main()
    never ran for ANY unit in that batch, so it never reached cleanup_lane
    for the lanes that DID get created. Nothing else in this file's own
    flow ever tears those down (worktree_lane.Lanes.release_all exists but
    nothing calls it), so they were left on disk forever.
    _reclaim_unmerged_lanes is the fix: routed from main() whenever
    lane_branches is non-empty and the batch could not integrate."""

    def _repo(self):
        import subprocess as sp
        d = tempfile.mkdtemp(prefix="orphan-lane-canon-")
        run = lambda *a: sp.run(["git"] + list(a), cwd=d, capture_output=True,
                                text=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "a@b.c")
        run("config", "user.name", "t")
        with open(os.path.join(d, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        return d

    def test_a_clean_never_integrated_lane_is_removed_when_routed(self):
        """The mechanism, proven directly on disk: a lane freshly acquired
        and never committed to is clean and its branch tip equals HEAD
        (its own ancestor), so cleanup_lane's own rule says remove it."""
        import worktree_lane as W
        import subprocess as sp
        repo = self._repo()
        path, branch, problem = W.acquire(repo, "U1")
        self.assertTrue(path, problem)
        self.assertTrue(os.path.isdir(path))
        B._reclaim_unmerged_lanes(repo, {"U1": branch}, "test reason")
        self.assertFalse(os.path.isdir(path))
        r = sp.run(["git", "rev-parse", "--verify", "--quiet",
                   "refs/heads/" + branch], cwd=repo, capture_output=True,
                  text=True)
        self.assertNotEqual(r.returncode, 0, "the branch should be gone too")

    def test_a_lane_holding_real_uncommitted_work_is_retained_not_lost(self):
        """The safety property this routing depends on: cleanup_lane never
        discards a lane carrying work nobody has looked at."""
        import worktree_lane as W
        repo = self._repo()
        path, branch, problem = W.acquire(repo, "U2")
        self.assertTrue(path, problem)
        with open(os.path.join(path, "unsaved.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("a worker's real, unmerged output\n")
        B._reclaim_unmerged_lanes(repo, {"U2": branch}, "test reason")
        self.assertTrue(os.path.isdir(path),
                        "a lane with real uncommitted work must survive an "
                        "unintegrated batch's teardown")

    def test_a_lane_that_never_got_its_own_branch_is_retained_not_skipped(self):
        """REPAIR C6 (2026-09-09 adversarial review of lane/continuity,
        round 2): lane_branches (this module's own CONT-0 U3 comment,
        right above the call site) is computed UNCONDITIONALLY from the
        unit id, never from worktree_lane.acquire()'s real return, so a
        unit whose `checkout -b` failed (acquire() then returns
        branch=None, leaving a real, detached, still-registered worktree
        behind) still gets routed through cleanup_lane with the
        SANITIZED branch name here, exactly like every other lane in the
        batch. Before REPAIR C6 this read as nothing left and reported
        removed; this proves _reclaim_unmerged_lanes still routes it
        (never filters it out beforehand) and that cleanup_lane itself
        now retains it rather than losing it silently."""
        import worktree_lane as W
        import subprocess as sp
        repo = self._repo()
        real = sp.run

        # worktree_lane._git() never forwards `cwd` to a caller-supplied
        # runner, so this routes each call by content: "checkout -b" is
        # faked (the scenario), the breadcrumb's "rev-parse --git-dir"
        # runs inside the lane path, everything else runs in `repo`.
        lane_root = tempfile.mkdtemp(prefix="c6-loop-bridge-root-")
        lane_path = os.path.join(lane_root, "U3")

        def checkout_b_fails(cmd, **kw):
            if "checkout" in cmd and "-b" in cmd:
                class _F:
                    returncode, stdout, stderr = (
                        1, "", "monkeypatched checkout -b failure")
                return _F()
            if "--git-dir" in cmd:
                return real(cmd, capture_output=True, text=True, cwd=lane_path)
            return real(cmd, capture_output=True, text=True, cwd=repo)

        path, branch, problem = W.acquire(repo, "U3", root=lane_root,
                                          runner=checkout_b_fails)
        self.assertIsNone(branch, "the scenario needs acquire's own "
                                  "branch=None path: %r" % problem)
        self.assertTrue(os.path.isdir(path))

        # loop_bridge's own computed name, never acquire()'s real (None)
        # return: this is the exact dict shape main() builds.
        B._reclaim_unmerged_lanes(repo, {"U3": "lane/U3"}, "test reason")

        self.assertTrue(os.path.isdir(path),
                        "a worktree that was never checked out to its own "
                        "branch must not be reported gone: it is still "
                        "real, on disk, and still registered with git")
        r = sp.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                  capture_output=True, text=True)
        self.assertIn(os.path.realpath(path),
                     [os.path.realpath(l[len("worktree "):])
                      for l in r.stdout.splitlines()
                      if l.startswith("worktree ")],
                     "git's own registration for it must survive too")

    def test_no_lanes_and_no_cwd_are_both_a_no_op(self):
        """Never touches disk when there is nothing to reclaim."""
        B._reclaim_unmerged_lanes(None, {"U1": "lane/U1"}, "no cwd")
        B._reclaim_unmerged_lanes("/some/repo", {}, "no lanes")


class AMachineWideRefusalIsAnAlertAndADependencyWaitIsNot(unittest.TestCase):
    """2026-08-31: eleven units ready, none claimed, every refusal reading 'no
    free slot: capacity is 0' because free disk was under the floor. The
    scheduler was correct and said so per unit, but the round READ as a normal
    quiet one, so it sat for a day until the founder asked why. The alert
    exists for that, and the whole difficulty is not crying wolf: a round that
    dispatches nothing because units are waiting on each other is the plan
    working."""

    @staticmethod
    def _n(i):
        return {"id": i}

    def test_a_pure_dependency_wait_is_silent(self):
        plan = {"batch": [], "deferred": [],
                "blocked": [(self._n("M6"), ["M5"]), (self._n("M7"), ["M6"])]}
        self.assertEqual(B.machine_wide_refusal(plan), "")

    def test_a_shared_machine_reason_alerts_and_quotes_that_reason(self):
        plan = {"batch": [],
                "deferred": [(self._n("M5"), "no free slot: capacity is 0")],
                "blocked": []}
        msg = B.machine_wide_refusal(plan)
        self.assertIn("capacity is 0", msg)
        self.assertIn("machine refusing", msg)

    def test_a_machine_reason_mixed_with_dependency_waits_still_alerts(self):
        plan = {"batch": [],
                "deferred": [(self._n("E1"), "no free slot: capacity is 0")],
                "blocked": [(self._n("E7"), ["E1"])]}
        self.assertNotEqual(B.machine_wide_refusal(plan), "")

    def test_any_dispatched_work_is_never_an_alert(self):
        plan = {"batch": [self._n("M5")],
                "deferred": [(self._n("E1"), "no free slot: capacity is 0")],
                "blocked": []}
        self.assertEqual(B.machine_wide_refusal(plan), "")

    def test_an_empty_plan_says_nothing(self):
        self.assertEqual(
            B.machine_wide_refusal({"batch": [], "deferred": [], "blocked": []}), "")

    def test_two_different_machine_reasons_are_both_named(self):
        plan = {"batch": [], "blocked": [],
                "deferred": [(self._n("A"), "no free slot: capacity is 0"),
                             (self._n("B"), "held elsewhere by owner x")]}
        msg = B.machine_wide_refusal(plan)
        self.assertIn("capacity is 0", msg)
        self.assertIn("held elsewhere", msg)


class ALaneWithNoBranchIsRefusedNotGuessed(unittest.TestCase):
    """H2: a branch a lane never actually got must never be reconstructed
    from the unit id, because a guessed name can collide with a stale
    branch of the same name left by an abandoned attempt."""

    def test_a_missing_branch_is_refused_with_no_data(self):
        isolation = {"isolated": True, "branches": {"A": "lane/A", "B": None}}
        branches, refused = B.integrable_branches(isolation)
        self.assertEqual(branches, {"A": "lane/A"})
        self.assertIn("B", refused)
        self.assertTrue(refused["B"].startswith("NO-DATA:"))

    def test_an_isolation_record_with_no_branches_map_yields_nothing(self):
        """An older or absent isolation shape must never fall back to
        reconstructing names from the unit ids it does carry."""
        isolation = {"isolated": True, "lanes": {"A": "/tmp/lane-a"}}
        branches, refused = B.integrable_branches(isolation)
        self.assertEqual(branches, {})
        self.assertEqual(refused, {})

    def test_an_empty_isolation_yields_nothing(self):
        branches, refused = B.integrable_branches({})
        self.assertEqual(branches, {})
        self.assertEqual(refused, {})

    def test_run_reports_the_branch_each_lane_actually_got(self):
        """The isolation record's own branches map, not a reconstruction,
        is what a caller downstream must be able to read."""
        class FakeLanes(object):
            isolated = True
            lanes = {"A": {"path": "/tmp/lane-a", "branch": "lane/A"},
                     "B": {"path": "/tmp/lane-b", "branch": None}}

            def why(self):
                return ""

            def safe_concurrency(self, requested):
                return requested

            def path_for(self, uid):
                return self.lanes[uid]["path"]

        plan = {"batch": [node("A"), node("B")], "deferred": [], "blocked": []}
        outcome = B.run(plan, parts(), Worker(), cwd=None, lanes=FakeLanes())
        self.assertEqual(outcome["isolation"]["branches"],
                         {"A": "lane/A", "B": None})


class RollingDispatchIsPureOrchestration(unittest.TestCase):
    """H4: rolling_dispatch() is PLAN, START, WAIT, INTEGRATE with everything
    injected, so its guarantees test without git, models or sleeping. It is
    not wired into main() or run() by this unit; these tests exercise the
    seam directly."""

    def test_a_dependent_starts_while_an_unrelated_slow_sibling_is_still_live(self):
        """Assert on the LIVE SET at the dependent's admission, not on wall
        clock: a timing assertion would pass even on a wave system where the
        dependent merely starts later."""
        live_view = {}
        admitted_while = {}
        ready_map = {frozenset(): ["SLOW"], frozenset(["SLOW"]): ["DEP"]}

        def plan_ready(live_ids):
            return list(ready_map.get(frozenset(live_ids), []))

        def start(uid):
            if uid == "DEP":
                admitted_while["DEP"] = set(live_view.get("live", set()))
            return uid

        order = iter([("SLOW", "slow-result"), ("DEP", "dep-result")])

        def wait_any(handles):
            return next(order)

        def integrate(unit, result):
            return {"id": unit["id"], "result": result}

        records = B.rolling_dispatch(plan_ready, start, wait_any, integrate,
                                     cap=2, live_view=live_view)
        self.assertIn("SLOW", admitted_while["DEP"])
        self.assertEqual(sorted(r["id"] for r in records), ["DEP", "SLOW"])

    def test_the_cap_is_never_exceeded(self):
        concurrent, max_seen = [0], [0]

        def plan_ready(live_ids):
            return ["A", "B", "C", "D"]

        def start(uid):
            concurrent[0] += 1
            max_seen[0] = max(max_seen[0], concurrent[0])
            return uid

        def wait_any(handles):
            handle = handles[0]
            concurrent[0] -= 1
            return handle, "ok"

        def integrate(unit, result):
            return {"id": unit["id"]}

        records = B.rolling_dispatch(plan_ready, start, wait_any, integrate, cap=2)
        self.assertLessEqual(max_seen[0], 2)
        self.assertEqual(sorted(r["id"] for r in records), ["A", "B", "C", "D"])

    def test_integration_is_never_concurrent(self):
        in_integrate = [False]

        def plan_ready(live_ids):
            return ["A", "B"]

        def start(uid):
            return uid

        def wait_any(handles):
            return handles[0], "ok"

        def integrate(unit, result):
            self.assertFalse(in_integrate[0], "integrate re-entered")
            in_integrate[0] = True
            try:
                return {"id": unit["id"]}
            finally:
                in_integrate[0] = False

        records = B.rolling_dispatch(plan_ready, start, wait_any, integrate, cap=2)
        self.assertEqual(len(records), 2)

    def test_the_planner_is_told_what_is_live(self):
        seen = []
        ready_map = {frozenset(): ["A"], frozenset(["A"]): ["B"]}

        def plan_ready(live_ids):
            seen.append(frozenset(live_ids))
            return list(ready_map.get(frozenset(live_ids), []))

        def start(uid):
            return uid

        order = iter([("A", "ok"), ("B", "ok")])

        def wait_any(handles):
            return next(order)

        def integrate(unit, result):
            return {"id": unit["id"]}

        B.rolling_dispatch(plan_ready, start, wait_any, integrate, cap=2)
        self.assertIn(frozenset(["A"]), seen)

    def test_no_unit_is_dispatched_twice(self):
        """A planner that keeps re-offering a unit while it is already live
        (its own bug) must not fool this loop into starting it again."""
        starts = []
        ready_map = {frozenset(): ["A"], frozenset(["A"]): ["A"]}

        def plan_ready(live_ids):
            return list(ready_map.get(frozenset(live_ids), []))

        def start(uid):
            starts.append(uid)
            return uid

        def wait_any(handles):
            return handles[0], "ok"

        def integrate(unit, result):
            return {"id": unit["id"]}

        B.rolling_dispatch(plan_ready, start, wait_any, integrate, cap=3)
        self.assertEqual(starts.count("A"), 1)

    def test_a_blocked_graph_terminates_rather_than_spinning(self):
        calls = [0]

        def plan_ready(live_ids):
            calls[0] += 1
            return []

        def start(uid):
            self.fail("start must never be called on a blocked graph")

        def wait_any(handles):
            self.fail("wait_any must never be called with nothing live")

        def integrate(unit, result):
            self.fail("integrate must never be called")

        records = B.rolling_dispatch(plan_ready, start, wait_any, integrate, cap=2)
        self.assertEqual(records, [])
        self.assertEqual(calls[0], 1)


class TheBreakerOpensOnConsecutiveAccountLimitFailures(unittest.TestCase):
    """SR-4: the recorded night where six lanes died together on one account
    limit, each burning its own three attempts before anyone noticed it was
    the same failure six times over, not six different ones."""

    def setUp(self):
        # Hygiene, not a requirement: every test here injects its own
        # Breaker, but reset the shared module singleton anyway so a stray
        # rate_limit/overloaded marker in some unrelated test's fixture text
        # can never leak a consecutive count across test order.
        B._BREAKER = B.Breaker()

    def _breaker(self, open_after=3, cooldown=120, max_failovers=10):
        self.sleeps = []
        return B.Breaker(open_after=open_after, cooldown=cooldown,
                         max_failovers=max_failovers,
                         clock=lambda: 1000.0,
                         sleep=lambda s: self.sleeps.append(s))

    def test_one_pause_and_no_burned_attempts_after_it_opens(self):
        class RateLimited(object):
            def __init__(self):
                self.seen = []

            def run(self, unit, cwd=None):
                self.seen.append(unit["unit_id"])
                return {"worker_claim": "", "artifacts": [],
                        "status": "unavailable",
                        "cost": {"tokens": 0, "minutes": 0},
                        "note": "exited 1: account limit "
                                "(failure_class=rate_limit)"}

        worker = RateLimited()
        brk = self._breaker()
        plan = {"batch": [node(n) for n in "ABCDEF"],
               "deferred": [], "blocked": []}
        got = B.run(plan, parts(), worker, max_in_flight=1, breaker=brk)

        self.assertEqual(self.sleeps, [120])
        self.assertEqual(worker.seen, ["A", "B", "C"])
        refused = [r for r in got["dispatched"] if r["id"] in "DEF"]
        self.assertEqual(len(refused), 3)
        for r in refused:
            self.assertIn("breaker", r["reason"])
            self.assertEqual(r["verdict"], "NO-DATA")

    def test_a_success_between_two_failures_resets_the_counter(self):
        class Mixed(object):
            def __init__(self, classes):
                self.classes = list(classes)
                self.seen = []

            def run(self, unit, cwd=None):
                self.seen.append(unit["unit_id"])
                cls = self.classes.pop(0)
                if cls is None:
                    return {"worker_claim": "ok", "artifacts": [],
                            "status": "returned",
                            "cost": {"tokens": 0, "minutes": 0}}
                return {"worker_claim": "", "artifacts": [],
                        "status": "unavailable",
                        "cost": {"tokens": 0, "minutes": 0},
                        "note": "exited 1 (failure_class=%s)" % cls}

        worker = Mixed(["rate_limit", "rate_limit", None,
                        "rate_limit", "rate_limit"])
        brk = self._breaker()
        plan = {"batch": [node(n) for n in "ABCDE"],
               "deferred": [], "blocked": []}
        got = B.run(plan, parts(), worker, max_in_flight=1, breaker=brk)

        self.assertEqual(self.sleeps, [])
        self.assertEqual(worker.seen, ["A", "B", "C", "D", "E"])
        self.assertEqual(len(got["dispatched"]), 5)

    def test_the_run_wide_cap_stops_admission_for_good(self):
        # open_after=1: a single rate_limit failure opens or re-opens it.
        # cooldown=0: the fixed fake clock reads the cooldown as elapsed
        # immediately, so the very next admit() is the one half-open trial.
        brk = self._breaker(open_after=1, cooldown=0, max_failovers=2)
        self.assertTrue(brk.admit())
        brk.record("rate_limit")        # opens: failover 1/2
        self.assertTrue(brk.admit())    # the one half-open trial
        brk.record("rate_limit")        # the trial failed: failover 2/2, DEAD
        self.assertFalse(brk.admit())
        self.assertIn("for good", brk.refusal_reason())

        class NeverAsked(object):
            def run(self, unit, cwd=None):
                raise AssertionError(
                    "must never be dispatched once the breaker is dead")

        plan = {"batch": [node("Z")], "deferred": [], "blocked": []}
        got = B.run(plan, parts(), NeverAsked(), max_in_flight=1, breaker=brk)
        rec = got["dispatched"][0]
        self.assertIn("for good", rec["reason"])


class AFailedHalfOpenTrialReopensWithAFreshCooldown(unittest.TestCase):
    """SR-4: a half-open trial that fails again must reopen the breaker on
    a FRESH cooldown. Before the fix, with the default open_after=3, the
    failed trial only counted one consecutive failure, the breaker kept its
    OLD opened_at, and the very next admit() granted another trial at once,
    so a persistent account limit kept spending attempts. The clock here
    MOVES: the test advances it by hand, and sleep only records the pause."""

    def setUp(self):
        self.now = [1000.0]
        self.sleeps = []
        self.brk = B.Breaker(open_after=3, cooldown=120, max_failovers=10,
                             clock=lambda: self.now[0],
                             sleep=lambda s: self.sleeps.append(s))
        for _ in range(3):
            self.assertTrue(self.brk.admit())
            self.brk.record("rate_limit")
        self.assertEqual(self.brk.state, "open")
        self.assertFalse(self.brk.admit())
        self.now[0] += 121  # past the first cooldown
        self.assertTrue(self.brk.admit())   # the one half-open trial
        self.assertFalse(self.brk.admit())  # and only one

    def test_a_failed_trial_waits_a_fresh_cooldown_from_its_own_failure(self):
        self.now[0] += 5  # the trial fails at t=1126
        self.brk.record("rate_limit")
        self.assertEqual(self.brk.state, "open")
        self.assertFalse(self.brk.admit())
        self.now[0] += 119  # 119s after the trial failed: still cooling
        self.assertFalse(self.brk.admit())
        self.now[0] += 2    # 121s after the trial failed: one trial again
        self.assertTrue(self.brk.admit())
        self.assertFalse(self.brk.admit())
        self.assertEqual(self.sleeps, [120, 120])
        self.assertEqual(self.brk.failover_count, 2)

    def test_a_successful_trial_closes_the_breaker(self):
        self.brk.record("other")
        self.assertEqual(self.brk.state, "closed")
        for _ in range(3):
            self.assertTrue(self.brk.admit())
        self.assertEqual(self.sleeps, [120])


class TheBreakerAlsoGatesRollingDispatch(unittest.TestCase):
    """SR-4, the rolling half: the same breaker guards rolling_dispatch(),
    the second of the two paths the brief names by name."""

    def test_an_open_breaker_refuses_without_ever_calling_start(self):
        brk = B.Breaker(open_after=1, cooldown=999,
                        clock=lambda: 0.0, sleep=lambda s: None)
        brk.record("rate_limit")  # one failure opens it (open_after=1)

        def plan_ready(live_ids):
            return ["A"]

        def start(uid):
            raise AssertionError("start must never be called while the "
                                 "breaker is open")

        def wait_any(handles):
            raise AssertionError("wait_any must never be called with "
                                 "nothing live")

        def integrate(unit, result):
            raise AssertionError("integrate must never be called")

        records = B.rolling_dispatch(plan_ready, start, wait_any, integrate,
                                     cap=2, breaker=brk)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "A")
        self.assertIn("breaker", records[0]["record"]["reason"])


if __name__ == "__main__":
    unittest.main()
