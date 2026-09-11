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
import datetime
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

try:
    import tomllib  # Python 3.11+ standard library; D3 prefers this parser
except ImportError:  # pragma: no cover - Python 3.9/3.10 fall back to a line scan
    tomllib = None

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED = 3
# D5 (repair, adversarial review 2026-09-09): the DOC-0 --status table's
# own third outcome, a table where no row FAILs but none reaches PASS
# either (every row landed on NO-DATA, N/A, NEEDS TRUST or NOT
# CONFIGURED) -- the "population of all NO-DATA composed into a PASS"
# failure the reviewer drove by forcing all six rows.
#
# D11 (repair, adversarial review round 3, 2026-09-10): this used to
# share its integer with EXIT_UNSUPPORTED on the theory that the two
# meanings never collide within one invocation (main() returns
# EXIT_UNSUPPORTED before --status ever runs). That theory is true and
# beside the point: a caller who reads only the exit code, the whole
# reason an exit code exists, cannot tell "no row passed" from "wrong
# Python" from the integer alone, and has to go read stdout to find out
# which one actually happened -- exactly the failure this file's own
# design note above ("a guard can express its verdict in stdout, not the
# exit code") already names. EXIT_NO_PASS now has its own integer,
# unused by any other EXIT_ constant here.
EXIT_NO_PASS = 4

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


def _mask_home_text(text):
    """Like _mask_home, but for a block of captured text (subprocess
    stdout, an exception str(), a probed detail message) rather than a
    single path argument: the home directory can appear ANYWHERE in
    such text, and more than once, so this replaces every occurrence
    rather than checking only a leading prefix. Finding 6 (security
    review, 2026-09-10): _mask_home alone left the account name
    reaching printed rows through bundle_runtime --check output,
    gits own exception text, codex hooks detail, and capability_probe
    vault detail and tried list, because none of those strings is a
    bare path argument. Never raises; a non-string input passes
    through unchanged."""
    if not isinstance(text, str) or not text:
        return text
    home = os.path.expanduser("~")
    if not home:
        return text
    return text.replace(home, "/Users/...")


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


