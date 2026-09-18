"""TRIGGER-05 calibration. Runs the real tool against the real repeat-guard
hook in a temporary HOME, so the matching under test is the guard's own."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "trigger_precision.py")
HOOK = os.path.expanduser("~/.claude/hooks/repeat_guard.py")


def call(text):
    return json.dumps({"message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": text}}]}})


class TriggerPrecision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, ".claude", "repeat-guard"))
        self.lessons = os.path.join(self.home, ".claude", "repeat-guard", "lessons.jsonl")
        self.tdir = os.path.join(self.home, "t")
        os.makedirs(self.tdir)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lessons, texts):
        with open(self.lessons, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(l) + "\n" for l in lessons))
        with open(os.path.join(self.tdir, "a.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(call(t) + "\n" for t in texts))

    def run_tool(self, *extra):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run(
            [sys.executable, TOOL, "--hook", HOOK,
             "--transcripts", os.path.join(self.tdir, "*.jsonl")] + list(extra),
            capture_output=True, text=True, env=env, timeout=60)

    def read_lessons(self):
        with open(self.lessons, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def test_noisy_lesson_flagged_and_silenced_rare_one_kept(self):
        texts = ["ls noisy"] * 30 + ["rare thing"] + ["echo ok"] * 269
        self.write([{"trigger": "noisy", "note": "a"}, {"trigger": "rare", "note": "b"}], texts)
        p = self.run_tool()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("OVER", p.stdout)
        p = self.run_tool("--apply")
        self.assertEqual(p.returncode, 0, p.stdout)
        got = {l["trigger"]: bool(l.get("silent")) for l in self.read_lessons()}
        self.assertEqual(got, {"noisy": True, "rare": False})
        p = self.run_tool()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("SILENT", p.stdout)

    def test_too_few_calls_is_no_data_never_clean(self):
        self.write([{"trigger": "x", "note": "a"}], ["echo x"] * 10)
        p = self.run_tool()
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("NO-DATA", p.stdout)

    def test_no_transcripts_is_no_data(self):
        self.write([{"trigger": "x", "note": "a"}], [])
        os.remove(os.path.join(self.tdir, "a.jsonl"))
        p = self.run_tool()
        self.assertEqual(p.returncode, 2, p.stdout)

    def test_apply_keeps_an_unreadable_line(self):
        texts = ["ls noisy"] * 30 + ["echo ok"] * 270
        self.write([{"trigger": "noisy", "note": "a"}], texts)
        with open(self.lessons, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        self.run_tool("--apply")
        with open(self.lessons, encoding="utf-8") as fh:
            self.assertIn("{not json", fh.read())


if __name__ == "__main__":
    unittest.main()
