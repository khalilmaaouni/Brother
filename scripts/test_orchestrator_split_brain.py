"""ORCH-04: the split-brain gauntlet for scripts/orchestrator_authority.py.

test_orchestrator_authority.py already proves each function of that module in
isolation (acquire refuses a live scope, renew refuses a stale epoch, a
corrupt store never reads as free, and so on). This file does not repeat any
of that. It exists to prove a SYSTEM-LEVEL property that no single function
call can express: driven through one adversarial sequence, end to end, two
contending orchestrators never once both believe they hold authority over
the same scope at the same time, and every takeover that happens along the
way is journaled with the facts that let a human reconstruct it later.

THE SEQUENCE (see the module docstring in orchestrator_authority.py for why
the epoch exists at all):
  1. Fable and Codex both attempt to claim the same scope at the same moment.
  2. Both then attempt to renew.
  3. The winner pauses (the clock advances; nobody calls renew for it).
  4. The loser retries before the TTL expires and is refused.
  5. The winner dies (its last process already exited; nothing releases it).
  6. The loser takes over after the TTL expires.
  7. The original winner wakes up and tries to act. That stale action is
     refused.

WHY REAL PROCESSES, NOT THREADS. Two threads in one interpreter share the
same Python-level state and would not exercise the file-level exclusion
(claim_store.Lock's O_CREAT|O_EXCL primitive) that is the actual mechanism
keeping two orchestrators from both winning the initial race. Every action
below that could plausibly race (the double acquire, and every polling check
along the way) runs in its own subprocess, given the clock explicitly, never
falling back to the real wall clock. See _run_op and _CHILD below.

THE BAD STATE A GREEN RUN OF THIS FILE WOULD ALSO PASS, if the defenses
below were missing: the sequence never actually executes (every subprocess
fails to start, or silently no-ops) and every assertion holds vacuously over
an empty list of results. test_the_gauntlet defends against exactly that by
asserting: every subprocess exits 0 and returns parsed JSON (a crash or a
missing print would fail loudly, not vanish); the winner and loser are two
distinct instance names actually decided by the race, never assumed; the
polling loop ran a known, counted number of times; and the audit trail has
the exact number of entries this sequence produces, not merely "at least
one". A version of this file that forgot any one of those checks could stay
green while the harness underneath it did nothing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchestrator_authority as A  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def store():
    return os.path.join(tempfile.mkdtemp(), "authority.json")


# One child process, one operation. Params travel as a single JSON argv
# string so floats (the injected clock) survive exactly, and the child
# prints exactly one JSON line back. The child asserts `now` is not None
# before it ever calls into the module: the one way real wall time could
# sneak into this gauntlet is a caller here forgetting to pass it, and that
# must fail loudly in the child, not silently race real time in a TTL check.
_CHILD = """
import sys, json
sys.path.insert(0, %r)
import orchestrator_authority as A

p = json.loads(sys.argv[1])
now = p["now"]
assert now is not None, "child received no explicit clock; refusing to fall back to wall time"
op = p["op"]
out = {"op": op, "now_received": now}
try:
    if op == "acquire":
        lease = A.acquire(p["store"], p["run_id"], p["scope"], p["orchestrator"],
                           p["instance"], p["ttl"], now=now)
        out["ok"] = True
        out["epoch"] = lease.epoch
    elif op == "renew":
        lease = A.renew(p["store"], p["run_id"], p["scope"], p["instance"],
                         p["epoch"], p["ttl"], now=now)
        out["ok"] = True
        out["epoch"] = lease.epoch
    elif op == "takeover":
        lease = A.takeover(p["store"], p["run_id"], p["scope"], p["orchestrator"],
                            p["instance"], p["ttl"], p["reason"], now=now)
        out["ok"] = True
        out["epoch"] = lease.epoch
    elif op == "check":
        out["authorized"] = A.check(p["store"], p["run_id"], p["scope"],
                                     p["instance"], p["epoch"], now=now)
    else:
        raise ValueError("unknown op %%r" %% op)
except A.AuthorityRefused as exc:
    out["ok"] = False
    out["refused"] = str(exc)
