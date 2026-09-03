#!/usr/bin/env python3
"""bm_vault_enrich_gate: the enrichment draft gate and golden-set evals. WBS VB13-05.

THE ROW. Rides ENTIRELY on the VB10-04 enrichment lane (tools/bm_vault_enrich.py),
never a second write path: a draft still lands under 00-Inbox/ as a candidate
through bm_vault_enrich.file_draft, this module only decides, IN CODE, whether a
draft is allowed to reach that writer at all, and adds the two extra frontmatter
scalars (prompt_version, confidence) that writer now optionally accepts (VB13-05's
own two-line seam there). Four pieces:

  1. THE SCHEMA GATE. A declared extraction schema (EXTRACTION_SCHEMA below):
     enum-bound values, value-range checks, all in code, never left to a model's
     own judgment. A schema-violating draft is refused AT FILING TIME, naming the
     violation, before bm_vault_enrich.file_draft is ever called -- nothing is
     written. Every draft that DOES reach the writer carries model id (the
     existing drafting_model field), prompt version and confidence.
  2. THE GOLDEN SET (tools/fixtures/enrich-goldens.json). Built from REAL GLEIF
     LEI records already fetched into tools/fixtures/gleif-toyota-group.json
     (VB13-07): real legal names (including one Japanese-script one), real
     ISO country codes, real GLEIF entity_status/registration_status values,
     so a case failing means the extraction logic actually regressed against
     real data, never against an invented string nobody would ever see.
     `goldens --prompt-version X` runs extract() (below) over every golden
     input and refuses to bless X when any case's output does not equal its
     expected record. THE BUMP IS THE RUN: a prompt-version is blessed only by
     a passing run; a failing run leaves whatever version was blessed before
     untouched, in a small json-store state file (default
     ~/.claude/bm_vault_enrich_gate_state.json, --state to override), the same
     outside-the-vault json-store shape tools/bm_vault_curate.py and
     tools/bm_vault_attribute_provenance.py already use.
  3. THE CONFIDENCE-RANKED QUEUE. queue_drafts() walks every enrichment draft
     (bm_vault_enrich.NOTE_TYPE) exactly the way bm_vault_enrich.list_drafts
     already walks one note's drafts, but across the whole vault and carrying
     confidence back out, sorted ascending (least confident first: the one a
     human should look at soonest). A draft with no confidence sorts last,
     never ahead of a scored one.
  4. PROMOTION CARRY-THROUGH. carry_provenance() reads one ALREADY-PROMOTED
     (canonical, clean-record; bm_vault_lifecycle.counts_as_canonical, the one
     definition of promoted this estate owns) draft's model, prompt_version and
     confidence, and records them via bm_vault_attribute_provenance.set_record
     -- that module's OWN writer, never reimplemented here. prompt_version has
     no dedicated column in that store, so it rides in `source` alongside the
     model, in the SAME "machine:<drafting_model>" convention that module's own
     docstring already names for the enrichment lane, extended by one "#<ver>"
     suffix rather than a second, divergent field.

WHY entity_extract, ONE NEW ENRICH_FIELD. The five VB10-04/VB11-03 fields
(description, tags, link, alias, question_form) are all free text; this row's
"declared extraction schema" needs a STRUCTURED value (a JSON object of named
attributes) to validate against enum/range rules in code, and that shape does
not fit any of the five without overloading one of them into something it was
never meant to hold. Adding a sixth field is the same one-line addition to
ENRICH_FIELDS VB11-03 already made twice for alias/question_form; the value
text filed under it is JSON (sort_keys=True, ensure_ascii=False so a Japanese
legal name round-trips as itself, not an escape sequence).

Exit 0 clean (a filed draft, a passing/blessing goldens run, a printed queue,
a carried-through provenance record). Exit 1 a refusal (schema violation, bad
confidence, empty prompt version, a failing golden set, an unpromoted draft
asked to carry through). Exit 2 NO-DATA (unreadable vault, IDENT resolves to
no note, missing/unreadable/empty golden fixture, empty queue, unreadable
gate-state or provenance store). Python 3.9, standard library only, no
network.

No em or en dashes anywhere in this file.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_attribute_provenance as attrprov  # noqa: E402
import bm_vault_enrich as enrich                  # noqa: E402
import bm_vault_ids as ids                        # noqa: E402
import bm_vault_lifecycle as lc                   # noqa: E402
import bm_vault_promotions as promo               # noqa: E402

ENTITY_EXTRACT_FIELD = "entity_extract"
DEFAULT_STATE = os.path.expanduser("~/.claude/bm_vault_enrich_gate_state.json")

# ---------------------------------------------------- the declared schema
#
# Enum values below are bound to what is ACTUALLY OBSERVED in
# tools/fixtures/gleif-toyota-group.json (fetched 2026-08-30 from the real
# GLEIF LEI API, CC0), never invented: a schema violation is therefore a real
# deviation from the golden data's own vocabulary. GLEIF's entity_status field
# is documented to carry a second value (INACTIVE) that never appears in this
# fixture; it is deliberately left OUT of the enum below rather than added on
# unverified recall, per this estate's own research rule.
COUNTRY_ENUM = ("GB", "US", "ZA", "PH", "IN", "JP", "DE", "AU", "NL", "KR")
ENTITY_STATUS_ENUM = ("ACTIVE",)
REGISTRATION_STATUS_ENUM = ("ISSUED", "LAPSED")
CONFIDENCE_MIN, CONFIDENCE_MAX = 0.0, 1.0

EXTRACTION_SCHEMA = {
    "legal_name": {"kind": "text", "min_len": 1},
    "country": {"kind": "enum", "values": COUNTRY_ENUM},
    "entity_status": {"kind": "enum", "values": ENTITY_STATUS_ENUM},
    "registration_status": {"kind": "enum", "values": REGISTRATION_STATUS_ENUM},
}


def validate_extraction(record):
    """(ok, violation_or_None). Every declared attribute must be present and
    conform; extra keys beyond the declared schema are tolerated (this gate
    enforces what IS declared, it does not forbid what is not)."""
    if not isinstance(record, dict):
        return False, "extraction record must be a JSON object, got %s" % type(record).__name__
    for attr, spec in EXTRACTION_SCHEMA.items():
        if attr not in record:
            return False, "missing required attribute %r" % attr
        value = record[attr]
        if spec["kind"] == "text":
            if not isinstance(value, str) or len(value.strip()) < spec["min_len"]:
                return False, "attribute %r must be non-empty text, got %r" % (attr, value)
        elif spec["kind"] == "enum":
            if value not in spec["values"]:
                return False, ("attribute %r=%r is out of the declared enum (%s)"
                                % (attr, value, "/".join(spec["values"])))
    return True, None


def validate_confidence(confidence):
    """(ok, violation_or_None). Required (an unattributed confidence gates
    nothing and cannot rank a review queue) and range-checked in code."""
    if confidence is None:
        return False, "confidence is required on every draft"
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return False, "confidence must be numeric, got %r" % (confidence,)
    if not (CONFIDENCE_MIN <= value <= CONFIDENCE_MAX):
        return False, ("confidence %s is out of the declared range [%s, %s]"
                        % (value, CONFIDENCE_MIN, CONFIDENCE_MAX))
    return True, None


def extract(raw):
    """The extraction function under golden-set test: a deterministic,
    subprocess-free, network-free field rename/pass-through over one
    GLEIF-shaped raw record. Stands in for 'run the prompt': the golden set
    gates THIS function's output exactly as it would gate a live model's,
    without this estate's own tests depending on a model or a network call."""
    return {
        "legal_name": raw.get("legalName", ""),
        "country": raw.get("country", ""),
        "entity_status": raw.get("entity_status", ""),
        "registration_status": raw.get("registration_status", ""),
    }


