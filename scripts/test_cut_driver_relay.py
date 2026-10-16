#!/usr/bin/env python3
"""CR15 (docs/plan/specs/CR15.md): the cut driver's stream relay shows a
question that ends without a line break before the answer is read.

At the 1.1.4 cut the relay copied the cut's stdout by whole lines, so the
final [y/N] question (no trailing newline, the cut then waits on stdin) was
never shown. Each case here runs the real entry point, real_runner(...,
stream=True) or cut_phase, inside a harness process whose stdin and stdout
this test holds, so the relay's output is read as it arrives and the answer
is written only after the question was seen. Every read is bounded, so the
line relay FAILS a case within seconds instead of hanging it.

Python 3.9, standard library only. No network. Writes only under a temp
directory, with HOME pointed there. No em or en dashes.
"""
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402

QUESTION_WAIT_S = 5.0
FINISH_WAIT_S = 15.0
QUESTION = "Approve cut v9.9.9 at commit abc, rehearsed by r: push tag v9.9.9? [y/N] "

# Runs the real entry point with this test holding the harness's stdin and stdout; the result goes to a file so
# the harness's stdout carries nothing but the relay.
HARNESS = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import cut_driver as D
mode, out = sys.argv[2], sys.argv[3]
if mode == "runner":
    res = list(D.real_runner(json.loads(sys.argv[4]), sys.argv[5], stream=True))
else:
    res = list(D.cut_phase(sys.argv[4], "9.9.9", "a" * 40, sys.argv[5], tty=True))
with open(out, "w", encoding="utf-8") as fh:
    json.dump(res, fh)
