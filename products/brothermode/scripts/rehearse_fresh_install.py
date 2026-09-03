#!/usr/bin/env python3
"""rehearse_fresh_install.py: the fresh-HOME install rehearsal, run for real.

WHY THIS EXISTS (Loop 3 design D-6, docs/superpowers/specs/2026-08-01-
loop3-consent-install-design.md, work package WP-F)
  The program gate is "fresh-machine install without developer help". This
  file is the one scripted rehearsal of that gate on this machine: it drives
  the CLONE-path story end to end, exactly as docs/QUICKSTART.md and
  docs/SETUP.md tell a stranger to do it, inside temporary directories with
  HOME and BROTHERME_CONFIG overridden so the real HOME and the real
  ~/.claude are never touched.

WHAT "CLONE" MEANS HERE, DISCLOSED
  Step 1 copies this repository's working tree into a fake
  ~/.claude/skills/brothermode instead of running `git clone`. That is a
  disclosed stand-in, not the real command: the docs' own clone step pulls a
  tagged release over the network from GitHub, and running that here would
  test GitHub and the network, not this machine's install code. The copy
  excludes .git, .brothermode, and node_modules, the same exclusion list
  scripts/checksums.sh and scripts/verify-install.sh already use for the same
  reason. Every other step below runs the actual project scripts against the
  fake HOME: scripts/install.py, scripts/setup.py, scripts/doctor.py,
  tools/bm_project.py, and scripts/uninstall.py, none of them simulated.

THE SEVEN STEPS
  1. copy (stands in for git clone) into fakehome/.claude/skills/brothermode
  2. python3 tools/test_all.py from the copy (skippable with --skip-gate for
     time; the default runs it)
  3. scripts/install.py, dry-run then real, against a fake settings.json
  4. scripts/setup.py flag mode consent, plus the vault-template copy
     docs/SETUP.md Step 3 documents (needed for doctor's vault check to have
     a real directory to check)
  5. scripts/doctor.py --json: fence, consent, vault, duplicate_install and
     settings_json checks are asserted; checksums is recorded as-is, not
     asserted, because this is a live development tree and CHECKSUMS.sha256
     was cut against a clean release commit, not this moment's working copy
  6. one project through tools/bm_project.py in a fresh project directory
     under the fake HOME: start, task add, several task state moves, a
     review call, status, next, deliver --partial
  7. scripts/uninstall.py --remove-consent, then three assertions: the hook
     entries are gone from settings.json, the consent config file is gone,
     and a byte-for-byte vault file manifest (taken right after the vault is
     created in step 4) is unchanged

WHAT THIS DOES NOT PROVE (named here, restated in the evidence file)
  One physical machine, the author's own environment, no second machine, no
  naive user (that is Loop 8's outside-install list), and the plugin path is
  not exercised at all here (this rehearsal is the clone path only).

Python 3.9, standard library only. No em or en dashes anywhere in this file,
its comments, or its output.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

EXIT_OK = 0
EXIT_FAILED = 1

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COPY_EXCLUDE_NAMES = {".git", ".brothermode", "node_modules"}

STEP_TITLES = (
    "copy (clone stand-in) into fakehome/.claude/skills/brothermode",
    "python3 tools/test_all.py from the copy",
    "scripts/install.py against the fake settings.json",
    "scripts/setup.py flag mode consent, plus the vault-template copy",
    "scripts/doctor.py --json checks",
    "one project through tools/bm_project.py",
    "scripts/uninstall.py --remove-consent",
)

# I3 (external review, Loop 3/5): the step 2 gate skip used to be recorded
# with .passed(), so a --skip-gate run's numbered line read "PASS" for a
# step that proved nothing at all. A SKIP is now its own status, distinct
# from PASS, so the seven-step ledger can never claim a proof it did not
# run. Step 0, the permanent demonstration of I1's fix, is numbered
# separately from the seven (it predates them chronologically: it must run
# right after the clone and strictly before setup.py ever creates a
# consent config), so TOTAL_STEPS stays 7 for the existing numbering.
TOTAL_STEPS = len(STEP_TITLES)


def _mask_home(path):
    """Replace the operator's real home-directory prefix with a generic
    /Users/... placeholder before this path is printed into the evidence
    file this output lands in (a committed file). The same, simple
    technique scripts/doctor.py's own _mask_home now uses for the same
    reason; this file cannot import that one (doctor.py is a script, not a
    library), so it is kept here too, deliberately duplicated rather than
    imported, the way every script in this project stays self-contained."""
    if not path:
        return path
    home = os.path.expanduser("~")
    if home and path.startswith(home):
        return "/Users/..." + path[len(home):]
    return path


def _out(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


class Result(object):
    """One numbered step's outcome plus the detail lines printed under it.
    status is one of "PASS", "FAIL", "SKIP" (never boolean-only: I3 needs a
    disclosed skip to be visibly distinct from a real pass)."""

    def __init__(self, n, title, total=TOTAL_STEPS):
        self.n = n
        self.title = title
        self.total = total
        self.status = "FAIL"
        self.lines = []

    def note(self, text):
        for line in text.splitlines():
            self.lines.append("    " + line)

    def passed(self, summary):
        self.status = "PASS"
        self.lines.insert(0, "    " + summary)

    def failed(self, summary):
        self.status = "FAIL"
        self.lines.insert(0, "    " + summary)

    def skipped(self, summary):
        self.status = "SKIP"
        self.lines.insert(0, "    " + summary)

    @property
    def ok(self):
        """Backward-compatible name for "did not fail": a disclosed SKIP
        must never flip the overall rehearsal to NOT ALL GREEN, or
        --skip-gate (which exists specifically to skip step 2 for time)
        would defeat its own purpose. main() tracks skip_count separately
        for the closing line, so a SKIP is never silently read as a PASS
        there either."""
        return self.status in ("PASS", "SKIP")

    def emit(self):
        _out("[%d/%d] %s: %s" % (self.n, self.total, self.title, self.status))
        for line in self.lines:
            _out(line)


class Proc(object):
    """A uniform stand-in for subprocess.CompletedProcess, including the two
    failure shapes (timeout, could not start) that never raise past here, so
    every step below can read .returncode/.stdout/.stderr without a
    try/except of its own."""

    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""


def run(cmd, cwd=None, env=None, timeout=None, input_text=None):
    try:
        r = subprocess.run(
            cmd, cwd=cwd, env=env, timeout=timeout, input=input_text,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        return Proc(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired as exc:
        return Proc(None, exc.stdout or "",
                    (exc.stderr or "") + "\nTIMEOUT after %s seconds" % timeout)
    except OSError as exc:
        return Proc(None, "", "could not start %s: %s" % (cmd, exc))


def shortcmd(cmd):
    return " ".join(cmd)


# ---------------------------------------------------------------------------
# paths and environment
# ---------------------------------------------------------------------------

def build_paths(tmp_root):
    fake_home = os.path.join(tmp_root, "fakehome")
    return {
        "tmp_root": tmp_root,
        "fake_home": fake_home,
        "target": os.path.join(fake_home, ".claude", "skills", "brothermode"),
        "settings": os.path.join(fake_home, ".claude", "settings.json"),
        "config": os.path.join(fake_home, ".brotherme", "config.json"),
        "vault": os.path.join(fake_home, "BrotherModeVault"),
        "project": os.path.join(fake_home, "projects", "fresh-home-demo"),
    }


def build_env(paths):
    """A copy of the real environment with HOME and BROTHERME_CONFIG pointed
    at the fake HOME, and every other BrotherMode-recognized environment
    variable this shell might already carry (BROTHERMODE_VAULT,
    BROTHERMODE_ROOT, BROTHERMODE_REGISTRIES) stripped out, so nothing about
    the real machine's own dogfood install leaks into the rehearsal."""
    # Every GIT_ name goes too: this rehearsal runs git init, add and commit in
    # a throwaway tree, and git honours GIT_DIR and GIT_WORK_TREE over cwd, so
    # an inherited one commits into the operator's real repository instead.
    # Same class as tools/bm_autosave.py; reproduced 2026-08-06.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for key in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT",
                "BROTHERMODE_REGISTRIES", "BROTHERME_CONFIG"):
        env.pop(key, None)
    env["HOME"] = paths["fake_home"]
    env["BROTHERME_CONFIG"] = paths["config"]
    return env


