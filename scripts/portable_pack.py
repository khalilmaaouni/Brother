#!/usr/bin/env python3
"""Portable pack: R25.2's weekly half (docs/plan/READINESS-ROADMAP-2026-08-29.json,
row R25.2) plus R25.4, the cross-machine portability proof.

WHY THIS EXISTS. The founder's own words on R25: when a weekly account limit is
about to hit, the ceremony must emit "a Md file and zip and learning and WBS
html file so the person can continue in another account or PC." A limit that
strands an in-flight run on one account is the failure; this is the fix.

COMPOSITION, NEVER RE-IMPLEMENTATION. Every piece here is a call into a tool
that already exists:
  - handover_ceremony.collect_state()   the measurable close state (git HEAD
                                         and clean/dirty per repo, sbe tasks,
                                         day-plan ready set, open PRs)
  - handover_ceremony._load_lessons()   the same --lesson-file loader the
                                         ceremony itself uses
  - gen_readiness_board.load/counts/    the roadmap JSON, its DONE-vs-OPEN
    ready_rows/render/validate          truth, and its own WBS board renderer

This module only composes those into one directory, then one zip, per the
one-zip law: the zip holds every file it names; nothing it refers to is
delivered loose beside it.

Exit contract, matching this estate's own house style:
  0  the pack was written cleanly (build), or the pack verifies (--verify)
  1  --verify found the pack readable but wrong, naming what
  2  NO-DATA: a required input could not be read (build), or the pack itself
     could not be read at all (--verify) -- never presented as a pass

No em or en dashes anywhere.

origin: a human running this script's own CLI directly, `python3
scripts/portable_pack.py [--repo ... --out-dir ...]` (see main(), below,
which defaults --repo to this file's own ROOT when none is given). Nothing
else in this repo calls into portable_pack.py (verified: grep -rl
portable_pack scripts bundle/runtime finds only this module's own test,
test_portable_pack.py); it is this module that imports FROM
handover_ceremony.py and gen_readiness_board.py (see the `import
handover_ceremony as HC` / `import gen_readiness_board as GB` lines above),
never the reverse.

PRODUCER: this module is the sole producer of the zip and its members. The
build happens in build_pack(): the five members are written via the local
`_write()` helper (`with open(path, "w", encoding="utf-8") as f: f.write(
content)`, near the top of this file), then zipped at `with
zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf: ...
zf.write(os.path.join(work_dir, name), arcname=name)` inside build_pack(),
called from main().
"""

import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handover_ceremony as HC
import gen_readiness_board as GB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDOVER_DIR = os.path.expanduser("~/Documents/BrotherModeUp-handovers")

# The three public repositories, in the same set record_drift.py's
# KNOWN_REPOS and leaf_pin_check.py's LEAVES already use for "the estate".
# URLs only, deliberately: a clone command never carries a local path, so the
# mega prompt below can never leak one through this list.
PUBLIC_REPOS = (
    ("Brother", "https://github.com/khalilmaaouni/Brother.git"),
    ("BrotherModeUp", "https://github.com/khalilmaaouni/BrotherModeUp.git"),
    ("BrotherSBE", "https://github.com/khalilmaaouni/BrotherSBE.git"),
)

# Generic engineering doctrine only: no client name, no team member, no
# credential, nothing that fails the public-repo scan. This is what a fresh
# session on another account needs to behave the same way, in brief.
LAWS_DIGEST = (
    "Never write done, fixed, or works unless a command run after the last "
    "edit passed; quote it.",
    "NO-DATA is not a pass and not a fail: a check that could not run has "
    "not said the thing is broken.",
    "A row ticks only when its own done-check ran after the last edit and "
    "the output is quoted beside it.",
    "No em or en dashes, no attribution trailer naming any AI vendor, in "
    "any committed text.",
    "One claim per file, exclusive and expiring: never bypass a refused "
    "write, take unrelated work instead.",
    "Change only the lines the task requires; grep every call site before "
    "changing a shared function or type.",
)

