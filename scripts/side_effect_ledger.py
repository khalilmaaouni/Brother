#!/usr/bin/env python3
"""side_effect_ledger: DOM-40.06, a receipt for what a diff cannot show.

THE DECIDING PROPERTY. A receipt separates REPOSITORY effects (a change a
diff already shows), EXTERNAL effects (a package installed, a network or
API call made, a process spawned, a database mutated, a file touched
outside the repository) and UNKNOWN effects (something the run could not
observe, or a kind nobody taught this module about). UNKNOWN is never
folded into "none": an agent that ran an uninstrumented subprocess has done
something a diff cannot show, and reporting silence as "no external
effects" would be exactly the fabrication this ledger exists to prevent.

WHY THE BUCKET IS NEVER TRUSTED FROM THE CALLER'S LABEL. For a filesystem
effect, the actual bucket is decided from the real, resolved path against
the repo root, never from the kind string the caller chose. An actor that
mislabels a write as "outside the repository" while it lands inside (or
the reverse) is exactly the case this module is built to catch; trusting
the label would make the receipt only as honest as the thing it audits.

OBSERVABILITY IS PART OF THE RECORD, not a footnote: every entry says
whether it was OBSERVED (this process saw the effect happen -- e.g. it
made the network call itself) or DECLARED (the actor reported it, but
nothing here independently confirmed it). A declared effect is weaker
evidence and summary() never presents the two as equal.

THE EMPTY-LEDGER DECISION, made here and not left implicit: zero entries
recorded is NOT treated as "zero effects occurred". It is treated as
UNKNOWN coverage, because a ledger nobody wrote to could equally mean the
run made no side effects, or that nothing in the run was instrumented at
all -- the two are indistinguishable from an empty file alone. summary()
therefore reports the unknown bucket's count honestly (0 when 0 entries
landed there) but only marks it "established_empty" (a positive claim that
there ARE no unknown effects) when the caller has appended a
COVERAGE_CONFIRMED_KIND entry, recording that something outside this
module actually checked coverage. Never true by default.

WHY A CORRUPT LEDGER IS NO-DATA, NEVER "CLEAN". A truncated last line is
exactly what a crash mid-write of a dropped external-effect entry looks
like. Silently skipping it and summarising the rest would be the same
fabrication as reporting zero effects for a ledger that never existed.
record() refuses to extend a ledger it cannot fully parse (see append()'s
docstring in run_journal_chain.py for the same reasoning); summary()
refuses to summarise one.

Append-only, atomic-write shape reused from scripts/run_journal_chain.py
(landed same day): tempfile.mkstemp beside the target, full content
rewritten, fsync, os.replace -- never an in-place open(path, "w"). This
ledger carries no hash chain (tamper-evidence is run_journal_chain's job,
not this one's); it exists only to bucket effects honestly.

Python 3, standard library only. No network, no real subprocess calls.
"""
import datetime
import json
import os
import tempfile

NODATA = "NO-DATA"

REPOSITORY = "repository"
EXTERNAL = "external"
UNKNOWN = "unknown"
BUCKETS = (REPOSITORY, EXTERNAL, UNKNOWN)

#: Kinds that are external by definition: none of them can be produced by a
#: normal repo-tracked commit, so a diff can never show them, and none of
#: them carries a path to double-check against the repo root.
EXTERNAL_KINDS = frozenset({
    "network", "api_call", "package_install", "process_spawned",
    "database_mutation",
})

#: The one filesystem kind. Its bucket is decided from the real path, never
#: from this label -- see _filesystem_bucket.
FILESYSTEM_KIND = "filesystem_outside_repo"

#: Sentinel a caller appends only after independently confirming full
#: instrumentation coverage for the run (e.g. every subprocess it spawned
#: had its own side effects checked). record() never appends this itself;
#: it is not an effect and is excluded from every bucket's count.
COVERAGE_CONFIRMED_KIND = "coverage_confirmed"

RECOGNISED_KINDS = EXTERNAL_KINDS | {FILESYSTEM_KIND, COVERAGE_CONFIRMED_KIND}


class LedgerError(Exception):
    """Raised by record() when the existing ledger cannot be safely
    extended (unreadable, or a line will not parse). Appending onto a
    ledger this process cannot read would make a pre-existing corruption
    look like it started at this append; refuse instead."""


def _filesystem_bucket(detail, repo_root):
    """(bucket, reason) for a filesystem entry, decided from the real path
    against repo_root -- never from the kind label the caller chose. No
    path in detail, or no repo_root given to check against: UNKNOWN, since
    this module could not itself confirm either side of the claim."""
    path = detail.get("path") if isinstance(detail, dict) else None
    if not path or not repo_root:
        return UNKNOWN, "no path/repo_root given: could not confirm location"
    real_path = os.path.realpath(os.path.expanduser(path))
    real_root = os.path.realpath(os.path.expanduser(repo_root))
    inside = real_path == real_root or real_path.startswith(real_root + os.sep)
    if inside:
        return REPOSITORY, "path resolves inside the repository"
    return EXTERNAL, "path resolves outside the repository"


