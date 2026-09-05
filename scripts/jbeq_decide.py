#!/usr/bin/env python3
"""JBEQ-MDM decision module: a deterministic engine that decides once a
blind reader has filled out a FACT SHEET, so the engine (not a fresh prompt
read) applies the rules consistently.

WHY THIS EXISTS. The founder's ruling of 2026-09-05 on JBEQ-MDM blind round 4
("Engine decides, model extracts") is that a blind model should only extract
structured facts from a prompt, never apply the rules itself: three blind
rounds against the same rules-as-prose showed a fresh reader collides rules
that look alike (see benchmarks/jbeq/mdm/decision-rules-addendum.md and the
diagnosis this module was built from). This file is the engine half of that
split. It never reads a benchmark seed and never sees an expected answer; it
only takes a fact sheet and a fixed rule table and returns one answer.

THE FACT SHEET. A JSON object per case, built by a BLIND reader from the
prompt text alone (see benchmarks/jbeq/mdm/fact-sheet-schema.json for a
worked example). Required keys, always present (value may be null, "none",
false or an empty list when the fact does not apply to a case; the key
itself must exist so a fact the extractor never considered is NEVER read as
"nothing to see here" by silent default):

  track                str    the case's track label, copied from the prompt
  allowed_answers      list   the case's own allowed-answer list, copied
                               verbatim from the prompt's "許容される回答"
  identifiers          list   [{value, domain, legal_person_type, status}]
                               domain: corporate_number | invoice_number |
                                 internal_sequence | tax_id | unknown
                               legal_person_type: corporation | individual |
                                 unknown
                               status: valid | expired | blank
  stated_relation      str    none | corporate_number_shared | parent_child |
                               role_pair | trade_flow | same_site_only |
                               group_company_code_shared_entity
                               (the last value is an extension of boundary
                               rule 7: two or more company-code-specific
                               records the input states name one external
                               entity)
  stated_difference    str    none | different_legal_entity |
                               different_tenant | contractual_wall |
                               different_identifier_domain
  tenant_boundary       str   none | stated
  evidence_strength     str or null   strong | medium | weak | null
                               null means this case is not a merge candidate
                               at all (no score, no identifier match is being
                               proposed), so the merge ladder never applies.
  evidence_reasons      list  zero or more of: unexplained_conflict,
                               explained_conflict, unvalidated_crosswalk,
                               score_only, missing_identifier
  corroborating_facts   list  facts, in the extractor's own words, that
                               support a blank or missing field without
                               confirming it outright
  blank_fields          list  field names the input states are blank,
                               expired, or otherwise unfilled
  irreversible          bool  the candidate action (a merge, a delete) cannot
                               be undone if wrong
  history_exists        bool  transaction history exists on one or both
                               sides and a person should look before merging
  hierarchy_parents     list  [{type, parent}], type: capital | trade_flow |
                               reporting
  one_to_many_object    bool  one record serves several of the other kind
                               (a shared delivery hub, a consolidation
                               center); extension of boundary rule 5

Two further keys are RESERVED for the survivorship (SV) and temporal (TM)
tracks, documented here because the brief that created this module asks for
them, but UNUSED by decide() today: this module was handed rules 1 to 7 and
A to D, none of which define an SV or TM answer vocabulary, so decide()
honestly returns NO-DATA naming the track unsupported rather than guess at
one. A future rules addendum for those tracks can start from these fields:

  effective_dates        dict   {as_of, candidate_effective_date, conflict}
  source_precedence      dict   {authoritative_source, overriding_source,
                                  override_validity: valid | expired}

THE PRECEDENCE ORDER, fixed and documented once here (decide() below is a
straight-line implementation of this list, checked in this order, first
match wins):

  0. Every required key must be present (see REQUIRED_FIELDS). Missing any
     one is NO-DATA naming that field. This runs before every rule below.
  1. Track SV, TM or RQ (survivorship, temporal, requirements): NO-DATA
     naming the track unsupported. These tracks answer from their own
     vocabulary, which no rule handed to this module defines.
  2. Rule D (hierarchy types independent): 2 or more hierarchy_parents of
     DIFFERENT types, each with a parent named, is not one conflict; answer
     KEEP SEPARATE. This runs before any relation or difference check
     because a multi-type hierarchy is never "the same object needing one
     merge decision" in the first place.
  3. Rule C (identifier-domain and tenant boundary): tenant_boundary
     "stated", or stated_difference one of different_tenant,
     contractual_wall, different_identifier_domain: REJECT MATCH. A numeric
     coincidence across domains or a stated tenant wall is a stated fact
     that the two are not the same or must never link, which boundary rule 2
     and rule 6 both require before REJECT MATCH is available; this is
     always checked ahead of the merge ladder, per the founder's ruling, so
     a strong-looking match never merges past a stated wall.
  4. Boundary rule 2, the generic case: stated_difference ==
     different_legal_entity: REJECT MATCH.
  5. Rule 5 (site-only, one-to-many): stated_relation == same_site_only and
     one_to_many_object: KEEP SEPARATE. A shared address alone is never the
     stated relation boundary rule 1 asks for, and collapsing a one-to-many
     object into one record misattributes every other side's records.
  6. Rule 7 (group company-code records): stated_relation ==
     group_company_code_shared_entity: LINK AS RELATED. An operational
     reason to keep company-code records apart is never a reason to record
     no relation between them.
  7. The merge ladder, rule B refining rule 4 (only when evidence_strength is
     not null, i.e. this case actually proposes a merge or link on evidence):
       evidence_strength == weak: ESCALATE, always, however many reasons are
         present. Irreversibility or history never upgrade weak evidence
         into a merge.
       evidence_strength == strong and evidence_reasons is empty and not
         irreversible and not history_exists: AUTO-MERGE.
       otherwise (medium, or strong with any reason, or irreversible, or
         history_exists): SUGGEST MERGE.
  8. Rule A (ESCALATE vs NO-DATA): blank_fields is non-empty:
       corroborating_facts non-empty: ESCALATE.
       corroborating_facts empty: NO-DATA naming the blank field(s).
  9. Boundary rule 1, the default: stated_relation != "none": LINK AS
     RELATED. stated_relation == "none": KEEP SEPARATE.
  10. Safety net: if the rule table produced an answer the case's own
      allowed_answers does not carry, NO-DATA naming the mismatch rather
      than emit an answer the prompt never offered.

Usage:
  python3 scripts/jbeq_decide.py decide <fact-sheets.json> [--out answers.json]
      [--decisions decisions.jsonl]
  python3 scripts/jbeq_decide.py validate <fact-sheets.json>

<fact-sheets.json> is a JSON object of {"CASE-ID": {fact sheet}, ...}.

A mutation control for tests: set JBEQ_DECIDE_DISABLE_RULES=1 in the
environment to make decide() ignore the rule table entirely and always
return NO-DATA with rule_fired="rules-disabled-for-test". This exists only
so a test suite can prove it is testing something real: with the rule table
disabled, any test that asserts a specific non-NO-DATA answer must fail.
"""
import argparse
import json
import os
import sys

