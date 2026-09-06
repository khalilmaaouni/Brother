#!/usr/bin/env python3
"""bm_vault_tiers: the severity-tiered write gate with quarantine. WBS VB10-01.

WHY THIS EXISTS. bm_vault_cli.py's `commit` gate used to run bm_vault_graph.py
check unscoped, over the WHOLE vault, on every commit: any broken link anywhere,
even on a note nobody touched, refused a commit that had nothing to do with it.
That blast radius is wrong for the same reason an unscoped `git commit` is wrong
(see scripts/bm_vault_precommit_hook.py's own docstring): the size of a gate's
refusal should match the size of the act it guards. A commit that only ever
touches note X should never be refused by a defect that already existed on
note Y before this session opened its editor.

THE TIER TABLE, one check class -> one base severity, declared once below
(TIER_TABLE). broken_link, schema_violation and missing_required_field are
ERROR-capable; staleness and rot stay WARN always, matching the report-only
contract bm_vault_graph.py's own _rot_scan and bm_vault_catalog.py's own
check already carry (detection only, never a delete or a block).

THE PROGRESSIVE RULE, the reason ERROR-capable is a ceiling and not a fixed
value: a check fires as ERROR only for a note NEW in the staged set (added to
the index by this commit, never seen by git before). The exact same defect on
a note that already existed downgrades to WARN, always, never blocking. This
is what fixes the blast-radius bug above: touching an unrelated file can
never again be swept into someone else's unrelated defect. THE TRADE, stated
plainly rather than left implied: NEW damage to an OLD note also downgrades
to WARN by this same rule, because "new in the staged set" asks only whether
git has ever seen the note before, never whether the diff hunk on it right
now introduced the defect. A one-line edit that snaps an existing note's only
outgoing link is, today, exactly as WARN-only as a link that was already
broken before this commit touched the file. That gap is accepted on purpose
for this WBS row (a per-hunk newness test is real work, and the blast-radius
fix above is the one this row exists to ship), not fixed. FUTURE WORK: a
diff-hunk-scoped newness test (does the added/changed hunk itself introduce
the broken link/frontmatter defect, via `git diff --cached -U0` on the note)
would close this gap without reopening the blast-radius bug; it is not
implemented here.

WARN never blocks. Every WARN finding is appended, one line per finding, to
the review queue at 99-System/telemetry/vault-review-queue.md (the same
99-System/telemetry/ home bm_autosave.py's own TEL_DIR already uses for
machine-written housekeeping data), naming the check class and the note, so
nothing is silently dropped, but no commit stalls on it. The queue file
itself is a path this gate must never gate: the whole 99-System/telemetry/
path class is excluded from every check below, reusing bm_vault_graph.py's
own TELEMETRY_PREFIX constant (the exact prefix its rot scan already exempts
from orphan-attachment findings) rather than a second literal that could
drift from it. Without that exclusion the gate would refuse its own first
commit of the queue file (staged as NEW, no frontmatter, ERROR-blocking
itself), so append_queue() also writes minimal valid frontmatter
(status/type) the first time it creates the file, belt and braces alongside
the path exclusion.

QUARANTINE. An ERROR refusal always OFFERS the divert, never performs it:
re-run with --quarantine to move the offending NEW note out of the staged set
(git reset -- path) and admit it through bm_vault_intake.py's own
`admit --restricted` door as a restricted candidate in 00-Inbox/quarantine/,
so the rest of the commit can land. This is a MOVE, never a loss: the note's
content is copied into the quarantined candidate FIRST, and only once that
copy is confirmed on disk does the original working-tree file get removed.
The removal exists so a later `git add -A` does not re-stage the untouched
original as a brand-new untracked file (which would otherwise reintroduce
the exact defect this divert just cleared). Never automatic without the
--quarantine flag.

Findings are computed by REUSE, never a second copy of a rule that could
drift from the original: bm_vault_graph.py's own _scoped_findings (broken
links, missing/bad status and type) is the source for the ERROR-capable
classes, and its own _measure()'s rot lists (already computed for `measure`)
are filtered down to the staged set for the WARN-only rot class. Staleness
reads bm_vault_catalog.py check's own "stale: <path>" lines the same way,
never re-implementing the freshness comparison.

Both bm_vault_graph.py and bm_vault_intake.py are loaded BY PATH (the
technique bm_vault_cli.py's own _load_by_path already uses), so the answer
never depends on the caller's sys.path.

Exit 0: nothing staged, or every finding was WARN-tier (queued, not blocking),
or a --quarantine divert cleared every ERROR. Exit 1: at least one ERROR
finding stands (or a --quarantine divert itself failed).

Python 3.9, standard library only. No em or en dashes anywhere in this file,
its comments, or its output.
"""
import argparse
import datetime
import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 1. THE ONE DECLARED TIER TABLE ------------------------------------
# check class -> base severity. ERROR is a CEILING: the progressive rule in
# classify() below only ever reaches it for a note new in the staged set.
# staleness and rot are WARN-only on purpose (see the module docstring).
TIER_TABLE = {
    "broken_link": "ERROR",
    "schema_violation": "ERROR",
    "missing_required_field": "ERROR",
    "staleness": "WARN",
    "rot": "WARN",
}

