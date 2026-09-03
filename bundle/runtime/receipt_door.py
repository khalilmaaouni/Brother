#!/usr/bin/env python3
"""receipt_door: the facts a delivery can prove, and the screens showing them.

Option A of the door redesign decided on 2026-08-31
(docs/plan/DOOR-REDESIGN-STUDY-2026-08-31.md, "OPTION 3, THE RECEIPT DOOR").
The engine already stored the evidence: scripts/brother_run.py's
_verify_evidence re-executes each unit's recorded check and refuses the
unprovable (lines 462 to 528). What was missing was the last step, handing
that evidence to the person as their own receipt instead of leaving it in a
claim store nobody opens.

THREE THINGS THIS FILE REFUSES TO INVENT, each one a way a receipt could
become theatre:

  A MARK IS A FACT LOOKED UP IN A TABLE, never a judgement. MARK_TABLE below
  is the whole of it: a unit whose recorded check re-executed and exited 0
  scores full marks, a refused unit scores zero, and a unit with no captured
  exit code is UNMARKED with its reason. decide.py's arithmetic is honest
  about how it multiplies; feeding it model-authored raw marks would launder
  an opinion through that honesty, which is precisely the risk the study
  named against the loom (study lines 34 to 36).

  A RISK TRIGGER IS A PATTERN MATCH over what the units themselves declared,
  never a model's sense of danger. risk_triggers() reads each unit's
  objective, its declared write scope and its done_check command, and reports
  which of the six named classes those words hit, with the words it hit on.
  Deterministic, so the same run always produces the same screen, and
  testable both ways (test_receipt_door.py drives all six and a plain change).

  A RECEIPT NAMES WHERE THE FULL OUTPUT LIVES rather than pasting a trimmed
  copy of it. The run log holds everything verbatim; the receipt is one
  sentence and a path.

Python 3, standard library only. No network.
"""
import json
import os
import re

import decide

NODATA = "NO-DATA"

#: Facts to marks. The whole table, deliberately three rows: there is no
#: partial credit, because there is no fact between "the recorded check
#: re-executed and exited 0" and "it did not".
MARK_TABLE = {
    "verified": 10.0,
    "refused": 0.0,
    "no-data": None,
}

#: The six risk classes, as regular expressions over a unit's own declared
#: words. Word-bounded on purpose: a bare substring test reads "auth" inside
#: "author" and a screen that cries wolf on every commit is a screen nobody
#: opens twice.
RISK_TRIGGERS = (
    ("encoding",
     r"\b(encod\w*|decod\w*|charset|utf-?8|unicode|base64|serialis\w*|serializ\w*)\b"),
    ("auth",
     r"\b(auth|authn|authz|authenticat\w*|authoris\w*|authoriz\w*|login|logout"
     r"|password\w*|credential\w*|oauth|api\s*key|access\s+token)\b"),
    ("migration",
     r"\b(migrat\w*|backfill\w*|schema\s+change|alter\s+table|create\s+table"
     r"|reindex\w*)\b"),
    ("money",
     r"\b(money|payment\w*|billing|billed|invoice\w*|pricing|refund\w*"
     r"|currency|payout\w*|charge\s+the\s+card|checkout)\b"),
    ("irreversibility",
     r"(\b(delete\w*|deletion|destroy\w*|purge\w*|truncat\w*|irreversib\w*"
     r"|unrecoverab\w*)\b|drop\s+table|rm\s+-rf|push\s+--force|--force-with-lease)"),
    ("public API",
     r"(\bpublic\s+api\b|\bendpoint\w*\b|\bopenapi\b|\bgraphql\b|/api/"
     r"|\bapi\s+contract\b|\bwire\s+format\b|\bpublished\s+interface\b)"),
)

#: The one sentence that scopes every green run this estate reports. Written
#: once here so the report, the acceptance screen and the release screen all
#: say the same thing rather than three softening variants of it.
SCOPING_SENTENCE = (
    "exit 0 means no check failed. It does not mean everything is proven: "
    "only the checks named above ran, and a check nobody wrote cannot fail.")


def _unit_text(row):
    """Everything a unit declared about itself, lowercased: what it is for,
    where it may write, and the command that decides it."""
    owns = row.get("owns") or row.get("writes") or []
    return " ".join([
        # `title` is what work_record.py actually stores (measured on a real
        # run, 2026-08-31: the door's own rows carry title, not objective);
        # the other two are what a hand-written or foreign plan may use, and
        # reading all three costs nothing.
        str(row.get("objective") or ""),
        str(row.get("title") or ""),
        str(row.get("name") or ""),
        str(row.get("done_check") or ""),
        " ".join(str(p) for p in owns),
    ]).lower()


