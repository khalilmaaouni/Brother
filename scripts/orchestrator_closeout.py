#!/usr/bin/env python3
"""ORCH-14 of the 1.0.20 orchestration control plane: the morning closeout.

WHO READS THIS. A person who was asleep while the run happened. They saw
none of it: not the dispatches, not the repairs, not which check almost
passed. Everything they will decide from is this one document. That is the
constraint every choice below follows from: never print a fact this module
did not itself count from run_state, never let a heading stand in for a
value, and never let the word "green" appear over a gap in the evidence.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE: NEVER PRINT AN OVERALL GREEN
WHILE REQUIRED EVIDENCE IS MISSING. A NO-DATA against a required check is
not a pass with an asterisk, it is the one thing that must turn the whole
page away from green, because a false green is read once and then nobody
looks again. This mirrors orchestrator_invariants.may_proceed()'s own
verdict-versus-obligation table exactly, and this module imports that
function rather than re-deciding the same question with different words.

THE BAD STATE A GREEN RUN WOULD ALSO PASS: a closeout that prints every
required heading (OUTCOME, WORK, THROUGHPUT, EVIDENCE, DECISIONS, PROBLEMS)
with the right labels, but with counts that never actually vary with the
input: zeroes, or the same three PASS/FAIL/NO-DATA numbers regardless of
what happened last night. That document would pass any test that only
greps for section headers. Defended against here by test_mutation_*: three
tests that remove or alter one true fact from a realistic fixture and
assert the rendered output changes, never merely that a heading exists.

WHAT IS REUSED, NOT REBUILT. scripts/brother_run.py already owns the
delivery report (build_report) and the on-disk receipt (_write_receipt);
scripts/receipt_door.py already owns turning a Work document and a claim
store into per-unit receipts (receipts_for). When `run_state["delivery_
evidence"]` supplies a real Work document, claim store and revision range,
this module calls exactly those three functions to produce the "delivery
report" sub-block of EVIDENCE and, when a receipt_run_dir is also given, a
persisted receipt.json beside it. It never re-parses a claim, never
re-derives a verdict from an exit code: brother_run._verdict_for and
receipt_door.receipts_for already answer that question and this module
quotes their answer. The four REQUIRED EVIDENCE COUNTS this module's own
headline rule is built on (required PASS, required FAIL, required NO-DATA,
optional NO-DATA) are a different question that brother_run's Work-document
receipts do not carry an obligation for at all (grep confirms neither
brother_run.py nor evidence_obligation.py ever key a row by
"evidence_obligation"): those four counts come straight from `run_state
["evidence_checks"]`, this run's own ledger of gate verdicts, each already
tagged with the obligation orchestrator_invariants.EVIDENCE_OBLIGATIONS
names. Reusing brother_run for a fact it does not carry would mean
inventing the missing tag on its behalf, which is exactly the kind of
guessed value this whole unit exists to refuse.

CONTINGENCY, how this fails and which direction. close_out() computes and
validates every section entirely in memory before touching disk; the first
sign of unreadable, missing or out-of-vocabulary input raises CloseoutError
and writes NOTHING, rather than leaving a half-written MORNING-HANDOFF.md
that reads as a finished handover. The one exception is the reused
brother_run/receipt_door delivery-report enrichment: that sub-block is
supporting detail, never load-bearing for the four required counts or the
overall verdict, so a failure inside it (a broken Work document, a target
directory that no longer exists) is caught narrowly and recorded as its own
NO-DATA line rather than taking down the whole closeout a stranger is
waiting to read. OPERATOR RECOVERY: a CloseoutError names the exact missing
key, unknown value or unfinished unit with no next_action; fix run_state
(or the upstream code that built it) and call close_out() again, from
scratch, on a corrected run_state. Never patch the output files by hand.

Imports orchestrator_invariants for TASK_STATES, TERMINAL_STATES,
is_terminal, VERDICTS, EVIDENCE_OBLIGATIONS and may_proceed: every one of
those vocabularies is defined there once, per ORCH-1020-WORKER-CONTRACT.md,
and this module never restates a member list of any of them.

Python 3.9 floor, standard library only, no network, no clock reads except
the one explicit `now` parameter (defaulted to the real clock only when a
caller omits it; every test supplies its own fixed string).
"""

import datetime
import json
import os

import orchestrator_invariants as invariants

NODATA = "NO-DATA"

