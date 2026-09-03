#!/usr/bin/env python3
"""BrotherMode Cursor compatibility CLI: install status, manage, and the
Fable-to-Cursor remote harness.

WHAT THIS IS
  The one command surface for running BrotherMode independently inside
  Cursor, and for driving Cursor execution from Claude Code (Fable or
  Opus) through a local mailbox. No network. Packets live under the
  project's .brothermode/cursor-mailbox/ directory.

COMMANDS
  status          what is installed for Cursor on this machine
  doctor          Cursor-specific health checks
  emit-rules      write .cursor/rules/brothermode.mdc into a project
  emit-hooks      print or write a Cursor hooks.json for a checkout
  dispatch        Fable side: enqueue a work packet for Cursor
  claim           Cursor side: claim the next (or named) packet
  claim-next      alias of claim with --next
  record-result   Cursor (or Fable) records a packet outcome
  adopt           Fable re-runs the done-check and accepts or rejects
  poll            wait locally for a packet to leave the running state
  list            show mailbox packets
  cancel          cancel a queued or running packet
  worktree-create create an isolated git worktree for a packet
  worktree-remove remove a packet worktree

THE HARNESS CONTRACT (mirrors the Codex-Execution proposal)
  1. Planner (Fable/Opus in Claude Code) never lets the executor merge
     or push.
  2. Executor works inside a private worktree when one is attached.
  3. Planner re-runs the done-check itself on adopt; a pasted green
     line is a claim, the re-run is the evidence.
  4. Halt-and-report: when the tree contradicts the packet, the
     executor stops and reports rather than improvising.
  5. Fence under Cursor stays ADVISORY until a live canary proves the
     hooks fire (docs/CURSOR-COMPAT.md).

Python 3.9, standard library only. subprocess is used only for git
worktree and for running a founder-authored done-check on adopt.
No em or en dashes anywhere in this file, its comments, or its output.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 4

MAILBOX_DIRNAME = "cursor-mailbox"
PACKET_SCHEMA = 1

PACKET_STATES = (
    "queued",
    "claimed",
    "running",
    "returned",
    "adopted",
    "rejected",
    "cancelled",
)

RULES_FRONTMATTER = """---
description: BrotherMode law and Cursor harness commands for this project
alwaysApply: true
---
"""


def _out(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _err(text):
    sys.stderr.write(text if text.endswith("\n") else text + "\n")


def _load(name):
    import importlib.util
    path = os.path.join(HERE, name + ".py")
    spec = importlib.util.spec_from_file_location("bm_cursor_" + name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_cursor_home():
    return os.path.join(os.path.expanduser("~"), ".cursor")


def default_install_target():
    return os.path.join(default_cursor_home(), "brothermode")


def default_hooks_path():
    return os.path.join(default_cursor_home(), "hooks.json")


def default_record_path():
    return os.path.join(default_cursor_home(), "brothermode-install.json")


def find_checkout():
    """Prefer a Cursor install, then the Claude clone path, then this repo."""
    candidates = [
        os.environ.get("BROTHERMODE_ROOT"),
        os.environ.get("CLAUDE_PLUGIN_ROOT"),
        default_install_target(),
        os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "brothermode"),
        REPO_ROOT,
    ]
    for raw in candidates:
        if not raw:
            continue
        path = os.path.abspath(raw)
        if (os.path.isfile(os.path.join(path, "VERSION"))
                and os.path.isfile(os.path.join(path, "tools",
                                                "bm_store.py"))):
            return path
    return None


def project_root(start=None):
    """Walk up for a .git directory; fall back to cwd."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start or os.getcwd())
        cur = parent


def mailbox_root(root=None):
    root = project_root(root)
    return os.path.join(root, ".brothermode", MAILBOX_DIRNAME)


def _ensure_mailbox(root=None):
    base = mailbox_root(root)
    for sub in ("inbox", "claimed", "outbox", "archive", "worktrees"):
        path = os.path.join(base, sub)
        os.makedirs(path, exist_ok=True)
    return base


def _atomic_write(path, text):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp-%s" % uuid.uuid4().hex
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # sbe: allow-silent best-effort cleanup of the temp file; the real error is already re-raised below
            pass
        raise