REQUIRED_FIELDS = [
    "track",
    "allowed_answers",
    "identifiers",
    "stated_relation",
    "stated_difference",
    "tenant_boundary",
    "evidence_strength",
    "evidence_reasons",
    "corroborating_facts",
    "blank_fields",
    "irreversible",
    "history_exists",
    "hierarchy_parents",
    "one_to_many_object",
]

# The three tracks whose answers come from a vocabulary this module was never
# handed. Named honestly rather than guessed at. See the module docstring.
UNSUPPORTED_TRACKS = {"survivorship", "temporal", "requirements"}

EXIT_OK = 0
EXIT_NOT_DECIDED = 1
EXIT_NODATA = 3


def _missing_fields(sheet):
    return [f for f in REQUIRED_FIELDS if f not in sheet]


def decide(fact_sheet):
    """Apply the fixed rule table to one fact sheet. Returns
    {"answer": str, "rule_fired": str, "why": str}. Never raises on a
    malformed sheet: a missing field is reported as NO-DATA, never a guess.
    """
    if os.environ.get("JBEQ_DECIDE_DISABLE_RULES") == "1":
        return {
            "answer": "NO-DATA",
            "rule_fired": "rules-disabled-for-test",
            "why": "JBEQ_DECIDE_DISABLE_RULES=1: the rule table was skipped",
        }

    missing = _missing_fields(fact_sheet)
    if missing:
        return {
            "answer": "NO-DATA",
            "rule_fired": "validation",
            "why": "missing required field(s): %s" % ", ".join(missing),
        }

    track = fact_sheet["track"]
    allowed = fact_sheet.get("allowed_answers") or []

    def finish(answer, rule_fired, why):
        if allowed and answer not in allowed:
            return {
                "answer": "NO-DATA",
                "rule_fired": "safety-net",
                "why": ("rule %s chose %r but the case's own allowed_answers "
                        "does not carry it: %s" % (rule_fired, answer, allowed)),
            }
        return {"answer": answer, "rule_fired": rule_fired, "why": why}

    if track in UNSUPPORTED_TRACKS:
        return finish(
            "NO-DATA", "track-unsupported",
            "track %r answers from its own vocabulary; no rule handed to "
            "this module defines it" % track,
        )

    hierarchy_parents = fact_sheet["hierarchy_parents"] or []
    distinct_types = {p["type"] for p in hierarchy_parents if p.get("parent")}
    if len(distinct_types) >= 2:
        return finish(
            "KEEP SEPARATE", "D",
            "hierarchy_parents states %d distinct types (%s), each valid; "
            "hierarchy types are independent, not one conflict"
            % (len(distinct_types), ", ".join(sorted(distinct_types))),
        )

    stated_difference = fact_sheet["stated_difference"]
    tenant_boundary = fact_sheet["tenant_boundary"]
    if tenant_boundary == "stated" or stated_difference in (
        "different_tenant", "contractual_wall", "different_identifier_domain",
    ):
        return finish(
            "REJECT MATCH", "C",
            "tenant_boundary=%r, stated_difference=%r states the two are "
            "not the same or must never link" % (tenant_boundary, stated_difference),
        )

    if stated_difference == "different_legal_entity":
        return finish(
            "REJECT MATCH", "2",
            "stated_difference=different_legal_entity refutes the proposed "
            "identity outright",
        )

    stated_relation = fact_sheet["stated_relation"]
    if stated_relation == "same_site_only" and fact_sheet["one_to_many_object"]:
        return finish(
            "KEEP SEPARATE", "5",
            "only a shared address is stated and one side is a one-to-many "
            "object; a shared address alone is never the stated relation",
        )

    if stated_relation == "group_company_code_shared_entity":
        return finish(
            "LINK AS RELATED", "7",
            "records are kept apart by company code operationally, but both "
            "name the same external entity, which is itself the relation",
        )

    evidence_strength = fact_sheet["evidence_strength"]
    if evidence_strength is not None:
        reasons = fact_sheet["evidence_reasons"] or []
        irreversible = bool(fact_sheet["irreversible"])
        history_exists = bool(fact_sheet["history_exists"])
        if evidence_strength == "weak":
            return finish(
                "ESCALATE", "B",
                "evidence_strength=weak; irreversibility or history never "
                "upgrade weak evidence into a merge",
            )
        no_reason = (not reasons and not irreversible and not history_exists)
        if evidence_strength == "strong" and no_reason:
            return finish(
                "AUTO-MERGE", "B",
                "evidence_strength=strong with no conflicting or "
                "confirmation reason",
            )
        return finish(
            "SUGGEST MERGE", "B",
            "evidence_strength=%s, reasons=%s, irreversible=%s, "
            "history_exists=%s: enough to merge but a person should confirm"
            % (evidence_strength, reasons, irreversible, history_exists),
        )

    blank_fields = fact_sheet["blank_fields"] or []
    if blank_fields:
        corroborating = fact_sheet["corroborating_facts"] or []
        if corroborating:
            return finish(
                "ESCALATE", "A",
                "blank field(s) %s but corroborating fact(s) %s support "
                "without confirming" % (blank_fields, corroborating),
            )
        return finish(
            "NO-DATA", "A",
            "blank field(s) %s and nothing corroborates them" % blank_fields,
        )

    if stated_relation != "none":
        return finish(
            "LINK AS RELATED", "1",
            "stated_relation=%r: the input itself states a relation" % stated_relation,
        )
    return finish(
        "KEEP SEPARATE", "1",
        "stated_relation=none: the input states no relation",
    )


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)


