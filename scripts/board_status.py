"""board_status: what is ACTUALLY done, counted from evidence.

FOUNDER ASK, 2026-08-29: "for each section mention what is actually done. For
example what is the status of F1 Mid-stream steering. Show it as a progress bar.
Make this a feature of /brother."

The question was fair and the board could not answer it. F1 carried a status of
SCHEDULED, no evidence, and zero decomposed subtasks, so nothing on the page said
whether it was untouched or nearly finished. A card with a name and no completion
signal is a card that has to be asked about, and being asked is the failure.

THE COUNTING RULE, which is this estate's tick contract and is why these numbers
are worth reading. A thing counts as DONE only when it says DONE **and carries
evidence**. A status of DONE with an empty evidence field is a CLAIM, and it is
counted separately and named, never folded into the percentage. Measured when
this was written: 0 such claims across 19 features and 24 rows, which is a real
result rather than a lucky one, and this module exists partly so that staying
true is checkable rather than remembered.

A SECTION WITH NOTHING COUNTABLE REPORTS NO-DATA, never 0 percent. Those two read
identically on a bar and mean opposite things: one is work not started, the other
is a section this tool does not understand.

Python 3, standard library only. No network.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

import journal
import receipt_door

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")

DONE_WORDS = ("DONE", "CLOSED", "MERGED", "SHIPPED")
FLIGHT_WORDS = ("IN-FLIGHT", "IN FLIGHT", "STARTED", "FIRST HALF DONE")
NODATA = "NO-DATA"

# ---------------------------------------------------------------------------
# THE VAULT COUNTER (WBS V12): "lessons recalled this week, receipts bound,
# notes written", read from the store and the vault, never typed. Three
# small readers, each over a real file this estate already writes, each
# paired with the command that would reproduce its own number.
#
# NO-DATA, not 0, when the SOURCE itself is missing (no audit file ever
# written, no runs directory, no vault at this path): that is "cannot
# check", never the same thing as "checked, and it is zero".
# ---------------------------------------------------------------------------

#: products/brothermode/tools/bm_vault_audit.py's own AUDIT_PATH: one JSON
#: line per recall (field "ts", ISO 8601), fixed at this path (bm_vault.py's
#: INDEX_PATH is not configurable by env or flag, so neither is this).
VAULT_AUDIT_PATH = os.path.expanduser("~/.claude/bm_vault_audit.jsonl")

#: scripts/brother_run.py's run_dir_for(): every run gets its own
#: docs/plan/runs/<stamp>-<slug>/ directory holding the Work document and
#: claims.json this reads.
RUNS_ROOT = os.path.join(ROOT, "docs", "plan", "runs")

#: bundle/runtime/brother-run's default_runs_root(): the shipped launcher
#: writes run state inside this dev checkout only when the checkout is a
#: writable git toplevel; an installed plugin has no such repo beside it, so
#: it falls back to this per-user state directory instead
#: (os.path.expanduser(os.path.join("~", ".claude", "brother-run")), copied
#: verbatim from that fallback line so this never drifts from it). A real
#: run made through the shipped runtime lands here, not under RUNS_ROOT.
USER_RUNS_ROOT = os.path.join(
    os.path.expanduser(os.path.join("~", ".claude", "brother-run")),
    "docs", "plan", "runs")

#: Same resolution every products/brothermode/tools/bm_vault_*.py module
#: uses (e.g. bm_vault_catalog.py's DEFAULT_VAULT): BROTHERMODE_VAULT, else
#: the founder's own vault.
VAULT_ROOT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser(
    "~/Documents/Kay Vault")

WEEK_DAYS = 7

_CREATED_RE = re.compile(r"(?m)^created:\s*(\d{4}-\d{2}-\d{2})")


def _parse_iso(s):
    """Same tolerance products/brothermode/tools/bm_vault_audit.py's own
    _parse_iso applies: a trailing Z (datetime.fromisoformat does not accept
    one before Python 3.11, and this estate is pinned to 3.9), and a naive
    value assumed UTC. Returns None rather than raising, so one malformed
    line degrades that line only, never the whole count."""
    if not isinstance(s, str) or not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:  # sbe: allow-silent malformed ts degrades only this row, never the week count, see the docstring above
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def lessons_recalled_this_week(audit_path=None, now=None):
    """(count_or_None, command, error_or_None). Entries in the recall access
    audit (one per real `bm_vault.py recall`, VB7-04) with a "ts" at or
    after seven days before `now`. The command shown is that module's own
    `search --since` (identical >= comparison), which a person can run by
    hand to check this number. `audit_path=None` resolves to
    VAULT_AUDIT_PATH AT CALL TIME (never bound into the default), so a
    caller can point it at a fixture without the module constant leaking
    through."""
    if audit_path is None:
        audit_path = VAULT_AUDIT_PATH
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=WEEK_DAYS)
    command = ("python3 products/brothermode/tools/bm_vault_audit.py search "
              "--since %s" % cutoff.isoformat())
    if not os.path.isfile(audit_path):
        return (None, command,
                "no access audit at %s (no recall has appended one yet)" % audit_path)
    count = 0
    try:
        with open(audit_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:  # sbe: allow-silent one bad audit line is skipped so it never blanks the week's count, see _parse_iso above
                    continue
                ts = _parse_iso(row.get("ts"))
                if ts is not None and ts >= cutoff:
                    count += 1
    except OSError as exc:
        return None, command, str(exc)
    return count, command, None


def receipts_bound(runs_root=None, from_journal=False):
    """(count_or_None, command, error_or_None). Every receipt
    scripts/receipt_door.py's own receipts_for() marks "verified" (a
    claim's evidence re-executed and exited 0), across every run directory
    scripts/brother_run.py's run_dir_for() has written under runs_root.
    Reuses receipt_door.receipts_for directly rather than re-deriving its
    rule, so this count can never drift from the one the delivery report
    itself prints.

    runs_root may be a single path (back compatible with every existing
    caller), a list/tuple of paths, or None. None means the real default:
    BOTH RUNS_ROOT (this checkout's own docs/plan/runs) and USER_RUNS_ROOT
    (where the shipped runtime actually writes for a real user, per
    bundle/runtime/brother-run's default_runs_root() fallback) are read, so
    a run made through the installed product is counted here too, not only
    a run made from inside this repository against itself. A run directory
    name seen in more than one root counts once. A root that exists but
    holds nothing verified yet is a real 0, never NO-DATA; only when NONE
    of the roots exist is this NO-DATA.

    E60, `from_journal=True`: fold <run_dir>/journal.jsonl instead of
    opening the run's Work document and claims.json. receipt_door.py's own
    receipts_for() already appends one "receipt.issued" event per CALL
    (never one per receipt, since brother_run.py calls it several times a
    run for the same final set -- see that append's own comment), carrying
    a "verified" count alongside "receipts" and "unproven"; this reads the
    LAST such event per run, which reflects the run's final receipt set the
    same way the file-reading branch above reads the record's final state.
    A run whose journal predates that field (E59's own journals, before
    E60 added "verified") or has no receipt.issued event at all (nothing
    integrated yet) contributes 0, exactly like a run this function's
    file-reading branch would find nothing verified in -- never an error,
    since one old or empty run must not blank the whole count."""
    if runs_root is None:
        roots = [RUNS_ROOT, USER_RUNS_ROOT]
    elif isinstance(runs_root, (list, tuple)):
        roots = list(runs_root)
    else:
        roots = [runs_root]
    existing = [r for r in roots if os.path.isdir(r)]
    command = ("python3 scripts/board_status.py --vault-counters "
               "(%d/%d run root(s) present: %s)"
               % (len(existing), len(roots), ", ".join(existing) or "none"))
    if not existing:
        return None, command, ("no such directory in any of %d root(s): %s"
                               % (len(roots), ", ".join(roots)))
    count = 0
    seen = set()
    for runs_dir in existing:
        for name in sorted(os.listdir(runs_dir)):
            if name in seen:
                continue
            seen.add(name)
            run_dir = os.path.join(runs_dir, name)
            if not os.path.isdir(run_dir):
                continue
            if from_journal:
                events = journal.read(run_dir)
                if not events:
                    continue
                last = None
                for event in events:
                    if event.get("type") == "receipt.issued":
                        last = event
                if last is not None:
                    count += int((last.get("payload") or {}).get(
                        "verified") or 0)
                continue
            wfiles = sorted(glob.glob(os.path.join(run_dir, "W-*.json")))
            if not wfiles:
                continue
            try:
                with open(wfiles[0], encoding="utf-8") as fh:
                    record = json.load(fh)
            except (OSError, ValueError):
                continue
            claims = {}
            claims_path = os.path.join(run_dir, "claims.json")
            if os.path.isfile(claims_path):
                try:
                    with open(claims_path, encoding="utf-8") as fh:
                        claims = json.load(fh)
                except (OSError, ValueError):
                    claims = {}
            for receipt in receipt_door.receipts_for(record, claims, [], None):
                if receipt["state"] == "verified":
                    count += 1
    return count, command, None


def notes_written_this_week(vault_root=None, now=None):
    """(count_or_None, command, error_or_None). Vault notes (*.md) whose
    YAML frontmatter `created:` date falls within the last WEEK_DAYS days,
    walked over vault_root the same way every products/brothermode/tools/
    bm_vault_*.py module resolves it. A note with no `created:` line is
    skipped, not counted and not an error: most vault files are not
    notes."""
    if vault_root is None:
        vault_root = VAULT_ROOT
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff_date = (now - datetime.timedelta(days=WEEK_DAYS)).date()
    command = "python3 scripts/board_status.py --vault-counters"
    if not os.path.isdir(vault_root):
        return None, command, "no such directory: %s" % vault_root
    count = 0
    for root, dirs, names in os.walk(vault_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(2000)
            except OSError:  # sbe: allow-silent an unreadable vault file is skipped so it never blanks the weekly notes count
                continue
            m = _CREATED_RE.search(head)
            if not m:
                continue
            try:
                d = datetime.date(*(int(x) for x in m.group(1).split("-")))
            except ValueError:  # sbe: allow-silent a malformed created date is skipped so it never blanks the weekly notes count
                continue
            if d >= cutoff_date:
                count += 1
    return count, command, None


def vault_counters(now=None, audit_path=None, runs_root=None, vault_root=None):
    """The three counts, in board order, each a dict with label, count
    (None on NO-DATA), command, and error (None unless count is None)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    count, command, err = lessons_recalled_this_week(audit_path, now)
    out.append({"label": "lessons recalled this week", "count": count,
               "command": command, "error": err})
    count, command, err = receipts_bound(runs_root)
    out.append({"label": "receipts bound", "count": count,
               "command": command, "error": err})
    count, command, err = notes_written_this_week(vault_root, now)
    out.append({"label": "notes written this week", "count": count,
               "command": command, "error": err})
    return out


def _print_vault_counters(counters):
    for c in counters:
        if c["count"] is None:
            print("%s: %s, %s (%s)"
                 % (c["label"].capitalize(), NODATA, c["error"], c["command"]))
        else:
            print("%s: %d (%s)" % (c["label"].capitalize(), c["count"], c["command"]))


def has_evidence(item):
    return bool(str(item.get("evidence") or "").strip())


def classify(item):
    """'done', 'claimed', 'in_flight', or 'open'.

    'claimed' is the important one: a DONE with no evidence. Folding it into
    done is how a board starts flattering itself, and the whole point of a
    progress bar somebody trusts is that it cannot."""
    st = str(item.get("status") or "").upper().strip()
    if any(w in st for w in DONE_WORDS):
        return "done" if has_evidence(item) else "claimed"
    if any(w in st for w in FLIGHT_WORDS):
        return "in_flight"
    return "open"


def tally(items):
    """(counts, total). Pure, so a caller can count anything shaped like this."""
    counts = {"done": 0, "claimed": 0, "in_flight": 0, "open": 0}
    for it in items or []:
        counts[classify(it)] += 1
    return counts, sum(counts.values())


def percent(counts, total):
    """Evidenced completion only. A claim is not progress."""
    if not total:
        return None
    return 100.0 * counts["done"] / total


def sections(doc):
    """Every countable part of the board, in reading order.

    Deliberately explicit rather than discovered: a section this tool has never
    been taught about must report NO-DATA rather than being silently omitted,
    and silent omission is what a discovery loop does."""
    out = []
    for key, label in (("features", "Features"),
                       ("rows", "Readiness rows"),
                       ("gates", "Gates")):
        items = doc.get(key) or []
        counts, total = tally(items)
        out.append({"key": key, "label": label, "counts": counts,
                    "total": total, "percent": percent(counts, total)})
    # THE TEAM'S ASKS. Counted here so `/brother progress` reports them beside
    # everything else, because they went unreported for two weeks by being in a
    # section nothing counted and nothing rendered.
    roll = ((doc.get("team_complaints") or {}).get("rollup_2026_08_29") or {})
    counted = roll.get("counted") or {}
    if counted:
        tot = sum(c.get("total", 0) for c in counted.values())
        done = sum(c.get("addressed", 0) for c in counted.values())
        part = sum(c.get("partial", 0) for c in counted.values())
        out.append({"key": "team_complaints", "label": "The team's asks",
                    "counts": {"done": done, "claimed": 0, "in_flight": part,
                               "open": tot - done - part},
                    "total": tot,
                    "percent": (100.0 * done / tot) if tot else None})

    ll = doc.get("learning_loop") or {}
    if ll:
        pri = ll.get("priority") or []
        counts, total = tally([{"status": p.get("state"),
                                "evidence": ("" if "NOT BUILT" in
                                             str(p.get("done_check", "")) else "x")}
                               for p in pri])
        out.append({"key": "learning_loop", "label": "The learning loop",
                    "counts": counts, "total": total,
                    "percent": percent(counts, total)})
    return out


def item_status(item):
    """One line a person can read, for one card. The answer to 'what is F1'."""
    kind = classify(item)
    subs = item.get("subtasks") or []
    scounts, stotal = tally(subs)
    if kind == "done":
        return "DONE", "closed, with evidence quoted on the card"
    if kind == "claimed":
        return "CLAIMED", ("says DONE but carries no evidence, so it is counted "
                           "as a claim and not as progress")
    if kind == "in_flight":
        return "IN FLIGHT", ("started, %d of %d subtask(s) evidenced"
                             % (scounts["done"], stotal) if stotal else "started")
    if stotal:
        return "OPEN", ("not started, %d of %d subtask(s) evidenced"
                        % (scounts["done"], stotal))
    return "OPEN", ("not started, and nothing is decomposed under it, so there "
                    "is no finer signal than this")


def bar(pct, width=28):
    if pct is None:
        return "[%s] %s" % ("?" * width, NODATA)
    filled = int(round(width * pct / 100.0))
    return "[%s%s] %3.0f%%" % ("#" * filled, "." * (width - filled), pct)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--item", help="one id, e.g. F1")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--vault-counters", action="store_true",
                    help="print only the three vault-counter lines (WBS V12)")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.vault_counters:
        _print_vault_counters(vault_counters())
        return 0

    try:
        with open(args.source, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: could not read %s: %s" % (NODATA, args.source, exc),
              file=sys.stderr)
        return 2

    if args.item:
        pool = (doc.get("features") or []) + (doc.get("rows") or [])
        found = [x for x in pool if str(x.get("id")) == args.item]
        if not found:
            print("%s: no item %r on this board" % (NODATA, args.item),
                  file=sys.stderr)
            return 2
        it = found[0]
        state, why = item_status(it)
        print("%s  %s" % (it.get("id"), it.get("name") or it.get("title")))
        print("  %-10s %s" % (state, why))
        print("  done-check: %s" % str(it.get("done_check", NODATA))[:200])
        return 0

    secs = sections(doc)
    if args.json:
        print(json.dumps(secs, indent=2, sort_keys=True))
        return 0

    claims = 0
    for s in secs:
        c = s["counts"]
        print("%-20s %s   %d done, %d in flight, %d open"
              % (s["label"], bar(s["percent"]), c["done"], c["in_flight"], c["open"]))
        claims += c["claimed"]
    print("")
    rc = 0
    if claims:
        print("%d item(s) say DONE and carry no evidence. They are NOT in the "
              "percentages above, because a claim is not progress." % claims,
              file=sys.stderr)
        rc = 1
    else:
        print("Every percentage above counts items that say DONE and carry evidence. "
              "Nothing on this board claims done without it.")
    print("")
    _print_vault_counters(vault_counters())
    return rc


if __name__ == "__main__":
    sys.exit(main())
