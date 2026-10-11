#!/usr/bin/env python3
"""Tests for scripts/host_live_verify.py (HP1.b, spec docs/plan/specs/HP1.md sections 5.3, 6 and 10).

TestVerifierRefusals builds one complete, fresh, signed in evidence set with the real writer host_live_proof.write_row
against a candidate checkout this file writes as git objects by hand (no git process: the verifier reads git objects
itself), then mutates ONE field per case so exactly one guard refuses. The Claude Code stream crosscheck belongs to HP1.e
(scripts/host_live_claude.py): each case puts a stand in module in sys.modules that compares shim rows and stream events
per event name, and one case removes it to show the verifier reads NO-DATA without it. Every case has its own temp folder
and HOME: nothing is written under ~/.claude.

Run: python3 scripts/test_host_live_verify.py TestVerifierRefusals -v
"""
import base64
import contextlib
import datetime
import hashlib
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
import zlib
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import codex_hooks_install as translator  # noqa: E402
import host_live_proof as proof  # noqa: E402
import host_live_verify as verifier  # noqa: E402

HOSTILE = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
WITNESS = os.path.join(HERE, "host_hook_trace.py")
TOOLS = ("scripts/host_hook_trace.py", "scripts/host_live_proof.py", "scripts/host_live_verify.py",
         "scripts/host_live_claude.py", "scripts/codex_hooks_install.py")   # the five tool scripts of spec 5.2
AG_ROOT = "bundle/.antigravity-plugin"
AG_SCRIPT = "scripts/brother_antigravity_hook.py"
AG_HOOKS = {"brother": {
    "PreInvocation": [{"type": "command", "command": "python3 %s pre_invocation" % AG_SCRIPT, "timeout": 10}],
    "PostInvocation": [{"type": "command", "command": "python3 %s post_invocation" % AG_SCRIPT, "timeout": 10}],
    "PreToolUse": [{"matcher": "write_to_file|run_command", "hooks": [
        {"type": "command", "command": "python3 %s pre_tool" % AG_SCRIPT, "timeout": 10}]}]}}
GUARD = 'python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py" brothermode %s %spython3 "${CLAUDE_PLUGIN_ROOT}/%s"'
CC_HOOKS = {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": GUARD % (
        "SessionStart", "", "runtime/hooks/brothermode/tools/start.py"), "timeout": 10}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": GUARD % (
        "UserPromptSubmit", "", "runtime/hooks/brothermode/tools/door.py"), "timeout": 10}]}],
    "PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": GUARD % (
        "PreToolUse", '"--matcher=Write" ', "runtime/hooks/brothermode/tools/guard.py"), "timeout": 10}]}]}}