EXPECTED_REQUIRED = ("01-START-HERE.md", "02-MEGA-PROMPT.md",
                     "03-BOARD.html", "05-STATE.json")
PATH_PATTERN = re.compile(r"(/Users/[\w./-]+|/home/[\w./-]+|~/[\w./-]+)")
ROWS_DONE_RE = re.compile(
    r'<span class="v">(\d+)/(\d+)</span><span class="l">rows done')
START_HERE_RE = re.compile(r"DONE:\s*(\d+)\s+OPEN:\s*(\d+)")


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def load_limit_state(path):
    """A small JSON object describing the limit that triggered this pack:
    class, reset_at, message. NO-DATA (raised) when it is not a JSON object,
    the same contract handover_ceremony._load_lessons uses for its list."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("%s is not a JSON object" % path)
    return data


def roadmap_counts(doc):
    """DONE-versus-OPEN truth, reusing gen_readiness_board's own counts()
    rather than re-deriving status classification a second time."""
    cnt = GB.counts(doc)
    total = sum(cnt.values())
    done = cnt.get("DONE", 0)
    return done, total - done, total


def render_board_html(doc, board_html_path=None):
    """The WBS board, gen_readiness_board's own render() over the SAME doc
    used for the counts above, so the two can never disagree. Falls back to
    the already-generated file on disk only when the roadmap fails its own
    validate() (never re-implements render()); raises, naming why, when
    neither is available."""
    if board_html_path:
        with open(board_html_path, encoding="utf-8") as f:
            return f.read()
    problems = GB.validate(doc)
    if not problems:
        return GB.render(doc)
    if os.path.isfile(GB.OUTPUT):
        with open(GB.OUTPUT, encoding="utf-8") as f:
            return f.read()
    raise ValueError("roadmap does not validate and no board html exists on "
                     "disk to fall back to: %s" % "; ".join(problems))


def build_start_here(state, lessons, limit_state, done, open_, total, ready):
    """Priority first: what was in flight, the limit state when given, then
    what to type first. Mirrors build_handover_markdown's own ordering."""
    lines = ["# START HERE (portable pack)", "",
             "Priority first; read top to bottom.", ""]

    lines.append("## Limit state")
    if limit_state:
        lines.append("- class: %s" % limit_state.get("class", "unknown"))
        lines.append("- reset at: %s" % limit_state.get("reset_at", "unknown"))
        if limit_state.get("message"):
            lines.append("- message: %s" % limit_state["message"])
    else:
        lines.append("- none recorded; this pack was not built from a "
                     "limit pause")
    lines.append("")

    lines.append("## What was in flight")
    dp = state.get("day_plan", {})
    if "error" in dp:
        lines.append("- %s" % dp["error"])
    else:
        lines.append("- IN-FLIGHT: %s"
                     % (", ".join(dp.get("in_flight") or []) or "none"))
        lines.append("- READY: %s"
                     % (", ".join(dp.get("ready") or []) or "none"))
    sbe = state.get("sbe_tasks", {})
    if "error" in sbe:
        lines.append("- %s" % sbe["error"])
    elif sbe.get("count"):
        for t in sbe["tasks"]:
            lines.append("- sbe task %s (owner %s)" % (t["id"], t["owner"]))
    lines.append("")

    lines.append("## Board position")
    lines.append("- DONE: %d   OPEN: %d   (of %d total rows)"
                 % (done, open_, total))
    lines.append("- ready now: %s" % (", ".join(ready) or "none"))
    lines.append("")

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

    lines.append("## Lessons captured this session")
    if lessons:
        for lesson in lessons:
            lines.append("- %s: %s" % (lesson.get("name", "?"),
                                       lesson.get("description", "")))
    else:
        lines.append("- none recorded")
    lines.append("")

    lines.append("## What to type first")
    lines.append("- Open 02-MEGA-PROMPT.md and paste its whole contents "
                 "into a fresh Claude Code session.")
    lines.append("")

    return "\n".join(lines) + "\n"


