#!/usr/bin/env python3
"""bm_vault_intake: the one front door for content entering a vault. WBS VB6-01.

WHY THIS EXISTS. Every vault write so far assumes a human or an already-trusted
tool is holding the pen. Nothing stands between an arbitrary file and 00-Inbox/
that checks it for a credential, a denied term, or a name collision with what
is already there. This is that door: one command, one landing zone, one set of
gates that run BEFORE a byte is written.

  admit --vault V --source SYSTEM --by ACTOR [--restricted]
        [--deny-list PATH] [--locale L] [--as-of DATE] [--encoding CODEC] FILE...

WBS VB6-09 adds a second, smaller door: CAPTURE, for a mid-task thought that
has no source file to admit, only text.

  capture --vault V --by ACTOR [--row ID] [--title T]
          [--expiry-class scratch|lesson-candidate|decision-candidate]
          [--deny-list PATH] [--today DATE] TEXT...   (or piped on stdin)

Capture runs the SAME hard gate admit does -- credential_hit and, when
--deny-list is given, deny_list_hit -- over the title and text combined,
before anything is written; a hit refuses the capture at exit 1 with the
class named only, same contract as admit's own hard rejection.

Files ONE note into 00-Inbox/ with type: capture, lifecycle: candidate,
provenance (captured_by, captured_at, session_context from BM_SESSION_ID when
set, wbs_row when --row is given), and an explicit expiry_at date derived from
--expiry-class (scratch=7d, lesson-candidate=30d, decision-candidate=90d;
default lesson-candidate) and --today or the real clock. It runs the same
duplicate-suspect finder admit's dirt classification uses
(bm_vault_distill._content_overlap via duplicate_suspects) against the
capture's title, so a near duplicate arrives LINKED in duplicate_of, never
silently filed blind and never auto-merged. bm_vault_staleness.py reads
lifecycle/expiry_at to surface an expired, unpromoted capture as its own
census line; nothing here or there is ever deleted.

HARD REJECTION runs first, per file, before anything is written: a credential
shape (the estate's own SECRET_PATTERNS in bm_telemetry.py, loaded by path so
this module never carries a second copy) or a term from --deny-list (matched
via bm_private_scan.py's own term loader and matcher, same reuse rule). A
rejection prints the CLASS NAME ONLY, never the matched value, and writes
nothing for that file. Exit 1 when any file was rejected or admitted with
findings; NO-DATA (a gate module missing or unreadable) also refuses that
file rather than silently admitting it ungated.

Both gates run over an NFKC-normalized, zero-width-and-joiner-stripped view
of the text ALONGSIDE the raw text, so a zero-width space or a fullwidth
homoglyph planted inside a credential or a deny-listed term cannot slip past
a plain-ASCII/plain-utf-8 pattern. And both gates run over EVERY encoding
that decodes the raw bytes strictly (utf-8, cp932, shift_jis, euc_jp,
utf-16, or the codec named by --encoding alone when given), so a deny-listed
non-ASCII term written in a non-UTF-8 file encoding is still caught rather
than silently missed by a scan that only ever looked at UTF-8 bytes.
RESIDUAL LIMIT: an encoding outside that candidate list (and not named by
--encoding) is never guessed at; a file none of the candidates can decode is
refused with class=unscannable-encoding at exit 1, not admitted unscanned.

DIRT CLASSIFICATIONS are computed at arrival, for every file that clears the
hard gate, and recorded in the note's own frontmatter, never silently fixed:
  encoding-suspect     bytes are not valid UTF-8, or a mojibake heuristic hits
  duplicate-suspect    title-word overlap with an existing note, reusing
                       bm_vault_distill's own _content_overlap so this tool's
                       idea of "close" can never drift from bm_vault_curate's
  missing-provenance   --source or --by was not given; the field records
                       NO-DATA rather than being invented
  stale-copy-suspect   the filename or the leading body carries a copy/final/
                       v2-style marker
  echo-of-vault-answer the file carries bm_vault_audit.py's own VB6-06
                       derived-from-vault marker AND the marker's event id
                       is confirmed present in bm_vault_audit's own access
                       log (has_event()): this content was itself served
                       by a real recall, so admitting it back in is an ECHO
                       of that source, never a second one agreeing.
                       Detection never rejects, it only classifies; the
                       source event id is recorded as echo_of_vault_event
                       in frontmatter. THE ONE EXCLUSION THIS ENFORCES
                       TODAY: an echoed file is never run through the
                       duplicate-suspect overlap check, so it can never
                       inflate a corroboration signal built on that dirt
                       class (see corroboration_count below); no other
                       corroboration-count feature exists elsewhere in
                       this tree to update (checked: bm_vault_curate.py
                       and bm_vault_distill.py title-overlap finders count
                       pairs of EXISTING vault notes, not admitted files,
                       and have no echo concept to exclude from).
  forged-vault-marker  a MAJOR review finding, fixed here: a marker-shaped
                       line whose event id is NOT in the access log (the
                       log is readable, the id is simply absent) is never
                       trusted as an echo -- a hand-written hex32 line of
                       the right shape must not launder a real duplicate
                       out of corroboration. It stays in the
                       duplicate-suspect pool, is counted as independent
                       exactly like any other note, and is flagged so a
                       reviewer can see the forgery attempt.
  unverifiable-vault-marker a marker-shaped line found while the access
                       log itself is absent or unreadable, so the id could
                       not be checked either way. Never silently trusted:
                       it stays in the duplicate-suspect pool, counted as
                       independent, and flagged, the same as a forged one.
                       KNOWN LIMITATION carried from bm_vault_audit.py's
                       own docstring: this marker check catches an honest
                       tooling round trip and a naive hand-written forgery,
                       never a deliberate strip-and-resubmit (stripping the
                       marker line before pasting the content back in
                       defeats detection entirely, since there is no
                       content-level match against a served answer yet).
                       Content-level matching against bm_vault.py's own
                       answer ledger is a FUTURE control, not implemented
                       anywhere in this tree today.

QUARANTINE. --restricted routes the note into 00-Inbox/quarantine/ instead of
00-Inbox/ directly (still inside the inbox: this tool never writes outside it)
and marks the note `restricted: true`. A real vault access-policy.json rule
denying 00-Inbox/quarantine/* is what makes recall actually withhold it
(bm_vault_policy.py, VB2-01); this tool only places the item where such a
rule can name it.

SPREADSHEET CAPSULES. .csv, .tsv and .xlsx (noted as unparsed: this tool never
opens the zip) get five capsule_* frontmatter fields recording what intake
could and could not determine: format, encoding-with-loss-flag, locale
(NO-DATA unless --locale), as-of (NO-DATA unless --as-of), a displayed-versus-
stored marker (NO-DATA for csv/tsv/xlsx, with the reason stated inline), and a
reproducibility marker (NO-DATA: intake never re-runs the source system).

Original bytes are never lost: valid UTF-8 goes into the note body verbatim;
anything else is copied byte-for-byte into 00-Inbox/originals/ and the note
body points at the sidecar rather than guessing a decode.

Exit 0: every file admitted clean. Exit 1: at least one file was hard-rejected
or a gate could not run. Exit 2: no readable vault, or bm_vault_ids.py (the
id minter) is missing, since this tool refuses to admit without a stable id.
Python 3.9, standard library only, no network.
"""
import argparse
import datetime
import importlib.util
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = "00-Inbox"
DUP_THRESHOLD = 0.5  # bm_vault_curate's own default; kept identical on purpose

