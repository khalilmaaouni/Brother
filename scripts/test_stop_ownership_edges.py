"""Five confirmed gaps from the SO24 adversarial review
(~/.claude/evidence/loop-remediation-0926/so24-deepseek-review.md, SO24 integrated at
99057136b), findings 3, 4, 5, 7 and 9, plus two guards that survived mutation because
nothing tested them: the fail-closed first read, and the same-command re-check.
Companion to scripts/test_stop_ownership.py, which stays untouched.

No real process on this machine is ever signalled: every Script/RealProcesses case
scopes its kill to STOP_LOOP_ONLY pointed at its own fixture directory, or runs --dry,
or both, per this estate's own hard rule.
Run from the repository root: python3 -B scripts/test_stop_ownership_edges.py
"""
import os, subprocess, sys, tempfile, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")
STOP = os.path.join(LOOP, "stop_loop.sh")
sys.path.insert(0, LOOP)
import loop_procs as LP  # noqa: E402

# A loop tool is the loop's only when it belongs to a run of this loop (its program in $HOME/.claude/bin, or a run marker
# under the runs root; scripts/test_stop_run_identity.py), so every fixture runner here lives in the fixture home's bin.
HOME = "/Users/you"   # the placeholder home shipped files may name (scripts/test_export_public.py)
RUNNER = HOME + "/.claude/bin/unit_runner.py H3 H3.d"
CLAUDE = "/usr/local/bin/claude -p --model sonnet"
RUN_VARS = ("BROTHER_RUN_DIR", "BROTHER_RUNS_ROOT", "STOP_LOOP_ONLY")   # the driver exports the first two to all it starts


def runner(home):
    """a unit runner's command line, launched from the fixture home's own bin"""
    return "python3 %s/.claude/bin/unit_runner.py H3 H3.d" % home


def fixture_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
    env["HOME"] = home
    env.update(extra)
    return env


def rows(*r):
    return [dict(pid=a, ppid=b, pgid=c, command=d) for a, b, c, d in r]


class F3PgidReuseNeedsAGroupLeader(unittest.TestCase):
    """Finding 3: `r["pgid"] in roots` proves nothing unless the root named by
    that pgid also leads its OWN group (by_pid[root]["pgid"] == root). A
    selected tool that is not itself a group leader has a pgid that names some
    OTHER, unrelated group; a generic CLI merely sharing that number by
    coincidence is not the tool's child."""

    def owned(self, snap):
        with mock.patch.dict(os.environ, fixture_env(HOME), clear=True):
            return set(LP.owned(snap, LP.RUNNERS_RX, self_pid=1))

    def test_a_root_that_does_not_lead_its_own_group_grants_nothing(self):
        # The review's own scenario: pid 500 is a selected runner but its OWN
        # pgid is 400 (it does not lead its own group). pid 600's pgid is 500
        # only by numeric coincidence with the runner's pid; group 500 is
        # unrelated to the runner, so 600 must not be selected.
        snap = rows((500, 1, 400, "python3 " + RUNNER), (600, 1, 500, CLAUDE))
        self.assertEqual(self.owned(snap), {500})

    def test_a_root_that_does_lead_its_own_group_still_grants_membership(self):
        # The regression guard: a genuine group leader (pgid == its own pid)
        # must still cover its real children, exactly as before this fix.
        snap = rows((500, 1, 500, "python3 " + RUNNER), (600, 1, 500, CLAUDE))
        self.assertEqual(self.owned(snap), {500, 600})