print(json.dumps(out))
""" % HERE


def _spawn(params):
    """Start one child process for one operation. Returns the Popen so the
    caller can start several before waiting on any of them, which is the
    only way to actually race two real processes against each other."""
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, json.dumps(params)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _finish(proc, label):
    """Wait for a spawned child and parse its one JSON line. Fails loudly
    (not vacuously) on a nonzero exit or unparseable output, since that is
    exactly the "sequence silently did not execute" bad state this file
    must not let through."""
    out, err = proc.communicate(timeout=30)
    if proc.returncode != 0:
        raise AssertionError(
            "%s: child exited %d, stderr:\n%s" % (label, proc.returncode, err))
    if not out.strip():
        raise AssertionError("%s: child printed nothing" % label)
    return json.loads(out.strip())


class SplitBrainGauntlet(unittest.TestCase):

    def test_the_gauntlet(self):
        p = store()
        run_id = "R1"
        scope = "S-gauntlet"
        ttl = 60

        dual_authority_moments = 0  # the counter assertion A is named after
        polls_run = 0

        def poll(step_label, queries):
            """Ask check() from both instances, as real subprocesses, at
            one moment in the sequence. queries: list of (role, instance,
            epoch, now). Returns {role: authorized_bool}. Asserts, right
            here, that at most one role is authorized: assertion B, made at
            every step, not only at the end."""
            nonlocal dual_authority_moments, polls_run
            procs = [(role, _spawn({"op": "check", "store": p, "run_id": run_id,
                                     "scope": scope, "instance": instance,
                                     "epoch": epoch, "now": now}))
                     for role, instance, epoch, now in queries]
            results = {role: _finish(proc, "%s/%s check" % (step_label, role))["authorized"]
                       for role, proc in procs}
            true_count = sum(1 for v in results.values() if v)
            self.assertLessEqual(
                true_count, 1,
                "%s: more than one instance held authority at once: %r"
                % (step_label, results))
            if true_count >= 2:
                dual_authority_moments += 1
            polls_run += 1
            return results

        # --- Step 1: Fable and Codex both attempt to claim the same scope
        # at the same moment. Two real OS processes, started before either
        # is waited on, so this actually contends on claim_store.Lock's
        # file-level exclusion rather than merely calling the function
        # twice in sequence.
        now0 = 1000.0
        race = [
            ("fable", _spawn({"op": "acquire", "store": p, "run_id": run_id,
                               "scope": scope, "orchestrator": "fable",
                               "instance": "fable-x", "ttl": ttl, "now": now0})),
            ("codex", _spawn({"op": "acquire", "store": p, "run_id": run_id,
                               "scope": scope, "orchestrator": "codex",
                               "instance": "codex-y", "ttl": ttl, "now": now0})),
        ]
        race_results = {role: _finish(proc, "race/%s" % role) for role, proc in race}
        winners = [role for role, r in race_results.items() if r["ok"]]
        losers = [role for role, r in race_results.items() if not r["ok"]]
        self.assertEqual(len(winners), 1, "expected exactly one winner: %r" % race_results)
        self.assertEqual(len(losers), 1, "expected exactly one loser: %r" % race_results)
        win_role, lose_role = winners[0], losers[0]
        win_instance = "fable-x" if win_role == "fable" else "codex-y"
        win_orchestrator = win_role
        lose_instance = "codex-y" if win_role == "fable" else "fable-x"
        lose_orchestrator = lose_role
        self.assertNotEqual(win_instance, lose_instance)
        win_epoch = race_results[win_role]["epoch"]
        self.assertEqual(win_epoch, 1)

        poll("after-race", [
            (win_role, win_instance, win_epoch, now0),
            (lose_role, lose_instance, 1, now0),
        ])

        # --- Step 2: both then attempt to renew. The winner's renew must
        # succeed (it is renewing what it actually holds); the loser's must
        # be refused (it holds no lease at all, so its instance can never
        # match the current record).
        now1 = 1010.0
        winner_renew = _finish(_spawn({
            "op": "renew", "store": p, "run_id": run_id, "scope": scope,
            "instance": win_instance, "epoch": win_epoch, "ttl": ttl, "now": now1,
        }), "renew/winner")
        self.assertTrue(winner_renew["ok"], winner_renew)
        self.assertEqual(winner_renew["epoch"], win_epoch)

        loser_renew = _finish(_spawn({
            "op": "renew", "store": p, "run_id": run_id, "scope": scope,
            "instance": lose_instance, "epoch": win_epoch, "ttl": ttl, "now": now1,
        }), "renew/loser")
        self.assertFalse(loser_renew["ok"], loser_renew)

        poll("after-renews", [
            (win_role, win_instance, win_epoch, now1),
            (lose_role, lose_instance, win_epoch, now1),
        ])

        # --- Step 3: the winner pauses. Simulated purely by advancing the
        # injected clock and calling nothing for the winner: no sleep, no
        # renew. The lease is still live (it was renewed to now1 + ttl).
        now2 = 1030.0
        poll("winner-paused", [
            (win_role, win_instance, win_epoch, now2),
            (lose_role, lose_instance, win_epoch, now2),
        ])

        # --- Step 4: the loser retries before the TTL expires (expiry is
        # now1 + ttl = 1070.0) and must be refused. Retrying here means
        # attempting to acquire again: the loser never held a lease to
        # renew, so acquiring it outright is the only retry available to it,
        # and acquire() itself refuses outright whenever the scope is live.
        now3 = 1050.0
        self.assertLess(now3, now1 + ttl, "test setup: retry must land before expiry")
        loser_retry = _finish(_spawn({
            "op": "acquire", "store": p, "run_id": run_id, "scope": scope,
            "orchestrator": lose_orchestrator, "instance": lose_instance,
            "ttl": ttl, "now": now3,
        }), "retry/loser")
        self.assertFalse(loser_retry["ok"], loser_retry)

        poll("loser-retry-refused", [
            (win_role, win_instance, win_epoch, now3),
            (lose_role, lose_instance, win_epoch, now3),
        ])

        # --- Step 5: the winner dies. Nothing to run: every process that
        # ever acted as the winner has already exited (each op above was
        # its own subprocess), and none of them ever called release(). A
        # dead process that never released is exactly the crash case
        # takeover() exists for.

        # --- Step 6: the loser takes over after the TTL expires (now4 is
        # past now1 + ttl = 1070.0).
        now4 = 1071.0
        self.assertGreater(now4, now1 + ttl, "test setup: takeover must land after expiry")
        reason = "%s stalled past its lease" % win_instance
        takeover_result = _finish(_spawn({
            "op": "takeover", "store": p, "run_id": run_id, "scope": scope,
            "orchestrator": lose_orchestrator, "instance": lose_instance,
            "ttl": ttl, "reason": reason, "now": now4,
        }), "takeover/loser")
        self.assertTrue(takeover_result["ok"], takeover_result)
        new_epoch = takeover_result["epoch"]
        self.assertEqual(new_epoch, win_epoch + 1)

        poll("after-takeover", [
            (win_role, win_instance, win_epoch, now4),
            (lose_role, lose_instance, new_epoch, now4),
        ])

        # --- Step 7: the original winner wakes up and attempts to dispatch
        # work. Modeled as the mutating call a live authority would make to
        # keep acting: a renew at the epoch it still believes it holds.
        # That stale action must be refused (assertion this unit is named
        # after, restated concretely): the epoch it presents is no longer
        # the current one.
        now5 = 1075.0
        stale_action = _finish(_spawn({
            "op": "renew", "store": p, "run_id": run_id, "scope": scope,
            "instance": win_instance, "epoch": win_epoch, "ttl": ttl, "now": now5,
        }), "stale-dispatch/winner")
        self.assertFalse(stale_action["ok"], stale_action)

        poll("winner-woken-refused", [
            (win_role, win_instance, win_epoch, now5),
            (lose_role, lose_instance, new_epoch, now5),
        ])

        # --- Defend against the vacuous-pass bad state named in the module
        # docstring: prove the sequence actually ran the number of polls it
        # was supposed to, not zero and not silently fewer.
        self.assertEqual(polls_run, 6)

        # --- Assertion A, the one this unit is named after: across the
        # whole sequence, the number of moments at which two instances both
        # answered check() True is exactly zero. Built from the counter
        # poll() incremented on every check, never inferred from "nothing
        # raised".
        self.assertEqual(dual_authority_moments, 0)

        # --- Assertion C: every takeover is journaled, and the epochs in
        # the trail are strictly increasing with no repeat and no gap.
        trail = A.audit(p, run_id)
        # Only the three calls that actually succeeded ever write a
        # transition: the winner's acquire, the winner's renew, and the
        # loser's takeover. Every refused call above (the loser's renew,
        # the loser's early retry, the winner's stale wakeup) raised before
        # ever reaching _write(), so none of them appear here. A version of
        # this file that let a refused call slip a phantom entry into the
        # trail, or that dropped a real one, fails this exact-length check.
        self.assertEqual(len(trail), 3, trail)
        kinds = [e["type"] for e in trail]
        self.assertEqual(kinds, ["acquire", "renew", "takeover"])

        takeover_entry = trail[-1]
        self.assertEqual(takeover_entry["old_owner"], win_instance)
        self.assertEqual(takeover_entry["new_owner"], lose_instance)
        self.assertEqual(takeover_entry["reason"], reason)
        self.assertEqual(takeover_entry["old_epoch"], win_epoch)
        self.assertEqual(takeover_entry["new_epoch"], new_epoch)

        # renew() never moves the epoch (it is the same lease, still held),
        # so "strictly increasing, no repeat, no gap" is a property of the
        # entries that actually mint a NEW epoch (acquire, takeover, and
        # handoff, had one occurred), not of every entry in the trail: a
        # renew's old_epoch and new_epoch are equal by design, and treating
        # that as a "repeat" would be flagging correct behaviour as a bug.
        epoch_mints = [e["new_epoch"] for e in trail if e["type"] in ("acquire", "takeover", "handoff")]
        self.assertEqual(epoch_mints, [1, 2])
        for prev, nxt in zip(epoch_mints, epoch_mints[1:]):
            self.assertEqual(nxt, prev + 1, "epoch %r to %r is not a clean +1 step" % (prev, nxt))
        self.assertEqual(sorted(set(epoch_mints)), epoch_mints, "an epoch was reused")


if __name__ == "__main__":
    unittest.main()
