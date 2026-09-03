#!/usr/bin/env python3
"""Approval concentration report (P13's buildable half, a MEASURE not a gate).

The complaint this answers, verbatim from
docs/handover/2026-08-16-complete/01-TEAM-ISSUES-AND-WHAT-IS-DONE.md: "the
signed approval trailer already records who approved each change, so count
approvers over a window and print the distribution, and when one person
approves most changes, print that name and the share." Build acceptance from
the same paragraph: three changes approved by one person print a
concentration line naming that person; no signed approvals print NO-DATA.

THE CONTRACT, same shape as tools/sbe_report.py's:
- This is the measure lever, never the gate lever. It must never fail, warn
  the build, or block: this tool ALWAYS exits 0, even when it cannot read
  git history at all or crashes internally on a bug of its own. A repository
  with no signed approvals in the window prints NO-DATA naming the window,
  never a nonzero exit and never silence.
- "Signed" means what tools/sbe_gate.py's approval gate already means: a
  commit whose `Approved-by:` trailer names an identity `sbe_checks.answered`
  accepts (not a placeholder like "TBD") AND whose `%G?` reads `G`, a
  signature this host actually verified. An Approved-by trailer with no
  verified signature is an unverified claim, not a signed approval, and is
  quietly excluded rather than warned about: that is the ordinary, expected
  shape of most commits, not a defect.
- A git log record this walk cannot use for a different reason (a line that
  does not match the expected shape, or a trailer that reduces to no usable
  identity) is a MALFORMED approval and is skipped with a named warning line,
  never silently dropped and never a crash.
- `git_log_records` is the only place this file shells out; `parse_records`
  is a pure function over that text, so a test can drive the parser directly
  with hand-built git log output without needing a real signed commit.

Usage: python3 tools/sbe_approval_concentration.py [target-dir] [--days N] [--out PATH]
target-dir defaults to the current directory. --days defaults to 30. Exit
code is always 0.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import answered

# %x1f (unit separator) between fields, %x1e (record separator) between the
# values of a single multi-valued trailer; neither appears in ordinary commit
# text, and this project's own git log callers already lean on %x1f for the
# same reason (src/brothersbe/decisions.py, src/brothersbe/status.py).
FIELD_SEP = "\x1f"
TRAILER_SEP = "\x1e"

GIT_LOG_FORMAT = (
    "%H" + FIELD_SEP + "%ai" + FIELD_SEP + "%G?" + FIELD_SEP +
    "%(trailers:key=Approved-by,valueonly,separator=" + TRAILER_SEP + ")"
)


def git_log_records(root, days):
    """(text, error). `text` is the raw `git log` stdout, one line per commit
    in the last `days` days, each line "sha\\x1fdate\\x1fsig\\x1ftrailer".
    `error` is a short reason string when git could not be read at all (not a
    repository, git missing, or a non-zero exit), in which case `text` is "".
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, "log", "--since=%d days ago" % days,
             "--format=" + GIT_LOG_FORMAT],
            capture_output=True, text=True, timeout=30)
    except OSError as e:
        return "", "git could not be run (%s)" % e
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        reason = detail[0] if detail else "git log exited %d" % proc.returncode
        return "", reason
    return proc.stdout, None


def parse_records(text):
    """(counts, warnings). `counts` maps a signed approver identity (the raw
    Approved-by trailer text) to how many commits in the window carry their
    signed approval. `warnings` names every record this walk could not use,
    in the order encountered, so a malformed one is reported once rather than
    dropped in silence."""
    counts = {}
    warnings = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD_SEP)
        if len(parts) != 4:
            warnings.append(
                "skipped a malformed git log record (%d field(s), wanted 4): %r"
                % (len(parts), line[:80]))
            continue
        sha, _date, sig, trailer_raw = parts
        trailer = trailer_raw.split(TRAILER_SEP)[0] if trailer_raw else ""
        if not trailer.strip():
            continue  # no Approved-by trailer here: not an approval, not a defect
        identity = answered(trailer)
        if identity is None:
            warnings.append(
                "skipped a malformed approval on commit %s: the Approved-by "
                "trailer names no usable identity (%r)"
                % (sha[:12], trailer.strip()))
            continue
        if sig != "G":
            continue  # an unverified claim is not a SIGNED approval; ordinary, not a defect
        counts[identity] = counts.get(identity, 0) + 1
    return counts, warnings


def render(root, days):
    """The full report as a list of lines. Never raises: a git failure or an
    empty window both render as text, never an exception."""
    lines = ["APPROVAL CONCENTRATION for %s" % root, "",
             "window: last %d day(s)" % days, ""]
    text, err = git_log_records(root, days)
    if err:
        lines.append("NO-DATA: could not read git history for the last %d "
                      "day(s) (%s)" % (days, err))
        return lines
    counts, warnings = parse_records(text)
    total = sum(counts.values())
    if total == 0:
        lines.append("NO-DATA: no signed approvals in the last %d day(s)" % days)
    else:
        lines.append("%d signed approval(s) from %d approver(s):"
                      % (total, len(counts)))
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        for identity, count in ranked:
            share = 100.0 * count / total
            lines.append("  %-40s %3d  (%.0f%%)" % (identity, count, share))
        top_identity, top_count = ranked[0]
        top_share = 100.0 * top_count / total
        if top_share > 50.0:
            lines.append("")
            lines.append(
                "CONCENTRATION: %s holds %d/%d (%.0f%%) of signed approvals "
                "in this window" % (top_identity, top_count, total, top_share))
    for w in warnings:
        lines.append("WARNING: %s" % w)
    return lines


def safe_render(root, days):
    """`render`, never raising: a bug in this tool is reported as an honest
    line, exactly as tools/sbe_report.py's own crash boundary does, because a
    measure that can fail the build over its own bug is a gate wearing a
    measure's name."""
    try:
        return render(root, days)
    except Exception as e:  # noqa: intentionally broad, see module docstring
        return ["APPROVAL CONCENTRATION for %s" % root, "",
                "NO-DATA: could not report because %s: %s"
                % (type(e).__name__, e)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default=os.getcwd(),
                    help="repository to report on (default: current directory)")
    ap.add_argument("--days", type=int, default=30,
                    help="the window size in days (default: 30)")
    ap.add_argument("--out", default=None,
                    help="also write the report here (best effort; a write "
                         "problem here is reported, never fails the build)")
    ns = ap.parse_args(argv)
    root = os.path.abspath(ns.target)
    days = ns.days if ns.days > 0 else 30

    lines = safe_render(root, days)
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)

    if ns.out:
        try:
            d = os.path.dirname(os.path.abspath(ns.out))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(ns.out, "w") as fh:
                fh.write(text)
        except OSError as e:
            sys.stderr.write("sbe-approval-concentration: could not write %s "
                              "(%s), report already printed above\n" % (ns.out, e))

    return 0


if __name__ == "__main__":
    sys.exit(main())