def make_fake_ps(d, table, argv_log=None, fail_full_table_after=None,
                 diverge=None, broken_pid=None, gone=None):
    """A ps stub answering the two shapes stop_loop.sh/loop_procs.py ask for
    (a full "-ax -ww -o ..." table dump, and a per-pid "-o command= -p PID"
    lookup), entirely from `table`: no real process is ever listed.

    argv_log: path to append this stub's own argv to, one call per line, so a
      caller can prove which flags were actually used (finding 7).
    fail_full_table_after: None, or the call number (0-based: 0 means even the
      FIRST call fails) after which every further full-table call fails with
      a stderr message and exit 1 (finding 5: a LATER read fails, distinct
      from the first).
    diverge: {pid: different_command}. The per-pid lookup for that pid
      answers `different_command` instead of the table's own row for it, so
      the snapshot and the re-check disagree on purpose (the same-command
      guard, and finding 6's shape).
    broken_pid: a pid whose per-pid lookup fails with a stderr message and
      exit 1: a broken ps, distinct from a pid simply absent from `table`
      (exit 1, nothing on any stream: legitimately gone) (finding 9).
    gone: pids that DO appear in `table` (so the initial full-table read
      lists them as candidates, matching a process that was alive at
      snapshot time) but whose per-pid lookup always answers "not found"
      (exit 1, nothing on any stream), simulating one that exited by the
      time of the re-check: the ordinary, not-an-error case."""
    p = os.path.join(d, "ps")
    body = (
        "#!/usr/bin/env python3\n"
        "import sys, os\n"
        "T = %r\n"
        "ARGV_LOG = %r\n"
        "FAIL_AFTER = %r\n"
        "DIVERGE = %r\n"
        "BROKEN_PID = %r\n"
        "GONE = %r\n"
        "COUNTER = %r\n"
        "if ARGV_LOG:\n"
        "    with open(ARGV_LOG, 'a') as fh:\n"
        "        fh.write(' '.join(sys.argv[1:]) + chr(10))\n"
        "if '-p' in sys.argv:\n"
        "    pid = int(sys.argv[sys.argv.index('-p') + 1])\n"
        "    field = sys.argv[sys.argv.index('-o') + 1] if '-o' in sys.argv else 'command='\n"
        "    if field == 'pgid=':\n"
        "        r = [t for t in T if t[0] == pid]\n"
        "        print(r[0][2] if r else '')\n"
        "        sys.exit(0 if r else 1)\n"
        "    if pid == BROKEN_PID:\n"
        "        sys.stderr.write('ps: could not read the process table' + chr(10))\n"
        "        sys.exit(1)\n"
        "    if pid in GONE:\n"
        "        sys.exit(1)\n"
        "    if pid in DIVERGE:\n"
        "        print(DIVERGE[pid]); sys.exit(0)\n"
        "    r = [t for t in T if t[0] == pid]\n"
        "    print(r[0][3] if r else '')\n"
        "    sys.exit(0 if r else 1)\n"
        "n = 0\n"
        "if os.path.isfile(COUNTER):\n"
        "    n = int(open(COUNTER).read().strip() or '0')\n"
        "open(COUNTER, 'w').write(str(n + 1))\n"
        "if FAIL_AFTER is not None and n > FAIL_AFTER:\n"
        "    sys.stderr.write('ps: could not read the process table' + chr(10))\n"
        "    sys.exit(1)\n"
        "for t in T:\n"
        "    print('%%d %%d %%d %%s' %% t)\n"
    ) % (table, argv_log, fail_full_table_after, diverge or {}, broken_pid,
         set(gone or ()), os.path.join(d, ".ps-call-count"))
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)


def run_stop(d, *args, only=None):
    """Run stop_loop.sh with a fake ps on PATH. `only`, when given, sets
    STOP_LOOP_ONLY (needed only when real processes are involved, so a real
    kill stays scoped to the fixture); left None, STOP_LOOP_ONLY is UNSET,
    matching test_stop_ownership.py's own Script class: a fake ps already
    replaces the entire process table, so no real process can ever appear
    regardless of --only, and a fixture command that does not happen to
    contain the fixture directory's own path must still be selected."""
    env = fixture_env(d, PATH=d + os.pathsep + os.environ["PATH"])
    if only is not None:
        env["STOP_LOOP_ONLY"] = only
    return subprocess.run(["bash", STOP] + list(args), env=env,
                          capture_output=True, text=True, timeout=60)


class F5ALaterReadFailureIsFailClosed(unittest.TestCase):
    """Finding 5: the FIRST process-table read already fails closed
    (`if ! INITIAL=$(procs "$KIND")`); every later read inside owned_now was
    masked by `procs "$KIND" || true`, so a ps failure after the first read
    let the script proceed and could print STOPPED while blind."""

    def test_a_read_that_fails_after_the_first_never_prints_stopped(self):
        d = tempfile.mkdtemp(prefix="stop-edge-f5-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        # Call 0 (the INITIAL read) succeeds; every call after it fails.
        make_fake_ps(d, [(700001, 1, 700001, runner(d))],
                    fail_full_table_after=0)
        r = run_stop(d, "--runners-only", "--dry")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stdout + r.stderr, r.stdout + r.stderr)
        self.assertNotIn("STOPPED", r.stdout, r.stdout)

    def test_only_the_final_read_failing_still_refuses_and_never_prints_stopped(self):
        """M-SO28-FINAL-READ (review re-run on SO28, 2026-09-26): the fixture
        above fails at owned_now's FIRST call (for TERM), so the guard at the
        LAST call site, the one that actually decides STOPPED vs NO-DATA
        (`if ! LEFT_LINES=$(owned_now); then ...; exit 2; fi`), was never
        exercised; replacing it with a bare `LEFT_LINES=$(owned_now)` left
        every fixture in this file green. This isolates exactly that call:
        every read succeeds (INITIAL, then owned_now's own read for both
        TERM and KILL) except the FINAL one, the one whose failure must
        still refuse rather than let a stale, empty LEFT_LINES read as
        STOPPED."""
        d = tempfile.mkdtemp(prefix="stop-edge-f5-finalread-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        # Calls 0, 1 and 2 succeed: INITIAL, then owned_now's own read for
        # TERM, then for KILL. Call 3, the FINAL owned_now (for
        # LEFT_LINES), is the only one that fails.
        make_fake_ps(d, [(700007, 1, 700007, runner(d))],
                    fail_full_table_after=2)
        r = run_stop(d, "--runners-only", "--dry")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("STOPPED cannot be confirmed", r.stdout + r.stderr,
                     r.stdout + r.stderr)
        self.assertNotIn("STOPPED: nothing of the loop is alive", r.stdout,
                        r.stdout)


class FailClosedFirstReadNeverSignalsOrPrintsStopped(unittest.TestCase):
    """The pre-existing guard finding 5 itself names as already correct
    (`if ! INITIAL=$(procs "$KIND")`), never tested, so a mutation to it
    would survive. A process table unreadable from the very first call must
    exit 2, signal nothing, and never print STOPPED."""

    def test_an_unreadable_table_from_the_start_exits_2_and_signals_nothing(self):
        d = tempfile.mkdtemp(prefix="stop-edge-firstread-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        make_fake_ps(d, [(700002, 1, 700002, runner(d))],
                    fail_full_table_after=-1)
        r = run_stop(d, "--runners-only", "--dry")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stdout + r.stderr, r.stdout + r.stderr)
        self.assertNotIn("STOPPED", r.stdout, r.stdout)


