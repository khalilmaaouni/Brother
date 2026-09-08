"""land_queue's three controls, driven backwards: the lock, the concurrency
ceiling, and the timeout-red reclassification. Fake gate/merge scripts play
the part of regate.sh and merge_if_green.sh so this needs no network, no git
worktree and no real GitHub pull request.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_queue as LQ  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

import tempfile  # noqa: E402


def write_script(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/bash\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class LockRefusesALivePid(unittest.TestCase):
    def test_second_runner_is_refused_naming_the_first_pid(self):
        d = tempfile.mkdtemp()
        lock = os.path.join(d, "q.lock")
        holder = subprocess.Popen(["sleep", "5"])
        try:
            LQ.write_lock(lock, {"pid": holder.pid, "since": time.time(), "inflight": []})
            ok, note = LQ.acquire_lock(lock)
            self.assertFalse(ok)
            self.assertIn(str(holder.pid), note)
        finally:
            holder.kill()
            holder.wait()

    def test_a_dead_pids_lock_is_reclaimed_and_logged(self):
        d = tempfile.mkdtemp()
        lock = os.path.join(d, "q.lock")
        # PIDs wrap but this one is astronomically unlikely to be alive.
        dead_pid = 999999
        LQ.write_lock(lock, {"pid": dead_pid, "since": time.time(), "inflight": []})
        ok, note = LQ.acquire_lock(lock)
        self.assertTrue(ok)
        self.assertIn("reclaimed", note)
        self.assertIn(str(dead_pid), note)
        held = LQ.read_lock(lock)
        self.assertEqual(held["pid"], os.getpid())


class ConcurrencyCeilingHolds(unittest.TestCase):
    def test_never_three_overlapping_at_concurrency_two(self):
        d = tempfile.mkdtemp()
        queue = os.path.join(d, "queue.txt")
        log = os.path.join(d, "land.log")
        timeline = os.path.join(d, "timeline.log")
        with open(queue, "w", encoding="utf-8") as fh:
            fh.write("1\n2\n3\n")
        gate = os.path.join(d, "gate.sh")
        write_script(gate, """
n=$1
echo "START $n $(date +%s.%N)" >> "TIMELINE"
sleep 0.3
echo "END $n $(date +%s.%N)" >> "TIMELINE"
echo "## required_fast exit 0"
""".replace("TIMELINE", timeline))
        merge = os.path.join(d, "merge.sh")
        write_script(merge, 'n=$1; echo "merge $n: state MERGED "')

        rc = LQ.run(queue, log, "bash %s {n}" % gate, "bash %s {n}" % merge, concurrency=2)
        self.assertEqual(rc, 0)

        starts, ends = {}, {}
        with open(timeline, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if parts[0] == "START":
                    starts[parts[1]] = float(parts[2])
                elif parts[0] == "END":
                    ends[parts[1]] = float(parts[2])
        self.assertEqual(set(starts), {"1", "2", "3"})
        # At any instant sampled at each start/end event, at most 2 may be open.
        events = sorted(starts.values()) + sorted(ends.values())
        for t in events:
            open_count = sum(1 for n in starts if starts[n] <= t < ends.get(n, float("inf")))
            self.assertLessEqual(open_count, 2, "more than 2 gates overlapped at %s" % t)


class TimeoutIsRedNeverAVerdict(unittest.TestCase):
    def test_timeout_then_clean_second_gate_merges(self):
        d = tempfile.mkdtemp()
        queue = os.path.join(d, "queue.txt")
        log = os.path.join(d, "land.log")
        attempts = os.path.join(d, "attempts.txt")
        with open(queue, "w", encoding="utf-8") as fh:
            fh.write("7\n")
        gate = os.path.join(d, "gate.sh")
        # First call: prints a TimeoutExpired with no required_fast green.
        # Second call (the alone re-gate): clean, required_fast green.
        write_script(gate, """
n=$1
count=$(grep -c . "%s" 2>/dev/null || echo 0)
echo "$count" >> "%s"
if [ "$count" = "0" ]; then
  echo "subprocess.TimeoutExpired: command timed out after 60 seconds"
else
  echo "## required_fast exit 0"
fi
""" % (attempts, attempts))
        merge = os.path.join(d, "merge.sh")
        write_script(merge, """
n=$1
if grep -q '## required_fast exit 0' "%s/gate-regate$n.log"; then
  echo "merge $n: state MERGED "
else
  echo "merge $n REFUSED: no green gate"
