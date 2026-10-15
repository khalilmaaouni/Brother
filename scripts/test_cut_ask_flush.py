#!/usr/bin/env python3
"""CF1.c: the cut's approval question is asked only after the input queue is
flushed, so a `y` typed ahead cannot answer it. Cases drive terminal_ask and
flush_input through injected seams (run everywhere), a real pseudo terminal
pair (skipped as NO-DATA when the host refuses one), and the real entry point
(cut.main reaching its approval ask with no ask passed).

Exit contract: 0 all pass, 1 a failure. Python 3 stdlib only, no network, no
ps, no HOME read. No em or en dashes anywhere in this file.
"""
import builtins
import io
import os
import select
import subprocess
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402

try:
    import termios
except ImportError:  # a host without termios
    termios = None


class Boom(object):
    pass


class TestFlushThenAsk(unittest.TestCase):
    def test_the_flush_runs_before_the_prompt_is_read(self):
        events = []
        out = C.terminal_ask(
            "Q? ",
            flush=lambda: events.append("flush") or "",
            read=lambda p: events.append("read:" + p) or "n")
        self.assertEqual(out, "n")
        self.assertEqual(events, ["flush", "read:Q? "])

    def test_a_flush_that_fails_on_a_terminal_declines_and_never_reads(self):
        reads = []
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            out = C.terminal_ask("Q? ", flush=lambda: "OSError: boom",
                                 read=lambda p: reads.append(p) or "y")
        self.assertEqual(out, "")
        self.assertEqual(reads, [])
        self.assertIn("REFUSED: could not clear typed ahead input "
                      "(OSError: boom); declining, nothing pushed",
                      buf.getvalue())

    def test_a_flusher_that_raises_or_answers_a_non_string_declines(self):
        for bad in (lambda: (_ for _ in ()).throw(RuntimeError("x")),
                    lambda: None, lambda: 0, lambda: ["e"]):
            reads = []
            with mock.patch.object(sys, "stdout", io.StringIO()):
                out = C.terminal_ask("Q? ", flush=bad,
                                     read=lambda p: reads.append(p) or "y")
            self.assertEqual((out, reads), ("", []))

    def test_a_hostile_prompt_is_refused_at_the_door(self):
        for bad in (None, b"Q?", 5, ["Q?"], object()):
            calls = []
            with self.assertRaises(ValueError):
                C.terminal_ask(bad, flush=lambda: calls.append("f") or "",
                               read=lambda p: calls.append("r") or "y")
            self.assertEqual(calls, [])

    def test_an_empty_prompt_is_asked_as_is(self):
        self.assertEqual(
            C.terminal_ask("", flush=lambda: "", read=lambda p: "[%s]" % p),
            "[]")

    def test_a_non_terminal_keeps_todays_behavior(self):
        flushed = []

        def eof(prompt):
            raise EOFError()

        with mock.patch.object(C, "_stdin_is_tty", return_value=False), \
             mock.patch.dict(sys.modules, {"termios": Boom()}):
            self.assertEqual(C.flush_input(), "")
            with self.assertRaises(EOFError):
                C.terminal_ask("Q? ", read=eof)
        self.assertEqual(
            C.flush_input(isatty=lambda: False,
                          flush=lambda: flushed.append(1)), "")
        self.assertEqual(flushed, [])

    def test_a_terminal_flushes_once_and_a_second_call_clears_an_empty_queue(self):
        flushed = []
        for _ in range(2):
            self.assertEqual(
                C.flush_input(isatty=lambda: True,
                              flush=lambda: flushed.append(1)), "")
        self.assertEqual(flushed, [1, 1])

    def test_a_flush_that_raises_midway_reads_as_a_failure(self):
        def broken():
            raise OSError("no tty")

        why = C.flush_input(isatty=lambda: True, flush=broken)
        self.assertIn("OSError", why)
        self.assertIn("no tty", why)

    def test_an_unknown_terminal_answer_flushes_nothing_and_declines_nothing(self):
        # isatty answering a non True value reads as no terminal, as
        # _stdin_is_tty itself does.
        flushed = []
        for odd in (None, 1, "yes", 0):
            self.assertEqual(
                C.flush_input(isatty=lambda o=odd: o,
                              flush=lambda: flushed.append(1)), "")
        self.assertEqual(flushed, [])

    @unittest.skipIf(termios is None, "termios absent: NO-DATA")
    def test_the_default_flush_discards_unread_input_on_stdin(self):
        class Fake(object):
            def fileno(self):
                return 7

        with mock.patch.object(sys, "stdin", Fake()), \
             mock.patch.object(termios, "tcflush") as tc:
            self.assertEqual(C.flush_input(isatty=lambda: True), "")
        tc.assert_called_once_with(7, termios.TCIFLUSH)

    def test_a_host_without_termios_or_a_failing_tcflush_reads_as_failure(self):
        with mock.patch.dict(sys.modules, {"termios": None}):
            why = C.flush_input(isatty=lambda: True)
            self.assertIn("termios", why)
            self.assertIn("Error", why)
        if termios is not None:
            with mock.patch.object(sys, "stdin", io.StringIO("")):
                # StringIO.fileno raises: a failure in words, not a crash.
                why = C.flush_input(isatty=lambda: True)
            self.assertTrue(why)