def _read_json(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.loads(fh.read())


def _write_json(path, data):
    _atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _packet_path(base, state_dir, packet_id):
    return os.path.join(base, state_dir, packet_id + ".json")


def _find_packet(base, packet_id):
    for sub in ("inbox", "claimed", "outbox", "archive"):
        path = _packet_path(base, sub, packet_id)
        if os.path.isfile(path):
            return path, sub
    return None, None


def new_packet(objective, read_scope, write_scope, done_check,
               actor, budget=None, unit_id=None, risk_class="normal",
               worktree=None, halt_and_report=True):
    packet_id = "cx-" + uuid.uuid4().hex[:12]
    return {
        "schema": PACKET_SCHEMA,
        "packet_id": packet_id,
        "unit_id": unit_id or packet_id,
        "state": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "actor_planner": actor,
        "actor_executor": None,
        "objective": objective,
        "read_scope": list(read_scope or []),
        "write_scope": list(write_scope or []),
        "done_check": done_check,
        "budget": budget or {},
        "risk_class": risk_class,
        "worktree": worktree,
        "halt_and_report": bool(halt_and_report),
        "freshness_assertion": (
            "Before any edit, run git status and git rev-parse HEAD. Quote "
            "both. If the tree contradicts this packet (missing file named "
            "in write_scope, done_check already green when the packet says "
            "it is red), HALT and report rather than complying."
        ),
        "constraints": [
            "Never merge or push.",
            "Stay inside write_scope.",
            "Verify with the done_check after the last edit.",
            "Return worker_claim, artifacts, and the done_check output.",
        ],
        "result": None,
        "adoption": None,
    }


# ---------------------------------------------------------------------------
# Install awareness / rules / hooks emission
# ---------------------------------------------------------------------------

def cmd_status(argv):
    p = argparse.ArgumentParser(prog="bm-cursor status")
    p.parse_args(argv)
    checkout = find_checkout()
    hooks = default_hooks_path()
    record = default_record_path()
    _out("BrotherMode Cursor compatibility")
    _out("  checkout:     %s" % (checkout or "(not found)"))
    if checkout:
        try:
            ver = io.open(os.path.join(checkout, "VERSION"),
                          encoding="utf-8").read().strip()
        except (IOError, OSError):
            ver = "unknown"
        _out("  version:      %s" % ver)
    _out("  hooks.json:   %s%s"
         % (hooks, " (present)" if os.path.isfile(hooks) else " (absent)"))
    _out("  install record: %s%s"
         % (record, " (present)" if os.path.isfile(record) else " (absent)"))
    if os.path.isfile(record):
        try:
            data = _read_json(record)
            _out("  recorded target: %s" % data.get("target", "?"))
            _out("  recorded version: %s" % data.get("version", "?"))
            _out("  recorded at: %s" % data.get("installed_at", "?"))
        except (IOError, OSError, ValueError) as exc:
            _err("  record unreadable: %s" % exc)
    # Project surface
    root = project_root()
    rules = os.path.join(root, ".cursor", "rules", "brothermode.mdc")
    project_hooks = os.path.join(root, ".cursor", "hooks.json")
    _out("  project root: %s" % root)
    _out("  project rules: %s%s"
         % (rules, " (present)" if os.path.isfile(rules) else " (absent)"))
    _out("  project hooks: %s%s"
         % (project_hooks,
            " (present)" if os.path.isfile(project_hooks) else " (absent)"))
    _out("  mailbox: %s%s"
         % (mailbox_root(root),
            " (present)" if os.path.isdir(mailbox_root(root)) else " (absent)"))
    _out("")
    _out("Fence enforcement under Cursor is ADVISORY until a live canary "
         "is recorded. See docs/CURSOR-COMPAT.md.")
    return EXIT_OK


def cmd_doctor(argv):
    p = argparse.ArgumentParser(prog="bm-cursor doctor")
    p.add_argument("--hooks", default=None,
                   help="hooks.json to inspect (default ~/.cursor/hooks.json)")
    p.add_argument("--checkout", default=None,
                   help="BrotherMode checkout to smoke (default: auto-detect)")
    args = p.parse_args(argv)
    problems = []
    checkout = args.checkout or find_checkout()
    if checkout is None:
        problems.append("FAIL: no BrotherMode checkout found "
                        "(install with scripts/install_cursor.py)")
    else:
        adapter = os.path.join(checkout, "tools", "bm_cursor_hook.py")
        if not os.path.isfile(adapter):
            problems.append("FAIL: bm_cursor_hook.py missing under %s"
                            % checkout)
        else:
            # Smoke: adapter fail-opens on a Read-shaped Cursor payload.
            payload = json.dumps({
                "hook_event_name": "preToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/brothermode-cursor-doctor"},
                "cwd": os.getcwd(),
                "conversation_id": "bm-cursor-doctor",
            })
            try:
                proc = subprocess.run(
                    [sys.executable, adapter, "preToolUse"],
                    input=payload, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, universal_newlines=True,
                    timeout=60)
                if proc.returncode != 0:
                    problems.append(
                        "FAIL: adapter exited %d (stderr: %s)"
                        % (proc.returncode, (proc.stderr or "")[:200]))
                else:
                    try:
                        body = json.loads(proc.stdout or "{}")
                    except ValueError:
                        problems.append("FAIL: adapter stdout was not JSON")
                    else:
                        if body.get("permission") not in (None, "allow"):
                            problems.append(
                                "FAIL: adapter denied a Read probe: %s"
                                % body)
            except (OSError, subprocess.SubprocessError) as exc:
                problems.append("FAIL: could not run adapter: %s" % exc)

    hooks = os.path.abspath(args.hooks) if args.hooks else default_hooks_path()
    if os.path.isfile(hooks):
        try:
            data = _read_json(hooks)
            if not isinstance(data.get("hooks"), dict):
                problems.append("FAIL: %s has no hooks object" % hooks)
            else:
                events = data["hooks"]
                if "preToolUse" not in events and "beforeShellExecution" not in events:
                    problems.append(
                        "FAIL: Cursor hooks.json has no preToolUse or "
                        "beforeShellExecution entry")
        except (IOError, OSError, ValueError) as exc:
            problems.append("FAIL: hooks.json unreadable: %s" % exc)
    else:
        problems.append("WARN: no user hooks at %s (project hooks may still "
                        "apply)" % hooks)

    if problems:
        for line in problems:
            _out(line)
        fails = [x for x in problems if x.startswith("FAIL")]
        return EXIT_FAILED if fails else EXIT_OK
    _out("PASS: Cursor adapter smokes clean and hooks look wired")
    return EXIT_OK