STALE_MARKERS = re.compile(r"\b(copy|final|draft|old|backup|v\d+)\b", re.I)
# ponytail: a short literal alternation of known mis-decode fragments, not a
# real charset detector; a character-class range after "Ã"/"â€" would also
# fire on ordinary accented prose. Upgrade only if measured missing real
MOJIBAKE_RE = re.compile(u"�|â€™|â€œ|â€|â€“|â€”|Ã©|Ã¨|Ã¯Â»Â¿|Ã¢â‚¬")
SPREADSHEET_EXTS = {".csv", ".tsv", ".xlsx"}

# Zero-width and joiner characters a credential shape can be salted with to
# defeat an ASCII regex without changing how the string displays: zero width
# space, zero width non-joiner, zero width joiner, word joiner, and the
# zero-width-no-break-space/BOM form.
_ZERO_WIDTH_RE = re.compile(u"[​‌‍⁠﻿]")

# The candidate decodes attempted for a file whose bytes are not valid UTF-8,
# tried in this order. Not exhaustive (there is no closed list of possible
# encodings); a file that fails every one of these is refused rather than
# admitted unscanned (class=unscannable-encoding in _admit_one).
DECODE_CANDIDATES = ["utf-8", "cp932", "shift_jis", "euc_jp", "utf-16"]