def build_mega_prompt(done, open_, total, ready):
    """A restart prompt for a fresh session on ANOTHER account or machine.
    Self-contained by construction: the only paths it names are clone URLs,
    and the board position is baked in as text so it needs no local state."""
    lines = ["# Mega prompt: paste this whole file into a fresh session", "",
             "You are resuming Brother estate work after a session, daily, "
             "or weekly limit pause. This prompt is self-contained: it "
             "names no local file, account, or machine.", ""]

    lines.append("## Clone the three public repositories")
    lines.append("")
    for _name, url in PUBLIC_REPOS:
        lines.append("git clone %s" % url)
    lines.append("")

    lines.append("## Board position, baked in, no local state needed")
    lines.append("")
    lines.append("Of %d roadmap rows: %d DONE, %d OPEN. Ready now: %s."
                 % (total, done, open_, ", ".join(ready) or "none"))
    lines.append("Source: docs/plan/READINESS-ROADMAP-2026-08-29.json in "
                 "the cloned Brother repository.")
    lines.append("")

    lines.append("## The laws, in brief")
    lines.append("")
    for law in LAWS_DIGEST:
        lines.append("- %s" % law)
    lines.append("")

    lines.append("## What to do next")
    lines.append("")
    lines.append("Open the roadmap JSON in the cloned Brother repository, "
                 "find the ready row with the highest priority, and "
                 "continue it. Run `sh scripts/check_all.sh` before "
                 "claiming anything done.")
    lines.append("")

    return "\n".join(lines) + "\n"


def build_pack(repos, lesson_file=None, limit_state_file=None,
               roadmap_path=None, board_html_path=None, out_dir=None,
               today=None, collect=None):
    """Composes the five members into a working directory, then one zip.
    Returns (zip_path, had_no_data): had_no_data is True when any collected
    piece was NO-DATA, mirroring handover_ceremony's own exit contract."""
    collect = collect or HC.collect_state
    state = collect(repos)

    lessons = []
    if lesson_file:
        lessons = HC._load_lessons(lesson_file)

    limit_state = None
    if limit_state_file:
        limit_state = load_limit_state(limit_state_file)

    doc = GB.load(roadmap_path)
    done, open_, total = roadmap_counts(doc)
    ready = GB.ready_rows(doc)
    html_text = render_board_html(doc, board_html_path)

    start_here = build_start_here(state, lessons, limit_state, done, open_,
                                  total, ready)
    mega = build_mega_prompt(done, open_, total, ready)

    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = out_dir or HANDOVER_DIR
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, "%s-portable-pack.zip" % today)

    with tempfile.TemporaryDirectory(prefix="portable-pack-") as work_dir:
        _write(os.path.join(work_dir, "01-START-HERE.md"), start_here)
        _write(os.path.join(work_dir, "02-MEGA-PROMPT.md"), mega)
        _write(os.path.join(work_dir, "03-BOARD.html"), html_text)
        if lessons:
            _write(os.path.join(work_dir, "04-LESSONS.json"),
                  json.dumps(lessons, indent=2, sort_keys=True))
        _write(os.path.join(work_dir, "05-STATE.json"),
              json.dumps(state, indent=2, sort_keys=True))

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(os.listdir(work_dir)):
                zf.write(os.path.join(work_dir, name), arcname=name)

    return zip_path, HC.state_has_error(state)


