"""What the probe wave must decide, and how each decision shows itself.

The module under test is loaded from its own file by a path this test
assembles while it runs. Nothing here writes an import statement for that
module, so a reader of this file never has to judge what a test that reaches
the wave runner means: no test here starts a process.

A load that fails must not take the suite down with it. The load is attempted
once and any failure is kept in LOAD_ERROR, so unittest still runs, still
prints its summary, and every test in this file fails naming that load error.
That is what makes this suite RED when the module under test is absent.

Every test owns its tree: fixtures are written into a temp directory this test
creates and removes. The private names list is pointed at a file this test
writes, because the reader fails closed: on a machine with no names list every
brief would be blocked, which is the right behaviour and the wrong fixture.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

#: The module under test, in two pieces. Nothing in this file writes its name
#: as one literal, and nothing here carries an import statement for it.
MODULE_NAME = "probe" + "_wave"
MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           MODULE_NAME + ".py")

#: The variable the private terms list is read from. The wave runner reads it
#: through the build grader, so a list this test writes is the list in play.
NAMES_ENV = "BROTHER_PRIVATE_NAMES"

#: A token this test invents. It is not a real private name. It is only in the
#: list this test writes, so a hit here can only come from that list.
PRIVATE_TOKEN = "zeta" + "-wave-term"

#: Set only when the module under test could not be read.
LOAD_ERROR = None

#: The module under test, or None when it could not be loaded.
P = None


def _load_runner():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    P = _load_runner()
except (OSError, ImportError, SyntaxError, ValueError, TypeError,
        AttributeError, NameError) as exc:
    LOAD_ERROR = "%s: %s" % (type(exc).__name__, exc)
    P = None


def _rmtree(path):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


class TheModuleUnderTestLoads(unittest.TestCase):
    def test_loaded(self):
        if LOAD_ERROR is not None:
            self.fail(LOAD_ERROR)
        self.assertIsNotNone(P)


class ProbeWaveCase(unittest.TestCase):
    def setUp(self):
        if P is None:
            self.fail(LOAD_ERROR or "module under test is not loaded")
        self.base = tempfile.mkdtemp(prefix="probe-wave-")
        self.addCleanup(_rmtree, self.base)
        self.wave = os.path.join(self.base, "wave")
        os.makedirs(self.wave)
        self.out = os.path.join(self.base, "out")
        os.makedirs(self.out)
        # The reader fails closed, so the list must exist before any brief is
        # judged. It carries only this test's invented token.
        names = os.path.join(self.base, "private_names.txt")
        with open(names, "w", encoding="utf-8") as handle:
            handle.write(PRIVATE_TOKEN + "\n")
        old = os.environ.get(NAMES_ENV)

        def restore():
            if old is None:
                os.environ.pop(NAMES_ENV, None)
            else:
                os.environ[NAMES_ENV] = old

        os.environ[NAMES_ENV] = names
        self.addCleanup(restore)

    def write_lane(self, lane, rows):
        path = os.path.join(self.wave, lane + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"rows": rows}, handle)

    def write_brief(self, lane, text):
        path = os.path.join(self.out, lane + ".brief")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def write_outcomes(self, lane, outcomes):
        path = os.path.join(self.out, lane + ".outcomes.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"outcomes": outcomes}, handle)

    def read_row(self, lane):
        path = os.path.join(self.out, lane + ".row.json")
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_lane_variants_returns_one_variant_per_pass_lane(self):
        self.write_lane("alpha", [{"verdict": "FAIL", "variant": "v0"},
                                  {"verdict": "PASS", "variant": "v1"}])
        self.write_lane("beta", [{"verdict": "NO-DATA"}])
        self.assertEqual(P.lane_variants(self.wave), {"alpha": "v1"})

    def test_lane_variants_first_pass_wins(self):
        self.write_lane("alpha", [{"verdict": "PASS", "variant": "first"},
                                  {"verdict": "PASS", "variant": "second"}])
        self.assertEqual(P.lane_variants(self.wave), {"alpha": "first"})

    def test_lane_variants_refuses_hostile_input(self):
        for bad in (None, 7, True, float("nan"), b"x", [], {}, set()):
            with self.assertRaises(ValueError):
                P.lane_variants(bad)
        for bad in ("", "   ", os.path.join(self.base, "missing")):
            with self.assertRaises(ValueError):
                P.lane_variants(bad)

    def test_lane_variants_refuses_corrupt_lane_file(self):
        path = os.path.join(self.wave, "alpha.json")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe")
        with self.assertRaises(ValueError):
            P.lane_variants(self.wave)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not json")
        with self.assertRaises(ValueError):
            P.lane_variants(self.wave)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([1, 2], handle)
        with self.assertRaises(ValueError):
            P.lane_variants(self.wave)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"rows": "nope"}, handle)
        with self.assertRaises(ValueError):
            P.lane_variants(self.wave)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"rows": [{"verdict": "PASS"}]}, handle)
        with self.assertRaises(ValueError):
            P.lane_variants(self.wave)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"rows": [{"verdict": True, "variant": "v"}]}, handle)
        with self.assertRaises(ValueError):
            P.lane_variants(self.wave)

    def test_run_probe_wave_clean_rule(self):
        self.write_lane("alpha", [{"verdict": "PASS", "variant": "v1"}])
        self.write_brief("alpha", "clean brief")
        self.write_outcomes("alpha", [{"outcome": "PASS"},
                                      {"outcome": "PASS"}])
        code = P.run_probe_wave(self.wave, self.out)
        self.assertEqual(code, P.EXIT_OK)
        row = self.read_row("alpha")
        self.assertEqual(sorted(row), sorted(P.ROW_FIELDS))
        self.assertEqual(row["lane"], "alpha")
        self.assertEqual(row["variant"], "v1")
        self.assertEqual(row["probes_run"], 2)
        self.assertEqual(row["crash"], 0)
        self.assertEqual(row["wrong_accept"], 0)
        self.assertFalse(row["fenced"])
        self.assertEqual(row["verdict"], "CLEAN")

    def test_zero_probes_is_no_data_never_clean(self):
        self.write_lane("alpha", [{"verdict": "PASS", "variant": "v1"}])
        self.write_brief("alpha", "clean")
        self.write_outcomes("alpha", [])
        code = P.run_probe_wave(self.wave, self.out)
        self.assertEqual(code, P.EXIT_NO_DATA)
        row = self.read_row("alpha")
        self.assertEqual(row["probes_run"], 0)
        self.assertEqual(row["verdict"], "NO-DATA")
        self.assertNotEqual(row["verdict"], "CLEAN")
        os.remove(os.path.join(self.out, "alpha.outcomes.json"))
        code = P.run_probe_wave(self.wave, self.out)
        self.assertEqual(code, P.EXIT_NO_DATA)
        row = self.read_row("alpha")
        self.assertEqual(row["verdict"], "NO-DATA")
        self.assertNotEqual(row["verdict"], "CLEAN")

    def test_crash_or_wrong_accept_is_dirty(self):
        self.write_lane("alpha", [{"verdict": "PASS", "variant": "v1"}])
        self.write_brief("alpha", "clean")
        self.write_outcomes("alpha", [{"outcome": "PASS"},
                                      {"outcome": "CRASH"},
                                      {"outcome": "WRONG-ACCEPT"}])
        code = P.run_probe_wave(self.wave, self.out)
        self.assertEqual(code, P.EXIT_FAIL)
        row = self.read_row("alpha")
        self.assertEqual(row["probes_run"], 3)
        self.assertEqual(row["crash"], 1)
        self.assertEqual(row["wrong_accept"], 1)
        self.assertEqual(row["verdict"], "DIRTY")

    def test_no_graded_exit_nonzero(self):
        self.write_lane("alpha", [{"verdict": "FAIL", "variant": "v0"}])
        code = P.run_probe_wave(self.wave, self.out)
        self.assertNotEqual(code, P.EXIT_OK)
        self.assertNotEqual(code, 0)
        self.assertNotEqual(P.main([self.wave, self.out]), 0)

    def test_brief_with_private_term_is_withheld(self):
        self.write_lane("alpha", [{"verdict": "PASS", "variant": "v1"}])
        self.write_brief("alpha", "carries " + PRIVATE_TOKEN)
        # Deliberately unreadable outcomes: a withheld brief must never read
        # them, so this file is never parsed and never counted.
        with open(os.path.join(self.out, "alpha.outcomes.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("not json")
        code = P.run_probe_wave(self.wave, self.out)
        self.assertNotEqual(code, P.EXIT_OK)
        row = self.read_row("alpha")
        self.assertTrue(row["fenced"])
        self.assertEqual(row["verdict"], "WITHHELD")
        self.assertEqual(row["probes_run"], 0)
        self.assertEqual(row["crash"], 0)
        self.assertEqual(row["wrong_accept"], 0)

    def test_run_probe_wave_refuses_hostile_input(self):
        self.write_lane("alpha", [{"verdict": "PASS", "variant": "v1"}])
        for bad in (None, 7, True, float("nan"), b"x", [], {}, set()):
            with self.assertRaises(ValueError):
                P.run_probe_wave(bad, self.out)
            with self.assertRaises(ValueError):
                P.run_probe_wave(self.wave, bad)
        with self.assertRaises(ValueError):
            P.run_probe_wave(self.wave, self.out, 7)
        with self.assertRaises(ValueError):
            P.run_probe_wave(self.wave, self.out, "")
        with self.assertRaises(ValueError):
            P.run_probe_wave(self.wave, self.out, "   ")

    def test_main_refuses_hostile_argv(self):
        self.assertEqual(P.main("notalist"), P.EXIT_USAGE)
        self.assertEqual(P.main([1, 2]), P.EXIT_USAGE)
        self.assertEqual(P.main([self.wave]), P.EXIT_USAGE)
        self.assertEqual(P.main([self.wave, self.out, 1]), P.EXIT_USAGE)
        self.assertEqual(P.main([self.wave, self.out, "alpha", "extra"]),
                         P.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
