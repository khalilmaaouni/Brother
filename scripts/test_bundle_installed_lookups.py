"""The installed bundle reaches the vault intake and the numbers gate (architecture review 2026-10-05).

An installed plugin is bundle/ alone: there is no products/ directory beside runtime/, so a lookup that only
tries products/... reads NO-DATA on every installed run. Each case copies bundle/ into a temp directory, with
nothing else from the repository, and imports the runtime modules there under python -I (no PYTHONPATH, no
user site), the way an installed host runs them.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(os.path.dirname(HERE), "bundle")

PROBE = r"""
import sys
sys.path.insert(0, ".")
import receipt_door, brother_run
fn, why = receipt_door._sbe_gate_numbers()
print("GATE", callable(fn), why)
mod = brother_run._load_bm_vault_intake()
print("INTAKE", mod is not None)
print("RECURRENCE", brother_run._load_bm_recurrence() is not None)
"""


class InstalledBundleLookups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="installed-lookups-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copytree(BUNDLE, os.path.join(self.tmp, "bundle"), symlinks=True)
        self.runtime = os.path.join(self.tmp, "bundle", "runtime")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "bundle", "products")))

    def probe(self):
        r = subprocess.run([sys.executable, "-I", "-B", "-c", PROBE], cwd=self.runtime,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_the_numbers_gate_is_reached_from_the_bundle(self):
        self.assertIn("GATE True", self.probe())

    def test_the_vault_intake_is_reached_from_the_bundle(self):
        self.assertIn("INTAKE True", self.probe())

    def test_the_recurrence_reader_is_reached_from_the_bundle(self):
        self.assertIn("RECURRENCE True", self.probe())

    def test_a_moved_head_reads_no_data_on_an_install_never_a_false_fail(self):
        # the installed bundle ships no brothersbe.evidence, so a receipt one commit behind HEAD cannot be checked
        # for binding there: the answer is NO-DATA (review 2026-10-05: it read a false "the code it covered has moved")
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "dw"))
        git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                         *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        git("init", "-q")
        with open(os.path.join(repo, "README.md"), "w") as fh:
            fh.write("a\n")
        git("add", "-A"); git("commit", "-q", "-m", "a")
        first = git("rev-parse", "HEAD")
        example = os.path.join(os.path.dirname(BUNDLE), "products", "brothersbe", "docs", "for-engineers", "examples",
                               "data-warehouse", "numbers-manifest.json")
        with open(example) as fh:
            doc = json.load(fh)
        doc["headCommit"] = first
        with open(os.path.join(repo, "dw", "numbers-manifest.json"), "w") as fh:
            json.dump(doc, fh)
        git("add", "-A"); git("commit", "-q", "-m", "b")
        probe = ("import sys; sys.path.insert(0, 'hooks/brothersbe/tools'); import sbe_gate; "
                 "v, why = sbe_gate.gate_numbers(sys.argv[1]); print(v); print(why)")
        r = subprocess.run([sys.executable, "-I", "-B", "-c", probe, os.path.join(repo, "dw")], cwd=self.runtime,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        verdict = r.stdout.splitlines()[0]
        self.assertEqual(verdict, "NO-DATA", r.stdout)
        self.assertIn("commit binding reader", r.stdout)

    def test_a_bundle_without_the_modules_still_reads_no_data(self):
        os.remove(os.path.join(self.runtime, "hooks", "brothersbe", "tools", "sbe_gate.py"))
        os.remove(os.path.join(self.runtime, "hooks", "brothermode", "tools", "bm_vault_intake.py"))
        out = self.probe()
        self.assertIn("GATE False NO-DATA", out)
        self.assertIn("INTAKE False", out)


if __name__ == "__main__":
    unittest.main()
