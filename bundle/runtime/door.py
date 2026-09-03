#!/usr/bin/env python3
"""door: a plain English outcome becomes a canonical Work document.

NIGHT-02, the decomposition seam. work_record.py said plainly what it does
not do: it does not decompose English, because a script pretending to would
produce confident nonsense with a done_check attached. This is the other
half, and it still does not decompose English itself. It hands the outcome
to a model (or any command that speaks the same contract), and hands that
answer straight to work_record's own validation, which refuses anything
that would break the scheduler downstream. This module reimplements none of
that checking; it only drives the conversation until the answer passes it,
or gives up and says why.

Decomposer command, in order: --model-cmd, else env DOOR_MODEL_CMD, else the
claude CLI headless in print mode (`claude -p`, reading the prompt on
stdin: verified against `claude --help` on 2026-08-30, -p/--print with no
positional prompt reads from stdin, output-format defaults to plain text on
stdout).

REFUSAL IS STILL THE FEATURE, borrowed whole from work_record.py: a refusal
is reported back to the decomposer verbatim and it gets another attempt, up
to --max-retries times, and after that the run fails loudly rather than
storing something unschedulable.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import work_record as WR  # noqa: E402

NODATA = "NO-DATA"

#: EVAD plan E2: the ceiling above which an "outcome" is really a pasted
#: document. 2,000 characters holds every real outcome sentence this estate
#: has recorded while refusing the 20,000-character probe that ran a model
#: call past two minutes.
MAX_OUTCOME_CHARS = 2000
DEFAULT_MODEL_CMD = ["claude", "-p"]
UNIT_COUNT_HINT = "2 to 9 units"


#: How many existing paths to name in the prompt. A bound, not a real limit
#: on the repo: naming every file in a large tree would blow the outcome
#: budget this module already enforces elsewhere, and the decomposer only
#: needs enough to stop guessing a filename that does not exist.
MAX_LISTED_FILES = 200


def list_repo_files(root, limit=MAX_LISTED_FILES):
    """Repository-relative paths under `root`, `.git` and generated noise
    excluded, for grounding the decomposer in what is ACTUALLY there.

    Returns [] rather than raising when `root` cannot be walked: an outcome
    is still decomposable without this, just more likely to guess a path
    that is not real (measured live 2026-08-31, E7: a decomposer blind to
    the target wrote a unit whose write scope was a bare '*.py' glob, since
    it could not name the one file that actually existed)."""
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                          if d not in (".git", "__pycache__", "node_modules")]
            for name in filenames:
                if name.endswith((".pyc", ".pyo")):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                out.append(rel.replace(os.sep, "/"))
                if len(out) >= limit:
                    return sorted(out)
    except OSError:
        return []
    return sorted(out)


def build_prompt(outcome, refusal=None, existing_files=None):
    """The unit contract, stated plainly, in the words a model reads rather
    than the keys work_record.py stores. normalize_unit() below is the
    bridge between the two vocabularies."""
    lines = [
        "Outcome: %s" % outcome,
        "",
    ]
    if existing_files:
        lines.append("Files that already exist in the target repository "
                     "(name these exactly when a unit changes one of them, "
                     "rather than guessing a plausible name):")
        lines.extend("  %s" % p for p in existing_files)
        lines.append("")
    elif existing_files is not None:
        lines.append("The target repository has no files yet; every unit "
                     "writes a new path.")
        lines.append("")
    lines += [
        "Decompose this outcome into %s of work that a scheduler can run."
        % UNIT_COUNT_HINT,
        "Each unit costs one full separate turn to run and verify, so use "
        "the FEWEST units the real dependency structure requires. A small, "
        "single-file change is usually 1 to 2 units: one that makes the "
        "change, and one that writes AND runs its own proof (its done_check "
        "may itself run the test; that does not need a separate unit). "
        "Never add a unit whose only job is to locate a file, record a "
        "path, or re-run a check another unit's own done_check already "
        "covers. Reach for 3 or more units only when parts are genuinely "
        "independent, can run in parallel, or carry separate risk.",
        "Answer with PURE JSON: a JSON list of unit objects. No prose, no "
        "explanation, no markdown, no code fences. Just the list.",
        "",
        "Every unit object needs exactly these fields:",
        "  id: a short string identifying the unit, unique within the list",
        "  objective: one sentence describing what the unit accomplishes",
        "  done_check: a single shell command that exits 0 once the unit is "
        "done. Use python3, never python, and name only programs that exist "
        "on a normal machine; a check whose command is not installed can "
        "never pass. Write it as ONE LINE: no embedded newline escapes, no "
        "here-doc, no multi-line script. It must FAIL right now, before the "
        "unit's work is done, and PASS once the work is done; it must not "
        "depend on the work having already happened.",
        "  writes: a list of CONCRETE repository-relative file paths this unit "
        "may write. Name the exact file (an existing one you can see, or the "
        "exact new path you intend to create); never a glob or wildcard "
        "pattern such as *.py or **/*.py, which the scope check that runs "
        "after this unit cannot match against the file it actually wrote.",
        "  deps: a list of other units' ids this unit depends on (may be empty)",
        "",
        "Rules: ids are unique. Every entry in deps names another unit in this "
        "same list (no dangling dependency). The deps taken together must not "
        "form a cycle. Every unit needs a non-empty done_check and at least "
        "one path in writes.",
    ]
    if refusal:
        lines += [
            "",
            "The previous answer was refused for exactly this reason:",
            refusal,
            "",
            "Fix exactly this and answer again with pure JSON.",
        ]
    return "\n".join(lines) + "\n"


def build_check_rewrite_prompt(objective, original_check, stderr_text):
    """The single-unit companion to build_prompt() above: asked only when a
    generated done_check turned out to be unrunnable (measured live
    2026-09-03: the planner twice wrote a multi-line `python3 -c "..."`
    check with literal backslash-n sequences inside the string, a syntax
    error before and after any work), never as a second attempt at the
    whole plan. Same contract as build_prompt's own done_check field,
    stated narrower: one unit, one replacement command, nothing else about
    the plan changes."""
    lines = [
        "One unit of a work plan has a done_check that cannot run at all.",
        "",
        "Unit objective: %s" % objective,
        "Original done_check: %s" % original_check,
        "",
        "Running that command, before any work on the unit, produced this "
        "output:",
        (stderr_text or "(no output was captured)").strip(),
        "",
        "Write a REPLACEMENT done_check for this SAME unit. Requirements:",
        "  - ONE LINE: a single shell command, no embedded newline escapes, "
        "no here-doc, no multi-line script.",
        "  - It must FAIL right now, before the unit's work is done, and "
        "PASS once the work is done; it must not depend on the work having "
        "already happened.",
        "  - Use python3, never python, and name only programs that exist "
        "on a normal machine.",
        "",
        "Answer with PURE JSON: a single JSON object of the form "
        '{"done_check": "..."}. No prose, no explanation, no markdown, no '
        "code fences.",
    ]
    return "\n".join(lines) + "\n"


def strip_code_fences(text):
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def resolve_done_check_interpreter(cmd):
    """A generated done_check names an interpreter that must exist on THIS
    machine. The harsh EVAD trials (2026-08-31) lost five of six lanes to one
    signature: the decomposer wrote `python`, this machine has only `python3`,
    every check exited 127, and the drain retried a missing-tool problem as if
    it were a code defect until the round ceiling. Resolve the common case
    (`python` to `python3` when only python3 exists) before the plan is
    accepted, so a missing interpreter never masquerades as broken code.
    Returns (resolved_cmd, note_or_None); note names any rewrite, and None
    means the command was left untouched."""
    if not cmd or not isinstance(cmd, str):
        return cmd, None
    tok = cmd.lstrip().split(None, 1)
    if not tok:
        return cmd, None
    prog = tok[0]
    if prog == "python" and shutil.which("python") is None \
            and shutil.which("python3") is not None:
        resolved = cmd.replace("python", "python3", 1)
        return resolved, "rewrote a done_check interpreter python to python3"
    return cmd, None


def normalize_unit(u):
    """The prompt asks for objective/writes/deps because that reads as plain
    English to a model. work_record.py validates title/owns/depends_on. This
    is the one place that gap is bridged, so the real contract is never
    reimplemented on either side of it."""
    out = dict(u)
    if "owns" not in out and "writes" in out:
        out["owns"] = out.get("writes")
    if "depends_on" not in out and "deps" in out:
        out["depends_on"] = out.get("deps")
    if "title" not in out and "objective" in out:
        out["title"] = out.get("objective")
    if out.get("done_check"):
        resolved, note = resolve_done_check_interpreter(out["done_check"])
        if note:
            out["done_check"] = resolved
            print("door: %s" % note, file=sys.stderr)
    return out


def resolve_cmd(model_cmd_arg):
    if model_cmd_arg:
        return shlex.split(model_cmd_arg)
    env_cmd = os.environ.get("DOOR_MODEL_CMD")
    if env_cmd:
        return shlex.split(env_cmd)
    return list(DEFAULT_MODEL_CMD)


def missing_reason(cmd):
    """None if cmd[0] looks runnable, else a message naming what is missing."""
    if not cmd:
        return "no decomposer command was given"
    exe = cmd[0]
    if os.sep in exe or (os.altsep and os.altsep in exe):
        if not os.path.isfile(exe):
            return "the decomposer command %r does not exist" % exe
        if not os.access(exe, os.X_OK):
            return "the decomposer command %r is not executable" % exe
        return None
    if shutil.which(exe) is None:
        return "the decomposer command %r was not found on PATH" % exe
    return None


def ask_decomposer(cmd, prompt, timeout=300):
    return subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          timeout=timeout)


def refusal_text(problems):
    return ("REFUSED: %d problem(s) that would break something downstream:\n"
            % len(problems) + "\n".join("  * %s" % p for p in problems))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("outcome", help="what should be true when this is done")
    ap.add_argument("--store", default=WR.STORE)
    ap.add_argument("--model-cmd", help="decomposer command line (shlex syntax)")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    # EVAD plan E2 (night probe 2026-08-31): a 20,000-character outcome went
    # straight to a model call and ran past two minutes. An outcome is a
    # sentence; anything book-sized is refused BY NAME before any model or
    # store is touched, exactly like the UnicodeEncodeError seam below.
    if len(args.outcome) > MAX_OUTCOME_CHARS:
        print("REFUSED: this outcome is %d characters; an outcome is a "
              "sentence (limit %d). For a spec this size, save it as a file "
              "and describe the outcome in one line."
              % (len(args.outcome), MAX_OUTCOME_CHARS), file=sys.stderr)
        return 1

    cmd = resolve_cmd(args.model_cmd)
    missing = missing_reason(cmd)
    if missing:
        print("%s: %s, so the outcome cannot be decomposed" % (NODATA, missing),
              file=sys.stderr)
        return 44

    # GROUNDING, not a per-retry cost: the repository does not change between
    # retries of the SAME decomposition, only the refusal text does.
    existing_files = list_repo_files(os.getcwd())

    total_attempts = args.max_retries + 1
    refusal = None
    last_problems = ["the decomposer was never asked"]
    for attempt in range(1, total_attempts + 1):
        prompt = build_prompt(args.outcome, refusal, existing_files)
        print("door: asking the decomposer (attempt %d of %d): %s"
              % (attempt, total_attempts, " ".join(cmd)), file=sys.stderr)
        try:
            proc = ask_decomposer(cmd, prompt)
        except OSError as exc:
            last_problems = ["the decomposer command could not be run: %s" % exc]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue
        except UnicodeEncodeError as exc:
            last_problems = ["the outcome could not be encoded to send to "
                             "the decomposer: %s" % exc]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue

        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)

        try:
            raw = json.loads(strip_code_fences(proc.stdout))
        except ValueError as exc:
            last_problems = ["the decomposer's answer could not be read as "
                             "JSON: %s" % exc]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue
        if not isinstance(raw, list):
            last_problems = ["the decomposer's answer was not a JSON list "
                             "of units"]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue

        units = [normalize_unit(u) for u in raw if isinstance(u, dict)]
        store = None if args.dry_run else args.store
        record, problems = WR.create(args.outcome, units, store=store)
        if not problems:
            if args.dry_run:
                print("door: validated %d unit(s), nothing written "
                      "(--dry-run)" % len(record["rows"]))
            else:
                print("Work %s created with %d unit(s)"
                      % (record["work_id"], len(record["rows"])))
            for row in record["rows"]:
                print("  %s: %s" % (row["id"], row["title"]))
            return 0

        last_problems = problems
        refusal = refusal_text(problems)
        print(refusal, file=sys.stderr)

    print("door: refused after %d attempt(s), store untouched" % total_attempts,
          file=sys.stderr)
    for p in last_problems:
        print("  * %s" % p, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
