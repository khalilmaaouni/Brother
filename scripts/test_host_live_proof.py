#!/usr/bin/env python3
"""Tests for scripts/host_live_proof.py and scripts/host_hook_trace.py (HP1.a, spec docs/plan/specs/HP1.md).

A stand in host, written to a temp folder, starts hook commands the way a host does: `/bin/sh -c <command>` in the hook
file's folder with the payload on stdin and the PATH the driver builds (the witness shim first), and reports the exit
code and bytes it saw. Every case uses the real writer, the shipped hook files (copied into a temp candidate checkout)
or a fixture hook where a case needs an exit code the shipped ones never give, and its own HOME and trace under a temp
folder: nothing is written under ~/.claude.

Run: python3 scripts/test_host_live_proof.py TestHostLiveProof -v
"""
import collections
import contextlib
import hashlib
import inspect
import io
import json
import base64
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import host_hook_trace as witness  # noqa: E402
import host_live_claude as claude  # noqa: E402
import host_live_proof as proof  # noqa: E402

AG_REL = os.path.join("bundle", ".antigravity-plugin")
AG_SCRIPT = os.path.join("scripts", "brother_antigravity_hook.py")
AG_PRE_TOOL = "python3 scripts/brother_antigravity_hook.py pre_tool"
WRITE_PAYLOAD = json.dumps({"toolCall": {"name": "write_to_file", "args": {"TargetFile": "/tmp/hp1-test.txt"}},
                            "stepIdx": 1, "conversationId": "c1"}).encode()
HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]

STAND_IN_HOST = '''import json, subprocess, sys
command, cwd, payload_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(payload_path, "rb") as fh:
    payload = fh.read()
done = subprocess.run(["/bin/sh", "-c", command], cwd=cwd, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
sys.stdout.write(json.dumps({"rc": done.returncode, "out": done.stdout.hex(), "err": done.stderr.hex()}))
'''

FIXTURE_HOOK = '''import json, sys, time
mode = sys.argv[1]
data = sys.stdin.buffer.read()
if mode == "sleep":
    time.sleep(30)
try:
    code = int(json.loads(data.decode("utf-8")).get("exit", 0))
except (ValueError, AttributeError):
    code = 0
sys.stdout.buffer.write(b"\\xff\\x00out:" + data)
sys.stderr.buffer.write(b"fixture stderr\\n")
sys.exit(code)
'''

