"""What the resource gate must keep true, calibrated in BOTH directions.

Every band is forced with an INJECTED reading, never by trying to make the
real machine scarce: a test that depended on this machine's current disk or
load would pass or fail by accident of when it ran, which is exactly the kind
of flaky evidence this whole module exists to stop producing elsewhere.

THE CASE THAT MATTERS MOST is TheTimeoutIsNeverRenderedAsFail: the module's
entire reason to exist is that a resource-induced timeout must never read as
a code defect. If that class ever goes green by accident (INVALID containing
the substring FAIL, or a scarce reading admitting cleanly), the tool has
stopped being a control and become a comment.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resource_gate as G  # noqa: E402


def reading(disk=20.0, cores=7, load1=1.0, mem=4.0, errors=None):
    """A fully healthy injected reading by default; override just the field
    under test. Never touches the real machine."""
    return {"disk_free_gib": disk, "cores_available": cores, "load1": load1,
            "mem_free_gib": mem, "errors": errors or {}}


class ReadingsAreNumbersOrNamedNoData(unittest.TestCase):
    """W2.1: each reading returns a number or NO-DATA naming why, never a
    guess. This class reads the REAL machine, once, for shape only: it never
    asserts what the numbers ARE, only that a None is always paired with a
    reason."""

    def test_one_call_returns_all_four_fields(self):
        r = G.read()
        for key in ("disk_free_gib", "cores_available", "load1", "mem_free_gib"):
            self.assertIn(key, r)

    def test_every_none_field_carries_a_named_reason(self):
        r = G.read()
        for key in ("disk_free_gib", "cores_available", "load1", "mem_free_gib"):
            if r[key] is None:
                self.assertIn(key, r["errors"], "%s is None with no reason" % key)
                self.assertTrue(r["errors"][key])
            else:
                self.assertNotIn(key, r["errors"])

    def test_disk_and_cores_read_on_this_machine(self):
        """These two are stdlib-portable everywhere this repo runs, so they
        must never be NO-DATA here."""
        r = G.read()
        self.assertIsInstance(r["disk_free_gib"], float)
        self.assertGreater(r["disk_free_gib"], 0)
        self.assertIsInstance(r["cores_available"], int)

    def test_memory_is_nodata_on_this_mac_by_design(self):
        """Verified live on this Mac: os.sysconf lacks SC_AVPHYS_PAGES, so
        free memory cannot be read from stdlib here. Documented in the
        module's own docstring; this is the test that keeps it honest."""
        if "SC_AVPHYS_PAGES" in getattr(os, "sysconf_names", {}):
            self.skipTest("this platform can read free memory; NO-DATA is a "
                          "macOS fact, not a module fact")
        r = G.read()
        self.assertIsNone(r["mem_free_gib"])
        self.assertIn("NO-DATA", r["errors"]["mem_free_gib"])


class UnreadableIsSimulatedWithoutTouchingTheMachine(unittest.TestCase):
    """W2.1.2: an unreadable field is NO-DATA, never assumed healthy. Forces
    the plumbing's own error path, on a healthy real machine, so the test
    proves the CODE PATH rather than hoping the machine happens to be
    scarce."""

    def test_each_field_can_be_forced_unreadable(self):
        # Baseline once, for real: on THIS machine mem_free_gib is already
        # NO-DATA (no stdlib free-memory read on macOS), so "no leakage"
        # below is judged against what was actually readable to begin with,
        # never against an assumption that every field starts healthy.
        baseline = G.read()
        for alias, full in G._FIELD_ALIAS.items():
            r = G.read(simulate_unreadable=[alias])
            self.assertIsNone(r[full], "simulating %r did not blank %r" % (alias, full))
            self.assertIn(full, r["errors"])
            for other in set(G._FIELD_ALIAS.values()) - {full}:
                if baseline[other] is None:
                    continue  # already NO-DATA on this machine; not a leak
                self.assertNotIn(other, r["errors"],
                                  "simulating %r leaked into %r" % (alias, other))


