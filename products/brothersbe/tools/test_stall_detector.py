#!/usr/bin/env python3
"""Forced-condition tests for the stall detector v2.

Run: python3 tools/test_stall_detector.py

The bar, from the design doc and from the night that taught it: A TEST THAT
CANNOT FAIL PROVES NOTHING. Every one of the nine conditions is FORCED live
against a real fixture and its alert line asserted, remediation included, and
the healthy fixture is asserted silent. Inputs are injected (paths, a stub
fence command, thresholds); verdict logic is never stubbed.

The stub fence command is a real executable printing the same line the fence
hook's `fences` subcommand prints, so the detector's parser is exercised for
real; one test runs the REAL fence hook against a real registry to prove the
default wiring parses too.
"""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "sbe_stall_detector", os.path.join(HERE, "sbe_stall_detector.py"))
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)

_hb_spec = importlib.util.spec_from_file_location(
    "sbe_heartbeat", os.path.join(HERE, "sbe_heartbeat.py"))
hb = importlib.util.module_from_spec(_hb_spec)
_hb_spec.loader.exec_module(hb)


def write(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def age(path, minutes):
    """Push a file's mtime into the past, the fixture's time machine."""
    ts = time.time() - minutes * 60
    os.utime(path, (ts, ts))


class DetectorCase(unittest.TestCase):
    """A real temp git repo, a stub fence command, and a one-pass runner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "t@t"],
                       check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "t"],
                       check=True)
        write(os.path.join(self.root, "README.md"), "fixture\n")
        subprocess.run(["git", "-C", self.root, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-q", "-m", "init"],
                       check=True)
        self.heartbeat = os.path.join(self.root, ".sbe", "runtime", "heartbeat.jsonl")
        self.alerts = os.path.join(self.root, ".sbe", "runtime", "alerts.jsonl")
        self.fence_stub = os.path.join(self.root, "fence-stub.sh")
        self.set_fences(0)

    def tearDown(self):
        self._tmp.cleanup()

    def set_fences(self, n, sessions=()):
        """A real executable printing what the fence hook's `fences`
        subcommand prints, so the detector's parser runs for real."""
        lines = ["#!/bin/sh"]
        for s in sessions:
            lines.append(
                "echo 'LIVE STATE.md | agent a | session %s | files x' >&2" % s)
        lines.append("echo '%d live fence line(s) enforceable from .' >&2" % n)
        write(self.fence_stub, "\n".join(lines) + "\n")
        os.chmod(self.fence_stub, os.stat(self.fence_stub).st_mode
                 | stat.S_IXUSR)

    def beat(self, minutes_ago=0, session="s1", ok=True, thash="aaa",
             event="PostToolUse", transcript_bytes=None):
        row = {"epoch": int(time.time() - minutes_ago * 60),
               "ts": "t", "session": session, "event": event,
               "tool": "Bash", "thash": thash, "ok": ok}
        if transcript_bytes is not None:
            row["transcript_bytes"] = transcript_bytes
        with open(self.heartbeat, "a") as fh:
            fh.write(json.dumps(row) + "\n")

    def ensure_runtime_dir(self):
        d = os.path.dirname(self.heartbeat)
        if not os.path.isdir(d):
            os.makedirs(d)

    def run_once(self, **overrides):
        """One real pass through main() with --once, output captured."""
        argv = ["--root", self.root, "--once", "--no-banners",
                "--heartbeat", self.heartbeat, "--alerts", self.alerts,
                "--fence-cmd", "/bin/sh %s" % self.fence_stub,
                # A pattern that matches no real process on any host, so the
                # fixture's process-table answer is deterministic: pgrep -f
                # with an unmatchable literal.
                "--pgrep-patterns", "no-such-process-marker-xyzq"]
        for k, v in overrides.items():
            argv += ["--%s" % k.replace("_", "-"), str(v)]
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "sbe_stall_detector.py")] + argv,
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # The CompletedProcess, not a (stdout, stderr) 2-tuple: the honesty
        # meta-test reads a 2-tuple return as a possible verdict source.
        return proc

    def quiet_tree(self):
        """Age every file so no recent-change signal remains."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for f in filenames:
                age(os.path.join(dirpath, f), 60)


class TestEachConditionFires(DetectorCase):

    def test_true_stall_fires_and_names_the_release_remediation(self):
        self.set_fences(2)
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("TRUE_STALL", out)
        self.assertIn("2 fence(s)", out)
        self.assertIn("RELEASED", out, "the remediation must be named")

    def test_dead_worker_holding_work_fires_with_salvage_commands(self):
        w = os.path.join(self.root, ".claude", "worktrees", "agent-deadbeef01")
        os.makedirs(w)
        subprocess.run(["git", "init", "-q"], cwd=w, check=True)
        write(os.path.join(w, "half-finished.py"), "x = 1\n")
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("DEAD_WORKER_HOLDING_WORK", out)
        self.assertIn("agent-deadbeef01(1)", out)
        self.assertIn("salvage.patch", out, "the salvage command must be named")
        self.assertIn("do NOT restart from zero", out)

    def test_the_detectors_own_ledgers_are_not_a_dead_workers_work(self):
        """Found by arming the detector for real: the heartbeat hook writes
        .sbe/runtime inside every worktree an agent runs in, and counting
        that as the worker's unfinished work made the first armed run report
        a dead worker holding nothing but the monitor's own output."""
        w = os.path.join(self.root, ".claude", "worktrees", "agent-selfnoise1")
        os.makedirs(w)
        subprocess.run(["git", "init", "-q"], cwd=w, check=True)
        write(os.path.join(w, ".sbe", "runtime", "heartbeat.jsonl"), "{}\n")
        write(os.path.join(w, ".sbe", "runtime", "alerts.jsonl"), "{}\n")
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertNotIn("DEAD_WORKER_HOLDING_WORK", out)

    def test_real_work_beside_the_detectors_ledgers_still_alerts(self):
        """The other half, so the filter above cannot silence a real find."""
        w = os.path.join(self.root, ".claude", "worktrees", "agent-realwork1")
        os.makedirs(w)
        subprocess.run(["git", "init", "-q"], cwd=w, check=True)
        write(os.path.join(w, ".sbe", "runtime", "heartbeat.jsonl"), "{}\n")
        write(os.path.join(w, "half-finished.py"), "x = 1\n")
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("DEAD_WORKER_HOLDING_WORK", out)
        self.assertIn("agent-realwork1(1)", out,
                      "the count must exclude the detector's own ledgers")

    def test_a_salvaged_marker_silences_the_dead_worker_alert(self):
        w = os.path.join(self.root, ".claude", "worktrees", "agent-deadbeef01")
        os.makedirs(w)
        subprocess.run(["git", "init", "-q"], cwd=w, check=True)
        write(os.path.join(w, "half-finished.py"), "x = 1\n")
        write(os.path.join(w, ".salvaged"), "saved 2026-08-11\n")
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertNotIn("DEAD_WORKER_HOLDING_WORK", out)

    def test_orphaned_fence_fires_when_the_fence_session_has_no_heartbeat(self):
        self.set_fences(1, sessions=["ghost-session"])
        self.ensure_runtime_dir()
        self.quiet_tree()
        # Another writer is alive and editing (a fresh file, inside the stall
        # window, which also keeps TRUE_STALL quiet), but the GHOST session on
        # the fence has no heartbeat at all: that is the orphan shape.
        write(os.path.join(self.root, "active-edit.py"), "y = 2\n")
        out = self.run_once().stdout
        self.assertIn("ORPHANED_FENCE", out)
        self.assertIn("ghost-session", out)
        self.assertIn("stale claim refuses the real writer", out)

    def test_ageing_uncommitted_fires_on_old_dirty_files(self):
        p = os.path.join(self.root, "README.md")
        write(p, "modified and forgotten\n")
        age(p, 60)
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("AGEING_UNCOMMITTED", out)
        self.assertIn("commit on the working branch", out)

    def test_disk_floor_fires_against_the_real_disk(self):
        # The threshold is injected, the measurement is real: any machine has
        # less than a million GiB free, so the condition is forced live
        # without stubbing the probe.
        out = self.run_once(disk_floor_gib=1000000).stdout
        self.assertIn("DISK_FLOOR", out)
        self.assertIn("under the 1000000 GiB floor", out)

    def test_token_burn_fires_when_spend_grows_in_a_quiet_tree(self):
        # The ledger lives under .sbe, which the recent-change walk prunes on
        # purpose: spend growing is not work progressing, and a ledger inside
        # the walk would mask the very condition it evidences.
        ledger = os.path.join(self.root, ".sbe", "spend.jsonl")
        self.quiet_tree()
        write(ledger, "{\"out_tokens\": 999}\n")  # fresh mtime = growing spend
        out = self.run_once(spend_ledger=ledger).stdout
        self.assertIn("TOKEN_BURN_NO_PROGRESS", out)
        self.assertIn("interrupt it and read its last turns", out)

    def test_repeat_command_loop_fires_on_five_identical_failures(self):
        self.ensure_runtime_dir()
        for _ in range(5):
            self.beat(minutes_ago=20, ok=False, thash="feedfeed")
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("REPEAT_COMMAND_LOOP", out)
        self.assertIn("feedfeed", out)
        self.assertIn("stop the retry loop", out)

    def test_context_fill_fires_once_per_session(self):
        self.ensure_runtime_dir()
        self.beat(minutes_ago=1, session="big-one",
                  transcript_bytes=99 * 1024 * 1024)
        out = self.run_once().stdout
        self.assertIn("CONTEXT_FILL_APPROACHING", out)
        self.assertIn("big-one", out)
        self.assertIn("handover", out)

    def test_handover_owed_fires_when_the_pack_never_lands(self):
        """The obligation, not the moment: an over-the-line session with an
        empty handover directory must be told again, with the directory
        named. Grace zero so one pass reaches the first escalation step."""
        self.ensure_runtime_dir()
        hdir = os.path.join(self.root, "handover-fixture")
        os.makedirs(hdir)
        self.beat(minutes_ago=1, session="owing-one",
                  transcript_bytes=99 * 1024 * 1024)
        out = self.run_once(handover_dir=hdir, handover_grace_min=0).stdout
        self.assertIn("HANDOVER_OWED", out)
        self.assertIn("owing-one", out)
        self.assertIn("NO-DATA", out, "an empty pack directory is NO-DATA")
        self.assertIn("baton", out, "the remediation must name the ceremony")

    def test_a_landed_pack_silences_the_handover_alert(self):
        """Calibration for the test above: same over-the-line session, but a
        pack file newer than the obligation. Silence here proves the first
        test fires on the missing pack and not merely on the context line."""
        self.ensure_runtime_dir()
        hdir = os.path.join(self.root, "handover-fixture")
        os.makedirs(hdir)
        with open(os.path.join(hdir, "00-HANDOVER.md"), "w") as fh:
            fh.write("what is finished, what is in flight, live fences\n")
        self.beat(minutes_ago=1, session="owing-one",
                  transcript_bytes=99 * 1024 * 1024)
        out = self.run_once(handover_dir=hdir, handover_grace_min=0).stdout
        self.assertNotIn("HANDOVER_OWED", out)

    def test_api_error_streak_fires_on_failures_across_commands(self):
        self.ensure_runtime_dir()
        for h in ("h1", "h2", "h3", "h4"):
            self.beat(minutes_ago=20, ok=False, thash=h)
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("API_ERROR_STREAK", out)
        self.assertIn("stuck on the provider", out)


