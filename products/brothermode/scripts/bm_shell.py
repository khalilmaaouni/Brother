#!/usr/bin/env python3
"""bm_shell.py: run a shell command whose file writes were DECLARED first.

WHY THIS EXISTS
  The fence hook gates Edit, Write, MultiEdit and NotebookEdit. It does not
  gate Bash, and no honest parse of arbitrary shell exists: `sed -i`, `>`,
  `tee`, `git checkout`, a here-doc, and a one-line Python script all write
  files, and a wrapper that claimed to recognize all of them would be selling
  a guarantee it cannot keep. So the policy is:

    1. Ordinary file changes go through Edit or Write, which ARE gated.
    2. An unavoidable generated write goes through THIS wrapper, which makes
       the caller name the paths first and checks those names against the same
       fence the hook uses.
    3. Anything else, a bare Bash write, is outside mechanical protection and
       is documented as such rather than implied to be covered.

  This wrapper is therefore a declaration channel, not a sandbox. It does not
  confine the command it runs. A command that writes a path the caller did not
  declare writes it, and nothing here stops that. What the wrapper buys is
  that the paths a caller DID declare are checked against the fence before a
  single byte moves, and that a command run with no declaration at all is
  refused when it obviously writes.

HOW THE CHECK WORKS
  Each declared path is turned into the same PreToolUse payload Claude Code
  would send for an Edit of that path, and handed to bm_fence_hook.decide().
  Not a reimplementation: the imported function, so the wrapper and the hook
  cannot drift. The check runs in STRICT mode (BM_FENCE_STRICT), which is
  stricter than the hook's default on purpose: for a shell write, "no record
  covers this path" is not good enough, the caller has to be inside its own
  claim.

  A declared path OUTSIDE the project root is a third answer, not a pass. The
  fence declines to judge it at all, so this wrapper refuses by default and
  says why, and `--allow-outside-root` runs the command while reporting those
  paths as NOT CHECKED rather than as claimed.

  One deliberate difference from the hook: decide() FAILS OPEN by design (a
  missing store, an empty store, a corrupt store, any bug), because a hook
  that fails closed would brick the founder's editing. This wrapper is not in
  front of every edit, it is invoked on purpose for one command, so it does
  the opposite and REFUSES on a fail-open, printing the hook's own reason.
  "The fence could not be checked" and "the fence approved this" are not the
  same answer and must not produce the same outcome here.

USAGE
  python3 scripts/bm_shell.py --path src/gen.py [--path ...] \\
      [--record my-work] [--session-id "$CLAUDE_SESSION_ID"] \\
      [--allow-outside-root] -- <command...>

  python3 scripts/bm_shell.py --declare-none -- <command...>

  One argument after `--` is run through `sh -c`, so redirection and pipes
  work. Two or more are executed directly as argv, with no shell involved.
  Exit code is the command's own.

Python 3.9, standard library only, no network. It runs the command you gave
it; that is the entire point of the file.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import os
import re
import subprocess
import sys

EXIT_USAGE = 2
EXIT_REFUSED = 4

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")

# A SMALL, EXPLICIT list of obviously write-shaped shell forms. It is not a
# shell parser and does not pretend to be one: it exists so that
# `--declare-none` cannot be used as a rubber stamp on an obvious write. False
# negatives are expected and accepted; a form not listed here is not "safe",
# it is "not recognized", and the honest place that says so is docs/HOOKS.md.
#
# The redirection pattern excludes a preceding '<' or '>' (so '<>' and the
# second '>' of '>>' are not counted twice) and a following '&' (so '2>&1',
# which redirects one descriptor onto another and writes no file, is not a
# hit). It deliberately does NOT exclude a preceding DIGIT any more
# (fix-round 2026-07-29): the old class [^0-9<>] skipped exactly the
# file-descriptor forms '1>', '2>', '1>>', so `echo x 1> src/other.py` ran
# under --declare-none while the identical command with a bare '>' was
# refused. That is a miss INSIDE the coverage this list claims, not one of
# the accepted misses outside it.
WRITE_SIGNALS = (
    (r"(^|[^<>&])>{1,2}([^&]|$)", "output redirection (> or >>)"),
    (r"\btee\b", "tee"),
    (r"\bsed\b[^|;]*\s-i\b", "sed -i"),
    (r"\bperl\b[^|;]*\s-i\b", "perl -i"),
    (r"\brm\b", "rm"),
    (r"\bmv\b", "mv"),
    (r"\bcp\b", "cp"),
    (r"\btouch\b", "touch"),
    (r"\btruncate\b", "truncate"),
    (r"\bdd\b", "dd"),
    (r"\bln\b", "ln"),
    (r"\bmkdir\b", "mkdir"),
    (r"\bpatch\b", "patch"),
    (r"\bgit\s+(checkout|restore|reset|apply|clean|stash)\b", "a git command that rewrites files"),
    (r"\b(python3?|node|ruby)\b[^|;]*\s-(c|e)\b", "an inline interpreter script"),
)


def _err(text):
    sys.stderr.write(text + "\n")


def _out(text):
    sys.stdout.write(text + "\n")


def load_fence():
    """Import tools/bm_fence_hook.py and tools/bm_store.py.

    Returns (fence_module, store_module, error_string). Both or neither: the
    store is needed to answer "is this path even inside the project", which
    is a different question from "is this path claimed" and must not be
    silently folded into it (see check_paths)."""
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    try:
        import bm_fence_hook  # noqa: E402
        import bm_store  # noqa: E402
        return bm_fence_hook, bm_store, None
    except Exception as exc:
        return None, None, "%s: %s" % (type(exc).__name__, exc)


def parse_argv(argv):
    """Returns (options_dict, command_list, error_string)."""
    opts = {"paths": [], "record": None, "session_id": None,
            "declare_none": False, "allow_outside_root": False}
    if "--" not in argv:
        return None, None, ("no `--` separator. The command to run goes after "
                            "`--`, so this wrapper never has to guess where "
                            "its own flags end.")
    cut = argv.index("--")
    flags, command = argv[:cut], argv[cut + 1:]
    i = 0
    while i < len(flags):
        a = flags[i]
        if a == "--path" and i + 1 < len(flags):
            opts["paths"].append(flags[i + 1])
            i += 2
        elif a.startswith("--path="):
            opts["paths"].append(a.split("=", 1)[1])
            i += 1
        elif a == "--record" and i + 1 < len(flags):
            opts["record"] = flags[i + 1]
            i += 2
        elif a.startswith("--record="):
            opts["record"] = a.split("=", 1)[1]
            i += 1
        elif a == "--session-id" and i + 1 < len(flags):
            opts["session_id"] = flags[i + 1]
            i += 2
        elif a.startswith("--session-id="):
            opts["session_id"] = a.split("=", 1)[1]
            i += 1
        elif a == "--declare-none":
            opts["declare_none"] = True
            i += 1
        elif a == "--allow-outside-root":
            opts["allow_outside_root"] = True
            i += 1
        elif a in ("-h", "--help"):
            return None, None, "help"
        else:
            return None, None, "unrecognized flag %r" % a
    if not command:
        return None, None, "nothing to run after `--`"
    return opts, command, None


def write_signals_in(command_text):
    hits = []
    for pattern, label in WRITE_SIGNALS:
        if re.search(pattern, command_text):
            hits.append(label)
    return hits


def check_paths(fence, store, paths, session_id, cwd):
    """Ask the fence about every declared path.

    Returns (problems, notes, outside, checked). `outside` holds the declared
    paths that resolve OUTSIDE the project root: the fence deliberately
    declines to judge those ("BrotherMode fences a project, not the machine"),
    decide() skips them with no decision and no note, and until fix-round
    2026-07-29 this function reported that silence as approval, so the
    wrapper printed "1 declared path(s) are inside this session's own claim"
    for a path that was in no claim and not even in the project. Not checked
    and checked-and-yours are different answers and are now returned
    separately."""
    problems, notes, outside, checked = [], [], [], []
    root = None
    try:
        root, _source = store.resolve_root(cwd or None)
    except Exception as exc:
        problems.append("the project root could not be resolved (%s: %s), so "
                        "no declared path could be placed inside or outside it"
                        % (type(exc).__name__, exc))
        return problems, notes, outside, checked
    if root is None:
        problems.append("no BrotherMode project root was found from %s, so "
                        "there is no fence to check these paths against" % cwd)
        return problems, notes, outside, checked

    previous = os.environ.get("BM_FENCE_STRICT")
    os.environ["BM_FENCE_STRICT"] = "1"
    try:
        for p in paths:
            if fence.canonical_target(root, p, cwd) is None:
                outside.append(p)
                continue
            checked.append(p)
            payload = {
                "session_id": session_id,
                "cwd": cwd,
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": p, "content": ""},
            }
            try:
                decision, hook_notes = fence.decide(payload)
            except Exception as exc:
                problems.append("the fence could not judge %s (%s: %s)"
                                % (p, type(exc).__name__, exc))
                continue
            notes.extend(hook_notes)
            if decision is not None:
                reason = (decision.get("hookSpecificOutput") or {}).get(
                    "permissionDecisionReason", "(no reason given)")
                problems.append("%s: %s" % (p, reason))
    finally:
        if previous is None:
            os.environ.pop("BM_FENCE_STRICT", None)
        else:
            os.environ["BM_FENCE_STRICT"] = previous
    return problems, notes, outside, checked


def main(argv):
    opts, command, err = parse_argv(list(argv))
    if err == "help":
        sys.stdout.write(__doc__)
        return 0
    if err is not None:
        _err("bm_shell: %s" % err)
        _err("usage: bm_shell.py --path P [--path Q] [--record R] "
             "[--session-id S] [--allow-outside-root] -- <command...>")
        _err("       bm_shell.py --declare-none -- <command...>")
        return EXIT_USAGE

    command_text = command[0] if len(command) == 1 else " ".join(command)

    if opts["paths"] and opts["declare_none"]:
        _err("bm_shell: --declare-none contradicts --path. Pick one.")
        return EXIT_USAGE

    if not opts["paths"] and not opts["declare_none"]:
        _err("bm_shell: REFUSED. No --path was declared.\n"
             "  Command: %s\n"
             "  This wrapper exists so a shell write names its files BEFORE it "
             "runs. Declare every path the command writes with --path, or, if "
             "it writes nothing, say so explicitly with --declare-none."
             % command_text)
        return EXIT_REFUSED

    if opts["declare_none"]:
        hits = write_signals_in(command_text)
        if hits:
            _err("bm_shell: REFUSED. --declare-none was passed, but this "
                 "command contains %s.\n"
                 "  Command: %s\n"
                 "  Declare the paths it writes with --path instead. Note that "
                 "this check is a SHORT LIST of obvious write forms, not a "
                 "shell parser: passing it is not evidence that a command "
                 "writes nothing."
                 % (" and ".join(hits), command_text))
            return EXIT_REFUSED
        _err("bm_shell: running with no declared paths. The command was "
             "checked against a short list of obvious write forms only.")
        return run(command)

    session_id = (opts["session_id"]
                  or os.environ.get("BM_FENCE_SESSION_ID", "")
                  or os.environ.get("CLAUDE_SESSION_ID", "")).strip()
    if not session_id:
        _err("bm_shell: REFUSED. No session id, so the fence cannot tell "
             "whether these paths are yours. Pass --session-id "
             "\"$CLAUDE_SESSION_ID\" or set BM_FENCE_SESSION_ID. This refuses "
             "rather than inventing one, for the same reason bm_fence_hook.py "
             "does: an invented id mints a token nobody can reproduce.")
        return EXIT_REFUSED

    fence, store, load_err = load_fence()
    if fence is None:
        _err("bm_shell: REFUSED. tools/bm_fence_hook.py could not be imported "
             "(%s), so nothing checked these paths. This refuses where the "
             "HOOK fails open, because a hook that blocks editing is worse "
             "than an unchecked write, while a wrapper you invoked on purpose "
             "can safely say no." % load_err)
        return EXIT_REFUSED

    problems, notes, outside, checked = check_paths(
        fence, store, opts["paths"], session_id, os.getcwd())
    for n in notes:
        _err(n)
    unchecked = [n for n in notes if "FAILING OPEN" in n]
    if unchecked:
        _err("bm_shell: REFUSED. The fence FAILED OPEN, which means these "
             "paths were not checked at all, and an unchecked declared write "
             "must not be reported as a checked one. The hook's reason is the "
             "line above. Fix that (usually: run `python3 tools/bm_store.py "
             "init` and claim the paths), or accept that this write is outside "
             "mechanical protection and run it as plain Bash.")
        return EXIT_REFUSED
    if problems:
        _err("bm_shell: REFUSED. The fence rejected %d of %d declared path(s):"
             % (len(problems), len(opts["paths"])))
        for p in problems:
            _err("  - %s" % p)
        return EXIT_REFUSED

    if outside and not opts["allow_outside_root"]:
        _err("bm_shell: REFUSED. %d declared path(s) resolve OUTSIDE this "
             "project root, where the fence deliberately makes no judgement "
             "(BrotherMode fences a project, not the machine):" % len(outside))
        for p in outside:
            _err("  - %s" % p)
        _err("  Nothing checked those paths, and an unchecked declared write "
             "must not be reported as a checked one, which is the same rule "
             "this wrapper applies to a fail-open. If the write really does "
             "belong outside the project, re-run with --allow-outside-root "
             "and the summary will say the paths were NOT checked.")
        return EXIT_REFUSED

    record_suffix = ("" if not opts["record"]
                     else " (record %s)" % opts["record"])
    if outside:
        _err("bm_shell: %d declared path(s) are inside this session's own "
             "claim; %d were NOT CHECKED because they are outside the project "
             "root (%s). The fence made no judgement about those%s. Running."
             % (len(checked), len(outside), ", ".join(outside), record_suffix))
    else:
        _err("bm_shell: %d declared path(s) are inside this session's own "
             "claim%s. Running." % (len(checked), record_suffix))
    return run(command)


def run(command):
    """One argument goes through `sh -c` so redirection works; several are
    executed directly, with no shell to re-interpret them."""
    argv = ["sh", "-c", command[0]] if len(command) == 1 else list(command)
    try:
        proc = subprocess.run(argv)
    except OSError as exc:
        _err("bm_shell: the command could not be started: %s" % exc)
        return EXIT_REFUSED
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
