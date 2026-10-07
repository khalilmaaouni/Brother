import hashlib
import pathlib
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import supply_chain_gate as g


class PinAndLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_pin_spec_exact(self):
        self.assertEqual(g.check_pin_spec("2.2.1"), (True, "exact"))
        self.assertFalse(g.check_pin_spec(">=2.2.1")[0])
        self.assertFalse(g.check_pin_spec("latest")[0])
        self.assertTrue(g.check_pin_spec("a" * 40)[0])
        self.assertFalse(g.check_pin_spec("1.0.0 x")[0])
        self.assertFalse(g.check_pin_spec(None)[0])

    def test_lockfile_sha256(self):
        p = self.root / "a.txt"
        p.write_bytes(b"abc")
        self.assertEqual(g.lockfile_sha256(p), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(g.lockfile_sha256(self.root / "missing.txt"), "")
        with self.assertRaises(ValueError):
            g.lockfile_sha256("not-a-path")

    def test_verify_pins_blocks_range(self):
        row = {"name": "pkg", "version_pinned": ">=1.0.0", "pin_location": "pyproject.toml:1"}
        self.root.joinpath("pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        findings = g.verify_pins([row], [self.root / "pyproject.toml"], self.root)
        self.assertTrue(any(f["severity"] == "BLOCK" and f["code"] == "pin.not_exact" for f in findings))

    def test_verify_lockfiles_mismatch(self):
        lock = self.root / "package-lock.json"
        lock.write_bytes(b"abc")
        row = {"name": "pkg", "lockfile_path": "package-lock.json", "lockfile_sha256": "0" * 64}
        findings = g.verify_lockfiles([row], self.root)
        self.assertTrue(any(f["severity"] == "BLOCK" and f["code"] == "lock.mismatch" for f in findings))

    def test_verify_manifest_coverage_extra_dep(self):
        pp = self.root / "pyproject.toml"
        pp.write_text(
            "[project]\n"
            "name = \"demo\"\n"
            "version = \"0.1.0\"\n"
            "dependencies = [\"alpha==1.0.0\", \"beta==2.0.0\"]\n",
            encoding="utf-8",
        )
        rows = [{"name": "alpha"}]
        findings = g.verify_manifest_coverage(rows, [pp], self.root)
        self.assertTrue(any(f["severity"] == "BLOCK" and f["code"] == "coverage.extra_manifest_dep" for f in findings))

    def test_hostile_inputs(self):
        with self.assertRaises(ValueError):
            g.verify_pins(None, [], self.root)
        with self.assertRaises(ValueError):
            g.verify_lockfiles("not a list", self.root)
        with self.assertRaises(ValueError):
            g.verify_manifest_coverage([], None, self.root)
        with self.assertRaises(ValueError):
            g.verify_manifest_coverage([], ["not-a-path"], self.root)
        self.assertFalse(g.check_pin_spec(123)[0])

    def test_hostile_manifest_bytes(self):
        req = self.root / "requirements.txt"
        req.write_bytes(b"\xff\xfe")
        findings = g.verify_manifest_coverage([], [req], self.root)
        self.assertTrue(any(f["severity"] == "NO-DATA" and f["code"] == "coverage.unparsable" for f in findings))

    def test_verify_manifest_coverage_newline_blocks(self):
        bad = self.root / ("pyproject.toml\nx")
        findings = g.verify_manifest_coverage([], [bad], self.root)
        self.assertTrue(any(f["severity"] == "BLOCK" and f["code"] == "coverage.newline" for f in findings))


if __name__ == "__main__":
    unittest.main()
