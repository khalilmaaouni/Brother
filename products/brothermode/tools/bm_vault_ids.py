#!/usr/bin/env python3
"""Stable note identity: an id that survives a rename, a move and a retitle.

WHY THIS EXISTS. Measured 2026-08-29 across 802 notes: zero carried an id. A
note's identity WAS its filename, so every inbound wikilink, every catalog entry,
every piece of evidence and every typed edge pointed at a string that any rename
silently invalidated. That is benchmark row D05, and it is the critical path:
the crosswalk (D06), fact provenance (D07), bi-temporal facts (D09) and the
typed ontology (D14) all need something durable to hang off, and a filename is
not it.

WHAT THIS IS NOT. It does not rewrite the vault; that is a separate step,
deliberately, because a whole-corpus frontmatter rewrite must not run while
another session holds files in that tree. This module is the tooling: mint,
read, insert, index, resolve. The migration uses it and is gated separately.

THE ID FORMAT, and why it carries no meaning. 16 hex characters from uuid4,
prefixed `n-`. Deliberately opaque:

  - It encodes no date, no title and no path, so nothing about it can go stale
    and nobody can be tempted to parse it. An id that means something is an id
    somebody will eventually derive rather than look up.
  - 64 bits over a corpus of this size makes accidental collision negligible,
    and mint() still checks against the live set rather than trusting that,
    because "negligible" is a probability and a duplicate id is a silent
    wrong-note lookup.

RESOLUTION NEVER GUESSES BY DEFAULT. resolve() takes allow_stem and it defaults
to False. Matching a filename when the id is unknown would return a correct
answer for every note in a vault where NO id exists at all, so this capability
would measure as present while nothing had been built. That is the exact shape
of the three false passes this estate's own benchmark produced on 2026-08-29,
and the guard exists so it cannot happen a fourth time. A caller that genuinely
wants filename matching asks for it explicitly in the call.

Exit 0 the command ran clean. Exit 1 a check found something. Exit 2 NO-DATA,
the vault could not be read. Python 3.9 floor, standard library only.
"""
import argparse
import os
import re
import sys
import uuid

ID_PREFIX = "n-"
ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.M)
ID_VALUE_RE = re.compile(r"^n-[0-9a-f]{16}$")
SKIP_DIRS = {".git", ".trash", ".obsidian"}


def mint(existing=()):
    """A fresh id, checked against the ids already in use.

    The check is not ceremony over a 64-bit space: a duplicate id resolves the
    wrong note silently, which is the one failure this module must not be able
    to produce. Cheap insurance against a bad random source or a copied file.
    """
    taken = set(existing or ())
    for _ in range(1000):
        candidate = ID_PREFIX + uuid.uuid4().hex[:16]
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not mint an unused id in 1000 attempts; "
                       "the id space or the random source is wrong")


def frontmatter(text):
    """(block, start, end) for the YAML frontmatter, or (None, -1, -1).

    Returns offsets rather than just the text so a caller can splice a field in
    without reserialising the document. A note whose frontmatter is rewritten
    wholesale comes back with its key order and comments destroyed, and the diff
    then hides whatever else changed inside it.
    """
    if not text.startswith("---"):
        return None, -1, -1
    end = text.find("\n---", 3)
    if end == -1:
        return None, -1, -1
    return text[3:end], 3, end


def read_id(text):
    """The declared id, or None. A malformed value reads as None rather than
    raising: one corrupt note must not stop an index of eight hundred others."""
    block, _, _ = frontmatter(text)
    if block is None:
        return None
    m = ID_RE.search(block)
    if not m:
        return None
    value = m.group(1).strip().strip('"').strip("'")
    return value if ID_VALUE_RE.match(value) else None


def add_id(text, note_id):
    """Insert `id:` as the first frontmatter field, leaving everything else byte
    identical. Returns the original text unchanged when an id already exists.

    First field on purpose: it is the note's identity and belongs where a reader
    looks first. Idempotent, so a half-finished migration can be re-run safely.
    """
    if read_id(text) is not None:
        return text
    block, start, end = frontmatter(text)
    if block is None:
        return text
    return text[:start] + "\nid: " + note_id + text[start:end] + text[end:]


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def index(vault):
    """Build the id map. Returns (by_id, missing, duplicates).

    Duplicates are RETURNED, never resolved. Two notes claiming one id is real
    corruption, and picking a winner would hide it behind a lookup that appears
    to work.
    """
    by_id, missing, duplicates = {}, [], {}
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent an unreadable note has no id to place, same skip-not-crash stance every sibling vault module uses
            continue
        rel = os.path.relpath(path, vault)
        nid = read_id(text)
        if nid is None:
            missing.append(rel)
        elif nid in by_id:
            duplicates.setdefault(nid, [by_id[nid]]).append(rel)
        else:
            by_id[nid] = rel
    return by_id, missing, duplicates


def resolve(vault, ident, allow_stem=False):
    """A note path from an id, or None. Name matching only when asked."""
    by_id, _, _ = index(vault)
    if ident in by_id:
        return by_id[ident]
    if not allow_stem:
        return None
    for path in walk(vault):
        if os.path.splitext(os.path.basename(path))[0] == ident:
            return os.path.relpath(path, vault)
    return None


def cmd_check(vault):
    by_id, missing, duplicates = index(vault)
    total = len(by_id) + len(missing)
    print("vault: %s" % vault)
    print("notes: %d" % total)
    print("with a stable id: %d" % len(by_id))
    print("missing an id: %d" % len(missing))
    if duplicates:
        print("DUPLICATE ids: %d" % len(duplicates))
        for nid, paths in sorted(duplicates.items()):
            print("  %s claimed by %s" % (nid, ", ".join(paths)))
    for rel in missing[:10]:
        print("  no id: %s" % rel)
    if len(missing) > 10:
        print("  ... and %d more" % (len(missing) - 10))
    return 1 if (missing or duplicates) else 0


def cmd_assign(vault, apply_changes):
    """Mint an id for every note lacking one. Dry unless --apply, because this
    edits every file in a corpus and this estate has already lost work to a
    whole-tree rewrite landing on top of another writer."""
    by_id, missing, _ = index(vault)
    taken = set(by_id)
    written = 0
    for rel in missing:
        path = os.path.join(vault, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print("SKIP %s: %s" % (rel, exc))
            continue
        if frontmatter(text)[0] is None:
            print("SKIP %s: no frontmatter block to add an id to" % rel)
            continue
        nid = mint(taken)
        taken.add(nid)
        if apply_changes:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(add_id(text, nid))
            except OSError as exc:
                print("FAILED %s: %s" % (rel, exc))
                continue
        written += 1
    print("%s %d note(s)" % ("assigned" if apply_changes else "would assign", written))
    if not apply_changes:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check", "assign", "resolve"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--id", help="for resolve")
    ap.add_argument("--allow-stem", action="store_true",
                    help="for resolve: permit filename matching, off by default")
    ap.add_argument("--apply", action="store_true", help="for assign: actually write")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_ids: NO-DATA, no readable vault at %r" % args.vault, file=sys.stderr)
        return 2
    if args.command == "check":
        return cmd_check(args.vault)
    if args.command == "assign":
        return cmd_assign(args.vault, args.apply)
    if not args.id:
        print("bm_vault_ids: resolve needs --id", file=sys.stderr)
        return 2
    hit = resolve(args.vault, args.id, allow_stem=args.allow_stem)
    if hit is None:
        print("NO-DATA: %r resolves to no note%s"
              % (args.id, "" if args.allow_stem else " (filename matching is off)"))
        return 1
    print(hit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
