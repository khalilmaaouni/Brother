#!/usr/bin/env python3
"""Retention and deletion propagation for the vault's derived copies.

WHY THIS EXISTS. Benchmark row D13, measured 2026-08-30: nothing removes a
fact, chunk, vector, summary or cache when a source note is deleted or
revoked. The estate's derived copies of a note are, measured in this tree:

  1. the retrieval index bm_vault.py builds (~/.claude/bm_vault_index.sqlite3):
     rows in notes, notes_fts, vectors, anchors, links, supersessions, keyed
     by the note's integer id and UNIQUE absolute path
  2. the baked catalogs bm_vault_catalog.py regenerates (deletion propagates
     there by rebake, so this tool NAMES the rebake, it never edits a catalog)
  3. hand-curated aggregate lines (40-Failures/Failures-Index.md): a human's
     writing, so this tool names the lines and NEVER auto-edits them
  4. the answer ledger (VB2-05, ~/.claude/bm_vault_answers.jsonl): a HISTORY
     of what past recalls actually read, which may cite a note this command
     is about to erase. This tool NEVER touches a ledger row, on purpose: the
     ledger's whole value is being an honest record of what WAS read at the
     time, so editing a past row to match a later deletion would falsify
     that record rather than report on it. Named as a MANUAL follow-up for a
     human to judge, the same non-negotiable as the Failures-Index lines
     above -- see tools/bm_vault_ledger.py's own docstring for the reader
     side of this same decision.

bm_vault.py's own `index` already sweeps gone notes, but only when a full
reindex runs; between reindexes a deleted note keeps answering recalls from
every derived table. This tool closes that gap two ways:

  census                 whole-vault reconciliation: every index row whose
                         source file is GONE from disk, or now sits in a
                         superseded/archive/attic folder (revoked, the same
                         filter bm_vault._walk applies), is a named finding.
                         Zero stale rows over a populated index is the clean
                         state. An absent or empty index is NO-DATA, never a
                         pass.
  propagate --note X     targeted removal for one note that is gone or
                         revoked: reports every derived row, every inbound
                         wikilink still pointing at the stem, the catalog
                         rebake command, the hand-curated lines needing a
                         human, and three derived caches this tool cannot
                         reach (named MANUAL follow-ups, never silently
                         skipped). DRY BY DEFAULT; --apply performs the
                         real erasure: secure_delete=ON, the index deletes
                         (notes_fts before notes, an fts5 external-content
                         ordering requirement, not an arbitrary choice),
                         then a full VACUUM. A note still live on disk is
                         REFUSED: propagation follows a deletion, it never
                         causes one.

PHYSICAL DELETION, not soft. Research 2026-08-30 (benchmark row VB2-04):
a plain SQLite DELETE only unlinks a row; the bytes stay in the file's
freed pages until something overwrites them, and an fts5 EXTERNAL CONTENT
table (notes_fts here) can leave stale index postings behind if its
content-table row is deleted before it is, because fts5 needs to read the
OLD text via content_rowid to know what to remove from its own inverted
index. Both are "ghost vectors": a deleted note that a raw scan of the db
file, or a plain full-text search, can still recover. --apply now closes
both: secure_delete zeroes freed bytes as part of the delete transaction,
notes_fts is deleted BEFORE notes (so fts5 can still see the old text),
and VACUUM rewrites the whole file so no freed-page history survives at
all. COST, paid on every --apply: VACUUM's rewrite is O(whole index size),
not O(one note), because SQLite has no "shred just these pages" primitive.
Calibrated in test_bm_vault_retention.py: a probe demonstrates the leak on
the pre-fix code path before asserting the sealed one.

TWO-PHASE ERASURE (VB3-08), EXTENDING the same physical erasure above rather
than forking it: propagate/census answer "the file is already gone or
revoked, clean up the index" -- they never delete a note themselves.
forget-plan/forget-execute answer the enterprise "right to be forgotten"
question, which starts from a note that is still LIVE on disk:

  forget-plan --vault V --id NOTE-ID [--out FILE] [--json]
      NOTE-ID is a stable id (n-<16 hex>), a vault-relative or absolute path,
      or a unique filename-stem suffix -- the note must still be LIVE (this
      command never operates on an already-gone note; that is what propagate
      is for). Read-only, touches nothing: it NAMES every derived object
      class this note would leave behind, one line per class, each with its
      own store and locator, so the record before deletion is exactly what a
      human (or forget-execute) can check completeness against afterward.
      Classes: index/vectors/fts/anchors (the six per-note tables
      cmd_propagate already knows how to erase, reusing its own table list),
      edges_outbound/edges_inbound (this note's own wikilink targets, and
      every OTHER note's links row that points back at this note's stem),
      supersessions, citations (bm_vault_cite.py's store, keyed on this
      note's own declared id), assertions/resolutions (bm_vault_assertions.py's
      two institutional stores, keyed on this note's own declared id as a
      subject), events (bm_vault_events.py's payload-free stream, keyed on
      the same id), evidence (this note's OWN `claim: ... [evidence: ...]`
      lines -- they die with the file, no external store to check), and
      summaries (other notes under 40-Failures/ that mention this note's
      filename stem in plain text -- the "digest mentions" case). A class
      with nothing impacted prints a zero, honestly; a class this tool
      cannot enumerate (caches: query_cache is keyed on query text, never on
      a note; exports: bm_vault_export.py bundles land in a caller-named
      --out directory with no registry recorded anywhere in the vault)
      prints NOT-ENUMERABLE naming why, never a guessed zero. --out FILE
      writes the same structure as JSON to FILE for forget-execute to
      consume; the plan itself changes nothing.

  forget-execute --vault V --plan FILE
      Executes exactly the plan FILE named, nothing recomputed and nothing
      guessed: first checks legal_holds.jsonl (99-System/legal_holds.jsonl)
      for an ACTIVE hold on the note's own id, its path, or its entity
      subject id (whichever the plan recorded) -- an active hold REFUSES the
      whole run, naming the hold's id/reason/by/at, before a single byte is
      touched. Absent a hold: removes the note file itself, then erases its
      six per-note rows from the retrieval index with the exact same
      secure_delete + fts5-ordering + VACUUM sequence cmd_propagate --apply
      already uses (same function, not a second copy). Institutional stores
      that are append-only by their own contract -- assertions.jsonl,
      resolutions.jsonl, citations.jsonl, the event stream, and any
      hand-authored mention in another note's body -- are NEVER auto-edited,
      exactly like Failures-Index and the answer ledger in propagate above;
      they are named as MANUAL follow-ups in the execute output, the same
      honest posture, never silently claimed as erased. Leaves a DELETION
      RECEIPT (99-System/forget-receipts/<id>.json): which classes, how many
      objects each, a self-integrity hash over the receipt's own content,
      and timestamps -- but never the erased note's own text, claim
      content, or any matched line: the receipt proves completion by count
      and locator, never by holding a copy of what it just erased.

  legal-hold --vault V --target T --by WHO --reason R [--release]
      Appends one record to legal_holds.jsonl: kind="hold" by default,
      kind="release" with --release. T is whatever forget-execute will check
      against (a note's own declared id, its path, or an entity subject id).
      The LAST record on file for a given target decides its current state
      -- a hold with no later release is active. Append-only, like every
      other institutional store in this family: a mistaken hold is undone by
      a release record, never by editing or deleting the hold line itself.

Exit codes: 0 clean/done, 1 findings or refusal, 2 NO-DATA.
Python 3.9, standard library only. propagate/census write nothing outside
the index db; forget-execute additionally removes the note file itself and
writes exactly two things outside it: legal_holds.jsonl (via legal-hold) and
one receipt file per execution, both under the vault's own 99-System/.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
CORRECTION_RULE_PATH_PREFIX = "correction-rule:"  # bm_vault.py's own prefix
# The six per-note tables cmd_propagate already knew how to count and erase;
# named once here so forget-plan/forget-execute reuse the exact same list
# rather than a second copy that could drift from it.
TABLE_KEYS = (("notes", "id"), ("notes_fts", "rowid"), ("anchors", "note_id"),
              ("links", "note_id"), ("vectors", "note_id"),
              ("supersessions", "by_note_id"))
LEGAL_HOLDS_RELPATH = os.path.join("99-System", "legal_holds.jsonl")
RECEIPTS_RELDIR = os.path.join("99-System", "forget-receipts")
CITATIONS_DEFAULT_RELPATH = os.path.join("99-System", "citations.jsonl")
EVENTS_DEFAULT_RELPATH = os.path.join(".vault", "events.jsonl")  # bm_vault_export's own convention
DIGEST_DIR_RELPATH = "40-Failures"


def _index_path():
    # Resolved at call time so a test subprocess with HOME moved gets its own
    # scratch index, exactly as bm_vault.py's tests already rely on.
    return brother_paths.config_path("bm_vault_index.sqlite3")


def _vault_root(cli_vault):
    """Same precedence as bm_vault.py and bm_vault_catalog.py."""
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    if env:
        return env
    # D01 contract (2026-08-30): environment first, the installer-written config
    # second, and NO guessed home path when neither is set. Absence is an audible
    # refusal downstream, never a wrong vault silently censused.
    try:
        with open(brother_paths.config_path("bm_vault.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh)
        v = cfg.get("vault")
        if isinstance(v, str) and v:
            return v
    except (OSError, ValueError):
        pass
    return ""


def _ledger_path():
    """Mirrors bm_vault.py's own LEDGER_PATH (VB2-05): sits beside the retrieval index
    this module already resolves, so no second path-resolution rule is invented here."""
    return os.path.join(os.path.dirname(_index_path()), "bm_vault_answers.jsonl")


def _stale_reason(path):
    """None while the note is live; otherwise why the index must forget it.
    The revoked test mirrors bm_vault._walk's directory filter verbatim, so
    census predicts exactly what the next full reindex would remove.

    ORDER NOTE, shared behavior with the indexer: the revoked (folder-name)
    heuristic is tested BEFORE existence on disk, so a still-live note that
    merely sits under a directory whose name contains "superseded",
    "archive" or "attic" is reported revoked, and propagate --apply will
    remove it, same as bm_vault._walk. This is not a false positive to fix
    here: it is the same folder-name policy the indexer already enforces,
    named so a caller does not assume "revoked" implies "gone".
    """
    low = os.path.dirname(path).replace("\\", "/").lower()
    if "superseded" in low or "/archive" in low or "/attic" in low:
        return "revoked: parent folder is superseded/archive/attic"
    if not os.path.exists(path):
        return "gone: source file no longer on disk"
    return None


def _connect():
    p = _index_path()
    if not os.path.exists(p):
        return None
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def _emit_json(tool, verdict, counts, findings):
    """The one shared --json envelope across every bm_vault_* reporting tool
    (VB7-02): {tool, verdict, counts, findings, schema_version}. verdict is
    always "PASS", "FAIL" or "NO-DATA" and always matches the process exit
    code the caller returns; counts/findings never change the exit code,
    only its format."""
    print(json.dumps({
        "tool": tool,
        "verdict": verdict,
        "counts": counts,
        "findings": findings,
        "schema_version": 1,
    }, indent=2, sort_keys=True))


def cmd_census(args):
    json_out = getattr(args, "json", False)
    con = _connect()
    if con is None:
        msg = "NO-DATA: no retrieval index at %s (run bm_vault.py index first)" % _index_path()
        if json_out:
            _emit_json("bm_vault_retention.census", "NO-DATA", {},
                       [{"kind": "no_data", "path": None, "detail": msg}])
        else:
            print(msg)
        return 2
    rows = con.execute("SELECT id, path FROM notes ORDER BY path").fetchall()
    file_rows = [r for r in rows if not r["path"].startswith(CORRECTION_RULE_PATH_PREFIX)]
    skipped = len(rows) - len(file_rows)
    if not file_rows:
        msg = ("NO-DATA: index holds no file-backed notes (%d correction-rule rows skipped)"
               % skipped)
        if json_out:
            _emit_json("bm_vault_retention.census", "NO-DATA",
                       {"correction_rule_rows_skipped": skipped},
                       [{"kind": "no_data", "path": None, "detail": msg}])
        else:
            print(msg)
        return 2
    stale = []
    for r in file_rows:
        reason = _stale_reason(r["path"])
        if reason:
            stale.append((r["id"], r["path"], reason))
    if json_out:
        verdict = "FAIL" if stale else "PASS"
        counts = {"indexed": len(file_rows), "stale": len(stale),
                  "correction_rule_rows_skipped": skipped}
        findings = [{"kind": "stale", "path": path, "detail": "id=%d: %s" % (nid, reason)}
                    for nid, path, reason in stale]
        _emit_json("bm_vault_retention.census", verdict, counts, findings)
        return 1 if stale else 0
    for nid, path, reason in stale:
        print("STALE id=%d %s\n  %s" % (nid, path, reason))
    print("census: %d file-backed note(s) indexed, %d stale, %d correction-rule row(s) skipped"
          % (len(file_rows), len(stale), skipped))
    if stale:
        print("propagate each with: %s propagate --note <id> --apply"
              % os.path.basename(__file__))
        return 1
    print("clean: every indexed note is present and unrevoked on disk")
    return 0


def _resolve(con, token):
    """The note row for an integer id, an exact path, or a unique path suffix.

    The suffix match is done in Python on a literal endswith, never SQL LIKE:
    LIKE treats "_" and "%" in the token as wildcards, so an unescaped
    `LIKE '%'||token` let a typo'd token (lessons_a.md, underscore) cross-match
    an unrelated indexed path (lessons-a.md, hyphen) that only LOOKS similar.
    --apply would then delete the wrong note's rows. A literal suffix compare
    has no wildcard semantics to exploit.
    """
    if token.isdigit():
        return con.execute("SELECT id, path FROM notes WHERE id=?", (int(token),)).fetchone()
    for cand in (token, os.path.abspath(os.path.expanduser(token))):
        row = con.execute("SELECT id, path FROM notes WHERE path=?", (cand,)).fetchone()
        if row:
            return row
    suffix = "/" + token
    hits = [(r["id"], r["path"]) for r in con.execute("SELECT id, path FROM notes").fetchall()
            if r["path"] == token or r["path"].endswith(suffix)]
    return hits[0] if len(hits) == 1 else None


def _index_table_counts(con, nid):
    """{table: row count for nid}, over TABLE_KEYS -- the one place this six-table
    list is read, so forget-plan/forget-execute and propagate can never drift into
    two different ideas of which tables a note's derived rows live in."""
    return {table: con.execute("SELECT COUNT(*) c FROM %s WHERE %s=?" % (table, key),
                               (nid,)).fetchone()["c"]
            for table, key in TABLE_KEYS}


