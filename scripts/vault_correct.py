#!/usr/bin/env python3
"""Vault correction: fix a wrong vault note with one sentence (row V13,
docs/plan/READINESS-ROADMAP-2026-08-29.json).

The vault constitution (Documents/Kay Vault/AGENTS.md, sections 2 and 5)
forbids rewriting an existing note's body: 40-Failures notes are
"append only", and the prohibitions section says plainly "never edit
decisions, failures, or past session logs: supersede or append". This tool
honors that instead of working around it: it APPENDS a dated "## Correction"
section to the note's body and only ever touches two frontmatter keys
(status, corrected_at). Every other byte of the note, frontmatter and body
alike, is left exactly as it was.

Usage:
  python3 scripts/vault_correct.py --vault ROOT --note SLUG "one sentence"
      [--supersedes "the sentence it overrides"]

ROOT is a vault root (a directory that may hold 00-Home, 40-Failures, and
so on). SLUG is the note's filename stem (its wikilink target); the note is
found by walking ROOT for SLUG.md, so callers never need the note's
sub-folder.

Exit 0: corrected. Prints the note's path, then the appended block.
Exit 2 (NO-DATA/refusal), one line on stderr naming the reason:
  - no such note under ROOT
  - the sentence is empty
  - the sentence carries a dash character (this estate's house style
    forbids em/en dashes in prose; a plain hyphen is refused too, so
    nobody has to remember which dash is the allowed one)

KNOWN TENSION, named rather than papered over: products/brothermode/tools/
bm_vault_retention.py documents 40-Failures/Failures-Index.md as
hand-curated and states twice that it is "never auto-edited by this or any
tool". This script appends one routing line there when correcting a
40-Failures note the index does not already list, mirroring
scripts/handover_ceremony.py's note style, because the brief for this row
asked for exactly that. It is scoped to this one explicit, human- or
agent-invoked command, never a background sweep, and a reviewer should
confirm the exception holds before anything leans on it further.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

DASH_CHARS = ("-", "\u2013", "\u2014")  # hyphen, en dash, em dash


def find_note(vault_root, slug):
    """Path to SLUG.md under vault_root, or None if it does not exist."""
    filename = slug + ".md"
    for root, _dirs, files in os.walk(vault_root):
        if filename in files:
            return os.path.join(root, filename)
    return None


def has_dash(sentence):
    return any(ch in sentence for ch in DASH_CHARS)


def split_frontmatter(content):
    """(frontmatter_lines, body) where frontmatter_lines is the list of
    lines between the opening and closing '---' delimiters, inclusive, or
    [] when the note carries no (closed) frontmatter block. body is
    everything after the closing delimiter, or the whole content when
    there is no frontmatter."""
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return [], content
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[:i + 1], "".join(lines[i + 1:])
    return [], content  # opening delimiter with no close: treat as no frontmatter


def set_frontmatter_field(front_lines, key, value):
    """Replace an existing 'key: ...' line in front_lines, or insert one
    just before the closing '---' when absent. front_lines must already
    include both delimiters (split_frontmatter's non-empty return)."""
    pattern = re.compile(r"^%s:\s*.*$" % re.escape(key))
    for i, line in enumerate(front_lines):
        if pattern.match(line.rstrip("\n")):
            front_lines[i] = "%s: %s\n" % (key, value)
            return front_lines
    # not found: insert before the closing '---' (the last line)
    front_lines.insert(len(front_lines) - 1, "%s: %s\n" % (key, value))
    return front_lines


def build_correction_block(sentence, supersedes, today):
    lines = ["\n## Correction %s\n" % today, "\n", "%s\n" % sentence]
    if supersedes:
        lines.append("\nSupersedes: %s\n" % supersedes)
    return "".join(lines)


def correct_note(path, sentence, supersedes=None, today=None):
    """Applies the correction in place. Returns the appended block text
    (for the CLI to echo). Raises nothing; callers already validated the
    sentence and the path."""
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(path, encoding="utf-8") as f:
        content = f.read()

    front_lines, body = split_frontmatter(content)
    if front_lines:
        set_frontmatter_field(front_lines, "status", "corrected")
        set_frontmatter_field(front_lines, "corrected_at", today)
        new_front = "".join(front_lines)
    else:
        new_front = ""

    block = build_correction_block(sentence, supersedes, today)
    if body and not body.endswith("\n"):
        body += "\n"
    new_content = new_front + body + block

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return block


# ---------------------------------------------------------------------------
# Failures-Index.md routing line (only for a 40-Failures note; see the
# KNOWN TENSION note in the module docstring).
# ---------------------------------------------------------------------------

def is_failures_note(vault_root, note_path):
    rel = os.path.relpath(note_path, vault_root)
    return rel.split(os.sep)[0] == "40-Failures"


def append_index_line(vault_root, slug, sentence, today):
    """Appends '- [[slug]] date. sentence.' to Failures-Index.md under a
    '## Corrections (auto)' section, creating that section if absent.
    No-op (returns False) when the index is missing (NO-DATA is never
    invented) or the slug is already listed there. Returns True when a
    line was appended."""
    index_path = os.path.join(vault_root, "40-Failures", "Failures-Index.md")
    if not os.path.isfile(index_path):
        return False
    with open(index_path, encoding="utf-8") as f:
        index_content = f.read()
    if ("[[%s]]" % slug) in index_content:
        return False

    line = "- [[%s]] %s. %s\n" % (slug, today, sentence)
    heading = "## Corrections (auto)\n"
    if heading in index_content:
        index_content = index_content.rstrip("\n") + "\n" + line
    else:
        if not index_content.endswith("\n"):
            index_content += "\n"
        index_content += "\n" + heading + "\n" + line
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_content)
    return True


def run(vault_root, slug, sentence, supersedes=None, today=None, out=None,
        err=None):
    """The whole command, seams injected for the test suite. Returns the
    exit code (0 or 2)."""
    out = out or sys.stdout
    err = err or sys.stderr

    sentence = (sentence or "").strip()
    if not sentence:
        err.write("NO-DATA: the correction sentence is empty\n")
        return 2
    if has_dash(sentence):
        err.write("NO-DATA: the correction sentence carries a dash "
                   "character, refused: %r\n" % sentence)
        return 2

    path = find_note(vault_root, slug)
    if path is None:
        err.write("NO-DATA: no note named %s.md under %s\n" % (slug, vault_root))
        return 2

    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    block = correct_note(path, sentence, supersedes=supersedes, today=today)

    if is_failures_note(vault_root, path):
        append_index_line(vault_root, slug, sentence, today)

    out.write("%s\n" % path)
    out.write(block)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True, help="vault root directory")
    ap.add_argument("--note", required=True,
                     help="the note's filename stem (its wikilink slug)")
    ap.add_argument("--supersedes", default=None,
                     help="the sentence this correction overrides")
    ap.add_argument("sentence", help="the one-sentence correction")
    args = ap.parse_args(argv)
    return run(args.vault, args.note, args.sentence,
               supersedes=args.supersedes)


if __name__ == "__main__":
    sys.exit(main())
