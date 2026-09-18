"""TOKEN-02 calibration: a pack holds only its wave, names what is missing,
and is refused rather than truncated when over the cap."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context_pack as P  # noqa: E402


class ContextPack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", "a.py"), "w") as fh:
            fh.write('def alpha(x, y=2):\n    """Adds."""\n    return x + y\n\nclass Beta:\n    pass\n')
        with open(os.path.join(self.root, "scripts", "bad.py"), "w") as fh:
            fh.write("def (:\n")
        self.plan = os.path.join(self.root, "wbs.json")
        with open(self.plan, "w") as fh:
            json.dump({"units": [
                {"id": "U1", "wave": 1, "objective": "one", "owns": ["scripts/a.py", "scripts/new.py"]},
                {"id": "U2", "wave": 1, "objective": "two", "owns": ["scripts/bad.py"]},
                {"id": "U3", "wave": 2, "objective": "OTHER-WAVE", "owns": []}]}, fh)
        self.contract = os.path.join(self.root, "c.md")
        with open(self.contract, "w") as fh:
            fh.write("CONTRACT-TEXT\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_pack(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = P.main(["--plan", self.plan, "--contract", self.contract,
                           "--root", self.root] + list(extra))
        return code, out.getvalue()

    def test_pack_holds_its_wave_signatures_and_contract(self):
        code, text = self.run_pack("--wave", "1")
        self.assertEqual(code, 0)
        self.assertIn("CONTRACT-TEXT", text)
        self.assertIn("def alpha(x, y=2)", text)
        self.assertIn("Adds.", text)
        self.assertIn("class Beta", text)
        self.assertNotIn("OTHER-WAVE", text)

    def test_missing_and_unparseable_files_are_named(self):
        code, text = self.run_pack("--wave", "1")
        missing = text.split("## MISSING")[1]
        self.assertIn("scripts/new.py", missing)
        self.assertIn("scripts/bad.py", missing)

    def test_over_cap_is_refused_not_truncated(self):
        code, text = self.run_pack("--wave", "1", "--max-bytes", "50")
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", text)
        self.assertNotIn("CONTRACT-TEXT", text)

    def test_unwritable_out_is_refused_not_a_traceback(self):
        code, text = self.run_pack("--wave", "1", "--out", "/dev/null/x/pack.md")
        self.assertEqual(code, 2)
        self.assertIn("cannot write", text)

    def test_empty_wave_is_no_data(self):
        code, text = self.run_pack("--wave", "9")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", text)


if __name__ == "__main__":
    unittest.main()