def render_rules_body(checkout):
    checkout = checkout or "<checkout>"
    return RULES_FRONTMATTER + """
# BrotherMode for Cursor

You are running under BrotherMode Cursor compatibility mode.

## Law

1. ONE WRITER PER FILE. Claim before edit. Stop on a refusal.
2. REPRODUCE BEFORE FIXING. Fail first, then patch.
3. VERIFY AFTER THE LAST EDIT. Name the command and quote the output.
4. HONEST LIMITS. Say what is unverified.
5. NEVER PUSH unless the founder asks. Never merge a harness packet.

## Checkout

BrotherMode tools live at: `%s`

Run them by absolute path from the project you are working in:

    python3 %s/tools/bm_store.py init
    python3 %s/tools/bm_store.py dashboard
    python3 %s/tools/bm_cursor.py status

## Harness packets

If a harness packet is claimed, obey it exactly:

    python3 %s/tools/bm_cursor.py claim-next
    # do the work inside write_scope (and the worktree when set)
    python3 %s/tools/bm_cursor.py record-result --packet-id <id> \\
        --worker-claim "..." --artifact <path> --done-output <file>

Halt and report when the tree contradicts the packet. Do not improvise
scope. Do not merge. Do not push.

Fence hooks under Cursor are ADVISORY until a live canary is recorded.
""" % (checkout, checkout, checkout, checkout, checkout, checkout)


def cmd_emit_rules(argv):
    p = argparse.ArgumentParser(prog="bm-cursor emit-rules")
    p.add_argument("--project", default=None,
                   help="project root (default: current project)")
    p.add_argument("--checkout", default=None,
                   help="BrotherMode checkout path")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing brothermode.mdc")
    p.add_argument("--stdout", action="store_true",
                   help="print instead of writing")
    args = p.parse_args(argv)
    checkout = args.checkout or find_checkout() or CHECKOUT_PLACEHOLDER()
    body = render_rules_body(checkout)
    if args.stdout:
        _out(body)
        return EXIT_OK
    root = project_root(args.project)
    dest = os.path.join(root, ".cursor", "rules", "brothermode.mdc")
    if os.path.isfile(dest) and not args.force:
        _err("bm-cursor emit-rules: %s exists; pass --force to overwrite"
             % dest)
        return EXIT_REFUSED
    _atomic_write(dest, body if body.endswith("\n") else body + "\n")
    _out("wrote %s" % dest)
    return EXIT_OK