class F9AliveSameDistinguishesGoneFromUnreadable(unittest.TestCase):
    """Finding 9: alive_same's `ps ... | sed ...` pipeline reported the exit
    status of sed, not ps, so a broken ps read for one pid was silently
    dropped exactly like a pid that had legitimately exited. The two must
    read differently: gone (exit 1, nothing on any stream) is dropped in
    silence; broken (exit 1, a message on stderr) must fail the whole read."""

    def test_a_pid_simply_gone_is_dropped_in_silence(self):
        d = tempfile.mkdtemp(prefix="stop-edge-f9-gone-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        # 700003 is in the initial table (so it IS selected as a candidate)
        # but its per-pid re-check always answers "not found, nothing on
        # any stream": it was alive at snapshot time and has since exited,
        # the ordinary, not-an-error, must-not-block-the-run case.
        make_fake_ps(d, [(700003, 1, 700003, runner(d))],
                    gone={700003})
        r = run_stop(d, "--runners-only", "--dry")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("STOPPED", r.stdout, r.stdout)

    def test_a_broken_read_for_one_pid_is_never_mistaken_for_gone(self):
        d = tempfile.mkdtemp(prefix="stop-edge-f9-broken-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        make_fake_ps(d, [(700004, 1, 700004, runner(d))],
                    broken_pid=700004)
        r = run_stop(d, "--runners-only", "--dry")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("STOPPED", r.stdout, r.stdout)


class TheSameCommandReCheckNeverSignalsADivergedPid(unittest.TestCase):
    """The guard alive_same already carries (`[ "$now" = "$c" ]`), untested:
    a pid whose live command no longer matches what the snapshot recorded
    (recycled, or genuinely changed) must never be reported as still owned,
    so it is never signalled."""

    def test_a_pid_whose_command_changed_is_never_reported_as_owned(self):
        d = tempfile.mkdtemp(prefix="stop-edge-samecmd-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        make_fake_ps(d, [(700005, 1, 700005, runner(d))],
                    diverge={700005: "/usr/bin/vim unrelated-notes.md"})
        r = run_stop(d, "--runners-only", "--dry")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("STOPPED", r.stdout, r.stdout)
        self.assertNotIn("700005", r.stdout, r.stdout)


class F7WidthIsNeverLimited(unittest.TestCase):
    """Finding 7: the multi-column snapshot and the single-column re-check
    must both ask for unlimited width, or a long command can truncate
    differently in each and the same-command comparison misfires."""

    def test_the_python_snapshot_asks_for_unlimited_width(self):
        d = tempfile.mkdtemp(prefix="stop-edge-f7-snap-")
        log = os.path.join(d, "argv.log")
        make_fake_ps(d, [(1, 1, 1, "/sbin/launchd")], argv_log=log)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = d + os.pathsep + old_path
        try:
            snap = LP.snapshot()
        finally:
            os.environ["PATH"] = old_path
        self.assertIsNotNone(snap, "the fake ps should have answered")
        with open(log) as fh:
            calls = fh.read().splitlines()
        self.assertTrue(calls, "the snapshot never called ps")
        self.assertTrue(any("-ww" in c.split() for c in calls),
                        "no snapshot call asked for unlimited width: %r" % calls)

    def test_the_shell_re_check_asks_for_unlimited_width(self):
        d = tempfile.mkdtemp(prefix="stop-edge-f7-recheck-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        log = os.path.join(d, "argv.log")
        make_fake_ps(d, [(700006, 1, 700006, runner(d))],
                    argv_log=log)
        run_stop(d, "--runners-only", "--dry")
        with open(log) as fh:
            calls = fh.read().splitlines()
        recheck_calls = [c for c in calls if "-p" in c.split()]
        self.assertTrue(recheck_calls, "no per-pid re-check call was logged: %r" % calls)
        self.assertTrue(any("-ww" in c.split() for c in recheck_calls),
                        "no re-check call asked for unlimited width: %r" % recheck_calls)


def call_signal_lines(d, sig, lines, pid_pgid):
    """Extract signal_lines() from stop_loop.sh by itself and call it
    directly, never running the rest of the script. `kill` is a bash
    BUILTIN: a PATH-shimmed executable is silently ignored (confirmed live:
    `type kill` reports "kill is a shell builtin"), so the only thing that
    outranks it is a shell FUNCTION named kill, defined here to record argv
    instead of signalling anything, real or otherwise. `ps` stays a real
    external command, shimmed via PATH exactly like every other test here,
    answering `-o pgid=` from pid_pgid ({pid: pgid})."""
    make_fake_ps(d, [(pid, 1, pgid, "x") for pid, pgid in pid_pgid.items()])
    extract = subprocess.run(["sed", "-n", "/^signal_lines() {/,/^}/p", STOP],
                             capture_output=True, text=True)
    assert extract.stdout.strip(), "signal_lines() was not found in stop_loop.sh"
    killlog = os.path.join(d, "kill.log")
    driver = os.path.join(d, "driver.sh")
    with open(driver, "w") as fh:
        fh.write("#!/bin/bash\nset -u\nDRY=0\n")
        fh.write("kill() { printf '%s\\n' \"$*\" >> \"$KILL_LOG\"; }\n")
        fh.write(extract.stdout)
        fh.write("\nsignal_lines \"$1\" \"$2\"\n")
    os.chmod(driver, 0o755)
    env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], KILL_LOG=killlog)
    subprocess.run(["bash", driver, sig, lines], env=env,
                   capture_output=True, text=True, timeout=30)
    if not os.path.isfile(killlog):
        return []
    with open(killlog) as fh:
        return fh.read().splitlines()


