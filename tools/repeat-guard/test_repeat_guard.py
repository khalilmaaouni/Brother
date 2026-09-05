"""Calibration for repeat_guard.py.

EVERY CASE ASSERTS THE EXIT CODE. A PreToolUse hook blocks ONLY on exit 2;
exit 1 is a non-blocking error and the tool call proceeds anyway. A test that
asserted the printed reason instead would pass over a guard that enforces
nothing, which is precisely the failure this estate recorded on 2026-08-23
when eleven tests passed over a gate printing FAIL and exiting 0.

Proving command:  python3 ~/.claude/hooks/test_repeat_guard.py
Expected tail:    0 failures
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HOOK = str(pathlib.Path(__file__).with_name("repeat_guard.py"))

results = []


def run(payload, home):
    env = dict(os.environ)
    env["HOME"] = home
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, p.stdout, p.stderr


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: exit {got}, wanted {want}")


def pre(cmd):
    return {"hook_event_name": "PreToolUse", "session_id": "s1",
            "tool_name": "Bash", "tool_input": {"command": cmd}}


def state_dir(home):
    return pathlib.Path(home) / ".claude" / "repeat-guard"


def signature_of(payload):
    """The hook's own signature function, imported rather than reimplemented,
    because two parsers of one format drift and neither side finds out."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_rg", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.signature(payload.get("tool_name", ""), payload.get("tool_input") or {})[0]


def pre_edit(path):
    """A PreToolUse payload for Edit. This suite had NO non-Bash case before
    2026-08-24, which is exactly why the defect pinned below shipped: the hook
    is registered on Bash|Edit|Write|NotebookEdit and only one of the four was
    ever exercised."""
    return {"hook_event_name": "PreToolUse", "session_id": "s1",
            "tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def post_edit(path):
    """A PostToolUse payload for a SUCCESSFUL Edit, exactly as the harness
    reports one: there is NO exit_code field, because that is a Bash concept."""
    return {"hook_event_name": "PostToolUse", "session_id": "s1",
            "tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"},
            "tool_response": {"filePath": path, "success": True}}


def post_write(path):
    """A SUCCESSFUL Write, as the harness reports it: no exit_code field."""
    return {"hook_event_name": "PostToolUse", "session_id": "s1",
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"},
            "tool_response": {"filePath": path, "success": True}}


def post_notebook(path):
    """A SUCCESSFUL NotebookEdit, same shape, same missing exit_code."""
    return {"hook_event_name": "PostToolUse", "session_id": "s1",
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": path, "new_source": "x"},
            "tool_response": {"filePath": path, "success": True}}


def post(cmd, code, err=""):
    return {"hook_event_name": "PostToolUse", "session_id": "s1",
            "tool_name": "Bash", "tool_input": {"command": cmd},
            "tool_response": {"stdout": "", "stderr": err,
                              "exit_code": code, "timed_out": False}}

def post_noexit(cmd, stdout="", stderr=""):
    """A Bash PostToolUse payload exactly as the harness reports the
    overwhelming majority of real calls: no exit_code key at all.
    Measured on this machine (lane E53, PR 279): 84,659 of 86,907
    recorded outcomes carried no exit_code, and not one of the recorded
    outcomes ever carried a nonzero value."""
    return {"hook_event_name": "PostToolUse", "session_id": "s1",
            "tool_name": "Bash", "tool_input": {"command": cmd},
            "tool_response": {"stdout": stdout, "stderr": stderr,
                              "timed_out": False}}


def last_row(home, payload):
    """The most recently recorded row for payload's own signature, or
    None. Reads the state file directly rather than through the hook, so
    the test can see fields (exit_code, err) the hook's own exit status
    never surfaces."""
    p = state_dir(home) / "s1.jsonl"
    if not p.exists():
        return None
    sig = signature_of(payload)
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in reversed(rows):
        if row.get("sig") == sig:
            return row
    return None


