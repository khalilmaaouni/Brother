#!/usr/bin/env python3
"""A role's headless Claude call runs on the loop login with a seat's protections (1.1.4, 2026-10-14).

THE DEFECT. With BROTHER_LOOP_TOKEN=on model_call._claude_run refused every Claude call that was not a native seat,
and a role (planner, repair advisor, finisher, checker, build planner) is such a call, so the intake read NO START for
all of them; with the switch off they ran on the machine's own sign in, which lapses overnight. No setting let an
unattended loop with Claude helper roles run. model_call._TokenLane now turns a role call into what a seat is: the
Keychain login on a pipe (never an environment, argv, file or log), sandbox-exec with the seat's token lane profile,
a scratch folder removed afterwards, and a refusal naming the remedy when any of it is missing.

Fakes only: a fake claude program, a fake Keychain reader (BL.READER), an injected runner or a fake program run under
the real sandbox-exec. The real Keychain, the real loop and the real model are never touched.
ONE CONDITION PER CASE, each aimed at a guard of model_call._TokenLane or the refusal it relies on:
  G_SANDBOX      the call is wrapped by the seat's sandbox head and token lane profile
  G_PIPE         the login travels on a pipe named by FD_VAR, never in the environment, argv, stdin or a file
  G_SCRUB        no credential shaped name of the parent reaches the child
  G_CLOSE        the pipe and the scratch are released on every ending (answer, failure, timeout, refusal)
  G_READER       an unreadable or missing login refuses before anything is spawned or registered
  G_NO_SANDBOX   a machine with no sandbox-exec refuses before the Keychain is asked
  G_PROGRAM      a program the sandbox cannot run refuses before the Keychain is asked
  G_OFF          with the switch off the call is byte for byte today's
  G_GUARD        _claude_run still refuses a call that is not sandboxed
  G_REAL         the REAL sandbox-exec around a fake claude: login read, home folder unreadable, nothing writable outside
Run from the repository root: python3 -B scripts/loop/test_model_call_token_roles.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import model_call as MC  # noqa: E402
import model_router as R  # noqa: E402
import native_worker as NW  # noqa: E402
import brother_login as BL  # noqa: E402
from adapters.claude import ClaudeAdapter  # noqa: E402

PREFIX = "sk-" + "ant-oat01-"   # joined so no file holds the key shape at rest; the push gate scans for it
CANARY = PREFIX + "CANARY" + "x7Qz" * 8 + "-_END"   # a synthetic login; the real Keychain is never touched
INHERITED = PREFIX + "INHERITED" + "a" * 24   # a parent's token: never the child's
MARK = "tokenroles_"


def answer(model="claude-sonnet-5"):
    return json.dumps({"result": "12", "is_error": False, "total_cost_usd": 0.01, "modelUsage": {model: {"inputTokens": 3}}})


OK_RESULT = {"returncode": 0, "stdout": answer(), "stderr": ""}


class _FakeAdmission(object):
    """The pool contract only, so a tree without plugin/ still reaches the code under test."""
    NoSlotAvailable = type("NoSlotAvailable", (Exception,), {})

    def __init__(self, root):
        self.root = root

    def state_root(self):
        return self.root

    def effective_slots(self, root, want):
        return want

    def acquire_slot(self, root, limit, holder, timeout_seconds=None):
        return object()

    def release_slot(self, slot):
        return None


def _registry():
    return {n: {"id": i, "transport": "claude", "privacy": R.PRIVATE, "quality": {"build": 9, "grade": 9},
                "kinds": {"build", "grade"}, "cost": 1.0}
            for n, i in (("k", "claude-sonnet-5"), ("o", "claude-opus-5-5"), ("h", "claude-haiku-4-5"))}


class _Fixture(object):
    """A fresh world per case: scratch under TMPDIR, the switch on, a fake reader, a fake program, no real pool."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix=MARK))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved_env = dict(os.environ)
        saved = (MC.admission, MC._admit_without_a_row, MC.CLAUDE, MC.SCRATCH, BL.READER)
        self.addCleanup(self._restore, saved_env, saved)
        for key in ("BROTHER_PROGRAM_RECORD", "BROTHER_PROOF_PHASE", "BROTHER_RUN_DIR", "BROTHER_BRIDGE_EFFORT",
                    "BROTHER_CLAUDE_EFFORT", "BROTHER_TRANSPORTS", "BROTHER_NATIVE_BUDGET_USD"):
            os.environ.pop(key, None)
        os.environ.update(BROTHER_OR_STATE_ROOT=self.tmp, BROTHER_BREAKER="off", BROTHER_LOOP_TOKEN="on",
                          BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(self.tmp, "claude-calls.jsonl"),
                          BROTHER_BRIDGE_CALLS_LEDGER=os.path.join(self.tmp, "bridge-calls.jsonl"))
        # credentials a parent holds: none may reach a child, and the descriptor name is rewritten, not inherited
        os.environ[BL.TOKEN_VAR] = INHERITED
        os.environ["GH_TOKEN"] = "gh_" + "p" * 20
        os.environ["OPENROUTER_API_KEY"] = "or_" + "k" * 20
        os.environ[BL.FD_VAR] = "99"
        MC.admission = _FakeAdmission(self.tmp)
        MC._admit_without_a_row = lambda timeout: None
        self.program = self.script("claude", "#!/bin/sh\nexit 3\n")
        MC.CLAUDE = self.program
        MC.SCRATCH = os.path.join(self.tmp, "scratch")
        self.sandbox = mock.patch.object(MC, "_have_sandbox", return_value=True)
        self.sandbox.start()
        self.addCleanup(self.sandbox.stop)
        self.reg = _registry()
        self.seen = []
        self.asked = os.path.join(self.tmp, "reader-argv.log")
        self.reader()

    @staticmethod
    def _restore(env, saved):
        MC.admission, MC._admit_without_a_row, MC.CLAUDE, MC.SCRATCH, BL.READER = saved
        os.environ.clear()
        os.environ.update(env)

    def script(self, name, text, directory=None):
        p = os.path.join(directory or self.tmp, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        os.chmod(p, 0o755)
        return p

    def reader(self, rc=0, value=CANARY):
        """A fake Keychain reader: records each ask, answers `get` with value at rc."""
        BL.READER = self.script("brother-keychain", "#!/bin/sh\necho \"$@\" >> '%s'\n[ %d -eq 0 ] && printf '%%s\\n' '%s'\nexit %d\n"
                                % (self.asked, rc, value, rc))

    def spy(self, argv, stdin, timeout, env=None):
        piped = os.read(int(env[BL.FD_VAR]), 65536) if env and BL.FD_VAR in env else None   # what the CLI would read
        self.seen.append({"argv": list(argv), "stdin": stdin, "timeout": timeout, "env": env, "piped": piped})
        return dict(OK_RESULT)

    def spy3(self, argv, stdin, timeout):   # today's runner shape: no env keyword
        self.seen.append({"argv": list(argv), "stdin": stdin, "timeout": timeout, "env": None, "piped": None})
        return dict(OK_RESULT)

    def call(self, runner=None, prompt="what is 12", name="k", **kw):
        return MC.call_one(name, prompt, "grade", R.PRIVATE, timeout=30, reg=self.reg, runner=runner or self.spy, **kw)

    def reader_asks(self):
        if not os.path.exists(self.asked):
            return 0
        with open(self.asked) as fh:
            return len([l for l in fh if l.strip()])

    def rows(self):
        path = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def leftovers(self):
        """Role scratch folders still on disk, plus the CLI tool folders of them."""
        base = MC.SCRATCH
        left = [d for d in (os.listdir(base) if os.path.isdir(base) else []) if d.startswith("role-")]
        tools = [os.path.join(base, d) for d in left]
        return left, [NW.tool_dir(t) for t in tools if os.path.isdir(NW.tool_dir(t))]

    def assertNoDescriptorLeft(self, env):
        with self.assertRaises(OSError, msg="the parent closed its end of the login pipe"):
            os.fstat(int(env[BL.FD_VAR]))

    def assertRefusedBeforeAnything(self, a, *words):
        self.assertFalse(a.ok)
        self.assertEqual(a.failure, "CONFIG", a.detail)
        self.assertIn(MC.CONFIG_WAIT, a.detail)
        for w in words:
            self.assertIn(w, a.detail)
        self.assertEqual(self.seen, [], "nothing was spawned")
        self.assertEqual(self.rows(), [], "nothing was registered in the call ledger")
        self.assertEqual(self.leftovers(), ([], []), "no scratch left behind")
        self.assertNotIn(CANARY, a.detail)


class TheRoleRunsSandboxedOnTheLoopLogin(_Fixture, unittest.TestCase):
    def test_G_SANDBOX_the_call_is_the_seats_sandbox_around_the_plain_command_line(self):
        a = self.call()
        self.assertTrue(a.ok, a.detail)
        call = self.seen[0]
        argv = call["argv"]
        self.assertEqual(argv[0], MC.SANDBOX_EXEC)
        self.assertEqual(argv[1:3], ["-p", NW.profile(True)], "the seat's token lane profile, byte for byte")
        self.assertIn('(deny process-exec (literal "/usr/bin/security"))', argv[2], "the Keychain tool is shut")
        plain, _stdin, _ = ClaudeAdapter(self.program, env={}).argv("k", "what is 12", 30, self.reg["k"])
        self.assertEqual(argv[-len(plain):], plain, "the plain command line is the tail, unchanged")
        self.assertEqual(call["stdin"], "what is 12", "the prompt goes on stdin")
        self.assertEqual(argv[argv.index("--tools") + 1], "", "a role still allows no tool")

    def test_G_SANDBOX_the_head_is_the_head_a_seat_gets(self):
        self.call()
        call = self.seen[0]
        argv = call["argv"]
        wt = next(a[3:] for a in argv if a.startswith("WT="))
        head = argv[:argv.index(self.program)]
        seat = NW.session_argv("claude-sonnet-5", "high", wt, self.program, env=call["env"], token_on=True)
        self.assertEqual(head, seat[:len(head)], "one wall for seats and roles")
        self.assertTrue(set(NW.PUSH_BLOCK) <= set(argv), "no push from a role")
        self.assertEqual(wt, os.path.realpath(wt))
        self.assertTrue(wt.startswith(os.path.join(MC.SCRATCH, "role-")), "the role's only writable place is its own scratch")

    def test_G_PIPE_the_login_travels_on_a_pipe_and_nowhere_else(self):
        a = self.call()
        self.assertTrue(a.ok, a.detail)
        call = self.seen[0]
        env = call["env"]
        self.assertEqual(call["piped"], CANARY.encode(), "the Keychain value, not the inherited one, is on the pipe")
        self.assertNotEqual(env[BL.FD_VAR], "99", "a stale descriptor name of the parent is replaced")
        self.assertNotIn(BL.TOKEN_VAR, env, "the login never sits in the child's environment")
        for name, blob in (("argv", " ".join(call["argv"])), ("stdin", call["stdin"]), ("env", json.dumps(env)),
                           ("detail", a.detail), ("answer", a.answer)):
            self.assertNotIn(CANARY, blob, name)
            self.assertNotIn(INHERITED, blob, name)
        self.assertEqual(env[NW.SCRUB_VAR], "1", "the CLI's own tool children never inherit the login")
        self.assertNoDescriptorLeft(env)
        for d, _, files in os.walk(self.tmp):
            for f in files:
                if f == "brother-keychain":   # the fake reader's own script carries the value by construction
                    continue
                with open(os.path.join(d, f), "rb") as fh:
                    self.assertNotIn(CANARY.encode(), fh.read(), f)

    def test_G_SCRUB_no_credential_of_the_parent_reaches_the_child(self):
        self.call()
        env = self.seen[0]["env"]
        self.assertFalse([k for k in env if k != BL.FD_VAR and NW.G.SECRET_ENV.search(k)], sorted(env))
        argv = self.seen[0]["argv"]
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"):
            self.assertEqual(argv[argv.index(name) - 1], "-u", name)

    def test_the_parents_environment_is_untouched(self):
        before = dict(os.environ)
        self.call()
        self.assertEqual(dict(os.environ), before)

    def test_one_ledger_pair_is_written_for_the_call(self):
        self.call()
        rows = self.rows()
        self.assertEqual([r.get("event", "start") for r in rows], ["start", "done"], rows)
        self.assertEqual(rows[0]["call"], rows[1]["call"])

    def test_the_chain_entry_point_takes_the_same_lane(self):
        a, tried = MC.call("what is 12", "grade", R.PRIVATE, chain=["k"], reg=self.reg, runner=self.spy)
        self.assertTrue(a is not None and a.ok, tried)
        self.assertEqual(self.seen[0]["argv"][0], MC.SANDBOX_EXEC)
        self.assertEqual(self.seen[0]["piped"], CANARY.encode())

    def test_every_call_asks_the_keychain_afresh_and_gets_its_own_scratch(self):
        """The actor is the same as last time: a second call must not reuse the first's pipe (it reads empty) or folder."""
        self.call()
        self.call()
        self.assertEqual(self.reader_asks(), 2)
        self.assertEqual([c["piped"] for c in self.seen], [CANARY.encode()] * 2)
        wts = [next(a for a in c["argv"] if a.startswith("WT=")) for c in self.seen]
        self.assertNotEqual(wts[0], wts[1])

    def test_two_calls_at_once_do_not_share_a_pipe_or_a_scratch(self):
        out = []

        def one():
            out.append(self.call())

        threads = [threading.Thread(target=one) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(a.ok for a in out), [a.detail for a in out])
        self.assertEqual(sorted(c["piped"] for c in self.seen), [CANARY.encode()] * 2)
        self.assertEqual(len({next(a for a in c["argv"] if a.startswith("WT=")) for c in self.seen}), 2)
        self.assertEqual(self.leftovers(), ([], []))


class TheLaneIsReleasedOnEveryEnding(_Fixture, unittest.TestCase):
    def test_G_CLOSE_after_an_answer_nothing_is_left(self):
        self.call()
        self.assertEqual(self.leftovers(), ([], []))
        self.assertNoDescriptorLeft(self.seen[0]["env"])

    def test_G_CLOSE_after_a_failed_answer_nothing_is_left(self):
        def bad(argv, stdin, timeout, env=None):
            self.seen.append({"env": env})
            return {"returncode": 1, "stdout": "", "stderr": "boom"}
        a = self.call(runner=bad)
        self.assertFalse(a.ok)
        self.assertEqual(self.leftovers(), ([], []))
        self.assertNoDescriptorLeft(self.seen[0]["env"])

    def test_G_CLOSE_after_a_timeout_nothing_is_left(self):
        def slow(argv, stdin, timeout, env=None):
            self.seen.append({"env": env})
            raise subprocess.TimeoutExpired(argv, timeout)
        a = self.call(runner=slow)
        self.assertEqual(a.status, "TIMEOUT")
        self.assertEqual(self.leftovers(), ([], []))
        self.assertNoDescriptorLeft(self.seen[0]["env"])

    def test_G_CLOSE_when_the_ledger_refuses_the_call_nothing_is_left(self):
        with mock.patch.object(MC.CL, "start", side_effect=OSError("disk full")):
            a = self.call()
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "REFUSED_BY_GATE", a.detail)
        self.assertEqual(self.leftovers(), ([], []))

    def test_G_CLOSE_is_idempotent(self):
        lane = MC._TokenLane([self.program, "-p", "--tools", ""], None)
        lane.close()
        lane.close()
        self.assertEqual(self.leftovers(), ([], []))

    def test_G_CLOSE_a_lane_that_fails_to_build_leaves_nothing(self):
        with mock.patch.object(NW, "role_argv", side_effect=ValueError("no")):
            with self.assertRaises(ValueError):
                MC._TokenLane([self.program, "-p"], None)
        self.assertEqual(self.leftovers(), ([], []))


