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

A FOURTH, added by E79 (the delivery-proof skeptic, 2026-09-04): A HARNESS
SHA IS NAMED RESOLVABLE OR PRIVATE, NEVER BARE. harness_label() checks the
sha against the public export remote in this checkout before printing it;
a hub commit almost never resolves there (scripts/export_public.py builds
new commits, it does not mirror hashes), so the honest default is the
private label, not a bare fragment that reads as though any reader could
look it up. Per-file evidence follows the same rule: per_file_checks()
turns a run's own receipts into one entry per changed file (the exact
check command, its exit code, its output path, the before-and-after
discrimination), and require_per_file_checks() is the one gate a stored
delivery record must pass before accept_delivery.py writes it, so a
record naming files and a PASS count with no check behind them is refused
rather than shipped.

Python 3, standard library only. No network.
"""
import json
import os
import re
import subprocess
import sys

import decide
import door
import journal
import scope_audit
import work_record

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


def _pack_forcing_triggers(pack_name, packs_dir=door.PACKS_DIR):
    """P10 (persona integration plan 2026-09-04, row P10; the founder
    document's section 25.2 forcing classes): [(class_id, pattern), ...]
    built from the named pack's own forcing_classes list, one pattern per
    entry, word-bounded exactly like the six classes above so "promotional"
    can never fire "promotion". The words come from each entry's `id`
    (underscore-separated, e.g. "threshold_change" becomes the two-word
    phrase "threshold change"), read through door.load_pack rather than
    copied by hand, so an edit to the pack's forcing_classes changes the
    trigger without a second file to keep in sync.

    GENERIC PER PACK since the compositional-selection row: the pack name
    is an argument with no default, because selection is compositional
    (persona doc 5.2) and data-science is one pack among thirteen rather
    than the one pack this reader knows about. The pattern building itself
    is door.forcing_class_triggers, shared with door.py's own composed
    challenge decision rather than written twice.

    An unreadable or missing pack is NO-DATA, not a crash: door.load_pack
    raises ValueError for a missing file, bad JSON or a manifest missing a
    required key, and this returns () for all three, so RISK_TRIGGERS below
    still carries its original six base classes and every other receipt
    still runs."""
    try:
        pack = door.load_pack(pack_name, packs_dir=packs_dir)
    except ValueError as exc:
        print("%s: %s pack unreadable, its forcing classes were not added "
              "to RISK_TRIGGERS: %s" % (NODATA, pack_name, exc),
              file=sys.stderr)
        return ()
    return tuple(door.forcing_class_triggers(pack))


def _dedupe(pairs):
    """(class_id, pattern) pairs with the FIRST declaration of each id kept,
    in the order given: base classes lead, then the composed packs in their
    own order, so a pack can never redefine a base class's pattern."""
    seen = set()
    out = []
    for class_id, pattern in pairs:
        if class_id in seen:
            continue
        seen.add(class_id)
        out.append((class_id, pattern))
    return tuple(out)


#: The six classes above under their own name, so the always-armed set
#: below can be read beside them.
BASE_RISK_TRIGGERS = RISK_TRIGGERS


def _lens_forcing_triggers(lens_names, packs_dir=door.PACKS_DIR):
    """The forcing classes of the lenses this run actually INFERRED, in the
    order they were composed, plus door.BASE_LENS's own, deduped by class id
    against the six base classes and against each other, and returned
    WITHOUT those six (the caller already holds them).

    ARMED BY INFERENCE, NOT BY INSTALLATION. The version this replaces read
    EVERY installed pack, which was right while one persona pack existed
    and wrong the moment eleven landed: the backend pack's "retry" class
    then fired on "add a retry to the fetch helper" and the data
    engineering pack's "backfill" class on any plain backfill, in
    repositories that are neither. A screen that cries wolf on every commit
    is a screen nobody opens twice, which is the same reason the six base
    classes are word-bounded. Composition already decides which packs a
    unit sits under (persona doc 5.2, door.infer_lenses); the risk surface
    is the union over THAT list.

    An empty `lens_names` (a record with no inferred lens, or a caller with
    no record) arms core alone, so a plain change fires the base six and
    nothing else.

    A pack that cannot be read is NO-DATA on stderr inside
    _pack_forcing_triggers and contributes nothing: the base classes still
    apply and every other receipt still runs."""
    names = [str(n) for n in (lens_names or []) if str(n or "").strip()]
    if door.BASE_LENS not in names:
        names.append(door.BASE_LENS)
    out = []
    for name in names:
        out.extend(_pack_forcing_triggers(name, packs_dir=packs_dir))
    composed = _dedupe(tuple(BASE_RISK_TRIGGERS) + tuple(out))
    return composed[len(BASE_RISK_TRIGGERS):]


#: What is armed for EVERY run whatever it touches: the six base classes
#: plus door.BASE_LENS's own forcing classes, whose ids repeat the base six
#: and are deduped away, so this is exactly the six today. A persona pack's
#: classes are added per run, from the lenses that run inferred, by
#: risk_triggers(rows, lenses) below, which is what loom.park_units and
#: brother_run's P4 risk line both read.
RISK_TRIGGERS = _dedupe(BASE_RISK_TRIGGERS + _lens_forcing_triggers([]))


def record_lenses(record):
    """[lens name, ...] as door.py stamped them on a Work document: the
    composed `lenses` list, most specific first, or the single `lens` of a
    document written before composition landed, or [] when nothing was
    inferred. Read here rather than in each caller so loom.py and
    brother_run.py arm the same classes from the same field."""
    inferred = (record or {}).get("lens_inferred") or {}
    if not isinstance(inferred, dict):
        return []
    names = []
    for entry in inferred.get("lenses") or []:
        lens, _matched = door._lens_entry(entry)
        if lens:
            names.append(lens)
    if not names and inferred.get("lens"):
        names.append(str(inferred["lens"]))
    return names

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


def risk_triggers(rows, lenses=None):
    """[(trigger_name, unit_id, the words it hit on)], for every one of the
    six base classes any unit's own declared scope names, plus the forcing
    classes of `lenses`: the lenses this run INFERRED, most specific first,
    as receipt_door.record_lenses reads them off the Work document. Empty
    list means a plain change, and a plain change gets no release screen.

    `lenses` omitted or empty means no lens was inferred, which arms core
    alone (see _lens_forcing_triggers): a persona pack's classes never fire
    on a repository that pack was not inferred for."""
    armed = _dedupe(tuple(RISK_TRIGGERS)
                    + tuple(_lens_forcing_triggers(lenses)))
    hits = []
    for row in rows or []:
        text = _unit_text(row)
        for name, pattern in armed:
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


#: P6 (doc E18/12.6): the five fields brother_run.py's _verify_evidence
#: reads off <run_dir>/evidence/<unit>.json for a unit whose evidence_family
#: is E18, and stamps onto the row as row["e18_evidence"]: either all five
#: (a metric with a value, what it beat, the seed, the holdout identity) or
#: a single {"missing_reason": ...} naming exactly which fact is missing.
#: Mirrored here, read-only, only to render the receipt's own sentence; the
#: reading and validation live in brother_run.py, once, at the source.
E18_FIELDS = ("metric", "value", "baseline", "seed", "holdout_id")


def e18_gap(row):
    """"" when this row's evidence_family is not E18, or is E18 and its
    e18_evidence carries every one of E18_FIELDS; otherwise the reason a
    number a check printed cannot be trusted as this row's statistical
    evidence. A unit with a green check and no number is not proof of the
    number, whatever the exit code says (the doc's own complaint this row
    exists to close)."""
    if str(row.get("evidence_family") or "") != "E18":
        return ""
    e18 = row.get("e18_evidence")
    if not isinstance(e18, dict):
        return "no metric recorded: no evidence file at all"
    reason = e18.get("missing_reason")
    if reason:
        return str(reason)
    for field in E18_FIELDS:
        if e18.get(field) is None or e18.get(field) == "":
            return "no metric recorded: field %r is missing" % field
    return ""


#: P7 (persona plan section 2, the verify stage; docs/plan/
#: PERSONA-INTEGRATION-PLAN-2026-09-04.md, row P7): the two evidence
#: families whose own claim is a decision figure a numbers-manifest.json
#: backs (work_record.EVIDENCE_FAMILIES already admits E1..E18; the plan
#: names E8 and E2 for this row). A row outside these two never triggers
#: anything below, the same way e18_gap above only ever looks at E18.
NUMBERS_EVIDENCE_FAMILIES = ("E8", "E2")

#: The P6 evidence file's key this row adds: brother_run.py's own
#: <run_dir>/evidence/<unit_id>.json (the same file _read_e18_evidence
#: reads for E18, mirrored read-only here for a different key, exactly as
#: E18_FIELDS above mirrors that function's field list) now also carries a
#: numbers-manifest.json PATH a unit's own check wrote before it exited.
NUMBERS_MANIFEST_FIELD = "numbers_manifest"

#: products/brothersbe/tools/ mounted onto sys.path from THIS file's own
#: location, the same one-directional pattern products/brothersbe/src/
#: brothersbe/_toolspath.py already uses to reach sbe_gate.py: that helper
#: cannot be imported here without first mounting tools/ itself (it lives
#: on the far side of the mount), so the same four lines are computed here
#: from receipt_door.py's own path instead of copying its logic.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SBE_TOOLS = os.path.join(_ROOT, "products", "brothersbe", "tools")


def _sbe_gate_numbers():
    """(gate_numbers, "") once products/brothersbe/tools/sbe_gate.py is
    reachable, or (None, a NO-DATA reason) when it is not: a product moved,
    renamed or absent is a fact this run's evidence reports, never a crash
    that would take the whole receipt down with it. Imported, never copied
    (P7's own words): sbe_gate.py's own gate_numbers is the single source
    of what a numbers-manifest.json proves."""
    if _SBE_TOOLS not in sys.path:
        sys.path.insert(0, _SBE_TOOLS)
    try:
        import sbe_gate  # noqa: E402  (mounted onto sys.path just above)
    except ImportError as exc:
        return None, ("%s: products/brothersbe/tools/sbe_gate.py could not "
                      "be imported (%s), so no numbers-manifest gate could "
                      "run for this unit" % (NODATA, exc))
    return sbe_gate.gate_numbers, ""


def _read_numbers_manifest_path(run_dir, unit_id):
    """(path, "") when <run_dir>/evidence/<unit_id>.json exists, parses as a
    JSON object and names a non-empty numbers_manifest string; otherwise
    (None, reason) naming exactly why not. Mirrors brother_run.py's own
    _read_e18_evidence read of this same file for a different key: that
    function is private to brother_run.py, and this module is read (and
    tested) without brother_run.py running at all, so the read is repeated
    here rather than imported the wrong way round."""
    path = os.path.join(run_dir or "", "evidence", "%s.json" % unit_id)
    if not run_dir or not os.path.isfile(path):
        return None, "no metric recorded: no evidence file at %s" % path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, ("no metric recorded: %s could not be read as JSON (%s)"
                      % (path, exc))
    if not isinstance(data, dict):
        return None, "no metric recorded: %s is not a JSON object" % path
    manifest = data.get(NUMBERS_MANIFEST_FIELD)
    if not manifest or not isinstance(manifest, str):
        return None, ("no metric recorded: field %r is missing from %s"
                      % (NUMBERS_MANIFEST_FIELD, path))
    return manifest, ""


def numbers_manifest_evidence(row, run_dir):
    """(gap, message) for a unit whose evidence_family is E8 or E2 (P7): gap
    is "" and message is sbe_gate.gate_numbers' own PASS sentence (it reads
    "N figure(s) ... re-run to zero drift") when the unit's evidence file
    names a numbers-manifest.json that gate verifies; otherwise gap names
    exactly why not, in the gate's own words when the gate is the one that
    refused (its FAIL or NO-DATA verdict and message, P7's own requirement),
    and message is "". A row outside the two named families gets ("", ""):
    untouched, the same way e18_gap leaves a non-E18 row alone."""
    if str(row.get("evidence_family") or "") not in NUMBERS_EVIDENCE_FAMILIES:
        return "", ""
    manifest, reason = _read_numbers_manifest_path(run_dir, row.get("id"))
    if manifest is None:
        return reason, ""
    manifest_path = manifest if os.path.isabs(manifest) else os.path.join(
        run_dir or "", manifest)
    if not os.path.isfile(manifest_path):
        return ("no metric recorded: numbers_manifest %r does not exist"
                % manifest), ""
    gate_numbers, reason = _sbe_gate_numbers()
    if gate_numbers is None:
        return reason, ""
    verdict, message = gate_numbers(os.path.dirname(manifest_path))
    if verdict != "PASS":
        return "%s: %s" % (verdict, message), ""
    return "", message


#: E74: the states a recalled lesson can hold once its own applies_to
#: anchors are checked against the tree a session actually runs in
#: (products/brothermode/tools/vault_recall_hook.py's lesson_states). Not a
#: fourth row in MARK_TABLE above: a stale memory is not a unit's check
#: failing, it is a citation that no longer resolves, and the receipt
#: records that as its own fact, on its own section.
MEMORY_STATES = ("applied", "stale", "unverified")


def applied_memory(recalled):
    """The receipt's applied-memory section: every lesson
    vault_recall_hook.py recalled during this run, partitioned by state.
    `recalled` is that hook's own lesson_states() output, a list of
    {"slug", "path", "state", "line", "note_type"} dicts, read here and never
    recomputed: this function only reports what the hook already decided,
    so the receipt and the hook can never disagree about which lessons were
    stale.

    Returns {"applied": [...], "stale": [...], "unverified": [...]}, each
    entry naming the lesson's slug plus its type (P11: data_semantic,
    test_oracle or whatever the note's own type: field says, omitted when
    the note declared none) and, whenever the hook attached one, its exact
    reason line -- a stale entry's refusal, or an unverified entry's
    human_approved-false reason -- so a reader can see at a glance which
    lessons this run trusted, which it refused, and why. An unrecognized
    state is dropped rather than guessed into one of the three, and reported
    once on stderr: a receipt that silently reclassifies a fourth state is
    worse than one that says nothing about it."""
    section = {state: [] for state in MEMORY_STATES}
    for rec in recalled or []:
        state = rec.get("state")
        if state not in MEMORY_STATES:
            sys.stderr.write(
                "receipt_door: applied_memory dropped a lesson with "
                "unknown state %r\n" % state)
            continue
        entry = {"slug": rec.get("slug")}
        if rec.get("note_type"):
            entry["type"] = rec.get("note_type")
        if rec.get("line"):
            entry["line"] = rec.get("line")
        section[state].append(entry)
    return section


def receipts_for(record, claims, refused, log_path=None,
                 target_revision=None, env_lock=None,
                 data_identity_by_id=None):
    """One receipt per unit in the Work document, in plan order.

    Facts only: what the unit was for, the command that decided it, the exit
    code that was actually captured, where the full output lives, and, when
    the unit did not deliver, the refusal reason in the words the engine
    already wrote. `refused` is build_report's own [(unit_id, reason)] list,
    so this never disagrees with the report it sits under.

    `target_revision`, `env_lock` and `data_identity_by_id` (P9, persona
    integration plan 2026-09-04 row P9; doc 12.6 code, environment and model
    identity; doc F14 reproducibility failure): three facts build_report
    already computed from the target (never derived here, this function
    reads no filesystem), stamped onto every receipt beside the harness
    revision it already carried, so a result can be tied to the exact code,
    environment and data that produced it. Every caller that predates this
    row leaves them at their defaults, and every receipt reads NO-DATA for
    whichever one was never given, never a made-up value."""
    refusals = dict(refused or [])
    # The run directory is not in this function's hands (it takes a Work
    # document, a claim store and a refusal list, never a path), so it comes
    # from the environment brother_run.py exports around the whole run
    # (E59). Read once, before the loop, because P7 (persona plan section 2)
    # needs it too: an E8 or E2 row's numbers-manifest evidence lives at
    # <run_dir>/evidence/<unit_id>.json, the same P6 file brother_run.py's
    # own _read_e18_evidence reads for a different key, and this function
    # reads it directly rather than waiting for brother_run.py to stamp it
    # onto the row (brother_run.py stamps e18_evidence for E18; it stamps
    # nothing for E8/E2, so this module reads its own evidence here).
    run_dir = journal.run_dir_from_env()
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
        # P7: computed once per row, ahead of the state machine below, since
        # (unlike e18_gap, a pure dict lookup) this reads a file and may run
        # sbe_gate.gate_numbers, which walks a directory and shells out to
        # git; a row outside NUMBERS_EVIDENCE_FAMILIES gets ("", "") at
        # near-zero cost.
        numbers_gap, numbers_message = numbers_manifest_evidence(row, run_dir)
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
            # P1 (persona integration, doc 6.2): what kind of proof this is.
            # evidence_family and oracle_source are read straight off the
            # row (work_record.py already validated the vocabulary at
            # creation, or the row predates the field and carries none);
            # independence is recomputed here rather than trusted from the
            # row, so a hand-built or pre-P1 row still gets a true answer
            # instead of a stale or absent one.
            "evidence_family": str(row.get("evidence_family") or "") or NODATA,
            "oracle_source": str(row.get("oracle_source") or "") or NODATA,
            "independence": work_record.independence_for(
                row.get("oracle_source")),
            # P9 (doc 12.6): the three identity facts, stamped straight from
            # what build_report already computed; NODATA when this call
            # never got them (every caller that predates this row).
            "target_revision": target_revision or NODATA,
            "env_lock": env_lock or NODATA,
            "data_identity": (data_identity_by_id or {}).get(uid, NODATA),
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
        elif exit_code == 0 and command and e18_gap(row):
            # P6 (doc E18/12.6): a green, dependency-proven check on an E18
            # unit is still not proof of ITS OWN claim (a metric against a
            # baseline) without the evidence file the check was supposed to
            # write; refused here, last, only after every other reason a
            # PASS could be hollow has already been cleared.
            receipt["state"] = "no-data"
            receipt["reason"] = e18_gap(row)
        elif exit_code == 0 and command and numbers_gap:
            # P7 (persona plan section 2, the verify stage): a green,
            # dependency-proven check on an E8 or E2 unit is still not proof
            # of ITS OWN claim (a decision figure) without a numbers-
            # manifest.json that products/brothersbe/tools/sbe_gate.py's own
            # gate_numbers verifies; refused here, last, only after every
            # other reason a PASS could be hollow has already been cleared,
            # in the gate's own words.
            receipt["state"] = "no-data"
            receipt["reason"] = numbers_gap
        elif exit_code == 0 and command:
            receipt["state"] = "verified"
            if str(row.get("evidence_family") or "") == "E18":
                receipt["e18_evidence"] = row.get("e18_evidence")
            if numbers_message:
                receipt["numbers_manifest_verdict"] = numbers_message
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
    # E59: ONE event per call, not one per receipt, because this is a
    # READ-TIME PROJECTION: brother_run builds the same receipts several
    # times for one run (the report, the acceptance screen, the exit code,
    # measured at four calls on a two-unit run), and one event per unit per
    # call would say a receipt was issued four times when it was computed
    # four times. `run_dir` was already read from the environment above, for
    # P7's own evidence-file read; called outside a run (a test reading
    # receipts off a fixture, `run_dir` empty) nothing is written here.
    # E60: "verified" alongside the existing counts, the exact predicate
    # board_status.receipts_bound() already counts (receipt["state"] ==
    # "verified", never merely exit_code == 0: a check that already passed
    # before the work, or that touched no file, exits 0 and is still
    # "no-data" above). Additive only: "receipts" and "unproven" are
    # unchanged, so nothing that already reads this payload sees a
    # different shape. This is what lets receipts_bound(from_journal=True)
    # fold a run's LAST receipt.issued event instead of opening its
    # claims.json and Work document to recompute the same count.
    journal.append(run_dir, "receipt.issued",
                   parent_ids=journal.previous(run_dir),
                   payload={"receipts": len(out),
                            "unproven": sum(1 for r in out
                                            if r.get("exit_code") != 0),
                            "verified": sum(1 for r in out
                                            if r.get("state") == "verified")})
    return out


#: The remote this checkout treats as the public export target (see
#: docs/plan/claims.json history and the estate's own rule: origin is
#: public, hub is where development happens, scripts/export_public.py is
#: the only door between them). Read once here, not hardcoded three ways.
PUBLIC_REMOTE_REF = "origin/main"


def harness_label(harness_revision, repo=None, public_ref=None):
    """(E79, harness-revision-v1 follow-up) The one clause a receipt prints
    for the harness that produced it: 'harness <sha12>' only when that
    exact commit is an ancestor of the public export remote's own HEAD in
    THIS checkout, 'harness <sha12> (private hub revision)' otherwise, and
    plain 'harness NO-DATA' when no revision was recorded at all.

    A hub commit is never mirrored byte for byte into the public repo
    (scripts/export_public.py builds new commits from a gated tree, it
    does not push the hub's own shas), so an unresolvable sha is the
    ordinary case for work still in flight, not an edge case to guess
    past. Never a network call: git looks only at refs this checkout
    already has, so a checkout that never fetched the public remote
    reports private rather than fetching to find out."""
    revision = str(harness_revision or "")
    if not revision or revision.startswith(NODATA):
        return "harness %s" % NODATA
    short = revision[:12]
    repo = repo or _ROOT
    public_ref = public_ref or PUBLIC_REMOTE_REF
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, public_ref],
            cwd=repo, capture_output=True, text=True, timeout=10)
        resolvable = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        resolvable = False
    return ("harness %s" % short if resolvable
            else "harness %s (private hub revision)" % short)


