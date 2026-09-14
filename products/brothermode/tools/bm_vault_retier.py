#!/usr/bin/env python3
"""bm_vault_retier: re-tier legacy vault notes under the strict-withhold law.

WHY THIS EXISTS (founder ruling 2026-09-06, question UI, verbatim "Strict
everywhere, re-tag the old notes"; FIX-DIRECTIVE-2026-09-06.md sections 3 and
4). A recalled lesson must resolve into exactly one trust state, and an
unclassified lesson (neither `evidence_locator:` nor `status:` declared) no
longer defaults to APPLY: bm_vault_contradiction.evidence_tier() now returns
TIER_UNVERIFIED for it, unknown means WITHHOLD. That flip lands on every note
in a real vault written before this row, which is most of them, so this tool
re-tags them ONCE: every note missing `status:` gets `status:
legacy-untiered` plus `retiered_at: <date>`, and, only where the note already
names evidence that actually resolves on disk, an `evidence_locator:` in the
one shape bm_vault_contradiction.make_evidence_probe() reads (`path:
<relpath>`). A note that can genuinely still prove itself keeps applying at
recall; a note that cannot is served as UNVERIFIED rather than silently as
before, which is the entire point of this row.

WHAT COUNTS AS EVIDENCE ALREADY ON THE NOTE. Frontmatter fields `evidence`,
`path`, `source`, `verified-by`, `verified_by` (checked in that order, first
non-empty wins), plus a body line reading "verified by: <value>" or
"verified-by: <value>" (case-insensitive) when no frontmatter field
qualifies. The value is resolved against --repo-root (default: the vault
root itself, for a note whose evidence is a sibling vault path) as a plain
relative or absolute path. A target outside --repo-root (a `..` escape) is
refused, never followed, the same posture the poisoning gauntlet's
locator-escapes-the-tree case demands of the reader, not just the writer.
A resolving target becomes `evidence_locator: path:<relpath-from-repo-root>`;
anything else is left with only the two status lines, per the brief this row
was written against ("notes it cannot evidence keep only the status line").

NEVER TOUCHED: a note that already carries a `status:` field of any value.
This tool tiers the UNTIERED; it never re-tiers, overwrites, or upgrades a
verdict a human or another lane already wrote.

MALFORMED FRONTMATTER: a file that does not open with a `---` fence, or
opens with one that never closes with a second `---`, is REFUSED (left on
disk exactly as found, counted and named with its own reason) rather than
guessed at: this tool never risks writing a new fence into a broken or
absent block.

IDEMPOTENT: a second run over an already-retiered vault changes 0 notes and
prints a "0 notes changed" line at exit 0, because every already-tiered note
(a tier this run itself just wrote, or any pre-existing status) is skipped
by the same `status:` check.

Python 3.9, standard library only. This tool never imports
bm_vault_contradiction and never runs the tier resolver; it only prepares
notes to be read by it correctly. The accepted evidence_locator shape is
documented above and mirrored, not imported, the stated convention of every
sibling contract module in this family (bm_vault_triage.py's own docstring:
"so no module's behaviour shifts when a sibling changes"). The frontmatter
regex below is the same duplicated `key: value` pattern bm_vault_triage.py
and bm_vault_contradiction.py each carry their own copy of, for the same
reason.

Usage:
  bm_vault_retier.py VAULT_ROOT [--repo-root REPO] [--dry-run]

--dry-run prints the same counts a real run prints, plus the first ten
paths that would change, and writes nothing. The real run writes in place
and prints the counts alone.
"""
import argparse
import datetime
import os
import re
import sys

# Same shape as bm_vault_triage.py's own FRONTMATTER_FIELD_RE / SKIP_DIRS:
# plain `key: value` frontmatter lines, line-oriented; the vault-tooling
# folders no walk here should ever descend into.
FRONTMATTER_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z_-]*):\s*(.*?)\s*$", re.M)
SKIP_DIRS = {".git", ".trash", ".obsidian"}

#: Checked in this order against the frontmatter dict; the first non-empty
#: value wins. `verified-by` and `verified_by` cover both spellings a note's
#: author might have used, since neither is a field any other module here
#: already reads or standardizes.
EVIDENCE_FIELDS = ("evidence", "path", "source", "verified-by", "verified_by")

#: The body-prose fallback, only consulted when no frontmatter field above
#: qualifies: a line reading "verified by: X" or "verified-by: X", anchored
#: to the whole line so it never matches a word appearing mid-sentence.
VERIFIED_BY_BODY_RE = re.compile(r"(?im)^\s*verified[- ]by:?\s+(\S+)\s*$")


def _split_frontmatter(text):
    """(front_text, close) for a file whose frontmatter block is well
    formed, or (None, None) when the file does not open with `---` at all,
    or opens with one that never closes. `close` is the index of the `\\n`
    that starts the closing `---` line: text[:close] is everything up to
    and including the last frontmatter line, the exact point a new field
    line is inserted."""
    if not text.startswith("---"):
        return None, None
    close = text.find("\n---", 3)
    if close == -1:
        return None, None
    return text[3:close], close