class TestSilenceMeansHealthy(DetectorCase):

    def test_a_healthy_fixture_is_silent(self):
        """Clean tree, no fences, fresh heartbeat, sane thresholds: not one
        alert line. This is the test whose absence muted v1.

        The disk reading is injected above the floor: the real free space on
        whatever machine runs this test is not part of the fixture, so the
        test cannot flip red on a full disk."""
        self.ensure_runtime_dir()
        self.beat(minutes_ago=1)
        proc = self.run_once(disk_free_gib_override=1000)
        out, err = proc.stdout, proc.stderr
        self.assertEqual(out, "", "expected silence, got: %r (stderr: %s)"
                         % (out, err))

    def test_disk_floor_fires_on_an_injected_low_reading(self):
        """The other half of the injected-disk property: a reading under the
        floor still alerts, with that exact number, regardless of the real
        disk on the host machine."""
        self.ensure_runtime_dir()
        self.beat(minutes_ago=1)
        out = self.run_once(disk_free_gib_override=5).stdout
        self.assertIn("DISK_FLOOR", out)
        self.assertIn("5 GiB free, under the 15 GiB floor.", out)

    def test_a_recent_heartbeat_suppresses_the_stall_and_names_its_source(self):
        """The exact v1 false positive: a long quiet test run. Here the tree
        is old and a fence is open, but a heartbeat within the window means
        busy, so TRUE_STALL must stay quiet."""
        self.set_fences(1)
        self.quiet_tree()
        self.ensure_runtime_dir()
        self.beat(minutes_ago=1)
        out = self.run_once().stdout
        self.assertNotIn("TRUE_STALL", out)

    def test_alerts_land_in_the_ledger_with_their_remediation(self):
        self.set_fences(1)
        self.quiet_tree()
        out = self.run_once().stdout
        self.assertIn("TRUE_STALL", out)
        with open(self.alerts) as fh:
            rows = [json.loads(l) for l in fh.read().splitlines()]
        self.assertTrue(any(r["condition"] == "TRUE_STALL" and
                            r["remediation"] and r["liveness_source"]
                            for r in rows), rows)

    def test_a_broken_fence_command_is_loud_and_produces_no_false_stall(self):
        write(self.fence_stub, "#!/bin/sh\nexit 7\n")
        os.chmod(self.fence_stub, os.stat(self.fence_stub).st_mode
                 | stat.S_IXUSR)
        self.quiet_tree()
        proc = self.run_once(); out, err = proc.stdout, proc.stderr
        self.assertNotIn("TRUE_STALL", out)
        self.assertIn("NO-DATA this pass", err,
                      "a failed probe must be loud on stderr, never silent")


