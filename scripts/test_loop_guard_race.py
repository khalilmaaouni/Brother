#!/usr/bin/env python3
"""The loop lease must grant itself to exactly ONE racer, and must still recover from a crash.

Probed 2026-09-21 by racing eight concurrent acquires against a free lease: EIGHT of eight printed
ACQUIRED and none was refused. The lease exists to stop two drivers writing one worktree, and it
failed at precisely the moment it exists for, which is two drivers starting together. It was a
check then act race: read the file, decide, then write, with no atomic create anywhere.

Making the create atomic with `set -o noclobber` was necessary and NOT sufficient. Six of eight
still acquired, because of a second bug in the steal path: a racer reading the lease in the instant
between another racer's `rm` and its create saw no holder, concluded the lease was abandoned,
removed it too, and won a create it should have lost. Reading nothing means look again, never take.

A third case was found the same way: a half written lease carries its pid line before its at line,
and the old code read that missing stamp as "past the ttl" and stole the lease from a process that
was still writing it. Every unknown now fails towards REFUSING, because a refused start costs one
relaunch while a double start costs two drivers writing one tree.

SIX MORE, found by adversarial review on 2026-09-21 once acquire() was hardened and nothing else
had been looked at. Every one of them let a second driver onto the tree by a route acquire() never
sees:

  1 RELEASE WAS UNAUTHENTICATED. `rm -f` with no ownership check, so any process freed a live
    lease in one command and took it next.
  2 RENEW TOOK NO PID, so a stranger kept a dead holder's lease alive forever and the recovery
    path could never fire.
  3 `kill -0` CONFLATED DEAD WITH NOT MINE. It fails with EPERM for a live process under another
    uid, which the code read as death. Demonstrated with pid 1, which `kill -0` refuses and
    `ps -p 1` finds.
  4 LEASE POISON. A planted pid with a stamp in the FUTURE made every driver refuse forever,
    because every age test is `now - at` and a negative age is younger than any limit, so the
    poison never aged out.
  5 MID PASS EXPIRY, the one that actually broke the promise. The TTL is 900s and loop_until.sh
    renews once per pass, before the pass runs, while land_batch allows one build 3000s. A
    legitimate long pass outlived its own lease and a second driver acquired, by the lease's own
    rule. A live holder is now never evicted, matching scripts/writer_lock.py.
  6 THE WORKTREE CLAIM WAS NEVER RENEWED. scripts/worktree_sentry.py had the same 900s TTL and
    loop_until.sh claims once at startup, so the claim was valid for fifteen minutes and free for
    the rest of the night.

RECOVERY IS NOT TRADED AWAY FOR ANY OF IT: a positively dead holder is still taken over, for both
the lease and the worktree claim, and both cases are checked below.

This test uses real live child processes as holders, because a fake pid reads as dead and turns
every case into the takeover path, which is how the first version of this probe fooled itself into
reporting a fixed lock as broken.

Run: python3 scripts/test_loop_guard_race.py
"""
import atexit, os, subprocess, sys, tempfile, time

GUARD = os.path.expanduser("~/.claude/bin/loop_guard.sh")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENTRY = os.path.join(ROOT, "scripts", "worktree_sentry.py")

# THIS SUITE OWNS ITS OWN LEASE, AND THAT IS NOT A CONVENIENCE.
# Measured 2026-09-21: driving the real path at ~/.claude/brother-or-dispatch-state left a
# fixture behind carrying pid=1. That pid is launchd, which is always alive, so the lease could
# never be reclaimed as dead, and the next run raced an immortal holder. Three consecutive runs
# with nothing changed between them gave FAILED, FAILED, OK, with different cases failing each
# time. A suite whose verdict depends on what a previous run of itself left on the machine is
# measuring this laptop, not the lease.
# The two seams below are the ones loop_guard.sh and worktree_sentry.py now read, and they are
# exported into every child process this file spawns.
_ISOLATED = tempfile.mkdtemp(prefix="brother-lease-test-")
LEASE = os.path.join(_ISOLATED, "loop.lease")
MARK = os.path.join(_ISOLATED, "worktree.claim")
os.environ["LOOP_LEASE"] = LEASE
os.environ["BROTHER_WORKTREE_CLAIM"] = MARK