class AnUnusableLoginRefusesBeforeAnythingRuns(_Fixture, unittest.TestCase):
    def test_G_READER_a_reader_that_is_missing_names_brother_login(self):
        BL.READER = os.path.join(self.tmp, "no-such-reader")
        self.assertRefusedBeforeAnything(self.call(), "brother_login.py")

    def test_G_READER_an_unreadable_keychain_names_brother_login(self):
        self.reader(rc=1)
        self.assertRefusedBeforeAnything(self.call(), "brother_login.py")

    def test_G_READER_a_login_that_was_never_saved_names_brother_login(self):
        self.reader(rc=BL.NOT_FOUND)
        self.assertRefusedBeforeAnything(self.call(), "brother_login.py")

    def test_G_READER_a_value_that_is_not_a_claude_token_is_refused(self):
        self.reader(rc=0, value="not-a-token")
        a = self.call()
        self.assertRefusedBeforeAnything(a, "brother_login.py")
        self.assertNotIn("not-a-token", a.detail)

    def test_G_HOLDER_an_ended_holder_names_a_restart_never_a_sign_in(self):
        """S2 round 2 (review claim 3 part c): the run's login holder has ended, so the role's refusal names the
        holder's remedy (restart the run) and never tells the owner to sign in again, which would not help. The words
        loop_roles.login_refusal lifts out of a role's attempts are in it. Red at d98550631: "sign in once"."""
        import socket
        import loop_roles
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(theirs.close)
        mine.close()
        os.environ[BL.HOLD_FD_VAR] = str(theirs.fileno())
        a = self.call()
        self.assertRefusedBeforeAnything(a, BL.HOLDER_ENDED, "Restart the run", loop_roles.ROLE_LOGIN_REFUSED)
        self.assertNotIn("sign in once", a.detail)
        self.assertEqual(loop_roles.login_refusal([a]), a.detail)
        self.assertEqual(self.reader_asks(), 0, "the Keychain is never read behind an ended holder")

    def test_G_READER_a_refusal_never_runs_the_call_on_the_interactive_login(self):
        """Running on the interactive login would be a plain call: some runner would see an argv that is not sandboxed."""
        BL.READER = os.path.join(self.tmp, "no-such-reader")
        self.call()
        self.call(runner=self.spy3)
        self.assertEqual(self.seen, [])

    def test_G_NO_SANDBOX_a_machine_without_sandbox_exec_refuses_before_the_keychain_is_asked(self):
        self.sandbox.stop()
        with mock.patch.object(MC, "_have_sandbox", return_value=False):
            a = self.call()
        self.sandbox.start()
        self.assertRefusedBeforeAnything(a, "sandbox-exec")
        self.assertEqual(self.reader_asks(), 0, "the Keychain was not asked")

    def test_G_PROGRAM_a_program_the_sandbox_cannot_run_refuses_before_the_keychain_is_asked(self):
        home = os.path.join(self.tmp, "home")
        os.environ["HOME"] = home
        outside = self.script("claude", "#!/bin/sh\nexit 3\n", directory=os.path.join(home, "somewhere"))
        MC.CLAUDE = outside
        a = self.call()
        self.assertRefusedBeforeAnything(a, "does not open")
        self.assertEqual(self.reader_asks(), 0, "the Keychain was not asked")

    def test_a_scratch_folder_that_cannot_be_made_is_not_an_unsandboxed_run(self):
        open(MC.SCRATCH, "w").close()   # a file where the folder must be
        a = self.call()
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "TRANSPORT_DOWN", a.detail)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.reader_asks(), 0)


