#!/usr/bin/env python3
"""BrotherMode SessionStart: digest plus mechanical nags. MUST always exit 0.

THE WINDOWS PORT (2026-08-17). This file is tools/bm_sessionstart.sh ported
statement for statement to Python, because `sh` is not on the Windows PATH
and a SessionStart hook wired through it was installed-and-silently-dead on
any Windows machine without Git Bash. The port was proved byte-identical to
the shell version with diff, on the same fixtures the consent suite uses
(an unconsented HOME, then a consented one), before the shell version was
deleted. Python is the interpreter every other hook already requires, so
this adds no new dependency on any platform. Windows behavior remains
UNVERIFIED until a real Windows machine runs it; nothing in this file has
been exercised outside POSIX yet, and docs/WINDOWS-CHECK.md is the protocol
for closing that.

Output is injected into session context (10k char cap; we stay far under).
Every check is fail-open ON PURPOSE, matching the shell version line for
line: this hook runs at every session start, so a tool that is absent on an
older install, or that crashes, must degrade to silence (or one short line)
rather than take the session down with it. That is why most calls below
discard stderr and ignore their exit codes; the shell spelled it 2>/dev/null
and `|| true`, and this file spells it _run(..) with the same intent.

The subprocess import is a NAMED exception in tools/test_bm.py's
zero-network suite, the same local-execution posture as bm_autosave.py
driving git: every process this file starts is a sibling tool from this
repository, on this machine, with no network anywhere. SECURITY.md
documents it beside the other named exceptions.

Consent gate (Loop 3 design D-1): before any write or store command below,
check consent via scripts/setup.py's cheap --consent-state probe (exit 0
consented, non-zero otherwise: absent config, setup_complete false, or a
broken config all read the same way here, fail closed). Not consented means
this script prints exactly one plain sentence and exits 0 having written
nothing at all: no digest, no telemetry, no store verify. scripts/setup.py
is the ONLY place that creates ~/.brotherme/config.json.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import glob
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.dirname(HERE)

#: What the store-health capture is allowed to stay silent about: silent when
#: healthy or when no store exists yet, printed whenever verify reports
#: something the founder has to act on. "refused (schema-" is matched too
#: (2026-08-04): a store one schema behind or ahead stopped being reported as
#: STORE CORRUPT that day, which is the truth, but it is still actionable
#: (one writable command migrates it); without this alternative the fix would
#: have traded a scary visible message for an accurate invisible one.
_STORE_WORRY = re.compile(
    r"problem\(s\) found|STORE CORRUPT|refused \(schema-|"
    r"refused \(git-tracked-store\)|WARNING|"
    r"unexpected error|db-busy|stale-identity|Traceback")
#: bm_store.py also refuses 'git-exposed-store' in any git repository that has
#: no store yet (nothing to leak, and `init` adds the ignore line), and
#: 'git-state-unknown' when it cannot read the index or an ignore file, so
#: those refusals are printed only when a store file is actually on disk.
_STORE_EXPOSED = re.compile(r"refused \(git-(?:exposed-store|state-unknown)\)")


def _store_on_disk():
    """True when a BrotherMode store file exists where bm_store.py would look:
    under BROTHERMODE_ROOT when it names a directory (bm_store.resolve_root
    ignores it otherwise), else in any directory from cwd up. An error
    answers True, so a privacy warning is shown rather than hidden."""
    try:
        rel = os.path.join(".brothermode", "store.sqlite3")
        env_root = os.environ.get("BROTHERMODE_ROOT")
        if env_root and os.path.isdir(os.path.realpath(env_root)):
            return os.path.isfile(os.path.join(os.path.realpath(env_root), rel))
        here = os.getcwd()
        while True:
            if os.path.isfile(os.path.join(here, rel)):
                return True
            parent = os.path.dirname(here)
            if parent == here:
                return False
            here = parent
    except (OSError, ValueError):
        return True


#: QS1 (the owner's 1.1.1 scope ruling, 2026-10-10): the routine start output is opt in. A session start used to
#: inject every housekeeping line (nags, progress page, stall sweep, handover status, idle and forecast lines, every
#: reconcile row), measured at 5,820 bytes in a fake established project and 17.4 KB on this estate. Either switch set
#: to exactly "1" turns it back on; anything else, unset included, keeps the start quiet. BROTHERMODE_MAINTAINER=1 was
#: already the digest's switch and keeps working.
VERBOSE_SWITCHES = ("BROTHER_VERBOSE_START", "BROTHERMODE_MAINTAINER")
#: What a quiet start drops from bm_reconcile.py's rows, and ONLY this: VALID rows, and the NO-DATA categories (its own
#: row "category" words) that purely report. Every other row shows, an unknown class or category included, so a new
#: kind of finding is never hidden by default. no-upstream is routine on any branch never pushed;
#: no-remote-tracking-branch repeats at every start for a remote with no main or master, which a fetch cannot change.
QUIET_CLASSES = ("VALID",)
REPORTING_NO_DATA_CATEGORIES = ("no-upstream", "no-remote-tracking-branch")


def verbose_start(env):
    """True only when one of VERBOSE_SWITCHES is exactly "1" in env (a mapping such as os.environ)."""
    return any(env.get(name) == "1" for name in VERBOSE_SWITCHES)


def actionable_reconcile(code, text):
    """What a quiet start prints for bm_reconcile.py --json's (exit code, output). Exit 0 or 127: nothing. Exit 1 with
    the tool's JSON: every row except the quiet ones (QUIET_CLASSES, REPORTING_NO_DATA_CATEGORIES), under one header
    line ("" when none is left). Any other exit with no output is one NO-DATA line. Anything else (a refusal, a crash,
    output that is not that JSON) is a failure the user must see, so it passes through unchanged."""
    if code in (0, 127):
        return ""
    if not (text or "").strip():
        return "NO-DATA: bm_reconcile exited %s with no output\n" % code
    passthrough = text.rstrip("\n") + "\n"
    if code != 1:
        return passthrough

    def shown(r):
        if r["class"] in QUIET_CLASSES:
            return False
        if r["class"] == "NO-DATA" and r.get("category") in REPORTING_NO_DATA_CATEGORIES:
            return False
        # store-unreadable is also reported in a git repository that has no store yet (the same git-exposed-store
        # refusal store health handles), so, like there, it needs a store on disk to be worth a line.
        return r.get("category") != "store-unreadable" or _store_on_disk()
    try:
        kept = [r for r in json.loads(text)["rows"] if shown(r)]
        lines = ["%s | %s | %s | %s | route %s" % (
            r["class"], ("%s %s" % (r["kind"], r["subject"])) if r.get("subject") else r["kind"], r["reason"],
            r.get("next_action") or "(no action proposed)", r.get("route") or "") for r in kept]
    except (ValueError, KeyError, TypeError, AttributeError):
        return passthrough
    if not lines:
        return ""
    return ("bm_reconcile: %d row(s) need action; BROTHER_VERBOSE_START=1 shows every row\n" % len(lines)
            + "\n".join(lines) + "\n")


def _say(text):
    """Write to stdout AND FLUSH, and the flush is the load-bearing half.

    The shell version's children inherited stdout and wrote as they ran, so
    every line landed in the order the script produced it. Python buffers a
    pipe, so without this the whole digest arrived AFTER every child's
    output and the injected context read in a different order than it had
    for a year. Found by diffing the two versions on a consented fixture
    before the shell one was deleted; the CONTENT matched on the first try
    and only the order did not, which is exactly the class of difference a
    port is most likely to ship."""
    sys.stdout.write(text)
    sys.stdout.flush()


def _tool(*parts):
    return os.path.join(DIR, *parts)


#: A project that has never been planned and never been queued has no
#: outstanding obligations, so the lines that report an ABSENCE of a handover,
#: a close pack, a queue, a calibration history or an upstream branch are
#: telling a first-time user they owe five things they have never started.
#: Those lines are correct on an established project and false on an empty one,
#: which is why the fix lives here, in the hook that decides what to SHOW,
#: rather than in the five general-purpose tools that a founder also runs
#: directly and where the same lines are right.
#:
#: The signal is deliberately two conditions rather than one. Keying only on
#: the queue file would mean a project that never uses a queue counts as brand
#: new forever, and would suppress a genuinely owed close pack for its whole
#: life. Requiring BOTH a missing plan and a missing queue means the moment a
#: project acquires either, every one of these lines comes back.
#:
#: The plan patterns are the same ones bm_progress_check.py globs, and that
#: tool is the precedent this follows: it already answers a planless project
#: with "no plan yet, nothing owed" instead of a demand.
PLAN_GLOBS = ("docs/plan/*PLAN*.md", "docs/plan/*WBS*.md", "PLAN.md")
QUEUE_REL = os.path.join("docs", "plan", "QUEUE.json")


def _is_first_run():
    """True when this project shows no sign of having been worked yet.

    Fail-open like everything else in this file: any error answers False, so
    an unreadable directory shows the full output rather than silently hiding
    a real obligation. Hiding a true debt is the worse failure of the two."""
    try:
        root = os.getcwd()
        if os.path.isfile(os.path.join(root, QUEUE_REL)):
            return False
        for pattern in PLAN_GLOBS:
            if glob.glob(os.path.join(root, pattern)):
                return False
        return True
    except (OSError, ValueError):
        return False


def _load_bm_repo_scope():
    """Load bm_repo_scope.py by path, same shape as bm_telemetry.py's
    _load_bm_learning: works from any cwd and never raises."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_repo_scope_for_sessionstart",
            os.path.join(HERE, "bm_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None


def _no_vault_bound():
    """True when bm_vault.py's own _default_vault() resolves to nothing:
    neither BM_VAULT_ROOT/BROTHERMODE_VAULT nor the installer config
    (~/.claude/bm_vault.json) names a folder. Row V2: a fresh install binds
    no vault and nothing ever nudges the newcomer to fix that, so the first
    session that would otherwise show "new project" also names the gap.
    Loaded by path, same technique _load_bm_repo_scope() above already uses
    (bm_vault.py is a sibling script, not a package this file can import),
    and fail-open like every check in this file: an unreadable or broken
    bm_vault.py degrades to False (no nudge shown) rather than a crash,
    since a missed nudge is a much smaller failure than taking the session
    down over a purely informational line."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_vault_for_sessionstart", os.path.join(HERE, "bm_vault.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._default_vault() is None
    except Exception:  # sbe: allow-silent optional nudge; a broken load just skips the line
        return False


def _run(args, stdin_text=None, capture=False, keep_stderr=False,
         stderr_sink=None):
    """One sibling tool, with the shell version's exact degrade semantics:
    OSError (interpreter or file missing) reads as a silent non-run, the
    same way 2>/dev/null swallowed it, and the caller decides what an exit
    code means. Returns (returncode, stdout) with stdout None unless
    captured. A list given as stderr_sink receives stderr captured on its
    own, kept out of stdout (it wins over keep_stderr)."""
    kwargs = {"universal_newlines": True}
    if stdin_text is not None:
        kwargs["input"] = stdin_text
    else:
        kwargs["stdin"] = subprocess.DEVNULL
    if capture:
        kwargs["stdout"] = subprocess.PIPE
    if stderr_sink is not None:
        kwargs["stderr"] = subprocess.PIPE
    elif keep_stderr:
        kwargs["stderr"] = subprocess.STDOUT if capture else None
    else:
        kwargs["stderr"] = subprocess.DEVNULL
    try:
        result = subprocess.run([sys.executable] + list(args), **kwargs)
    except OSError:
        return 127, ""
    if stderr_sink is not None:
        stderr_sink.append(result.stderr or "")
    return result.returncode, (result.stdout if capture else None)


def main():
    # Capture the hook JSON from stdin ONCE so we can both ignore it
    # (digest/nags) and replay it to the compaction hint below. The shell
    # version's PAYLOAD="$(cat)" stripped trailing newlines; matched here.
    try:
        payload = sys.stdin.read()
    except (IOError, OSError, ValueError):
        payload = ""
    payload = payload.rstrip("\n")

    # E76: per-repository hook scoping, checked before anything else this
    # hook does (before even the consent probe), so an inactive repository
    # gets zero work and zero I/O from this hook, not just a shorter run.
    _rs = _load_bm_repo_scope()
    if _rs is not None and _rs.hooks_off(payload=payload):
        return 0

    # The shell spelled this >/dev/null 2>&1: the probe is an exit code, and
    # nothing it might print belongs in session context.
    code, _ = _run([_tool("scripts", "setup.py"), "--consent-state"],
                   capture=True)
    if code != 0:
        _say("BrotherMode setup is not complete yet; run: "
             "python3 scripts/setup.py\n")
        return 0

    first_run = _is_first_run()
    verbose = verbose_start(os.environ)
    if first_run:
        # The one line a newcomer actually needs, and the whole gap between
        # "installed" and "used". README.md names this command as the first
        # thing to type; until now the product's own first words never did.
        # This hook also runs at Codex's SessionStart, which has no slash
        # commands, so the slash command is named for Claude Code only.
        _say("BrotherMode: new project. To begin, say what you want done "
             "(in Claude Code: /brother).\n")
        # Row V2: a fresh install binds no vault at all, and a nag that fires
        # every session stops being read, so this line is gated to the same
        # first-run moment as the line above, silent ever after.
        if _no_vault_bound():
            _say("No memory vault is bound yet; once you begin, Brother will "
                 "ask where it should live and bind it.\n")

    # R-4 (persona dogfood 2026-09-07): DIGEST.md printed to every session,
    # including a beginner's very first one, who has no use for an
    # engineering digest and no way to know why it appeared. It is real
    # and useful to whoever maintains this product, so it is gated to
    # that reader rather than deleted: BROTHERMODE_MAINTAINER=1 opts in
    # (QS1: so does BROTHER_VERBOSE_START=1, through verbose_start).
    # Anyone else already got the one line above (first_run's welcome, or
    # nothing when there is nothing new), and gets nothing more here.
    if verbose:
        try:
            with io.open(_tool("DIGEST.md"), encoding="utf-8") as fh:
                _say(fh.read())
        except (IOError, OSError):
            pass

    # QS1: every routine line below, through the forecast line, runs only on the switch.
    if verbose and not first_run:
        _run([_tool("tools", "bm_telemetry.py"), "startup-nags"])
    # PROGRESS PAGE OWED (founder directive 2026-08-10): one line ONLY when a
    # plan exists and the page is missing or older than that plan; silent
    # otherwise, because a nag that fires every session stops being read.
    if verbose:
        _run([_tool("tools", "bm_progress_check.py"), "status"])
    # Stall sweep (Loop SD): pure read, prints stale fences and dead owners
    # with their exact clearing command. Fail-open like everything here.
    if verbose:
        _run([_tool("tools", "bm_stall.py"), "sweep"])

    # BATON CEREMONY OPENING HALF (R1.3). CLAUDE.md's baton ceremony section
    # calls `bm_handover.py detect` the START half every session runs before
    # new work. detect promises exit 0 always and turns every read failure it
    # knows about into a stated NO-DATA line, so a non-zero exit or empty
    # output here means something UNEXPECTED happened (the file moved), and
    # that case degrades to one short line, never a traceback.
    if verbose:
        code, detect_out = _run([_tool("tools", "bm_handover.py"), "detect"],
                                capture=True)
        detect_out = (detect_out or "").rstrip("\n")
        suppressed_first_run = False
        if code == 0 and detect_out and first_run:
            filtered = "\n".join(
                ln for ln in detect_out.splitlines()
                if not ln.startswith("NO-DATA: no handover pack exists yet")
                and not ln.startswith("NO-DATA: no handover zip exists yet"))
            # A first-run all-suppressed output is not a failure; track it.
            suppressed_first_run = bool(filtered == "" and detect_out)
            detect_out = filtered
        if code == 0 and detect_out:
            _say(detect_out + "\n")
        elif not suppressed_first_run:
            _say(
                "baton ceremony check (bm_handover.py detect) could not run "
                "this session; run it by hand, see CLAUDE.md baton ceremony "
                "section\n")

        # CLOSE-PACK OWED (2026-08-12): a session opens being told the previous
        # one left no handover, which is the person who can still do something
        # about it. Silent when nothing is owed. Runs HERE rather than on Stop
        # because this script has already passed the consent door, so the check
        # inherits that gate instead of needing its own.
        code, owed_out = _run([_tool("tools", "bm_handover.py"), "owed"],
                              capture=True)
        if code != 127 and owed_out:
            for line in owed_out.splitlines(True):
                if line.startswith("CURRENT"):
                    continue
                if first_run and line.startswith(
                        "OWED: no close pack exists in this checkout at all"):
                    continue
                _say(line)

    _run([_tool("tools", "bm_telemetry.py"), "check-update"])

    # IDLE CONTROL (O19 plus A4, founder order 2026-08-15: never stay idle).
    # Two one-line verdicts, both fail open, because a session that cannot
    # compute its idle verdict must still start; the absence of the line is
    # itself visible.
    if verbose and not first_run:
        _run([_tool("tools", "bm_idle.py"), "check"])
        code, cal_out = _run([_tool("tools", "bm_forecast.py"), "calibrate",
                              "--clock", "agent", "--basis", "judged"],
                             capture=True)
        if code != 127 and cal_out:
            lines = cal_out.splitlines(True)
            if lines:
                _say(lines[-1])

    # If this session resumed from a compaction, point it at the autosave.
    _run([_tool("tools", "bm_telemetry.py"), "compact-hint"],
         stdin_text=payload)

    # Store health: silent when healthy or when no store exists yet, printed
    # whenever there is something real to see, so a lost database is never a
    # session's whole SessionStart output being nothing.
    _code, health = _run([_tool("tools", "bm_store.py"), "verify"],
                         capture=True, keep_stderr=True)
    health = (health or "").rstrip("\n")
    said = set()
    if health and (_STORE_WORRY.search(health) or (
            _STORE_EXPOSED.search(health) and _store_on_disk())):
        _say(health + "\n")
        said.update(health.splitlines())

    # Startup reconciliation (Z2.3, docs/RECOVERY-TRUTH.md): compares what
    # the store persists against observed reality (git, the filesystem,
    # controller-run liveness) and classifies drift. Silent when nothing
    # observable contradicts the store (exit 0), printed whenever there is
    # something real to see (exit 1 found a row, exit 2 is its own
    # NO-DATA/usage refusal, both worth showing); 127 means the tool is
    # missing on an older install, the same silent-degrade every other
    # call in this file gives that case. Guarded on its own, on top of
    # that: this block's own contract is "never block session start," so
    # any unexpected exception here (not just a bad exit code) degrades to
    # one NO-DATA line instead of taking the hook down with it.
    # The harness session id rides along so the pass can tell which
    # findings are THIS session's own (route mine, via the label its
    # fence token derives to) rather than another session's or the
    # founder's. Read from the same payload the fence hook reads it from;
    # absent or unparsable means no id, and nothing routes mine.
    try:
        session_id = json.loads(payload).get("session_id") if payload else ""
    except (ValueError, AttributeError):  # sbe: allow-silent an unparsable payload means no session id, the fail-closed answer (nothing is mine)
        session_id = ""
    session_args = (["--session-id", session_id.strip()]
                    if isinstance(session_id, str) and session_id.strip()
                    else [])
    try:
        # QS1: a quiet start reads the rows as JSON, so it can keep exactly
        # the ones that need action by their class and category, and passes
        # any failure output through unchanged (actionable_reconcile).
        if not verbose:
            # stderr is captured apart from the JSON on stdout, so a warning
            # (BROTHERMODE_SKIP_GIT_CONTAINMENT=1 prints one per store open)
            # never breaks the parse; it is passed through after the rows,
            # each distinct line once and none the store health block above
            # already printed.
            err = []
            code, reconciled = _run([_tool("tools", "bm_reconcile.py")]
                                    + session_args + ["--json"],
                                    capture=True, stderr_sink=err)
            _say(actionable_reconcile(code, reconciled))
            for line in "".join(err).splitlines():
                if line.strip() and line not in said:
                    said.add(line)
                    _say(line + "\n")
            return 0
        code, reconciled = _run([_tool("tools", "bm_reconcile.py")]
                                + session_args,
                                capture=True, keep_stderr=True)
        reconciled = (reconciled or "").rstrip("\n")
        if code not in (0, 127) and reconciled and first_run:
            reconciled = "\n".join(
                ln for ln in reconciled.splitlines()
                if "no upstream tracking branch is configured" not in ln)
        if code not in (0, 127) and reconciled:
            _say(reconciled + "\n")
    except Exception as exc:
        _say("NO-DATA: reconcile: %s\n" % type(exc).__name__)

    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        # The header's contract: MUST always exit 0. An unexpected crash in
        # a session-start nag must never take the session down with it.
        code = 0
    sys.exit(code)
