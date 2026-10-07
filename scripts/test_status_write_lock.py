"""Every run STATUS writer in this lane takes the runs root's lock, <runs>/.status.lock, and decides under it.

WHAT IT PROTECTS (RR lane C follow up, 2026-09-27). A run's STATUS has five writers (the runner, the pool's dead runner
reconcile, the checker's quarantine, salvage, the landing) and none took a lock, so a decision read one word and the
write landed over a newer one: salvage re-promoted a build the landing had just quarantined. Salvage now writes under
<runs>/.status.lock and compares first (lane C2); this file holds the three writers lane C owns to the same protocol:
  - unit_runner.write_status waits for the lock before it writes its own STATUS;
  - runner_pool.reconcile_dead decides "no STATUS yet" UNDER the lock, so a READY the runner wrote after the pool
    looked is never overwritten with STALLED;
  - check_wave's gate quarantine reads the READY it replaces UNDER the lock, so a landing's word written meanwhile
    stands, and it writes by temp file plus rename (the plain open("w") it replaces truncated first).
The lock sits in the runs root, outside every run folder, so taking it never moves a run folder's mtime.
Run from the repository root: python3 -B scripts/test_status_write_lock.py
"""
import fcntl, json, os, shutil, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, HERE)
sys.path.insert(0, LOOP)
import test_run_folder_identity as RF  # noqa: E402  the runner's stub bin fixture
import runner_pool  # noqa: E402
import check_wave  # noqa: E402

SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
LANDING = "QUARANTINE refused at the gates alone, gates RED | previous: READY /x\n"


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


class Held(object):
    """The runs root's lock, held by this test from a descriptor of its own until release()."""

    def __init__(self, runs):
        self.fh = open(os.path.join(runs, ".status.lock"), "a")
        fcntl.flock(self.fh, fcntl.LOCK_EX)

    def release(self):
        if self.fh is not None:
            self.fh.close(); self.fh = None


def in_thread(fn, *args):
    box = {}

    def body():
        try:
            box["value"] = fn(*args)
        except BaseException as exc:   # the case reads it; a raise in a thread must not vanish
            box["error"] = exc
    t = threading.Thread(target=body, daemon=True)
    t.start()
    return t, box


class TheRunnerWaitsForTheLock(unittest.TestCase):
    """Entry point: `unit_runner.py Z9 Z9.1 1` as a subprocess, stopped by the expected value gate at round 0, so its
    one STATUS write is EXHAUSTED right after it records the lesson."""

    def test_its_status_is_written_only_once_the_lock_is_free(self):
        f = RF.Fixture()
        self.addCleanup(f.cleanup)
        held = Held(f.runs)
        self.addCleanup(held.release)
        p = f.start()
        try:
            until = time.time() + 60
            while time.time() < until and "failure_ledger record" not in (read(f.rec) or ""):
                time.sleep(0.05)
            self.assertIn("failure_ledger record", read(f.rec) or "", "the runner never reached its STATUS write")
            time.sleep(1.0)
            folders = f.folders()
            written = [q for q in folders if read(os.path.join(q, "STATUS")) is not None]
            self.assertEqual(written, [], "the runner wrote its STATUS while another writer held the lock")
            self.assertIsNone(p.poll(), "the runner ended instead of waiting for the lock")
        finally:
            held.release()
            out, _ = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 1, out[-800:])
        self.assertTrue((read(os.path.join(f.folders()[0], "STATUS")) or "").startswith("EXHAUSTED by the expected value gate"))


class ThePoolDecidesUnderTheLock(unittest.TestCase):
    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.runs = tempfile.mkdtemp(prefix="status-lock-pool-", dir=SCRATCH)
        self.addCleanup(shutil.rmtree, self.runs, True)
        self.run_dir = os.path.join(self.runs, "X.a-010000"); os.makedirs(self.run_dir)
        with open(os.path.join(self.run_dir, "PID"), "w") as fh: fh.write("999999\n")
        self.st = os.path.join(self.run_dir, "STATUS")

    def test_a_ready_written_after_the_pool_looked_is_never_overwritten_with_stalled(self):
        def runner_finishes_then_dies(pid):
            with open(self.st, "w") as fh: fh.write("READY /x/b.json (round 0)\n")
            return False
        got = runner_pool.reconcile_dead(self.run_dir, runner_finishes_then_dies)
        self.assertEqual(read(self.st), "READY /x/b.json (round 0)\n", "a READY was overwritten with STALLED")
        self.assertEqual(got, "")

    def test_the_stalled_write_waits_for_the_lock(self):
        held = Held(self.runs)
        self.addCleanup(held.release)
        t, box = in_thread(runner_pool.reconcile_dead, self.run_dir, lambda pid: False)
        time.sleep(0.5)
        self.assertIsNone(read(self.st), "STALLED was written while another writer held the lock")
        self.assertTrue(t.is_alive())
        held.release(); t.join(10)
        self.assertNotIn("error", box, box.get("error"))
        self.assertEqual(box.get("value"), "STALLED")
        self.assertEqual(read(self.st), "STALLED dead runner pid 999999")


class TheCheckerQuarantinesUnderTheLock(unittest.TestCase):
    """check_one in gate mode on a build whose checker gave no verdict for the MAX_NODATA'th time: it replaces a READY
    STATUS with its quarantine note. No spec exists, so no model is called."""

    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.runs = tempfile.mkdtemp(prefix="status-lock-check-", dir=SCRATCH)
        self.addCleanup(shutil.rmtree, self.runs, True)
        run = os.path.join(self.runs, "X.a-010000")
        os.makedirs(os.path.join(run, "round0", "out"))
        self.build = os.path.join(run, "round0", "out", "X.a-r0-build.json")
        with open(self.build, "w") as fh: fh.write('{"edits": [], "tests": []}')
        with open(self.build + ".check.json", "w") as fh:
            json.dump({"sha256": check_wave.sha(self.build), "model": "opus", "verdict": "NO-DATA",
                       "attempts": check_wave.MAX_NODATA - 1}, fh)
        self.st = os.path.join(run, "STATUS")
        with open(self.st, "w") as fh: fh.write("READY %s (round 0, grader PASS, probes clean)\n" % self.build)
        self.ready = read(self.st)

    def check(self):
        return check_wave.check_one(self.build, "opus", {"units": []}, mode="gate", specs=self.runs)

    def test_a_landing_word_written_while_it_waited_stands(self):
        held = Held(self.runs)
        self.addCleanup(held.release)
        t, box = in_thread(self.check)
        time.sleep(0.5)
        self.assertEqual(read(self.st), self.ready, "the checker wrote STATUS while another writer held the lock")
        with open(self.st, "w") as fh: fh.write(LANDING)   # the landing, holding the lock, quarantines the build
        held.release(); t.join(30)
        self.assertNotIn("error", box, box.get("error"))
        self.assertEqual(read(self.st), LANDING, "the checker overwrote the landing's quarantine with its own note")

    def test_the_quarantine_replaces_the_file_whole(self):
        before = os.stat(self.st).st_ino
        rec = self.check()
        self.assertEqual(rec["verdict"], "NO-DATA", rec)
        self.assertTrue((read(self.st) or "").startswith("QUARANTINE checker opus FIX FIRST: no verdict after %d attempts" % check_wave.MAX_NODATA), read(self.st))
        self.assertIn("previous: READY", read(self.st))
        self.assertNotEqual(os.stat(self.st).st_ino, before, "STATUS was truncated and rewritten in place, not replaced whole")
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.st)) if ".tmp" in n], [], "a temp file was left behind")


if __name__ == "__main__":
    unittest.main(verbosity=2)
