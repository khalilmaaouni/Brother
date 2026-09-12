import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import public_host_truth as truth


class PublicHostTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        (self.root / ".claude-plugin").mkdir()
        (self.root / "bundle" / ".codex-plugin").mkdir(parents=True)
        (self.root / "bundle" / ".cursor-plugin").mkdir(parents=True)
        for relative in (".claude-plugin/marketplace.json",
                         "bundle/.codex-plugin/plugin.json",
                         "bundle/.cursor-plugin/plugin.json"):
            path = self.root / relative
            path.write_text(json.dumps({"name": "brother"}), encoding="utf-8")
        (self.root / "docs/reference").mkdir(parents=True)
        self._write_docs("Claude Code\nCodex\nCursor\n")

    def tearDown(self):
        self.temp.cleanup()

    def _write_docs(self, text):
        (self.root / "README.md").write_text(text, encoding="utf-8")
        (self.root / "docs/reference/install-matrix.md").write_text(
            text, encoding="utf-8")

    def test_all_shipped_hosts_are_documented(self):
        hosts, problems = truth.check(self.root)
        self.assertEqual(("Claude Code", "Codex", "Cursor"), hosts)
        self.assertEqual((), problems)

    def test_cursor_manifest_missing_from_docs_fails(self):
        self._write_docs("Claude Code\nCodex\n")
        _hosts, problems = truth.check(self.root)
        self.assertIn("shipped host missing from public docs: Cursor", problems)

    def test_docs_inventing_host_fails(self):
        self._write_docs("Claude Code\nCodex\nCursor\n")
        (self.root / "docs/reference/install-matrix.md").write_text(
            "| Host | Surface | Note |\n| --- | --- | --- |\n"
            "| Neon Host | plugin | invented |\n", encoding="utf-8")
        _hosts, problems = truth.check(self.root)
        self.assertIn("public docs name host without shipped surface: Neon Host", problems)


if __name__ == "__main__":
    unittest.main()
