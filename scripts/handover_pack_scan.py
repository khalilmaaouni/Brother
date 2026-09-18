#!/usr/bin/env python3
"""handover_pack_scan: the dry run the founder reads before any pack is rewritten.

Every handover pack under ~/Documents/BrotherModeUp-handovers is handed to a
session on ANOTHER ACCOUNT (the closing ceremony law, docs/plan). A private
term sitting anywhere in one of those packs is a term leaving the machine, and
the closing ceremony check has only ever scanned the NEWEST pack's markdown.
A 2026-09-03 audit found 24 term-bearing files sitting in OLDER packs that
check never looked at: 11 in plain text files, 6 inside zip archives, and 1
whose own FILE NAME carries the term (readiness row E35).

This tool is READ-ONLY: it renames nothing, rewrites nothing, deletes
nothing. It only reports. The actual sweep that rewrites or renames an
offending file is a separate, deliberate, founder-approved act; this is the
dry run the founder reads before that sweep runs, never the sweep itself.

WHAT IT SCANS, under --root (default the handovers directory above):
  * every file and directory NAME, whatever its extension;
  * the raw BYTES of every file, whatever its extension, decoded as UTF-8
    with undecodable bytes replaced (a replaced byte is a non-word
    character, so it bounds a short term like any other separator);
  * every ZIP archive's member NAMES and the raw bytes of EVERY member, and
    a zip nested inside a zip the same way, up to MAX_ZIP_DEPTH levels.
    A file is read as a zip when its name ends in .zip OR its bytes say it
    is one (zipfile.is_zipfile), so .xlsx and .docx, whose members are
    deflated and invisible to a raw byte search, have their members read.
    Members are streamed from zipfile, never extracted to disk.

WHY EVERY FILE (decided 2026-09-18): the old rule read content only for
an allowlist of text extensions, and a 25 MiB .duckdb inside a pack
carried a term a byte search found while this scanner reported 0 hits,
so close_ceremony_check.py printed PASS over it. The alternative, calling
every non-allowlisted file NO-DATA, was rejected: 1,690 files in the root
that day sat outside the allowlist (738 with no extension, 117 .patch, 52
.log, most of them plain text), so the ceremony could never PASS again
and a gate that can never pass gets bypassed. Reading every byte keeps the
gate able to pass while making it see everything it passes over.

COMPRESSED MEDIA: a file or member whose first bytes say image or audio
(PNG, JPEG, GIF, WebP, MP3, MP4/M4A) is checked for terms over
SHORT_TERM_MAX_LEN characters only. Its payload is compressed noise, and
measured on the real root 2026-09-18 the 832 MiB of PNG members gave 5
chance matches of a 4 letter term (mixed case, random bytes around each),
the rate (2/256)^4 per byte predicts; a longer term's chance rate there
is about 1 in 5,000 per GiB. A gate that fails on noise forever gets
bypassed like one that can never pass. Cost: a short term written into
an image's or a recording's metadata is not caught.

KNOWN LIMIT: a term inside a compressed stream that is NOT a zip (a PDF's
deflated page, a gzip, a git .pack, image pixel data) is not decoded, and
neither is UTF-16 text. Those bytes are read, but a compressed term does
not appear in them.

THE RULE, same one this estate's other scanners use (bm_private_scan.py):
the term's LENGTH decides its strictness, never its stored spelling. A term
of five characters or fewer matches as a WHOLE WORD (bounded by a
non-word character or the string edge), case insensitively. A term over
five characters matches as a plain substring, case insensitively.

The term list itself lives OUTSIDE every repository, at
~/.brothersbe-private-names by default (one term per line, # comments), and
is never printed.

SCOPE DIRECTIVE (founder rule 2026-09-12: the first target app is never
named inside Brother's OWN tree, hub, export, bundle, docs, screens; a
handover pack is a different, private tree and may name it). A comment line
`# scope: brother-tree` starts a block of terms that bind ONLY Brother's own
tree; `# scope: all` ends it. THIS scanner (and cutover_pack.py, which loads
terms through the same function) SKIPS every term inside such a block,
because a pack is not Brother's tree. Every OTHER reader of the list treats
a `#` line as a plain comment and so keeps enforcing those terms globally,
which for the hub-tree scanners (private_terms_scan.py, export_public.py,
doc_assurance.py) is exactly the rule. The directive is a comment on purpose:
a suffix on the term line would make those readers match a string that
never occurs and silently disable the term. The SCAN SUMMARY line prints
how many terms were scoped out, never which. NEVER print a term anywhere: every hit line and every
printed path names only the character COUNT of the term that matched, and a
path that itself carries a term has that term masked to <N> before it is
ever printed, the same way a hit found because of its name is printed.

A file, a whole zip, or a zip member this tool could not open or decode
(permission denied, corrupt, encrypted) is a DIFFERENT outcome from reading
it and finding it clean (SBE law L11, silent-failure-lints): its path is
counted in the "unreadable" bucket, printed one line per path (never its
content, since there is none this tool could read), and folded into the
SCAN SUMMARY line's own count.

Exit codes: 0 no hit found and nothing was unreadable. 1 one or more hits
found (each printed, never the term, then a summary line with the count by
kind). 2 NO-DATA: the terms list is missing or empty, --root does not exist,
or one or more paths were unreadable and that was the ONLY thing found (an
actual hit still takes priority and reads exit 1); NO-DATA is never a pass,
because a scan that could not open every file cannot call the tree clean.

Python 3, standard library only. No network. Nothing written to disk BY
DEFAULT: pass --cache PATH (opt in, never the default) to persist a
path+mtime+size+terms-hash index across runs, so an unchanged file or zip
is not re-read on the next scan. This exists because old packs are
effectively immutable (measured 2026-09-13: 52 seconds to rescan 1,560
directories, 6,321 files and 205 zip archives every close, none of it
having changed since the last run), and close_ceremony_check.py is the
one caller meant to pass --cache; a bare `handover_pack_scan.py` with no
--cache still writes nothing and behaves exactly as it always has, so
every existing caller and test keeps its "read-only, nothing written"
guarantee unless it explicitly asks otherwise.

CACHE CORRECTNESS. Keyed by (relpath, mtime_ns, size) per file and per
zip; a cached entry is only reused when both match the file on disk
right now, so a changed, touched, or replaced file is always re-read,
never trusted stale. The terms list itself is hashed (sha256 of its raw
bytes) and stored once in the cache; if that hash ever differs from the
terms file's current hash, the ENTIRE cache is discarded and every file
is re-scanned fresh in that run, because a newly added term could match
content or a zip member a prior run correctly found clean under the OLD
list -- per-entry invalidation cannot know that without re-reading
everything anyway, so whole-cache invalidation is the only correct
answer, not merely the simplest one. The cache file itself is written
atomically (a temp file then os.replace) and is skipped by scan_root's
own walk so it is never scanned as if it were pack content. A cache file
that cannot be read, is corrupt, or is missing degrades to today's exact
behavior: a full fresh scan, cache treated as empty, nothing about
correctness depends on the cache existing.

Python 3, standard library only. No network.
"""
import argparse
import codecs
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import zipfile
import zlib