#: The facts a per-file check entry must carry for require_per_file_checks
#: to accept it (E79). file and check_command are the two a stranger needs
#: to re-run anything; exit_code and output_location may legitimately be
#: absent on a no-data unit (nothing ran to completion), so only these two
#: are refused for being missing.
_REQUIRED_CHECK_FIELDS = ("file", "check_command")


def per_file_checks(record, receipts):
    """One entry per changed file, built straight from this run's own
    receipts (E79, the delivery-proof skeptic finding): the README and the
    stored delivery record named two files and two PASS units with no
    check a stranger could re-run. Every entry here carries the exact
    check command, its captured exit code, where the full output lives,
    and the before-and-after discrimination the receipt already computed
    (row.check_passed_before plus the receipt's own state and reason) so a
    reader does not have to trust a bare PASS.

    A unit whose files were never recorded (or that changed none)
    contributes no entry: this never invents a file nobody touched. Pure
    reshaping of receipts_for's own output; it runs no check itself."""
    rows = record.get("rows") or record.get("units") or []
    receipt_by_id = {r["id"]: r for r in receipts}
    entries = []
    for row in rows:
        receipt = receipt_by_id.get(row.get("id"))
        if receipt is None:
            continue
        for f in (row.get("files_changed_by_unit") or []):
            entries.append({
                "file": f,
                "unit": receipt["id"],
                "check_command": receipt.get("command") or "",
                "exit_code": receipt.get("exit_code"),
                "output_location": receipt.get("output_location") or NODATA,
                # The before-and-after discrimination (P1/E40): True only
                # when this exact check was shown to fail before the work
                # and pass after; False when it was measured and did not;
                # None when that measurement was never made at all (a
                # harness older than the stamp, or a no-data unit).
                "check_passed_before": row.get("check_passed_before"),
                "state": receipt.get("state"),
                "reason": receipt.get("reason") or "",
            })
    return entries


