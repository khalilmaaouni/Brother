#!/usr/bin/env python3
"""Heartbeat writer for the stall detector: one JSONL line per hook event.

Wired as PostToolUse, PostToolUseFailure and Stop hooks in the estate's OWN
`.claude/settings.local.json`, never in the shipped plugin hooks: a consumer
installing a design skill must not inherit an estate monitor.

This file is the one component in the detector stack that sits in front of
every tool call, so it has exactly one hard rule: IT NEVER BLOCKS. Any
internal failure (unreadable stdin, unwritable ledger, full disk) exits 0
silently. That inverts the "a monitor must fail loudly" rule on purpose, and
the spec states why: the watcher is the monitor and fails loudly; this writer
is plumbing, and plumbing that can stop the pipeline is a stall generator,
not a stall detector. The watcher notices a silent writer the same way it
notices a dead one: the ledger goes quiet while files change.

Design doc: docs/specs/2026-08-11-stall-detector-v2-design.md.
"""
import hashlib
import json
import os
import sys
import time

#: Rotate when the ledger crosses this size, keeping the newest tail. The
#: detector only ever reads recent minutes, so history beyond that is dead
#: weight; 5 MB is far more than a day of events on this estate.
ROTATE_BYTES = 5 * 1024 * 1024
ROTATE_KEEP_LINES = 2000

ENV_PATH = "BROTHERSBE_HEARTBEAT_PATH"


def ledger_path(payload):
    override = os.environ.get(ENV_PATH, "").strip()
    if override:
        return override
    root = payload.get("project_dir") or payload.get("cwd") or os.getcwd()
    return os.path.join(root, ".sbe", "runtime", "heartbeat.jsonl")


def target_hash(tool_input):
    """A short stable fingerprint of WHAT the tool acted on, so the detector
    can see the same failing command repeating without this ledger ever
    holding the command text itself (commands can carry paths and prompts
    that do not belong in a monitoring file)."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "notebook_path", "url", "prompt"):
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            return hashlib.sha256(v.encode("utf-8", "replace")).hexdigest()[:12]
    return ""


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name") or ""
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "epoch": int(time.time()),
            "session": payload.get("session_id") or "",
            "event": event,
            "tool": payload.get("tool_name") or "",
            "thash": target_hash(payload.get("tool_input")),
            "ok": event != "PostToolUseFailure",
        }
        transcript = payload.get("transcript_path")
        if isinstance(transcript, str) and transcript:
            try:
                line["transcript_bytes"] = os.path.getsize(transcript)
            except OSError:  # sbe: allow-silent a transcript that vanished mid-read is not worth blocking a tool call over; the size field is optional by design
                pass
        path = ledger_path(payload)
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "a") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
        try:
            if os.path.getsize(path) > ROTATE_BYTES:
                with open(path) as fh:
                    tail = fh.readlines()[-ROTATE_KEEP_LINES:]
                with open(path, "w") as fh:
                    fh.writelines(tail)
        except OSError:  # sbe: allow-silent rotation is best-effort maintenance; a failed rotation leaves a bigger ledger, never a lost event
            pass
    except Exception:  # sbe: allow-silent the writer's one hard rule is IT NEVER BLOCKS a tool call; the watcher notices a quiet ledger, which is the loud path
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
