"""Stream-json transcript in, one cold-start run's five metrics out.

Pure by design: it reads the path it is handed and computes. No subprocess, no
network, no model. That is what lets the whole metric definition be tested
exhaustively against checked-in transcripts at zero token cost.

A transcript that cannot be read RAISES rather than returning zeroes. Zeroes
would be the best possible score on four of the five metrics, so a broken read
must never be able to produce one.
"""

import json
import os

# Reads under these prefixes are the "internal documentation" the north star's
# check says a beginner should not have needed.
INTERNAL_PREFIXES = ("docs/", "references/")

READ_TOOLS = ("Read", "Grep", "Glob")


def _content(event):
    msg = event.get("message") or {}
    body = msg.get("content")
    return body if isinstance(body, list) else []


def _is_command(text):
    stripped = (text or "").strip()
    return stripped.startswith("/")


#: The field a system event carries hook output in, preferred first. See the
#: comment in parse_transcript for the probe these names came from.
HOOK_OUTPUT_FIELD = "output"
HOOK_OUTPUT_FALLBACK_FIELDS = ("stdout", "stderr")


def _hook_bytes(event):
    """Bytes of hook output one system event injected into the session.

    `output` when present, else `stdout` plus `stderr`, never both paths
    summed: in the probe those two spellings carry the SAME content, so
    adding them would report roughly double the real context tax. A zero here
    now means the event genuinely carried no hook output, rather than meaning
    this function looked for a key nothing emits."""
    primary = event.get(HOOK_OUTPUT_FIELD)
    if isinstance(primary, str) and primary:
        return len(primary)
    total = 0
    for name in HOOK_OUTPUT_FALLBACK_FIELDS:
        value = event.get(name)
        if isinstance(value, str):
            total += len(value)
    return total


def parse_transcript(path):
    if not os.path.isfile(path):
        raise OSError("no transcript at %s" % path)
    events = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, raw in enumerate(fh.read().splitlines(), 1):
            if not raw.strip():
                continue
            try:
                events.append(json.loads(raw))
            except ValueError:
                raise ValueError("%s:%d is not valid JSON" % (os.path.basename(path), i))

    commands = 0
    internal_docs = 0
    context_bytes = 0
    # Field names pinned against a live probe of the CLI's stream-json output
    # taken 2026-08-11, not written from expectation. The previous version
    # summed event["hook_output"], a key that probe contains ZERO times, so
    # this metric read 0 on every run: a perfect score on the exact weakness
    # it exists to expose, which is what its own design warned a broken feed
    # would look like. In the probe the bytes live in "output" (54,161), with
    # "stdout" plus "stderr" carrying the same content split (53,926 and 235),
    # so "output" is preferred and the split is the fallback rather than being
    # added to it, because summing all three double counts.
    turns = 0
    turns_to_artifact = None
    completed = False
    outcome = "no result event in transcript"

    for event in events:
        kind = event.get("type")
        if kind == "system":
            context_bytes += _hook_bytes(event)
        elif kind == "user":
            for block in _content(event):
                if block.get("type") == "text" and _is_command(block.get("text")):
                    commands += 1
        elif kind == "assistant":
            turns += 1
            for block in _content(event):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                target = (block.get("input") or {}).get("file_path") or ""
                if name in READ_TOOLS and any(p in target for p in INTERNAL_PREFIXES):
                    internal_docs += 1
                if name in ("Write", "Edit") and turns_to_artifact is None:
                    turns_to_artifact = turns
        elif kind == "result":
            completed = not event.get("is_error", True)
            outcome = "ok" if completed else (event.get("subtype") or "unknown error")

    return {
        "commands_typed": commands,
        "internal_docs_opened": internal_docs,
        "context_bytes_at_start": context_bytes,
        "turns_to_first_artifact": turns_to_artifact,
        "completed": completed,
        "outcome": outcome,
    }