class TheSwitchOffIsTodaysCall(_Fixture, unittest.TestCase):
    def test_G_OFF_the_command_line_is_the_plain_adapter_line_and_the_keychain_is_never_asked(self):
        os.environ["BROTHER_LOOP_TOKEN"] = "off"
        a = self.call(runner=self.spy3)
        self.assertTrue(a.ok, a.detail)
        argv, stdin, _ = ClaudeAdapter(self.program, env={}).argv("k", "what is 12", 30, self.reg["k"])
        self.assertEqual((self.seen[0]["argv"], self.seen[0]["stdin"], self.seen[0]["timeout"]), (argv, stdin, 30))
        self.assertEqual(self.reader_asks(), 0)
        self.assertFalse(os.path.exists(MC.SCRATCH), "no scratch of a role lane")

    def test_G_OFF_a_misspelt_switch_is_off(self):
        for v in ("yes", " on ", "On", "1", ""):
            self.seen = []
            os.environ["BROTHER_LOOP_TOKEN"] = v
            a = self.call(runner=self.spy3)
            self.assertTrue(a.ok, (v, a.detail))
            self.assertNotEqual(self.seen[0]["argv"][0], MC.SANDBOX_EXEC, v)
        self.assertEqual(self.reader_asks(), 0)

    def test_G_OFF_the_switch_unset_is_off(self):
        os.environ.pop("BROTHER_LOOP_TOKEN")
        self.assertTrue(self.call(runner=self.spy3).ok)
        self.assertEqual(self.reader_asks(), 0)

    def test_other_transports_never_take_the_lane(self):
        """The lane belongs to the claude transport; a bridge row is not wrapped and the Keychain is not asked."""
        reg = dict(self.reg, b={"id": "x/b", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 5},
                                "kinds": {"build"}, "cost": 1.0})
        with mock.patch.object(MC, "_TokenLane", side_effect=AssertionError("a bridge call took the lane")):
            MC.call_one("b", "hi", "build", R.PUBLIC, timeout=30, reg=reg,
                        runner=lambda a, s, t: {"returncode": 0, "stdout": "12", "stderr": ""})
        self.assertEqual(self.reader_asks(), 0)


