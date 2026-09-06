#!/usr/bin/env python3
"""bm_vault_cite: a citation that binds to the content it cited, not just a note's name.

WHY THIS EXISTS. WBS row VB6-02, the codex-debated defect: a WBS row (or another vault
note) cites a vault note in prose -- "see n-<hex>" or "per docs/plan/X.md" -- and that
citation carries no referential integrity. The cited note is free to be edited or
superseded afterward, and the citation keeps pointing at the same id or path while
silently meaning different content. Nothing today notices.

MEASURED FIRST, per the brief. `grep -rniE "cite|citation"` over tools/*.py and every
*.md in this repository found ONLY prose usage of the word ("this row cites...",
"citations:" as a section heading) -- never a `cites:` frontmatter field, a citation
record shape, or any citation-checking tool. There is nothing existing to reuse or
collide with. The closest existing precedent, and the one this module's `check`
deliberately mirrors, is tools/bm_vault_ledger.py's `replay`: it also records a
content_sha256 at one point in time and later recomputes it to report MATCHES /
CHANGED SINCE / GONE. This module is the same idea applied to citations instead of
served recall hits.

THE RECORD, one JSON object per line in a caller-named file (never vault frontmatter --
the brief allows either shape, and a flat file needs no note to be writable to record a
citation against it, which matches every other tool in this family reading the vault
without ever editing it):

    {"note_id": "n-<16 hex>", "content_sha256": "<hex>", "lifecycle": "<state>", "by": "<key>"}

  note_id       the cited note's stable id, exactly the D05 format bm_vault_ids.py mints
                (n- followed by 16 lowercase hex digits).
  content_sha256  sha256 of the note file's full text, at the moment the citation was minted.
  lifecycle     the note's lifecycle state at that moment: one of bm_vault_lifecycle.py's
                STATES (candidate/validated/canonical/rejected), "legacy" when the note
                declares no `promotion:` field, or "unknown" when it declares a value the
                lifecycle contract does not recognise. Read the same way
                bm_vault_lifecycle.py's read_promotion does; duplicated rather than
                imported, matching bm_vault_provenance.py's own stated reason: every
                sibling contract module in this family reads the vault on its own, so no
                module's behaviour shifts when a sibling changes.
  by            the citing key: a WBS row id or a note id. Free text, not validated --
                this module has no registry of WBS row ids to check it against.

  mint --vault V --note ID --by KEY [--out FILE]
      Emits one citation record for the note exactly as it stands right now. Reads the
      note to hash it and to read its lifecycle; never writes to it. With --out, appends
      one line to FILE (creating it if absent); without it, prints the line to stdout.
      Refuses, rather than picking a first match, when ID resolves to more than one note:
      AMBIGUOUS-ID naming every path holding the id, exit 1.

  check --vault V --citations FILE
      Recomputes each record's hash against the note as it stands now.
        unchanged      counted, printed nowhere (the brief: "Unchanged: silent (counted)")
        SUPERSEDED-CONTENT  note id, old/new hash prefix (12 hex chars, matching
                       bm_vault_ledger.py's own display prefix), lifecycle then and now
        MISSING        the note_id resolves to no note, AND every file the walk visited
                       was readable -- a genuinely absent id, not a scan gap
        AMBIGUOUS-ID   the note_id resolves to two or more notes; every holding path is
                       named; never a silent pick of the first match
        UNREADABLE-SCAN  the note_id resolved to no note, but at least one file could not
                       be opened during the walk (e.g. permission denied), so the id's
                       true location may be hiding behind that unreadable file -- never
                       reported as MISSING. Message names the count of unreadable files.
        MALFORMED-RECORD  the citation record itself carries no note_id
      Counts are always printed, on the last line, regardless of verdict.

Exit codes: mint 0 on success, 1 AMBIGUOUS-ID (--note resolves to two or more notes),
2 NO-DATA (unreadable vault, a --note that is not a valid n-<16 hex> id, no such note in
the vault, or no note found with at least one file unreadable during the scan). check 0
when every citation is current, 1 when any is SUPERSEDED-CONTENT, MISSING, AMBIGUOUS-ID,
or MALFORMED-RECORD, 2 NO-DATA (unreadable vault, unreadable or absent --citations file,
a --citations file holding zero readable records, or any record reporting
UNREADABLE-SCAN for that run -- zero citations checked is not the same claim as zero
found stale, and a scan gap is never claimed as a clean miss).

Python 3.9, standard library only. Read-only over the vault: every open() here reads:
mint and check never write into the vault, only (optionally) to the caller-named
citations file, which lives outside it.
"""
import argparse
import hashlib
import json
import os
import re
import sys

SKIP_DIRS = {".git", ".trash", ".obsidian"}
ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.M)
ID_VALUE_RE = re.compile(r"^n-[0-9a-f]{16}$")
PROMOTION_RE = re.compile(r"^promotion:\s*(.+?)\s*$", re.M)
STATES = ("candidate", "validated", "canonical", "rejected")


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _resolve(vault, note_id):
    """(matches, unreadable_count). matches is every path whose frontmatter declares
    id: note_id, in walk order -- exactly one resolves the id, two or more is
    AMBIGUOUS-ID and the caller refuses rather than silently picking the first.
    unreadable_count is how many files this same walk could not open (e.g. permission
    denied); zero matches together with a nonzero count means the id's true location may
    be hiding behind a file that could not be scanned, which the caller reports as
    UNREADABLE-SCAN rather than MISSING. Duplicated from bm_vault_ids.py's own walk
    rather than imported, per this family's own stated convention (see
    bm_vault_provenance.py's docstring)."""
    matches = []
    unreadable = 0
    for path in _walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            unreadable += 1
            continue
        m = ID_RE.search(_frontmatter(text))
        if m and m.group(1).strip().strip('"').strip("'") == note_id:
            matches.append(path)
    return matches, unreadable