# bm_vault_graph.py / bm_vault_catalog.py finding "kind" -> the check class
# TIER_TABLE governs. An unlisted kind falls back to missing_required_field
# in classify(), the strictest class, so a future finding kind nobody taught
# this table about is never silently treated as harmless.
KIND_TO_CLASS = {
    "broken_link": "broken_link",
    "bad_status_value": "schema_violation",
    "bad_type_value": "schema_violation",
    "missing_status": "missing_required_field",
    "missing_type": "missing_required_field",
    "no_frontmatter": "missing_required_field",
    "empty_note": "rot",
    "whitespace_only_note": "rot",
    "orphan_attachment": "rot",
    "staleness": "staleness",
}

QUEUE_RELPATH = os.path.join("99-System", "telemetry", "vault-review-queue.md")
# Belt and braces alongside the TELEMETRY_PREFIX gate exclusion in run_gate():
# minimal valid frontmatter (status/type both in bm_vault_graph.py's own
# ALLOWED_STATUS/ALLOWED_TYPE), matching how the sibling telemetry notes
# under 99-System/telemetry/ already carry frontmatter and survive the vault
# gates on their own. Written once, only when append_queue() creates the file.
QUEUE_FRONTMATTER = "---\nstatus: standing\ntype: reference\n---\n\n"


