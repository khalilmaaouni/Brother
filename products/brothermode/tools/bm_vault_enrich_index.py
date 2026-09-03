#!/usr/bin/env python3
"""bm_vault_enrich_index: the retrieval enrichment pre-compute lane. WBS VB11-03.

THE ROW. Model-drafted aliases and question-forms for a note land as
machine-drafted candidates through the EXISTING enrichment lane
(tools/bm_vault_enrich.py, VB10-04), never a second approval surface.
Promoted (canonical, clean-record) alias/question-form metadata joins the
retrieval index AT BAKE (tools/bm_vault_catalog.py); an unpromoted draft
changes nothing. Lexical-first hit-rate on a fixture query set is measured
before and after a promotion, so the claim is checked, not asserted.

WHY A NEW FILE rather than growing bm_vault_enrich.py further: that module
already carries the full drafting/listing surface for three field kinds
(VB10-04) and its own tests; this row adds a CLI surface (draft-aliases,
measure), a bake-time reader, and a hit-rate tool, none of which are
enrichment-drafting concerns. Only the two new ENRICH_FIELDS entries
(alias, question_form) landed in bm_vault_enrich.py itself, a two-line
diff, because file_draft's field validation lives there and duplicating
it here would be the second write path this estate's own docs warn
against. Everything past drafting -- reading promoted drafts back for
bake, and measuring hit rate -- lives here.

THE COMMANDS.
  draft-aliases --vault V --note IDENT --model MODEL_ID --from-file PATH
        Files one machine-drafted candidate per non-blank, non-comment
        (#) line of PATH, through bm_vault_enrich.file_draft, field=alias
        by default. A line prefixed "Q: " (case sensitive, matching the
        prefix exactly) files that line's remainder as field=question_form
        instead, so one file can draft a mix of aliases and question forms
        in one call. NO live model call: a model would draft the file-file
        content itself in a fuller build; wiring one is out of scope
        tonight, so this command reads already-drafted lines and only
        records the given --model as their attributed drafter, exactly as
        enrich-draft already does for any other field. Deterministic:
        same file in, same drafts out (module names differ, mint() draws a
        fresh id per note, which is the SAME non-determinism enrich-draft
        itself already carries; nothing added here).

  measure --vault V --queries PATH
        A fixture query set (JSON list of {"query": "...",
        "expected_note": "IDENT-or-relpath"}) against the vault AS IT
        CURRENTLY STANDS ON DISK. For each query, a hit is any .md file
        under the vault that (a) names the expected note -- IS that note,
        or wikilinks to its stem, which is exactly how a baked Catalog.md
        entry names the notes it lists -- AND (b) contains every
        significant (len > 2) lowercase token of the query text as a
        substring. Prints "hit-rate: H/N (P%)". NO-DATA (never a pass,
        never a block) when PATH is missing or the query list is empty,
        naming the path.

        WHY THIS PROXY, not tools/bm_vault.py's own SQLite FTS5/anchor/
        dense-embedding recall pipeline: that pipeline's staged
        lexical-then-dense decision depends on an embed machine that may
        or may not be present on the machine running a test, on
        subprocess timing, and on ranking heuristics tuned for a much
        larger real corpus (see bm_vault.py's own module docstring). None
        of that is what this row's own done-check asks for: "a promoted
        alias makes a previously-dense query resolve lexically, measured".
        The claim under test is narrower and mechanical -- does the
        promoted text reach a file that names the note, at bake, in a form
        a plain substring scan finds -- and a second, deterministic,
        subprocess-free scan proves exactly that claim without inheriting
        the larger pipeline's non-determinism. Run BEFORE and AFTER a
        promote-plus-bake step (two invocations; this command reports one
        state each time) to see the delta the row asks for; the workflow
        is the same one tools/test_bm_vault.py already uses for
        before/after CLI comparisons, applied to this tool instead of a
        second copy of that harness.

Exit 0 clean measurement or a clean draft. Exit 1 a REFUSED draft-aliases
line and no draft filed at all. Exit 2 NO-DATA (missing vault, missing or
empty --from-file/--queries, IDENT resolves to no note). Python 3.9,
standard library only, no network.

No em or en dashes anywhere in this file.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_enrich as enrich      # noqa: E402
import bm_vault_ids as ids            # noqa: E402
import bm_vault_lifecycle as lc       # noqa: E402
import bm_vault_promotions as promo   # noqa: E402

QUESTION_PREFIX = "Q: "
INDEXABLE_FIELDS = ("alias", "question_form")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------- draft-aliases

def _parsed_lines(path):
    """[(field, value), ...] from PATH, or None when PATH is unreadable.
    Blank lines and lines starting with # are skipped. A line starting
    with the literal QUESTION_PREFIX files as question_form with the
    prefix stripped; every other non-blank line files as alias, verbatim."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:  # sbe: allow-silent documented contract, this function's own docstring says None means unreadable
        return None
    out = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith(QUESTION_PREFIX):
            out.append(("question_form", line[len(QUESTION_PREFIX):].strip()))
        else:
            out.append(("alias", stripped))
    return out