# ---------------------------------------------------------------------------
# step 1: copy stands in for git clone
# ---------------------------------------------------------------------------

def copytree_excluding(src, dst, exclude_names):
    os.makedirs(dst, exist_ok=True)
    count = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in exclude_names)
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for f in sorted(files):
            if f in exclude_names:
                continue
            src_f = os.path.join(root, f)
            if os.path.islink(src_f):
                continue
            shutil.copy2(src_f, os.path.join(target_dir, f))
            count += 1
    return count


def step1_clone(paths, result):
    try:
        n = copytree_excluding(REPO_ROOT, paths["target"], COPY_EXCLUDE_NAMES)
    except OSError as exc:
        result.failed("copy failed: %s" % exc)
        return False
    marker = os.path.join(paths["target"], "SKILL.md")
    version = os.path.join(paths["target"], "VERSION")
    if not os.path.isfile(marker) or not os.path.isfile(version):
        result.failed("copy step wrote %d file(s) but SKILL.md or VERSION is "
                      "missing at %s" % (n, paths["target"]))
        return False
    # The source path starts under the operator's real HOME; the evidence
    # file this output lands in is committed, so the home prefix is masked
    # here at the source, the same policy mask_absolute_paths applies to
    # every exported store column.
    result.passed(
        "%d files copied from %s to %s" % (n, _mask_home(REPO_ROOT), paths["target"]))
    result.note("disclosed deviation: this is a plain recursive copy, not "
               "git clone. It excludes %s, the same list "
               "scripts/checksums.sh and scripts/verify-install.sh use."
               % ", ".join(sorted(COPY_EXCLUDE_NAMES)))
    result.note("present at target: SKILL.md, VERSION")
    return True


# ---------------------------------------------------------------------------
# step 0: the I1/I2 pre-consent no-write probe (external review, Loop 3/5,
# extended 2026-08-02 after Loop 9 Criticals 1 and 2)
#
# WHY THIS EXISTS
#   I1 (the most serious finding of that review) reproduced tools/
#   bm_autosave.py's cmd_precompact writing a namespaced git ref, a
#   snapshot commit, and a JSON event file with NO ~/.brotherme/config.json
#   present at all. tools/test_bm_autosave.py now carries the unit-level
#   proof of the fix; this step is the PERMANENT, EXECUTED demonstration of
#   the same fact against the real binaries, in the exact fresh-machine
#   shape this whole rehearsal exists to reproduce: a throwaway git repo,
#   no consent config anywhere BROTHERME_CONFIG could resolve to (this step
#   runs right after the clone, step 1, and strictly before step 4, the
#   only step that ever creates one).
#
#   THIS STEP USED TO HARDCODE A LIST OF "the three real write-capable
#   entry points" and drive one program per hook EVENT. That list itself
#   was the bug: hooks/hooks.json's PreCompact line runs TWO programs off
#   one stdin payload (tools/bm_autosave.py precompact and tools/
#   bm_telemetry.py precompact-brief), and the Stop hook (tools/
#   bm_telemetry.py stop-warn) was not driven at all. Both wrote the
#   founder's own words into a fresh ~/BrotherModeVault before consent
#   existed (Loop 9 Criticals 1 and 2), and this probe's own hand-
#   maintained list never noticed, because a list beside hooks.json can
#   drift from hooks.json silently while a probe that reads hooks.json
#   cannot. See the 2026-08-02 addendum in
#   docs/evidence/2026-08-01-fresh-home-rehearsal.md for the full account.
#
#   This step now PARSES hooks/hooks.json (parse_hook_programs) and drives
#   EVERY hook entry's own real command, however many programs one entry's
#   command chains together, run via `sh -c` exactly once per entry (the
#   same shape the real hook runner uses; see _command_for_entry and
#   _payload_for_entry). A hook entry this file has no payload for is a
#   hard FAIL, not a silent skip, because silently skipping an unrecognized
#   entry is exactly how the old list went stale. The assertion is no
#   longer "the three programs I already knew about wrote nothing": it is
#   that the fresh HOME holds ZERO files BrotherMode wrote, full stop
#   (Python's own bytecode cache under Library/Caches excepted;
#   see IGNORED_HOME_PREFIXES).
#
#   Numbered "step 0", not inserted into the seven-step STEP_TITLES
#   tuple: it predates step 2 (the test-suite gate) and every step after
#   it, chronologically, and giving it its own number keeps every
#   existing step's number exactly what evidence readers already know it
#   to be, rather than renumbering seven historically-referenced steps for
#   one addition.
# ---------------------------------------------------------------------------

HOOKS_JSON_PATH = os.path.join(REPO_ROOT, "hooks", "hooks.json")

# A leak of this exact sentence anywhere under the fresh HOME is, by
# construction, a BrotherMode write of the founder's own words before
# consent: it is planted as the tail of the PreCompact transcript
# (precompact-brief's own subject, Loop 9 Critical 1) and grepped for
# afterward, so a leak is visible by grep rather than only by a manifest
# diff.
CANARY_SENTENCE = ("CANARY-9f3c2a-DO-NOT-PERSIST: if this sentence exists "
                   "anywhere under the fresh HOME, a pre-consent write "
                   "leaked the founder's own words, which is the exact "
                   "defect this probe exists to catch.")

# Python's own bytecode cache, not a BrotherMode write. Excluded by relative
# path prefix (POSIX separators) from every fresh-HOME manifest and grep
# below, so this probe's result depends only on what BrotherMode's own code
# does, not on how the local interpreter happens to be configured.
IGNORED_HOME_PREFIXES = ("Library/Caches",)

# The command-string shape every hook line in hooks/hooks.json uses: an
# interpreter (python3 or sh) followed by a CLAUDE_PLUGIN_ROOT-rooted,
# double-quoted script path, optionally followed by bare-word arguments
# (a subcommand such as "outcomes-append" or "pre"). This is scanned
# against the RAW command string, not tokenized with shlex, so it finds
# every invocation inside a `sh -c '...'` wrapper (the PreCompact line)
# exactly as readily as a bare top-level command.
_PROGRAM_RE = re.compile(
    r'(?P<interp>python3|sh)\s+"\$\{CLAUDE_PLUGIN_ROOT\}'
    r'(?P<relpath>/[^"]+)"(?P<args>(?:\s+[A-Za-z0-9_-]+)*)')