CACHE_BASENAME = ".handover-pack-scan-cache.json"

ROOT = os.path.expanduser("~/Documents/BrotherModeUp-handovers")
TERMS_FILE = os.path.expanduser("~/.brothersbe-private-names")
SHORT_TERM_MAX_LEN = 5
# Bytes read per step. The overlap carried between steps (the longest
# term plus one character) means a term split across two reads is found.
CHUNK_BYTES = 1 << 20
# A zip inside a zip is opened in memory; deeper than this is counted
# unreadable (NO-DATA), never skipped as clean.
MAX_ZIP_DEPTH = 4
ZIP_MAGIC = b"PK\x03\x04"
# Folded into the cache hash: a cache written under an older scan rule
# (which read less) must never answer for this one.
SCAN_RULE = "every-file-raw-bytes-media-long-only-2026-09-18"


def _is_media(head):
    """True when head (a stream's first bytes) opens an image or audio
    payload: PNG, JPEG, GIF, WebP, MP3 (ID3 tag or frame sync), MP4/M4A."""
    return (head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"ID3",
                             b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
            or head[4:8] == b"ftyp")

EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_NO_DATA = 2


SCOPE_DIRECTIVE = re.compile(r"^#\s*scope\s*:\s*([a-z-]+)\s*$", re.IGNORECASE)
SCOPE_BROTHER_TREE = "brother-tree"


