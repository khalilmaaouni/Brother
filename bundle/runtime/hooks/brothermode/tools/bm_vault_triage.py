#!/usr/bin/env python3
"""bm_vault_triage: scope-first contradiction triage, WBS row VB6-05, report-only.

WHY THIS EXISTS. The codex-debated insight this row encodes: most apparent
contradictions in a notes corpus are missing DIMENSIONS, not competing
claims. Two values differing by effective date, region or branch, legal
entity, source system, or as-of time are two scoped claims, not one
contradiction, and treating them as a contradiction is the false alarm this
row exists to stop before it reaches a human or a `contradicts:` edge.

TWO SHAPES MEASURED BEFORE WRITING THIS, both real, neither guessed:

  the claim line       owned by bm_vault_provenance.py: a plain-prose line
                        `claim: <text> [evidence: <locator>]`, a leading list
                        marker and indentation both allowed. Duplicated here
                        rather than imported, the same reason
                        bm_vault_provenance.py's own docstring gives for
                        duplicating bm_vault_ids.py's frontmatter read: every
                        sibling contract module in this family reads the
                        vault on its own, so no module's behaviour shifts
                        when a sibling changes.

  the contradicts edge owned by bm_vault_graph.py: a note-level, hand-written
                        `contradicts:` frontmatter field naming another note
                        by [[wikilink]], symmetric like `relates:` (declared
                        once, read both ways), resolved and gated for broken
                        targets the same way `supersedes:` is. This module
                        never writes that field: it reports CANDIDATE pairs
                        for a human (or a later, separate step) to mint one
                        from, never mints one itself.

Neither module's claim or frontmatter syntax carries an explicit "subject"
or "value" field, so this row cannot compare claims field-for-field; it
splits a claim's own prose on the LAST copula (`is`/`are`/`was`/`were`/
`equals`) into a subject and a value, which is a heuristic, not NLP.
Splitting on the last occurrence, not the first, matters: a claim's own
subject can carry an embedded copula ("the number that was reported is 5"),
and splitting on the first one truncates the subject to a shared fragment
("the number that") that an unrelated claim about a different number
("the number that was audited is 7") would collide with, becoming a false
CONTRADICTION between two claims that were never about the same thing.
This heuristic has two real failure modes, neither of them the other:
a claim this split cannot parse (no copula) has no comparable value and is
silently excluded from pairing, a false negative this tool never reports
(false silence, never a wrong verdict); and two claims whose full
pre-copula text is identical after normalization, despite being about
different things, still pair and classify by the dimension rules below (a
residual false-pair risk, bounded to subjects that match byte-for-byte
after normalization, not merely a shared prefix or suffix).

SCOPE DIMENSIONS, the row's named five, folded to one of five canonical
keys (date, asof, region, entity, source) so aliases compare as the same
axis: effective date / effective_date / effective-date -> date; as_of /
as-of -> asof; region / branch -> region; legal_entity / legal-entity /
entity -> entity; source_system / source-system / source -> source.
Recognised in two places: a bracket tag trailing a claim line's own
evidence bracket (`claim: x [evidence: a.md] [region: JP]`), or a plain
frontmatter field on the note holding the claim. Either or both.

CLASSIFICATION. Two claims sharing a subject (post-split) whose values
differ are a PAIR. A pair is SCOPED when at least one dimension is declared
on BOTH sides with differing values: that is a stated axis the two claims
disagree on, which is exactly a missing-dimension false contradiction. A
dimension named on only one side proves nothing about the other claim's
scope and never counts alone. Everything left over, where no shared
dimension differs (including the common case of neither side declaring
any), is a CONTRADICTION: a same-scope collision and a candidate for a
`contradicts:` edge bm_vault_graph.py would then carry, never one this
module writes.

Two claims inside the SAME note are never paired: a note contradicting
itself line to line is a drafting defect, not the cross-note collision this
row is scoped to find.

  scan --vault V   prints the three counts this row's own done-check names
                   (pairs examined, scoped, contradictions) plus one line
                   per pair naming its sources and, for a scoped pair, the
                   distinguishing dimension. A per-file OSError while
                   reading (permission denied, broken symlink, a race with
                   a delete) is counted, never raised or silently dropped:
                   an "unreadable files skipped: N" line prints whenever
                   N > 0, and the clean line below claims cleanliness only
                   over the files actually read. Zero pairs prints that
                   clean line and exits 0. Exits 1 when at least one
                   CONTRADICTION was found (report-only: this is a signal
                   for a human, never an action this module takes). An
                   unreadable vault path is NO-DATA, exit 2, before any
                   counting happens.

WHAT THIS IS NOT. It never mints a `contradicts:` edge, never edits a note,
never touches the corpus. Its output is a list of candidates for a human
(or a fenced, separate write step) to act on.

Python 3.9 floor, standard library only, writes nothing anywhere.
"""
import argparse
import os
import re
import sys

