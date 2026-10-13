"""JWP.b: a missing, corrupt or unreadable Jev registry is one NO-DATA line,
never a traceback, and a clone install receives exactly the two data files.

HOME and BROTHER_JEV_STATE_DIR point at fresh temp folders before any product
import, and every subprocess inherits them. No test asks a model anything:
every CLI run here either has every seam off or a registry that cannot load,
so the CLI returns before any check runs.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HOME = tempfile.mkdtemp(prefix="jev-registry-nodata-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")
for _name in ("BROTHER_DECISION_BRIDGE", "BROTHER_TRANSPORTS", "BROTHER_JEV_SEAMS_OFF"):
    os.environ.pop(_name, None)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import export_public  # noqa: E402
import jev_seam  # noqa: E402

CLI = os.path.join(HERE, "jev_checks.py")
PREFIX = "NO-DATA: Jev is not installed here"


class JevRegistryNoDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="work-", dir=_HOME)
        cls.live = os.path.join(cls.tmp, "seams-live.json")
        with open(cls.live, "w") as fh:
            json.dump({"modes": {"J030": "shadow"}}, fh)
        cls.off = os.path.join(cls.tmp, "seams-off.json")
        with open(cls.off, "w") as fh:
            json.dump({"modes": {}}, fh)

    def _cli(self, registry, seams):
        return subprocess.run(
            [sys.executable, "-B", CLI, "--registry", registry,
             "--seams-config", seams,
             "--ledger-dir", os.path.join(self.tmp, "ledger"),
             "j030", "--claim", "c", "--source", "s"],
            capture_output=True, text=True, timeout=120, env=dict(os.environ))

    def _assert_one_nodata(self, registry):
        proc = self._cli(registry, self.live)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertTrue(lines[0].startswith(PREFIX), lines[0])
        return lines[0]

    def test_a_missing_registry_prints_one_nodata_line(self):
        self._assert_one_nodata(os.path.join(self.tmp, "absent.json"))

    def test_a_corrupt_registry_reads_the_same(self):
        bad = os.path.join(self.tmp, "corrupt.json")
        with open(bad, "w") as fh:
            fh.write("not json")
        line = self._assert_one_nodata(bad)
        self.assertIn("RegistryError", line)
        lst = os.path.join(self.tmp, "list.json")
        with open(lst, "w") as fh:
            fh.write('{"truncated": [')
        self._assert_one_nodata(lst)

    def test_a_directory_in_place_of_the_file(self):
        d = os.path.join(self.tmp, "a-dir.json")
        os.makedirs(d, exist_ok=True)
        self._assert_one_nodata(d)

    def test_the_message_hides_the_home_folder(self):
        line = self._assert_one_nodata(os.path.join(self.tmp, "absent2.json"))
        self.assertIn("~/", line)
        self.assertNotIn(os.path.realpath(_HOME), line)
        self.assertNotIn(_HOME, line)

    def test_registry_or_nodata_never_raises(self):
        absent = os.path.join(self.tmp, "absent3.json")
        for path in (absent, "bad\x00path", 42, ["x"]):
            reg, msg = jev_seam.registry_or_nodata(path)
            self.assertIsNone(reg, path)
            self.assertTrue(msg.startswith("Jev is not installed here:"), msg)
            self.assertIn("every Jev check reads NO-DATA", msg)
        self.assertEqual(jev_seam.registry_or_nodata(absent),
                         jev_seam.registry_or_nodata(absent))
        # None and "" fall to the default path: the checkout registry loads.
        for path in (None, ""):
            reg, msg = jev_seam.registry_or_nodata(path)
            self.assertEqual(msg, "")
            self.assertIsInstance(reg, list)

    def test_a_good_registry_loads_unchanged(self):
        reg, msg = jev_seam.registry_or_nodata(jev_seam.DEFAULT_REGISTRY_PATH)
        self.assertEqual(msg, "")
        self.assertEqual(reg, jev_seam.load_registry(jev_seam.DEFAULT_REGISTRY_PATH))

    def test_the_allowlist_names_the_two_leaf_files_and_not_the_folder(self):
        entries = export_public.load_allowlist()
        self.assertIsNotNone(entries)
        self.assertIn("data/jev-registry.json", entries)
        self.assertIn("data/jev-seams.json", entries)
        self.assertNotIn("data", entries)
        self.assertNotIn("data/", entries)
        self.assertNotIn("data/jev-canary-baseline.json", entries)

    def test_an_off_seam_still_costs_nothing(self):
        proc = self._cli(os.path.join(self.tmp, "absent4.json"), self.off)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertNotIn("NO-DATA", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