def verify_pack(zip_path):
    """Returns (verdict, problems): verdict is 'PASS', 'FAIL', or 'NO-DATA'.
    NO-DATA means the zip itself could not even be read; that is never
    presented as the content having been checked and found correct."""
    if not os.path.isfile(zip_path):
        return "NO-DATA", ["pack not found: %s" % zip_path]
    if not zipfile.is_zipfile(zip_path):
        return "NO-DATA", ["not a valid zip file: %s" % zip_path]

    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            return "FAIL", ["corrupted member (bad CRC): %s" % bad]

        names = set(zf.namelist())
        missing = [m for m in EXPECTED_REQUIRED if m not in names]
        if missing:
            return "FAIL", ["missing required member: %s" % m
                           for m in missing]

        mega = zf.read("02-MEGA-PROMPT.md").decode("utf-8")
        start = zf.read("01-START-HERE.md").decode("utf-8")
        html_text = zf.read("03-BOARD.html").decode("utf-8")
        state_raw = zf.read("05-STATE.json").decode("utf-8")

        if not html_text:
            problems.append("03-BOARD.html is empty")
        else:
            try:
                HTMLParser().feed(html_text)
            except Exception as e:  # noqa: BLE001, a malformed fragment names itself
                problems.append("03-BOARD.html does not parse as html: %s" % e)

        try:
            state = json.loads(state_raw)
        except ValueError as e:
            problems.append("05-STATE.json does not parse: %s" % e)
        else:
            repo_states = state.get("repos") or {}
            heads = [r.get("head") for r in repo_states.values()
                    if isinstance(r, dict) and r.get("head")]
            if not heads:
                problems.append("05-STATE.json carries no repo HEAD sha")

        if "04-LESSONS.json" in names:
            try:
                json.loads(zf.read("04-LESSONS.json").decode("utf-8"))
            except ValueError as e:
                problems.append("04-LESSONS.json does not parse: %s" % e)

        for line in mega.splitlines():
            if line.strip().startswith("git clone"):
                continue
            if PATH_PATTERN.search(line):
                problems.append("02-MEGA-PROMPT.md names a machine-local "
                               "path outside the clone commands: %r"
                               % line.strip())

        board_m = ROWS_DONE_RE.search(html_text)
        start_m = START_HERE_RE.search(start)
        if board_m and start_m:
            board_done, board_total = int(board_m.group(1)), int(board_m.group(2))
            start_done, start_open = int(start_m.group(1)), int(start_m.group(2))
            if board_done != start_done or (board_total - board_done) != start_open:
                problems.append("01-START-HERE.md DONE/OPEN counts do not "
                               "match 03-BOARD.html's own count")
        elif not board_m:
            problems.append("03-BOARD.html has no rows-done count to cross check")
        elif not start_m:
            problems.append("01-START-HERE.md has no DONE/OPEN line to cross check")

    return ("FAIL" if problems else "PASS"), problems


def main(argv):
    repos = []
    lesson_file = limit_state_file = roadmap_path = None
    board_path = out_dir = verify_path = today = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--repo" and i + 1 < len(argv):
            repos.append(argv[i + 1]); i += 1
        elif a == "--lesson-file" and i + 1 < len(argv):
            lesson_file = argv[i + 1]; i += 1
        elif a == "--limit-state" and i + 1 < len(argv):
            limit_state_file = argv[i + 1]; i += 1
        elif a == "--roadmap" and i + 1 < len(argv):
            roadmap_path = argv[i + 1]; i += 1
        elif a == "--board-html" and i + 1 < len(argv):
            board_path = argv[i + 1]; i += 1
        elif a == "--out-dir" and i + 1 < len(argv):
            out_dir = argv[i + 1]; i += 1
        elif a == "--date" and i + 1 < len(argv):
            today = argv[i + 1]; i += 1
        elif a == "--verify" and i + 1 < len(argv):
            verify_path = argv[i + 1]; i += 1
        i += 1

    if verify_path:
        verdict, problems = verify_pack(verify_path)
        for p in problems:
            print("portable-pack: %s: %s" % (verdict, p))
        if verdict == "PASS":
            print("portable-pack: PASS: %s verifies" % verify_path)
            return 0
        return 1 if verdict == "FAIL" else 2

    if not repos:
        repos = [ROOT]

    try:
        zip_path, had_no_data = build_pack(
            repos, lesson_file=lesson_file, limit_state_file=limit_state_file,
            roadmap_path=roadmap_path, board_html_path=board_path,
            out_dir=out_dir, today=today)
    except (OSError, ValueError) as e:
        print("portable-pack: NO-DATA: %s" % e)
        return 2

    print("portable-pack: wrote %s" % zip_path)
    return 2 if had_no_data else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