def _find_evidence_value(front_text, body_text):
    fields = dict(FRONTMATTER_FIELD_RE.findall(front_text))
    for name in EVIDENCE_FIELDS:
        value = fields.get(name, "").strip()
        if value:
            return value
    m = VERIFIED_BY_BODY_RE.search(body_text)
    return m.group(1).strip() if m else None


def _resolve_evidence_locator(value, repo_root):
    """`path:<relpath>` (the shape make_evidence_probe reads) when `value`
    resolves to a real file under `repo_root`, else None. `value` may be a
    bare relative path, an absolute path, or the same value wrapped in
    [[wikilink]] brackets or quotes -- stripped here rather than asking
    every caller to pre-clean it. A target that resolves OUTSIDE repo_root
    (a `..` escape, or an absolute path elsewhere on disk) is refused: this
    tool never mints a locator it cannot vouch stays inside the tree it was
    told to trust."""
    if not value:
        return None
    value = value.strip().strip("\"'")
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2].strip()
    if not value:
        return None
    repo_root_norm = os.path.normpath(repo_root)
    candidate = value if os.path.isabs(value) else os.path.join(repo_root, value)
    candidate = os.path.normpath(candidate)
    try:
        common = os.path.commonpath([candidate, repo_root_norm])
    except ValueError:  # sbe: allow-silent different drives/roots never happens on posix, stay defensive
        return None
    if common != repo_root_norm:
        return None
    if not os.path.isfile(candidate):  # docstring promises a real file; a directory is not evidence
        return None
    rel = os.path.relpath(candidate, repo_root_norm)
    return "path:%s" % rel.replace(os.sep, "/")


def plan_vault(vault_root, repo_root, today=None):
    """{"to_tier": [(path, close, insertion_text), ...], "evidenced": int,
    "status_only": int, "already_tiered": int, "refused": [(path, reason),
    ...]}. Never writes; the caller decides whether to apply the plan."""
    today = today or datetime.date.today().isoformat()
    to_tier = []
    evidenced = 0
    status_only = 0
    already_tiered = 0
    refused = []
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as e:  # sbe: allow-silent an unreadable note is refused, never a crash of the whole walk
                refused.append((path, "unreadable: %s" % e))
                continue
            front_text, close = _split_frontmatter(text)
            if front_text is None:
                reason = ("no frontmatter block: file does not open with a --- fence"
                          if not text.startswith("---") else
                          "malformed frontmatter: no closing --- fence found")
                refused.append((path, reason))
                continue
            fields = dict(FRONTMATTER_FIELD_RE.findall(front_text))
            if fields.get("status", "").strip():
                already_tiered += 1
                continue
            value = _find_evidence_value(front_text, text[close:])
            locator = _resolve_evidence_locator(value, repo_root) if value else None
            new_lines = ["status: legacy-untiered", "retiered_at: %s" % today]
            if locator:
                new_lines.insert(0, "evidence_locator: %s" % locator)
                evidenced += 1
            else:
                status_only += 1
            insertion = "\n" + "\n".join(new_lines)
            to_tier.append((path, close, insertion))
    return {"to_tier": to_tier, "evidenced": evidenced, "status_only": status_only,
            "already_tiered": already_tiered, "refused": refused}


def apply_plan(plan):
    for path, close, insertion in plan["to_tier"]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        new_text = text[:close] + insertion + text[close:]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)


def _report(plan, changed_word):
    changed = len(plan["to_tier"])
    print("%d notes %s (%d evidenced, %d status-only)"
          % (changed, changed_word, plan["evidenced"], plan["status_only"]))
    print("already tiered (skipped): %d" % plan["already_tiered"])
    print("refused: %d" % len(plan["refused"]))
    for path, reason in plan["refused"]:
        print("  REFUSED %s: %s" % (path, reason))


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Re-tag every legacy vault note lacking status: with "
                     "status: legacy-untiered, retiered_at, and an "
                     "evidence_locator only where the note's own evidence "
                     "field already resolves. Never touches a note that "
                     "already carries status:.")
    p.add_argument("vault_root", help="vault directory to walk")
    p.add_argument("--repo-root", default=None,
                    help="base directory an existing evidence/path/source/"
                         "verified-by value resolves against; default: "
                         "vault_root itself")
    p.add_argument("--dry-run", action="store_true",
                    help="print counts and the first ten paths that would "
                         "change; write nothing")
    args = p.parse_args(argv)

    vault_root = os.path.abspath(args.vault_root)
    if not os.path.isdir(vault_root):
        sys.stderr.write("NO-DATA: vault root %s does not exist\n" % vault_root)
        return 2
    repo_root = os.path.abspath(args.repo_root) if args.repo_root else vault_root

    plan = plan_vault(vault_root, repo_root)
    if args.dry_run:
        _report(plan, "would change")
        changed = len(plan["to_tier"])
        print("first %d path(s) that would change:" % min(10, changed))
        for path, _close, _insertion in plan["to_tier"][:10]:
            print("  %s" % path)
        return 0
    apply_plan(plan)
    _report(plan, "changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