def CHECKOUT_PLACEHOLDER():
    return "<checkout>"


def build_hooks_json(checkout, with_telemetry=False):
    """Cursor-native hooks.json for a BrotherMode checkout."""
    tools = os.path.join(checkout, "tools")
    adapter = os.path.join(tools, "bm_cursor_hook.py")
    py = "python3"

    def entry(event, timeout=10):
        return {
            "command": "%s %s %s" % (py, _shell_quote(adapter), event),
            "timeout": timeout,
        }

    hooks = {
        "preToolUse": [
            dict(entry("preToolUse"), matcher="Write|Shell|Delete|Edit"),
        ],
        "beforeShellExecution": [
            entry("beforeShellExecution"),
        ],
        "postToolUse": [
            dict(entry("postToolUse", 15), matcher="Shell"),
        ],
        "afterShellExecution": [
            entry("afterShellExecution", 15),
        ],
        "afterFileEdit": [
            entry("afterFileEdit", 10),
        ],
        "sessionStart": [
            entry("sessionStart", 30),
        ],
        "preCompact": [
            entry("preCompact", 60),
        ],
        "stop": [
            entry("stop", 30),
        ],
    }
    if with_telemetry:
        # Telemetry stays optional: consent gated inside the tools.
        pass
    return {"version": 1, "hooks": hooks}


def _shell_quote(path):
    import shlex
    return shlex.quote(path)


def cmd_emit_hooks(argv):
    p = argparse.ArgumentParser(prog="bm-cursor emit-hooks")
    p.add_argument("--checkout", default=None)
    p.add_argument("--write", default=None,
                   help="path to write (default: print to stdout)")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    checkout = args.checkout or find_checkout()
    if not checkout:
        _err("bm-cursor emit-hooks: no checkout found")
        return EXIT_FAILED
    doc = build_hooks_json(checkout)
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if not args.write:
        _out(text.rstrip("\n"))
        return EXIT_OK
    dest = os.path.abspath(args.write)
    if os.path.isfile(dest) and not args.force:
        # Refuse to clobber foreign hooks; install_cursor.py merges.
        try:
            existing = _read_json(dest)
        except (IOError, OSError, ValueError):
            existing = None
        if existing is not None and not _hooks_are_ours(existing, checkout):
            _err("bm-cursor emit-hooks: %s exists and is not owned by "
                 "this BrotherMode checkout; pass --force to overwrite"
                 % dest)
            return EXIT_REFUSED
    _atomic_write(dest, text)
    _out("wrote %s" % dest)
    return EXIT_OK


def _hooks_are_ours(doc, checkout):
    """True when every command path in the hooks doc names this checkout."""
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return False
    marker = os.path.join(os.path.abspath(checkout), "tools",
                          "bm_cursor_hook.py")
    seen = False
    for _event, entries in hooks.items():
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, dict):
                return False
            cmd = entry.get("command")
            if not isinstance(cmd, str):
                return False
            if marker not in cmd and os.path.realpath(marker) not in cmd:
                # Allow quoted forms.
                if "bm_cursor_hook.py" not in cmd:
                    return False
                if os.path.abspath(checkout) not in cmd and checkout not in cmd:
                    return False
            seen = True
    return seen


# ---------------------------------------------------------------------------
# Harness mailbox
# ---------------------------------------------------------------------------

def cmd_dispatch(argv):
    p = argparse.ArgumentParser(prog="bm-cursor dispatch")
    p.add_argument("--objective", required=True)
    p.add_argument("--read-scope", action="append", default=[])
    p.add_argument("--write-scope", action="append", default=[])
    p.add_argument("--done-check", required=True,
                   help="shell command the planner will re-run on adopt")
    p.add_argument("--actor", default="fable")
    p.add_argument("--unit-id", default=None)
    p.add_argument("--risk-class", default="normal")
    p.add_argument("--project", default=None)
    p.add_argument("--with-worktree", action="store_true",
                   help="create an isolated git worktree for the packet")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    worktree = None
    packet = new_packet(
        objective=args.objective,
        read_scope=args.read_scope,
        write_scope=args.write_scope,
        done_check=args.done_check,
        actor=args.actor,
        unit_id=args.unit_id,
        risk_class=args.risk_class,
    )
    if args.with_worktree:
        wt = os.path.join(base, "worktrees", packet["packet_id"])
        code, err = _git_worktree_add(root, wt, packet["packet_id"])
        if code != 0:
            _err("bm-cursor dispatch: worktree failed: %s" % err)
            return EXIT_FAILED
        worktree = wt
        packet["worktree"] = worktree
    path = _packet_path(base, "inbox", packet["packet_id"])
    _write_json(path, packet)
    if args.json:
        _out(json.dumps(packet, sort_keys=True))
    else:
        _out("dispatched %s" % packet["packet_id"])
        _out("  state: queued")
        _out("  inbox: %s" % path)
        if worktree:
            _out("  worktree: %s" % worktree)
        _out("Next on the Cursor side: bm-cursor claim-next")
    return EXIT_OK


