#!/usr/bin/env python3
"""NIGHT-01: the real coding-model worker for the graph loop.

WHAT THIS IS. This is the program loop_bridge.LaneWorker spawns as one unit's
worker (scripts/loop_bridge.py, LaneWorker/SpawningWorker). It is invoked with
cwd already set to the unit's lane worktree, so every write it makes lands in
that lane by construction and never needs to say so.

THE CONTRACT, copied from bm_worker_spawn.SpawningWorker rather than
reinvented: the brief arrives as ONE JSON object on stdin, the answer leaves
as ONE JSON object on stdout with worker_claim and artifacts, and every log
line goes to stderr because stdout is reserved for that one object. Exit 0
with unreadable stdout reads as "malformed"; nonzero exit reads as
"unavailable". Both are the caller's business, not this file's: this file
just has to keep the two paths distinct, which is why the model-timeout and
model-nonzero paths below exit 3 (unavailable) rather than ever emitting a
half-formed result on stdout.

READING THE BRIEF DEFENSIVELY. The estate's own recorded lesson is that a
translation above a stage silently drops fields (see the graph_loop /
loop_bridge node-shape defect scripts/test_spine.py's docstring names). A
brief here is read with .get and a default of "", never indexed, so a field
this stage does not recognize costs a blank line in the prompt rather than a
KeyError that turns the whole unit into "unavailable" for a reason with
nothing to do with the model.

WHAT RUNS THE MODEL. MODEL_WORKER_CMD, split with shlex, if set; otherwise the
claude CLI headless: `claude -p --output-format json --permission-mode
acceptEdits` (flags verified against `claude --help` on this machine on
2026-08-30; --output-format json verified live on 2026-09-02 with `env -u
CLAUDECODE claude -p --output-format json "Reply with the single word ok"`,
which returned one JSON object carrying "result" and a "usage" sub-object
with input_tokens, output_tokens, cache_read_input_tokens and
cache_creation_input_tokens, never invented). The prompt text is appended as
the final argv element. The child's cwd is inherited (not overridden),
because the lane cwd was already set by whoever started this process
(LaneWorker/SpawningWorker), and setting it again here would be a second,
possibly stale, opinion about where the work happens.

READING THE MODEL'S OWN ANSWER. --output-format json makes the model
command's stdout one JSON object rather than plain text, and this worker's
own claim text and real usage both come out of it: "result" becomes the
claim text, and "usage" is renamed into the tokens_in / tokens_out /
tokens_cached names build_cost_block already sums (scripts/brother_run.py),
tokens_cached read from cache_read_input_tokens since that is the count of
tokens the model actually reused rather than paid for fresh. Anything that
is not that shape (a test's plain-text stub via MODEL_WORKER_CMD, a
malformed line) falls back to the raw stdout as the claim and NO usage: a
worker that cannot read a structured answer must never invent token counts
for it, so "cost" in the result below stays {} exactly as it did before this
file read usage at all.

ARTIFACTS. Collected from `git status --porcelain` in the cwd after the model
exits 0, staged and unstaged alike, path only. A rename line ("R  old -> new")
reports the new path, since that is the file that now exists.

DONE_CHECK. If the unit carried one, it is run in the cwd with its own
300s timeout and its exit code is recorded in the claim. A done_check that
cannot even be started (missing interpreter, bad shell) is reported as
NO-DATA, never as a pass: a check that did not run has proven nothing.

Python 3.9, standard library only. No network calls of its own; the model
invocation is a subprocess, not an import.
"""
import json
import os
import shlex
import subprocess
import sys

DEFAULT_TIMEOUT_S = 1200
DEFAULT_DONE_CHECK_TIMEOUT_S = 300

NO_DASH_INSTRUCTION = (
    "Work in the current directory only. Never push, and never commit "
    "outside this lane's own branch. Never add AI attribution (no "
    "Co-Authored-By, no generated-with line, no credit or watermark of any "
    "kind). Never use em dashes or en dashes anywhere in code, comments, "
    "commit messages, or output; use commas, colons, or parentheses instead."
)


