#!/usr/bin/env python3
"""JEV-02 of the 1.0.20 orchestration control plane: the calibration ledger.

WHY THIS EXISTS. TypeSafe (the vendor behind Jev, the decision model this
estate is adding) publishes no independent, reproduced calibration study:
its own docs state the calibration claim as a definition ("a probability of
0.8 should be right about 80 percent of the time, over many predictions"),
not a measured number, and the sourced research this unit was built from
(docs/plan research notes, not repeated here) found no third party who has
verified it either. So trust in a Jev probability or confidence number is
never assumed here: it is MEASURED, from this estate's own real decisions
joined to their own real outcomes, before any caller is allowed to treat a
confidence number as a license to act automatically. JEV-03 (the cascade)
is the caller; this module is where the measurement lives.

THE LEDGER is two append-only, line-delimited JSON files: one line of
"decision" (a question was answered, with its confidence) written when the
answer arrives, one line of "outcome" (was the answer actually right)
written later, sometimes much later, sometimes never, sometimes twice by
accident. Append-only and line-delimited on purpose: a crash can corrupt at
worst one trailing partial line, never the file whole; nothing here is ever
rewritten or deleted in place, so a reader can always replay the ledger's
whole history from empty; and a caller who already validated a record fully
before this module ever ran (jev_decide.py, JEV-01, produces its own
in-memory Decision objects, not ledger lines) still gets that record
re-validated on the way in, because this ledger outlives any one caller and
a line on disk is not proven safe just because something upstream once
checked it.

VALIDATION RUNS TWICE ON PURPOSE: once in append_decision()/append_outcome()
before a line is ever written, and again, by the SAME function, every time
a line is read back in join(). A line that fails validation on the way in
is refused outright (raises ValueError, nothing is written). A line that
fails validation on the way OUT (hand-edited, written by some other tool,
corrupted on disk) is not allowed to crash the whole ledger read: it is
routed to a "corrupt" bucket and reported by line number, and everything
else in the file is still read. One validator, not two, is the reason the
two behave the same way: see _validate_decision() and _validate_outcome().

THE CENTRAL NUMBER is the Wilson score lower bound on precision, not raw
precision. Raw precision on a small sample overstates how far a caller can
trust it (9 of 10 decisions correct sounds like 90 percent, but with only
10 data points the honest 95 percent lower bound is closer to 60 percent);
threshold() reads the LOWER bound so a family, qtype and risk class earns
autonomous trust only once the evidence is actually strong enough for the
target precision to survive a conservative interval, not merely to be
plausible.

A "noul", a "choice" answered from the same family, and the exact same
family/qtype pair asked with different wording (a different "framing") are
NEVER pooled into one report row. Confidence numbers do not carry the same
meaning across question types (TypeSafe's own docs name concrete
mismatches between a noul's probability and a structurally similar
choice's probability for what looks like the same question), and a
reworded question is, for calibration purposes, a different question.
report() enforces this by filtering on family, qtype and framing together,
never approximately.

LEDGER ROTATION (A0.8, 2026-09-19). An append-only ledger that never
rotates grows without bound: after enough live traffic decisions.jsonl or
outcomes.jsonl eventually becomes a multi-gigabyte single file every
join() call has to open and scan whole. append_decision()/append_outcome()
therefore check the ACTIVE file's size after every successful write
(_rotate_if_needed()) and, once it crosses max_segment_bytes (default
DEFAULT_MAX_SEGMENT_BYTES, 50 MB; callers may pass a different value per
call), os.replace() it aside into a dated segment named
"<stem>.<UTC timestamp>Z-<8 hex>.<ext>" next to the active path, leaving a
fresh, empty active file for the very next append to recreate. Segments
older than the newest `retention_segments` (default
DEFAULT_RETENTION_SEGMENTS, 12) are removed at the same time, oldest
first. join() (via _read_rotated_jsonl(), used by report()/threshold())
reads every rotated segment for a path (oldest first, by filename sort:
the timestamp in the name is what makes "oldest" meaningful) followed by
the active file itself, and concatenates their records exactly as if they
had never been split apart: a path with zero rotated segments (every
ledger in this test suite, and any ledger that has never crossed
max_segment_bytes) reads identically to reading that one file alone, so
this is a pure addition, never a behaviour change for an unrotated ledger.
_read_jsonl()'s own line-by-line iteration already avoids buffering a
whole segment's raw bytes at once; join()'s pairing step still holds every
PARSED record from every segment in memory to match decisions to
outcomes, since an outcome can land in a different segment than its
decision and pairing them is inherently a whole-ledger operation -- this
is unchanged by rotation, not a regression rotation introduces (a true
streaming join would need a different algorithm entirely, out of scope
here: "where practical" is met at the file-read layer, not by rewriting
join()'s pairing).

Python 3.9 floor, standard library only, no network, no third party
dependency.
"""

import hashlib
import json
import math
import os
import re
import stat
import sys
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

#: The three question types this ledger and TypeSafe's own API recognise.
#: An unrecognised fourth value is never guessed at: see _validate_decision.
QTYPES = frozenset(("noul", "choice", "score"))

#: Confidence band lower bounds used when a caller supplies none. The WBS
#: unit that specified this module (JEV-02) pins this exact tuple.
DEFAULT_BANDS = (0.5, 0.7, 0.9, 1.0)

#: The z-score for a 95 percent two-sided normal interval, used by every
#: Wilson bound this module computes. Not configurable: a caller wanting a
#: different confidence level is asking a different statistical question,
#: not a parameter of this one.
_Z95 = 1.959963984540054

#: The module's own default ledger path, used only when a caller does not
#: supply one. Every test in test_jev_calibration.py uses an injected,
#: caller-chosen temp path instead: this constant exists so a real deployed
#: caller has one obvious place to point at, and so a test can assert this
#: path is never touched by a call that used its own path instead.
# ponytail: a single flat file with no locking is a known ceiling for a
# single-writer deployment. If a second concurrent writer to the SAME
# default path is ever needed, add advisory locking (fcntl.flock on POSIX)
# around _append_line; every write below is already a single os.write() of
# one line under PIPE_BUF for real records, which POSIX guarantees is
# atomic against other single-writer appenders, so the ceiling is
# concurrent writers, not single-writer safety.
DEFAULT_LEDGER_PATH = os.path.join(HERE, "jev_calibration_default_ledger.jsonl")

#: A0.8 rotation defaults: see the module docstring's LEDGER ROTATION
#: section. 50 MB keeps a single active file well within "open the whole
#: thing every join() call" comfort on ordinary hardware; 12 retained
#: segments is a deliberately round policy knob, not a derived number.
DEFAULT_MAX_SEGMENT_BYTES = 50 * 1024 * 1024
DEFAULT_RETENTION_SEGMENTS = 12

#: Matches the "<10-digit sequence>-<UTC timestamp>Z-<8 hex>" middle
#: section of a rotated segment's filename (see _rotate_if_needed() and
#: _next_segment_seq()), so directory scanning (_rotated_segment_paths())
#: never sweeps in an unrelated file that merely shares the active file's
#: stem and extension (e.g. a hand-made "decisions.backup.jsonl" sitting
#: next to "decisions.jsonl"). Group 1 is the sequence number, the part
#: ordering and retention actually rely on -- see _next_segment_seq()'s
#: own docstring for why it, not the timestamp, is load-bearing.
_SEGMENT_STAMP_RE = re.compile(r"^(\d{10})-\d{8}T\d{12}Z-[0-9a-f]{8}$")


