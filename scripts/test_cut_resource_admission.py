"""CUT2.e: the machine ledger is the one admission authority for the cut's nested jobs.

Baseline (1.1.9 records): step 2b took 2480 s with 20 NO-DATA rows because a clean baseline missed its 495 s
deadline, seven pool workers at load 14.6. The perturbation pool now sizes a wave by what its workers will DEMAND
(cores, memory, scratch disk), leased from machine_reservation's ledger and fitted by load_reservation.fit_leases,
not by a load reading taken before they ramp up.

Every lock, ledger and folder here is a fixture under the scratch root; nothing reads or touches the real machine
ledger or the live heavy_slot lock. Each test isolates ONE condition, so each guard dies under its own mutant
alone. The stop classes, the retry and the scratch reclaim are in test_cut_stop_classes.py.
Run: python3 scripts/test_cut_resource_admission.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import load_reservation as LR  # noqa: E402
import machine_reservation as MR  # noqa: E402
import perturb_pool as PP  # noqa: E402

DEMAND = {"cores": 3.0, "mem_gib": 1.0, "disk_gib": 0.25}
BIG = {"cores": 99, "mem_gib": 99, "disk_gib": 99}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="cut-admission-", dir=export_public.scratch_root()))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ledger = os.path.join(self.dir, "leases.json")

    def seat(self, demand=None, ncpu=8, mem_gib=16.0, free=100.0, calls=None):
        """A Leases over fixture readings: cores 6 (8 less the reserve), memory 10, disk 85 unless one is changed."""
        def reading(path):
            if calls is not None:
                calls.append(path)
            return free
        return PP.Leases(path=self.ledger, demand=demand or DEMAND, root=self.dir, floor_gib=15,
                         ncpu=ncpu, mem_gib=mem_gib, free=reading)

    def hold(self, name, demand):
        got, why = MR.lease(self.ledger, name, demand, BIG, 3600)
        self.assertEqual(why, "")
        return got

    def make_copy(self, src, dest):
        os.makedirs(dest)
        open(dest + PP.MARKER_SUFFIX, "w").close()


class FakeProc(object):
    returncode = 0

    def __init__(self, on_wait=None):
        self.on_wait = on_wait

    def communicate(self):
        if self.on_wait:
            self.on_wait()
        return b"", b""


class NestedLease(Fixture):
    """The entry point perturb_pool uses to settle its worker count: settled_workers."""

    def settle(self, seat, requested=7, wait_s=20, slept=None, on_sleep=None):
        t = [0.0]

        def sleep(s):
            t[0] += s
            if slept is not None:
                slept.append(s)
            if on_sleep:
                on_sleep()
        return PP.settled_workers(requested, wait_s=wait_s, cap=lambda r, load1=None: r, sleep=sleep,
                                  clock=lambda: t[0], lease=seat)

    def test_the_premise_an_idle_load_reading_alone_admits_all_seven(self):
        self.assertEqual(PP.capped_workers(7, load1=0.0, ncpu=8), 7)

    def test_nested_suite_demand_over_the_lease_admits_only_what_fits(self):
        # idle load, 6 leasable cores, 3 cores of nested demand per worker: 2 fit, not the 7 the load reading allows
        self.assertEqual(self.settle(self.seat()), 2)

    def test_the_production_default_asks_the_ledger_when_no_cap_is_supplied(self):
        with mock.patch.object(PP, "default_leases", lambda: self.seat()), \
                mock.patch.object(PP, "capped_workers", lambda requested, **kw: requested):
            self.assertEqual(PP.settled_workers(7), 2)

    def test_a_cap_the_caller_supplies_owns_the_count_and_the_ledger_is_not_asked(self):
        with mock.patch.object(PP, "default_leases", side_effect=AssertionError("the ledger was asked")):
            self.assertEqual(PP.settled_workers(4, wait_s=0, cap=lambda requested, load1=None: 4), 4)

    def test_another_jobs_live_lease_takes_its_share_and_the_wait_is_bounded(self):
        self.hold("other-job", DEMAND)
        slept = []
        self.assertEqual(self.settle(self.seat(), wait_s=20, slept=slept), 1)   # 3 cores left: one fits
        self.assertEqual(sum(slept), 20, "the wait ends at its bound, not before and not after")

    def test_a_wait_recovers_when_the_other_job_ends(self):
        self.hold("other-job", {"cores": 6.0, "mem_gib": 1.0, "disk_gib": 0.25})
        slept = []
        n = self.settle(self.seat(), wait_s=60, slept=slept, on_sleep=lambda: MR.end_lease(self.ledger, "other-job"))
        self.assertEqual(n, 2)
        self.assertEqual(len(slept), 1, "it stopped waiting the moment the count reached what an idle machine gives")

    def test_a_demand_no_machine_could_meet_still_gives_one_worker_not_zero(self):
        self.assertEqual(self.settle(self.seat(demand={"cores": 100.0, "mem_gib": 1.0, "disk_gib": 0.25}),
                                     wait_s=0), 1)

    def test_memory_alone_binds(self):
        self.assertEqual(self.settle(self.seat(demand={"cores": 1.0, "mem_gib": 4.0, "disk_gib": 0.25})), 2)

    def test_scratch_disk_alone_binds(self):
        self.assertEqual(self.settle(self.seat(demand={"cores": 1.0, "mem_gib": 0.5, "disk_gib": 10.0}, free=36.0)), 2)

    def test_the_disk_is_read_on_the_scratch_filesystem_not_on_some_other_path(self):
        calls = []
        self.settle(self.seat(calls=calls))
        self.assertEqual(set(calls), {self.dir})

    def test_a_corrupt_ledger_gives_one_worker_and_waits_for_nothing(self):
        with open(self.ledger, "w") as fh:
            fh.write("{not json")
        slept = []
        self.assertEqual(self.settle(self.seat(), wait_s=60, slept=slept), 1)
        self.assertEqual(slept, [])

    def test_an_unreadable_disk_reading_gives_one_worker_and_waits_for_nothing(self):
        slept = []
        self.assertEqual(self.settle(self.seat(free=float("nan")), wait_s=60, slept=slept), 1)
        self.assertEqual(slept, [])

    def test_a_dead_holders_lease_does_not_count(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.hold("dead-job", {"cores": 6.0, "mem_gib": 1.0, "disk_gib": 0.25})
        with open(self.ledger) as fh:
            data = json.load(fh)
        data["leases"]["dead-job"]["pid"] = gone.pid
        with open(self.ledger, "w") as fh:
            json.dump(data, fh)
        self.assertEqual(self.settle(self.seat(), wait_s=0), 2)

    def run_pool(self, rows, seat, workers, say=None):
        live, peak = [0], [0]
        settle = PP.settled_workers

        def spawn(argv, env):
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            with open(argv[4]) as fh, open(argv[6], "w") as out:
                json.dump([[f, s, True, "red"] for f, s in json.load(fh)], out)
            return FakeProc(lambda: live.__setitem__(0, live[0] - 1))
        base = tempfile.mkdtemp(prefix="run-", dir=self.dir)
        with mock.patch.object(PP, "pool_scratch_root", lambda: self.dir), \
                mock.patch.object(PP, "capped_workers", lambda requested, **kw: requested), \
                mock.patch.object(PP, "settled_workers", lambda w, **kw: settle(w, wait_s=0, **kw)):   # no real waiting
            out = PP.run_rows(rows, self.dir, 5, workers=workers, base=base, spawn=spawn, copier=self.make_copy,
                              cap=lambda requested, **kw: requested, say=say or (lambda text: None), leases=seat)
        return out, peak[0], base

    def test_the_pool_takes_a_lease_per_worker_and_never_runs_more_than_the_ledger_fits(self):
        rows = [("scripts/f%d.py" % i, "scripts/test_s%d.py" % i) for i in range(6)]
        out, peak, base = self.run_pool(rows, self.seat(), 6)
        self.assertEqual([(f, s) for f, s, _, _ in out], rows)
        self.assertEqual([v for _, _, v, _ in out], [True] * 6)
        self.assertLessEqual(peak, 2, "never more workers running than the ledger fitted")
        self.assertEqual(MR.leased(self.ledger), ({}, ""), "every lease was given back")
        self.assertEqual([e for e in os.listdir(base) if e.startswith("scratch")], [], "every scratch was reclaimed")

    def test_a_run_that_raises_still_gives_every_lease_back(self):
        rows = [("scripts/a.py", "scripts/test_a.py"), ("scripts/b.py", "scripts/test_b.py")]

        def boom():
            raise RuntimeError("the worker pipe broke")
        base = tempfile.mkdtemp(prefix="run-", dir=self.dir)
        with mock.patch.object(PP, "pool_scratch_root", lambda: self.dir), \
                mock.patch.object(PP, "capped_workers", lambda requested, **kw: requested), \
                self.assertRaises(RuntimeError):
            PP.run_rows(rows, self.dir, 5, workers=2, base=base, spawn=lambda argv, env: FakeProc(boom),
                        copier=self.make_copy, cap=lambda requested, **kw: requested, say=lambda text: None,
                        leases=self.seat(demand={"cores": 1.0, "mem_gib": 1.0, "disk_gib": 0.25}))
        self.assertEqual(MR.leased(self.ledger), ({}, ""))

    def test_a_spawn_that_raises_still_gives_its_lease_back(self):
        def no_spawn(argv, env):
            raise OSError("fork failed")
        base = tempfile.mkdtemp(prefix="run-", dir=self.dir)
        with mock.patch.object(PP, "pool_scratch_root", lambda: self.dir), \
                mock.patch.object(PP, "capped_workers", lambda requested, **kw: requested), \
                self.assertRaises(OSError):
            PP.run_rows([("scripts/a.py", "scripts/test_a.py")], self.dir, 5, workers=1, base=base, spawn=no_spawn,
                        copier=self.make_copy, cap=lambda requested, **kw: requested, say=lambda text: None,
                        leases=self.seat())
        self.assertEqual(MR.leased(self.ledger), ({}, ""))

    def test_a_worker_that_ends_hands_its_share_to_the_next_while_the_run_goes_on(self):
        said = []
        seat = self.seat(demand={"cores": 6.0, "mem_gib": 1.0, "disk_gib": 0.25})   # room for one worker at a time
        rows = [("scripts/f%d.py" % i, "scripts/test_s%d.py" % i) for i in range(3)]
        out, peak, _base = self.run_pool(rows, seat, 3, say=said.append)
        self.assertEqual([v for _, _, v, _ in out], [True] * 3)
        self.assertEqual(peak, 1)
        self.assertFalse([t for t in said if "unleased" in t], "each next worker got the share the last one gave back")

    def test_an_unleased_slice_runs_when_nothing_runs_and_the_ledger_is_full(self):
        self.hold("other-job", {"cores": 6.0, "mem_gib": 1.0, "disk_gib": 0.25})
        said = []
        out, _peak, _base = self.run_pool([("scripts/a.py", "scripts/test_a.py")], self.seat(), 1, say=said.append)
        self.assertEqual(out[0][2], True, "a busy machine is not a verdict")
        self.assertTrue([t for t in said if "runs unleased" in t], said)


class TheLedger(Fixture):
    CAP = {"cores": 6.0, "mem_gib": 10.0, "disk_gib": 85.0}

    def lease(self, name, cores, ttl=100, clock=None, cap=None):
        return MR.lease(self.ledger, name, {"cores": cores}, cap or self.CAP, ttl, clock=clock)

    def test_a_lease_that_does_not_fit_is_a_capacity_stop_naming_the_resource(self):
        self.lease("a", 4.0)
        got, why = self.lease("b", 3.0)
        self.assertIsNone(got)
        self.assertTrue(why.startswith(MR.CAPACITY + ":"), why)
        self.assertIn("cores", why)

    def test_a_resource_with_no_stated_capacity_is_never_granted(self):
        got, why = MR.lease(self.ledger, "a", {"gpu": 1.0}, self.CAP, 100)
        self.assertIsNone(got)
        self.assertTrue(why.startswith(MR.NODATA), why)

    def test_a_bad_amount_is_refused_not_read_as_zero(self):
        for bad in (-1.0, float("nan"), float("inf"), True, "3"):
            got, why = self.lease("a", bad)
            self.assertIsNone(got, bad)
            self.assertFalse(why.startswith(MR.CAPACITY), bad)
        self.assertFalse(os.path.exists(self.ledger))

    def test_a_live_name_is_refused_but_a_dead_one_is_reclaimed(self):
        self.lease("a", 1.0, clock=lambda: 0.0)
        got, why = self.lease("a", 1.0, clock=lambda: 50.0)
        self.assertIsNone(got)
        self.assertIn("already leased", why)
        got, why = self.lease("a", 1.0, clock=lambda: 500.0)   # the same name, restarted after its lease ran out
        self.assertEqual(why, "")
        self.assertEqual(MR.leased(self.ledger, clock=lambda: 500.0)[0], {"cores": 1.0})

    def test_a_dead_pid_frees_its_share_before_the_ttl(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.lease("a", 6.0, ttl=3600)
        with open(self.ledger) as fh:
            data = json.load(fh)
        data["leases"]["a"]["pid"] = gone.pid
        with open(self.ledger, "w") as fh:
            json.dump(data, fh)
        self.assertEqual(MR.leased(self.ledger)[0], {})

    def test_the_holder_slot_and_the_ledger_leave_each_other_alone(self):
        MR.acquire(self.ledger, "gate", 100, clock=lambda: 0.0)
        self.lease("w", 1.0, clock=lambda: 0.0)
        with open(self.ledger) as fh:
            data = json.load(fh)
        self.assertEqual(data["holder"]["holder"], "gate")
        self.assertIn("w", data["leases"])
        MR.release(self.ledger, "gate", clock=lambda: 1.0)
        MR.end_lease(self.ledger, "w", clock=lambda: 1.0)
        with open(self.ledger) as fh:
            data = json.load(fh)
        self.assertIsNone(data["holder"])
        self.assertEqual(data["leases"], {})

    def test_ending_a_lease_removes_that_one_only_and_twice_is_harmless(self):
        self.lease("a", 1.0)
        self.lease("b", 2.0)
        self.assertEqual(MR.end_lease(self.ledger, "a")[1], "")
        self.assertEqual(MR.leased(self.ledger)[0], {"cores": 2.0})
        got, _why = MR.end_lease(self.ledger, "a")
        self.assertIsNone(got)
        self.assertEqual(MR.leased(self.ledger)[0], {"cores": 2.0})

    def test_a_corrupt_ledger_is_no_data_for_every_entry_point(self):
        for text in ("{not json", "", "[]", '{"leases": []}', '{"leases": {"a": 5}}'):
            with open(self.ledger, "w") as fh:
                fh.write(text)
            for call in (lambda: self.lease("a", 1.0), lambda: MR.end_lease(self.ledger, "a"),
                         lambda: MR.leased(self.ledger)):
                got, why = call()
                self.assertIsNone(got, text)
                self.assertTrue(why.startswith(MR.NODATA), (text, why))

    def test_bytes_that_are_not_text_are_no_data_for_every_entry_point(self):
        for blob in (b"\xff\xfe\x00{", b'{"leases": {"a": {"demand": {"cores": 1}}}}\xc3\x28'):
            with open(self.ledger, "wb") as fh:
                fh.write(blob)
            for call in (lambda: self.lease("a", 1.0), lambda: MR.end_lease(self.ledger, "a"),
                         lambda: MR.leased(self.ledger), lambda: MR.acquire(self.ledger, "h", 10)):
                got, why = call()
                self.assertIsNone(got, blob)
                self.assertTrue(why.startswith(MR.NODATA), (blob, why))

    def test_a_read_creates_no_folder_and_no_file(self):
        missing = os.path.join(self.dir, "nowhere", "leases.json")
        self.assertEqual(MR.leased(missing), ({}, ""))
        self.assertIsNone(MR.end_lease(missing, "a")[0])
        PP.Leases(path=missing, root=self.dir, floor_gib=15, ncpu=8, mem_gib=16.0, free=lambda p: 100.0).fit(7)
        self.assertEqual(os.listdir(self.dir), [])

    def test_odd_names_are_distinct_whole_names(self):
        names = ["-dash", "with space", "line break", "nel\u0085", "Case", "case", "../up", ""]
        for n in names[:-1]:
            got, why = self.lease(n, 0.5)
            self.assertEqual(why, "", n)
        self.assertEqual(MR.leased(self.ledger)[0], {"cores": 0.5 * (len(names) - 1)})
        self.assertIn("name", self.lease("", 0.5)[1])
        self.assertIn("already leased", self.lease("Case", 0.5)[1])
        self.assertEqual(MR.end_lease(self.ledger, "case")[1], "")
        self.assertIn("already leased", self.lease("Case", 0.5)[1], "ending 'case' did not end 'Case'")

    def test_a_ledger_reached_through_a_link_or_a_path_with_spaces_works(self):
        real = os.path.join(self.dir, "a dir with spaces")
        os.makedirs(real)
        link = os.path.join(self.dir, "link.json")
        os.symlink(os.path.join(real, "leases.json"), link)
        self.assertEqual(MR.lease(link, "a", {"cores": 1.0}, self.CAP, 100)[1], "")
        self.assertEqual(MR.leased(link)[0], {"cores": 1.0})   # ponytail: the link is replaced by a plain file on write; realpath is the upgrade

    def test_a_lock_held_past_its_bound_is_no_data_not_a_wait_for_ever(self):
        with MR._locked(self.ledger), mock.patch.object(MR, "LOCK_WAIT_S", 0.2):
            got, why = self.lease("a", 1.0)
        self.assertIsNone(got)
        self.assertTrue(why.startswith(MR.NODATA), why)

    def test_racing_processes_never_lease_more_than_the_capacity(self):
        code = ("import sys, time; sys.path.insert(0, %r); import machine_reservation as M\n"
                "g, w = M.lease(sys.argv[1], 'p' + sys.argv[2], {'cores': 1.0}, {'cores': 3.0}, 60)\n"
                "time.sleep(1.5) if g else None\nprint('GOT' if g else 'NO')" % HERE)
        procs = [subprocess.Popen([sys.executable, "-c", code, self.ledger, str(i)], stdout=subprocess.PIPE,
                                  text=True) for i in range(6)]
        got = sorted(p.communicate()[0].strip() for p in procs)
        self.assertEqual((got.count("GOT"), got.count("NO")), (3, 3), got)


class TheFit(unittest.TestCase):
    CAP = {"cores": 6.0, "mem_gib": 10.0, "disk_gib": 85.0}

    def test_the_binding_resource_is_named(self):
        got = LR.fit_leases(7, {"cores": 3.0, "mem_gib": 1.0}, self.CAP, {})
        self.assertEqual((got["workers"], got["limit"]), (2, "cores"))

    def test_a_request_below_every_limit_is_not_limited(self):
        got = LR.fit_leases(1, {"cores": 3.0}, self.CAP, {})
        self.assertEqual((got["workers"], got["limit"]), (1, None))

    def test_what_is_already_leased_is_taken_off(self):
        self.assertEqual(LR.fit_leases(7, {"cores": 2.0}, self.CAP, {"cores": 4.0})["workers"], 1)

    def test_a_ledger_that_could_not_be_read_fits_nothing(self):
        got = LR.fit_leases(7, {"cores": 2.0}, self.CAP, None)
        self.assertEqual(got["workers"], 0)
        self.assertTrue(got["problem"].startswith("NO-DATA"))

    def test_a_missing_or_bad_capacity_fits_nothing(self):
        for cap in ({}, {"cores": None}, {"cores": float("nan")}, {"cores": True}, {"cores": -1.0}):
            got = LR.fit_leases(7, {"cores": 2.0}, cap, {})
            self.assertEqual(got["workers"], 0, cap)
            self.assertTrue(got["problem"].startswith("NO-DATA"), cap)

    def test_zero_demand_for_a_resource_never_binds_it(self):
        self.assertEqual(LR.fit_leases(4, {"cores": 1.0, "mem_gib": 0}, {"cores": 6.0, "mem_gib": 0.0}, {})["workers"], 4)


def old_settled_workers(requested, wait_s=120, cap=None, sleep=None, clock=None):
    """settled_workers as it stood on 007b30574, copied verbatim (its capped_workers default is the module's own)."""
    import time
    cap = cap or PP.capped_workers
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    want = cap(requested, load1=0.0)
    n = cap(requested)
    end = clock() + wait_s
    while n < want and clock() < end:
        sleep(5)
        n = cap(requested)
    return n


class TheAdmissionSwitch(Fixture):
    """BROTHER_PERTURB_ADMISSION: unset is the lease, load is the old count, anything else refuses."""
    ENV = PP.ADMISSION_ENV
    run_pool = NestedLease.run_pool

    def scenario(self, loads, wait_s):
        """A fresh (cap, sleep, clock, slept) over a scripted load history, 8 cores."""
        loads, t, slept = list(loads), [0.0], []

        def cap(requested, load1=None):
            return PP.capped_workers(requested, load1=loads.pop(0) if load1 is None else load1, ncpu=8)

        def sleep(s):
            slept.append(s)
            t[0] += s
        return dict(wait_s=wait_s, cap=cap, sleep=sleep, clock=lambda: t[0]), slept

    SCENARIOS = [([0.5] * 60, 120), ([12.0, 9.0, 3.0] + [3.0] * 60, 120), ([12.0] * 60, 20), ([40.0] * 60, 0)]

    def test_unset_gives_the_lease_path(self):
        kw, _ = self.scenario(*self.SCENARIOS[0])
        self.assertEqual(PP.settled_workers(7, lease=self.seat(), env={}, **kw), 2)

    def test_an_empty_value_is_unset(self):
        kw, _ = self.scenario(*self.SCENARIOS[0])
        self.assertEqual(PP.settled_workers(7, lease=self.seat(), env={self.ENV: ""}, **kw), 2)

    def test_the_word_lease_gives_the_lease_path(self):
        kw, _ = self.scenario(*self.SCENARIOS[0])
        self.assertEqual(PP.settled_workers(7, lease=self.seat(), env={self.ENV: "lease"}, **kw), 2)

    def test_load_gives_exactly_the_old_count_and_the_old_waiting_for_the_same_inputs(self):
        for loads, wait_s in self.SCENARIOS:
            new_kw, new_slept = self.scenario(loads, wait_s)
            old_kw, old_slept = self.scenario(loads, wait_s)
            got = PP.settled_workers(7, lease=self.seat(), env={self.ENV: "load"}, **new_kw)
            self.assertEqual(got, old_settled_workers(7, **old_kw), (loads[:3], wait_s))
            self.assertEqual(new_slept, old_slept, (loads[:3], wait_s))

    def test_the_fixture_tells_the_two_paths_apart(self):
        kw, _ = self.scenario(*self.SCENARIOS[0])
        old_kw, _ = self.scenario(*self.SCENARIOS[0])
        self.assertEqual(old_settled_workers(7, **old_kw), 7)   # idle load: the old count is every worker asked for
        self.assertEqual(PP.settled_workers(7, lease=self.seat(), env={}, **kw), 2)

    def test_load_never_asks_the_ledger_whatever_lease_was_passed(self):
        class Asked(object):
            def fit(self, requested):
                raise AssertionError("the ledger was asked")
        kw, _ = self.scenario(*self.SCENARIOS[0])
        self.assertEqual(PP.settled_workers(7, lease=Asked(), env={self.ENV: "load"}, **kw), 7)
        with mock.patch.object(PP, "default_leases", side_effect=AssertionError("the default seat was built")):
            kw, _ = self.scenario(*self.SCENARIOS[0])
            self.assertEqual(PP.settled_workers(7, env={self.ENV: "load"}, **kw), 7)

    def test_an_unknown_value_refuses_naming_the_accepted_values_and_measures_nothing(self):
        def never(*a, **k):
            raise AssertionError("something was measured before the refusal")
        for bad in ("Load", "LOAD", "load ", " load", "lease ", "off", "none", "1", "true", "load,lease", "-load"):
            with self.assertRaises(PP.AdmissionRefused, msg=bad) as ctx:
                PP.settled_workers(7, cap=never, lease=self.seat(), env={self.ENV: bad})
            text = str(ctx.exception)
            self.assertTrue(text.startswith("NO-DATA:"), text)
            self.assertIn("accepted values: lease, load", text)
            self.assertIn(repr(bad), text)

    def test_the_real_environment_is_what_is_read_when_no_env_is_passed(self):
        kw, _ = self.scenario(*self.SCENARIOS[0])
        with mock.patch.dict(os.environ, {self.ENV: "sideways"}), self.assertRaises(PP.AdmissionRefused):
            PP.settled_workers(7, lease=self.seat(), **kw)

    def test_the_pool_in_load_mode_takes_no_lease_and_runs_the_old_wave(self):
        rows = [("scripts/f%d.py" % i, "scripts/test_s%d.py" % i) for i in range(6)]
        with mock.patch.dict(os.environ, {self.ENV: "load"}):
            out, peak, _base = self.run_pool(rows, self.seat(), 6)
        self.assertEqual([v for _, _, v, _ in out], [True] * 6)
        self.assertEqual(peak, 6, "the old count: every slice at once, the ledger would have held it to 2")
        self.assertFalse(os.path.exists(self.ledger), "no lease was taken, so the ledger was never written")

    def test_the_pool_in_load_mode_makes_the_same_bare_settle_call_the_old_pool_made(self):
        rows = [("scripts/f%d.py" % i, "scripts/test_s%d.py" % i) for i in range(3)]
        calls = []

        def bare(n):   # the shape callers that patch settled_workers expect: one positional argument
            calls.append(n)
            return n
        base = tempfile.mkdtemp(prefix="run-", dir=self.dir)

        def spawn(argv, env):
            with open(argv[4]) as fh, open(argv[6], "w") as out:
                json.dump([[f, s, True, "red"] for f, s in json.load(fh)], out)
            return FakeProc()
        with mock.patch.dict(os.environ, {self.ENV: "load"}), mock.patch.object(PP, "settled_workers", bare), \
                mock.patch.object(PP, "pool_scratch_root", lambda: self.dir):
            PP.run_rows(rows, self.dir, 5, workers=3, base=base, spawn=spawn, copier=self.make_copy,
                        cap=lambda requested, **kw: requested, say=lambda text: None, leases=self.seat())
        self.assertEqual(calls, [3])

    def test_the_pool_with_the_switch_unset_still_takes_leases(self):
        rows = [("scripts/f%d.py" % i, "scripts/test_s%d.py" % i) for i in range(6)]
        with mock.patch.dict(os.environ):
            os.environ.pop(self.ENV, None)
            _out, peak, _base = self.run_pool(rows, self.seat(), 6)
        self.assertEqual(peak, 2)
        self.assertTrue(os.path.exists(self.ledger))

    def test_the_pool_refuses_an_unknown_value_before_it_makes_a_folder_or_starts_a_worker(self):
        before = sorted(os.listdir(self.dir))
        spawned = []
        with mock.patch.dict(os.environ, {self.ENV: "Load"}), \
                mock.patch.object(PP, "pool_scratch_root", lambda: self.dir), self.assertRaises(PP.AdmissionRefused):
            PP.run_rows([("scripts/a.py", "scripts/test_a.py")], self.dir, 5, workers=1, spawn=lambda a, e: spawned.append(a),
                        copier=self.make_copy, say=lambda text: None)
        self.assertEqual((spawned, sorted(os.listdir(self.dir))), ([], before))


if __name__ == "__main__":
    unittest.main()
