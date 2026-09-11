"""D2, 2026-09-10: a dead embed machine must never look like a run with nothing to do.

The shipped shim bm-embed-bge execs ../.venv-embed/bin/python, which no install
contains, so it exits 126 and writes nothing to stdout. Before this test existed,
_embed_texts_subprocess ignored the exit code, parsed the empty stdout into an
empty dict, and an empty dict is not None, so the caller printed
"embedded 0 of N pending note(s)". Total failure, success-shaped output.

These cases pin the distinction the caller depends on: None means no machine and
triggers the NO-DATA branch; a dict means the machine ran.
"""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault  # noqa: E402


def _shim(dirpath, name, body):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


class EmbedExitCode(unittest.TestCase):
    def _run_with(self, body):
        d = tempfile.mkdtemp()
        path = _shim(d, "fake-embed", body)
        real = bm_vault._embed_bin
        bm_vault._embed_bin = lambda: path
        try:
            return bm_vault._embed_texts_subprocess([(1, "hello")])
        finally:
            bm_vault._embed_bin = real

    def test_missing_interpreter_returns_none_not_empty_dict(self):
        """The real defect: the shim cannot exec its interpreter, exits non-zero
        with empty stdout. That must be None, never {}."""
        got = self._run_with('#!/bin/sh\nexec "$(dirname "$0")/../.venv-embed/bin/python" "$@"\n')
        self.assertIsNone(
            got,
            "a non-zero exit with empty stdout returned %r; an empty dict is not None, "
            "so the caller takes the success branch and reports embedding 0 notes" % (got,))

    def test_any_non_zero_exit_returns_none(self):
        got = self._run_with('#!/bin/sh\necho "model failed to load" >&2\nexit 3\n')
        self.assertIsNone(got)

    def test_non_zero_exit_names_itself_on_stderr(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self._run_with('#!/bin/sh\necho "model failed to load" >&2\nexit 3\n')
        said = buf.getvalue()
        self.assertIn("NO-DATA", said)
        self.assertIn("exited 3", said)
        self.assertIn("model failed to load", said,
                      "the machine's own first stderr line must survive, it is the only clue")

    def test_a_working_machine_still_returns_its_vectors(self):
        body = ('#!/bin/sh\n'
                'while read -r _line; do :; done\n'
                'printf \'{"id": 1, "v": [0.5, 0.25]}\\n\'\n')
        got = self._run_with(body)
        self.assertEqual(got, {1: [0.5, 0.25]})

    def test_empty_stdout_on_a_zero_exit_is_still_a_dict(self):
        """A machine that ran and embedded nothing is NOT the same as no machine.
        That case must stay a dict, or this fix would hide a different truth."""
        got = self._run_with('#!/bin/sh\nwhile read -r _l; do :; done\nexit 0\n')
        self.assertEqual(got, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