def require_per_file_checks(checks):
    """(True, "") when `checks` is a non-empty list where every entry names
    at least the file it covers and the check_command that decided it;
    otherwise (False, reason) naming exactly what is missing, by field
    name, per the row's own done_check: 'scripts/test_receipt_door.py
    refuses a record with no per-file check'. This is the one gate a
    delivery record must pass before accept_delivery.py writes it with
    per-file evidence attached."""
    if not isinstance(checks, list) or not checks:
        return False, ("no per-file checks: a delivery record must name "
                       "the check that decided each changed file")
    for i, entry in enumerate(checks):
        if not isinstance(entry, dict):
            return False, "checks[%d] is not an object" % i
        missing = [f for f in _REQUIRED_CHECK_FIELDS
                  if not str(entry.get(f) or "").strip()]
        if missing:
            return False, ("checks[%d] (file=%r) is missing %s"
                           % (i, entry.get("file"), ", ".join(missing)))
    return True, ""


#: E75: the dependency manifests, spelled once and read twice below (a
#: changed manifest is both a REVIEW FIRST path and the "new dependency"
#: cognitive-debt signal). Anchored at a path segment and at the end, so
#: "docs/package.json.md" is not a manifest.
DEPENDENCY_MANIFESTS = (
    r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg"
    r"|package\.json|package-lock\.json|yarn\.lock|Pipfile|Pipfile\.lock"
    r"|poetry\.lock|go\.mod|go\.sum|Gemfile|Gemfile\.lock|Cargo\.toml"
    r"|Cargo\.lock)$")