# EVERY FIXTURE PROCESS IS TRACKED AND REAPED, and this is not tidiness.
# Measured 2026-09-22 while this suite was being timed: the machine carried 42 orphaned `sleep`
# processes and 11 zombies, and a bare `bash -c exit` cost 0.365 s against about 0.003 s on a
# quiet machine, a hundredfold. This suite spawns one `sleep` per racer as a live pid fixture.
# Some were killed without being waited on, which leaves a zombie, and some were never killed at
# all, which leaves a real process running for up to 60 seconds.
#
# THE PART THAT MATTERS: the leak raises load DURING the run that leaks it, so the suite slows
# itself down as it goes, and any timing taken from it is a property of the machine the suite
# just degraded rather than of the lease it is testing. A whole evening of "the battery is slow"
# was measured against that number.
#
# atexit rather than a per case finally: a case that raises must not strand its fixtures either,
# and the registry is the one place every spawn routes through.
_FIXTURES = []


def reap_fixtures():
    """Kill and WAIT every fixture. kill() alone leaves a zombie, which still holds a slot."""
    for proc in _FIXTURES:
        try:
            proc.kill()
        except OSError:
            pass                       # already gone is the outcome we wanted
        try:
            proc.wait(timeout=5)       # the wait is what actually reaps it
        except (OSError, subprocess.SubprocessError):
            pass
    del _FIXTURES[:]


atexit.register(reap_fixtures)


def live_pid(seconds=30):
    proc = subprocess.Popen(["sleep", str(seconds)])
    _FIXTURES.append(proc)
    return proc