def _hooks_json_names_the_fence(directory):
    """True when `directory`/hooks/hooks.json exists and names the fence
    basename. Best effort in the same sense as its caller: a missing or
    unreadable file is simply not a match, never an exception."""
    hooks = os.path.join(directory, "hooks", "hooks.json")
    try:
        with io.open(hooks, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:  # sbe: allow-silent no hooks.json means this copy wires nothing, not a problem
        return False
    return FENCE_BASENAME in text


def _loader_fence_path(directory):
    """The fence script `directory`/hooks/hooks.json actually runs, with
    ${CLAUDE_PLUGIN_ROOT} read as `directory` the way the loader expands it.
    The product's own layout puts it at tools/, but the umbrella bundle
    installs it under runtime/hooks/brothermode/tools/ (found 2026-09-11 on
    the 1.0.12 plugin cache, where check 1 called a live fence dead because
    it only ever looked at tools/). Falls back to tools/ when the command
    cannot be read, so a missing file is still reported as missing."""
    default = os.path.join(directory, "tools", FENCE_BASENAME)
    hooks = os.path.join(directory, "hooks", "hooks.json")
    try:
        with io.open(hooks, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):  # sbe: allow-silent unreadable hooks.json falls back to the tools/ layout, whose absence is then reported
        return default
    for group in ((data.get("hooks") or {}).get("PreToolUse") or []
                  if isinstance(data, dict) else []):
        for hook in (group.get("hooks") or [] if isinstance(group, dict) else []):
            command = hook.get("command") if isinstance(hook, dict) else None
            if not isinstance(command, str) or FENCE_BASENAME not in command:
                continue
            try:
                words = shlex.split(
                    command.replace("${CLAUDE_PLUGIN_ROOT}", directory))
            except ValueError:  # sbe: allow-silent an unparseable command falls back to the tools/ layout
                continue
            path = fence_path_in(words)
            if path:
                return path
    return default


def _loader_recorded_plugin_paths():
    """Every installPath Claude Code's own ~/.claude/plugins/
    installed_plugins.json records, in file order. This is the loader's
    record of what it actually loaded, so it resolves the version
    directory without doctor having to sort version strings and pick a
    winner. Best effort: a missing, unreadable or unexpectedly shaped
    file yields nothing rather than raising, because a machine with no
    plugin installs is the common case, not a fault."""
    record = os.path.join(
        os.path.expanduser("~"), ".claude", "plugins",
        "installed_plugins.json")
    try:
        with io.open(record, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):  # sbe: allow-silent absent or malformed record means no recorded copies, per docstring
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return []
    paths = []
    for entries in plugins.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("installPath")
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
    return paths


def loader_managed_fence_copies():
    """BrotherMode copies Claude Code auto-loads as plugins, each carrying
    its own hook wiring so settings.json holds no block at all: directories
    under ~/.claude/skills/<name>/ (the skills-dir auto-load) and under the
    plugin cache ~/.claude/plugins/cache/<marketplace>/<name>/, whose own
    hooks/hooks.json names the fence basename. Added in the finalization
    run 2026-08-08, when this machine's duplicated settings.json wiring was
    retired in favor of the plugin loader and doctor had no model for that
    shape. Best effort: an unreadable directory is simply not a match, and
    a private HOME (the test fixtures) sees only its own tree.

    THE CACHE IS VERSION KEYED, found 2026-09-04 on the founder's own
    machine: a `/plugin install` lands the plugin at
    cache/<marketplace>/<name>/<version>/, one level deeper than the scan
    below reaches, so a correctly installed and live fence was invisible
    here and doctor's check 1 said "NO FENCE HOOK IS WIRED" while check 8
    said the same plugin's fence was wired. Guessing the version directory
    would be a second guess; ~/.claude/plugins/installed_plugins.json is
    the loader's OWN record of which path it loaded, so that is what is
    read first, and the directory scan stays for the skills-dir shape and
    for any flat cache layout."""
    out = []
    for path in _loader_recorded_plugin_paths():
        if _hooks_json_names_the_fence(path):
            out.append(path)
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
                candidate = os.path.join(base, name)
                if candidate not in out:
                    out.append(candidate)
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

        # E50 SCOPING, and without this the whole simulation measures the
        # wrong thing. An installer that left the scope marker beside the
        # settings file runs its hooks ONLY in a repository carrying
        # .brother/config, so in a throwaway directory nobody opted in the
        # fence correctly stands down and this function reported "the hook is
        # wired but it is not enforcing" on every fresh install. The question
        # here is whether the fence ENFORCES where it applies, so the
        # throwaway project is opted in exactly as a real one would be. It is
        # inside the mkdtemp this function deletes, so nothing outside is
        # touched. Whether the FOUNDER'S OWN repository is opted in is a
        # different question, and _verify_real_store() below is where it is
        # asked against the real store.
        # The .git entry is part of the opt-in, not decoration:
        # bm_repo_scope.find_repo_root walks up for a .git entry with plain
        # os.path (it never invokes git) and only then looks for
        # .brother/config beside it, so a throwaway with the config and no
        # .git resolves to no repository at all and reads as not opted in.
        os.makedirs(os.path.join(root, ".git"), exist_ok=True)
        os.makedirs(os.path.join(root, ".brother"), exist_ok=True)
        with io.open(os.path.join(root, ".brother", "config"), "w",
                     encoding="utf-8") as fh:
            fh.write("# doctor simulation opt-in, deleted when doctor exits\n")

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
            fence = _loader_fence_path(copy)
            notes.append("no settings.json fence block, and none is "
                         "needed: %s is auto-loaded by Claude Code as a "
                         "plugin and registers its own hooks/hooks.json, "
                         "fence included. Liveness is proven below against "
                         "that copy." % _mask_home(copy))
            if not os.path.isfile(fence):
                return (problems + [
                    "the loader-managed copy at %s wires a fence in its "
                    "hooks/hooks.json but carries no %s file, so the "
                    "registered hook is dead: Claude Code will report a "
                    "hook error and continue, and writes proceed unfenced."
                    % (_mask_home(copy), os.path.relpath(fence, copy))], notes)
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
# C-3 (repair, backend review 2026-09-10): a distinct status for a DOC-0
# row whose function raised, so _status_exit_code can tell "this row
# crashed" apart from an ordinary NO-DATA reading instead of both
# collapsing into the same exit-0-eligible bucket a crash must never
# share.
STATUS_CRASHED = "CRASHED"
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


# ---------------------------------------------------------------------------
# DOC-0: the six-row status table. Separate from the fifteen-check surface
# above (a different question: not "is every part of this install healthy"
# but "can I trust the safety mechanisms and the recovery state right now,
# in six lines"). Reuses that surface's own helpers (_mask_home, check_fence,
# _git_tree_state, STATUS_* constants) rather than duplicating them.
#
# DECISION (non_goals asked for one, with its reason, here): this table
# lives in doctor.py rather than becoming a new top-level command, because
# rows 3 and 6 already need check_fence and _git_tree_state, which are
# private to this file, and doctor.py is already "one command, plain
# status" by charter (see the module docstring). --status is a new flag on
# the existing parser, not a new script.
#
# WHY CODEX TRUST IS NEVER CONFIRMED BY ASKING CODEX (measured 2026-09-09,
# read again below the row-4 function): this file's own module docstring
# and the founder's own instructions for this run both say the status path
# must never mutate the real ~/.codex. scripts/codex_hooks_install.py's
# `hooks/list` read-back (the only way to ask Codex whether a hook is
# TRUSTED rather than merely WIRED) runs Codex's own app-server as a
# subprocess, and a live measurement on this machine (a throwaway
# CODEX_HOME, snapshotted by file hash before and after one single
# hooks/list call) showed several sqlite databases and a lock file changed
# even though the call itself requests no write. So row 4 never invokes it;
# see _status_codex_hooks for the safe, file-only signal it uses instead
# and the honest limit that leaves it stating instead of asserting PASS.


def _brother_repo_root():
    """The checkout this doctor.py file lives in, four directories up
    (products/brothermode/scripts/doctor.py -> repo root): the same
    distance scripts/brother_run.py's own REPO_ROOT sits from itself (one
    directory up), just deeper because this file sits one product further
    in. Deterministic from __file__, no cwd or git dependency, so a status
    row never depends on where the caller happened to cd from."""
    here = os.path.abspath(__file__)
    for _ in range(4):
        here = os.path.dirname(here)
    return here


def _git_ls_files_tracked(repo_root, path):
    """True if `git ls-files` inside repo_root reports `path` as tracked;
    False on any git failure, a missing git, or a genuine "not tracked"
    answer -- never raises, matching every other check in this file. Used
    only by _load_top_level_module's D2 pin below. Both repo_root and path
    are realpath'd before the relative path is computed: `path` arrives
    already realpath'd from the caller, and repo_root must be too, or a
    machine where the temp/home root itself is a symlink (macOS /var ->
    /private/var) computes a bogus relative path and reports a genuinely
    tracked file as untracked."""
    if shutil.which("git") is None:
        return False
    repo_root = os.path.realpath(repo_root)
    rel = os.path.relpath(path, repo_root)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "--error-unmatch", "--", rel],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _runtime_root():
    """(root, basis) for locating this file's sibling top-level scripts
    (codex_hooks_install.py, capability_probe.py, brother_paths.py).

    D13 (repair, adversarial review round 3, 2026-09-10): _brother_repo_root
    walks a fixed four directories up from __file__ and is right about
    WHERE that lands in a dev checkout, but says nothing about whether a
    checkout is actually there. On an installed plugin (a copy, never a
    checkout: no .git anywhere near it) that four-up root has no
    repository to ask, so basis "checkout" below never held there. This
    function is the seam that now tells the difference: basis is
    "checkout" when the four-up root itself sits inside a real git
    checkout (.git present there, file or directory, so a linked
    worktree's own gitdir pointer still counts); otherwise the root comes
    from the SAME seam the product already uses to find its own installed
    location -- brother_paths.plugin_root()'s own rung order
    (BROTHER_PLUGIN_ROOT, CLAUDE_PLUGIN_ROOT, PLUGIN_ROOT, read directly
    here rather than through _load_top_level_module, which needs a root
    to work from in the first place) -- with this file's own package root
    (brother_paths.package_root()'s contract: a script under a "tools",
    "runtime" or "scripts" directory answers that directory's own parent)
    as the last rung when none of the three variables is set."""
    checkout_root = _brother_repo_root()
    if os.path.exists(os.path.join(checkout_root, ".git")):
        return checkout_root, "checkout"
    for var in ("BROTHER_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"):
        named = os.environ.get(var, "").strip()
        if named:
            return os.path.abspath(os.path.expanduser(named)), "plugin_root"
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) in ("tools", "runtime", "scripts"):
        return os.path.dirname(here), "package_root"
    return here, "package_root"


