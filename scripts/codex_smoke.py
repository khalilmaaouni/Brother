#!/usr/bin/env python3
"""codex_smoke: the clean-install Codex smoke test, run in an isolated home.

Ship gate 7 of the Codex workstream (board row C7). It drives, in order:

  1. codex plugin marketplace add <this repository>
  2. codex plugin add brother@brother --json
  3. codex plugin list --available --json    (pluginId brother@brother)
  4. python3 scripts/codex_hooks_install.py --codex-home <isolated> --trust
  5. a REAL codex exec turn against a throwaway git repository holding the
     README's toy (mathlib.py, test_mathlib.py), whose tool call runs
     scripts/brother_run.py, which produces a receipt.

WHAT IS REAL AND WHAT IS STUBBED, stated here rather than left to a reader.
The Codex binary, its plugin install, its hooks machinery, Brother's hooks,
brother_run.py, the claim store and the receipt are all REAL. Two model calls
are stubbed, because an isolated home has no credentials:

  - Codex's own model, by a local HTTP provider this file serves on 127.0.0.1
    (the same technique the C3 lane used). Step 5 first attempts the run with
    NO stub, so the credential refusal is captured verbatim rather than
    assumed, and only then re-runs it through the stub.
  - Brother's own model worker, through the documented DOOR_MODEL_CMD and
    MODEL_WORKER_CMD seam (scripts/model_worker.py), the same seam
    scripts/product_acceptance.py uses.

So this proves the PLUMBING end to end. It does not prove a real model's
behaviour: that half is the founder's, and docs/codex/SMOKE-RUNBOOK.md is the
runbook he executes in his own signed-in Codex to close it.

ISOLATION. Every Codex invocation gets CODEX_HOME and HOME pointed inside the
work directory, so the founder's ~/.codex is never written. The isolation is
not asserted, it is measured: a witness over the founder's ~/.codex (its
config.toml, its AGENTS.md, whether a hooks.json exists, and the listing of
its plugins tree) is hashed before and after and both hashes are printed. The
volatile parts of that directory (its sqlite databases and caches, which the
ChatGPT desktop app rewrites on its own) are deliberately excluded: a hash
that drifts by itself is evidence of nothing.

Verdicts: PASS, FAIL, or NO-DATA (exit 2) when the Codex binary is absent.
NO-DATA is never a pass.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

#: The app-bundled Codex CLI. The PATH `codex` on this machine is an older
#: npm build (0.146.0), so the binary is named rather than resolved.
DEFAULT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex"
DEFAULT_WORK = os.path.expanduser("~/.claude/evidence/codex-smoke")
FOUNDER_CODEX_HOME = os.path.expanduser("~/.codex")

TOY_MATHLIB = "def add(a, b):\n    return a + b\n"
TOY_TEST = ("from mathlib import add\n\n\ndef test_add():\n"
            "    assert add(1, 2) == 3\n")

#: Brother's decomposer stub: one unit over mathlib.py whose done_check is a
#: command a stranger can re-run, which is what makes the receipt mean
#: something.
DECOMPOSER_STUB = '''import json, sys
sys.stdin.read()
print(json.dumps([
    {"id": "U1", "objective": "make add() refuse non-numeric input",
     "done_check": "python3 -c \\"import mathlib; mathlib.add(1,2)\\" && "
                   "! python3 -c \\"import mathlib; mathlib.add('a','b')\\" "
                   "2>/dev/null",
     "writes": ["mathlib.py"], "deps": []}]))
'''

#: Brother's model-worker stub: writes the guard the unit declared.
MODEL_STUB = '''import re, sys
prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
    if path.endswith("mathlib.py"):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("def add(a, b):\\n"
                     "    for value in (a, b):\\n"
                     "        if isinstance(value, bool) or not isinstance("
                     "value, (int, float)):\\n"
                     "            raise TypeError("
                     "'add() needs numbers, got %r' % (value,))\\n"
                     "    return a + b\\n")
print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
'''


def sh(args, env=None, cwd=None, timeout=600):
    """Run a command and return it, never raising on a nonzero exit: every
    step here reports its own exit code as evidence."""
    try:
        return subprocess.run(args, env=env, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 99, "", "codex_smoke: %s" % exc)


#: Where each step's WHOLE output is kept. A tail is what a reader wants; a
#: tail is never what a debugger needs, and trimming at capture time is how
#: this estate has lost an afternoon before.
LOG_DIR = [""]


def report(step, proc, tail=6):
    """Print a step's command, its exit code and the decisive tail, and keep
    the whole output on disk. This is the text the runbook quotes, so it is
    written once, here."""
    print("\n$ %s" % " ".join(proc.args if isinstance(proc.args, list)
                              else [str(proc.args)]))
    body = (proc.stdout or "") + (proc.stderr or "")
    if LOG_DIR[0]:
        name = "".join(c if c.isalnum() else "-" for c in step) + ".log"
        try:
            with open(os.path.join(LOG_DIR[0], name), "w",
                      encoding="utf-8") as fh:
                fh.write(body)
        except OSError as exc:
            print("  (could not keep this step's full output: %s)" % exc)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    for line in lines[-tail:]:
        print("  %s" % line)
    print("  exit %d   [%s]" % (proc.returncode, step))
    return proc.returncode


def founder_witness():
    """A stable hash over the parts of the founder's ~/.codex that a plugin
    or hook install would change. Returns (hexdigest, description) or
    (None, why) when the directory is not there."""
    if not os.path.isdir(FOUNDER_CODEX_HOME):
        return None, "no %s on this machine" % FOUNDER_CODEX_HOME
    digest = hashlib.sha256()
    parts = []
    for name in ("config.toml", "AGENTS.md"):
        path = os.path.join(FOUNDER_CODEX_HOME, name)
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            blob = ("ABSENT: %s" % exc).encode("utf-8")
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(blob).hexdigest().encode("utf-8"))
        parts.append(name)
    for name in ("hooks.json", "hooks"):
        present = os.path.exists(os.path.join(FOUNDER_CODEX_HOME, name))
        digest.update(("%s=%s" % (name, present)).encode("utf-8"))
        parts.append("%s present=%s" % (name, present))
    listing = []
    plugins = os.path.join(FOUNDER_CODEX_HOME, "plugins")
    for root, dirs, files in os.walk(plugins):
        dirs.sort()
        rel = os.path.relpath(root, FOUNDER_CODEX_HOME)
        listing.append(rel)
        listing.extend(os.path.join(rel, f) for f in sorted(files))
    for entry in sorted(listing):
        digest.update(entry.encode("utf-8"))
    parts.append("plugins tree: %d entries" % len(listing))
    return digest.hexdigest(), ", ".join(parts)


def build_toy(path):
    """The README's toy, in its own git repository."""
    os.makedirs(path, exist_ok=True)
    for name, body in (("mathlib.py", TOY_MATHLIB),
                       ("test_mathlib.py", TOY_TEST)):
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    # The identity is written into the repository's OWN config, not passed
    # with -c on the one commit. Every Codex invocation here runs under an
    # isolated HOME, so there is no global gitconfig: a lane worktree's own
    # commit then fails, the lane lands empty, and the unit's check fails on
    # a base nothing was applied to. Measured: that is exactly how the first
    # run of this script reported NEEDS-REPAIR-ON-NEW-BASE.
    for args in (["git", "init", "-q", "."],
                 ["git", "config", "user.name", "Khalil Maaouni"],
                 ["git", "config", "user.email", "khalil@example.com"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "toy"]):
        proc = sh(args, cwd=path, timeout=60)
        if proc.returncode != 0:
            return "could not build the toy repository: %s%s" % (
                proc.stdout, proc.stderr)
    return ""


