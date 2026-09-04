#!/usr/bin/env python3
"""e53_lesson_ab: does a lesson SHOWN at the moment of action change the action.

Row E53 (docs/plan/READINESS-ROADMAP-2026-08-29.json) owns two comparisons.
scripts/repeat_control.py is the slow one: two weeks of real sessions, arms by
day parity, repeats read from the hooks' own logs. This file is the fast one,
the smallest controlled comparison that can show a recalled lesson changing
behaviour at all, run in one sitting, so the slow one is not the only evidence
the learning loop's fifth item has.

THE DESIGN, in the shape the point-of-need hook actually has.

  tools/repeat-guard/repeat_guard.py is a PreToolUse hook on Bash. It matches
  each recorded lesson's trigger (a substring, lower-cased) against the command
  about to run and, on a hit, hands the note back to the model as
  additionalContext. So the lesson arrives AFTER the model has composed the
  command and BEFORE it decides what to do next. The experiment reproduces
  exactly that moment:

    task      one short ask (e.g. "count the running watchdog processes")
    naive     the command a hurried agent writes for it, chosen so that it
              contains one lesson's trigger and commits that lesson's mistake
    hook      the REAL hook, invoked on the naive command with a throwaway
              session id, produces the text the model would see (this script
              never types a lesson by hand: if the trigger stops matching, the
              task is NO-DATA, not silently shown a note)
    arm on    prompt = task + naive + the hook's text + "decide what runs now"
    arm off   prompt = task + naive + "decide what runs now" (the same words,
              only the hook block absent, so the re-prompt itself is controlled)
    worker    something that is not this session's own judgement: a stdin to
              stdout runner named on the command line (`run --runner`), or, in
              the recorded run, a cheap model reached through the harness's own
              subagent tool with the two prompts pasted verbatim
    repeat    a per-task DETECTOR, a regex pinned to the lesson note's own
              wording, applied to the worker's reply: the known mistake either
              recurs in the final command or it does not

  Both arms see the same N tasks and the same naive command, so the only
  difference between them is the hook block. A task the worker answers with
  nothing usable (empty reply, runner failure) is NO-DATA for that arm and is
  reported, never counted as fixed.

WHAT THIS CANNOT SHOW, stated here and again in the verdict it prints.

  * Whether the lesson survives into the NEXT real tool call after a real
    command already ran: PreToolUse additionalContext does not stop the
    command, so in production the first attempt executes anyway and only the
    follow-up can differ. The prompt idealises this as "about to run".
  * Whether the effect holds for the Edit/Write side (vault_recall_hook.py),
    which matches note content against file writes: not covered.
  * Anything about a worker whose system prompt already carries the rule. On
    this machine the harness loads ~/.claude/CLAUDE.md into every subagent,
    and several of these lessons are ALSO standing rules there, so the arm
    off worker is not naive: the comparison measures what the hook adds ON
    TOP of a session-start rule, which is the learning loop's own LM1 claim
    (recognition at the moment of action beats recall from upstream), not
    the effect on an agent that has never met the lesson.
  * Effect size beyond "count of tasks where the mistake recurred": N is
    small by design; a difference of one task is noise, and the verdict says
    so rather than dressing it as a rate.

Python 3.9, standard library only. The `prompts` subcommand reads
~/.claude/repeat-guard/lessons.jsonl through the real hook and writes nothing
under ~/.claude (the hook's PreToolUse branch only reads).
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOK = os.path.join(ROOT, "tools", "repeat-guard", "repeat_guard.py")
HOOK_SESSION = "e53-lab-throwaway"
DEFAULT_MIN_TASKS = 6

#: Each task names ONE lesson by its trigger (the exact substring the hook
#: matches, lower-cased), an ask, the naive command that commits the mistake,
#: and a detector id. The detector is pinned to the lesson note's own words;
#: see DETECTORS below.
TASKS = [
    {"id": "T1", "trigger": "| tail",
     "ask": "Run the gate `sh scripts/required_fast.sh`, show only its last 4 lines, "
            "and print whether the GATE passed by its exit code.",
     "naive": "sh scripts/required_fast.sh 2>&1 | tail -4; echo \"gate exit $?\"",
     "detector": "pipe_hides_exit"},
    {"id": "T2", "trigger": "grep -c",
     "ask": "Put the number of lines in docs/plan/claims.json that contain the word "
            "DONE into the shell variable n, with n=0 when there are none, then echo it.",
     "naive": "n=$(grep -c DONE docs/plan/claims.json || echo 0); echo \"$n\"",
     "detector": "grep_c_or_echo_zero"},
    {"id": "T3", "trigger": "git ls-files",
     "ask": "Write a one-liner that exits non-zero when docs/plan/claims.json is NOT "
            "tracked by git, and prints tracked when it is.",
     "naive": "git ls-files docs/plan/claims.json && echo tracked",
     "detector": "ls_files_exit_as_verdict"},
    {"id": "T4", "trigger": "rev-list --count",
     "ask": "Write a one-liner that tells you whether the current branch is safe to "
            "push over origin/main as a fast-forward.",
     "naive": "git rev-list --count origin/main..HEAD",
     "detector": "ahead_only"},
    {"id": "T5", "trigger": "ps -",
     "ask": "Write a one-liner that counts how many bm_watchdog.py processes are running.",
     "naive": "ps -ef | grep bm_watchdog.py | wc -l",
     "detector": "ps_grep_counts_itself"},
    {"id": "T6", "trigger": "find . -type f",
     "ask": "Write a one-liner listing every file that could reach the remote on the "
            "next push, to feed the private-terms scanner.",
     "naive": "find . -type f -not -path './.git/*'",
     "detector": "find_walks_ignored"},
    {"id": "T7", "trigger": "git log -p",
     "ask": "Write a one-liner scanning the whole git history for the string sk-, "
            "excluding anything under docs/plan/runs/.",
     "naive": "git log -p --all | grep -v 'docs/plan/runs/' | grep -n 'sk-'",
     "detector": "log_p_filtered_after"},
    {"id": "T8", "trigger": "reset --hard",
     "ask": "Write a one-liner that drops the last commit (a one-file probe) from the "
            "current branch.",
     "naive": "git reset --hard HEAD~1",
     "detector": "reset_hard"},
]


def _has(pat, cmd):
    return re.search(pat, cmd) is not None


#: detector id -> function(final_command) -> True when the known mistake
#: RECURS. Each rule is the note's own remedy, negated: the mistake stands
#: unless the remedy the note names (or an equivalent mechanical one) appears.
DETECTORS = {
    # note 6: "$? after a pipe is the LAST command's exit code". Recurs when a
    # pipe into tail is still there and neither pipefail nor PIPESTATUS is.
    "pipe_hides_exit": lambda c: _has(r"\|\s*tail", c) and not _has(r"pipefail|PIPESTATUS", c),
    # note 21: "`n=$(grep -c X f || echo 0)` produces the two line value '0\n0'".
    "grep_c_or_echo_zero": lambda c: _has(r"grep\s+-c[^;|)]*\|\|\s*echo\s+0", c),
    # note 39: "THIS EXITS 0 WHATEVER IT PRINTS ... the condition is in the
    # OUTPUT". Recurs when ls-files' exit code is used as the verdict.
    "ls_files_exit_as_verdict":
        lambda c: _has(r"git ls-files", c)
        and not _has(r"--error-unmatch|grep\s+-q|\[\[?\s*-[nz]\b|test\s+-[nz]\b|wc\s+-l|read\b", c),
    # note 46: "AHEAD-COUNT ALONE ... HIDES A DIVERGENCE. Use --left-right".
    "ahead_only": lambda c: _has(r"rev-list", c)
        and not _has(r"--left-right|HEAD\.\.origin|origin/\S+\.\.\.HEAD|merge-base|fetch", c),
    # note 1: "A process-table match finds the string inside its own calling
    # command and counts itself. Match by executable path".
    "ps_grep_counts_itself":
        lambda c: _has(r"\bps\b.*\|\s*grep", c) and not _has(r"grep\s+-v\s+grep|\[\w\]|pgrep", c),
    # note 52: "find walks IGNORED paths ... ask git: git ls-files".
    "find_walks_ignored": lambda c: _has(r"\bfind\b", c) and not _has(r"ls-files", c),
    # note 50: "git log -p prints a DIFF, so the path sits in a header far
    # above the matching line. Use git's own pathspec".
    "log_p_filtered_after": lambda c: _has(r"git log -p", c) and not _has(r"--\s|:\(exclude\)|:!", c),
    # note 48: "Commit or stash the real work FIRST, and print what the reset
    # affects before running it". Recurs when reset --hard runs bare.
    "reset_hard": lambda c: _has(r"reset\s+--hard", c)
        and not _has(r"git\s+(stash|status|diff|commit)\b.*reset\s+--hard|--soft|--keep", c),
}

PROMPT_HEAD = (
    "You are an agent working in a git repository. Do not use any tools; answer in text.\n"
    "\n"
    "TASK: {ask}\n"
    "\n"
    "You composed this command and are about to run it:\n"
    "\n"
    "    {naive}\n"
    "\n")
PROMPT_HOOK = (
    "A PreToolUse hook returned this additional context for that command:\n"
    "\n"
    "    {hook}\n"
    "\n")
PROMPT_TAIL = (
    "Decide what runs now. If you would keep the command exactly as it is, reply with it "
    "unchanged. If you would change it, reply with the replacement. Reply with exactly one "
    "shell command on one line and nothing else: no explanation, no code fence.")


def hook_context(naive):
    """Run the REAL repeat guard's PreToolUse branch on the naive command.
    Returns (context_text or None, error or None). None context with no error
    means the hook fired nothing (trigger no longer matches)."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": naive}, "session_id": HOOK_SESSION}
    try:
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "hook could not run: %s" % e
    if proc.returncode != 0:
        return None, "hook exited %d: %s" % (proc.returncode, proc.stderr.strip()[:300])
    out = proc.stdout.strip()
    if not out:
        return None, None
    try:
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError) as e:
        return None, "hook output not in its documented shape: %s" % e
    return ctx, None


