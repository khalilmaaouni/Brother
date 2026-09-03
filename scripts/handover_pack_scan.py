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
  * the CONTENT of every file whose extension is one of .md .txt .json
    .html .csv .yml .yaml .py .sh .jsonl;
  * every ZIP archive's member NAMES, and the CONTENT of every member whose
    own name carries one of those same extensions. Members are read with
    zipfile's in-memory read(), never extracted to disk.

THE RULE, same one this estate's other scanners use (bm_private_scan.py):
the term's LENGTH decides its strictness, never its stored spelling. A term
of five characters or fewer matches as a WHOLE WORD (bounded by a
non-word character or the string edge), case insensitively. A term over
five characters matches as a plain substring, case insensitively.

The term list itself lives OUTSIDE every repository, at
~/.brothersbe-private-names by default (one term per line, # comments), and
is never printed. NEVER print a term anywhere: every hit line and every
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

Python 3, standard library only. No network. Nothing written to disk.
"""
import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.expanduser("~/Documents/BrotherModeUp-handovers")
TERMS_FILE = os.path.expanduser("~/.brothersbe-private-names")
SHORT_TERM_MAX_LEN = 5
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".html", ".csv", ".yml", ".yaml",
                    ".py", ".sh", ".jsonl"}

EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_NO_DATA = 2


def load_terms(path):
    """Returns (terms, reason). terms is None (never []) on any failure, so
    a caller cannot mistake "could not read the list" for "the list is
    empty, therefore clean": an empty list would make every scan pass."""
    if not os.path.isfile(path):
        return None, "terms file not found: %s" % path
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return None, "terms file unreadable: %s (%s)" % (path, exc)
    terms = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not terms:
        return None, "terms file has no usable entries: %s" % path
    return terms, None


def build_patterns(terms):
    """(short_patterns, long_patterns), each a list of (term, compiled). A
    term of SHORT_TERM_MAX_LEN characters or fewer matches only as a whole
    word (bounded by a non-word character or the string edge); a longer
    term matches as a plain substring. Both arms are case insensitive."""
    short_patterns, long_patterns = [], []
    for term in terms:
        escaped = re.escape(term)
        if len(term) <= SHORT_TERM_MAX_LEN:
            pat = re.compile(r"(?<![A-Za-z0-9_])" + escaped + r"(?![A-Za-z0-9_])",
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


def _read_text(path):
    """(text, reason). reason is None on success (text holds the file's
    content, possibly empty). On failure, text is None and reason is a short
    string the caller must record: a permission error or a binary file
    wearing a text extension must be counted as UNREADABLE, never silently
    treated the same as a file this tool actually read and found clean
    (SBE law L11: the old except-then-return-None shape dropped this record
    with no trace). Never raises."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return (None, "could not be opened or read")
    return (text, None)


def _scan_zip(full_path, relpath, short_patterns, long_patterns):
    """(hits, member_count, unreadable). hits is a list of ("zip-member",
    path, n). unreadable is a list of relpaths (the zip itself, or one of its
    members) this tool could not open, read or decode: that is a DIFFERENT
    outcome from reading something and finding it clean, and SBE law L11
    (silent-failure-lints) is exactly the rule that a swallowed record here
    must not disappear with no trace. Never extracts to disk: member bytes
    come from ZipFile.read(), which decompresses in memory only."""
    hits = []
    member_count = 0
    unreadable = []
    try:
        zf = zipfile.ZipFile(full_path)
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
            ext = os.path.splitext(info.filename)[1].lower()
            if ext in TEXT_EXTENSIONS:
                try:
                    raw = zf.read(info)
                except (KeyError, RuntimeError, zipfile.BadZipFile, OSError):
                    unreadable.append(member_path)
                    continue
                n = first_match_len(raw.decode("utf-8", "replace"),
                                     short_patterns, long_patterns)
                if n:
                    hits.append(("zip-member", member_path, n))
    finally:
        zf.close()
    return hits, member_count, unreadable


def scan_root(root, short_patterns, long_patterns):
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
            stats["files"] += 1
            relpath = name if rel_dir == "." else os.path.join(rel_dir, name)
            full = os.path.join(dirpath, name)

            n = first_match_len(name, short_patterns, long_patterns)
            if n:
                hits.append(("name", relpath, n))

            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXTENSIONS:
                text, reason = _read_text(full)
                if reason is not None:
                    unreadable.append(relpath)
                else:
                    n = first_match_len(text, short_patterns, long_patterns)
                    if n:
                        hits.append(("content", relpath, n))

            if ext == ".zip":
                stats["zips"] += 1
                zip_hits, member_count, zip_unreadable = _scan_zip(
                    full, relpath, short_patterns, long_patterns)
                hits.extend(zip_hits)
                stats["zip_members"] += member_count
                unreadable.extend(zip_unreadable)

    stats["unreadable"] = unreadable
    return hits, stats


def run_scan(root, terms_path=None):
    """One call doing the whole job: load the list, build the patterns, walk
    root. Returns (hits, short_patterns, long_patterns, stats, no_data_reason).
    hits is None (never []) and no_data_reason is set when the terms list
    could not be loaded, so a caller (this module's own CLI, or
    close_ceremony_check.py importing this function) never mistakes "the
    list was unreadable" for "nothing was found"."""
    terms, reason = load_terms(terms_path or TERMS_FILE)
    if terms is None:
        return None, [], [], {}, reason
    short_patterns, long_patterns = build_patterns(terms)
    hits, stats = scan_root(root, short_patterns, long_patterns)
    return hits, short_patterns, long_patterns, stats, None


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=ROOT,
                    help="handover packs directory (default: %s)" % ROOT)
    p.add_argument("--terms", default=TERMS_FILE,
                    help="private terms list (default: %s)" % TERMS_FILE)
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not os.path.isdir(args.root):
        print("NO-DATA: handover root not found: %s" % args.root)
        return EXIT_NO_DATA

    hits, short_patterns, long_patterns, stats, no_data_reason = run_scan(
        args.root, args.terms)
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
          "hits=%d (name=%d content=%d zip-member=%d) unreadable=%d"
          % (args.root, stats["dirs"], stats["files"], stats["zips"],
             stats["zip_members"], len(hits), by_kind["name"], by_kind["content"],
             by_kind["zip-member"], len(unreadable)))

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
