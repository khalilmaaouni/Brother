#!/usr/bin/env python3
"""host_capability: WBS-70.03's Host Capability Receipt.

docs/plan/1.0.17/WBS-VERIFIED-BREAKDOWN-2026-09-13.md item 5 and the
convergence roadmap's WBS-70.03 section name fourteen facts "every
consequential run can identify": host, host_version, environment,
pre-tool hook, enforceable deny, workspace isolation, resume, signed-in
headless, structured tool events, receipt path, network control, known
limitations, certification evidence, certification freshness. The
roadmap's own closing line: "Autonomy consumes capability. Do not branch
behavior on brand name alone when capability is what matters."

A FACT LOOKED UP IN A TABLE, NEVER A JUDGEMENT (receipt_door.MARK_TABLE's
own stance, reused here on purpose). CAPABILITY_TABLE below repeats
nothing this repository has not already measured and written down:
products/brothermode/docs/RUNTIMES.md's own capability table (Claude
Code, OpenAI Codex CLI) and products/brothermode/docs/CURSOR-COMPAT.md's
own honest-limit section (Cursor). Where those documents say UNVERIFIED
or admit a gap, this table says NO-DATA and names the same gap; it never
upgrades a documented unknown into an answer.

HOST DETECTION IS REUSED, NOT REINVENTED: brother_paths.client() is
already the one function this estate uses to tell Claude, Codex and
Cursor apart (marker variables, then a plugin manifest on disk). This
module calls it once and asks nothing else about identity.

Python 3, standard library only. No network."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_paths  # noqa: E402

NODATA = "NO-DATA"

#: The fourteen facts, in the roadmap's own order. host_capability_receipt()
#: always returns exactly this key set, so a reader never has to guess
#: whether a missing key means "no" or "never asked".
RECEIPT_FIELDS = (
    "host", "host_version", "environment", "pre_tool_hook",
    "enforceable_deny", "workspace_isolation", "resume",
    "signed_in_headless", "structured_tool_events", "receipt_path",
    "network_control", "known_limitations", "certification_evidence",
    "certification_freshness",
)

#: The ten facts CAPABILITY_TABLE carries per host (RECEIPT_FIELDS minus
#: host, host_version, environment and receipt_path, which are read fresh
#: for every run rather than looked up, and minus certification_freshness,
#: which is computed from CERT_PATH_TABLE below rather than typed here).
_TABLE_FIELDS = ("pre_tool_hook", "enforceable_deny", "workspace_isolation",
                 "resume", "signed_in_headless", "structured_tool_events",
                 "network_control", "known_limitations",
                 "certification_evidence")

CAPABILITY_TABLE = {
    "claude": {
        "pre_tool_hook": "yes: PreToolUse (docs/HOOKS.md; RUNTIMES.md "
            "row Claude Code, \"YES, this is the one verified runtime\")",
        "enforceable_deny": "yes, measured: this repo's own fence "
            "(bm_fence_hook.py) is the reference implementation every "
            "other host's deny is compared against",
        "workspace_isolation": "yes: git worktrees, single-writer fence "
            "(claim_store)",
        "resume": "yes: brother_run.py --resume / --continue, "
            "capsule.json (E73.1/E73.2)",
        "signed_in_headless": "yes: claude -p, model_worker.py's own "
            "default worker command",
        "structured_tool_events": "yes: Claude Code's own PreToolUse/"
            "PostToolUse JSON contract, the shape docs/HOOKS.md documents "
            "and every other adapter is translated into",
        "network_control": NODATA + ": not measured by this receipt",
        "known_limitations": "none named beyond the scoping sentence "
            "every receipt already carries (receipt_door.SCOPING_SENTENCE)",
        "certification_evidence": "products/brothermode/docs/RUNTIMES.md, "
            "capability table, row Claude Code",
    },
    "codex": {
        "pre_tool_hook": "yes: PreToolUse, measured 2026-08-05 on "
            "codex-cli 0.146.0 (RUNTIMES.md, OpenAI Codex CLI section)",
        "enforceable_deny": "yes, measured live on a probe payload "
            "(\"error=Command blocked by PreToolUse hook\"); the "
            "apply_patch matcher widened on 2026-08-06 (L06) but no live "
            "wired Codex session has rehearsed the deny since, so the "
            "end-to-end claim stays UNVERIFIED per RUNTIMES.md",
        "workspace_isolation": "partial: needs -s workspace-write; the "
            "default read-only sandbox refuses to create the store at all",
        "resume": NODATA + ": not measured for the Codex adapter",
        "signed_in_headless": "yes: codex exec, the caller's own OpenAI "
            "credentials (CODEX_HOME)",
        "structured_tool_events": "measured NO: file writes arrive as "
            "tool_name Bash running apply_patch, not Edit/Write "
            "(RUNTIMES.md, OpenAI Codex CLI section)",
        "network_control": NODATA + ": not measured by this receipt",
        "known_limitations": "${CLAUDE_PLUGIN_ROOT} expands empty; "
            "SessionEnd clamped to 3s; hooks silently inert until the "
            "project is trusted; 5 of 11 events never observed firing "
            "(RUNTIMES.md, OpenAI Codex CLI section)",
        "certification_evidence": "products/brothermode/docs/RUNTIMES.md, "
            "capability table, row OpenAI Codex CLI",
    },
    "cursor": {
        "pre_tool_hook": "yes, installable: preToolUse / "
            "beforeShellExecution (bm_cursor_hook.py); payload shape "
            "UNVERIFIED live (RUNTIMES.md, Cursor row)",
        "enforceable_deny": NODATA + ": admitted missing in "
            "CURSOR-COMPAT.md's own text, \"not an automated smoke "
            "result or a measured hook denial\"",
        "workspace_isolation": "yes: --workspace, worktrees supported by "
            "the cursor-mailbox harness",
        "resume": NODATA + ": not measured for the Cursor adapter",
        "signed_in_headless": "yes: cursor-agent -p --output-format json "
            "(scripts/model_worker.py's cursor-agent argv)",
        "structured_tool_events": NODATA + ": UNVERIFIED, payload shape "
            "not captured (RUNTIMES.md, Cursor row)",
        "network_control": NODATA + ": not measured by this receipt",
        "known_limitations": "CURSOR-COMPAT.md's own honest-limit "
            "section: hook denial is ADVISORY until a live signed-in "
            "canary is measured; use worktrees for isolation meanwhile",
        "certification_evidence": "docs/decisions/"
            "cursor-live-canary-2026-09-13.json (install/adapter "
            "confirmed real-machine; enforcement not)",
    },
}

#: The one file, per host, whose last commit date stands for this
#: receipt's certification_freshness: the same evidence
#: certification_evidence above names, read by `git log`, never typed by
#: hand (a hand-typed date goes stale the next time that file changes and
#: nobody remembers to bump it).
CERT_PATH_TABLE = {
    "claude": "products/brothermode/docs/RUNTIMES.md",
    "codex": "products/brothermode/docs/RUNTIMES.md",
    "cursor": "docs/decisions/cursor-live-canary-2026-09-13.json",
}

#: host -> the environment variable this repo has actually seen the host
#: export with something that looks like a version (measured on this
#: machine, 2026-09-14: CLAUDECODE carries no version; the desktop app
#: does). Codex and Cursor are read from their own CLI's --version instead
#: (VERSION_COMMAND below), since neither exports one.
_VERSION_ENV_VARS = {
    "claude": ("CLAUDE_CODE_DESKTOP_APP_VERSION", "CLAUDE_AGENT_SDK_VERSION"),
}

#: host -> [argv] for "print your own version and exit", tried only when
#: no env var above answered. Never the ONLY source: an unreachable binary
#: (this host running headless, no CLI on PATH) is NO-DATA, not a crash.
_VERSION_COMMAND = {
    "codex": ("codex", "--version"),
    "cursor": ("cursor-agent", "--version"),
}


def _host_version(host, env):
    """The host's own reported version string, or NODATA naming why not.
    Never invented: only a version the host itself printed (an env var it
    exported, or its own --version output) is used."""
    for var in _VERSION_ENV_VARS.get(host, ()):
        val = (env.get(var) or "").strip()
        if val:
            return val
    argv = _VERSION_COMMAND.get(host)
    if not argv:
        return NODATA + ": no version source known for host %r" % (host or "")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return NODATA + ": %s --version could not be run (%s)" % (argv[0],
                                                                   exc)
    text = (proc.stdout or proc.stderr or "").strip()
    return text.splitlines()[0] if text else (
        NODATA + ": %s --version printed nothing" % argv[0])


def _environment_summary(env):
    """One line naming whether this run is inside a coding session and,
    when it is, which one: the same two marker families brother_run.py's
    own in_claude_code_session() reads, spelled out here rather than
    reduced to a bool, so a receipt reader sees WHICH marker fired."""
    for var in brother_paths.CLAUDE_MARKER_VARS:
        if (env.get(var) or "").strip():
            return "interactive coding session (%s set)" % var
    for var in brother_paths.CODEX_MARKER_VARS:
        if (env.get(var) or "").strip():
            return "headless turn (%s set)" % var
    return "no session marker set: likely a plain shell or a worker " \
           "process started without one"


def _certification_freshness(host, repo=None):
    """The last commit date (ISO 8601) of the file CERT_PATH_TABLE names
    for `host`, or NODATA when the host is unknown, the file was never
    committed, or git could not be asked (mirrors receipt_door.
    public_remote_ref's own no-network, never-a-crash posture)."""
    path = CERT_PATH_TABLE.get(host)
    if not path:
        return NODATA + ": no certification evidence file known for " \
            "host %r" % (host or "")
    repo = repo or os.path.dirname(HERE)
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", path],
            cwd=repo, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return NODATA + ": git log could not be run (%s)" % exc
    date = (proc.stdout or "").strip()
    return date if (proc.returncode == 0 and date) else (
        NODATA + ": %s has no commit history in this checkout" % path)


def host_capability_receipt(run_dir=None, receipt_path=None, env=None,
                            repo=None):
    """The Host Capability Receipt: a dict carrying exactly
    RECEIPT_FIELDS. Always returns a full dict, whatever the host: an
    unrecognised client() answer (host is NODATA, or a client this table
    has no row for) fills every table-sourced field with NODATA naming
    that, rather than raising or silently omitting keys.

    Pure and safe to call from _write_receipt: every external read
    (an env var, a subprocess, git log) is wrapped so a missing binary or
    an unreadable checkout degrades a field to NODATA rather than taking
    the whole receipt down with it."""
    env = os.environ if env is None else env
    host = brother_paths.client(env) or NODATA
    row = {"host": host}
    row["host_version"] = _host_version(host, env)
    row["environment"] = _environment_summary(env)
    facts = CAPABILITY_TABLE.get(host, {})
    unknown_host_reason = (NODATA + ": host %r has no capability row in "
                           "this receipt's CAPABILITY_TABLE" % host)
    for field in _TABLE_FIELDS:
        row[field] = facts.get(field, unknown_host_reason)
    row["receipt_path"] = receipt_path or NODATA
    row["certification_freshness"] = _certification_freshness(host, repo)
    assert set(row) == set(RECEIPT_FIELDS), (set(row), set(RECEIPT_FIELDS))
    return row


def main(argv):
    import json
    print(json.dumps(host_capability_receipt(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