'''

# The cut's own shape: python -u, the question through input(), which writes it with no newline and waits.
ASKER = r'''
import sys
sys.stderr.write("note on stderr\n")
print("the cut prints a whole line first")
answer = input(%r)
print("got " + answer.strip())
''' % QUESTION

# A fake worktree's scripts/cut.py for cut_phase: asks, reads the answer, declines without pushing.
FAKE_CUT = r'''
answer = input(%r)
print("declined: %%s, nothing pushed" %% answer.strip())
''' % QUESTION


class Relay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-driver-relay-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ev = os.path.join(self.tmp, "ev")
        self.result = os.path.join(self.tmp, "result.json")

    # ---- the harness

    def spawn(self, mode, args):
        env = dict(os.environ)
        env.update({"HOME": self.tmp, "PYTHONIOENCODING": "utf-8", "BROTHER_CUT_TEST": "1",
                    "BROTHER_CUT_EVIDENCE_DIR": self.ev, "BROTHER_SCRATCH": os.path.join(self.tmp, "scratch")})
        proc = subprocess.Popen([sys.executable, "-B", "-c", HARNESS, HERE, mode, self.result] + list(args),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=self.tmp, env=env, start_new_session=True)
        self.addCleanup(self.reap, proc)
        return proc

    def reap(self, proc):
        """Kill the harness and everything it started, whatever state it is in; never hang."""
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    def read_until(self, proc, needle, timeout):
        """The relay's bytes read so far, stopping at `needle`, at EOF or at the deadline."""
        fd, buf, deadline = proc.stdout.fileno(), b"", time.time() + timeout
        while needle is None or needle not in buf:
            left = deadline - time.time()
            if left <= 0:
                break
            ready, _w, _x = select.select([fd], [], [], left)
            if not ready:
                break
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
        return buf

    def finish(self, proc, relayed):
        """(all relayed bytes, stderr text, [rc, text] the entry point returned); fails rather than hangs."""
        relayed += self.read_until(proc, None, FINISH_WAIT_S)
        try:
            proc.wait(timeout=FINISH_WAIT_S)
        except subprocess.TimeoutExpired:
            self.fail("the harness did not finish within %ss; relayed %r" % (FINISH_WAIT_S, relayed))
        err = proc.stderr.read().decode("utf-8", "replace")
        self.assertTrue(os.path.exists(self.result), "the entry point returned nothing (raised?): %s" % err)
        with open(self.result, encoding="utf-8") as fh:
            return relayed, err, json.load(fh)

    def run_child(self, code):
        """real_runner(stream=True) over `python -u -c code` with no input; (relayed bytes, stderr, result)."""
        proc = self.spawn("runner", [json.dumps([sys.executable, "-u", "-c", code]), self.tmp])
        proc.stdin.close()
        return self.finish(proc, b"")

    # ---- CR15.a: the question is shown before the answer is read

    def test_a_question_without_a_newline_is_relayed_before_the_answer(self):
        proc = self.spawn("runner", [json.dumps([sys.executable, "-u", "-c", ASKER]), self.tmp])
        seen = self.read_until(proc, QUESTION.encode(), QUESTION_WAIT_S)
        self.assertIn(QUESTION.encode(), seen,
                      "the question was not relayed within %ss with no answer written: %r" % (QUESTION_WAIT_S, seen))
        self.assertNotIn(b"got ", seen)
        proc.stdin.write(b"n\n")          # stdin is inherited: the child reads the answer the harness was given
        proc.stdin.close()
        relayed, err, (rc, text) = self.finish(proc, seen)
        want = "the cut prints a whole line first\n" + QUESTION + "got n\n"
        self.assertEqual((rc, text), (0, want))
        self.assertEqual(relayed, want.encode("utf-8"))
        self.assertIn("note on stderr", err)        # stderr is inherited, never kept
        self.assertNotIn("note on stderr", text)

    def test_the_driver_cut_phase_shows_the_question_before_the_answer(self):
        worktree = os.path.join(self.tmp, "wt")
        os.makedirs(os.path.join(worktree, "scripts"))
        with open(os.path.join(worktree, "scripts", "cut.py"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_CUT)
        body = os.path.join(self.tmp, "notes.md")
        with open(body, "w", encoding="utf-8") as fh:
            fh.write("notes\n")
        proc = self.spawn("cut", [worktree, body])
        seen = self.read_until(proc, QUESTION.encode(), QUESTION_WAIT_S)
        self.assertIn(QUESTION.encode(), seen,
                      "cut_phase did not show the question within %ss: %r" % (QUESTION_WAIT_S, seen))
        proc.stdin.write(b"n\n")
        proc.stdin.close()
        relayed, _err, (verdict, detail) = self.finish(proc, seen)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("exited 0 without pushing", detail)
        self.assertEqual(relayed, (QUESTION + "declined: n, nothing pushed\n").encode("utf-8"))
        logs = [p for p in os.listdir(self.ev) if p.startswith("driver-9.9.9-cut-")]
        self.assertEqual(len(logs), 1, logs)
        with open(os.path.join(self.ev, logs[0]), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), QUESTION + "declined: n, nothing pushed\n")

    # ---- what the chunk relay keeps

    def test_a_character_split_across_chunks_is_relayed_and_kept_whole(self):
        code = ("import os, time\nos.write(1, b'caf\\xc3')\ntime.sleep(0.3)\nos.write(1, b'\\xa9 \\xe2\\x9c')\n"
                "time.sleep(0.3)\nos.write(1, b'\\x93 done\\n')\n")
        relayed, _err, (rc, text) = self.run_child(code)
        want = u"caf\u00e9 \u2713 done\n"
        self.assertEqual((rc, text), (0, want))
        self.assertEqual(relayed, want.encode("utf-8"))

    def test_a_truncated_character_at_the_end_is_replaced_never_raised(self):
        relayed, _err, (rc, text) = self.run_child("import os\nos.write(1, b'ab\\xc3')\n")
        self.assertEqual((rc, text), (0, u"ab\ufffd"))
        self.assertEqual(relayed, u"ab\ufffd".encode("utf-8"))

    def test_a_child_killed_mid_line_keeps_its_partial_line(self):
        code = "import os, signal\nos.write(1, b'partial line, no break')\nos.kill(os.getpid(), signal.SIGKILL)\n"
        relayed, _err, (rc, text) = self.run_child(code)
        self.assertEqual((rc, text), (-signal.SIGKILL, "partial line, no break"))
        self.assertEqual(relayed, b"partial line, no break")

    def test_line_endings_read_as_text_mode_reads_them(self):
        relayed, _err, (rc, text) = self.run_child("import os\nos.write(1, b'x\\r\\ny\\rz\\r')\n")
        self.assertEqual((rc, text), (0, "x\ny\nz\n"))
        self.assertEqual(relayed, b"x\ny\nz\n")

    # ---- what stays as it was

    def test_the_non_stream_path_is_unchanged(self):
        ok = D.real_runner([sys.executable, "-c", "import sys; print('a'); sys.stderr.write('e')"], self.tmp)
        self.assertEqual(ok, (0, "a\n"))
        bad = D.real_runner([sys.executable, "-c", "import sys; print('a'); sys.stderr.write('e'); sys.exit(3)"],
                            self.tmp)
        self.assertEqual(bad, (3, "a\ne"))

    def test_a_missing_binary_or_directory_answers_127_in_both_modes(self):
        missing = os.path.join(self.tmp, "no-such-binary")
        for stream in (False, True):
            rc, text = D.real_runner([missing], self.tmp, stream=stream)
            self.assertEqual(rc, 127, (stream, text))
            self.assertIn("no-such-binary", text)
            rc, _text = D.real_runner([sys.executable, "-c", "pass"], os.path.join(self.tmp, "gone"), stream=stream)
            self.assertEqual(rc, 127, stream)


if __name__ == "__main__":
    unittest.main(verbosity=2)