class _PlainLane(object):
    """A lane that forgets to sandbox: what a regression in _TokenLane would look like to the guard behind it."""

    def __init__(self, argv, runner):
        self.argv, self.go = argv, runner

    def close(self):
        pass


class TheExistingRefusalStandsBehindTheLane(_Fixture, unittest.TestCase):
    def test_G_GUARD_an_unsandboxed_call_under_the_switch_is_still_refused_at_call_one(self):
        with mock.patch.object(MC, "_TokenLane", _PlainLane):
            a = MC.call_one("k", "what is 12", "grade", R.PRIVATE, timeout=30, reg=self.reg, runner=self.spy3)
        self.assertFalse(a.ok)
        self.assertEqual(a.status, "TRANSPORT_DOWN", a.detail)
        self.assertIn("only native seats", a.detail)
        self.assertEqual(self.seen, [], "nothing was sent")
        self.assertEqual([r.get("outcome") for r in self.rows() if r.get("event") == "done"], ["not_started"])

    def test_G_GUARD_an_adapter_refusal_under_the_switch_sends_nothing(self):
        a = MC.call_one("k", "   ", "grade", R.PRIVATE, timeout=30, reg=self.reg, runner=self.spy)
        self.assertFalse(a.ok)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.reader_asks(), 0)

    def test_G_GUARD_claude_run_refuses_a_plain_command_line(self):
        path = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]
        started = MC.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "t-1"}, 10)
        with self.assertRaises(MC.SpawnFailed) as cm:
            MC._claude_run(path, started, ["/usr/local/bin/claude", "-p"], "x", 10, self.spy3)
        self.assertIn("only native seats", str(cm.exception))
        self.assertEqual(self.seen, [])

    def test_G_GUARD_claude_run_refuses_an_empty_command_line(self):
        path = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]
        started = MC.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "t-2"}, 10)
        with self.assertRaises(MC.SpawnFailed):
            MC._claude_run(path, started, [], "x", 10, self.spy3)