SKIP_DIRS = {".git", ".trash", ".obsidian"}

# Same syntax bm_vault_provenance.py owns, duplicated per this module's own
# docstring above. Unlike bm_vault_provenance.py's find_claims/
# find_malformed_claims split, this module keeps the trailing text (group 3)
# in every case: that trailing text is where a claim's own scope-dimension
# tags live, and whether it also happens to be "malformed" by
# bm_vault_provenance's separate opinion is not this module's concern.
CLAIM_RE = re.compile(
    r"^[ \t]*(?:[-*+]\s+)?claim:\s*(.+?)\s*\[evidence:\s*([^\]]+?)\s*\](.*)$", re.M)

DIMENSION_ALIASES = {
    "date": "date", "effective_date": "date", "effective-date": "date",
    "effective date": "date",
    "as_of": "asof", "as-of": "asof", "asof": "asof",
    "region": "region", "branch": "region",
    "legal_entity": "entity", "legal-entity": "entity", "entity": "entity",
    "source_system": "source", "source-system": "source", "source": "source",
}
DIMENSION_ORDER = ("date", "asof", "region", "entity", "source")

# Bracket tags trailing a claim line's evidence bracket, e.g. `[region: JP]`.
DIMENSION_TAG_RE = re.compile(r"\[\s*([A-Za-z][A-Za-z _-]*?)\s*:\s*([^\]]+?)\s*\]")
# Plain `key: value` frontmatter lines, read the same line-oriented way every
# FRONT_* regex in bm_vault_graph.py reads status/type/tags.
FRONTMATTER_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z_-]*):\s*(.+?)\s*$", re.M)

# The first copula-like separator in a claim's prose, splitting it into a
# subject (before) and a value (after). Heuristic: good enough to report a
# candidate pair, never treated as a parse a human should trust unchecked.
COPULA_RE = re.compile(r"\b(?:is|are|was|were|equals)\b", re.IGNORECASE)


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _norm(text):
    """Lowercase, collapsed whitespace, trimmed edge punctuation: the
    comparison key for both a subject and a value, so "The Price" and
    "the price " compare equal, and a trailing period never breaks a match."""
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" .;:,")


def split_subject_value(claim_text):
    """(subject, value) normalized, or (None, None) if no copula is found.

    Splits on the LAST copula match, not the first: see the module
    docstring's SCOPE DIMENSIONS-adjacent paragraph for why splitting on
    the first copula lets an embedded copula in the subject truncate it to
    a fragment two unrelated claims can share."""
    matches = list(COPULA_RE.finditer(claim_text))
    if not matches:
        return None, None
    m = matches[-1]
    subject = _norm(claim_text[:m.start()])
    value = _norm(claim_text[m.end():])
    if not subject or not value:
        return None, None
    return subject, value


def _dims_from_pairs(pairs):
    dims = {}
    for key, value in pairs:
        canon = DIMENSION_ALIASES.get(key.strip().lower())
        if canon:
            dims[canon] = _norm(value)
    return dims