def parse_hook_programs(hooks_json_path, result):
    """Every BrotherMode hook ENTRY named in hooks/hooks.json (one entry per
    "command" string: a simple one-program line, or a compound `sh -c
    '...; ...'` chain that pipes ONE captured stdin into several programs
    in a row), found by regex-scanning each entry's own command string
    rather than by a hand-maintained list beside it (see the block comment
    above for why a hand-maintained list is the exact bug this replaces).
    A hook whose command does not match the expected shape, or a
    hooks.json with no "hooks" object at all, fails this step loudly
    (result.failed already called) rather than silently contributing zero
    entries: a probe that can go quiet on a hook it does not understand is
    no safer than the list it replaces.

    Codex finding 7 (v3 gap-closure cross-family review): this used to
    return one dict PER MATCHED PROGRAM, and the caller ran each program as
    its own isolated subprocess with its own synthetic stdin. That never
    executed the real command Claude Code's own hook runner executes (for
    Stop and PreCompact, a single `sh -c '...'` chain where every program
    shares ONE piped stdin captured once by `p=$(cat)`), so broken piping,
    quoting, conditionals, or an added write the regex does not recognize
    could pass unexercised. This now groups matches by their OWN raw
    command string instead, so the caller can hand that exact string to
    `sh -c` once, matching the real runtime's own execution shape.

    Returns a list of dicts with keys event, matcher (may be None),
    command_raw, and programs (a list of {interp, rel_path, args} dicts
    parsed from command_raw, kept for building the one shared payload the
    whole entry needs); or None on any parse failure."""
    try:
        with io.open(hooks_json_path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (IOError, OSError, ValueError) as exc:
        result.failed("could not read or parse %s: %s" % (hooks_json_path, exc))
        return None
    hooks_by_event = spec.get("hooks") if isinstance(spec, dict) else None
    if not isinstance(hooks_by_event, dict):
        result.failed("%s has no top-level \"hooks\" object" % hooks_json_path)
        return None
    entries = []
    for event, groups in hooks_by_event.items():
        for group in groups or []:
            matcher = group.get("matcher") if isinstance(group, dict) else None
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                command = hook.get("command") or "" if isinstance(hook, dict) else ""
                matches = list(_PROGRAM_RE.finditer(command))
                if not matches:
                    result.failed(
                        "hooks.json event %s has a command with no "
                        "recognizable CLAUDE_PLUGIN_ROOT program invocation "
                        "(add a case to _PROGRAM_RE or _payload_for_entry "
                        "before trusting this probe again): %r"
                        % (event, command))
                    return None
                programs = [{"interp": m.group("interp"),
                            "rel_path": m.group("relpath"),
                            "args": m.group("args").split()} for m in matches]
                entries.append({
                    "event": event,
                    "matcher": matcher,
                    "command_raw": command,
                    "programs": programs,
                })
    if not entries:
        result.failed("%s named no hook entries at all" % hooks_json_path)
        return None
    return entries


def _load_stopwarn_floor(paths, result):
    """STOPWARN_MIN_BYTES read directly from the copied tools/bm_telemetry.py
    rather than retyped as a literal here: a constant this probe
    reimplements by hand is exactly the class of drift that let the old
    three-entry-point list go stale. Returns the int, or None on any import
    or shape failure (result.failed already called)."""
    telemetry_py = os.path.join(paths["target"], "tools", "bm_telemetry.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_telemetry_for_rehearsal", telemetry_py)
        if spec is None or spec.loader is None:
            result.failed("could not build an import spec for %s" % telemetry_py)
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (ImportError, OSError, SyntaxError, AttributeError) as exc:
        result.failed("could not import %s to read STOPWARN_MIN_BYTES: %s"
                      % (telemetry_py, exc))
        return None
    floor = getattr(mod, "STOPWARN_MIN_BYTES", None)
    if not isinstance(floor, int) or floor <= 0:
        result.failed("tools/bm_telemetry.py has no usable STOPWARN_MIN_BYTES "
                      "constant (got %r)" % (floor,))
        return None
    return floor


def _build_probe_transcripts(tmp_root, stopwarn_floor, result):
    """Three throwaway transcripts, one per payload shape this step's
    invocations need: a small one (SessionEnd/outcomes-append does not care
    about size), one comfortably OVER stopwarn_floor (Stop/stop-warn's own
    trivial-size short-circuit, tools/bm_telemetry.py's STOPWARN_MIN_BYTES,
    read by _load_stopwarn_floor rather than retyped), and one whose last
    message is the CANARY_SENTENCE (PreCompact/precompact-brief writes the
    founder's last message verbatim; a leak of it is then visible by
    grepping the fresh HOME for that exact text).

    Returns (small_path, big_path, big_bytes, canary_path), or None on any
    write failure (result.failed already called)."""
    try:
        small = os.path.join(tmp_root, "probe-transcript-small.jsonl")
        with io.open(small, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user",
                                 "timestamp": "2026-08-01T00:00:00Z",
                                 "message": {"content": "hello"}}) + "\n")

        header = json.dumps({"type": "user", "timestamp": "2026-08-01T00:00:00Z",
                             "message": {"content":
                                        "hello, this is a substantial session"}})
        filler = json.dumps({"type": "assistant",
                             "timestamp": "2026-08-01T00:00:01Z",
                             "message": {"content": [
                                 {"type": "text", "text": "x" * 900}]}})
        # Comfortably over the floor (+50000 bytes), not merely past it, so a
        # future rounding change in either number cannot flip this silently.
        target_bytes = stopwarn_floor + 50000
        lines = [header]
        while sum(len(l) + 1 for l in lines) < target_bytes:
            lines.append(filler)
        big = os.path.join(tmp_root, "probe-transcript-big.jsonl")
        with io.open(big, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        big_bytes = os.path.getsize(big)

        canary = os.path.join(tmp_root, "probe-transcript-canary.jsonl")
        with io.open(canary, "w", encoding="utf-8") as fh:
            for rec in (
                {"type": "user", "timestamp": "2026-08-01T00:00:00Z",
                 "message": {"content": "hello"}},
                {"type": "assistant", "timestamp": "2026-08-01T00:00:01Z",
                 "message": {"content": [{"type": "text",
                                          "text": "sure, one moment"}]}},
                {"type": "user", "timestamp": "2026-08-01T00:00:02Z",
                 "message": {"content": CANARY_SENTENCE}}):
                fh.write(json.dumps(rec) + "\n")
    except OSError as exc:
        result.failed("could not write throwaway probe transcript(s): %s" % exc)
        return None
    return small, big, big_bytes, canary


def _command_for_entry(entry):
    """The REAL command Claude Code's own hook runner executes for this
    entry: `entry["command_raw"]` handed to `sh -c` verbatim, exactly once,
    whatever shape it is (a bare `python3 "${CLAUDE_PLUGIN_ROOT}/..."` line,
    or a compound `sh -c '...; ...'` chain, which then nests a second,
    inner `sh -c` the way the real runtime's own invocation does: this
    outer shell parses the already-quoted inner `sh -c '...'` text as its
    own single command line, exactly as it would if Claude Code itself
    handed the whole "command" string to /bin/sh -c). `${CLAUDE_PLUGIN_ROOT}`
    is expanded by THIS shell, from the CLAUDE_PLUGIN_ROOT the caller sets
    in the environment passed to run(), not by Python-side path
    substitution: codex finding 7 (v3 gap-closure cross-family review)
    is exactly that a probe which reconstructs child processes from the
    parsed program list, instead of running the string a real shell
    receives, can silently drop piping, quoting, conditionals or an
    appended write the parser's own regex does not recognize."""
    return ["sh", "-c", entry["command_raw"]]


def _payload_for_entry(entry, repo, session_id, transcripts):
    """The ONE stdin JSON body for a whole hook entry, shaped the way
    hooks/hooks.json actually pipes it in. Keyed by event (plus, for
    PreToolUse, by which program the entry's own matcher gates), never by
    an individual program: a compound Stop or PreCompact chain captures its
    stdin exactly once (`p=$(cat)`) and pipes an IDENTICAL copy of it into
    every program in the chain, so every program in one entry shares this
    single payload by construction, the same way the real shell does it.
    Returns None for an entry this function does not recognize; the caller
    treats that as a hard failure (see parse_hook_programs's own
    docstring), never a silent skip."""
    small_tp, big_tp, canary_tp = transcripts
    event = entry["event"]
    programs = {(os.path.basename(p["rel_path"]), tuple(p["args"]))
               for p in entry["programs"]}
    if event == "SessionStart":
        return json.dumps({"cwd": repo, "session_id": session_id,
                           "hook_event_name": "SessionStart"})
    if event == "SessionEnd":
        return json.dumps({"transcript_path": small_tp, "session_id": session_id,
                           "cwd": repo, "reason": "test",
                           "hook_event_name": "SessionEnd"})
    if event == "Stop":
        # Over STOPWARN_MIN_BYTES, the one thing this payload must get right
        # for the probe to be meaningful: a transcript UNDER the floor would
        # short-circuit stop-warn before its write path is even reached, and
        # a probe that never reaches the write path proves nothing about it.
        # Every OTHER program in this same compound chain (bm_lead.py
        # watchdog --tick, bm_view.py render --if-stale, bm_view.py alert
        # --tick) reads this identical payload too, exactly as `p=$(cat)`
        # makes them, so none of them needs its own recognized case here.
        return json.dumps({"session_id": session_id, "transcript_path": big_tp,
                           "cwd": repo, "hook_event_name": "Stop"})
    if event == "PreCompact":
        # bm_autosave.py precompact and bm_telemetry.py precompact-brief
        # share this same piped stdin too; canary_tp lets the leak check
        # (CANARY_SENTENCE must never reach the fresh HOME) mean something
        # for precompact-brief specifically.
        return json.dumps({"transcript_path": canary_tp, "session_id": session_id,
                           "cwd": repo, "hook_event_name": "PreCompact",
                           "trigger": "auto"})
    if event == "PreToolUse" and ("bm_fence_hook.py", ()) in programs:
        return json.dumps({"session_id": session_id, "cwd": repo,
                           "tool_name": "Edit",
                           "tool_use_id": "probe-tool-use-edit",
                           "tool_input": {
                               "file_path": os.path.join(repo, "would-be-edited.txt"),
                               "old_string": "v1", "new_string": "v2"},
                           "hook_event_name": "PreToolUse"})
    if event == "PreToolUse" and ("bm_bash_audit.py", ("pre",)) in programs:
        return json.dumps({"session_id": session_id, "cwd": repo,
                           "tool_name": "Bash",
                           "tool_use_id": "probe-tool-use-bash",
                           "tool_input": {"command": "echo probe"},
                           "hook_event_name": "PreToolUse"})
    if event == "PostToolUse" and ("bm_bash_audit.py", ("post",)) in programs:
        return json.dumps({"session_id": session_id, "cwd": repo,
                           "tool_name": "Bash",
                           "tool_use_id": "probe-tool-use-bash",
                           "tool_input": {"command": "echo probe"},
                           "hook_event_name": "PostToolUse"})
    return None


def _brothermode_refs(repo, git_env):
    r = run(["git", "for-each-ref", "refs/brothermode/"], cwd=repo, env=git_env, timeout=30)
    return [line for line in (r.stdout or "").splitlines() if line.strip()]


def _file_digest(full):
    """sha256 of a file's actual bytes, chunked the same way
    scripts/doctor.py's own check_checksums reads a file (65536-byte
    chunks), or None on any read failure. Codex finding 8 (v3 gap-closure
    cross-family review): a manifest keyed on byte COUNT alone lets a hook
    rewrite an existing file with different same-length content and still
    compare equal; hashing the content is what actually backs this probe's
    own "byte-for-byte unchanged" claim."""
    digest = hashlib.sha256()
    try:
        with io.open(full, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError:  # sbe: allow-silent unreadable file digests as None, per docstring, still detected as a manifest change
        return None
    return digest.hexdigest()


def _home_manifest(fake_home):
    """Every file under the fake HOME as {relpath: sha256 digest}, skipping
    IGNORED_HOME_PREFIXES (the interpreter's own bytecode cache, not a
    BrotherMode write). Compared before and after this step drives every
    hook program: any new key, or any digest that changed on an existing
    key, is a file BrotherMode wrote or rewrote before consent existed."""
    out = {}
    for root, _dirs, files in os.walk(fake_home):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, fake_home).replace(os.sep, "/")
            if any(rel == p or rel.startswith(p + "/") for p in IGNORED_HOME_PREFIXES):
                continue
            out[rel] = _file_digest(full)
    return out


def _grep_tree_for_text(root_dir, needle):
    """Every relpath under root_dir whose bytes contain needle, skipping
    IGNORED_HOME_PREFIXES. Read as raw bytes so a stray non-UTF-8 file
    cannot raise past this check; these are small throwaway files, so a
    per-file whole read is fine."""
    hits = []
    needle_bytes = needle.encode("utf-8")
    for root, _dirs, files in os.walk(root_dir):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, root_dir).replace(os.sep, "/")
            if any(rel == p or rel.startswith(p + "/") for p in IGNORED_HOME_PREFIXES):
                continue
            try:
                with io.open(full, "rb") as fh:
                    data = fh.read()
            except OSError:  # sbe: allow-silent unreadable throwaway file skipped, per docstring on this probe
                continue
            if needle_bytes in data:
                hits.append(rel)
    return hits