FAKE_CLAUDE = r"""#!/bin/bash
tok=$(cat <&"$CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR")
[ "$tok" = '__CANARY__' ] && login=read-login || login=no-login
[ -n "${CLAUDE_CODE_OAUTH_TOKEN-}" ] && envtok=env-token || envtok=no-env-token
cat "$HOME/.ssh/id_probe" >/dev/null 2>&1 && ssh=ssh-readable || ssh=ssh-denied
cat "$HOME/Documents/secret.txt" >/dev/null 2>&1 && doc=doc-readable || doc=doc-denied
( echo x > "$TOKENROLES_OUTSIDE/w" ) 2>/dev/null && wr=write-outside-ok || wr=write-outside-denied
( echo x > "./inside.txt" ) 2>/dev/null && wi=write-inside-ok || wi=write-inside-denied
printf '{"result":"12","is_error":false,"total_cost_usd":0.01,"modelUsage":{"claude-sonnet-5":{"inputTokens":3}},"probe":"%s %s %s %s %s %s","cwd":"%s"}' \
  "$login" "$envtok" "$ssh" "$doc" "$wr" "$wi" "$(pwd -P)"
"""


@unittest.skipUnless(os.access(MC.SANDBOX_EXEC, os.X_OK), "NO-DATA: the native sandbox exists on macOS only")
class TheRealSandboxHoldsTheRole(_Fixture, unittest.TestCase):
    """The REAL sandbox-exec and the real subprocess path (no injected runner) around a fake claude that reads its login
    from the descriptor as the CLI does, then tries what a hostile role could: read the home folder, write outside."""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".ssh"))
        os.makedirs(os.path.join(self.home, "Documents"))
        for rel in (".ssh/id_probe", "Documents/secret.txt"):
            with open(os.path.join(self.home, rel), "w") as fh:
                fh.write("private\n")
        os.environ["HOME"] = self.home
        self.outside = "/private/tmp/%s%d" % (MARK, os.getpid())   # the profile does not open /private/tmp to a role
        os.makedirs(self.outside, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.outside, True)
        os.environ["TOKENROLES_OUTSIDE"] = self.outside
        MC.CLAUDE = self.script("claude", FAKE_CLAUDE.replace("__CANARY__", CANARY))

    def test_G_REAL_the_role_reads_its_login_cannot_read_home_and_cannot_write_outside(self):
        a = MC.call_one("k", "what is 12", "grade", R.PRIVATE, timeout=60, reg=self.reg)
        self.assertTrue(a.ok, a.detail + " | " + json.dumps(a.raw))
        doc = json.loads(a.raw["stdout"])
        self.assertEqual(doc["probe"].split()[:5], ["read-login", "no-env-token", "ssh-denied", "doc-denied",
                                                    "write-outside-denied"], doc)
        self.assertFalse(os.path.exists(os.path.join(self.outside, "w")), "a role wrote outside its scratch")
        self.assertTrue(doc["cwd"].startswith(os.path.join(MC.SCRATCH, "role-")), doc["cwd"])
        self.assertNotIn(CANARY, a.raw["stdout"] + a.raw["stderr"])
        self.assertEqual(self.leftovers(), ([], []), "the scratch is removed afterwards")

    def test_G_REAL_a_role_may_write_its_own_scratch(self):
        a = MC.call_one("k", "what is 12", "grade", R.PRIVATE, timeout=60, reg=self.reg)
        self.assertTrue(a.ok, a.detail)
        self.assertEqual(json.loads(a.raw["stdout"])["probe"].split()[5], "write-inside-ok")