def _lifecycle(text):
    """One of STATES, "legacy" (no promotion: field declared) or "unknown" (a value the
    contract does not recognise). Mirrors bm_vault_lifecycle.py's read_promotion."""
    m = PROMOTION_RE.search(_frontmatter(text))
    if not m:
        return "legacy"
    value = m.group(1).strip().strip('"').strip("'")
    return value if value in STATES else "unknown"


def _hash_and_lifecycle(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), _lifecycle(text)


def cmd_mint(args):
    if not args.vault or not os.path.isdir(args.vault):
        sys.stderr.write("bm_vault_cite: NO-DATA, no readable vault at %r\n" % args.vault)
        return 2
    if not ID_VALUE_RE.match(args.note or ""):
        sys.stderr.write(
            "bm_vault_cite: NO-DATA, %r is not a note id (n-<16 hex>)\n" % args.note)
        return 2
    paths, unreadable = _resolve(args.vault, args.note)
    if len(paths) > 1:
        sys.stderr.write(
            "bm_vault_cite: AMBIGUOUS-ID, %s resolves to %d notes: %s\n"
            % (args.note, len(paths), ", ".join(paths)))
        return 1
    if not paths:
        if unreadable:
            sys.stderr.write(
                "bm_vault_cite: NO-DATA, no note %s found and %d file(s) unreadable "
                "during the scan\n" % (args.note, unreadable))
        else:
            sys.stderr.write(
                "bm_vault_cite: NO-DATA, no note %s in %s\n" % (args.note, args.vault))
        return 2
    content_hash, lifecycle = _hash_and_lifecycle(paths[0])
    record = {"note_id": args.note, "content_sha256": content_hash,
              "lifecycle": lifecycle, "by": args.by}
    line = json.dumps(record, sort_keys=True)
    if args.out:
        with open(args.out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print("minted %s -> %s" % (args.note, args.out))
    else:
        print(line)
    return 0


def _read_citations(path):
    """Records in file order, or None when the file is absent, unreadable, or holds zero
    readable JSON lines. A malformed line is skipped with a stderr warning rather than
    aborting the whole check, matching bm_vault_ledger.py's own ledger treatment."""
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
                except ValueError as e:
                    sys.stderr.write(
                        "bm_vault_cite: skipping malformed line %d (%s)\n" % (i, e))
    except OSError:  # sbe: allow-silent unreadable file, per docstring, caller turns None into a named NO-DATA
        return None
    return records or None


def cmd_check(args):
    if not args.vault or not os.path.isdir(args.vault):
        sys.stderr.write("bm_vault_cite: NO-DATA, no readable vault at %r\n" % args.vault)
        return 2
    records = _read_citations(args.citations)
    if records is None:
        sys.stderr.write(
            "bm_vault_cite: NO-DATA, no readable citation record at %r\n" % args.citations)
        return 2
    current = superseded = missing = ambiguous = unreadable_scan = malformed = 0
    for rec in records:
        note_id, by = rec.get("note_id"), rec.get("by")
        if not note_id:
            print("MALFORMED-RECORD cited by %s: record carries no note_id" % by)
            malformed += 1
            continue
        old_hash, old_life = rec.get("content_sha256"), rec.get("lifecycle")
        paths, unreadable = _resolve(args.vault, note_id)
        if len(paths) > 1:
            print("AMBIGUOUS-ID %s cited by %s: %d notes hold this id: %s"
                  % (note_id, by, len(paths), ", ".join(paths)))
            ambiguous += 1
            continue
        if not paths:
            if unreadable:
                print("UNREADABLE-SCAN %s cited by %s: %d file(s) unreadable during "
                      "the scan" % (note_id, by, unreadable))
                unreadable_scan += 1
            else:
                print("MISSING %s cited by %s: no such note in the vault" % (note_id, by))
                missing += 1
            continue
        new_hash, new_life = _hash_and_lifecycle(paths[0])
        if new_hash == old_hash:
            current += 1
            continue
        superseded += 1
        print("SUPERSEDED-CONTENT %s cited by %s: hash %s -> %s, lifecycle %s -> %s"
              % (note_id, by, (old_hash or "")[:12], new_hash[:12], old_life, new_life))
    print("citations: %d  current: %d  superseded: %d  missing: %d  ambiguous: %d  "
          "unreadable-scan: %d  malformed: %d"
          % (len(records), current, superseded, missing, ambiguous, unreadable_scan,
             malformed))
    if unreadable_scan:
        return 2
    return 1 if (superseded or missing or ambiguous or malformed) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bm_vault_cite", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command")
    p_mint = sub.add_parser("mint")
    p_mint.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_mint.add_argument("--note", required=True)
    p_mint.add_argument("--by", required=True)
    p_mint.add_argument("--out")
    p_check = sub.add_parser("check")
    p_check.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_check.add_argument("--citations", required=True)
    args = ap.parse_args(argv)
    if args.command == "mint":
        return cmd_mint(args)
    if args.command == "check":
        return cmd_check(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