def parse_terms(lines):
    """(terms, scoped_out). terms are the entries this PACK scanner enforces;
    scoped_out is the count of entries skipped because they sat inside a
    `# scope: brother-tree` block (ended by `# scope: all`). Any other
    `#` line is a comment. The count is the only thing about a skipped
    term that ever leaves this function."""
    terms, scoped_out = [], 0
    scope = "all"
    for ln in lines:
        line = ln.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = SCOPE_DIRECTIVE.match(line)
            if m:
                scope = m.group(1).lower()
            continue
        if scope == SCOPE_BROTHER_TREE:
            scoped_out += 1
            continue
        terms.append(line)
    return terms, scoped_out


def _read_terms(path):
    """(terms, scoped_out, reason). terms is None (never []) on any failure,
    so a caller cannot mistake "could not read the list" for "the list is
    empty, therefore clean": an empty list would make every scan pass. A
    list whose every entry is scoped out is the same NO-DATA: nothing for
    this scanner to enforce is not evidence that a pack is clean."""
    if not os.path.isfile(path):
        return None, 0, "terms file not found: %s" % path
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return None, 0, "terms file unreadable: %s (%s)" % (path, exc)
    terms, scoped_out = parse_terms(lines)
    if not terms:
        return None, scoped_out, "terms file has no usable entries: %s" % path
    return terms, scoped_out, None


def load_terms(path):
    """Returns (terms, reason); see _read_terms. Kept as the two-tuple that
    cutover_pack.py and the tests call."""
    terms, _scoped_out, reason = _read_terms(path)
    return terms, reason


def build_patterns(terms):
    """(short_patterns, long_patterns), each a list of (term, compiled). A
    term of SHORT_TERM_MAX_LEN characters or fewer matches only as a whole
    word (bounded by anything but a letter or digit, so an underscore IS a
    boundary, or the string edge); a longer
    term matches as a plain substring. Both arms are case insensitive."""
    short_patterns, long_patterns = [], []
    for term in terms:
        escaped = re.escape(term)
        if len(term) <= SHORT_TERM_MAX_LEN:
            # Word class widened to accented Latin letters (U+00C0-U+024F) so
            # an accented letter does not wrongly read as a boundary (e.g.
            # "abc" inside "éabc"). Deliberately NOT the full Unicode \w:
            # that also matches CJK ideographs, which would stop a short
            # private term (an all-capitals client code) from being caught when written
            # directly against Japanese text with no space, loosening a gate
            # that must only ever get stricter. The underscore is NOT in the
            # class: a client code written as an identifier prefix
            # (CODE_APP_DEV, CODE_master.csv) must be a hit (2026-09-18; the
            # sibling bm_private_scan.py fixed the same bound as E37).
            pat = re.compile(r"(?<![A-Za-z0-9À-ɏ])" + escaped +
                              r"(?![A-Za-z0-9À-ɏ])",
                              re.IGNORECASE)
            short_patterns.append((term, pat))
        else:
            long_patterns.append((term, re.compile(escaped, re.IGNORECASE)))
    return short_patterns, long_patterns


def first_match_len(text, short_patterns, long_patterns):
    """The character length of the first loaded term whose pattern fires in
    text, or None. Only the length is ever handed back to a caller that
    might print it."""
    for term, pat in short_patterns:
        if pat.search(text):
            return len(term)
    for term, pat in long_patterns:
        if pat.search(text):
            return len(term)
    return None


def mask_path(path, short_patterns, long_patterns):
    """path with every occurrence of every loaded term replaced by <N> (N =
    that term's character count). Applied to EVERY path this tool prints,
    not only the ones a name-hit fired on: a directory two levels up can
    carry a term even when the hit itself is a content match below it."""
    for _term, pat in short_patterns:
        path = pat.sub(lambda m: "<%d>" % len(m.group(0)), path)
    for _term, pat in long_patterns:
        path = pat.sub(lambda m: "<%d>" % len(m.group(0)), path)
    return path


def _first_hit(text, short_patterns, long_patterns, final):
    """first_match_len, except that when more bytes are still to come
    (final False) a match touching the END of text is not trusted yet: a
    short term's "no word character follows" check would read the end of
    this read as the end of the word. That match sits inside the overlap
    carried into the next read and is judged there, with its real next
    character in view."""
    for term, pat in short_patterns + long_patterns:
        m = pat.search(text)
        if m and (final or m.end() < len(text)):
            return len(term)
    return None