class TheIntakeNoLongerRefusesTheRoles(_Fixture, unittest.TestCase):
    """loop_intake.prepare through its own probe seam (the same MR.require_proof the real reach probe runs) with the
    token on: planner, repair advisor and finisher read OK, not NO START."""

    def setUp(self):
        super().setUp()
        try:
            import loop_intake as LI
            import model_reachability as MR
        except ImportError as exc:   # a tree without the intake cannot run this case
            self.skipTest("NO-DATA: %s" % exc)
        self.LI, self.MR = LI, MR
        os.environ.update(BROTHER_PROOF_CACHE=os.path.join(self.tmp, "proofs.json"),
                          BROTHER_PROGRAM_RECORD=os.path.join(self.tmp, "programs.json"))
        self.new = self.script("claude-new", "#!/bin/sh\necho '2.1.284 (Claude Code)'\n")

    def runner(self, argv, stdin, timeout, env=None):
        model = argv[argv.index("--model") + 1]
        self.seen.append({"argv": list(argv), "model": model, "piped": os.read(int(env[BL.FD_VAR]), 65536) if env else None})
        return {"returncode": 0, "stdout": answer(model), "stderr": ""}

    def prepare(self):
        base = {"does": "x", "when": "inside", "kind": "grade", "content": "private", "must_be_chosen": False}
        roles = {"planner": dict(base, setting="BROTHER_BUILD_PLAN_MODEL", default="k"),
                 "repair_advisor": dict(base, setting="BROTHER_REPAIR_ADVISOR_MODEL", default="k"),
                 "finisher": dict(base, setting="BROTHER_FINISHER_MODEL", when="after_run", kind="build", default=None,
                                  must_be_chosen=True)}
        noon = __import__("datetime").datetime(2026, 1, 1, 12, 0)
        ok = lambda: ("OK", "fixture")
        probes = {k: ok for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")}
        probes.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: noon,
                      reach=lambda plan: self.MR.require_proof(plan, self.reg, runner=self.runner,
                                                               programs={"claude": self.new}))
        env = {"BROTHER_WORKER_MIX": "k:1", "BROTHER_BUILD_PLAN_MODEL": "k", "BROTHER_REPAIR_ADVISOR_MODEL": "k"}
        stop = "%d:%02d" % (18, 0)   # built, not written: the deadline the intake reads is a clock time
        return self.LI.prepare({"finisher": "o"}, stop, 10.0, None, roles, self.reg, probes, env=env)[0]

    def test_planner_repair_advisor_and_finisher_are_proved_on_the_loop_login(self):
        rec = self.prepare()
        text = "\n".join(rec["lines"])
        self.assertNotIn("NO START", text)
        self.assertNotIn("only native seats", text)
        for role in ("planner", "repair_advisor", "finisher"):
            self.assertTrue([l for l in rec["lines"] if l.startswith("OK") and "reach role=%s " % role in l], role + "\n" + text)
        self.assertTrue(self.seen and all(c["piped"] == CANARY.encode() for c in self.seen), "every proof read the login on its pipe")
        self.assertTrue(all(c["argv"][0] == MC.SANDBOX_EXEC for c in self.seen))

    def test_with_the_login_unreadable_the_roles_are_no_start_naming_brother_login(self):
        self.reader(rc=1)
        rec = self.prepare()
        no_start = [l for l in rec["lines"] if "NO START" in l]
        self.assertTrue(no_start, "\n".join(rec["lines"]))
        self.assertTrue(all("brother_login.py" in l for l in no_start), no_start)
        self.assertEqual(rec["verdict"], "NOT READY")
        self.assertEqual(self.seen, [], "nothing was spawned")


