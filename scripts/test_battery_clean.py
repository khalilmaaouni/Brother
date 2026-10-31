#!/usr/bin/env python3
"""S10's frozen harness: the reds only the full battery sees are fixed at their source.

Written by the session before either sub unit is built and never by their builder (HARN1), which both
spec sections state in as many words: "the frozen harness: written by the session, red first, before the
code is built, never by its builder".

RED ON THE BASE BY AN ASSERTION, never by an import error and never by a skip. Every requirement here
names behaviour that does not exist yet, so presence is established with `defines`, which parses the
file with ast and imports nothing; the behaviour assertions run only once the symbol is there. That
shape is not a style choice: a case that calls a function which does not exist yet raises, which reads
as an error, which is red for the wrong reason, and scripts/test_plan_119.py refuses a class that is red
for the wrong reason.

WHAT A CHECK ONLY THE BATTERY SEES MEANS, and why it decides this file's whole design. S10's title is
"The reds only the full battery sees on main are fixed or classified at their source". A class that
passes when run alone and fails when its file runs whole is failing on test INTERACTION, not on its own
logic. So a harness that runs the class alone and asserts OK would be green while the battery stayed
red, which is the hollow green the 2026-09-21 delivery law names: the subject of a check is the
deliverable, and here the deliverable is the battery's own verdict on the whole file.

  python3 -B scripts/loop/test_land_apply.py NativeWorkerThroughTheGate   the class alone
  run_check "land-apply-self" python3 -B scripts/loop/test_land_apply.py  what the battery runs

The second is the subject. NativeWorker below runs the file, not the class.

CLASSES AND THE SUB UNIT EACH ONE CLOSES:
  NativeWorker  S10.a  scripts/loop/test_land_apply.py
  PoolTimeout   S10.b  scripts/perturb_pool.py

Done checks: python3 -B scripts/test_battery_clean.py NativeWorker
             python3 -B scripts/test_battery_clean.py PoolTimeout
"""
import ast
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAND_APPLY = os.path.join(ROOT, "scripts/loop/test_land_apply.py")
POOL = os.path.join(ROOT, "scripts/perturb_pool.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"
#: unittest's own summary lines, read rather than re-derived
RAN = re.compile(r"^Ran (\d+) test", re.M)
BAD = re.compile(r"\b(?:errors|skipped|expected failures|unexpected successes)=", re.M)
NAMED = re.compile(r"^(?:FAIL|ERROR): (\w+)", re.M)
#: the whole file takes minutes under contention; a run that hangs is NO-DATA, never a pass
FILE_TIMEOUT_S = 1800


def defines(path, name):
    """Does this file define a module level function or class called `name`? Answered by parsing, so an
    absent symbol is a False to assert on rather than an ImportError to trip over, and so a module with
    import side effects is never executed just to ask whether a name exists. None when unreadable:
    never a quiet False."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
               for n in tree.body)


def load(path, name):
    """The module at `path`, imported under a private name so it cannot collide with anything already
    imported, or None when it will not import. Only called once `defines` says the symbol is there."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


class _Base(unittest.TestCase):
    FILE = None

    def setUp(self):
        if self.FILE and not os.path.isfile(self.FILE):
            self.fail(ABSENT % os.path.relpath(self.FILE, ROOT))

    def has(self, path, name):
        """Assert the symbol exists, and say what is missing. An unreadable file fails, never passes."""
        found = defines(path, name)
        self.assertIsNotNone(found, "%s could not be parsed, so %s cannot be judged"
                             % (os.path.relpath(path, ROOT), name))
        self.assertTrue(found, "%s does not define %s yet"
                        % (os.path.relpath(path, ROOT), name))


class PoolTimeout(_Base):
    """S10.b. The baseline run's wall grows with the pool's own concurrency.

    THE DEFECT, read off the tree rather than described: run_rows takes `baseline_wall` and hands it
    straight to run_worker, which passes it as `timeout=baseline_wall` into every baseline, whatever
    the worker count is. So a baseline that finishes comfortably alone times out when six siblings
    compete with it, which is D19 run 2.

    THE PROPERTY IS PINNED, NOT THE FORMULA. Whether the wall should scale by the worker count, by the
    workers over the cores, or by something the builder measures, is a design question with a real
    answer and it is the builder's to make. What cannot be left open is that the wall RESPONDS to
    concurrency at all, never shrinks, and refuses junk instead of raising. A harness that pinned an
    arithmetic expression would be the session choosing the fix and then grading its own choice.
    """
    FILE = POOL
    NAME = "baseline_timeout"

    def module(self):
        self.has(POOL, self.NAME)
        module = load(POOL, "_perturb_pool_under_test")
        self.assertIsNotNone(module, "scripts/perturb_pool.py would not import, so %s cannot be called"
                             % self.NAME)
        return getattr(module, self.NAME)

    # REQ-S10B-01, the case the spec names, and the one M-S10B-FLAT must kill: seven workers get more
    # wall than one, on the same machine.
    def test_baseline_timeout_scales_with_workers(self):
        timeout = self.module()
        for cores in (1, 2, 4, 8, 16):
            alone = timeout(600, 1, ncpu=cores)
            crowded = timeout(600, 7, ncpu=cores)
            self.assertGreater(crowded, alone,
                               "on %d core(s) seven baselines get %r and one gets %r, so the wall "
                               "ignores the pool's own concurrency" % (cores, crowded, alone))

    # It never shrinks a wall. A pool that made the timeout SMALLER would turn D19 into a worse bug,
    # and a scaling function is the natural place for a sign error.
    def test_the_wall_never_shrinks(self):
        timeout = self.module()
        for workers in (1, 2, 3, 7, 64):
            for cores in (1, 4, 16):
                got = timeout(600, workers, ncpu=cores)
                self.assertGreaterEqual(got, 600,
                                        "%d worker(s) on %d core(s) got %r, less than the wall asked "
                                        "for" % (workers, cores, got))

    # It is monotone in the worker count: more competition never buys less wall.
    def test_more_workers_never_buy_less_wall(self):
        timeout = self.module()
        for cores in (1, 4, 8):
            walls = [timeout(600, n, ncpu=cores) for n in range(1, 9)]
            self.assertEqual(walls, sorted(walls),
                             "on %d core(s) the wall is not monotone in the worker count: %r"
                             % (cores, walls))

    # Hostile inputs refuse or fall back to something no smaller than the wall, and never raise. The
    # whole point of the module's own default_workers docstring is that a bad count reads as fewer
    # workers, "slower, never wrong", so this follows the file's own fail direction.
    def test_junk_refuses_rather_than_raising(self):
        timeout = self.module()
        for workers in (None, "", "seven", 0, -3, True, 2.5, [], {}):
            try:
                got = timeout(600, workers)
            except Exception as exc:                      # noqa: BLE001, the point is that it does not
                self.fail("workers=%r raised %s instead of refusing" % (workers, exc))
            self.assertIsInstance(got, (int, float),
                                  "workers=%r answered %r, which is not a wall" % (workers, got))
            self.assertGreaterEqual(got, 600, "workers=%r answered %r, under the wall" % (workers, got))
        for wall in (None, "", "ten", -1, [], {}):
            try:
                got = timeout(wall, 4)
            except Exception as exc:                      # noqa: BLE001
                self.fail("wall=%r raised %s instead of refusing" % (wall, exc))
            self.assertIsInstance(got, (int, float),
                                  "wall=%r answered %r, which is not a wall" % (wall, got))
            self.assertGreater(got, 0, "wall=%r answered %r, which is not a usable wall" % (wall, got))

    # An unreadable core count is never the generous case by accident: it must still answer a wall.
    def test_an_unknown_core_count_still_answers(self):
        timeout = self.module()
        for cores in (None, 0, -1, "eight", True, 1.5):
            try:
                got = timeout(600, 7, ncpu=cores)
            except Exception as exc:                      # noqa: BLE001
                self.fail("ncpu=%r raised %s instead of refusing" % (cores, exc))
            self.assertGreaterEqual(got, 600, "ncpu=%r answered %r, under the wall" % (cores, got))

    # The pool must actually USE it. A scaling function nothing calls is a dead control, which this
    # estate has shipped before and measured: the fix is not finished until run_rows routes through it.
    def test_the_pool_routes_its_baseline_through_it(self):
        with open(POOL, encoding="utf-8") as fh:
            source = fh.read()
        where = source.find("def run_rows(")
        self.assertGreater(where, -1, "scripts/perturb_pool.py no longer defines run_rows")
        self.assertGreater(source.count(self.NAME + "("), 1,
                           "%s is defined but never called, so the pool's baselines still get a flat "
                           "wall and the fix is a dead control" % self.NAME)


class NativeWorker(_Base):
    """S10.a. D16's red is fixed at its source or classified there, never waived in silence.

    THE SUBJECT IS WHAT THE BATTERY RUNS. check_all.sh registers the WHOLE FILE as one row,
    `run_check "land-apply-self" python3 -B scripts/loop/test_land_apply.py`, so the class passing when
    run alone says nothing about the row. Measured on this tree the day this was written: the class
    alone reads Ran 2 tests, OK, and the whole file on this interpreter reads Ran 57 tests, OK. The
    spec's own words are that D16 is stale since 5f9a6bc16, and a red that no longer reproduces is a
    real outcome, not a failure to try hard enough.

    SO THIS CLASS DOES NOT ASSERT A RED. It asserts the requirement, REQ-S10A-01, that the class passes
    on BOTH interpreters under the battery's own invocation, and it asserts that the outcome was
    WRITTEN DOWN. The spec says never waived silently, and the only difference between a stale red that
    was investigated and one that was ignored is whether somebody recorded which it was. That record is
    the red case here, and it is red today because nothing in the file names D16 at all.

    A SKIP IS NOT GREEN, which is what M-S10A-WAIVE reaches for: the easiest way to make a stubborn
    class stop failing is to decorate it with a skip, and unittest then prints OK and exits 0. So the
    summary is read, not just the exit code, and any skipped= or errors= refuses the row.
    """
    FILE = LAND_APPLY
    CLASS = "NativeWorkerThroughTheGate"
    #: the one spelling of D16 the record must carry, so a vague comment does not satisfy it
    MARKER = "D16"

    def whole_file(self, interpreter):
        """(exit code, summary) from running the file the way the battery does, or None when the run
        could not happen at all, which is NO-DATA and never a pass."""
        if not os.path.exists(interpreter):
            return None, "%s is not on this machine, so the row cannot be judged there" % interpreter
        try:
            done = subprocess.run([interpreter, "-B", LAND_APPLY], capture_output=True, text=True,
                                  cwd=ROOT, timeout=FILE_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "the run could not complete under %s: %s" % (interpreter, exc)
        return done.returncode, (done.stdout or "") + (done.stderr or "")

    # REQ-S10A-01
    def test_native_worker_class_passes(self):
        self.has(LAND_APPLY, self.CLASS)
        for interpreter in (sys.executable, "/usr/bin/python3"):
            code, said = self.whole_file(interpreter)
            self.assertIsNotNone(code, "NO-DATA: %s" % said)
            ran = RAN.search(said)
            self.assertIsNotNone(ran, "%s printed no test summary, so nothing says what it ran: %s"
                                 % (interpreter, said.strip()[-200:]))
            self.assertGreater(int(ran.group(1)), 0,
                               "%s ran zero tests, which is not a pass" % interpreter)
            bad = BAD.search(said)
            self.assertIsNone(bad, "%s reports %s in its summary, and a skip or an error is not green: "
                                   "%s" % (interpreter, bad.group(0) if bad else "", 
                                           said.strip().splitlines()[-1][:160]))
            self.assertEqual(0, code, "%s exited %d; reds: %s"
                             % (interpreter, code, sorted(set(NAMED.findall(said))) or "none named"))

    # The battery must still read the whole file. Narrowing the row to the one class would make it
    # green by changing the subject, which is the shape the 2026-09-21 delivery law forbids.
    def test_the_battery_still_runs_the_whole_file(self):
        battery = os.path.join(ROOT, "scripts/check_all.sh")
        if not os.path.isfile(battery):
            self.fail(ABSENT % "scripts/check_all.sh")
        with open(battery, encoding="utf-8") as fh:
            rows = [line for line in fh if "test_land_apply.py" in line
                    and line.lstrip().startswith("run_check")]
        self.assertTrue(rows, "no battery row runs scripts/loop/test_land_apply.py any more")
        for row in rows:
            self.assertNotIn(self.CLASS, row,
                             "a battery row was narrowed to %s, so the row no longer measures the "
                             "interaction that made it red: %s" % (self.CLASS, row.strip()))

    # The red case: the outcome is written down in the file S10 owns, naming D16.
    def test_the_outcome_is_recorded_and_not_waived(self):
        with open(LAND_APPLY, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn(self.MARKER, source,
                      "scripts/loop/test_land_apply.py never names %s, so whether its red was fixed at "
                      "the source, had its premise corrected, or simply stopped reproducing is "
                      "recorded nowhere, which is the silent waive the spec forbids" % self.MARKER)
        where = source.find(self.MARKER)
        window = source[max(0, where - 400):where + 800]
        self.assertTrue(any(word in window for word in ("premise", "source", "stale", "reproduce")),
                        "%s is named but nothing beside it says what was concluded about it" %
                        self.MARKER)


if __name__ == "__main__":
    unittest.main(verbosity=1)
