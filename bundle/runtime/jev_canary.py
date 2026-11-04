#!/usr/bin/env python3
"""jev_canary: JEV-05, the golden-set drift canary for Jev (TypeSafe's
typesafe/jev-1.13 decision model).

WHY THIS EXISTS. Jev is reached only through a vendor alias
("jev-latest") that TypeSafe can repoint at a new model id at any time
(see jev_decide.py's own docstring). A repoint invalidates every
calibration this estate has measured (jev_eval's accuracy, balanced
accuracy, coverage and cost-per-success numbers) silently, since a
repointed alias still answers every call. This module is the tripwire: a
small, fixed subset of the labelled dataset (benchmarks/jev_eval/
dataset.json), run through the SAME pinned model id every time, checked
against a recorded baseline accuracy and calibration. A model-id change,
a genuine accuracy drop, a genuine calibration collapse, or a Jev that
has gone persistently unreachable all fail loud, before anything
downstream trusts a Jev answer that quietly stopped being the Jev that
was calibrated.

WHY TASK C IS EXCLUDED. jev_eval's own calibration read found no judge
(Jev included) beat task C's base rate there: a routing choice where the
majority label already outperforms every system tried. A canary built on
a task no system ever demonstrably solved better than guessing cannot
tell "Jev drifted" apart from "Jev was never better than the base rate
here". Tasks A (mutation triage), B (unknown-reads-safe) and D (log
triage) are where Jev's edge over the base rate is real.

WHY THE SUBSET IS HASH-ORDERED, NOT POSITIONAL. Ranking by
sha256(task:item_id) rather than by list position means the same item
identities are selected for as long as those ids exist in the dataset,
whatever order they are written in.

HOW TASK C IS SKIPPED WITHOUT TOUCHING ITS SPEC FILES. jev_eval.items()
is one generator covering all four tasks in order A, B, C, D, reading a
spec text file per C item before yielding it. This module hands items()
a shallow copy of the dataset whose C_routing.labels is emptied, so that
read loop never executes a single iteration; A, B and D are read through
jev_eval's own, unmodified code.

NOISE, JEV-05 ROUND 2. A single run's accuracy or calibration figure
carries sampling noise (with n=40, a 0.05 tolerance is roughly one
standard error): treating one dip as drift would fire the reset marker
on ordinary variance, not on a vendor repoint. So a bad accuracy or a
worse Brier score only fails on the SECOND CONSECUTIVE bad run; a model
id change still fails on the first, since there the signal is exact, not
statistical. A run history file (--state, default next to the baseline)
tracks how many consecutive bad runs of each kind have just happened,
written atomically (temp file + os.replace) so a crash mid-write can
never leave a half-written streak count for the next run to trust.
Three consecutive NO-DATA runs (Jev unreachable, not merely "wrong") are
treated the same way: a single NO-DATA is exit 2 and never touches the
baseline, but three in a row is loud enough on its own to FAIL with
"persistent NO-DATA".

CALIBRATION. Alongside accuracy, the baseline also records the Brier
score: mean((confidence_in_the_chosen_answer - is_it_actually_correct)^2)
over every scorable golden item. Confidence in the chosen answer is
computed the same way jev_eval.jev_pred() computes it from a raw bridge
payload (max(p, 1-p) for a noul question, since decide()'s "probability"
field for a noul answer is P(true), not P(chosen); the answer's own
probability, unchanged, for a choice question, since decide() has
already resolved that from the bridge's "probabilities" dict). Brier can
catch a model that has become over- or under-confident about answers it
still gets right just as often as before: exactly the "accuracy holds,
calibration collapses" case a raw accuracy check cannot see. A baseline
recorded before this round has no "brier" key; the calibration check is
SKIPPED (not failed) against such a baseline rather than guessing a
number that was never measured.

RESET MARKER. Carries reason, time, the pinned model id, and every model
id actually seen, written atomically to DEFAULT_RESET_MARKER (under
JEV_STATE_DIR, a fixed machine-level directory outside any checkout --
item 1, approved-with-nits review 2026-09-18, see JEV_STATE_DIR's own
docstring below for why) unless --reset-marker says otherwise. Once
written it stays until the ONLY thing allowed to clear it: a fresh
--record-baseline run. An ordinary PASS afterwards never clears it; it
prints that the marker is still present, since "the last check happened
to come back clean" is not the same claim as "the earlier incident is
resolved and re-baselined."

CONFIG. PINNED_MODEL (below, overridable by --model) is the exact model
id every golden answer must carry.

  Exit 0 PASS     model matches, and neither accuracy nor Brier has just
                  completed its consecutive-run FAIL gate (a single bad
                  run still prints as PASS with a note; see NOISE above).
  Exit 1 FAIL     model id changed (every distinct id seen is named); or
                  accuracy/Brier just completed its second consecutive
                  bad run; or NO-DATA just completed its third
                  consecutive run ("persistent NO-DATA"). Writes the
                  reset marker.
  Exit 2 NO-DATA  the dataset, baseline or state file could not be read
                  or written, or decide() itself returned NO-DATA, or the
                  scorable count fell below MIN_SAMPLE_FRACTION of the
                  baseline's n (M5: a tiny sample can never PASS), and
                  fewer than three such runs have happened in a row.
                  NEVER a PASS and never silently counted as a correct or
                  incorrect answer: nothing was measured.

--record-baseline still requires every answer to match PINNED_MODEL,
writes {accuracy, brier, n, model, recorded_at} to --baseline, and
re-anchors everything: it resets the run-history file to a clean slate
and clears any standing reset marker, since streaks and incidents
measured against the OLD baseline say nothing about the new one.

Python 3.9 floor, standard library only, no network in this module
itself (network lives in the runner/bridge, exactly as in jev_decide.py).
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration  # noqa: E402  (sibling module, reused not copied; item 1, 2026-09-19)
import jev_decide  # noqa: E402  (sibling module, reused not copied)
import jev_eval  # noqa: E402  (sibling module, reused not copied)

#: This file lives at <repo root>/scripts/jev_canary.py, so its
#: grandparent directory is the repo root regardless of the caller's cwd.
#: Still used for --dataset/--baseline defaults (jev_eval.BENCH_DIR,
#: tree content); NO LONGER used for the reset marker or run-history
#: defaults (item 1, approved-with-nits review 2026-09-18, see
#: JEV_STATE_DIR below).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The one machine-level root for Jev state that must outlive any single
#: checkout (item 1, approved-with-nits review 2026-09-18): the canary's
#: drift reset marker and its consecutive-run history are facts about the
#: outside world -- whether the pinned model actually drifted, how many
#: bad runs just happened in a row -- never tree content a `git clean -X`
#: or a worktree removal is entitled to discard. M1's original fix (below)
#: anchored the reset marker to REPO_ROOT instead of the process cwd,
#: which fixed the cwd bug but still left the marker inside whichever
#: checkout ran the canary: a FAIL recorded in one checkout or worktree
#: was invisible to a jev_seam.consult() running from a sibling checkout
#: of the same repo, since each checkout resolved its own, different
#: REPO_ROOT. JEV_STATE_DIR fixes that by moving the default OUT of every
#: checkout entirely. BROTHER_JEV_STATE_DIR overrides it; every test in
#: this module passes an explicit --reset-marker/--state instead of
#: relying on the default, so none of them touch the real home path.
#: jev_seam.py defines the IDENTICAL expression under the identical name
#: rather than importing it from here (a sibling module reused, never
#: copied, per this file's own header discipline) -- both constants must
#: resolve to the exact same directory without either module importing
#: the other.
JEV_STATE_DIR = os.environ.get("BROTHER_JEV_STATE_DIR") or os.path.expanduser("~/.brother/jev")

#: M1: the canary's reset marker default (review 70268c3: first moved off
#: the process cwd and onto REPO_ROOT; item 1, approved-with-nits review
#: 2026-09-18: moved again, off REPO_ROOT and onto JEV_STATE_DIR -- see
#: JEV_STATE_DIR's own docstring above for why a repo-root path was still
#: not far enough). The seam (jev_seam.py's DEFAULT_CANARY_MARKER)
#: resolves this identical path on its side by contract (the identical
#: JEV_STATE_DIR expression, not an import of this constant), so a canary
#: FAIL is visible to the seam without every caller having to pass a
#: matching --reset-marker by hand. Still overridable via --reset-marker
#: for a test or an alternate baseline.
DEFAULT_RESET_MARKER = os.path.join(JEV_STATE_DIR, "canary-reset.json")

#: The canary's run-history default (item 1, approved-with-nits review
#: 2026-09-18): previously derived per-baseline as
#: "<baseline_path>.state.json" (see the old _default_state_path(), now
#: removed), which happened to land outside data/ already (baseline
#: defaults under benchmarks/jev_eval/) but was never anchored to
#: JEV_STATE_DIR and so shared none of the cross-checkout guarantee the
#: reset marker gets above. Fixed under JEV_STATE_DIR for the identical
#: reason: the consecutive-run streak is a fact about recent real runs,
#: not tree content, and a streak lost to a deleted checkout only ever
#: delays an escalation (see the module docstring's NOISE section) rather
#: than masking one -- but there is no reason to accept that delay when a
#: fixed, checkout-independent path avoids it entirely. Still overridable
#: via --state.
DEFAULT_STATE_PATH = os.path.join(JEV_STATE_DIR, "canary-state.json")

#: The calibration ledger's default directory (item 1, 2026-09-19): the
#: IDENTICAL expression jev_seam.py resolves its own DEFAULT_LEDGER_DIR
#: to, defined independently here rather than imported -- the same
#: "define identically, never import" discipline this module already
#: follows for JEV_STATE_DIR itself (see that constant's own docstring).
#: --ledger-status (below) defaults to decisions.jsonl/outcomes.jsonl
#: under this directory, so an operator running the canary's own doctor
#: check without arguments checks the SAME ledger every real seam call
#: writes to.
DEFAULT_LEDGER_DIR = os.path.join(JEV_STATE_DIR, "ledger")

#: The exact model id every golden answer must carry. A vendor-repointed
#: "jev-latest" alias answers just as fluently under a different id; that
#: different id is exactly the drift this module exists to catch, so the
#: comparison is exact-string, never a prefix.
PINNED_MODEL = "typesafe/jev-1.13-20260917"

#: Golden-set size and the three tasks it is drawn from. Fixed, not a
#: flag: a canary whose own size or task mix could change between runs
#: would not be measuring the same thing twice.
CANARY_SIZE = 40
CANARY_TASKS = ("A", "B", "D")

#: How far below the recorded baseline accuracy, or above the recorded
#: baseline Brier score, still counts as an ordinary run (no streak
#: increment at all).
DEFAULT_TOLERANCE = 0.05
DEFAULT_BRIER_TOLERANCE = 0.05

#: M5: a scored run whose scorable count falls below this fraction of the
#: baseline's recorded n can never PASS on its own merits: with only a
#: handful of golden items actually scored, an accuracy or Brier figure
#: is not a measurement of the same thing the baseline measured, and a
#: tiny sample landing on a lucky 100% must not read as "drift-free".
MIN_SAMPLE_FRACTION = 0.9

#: How many consecutive bad runs of each kind it takes to actually FAIL.
#: Model drift is an exact signal (a string either matches or it does
#: not) so it needs none of this; accuracy and Brier are statistical
#: (noise at n=40) so they need two in a row; NO-DATA needs three,
#: reserved for "Jev looks unreachable", not "one flaky call".
CONSECUTIVE_ACCURACY_FAIL = 2
CONSECUTIVE_BRIER_FAIL = 2
CONSECUTIVE_NO_DATA_FAIL = 3

NO_DATA = jev_decide.NO_DATA
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NO_DATA = 2


# ---------------------------------------------------------------------------
# Golden-set selection and question building.
# ---------------------------------------------------------------------------

def _golden_items(dataset, dataset_dir, size=CANARY_SIZE, tasks=CANARY_TASKS):
    """(task, item_id, truth, state, question) for a fixed, deterministic
    subset of at most `size` items drawn only from `tasks`, reusing
    jev_eval's own per-task question building. See the module docstring
    for why C is excluded by emptying its labels rather than filtering
    its output, and why the subset is ranked by a content hash rather
    than list position."""
    trimmed = dict(dataset)
    trimmed["C_routing"] = dict(trimmed.get("C_routing") or {}, labels={})
    pool = [it for it in jev_eval.items(trimmed, dataset_dir) if it[0] in tasks]
    pool.sort(key=lambda it: hashlib.sha256(("%s:%s" % (it[0], it[1])).encode("utf-8")).hexdigest())
    return pool[:size]


def _batch(golden):
    """(state, questions) for one decide() call covering every golden
    item, mirroring jev_eval._batched()'s own shape: `state` keyed by the
    raw item id (what each question's instructions refer to), `questions`
    keyed by a sanitized id (what decide()'s answer records are keyed
    by), each question's instructions prefixed to say which item it is
    about so one shared state blob answers many items unambiguously."""
    state = {}
    questions = {}
    for _task, iid, _truth, item_state, q in golden:
        state[iid] = item_state
        key = iid.replace(".", "_").replace("-", "_")
        q2 = dict(q)
        q2["instructions"] = "About input %s only: %s" % (iid, q["instructions"])
        questions[key] = q2
    return state, questions


def _correct(record, truth):
    """True/False for one Decision record against its item's `truth`, or
    None when the record cannot be scored at all (a monkeypatched
    decide() in a test can hand back anything, so this stays defensive
    rather than trusting the shape decide() itself guarantees). noul
    rounds at 0.5, the same threshold jev_eval.jev_pred() uses on the raw
    bridge payload; choice compares the answer key directly, the same as
    jev_eval's own truth comparison (`pred == truth` in _job/_batched)."""
    if record.get("type") == "noul":
        probability = record.get("probability")
        if not isinstance(probability, (int, float)) or isinstance(probability, bool):
            return None
        return (probability >= 0.5) == bool(truth)
    if record.get("type") == "choice":
        return record.get("answer") == truth
    return None


def _confidence(record):
    """The model's own confidence in its CHOSEN answer, computed the same
    way jev_eval.jev_pred() computes it from a raw bridge payload: for a
    noul question decide()'s "probability" field is P(true), so the
    confidence in whichever side was actually chosen is max(p, 1-p); for
    a choice question decide() has already resolved the chosen answer's
    own probability, so it is used unchanged. None when there is nothing
    usable to score, same discipline as _correct()."""
    probability = record.get("probability")
    if not isinstance(probability, (int, float)) or isinstance(probability, bool):
        return None
    if record.get("type") == "noul":
        return max(probability, 1 - probability)
    if record.get("type") == "choice":
        return probability
    return None


# ---------------------------------------------------------------------------
# Small JSON helpers: read-with-reason, atomic write, path defaults.
# ---------------------------------------------------------------------------

def _read_json(path):
    """(data, None) on success, (None, reason) on any read or parse
    failure. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)


def _write_json_atomic(path, payload):
    """None on success, an error string on any failure to write. Writes
    to a temp file in the SAME directory as `path` and os.replace()s it
    into place, so a reader (or a crash mid-write) never observes a
    half-written file: the run history and the reset marker are both
    read by this module on its own very next invocation, so a torn write
    here would corrupt the next run's decision, not just this run's
    evidence."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".jev_canary-", suffix=".tmp", dir=directory)
    except OSError as exc:
        return "cannot create a temp file next to %s: %s" % (path, exc)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass  # best-effort cleanup; the original error is what matters
        return "cannot write %s: %s" % (path, exc)
    return None


def _fresh_history():
    return {"consecutive_accuracy_below": 0, "consecutive_brier_worse": 0, "consecutive_no_data": 0}


def _load_history(path):
    """A fresh, zeroed history on ANY read problem (missing file: this is
    the normal first-run state; corrupt file: losing a streak count is
    recoverable bookkeeping, not a safety issue, and only ever delays an
    escalation by a run rather than masking one; see the module
    docstring's NOISE section for why erring toward re-counting from zero
    is the safe direction here)."""
    data, _err = _read_json(path)
    history = _fresh_history()
    if isinstance(data, dict):
        for key in history:
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                history[key] = value
    return history


def _write_reset_marker(path, reason, pinned_model, models_seen):
    payload = {
        "reason": reason,
        "time": time.time(),
        "time_human": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pinned_model": pinned_model,
        "models_seen": models_seen,
    }
    return _write_json_atomic(path, payload)


def _clear_reset_marker(path):
    """Removes a reset marker. The ONLY caller of this is a successful
    --record-baseline run (see the module docstring): a marker is never
    cleared by an ordinary PASS. Missing is not an error, there may never
    have been one; any other failure to remove it is best-effort (a
    marker that lingers a bit longer is a stale note on the next PASS's
    message, not a masked defect: the incident it names is already
    superseded by the new baseline either way)."""
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# One measurement attempt: dataset -> golden set -> decide() -> a verdict
# tagged by kind, never an exception for a documented failure cause.
# ---------------------------------------------------------------------------

def _measure(dataset_path, pinned_model, bridge, runner):
    """One measurement attempt. Returns a dict tagged by "kind":
      {"kind": "no_data", "reason": str}
      {"kind": "model_drift", "models_seen": [str, ...]}
      {"kind": "scored", "accuracy": float, "brier": float or None,
       "n": int, "models_seen": [pinned_model]}
    Never raises for a documented failure cause, the same discipline
    jev_decide.decide() itself follows."""
    dataset, err = _read_json(dataset_path)
    if err:
        return {"kind": "no_data", "reason": "%s: %s" % (NO_DATA, err)}

    dataset_dir = os.path.dirname(os.path.abspath(dataset_path))
    golden = _golden_items(dataset, dataset_dir)
    if not golden:
        return {"kind": "no_data",
                "reason": "%s: no golden items found in tasks %s" % (NO_DATA, ", ".join(CANARY_TASKS))}
    truths = dict((iid, truth) for _task, iid, truth, _state, _q in golden)
    key_to_iid = dict((iid.replace(".", "_").replace("-", "_"), iid) for _task, iid, _t, _s, _q in golden)
    state, questions = _batch(golden)

    result = jev_decide.decide(state, questions, "jev_canary", bridge=bridge, runner=runner)
    if isinstance(result, tuple):
        _status, reason = result
        return {"kind": "no_data", "reason": "%s: decide() returned NO-DATA: %s" % (NO_DATA, reason)}

    models_seen = sorted(set(r.get("model") for r in result))
    if models_seen != [pinned_model]:
        return {"kind": "model_drift", "models_seen": models_seen}

    correctness = []
    briers = []
    for record in result:
        iid = key_to_iid.get(record.get("id"))
        if iid is None:
            continue
        correct = _correct(record, truths[iid])
        if correct is None:
            continue
        correctness.append(correct)
        confidence = _confidence(record)
        if confidence is not None:
            briers.append((confidence - (1.0 if correct else 0.0)) ** 2)

    n = len(correctness)
    if n == 0:
        return {"kind": "no_data", "reason": "%s: no golden answer could be scored" % NO_DATA}
    accuracy = sum(1 for c in correctness if c) / n
    brier = (sum(briers) / len(briers)) if briers else None
    return {"kind": "scored", "accuracy": accuracy, "brier": brier, "n": n, "models_seen": models_seen}


def _escalate_no_data(history, state_path, reset_marker_path, pinned_model, reason):
    """Applies the consecutive-NO-DATA rule (module docstring NOISE/RESET
    MARKER sections) for one no-data-shaped outcome: increments and
    persists the streak, escalating to FAIL with a written reset marker
    on the third consecutive occurrence, else a plain NO-DATA. Shared by
    _measure() itself returning "no_data" and by a scored run whose
    scorable count falls below MIN_SAMPLE_FRACTION of the baseline's n
    (M5): both are "nothing trustworthy was measured this run", so both
    count toward the same streak rather than each keeping its own."""
    history["consecutive_no_data"] += 1
    if history["consecutive_no_data"] >= CONSECUTIVE_NO_DATA_FAIL:
        marker_err = _write_reset_marker(
            reset_marker_path,
            "persistent NO-DATA (%d consecutive runs): %s" % (history["consecutive_no_data"], reason),
            pinned_model, [])
        _write_json_atomic(state_path, history)
        if marker_err:
            return EXIT_NO_DATA, "%s: FAIL (persistent NO-DATA) but %s" % (NO_DATA, marker_err)
        return EXIT_FAIL, ("FAIL: persistent NO-DATA, %d consecutive runs (reset marker written to %s): %s"
                            % (history["consecutive_no_data"], reset_marker_path, reason))
    _write_json_atomic(state_path, history)
    return EXIT_NO_DATA, reason


# ---------------------------------------------------------------------------
# run_canary(): load history, measure, apply the consecutive-run rules,
# persist history, PASS/FAIL/NO-DATA.
# ---------------------------------------------------------------------------

def run_canary(dataset_path, baseline_path, *, pinned_model=PINNED_MODEL,
               tolerance=DEFAULT_TOLERANCE, brier_tolerance=DEFAULT_BRIER_TOLERANCE,
               reset_marker_path=None, state_path=None,
               bridge=None, runner=None, record_baseline=False):
    """Runs the canary once. Returns (exit_code, message). Never raises
    for a documented failure cause: every one of them is a returned
    (code, message), the same discipline jev_decide.decide() itself
    follows."""
    # M1/item 1: default is the JEV_STATE_DIR-anchored path the seam
    # resolves too (DEFAULT_RESET_MARKER above), never one derived from
    # --baseline or the repo root. Read as a module global (not a bound
    # default) so a test can monkeypatch jev_canary.DEFAULT_RESET_MARKER
    # (or jev_canary.DEFAULT_STATE_PATH) to a temp path without ever
    # writing into the real home directory.
    reset_marker_path = reset_marker_path or DEFAULT_RESET_MARKER
    state_path = state_path or DEFAULT_STATE_PATH
    history = _load_history(state_path)

    measurement = _measure(dataset_path, pinned_model, bridge, runner)

    if measurement["kind"] == "no_data":
        return _escalate_no_data(history, state_path, reset_marker_path, pinned_model, measurement["reason"])

    if measurement["kind"] == "model_drift":
        # A model-id mismatch is a real, trustworthy answer about model
        # identity (not a low-sample scored run, see below), so it breaks
        # the NO-DATA streak same as before.
        history["consecutive_no_data"] = 0
        marker_err = _write_reset_marker(
            reset_marker_path,
            "model id changed: expected %r, saw %s" % (pinned_model, measurement["models_seen"]),
            pinned_model, measurement["models_seen"])
        _write_json_atomic(state_path, history)
        if marker_err:
            return EXIT_NO_DATA, "%s: FAIL (model id changed) but %s" % (NO_DATA, marker_err)
        return EXIT_FAIL, ("FAIL: model id changed, expected %r, saw %s (reset marker written to %s)"
                            % (pinned_model, measurement["models_seen"], reset_marker_path))

    # measurement["kind"] == "scored"
    accuracy = measurement["accuracy"]
    brier = measurement["brier"]
    n = measurement["n"]
    models_seen = measurement["models_seen"]

    if record_baseline:
        payload = {"accuracy": accuracy, "n": n, "model": pinned_model,
                   "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if brier is not None:
            payload["brier"] = brier
        write_err = _write_json_atomic(baseline_path, payload)
        if write_err:
            return EXIT_NO_DATA, "%s: %s" % (NO_DATA, write_err)
        # A fresh baseline re-anchors every consecutive-run streak and
        # clears any earlier incident: both were measured against the
        # OLD baseline and say nothing about this new one.
        _write_json_atomic(state_path, _fresh_history())
        _clear_reset_marker(reset_marker_path)
        brier_note = ("brier=%.4f" % brier) if brier is not None else "brier=NO-DATA (no confidence on any answer)"
        return EXIT_PASS, "PASS: baseline recorded, accuracy=%.4f n=%d %s model=%s" % (
            accuracy, n, brier_note, pinned_model)

    baseline, err = _read_json(baseline_path)
    if err:
        return EXIT_NO_DATA, "%s: cannot read baseline %s: %s" % (NO_DATA, baseline_path, err)
    baseline_accuracy = baseline.get("accuracy")
    if not isinstance(baseline_accuracy, (int, float)) or isinstance(baseline_accuracy, bool):
        return EXIT_NO_DATA, "%s: baseline %s has no numeric accuracy" % (NO_DATA, baseline_path)
    baseline_brier = baseline.get("brier")
    if not isinstance(baseline_brier, (int, float)) or isinstance(baseline_brier, bool):
        baseline_brier = None  # legacy baseline recorded before calibration tracking existed

    # M5: a tiny scorable sample can never PASS. Compared only when the
    # baseline itself carries a usable n (a legacy baseline without one
    # has nothing to compare against, so this is skipped rather than
    # guessed, same discipline as the Brier comparison above).
    baseline_n = baseline.get("n")
    if isinstance(baseline_n, int) and not isinstance(baseline_n, bool) and baseline_n > 0:
        min_n = MIN_SAMPLE_FRACTION * baseline_n
        if n < min_n:
            return _escalate_no_data(
                history, state_path, reset_marker_path, pinned_model,
                "%s: only %d of baseline's %d golden answers were scorable this run "
                "(needs at least %.1f, %.0f%% of baseline n)"
                % (NO_DATA, n, baseline_n, min_n, MIN_SAMPLE_FRACTION * 100))

    # Only now, having confirmed this run's sample is large enough to
    # trust, does it break the NO-DATA streak: a genuinely scored,
    # adequately-sized run is real data, same discipline as model_drift
    # above.
    history["consecutive_no_data"] = 0

    accuracy_bad = accuracy < baseline_accuracy - tolerance
    history["consecutive_accuracy_below"] = history["consecutive_accuracy_below"] + 1 if accuracy_bad else 0

    have_brier_comparison = brier is not None and baseline_brier is not None
    brier_bad = have_brier_comparison and brier > baseline_brier + brier_tolerance
    if have_brier_comparison:
        history["consecutive_brier_worse"] = history["consecutive_brier_worse"] + 1 if brier_bad else 0
    # else: nothing to compare Brier against this run (legacy baseline or
    # nothing scorable); leave that streak exactly where it was rather
    # than guessing it either up or down.

    reasons = []
    if history["consecutive_accuracy_below"] >= CONSECUTIVE_ACCURACY_FAIL:
        reasons.append("accuracy dropped %d consecutive runs: old=%.4f new=%.4f n=%d tolerance=%.4f"
                        % (history["consecutive_accuracy_below"], baseline_accuracy, accuracy, n, tolerance))
    if history["consecutive_brier_worse"] >= CONSECUTIVE_BRIER_FAIL:
        reasons.append("Brier worsened %d consecutive runs: old=%.4f new=%.4f n=%d tolerance=%.4f"
                        % (history["consecutive_brier_worse"], baseline_brier, brier, n, brier_tolerance))

    state_write_err = _write_json_atomic(state_path, history)

    if reasons:
        marker_err = _write_reset_marker(reset_marker_path, "; ".join(reasons), pinned_model, models_seen)
        if marker_err:
            return EXIT_NO_DATA, "%s: FAIL (%s) but %s" % (NO_DATA, "; ".join(reasons), marker_err)
        return EXIT_FAIL, "FAIL: %s (reset marker written to %s)" % ("; ".join(reasons), reset_marker_path)

    # This run did not itself complete a FAIL-worthy streak. If it WAS a
    # bad run and its streak increment could not be persisted, the next
    # run would wrongly see "first strike" again instead of "second":
    # that silently defeats the whole consecutive-run rule, so it is
    # reported as NO-DATA rather than a clean PASS (a streak this module
    # cannot remember is a streak it cannot enforce: a control that
    # prevents beats a control that only reports).
    if state_write_err and (accuracy_bad or brier_bad):
        return EXIT_NO_DATA, ("%s: this run was below threshold but its streak could not be persisted (%s)"
                               % (NO_DATA, state_write_err))

    notes = []
    if accuracy_bad:
        notes.append("accuracy below threshold this run (%d/%d consecutive needed to FAIL)"
                      % (history["consecutive_accuracy_below"], CONSECUTIVE_ACCURACY_FAIL))
    if brier_bad:
        notes.append("Brier worse this run (%d/%d consecutive needed to FAIL)"
                      % (history["consecutive_brier_worse"], CONSECUTIVE_BRIER_FAIL))
    marker_data, marker_read_err = _read_json(reset_marker_path)
    if not marker_read_err and isinstance(marker_data, dict):
        notes.append("reset marker still present at %s (reason: %s, recorded %s); "
                     "clear it with --record-baseline"
                     % (reset_marker_path, marker_data.get("reason"), marker_data.get("time_human")))

    brier_txt = ("%.4f" % brier) if brier is not None else "NO-DATA"
    base_brier_txt = ("%.4f" % baseline_brier) if baseline_brier is not None else "NO-DATA (legacy baseline)"
    message = ("PASS: accuracy=%.4f n=%d baseline=%.4f tolerance=%.4f brier=%s baseline_brier=%s model=%s"
               % (accuracy, n, baseline_accuracy, tolerance, brier_txt, base_brier_txt, pinned_model))
    if notes:
        message += " | " + " | ".join(notes)
    return EXIT_PASS, message


# ---------------------------------------------------------------------------
# Item 1 (2026-09-19): the calibration ledger's own reliability, as a
# second, independent doctor-style check this same module can run --
# additive to the golden-set drift check above, never mixed into it (a
# corrupt calibration ledger and a drifted model are different facts, and
# run_canary()'s own tests must stay unaffected by this addition).
# ---------------------------------------------------------------------------

def check_ledger_reliability(decisions_path, outcomes_path, reset_marker_path=None, pinned_model=PINNED_MODEL):
    """Runs jev_calibration.ledger_reliability() and turns it into the
    same (exit_code, message) shape run_canary() returns, following this
    module's own established PASS/FAIL pattern rather than inventing a
    new one. FAIL also writes the SAME reset marker run_canary() writes
    on drift (jev_seam.py already treats that marker's mere presence as
    "force every seam off, for every mode, no call at all" -- see the
    module docstring's RESET MARKER section): a corrupt calibration
    ledger means any threshold jev_cascade.route() would compute from it
    is untrustworthy, exactly the same "before anything downstream
    trusts a Jev answer" charter this canary already exists to serve,
    so the existing fail-safe is reused rather than left toothless.
    There is no NO-DATA case here distinct from PASS: a ledger that has
    never been written to is a reliable, empty ledger by
    ledger_reliability()'s own contract (never fabricated as damage).
    """
    reset_marker_path = reset_marker_path or DEFAULT_RESET_MARKER
    res = jev_calibration.ledger_reliability(decisions_path, outcomes_path)
    if res["reliable"]:
        return EXIT_PASS, "PASS: calibration ledger reliable (%s, %s)" % (decisions_path, outcomes_path)
    marker_err = _write_reset_marker(
        reset_marker_path,
        "calibration ledger unreliable: %s" % res["reason"],
        pinned_model, [])
    if marker_err:
        return EXIT_FAIL, "FAIL: calibration ledger unreliable (%s) but %s" % (res["reason"], marker_err)
    return EXIT_FAIL, ("FAIL: calibration ledger unreliable: %s (reset marker written to %s)"
                        % (res["reason"], reset_marker_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Golden-set drift canary for Jev (JEV-05)")
    parser.add_argument("--dataset", default=jev_eval.DEFAULT_DATASET)
    parser.add_argument("--baseline", default=os.path.join(jev_eval.BENCH_DIR, "jev_canary_baseline.json"))
    parser.add_argument("--model", default=PINNED_MODEL)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--brier-tolerance", type=float, default=DEFAULT_BRIER_TOLERANCE)
    parser.add_argument("--reset-marker", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--ledger-status", action="store_true",
                         help="item 1, 2026-09-19: instead of the golden-set drift check, "
                              "run jev_calibration.ledger_reliability() against --decisions/"
                              "--outcomes and report PASS/FAIL the same way")
    parser.add_argument("--decisions", default=None,
                         help="decisions ledger path for --ledger-status "
                              "(default: DEFAULT_LEDGER_DIR/decisions.jsonl)")
    parser.add_argument("--outcomes", default=None,
                         help="outcomes ledger path for --ledger-status "
                              "(default: DEFAULT_LEDGER_DIR/outcomes.jsonl)")
    args = parser.parse_args(argv)

    if args.ledger_status:
        decisions_path = args.decisions or os.path.join(DEFAULT_LEDGER_DIR, "decisions.jsonl")
        outcomes_path = args.outcomes or os.path.join(DEFAULT_LEDGER_DIR, "outcomes.jsonl")
        code, message = check_ledger_reliability(
            decisions_path, outcomes_path, reset_marker_path=args.reset_marker, pinned_model=args.model)
        print(message)
        return code

    code, message = run_canary(
        args.dataset, args.baseline, pinned_model=args.model, tolerance=args.tolerance,
        brier_tolerance=args.brier_tolerance, reset_marker_path=args.reset_marker,
        state_path=args.state, record_baseline=args.record_baseline,
    )
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