def guard(*args):
    """(exit code, combined output). The code comes from the process itself, never from a pipe."""
    r = subprocess.run(["bash", GUARD] + [str(a) for a in args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def acquire(pid, env=None):
    r = subprocess.run(["bash", GUARD, "acquire", str(pid)], capture_output=True, text=True, env=env)
    return (r.stdout + r.stderr).strip().splitlines()[0] if (r.stdout + r.stderr).strip() else ""


def sentry(*args):
    r = subprocess.run([sys.executable, "-B", SENTRY] + [str(a) for a in args],
                       capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout + r.stderr).strip()


def write_lease(**fields):
    os.makedirs(os.path.dirname(LEASE), exist_ok=True)
    with open(LEASE, "w") as f:
        for k, v in fields.items():
            f.write("%s=%s\n" % (k, v))


def read_lease_field(name):
    try:
        with open(LEASE) as f:
            for line in f:
                if line.startswith(name + "="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return None


def write_claim(**fields):
    os.makedirs(os.path.dirname(MARK), exist_ok=True)
    with open(MARK, "w") as f:
        for k, v in fields.items():
            f.write("%s=%s\n" % (k, v))


def clear():
    for p in (LEASE, MARK):
        try: os.remove(p)
        except OSError: pass
    # The reclaim gate is never freed by acquire itself, by design, so a fixture that kills a racer
    # mid reclaim would wedge every case after it. Break glass is the product's recovery; this is
    # the suite's, and it runs between cases rather than inside one.
    try: os.rmdir(LEASE + ".reclaim")
    except OSError: pass


def race(n=8, seed_dead=False):
    """n concurrent acquires, each with its own LIVE holder pid. Exactly one must win.

    seed_dead plants a lease naming a positively DEAD pid first, which is a DIFFERENT path through
    acquire and the one nothing used to probe: the free-lease race only ever exercises the atomic
    create, never the reclaim. Measured 2026-09-22 before the reclaim gate existed: 3, 3, 2, 3, 2
    winners of eight over five trials."""
    clear()
    if seed_dead:
        write_lease(pid=99998, at=int(time.time()), host="x", branch="y")   # 99998: no such process
    holders = [live_pid() for _ in range(n)]
    outs = []
    procs = [subprocess.Popen(["bash", GUARD, "acquire", str(h.pid)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for h in holders]
    for p in procs:
        outs.append((p.communicate()[0] or "").strip())
    for h in holders:
        h.kill(); h.wait(timeout=5)
    return sum(1 for o in outs if "ACQUIRED" in o), sum(1 for o in outs if "REFUSED" in o)


def main():
    if not os.path.isfile(GUARD):
        # NOT APPLICABLE, which is not NO-DATA. This test asks whether the INSTALLED lease grants
        # itself to one racer. Where no loop is installed under this HOME, as in the hermetic
        # export tree the push gate builds, there is no lease to race and no driver that could
        # take it twice, so the claim is vacuously safe rather than unknown. Everywhere a loop IS
        # installed the full race runs below and a wrong answer is exit 1.
        print("NOT APPLICABLE: no loop is installed under this HOME (%s), so there is no lease to "
              "race and no driver that could hold it twice" % GUARD)
        return 0
    saved = saved_claim = None
    if os.path.isfile(LEASE):
        with open(LEASE) as f: saved = f.read()
    if os.path.isfile(MARK):
        with open(MARK) as f: saved_claim = f.read()
    try:
        cases = []
        # the race, repeated: a lock that is right once by luck is not a lock
        for attempt in range(3):
            won, refused = race(8)
            cases.append(("round %d grants exactly one of eight" % (attempt + 1), won == 1 and refused == 7,
                          "acquired %d, refused %d" % (won, refused)))

        holder = live_pid(60); other = live_pid(60)
        clear()
        write_lease(pid=99998, at=int(time.time()), host="x", branch="y")   # 99998: no such process
        cases.append(("a dead holder is taken over", "ACQUIRED" in acquire(holder.pid), ""))

        write_lease(pid=holder.pid, at=int(time.time()), host="x", branch="y")
        cases.append(("a live fresh holder is refused", "REFUSED" in acquire(other.pid), ""))

        write_lease(pid=holder.pid)                                          # half written: no at line yet
        rc, out = guard("acquire", other.pid)
        cases.append(("a half written lease is refused, never stolen", "REFUSED" in out, out[:70]))

        # DEFECT 5, MID PASS EXPIRY. A build is allowed 3000s and the lease renews once per pass at
        # 900s, so a legitimate long pass used to be evicted from its own lease mid landing. A live
        # holder must be refused however old it is; only death frees the tree.
        write_lease(pid=holder.pid, at=int(time.time()) - 5000, host="x", branch="y")
        rc, out = guard("acquire", other.pid)
        cases.append(("a LIVE holder past the ttl is NOT evicted", "REFUSED" in out and rc != 0, out[:90]))
        cases.append(("and the live holder still owns the lease", read_lease_field("pid") == str(holder.pid),
                      "pid now %s" % read_lease_field("pid")))

        # DEFECT 3, EPERM IS NOT DEATH. pid 1 is real and live and owned by another uid, so
        # `kill -0 1` fails on this machine while `ps -p 1 -o pid=` finds it. Reading that failure
        # as death is how a live loop's lease became takeable.
        write_lease(pid=1, at=int(time.time()), host="x", branch="y")
        rc, out = guard("acquire", other.pid)
        cases.append(("a live pid under another uid is not read as dead", "REFUSED" in out and rc != 0, out[:90]))

        # DEFECT 4, LEASE POISON, split into its TWO conditions because one fixture that trips two
        # guards proves neither. A future stamp and a dead holder are separate facts and the code
        # now answers them separately: liveness decides, the stamp only colours the message.
        # (a) A FUTURE STAMP NAMING A LIVE HOLDER IS REFUSED. Before 2026-09-22 this read ACQUIRED,
        #     which means moving this machine's clock back more than SKEW took the tree from a
        #     running process. The stamp is +3600 and NOT past the ttl in the other direction, so
        #     only the future-stamp guard can refuse this fixture.
        write_lease(pid=holder.pid, at=int(time.time()) + 3600, host="x", branch="y")
        rc, out = guard("acquire", other.pid)
        cases.append(("a FUTURE stamp naming a LIVE holder is refused, not stolen",
                      "REFUSED" in out and rc != 0, out[:90]))
        cases.append(("and that live holder still owns the lease", read_lease_field("pid") == str(holder.pid),
                      "pid now %s" % read_lease_field("pid")))
        rc, out = guard("check")
        cases.append(("check agrees: a live holder with a future stamp is HELD, not STALE",
                      "HELD" in out and rc == 1, out[:90]))
        # (b) POISON RECOVERY IS NOT TRADED AWAY. The same future stamp naming a DEAD pid is still
        #     cleared, so a planted line does not lock the tree for the night.
        write_lease(pid=99998, at=int(time.time()) + 3600, host="x", branch="y")
        rc, out = guard("acquire", other.pid)
        cases.append(("a FUTURE stamp naming a DEAD pid is still cleared", "ACQUIRED" in out and rc == 0, out[:90]))

        # DEFECT 7, THE DOUBLE ACQUIRE RACE ON ONE DEAD HOLDER. noclobber makes CREATION atomic and
        # nothing more: read, decide and unlink were three operations, so two racers both reading
        # dead D both removed the lease, the second removing the FIRST'S NEW ONE, and both won.
        # POSIX offers no compare-and-unlink, so the unlink is serialised behind an atomic exclusive
        # mkdir instead. Repeated, because a race that is right once by luck is not a fix.
        for attempt in range(3):
            won, refused = race(8, seed_dead=True)
            cases.append(("dead-holder round %d grants exactly one of eight" % (attempt + 1),
                          won == 1 and refused == 7, "acquired %d, refused %d" % (won, refused)))

        # EXACTLY ONE RACER, the degenerate edge. A mutual exclusion primitive that refuses the only
        # caller is as broken as one that grants everybody, and nothing else here would catch it.
        won, refused = race(1)
        cases.append(("exactly one racer against a free lease wins it", won == 1 and refused == 0,
                      "acquired %d, refused %d" % (won, refused)))

        # THE HOLDER DIES MID HOLD. A crashed driver must never lock the tree for the night, and
        # this is the recovery the live-holder rule above must not have cost.
        doomed = live_pid(60)
        write_lease(pid=doomed.pid, at=int(time.time()), host="x", branch="y")
        doomed.kill(); doomed.wait()
        rc, out = guard("acquire", other.pid)
        cases.append(("a holder that dies mid hold is taken over", "ACQUIRED" in out and rc == 0, out[:90]))

        # THE SAME PID RESTARTING AND RECLAIMING ITS OWN OLD LEASE. Named on this estate as the
        # edge nobody enumerates: the actor is the same as last time, so every "is it someone else"
        # test answers no and the stale-own-lease case falls between them.
        write_lease(pid=holder.pid, at=int(time.time()) - 5000, host="x", branch="y")
        rc, out = guard("acquire", holder.pid)
        cases.append(("a pid reclaims its OWN old lease", "ACQUIRED" in out and rc == 0, out[:90]))
        cases.append(("and its reclaimed lease carries a fresh stamp",
                      abs(int(read_lease_field("at") or 0) - int(time.time())) < 120,
                      "at now %s" % read_lease_field("at")))

        # A RECYCLED PID IS NOT THE HOLDER. Raised by adversarial review 2026-09-22: once eviction
        # is gated on liveness, a holder that died and whose pid the OS reused onto an unrelated
        # process is protected forever, because that process is alive. The holder is LIVE and the
        # stamp is FRESH here, so nothing except the recorded start time can free this lease.
        write_lease(pid=holder.pid, at=int(time.time()), start="Thu Jan  1 00:00:00 1970", host="x", branch="y")
        rc, out = guard("acquire", other.pid)
        cases.append(("a live pid whose START does not match is treated as recycled",
                      "ACQUIRED" in out and rc == 0, out[:90]))
        # and the same lease with the RIGHT start is still refused, so the field cannot be a
        # blanket "ignore liveness" switch.
        # WORDS, NOT RAW TEXT (2026-10-01): ps pads a one digit day ("Thu Oct  1"), and loop_guard.sh stores and compares
        # `echo $(ps ...)`, which collapses the padding; the raw text made this case red on days 1 to 9 of every month.
        real_start = " ".join(subprocess.run(["ps", "-p", str(holder.pid), "-o", "lstart="],
                                             capture_output=True, text=True).stdout.split())
        write_lease(pid=holder.pid, at=int(time.time()), start=real_start, host="x", branch="y")
        rc, out = guard("acquire", other.pid)
        cases.append(("a live pid whose START matches is still refused", "REFUSED" in out and rc != 0, out[:90]))

        # THE CLOCK DECIDES NOTHING. Stronger than "the checks are in the right order": against one
        # live holder, every possible stamp must produce the SAME verdict, so no clock fault of any
        # size can change who owns the tree. The stamp is reported, never acted on.
        verdicts = []
        for label, at in (("now", int(time.time())), ("5000s old", int(time.time()) - 5000),
                          ("3600s future", int(time.time()) + 3600), ("garbage", "not-a-number")):
            write_lease(pid=holder.pid, at=at, host="x", branch="y")
            rc, out = guard("acquire", other.pid)
            verdicts.append((label, rc, "REFUSED" in out))
        cases.append(("every stamp gives the same verdict against one live holder",
                      all(rc != 0 and ref for _, rc, ref in verdicts),
                      "; ".join("%s rc=%d" % (l, rc) for l, rc, _ in verdicts)))

        # AN UNREADABLE HOLDER MUST NOT PERMIT RELEASE. NO-DATA fails toward refusing, here as
        # everywhere: a lease whose pid line is absent is not provably anyone's, so it is not
        # provably yours either.
        write_lease(at=int(time.time()), host="x", branch="y")
        rc, out = guard("release", other.pid)
        cases.append(("release against an unreadable holder is refused",
                      "REFUSED" in out and rc != 0 and os.path.isfile(LEASE), out[:70]))

        # AN EMPTY LEASE FILE. Zero bytes is indistinguishable from a writer caught mid create, so
        # it refuses and leaves the file alone: the recovery is `release --force`, deliberately, and
        # not a timer that would be the same read-decide-unlink race wearing a clock.
        clear()
        open(LEASE, "w").close()
        rc, out = guard("acquire", other.pid)
        cases.append(("an EMPTY lease file is refused, never taken", "REFUSED" in out and rc != 0, out[:70]))
        cases.append(("and the empty lease file is left untouched",
                      os.path.isfile(LEASE) and os.path.getsize(LEASE) == 0, ""))

        # DEFECT 1, RELEASE WAS UNAUTHENTICATED. One command from any process freed a live lease.
        write_lease(pid=holder.pid, at=int(time.time()), host="x", branch="y")
        rc, out = guard("release", other.pid)
        cases.append(("release by a foreign pid is refused", "REFUSED" in out and rc != 0, out[:90]))
        cases.append(("and the refused release leaves the lease in place", os.path.isfile(LEASE), ""))
        rc, out = guard("release", holder.pid)
        cases.append(("release by the owner frees the lease", rc == 0 and not os.path.isfile(LEASE), out[:90]))

        # BREAK GLASS stays possible, and stays loud, for a holder that is genuinely stuck.
        write_lease(pid=holder.pid, at=int(time.time()), host="x", branch="y")
        rc, out = guard("release", "--force")
        cases.append(("release --force is a loud break glass that works",
                      rc == 0 and "BREAK-GLASS" in out and not os.path.isfile(LEASE), out[:90]))

        # DEFECT 2, RENEW WAS PID LESS. A stranger refreshed anyone's lease, so a dead holder's
        # lease could be kept alive forever and never recovered.
        old_stamp = int(time.time()) - 400
        write_lease(pid=holder.pid, at=old_stamp, host="x", branch="y")
        rc, out = guard("renew", other.pid)
        cases.append(("renew by a foreign pid is refused", "REFUSED" in out and rc != 0, out[:90]))
        cases.append(("and the refused renew leaves the stamp untouched",
                      read_lease_field("at") == str(old_stamp), "at now %s" % read_lease_field("at")))
        rc, out = guard("renew", holder.pid)
        cases.append(("renew by the owner refreshes the stamp",
                      rc == 0 and read_lease_field("at") != str(old_stamp), out[:90]))

        # DEFECT 6, THE WORKTREE CLAIM WAS NEVER RENEWED. Same TTL, claimed once at startup, so it
        # was honoured for fifteen minutes of a twelve hour night.
        clear()
        write_claim(pid=holder.pid, at=time.time() - 5000, root=ROOT)
        rc, out = sentry("claim", other.pid)
        cases.append(("the worktree claim honours a LIVE claimant past the ttl", "REFUSED" in out and rc == 1, out[:90]))
        # check() is re-armed from scratch rather than read after the refusal above, because a
        # refused claim leaves whatever the previous verb wrote and a case that only passes on
        # another case's leftovers is not a case.
        write_claim(pid=holder.pid, at=time.time() - 5000, root=ROOT)
        rc, out = sentry("check")
        cases.append(("and check reports it HELD rather than stale", "HELD" in out and rc == 1, out[:90]))
        # RECOVERY, unweakened: a dead claimant still frees the tree.
        write_claim(pid=99998, at=time.time(), root=ROOT)
        rc, out = sentry("claim", other.pid)
        cases.append(("a DEAD claimant is still taken over", "CLAIMED" in out and rc == 0, out[:90]))

        # THE SIBLING UNLINK SITE. `release` here was an unconditional remove while the lease's own
        # release had already been authenticated, which is the lazy fix that leaves the bug and adds
        # a second code path. Found by sweeping every site that unlinks either file.
        write_claim(pid=holder.pid, at=time.time(), root=ROOT)
        rc, out = sentry("release", other.pid)
        cases.append(("sentry release by a foreign pid is refused",
                      "REFUSED" in out and rc == 1 and os.path.isfile(MARK), out[:90]))
        rc, out = sentry("release", holder.pid)
        cases.append(("sentry release by the claimant frees the tree",
                      rc == 0 and not os.path.isfile(MARK), out[:90]))
        write_claim(pid=holder.pid, at=time.time(), root=ROOT)
        rc, out = sentry("release", "--force")
        cases.append(("sentry release --force is a loud break glass that works",
                      rc == 0 and "BREAK-GLASS" in out and not os.path.isfile(MARK), out[:90]))

        # THE BACKWARDS CLOCK, in the sentry as well as the lease, because the same wrong order was
        # in both. The stamp is fresh-but-future and the claimant is LIVE, so only the liveness rule
        # can refuse this fixture: the ttl branch cannot fire on a stamp ahead of now.
        write_claim(pid=holder.pid, at=time.time() + 3600, root=ROOT)
        rc, out = sentry("claim", other.pid)
        cases.append(("a FUTURE stamp naming a LIVE claimant is refused, not taken",
                      "REFUSED" in out and rc == 1, out[:90]))
        write_claim(pid=holder.pid, at=time.time() + 3600, root=ROOT)
        rc, out = sentry("check")
        cases.append(("and sentry check reports it HELD rather than stale", "HELD" in out and rc == 1, out[:90]))
        # Poison recovery kept where it is safe: future stamp on a DEAD claimant is still taken.
        write_claim(pid=99998, at=time.time() + 3600, root=ROOT)
        rc, out = sentry("claim", other.pid)
        cases.append(("a FUTURE stamp naming a DEAD claimant is still taken", "CLAIMED" in out and rc == 0, out[:90]))

        holder.kill(); holder.wait(timeout=5)
        other.kill(); other.wait(timeout=5)
        bad = [(n, d) for n, ok, d in cases if not ok]
        for n, ok, d in cases:
            print("%-56s %s%s" % (n, "ok" if ok else "FAIL", ("  " + d) if d and not ok else ""))
        print("\n%d case(s), %s" % (len(cases), "OK" if not bad else "FAILED"))
        return 1 if bad else 0
    finally:
        clear()
        if saved is not None:
            with open(LEASE, "w") as f: f.write(saved)
        if saved_claim is not None:
            with open(MARK, "w") as f: f.write(saved_claim)


if __name__ == "__main__":
    sys.exit(main())