def step0_preconsent_probe(paths, base_env, result):
    entries = parse_hook_programs(HOOKS_JSON_PATH, result)
    if entries is None:
        return False
    stopwarn_floor = _load_stopwarn_floor(paths, result)
    if stopwarn_floor is None:
        return False

    repo = os.path.join(paths["tmp_root"], "preconsent-probe-repo")
    os.makedirs(repo)
    git_env = dict(base_env)
    git_env.update({"GIT_AUTHOR_NAME": "rehearsal",
                    "GIT_AUTHOR_EMAIL": "rehearsal@example.invalid",
                    "GIT_COMMITTER_NAME": "rehearsal",
                    "GIT_COMMITTER_EMAIL": "rehearsal@example.invalid"})
    for cmd in (["git", "init", "-q"],
               ["git", "config", "user.email", "rehearsal@example.invalid"],
               ["git", "config", "user.name", "rehearsal"],
               ["git", "config", "commit.gpgsign", "false"]):
        r = run(cmd, cwd=repo, env=git_env, timeout=30)
        if r.returncode != 0:
            result.failed("could not prepare the throwaway probe repo (%s): %s"
                          % (shortcmd(cmd), r.stderr.strip()))
            return False

    tracked = os.path.join(repo, "tracked.txt")
    with io.open(tracked, "w", encoding="utf-8") as fh:
        fh.write("v1\n")
    run(["git", "add", "-A"], cwd=repo, env=git_env, timeout=30)
    run(["git", "commit", "-q", "-m", "init"], cwd=repo, env=git_env, timeout=30)
    # Real, uncommitted work: exactly the shape a PreCompact snapshot would
    # capture if I1's gate did not exist.
    with io.open(tracked, "w", encoding="utf-8") as fh:
        fh.write("v2 uncommitted work that must never be touched\n")
    with io.open(os.path.join(repo, "untracked.txt"), "w", encoding="utf-8") as fh:
        fh.write("untracked WIP that must never be touched\n")

    env = dict(base_env)
    # The real hook runner expands ${CLAUDE_PLUGIN_ROOT} itself, inside the
    # shell it hands the "command" string to; this probe's own
    # _command_for_entry now runs that exact string via `sh -c` (codex
    # finding 7), so the expansion must come from THIS environment
    # variable too, never from Python-side path substitution.
    env["CLAUDE_PLUGIN_ROOT"] = paths["target"]
    cfg_path = env.get("BROTHERME_CONFIG")
    if cfg_path and os.path.exists(cfg_path):
        result.failed("the probe's own precondition is broken: a consent "
                      "config already exists at %s before step 4 (the only "
                      "step that creates one) has run" % _mask_home(cfg_path))
        return False

    transcripts = _build_probe_transcripts(paths["tmp_root"], stopwarn_floor, result)
    if transcripts is None:
        return False
    small_tp, big_tp, big_bytes, canary_tp = transcripts
    result.note("Stop payload transcript: %d bytes (floor read from "
               "tools/bm_telemetry.py STOPWARN_MIN_BYTES=%d, +50000 margin)"
               % (big_bytes, stopwarn_floor))

    before_refs = _brothermode_refs(repo, git_env)
    before_tree = _vault_manifest(repo)
    before_home = _home_manifest(paths["fake_home"])

    session_id = "preconsent-probe-session"
    exits_ok = True
    for entry in entries:
        payload = _payload_for_entry(
            entry, repo, session_id, (small_tp, big_tp, canary_tp))
        if payload is None:
            result.failed(
                "hooks.json names a hook entry this probe has no payload "
                "for (event %s, command: %r). Add a case to "
                "_payload_for_entry before trusting this probe again."
                % (entry["event"], entry["command_raw"]))
            return False
        cmd = _command_for_entry(entry)
        r = run(cmd, cwd=repo, env=env, input_text=payload, timeout=60)
        matcher_suffix = (", matcher=%s" % entry["matcher"]) if entry["matcher"] else ""
        label = "%s (%s hook%s)" % (shortcmd(cmd), entry["event"], matcher_suffix)
        result.note("%s: exit %s" % (label, r.returncode))
        if r.stdout.strip():
            result.note("  stdout: %r" % r.stdout.strip()[:300])
        if r.stderr.strip():
            result.note("  stderr: %r" % r.stderr.strip()[:300])
        if r.returncode != 0:
            exits_ok = False

    after_refs = _brothermode_refs(repo, git_env)
    after_tree = _vault_manifest(repo)
    after_home = _home_manifest(paths["fake_home"])
    canary_hits = _grep_tree_for_text(paths["fake_home"], CANARY_SENTENCE)

    refs_empty = (before_refs == [] and after_refs == [])
    refs_untouched = (before_refs == after_refs)
    tree_untouched = (before_tree == after_tree)
    home_untouched = (before_home == after_home)
    canary_absent = (canary_hits == [])
    new_home_files = sorted(set(after_home) - set(before_home))

    result.note("git for-each-ref refs/brothermode/ before: %r" % before_refs)
    result.note("git for-each-ref refs/brothermode/ after:  %r" % after_refs)
    result.note("probe repo's full tree walk unchanged (sha256 per file, "
               "not byte count): %s (%d file(s))"
               % (tree_untouched, len(after_tree)))
    result.note("fresh HOME unchanged (sha256 per file, not byte count), "
               "ignoring %s: %s (%d file(s) before, %d after)"
               % (", ".join(IGNORED_HOME_PREFIXES), home_untouched,
                  len(before_home), len(after_home)))
    if new_home_files:
        result.note("new file(s) written under the fresh HOME: %s"
                   % ", ".join(new_home_files[:20]))
    result.note("canary sentence found under the fresh HOME: %r" % canary_hits)

    if (exits_ok and refs_empty and refs_untouched and tree_untouched
            and home_untouched and canary_absent):
        result.passed(
            "all %d hook entry(ies) parsed from hooks/hooks.json (every "
            "entry's own real command, run once via sh -c exactly as the "
            "real hook runner runs it, not one reconstructed process per "
            "matched program) exited 0 and wrote nothing at all: "
            "refs/brothermode/ stayed empty, the probe repo's tree (%d "
            "file(s)) is byte-for-byte unchanged (sha256 comparison), the "
            "fresh HOME (%d file(s)) is unchanged (sha256 comparison), and "
            "the canary sentence never landed anywhere in it"
            % (len(entries), len(after_tree), len(after_home)))
        return True
    result.failed(
        "exits_ok=%s refs_empty=%s refs_untouched=%s tree_untouched=%s "
        "home_untouched=%s canary_absent=%s"
        % (exits_ok, refs_empty, refs_untouched, tree_untouched,
           home_untouched, canary_absent))
    return False


