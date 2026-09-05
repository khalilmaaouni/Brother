#!/usr/bin/env python3
"""Vault correction: fix a wrong vault note with one sentence (row V13,
docs/plan/READINESS-ROADMAP-2026-08-29.json).

The vault constitution (Documents/Kay Vault/AGENTS.md, sections 2 and 5)
forbids rewriting an existing note's body: 40-Failures notes are
"append only", and the prohibitions section says plainly "never edit
decisions, failures, or past session logs: supersede or append". Row V13's
own done_check spells out which half of that sentence this tool takes:
it WRITES A NEW SUPERSEDING NOTE, never edits the old one's body, and links
the two through frontmatter so recall can withhold the old one with a
named reason instead of serving a lesson someone already said is wrong.

Usage:
  python3 scripts/vault_correct.py --vault ROOT --note SLUG "one sentence"
      [--supersedes "the sentence it overrides"] [--deny-list PATH]

ROOT is a vault root (a directory that may hold 00-Home, 40-Failures, and
so on). SLUG is the OLD note's filename stem (its wikilink target); the
note is found by walking ROOT for SLUG.md, so callers never need the
note's sub-folder.

WHAT IS WRITTEN. A new note, SLUG-correction-DATE.md, lands beside the old
note (same folder) with `type: correction`, `supersedes: [[SLUG]]`, and the
one-sentence correction as its body. That write goes through the SAME
admission gate every other vault entry point uses: bm_vault_intake.py's
`hard_gate` (credential_hit, then deny_list_hit when --deny-list is given),
loaded by path exactly the way scripts/pattern_note.py already does, so a
correction can no more carry a credential or a denied term into the vault
than a capture or an admit can. The OLD note then gets three frontmatter
keys set: status: corrected, corrected_at: DATE, superseded_by: [[NEW-SLUG]].
Its body is read and rewritten byte for byte unchanged; only the
frontmatter block (found via split_frontmatter/set_frontmatter_field) is
touched. A note with no frontmatter block at all cannot carry that pointer
(nothing is invented in its place); the new superseding note is still
written and named on stdout, but the reverse link is skipped and said so.

THE RECALL SIDE IS NOT THIS FILE. products/brothermode/tools/bm_vault.py
already indexes any note's `supersedes: [[stem]]` frontmatter line into a
supersessions table (`_rebuild_supersessions`) and, at every `check`/
`recall` query, withholds the superseded note and names its successor
("WITHHELD (superseded) ... superseded by: ..."), proven by
test_bm_vault.py's ASupersededLessonIsNotServedAsCurrent. This tool's whole
job is to produce a note in the shape that mechanism already reads: write a
real `supersedes:` link and nothing here re-implements the withholding.

Exit 0: corrected. Prints the new note's path, then the old note's path it
supersedes.
Exit 2 (NO-DATA/refusal), one line on stderr naming the reason:
  - no such note under ROOT
  - the sentence is empty
  - the sentence carries a dash character (this estate's house style
    forbids em/en dashes in prose; a plain hyphen is refused too, so
    nobody has to remember which dash is the allowed one)
  - the admission gate refused the sentence (a credential shape, or a
    denied term when --deny-list was given)

KNOWN TENSION, named rather than papered over: products/brothermode/tools/
bm_vault_retention.py documents 40-Failures/Failures-Index.md as
hand-curated and states twice that it is "never auto-edited by this or any
tool". This script appends one routing line there, pointing at the NEW
(superseding) note rather than the old one, when the old note lives in
40-Failures and the index does not already list the new note; mirroring
scripts/handover_ceremony.py's note style, because the brief for this row
asked for exactly that. It is scoped to this one explicit, human- or
agent-invoked command, never a background sweep, and a reviewer should
confirm the exception holds before anything leans on it further.
"""

import argparse
import importlib.util
import os
import re
import sys
from datetime import datetime, timezone

DASH_CHARS = ("-", "\u2013", "\u2014")  # hyphen, en dash, em dash