def _exec_module(name, path):
    """(module, None) or (None, error-string) from exec'ing `path` as a
    module named `name`; the last step both _load_top_level_module
    branches below share, factored out so neither repeats it. Never
    raises."""
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:  # noqa: BLE001 - a check must never crash doctor
        return None, "%s could not be loaded (%s: %s)" % (
            _mask_home(path), type(exc).__name__, exc)


def _manifest_digest_ok(directory, filename, path):
    """(True, None) only when `directory` carries a checksum manifest
    (RUNTIME-MANIFEST.json, then HOOKS-MANIFEST.json, the two shapes
    scripts/bundle_runtime.py writes) that NAMES `filename` with a
    sha256 matching the bytes stored at `path`. (False, reason) for
    every other case, including the two this function used to treat
    as a silent pass-through: no manifest present at all, and a
    manifest present that simply does not list `filename`. Finding 5
    (security review, 2026-09-10): the non-checkout branch of
    _load_top_level_module is a plugin root realpath containment
    plus this function as its only pin, so a pass-through here meant
    a directory with no manifest, or a manifest that happened not to
    mention the file, gave _exec_module (arbitrary code as the user)
    the moment doctor.py --status ran there. An unreadable manifest,
    an unhashable file, and a real digest mismatch are also (False,
    reason): the tamper case this already caught. Never raises."""
    found_manifest = False
    for manifest_name in ("RUNTIME-MANIFEST.json", "HOOKS-MANIFEST.json"):
        manifest_path = os.path.join(directory, manifest_name)
        if not os.path.isfile(manifest_path):
            continue
        found_manifest = True
        try:
            with io.open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (IOError, OSError, ValueError) as exc:
            return False, ("%s could not be read: %s"
                           % (_mask_home(manifest_path), exc))
        entries = {e.get("path"): e.get("sha256")
                  for e in manifest.get("files", [])
                  if isinstance(e, dict)}
        if filename not in entries:
            continue
        expected = entries[filename]
        try:
            with io.open(path, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
        except (IOError, OSError) as exc:
            return False, ("%s could not be hashed: %s"
                           % (_mask_home(path), exc))
        if actual != expected:
            return False, ("%s digest for %s does not match: "
                    "expected %s, got %s"
                    % (_mask_home(manifest_path), filename,
                       expected, actual))
        return True, None
    if found_manifest:
        return False, ("%s carries a checksum manifest that does not "
                "list %s: refused without a matching digest, never "
                "exec-ed on an installed plugin with no git checkout "
                "to confirm tracking instead"
                % (_mask_home(directory), filename))
    return False, ("%s has no RUNTIME-MANIFEST.json or "
            "HOOKS-MANIFEST.json: refused without a signed digest, "
            "never exec-ed on an installed plugin with no git "
            "checkout to confirm tracking instead"
            % _mask_home(directory))


def _load_top_level_module(name, filename):
    """Load a sibling top-level script (codex_hooks_install.py,
    capability_probe.py, brother_paths.py) as a module named `name`, the
    same way check_stranded_install above loads tools/bm_project_facts.py:
    by file path, via importlib.util, never by adding a directory to
    sys.path. None of the three import each other, so a spec-loaded copy
    behaves exactly like a normal import. Returns (module, None) or
    (None, error-string); never raises, the same never-crash promise
    every check in this file keeps.

    D2 (repair, adversarial review 2026-09-09): exec_module runs the
    loaded file's top-level code, so this seam is pinned even though every
    caller today passes a hardcoded filename.

    D13 (repair, adversarial review round 3, 2026-09-10): D2's pin was
    git-tracked-or-refuse, which only ever held inside a real checkout;
    on an installed plugin (_runtime_root's basis "plugin_root" or
    "package_root") there is no checkout to ask, so every row that loads
    a sibling script through this seam degraded to NO-DATA on the exact
    install the doctor exists to examine. Inside a checkout, behavior is
    UNCHANGED: `filename` must resolve (after realpath) inside the
    checkout's own scripts/ directory and be reported tracked by `git
    ls-files`. Outside a checkout, `filename` is looked for under the
    runtime root's own "runtime" directory (an installed plugin's mirrored
    copy, per scripts/bundle_runtime.py) and then its "scripts" directory
    (a layout that ships sources directly); pinned by realpath containment
    inside whichever directory answers, and, when that directory ships its
    own RUNTIME-MANIFEST.json or HOOKS-MANIFEST.json, additionally by the
    file's own listed sha256 (_manifest_digest_ok) -- a tampered or stale
    file sitting next to a real manifest is still refused. NO-DATA only
    when none of these holds."""
    root, basis = _runtime_root()
    if basis == "checkout":
        scripts_dir = os.path.realpath(os.path.join(root, "scripts"))
        path = os.path.realpath(os.path.join(scripts_dir, filename))
        if path != scripts_dir and not path.startswith(scripts_dir + os.sep):
            return None, ("%s resolves outside the repository's own scripts "
                          "directory (%s)" % (_mask_home(path), _mask_home(scripts_dir)))
        if not os.path.isfile(path):
            return None, "%s is not there" % _mask_home(path)
        if not _git_ls_files_tracked(root, path):
            return None, "%s is not tracked by git" % _mask_home(path)
        return _exec_module(name, path)
    for subdir in ("runtime", "scripts"):
        candidate_dir = os.path.realpath(os.path.join(root, subdir))
        path = os.path.realpath(os.path.join(candidate_dir, filename))
        if path != candidate_dir and not path.startswith(candidate_dir + os.sep):
            continue
        if not os.path.isfile(path):
            continue
        matched, digest_err = _manifest_digest_ok(candidate_dir, filename,
                                                  path)
        if not matched:
            return None, digest_err
        return _exec_module(name, path)
    return None, ("%s: no %s found under this install's own runtime or "
                  "scripts directory (%s, no git checkout here to confirm "
                  "tracking instead)" % (_mask_home(root), filename, basis))


def _status_brother_runtime(_settings_path):
    """Row 1: scripts/bundle_runtime.py --check, exit code AND printed
    verdict must agree (a guard can express its true verdict in stdout
    rather than its exit code, so reading only one of the two is how a
    drifted bundle gets reported healthy)."""
    script = os.path.join(_brother_repo_root(), "scripts", "bundle_runtime.py")
    if not os.path.isfile(script):
        return ("NO-DATA", "no scripts/bundle_runtime.py under %s: not a "
                "dev checkout of this project" % _mask_home(_brother_repo_root()),
                None)
    try:
        r = subprocess.run(["python3", script, "--check"],
                           cwd=_brother_repo_root(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return ("NO-DATA", "could not run scripts/bundle_runtime.py --check: "
                "%s" % _mask_home_text(str(exc)), None)
    out = _mask_home_text((r.stdout or "").strip())
    says_ok = "matches scripts/" in out
    says_drift = "DRIFT:" in out
    if r.returncode == 0 and says_ok and not says_drift:
        return ("PASS", out.splitlines()[-1] if out else "matches", None)
    if r.returncode != 0 and says_drift:
        return ("FAIL", out, "python3 scripts/bundle_runtime.py")
    return ("NO-DATA", "exit code (%d) and the printed verdict disagree: %s"
            % (r.returncode, out[:500]), "python3 scripts/bundle_runtime.py --check")


def _canonical_worktree_root(cwd):
    """The first entry of `git worktree list --porcelain` run from cwd:
    git's own contract lists the canonical (main) worktree first, whatever
    linked worktree cwd itself sits in. Returns (path, None) or (None,
    error-string); never raises."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-C", cwd, "worktree", "list", "--porcelain"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, _mask_home_text(str(exc))
    if r.returncode != 0:
        return None, _mask_home_text((r.stderr or "").strip()) or "git worktree list failed"
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip(), None
    return None, "git worktree list printed no worktree entry"


def _status_git_isolation(_settings_path):
    """Row 2: PASS in a linked lane worktree (.git is a FILE there, a
    directory in the canonical checkout) when the canonical checkout is
    ALSO clean; FAIL when the canonical checkout is dirty, whether this
    row is running from that checkout directly or from a linked worktree
    beside it; NO-DATA outside git.

    D4 (repair, adversarial review 2026-09-09): a linked worktree's own
    PASS used to say nothing about the canonical (main) worktree it was
    created from, so a dirty canonical tree sat silently behind an
    isolated, clean lane. This row now always resolves the canonical
    worktree via `git worktree list --porcelain` (its first entry, by
    git's own contract) when it is running in a linked worktree, and names
    the canonical checkout's own state in the message, never leaving it
    unmentioned.

    D9 (repair, adversarial review round 3, 2026-09-10): D4 put the
    canonical checkout's dirty state into the MESSAGE but left the VALUE
    at PASS regardless, so this row printed PASS with "DIRTY" sitting
    inside its own text -- a verdict in the message and not in the value,
    the estate's own recorded failure shape. The driven case: the SAME
    repository state (main dirty) read PASS one directory over (run from
    the linked worktree) from where it read FAIL (run from the canonical
    checkout itself, the "else" branch below, unchanged by this repair).
    A dirty canonical checkout is now FAIL from any worktree; the
    linked-worktree fact stays in the message exactly as D4 put it
    there."""
    if shutil.which("git") is None:
        return ("NO-DATA", "git is not on PATH", None)
    cwd = os.getcwd()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return ("NO-DATA", "git could not be run: %s" % _mask_home_text(str(exc)), None)
    if r.returncode != 0:
        return ("NO-DATA", "not inside a git repository (ran from %s)"
                % _mask_home(cwd), None)
    top = r.stdout.strip()
    if os.path.isfile(os.path.join(top, ".git")):
        canon, canon_err = _canonical_worktree_root(top)
        note = ""
        canon_dirty = False
        if canon_err:
            note = ("; the canonical worktree could not be resolved: %s"
                    % canon_err)
        elif canon and os.path.realpath(canon) != os.path.realpath(top):
            canon_state = _git_tree_state(canon)
            if canon_state == "dirty":
                canon_dirty = True
                note = ("; the canonical checkout at %s is DIRTY"
                        % _mask_home(canon))
            elif canon_state == "clean":
                note = ("; the canonical checkout at %s is clean"
                        % _mask_home(canon))
            else:
                note = ("; the canonical checkout at %s could not be read"
                        % _mask_home(canon))
        message = ("running in a linked worktree at %s, isolated from "
                  "the canonical checkout%s" % (_mask_home(top), note))
        if canon_dirty:
            return ("FAIL", message, "git status")
        return ("PASS", message, None)
    state = _git_tree_state(top)
    if state is None:
        return ("NO-DATA", "git status could not be read for %s"
                % _mask_home(top), None)
    if state == "clean":
        return ("PASS", "the canonical checkout at %s is clean"
                % _mask_home(top), None)
    try:
        r2 = subprocess.run(["git", "-C", top, "status", "--porcelain"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=30, env=env)
        paths = [ln[3:] for ln in r2.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        paths = []
    return ("FAIL", "the canonical checkout at %s is dirty: %s"
            % (_mask_home(top), ", ".join(paths[:10]) or "uncommitted changes"),
            "git status")


def _status_settings_path(explicit):
    """The Claude Code settings.json this row checks: an explicit --settings
    wins; otherwise scripts/brother_paths.py's own config_dir() (honors
    CLAUDE_CONFIG_DIR, unlike this file's own default_settings_path, which
    only ever looked under HOME); falling back to that HOME-only default
    only when brother_paths itself cannot be loaded."""
    if explicit:
        return os.path.abspath(explicit)
    bp, _err = _load_top_level_module("brother_paths", "brother_paths.py")
    if bp is not None:
        return bp.config_path("settings.json")
    return default_settings_path()


def _first_problem_line(message):
    """The first "  - <problem>" line inside a check_fence FAIL message
    (its own format: notes, then "PROBLEMS (N):", then one such line per
    problem, then a footer), with the "  - " prefix stripped; the full
    first line of `message` when none is found (defensive: some other
    message shape).

    Hostile case 5 (steering, 2026-09-09/10): check_fence's FAIL message
    always starts with the NOTES collected before the failure was found
    (a loader-plugin note, a "fence command: ..." note, or nothing at
    all), never with the problem itself. Reading only
    message.splitlines()[0], as this row's caller did before this fix,
    showed a FAIL row's own explanatory note (once even just the bare
    "PROBLEMS (N):" header, when there were no notes) and never the
    actual problem text, so a real FAIL (a partial install, a dead
    fence path, a missing file) printed no evidence of what was wrong."""
    for line in message.splitlines():
        if line.startswith("  - "):
            return line[len("  - "):]
    return message.splitlines()[0] if message else ""


def _status_claude_hooks(settings_path):
    """Row 3: reuses check_fence (the existing, already-audited fence
    simulation, which never touches anything outside its own mkdtemp) as
    is. N/A when the whole Claude config directory is absent (nothing here
    applies to this machine at all), never inferred from settings.json
    alone: a config directory that exists but has no settings.json yet is
    a real, examined FAIL from check_fence, not an N/A."""
    # config_dir is derived from settings_path itself (not re-resolved via
    # brother_paths here), so this row always checks the SAME directory the
    # settings_path it is about to hand check_fence actually lives in;
    # brother_paths is only the picker of settings_path in the first place
    # (_status_settings_path above), and re-deriving it independently here
    # would let a caller-supplied settings_path (an explicit --settings, or
    # a test fixture) disagree with an ambient CLAUDE_CONFIG_DIR/HOME.
    config_dir = os.path.dirname(settings_path)
    if not os.path.isdir(config_dir):
        return ("N/A", "no Claude config directory at %s: nothing to check "
                "here." % _mask_home(config_dir), None)
    result = check_fence(settings_path)
    if result.status == STATUS_FAIL:
        # Hostile case 5 repair: the actual problem, not the leading note.
        first_line = _first_problem_line(result.message)
        return ("FAIL", first_line, "python3 scripts/install.py --upgrade")
    first_line = result.message.splitlines()[0] if result.message else result.status
    if result.status == STATUS_PASS:
        return ("PASS", first_line, None)
    return ("NO-DATA", first_line, None)


def _count_trust_entries_by_line(block):
    """The line scan used when tomllib is unavailable (Python 3.9, 3.10)
    or does not parse the block cleanly. Pairs every [hooks.state."..."]
    table header with the next "enabled = true" line, mirroring
    write_trust's own format (trust_block in codex_hooks_install.py); the
    smaller of the two tallies is used, exactly as before D3, so a block
    hand-edited since Brother wrote it never over-counts.

    D3 (repair, adversarial review 2026-09-09): a line whose first
    non-space character is "#" is a TOML comment and is skipped before
    either tally, so a commented-out block left behind by a hand edit can
    no longer inflate the count."""
    tables = 0
    enabled = 0
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] == "#":
            continue
        if stripped.startswith('[hooks.state."'):
            tables += 1
        elif stripped == "enabled = true":
            enabled += 1
    return min(tables, enabled)


def _count_trust_entries_by_toml(block):
    """(count) or None. D3: when tomllib is present (Python 3.11+), parse
    the trust block as a standalone TOML document -- TRUST_BEGIN and
    TRUST_END are themselves "#" comment lines, so the block between them
    is valid TOML on its own, and tomllib's own comment handling replaces
    the "#" guess in _count_trust_entries_by_line entirely for this path.
    Returns None on any parse failure, so the caller uses the line scan
    above instead of trusting a partial parse."""
    try:
        doc = tomllib.loads(block)
    except tomllib.TOMLDecodeError:  # sbe: allow-silent pure reader, never rewrites; None tells the caller to fall back to _count_trust_entries_by_line, which computes the same count without tomllib, so no finding is dropped
        return None
    state = doc.get("hooks", {}).get("state", {})
    if not isinstance(state, dict):
        return 0
    return sum(1 for entry in state.values()
              if isinstance(entry, dict) and entry.get("enabled") is True)


def _codex_trust_entry_count(chi, config_path):
    """(count, error). Counts Brother's own trust entries in config.toml,
    purely as text, never by asking Codex. Uses tomllib
    (_count_trust_entries_by_toml) when it is present and parses cleanly;
    otherwise uses _count_trust_entries_by_line. Both ignore a
    commented-out entry (D3, adversarial review 2026-09-09)."""
    if not os.path.isfile(config_path):
        return 0, None
    try:
        with io.open(config_path, encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError) as exc:
        return 0, str(exc)
    start = text.find(chi.TRUST_BEGIN)
    if start < 0:
        return 0, None
    end = text.find(chi.TRUST_END, start)
    block = text[start:end + len(chi.TRUST_END)] if end >= 0 else text[start:]
    if tomllib is not None:
        count = _count_trust_entries_by_toml(block)
        if count is not None:
            return count, None
    return _count_trust_entries_by_line(block), None


def _status_codex_hooks(_settings_path):
    """Row 4: see the WHY CODEX TRUST IS NEVER CONFIRMED BY ASKING CODEX
    note above this section. hooks.json content match (a pure read, via
    codex_hooks_install.check) can reach FAIL/NO-DATA honestly; the trust
    half is answered from config.toml text alone, which can prove NEEDS
    TRUST for certain (no trust block at all) but can only ever prove NO-
    DATA, never PASS, for "a trust block is recorded": recording once does
    not guarantee the hook file has not changed since, and confirming that
    would mean asking Codex, which this row will not do."""
    chi, err = _load_top_level_module("codex_hooks_install", "codex_hooks_install.py")
    if chi is None:
        return ("NO-DATA", "could not load scripts/codex_hooks_install.py: %s"
                % err, None)
    home = os.path.abspath(os.path.expanduser(
        os.environ.get("CODEX_HOME") or "~/.codex"))
    default_home = os.path.realpath(os.path.expanduser("~/.codex"))
    flag = " --allow-default-home" if os.path.realpath(home) == default_home else ""
    try:
        products = [os.path.join(chi.repo_root(), rel) for rel in chi.DEFAULT_PRODUCTS]
        built = chi.build(products)
    except Exception as exc:  # noqa: BLE001 - a check must never crash doctor
        return ("NO-DATA", "could not compute the expected hooks document: "
                "%s" % exc, None)
    if built["problems"]:
        return ("NO-DATA", "the shipped hooks could not be built: %s"
                % "; ".join(built["problems"]), None)
    install_repair = "python3 scripts/codex_hooks_install.py" + flag
    trust_repair = "python3 scripts/codex_hooks_install.py --trust" + flag
    verdict, detail = chi.check(home, built["document"])
    if verdict != "PASS":
        return ("NO-DATA", _mask_home_text(detail), install_repair)
    expected = sum(len(b["hooks"]) for blocks in built["document"]["hooks"].values()
                  for b in blocks)
    if expected == 0:
        return ("NO-DATA", "the shipped hooks document carries no hook "
                "command to check trust for", None)
    config_path = os.path.join(home, "config.toml")
    trust_count, trust_err = _codex_trust_entry_count(chi, config_path)
    if trust_err:
        return ("NO-DATA", "hooks.json matches, but %s could not be read: %s"
                % (_mask_home(config_path), trust_err), trust_repair)
    if trust_count >= expected:
        return ("NO-DATA",
                "hooks.json matches and %s records %d trusted hook(s) "
                "matching the %d wired here; this status path never asks "
                "Codex itself to reconfirm the hash is current, because "
                "that call writes to your Codex home even though it makes "
                "no write request of its own (measured 2026-09-09: one "
                "hooks/list read-back rewrote several sqlite databases and "
                "created a new lock file under a throwaway CODEX_HOME). "
                "Run the repair command yourself for Codex's own "
                "confirmation." % (_mask_home(config_path), trust_count, expected),
                trust_repair)
    return ("NEEDS TRUST",
            "hooks.json matches but %s records only %d of %d hook(s) as "
            "trusted." % (_mask_home(config_path), trust_count, expected),
            trust_repair)


def _status_vault(_settings_path):
    """Row 5: capability_probe's own vault-recall capability (every
    alternative it names tried before MISSING, never one path checked and
    stopped)."""
    cp, err = _load_top_level_module("capability_probe", "capability_probe.py")
    if cp is None:
        return ("NO-DATA", "could not load scripts/capability_probe.py: %s"
                % err, None)
    vault_cap = next((c for c in cp.CAPABILITIES if c.get("name") == "vault-recall"),
                     None)
    if vault_cap is None:
        return ("NO-DATA", "capability_probe.py no longer names a "
                "vault-recall capability", None)
    state, detail, tried = cp.probe(vault_cap)
    detail = _mask_home_text(detail)
    if state == cp.PRESENT:
        return ("PASS", "vault-recall: %s" % detail, None)
    if state == cp.MISSING:
        tried_str = "; ".join(
            "%s (%s)" % (_mask_home_text(p), _mask_home_text(d))
            for p, _s, d in tried)
        return ("NOT CONFIGURED", "vault-recall: %s" % tried_str,
                "python3 scripts/setup.py --reconfigure")
    return ("NO-DATA", "vault-recall could not be probed: %s" % detail, None)


def _journal_last_line_record(path):
    """(record, None) from journal.jsonl's last non-blank line, parsed as a
    JSON object, or (None, reason) when the file has zero lines or that
    last line is not a valid JSON object. Never raises.

    D6 (repair, adversarial review 2026-09-09): only the LAST line has to
    parse. A torn line earlier in the file (an append caught mid-write) is
    journal.py's own documented, non-fatal case and is not this function's
    concern; a zero-byte or bad-last-line file used to read as readable
    here, which is what let an empty run directory's journal show PASS."""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (IOError, OSError) as exc:
        return None, str(exc)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, "%s has zero lines" % os.path.basename(path)
    try:
        record = json.loads(lines[-1])
    except ValueError as exc:
        return None, "%s: last line is not valid JSON (%s)" % (
            os.path.basename(path), exc)
    if not isinstance(record, dict):
        return None, "%s: last line is not a JSON object" % os.path.basename(path)
    return record, None


def _run_journal_timestamp(path):
    """The UTC-aware datetime parsed from journal.jsonl's last valid line's
    "at" field (journal.append's own field, ISO 8601 with offset), or None
    when the file is missing, its last line does not parse, or "at" is
    missing or unparseable. Never raises."""
    if not os.path.isfile(path):
        return None
    record, _reason = _journal_last_line_record(path)
    if record is None:
        return None
    at = record.get("at")
    if not isinstance(at, str):
        return None
    try:
        return datetime.datetime.fromisoformat(at)
    except ValueError:  # sbe: allow-silent pure reader, never rewrites; None tells _newest_run_dir this run has no usable timestamp, and it falls back to directory mtime and names that fallback in its own note, so nothing is dropped
        return None


def _newest_run_dir(runs_dir, names):
    """Which of these run directory names (immediate children of runs_dir)
    is newest: (name, note). Every name in `names` is a candidate; its
    comparison key is its own journal timestamp when
    _run_journal_timestamp finds one, else the run directory's own
    modification time -- both converted to an aware UTC datetime so the
    two bases compare on one axis. The candidate with the largest key
    wins. note names which basis decided the winner, and is None only
    when a real journal timestamp decided it with nothing to explain;
    names must be non-empty.

    D7 (repair, adversarial review 2026-09-09): mtime alone picked the
    wrong run when an older directory's journal.jsonl was appended to in
    place (an append never moves an existing file's own mtime) while a
    newer directory sat untouched since creation.

    D10 (repair, adversarial review round 3, 2026-09-10): D7's own fix
    still only ever considered directories whose journal carried a
    parseable timestamp; a run directory with NO journal at all was
    dropped before comparison, invisible rather than ranked, so the row
    reported PASS about a run measured four days older than the newest
    directory actually on disk. Every directory is now a candidate: a
    journal-less run compares by its own directory mtime right alongside
    the journal-timestamped ones, so a newer journal-less run wins (and
    is then examined on its own merits by the caller) instead of being
    silently passed over for a stale journaled one.

    D12 (repair, adversarial review round 3, 2026-09-10): a journal
    timestamp with no UTC offset ("naive") compared directly against one
    that carries an offset ("aware") raises TypeError, which used to
    crash this whole row. A naive timestamp is now treated as UTC (and
    the note says so, when it decided the winner) before anything is
    compared."""
    candidates = []  # (comparable aware-UTC datetime, basis, name, was_naive)
    for name in names:
        raw_ts = _run_journal_timestamp(os.path.join(runs_dir, name, "journal.jsonl"))
        if raw_ts is not None:
            was_naive = raw_ts.tzinfo is None
            ts = (raw_ts.replace(tzinfo=datetime.timezone.utc) if was_naive
                 else raw_ts.astimezone(datetime.timezone.utc))
            candidates.append((ts, "journal", name, was_naive))
        else:
            mtime = os.path.getmtime(os.path.join(runs_dir, name))
            mdt = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
            candidates.append((mdt, "mtime", name, False))
    candidates.sort(key=lambda c: c[0])
    _winner_dt, winner_basis, winner_name, winner_naive = candidates[-1]
    if winner_basis == "mtime":
        journaled = sorted(n for _dt, basis, n, _naive in candidates
                           if basis == "journal")
        if journaled:
            note = ("%s has no usable journal.jsonl timestamp; picked by "
                    "directory modification time, newer than the "
                    "journaled run(s) here: %s"
                    % (winner_name, ", ".join(journaled)))
        else:
            note = ("no candidate run's journal.jsonl carried a usable "
                    "timestamp; picked by directory modification "
                    "time instead")
    elif winner_naive:
        note = ("%s's own journal timestamp carries no UTC offset; "
                "treated as UTC" % winner_name)
    else:
        note = None
    return winner_name, note


def _readable_recovery_file(path):
    """(True, None) or (False, reason). journal.jsonl defers to
    _journal_last_line_record (D6: zero lines or an invalid last line is
    unreadable, never PASS); capsule.json and claims.json are each one
    JSON object, checked whole."""
    if os.path.basename(path) == "journal.jsonl":
        record, reason = _journal_last_line_record(path)
        return (True, None) if record is not None else (False, reason)
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (IOError, OSError) as exc:
        return False, str(exc)
    try:
        json.loads(text)
    except ValueError as exc:
        return False, str(exc)
    return True, None


def _status_recovery_store(_settings_path):
    """Row 6: the newest run directory's journal.jsonl, capsule.json and
    claims.json (whichever of the three it actually carries; a run
    predating one of them is not a failure, continuity.py's own docstring
    says the same about a missing journal). No runs root at all is NO-DATA,
    not FAIL: a fresh install has none.

    D6/D7 (repair, adversarial review 2026-09-09): a zero-byte or
    bad-last-line journal.jsonl is this row's own NO-DATA, named by file,
    never PASS; capsule.json or claims.json corruption still FAILs the row
    outright, since those are single JSON objects with no torn-append case
    to excuse a bad read. The newest run is picked by _newest_run_dir
    (journal timestamp first, directory mtime only as a named fallback)."""
    runs_dir = os.path.join(_brother_repo_root(), "docs", "plan", "runs")
    if not os.path.isdir(runs_dir):
        return ("NO-DATA", "no runs root yet at %s: a fresh install has "
                "none, that is not a failure" % _mask_home(runs_dir), None)
    try:
        names = [n for n in os.listdir(runs_dir)
                if os.path.isdir(os.path.join(runs_dir, n))]
    except OSError as exc:
        return ("NO-DATA", "%s could not be listed: %s"
                % (_mask_home(runs_dir), exc), None)
    if not names:
        return ("NO-DATA", "%s exists but holds no runs yet"
                % _mask_home(runs_dir), None)
    latest_name, pick_note = _newest_run_dir(runs_dir, names)
    latest_dir = os.path.join(runs_dir, latest_name)
    suffix = (" (%s)" % pick_note) if pick_note else ""
    checked = []
    for name in ("journal.jsonl", "capsule.json", "claims.json"):
        path = os.path.join(latest_dir, name)
        if os.path.isfile(path):
            ok, reason = _readable_recovery_file(path)
            checked.append((name, ok, reason))
    if not checked:
        # D10: pick_note is carried here too, never only into the three
        # branches below it -- a journal-less run picked over an older
        # journaled one is exactly the shape most likely to land here
        # (nothing of the three files to check at all), and that is the
        # one case where staying silent about the basis would put the
        # newer, empty run back in the position D10 fixed: visible only
        # as an unexplained NO-DATA, its relationship to the journaled
        # run it outranked left unsaid.
        return ("NO-DATA", "the most recent run at %s holds none of "
                "journal.jsonl, capsule.json or claims.json to check%s"
                % (_mask_home(latest_dir), suffix), None)
    broken = [(n, r) for n, ok, r in checked if not ok]
    journal_broken = [(n, r) for n, r in broken if n == "journal.jsonl"]
    other_broken = [(n, r) for n, r in broken if n != "journal.jsonl"]
    if other_broken:
        return ("FAIL", "%s: %s%s" % (_mask_home(latest_dir),
                "; ".join("%s unreadable (%s)" % (n, r) for n, r in other_broken),
                suffix), None)
    if journal_broken:
        n, r = journal_broken[0]
        return ("NO-DATA", "%s: %s unreadable (%s)%s" % (
                _mask_home(latest_dir), n, r, suffix), None)
    return ("PASS", "%s: %s readable%s" % (_mask_home(latest_dir),
            ", ".join(n for n, _ok, _r in checked), suffix), None)


_STATUS_ROWS = (
    ("Brother runtime", _status_brother_runtime),
    ("Git isolation", _status_git_isolation),
    ("Claude safety hooks", _status_claude_hooks),
    ("Codex safety hooks", _status_codex_hooks),
    ("Vault", _status_vault),
    ("Recovery store", _status_recovery_store),
)
# D8 (repair, adversarial review 2026-09-09): one tuple of (label, callable)
# pairs, replacing the earlier STATUS_ROW_LABELS tuple plus the
# _STATUS_ROW_FUNCS dict it was joined to by a string key -- two parallel
# structures kept in step by hand, with five lambdas that only existed to
# discard the settings_path argument the row function did not want. Every
# row function now takes settings_path (four of them as the deliberately
# unused _settings_path), so this list can hold the plain callables.


def run_status_rows(settings_path):
    """The DOC-0 six-row table: [(label, status, message, repair), ...] in
    _STATUS_ROWS order. A row function that raises becomes its own row
    rather than aborting the other five (the same never-crash promise
    _run_check keeps for the fifteen-check surface above) -- but it is
    marked STATUS_CRASHED, never plain NO-DATA (C-3, repair, backend
    review 2026-09-10): a check that could not run is not a clean
    NO-DATA reading, and _status_exit_code must be able to tell the two
    apart so a crashed safety-fence or git-isolation row can never exit
    0 alongside five real PASSes."""
    rows = []
    for label, func in _STATUS_ROWS:
        try:
            status, message, repair = func(settings_path)
        except Exception as exc:  # noqa: BLE001 - one row must never crash the table
            status, message, repair = (
                STATUS_CRASHED, "this row crashed (%s: %s)" % (type(exc).__name__, exc),
                None)
        rows.append((label, status, message, repair))
    return rows


def print_status_table(rows):
    width = max(len(label) for label, _s, _m, _r in rows)
    for label, status, message, repair in rows:
        _out("%s  %s" % (label.ljust(width), status))
        for line in (message or "").splitlines():
            _out("  %s" % line)
        if repair:
            _out("  repair: %s" % repair)


def _status_exit_code(rows):
    """The DOC-0 status table's own exit code, computed purely from each
    row's status (rows are (label, status, message, repair) 4-tuples):
    EXIT_PROBLEMS when any row is FAIL or STATUS_CRASHED; EXIT_OK when no
    row FAILs or crashes and at least one row is PASS; EXIT_NO_PASS when
    no row FAILs or crashes and none reaches PASS either.

    D5 (repair, adversarial review 2026-09-09): a table where every row
    landed on NO-DATA, N/A, NEEDS TRUST or NOT CONFIGURED used to exit 0,
    the "population of all NO-DATA composed into a PASS" failure the
    reviewer drove by forcing all six rows. Pure and side-effect free, so a
    test can hand it fabricated rows directly rather than faking six real
    environments to reach all three exit codes.

    C-3 (repair, backend review 2026-09-10): a row whose function raised
    used to land on plain NO-DATA, so a crashed safety-fence or git
    isolation row exited 0 as long as the other five rows read PASS or
    better -- a check that never ran read to a script as a healthy
    install. STATUS_CRASHED now forces EXIT_PROBLEMS the same as a FAIL,
    before the PASS count is even looked at."""
    statuses = [s for _l, s, _m, _r in rows]
    if any(s in (STATUS_FAIL, STATUS_CRASHED) for s in statuses):
        return EXIT_PROBLEMS
    if any(s == STATUS_PASS for s in statuses):
        return EXIT_OK
    return EXIT_NO_PASS


def main_status(explicit_settings):
    settings_path = _status_settings_path(explicit_settings)
    rows = run_status_rows(settings_path)
    print_status_table(rows)
    _out("")
    # C-3: a crashed row is also a problem row for this summary line, not
    # only a literal FAIL -- otherwise "0 of 6 row(s) FAIL" would print
    # beside an EXIT_PROBLEMS exit code caused by a row that crashed.
    fail_count = sum(1 for _l, s, _m, _r in rows
                     if s in (STATUS_FAIL, STATUS_CRASHED))
    code = _status_exit_code(rows)
    if code == EXIT_PROBLEMS:
        _out("%d of %d row(s) FAIL or crashed." % (fail_count, len(rows)))
        return code
    if code == EXIT_NO_PASS:
        _out("no row FAILs, but no row reached PASS either: this table "
             "measured nothing that passed. Run the repair command named "
             "above for each row, then re-run --status.")
        return code
    _out("no row FAILs. A row that is not PASS still names its own repair "
         "command above; run it by hand.")
    return code


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
    p.add_argument("--status", action="store_true",
                   help="print the six-row DOC-0 status table (Brother "
                        "runtime, Git isolation, Claude/Codex safety hooks, "
                        "Vault, Recovery store) instead of the fifteen-check "
                        "report, and write nothing; refused (exit 2) when "
                        "combined with --json or --strict (D1, adversarial "
                        "review 2026-09-09)")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 9):
        _err("doctor.py: needs Python 3.9 or newer; this interpreter is %s"
             % platform.python_version())
        return EXIT_UNSUPPORTED
    if args.status:
        if args.json or args.strict:
            _err("doctor.py: --status cannot be combined with --json or "
                 "--strict (D1, adversarial review 2026-09-09); run "
                 "--status alone, or drop --status to use --json/--strict "
                 "with the fifteen-check report.")
            return EXIT_USAGE
        return main_status(args.settings)
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