class AdmissionForcesEveryBand(unittest.TestCase):
    """W2.3.1: force each band with an injected reading and assert the
    matching verdict. No case here reads the real machine."""

    def test_a_healthy_reading_admits_with_no_reasons(self):
        a = G.admit("battery", reading=reading())
        self.assertEqual(a["verdict"], G.ADMIT)
        self.assertEqual(a["reasons"], [])

    def test_disk_just_under_the_refuse_floor_defers(self):
        a = G.admit("battery", reading=reading(disk=G.DISK_REFUSE_GIB - 0.01))
        self.assertEqual(a["verdict"], G.DEFER)
        self.assertTrue(any("refuse floor" in r for r in a["reasons"]))

    def test_disk_exactly_at_the_refuse_floor_is_admitted_with_a_cleanup_warning(self):
        """The refuse floor is a hard '<' boundary: 8.0 itself is not under
        8.0. It lands in the softer cleanup band instead (8.0 < 15.0)."""
        a = G.admit("battery", reading=reading(disk=G.DISK_REFUSE_GIB))
        self.assertEqual(a["verdict"], G.ADMIT)
        self.assertTrue(any("cleanup band" in r for r in a["reasons"]))

    def test_disk_in_the_cleanup_band_is_admitted_but_flagged(self):
        a = G.admit("battery", reading=reading(disk=10.0))
        self.assertEqual(a["verdict"], G.ADMIT)
        self.assertTrue(any("cleanup band" in r for r in a["reasons"]))

    def test_disk_above_the_cleanup_band_is_admitted_clean(self):
        a = G.admit("battery", reading=reading(disk=G.DISK_CLEANUP_GIB))
        self.assertEqual(a["verdict"], G.ADMIT)
        self.assertEqual(a["reasons"], [])

    def test_disk_unreadable_defers_rather_than_assuming_healthy(self):
        a = G.admit("battery", reading=reading(
            disk=None, errors={"disk_free_gib": "simulated"}))
        self.assertEqual(a["verdict"], G.DEFER)

    def test_oversubscribed_load_defers(self):
        a = G.admit("battery", reading=reading(cores=4, load1=9.0))
        self.assertEqual(a["verdict"], G.DEFER)
        self.assertTrue(any("oversubscribed" in r for r in a["reasons"]))

    def test_load_at_or_under_cores_admits(self):
        a = G.admit("battery", reading=reading(cores=4, load1=4.0))
        self.assertEqual(a["verdict"], G.ADMIT)

    def test_cores_unreadable_defers(self):
        a = G.admit("battery", reading=reading(
            cores=None, errors={"cores_available": "simulated"}))
        self.assertEqual(a["verdict"], G.DEFER)

    def test_load_unreadable_defers(self):
        a = G.admit("battery", reading=reading(
            load1=None, errors={"load1": "simulated"}))
        self.assertEqual(a["verdict"], G.DEFER)

    def test_the_numbers_that_refused_it_are_printed(self):
        """what_they_see in the roadmap: 'a dispatch that would oversubscribe
        the machine is deferred with the reason and the number'."""
        a = G.admit("battery", reading=reading(disk=1.0))
        self.assertEqual(a["numbers"]["disk_free_gib"], 1.0)


class TheTimeoutIsNeverRenderedAsFail(unittest.TestCase):
    """W2.2.2 and W2.3.2 together: a resource-induced timeout is INVALID,
    never FAIL, and that must hold on both sides of the boundary."""

    def test_a_timeout_against_a_scarce_reading_is_invalid(self):
        verdict, why = G.classify("timeout", reading=reading(disk=1.0))
        self.assertEqual(verdict, G.INVALID)
        self.assertNotIn("FAIL", why)

    def test_a_timeout_against_a_healthy_reading_stays_fail(self):
        verdict, why = G.classify("timeout", reading=reading())
        self.assertEqual(verdict, G.FAIL)
        self.assertNotIn("INVALID", why)

    def test_invalid_never_contains_fail_across_every_scarce_band(self):
        scarce_readings = [
            reading(disk=1.0),
            reading(disk=None, errors={"disk_free_gib": "x"}),
            reading(cores=4, load1=9.0),
            reading(cores=None, errors={"cores_available": "x"}),
            reading(load1=None, errors={"load1": "x"}),
        ]
        for r in scarce_readings:
            verdict, why = G.classify("timeout", reading=r)
            self.assertEqual(verdict, G.INVALID, r)
            self.assertNotIn("FAIL", why, "INVALID rendered as FAIL for %r" % (r,))

    def test_a_genuine_failed_exit_stays_fail_even_on_a_scarce_machine(self):
        """Only a TIMEOUT is ambiguous. A real non-zero exit is evidence
        about the code whatever the machine was doing, so scarcity must
        never launder a real bug into INVALID."""
        verdict, why = G.classify("failed", reading=reading(disk=1.0))
        self.assertEqual(verdict, G.FAIL)
        self.assertNotIn("INVALID", why)

    def test_a_passed_run_is_never_reclassified_by_scarcity(self):
        verdict, _why = G.classify("passed", reading=reading(disk=1.0))
        self.assertEqual(verdict, G.PASS)

    def test_an_unknown_outcome_is_nodata_not_a_guess(self):
        verdict, _why = G.classify("sideways")
        self.assertEqual(verdict, G.NODATA)


