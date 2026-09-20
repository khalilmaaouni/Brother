#!/usr/bin/env python3
"""jev_checks: the six Jev wave-1 seams named in the wave-1 seam brief
(docs/decisions'-worth of research at
Documents/BrotherArchive/jev-deep-research-2026-09-18/wave2/seam-brief-
common.md) that have no call site of their own yet, or that gained one in
this same change: one thin function per registry entry, each doing
nothing but shape its own named arguments into a jev_seam.consult() call
and hand back whatever consult() returns.

WHY A SEPARATE MODULE FROM jev_seam.py. jev_seam.py is the one place that
decides mode, writes the calibration ledger, and runs the canary and
near-threshold checks -- see its own docstring. It knows nothing about
what a "row claim" or a "gate log line" IS: `state` is an opaque dict as
far as consult() is concerned. Every harness call site that wants to ask
J030, J064, J014, J063, J102 or J117 would otherwise have to know that
registry entry's own state shape and build it correctly by hand, at every
call site, forever. This module is that one place: J030's state shape
(claim, source_excerpt) is decided here once, not re-typed at a front
door, at loop_bridge.py, and in a test fixture three ways that quietly
drift apart.

EVERY FUNCTION HERE IS A CALL SITE (rule 2 of the wave-1 seam brief), so
every one of them follows the same shape: build a small state dict from
its own named arguments (never a whole file, never a secret -- rule 3),
resolve the registry entry ONLY by its id (rule 4, the question, its
options and its risk come from the registry, never re-typed here), and
never let an exception reach the caller. jev_seam.consult() already never
raises for any DOCUMENTED failure (see its own CONTINGENCY table); the
try/except in _consult() below is defence for the undocumented case -- a
transitive import that broke, a bug in this module's own state shaping --
so a caller of ANY function below can never be broken by this one,
whatever goes wrong on the Jev side of it.

MODE IS DECIDED BY data/jev-seams.json, NEVER HERE. As of this change
that file's "modes" map names none of J030/J064/J014/J063/J102/J117:
every function below therefore makes no call at all until a human edits
that file, in a change separate from this one, per the wave-1 brief's
rule 1 ("the seam ships with its mode OFF").

SIX FUNCTIONS, ONE PER ENTRY:
  check_row_claim_support(claim, source_excerpt, current_answer, ...)       J030
  check_gate_log_lines(lines, current_answer, ...)                         J064
  check_front_door_skill(request_text, available_skills,
                          current_answer, ...)                             J014
  check_worker_completion_claim(note, changed_paths, current_answer, ...)  J063
  check_done_claim_verification(note, quotes_verification,
                                 current_answer, ...)                      J102
  check_scope_audit(objective, changed_path, current_answer, ...)          J117

Every one of them returns whatever jev_seam.consult() would (a
SeamResult), or this module's own off-shaped stand-in when jev_seam
itself could not even be imported or raised something undocumented --
never anything else, so a caller reads `.answer`, `.mode`, `.reason`,
`.audit`, `.decision_id` the same way regardless of which check it
called.

check_gate_log_lines is the one plural function. J064's own registry
entry says it is cheapest run "batched across the whole output" against
the real bridge, but jev_seam.consult() asks exactly one question per
call (see its own signature: one `state`, one `current_answer`). Batching
here means this function loops over `lines` and calls consult() once per
line -- each line gets its own decision_id (consult()'s own state hash,
see jev_seam._decision_id), so nothing is lost by not sending them in one
wire request; it is a caller convenience, not a different wire shape.

Python 3.9 floor, standard library only, no network in this module itself
(exactly like jev_seam.py: network lives inside decide()'s bridge
subprocess, never here).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    import jev_seam as _seam
except Exception:  # noqa: BLE001  # rule 2: an import failure must never raise into a caller
    _seam = None

#: Defaults for the CLI (main(), below) and for any caller that does not
#: keep its own copy of these paths (loop_bridge.py's two call sites use
#: DEFAULT_LEDGER_DIR exactly this way). REUSED from jev_seam.py, never
#: re-derived here: DEFAULT_LEDGER_DIR in particular must be the ONE path
#: jev_seam.py itself resolves it to -- a machine-level directory under
#: JEV_STATE_DIR (default ~/.brother/jev, override BROTHER_JEV_STATE_DIR),
#: never a path inside this checkout. jev_seam.py's own module docstring
#: ("item 1") explains why: the human-labelled calibration ledger is a
#: fact about the outside world, not tree content a `git clean -X` or a
#: worktree removal is entitled to discard, and it must be the SAME
#: directory regardless of which checkout or worktree a seam consult()
#: runs from. A second, checkout-local copy of that constant here would
#: silently reopen the exact defect jev_seam.py's foundation fixed.
#: Falls back to the identical expression jev_seam.py itself uses only
#: when jev_seam could not even be imported (in which case no call this
#: module makes ever reaches a filesystem path anyway -- see
#: _consult()/_off_result() below).
if _seam is not None:
    DEFAULT_REGISTRY_PATH = _seam.DEFAULT_REGISTRY_PATH
    DEFAULT_SEAMS_CONFIG_PATH = _seam.DEFAULT_SEAMS_CONFIG_PATH
    DEFAULT_LEDGER_DIR = _seam.DEFAULT_LEDGER_DIR
else:
    DEFAULT_REGISTRY_PATH = os.path.join(REPO_ROOT, "data", "jev-registry.json")
    DEFAULT_SEAMS_CONFIG_PATH = os.path.join(REPO_ROOT, "data", "jev-seams.json")
    _JEV_STATE_DIR_FALLBACK = (os.environ.get("BROTHER_JEV_STATE_DIR")
                                or os.path.expanduser("~/.brother/jev"))
    DEFAULT_LEDGER_DIR = os.path.join(_JEV_STATE_DIR_FALLBACK, "ledger")


class _FallbackResult(object):
    """Used only when `import jev_seam` itself failed, so jev_seam.SeamResult
    is not even available to construct. Same field names a real SeamResult
    carries, so a caller written against that shape never has to
    special-case this."""

    def __init__(self, answer, reason):
        self.answer = answer
        self.jev = None
        self.mode = "off"
        self.reason = reason
        self.audit = False
        self.decision_id = None


def _off_result(current_answer, reason):
    """The same shape jev_seam.SeamResult carries: answer=current_answer
    unchanged, jev=None, mode="off", the given reason, audit=False,
    decision_id=None. Used for both of this module's own failure causes
    (jev_seam did not import, or consult() itself raised something
    undocumented): a caller reading `.answer` sees current_answer either
    way."""
    if _seam is not None:
        return _seam.SeamResult(current_answer, None, "off", reason, False, None)
    return _FallbackResult(current_answer, reason)


def _consult(entry_id, state, current_answer, *, seams_config, registry, ledger_dir,
             runner=None, rng=None):
    """The one call every function below makes. Never raises: any exception
    from jev_seam.consult() itself, or from jev_seam having failed to
    import at module load time, is caught here and turned into an
    off-shaped result carrying `current_answer` unchanged (rule 2 of the
    wave-1 seam brief: "the call site must be impossible to break the
    caller")."""
    if _seam is None:
        return _off_result(current_answer, "jev_seam could not be imported")
    try:
        return _seam.consult(
            entry_id, state, current_answer,
            seams_config=seams_config, registry=registry, ledger_dir=ledger_dir,
            runner=runner, rng=rng,
        )
    except Exception as exc:  # noqa: BLE001  # rule 2: never break the caller
        return _off_result(
            current_answer,
            "jev_seam.consult raised %s: %s" % (type(exc).__name__, exc))


def check_row_claim_support(claim, source_excerpt, current_answer, *, seams_config,
                             registry, ledger_dir, runner=None, rng=None):
    """J030: is `claim` supported by `source_excerpt`? noul, abstain band
    0.2-0.8 (jev_decide's own reading of the registry entry's
    instructions). `current_answer` always wins in every mode but a signed
    act promotion -- see jev_seam.py's MODES section -- so this is a
    second opinion on a human reading the source, never a replacement for
    it."""
    state = {"claim": claim, "source_excerpt": source_excerpt}
    return _consult("J030", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_gate_log_lines(lines, current_answer="unknown", *, seams_config, registry,
                          ledger_dir, runner=None, rng=None):
    """J064: classify each of `lines` (one gate/CI/PR log line each). Returns
    a list of SeamResult, one per line, in `lines`' own order -- see the
    module docstring for why this is a Python-level loop over consult()
    rather than one batched wire call. `current_answer` is the same
    starting point for every line (this entry has no existing call site of
    its own to have already judged one line differently from another); the
    gate/CI/PR's own exit code keeps sole pass/fail authority regardless of
    what any of these say, per the registry entry's own fail_direction.

    CHUNKED SUBMISSION (M1(b), Opus rereview4, 2026-09-19): consult()
    enforces max_inflight_calls as a hard cap on background workers
    running at once and DROPS any call past it rather than queueing it
    (see jev_seam.py's own module docstring, item 1) -- a caller that
    fires more calls than the cap in one burst therefore loses some of
    them to a "queue full" drop it could have avoided by simply not
    submitting them all at once (probed: 40 lines against the default cap
    of 32 dropped 8). This function submits at most max_inflight_calls
    lines, drains (waits for that wave's workers to finish and free their
    slot), then submits the next wave, so a caller with more lines than
    the cap gets every line answered rather than losing whatever did not
    fit in the first wave."""
    lines = list(lines)
    cap = _seam.max_inflight_calls(seams_config) if _seam is not None else max(len(lines), 1)
    results = []
    for start in range(0, len(lines), cap):
        chunk = lines[start:start + cap]
        results.extend(
            _consult("J064", {"line": line}, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)
            for line in chunk
        )
        if start + cap < len(lines):
            _seam.drain()
    return results


def check_front_door_skill(request_text, available_skills, current_answer, *,
                            seams_config, registry, ledger_dir, runner=None, rng=None):
    """J014: which named skill (or none) fits `request_text`, given
    `available_skills` for context. The registry entry's own options are
    its fixed catalogue (intake/review/status/handover/none-of-the-above/
    unknown, per data/jev-registry.json); `available_skills` is carried in
    `state` so Jev can read it, never used here to change what options it
    may answer with -- only the registry entry does that (rule 4)."""
    state = {"request_text": request_text,
              "available_skills": list(available_skills or [])}
    return _consult("J014", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_front_door_readiness(request_text, frontdoor_bucket, current_answer, *,
                                seams_config, registry, ledger_dir, runner=None, rng=None):
    """J119: given `request_text` and the bucket the already-shipped J014
    seam chose for it, should dispatch proceed as-is or ask exactly one
    clarifying slot? `current_answer` is always PROCEED_BEST (dispatch's
    own real default, never overridden by this seam -- see MODES in
    jev_seam.py); a deterministic destructive-pattern check the CALLER
    runs before this function is invoked is what may force NEEDS_HUMAN
    for real, never this function's own local answer."""
    state = {"request_text": request_text, "frontdoor_bucket": frontdoor_bucket}
    return _consult("J119", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_worker_completion_claim(note, changed_paths, current_answer, *, seams_config,
                                   registry, ledger_dir, runner=None, rng=None):
    """J063: does the worker's own completion `note` match `changed_paths`
    (the real diff, read from git by the caller -- never the worker's own
    account of itself, which is exactly the claim being audited)."""
    state = {"claim": note, "changed_paths": list(changed_paths or [])}
    return _consult("J063", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_done_claim_verification(note, quotes_verification, current_answer, *,
                                   seams_config, registry, ledger_dir, runner=None,
                                   rng=None):
    """J102: does the done-claim in `note` lack a verbatim quote of a
    verification run after the last edit? `quotes_verification` is the
    caller's own deterministic read of that fact (never guessed here or by
    Jev -- this module only carries it into `state` for Jev to weigh)."""
    state = {"final_message": note, "quotes_verification": bool(quotes_verification)}
    return _consult("J102", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_scope_audit(objective, changed_path, current_answer, *, seams_config,
                       registry, ledger_dir, runner=None, rng=None):
    """J117: is `changed_path` part of doing `objective`? Beside, never
    instead of, the deterministic declared-scope path check that alone
    decides QUARANTINE (scope_audit.py / loop_bridge._audit_scope)."""
    state = {"objective": objective, "changed_path": changed_path}
    return _consult("J117", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_founder_decision_risk(decision_text, current_answer, *, seams_config,
                                 registry, ledger_dir, runner=None, rng=None):
    """J091: red/amber/unknown second opinion on a decision's risk framing,
    beside fable_authority.classify()'s own keyword list (sole authority:
    GREEN is unreachable there by design, per the PR-572 fix removing the
    green-on-no-match default -- see fable_authority.py's own module
    docstring). `current_answer` is always classify()'s own real label
    (RED or AMBER); this seam can never turn a RED into an AMBER or an
    AMBER into a RED, whatever mode says, including "act" -- there is no
    promoted, calibrated evidence for this high-risk entry to make "act"
    a real path today regardless."""
    state = {"decision_text": decision_text}
    return _consult("J091", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_worker_failure_classification(note_text, current_answer, *, seams_config,
                                         registry, ledger_dir, runner=None, rng=None):
    """J035: a finer-grained second opinion on a worker failure, ONLY
    meaningful when the deterministic token match (loop_bridge.py's
    failure_class_of(), scanning for a literal 'failure_class=<token>'
    marker in the worker's note) found nothing and fell through to
    'other' -- 'other' is the exact class the circuit breaker
    (_BREAKER_CLASSES) never counts, so a genuine rate_limit or
    overloaded failure whose note lost its token currently opens no
    breaker at all. `current_answer` is always the real deterministic
    result ('other'); this seam can never turn it into anything the
    breaker would count differently, whatever mode says."""
    state = {"note_text": note_text}
    return _consult("J035", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_drift_classification(evidence_text, current_answer, *, seams_config,
                                registry, ledger_dir, runner=None, rng=None):
    """J029: fired ONLY after record_drift.py's own regex first pass
    (the decided-word scope check, the reaffirmation check, or the
    complaint-verdict comparison) has already flagged a DRIFT -- never on
    a clean row. Classifies WHICH KIND of drift this looks like
    (on-track/scope-creep/persona-shift/stalled) for the calibration
    ledger only. `current_answer` is always the caller's own real DRIFT
    finding (a (kind, id, detail) tuple, unchanged); this seam can never
    clear, soften, or replace a fired DRIFT, whatever mode says -- the
    registry's own fail_direction is explicit that unknown or a
    disagreement never silently clears the flag."""
    state = {"evidence_text": evidence_text}
    return _consult("J029", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_lane_routing(spec_text, current_answer, *, seams_config, registry,
                        ledger_dir, runner=None, rng=None):
    """J033: second-opinions lane_router.py's own task_class/risk_class
    table for the calibration ledger only, on every unit entering the
    dispatch loop. `current_answer` is always the table's own real
    routed lane name; this seam can never reroute a unit, whatever mode
    says -- the registry's own fail_direction is explicit that unknown
    keeps the table's routed lane."""
    state = {"spec_text": spec_text}
    return _consult("J033", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_adversarial_review_tier(checker_text, current_answer, *, seams_config,
                                   registry, ledger_dir, runner=None, rng=None):
    """J051: fired only when lane_router.py's own word-boundary regex
    names neither opus nor muse in a review unit's checker field, or
    names both -- the two cases that regex cannot resolve on its own.
    `current_answer` is always the real routed lane (lane_router.py
    already defaults ambiguous cases to muse, the cheaper/safer lane);
    this seam can never reroute a unit, whatever mode says -- the
    registry's own fail_direction routes ambiguous/unknown to the more
    expensive/safer lane, never silently to the cheap one."""
    state = {"checker_text": checker_text}
    return _consult("J051", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_review_route_disposition(route_summary, current_answer, *, seams_config,
                                    registry, ledger_dir, runner=None, rng=None):
    """J049/J050: reviewroute.py's route() is a deterministic function with
    an absolute byte-identical-on-the-same-diff contract (its own test
    suite enforces this); this seam is a pure side effect, fired AFTER the
    real tier/reviewer decision is computed, never before and never
    consulted for the decision itself. `current_answer` is always the
    real (tier, primary, secondary) tuple route() already computed; this
    seam can never change routing, whatever mode says."""
    state = {"route_summary": route_summary}
    return _consult("J049", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_review_route_reviewer(route_summary, current_answer, *, seams_config,
                                 registry, ledger_dir, runner=None, rng=None):
    """J050: the same anchor and moment as J049 (route()'s own deterministic
    result), a separate registry entry asking specifically about reviewer
    selection rather than tier/disposition. Same C1 guarantee: fired after
    the real decision, never before, never changes it."""
    state = {"route_summary": route_summary}
    return _consult("J050", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_vault_recall_rerank(unit_spec, note_or_rule_text, current_answer, *,
                               seams_config, registry, ledger_dir, runner=None,
                               rng=None):
    """J083: given `unit_spec` and one recalled vault note or rule's text,
    second-opinions its relevance on a 2-10 scale, beside
    products/brothermode/tools/bm_learning.py's own lexical_overlap()
    (BM25-ish) ranking at line 181, which stays the real, authoritative
    ranking. `current_answer` is always that function's own real score for
    this pair, unchanged: this can never reorder or replace the ranking a
    caller receives, whatever mode says (registry fail_direction: 'unknown
    keeps the lexical ranking's own order'). A caller batches by calling
    this once per item in the recalled set (the same per-item shape every
    check above but check_gate_log_lines already uses)."""
    state = {"unit_spec": unit_spec, "recalled_text": note_or_rule_text}
    return _consult("J083", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_vault_note_type(note_text, current_answer, *, seams_config, registry,
                           ledger_dir, runner=None, rng=None):
    """J089: given a vault note's text, second-opinions its type (lesson,
    checklist, pattern, gotcha, decision-record, unknown), beside
    products/brothermode/tools/bm_vault_staleness.py's own regex
    classification of the frontmatter `type:` field at line 84, which
    stays the first pass. `current_answer` is always that regex's own raw
    answer, unchanged: this can never change the type a caller reads,
    whatever mode says (registry fail_direction: 'unknown keeps the
    regex's own type guess')."""
    state = {"note_text": note_text}
    return _consult("J089", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_vault_triage_relationship(claim_a_text, claim_b_text, current_answer, *,
                                     seams_config, registry, ledger_dir, runner=None,
                                     rng=None):
    """J082: given two paired vault notes' claims, second-opinions their
    relationship (duplicate, scoped-both-true, contradiction, unrelated,
    unknown), beside products/brothermode/tools/bm_vault_triage.py's own
    dimension-equality compare (classify(), line 244), which stays the
    real, authoritative verdict. `current_answer` is always that
    function's own real ("SCOPED", dimension) or ("CONTRADICTION", None)
    result, unchanged: this can NEVER change whether bm_vault_triage.py
    treats a pair as a duplicate, a merge candidate, or anything else,
    whatever mode says (registry fail_direction: 'unknown surfaces the
    pair for human triage, never auto-merges or auto-contradicts')."""
    state = {"claim_a": claim_a_text, "claim_b": claim_b_text}
    return _consult("J082", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_vault_dedup_adjudication(candidate_title, note_text, candidates,
                                    current_answer, *, seams_config, registry,
                                    ledger_dir, runner=None, rng=None):
    """J118: given a drafted vault note (`candidate_title`, `note_text`) and
    up to the top-3 deterministically-retrieved candidate notes
    (`candidates`, a list of {"id", "title", "score"} dicts), second-opinions
    whether the new note shares the SAME root cause as the closest candidate,
    is merely RELATED, or is genuinely NOVEL -- beside, never instead of,
    products/brothermode/tools/bm_vault_intake.py's own duplicate_suspects()
    at line 385, which is title-lexical only and cannot see whether the
    failing MECHANISM matches, not just the symptom text.

    INVARIANT (registry fail_direction, C1): whatever this seam answers, the
    note's write outcome and content never change. `_admit_one()` at
    bm_vault_intake.py:568 calls duplicate_suspects() at line 645 to decide
    the deterministic 'duplicate-suspect' dirt tag and writes the note
    exactly as that real result dictates; this function is fired only AFTER
    that real result is already computed, as a pure side effect for the
    calibration ledger -- a merge is never auto-applied, only ever a logged
    suggestion for a later human vault-gc pass (see
    bm_vault_intake._dedup_second_opinion, the one caller, which discards
    this function's return value and returns duplicate_suspects()'s own
    result unchanged)."""
    state = {"candidate_title": candidate_title, "note_text": note_text,
              "candidates": list(candidates or [])}
    return _consult("J118", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_fast_path_eligibility(route_summary, current_answer, *, seams_config,
                                 registry, ledger_dir, runner=None, rng=None):
    """J056: reviewroute.py's own tier+selection heuristic (`_is_low_risk_fast_path`)
    already decides whether a diff is eligible for the low-risk fast path; this seam
    is a pure side effect fired AFTER that heuristic's real boolean is computed
    inside `route()`, at the registry's own anchor
    products/brothersbe/src/brothersbe/reviewroute.py:507 (the function has since
    shifted lines in the same file; the call site itself is `route()`'s
    `low_risk_fast_path` variable, computed once and never re-read from this seam).
    `current_answer` is always that real boolean, unchanged, whatever mode says."""
    state = {"route_summary": route_summary}
    return _consult("J056", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_migration_content_detector(diff_summary, current_answer, *, seams_config,
                                      registry, ledger_dir, runner=None, rng=None):
    """J052: reviewroute.py's own `_migration_content_hits` regex detector already
    decides whether added diff lines contain migration content; this seam is a pure
    side effect fired AFTER that regex's real hits are computed inside `route()`,
    at the registry's own anchor
    products/brothersbe/src/brothersbe/reviewroute.py:563 (the function has since
    shifted lines in the same file). `current_answer` is always the regex's own
    real boolean (whether any hit was found), unchanged, whatever mode says."""
    state = {"diff_summary": diff_summary}
    return _consult("J052", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_embedded_sql_detector(diff_summary, current_answer, *, seams_config,
                                 registry, ledger_dir, runner=None, rng=None):
    """J053: reviewroute.py's own `_embedded_sql_hits` regex detector already
    decides whether added diff lines embed SQL in a host-language file; this seam
    is a pure side effect fired AFTER that regex's real hits are computed inside
    `route()`, at the registry's own anchor
    products/brothersbe/src/brothersbe/reviewroute.py:579 (the function has since
    shifted lines in the same file). `current_answer` is always the regex's own
    real boolean (whether any hit was found), unchanged, whatever mode says."""
    state = {"diff_summary": diff_summary}
    return _consult("J053", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_doc_control_promise_detector(diff_summary, current_answer, *, seams_config,
                                        registry, ledger_dir, runner=None, rng=None):
    """J054: reviewroute.py's own `_doc_control_promise_hits` regex detector
    already decides whether removed doc lines drop a stated control guarantee;
    this seam is a pure side effect fired AFTER that regex's real hits are
    computed inside `route()`, at the registry's own anchor
    products/brothersbe/src/brothersbe/reviewroute.py:621 (the function has since
    shifted lines in the same file). `current_answer` is always the regex's own
    real boolean (whether any hit was found), unchanged, whatever mode says."""
    state = {"diff_summary": diff_summary}
    return _consult("J054", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_test_tampering_detector(diff_summary, current_answer, *, seams_config,
                                   registry, ledger_dir, runner=None, rng=None):
    """J055: reviewroute.py's own `_qa_hits` heuristic already decides whether a
    test-only diff removed an assertion or added a skip/xfail marker with no
    replacement assertion (the "test-weakened-assertion" hit shape, distinct from
    the non-tampering "test-only-significant" hit shape the same function can also
    return); this seam is a pure side effect fired AFTER that heuristic's real
    tampering hits are computed inside `route()`, at the registry's own anchor
    products/brothersbe/src/brothersbe/reviewroute.py:647 (the function has since
    shifted lines in the same file). `current_answer` is always the heuristic's
    own real boolean (whether any tampering hit was found), unchanged, whatever
    mode says."""
    state = {"diff_summary": diff_summary}
    return _consult("J055", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_learned_rule_atomicity(action_text, current_answer, *, seams_config,
                                  registry, ledger_dir, runner=None, rng=None):
    """J093: given a captured learning-rule's `action_text`, second-opinions
    whether it has an atomicity problem (bundles more than one rule), beside
    products/brothermode/tools/bm_learning.py's own heuristic compound-pattern
    scan at line 141 (atomicity_problems()), which stays the real, first-pass
    verdict. `current_answer` is always that function's own real
    bool(reasons) verdict, unchanged: this can never change whether a
    capture is flagged for human review, whatever mode says (registry
    fail_direction: 'abstain flags the capture for human review anyway
    (safer default)')."""
    state = {"action_text": action_text}
    return _consult("J093", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_receipt_risk_trigger(unit_text, current_answer, *, seams_config,
                                registry, ledger_dir, runner=None, rng=None):
    """J099: given one release-receipt unit's own declared text, second-
    opinions whether it contains a risk-trigger phrase worth flagging, beside
    scripts/receipt_door.py's own regex risk-class scan at line 237
    (risk_triggers()), which stays the real, first-pass verdict.
    `current_answer` is always that function's own real per-unit hit
    boolean, unchanged: this can never add or remove a risk flag on the
    receipt, whatever mode says (registry fail_direction: 'abstain flags
    the unit anyway (safer default)')."""
    state = {"unit_text": unit_text}
    return _consult("J099", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def _load_registry(path):
    """REUSED from jev_seam.load_registry() when available (its own
    docstring: raises jev_registry.RegistryError unchanged, this CLI's own
    main() is the guarded call site that turns that into an exit). Falls
    back to a direct jev_registry.load() only in the defensive case
    _consult() already handles (jev_seam did not import): the CLI cannot
    do anything useful then either way, so this just avoids a second
    ImportError obscuring the real one."""
    if _seam is not None:
        return _seam.load_registry(path)
    import jev_registry
    return jev_registry.load(path)


def _load_seams_config(path):
    """REUSED from jev_seam.load_seams_config() when available: the same
    mtime-cached, deepcopy-per-call loader every real seam call site
    shares (see its own docstring for why a second, uncached JSON read
    here would defeat rule 9's off-mode cost bound for any caller that
    reloads config on every CLI invocation). Falls back to a plain read
    only when jev_seam itself is unavailable."""
    if _seam is not None:
        return _seam.load_seams_config(path)
    import json
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_stop_backlog_classify(row_text, current_answer, *, seams_config, registry,
                                 ledger_dir, runner=None, rng=None):
    """J048: given one candidate backlog row's own text, second-opinions
    whether it reads as ready, blocked, waiting-on-founder, done, or
    unknown, beside ~/.claude/hooks/keep_pulling_the_backlog.py's own
    READY_ROW/NOT_STARTED_VALUES/BLOCKED_WORDS regex classification of that
    same row (the real, authoritative call: whether the Stop hook blocks a
    session's turn at all). `current_answer` is always that regex's own
    real classification for this row ("ready" or "blocked"), unchanged:
    this can NEVER change whether the Stop hook actually blocks, whatever
    mode says (registry fail_direction: 'unknown escalates to
    BLOCKED-session-continues, never reads as done').

    CALLED FROM A DETACHED, SEPARATE PROCESS ONLY (jev_backlog_row_audit.py,
    beside the hook, launched fire-and-forget the same way the hook's own
    Muse-based NI-2 audit already is), never inline inside the Stop hook
    itself: keep_pulling_the_backlog.py runs on every session's every Stop
    event, in every project, and Jev (this registry, this ledger) is a
    Brother-repo concept, not a machine-wide one. The hook's own verdict is
    already final and returned before this function is ever reached."""
    state = {"row_text": row_text}
    return _consult("J048", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_work_profile_tier_classify(request_text, current_answer, *, seams_config,
                                      registry, ledger_dir, runner=None, rng=None):
    """J001: given the raw intake ask text, second-opinions the work
    profile it implies (build, fix, research, ops, migration, infra,
    unknown), beside products/brothersbe/skills/kickoff/SKILL.md's own
    step 1 CLASSIFY -- a live agent's own free-text judgment call, not a
    deterministic function, which is why this entry has NO Python call
    site of its own: like J030/J064/J014 before it, this is a CLI a skill
    shells out to (see main() below, `--check j001`), never invoked
    inline from this module. `current_answer` is always the agent's own
    real classification for this ask, unchanged: this can NEVER override
    the founder confirm screen or the agent's own stated profile, whatever
    mode says (registry fail_direction: 'unknown or low confidence keeps
    the current free-text LLM classify as the answer; never silently
    overrides the founder confirm screen')."""
    state = {"request_text": request_text}
    return _consult("J001", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_repeat_mistake_classification(matchable_text, current_answer, *, seams_config,
                                         registry, ledger_dir, runner=None, rng=None):
    """J045: given one tool call's own matchable text (already flagged by
    ~/.claude/hooks/repeat_guard.py's own matching_lessons() lexical
    trigger-word/regex match at line 140, the real, first-pass trigger),
    second-opinions whether it genuinely matches the recorded mistake, only
    discusses it, is unrelated, or is unknown. `current_answer` is always
    the real classification matching_lessons() already produced for this
    call ("matches-mistake" when a lesson hit fired, since the current code
    only ever surfaces a hit, never blocks on it): this can NEVER change
    whether the additionalContext warning is printed, whatever mode says
    (registry fail_direction describes a hypothetical future live mode,
    not this shadow-only wiring: 'unknown or low confidence stays BLOCKED
    (current behavior), never silently cleared').

    CALLED FROM A DETACHED, SEPARATE PROCESS ONLY
    (repeat_guard_lesson_audit.py, beside the hook, launched fire-and-forget
    only when a real lesson hit already fired, the same throttled contract
    as J048's jev_backlog_row_audit.py), never inline inside repeat_guard.py
    itself: that hook runs before EVERY Bash/Edit/Write tool call, in every
    project, and Jev is a Brother-repo concept, not a machine-wide one. The
    hook's own verdict (print the warning, or not) is already final and
    returned before this function is ever reached."""
    state = {"matchable_text": matchable_text}
    return _consult("J045", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def check_drift_pattern_confidence(matched_text, pattern_id, current_answer, *,
                                    seams_config, registry, ledger_dir, runner=None, rng=None):
    """J116: given a drift-pattern match scripts/drift_gate.py's own
    pattern set already fired on, second-opinions its confidence, one of
    the registry's own options (exhibits-drift, discusses-drift-only,
    unclear, unknown), beside drift_gate.py's own regex trigger, which
    stays the real detector. `current_answer` is always "exhibits-drift"
    unchanged: by the time a finding reaches this seam,
    strip_non_live_text() has already ruled out the discusses-only case
    structurally (a quoted or described example never survives to this
    point), so this labels confidence only and can NEVER suppress a real
    finding or add one that was not already there, whatever mode says.

    Unlike J045/J048, drift_gate.py lives INSIDE this repo (scripts/),
    the same package as this module, so no cross-checkout detection or
    detached-process launch is needed here: a caller already running
    inside this repo can import this module directly."""
    state = {"matched_text": matched_text, "pattern_id": pattern_id}
    return _consult("J116", state, current_answer, seams_config=seams_config,
                     registry=registry, ledger_dir=ledger_dir, runner=runner, rng=rng)


def main(argv=None):
    """A small CLI for the checks with no harness call site yet (J030,
    J064, J014, J001), so a later surface or skill can shell out to this
    module rather than re-deriving each entry's state shape. Prints one
    JSON object per result to stdout; exit code is always 0 (this is an
    advisory second opinion, never a gate -- see jev_seam.py's own MODES
    section: nothing this module returns can change caller behaviour
    unless a human first sets that entry's mode in data/jev-seams.json)."""
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        description="Ask one of the wave-1/2 Jev checks with no harness call "
                     "site yet (J030, J064, J014, J001). Off by default: see "
                     "data/jev-seams.json.")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--seams-config", default=DEFAULT_SEAMS_CONFIG_PATH)
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR)
    sub = parser.add_subparsers(dest="check", required=True)

    j030 = sub.add_parser("j030", help="row claim-vs-source support")
    j030.add_argument("--claim", required=True)
    j030.add_argument("--source", required=True)
    j030.add_argument("--current-answer", default=None)

    j064 = sub.add_parser("j064", help="gate/CI/PR log-line classification, batched")
    j064.add_argument("--lines-file", help="one log line per line; default stdin")
    j064.add_argument("--current-answer", default="unknown")

    j014 = sub.add_parser("j014", help="front-door skill selection")
    j014.add_argument("--request", required=True)
    j014.add_argument("--skills", default="",
                       help="comma-separated available skill names")
    j014.add_argument("--current-answer", default=None)

    j001 = sub.add_parser("j001", help="intake work-profile + tier classify")
    j001.add_argument("--request", required=True)
    j001.add_argument("--current-answer", required=True,
                       help="the agent's own real classification for this ask")

    args = parser.parse_args(argv)
    seams_config = _load_seams_config(args.seams_config)

    # OFF MODE COSTS NOTHING (rule 9), including from this CLI (opus
    # review, item 7, 2026-09-19): the ledger folder used to be created
    # unconditionally, even for the overwhelmingly common case (every
    # entry off, data/jev-seams.json's own shipped default) where nothing
    # will ever write to it. resolve_mode() is a pure dict lookup with no
    # filesystem cost of its own, so checking it first is free; the
    # registry (a real parse-plus-validate of ~120 entries) is skipped the
    # same way, for the same reason -- an off-mode call never reads it
    # either, see jev_seam.consult()'s own OFF branch.
    entry_id = {"j030": "J030", "j064": "J064", "j014": "J014", "j001": "J001"}[args.check]
    live = _seam is not None and _seam.resolve_mode(seams_config, entry_id) != _seam.OFF
    if live:
        os.makedirs(args.ledger_dir, exist_ok=True)
        registry = _load_registry(args.registry)
    else:
        registry = []

    if args.check == "j030":
        results = [check_row_claim_support(
            args.claim, args.source, args.current_answer,
            seams_config=seams_config, registry=registry, ledger_dir=args.ledger_dir)]
    elif args.check == "j064":
        if args.lines_file:
            with open(args.lines_file, "r", encoding="utf-8") as fh:
                lines = [line.rstrip("\n") for line in fh if line.strip()]
        else:
            lines = [line.rstrip("\n") for line in sys.stdin if line.strip()]
        results = check_gate_log_lines(
            lines, args.current_answer,
            seams_config=seams_config, registry=registry, ledger_dir=args.ledger_dir)
    elif args.check == "j014":
        skills = [s.strip() for s in args.skills.split(",") if s.strip()]
        results = [check_front_door_skill(
            args.request, skills, args.current_answer,
            seams_config=seams_config, registry=registry, ledger_dir=args.ledger_dir)]
    else:  # j001
        results = [check_work_profile_tier_classify(
            args.request, args.current_answer,
            seams_config=seams_config, registry=registry, ledger_dir=args.ledger_dir)]

    # DRAIN BEFORE RETURNING (M1(a), Opus rereview4, 2026-09-19): this CLI
    # is exactly the short-lived process A0.8's own drain() docstring
    # means by "a call site that knows it is about to exit" -- left to the
    # atexit hook alone (DEFAULT_EXIT_DRAIN_S=1.0s, sized for an ORDINARY
    # process that merely happens to import jev_seam), a shadow call whose
    # worker needed longer than 1s to finish spent its budget, started a
    # real bridge call, and left no ledger row and no trace on stdout or
    # stderr (probed: rc=0, "reason": "shadow: submitted", ledger decision
    # rows: 0). cli_drain_timeout_s() sizes the wait to how many calls
    # this run actually issued, never the atexit hook's fixed bound.
    if live:
        _seam.drain(timeout=_seam.cli_drain_timeout_s(seams_config, len(results)))

    for result in results:
        payload = {"answer": result.answer, "mode": result.mode,
                   "reason": result.reason, "audit": result.audit,
                   "decision_id": result.decision_id, "jev": result.jev}
        print(_json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