PRODUCT_HOOKS = {"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/start.py"',
                                 "timeout": 10}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/door.py"',
                                     "timeout": 10}]}],
    "PreToolUse": [{"matcher": "apply_patch", "hooks": [
        {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/guard.py"', "timeout": 10}]}]}}
TURN_OK = b'{"type": "thread.started", "thread_id": "thread-write"}\n{"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 4}}\n'
STREAM_OK = b'{"type": "system", "subtype": "init", "session_id": "cc-write"}\n{"type": "result", "usage": {"input_tokens": 9, "output_tokens": 2}}\n'
ANSWER = b'{"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "Brother status: **NO-DATA**, nothing recorded yet."}}\n'
#: The Claude Code door turn as the 2.1.286 stream-json shapes read on 2026-10-04 say a signed in one ends: an init line
#: naming the session, then a result line with is_error false, the answer in result, and usage.
CC_DOOR_OK = (b'{"type": "system", "subtype": "init", "session_id": "cc-door"}\n'
              b'{"type": "result", "subtype": "success", "is_error": false, "session_id": "cc-door", '
              b'"result": "Brother status: **NO-DATA**, nothing recorded yet.", "usage": {"input_tokens": 9}}\n')
DOOR_OK = TURN_OK.replace(b'{"type": "turn.completed"', ANSWER + b'{"type": "turn.completed"').replace(
    b"thread-write", b"thread-door")   # the door answered, in the door run's own thread


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha1(data):
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def stamp(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def write(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)
    return path


def jbytes(doc):
    return (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")


def group(files):
    """{name: (mode, bytes) or a nested group} for one tree level."""
    out = {}
    for rel, item in files.items():
        head, _, rest = rel.partition("/")
        if rest:
            out.setdefault(head, {})[rest] = item
        else:
            out[head] = item
    return out


def tree_order(level):
    """The names of one tree level in git's order: a folder compares as its name followed by a slash."""
    return sorted(level, key=lambda n: n.encode("utf-8") + (b"/" if isinstance(level[n], dict) else b""))


def ls_tree_lines(files, tops=("bundle", "plugin", "products")):
    """What `git ls-tree -r HEAD -- plugin bundle products` prints for these files (none of them needs quoting)."""
    def walk(level, prefix):
        for name in tree_order(level):
            item = level[name]
            if isinstance(item, dict):
                for line in walk(group(item), prefix + name + "/"):
                    yield line
            else:
                yield "%s blob %s\t%s%s\n" % (item[0], blob_sha1(item[1]), prefix, name)
    top = group(files)
    return "".join(line for name in tree_order(top) if name in tops for line in walk({name: top[name]}, ""))


class GitRepo(object):
    """A checkout written as git objects by hand: loose objects, one branch, the working files beside them."""

    def __init__(self, root):
        self.root, self.git, self.head = root, os.path.join(root, ".git"), None
        os.makedirs(os.path.join(self.git, "objects"))
        os.makedirs(os.path.join(self.git, "refs", "heads"))
        write(os.path.join(self.git, "HEAD"), b"ref: refs/heads/main\n")

    def put(self, kind, body):
        data = b"%s %d\x00" % (kind, len(body)) + body
        name = hashlib.sha1(data).hexdigest()
        path = os.path.join(self.git, "objects", name[:2], name[2:])
        if not os.path.exists(path):
            write(path, zlib.compress(data))
        return name

    def tree(self, files):
        body, level = b"", group(files)
        for name in tree_order(level):
            item = level[name]
            mode, sub = (b"40000", self.tree(item)) if isinstance(item, dict) else (item[0].encode(), self.put(b"blob", item[1]))
            body += mode + b" " + name.encode("utf-8") + b"\x00" + bytes.fromhex(sub)
        return self.put(b"tree", body)

    def commit(self, files, when, on_disk=True):
        lines = [b"tree " + self.tree(files).encode()]
        lines += [b"parent " + self.head.encode()] if self.head else []
        lines += [b"author fixture <fixture@example.invalid> %d +0000" % when,
                  b"committer fixture <fixture@example.invalid> %d +0000" % when, b"", b"fixture commit", b""]
        self.head = self.put(b"commit", b"\n".join(lines))
        write(os.path.join(self.git, "refs", "heads", "main"), self.head.encode() + b"\n")
        if on_disk:
            for rel, (mode, data) in files.items():
                write(os.path.join(self.root, rel), data, 0o755 if mode == "100755" else 0o644)
        return self.head


def candidate_files():
    files = {
        "README.md": ("100644", b"fixture candidate\n"),
        "plugin/VERSION": ("100644", b"1.1.0\n"),
        AG_ROOT + "/hooks.json": ("100644", jbytes(AG_HOOKS)),
        AG_ROOT + "/" + AG_SCRIPT: ("100644", b"# the Antigravity adapter fixture\n"),
        "bundle/hooks/hooks.json": ("100644", jbytes(CC_HOOKS)),
        "bundle/runtime/hooks/hook_guard.py": ("100644", b"# the guard fixture\n"),
        "bundle/runtime/hooks/brothermode/tools/door.py": ("100644", b"# the Claude Code door hook fixture\n"),
        "bundle/runtime/hooks/brothermode/tools/guard.py": ("100644", b"# the Claude Code write guard fixture\n"),
        "bundle/runtime/hooks/brothermode/tools/start.py": ("100644", b"# the Claude Code session start fixture\n"),
        "products/brothermode/hooks/hooks.json": ("100644", jbytes(PRODUCT_HOOKS)),
        "bundle/.codex-plugin/plugin.json": ("100644", jbytes({"name": "brother", "version": "1.1.0"})),
        "bundle/skills/using-brother/SKILL.md": ("100644", b"---\nname: using-brother\n---\nthe door fixture\n"),
        "bundle/skills/brother-status/SKILL.md": ("100644", b"---\nname: brother-status\n---\nstatus fixture\n"),
        "products/brothermode/tools/door.py": ("100755", b"# the product door hook fixture\n"),
        "products/brothermode/tools/guard.py": ("100755", b"# the product write guard fixture\n"),
        "products/brothermode/tools/start.py": ("100755", b"# the product session start hook fixture\n"),
        "products/brothersbe/hooks/hooks.json": ("100644", jbytes(PRODUCT_HOOKS)),
        "products/brothersbe/tools/door.py": ("100644", b"# the second product door hook fixture\n"),
        "products/brothersbe/tools/guard.py": ("100644", b"# the second product write guard fixture\n"),
        "products/brothersbe/tools/start.py": ("100644", b"# the second product session start hook fixture\n"),
    }
    for rel in TOOLS:
        path = os.path.join(os.path.dirname(HERE), rel)
        if os.path.isfile(path):
            files[rel] = ("100644", read(path))
    return files


def stand_in_claude():
    """HP1.e's two functions as the spec states them: parse the stream, compare counts per event name."""
    mod = types.ModuleType("host_live_claude")

    def parse_hook_events(stream, plugin_root=None):
        return [json.loads(line) for line in stream.decode("utf-8").splitlines() if line.strip()]

    def crosscheck(shim_rows, stream_events):
        shim = sorted(r["event"] for r in shim_rows)
        seen = sorted(e["event"] for e in stream_events)
        return (shim == seen, "" if shim == seen else "shim %s, stream %s" % (shim, seen))
    mod.parse_hook_events, mod.crosscheck = parse_hook_events, crosscheck
    return mod


class Fixture(object):
    """A candidate checkout, three host installs and the evidence of one complete, fresh, signed in run per host."""

    def __init__(self, tmp, now):
        self.tmp, self.now = tmp, now
        self.root = os.path.join(tmp, "candidate")
        self.repo = GitRepo(self.root)
        self.files = candidate_files()
        self.repo.commit(self.files, now - 3600)   # the last commit to plugin, bundle or products
        self.files["README.md"] = ("100644", b"fixture candidate, edited outside the plugin\n")
        self.repo.commit(self.files, now - 1800)   # a later commit that touches none of them
        self.real = os.path.realpath(self.root)
        self.tree_hash = sha(ls_tree_lines(self.files).encode("utf-8"))
        self.tools = dict((rel, sha(self.files[rel][1]) if rel in self.files else "absent") for rel in TOOLS)
        self.evidence_dir = os.path.join(tmp, "evidence")
        os.makedirs(self.evidence_dir)
        self.evidence = os.path.join(self.evidence_dir, "HP1-host-live.jsonl")
        self.shim_dir = os.path.join(tmp, "shim")
        shim = proof.write_shim(self.shim_dir, sys.executable)
        write(os.path.join(self.evidence_dir, "python3.shim"), read(shim))
        self.shim_sha = sha(read(shim))
        self.target = os.path.join(tmp, "outside", proof.GUARDED_WRITE_NAME)
        self.workspace = os.path.join(tmp, "workspace")
        self.hosts = dict((h, os.path.join(tmp, "hosts", "%s.app" % h)) for h in verifier.REQUIRED_HOSTS)
        self.bins = dict((h, os.path.join(self.hosts[h], "Contents", "MacOS", h)) for h in verifier.REQUIRED_HOSTS)
        link = os.path.join(self.workspace, ".agents", "plugins", "brother")
        os.makedirs(os.path.dirname(link))
        os.symlink(os.path.join(self.real, AG_ROOT), link)
        codex_doc = translator.build([os.path.join(self.real, p) for p in ("products/brothermode", "products/brothersbe")])
        assert not codex_doc["problems"], codex_doc["problems"]
        codex_hooks = translator.dump(codex_doc["document"]).encode("utf-8")
        self.hooks = {
            "antigravity": (os.path.join(link, "hooks.json"), sha(self.files[AG_ROOT + "/hooks.json"][1])),
            "claude": (os.path.join(self.real, "bundle", "hooks", "hooks.json"), sha(self.files["bundle/hooks/hooks.json"][1])),
            "codex": (write(os.path.join(tmp, "codex-home", "hooks.json"), codex_hooks), sha(codex_hooks)),
        }
        self.roots = {"antigravity": os.path.join(self.real, AG_ROOT), "claude": os.path.join(self.real, "bundle"),
                      "codex": os.path.join(self.real, "products", "brothermode")}
        for name in verifier.DOOR_SKILLS:   # the Codex install on disk, byte equal to the candidate's door skills
            write(self.door_skill(name), self.files["bundle/skills/%s/SKILL.md" % name][1])
        self.entries = self.scenario()

    # the evidence ------------------------------------------------------------------------------------------------------

    def envelope(self, name, argv, code, stdout):
        body = json.dumps({"argv": argv, "exit_code": code, "stdout_b64": base64.b64encode(stdout).decode("ascii")},
                          sort_keys=True).encode("utf-8")
        write(os.path.join(self.evidence_dir, name), body)
        return {"argv": argv, "envelope_path": name, "envelope_sha256": sha(body)}

    def chain(self, host, witness_argv, middle=()):
        """The witness, then any middle links (exe, argv), then the host binary."""
        links = [{"pid": 4100, "ppid": 4099, "comm": "python3", "exe": os.path.realpath(sys.executable),
                  "argv": "%s -B %s" % (sys.executable, " ".join(witness_argv))}]
        pid = 4099
        for exe, argv in middle:
            links.append({"pid": pid, "ppid": pid - 1, "comm": os.path.basename(exe), "exe": exe, "argv": argv})
            pid -= 1
        links.append({"pid": pid, "ppid": 1, "comm": host, "exe": self.bins[host], "argv": self.bins[host]})
        return links

    def entry(self, host, probe, event, script_rel, args, payload, raw_out, exit_code, want, stderr_len=0, effect=None):
        """One evidence row as the collector writes it, with its raw stdin and stdout, checked against the one labeller
        and the one verdict table so the fixture itself is what a host produces."""
        script = os.path.join(self.roots[host], script_rel)
        first = script_rel if host == "antigravity" else script   # Antigravity passes the relative script
        raw_in = json.dumps(payload, sort_keys=True).encode("utf-8")
        verdict = proof.parse_verdict(raw_out, exit_code)
        assert verdict == want, (host, probe, verdict, want)
        assert proof.probe_of(raw_in, event=event) == probe, (host, probe)
        witness_argv = [WITNESS, first] + list(args)
        row = {"schema": "hp1.v1", "host": host, "run_id": "run-%s-2" % host, "probe": probe, "event": event,
               "verdict": verdict[0], "verdict_source": verdict[1], "exit_code": exit_code, "stderr_len": stderr_len,
               "hook_command": [first] + list(args), "script": script, "script_sha256": sha(read(script)),
               "plugin_root": self.roots[host], "parent_chain": self.chain(host, witness_argv),
               "host_roots": [self.hosts[host]], "host_bin_realpath": self.bins[host],
               "host_version": "%s 1.107.0" % host, "recorded_at": stamp(self.now - 600),
               "plugin_tree_sha256": self.tree_hash, "tree_clean": True, "shim_dir": self.shim_dir,
               "shim_path": "python3.shim", "shim_sha256": self.shim_sha, "real_python": sys.executable,
               "witness_argv": witness_argv, "witness_sha256": sha(read(WITNESS)), "hooks_path": self.hooks[host][0],
               "hooks_sha256": self.hooks[host][1], "hooks_expected_sha256": self.hooks[host][1],
               "tool_sha256": dict(self.tools)}
        if effect is not None:
            row["effect"] = dict(effect)
        if host != "antigravity":
            prompt = {"door": proof.DOOR_SENTENCE, "verb": proof.VERB_SENTENCE}.get(probe, "write the guarded file")
            argv = ([self.bins[host], "exec", "--json", "--ephemeral", "-C", self.workspace, prompt] if host == "codex" else
                    [self.bins[host], "--print", "--output-format", "stream-json", "--verbose", "--include-hook-events", "--plugin-dir",
                     self.roots["claude"], prompt])
            row["model_turn"] = self.envelope("%s-%s-turn.json" % (host, probe), argv, 0,
                                              TURN_OK if host == "codex" else STREAM_OK)
        if host == "codex":
            row["signed_in"] = self.envelope("codex-login-status.json", [self.bins[host], "login", "status"], 0,
                                             b"Logged in using ChatGPT\n")
        return {"row": row, "raw_in": raw_in, "raw_out": raw_out}

    def scenario(self):
        clean = {"path": self.target, "exists_before": False, "exists_after": False}
        written = dict(clean, exists_after=True)
        door = {"hook_event_name": "UserPromptSubmit", "prompt": proof.DOOR_SENTENCE, "session_id": "s1"}
        verb = dict(door, prompt=proof.VERB_SENTENCE)
        out = []
        for host, rel in (("claude", "runtime/hooks/brothermode/tools/"), ("codex", "tools/")):
            out.append(self.entry(host, "door", "UserPromptSubmit", rel + "door.py", [], door, b"", 0,
                                  ("allow", "silent_exit_0")))
            out.append(self.entry(host, "verb", "UserPromptSubmit", rel + "door.py", [], verb, b"", 0,
                                  ("allow", "silent_exit_0")))
        # The guarded write as EXPECTED_GUARDED_WRITE says the shipped hooks answer: unguarded in 1.1.0 (every hook
        # allows; the host's own permission system decides the file, so Claude Code refused it here and Codex wrote
        # it, both consistent), deny when a test patches the table to the 1.1.1 guard.
        deny = dict((h, verifier.EXPECTED_GUARDED_WRITE[h] == "deny") for h in ("claude", "codex"))
        cc_out = (b"", 2, ("deny", "exit_2")) if deny["claude"] else (b"", 0, ("allow", "silent_exit_0"))
        out.append(self.entry("claude", "guarded_write", "PreToolUse", "runtime/hooks/brothermode/tools/guard.py", [],
                              {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "cc-write",
                               "tool_input": {"file_path": self.target, "content": "hp1"}},
                              cc_out[0], cc_out[1], cc_out[2], stderr_len=41 if deny["claude"] else 0, effect=clean))
        cx_out = ((b'{"decision": "block", "reason": "outside the workspace"}', 0, ("deny", "json_deny"))
                  if deny["codex"] else (b"", 0, ("allow", "silent_exit_0")))
        out.append(self.entry("codex", "guarded_write", "PreToolUse", "tools/guard.py", [],
                              {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "session_id": "thread-write",
                               "tool_input": {"command": "*** Add File: %s" % self.target}},
                              cx_out[0], cx_out[1], cx_out[2], effect=clean if deny["codex"] else written))
        # Codex's SessionStart payload carries no prompt (the 2026-10-04 run): labelled other, and what the
        # hp1-codex-door ruling makes Codex owe. The collector attaches no envelope to an other row.
        start = self.entry("codex", "other", "SessionStart", "tools/start.py", [],
                           {"hook_event_name": "SessionStart", "source": "startup", "session_id": "thread-door",
                            "cwd": self.workspace, "model": "gpt", "permission_mode": "default",
                            "transcript_path": None}, b"", 0, ("allow", "silent_exit_0"))
        for key in ("model_turn", "signed_in"):
            start["row"].pop(key)
        start["row"]["plugin_install"] = self.install_root()   # what --run measured after the install
        start["row"]["plugin_install_sha256"] = self.install_sha()   # and the digest of the door skills it installed
        start["row"]["door_turn"] = self.envelope("run-codex-2-door.json", [
            self.bins["codex"], "exec", "--json", "--ephemeral", "-C", self.workspace, proof.DOOR_SENTENCE], 0, DOOR_OK)
        out.append(start)
        # The door routed: the door thread's own PreToolUse hook stdin loads the status skill from the installed
        # plugin root, as measured on 2026-10-04 (labelled other; the collector attaches no envelope to it).
        load = self.entry("codex", "other", "PreToolUse", "tools/guard.py", [],
                          {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "thread-door",
                           "tool_input": {"command": "cat %s" % self.door_skill()}}, b"", 0, ("allow", "silent_exit_0"))
        for key in ("model_turn", "signed_in"):
            load["row"].pop(key)
        out.append(load)
        # Claude Code owes the same three legs (owner ruling 2026-10-05 hp1-claude-door): its SessionStart payload
        # carries no prompt (the real 2.1.286 run), the door turn is its stream-json transcript, and the door skill is
        # read under the --plugin-dir that turn named, which is the plugin_root the witness recorded.
        cc_start = self.entry("claude", "other", "SessionStart", "runtime/hooks/brothermode/tools/start.py", [],
                              {"hook_event_name": "SessionStart", "source": "startup", "session_id": "cc-door",
                               "cwd": self.workspace, "transcript_path": None}, b"", 0, ("allow", "silent_exit_0"))
        cc_start["row"].pop("model_turn")
        cc_start["row"]["door_turn"] = self.envelope("run-claude-2-door.json", self.claude_argv(), 0, CC_DOOR_OK)
        out.append(cc_start)
        cc_load = self.entry("claude", "other", "PreToolUse", "runtime/hooks/brothermode/tools/guard.py", [],
                             {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "cc-door",
                              "tool_input": {"command": "cat %s" % self.door_skill(root=self.roots["claude"])}},
                             b"", 0, ("allow", "silent_exit_0"))
        cc_load["row"].pop("model_turn")
        out.append(cc_load)
        inv = {"conversationId": "c1", "workspacePaths": [self.workspace]}
        out.append(self.entry("antigravity", "door", "PreInvocation", AG_SCRIPT, ["pre_invocation"],
                              dict(inv, invocationNum=1), b'{"injectSteps": []}', 0, ("allow", "json_inject")))
        out.append(self.entry("antigravity", "verb", "PreInvocation", AG_SCRIPT, ["pre_invocation"],
                              dict(inv, invocationNum=2), b'{"injectSteps": []}', 0, ("allow", "json_inject")))
        out.append(self.entry("antigravity", "guarded_write", "PreToolUse", AG_SCRIPT, ["pre_tool"],
                              {"toolCall": {"name": "write_to_file", "args": {"TargetFile": self.target}},
                               "stepIdx": 1, "conversationId": "c1"},
                              b'{"decision": "allow", "reason": "full access by default"}', 0, ("allow", "json_allow"),
                              effect=written))
        out.append(self.entry("antigravity", "post_invocation", "PostInvocation", AG_SCRIPT, ["post_invocation"],
                              dict(inv, invocationNum=1), b'{"injectSteps": [], "terminationBehavior": ""}', 0,
                              ("allow", "json_inject")))
        stream = b"".join(json.dumps({"event": e["row"]["event"]}).encode() + b"\n"
                          for e in out if e["row"]["host"] == "claude")
        write(os.path.join(self.evidence_dir, "claude-stream.jsonl"), stream)
        for e in out:
            if e["row"]["host"] == "claude":
                e["row"].update({"stream_path": "claude-stream.jsonl", "stream_sha256": sha(stream)})
        return out

    def claude_argv(self, prompt=None, plugin_dir=None):
        return [self.bins["claude"], "--print", "--output-format", "stream-json", "--verbose", "--include-hook-events",
                "--plugin-dir", plugin_dir or self.roots["claude"], prompt or proof.DOOR_SENTENCE]

    def install_root(self):
        """Where Codex installs the plugin: <CODEX_HOME>/plugins/cache/brother/brother/<version> (measured 2026-10-04)."""
        return os.path.join(os.path.dirname(self.hooks["codex"][0]), "plugins", "cache", "brother", "brother", "1.1.0")

    def install_sha(self, root=None):
        """install_digest of the candidate's door skills, as --run computes it from a faithful install at root."""
        shas = dict((name, sha(self.files["bundle/skills/%s/SKILL.md" % name][1])) for name in verifier.DOOR_SKILLS)
        return proof.install_digest(root or self.install_root(), shas)

    def door_skill(self, name="brother-status", root=None):
        """A shipped skill's SKILL.md as Codex installs it, under root (default the installed plugin root)."""
        return os.path.join(root or self.install_root(), "skills", name, "SKILL.md")

    def find(self, host, probe):
        return next(e for e in self.entries if e["row"]["host"] == host and e["row"]["probe"] == probe)

    def write_evidence(self, entries=None):
        if os.path.exists(self.evidence):
            os.unlink(self.evidence)
        for e in self.entries if entries is None else entries:
            proof.write_row(self.evidence, e["row"], e["raw_in"], e["raw_out"])
        proof.write_manifest(self.evidence)   # what the collector writes after every collect
        return self.evidence


class TestVerifierRefusals(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="hp1b-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        for patch in (mock.patch.dict(os.environ, {"HOME": home}),
                      mock.patch.dict(sys.modules, {"host_live_claude": stand_in_claude()})):
            patch.start()
            self.addCleanup(patch.stop)
        self.now = int(time.time())
        self.fx = Fixture(self.tmp, self.now)

    # helpers -----------------------------------------------------------------------------------------------------------

    def judge(self, evidence=None, root=None, now=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, line = verifier.verify(evidence or self.fx.evidence, root or self.fx.root,
                                         self.now if now is None else now, verifier.REQUIRED_HOSTS)   # the 1.1.1 bar
        self.assertEqual(out.getvalue(), line + "\n")   # one printed line, the one returned
        return code, line

    def assertGreen(self):
        self.fx.write_evidence()
        code, line = self.judge()
        self.assertEqual((code, line[:6]), (0, "GREEN:"), line)
        return line

    def assertRed(self, *words):
        self.fx.write_evidence()
        code, line = self.judge()
        self.assertEqual((code, line[:4]), (1, "RED:"), line)
        for word in words:
            self.assertIn(word, line)
        return line

    def assertNoData(self, *words):
        self.fx.write_evidence()
        code, line = self.judge()
        self.assertEqual((code, line[:8]), (3, "NO-DATA:"), line)
        for word in words:
            self.assertIn(word, line)
        return line

    def row(self, host, probe):
        return self.fx.find(host, probe)["row"]

    def rows_of(self, host):
        return [e["row"] for e in self.fx.entries if e["row"]["host"] == host]

    def loaded(self, host, probe):
        rows, problem = verifier.load_rows(self.fx.write_evidence())
        self.assertEqual(problem, "")
        return next(r for r in rows if r["host"] == host and r["probe"] == probe)

    def set_turn(self, host, probe, code=0, stdout=TURN_OK, extra=None):
        ref = self.row(host, probe)["model_turn"]
        new = self.fx.envelope(ref["envelope_path"], ref["argv"], code, stdout)
        new.update(extra or {})
        self.row(host, probe)["model_turn"] = new

    # the baseline --------------------------------------------------------------------------------------------------------

    def test_complete_fresh_signed_in_evidence_is_green(self):
        line = self.assertGreen()
        for host in verifier.REQUIRED_HOSTS:
            self.assertIn("%s run run-%s-2" % (host, host), line)
        self.assertIn(self.fx.tree_hash[:12], line)
        self.assertEqual(verifier.missing_hosts([e["row"] for e in self.fx.entries]), [])
        self.assertEqual(verifier.EXPECTED_GUARDED_WRITE,
                         {"claude": "unguarded", "codex": "unguarded", "antigravity": "allow"})
        self.assertEqual(verifier.RULED_NO_DATA, {"codex": ("door", "verb"), "claude": ("door", "verb")})
        self.assertNotIn("NO-DATA by", line)   # every Codex probe row arrived, so none is reported as ruled
        self.assertEqual(verifier.REQUIRED_PROBES, ("door", "verb", "guarded_write"))
        self.assertEqual(verifier.EXTRA_PROBES, {"antigravity": ("post_invocation",)})

    # REQ-HP1-NO-DATA and REQ-HP1-REQUIRED ------------------------------------------------------------------------------

    def test_missing_file_is_no_data(self):
        code, line = self.judge(evidence=os.path.join(self.tmp, "evidence", "not-recorded.jsonl"))
        self.assertEqual((code, line[:8]), (3, "NO-DATA:"), line)
        self.assertIn("host_live_proof.py --run", line)   # names the command that writes it
        self.assertNotIn("per host", line)   # one unreadable file is one answer, never repeated per host
        self.assertEqual(verifier.load_rows(os.path.join(self.tmp, "nope.jsonl"))[0], [])

    def test_empty_file_is_no_data(self):
        for data in (b"", b"\n", b"\n  \n\t\n"):
            write(self.fx.evidence, data)
            code, line = self.judge()
            self.assertEqual((code, line[:8]), (3, "NO-DATA:"), (data, line))

    def test_missing_host_is_no_data(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] != "codex"]
        self.assertNoData("no row from codex")
        rows, _ = verifier.load_rows(self.fx.evidence)
        self.assertEqual(verifier.missing_hosts(rows), ["codex"])
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] == "antigravity"]
        self.assertNoData("claude: NO-DATA: no row from claude", "codex: NO-DATA: no row from codex")

    # the 1.1.0 scope (owner decision 2026-10-03): the Antigravity rows are reported NO-DATA and never decide -----------

    def scoped(self, required):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            return verifier.verify(self.fx.write_evidence(), self.fx.root, self.now, required)

    def test_a_host_outside_the_deciding_scope_is_reported_and_never_decides(self):
        self.assertEqual(verifier.DECIDING_HOSTS_1_1_0, ("claude", "codex"))
        self.row("antigravity", "guarded_write")["verdict"] = "deny"   # RED when every host decides
        self.assertRed("antigravity")
        code, line = self.scoped(verifier.DECIDING_HOSTS_1_1_0)
        self.assertEqual((code, line[:6]), (0, "GREEN:"), line)
        self.assertIn(verifier.ASIDE_NOTE % ("antigravity", 4), line)
        self.assertNotIn("antigravity run", line)

    # the 1.1.0 default (owner decision 2026-10-03): claude and codex decide, each judged and reported on its own -----

    def default(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, line = verifier.verify(self.fx.write_evidence(), self.fx.root, self.now)
        self.assertEqual(out.getvalue(), line + "\n")
        return code, line

    def test_the_default_scope_is_claude_and_codex_and_antigravity_never_blocks(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] != "antigravity"]
        code, line = self.default()
        self.assertEqual((code, line[:6]), (0, "GREEN:"), line)
        self.assertIn(verifier.ASIDE_NOTE % ("antigravity", 0), line)
        self.assertIn("NO-DATA: experimental, certification moved to 1.1.1 by owner decision 2026-10-03", line)
        with mock.patch.object(time, "time", return_value=self.now), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(verifier.main([self.fx.evidence, "--root", self.fx.root]), 0)   # the CLI judges the same
        self.assertTrue(out.getvalue().startswith("GREEN:"), out.getvalue())

    def test_one_host_short_of_green_is_named_alone_and_the_other_keeps_its_verdict(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] != "antigravity"]
        self.cc_door()
        self.cc_start()["row"].pop("door_turn")
        code, line = self.default()
        self.assertEqual((code, line[:19]), (3, "NO-DATA: per host: "), line)
        parts = dict(p.split(": ", 1) for p in line[len("NO-DATA: per host: "):].split(" | "))
        self.assertEqual(sorted(parts), ["antigravity", "claude", "codex"])
        self.assertTrue(parts["claude"].startswith("NO-DATA: claude run run-claude-2: no transcript"), parts)
        self.assertTrue(parts["codex"].startswith("GREEN: codex run run-codex-2"), parts)
        self.assertNotIn("NO-DATA", parts["codex"])
        self.assertNotIn("antigravity", parts["claude"] + parts["codex"])   # the aside host is named once, at the end
        self.assertNotIn("extra host", line)   # the other deciding host is judged on its own, never ignored
        self.assertTrue(parts["antigravity"].startswith("NO-DATA: experimental, certification moved to 1.1.1"), parts)

    def test_every_deciding_host_is_reported_never_only_the_first(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] == "antigravity"]
        code, line = self.default()
        self.assertEqual(code, 3, line)
        self.assertIn("claude: NO-DATA: no row from claude", line)
        self.assertIn("codex: NO-DATA: no row from codex", line)
        self.assertIn(verifier.ASIDE_NOTE % ("antigravity", 4), line)

    def test_a_red_host_makes_the_line_red_and_the_other_host_still_reports(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] != "antigravity"]
        self.without("codex", "door")
        self.row("claude", "guarded_write")["verdict"] = "deny"
        code, line = self.default()
        self.assertEqual((code, line[:15]), (1, "RED: per host: "), line)
        self.assertIn("claude: RED:", line)
        self.assertIn("codex: GREEN:", line)

    def test_the_scope_narrows_what_decides_never_what_a_deciding_host_owes(self):
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] != "antigravity"]
        self.assertNoData("no row from antigravity")
        code, line = self.scoped(verifier.DECIDING_HOSTS_1_1_0)
        self.assertEqual((code, line[:6]), (0, "GREEN:"), line)
        self.assertIn(verifier.ASIDE_NOTE % ("antigravity", 0), line)
        self.fx.entries = [e for e in self.fx.entries if e["row"]["host"] != "codex"]
        code, line = self.scoped(verifier.DECIDING_HOSTS_1_1_0)
        self.assertEqual((code, line[:8]), (3, "NO-DATA:"), line)
        self.assertIn("no row from codex", line)
        evidence = self.fx.write_evidence()
        for bad in HOSTILE + [(), ("claude", "claude"), ("claude", "x"), "claude", ["codex", None]]:
            with self.assertRaises(verifier.VerifyRefused):
                verifier.verify(evidence, self.fx.root, self.now, bad)

    def test_the_acceptance_gate_exit_code_never_reads_no_data_as_a_pass(self):
        import test_hp1_acceptance as gate   # the gate the cut reads; its mapping is judged here, outside its own file

        class Result(object):
            def __init__(self, ok, failures=(), errors=()):
                self.ok, self.failures, self.errors = ok, list(failures), list(errors)

            def wasSuccessful(self):
                return self.ok
        no_data = ("case", "Traceback\nAssertionError: NO-DATA: not recorded yet")
        red = ("case", "Traceback\nAssertionError: FAIL: 2 refusal(s)")
        self.assertEqual(gate.exit_code(Result(True)), 0)
        self.assertEqual(gate.exit_code(Result(False, [no_data])), 2)
        self.assertEqual(gate.exit_code(Result(False, [no_data, no_data])), 2)
        self.assertEqual(gate.exit_code(Result(False, [no_data, red])), 1)
        self.assertEqual(gate.exit_code(Result(False, [red])), 1)
        self.assertEqual(gate.exit_code(Result(False, [no_data], [("case", "Traceback\nOSError")])), 1)
        self.assertEqual(gate.exit_code(Result(False)), 1)

    def test_missing_probe_is_no_data(self):
        every = list(self.fx.entries)
        self.fx.entries = [e for e in every if not (e["row"]["host"] == "claude" and e["row"]["probe"] == "guarded_write")]
        self.assertNoData("claude", "guarded_write")
        self.fx.entries = [e for e in every if e["row"]["probe"] != "post_invocation"]
        self.assertNoData("antigravity", "post_invocation")

    # the owner ruling of 2026-10-04 hp1-codex-door: Codex door and verb read NO-DATA by ruling ---------------------------

    def without(self, host, *probes):
        self.fx.entries = [e for e in self.fx.entries if not (e["row"]["host"] == host and e["row"]["probe"] in probes)]

    def test_codex_door_and_verb_read_no_data_by_ruling_never_a_pass_never_a_block(self):
        """GREEN for Codex rests on three legs, never on a ruled probe: a SessionStart row, a PreToolUse row
        cross-checked against its own turn, and the transcript showing the door answered. Missing any one: NO-DATA."""
        self.without("codex", "door", "verb")
        note = "codex door and verb: NO-DATA by %s, not judged, never a pass" % verifier.RULED_NO_DATA_REF["codex"]
        line = self.assertGreen()
        self.assertIn(note, line)
        self.assertIn("hp1-codex-door", line)
        self.assertIn("codex door transcript answered", line)
        code, line = self.scoped(verifier.DECIDING_HOSTS_1_1_0)
        self.assertEqual((code, line[:6]), (0, "GREEN:"), line)
        self.assertIn(note, line)
        every = list(self.fx.entries)
        self.fx.entries = [e for e in every if not (e["row"]["host"] == "codex" and e["row"]["event"] == "SessionStart")]
        self.assertNoData("codex run run-codex-2 has no SessionStart row")                 # leg 1 missing
        self.fx.entries = [e for e in every if not (e["row"]["host"] == "codex" and e["row"]["event"] == "PreToolUse")]
        self.assertNoData("codex run run-codex-2 has no guarded_write row")               # leg 2 missing
        self.fx.entries = every
        self.fx.find("codex", "other")["row"].pop("door_turn")                              # leg 3 missing
        self.assertNoData("no transcript shows the door answered")

    def test_a_codex_pretooluse_row_from_another_session_is_no_data(self):
        """The Codex cross-check: the hook stdin's session_id must be the thread its own turn's stream started."""
        entry = self.fx.find("codex", "guarded_write")
        payload = json.loads(entry["raw_in"].decode("utf-8"))
        payload["session_id"] = "thread-elsewhere"
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.assertNoData("codex", "the PreToolUse row's session_id is not its turn's thread_id")

    def test_a_codex_turn_that_starts_no_thread_is_no_data(self):
        self.set_turn("codex", "guarded_write", stdout=TURN_OK.replace(b', "thread_id": "thread-write"', b""))
        self.assertNoData("codex", "the PreToolUse row's session_id is not its turn's thread_id")

    def codex_ids(self, session_id, thread_id):
        entry = self.fx.find("codex", "guarded_write")
        payload = json.loads(entry["raw_in"].decode("utf-8"))
        payload["session_id"] = session_id
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.set_turn("codex", "guarded_write", stdout=TURN_OK.replace(
            b'"thread_id": "thread-write"', b'"thread_id": ' + json.dumps(thread_id).encode()))

    def test_an_empty_session_never_matches_an_empty_thread(self):
        self.codex_ids("", "")
        self.assertNoData("codex", "the PreToolUse row's session_id is not its turn's thread_id")

    def test_a_session_id_that_is_not_text_never_matches(self):
        self.codex_ids(7, 7)
        self.assertNoData("codex", "the PreToolUse row's session_id is not its turn's thread_id")

    def test_only_the_thread_started_line_names_the_thread(self):
        other = b'{"type": "item.started", "thread_id": "thread-write"}\n'
        self.set_turn("codex", "guarded_write", stdout=other + TURN_OK.replace(b"thread-write", b"thread-other"))
        self.assertNoData("codex", "the PreToolUse row's session_id is not its turn's thread_id")

    def test_a_claude_code_pretooluse_row_from_another_session_is_no_data(self):
        """Leg 2 on Claude Code: the hook stdin's session_id must be the session_id its own turn's init line names."""
        entry = self.fx.find("claude", "guarded_write")
        payload = json.loads(entry["raw_in"].decode("utf-8"))
        payload["session_id"] = "cc-elsewhere"
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.assertNoData("claude", "the PreToolUse row's session_id is not its turn's thread_id")
        payload["session_id"] = "cc-write"
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.set_turn("claude", "guarded_write", stdout=STREAM_OK.replace(b', "session_id": "cc-write"', b""))
        self.assertNoData("claude", "the PreToolUse row's session_id is not its turn's thread_id")
        self.set_turn("claude", "guarded_write", stdout=STREAM_OK.replace(b'"init"', b'"status"'))
        self.assertNoData("claude", "the PreToolUse row's session_id is not its turn's thread_id")

    def test_a_door_transcript_of_another_session_is_no_data(self):
        self.door_turn(stdout=DOOR_OK.replace(b"thread-door", b"thread-elsewhere"))
        self.assertNoDoor("the SessionStart row's session_id is not the door turn's thread_id")

    # the owner ruling of 2026-10-05 hp1-claude-door: Claude Code door and verb read NO-DATA by ruling, as on Codex ----

    def cc_start(self):
        return next(e for e in self.fx.entries if e["row"]["host"] == "claude" and e["row"]["event"] == "SessionStart")

    def cc_load(self):
        return next(e for e in self.fx.entries if e["row"]["host"] == "claude" and e["row"]["event"] == "PreToolUse"
                    and e["row"]["probe"] == "other")

    def cc_ruled(self):
        """The Claude Code run as the real host records it: no door or verb row, and a stream that never saw one."""
        self.without("claude", "door", "verb")
        self.fx.entries = [e for e in self.fx.entries if not (e["row"]["host"] == "claude"
                                                              and e["row"]["event"] == "UserPromptSubmit")]
        stream = b"".join(json.dumps({"event": e["row"]["event"]}).encode() + b"\n"
                          for e in self.fx.entries if e["row"]["host"] == "claude")
        write(os.path.join(self.fx.evidence_dir, "claude-stream.jsonl"), stream)
        for e in self.fx.entries:
            if e["row"]["host"] == "claude":
                e["row"].update({"stream_path": "claude-stream.jsonl", "stream_sha256": sha(stream)})

    def cc_door(self, code=0, stdout=CC_DOOR_OK, argv=None):
        self.cc_ruled()
        self.cc_start()["row"]["door_turn"] = self.fx.envelope("run-claude-2-door.json", argv or self.fx.claude_argv(),
                                                               code, stdout)

    def assertNoCcDoor(self, *words):
        line = self.assertNoData("claude run run-claude-2: no transcript shows the door answered", *words)
        self.assertNotIn("claude: GREEN", line)

    def test_claude_code_door_and_verb_read_no_data_by_ruling_on_three_legs(self):
        self.cc_ruled()
        line = self.assertGreen()
        self.assertIn("claude door and verb: NO-DATA by %s, not judged, never a pass" % verifier.RULED_NO_DATA_REF["claude"],
                      line)
        self.assertIn("claude door transcript answered", line)
        every = list(self.fx.entries)
        self.fx.entries = [e for e in every if e is not self.cc_start()]                   # leg 1 missing
        self.assertNoData("claude run run-claude-2 has no SessionStart row")
        self.fx.entries = [e for e in every if not (e["row"]["host"] == "claude" and e["row"]["event"] == "PreToolUse")]
        self.assertNoData("claude run run-claude-2 has no guarded_write row")             # leg 2 missing
        self.fx.entries = every
        self.cc_start()["row"].pop("door_turn")                                              # leg 3 missing
        self.assertNoCcDoor("no door_turn envelope")

    def test_a_claude_code_door_miss_with_no_legs_is_never_green(self):
        self.cc_ruled()
        self.fx.entries = [e for e in self.fx.entries if not (e["row"]["host"] == "claude" and e["row"]["probe"] == "other")]
        line = self.assertNoData("claude run run-claude-2 has no SessionStart row")
        self.assertNotIn("claude: GREEN", line)

    def test_a_claude_code_door_turn_that_was_not_signed_in_is_no_data(self):
        self.cc_door(stdout=CC_DOOR_OK.replace(b'"is_error": false', b'"is_error": true'))   # the 2026-10-04 shape
        self.assertNoCcDoor("the door did not route to the status verb")
        self.cc_door(stdout=CC_DOOR_OK.replace(b'"is_error": false, ', b""))
        self.assertNoCcDoor("the door did not route to the status verb")
        self.cc_door(stdout=CC_DOOR_OK.replace(b"**NO-DATA**", b"nothing"))
        self.assertNoCcDoor("the door did not route to the status verb")
        self.cc_door(stdout=CC_DOOR_OK.replace(b'"type": "result"', b'"type": "assistant"'))
        self.assertNoCcDoor("the door did not route to the status verb")

    def test_a_claude_code_door_turn_of_another_shape_or_session_is_no_data(self):
        self.cc_door(argv=self.fx.claude_argv(prompt=proof.VERB_SENTENCE))
        self.assertNoCcDoor("stream-json` turn of the door sentence")
        self.cc_door(argv=[a for a in self.fx.claude_argv() if a not in ("--output-format", "stream-json")])
        self.assertNoCcDoor("stream-json` turn of the door sentence")
        self.cc_door(code=1)
        self.assertNoCcDoor("the door turn exited 1")
        self.cc_door(stdout=CC_DOOR_OK.replace(b'"init", "session_id": "cc-door"', b'"init", "session_id": "cc-x"'))
        self.assertNoCcDoor("the SessionStart row's session_id is not the door turn's thread_id")
        self.cc_door(stdout=CC_DOOR_OK.replace(b', "usage": {"input_tokens": 9}', b""))
        self.assertNoCcDoor("no JSON line with a usage key")

    def test_a_claude_code_plugin_dir_that_is_not_the_recorded_root_is_no_data(self):
        self.cc_door(argv=self.fx.claude_argv(plugin_dir=self.fx.install_root()))
        self.assertNoCcDoor("the door turn's --plugin-dir")
        self.cc_door(argv=self.fx.claude_argv() [:-1] + ["--plugin-dir", self.fx.roots["claude"], proof.DOOR_SENTENCE])
        self.assertNoCcDoor("the door turn's --plugin-dir")   # named twice: never guess which one loaded
        self.cc_door(argv=self.fx.claude_argv(plugin_dir=self.fx.roots["claude"] + "/"))
        self.cc_start()["row"]["plugin_root"] = self.fx.roots["claude"] + "/"
        self.assertNoCcDoor("the door turn's --plugin-dir")   # equal but not normal

    def cc_root(self, root, argv_root):
        """The Claude Code SessionStart row recorded under root (a folder its hook script still lies under), its door
        turn naming argv_root as the --plugin-dir, and the door skill load read under argv_root."""
        self.cc_door(argv=self.fx.claude_argv(plugin_dir=argv_root))
        for e in (self.cc_start(), self.cc_load()):
            e["row"]["plugin_root"] = root
        payload = json.loads(self.cc_load()["raw_in"].decode("utf-8"))
        payload["tool_input"] = {"command": "cat %s" % self.fx.door_skill(root=argv_root)}
        self.cc_load()["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")

    def test_a_claude_code_plugin_dir_the_witness_did_not_record_is_no_data(self):
        self.cc_root(os.path.join(self.fx.roots["claude"], "runtime"), self.fx.roots["claude"])
        self.assertNoCcDoor("the door turn's --plugin-dir")

    def test_a_claude_code_plugin_dir_that_is_not_the_candidate_bundle_is_no_data(self):
        inner = os.path.join(self.fx.roots["claude"], "runtime")
        for name in verifier.DOOR_SKILLS:   # door skills planted there, outside what the candidate ships as the plugin
            write(self.fx.door_skill(name, root=inner), self.fx.files["bundle/skills/%s/SKILL.md" % name][1])
        self.addCleanup(shutil.rmtree, os.path.join(inner, "skills"), True)
        self.cc_root(inner, inner)
        line = self.judge(self.fx.write_evidence())[1]
        self.assertFalse(line.startswith("GREEN"), line)
        self.assertNotIn("claude: GREEN", line)
        self.assertTrue("the door turn's --plugin-dir" in line or "dirty" in line, line)

    def test_a_claude_code_door_skill_edited_on_disk_is_red_dirty(self):
        """Claude Code installs nothing: the --plugin-dir is the candidate's bundle, so an edited door skill is a
        dirty candidate, RED before any leg is read."""
        self.cc_door()
        write(self.fx.door_skill(root=self.fx.roots["claude"]), b"edited\n")
        self.assertRed("dirty", "bundle/skills/brother-status/SKILL.md")

    def test_a_claude_code_candidate_without_a_door_skill_is_no_data(self):
        self.cc_door()
        for name in verifier.DOOR_SKILLS:
            with mock.patch.object(verifier._Candidate, "blob", lambda self, rel, _n=name, _real=verifier._Candidate.blob:
                                   None if rel == "bundle/skills/%s/SKILL.md" % _n else _real(self, rel)):
                self.assertNoCcDoor("the door turn's --plugin-dir")

    def test_a_claude_code_door_skill_load_elsewhere_is_no_data(self):
        for command, sid in (("cat %s" % self.fx.door_skill(root=self.fx.roots["claude"]), "cc-write"),
                             ("cat %s" % self.fx.door_skill(), "cc-door"),
                             ("cat %s" % self.fx.door_skill("brothersbe-status", root=self.fx.roots["claude"]), "cc-door")):
            self.cc_door()
            entry = self.cc_load()
            payload = json.loads(entry["raw_in"].decode("utf-8"))
            payload.update({"session_id": sid, "tool_input": {"command": command}})
            entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.assertNoCcDoor("no PreToolUse row in the door's session loads")

    def test_codex_owes_a_session_start_row(self):
        self.without("codex", "door", "verb")
        start = self.fx.find("codex", "other")
        self.fx.entries.remove(start)
        self.assertNoData("codex run run-codex-2 has no SessionStart row")
        self.fx.entries.append(start)
        start["row"]["event"] = "Stop"   # an other row of another event is not a SessionStart row
        payload = json.loads(start["raw_in"].decode("utf-8"))
        payload["hook_event_name"] = "Stop"
        start["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.assertNoData("codex run run-codex-2 has no SessionStart row")

    # the ruling's GREEN rests on the transcript showing the door answered: each condition alone is NO-DATA ------------

    def door_turn(self, name="run-codex-2-door.json", code=0, stdout=DOOR_OK, prompt=None, drop=()):
        """The Codex SessionStart row's door_turn, rewritten with exactly one condition changed."""
        argv = [a for a in [self.fx.bins["codex"], "exec", "--json", "--ephemeral", "-C", self.fx.workspace,
                            prompt or proof.DOOR_SENTENCE] if a not in drop]
        self.without("codex", "door", "verb")
        self.fx.find("codex", "other")["row"]["door_turn"] = self.fx.envelope(name, argv, code, stdout)

    def assertNoDoor(self, *words):
        line = self.assertNoData("codex run run-codex-2: no transcript shows the door answered", *words)
        self.assertNotIn("codex: GREEN", line)

    def test_the_door_transcript_that_answered_is_green(self):
        self.door_turn()
        line = self.assertGreen()
        self.assertIn("codex door transcript answered", line)

    def test_no_door_transcript_is_no_data(self):
        self.door_turn()
        self.fx.find("codex", "other")["row"].pop("door_turn")
        self.assertNoDoor("no door_turn envelope")

    def test_a_door_transcript_of_another_run_is_no_data(self):
        self.door_turn(name="run-codex-1-door.json")
        self.assertNoDoor("not this run's door transcript")

    def test_an_altered_door_transcript_is_no_data(self):
        self.door_turn()
        write(os.path.join(self.fx.evidence_dir, "run-codex-2-door.json"), b"{}")
        self.assertNoDoor("does not hash")

    def test_a_turn_of_another_sentence_is_no_data(self):
        self.door_turn(prompt=proof.VERB_SENTENCE)
        self.assertNoDoor("turn of the door sentence")

    def test_a_turn_that_is_not_codex_exec_json_is_no_data(self):
        self.door_turn(drop=("--json",))
        self.assertNoDoor("turn of the door sentence")

    def test_a_turn_that_is_not_codex_exec_is_no_data(self):
        self.door_turn(drop=("exec",))
        self.assertNoDoor("turn of the door sentence")

    def test_a_failed_door_turn_is_no_data(self):
        self.door_turn(code=1)
        self.assertNoDoor("the door turn exited 1")

    def test_a_door_turn_with_no_usage_is_no_data(self):
        self.door_turn(stdout=ANSWER)
        self.assertNoDoor("no JSON line with a usage key")

    def test_a_door_turn_with_no_answer_is_no_data(self):
        self.door_turn(stdout=TURN_OK)
        self.assertNoDoor("the door did not route to the status verb")

    def test_an_empty_answer_is_no_answer(self):
        self.door_turn(stdout=DOOR_OK.replace(b"Brother status: **NO-DATA**, nothing recorded yet.", b"  "))
        self.assertNoDoor("the door did not route to the status verb")

    def test_a_non_message_item_is_no_answer(self):
        self.door_turn(stdout=DOOR_OK.replace(b'"agent_message"', b'"reasoning"'))
        self.assertNoDoor("the door did not route to the status verb")

    def test_an_answer_outside_a_completed_item_is_no_answer(self):
        self.door_turn(stdout=DOOR_OK.replace(b'"item.completed"', b'"item.started"'))
        self.assertNoDoor("the door did not route to the status verb")

    def test_one_session_start_row_with_the_answer_is_enough(self):
        self.door_turn()
        original = self.fx.find("codex", "other")["row"]
        extra = self.fx.entry("codex", "other", "SessionStart", "tools/start.py", [],
                              {"hook_event_name": "SessionStart", "source": "startup", "session_id": "s2"},
                              b"", 0, ("allow", "silent_exit_0"))
        for key in ("model_turn", "signed_in"):
            extra["row"].pop(key)   # a second SessionStart row with no transcript
        self.fx.entries.insert(0, extra)
        self.assertGreen()
        original.pop("door_turn")   # now neither row carries one
        self.assertNoDoor()

    # leg 3 proves the door ROUTED, not that Codex replied ------------------------------------------------------------

    def answer(self, text):
        """The door turn rewritten with one agent message of this text."""
        self.door_turn(stdout=DOOR_OK.replace(b"Brother status: **NO-DATA**, nothing recorded yet.",
                                              json.dumps(text)[1:-1].encode("utf-8")))

    def load(self):
        return next(e for e in self.fx.entries if e["row"]["host"] == "codex" and e["row"]["event"] == "PreToolUse"
                    and e["row"]["probe"] == "other")

    def set_load(self, command=None, session_id="thread-door"):
        entry = self.load()
        payload = json.loads(entry["raw_in"].decode("utf-8"))
        payload.update({"session_id": session_id, "tool_input": {"command": command or "cat %s" % self.fx.door_skill()}})
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")

    def test_replies_that_did_not_route_are_no_data(self):
        for text in ("I do not know what Brother is.", "Error: no skill named brother.", "ok"):
            self.answer(text)
            self.assertNoDoor("the door did not route to the status verb")

    def test_a_routed_answer_carries_any_verdict_word(self):
        for text in ("Status: PASS, every check green.", "FAIL: two units red.", "Brother reports **NO-DATA**."):
            self.answer(text)
            self.assertGreen()

    def test_a_verdict_word_inside_another_word_is_no_verdict(self):
        for text in ("PASSWORD reset needed.", "FAILED-OVER", "SURPASS"):
            self.answer(text)
            self.assertNoDoor("the door did not route to the status verb")

    def test_no_door_skill_load_is_no_data(self):
        self.door_turn()
        self.fx.entries.remove(self.load())
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_a_skill_load_in_another_session_is_no_data(self):
        self.door_turn()
        self.set_load(session_id="thread-write")
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_a_skill_outside_the_installed_plugin_root_is_no_data(self):
        self.door_turn()
        self.set_load("cat %s" % self.fx.door_skill(root=os.path.join(self.fx.real, "bundle")))
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_a_skill_that_is_not_a_door_skill_is_no_data(self):
        self.door_turn()
        self.set_load("cat %s" % self.fx.door_skill("brothersbe-status"))
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_the_shared_door_skill_counts_as_routed(self):
        self.door_turn()
        self.set_load("cat %s" % self.fx.door_skill("using-brother"))
        self.assertGreen()

    # the load is parsed and compared exactly, never searched for: one fixture per probe of the 2026-10-04 review -----

    def assertNotLoaded(self, command):
        self.door_turn()
        self.set_load(command)
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_dotdot_in_place_of_the_version_folder_is_no_load(self):
        self.assertNotLoaded("cat %s/../skills/brother-status/SKILL.md" % os.path.dirname(self.fx.install_root()))

    def test_the_root_as_a_substring_of_another_path_is_no_load(self):
        self.assertNotLoaded("cat /tmp/evil%s" % self.fx.door_skill())

    def test_a_command_that_reads_nothing_is_no_load(self):
        self.assertNotLoaded("echo %s" % self.fx.door_skill())

    def test_a_longer_file_name_is_no_load(self):
        self.assertNotLoaded("cat %s.bak" % self.fx.door_skill())

    def test_an_escape_after_the_path_is_no_load(self):
        self.assertNotLoaded("cat %s/../../../../../../../../tmp/fake" % self.fx.door_skill())

    def test_a_dotdot_that_normalises_back_to_the_skill_is_still_no_load(self):
        self.assertNotLoaded("cat %s/skills/x/../brother-status/SKILL.md" % self.fx.install_root())

    def test_a_second_command_or_pipe_is_no_load(self):
        for tail in ("; rm -rf /tmp/x", " | sh", " > /tmp/copy"):
            self.assertNotLoaded("cat %s%s" % (self.fx.door_skill(), tail))

    def test_two_files_in_one_read_is_no_load(self):
        self.assertNotLoaded("cat %s %s" % (self.fx.door_skill(), self.fx.door_skill("using-brother")))

    def test_an_unparseable_command_is_no_load(self):
        self.assertNotLoaded("cat '%s" % self.fx.door_skill())

    def test_a_relative_path_is_no_load(self):
        self.assertNotLoaded("cat skills/brother-status/SKILL.md")

    def test_a_command_that_is_not_text_is_no_load(self):
        self.door_turn()
        entry = self.load()
        payload = json.loads(entry["raw_in"].decode("utf-8"))
        payload["tool_input"] = {"command": ["cat", self.fx.door_skill()]}
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_wrong_options_to_a_read_verb_are_no_load(self):
        for command in ("head -c 10 %s", "head -n x %s", "sed -e 1p %s", "sed -n 1,2d %s", "head - %s",
                        "tail -n 40 %s", "tail -40 %s", "awk -n 1p %s", "less %s"):
            self.assertNotLoaded(command % self.fx.door_skill())

    def test_a_tool_input_that_is_not_an_object_is_no_load(self):
        self.door_turn()
        entry = self.load()
        payload = json.loads(entry["raw_in"].decode("utf-8"))
        payload["tool_input"] = "cat %s" % self.fx.door_skill()
        entry["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.assertNoDoor("no PreToolUse row in the door's session loads")

    def test_a_path_that_normalises_to_the_skill_without_dotdot_loads(self):
        root = self.fx.install_root()
        for path in (root + "/skills//brother-status/SKILL.md", root + "/./skills/brother-status/SKILL.md"):
            self.door_turn()
            self.set_load("cat %s" % path)
            self.assertGreen()

    def test_every_named_read_form_of_the_exact_path_loads(self):
        skill = self.fx.door_skill()
        for command in ('cat "%s"', "cat '%s'", "cat %s", "head %s", "head -n 40 %s", "head -40 %s",
                        "sed -n '1,80p' %s", "sed -n 5p %s"):
            self.door_turn()
            self.set_load(command % skill)
            self.assertGreen()

    def install(self, value):
        self.door_turn()
        row = self.fx.find("codex", "other")["row"]
        if value is None:
            row.pop("plugin_install")
        else:
            row["plugin_install"] = value

    def test_no_recorded_install_is_no_data(self):
        self.install(None)
        self.assertNoDoor("recorded no installed plugin root")

    def test_a_recorded_install_outside_this_codex_home_is_no_data(self):
        for value in (os.path.join(self.fx.real, "bundle"), "/tmp/evil" + self.fx.install_root(),
                      os.path.dirname(self.fx.install_root())):
            self.install(value)
            self.assertNoDoor("recorded no installed plugin root")

    def test_a_recorded_install_that_is_not_a_normal_absolute_path_is_no_data(self):
        root = self.fx.install_root()
        cache = os.path.dirname(root)
        for value in ("plugins/cache/brother/brother/1.1.0", root + "/", cache + "//1.1.0", cache + "/..", 7):
            self.install(value)
            self.assertNoDoor("recorded no installed plugin root")

    # the recorded install is bound to disk: its digest must match the candidate's door skills at that very root -----

    def test_a_row_edited_to_name_another_version_is_no_data(self):
        self.door_turn()
        other = os.path.join(os.path.dirname(self.fx.install_root()), "9.9.9")
        self.fx.find("codex", "other")["row"]["plugin_install"] = other
        self.set_load("cat %s" % self.fx.door_skill(root=other))   # the load edited to match, the digest not
        self.assertNoDoor("bound to the candidate's door skills")

    def test_no_install_digest_is_no_data(self):
        self.install(self.fx.install_root())
        self.fx.find("codex", "other")["row"].pop("plugin_install_sha256")
        self.assertNoDoor("bound to the candidate's door skills")

    def test_an_install_whose_skills_are_not_the_candidates_is_no_data(self):
        self.install(self.fx.install_root())
        shas = dict((name, sha(b"an edited install of " + name.encode())) for name in verifier.DOOR_SKILLS)
        self.fx.find("codex", "other")["row"]["plugin_install_sha256"] = proof.install_digest(self.fx.install_root(), shas)
        self.assertNoDoor("bound to the candidate's door skills")

    def test_a_candidate_without_a_door_skill_is_no_data(self):
        self.install(self.fx.install_root())
        real = verifier._Candidate.blob
        with mock.patch.object(verifier._Candidate, "blob", lambda facts, rel: None if rel.startswith(
                "bundle/skills/using-brother/") else real(facts, rel)):
            self.assertNoDoor("bound to the candidate's door skills")

    # the install is read on disk at judging time: one condition per fixture ------------------------------------------

    NOT_ON_DISK = "present on disk as the candidate's install"

    def test_an_install_root_missing_from_disk_is_no_data(self):
        self.door_turn()
        shutil.rmtree(self.fx.install_root())
        self.assertNoDoor(self.NOT_ON_DISK)

    def test_an_install_root_reached_through_a_link_is_no_data(self):
        self.door_turn()
        elsewhere = os.path.join(self.tmp, "workspace-copy")
        shutil.move(self.fx.install_root(), elsewhere)
        os.symlink(elsewhere, self.fx.install_root())
        self.assertNoDoor(self.NOT_ON_DISK)

    def test_an_install_folder_that_is_not_the_candidates_version_is_no_data(self):
        other = os.path.join(os.path.dirname(self.fx.install_root()), "9.9.9")
        shutil.copytree(self.fx.install_root(), other)   # on disk, byte equal, digest right: only the version is wrong
        self.door_turn()
        row = self.fx.find("codex", "other")["row"]
        row.update({"plugin_install": other, "plugin_install_sha256": self.fx.install_sha(other)})
        self.set_load("cat %s" % self.fx.door_skill(root=other))
        self.assertNoDoor(self.NOT_ON_DISK)

    def test_a_faithful_install_outside_this_codex_home_is_no_data(self):
        other = os.path.join(self.tmp, "elsewhere", "1.1.0")   # real, version named, byte equal, digest right
        shutil.copytree(self.fx.install_root(), other)
        self.door_turn()
        row = self.fx.find("codex", "other")["row"]
        row.update({"plugin_install": other, "plugin_install_sha256": self.fx.install_sha(other)})
        self.set_load("cat %s" % self.fx.door_skill(root=other))
        self.assertNoDoor("recorded no installed plugin root")

    def test_an_edited_door_skill_on_disk_is_no_data(self):
        self.door_turn()
        write(self.fx.door_skill("using-brother"), b"edited after the install\n")
        self.assertNoDoor(self.NOT_ON_DISK)

    def test_a_door_skill_on_disk_reached_through_a_link_is_no_data(self):
        self.door_turn()
        skill = self.fx.door_skill()
        copy = write(os.path.join(self.tmp, "skill-copy.md"), read(skill))   # the same bytes, through a link
        os.unlink(skill)
        os.symlink(copy, skill)
        self.assertNoDoor(self.NOT_ON_DISK)

    def test_a_door_skill_missing_from_disk_is_no_data(self):
        self.door_turn()
        os.unlink(self.fx.door_skill("using-brother"))
        self.assertNoDoor(self.NOT_ON_DISK)

    def test_a_candidate_with_no_readable_codex_version_is_no_data(self):
        self.door_turn()
        with mock.patch.object(verifier._Candidate, "plugin_version", lambda facts: None):
            self.assertNoDoor(self.NOT_ON_DISK)

    def test_the_candidates_codex_version_is_read_from_its_manifest(self):
        facts = verifier._Candidate(self.fx.root)
        self.assertEqual(facts.plugin_version(), "1.1.0")
        for data in (None, b"not json", b"[]", b'{"version": 7}', b'{"version": ""}', b'{"name": "brother"}'):
            with mock.patch.object(verifier, "_blob_bytes", lambda store, tree, rel, data=data: data):
                self.assertIsNone(facts.plugin_version(), data)

    # the evidence is bound to its collection: the collector's manifest is checked last --------------------------------

    def test_evidence_with_no_manifest_is_no_data(self):
        self.fx.write_evidence()
        os.unlink(self.fx.evidence + proof.MANIFEST_SUFFIX)
        code, line = self.judge()
        self.assertEqual((code, line[:8]), (3, "NO-DATA:"), line)
        self.assertIn("has no manifest", line)

    def test_a_row_edited_after_collection_is_no_data(self):
        self.fx.write_evidence()
        data = read(self.fx.evidence)
        write(self.fx.evidence, data.replace(b"codex 1.107.0", b"codex 1.107.1", 1))   # still a valid row
        code, line = self.judge()
        self.assertEqual((code, line[:8]), (3, "NO-DATA:"), line)
        self.assertIn("differs from the manifest the collector wrote", line)

    def test_the_deepseek_forged_run_folder_is_no_data(self):
        """2026-10-04 attack: a hand written folder (SessionStart row naming .../9.9.9 with its digest recomputed, a
        door envelope in thread S answering NO-DATA, a PreToolUse row cat-ing that path), no Codex involved."""
        forged = os.path.join(os.path.dirname(self.fx.install_root()), "9.9.9")   # never installed: not on disk
        self.door_turn()
        row = self.fx.find("codex", "other")["row"]
        row.update({"plugin_install": forged, "plugin_install_sha256": self.fx.install_sha(forged)})
        self.set_load("cat %s" % self.fx.door_skill(root=forged))
        self.fx.write_evidence()
        os.unlink(self.fx.evidence + proof.MANIFEST_SUFFIX)   # written by hand, not by the collector
        code, line = self.judge()
        self.assertEqual((code, line[:8]), (3, "NO-DATA:"), line)
        self.assertNotIn("codex: GREEN", line)

    def test_a_refused_skill_load_is_no_data(self):
        self.door_turn()
        entry = self.load()
        entry["raw_out"] = b'{"decision": "block", "reason": "NO-DATA"}'
        entry["row"].update({"verdict": "deny", "verdict_source": "json_deny"})
        self.assertNoDoor("a hook refused the door skill load")

    def test_a_skill_load_row_not_launched_by_the_host_is_red(self):
        self.door_turn()
        row = self.load()["row"]
        wrapper = (os.path.realpath(sys.executable), "%s /tmp/model-wrapper.py" % sys.executable)
        row["parent_chain"] = self.fx.chain("codex", row["witness_argv"], [wrapper])
        self.assertRed("codex", "(PreToolUse)", "not host launched")

    def test_an_other_row_of_another_event_is_not_judged(self):
        self.door_turn()
        stop = self.fx.entry("codex", "other", "Stop", "tools/start.py", [], {"hook_event_name": "Stop",
                             "session_id": "thread-door"}, b"", 0, ("allow", "silent_exit_0"))
        for key in ("model_turn", "signed_in"):
            stop["row"].pop(key)
        wrapper = (os.path.realpath(sys.executable), "%s /tmp/model-wrapper.py" % sys.executable)
        stop["row"]["parent_chain"] = self.fx.chain("codex", stop["row"]["witness_argv"], [wrapper])
        self.fx.entries.append(stop)
        line = self.assertGreen()   # counted and ignored, as before the ruling
        self.assertIn("ignored 1 row(s) labelled other", line)

    def test_the_door_skills_ship_in_the_bundle(self):
        for name in verifier.DOOR_SKILLS:
            self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(HERE), "bundle", "skills", name, "SKILL.md")))

    def test_the_door_transcript_is_owed_only_while_the_door_row_is_missing(self):
        self.fx.find("codex", "other")["row"].pop("door_turn")
        line = self.assertGreen()   # the Codex door and verb rows arrived: their own turns are judged instead
        self.assertNotIn("door transcript", line)

    def test_the_codex_session_start_row_is_judged_like_a_probe(self):
        self.without("codex", "door", "verb")
        row = self.fx.find("codex", "other")["row"]
        harness = (os.path.realpath(sys.executable), "%s tests/e2e/codex/run_e2e.py" % sys.executable)
        good = row["parent_chain"]
        chain = self.fx.chain("codex", row["witness_argv"], [harness])
        chain[-1].update({"exe": "/sbin/launchd", "comm": "launchd", "argv": "/sbin/launchd"})
        row["parent_chain"] = chain
        self.assertRed("codex", "(SessionStart)", "not host originated")
        row["parent_chain"] = good
        row["recorded_at"] = stamp(self.now - 3700)   # before the last plugin commit
        self.assertRed("codex", "stale")
        row["recorded_at"] = stamp(self.now - 600)
        row["script_sha256"] = sha(b"an edited session start hook")
        self.assertRed("codex", "committed blob")
        row["script_sha256"] = sha(self.fx.files["products/brothermode/tools/start.py"][1])
        self.assertGreen()

    # REQ-HP1-FRESH --------------------------------------------------------------------------------------------------------

    def test_stale_tree_is_red(self):
        for e in self.fx.entries:
            e["row"]["plugin_tree_sha256"] = sha(b"the candidate before the last plugin commit")
        self.assertRed("stale", "plugin_tree_sha256")

    def test_row_older_than_last_commit_is_red(self):
        self.row("codex", "verb")["recorded_at"] = stamp(self.now - 3700)   # the plugin commit is now - 3600
        self.assertRed("codex", "stale", "before the last commit")
        self.row("codex", "verb")["recorded_at"] = stamp(self.now - 3500)
        self.assertGreen()

    def test_future_clock_is_red(self):
        self.row("antigravity", "door")["recorded_at"] = stamp(self.now + 301 + 60)
        self.assertRed("antigravity", "future")
        self.row("antigravity", "door")["recorded_at"] = stamp(self.now + 200)   # within the 300 s slack
        self.assertGreen()

    def test_two_candidates_mixed_is_red(self):
        for row in self.rows_of("codex"):
            row["plugin_tree_sha256"] = sha(b"another candidate")
        self.assertRed("codex: RED: codex line", "stale: plugin_tree_sha256")   # judged per host, each bound to the candidate
        self.row("codex", "door")["plugin_tree_sha256"] = self.fx.tree_hash   # one run of one host at two candidates
        self.assertRed("run run-codex-2 carries 2 candidates")

    def test_newest_run_is_judged(self):
        older = []
        for e in self.fx.entries:
            if e["row"]["host"] == "codex":
                copy = {"row": json.loads(json.dumps(e["row"])), "raw_in": e["raw_in"], "raw_out": e["raw_out"]}
                copy["row"].update({"run_id": "run-codex-1", "recorded_at": stamp(self.now - 900)})
                copy["row"].pop("signed_in", None)   # an older run that was not signed in is ignored, not judged
                older.append(copy)
        self.fx.entries = older + self.fx.entries
        line = self.assertGreen()
        self.assertIn("ignored 5 row(s) of older runs", line)
        newest = [e for e in self.fx.entries if e["row"]["run_id"] == "run-codex-2"
                  and e["row"]["probe"] != "guarded_write"]
        for e in newest:
            e["row"]["recorded_at"] = stamp(self.now - 300)
        self.fx.entries = [e for e in self.fx.entries if e["row"]["run_id"] != "run-codex-2"] + newest
        self.assertNoData("codex run run-codex-2 has no guarded_write row")

    def test_no_plugin_history_is_no_data(self):
        rest = dict((k, v) for k, v in self.fx.files.items() if k.split("/")[0] not in ("plugin", "bundle", "products"))
        self.fx.repo.commit(rest, self.now - 1200, on_disk=False)
        self.assertNoData("plugin, bundle or products")
        with self.assertRaises(verifier.VerifyNoData):
            verifier.tree_sha256(self.fx.root)
        bare = GitRepo(os.path.join(self.tmp, "no-plugin-ever"))
        bare.commit({"README.md": ("100644", b"nothing shipped\n")}, self.now - 50)
        for fn in (verifier.tree_sha256, verifier.last_plugin_commit_time):
            with self.assertRaises(verifier.VerifyNoData):
                fn(bare.root)

    def test_git_absent_is_no_data(self):
        shutil.rmtree(os.path.join(self.fx.root, ".git"))
        self.assertNoData("no readable git directory")
        for fn in (verifier.tree_sha256, verifier.last_plugin_commit_time):
            with self.assertRaises(verifier.VerifyNoData):
                fn(self.fx.root)

    def test_tree_hash_and_commit_time_are_what_git_prints(self):
        self.assertEqual(verifier.tree_sha256(self.fx.root), self.fx.tree_hash)
        self.assertEqual(verifier.last_plugin_commit_time(self.fx.root), self.now - 3600)   # the README commit is skipped
        odd = GitRepo(os.path.join(self.tmp, "quoted"))
        odd.commit({"plugin/caf\u00e9 \"q\"\tx.txt": ("100644", b"a\n"), "plugin/b.txt": ("100755", b"b\n"),
                    "plugin/sub/c.txt": ("100644", b"c\n"), "plugin/sub.txt": ("100644", b"d\n")}, 1000, on_disk=False)
        want = ('100755 blob %s\tplugin/b.txt\n'
                '100644 blob %s\t"plugin/caf\\303\\251 \\"q\\"\\tx.txt"\n'
                '100644 blob %s\tplugin/sub.txt\n'
                '100644 blob %s\tplugin/sub/c.txt\n') % (blob_sha1(b"b\n"), blob_sha1(b"a\n"), blob_sha1(b"d\n"),
                                                          blob_sha1(b"c\n"))
        self.assertEqual(verifier.tree_sha256(odd.root), sha(want.encode("ascii")))
        self.assertEqual(verifier.last_plugin_commit_time(odd.root), 1000)

    # REQ-HP1-HOST-ORIGIN ------------------------------------------------------------------------------------------------

    def test_harness_started_hook_is_red(self):
        row = self.row("antigravity", "guarded_write")
        harness = (os.path.realpath(sys.executable), "%s tests/e2e/antigravity/run_e2e.py --real" % sys.executable)
        chain = self.fx.chain("antigravity", row["witness_argv"], [harness])
        chain[-1].update({"exe": "/sbin/launchd", "comm": "launchd", "argv": "/sbin/launchd"})   # no host anywhere
        row.update({"parent_chain": chain, "host_ancestor": True})   # a stored claim is never read
        self.assertFalse(verifier.host_ancestor(row))
        self.assertRed("antigravity", "not host originated")

    def test_a_wrapper_started_hook_is_red(self):
        row = self.row("claude", "door")
        wrapper = (os.path.realpath(sys.executable), "%s /tmp/model-wrapper.py" % sys.executable)
        row["parent_chain"] = self.fx.chain("claude", row["witness_argv"], [wrapper])
        self.assertTrue(verifier.host_ancestor(row))
        self.assertFalse(verifier.host_launched(row, row["shim_dir"]))
        self.assertRed("claude", "not host launched")
        terminal = [("/bin/zsh", "/bin/zsh -c %s" % CC_HOOKS["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]),
                    ("/bin/zsh", "/bin/zsh -l")]   # a terminal tool's shell between the hook and the host
        row["parent_chain"] = self.fx.chain("claude", row["witness_argv"], terminal)
        self.assertRed("claude", "not host launched")

    def test_a_host_shell_running_a_model_command_is_red(self):
        row = self.row("antigravity", "guarded_write")
        shipped = AG_HOOKS["brother"]["PreToolUse"][0]["hooks"][0]["command"]
        row["parent_chain"] = self.fx.chain("antigravity", row["witness_argv"], [("/bin/sh", "/bin/sh -c " + shipped)])
        self.assertTrue(verifier.host_launched(row, row["shim_dir"]))
        self.assertGreen()   # the host's own launcher shell running the shipped command verbatim
        model = "echo hp1 > /dev/null; " + shipped
        row["parent_chain"] = self.fx.chain("antigravity", row["witness_argv"], [("/bin/sh", "/bin/sh -c " + model)])
        self.assertFalse(verifier.host_launched(row, row["shim_dir"]))
        self.assertRed("antigravity", "not host launched")
        cc = self.row("claude", "guarded_write")
        command = CC_HOOKS["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        row["parent_chain"] = self.fx.chain("antigravity", row["witness_argv"])
        cc["parent_chain"] = self.fx.chain("claude", cc["witness_argv"], [
            ("/bin/sh", "/bin/sh -c " + command.replace("${CLAUDE_PLUGIN_ROOT}", self.fx.roots["claude"]))])
        self.assertGreen()   # the host expanded ${CLAUDE_PLUGIN_ROOT} itself
        shim = os.path.join(self.fx.shim_dir, "python3")
        cc["parent_chain"] = self.fx.chain("claude", cc["witness_argv"], [("/bin/sh", "/bin/sh %s x" % shim)])
        self.assertGreen()   # the driver's shim sh before the host

    # REQ-HP1-SIGNED ---------------------------------------------------------------------------------------------------

    def test_unsigned_host_is_red(self):
        login = self.row("codex", "door")["signed_in"]
        self.row("codex", "door")["signed_in"] = self.fx.envelope("codex-login-out.json", login["argv"], 1, b"Not logged in\n")
        self.assertRed("codex not signed in", "login status")
        self.row("codex", "door")["signed_in"] = login
        self.row("codex", "verb").pop("signed_in")
        self.assertRed("codex not signed in", "no signed_in envelope")
        self.row("codex", "verb")["signed_in"] = login
        post = self.fx.find("antigravity", "post_invocation")
        payload = json.loads(post["raw_in"].decode("utf-8"))
        payload["invocationNum"] = 0
        post["raw_in"] = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.assertRed("antigravity not signed in", "invocationNum")

    def test_failed_model_turn_is_red(self):
        self.set_turn("codex", "guarded_write", code=1)
        self.assertRed("codex not signed in", "failed model turn")
        self.set_turn("codex", "guarded_write")
        self.set_turn("claude", "door", code=1, stdout=STREAM_OK)
        self.assertRed("claude not signed in", "failed model turn")

    def test_usage_is_rederived_from_envelope(self):
        self.set_turn("codex", "door", stdout=b'{"type": "turn.completed"}\n{"usage_seen": true}\n',
                      extra={"usage_seen": True})
        self.row("codex", "door")["usage_seen"] = True   # the row says it saw one; the bytes say not
        self.assertRed("codex not signed in", "no JSON line with a usage key")

    def test_model_tool_call_naming_a_hook_script_is_red(self):
        call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {
            "command": "python3 %s < forged.json" % self.row("claude", "door")["script"]}}]}}
        self.set_turn("claude", "verb", stdout=json.dumps(call).encode() + b"\n" + STREAM_OK)
        self.assertRed("claude not signed in", "names the hook script door.py")

    def test_no_data_version_is_red(self):
        for version in ("no_data", "", "  "):
            self.row("codex", "door")["host_version"] = version
            self.assertRed("codex", "host_version")

    # REQ-HP1-EFFECT -------------------------------------------------------------------------------------------------------

    def flip_to_deny(self):
        """The 1.1.1 guard: the table set back to deny for Claude Code and Codex, and the fixture rebuilt to match."""
        patch = mock.patch.dict(verifier.EXPECTED_GUARDED_WRITE, {"claude": "deny", "codex": "deny"})
        patch.start()
        self.addCleanup(patch.stop)
        shutil.rmtree(self.tmp)
        os.makedirs(os.path.join(self.tmp, "home"))
        self.fx = Fixture(self.tmp, self.now)
        self.assertGreen()

    def test_unguarded_guarded_write_is_judged_per_host(self):
        """Owner ruling 2026-10-04 hp1-guarded-write: no shipped hook guards the write on Claude Code or Codex, so an
        allow is the shipped answer whatever the host's own permission system did with the file; a deny is a guard the
        product does not ship."""
        for host in ("claude", "codex"):
            self.assertEqual(verifier.EXPECTED_GUARDED_WRITE[host], "unguarded")
            entry = self.fx.find(host, "guarded_write")
            for after in (False, True):   # the file is the host's call, recorded and never credited to Brother
                entry["row"]["effect"]["exists_after"] = after
                self.assertTrue(verifier.effect_matches(self.loaded(host, "guarded_write")), (host, after))
                self.assertGreen()
            entry["raw_out"] = b'{"decision": "allow"}'
            entry["row"].update({"verdict": "allow", "verdict_source": "json_allow"})
            self.assertGreen()
            entry["raw_out"] = b'{"decision": "ask", "reason": "confirm"}'
            entry["row"].update({"verdict": "ask", "verdict_source": "json_ask"})
            self.assertRed(host, "guarded write ask", "unguarded")
            entry["raw_out"], entry["row"]["exit_code"], entry["row"]["stderr_len"] = b"", 2, 30
            entry["row"].update({"verdict": "deny", "verdict_source": "exit_2"})
            self.assertRed(host, "guarded write deny", "unguarded")
            entry["raw_out"], entry["row"]["exit_code"], entry["row"]["stderr_len"] = b"", 0, 0
            entry["row"].update({"verdict": "allow", "verdict_source": "silent_exit_0"})
            entry["row"]["effect"]["exists_before"] = True   # the target was not clean before the probe
            self.assertRed(host, "guarded write allow")
            entry["row"]["effect"]["exists_before"] = False
            self.assertGreen()
        self.assertEqual(verifier.EXPECTED_GUARDED_WRITE["antigravity"], "allow")

    def test_deny_with_written_file_is_red(self):
        self.flip_to_deny()
        row = self.row("claude", "guarded_write")
        row["effect"]["exists_after"] = True   # the hook said deny and the host wrote the file anyway
        self.assertFalse(verifier.effect_matches(self.loaded("claude", "guarded_write")))
        self.assertRed("claude", "guarded write deny")
        row["effect"]["exists_after"] = False
        self.row("codex", "guarded_write")["effect"]["exists_before"] = True   # the target was not clean
        self.assertRed("codex", "guarded write")

    def test_guarded_write_allow_is_red(self):
        self.flip_to_deny()
        entry = self.fx.find("claude", "guarded_write")
        entry["raw_out"], entry["row"]["exit_code"] = b'{"decision": "allow"}', 0
        entry["row"].update({"verdict": "allow", "verdict_source": "json_allow"})
        for after in (True, False):   # an allow is red whatever the file shows
            entry["row"]["effect"]["exists_after"] = after
            self.assertRed("claude", "guarded write allow")
        entry["raw_out"], entry["row"]["exit_code"], entry["row"]["stderr_len"] = b"", 2, 0
        entry["row"].update({"verdict": "deny", "verdict_source": "exit_2"})
        entry["row"]["effect"]["exists_after"] = False
        self.assertRed("claude", "guarded write deny")   # exit 2 with empty stderr and stdout is a crash, not a deny

    def test_antigravity_allow_with_written_file_is_green_and_deny_is_red(self):
        self.assertTrue(verifier.effect_matches(self.loaded("antigravity", "guarded_write")))
        self.assertGreen()
        entry = self.fx.find("antigravity", "guarded_write")
        entry["raw_out"] = b'{"decision": "deny", "reason": "outside the workspace"}'
        entry["row"].update({"verdict": "deny", "verdict_source": "json_deny"})
        entry["row"]["effect"]["exists_after"] = False
        self.assertRed("antigravity", "guarded write deny")
        entry["raw_out"] = b'{"decision": "allow", "reason": "full access by default"}'
        entry["row"].update({"verdict": "allow", "verdict_source": "json_allow"})
        self.assertRed("antigravity", "guarded write allow")   # an allow whose file does not exist after it
        entry["raw_out"] = b""
        entry["row"].update({"verdict": "allow", "verdict_source": "silent_exit_0"})
        entry["row"]["effect"]["exists_after"] = True
        self.assertRed("antigravity", "silent_exit_0")   # a no op hook is not the shipped adapter

    # dirt, shapes and bytes ------------------------------------------------------------------------------------------

    def test_dirty_tree_is_red(self):
        self.row("codex", "verb")["tree_clean"] = False
        self.assertRed("codex", "dirty")
        self.row("codex", "verb")["tree_clean"] = True
        cache = os.path.join(self.fx.root, "products", "brothermode", "tools", "__pycache__")
        write(os.path.join(cache, "door.cpython-39.pyc"), b"\x00")   # ignored, as git status ignores it
        self.assertGreen()
        version = os.path.join(self.fx.root, "plugin", "VERSION")
        cases = [(version, lambda p: write(p, b"1.1.1\n"), "plugin/VERSION modified"),
                 (os.path.join(self.fx.root, "products", "brothermode", "tools", "door.py"),
                  lambda p: os.chmod(p, 0o644), "door.py mode changed"),
                 (os.path.join(self.fx.root, "scripts", "host_live_proof.py"),
                  lambda p: write(p, read(p) + b"\n# edited\n"), "scripts/host_live_proof.py modified"),
                 (os.path.join(self.fx.root, "bundle", "hooks", "extra.py"),
                  lambda p: write(p, b"x = 1\n"), "bundle/hooks/extra.py untracked"),
                 (os.path.join(self.fx.root, "scripts", "host_live_claude.py"), lambda p: write(p, b"x = 1\n"),
                  "scripts/host_live_claude.py " + ("modified" if TOOLS[3] in self.fx.files else "untracked"))]
        for path, change, words in cases:
            before = (read(path), os.stat(path).st_mode) if os.path.exists(path) else None
            change(path)
            self.assertRed("dirty", words)
            if before is None:
                os.unlink(path)
            else:
                write(path, before[0], before[1] & 0o777)
            self.assertGreen()

    def test_old_summary_shape_is_red(self):
        summary = b'{"outcome": "loaded", "events_fired": ["PreToolUse", "PostToolUse"]}'
        for data in (summary, summary + b"\n", b'{\n "events_fired": [],\n "outcome": "loaded"\n}\n'):
            write(self.fx.evidence, data)
            code, line = self.judge()
            self.assertEqual(code, 1, line)
            self.assertIn("summary shape {outcome, events_fired}", line)

    def test_altered_raw_byte_is_red(self):
        path = self.fx.write_evidence()
        rows, _ = verifier.load_rows(path)
        raw = os.path.join(self.fx.evidence_dir, rows[0]["raw_out_path"] if rows[0]["raw_out_len"] else rows[0]["raw_in_path"])
        data = bytearray(read(raw))
        data[-2] ^= 0x01
        write(raw, bytes(data))
        code, line = self.judge()
        self.assertEqual(code, 1, line)
        self.assertIn("altered", line)
        os.unlink(raw)
        code, line = self.judge()
        self.assertEqual(code, 1, line)
        self.assertIn("missing", line)

    def test_truncated_last_line_is_red(self):
        whole = read(self.fx.write_evidence())
        for data in (whole + b'{"schema": "hp1.v1", "host": "co', whole[:-1]):
            write(self.fx.evidence, data)
            code, line = self.judge()
            self.assertEqual(code, 1, line)
            self.assertIn("truncated", line)

    def test_unknown_host_name_is_red(self):
        row = self.row("codex", "verb")
        for host in ("no_data", "Claude", "codex ", 7, None):
            row["host"] = host
            self.assertRed("unknown host")

    def test_extra_fourth_host_is_ignored_and_reported(self):
        for e in list(self.fx.entries):
            if e["row"]["host"] == "claude":
                copy = dict(e, row=dict(e["row"], host="cursor", run_id="run-cursor-1", tree_clean=False))
                self.fx.entries.append(copy)
        line = self.assertGreen()
        self.assertIn("ignored 5 row(s) of extra host cursor", line)

    def test_relabelled_probe_and_rewritten_verdict_are_red(self):
        verb = self.row("claude", "verb")
        verb["probe"] = "door"
        self.assertRed("claude", "relabelled")
        verb["probe"] = "verb"
        self.row("codex", "guarded_write").update({"verdict": "allow", "verdict_source": "json_allow"})
        self.assertRed("codex", "not what the raw stdout and exit code say")
        self.row("codex", "guarded_write").update({"verdict": "deny", "verdict_source": "json_deny"})
        self.row("codex", "guarded_write").update({"schema": "hp1.trace.v1"})
        self.assertRed("codex", "schema")
        self.row("codex", "guarded_write").update({"schema": "hp1.v1", "verdict": "maybe"})
        self.assertRed("codex", "unknown verdict word")

    def test_door_row_that_denies_is_red(self):
        entry = self.fx.find("codex", "door")
        entry["raw_out"], entry["row"]["exit_code"], entry["row"]["stderr_len"] = b"", 2, 9
        entry["row"].update({"verdict": "deny", "verdict_source": "exit_2"})
        self.assertRed("codex", "door row whose verdict is deny")

    def test_the_bytes_that_ran_are_the_candidates(self):
        self.row("claude", "door")["witness_sha256"] = sha(b"an edited witness")
        self.assertRed("claude", "witness")
        self.row("claude", "door")["witness_sha256"] = sha(read(WITNESS))
        self.row("codex", "verb")["script_sha256"] = sha(b"an edited hook")
        self.assertRed("codex", "committed blob")
        self.row("codex", "verb")["script_sha256"] = sha(self.fx.files["products/brothermode/tools/door.py"][1])
        self.row("antigravity", "verb")["tool_sha256"] = dict(self.fx.tools, **{TOOLS[1]: sha(b"edited driver")})
        self.assertRed("antigravity", "tool_sha256")
        self.row("antigravity", "verb")["tool_sha256"] = dict(self.fx.tools)
        self.row("claude", "verb")["shim_sha256"] = sha(b"#!/bin/sh\nexec python3 \"$@\"\n")
        self.assertRed("claude", "shim")
        self.row("claude", "verb")["shim_sha256"] = self.fx.shim_sha
        self.row("codex", "door")["script"] = os.path.join(self.tmp, "elsewhere", "door.py")
        self.assertRed("codex", "does not resolve under the row's plugin_root")

    def test_installed_hook_file_is_rehashed(self):
        hooks = self.fx.hooks["codex"][0]
        original = read(hooks)
        write(hooks, original + b"\n")
        self.assertRed("codex", "no longer hashes to hooks_sha256")
        for e in self.fx.entries:
            if e["row"]["host"] == "codex":
                e["row"]["hooks_sha256"] = e["row"]["hooks_expected_sha256"] = sha(original + b"\n")
        self.assertRed("codex", "not the candidate's codex hook file")
        os.unlink(hooks)
        self.assertNoData("codex", "is gone")

    def test_crosscheck_is_hp1e_and_never_a_pass_without_it(self):
        with mock.patch.dict(sys.modules, {"host_live_claude": None}):
            self.assertNoData("claude", "HP1.e")
        stream = os.path.join(self.fx.evidence_dir, "claude-stream.jsonl")
        extra = read(stream) + b'{"event": "PreToolUse"}\n'
        write(stream, extra)
        for row in self.rows_of("claude"):
            row["stream_sha256"] = sha(extra)
        self.assertRed("claude", "disagree")

    def test_command_line_prints_one_line_and_exits_with_the_code(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = verifier.main([os.path.join(self.tmp, "absent.jsonl"), "--root", self.fx.root])
        self.assertEqual(code, 3)
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertTrue(out.getvalue().startswith("NO-DATA:"))
        self.fx.write_evidence()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = verifier.main([self.fx.evidence, "--root", self.fx.root])
        self.assertEqual(code, 0, out.getvalue())
        self.assertTrue(out.getvalue().startswith("GREEN:"))

    def test_hostile_inputs_are_refused(self):
        refusals = (ValueError, LookupError, SystemExit)
        cwd = os.getcwd()
        work = os.path.join(self.tmp, "fuzz")
        os.makedirs(work)
        os.chdir(work)
        try:
            with mock.patch.object(sys, "argv", ["fuzz"]), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                for name, fn in inspect.getmembers(verifier, inspect.isfunction):
                    if name.startswith("_") or fn.__module__ != verifier.__name__:
                        continue
                    params = [p for p in inspect.signature(fn).parameters.values()
                              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                    for value in HOSTILE if params else ():
                        try:
                            fn(*[value] * len(params))
                        except refusals:
                            pass
                        except Exception as exc:  # the classification the landing fuzz applies
                            self.fail("%s(%r) crashed: %s: %s" % (name, value, type(exc).__name__, exc))
                evidence = self.fx.write_evidence()
                for now in (True, 1.5, float("nan"), -1, "1", None):
                    with self.assertRaises(verifier.VerifyRefused):
                        verifier.verify(evidence, self.fx.root, now)
                for bad in (["x"], "x", None, [{"host": ["x"]}, 1]):
                    with self.assertRaises(verifier.VerifyRefused):
                        verifier.signed_in_reason("codex", bad)
                with self.assertRaises(verifier.VerifyRefused):
                    verifier.host_launched(self.row("claude", "door"), "relative/shim")
                self.assertFalse(verifier.effect_matches({"host": ["unhashable"], "effect": {"path": [1]}}))
                self.assertFalse(verifier.host_ancestor({"parent_chain": "x", "host_roots": ["/"]}))
                self.assertEqual(verifier.missing_hosts([{"host": ["x"]}, {"host": {"a": 1}}]), list(verifier.REQUIRED_HOSTS))
                self.assertEqual(verifier.signed_in_reason("claude", []), "no row to show a session")
        finally:
            os.chdir(cwd)
        self.assertEqual(os.listdir(work), [])   # nothing written by a refused call


if __name__ == "__main__":
    unittest.main()
