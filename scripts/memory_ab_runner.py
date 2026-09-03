#!/usr/bin/env python3
"""Runner for scripts/memory_ab.py (VB-11, D04 part B), 2026-08-30 run.

Contract, per the harness docstring: task JSON on stdin, BM_MEMORY=on|off in
the environment, stdout a JSON object {"output": str}.

HOW THE MODEL WAS INVOKED, and why not through `claude -p`. The intended
design invoked `claude -p --model claude-haiku-4-5-20251001` per task per
mode. That path is DEAD on this machine tonight: every non-interactive
`claude -p` returns is_error true with "Failed to authenticate: OAuth session
expired and could not be refreshed" (reproduced twice, once with the parent
session's env and once with a scrubbed env), and re-authenticating is a
founder-only credential action. The measurement still had to run, so task
execution went through Claude Code subagents on claude-haiku-4-5 instead:
one subagent per task per mode, no tools allowed, prompt passed verbatim.
Claude only; nothing left Anthropic.

The ON pass prompt = the product's recalled memory prepended to the task
prompt. Retrieval is the installed product surface, the same function
scripts/memory_lift.py loads: matching_lessons() from
~/.claude/hooks/repeat_guard.py over ~/.claude/repeat-guard/lessons.jsonl.
The exact prompt pairs are frozen in
benchmarks/memory-ab/prompts-2026-08-30.json (generated mechanically).
bm_vault.py was not used because this project's write guard forbids running
the companion project's tools from a Brother session.

The subagents ran BEFORE the harness, so this runner is a replay: it returns
the stored output for (task id, mode) from benchmarks/memory-ab/outputs/.
Consequences, stated rather than hidden: the harness's duration column
measures a file read and is MEANINGLESS for this run, and tokens_out is
NO-DATA because the subagent interface does not report per-call output
tokens. Task success, the headline measure, is unaffected: the checks run on
the models' real outputs, produced with the injected context as the only
difference between the passes.

Python 3.9 floor, standard library only.
"""
import json
import os
import sys

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "benchmarks", "memory-ab", "outputs")


def main():
    task = json.load(sys.stdin)
    mode = os.environ.get("BM_MEMORY")
    if mode not in ("on", "off"):
        sys.stderr.write("memory_ab_runner: BM_MEMORY must be on or off\n")
        return 2
    path = os.path.join(OUT_DIR, "%s-%s.txt" % (task["id"], mode))
    try:
        with open(path, encoding="utf-8") as fh:
            output = fh.read()
    except OSError as exc:
        sys.stderr.write("memory_ab_runner: no stored output: %s\n" % exc)
        return 2
    json.dump({"output": output}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