#: E75 (acceptance compression): the classes that put a CHANGED PATH at the
#: top of a reviewer's list. A second, independent reading of the same run:
#: RISK_TRIGGERS above matches what a unit SAID about itself, and a diff can
#: cross a boundary the unit's own words never named. Bounded on path
#: separators for the reason RISK_TRIGGERS is word-bounded: "author.py" is
#: not auth, "monetary.md" is not money, and a screen that cries wolf on
#: every commit is a screen nobody opens twice.
RISK_PATHS = (
    ("auth", r"(^|[/_.-])(auth|authn|authz|login|logout|session|sessions"
             r"|credential|credentials|secret|secrets|token|tokens"
             r"|permission|permissions|middleware)([/_.-]|$)"),
    ("money", r"(^|[/_.-])(money|payment|payments|billing|invoice|invoices"
              r"|pricing|price|refund|refunds|currency|payout|payouts"
              r"|checkout|ledger)([/_.-]|$)"),
    ("migration", r"(^|[/_.-])(migration|migrations|migrate|backfill|schema"
                  r"|alembic)([/_.-]|$)"),
    ("parsing", r"(^|[/_.-])(parse|parser|parsers|parsing|lexer|grammar"
                r"|decode|decoder|encode|encoder|codec|serializer"
                r"|serialize|deserialize)([/_.-]|$)"),
    ("concurrency", r"(^|[/_.-])(concurrency|concurrent|thread|threads"
                    r"|threading|async|asyncio|lock|locks|locking|mutex"
                    r"|queue|queues|worker|workers|scheduler)([/_.-]|$)"),
    ("dependency manifest", DEPENDENCY_MANIFESTS),
)

#: E75.3: the paths that read as a new abstraction. The same kind of reading
#: as RISK_PATHS, a different question: not "is this dangerous" but "does
#: this change cost the next reader an indirection they did not have."
ABSTRACTION_PATHS = (
    r"(^|[/_.-])(abstract|abstracts|base|interface|interfaces|iface|factory"
    r"|factories|adapter|adapters|provider|providers|registry|manager"
    r"|wrapper|mixin|mixins|strategy|proxy|facade)([/_.-]|$)")

#: The four sections, in the order a reviewer reads them. The names are the
#: row's own (E75.2), spelled once so the screen, the record and the tests
#: never drift into three variants of the same heading.
REVIEW_FIRST = "REVIEW FIRST"
LOW_RISK_MECHANICAL = "LOW-RISK MECHANICAL"
NOT_PROVEN = "NOT PROVEN"
NO_NEED_TO_RE_READ = "NO NEED TO RE-READ"
READING_SECTIONS = (REVIEW_FIRST, LOW_RISK_MECHANICAL, NOT_PROVEN,
                    NO_NEED_TO_RE_READ)

#: What an empty section says. An empty section is still PRINTED (E75.2): a
#: heading that disappears when it holds nothing reads, to the person in
#: front of it, exactly like a heading nobody ever computed.
EMPTY_SECTION = "No file in this run landed here."


def path_risk(path):
    """The name of the first RISK_PATHS class this path hits, or "". A
    pattern match over the path itself, never a judgement: the same path
    always sorts the same way, which is what makes the section testable
    both ways."""
    text = str(path or "").lower()
    for name, pattern in RISK_PATHS:
        if re.search(pattern, text):
            return name
    return ""