def write_stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


class _Handler(BaseHTTPRequestHandler):
    """A local provider that answers Codex with exactly one tool call: the
    Brother engine command this smoke test wants run, then a final message."""

    # NOT `command`: BaseHTTPRequestHandler sets self.command to the HTTP
    # method, and an instance attribute shadows a class one. That collision
    # made a first draft ask Codex to run the shell command "POST".
    brother_command = ""
    turn = [0]

    def log_message(self, *_args):
        pass

    def _tool_name(self, body):
        for tool in body.get("tools") or []:
            fn = tool.get("function") or tool
            if (fn.get("name") or "") in ("shell", "exec_command",
                                          "unified_exec", "local_shell",
                                          "bash"):
                return fn["name"]
        for tool in body.get("tools") or []:
            fn = tool.get("function") or tool
            if fn.get("name"):
                return fn["name"]
        return None

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            body = json.loads(raw) if raw else {}
        except (ValueError, OSError) as exc:
            self.send_response(400)
            self.end_headers()
            sys.stderr.write("codex_smoke provider: bad request: %s\n" % exc)
            return
        self.turn[0] += 1
        turn = self.turn[0]
        created = {"type": "response.created",
                   "response": {"id": "resp_c7_%d" % turn}}
        completed = {"type": "response.completed",
                     "response": {
                         "id": "resp_c7_%d" % turn, "output": [],
                         "usage": {"input_tokens": 1,
                                   "input_tokens_details": {"cached_tokens": 0},
                                   "output_tokens": 1,
                                   "output_tokens_details":
                                       {"reasoning_tokens": 0},
                                   "total_tokens": 2}}}
        name = self._tool_name(body)
        if turn == 1 and name is not None:
            item = {"type": "function_call", "id": "fc_c7",
                    "call_id": "call_c7", "name": name,
                    "arguments": json.dumps({"cmd": self.brother_command})}
        else:
            item = {"type": "message", "id": "msg_c7", "role": "assistant",
                    "content": [{"type": "output_text",
                                 "text": "brother run finished"}]}
        chunks = [created, {"type": "response.output_item.done", "item": item},
                  completed]
        payload = "".join("data: " + json.dumps(c) + "\n\n"
                          for c in chunks).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except OSError as exc:
            sys.stderr.write("codex_smoke provider: client hung up: %s\n" % exc)