def _scan_stream(fh, short_patterns, long_patterns, head=b""):
    """The character length of the first term found in the bytes of fh
    (after head, bytes the caller already read from it), or None. Every
    file and every zip member goes through here, whatever its extension.
    Reads CHUNK_BYTES at a time, so memory never grows with the file.
    Read errors propagate: the caller records the path as unreadable.
    A media payload (_is_media) is checked for long terms only."""
    chunk = head or fh.read(CHUNK_BYTES)
    if _is_media(chunk):
        short_patterns = []
    overlap = max((len(t) for t, _p in short_patterns + long_patterns), default=0) + 1
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    tail = ""
    while chunk:
        text = tail + decoder.decode(chunk)
        n = _first_hit(text, short_patterns, long_patterns, final=False)
        if n:
            return n
        tail = text[-overlap:]
        chunk = fh.read(CHUNK_BYTES)
    return _first_hit(tail + decoder.decode(b"", final=True),
                      short_patterns, long_patterns, final=True)


# Everything zipfile and zlib raise on a member it cannot give back: a
# missing entry, encryption, an unsupported method, a bad CRC, a truncated
# or corrupt deflate stream.
_MEMBER_ERRORS = (KeyError, RuntimeError, NotImplementedError, EOFError,
                  zipfile.BadZipFile, zlib.error, OSError)


def _scan_zip(source, relpath, short_patterns, long_patterns, depth=0):
    """(hits, member_count, unreadable). source is a path or a seekable
    file object. hits is a list of ("zip-member", path, n). unreadable is a
    list of relpaths (the zip itself, or one of its members) this tool
    could not open, read or decode: that is a DIFFERENT outcome from
    reading something and finding it clean, and SBE law L11
    (silent-failure-lints) is exactly the rule that a swallowed record here
    must not disappear with no trace. Never extracts to disk. A member that
    is itself a zip is opened the same way, its members named
    outer::inner::member."""
    hits = []
    member_count = 0
    unreadable = []
    try:
        zf = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError):
        unreadable.append(relpath)
        return hits, member_count, unreadable
    try:
        for info in zf.infolist():
            if info.filename.endswith("/"):
                continue  # a directory entry inside the zip, no content of its own
            member_count += 1
            member_path = "%s::%s" % (relpath, info.filename)
            n = first_match_len(info.filename, short_patterns, long_patterns)
            if n:
                hits.append(("zip-member", member_path, n))
            try:
                with zf.open(info) as fh:
                    head = fh.read(CHUNK_BYTES)
                    nested = (head.startswith(ZIP_MAGIC)
                              or info.filename.lower().endswith(".zip"))
                    if not nested:
                        n = _scan_stream(fh, short_patterns, long_patterns, head)
                    elif depth + 1 < MAX_ZIP_DEPTH:
                        # ponytail: a nested zip is held in memory whole
                        # (largest member in the root 2026-09-18: 25 MiB);
                        # spool to a temp file if one ever outgrows memory.
                        inner = io.BytesIO(head + fh.read())
                    else:
                        unreadable.append(member_path)
                        continue
            except _MEMBER_ERRORS:
                unreadable.append(member_path)
                continue
            if nested:
                sub_hits, sub_count, sub_unreadable = _scan_zip(
                    inner, member_path, short_patterns, long_patterns, depth + 1)
                hits.extend(sub_hits)
                member_count += sub_count
                unreadable.extend(sub_unreadable)
            elif n:
                hits.append(("zip-member", member_path, n))
    finally:
        zf.close()
    return hits, member_count, unreadable


def _terms_hash(terms):
    """sha256 of SCAN_RULE plus the loaded, already-scope-filtered term
    list, joined by a byte no term can itself contain (newline), so the
    hash changes exactly when the enforced set changes -- adding, removing
    or editing a term, or a term moving in/out of a brother-tree scope
    block -- or when the scan rule itself changes."""
    return hashlib.sha256("\n".join([SCAN_RULE] + list(terms)).encode("utf-8")).hexdigest()


