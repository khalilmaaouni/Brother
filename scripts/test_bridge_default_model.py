#!/usr/bin/env python3
"""test_bridge_default_model.py: bridge_default_model.py driven backwards.

PROPERTY PROTECTED: check_pinned() must report a real match (return the
value, no exception) only when the bridge's live DEFAULT_MODEL is byte for
byte equal to the pinned value, and must never report a match when the
bridge could not be read at all (NO-DATA, BridgeUnreadable) or when the live
value differs from the pin, even by one character (a real answer,
DefaultModelDrift). A bridge that cannot be read is never treated as
matching.

EDGE LIST WALKED:
  empty              -> a source file with no DEFAULT_MODEL assignment at
                        all (test_no_assignment_is_unreadable)
  exactly one         -> a real assignment among unrelated source, found
                        correctly (test_read_finds_value_among_noise)
  many                -> two distinct DEFAULT_MODEL assignments in one file:
                        refused as ambiguous rather than picking either
                        (test_two_distinct_assignments_is_unreadable)
  unknown value       -> a reference to DEFAULT_MODEL used as a VALUE
                        (default=DEFAULT_MODEL, as the real bridge's own
                        argparse call does) is never mistaken for an
                        assignment (test_reference_is_not_an_assignment)
  corrupt/truncated   -> a path that does not exist on disk at all
                        (test_missing_file_is_unreadable)
  already done        -> check_pinned() called with a pin that already
                        equals the live value returns cleanly, twice in a
                        row, with no side effect between calls
                        (test_check_pinned_match_is_idempotent)
  the actor is the same as last time -> the one-character mutation test
                        below is exactly this: the live value looks almost
                        identical to the pin, and the fix must still catch
                        it rather than treating "close enough" as a match
                        (test_one_character_drift_is_still_drift)
  concurrent second actor -> N/A: every function here is pure text
                        processing over one path argument, no shared or
                        persisted state, so two callers reading the same
                        file at the same time cannot corrupt each other
  the module's own real target -> BRIDGE_PATH and PINNED_DEFAULT_MODEL are
                        exercised against a fixture that mimics the real
                        or_ask.py shape (a reference use on one line, the
                        real assignment on another, surrounded by noise),
                        never against the real live file on disk: a test
                        must not depend on what happens to be installed on
                        the machine running it
                        (test_read_finds_value_among_noise)

MUTATION THIS SUITE IS BUILT TO CATCH: if check_pinned() is ever weakened to
return `live` without comparing it to `pinned` (for example the `if live !=
pinned:` guard is deleted), test_one_character_drift_is_still_drift goes red,
because it asserts DefaultModelDrift is raised for a live value that differs
from the pin by exactly one character.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_default_model as BDM  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


class BridgeDefaultModelTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._rmtree, self.tmpdir)

    @staticmethod
    def _rmtree(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    # -- read_bridge_default_model -----------------------------------

    def test_read_finds_value_among_noise(self):
        content = (
            '#!/usr/bin/env python3\n'
            '"""A fixture shaped like the real bridge."""\n'
            'import argparse\n'
            '\n'
            'KEYCHAIN_SERVICE = "openrouter"\n'
            'DEFAULT_MODEL = "provider/model-a"\n'
            'MODEL_ALIASES = {"muse": "provider/model-a"}\n'
            '\n'
            'def main():\n'
            '    p = argparse.ArgumentParser()\n'
            '    p.add_argument("--model", default=DEFAULT_MODEL)\n'
            '    return p\n'
        )
        path = self._write("noise.py", content)
        self.assertEqual(BDM.read_bridge_default_model(path), "provider/model-a")

    def test_reference_is_not_an_assignment(self):
        # Only the `default=DEFAULT_MODEL, ...` reference exists on this
        # line; there must be no false match treating the reference itself
        # as a second assignment.
        content = 'p.add_argument("--model", default=DEFAULT_MODEL)\n'
        path = self._write("reference_only.py", content)
        with self.assertRaises(BDM.BridgeUnreadable):
            BDM.read_bridge_default_model(path)

    def test_no_assignment_is_unreadable(self):
        content = '# no default model here\nOTHER = "value"\n'
        path = self._write("empty.py", content)
        with self.assertRaises(BDM.BridgeUnreadable):
            BDM.read_bridge_default_model(path)

    def test_two_distinct_assignments_is_unreadable(self):
        content = (
            'DEFAULT_MODEL = "provider/model-a"\n'
            '# some other code in between\n'
            'DEFAULT_MODEL = "provider/model-b"\n'
        )
        path = self._write("ambiguous.py", content)
        with self.assertRaises(BDM.BridgeUnreadable) as ctx:
            BDM.read_bridge_default_model(path)
        msg = str(ctx.exception)
        self.assertIn("provider/model-a", msg)
        self.assertIn("provider/model-b", msg)

    def test_missing_file_is_unreadable(self):
        path = os.path.join(self.tmpdir, "does_not_exist.py")
        with self.assertRaises(BDM.BridgeUnreadable):
            BDM.read_bridge_default_model(path)

    # -- check_pinned ---------------------------------------------------

    def test_check_pinned_match_is_idempotent(self):
        path = self._write("match.py", 'DEFAULT_MODEL = "provider/model-a"\n')
        self.assertEqual(BDM.check_pinned(path, pinned="provider/model-a"),
                         "provider/model-a")
        # calling it again must not mutate state or change the answer
        self.assertEqual(BDM.check_pinned(path, pinned="provider/model-a"),
                         "provider/model-a")

    def test_one_character_drift_is_still_drift(self):
        # "provider/model-a" vs "provider/model-b" differs by exactly one
        # character. This is the case a weakened comparison (or one deleted
        # entirely) would let through as a silent match.
        path = self._write("drift.py", 'DEFAULT_MODEL = "provider/model-b"\n')
        with self.assertRaises(BDM.DefaultModelDrift) as ctx:
            BDM.check_pinned(path, pinned="provider/model-a")
        exc = ctx.exception
        self.assertEqual(exc.pinned, "provider/model-a")
        self.assertEqual(exc.live, "provider/model-b")
        msg = str(exc)
        self.assertIn("provider/model-a", msg)
        self.assertIn("provider/model-b", msg)

    def test_unreadable_bridge_never_reads_as_a_match(self):
        # A missing file must raise BridgeUnreadable, never DefaultModelDrift
        # and never a silent return: NO-DATA is not a pass, worker contract
        # rule 1.
        path = os.path.join(self.tmpdir, "gone.py")
        with self.assertRaises(BDM.BridgeUnreadable):
            BDM.check_pinned(path, pinned="anything")

    # -- main() CLI -------------------------------------------------------

    def test_main_pass_drift_nodata_exit_codes(self):
        matching = self._write(
            "matching.py", 'DEFAULT_MODEL = "%s"\n' % BDM.PINNED_DEFAULT_MODEL)
        self.assertEqual(BDM.main(["--bridge", matching]), 0)

        mismatched = self._write(
            "mismatched.py", 'DEFAULT_MODEL = "definitely/not-pinned"\n')
        self.assertEqual(BDM.main(["--bridge", mismatched]), 1)

        missing = os.path.join(self.tmpdir, "missing.py")
        self.assertEqual(BDM.main(["--bridge", missing]), 2)

    def test_pinned_constant_is_a_nonempty_string(self):
        # The pin itself must be a real value, never blank: an empty pin
        # would make every bridge with an empty DEFAULT_MODEL read as PASS.
        self.assertIsInstance(BDM.PINNED_DEFAULT_MODEL, str)
        self.assertTrue(BDM.PINNED_DEFAULT_MODEL.strip())


if __name__ == "__main__":
    unittest.main()