# ---------------------------------------------------------------------------
# step 2: the gate
# ---------------------------------------------------------------------------

def step2_gate(paths, env, skip_gate, result):
    if skip_gate:
        # I3: this used to call result.passed(), so the numbered line for a
        # step that ran nothing at all read "PASS" exactly like a step that
        # had proved something. It must never read PASS: skipped() records
        # it as its own SKIP status while still letting the rehearsal as a
        # whole finish (a disclosed skip is not a failure of the rehearsal
        # itself; see Result.ok).
        result.skipped("SKIPPED (gate not run, disclosed)")
        return True
    test_all = os.path.join(paths["target"], "tools", "test_all.py")
    cmd = [sys.executable, test_all]
    started = time.time()
    r = run(cmd, cwd=paths["target"], env=env, timeout=1800)
    elapsed = time.time() - started
    ok = (r.returncode == 0 and "ALL GREEN" in r.stdout)
    result.note("command: %s (cwd=%s)" % (shortcmd(cmd), paths["target"]))
    result.note("elapsed: %.1fs, exit code: %s" % (elapsed, r.returncode))
    for line in r.stdout.strip().splitlines():
        result.note(line)
    if r.stderr.strip():
        result.note("stderr: " + r.stderr.strip()[:2000])
    if ok:
        result.passed("exit code 0, ALL GREEN in the output")
    else:
        result.failed("exit code %s, ALL GREEN not found in the output"
                      % r.returncode)
    return ok