def declared_untouched(record, receipts):
    """[{"unit", "path"}] for every path a unit declared it owns that no
    file it actually changed lands under: the files a reviewer does not have
    to re-read, straight off the two lists the run already recorded."""
    rows = record.get("rows") or record.get("units") or []
    changed_by_unit = {}
    for entry in per_file_checks(record, receipts):
        changed_by_unit.setdefault(entry["unit"], set()).add(entry["file"])
    untouched = []
    for row in rows:
        uid = row.get("id")
        touched = changed_by_unit.get(uid, set())
        for path in (row.get("owns") or row.get("writes") or []):
            path = str(path).rstrip("/")
            if not any(f == path or f.startswith(path + "/")
                       for f in touched):
                untouched.append({"unit": uid, "path": path})
    return untouched


def reading_order(record, receipts):
    """E75.1: {section: [{"path", "unit", "why"}]} for all four sections,
    every section present even when empty, derived from the run's own
    changed-file list, each row's declared scope and each receipt's state.
    Nothing here reads prose and nothing here asks a model, so the same run
    always produces the same order.

    The precedence, top of the list downwards, and why it runs that way:

      REVIEW FIRST         a path outside the unit's declared scope (or a
                           unit that declared none), or a path naming one of
                           the risk classes. Risk wins over proof: an
                           unproven risky file belongs at the top of the
                           list rather than in its own quieter section, and
                           its reason says the check proved nothing.
      NOT PROVEN           anything else whose own receipt is not verified.
      LOW-RISK MECHANICAL  a declared, risk-free path whose check proved it.
      NO NEED TO RE-READ   a path the unit declared and never touched.
    """
    order = {name: [] for name in READING_SECTIONS}
    rows = record.get("rows") or record.get("units") or []
    row_by_id = {row.get("id"): row for row in rows}
    for entry in per_file_checks(record, receipts):
        path, unit = entry["file"], entry["unit"]
        row = row_by_id.get(unit) or {}
        declared = [str(p) for p in (row.get("owns") or row.get("writes")
                                     or [])]
        proven = entry.get("state") == "verified"
        risk = path_risk(path)
        if not declared:
            section = REVIEW_FIRST
            why = "the unit that changed it declared no scope at all"
        elif not scope_audit.covered(path, declared):
            section = REVIEW_FIRST
            why = ("outside the scope unit %s declared (%s)"
                   % (unit, ", ".join(declared)))
        elif risk:
            section = REVIEW_FIRST
            why = "the path names %s" % risk
        elif not proven:
            section = NOT_PROVEN
            why = entry.get("reason") or "its check proved nothing"
        else:
            section = LOW_RISK_MECHANICAL
            why = ("no risk class in the path, and the check that decided "
                   "it exited %s" % entry.get("exit_code"))
        if section == REVIEW_FIRST and not proven:
            why += ", and its own check proved nothing"
        order[section].append({"path": path, "unit": unit, "why": why})
    for item in declared_untouched(record, receipts):
        order[NO_NEED_TO_RE_READ].append({
            "path": item["path"], "unit": item["unit"],
            "why": ("unit %s declared it and changed nothing under it"
                    % item["unit"])})
    return order


def cognitive_debt(record, receipts):
    """E75.3: {"count": n, "signals": [...]}, the debt this delivery leaves
    the next reader, counted off fields the run already recorded and never
    off anybody's prose. Three signals, each a fact about a changed path:

      new dependency   a dependency manifest is in the change set, so the
                       next reader inherits something this estate did not
                       build.
      new abstraction  a path naming an indirection (a base, a factory, an
                       adapter, a registry) is in the change set.
      scope drift      a changed file falls outside the scope its own unit
                       declared, or its unit declared no scope at all.

    INTERNAL, and deliberately so: the count rides on the machine receipt
    (receipt_record) and never becomes a flag, a mode or a screen. A number
    a person is shown is a number a person can be asked to hit, and this one
    is worth more unwatched than displayed.

    # ponytail: "new" is read as "present in this change set", because the
    # run records what changed and not what was created; a per-file created
    # flag on the claim's own evidence would sharpen it.
    """
    signals = []
    rows = record.get("rows") or record.get("units") or []
    row_by_id = {row.get("id"): row for row in rows}
    for entry in per_file_checks(record, receipts):
        path, unit = entry["file"], entry["unit"]
        low = str(path).lower()
        row = row_by_id.get(unit) or {}
        declared = [str(p) for p in (row.get("owns") or row.get("writes")
                                     or [])]
        if re.search(DEPENDENCY_MANIFESTS, low):
            signals.append({"signal": "new dependency", "path": path,
                            "unit": unit,
                            "why": "a dependency manifest changed"})
        if re.search(ABSTRACTION_PATHS, low):
            signals.append({"signal": "new abstraction", "path": path,
                            "unit": unit,
                            "why": "the path names an indirection"})
        if not declared or not scope_audit.covered(path, declared):
            signals.append({"signal": "scope drift", "path": path,
                            "unit": unit,
                            "why": ("outside the scope unit %s declared (%s)"
                                    % (unit, ", ".join(declared)
                                       or "it declared none"))})
    return {"count": len(signals), "signals": signals}


def _reading_sections(record, receipts):
    """The four sections in decide.render's own `sections` shape: a heading,
    the paths the classifier put under it (each with the reason it landed
    there), and the sentence an empty one prints instead of vanishing."""
    order = reading_order(record, receipts)
    return [{"heading": name,
             "items": ["%s (%s)" % (e["path"], e["why"])
                       for e in order[name]],
             "empty": EMPTY_SECTION}
            for name in READING_SECTIONS]


def _data_identity_text(data_identity):
    """P9's own rendering of receipts_for's `data_identity` field: the
    NO-DATA reason verbatim when the unit declared none (a plain string),
    or 'path=<sha256 or its own NO-DATA reason>' pairs, sorted by path and
    joined by commas, when it declared one or more."""
    if not isinstance(data_identity, dict):
        return str(data_identity) if data_identity else NODATA
    return ", ".join("%s=%s" % (path, value)
                     for path, value in sorted(data_identity.items()))


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
        # P6 (doc E18/12.6): the statistical evidence a bare exit code loses,
        # printed on the one line a person reads, exactly the four values
        # (metric with its value, baseline, seed, holdout) receipts_for only
        # ever attaches once e18_gap(row) has already come back empty.
        e18 = receipt.get("e18_evidence")
        if e18:
            body += (" %s %s against baseline %s, seed %s, holdout %s."
                     % (e18.get("metric"), e18.get("value"),
                        e18.get("baseline"), e18.get("seed"),
                        e18.get("holdout_id")))
        # P7: the numbers-manifest gate's own PASS sentence (it reads
        # "N figure(s) ... re-run to zero drift"), attached only once
        # receipts_for's own numbers_manifest_evidence() already came back
        # with a message, which only happens on the gate's own PASS.
        numbers_verdict = receipt.get("numbers_manifest_verdict")
        if numbers_verdict:
            body += " Numbers manifest: %s." % numbers_verdict
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
    # P1: evidence family, oracle source and independence beside the
    # verdict (doc 6.2), inserted before the harness clause so the existing
    # "harness X." tail (asserted verbatim elsewhere, including immediately
    # before brother_run.py's own appended " verdict: ...") stays intact.
    body += (" Evidence family %s, oracle %s, independence %s."
             % (receipt.get("evidence_family") or NODATA,
                receipt.get("oracle_source") or NODATA,
                receipt.get("independence") or NODATA))
    # P9 (doc 12.6): the target's own revision, the environment lock hash
    # and the declared data hashes, on the same line a person already reads
    # the rest of the receipt on, so a reproduction six weeks later never
    # has to open the run directory to find them.
    body += (" Target revision %s, environment lock %s, data identity %s."
             % (receipt.get("target_revision") or NODATA,
                receipt.get("env_lock") or NODATA,
                _data_identity_text(receipt.get("data_identity"))))
    # E79: harness_label resolves the sha against the public export remote
    # (or names it private) instead of printing a bare fragment that reads
    # as though any reader could look it up.
    return "%s Check written by %s, %s." % (
        body, receipt.get("author") or "the planning model",
        harness_label(receipt.get("harness_revision")))


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
        # E75.2: the four sections that order a reviewer's attention, above
        # the ranking, because the first question a reader has is which of
        # these files to open first and the answer is computed, not written.
        "sections": _reading_sections(record, receipts),
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
    # E59: the run directory as the caller gave it, kept before the line
    # below rebinds the name to the screens/ subdirectory. The event is
    # appended after both screens are written, at the bottom of this
    # function, so it records what actually landed.
    run_root = run_dir
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
    journal.append(run_root, "acceptance.screened",
                   parent_ids=journal.previous(run_root),
                   payload={"screens": len(written),
                            "release_triggered": bool(triggers)})
    return written, triggers