def _load_by_path(name, path):
    """Same by-path import technique bm_vault_cli.py's own _load_by_path uses,
    so this file reuses bm_vault_graph.py and bm_vault_intake.py rather than
    re-deriving their logic."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_graph():
    return _load_by_path("bm_vault_graph", os.path.join(HERE, "bm_vault_graph.py"))


def classify(kind, is_new):
    """(severity, check_class) for one finding kind, given whether its note
    is new in the staged set. Pure: the whole progressive rule in one place."""
    cls = KIND_TO_CLASS.get(kind, "missing_required_field")
    base = TIER_TABLE.get(cls, "WARN")
    if base == "ERROR" and is_new:
        return "ERROR", cls
    return "WARN", cls


def _git_paths(vault, *args):
    """Vault-relative paths from one `git diff --cached` line-listing call.
    Best effort: any failure to read git reads as "nothing", which is the
    safe direction here (it can only ever WIDEN what downgrades to WARN,
    never invent a block git itself did not confirm)."""
    try:
        proc = subprocess.run(["git", "-C", vault, "diff", "--cached"] + list(args),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
    except OSError:
        return set()
    if proc.returncode != 0:
        return set()
    return {l for l in proc.stdout.splitlines() if l.strip()}


def staged_paths(vault):
    return _git_paths(vault, "--name-only")


def staged_new_paths(vault):
    """Paths added ("A") to the index right now: "new in the staged set" for
    the progressive rule. -M50 forces rename detection at git's own default
    50 percent similarity regardless of the caller's diff.renames config
    (some setups carry diff.renames=false), so a renamed pre-existing note
    reports as "R", never "A": without it, a note that only moved (or moved
    with a small edit) would misclassify as new and its defects would wrongly
    reach ERROR."""
    return _git_paths(vault, "--name-only", "--diff-filter=A", "-M50")


def _staleness_findings(vault, staged):
    """bm_vault_catalog.py check's own "stale: <path>" lines, filtered to the
    staged set. Best effort: catalog check failing to run means no staleness
    signal, never a crash of this gate."""
    catalog = os.path.join(HERE, "bm_vault_catalog.py")
    try:
        proc = subprocess.run([sys.executable, catalog, "check", "--vault", vault],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    findings = []
    for line in proc.stdout.splitlines():
        if line.startswith("stale: "):
            path = line[len("stale: "):].strip()
            if path in staged:
                findings.append({"kind": "staleness", "path": path, "detail": None})
    return findings


def append_queue(vault, warns):
    """One line per (finding, check_class) WARN pair, appended (never
    overwritten) to the review queue. Best effort: a write failure is spoken
    on stderr but never blocks the commit it only records, matching the
    "WARN never blocks" contract this whole module carries."""
    path = os.path.join(vault, QUEUE_RELPATH)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        is_new_file = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if is_new_file:
                f.write(QUEUE_FRONTMATTER)
            for finding, cls in warns:
                detail = (" %s" % finding["detail"]) if finding.get("detail") else ""
                f.write("%s WARN %s %s%s\n" % (
                    stamp, cls, finding.get("path") or "-", detail))
        return True
    except OSError as exc:
        sys.stderr.write("bm_vault_tiers: could not append the review queue (%s)\n" % exc)
        return False


_ADMITTED_RE = re.compile(r"^ADMITTED .* -> (\S+)  ")


def _admitted_relpath(stdout):
    """The quarantined candidate's own vault-relative path, parsed from
    bm_vault_intake.py's own "ADMITTED <src> -> <rel_note>  id=...  dirt=..."
    success line. None when the line is not found (best effort: the caller
    treats that as "cannot confirm the copy", never as "copy confirmed")."""
    for line in stdout.splitlines():
        m = _ADMITTED_RE.match(line)
        if m:
            return m.group(1)
    return None


def _quarantine(vault, relpath, source, by):
    """Moves ONE offending new note out of the staged set (git reset) and
    admits it into 00-Inbox/quarantine/ as a restricted candidate through
    bm_vault_intake.py's own admit --restricted door. Returns (ok, message);
    ok False means the divert itself did not complete and the caller must
    keep treating the finding as blocking rather than drop it.

    MINOR fix: admit --restricted COPIES the note's content into a new file
    under 00-Inbox/quarantine/; it never touches the original at `abspath`.
    Left alone, the original stays on disk as an untracked file that the
    next `git add -A` would re-stage as brand new, reintroducing the very
    defect this divert exists to clear. So once the quarantined copy is
    confirmed on disk (checked BEFORE anything is removed, never assumed
    from intake's exit code alone), the original working-tree file is
    removed: the content already lives on as the quarantined candidate, so
    this is a move, never a loss."""
    abspath = os.path.join(vault, relpath)
    intake = os.path.join(HERE, "bm_vault_intake.py")
    proc = subprocess.run(
        [sys.executable, intake, "admit", "--vault", vault, "--source", source,
         "--by", by, "--restricted", abspath],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        return False, ("bm_vault_tiers: could not quarantine %s (bm_vault_intake "
                        "admit exit %d): %s" % (relpath, proc.returncode,
                                                 (proc.stdout + proc.stderr).strip()))
    inbox_relpath = _admitted_relpath(proc.stdout)
    inbox_ok = inbox_relpath and os.path.exists(os.path.join(vault, inbox_relpath))
    if not inbox_ok:
        return False, ("bm_vault_tiers: bm_vault_intake admit reported success for "
                        "%s but no quarantined copy could be confirmed on disk; "
                        "refusing to touch the original: %s"
                        % (relpath, (proc.stdout + proc.stderr).strip()))
    reset = subprocess.run(["git", "-C", vault, "reset", "--", relpath],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    if reset.returncode != 0:
        return False, ("bm_vault_tiers: admitted %s into quarantine but could not "
                        "unstage it (git reset exit %d): %s" % (
                            relpath, reset.returncode, reset.stderr.strip()))
    try:
        os.remove(abspath)
    except OSError as exc:
        return False, ("bm_vault_tiers: quarantined %s to %s but could not remove "
                        "the original working-tree file (%s); it would be re-staged "
                        "by the next `git add -A`" % (relpath, inbox_relpath, exc))
    return True, ("bm_vault_tiers: quarantined %s -> %s (restricted candidate; "
                   "the quarantined copy was confirmed on disk before the original "
                   "was removed, a move not a loss)" % (relpath, inbox_relpath))


def run_gate(vault, quarantine=False, source="bm_vault_tiers", by=None):
    """The tiered gate over whatever is staged right now. Returns (rc, text):
    rc 0 on a clean gate, a WARN-only gate, or a quarantine divert that cleared
    every ERROR; rc 1 when an ERROR finding still stands. text is never empty
    when there is something to report and may be "" when there is nothing
    staged to gate."""
    by = by or os.environ.get("CLAUDE_SESSION_ID") or ("pid-%d" % os.getpid())
    graph = _load_graph()
    resolved = graph._vault_root(vault)
    staged_all = staged_paths(resolved)
    if not staged_all:
        return 0, "bm_vault_tiers: nothing staged to gate"
    # MAJOR fix (self-blocking bookkeeping): exclude the whole telemetry path
    # class from gating, by importing bm_vault_graph.py's own TELEMETRY_PREFIX
    # rather than a second literal (see the module docstring). Without this,
    # this gate's own append_queue() write target would ERROR-block the very
    # first commit that stages it.
    staged = {p for p in staged_all if not p.startswith(graph.TELEMETRY_PREFIX)}
    if not staged:
        return 0, "bm_vault_tiers: nothing staged to gate (only telemetry housekeeping)"
    notes = graph._load_notes(resolved)
    if not notes:
        return 0, "bm_vault_tiers: NO-DATA, no notes found under %s" % resolved
    stats = graph._measure(resolved, notes)
    new_paths = staged_new_paths(resolved)

    _violations, findings = graph._scoped_findings(notes, stats, staged)
    for kind, plural in (("empty_note", "empty_notes"),
                          ("whitespace_only_note", "whitespace_only_notes"),
                          ("orphan_attachment", "orphan_attachments")):
        findings.extend({"kind": kind, "path": p, "detail": None}
                         for p in stats[plural] if p in staged)
    findings.extend(_staleness_findings(resolved, staged))

    errors, warns = [], []
    for f in findings:
        severity, cls = classify(f["kind"], f.get("path") in new_paths)
        (errors if severity == "ERROR" else warns).append((f, cls))

    lines = []
    if warns:
        append_queue(resolved, warns)
        lines.append("bm_vault_tiers: %d finding(s) downgraded to WARN, queued at %s"
                      % (len(warns), QUEUE_RELPATH))

    if not errors:
        lines.append("bm_vault_tiers: gate clean, %d path(s) checked" % len(staged))
        return 0, "\n".join(lines)

    for f, cls in errors:
        detail = " %s" % f["detail"] if f.get("detail") else ""
        lines.append("ERROR (%s): %s%s" % (cls, f.get("path") or "-", detail))

    error_paths = sorted({f["path"] for f, _cls in errors if f.get("path")})
    if not quarantine:
        lines.append(
            "bm_vault_tiers: %d ERROR finding(s) on new note(s). Re-run with "
            "--quarantine to divert the offending note(s) into "
            "00-Inbox/quarantine/ and land the rest of the commit." % len(errors))
        return 1, "\n".join(lines)

    remaining = []
    for path in error_paths:
        ok, msg = _quarantine(resolved, path, source, by)
        lines.append(msg)
        if not ok:
            remaining.append(path)
    if remaining:
        lines.append("bm_vault_tiers: %d note(s) could not be quarantined; "
                      "commit still refused." % len(remaining))
        return 1, "\n".join(lines)
    lines.append("bm_vault_tiers: %d note(s) quarantined; commit may proceed."
                  % len(error_paths))
    return 0, "\n".join(lines)


def _build_parser():
    p = argparse.ArgumentParser(description="the severity-tiered write gate")
    p.add_argument("--vault", required=True)
    p.add_argument("--quarantine", action="store_true")
    p.add_argument("--source", default="bm_vault_tiers")
    p.add_argument("--by", default=None)
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    rc, text = run_gate(args.vault, quarantine=args.quarantine,
                         source=args.source, by=args.by)
    if text:
        print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