def _read_brief():
    """All of stdin, parsed as JSON. Never partial: a truncated read here
    would silently hand the model half a brief and no error at all."""
    raw = sys.stdin.read()
    return json.loads(raw)


def build_prompt(brief):
    """One plain-text prompt, every field read defensively so a brief missing
    a key costs a blank line here rather than a crash."""
    unit_id = brief.get("id") or brief.get("unit_id") or ""
    objective = brief.get("objective") or brief.get("title") or ""
    done_check = brief.get("done_check") or ""
    # THE FIELD-NAME GAP THIS FILE'S OWN DOCSTRING WARNS ABOUT: loop_bridge's
    # real run_node() sends the write scope as "write_scope", not "writes" or
    # "owns" (those are the names door.py and work_record.py use one stage
    # earlier). Found wiring P0.2, the default worker, where a real brief
    # never carried either of the two names this used to check, so the model
    # was never told its own write scope through the real spine, only in a
    # hand-built test brief.
    writes = (brief.get("writes") or brief.get("owns")
             or brief.get("write_scope") or [])
    if not isinstance(writes, list):
        writes = [writes]
    notes = brief.get("notes") or ""
    # THE GAP THIS FIXES (measured 2026-08-31, E7): bm_repair.py builds a rich
    # prior_failure_note and recalled_lesson for every retry (its own
    # docstring: "a brief that RECORDS the failed approach so the retry is a
    # different attempt, not a third identical one"), and bm_worker_spawn.py
    # ships the whole brief to this process as JSON on stdin. This function
    # read neither field, so every retry, in-lane or across an outer round,
    # built the IDENTICAL prompt as attempt 1: no attempt number, no note, no
    # lesson. A repair loop that always asks the same question gets the same
    # answer, which is why a failing unit made zero repair progress across
    # four claims in the external evaluator's run.
    try:
        attempt_n = int(brief.get("attempt") or 1)
    except (TypeError, ValueError):
        attempt_n = 1
    prior_note = brief.get("prior_failure_note") or ""
    recalled = brief.get("recalled_lesson") or ""

    lines = [
        "You are the worker for one unit of a graph-loop run.",
        "",
        "Unit id: %s" % unit_id,
        "Objective: %s" % objective,
    ]
    if writes:
        lines.append("Declared write scope: %s" % ", ".join(str(w) for w in writes))
        lines.append(
            "Writing outside this declared scope means the unit is "
            "quarantined: stay inside these paths."
        )
    else:
        lines.append(
            "No write scope was declared for this unit; make no changes "
            "outside what the objective plainly requires."
        )
    if done_check:
        lines.append("Done check (must pass when you are finished): %s" % done_check)
    if notes:
        lines.append("Notes: %s" % notes)
    if attempt_n > 1 or prior_note:
        lines.append("")
        lines.append("This is attempt %d. A previous attempt on this exact "
                     "unit did not pass." % attempt_n)
    if prior_note:
        lines.append("What the previous attempt did and why it did not pass: "
                     "%s" % prior_note)
        lines.append("Do not repeat that approach unchanged; make a "
                     "genuinely different change that addresses the actual "
                     "failure above.")
    if recalled:
        lines.append("A relevant lesson recalled from prior work: %s" % recalled)
    lines.append("")
    lines.append(NO_DASH_INSTRUCTION)
    return "\n".join(lines)


def _default_argv():
    return ["claude", "-p", "--output-format", "json", "--permission-mode",
            "acceptEdits"]


#: usage's own keys (Anthropic's CLI JSON result), renamed to what
#: build_cost_block sums (scripts/brother_run.py COST_FIELDS). tokens_cached
#: reads cache_read_input_tokens: the count of tokens the model actually
#: reused from cache, which is what a cache HIT rate means.
USAGE_FIELD_MAP = {"tokens_in": "input_tokens", "tokens_out": "output_tokens",
                    "tokens_cached": "cache_read_input_tokens"}


