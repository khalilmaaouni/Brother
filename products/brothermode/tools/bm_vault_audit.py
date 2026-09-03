#!/usr/bin/env python3
"""bm_vault_audit: the access audit -- every recall records its principal (WBS row VB7-04).

WHY THIS EXISTS. bm_vault_ledger.py (VB2-05) answers "what did a recall actually read": one
line per recall with the served note ids, paths and content hashes, so a past answer can be
traced back to its exact sources. That is a CONTENT-integrity record, not an access record: it
never asked "who". A real access audit needs the second question answered too, on the
same trigger (every recall, never a sampled subset), and the two answers must never live in one
file, because the ledger's paths and content hashes are exactly the kind of detail an
access-audit record must NOT leak about a note a principal was denied.

SENSITIVITY. The free-text query itself is written into this file verbatim, and a query is
itself sensitive material: it can name a person, a project, or a fact the asker never meant
to put in a shareable log. This audit is LOCAL-ONLY (the file is created 0600, owner read/
write only, never group or world readable), and how long a row should be kept is UNDECIDED,
pending the VB7-05/VB7-06 rulings; nothing here rotates or expires a row on its own yet.

THE RECORD, one JSON line per recall, append-only, next to the answer ledger:

  {"ts": "...", "event_id": "...", "principal": "...", "query": "...",
   "served_ids": ["...", ...], "withheld_count": N, "purpose": "..."}

VB3-04 adds two fields. purpose is recorded on EVERY row (the caller's --purpose, or
the literal string "NO-DATA" when none was given, the same never-a-guess stance
principal already takes): the row's own requirement that a policy decision's purpose
is auditable, not merely assumed from the query text. degraded is present only on a
row where the enterprise-mode fail-closed fallback actually fired (the access policy
module was missing or its decision crashed, so restricted notes were withheld by a
fallback definition rather than by the real policy) -- omitted entirely otherwise, the
same optional-field shape refused already uses below, so every row written before
this existed keeps its exact prior shape.

NOTE IDS ONLY, never a title, a path or a body excerpt: the audit trail itself must never
become a second way to leak what a principal could not see. served_ids names only notes
actually printed to the caller (bm_vault.py's own served set, after supersession, D12
candidate withholding, freshness staleness and the VB2-01 access policy all ran);
withheld_count is every note held back for ANY of those reasons, policy included, so a
principal's own row is honest about how much was withheld even though it never names which
notes.

WHERE IT LIVES. Next to bm_vault.py's own answer ledger: this file derives its path from
bm_vault.LEDGER_PATH (loaded by path below, the same dynamic-import pattern every sibling
contract module in this tree already uses), never by recomputing the ~/.claude resolution
independently. Two files answering "what was read" and "who read it" must sit in the one
directory bm_vault.py itself resolves, or a future change to that resolution could silently
split them onto two machines' different ideas of "the vault's local state directory".

  search   --principal NAME, --note ID, --since ISO, --until ISO, any combination (every
           filter given must match: AND across them). Zero matches prints one clean
           "0 record(s)" line, never nothing silent. An absent or unreadable audit file is
           NO-DATA at exit 2, never a quiet pass.

Exit codes: 0 search ran (0 or more matches), 2 NO-DATA (file absent, unreadable, or a bad
--since/--until value). Python 3.9, standard library only. Writes nothing but its own
append-only file: search never mutates a record, same report-only stance bm_vault_ledger.py
already takes on its own file.

SELF-ECHO PROVENANCE MARKER (VB6-06). A served answer that finds its way back into the vault
through the intake door (someone pastes a recall's output into a note, or a downstream system
re-ingests what bm_vault_serve.py handed it) must never be mistaken for a second, independent
source agreeing with the first: it is the same source, echoed. marker_line() is the ONE place
that formats the one-line, machine-readable tag both bm_vault.py's recall output and
bm_vault_serve.py's JSON response carry to say "this content was derived from vault event X";
detect_marker() is the ONE place that reads it back, so bm_vault_intake.py's admit path (and
anything else) can never drift into a second idea of what the tag looks like.

KNOWN LIMITATION (MAJOR review, VB6-06). detect_marker() matches the marker's SHAPE, not its
provenance: a hand-written line of the same shape (any hex32 after "event=") matches equally
well, and would have been trusted as a genuine echo if nothing else checked it. has_event()
below is the fix for that one gap only: bm_vault_intake.py's admit path now looks the detected
event_id up against this file's own rows before trusting it, so a forged marker is told apart
from a real one. What is still NOT covered, and is not implemented anywhere in this tree today:
an adversary who simply strips the marker line before pasting served content back in defeats
detection entirely, because there is no second, content-level check that a note's text matches
a served answer. Content-level matching against served answers (a hash or near-duplicate check
against bm_vault.py's own answer ledger, independent of any marker line) is a FUTURE control,
not a present one; this module and bm_vault_intake.py catch an honest tooling round trip, never
a deliberate strip-and-resubmit.

No em or en dashes anywhere in this file.
"""
import datetime
import importlib.util
import json
import os
import re
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_bm_vault():
    """Dynamic import by path, the same defensive pattern bm_vault.py's own
    _load_bm_vault_policy and its neighbors use: a bare `import bm_vault` only resolves by
    accident of sys.path, and this file sets up none of its own. Used for exactly one
    constant, LEDGER_PATH, so this module's audit path can never drift from the ledger's."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault", os.path.join(_TOOLS_DIR, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AUDIT_PATH = os.path.join(os.path.dirname(_load_bm_vault().LEDGER_PATH), "bm_vault_audit.jsonl")


def new_event_id():
    """One id per recall, shared by the caller between its own bookkeeping and this file's
    append() call, so a record here can be correlated back to the recall that produced it."""
    return uuid.uuid4().hex


#: The marker's exact shape, matched by detect_marker below. A bare "event=<hex32>" line,
#: never JSON and never wrapped, so it survives a plain copy-paste of a terminal's printed
#: output or a JSON string field equally.
_MARKER_PREFIX = "derived-from-vault"
_MARKER_RE = re.compile(r"^derived-from-vault: event=([0-9a-f]{32})\s*$", re.M)


def marker_line(event_id):
    """The one-line, machine-readable provenance tag: this exact event_id, plain text. Sole
    owner of the marker's shape; detect_marker below is the sole reader, so emit and detect
    can never drift apart. Callers: bm_vault.py's cmd_recall appends this as the last line of
    a recall's printed output; bm_vault_serve.py mirrors the same event_id as a JSON field
    rather than making a caller parse the printed line out of the raw text."""
    return "%s: event=%s" % (_MARKER_PREFIX, event_id)


def detect_marker(text):
    """The event_id from the first derived-from-vault marker line in text, or None when no
    marker is present (ordinary, unmarked content -- the common case). Used by
    bm_vault_intake.py's admit path to classify content that is itself an echo of a served
    vault answer, so it is never scored as independent testimony agreeing with the original."""
    if not text:
        return None
    m = _MARKER_RE.search(text)
    return m.group(1) if m else None


def has_event(event_id):
    """(found, no_data_reason). True when event_id is a real row already appended to
    this audit log; the ONE place that tells a genuine recall marker apart from a
    hand-written hex32 line of the same shape (see the KNOWN LIMITATION paragraph on
    detect_marker above -- this is the fix for that one gap). no_data_reason is set,
    never both, when the audit file is absent or unreadable: that is "could not check",
    never the same thing as "checked, and it is not there", so a caller must not fold
    the two into one silent False."""
    rows = _read_rows()
    if rows is None:
        return False, ("NO-DATA: no access audit at %s (no recall has appended one yet)"
                        % AUDIT_PATH)
    for row in rows:
        if row.get("event_id") == event_id:
            return True, None
    return False, None


def append(principal, query, served_ids, withheld_count, event_id, refused=None,
           purpose=None, degraded=None):
    """One JSON line, append-only. AVAILABILITY OVER BOOKKEEPING, the identical stance
    bm_vault.py's own _append_ledger already takes for the content ledger: a write failure
    here (unwritable directory, full disk) must never break the recall it is auditing, so it
    prints one stderr warning and returns rather than raising. os.open(O_APPEND) below
    PIPE_BUF is POSIX-atomic against other appenders to the same file, so concurrent recalls
    interleave whole lines, never partial ones.

    principal absent or empty records "NO-DATA": never a guess, never a skipped append -- an
    access audit that quietly omits the unlabeled caller is worse than one that names it
    can't tell who asked. purpose (VB3-04) follows the identical rule: absent or empty
    records "NO-DATA" too, and is always present in the row, never omitted -- unlike refused
    and degraded below, a decision's purpose is asked for on every row, not only an
    exceptional one.

    refused (VB7-05): a truthy reason string marks this row as a REFUSAL, not merely an
    ordinary partial withholding -- the principal registry denied the caller everything,
    e.g. because they are revoked. Omitted from the row entirely when None, so every row
    written before this field existed, and every ordinary policy-trimmed row today, keeps
    its exact prior shape.

    degraded (VB3-04): a truthy reason string marks this row as one where the enterprise
    fail-closed fallback actually fired -- the real access policy could not be consulted
    (missing module, or a crashed decision) and bm_vault.py fell back to withholding only
    restricted-flagged notes. Same omit-when-None shape as refused, for the same reason."""
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_id": event_id,
        "principal": principal if principal else "NO-DATA",
        "query": query,
        "served_ids": list(served_ids or []),
        "withheld_count": int(withheld_count or 0),
        "purpose": purpose if purpose else "NO-DATA",
    }
    if refused:
        row["refused"] = refused
    if degraded:
        row["degraded"] = degraded
    try:
        line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        # 0600, never 0644: the query text written into every row is itself sensitive (see
        # the module docstring), so this file is owner-only from the moment it is created.
        fd = os.open(AUDIT_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write("bm_vault_audit: append failed (%s); recall continues\n" % e)
    return row


def _read_rows():
    """All audit rows in file order, or None when the file is absent or carries no readable
    row -- the same three-way result bm_vault_ledger.py's own _read_rows already returns, for
    the same reason: an absent file and a present-but-empty one both mean nothing has been
    proven yet, and NO-DATA says so rather than a silent zero. A malformed line (a partial
    write from a crash mid-append) is skipped with a stderr warning, never aborts the read."""
    if not os.path.exists(AUDIT_PATH):
        return None
    rows = []
    with open(AUDIT_PATH, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                sys.stderr.write("bm_vault_audit: skipping malformed line %d (%s)\n" % (i, e))
    return rows or None


def _parse_iso(s):
    """A --since/--until value, tolerant of a trailing Z (not accepted by
    datetime.fromisoformat before 3.11, and this estate is pinned to 3.9). A naive result
    (no offset given) is assumed UTC, matching the offset append() always writes, so a
    naive boundary still compares correctly against every recorded row."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _str_arg(args, key):
    v = args.get(key)
    return v if isinstance(v, str) else None