def _bucket_for(kind, detail, repo_root):
    """(bucket, reason) for any recognised or unrecognised kind.
    COVERAGE_CONFIRMED_KIND is handled by the caller (record/summary), not
    here, since it is not an effect and has no bucket of its own."""
    if kind == FILESYSTEM_KIND:
        return _filesystem_bucket(detail, repo_root)
    if kind in EXTERNAL_KINDS:
        return EXTERNAL, "recognised external kind"
    return UNKNOWN, "unrecognised kind %r" % (kind,)


def _read_lines(path):
    """The ledger's lines as raw text (JSON not yet parsed), or None when
    the file exists but could not be opened/read -- NO-DATA, distinct from
    "no file yet" which is a legitimately empty ledger. Mirrors
    run_journal_chain._read_lines exactly, including trailing-newline
    handling."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _atomic_write(path, text):
    """Write `text` to `path` via tempfile.mkstemp beside it, fsync,
    os.replace -- never open(path, "w") in place. Mirrors
    run_journal_chain._atomic_write: the file at `path` is always either
    the complete old version or the complete new one."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".side_effect_ledger-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _parse_all(lines):
    """[dict, ...] parsed from `lines`, or raises ValueError naming the
    first line that will not parse -- one shared parse path so record()
    and summary() refuse a corrupt ledger the same way."""
    events = []
    for i, line in enumerate(lines):
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ValueError("line %d is not valid JSON (%s), a truncated "
                              "or otherwise corrupted write" % (i, exc))
        if not isinstance(event, dict) or "kind" not in event:
            raise ValueError("line %d is not a valid ledger entry" % i)
        events.append(event)
    return events


def record(path, kind, detail, observed, repo_root=None, timestamp=None):
    """Append one effect entry to the ledger at `path`, atomically.

    kind: one of RECOGNISED_KINDS, or anything else (bucketed UNKNOWN and
        the exact string kept, so an unrecognised kind is visible, not
        swallowed). detail: a dict describing what was touched (for
        FILESYSTEM_KIND, include "path" so the bucket can be verified).
    observed: True when this process itself saw the effect happen, False
        when it is the actor's own declaration only. Required, not
        defaulted: silently defaulting to "observed" would launder a
        declared effect into the stronger evidence class.

    Raises LedgerError rather than writing anything when the existing
    ledger cannot be read or fully parsed -- see LedgerError's docstring.
    """
    lines = _read_lines(path)
    if lines is None:
        raise LedgerError("%s: %s could not be read" % (NODATA, path))
    try:
        events = _parse_all(lines)
    except ValueError as exc:
        raise LedgerError("refusing to append to %s: %s; the existing "
                           "ledger is already broken" % (path, exc))
    ts = timestamp if timestamp is not None else (
        datetime.datetime.now(datetime.timezone.utc).isoformat())
    if kind == COVERAGE_CONFIRMED_KIND:
        bucket, reason = None, "coverage marker, not an effect"
    else:
        bucket, reason = _bucket_for(kind, detail, repo_root)
    entry = {
        "timestamp": ts, "kind": kind, "detail": detail,
        "evidence": "observed" if observed else "declared",
        "bucket": bucket, "reason": reason,
    }
    events.append(entry)
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    _atomic_write(path, text)
    return entry


def summary(path):
    """Bucket counts for the ledger at `path`.

    Returns one of:
      {"verdict": "clean", "count": N, "repository": {"count": R},
       "external": {"count": E},
       "unknown": {"count": U, "established_empty": bool}, "note": "..."}
      {"verdict": NO-DATA, "problem": "..."} -- unreadable or unparseable.
        Never summarised as "no external effects": see module docstring.

    "established_empty" under "unknown" is True only when a
    COVERAGE_CONFIRMED_KIND entry is present AND the unknown count is 0:
    a positive claim of full coverage, never assumed from silence.
    """
    lines = _read_lines(path)
    if lines is None:
        return {"verdict": NODATA, "problem": "%s could not be read" % path}
    try:
        events = _parse_all(lines)
    except ValueError as exc:
        return {"verdict": NODATA, "problem": str(exc)}
    counts = {REPOSITORY: 0, EXTERNAL: 0, UNKNOWN: 0}
    coverage_confirmed = False
    for i, event in enumerate(events):
        if event.get("kind") == COVERAGE_CONFIRMED_KIND:
            coverage_confirmed = True
            continue
        bucket = event.get("bucket")
        if bucket not in counts:
            return {"verdict": NODATA,
                    "problem": "line %d has an unrecognised bucket %r"
                               % (i, bucket)}
        counts[bucket] += 1
    established_empty = coverage_confirmed and counts[UNKNOWN] == 0
    note = ("zero entries recorded: not proof zero effects occurred, "
            "reported as unestablished unless coverage_confirmed"
            if not events else "")
    return {
        "verdict": "clean",
        "count": sum(counts.values()),
        "repository": {"count": counts[REPOSITORY]},
        "external": {"count": counts[EXTERNAL]},
        "unknown": {"count": counts[UNKNOWN],
                    "established_empty": established_empty},
        "note": note,
    }