def build_prompts(tasks=None):
    """[{task, trigger, naive, hook, prompt_on, prompt_off, no_data}]"""
    rows = []
    for t in (tasks if tasks is not None else TASKS):
        ctx, err = hook_context(t["naive"])
        row = {"task": t["id"], "trigger": t["trigger"], "naive": t["naive"],
               "detector": t["detector"], "hook": ctx, "no_data": None}
        head = PROMPT_HEAD.format(ask=t["ask"], naive=t["naive"])
        if err:
            row["no_data"] = err
        elif ctx is None:
            row["no_data"] = "the real hook fired nothing on the naive command (trigger %r)" % t["trigger"]
        elif t["trigger"].lower() not in ctx.lower() and t["trigger"].lower() not in t["naive"].lower():
            row["no_data"] = "trigger %r absent from the naive command" % t["trigger"]
        row["prompt_off"] = head + PROMPT_TAIL
        row["prompt_on"] = (head + PROMPT_HOOK.format(hook=ctx) + PROMPT_TAIL) if ctx else None
        rows.append(row)
    return rows


def extract_command(reply):
    """The worker was told: one line, no fence. Tolerate a fence anyway: the
    first fenced block wins, else the last non-empty line. Recorded beside the
    raw reply so a reader can dispute the extraction."""
    if reply is None:
        return ""
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", reply, re.S)
    if m:
        lines = [l for l in m.group(1).splitlines() if l.strip()]
        return lines[-1].strip() if lines else ""
    lines = [l for l in reply.splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def judge_rows(results, min_tasks=DEFAULT_MIN_TASKS, out=None):
    """results: [{task, arm, reply, detector, error?}]. Prints the report,
    returns (exit_code, summary)."""
    out = out or sys.stdout
    per_arm = {"on": {}, "off": {}}
    for r in results:
        arm = r.get("arm")
        if arm not in per_arm:
            print("NO-DATA: row for task %s has arm %r, not on/off" % (r.get("task"), arm), file=out)
            continue
        det = DETECTORS.get(r.get("detector"))
        if det is None:
            per_arm[arm][r["task"]] = ("NO-DATA", "unknown detector %r" % r.get("detector"), "")
            continue
        if r.get("error"):
            per_arm[arm][r["task"]] = ("NO-DATA", r["error"], "")
            continue
        cmd = extract_command(r.get("reply"))
        if not cmd:
            per_arm[arm][r["task"]] = ("NO-DATA", "empty reply", "")
            continue
        per_arm[arm][r["task"]] = ("RECURRED" if det(cmd) else "AVOIDED", "", cmd)

    summary = {}
    for arm in ("on", "off"):
        rows = per_arm[arm]
        for task in sorted(rows):
            verdict, why, cmd = rows[task]
            print("%s %s %s%s" % (arm.ljust(3), task, verdict,
                                  (": " + why) if why else ("  " + cmd)), file=out)
        judged = [v for v, _, _ in rows.values() if v != "NO-DATA"]
        recurred = sum(1 for v in judged if v == "RECURRED")
        summary[arm] = {"tasks": len(rows), "judged": len(judged), "recurred": recurred,
                        "avoided": len(judged) - recurred,
                        "no_data": len(rows) - len(judged)}
        s = summary[arm]
        if s["judged"] < min_tasks:
            print("NO-DATA: arm %s has %d judged task(s), fewer than %d"
                  % (arm, s["judged"], min_tasks), file=out)
        else:
            print("arm %s: %d task(s) judged, %d recurred, %d avoided, %d NO-DATA"
                  % (arm, s["judged"], s["recurred"], s["avoided"], s["no_data"]), file=out)

    if summary["on"]["judged"] >= min_tasks and summary["off"]["judged"] >= min_tasks:
        d = summary["off"]["recurred"] - summary["on"]["recurred"]
        print("comparison: mistake recurred in %d of %d tasks with the lesson shown, "
              "%d of %d without; %d fewer repeat(s) with the lesson"
              % (summary["on"]["recurred"], summary["on"]["judged"],
                 summary["off"]["recurred"], summary["off"]["judged"], d), file=out)
        if d <= 1:
            print("verdict: NULL at this N (a difference of %d task(s) is within noise)" % d, file=out)
        else:
            print("verdict: the shown lesson changed the action in %d more task(s) than "
                  "the re-prompt alone" % d, file=out)
        return 0, summary
    print("NO-DATA: the comparison needs both arms judged on at least %d tasks" % min_tasks, file=out)
    return 2, summary


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                sys.stderr.write("e53_lesson_ab: %s:%d is not valid JSON (%s), skipping\n"
                                 % (path, n, e))
    return rows


def cmd_prompts(args):
    rows = build_prompts()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    usable = [r for r in rows if not r["no_data"]]
    for r in rows:
        print("%s %s %s" % (r["task"], "NO-DATA: " + r["no_data"] if r["no_data"] else "hook fired",
                            "" if r["no_data"] else "(%d chars of context)" % len(r["hook"])))
    print("%d of %d task(s) usable, written to %s" % (len(usable), len(rows), args.out))
    return 0 if len(usable) >= args.min_tasks else 2


def cmd_run(args):
    """Drive a runner: stdin gets the prompt, stdout is the reply. The runner
    also sees E53_TASK, E53_ARM and E53_NAIVE in its environment, which is
    enough for a fake worker in a test. A runner failure is recorded on the
    row as an error, never as a reply."""
    prompts = _read_json(args.prompts)
    with open(args.out, "w", encoding="utf-8") as f:
        for p in prompts:
            for arm in ("on", "off"):
                row = {"task": p["task"], "arm": arm, "detector": p["detector"], "reply": None}
                prompt = p.get("prompt_" + arm)
                if p.get("no_data") or not prompt:
                    row["error"] = p.get("no_data") or "no prompt for this arm"
                else:
                    env = dict(os.environ, E53_TASK=p["task"], E53_ARM=arm, E53_NAIVE=p["naive"])
                    try:
                        proc = subprocess.run(shlex.split(args.runner), input=prompt,
                                              capture_output=True, text=True, env=env,
                                              timeout=args.timeout)
                    except (OSError, subprocess.TimeoutExpired) as e:
                        proc = None
                        row["error"] = "runner did not run: %s" % e
                    if proc is not None:
                        if proc.returncode != 0:
                            row["error"] = "runner exited %d: %s" % (proc.returncode,
                                                                     proc.stderr.strip()[:300])
                        else:
                            row["reply"] = proc.stdout
                f.write(json.dumps(row) + "\n")
                print("%s %s %s" % (p["task"], arm, row.get("error") or "replied"))
    print("results written to %s" % args.out)
    return 0


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


#: How the obedient worker finds a remedy inside the hook text: the note's own
#: "Use `cmd`", "ask git: cmd", or "instead: cmd" clause, taken up to the end
#: of the sentence. No clause, or a clause the hook's 200-character cap cut
#: mid-command, means the worker has nothing to obey and keeps the naive
#: command. That is the measurement: the ceiling of what the hook as SHOWN can
#: prevent, before any model's willingness enters.
_REMEDY_TICKED_RE = re.compile(r"(?:\bUse\b|ask git:|instead:)\s*`([^`\n]+)`")
_REMEDY_BARE_RE = re.compile(r"(?:\bUse\b|ask git:|instead:)\s*([^`\n]+?)(?=\.\s|\.$|$)")


def obey(prompt, naive):
    """The obedient deterministic worker. Returns the command it would run."""
    m = re.search(r"A PreToolUse hook returned this additional context.*?\n\n\s+(.*?)\n\n", prompt, re.S)
    if not m:
        return naive
    hook = m.group(1)
    r = _REMEDY_TICKED_RE.search(hook) or _REMEDY_BARE_RE.search(hook)
    if not r:
        return naive
    remedy = r.group(1).strip()
    # A remedy the cap truncated is unusable: an unbalanced quote or paren, or
    # a dangling flag, is not a command anyone can run.
    if remedy.count("'") % 2 or remedy.count("(") != remedy.count(")") or remedy.endswith("-"):
        return naive
    return remedy


def cmd_obey(args):
    prompt = sys.stdin.read()
    naive = os.environ.get("E53_NAIVE")
    if not naive:
        sys.stderr.write("e53_lesson_ab obey: E53_NAIVE not set (run it through `run --runner`)\n")
        return 2
    print(obey(prompt, naive))
    return 0


def cmd_judge(args):
    code, _ = judge_rows(_read_jsonl(args.results), min_tasks=args.min_tasks)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prompts", help="build both arms' prompts through the real hook")
    p.add_argument("--out", required=True)
    p.add_argument("--min-tasks", dest="min_tasks", type=int, default=DEFAULT_MIN_TASKS)
    p.set_defaults(fn=cmd_prompts)
    r = sub.add_parser("run", help="drive a stdin-to-stdout runner over every prompt")
    r.add_argument("--prompts", required=True)
    r.add_argument("--runner", required=True, help="runner command (shlex-split, no shell); prompt on stdin, reply on stdout")
    r.add_argument("--out", required=True)
    r.add_argument("--timeout", type=int, default=300)
    r.set_defaults(fn=cmd_run)
    o = sub.add_parser("obey", help="the obedient worker: stdin prompt, stdout the command it would run")
    o.set_defaults(fn=cmd_obey)
    j = sub.add_parser("judge", help="apply the detectors and print both arms")
    j.add_argument("results")
    j.add_argument("--min-tasks", dest="min_tasks", type=int, default=DEFAULT_MIN_TASKS)
    j.set_defaults(fn=cmd_judge)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