def risk_triggers(rows):
    """[(trigger_name, unit_id, the words it hit on)], for every one of the
    six classes any unit's own declared scope names. Empty list means a plain
    change, and a plain change gets no release screen."""
    hits = []
    for row in rows or []:
        text = _unit_text(row)
        for name, pattern in RISK_TRIGGERS:
            found = re.findall(pattern, text)
            if not found:
                continue
            words = sorted({m if isinstance(m, str) else next(
                (p for p in m if p), "") for m in found} - {""})
            hits.append((name, row.get("id"), ", ".join(words)))
    return hits


#: The row field brother_run._stamp_dependency_mutations writes, spelled
#: here too so this module reads a record without importing the engine.
CHECK_WITHOUT_FIELD = "check_without_dependencies"

#: The stamp note, and the receipt reason, for a dependency whose own
#: per-unit file list is empty: nothing can be reverted, so the re-run is
#: never made (run 7, 2026-09-03: the engine tried to revert "(no file)" and
#: then failed on git). Spelled once, formatted with the dependency's id, so
#: brother_run writes the same sentence receipts_for reads back.
NO_FILE_DEPENDENCY = ("its dependency %s changed no file, so nothing shows "
                      "the check exercises it")

#: What a revert re-run's stderr says when the check BROKE rather than failed
#: (E42, run 5 critic 3, hole H3, 2026-09-03: dependency_gap read any
#: non-zero exit as coverage, so a check that exited 2 because the reverted
#: tree no longer parses counted as proof that it exercises the change).
#: These three are the interpreter's and the shell's own fixed wording, so a
#: plain substring test is enough; brother_run names the same three for its
#: own pre-work check, spelled again here because the import runs the other
#: way (brother_run imports this module).
BROKEN_REVERT_SIGNATURES = ("SyntaxError", "command not found",
                            "No such file or directory")

#: Exception names that mean a broken check ONLY when the traceback's LAST
#: line starts with one. A test that fails for the behaviour can print any of
#: these in its own output (a name it asserts on, a message it echoes), and
#: the last line of a traceback is the one place the interpreter itself says
#: what actually stopped the run.
BROKEN_REVERT_EXCEPTIONS = ("ImportError", "ModuleNotFoundError",
                            "NameError", "AttributeError")


def revert_broke_check(stderr_text):
    """True when `stderr_text` says the revert re-run could not run at all,
    rather than running and failing. Empty or absent stderr (a record from
    before the engine stamped it) is False: unmeasured reads exactly as it
    did before this existed, and the caller decides what that means."""
    text = str(stderr_text or "")
    if any(sig in text for sig in BROKEN_REVERT_SIGNATURES):
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return bool(lines) and lines[-1].strip().startswith(BROKEN_REVERT_EXCEPTIONS)


def dependency_gap(row):
    """The reason this unit's check cannot vouch for the change it depends
    on, or "" when it can (or when it depends on nothing). THE MUTATION AT
    RECEIPT (EVAD run 4 trial 2, 2026-09-03: the toy's test unit passed with
    the guard it covered deleted, and only a human audit noticed). The
    engine re-runs a dependent unit's check with each dependency's own
    change reverted and stamps the exit code; this reads that stamp:

      exit 0 with the change reverted: the check does not need that change,
        so it proves nothing about it; the reason names the files whose
        reversion did not change the verdict.
      no exit code (None): the re-run could not be made, and the stamp's
        own note says why; NO-DATA, never a pass.
      a declared dependency with no stamp at all: the record predates the
        rule, or the engine never ran it; NO-DATA, never a pass.
      a dependency that changed no file (its stamp carries the
        NO_FILE_DEPENDENCY note): nothing to revert, so no re-run was made;
        NO-DATA with exactly that reason, never a pass.
      non-zero with the change reverted, and stderr saying the check BROKE
        (E42): the re-run never reached a verdict about the behaviour, so
        it shows no coverage either; NO-DATA, never a pass.
      non-zero with the change reverted: the check fails without it, which
        is exactly what a covering check must do; no gap."""
    deps = [str(d) for d in (row.get("depends_on") or [])]
    if not deps:
        return ""
    entries = row.get(CHECK_WITHOUT_FIELD)
    if not isinstance(entries, list):
        return ("this unit depends on %s, and its check was never re-run "
                "with that change reverted, so nothing shows the check "
                "exercises it" % ", ".join(deps))
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dep = str(entry.get("unit") or "?")
        files = ", ".join(str(f) for f in (entry.get("files") or [])) or "(no file)"
        code = entry.get("exit_code")
        if str(entry.get("note") or "") == NO_FILE_DEPENDENCY % dep:
            return NO_FILE_DEPENDENCY % dep
        if isinstance(code, bool) or not isinstance(code, int):
            return ("the check could not be re-run with %s's change to %s "
                    "reverted (%s), so nothing shows the check exercises it"
                    % (dep, files, entry.get("note") or "no reason recorded"))
        if code == 0:
            return ("the check still passes with %s's change to %s reverted, "
                    "so it does not exercise that change" % (dep, files))
        if revert_broke_check(entry.get("stderr")):
            return ("the check broke with %s's files reverted rather than "
                    "failing, so nothing shows it exercises them" % dep)
    return ""


