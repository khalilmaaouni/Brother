#!/usr/bin/env python3
"""Records promotions the lifecycle contract (tools/bm_vault_lifecycle.py, D12)
already decided are legal, and reports what state a note is in.

NAMED promotionS, deliberately not promote: tools/bm_vault_promote.py already
exists and means something unrelated (the WBS 9 distillation nudge counter).
bm_vault_lifecycle.py's own docstring already flagged this exact trap ("two
tools whose names differ by a suffix is how the estate once ended up with two
writer locks in two formats"), so this module gets its own unambiguous name
rather than colliding with either sibling.

WHY THIS EXISTS. bm_vault_lifecycle.py is the contract: it reads a note's
promotion field, says whether a move is legal, and censuses a vault. It writes
nothing anywhere, by its own docstring. Nothing in the estate could yet RECORD
a promotion, so every note was stuck wherever its frontmatter already said.
This module is the write side: it imports the contract rather than
re-deciding legality, and its only new rule is the one the contract does not
cover, because "legacy" is not one of its states: a note with no promotion
field at all may become "candidate" (entering the machine for the first
time), and nothing else, for the same reason candidate cannot jump straight
to canonical. Skipping the first declaration is exactly the same shortcut the
contract already forbids one step later.

THREE COMMANDS.
  check     delegates straight to bm_vault_lifecycle.cmd_check: this module
            invents no second census, it reuses the one the contract already
            ships and already tests.
  state     the promotion state of ONE note, by id (bm_vault_ids) or by a
            vault-relative path, legacy included.
  promote   records a transition on ONE note's frontmatter. Refuses an
            illegal move (imported from the contract, never re-implemented
            here) and refuses to record validated/canonical/rejected without
            --by. DRY RUN unless --apply: this edits a file inside a corpus
            another session may be reading at the same time, same posture as
            bm_vault_ids.py's cmd_assign and for the same reason.

            V14: also refuses an approval where --by is the note's own
            `author:` (the contract's check_separation_of_duties, imported
            rather than re-decided here, same posture as legal_move above).
            A note with no `author:` field is refused too, fail closed: an
            unknown author is never a silent pass just because there is
            nothing to compare against.

Exit 0 clean. Exit 1 a legality or completeness finding (mirrors the
contract's own findings-are-never-silent posture). Exit 2 NO-DATA: no
readable vault, or the named note could not be resolved. Python 3.9,
standard library only, no network, no subprocess.

TWO KNOWN COSMETIC EDGES, named rather than fixed here because both belong
to shared code this module only calls. First: a CRLF note keeps the \r on
every line this module leaves untouched, while the promotion lines it
splices in are plain LF, so a promoted CRLF note ends up with mixed line
endings. Cosmetic, not a correctness bug (every reader on this estate treats
\r as insignificant whitespace), but worth knowing before diffing such a
note by eye. Second: a note that opens with a bare horizontal rule (---) and
no real key: value frontmatter is still read as a frontmatter block by
bm_vault_ids.frontmatter, the estate-wide helper this module (and
apply_promotion above) delegates to for locating that block; that behavior
is owned by bm_vault_ids.py, not by this module, and any fix belongs there.
"""
import argparse
import datetime
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_ids as ids       # noqa: E402
import bm_vault_lifecycle as lc  # noqa: E402

FIELD_RE = re.compile(r"(?m)^(promotion|promoted_by|promoted_at):.*\n?")
AUTHOR_RE = re.compile(r"(?m)^author:\s*(.+?)\s*$")


def _read_author(text):
    """The note's declared `author:` frontmatter value, or None. Read the
    same way bm_vault_ids.read_id reads `id:`: from the raw frontmatter
    block, quotes stripped, never guessed. A missing frontmatter block, a
    missing author field, or an empty value all read as None alike, so a
    caller cannot mistake "" for a real identity."""
    block, _start, _end = ids.frontmatter(text)
    if block is None:
        return None
    m = AUTHOR_RE.search(block)
    if not m:
        return None
    value = m.group(1).strip().strip('"').strip("'")
    return value or None


def _resolve(vault, ident):
    """A vault-relative path for `ident`, or None. Tries a stable id first
    (bm_vault_ids.resolve, filename matching off, matching that module's own
    no-guessing default), then a path relative to the vault root."""
    hit = ids.resolve(vault, ident, allow_stem=False)
    if hit is not None:
        return hit
    candidate = os.path.join(vault, ident)
    if os.path.isfile(candidate):
        return os.path.relpath(candidate, vault)
    return None


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _atomic_write(path, text):
    """Write text to path atomically: write to a temp file in the SAME
    directory, then os.replace it over the target (atomic on POSIX). Plain
    open(path, "w") truncates in place with no lock on a corpus other
    sessions read concurrently, so a crash mid-write would leave a truncated
    note; this way a reader always sees either the old file or the new one,
    never a partial one."""
    dirname = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".bm-promote-", suffix=".tmp", dir=dirname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # sbe: allow-silent best-effort cleanup of the temp file; the real error is already re-raised below
            pass
        raise