def frontmatter_dimensions(front_text):
    return _dims_from_pairs(FRONTMATTER_FIELD_RE.findall(front_text))


def claim_line_dimensions(trailing_text):
    return _dims_from_pairs(DIMENSION_TAG_RE.findall(trailing_text))


def collect_claims(vault):
    """([claim record], unreadable_count). A record: path (vault-relative),
    text, locator, subject, value, dims (scope dimensions, frontmatter ones
    overridden by any inline claim-line tag naming the same dimension, since
    the inline tag is the more specific of the two). A record exists for
    every claim: line whose text splits on a copula. unreadable_count is
    the number of files an OSError (permission denied, broken symlink, a
    race with a delete) stopped this scan from reading: those files are
    never counted as read, never silently absorbed into a "clean" verdict,
    and the caller reports the count whenever it is nonzero."""
    claims = []
    unreadable = 0
    for path in walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            unreadable += 1
            continue
        rel = os.path.relpath(path, vault)
        front_dims = frontmatter_dimensions(_frontmatter(text))
        for claim_text, locator, trailing in CLAIM_RE.findall(text):
            subject, value = split_subject_value(claim_text.strip())
            if subject is None:
                continue
            dims = dict(front_dims)
            dims.update(claim_line_dimensions(trailing))
            claims.append({
                "path": rel, "text": claim_text.strip(), "locator": locator.strip(),
                "subject": subject, "value": value, "dims": dims,
            })
    return claims, unreadable


def pair_claims(claims):
    """[(claim_a, claim_b)]: every two claims, from different notes, sharing
    a subject whose values disagree. One pair per distinct pair of notes on
    that subject; a note is never paired against itself."""
    pairs = []
    by_subject = {}
    for c in claims:
        by_subject.setdefault(c["subject"], []).append(c)
    for group in by_subject.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a["path"] == b["path"]:
                    continue
                if a["value"] != b["value"]:
                    pairs.append((a, b))
    return pairs


def classify(a, b):
    """("SCOPED", dimension) when a stated dimension differs on both sides;
    ("CONTRADICTION", None) otherwise. Checked in DIMENSION_ORDER so two
    differing dimensions always report the same (first) one, deterministic
    regardless of dict ordering."""
    for dim in DIMENSION_ORDER:
        va = a["dims"].get(dim)
        vb = b["dims"].get(dim)
        if va is not None and vb is not None and va != vb:
            return "SCOPED", dim
    return "CONTRADICTION", None


def scan(vault):
    """(pairs, scoped, contradictions, unreadable). scoped:
    [(a, b, dimension)]. contradictions: [(a, b)]. unreadable: the count of
    files collect_claims could not read, see its own docstring."""
    claims, unreadable = collect_claims(vault)
    pairs = pair_claims(claims)
    scoped = []
    contradictions = []
    for a, b in pairs:
        verdict, dim = classify(a, b)
        if verdict == "SCOPED":
            scoped.append((a, b, dim))
        else:
            contradictions.append((a, b))
    return pairs, scoped, contradictions, unreadable


def cmd_scan(vault):
    pairs, scoped, contradictions, unreadable = scan(vault)
    print("vault: %s" % vault)
    if unreadable:
        print("unreadable files skipped: %d" % unreadable)
    print("claim pairs examined: %d" % len(pairs))
    print("scoped: %d" % len(scoped))
    print("contradictions: %d" % len(contradictions))
    if not pairs:
        print("clean: no disagreeing claim pairs found in the files read")
        return 0
    for a, b, dim in scoped:
        print("SCOPED (%s differs): %s <%s> vs %s <%s>" % (
            dim, a["path"], a["text"], b["path"], b["text"]))
    for a, b in contradictions:
        print("CONTRADICTION (candidate contradicts edge, not minted): "
              "%s <%s> vs %s <%s>" % (a["path"], a["text"], b["path"], b["text"]))
    return 1 if contradictions else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("scan",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_triage: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    return cmd_scan(args.vault)


if __name__ == "__main__":
    sys.exit(main())