def codex_env(base, codex_home, home):
    env = dict(base)
    env["CODEX_HOME"] = codex_home
    env["HOME"] = home
    return env


def stub_turn(codex_bin, env, toy, command):
    """One real `codex exec` turn whose model is this file's local provider."""
    _Handler.brother_command = command
    _Handler.turn = [0]
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run_env = dict(env)
    run_env["C7_STUB_KEY"] = "c7-not-a-real-key"
    args = [codex_bin, "exec", "--skip-git-repo-check",
            "-c", 'model_provider="c7stub"',
            "-c", 'model="c7-stub-model"',
            "-c", 'model_providers.c7stub.name="c7 stub"',
            "-c", 'model_providers.c7stub.base_url='
                  '"http://127.0.0.1:%d/v1"' % port,
            "-c", 'model_providers.c7stub.wire_api="responses"',
            "-c", 'model_providers.c7stub.env_key="C7_STUB_KEY"',
            "-c", 'approval_policy="never"',
            "-c", 'sandbox_mode="danger-full-access"',
            "-C", toy,
            "use the Brother plugin to make add() refuse non-numeric input"]
    try:
        return sh(args, env=run_env, timeout=900)
    finally:
        server.shutdown()


def receipt_lines(run_dir):
    """The receipt's per-file check entries, read from the receipt the engine
    itself wrote. `scope` is receipt_door.per_file_checks' own output, stored
    by brother_run._write_receipt, so nothing is recomputed or retyped here."""
    path = os.path.join(run_dir, "receipt", "receipt.json")
    if not os.path.isfile(path):
        return None, "no receipt at %s" % path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "receipt unreadable: %s" % exc
    scope = (body.get("scope") or {}).get("changed")
    if not isinstance(scope, list) or not scope:
        return None, "the receipt at %s names no changed file" % path
    lines = []
    for entry in scope:
        lines.append("%s (unit %s): state %s, check %s exited %s%s" % (
            entry.get("file"), entry.get("unit"),
            entry.get("state") or "NO-DATA",
            entry.get("check_command") or "NO-DATA",
            entry.get("exit_code"),
            (", " + entry["reason"]) if entry.get("reason") else ""))
    return (path, lines), ""