def _load_intake_gate():
    """bm_vault_intake.py's hard_gate, loaded by path (products/brothermode/
    tools/bm_vault_intake.py, a sibling tree this module never edits): the
    SAME credential and deny-list gate `admit` and `capture` run before
    writing, so a correction note routes through the estate's one front
    door for vault content rather than being written ungated. Mirrors
    scripts/pattern_note.py's _load_intake_gate exactly. Returns None when
    the module cannot be loaded, so the caller can fail closed rather than
    silently skip the gate."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "products", "brothermode", "tools",
                         "bm_vault_intake.py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "bm_vault_intake_for_correct", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.hard_gate
    except Exception:  # sbe: allow-silent optional gate module load failure, gate_text below turns this into a named refusal rather than an ungated write
        return None


def gate_text(text, deny_list_path=None, loader=_load_intake_gate):
    """(ok, reason_or_None) via bm_vault_intake.hard_gate. Fails closed,
    matching capture's own contract: a gate that could not be loaded is a
    refusal, never a silent ungated pass."""
    hard_gate = loader()
    if hard_gate is None:
        return False, ("NO-DATA: bm_vault_intake.hard_gate unavailable, "
                        "the admission gate could not run")
    return hard_gate(text, deny_list_path)


def find_note(vault_root, slug):
    """Path to SLUG.md under vault_root, or None if it does not exist."""
    filename = slug + ".md"
    for root, _dirs, files in os.walk(vault_root):
        if filename in files:
            return os.path.join(root, filename)
    return None


def has_dash(sentence):
    return any(ch in sentence for ch in DASH_CHARS)


def slug_from(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "correction"


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


def superseding_note_path(old_path, old_slug, today):
    """A fresh path beside old_path, never colliding with one already on
    disk: a second same-day correction of the same note gets -2, -3, and so
    on, rather than overwriting the first correction note."""
    directory = os.path.dirname(old_path)
    base = "%s-correction-%s" % (old_slug, today)
    candidate = os.path.join(directory, base + ".md")
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, "%s-%d.md" % (base, n))
        n += 1
    return candidate


def build_superseding_note(old_slug, sentence, supersedes_text, today):
    """The new note's full text: frontmatter carrying `supersedes: [[old_slug]]`
    (the exact field products/brothermode/tools/bm_vault.py's
    _rebuild_supersessions reads to withhold the old note at recall), then
    the correction sentence as the body."""
    lines = [
        "---",
        "type: correction",
        "status: standing",
        "created: %s" % today,
        "supersedes: [[%s]]" % old_slug,
        "description: %s" % sentence,
        "---",
        "",
        "# Correction of %s" % old_slug,
        "",
        sentence,
    ]
    if supersedes_text:
        lines += ["", "Supersedes: %s" % supersedes_text]
    body = "\n".join(lines)
    return body if body.endswith("\n") else body + "\n"


def link_old_note(old_path, new_slug, today):
    """Frontmatter-only edit of the OLD note: status, corrected_at,
    superseded_by. The body string is read and written back verbatim,
    unchanged, so nothing below the closing '---' can ever differ. Returns
    True when the frontmatter existed and was updated, False when the note
    carries no (closed) frontmatter block at all -- nothing is invented in
    that case, the reverse pointer is simply not recorded, and the caller
    still proceeds (the new note and its forward `supersedes:` link are
    what recall actually reads)."""
    with open(old_path, encoding="utf-8") as f:
        content = f.read()
    front_lines, body = split_frontmatter(content)
    if not front_lines:
        return False
    set_frontmatter_field(front_lines, "status", "corrected")
    set_frontmatter_field(front_lines, "corrected_at", today)
    set_frontmatter_field(front_lines, "superseded_by", "[[%s]]" % new_slug)
    with open(old_path, "w", encoding="utf-8") as f:
        f.write("".join(front_lines) + body)
    return True


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
    line was appended. Called here with the NEW (superseding) note's slug,
    so a reader following the index lands on the current lesson rather
    than the one just withheld."""
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
        err=None, deny_list=None, gate=gate_text):
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

    old_path = find_note(vault_root, slug)
    if old_path is None:
        err.write("NO-DATA: no note named %s.md under %s\n" % (slug, vault_root))
        return 2

    ok, reason = gate(sentence, deny_list)
    if not ok:
        err.write("NO-DATA: the admission gate refused the correction, %s\n" % reason)
        return 2

    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_path = superseding_note_path(old_path, slug, today)
    new_slug = os.path.splitext(os.path.basename(new_path))[0]
    with open(new_path, "w", encoding="utf-8") as f:
        f.write(build_superseding_note(slug, sentence, supersedes, today))

    linked = link_old_note(old_path, new_slug, today)

    if is_failures_note(vault_root, old_path):
        append_index_line(vault_root, new_slug, sentence, today)

    out.write("%s\n" % new_path)
    out.write("supersedes: %s\n" % old_path)
    if not linked:
        out.write("NOTE: the old note carries no frontmatter block, so "
                   "superseded_by could not be recorded on it\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", required=True, help="vault root directory")
    ap.add_argument("--note", required=True,
                     help="the OLD note's filename stem (its wikilink slug)")
    ap.add_argument("--supersedes", default=None,
                     help="the sentence this correction overrides")
    ap.add_argument("--deny-list", default=None,
                     help="a deny-list file for the admission gate, same as "
                          "bm_vault_intake.py's admit/capture")
    ap.add_argument("sentence", help="the one-sentence correction")
    args = ap.parse_args(argv)
    return run(args.vault, args.note, args.sentence,
               supersedes=args.supersedes, deny_list=args.deny_list)


if __name__ == "__main__":
    sys.exit(main())
