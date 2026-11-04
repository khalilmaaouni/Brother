#!/usr/bin/env python3
"""ruling_ledger: joins a founder ruling to whatever actually landed it. (M7)

WHY THIS EXISTS, measured 2026-09-07 (row M7 of the reflection). A founder
ruling was recorded in docs/decisions/attempt-hook-registration-2026-09-03.json
on 2026-09-03 at 16:52 JST. The hub's settings.json was edited the same
evening, and a roadmap row (learning_loop.priority n=2) read REGISTERED. What
actually happened: that same-day edit was quietly removed two days later
(commit 9a18c7375, 2026-09-05, moving the hook into the shipped plugin
instead) and the founder's chosen shape (project-scoped, in the hub's own
tracked settings) was not back on disk until PR 442 landed on 2026-09-06.
Nothing in this estate ever checked a ruling against a landing, so the gap
sat for three days while the roadmap kept reading a state that was no longer
true. This script is that check.

A "landing" is one of, in this priority order:
  1. the decision record's own "landing" field (a PR number or similar,
     written in by hand once someone has actually confirmed it),
  2. a roadmap row in READINESS-ROADMAP whose "role" or "evidence" field
     names the record's file name or its title, AND whose own status/state
     reads as done (DONE, CLOSED, MERGED, SHIPPED) -- a row that only
     *mentions* the record while still NOT BUILT is a citation, not a
     landing, and counting it as one is the exact flattery this estate's
     tick contract refuses everywhere else,
  3. a commit (found by grep -F over the whole history) whose message names
     the record's file name, other than the commit that added the record
     file itself (that commit always names it, trivially, and would make
     every ruling ever recorded read APPLIED the instant it was written).

RULINGS THAT ARE NOT RULINGS: a "ruling" field of null, "", or the literal
placeholder "PENDING" means nobody has decided yet. That is a different
problem (a stale open question) from an applied-then-lost decision, and
flagging it RULING UNAPPLIED would misname it. Only a real ruling (an option
letter or name, with the founder's words) is scored here.

Python 3, standard library only. No network.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISIONS_DIR = os.path.join(ROOT, "docs", "decisions")
ROADMAP_PATH = os.path.join(ROOT, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")

NODATA = "NO-DATA"
DONE_WORDS = ("DONE", "CLOSED", "MERGED", "SHIPPED")
JST = datetime.timezone(datetime.timedelta(hours=9))
NON_RULINGS = ("", "pending")


def is_real_ruling(ruling):
    """A ruling field counts only when it names an actual decision. null,
    empty, and the "PENDING" placeholder all mean nobody has ruled yet."""
    if ruling is None:
        return False
    return str(ruling).strip().lower() not in NON_RULINGS


_RULING_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[^0-9]{0,60}?(\d{1,2}):(\d)([\dx])\b")


def parse_ruling_timestamp(ruling_text):
    """Best-effort (JST) datetime out of a ruling sentence, or None.

    Rulings are written "at about 19:0x JST" or "at 20:37 JST": an 'x' in the
    second minute digit is the source's own way of saying "not exact past ten
    minutes", so it rounds down to 0 rather than being invented."""
    if not ruling_text:
        return None
    m = _RULING_TS_RE.search(str(ruling_text))
    if not m:
        return None
    date_s, hour_s, min1, min2 = m.groups()
    if min2 == "x":
        min2 = "0"
    try:
        y, mo, d = (int(x) for x in date_s.split("-"))
        return datetime.datetime(y, mo, d, int(hour_s), int(min1 + min2), tzinfo=JST)
    except ValueError:  # sbe: allow-silent a malformed ruling timestamp has no defensible time, so the caller falls through to git provenance
        return None