def draft_aliases(vault, note_ident, model, from_file, deny_list=None):
    """(ok, [message, ...]). ok is True only when at least one line drafted
    and none were refused; a mix of drafted and refused lines still writes
    the drafted ones (file_draft's own per-call gates already decided each
    one independently) but is reported as not-fully-ok so a caller notices
    a partial run rather than reading silence as full success."""
    lines = _parsed_lines(from_file)
    if lines is None:
        return None, ["NO-DATA: cannot read --from-file at %r" % from_file]
    if not lines:
        return None, ["NO-DATA: %r carries no alias or question-form line" % from_file]
    messages = []
    any_ok = False
    any_refused = False
    for field, value in lines:
        ok, message, _rel = enrich.file_draft(vault, note_ident, field, value, model,
                                               deny_list=deny_list)
        messages.append(message)
        if ok:
            any_ok = True
        else:
            any_refused = True
    return (any_ok and not any_refused), messages


# ------------------------------------------------------------ bake integration

def _draft_value_text(path):
    """The drafted value text back out of one enrichment note file, or ""
    when the file carries no frontmatter block (never invented, never a
    crash). Mirrors file_draft's own fixed shape (see bm_vault_enrich.py:
    a "---" fenced frontmatter block, a blank line, a "# machine-drafted
    ..." heading, a blank line, then the value) by splicing off exactly
    those two known lines rather than re-deriving the shape from scratch,
    so the two stay in lockstep by construction."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    _block, _start, end = ids.frontmatter(text)
    if end == -1:
        return ""
    rest = text[end:]
    if rest.startswith("\n---"):
        rest = rest[4:]
    rest = rest.lstrip("\n")
    body_lines = rest.split("\n")
    if body_lines and body_lines[0].startswith("#"):
        body_lines = body_lines[1:]
    return "\n".join(body_lines).strip()


def promoted_terms_for_note(vault, note_relpath):
    """[(enrich_field, value_text, drafting_model), ...] for every alias or
    question-form draft targeting note_relpath that COUNTS AS CANONICAL
    right now (bm_vault_lifecycle.counts_as_canonical: canonical state,
    clean record -- the one definition of promoted this estate owns,
    reused via bm_vault_enrich.list_drafts's own canonical_only filter,
    never re-decided here). An unpromoted candidate, a validated-but-not-
    canonical draft, or a canonical draft with a missing promoted_by is
    exactly as absent from this list as a draft that was never filed.
    Read-only: this never writes anything, so a caller may run it at bake
    time without taking the vault writer lock itself."""
    if not vault or not os.path.isdir(vault):
        return []
    out = []
    for field in INDEXABLE_FIELDS:
        for rel, _state, model, _kind in enrich.list_drafts(
                vault, note_relpath, field=field, canonical_only=True):
            value = _draft_value_text(os.path.join(vault, rel))
            if value:
                out.append((field, value, model))
    return out


def catalog_line_suffix(vault, note_relpath):
    """A trailing string to append to one note's catalog line, or "" when
    the note has no promoted alias or question-form. Kept to plain words
    (no wikilink, no markdown table) so bm_vault_catalog.py's own
    byte-identical-on-repeat-bake contract (see that module's docstring)
    holds: this returns the SAME string for the SAME promoted state every
    time, nothing here depends on wall-clock time."""
    terms = promoted_terms_for_note(vault, note_relpath)
    if not terms:
        return ""
    aliases = [v for f, v, _m in terms if f == "alias"]
    questions = [v for f, v, _m in terms if f == "question_form"]
    parts = []
    if aliases:
        parts.append("aka: %s" % "; ".join(aliases))
    if questions:
        parts.append("asks: %s" % "; ".join(questions))
    return "  (%s)" % "; ".join(parts)


# ------------------------------------------------------------------- measure

def _tokens(text):
    return set(_TOKEN_RE.findall(text.lower()))


def _wikilink_stems(body):
    return {m.split("|")[0].strip() for m in re.findall(r"\[\[([^\]]+)\]\]", body)}


def _names_note(relpath, body, expected_relpath, expected_stem):
    if relpath == expected_relpath:
        return True
    for stem in _wikilink_stems(body):
        if stem == expected_stem or stem.split("/")[-1] == expected_stem.split("/")[-1]:
            return True
    return False


def _resolve_expected(vault, ident):
    hit = promo._resolve(vault, ident)
    return hit


def lexical_hit(vault, query_text, expected_ident):
    """True when some .md file under vault names expected_ident (is it, or
    wikilinks to its stem) AND contains every significant query token as a
    lowercase substring. False (never an exception) when expected_ident
    resolves to no note at all -- an unresolvable expected note can never
    be hit, by construction, not by a caught error hiding a bug."""
    expected_relpath = _resolve_expected(vault, expected_ident)
    if expected_relpath is None:
        return False
    expected_stem = expected_relpath[:-3] if expected_relpath.endswith(".md") else expected_relpath
    want = {t for t in _tokens(query_text) if len(t) > 2}
    if not want:
        return False
    for path in lc.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:  # sbe: allow-silent skip one unreadable vault file while scanning, other files still checked
            continue
        relpath = os.path.relpath(path, vault).replace(os.sep, "/")
        if not _names_note(relpath, body, expected_relpath, expected_stem):
            continue
        low = body.lower()
        if want <= {t for t in _tokens(low)} or all(t in low for t in want):
            return True
    return False


def measure(vault, queries_path):
    """(hits, total, [line, ...]) or (None, None, [NO-DATA line]) when
    queries_path is missing or empty. Never a silent zero-query pass."""
    if not queries_path or not os.path.isfile(queries_path):
        return None, None, ["NO-DATA: no query set at %r" % queries_path]
    try:
        with open(queries_path, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError) as e:
        return None, None, ["NO-DATA: %r is not readable JSON (%s)" % (queries_path, e)]
    if not rows:
        return None, None, ["NO-DATA: %r carries an empty query set" % queries_path]
    hits = 0
    detail = []
    for row in rows:
        q = row.get("query", "")
        note = row.get("expected_note", "")
        hit = lexical_hit(vault, q, note)
        hits += int(hit)
        detail.append("  [%s] %r -> %s" % ("HIT" if hit else "MISS", q, note))
    return hits, len(rows), detail


# -------------------------------------------------------------------- CLI

def cmd_draft_aliases(args):
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_enrich_index: NO-DATA, no readable vault at %r" % args.vault)
        return 2
    ok, messages = draft_aliases(args.vault, args.note, args.model, args.from_file,
                                  deny_list=args.deny_list)
    for m in messages:
        print(m)
    if ok is None:
        return 2
    return 0 if ok else 1


def cmd_measure(args):
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_enrich_index: NO-DATA, no readable vault at %r" % args.vault)
        return 2
    hits, total, detail = measure(args.vault, args.queries)
    for line in detail:
        print(line)
    if hits is None:
        return 2
    pct = (100.0 * hits / total) if total else 0.0
    print("hit-rate: %d/%d (%.0f%%)" % (hits, total, pct))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("draft-aliases",
                         help="file alias/question-form candidates from prepared lines")
    pd.add_argument("--vault", required=True)
    pd.add_argument("--note", required=True, help="target note id or vault-relative path")
    pd.add_argument("--model", required=True, help="the drafting model id; never NO-DATA")
    pd.add_argument("--from-file", required=True,
                     help="prepared lines; one alias per line, 'Q: ' prefix for a "
                          "question form; no live model call (out of scope tonight)")
    pd.add_argument("--deny-list", default=None)

    pm = sub.add_parser("measure",
                         help="lexical-first hit rate over a fixture query set")
    pm.add_argument("--vault", required=True)
    pm.add_argument("--queries", required=True,
                     help="JSON list of {query, expected_note}")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "draft-aliases":
        return cmd_draft_aliases(args)
    return cmd_measure(args)


if __name__ == "__main__":
    sys.exit(main())