# ---------------------------------------------------------------------------
# step 3: scripts/install.py
# ---------------------------------------------------------------------------

def step3_install(paths, env, result):
    install_py = os.path.join(paths["target"], "scripts", "install.py")
    dry_cmd = [sys.executable, install_py, "--target", paths["target"],
              "--settings", paths["settings"], "--dry-run"]
    real_cmd = [sys.executable, install_py, "--target", paths["target"],
               "--settings", paths["settings"]]
    r_dry = run(dry_cmd, env=env, timeout=120)
    result.note("command: %s" % shortcmd(dry_cmd))
    result.note("dry-run exit code: %s" % r_dry.returncode)
    if r_dry.returncode != 0:
        result.note("dry-run stdout: " + r_dry.stdout.strip()[:2000])
        result.note("dry-run stderr: " + r_dry.stderr.strip()[:2000])
        result.failed("dry-run exit code was not 0")
        return False
    r_real = run(real_cmd, env=env, timeout=120)
    result.note("command: %s" % shortcmd(real_cmd))
    result.note("real-run exit code: %s" % r_real.returncode)
    for line in r_real.stdout.strip().splitlines():
        result.note(line)
    if r_real.stderr.strip():
        result.note("stderr: " + r_real.stderr.strip()[:2000])
    smoke_line_present = "fence hook ran end to end and exited 0" in r_real.stdout
    settings_written = os.path.isfile(paths["settings"])
    if r_real.returncode == 0 and smoke_line_present and settings_written:
        result.passed(
            "exit code 0, five hooks wired into %s, smoke line present"
            % paths["settings"])
        return True
    result.failed("exit code %s, smoke line present: %s, settings written: %s"
                  % (r_real.returncode, smoke_line_present, settings_written))
    return False


# ---------------------------------------------------------------------------
# step 4: scripts/setup.py flag mode, plus the vault-template copy
# ---------------------------------------------------------------------------

def step4_setup(paths, env, result):
    vault_template = os.path.join(paths["target"], "vault-template")
    try:
        n = copytree_excluding(vault_template, paths["vault"], set())
    except OSError as exc:
        result.failed("could not copy vault-template to %s: %s"
                      % (paths["vault"], exc))
        return False
    home_md = os.path.join(paths["vault"], "Home.md")
    if not os.path.isfile(home_md):
        result.failed("vault-template copy wrote %d file(s) but Home.md is "
                      "missing at %s" % (n, paths["vault"]))
        return False
    result.note("vault-template copied to %s (%d files), mirroring "
               "docs/SETUP.md Step 3, so the doctor vault check below has a "
               "real directory to look at" % (paths["vault"], n))

    setup_py = os.path.join(paths["target"], "scripts", "setup.py")
    cmd = [sys.executable, setup_py, "--vault", paths["vault"],
          "--mode", "clone", "--accept-notice"]
    r = run(cmd, env=env, timeout=300)
    result.note("command: %s" % shortcmd(cmd))
    result.note("exit code: %s" % r.returncode)
    for line in r.stdout.strip().splitlines()[:20]:
        result.note(line)
    if r.stderr.strip():
        result.note("stderr: " + r.stderr.strip()[:1000])

    if r.returncode != 0 or not os.path.isfile(paths["config"]):
        result.failed("exit code %s, config file present: %s"
                      % (r.returncode, os.path.isfile(paths["config"])))
        return False
    try:
        cfg = json.loads(io.open(paths["config"], encoding="utf-8").read())
    except (IOError, OSError, ValueError) as exc:
        result.failed("could not read %s after setup: %s" % (paths["config"], exc))
        return False
    fields_match = (cfg.get("setup_complete") is True
                    and cfg.get("installation_mode") == "clone"
                    and cfg.get("vault_path") == paths["vault"])
    if not fields_match:
        result.failed("config at %s does not hold the expected fields: %r"
                      % (paths["config"], cfg))
        return False
    result.passed("config at %s: setup_complete True, installation_mode "
                 "clone, vault_path %s" % (paths["config"], paths["vault"]))
    return True


# ---------------------------------------------------------------------------
# step 5: scripts/doctor.py --json
# ---------------------------------------------------------------------------

# Codex finding 9 (v3 gap-closure cross-family review): this used to omit
# "version", "runtime", "store" and "mode_wiring" from the required-PASS
# set with no comment at all, so a real FAIL on any of those four (a
# broken installed-runtime or hook-wiring defect, exactly the class doctor
# exists to catch) could not fail this step. Every key doctor.py's own
# --json ever returns must now appear in EXACTLY ONE of the two sets
# below, and step5_doctor asserts that coverage itself: an unclassified
# key (doctor.py grows a new check nobody has judged here) is a hard
# failure, not a silent gap.
REQUIRED_PASS_KEYS = ("fence", "version", "runtime", "consent", "vault",
                      "duplicate_install", "mode_wiring", "settings_json")

# Keys allowed to be something other than PASS here, each with the reason
# committed in code rather than left to a reader's memory.
EXCLUDED_KEYS = {
    "checksums": "a live development tree, not a clean checked-out "
                 "release, so CHECKSUMS.sha256 disagreeing is expected "
                 "here (doctor reports it FAIL, honestly; this probe "
                 "does not assert on it)",
    "store": "step 5 runs BEFORE step 6 ever creates a project store "
             "under the fake HOME, so check_store_health SKIPs here by "
             "ordering, every run, not by a defect",
}

# scripts/doctor.py's own EXIT_OK/EXIT_PROBLEMS, duplicated here rather
# than imported (doctor.py is a script, not a library, the same reason
# _mask_home is duplicated in this file instead of imported). doctor.py's
# own non-strict rule: EXIT_OK when payload["failed"] == 0, EXIT_PROBLEMS
# otherwise; this probe never passes --strict, so a SKIP never flips it.
DOCTOR_EXIT_OK = 0
DOCTOR_EXIT_PROBLEMS = 1