# --------------------------------------------------------------- the draft gate

def file_extraction_draft(vault, note_ident, record, model, prompt_version, confidence,
                           deny_list=None):
    """(ok, message, relpath_or_None). Refuses at filing -- writes nothing --
    when the record violates the declared schema, confidence is missing or
    out of range, or prompt_version is empty. Only a schema-conforming, fully
    attributed draft ever reaches bm_vault_enrich.file_draft, the one writer
    this module rides on."""
    ok, violation = validate_extraction(record)
    if not ok:
        return False, "REFUSED: schema violation, %s" % violation, None
    ok, violation = validate_confidence(confidence)
    if not ok:
        return False, "REFUSED: %s" % violation, None
    if not prompt_version or not str(prompt_version).strip():
        return False, ("REFUSED: --prompt-version is empty; an unversioned draft "
                        "cannot be gated by any golden set"), None
    value_text = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return enrich.file_draft(vault, note_ident, ENTITY_EXTRACT_FIELD, value_text, model,
                              deny_list=deny_list, prompt_version=str(prompt_version),
                              confidence=float(confidence))


# ------------------------------------------------------------------ the goldens

def run_goldens(goldens_path):
    """(ok, [line, ...]). ok is None (never True/False) on NO-DATA: a missing
    file, unreadable JSON, or an empty case list -- never a silent pass."""
    if not goldens_path or not os.path.isfile(goldens_path):
        return None, ["NO-DATA: no golden fixture at %r" % goldens_path]
    try:
        with open(goldens_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return None, ["NO-DATA: %r is not readable JSON (%s)" % (goldens_path, e)]
    cases = data.get("cases") if isinstance(data, dict) else None
    if not cases:
        return None, ["NO-DATA: %r carries no golden case(s)" % goldens_path]
    lines = []
    all_ok = True
    for case in cases:
        cid = case.get("id", "?")
        got = extract(case.get("input", {}))
        expected = case.get("expected", {})
        if got == expected:
            lines.append("  [PASS] %s" % cid)
        else:
            all_ok = False
            lines.append("  [FAIL] %s  got=%s expected=%s" % (cid, got, expected))
    return all_ok, lines


def load_state(path):
    """The gate-state dict, or a fresh empty one when no file exists yet.
    Raises RuntimeError (never a silent empty state) on a corrupt file."""
    if not os.path.isfile(path):
        return {"blessed_prompt_version": None, "blessed_at": None, "goldens": None}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise RuntimeError("cannot read gate state %s: %s" % (path, e))
    if not isinstance(data, dict):
        raise RuntimeError("gate state %s is not a JSON object" % path)
    return data


def save_state(path, data):
    """Atomic write: temp file, then os.replace, the same technique every
    other json-store in this estate already uses."""
    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def bump_prompt_version(goldens_path, prompt_version, state_path):
    """(ok, [line, ...]). ok is None on NO-DATA (propagated from run_goldens
    or an unreadable state file), False when the golden set fails (the bump
    is REFUSED and the state file is left completely untouched, so whatever
    version was blessed before stays blessed), True when it passes (the new
    version is recorded as blessed)."""
    if not prompt_version or not str(prompt_version).strip():
        return False, ["REFUSED: --prompt-version is empty"]
    ok, lines = run_goldens(goldens_path)
    if ok is None:
        return None, lines
    if not ok:
        return False, lines + [
            "REFUSED: prompt-version bump to %r refused; the golden set has at "
            "least one failing case, blessed version unchanged" % prompt_version]
    try:
        state = load_state(state_path)
    except RuntimeError as e:
        return None, ["NO-DATA: %s" % e]
    state["blessed_prompt_version"] = str(prompt_version)
    state["blessed_at"] = enrich.intake._now_iso()
    state["goldens"] = goldens_path
    save_state(state_path, state)
    return True, lines + ["BLESSED: prompt-version %r recorded as passing the "
                           "golden set" % prompt_version]


# --------------------------------------------------------------- the review queue

def queue_drafts(vault, note_ident=None, field=None):
    """[{...}, ...] every enrichment draft (optionally narrowed to one
    resolved note and/or one field), confidence ASCENDING (least confident
    first, for human attention); a draft with no confidence sorts last.
    Returns None when note_ident is given but resolves to no note, mirroring
    bm_vault_enrich's own resolution contract rather than a second one."""
    target = None
    if note_ident:
        target = promo._resolve(vault, note_ident)
        if target is None:
            return None
    rows = []
    for path in ids.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent skip one unreadable vault file while scanning, other files still checked
            continue
        meta = enrich._read_enrich_meta(text)
        if meta is None or meta.get("type") != enrich.NOTE_TYPE:
            continue
        if target is not None and meta.get("target_note") != target:
            continue
        if field and meta.get("enrich_field") != field:
            continue
        state, _record, _problems = lc.read_promotion(text)
        conf_raw = meta.get("confidence")
        try:
            conf = float(conf_raw) if conf_raw is not None else None
        except ValueError:
            conf = None
        rel = os.path.relpath(path, vault).replace(os.sep, "/")
        rows.append({"rel": rel, "state": state, "model": meta.get("drafting_model"),
                     "prompt_version": meta.get("prompt_version"), "confidence": conf,
                     "target_note": meta.get("target_note"), "field": meta.get("enrich_field")})
    rows.sort(key=lambda r: (r["confidence"] is None,
                              r["confidence"] if r["confidence"] is not None else 0.0))
    return rows


# --------------------------------------------------------- promotion carry-through

def carry_provenance(vault, draft_relpath, store_path=None):
    """(ok, message, record_or_None). Refuses when the draft is not YET
    promoted (bm_vault_lifecycle.counts_as_canonical): nothing to carry
    through until a human actually promotes it. Writes through
    bm_vault_attribute_provenance.set_record, that module's own writer,
    status "unverified" (promotion moves lifecycle state, it does not
    verify the drafted value's correctness)."""
    path = os.path.join(vault, draft_relpath)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False, "NO-DATA: cannot read draft at %r" % draft_relpath, None
    meta = enrich._read_enrich_meta(text)
    if meta is None or meta.get("type") != enrich.NOTE_TYPE:
        return False, "NO-DATA: %r is not an enrichment draft" % draft_relpath, None
    state, _record, problems = lc.read_promotion(text)
    if not lc.counts_as_canonical(state, problems):
        return False, ("REFUSED: %r is not promoted (state=%s); nothing to carry "
                        "into provenance until it is" % (draft_relpath, state)), None
    target = meta.get("target_note")
    field = meta.get("enrich_field")
    model = meta.get("drafting_model") or "unknown"
    prompt_version = meta.get("prompt_version") or "unversioned"
    conf_raw = meta.get("confidence")
    confidence = None
    if conf_raw is not None:
        try:
            confidence = float(conf_raw)
        except ValueError:
            confidence = None
    # Same "machine:<drafting_model>" convention bm_vault_attribute_provenance.py's
    # own docstring already names for this lane; "#<prompt_version>" rides beside
    # it because that store has no dedicated prompt_version column of its own.
    source = "machine:%s#%s" % (model, prompt_version)
    store_path = store_path or attrprov.DEFAULT_STORE
    return attrprov.set_record(vault, store_path, target, field, source, "unverified",
                                confidence=confidence)


# -------------------------------------------------------------------- CLI

def cmd_file(args):
    if args.record_file:
        try:
            with open(args.record_file, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError) as e:
            print("NO-DATA: cannot read --record-file at %r (%s)" % (args.record_file, e))
            return 2
    elif args.record is not None:
        try:
            record = json.loads(args.record)
        except ValueError as e:
            print("REFUSED: --record is not valid JSON (%s)" % e)
            return 1
    else:
        print("REFUSED: one of --record or --record-file is required")
        return 1
    ok, message, _rel = file_extraction_draft(
        args.vault, args.note, record, args.model, args.prompt_version, args.confidence,
        deny_list=args.deny_list)
    print(message)
    if ok:
        return 0
    return 2 if message.startswith("NO-DATA") else 1


def cmd_goldens(args):
    ok, lines = bump_prompt_version(args.goldens, args.prompt_version, args.state)
    for line in lines:
        print(line)
    if ok is None:
        return 2
    return 0 if ok else 1


def cmd_queue(args):
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_enrich_gate: NO-DATA, no readable vault at %r" % args.vault)
        return 2
    rows = queue_drafts(args.vault, note_ident=args.note, field=args.field)
    if rows is None:
        print("bm_vault_enrich_gate: NO-DATA, %r resolves to no note" % args.note)
        return 2
    if not rows:
        print("NO-DATA: no enrichment draft(s) found")
        return 2
    print("review queue, confidence ascending (%d):" % len(rows))
    for r in rows:
        conf_disp = "%.3f" % r["confidence"] if r["confidence"] is not None else "NO-DATA"
        print("  confidence=%s  [%s]  %s  field=%s  model=%s  prompt_version=%s"
              % (conf_disp, r["state"], r["rel"], r["field"], r["model"], r["prompt_version"]))
    return 0


def cmd_carry(args):
    ok, message, _record = carry_provenance(args.vault, args.draft, store_path=args.store)
    print(message)
    if ok:
        return 0
    return 2 if message.startswith("NO-DATA") else 1


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("file", help="schema-gate and file one entity_extract draft")
    pf.add_argument("--vault", required=True)
    pf.add_argument("--note", required=True)
    pf.add_argument("--model", required=True)
    pf.add_argument("--prompt-version", dest="prompt_version", required=True)
    pf.add_argument("--confidence", type=float, required=True)
    pf.add_argument("--record", default=None, help="the extraction record, as a JSON object")
    pf.add_argument("--record-file", dest="record_file", default=None,
                     help="path to a JSON file holding the extraction record")
    pf.add_argument("--deny-list", dest="deny_list", default=None)

    pg = sub.add_parser("goldens", help="run the golden set; bless a passing prompt version")
    pg.add_argument("--goldens", required=True)
    pg.add_argument("--prompt-version", dest="prompt_version", required=True)
    pg.add_argument("--state", default=DEFAULT_STATE)

    pq = sub.add_parser("queue", help="the confidence-ranked review queue")
    pq.add_argument("--vault", required=True)
    pq.add_argument("--note", default=None)
    pq.add_argument("--field", default=None)

    pc = sub.add_parser("carry-provenance",
                         help="carry a promoted draft's model/prompt_version/confidence "
                              "into per-attribute provenance")
    pc.add_argument("--vault", required=True)
    pc.add_argument("--draft", required=True, help="the draft's vault-relative path")
    pc.add_argument("--store", default=None)

    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return {"file": cmd_file, "goldens": cmd_goldens, "queue": cmd_queue,
            "carry-provenance": cmd_carry}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