def receipt_record(run_dir_or_record, receipts, log_path=None):
    """E72.1 (docs/plan/READINESS-ROADMAP-2026-08-29.json row E72; steering
    doc docs/plan/STEERING-TRUSTED-DELEGATION-2026-09-03.md section 16,
    WORKSTREAM A): the one machine view a reviewer with no transcript reads
    to answer the eight acceptance questions (doc lines 1533-1544), built
    from the run record, the claim store and the journal, reusing exactly
    what the engine already produces and parsing none of the three a second
    time: receipts_for's own per-unit receipts (`receipts`, computed by the
    caller, never rebuilt here), per_file_checks and mark_for (this same
    module), risk_triggers (this same module), journal.read plus
    journal_projection.claims_from_journal (the journal), and
    continuity.capsule (the journal, the Work document and claims.json,
    already folded).

    `run_dir_or_record` is either a run directory (a string, the same shape
    journal.run_dir_from_env() hands brother_run.py) or an already-loaded
    Work document (a dict, what receipts_for's own callers usually already
    hold). A run directory loads its own record from the one non-bookkeeping
    *.json file inside it (continuity._work_doc_path's own rule, reused
    rather than re-walked); a record given directly skips that read, and the
    run directory is then whatever journal.run_dir_from_env() reports (""
    outside a run, exactly journal.append's own "nothing to journal" case).

    Question (doc line)           Field             Built from
    -----------------------------  ----------------  ------------------------
    What changed? (1536)           scope             per_file_checks() (the
                                                       claim store, via
                                                       receipts) for what
                                                       changed; each row's own
                                                       declared owns/writes
                                                       left untouched by any
                                                       changed file for what
                                                       was deliberately left
                                                       alone
    Why? (1537)                    intent             record["outcome"] (the
                                                       run record) plus every
                                                       receipt's own objective
    What proves it? (1538)         evidence            the receipts in state
                                                       "verified": command,
                                                       exit code, author,
                                                       evidence_family,
                                                       independence and the
                                                       row's own
                                                       check_passed_before
                                                       (before-and-after
                                                       discrimination)
    What does not have proof?      unproven           the receipts in state
    (1539)                                            "no-data" or "refused",
                                                       each with its own
                                                       reason verbatim and
                                                       mark_for()'s own mark,
                                                       which is None (never a
                                                       number) for every one
                                                       of them, a zero-change
                                                       unit included
    What failed and was repaired?  repair_history     the journal's own
    (1540)                                            attempt.traced and
                                                       check.rewritten events,
                                                       folded with
                                                       journal_projection.
                                                       claims_from_journal's
                                                       own attempt count, one
                                                       entry per unit that
                                                       actually needed repair
    What should I inspect first?   attention          risk_triggers() (this
    (1541)                                            module) over the run
                                                       record's rows, plus the
                                                       id of every unproven
                                                       receipt
    Was the run contained? (1542)  containment        scope_audit.covered()
                                                       (the claim store's own
                                                       changed-file list, via
                                                       per_file_checks) tested
                                                       against each row's own
                                                       declared owns/writes
    Can I reproduce/continue?      continuity         continuity.capsule()
    (1543)                                            (the journal, the Work
                                                       document and
                                                       claims.json), plus the
                                                       receipts' own P9
                                                       target_revision and
                                                       env_lock

    A unit whose evidence proves nothing (receipts_for's own "no-data"
    state, the zero-change and check-discrimination cases included) lands
    in `unproven` with mark_for() reading None: this receipt never reports a
    mark for a unit that has none, whatever its exit code was."""
    # Local imports: continuity imports brother_run, and brother_run imports
    # THIS module at its own load time (line ~120), so importing continuity
    # at module load here would cycle back on itself. Importing at call
    # time, after every module has finished loading, sidesteps the cycle
    # instead of fighting it; several acceptance_*.py files already use the
    # same trick for the same reason.
    import continuity
    import journal_projection

    if isinstance(run_dir_or_record, dict):
        record = run_dir_or_record
        run_dir = journal.run_dir_from_env()
    else:
        run_dir = str(run_dir_or_record or "")
        doc_path = continuity._work_doc_path(run_dir) if run_dir else None
        record = None
        if doc_path:
            record, problem = continuity._read_json(doc_path)
            if record is None:
                sys.stderr.write("receipt_door: could not read %s (%s)\n"
                                 % (doc_path, problem))
        record = record or {}

    rows = record.get("rows") or record.get("units") or []
    row_by_id = {row.get("id"): row for row in rows}
    receipts = receipts or []

    # Q1: what changed, and what was declared but deliberately left alone.
    changed = per_file_checks(record, receipts)
    scope = {"changed": changed,
             "declared_untouched": declared_untouched(record, receipts)}

    # Q2: why, in the run's own words and each unit's own objective.
    intent = {"outcome": record.get("outcome") or NODATA,
              "units": [{"id": r["id"], "objective": r.get("objective") or NODATA}
                       for r in receipts]}

    # Q3/Q4: proof, and its absence. mark_for is the single source of the
    # mark; a zero-change or check-discriminated unit is "no-data" there and
    # reads None here, never a number.
    evidence, unproven = [], []
    for r in receipts:
        mark, why = mark_for(r)
        row = row_by_id.get(r["id"]) or {}
        entry = {"id": r["id"], "state": r["state"], "mark": mark,
                 "command": r.get("command") or NODATA,
                 "exit_code": r.get("exit_code"),
                 "check_passed_before": row.get("check_passed_before"),
                 "author": r.get("author") or NODATA,
                 "evidence_family": r.get("evidence_family") or NODATA,
                 "independence": r.get("independence") or NODATA,
                 "output_location": r.get("output_location") or log_path
                 or NODATA,
                 "why": why}
        if r["state"] == "verified":
            evidence.append(entry)
        else:
            entry["reason"] = r.get("reason") or NODATA
            unproven.append(entry)

    # Q5: repair history, folded from the journal's own attempt and
    # check-rewrite events; only units that actually needed repair appear.
    events = journal.read(run_dir) if run_dir else None
    journal_claims = journal_projection.claims_from_journal(events or ())
    rewritten = {}
    for event in events or ():
        if event.get("type") == "check.rewritten" and event.get("unit_id"):
            rewritten[event["unit_id"]] = bool(
                (event.get("payload") or {}).get("still_looks_broken"))
    repair_history = []
    for uid in sorted(set(journal_claims) | set(rewritten)):
        attempt = (journal_claims.get(uid) or {}).get("attempt")
        was_rewritten = uid in rewritten
        if (isinstance(attempt, int) and attempt > 1) or was_rewritten:
            repair_history.append({
                "unit": uid,
                "attempts": attempt if attempt is not None else NODATA,
                "check_rewritten": was_rewritten,
                "still_looks_broken": rewritten.get(uid),
            })

    # Q6: where a reviewer should look first. risk_triggers is the same
    # deterministic pattern match the release screen already uses; every
    # unproven unit is worth a first look by definition.
    attention = {
        "risk_triggers": [{"class": name, "unit": uid, "words": words}
                          for name, uid, words in risk_triggers(rows)],
        "unproven_units": [e["id"] for e in unproven],
        # E75: the same four sections the acceptance screen prints, on the
        # machine receipt too, so a reviewer with no browser reads the same
        # order, and the internal debt count that never becomes a mode.
        "reading_order": reading_order(record, receipts),
        "cognitive_debt": cognitive_debt(record, receipts),
    }

    # Q7: was the run contained. Every changed file (the claim store's own
    # account, via per_file_checks) tested against the row that changed it
    # declared writing there at all; scope_audit.covered() is the exact test
    # the estate's own scheduler uses, reused rather than reimplemented.
    crossings, undeclared_units, seen_undeclared = [], [], set()
    for entry in changed:
        row = row_by_id.get(entry["unit"]) or {}
        declared = [str(p) for p in (row.get("owns") or row.get("writes")
                                     or [])]
        if not declared:
            if entry["unit"] not in seen_undeclared:
                undeclared_units.append(entry["unit"])
                seen_undeclared.add(entry["unit"])
            continue
        if not scope_audit.covered(entry["file"], declared):
            crossings.append({"unit": entry["unit"], "file": entry["file"],
                              "declared": declared})
    containment = {
        "boundary_crossings": crossings,
        "undeclared_scope_units": undeclared_units,
        "contained": not crossings and not undeclared_units,
    }

    # Q8: can this be reproduced or continued. continuity.capsule folds the
    # journal, the Work document and claims.json into the one resume view;
    # target_revision/env_lock ride on every receipt already (P9), read off
    # the first one since build_report stamps them once per run.
    if run_dir:
        cap, cap_problem = continuity.capsule(run_dir)
    else:
        cap, cap_problem = None, ("%s: no run directory was given to build "
                                  "a continuity capsule from" % NODATA)
    identity = receipts[0] if receipts else {}
    continuity_state = {
        "capsule": cap,
        "problem": cap_problem or "",
        "target_revision": identity.get("target_revision", NODATA),
        "env_lock": identity.get("env_lock", NODATA),
    }

    return {
        "scope": scope,
        "intent": intent,
        "evidence": evidence,
        "unproven": unproven,
        "repair_history": repair_history,
        "attention": attention,
        "containment": containment,
        "continuity": continuity_state,
    }