def step5_doctor(paths, env, result):
    doctor_py = os.path.join(paths["target"], "scripts", "doctor.py")
    cmd = [sys.executable, doctor_py, "--settings", paths["settings"], "--json"]
    r = run(cmd, cwd=paths["fake_home"], env=env, timeout=300)
    result.note("command: %s (cwd=%s)" % (shortcmd(cmd), paths["fake_home"]))
    result.note("exit code: %s" % r.returncode)
    try:
        payload = json.loads(r.stdout)
    except ValueError as exc:
        result.note("stdout: " + r.stdout.strip()[:2000])
        result.note("stderr: " + r.stderr.strip()[:2000])
        result.failed("stdout was not valid JSON (%s)" % exc)
        return False
    checks = payload.get("checks", [])
    by_key = {c["key"]: c for c in checks}
    for key in sorted(set(REQUIRED_PASS_KEYS) | set(EXCLUDED_KEYS)):
        c = by_key.get(key)
        if c is None:
            result.note("no entry in the JSON for check: %s" % key)
            continue
        first_line = (c.get("message") or "").splitlines()[0] if c.get("message") else ""
        result.note("%-18s %-4s %s" % (key, c.get("status"), first_line[:140]))

    unclassified = sorted(set(by_key) - set(REQUIRED_PASS_KEYS) - set(EXCLUDED_KEYS))
    unmet = [k for k in REQUIRED_PASS_KEYS
            if by_key.get(k, {}).get("status") != "PASS"]
    for key, reason in sorted(EXCLUDED_KEYS.items()):
        result.note("%s status recorded as-is: %s (excluded: %s)"
                   % (key, by_key.get(key, {}).get("status"), reason))

    # codex 9's other half: READ AND ENFORCE the process exit code, not
    # just log it. doctor.py's own non-strict rule is "EXIT_OK iff no
    # check FAILED anywhere" (scripts/doctor.py main(), fail_count over
    # ALL checks, not only the ones this probe requires); payload["failed"]
    # is that same count, straight from the JSON this probe already
    # parsed. Comparing the two catches doctor.py's PROCESS exit code
    # disagreeing with its own JSON verdict, without wrongly demanding
    # exit 0 on a run where the (excluded, expected) checksums FAIL
    # legitimately makes doctor's own exit code nonzero.
    expected_exit = DOCTOR_EXIT_OK if payload.get("failed") == 0 else DOCTOR_EXIT_PROBLEMS
    exit_code_honest = (r.returncode == expected_exit)
    result.note("doctor's own JSON says failed=%s; expected process exit "
               "%s for that; actual process exit was %s (match: %s)"
               % (payload.get("failed"), expected_exit, r.returncode,
                  exit_code_honest))

    if unclassified:
        result.failed(
            "doctor.py --json returned check key(s) this probe has not "
            "judged (add each to REQUIRED_PASS_KEYS or EXCLUDED_KEYS "
            "with a stated reason before trusting this probe again): %s"
            % ", ".join(unclassified))
        return False
    if not exit_code_honest:
        result.failed(
            "doctor.py's process exit code (%s) does not match what its "
            "own JSON verdict (failed=%s) implies (expected %s)"
            % (r.returncode, payload.get("failed"), expected_exit))
        return False
    if unmet:
        result.failed("status was not PASS for: %s" % ", ".join(unmet))
        return False
    result.passed("status PASS for: %s; exit code %s matches the JSON "
                 "verdict; excluded key(s) %s recorded with their reason"
                 % (", ".join(REQUIRED_PASS_KEYS), r.returncode,
                    ", ".join(sorted(EXCLUDED_KEYS))))
    return True


# ---------------------------------------------------------------------------
# step 6: one project through tools/bm_project.py
# ---------------------------------------------------------------------------

PROJECT_ID = "fresh-home-demo"
ACTOR_NAME = "Fresh Home Rehearsal"


def _bm(paths, name):
    return [sys.executable, os.path.join(paths["target"], "tools", name)]


def _run_cli(cmd, cwd, env, result, label):
    r = run(cmd, cwd=cwd, env=env, timeout=120)
    result.note("%s: %s" % (label, shortcmd(cmd)))
    result.note("  exit code: %s" % r.returncode)
    out = r.stdout.strip()
    if out:
        for line in out.splitlines()[:10]:
            result.note("  " + line)
    if r.returncode != 0 and r.stderr.strip():
        result.note("  stderr: " + r.stderr.strip()[:1000])
    return r


def step6_project(paths, env, result):
    project_dir = paths["project"]
    os.makedirs(project_dir, exist_ok=True)
    actor_flags = ["--actor-type", "human", "--actor-name", ACTOR_NAME]

    r = _run_cli(_bm(paths, "bm_store.py") + ["init"], project_dir, env,
                result, "init")
    if r.returncode != 0:
        result.failed("bm_store.py init exit code was not 0")
        return False

    r = _run_cli(
        _bm(paths, "bm_project.py")
        + ["start", "--project-id", PROJECT_ID, "--name", "Fresh Home Demo",
           "--goal", "exercise the Loop 2 CLI from a fresh project directory",
           "--out-json"] + actor_flags,
        project_dir, env, result, "start")
    if r.returncode != 0:
        result.failed("start exit code was not 0")
        return False

    r = _run_cli(
        _bm(paths, "bm_project.py")
        + ["task", "add", "--project-id", PROJECT_ID,
           "--title", "Write the welcome note", "--out-json"] + actor_flags,
        project_dir, env, result, "task add")
    if r.returncode != 0:
        result.failed("task add exit code was not 0")
        return False
    try:
        task_id = json.loads(r.stdout).get("task_id")
    except ValueError:
        task_id = None
    if not task_id:
        result.failed("task add did not print a task_id in --out-json output")
        return False
    result.note("task_id: %s" % task_id)

    # Three of the ten canonical lifecycle states, each a legal forward move
    # per brotherme/core/schema.py LEGAL_TRANSITIONS. The forbidden state
    # named 'done' never appears, by design of the protocol itself.
    transitions = (
        ("ready", "no blocking dependencies"),
        ("active", "starting the drafting"),
        ("awaiting review", "handed off for a second look"),
    )
    for to_state, reason in transitions:
        r = _run_cli(
            _bm(paths, "bm_project.py")
            + ["task", "transition", "--task-id", task_id, "--to", to_state,
               "--reason", reason, "--out-json"] + actor_flags,
            project_dir, env, result, "task transition to %s" % to_state)
        if r.returncode != 0:
            result.failed("task transition to %r exit code was not 0"
                          % to_state)
            return False

    r = _run_cli(
        _bm(paths, "bm_project.py")
        + ["review", task_id, "--project-id", PROJECT_ID, "--kind", "manual",
           "--note", "second pair of eyes on the note text",
           "--reason", "hand check of the note text against the brief",
           "--out-json"] + actor_flags,
        project_dir, env, result, "review")
    if r.returncode != 0:
        result.failed("review exit code was not 0")
        return False

    r = _run_cli(
        _bm(paths, "bm_project.py") + ["status", "--project-id", PROJECT_ID],
        project_dir, env, result, "status")
    if r.returncode != 0:
        result.failed("status exit code was not 0")
        return False

    r = _run_cli(
        _bm(paths, "bm_project.py") + ["next", "--project-id", PROJECT_ID],
        project_dir, env, result, "next")
    if r.returncode != 0:
        result.failed("next exit code was not 0")
        return False

    r = _run_cli(
        _bm(paths, "bm_project.py")
        + ["deliver", "--project-id", PROJECT_ID, "--partial", "--out-json"],
        project_dir, env, result, "deliver --partial")
    if r.returncode != 0:
        result.failed("deliver --partial exit code was not 0")
        return False

    canvas = os.path.join(project_dir, "CANVAS.md")
    packet = os.path.join(project_dir, "DELIVERY-PACKET.md")
    store = os.path.join(project_dir, ".brothermode", "store.sqlite3")
    missing = [p for p in (canvas, packet, store) if not os.path.isfile(p)]
    if missing:
        result.failed("path(s) not found where documented: %s"
                      % ", ".join(missing))
        return False
    result.note("CANVAS.md: %s" % canvas)
    result.note("DELIVERY-PACKET.md: %s" % packet)
    result.note("store: %s" % store)
    result.passed("nine CLI calls all returned exit code 0; CANVAS.md, "
                 "DELIVERY-PACKET.md and the store are present at the "
                 "documented paths")
    return True