def _normalized_view(text):
    """NFKC-normalize and strip zero-width/joiner characters, so a fullwidth
    homoglyph or a zero-width-space-salted credential shape still matches the
    ASCII gate regexes. Returned ALONGSIDE the raw text by every caller,
    never in place of it, so a shape only visible in the untouched original
    still fires too."""
    return _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", text))


def _decode_candidates(raw, forced_encoding=None):
    """[(encoding_name, text), ...] for every codec that decodes raw bytes
    STRICTLY (no errors="replace" papering over a bad decode). forced_encoding
    (--encoding) is tried alone, in place of the candidate list, when given."""
    codecs_to_try = [forced_encoding] if forced_encoding else DECODE_CANDIDATES
    out = []
    for enc in codecs_to_try:
        try:
            out.append((enc, raw.decode(enc)))
        except (LookupError, UnicodeDecodeError):
            continue
    return out


def _load_sibling(name):
    """tools/<name>.py loaded BY PATH, guarded, the pattern every sibling
    bm_vault_* module already uses (bm_vault_lint, bm_vault_curate). Returns
    the module, or None when the file is absent or fails to import, so a
    missing contract module is a named NO-DATA at the call site rather than a
    silent pass."""
    path = os.path.join(HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional sibling module load failure, caller degrades on a None return
        return None


def _today():
    return datetime.date.today().isoformat()


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "note"


def _slugify(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return _slug(stem)


def _parse_date_arg(raw):
    try:
        return datetime.date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------- hard gate

def credential_hit(text):
    """(hit, no_data_reason). Reuses bm_telemetry.py's own SECRET_PATTERNS
    (the estate's secret regexes) rather than a second copy. no_data_reason
    is set, never both fields, when the module cannot be loaded at all.
    Scans BOTH the raw text and its NFKC-normalized, zero-width-stripped
    view, so a zero-width space or a fullwidth homoglyph planted inside a
    credential shape cannot slip past the plain-ASCII patterns."""
    telemetry = _load_sibling("bm_telemetry")
    if telemetry is None or not hasattr(telemetry, "SECRET_PATTERNS"):
        return False, ("NO-DATA: bm_telemetry.SECRET_PATTERNS unavailable, "
                        "the credential gate could not run")
    for view in (text, _normalized_view(text)):
        for pat in telemetry.SECRET_PATTERNS:
            if pat.search(view):
                return True, None
    return False, None


def deny_list_hit(text, deny_list_path):
    """(hit, no_data_reason) against --deny-list, reusing bm_private_scan.py's
    own term loader and byte matcher (never a second regex builder). Takes
    already-decoded TEXT (from any successful candidate decode, see
    _decode_candidates), re-encodes both the raw and normalized views to
    utf-8 bytes, and matches against the deny-list's own utf-8-encoded terms,
    so a non-ASCII term is caught regardless of which byte encoding the
    source file was written in or whether it was salted with zero-width
    characters."""
    scan = _load_sibling("bm_private_scan")
    if scan is None:
        return False, "NO-DATA: bm_private_scan.py unavailable, the deny-list gate could not run"
    terms, reason = scan._load_terms(deny_list_path)
    if terms is None:
        return False, "NO-DATA: deny-list unreadable, %s" % reason
    short_patterns, long_patterns = scan._build_patterns(terms)
    for view in (text, _normalized_view(text)):
        if scan._scan_bytes(view.encode("utf-8"), short_patterns, long_patterns):
            return True, None
    return False, None


def hard_gate(text, deny_list_path):
    """(ok, message_or_None). The SAME two hard gates _admit_one runs --
    credential_hit then, when deny_list_path is given, deny_list_hit -- both
    already scanning text's own NFKC-normalized, zero-width-stripped view
    alongside the raw text (see credential_hit/deny_list_hit docstrings).
    Shared by admit and capture so neither can drift from the other's
    hard-rejection-before-write contract. message_or_None is a
    "class=..."/NO-DATA reason, never the matched value."""
    hit, no_data = credential_hit(text)
    if no_data:
        return False, no_data
    if hit:
        return False, "class=credential-shape"
    if deny_list_path:
        hit, no_data = deny_list_hit(text, deny_list_path)
        if no_data:
            return False, no_data
        if hit:
            return False, "class=deny-list-term"
    return True, None


# ------------------------------------------------------------ dirt classes

def _valid_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _existing_titles(ids_mod, vault):
    """[(title, relpath, id_or_None)] for every note already in the vault,
    title from the first H1 or the filename stem. Used only for the
    duplicate-suspect finder; a note this tool cannot read is skipped, never
    a crash for the whole batch."""
    out = []
    h1 = re.compile(r"^#\s+(.+)$", re.M)
    for path in ids_mod.walk(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:  # sbe: allow-silent a note this tool cannot read is skipped per docstring, never a crash for the whole batch
            continue
        m = h1.search(body)
        title = m.group(1).strip() if m else os.path.splitext(os.path.basename(rel))[0]
        out.append((title, rel, ids_mod.read_id(body)))
    return out


def duplicate_suspects(distill_mod, candidate_title, existing):
    """[(id_or_relpath, score)] over threshold, via bm_vault_curate's own
    finder function (bm_vault_distill._content_overlap) so "close" can never
    mean something different here than it does in the curation queue."""
    hits = []
    for title, rel, nid in existing:
        score = distill_mod._content_overlap(candidate_title, title)
        if score >= DUP_THRESHOLD:
            hits.append((nid or rel, round(score, 3)))
    return hits


def classify_marker(audit_mod, text):
    """(status, event_id) where status is "echo", "forged", "unverifiable", or None
    (no marker line present at all, the common case). THE FIX for the forgeable-marker
    MAJOR: detect_marker() alone only proves a line of the right SHAPE is present, and
    a hand-written hex32 matches that shape exactly as well as a real recall's marker
    does. This is the ONE place that decides whether a detected marker is trusted,
    by asking bm_vault_audit.has_event() whether the log actually recorded it:
      echo         the log is readable and the event id is in it: a real recall.
      forged       the log is readable and the event id is NOT in it: a hand-written
                   line of the marker's shape, never produced by a recall.
      unverifiable the log itself is absent or unreadable, so neither could be
                   proven; never silently treated as either echo or forged.
    audit_mod is None (the gate module itself could not be loaded) degrades to no
    marker detected at all, same as no marker present -- callers report that
    degradation themselves (see the NO-DATA stderr line and echo_detection
    frontmatter field at the call site in _admit_one)."""
    if audit_mod is None:
        return None, None
    event_id = audit_mod.detect_marker(text)
    if not event_id:
        return None, None
    found, no_data = audit_mod.has_event(event_id)
    if no_data:
        return "unverifiable", event_id
    return ("echo", event_id) if found else ("forged", event_id)


def classify_dirt(raw, text, filename, source, by, marker_status=None):
    dirt = []
    if not _valid_utf8(raw) or MOJIBAKE_RE.search(text):
        dirt.append("encoding-suspect")
    if not source or not by:
        dirt.append("missing-provenance")
    stem = os.path.splitext(os.path.basename(filename))[0]
    if STALE_MARKERS.search(stem) or STALE_MARKERS.search(text[:2000]):
        dirt.append("stale-copy-suspect")
    if marker_status == "echo":
        dirt.append("echo-of-vault-answer")
    elif marker_status == "forged":
        dirt.append("forged-vault-marker")
    elif marker_status == "unverifiable":
        dirt.append("unverifiable-vault-marker")
    return dirt


def corroboration_count(vault, ids_mod):
    """How many notes already in the vault carry dirt_classes: duplicate-suspect --
    the one corroboration-like signal this tree has (a second file whose title/content
    overlaps an existing note). A note ALSO carrying echo-of-vault-answer can never be
    counted here, because _admit_one below never runs the duplicate-suspect overlap
    check on an echo in the first place: the two dirt classes cannot co-occur, so this
    reads as a plain count, and the exclusion is structural rather than a filter bolted
    on after the fact. No other corroboration-count feature exists in this tree to wire
    this into (see the module docstring's DIRT CLASSIFICATIONS section)."""
    count = 0
    for path in ids_mod.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:  # sbe: allow-silent an unreadable note is excluded from the count, same skip-not-crash stance as _titles above
            continue
        m = re.search(r"^dirt_classes:\s*(.+)$", body, re.M)
        if m and "duplicate-suspect" in [c.strip() for c in m.group(1).split(",")]:
            count += 1
    return count


# ------------------------------------------------------------ capsule fields

def capsule_fields(ext, raw, args):
    """The five spreadsheet interpretation fields (point 3), plus a format
    marker. .xlsx is a binary zip container this tool never opens, so its
    encoding and displayed-versus-stored fields are NO-DATA by construction,
    not by omission."""
    if ext == ".xlsx":
        fmt = "xlsx-unparsed"
        encoding = "NO-DATA (binary spreadsheet container, no text encoding to declare)"
        dvs = "NO-DATA (xlsx not parsed at intake; cell display formatting unread)"
    else:
        fmt = "tsv" if ext == ".tsv" else "csv"
        encoding = "utf-8 (loss=false)" if _valid_utf8(raw) else "unknown (loss=true, utf-8 decode failed)"
        dvs = "NO-DATA (csv/tsv carries no display formatting distinct from the stored value)"
    return [
        ("capsule_format", fmt),
        ("capsule_encoding", encoding),
        ("capsule_locale", args.locale or "NO-DATA (not given at intake)"),
        ("capsule_as_of", args.as_of or "NO-DATA (not given at intake)"),
        ("capsule_display_vs_stored", dvs),
        ("capsule_reproducible", "NO-DATA (re-running the source system was not observed at intake)"),
    ]


# ------------------------------------------------------------------ writing

def _sanitize_frontmatter_scalar(value):
    """Neutralizes a value about to be written verbatim into a raw
    'key: value' YAML frontmatter line: strips CR/LF (a newline there would
    inject an arbitrary new frontmatter key on the next line) and any
    leading '-' (YAML would read that as a list marker). Defense in depth
    only: bm_vault_exchange already validates bundle_id's charset before an
    exchange-sourced value can reach here, but this is the one place every
    caller's --source value actually lands in the note, so it is guarded
    here too."""
    if value is None:
        return value
    cleaned = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    cleaned = cleaned.lstrip("-").strip()
    return cleaned if cleaned else "NO-DATA (sanitized empty)"


def _build_note(note_id, title, source, by, restricted, dirt, dup_links,
                 capsule, original_sidecar, valid_utf8, text, original_path,
                 echo_event_id=None, echo_detection_no_data=False):
    lines = ["---",
             "id: %s" % note_id,
             "type: reference",
             "status: open",
             "created: %s" % _today(),
             "promotion: candidate",
             "ingested_at: %s" % _today(),
             "provenance_source: %s" % _sanitize_frontmatter_scalar(
                 source or "NO-DATA (--source not given)"),
             "provenance_actor: %s" % (by or "NO-DATA (--by not given)"),
             "provenance_ingested_at: %s" % _now_iso(),
             "provenance_original_path: %s" % original_path]
    if by:
        # V14.1: the author of record for the separation-of-duties check in
        # bm_vault_promotions.cmd_promote. Written only when a real actor was
        # given, same fail-closed posture as before: no --by still means no
        # author: field, so promote's own "no author of record" refusal
        # still fires exactly as it did before this row.
        lines.append("author: %s" % by)
    if restricted:
        lines.append("restricted: true")
    if dirt:
        lines.append("dirt_classes: %s" % ", ".join(dirt))
    if echo_event_id:
        lines.append("echo_of_vault_event: %s" % echo_event_id)
    if echo_detection_no_data:
        lines.append("echo_detection: NO-DATA")
    if dup_links:
        lines.append("duplicate_of: %s" % ", ".join(str(x[0]) for x in dup_links))
    if original_sidecar:
        lines.append("original_sidecar: %s" % original_sidecar)
    for key, value in capsule:
        lines.append("%s: %s" % (key, value))
    lines += ["---", "", "# %s" % title, ""]
    if valid_utf8:
        lines.append(text)
    else:
        lines.append("Original bytes are not valid UTF-8. Preserved verbatim "
                      "at sidecar `%s`." % original_sidecar)
    body = "\n".join(lines)
    return body if body.endswith("\n") else body + "\n"


def _admit_one(src, args, ids_mod, distill_mod, taken_ids, existing_titles):
    """(ok, message). Writes at most one note (and its sidecar, both inside
    00-Inbox/) when ok is True; writes nothing at all when ok is False."""
    if not os.path.isfile(src):
        return False, "REJECT %s: class=source-not-found" % src
    with open(src, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")

    # Every gate runs over every successful decode of the raw bytes, not just
    # a lossy utf-8-with-replace view: a deny-listed non-ASCII term or a
    # credential shape written in cp932/shift_jis/euc_jp/utf-16 must not slip
    # past a scan that only ever looked at utf-8 text. A file none of the
    # candidate codecs can decode is not scannable at all and is refused
    # outright, rather than silently admitted ungated.
    decodes = _decode_candidates(raw, args.encoding)
    if not decodes:
        if args.encoding:
            return False, ("REJECT %s: class=unscannable-encoding, --encoding %s "
                            "failed to decode this file" % (src, args.encoding))
        return False, ("REJECT %s: class=unscannable-encoding, no candidate codec "
                        "(%s) decoded this file; supply --encoding CODEC"
                        % (src, ", ".join(DECODE_CANDIDATES)))

    for _enc, dtext in decodes:
        hit, no_data = credential_hit(dtext)
        if no_data:
            return False, "REJECT %s: %s" % (src, no_data)
        if hit:
            return False, "REJECT %s: class=credential-shape" % src

    if args.deny_list:
        for _enc, dtext in decodes:
            hit, no_data = deny_list_hit(dtext, args.deny_list)
            if no_data:
                return False, "REJECT %s: %s" % (src, no_data)
            if hit:
                return False, "REJECT %s: class=deny-list-term" % src

    note_id = ids_mod.mint(taken_ids)
    taken_ids.add(note_id)
    # VB6-06: self-echo provenance. bm_vault_audit.py is the ONE owner of the marker's
    # shape (see its module docstring); loaded by path here, the same guarded pattern
    # every sibling gate above already uses. An absent module is a NAMED degradation
    # (MAJOR review), never a silent one: it is reported on stderr and recorded in the
    # note's own frontmatter (echo_detection: NO-DATA below), because a recall that
    # cannot tell an echo from a fresh source must say so in both channels, not just
    # exit 0 as if nothing were missing. Checked over the decoded text, not the raw
    # bytes, since the marker is plain ASCII text a recall printed.
    audit_mod = _load_sibling("bm_vault_audit")
    echo_detection_no_data = audit_mod is None
    if echo_detection_no_data:
        sys.stderr.write("bm_vault_intake: NO-DATA, bm_vault_audit.py unavailable; "
                          "echo-of-vault-answer detection could not run for %s\n" % src)
    # MAJOR fix: a marker-shaped line is never trusted on shape alone. classify_marker
    # verifies the detected event id against bm_vault_audit's own access log before
    # this file is treated as an echo, so a hand-written hex32 line of the marker's
    # shape cannot launder a real duplicate out of corroboration (see forged-vault-marker
    # and unverifiable-vault-marker in the module docstring).
    marker_status, marker_event_id = classify_marker(audit_mod, text)
    dirt = classify_dirt(raw, text, src, args.source, args.by, marker_status)
    echo_event_id = marker_event_id if marker_status == "echo" else None

    candidate_title = re.sub(r"[-_]+", " ", os.path.splitext(os.path.basename(src))[0]).strip()
    dup_links = []
    # THE EXCLUSION (VB6-06, narrowed by the MAJOR fix above): only a CONFIRMED echo
    # skips the duplicate-suspect overlap check. A forged or unverifiable marker is not
    # proven to be the same source, so it runs the overlap check like any ordinary file
    # and is counted as independent if it matches -- exactly the outcome the forgeable-
    # marker fix exists to force, since excluding an unverified marker from
    # corroboration is the same evidence-laundering hole with an extra step.
    if distill_mod is not None and marker_status != "echo":
        dup_links = duplicate_suspects(distill_mod, candidate_title, existing_titles)
        if dup_links:
            dirt.append("duplicate-suspect")

    ext = os.path.splitext(src)[1].lower()
    capsule = capsule_fields(ext, raw, args) if ext in SPREADSHEET_EXTS else []

    vault = args.vault
    inbox_dir = os.path.join(vault, INBOX)
    target_dir = os.path.join(inbox_dir, "quarantine") if args.restricted else inbox_dir
    originals_dir = os.path.join(inbox_dir, "originals")
    slug = _slugify(src)
    valid_utf8 = _valid_utf8(raw)

    original_sidecar = None
    if not valid_utf8:
        os.makedirs(originals_dir, exist_ok=True)
        side_name = "%s-%s%s" % (slug, note_id[-8:], ext or ".bin")
        side_path = os.path.join(originals_dir, side_name)
        with open(side_path, "wb") as fh:
            fh.write(raw)
        original_sidecar = os.path.relpath(side_path, vault).replace(os.sep, "/")

    note_text = _build_note(note_id, candidate_title, args.source, args.by,
                             args.restricted, dirt, dup_links, capsule,
                             original_sidecar, valid_utf8, text, src,
                             echo_event_id, echo_detection_no_data)
    os.makedirs(target_dir, exist_ok=True)
    note_path = os.path.join(target_dir, "%s-%s.md" % (slug, note_id[-8:]))
    with open(note_path, "w", encoding="utf-8") as fh:
        fh.write(note_text)

    rel_note = os.path.relpath(note_path, vault).replace(os.sep, "/")
    existing_titles.append((candidate_title, rel_note, note_id))
    return True, ("ADMITTED %s -> %s  id=%s  dirt=%s"
                  % (src, rel_note, note_id, ",".join(dirt) if dirt else "none"))


# ------------------------------------------------------------ capture verb
#
# WBS VB6-09: the mid-task capture verb. Extends this module rather than a new
# one, because capture reuses the exact same id minting, dedup finder and
# 00-Inbox landing zone admit already owns; a second module would either
# duplicate that or import this one, and this one already imports nothing
# capture-specific in return.

EXPIRY_CLASSES = {
    "scratch": 7,
    "lesson-candidate": 30,
    "decision-candidate": 90,
}
DEFAULT_EXPIRY_CLASS = "lesson-candidate"


def expiry_date(expiry_class, today):
    """today + the class's horizon, or None for an unknown class (the caller
    validates against EXPIRY_CLASSES before this is ever reached in practice;
    None here is a defensive fallback, never invented)."""
    days = EXPIRY_CLASSES.get(expiry_class)
    return None if days is None else today + datetime.timedelta(days=days)


def _build_capture_note(note_id, title, text, by, row, expiry_class, expires,
                         session_context, dup_links):
    lines = ["---",
             "id: %s" % note_id,
             "type: capture",
             "status: open",
             "created: %s" % _today(),
             "lifecycle: candidate",
             "captured_by: %s" % (by or "NO-DATA (--by not given)"),
             "captured_at: %s" % _now_iso(),
             "session_context: %s" % (session_context or "NO-DATA (BM_SESSION_ID not set)"),
             "wbs_row: %s" % (row or "NO-DATA (--row not given)"),
             "expiry_class: %s" % expiry_class,
             "expiry_at: %s" % expires.isoformat()]
    if dup_links:
        lines.append("duplicate_of: %s" % ", ".join(str(x[0]) for x in dup_links))
    lines += ["---", "", "# %s" % title, "", text]
    body = "\n".join(lines)
    return body if body.endswith("\n") else body + "\n"


def cmd_capture(args):
    """(exit code). Files ONE candidate note into 00-Inbox/ with provenance and
    an explicit expiry date, running the same dedup-suspect finder admit uses
    (bm_vault_distill._content_overlap via duplicate_suspects) so a near-
    duplicate arrives LINKED, never silently filed blind and never auto-merged.
    A missing finder module never blocks the capture, same contract as admit's
    own gate degradations: the capture still lands, just unlinked, and nothing
    is invented in its place."""
    vault = args.vault
    if not vault or not os.path.isdir(vault):
        print("bm_vault_intake: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2
    ids_mod = _load_sibling("bm_vault_ids")
    if ids_mod is None:
        print("bm_vault_intake: NO-DATA, bm_vault_ids.py not found or not importable; "
              "refusing to capture without a stable id", file=sys.stderr)
        return 2

    text = " ".join(args.text).strip() if args.text else sys.stdin.read().strip()
    if not text:
        print("bm_vault_intake: capture needs TEXT, as trailing words or on stdin",
              file=sys.stderr)
        return 2

    today = datetime.date.today()
    if args.today:
        today = _parse_date_arg(args.today)
        if today is None:
            print("bm_vault_intake: --today needs YYYY-MM-DD, got %r" % args.today,
                  file=sys.stderr)
            return 2

    expiry_class = args.expiry_class or DEFAULT_EXPIRY_CLASS
    expires = expiry_date(expiry_class, today)
    title = args.title or text[:60].strip()

    # MAJOR fix: the same hard-rejection-before-write gate admit runs, now run
    # here too, over title and text combined, before anything (id mint, dedup
    # read, directory, note file) touches disk. A hit refuses the capture at
    # exit 1 with the class named, never the matched value; nothing is written.
    ok, reject = hard_gate(title + "\n" + text, args.deny_list)
    if not ok:
        print("bm_vault_intake: REJECT capture, %s" % reject, file=sys.stderr)
        return 1

    distill_mod = _load_sibling("bm_vault_distill")
    dup_links = []
    if distill_mod is not None:
        existing_titles = _existing_titles(ids_mod, vault)
        dup_links = duplicate_suspects(distill_mod, title, existing_titles)
    else:
        sys.stderr.write("bm_vault_intake: NO-DATA, bm_vault_distill.py unavailable; "
                          "duplicate-suspect linkage could not run for this capture\n")

    taken_ids = set(ids_mod.index(vault)[0])
    note_id = ids_mod.mint(taken_ids)
    session_context = os.environ.get("BM_SESSION_ID")

    note_text = _build_capture_note(note_id, title, text, args.by, args.row,
                                     expiry_class, expires, session_context, dup_links)
    inbox_dir = os.path.join(vault, INBOX)
    os.makedirs(inbox_dir, exist_ok=True)
    note_path = os.path.join(inbox_dir, "%s-%s.md" % (_slug(title), note_id[-8:]))
    with open(note_path, "w", encoding="utf-8") as fh:
        fh.write(note_text)

    rel_note = os.path.relpath(note_path, vault).replace(os.sep, "/")
    msg = ("CAPTURED %s  id=%s  expiry=%s (%s)"
           % (rel_note, note_id, expires.isoformat(), expiry_class))
    if dup_links:
        msg += "  duplicate_of=%s" % ",".join(str(x[0]) for x in dup_links)
    print(msg)
    return 0


def cmd_admit(args):
    vault = args.vault
    if not vault or not os.path.isdir(vault):
        print("bm_vault_intake: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2
    ids_mod = _load_sibling("bm_vault_ids")
    if ids_mod is None:
        print("bm_vault_intake: NO-DATA, bm_vault_ids.py not found or not importable; "
              "refusing to admit without a stable id", file=sys.stderr)
        return 2
    distill_mod = _load_sibling("bm_vault_distill")

    taken_ids = set(ids_mod.index(vault)[0])
    existing_titles = _existing_titles(ids_mod, vault) if distill_mod is not None else []

    admitted = 0
    rejected = 0
    for src in args.files:
        ok, message = _admit_one(src, args, ids_mod, distill_mod, taken_ids, existing_titles)
        print(message)
        if ok:
            admitted += 1
        else:
            rejected += 1
    print("%d admitted, %d rejected" % (admitted, rejected))
    return 1 if rejected else 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    pa = sub.add_parser("admit", help="admit one or more files into 00-Inbox/")
    pa.add_argument("--vault", required=True)
    pa.add_argument("--source", default=None, help="the source system; NO-DATA if omitted")
    pa.add_argument("--by", default=None, help="the actor; NO-DATA if omitted")
    pa.add_argument("--restricted", action="store_true",
                     help="quarantine into 00-Inbox/quarantine/")
    pa.add_argument("--deny-list", default=None, help="path to a deny-list terms file")
    pa.add_argument("--locale", default=None, help="declared locale for a spreadsheet capsule")
    pa.add_argument("--as-of", default=None, help="declared as-of date for a spreadsheet capsule")
    pa.add_argument("--encoding", default=None,
                     help="force decode with this codec instead of the candidate list "
                          "(utf-8, cp932, shift_jis, euc_jp, utf-16); a file that fails "
                          "it refuses with class=unscannable-encoding")
    pa.add_argument("files", nargs="+")

    pc = sub.add_parser("capture", help="capture one candidate note into 00-Inbox/")
    pc.add_argument("--vault", required=True)
    pc.add_argument("--by", required=True, help="the capturing actor")
    pc.add_argument("--row", default=None, help="WBS row id; NO-DATA if omitted")
    pc.add_argument("--title", default=None,
                     help="note title; derived from the leading text when omitted")
    pc.add_argument("--expiry-class", default=None, choices=sorted(EXPIRY_CLASSES),
                     help="expiry vocabulary (default %s)" % DEFAULT_EXPIRY_CLASS)
    pc.add_argument("--today", default=None,
                     help="treat this YYYY-MM-DD as today, for calibration")
    pc.add_argument("--deny-list", default=None, help="path to a deny-list terms file")
    pc.add_argument("text", nargs="*", help="capture text; read from stdin if omitted")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return cmd_capture(args) if args.command == "capture" else cmd_admit(args)


if __name__ == "__main__":
    sys.exit(main())