fi
""" % d)

        rc = LQ.run(queue, log, "bash %s {n}" % gate, "bash %s {n}" % merge, concurrency=1)
        self.assertEqual(rc, 0)
        with open(attempts, encoding="utf-8") as fh:
            calls = fh.read().split()
        self.assertEqual(calls, ["0", "1"], "gate must run exactly twice: once, then alone")
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("TIMEOUT-RED 7", text)
        self.assertIn("merge 7: state MERGED", text)
        self.assertIn("LAND-7-END", text)

    def test_timeout_that_stays_red_is_refused_not_merged(self):
        # P3a (codex-findings-P3 #1) closed the legacy fail-open hole: a
        # gate that is still red after its lone re-gate now never even
        # reaches the --merge template, so this test no longer asserts
        # the TEMPLATE'S OWN grep-and-refuse message (that message can
        # never print again, because the template is never invoked); it
        # asserts land_queue's own refusal instead, plus a marker file
        # the template would have created if it HAD run, absent.
        d = tempfile.mkdtemp()
        queue = os.path.join(d, "queue.txt")
        log = os.path.join(d, "land.log")
        marker = os.path.join(d, "merge-ran.marker")
        with open(queue, "w", encoding="utf-8") as fh:
            fh.write("9\n")
        gate = os.path.join(d, "gate.sh")
        write_script(gate, 'echo "the socket timed out again"')
        merge = os.path.join(d, "merge.sh")
        write_script(merge, """
n=$1
touch "%s"
if grep -q '## required_fast exit 0' "%s/gate-regate$n.log"; then
  echo "merge $n: state MERGED "
else
  echo "merge $n REFUSED: no green gate"
fi
""" % (marker, d))
        LQ.run(queue, log, "bash %s {n}" % gate, "bash %s {n}" % merge, concurrency=1)
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("land 9: gate verdict TIMEOUT-RED, merge template not invoked", text)
        self.assertNotIn("state MERGED", text)
        self.assertFalse(os.path.exists(marker), "the --merge template ran on a still-red gate")


class MergeOrderFollowsQueueOrder(unittest.TestCase):
    def test_merges_run_in_the_order_they_were_queued(self):
        d = tempfile.mkdtemp()
        queue = os.path.join(d, "queue.txt")
        log = os.path.join(d, "land.log")
        with open(queue, "w", encoding="utf-8") as fh:
            fh.write("30\n10\n20\n")
        gate = os.path.join(d, "gate.sh")
        write_script(gate, 'echo "## required_fast exit 0"')
        merge = os.path.join(d, "merge.sh")
        write_script(merge, 'n=$1; echo "merge $n: state MERGED "')
        LQ.run(queue, log, "bash %s {n}" % gate, "bash %s {n}" % merge, concurrency=3)
        with open(log, encoding="utf-8") as fh:
            merges = [line.split()[1].rstrip(":") for line in fh if line.startswith("merge ")]
        self.assertEqual(merges, ["30", "10", "20"])


class VerdictLineFormatMatchesTheExistingLog(unittest.TestCase):
    def test_merge_and_land_end_lines_match_serial_land_log(self):
        d = tempfile.mkdtemp()
        queue = os.path.join(d, "queue.txt")
        log = os.path.join(d, "land.log")
        with open(queue, "w", encoding="utf-8") as fh:
            fh.write("501\n")
        gate = os.path.join(d, "gate.sh")
        write_script(gate, 'echo "## required_fast exit 0"')
        merge = os.path.join(d, "merge.sh")
        write_script(merge, 'n=$1; echo "merge $n: state MERGED "')
        LQ.run(queue, log, "bash %s {n}" % gate, "bash %s {n}" % merge, concurrency=1)
        with open(log, encoding="utf-8") as fh:
            lines = [l.rstrip("\n") for l in fh]
        self.assertIn("merge 501: state MERGED ", lines)
        self.assertIn("LAND-501-END", lines)


class QueueIsDrainedAtomically(unittest.TestCase):
    def test_pop_one_leaves_the_rest_and_is_atomic_on_disk(self):
        d = tempfile.mkdtemp()
        q = os.path.join(d, "queue.txt")
        with open(q, "w", encoding="utf-8") as fh:
            fh.write("100\n101\n102\n")
        self.assertEqual(LQ.pop_one(q), "100")
        with open(q, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "101\n102\n")
        self.assertEqual(LQ.queue_length(q), 2)


class StatusAndStop(unittest.TestCase):
    def test_status_with_no_lock_reports_no_holder(self):
        d = tempfile.mkdtemp()
        q = os.path.join(d, "queue.txt")
        open(q, "w").close()
        args = _ns(queue=q, lock=None)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = LQ.cmd_status(args)
        self.assertEqual(rc, 0)

    def test_stop_with_no_holder_refuses(self):
        d = tempfile.mkdtemp()
        q = os.path.join(d, "queue.txt")
        open(q, "w").close()
        args = _ns(queue=q, lock=None)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = LQ.cmd_stop(args)
        self.assertEqual(rc, 1)


def _ns(**kw):
    class NS:
        pass
    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


if __name__ == "__main__":
    unittest.main()