#: E72.2: the eight acceptance questions of the trusted-delegation steering
#: doc (docs/plan/STEERING-TRUSTED-DELEGATION-2026-09-03.md section 16,
#: WORKSTREAM A, lines 1536 to 1543) in the order receipt_record()'s own
#: docstring table lists them, each bound to the ONE field of that record
#: which answers it. The page below is built from this table and from
#: nothing else, so a field added to the machine view and not to this table
#: is visibly missing from the page rather than quietly absent from both.
RECEIPT_QUESTIONS = (
    ("scope", "What changed?"),
    ("intent", "Why was this run made?"),
    ("evidence", "What proves it?"),
    ("unproven", "What does not have proof?"),
    ("repair_history", "What failed and was repaired?"),
    ("attention", "What should I inspect first?"),
    ("containment", "Was the run contained?"),
    ("continuity", "Can I reproduce this, or continue it?"),
)


def _answer(field, view, log_path, record_path):
    """(answer, where, answered) for one acceptance question.

    `answered` is False only where the record itself has nothing to report,
    and that case renders as NO-DATA. An empty repair history, an empty
    unproven list and a clean containment verdict are ANSWERS, not absences:
    calling them NO-DATA would turn a run that needed no repair into a run
    nobody can speak about."""
    where = "%s, field %r" % (record_path or NODATA, field)
    if field == "scope":
        changed = view["scope"]["changed"]
        untouched = view["scope"]["declared_untouched"]
        if not changed:
            return ("%s: this run's claim store recorded no changed file at "
                    "all" % NODATA, where, False)
        files = sorted({e["file"] for e in changed})
        return ("%d file(s) changed by %d unit(s): %s. %d declared path(s) "
                "were left untouched on purpose: %s"
                % (len(files), len({e["unit"] for e in changed}),
                   ", ".join(files), len(untouched),
                   ", ".join("%s (%s)" % (u["unit"], u["path"])
                             for u in untouched) or "none"),
                where, True)
    if field == "intent":
        outcome = view["intent"]["outcome"]
        units = view["intent"]["units"]
        if outcome == NODATA:
            return ("%s: the run record carried no outcome sentence" % NODATA,
                    where, False)
        return ("%s. The units asked for: %s"
                % (outcome, "; ".join("%s (%s)" % (u["id"], u["objective"])
                                      for u in units) or "none"),
                where, True)
    if field == "evidence":
        rows = view["evidence"]
        if not rows:
            return ("%s: no unit of this run carried a check that re-executed "
                    "and exited 0" % NODATA, where, False)
        return ("%d unit(s) proved by their own re-executed check: %s"
                % (len(rows),
                   "; ".join("%s ran %r, exit %s, author %s, family %s, "
                             "independence %s"
                             % (r["id"], r["command"], r["exit_code"],
                                r["author"], r["evidence_family"],
                                r["independence"]) for r in rows)),
                "the full untrimmed output of every check is in %s"
                % (log_path or NODATA), True)
    if field == "unproven":
        rows = view["unproven"]
        if not rows:
            return ("every unit of this run carries proof; nothing is "
                    "unproven", where, True)
        return ("%d unit(s) without proof, each unmarked rather than scored: "
                "%s" % (len(rows), "; ".join("%s (%s: %s)"
                                             % (r["id"], r["state"],
                                                r.get("reason") or NODATA)
                                             for r in rows)),
                where, True)
    if field == "repair_history":
        rows = view["repair_history"]
        if not rows:
            return ("nothing in this run needed a second attempt or a "
                    "rewritten check", where, True)
        return ("%d unit(s) were repaired: %s"
                % (len(rows), "; ".join(
                    "%s (%s attempt(s), check rewritten: %s)"
                    % (r["unit"], r["attempts"],
                       "yes" if r["check_rewritten"] else "no")
                    for r in rows)),
                where, True)
    if field == "attention":
        triggers = view["attention"]["risk_triggers"]
        unproven = view["attention"]["unproven_units"]
        if not triggers and not unproven:
            return ("nothing in this run named a risk class and every unit "
                    "carries proof, so there is no first place to look",
                    where, True)
        return ("risk classes named by the units themselves: %s. Units "
                "without proof, worth a first look by definition: %s"
                % ("; ".join("%s (unit %s, on the words: %s)"
                             % (t["class"], t["unit"], t["words"])
                             for t in triggers) or "none",
                   ", ".join(unproven) or "none"),
                where, True)
    if field == "containment":
        con = view["containment"]
        if con["contained"]:
            return ("yes: every changed file sits inside the scope the unit "
                    "that changed it declared", where, True)
        return ("no: %d boundary crossing(s) (%s) and %d unit(s) that "
                "declared no scope at all (%s)"
                % (len(con["boundary_crossings"]),
                   "; ".join("%s wrote %s" % (c["unit"], c["file"])
                             for c in con["boundary_crossings"]) or "none",
                   len(con["undeclared_scope_units"]),
                   ", ".join(con["undeclared_scope_units"]) or "none"),
                where, True)
    if field == "continuity":
        con = view["continuity"]
        if con["capsule"] is None:
            return ("%s: %s" % (NODATA, con["problem"] or
                                "no continuity capsule was built"),
                    where, False)
        buckets = (con["capsule"].get("buckets") or {})
        return ("the run's own capsule is on disk, target revision %s, "
                "environment lock %s, integrated units: %s"
                % (con["target_revision"], con["env_lock"],
                   ", ".join(buckets.get("integrated") or []) or "none"),
                where, True)
    return ("%s: no reader is written for field %r" % (NODATA, field),
            where, False)