class G1ARunnerThatCannotTakeEnvIsRefusedFirst(_Fixture, unittest.TestCase):
    """Review 2026-10-14, G1: a three argument runner under the switch used to raise TypeError out of call_one AFTER the
    Keychain was read and the ledger START written, a phantom uncosted call."""

    def test_G1_a_three_argument_runner_is_refused_before_the_keychain_the_spawn_and_the_ledger(self):
        a = self.call(runner=self.spy3)
        self.assertRefusedBeforeAnything(a, "env")
        self.assertEqual(self.reader_asks(), 0, "the Keychain was not asked")

    def test_G1_a_runner_with_kwargs_is_accepted(self):
        seen = []

        def runner(argv, stdin, timeout, **kw):
            seen.append(kw)
            return dict(OK_RESULT)
        self.assertTrue(self.call(runner=runner).ok)
        self.assertIn(BL.FD_VAR, seen[0]["env"])

    def test_G1_a_keyword_only_env_is_accepted(self):
        seen = []

        def runner(argv, stdin, timeout, *, env):
            seen.append(env)
            return dict(OK_RESULT)
        self.assertTrue(self.call(runner=runner).ok)
        self.assertIn(BL.FD_VAR, seen[0])

    def test_G1_a_runner_whose_signature_cannot_be_read_is_refused(self):
        with mock.patch("inspect.signature", side_effect=ValueError("no signature")):
            a = self.call(runner=self.spy)
        self.assertRefusedBeforeAnything(a, "env")
        self.assertEqual(self.reader_asks(), 0)

    def test_G1_with_the_switch_off_a_three_argument_runner_still_runs(self):
        os.environ["BROTHER_LOOP_TOKEN"] = "off"
        self.assertTrue(self.call(runner=self.spy3).ok)


ROUTING = {"ANTHROPIC_BASE_URL": "https://other.example", "ANTHROPIC_AUTH_URL": "https://other.example", "NODE_OPTIONS": "--require /x.js",
           "HTTPS_PROXY": "http://p.example", "https_proxy": "http://p.example", "HTTP_PROXY": "http://p.example",
           "http_proxy": "http://p.example", "ALL_PROXY": "http://p.example", "all_proxy": "http://p.example",
           "NO_PROXY": "x", "NODE_EXTRA_CA_CERTS": "/tmp/ca.pem", "DYLD_INSERT_LIBRARIES": "/tmp/x.dylib",
           "DYLD_LIBRARY_PATH": "/tmp", "SSL_CERT_FILE": "/tmp/ca.pem", "CLAUDE_CONFIG_DIR": "/tmp/cfg",
           "PYTHONPATH": "/tmp/inject", "LD_PRELOAD": "/tmp/x.so"}


class G2TheTokenChildEnvironmentIsAnAllowlist(_Fixture, unittest.TestCase):
    """Review 2026-10-14, G2: a routed parent environment must not send the owner's login to another host. login_child is
    the one place every token child (seat and role) routes through."""

    def setUp(self):
        super().setUp()
        os.environ.update(ROUTING)
        os.environ.update(LC_ALL="en_US.UTF-8", BROTHER_PROBE_FLAG="1", BROTHER_PROBE_TOKEN="t" * 12)

    def assertClean(self, env):
        for name in ROUTING:
            self.assertNotIn(name, env, name)
        self.assertEqual([k for k in env if k.upper().startswith(("DYLD_", "LD_")) or k.upper().endswith("_PROXY")], [])
        self.assertEqual(env["LC_ALL"], "en_US.UTF-8")
        self.assertEqual(env["BROTHER_PROBE_FLAG"], "1")
        self.assertNotIn("BROTHER_PROBE_TOKEN", env, "a credential shaped name is still dropped")
        self.assertEqual(env["PATH"], os.environ["PATH"])
        self.assertIn(BL.FD_VAR, env)

    def test_G2_a_role_child_has_no_routing_name(self):
        a = self.call()
        self.assertTrue(a.ok, a.detail)
        self.assertClean(self.seen[0]["env"])
        self.assertEqual(self.seen[0]["piped"], CANARY.encode(), "the token still arrives on the pipe")

    def test_G2_a_seat_child_has_no_routing_name(self):
        held = []
        wt = os.path.join(self.tmp, "seatwt")
        os.makedirs(wt)
        argv, effort, env, fd, why = NW._seat_session({"id": "claude-sonnet-5"}, "high", wt, "", held, program=self.program)
        self.addCleanup(lambda: [os.close(f) for f in held if f is not None and self._open(f)])
        self.assertEqual(why, "")
        self.assertClean(env)
        self.assertEqual(os.read(fd, 65536), CANARY.encode(), "the token still arrives on the pipe")

    @staticmethod
    def _open(fd):
        try:
            os.fstat(fd)
            return True
        except OSError:
            return False

    def test_G2_a_name_is_allowed_only_by_the_list(self):
        for name in ("ANTHROPIC_BASE_URL", "NODE_OPTIONS", "https_proxy", "DYLD_X", "PYTHONPATH", "XDG_CONFIG_HOME"):
            self.assertFalse(NW.child_env_name(name), name)
        for name in ("HOME", "PATH", "LC_CTYPE", "BROTHER_ANYTHING"):
            self.assertTrue(NW.child_env_name(name), name)

    def test_G2_off_lane_the_environment_is_the_parents_unchanged(self):
        """A non token call inherits: no env is built, so nothing of the allowlist applies."""
        os.environ["BROTHER_LOOP_TOKEN"] = "off"
        wt = os.path.join(self.tmp, "seatwt")
        os.makedirs(wt)
        argv, effort, env, fd, why = NW._seat_session({"id": "claude-sonnet-5"}, "high", wt, "", [], program=self.program)
        self.assertEqual((env, fd, why), (None, None, ""))
        for n in ("DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH"):
            os.environ.pop(n)   # a bogus loader path would crash the probe child itself
        out = MC._run_in([sys.executable, "-c", "import os; print(os.environ.get('ANTHROPIC_BASE_URL', 'absent'))"], "", 20, self.tmp)
        self.assertEqual(out["stdout"].strip(), ROUTING["ANTHROPIC_BASE_URL"], "an inheriting child is untouched")
        self.seen = []
        self.assertTrue(self.call(runner=self.spy3).ok)


