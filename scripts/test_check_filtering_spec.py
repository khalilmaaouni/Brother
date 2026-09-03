"""Calibration for scripts/check_filtering_spec.py, driven backwards.

Every case asserts the EXIT CODE, never only the printed verdict, per this
estate's own recorded lesson: a gate that prints FAIL and exits 0 manufactures
a pass for every wrapper reading only the exit code.

The backwards drive: copy the REAL, currently-passing spec into a temp file,
remove one content class's row, and watch the check fail on exactly that
class. That proves the check reads the file's content rather than always
agreeing with whatever is on disk.
"""
import pathlib
import tempfile
import unittest

import check_filtering_spec as cfs


class Backwards(unittest.TestCase):

    def setUp(self):
        with open(cfs.SPEC_PATH, encoding="utf-8") as f:
            self.real_spec = f.read()
        # Sanity: the real, shipped spec must pass before any test mutates a
        # copy of it. If this fails, the fixture below proves nothing.
        self.assertIsNotNone(cfs.read_spec(cfs.SPEC_PATH))

    def run_against(self, text):
        """Write text to a temp file and return (exit_code, printed_lines)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "spec.md"
            path.write_text(text, encoding="utf-8")
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                from unittest import mock
                with mock.patch.object(cfs, "SPEC_PATH", path):
                    code = cfs.main()
            return code, buf.getvalue()

    def test_real_spec_passes(self):
        code, out = self.run_against(self.real_spec)
        self.assertEqual(code, 0, out)
        self.assertIn("PASSED", out)

    def test_removing_one_class_fails_and_names_it(self):
        """Delete every line mentioning 'credentials and keys' from a copy
        of the real spec. The check must fail and name that exact class,
        not just fail generically."""
        mutated = "\n".join(
            line for line in self.real_spec.splitlines()
            if "credentials and keys" not in line.lower()
        )
        code, out = self.run_against(mutated)
        self.assertNotEqual(code, 0, "removing a class row still passed")
        self.assertEqual(code, 1, out)
        self.assertIn("credentials and keys", out)

    def test_removing_the_default_rule_fails(self):
        mutated = self.real_spec.replace(
            "When unsure, the most private plausible owner wins; "
            "forgetting must fail safe.",
            "Ask the founder first.",
        )
        # Confirm the mutation actually removed the sentence, else this test
        # would pass for the wrong reason.
        self.assertNotIn("forgetting must fail safe", mutated.lower())
        code, out = self.run_against(mutated)
        self.assertNotEqual(code, 0, "removing the default rule still passed")
        self.assertEqual(code, 1, out)
        self.assertIn("default rule", out)

    def test_missing_file_is_no_data_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "does-not-exist.md"
            from unittest import mock
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                with mock.patch.object(cfs, "SPEC_PATH", path):
                    code = cfs.main()
        self.assertNotEqual(code, 0, "a missing file exited as a pass")
        self.assertEqual(code, 2, buf.getvalue())
        self.assertIn("NO-DATA", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