#: docs/plan/runs/<run-id>/ file names, named once so both the writer and
#: any reader of this module agree on the exact two artifacts the WBS row
#: for this unit requires.
MORNING_HANDOFF_FILENAME = "MORNING-HANDOFF.md"
FINAL_STATE_DIRNAME = "orchestration"
FINAL_STATE_FILENAME = "final-state.json"

#: The four WORK buckets the WBS row names explicitly. A unit landing in
#: none of these (a state this module still calls terminal, such as
#: CANCELLED) is counted too, under _OTHER_TERMINAL_LABEL, so the WORK
#: section's own total always foots against len(units) -- an unlabelled
#: terminal state must never simply vanish from the count.
_NAMED_WORK_STATES = ("DONE", "PARKED", "EXHAUSTED", "AWAITING-HUMAN")
_OTHER_TERMINAL_LABEL = "OTHER-TERMINAL"

#: evidence_checks obligations this module treats as "required": anything
#: orchestrator_invariants.EVIDENCE_OBLIGATIONS names except OPTIONAL. Not
#: re-typed from evidence_obligation.py's LEVELS (that would be a second
#: copy of the same list); derived from the one already-imported set.
_REQUIRED_OBLIGATIONS = frozenset(
    invariants.EVIDENCE_OBLIGATIONS - frozenset(("OPTIONAL",)))

_THROUGHPUT_FIELDS = (
    "successful_integrations", "repair_attempts", "replans", "handoffs",
    "provider_fallbacks", "cross_reviews",
)


class CloseoutError(Exception):
    """run_state could not be turned into a closeout: a required field is
    missing, a value falls outside the vocabulary orchestrator_invariants
    defines, or a non-DONE unit names no next_action. The message always
    names the exact field, unit id or value at fault, never just "invalid
    run_state": see the module docstring's CONTINGENCY section for what an
    operator does with it."""


class CloseoutResult:
    """What close_out() actually wrote, and the counted facts a caller (or
    a test) can assert on directly instead of re-parsing the rendered
    files. `final_state` is exactly the dict serialised to
    final-state.json; `overall_verdict` is "GREEN" or "RED", never a third
    value, per this module's one rule. `receipt_path` is None whenever no
    delivery_evidence/receipt_run_dir was supplied, or whenever the reused
    brother_run/receipt_door call could not produce one; `receipt_note`
    then names why, verbatim, never silently."""

    def __init__(self, *, run_id, overall_verdict, anomalies, final_state,
                morning_handoff_path, final_state_path,
                receipt_path, receipt_note):
        self.run_id = run_id
        self.overall_verdict = overall_verdict
        self.anomalies = anomalies
        self.final_state = final_state
        self.morning_handoff_path = morning_handoff_path
        self.final_state_path = final_state_path
        self.receipt_path = receipt_path
        self.receipt_note = receipt_note

    def __repr__(self):
        return "CloseoutResult(run_id=%r, overall_verdict=%r, anomalies=%r)" % (
            self.run_id, self.overall_verdict, self.anomalies)


def _require(condition, message):
    if not condition:
        raise CloseoutError(message)


def _require_str(value, field):
    _require(isinstance(value, str) and value.strip() != "",
             "run_state[%r] must be a non-empty string, got %r" % (field, value))
    return value


def _require_dict(run_state, field):
    _require(field in run_state, "run_state is missing required key %r" % (field,))
    value = run_state[field]
    _require(isinstance(value, dict),
             "run_state[%r] must be a dict, got %r" % (field, type(value).__name__))
    return value


def _require_list(run_state, field):
    _require(field in run_state, "run_state is missing required key %r" % (field,))
    value = run_state[field]
    _require(isinstance(value, list),
             "run_state[%r] must be a list, got %r" % (field, type(value).__name__))
    return value


def _validate_unit(unit, index):
    _require(isinstance(unit, dict), "units[%d] must be a dict, got %r" % (
        index, type(unit).__name__))
    uid = unit.get("id")
    _require(isinstance(uid, str) and uid.strip() != "",
             "units[%d] has no non-empty 'id'" % (index,))
    state = unit.get("state")
    _require(state in invariants.TASK_STATES,
             "unit %r has unknown state %r (not in orchestrator_invariants."
             "TASK_STATES)" % (uid, state))
    task_class = unit.get("task_class")
    _require(task_class in invariants.TASK_CLASSES,
             "unit %r has unknown task_class %r" % (uid, task_class))
    if state != "DONE":
        next_action = unit.get("next_action")
        _require(isinstance(next_action, str) and next_action.strip() != "",
                 "unit %r is not DONE (state %r) and names no next_action: "
                 "a problem with no next action is a complaint, not a "
                 "handover" % (uid, state))
    return uid