class G3TheSandboxProgramIsOneAbsolutePath(_Fixture, unittest.TestCase):
    """Review 2026-10-14, G3: sandbox-exec resolved through PATH, and a guard that read only the basename."""

    def test_G3_the_constant_is_the_system_path(self):
        self.assertEqual(MC.SANDBOX_EXEC, "/usr/bin/sandbox-exec")

    def test_G3_a_role_argv_leads_with_the_absolute_path_whatever_the_path_holds(self):
        fakebin = os.path.join(self.tmp, "fakebin")
        self.script("sandbox-exec", "#!/bin/sh\ntouch '%s/ran'\nexit 0\n" % self.tmp, directory=fakebin)
        os.environ["PATH"] = fakebin + os.pathsep + os.environ["PATH"]
        self.assertTrue(self.call().ok)
        self.assertEqual(self.seen[0]["argv"][0], "/usr/bin/sandbox-exec")

    def test_G3_a_seat_argv_leads_with_the_absolute_path(self):
        argv = NW.session_argv("claude-sonnet-5", "high", self.tmp, self.program, env={}, token_on=True)
        self.assertEqual(argv[0], "/usr/bin/sandbox-exec")
        self.assertEqual(NW.session_argv("m", "low", self.tmp, self.program)[0], "/usr/bin/sandbox-exec")

    def test_G3_claude_run_refuses_any_other_spelling(self):
        path = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]
        for i, first in enumerate(("sandbox-exec", "./sandbox-exec", "/tmp/x/sandbox-exec", "/usr/local/bin/sandbox-exec",
                                   "/usr/bin/../bin/sandbox-exec", "/usr/bin/sandbox-exec ", "")):
            started = MC.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "g3-%d" % i}, 10)
            with self.assertRaises(MC.SpawnFailed, msg=repr(first)):
                MC._claude_run(path, started, [first, "-p", "x", "claude"], "x", 10, self.spy3)
        self.assertEqual(self.seen, [])

    def test_G3_claude_run_accepts_the_absolute_path(self):
        path = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]
        started = MC.CL.start(path, {"model": "m", "effort": "low", "prompt_chars": 1, "call": "g3-ok"}, 10)
        MC._claude_run(path, started, [MC.SANDBOX_EXEC, "-p", "x", "claude"], "x", 10, self.spy3)
        self.assertEqual(len(self.seen), 1)

    def test_G3_the_availability_check_reads_the_absolute_path_not_PATH(self):
        self.sandbox.stop()
        try:
            fakebin = os.path.join(self.tmp, "fakebin")
            self.script("sandbox-exec", "#!/bin/sh\nexit 0\n", directory=fakebin)
            os.environ["PATH"] = fakebin
            with mock.patch.object(MC, "SANDBOX_EXEC", os.path.join(self.tmp, "absent")):
                self.assertFalse(MC._have_sandbox(), "a sandbox-exec on PATH does not count")
            with mock.patch.object(MC, "SANDBOX_EXEC", os.path.join(fakebin, "sandbox-exec")):
                self.assertTrue(MC._have_sandbox())
        finally:
            self.sandbox.start()


@unittest.skipUnless(os.access(MC.SANDBOX_EXEC, os.X_OK), "NO-DATA: the native sandbox exists on macOS only")
class G3ARealRunIgnoresAFakeOnPath(TheRealSandboxHoldsTheRole):
    def test_G3_REAL_a_fake_sandbox_exec_ahead_on_PATH_is_never_run(self):
        fakebin = os.path.join(self.tmp, "fakebin")
        self.script("sandbox-exec", "#!/bin/sh\ntouch '%s/ran'\nexec \"$@\"\n" % self.tmp, directory=fakebin)
        os.environ["PATH"] = fakebin + os.pathsep + os.environ["PATH"]
        a = MC.call_one("k", "what is 12", "grade", R.PRIVATE, timeout=60, reg=self.reg)
        self.assertTrue(a.ok, a.detail)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ran")), "the PATH program ran")


if __name__ == "__main__":
    unittest.main()
