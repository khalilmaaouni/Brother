#!/usr/bin/env python3
"""vault_anchor_backfill.py: PROPOSE applies_to anchors for old failure notes.

Owner order 2026-10-04: retrieval of the Failures-Index lessons must be easy
and cheap, and the recall hook only promotes a lesson whose frontmatter
applies_to names the touched file. Almost no 40-Failures note declares one.
This tool reads each note in VAULT/40-Failures that has frontmatter but no
applies_to field, collects the repo-relative paths named in its BODY that
exist as files under --tree, and proposes them as the note's applies_to.

  python3 scripts/vault_anchor_backfill.py --vault DIR --tree REPO
      DRY RUN (the default): prints one row per note, then the counts. Writes
      nothing.
  ... --apply
      Adds only the `applies_to: [...]` line just before each proposed note's
      closing frontmatter fence. The body and every other frontmatter line are
      left byte for byte. Run it only after the owner has seen the dry run.

A path is a token with at least one "/" and a file extension, not absolute,
not under a home or temp directory, that resolves to a file under --tree.
A note that already declares applies_to (any value, including the empty list
a process lesson carries) is skipped, and so is an index note (type: index). A note with no frontmatter is reported
and never written. Exit 0 on a clean run, 2 NO-DATA when the folder or the
tree is missing. Python 3, standard library only.
"""
import argparse
import os
import re
import sys
import tempfile

VAULT = os.path.expanduser("~/Documents/Kay Vault")
INDEX_NAME = "Failures-Index.md"
#: Repo-relative path shape: segments of word, dot or dash characters joined by
#: "/", ending in a file extension. The look-behind refuses a token that
#: continues an absolute or home path ("/x/a.py", "~/a.py").
PATH_RE = re.compile(r"(?<![\w./~-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+)")


def split_frontmatter(text):
    """(front, rest) where front is the text between the fences and rest
    starts at the closing fence line; (None, text) without frontmatter."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end == -1 and text.endswith("\n---"):
        end = len(text) - 4
    if end == -1:
        return None, text
    return text[4:end + 1], text[end + 1:]


def propose(body, tree):
    """Distinct repo-relative paths named in `body` that are files under
    `tree`, in first-mention order. Never a path containing a comma or a
    bracket (the one-line frontmatter list cannot hold one)."""
    out = []
    for m in PATH_RE.finditer(body):
        p = m.group(1)
        if p.startswith("./"):
            p = p[2:]
        if p in out or p.startswith(".."):
            continue
        if os.path.isfile(os.path.join(tree, p)):
            out.append(p)
    return out


def scan(vault, tree):
    """List of (slug, status, anchors) per note: status is "proposed",
    "none" (nothing resolvable named), "has applies_to", "index note" or
    "no frontmatter".
    None when the folder is missing (NO-DATA)."""
    folder = os.path.join(vault, "40-Failures")
    try:
        names = sorted(os.listdir(folder))
    except OSError:  # sbe: allow-silent explicit None sentinel, the NO-DATA case
        return None
    rows = []
    for fn in names:
        if not fn.endswith(".md") or fn == INDEX_NAME:
            continue
        slug = fn[:-3]
        try:
            with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            rows.append((slug, "unreadable or not UTF-8", []))
            continue
        front, rest = split_frontmatter(text)
        if front is None:
            rows.append((slug, "no frontmatter", []))
        elif re.search(r"^applies_to:", front, re.M):
            rows.append((slug, "has applies_to", []))
        elif re.search(r"^type:\s*\"?index\"?\s*$", front, re.M):
            rows.append((slug, "index note", []))
        else:
            anchors = propose(rest, tree)
            rows.append((slug, "proposed" if anchors else "none", anchors))
    return rows


def apply_one(path, anchors):
    """Insert the applies_to line before the closing fence, keeping the note's
    own newline style (CRLF stays CRLF) and every other byte. Written to a
    temp file in the same folder, then os.replace, so a crash never leaves a
    half-written note. Returns None on success, else the reason it refused:
    unreadable or not UTF-8, or the note changed shape since the scan."""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "unreadable or not UTF-8 (%s)" % type(exc).__name__
    nl = "\r\n" if text.startswith("---\r\n") else "\n"
    front, _rest = split_frontmatter(text.replace("\r\n", "\n"))
    if front is None or re.search(r"^applies_to:", front, re.M):
        return "changed since the scan"
    fence = text.find(nl + "---", 3)
    if fence == -1:
        return "changed since the scan"
    at = fence + len(nl)
    new = text[:at] + "applies_to: [%s]" % ", ".join(anchors) + nl + text[at:]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".backfill-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new.encode("utf-8"))
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:  # sbe: allow-silent the temp may already be gone; the refusal below is the answer
            pass
        return "write failed (%s)" % exc
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", default=VAULT)
    ap.add_argument("--tree", default=os.getcwd(),
                    help="the repository the paths must resolve in")
    ap.add_argument("--apply", action="store_true",
                    help="write the proposed applies_to lines (default: dry run)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.tree):
        print("NO-DATA: tree not found at %s" % args.tree)
        return 2
    rows = scan(args.vault, args.tree)
    if rows is None:
        print("NO-DATA: vault failures not found at %s"
              % os.path.join(args.vault, "40-Failures"))
        return 2
    folder = os.path.join(args.vault, "40-Failures")
    written = 0
    for slug, status, anchors in rows:
        if status == "proposed":
            print("%s | %s" % (slug, ", ".join(anchors)))
            if args.apply:
                why = apply_one(os.path.join(folder, slug + ".md"), anchors)
                if why is None:
                    written += 1
                else:
                    print("SKIPPED %s: %s" % (slug, why))
        elif status == "unreadable or not UTF-8":
            print("SKIPPED %s: %s" % (slug, status))
    count = lambda s: sum(1 for r in rows if r[1] == s)
    print("notes scanned: %d; with a proposal: %d (anchors proposed: %d); "
          "none resolvable: %d; already declare applies_to: %d; index notes: %d; "
          "no frontmatter: %d; unreadable or not UTF-8: %d"
          % (len(rows), count("proposed"), sum(len(r[2]) for r in rows),
             count("none"), count("has applies_to"), count("index note"),
             count("no frontmatter"), count("unreadable or not UTF-8")))
    print(("APPLIED to %d note(s)" % written) if args.apply
          else "DRY RUN: nothing written; pass --apply after review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