def _validate_check(check, index):
    _require(isinstance(check, dict), "evidence_checks[%d] must be a dict, "
             "got %r" % (index, type(check).__name__))
    name = check.get("name")
    _require(isinstance(name, str) and name.strip() != "",
             "evidence_checks[%d] has no non-empty 'name'" % (index,))
    verdict = check.get("verdict")
    _require(verdict in invariants.VERDICTS,
             "evidence check %r has unknown verdict %r" % (name, verdict))
    obligation = check.get("obligation")
    _require(obligation in invariants.EVIDENCE_OBLIGATIONS,
             "evidence check %r has unknown obligation %r" % (name, obligation))
    return name


def _validate_red_queue_item(item, index):
    _require(isinstance(item, dict), "red_queue[%d] must be a dict, got %r" % (
        index, type(item).__name__))
    item_id = item.get("id")
    _require(isinstance(item_id, str) and item_id.strip() != "",
             "red_queue[%d] has no non-empty 'id'" % (index,))
    return item_id


def _validate_decision(item, index):
    _require(isinstance(item, dict), "provisional_decisions[%d] must be a "
             "dict, got %r" % (index, type(item).__name__))
    decision = item.get("decision")
    _require(isinstance(decision, str) and decision.strip() != "",
             "provisional_decisions[%d] has no non-empty 'decision'" % (index,))
    return decision


def _validate_awaiting(item, index):
    _require(isinstance(item, dict), "awaiting_human[%d] must be a dict, "
             "got %r" % (index, type(item).__name__))
    question = item.get("question")
    _require(isinstance(question, str) and question.strip() != "",
             "awaiting_human[%d] has no non-empty 'question'" % (index,))
    return question


def _validate_throughput(run_state):
    throughput = _require_dict(run_state, "throughput")
    out = {}
    for field in _THROUGHPUT_FIELDS:
        _require(field in throughput,
                 "run_state['throughput'] is missing %r" % (field,))
        value = throughput[field]
        _require(isinstance(value, int) and not isinstance(value, bool)
                 and value >= 0,
                 "run_state['throughput'][%r] must be a non-negative int, "
                 "got %r" % (field, value))
        out[field] = value
    return out


def _validate(run_state):
    """Every required field, present and in-vocabulary, or CloseoutError
    naming exactly what is wrong. Returns nothing: this is a gate, not a
    transform, so close_out() re-reads run_state itself once validation
    has proven it safe to read."""
    _require(isinstance(run_state, dict),
             "run_state must be a dict, got %r" % (type(run_state).__name__,))
    for field in ("run_id", "goal", "base_revision", "final_revision"):
        _require_str(run_state.get(field), field)
    _require("hard_stop_reached" in run_state,
             "run_state is missing required key 'hard_stop_reached'")
    hard_stop_reached = run_state["hard_stop_reached"]
    _require(hard_stop_reached is None or isinstance(hard_stop_reached, bool),
             "run_state['hard_stop_reached'] must be True, False or None, "
             "got %r" % (hard_stop_reached,))
    gate_verdict = run_state.get("final_required_gate_verdict")
    _require(gate_verdict in invariants.VERDICTS,
             "run_state['final_required_gate_verdict'] must be one of %r, "
             "got %r" % (sorted(invariants.VERDICTS), gate_verdict))
    units_planned = run_state.get("units_planned")
    _require(isinstance(units_planned, int) and not isinstance(units_planned, bool)
             and units_planned >= 0,
             "run_state['units_planned'] must be a non-negative int, got %r"
             % (units_planned,))

    units = _require_list(run_state, "units")
    seen_ids = set()
    for index, unit in enumerate(units):
        uid = _validate_unit(unit, index)
        _require(uid not in seen_ids, "duplicate unit id %r in units" % (uid,))
        seen_ids.add(uid)

    _validate_throughput(run_state)

    checks = _require_list(run_state, "evidence_checks")
    seen_names = set()
    for index, check in enumerate(checks):
        name = _validate_check(check, index)
        _require(name not in seen_names,
                 "duplicate evidence check name %r in evidence_checks" % (name,))
        seen_names.add(name)

    for index, item in enumerate(_require_list(run_state, "provisional_decisions")):
        _validate_decision(item, index)

    red_ids = set()
    for index, item in enumerate(_require_list(run_state, "red_queue")):
        red_id = _validate_red_queue_item(item, index)
        _require(red_id not in red_ids, "duplicate red_queue id %r" % (red_id,))
        red_ids.add(red_id)

    for index, item in enumerate(_require_list(run_state, "awaiting_human")):
        _validate_awaiting(item, index)

    delivery_evidence = run_state.get("delivery_evidence")
    if delivery_evidence is not None:
        _require(isinstance(delivery_evidence, dict),
                 "run_state['delivery_evidence'] must be a dict or absent, "
                 "got %r" % (type(delivery_evidence).__name__,))
        for field in ("record", "claims", "before", "after"):
            _require(field in delivery_evidence,
                     "run_state['delivery_evidence'] is missing %r" % (field,))

    receipt_run_dir = run_state.get("receipt_run_dir")
    _require(receipt_run_dir is None or isinstance(receipt_run_dir, str),
             "run_state['receipt_run_dir'] must be a string or absent")


