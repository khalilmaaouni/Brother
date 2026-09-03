#!/usr/bin/env python3
"""Claim-level provenance: a sentence that points at its own evidence.

WHY THIS EXISTS. Benchmark row D07, measured 2026-08-29: provenance in this
vault tops out at NOTE level, `verified-by` on 564 of 823 notes, and never
below that. A note can carry a verified-by line while one sentence inside it
is unsupported, and nothing distinguishes the two. The gap this row names is
a locator on the CLAIM itself, not the note that holds it.

THE CLAIM SYNTAX, one line in a note's body:

    claim: <text> [evidence: <locator>]

A leading list marker (`- `, `* `, `+ `) and leading indentation are both
allowed before `claim:`, because a claim written inside a bulleted or nested
list is still a claim: `- claim: x [evidence: a.md]` and `  claim: x
[evidence: a.md]` both parse. Text after the closing `]` is a different
matter: it is never silently folded into the claim or the locator (either
guess could be wrong), so a line like `claim: x [evidence: a.md] and also on
Tuesdays` is a MALFORMED claim, named in `check` output on its own line,
distinct from dangling and never counted as a passing claim. Two `[evidence:
...]` brackets on one line fall out of the same rule rather than needing a
special case: the first bracket is captured as the locator and everything
after it, including the second bracket, is trailing text, so the line is
reported malformed with the first locator named.

Plain prose, no new frontmatter field, so an existing note gains provenance
by adding a line rather than restructuring itself. A locator is one of:

  a vault path         repo-relative, optionally with #Heading, e.g.
                        `50-Reference/some-note.md` or
                        `50-Reference/some-note.md#A Named Section`
  a note id             `n-<16 hex>`, the D05 stable id, resolved by reading
                        the same `id:` frontmatter field bm_vault_ids.py reads
                        (duplicated here rather than imported: every sibling
                        contract module in this family reads the vault on its
                        own rather than depending on another module's walk,
                        so no module's behaviour shifts when a sibling changes)
  a commit              `repo:<sha>`, a commit in the vault's own git history
  a URL                 `http://` or `https://`, recorded but never checked

ENTERPRISE EVIDENCE (VB3-07): the four kinds above cover the vault's own
notes and git history. A claim about enterprise work often cites evidence
that lives outside the vault entirely -- a warehouse query, a versioned
document, a captured file -- and that evidence must resolve to the EXACT
version bound at link time, or say plainly that it cannot, never silently.
Three more locator kinds, each a `prefix:field|field|...` string so the
locator stays one bracket-free line like every other kind above:

  a query id     `query:<system>|<query_id>|<executed_at>|<result_hash>`,
                 checked against a query ledger fixture (--query-ledger, one
                 JSON object per line: system, query_id, executed_at,
                 result_hash). A match on all three key fields AND the same
                 result_hash resolves. No ledger, no matching record, or a
                 record whose result_hash differs: UNAVAILABLE, named, never
                 dangling -- a query ledger this checker was not given is not
                 proof the query never ran.
  a document span  `docspan:<document>|<version_or_hash>|<page>|<span_start>
                 |<span_end>`, a page and character span inside a document
                 version identified by its sha256. Checked against
                 --document-store: resolves when the store holds a file at
                 <document> whose CURRENT sha256 equals <version_or_hash>. A
                 source change after linking is never silently followed --
                 a store file present at a DIFFERENT hash is UNAVAILABLE,
                 naming the drift (the bound hash stays the evidence; the
                 changed file is named, never substituted for it). No store,
                 or no file at that path: UNAVAILABLE, named.
  a capture      `capture:<path_or_blob_ref>|<captured_at>|<sha256>`, a
                 captured file or blob checked by content hash. Resolved
                 relative to --capture-root (the vault root when no root is
                 given). Resolves only when the file is present AND its
                 current sha256 equals the bound one. Present with a
                 DIFFERENT hash is TAMPERED, named with both hashes, never
                 silent and never folded into a plain dangling/unavailable
                 bucket, because the file being there but wrong is a
                 different fact than the file being gone. Missing entirely:
                 UNAVAILABLE, named.

`kinds [--json]` prints every locator kind's own required fields, so a new
caller learns the contract from the tool rather than from this docstring.

`get-evidence --vault V --note NOTE --claim TEXT` resolves ONE assertion (a
note's `claim:` line matched by a substring of its claim text) to its exact
locator and, for the enterprise kinds, its exact bound version. Prints
RESOLVED, TAMPERED, or UNAVAILABLE and always names the reason except on
RESOLVED. Exit 0 RESOLVED, 1 TAMPERED or UNAVAILABLE (found, not clean), 2
UNKNOWN-ASSERTION (no claim in NOTE matches --claim) or NO-DATA (unreadable
vault or NOTE).

WHAT COUNTS AS RESOLVED, per kind, and why URLs and commits are NOT the same
kind of unknown:

  path      the file exists under the vault root (and, if a heading is named,
            a markdown heading matching it exists in that file). Dangling
            otherwise: FAIL, named.
  id        the id appears in the vault's own id index. Dangling otherwise:
            FAIL, named.
  commit    checked against the vault's LOOSE git objects only (zlib-inflate,
            stdlib, no `git` subprocess and therefore no no-network exception
            to register). A sha found loose is ok. A sha absent loose AND the
            repository holds no pack files at all is dangling: FAIL, named,
            because a small unpacked repo has nowhere else the object could
            be. A sha absent loose while pack files exist is UNVERIFIABLE,
            never dangling, because this checker cannot read a packfile and
            reporting FAIL there would be exactly the false-dangling failure
            this row must not produce. Same for a vault with no `.git` at
            its root at all.
  url       always UNVERIFIABLE. Nothing here ever fetches a URL: recorded,
            counted, never resolved offline, never a pass and never a fail.

ZERO CLAIMS IS NOT A PASS. A corpus that has not adopted the `claim: ... [evidence: ...]`
syntax yet has zero claims to check, which is a true statement about the
corpus, not a clean bill of health: `check` reports NO-DATA and exits 2,
never 0, because 0 would read as "checked, found nothing wrong" when the
honest reading is "this capability is not in use yet". That is the exact
shape of the false pass this estate's 2026-08-29 benchmark already produced
three times.

WHAT THIS IS NOT. It does not touch the corpus: no note in the Kay Vault
gains a `claim:` line from this module. Applying the syntax to real notes is
a separate, fenced step (see docs/plan/VB-10-CORPUS-SAMPLE-PROPOSAL, the
named sample this checker's own author proposes for whoever holds that
fence), because a whole-corpus edit landing on top of another writer is
exactly how this estate has already lost work once.

Exit 0 clean, 1 findings (a dangling locator), 2 NO-DATA (no readable vault,
or zero claims found). Python 3.9 floor, standard library only, writes
nothing anywhere.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zlib

SKIP_DIRS = {".git", ".trash", ".obsidian"}

CLAIM_RE = re.compile(
    r"^[ \t]*(?:[-*+]\s+)?claim:\s*(.+?)\s*\[evidence:\s*([^\]]+?)\s*\](.*)$", re.M)
ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.M)
ID_VALUE_RE = re.compile(r"^n-[0-9a-f]{16}$")
REPO_SHA_RE = re.compile(r"^repo:([0-9a-f]{7,40})$")
URL_RE = re.compile(r"^https?://\S+$")
QUERY_RE = re.compile(r"^query:")
DOCSPAN_RE = re.compile(r"^docspan:")
CAPTURE_RE = re.compile(r"^capture:")

# Enterprise locator kind -> the fields it declares, for `kinds` and for
# anyone extending this registry: the fields named here are exactly the
# pipe-delimited parts each prefix parser below requires, in order.
KIND_FIELDS = {
    "path": ["vault-relative path", "optional #Heading"],
    "id": ["n-<16 hex> stable note id"],
    "commit": ["repo:<sha>, checked against the vault's own loose git objects"],
    "url": ["http:// or https:// URL, recorded, never resolved offline"],
    "query_id": ["system", "query_id", "executed_at", "result_hash"],
    "document_span": ["document (id or path)", "version_or_hash (sha256 bound at link time)",
                       "page", "span_start", "span_end"],
    "capture": ["path_or_blob_ref", "captured_at", "sha256"],
}


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def classify_locator(locator):
    """One of "path", "id", "commit", "url", "query_id", "document_span",
    "capture". Disjoint by construction: every other kind requires a
    specific prefix or shape, so anything left over is treated as a path,
    which is the most common case."""
    if REPO_SHA_RE.match(locator):
        return "commit"
    if ID_VALUE_RE.match(locator):
        return "id"
    if URL_RE.match(locator):
        return "url"
    if QUERY_RE.match(locator):
        return "query_id"
    if DOCSPAN_RE.match(locator):
        return "document_span"
    if CAPTURE_RE.match(locator):
        return "capture"
    return "path"


def find_claims(text):
    """[(claim text, locator)] for every well-formed `claim: ... [evidence:
    ...]` line: leading list markers and indentation are allowed, trailing
    text after the closing bracket is not (see find_malformed_claims)."""
    return [(claim.strip(), locator.strip())
            for claim, locator, trailing in CLAIM_RE.findall(text)
            if not trailing.strip()]


def find_malformed_claims(text):
    """[(claim text, locator, trailing text)] for every claim line whose
    evidence bracket resolves but is followed by more text on the same line
    (a stray comment, or a second `[evidence: ...]` marker). Never folded
    into find_claims: the trailing text is ambiguous, and treating it as
    part of the claim or the locator would be a guess."""
    return [(claim.strip(), locator.strip(), trailing.strip())
            for claim, locator, trailing in CLAIM_RE.findall(text)
            if trailing.strip()]


def id_index(vault):
    """{id: relpath}. A duplicate keeps the first note found; this checker
    only asks "does this id resolve", never "resolve it uniquely", so a
    duplicate id is a different finding (bm_vault_ids.py's) than this one."""
    by_id = {}
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent vault walk skips a file it cannot read; that note contributes nothing to the id index, same convention as scan() below
            continue
        m = ID_RE.search(_frontmatter(text))
        if not m:
            continue
        value = m.group(1).strip().strip('"').strip("'")
        if ID_VALUE_RE.match(value) and value not in by_id:
            by_id[value] = os.path.relpath(path, vault)
    return by_id


def resolve_path(vault, locator):
    """("ok"|"dangling", detail). A locator escaping the vault root is
    dangling rather than silently followed: this checker only ever reads,
    but a locator string is untrusted input from a note body, and a path
    check that trusts `..` is a trust-boundary gap even in a read-only tool."""
    path_part, _, heading = locator.partition("#")
    if not path_part:
        return "dangling", "empty path in locator %r" % locator
    full = os.path.normpath(os.path.join(vault, path_part))
    vault_norm = os.path.normpath(vault)
    if full != vault_norm and not full.startswith(vault_norm + os.sep):
        return "dangling", "locator escapes the vault root: %r" % locator
    if not os.path.isfile(full):
        return "dangling", "no file at %s" % path_part
    if heading:
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError as exc:
            return "dangling", "could not read %s: %s" % (path_part, exc)
        pattern = re.compile(r"^#{1,6}\s*" + re.escape(heading.strip()) + r"\s*$", re.M)
        if not pattern.search(body):
            return "dangling", "%s has no heading %r" % (path_part, heading)
    return "ok", None


def resolve_id(vault_id_index, locator):
    if locator in vault_id_index:
        return "ok", None
    return "dangling", "id %s resolves to no note" % locator


def _has_pack_files(git_dir):
    pack_dir = os.path.join(git_dir, "objects", "pack")
    try:
        return any(f.endswith(".pack") for f in os.listdir(pack_dir))
    except OSError:
        return False


def _loose_matches(git_dir, sha):
    prefix_dir = os.path.join(git_dir, "objects", sha[:2])
    try:
        names = os.listdir(prefix_dir)
    except OSError:
        return []
    rest = sha[2:]
    return [n for n in names if n.startswith(rest)]


def _loose_object_type(path):
    """The git type word ("commit", "tree", "blob", "tag") read from a loose
    object's own zlib header, or None if the file is missing or is not
    valid zlib-compressed git object data. Reads only the header: max_length
    caps the inflate so a large blob is never decompressed in full just to
    learn its type."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:  # sbe: allow-silent documented above: None when the loose object is missing or not valid zlib data
        return None
    try:
        header = zlib.decompressobj().decompress(raw, 32)
    except zlib.error:  # sbe: allow-silent documented above: None when the loose object is missing or not valid zlib data
        return None
    space = header.find(b" ")
    if space == -1:
        return None
    return header[:space].decode("ascii", "replace")


def resolve_commit(vault, sha):
    """("ok"|"dangling"|"unverifiable", detail). Loose objects only, stdlib
    only: see the module docstring for why a packed repository reports
    unverifiable rather than a false dangling. A loose object matching the
    sha is inflated far enough to read its own type header: a tree, a blob,
    or any other non-commit object at that sha is dangling, not ok, because
    the claim is that this locator names a commit."""
    git_dir = os.path.join(vault, ".git")
    if not os.path.isdir(git_dir):
        return "unverifiable", "vault has no .git directory at its root"
    sha = sha.lower()
    matches = _loose_matches(git_dir, sha)
    if len(matches) > 1:
        return "unverifiable", "sha %s is ambiguous among loose objects" % sha
    if len(matches) == 1:
        obj_type = _loose_object_type(os.path.join(git_dir, "objects", sha[:2], matches[0]))
        if obj_type == "commit":
            return "ok", None
        return "dangling", ("sha %s is loose but not a commit object (type=%s)"
                             % (sha, obj_type or "unreadable"))
    if _has_pack_files(git_dir):
        return "unverifiable", ("commit %s not found loose; this checker cannot "
                                 "read packed objects offline" % sha)
    return "dangling", "commit %s not found in the local loose-object store" % sha


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_jsonl(path):
    """List of dicts from a JSON-lines fixture file, or None when the path
    is absent or unreadable. A malformed line is skipped with a stderr note
    rather than aborting the whole read, matching bm_vault_cite.py's own
    citations-file treatment. An empty-but-readable file returns []."""
    if not path or not os.path.isfile(path):
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
                    sys.stderr.write(
                        "bm_vault_provenance: skipping malformed ledger line %d (%s)\n"
                        % (i, exc))
    except OSError:  # sbe: allow-silent documented above: None when the jsonl path is absent or unreadable
        return None
    return records


def parse_query_locator(locator):
    """(system, query_id, executed_at, result_hash) or None if the locator
    does not carry exactly four non-empty pipe-delimited fields."""
    parts = locator[len("query:"):].split("|")
    if len(parts) != 4 or not all(p.strip() for p in parts):
        return None
    return tuple(p.strip() for p in parts)


def parse_docspan_locator(locator):
    """(document, version_or_hash, page, span_start, span_end) or None."""
    parts = locator[len("docspan:"):].split("|")
    if len(parts) != 5 or not all(p.strip() for p in parts):
        return None
    return tuple(p.strip() for p in parts)


def parse_capture_locator(locator):
    """(path_or_blob_ref, captured_at, sha256) or None."""
    parts = locator[len("capture:"):].split("|")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        return None
    return tuple(p.strip() for p in parts)


def resolve_query(locator, query_ledger):
    """("ok"|"dangling"|"unavailable", detail). Ground truth is a caller-
    supplied query ledger fixture, matching resolve_commit's own pattern of
    checking against a real store rather than trusting the locator's own
    say-so. No ledger, no matching record, or a record whose result_hash
    differs are all "unavailable": this checker was not shown proof the
    query ran with that result, which is not the same claim as "it did
    not"."""
    fields = parse_query_locator(locator)
    if fields is None:
        return ("dangling",
                "malformed query_id locator %r: expected "
                "query:<system>|<query_id>|<executed_at>|<result_hash>" % locator)
    system, query_id, executed_at, result_hash = fields
    records = _read_jsonl(query_ledger)
    if records is None:
        return ("unavailable",
                "no query ledger available to verify system=%s query_id=%s"
                % (system, query_id))
    for rec in records:
        if (rec.get("system") == system and rec.get("query_id") == query_id
                and rec.get("executed_at") == executed_at):
            ledger_hash = rec.get("result_hash")
            if ledger_hash == result_hash:
                return "ok", None
            return ("unavailable",
                     "ledger result_hash differs for system=%s query_id=%s: locator "
                     "has %s, ledger has %s (drift)"
                     % (system, query_id, result_hash, ledger_hash))
    return ("unavailable",
            "no ledger record for system=%s query_id=%s executed_at=%s"
            % (system, query_id, executed_at))


def resolve_document_span(vault, locator, document_store):
    """("ok"|"dangling"|"unavailable", detail). The bound version_or_hash is
    the evidence; a store file present at a DIFFERENT current hash is never
    silently treated as the same evidence -- it is unavailable, naming both
    hashes, exactly the drift this row exists to surface rather than hide."""
    fields = parse_docspan_locator(locator)
    if fields is None:
        return ("dangling",
                "malformed document_span locator %r: expected docspan:<document>|"
                "<version_or_hash>|<page>|<span_start>|<span_end>" % locator)
    document, version, page, start, end = fields
    if not (page.isdigit() and start.isdigit() and end.isdigit()):
        return ("dangling",
                "document_span locator %r has a non-integer page or span field" % locator)
    if not document_store:
        return ("unavailable",
                "no document store provided; cannot verify version %s for document %s"
                % (version[:12], document))
    full = document if os.path.isabs(document) else os.path.join(document_store, document)
    if not os.path.isfile(full):
        return ("unavailable",
                "document store has no file for %s (bound version %s)"
                % (document, version[:12]))
    current = _sha256_file(full)
    if current == version:
        return "ok", None
    return ("unavailable",
            "document %s changed since linking: bound version %s, current version %s "
            "(drift; refusing to resolve against the changed version)"
            % (document, version[:12], current[:12]))


def resolve_capture(vault, locator, capture_root):
    """("ok"|"dangling"|"unavailable"|"tampered", detail). A hash mismatch on
    a file that IS present is reported distinctly from a missing file: the
    bytes being wrong is a different, louder fact than the bytes being
    absent, and this checker never folds the two into one silent bucket."""
    fields = parse_capture_locator(locator)
    if fields is None:
        return ("dangling",
                "malformed capture locator %r: expected "
                "capture:<path_or_blob_ref>|<captured_at>|<sha256>" % locator)
    path_or_ref, captured_at, sha = fields
    root = capture_root or vault
    full = path_or_ref if os.path.isabs(path_or_ref) else os.path.join(root, path_or_ref)
    if not os.path.isfile(full):
        return ("unavailable",
                "capture file not found at %s (captured_at=%s)" % (path_or_ref, captured_at))
    current = _sha256_file(full)
    if current == sha:
        return "ok", None
    return ("tampered",
            "capture hash mismatch for %s: locator declares %s, file now hashes to %s "
            "(TAMPERED or version-drift)" % (path_or_ref, sha[:12], current[:12]))


def scan(vault, query_ledger=None, document_store=None, capture_root=None):
    """[(relpath, claim text, locator, kind, status, detail)] for every claim
    line in the vault. The id index is built once, lazily, only if a claim
    actually needs it."""
    rows = []
    by_id = None
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent vault walk skips a file it cannot read, same convention as id_index above
            continue
        rel = os.path.relpath(path, vault)
        for claim_text, locator in find_claims(text):
            kind = classify_locator(locator)
            if kind == "url":
                status, detail = "unverifiable", "URL locator, resolution unverifiable offline"
            elif kind == "commit":
                status, detail = resolve_commit(vault, locator[len("repo:"):])
            elif kind == "id":
                if by_id is None:
                    by_id = id_index(vault)
                status, detail = resolve_id(by_id, locator)
            elif kind == "query_id":
                status, detail = resolve_query(locator, query_ledger)
            elif kind == "document_span":
                status, detail = resolve_document_span(vault, locator, document_store)
            elif kind == "capture":
                status, detail = resolve_capture(vault, locator, capture_root)
            else:
                status, detail = resolve_path(vault, locator)
            rows.append((rel, claim_text, locator, kind, status, detail))
        for claim_text, locator, trailing in find_malformed_claims(text):
            kind = classify_locator(locator)
            detail = "trailing text after the evidence bracket: %r" % trailing
            rows.append((rel, claim_text, locator, kind, "malformed", detail))
    return rows


def cmd_check(vault, query_ledger=None, document_store=None, capture_root=None):
    notes = 0
    for _ in walk(vault):
        notes += 1
    rows = scan(vault, query_ledger, document_store, capture_root)
    print("vault: %s" % vault)
    print("notes scanned: %d" % notes)
    print("claims found: %d" % len(rows))
    if not rows:
        print("NO-DATA: no claim: lines found (the corpus does not use the "
              "syntax yet); this is not a clean pass")
        return 2
    dangling = [r for r in rows if r[4] == "dangling"]
    unverifiable = [r for r in rows if r[4] == "unverifiable"]
    malformed = [r for r in rows if r[4] == "malformed"]
    unavailable = [r for r in rows if r[4] == "unavailable"]
    tampered = [r for r in rows if r[4] == "tampered"]
    resolving = [r for r in rows if r[4] == "ok"]
    print("claims resolving: %d" % len(resolving))
    print("claims unverifiable offline (URL or unreadable git object, counted, "
          "never a pass and never a fail): %d" % len(unverifiable))
    print("claims DANGLING (locator points at nothing): %d" % len(dangling))
    for rel, claim_text, locator, _kind, _status, detail in dangling:
        print("  %s: claim %r -> evidence %r: %s" % (rel, claim_text, locator, detail))
    print("claims UNAVAILABLE (enterprise evidence not resolvable against the "
          "stores given, named, never silent): %d" % len(unavailable))
    for rel, claim_text, locator, _kind, _status, detail in unavailable:
        print("  %s: claim %r -> evidence %r: %s" % (rel, claim_text, locator, detail))
    print("claims TAMPERED (a capture is present but its hash no longer matches): %d"
          % len(tampered))
    for rel, claim_text, locator, _kind, _status, detail in tampered:
        print("  %s: claim %r -> evidence %r: %s" % (rel, claim_text, locator, detail))
    print("claims MALFORMED (trailing text after the evidence bracket, "
          "including a stray second evidence marker): %d" % len(malformed))
    for rel, claim_text, locator, _kind, _status, detail in malformed:
        print("  %s: claim %r -> evidence %r: %s" % (rel, claim_text, locator, detail))
    return 1 if (dangling or malformed or unavailable or tampered) else 0


def cmd_kinds(as_json):
    if as_json:
        print(json.dumps([{"kind": k, "fields": v} for k, v in KIND_FIELDS.items()],
                          indent=2, sort_keys=False))
    else:
        for k, fields in KIND_FIELDS.items():
            print("%s: %s" % (k, ", ".join(fields)))
    return 0


def _resolve_note_path(vault, note):
    """The absolute path a --note argument names, or None if it resolves to
    nothing: NOTE may be an n-<16 hex> stable id (looked up in the id index)
    or a vault-relative path, the same two shapes every locator in this
    module already accepts. A path escaping the vault root resolves to None
    rather than being followed, matching resolve_path's own trust-boundary
    rule for a locator string."""
    if ID_VALUE_RE.match(note):
        rel = id_index(vault).get(note)
        return os.path.join(vault, rel) if rel else None
    full = os.path.normpath(os.path.join(vault, note))
    vault_norm = os.path.normpath(vault)
    if full != vault_norm and not full.startswith(vault_norm + os.sep):
        return None
    return full if os.path.isfile(full) else None


def cmd_get_evidence(vault, note, claim_substring, query_ledger=None,
                      document_store=None, capture_root=None):
    full = _resolve_note_path(vault, note)
    if full is None:
        sys.stderr.write("bm_vault_provenance: NO-DATA, no note %r in %s\n" % (note, vault))
        return 2
    with open(full, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    match = None
    for claim_text, locator in find_claims(text):
        if claim_substring in claim_text:
            match = (claim_text, locator)
            break
    if match is None:
        sys.stderr.write(
            "bm_vault_provenance: UNKNOWN-ASSERTION, no claim in %s matches %r\n"
            % (note, claim_substring))
        return 2
    claim_text, locator = match
    kind = classify_locator(locator)
    if kind == "url":
        status, detail = "unverifiable", "URL locator, resolution unverifiable offline"
    elif kind == "commit":
        status, detail = resolve_commit(vault, locator[len("repo:"):])
    elif kind == "id":
        status, detail = resolve_id(id_index(vault), locator)
    elif kind == "query_id":
        status, detail = resolve_query(locator, query_ledger)
    elif kind == "document_span":
        status, detail = resolve_document_span(vault, locator, document_store)
    elif kind == "capture":
        status, detail = resolve_capture(vault, locator, capture_root)
    else:
        status, detail = resolve_path(vault, locator)
    if status == "ok":
        print("RESOLVED %s: claim %r -> evidence %r (kind=%s)"
              % (note, claim_text, locator, kind))
        return 0
    if status == "tampered":
        print("TAMPERED %s: claim %r -> evidence %r: %s" % (note, claim_text, locator, detail))
        return 1
    print("UNAVAILABLE %s: claim %r -> evidence %r: %s" % (note, claim_text, locator, detail))
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="bm_vault_provenance", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command")

    p_check = sub.add_parser("check")
    p_check.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_check.add_argument("--query-ledger")
    p_check.add_argument("--document-store")
    p_check.add_argument("--capture-root")

    p_kinds = sub.add_parser("kinds")
    p_kinds.add_argument("--json", action="store_true")

    p_get = sub.add_parser("get-evidence")
    p_get.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_get.add_argument("--note", required=True)
    p_get.add_argument("--claim", required=True)
    p_get.add_argument("--query-ledger")
    p_get.add_argument("--document-store")
    p_get.add_argument("--capture-root")

    args = ap.parse_args(argv)

    if args.command == "kinds":
        return cmd_kinds(args.json)

    if args.command in ("check", "get-evidence"):
        if not args.vault or not os.path.isdir(args.vault):
            print("bm_vault_provenance: NO-DATA, no readable vault at %r" % args.vault,
                  file=sys.stderr)
            return 2

    if args.command == "check":
        return cmd_check(args.vault, args.query_ledger, args.document_store,
                          args.capture_root)
    if args.command == "get-evidence":
        return cmd_get_evidence(args.vault, args.note, args.claim, args.query_ledger,
                                 args.document_store, args.capture_root)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
