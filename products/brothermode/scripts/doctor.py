#!/usr/bin/env python3
"""doctor.py: ten named checks, each PASS, FAIL, or SKIP, plain remediation.

WHY THIS EXISTS (grew under Loop 3 design D-3, docs/superpowers/specs/
2026-08-01-loop3-consent-install-design.md)
  This file started as one question: is the fence hook wired AND live, or
  only wired? That question still matters most and its check is check 1,
  unchanged. The external review's section 13.5 asked for a wider surface:
  a founder running one command should learn whether the whole install is
  healthy, not only the fence. So doctor grew to ten checks: the fence
  simulation; version identity between VERSION and the plugin manifest;
  python3 and git availability; consent (has scripts/setup.py been run);
  the vault path; plugin-vs-clone duplicate hook detection (the recon's
  confirmed double-fire bug: both wirings present means every hook fires
  twice); project store health; hook wiring matching the consented
  installation_mode; a CHECKSUMS.sha256 self-check (catches a half-finished
  update); and whether settings.json itself is valid JSON.

WHAT EVERY CHECK PROMISES
  PASS, FAIL with one plain-word remediation a non-engineer can follow, or
  SKIP with the reason nothing could be checked. A check never crashes this
  program: an unexpected exception inside one check is caught and reported
  as that check's own FAIL, naming the exception, so one broken check cannot
  hide the other nine. Exit 0 only when every check is PASS or SKIP; --json
  prints the same information as one machine-readable object. BROTHERME_CONFIG
  and HOME are honored throughout (mostly by reusing scripts/setup.py's own
  config reader), so a test harness can point every check at a throwaway
  HOME without touching the real one.

CHECK 1: THE FENCE HOOK, KEPT EXACTLY AS BEFORE
  1. Reads settings.json and finds the PreToolUse group whose command names a
     bm_fence_hook.py. Missing group, missing entry, or a command pointing at
     a file that is not there are each reported as a PROBLEM, by name.
  2. Runs a BLOCKED-WRITE SIMULATION against the command string settings.json
     actually holds, not against a path this script reconstructs. It builds a
     throwaway project under a temporary directory, gives it its own store,
     claims one file under one session's label, then asks the wired hook to
     approve a write to that file from a DIFFERENT session. A healthy fence
     denies. Then it asks again as the owner, and a healthy fence allows,
     with output AND exit code both clean. Both halves are required: a hook
     that denied everything would pass the first check and is not a fence, it
     is a brick, and a hook that bricks by exiting 2 rather than by printing
     deny is the same brick. Every supported write tool is simulated in its
     own real input shape, so a fence that gates one of them and not the
     other three cannot report itself healthy.
  3. Says out loud that the fence FAILS OPEN, and what that means for the
     answer above.
  4. Asks that SAME wired hook's own store code to verify THIS PROJECT'S
     ACTUAL store, read-only, if one exists on disk (fix-round 2026-08-21,
     queue M17, family evidence-integrity). Steps 1-3 build a throwaway
     store and read it right back with the very code that just created it,
     so that loop can never be behind itself and can never expose a version
     gap. A real store already migrated ahead of a stale copy of
     bm_store.py sitting next to the wired hook is reported as a PROBLEM
     here; before this step existed, that gap passed silently and doctor
     reported the fence live while it was failing open on every write.

  The simulation is harmless in the strict sense: every file it creates lives
  under a fresh mkdtemp directory that is removed at the end, and the write it
  simulates is never performed by anything. Nothing outside the temporary
  directory is read for the simulation except the hook and store code being
  tested and, for step 4, a single read-only verify of this project's own
  store, which never writes to it or to your STATE.md.

WHAT NO CHECK HERE CAN TELL YOU
  That Claude Code has loaded the settings file you just fixed. A hook is
  read by the client at session start; a correct settings.json edited
  mid-session is not live until the next session. Doctor checks the file and
  the code, which is everything except the client's memory.

Python 3.9, standard library only. Runs subprocesses only to execute the
wired fence hook command, this project's own store CLI (check 7), local git
(check install_identity), and the stranded_install check, whose second half
(git ls-remote against the public origin) is the one network call this file
makes; offline or unreachable, that check reports NO-DATA rather than a
silent PASS.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import argparse
import collections
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3

FENCE_BASENAME = "bm_fence_hook.py"
WRITE_TOOL_NAMES = ("Edit", "Write", "MultiEdit", "NotebookEdit")

# Mirrors scripts/install.py's own INSTALLED_FROM_NAME. Duplicated rather
# than imported, same rule as FENCE_BASENAME above: each script in this
# directory is self-contained on purpose.
INSTALLED_FROM_NAME = "INSTALLED-FROM"

#: The tool_input shape each simulated write tool sends, keyed by tool name.
#: The path key differs per tool (NotebookEdit carries notebook_path, not
#: file_path), and a hook that reads only one of them gates only one of them,
#: so the simulation sends each tool its own real shape rather than one shape
#: relabelled four times.
SIM_TOOL_INPUTS = {
    "Edit": lambda t: {"file_path": t, "old_string": "a", "new_string": "b"},
    "Write": lambda t: {"file_path": t, "content": "doctor simulation\n"},
    "MultiEdit": lambda t: {"file_path": t,
                            "edits": [{"old_string": "a", "new_string": "b"}]},
    "NotebookEdit": lambda t: {"notebook_path": t, "new_source": "x = 1\n"},
}

OWNER_SESSION = "bm-doctor-owner"
INTRUDER_SESSION = "bm-doctor-intruder"
SIM_REL_PATH = os.path.join("sim", "fenced.txt")

# scripts/setup.py, same directory, deliberate (mirrors scripts/uninstall.py's
# own "import install as _install"): this is the ONE place consent is read or
# written, per its own docstring, and every check below that needs to know
# whether setup has run goes through it rather than re-reading the config file
# a second way.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setup as _bm_setup  # noqa: E402  (same directory, deliberate)


def _out(text):
    sys.stdout.write(text + "\n")


def _err(text):
    sys.stderr.write(text + "\n")


def default_settings_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _mask_home(path):
    """Replace the operator's own home-directory prefix with a generic
    /Users/... placeholder before this path is printed (R5, external review
    Loop 3/5): every message below that names an absolute path runs through
    this, so a remediation command doctor.py prints never leaks the machine's
    real account name. Mirrors the same, simpler technique scripts/
    rehearse_fresh_install.py's step1_clone already uses for the same
    reason: the heavier, free-text bs.mask_absolute_paths in
    tools/bm_store.py replaces a whole path with a withheld marker, which
    would make a printed command impossible to run; this hides only the
    account name and keeps the rest of the path, including the command,
    copy-pasteable."""
    if not path:
        return path
    home = os.path.expanduser("~")
    if home and path.startswith(home):
        return "/Users/..." + path[len(home):]
    return path


def read_settings(path):
    """Returns (settings_dict, error_string). Never raises on bad input: an
    unreadable settings file is a finding to report, not a traceback."""
    if not os.path.exists(path):
        return None, ("no settings file at %s. This file is normally created "
                      "by python3 scripts/install.py, which also wires the "
                      "hooks into it; run that to create it."
                      % _mask_home(path))
    try:
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except (IOError, OSError) as exc:
        return None, "settings file at %s could not be read: %s" % (_mask_home(path), exc)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, ("settings file at %s is not valid JSON (%s). Claude Code "
                      "ignores it silently, so EVERY hook is off." % (_mask_home(path), exc))
    if not isinstance(data, dict):
        return None, "settings file at %s is JSON but not an object" % _mask_home(path)
    return data, None


def loader_managed_fence_copies():
    """BrotherMode copies Claude Code auto-loads as plugins, each carrying
    its own hook wiring so settings.json holds no block at all: directories
    under ~/.claude/skills/<name>/ (the skills-dir auto-load) and under the
    plugin cache ~/.claude/plugins/cache/<marketplace>/<name>/, whose own
    hooks/hooks.json names the fence basename. Added in the finalization
    run 2026-08-08, when this machine's duplicated settings.json wiring was
    retired in favor of the plugin loader and doctor had no model for that
    shape. Best effort: an unreadable directory is simply not a match, and
    a private HOME (the test fixtures) sees only its own tree."""
    out = []
    home = os.path.expanduser("~")
    bases = [os.path.join(home, ".claude", "skills")]
    cache = os.path.join(home, ".claude", "plugins", "cache")
    try:
        for marketplace in sorted(os.listdir(cache)):
            bases.append(os.path.join(cache, marketplace))
    except OSError:  # sbe: allow-silent no plugin cache is the common case, not a problem
        pass
    for base in bases:
        try:
            names = sorted(os.listdir(base))
        except OSError:  # sbe: allow-silent unreadable base dir is simply not a match, per docstring above
            continue
        for name in names:
            hooks = os.path.join(base, name, "hooks", "hooks.json")
            try:
                with io.open(hooks, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:  # sbe: allow-silent missing/unreadable hooks.json means this plugin has none, not a match
                continue
            if FENCE_BASENAME in text:
                out.append(os.path.join(base, name))
    return out


def find_fence_entries(settings):
    """Every PreToolUse hook entry whose command names a bm_fence_hook.py.

    Matched on the BASENAME of a real path inside the command, read the way a
    shell reads it, so a hook of the user's own called my_bm_fence_hook.py is
    not mistaken for ours. This mirrors install.py's ownership rule; it is
    deliberately duplicated rather than imported, because each script in this
    directory is self-contained on purpose."""
    found = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return found
    groups = hooks.get("PreToolUse")
    if not isinstance(groups, list):
        return found
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []) or []:
            if not isinstance(entry, dict):
                continue
            command = entry.get("command")
            if not isinstance(command, str):
                continue
            try:
                words = shlex.split(command)
            except ValueError:  # sbe: allow-silent unparseable command string is simply not a fence match, not our hook to fix
                continue
            if any(os.path.basename(w) == FENCE_BASENAME for w in words):
                found.append((group, entry, command, words))
    return found


def matcher_covers(matcher, tool_name):
    """Does this PreToolUse matcher select tool_name? Returns True, False, or
    None when the matcher is not a usable regular expression.

    Claude Code treats the matcher as a REGEX tested against the tool name, so
    that is what this tests. It is emphatically NOT a substring test of the
    tool name in the matcher string (fix-round 2026-07-29): 'Edit' is a
    substring of both 'MultiEdit' and 'NotebookEdit', so a matcher of
    'Write|MultiEdit|NotebookEdit' passed a `tool not in matcher` check while
    leaving Edit, the primary write tool, completely ungated. Reproduced by
    running doctor against exactly that matcher: it printed OK and exited 0."""
    m = (matcher or "").strip()
    if m in ("*", ".*"):
        return True
    try:
        return re.search(m, tool_name) is not None
    except re.error:  # sbe: allow-silent not a usable regex, function's own contract returns None for that per docstring
        return None


def fence_path_in(words):
    for w in words:
        if os.path.basename(w) == FENCE_BASENAME:
            return w
    return None


def _run(cmd, cwd, env, stdin_text=None, timeout=120):
    return subprocess.run(
        cmd, cwd=cwd, env=env, input=stdin_text,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout)


def _run_shell(command, cwd, env, stdin_text=None, timeout=120):
    """Runs `command` (a STRING, not an argv list) through a real shell.

    This is how the wired hook command must be judged (queue M20, family
    evidence-integrity): shell=True is required, not a shortcut, because it
    is the exact way Claude Code hands this same command string to a shell,
    established at tools/test_bm_consent.py's test_no_wired_command_of_any_
    module_writes_before_consent ("the strings being run are the exact
    command lines Claude Code hands to a shell") and reused unmodified at
    tools/test_bm_hookperf.py's run_real(). An argv-list run
    (subprocess.run([...])) leaves a redirection, pipe, or other shell
    metacharacter as a literal extra word instead of applying it, so a
    wired command carrying one was judged on output the real harness would
    have thrown away."""
    return subprocess.run(
        command, shell=True, cwd=cwd, env=env, input=stdin_text,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout)


def blocked_write_simulation(command, command_words, tools_dir):
    """Ask the WIRED hook command to judge one foreign write and one own
    write, in a throwaway project this function builds and deletes itself.

    `command` is the raw command STRING (settings.json's own text, or the
    equivalent reconstructed for a loader-managed plugin copy); it is what
    actually gets executed, through a real shell (_run_shell above), because
    that is how Claude Code invokes it. `command_words` is that same command
    already shlex.split, used only to LOCATE files (the fence path, the
    interpreter name) on disk, never to execute anything.

    THIS PROJECT'S OWN real store is verified separately, by
    _verify_real_store() below, called from doctor(): the loop here (the
    same store code creates a throwaway store and then reads it right
    back) can never be behind itself, so it can never expose a version gap
    by itself (queue M17, family evidence-integrity).

    Returns a list of problem strings; empty means the fence is live."""
    problems = []
    tmp = tempfile.mkdtemp(prefix="bm-doctor-")
    try:
        root = os.path.realpath(tmp)
        os.makedirs(os.path.join(root, "sim"))
        target = os.path.join(root, SIM_REL_PATH)
        with io.open(target, "w", encoding="utf-8") as fh:
            fh.write("doctor simulation target, deleted when doctor exits\n")

        env = dict(os.environ)
        env["BROTHERMODE_ROOT"] = root
        env.pop("BM_FENCE_STRICT", None)
        env.pop("BM_FENCE_SESSION_ID", None)
        # queue M25, family provenance: the hook and store code this
        # simulation runs lazily importlib-load their sibling module
        # (bm_fence_hook.py loads bm_store.py) with the ordinary
        # SourceFileLoader path, which writes a __pycache__/*.pyc beside
        # the INSTALLED file it just imported, not under root above, the
        # moment PYTHONDONTWRITEBYTECODE is unset and that directory is
        # writable. That contradicts this file's own module docstring
        # (CHECK 1: "every file it creates lives under a fresh mkdtemp
        # directory that is removed at the end"), so every subprocess this
        # function spawns gets the flag rather than the docstring's claim
        # being weakened to fit the leak.
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        store = os.path.join(tools_dir, "bm_store.py")
        fence = fence_path_in(command_words)
        if not os.path.isfile(store):
            return ["cannot simulate: no bm_store.py beside the wired hook "
                    "(looked for %s)" % store]

        r = _run([sys.executable, store, "init"], root, env)
        if r.returncode != 0:
            return ["cannot simulate: the store CLI could not create a "
                    "throwaway store (exit %d): %s"
                    % (r.returncode, (r.stderr or r.stdout or "").strip()[:300])]

        r = _run([sys.executable, fence, "session-label",
                  "--session-id", OWNER_SESSION], root, env, stdin_text="")
        owner_label = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        if r.returncode != 0 or not owner_label:
            return ["cannot simulate: the hook would not mint a session label "
                    "(exit %d): %s"
                    % (r.returncode, (r.stderr or "").strip()[:300])]

        r = _run([sys.executable, store, "claim", "bm-doctor-simulation",
                  "--lifetime", "ephemeral",
                  "--objective", "doctor blocked-write simulation",
                  "--files", SIM_REL_PATH,
                  "--session", owner_label], root, env)
        if r.returncode != 0:
            return ["cannot simulate: the throwaway claim was refused (exit "
                    "%d): %s" % (r.returncode,
                                 (r.stderr or r.stdout or "").strip()[:300])]

        def ask(session_id, tool_name):
            payload = json.dumps({
                "session_id": session_id,
                "cwd": root,
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": SIM_TOOL_INPUTS[tool_name](target),
            })
            return _run_shell(command, root, env, stdin_text=payload)

        # Every supported write tool is simulated, not just Edit (fix-round
        # 2026-07-29). One shape per tool, because the four do not share a
        # path key, and a hook whose WRITE_TOOLS set had lost three of them
        # passed the Edit-only simulation while three tools wrote unfenced.
        for tool_name in WRITE_TOOL_NAMES:
            # Half one: a foreign session must be DENIED.
            r = ask(INTRUDER_SESSION, tool_name)
            if r.returncode != 0:
                problems.append(
                    "the wired hook exited %d on the %s simulation; it is "
                    "documented to always exit 0. stderr: %s"
                    % (r.returncode, tool_name, (r.stderr or "").strip()[:300]))
            decision = None
            text = (r.stdout or "").strip()
            if text:
                try:
                    decision = json.loads(text)
                except ValueError:
                    problems.append(
                        "the wired hook printed something that is not JSON on "
                        "the %s simulation: %s" % (tool_name, text[:200]))
            verdict = None
            if isinstance(decision, dict):
                verdict = (decision.get("hookSpecificOutput") or {}).get(
                    "permissionDecision")
            if verdict != "deny":
                problems.append(
                    "BLOCKED-WRITE SIMULATION FAILED for %s: a session that "
                    "owns nothing was allowed to write a file another session "
                    "had claimed. The hook is wired but it is not enforcing "
                    "%s. Hook stderr: %s"
                    % (tool_name, tool_name,
                       (r.stderr or "").strip()[:400] or "(none)"))

            # Half two: the OWNER must still be allowed. A hook that denies
            # everything would pass half one and would not be a fence.
            r2 = ask(OWNER_SESSION, tool_name)
            # The EXIT CODE is checked here as well as the output (fix-round
            # 2026-07-29). Claude Code's PreToolUse contract blocks a tool
            # call on exit code 2 with stderr, not only on deny JSON, so a
            # hook that emitted correct deny JSON for a foreign session and
            # exited 2 on every allowed write bricked the owner's own editing
            # while doctor printed OK and exited 0.
            if r2.returncode != 0:
                problems.append(
                    "CALIBRATION FAILED: on the owner's own %s the hook exited "
                    "%d. A non-zero exit is how a PreToolUse hook BLOCKS a "
                    "call, so this hook refuses writes it should allow even "
                    "though it printed no deny. stderr: %s"
                    % (tool_name, r2.returncode, (r2.stderr or "").strip()[:300]))
            text2 = (r2.stdout or "").strip()
            if text2:
                problems.append(
                    "CALIBRATION FAILED: the owner of the claim was refused "
                    "its own file on %s, so this hook denies writes it should "
                    "allow. Output: %s" % (tool_name, text2[:300]))

        if os.path.exists(os.path.join(root, "sim", "written-by-doctor")):
            problems.append("the simulation wrote a file it should not have")
        return problems
    except (OSError, subprocess.SubprocessError) as exc:
        return ["the simulation could not be run: %s: %s"
                % (type(exc).__name__, exc)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _resolve_real_project_root(start=None):
    """Where does THIS PROJECT'S real store actually live? Mirrors tools/
    bm_store.py's resolve_root() precedence, stated in its own docstring:
    BROTHERMODE_ROOT first, then a marker directory (.brothermode/)
    anywhere up the tree, then .git, never os.getcwd() alone. Duplicated
    here rather than imported, the same reason find_fence_entries above
    duplicates install.py's ownership rule: each script in this directory
    is self-contained on purpose.

    Fixes queue M17 defect B3: the real-store check used to use
    os.getcwd() alone as the project root, so a doctor run from ANY
    subdirectory of a project (project/docs/, say) looked for
    docs/.brothermode/store.sqlite3, found nothing, and silently treated
    a real, existing, schema-behind store as though it were not there.

    Returns (root_path, source), source in ("env", "marker", "git"), or
    (None, None) when nothing anchors a project at or above `start`."""
    env_root = os.environ.get("BROTHERMODE_ROOT")
    if env_root:
        p = os.path.realpath(env_root)
        if os.path.isdir(p):
            return p, "env"
    chain = []
    cur = os.path.realpath(start or os.getcwd())
    while True:
        chain.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for d in chain:
        if os.path.isdir(os.path.join(d, ".brothermode")):
            return d, "marker"
    for d in chain:
        if os.path.exists(os.path.join(d, ".git")):
            return d, "git"
    return None, None


def _describe_non_regular_path(path):
    """One-line description of what sits at `path` when it exists but is
    not a regular file, so a FAIL message says what to go fix without the
    reader having to go look themselves."""
    if os.path.isdir(path):
        return "a directory"
    if os.path.islink(path):
        return "a broken symlink (its target does not exist)"
    return "present but not a regular file"


def _verify_real_store(tools_dir, command_words):
    """THIS PROJECT'S OWN STORE, separate from the throwaway one
    blocked_write_simulation above builds and reads back with the same
    code (queue M17, family evidence-integrity): that loop can never
    expose a version gap by itself, because the same code creates the
    store and then reads it right back. The wired hook's real job is
    judging writes against the store THIS PROJECT actually uses, which
    may already sit at a schema a stale copy of bm_store.py next to the
    wired hook cannot read; a refusal here means the fence fails open on
    every real write in this project while the throwaway simulation above
    still reports clean, exactly what happened on 2026-08-20.

    Fix-round 2026-08-21 closed three defects an outside reviewer found in
    that first fix, all one family: this step declining to look and
    saying nothing when it declined.
      - B3: real_root used to be os.getcwd() alone; _resolve_real_
        project_root() above now walks the same precedence tools/
        bm_store.py documents for itself, so a doctor run from a
        subdirectory of the project still finds the real store.
      - B4: a store path that exists but is not a regular file (a
        directory, a dangling symlink) used to fail os.path.isfile() and
        get read as "no store to inspect"; it is a PROBLEM now, because
        the wired hook hits that same broken path on every real write and
        takes its no-store fail-open path there, which is not the same as
        there being no store to worry about.
      - B5: the verify subprocess used sys.executable; it now uses
        command_words[0], the interpreter the WIRED command actually
        names, the same one the deny/allow simulation above already runs
        faithfully, so this step exercises what the harness would really
        run.

    Returns (problems, notes), both lists of strings. Every path where
    this check cannot tell goes into NOTES, never silence: a project with
    no store yet is not a failure (matches check_store_health's own SKIP
    a little further down in this file), but this step must never look
    like it verified something it did not."""
    problems = []
    notes = []
    store = os.path.join(tools_dir, "bm_store.py")

    real_root, source = _resolve_real_project_root()
    if real_root is None:
        notes.append(
            "real-store check: NO-DATA, could not resolve this project's "
            "root from the current directory (checked BROTHERMODE_ROOT, "
            "then every parent directory for .brothermode/, then for "
            ".git), so this project's real store was not verified.")
        return problems, notes

    real_store_path = os.path.join(real_root, ".brothermode", "store.sqlite3")
    if not os.path.lexists(real_store_path):
        notes.append(
            "real-store check: SKIP, no project store at %s (root "
            "resolved via %s), so there is nothing to verify yet."
            % (real_store_path, source))
        return problems, notes

    if not os.path.isfile(real_store_path):
        problems.append(
            "THE REAL STORE PATH IS NOT A DATABASE FILE: %s exists but is "
            "%s, so bm_store.py cannot open it. Doctor is reporting this "
            "rather than silently calling it 'no store': the wired hook "
            "hits the exact same broken path on every real write in this "
            "project and takes its no-store fail-open path there, which "
            "means the fence is unenforced, not merely unproven. Remove "
            "or fix %s so a real store can be created (bm_store.py init)."
            % (real_store_path, _describe_non_regular_path(real_store_path),
               real_store_path))
        return problems, notes

    interpreter = command_words[0]
    real_env = dict(os.environ)
    real_env["BROTHERMODE_ROOT"] = real_root
    rv = _run([interpreter, store, "verify"], real_root, real_env)
    if rv.returncode != 0:
        problems.append(
            "THE WIRED HOOK CANNOT READ THIS PROJECT'S OWN STORE: "
            "%s verify against %s exited %d: %s. The blocked-write "
            "simulation above only proves the hook can judge a "
            "throwaway store it built itself, which is always at "
            "the schema this same code writes; it says nothing "
            "about the store this project actually uses. Until the "
            "wired hook is upgraded to a copy that understands this "
            "store, the fence fails open on every real write here."
            % (store, real_store_path, rv.returncode,
               ((rv.stdout or "") + (rv.stderr or "")).strip()[:400]
               or "(no output)"))
    return problems, notes


def doctor(settings_path):
    """Returns (problems, notes). Both are lists of strings. This is check 1,
    unchanged from before the ten-check surface existed."""
    problems = []
    notes = []

    settings, err = read_settings(settings_path)
    if err is not None:
        return [err], notes

    entries = find_fence_entries(settings)
    if not entries:
        loader_copies = loader_managed_fence_copies()
        if loader_copies:
            copy = loader_copies[0]
            fence = os.path.join(copy, "tools", FENCE_BASENAME)
            notes.append("no settings.json fence block, and none is "
                         "needed: %s is auto-loaded by Claude Code as a "
                         "plugin and registers its own hooks/hooks.json, "
                         "fence included. Liveness is proven below against "
                         "that copy." % _mask_home(copy))
            if not os.path.isfile(fence):
                return (problems + [
                    "the loader-managed copy at %s wires a fence in its "
                    "hooks/hooks.json but carries no tools/%s file, so the "
                    "registered hook is dead: Claude Code will report a "
                    "hook error and continue, and writes proceed unfenced."
                    % (_mask_home(copy), FENCE_BASENAME)], notes)
            words = ["python3", fence]
            loader_command = "python3 %s" % shlex.quote(fence)
            loader_tools_dir = os.path.dirname(fence)
            problems.extend(blocked_write_simulation(
                loader_command, words, loader_tools_dir))
            store_problems, store_notes = _verify_real_store(
                loader_tools_dir, words)
            problems.extend(store_problems)
            notes.extend(store_notes)
            return problems, notes
        return (["NO FENCE HOOK IS WIRED. settings.json (%s) has no PreToolUse "
                 "entry naming %s, no auto-loaded plugin copy under "
                 "~/.claude/skills or the plugin cache wires one either, so "
                 "nothing stands in front of a write and "
                 "the one-writer promise is a ledger, not a boundary. Fix: run "
                 "python3 scripts/install.py (or --upgrade over an existing "
                 "install), or add the block in docs/HOOKS.md by hand."
                 % (_mask_home(settings_path), FENCE_BASENAME)], notes)
    if len(entries) > 1:
        notes.append("%d fence entries are wired; the first one is the one "
                     "checked below." % len(entries))

    group, entry, command, words = entries[0]
    fence = fence_path_in(words)
    notes.append("fence command: %s" % command)

    matcher = group.get("matcher")
    if not isinstance(matcher, str) or not matcher.strip():
        problems.append(
            "the fence group has no matcher, so it runs on every tool call. "
            "That is wasteful rather than wrong, but it is not what install.py "
            "writes; expected %s." % "|".join(WRITE_TOOL_NAMES))
    else:
        verdicts = [(t, matcher_covers(matcher, t)) for t in WRITE_TOOL_NAMES]
        if any(v is None for _t, v in verdicts):
            problems.append(
                "the fence matcher %r is not a valid regular expression, so "
                "Claude Code cannot match any tool name against it and EVERY "
                "write tool is ungated. Expected %s."
                % (matcher, "|".join(WRITE_TOOL_NAMES)))
        else:
            missing = [t for t, v in verdicts if not v]
            if missing:
                problems.append(
                    "the fence matcher %r does not cover %s, so those write "
                    "tools are UNGATED." % (matcher, ", ".join(missing)))

    if not fence or not os.path.isfile(fence):
        return (problems + [
            "the wired fence command points at %s, which is not a file. The "
            "hook is configured and dead: Claude Code will report a hook error "
            "and continue, so writes proceed unfenced."
            % (fence or "(no path found in the command)")], notes)

    tools_dir = os.path.dirname(os.path.abspath(fence))
    problems.extend(blocked_write_simulation(command, words, tools_dir))
    store_problems, store_notes = _verify_real_store(tools_dir, words)
    problems.extend(store_problems)
    notes.extend(store_notes)
    return problems, notes


# ---------------------------------------------------------------------------
# The ten-check surface (Loop 3 design D-3). Each check function below
# returns a CheckResult; none of them may raise, because _run_check wraps
# every call and turns an unexpected exception into that check's own FAIL
# rather than a doctor.py traceback.
# ---------------------------------------------------------------------------

CheckResult = collections.namedtuple("CheckResult", "key title status message")

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"

CHECK_TITLES = collections.OrderedDict((
    ("fence", "the write-protection check (fence hook) wired and live"),
    ("version", "VERSION matches the plugin manifest"),
    ("runtime", "python3 3.9+ and git are available"),
    ("windows_hooks", "Windows can run this project's automatic hooks"),
    ("consent", "setup has been completed"),
    ("vault", "vault path exists and is writable"),
    ("duplicate_install", "only one install method is wired"),
    ("install_shape", "the install actually present is not stranded"),
    ("store", "project store health"),
    ("mode_wiring", "hook wiring matches installation_mode"),
    ("checksums", "CHECKSUMS.sha256 self-check"),
    ("settings_json", "settings.json is valid JSON"),
    ("install_identity", "this tree matches the commit it was installed from"),
    ("stranded_install", "the installed public tag matches the public origin"),
    ("data_locations", "where your data lives, in plain language"),
))


def _result(key, status, message):
    return CheckResult(key, CHECK_TITLES[key], status, message)


def _run_check(key, fn, *args):
    """Call fn(*args), which must return a CheckResult for `key`. An
    exception from fn becomes that check's own FAIL instead of a crash: one
    broken check must never hide the other nine."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - a check must never crash doctor
        return _result(key, STATUS_FAIL,
                       "FAIL: this check crashed (%s: %s). Re-run with --json "
                       "for the full check list, and report this at "
                       "https://github.com/khalilmaaouni/BrotherModeUp/issues "
                       "(include the --json output)."
                       % (type(exc).__name__, exc))


def check_fence(settings_path):
    problems, notes = doctor(settings_path)
    if problems:
        lines = list(notes)
        lines.append("PROBLEMS (%d):" % len(problems))
        for p in problems:
            lines.append("  - %s" % p)
        lines.append(
            "Until these are fixed, treat file ownership as a coordination "
            "ledger only: bm_store.py still refuses an overlapping CLAIM, but "
            "nothing refuses a WRITE.")
        return _result("fence", STATUS_FAIL, "\n".join(lines))
    lines = list(notes)
    lines.append(
        "OK: for each of %s, the wired hook denied a foreign write and "
        "allowed the owner's own write, in a throwaway project that has been "
        "deleted." % ", ".join(WRITE_TOOL_NAMES))
    lines.append("")
    lines.append("What this does NOT prove, stated so it is not assumed:")
    lines.append(
        "  - The fence FAILS OPEN by design (docs/HOOKS.md). A missing store, "
        "a corrupt store, a store with no active claims, or any bug in the "
        "hook allows the write and prints a line starting "
        "'bm_fence_hook: FAILING OPEN'. A hook that fails closed would brick "
        "editing, which is a worse failure than an unenforced fence, so this "
        "is a choice and not an oversight.")
    lines.append(
        "  - Bash is NOT gated. Shell redirection, sed -i, tee, python -c and "
        "the rest write files without passing any hook. That is outside "
        "mechanical protection, by policy and not by accident.")
    lines.append(
        "  - Claude Code loads settings at session start, so a settings file "
        "corrected during a session is live at the NEXT session, not this one.")
    return _result("fence", STATUS_PASS, "\n".join(lines))


def check_version_identity(root):
    version_path = os.path.join(root, "VERSION")
    manifest_path = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.isfile(version_path) or not os.path.isfile(manifest_path):
        missing = version_path if not os.path.isfile(version_path) else manifest_path
        return _result("version", STATUS_SKIP,
                       "SKIP: %s is not there, so there is nothing to compare "
                       "this install root against." % _mask_home(missing))
    try:
        version_text = io.open(version_path, encoding="utf-8").read().strip()
    except (IOError, OSError) as exc:
        return _result("version", STATUS_FAIL,
                       "FAIL: could not read %s: %s" % (_mask_home(version_path), exc))
    try:
        manifest = json.loads(io.open(manifest_path, encoding="utf-8").read())
    except (IOError, OSError, ValueError) as exc:
        return _result("version", STATUS_FAIL,
                       "FAIL: could not read or parse %s: %s"
                       % (_mask_home(manifest_path), exc))
    manifest_version = manifest.get("version") if isinstance(manifest, dict) else None
    if manifest_version != version_text:
        return _result("version", STATUS_FAIL,
                       "FAIL: VERSION says %r but %s says %r. Bring them back "
                       "in agreement (docs/RELEASE.md's release-cut steps), "
                       "or this install cannot tell you honestly which "
                       "release it is." % (version_text, _mask_home(manifest_path),
                                           manifest_version))
    return _result("version", STATUS_PASS,
                   "PASS: VERSION and %s both say %s."
                   % (_mask_home(manifest_path), version_text))


def check_runtime():
    problems = []
    if sys.version_info < (3, 9):
        problems.append(
            "python3 is %s, older than the 3.9 this project needs. Install a "
            "newer python3 and re-run." % platform.python_version())
    if shutil.which("git") is None:
        problems.append(
            "git was not found on PATH. Install git so the update and "
            "release-verification steps that use it can run.")
    if problems:
        return _result("runtime", STATUS_FAIL, "FAIL: " + " ".join(problems))
    return _result("runtime", STATUS_PASS,
                   "PASS: python3 %s, git on PATH." % platform.python_version())


def _sh_wired_events(hooks_path):
    """Reads hooks/hooks.json and returns (event, statusMessage) for every
    event that wires at least one command starting with `sh` (matched on
    the command's first shell word, the same way find_fence_entries above
    matches a basename rather than guessing from raw text). Raises the
    same exceptions io.open/json.loads would; callers turn those into a
    FAIL rather than a crash, same as every other check in this file."""
    with io.open(hooks_path, encoding="utf-8") as fh:
        data = json.load(fh)
    events = data.get("hooks") if isinstance(data, dict) else None
    found = []
    for event, groups in sorted((events or {}).items()):
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for entry in group.get("hooks", []) or []:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if not isinstance(command, str):
                    continue
                try:
                    words = shlex.split(command)
                except ValueError:
                    words = command.split()
                if words and os.path.basename(words[0]) == "sh":
                    label = entry.get("statusMessage") or command
                    found.append("%s (%s)" % (event, label))
                    break
    return found


def check_windows_hooks(root):
    """V3 adoption defect: a Windows user who installs via the recommended
    path (docs/QUICKSTART.md Path 1, `claude plugin install`) never runs
    scripts/install.py, so its own Windows refusal (check_platform, around
    line 694) never fires, and Claude Code reports the install a success
    while several automatic hooks are wired to a POSIX shell that cmd.exe
    and PowerShell cannot run. This check is the thing that tells that
    user, since nothing else on that path will.

    Windows: FAILS whenever any event is wired through `sh`, naming the
    affected events by reading hooks/hooks.json directly rather than
    repeating a fixed list, so a future hook that starts using a shell is
    caught here without an edit to this check. Since the 2026-08-17 port
    every wired command is python3, so the healthy Windows answer is now a
    PASS that states its own limit: the wiring can run, and no Windows
    machine has yet run the battery, which is a different claim.

    Every other platform: SKIPs, never PASSes. A PASS would claim this
    machine was examined and found fine; it was not examined at all, so
    the only honest report is a SKIP naming what would have been checked
    (the same shape check_vault above uses for "setup never ran")."""
    hooks_path = os.path.join(root, "hooks", "hooks.json")
    if os.name != "nt":
        return _result("windows_hooks", STATUS_SKIP,
                       "SKIP: this machine is not Windows, so there is "
                       "nothing to check here. On Windows, this would read "
                       "%s and name which automatic behaviors cannot run, "
                       "because some of them are wired through a command "
                       "(`sh`) that only a POSIX shell can run."
                       % _mask_home(hooks_path))

    try:
        broken = _sh_wired_events(hooks_path)
    except (IOError, OSError, ValueError) as exc:
        return _result("windows_hooks", STATUS_FAIL,
                       "FAIL: this is Windows, where this project cannot "
                       "confirm its automatic behaviors work, and %s could "
                       "not even be read to say which ones (%s). Recommended: "
                       "install inside WSL (Windows Subsystem for Linux) "
                       "instead of directly on Windows; everything in this "
                       "project runs there normally."
                       % (_mask_home(hooks_path), exc))

    if not broken:
        # CHANGED 2026-08-17, when the three shell-wired events were ported
        # to python3. Before that day this branch was unreachable in
        # practice and said so; reaching it now means the manifest really
        # is python3 only, which is a genuinely different situation from
        # the one this check was written for.
        #
        # PASS, and the sentence is careful about what it claims: the
        # wiring can RUN here, which is what this check can actually see by
        # reading the manifest. Whether every hook then behaves correctly
        # on Windows is not something a manifest read can answer, and no
        # Windows machine has run this project's battery, so the message
        # says that rather than implying a verified platform.
        return _result("windows_hooks", STATUS_PASS,
                       "PASS: this is Windows, and every automatic behavior "
                       "in %s is wired through python3 rather than a POSIX "
                       "shell, so none of them is dead on arrival here. "
                       "Honest limit, worth reading before relying on it: "
                       "that is what the WIRING says, and no Windows "
                       "machine has yet run this project's test battery, so "
                       "correct BEHAVIOR on Windows is unverified rather "
                       "than proven. docs/WINDOWS-CHECK.md is the written "
                       "protocol for closing that, and scripts/install.py "
                       "still refuses to install here until somebody does."
                       % _mask_home(hooks_path))

    return _result("windows_hooks", STATUS_FAIL,
                   "FAIL: this is Windows, and %d of this project's "
                   "automatic behaviors will silently not run here, even "
                   "though the install will have reported success: %s. Each "
                   "one is wired through a command (`sh`) that only a POSIX "
                   "shell can run, and neither cmd.exe nor PowerShell is "
                   "one, so Claude Code will treat the hook as installed "
                   "while it quietly does nothing every time it fires. "
                   "Recommended: install inside WSL (Windows Subsystem for "
                   "Linux) instead of directly on Windows; everything in "
                   "this project runs there normally."
                   % (len(broken), ", ".join(broken)))


def check_consent():
    cfg, err = _bm_setup.read_config()
    cfg_path = _bm_setup.config_path()
    if err:
        return _result("consent", STATUS_FAIL,
                       "FAIL: the config at %s could not be read (%s). Run: "
                       "python3 scripts/setup.py --reconfigure"
                       % (_mask_home(cfg_path), err))
    if not _bm_setup.is_consented(cfg):
        return _result("consent", STATUS_FAIL,
                       "FAIL: setup has not been completed yet, so nothing "
                       "below that depends on it can be checked either. Run: "
                       "python3 scripts/setup.py")
    return _result("consent", STATUS_PASS,
                   "PASS: setup is complete (config at %s)." % _mask_home(cfg_path))


def check_vault(root):
    cfg, err = _bm_setup.read_config()
    if err or not _bm_setup.is_consented(cfg):
        return _result("vault", STATUS_SKIP,
                       "SKIP: setup has not been completed yet, so no vault "
                       "path is known. Run: python3 scripts/setup.py")
    vault_path = cfg.get("vault_path")
    if not isinstance(vault_path, str) or not vault_path:
        return _result("vault", STATUS_FAIL,
                       "FAIL: the config has no vault_path recorded. Run: "
                       "python3 scripts/setup.py --reconfigure")
    vault_path = os.path.expanduser(vault_path)
    if not os.path.isdir(vault_path):
        return _result("vault", STATUS_FAIL,
                       "FAIL: the vault path %s does not exist yet. Create it: "
                       "cp -R %s/vault-template %s"
                       % (_mask_home(vault_path), _mask_home(root), _mask_home(vault_path)))
    if not os.access(vault_path, os.W_OK):
        return _result("vault", STATUS_FAIL,
                       "FAIL: the vault path %s exists but is not writable by "
                       "you. Fix it: chmod u+w %s"
                       % (_mask_home(vault_path), _mask_home(vault_path)))
    return _result("vault", STATUS_PASS,
                   "PASS: the vault at %s exists and is writable." % _mask_home(vault_path))


def check_data_locations():
    """V3 Final B2. Answers "where does my data live" without a founder
    having to read an uninstaller to find out.

    It is PASS always and by design, because it reports rather than judges:
    there is no failing state for "your data is in these places". It is here
    because a product that markets trust owes a plain answer to that
    question in the same tool a user already runs, not buried in the
    removal path.

    The list is IMPORTED from scripts/uninstall.py rather than retyped, so
    the page that tells you where your data is and the script that acts on
    it can never disagree. It deletes nothing and reads nothing but the
    environment.

    The import is LAZY and inside this function on purpose. doctor.py is
    copied next to setup.py alone for install rehearsals, and a module-level
    import of a third script would crash the whole tool in that shape rather
    than costing it one check. Where the shared definition genuinely cannot
    be read, this check SKIPS and names why. It must never answer from a
    second copy of the list held here: two copies is the drift importing it
    was meant to prevent, and a stale answer to "where is my data" is worse
    than an honest refusal to answer."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import uninstall as _uninstall
    except ImportError as exc:
        return _result(
            "data_locations", STATUS_SKIP,
            "SKIP: scripts/uninstall.py is not beside this file, and it "
            "holds the one list of where your data lives (%s). Run this "
            "from a full checkout to get the answer." % exc)
    finally:
        sys.path.pop(0)
    # Mask the vault path BEFORE it is interpolated into a sentence.
    # _mask_home works on a path, not on prose that happens to contain one,
    # so masking the finished line leaves the home directory in the output
    # while every other line in this tool has it hidden.
    vault = os.environ.get("BROTHERMODE_VAULT")
    lines = _uninstall.data_locations(_mask_home(vault) if vault else None)
    return _result("data_locations", STATUS_PASS,
                   "PASS: your data lives in these places, and nothing here "
                   "touched any of them:\n    - %s" % "\n    - ".join(lines))


def _plugin_name(root):
    """The name this project's own plugin manifest claims, so a duplicate
    check names the real plugin rather than a hardcoded guess. Falls back to
    'brotherme' (the shipped name) when the manifest cannot be read, which
    keeps the check usable rather than silently disabled."""
    manifest_path = os.path.join(root, ".claude-plugin", "plugin.json")
    try:
        manifest = json.loads(io.open(manifest_path, encoding="utf-8").read())
    except (IOError, OSError, ValueError):
        return "brotherme"
    name = manifest.get("name") if isinstance(manifest, dict) else None
    return name if isinstance(name, str) and name else "brotherme"


def _plugin_enabled(settings, plugin_name):
    """True when settings.json's own enabledPlugins map names this plugin,
    under any marketplace suffix ('brotherme@some-marketplace'), as truthy.
    This is the real, observed mechanism (Claude Code writes enabledPlugins
    into settings.json itself when a plugin is installed and enabled), not a
    guess at how plugin hooks might appear."""
    enabled = settings.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return False
    for key, value in enabled.items():
        if not value or not isinstance(key, str):
            continue
        if key.split("@", 1)[0] == plugin_name:
            return True
    return False


def _clone_fence_present(settings):
    """True when settings.json has a PreToolUse entry naming a REAL,
    on-disk bm_fence_hook.py: the shape scripts/install.py writes. A plugin
    hook command such as '${CLAUDE_PLUGIN_ROOT}/tools/bm_fence_hook.py' fails
    os.path.isfile (the variable is never expanded here), so it is correctly
    not counted as a clone install by this same test."""
    for _group, _entry, _command, words in find_fence_entries(settings):
        path = fence_path_in(words)
        if path and os.path.isfile(path):
            return True
    return False


def check_duplicate_install(settings, settings_err, settings_path, root):
    if settings_err:
        return _result("duplicate_install", STATUS_SKIP,
                       "SKIP: %s could not be read, so this cannot be "
                       "checked here; see the settings.json check below."
                       % _mask_home(settings_path))
    plugin_name = _plugin_name(root)
    has_plugin = _plugin_enabled(settings, plugin_name)
    has_clone = _clone_fence_present(settings)
    if has_plugin and has_clone:
        return _result("duplicate_install", STATUS_FAIL,
                       "FAIL: both the plugin (%s is enabled in %s) and a "
                       "clone install (a PreToolUse entry wired to a real "
                       "bm_fence_hook.py on disk) are present, so every hook "
                       "fires twice. Keep one: run '/plugin uninstall %s' to "
                       "remove the plugin wiring, or run 'python3 "
                       "scripts/uninstall.py' to remove the clone wiring."
                       % (plugin_name, _mask_home(settings_path), plugin_name))
    which = "plugin" if has_plugin else ("clone" if has_clone else "neither")
    return _result("duplicate_install", STATUS_PASS,
                   "PASS: only one install method (%s) is wired in %s."
                   % (which, _mask_home(settings_path)))


def check_install_shape(settings, settings_err, settings_path, root):
    """M1 (docs/plan/QUEUE.json), part (a): the install-shape detector.

    Measured on the founder's own machine (docs/NORTH-STAR-CHAIN.md
    finding 1): a skill-directory clone sits at ~/.claude/skills/
    brothermode, its own hooks/hooks.json carries no reference to
    bm_fence_hook.py (or the file is missing outright), and settings.json
    holds no fence wiring either. loader_managed_fence_copies() only
    counts a skill-dir copy whose OWN hooks.json really does name the
    fence, so a copy in exactly this broken shape is invisible to it, and
    check_duplicate_install above reports a bare PASS for "neither method
    is wired" (its question is "is more than one wired", not "is any
    wired at all"). This check asks the question neither of those asks:
    given whatever install shape IS present on disk (skill-directory
    clone, plugin, or nothing), is its fence hook actually wired
    somewhere real. NO-DATA (STATUS_SKIP) when settings.json cannot be
    read, or when no install evidence of ANY kind exists to judge the
    shape of; FAIL, naming the stranded path, when a clone is present but
    nothing loads it; PASS otherwise. Never a silent pass for "nothing
    found"."""
    if settings_err:
        return _result(
            "install_shape", STATUS_SKIP,
            "NO-DATA: %s could not be read (%s), so whether this is a "
            "skill-directory clone or a plugin install, and whether its "
            "fence hook is wired, cannot be determined."
            % (_mask_home(settings_path), settings_err))
    plugin_name = _plugin_name(root)
    home = os.path.expanduser("~")
    skill_dir = os.path.join(home, ".claude", "skills", plugin_name)
    has_skill_dir = os.path.isdir(skill_dir)
    has_plugin = _plugin_enabled(settings, plugin_name)
    has_settings_wiring = _clone_fence_present(settings)
    loader_copies = loader_managed_fence_copies()
    loader_wired = has_skill_dir and any(
        os.path.normpath(copy) == os.path.normpath(skill_dir)
        for copy in loader_copies)

    if not (has_skill_dir or has_plugin or has_settings_wiring):
        return _result(
            "install_shape", STATUS_SKIP,
            "NO-DATA: no plugin (%s is not in enabledPlugins), no "
            "settings.json fence entry, and no skill-directory clone at "
            "%s were found, so there is no install shape to judge here."
            % (plugin_name, _mask_home(skill_dir)))

    if has_plugin:
        shape = "a plugin install (%s enabled in %s)" % (
            plugin_name, _mask_home(settings_path))
    elif has_settings_wiring:
        shape = "a clone install with a settings.json fence entry"
    else:
        shape = "a skill-directory clone at %s" % _mask_home(skill_dir)

    if has_plugin or has_settings_wiring or loader_wired:
        return _result("install_shape", STATUS_PASS,
                       "PASS: %s, and its fence hook is wired." % shape)
    return _result(
        "install_shape", STATUS_FAIL,
        "FAIL: %s exists, but no fence hook is wired for it anywhere: "
        "not in %s, and its own hooks/hooks.json does not name %s (or is "
        "missing). This is a STRANDED install: the files are on disk and "
        "nothing loads them. Run: python3 scripts/install.py --upgrade, "
        "or remove the stranded copy at %s and reinstall."
        % (shape, _mask_home(settings_path), FENCE_BASENAME,
           _mask_home(skill_dir)))


