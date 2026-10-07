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
            # Only research notes the allowlist names one by one may export; the rest of that
            # folder is private working material. (This asserted "no folder at all" until two
            # notes were allowlisted on purpose; no gate ran it, so nobody saw it go stale.)
            allowed = sorted(a for a in allow if a.startswith("products/brotherds/research/"))
            # Pinned on purpose: publishing another research note is a decision, so it must turn this red.
            self.assertEqual(allowed, ["products/brotherds/research/C1-mdm-data-science-best-practice-2026-09-12.md",
                                       "products/brotherds/research/C2-harness-and-market-practice-2026-09-12.md"])
            research = pathlib.Path(folder)/"products/brotherds/research"
            shipped = sorted(str(p.relative_to(folder)) for p in research.rglob("*") if p.is_file())
            self.assertEqual(shipped, allowed)
            private = sorted(str(p.relative_to(root)) for p in (root/"products/brotherds/research").glob("*") if p.is_file())
            self.assertTrue(set(private) - set(shipped), "the private notes this test protects are gone: rewrite it")

if __name__ == "__main__":
    unittest.main()
