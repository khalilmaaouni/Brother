#!/usr/bin/env python3
"""brother_wastage_census.py: a corrected with-Brother vs without-Brother census.

Built 2026-09-13 after Fable's adversarial review of an earlier, uncorrected
proposal found two real defects and this script exists to fix both rather
than repeat them:

  1. SUBAGENT FILES ARE NOT SESSIONS. A transcript under a session directory's
     own subagents/ subfolder (isSidechain: true) is not an independent
     top-level session; the earlier approach would have counted thousands of
     these as separate zero-cost "sessions", which pollutes every median. This
     script rolls a session's subagent transcripts into that session's own
     totals instead of counting them separately.

  2. BROTHER ACTIVITY IS A CONTENT SIGNAL, NOT A DIRECTORY NAME. 360 of 479
     project directories contain the substring "brother" purely because they
     are git worktree checkouts of the Brother repo itself; that proves
     nothing about whether a Brother skill fired in a given session. This
     script detects activity from the transcript's own content: a tool_use
     block naming the Skill tool with an input.skill value starting with
     "brother" (brothermode:*, brothersbe:*, or the bare "brother" router).

WHAT THIS DOES NOT FIX, stated plainly per the same review: Brother is
enabled MACHINE-WIDE in settings.json, so every post-install session pays its
fixed startup-floor rent whether or not a skill fires. Comparing "active" to
"no-evidence" sessions below is therefore a comparison of SESSIONS THAT USED
A BROTHER SKILL versus SESSIONS THAT DID NOT, both already paying the
machine-wide rent -- it is NOT a clean with-Brother-installed vs
without-Brother-installed causal comparison, because there is no unconfounded
control arm among post-install sessions. That bigger question needs a
controlled A/B (two virgin-HOME arms) or genuine pre-install session data;
neither happens here. This script answers a narrower, still useful question:
among sessions that already pay Brother's rent, does actually firing a
Brother skill correlate with more or less waste, and by how much.

Streaming, single pass, one line at a time: at 5.6GB across 4690 files this
must not load a whole file into memory to answer one boolean.

Python 3, standard library only.
"""
import argparse
import glob
import json
import os
import statistics
import sys
from collections import defaultdict

USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")


def is_subagent_path(path):
    """A transcript under its parent session's own subagents/ subfolder."""
    return os.sep + "subagents" + os.sep in path


def session_dir_and_id(path, root):
    """(project_dir, session_id) for a transcript path.

    A top-level session: <root>/<project>/<session_id>.jsonl
    A subagent transcript: <root>/<project>/<session_id>/subagents/agent-*.jsonl
    """
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    project = parts[0]
    if is_subagent_path(path):
        # parts: [project, session_id, 'subagents', 'agent-*.jsonl']
        session_id = parts[1]
    else:
        session_id = os.path.splitext(parts[-1])[0]
    return project, session_id


def scan_file(path):
    """(usage_totals dict, brother_active bool, calls int, dup_reads int,
    dup_bash int, problem_or_none, malformed_lines int). Streams the file
    line by line; a line that fails to parse as JSON is counted rather than
    dropped silently, since this tool's totals are a token-accounting
    census and an unreported skip would understate them without saying so."""
    totals = defaultdict(int)
    brother_active = False
    calls = 0
    malformed_lines = 0
    read_paths = []
    bash_cmds = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    malformed_lines += 1
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            calls += 1
                            name = block.get("name") or ""
                            inp = block.get("input") or {}
                            if name == "Skill":
                                skill = str(inp.get("skill") or "")
                                if skill.startswith("brother"):
                                    brother_active = True
                            elif name == "Read":
                                p = inp.get("file_path")
                                if p:
                                    read_paths.append(p)
                            elif name == "Bash":
                                c = inp.get("command")
                                if c:
                                    bash_cmds.append(c.strip())
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    for f in USAGE_FIELDS:
                        v = usage.get(f)
                        if isinstance(v, (int, float)):
                            totals[f] += v
    except OSError as exc:
        return totals, brother_active, calls, 0, 0, str(exc), malformed_lines
    dup_reads = len(read_paths) - len(set(read_paths))
    dup_bash = len(bash_cmds) - len(set(bash_cmds))
    return totals, brother_active, calls, dup_reads, dup_bash, None, malformed_lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many top-level sessions (0 = no limit)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    files = glob.glob(os.path.join(args.root, "**", "*.jsonl"), recursive=True)
    sessions = {}  # (project, session_id) -> aggregated dict
    skipped = 0
    malformed_total = 0

    for i, path in enumerate(files):
        project, sid = session_dir_and_id(path, args.root)
        key = (project, sid)
        totals, active, calls, dup_r, dup_b, problem, malformed = scan_file(path)
        malformed_total += malformed
        if problem:
            skipped += 1
            continue
        s = sessions.setdefault(key, {
            "usage": defaultdict(int), "brother_active": False,
            "calls": 0, "dup_reads": 0, "dup_bash": 0, "files": 0,
        })
        for f, v in totals.items():
            s["usage"][f] += v
        s["brother_active"] = s["brother_active"] or active
        s["calls"] += calls
        s["dup_reads"] += dup_r
        s["dup_bash"] += dup_b
        s["files"] += 1
        if args.limit and len(sessions) >= args.limit and (project, sid) not in sessions:
            break

    active_sessions = [s for s in sessions.values() if s["brother_active"]]
    inactive_sessions = [s for s in sessions.values() if not s["brother_active"]]

    def totals_input(s):
        return (s["usage"].get("input_tokens", 0)
                + s["usage"].get("cache_read_input_tokens", 0)
                + s["usage"].get("cache_creation_input_tokens", 0))

    def summarize(group, label):
        if not group:
            return {"label": label, "n_sessions": 0, "note": "NO-DATA: empty group"}
        inputs = [totals_input(s) for s in group]
        outputs = [s["usage"].get("output_tokens", 0) for s in group]
        calls = [s["calls"] for s in group]
        dup_reads = [s["dup_reads"] for s in group]
        dup_bash = [s["dup_bash"] for s in group]
        return {
            "label": label,
            "n_sessions": len(group),
            "median_input_tokens": statistics.median(inputs),
            "median_output_tokens": statistics.median(outputs),
            "median_calls": statistics.median(calls),
            "total_input_tokens": sum(inputs),
            "total_output_tokens": sum(outputs),
            "total_dup_reads": sum(dup_reads),
            "total_dup_bash": sum(dup_bash),
            "dup_reads_per_session_median": statistics.median(dup_reads),
            "dup_bash_per_session_median": statistics.median(dup_bash),
        }

    report = {
        "root": args.root,
        "files_scanned": len(files),
        "files_skipped_unreadable": skipped,
        "top_level_sessions_found": len(sessions),
        "malformed_lines_skipped": malformed_total,
        "note_on_methodology": (
            "brother_active means at least one Skill tool_use block in this "
            "session (including its own subagent transcripts) named a skill "
            "starting with 'brother'. This is a within-post-install "
            "comparison (active-skill-use vs no-skill-use), not a "
            "with-Brother-installed vs without-Brother-installed causal "
            "comparison, because Brother is enabled machine-wide."
        ),
        "brother_active": summarize(active_sessions, "brother_active"),
        "brother_inactive": summarize(inactive_sessions, "brother_inactive"),
    }
    out = args.out or os.path.expanduser(
        "~/.claude/evidence/token-strategy-2026-09-13/wastage-census.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps(report, indent=1))
    print("\nwritten to", out, file=sys.stderr)


if __name__ == "__main__":
    main()