def _parse_ts(value):
    """Parses an ISO 8601 timestamp string into an aware UTC datetime.

    Raises ValueError (never any other exception type) naming the bad
    value when `value` is not a string or does not parse. A timestamp with
    no timezone is treated as UTC rather than rejected, since this estate's
    own callers write naive-looking ISO strings; a timestamp that DOES
    carry a timezone is converted to UTC rather than compared naively
    against one that does not, so two decisions from different callers are
    always ordered correctly against each other.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("at must be a non-empty ISO 8601 timestamp string, got %r" % (value,))
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("at is not a parseable ISO 8601 timestamp: %r" % (value,))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _require_nonempty_str(rec, field):
    if field not in rec:
        raise ValueError("missing required field: %s" % field)
    val = rec[field]
    if not isinstance(val, str) or not val.strip():
        raise ValueError("%s must be a non-empty string, got %r" % (field, val))
    return val


def _require_finite_number(rec, field, low=None, high=None):
    if field not in rec:
        raise ValueError("missing required field: %s" % field)
    val = rec[field]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError("%s must be a number, got %r" % (field, val))
    if not math.isfinite(val):
        raise ValueError("%s must be finite, got %r" % (field, val))
    if low is not None and val < low:
        raise ValueError("%s must be >= %r, got %r" % (field, low, val))
    if high is not None and val > high:
        raise ValueError("%s must be <= %r, got %r" % (field, high, val))
    return float(val)


def _validate_decision(rec):
    """Raises ValueError naming the first problem found. Never mutates rec.

    Reused by both append_decision() (before a write) and join() (on every
    line read back), so a record is held to exactly one standard on the way
    in and on the way out. See the module docstring's VALIDATION RUNS
    TWICE note for why this matters.
    """
    if not isinstance(rec, dict):
        raise ValueError("decision record must be a JSON object, got %r" % (rec,))
    _require_nonempty_str(rec, "id")
    _require_nonempty_str(rec, "family")
    qtype = _require_nonempty_str(rec, "qtype")
    if qtype not in QTYPES:
        raise ValueError("qtype must be one of %s, got %r" % (sorted(QTYPES), qtype))
    _require_nonempty_str(rec, "framing")
    if "answer" not in rec:
        raise ValueError("missing required field: answer")
    try:
        json.dumps(rec["answer"])
    except TypeError:
        raise ValueError("answer is not JSON-serializable: %r" % (rec["answer"],))
    prob = _require_finite_number(rec, "prob", low=0.0, high=1.0)
    confidence = _require_finite_number(rec, "confidence", low=0.0, high=1.0)
    _require_nonempty_str(rec, "model")
    _require_finite_number(rec, "cost", low=0.0)
    _require_nonempty_str(rec, "at")
    _parse_ts(rec["at"])
    if qtype == "noul" and abs(prob - confidence) > 1e-9:
        raise ValueError(
            "for qtype noul, confidence must equal prob (TypeSafe reports no "
            "separate confidence for a noul answer); got prob=%r confidence=%r"
            % (prob, confidence)
        )


def _validate_outcome(rec):
    """Raises ValueError naming the first problem found. Never mutates rec."""
    if not isinstance(rec, dict):
        raise ValueError("outcome record must be a JSON object, got %r" % (rec,))
    _require_nonempty_str(rec, "id")
    if "correct" not in rec:
        raise ValueError("missing required field: correct")
    if not isinstance(rec["correct"], bool):
        raise ValueError("correct must be true or false, got %r" % (rec["correct"],))
    _require_nonempty_str(rec, "source")
    _require_nonempty_str(rec, "at")
    _parse_ts(rec["at"])


def _append_line(path, rec):
    """Writes one JSON line to `path` in a single write() syscall.

    A single os.write() of one line under PIPE_BUF (4096 bytes on every
    POSIX platform this estate targets) is atomic against other appenders
    to the same file: readers never observe a torn line from a concurrent
    single writer. This is weaker than a lock (concurrent writers can still
    interleave complete lines in either order, which this ledger's
    append-only, order-independent design tolerates) and is documented as
    a stated ceiling above, not silently assumed.
    """
    line = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > 4096:
        # Still correct, just no longer provably atomic against a
        # concurrent writer; state this rather than silently proceed.
        pass
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _stat_reliable(path):
    """os.stat(path), protected against the "cannot tell" case a bare
    os.path.isdir()/os.path.isfile() precheck folds into "does not
    exist". Proven this session (mirrors the fact _read_jsonl()'s own
    docstring already names about os.path.isfile()): os.path.isdir()/
    isfile() return False, not raise, when `path` itself is fine but a
    PARENT directory somewhere above it cannot even be traversed
    (permission denied) -- os.stat() needs search permission on every
    ancestor except the final component, and both isdir()/isfile()
    swallow that OSError internally and hand back a bare False, which
    reads exactly like "genuinely does not exist yet".

    Returns (stat_result_or_None, reliable). reliable is False only when
    os.stat() failed for a reason OTHER than the path (or a path
    component) genuinely not existing: FileNotFoundError and
    NotADirectoryError are trusted as "confirmed absent" (the ordinary,
    expected case for a ledger segment or directory that has simply
    never been written yet); every other OSError (PermissionError
    chief among them) means "cannot tell", never "absent".
    """
    try:
        return os.stat(path), True
    except (FileNotFoundError, NotADirectoryError):
        return None, True
    except OSError:
        return None, False


def _safe_isdir(path):
    """(is_a_directory, reliable). reliable False means the check itself
    could not be trusted (see _stat_reliable's own docstring) -- a caller
    must not read that case as "not a directory", only as "cannot tell"."""
    st, reliable = _stat_reliable(path)
    if not reliable:
        return False, False
    return (st is not None and stat.S_ISDIR(st.st_mode)), True


def _safe_isfile(path):
    """(is_a_regular_file, reliable). Same reliability semantics as
    _safe_isdir()."""
    st, reliable = _stat_reliable(path)
    if not reliable:
        return False, False
    return (st is not None and stat.S_ISREG(st.st_mode)), True


def _segment_parts(path):
    """(directory, stem, ext) for `path`, e.g. "/x/decisions.jsonl" ->
    ("/x", "decisions", ".jsonl"). Used by both rotation (naming a new
    segment) and segment discovery (matching existing ones)."""
    directory = os.path.dirname(os.path.abspath(path))
    stem, ext = os.path.splitext(os.path.basename(path))
    return directory, stem, ext


def _list_segment_names(directory, stem, ext):
    """(sorted matching rotated-segment file names in `directory` for
    (stem, ext), ok). A nonexistent directory is ([], True) -- nothing
    has ever been written, the ordinary case, not an anomaly. ok is False
    only when the directory EXISTS but os.listdir() itself raised (a
    permission problem, a race): m1 fix (independent re-review,
    2026-09-19, "make rotation's error handling match its comment" --
    _rotate_if_needed()'s own docstring already claimed every OSError
    here is swallowed, but this call was unguarded and could still raise
    straight through it). Shared by _rotated_segment_paths() and
    _next_segment_seq() (both best-effort: an unlistable directory reads
    the same as an empty one for THEIR purposes) and by
    _protection_scan() (N3: which must SEE ok=False rather than have it
    silently folded into "no segments", since "cannot list" must never
    read as "protects nothing" -- see that function's own docstring).

    ITEM 3 FIX (2026-09-19): the isdir precheck used to be a bare
    `os.path.isdir(directory)`, which returns False -- not raise -- when
    a PARENT of `directory` cannot be traversed (permission denied),
    exactly the same "cannot tell" case _read_jsonl()'s own docstring
    already documents for os.path.isfile(). That silently read "cannot
    even reach this directory" as "confirmed absent, nothing recorded
    here" -- the fail-open direction every other read in this module
    exists to refuse (probed: a ledger directory's own PARENT chmod
    0o000, real segments and a real active file underneath, still
    unaffected in their own permissions -- read as ([], True), an empty,
    reliable ledger, when the true answer is "cannot tell"). Now routed
    through _safe_isdir(), which distinguishes "genuinely does not
    exist yet" (still ([], True), the ordinary case) from "cannot even
    stat it" (([], False))."""
    is_dir, dir_reliable = _safe_isdir(directory)
    if not dir_reliable:
        return [], False
    if not is_dir:
        return [], True
    prefix = stem + "."
    active_name = stem + ext
    try:
        listing = os.listdir(directory)
    except OSError:
        return [], False
    names = []
    for name in listing:
        if name == active_name or not name.startswith(prefix):
            continue
        if ext and not name.endswith(ext):
            continue
        middle = name[len(prefix):(len(name) - len(ext)) if ext else len(name)]
        if _SEGMENT_STAMP_RE.match(middle):
            names.append(name)
    names.sort()
    return names, True


def _rotated_segment_paths(path):
    """Every already-rotated segment for `path`, oldest first. Sorted by
    plain string comparison, which is correct because the SEQUENCE NUMBER
    leading every segment name (see _next_segment_seq()) is a fixed-width,
    zero-padded integer, never the wall clock: a system clock set
    backwards (review minor, 2026-09-19) can no longer reorder segments,
    since ordering never depends on it. Never includes `path` itself.
    Returns [] when the directory does not exist yet (nothing has ever
    been written), holds no matching segment, or cannot be listed at all
    (best-effort read side -- see _list_segment_names(); a caller that
    must tell "empty" apart from "unreadable" uses that helper, or
    _protection_scan(), directly)."""
    directory, stem, ext = _segment_parts(path)
    names, _ok = _list_segment_names(directory, stem, ext)
    return [os.path.join(directory, name) for name in names]


def _next_segment_seq(directory, stem, ext):
    """(seq, reliable). seq is 1 + the highest existing segment sequence
    number for this stem in `directory`, or 0 when there are none yet.
    reliable is False when the directory cannot be listed -- round 4 fix
    (2026-09-19, Major 1's sibling defect): seq used to be handed back as
    a bare 0 in that case (best-effort, same as the old
    _rotated_segment_paths()), and _rotate_if_needed() would then happily
    number a brand new segment "0000000000-..." while blind to what
    sequence numbers already exist on disk -- exactly the ordering
    collision _next_segment_seq()'s own review-minor fix (reading real
    segment names instead of the wall clock) exists to prevent. The
    caller must now refuse to rotate at all when reliable is False (see
    _rotate_if_needed()), never assign sequence 0 while blind. Review
    minor, 2026-09-19: reading the ACTUAL existing segment names, rather
    than trusting time.time()/datetime.now(), is what keeps segment
    ordering correct even when the wall clock is set backwards --
    ordering (_rotated_segment_paths' plain string sort) comes entirely
    from this integer prefix; the human-readable timestamp that follows
    it in the filename is for a person reading the directory listing,
    never for sorting."""
    names, reliable = _list_segment_names(directory, stem, ext)
    prefix = stem + "."
    highest = -1
    for name in names:
        middle = name[len(prefix):(len(name) - len(ext)) if ext else len(name)]
        m = _SEGMENT_STAMP_RE.match(middle)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1, reliable


#: Matches a `"family":"<value>"` fragment in a raw, possibly-truncated
#: ledger line that does not even parse as JSON -- see
#: _extract_family_from_raw_text() below.
_RAW_FAMILY_RE = re.compile(r'"family"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _extract_family_from_raw_text(raw_text):
    """Best-effort family attribution for a decision-ledger line that is
    not even valid JSON (a genuinely torn write: this module's own
    append discipline means a crash corrupts at worst one TRAILING
    partial line -- see the module docstring's THE LEDGER section). A
    regex match on the raw bytes, never a JSON parse (a torn line by
    definition cannot be parsed): _append_line() writes keys in sorted
    order (json.dumps(..., sort_keys=True)), and among a decision
    record's own keys (answer, at, confidence, cost, family, framing,
    id, model, prob, qtype) "family" sorts before "id" and "qtype" --
    so a truncation that cuts off the END of a line often still leaves
    "family" intact even though the record as a whole does not parse.

    Returns None (unattributable) when no such fragment is found, or
    when the matched fragment is itself not valid JSON string content
    (a torn escape sequence) or is blank -- the same "unattributable
    means treat as ledger-wide" contract _extract_family_qtype() and
    _family_from_orphan_id() already follow elsewhere in this module.
    Never raises: this is read on the failure path itself.
    """
    if not isinstance(raw_text, str):
        return None
    m = _RAW_FAMILY_RE.search(raw_text)
    if not m:
        return None
    try:
        fam = json.loads('"%s"' % m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    return fam if isinstance(fam, str) and fam.strip() else None


def _segment_ids_scan(path, id_field="id"):
    """(ids, reliable, corrupt_families) -- every well-formed record's
    `id_field` string value found in the single file at `path` (pass one
    segment or the active file; no rotation-awareness here), together
    with whether the read could be TRUSTED, and a best-effort family
    attribution for whatever made it untrustworthy.

    reliable is False on any OSError, UnicodeDecodeError (bad UTF-8) or
    corrupt (non-JSON) line found while reading `path` -- ONE LEDGER READ
    LAYER, round 4 fix (2026-09-19): a caller about to make a DESTRUCTIVE
    decision from this membership check (M2's stale-segment delete in
    _rotate_if_needed(); the write-boundary check in append_outcome(),
    via _all_ledger_ids()) must see the read failed, never read "could
    not check" as "not referenced" -- the exact fail-open direction M2
    named: a stale segment chmod 000 used to read as "protects nothing,
    safe to delete" via _segment_ids()'s own silent OSError swallow. A
    record present but missing `id_field` (or not a JSON object) is left
    as silently skipped, unchanged from _segment_ids()'s prior
    behaviour: this function is still a best-effort MEMBERSHIP check,
    not _read_jsonl()'s full schema validation, for that one narrower
    case.

    corrupt_families (item 5, 2026-09-19) is a set of every best-effort
    family attribution for a line or read failure that made `reliable`
    False: _extract_family_from_raw_text() on the raw text of a line
    that fails to parse as JSON at all, or None (unattributable) for the
    whole-file OSError/UnicodeDecodeError branch, which carries no line
    to attribute at all. This is what lets append_outcome()'s
    write-boundary check (via _all_ledger_ids()) scope its refusal to
    only the family a torn line actually names, instead of blocking
    every family in the ledger the same way _ledger_anomaly_touches()
    already scopes threshold()'s own anomaly gate for the READ side --
    see that function's own docstring, and append_outcome()'s, for how a
    caller reads this set: None present, or the id's own family present,
    means "cannot rule this out, refuse"; every other family found here
    is provably unrelated to a differently-named id.

    ITEM 3 FIX (2026-09-19): the isfile precheck used to be a bare
    `os.path.isfile(path)`, which returns False -- not raise -- when a
    PARENT of `path` cannot be traversed (permission denied), exactly
    the "cannot tell" case _read_jsonl()'s own docstring documents. That
    silently read "cannot even reach this file" as "confirmed absent",
    the fail-open direction this whole function otherwise exists to
    refuse. Now routed through _safe_isfile()."""
    ids = set()
    reliable = True
    corrupt_families = set()
    is_file, file_reliable = _safe_isfile(path)
    if not file_reliable:
        return ids, False, {None}
    if not is_file:
        return ids, True, corrupt_families
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    reliable = False
                    corrupt_families.add(_extract_family_from_raw_text(stripped))
                    continue
                if isinstance(rec, dict) and isinstance(rec.get(id_field), str):
                    ids.add(rec[id_field])
    except (OSError, UnicodeDecodeError):
        reliable = False
        corrupt_families.add(None)
    return ids, reliable, corrupt_families


def _segment_ids(path, id_field="id"):
    """The set of every well-formed record's `id_field` string value found
    in the single file at `path`. Best-effort (a read failure is folded
    into "found nothing" here, not raised or reported): see
    _segment_ids_scan() above for the reliability-tracking variant a
    destructive caller must use instead. Kept, unchanged in shape, for
    the M5 protected-ids widening read (never destructive: it only ever
    WIDENS what retention keeps) and for direct test inspection."""
    ids, _reliable, _families = _segment_ids_scan(path, id_field=id_field)
    return ids


def _all_ledger_ids(path, id_field="id"):
    """(ids, reliable, corrupt_families) -- the union of
    _segment_ids_scan() over every rotated segment for `path` plus the
    active file itself: every id anywhere in this ledger, rotated or
    not, together with whether the directory listing and every segment
    read could be trusted, and the union of every segment's own
    corrupt_families (item 5, see _segment_ids_scan()'s own docstring).
    round 4 fix (2026-09-19): previously returned a best-effort set
    alone (via _segment_ids()/_rotated_segment_paths()), which let
    append_outcome() read "one segment could not be opened" as "id not
    found anywhere" and refuse a legitimate label -- the caller now sees
    the read failed and can refuse to WRITE instead of silently
    mis-refusing."""
    directory, stem, ext = _segment_parts(path)
    names, listed_ok = _list_segment_names(directory, stem, ext)
    ids = set()
    reliable = listed_ok
    corrupt_families = set() if listed_ok else {None}
    segment_paths = [os.path.join(directory, name) for name in names] + [path]
    for segment_path in segment_paths:
        seg_ids, seg_reliable, seg_families = _segment_ids_scan(segment_path, id_field=id_field)
        ids |= seg_ids
        corrupt_families |= seg_families
        if not seg_reliable:
            reliable = False
    return ids, reliable, corrupt_families


def _protection_scan(outcomes_path):
    """(ids, reliable) -- every outcome id findable in `outcomes_path`
    (every rotated segment plus the active file), for retention's M5
    cross-file protection check below, together with whether the scan
    can be TRUSTED.

    N3 fix (independent re-review, 2026-09-19): M5's original protection
    read reused _all_ledger_ids()/_segment_ids(), which are deliberately
    best-effort (an OSError or a corrupt line is silently skipped -- fine
    for a membership check that only ever WIDENS a keep-list). Retention
    reused that same best-effort read for a DESTRUCTIVE decision instead,
    so "cannot read the sibling outcomes file" silently became "nothing
    to protect, safe to delete" -- the fail-OPEN direction on an
    irreversible action (probed: 140 decisions written while the sibling
    outcomes file was unreadable orphaned all 60 of their later-readable
    labels once the file became readable again). reliable=False the
    moment ANY of these is found, anywhere across every segment: the
    outcomes directory cannot be listed, a segment file cannot be opened,
    a line is not valid JSON, or a line parses but is not an object with
    a non-empty string "id". The caller (_rotate_if_needed() below) must
    then skip the ENTIRE prune pass, not just the segments this scan
    happened to fail on: unknown means keep, never means empty.

    ITEM 3 FIX (2026-09-19): the per-segment isfile precheck used to be a
    bare `os.path.isfile(segment_path)`, which returns False -- not
    raise -- when a PARENT of `segment_path` cannot be traversed
    (permission denied): the exact "cannot tell" case documented at
    _read_jsonl()'s own docstring and this function's own N3 fix above
    already exist to refuse elsewhere in this same function. Routed
    through _safe_isfile() so that case sets reliable=False instead of
    silently `continue`-ing past it as "the active file, nothing written
    yet"."""
    ids = set()
    reliable = True
    directory, stem, ext = _segment_parts(outcomes_path)
    names, listed_ok = _list_segment_names(directory, stem, ext)
    if not listed_ok:
        reliable = False
    segment_paths = [os.path.join(directory, name) for name in names] + [outcomes_path]
    for segment_path in segment_paths:
        is_file, file_reliable = _safe_isfile(segment_path)
        if not file_reliable:
            reliable = False
            continue
        if not is_file:
            continue  # the active file, when nothing has been written to it yet
        try:
            with open(segment_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        reliable = False
                        continue
                    if isinstance(rec, dict) and isinstance(rec.get("id"), str) and rec["id"]:
                        ids.add(rec["id"])
                    else:
                        reliable = False
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError (bad UTF-8) added round 4, 2026-09-19: an
            # OSError-only catch let invalid UTF-8 in the outcomes ledger
            # raise straight through append_decision()'s own rotation
            # step (round 3 re-review m6) -- ONE LEDGER READ LAYER means
            # this scan never lets an unreadable BYTE, not only an
            # unreadable FILE, read as "reliable".
            reliable = False
    return ids, reliable


#: Directories this process has already warned about via
#: _warn_once_unlistable(), so a busy ledger that appends far more often
#: than an operator fixes a permission problem does not flood stderr with
#: the same line on every single append past max_segment_bytes.
_WARNED_UNLISTABLE_DIRS = set()


def _warn_once_unlistable(directory):
    """One stderr line per unlistable segment directory per process (item
    2c): rotation refuses to number a new segment while blind to what is
    already there, and an operator needs to know why the active ledger
    file keeps growing past max_segment_bytes instead of rotating."""
    if directory in _WARNED_UNLISTABLE_DIRS:
        return
    _WARNED_UNLISTABLE_DIRS.add(directory)
    print(
        "jev_calibration: %s cannot be listed -- rotation refused for this "
        "path until it can be (the active ledger file keeps growing "
        "safely; no segment is numbered while existing segments cannot "
        "be seen)" % directory,
        file=sys.stderr,
    )


def _rotate_if_needed(path, max_segment_bytes, retention_segments, protect_ids_referenced_in=None):
    """Rotates `path` aside into a dated segment once its size crosses
    max_segment_bytes, then prunes rotated segments beyond
    retention_segments (oldest first). Called after every successful
    append (see append_decision()/append_outcome()). Best-effort: any
    OSError here (permission, a concurrent remover, a slow filesystem) is
    swallowed rather than raised -- the record the caller just appended is
    already safely on disk either way, a rotation failure must never turn
    a successful write into a reported one, and the very next append
    simply tries rotation again. m1 fix (independent re-review,
    2026-09-19, "make rotation's error handling match its comment"): this
    promise now actually holds end to end -- every os.listdir() this
    function's own call chain reaches (_next_segment_seq(),
    _rotated_segment_paths(), _protection_scan()) is guarded via
    _list_segment_names(), never left to raise straight through the way
    it previously could when a listable-but-not-readable directory
    surfaced a bare PermissionError.

    M5 fix (independent review, 2026-09-19): retention on the DECISIONS
    ledger must never delete a segment whose ids are still referenced by
    a currently-retained OUTCOME -- doing so orphans a real human label
    (probed: 300 decisions/60 early outcomes at retention=3 produced 60
    orphans and 0 joins). `protect_ids_referenced_in`, when given
    (append_decision() passes its sibling outcomes_path; append_outcome()
    passes nothing, since losing an old DECISION segment with no matching
    outcome yet is the normal, harmless "unmatched, not yet labelled"
    state), is read via _protection_scan() (N3 fix: no longer
    _all_ledger_ids(), see that function's own docstring for why a
    destructive check needs a STRICTER read than an ordinary membership
    one) for every candidate-for-deletion segment; a segment whose ids
    intersect it is SKIPPED (kept alive), never deleted to hit the
    nominal retention count, and an UNRELIABLE scan skips the entire
    prune pass for this call (N3). This can make decision-segment
    retention exceed `retention_segments` for as long as the
    corresponding outcome segment survives its OWN retention -- outcomes
    are written far less often than decisions, so they rotate, and
    therefore get pruned, far less often too, which is the source of the
    excess, NOT a bound on it (m4 fix, independent re-review, 2026-09-19:
    this section previously called that excess "a deliberate, bounded
    tradeoff", which had it backwards -- nothing here bounds how large it
    can grow; see the hard ceiling warning below, which is a stderr
    notice, not a bound either). Once that outcome segment is eventually
    pruned in its own right, the decision segment it was protecting
    becomes eligible again on the next rotation pass. Never a full
    solution to unbounded retention (nothing here promises decisions are
    kept forever), but it stops the premature, silent loss of a label
    that the reviewer's probe found.

    ROUND 4 FIX (2026-09-19), two more fail-open directions the previous
    round's own m1/N3 fixes left open, both found by re-review against
    this same function:

    (item 2c) SEGMENT NUMBERING REFUSES TO ROTATE ON AN UNLISTABLE
    DIRECTORY. _next_segment_seq() used to hand back a bare best-effort
    0 when the directory could not be listed, and this function happily
    numbered a brand new segment "0000000000-..." while blind to what
    sequence numbers already exist -- sorting it to the FRONT of the
    prune order (oldest-first) despite holding the newest data. Now: an
    unreliable seq means no rotation happens at all this call (the active
    file keeps growing, which is always safe -- append_decision()/
    append_outcome() already wrote their record before rotation is even
    attempted), a one-line stderr warning fires once per directory per
    process (not once per call: this runs on every append past
    max_segment_bytes), and the very next append tries again.

    (item 2b, Major 2 / M2) A STALE SEGMENT THAT CANNOT BE READ IS NEVER
    DELETED. The M5 protection loop below used to call _segment_ids()
    (best-effort) directly on the very segment about to be removed: an
    unreadable stale segment (permission, a concurrent remover) silently
    read as "holds no protected ids" and was deleted anyway -- reviewer
    probe: a retention-1 stale segment chmod 000 was still removed while
    a retained outcome named an id inside it, orphaning that label
    exactly like N3's original unreadable-OUTCOMES-file case, just on the
    read of the DECISIONS segment itself this time. Now: every
    candidate-for-deletion segment's ids are read with
    _segment_ids_scan() (the reliability-tracking layer) BEFORE anything
    is removed; ANY one of them being unreadable aborts the WHOLE prune
    pass for this call, deleting nothing (never "delete the ones that
    read fine, skip only the one that didn't" -- a partial prune still
    needs the unreadable segment's own contents to know whether skipping
    it was even necessary, so the safe default is the same as N3's:
    unknown means keep, never means empty)."""
    if max_segment_bytes is None:
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size <= max_segment_bytes:
        return
    directory, stem, ext = _segment_parts(path)
    seq, seq_reliable = _next_segment_seq(directory, stem, ext)
    if not seq_reliable:
        _warn_once_unlistable(directory)
        return  # never assigns sequence 0 while blind to what is on disk
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    segment_path = os.path.join(
        directory, "%s.%010d-%sZ-%s%s" % (stem, seq, stamp, uuid.uuid4().hex[:8], ext))
    try:
        os.replace(path, segment_path)
    except OSError:
        return  # next append recreates `path` fresh either way
    if retention_segments is None:
        return
    names, listed_ok = _list_segment_names(directory, stem, ext)
    if not listed_ok:
        # Rotation itself already succeeded (the record is safely in its
        # new segment); only the PRUNE step is skipped here, same as any
        # other unreliable read in this function -- nothing is deleted
        # while blind to what is actually on disk.
        return
    segments = [os.path.join(directory, name) for name in names]  # oldest first, includes the one just rotated
    excess = len(segments) - retention_segments
    if excess <= 0:
        return
    protected_ids = None
    if protect_ids_referenced_in is not None:
        protected_ids, reliable = _protection_scan(protect_ids_referenced_in)
        if not reliable:
            # N3 fix: cannot verify what this pass would orphan -- keep
            # everything rather than guess. The very next successful
            # append tries rotation, and this same protection scan, again.
            return

    # M2 (round 4 fix): read every stale candidate's own ids RELIABLY
    # before deleting any of them -- see this function's own docstring.
    stale_candidates = []
    for stale in segments[:excess]:
        stale_ids, stale_reliable, _stale_families = _segment_ids_scan(stale, id_field="id")
        if not stale_reliable:
            return  # cannot verify this segment is safe to remove -- delete nothing this pass
        stale_candidates.append((stale, stale_ids))

    deleted = 0
    for stale, stale_ids in stale_candidates:
        if protected_ids is not None and (stale_ids & protected_ids):
            continue  # M5: still referenced by a retained outcome -- keep it
        try:
            os.remove(stale)
            deleted += 1
        except OSError:
            pass

    # m4 fix (independent re-review, 2026-09-19): a hard CEILING, not a
    # deletion -- M5's own protection above can make retention grow
    # without bound (see this function's own docstring). Protected data
    # is never removed to enforce this; it is purely a one-line, one-call
    # operator signal that this ledger's segment count has grown past a
    # sanity multiple of its configured retention, so the actual cause
    # (an outcomes segment that itself never rotates, or never gets
    # pruned) gets a human's attention before disk usage becomes the
    # discovery mechanism instead.
    remaining = len(segments) - deleted
    if remaining > retention_segments * 10:
        print(
            "jev_calibration: %s has %d rotated segments kept, more than "
            "10x its retention setting (%d) -- likely protected by M5 "
            "(see _rotate_if_needed's own docstring); not deleting "
            "protected data, but this needs an operator's attention"
            % (path, remaining, retention_segments),
            file=sys.stderr,
        )


def _read_rotated_jsonl(path, validator):
    """Like _read_jsonl(), but reads every rotated segment for `path`
    (oldest first) followed by `path` itself (the active file), and
    concatenates their records and corrupt entries. A path with no rotated
    segments reads identically to _read_jsonl(path, validator) alone: see
    the module docstring's LEDGER ROTATION section.

    Returns (records, corrupt, reliable). round 4 fix (2026-09-19, Major
    1): reliable is False when the segment directory itself could not be
    listed -- this used to be silently dropped (via the old, plain-list
    _rotated_segment_paths()), so join()/threshold() read an unlistable
    ledger directory as "no rotated history" instead of "cannot tell",
    and act mode could ACT on a threshold computed from only the active
    file (reviewer probe: a ledger dir chmod 0300, 40 older mixed labels
    rotated aside, 40 recent correct labels active -- the full ledger
    correctly refused to compute a threshold; reading only the active
    file computed one). A corrupt, unattributable entry is STILL appended
    to `corrupt` in this case too (defence in depth: report()'s existing
    "ledger anomaly" gate also catches it), but the distinct reliable
    flag is what lets threshold() give the more specific "ledger
    unreadable" reason, checked before any anomaly gate, so act never
    computes from a partial read in the first place.

    reliable is ALSO False when the active file was rotated out from
    under this read (concurrent writer): segment names are listed once,
    up front, so a rotation happening after that listing moves whatever
    was in the active file into a new segment this call never fetches,
    and replaces the active file with a fresh, usually-empty one --
    silently under-counting records with no error. Detected via one
    os.stat() before listing and one after reading the active file: a
    genuine ordinary append only grows size and advances mtime, so ONLY
    a size decrease or an mtime_ns that moved backward is treated as
    rotation (a plain concurrent append, which only grows the file,
    must never be flagged -- that would be a false alarm on the single
    most common case, not a rotation)."""
    directory, stem, ext = _segment_parts(path)
    names, listed_ok = _list_segment_names(directory, stem, ext)
    records = []
    corrupt = []
    if not listed_ok:
        corrupt.append({
            "line": 0,
            "error": "could not list rotated segments for %s: directory unreadable" % path,
            "raw": None, "family": None, "qtype": None,
        })
    try:
        stat_before = os.stat(path)
    except OSError:
        stat_before = None
    segment_paths = [os.path.join(directory, name) for name in names] + [path]
    for segment_path in segment_paths:
        seg_records, seg_corrupt = _read_jsonl(segment_path, validator)
        records.extend(seg_records)
        corrupt.extend(seg_corrupt)
    try:
        stat_after = os.stat(path)
    except OSError:
        stat_after = None
    raced = False
    if stat_before is not None and stat_after is not None:
        raced = (stat_after.st_size < stat_before.st_size
                  or stat_after.st_mtime_ns < stat_before.st_mtime_ns)
    elif stat_before is not None and stat_after is None:
        raced = True  # active file vanished mid-read: rotated (or deleted) out from under us
    if raced:
        corrupt.append({
            "line": 0,
            "error": ("active file %s changed size or mtime during read "
                       "(likely rotated away mid-read): read may be missing "
                       "records that moved into a new segment" % path),
            "raw": None, "family": None, "qtype": None,
        })
    return records, corrupt, listed_ok and not raced


def append_decision(path, rec, *, max_segment_bytes=DEFAULT_MAX_SEGMENT_BYTES,
                     retention_segments=DEFAULT_RETENTION_SEGMENTS, sibling_outcomes_path=None):
    """Validates and appends one decision record. Raises ValueError and
    writes nothing on any validation failure (validate-then-write, never
    partial). Rotates `path` per _rotate_if_needed() afterward; pass
    max_segment_bytes=None to disable rotation for this path entirely.
    `sibling_outcomes_path`, when given, is the matching outcomes ledger:
    retention will never delete a decision segment whose ids are still
    referenced by a retained outcome there (M5, see _rotate_if_needed()'s
    own docstring). Optional and defaults to None (no cross-file
    protection) so an existing caller that has no sibling path to hand in
    is unaffected; a caller writing REAL, retained decisions should pass
    it."""
    _validate_decision(rec)
    _append_line(path, rec)
    _rotate_if_needed(path, max_segment_bytes, retention_segments,
                       protect_ids_referenced_in=sibling_outcomes_path)


def append_outcome(path, rec, *, decisions_path=None, max_segment_bytes=DEFAULT_MAX_SEGMENT_BYTES,
                    retention_segments=DEFAULT_RETENTION_SEGMENTS, allow_unchecked=False):
    """Validates and appends one outcome record. Raises ValueError and
    writes nothing on any validation failure. Rotates `path` per
    _rotate_if_needed() afterward; pass max_segment_bytes=None to disable
    rotation for this path entirely. No cross-file protection on THIS
    file's own rotation (see M5 in _rotate_if_needed()'s own docstring
    for why only the decisions side needs that).

    `decisions_path`, N1 fix (independent re-review, 2026-09-19): the
    outcome-labelling boundary -- when given, `rec["id"]` must already be
    present somewhere in that decisions ledger (every rotated segment
    plus the active file, via _all_ledger_ids()), or this raises
    ValueError and writes nothing. WHY: jev_seam.py's shadow mode used to
    hand a caller a decision_id before its worker had actually written
    the row (a bridge NO-DATA, a near-threshold disagreement, or a local
    write error could all leave it unwritten); a human labelling an
    outcome against that id created a real ledger row with no matching
    decision, which _ledger_anomaly_touches() then reads as a corrupt
    ledger and disables that whole family's calibration for as long as
    the label is retained -- a self-inflicted, durable denial of service
    from a single mislabel. Refusing AT THE WRITE BOUNDARY, for any
    caller that can name its decisions ledger, prevents that class of
    anomaly from ever being created in the first place (a control that
    PREVENTS, not one that reports after the fact).

    decisions_path is now REQUIRED FOR REAL USE (round 4 fix, 2026-09-19):
    omitting it raises ValueError -- the round 3 re-review found 39 test
    call sites and the how-to's own labelling instructions omitted it,
    which meant the FIRST real labelling caller doing the same reopened
    the exact anomaly path N1 exists to close (a default that silently
    skips a check is a check nobody can rely on). `allow_unchecked=True`
    is the one explicit, named opt-out: for a caller that genuinely has
    no decisions ledger to check against (a migration import of
    pre-existing data, or a test deliberately constructing an orphan to
    exercise join()'s own tolerance for one -- see TestJoin's orphan
    tests, which model a fact about EXISTING data this ledger reads, not
    something an append-time guard can undo). Every real caller in this
    codebase already names its decisions ledger and needs no opt-out:
    seed_from_eval() below, and jev_cascade.py's own selftest, both
    already pass decisions_path.

    When decisions_path IS given, the presence check itself now also
    requires a RELIABLE read of that ledger (via _all_ledger_ids()):
    a directory or segment that could not be listed/read raises too,
    rather than let "cannot verify" be silently read as "id not found"
    (a false refusal) or, worse, "found" (an orphan the guard exists to
    prevent).

    ITEM 5 FIX (2026-09-19): that "cannot verify" refusal used to be
    whole-ledger -- ANY unreadable byte or corrupt (non-JSON) line
    ANYWHERE in the decisions ledger durably blocked labelling for EVERY
    family, including one that shares no torn line with the corrupt
    event at all (probed: one hand-torn line naming family "shipping"
    made every "billing:..." outcome unlabellable too, with the ledger
    otherwise perfectly healthy). Scoped now exactly the way
    _ledger_anomaly_touches() already scopes threshold()'s own anomaly
    gate on the READ side, applied here to the WRITE boundary instead:
    `rec["id"]`'s own family is read from its "<family>:<...>" shape
    (_family_from_orphan_id(), the same convention decision ids from the
    seam already follow) and compared against _all_ledger_ids()'s
    corrupt_families. The refusal still fires, unchanged, when: the id
    carries no readable family prefix at all (nothing to scope against);
    or any corrupt event is itself unattributable (None in
    corrupt_families -- could be anything, including this exact family);
    or a corrupt event names this exact id's family. Every other
    unreliability is now provably about a DIFFERENT family, so the
    ordinary "found in ids" check below is trusted instead of refusing
    on principle."""
    _validate_outcome(rec)
    if decisions_path is None:
        if not allow_unchecked:
            raise ValueError(
                "append_outcome requires decisions_path (or allow_unchecked=True as an "
                "explicit, deliberate opt-out): omitting it used to silently skip the "
                "write-boundary check that stops an outcome being labelled against an id "
                "that was never written (see this function's own docstring, N1)")
    else:
        ids, reliable, corrupt_families = _all_ledger_ids(decisions_path)
        if not reliable:
            outcome_family = _family_from_orphan_id(rec.get("id"))
            scoped_clean = (
                outcome_family is not None
                and None not in corrupt_families
                and outcome_family not in corrupt_families
            )
            if not scoped_clean:
                raise ValueError(
                    "cannot verify decisions_path %s is fully readable: refusing to label an "
                    "outcome against it rather than risk writing an orphan a later, reliable "
                    "read would silently accept" % decisions_path)
        if rec.get("id") not in ids:
            raise ValueError(
                "outcome id %r is not present in the decisions ledger at %s: refusing to "
                "label an id that was never written, rather than risk a ledger anomaly "
                "for a family that never had a real decision behind this id" % (rec.get("id"), decisions_path))
    _append_line(path, rec)
    _rotate_if_needed(path, max_segment_bytes, retention_segments)


def _extract_family_qtype(raw):
    """Best-effort (family, qtype) attribution for a corrupt or unreadable
    record, so an anomaly caused by one bad line can be scoped to the
    family/qtype it names instead of forcing every family/qtype to
    escalate. Returns (None, None) -- "unattributable" -- when raw is not
    a dict, or does not carry two non-empty string family/qtype values.
    This is deliberately permissive about what else is wrong with the
    record (that is exactly why it is corrupt) and deliberately strict
    that family/qtype themselves must be trustworthy strings before
    anything is attributed to them. An outcome record never carries
    family/qtype at all, so a corrupt outcome line is always
    unattributable by this function, on purpose (see
    threshold()'s ledger-anomaly check, C1, 2026-09-18 review fix).
    """
    if not isinstance(raw, dict):
        return None, None
    fam = raw.get("family")
    qt = raw.get("qtype")
    if isinstance(fam, str) and fam.strip() and isinstance(qt, str) and qt.strip():
        return fam, qt
    return None, None


def _read_jsonl(path, validator):
    """Reads `path` line by line. Returns (records, corrupt).

    records is a list of (line_no, rec) for every line that is valid JSON
    AND passes `validator`. corrupt is a list of {"line", "error", "raw"}
    for every non-blank line that is not valid JSON, or is valid JSON but
    fails validator. A missing file is treated as an empty ledger (an
    empty ledger and a not-yet-created one are the same fact for this
    module: nothing has been recorded), never an error. open() is the
    single source of truth for that, not an os.path.isfile() pre-check:
    proven this session that os.path.isfile() on a path inside a
    directory whose mode denies traverse (e.g. chmod 0o600) returns
    False, not an exception, so a pre-check here would have silently
    folded "cannot even traverse the parent" into "nothing recorded",
    exactly the anomaly this module's own OSError handling below exists
    to catch. Any other unreadability (permission denied, ENOTDIR,
    EISDIR, a mid-read error, bad UTF-8) still falls into the existing
    single line-0 corrupt entry below, never empty; no new return shape,
    since corrupt != [] already IS this function's reliable=False.
    """
    records = []
    corrupt = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                text = raw.strip()
                if not text:
                    continue
                try:
                    rec = json.loads(text)
                except json.JSONDecodeError as exc:
                    corrupt.append({
                        "line": line_no, "error": "not valid JSON: %s" % exc,
                        "raw": text, "family": None, "qtype": None,
                    })
                    continue
                try:
                    validator(rec)
                except ValueError as exc:
                    fam, qt = _extract_family_qtype(rec)
                    corrupt.append({
                        "line": line_no, "error": str(exc),
                        "raw": rec, "family": fam, "qtype": qt,
                    })
                    continue
                records.append((line_no, rec))
    except FileNotFoundError:
        # Must precede OSError below (FileNotFoundError is a subclass):
        # the file, or a parent directory in its path, does not exist.
        # This is the ONLY unreadability that reads as empty, per this
        # function's own docstring; every other OSError falls through to
        # the anomaly branch below, never silently to empty.
        return records, corrupt
    except (OSError, UnicodeDecodeError) as exc:
        # An OSError here (permission denied on the file or an
        # untraversable parent -- proven this session that
        # os.path.isfile() alone would have hidden exactly this case --,
        # a disk error mid-read, a race where the file existed a moment
        # ago but not by open() time) must never escape this module and
        # crash a caller: it fails closed the same way a corrupt line
        # does, unattributable to any one family/qtype (see
        # _extract_family_qtype and the module's C1/OSError 2026-09-18
        # review fix), so every scope treats it as a ledger anomaly
        # rather than silently reading the file as empty.
        # UnicodeDecodeError (bad UTF-8) added round 4, 2026-09-19: this
        # used to escape _read_jsonl() entirely and raise straight out of
        # join() -- ONE LEDGER READ LAYER means an unreadable byte gets
        # the same treatment as an unreadable file, never a bare raise.
        corrupt.append({
            "line": 0, "error": "%s reading %s: %s" % (type(exc).__name__, path, exc),
            "raw": None, "family": None, "qtype": None,
        })
    return records, corrupt


def _family_from_orphan_id(outcome_id):
    """Best-effort family attribution for an orphan outcome (an outcome
    whose decision id was never recorded at all, so there is no decision
    record here to read family/qtype from). Decision ids the seam writes
    are shaped "<family>:<...>": returns the substring before the first
    ':' when it is non-empty, else None (unattributable -- the orphan is
    then ledger-wide; see _ledger_anomaly_touches).

    This reads the id's own shape, it does not look the prefix up against
    a registry of families this ledger has already recorded: a family
    this ledger has never seen before is still a real family, and
    requiring an exact match against already-recorded families would
    silently fail to scope an orphan for a genuinely new family (treating
    it as unattributable and blocking every family instead) -- the exact
    over-wide-blast-radius failure this function exists to fix (coordinator
    re-review, MAJOR, 2026-09-18: one mislabelled outcome must not disable
    acting for every family).
    """
    if not isinstance(outcome_id, str):
        return None
    head, sep, _rest = outcome_id.partition(":")
    head = head.strip()
    if sep and head:
        return head
    return None


def join(decisions_path, outcomes_path):
    """Reads both ledger files and pairs decisions with outcomes by id.

    Returns a dict:
      joined                     list of {"decision": rec, "outcome": rec}
                                  for every id with exactly one decision
                                  record and one clean (non-conflicting)
                                  correctness verdict.
      unmatched_decisions        list of decision records with no outcome
                                  yet. Excluded from any precision
                                  calculation: correctness is unknown, not
                                  false.
      orphan_outcomes            list of {"id", "records", "family"} for
                                  outcome ids that never appeared as a
                                  decision at all. Never silently dropped,
                                  never guessed onto the nearest-looking
                                  decision. "family" is the best-effort
                                  attribution from _family_from_orphan_id()
                                  (the id's own "<family>:<...>" shape),
                                  None when the id carries no readable
                                  prefix. Used by threshold()'s
                                  ledger-anomaly check to scope an orphan
                                  to the one family it names instead of
                                  blocking every family (coordinator
                                  re-review, MAJOR, 2026-09-18).
      outcomes_for_duplicate_decision_ids
                                  list of {"id", "records"} for outcome ids
                                  whose decision id was written more than
                                  once (see duplicate_decision_ids below):
                                  kept separate from orphan_outcomes
                                  because a decision WAS made, just
                                  ambiguously, so calling it "no decision
                                  at all" would misstate what happened.
      duplicate_decision_ids      list of ids with more than one decision
                                  record. Excluded from precision entirely:
                                  which one actually produced the outcome
                                  cannot be known.
      ambiguous_outcomes          list of {"id", "reason", "records"} for
                                  ids whose outcome records disagree on
                                  `correct`. Excluded from precision: two
                                  irreconcilable verdicts, refuse both
                                  rather than guess which is right.
      duplicate_outcomes          list of {"id", "reason", "records"} for
                                  ids with more than one outcome record
                                  that all agree on `correct`. REFUSED, not
                                  joined (A0.3, 2026-09-18): which write was
                                  the real one is unknowable even when the
                                  two happen to agree, and a caller trusting
                                  "they agree so it is fine" is exactly the
                                  silent-default this ledger's validation
                                  discipline exists to refuse elsewhere.
                                  Reported here, and counted in report()'s
                                  ledger_wide, so an operator can see the
                                  same outcome was recorded twice.
      corrupt_decisions           list of {"line", "error", "raw", "family",
                                  "qtype"} for decision-file lines that
                                  failed validation on read. family/qtype
                                  are the best-effort attribution from
                                  _extract_family_qtype(), None when the
                                  bad line does not carry trustworthy
                                  string values for both.
      corrupt_outcomes            list of {"line", "error", "raw", "family",
                                  "qtype"} for outcome-file lines that
                                  failed validation on read. family/qtype
                                  are always None here: an outcome record
                                  never carries them.
      duplicate_decision_families
                                  dict {id: sorted [(family, qtype), ...]}
                                  for every id in duplicate_decision_ids,
                                  naming every distinct (family, qtype)
                                  pair among that id's colliding decision
                                  records (usually one pair, since a
                                  duplicate id is normally the same
                                  question written twice). Used by
                                  threshold()'s ledger-anomaly check (C1,
                                  2026-09-18 review fix) to scope
                                  escalation to the family/qtype actually
                                  touched.
      reliable                    False when either ledger's segment
                                  directory could not be listed (round 4
                                  fix, 2026-09-19, Major 1) -- distinct
                                  from the corrupt/anomaly counts above:
                                  those name records this read COULD see
                                  but did not trust; this names a read
                                  that could not see everything that is
                                  there at all. threshold() checks this
                                  BEFORE its anomaly gate and returns
                                  reason "ledger unreadable" rather than
                                  computing from a partial view.
    """
    # A0.8: reads every rotated segment plus the active file, transparently
    # -- see _read_rotated_jsonl() and the module docstring's LEDGER
    # ROTATION section. Identical to _read_jsonl(path, validator) alone
    # when `path` has never rotated.
    dec_records, corrupt_decisions, dec_reliable = _read_rotated_jsonl(decisions_path, _validate_decision)
    out_records, corrupt_outcomes, out_reliable = _read_rotated_jsonl(outcomes_path, _validate_outcome)

    dec_records_by_id = {}
    for line_no, rec in dec_records:
        dec_records_by_id.setdefault(rec["id"], []).append(rec)

    decisions_by_id = {}
    duplicate_decision_ids = set()
    duplicate_decision_families = {}
    for did, recs in dec_records_by_id.items():
        if len(recs) > 1:
            duplicate_decision_ids.add(did)
            duplicate_decision_families[did] = sorted({(r["family"], r["qtype"]) for r in recs})
        else:
            decisions_by_id[did] = recs[0]

    outcomes_by_id = {}
    for line_no, rec in out_records:
        outcomes_by_id.setdefault(rec["id"], []).append((line_no, rec))

    joined = []
    unmatched_decisions = []
    ambiguous_outcomes = []
    duplicate_outcomes = []

    for did, dec in decisions_by_id.items():
        outs = outcomes_by_id.get(did, [])
        if not outs:
            unmatched_decisions.append(dec)
            continue
        correct_values = {rec["correct"] for _, rec in outs}
        if len(correct_values) > 1:
            ambiguous_outcomes.append({
                "id": did,
                "reason": "conflicting correct values",
                "records": [rec for _, rec in outs],
                "family": dec["family"],
                "qtype": dec["qtype"],
            })
            continue
        if len(outs) > 1:
            # A0.3 (2026-09-18): a second outcome for the same decision id
            # is refused, not silently joined via "pick the latest", even
            # when both agree on `correct`. Which write actually happened
            # is unknowable from here (the ledger's own append-only,
            # order-tolerant design means two outcomes CAN arrive in
            # either order for the same id), so this id is excluded from
            # precision entirely, the same way ambiguous_outcomes already
            # is, and named here instead.
            duplicate_outcomes.append({
                "id": did,
                "reason": "duplicate outcome, same correct value",
                "records": [rec for _, rec in outs],
                "family": dec["family"],
                "qtype": dec["qtype"],
            })
            continue
        _, chosen_rec = outs[0]
        joined.append({"decision": dec, "outcome": chosen_rec})

    orphan_outcomes = []
    outcomes_for_duplicate_decision_ids = []
    for did, outs in outcomes_by_id.items():
        if did in decisions_by_id:
            continue
        if did in duplicate_decision_ids:
            outcomes_for_duplicate_decision_ids.append({
                "id": did,
                "records": [rec for _, rec in outs],
            })
        else:
            orphan_outcomes.append({
                "id": did,
                "records": [rec for _, rec in outs],
                "family": _family_from_orphan_id(did),
            })

    return {
        "joined": joined,
        "unmatched_decisions": unmatched_decisions,
        "orphan_outcomes": orphan_outcomes,
        "outcomes_for_duplicate_decision_ids": outcomes_for_duplicate_decision_ids,
        "duplicate_decision_ids": sorted(duplicate_decision_ids),
        "duplicate_decision_families": duplicate_decision_families,
        "ambiguous_outcomes": ambiguous_outcomes,
        "duplicate_outcomes": duplicate_outcomes,
        "corrupt_decisions": corrupt_decisions,
        "corrupt_outcomes": corrupt_outcomes,
        "reliable": dec_reliable and out_reliable,
    }


def ledger_reliability(decisions_path, outcomes_path):
    """Item 1 (2026-09-19): "is the calibration ledger currently
    reliable" as a real, callable answer -- a function, not a print
    statement -- so a board generator or doctor script can ask it
    without knowing join()'s full return shape or scoping the question
    to any one family/qtype (report()'s "ledger_reliable" and
    "family_qtype_anomaly" already answer THAT narrower question for a
    caller that has a family in hand; this is the whole-ledger version
    for an operator who does not).

    Returns a dict:
      reliable            True only when join() could fully read both
                          ledger files (their segment directories could
                          be listed, no rotation race was caught) AND
                          neither ledger holds a single corrupt line.
                          An empty or not-yet-created ledger is reliable
                          (per _read_jsonl()'s own "missing file is an
                          empty ledger, never an error" rule) -- this
                          reports operator-visible DAMAGE, not mere
                          absence of data.
      reason              None when reliable, else a short, human
                          readable sentence naming what made it False:
                          an unreadable segment directory/rotation race,
                          and/or how many corrupt lines were found in
                          each file. Never fabricated: built only from
                          join()'s own real counts.
      corrupt_decisions   int, corrupt (unparseable or schema-invalid)
                          line count in the decisions ledger.
      corrupt_outcomes    int, same for the outcomes ledger.
      segment_read_ok     join()'s own "reliable" flag, passed through
                          unchanged: False when a segment directory
                          could not be listed or a rotation race was
                          caught mid-read (see join()'s own docstring).
    """
    j = join(decisions_path, outcomes_path)
    corrupt_decisions = len(j["corrupt_decisions"])
    corrupt_outcomes = len(j["corrupt_outcomes"])
    segment_read_ok = j["reliable"]
    reliable = segment_read_ok and corrupt_decisions == 0 and corrupt_outcomes == 0

    reasons = []
    if not segment_read_ok:
        reasons.append("a ledger segment directory could not be fully listed/read, "
                        "or a rotation race was caught mid-read")
    if corrupt_decisions:
        reasons.append("%d corrupt line(s) in the decisions ledger" % corrupt_decisions)
    if corrupt_outcomes:
        reasons.append("%d corrupt line(s) in the outcomes ledger" % corrupt_outcomes)

    return {
        "reliable": reliable,
        "reason": "; ".join(reasons) if reasons else None,
        "corrupt_decisions": corrupt_decisions,
        "corrupt_outcomes": corrupt_outcomes,
        "segment_read_ok": segment_read_ok,
    }


def _wilson_lower_bound(k, n, z=_Z95):
    """The 95 percent Wilson score interval's LOWER bound for k correct out
    of n trials. Returns None when n is 0 (no fabricated 0 or 1)."""
    if n == 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (center - margin) / denom


def _validate_bands(bands):
    if not bands:
        raise ValueError("bands must be a non-empty sequence")
    nums = []
    for b in bands:
        if isinstance(b, bool) or not isinstance(b, (int, float)):
            raise ValueError("every band bound must be a number, got %r" % (b,))
        if not (0.0 <= b <= 1.0):
            raise ValueError("every band bound must be within [0, 1], got %r" % (b,))
        nums.append(float(b))
    for i in range(1, len(nums)):
        if nums[i] <= nums[i - 1]:
            raise ValueError("bands must be strictly increasing, got %r" % (bands,))
    return tuple(nums)


def _ledger_anomaly_touches(j, family, qtype):
    """True when any duplicate, ambiguous, orphan or corrupt record in a
    join() result `j` touches (family, qtype): either it is directly
    attributable to that exact family/qtype, or it cannot be attributed
    to any family/qtype at all, in which case it is treated as if it
    touches every family/qtype (fail closed; an unattributable anomaly
    could belong to this one and there is no way to rule that out from
    here). Never scoped by framing: the review this fixes (C1, 2026-09-18)
    asks for family/qtype scoping only.

    C1 REGRESSION THIS FIXES: excluding a duplicated or ambiguous outcome
    from precision (join()'s existing, correct refusal to guess which
    write was real) silently SHRINKS the denominator a caller's threshold()
    call sees. A wrong answer duplicated twice removed itself, not just
    its duplicate, from the count -- so more duplicated wrong labels made
    the surviving sample look MORE precise, not less. threshold() must
    refuse to compute a threshold at all when the ledger it is reading is
    contaminated for this family/qtype, rather than silently compute one
    from whatever clean-looking remainder is left.

    ORPHAN OUTCOMES ARE SCOPED BY THEIR ID'S OWN "<family>:<...>" PREFIX
    (coordinator re-review, MAJOR, 2026-09-18), not treated as always
    ledger-wide: an outcome record itself never carries family/qtype (see
    _validate_outcome), but its id usually does, in the shape the seam
    writes ids. Only an orphan whose id carries no readable prefix (see
    _family_from_orphan_id) is ledger-wide; one mislabelled outcome for a
    real, named family must not disable acting for every OTHER family.
    qtype is never checked for an orphan match (only family): the id's
    prefix never names a qtype, so a family match blocks every qtype
    within that family rather than guessing which qtype it meant.
    """
    for item in j["ambiguous_outcomes"]:
        if item["family"] == family and item["qtype"] == qtype:
            return True
    for item in j["duplicate_outcomes"]:
        if item["family"] == family and item["qtype"] == qtype:
            return True
    for item in j["orphan_outcomes"]:
        orphan_family = item["family"]
        if orphan_family is None:
            return True  # unattributable: ledger-wide
        if orphan_family == family:
            return True  # attributed to this family, any qtype
    for pairs in j["duplicate_decision_families"].values():
        if (family, qtype) in pairs:
            return True
    for entry in j["corrupt_decisions"]:
        if entry["family"] is None:
            return True  # unattributable: ledger-wide
        if entry["family"] == family and entry["qtype"] == qtype:
            return True
    if j["corrupt_outcomes"]:
        # Same reasoning as orphan_outcomes: an outcome-shaped corrupt
        # line never carries trustworthy family/qtype.
        return True
    return False


def report(decisions_path, outcomes_path, family, qtype, framing=None, bands=DEFAULT_BANDS):
    """Per-confidence-band precision for one family, qtype and, optionally,
    one exact framing. A noul and a choice sharing a family name are never
    pooled: filtering is on family AND qtype AND (if given) framing,
    together.

    Returns a dict:
      rows                 one entry per band, each
                            {"lower", "upper", "count", "correct",
                             "precision", "wilson_lower_95", "note"}.
                            "upper" is None for the last band (unbounded
                            above; every valid confidence is <= 1.0, which
                            append_decision already enforces). When count
                            is 0, precision and wilson_lower_95 are None
                            and note explains why: a None here is a
                            missing measurement, never a 0.
      below_lowest_band     {"count", "correct", "precision",
                             "wilson_lower_95"} for decisions in scope
                            whose confidence is below bands[0]. Reported
                            explicitly rather than silently vanishing: a
                            caller asking for bands starting at 0.5 still
                            deserves to know a 0.2-confidence decision
                            existed and whether it was right.
      total_in_scope        count of joined (decision, outcome) pairs
                            matching family/qtype/framing. Equals the sum
                            of every band's count plus
                            below_lowest_band["count"] (below_lowest_band
                            is a subset view of the same in-scope pairs,
                            not an addition to them).
      unmatched_decisions_in_scope
                            ids of decisions matching family/qtype/framing
                            with no outcome yet.
      ledger_reliable       join()'s own "reliable" flag, passed through
                            (round 4 fix, 2026-09-19): False when either
                            ledger's segment directory could not be
                            listed. threshold() checks this first, before
                            family_qtype_anomaly, and refuses with reason
                            "ledger unreadable" when it is False.
      ledger_wide           counts that are NOT scoped to this
                            family/qtype/framing, because the records they
                            describe (an orphan outcome, a corrupt line, a
                            duplicate decision id) carry no reliable
                            family/qtype of their own to filter by:
                            {"orphan_outcomes", "duplicate_decision_ids",
                             "ambiguous_outcomes", "duplicate_outcomes",
                             "corrupt_decisions", "corrupt_outcomes"},
                            each a plain count. duplicate_outcomes (A0.3)
                            counts ids refused for a second, same-value
                            outcome record: see join()'s own docstring.
    """
    family = _require_nonempty_str({"family": family}, "family")
    qtype = _require_nonempty_str({"qtype": qtype}, "qtype")
    if qtype not in QTYPES:
        raise ValueError("qtype must be one of %s, got %r" % (sorted(QTYPES), qtype))
    if framing is not None:
        framing = _require_nonempty_str({"framing": framing}, "framing")
    bands = _validate_bands(bands)

    j = join(decisions_path, outcomes_path)

    def in_scope(dec):
        if dec["family"] != family or dec["qtype"] != qtype:
            return False
        if framing is not None and dec["framing"] != framing:
            return False
        return True

    filtered = [item for item in j["joined"] if in_scope(item["decision"])]
    unmatched_in_scope = [dec["id"] for dec in j["unmatched_decisions"] if in_scope(dec)]

    below = [item for item in filtered if item["decision"]["confidence"] < bands[0]]
    below_n = len(below)
    below_k = sum(1 for item in below if item["outcome"]["correct"])
    below_lowest_band = {
        "count": below_n,
        "correct": below_k,
        "precision": (below_k / below_n) if below_n else None,
        "wilson_lower_95": _wilson_lower_bound(below_k, below_n),
    }

    rows = []
    for i, lower in enumerate(bands):
        upper = bands[i + 1] if i + 1 < len(bands) else None
        band_items = [
            item for item in filtered
            if item["decision"]["confidence"] >= lower
            and (upper is None or item["decision"]["confidence"] < upper)
        ]
        n = len(band_items)
        k = sum(1 for item in band_items if item["outcome"]["correct"])
        if n == 0:
            precision = None
            wl = None
            note = "no decisions in this band"
        else:
            precision = k / n
            wl = _wilson_lower_bound(k, n)
            note = None
        rows.append({
            "lower": lower,
            "upper": upper,
            "count": n,
            "correct": k,
            "precision": precision,
            "wilson_lower_95": wl,
            "note": note,
        })

    return {
        "family": family,
        "qtype": qtype,
        "framing": framing,
        "rows": rows,
        "below_lowest_band": below_lowest_band,
        "total_in_scope": len(filtered),
        "unmatched_decisions_in_scope": unmatched_in_scope,
        "family_qtype_anomaly": _ledger_anomaly_touches(j, family, qtype),
        "ledger_reliable": j["reliable"],
        "ledger_wide": {
            "orphan_outcomes": len(j["orphan_outcomes"]),
            "duplicate_decision_ids": len(j["duplicate_decision_ids"]),
            "ambiguous_outcomes": len(j["ambiguous_outcomes"]),
            "duplicate_outcomes": len(j["duplicate_outcomes"]),
            "corrupt_decisions": len(j["corrupt_decisions"]),
            "corrupt_outcomes": len(j["corrupt_outcomes"]),
        },
    }


def threshold(decisions_path, outcomes_path, family, qtype, target_precision, min_n,
              framing=None, bands=DEFAULT_BANDS):
    """The lowest confidence band whose Wilson lower bound meets or exceeds
    target_precision, using only bands with at least min_n joined
    decisions. Never falls back to a default: returns
    {"threshold": None, "reason": "..."} when no band qualifies, naming
    which of the two reasons applies.

    Returns {"threshold": <band lower bound> or None, "reason": None or
    str, "rows": the report()'s rows, "below_lowest_band": ...}.

    LEDGER UNREADABLE GATE (round 4 fix, 2026-09-19, Major 1, checked
    FIRST, before the anomaly gate below): when either ledger's segment
    directory could not be listed at all, this always returns
    {"threshold": None, "reason": "ledger unreadable", ...} -- distinct
    from "ledger anomaly" below, because this is not about a record this
    read COULD see but did not trust (a duplicate, a corrupt line); it is
    about a read that could not see everything that is there. Reviewer
    probe: a ledger directory chmod 0300 (traversable, not listable) used
    to read as "no rotated history", computing a threshold -- and act
    mode acting on it -- from only the active file's partial view. See
    join()'s own "reliable" and report()'s "ledger_reliable".

    LEDGER ANOMALY GATE (C1, 2026-09-18 review fix, before anything else
    below is even computed): when any duplicate, ambiguous, orphan or
    corrupt record touches this family and qtype -- or is ledger-wide
    because it cannot be attributed to any family/qtype at all -- this
    always returns {"threshold": None, "reason": "ledger anomaly", ...},
    regardless of what the eligible bands below would otherwise compute.
    A caller granting autonomy from a contaminated read of the ledger is
    exactly the regression this gate exists to close: see
    _ledger_anomaly_touches() and report()'s "family_qtype_anomaly".
    """
    if isinstance(target_precision, bool) or not isinstance(target_precision, (int, float)):
        raise ValueError("target_precision must be a number, got %r" % (target_precision,))
    if not (0.0 < target_precision <= 1.0):
        raise ValueError("target_precision must be within (0, 1], got %r" % (target_precision,))
    if isinstance(min_n, bool) or not isinstance(min_n, int) or min_n < 1:
        raise ValueError("min_n must be a positive integer, got %r" % (min_n,))

    rep = report(decisions_path, outcomes_path, family, qtype, framing=framing, bands=bands)
    rows = rep["rows"]

    if not rep["ledger_reliable"]:
        return {
            "threshold": None,
            "reason": "ledger unreadable",
            "rows": rows,
            "below_lowest_band": rep["below_lowest_band"],
        }

    if rep["family_qtype_anomaly"]:
        return {
            "threshold": None,
            "reason": "ledger anomaly",
            "rows": rows,
            "below_lowest_band": rep["below_lowest_band"],
        }

    eligible = [
        row for row in rows
        if row["count"] >= min_n and row["wilson_lower_95"] is not None
        and row["wilson_lower_95"] >= target_precision
    ]
    if eligible:
        return {
            "threshold": min(row["lower"] for row in eligible),
            "reason": None,
            "rows": rows,
            "below_lowest_band": rep["below_lowest_band"],
        }

    any_min_n = any(row["count"] >= min_n for row in rows)
    if not any_min_n:
        reason = "no band has at least min_n=%d joined decisions" % min_n
    else:
        reason = (
            "no band with at least min_n=%d joined decisions reached a Wilson "
            "lower bound of target_precision=%.6f" % (min_n, target_precision)
        )
    return {
        "threshold": None,
        "reason": reason,
        "rows": rows,
        "below_lowest_band": rep["below_lowest_band"],
    }


#: The literal source string every seed_from_eval() outcome record carries.
#: Pinned as a constant, not reconstructed, so a caller filtering the
#: ledger for this exact provenance string cannot be broken by a rewording.
SEED_SOURCE = "orchestrator eval 2026-09-18"

#: The eval system name this estate's own eval harness used for Jev's own
#: answers (as opposed to a paraphrased-framing run, a second Jev call, or
#: a different model entirely run for comparison in the same file).
SEED_SYSTEM = "jev"


def seed_from_eval(results_path, dataset_path, decisions_out_path, outcomes_out_path,
                    system=SEED_SYSTEM, source=SEED_SOURCE):
    """Loads this session's labelled eval rows and appends them to the
    ledger as decisions plus outcomes, so the calibration report has real
    data to measure before any live traffic has been recorded.

    results_path is a line-delimited JSON file, each line at least
    {"task", "item", "pred", "conf", "correct", "model", "cost"} plus a
    "system" field this function filters on. dataset_path is a JSON object
    mapping "<family letter>_<name>" keys to {"instructions": <str>, ...};
    the family letter before the first underscore is matched against each
    result row's "task" to find that family's instructions text, which is
    hashed to produce a stable `framing` value (every item in one eval
    family was asked with the same wording, so one hash per family is
    correct here, not a defect: a caller with per-item wording would need
    a different framing source).

    Neither results_path nor dataset_path carries a per-row timestamp,
    only the eval's own recorded fields. Rather than fabricate one (this
    estate's own standing rule: never present a guessed value as measured),
    every seeded record's "at" is the results file's own last-modified
    time, read fresh from the filesystem in this call, converted to UTC.
    This is a real, checkable fact about when the eval data was produced,
    not an invented one; it is documented here so nobody downstream reads
    it as a per-decision timestamp.

    Returns {"status": "NO-DATA", "reason": "..."} when either input file
    is absent: this is not a pass, and it is never silently treated as
    "zero decisions" by the caller. On success, returns
    {"status": "OK", "decisions_written": int, "outcomes_written": int,
    "skipped": [{"task", "item", "reason"}]}.
    """
    if not os.path.isfile(results_path):
        return {"status": "NO-DATA", "reason": "results file not found: %s" % results_path}
    if not os.path.isfile(dataset_path):
        return {"status": "NO-DATA", "reason": "dataset file not found: %s" % dataset_path}

    mtime = os.path.getmtime(results_path)
    at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            dataset = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "NO-DATA", "reason": "dataset file could not be read: %s" % exc}
    if not isinstance(dataset, dict):
        return {"status": "NO-DATA", "reason": "dataset file is not a JSON object"}

    family_framing = {}
    for key, val in dataset.items():
        if "_" not in key or not isinstance(val, dict):
            continue
        letter = key.split("_", 1)[0]
        instructions = val.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            family_framing[letter] = hashlib.sha256(instructions.encode("utf-8")).hexdigest()[:16]

    decisions_written = 0
    outcomes_written = 0
    skipped = []

    try:
        results_file = open(results_path, "r", encoding="utf-8")
    except OSError as exc:
        return {"status": "NO-DATA", "reason": "results file could not be opened: %s" % exc}

    with results_file:
        for line_no, raw in enumerate(results_file, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                skipped.append({"task": None, "item": None, "reason": "line %d not valid JSON: %s" % (line_no, exc)})
                continue
            if not isinstance(row, dict) or row.get("system") != system:
                continue

            family = row.get("task")
            item = row.get("item")
            pred = row.get("pred")
            conf = row.get("conf")
            correct = row.get("correct")

            if not isinstance(family, str) or family not in family_framing:
                skipped.append({"task": family, "item": item, "reason": "no dataset instructions found for this family"})
                continue
            if not isinstance(item, str) or not item:
                skipped.append({"task": family, "item": item, "reason": "missing or invalid item id"})
                continue
            if pred is None or conf is None:
                skipped.append({"task": family, "item": item, "reason": "no prediction or confidence recorded (likely an eval-time failure row)"})
                continue
            if not isinstance(correct, bool):
                skipped.append({"task": family, "item": item, "reason": "no boolean correct field recorded"})
                continue

            qtype = "noul" if isinstance(pred, bool) else "choice"
            decision_id = "%s:%s" % (family, item)
            model = row.get("model")
            if not isinstance(model, str) or not model.strip():
                model = system
            cost = row.get("cost", 0.0)
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                cost = 0.0

            decision = {
                "id": decision_id,
                "family": family,
                "qtype": qtype,
                "framing": family_framing[family],
                "answer": pred,
                "prob": float(conf),
                "confidence": float(conf),
                "model": model,
                "cost": float(cost),
                "at": at,
            }
            outcome = {
                "id": decision_id,
                "correct": correct,
                "source": source,
                "at": at,
            }
            try:
                append_decision(decisions_out_path, decision)
                decisions_written += 1
                append_outcome(outcomes_out_path, outcome, decisions_path=decisions_out_path)
                outcomes_written += 1
            except ValueError as exc:
                skipped.append({"task": family, "item": item, "reason": "validation failed: %s" % exc})

    return {
        "status": "OK",
        "decisions_written": decisions_written,
        "outcomes_written": outcomes_written,
        "skipped": skipped,
    }


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "selftest":
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dp = os.path.join(d, "decisions.jsonl")
            op = os.path.join(d, "outcomes.jsonl")
            append_decision(dp, {
                "id": "x1", "family": "f", "qtype": "noul", "framing": "h1",
                "answer": True, "prob": 0.9, "confidence": 0.9, "model": "m",
                "cost": 0.0, "at": "2026-09-18T00:00:00Z",
            })
            append_outcome(op, {"id": "x1", "correct": True, "source": "test", "at": "2026-09-18T00:01:00Z"},
                           decisions_path=dp)
            rep = report(dp, op, "f", "noul")
            assert rep["rows"][2]["count"] == 1, rep
            print("selftest OK")
        return 0
    print("usage: jev_calibration.py selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main())