def _inbound_links(con, stem, nid):
    """Every OTHER note's links row that names this note's filename stem as its
    wikilink target, in path order. Factored out of cmd_propagate so
    forget-plan reports the identical inbound set forget-execute will leave
    dangling (a human decision, never auto-edited -- see the MANUAL follow-up
    below)."""
    return con.execute(
        "SELECT DISTINCT n.path FROM links l JOIN notes n ON n.id=l.note_id "
        "WHERE l.target=? AND n.id<>? ORDER BY n.path", (stem, nid)).fetchall()


def _delete_index_rows(con, nid):
    """The physical erasure sequence VB2-04 hardened: secure_delete=ON so freed
    pages are zeroed as part of the transaction, notes_fts deleted BEFORE notes
    (fts5 EXTERNAL CONTENT needs the old text via content_rowid to remove its own
    postings), then VACUUM so no freed-page history survives at all. The one
    place this sequence is written; cmd_propagate --apply and forget-execute both
    call it rather than each keeping their own copy that could drift apart."""
    con.execute("PRAGMA secure_delete=ON")
    con.execute("DELETE FROM notes_fts WHERE rowid=?", (nid,))
    con.execute("DELETE FROM notes WHERE id=?", (nid,))
    con.execute("DELETE FROM anchors WHERE note_id=?", (nid,))
    con.execute("DELETE FROM links WHERE note_id=?", (nid,))
    con.execute("DELETE FROM vectors WHERE note_id=?", (nid,))
    con.execute("DELETE FROM supersessions WHERE by_note_id=?", (nid,))
    con.commit()
    con.execute("VACUUM")