def main():
    with tempfile.TemporaryDirectory() as home:
        os.makedirs(os.path.join(home, ".claude", "repeat-guard"))
        cmd = "pytest tests/test_thing.py -k broken"

        # 1. nothing recorded yet, the work proceeds
        check("first attempt allowed", run(pre(cmd), home)[0], 0)

        # 2. three recorded failures, the fourth is REFUSED with exit 2
        for _ in range(3):
            run(post(cmd, 1, "AssertionError"), home)
        code, _, err = run(pre(cmd), home)
        check("fourth attempt blocked", code, 2)
        check("blocked for the right reason",
              0 if "already failed 3 times" in err else -1, 0)

        # 3. the SAME approach with different volatile noise must count as the
        #    same approach, or the counter never fires at all
        noisy = "pytest /tmp/abc123/tests/test_thing.py -k broken"
        base = "pytest /tmp/zzz999/tests/test_thing.py -k broken"
        for _ in range(3):
            run(post(noisy, 1), home)
        check("volatile paths do not disguise a repeat",
              run(pre(base), home)[0], 2)

        # 4. an approach that SUCCEEDED is not blocked, however often it failed
        #    before. The rule is about what does not work.
        fixed = "make build"
        for _ in range(5):
            run(post(fixed, 2, "boom"), home)
        run(post(fixed, 0), home)
        check("success resets the counter", run(pre(fixed), home)[0], 0)

        # 5. a DIFFERENT approach is never blocked by another's history
        check("a different approach is untouched",
              run(pre("pytest tests/test_other.py"), home)[0], 0)

        # 6. FAILS OPEN. A broken payload must never break the session.
        for bad in ('not json at all', '[]', '{}', '{"hook_event_name":"PreToolUse"}'):
            p = subprocess.run([sys.executable, HOOK], input=bad,
                               capture_output=True, text=True,
                               env={**os.environ, "HOME": home}, timeout=60)
            check(f"fails open on {bad[:28]!r}", p.returncode, 0)

        # 7. PostToolUse never blocks, whatever it sees
        check("post never blocks", run(post(cmd, 1), home)[0], 0)

        # 8. a recorded cross-session lesson WARNS and does not block, because
        #    blocking on a substring match is how three false positives
        #    happened on this machine in one day
        lessons = pathlib.Path(home) / ".claude" / "repeat-guard" / "lessons.jsonl"
        lessons.write_text(json.dumps(
            {"trigger": "git push --force",
             "note": "force push over a shared branch destroyed peer work"}) + "\n")
        code, out, _ = run(pre("git push --force origin main"), home)
        check("a recorded lesson warns", code, 0)
        check("and the warning reaches the model",
              0 if "recorded lesson" in out else -1, 0)

        # 9. SUCCESS ON A NON-BASH TOOL IS RECORDED AS SUCCESS. Fixed
        #    2026-08-25 on the founder's authorisation; these three cases were
        #    written the night before as characterization pins asserting the
        #    BROKEN behaviour, and they did exactly what they were built to do:
        #    the moment the one line changed, they went red and named
        #    themselves as the thing to come and flip.
        #
        #    The defect: success was decided by `ok = (code == 0)` where code is
        #    tool_response's exit_code, and Edit, Write and NotebookEdit never
        #    supply one, so (None == 0) was False and the failure count only
        #    ever climbed. Four successful edits to one file then tripped a
        #    control whose purpose is refusing an approach that FAILED.
        for _ in range(4):
            run(post_edit("/tmp/pin_target.md"), home)
        code, _, _ = run(pre_edit("/tmp/pin_target.md"), home)
        check("9a four SUCCESSFUL edits are allowed, not counted as failures",
              code, 0)

        #    The contrast case. Bash supplies exit_code and always classified
        #    correctly, which is precisely why the defect stayed invisible: the
        #    only tool the suite tested was the only tool that worked.
        for _ in range(4):
            run(post("echo ok", 0), home)
        code, _, _ = run(pre("echo ok"), home)
        check("9b four SUCCESSFUL bash calls are correctly allowed", code, 0)

        # 10. The remaining two registered tools, so this suite's coverage
        #     surface equals the hook's REGISTRATION surface. That equality is
        #     itself asserted by tests/test_truth_claims.py, because a suite
        #     covering one of four registered tools is how the defect above
        #     survived thirteen passing cases.
        for _ in range(4):
            run(post_write("/tmp/pin_write.md"), home)
        code, _, _ = run({"hook_event_name": "PreToolUse", "session_id": "s1",
                          "tool_name": "Write",
                          "tool_input": {"file_path": "/tmp/pin_write.md",
                                         "content": "x"}}, home)
        check("10a four SUCCESSFUL writes are allowed, not counted as failures",
              code, 0)

        for _ in range(4):
            run(post_notebook("/tmp/pin_nb.ipynb"), home)
        code, _, _ = run({"hook_event_name": "PreToolUse", "session_id": "s1",
                          "tool_name": "NotebookEdit",
                          "tool_input": {"notebook_path": "/tmp/pin_nb.ipynb",
                                         "new_source": "x"}}, home)
        check("10b four SUCCESSFUL notebook edits are allowed", code, 0)

        # 11. A HISTORY POISONED BY THE OLD CLASSIFIER MUST NOT BLOCK.
        #     Fixing the classifier released nothing on its own: the counters it
        #     had already poisoned stayed poisoned, and the stuck state is
        #     SELF-SEALING because only a recorded SUCCESS resets a counter and
        #     a refused approach never runs to produce one. 465 of 991 records
        #     on this machine were affected and three signatures were stuck.
        #
        #     The fix is to derive the verdict at READ time from the raw fields
        #     rather than trusting the stored `ok`, so a classifier correction
        #     propagates BACKWARD through every history without rewriting
        #     anyone's log. This case writes exactly what the defect wrote,
        #     four successes stored as ok=False with no exit_code, and asserts
        #     the guard does not refuse.
        poisoned = state_dir(home) / "s1.jsonl"
        poisoned.parent.mkdir(parents=True, exist_ok=True)
        sig_payload = pre_edit("/tmp/poisoned_target.md")
        with poisoned.open("a", encoding="utf-8") as fh:
            for _ in range(4):
                fh.write(json.dumps({
                    "sig": signature_of(sig_payload),
                    "approach": "edit /tmp/poisoned_target.md",
                    "ok": False,          # what the DEFECT stored
                    "exit_code": None,    # the raw fact: no code was reported
                    "err": "",
                }) + "\n")
        code, _, _ = run(pre_edit("/tmp/poisoned_target.md"), home)
        check("11 four poisoned records do not block a fifth attempt", code, 0)

        # 12. THE NEGATIVE CONTROL FOR CASE 11, and without it case 11 could
        #     pass vacuously: if the fixture's signature did not match what the
        #     hook computes, the records would simply be invisible and the
        #     guard would allow the attempt for the wrong reason. Genuine
        #     failures, carrying a real non-zero exit code, MUST still refuse.
        #     A check that cannot fail cannot verify anything.
        genuine = state_dir(home) / "s1.jsonl"
        with genuine.open("a", encoding="utf-8") as fh:
            for _ in range(4):
                fh.write(json.dumps({
                    "sig": signature_of(pre_edit("/tmp/genuine_target.md")),
                    "approach": "edit /tmp/genuine_target.md",
                    "ok": False,
                    "exit_code": 1,      # a REAL failure, reported as one
                    "err": "boom",
                }) + "\n")
        code, _, _ = run(pre_edit("/tmp/genuine_target.md"), home)
        check("12 four GENUINE failures still refuse a fifth attempt", code, 2)

        # 13. THE LEDGER ROW ITSELF NAMES WHAT IT KNOWS. lane E53 / PR 279:
        #     a payload carrying a real exit code writes that number; a
        #     payload carrying none writes exit_code None and SAYS SO in
        #     the stored row, rather than silently guessing a pass the way
        #     the old `rec.get("success") is not False` default did.
        ledger_cmd = "pytest tests/test_ledger_naming.py"
        run(post(ledger_cmd, 1, "boom"), home)
        row = last_row(home, pre(ledger_cmd))
        check("13a exit 1 writes exit_code 1", row.get("exit_code"), 1)

        run(post(ledger_cmd, 0), home)
        row = last_row(home, pre(ledger_cmd))
        check("13b exit 0 writes exit_code 0", row.get("exit_code"), 0)

        run(post_noexit(ledger_cmd, stdout="nothing conclusive here"), home)
        row = last_row(home, pre(ledger_cmd))
        check("13c a payload without an exit writes exit_code None",
              row.get("exit_code"), None)
        check("13c and says so instead of silently guessing a pass",
              row.get("err"),
              "no exit code and no failure signature in output")

        # 14. THE FIX ITSELF. A Bash call that reports no exit_code but whose
        #     output plainly failed must be counted as a failure, not
        #     defaulted to a pass. Three such calls, then the fourth attempt
        #     is refused: the same shape as case 2, but with the exit_code
        #     the harness actually supplies for almost every real call --
        #     none at all.
        broken = "run_missing_tool.sh"
        for _ in range(3):
            run(post_noexit(broken,
                            stderr="bash: run_missing_tool.sh: command not found"),
                home)
        row = last_row(home, pre(broken))
        check("14a an output-inferred failure still writes exit_code None",
              row.get("exit_code"), None)
        code, _, _ = run(pre(broken), home)
        check("14b three output-inferred failures block the fourth attempt",
              code, 2)

        # 15. A GENUINELY UNKNOWN OUTCOME IS NEITHER A PASS NOR A FAILURE.
        #     No exit_code, no failure signature: ten of these must never
        #     trip the guard (it is not a counted failure) and must never
        #     mask a real failure streak either (unlike an actual success,
        #     it does not reset the counter).
        vague = "some_tool --quiet"
        for _ in range(10):
            run(post_noexit(vague, stdout="working on it"), home)
        code, _, _ = run(pre(vague), home)
        check("15a ten unknown outcomes never block", code, 0)

        mixed = "flaky_tool.sh"
        run(post_noexit(mixed, stderr="fatal: could not do the thing"), home)
        run(post_noexit(mixed, stdout="working on it"), home)  # unknown
        run(post_noexit(mixed, stderr="fatal: could not do the thing"), home)
        run(post_noexit(mixed, stdout="working on it"), home)  # unknown
        run(post_noexit(mixed, stderr="fatal: could not do the thing"), home)
        code, _, _ = run(pre(mixed), home)
        check("15b an unknown outcome between failures does not reset the streak",
              code, 2)

    bad = results.count(False)
    print(f"\n{len(results)} cases, {bad} failures")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