def _evidence_counts(checks):
    """(required_pass, required_fail, required_nodata, optional_nodata),
    four independent counts, never summed into one number (WBS rule 2):
    this function returns a 4-tuple rather than a total on purpose, and
    the test suite asserts each element separately from a fixture where
    they are all different, so a bug that swaps or merges two of them
    cannot hide behind an equal sum."""
    required_pass = required_fail = required_nodata = optional_nodata = 0
    for check in checks:
        obligation = check["obligation"]
        verdict = check["verdict"]
        if obligation in _REQUIRED_OBLIGATIONS:
            if verdict == "PASS":
                required_pass += 1
            elif verdict == "FAIL":
                required_fail += 1
            else:  # NO-DATA
                required_nodata += 1
        else:  # OPTIONAL
            if verdict == "NO-DATA":
                optional_nodata += 1
    return required_pass, required_fail, required_nodata, optional_nodata


def _overall_verdict(checks, anomalies):
    """"GREEN" only when every evidence check may proceed to release
    (orchestrator_invariants.may_proceed, imported rather than re-decided)
    and no unit was found non-terminal. "RED" otherwise, always: this
    module's one rule has no third answer, because a third answer is
    exactly the kind of soft landing a NO-DATA would use to read as safe."""
    if anomalies:
        return "RED"
    for check in checks:
        if not invariants.may_proceed(check["verdict"], check["obligation"],
                                      "release"):
            return "RED"
    return "GREEN"


def _find_anomalies(units):
    """Every unit whose state is not terminal, sorted by id. Rule 6: the
    drain (ORCH-13) is supposed to leave nothing here, so anything found
    is reported loudly, at the top of the document, never folded quietly
    into the WORK counts below."""
    out = []
    for unit in units:
        if not invariants.is_terminal(unit["state"]):
            out.append(unit["id"])
    return sorted(out)


def _work_counts(units):
    counts = {label: 0 for label in _NAMED_WORK_STATES}
    counts[_OTHER_TERMINAL_LABEL] = 0
    counts["NON-TERMINAL"] = 0
    for unit in units:
        state = unit["state"]
        if state in _NAMED_WORK_STATES:
            counts[state] += 1
        elif invariants.is_terminal(state):
            counts[_OTHER_TERMINAL_LABEL] += 1
        else:
            counts["NON-TERMINAL"] += 1
    return counts


