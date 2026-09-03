#!/usr/bin/env python3
"""bm_vault_enrich: the LLM enrichment lane. WBS VB10-04.

WHY THIS EXISTS. A model can draft a description, a tag set or a link
suggestion for a note faster than a human can, and settled decision 17 (D12,
tools/bm_vault_lifecycle.py) already says what a model-generated draft is
worth on arrival: a candidate, never truth. This module is the one door a
machine draft walks through. It invents no new epistemic state, no new
approval flow and no new store: it reuses the SAME 00-Inbox/ landing zone and
`promotion:` frontmatter field tools/bm_vault_intake.py and
tools/bm_vault_lifecycle.py already own, so a machine-drafted note lives
alongside every human-admitted one and is governed by the SAME machine.
Promotion out of candidate is handled ENTIRELY by tools/bm_vault_promotions.py,
called here, never reimplemented: this module records drafts, it does not
approve them.

WHY NOT tools/bm_vault_curate.py's queue, read before writing this. That
module's candidate queue is real, but it is shaped for PAIRS of existing
notes proposed for a relates/supersedes edge, scored by three link-prediction
finders, and stored in an external JSON file OUTSIDE the vault
(~/.claude/bm_vault_curation.json). A single-note draft of a description or a
tag set has no "pair" and no edge to accept, and filing it into that JSON
file would be exactly the second, parallel store this row's own brief warns
against. The vault's own notes, under the vault's own `promotion:` field, are
the ONE store; bm_vault_curate.py's queue is a different, narrower tool for a
different shape of candidate and is left untouched.

VB11-03 ADDED TWO FIELDS, alias and question_form, for the retrieval
enrichment pre-compute lane: a model-drafted alternate name or a
model-drafted question form for a note rides through this SAME flow,
never a second approval surface. Nothing else about the flow changes:
still a candidate on arrival, still named to a drafting model, still
promoted only by tools/bm_vault_promotions.py's own ceremony, and only
a promoted (canonical, clean-record) alias or question-form joins the
retrieval index, at bake (tools/bm_vault_catalog.py), never before.

THE COMMANDS.
  enrich-draft --vault V --note IDENT
               --field description|tags|link|alias|question_form --value TEXT
               --model MODEL_ID [--deny-list PATH]
        Files ONE machine-drafted candidate note into 00-Inbox/, frontmatter
        type: enrichment, promotion: candidate, drafter_kind: machine,
        drafting_model: MODEL_ID, drafted_at: now, target_note: IDENT resolved,
        enrich_field: description|tags|link|alias|question_form. Runs the SAME
        hard gate tools/bm_vault_intake.py runs (bm_vault_intake.hard_gate:
        credential shape, then --deny-list) over --value before a byte is
        written. Refuses, writing nothing, when: --field is not one of the
        six, --model is empty (an unattributed draft corroborates nothing
        and is never filed), --value is empty, IDENT resolves to no note, or
        the hard gate hits.

        VB13-05 ADDED entity_extract, ONE FIELD, plus two optional
        frontmatter fields (prompt_version, confidence) on file_draft, for
        the enrichment gate (tools/bm_vault_enrich_gate.py). Same two-line-
        diff posture VB11-03 already used for alias/question_form: the
        schema-constrained draft gate, the golden fixture set and the
        confidence-ranked queue all live in the new gate module, never
        duplicated here.
  propose-edit  Identical handler and identical gates to enrich-draft, under
        the name the serve surface's one write action uses. The MCP/serve
        surface (tools/bm_vault_serve.py) stays read-mostly in this change;
        this is the single write action such a surface would shell out to,
        exactly the way it already shells out to bm_vault.py recall, so no
        second write path exists to drift from this one.
  list-drafts --vault V --note IDENT [--field F] [--canonical-only]
        Every enrichment draft filed against IDENT. --canonical-only filters
        through bm_vault_lifecycle.counts_as_canonical, the ONE definition of
        canonical this estate owns: a machine-drafted candidate is INVISIBLE
        under --canonical-only until a named human promotes it there via
        tools/bm_vault_promotions.py's own ceremony (promote --to validated,
        then --to canonical, each requiring --by).

PROMOTION ITSELF is never re-implemented here: run
`python3 tools/bm_vault_promotions.py promote --id <draft-id-or-path> --to
validated --by NAME --apply`, then `--to canonical --by NAME --apply`. The
promoter's name and date land in the draft's own `promoted_by`/`promoted_at`
fields by that module, read straight back by list-drafts here.

SELF-ECHO PROTECTION. corroborates(model_a, model_b) is False whenever either
model id is missing or the two are the SAME drafting model: a model's own
second guess about the same note is not independent confirmation of itself.
This is the SAME structural rule tools/bm_vault_intake.py's VB6-06 echo
detection already enforces (a confirmed echo of the same source is excluded
from ever inflating a corroboration signal, see corroboration_count there) --
reused here as the identical rule applied to a drafting model rather than a
recall event, because the two are genuinely different signals (one is "this
text was already served by a real recall", the other is "this text was
already drafted by this same model") and no single function covers both.

Exit 0 clean. Exit 1: a refusal (bad field, missing model, empty value, a
hard-gate hit) -- named, nothing written. Exit 2: NO-DATA, no readable vault
or IDENT resolves to no note. Exit 3: list-drafts found nothing (NO-DATA,
never a silent empty pass, same convention bm_vault_curate.py's cmd_list
already uses). Python 3.9, standard library only, no network.

No em or en dashes anywhere in this file.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_ids as ids            # noqa: E402
import bm_vault_lifecycle as lc       # noqa: E402
import bm_vault_promotions as promo   # noqa: E402
import bm_vault_intake as intake      # noqa: E402

ENRICH_FIELDS = ("description", "tags", "link", "alias", "question_form", "entity_extract")
INBOX = "00-Inbox"
NOTE_TYPE = "enrichment"

_KEY_RE = {
    key: re.compile(r"^%s:\s*(.+?)\s*$" % key, re.M)
    for key in ("type", "target_note", "drafting_model", "drafter_kind", "enrich_field",
                "prompt_version", "confidence")
}


def _read_enrich_meta(text):
    """dict of this module's own frontmatter fields, or None when the note
    carries no frontmatter block at all. A missing individual field reads as
    None, never invented."""
    block, _start, _end = ids.frontmatter(text)
    if block is None:
        return None
    out = {}
    for key, pat in _KEY_RE.items():
        m = pat.search(block)
        out[key] = m.group(1).strip().strip('"').strip("'") if m else None
    return out


def file_draft(vault, note_ident, field, value, model, deny_list=None,
                prompt_version=None, confidence=None):
    """(ok, message, relpath_or_None). Writes at most one note under
    00-Inbox/ when ok is True; writes nothing at all when ok is False.
    message never contains --value or --model text beyond what is already
    reported by the reused hard gate (which itself never echoes a matched
    secret, only its class).

    prompt_version and confidence are OPTIONAL, VB13-05 additions: given,
    each lands as its own frontmatter scalar so tools/bm_vault_enrich_gate.py
    can read them back for the golden-set gate and the confidence-ranked
    queue; omitted (the base VB10-04 CLI's own call shape, unchanged), a
    draft carries neither, exactly as before this row."""
    if field not in ENRICH_FIELDS:
        return False, ("REFUSED: --field must be one of %s, got %r"
                        % ("/".join(ENRICH_FIELDS), field)), None
    if not model or not model.strip():
        return False, ("REFUSED: no drafting model id given; an unattributed "
                        "draft corroborates nothing and is never filed"), None
    if not value or not value.strip():
        return False, "REFUSED: --value is empty; nothing to draft", None
    if not vault or not os.path.isdir(vault):
        return False, "NO-DATA: no readable vault at %r" % vault, None

    target = promo._resolve(vault, note_ident)
    if target is None:
        return False, "NO-DATA: %r resolves to no note" % note_ident, None

    # The SAME hard gate tools/bm_vault_intake.py runs before any admit write:
    # credential_hit first, then --deny-list when given. A hit refuses here
    # exactly as it refuses there, class named, value never echoed.
    ok, reject = intake.hard_gate(value, deny_list)
    if not ok:
        return False, "REFUSED: %s" % reject, None

    taken = set(ids.index(vault)[0])
    note_id = ids.mint(taken)
    drafted_at = intake._now_iso()

    lines = [
        "---",
        "id: %s" % note_id,
        "type: %s" % NOTE_TYPE,
        "status: open",
        "created: %s" % intake._today(),
        "promotion: candidate",
        "drafter_kind: machine",
        "drafting_model: %s" % intake._sanitize_frontmatter_scalar(model),
        # V14.1: the author of record for cmd_promote's separation-of-duties
        # check. This module has no --by; the drafting model is the one
        # identity file_draft already refuses to leave empty (see the guard
        # above), so it is the most defensible principal for this write site.
        "author: %s" % intake._sanitize_frontmatter_scalar(model),
        "drafted_at: %s" % drafted_at,
        "target_note: %s" % intake._sanitize_frontmatter_scalar(target),
        "enrich_field: %s" % field,
    ]
    if prompt_version:
        lines.append("prompt_version: %s" % intake._sanitize_frontmatter_scalar(prompt_version))
    if confidence is not None:
        lines.append("confidence: %s" % confidence)
    lines += [
        "---",
        "",
        "# machine-drafted %s for %s" % (field, target),
        "",
        value.strip(),
        "",
    ]
    text = "\n".join(lines)

    inbox_dir = os.path.join(vault, INBOX)
    os.makedirs(inbox_dir, exist_ok=True)
    slug_base = "enrich-%s-%s" % (field, os.path.splitext(os.path.basename(target))[0])
    slug = re.sub(r"[^a-z0-9]+", "-", slug_base.lower()).strip("-") or "enrich"
    note_path = os.path.join(inbox_dir, "%s-%s.md" % (slug, note_id[-8:]))
    with open(note_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    rel = os.path.relpath(note_path, vault).replace(os.sep, "/")
    return True, ("DRAFTED %s  id=%s  target=%s  field=%s  model=%s"
                  % (rel, note_id, target, field, model)), rel


def list_drafts(vault, target_note, field=None, canonical_only=False):
    """[(relpath, promotion_state, drafting_model, drafter_kind), ...] for
    every enrichment note whose target_note equals target_note (and whose
    enrich_field equals field, when given). canonical_only filters through
    bm_vault_lifecycle.counts_as_canonical -- the one definition of canonical
    this estate owns, never re-decided here -- so a machine-drafted candidate
    stays invisible under this filter until a named human promotes it."""
    out = []
    for path in ids.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("bm_vault_enrich: WARNING, could not read %s (%s); "
                             "excluded from this listing\n" % (path, exc))
            continue
        meta = _read_enrich_meta(text)
        if meta is None or meta.get("type") != NOTE_TYPE:
            continue
        if meta.get("target_note") != target_note:
            continue
        if field and meta.get("enrich_field") != field:
            continue
        state, _record, problems = lc.read_promotion(text)
        if canonical_only and not lc.counts_as_canonical(state, problems):
            continue
        rel = os.path.relpath(path, vault).replace(os.sep, "/")
        out.append((rel, state, meta.get("drafting_model"), meta.get("drafter_kind")))
    return out


def corroborates(model_a, model_b):
    """True only when both drafting models are given AND different: a
    drafting model can never corroborate its own draft. See the module
    docstring's SELF-ECHO PROTECTION section for why this is the VB6-06
    mechanic reapplied rather than a second copy of it."""
    return bool(model_a) and bool(model_b) and model_a != model_b


# -------------------------------------------------------------------- CLI

def cmd_file(args):
    ok, message, _rel = file_draft(args.vault, args.note, args.field, args.value,
                                    args.model, deny_list=args.deny_list)
    print(message)
    if ok:
        return 0
    return 2 if message.startswith("NO-DATA") else 1


def cmd_list(args):
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_enrich: NO-DATA, no readable vault at %r" % args.vault)
        return 2
    target = promo._resolve(args.vault, args.note)
    if target is None:
        print("bm_vault_enrich: NO-DATA, %r resolves to no note" % args.note)
        return 2
    rows = list_drafts(args.vault, target, field=args.field,
                        canonical_only=args.canonical_only)
    if not rows:
        print("NO-DATA: no%s enrichment draft(s) found for %s"
              % (" canonical" if args.canonical_only else "", target))
        return 3
    print("enrichment drafts for %s (%d):" % (target, len(rows)))
    for rel, state, model, kind in rows:
        print("  [%s] %s  drafter=%s  model=%s" % (state, rel, kind, model))
    return 0


def _add_file_args(sp):
    sp.add_argument("--vault", required=True)
    sp.add_argument("--note", required=True, help="target note id or vault-relative path")
    sp.add_argument("--field", default="description", choices=ENRICH_FIELDS)
    sp.add_argument("--value", required=True, help="the drafted text")
    sp.add_argument("--model", required=True, help="the drafting model id; never NO-DATA")
    sp.add_argument("--deny-list", default=None, help="path to a deny-list terms file")


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("enrich-draft", help="file a machine-drafted candidate")
    _add_file_args(pd)

    pe = sub.add_parser("propose-edit",
                         help="the serve surface's one write action; same gates as "
                              "enrich-draft")
    _add_file_args(pe)

    pl = sub.add_parser("list-drafts", help="list enrichment drafts for a note")
    pl.add_argument("--vault", required=True)
    pl.add_argument("--note", required=True)
    pl.add_argument("--field", default=None, choices=ENRICH_FIELDS)
    pl.add_argument("--canonical-only", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command in ("enrich-draft", "propose-edit"):
        return cmd_file(args)
    return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
