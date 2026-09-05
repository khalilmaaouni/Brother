#!/usr/bin/env python3
"""bm_vault_read_audit: an IMMUTABLE, HASH CHAINED record of who read what (WBS row V5).

WHY THIS EXISTS, AND WHY IT IS NOT bm_vault_audit.py. bm_vault_audit.py (VB7-04)
already records "who recalled what": one append-only JSON line per recall, with the
principal, the query text, and the served note ids. That answers WHO ASKED. It is
append-only in the ordinary sense (new lines go on the end), but nothing in that file
lets a reader PROVE the log has not been edited or trimmed after the fact: a row
deleted or rewritten by hand leaves no trace in the file itself. VAULT-HARDENING-SCOPE
V5 names exactly that gap ("no immutable audit of who read what") as new capability,
parked under the freeze until the founder ordered it by name (2026-09-05). This module
is that capability: the same kind of record, but HASH CHAINED, so a verifier can walk
the file and prove whether it is intact.

THE RECORD, one JSON line per read event, append-only:

  {"ts": "...", "session": "...", "note": "...", "query_sha256": "...",
   "surface": "...", "prev_hash": "<64 hex>", "hash": "<64 hex>"}

  ts        UTC ISO-8601, same shape every other writer in this tree already uses.
  session   the hook payload's session id when the caller has one, else the literal
            string "NO-DATA" (never a guess, the same law bm_vault_audit.py's own
            principal field already follows).
  note      the note actually shown: its note id when the caller has one, else its
            path. Never a title or a body excerpt; same NOTE-IDS-ONLY posture
            bm_vault_audit.py's own module docstring already states, for the same
            reason (the audit trail must never become a second way to leak content).
  query_sha256  sha256 hex of the query TEXT, never the text itself. The query can
            itself name a person or a fact the asker never meant to log verbatim
            (bm_vault_audit.py's own SENSITIVITY note says as much about its own
            query field); this file avoids that exposure entirely by hashing.
  surface   which caller produced this read: "recall_hook" (vault_recall_hook.py's
            PreToutUse check), "bm_vault_recall" (bm_vault.py's cmd_recall). A third
            surface, the contradiction resolver, is NOT wired in (see WHAT THIS DOES
            NOT COVER below).
  prev_hash the previous line's own "hash" field, or GENESIS_HASH ("0" * 64) for the
            first line ever written.
  hash      sha256 hex over the canonical (sort_keys) JSON encoding of this row's OWN
            five fields above (ts, session, note, query_sha256, surface) PLUS
            prev_hash -- never over anything computed later, so a verifier can
            recompute it from the row alone.

THE CHAIN. Each row commits to the row before it by embedding that row's hash as its
own prev_hash, and then to its own content by hashing that content plus prev_hash.
Editing any field of any row changes what verify recomputes for that row's "hash",
so the stored value no longer matches; deleting or reordering a row breaks the link
between the row that follows it (whose prev_hash names the row that is now missing or
displaced) and whatever row actually precedes it on disk. Both are structural: a
verifier does not need a separate signature or external ledger to detect either.

AVAILABILITY OVER BOOKKEEPING, the identical stance bm_vault.py's own _append_ledger
and bm_vault_audit.py's own append already take: record_read() never raises into its
caller. A read must always proceed whether or not it could be logged. A write failure
(unwritable directory, full disk, a broken chain-read) prints exactly one line
"NO-DATA: read audit not recorded (<reason>)" to stderr and returns; it is reported,
never silent, but it is never fatal.

WHERE IT LIVES. Same directory as bm_vault.py's own LEDGER_PATH and bm_vault_audit.py's
own AUDIT_PATH (loaded by path, the same dynamic-import pattern every sibling contract
module in this tree already uses) -- never a second, independently recomputed idea of
where "the vault's local state directory" is. That directory is BrotherMode's own
config dir (brother_paths.config_dir(), ~/.claude by default on this machine), NOT the
real Kay Vault under ~/Documents: the constitution's 99-System folder is where the real
vault's own machinery lives, but bm_vault.py's own answer ledger and access audit have
never written there, and this file follows the writers it was told to mirror rather
than opening a second location.

WHAT VERIFY DOES NOT PROVE. This chain proves the log has not been altered SINCE this
tool's own record_read() wrote each line. It records reads made by Brother's own
tools (the recall hook, bm_vault recall) -- it says nothing about a person or another
program opening the vault's markdown files directly, outside these tools, which leaves
no row here at all. It is a chain of custody over Brother's own recall path, not a
filesystem-level access log.

CLI:

  verify [vault-dir]     Walk the chain at vault-dir/bm_vault_read_audit.jsonl (or the
                          module's own default path when vault-dir is omitted). Exit 0
                          and the event count on an intact chain; exit 1 naming the
                          first broken line (tampered, deleted, or reordered) on a
                          broken one; exit 2 NO-DATA when no log exists yet.
  who-read <note> [--root vault-dir]
                          List every read event recorded for one note (by id or path),
                          in file order. Exit 0 always (an empty list still exits 0,
                          same "0 record(s)" shape bm_vault_audit.py's own search
                          command already uses); exit 2 NO-DATA when no log exists.

Python 3.9, standard library only. No em or en dashes anywhere in this file.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

#: The first row's prev_hash: 64 zero hex digits, never a real sha256 output (which is
#: astronomically unlikely to be all zero), so genesis is unmistakable in the file itself.
GENESIS_HASH = "0" * 64

_LOG_NAME = "bm_vault_read_audit.jsonl"

#: Test and operator override, the same env-first shape vault_recall_hook.py's own
#: BM_HOOK_OUTCOMES already uses: a full file path, not a directory.
_ENV_OVERRIDE = "BM_READ_AUDIT_PATH"


def _load_bm_vault():
    """Dynamic import by path, the same defensive pattern bm_vault_audit.py's own
    _load_bm_vault uses: a bare `import bm_vault` only resolves by accident of sys.path,
    and this file sets up none of its own. Used for exactly one constant, LEDGER_PATH,
    so this file's default directory can never drift from the ledger's and the access
    audit's."""
    spec = importlib.util.spec_from_file_location(
        "bm_vault", os.path.join(_TOOLS_DIR, "bm_vault.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_path():
    """The log path this module writes to when no explicit path or --root is given:
    the env override if set, else the same directory as bm_vault.py's own LEDGER_PATH.
    Computed lazily (called at use time, never cached at import time) so a test that
    sets BM_READ_AUDIT_PATH or HOME right before calling still takes effect."""
    override = os.environ.get(_ENV_OVERRIDE)
    if isinstance(override, str) and override.strip():
        return override.strip()
    bm_vault = _load_bm_vault()
    return os.path.join(os.path.dirname(bm_vault.LEDGER_PATH), _LOG_NAME)


def _row_hash(ts, session, note, query_sha256, surface, prev_hash):
    """sha256 hex over the canonical (sort_keys) encoding of exactly these six fields.
    The SAME function is used to compute a fresh row's hash at write time and to
    recompute the expected hash of a stored row at verify time, so the two can never
    drift into two different ideas of what is being committed to."""
    canonical = json.dumps(
        {"ts": ts, "session": session, "note": note, "query_sha256": query_sha256,
         "surface": surface, "prev_hash": prev_hash},
        sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_hash(path):
    """The last line's own "hash" field, GENESIS_HASH when the file is absent or empty,
    or None when the file exists but its last non-blank line will not parse (a torn
    write from a crash mid-append): appending on top of an unreadable tail would silently
    orphan the chain, so the caller treats None as a write failure rather than guessing
    a prev_hash.
    ponytail: full-file linear scan per append, same choice bm_vault.py's own
    _ledger_lookup already made and documented at the answer ledger's append rate;
    revisit with a tail-only read only if this is ever measured hot."""
    if not os.path.exists(path):
        return GENESIS_HASH
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last = line
    if last is None:
        return GENESIS_HASH
    try:
        row = json.loads(last)
    except ValueError:
        return None
    h = row.get("hash")
    return h if isinstance(h, str) else None


def record_read(note, surface, session=None, query=None, path=None):
    """Append one hash-chained read-event row. NEVER RAISES: a read must always proceed
    whether or not this call succeeds. On any failure (path unwritable, chain tail
    unreadable) prints exactly one NO-DATA line to stderr and returns without writing.

    note      the note id or path actually shown to the reader. Required; a falsy value
              is recorded as "NO-DATA" rather than skipping the row (a read audit that
              silently drops rows it cannot label is worse than one that labels them
              honestly).
    surface   which caller is recording this read, e.g. "recall_hook" or
              "bm_vault_recall". Required the same way.
    session   the hook payload's session id, or None when the caller has none (the CLI
              recall path never sees one); recorded as "NO-DATA" when absent.
    query     the query TEXT, or None; only its sha256 is ever written to disk.
    path      an explicit log file path (tests, or a caller with its own root); defaults
              to default_path() above.
    """
    target = path if path else default_path()
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    session_v = session if (isinstance(session, str) and session) else "NO-DATA"
    note_v = note if (isinstance(note, str) and note) else "NO-DATA"
    surface_v = surface if (isinstance(surface, str) and surface) else "NO-DATA"
    query_sha256 = (hashlib.sha256(query.encode("utf-8")).hexdigest()
                    if isinstance(query, str) and query else "NO-DATA")
    try:
        prev_hash = _last_hash(target)
        if prev_hash is None:
            sys.stderr.write(
                "NO-DATA: read audit not recorded (chain tail at %s will not parse); "
                "read continues\n" % target)
            return
        this_hash = _row_hash(ts, session_v, note_v, query_sha256, surface_v, prev_hash)
        row = {"ts": ts, "session": session_v, "note": note_v,
               "query_sha256": query_sha256, "surface": surface_v,
               "prev_hash": prev_hash, "hash": this_hash}
        line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        d = os.path.dirname(target)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        # 0600, the same owner-only mode bm_vault_audit.py's own append() already uses:
        # this file names notes actually read, which is exactly the kind of detail an
        # access record must not leak to other local accounts.
        fd = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write("NO-DATA: read audit not recorded (%s); read continues\n" % e)


def _read_rows(path):
    """All rows in file order, or None when the file is absent. A malformed line is
    returned as-is (a dict with a "_malformed" marker and the raw text) rather than
    skipped, because verify must be able to name a corrupted line as the break point,
    not silently pretend it was never there."""
    if not os.path.exists(path):
        return None
    rows = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                rows.append({"_malformed": True, "_lineno": i, "_raw": line})
    return rows


def verify_chain(path):
    """(status, message, count). status is one of:
      "ok"       chain intact; count is the number of rows verified, message is None.
      "broken"   the first broken line found; message names it (1-based, counting only
                 non-blank lines), count is the number of rows that verified clean
                 before it. Causes: a malformed line, a row whose recomputed hash does
                 not match its stored "hash" (tampered), or a row whose prev_hash does
                 not match the actual previous row's hash (deleted or reordered).
      "no_data"  no log exists at path at all; message names the path, count is None.
    Three distinct outcomes on purpose: "no log yet" and "log exists but is broken"
    must never collapse into the same caller-visible result, or a verifier could not
    tell a fresh install apart from a tampered one."""
    rows = _read_rows(path)
    if rows is None:
        return "no_data", "no read audit at %s" % path, None
    expected_prev = GENESIS_HASH
    for i, row in enumerate(rows, 1):
        if row.get("_malformed"):
            return "broken", "line %d is not valid JSON" % row.get("_lineno", i), i - 1
        if row.get("prev_hash") != expected_prev:
            return "broken", ("line %d: prev_hash does not match the preceding line's "
                              "hash (a line was deleted, reordered, or its prev_hash "
                              "was edited)" % i), i - 1
        recomputed = _row_hash(row.get("ts"), row.get("session"), row.get("note"),
                               row.get("query_sha256"), row.get("surface"),
                               row.get("prev_hash"))
        if recomputed != row.get("hash"):
            return "broken", ("line %d: stored hash does not match its own recomputed "
                              "hash (the row's content was edited after it was "
                              "written)" % i), i - 1
        expected_prev = row.get("hash")
    return "ok", None, len(rows)


def cmd_verify(argv):
    vault_dir = argv[0] if argv else None
    path = os.path.join(vault_dir, _LOG_NAME) if vault_dir else default_path()
    status, message, count = verify_chain(path)
    if status == "no_data":
        print("NO-DATA: %s" % message)
        return 2
    if status == "ok":
        print("OK: %d read event(s) verified, chain intact at %s" % (count, path))
        return 0
    print("BROKEN: %s (%s)" % (message, path))
    return 1


def cmd_who_read(argv):
    if not argv:
        sys.stderr.write("bm_vault_read_audit: who-read needs a note id or path\n")
        return 2
    note = argv[0]
    root = None
    if len(argv) >= 3 and argv[1] == "--root":
        root = argv[2]
    path = os.path.join(root, _LOG_NAME) if root else default_path()
    rows = _read_rows(path)
    if rows is None:
        print("NO-DATA: no read audit at %s" % path)
        return 2
    matched = [r for r in rows if not r.get("_malformed") and r.get("note") == note]
    if not matched:
        print("0 record(s)")
        return 0
    print("%d record(s)" % len(matched))
    for row in matched:
        print("[%s] session=%s surface=%s query_sha256=%s"
              % (row.get("ts", "?"), row.get("session", "?"), row.get("surface", "?"),
                 row.get("query_sha256", "?")))
    return 0


HELP = """bm_vault_read_audit: immutable, hash chained record of who read what (V5).

Usage:
  bm_vault_read_audit.py verify [vault-dir]
  bm_vault_read_audit.py who-read <note> [--root vault-dir]

Exit codes: verify: 0 intact, 1 broken (first bad line named), 2 NO-DATA (no log).
who-read: 0 always when the log exists (0 or more matches printed), 2 NO-DATA.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if argv[0] == "verify":
        return cmd_verify(argv[1:])
    if argv[0] == "who-read":
        return cmd_who_read(argv[1:])
    sys.stderr.write("bm_vault_read_audit: unknown command %r; known: verify, who-read\n"
                     % argv[0])
    return 2


if __name__ == "__main__":
    sys.exit(main())