def cmd_validate(args):
    sheets, err = _load_json(args.fact_sheets)
    if err:
        sys.stderr.write("NO-DATA: %s\n" % err)
        return EXIT_NODATA
    if not isinstance(sheets, dict):
        sys.stderr.write("NO-DATA: fact sheet file must be an object of "
                         "{case id: fact sheet}, got %s\n" % type(sheets).__name__)
        return EXIT_NODATA
    bad = {}
    for case_id, sheet in sheets.items():
        if not isinstance(sheet, dict):
            bad[case_id] = ["fact sheet is not an object"]
            continue
        missing = _missing_fields(sheet)
        if missing:
            bad[case_id] = missing
    if bad:
        for case_id in sorted(bad):
            print("REFUSED %s: missing %s" % (case_id, ", ".join(bad[case_id])))
        print("validate: %d of %d fact sheet(s) refused" % (len(bad), len(sheets)))
        return EXIT_NOT_DECIDED
    print("validate: %d fact sheet(s) OK" % len(sheets))
    return EXIT_OK


def cmd_decide(args):
    sheets, err = _load_json(args.fact_sheets)
    if err:
        sys.stderr.write("NO-DATA: %s\n" % err)
        return EXIT_NODATA
    if not isinstance(sheets, dict):
        sys.stderr.write("NO-DATA: fact sheet file must be an object of "
                         "{case id: fact sheet}, got %s\n" % type(sheets).__name__)
        return EXIT_NODATA

    answers = {}
    decisions = []
    for case_id in sheets:
        sheet = sheets[case_id]
        if not isinstance(sheet, dict):
            result = {"answer": "NO-DATA", "rule_fired": "validation",
                      "why": "fact sheet is not an object"}
        else:
            result = decide(sheet)
        answers[case_id] = result["answer"]
        decisions.append({"case_id": case_id, "answer": result["answer"],
                          "rule_fired": result["rule_fired"], "why": result["why"]})

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(answers, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("NO-DATA: cannot write %s: %s\n" % (args.out, exc))
        return EXIT_NODATA

    try:
        with open(args.decisions, "w", encoding="utf-8") as fh:
            for row in decisions:
                fh.write(json.dumps(row, ensure_ascii=False))
                fh.write("\n")
    except OSError as exc:
        sys.stderr.write("NO-DATA: cannot write %s: %s\n" % (args.decisions, exc))
        return EXIT_NODATA

    print("wrote %d answer(s) to %s and %s" % (len(answers), args.out, args.decisions))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd")

    d = sub.add_parser("decide", help="apply the rule table to a fact sheet file")
    d.add_argument("fact_sheets")
    d.add_argument("--out", default="answers.json")
    d.add_argument("--decisions", default="decisions.jsonl")
    d.set_defaults(func=cmd_decide)

    v = sub.add_parser("validate", help="refuse a fact sheet file missing required fields")
    v.add_argument("fact_sheets")
    v.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_NODATA
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