# ---------------------------------------------------------------------------
# step 7: scripts/uninstall.py --remove-consent
# ---------------------------------------------------------------------------

def _vault_manifest(vault_dir):
    """Every file under the vault as {relpath: sha256 digest} (see
    _file_digest and _home_manifest's own docstrings: codex finding 8, a
    byte-count manifest cannot catch a same-length rewrite)."""
    out = {}
    for root, _dirs, files in os.walk(vault_dir):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, vault_dir)
            out[rel] = _file_digest(full)
    return out


def step7_uninstall(paths, env, before_manifest, result):
    uninstall_py = os.path.join(paths["target"], "scripts", "uninstall.py")
    cmd = [sys.executable, uninstall_py, "--settings", paths["settings"],
          "--remove-consent"]
    r = run(cmd, env=env, timeout=120)
    result.note("command: %s" % shortcmd(cmd))
    result.note("exit code: %s" % r.returncode)
    for line in r.stdout.strip().splitlines():
        result.note(line)
    if r.stderr.strip():
        result.note("stderr: " + r.stderr.strip()[:1000])
    if r.returncode != 0:
        result.failed("uninstall.py exit code was not 0")
        return False

    try:
        settings_text = io.open(paths["settings"], encoding="utf-8").read()
    except (IOError, OSError) as exc:
        result.failed("could not re-read %s after uninstall: %s"
                      % (paths["settings"], exc))
        return False
    hooks_gone = paths["target"] not in settings_text
    consent_gone = not os.path.isfile(paths["config"])
    after_manifest = _vault_manifest(paths["vault"])
    vault_untouched = (after_manifest == before_manifest)

    result.note("hooks_gone=%s (settings.json no longer names %s)"
               % (hooks_gone, paths["target"]))
    result.note("consent_gone=%s (%s does not exist)"
               % (consent_gone, paths["config"]))
    result.note("vault_untouched=%s (%d file(s) before, %d after, "
               "content-hash manifest identical: %s)" % (vault_untouched,
                                             len(before_manifest),
                                             len(after_manifest),
                                             vault_untouched))

    if hooks_gone and consent_gone and vault_untouched:
        result.passed("hooks_gone, consent_gone, vault_untouched all true")
        return True
    result.failed("hooks_gone=%s consent_gone=%s vault_untouched=%s"
                 % (hooks_gone, consent_gone, vault_untouched))
    return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="rehearse_fresh_install.py",
        description="Fresh-HOME clone-path install rehearsal (Loop 3 design "
                    "D-6). Never touches the real HOME or the real "
                    "~/.claude.")
    p.add_argument("--skip-gate", action="store_true",
                   help="skip step 2 (python3 tools/test_all.py from the "
                        "copy) for time; the default runs it")
    p.add_argument("--keep", action="store_true",
                   help="do not delete the temporary directories on exit "
                        "(for inspecting a failure)")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    tmp_root = tempfile.mkdtemp(prefix="bm-rehearsal-")
    paths = build_paths(tmp_root)
    env = build_env(paths)

    _out("rehearse_fresh_install.py: fresh-HOME clone-path rehearsal")
    _out("  temp root: %s" % tmp_root)
    _out("  fake HOME: %s" % paths["fake_home"])
    _out("  gate: %s" % ("SKIPPED (--skip-gate)" if args.skip_gate else "RUN"))
    _out("")

    results = []
    overall_ok = True
    vault_manifest_before = None
    step0_ok = False

    try:
        r1 = Result(1, STEP_TITLES[0])
        ok1 = step1_clone(paths, r1)
        results.append(r1)
        overall_ok = overall_ok and ok1
        r1.emit()
        if not ok1:
            raise SystemExit  # nothing downstream can proceed without the copy

        # I3: step 0, run here (right after the clone, strictly before step
        # 4 ever creates a consent config) and reported separately from the
        # seven numbered steps, so it never disturbs their established
        # numbering. Its own failure does not abort the rehearsal, matching
        # every other step here except step 1: a probe result is worth
        # having even if something downstream also breaks.
        r0 = Result(0, "I1 pre-consent no-write probe (permanent proof "
                      "the consent gate holds)", total=TOTAL_STEPS)
        step0_ok = step0_preconsent_probe(paths, env, r0)
        r0.emit()
        _out("")

        r2 = Result(2, STEP_TITLES[1])
        ok2 = step2_gate(paths, env, args.skip_gate, r2)
        results.append(r2)
        overall_ok = overall_ok and ok2
        r2.emit()

        r3 = Result(3, STEP_TITLES[2])
        ok3 = step3_install(paths, env, r3)
        results.append(r3)
        overall_ok = overall_ok and ok3
        r3.emit()

        r4 = Result(4, STEP_TITLES[3])
        ok4 = step4_setup(paths, env, r4)
        results.append(r4)
        overall_ok = overall_ok and ok4
        r4.emit()
        if ok4 and os.path.isdir(paths["vault"]):
            vault_manifest_before = _vault_manifest(paths["vault"])

        r5 = Result(5, STEP_TITLES[4])
        ok5 = step5_doctor(paths, env, r5)
        results.append(r5)
        overall_ok = overall_ok and ok5
        r5.emit()

        r6 = Result(6, STEP_TITLES[5])
        ok6 = step6_project(paths, env, r6)
        results.append(r6)
        overall_ok = overall_ok and ok6
        r6.emit()

        r7 = Result(7, STEP_TITLES[6])
        if vault_manifest_before is None:
            r7.failed("step 4 produced no vault manifest to compare against")
            ok7 = False
        else:
            ok7 = step7_uninstall(paths, env, vault_manifest_before, r7)
        results.append(r7)
        overall_ok = overall_ok and ok7
        r7.emit()
    except SystemExit:  # sbe: allow-silent internal signal from the step1-failed raise above, overall_ok is already False
        pass
    finally:
        if args.keep:
            _out("")
            _out("--keep given: leaving %s in place." % tmp_root)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

    _out("")
    # I3/I2-style honesty: a SKIP is not a PASS, so the closing line counts
    # them separately instead of folding a disclosed --skip-gate skip into
    # the same bucket as a step that actually proved something.
    pass_count = sum(1 for r in results if r.status == "PASS")
    skip_count = sum(1 for r in results if r.status == "SKIP")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    all_seven_accounted = overall_ok and len(results) == len(STEP_TITLES)
    all_green = all_seven_accounted and step0_ok
    _out("rehearse_fresh_install.py: step 0 (I1 pre-consent probe): %s. "
        "%d/%d step(s) PASS, %d SKIP, %d FAIL. %s"
        % ("PASS" if step0_ok else "FAIL", pass_count, len(STEP_TITLES),
           skip_count, fail_count,
           "ALL GREEN" if all_green else "NOT ALL GREEN"))
    return EXIT_OK if all_green else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