def _parse_model_output(raw):
    """(claim_text, usage_or_None) from the model command's stdout.

    With --output-format json a real `claude -p` answer is one JSON object
    carrying "result" (the text) and a "usage" sub-object. This reads both,
    renamed to the names build_cost_block already sums. Anything else (a
    test's plain-text stub via MODEL_WORKER_CMD, a malformed line, an
    unexpected shape) falls back to the raw text as the claim and NO usage:
    a worker that cannot read a structured answer must never invent token
    counts for it."""
    text = (raw or "").strip()
    if not text:
        return "(model produced no stdout)", None
    try:
        parsed = json.loads(text)
    except ValueError:
        return text, None
    if not isinstance(parsed, dict):
        return text, None
    result_text = parsed.get("result")
    claim = str(result_text) if result_text is not None else text
    raw_usage = parsed.get("usage")
    usage = {}
    if isinstance(raw_usage, dict):
        for field, cli_key in USAGE_FIELD_MAP.items():
            val = raw_usage.get(cli_key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                usage[field] = val
    return claim, (usage or None)


def _model_argv(prompt):
    cmd = os.environ.get("MODEL_WORKER_CMD")
    argv = shlex.split(cmd) if cmd else _default_argv()
    return argv + [prompt]


def _timeout_s():
    raw = os.environ.get("MODEL_WORKER_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S


def run_model(prompt, cwd=None, runner=None):
    """Invokes the model command. Returns (ok, claim_text_or_reason, usage).

    ok is False on nonzero exit or timeout; the caller's job is to turn that
    into exit 3 without ever writing to stdout, since a nonzero/timeout
    result carries nothing readable as a worker_claim. usage is None
    whenever the model's stdout could not be read as --output-format json's
    shape (see _parse_model_output); it is never a fabricated number."""
    runner = runner or subprocess.run
    argv = _model_argv(prompt)
    try:
        completed = runner(argv, cwd=cwd, capture_output=True, text=True,
                            timeout=_timeout_s())
    except subprocess.TimeoutExpired:
        return False, ("model command timed out after %ss: %s"
                        % (_timeout_s(), argv)), None
    except OSError as exc:
        return False, "could not start model command %r: %s" % (argv, exc), None

    if completed.returncode != 0:
        return False, ("model command exited %s: %s"
                        % (completed.returncode,
                           (completed.stderr or completed.stdout or "").strip()[:400])), None
    claim, usage = _parse_model_output(completed.stdout)
    return True, claim, usage


def collect_artifacts(cwd, runner=None):
    """Paths from `git status --porcelain`, staged and unstaged, path only.
    A rename line's NEW path is what now exists, so that is what is kept."""
    runner = runner or subprocess.run
    try:
        completed = runner(["git", "status", "--porcelain"], cwd=cwd,
                            capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print("model_worker: could not read git status in %r: %s" % (cwd, exc),
              file=sys.stderr)
        return []
    if completed.returncode != 0:
        print("model_worker: git status exited %s: %s"
              % (completed.returncode, (completed.stderr or "").strip()),
              file=sys.stderr)
        return []

    paths = []
    for line in (completed.stdout or "").splitlines():
        if not line:
            continue
        # porcelain format: XY<space>path  (or "XY path -> newpath" on rename)
        path_part = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        path_part = path_part.strip().strip('"')
        if path_part:
            paths.append(path_part)
    return paths


def commit_changes(cwd, unit_id, runner=None):
    """git add -A; git commit, if there is anything staged. Returns
    (committed, detail); never raises.

    NEEDED so the lane's branch actually advances: nothing else in the spine
    commits a unit's work (test_spine.py's own stub worker does this by hand),
    and integrate.py merges the lane BRANCH, not its working tree, so
    uncommitted writes would integrate as nothing at all."""
    runner = runner or subprocess.run
    try:
        # Bytecode never travels in a lane: a committed .pyc collides with the
        # same path sitting untracked on canonical (left by integrate's own
        # check run) and the merge refuses, which starved a correct unit three
        # attempts in a row on the first crash-resume arc (2026-08-30).
        added = runner(["git", "add", "-A", "--",
                        ":(exclude,glob)**/__pycache__/**",
                        ":(exclude,glob)**/*.pyc",
                        ":(exclude,glob)**/*.pyo"],
                       cwd=cwd, capture_output=True,
                       text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, "could not stage changes: %s" % exc
    if added.returncode != 0:
        return False, "git add failed: %s" % (added.stderr or "").strip()[:200]

    try:
        staged = runner(["git", "status", "--porcelain"], cwd=cwd,
                        capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, "could not check staged changes: %s" % exc
    if not (staged.stdout or "").strip():
        return True, "nothing to commit for unit %s" % unit_id

    try:
        committed = runner(["git", "commit", "-q", "-m",
                            "unit %s: model worker" % unit_id],
                           cwd=cwd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, "could not commit: %s" % exc
    if committed.returncode != 0:
        return False, "git commit failed: %s" % (committed.stderr or "").strip()[:200]
    return True, "committed unit %s's changes" % unit_id


def run_done_check(done_check, cwd, runner=None):
    """Returns (ran, exit_code_or_None). ran is False, exit_code None, when
    the check itself could not be started; that is reported as NO-DATA by
    the caller, never as a pass."""
    if not done_check:
        return True, None
    runner = runner or subprocess.run
    try:
        completed = runner(done_check, cwd=cwd, shell=True,
                            capture_output=True, text=True,
                            timeout=DEFAULT_DONE_CHECK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print("model_worker: done_check timed out after %ss: %s"
              % (DEFAULT_DONE_CHECK_TIMEOUT_S, done_check), file=sys.stderr)
        return False, None
    except OSError as exc:
        print("model_worker: could not start done_check %r: %s"
              % (done_check, exc), file=sys.stderr)
        return False, None
    # Exit 127 is the shell's "command not found". That is a missing-TOOL
    # problem on this machine, never a defect in the code the unit wrote, so
    # it must not be reported as a repairable failure the drain retries to its
    # ceiling (the harsh EVAD 2026-08-31 lost five of six lanes exactly here).
    # ran=False makes the caller treat it as NO-DATA with a named reason.
    if completed.returncode == 127:
        print("model_worker: done_check command not found (exit 127): %s. "
              "This is a missing tool on this machine, not a code defect; not "
              "retried as repairable." % done_check, file=sys.stderr)
        return False, None
    return True, completed.returncode


def main(argv=None):  # noqa: ARG001 (argv kept for a hand-run --selftest shape)
    try:
        brief = _read_brief()
    except ValueError as exc:
        print("model_worker: stdin is not valid JSON: %s" % exc, file=sys.stderr)
        return 3
    if not isinstance(brief, dict):
        print("model_worker: brief must be a JSON object, got %s"
              % type(brief).__name__, file=sys.stderr)
        return 3

    cwd = os.getcwd()
    prompt = build_prompt(brief)

    print("model_worker: invoking model for unit %s" % brief.get("id", ""),
          file=sys.stderr)
    ok, model_out, usage = run_model(prompt, cwd=cwd)
    if not ok:
        print("model_worker: %s" % model_out, file=sys.stderr)
        return 3

    artifacts = collect_artifacts(cwd)

    unit_id = brief.get("id") or brief.get("unit_id") or ""
    committed, commit_detail = commit_changes(cwd, unit_id)
    if not committed:
        print("model_worker: %s" % commit_detail, file=sys.stderr)

    done_check = brief.get("done_check") or ""
    claim_parts = [model_out or "(model produced no stdout)", commit_detail]
    if done_check:
        ran, code = run_done_check(done_check, cwd)
        if not ran:
            claim_parts.append("done_check could not be run: NO-DATA")
        else:
            claim_parts.append("done_check exit code: %s" % code)
    else:
        claim_parts.append("no done_check was declared for this unit")

    result = {
        "worker_claim": " | ".join(claim_parts),
        "artifacts": artifacts,
        # THE REAL COUNT WHEN THE CLI GAVE ONE, never a fabricated one: {}
        # when --output-format json's answer could not be read (a stub in a
        # test, a malformed line), exactly as before this file read usage at
        # all (see _parse_model_output).
        "cost": usage or {},
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
