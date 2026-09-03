"""Calibration for scripts/authority_path_coverage.py's registrations().

The behaviour under test: a hooks.json this pass cannot open or parse must be
named, never dropped through a bare `except Exception: continue`. Before the
fix, a malformed sibling file made every hook it declared invisible to the
coverage report with no record that anything had been skipped. This asserts
that a good file's registrations still surface, that a bad file's failure is
reported by name in the second return value, and that a directory holding
only bad files still returns an empty (not missing) registrations list.
"""
import json
import pathlib
import tempfile
import unittest

import authority_path_coverage as apc


class Registrations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_hooks(self, plugin_name, content):
        d = self.root / "plugins" / plugin_name / "hooks"
        d.mkdir(parents=True)
        (d / "hooks.json").write_text(content, encoding="utf-8")

    def test_a_good_file_still_registers(self):
        self._write_hooks("good", json.dumps({
            "hooks": {"PreToolUse": [{"matcher": "Bash",
                                       "hooks": [{"command": 'python3 "/x/sbe_thing.py"'}]}]}
        }))
        regs, unread = apc.registrations(self.root)
        self.assertIn(("Bash", "sbe_thing.py"), regs)
        self.assertEqual(unread, [])

    def test_a_malformed_file_is_named_not_silently_dropped(self):
        self._write_hooks("good", json.dumps({
            "hooks": {"PreToolUse": [{"matcher": "Bash",
                                       "hooks": [{"command": 'python3 "/x/sbe_thing.py"'}]}]}
        }))
        self._write_hooks("bad", "{not valid json")
        regs, unread = apc.registrations(self.root)
        # The good sibling's registration still surfaces...
        self.assertIn(("Bash", "sbe_thing.py"), regs)
        # ...and the bad file is named, not swallowed.
        self.assertEqual(len(unread), 1)
        self.assertIn("bad", unread[0])
        self.assertIn("hooks.json", unread[0])

    def test_all_bad_returns_empty_registrations_and_one_named_failure(self):
        self._write_hooks("bad", "{not valid json")
        regs, unread = apc.registrations(self.root)
        self.assertEqual(regs, [])
        self.assertEqual(len(unread), 1)


if __name__ == "__main__":
    unittest.main()