def receipt_answers(view, log_path=None, record_path=None):
    """The eight questions answered from `view` (a receipt_record()), one
    entry per question: key, question, answer, where the full output lives,
    and whether the record answered it at all. THE ONE PLACE the answers are
    worded, so the page (receipt_spec) and the block the run prints
    (receipt_text) cannot say different things about the same run."""
    out = []
    for field, question in RECEIPT_QUESTIONS:
        answer, where, answered = _answer(field, view, log_path, record_path)
        out.append({"key": field, "question": question, "answer": answer,
                    "where": where, "answered": answered})
    return out


def receipt_spec(view, record, before=None, after=None, log_path=None,
                 record_path=None):
    """E72.2: the human view of `view` (a receipt_record()), as a decide.py
    spec write_screen() renders. Built from the record and nothing else, so
    the page and the schema cannot disagree; the full record travels beside
    the page as write_screen's own JSON sibling.

    THE MARK ON THIS PAGE MEANS ONE THING ONLY: the record answered that
    question (10) or it did not (unmarked, rendered NO-DATA, contributing
    nothing). It is not a quality score, and it does not restate the
    acceptance screen's own marks, which are the ones that come out of
    MARK_TABLE."""
    answers = receipt_answers(view, log_path, record_path)
    answered = [a for a in answers if a["answered"]]
    each = 1.0 / len(answers) if answers else 0.0
    return {
        "title": "The receipt for this delivery",
        "eyebrow": "Receipt",
        "stamp": _stamp(record, before, after),
        "plain_summary": (
            "This is the whole of what this run did, as eight answers a "
            "reviewer with no transcript can read in minutes: what changed, "
            "why, what proves it, what has no proof, what was repaired, what "
            "to look at first, whether the run stayed inside the scope it "
            "declared, and whether you can continue it. %d of the %d "
            "questions are answered by the record; the rest read %s, which "
            "counts as nothing rather than as a low score. Each answer names "
            "where its full output lives instead of pasting a trimmed copy."
            % (len(answered), len(answers), NODATA)),
        "question": ("Nothing on this page asks you for a decision: the "
                     "acceptance screen beside it does that. This page "
                     "answers the eight questions, and every answer is a "
                     "field of the same record a machine reads."),
        "criteria": [{"key": a["key"],
                      "label": "%s (%s)" % (a["question"], a["key"]),
                      "weight": each,
                      "why": ("answered by receipt_record()[%r]; %s"
                              % (a["key"], a["where"]))}
                     for a in answers],
        "options": [{
            "id": "as-recorded",
            "name": "This run, as its own record tells it",
            "one_liner": ("the eight acceptance questions, each answered from "
                          "one field of this run's receipt record"),
            "cost": "already spent: this run is finished",
            "reversible": "the reading is; the run already happened",
            "pros": ["%s %s" % (a["question"], a["answer"])
                     for a in answered] or ["the record answered nothing"],
            "cons": ["%s %s" % (a["question"], a["answer"])
                     for a in answers if not a["answered"]]
                    or ["every question is answered by the record"],
            "scores": {a["key"]: 10.0 for a in answered},
            "score_basis": {a["key"]: a["answer"] for a in answers},
            "sources": [{"what": a["question"], "where": a["answer"],
                         "found_in": a["where"]} for a in answers],
        }],
        "would_change": [
            "A question reading %s gets its field filled by a run that "
            "actually recorded it: the mark moves from unmarked to 10 by the "
            "same reading, with nobody's judgement in between." % NODATA,
            "You open the record beside this page and one of its fields "
            "disagrees with the sentence above it. Both come from the same "
            "dict, so that would be a defect in this file, not a matter of "
            "opinion.",
        ],
        "footer": ("Rendered from this run's own receipt record by "
                   "scripts/receipt_door.py. The record itself is the JSON "
                   "beside this page; the full, untrimmed output of every "
                   "check is in %s." % (log_path or NODATA)),
    }


def receipt_text(view, log_path=None, record_path=None, page_path=None):
    """The same eight answers as plain text, for the end of a run. One
    block, no second wording: receipt_answers() is the source of both."""
    lines = ["  the receipt for this delivery, in eight answers:"]
    for a in receipt_answers(view, log_path, record_path):
        lines.append("    %s %s" % (a["question"], a["answer"]))
    lines.append("    the page: %s" % (page_path or NODATA))
    return "\n".join(lines)


def render_receipt_screen(record, receipts, run_dir, before=None, after=None,
                          log_path=None):
    """(path_or_problem, view, text). The receipt page written beside the
    run's other screens, the machine view it was rendered from, and the
    block the run prints.

    `record` is passed to receipt_record() as the dict it is, never as the
    run directory: at the end of a run the in-memory record carries the unit
    states and changed files the document on disk may not have been rewritten
    with, and a page built from the stale copy would disagree with the
    acceptance screen built from this one. receipt_record() then reads the
    run directory from BROTHER_RUN_DIR (brother_run.py exports it when the
    run opens), which is the same directory, and reports NO-DATA continuity
    outside a run rather than guessing.

    Never raises: write_screen returns its own problem string, and a receipt
    that fails to render must not fail the delivery it describes."""
    view = receipt_record(record, receipts, log_path)
    out_path = os.path.join(run_dir, "screens", "delivery-receipt.html")
    record_path = os.path.splitext(out_path)[0] + ".json"
    spec = receipt_spec(view, record, before, after, log_path, record_path)
    spec["receipt_record"] = view
    path, problem = write_screen(spec, out_path)
    return (path or problem), view, receipt_text(view, log_path, record_path,
                                                 path)