class F4GroupKillNeverHitsAnUnownedSiblingGroup(unittest.TestCase):
    """Finding 4: `kill -SIG -- -$p` signals p's WHOLE process group, which is
    only safe when p itself leads that group (its own pgid equals its own
    pid). A pid that does not lead its own group has a REAL pgid naming some
    OTHER group; a naive negative-pid kill risks a sibling in that group that
    was never selected. The exact numeric coincidence the review describes
    (an unrelated real group whose gid equals a DIFFERENT selected pid, only
    possible through kernel pid reuse across time) cannot be constructed on
    demand, so this calls signal_lines directly and proves the SHELL LOGIC
    by the kill syntax it chooses, rather than an outcome a fake collision
    could not reliably stage anyway."""

    def test_a_non_leader_pid_is_signalled_alone_never_by_its_pid_as_a_group(self):
        # pid 700100 does NOT lead its own group (its real pgid is 400000,
        # an unrelated number): the only safe kill is the plain pid form.
        d = tempfile.mkdtemp(prefix="stop-edge-f4-nonleader-")
        calls = call_signal_lines(d, "TERM", "700100 irrelevant\n",
                                  {700100: 400000})
        self.assertTrue(calls, "no kill was ever invoked")
        self.assertTrue(
            all("-700100" not in c.split() for c in calls),
            "a non-leader pid was signalled by process GROUP (its own pid "
            "number used as a group id it does not lead): %r" % calls)
        self.assertTrue(any(c.split() == ["-TERM", "700100"] for c in calls),
                        "the non-leader pid was never signalled by its plain "
                        "pid at all: %r" % calls)

    def test_a_real_group_leader_is_still_signalled_by_its_group(self):
        # Regression guard: pid 700101 DOES lead its own group (pgid equals
        # its own pid); the existing, correct behaviour (group kill, so a
        # session leader's real children go with it) must be unchanged.
        d = tempfile.mkdtemp(prefix="stop-edge-f4-leader-")
        calls = call_signal_lines(d, "TERM", "700101 irrelevant\n",
                                  {700101: 700101})
        self.assertTrue(calls, "no kill was ever invoked")
        self.assertTrue(
            any(c.split() == ["-TERM", "--", "-700101"] for c in calls),
            "a genuine group leader was not signalled by its process group: %r"
            % calls)


if __name__ == "__main__":
    unittest.main(verbosity=1)