def check_store_health(doctor_root):
    store_path = os.path.join(os.getcwd(), ".brothermode", "store.sqlite3")
    if not os.path.isfile(store_path):
        return _result("store", STATUS_SKIP,
                       "SKIP: no project store at %s under the current "
                       "directory." % _mask_home(store_path))
    bm_store = os.path.join(doctor_root, "tools", "bm_store.py")
    if not os.path.isfile(bm_store):
        return _result("store", STATUS_FAIL,
                       "FAIL: a store exists at %s but %s is not there to "
                       "verify it. Reinstall BrotherMode to restore it: "
                       "python3 scripts/install.py --upgrade (or update via "
                       "your plugin manager)."
                       % (_mask_home(store_path), _mask_home(bm_store)))
    try:
        r = subprocess.run(
            [sys.executable, bm_store, "verify"], cwd=os.getcwd(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return _result("store", STATUS_FAIL,
                       "FAIL: could not run %s verify: %s" % (bm_store, exc))
    verdict = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return _result("store", STATUS_FAIL,
                       "FAIL: %s" % (verdict or
                                    "bm_store.py verify exited %d with no "
                                    "output" % r.returncode))
    return _result("store", STATUS_PASS,
                   "PASS: %s" % (verdict or "bm_store.py verify reports "
                                            "healthy."))


def check_hook_wiring_matches_mode(settings, settings_err, settings_path, root):
    cfg, cfg_err = _bm_setup.read_config()
    if cfg_err or not _bm_setup.is_consented(cfg):
        return _result("mode_wiring", STATUS_SKIP,
                       "SKIP: setup has not been completed yet, so "
                       "installation_mode is not known. Run: python3 "
                       "scripts/setup.py")
    if settings_err:
        return _result("mode_wiring", STATUS_FAIL,
                       "FAIL: %s could not be read (%s), so hook wiring "
                       "cannot be compared against installation_mode."
                       % (_mask_home(settings_path), settings_err))
    mode = cfg.get("installation_mode")
    plugin_name = _plugin_name(root)
    has_plugin = _plugin_enabled(settings, plugin_name)
    has_clone = _clone_fence_present(settings)
    if mode == "clone":
        if has_clone:
            return _result("mode_wiring", STATUS_PASS,
                           "PASS: installation_mode is clone and a clone "
                           "hook wiring is present in %s." % _mask_home(settings_path))
        loader = loader_managed_fence_copies()
        if loader:
            return _result("mode_wiring", STATUS_PASS,
                           "PASS: installation_mode is clone with no "
                           "settings.json block, and %s is auto-loaded by "
                           "Claude Code as a plugin carrying its own hook "
                           "wiring, which is the loader-managed clone "
                           "shape (a clone parked under ~/.claude/skills)."
                           % _mask_home(loader[0]))
        return _result("mode_wiring", STATUS_FAIL,
                       "FAIL: installation_mode is clone but no BrotherMode "
                       "hook wiring was found in %s. Run: python3 "
                       "scripts/install.py" % _mask_home(settings_path))
    if mode == "plugin":
        if has_plugin:
            return _result("mode_wiring", STATUS_PASS,
                           "PASS: installation_mode is plugin and %s is "
                           "enabled in %s." % (plugin_name, _mask_home(settings_path)))
        return _result("mode_wiring", STATUS_FAIL,
                       "FAIL: installation_mode is plugin but %s is not "
                       "enabled in %s. Run: /plugin install %s"
                       % (plugin_name, _mask_home(settings_path), plugin_name))
    return _result("mode_wiring", STATUS_FAIL,
                   "FAIL: the config's installation_mode is %r, which is "
                   "neither plugin nor clone. Run: python3 scripts/setup.py "
                   "--reconfigure" % (mode,))


def _git_tree_state(root):
    """Returns 'dirty', 'clean', or None (not a git working tree, or git is
    not available). Used only to tell a genuine half-finished RELEASE update
    (a checked-out tag or branch, always clean the moment the checkout
    finishes) apart from a live development tree with ordinary uncommitted
    edits, which a checked-in manifest was never generated to describe and
    was never going to match. Best-effort: any git failure here is treated
    as 'cannot tell', which check_checksums treats the same as 'clean' (run
    the check for real) rather than silently skipping on an ambiguous
    answer."""
    if shutil.which("git") is None:
        return None
    # A LINKED worktree (git worktree add) and a submodule both keep a .git
    # FILE holding a gitdir: pointer where an ordinary checkout keeps a .git
    # DIRECTORY. Both are real working trees whose state git reports
    # perfectly well, so both must be accepted here; accepting only the
    # directory returned 'cannot tell' for a tree that could be told, the
    # SKIP guard below never fired, and check_checksums compared a live
    # edited worktree against a release manifest as though it were clean.
    # Requiring .git AT root (rather than letting git walk upwards) is still
    # deliberate: a plain unpacked release sitting inside somebody else's
    # repository must not be judged by that repository's tree state.
    dot_git = os.path.join(root, ".git")
    if not (os.path.isdir(dot_git) or os.path.isfile(dot_git)):
        return None
    # `-C <root>` does NOT beat GIT_DIR or GIT_WORK_TREE: git honours those over
    # the working directory, so an inherited one makes this report ANOTHER
    # repository's cleanliness, and check_checksums trusts this answer to decide
    # whether the integrity comparison runs at all. Same class as the fix in
    # tools/bm_autosave.py and tools/bm_controller.py; reproduced 2026-08-06.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return "dirty" if r.stdout.strip() else "clean"


def _git_head_commit(root):
    """Returns (commit, None) or (None, reason). Same GIT_* stripping as
    _git_tree_state above and for the same reason: an inherited GIT_DIR or
    GIT_WORK_TREE would report another repository's HEAD as this tree's own."""
    if shutil.which("git") is None:
        return None, "git is not on PATH"
    dot_git = os.path.join(root, ".git")
    if not (os.path.isdir(dot_git) or os.path.isfile(dot_git)):
        return None, "%s is not a git working tree" % _mask_home(root)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "git could not be run: %s" % exc
    if r.returncode != 0:
        return None, ("git could not read HEAD: %s"
                      % (r.stderr or "").strip()[:200])
    commit = r.stdout.strip()
    if len(commit) != 40:
        return None, "git returned an unexpected commit id: %r" % commit
    return commit, None


def check_install_identity(root):
    """SBE1: Claude Code runs the INSTALLED tree, a separate clone from
    wherever a session happens to be working. This check answers a question
    check_version_identity cannot: it compares the commit this tree was
    STAMPED with at install time (scripts/install.py writes INSTALLED-FROM
    from the SOURCE tree's own git HEAD) against the commit this tree
    itself is at right now, per git run IN THIS tree. VERSION-vs-manifest
    is compared inside one tree and agrees even when two trees have
    diverged real source, which is exactly the gap measured 2026-08-15: a
    tree 22 commits behind still reported the same VERSION as its source.
    A check that cannot tell (no stamp, an "unknown" stamp, or no git here)
    must never read as a pass, so every such case is SKIP, never PASS."""
    stamp_path = os.path.join(root, INSTALLED_FROM_NAME)
    if not os.path.isfile(stamp_path):
        return _result(
            "install_identity", STATUS_SKIP,
            "SKIP: no %s at %s. Either this install predates the stamp, or "
            "it was set up by hand instead of scripts/install.py, so there "
            "is nothing to compare this tree's commit against. Run: python3 "
            "scripts/install.py --upgrade to add one." % (
                INSTALLED_FROM_NAME, _mask_home(stamp_path)))
    try:
        text = io.open(stamp_path, encoding="utf-8").read()
    except (IOError, OSError) as exc:
        return _result("install_identity", STATUS_FAIL,
                       "FAIL: %s could not be read: %s"
                       % (_mask_home(stamp_path), exc))
    lines = text.splitlines()
    stamped_commit = lines[0].strip() if lines else ""
    if not stamped_commit or stamped_commit == "unknown":
        reason = lines[2].strip() if len(lines) > 2 else "not recorded at install time"
        return _result(
            "install_identity", STATUS_SKIP,
            "SKIP: %s records the source commit as unknown (%s), so this "
            "tree's identity cannot be checked against it." % (
                _mask_home(stamp_path), reason))
    if len(stamped_commit) != 40:
        return _result(
            "install_identity", STATUS_FAIL,
            "FAIL: %s does not hold a 40 character commit id (got %r). "
            "Re-run: python3 scripts/install.py --upgrade"
            % (_mask_home(stamp_path), stamped_commit[:80]))
    running_commit, err = _git_head_commit(root)
    if running_commit is None:
        return _result(
            "install_identity", STATUS_SKIP,
            "SKIP: this tree's own commit could not be determined (%s), so "
            "it cannot be compared against the %s stamp (%s)."
            % (err, INSTALLED_FROM_NAME, stamped_commit[:12]))
    if running_commit == stamped_commit:
        return _result(
            "install_identity", STATUS_PASS,
            "PASS: this tree is running the same commit (%s) it was "
            "installed from." % stamped_commit[:12])
    return _result(
        "install_identity", STATUS_FAIL,
        "FAIL: this tree is at commit %s but %s says it was installed from "
        "%s. The running tree and the code that wired its hooks have "
        "drifted apart. Resync: from an up-to-date source checkout, run "
        "python3 scripts/install.py --upgrade --target %s"
        % (running_commit[:12], _mask_home(stamp_path), stamped_commit[:12],
           _mask_home(root)))


def check_stranded_install(root):
    """C5: check_install_identity above proves this tree is at the commit it
    was installed from; it says nothing about whether the PUBLIC tag every
    onboarding page pins (PUBLIC_INSTALL_TAG in tools/bm_project_facts.py)
    has since been moved on the public origin, for example by a re-tag after
    a bad cut. A tree can pass install_identity and still be a STRANDED
    install: correctly matching a commit that the public tag no longer
    names. This check compares what refs/tags/<tag> resolves to LOCALLY
    against what git ls-remote reports for the same ref on the public
    origin.

    Needs a local git, the tag existing locally, and the public origin
    reachable: any one of those missing is NO-DATA, never a silent PASS,
    because a check that cannot tell must never read as proof nothing is
    wrong."""
    facts_path = os.path.join(root, "tools", "bm_project_facts.py")
    if not os.path.isfile(facts_path):
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: %s is not there, so the public install tag could not "
            "be read." % _mask_home(facts_path))
    try:
        spec = importlib.util.spec_from_file_location(
            "bm_project_facts_for_doctor", facts_path)
        bpf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bpf)
        tag = bpf.PUBLIC_INSTALL_TAG
        repo_url = bpf.REPO_URL
    except Exception as exc:
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: %s could not be read (%s: %s), so the public install "
            "tag could not be read."
            % (_mask_home(facts_path), type(exc).__name__, exc))

    if shutil.which("git") is None:
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: git is not on PATH, so neither the local tag nor the "
            "public origin can be resolved.")
    # Same GIT_* stripping as _git_head_commit above, same reason: an
    # inherited GIT_DIR or GIT_WORK_TREE would resolve another repository's
    # tag instead of this tree's own.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    ref = "refs/tags/%s" % tag
    try:
        local = subprocess.run(
            ["git", "-C", root, "rev-parse", ref],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: could not run git to resolve local tag %s (%s: %s)."
            % (tag, type(exc).__name__, exc))
    if local.returncode != 0:
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: tag %s does not exist in this local tree (%s), so "
            "there is nothing to compare against the public origin."
            % (tag, local.stderr.strip() or "git rev-parse failed"))
    local_sha = local.stdout.strip()

    try:
        remote = subprocess.run(
            ["git", "ls-remote", repo_url, ref],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: could not reach the public origin to resolve %s (%s: "
            "%s). No network, or the remote is unreachable." % (
                tag, type(exc).__name__, exc))
    if remote.returncode != 0 or not remote.stdout.strip():
        return _result(
            "stranded_install", STATUS_SKIP,
            "NO-DATA: git ls-remote %s %s returned nothing (%s), so the "
            "public origin's commit for this tag could not be read. No "
            "network, or the remote is unreachable."
            % (repo_url, ref, remote.stderr.strip() or "no output"))
    remote_sha = remote.stdout.split()[0].strip()
    if remote_sha == local_sha:
        return _result(
            "stranded_install", STATUS_PASS,
            "PASS: the public install tag %s resolves to %s here, the same "
            "commit the public origin resolves it to." % (tag, local_sha[:12]))
    return _result(
        "stranded_install", STATUS_FAIL,
        "FAIL: the public install tag %s resolves to %s in this tree but "
        "%s on the public origin: this install is STRANDED behind a moved "
        "tag. Recovery: docs/PUBLIC-RELEASE-CHECK-2026-08-18.md."
        % (tag, local_sha[:12], remote_sha[:12]))


def check_checksums(root):
    manifest_path = os.path.join(root, "CHECKSUMS.sha256")
    if not os.path.isfile(manifest_path):
        return _result("checksums", STATUS_SKIP,
                       "SKIP: no CHECKSUMS.sha256 at %s." % _mask_home(manifest_path))
    if _git_tree_state(root) == "dirty":
        # A9 fix (loop6 refuter finding): say in one plain sentence that
        # the integrity check did NOT run, so this SKIP can never be
        # misread as a PASS (a plain doctor run, without --strict, exits 0
        # on a SKIP exactly as it would on every check passing).
        return _result("checksums", STATUS_SKIP,
                       "SKIP: the file-integrity check did NOT run this "
                       "time. %s is a live git working tree with "
                       "uncommitted changes, so CHECKSUMS.sha256 (generated "
                       "from a clean, checked-out release) cannot be "
                       "compared honestly here, and this SKIP is not proof "
                       "your files are untampered. Commit your work or "
                       "check out a clean tag, then re-run this check."
                       % _mask_home(root))
    try:
        text = io.open(manifest_path, encoding="utf-8").read()
    except (IOError, OSError) as exc:
        return _result("checksums", STATUS_FAIL,
                       "FAIL: could not read %s: %s" % (_mask_home(manifest_path), exc))
    problems = []
    listed = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        want_hash, rel_path = parts
        listed += 1
        full = os.path.join(root, rel_path)
        if not os.path.isfile(full):
            problems.append("%s is listed but missing" % rel_path)
            continue
        digest = hashlib.sha256()
        try:
            with io.open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
        except (IOError, OSError) as exc:
            problems.append("%s could not be read (%s)" % (rel_path, exc))
            continue
        if digest.hexdigest().lower() != want_hash.strip().lower():
            problems.append("%s does not match its checksum" % rel_path)
    if problems:
        shown = problems[:5]
        more = "" if len(problems) <= 5 else (
            " and %d more" % (len(problems) - 5))
        return _result("checksums", STATUS_FAIL,
                       "FAIL: %d of %d listed file(s) do not match "
                       "CHECKSUMS.sha256: %s%s. This catches a half-finished "
                       "update; re-run the update steps (commands/"
                       "brotherme-update.md) or restore the named file(s)."
                       % (len(problems), listed, "; ".join(shown), more))
    return _result("checksums", STATUS_PASS,
                   "PASS: all %d file(s) listed in CHECKSUMS.sha256 match."
                   % listed)


def check_settings_json(settings_path):
    _settings, err = read_settings(settings_path)
    if err:
        return _result("settings_json", STATUS_FAIL, "FAIL: %s" % err)
    return _result("settings_json", STATUS_PASS,
                   "PASS: %s is valid JSON." % _mask_home(settings_path))


def run_all_checks(settings_path):
    """Runs every check in the pinned order and returns the list of ten
    CheckResults. Settings is read ONCE here and the parsed dict (or an
    empty one, with the error carried alongside) is handed to every check
    that needs it, so ten checks never disagree about what settings.json
    said because one of them re-read it after a change."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    settings, settings_err = read_settings(settings_path)
    settings_for_scan = settings if isinstance(settings, dict) else {}

    return [
        _run_check("fence", check_fence, settings_path),
        _run_check("version", check_version_identity, root),
        _run_check("runtime", check_runtime),
        _run_check("windows_hooks", check_windows_hooks, root),
        _run_check("consent", check_consent),
        _run_check("vault", check_vault, root),
        _run_check("duplicate_install", check_duplicate_install,
                   settings_for_scan, settings_err, settings_path, root),
        _run_check("install_shape", check_install_shape,
                   settings_for_scan, settings_err, settings_path, root),
        _run_check("store", check_store_health, root),
        _run_check("mode_wiring", check_hook_wiring_matches_mode,
                   settings_for_scan, settings_err, settings_path, root),
        _run_check("checksums", check_checksums, root),
        _run_check("settings_json", check_settings_json, settings_path),
        _run_check("install_identity", check_install_identity, root),
        _run_check("stranded_install", check_stranded_install, root),
        _run_check("data_locations", check_data_locations),
    ]


def build_parser():
    p = argparse.ArgumentParser(
        prog="doctor.py",
        description="Ten checks: is this BrotherMode install healthy, in "
                    "plain words, PASS or a one-sentence fix for each.")
    p.add_argument("--settings", default=None,
                   help="Claude Code settings file "
                        "(default ~/.claude/settings.json)")
    p.add_argument("--json", action="store_true",
                   help="print one JSON object instead of plain text")
    p.add_argument("--strict", action="store_true",
                   help="treat any SKIP as a failure too: exit nonzero and "
                        "list each skipped check and why (default: a SKIP "
                        "can be legitimate and still exits 0)")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 9):
        _err("doctor.py: needs Python 3.9 or newer; this interpreter is %s"
             % platform.python_version())
        return EXIT_UNSUPPORTED
    settings_path = os.path.abspath(args.settings or default_settings_path())

    checks = run_all_checks(settings_path)
    proven = sum(1 for c in checks if c.status == STATUS_PASS)
    skipped = sum(1 for c in checks if c.status == STATUS_SKIP)
    fail_count = sum(1 for c in checks if c.status == STATUS_FAIL)
    # I2 (external review, Loop 3/5): STATUS_SKIP used to be folded into
    # "all_ok" with no visible count anywhere, so a run that only ever
    # SKIPPED (no store, no manifest, setup never run) read on the exit
    # code alone exactly like a run that actually PROVED ten things. The
    # exit code still accepts a SKIP as legitimate by default (a SKIP can be
    # an honest "nothing to check yet"), but the summary line below states
    # all three counts every time, so a vacuous pass is impossible to
    # misread; --strict is the opt-in for a caller (a release gate, a CI
    # job) that wants every SKIP treated as a blocker instead of a shrug.
    summary = ("%d of %d proven, %d skipped, %d failed."
              % (proven, len(checks), skipped, fail_count))
    ok = fail_count == 0 and not (args.strict and skipped)

    if args.json:
        payload = {
            "settings": _mask_home(settings_path),
            "ok": fail_count == 0,
            "strict": args.strict,
            "strict_ok": ok,
            "proven": proven,
            "skipped": skipped,
            "failed": fail_count,
            "summary": summary,
            "checks": [
                {"id": i, "key": c.key, "title": c.title,
                 "status": c.status, "message": c.message}
                for i, c in enumerate(checks, 1)
            ],
        }
        _out(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK if ok else EXIT_PROBLEMS

    _out("BrotherMode doctor: %d checks" % len(checks))
    _out("  settings: %s" % _mask_home(settings_path))
    for i, c in enumerate(checks, 1):
        _out("")
        _out("[%d/%d] %s: %s" % (i, len(checks), c.title, c.status))
        for line in c.message.splitlines():
            _out("  %s" % line)
    _out("")
    _out(summary)
    if fail_count:
        _out("%d of %d checks FAILED. Follow each remediation above, then "
             "re-run this command." % (fail_count, len(checks)))
        return EXIT_PROBLEMS
    if args.strict and skipped:
        _out("--strict: %d check(s) skipped, and --strict treats a SKIP as "
             "a failure:" % skipped)
        for c in checks:
            if c.status == STATUS_SKIP:
                reason = c.message.splitlines()[0] if c.message else "(no reason given)"
                _out("  - %s: %s" % (c.title, reason))
        return EXIT_PROBLEMS
    _out("All %d checks passed (SKIP is not a failure unless --strict; "
         "see the reason printed next to it)." % len(checks))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