def newest_run_dir(runs_root):
    if not os.path.isdir(runs_root):
        return None
    entries = [os.path.join(runs_root, n) for n in os.listdir(runs_root)]
    entries = [e for e in entries if os.path.isdir(e)]
    if not entries:
        return None
    return max(entries, key=os.path.getmtime)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--codex-bin", default=DEFAULT_CODEX,
                    help="the Codex binary to smoke (defaults to the "
                         "app-bundled one)")
    ap.add_argument("--work", default=DEFAULT_WORK,
                    help="the throwaway directory holding the isolated Codex "
                         "home, HOME and toy repository")
    ap.add_argument("--keep", action="store_true",
                    help="keep the work directory instead of wiping it first")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.codex_bin) or not os.access(args.codex_bin,
                                                           os.X_OK):
        print("NO-DATA: no executable Codex binary at %s, so nothing was "
              "smoked. Install the Codex CLI or pass --codex-bin." %
              args.codex_bin)
        return 2

    work = os.path.abspath(args.work)
    if not args.keep and os.path.isdir(work):
        shutil.rmtree(work)
    codex_home = os.path.join(work, "codex-home")
    fake_home = os.path.join(work, "home")
    toy = os.path.join(work, "toy")
    stubs = os.path.join(work, "stubs")
    runs_root = os.path.join(work, "runs")
    logs = os.path.join(work, "logs")
    for path in (codex_home, fake_home, stubs, runs_root, logs):
        os.makedirs(path, exist_ok=True)
    LOG_DIR[0] = logs
    print("full output of every step below: %s" % logs)

    before, witness_desc = founder_witness()
    print("founder ~/.codex witness before: %s" % (before or "NO-DATA"))
    print("  (%s)" % witness_desc)

    version = sh([args.codex_bin, "--version"], timeout=60)
    print("codex binary: %s   %s" % (args.codex_bin,
                                     (version.stdout or "").strip()))

    failures = []
    env = codex_env(os.environ, codex_home, fake_home)

    step1 = sh([args.codex_bin, "plugin", "marketplace", "add", REPO], env=env)
    if report("1 marketplace add", step1) != 0:
        failures.append("marketplace add")

    step2 = sh([args.codex_bin, "plugin", "add", "brother@brother", "--json"],
               env=env)
    if report("2 plugin add", step2) != 0:
        failures.append("plugin add")

    step3 = sh([args.codex_bin, "plugin", "list", "--available", "--json"],
               env=env)
    report("3 plugin list --available --json", step3, tail=3)
    if step3.returncode != 0 or '"brother@brother"' not in (step3.stdout or ""):
        failures.append("plugin list did not report pluginId brother@brother")

    why = build_toy(toy)
    if why:
        print("FAIL: %s" % why)
        return 1

    step4 = sh([sys.executable, os.path.join(HERE, "codex_hooks_install.py"),
                "--codex-home", codex_home, "--trust", "--cwd", toy,
                "--codex-bin", args.codex_bin], env=env)
    if report("4 codex_hooks_install", step4, tail=3) != 0:
        failures.append("codex_hooks_install")

    # Step 5, first half: the real thing, with no stub, so the credential
    # refusal is CAPTURED rather than predicted.
    real = sh([args.codex_bin, "exec", "-C", toy,
               "use the Brother plugin to make add() refuse non-numeric "
               "input and cover it with a test"], env=env, timeout=300)
    report("5a real codex task (no credentials in the isolated home)", real,
           tail=2)
    refusal = [ln for ln in ((real.stdout or "") + (real.stderr or "")
                             ).splitlines() if "401 Unauthorized" in ln]
    if real.returncode == 0:
        print("  NOTE: this home HAS credentials, so 5a was a real run.")
    elif refusal:
        print("  blocker for the credentialled half: %s" % refusal[-1][:200])
    else:
        failures.append("5a failed for a reason that is not a login refusal")

    # Step 5, second half: the same turn through the local stub provider, so
    # the plugin, the hooks, the engine and the receipt path all run.
    stub_env = dict(env)
    stub_env["DOOR_MODEL_CMD"] = "%s %s" % (
        sys.executable, write_stub(os.path.join(stubs, "decomposer.py"),
                                   DECOMPOSER_STUB))
    stub_env["MODEL_WORKER_CMD"] = "%s %s" % (
        sys.executable, write_stub(os.path.join(stubs, "model.py"),
                                   MODEL_STUB))
    command = ("%s %s 'make add() refuse non-numeric input' --cwd %s "
               "--runs-root %s --quiet" % (
                   sys.executable, os.path.join(HERE, "brother_run.py"), toy,
                   runs_root))
    stub = stub_turn(args.codex_bin, stub_env, toy, command)
    if report("5b codex task through the stub provider", stub, tail=8) != 0:
        failures.append("the stub codex turn did not complete")

    run_dir = newest_run_dir(os.path.join(runs_root, "docs", "plan", "runs")) \
        or newest_run_dir(runs_root)
    if run_dir is None:
        failures.append("the run left no run directory under %s" % runs_root)
    else:
        found, why = receipt_lines(run_dir)
        if found is None:
            failures.append("no usable receipt: %s" % why)
        else:
            path, lines = found
            print("\nreceipt: %s" % path)
            for line in lines:
                print("  %s" % line)

    after, _ = founder_witness()
    print("\nfounder ~/.codex witness after:  %s" % (after or "NO-DATA"))
    if before is None or after is None:
        print("NO-DATA: the founder's ~/.codex could not be witnessed, so "
              "isolation is unproven.")
        return 2
    if before != after:
        failures.append("the founder's ~/.codex CHANGED during this run")
    else:
        print("isolation holds: the witness is byte-identical before and "
              "after.")

    if failures:
        print("\nFAIL: %s" % "; ".join(failures))
        return 1
    print("\nPASS: the four codex plugin commands ran at exit 0 in an "
          "isolated home, a real codex turn produced a receipt through the "
          "stub provider, and the founder's ~/.codex was untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