class LockSerializesBatteries(unittest.TestCase):
    """W2.2.3: a battery lock a second caller waits on, mirroring
    integrate.py's O_EXCL _Lock. Proven by contention, not by inspection."""

    def test_a_second_acquire_waits_and_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            with G._Lock(repo=tmp, timeout=5.0):
                with self.assertRaises(TimeoutError):
                    G._Lock(repo=tmp, timeout=0.2).__enter__()

    def test_the_lock_releases_and_a_second_caller_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            with G._Lock(repo=tmp):
                pass
            with G._Lock(repo=tmp, timeout=1.0):
                pass  # did not raise: the file was cleaned up on exit


class RunLockedDefersWithoutRunningAndRecordsForClassifyLast(unittest.TestCase):
    """W2.2.1 plus the --classify-last wiring: a deferred dispatch never
    runs the command, and the record left behind classifies as INVALID."""

    def test_a_deferred_run_never_executes_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            marker = os.path.join(tmp, "ran")
            state = os.path.join(tmp, "state.json")
            result = G.run_locked(
                [sys.executable, "-c", "open(%r, 'w').close()" % marker],
                cost="battery", reading=reading(disk=1.0), repo=tmp,
                state_path=state)
            self.assertEqual(result["outcome"], "deferred")
            self.assertFalse(os.path.exists(marker), "a deferred dispatch ran anyway")

    def test_classify_last_reports_invalid_for_a_deferred_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            state = os.path.join(tmp, "state.json")
            G.run_locked([sys.executable, "-c", "pass"], cost="battery",
                         reading=reading(disk=1.0), repo=tmp, state_path=state)
            verdict, why = G.classify_last(state_path=state)
            self.assertEqual(verdict, G.INVALID)
            self.assertNotIn("FAIL", why)

    def test_an_admitted_run_executes_and_classifies_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            marker = os.path.join(tmp, "ran")
            state = os.path.join(tmp, "state.json")
            result = G.run_locked(
                [sys.executable, "-c", "open(%r, 'w').close()" % marker],
                cost="battery", reading=reading(), repo=tmp, state_path=state)
            self.assertEqual(result["outcome"], "passed")
            self.assertTrue(os.path.exists(marker))
            verdict, _why = G.classify_last(state_path=state)
            self.assertEqual(verdict, G.PASS)

    def test_classify_last_with_no_recorded_run_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict, _why = G.classify_last(state_path=os.path.join(tmp, "missing.json"))
            self.assertEqual(verdict, G.NODATA)


class CliSmoke(unittest.TestCase):
    """The CLI shapes named in the roadmap. Kept thin: the logic itself is
    already proven above through the pure functions."""

    def test_read_exits_zero(self):
        self.assertEqual(G.main(["--read"]), 0)

    def test_read_with_simulated_unreadable_exits_zero(self):
        self.assertEqual(G.main(["--read", "--simulate-unreadable", "disk"]), 0)

    def test_admit_prints_a_verdict_and_exits_0_or_2(self):
        self.assertIn(G.main(["--admit", "battery"]), (0, 2))

    def test_no_flags_prints_help_and_is_nodata_shaped(self):
        self.assertEqual(G.main([]), 2)


if __name__ == "__main__":
    unittest.main()
