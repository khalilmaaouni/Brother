#!/usr/bin/env python3
"""bm_vault_labels: derived memory cannot declassify. WBS VB3-13.

WHY THIS EXISTS. Codex's conceded refutation: a note synthesized from a
restricted source and a public one becomes an accidental declassifier unless
every derived claim carries an executable security label inherited from its
sources (most-restrictive wins by default) and lineage back to them. Human
approval does not declassify source material.

THE VOCABULARY, declared exactly ONCE, least to most restrictive:

    LABELS = ("public", "internal", "restricted")

Extensible: insert a new name at its correct rank; nothing else in this file
hardcodes a count, only that "restricted" is the name the legacy `restricted:
true` frontmatter field (bm_vault.py's own _is_restricted, bm_vault_export.py's
own read_restricted) maps to and from, so both of those ALREADY-SHIPPED
consumers keep working unmodified.

WHERE THIS LIVES, and why not inside bm_vault_policy.py: that module owns
decide()/decide_dual(), which are PATH-GLOB rules against an identity, and
know nothing about a note's own content or frontmatter. This module owns a
DIFFERENT axis entirely -- what a note's OWN content declares about itself --
and composes with decide()/decide_dual() rather than duplicating them: the
one thing a caller does with a computed label is write it into the SAME
`restricted: true` field bm_vault.py's recall trim and bm_vault_export.py's
sensitivity column already read, so enforcement stays exactly where it
already lived. No third, parallel deny mechanism is added anywhere by this
module: it never withholds a note itself, and it exposes no decide()-shaped
function of its own.

DERIVATION. When a note is composed or synthesized from named sources
(bm_vault_compose.py's split/merge -- see that module for the one place this
executes), annotate_derivation() computes the new note's security_label as
derive() of its sources' labels (most-restrictive wins) and stamps a
derivation record onto the SAME note, in plain frontmatter fields
bm_vault_lineage.py's INTAKE section already knows how to read straight off a
note (no second store): derived_from_ids, derived_from_labels (aligned by
position, the label each source carried AT DERIVATION TIME, never a live
pointer) and derived_at.

HUMAN APPROVAL DOES NOT DECLASSIFY. A promotion (bm_vault_promotions.py /
bm_vault_pane.py's own ceremony) never touches security_label at all -- it is
outside this module's surface entirely, so promoting a derived note leaves
its inherited label exactly as derived. The only way to loosen what a SOURCE
declares is relabel_source(), and it never reaches backward into anything
already derived from that source: propagate() is the forward-only half, and
it only ever TIGHTENS an already-derived note (a source relabeled looser
never loosens anything derived from it). check_derived_notes() is the
standing check: a derived note's own declared label weaker than derive() of
its own recorded per-source labels is a POLICY VIOLATION, named, never
silently honored -- exactly the shape a hand-edit after the fact would leave
behind.

CLI: `check --vault V` runs check_derived_notes() over the whole vault.
Exit 0 clean. Exit 1 one or more violations found, each named. Exit 2
NO-DATA, no readable vault. Python 3.9, standard library only, no network.

No em or en dashes anywhere in this file.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_ids as ids  # noqa: E402 -- reuse frontmatter()/walk(), never re-derive

LABELS = ("public", "internal", "restricted")
RANK = {name: i for i, name in enumerate(LABELS)}


def rank_of(label):
    """LABELS fail toward the more restrictive reading (project rule): a
    label this vocabulary does not recognize ranks as the MOST restrictive
    rank declared, never as unranked or permissive."""
    return RANK.get(label, len(LABELS) - 1)


def most_restrictive(a, b):
    """The more restrictive of two labels. Commutative: whichever argument
    order, the higher-rank one wins."""
    return a if rank_of(a) >= rank_of(b) else b


def derive(labels):
    """The most restrictive label across an iterable of source labels.
    "public" -- the least restrictive, the correct baseline for zero
    declared sources -- when the iterable is empty."""
    result = LABELS[0]
    for label in labels:
        result = most_restrictive(result, label)
    return result


_SECURITY_LABEL_RE = re.compile(r"^security_label:\s*(\S+)\s*$", re.M)
# Mirrors bm_vault.py's own _RESTRICTED_RE and bm_vault_export.py's own
# RESTRICTED_RE verbatim (same regex, same accepted spellings): "mirror, don't
# share" for one regex, the same call bm_vault_principals.py's own
# normalize_identity docstring already makes.
_RESTRICTED_RE = re.compile(r"^restricted:\s*(\S+)\s*$", re.M)


def read_label(text):
    """The label a note DECLARES today. Its own security_label: field wins
    when present (an unrecognized value fails toward "restricted", the most
    severe rank this vocabulary knows, never toward "public"); otherwise the
    legacy restricted: true/false field maps to "restricted"/"public"; a note
    with neither declares "public", matching every note written before this
    row existed."""
    block, _start, _end = ids.frontmatter(text)
    if block is None:
        return LABELS[0]
    m = _SECURITY_LABEL_RE.search(block)
    if m:
        value = m.group(1).strip().strip('"').strip("'")
        return value if value in RANK else LABELS[-1]
    m = _RESTRICTED_RE.search(block)
    if m:
        value = m.group(1).strip().strip('"').strip("'").lower()
        return "restricted" if value in ("true", "yes", "1") else LABELS[0]
    return LABELS[0]


def _set_field(text, key, value):
    """text with a scalar `key: value` frontmatter field set (replacing an
    existing line in place, or appended when absent) -- the same insert-or-
    replace splice contract bm_vault_ids.add_id and bm_vault_compose's own
    _add_supersedes already use, generalized to any key so no second
    frontmatter parser gets written here. None when text has no frontmatter
    block to write into (never invents one; that is the caller's refusal to
    make)."""
    block, start, end = ids.frontmatter(text)
    if block is None:
        return None
    pattern = re.compile(r"(?m)^%s:.*$" % re.escape(key))
    line = "%s: %s" % (key, value)
    if pattern.search(block):
        new_block = pattern.sub(line, block, count=1)
    else:
        sep = "" if block.endswith("\n") else "\n"
        new_block = block + sep + line
    return text[:start] + new_block + text[end:]


def apply_label(text, label):
    """text with security_label: label set AND the legacy restricted: field
    kept in sync (true at label's rank >= "restricted", false below it), so
    EVERY EXISTING consumer of restricted: true -- bm_vault.py's own recall
    trim fallback, bm_vault_export.py's sensitivity column -- honors the new
    label with no change of its own. This module never withholds anything
    itself; it only makes the field the existing mechanisms already read say
    the right thing (composes with decide/decide_dual, never a parallel
    mechanism). None when text has no frontmatter block."""
    new_text = _set_field(text, "security_label", label)
    if new_text is None:
        return None
    restricted_value = "true" if rank_of(label) >= RANK["restricted"] else "false"
    return _set_field(new_text, "restricted", restricted_value)


def annotate_derivation(text, sources, derived_at):
    """text with the inherited label applied (most restrictive of sources'
    labels) plus a derivation record bm_vault_lineage.py's own INTAKE section
    can read straight off this note's frontmatter, no second store:
    derived_from_ids (comma list), derived_from_labels (comma list, aligned
    by position -- each source's label AT DERIVATION TIME, per the row's own
    words, never a live pointer a later source edit could rewrite
    retroactively) and derived_at. sources is [(source_id_or_relpath, label),
    ...]. None when text has no frontmatter block."""
    label = derive(l for _s, l in sources)
    new_text = apply_label(text, label)
    if new_text is None:
        return None
    new_text = _set_field(new_text, "derived_from_ids",
                           ",".join(s for s, _l in sources))
    new_text = _set_field(new_text, "derived_from_labels",
                           ",".join(l for _s, l in sources))
    return _set_field(new_text, "derived_at", derived_at)


def relabel_source(text, new_label, changed_at):
    """(new_text, old_label) for a source note's OWN label being changed by a
    recorded act -- security_label_changed_from/security_label_changed_at
    land on THIS note, never on any note derived from it (the row's own
    words: "changing a label is its own recorded act on the SOURCE,
    propagating forward, never an edit on the derived note"; propagate()
    below is the forward half). (None, old_label) when text has no
    frontmatter block."""
    old_label = read_label(text)
    new_text = apply_label(text, new_label)
    if new_text is None:
        return None, old_label
    new_text = _set_field(new_text, "security_label_changed_from", old_label)
    new_text = _set_field(new_text, "security_label_changed_at", changed_at)
    return new_text, old_label


def check_derived_notes(vault):
    """[violation, ...], one per derived note (a derived_from_labels field
    present) whose OWN declared label (read_label -- the same fail-restrictive
    reading a recall trim would use) is WEAKER than derive() of its own
    recorded per-source labels. A hand-set weaker label is never silently
    honored: this is that check. A live source's label having since changed
    is irrelevant here -- this checks the note against its own recorded
    evidence; a live tightening reaches a derived note only through
    propagate(), never a check-time re-derivation. Each violation:
    {"relpath", "declared", "recorded_labels", "expected"}."""
    violations = []
    for abspath in ids.walk(vault):
        try:
            with open(abspath, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("bm_vault_labels: WARNING, could not read %s (%s); "
                             "excluded from this check\n" % (abspath, exc))
            continue
        block, _s, _e = ids.frontmatter(text)
        if block is None:
            continue
        m = re.search(r"^derived_from_labels:\s*(.*)$", block, re.M)
        if not m or not m.group(1).strip():
            continue
        recorded = [v.strip() for v in m.group(1).split(",") if v.strip()]
        expected = derive(recorded)
        declared = read_label(text)
        if rank_of(declared) < rank_of(expected):
            violations.append({
                "relpath": os.path.relpath(abspath, vault).replace(os.sep, "/"),
                "declared": declared, "recorded_labels": recorded, "expected": expected,
            })
    return violations


def propagate(vault, source_id, old_label, new_label):
    """[relpath, ...] of every derived note actually tightened because
    source_id's label moved from old_label to new_label. Only fires when
    new_label is STRICTLY more restrictive than old_label -- a source
    relabeled looser NEVER loosens anything already derived from it (human
    approval does not declassify; the same rule applies to a source's own
    relaxation). For every derived note recording source_id at exactly
    old_label in its own derived_from_labels, that one entry is tightened to
    new_label, the note's own recorded label is re-derived from the (now
    updated) recorded set, and the note's declared label is raised to match
    if that recompute is stricter than what it already declares (never
    lowered)."""
    if rank_of(new_label) <= rank_of(old_label):
        return []
    touched = []
    for abspath in ids.walk(vault):
        try:
            with open(abspath, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("bm_vault_labels: WARNING, could not read %s (%s); "
                             "excluded from this propagation pass\n" % (abspath, exc))
            continue
        block, _s, _e = ids.frontmatter(text)
        if block is None:
            continue
        ids_m = re.search(r"^derived_from_ids:\s*(.*)$", block, re.M)
        labels_m = re.search(r"^derived_from_labels:\s*(.*)$", block, re.M)
        if not ids_m or not labels_m:
            continue
        source_list = [v.strip() for v in ids_m.group(1).split(",")]
        label_list = [v.strip() for v in labels_m.group(1).split(",")]
        if source_id not in source_list:
            continue
        idx = source_list.index(source_id)
        if idx >= len(label_list) or label_list[idx] != old_label:
            continue
        label_list[idx] = new_label
        new_text = _set_field(text, "derived_from_labels", ",".join(label_list))
        expected = derive(label_list)
        declared = read_label(new_text)
        if rank_of(expected) > rank_of(declared):
            new_text = apply_label(new_text, expected)
        tmp = abspath + ".bm-vault-labels.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, abspath)
        touched.append(os.path.relpath(abspath, vault).replace(os.sep, "/"))
    return touched


def cmd_check(vault):
    if not vault or not os.path.isdir(vault):
        print("bm_vault_labels: NO-DATA, no readable vault at %r" % vault)
        return 2
    violations = check_derived_notes(vault)
    if not violations:
        print("bm_vault_labels: clean, no derived note declares a weaker label "
              "than its own recorded sources require")
        return 0
    for v in violations:
        print("VIOLATION: %s declares %r but its recorded sources %s require at "
              "least %r" % (v["relpath"], v["declared"], v["recorded_labels"], v["expected"]))
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    args = ap.parse_args(argv)
    return cmd_check(args.vault)


if __name__ == "__main__":
    sys.exit(main())