def apply_promotion(text, new_state, by, at):
    """The new file text, or None if there is no frontmatter block to record
    into. Removes any existing promotion/promoted_by/promoted_at lines (so
    re-promoting is idempotent-shaped, not accumulating) and appends the
    fresh ones. Splices rather than reserialising, same reasoning as
    bm_vault_ids.add_id: a note's other frontmatter and its key order must
    come back byte identical."""
    block, start, end = ids.frontmatter(text)
    if block is None:
        return None
    stripped = FIELD_RE.sub("", block).rstrip("\n")
    lines = ["promotion: %s" % new_state]
    if by:
        lines.append("promoted_by: %s" % by)
    if at:
        lines.append("promoted_at: %s" % at)
    new_block = stripped + "\n" + "\n".join(lines)
    return text[:start] + new_block + text[end:]


def legal_move(old_state, new_state):
    """True for the contract's own legal moves, plus the one bootstrap move
    the contract does not model: legacy (no declaration yet) to candidate.
    Everything else, including legacy straight to validated/canonical/
    rejected, is False: that would skip the first declaration the same way
    candidate-to-canonical skips validation."""
    if old_state == "legacy":
        return new_state == "candidate"
    return lc.legal_transition(old_state, new_state)


def cmd_check(vault):
    return lc.cmd_check(vault)


def cmd_state(vault, ident):
    path = _resolve(vault, ident)
    if path is None:
        print("bm_vault_promotions: NO-DATA, %r resolves to no note" % ident,
              file=sys.stderr)
        return 2
    state, record, problems = lc.read_promotion(_read(os.path.join(vault, path)))
    print("note: %s" % path)
    print("state: %s" % (state if state is not None else "UNKNOWN"))
    for k, v in sorted(record.items()):
        print("  %s: %s" % (k, v))
    if problems:
        print("FINDINGS: %d" % len(problems))
        for p in problems:
            print("  %s" % p)
    return 1 if (state is None or problems) else 0


def cmd_promote(vault, ident, new_state, by, at, apply_changes):
    path = _resolve(vault, ident)
    if path is None:
        print("bm_vault_promotions: NO-DATA, %r resolves to no note" % ident,
              file=sys.stderr)
        return 2
    full = os.path.join(vault, path)
    text = _read(full)
    old_state, _record, problems = lc.read_promotion(text)
    if old_state is None:
        print("REFUSED: %s carries an unrankable promotion value (%s); fix it "
              "before promoting further" % (path, "; ".join(problems)))
        return 1
    if old_state == new_state:
        print("no-op: %s already holds %s; nothing written" % (path, new_state))
        return 0
    if not legal_move(old_state, new_state):
        print("REFUSED: %s -> %s is not a legal move for %s"
              % (old_state, new_state, path))
        return 1
    if new_state != "candidate" and not by:
        print("bm_vault_promotions: promoting to %s needs --by; a promotion "
              "that is not recorded did not happen" % new_state, file=sys.stderr)
        return 2
    if new_state != "candidate":
        # V14: separation of duties, wired to the contract rather than
        # re-decided here (bm_vault_lifecycle.check_separation_of_duties).
        # No author of record is NOT the same as "nobody to collide with":
        # the contract itself treats a missing identity as a pass (nothing
        # to compare), so this module adds its own fail-closed refusal in
        # front of it, before any write.
        author = _read_author(text)
        if not author:
            print("REFUSED: %s carries no author of record (NO-DATA); "
                  "refusing to promote under the default fail-closed "
                  "separation-of-duties policy" % path)
            return 1
        sod = lc.check_separation_of_duties(author, by)
        if sod:
            print("REFUSED: %s" % sod)
            return 1
    new_text = apply_promotion(text, new_state, by, at)
    if new_text is None:
        print("REFUSED: %s has no frontmatter block to record a promotion into"
              % path)
        return 1
    print("%s %s: %s -> %s" % ("would promote" if not apply_changes else "promoted",
                                path, old_state, new_state))
    if apply_changes:
        _atomic_write(full, new_text)
    else:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check", "state", "promote"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--id", dest="ident", help="note id or vault-relative path, "
                    "for state and promote")
    ap.add_argument("--to", choices=lc.STATES, help="target state, for promote")
    ap.add_argument("--by", help="who is recording the promotion, for promote")
    ap.add_argument("--at", default=None, help="ISO date, for promote; "
                    "defaults to today")
    ap.add_argument("--apply", action="store_true", help="for promote: actually write")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_promotions: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    if args.command == "check":
        return cmd_check(args.vault)
    if not args.ident:
        ap.error("%s needs --id" % args.command)
    if args.command == "state":
        return cmd_state(args.vault, args.ident)
    if not args.to:
        ap.error("promote needs --to")
    at = args.at or datetime.date.today().isoformat()
    return cmd_promote(args.vault, args.ident, args.to, args.by, at, args.apply)


if __name__ == "__main__":
    sys.exit(main())