CHILD = r"""
import sys
sys.path.insert(0, sys.argv[1])
import cut as C
print("READY", flush=True)
ans = C.terminal_ask("Q? ")
print("ANSWER=" + repr(ans), flush=True)
"""


class TestPseudoTerminal(unittest.TestCase):
    def test_a_typed_ahead_y_on_a_terminal_does_not_answer(self):
        import pty
        try:
            master, slave = pty.openpty()
        except (OSError, ImportError, AttributeError) as exc:
            self.skipTest("NO-DATA: host refuses a pseudo terminal: %s" % exc)
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        # The typed ahead y waits in the terminal's input queue, unread.
        os.write(master, b"y\n")
        proc = subprocess.Popen(
            [sys.executable, "-B", "-c", CHILD, HERE],
            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(proc.stderr.close)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.kill)
        got = b""
        sent = False
        deadline = time.time() + 60
        while time.time() < deadline and b"ANSWER=" not in got:
            r, _, _ = select.select([proc.stdout, master], [], [], 0.2)
            if master in r:
                try:
                    os.read(master, 4096)  # drain the echo
                except OSError:
                    pass
            if proc.stdout in r:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                got += chunk
            if not sent and b"Q? " in got:
                os.write(master, b"n\n")  # typed AFTER the question
                sent = True
        proc.wait(timeout=30)
        text = got.decode("utf-8", "replace")
        self.assertIn("ANSWER='n'", text, text + proc.stderr.read().decode())


class TestMainDefaultAsker(unittest.TestCase):
    """The real entry point: cut.main with no ask reaches the approval
    question through terminal_ask, flush first."""

    @classmethod
    def setUpClass(cls):
        import test_cut as T
        cls.T = T
        T.setUpModule()  # the cut harness patches; undone in tearDownClass

    @classmethod
    def tearDownClass(cls):
        unittest.case.doModuleCleanups()

    def _run(self, ask):
        T = self.T
        r = T.Runner(T.GREEN)
        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(T.FakeBmStore, T.BM_PATH, None)), \
             mock.patch.object(C, "release_paths",
                               return_value=["scripts/", "bundle/"]):
            code = C.main(["--no-release-page", "a test about something else"],
                          root="/fake/root", runner=r, ask=ask,
                          terms_file=T.EMPTY_TERMS_FILE)
        del C._BM_STORE_CACHE[:]
        return code, r

    def test_main_uses_the_flushing_asker_by_default_and_a_given_ask_wins(self):
        events = []
        with mock.patch.object(C, "flush_input",
                               side_effect=lambda: events.append("flush") or ""), \
             mock.patch.object(builtins, "input",
                               side_effect=lambda p: events.append("read") or "n"):
            code, r = self._run(None)
        self.assertEqual(code, C.EXIT_OK)
        self.assertEqual(events, ["flush", "read"])
        self.assertNotIn("export_public.py", r.calls)
        # a caller's own ask is untouched: no flush, no input
        events[:] = []
        mine = []
        with mock.patch.object(C, "flush_input",
                               side_effect=lambda: events.append("flush") or ""), \
             mock.patch.object(builtins, "input",
                               side_effect=lambda p: events.append("read") or "n"):
            code, r = self._run(lambda p: mine.append(p) or "n")
        self.assertEqual((code, events, len(mine)), (C.EXIT_OK, [], 1))

    def test_a_failed_flush_through_main_declines_and_pushes_nothing(self):
        reads = []
        with mock.patch.object(C, "flush_input", return_value="OSError: x"), \
             mock.patch.object(builtins, "input",
                               side_effect=lambda p: reads.append(p) or "y"):
            code, r = self._run(None)
        self.assertEqual(code, C.EXIT_OK)
        self.assertEqual(reads, [])
        self.assertNotIn("export_public.py", r.calls)


def tearDownModule():
    # Python 3.9 runs module cleanups only when this hook exists.
    pass


if __name__ == "__main__":
    unittest.main()
