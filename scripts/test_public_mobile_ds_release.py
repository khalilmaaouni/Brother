import pathlib
import tempfile
import unittest
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import export_public

class PublicRelease(unittest.TestCase):
    def test_export_contains_mobile_and_claim_verification(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        allow = [line.strip() for line in (root / "docs/plan/EXPORT-ALLOWLIST.txt").read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
        required = ["scripts/mobile_workflow.py", "scripts/mobile_design.py", "scripts/native_evidence.py", "scripts/test_mobile_workflow.py", "docs/how-to/native-mobile-workflow.md", "docs/how-to/native-evidence.md", "products/brotherds/bds.py", "products/brotherds/tests/run_all.sh", "products/brotherds/README.md"]
        with tempfile.TemporaryDirectory() as folder:
            export_public.build_export_tree(folder, allow, root=str(root))
            for name in required:
                self.assertTrue((pathlib.Path(folder)/name).is_file(), name)
            self.assertFalse((pathlib.Path(folder)/"products/brotherds/research").exists())

if __name__ == "__main__":
    unittest.main()