def _run_git(args, cwd):
    try:
        proc = subprocess.run(["git"] + list(args), cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def record_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def first_add_commits(path, repo_root):
    """(earliest_add_time_or_None, set_of_add_commit_hashes) for `path`,
    across every branch git knows about. The hashes are excluded from the
    commit-message landing search below, since the commit that first adds a
    decision record always names its own file name in the message."""
    rel = os.path.relpath(os.path.abspath(path), repo_root)
    out = _run_git(["log", "--all", "--diff-filter=A", "--format=%H %ci", "--", rel], repo_root)
    if not out:
        return None, set()
    times, hashes = [], set()
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        h, _, rest = line.partition(" ")
        hashes.add(h)
        try:
            times.append(datetime.datetime.strptime(rest.strip(), "%Y-%m-%d %H:%M:%S %z"))
        except ValueError:
            continue  # sbe: allow-silent malformed timestamp contributes no ordering evidence
    return (min(times) if times else None), hashes


def resolve_ruling_time(path, ruling, repo_root):
    """(datetime_or_None, basis_string)."""
    ts = parse_ruling_timestamp(ruling)
    if ts is not None:
        return ts, "the ruling's own stated time"
    added, _hashes = first_add_commits(path, repo_root)
    if added is not None:
        return added, "the record file's own first commit (no time in the ruling text)"
    return None, "unknown (no time in the ruling text, and git has no history for this file)"


def load_decision_records(decisions_dir):
    out = []
    for path in sorted(glob.glob(os.path.join(decisions_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((path, data))
    return out


def load_roadmap(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _iter_dicts(node):
    """Every dict anywhere in a JSON tree, depth-first."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def find_roadmap_landing(name, title, roadmap_doc):
    if not roadmap_doc:
        return None
    needles = [n for n in (name, title) if n]
    if not needles:
        return None
    for node in _iter_dicts(roadmap_doc):
        for field in ("role", "evidence"):
            val = node.get(field)
            if not isinstance(val, str):
                continue
            if not any(n in val for n in needles):
                continue
            status = str(node.get("status") or node.get("state") or "")
            if status and not any(w in status.upper() for w in DONE_WORDS):
                continue  # named there, but that row is not itself done: a mention, not a landing
            ident = node.get("id") or node.get("n") or node.get("title") or "?"
            return "roadmap row %s (%s field)" % (ident, field)
    return None


def find_commit_landing(name, repo_root, exclude_hashes):
    out = _run_git(["log", "--all", "--format=%H\x1f%s", "-F", "--grep=" + name], repo_root)
    if not out:
        return None
    for line in out.splitlines():
        if "\x1f" not in line:
            continue
        h, subject = line.split("\x1f", 1)
        if h in exclude_hashes:
            continue
        return "commit %s: %s" % (h[:9], subject.strip())
    return None


def find_landing(path, record, roadmap_doc, repo_root):
    landing = record.get("landing")
    if landing:
        return "recorded landing: %s" % landing
    name = record_name(path)
    hit = find_roadmap_landing(name, record.get("title"), roadmap_doc)
    if hit:
        return hit
    _added, add_hashes = first_add_commits(path, repo_root)
    return find_commit_landing(name, repo_root, add_hashes)


def evaluate(decisions_dir=None, roadmap_path=None, repo_root=None, max_age_hours=24.0, now=None):
    """(lines, summary, exit_code). Pure enough to test: every filesystem and
    git read happens once, up front, per record."""
    decisions_dir = decisions_dir or DECISIONS_DIR
    roadmap_path = roadmap_path or ROADMAP_PATH
    repo_root = repo_root or ROOT
    now = now or datetime.datetime.now(JST)

    records = load_decision_records(decisions_dir)
    if not records:
        return (["%s: no decision records found under %s" % (NODATA, decisions_dir)],
                {"nodata": True}, 0)

    roadmap_doc = load_roadmap(roadmap_path)

    lines = []
    applied = 0
    unapplied_items = []  # (name, age_hours_or_None)
    for path, record in records:
        ruling = record.get("ruling")
        if not is_real_ruling(ruling):
            continue
        name = record_name(path)
        landing = find_landing(path, record, roadmap_doc, repo_root)
        if landing:
            applied += 1
            lines.append("APPLIED  %s -- %s" % (name, landing))
            continue
        ruling_time, basis = resolve_ruling_time(path, ruling, repo_root)
        if ruling_time is None:
            unapplied_items.append((name, None))
            lines.append("RULING UNAPPLIED  %s -- age %s (%s)" % (name, NODATA, basis))
        else:
            age_hours = (now - ruling_time).total_seconds() / 3600.0
            unapplied_items.append((name, age_hours))
            lines.append("RULING UNAPPLIED  %s -- %.1f hours since the ruling (%s)"
                          % (name, age_hours, basis))

    stale = [(n, a) for n, a in unapplied_items if a is not None and a > max_age_hours]
    summary = {
        "applied": applied,
        "unapplied": len(unapplied_items),
        "unapplied_items": unapplied_items,
        "stale": stale,
    }
    return lines, summary, (1 if stale else 0)


def format_summary_line(summary):
    """The one line board_status prints, mirroring founder_queue_status_line's
    own NO-DATA-vs-0-vs-N shape so the two never disagree in style."""
    if summary.get("nodata"):
        return "Rulings: %s" % NODATA
    items = summary["unapplied_items"]
    if not items:
        return "Rulings: %d applied, %d unapplied" % (summary["applied"], summary["unapplied"])
    known = [(a, n) for n, a in items if a is not None]
    names = ", ".join(n for n, _ in items)
    if not known:
        return ("Rulings: %d applied, %d unapplied (oldest %s): %s"
                % (summary["applied"], summary["unapplied"], NODATA, names))
    age, _oldest = max(known, key=lambda pair: pair[0])
    return ("Rulings: %d applied, %d unapplied (oldest %.0f hours): %s"
            % (summary["applied"], summary["unapplied"], age, names))


def summary_line(decisions_dir=None, roadmap_path=None, repo_root=None, max_age_hours=24.0,
                  now=None):
    _lines, summary, _exit = evaluate(decisions_dir, roadmap_path, repo_root, max_age_hours, now)
    return format_summary_line(summary)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--decisions-dir", default=DECISIONS_DIR)
    ap.add_argument("--roadmap", default=ROADMAP_PATH)
    ap.add_argument("--repo-root", default=ROOT)
    ap.add_argument("--max-age-hours", type=float, default=24.0)
    args = ap.parse_args(argv)

    lines, summary, exit_code = evaluate(args.decisions_dir, args.roadmap, args.repo_root,
                                          args.max_age_hours)
    for line in lines:
        print(line)
    print("")
    print(format_summary_line(summary))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
