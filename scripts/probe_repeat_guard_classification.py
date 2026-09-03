#!/usr/bin/env python3
"""Probe: does the repeat guard count a SUCCESSFUL tool call as a failure?

WHAT THIS FOUND, 2026-08-24. It does, for every tool except Bash.

tools/repeat-guard/repeat_guard.py decides success in one line:

    ok = (code == 0) and not resp.get("timed_out")

where `code = resp.get("exit_code")`. But `exit_code` is a BASH concept. The
Edit, Write and NotebookEdit tools return no such field, so `code` is None,
`(None == 0)` is False, and EVERY SUCCESSFUL EDIT IS RECORDED AS A FAILURE.
Four successful edits to one file therefore trip a control whose entire purpose
is to refuse an approach that FAILED three times.

This is the estate's own failure family, one more time: a control reading a
field its target does not provide, and defaulting to the wrong verdict rather
than to no verdict. The file's own header promises the opposite behaviour,
"FAILS OPEN. Any malformed payload, unreadable state file, or unexpected shape"
is allowed. This path fails CLOSED.

WHY IT MATTERS RATHER THAN BEING A CURIOSITY. The live hook at
~/.claude/hooks/repeat_guard.py is byte identical to the repository copy, so
this is active in every session on this machine, and it blocks legitimate
repeated editing while reporting those edits as failures.

    python3 scripts/probe_repeat_guard_classification.py

Exit 0 when the guard classifies BOTH tools correctly. Exit 1 while the defect
is present, which is the expected result today. This probe REPORTS; it does not
change the guard, and the fix is deliberately left for a person.
"""
import json
import pathlib
import subprocess
import sys

GUARD = pathlib.Path(__file__).resolve().parent.parent / "tools" / "repeat-guard" / "repeat_guard.py"


def run(payload):
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True)


def refused_after_four_successes(tool_name, tool_input, tool_response, session):
    """Feed four SUCCESSFUL calls, then ask PreToolUse whether a fifth is allowed."""
    post = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
    }
    for _ in range(4):
        run(post)
    pre = dict(post)
    pre["hook_event_name"] = "PreToolUse"
    pre.pop("tool_response", None)
    return run(pre).returncode == 2


def main():
    cases = [
        ("Edit", {"file_path": "/tmp/probe_target.md", "old_string": "a", "new_string": "b"},
         {"filePath": "/tmp/probe_target.md", "success": True}, "probe-edit-success"),
        ("Write", {"file_path": "/tmp/probe_target2.md", "content": "x"},
         {"filePath": "/tmp/probe_target2.md", "success": True}, "probe-write-success"),
        ("Bash", {"command": "echo hi"},
         {"exit_code": 0, "stdout": "hi"}, "probe-bash-success"),
    ]
    bad = 0
    print("%-8s %-38s %s" % ("TOOL", "AFTER FOUR SUCCESSFUL CALLS", "VERDICT"))
    for tool, tin, tresp, sid in cases:
        refused = refused_after_four_successes(tool, tin, tresp, sid)
        verdict = "REFUSED, and it should not be" if refused else "allowed, correct"
        if refused:
            bad += 1
        print("%-8s %-38s %s" % (tool, "a fifth identical successful call", verdict))
    print()
    if bad:
        print("DEFECT PRESENT in %d of %d tools." % (bad, len(cases)))
        print("Cause: ok = (code == 0) where code = tool_response.get('exit_code').")
        print("Only Bash supplies exit_code, so every other tool scores as a failure.")
        print("The safe fix is to FAIL OPEN when no exit code is present, which is")
        print("what this file's own header already promises it does.")
        return 1
    print("All tools classified correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