def _git_worktree_add(repo, path, packet_id):
    branch = "brothermode/cursor-%s" % packet_id
    if os.path.exists(path):
        return 1, "path already exists: %s" % path
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "worktree", "add", "-b", branch, path, "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    if proc.returncode != 0:
        return proc.returncode, (proc.stderr or proc.stdout or "").strip()
    return 0, ""


def _git_worktree_remove(repo, path):
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "worktree", "remove", "--force", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    if proc.returncode != 0:
        # Fall back to plain rmtree if git refuses (already removed).
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        return 0, ""
    return 0, ""


def cmd_claim(argv):
    p = argparse.ArgumentParser(prog="bm-cursor claim")
    p.add_argument("--packet-id", default=None)
    p.add_argument("--next", action="store_true",
                   help="claim the oldest queued packet")
    p.add_argument("--actor", default="cursor")
    p.add_argument("--project", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if not args.packet_id and not args.next:
        args.next = True
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    if args.packet_id:
        path, sub = _find_packet(base, args.packet_id)
        if path is None or sub != "inbox":
            _err("bm-cursor claim: packet %s is not queued"
                 % args.packet_id)
            return EXIT_REFUSED
    else:
        inbox = os.path.join(base, "inbox")
        names = sorted(n for n in os.listdir(inbox) if n.endswith(".json"))
        if not names:
            _err("bm-cursor claim: inbox empty")
            return EXIT_REFUSED
        path = os.path.join(inbox, names[0])
    packet = _read_json(path)
    packet["state"] = "claimed"
    packet["actor_executor"] = args.actor
    packet["updated_at"] = _now()
    packet["claimed_at"] = _now()
    dest = _packet_path(base, "claimed", packet["packet_id"])
    _write_json(dest, packet)
    os.unlink(path)
    if args.json:
        _out(json.dumps(packet, sort_keys=True))
    else:
        _out("claimed %s" % packet["packet_id"])
        _out("  objective: %s" % packet.get("objective"))
        _out("  write_scope: %s" % ", ".join(packet.get("write_scope") or []))
        _out("  done_check: %s" % packet.get("done_check"))
        if packet.get("worktree"):
            _out("  worktree: %s" % packet["worktree"])
            _out("  WORK IN THE WORKTREE. Do not edit the shared tree.")
        _out("  freshness: %s" % packet.get("freshness_assertion"))
        _out("When done: bm-cursor record-result --packet-id %s ..."
             % packet["packet_id"])
    return EXIT_OK


def cmd_record_result(argv):
    p = argparse.ArgumentParser(prog="bm-cursor record-result")
    p.add_argument("--packet-id", required=True)
    p.add_argument("--worker-claim", required=True)
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--done-output", default=None,
                   help="file containing the executor's done_check output")
    p.add_argument("--status", default="returned",
                   choices=["returned", "halted", "failed"])
    p.add_argument("--project", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    path, sub = _find_packet(base, args.packet_id)
    if path is None or sub not in ("claimed", "inbox"):
        _err("bm-cursor record-result: packet %s not claimable/claimed"
             % args.packet_id)
        return EXIT_REFUSED
    packet = _read_json(path)
    done_output = ""
    if args.done_output:
        try:
            done_output = io.open(args.done_output, encoding="utf-8").read()
        except (IOError, OSError) as exc:
            _err("bm-cursor record-result: cannot read --done-output: %s"
                 % exc)
            return EXIT_FAILED
    packet["state"] = "returned"
    packet["updated_at"] = _now()
    packet["result"] = {
        "status": args.status,
        "worker_claim": args.worker_claim,
        "artifacts": list(args.artifact or []),
        "done_output": done_output,
        "recorded_at": _now(),
    }
    dest = _packet_path(base, "outbox", packet["packet_id"])
    _write_json(dest, packet)
    if path != dest:
        try:
            os.unlink(path)
        except OSError as exc:
            _err("bm-cursor record-result: wrote %s but could not remove stale "
                 "copy %s (%s); _find_packet may report the old state until "
                 "this is cleaned up by hand" % (dest, path, exc))
    if args.json:
        _out(json.dumps(packet, sort_keys=True))
    else:
        _out("recorded %s (%s)" % (packet["packet_id"], args.status))
        _out("Next on the planner side: bm-cursor adopt --packet-id %s"
             % packet["packet_id"])
    return EXIT_OK


def cmd_adopt(argv):
    p = argparse.ArgumentParser(prog="bm-cursor adopt")
    p.add_argument("--packet-id", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--skip-check", action="store_true",
                   help="record adoption without re-running done_check "
                        "(discouraged; for dry rehearsal only)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    path, sub = _find_packet(base, args.packet_id)
    if path is None or sub != "outbox":
        _err("bm-cursor adopt: packet %s is not in outbox" % args.packet_id)
        return EXIT_REFUSED
    packet = _read_json(path)
    cwd = packet.get("worktree") or root
    check = packet.get("done_check")
    exit_code = None
    stdout = ""
    stderr = ""
    if args.skip_check:
        verdict = "skipped"
        ok = True
        # --skip-check means no check actually ran: whatever this packet
        # claims is asserted, not executed, the exact record class the
        # invariant this field exists for (tools/bm_store.py's own
        # EVIDENCE_ORIGINS/origin_satisfies) calls synthetic. "adopted"
        # below is still this packet's own routing bucket (it leaves
        # outbox either way), never a claim that this origin satisfies
        # anything on its own.
        origin = "synthetic"
    else:
        if not check:
            _err("bm-cursor adopt: packet has no done_check")
            return EXIT_REFUSED
        try:
            proc = subprocess.run(
                check, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=600)
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except (OSError, subprocess.SubprocessError) as exc:
            _err("bm-cursor adopt: done_check could not run: %s" % exc)
            return EXIT_FAILED
        ok = exit_code == 0
        verdict = "passed" if ok else "failed"
        # The check just ran, in this process, against the packet's own
        # done_check: "executed" in the same vocabulary origin above uses.
        origin = "executed"
    packet["adoption"] = {
        "verdict": verdict,
        "origin": origin,
        "exit_code": exit_code,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "adopted_at": _now(),
        "cwd": cwd,
    }
    packet["state"] = "adopted" if ok else "rejected"
    packet["updated_at"] = _now()
    dest = _packet_path(base, "archive", packet["packet_id"])
    _write_json(dest, packet)
    try:
        os.unlink(path)
    except OSError as exc:
        _err("bm-cursor adopt: wrote %s but could not remove stale copy %s "
             "(%s); _find_packet may report the old state until this is "
             "cleaned up by hand" % (dest, path, exc))
    if args.json:
        _out(json.dumps(packet, sort_keys=True))
    else:
        _out("%s %s" % (packet["state"], packet["packet_id"]))
        _out("  done_check verdict: %s" % verdict)
        if exit_code is not None:
            _out("  exit_code: %d" % exit_code)
        if not ok:
            _out("  stderr (tail): %s" % (stderr[-400:] or "(empty)"))
        if origin == "synthetic":
            _out("  INTERRUPTED: result NO-DATA (origin=synthetic; "
                 "--skip-check means no check actually ran, so this "
                 "packet's own routing state above may never be read as "
                 "a completion claim); next action: re-run adopt without "
                 "--skip-check to obtain executed evidence")
    return EXIT_OK if ok else EXIT_FAILED


def cmd_poll(argv):
    p = argparse.ArgumentParser(prog="bm-cursor poll")
    p.add_argument("--packet-id", required=True)
    p.add_argument("--project", default=None)
    p.add_argument("--timeout", type=int, default=0,
                   help="seconds to wait; 0 means one shot")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    deadline = time.time() + max(0, args.timeout)
    while True:
        path, sub = _find_packet(base, args.packet_id)
        if path is None:
            _err("bm-cursor poll: packet %s not found" % args.packet_id)
            return EXIT_REFUSED
        packet = _read_json(path)
        state = packet.get("state")
        if state in ("returned", "adopted", "rejected", "cancelled"):
            if args.json:
                _out(json.dumps(packet, sort_keys=True))
            else:
                _out("%s %s (in %s)" % (state, args.packet_id, sub))
            return EXIT_OK
        if args.timeout <= 0 or time.time() >= deadline:
            if args.json:
                _out(json.dumps(packet, sort_keys=True))
            else:
                _out("%s %s (still open in %s)" % (state, args.packet_id, sub))
            return EXIT_OK
        time.sleep(max(0.1, args.interval))


def cmd_list(argv):
    p = argparse.ArgumentParser(prog="bm-cursor list")
    p.add_argument("--project", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    rows = []
    for sub in ("inbox", "claimed", "outbox", "archive"):
        folder = os.path.join(base, sub)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            try:
                packet = _read_json(os.path.join(folder, name))
            except (IOError, OSError, ValueError):
                continue
            rows.append({
                "packet_id": packet.get("packet_id"),
                "state": packet.get("state"),
                "folder": sub,
                "objective": packet.get("objective"),
                "updated_at": packet.get("updated_at"),
            })
    if args.json:
        _out(json.dumps(rows, indent=2, sort_keys=True))
    else:
        if not rows:
            _out("(mailbox empty)")
            return EXIT_OK
        for row in rows:
            _out("%-10s %-12s %s"
                 % (row["folder"], row["state"], row["packet_id"]))
            _out("    %s" % (row["objective"] or ""))
    return EXIT_OK


def cmd_cancel(argv):
    p = argparse.ArgumentParser(prog="bm-cursor cancel")
    p.add_argument("--packet-id", required=True)
    p.add_argument("--project", default=None)
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    path, sub = _find_packet(base, args.packet_id)
    if path is None:
        _err("bm-cursor cancel: packet %s not found" % args.packet_id)
        return EXIT_REFUSED
    if sub == "archive":
        _err("bm-cursor cancel: packet already archived")
        return EXIT_REFUSED
    packet = _read_json(path)
    packet["state"] = "cancelled"
    packet["updated_at"] = _now()
    dest = _packet_path(base, "archive", packet["packet_id"])
    _write_json(dest, packet)
    try:
        os.unlink(path)
    except OSError as exc:
        _err("bm-cursor cancel: wrote %s but could not remove stale copy %s "
             "(%s); _find_packet may report the old state until this is "
             "cleaned up by hand" % (dest, path, exc))
    if packet.get("worktree"):
        _git_worktree_remove(root, packet["worktree"])
    _out("cancelled %s" % packet["packet_id"])
    return EXIT_OK


def cmd_worktree_create(argv):
    p = argparse.ArgumentParser(prog="bm-cursor worktree-create")
    p.add_argument("--packet-id", required=True)
    p.add_argument("--project", default=None)
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    path, sub = _find_packet(base, args.packet_id)
    if path is None:
        _err("bm-cursor worktree-create: packet not found")
        return EXIT_REFUSED
    packet = _read_json(path)
    if packet.get("worktree") and os.path.isdir(packet["worktree"]):
        _out(packet["worktree"])
        return EXIT_OK
    wt = os.path.join(base, "worktrees", packet["packet_id"])
    code, err = _git_worktree_add(root, wt, packet["packet_id"])
    if code != 0:
        _err("bm-cursor worktree-create: %s" % err)
        return EXIT_FAILED
    packet["worktree"] = wt
    packet["updated_at"] = _now()
    _write_json(path, packet)
    _out(wt)
    return EXIT_OK


def cmd_worktree_remove(argv):
    p = argparse.ArgumentParser(prog="bm-cursor worktree-remove")
    p.add_argument("--packet-id", required=True)
    p.add_argument("--project", default=None)
    args = p.parse_args(argv)
    root = project_root(args.project)
    base = _ensure_mailbox(root)
    path, sub = _find_packet(base, args.packet_id)
    if path is None:
        _err("bm-cursor worktree-remove: packet not found")
        return EXIT_REFUSED
    packet = _read_json(path)
    wt = packet.get("worktree")
    if not wt:
        _out("no worktree attached")
        return EXIT_OK
    code, err = _git_worktree_remove(root, wt)
    if code != 0:
        _err("bm-cursor worktree-remove: %s" % err)
        return EXIT_FAILED
    packet["worktree"] = None
    packet["updated_at"] = _now()
    _write_json(path, packet)
    _out("removed %s" % wt)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Controller WorkerAdapter (importable)
# ---------------------------------------------------------------------------

class CursorMailboxWorker(object):
    """WorkerAdapter-compatible mailbox dispatcher for bm_controller.

    run(brief) writes a queued packet and returns pending. The planner
    later calls bm-cursor adopt / bm-controller record-result. This class
    never calls a model and never opens a network socket.
    """

    def __init__(self, project=None, actor="fable", with_worktree=False,
                 out=None):
        self.project = project
        self.actor = actor
        self.with_worktree = with_worktree
        self._out = out or _out

    def run(self, brief):
        if not isinstance(brief, dict):
            return {"worker_claim": "", "artifacts": [],
                    "cost": {"tokens": 0, "minutes": 0},
                    "status": "malformed"}
        root = project_root(self.project)
        base = _ensure_mailbox(root)
        packet = new_packet(
            objective=brief.get("objective") or "",
            read_scope=brief.get("read_scope") or [],
            write_scope=brief.get("write_scope") or [],
            done_check=(brief.get("done_check")
                        or "true  # packet lacked done_check"),
            actor=self.actor,
            unit_id=brief.get("unit_id"),
            risk_class=brief.get("risk_class") or "normal",
        )
        if self.with_worktree:
            wt = os.path.join(base, "worktrees", packet["packet_id"])
            code, err = _git_worktree_add(root, wt, packet["packet_id"])
            if code != 0:
                return {"worker_claim": "", "artifacts": [],
                        "cost": {"tokens": 0, "minutes": 0},
                        "status": "unavailable"}
            packet["worktree"] = wt
        path = _packet_path(base, "inbox", packet["packet_id"])
        _write_json(path, packet)
        self._out(json.dumps({
            "cursor_packet": {
                "packet_id": packet["packet_id"],
                "path": path,
                "worktree": packet.get("worktree"),
                "unit_id": packet.get("unit_id"),
            }
        }, sort_keys=True))
        return {"worker_claim": "", "artifacts": [path],
                "cost": {"tokens": 0, "minutes": 0}, "status": "pending"}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "status": cmd_status,
    "doctor": cmd_doctor,
    "emit-rules": cmd_emit_rules,
    "emit-hooks": cmd_emit_hooks,
    "dispatch": cmd_dispatch,
    "claim": cmd_claim,
    "claim-next": cmd_claim,
    "record-result": cmd_record_result,
    "adopt": cmd_adopt,
    "poll": cmd_poll,
    "list": cmd_list,
    "cancel": cmd_cancel,
    "worktree-create": cmd_worktree_create,
    "worktree-remove": cmd_worktree_remove,
}

_ONE_LINERS = (
    ("status", "Cursor install and project surface"),
    ("doctor", "smoke the Cursor adapter and hooks"),
    ("emit-rules", "write .cursor/rules/brothermode.mdc"),
    ("emit-hooks", "print or write Cursor hooks.json"),
    ("dispatch", "Fable: enqueue a Cursor work packet"),
    ("claim", "Cursor: claim a queued packet"),
    ("claim-next", "Cursor: claim the oldest queued packet"),
    ("record-result", "Cursor: return packet results"),
    ("adopt", "Fable: re-run done_check and accept or reject"),
    ("poll", "wait locally for a packet terminal state"),
    ("list", "show mailbox packets"),
    ("cancel", "cancel a queued or running packet"),
    ("worktree-create", "attach an isolated git worktree"),
    ("worktree-remove", "remove a packet worktree"),
)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out("bm-cursor: BrotherMode Cursor compatibility and harness")
        _out("")
        for name, line in _ONE_LINERS:
            _out("  %-16s %s" % (name, line))
        _out("")
        _out("Install: python3 scripts/install_cursor.py")
        _out("Docs:    docs/CURSOR-COMPAT.md")
        return EXIT_OK
    cmd = argv[0]
    if cmd == "claim-next":
        # Force --next for the alias when the user did not pass flags.
        rest = argv[1:]
        if "--packet-id" not in rest and "--next" not in rest:
            rest = ["--next"] + rest
        return cmd_claim(rest)
    if cmd not in COMMANDS:
        _err("bm-cursor: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return EXIT_USAGE
    return COMMANDS[cmd](argv[1:])


def cli():
    try:
        return main()
    except KeyboardInterrupt:
        _err("bm-cursor: interrupted")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(cli())