class TestRealFenceHookWiring(DetectorCase):

    def test_the_default_fence_command_parses_the_real_hook(self):
        """The real fence hook, a real registry with one live claim, and the
        detector's own default command construction: the count must arrive."""
        write(os.path.join(self.root, "STATE.md"),
              "### Fence: x\n"
              "- OPEN. agent: someone (sole writer) | files: x.py |\n"
              "  check: true\n")
        cfg = sd.Config(sd.main.__wrapped__ if False else _ns(root=self.root))
        count, _sessions, ok = sd.fence_count_and_sessions(cfg)
        self.assertTrue(ok, "the real fences subcommand must parse")
        self.assertEqual(count, 1)


def _ns(root):
    """An argparse-shaped namespace with defaults, for direct Config builds."""
    import argparse
    ns = argparse.Namespace(
        root=root, once=True, poll_seconds=180, stall_min=12, dirty_min=45,
        disk_floor_gib=15, context_warn_bytes=20 * 1024 * 1024,
        heartbeat=None, alerts=None, spend_ledger=None, worktrees=None,
        fence_cmd=None, pgrep_patterns=sd.BUSY_PATTERNS, banners=False,
        handover_dir=None, handover_grace_min=20)
    return ns


class TestHeartbeatWriter(unittest.TestCase):

    def test_a_hook_payload_becomes_one_ledger_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "hb.jsonl")
            env = dict(os.environ)
            env[hb.ENV_PATH] = ledger
            payload = {"hook_event_name": "PostToolUse", "session_id": "s9",
                       "tool_name": "Bash",
                       "tool_input": {"command": "echo hello"}}
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "sbe_heartbeat.py")],
                input=json.dumps(payload), capture_output=True, text=True,
                env=env, timeout=30)
            self.assertEqual(proc.returncode, 0)
            with open(ledger) as fh:
                row = json.loads(fh.read().strip())
            self.assertEqual(row["session"], "s9")
            self.assertEqual(row["tool"], "Bash")
            self.assertTrue(row["ok"])
            self.assertEqual(len(row["thash"]), 12)

    def test_a_failure_event_is_marked_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "hb.jsonl")
            env = dict(os.environ)
            env[hb.ENV_PATH] = ledger
            payload = {"hook_event_name": "PostToolUseFailure",
                       "session_id": "s9", "tool_name": "Bash",
                       "tool_input": {"command": "false"}}
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "sbe_heartbeat.py")],
                input=json.dumps(payload), capture_output=True, text=True,
                env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(ledger) as fh:
                row = json.loads(fh.read().strip())
            self.assertFalse(row["ok"])

    def test_garbage_stdin_never_blocks_and_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "sbe_heartbeat.py")],
            input="{not json", capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_the_ledger_never_stores_command_text(self):
        """The privacy property: commands can carry prompts and paths; only
        a hash prefix may land in the monitoring file."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "hb.jsonl")
            env = dict(os.environ)
            env[hb.ENV_PATH] = ledger
            # A canary that proves the property without minting a
            # credential-shaped string. The first version of this fixture used
            # a realistic `sk-` literal and tripped the push secret scan on
            # every future range, which is how a scan teaches people to ignore
            # it. What the test needs is a distinctive token that must not
            # appear in the ledger, not a convincing forgery of a real key.
            secret = "export TOKEN=NOTAKEY-canary-must-not-be-logged"
            payload = {"hook_event_name": "PostToolUse", "session_id": "s",
                       "tool_name": "Bash", "tool_input": {"command": secret}}
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "sbe_heartbeat.py")],
                input=json.dumps(payload), capture_output=True, text=True,
                env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(ledger) as fh:
                content = fh.read()
            self.assertNotIn("NOTAKEY-canary", content)


if __name__ == "__main__":
    unittest.main(verbosity=1)