def _build_delivery_report(delivery_evidence, receipt_run_dir):
    """(report_text_or_none, receipt_path_or_none, note). Reuses
    brother_run.build_report for the text and, when a receipt_run_dir is
    also given, receipt_door.receipts_for plus brother_run._write_receipt
    for the persisted receipt: see the module docstring's "WHAT IS REUSED"
    section for why this is the whole implementation, not a stub around a
    second reporter. Any failure here is this sub-block's own NO-DATA, not
    a CloseoutError: a broken Work document must not cost a person the
    rest of the morning handoff."""
    try:
        import brother_run
        import receipt_door
    except ImportError as exc:
        return None, None, "brother_run/receipt_door not importable: %s" % (exc,)

    try:
        report_text, _integrated, refused = brother_run.build_report(
            delivery_evidence["record"], delivery_evidence["claims"],
            delivery_evidence["before"], delivery_evidence["after"],
            delivery_evidence.get("changed"),
            log_path=delivery_evidence.get("log_path"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return None, None, "brother_run.build_report failed: %s" % (exc,)

    if not receipt_run_dir:
        return report_text, None, "no receipt_run_dir supplied: receipt not written"

    try:
        receipts = receipt_door.receipts_for(
            delivery_evidence["record"], delivery_evidence["claims"], refused,
            delivery_evidence.get("log_path"))
        receipt_path, why = brother_run._write_receipt(
            receipt_run_dir, receipts, report_text,
            delivery_evidence.get("log_path"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return report_text, None, "receipt could not be built: %s" % (exc,)
    if receipt_path is None:
        return report_text, None, why or "receipt not written for an unstated reason"
    return report_text, receipt_path, ""


def _problem_block(unit):
    lines = ["  - %s (state %s, task_class %s):" % (
        unit["id"], unit["state"], unit["task_class"])]
    lines.append("      blocked by: %s" % (unit.get("blocking_reason") or NODATA))
    lines.append("      tried: %s" % (unit.get("attempted") or NODATA))
    lines.append("      why the latest attempt failed: %s"
                 % (unit.get("failure_detail") or NODATA))
    lines.append("      evidence: %s" % (unit.get("evidence_summary") or NODATA))
    lines.append("      next action: %s" % unit["next_action"])
    return "\n".join(lines)


def _render_markdown(run_state, *, now, overall_verdict, anomalies,
                     work_counts, evidence_counts, delivery_report_text,
                     receipt_path, receipt_note):
    units = sorted(run_state["units"], key=lambda unit: unit["id"])
    checks = sorted(run_state["evidence_checks"], key=lambda check: check["name"])
    decisions = sorted(run_state["provisional_decisions"],
                       key=lambda item: item["decision"])
    red_queue = sorted(run_state["red_queue"], key=lambda item: item["id"])
    awaiting = sorted(run_state["awaiting_human"],
                      key=lambda item: item["question"])
    required_pass, required_fail, required_nodata, optional_nodata = evidence_counts

    lines = ["# Morning handoff: %s" % run_state["run_id"], ""]

    if anomalies:
        lines.append("## ANOMALY (read this first)")
        lines.append("")
        lines.append(
            "%d unit(s) are still non-terminal after this run's closeout. "
            "The hard-stop drain (ORCH-13) is supposed to leave nothing "
            "here; this means the drain did not run, or did not finish. "
            "Nothing below can be trusted as a final count until this is "
            "understood." % len(anomalies))
        for uid in anomalies:
            lines.append("  - %s" % uid)
        lines.append("")

    lines.append("## OUTCOME")
    lines.append("")
    lines.append("- goal: %s" % run_state["goal"])
    lines.append("- base revision: %s" % run_state["base_revision"])
    lines.append("- final revision: %s" % run_state["final_revision"])
    if run_state["final_revision"] == run_state["base_revision"]:
        lines.append("- nothing shipped this run: the final revision is "
                     "identical to the base revision")
    hard_stop_reached = run_state["hard_stop_reached"]
    if hard_stop_reached is None:
        lines.append("- hard stop: no hard stop was recorded for this run")
    else:
        lines.append("- hard stop reached: %s" % ("yes" if hard_stop_reached else "no"))
    lines.append("- final required-gate verdict: %s"
                 % run_state["final_required_gate_verdict"])
    lines.append("- overall verdict: %s" % overall_verdict)
    lines.append("")

    lines.append("## WORK")
    lines.append("")
    lines.append("- units planned: %d" % run_state["units_planned"])
    added_by_replan = sum(1 for u in units if u.get("added_by_replan"))
    lines.append("- units added by re-plan: %d" % added_by_replan)
    lines.append("- DONE: %d" % work_counts["DONE"])
    lines.append("- PARKED: %d" % work_counts["PARKED"])
    lines.append("- EXHAUSTED: %d" % work_counts["EXHAUSTED"])
    lines.append("- AWAITING-HUMAN: %d" % work_counts["AWAITING-HUMAN"])
    if work_counts[_OTHER_TERMINAL_LABEL]:
        lines.append("- other terminal states: %d" % work_counts[_OTHER_TERMINAL_LABEL])
    lines.append("")

    lines.append("## THROUGHPUT")
    lines.append("")
    throughput = run_state["throughput"]
    lines.append("- successful integrations: %d" % throughput["successful_integrations"])
    lines.append("- repair attempts: %d" % throughput["repair_attempts"])
    lines.append("- re-plans: %d" % throughput["replans"])
    lines.append("- handoffs: %d" % throughput["handoffs"])
    lines.append("- provider fallbacks: %d" % throughput["provider_fallbacks"])
    lines.append("- cross-reviews: %d" % throughput["cross_reviews"])
    lines.append("")

    lines.append("## EVIDENCE")
    lines.append("")
    lines.append("- required PASS: %d" % required_pass)
    lines.append("- required FAIL: %d" % required_fail)
    lines.append("- required NO-DATA: %d" % required_nodata)
    lines.append("- optional NO-DATA: %d" % optional_nodata)
    lines.append("")
    if checks:
        lines.append("  per check:")
        for check in checks:
            lines.append("  - %-30s verdict %-8s obligation %s" % (
                check["name"], check["verdict"], check["obligation"]))
    else:
        lines.append("  no evidence checks were recorded for this run")
    lines.append("")
    lines.append("  delivery report (reused from brother_run.build_report):")
    if delivery_report_text:
        for line in delivery_report_text.splitlines():
            lines.append("    " + line)
    else:
        lines.append("    %s: no delivery evidence was supplied to this "
                     "closeout" % NODATA)
    lines.append("  receipt: %s" % (receipt_path or ("%s: %s" % (NODATA, receipt_note))))
    lines.append("")

    lines.append("## DECISIONS")
    lines.append("")
    lines.append("  provisional rulings made without the owner:")
    if decisions:
        for item in decisions:
            made_by = item.get("made_by") or NODATA
            flip = item.get("flip_condition") or NODATA
            lines.append("  - %s (made by: %s; flip condition: %s)"
                         % (item["decision"], made_by, flip))
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("  queued RED items:")
    if red_queue:
        for item in red_queue:
            lines.append("  - %s: %s" % (item["id"],
                                         item.get("description") or NODATA))
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("  awaiting a human:")
    if awaiting:
        for item in awaiting:
            owner = item.get("owner") or NODATA
            lines.append("  - %s (owner: %s)" % (item["question"], owner))
    else:
        lines.append("  - none")
    lines.append("")

    lines.append("## PROBLEMS")
    lines.append("")
    unfinished = [u for u in units if u["state"] != "DONE"]
    if unfinished:
        for unit in unfinished:
            lines.append(_problem_block(unit))
    else:
        lines.append("  none: every unit closed DONE")
    lines.append("")

    lines.append("---")
    lines.append("generated at: %s" % now)
    lines.append("")
    return "\n".join(lines)


def _build_final_state(run_state, *, now, overall_verdict, anomalies,
                       work_counts, evidence_counts, receipt_path, receipt_note):
    required_pass, required_fail, required_nodata, optional_nodata = evidence_counts
    units = sorted(run_state["units"], key=lambda unit: unit["id"])
    return {
        "schema_version": invariants.SCHEMA_VERSION,
        "run_id": run_state["run_id"],
        "generated_at": now,
        "goal": run_state["goal"],
        "base_revision": run_state["base_revision"],
        "final_revision": run_state["final_revision"],
        "shipped": run_state["final_revision"] != run_state["base_revision"],
        "hard_stop_reached": run_state["hard_stop_reached"],
        "final_required_gate_verdict": run_state["final_required_gate_verdict"],
        "overall_verdict": overall_verdict,
        "anomalies": anomalies,
        "work": {
            "units_planned": run_state["units_planned"],
            "units_added_by_replan": sum(
                1 for u in units if u.get("added_by_replan")),
            "done": work_counts["DONE"],
            "parked": work_counts["PARKED"],
            "exhausted": work_counts["EXHAUSTED"],
            "awaiting_human": work_counts["AWAITING-HUMAN"],
            "other_terminal": work_counts[_OTHER_TERMINAL_LABEL],
        },
        "throughput": dict(run_state["throughput"]),
        "evidence": {
            "required_pass": required_pass,
            "required_fail": required_fail,
            "required_nodata": required_nodata,
            "optional_nodata": optional_nodata,
            "checks": [
                {"name": c["name"], "verdict": c["verdict"],
                 "obligation": c["obligation"]}
                for c in sorted(run_state["evidence_checks"],
                                key=lambda c: c["name"])
            ],
            "receipt_path": receipt_path,
            "receipt_note": receipt_note,
        },
        "decisions": {
            "provisional": sorted(
                [dict(item) for item in run_state["provisional_decisions"]],
                key=lambda item: item["decision"]),
            "red_queue": sorted(
                [dict(item) for item in run_state["red_queue"]],
                key=lambda item: item["id"]),
            "awaiting_human": sorted(
                [dict(item) for item in run_state["awaiting_human"]],
                key=lambda item: item["question"]),
        },
        "problems": [
            {
                "id": unit["id"],
                "state": unit["state"],
                "task_class": unit["task_class"],
                "blocking_reason": unit.get("blocking_reason"),
                "attempted": unit.get("attempted"),
                "failure_detail": unit.get("failure_detail"),
                "evidence_summary": unit.get("evidence_summary"),
                "next_action": unit["next_action"],
            }
            for unit in units if unit["state"] != "DONE"
        ],
    }


def _existing_run_id(out_dir):
    """The run_id an earlier close_out() attempt already stamped into
    out_dir's final-state.json, or None (no earlier attempt, or one that
    could not be read). Used only to tell a safe idempotent re-run of THIS
    run's own closeout from a misdirected call that would clobber a
    different run's handover; see the edge-list item on a pre-populated
    out_dir in the module docstring."""
    path = os.path.join(out_dir, FINAL_STATE_DIRNAME, FINAL_STATE_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("run_id")
    except (OSError, ValueError, AttributeError):
        return None


def close_out(run_state, *, out_dir, now=None):
    """Validate run_state, render the two required artifacts under
    out_dir, and return a CloseoutResult. Raises CloseoutError (writing
    nothing) on any missing field, out-of-vocabulary value, or non-DONE
    unit with no next_action: see the module docstring's CONTINGENCY
    section. `now` is a caller-supplied stamp string; omitted, this reads
    the real clock once, here, for a live invocation's own timestamp, and
    every test instead passes a fixed string so output stays comparable."""
    _validate(run_state)

    existing_run_id = _existing_run_id(out_dir)
    if existing_run_id is not None and existing_run_id != run_state["run_id"]:
        raise CloseoutError(
            "out_dir %r already holds a closeout for run %r; refusing to "
            "overwrite it with run %r. Point out_dir at a fresh directory "
            "for a different run." % (out_dir, existing_run_id, run_state["run_id"]))

    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    anomalies = _find_anomalies(run_state["units"])
    work_counts = _work_counts(run_state["units"])
    evidence_counts = _evidence_counts(run_state["evidence_checks"])
    overall_verdict = _overall_verdict(run_state["evidence_checks"], anomalies)

    delivery_evidence = run_state.get("delivery_evidence")
    if delivery_evidence is not None:
        delivery_report_text, receipt_path, receipt_note = _build_delivery_report(
            delivery_evidence, run_state.get("receipt_run_dir"))
    else:
        delivery_report_text, receipt_path = None, None
        receipt_note = "no delivery_evidence supplied to this closeout"

    markdown = _render_markdown(
        run_state, now=now, overall_verdict=overall_verdict, anomalies=anomalies,
        work_counts=work_counts, evidence_counts=evidence_counts,
        delivery_report_text=delivery_report_text, receipt_path=receipt_path,
        receipt_note=receipt_note)
    final_state = _build_final_state(
        run_state, now=now, overall_verdict=overall_verdict, anomalies=anomalies,
        work_counts=work_counts, evidence_counts=evidence_counts,
        receipt_path=receipt_path, receipt_note=receipt_note)

    handoff_path = os.path.join(out_dir, MORNING_HANDOFF_FILENAME)
    final_state_dir = os.path.join(out_dir, FINAL_STATE_DIRNAME)
    final_state_path = os.path.join(final_state_dir, FINAL_STATE_FILENAME)
    try:
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(final_state_dir, exist_ok=True)
        with open(handoff_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        with open(final_state_path, "w", encoding="utf-8") as fh:
            json.dump(final_state, fh, indent=1, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        raise CloseoutError("could not write closeout artifacts under %r: %s"
                            % (out_dir, exc))

    return CloseoutResult(
        run_id=run_state["run_id"], overall_verdict=overall_verdict,
        anomalies=anomalies, final_state=final_state,
        morning_handoff_path=handoff_path, final_state_path=final_state_path,
        receipt_path=receipt_path, receipt_note=receipt_note)
