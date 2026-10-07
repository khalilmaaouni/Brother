"""repair_wave's fan-out drain honours its deadline and never escalates on a pid the table no longer shows in the group.

WHAT IT PROTECTS (RR lane C, 2026-09-27, reproduced by an adversarial audit on hub main e7e17784c):
  - drain_hang: with the group signal denied, the process table unreadable and proc.kill() refused, _drain_fanout
    blocked in proc.wait() with no bound for as long as the fan-out lived, whatever its deadline said.
  - drain_reuse: it SIGTERMed the group's members from one snapshot, then SIGKILLed the same pids without looking
    again, so a pid reused by an unrelated process in between was SIGKILLed although a fresh read showed it outside
    the fan-out's group.
Entry point: repair_wave.py run as __main__ through the contract suite's own offline harness (run_wave), every model,
subprocess and fan-out seam replaced. Run from the repository root: python3 -B scripts/test_repair_drain_bounds.py
"""
import os, shutil, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_repair_wave_contract as C  # noqa: E402  the offline wave harness and its fixtures

MEMBER = 42001


class Late:
    """A fan-out that is past its deadline and never reaps inside a bounded wait. Its pid is 0, so its group is 0."""
    pid = 0

    def __init__(self, argv, **kw):
        pass

    def wait(self, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired("fanout", timeout)
        return -9

    def poll(self):
        return None

    def kill(self):
        pass


class Watchdog(BaseException):
    pass


class Drain(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repair-drain-bounds-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.bw, self.pw, self.nw = C.waves(self.root)
        self.tail = [str(self.bw), str(self.pw), str(self.nw), "1"]

    def wave_with_signals(self, lines):
        sent = []
        with patch.object(os, "killpg", side_effect=PermissionError("fixture denied")), \
             patch.object(os, "kill", side_effect=lambda pid, sig: sent.append((pid, sig))):
            code, _, out = C.run_wave(self.tail, popen=Late, ps_fake_lines=list(lines),
                                      env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"})
        return code, out, sent

    def test_a_pid_reused_outside_the_group_is_never_killed(self):
        # read 1: the member is in the fan-out's group 0; read 2, after the TERM: that pid now leads a group of its own
        code, out, sent = self.wave_with_signals(["%d 0\n" % MEMBER, "%d %d\n" % (MEMBER, MEMBER), "%d %d\n" % (MEMBER, MEMBER)])
        self.assertIn((MEMBER, signal.SIGTERM), sent, out)
        self.assertNotIn((MEMBER, signal.SIGKILL), sent, "a pid the fresh table shows outside the group was SIGKILLed: " + out[-400:])

    def test_a_fresh_read_that_fails_stops_the_drain_before_any_kill(self):
        code, out, sent = self.wave_with_signals(["%d 0\n" % MEMBER, "garbage\n"])
        self.assertNotIn((MEMBER, signal.SIGKILL), sent, "a KILL was sent on a table the drain could not re-read: " + out[-400:])
        self.assertIn("NO-DATA", out, out)
        self.assertNotEqual(code, 0, out)

    def test_a_member_still_in_the_group_is_killed(self):
        # the control: the fresh read still shows the member, so the escalation happens
        code, out, sent = self.wave_with_signals(["%d 0\n" % MEMBER, "%d 0\n" % MEMBER, "\n"])
        self.assertIn((MEMBER, signal.SIGKILL), sent, out)

    def bounded_wave(self, killpg, **wave):
        """The wave over a REAL fan-out that sleeps 30 s and refuses proc.kill(), under a 10 s watchdog. `wave` carries
        run_wave's ps fixture; a list named `member_lines` is filled with the child's own "pid pgid" rows once it runs."""
        seen, lines = {}, wave.pop("member_lines", None)

        def popen(argv, **kw):
            p = C.REAL_POPEN([sys.executable, "-c", "import time; time.sleep(30)"], **kw)

            def deny():
                raise PermissionError("fixture denied")
            p.kill = deny
            seen["p"] = p
            if lines is not None:
                lines.extend(["%d %d\n" % (p.pid, p.pid)] * 3)
            return p

        def reap():
            p = seen.get("p")
            if p is not None and p.poll() is None:
                os.kill(p.pid, signal.SIGKILL)
                p.wait()
        self.addCleanup(reap)

        def alarm(*a):
            raise Watchdog()
        old = signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, 10)
        began = time.monotonic()
        try:
            with patch.object(os, "killpg", side_effect=killpg):
                if lines is not None:
                    with patch.object(os, "kill", side_effect=lambda pid, sig: None):   # signals recorded nowhere, sent nowhere
                        code, _, out = C.run_wave(self.tail, popen=popen, ps_fake_lines=lines, env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"}, **wave)
                else:
                    code, _, out = C.run_wave(self.tail, popen=popen, env={"BROTHER_REPAIR_FANOUT_TIMEOUT_S": "1"}, **wave)
        except Watchdog:
            self.fail("the drain blocked past its deadline of 1 s (watchdog at 10 s) on a fan-out it could not kill")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
        self.assertLess(time.monotonic() - began, 10)
        self.assertIn("NO-DATA", out, out)
        self.assertNotEqual(code, 0, out)
        return out

    def test_an_unkillable_fan_out_on_an_unreadable_table_returns_within_its_deadline(self):
        self.bounded_wave(PermissionError("fixture denied"), ps_fake_lines=["unreadable\n"])

    def test_an_unkillable_fan_out_whose_table_read_times_out_returns_within_its_deadline(self):
        self.bounded_wave(PermissionError("fixture denied"), ps_timeout=True)

    def test_a_group_signal_that_did_not_kill_still_returns_within_its_deadline(self):
        out = self.bounded_wave(lambda pgid, sig: None)
        self.assertIn("not reaped inside the drain's deadline", out)

    def test_an_empty_group_with_the_leader_still_alive_is_no_data_within_its_deadline(self):
        out = self.bounded_wave(PermissionError("fixture denied"), ps_fake_lines=["\n"])
        self.assertIn("not reaped inside the drain's deadline", out)

    def test_a_leader_that_outlives_the_kill_pass_is_a_survivor_within_its_deadline(self):
        out = self.bounded_wave(PermissionError("fixture denied"), member_lines=[])
        self.assertIn("survived", out)

if __name__ == "__main__":
    unittest.main(verbosity=2)
