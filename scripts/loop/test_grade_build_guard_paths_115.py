#!/usr/bin/env python3
"""Security review 2026-10-16, finding 2: grade_build.dest, the one check every build writer goes through, refuses the
gate ledger and every other path under logs/, and the two scripts that write the whole-gate PASS record
(scripts/required_fast.sh, scripts/gate_pair.sh). Before: dest(root, "logs/gate-ledger.jsonl") and
dest(root, "scripts/required_fast.sh") both returned a path, so a build could forge the record its own push is judged by,
or rewrite the gate that writes it. A normal loop file is still accepted.

Run: python3 -B scripts/loop/test_grade_build_guard_paths_115.py
"""
import os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grade_build as G   # noqa: E402

REFUSED = ("logs/gate-ledger.jsonl", "logs/x", "LOGS/Gate-Ledger.jsonl", "logs/deep/er.txt",
           "scripts/required_fast.sh", "scripts/gate_pair.sh", "Scripts/Required_Fast.sh")
ACCEPTED = ("scripts/loop/some_tool.py", "docs/logs/notes.md", "scripts/logs_reader.py")


class DestRefusesTheGateRecordPaths(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gb-paths-115-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_every_gate_record_path_is_refused(self):
        for rel in REFUSED:
            full, why = G.dest(self.root, rel)
            self.assertIsNone(full, rel)
            self.assertIn("never written by a build", why, rel)

    def test_a_normal_path_is_still_accepted(self):
        for rel in ACCEPTED:
            full, why = G.dest(self.root, rel)
            self.assertEqual((full, why), (os.path.join(self.root, rel), ""), rel)


if __name__ == "__main__":
    unittest.main()