def _load_cache(cache_path, terms_hash):
    """The cache dict if it exists, is readable, valid JSON, and was built
    under the SAME terms hash; otherwise an empty cache. Any failure here
    (missing file, corrupt JSON, hash mismatch) degrades to "no cache" --
    the caller re-scans everything fresh, identical to today's behavior,
    never a wrong-but-fast answer."""
    empty = {"terms_hash": terms_hash, "files": {}, "zips": {}}
    if not cache_path or not os.path.isfile(cache_path):
        return empty
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or data.get("terms_hash") != terms_hash:
        return empty
    if not isinstance(data.get("files"), dict) or not isinstance(data.get("zips"), dict):
        return empty
    return data


def _save_cache(cache_path, cache):
    """Atomic write: a reader (this scanner running concurrently, or a
    person opening the file) never observes a half-written cache, and a
    crash mid-write leaves the OLD cache file in place, never a corrupt
    one. Best-effort: a write failure (permissions, disk full, another
    process holding a lock) is swallowed here on purpose -- the cache is
    an optimization, and a scan must never fail or report wrong because
    it could not save one."""
    if not cache_path:
        return
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(cache_path) or ".", prefix=".tmp-pack-scan-cache-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
            os.replace(tmp_path, cache_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        pass


def scan_root(root, short_patterns, long_patterns, cache=None):
    """(hits, stats). hits is a list of (kind, relpath, n) with relpath
    relative to root and kind one of "name", "content", "zip-member". Never
    writes anything; every read is a plain open() or zipfile's read().
    stats["unreadable"] is a list of relpaths (a file, a whole zip, or one
    zip member) this tool could not open or decode: a path it never actually
    read must never be counted the same as one it read and found clean."""
    hits = []
    unreadable = []
    stats = {"dirs": 0, "files": 0, "zips": 0, "zip_members": 0}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)

        for name in dirnames:
            stats["dirs"] += 1
            relpath = name if rel_dir == "." else os.path.join(rel_dir, name)
            n = first_match_len(name, short_patterns, long_patterns)
            if n:
                hits.append(("name", relpath, n))

        for name in filenames:
            if name == CACHE_BASENAME:
                continue  # this scanner's own cache file, never pack content
            stats["files"] += 1
            relpath = name if rel_dir == "." else os.path.join(rel_dir, name)
            full = os.path.join(dirpath, name)

            n = first_match_len(name, short_patterns, long_patterns)
            if n:
                hits.append(("name", relpath, n))

            try:
                st = os.stat(full)
                stat_key = [st.st_mtime_ns, st.st_size]
            except OSError:
                stat_key = None

            if not os.path.isfile(full):
                # A FIFO would block the read forever and a device (or a
                # symlink to one) never ends; a broken symlink has nothing to
                # read. Never opened, never clean: counted unreadable.
                unreadable.append(relpath)
                continue
            is_zip = name.lower().endswith(".zip") or zipfile.is_zipfile(full)
            if not is_zip:
                cached = cache["files"].get(relpath) if cache is not None else None
                if cached is not None and stat_key is not None and cached.get("stat") == stat_key:
                    if cached.get("unreadable"):
                        unreadable.append(relpath)
                    elif cached.get("hit_len"):
                        hits.append(("content", relpath, cached["hit_len"]))
                else:
                    try:
                        with open(full, "rb") as fh:
                            n = _scan_stream(fh, short_patterns, long_patterns)
                    except OSError:
                        n = None
                        unreadable.append(relpath)
                        entry = {"unreadable": True}
                    else:
                        if n:
                            hits.append(("content", relpath, n))
                        entry = {"hit_len": n}
                    if cache is not None and stat_key is not None:
                        entry["stat"] = stat_key
                        cache["files"][relpath] = entry

            if is_zip:
                stats["zips"] += 1
                cached = cache["zips"].get(relpath) if cache is not None else None
                if cached is not None and stat_key is not None and cached.get("stat") == stat_key:
                    for kind, member_path, hit_n in cached.get("hits", []):
                        hits.append((kind, member_path, hit_n))
                    stats["zip_members"] += cached.get("member_count", 0)
                    unreadable.extend(cached.get("unreadable_members", []))
                else:
                    zip_hits, member_count, zip_unreadable = _scan_zip(
                        full, relpath, short_patterns, long_patterns)
                    hits.extend(zip_hits)
                    stats["zip_members"] += member_count
                    unreadable.extend(zip_unreadable)
                    if cache is not None and stat_key is not None:
                        cache["zips"][relpath] = {
                            "stat": stat_key,
                            "hits": [list(h) for h in zip_hits],
                            "member_count": member_count,
                            "unreadable_members": zip_unreadable,
                        }

    stats["unreadable"] = unreadable
    return hits, stats