def cmd_propagate(args):
    con = _connect()
    if con is None:
        print("NO-DATA: no retrieval index at %s, nothing to propagate" % _index_path())
        return 2
    row = _resolve(con, args.note)
    if row is None:
        print("NO-DATA: %r is not in the retrieval index (no row, or an ambiguous suffix); "
              "nothing to propagate" % args.note)
        return 2
    nid, path = row["id"], row["path"]
    reason = _stale_reason(path)
    if reason is None:
        print("REFUSED: %s is still live on disk. Propagation follows a deletion or "
              "revocation, it never causes one. Delete the note, or move it into a "
              "superseded/archive folder, then re-run." % path)
        return 1
    print("note id=%d %s\n  %s" % (nid, path, reason))

    counts = _index_table_counts(con, nid)
    verb = "removing" if args.apply else "would remove"
    for table in ("notes", "notes_fts", "anchors", "links", "vectors", "supersessions"):
        print("  %s %d row(s) from %s" % (verb, counts[table], table))

    stem = os.path.splitext(os.path.basename(path))[0]
    inbound = _inbound_links(con, stem, nid)
    for r in inbound:
        print("  still linked from (a human decides what those links should now say): %s"
              % r["path"])

    vault = _vault_root(args.vault)
    if path.startswith(vault.rstrip("/") + "/"):
        print("  baked catalogs propagate by rebake: python3 %s bake"
              % os.path.join(HERE, "bm_vault_catalog.py"))
        fi = os.path.join(vault, "40-Failures", "Failures-Index.md")
        if os.path.exists(fi):
            try:
                with open(fi, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if stem in line:
                            print("  hand-curated, needs a human, NEVER auto-edited: "
                                  "%s:%d: %s" % (fi, i, line.rstrip()))
            except OSError as e:
                print("  NO-DATA: could not read %s (%s)" % (fi, e))
        print("  MANUAL follow-up, this tool cannot reach it: distilled failure notes under "
              "%s (bm_vault_distill.py output) may quote or summarize this note; a human "
              "reviews them for the same content." % os.path.join(vault, "40-Failures"))

    # MANUAL follow-up named unconditionally: the recall hook's own SEEN cache is not an
    # index table this tool touches, and it is keyed by path/title text the hook already
    # printed once, so it can keep answering as though the note still existed.
    print("  MANUAL follow-up, this tool cannot reach it: the recall SEEN cache at %s may "
          "still remember this note; a human decides whether to prune it."
          % brother_paths.config_path(".vault_recall_seen"))

    # REPORT-ONLY, NEVER EDITED (VB2-05). The answer ledger is append-only history: a
    # record of what a past recall actually read AT THE TIME. Rewriting a past row to
    # drop this note after it is erased would make the ledger lie about what happened
    # instead of report it, so this is named for a human exactly like the hand-curated
    # Failures-Index lines above, never auto-edited by this or any tool.
    print("  MANUAL follow-up, this tool cannot reach it: the answer ledger at %s is "
          "append-only history and may hold rows citing this note as a served recall "
          "hit; a human decides whether those historical rows still deserve to stand."
          % _ledger_path())

    if not args.apply:
        print("DRY RUN: nothing changed. Re-run with --apply to remove the index rows.")
        con.close()
        return 0
    # See _delete_index_rows' own docstring for why this exact sequence (secure_delete,
    # notes_fts before notes, VACUUM) is the one this tool stands behind; forget-execute
    # below calls the identical function rather than a second copy.
    _delete_index_rows(con, nid)
    con.close()
    print("applied: id=%d forgotten by the retrieval index" % nid)
    return 0


# ------------------------------------------------------------- siblings ----

def _load_sibling(filename, modname):
    """Dynamic import by path, the same pattern bm_vault_assertions.py and
    bm_vault_lineage.py already use to read their own sibling contract
    modules: load without relying on tools/ being on sys.path, and without
    re-deciding anything the sibling already owns."""
    spec = importlib.util.spec_from_file_location(modname, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _forget_siblings():
    """The five contract modules forget-plan reads from, loaded once per call
    site and returned by name so call sites never depend on load order."""
    return dict(
        ids=_load_sibling("bm_vault_ids.py", "bm_vault_ids"),
        entity=_load_sibling("bm_vault_entity.py", "bm_vault_entity"),
        provenance=_load_sibling("bm_vault_provenance.py", "bm_vault_provenance"),
        cite=_load_sibling("bm_vault_cite.py", "bm_vault_cite"),
        events=_load_sibling("bm_vault_events.py", "bm_vault_events"),
        assertions=_load_sibling("bm_vault_assertions.py", "bm_vault_assertions"),
    )


# --------------------------------------------------------- jsonl stores ----
# legal_holds.jsonl and the deletion receipts share the append-only shape
# bm_vault_assertions.py's own assertions/resolutions stores already use;
# duplicated rather than imported, matching this family's own stated
# convention (see bm_vault_provenance.py's docstring) that every sibling
# contract module reads or mints its own store rather than sharing a
# function two modules deep.

def _read_jsonl(path):
    """[record, ...] in file order, or None when the file does not exist --
    the NO-DATA case, distinct from a present-but-empty file (which returns
    [], a legitimate zero-record store). A malformed line is skipped with a
    stderr warning rather than hiding every other record."""
    if not os.path.isfile(path):
        return None
    records = []
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError as exc:
                    sys.stderr.write("bm_vault_retention: skipping malformed line %d in "
                                     "%s (%s)\n" % (i, path, exc))
    except OSError:  # sbe: allow-silent documented contract, this function's own docstring says None means unreadable
        return None
    return records


def _append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(record, sort_keys=True)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _mint_local_id(prefix):
    return prefix + uuid.uuid4().hex[:16]


# ---------------------------------------------------------- legal holds ----

def _legal_holds_path(vault):
    return os.path.join(vault, LEGAL_HOLDS_RELPATH)


def active_hold(records, target):
    """The most recent record naming the given target, if its kind is
    "hold" and no later record for the same target released it. Records
    are read in file (append) order, so the LAST one for a target decides
    its current state -- a hold with no later release is active. None
    when target was never held, or was released after its last hold."""
    last = None
    for rec in records or ():
        if rec.get("target") == target:
            last = rec
    if last is not None and last.get("kind") == "hold":
        return last
    return None


def cmd_legal_hold(args):
    vault = _vault_root(args.vault)
    if not vault or not os.path.isdir(vault):
        print("NO-DATA: no readable vault at %r" % vault)
        return 2
    path = _legal_holds_path(vault)
    record = dict(
        id=_mint_local_id("lh-"),
        target=args.target,
        kind="release" if args.release else "hold",
        reason=args.reason or "",
        by=args.by,
        at=datetime.date.today().isoformat(),
    )
    _append_jsonl(path, record)
    verb = "released" if args.release else "held"
    print("%s: target=%s id=%s by=%s -> %s" % (verb, args.target, record["id"], args.by, path))
    return 0


# ------------------------------------------------------- note resolution ---

def _resolve_live_note(vault, token, ids_mod):
    """(relpath, error_or_None). token is a stable id (n-<16 hex>, via
    ids_mod.resolve), an exact vault-relative or absolute path, or a unique
    filename-stem suffix -- the same three shapes cmd_propagate's own
    _resolve accepts against the retrieval index, read here directly off the
    filesystem instead, because forget-plan operates on a note that is still
    LIVE (propagate's own index-only lookup only ever sees a note after it is
    already gone or revoked). A literal endswith suffix match, never SQL or
    glob wildcards, for the same reason _resolve's own docstring gives: a
    typo in the token must never cross-match an unrelated path that only
    looks similar.
    """
    if ids_mod.ID_VALUE_RE.match(token):
        rel = ids_mod.resolve(vault, token, allow_stem=False)
        if rel is None:
            return None, "no note in %s declares id %s" % (vault, token)
        return rel, None
    if os.path.isfile(os.path.join(vault, token)):
        return token, None
    if os.path.isabs(token) and os.path.isfile(token):
        return os.path.relpath(token, vault), None
    suffix = "/" + token
    hits = [os.path.relpath(p, vault) for p in ids_mod.walk(vault)
            if p == token or p.endswith(suffix)]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, "%r is an ambiguous suffix, matching %d notes" % (token, len(hits))
    return None, ("%r is neither a resolvable note id (n-<16 hex>), an existing path, "
                  "nor a unique filename-stem suffix under %s" % (token, vault))


# --------------------------------------------------------- forget-plan ----

def _cls(name, store, locator, count, enumerable=True, reason=None, extra=None):
    """One derived-object class entry: name, where it lives, a human locator,
    and the count found there. `enumerable=False` carries `reason` instead of
    a count that would otherwise be guessed; `extra` is an optional list of
    ids/paths for a human (or a test) to inspect beyond the bare count."""
    entry = dict(name=name, store=store, locator=locator, count=count, enumerable=enumerable)
    if reason is not None:
        entry["reason"] = reason
    if extra is not None:
        entry["ids"] = extra
    return entry


def _entity_subject(text, note_id, entity_mod):
    """The note's own declared id, but ONLY when it also declares entity: in
    entity_mod's own vocabulary -- an ordinary document that merely carries an
    id is not a subject any assertion or resolution could key on. None
    otherwise, never a guess."""
    if not note_id:
        return None
    block = entity_mod._frontmatter(text)
    if block is None:
        return None
    etype = entity_mod._field(block, "entity")
    if not etype:
        return None
    etype = etype.strip().strip('"').strip("'")
    return note_id if etype in entity_mod.ENTITY_TYPES else None


def _digest_mentions(vault, abspath, stem):
    """[(path, lineno), ...] for every OTHER note under 40-Failures/ whose
    plain text names this note's own filename stem -- the "digest mentions"
    case named in the row's own done-check. Restricted to 40-Failures/ on
    purpose: the FULL vault's wikilink graph is already covered by
    edges_inbound above (the links table), so this class is the narrower,
    plain-text one bm_vault_distill.py's own output is made of. Never
    touches frontmatter: only the line's own text is compared."""
    digest_dir = os.path.join(vault, DIGEST_DIR_RELPATH)
    hits = []
    if not os.path.isdir(digest_dir):
        return hits
    target_abs = os.path.abspath(abspath)
    for dirpath, dirnames, filenames in os.walk(digest_dir):
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.abspath(p) == target_abs:
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if stem in line:
                            hits.append("%s:%d" % (p, lineno))
            except OSError:  # sbe: allow-silent skip one unreadable digest file while scanning, other files still checked
                continue
    return hits


def build_forget_plan(vault, rel_path, sib, con):
    """The whole plan dict for the note at vault-relative rel_path: every
    derived object class this note holds today, one entry per class, an
    honest zero where nothing was found, NOT-ENUMERABLE where this tool has
    no key to search by. `con` is the retrieval index connection (already
    known non-None by the caller); index-derived classes read 0 when the
    note simply has not been indexed yet, which is a legitimate state, never
    NO-DATA on its own."""
    abspath = os.path.join(vault, rel_path)
    with open(abspath, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    note_id = sib["ids"].read_id(text)
    entity_subject = _entity_subject(text, note_id, sib["entity"])
    stem = os.path.splitext(os.path.basename(rel_path))[0]

    row = con.execute("SELECT id FROM notes WHERE path=?", (abspath,)).fetchone()
    index_id = row["id"] if row else None
    counts = _index_table_counts(con, index_id) if index_id is not None else \
        dict((t, 0) for t, _k in TABLE_KEYS)

    classes = []
    classes.append(_cls("index", "bm_vault_index.sqlite3:notes",
                        "path=%s" % abspath, counts["notes"]))
    classes.append(_cls("vectors", "bm_vault_index.sqlite3:vectors",
                        "note_id=%s" % index_id, counts["vectors"]))
    classes.append(_cls("fts", "bm_vault_index.sqlite3:notes_fts",
                        "rowid=%s" % index_id, counts["notes_fts"]))
    classes.append(_cls("anchors", "bm_vault_index.sqlite3:anchors",
                        "note_id=%s" % index_id, counts["anchors"]))
    classes.append(_cls("edges_outbound", "bm_vault_index.sqlite3:links",
                        "note_id=%s" % index_id, counts["links"]))
    classes.append(_cls("supersessions", "bm_vault_index.sqlite3:supersessions",
                        "by_note_id=%s" % index_id, counts["supersessions"]))

    inbound_paths = ([r["path"] for r in _inbound_links(con, stem, index_id)]
                     if index_id is not None else [])
    classes.append(_cls("edges_inbound", "bm_vault_index.sqlite3:links",
                        "target=%s" % stem, len(inbound_paths), extra=inbound_paths))

    citations_path = os.path.join(vault, CITATIONS_DEFAULT_RELPATH)
    if note_id is None:
        classes.append(_cls("citations", citations_path, "note_id=%s" % note_id, 0,
                            enumerable=False,
                            reason="note declares no stable id (id: n-<16 hex> frontmatter "
                                  "field); citations key on that id"))
    else:
        recs = sib["cite"]._read_citations(citations_path) or []
        hits = [r for r in recs if r.get("note_id") == note_id or r.get("by") == note_id]
        classes.append(_cls("citations", citations_path, "note_id=%s" % note_id, len(hits)))

    assertions_path = sib["assertions"].assertions_path(vault)
    resolutions_path = sib["assertions"].resolutions_path(vault)
    if entity_subject is None:
        reason = ("note is not a declared entity (needs id: n-<16 hex> plus a recognised "
                 "entity: type); assertions and resolutions key subjects by entity id")
        classes.append(_cls("assertions", assertions_path, "subject=%s" % entity_subject, 0,
                            enumerable=False, reason=reason))
        classes.append(_cls("resolutions", resolutions_path, "subject=%s" % entity_subject, 0,
                            enumerable=False, reason=reason))
    else:
        a_hits = [r for r in (sib["assertions"]._read_records(assertions_path) or ())
                 if r.get("subject") == entity_subject]
        classes.append(_cls("assertions", assertions_path, "subject=%s" % entity_subject,
                            len(a_hits), extra=[r.get("id") for r in a_hits]))
        r_hits = [r for r in (sib["assertions"]._read_records(resolutions_path) or ())
                 if r.get("subject") == entity_subject]
        classes.append(_cls("resolutions", resolutions_path, "subject=%s" % entity_subject,
                            len(r_hits), extra=[r.get("id") for r in r_hits]))

    events_path = os.path.join(vault, EVENTS_DEFAULT_RELPATH)
    if note_id is None:
        classes.append(_cls("events", events_path, "ref=%s" % note_id, 0, enumerable=False,
                            reason="note declares no stable id; events key on ref=<note id>"))
    elif not os.path.isfile(events_path):
        classes.append(_cls("events", events_path, "ref=%s" % note_id, 0))
    else:
        try:
            parsed = sib["events"].load_events([events_path])
            matching = [e for e in parsed if e.get("ref") == note_id]
            classes.append(_cls("events", events_path, "ref=%s" % note_id, len(matching),
                                extra=[e.get("event_key") for e in matching]))
        except sib["events"].FoldError as exc:
            classes.append(_cls("events", events_path, "ref=%s" % note_id, 0,
                                enumerable=False,
                                reason="event stream failed to fold: %s" % exc))

    claims = sib["provenance"].find_claims(text)
    classes.append(_cls("evidence", "note body (claim: ... [evidence: ...] lines)",
                        "%d claim line(s) inside %s" % (len(claims), abspath), len(claims)))

    mentions = _digest_mentions(vault, abspath, stem)
    classes.append(_cls("summaries", os.path.join(vault, DIGEST_DIR_RELPATH),
                        "stem=%s" % stem, len(mentions), extra=mentions))

    classes.append(_cls("caches", "bm_vault_index.sqlite3:query_cache",
                        "note_id=<none>", 0, enumerable=False,
                        reason="query_cache is keyed by (embed model, exact query text) "
                              "hash, never by note id; there is no per-note key to filter "
                              "this class on"))
    classes.append(_cls("exports", "bm_vault_export.py bundles", "note_id=%s" % note_id, 0,
                        enumerable=False,
                        reason="export bundles land in a caller-named --out directory with "
                              "no manifest registry recorded anywhere in the vault; there is "
                              "no location to check for this note"))

    holds = _read_jsonl(_legal_holds_path(vault)) or []
    hold_hit = None
    for target in (t for t in (note_id, abspath, entity_subject) if t):
        hold_hit = active_hold(holds, target)
        if hold_hit is not None:
            break

    return dict(
        tool="bm_vault_retention.forget-plan",
        vault=vault,
        note=dict(stable_id=note_id, index_id=index_id, path=abspath, rel_path=rel_path),
        entity_subject=entity_subject,
        legal_hold=dict(status="active" if hold_hit else "none", record=hold_hit),
        classes=classes,
        generated_at=datetime.date.today().isoformat(),
    )

def _print_forget_plan(plan):
    note = plan["note"]
    print("forget-plan for %s (stable_id=%s, index_id=%s)"
         % (note["path"], note["stable_id"], note["index_id"]))
    print("entity_subject=%s" % plan["entity_subject"])
    print("legal_hold: %s" % plan["legal_hold"]["status"])
    if plan["legal_hold"]["status"] == "active":
        h = plan["legal_hold"]["record"]
        print("  HOLD id=%s target=%s by=%s at=%s reason=%s"
             % (h.get("id"), h.get("target"), h.get("by"), h.get("at"), h.get("reason")))
    for c in plan["classes"]:
        if c["enumerable"]:
            print("  %s: %d object(s) in %s (%s)"
                 % (c["name"], c["count"], c["store"], c["locator"]))
        else:
            print("  %s: NOT-ENUMERABLE (%s)" % (c["name"], c["reason"]))


def cmd_forget_plan(args):
    vault = _vault_root(args.vault)
    if not vault or not os.path.isdir(vault):
        print("NO-DATA: no readable vault at %r" % vault)
        return 2
    sib = _forget_siblings()
    rel, err = _resolve_live_note(vault, args.id, sib["ids"])
    if err:
        print("NO-DATA: %s could not be resolved to a live note: %s" % (args.id, err))
        return 2
    con = _connect()
    if con is None:
        print("NO-DATA: no retrieval index at %s (run bm_vault.py index first)" % _index_path())
        return 2
    try:
        plan = build_forget_plan(vault, rel, sib, con)
    finally:
        con.close()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, sort_keys=True, indent=2)

    if getattr(args, "json", False):
        print(json.dumps(plan, sort_keys=True, indent=2))
    else:
        _print_forget_plan(plan)
        if args.out:
            print("plan written to %s" % args.out)
    return 0

# ------------------------------------------------------ forget-execute ----

def cmd_forget_execute(args):
    vault = _vault_root(args.vault)
    if not vault or not os.path.isdir(vault):
        print("NO-DATA: no readable vault at %r" % vault)
        return 2
    if not args.plan or not os.path.isfile(args.plan):
        print("NO-DATA: no readable plan file at %r; run forget-plan --out FILE first"
             % args.plan)
        return 2
    try:
        with open(args.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: plan file %r could not be read (%s)" % (args.plan, exc))
        return 2

    note = plan.get("note") or {}
    path = note.get("path")
    stable_id = note.get("stable_id")
    entity_subject = plan.get("entity_subject")
    if not path:
        print("NO-DATA: plan carries no note path; nothing to execute")
        return 2

    holds = _read_jsonl(_legal_holds_path(vault)) or []
    for target in (t for t in (stable_id, path, entity_subject) if t):
        hold = active_hold(holds, target)
        if hold is not None:
            print("REFUSED: legal hold %s blocks execution (target=%s by=%s at=%s "
                 "reason=%s). Nothing was touched. Release it first with "
                 "legal-hold --release." % (hold.get("id"), hold.get("target"),
                                           hold.get("by"), hold.get("at"), hold.get("reason")))
            return 1

    if not os.path.isfile(path):
        print("REFUSED: the plan is stale, %s no longer exists on disk; re-run "
             "forget-plan." % path)
        return 1

    con = _connect()
    index_id = None
    counts = dict((t, 0) for t, _k in TABLE_KEYS)
    if con is not None:
        row = con.execute("SELECT id FROM notes WHERE path=?", (path,)).fetchone()
        if row is not None:
            index_id = row["id"]
            counts = _index_table_counts(con, index_id)

    try:
        os.remove(path)
    except OSError as exc:
        if con is not None:
            con.close()
        print("REFUSED: could not remove %s (%s); nothing else was touched." % (path, exc))
        return 1

    if con is not None:
        if index_id is not None:
            _delete_index_rows(con, index_id)
        con.close()

    # evidence (claim:/[evidence:] lines) lives only inside the note body itself,
    # so removing the file above already erases it -- it needs no separate MANUAL
    # follow-up, unlike the institutional/append-only stores below.
    automated = ("index", "vectors", "fts", "anchors", "edges_outbound",
                 "supersessions", "evidence")
    manual_followups = [c["name"] for c in plan.get("classes", ()) if c["name"] not in automated]
    removed = dict(index=counts["notes"], vectors=counts["vectors"], fts=counts["notes_fts"],
                  anchors=counts["anchors"], edges_outbound=counts["links"],
                  supersessions=counts["supersessions"])

    receipt_id = _mint_local_id("fr-")
    receipt = dict(receipt_id=receipt_id, note_path=path, note_stable_id=stable_id,
                  entity_subject=entity_subject, removed=removed,
                  manual_followups=manual_followups, legal_hold_check="clear",
                  executed_at=datetime.date.today().isoformat())
    receipt["content_hash"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True).encode("utf-8")).hexdigest()

    receipts_dir = os.path.join(vault, RECEIPTS_RELDIR)
    os.makedirs(receipts_dir, exist_ok=True)
    receipt_path = os.path.join(receipts_dir, receipt_id + ".json")
    with open(receipt_path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, sort_keys=True, indent=2)

    print("applied: %s forgotten" % path)
    for name in sorted(removed):
        print("  removed %d row(s) from %s" % (removed[name], name))
    for name in manual_followups:
        print("  MANUAL follow-up, never auto-erased: %s (see the plan for detail)" % name)
    print("receipt: %s content_hash=%s" % (receipt_path, receipt["content_hash"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("census", help="index rows whose source file is gone or revoked")
    c.add_argument("--vault", default=None)
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_census)
    p = sub.add_parser("propagate", help="remove one deleted/revoked note's derived rows")
    p.add_argument("--note", required=True, help="note id, path, or unique path suffix")
    p.add_argument("--apply", action="store_true", help="perform the deletes (dry by default)")
    p.add_argument("--vault", default=None)
    p.set_defaults(fn=cmd_propagate)
    fp = sub.add_parser("forget-plan", help="name every derived object a live note holds")
    fp.add_argument("--vault", default=None)
    fp.add_argument("--id", required=True, help="note id (n-<16 hex>), path, or unique stem suffix")
    fp.add_argument("--out", default=None, help="write the plan as JSON to FILE")
    fp.add_argument("--json", action="store_true")
    fp.set_defaults(fn=cmd_forget_plan)
    fe = sub.add_parser("forget-execute", help="execute a forget-plan, honoring legal holds")
    fe.add_argument("--vault", default=None)
    fe.add_argument("--plan", required=True, help="the JSON file forget-plan --out wrote")
    fe.set_defaults(fn=cmd_forget_execute)
    lh = sub.add_parser("legal-hold", help="hold or release a target against forget-execute")
    lh.add_argument("--vault", default=None)
    lh.add_argument("--target", required=True, help="a note stable id, path, or entity subject id")
    lh.add_argument("--by", required=True)
    lh.add_argument("--reason", default=None)
    lh.add_argument("--release", action="store_true", help="release instead of hold")
    lh.set_defaults(fn=cmd_legal_hold)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
