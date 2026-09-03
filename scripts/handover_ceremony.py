#!/usr/bin/env python3
"""Handover ceremony: collects a session's closing state and emits it as
vault-ready notes plus a human handover doc. Founder order 2026-08-29: the
handover ceremony becomes a FEATURE of /brother that automatically updates
the estate's wisdom (vault notes, learnings, mistakes) and is parsable and
searchable by the vault architecture.

Four independent actions, any combination in one invocation:

  --collect               print the measurable close state as JSON on
                           stdout: git HEAD/clean state per --repo PATH
                           (repeatable), open sbe tasks (id, owner, age),
                           the day-plan ready set (reusing
                           task_watchdog.py's own mirror of
                           gen_command_center.ready_state, imported rather
                           than re-derived), open pull requests (NO-DATA
                           when gh is not available, never an empty list
                           presented as none), and, when --limit-state
                           FILE names a limit_watch.py JSON output, that
                           file's classification under "limit_state"
                           (R25.1; NO-DATA when the file cannot be read,
                           simply absent when no path was passed).
  --emit-patterns          V4: reads --green-close-file PATH (a JSON list
                           of {name, problem, technique, receipt,
                           exit_code}) and writes one pattern_note.py note
                           per green close into --pattern-vault DIR, through
                           pattern_note.write() (which itself routes through
                           the same hard gate bm_vault_intake.py's `admit`
                           and `capture` run, never an ungated write). A
                           close missing a receipt or carrying a non-zero
                           exit_code writes NOTHING and is named in the
                           output, never silently dropped. Idempotent by
                           name, same as pattern_note.py itself: a second
                           run over the same green closes writes nothing.
  --emit-vault DIR         write one vault note per lesson in
                           --lesson-file PATH into DIR, each with valid
                           frontmatter (type, project, created, status,
                           tags, verified-by, and symptom for failures) in
                           the schema the vault's own pre-commit gate
                           (bm_vault_graph.py check) enforces. A lesson
                           whose status or type falls outside the
                           controlled vocabulary is REFUSED and never
                           written; every other lesson in the batch still
                           writes. No wikilinks are ever emitted, so the
                           broken-outgoing-link rule holds trivially.
  --emit-handover PATH     write the human START-HERE markdown: priority
                           first (uncommitted work, open sbe tasks, the
                           day-plan ready set, open pull requests), then
                           the lessons captured this session.

This tool only emits. It never pushes, never commits, never closes a task.
Exit 0 on a clean run, 1 when something was refused or flagged, 2 on
NO-DATA (a required input could not be read, never presented as a pass).
No em or en dashes anywhere.

PRODUCER: this module is the sole producer of its own outputs, with one
delegation. emit_vault_notes() (line 298) writes each vault note through
open(path, "w", encoding="utf-8") plus f.write(content) at line 313; main()
(line 454) writes the --emit-handover markdown through
open(emit_handover_path, "w", encoding="utf-8") plus f.write(markdown) at
line 534. emit_pattern_notes() (line 330) writes NOTHING itself: it
delegates every pattern note's actual write to pattern_note.write() (see
scripts/pattern_note.py), the sole producer of that file, so the vault's
one-writer-per-note-kind rule holds.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# Importable both from scripts/ and as scripts.handover_ceremony from the
# repository root, matching task_watchdog's own sys.path fix (its first
# live run caught the root-form import failing).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task_watchdog import (read_registry, age_hours, read_day_plan_rows,
                           ready_set_summary)
import pattern_note

ALLOWED_STATUS = {"open", "closed", "standing"}
ALLOWED_TYPE = {"failure", "finding", "decision", "session-log", "overview",
                "index", "reference"}
GH_FIELDS = "number,title,headRefName,url"


# ---------------------------------------------------------------------------
# Collection: each piece named NO-DATA on its own, never a false empty.
# ---------------------------------------------------------------------------

def _real_git_run(repo_path, args):
    try:
        out = subprocess.run(["git", "-C", repo_path] + args,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def repo_state(path, git_run=None):
    """git HEAD and clean/dirty for one repo root. NO-DATA when either git
    call fails (not a repo, git missing, or the call times out)."""
    git_run = git_run or _real_git_run
    head = git_run(path, ["rev-parse", "HEAD"])
    if head is None:
        return {"error": "NO-DATA: git rev-parse HEAD failed for %s" % path}
    status = git_run(path, ["status", "--porcelain"])
    if status is None:
        return {"error": "NO-DATA: git status failed for %s" % path}
    dirty = sorted(line[3:].strip() for line in status.splitlines()
                   if len(line) > 3)
    return {"head": head.strip(), "clean": len(dirty) == 0,
            "dirty_count": len(dirty), "dirty_paths": dirty[:30]}


def sbe_task_summary(now=None, registry_reader=None):
    """Open sbe tasks with id, owner (the agent field) and age in whole
    hours. NO-DATA when the registry cannot be read at all (distinct from
    a registry that reads clean with zero tasks, which is a real empty)."""
    registry_reader = registry_reader or read_registry
    tasks = registry_reader()
    if tasks is None:
        return {"error": "NO-DATA: the sbe task registry could not be read"}
    out = []
    for t in tasks:
        hours = age_hours(t.get("openedAt", ""), now)
        out.append({"id": t.get("id", "?"), "owner": t.get("agent", "?"),
                    "age_hours": int(hours) if hours is not None else None})
    return {"count": len(out), "tasks": out}


def day_plan_state(rows_reader=None):
    """The day-plan ready set, via task_watchdog's own ready_set_summary:
    the same rule gen_command_center.py implements, mirrored there and
    reused here rather than re-derived a third time. NO-DATA when
    LIVE-STATE.json or its day_plan.rows cannot be read."""
    rows_reader = rows_reader or read_day_plan_rows
    rows = rows_reader()
    if rows is None:
        return {"error": "NO-DATA: docs/plan/LIVE-STATE.json day_plan.rows "
                         "could not be read"}
    return ready_set_summary(rows)


def _gh_available():
    return shutil.which("gh") is not None


def _real_gh_list():
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--json", GH_FIELDS, "--limit", "50"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:  # sbe: allow-silent pr_state below turns None into an explicit NO-DATA
        return None


def pr_state(available=None, gh_list=None):
    """Open pull requests via gh. NO-DATA when gh is not on PATH at all or
    the call fails; a real zero-length list from a working gh call is a
    genuine empty, never collapsed into the same NO-DATA case."""
    available = available or _gh_available
    if not available():
        return {"error": "NO-DATA: gh is not available; open pull requests "
                         "were not checked"}
    gh_list = gh_list or _real_gh_list
    result = gh_list()
    if result is None:
        return {"error": "NO-DATA: gh pr list failed"}
    return {"count": len(result), "pull_requests": result}


def _real_limit_state_reader(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def limit_state(path, reader=None):
    """R25.1 wiring: the limit_watch.py classification JSON for the
    session that is closing. Only ever called when --limit-state passed a
    path; NO-DATA when that path could not be read or parsed, exactly
    like this file's other NO-DATA-safe collectors (never a false empty:
    a real limit_watch NORMAL/class dict is not this case)."""
    reader = reader or _real_limit_state_reader
    data = reader(path)
    if data is None:
        return {"error": "NO-DATA: limit state file %s could not be read" % path}
    return data


def collect_state(repos, git_run=None, registry_reader=None,
                   day_plan_reader=None, gh_available=None, gh_list=None,
                   now=None, limit_state_path=None, limit_state_reader=None):
    """The whole collector, pure over its injected seams so the tests can
    drive every NO-DATA path without a live subprocess. limit_state is
    only present when limit_state_path was passed: it is fed
    conditionally by design (R25.1), not a source this collector always
    attempts, so its absence is not itself a NO-DATA condition."""
    state = {
        "repos": {r: repo_state(r, git_run=git_run) for r in repos},
        "sbe_tasks": sbe_task_summary(now=now, registry_reader=registry_reader),
        "day_plan": day_plan_state(rows_reader=day_plan_reader),
        "pull_requests": pr_state(available=gh_available, gh_list=gh_list),
    }
    if limit_state_path:
        state["limit_state"] = limit_state(limit_state_path, reader=limit_state_reader)
    return state


def state_has_error(state):
    """True when any collected piece is NO-DATA, for the exit code."""
    for r in state.get("repos", {}).values():
        if isinstance(r, dict) and "error" in r:
            return True
    for key in ("sbe_tasks", "day_plan", "pull_requests", "limit_state"):
        if isinstance(state.get(key), dict) and "error" in state[key]:
            return True
    return False


# ---------------------------------------------------------------------------
# Vault note emission.
# ---------------------------------------------------------------------------

def _slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return s.strip("-")


def build_vault_note(lesson, project="brother", today=None):
    """One lesson to (filename, content). Raises ValueError, naming the
    reason, for anything the vault gate would refuse: no usable name, or a
    status/type outside the controlled vocabulary. Never emits a wikilink,
    so the broken-outgoing-link rule holds by construction."""
    name = (lesson.get("name") or "").strip()
    if not name:
        raise ValueError("lesson has no name")
    slug = _slugify(name)
    if not slug:
        raise ValueError(
            "lesson name %r has no usable characters for a filename" % name)

    status = (lesson.get("status") or "open").strip()
    if status not in ALLOWED_STATUS:
        raise ValueError("lesson %r has status %r, not in %s"
                         % (name, status, sorted(ALLOWED_STATUS)))

    note_type = (lesson.get("type") or "failure").strip()
    if note_type not in ALLOWED_TYPE:
        raise ValueError("lesson %r has type %r, not in %s"
                         % (name, note_type, sorted(ALLOWED_TYPE)))

    proj = (lesson.get("project") or project).strip()
    tags = lesson.get("tags") or ["handover", "ceremony"]
    created = (lesson.get("created") or today
              or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    verified_by = (lesson.get("verified_by")
                  or lesson.get("what_happened") or "").strip()
    symptom = (lesson.get("symptom") or "").strip()

    front = [
        "---",
        "type: %s" % note_type,
        "project: %s" % proj,
        "created: %s" % created,
        "status: %s" % status,
        "tags: [%s]" % ", ".join(tags),
        "verified-by: \"%s\"" % verified_by.replace('"', "'"),
    ]
    if note_type == "failure" and symptom:
        front.append("symptom: \"%s\"" % symptom.replace('"', "'"))
    front.append("---")

    body = ["", "# %s" % name, ""]
    description = (lesson.get("description") or "").strip()
    if description:
        body += [description, ""]
    for header, key in (("What happened", "what_happened"),
                        ("Why it matters", "why_it_matters"),
                        ("How to apply", "how_to_apply")):
        text = (lesson.get(key) or "").strip()
        if text:
            body += ["## %s" % header, "", text, ""]

    content = "\n".join(front + body).rstrip("\n") + "\n"
    return ("%s.md" % slug, content)


def emit_vault_notes(dir_path, lessons, project="brother", today=None):
    """Writes one file per lesson that passes build_vault_note; a refused
    lesson is named in the return, never written. Returns
    (written_paths, refused_reasons)."""
    os.makedirs(dir_path, exist_ok=True)
    written, refused = [], []
    for lesson in lessons:
        try:
            filename, content = build_vault_note(
                lesson, project=project, today=today)
        except ValueError as e:
            refused.append(str(e))
            continue
        path = os.path.join(dir_path, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(path)
    return written, refused


# ---------------------------------------------------------------------------
# Pattern note emission (V4): capture what worked on a green close.
# ---------------------------------------------------------------------------

def _green_close_ok(gc):
    """True when a green close's receipt evidences a real pass: a named
    receipt (path or command) AND exit_code exactly 0. Either missing means
    the close is not provably green, so no pattern note is written for it."""
    receipt = (gc.get("receipt") or "").strip()
    return bool(receipt) and gc.get("exit_code") == 0


def emit_pattern_notes(vault_dir, green_closes, project="brother",
                       pattern_write=pattern_note.write):
    """One pattern note per green close, written through pattern_write
    (pattern_note.write by default, which itself routes through the same
    hard gate bm_vault_intake.py's `admit` and `capture` run, and never
    overwrites an existing note by name). A close with no receipt, a
    non-zero exit_code, or a missing name/problem/technique writes NOTHING
    and is named in `skipped` with the reason, never silently dropped.
    Returns (written_paths, skipped_reasons)."""
    written, skipped = [], []
    for gc in green_closes:
        name = (gc.get("name") or "").strip()
        problem = (gc.get("problem") or "").strip()
        technique = (gc.get("technique") or "").strip()
        label = name or "(unnamed green close)"
        if not (name and problem and technique):
            skipped.append("%s: needs name, problem and technique" % label)
            continue
        if not _green_close_ok(gc):
            skipped.append(
                "%s: no usable receipt (needs a receipt and exit_code 0), "
                "got receipt=%r exit_code=%r"
                % (label, gc.get("receipt"), gc.get("exit_code")))
            continue
        receipt_str = "%s (exit %s)" % (gc["receipt"], gc["exit_code"])
        path, ok = pattern_write(
            name, problem, technique, "green close: %s" % receipt_str,
            project=project, receipt=receipt_str, vault=vault_dir)
        if ok:
            written.append(path)
        elif path is None:
            skipped.append("%s: refused by the vault gate or vault "
                           "unavailable (see stderr)" % label)
        # path is not None and not ok: already recorded by an earlier run,
        # which is the idempotence contract, never a failure to report.
    return written, skipped


# ---------------------------------------------------------------------------
# Human handover markdown.
# ---------------------------------------------------------------------------

def build_handover_markdown(state, lessons, project="brother"):
    """Priority first: uncommitted work, then open sbe tasks, then the
    day-plan ready set, then open pull requests, then the lessons captured
    this session."""
    lines = ["# START HERE", "",
             "Handover ceremony output for %s. Priority first; read top to "
             "bottom." % project, ""]

    lines.append("## Uncommitted work")
    dirty = [(p, r) for p, r in sorted(state.get("repos", {}).items())
             if isinstance(r, dict) and "error" not in r
             and not r.get("clean", True)]
    if dirty:
        for path, r in dirty:
            lines.append("- %s: %d dirty path(s) at HEAD %s"
                         % (path, r["dirty_count"], r["head"][:12]))
    else:
        lines.append("- none: every named repo is clean at HEAD")
    lines.append("")

    lines.append("## Open sbe tasks")
    sbe = state.get("sbe_tasks", {})
    if "error" in sbe:
        lines.append("- %s" % sbe["error"])
    elif sbe.get("count"):
        for t in sbe["tasks"]:
            age = ("%dh" % t["age_hours"]) if t["age_hours"] is not None \
                else "unknown age"
            lines.append("- %s (owner %s, open %s)" % (t["id"], t["owner"], age))
    else:
        lines.append("- none open")
    lines.append("")

    lines.append("## Day-plan ready set")
    dp = state.get("day_plan", {})
    if "error" in dp:
        lines.append("- %s" % dp["error"])
    else:
        lines.append("- READY: %s" % (", ".join(dp.get("ready") or []) or "none"))
        lines.append("- IN-FLIGHT: %s"
                     % (", ".join(dp.get("in_flight") or []) or "none"))
        ew = dp.get("event_wait") or []
        lines.append("- EVENT-WAIT: %s"
                     % (", ".join("%s (%s)" % (i, e) for i, e in ew) or "none"))
    lines.append("")

    lines.append("## Open pull requests")
    prs = state.get("pull_requests", {})
    if "error" in prs:
        lines.append("- %s" % prs["error"])
    elif prs.get("count"):
        for pr in prs["pull_requests"]:
            lines.append("- #%s %s (%s)"
                         % (pr.get("number"), pr.get("title"), pr.get("url")))
    else:
        lines.append("- none open")
    lines.append("")

    lines.append("## Lessons captured this session")
    if lessons:
        for lesson in lessons:
            lines.append("- %s: %s" % (lesson.get("name", "?"),
                                       lesson.get("description", "")))
    else:
        lines.append("- none recorded")
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI, manual argv parsing matching task_watchdog.py's own house style.
# ---------------------------------------------------------------------------

def _load_lessons(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("%s is not a JSON list" % path)
    return data


def main(argv):
    repos, lesson_file = [], None
    emit_vault_dir = emit_handover_path = None
    limit_state_path = None
    green_close_file = pattern_vault = None
    project = "brother"
    do_collect = do_emit_patterns = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--collect":
            do_collect = True
        elif arg == "--repo" and i + 1 < len(argv):
            repos.append(argv[i + 1]); i += 1
        elif arg == "--emit-vault" and i + 1 < len(argv):
            emit_vault_dir = argv[i + 1]; i += 1
        elif arg == "--emit-handover" and i + 1 < len(argv):
            emit_handover_path = argv[i + 1]; i += 1
        elif arg == "--lesson-file" and i + 1 < len(argv):
            lesson_file = argv[i + 1]; i += 1
        elif arg == "--project" and i + 1 < len(argv):
            project = argv[i + 1]; i += 1
        elif arg == "--limit-state" and i + 1 < len(argv):
            limit_state_path = argv[i + 1]; i += 1
        elif arg == "--emit-patterns":
            do_emit_patterns = True
        elif arg == "--green-close-file" and i + 1 < len(argv):
            green_close_file = argv[i + 1]; i += 1
        elif arg == "--pattern-vault" and i + 1 < len(argv):
            pattern_vault = argv[i + 1]; i += 1
        i += 1

    if not (do_collect or emit_vault_dir or emit_handover_path
            or do_emit_patterns):
        print("handover-ceremony: nothing to do; pass --collect, "
              "--emit-vault DIR, --emit-handover PATH, or --emit-patterns")
        return 2

    lessons = []
    if lesson_file:
        try:
            lessons = _load_lessons(lesson_file)
        except (OSError, ValueError) as e:
            print("handover-ceremony: NO-DATA: lesson file %s could not be "
                 "read: %s" % (lesson_file, e))
            return 2

    green_closes = []
    if green_close_file:
        try:
            green_closes = _load_lessons(green_close_file)
        except (OSError, ValueError) as e:
            print("handover-ceremony: NO-DATA: green-close file %s could "
                 "not be read: %s" % (green_close_file, e))
            return 2

    codes = []

    if do_collect:
        state = collect_state(repos, limit_state_path=limit_state_path)
        print(json.dumps(state, indent=2, sort_keys=True))
        codes.append(2 if state_has_error(state) else 0)

    if emit_vault_dir:
        written, refused = emit_vault_notes(
            emit_vault_dir, lessons, project=project)
        print("handover-ceremony: wrote %d vault note(s) to %s, %d refused"
             % (len(written), emit_vault_dir, len(refused)))
        for reason in refused:
            print("handover-ceremony: REFUSED: %s" % reason)
        codes.append(1 if refused else 0)

    if emit_handover_path:
        state = collect_state(repos, limit_state_path=limit_state_path)
        markdown = build_handover_markdown(state, lessons, project=project)
        parent = os.path.dirname(emit_handover_path)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(emit_handover_path, "w", encoding="utf-8") as f:
                f.write(markdown)
        except OSError as e:
            print("handover-ceremony: could not write %s: %s"
                 % (emit_handover_path, e))
            return 2
        print("handover-ceremony: wrote handover to %s" % emit_handover_path)
        codes.append(0)

    if do_emit_patterns:
        if not pattern_vault:
            print("handover-ceremony: NO-DATA: --emit-patterns needs "
                 "--pattern-vault DIR")
            codes.append(2)
        else:
            written, skipped = emit_pattern_notes(
                pattern_vault, green_closes, project=project)
            print("handover-ceremony: wrote %d pattern note(s) to %s, "
                 "%d skipped" % (len(written), pattern_vault, len(skipped)))
            for reason in skipped:
                print("handover-ceremony: SKIPPED: %s" % reason)
            codes.append(1 if skipped else 0)

    return max(codes) if codes else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
