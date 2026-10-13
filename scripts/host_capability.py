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
import datetime
import hashlib
import json
import os
import re
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

#: Two limits the Claude Code and Codex rows share. The fence clause is
#: per mode and per path, read from products/brothermode/tools/
#: bm_fence_hook.py (decide, fence_mode, _ALLOW_EVEN_WHEN_ENFORCED): it is
#: never a blanket "fails open", and never a claim that a deny was seen live.
_NO_LIVE_PROOF = (
    "no signed-in end-to-end proof exists for a host version unless "
    "certification_evidence names a sealed live run for it; the live "
    "probe's guarded write expects no deny on this host "
    "(scripts/host_live_verify.py, EXPECTED_GUARDED_WRITE: unguarded)")
_FENCE_LIMITS = (
    "bm_fence_hook.py, by mode and path: it denies a write to a path "
    "another live record owns, and an apply_patch envelope whose file "
    "directives cannot be read; a project path no record covers is allowed "
    "unless BM_FENCE_STRICT is set; its own errors allow the write in the "
    "default advisory mode and deny it under BM_FENCE_MODE=enforced, except "
    "when no project root is found; a write outside the project root, or by "
    "a shell command other than apply_patch, is not checked")

CAPABILITY_TABLE = {
    "claude": {
        "pre_tool_hook": "yes, documented: PreToolUse (docs/HOOKS.md; "
            "RUNTIMES.md row Claude Code). The mechanism is documented and "
            "payload-tested; it is not a signed-in end-to-end test on this "
            "host version.",
        "enforceable_deny": NODATA + ": no deny is shown to block a write "
            "on this host version by anything this receipt reads (see "
            "certification_evidence). Documented, payload-tested mechanism: "
            "bm_fence_hook.py returns a PreToolUse deny when another live "
            "record owns the path (products/brothermode/tools/"
            "test_bm_fence_hook.py).",
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
        "known_limitations": _NO_LIVE_PROOF + "; " + _FENCE_LIMITS
            + "; hooks are not an operating system sandbox "
            "(docs/reference/safety-boundaries.md); plus the scoping "
            "sentence every receipt already carries "
            "(receipt_door.SCOPING_SENTENCE)",
        "certification_evidence": "documentation only, not a live test: "
            "products/brothermode/docs/RUNTIMES.md, capability table, row "
            "Claude Code",
    },
    "codex": {
        "pre_tool_hook": "yes, documented: PreToolUse, measured once on "
            "2026-08-05 on codex-cli 0.146.0 (RUNTIMES.md, OpenAI Codex CLI "
            "section); not a signed-in end-to-end test on this host "
            "version.",
        "enforceable_deny": NODATA + ": no deny is shown to block a write "
            "on this host version by anything this receipt reads (see "
            "certification_evidence). Documented mechanism: a probe payload "
            "was denied (\"error=Command blocked by PreToolUse hook\"); "
            "the apply_patch matcher widened on 2026-08-06 (L06); a codex "
            "exec run on 2026-08-07 wrote a claimed file and the PreToolUse "
            "hook never executed (RUNTIMES.md, OpenAI Codex CLI row; "
            "products/brothermode/docs/mistakes/M19-the-codex-fence-does-"
            "not-fire-in-exec-mode.md).",
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
            "(RUNTIMES.md, OpenAI Codex CLI section); " + _NO_LIVE_PROOF
            + "; " + _FENCE_LIMITS,
        "certification_evidence": "documentation only, not a live test: "
            "products/brothermode/docs/RUNTIMES.md, capability table, row "
            "OpenAI Codex CLI",
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


#: The live evidence scripts/host_live_proof.py --collect writes, read here
#: directly rather than by importing that module (it is not in
#: bundle/runtime, and the receipt is written on every run: one stat when the
#: file is absent, which it is on every release tree and installed plugin).
#: test_host_capability.py pins these three to host_live_proof's own.
EVIDENCE_REL = "docs/plan/evidence/HP1-host-live.jsonl"
MANIFEST_SUFFIX = ".sha256"
EVIDENCE_SCHEMA = "hp1.v1"
EVIDENCE_CAP = 8 * 1024 * 1024
#: The ISO UTC stamp the collector writes (host_live_verify.STAMP).
_STAMP = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$")


def _known(text):
    """The stripped text, or None when it is not a string, blank, or an
    unknown-version sentinel (NO-DATA..., no_data, unknown): a sentinel is
    the absence of a value and never matches another sentinel."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    low = text.lower().replace("-", "_")
    if low.startswith("no_data") or low == "unknown":
        return None
    return text


def _when(stamp):
    """A datetime for a collector stamp, or None: rows are ordered by time,
    never by text."""
    m = _STAMP.match(stamp) if isinstance(stamp, str) else None
    if not m:
        return None
    try:
        parts = [int(x) for x in m.groups()[:6]]
        micro = int((m.group(7) or "0").ljust(6, "0"))
        return datetime.datetime(*parts, microsecond=micro,
                                 tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _live_evidence(host, host_version, repo=None):
    """One sentence about sealed live evidence for this host at this exact
    version. NO-DATA, naming why, in every case but one: an exact sealed
    match, which is a pointer to scripts/host_live_verify.py and never a
    verdict. Never raises."""
    host, version = _known(host), _known(host_version)
    if host is None or version is None:
        return NODATA + (": this run's host or its version is unknown, so no "
                         "live evidence row can match it")
    if not isinstance(repo, str) or not repo:
        repo = os.path.dirname(HERE)
    path = os.path.join(repo, *EVIDENCE_REL.split("/"))
    if not os.path.isfile(path):
        return NODATA + ": no live end-to-end evidence is recorded (%s is " \
            "absent)" % EVIDENCE_REL
    try:
        with open(path, "rb") as fh:
            data = fh.read(EVIDENCE_CAP + 1)
        with open(path + MANIFEST_SUFFIX, "rb") as fh:
            seal = fh.read(256).decode("ascii", "replace").strip()
    except OSError as exc:
        return NODATA + ": %s or its seal cannot be read (%s)" % (
            EVIDENCE_REL, type(exc).__name__)
    if len(data) > EVIDENCE_CAP:
        return NODATA + ": %s is larger than %d bytes and is not read " \
            "here" % (EVIDENCE_REL, EVIDENCE_CAP)
    if seal != hashlib.sha256(data).hexdigest():
        return NODATA + ": %s does not match its seal, so its rows are not " \
            "trusted here" % EVIDENCE_REL
    rows = []
    for n, line in enumerate(data.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            row = None
        if not isinstance(row, dict):
            return NODATA + ": CORRUPT: line %d of %s is not a JSON " \
                "object" % (n, EVIDENCE_REL)
        rows.append(row)
    mine = [r for r in rows
            if r.get("schema") == EVIDENCE_SCHEMA and r.get("host") == host]
    if not mine:
        return NODATA + ": %s holds no row for %s" % (EVIDENCE_REL, host)
    match = []
    for r in mine:
        when = _when(r.get("recorded_at"))
        run_id = _known(r.get("run_id"))
        if _known(r.get("host_version")) == version and when and run_id:
            match.append((when, run_id, r["recorded_at"]))
    if not match:
        seen = sorted(set(repr(str(r.get("host_version", "")).strip())
                          for r in mine))
        return NODATA + ": %s holds rows for %s at %s, not for this run's " \
            "%r" % (EVIDENCE_REL, host, ", ".join(seen), version)
    when, run_id, stamp = max(match)
    count = sum(1 for m in match if m[1] == run_id)
    return ("recorded: %d sealed row(s) for %s %s, newest run %s at %s; this "
            "receipt does not judge them: python3 scripts/host_live_verify.py "
            "%s --root <checkout> prints GREEN, RED or NO-DATA"
            % (count, host, version, run_id, stamp, EVIDENCE_REL))


#: The three answers the public truth table gives per host (HG1). The states
#: come from scripts/host_live_verify.py truth_states; this file only prints.
HOST_STATES = ("certified", "advisory", NODATA)
TRUTH_CLOSING = (
    "certified means a denial of an out of scope write is on record for this "
    "exact build and host; advisory means hooks are bound and no denial is "
    "on record; NO-DATA means nothing was shown. Brother is not an operating "
    "system sandbox.")
#: Words that would claim more than an advisory or NO-DATA host has shown.
_OVERCLAIM = re.compile(r"enforced|sandbox\s+secure", re.IGNORECASE)


def render_truth_table(states, evidence=""):
    """The truth table text for {host: (state, reason)}: a header naming the
    evidence, one line per host (reason flattened to one line, cut to 100
    characters, "no reason recorded" when blank), and TRUTH_CLOSING. Pure. A
    state outside HOST_STATES, or a malformed mapping, raises ValueError:
    a caller bug, never printed. A host that is not certified never carries
    the word enforced: an overclaiming reason word is withheld."""
    if not isinstance(states, dict):
        raise ValueError("states must be a dict of host: (state, reason)")
    where = " ".join(evidence.split()) if isinstance(evidence, str) else ""
    lines = ["host truth table for %s: host, state, reason"
             % (_OVERCLAIM.sub("[withheld]", where) or NODATA)]
    for host, pair in states.items():
        if not isinstance(host, str) or host.split() != [host] \
                or not isinstance(pair, tuple) \
                or len(pair) != 2 or pair[0] not in HOST_STATES:
            raise ValueError("unrecognized host state %r: %r" % (host, pair))
        state, reason = pair
        reason = " ".join(reason.split()) if isinstance(reason, str) else ""
        if state != "certified":
            reason = _OVERCLAIM.sub("[withheld]", reason)
        lines.append("%-12s %-10s %s" % (
            host, state, reason[:100] or "no reason recorded"))
    lines.append(TRUTH_CLOSING)
    return "\n".join(lines) + "\n"


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
    if host in CAPABILITY_TABLE:
        row["certification_evidence"] += (
            ". Live end-to-end evidence: "
            + _live_evidence(host, row["host_version"], repo))
    row["receipt_path"] = receipt_path or NODATA
    fresh = _certification_freshness(host, repo)
    row["certification_freshness"] = fresh if fresh.startswith(NODATA) else (
        "documentation changed %s; not a live test" % fresh)
    assert set(row) == set(RECEIPT_FIELDS), (set(row), set(RECEIPT_FIELDS))
    return row


def main(argv):
    import json
    print(json.dumps(host_capability_receipt(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