def cmd_search(args):
    rows = _read_rows()
    if rows is None:
        print("NO-DATA: no access audit at %s (no recall has appended one yet)" % AUDIT_PATH)
        return 2
    principal = _str_arg(args, "principal")
    note = _str_arg(args, "note")
    since_raw, until_raw = _str_arg(args, "since"), _str_arg(args, "until")
    try:
        since = _parse_iso(since_raw) if since_raw else None
        until = _parse_iso(until_raw) if until_raw else None
    except ValueError as e:
        print("NO-DATA: bad --since/--until value: %s" % e)
        return 2
    matched = []
    for row in rows:
        if principal and row.get("principal") != principal:
            continue
        if note and note not in (row.get("served_ids") or []):
            continue
        if since or until:
            try:
                ts = _parse_iso(row.get("ts", ""))
            except ValueError as e:
                sys.stderr.write("bm_vault_audit: skipping row with unparseable ts "
                                 "%r under a since/until filter (%s)\n" % (row.get("ts"), e))
                continue
            if since and ts < since:
                continue
            if until and ts > until:
                continue
        matched.append(row)
    if not matched:
        print("0 record(s)")
        return 0
    print("%d record(s)" % len(matched))
    print("caveat: principal values are client-declared, not authenticated identities.")
    for row in matched:
        print("\n[%s] event=%s principal=%s"
              % (row.get("ts", "?"), row.get("event_id", "?"), row.get("principal", "?")))
        print("  query: %s" % row.get("query", ""))
        print("  served: %s" % (", ".join(row.get("served_ids") or []) or "(none)"))
        print("  withheld: %d" % row.get("withheld_count", 0))
        if row.get("purpose"):
            print("  purpose: %s" % row["purpose"])
        if row.get("refused"):
            print("  refused: %s" % row["refused"])
        if row.get("degraded"):
            print("  degraded: %s" % row["degraded"])
    return 0


def _parse(argv):
    args, key = {}, None
    for a in argv:
        if a.startswith("--"):
            key = a[2:]
            args.setdefault(key, True)
        elif key:
            args[key] = a
            key = None
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fns = {"search": cmd_search}
    if argv[0] not in fns:
        sys.stderr.write("bm_vault_audit: unknown command %r; known: %s\n"
                         % (argv[0], ", ".join(sorted(fns))))
        return 2
    return fns[argv[0]](_parse(argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
