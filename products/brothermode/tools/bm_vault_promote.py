#!/usr/bin/env python3
"""bm_vault_promote: the promotion counter WBS 9 asked for, adopted from TencentDB's
distillation trigger. A lockfile and a status/type check only keep notes CORRECT; this
is the piece that nudges toward FRESH, because distillation today is manual and this
stream already proved manual means sometimes skipped for days at a time.

  check   print how long it has been since the last distilled 40-Failures note (by
          session-log count and by raw-note count since that date), and whether either
          threshold is crossed. Exit 0 always: this NUDGES, it never blocks and it
          never writes a note itself, per the vault constitution's own append-only law
          ("supersede or append, never edit") and the founder's explicit boundary on
          this exact class of tool.

Python 3.9, standard library only, no network.
"""
import argparse
import json
import os
import re
import sys

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
SESSION_THRESHOLD_DEFAULT = 5
NOTE_THRESHOLD_DEFAULT = 8

DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
FRONT_CREATED = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)


def _vault_root(cli_vault):
    return cli_vault or DEFAULT_VAULT


def _read(path):
    """Explicit failure path, matching bm_vault_graph.py's own _load_notes: a file
    that cannot be read is skipped and named, never silently dropped, never a crash."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (IOError, OSError) as e:
        sys.stderr.write("bm_vault_promote: cannot read %s: %s\n" % (path, e))
        return None


def _frontmatter_created(body):
    if not body or not body.startswith("---"):
        return None
    end = body.find("\n---", 3)
    block = body[3:end] if end != -1 else ""
    m = FRONT_CREATED.search(block)
    return m.group(1) if m else None


def _last_distillation_date(vault_root):
    """The most recent created: date among real 40-Failures notes. Failures-Index.md
    and Failures-by-Symptom.md are routing pages, not distilled notes, and are excluded
    by name so a routine index refresh can never look like a fresh distillation."""
    failures_dir = os.path.join(vault_root, "40-Failures")
    if not os.path.isdir(failures_dir):
        return None
    dates = []
    for fn in os.listdir(failures_dir):
        if not fn.endswith(".md"):
            continue
        if fn in ("Failures-Index.md", "Failures-by-Symptom.md"):
            continue
        created = _frontmatter_created(_read(os.path.join(failures_dir, fn)))
        if created:
            dates.append(created)
    return max(dates) if dates else None


def _session_log_dates(vault_root):
    """Every session log's own date, read from its filename prefix (the recording
    contract's own naming rule: YYYY-MM-DD-<slug>-<topic>.md), not its frontmatter,
    since a session log is append-only and its created: date and filename date could
    drift if either were ever hand-edited; the filename is the one the constitution
    actually mandates."""
    dates = []
    projects_dir = os.path.join(vault_root, "10-Projects")
    if not os.path.isdir(projects_dir):
        return dates
    for slug in os.listdir(projects_dir):
        sessions_dir = os.path.join(projects_dir, slug, "Sessions")
        if not os.path.isdir(sessions_dir):
            continue
        for fn in os.listdir(sessions_dir):
            if not fn.endswith(".md"):
                continue
            m = DATE_PREFIX.match(fn)
            if m:
                dates.append(m.group(1))
    return dates


def _raw_note_dates(vault_root):
    """created: dates for every note under a project's Findings/ and Decisions/ folders
    plus every 40-Failures note (including the ones counted as distillations, since a
    note distilled today still counted as "raw" the moment before it existed): the
    population WBS 9's own wording, "N raw notes accumulated", names."""
    dates = []
    projects_dir = os.path.join(vault_root, "10-Projects")
    if os.path.isdir(projects_dir):
        for slug in os.listdir(projects_dir):
            for sub in ("Findings", "Decisions"):
                d = os.path.join(projects_dir, slug, sub)
                if not os.path.isdir(d):
                    continue
                for fn in os.listdir(d):
                    if fn.endswith(".md"):
                        created = _frontmatter_created(_read(os.path.join(d, fn)))
                        if created:
                            dates.append(created)
    failures_dir = os.path.join(vault_root, "40-Failures")
    if os.path.isdir(failures_dir):
        for fn in os.listdir(failures_dir):
            if fn.endswith(".md") and fn not in ("Failures-Index.md", "Failures-by-Symptom.md"):
                created = _frontmatter_created(_read(os.path.join(failures_dir, fn)))
                if created:
                    dates.append(created)
    return dates


def check(vault_root, session_threshold, note_threshold):
    last = _last_distillation_date(vault_root)
    sessions_since = 0
    notes_since = 0
    if last is None:
        # No distilled note exists at all yet: every session and every raw note found
        # counts toward the nudge, since there is nothing to measure "since".
        sessions_since = len(_session_log_dates(vault_root))
        notes_since = len(_raw_note_dates(vault_root))
    else:
        sessions_since = sum(1 for d in _session_log_dates(vault_root) if d > last)
        notes_since = sum(1 for d in _raw_note_dates(vault_root) if d > last)
    nudge = sessions_since >= session_threshold or notes_since >= note_threshold
    return {
        "last_distillation": last,
        "sessions_since": sessions_since,
        "session_threshold": session_threshold,
        "notes_since": notes_since,
        "note_threshold": note_threshold,
        "nudge": nudge,
    }


def cmd_check(args):
    vault = _vault_root(args.vault)
    if not os.path.isdir(vault):
        print("NO-DATA: no vault found at %s" % vault)
        return 3
    result = check(vault, args.session_threshold, args.note_threshold)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if result["last_distillation"] is None:
        print("last distillation: NEVER")
    else:
        print("last distillation: %s" % result["last_distillation"])
    print("sessions since: %d (threshold %d)" % (
        result["sessions_since"], result["session_threshold"]))
    print("raw notes since: %d (threshold %d)" % (
        result["notes_since"], result["note_threshold"]))
    if result["nudge"]:
        print("NUDGE: distillation is overdue by this counter's own threshold. This is "
              "advice, not a block; nothing here writes a note for you.")
    else:
        print("no nudge: both counters are under threshold")
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check", help="print the distillation nudge, never blocks")
    pc.add_argument("--vault", default=None)
    pc.add_argument("--session-threshold", type=int, default=SESSION_THRESHOLD_DEFAULT)
    pc.add_argument("--note-threshold", type=int, default=NOTE_THRESHOLD_DEFAULT)
    pc.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