def dependency_note(row):
    """What this unit's check was shown to prove beyond its own change, for
    the receipt line (E40, run 5 critic 3, 2026-09-03: a PASS said nothing
    about what the check had been shown to cover). "no dependency declared:
    this check proves its own change only" when depends_on is empty;
    otherwise one clause per declared dependency naming it and the exit
    code the check produced with that dependency's files reverted (NO-DATA
    when that re-run was never made)."""
    deps = [str(d) for d in (row.get("depends_on") or [])]
    if not deps:
        return "no dependency declared: this check proves its own change only"
    entries = row.get(CHECK_WITHOUT_FIELD)
    parts = []
    for entry in (entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        code = entry.get("exit_code")
        parts.append("re-run with %s's files reverted: %s" % (
            entry.get("unit") or "?",
            "exit %d" % code if isinstance(code, int)
            and not isinstance(code, bool) else NODATA))
    return "; ".join(parts) or (
        "declared dependency on %s, never re-run with it reverted"
        % ", ".join(deps))


def receipts_for(record, claims, refused, log_path=None):
    """One receipt per unit in the Work document, in plan order.

    Facts only: what the unit was for, the command that decided it, the exit
    code that was actually captured, where the full output lives, and, when
    the unit did not deliver, the refusal reason in the words the engine
    already wrote. `refused` is build_report's own [(unit_id, reason)] list,
    so this never disagrees with the report it sits under."""
    refusals = dict(refused or [])
    out = []
    for row in (record.get("rows") or record.get("units") or []):
        uid = row.get("id")
        claim = (claims or {}).get(uid) or {}
        evidence = claim.get("evidence") if isinstance(
            claim.get("evidence"), dict) else {}
        command = str(evidence.get("check_command")
                      or row.get("done_check") or "").strip()
        exit_code = evidence.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            exit_code = None
        receipt = {
            "id": uid,
            "objective": str(row.get("objective") or row.get("title")
                             or row.get("name") or ""),
            "command": command,
            "exit_code": exit_code,
            "output_location": log_path or NODATA,
            "reason": "",
            # check-authorship-v1 (docs/decisions/check-authorship-v1-
            # 2026-09-03.json), Option A: the model that decomposed the
            # outcome writes every unit's done_check (bundle/runtime/
            # door.py), and this receipt names that author beside the
            # verdict. "the person" only when the intent screen recorded a
            # live edit of THIS unit's check; nothing in this estate builds
            # that edit path yet, so the field defaults honestly to the one
            # author that has ever actually written a done_check here.
            "author": ("the person" if row.get("check_author") == "the person"
                      else "the planning model"),
            # harness-revision-v1 (zero-context critic finding, defect 2,
            # 2026-09-03): the exact commit of scripts/brother_run.py that
            # RAN this unit, so a pre-fix receipt can be told from a
            # post-fix one by reading the receipt alone. Stamped once, per
            # run, onto the whole Work document (main()); NODATA when the
            # run predates that stamp or the engine was not a git checkout.
            "harness_revision": record.get("harness_revision") or NODATA,
            # E40: what the check was shown to prove beyond its own change,
            # rendered inside the verified sentence by receipt_sentence.
            "dependency_note": dependency_note(row),
        }
        if uid in refusals:
            receipt["state"] = "refused"
            receipt["reason"] = refusals[uid]
        elif row.get("integration_refused"):
            receipt["state"] = "refused"
            receipt["reason"] = str(row["integration_refused"])
        elif row.get("check_passed_before") is True:
            # CHECK DISCRIMINATION (the toy-repo finding, 2026-09-03): this
            # exact check already exited 0 on the untouched repository
            # before any worker ran, per brother_run.py's own
            # _stamp_prechecks. A green re-execution now proves nothing
            # about the work; never counted as passing, whatever the exit
            # code is.
            receipt["state"] = "no-data"
            receipt["reason"] = ("the check already passed before the work "
                                 "began, so it cannot prove the work")
        elif ("files_changed_by_unit" in row
              and not row.get("files_changed_by_unit")):
            # ZERO-CHANGE UNITS (the same finding): brother_run.py's own
            # _mark_integrated stamped the file list THIS unit's own lane
            # merge changed on canonical (integrate.py measured it at the
            # merge and the claim's evidence carried it; E41), and it is
            # empty. A check that passes over an untouched tree is not a
            # delivery, whatever it checks.
            receipt["state"] = "no-data"
            receipt["reason"] = ("no file changed, so nothing here proves "
                                 "the work was done")
        elif row.get("check_passed_before") is not False:
            # NO-DATA LAUNDERED INTO PASS (the zero-context critic finding,
            # defect 1, 2026-09-03): check_passed_before is either None (the
            # engine's own _check_passes_now could not even attempt the
            # precheck) or the key is simply absent (a Work document written
            # by a harness older than 5ea2305f, which never stamped this
            # fact at all). Neither is the same fact as "measured False", and
            # an unknown must never read as proof, whatever the exit code
            # captured below says.
            receipt["state"] = "no-data"
            receipt["reason"] = ("the pre-run check was not recorded for "
                                 "this unit, so nothing shows the check can "
                                 "tell the work from no work")
        elif "files_changed_by_unit" not in row:
            # Same laundering, the other fact: _mark_integrated never
            # stamped which files this unit actually changed (a harness
            # older than the fact, or a claim whose evidence carried no
            # files_changed). Absent is not "measured empty" above; it is
            # unmeasured.
            receipt["state"] = "no-data"
            receipt["reason"] = "the files this unit changed were not recorded"
        elif dependency_gap(row):
            # THE MUTATION AT RECEIPT (rule 5, EVAD run 4 trial 2,
            # 2026-09-03): the check passed before-and-after its own
            # existence, yet still passes with the change it depends on
            # reverted, or that re-run could not be made. Either way the
            # check cannot vouch for the covered change, and dependency_gap
            # names the file whose reversion did not change the verdict.
            receipt["state"] = "no-data"
            receipt["reason"] = dependency_gap(row)
        elif exit_code == 0 and command:
            receipt["state"] = "verified"
        else:
            check_exit_before = row.get("check_exit_before")
            same_broken_check = (
                isinstance(check_exit_before, int)
                and not isinstance(check_exit_before, bool)
                and exit_code is not None
                and exit_code == check_exit_before
                and exit_code != 0)
            receipt["state"] = "no-data"
            if same_broken_check:
                # A BROKEN CHECK IS NOT A FAILING CHECK (rule 4, the
                # zero-context critic, 2026-09-03): check_passed_before is
                # False either way a check that cannot run at all reports
                # (an ordinary failing assertion, or a syntax error that
                # never even reaches one), so this comparison is the one
                # fact that tells them apart: the SAME command exited the
                # SAME non-zero code before any work happened and again
                # after it finished. Nothing about that difference proves
                # the work; it proves the check never ran to begin with.
                receipt["reason"] = (
                    "the check fails the same way before and after the "
                    "work, so it does not run and proves nothing")
            else:
                receipt["reason"] = (
                    "no captured exit code was recorded for this unit, so "
                    "nothing here proves it ran" if exit_code is None else
                    "the recorded check exited %d, so this unit is not "
                    "proven" % exit_code)
        out.append(receipt)
    return out


def receipt_sentence(receipt):
    """One plain sentence per delivered unit, plus WHO WROTE THE CHECK IT
    RAN ON (check-authorship-v1, Option A): the receipt proves a stated
    check reproduced, never that the check was the right one, because the
    model doing the work is the same model that wrote the check. Naming the
    author right beside the verdict is the cheapest way this estate has to
    stop that fact from being invisible. No jargon, no unit vocabulary
    beyond the id the plan already used."""
    if receipt["state"] == "verified":
        # The parenthesis is E40's clause: what this check was shown to
        # prove beyond its own change (dependency_note), on the one line a
        # person reads, so a PASS never reads wider than it was measured.
        body = ("%s delivered: the check %s was run and exited %d (%s), and "
                "its full output is in %s."
                % (receipt["id"], receipt["command"], receipt["exit_code"],
                   receipt.get("dependency_note") or NODATA,
                   receipt["output_location"]))
    elif receipt["state"] == "refused":
        body = "%s was refused: %s" % (receipt["id"], receipt["reason"])
    else:
        body = ("%s is %s: %s. The check it named was %s."
                % (receipt["id"], NODATA, receipt["reason"],
                   receipt["command"] or "not recorded"))
    # harness-revision-v1 (defect 2): the producer of the check itself, and
    # the producer of the RUN that reproduced it, both named on the one line
    # a person actually reads. First 12 hex of the sha (the shortest prefix
    # git itself treats as unambiguous on a repository this size), never the
    # full 40, because a receipt is a sentence, not a log line.
    harness = str(receipt.get("harness_revision") or NODATA)
    fragment = harness[:12] if not harness.startswith(NODATA) else NODATA
    return "%s Check written by %s, harness %s." % (
        body, receipt.get("author") or "the planning model", fragment)


def mark_for(receipt):
    """(mark_or_None, why). The table above, looked up, plus the sentence
    saying which fact produced it. None means unmarked, which decide.py
    renders as NO-DATA contributing nothing: not a zero, not a middle."""
    state = receipt.get("state")
    mark = MARK_TABLE.get(state, None)
    if state == "verified":
        why = ("the recorded check exited 0 when it was re-executed at "
               "delivery, which the fixed table marks %.0f" % mark)
    elif state == "refused":
        why = ("this unit was refused (%s), which the fixed table marks 0"
               % (receipt.get("reason") or "no reason recorded"))
    else:
        why = ("%s: %s, so this criterion is unmarked rather than scored"
               % (NODATA, receipt.get("reason") or "no captured exit code"))
    return mark, why


def _criteria(receipts):
    # Weights that already sum to 1, so decide.py has nothing to rescale and
    # the page carries no note about arithmetic the reader never asked about.
    each = 1.0 / len(receipts) if receipts else 0.0
    return [{"key": r["id"],
             "label": "%s%s" % (r["id"], (": " + r["objective"][:70])
                                if r["objective"] else ""),
             "weight": each,
             "why": ("every unit of the same delivery counts the same; the "
                     "weights are equal because nothing here ranks one "
                     "person's ask above another")}
            for r in receipts]


def _option(receipts, name, one_liner):
    scores, basis, pros, cons = {}, {}, [], []
    for r in receipts:
        mark, why = mark_for(r)
        if mark is not None:
            scores[r["id"]] = mark
        basis[r["id"]] = why
        (pros if r["state"] == "verified" else cons).append(
            receipt_sentence(r))
    return {"id": "as-measured", "name": name, "one_liner": one_liner,
            "cost": "already spent: this run is finished",
            "reversible": "the decision is; the run already happened",
            "pros": pros or ["nothing in this run verified"],
            "cons": cons or ["nothing in this run was refused or unproven"],
            "scores": scores, "score_basis": basis}


def _stamp(record, before, after):
    return ("run of %r; canonical revision %s to %s"
            % (record.get("outcome") or "(no outcome recorded)",
               (before or NODATA)[:12], (after or NODATA)[:12]))


def acceptance_spec(record, receipts, before=None, after=None, log_path=None):
    """The fourth human moment of the charter (docs/CHARTER.md line 27), as a
    screen: only a human accepts that the delivered result is the one that was
    wanted. Every number on it came out of MARK_TABLE."""
    return {
        "title": "Accept this delivery, or do not",
        "eyebrow": "Acceptance",
        "stamp": _stamp(record, before, after),
        "plain_summary": (
            "This run is finished. Below is what it delivered and how each "
            "piece was checked, one line per piece. The score is not an "
            "opinion: a piece whose check ran and exited 0 is marked 10, a "
            "refused piece is marked 0, and a piece with no captured exit "
            "code is left unmarked with its reason. " + SCOPING_SENTENCE),
        "question": ("Is this the result you wanted? Accepting and holding "
                     "are both yours to choose, and neither is scored here, "
                     "because scoring your own decision would be an opinion "
                     "wearing arithmetic's clothes. What is scored is the "
                     "evidence."),
        "criteria": _criteria(receipts),
        "options": [_option(
            receipts, "The delivery as it stands",
            "every piece of this run, with the check that decided it")],
        "would_change": [
            "A piece marked %s gets a real check and it passes: the mark "
            "moves from unmarked to 10 by the same table, with nobody's "
            "judgement in between." % NODATA,
            "A refused piece is repaired and re-run: its refusal reason "
            "disappears from this screen and its mark becomes 10.",
            "You run the checks named above yourself and one of them "
            "disagrees with the exit code recorded here. That is the whole "
            "point of naming them.",
        ],
        "footer": ("Generated from this run's own receipts by "
                   "scripts/receipt_door.py. The full, untrimmed output of "
                   "every check is in %s." % (log_path or NODATA)),
    }


def release_spec(record, receipts, triggers, before=None, after=None,
                 log_path=None):
    """The third human moment (docs/CHARTER.md line 26): only a human decides
    to ship, informed by assurance, never overruled by it. Rendered only when
    this run's own units named one of the six risk classes."""
    spec = acceptance_spec(record, receipts, before, after, log_path)
    named = "; ".join("%s (unit %s, on the words: %s)" % (t[0], t[1], t[2])
                      for t in triggers)
    spec.update({
        "title": "Release this change, or hold it",
        "eyebrow": "Release",
        "plain_summary": (
            "This run touched something the estate treats as risky, so it "
            "stops here for a human. What it hit: %s. Below is every piece "
            "of the run with the check that decided it. The score is not an "
            "opinion: checked and green is 10, refused is 0, and no captured "
            "exit code is left unmarked with its reason. %s"
            % (named, SCOPING_SENTENCE)),
        "question": ("Do you release this, or hold it? Assurance can "
                     "recommend and it cannot decide; the recommendation is "
                     "the evidence below, and the decision is yours."),
    })
    spec["options"][0]["name"] = "The change as it stands"
    spec["would_change"].insert(0, (
        "The risky words come out of the units' own scope: a run whose "
        "pieces name none of the six classes (encoding, auth, migration, "
        "money, irreversibility, public API) never renders this screen at "
        "all."))
    return spec


def write_screen(spec, out_path):
    """The spec beside the page, both. The JSON is what a later session can
    re-render or argue with; the HTML is what a person opens. Returns
    (html_path, "") or (None, why); never raises, because a screen that fails
    to render must not fail the delivery it describes."""
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(os.path.splitext(out_path)[0] + ".json", "w",
                  encoding="utf-8") as fh:
            json.dump(spec, fh, indent=1)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(decide.render(spec))
    except (OSError, ValueError) as exc:  # noqa: BLE001
        return None, "%s: the screen could not be written: %s" % (NODATA, exc)
    return out_path, ""


def render_run_screens(record, receipts, run_dir, before=None, after=None,
                       log_path=None):
    """(written_paths, triggers). The acceptance screen for every completed
    run, and the release screen too when the run's units named a risk class.

    decide.main() is NOT used here on purpose: it stamps a machine-wide
    intake sentinel meant for founder-facing questions, and a test fixture
    finishing a run must not touch it. This calls decide.render directly."""
    triggers = risk_triggers(record.get("rows") or record.get("units") or [])
    # A SUBDIRECTORY, NOT THE RUN DIRECTORY ITSELF. brother_run finds a run's
    # Work document by "the one .json in the run directory that is neither
    # claims nor target", so a screen's own spec file sitting beside it made
    # --resume fail with "expected exactly one Work document" (caught by the
    # estate's acceptance area 6 the same day this landed). Screens live
    # under screens/ where nothing counts them.
    run_dir = os.path.join(run_dir, "screens")
    written = []
    for name, spec in (
            ("acceptance-screen.html",
             acceptance_spec(record, receipts, before, after, log_path)),
            ("release-screen.html",
             release_spec(record, receipts, triggers, before, after, log_path)
             if triggers else None)):
        if spec is None:
            continue
        path, problem = write_screen(spec, os.path.join(run_dir, name))
        if path:
            written.append(path)
        else:
            written.append(problem)
    return written, triggers