def run_scan(root, terms_path=None, cache_path=None):
    """One call doing the whole job: load the list, build the patterns, walk
    root. Returns (hits, short_patterns, long_patterns, stats, no_data_reason).
    hits is None (never []) and no_data_reason is set when the terms list
    could not be loaded, so a caller (this module's own CLI, or
    close_ceremony_check.py importing this function) never mistakes "the
    list was unreadable" for "nothing was found".

    cache_path is opt-in (default None: no cache, nothing written, exactly
    today's behavior). Passing a path reuses unchanged files' and zips'
    prior results across runs; see the module docstring for the exact
    correctness rule (whole-cache invalidation on any terms-list change)."""
    terms, scoped_out, reason = _read_terms(terms_path or TERMS_FILE)
    if terms is None:
        return None, [], [], {}, reason
    short_patterns, long_patterns = build_patterns(terms)
    cache = _load_cache(cache_path, _terms_hash(terms)) if cache_path else None
    hits, stats = scan_root(root, short_patterns, long_patterns, cache=cache)
    stats["scoped_out"] = scoped_out
    stats["cache_used"] = cache_path is not None
    if cache_path:
        _save_cache(cache_path, cache)
    return hits, short_patterns, long_patterns, stats, None


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=ROOT,
                    help="handover packs directory (default: %s)" % ROOT)
    p.add_argument("--terms", default=TERMS_FILE,
                    help="private terms list (default: %s)" % TERMS_FILE)
    p.add_argument("--cache", default=None, metavar="PATH",
                    help="opt in to a persistent path+mtime+size+terms-hash cache at PATH "
                         "(default: no cache, nothing written, unchanged from before this "
                         "flag existed); an unchanged file or zip since the last run at this "
                         "path is not re-read")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not os.path.isdir(args.root):
        print("NO-DATA: handover root not found: %s" % args.root)
        return EXIT_NO_DATA

    hits, short_patterns, long_patterns, stats, no_data_reason = run_scan(
        args.root, args.terms, cache_path=args.cache)
    if hits is None:
        print("NO-DATA: %s" % no_data_reason)
        return EXIT_NO_DATA

    by_kind = {"name": 0, "content": 0, "zip-member": 0}
    for kind, path, n in hits:
        masked = mask_path(path, short_patterns, long_patterns)
        print("%s %s (a term of %d characters)" % (kind, masked, n))
        by_kind[kind] += 1

    # A file, a whole zip, or a zip member this tool could not open or decode
    # is a DIFFERENT outcome from reading it and finding it clean (SBE law
    # L11): its path is named (masked the same way any other printed path
    # is, since the path itself can carry a term) and never its content,
    # because there IS no content this tool was able to read.
    unreadable = stats.get("unreadable", [])
    for path in unreadable:
        masked = mask_path(path, short_patterns, long_patterns)
        print("unreadable %s (could not be opened or decoded)" % masked)

    print("SCAN SUMMARY: root=%s dirs=%d files=%d zips=%d zip-members=%d "
          "hits=%d (name=%d content=%d zip-member=%d) unreadable=%d "
          "terms-scoped-out=%d cache=%s"
          % (args.root, stats["dirs"], stats["files"], stats["zips"],
             stats["zip_members"], len(hits), by_kind["name"], by_kind["content"],
             by_kind["zip-member"], len(unreadable), stats.get("scoped_out", 0),
             args.cache if stats.get("cache_used") else "off"))

    if hits:
        return EXIT_FOUND
    if unreadable:
        # A scan that could not open every file cannot call the tree clean:
        # an unreadable path might have carried a term this tool never got
        # the chance to see. NO-DATA only when unreadable is the ONLY thing
        # found; an actual hit above still takes priority.
        print("NO-DATA: %d path(s) could not be opened or decoded, so this "
              "tree cannot be called clean" % len(unreadable))
        return EXIT_NO_DATA
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