# A stand in Claude Code: one `--print` session per prompt, which fires the shipped hook of the probe's event through
# `python3` on PATH (the shim) the way the host's hook launcher does, then prints the host's own stream-json account of
# it. The real host runs the hook_guard.py wrapper; the stand in runs the inner script the wrapper execs, which is the
# one call the witness records either way. FAKE_CLAUDE_EXTRA=1 reports one hook fire more than it made.
# FAKE_CLAUDE_SHAPE=2.1.286 writes the hook line the way the real 2.1.286 stream does (no command field) and
# FAKE_CLAUDE_LOGGED_OUT=1 fires only a prompt-less SessionStart and ends on the real not signed in error result.
FAKE_CLAUDE = '''import json, os, subprocess, sys
argv = sys.argv[1:]
if "--bare" in argv:
    sys.exit(9)
plugin_dir, prompt = argv[argv.index("--plugin-dir") + 1], argv[-1]
with open(os.path.join(plugin_dir, "hooks", "hooks.json")) as fh:
    hooks = json.load(fh)["hooks"]
if os.environ.get("FAKE_CLAUDE_LOGGED_OUT") == "1":
    event = "SessionStart"
    payload = {"hook_event_name": event, "source": "startup"}
elif prompt.startswith("Write the single word"):
    event = "PreToolUse"
    payload = {"hook_event_name": event, "tool_name": "Write", "tool_input": {"file_path": prompt.rsplit(" ", 1)[1]}}
else:
    event = "SessionStart"
    payload = {"hook_event_name": event, "source": "startup"}   # the real 2.1.286 SessionStart carries no prompt
sid = "s-%d" % os.getpid()
payload["session_id"] = sid
# SessionStart has no matcher, so like the real host every hook the plugin registers for it fires; PreToolUse fires one
blocks = hooks[event] if event == "SessionStart" else hooks[event][:1]
commands = [h["command"] for b in blocks for h in b["hooks"]] if event == "SessionStart" else [blocks[0]["hooks"][0]["command"]]
lines = [{"type": "system", "subtype": "fake_path", "path": os.environ.get("PATH", "")}]
for n, command in enumerate(commands):
    command = command.replace("${CLAUDE_PLUGIN_ROOT}", plugin_dir)
    inner = "python3 " + command.split(" python3 ", 1)[1]
    done = subprocess.run(["/bin/sh", "-c", inner], input=json.dumps(payload).encode(), cwd=os.path.join(plugin_dir, "hooks"),
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    lines.append({"type": "system", "subtype": "hook_response", "hook_event": event, "command": command,
                  "hook_id": "h-%d-%d" % (os.getpid(), n), "session_id": sid,
                  "stdout": done.stdout.decode("utf-8", "replace"), "exit_code": done.returncode})
lines.append({"type": "result", "usage": {"input_tokens": 1}})
if os.environ.get("FAKE_CLAUDE_EXTRA") == "1":
    lines.insert(2, dict(lines[1], hook_id="h-extra-%d" % os.getpid()))
if os.environ.get("FAKE_CLAUDE_SHAPE") == "2.1.286":
    for line in lines[1:-1]:
        del line["command"]
if os.environ.get("FAKE_CLAUDE_LOGGED_OUT") == "1":
    lines[-1] = {"type": "result", "is_error": True, "result": "Not logged in (stand in)"}
for line in lines:
    sys.stdout.write(json.dumps(line) + "\\n")
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with open(path, "rb") as fh:
        return sha(fh.read())


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


def copy_source(dest):
    """A candidate checkout holding the shipped hook files byte for byte and the Antigravity hook script."""
    for rel in proof.SHIPPED_HOOKS + (os.path.join(AG_REL, AG_SCRIPT),):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), os.path.join(dest, rel))
    for product in ("brothermode", "brothersbe"):
        rel = os.path.join("products", product, "hooks", "hooks.json")
        if os.path.isfile(os.path.join(ROOT, rel)):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy2(os.path.join(ROOT, rel), os.path.join(dest, rel))
    return dest


@contextlib.contextmanager
def no_process():
    """Every way this process could start another one raises, and is counted."""
    started = []

    def refuse(*args, **kwargs):
        started.append(args)
        raise AssertionError("a process was started")
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("subprocess.Popen", side_effect=refuse))
        for name in ("fork", "execv", "execve", "posix_spawn", "system"):
            if name in dir(os):
                stack.enter_context(mock.patch.object(os, name, side_effect=refuse))
        yield started


class TestHostLiveProof(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hp1a-")
        cls.src = copy_source(os.path.join(cls.tmp, "source"))
        cls.ag_dir = os.path.join(cls.src, AG_REL)
        cls.host_dir = os.path.join(cls.tmp, "standin-host")
        cls.host_script = write(os.path.join(cls.host_dir, "standin_host.py"), STAND_IN_HOST)
        cls.shim_dir = os.path.join(cls.tmp, "shim")
        cls.shim = proof.write_shim(cls.shim_dir, sys.executable)
        cls.fixture = os.path.join(cls.tmp, "fixture-plugin")
        write(os.path.join(cls.fixture, "scripts", "fixture_hook.py"), FIXTURE_HOOK)
        write(os.path.join(cls.fixture, "hooks.json"), json.dumps({"fixture-plugin": {
            "PreToolUse": [{"matcher": "x", "hooks": [
                {"type": "command", "command": "python3 scripts/fixture_hook.py exit", "timeout": 60}]}],
            "Stop": [{"type": "command", "command": "python3 scripts/fixture_hook.py sleep", "timeout": 1}]}}))
        cls.guarded = os.path.join(cls.tmp, "guarded-plugin")
        shutil.copy2(os.path.join(HERE, "hook_guard.py"), write(os.path.join(cls.guarded, "runtime", "hooks", "hook_guard.py"), ""))
        write(os.path.join(cls.guarded, "tools", "fixture_hook.py"), FIXTURE_HOOK)
        cls.guard_command = ('python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py" fixture PreToolUse "--matcher=Bash" '
                             'python3 "${CLAUDE_PLUGIN_ROOT}/tools/fixture_hook.py" exit')
        write(os.path.join(cls.guarded, "hooks", "hooks.json"), json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": cls.guard_command, "timeout": 60}]}]}}))
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        cls.base = dict((k, v) for k, v in os.environ.items() if not k.startswith("HP1_"))
        cls.base.update({"HOME": cls.home, "CLAUDE_PLUGIN_ROOT": cls.guarded})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="case-", dir=self.tmp)
        self.trace = os.path.join(self.work, "trace", "trace.jsonl")
        os.makedirs(os.path.dirname(self.trace))

    # helpers -------------------------------------------------------------------------------------------------------

    def env(self, trace=None, src=None):
        calls, roots = proof._allowlist(src or self.src)
        for path, root in ((os.path.join(self.fixture, "hooks.json"), self.fixture),
                           (os.path.join(self.guarded, "hooks", "hooks.json"), self.guarded)):
            calls += proof._hook_calls(path, root)
            roots.append(os.path.realpath(root))
        return proof._witness_env(self.base, calls, roots, trace or self.trace, "antigravity", "run-test",
                                  sys.executable, self.shim_dir, [self.host_dir])

    def payload_file(self, payload):
        fd, path = tempfile.mkstemp(dir=self.work)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        return path

    def host_argv(self, command, cwd, payload):
        return [sys.executable, "-B", self.host_script, command, cwd, self.payload_file(payload)]

    def fire(self, command, cwd, payload, env=None):
        """(exit code, stdout, stderr) the stand in host saw for one hook command started through the shim."""
        run = subprocess.run(self.host_argv(command, cwd, payload), env=env or self.env(), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
        seen = json.loads(run.stdout.decode("utf-8"))
        return seen["rc"], bytes.fromhex(seen["out"]), bytes.fromhex(seen["err"])

    def direct(self, command, cwd, payload):
        """The same command with no witness on PATH: what the host would see without HP1."""
        run = subprocess.run(["/bin/sh", "-c", command], cwd=cwd, input=payload, env=self.base, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=120)
        return run.returncode, run.stdout, run.stderr

    def rows(self, trace=None):
        path = trace or self.trace
        if not os.path.exists(path):
            return []
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertTrue(data == b"" or data.endswith(b"\n"), "a row line is cut")
        return [json.loads(line.decode("ascii")) for line in data.splitlines()]

    def raw(self, row, name):
        with open(os.path.join(os.path.dirname(self.trace), row[name + "_path"]), "rb") as fh:
            return fh.read()

    # REQ-HP1-WITNESS and REQ-HP1-PASSTHROUGH ------------------------------------------------------------------------

    def test_hook_call_is_recorded_with_raw_bytes(self):
        want = self.direct(AG_PRE_TOOL, self.ag_dir, WRITE_PAYLOAD)
        got = self.fire(AG_PRE_TOOL, self.ag_dir, WRITE_PAYLOAD)
        self.assertEqual(got, want)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(self.raw(row, "raw_in"), WRITE_PAYLOAD)
        self.assertEqual(self.raw(row, "raw_out"), got[1])
        self.assertEqual(row["raw_in_sha256"], sha(WRITE_PAYLOAD))
        self.assertEqual(row["raw_in_path"], sha(WRITE_PAYLOAD) + ".raw")
        self.assertEqual(row["raw_out_sha256"], sha(got[1]))
        self.assertEqual((row["verdict"], row["verdict_source"]), proof.parse_verdict(got[1], got[0]))
        self.assertEqual((row["verdict"], row["verdict_source"]), ("allow", "json_allow"))
        self.assertEqual(row["exit_code"], got[0])
        self.assertEqual(row["event"], "PreToolUse")
        self.assertEqual(row["hook_command"], [AG_SCRIPT, "pre_tool"])
        self.assertEqual(row["script"], os.path.realpath(os.path.join(self.ag_dir, AG_SCRIPT)))
        self.assertEqual(row["plugin_root"], os.path.realpath(self.ag_dir))
        self.assertEqual((row["host"], row["run_id"], row["truncated"]), ("antigravity", "run-test", False))
        self.assertEqual(row["witness_sha256"], file_sha(os.path.join(HERE, "host_hook_trace.py")))

    def test_exit_code_and_bytes_pass_through(self):
        cwd = self.fixture
        payload = json.dumps({"exit": 3, "hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}}).encode()
        rc, out, err = self.fire("python3 scripts/fixture_hook.py exit", cwd, payload)
        self.assertEqual(rc, 3)
        self.assertEqual(out, b"\xff\x00out:" + payload)
        self.assertEqual(err, b"fixture stderr\n")
        payload2 = json.dumps({"exit": 2, "hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}}).encode()
        rc2, out2, err2 = self.fire("python3 scripts/fixture_hook.py exit", cwd, payload2)
        self.assertEqual((rc2, out2, err2), (2, b"\xff\x00out:" + payload2, b"fixture stderr\n"))
        rows = self.rows()
        self.assertEqual([r["exit_code"] for r in rows], [3, 2])
        self.assertEqual([(r["verdict"], r["verdict_source"]) for r in rows], [("no_data", "no_rule"), ("deny", "exit_2")])
        self.assertEqual(self.raw(rows[0], "raw_out"), out)
        self.assertEqual(rows[0]["stderr_sha256"], sha(err))

    def test_non_hook_calls_are_not_recorded(self):
        payload = json.dumps({"hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}, "pad": "é" * 50}).encode()
        code = "import sys; d = sys.stdin.buffer.read(); sys.stdout.buffer.write(d[::-1]); sys.exit(5)"
        rc, out, err = self.fire("python3 -c '%s'" % code, self.work, payload)
        self.assertEqual((rc, out), (5, payload[::-1]))   # stdin reached the child byte for byte
        # the shipped script with a mode no shipped command carries is not a hook fire either
        want = self.direct("python3 scripts/brother_antigravity_hook.py pre_toolx", self.ag_dir, WRITE_PAYLOAD)
        self.assertEqual(self.fire("python3 scripts/brother_antigravity_hook.py pre_toolx", self.ag_dir, WRITE_PAYLOAD), want)
        self.assertFalse(os.path.exists(self.trace))

    def test_same_basename_elsewhere_is_not_a_hook_fire(self):
        elsewhere = os.path.join(self.work, "elsewhere")
        os.makedirs(os.path.join(elsewhere, "scripts"))
        shutil.copy2(os.path.join(self.ag_dir, AG_SCRIPT), os.path.join(elsewhere, AG_SCRIPT))
        want = self.direct(AG_PRE_TOOL, elsewhere, WRITE_PAYLOAD)
        self.assertEqual(self.fire(AG_PRE_TOOL, elsewhere, WRITE_PAYLOAD), want)
        self.assertEqual(self.rows(), [])

    def test_guard_wrapper_passes_through_and_its_inner_hook_is_recorded(self):
        payload = json.dumps({"exit": 3, "hook_event_name": "PreToolUse", "tool_name": "Bash"}).encode()
        rc, out, _err = self.fire(self.guard_command, self.work, payload)
        self.assertEqual((rc, out), (3, b"\xff\x00out:" + payload))
        rows = self.rows()
        self.assertEqual(len(rows), 1)   # the inner hook, never the hook_guard.py call that wrapped it
        fired = os.path.join(self.guarded, "tools", "fixture_hook.py")   # the argv as fired (host_hook_trace records list(argv))
        inner = os.path.realpath(fired)                                  # the script's identity, resolved
        # 2026-10-03: this expected the resolved path in hook_command too, which holds only where the temp folder has no
        # symlink in it; under /var/folders (a link to /private/var) the push gate read it red
        self.assertEqual((rows[0]["script"], rows[0]["hook_command"], rows[0]["event"]), (inner, [fired, "exit"], "PreToolUse"))
        self.assertEqual(proof.hook_scripts([os.path.join(self.guarded, "hooks", "hooks.json")], self.guarded), [inner])

    def test_stdin_that_is_not_a_json_object_is_recorded_as_no_data(self):
        for payload in (b"not json", b"", b"[1, 2]"):
            want = self.direct(AG_PRE_TOOL, self.ag_dir, payload)
            self.assertEqual(self.fire(AG_PRE_TOOL, self.ag_dir, payload), want)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual({(r["verdict"], r["verdict_source"]) for r in rows}, {("no_data", "stdin_not_object")})
        self.assertEqual([self.raw(r, "raw_in") for r in rows], [b"not json", b"", b"[1, 2]"])

    def test_a_hook_that_times_out_is_recorded_as_minus_nine(self):
        payload = json.dumps({"hook_event_name": "Stop"}).encode()
        rc, _out, _err = self.fire("python3 scripts/fixture_hook.py sleep", self.fixture, payload)
        self.assertIn(rc, (-9, 137))   # killed by SIGKILL, as the host would see a killed hook
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["exit_code"], rows[0]["verdict"], rows[0]["verdict_source"]), (-9, "no_data", "timeout"))

    def test_witness_without_arguments_prints_usage_and_records_nothing(self):
        run = subprocess.run([self.shim], env=self.env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertIn(b"usage", run.stderr)
        self.assertFalse(os.path.exists(self.trace))

    # REQ-HP1-ANCESTOR -----------------------------------------------------------------------------------------------

    def test_rows_carry_the_host_ancestor(self):
        self.fire(AG_PRE_TOOL, self.ag_dir, WRITE_PAYLOAD)
        row = self.rows()[0]
        chain = row["parent_chain"]
        self.assertTrue(chain, row.get("parent_chain_error"))
        for link in chain:
            self.assertEqual(set(link), {"pid", "ppid", "comm", "exe", "argv"})
        self.assertIn("host_hook_trace.py", chain[0]["argv"])   # the witness itself, then its ancestors
        self.assertTrue(any(self.host_dir in link["argv"] for link in chain[1:]), json.dumps(chain)[:600])
        self.assertTrue(all(os.path.isabs(link["exe"]) for link in chain if link["exe"]))
        self.assertEqual(row["host_roots"], [self.host_dir])
        self.assertLessEqual(len(proof.parent_chain(os.getpid(), 2)), 2)

    # concurrency and fail open -------------------------------------------------------------------------------------

    def test_two_concurrent_hook_calls_append_two_whole_lines(self):
        payloads = [WRITE_PAYLOAD.replace(b"c1", b"c-one"), WRITE_PAYLOAD.replace(b"c1", b"c-two")]
        env = self.env()
        procs = [subprocess.Popen(self.host_argv(AG_PRE_TOOL, self.ag_dir, p), env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for p in payloads]
        for p in procs:
            p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0)
        with open(self.trace, "rb") as fh:
            data = fh.read()
        lines = data.split(b"\n")
        self.assertEqual(lines[-1], b"")
        self.assertEqual(len(lines), 3)
        self.assertEqual(sorted(json.loads(l)["raw_in_sha256"] for l in lines[:2]), sorted(sha(p) for p in payloads))

    def test_trace_in_a_missing_directory_fails_open(self):
        missing = os.path.join(self.work, "missing", "deeper", "trace.jsonl")
        want = self.direct(AG_PRE_TOOL, self.ag_dir, WRITE_PAYLOAD)
        rc, out, err = self.fire(AG_PRE_TOOL, self.ag_dir, WRITE_PAYLOAD, env=self.env(trace=missing))
        self.assertEqual((rc, out), want[:2])   # the hook still returns its verdict
        self.assertTrue(err.startswith(want[2]))
        self.assertIn(b"host_hook_trace: WARNING", err)
        self.assertFalse(os.path.exists(os.path.dirname(os.path.dirname(missing))))

    # the verdict table and the probe label ---------------------------------------------------------------------------

    def test_verdict_rules_table(self):
        cases = [
            ((b"", 2), ("deny", "exit_2")),
            ((b'{"decision": "allow"}', 2), ("deny", "exit_2")),
            ((b'{"decision": "deny", "reason": "r"}', 0), ("deny", "json_deny")),
            ((b'{"decision": "block"}', 0), ("deny", "json_deny")),
            ((b'{"decision": "allow"}', 0), ("allow", "json_allow")),
            ((b'{"decision": "ask"}', 0), ("ask", "json_ask")),
            ((b'{"hookSpecificOutput": {"permissionDecision": "deny"}}', 0), ("deny", "json_deny")),
            ((b'{"hookSpecificOutput": {"permissionDecision": "allow"}}', 0), ("allow", "json_allow")),
            ((b'{"decision": "allow", "hookSpecificOutput": {"permissionDecision": "deny"}}', 0), ("no_data", "no_rule")),
            ((b"", 0), ("allow", "silent_exit_0")),
            ((b"\n", 0), ("no_data", "no_rule")),
            ((b"not json", 0), ("no_data", "no_rule")),
            ((b'{"decision": "allow"}', 1), ("no_data", "no_rule")),
            ((b'{"decision": "continue"}', 0), ("no_data", "no_rule")),
            ((b'{"decision": ["deny"]}', 0), ("no_data", "no_rule")),
            ((b"{}", 0), ("no_data", "no_rule")),
            ((b"", -9), ("no_data", "no_rule")),
        ]
        for args, want in cases:
            self.assertEqual(proof.parse_verdict(*args), want, args)

    def test_antigravity_inject_steps_is_an_allow_from_json_inject(self):
        for out in (b'{"injectSteps": []}', b'{"injectSteps": [], "terminationBehavior": ""}',
                    b'{"injectSteps": [], "terminationBehavior": "force_continue"}'):
            self.assertEqual(proof.parse_verdict(out, 0), ("allow", "json_inject"), out)
        self.assertEqual(proof.parse_verdict(b'{"injectSteps": [], "terminationBehavior": "terminate"}', 0),
                         ("deny", "json_terminate"))
        self.assertEqual(proof.parse_verdict(b'{"injectSteps": [], "terminationBehavior": "later"}', 0), ("no_data", "no_rule"))
        self.assertEqual(proof.parse_verdict(b'{"injectSteps": {}}', 0), ("no_data", "no_rule"))
        payload = json.dumps({"invocationNum": 1, "conversationId": "c1", "workspacePaths": ["/w"]}).encode()
        self.fire("python3 scripts/brother_antigravity_hook.py pre_invocation", self.ag_dir, payload)
        row = self.rows()[0]
        self.assertEqual((row["event"], row["verdict"], row["verdict_source"], row["probe"]),
                         ("PreInvocation", "allow", "json_inject", "door"))

    def test_probe_label_comes_from_the_payload_not_the_order(self):
        target = "/scratch/outside/" + proof.GUARDED_WRITE_NAME
        cases = [
            ({"hook_event_name": "PreToolUse", "tool_input": {"file_path": target, "content": "hp1"}}, None, "guarded_write"),
            ({"toolCall": {"name": "write_to_file", "args": {"TargetFile": target}}}, "PreToolUse", "guarded_write"),
            ({"hook_event_name": "PreToolUse", "tool_input": {"command": "echo hp1 > %s" % target}}, None, "guarded_write"),
            ({"hook_event_name": "PreToolUse", "tool_input": {"file_path": "/scratch/other.txt"}}, None, "other"),
            ({"hook_event_name": "UserPromptSubmit", "prompt": proof.DOOR_SENTENCE}, None, "door"),
            ({"hook_event_name": "UserPromptSubmit", "prompt": proof.VERB_SENTENCE + "\n"}, None, "verb"),
            ({"hook_event_name": "UserPromptSubmit", "prompt": "something else"}, None, "other"),
            ({"invocationNum": 2, "conversationId": "c"}, "PreInvocation", "verb"),
            ({"invocationNum": 1, "conversationId": "c"}, "PreInvocation", "door"),
            ({"invocationNum": True, "conversationId": "c"}, "PreInvocation", "other"),
            ({"invocationNum": 3, "conversationId": "c"}, "PreInvocation", "other"),
            ({"invocationNum": 1, "conversationId": "c"}, "PostInvocation", "post_invocation"),
            ({"invocationNum": 1, "conversationId": "c"}, None, "other"),
            ({"hook_event_name": "Stop", "prompt": proof.DOOR_SENTENCE}, "UserPromptSubmit", "other"),   # the payload's event wins
        ]
        for order in (cases, list(reversed(cases)), cases[1::2] + cases[::2]):
            for payload, event, want in order:
                self.assertEqual(proof.probe_of(json.dumps(payload).encode(), event=event), want, (payload, event))
        for raw in (b"", b"not json", b"[]", b"{}"):
            self.assertEqual(proof.probe_of(raw), "other")

    def test_real_codex_payloads_label_only_the_guarded_write(self):
        # Codex run hp1-codex-20261004T111227Z-14730, one row per distinct shape, home path and account name replaced: SessionStart
        # carries no prompt and no UserPromptSubmit fired, so door and verb are never labelled (owner ruling hp1-codex-door)
        with open(os.path.join(HERE, "fixtures", "hp1-real-2026-10-04", "codex-trace-rows.jsonl")) as fh:
            rows = [json.loads(line) for line in fh]
        got = [(r["event"], proof.probe_of(json.dumps(r["raw_in"]).encode(), event=r["event"])) for r in rows]
        self.assertEqual([g for g in got if g[1] != "other"], [("PreToolUse", "guarded_write")])
        self.assertEqual(got, [(r["event"], r["probe"]) for r in rows])   # the witness at run time labelled the same
        self.assertNotIn("prompt", next(r["raw_in"] for r in rows if r["event"] == "SessionStart"))

    def test_a_claude_run_that_was_not_signed_in_says_so(self):
        folder = tempfile.mkdtemp(prefix="hp1-err-")
        self.addCleanup(shutil.rmtree, folder, True)
        trace = os.path.join(folder, "trace.jsonl")
        self.assertEqual(proof._stream_errors("claude", trace), "")   # no stream: nothing named, never a guess
        shutil.copy(os.path.join(HERE, "fixtures", "hp1-real-2026-10-04", "claude-stream.jsonl"),
                    claude.claude_stream_path(trace))
        said = proof._stream_errors("claude", trace)
        self.assertTrue(said.startswith(", the host's stream says: Not logged in"), said)
        self.assertEqual(said.count("Not logged in"), 1)   # three equal results named once
        self.assertEqual(proof._stream_errors("codex", trace), "")

    def test_run_codex_with_only_the_guarded_write_row_is_recorded_with_the_ruling_named(self):
        # main's own verdict over the rows run_host returns (the host itself is stubbed: Codex has no stand in here)
        home = tempfile.mkdtemp(prefix="hp1-main-")
        self.addCleanup(shutil.rmtree, home, True)
        for rows, code, start in (([{"probe": "guarded_write"}, {"probe": "other"}], 0, "RECORDED: codex"),
                                  ([{"probe": "other"}], 3, "NO-DATA: codex")):
            out = io.StringIO()
            with mock.patch.object(proof, "run_host", return_value=rows), contextlib.redirect_stdout(out):
                got = proof.main(["--run", "codex", "--home", home])
            self.assertEqual((got, out.getvalue()[:len(start)]), (code, start), out.getvalue())
            if code == 0:
                self.assertIn("door and verb NO-DATA by owner ruling 2026-10-04 hp1-codex-door", out.getvalue())
            else:
                self.assertIn("but no guarded_write row;", out.getvalue())

    def test_missing_probes_leaves_out_what_the_owner_ruled(self):
        guarded = [{"probe": "guarded_write"}, {"probe": "other"}]
        self.assertEqual(proof.missing_probes("codex", guarded), [])
        self.assertEqual(proof.missing_probes("codex", [{"probe": "other"}]), ["guarded_write"])
        self.assertEqual(proof.missing_probes("claude", guarded), [])   # owner ruling 2026-10-05 hp1-claude-door
        self.assertEqual(proof.missing_probes("claude", [{"probe": "other"}]), ["guarded_write"])
        self.assertEqual(proof.missing_probes("antigravity", guarded), ["door", "post_invocation", "verb"])
        self.assertEqual(proof.missing_probes("antigravity", [None, "x", {"probe": "door"}]),
                         ["guarded_write", "post_invocation", "verb"])
        with self.assertRaises(proof.HP1Refused):
            proof.missing_probes("cursor", guarded)

    def test_the_row_hashes_the_script_that_ran(self):
        src = copy_source(os.path.join(self.work, "edited-source"))
        script = os.path.join(src, AG_REL, AG_SCRIPT)
        with open(script, "a", encoding="utf-8") as fh:
            fh.write("\n# an edit in the working tree\n")
        self.fire(AG_PRE_TOOL, os.path.join(src, AG_REL), WRITE_PAYLOAD, env=self.env(src=src))
        row = self.rows()[0]
        self.assertEqual(row["script"], os.path.realpath(script))
        self.assertEqual(row["script_sha256"], file_sha(script))
        self.assertNotEqual(row["script_sha256"], file_sha(os.path.join(ROOT, AG_REL, AG_SCRIPT)))

    # REQ-HP1-NO-EDIT and REQ-HP1-THROWAWAY --------------------------------------------------------------------------

    def test_the_workspace_is_a_git_repository_and_a_failed_init_refuses(self):
        # 2026-10-04: Codex refuses a directory that is not a repository, so every probe of the first real run exited 1
        scratch = os.path.join(self.home, ".claude", "brother-scratch")
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            cc = proof.prepare("claude", os.path.join(scratch, "hp1-%s-git" % os.path.basename(self.work)), self.src)
            self.assertTrue(os.path.isdir(os.path.join(cc["workspace"], ".git")), cc["workspace"])
            failed = subprocess.CompletedProcess(["git"], 128, b"fatal: nope", b"")
            with mock.patch.object(proof.subprocess, "run", return_value=failed):
                with self.assertRaises(proof.HP1Refused) as cm:
                    proof.prepare("claude", os.path.join(scratch, "hp1-%s-nogit" % os.path.basename(self.work)), self.src)
        self.assertIn("git init", str(cm.exception))

    def test_a_probe_keeps_its_stderr_and_never_reads_stdin(self):
        # 2026-10-04: the refusal that explained the empty run was on stderr, which was discarded
        seen = {}
        def fake(argv, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(argv, 1, b"", b"Not inside a trusted directory")
        with mock.patch.object(proof.subprocess, "run", side_effect=fake):
            code, out, err = proof._run_envelope(["codex", "exec"], {"env": {}, "cwd": self.work}, 5)
        self.assertEqual((code, out, err), (1, b"", b"Not inside a trusted directory"))
        self.assertIs(seen.get("stdin"), subprocess.DEVNULL)
        self.assertIs(seen.get("stderr"), subprocess.PIPE)

    def test_stored_stderr_is_a_scanned_tail_of_a_failure_only(self):
        self.assertEqual(proof._stderr_field(b"noise", 0), {})
        if not os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(proof.__file__)), "loop", "secret_scan.py")):
            # a tree that ships no secret table (the public export): the tail is withheld, never stored unscanned
            self.assertIn("could not load", proof._stderr_field(b"Not inside a trusted directory", 1)["stderr_withheld"])
            return
        ok = proof._stderr_field(b"x" * 5000 + b"Not inside a trusted directory", 1)
        self.assertTrue(base64.b64decode(ok["stderr_tail_b64"]).endswith(b"trusted directory"))
        self.assertLessEqual(len(base64.b64decode(ok["stderr_tail_b64"])), proof.STDERR_TAIL_BYTES)
        mail = proof._stderr_field(b"signed in as someone.name@example.org: request failed", 1)
        self.assertNotIn(b"@", base64.b64decode(mail["stderr_tail_b64"]))
        secret = proof._stderr_field(b"token ghp_" + b"a" * 36, 1)
        self.assertNotIn("stderr_tail_b64", secret)
        self.assertIn("secret shaped", secret["stderr_withheld"])

    def test_the_workspace_init_never_inherits_the_callers_git_world(self):
        seen = {}
        real = proof.subprocess.run
        def spy(argv, **kw):
            if argv[:2] == ["git", "init"]:
                seen.update(argv=argv, env=kw.get("env"))
            return real(argv, **kw)
        scratch = os.path.join(self.home, ".claude", "brother-scratch")
        with mock.patch.dict(os.environ, {"HOME": self.home, "GIT_DIR": "/nonexistent-elsewhere"}):
            with mock.patch.object(proof.subprocess, "run", side_effect=spy):
                cc = proof.prepare("claude", os.path.join(scratch, "hp1-%s-env" % os.path.basename(self.work)), self.src)
        self.assertIn("--template=", seen["argv"])
        self.assertNotIn("GIT_DIR", seen["env"])
        self.assertEqual(seen["env"].get("GIT_CONFIG_NOSYSTEM"), "1")
        self.assertTrue(os.path.isdir(os.path.join(cc["workspace"], ".git")))

    def test_shipped_hooks_untouched(self):
        repo_before = dict((rel, file_sha(os.path.join(ROOT, rel))) for rel in proof.SHIPPED_HOOKS)
        src_before = dict((rel, file_sha(os.path.join(self.src, rel))) for rel in proof.SHIPPED_HOOKS)
        scratch = os.path.join(self.home, ".claude", "brother-scratch")
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            ag = proof.prepare("antigravity", os.path.join(scratch, "hp1-%s-ag" % os.path.basename(self.work)), self.src)
            cc = proof.prepare("claude", os.path.join(scratch, "hp1-%s-cc" % os.path.basename(self.work)), self.src)
        for out in (ag, cc):
            self.assertEqual(out["shipped_hooks_before"], out["shipped_hooks_after"])
            self.assertTrue(out["home"].startswith(os.path.realpath(scratch) + os.sep))
            self.assertTrue(os.access(out["shim_path"], os.X_OK))
        self.assertTrue(os.path.islink(ag["plugin_root"]))   # linked, never copied or rewritten
        self.assertEqual(os.path.realpath(ag["plugin_root"]), os.path.realpath(self.ag_dir))
        self.fire(AG_PRE_TOOL, ag["plugin_root"], WRITE_PAYLOAD)   # the host runs the hook from the installed plugin
        self.assertEqual(self.rows()[0]["plugin_root"], os.path.realpath(self.ag_dir))
        self.assertEqual(dict((rel, file_sha(os.path.join(self.src, rel))) for rel in proof.SHIPPED_HOOKS), src_before)
        self.assertEqual(dict((rel, file_sha(os.path.join(ROOT, rel))) for rel in proof.SHIPPED_HOOKS), repo_before)

    def test_real_home_is_refused(self):
        real = pwd.getpwuid(os.getuid()).pw_dir
        scratch = os.path.join(self.home, ".claude", "brother-scratch")
        os.makedirs(scratch, exist_ok=True)
        link = os.path.join(scratch, "points-home-%s" % os.path.basename(self.work))
        os.symlink(self.home, link)
        listing = sorted(os.listdir(self.home))
        with mock.patch.dict(os.environ, {"HOME": self.home}), no_process() as started:
            for home in (real, self.home, "~", self.home + "/.", os.path.join(real, ".codex"), link):
                with self.assertRaises(proof.HP1Refused) as ctx:
                    proof.prepare("antigravity", home, self.src)
                self.assertIn("real home", str(ctx.exception), home)
                with self.assertRaises(proof.HP1Refused):
                    proof.run_host("codex", home, self.src, self.trace)
                with self.assertRaises(proof.HP1Refused):
                    proof.owner_commands("antigravity", home)
            with self.assertRaises(proof.HP1Refused):   # outside the scratch root is refused too
                proof.prepare("antigravity", os.path.join(self.work, "elsewhere-home"), self.src)
        self.assertEqual(started, [])
        self.assertEqual(sorted(os.listdir(self.home)), listing)

    # REQ-HP1-SESSION ------------------------------------------------------------------------------------------------

    def test_run_host_without_start_spawns_nothing(self):
        home = os.path.join(self.home, ".claude", "brother-scratch", "hp1-plan-%s" % os.path.basename(self.work))
        with mock.patch.dict(os.environ, {"HOME": self.home}), no_process() as started:
            for host in proof.HOSTS:
                plans = proof.run_host(host, home, self.src, self.trace)
                self.assertEqual([p["probe"] for p in plans], list(proof.PROBES))
                shim_dir = os.path.join(os.path.realpath(home), "shim")
                for plan in plans:
                    self.assertEqual(plan["path"].split(os.pathsep)[0], shim_dir)   # the witness first on PATH
                    self.assertEqual(plan["env"]["PATH"], plan["path"])
                    self.assertEqual(plan["env"]["HOME"], os.path.realpath(home))
                    self.assertNotIn(proof.LIVE_ENV, plan["env"])
                    self.assertIn(os.path.realpath(os.path.join(self.ag_dir, AG_SCRIPT)),
                                  plan["env"][proof.SCRIPTS_ENV].split(os.pathsep))
                if host == "codex":
                    self.assertEqual(plans[0]["env"]["CODEX_HOME"], os.path.join(os.path.realpath(home), ".codex"))
                    if plans[0]["argv"] is not None:
                        self.assertEqual(plans[0]["argv"][1:6], ["exec", "--json", "--ephemeral", "-C", plans[0]["cwd"]])
                        self.assertEqual(plans[0]["argv"][-1], proof.DOOR_SENTENCE)
        self.assertEqual(started, [])
        self.assertFalse(os.path.lexists(home))   # a plan writes nothing either

    def test_run_without_hp1_live_is_refused(self):
        home = os.path.join(self.home, ".claude", "brother-scratch", "hp1-live-%s" % os.path.basename(self.work))
        env = dict((k, v) for k, v in os.environ.items() if k != proof.LIVE_ENV)
        env["HOME"] = self.home
        for value in (None, "0", "yes"):
            if value is not None:
                env[proof.LIVE_ENV] = value
            with mock.patch.dict(os.environ, env, clear=True), no_process() as started:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = proof.main(["--run", "codex", "--home", home, "--source", self.src])
                self.assertEqual(code, 2)
                self.assertIn("HP1_LIVE=1", err.getvalue())
                with self.assertRaises(proof.HP1Refused):
                    proof.run_host("antigravity", home, self.src, self.trace, True, 5)
            self.assertEqual(started, [])
            self.assertFalse(os.path.lexists(home))

    def test_owner_commands_name_the_binary_and_the_probes(self):
        fake = write(os.path.join(self.work, "bin", "antigravity-ide"), "#!/bin/sh\nexit 0\n", 0o755)
        home = os.path.join(self.home, ".claude", "brother-scratch", "hp1-owner-%s" % os.path.basename(self.work))
        with mock.patch.dict(os.environ, {"HOME": self.home, "ANTIGRAVITY_BIN": fake}), no_process() as started:
            lines = proof.owner_commands("antigravity", home)
        self.assertEqual(started, [])
        self.assertIn("export ANTIGRAVITY_BIN=%s" % os.path.realpath(fake), lines)
        probes = [l for l in lines if l.split(":")[0] in proof.PROBES]
        self.assertEqual([l.split(":")[0] for l in probes], list(proof.PROBES))
        self.assertEqual(probes[0], "door: " + proof.DOOR_SENTENCE)
        self.assertIn(proof.GUARDED_WRITE_NAME, probes[2])
        self.assertTrue(any(l.startswith("env ") and "PATH=" in l and proof.SCRIPTS_ENV in l for l in lines))
        self.assertFalse(os.path.lexists(home))

    # Claude Code through HP1.e (host_live_claude.py) ------------------------------------------------------------------

    def claude_env(self, **extra):
        env = dict((k, v) for k, v in self.base.items() if k not in claude.BIN_ENVS and k != claude.ROOTS_ENV)
        env.update(extra)
        return env

    def fake_claude(self):
        """(host root, binary): a stand in Claude Code under its own root, started through the real interpreter."""
        root = os.path.join(self.work, "claude-root")
        script = write(os.path.join(root, "lib", "fake_claude.py"), FAKE_CLAUDE)
        wrapper = write(os.path.join(root, "bin", "claude"),
                        "#!/bin/sh\nexec %s -B %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(script)), 0o755)
        return root, wrapper

    def scratch_home(self, tag):
        return os.path.join(self.home, ".claude", "brother-scratch", "hp1-%s-%s" % (tag, os.path.basename(self.work)))

    def test_claude_binary_is_named_by_resolve_claude(self):
        root, fake = self.fake_claude()
        home = self.scratch_home("claude-bin")
        env = self.claude_env(HP1_CLAUDE_BIN=fake, HP1_HOST_ALLOWED_ROOTS=root,
                              PATH=os.path.dirname(fake) + os.pathsep + self.base.get("PATH", ""))
        with mock.patch.dict(os.environ, env, clear=True), no_process() as started:
            plans = proof.run_host("claude", home, self.src, self.trace)
            lines = proof.owner_commands("claude", home)
        real = os.path.realpath(fake)
        self.assertEqual([p["host_bin"] for p in plans], [real] * len(proof.PROBES))
        self.assertEqual([p["argv"][0] for p in plans], [real] * len(proof.PROBES))
        self.assertEqual(plans[0]["host_roots"], [os.path.realpath(root)])
        self.assertIn("export %s=%s" % (claude.BIN_ENVS[0], shlex.quote(real)), lines)
        self.assertTrue(any(l.startswith("HP1_LIVE=1 python3 scripts/host_live_proof.py --run claude --home ") for l in lines))
        self.assertEqual(started, [])
        self.assertFalse(os.path.lexists(home))

    def test_claude_binary_is_never_found_on_path_alone(self):
        root, fake = self.fake_claude()
        home = self.scratch_home("claude-path")
        env = self.claude_env(PATH=os.path.dirname(fake) + os.pathsep + self.base.get("PATH", ""))   # HOME holds no install
        with mock.patch.dict(os.environ, env, clear=True), no_process() as started:
            plans = proof.run_host("claude", home, self.src, self.trace)
            lines = proof.owner_commands("claude", home)
        self.assertEqual([(p["host_bin"], p["argv"]) for p in plans], [(None, None)] * len(proof.PROBES))
        self.assertIn("resolve_claude", plans[0]["no_data"])
        self.assertTrue(any(l.startswith("NO-DATA: ") for l in lines))
        self.assertFalse(any("export %s=" % claude.BIN_ENVS[0] in l for l in lines))
        self.assertEqual(started, [])

    def test_claude_plan_never_carries_bare_and_names_the_candidate_bundle(self):
        root, fake = self.fake_claude()
        env = self.claude_env(HP1_CLAUDE_BIN=fake, HP1_HOST_ALLOWED_ROOTS=root)
        with mock.patch.dict(os.environ, env, clear=True), no_process():
            plans = proof.run_host("claude", self.scratch_home("claude-argv"), self.src, self.trace)
        bundle = os.path.join(os.path.realpath(self.src), "bundle")
        for plan in plans:
            self.assertNotIn("--bare", plan["argv"])
            self.assertIn("--include-hook-events", plan["argv"])
            self.assertEqual(plan["argv"][plan["argv"].index("--plugin-dir") + 1], bundle)
            self.assertEqual(plan["argv"][-1], plan["prompt"])

    def run_claude(self, tag, **extra):
        """(exit code, stdout, home) of the owner command `--run claude` against the stand in host."""
        root, fake = self.fake_claude()
        home = self.scratch_home(tag)
        env = self.claude_env(HP1_CLAUDE_BIN=fake, HP1_HOST_ALLOWED_ROOTS=root)
        env[proof.LIVE_ENV] = "1"
        env.update(extra)
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            code = proof.main(["--run", "claude", "--home", home, "--source", self.src, "--wait", "30"])
        return code, out.getvalue(), os.path.realpath(home)

    def test_run_claude_stores_the_stream_beside_the_trace_and_the_crosscheck_agrees(self):
        code, out, home = self.run_claude("claude-run")
        self.assertEqual((code, out[:16]), (0, "RECORDED: claude"), out)
        self.assertIn("agree", out)
        trace = os.path.join(home, "trace", "trace.jsonl")
        rows = self.rows(trace)
        # one row per probe the host can label: the guarded write; the door and the verb sessions each fire the four
        # shipped SessionStart hooks with no prompt (owner ruling 2026-10-05 hp1-claude-door), labelled other
        self.assertEqual(collections.Counter((r["event"], r["probe"]) for r in rows),
                         {("SessionStart", "other"): 8, ("PreToolUse", "guarded_write"): 1})
        self.assertIn("door and verb NO-DATA by owner ruling 2026-10-05 hp1-claude-door", out)
        self.assertEqual({r["host"] for r in rows}, {"claude"})
        with open(claude.claude_stream_path(trace), "rb") as fh:
            stream = fh.read()
        docs = [json.loads(line) for line in stream.splitlines()]
        self.assertEqual(sum(1 for d in docs if d.get("subtype") == "hook_response"), len(rows))
        self.assertEqual({d["path"].split(os.pathsep)[0] for d in docs if d.get("subtype") == "fake_path"},
                         {os.path.join(home, "shim")})   # the witness was first on the host's PATH
        bundle = os.path.join(os.path.realpath(self.src), "bundle")
        for probe, prompt in (("door", proof.DOOR_SENTENCE), ("verb", proof.VERB_SENTENCE)):
            with open(os.path.join(home, "trace", "%s-%s.json" % (os.path.basename(home), probe))) as fh:
                envelope = json.load(fh)
            self.assertEqual(envelope["argv"], claude.claude_argv(os.path.realpath(envelope["argv"][0]), bundle, prompt))
            self.assertEqual(envelope["exit_code"], 0)
        with open(os.path.join(home, "trace", "%s-guarded_write.json" % os.path.basename(home))) as fh:
            guarded = json.load(fh)
        self.assertEqual((guarded["effect"]["exists_before"], guarded["effect"]["path"]),
                         (False, os.path.join(home, "outside", proof.GUARDED_WRITE_NAME)))

    def test_run_claude_reads_the_real_2_1_286_hook_line_shape(self):
        code, out, _home = self.run_claude("claude-real-shape", FAKE_CLAUDE_SHAPE="2.1.286")
        self.assertEqual((code, out[:16]), (0, "RECORDED: claude"), out)
        self.assertIn("agree", out)

    def test_run_claude_that_was_not_signed_in_names_the_reason(self):
        code, out, _home = self.run_claude("claude-logged-out", FAKE_CLAUDE_LOGGED_OUT="1")
        self.assertEqual(code, 3, out)
        self.assertIn("but no guarded_write row, the host's stream says: Not logged in (stand in)", out)

    def test_run_claude_whose_stream_disagrees_with_the_witness_is_not_recorded_as_done(self):
        code, out, home = self.run_claude("claude-extra", FAKE_CLAUDE_EXTRA="1")
        self.assertEqual(code, 3, out)
        self.assertTrue(out.startswith("NO-DATA: claude run "), out)
        self.assertIn("do not agree", out)
        self.assertEqual(len(self.rows(os.path.join(home, "trace", "trace.jsonl"))), 9)   # 4 SessionStart hooks for the door and the verb, 1 PreToolUse

    # the parts --------------------------------------------------------------------------------------------------------

    def test_write_row_writes_the_bytes_before_the_row(self):
        row = {"host": "antigravity", "event": "PreToolUse"}
        with mock.patch.object(proof.os, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                proof.write_row(self.trace, row, b"in bytes", b"out bytes")
        folder = os.path.dirname(self.trace)
        self.assertTrue(os.path.isfile(os.path.join(folder, sha(b"in bytes") + ".raw")))
        self.assertTrue(os.path.isfile(os.path.join(folder, sha(b"out bytes") + ".raw")))
        self.assertEqual(self.rows(), [])   # raw files with no row, never a row with no bytes
        big = b"x" * (proof.RAW_CAP + 5)
        proof.write_row(self.trace, row, big, b"")
        got = self.rows()[0]
        self.assertEqual((got["raw_in_len"], got["truncated"]), (proof.RAW_CAP + 5, True))
        self.assertEqual(len(self.raw(got, "raw_in")), proof.RAW_CAP)
        self.assertEqual(row, {"host": "antigravity", "event": "PreToolUse"})   # the caller's row is not changed
        with self.assertRaises(proof.HP1Refused):
            proof.write_row(os.path.join(self.work, "no-such-folder", "t.jsonl"), row, b"", b"")

    def test_write_shim_creates_its_folder_and_names_the_interpreter(self):
        shim_dir = os.path.join(self.work, "not", "yet", "there")
        path = proof.write_shim(shim_dir, sys.executable)
        self.assertEqual(path, os.path.join(shim_dir, "python3"))
        with open(path, "rb") as fh:
            body = fh.read()
        lines = body.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertIn(sys.executable, lines[1])
        self.assertIn(proof.WITNESS, lines[1])
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(file_sha(proof.write_shim(shim_dir, sys.executable)), sha(body))   # the same bytes every time
        for bad in (("relative/dir", sys.executable), (shim_dir, "python3"), (shim_dir, os.path.join(self.work, "nope"))):
            with self.assertRaises(proof.HP1Refused):
                proof.write_shim(*bad)

    def test_hook_scripts_are_absolute_and_exact(self):
        bundle = os.path.join(ROOT, "bundle")
        scripts = proof.hook_scripts([os.path.join(bundle, "hooks", "hooks.json")], bundle)
        self.assertTrue(scripts)
        self.assertTrue(all(os.path.isabs(s) for s in scripts))
        self.assertFalse([s for s in scripts if os.path.basename(s) == "hook_guard.py"])   # the wrapper is never a hook
        self.assertIn(os.path.realpath(os.path.join(bundle, "runtime", "hooks", "brothermode", "tools", "bm_clock_guard.py")),
                      scripts)
        ag = os.path.join(ROOT, AG_REL)
        self.assertEqual(proof.hook_scripts([os.path.join(ag, "hooks.json")], ag), [os.path.realpath(os.path.join(ag, AG_SCRIPT))])
        broken = write(os.path.join(self.work, "broken.json"), "{not json")
        for files in ([os.path.join(self.work, "missing.json")], [broken], "hooks.json", []):
            with self.assertRaises(proof.HP1Refused):
                proof.hook_scripts(files, ag)

    def test_hostile_inputs_are_refused(self):
        refusals = (ValueError, LookupError, SystemExit)
        cwd = os.getcwd()
        os.chdir(self.work)
        try:
            with mock.patch.object(sys, "argv", ["fuzz"]), no_process() as started, \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                for mod in (proof, witness):
                    for name, fn in inspect.getmembers(mod, inspect.isfunction):
                        if name.startswith("_") or fn.__module__ != mod.__name__:
                            continue
                        params = [p for p in inspect.signature(fn).parameters.values()
                                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                        if not params:
                            continue   # as the landing fuzz: a call with no arguments is not a hostile call
                        for value in HOSTILE:
                            try:
                                fn(*[value] * len(params))
                            except refusals:
                                pass
                            except Exception as exc:  # the classification the landing fuzz applies
                                self.fail("%s.%s(%r) crashed: %s: %s" % (mod.__name__, name, value, type(exc).__name__, exc))
        finally:
            os.chdir(cwd)
        self.assertEqual(started, [])
        self.assertEqual(os.listdir(self.work), ["trace"])   # nothing written by a refused call
        self.assertEqual(proof.probe_of(b"x"), "other")


if __name__ == "__main__":
    unittest.main()
