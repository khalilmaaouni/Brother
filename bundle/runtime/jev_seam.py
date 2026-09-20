#!/usr/bin/env python3
"""jev_seam: A0.2/A0.6 of the 1.0.20 orchestration control plane, the one
shared helper every harness seam calls to ask Jev a registry-defined
question without deciding, on its own, whether to trust the answer.

WHY THIS EXISTS. jev_registry.py (JEV-04) already refuses to let a caller
ask a MUST_NOT or dirty entry; jev_decide.py (JEV-01) already refuses a
malformed question or an answer that did not really come from Jev;
jev_calibration.py (JEV-02) already measures, from real outcomes, whether
a confidence level can be trusted; jev_cascade.py (JEV-03) already decides
ACT/ESCALATE/NO-DATA from that measurement plus a signed promotion. None
of those four modules decide WHETHER a given harness call site is even
allowed to change behaviour on Jev's answer yet (shadow vs. advise vs.
act), and none of them write the calibration ledger row a caller's own
answer needs on the way in. Without this module, every one of the
registry's 116 call sites would re-implement that wiring -- the mode
lookup, the ledger append, the canary safety check, the audit-sampling fix
for calibration bias, the near-threshold both-orders probe -- itself, on
its own schedule, inevitably drifting. consult() is the one place all of
that lives, so wiring a new seam is one call site plus one row in
data/jev-seams.json.

THE ONE ENTRY POINT.

  consult(entry_id, state, current_answer, *, seams_config, registry,
          ledger_dir, runner=None, rng=None) -> SeamResult

  SeamResult(answer, jev, mode, reason, audit, decision_id):
    answer        what the CALLER must use. Never Jev's answer unless the
                  entry's resolved mode is "act" AND jev_cascade.route()
                  actually returned ACT for it; every other path, of every
                  mode, returns `current_answer` unchanged.
    jev           the Jev Decision record (see jev_decide.py) this call
                  produced, or None when Jev was never actually asked
                  (mode off, a registry refusal, or a NO-DATA from
                  decide()).
    mode          the mode this call actually ran under: "off", "shadow",
                  "advise" or "act". This is the EFFECTIVE mode, which can
                  differ from the configured one only when the canary
                  reset marker forces act/advise down to shadow (see rule
                  3 below); the refusal path (rule 1) reports the
                  CONFIGURED mode unchanged, since no call was made to
                  force anything down from.
    reason        None on an ordinary act/shadow/advise pass with nothing
                  to explain, else a human sentence: why the mode is off,
                  why the registry refused, why decide() returned
                  NO-DATA, why the canary forced shadow, why a
                  near-threshold choice disagreed on order, or why act
                  mode did not ACT (jev_cascade's own reason, or "no
                  usable confidence for cascade routing").
    audit         True when this decision was chosen, by the A0.6 audit
                  sample, for a human to label even though it would not
                  otherwise be escalated. See AUDIT SAMPLING below.
    decision_id   the id this call actually WROTE to the calibration
                  ledger (MAJOR fix, re-review of 1385ab88c: never "would
                  have written"). None whenever nothing was written:
                  mode off, a registry refusal, a NO-DATA from decide()
                  (primary or the reordered probe), a near-threshold
                  disagreement, or a record with no usable numeric
                  probability/confidence for its qtype. UNIQUE per call
                  now, never shared with an earlier identical call: see
                  _decision_id() and rule 4 below.

RISK CLASS COMES FROM THE REGISTRY, NEVER FROM A CALLER (adversarial
review finding, folded in before this unit shipped). consult() takes no
risk_class argument. The one risk_class jev_cascade.route() is ever asked
about is entry["risk"], read fresh from the registry entry this call
resolved. A caller cannot downgrade a "high" or "critical" registry entry
to "low" by passing a friendlier value in: there is nowhere in this
module's signature to pass one.

MODES, per data/jev-seams.json (default "off" for any entry not listed):
  off       no call to Jev at all. answer=current_answer, jev=None,
            audit=False, decision_id=None. Nothing else in this module
            runs: not the registry lookup, not the ledger, not the canary
            check.
  shadow    Jev is asked and the answer is recorded, but answer is
            ALWAYS current_answer, whatever Jev said.
  advise    identical to shadow for the returned `answer` (still
            current_answer); the difference is for the caller's own UI,
            which may choose to DISPLAY `jev.answer` alongside the real
            answer. This module makes no distinction in its own behaviour
            beyond carrying the same `jev` record either way.
  act       answer is Jev's answer ONLY when jev_cascade.route() returns
            ACT for (family=entry_id, qtype, risk_class=entry["risk"],
            model=jev's own reported model, confidence). Any other route()
            outcome (ESCALATE or NO-DATA), or a decision with no usable
            confidence to route on at all, falls back to current_answer.
            NEVER on an abstain-shaped answer either (review 70268c3,
            C3): a choice OR SCORE answer whose chosen option NAME
            contains "unknown" (case-insensitive -- the registry's own
            required abstain option, jev_registry lints every
            choice/score entry for one; the score half of this was
            missed in the first pass and fixed in re-review of 1385ab88c),
            or a noul probability inside [0.2, 0.8] (ABSTAIN_LOW,
            ABSTAIN_HIGH), is never routed to ACT and never counted as
            confident for the A0.6 audit sample either: this module treats
            it exactly as "no usable confidence", but with the actual
            named reason (see _abstain_reason()) rather than the generic
            one, so the caller can tell an abstain apart from missing
            calibration evidence.

            NOUL ANSWERS STAY PROBABILITIES (minor fix, review 70268c3: do
            not change the return type here). When act mode does return
            Jev's own answer for a noul question, that answer is the raw
            probability jev_decide.decide() reported (a noul answer IS a
            probability, see its own docstring), never rounded to a
            boolean by this module. A caller wired to a yes/no noul entry
            maps that probability to its own decision itself.

            THE PROMOTIONS STORE NEVER LIVES UNDER ledger_dir (M3 fix,
            review 70268c3). The CalibrationHandle this module builds for
            route() reads its promotions from _promotions_path()'s own
            result: seams_config["promotions_path"] when given, else
            jev_cascade.DEFAULT_PROMOTIONS_PATH (a repo-root data/ path).
            Never a path this module derives from ledger_dir, which is a
            directory THIS module itself writes decisions/outcomes to on
            every call -- a founder-signed promotion record must live
            somewhere no seam write path can reach.

THE CANARY OVERRIDE (rule 3). Before anything else runs, this module
checks whether the reset marker jev_canary.py (JEV-05) writes on a drift
FAIL exists (path from seams_config["canary_reset_marker_path"], default
DEFAULT_CANARY_MARKER). If it does, every act or advise entry for this
call is treated as shadow, and `reason` names why. This is checked once
per call, fresh, never cached: a canary FAIL between two consult() calls
for the same entry takes effect on the very next call, without this
module or its caller needing to restart or re-load anything.

AN EXPLICIT RELATIVE OVERRIDE STILL RESOLVES FROM THE REPO ROOT (M1 fix,
review 70268c3, unchanged by item 1 below). A relative
canary_reset_marker_path given in data/jev-seams.json is resolved against
the REPO ROOT (dirname(dirname(abspath(__file__))), _REPO_ROOT below),
never against whatever the running process's cwd happens to be; an
absolute override is used unchanged. Before the original M1 fix, this
module's own default AND a relative override were both resolved against
the process cwd instead, so a canary FAIL almost never actually reached a
seam invoked from a different working directory.

THE UNCONFIGURED DEFAULT NO LONGER LIVES INSIDE ANY CHECKOUT AT ALL (item
1, approved-with-nits review 2026-09-18). M1's own fix moved the
DEFAULT off the process cwd and onto _REPO_ROOT -- correct against the
cwd bug it targeted, but a repo-root path is still INSIDE whichever
checkout happened to run the canary or the seam. A `git clean -X`, or
removing the worktree the canary ran the last FAIL from, deletes that
marker outright; a seam consult() running from a SIBLING checkout or
worktree of the same repo (a common shape for this estate: multiple
worktrees, multiple sessions) never saw a repo-root marker written by a
different checkout's canary, since each checkout has its own
_REPO_ROOT and therefore its own marker file. DEFAULT_CANARY_MARKER
(below) is now built from JEV_STATE_DIR instead: one machine-level
directory (default ~/.brother/jev, override BROTHER_JEV_STATE_DIR) both
this module and jev_canary.py resolve to via the IDENTICAL expression,
regardless of which checkout, worktree, or cwd either one is running
from. jev_canary.py defines JEV_STATE_DIR the same way rather than
importing it from here, per this file's own header discipline (a sibling
module reused, never copied) -- see jev_canary.py's own module docstring
for its half of this fix. DEFAULT_LEDGER_DIR (below) moves for the exact
same reason: the human-labelled calibration ledger is a fact about what a
human actually labelled, not tree content a `git clean -X` may ever
discard.

THE CALIBRATION LEDGER WRITE (rule 4). Every call that reaches Jev (any
mode but off, not refused by the registry, not a NO-DATA from decide(),
and not thrown out by the near-threshold disagreement check) appends one
decision record to ledger_dir/decisions.jsonl via
jev_calibration.append_decision(), with a decision_id that is UNIQUE PER
CALL (MAJOR fix, orchestrator decision, re-review of 1385ab88c -- see
_decision_id()): a caller that calls consult() twice for the exact same
(entry_id, state) gets TWO DISTINCT ids back and TWO SEPARATE ledger
rows, never a deduplicated id -- a repeated identical question is a
repeated identical measurement, not the same measurement read twice.
The returned SeamResult.decision_id is the id actually written, never
"the id this call would have written": every non-writing path (mode off,
a registry refusal, a NO-DATA from decide() or the reordered probe, a
near-threshold disagreement, or a record with no usable probability/
confidence) returns decision_id=None instead, so no caller can label a
decision that does not exist in the ledger. The A0.6 audit sample below
stays deterministic PER decision_id exactly as before: it is a pure
function of whatever id a call actually used, unaffected by that id no
longer being a pure function of (entry_id, state) itself.
A record jev_decide handed back with no usable numeric probability or
confidence for its qtype (see _ledger_decision()) is never forced into
the ledger with an invented number: it is simply not written, and the
call still returns its Jev record and mode normally (decision_id=None).

AUDIT SAMPLING (rule 5, A0.6). A calibration ledger that only ever
collects outcomes for ESCALATEd decisions is measuring the wrong
population: the confident, would-ACT decisions -- the ones a caller is
actually trusting Jev's number for -- would never get a human label at
all, and "we are 98 percent precise" would be true only of the hard cases
nobody trusted Jev on. So whenever this call's decision is confident
enough that jev_cascade's own calibrated threshold would NOT escalate it
(reusing jev_cascade.RISK_TARGET_PRECISION and MIN_SAMPLES, the same
inputs route() itself uses, never a second copy of that policy), a
deterministic-per-decision_id sample of seams_config["audit_rate"]
(default DEFAULT_AUDIT_RATE) is flagged audit=True, so a human labels it
anyway. The sample is a hash of decision_id, not real randomness, so a
retried call for the same (entry_id, state) always samples the same way;
`rng`, when given (a zero-argument callable returning a float in [0, 1)),
overrides the hash for a test that wants to force one branch or the
other. Never computed, and never True, for "critical" risk (never acts,
so there is no ACT-vs-ESCALATE bias to correct for), an unrecognised risk
class, or a decision whose confidence could not be resolved.

THE NEAR-THRESHOLD BOTH-ORDERS PROBE (rule 6). Only for a choice
question, and only once the calibrated threshold for this
(entry_id, qtype, risk_class) is known: when the winning answer's own
probability sits within NEAR_THRESHOLD_BAND (0.1) of that threshold, this
module asks the SAME question a second time with jev_registry.both_orders'
reversed option order, and if the two answers disagree, the whole call is
NO-DATA (answer=current_answer, jev=None, nothing written to the ledger)
rather than trusting whichever order happened to be asked first. A
question whose winning probability is not close to the threshold, or
whose registry risk has no calibration policy (RISK_TARGET_PRECISION),
skips this probe entirely: it is a targeted check on the exact cases
where option order could plausibly flip the routing decision, not a
doubled cost on every call.

THE PROBE USES THE SAME NUMBER ROUTING USES (M4 fix, review 70268c3).
The "winning answer's own probability" above is
_confidence_for_routing()'s number (the bridge's own reported
"confidence" when present, else the chosen option's probability) -- the
EXACT number _cascade.route() is later called with -- never a separate
"probability first" number. Before this fix the probe compared against
record["probability"] while routing used record["confidence"]; a choice
answer with probability 0.5 and confidence 0.99 sat far from the
threshold by the probe's number but well within it by routing's, so the
probe never ran and the disagreement it exists to catch was never
checked before acting.

CONTINGENCY, edge by edge.

  entry_id not in seams_config["modes"]      off (the documented default),
                                              no registry call at all.
  seams_config is not a dict, or "modes" is  every entry treated as off:
  missing or not itself a dict               a malformed or absent config
                                              file is never read as
                                              licence to act.
  an entry's configured mode is not one of   treated as off: an unrecognised
  off/shadow/advise/act                      mode string is a config typo,
                                              never guessed at.
  registry.callable() refuses (unknown id,   no call, answer=current_answer,
  MUST_NOT, must_stay_on_machine, dirty      jev=None, mode=configured mode
  entry)                                     unchanged, reason=the refusal.
  decide() returns NO-DATA (bridge down,     answer=current_answer, jev=None,
  gate refusal, malformed response, wrong    reason names it, decision_id
  model)                                     is None (MAJOR fix, re-review
                                              of 1385ab88c: never "the id
                                              this call would have used"):
                                              nothing was written to the
                                              ledger.
  the reordered probe itself returns         the whole call is NO-DATA, the
  NO-DATA                                    same as the primary call
                                              failing: decision_id is None,
                                              a probe that could not even
                                              run cannot rule out
                                              disagreement, and nothing was
                                              written either way.
  a decision record with no numeric          the answer/mode/reason/audit are
  probability or confidence usable for its   still returned normally; only
  qtype (jev_decide's own documented "score  the ledger append is skipped
  has no probability" case, or a malformed   (see _ledger_decision()), so
  bridge answer)                             decision_id is None; act mode
                                              falls back to current_answer
                                              with "no usable confidence
                                              for cascade routing" rather
                                              than calling route() with a
                                              fabricated number.
  act mode, route() raises ValueError (a     caught, answer=current_answer,
  structurally invalid decision object this  reason names it: this module
  module itself failed to build correctly),  never lets a defensive
  OSError (a promotions-file read inside     programming bug in its own
  the cascade, minor fix review 70268c3),    decision-object construction,
  or any other unexpected exception          an OSError from a file this
                                              module does not itself
                                              control, or any other
                                              surprise from the cascade,
                                              escape as an uncaught
                                              exception past a harness
                                              call site: consult() never
                                              raises, full stop.
  act mode, the chosen answer is an          answer=current_answer, never
  abstain signal (a choice OR SCORE option   counted as confident for the
  NAMED "unknown", case-insensitive, or a    A0.6 audit sample either:
  noul probability inside the abstain band   reason names the abstain
  [0.2, 0.8])                                cause, not the generic "no
                                              usable confidence" message.
  risk_class is "critical", unrecognised, or route() itself decides (never
  has no calibration evidence yet            re-derived here): critical
                                              never acts by POLICY, an
                                              unrecognised class and no
                                              evidence both ESCALATE. This
                                              module never second-guesses
                                              that decision by inspecting
                                              risk_class itself beyond
                                              reading it once, unmodified,
                                              from the registry entry.
  canary marker present, mode is shadow or   nothing changes: shadow was
  off                                        already the safe case, off
                                              never reached the check.
  two consult() calls for the same           TWO DISTINCT decision_ids and
  (entry_id, state), even the literal        two separate ledger rows
  same call repeated (MAJOR fix,             (see rule 4): a repeated
  orchestrator decision, re-review of        identical question is treated
  1385ab88c)                                 as two real decisions, never
                                              deduplicated into one; the
                                              A0.6 audit sample is computed
                                              independently for each, since
                                              it is a pure function of
                                              whichever id a given call
                                              actually used.
  a seams_config["promotions_path"]          refused: falls back to
  override that resolves under ledger_dir    jev_cascade.DEFAULT_PROMOTIONS
  (Minor fix, re-review of 1385ab88c)        _PATH, the same as an
                                              unconfigured call (see
                                              _promotions_path()).

A0.8, THE ENABLEMENT GATE (2026-09-19). Four findings from an adversarial
review of the design above, before any Jev seam is switched from off to
shadow: a shadow call was synchronous (a hung or down API stalled the
caller on a hot path), there was no cost cap or breaker, the ledger grew
without bound, and a canary failure only dropped seams to shadow, which
still called the failing API. All four are fixed here, in consult() and in
jev_calibration.py's own ledger (see that module's LEDGER ROTATION
section); nothing above this point changed meaning, only what happens
around the edges of "make the Jev call".

CANARY FAIL NOW MEANS OFF, NOT SHADOW (supersedes rule 3 above). The
canary reset marker check (still read from _canary_marker_path()) now runs
BEFORE the registry lookup, exactly where the OFF-mode check itself runs:
when the marker is present, this call behaves in EVERY respect like a
seam configured off (mode="off" in the returned SeamResult, no registry
read, no decide() call, no ledger write, no budget consumed), for every
configured mode including shadow -- "shadow" no longer means "call Jev but
never act on it", it now means "watching, but not while the canary says
Jev has drifted". The old `effective_mode` distinction (configured mode
forced down to shadow) is gone along with it: past this check, the mode a
call runs under is simply its configured mode, unchanged.

BUDGET AND BREAKER (a global admission gate, not per-entry). Independent
review fixes, 2026-09-19 (C1, M1): both were re-worked after a live probe
found each one silently admitting calls it was supposed to deny.
  breaker   _breaker_admission()  (renamed from the first draft's
            _breaker_denied_reason(): it now also reports whether THIS
            call claimed the half-open probe slot, see below): after
            BREAKER_FAIL_THRESHOLD (default 5, config key
            "breaker_fail_threshold") consecutive Jev *timeouts or
            NO-DATA answers* (in-memory, module-global, across every
            entry_id -- a hung or down Jev is a fact about Jev, not about
            which seam happened to ask), every seam behaves as off for
            BREAKER_COOLDOWN_S (default 600, config key
            "breaker_cooldown_s") from the moment it opened. After the
            cool-off elapses, exactly ONE concurrent caller is admitted as
            a half-open probe (`_BREAKER_STATE["probing"]`, review minor:
            every concurrent caller used to get through at once, which is
            not what the docstring ever claimed); every other concurrent
            caller is denied with its own "already in flight" reason.
            Success closes the breaker (resets the streak to zero and
            releases the probe claim); a failure or timeout re-opens it,
            restarts the cool-off, and also releases the claim. A probe
            that is ADMITTED but then refused for an unrelated reason
            (registry, promotions path, budget, inflight -- see
            `claimed_probe`/_breaker_release_probe() at each such return
            site below) releases its claim too, so a probe that never
            actually ran never locks out every future one. The in-memory
            `_BREAKER_STATE` is process-local by design (no cross-process
            file, unlike the budget below): a fresh process starts closed.
  budget    _consume_budget_slot(): a calendar-day call budget (UTC date,
            default DEFAULT_DAILY_CALL_BUDGET=2000, config key
            "daily_call_budget"), persisted to JEV_STATE_DIR (default
            DEFAULT_BUDGET_PATH, config key "budget_path") so a restarted
            process does not reopen a budget a still-running process
            already spent, atomic temp-file-then-os.replace writes
            (mirrors jev_canary.py's own _write_json_atomic pattern,
            duplicated rather than imported: same reasoning as
            JEV_STATE_DIR above -- this module and jev_canary
            deliberately never import each other).
            C1 FIX (CRITICAL, independent review 2026-09-19): FAILS
            CLOSED. A present-but-corrupt or unreadable budget file, or a
            failed persist, now DENIES the call -- probed before this fix:
            a read-only budget directory with a limit of 2 admitted 10 of
            10 calls, because "cannot read the file" and "cannot write the
            file" were both silently treated as "zero calls used so far".
            _read_budget_state() distinguishes "missing" (the ordinary
            fresh-day case: zero used) from "corrupt" (deny) on the read
            side; a failed write also denies, never admits an untracked
            call. m2 FIX (independent re-review, 2026-09-19): the file's
            `date` field is now validated as a strict, zero-padded ISO
            "YYYY-MM-DD" calendar date (see _parse_iso_calendar_date()) --
            a malformed date (probed: "19/09/2026", "0", "zzzz") is
            corrupt, refused exactly like an unreadable file. A date
            LATER than today is also refused outright, rather than the
            original review-minor fix's "the later of (today, the file's
            own stored date) wins", which kept silently admitting/denying
            calls against that future date until the real clock caught up
            to it (probed: a file dated 2027-03-01 after a forward clock
            excursion locked the budget with no operator-visible cause).
            Refusing is STRICTER than the old rule in every case, so a
            system clock set BACKWARDS still cannot reset the count: it
            now denies every call outright instead of silently continuing
            to count against the (later, real) stored date -- denial is a
            subset of "never exceeds the daily limit", so the original
            anti-bypass goal is still met, just via a named refusal that
            also says how to reset (delete the file) rather than a silent
            multi-week lock.
            M1 FIX (independent review 2026-09-19): checked and consumed
            in ONE call (_consume_budget_slot()) after the registry,
            promotions-path, AND INFLIGHT checks all pass -- moved after
            inflight specifically, not merely after registry/promotions,
            because the ORIGINAL ordering spent the slot BEFORE checking
            inflight: one hung runner filling every inflight slot could
            silently drain an entire day's budget with zero real
            attempts (probed: max_inflight_calls=1, budget 5, 8 calls ->
            1 real attempt, 4 drops, 3 "budget exhausted", file read
            {"calls": 5}). A call the registry would have refused, or
            that gets dropped for queue-full, now never burns a slot.
            ponytail: a
            process-local lock (_BUDGET_LOCK) still makes THIS correct within
            one process; two processes racing the same budget file could
            each read the same stale count and both increment, overshooting
            the daily ceiling by a handful of calls under real concurrency
            -- a soft daily ceiling, not a cross-process transaction, and
            the ceiling this control exists to protect (an API bill, a
            rate limit) tolerates that overshoot. Add fcntl.flock-based
            locking if it ever does not.
Both report mode="off" with a reason naming which gate fired, exactly like
the canary check: no registry read, no decide() call, no ledger write.

NON-BLOCKING SHADOW; BOUNDED ADVISE AND ACT (never hung). Once every
admission gate above has passed and the registry/promotions/decision_id
work is done, the actual reach to Jev -- decide(), the near-threshold
reorder probe, the ledger write, and (for act) jev_cascade.route() -- runs
on a per-call daemon thread (_run_seam_job()), never on the caller's own
thread. _fires_and_forgets(mode_cfg) decides what happens next, and is
NOT the same for every mode (M3 fix, independent review 2026-09-19,
correcting the first draft of this section, which had shadow AND advise
both fire-and-forget):

  shadow          consult() NEVER waits for the worker, not even bounded
                  by the deadline: shadow's own return value never depends
                  on Jev's answer, so waiting for it, even briefly, buys
                  nothing. Returns at once: answer=current_answer,
                  jev=None, reason="shadow: submitted", audit=False,
                  decision_id=None (N1 fix, independent re-review,
                  2026-09-19: RESTORES the foundations contract -- see the
                  module docstring's rule 4 and SeamResult's own field
                  docs above -- that an id is only ever returned once it
                  has actually been written. The prior draft handed back
                  the id the worker WOULD write under, generated on the
                  calling thread before the worker even started, as a
                  "deliberate, narrow exception"; that exception was
                  itself the defect: a caller cannot tell a written id
                  from an id whose worker later hit a bridge NO-DATA, a
                  near-threshold disagreement, or a local write error
                  (all routine, not rare), and a human labelling an
                  outcome against an id that was never written poisons
                  that whole family's calibration as a ledger anomaly for
                  as long as the label is retained -- see
                  jev_calibration.append_outcome()'s own decisions_path
                  refusal, added for the same reason). The thread keeps
                  running regardless, and writes its ledger row (with the
                  A0.6 `audit` sample flag stored IN the row -- see
                  _ledger_decision()) whenever it finishes, however long
                  that takes; a caller with a genuine need to know a
                  shadow call's eventual id must read the ledger itself,
                  never trust a value handed back before the write.
  advise, act     consult() WAITS for the worker, for at most
                  _hard_deadline(seams_config) seconds (default
                  DEFAULT_HARD_DEADLINE_S=2.0, config key
                  "call_deadline_s") via a threading.Event. Advise waits
                  because it exists so a caller's OWN interface can
                  DISPLAY Jev's answer (unchanged docstring, MODES
                  section, above): that is impossible without waiting for
                  it, and advise is documented as being for display, never
                  a hot path -- a caller that cannot afford to wait uses
                  shadow instead, precisely because shadow throws the
                  answer away and advise does not. Act waits because its
                  OWN return value can change based on Jev's answer. If
                  the thread finishes first, consult() returns its full
                  result exactly as if it had run synchronously (a fast
                  runner, the common and default case, never sees any
                  difference at all). If the deadline is reached first,
                  consult() returns current_answer with jev=None,
                  decision_id=None, reason naming the timeout -- but the
                  thread itself is NEVER cancelled or abandoned: it keeps
                  running, and if it eventually gets an answer, it still
                  writes the ledger row itself, just too late for THIS
                  call's own return value to reflect it.

A concurrency cap (_try_acquire_inflight(), default
DEFAULT_MAX_INFLIGHT=32, config key "max_inflight_calls", a plain
counter+lock rather than threading.BoundedSemaphore because the cap must
be read fresh from seams_config on every call rather than fixed once at
construction) bounds how many of these background threads may be running
at once; a call that would exceed it is DROPPED immediately (never
blocks): mode=<configured mode>, reason names the drop, jev=None,
decision_id=None, no thread spawned, no budget slot consumed by a call
that made no attempt (M1 fix: checked BEFORE the budget spend -- see
BUDGET AND BREAKER above). A thread-creation failure at worker.start()
itself (review minor, 2026-09-19: an OS thread-limit error used to escape
consult() as a raw RuntimeError and leak the already-acquired inflight
slot) is caught, the slot released, and reported the same way as any
other admission failure: consult() never raises.

DRAIN, FOR A SHORT-LIVED PROCESS (M4 fix, independent review 2026-09-19).
Every worker thread consult() actually starts is tracked in
_ACTIVE_WORKERS; drain(timeout=None) (default DEFAULT_DRAIN_TIMEOUT_S=3.0)
waits up to that bound for every one of them to finish, so a short-lived
process (a CLI command, a brothersbe kickoff "moment") gives its own
already-dispatched shadow/advise calls a real chance to land before
exiting. THE PROBE THIS ANSWERS: a shadow call against a runner that took
0.3s, followed immediately by process exit, spent a budget slot ({"calls":
1} on disk) but left the ledger folder EMPTY -- the worker never got to
finish. A call site that knows it is about to do something depending on
the ledger, or is about to exit deliberately, should call drain()
directly, with whatever timeout it can afford, rather than relying on the
atexit fallback below.

THE ATEXIT DRAIN HAS ITS OWN BOUNDED, CONFIGURABLE WAIT (N2 fix,
independent re-review, 2026-09-19). atexit.register() below calls
_atexit_drain(), never drain() directly, once, for every process that
imports this module (a no-op when nothing is in flight): it reads
"exit_drain_s" fresh from load_seams_config()'s OWN file-backed config
(the real data/jev-seams.json a deployed process actually runs under,
per-path cached exactly as every other seams_config read is -- an atexit
hook fires with no call-specific seams_config in hand, since it is
registered once at import time, so this is the one config source it CAN
read) -- default DEFAULT_EXIT_DRAIN_S=1.0, clamped to [0, 10]; an
unknown, non-numeric, or out-of-range value falls back to the default
(see _exit_drain_s()) -- and passes THAT as drain()'s timeout,
deliberately smaller than drain()'s own 3.0s default for a call site
that drains itself deliberately. ponytail: a process that calls
consult() with several DIFFERENT in-memory seams_config dicts (rather
than the one load_seams_config() itself would return for the default
path) still gets only the ONE exit_drain_s this reads at exit, not a
per-call value; add a way to register a process-wide override if that
ever matters in practice. WHY A SEPARATE, SMALLER
DEFAULT: registry "moments" run as short-lived CLI processes, and every
one of them pays this wait on every exit, whether or not anything is
actually in flight for it (a no-op when _ACTIVE_WORKERS is empty, but the
wait itself is not free when something IS in flight) -- probed with a
real interpreter exit against a 5s bridge: the process did not exit until
process_wall_s=3.11 under the OLD fixed 3.0s atexit wait, bringing back,
for every short-lived caller, the exact "a hung or down API stalled the
caller" finding A0.8 exists to fix, just moved from consult()'s own
return to process exit. A shadow row lost this way (the worker was still
running when the configured exit_drain_s ran out) is ACCEPTED, not
silently unaccounted for: consult() already spent this call's budget slot
and dispatched the worker in good faith, and the row's absence is counted
in DROP_COUNTS under the "__exit_drain__" key (see _atexit_drain()) so an
operator can see how often this happens rather than only inferring it
from a missing ledger row. A call site that cannot tolerate ANY row loss
at exit sets seams_config["exit_drain_s"] to something larger (up to 10s,
the clamp ceiling), or calls drain() itself with an even larger timeout
before exiting, rather than relying on this bounded default.

WHY A PER-CALL THREAD, NOT A SHARED QUEUE. The brief offered either "a
bounded queue plus a daemon thread per process" or "a per-call thread with
join timeout". A single shared worker thread draining one queue would
give one slow call a head-of-line blocking effect on every other seam's
calls behind it in the queue; a per-call thread has no such coupling, and
the inflight counter above already gives the "bounded" property the
brief's queue option was after (a cap on concurrently RUNNING calls, not
literally a data structure named a queue) at a fraction of the mechanism.
Python threads are cheap enough for this call volume (a handful of Jev
consultations per harness run, never a hot per-request loop), so the
extra thread-per-call is not a real cost here.

BREAKER OUTCOME CLASSIFICATION happens wherever the outcome is actually
known first. For advise and act, that is the CALLER's thread, after
waiting: a timeout (the deadline elapsed with no result) always counts as
a breaker failure, regardless of what the background thread eventually
decides. For shadow, which never waits (M3 fix, above), there is no
caller thread left by the time the worker finishes, so the WORKER
classifies its own outcome, in its own `finally` block
(`record_breaker_here`, see _run_seam_job()'s own docstring) -- whenever
it finishes, however long that takes; a shadow call is therefore never
double-counted (the caller records nothing for it) and never left
unclassified (the worker always does, eventually). Either way, a
completed job counts as a failure only when its PRIMARY decide() call
itself returned NO-DATA (a real bridge/API problem), never for a
near-threshold reorder disagreement or an abstain (both are business
outcomes from a Jev that answered fine), and never for a local ledger
write's own OSError (M2 fix, above: a filesystem problem on THIS
machine is never Jev's fault). Every other completed job -- shadow,
advise, or act, ACTed or not -- counts as a success and closes the
breaker.

Python 3.9 floor, standard library only, no network in this module itself
(network lives inside decide()'s bridge subprocess, exactly as in
jev_decide.py, jev_cascade.py and jev_canary.py).
"""
import atexit
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_calibration as _calibration  # noqa: E402  (sibling module, reused not copied)
import jev_cascade as _cascade  # noqa: E402  (sibling module, reused not copied)
import jev_decide as _decide  # noqa: E402  (sibling module, reused not copied)
import jev_registry as _registry  # noqa: E402  (sibling module, reused not copied)

#: The four modes a registry entry can be configured to run under. Any
#: other string in data/jev-seams.json is treated as OFF (see
#: _resolve_mode()): a config typo is never guessed into a behaviour.
OFF = "off"
SHADOW = "shadow"
ADVISE = "advise"
ACT = "act"
_VALID_MODES = (OFF, SHADOW, ADVISE, ACT)

#: The repo root, resolved the IDENTICAL way jev_canary.py's own
#: DEFAULT_RESET_MARKER contract resolves it (M1 fix, review 70268c3):
#: repo root = dirname(dirname(abspath(<module's own __file__>))). Kept
#: as this module's own expression rather than an import of jev_canary
#: (a sibling module reused, never copied, per this file's own header
#: discipline) -- both modules live directly under scripts/, one level
#: under the repo root, so the same expression resolves to the same path
#: from either file.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Where data/jev-seams.json and data/jev-registry.json live by default,
#: relative to _REPO_ROOT above (ported from a sibling worktree's wave-2
#: patch, g1-jev_seam-cache.patch: ONE loader per path lives in this
#: foundation module, not reimplemented per call site or duplicated into a
#: second module). Both are TREE content, reviewed and shipped with the
#: code that reads them -- unlike DEFAULT_LEDGER_DIR below, which is not.
DEFAULT_SEAMS_CONFIG_PATH = os.path.join(_REPO_ROOT, "data", "jev-seams.json")
DEFAULT_REGISTRY_PATH = os.path.join(_REPO_ROOT, "data", "jev-registry.json")

#: The one machine-level root for Jev state that must outlive any single
#: checkout (item 1, approved-with-nits review 2026-09-18): the
#: human-labelled calibration ledger and the canary's drift reset marker
#: are facts about the outside world -- what a human actually labelled,
#: whether the pinned model actually drifted -- never tree content a
#: `git clean -X` or a worktree removal is entitled to discard, and never
#: private to the one checkout that happened to write them: a canary FAIL
#: recorded from one checkout or worktree must still be seen by a seam
#: consult() running from a DIFFERENT checkout of the same repo.
#: BROTHER_JEV_STATE_DIR overrides it; every test in this module sets
#: this env var (or patches this constant directly) to a temp directory,
#: never touching the real home path. jev_canary.py defines the IDENTICAL
#: expression under the identical name rather than importing it from here
#: (a sibling module reused, never copied, per this file's own header
#: discipline) -- both constants must resolve to the exact same directory
#: without either module importing the other.
JEV_STATE_DIR = os.environ.get("BROTHER_JEV_STATE_DIR") or os.path.expanduser("~/.brother/jev")

#: The calibration ledger's default directory, under JEV_STATE_DIR, never
#: under _REPO_ROOT (item 1, approved-with-nits review 2026-09-18: see
#: JEV_STATE_DIR above for why). consult()'s own ledger_dir parameter has
#: no default of its own -- every call site passes one explicitly -- this
#: constant is the value a call site should pass to share the one
#: machine-level ledger rather than keep a private one.
DEFAULT_LEDGER_DIR = os.path.join(JEV_STATE_DIR, "ledger")

#: How often load_seams_config() is willing to touch the filesystem at
#: all, per distinct path: at most one os.stat() per this many seconds,
#: and a re-read of the file only when that stat's mtime actually moved.
#: A call inside this window is a pure in-process dict lookup: no stat,
#: no open, no import beyond the module already in sys.modules. Measured
#: need: a seam in off mode (the production default -- data/jev-seams.json
#: ships with every entry off) was found to make a hot call site 52x
#: slower by re-opening this file on every single call.
_CONFIG_CACHE_INTERVAL_S = 1.0

#: One cache entry per path this process has ever loaded, so a test that
#: points load_seams_config() at a different path (or the real caller
#: hitting the real default) never share a stale entry.
_CONFIG_CACHE = {}


def load_seams_config(path=None):
    """The seams_config dict a harness call site hands to consult(), read
    from `path` (default DEFAULT_SEAMS_CONFIG_PATH) and cached in-process
    per _CONFIG_CACHE_INTERVAL_S: see that constant's own docstring for
    why. Never raises: a missing or malformed file returns {}, which
    _resolve_mode() already treats as "every entry off", the exact safe
    default consult() falls back to for a malformed config on its own.
    One loader every wave-1 call site shares, so a guarded block never
    re-implements JSON reading, and a live mode flip in
    data/jev-seams.json is still picked up WITHIN ONE
    _CONFIG_CACHE_INTERVAL_S (one second) of the write (item 5,
    approved-with-nits review 2026-09-18): the cache trusts the file's
    modification time (os.stat().st_mtime), not a content hash, as its
    only dirty signal, so this relies on the write actually moving that
    mtime forward -- true of an ordinary editor or atomic-replace write,
    but a mode flip is never picked up FASTER than the next re-stat after
    the interval elapses, and a filesystem whose mtime resolution is
    coarser than one second (rare, but not impossible) could in principle
    still read as unchanged for a moment longer.

    EVERY CALL RETURNS A FRESH COPY (item 4, approved-with-nits review
    2026-09-18): the dict handed back is copy.deepcopy()'d from the
    cached entry on every call, never the cached dict itself, so a caller
    that mutates its own copy (e.g. `cfg["modes"]["X"] = "act"` to patch
    one entry for a test) can never corrupt the process-wide cache that
    every OTHER caller reads from next. The cost of a deepcopy on a
    config this small is negligible next to the disk read it exists to
    avoid; see _CONFIG_CACHE_INTERVAL_S's own docstring for that
    tradeoff."""
    resolved = path or DEFAULT_SEAMS_CONFIG_PATH
    now = time.monotonic()
    entry = _CONFIG_CACHE.get(resolved)
    if entry is not None and (now - entry["checked_at"]) < _CONFIG_CACHE_INTERVAL_S:
        return copy.deepcopy(entry["data"])

    if entry is None:
        entry = {"mtime": None, "data": {}, "checked_at": 0.0}
        _CONFIG_CACHE[resolved] = entry
    entry["checked_at"] = now

    try:
        mtime = os.stat(resolved).st_mtime
    except OSError:
        entry["mtime"] = None
        entry["data"] = {}
        return copy.deepcopy(entry["data"])

    if mtime == entry["mtime"]:
        return copy.deepcopy(entry["data"])  # unchanged on disk: reuse the parsed dict, no reopen

    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    entry["mtime"] = mtime
    entry["data"] = data if isinstance(data, dict) else {}
    return copy.deepcopy(entry["data"])


def load_registry(path=None):
    """The registry list a harness call site hands to consult(), read
    fresh from `path` (default DEFAULT_REGISTRY_PATH) via
    jev_registry.load(). Raises jev_registry.RegistryError exactly as
    load() does: a call site's own guarded block (rule 2 of the wave-1
    seam brief) is what turns that into a safe current_answer fallback,
    this loader does not swallow it a second time."""
    return _registry.load(path or DEFAULT_REGISTRY_PATH)


#: Where the canary's reset marker is expected when seams_config does not
#: say otherwise: the same path jev_canary.py's DEFAULT_RESET_MARKER
#: contract writes to, resolved from JEV_STATE_DIR (item 1,
#: approved-with-nits review 2026-09-18 -- see THE UNCONFIGURED DEFAULT
#: NO LONGER LIVES INSIDE ANY CHECKOUT AT ALL, above), never from
#: _REPO_ROOT or the process cwd. A canary run is pointed at this path
#: explicitly via its own --reset-marker flag.
DEFAULT_CANARY_MARKER = os.path.join(JEV_STATE_DIR, "canary-reset.json")

#: How close a noul probability may sit to 0.5 and still be treated as an
#: abstain rather than a confident yes/no, for act mode (C3 fix, review
#: 70268c3). A choice answer is judged instead by whether its chosen
#: option NAME contains "unknown" (case-insensitive): see _abstain_reason().
ABSTAIN_LOW = 0.2
ABSTAIN_HIGH = 0.8

#: The A0.6 audit sample rate when seams_config does not say otherwise.
DEFAULT_AUDIT_RATE = 0.05

#: How close a choice question's winning probability must be to the
#: calibrated act threshold before the both-orders disagreement probe
#: (rule 6) runs at all.
NEAR_THRESHOLD_BAND = 0.1

#: A0.8 defaults: see the module docstring's A0.8, THE ENABLEMENT GATE
#: section for what each one gates.
DEFAULT_HARD_DEADLINE_S = 2.0
DEFAULT_MAX_INFLIGHT = 32
DEFAULT_DAILY_CALL_BUDGET = 2000
DEFAULT_BREAKER_FAIL_THRESHOLD = 5
DEFAULT_BREAKER_COOLDOWN_S = 600.0

#: Per-field ceilings for _config_duration_s() below (round 4 fix,
#: 2026-09-19, item 5, "ONE CONFIG CLAMP"): call_deadline_s is a single
#: Jev call's wait, never legitimately more than about a minute;
#: breaker_cooldown_s is a circuit-breaker cool-off and legitimately
#: wants to be minutes (DEFAULT_BREAKER_COOLDOWN_S itself is 600s), so it
#: gets a much larger, still-finite ceiling rather than the same one as a
#: single call's deadline. exit_drain_s keeps its own existing [0, 10]
#: clamp inline in _exit_drain_s() (unchanged, already correct).
MAX_CALL_DEADLINE_S = 60.0
MAX_BREAKER_COOLDOWN_S = 3600.0

#: Where the per-day call budget is persisted by default: under
#: JEV_STATE_DIR, for the identical reason DEFAULT_LEDGER_DIR and
#: DEFAULT_CANARY_MARKER are (a fact about real calls made, never tree
#: content). Overridable per call via seams_config["budget_path"].
DEFAULT_BUDGET_PATH = os.path.join(JEV_STATE_DIR, "jev-budget.json")


@dataclass(frozen=True)
class SeamResult:
    """See the module docstring's THE ONE ENTRY POINT section for what
    each field means."""
    answer: object
    jev: object
    mode: str
    reason: object
    audit: bool
    decision_id: object


def _is_number(value):
    """True for a real int/float, never a bool (bool is an int subclass in
    Python, and a stray True/False must never be read as a probability or
    confidence)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _config_duration_s(seams_config, key, default, max_s, min_s=0.0,
                        negative_is_default=False):
    """ONE CONFIG CLAMP (round 4 fix, 2026-09-19, item 5) for every
    seams_config *_s duration this module reads (call_deadline_s,
    exit_drain_s, breaker_cooldown_s): seams_config[key] when it is a
    real, finite number, clamped to [min_s, max_s]; `default` for
    anything else (missing key, wrong type, NaN, +-inf, a bool -- see
    _is_number()). A caller-supplied config value is untrusted input the
    same way a ledger line is, and must never reach
    time.time()/threading.Event.wait() unclamped: m2 (independent
    re-review, 2026-09-19) probed call_deadline_s=1e10 raising
    OverflowError ("timestamp out of range for platform time_t") on the
    CALLER's own thread, after the worker had already been dispatched,
    permanently leaking the breaker's half-open probe claim -- fixing the
    input here, at the one place every one of these durations is read, is
    simpler and safer than teaching every wait call to tolerate an
    unbounded number. Each caller passes its OWN sane ceiling (a call
    deadline and a breaker cooldown are not the same kind of wait), which
    is why this is one FUNCTION, not one universal number.

    `negative_is_default` (m6 fix, Opus rereview4, 2026-09-19): False (the
    default) keeps the plain clamp above, where a negative value floors to
    min_s -- exit_drain_s relies on exactly this ("a caller that wants NO
    atexit wait at all sets this to 0", _exit_drain_s()'s own docstring),
    so that field's behaviour is unchanged. True makes a negative value
    read the SAME as any other invalid input (missing, wrong type, NaN):
    breaker_cooldown_s passes this, since a negative cooldown clamped to
    0s used to select the LEAST protective breaker (probe immediately
    after opening) rather than falling back to
    DEFAULT_BREAKER_COOLDOWN_S=600s the way every other invalid value for
    this field already does."""
    if isinstance(seams_config, dict):
        v = seams_config.get(key)
        if _is_number(v) and math.isfinite(v):
            if negative_is_default and v < 0:
                return default
            return max(min_s, min(max_s, float(v)))
    return default


def _resolve_mode(seams_config, entry_id):
    """The configured mode for `entry_id`, or OFF for anything that is not
    exactly one of _VALID_MODES: a missing entry, a malformed config, or
    an unrecognised mode string all collapse to the same safe default.
    Never raises."""
    if not isinstance(seams_config, dict):
        return OFF
    modes = seams_config.get("modes")
    if not isinstance(modes, dict):
        return OFF
    mode = modes.get(entry_id, OFF)
    return mode if mode in _VALID_MODES else OFF


def resolve_mode(seams_config, entry_id):
    """Public wrapper around _resolve_mode() (ported from a sibling
    worktree's wave-2 patch, g1-jev_seam-cache.patch): the configured
    mode for `entry_id`, OFF for anything malformed (see _resolve_mode's
    own docstring; never a second, looser copy of that rule). A harness
    call site checks this BEFORE loading the registry (registry.load()
    validates all ~117 entries, real work an off seam, the common case in
    production, should never pay for on every call):
    `if jev_seam.resolve_mode(cfg, entry_id) != jev_seam.OFF: ...`.
    consult() itself never trusts this shortcut; it re-derives the same
    mode from scratch, so a call site that skips this check and calls
    consult() directly is exactly as safe, only slower when off."""
    return _resolve_mode(seams_config, entry_id)


def _canary_marker_path(seams_config):
    """The canary marker path to check, resolved from the repo root when
    relative (M1 fix, review 70268c3): a relative
    canary_reset_marker_path in data/jev-seams.json (e.g.
    "data/jev-canary-reset.json") must resolve the same way regardless of
    the calling process's cwd. An already-absolute configured path is
    used unchanged."""
    if isinstance(seams_config, dict):
        path = seams_config.get("canary_reset_marker_path")
        if isinstance(path, str) and path.strip():
            return path if os.path.isabs(path) else os.path.join(_REPO_ROOT, path)
    return DEFAULT_CANARY_MARKER


class _UnsafePromotionsPath(Exception):
    """Raised by _promotions_path() when even the UNCONFIGURED default
    (jev_cascade.DEFAULT_PROMOTIONS_PATH) resolves under ledger_dir (item
    3, approved-with-nits review 2026-09-18). Every other refusal this
    helper makes has a safe fallback to fall through to (an override under
    ledger_dir falls back to the default); this is the one case with
    nowhere safer left to fall back TO, so it is not a fallback case at
    all -- consult() catches this and escalates the whole call instead of
    ever risking a promotions read from inside a seam's own write path."""


def _resolves_under(path, directory):
    """True when os.path.realpath(path) is `directory` itself or anywhere
    beneath it, comparing REAL paths -- not os.path.abspath -- so a
    symlink cannot alias its way around this check (item 2,
    approved-with-nits review 2026-09-18). os.path.abspath alone
    normalizes `..` and a relative cwd but never resolves a symlink: a
    promotions_path that is itself a symlink whose target sits under
    ledger_dir, or a ledger_dir reached only via a symlinked ancestor
    directory, used to compare as two different strings under abspath
    while being the exact same location on disk under realpath. Safe to
    call on a path that does not exist yet (a promotions file not yet
    written): realpath still resolves every symlinked directory that DOES
    exist along the way and appends the non-existent tail unchanged."""
    candidate = os.path.realpath(path)
    base = os.path.realpath(directory)
    return candidate == base or candidate.startswith(base + os.sep)


def _promotions_path(seams_config, ledger_dir):
    """The promotions store path this call uses (M3 fix, review 70268c3):
    an explicit seams_config["promotions_path"] override when given
    (tests use this key with a temp file), else
    jev_cascade.DEFAULT_PROMOTIONS_PATH -- NEVER a path this module
    builds from a seam's own ledger_dir. A seam's ledger_dir is a path
    the seam itself writes decisions/outcomes to on every consult(); a
    founder-signed promotion record must live somewhere no seam write
    path can reach (jev_cascade's own DEFAULT_PROMOTIONS_PATH docstring
    names this exact defect: this module previously built
    promotions_path=os.path.join(ledger_dir, "promotions.jsonl"), which
    is precisely the directory a seam write path CAN reach).

    MINOR fix, re-review of 1385ab88c: an explicit override is refused
    too, not only the module's own unconfigured default, when it
    RESOLVES (_resolves_under(), item 2: real paths, symlinks included,
    never os.path.abspath alone) to ledger_dir itself or anywhere under
    it -- otherwise a config typo or a copy-pasted ledger_dir path would
    reopen exactly the M3 hole through the override key that exists to
    let a caller point at a REAL promotions store. Falls back to
    jev_cascade.DEFAULT_PROMOTIONS_PATH, the same as if no override had
    been given at all.

    ITEM 3, approved-with-nits review 2026-09-18: the SAME refusal now
    also applies to that fallback default itself. If
    jev_cascade.DEFAULT_PROMOTIONS_PATH resolves under ledger_dir (a
    caller-chosen ledger_dir that happens to contain, or be, the repo's
    own data/ directory), there is no third path left to fall back to --
    raises _UnsafePromotionsPath rather than silently handing back a path
    this helper itself just proved unsafe."""
    if isinstance(seams_config, dict):
        path = seams_config.get("promotions_path")
        if isinstance(path, str) and path.strip():
            if not _resolves_under(path, ledger_dir):
                return path
            # refused: falls through to the safe default below, exactly
            # as jev_cascade.DEFAULT_PROMOTIONS_PATH's own docstring
            # requires -- a promotions store must live somewhere no seam
            # write path (ledger_dir) can reach.
    default_path = _cascade.DEFAULT_PROMOTIONS_PATH
    if _resolves_under(default_path, ledger_dir):
        raise _UnsafePromotionsPath(
            "the unconfigured default promotions store (%s) resolves under "
            "this call's own ledger_dir (%s): refusing rather than reading "
            "or writing a promotion from a seam's own write path"
            % (default_path, ledger_dir))
    return default_path


def _audit_rate(seams_config):
    if isinstance(seams_config, dict):
        rate = seams_config.get("audit_rate")
        if _is_number(rate) and 0.0 <= rate <= 1.0:
            return rate
    return DEFAULT_AUDIT_RATE


def _decision_id(entry_id, state):
    """A decision_id that is UNIQUE PER CALL, never reused for a repeated
    identical question (MAJOR fix, orchestrator decision, re-review of
    1385ab88c: see the module docstring's rule 4). Shape
    "<entry_id>:<content hash>:<16 random hex>": the content hash (over
    `state`, the same material the old stable id hashed, falling back to
    repr() for a state that is not JSON-serializable -- never raises, an
    id is still owed even for state jev_decide.decide() will itself go on
    to reject as unserializable) is kept only so a human reading the
    ledger by eye can see which questions were asked; the 16 hex chars of
    real randomness after it is what actually makes the id unique,
    including across separate PROCESSES (this module keeps no counter or
    other shared state to synchronize for that). The A0.6 audit sample
    stays deterministic PER decision_id exactly as before
    (_hash_unit_interval is a pure function of whatever id it is given);
    it is the id itself that is no longer a pure function of
    (entry_id, state), so two consult() calls for the exact same question
    now produce two distinct ids and two ledger rows, never one
    deduplicated id."""
    try:
        material = json.dumps(state, sort_keys=True, default=str)
    except TypeError:
        material = repr(state)
    content_digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    unique = uuid.uuid4().hex[:16]
    return "%s:%s:%s" % (entry_id, content_digest, unique)


def _hash_unit_interval(text):
    """A deterministic float in [0, 1) derived from `text`, used as the
    A0.6 audit sample's default source of "randomness" -- see the module
    docstring's AUDIT SAMPLING section for why this is a hash, not real
    randomness."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 1_000_000) / 1_000_000


def _confidence_for_routing(qtype, record):
    """The one number this module trusts as `record`'s confidence for
    calibration lookups and cascade routing: the noul probability itself
    for a noul question (jev_cascade's own rule: a noul's confidence IS
    its probability, TypeSafe reports no separate number), else the
    bridge's own reported confidence, else the answer's probability as a
    last resort, else None when nothing usable was reported at all."""
    if qtype == "noul":
        prob = record.get("probability")
        return prob if _is_number(prob) else None
    conf = record.get("confidence")
    if _is_number(conf):
        return conf
    prob = record.get("probability")
    return prob if _is_number(prob) else None


def _abstain_reason(qtype, record):
    """A named reason string when `record`'s own answer is an abstain
    signal act mode must never trust, else None (C3 fix, review 70268c3).

    A choice OR score answer whose CHOSEN option NAME contains "unknown"
    (case-insensitive) is the registry's own required abstain option --
    jev_registry lints every choice AND score entry for exactly one
    option named that way (CRITICAL fix, re-review of 1385ab88c: a score
    question answering "unknown" was missed here, so a calibrated,
    promoted score entry could still ACT on it), so a chosen "unknown" is
    always a legally declared answer, never caught by jev_decide's own
    declared-options check. A noul probability inside
    [ABSTAIN_LOW, ABSTAIN_HIGH] is close enough to "I don't know" that
    routing it as a confident yes/no is not supported by the number
    itself.

    Named separately from a plain missing-confidence NO_DATA so a caller
    can tell "Jev said it doesn't know" apart from "there is no
    calibration evidence yet"."""
    if qtype in ("choice", "score"):
        answer = record.get("answer")
        if isinstance(answer, str) and "unknown" in answer.lower():
            return "abstain: chosen option %r names the abstain option" % (answer,)
        return None
    if qtype == "noul":
        prob = record.get("probability")
        if _is_number(prob) and ABSTAIN_LOW <= prob <= ABSTAIN_HIGH:
            return ("abstain: noul probability %.3f is inside the abstain "
                     "band [%.1f, %.1f]" % (prob, ABSTAIN_LOW, ABSTAIN_HIGH))
        return None
    return None


def _combine_reasons(*parts):
    """Joins every non-empty reason string in `parts`, in order, with
    "; ", or returns None when none of them are set. M2 fix (independent
    review, 2026-09-19): a local ledger-write failure and an act-mode
    routing reason (or an abstain, or a cascade exception) are two
    genuinely different facts about one call -- this reports both rather
    than silently dropping one in favour of the other."""
    joined = "; ".join(p for p in parts if p)
    return joined or None


def _ledger_decision(decision_id, family, qtype, record, audit=False):
    """The jev_calibration.append_decision()-shaped dict for `record`, or
    None when `record` carries no usable numeric probability/confidence
    for its qtype (a score question, per jev_decide's own docstring, or a
    malformed bridge answer): see the module docstring's CONTINGENCY
    table. Never fabricates a probability or confidence that was not
    actually reported.

    M3 fix (independent review, 2026-09-19): the ledger row now carries
    the A0.6 `audit` sample flag (jev_calibration's own record validator
    only checks its OWN required fields and never rejects an extra one,
    so this is a pure addition). Before this fix the audit decision was
    computed and then thrown away -- unreachable by anything reading the
    ledger later, which defeats the entire point of the audit sample: "so
    a human labels it anyway" only works if the label-worthy rows are
    findable in the ledger itself, not only in a SeamResult nobody kept."""
    prob = record.get("probability")
    confidence = record.get("confidence")
    prob_ok = _is_number(prob)
    conf_ok = _is_number(confidence)

    if qtype == "noul":
        if not prob_ok:
            return None
        confidence = prob
        conf_ok = True
    else:
        if not conf_ok and prob_ok:
            confidence = prob
            conf_ok = True
        if not prob_ok and conf_ok:
            prob = confidence
            prob_ok = True
        if not (prob_ok and conf_ok):
            return None

    if not (0.0 <= prob <= 1.0 and 0.0 <= confidence <= 1.0):
        return None

    cost = record.get("cost_share")
    if not _is_number(cost):
        cost = 0.0

    model = record.get("model")
    if not isinstance(model, str) or not model.strip():
        model = "unknown"

    return {
        "id": decision_id,
        "family": family,
        "qtype": qtype,
        "framing": record.get("framing_hash") or "unknown",
        "answer": record.get("answer"),
        "prob": float(prob),
        "confidence": float(confidence),
        "model": model,
        "cost": float(cost),
        "at": datetime.now(timezone.utc).isoformat(),
        "audit": bool(audit),
    }


def _calibrated_threshold(decisions_path, outcomes_path, family, qtype, risk_class):
    """(threshold_value or None) for (family, qtype, risk_class), reusing
    jev_cascade's own RISK_TARGET_PRECISION and MIN_SAMPLES so this module
    never carries a second copy of that policy. None for "critical" (never
    acts, so there is no threshold to speak of), an unrecognised
    risk_class, or a risk_class with no calibration evidence yet."""
    target_precision = _cascade.RISK_TARGET_PRECISION.get(risk_class)
    if target_precision is None:
        return None
    result = _calibration.threshold(
        decisions_path, outcomes_path, family, qtype, target_precision,
        _cascade.MIN_SAMPLES,
    )
    return result["threshold"]


def _audit_flag(entry_id, qtype, risk_class, confidence, decisions_path, outcomes_path,
                 decision_id, seams_config, rng):
    """True when this decision is picked by the A0.6 audit sample: see the
    module docstring's AUDIT SAMPLING section. False whenever confidence
    is unknown, or the calibrated threshold cannot be computed (critical
    risk, an unrecognised risk_class, or no evidence yet), or the
    confidence does not clear that threshold (this decision would be
    escalated anyway, so it is not the case this sample exists to fix)."""
    if confidence is None:
        return False
    # ponytail: recomputes the calibrated threshold here even when the
    # near-threshold probe (rule 6) already computed the same value for a
    # choice question a few lines up in consult(); a second small ledger
    # read per call is cheap at this call volume. Share the value if this
    # ever profiles hot.
    threshold_value = _calibrated_threshold(
        decisions_path, outcomes_path, entry_id, qtype, risk_class)
    if threshold_value is None or confidence < threshold_value:
        return False
    sample = rng() if rng is not None else _hash_unit_interval(decision_id)
    return sample < _audit_rate(seams_config)


# ---------------------------------------------------------------------------
# A0.8: admission gates (breaker, budget) and the bounded background worker.
# See the module docstring's A0.8, THE ENABLEMENT GATE section.
# ---------------------------------------------------------------------------

def _write_json_atomic_safe(path, payload):
    """None on success, an error string otherwise; never raises. Mirrors
    jev_canary.py's own _write_json_atomic (temp file in the same
    directory, then os.replace): duplicated, not imported (this module
    and jev_canary deliberately never import each other -- see
    JEV_STATE_DIR's own docstring above for why).

    m3 fix (independent re-review, 2026-09-19): also catches ValueError,
    not only OSError. A `path` with an embedded NUL byte (probed:
    seams_config["budget_path"]="\\x00", a caller/config problem, not a
    filesystem one) makes os.makedirs()/tempfile.mkstemp() raise
    ValueError ("embedded null byte"), which used to escape this
    function, then _consume_budget_slot(), then consult() itself
    uncaught -- while os.path.exists() (the read side, _read_budget_state
    below) already tolerates the identical input by returning False, so
    only the write side needed this fix."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".jev_seam-", suffix=".tmp", dir=directory)
    except (OSError, ValueError) as exc:
        return "cannot create a temp file next to %s: %s" % (path, exc)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
        os.replace(tmp_path, path)
    except (OSError, ValueError) as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return "cannot write %s: %s" % (path, exc)
    return None


def _hard_deadline(seams_config):
    """m3 fix (independent re-review, 2026-09-19): also requires
    math.isfinite(v). threading.Event.wait() raises OverflowError on an
    infinite timeout (probed: seams_config["call_deadline_s"]=float("inf")
    -- json.loads happily accepts that literal -- crashed consult() on
    the caller's own thread, AFTER the worker had already been
    dispatched, leaking the breaker's half-open probe claim forever since
    nothing was left to classify that call's outcome). Fixing the
    INPUT here, at the source, is simpler and safer than teaching
    box.event.wait() to tolerate infinity: a caller-supplied deadline was
    never meant to mean "wait forever" in the first place.

    m2 fix (round 4, independent re-review, 2026-09-19): isfinite alone
    was not enough -- a large but FINITE value (probed:
    call_deadline_s=1e10, well under Python's float range but past
    threading.TIMEOUT_MAX, ~106,751 days) still raised the same
    OverflowError, just later, after dispatch. Routed through
    _config_duration_s() (item 5, ONE CONFIG CLAMP) with a 60s ceiling: a
    caller-supplied deadline is clamped, not replaced by the default,
    since a large-but-meant value should still mean "wait a while", just
    not unboundedly."""
    v = _config_duration_s(seams_config, "call_deadline_s", None, max_s=MAX_CALL_DEADLINE_S)
    if v is not None and v > 0:
        return v
    return DEFAULT_HARD_DEADLINE_S


def _max_inflight(seams_config):
    if isinstance(seams_config, dict):
        v = seams_config.get("max_inflight_calls")
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            return v
    return DEFAULT_MAX_INFLIGHT


#: Bounds how many _run_seam_job() background threads may be running at
#: once (item 1: "a full queue drops the call with a counted reason,
#: never blocks"). A plain counter+lock, not threading.BoundedSemaphore:
#: the cap is read fresh from seams_config on every call (see
#: _max_inflight()), not fixed once at object construction, and a
#: semaphore's capacity cannot be changed after it is built.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_COUNT = 0

#: A plain counter of every call dropped for queue-full, keyed by
#: entry_id, so an operator (or a test) can see the drop happened even
#: though the caller only gets a reason string back. Never read by
#: consult() itself for any decision; purely observational.
DROP_COUNTS = {}

#: M4 fix (independent review, 2026-09-19): every _run_seam_job() thread
#: consult() actually starts is tracked here so drain() (and the atexit
#: hook below) can wait for it. Separate lock from _INFLIGHT_LOCK on
#: purpose: this list is walked and pruned by drain(), a much less
#: frequent operation than the per-call inflight increment/decrement, and
#: giving it its own lock keeps the hot per-call path from ever blocking
#: on a drain that happens to be running concurrently.
_ACTIVE_WORKERS_LOCK = threading.Lock()
_ACTIVE_WORKERS = []

#: How long drain() (and the atexit hook) wait by default for in-flight
#: workers to finish before giving up. A SMALL, bounded time on purpose
#: (review: "a small configured time"): draining exists to give a
#: short-lived process's already-dispatched calls a real chance to land,
#: never to turn process exit itself into an unbounded wait on a hung
#: Jev call.
DEFAULT_DRAIN_TIMEOUT_S = 3.0

#: N2 fix (independent re-review, 2026-09-19): the default wait
#: _atexit_drain() (not drain() itself -- see its own docstring) uses,
#: deliberately smaller than DEFAULT_DRAIN_TIMEOUT_S above: an atexit
#: hook fires on every process exit whether or not anything is in
#: flight, so its default favours a fast exit over a guaranteed write.
#: Config key "exit_drain_s"; see _exit_drain_s() for the clamp.
DEFAULT_EXIT_DRAIN_S = 1.0

#: DROP_COUNTS key for shadow rows accepted as lost because the atexit
#: drain's own bounded wait ran out before their worker finished (N2):
#: distinct from a per-entry_id key so it is never confused with an
#: ordinary queue-full drop (_record_drop above), and so it survives
#: even though, by definition, no entry_id-scoped caller is left running
#: to read it -- an operator inspects DROP_COUNTS itself after the fact.
_EXIT_DRAIN_DROP_KEY = "__exit_drain__"


def _register_worker(thread):
    with _ACTIVE_WORKERS_LOCK:
        _ACTIVE_WORKERS[:] = [t for t in _ACTIVE_WORKERS if t.is_alive()]  # opportunistic prune
        _ACTIVE_WORKERS.append(thread)


def drain(timeout=None):
    """Waits up to `timeout` seconds (default DEFAULT_DRAIN_TIMEOUT_S) for
    every currently in-flight _run_seam_job() worker thread to finish, so
    it gets a real chance to write its ledger row before the caller (a
    short-lived script, a command-line "moment" such as a brothersbe
    kickoff run) moves on or the process exits.

    M4 fix (independent review, 2026-09-19). THE PROBE THIS ANSWERS: a
    shadow call against a runner that took 0.3s, immediately followed by
    process exit -- the budget file read {"calls": 1} (the call was
    admitted and dispatched) but the ledger folder was empty (the worker
    never got to finish writing its row). A real bridge subprocess can
    outlive its own parent process, so budget spent with nothing ever
    recorded is a real, not hypothetical, cost. Call sites that know they
    are about to exit (or that just want their own shadow/advise calls to
    have landed before doing something that depends on the ledger) call
    this directly; the atexit hook below calls it automatically, once,
    for every process that imports this module, so a caller that forgets
    to is still covered up to DEFAULT_DRAIN_TIMEOUT_S.

    Returns the number of worker threads still alive when the timeout was
    reached (0 means every in-flight call finished in time). Never
    raises: Thread.join() itself does not raise for an ordinary timeout,
    and this function does no other I/O of its own."""
    timeout = DEFAULT_DRAIN_TIMEOUT_S if timeout is None else timeout
    with _ACTIVE_WORKERS_LOCK:
        threads = list(_ACTIVE_WORKERS)
    deadline = time.monotonic() + timeout
    for t in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(remaining)
    with _ACTIVE_WORKERS_LOCK:
        _ACTIVE_WORKERS[:] = [t for t in _ACTIVE_WORKERS if t.is_alive()]
        return len(_ACTIVE_WORKERS)


def max_inflight_calls(seams_config):
    """Public wrapper around _max_inflight() (M1(b), Opus rereview4,
    2026-09-19) for a caller outside this module that needs to submit its
    OWN calls in batches no larger than the same cap consult() itself
    enforces, without reaching into a private name across the module
    boundary (jev_checks.check_gate_log_lines's own chunking)."""
    return _max_inflight(seams_config)


#: The fixed pad cli_drain_timeout_s() below adds on top of its batched
#: per-call estimate (M1(a), Opus rereview4, 2026-09-19): the last wave's
#: workers still need a little room to finish decide()'s own bridge call
#: and write their ledger row after _hard_deadline() itself would have
#: given up waiting on a synchronous caller, since _hard_deadline() times
#: only the wait an advise/act caller does for ONE worker, not the write
#: that follows it.
DEFAULT_CLI_DRAIN_MARGIN_S = 2.0


def cli_drain_timeout_s(seams_config, calls_started):
    """The bound a short-lived CLI (jev_checks.py main(), M1(a)) should
    hand drain() before it returns, sized to the work rather than to the
    atexit hook's own DEFAULT_EXIT_DRAIN_S=1.0s bound -- that default
    exists to keep an ORDINARY process exit fast for a process that
    happens to import this module, never to cover a CLI whose entire
    purpose is to wait for the calls it just dispatched.

    `calls_started` is the number of consult() calls the caller issued:
    an upper bound on the number of background workers actually running
    (some of those calls may have been OFF-shaped, or dropped for
    queue-full before a worker thread even started, which only makes this
    bound generous, never short -- drain() itself returns as soon as
    every live thread finishes, so an oversized bound costs nothing when
    nothing is actually still running).

    Batches calls_started into ceil(calls_started / max_inflight_calls)
    waves (the same in-flight cap consult() enforces on this same
    seams_config), each wave allowed up to _hard_deadline(seams_config)
    -- the same per-call deadline advise/act already wait on a single
    worker -- plus DEFAULT_CLI_DRAIN_MARGIN_S so the last wave's workers
    have room to finish writing their ledger row after decide() itself
    returns. 0 or fewer calls_started returns 0.0 (nothing to wait for)."""
    if calls_started <= 0:
        return 0.0
    cap = _max_inflight(seams_config)
    waves = (calls_started + cap - 1) // cap
    return _hard_deadline(seams_config) * waves + DEFAULT_CLI_DRAIN_MARGIN_S


def _exit_drain_s(seams_config, in_flight=0):
    """The bounded wait _atexit_drain() passes to drain(): N2 fix
    (independent re-review, 2026-09-19), resized by item 3 (A0.8 round 6).

    An explicit seams_config["exit_drain_s"] (a real, finite number, see
    _is_number()) always wins, in-flight or not: clamped to [0, 10], same
    as before (a negative config value floors to 0 -- a caller that wants
    NO atexit wait at all sets this to 0 rather than needing a magic
    sentinel; a config value above 10 ceilings to 10). Routed through
    _config_duration_s() (round 4, item 5, ONE CONFIG CLAMP): same clamp,
    same shape, as _hard_deadline() and _breaker_cooldown_s().

    With no override, `in_flight` (the count of _ACTIVE_WORKERS still
    alive right before the wait, see _atexit_drain()) decides the shape:

      in_flight == 0   DEFAULT_EXIT_DRAIN_S (1.0s), exactly as before --
                        the ordinary case for a process that merely
                        imports this module with nothing dispatched.
      in_flight > 0     _hard_deadline(seams_config) + DEFAULT_CLI_DRAIN_MARGIN_S
                        (item 3, A0.8 round 6, m1/G1): a call actually in
                        flight gets the same per-call bound advise/act
                        already wait on one worker, plus room to write
                        its ledger row after that -- the flat 1.0s
                        default silently lost any real call slower than
                        that, which every one of the eight G1 short-lived
                        callers (board_status, doc_assurance,
                        export_public, intake_score x2,
                        mobile_hybrid_action_router x2, receipt_door) hit
                        since none of them call drain() themselves.
                        Still bounded: _hard_deadline() is itself already
                        clamped to at most MAX_CALL_DEADLINE_S (60s), so
                        this can never exceed 60s + DEFAULT_CLI_DRAIN_MARGIN_S
                        without a second, separate ceiling."""
    explicit = _config_duration_s(seams_config, "exit_drain_s", None, max_s=10.0)
    if explicit is not None:
        return explicit
    if in_flight > 0:
        return _hard_deadline(seams_config) + DEFAULT_CLI_DRAIN_MARGIN_S
    return DEFAULT_EXIT_DRAIN_S


def _atexit_drain():
    """The function atexit actually registers (N2 fix, independent
    re-review, 2026-09-19) -- never drain itself, so this call always
    uses the CURRENT seams_config's exit_drain_s (see _exit_drain_s()),
    not drain()'s own larger DEFAULT_DRAIN_TIMEOUT_S meant for a call
    site that drains itself deliberately. A shadow row still in flight
    when this bounded wait runs out is an ACCEPTED loss (see the module
    docstring's THE ATEXIT DRAIN HAS ITS OWN BOUNDED, CONFIGURABLE WAIT
    section): counted here, once, under _EXIT_DRAIN_DROP_KEY in
    DROP_COUNTS, and reported once on stderr (item 3, A0.8 round 6),
    rather than left silently unaccounted for the way a caller reading
    only stdout or the CLI's own exit code would never see.

    M1(c) fix (Opus rereview4, 2026-09-19): a worker thread still alive
    when this bounded wait gives up is abandoned at process exit, but the
    real bridge subprocess it started (jev_decide._default_runner's own
    Popen) does not die with it -- reparented to init, it keeps running
    and spending budget with no process left to read its answer (probed:
    a 3s bridge outlived the process by 10+ s). still_alive>0 therefore
    also asks jev_decide to kill every bridge subprocess it still has a
    handle on, best-effort, so none of them are left orphaned.

    Item 3 (A0.8 round 6, m1 and the G1 scope): the wait itself is now
    sized to whatever is actually in flight (see _exit_drain_s()), so a
    call from one of the eight G1 short-lived callers gets a real chance
    to land its ledger row without any of those eight call sites calling
    drain() themselves -- fixed once, here, at the one place every
    process that imports this module already routes through at exit.
    Each worker thread is named for its own entry_id (see consult()),
    so a row abandoned here can be reported by which entry it belonged
    to, not just a bare count."""
    with _ACTIVE_WORKERS_LOCK:
        in_flight = sum(1 for t in _ACTIVE_WORKERS if t.is_alive())
    still_alive = drain(timeout=_exit_drain_s(load_seams_config(), in_flight=in_flight))
    if still_alive:
        with _ACTIVE_WORKERS_LOCK:
            abandoned_ids = sorted({t.name for t in _ACTIVE_WORKERS})
        _record_drop(_EXIT_DRAIN_DROP_KEY, n=still_alive)
        print(
            "jev_seam: atexit drain abandoned %d shadow call(s), entry id(s): %s"
            % (still_alive, ", ".join(abandoned_ids) if abandoned_ids else "unknown"),
            file=sys.stderr,
        )
        _decide.kill_active_processes()


atexit.register(_atexit_drain)


def _try_acquire_inflight(seams_config):
    """(allowed, current_count_after). Reads/writes _INFLIGHT_COUNT under
    the lock and hands back the post-decision count in the SAME critical
    section, so a caller building a message from it (see consult()'s drop
    reason) never re-reads the global unguarded (review minor,
    2026-09-19)."""
    global _INFLIGHT_COUNT
    limit = _max_inflight(seams_config)
    with _INFLIGHT_LOCK:
        if _INFLIGHT_COUNT >= limit:
            return False, _INFLIGHT_COUNT
        _INFLIGHT_COUNT += 1
        return True, _INFLIGHT_COUNT


def _release_inflight():
    global _INFLIGHT_COUNT
    with _INFLIGHT_LOCK:
        _INFLIGHT_COUNT = max(0, _INFLIGHT_COUNT - 1)


#: Guards DROP_COUNTS (review minor, 2026-09-19: it was read and written
#: without one).
_DROP_COUNTS_LOCK = threading.Lock()


def _record_drop(entry_id, n=1):
    with _DROP_COUNTS_LOCK:
        DROP_COUNTS[entry_id] = DROP_COUNTS.get(entry_id, 0) + n


#: In-memory, process-local breaker state (module-global on purpose: the
#: breaker protects the one shared Jev call surface, not a per-entry
#: resource -- see the module docstring). Never persisted: a restarted
#: process starts closed, unlike the budget below. `probing`, review
#: minor 2026-09-19: True while a single half-open retry is in flight, so
#: a cool-off that has just elapsed admits exactly ONE probe call at a
#: time, not every concurrent caller at once (the docstring's own claim,
#: which was not actually enforced before this fix).
_BREAKER_LOCK = threading.Lock()
_BREAKER_STATE = {"consecutive_failures": 0, "opened_at": None, "probing": False}


def _breaker_fail_threshold(seams_config):
    if isinstance(seams_config, dict):
        v = seams_config.get("breaker_fail_threshold")
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            return v
    return DEFAULT_BREAKER_FAIL_THRESHOLD


def _breaker_cooldown_s(seams_config):
    """Round 4 fix (item 5, ONE CONFIG CLAMP): this used to accept any
    non-negative number with no isfinite check and no ceiling at all
    (float("inf") passed through, the breaker would then never reopen)
    -- now routed through _config_duration_s(), same as _hard_deadline()
    and _exit_drain_s(), with its own MAX_BREAKER_COOLDOWN_S ceiling
    (1 hour) rather than the much smaller call-deadline one: a breaker
    cooldown legitimately wants to be minutes (the module default itself
    is 600s), so "one clamp" means one FUNCTION shared by every duration,
    not one number shared by durations that mean different things.

    m6 fix (Opus rereview4, 2026-09-19): negative_is_default=True -- a
    negative breaker_cooldown_s now falls back to
    DEFAULT_BREAKER_COOLDOWN_S=600s exactly like a NaN or a string does,
    rather than clamping to 0s and reopening the breaker for an immediate
    probe (probed: breaker_cooldown_s=-1 -> 0.0 before this fix,
    -1 -> 600.0 after)."""
    return _config_duration_s(seams_config, "breaker_cooldown_s", DEFAULT_BREAKER_COOLDOWN_S,
                               max_s=MAX_BREAKER_COOLDOWN_S, negative_is_default=True)


def _breaker_admission(seams_config, clock):
    """(deny_reason, claimed_probe). deny_reason is None when the call is
    admitted; claimed_probe is True only when THIS call is the one that
    just claimed the single half-open probe slot (cool-off elapsed,
    nobody else probing yet) -- a caller that is admitted this way but
    then goes on to be refused for an UNRELATED reason (registry, budget,
    inflight, canary; see consult()) must call _breaker_release_probe()
    so the slot is not left claimed forever by a call that never actually
    ran. A call denied outright (breaker fully open, or already
    probing) never claims anything."""
    with _BREAKER_LOCK:
        opened_at = _BREAKER_STATE["opened_at"]
        if opened_at is None:
            return None, False
        cooldown = _breaker_cooldown_s(seams_config)
        elapsed = clock() - opened_at
        if elapsed < cooldown:
            return ("breaker open: %d consecutive Jev failures/timeouts, cooling off "
                     "for %.1fs more" % (_BREAKER_STATE["consecutive_failures"], cooldown - elapsed)), False
        if _BREAKER_STATE["probing"]:
            return "breaker half-open: a probe call is already in flight, cooling off", False
        _BREAKER_STATE["probing"] = True
        return None, True


def _breaker_release_probe():
    """Releases this call's claim on the half-open probe slot WITHOUT
    touching the failure streak or opened_at: used when a call that
    claimed the probe never actually reached the worker (refused
    afterward for an unrelated reason), so a FUTURE call still gets a
    chance to probe rather than being locked out by a probe that was
    claimed but never run."""
    with _BREAKER_LOCK:
        _BREAKER_STATE["probing"] = False


def _breaker_record_failure(seams_config, clock):
    with _BREAKER_LOCK:
        _BREAKER_STATE["probing"] = False
        _BREAKER_STATE["consecutive_failures"] += 1
        if _BREAKER_STATE["consecutive_failures"] >= _breaker_fail_threshold(seams_config):
            _BREAKER_STATE["opened_at"] = clock()


def _breaker_record_success():
    with _BREAKER_LOCK:
        _BREAKER_STATE["probing"] = False
        _BREAKER_STATE["consecutive_failures"] = 0
        _BREAKER_STATE["opened_at"] = None


def _budget_path(seams_config):
    """Round 4 fix (item 6, 2026-09-19): a NUL byte in
    seams_config["budget_path"] is refused HERE, at the config read site,
    rather than left to raise several calls downstream. Probed cause:
    os.path.exists(path) (the FIRST thing _read_budget_state() does, with
    no try/except of its own around that one call) raises ValueError
    ("embedded null byte") before that function's own OSError/ValueError
    handling around open()/json.load() is ever reached -- the broad
    except in consult()'s admission try/finally (item 4) already keeps
    this from crashing the caller, but relying on a catch-all downstream
    for an input this function itself can already see is invalid is a
    symptom fix, not the source one: a NUL byte here is exactly as
    invalid as an empty or all-whitespace path already was, so it falls
    back to DEFAULT_BUDGET_PATH the same way."""
    if isinstance(seams_config, dict):
        p = seams_config.get("budget_path")
        if isinstance(p, str) and p.strip() and "\x00" not in p:
            return p
    return DEFAULT_BUDGET_PATH


def _daily_budget(seams_config):
    if isinstance(seams_config, dict):
        v = seams_config.get("daily_call_budget")
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return DEFAULT_DAILY_CALL_BUDGET


#: Guards the budget file's read-modify-write within THIS process; see the
#: module docstring's ponytail note on the cross-process overshoot this
#: does not close.
_BUDGET_LOCK = threading.Lock()


def _read_budget_state(path):
    """("missing", None) when `path` does not exist yet -- the ordinary
    first-call-of-the-day case, read as zero calls so far. ("ok", data)
    when it exists and parses as a JSON object. ("corrupt", reason) for
    anything else: present but unreadable, not valid JSON, or not a JSON
    object. C1 fix (independent review, 2026-09-19): "missing" and
    "corrupt" were previously conflated (both read as zero calls used),
    which let a broken budget file admit every call it was supposed to
    deny -- probed at 10 of 10 admitted against a limit of 2. Never
    raises."""
    if not os.path.exists(path):
        return "missing", None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return "corrupt", "cannot read budget file %s: %s" % (path, exc)
    if not isinstance(data, dict):
        return "corrupt", "budget file %s does not contain a JSON object" % path
    return "ok", data


def _parse_iso_calendar_date(value):
    """A real datetime.date for a strict, zero-padded "YYYY-MM-DD" string,
    or None for anything else: not a string, the wrong shape (a slash-
    separated date, a bare number, a non-padded month/day), or a string
    that looks the right shape but names no real calendar day (e.g.
    "2026-13-40"). m2 fix (independent re-review, 2026-09-19): the
    budget's date field was previously only checked for "non-empty
    string" -- a non-ISO date that happened to sort before today (e.g.
    "19/09/2026") silently read as a fresh day and reset the count,
    exactly the bypass this field exists to prevent; see
    _consume_budget_slot()'s own docstring for the rest of this fix."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _consume_budget_slot(seams_config):
    """(allowed, deny_reason). allowed=True (and persisted) when a slot
    was available and the write succeeded. allowed=False otherwise, with
    deny_reason naming exactly one of four distinct causes:
      - today's count already meets the configured limit (ordinary
        exhaustion);
      - the budget file exists but cannot be trusted (unreadable, not
        JSON, not an object, or its `calls` field is not a usable
        non-negative int) -- C1 fix, independent review 2026-09-19: FAILS
        CLOSED rather than silently reading a broken file as zero calls
        used;
      - the file's `date` field is missing, not a string, or not a real,
        strictly ISO "YYYY-MM-DD" calendar date -- m2 fix, independent
        re-review, 2026-09-19: malformed is corrupt is refuse, same
        direction as C1 above (see _parse_iso_calendar_date());
      - the file's date is LATER than today -- m2 fix (independent
        re-review, 2026-09-19, replacing the review-minor "later of the
        two dates wins" rule below): refused outright, never silently
        admitted against that future date. WHY THIS STILL KEEPS THE
        ORIGINAL "clock set backwards must not reset the count" PROMISE:
        that promise only ever needed calls to STOP counting as a fresh
        day, never to keep being silently ADMITTED against a date that
        has not arrived yet -- probed: a file dated 2027-03-01 (a forward
        clock excursion, or plain corruption) used to keep silently
        admitting/denying calls against that future date FOREVER (or
        until the real clock caught up), with no clue to an operator
        beyond a repeating "budget exhausted" once the limit under that
        date was reached. Refusing here is STRICTER than the old
        behaviour in every case (denial is a subset of "never exceeds the
        daily limit"), so it satisfies the anti-reset goal at least as
        well while also naming the real cause and how to fix it, rather
        than silently locking until a date that may be months away;
      - persisting the spend failed (disk full, permission race) -- also
        fails closed, since an admitted-but-unrecorded call would let the
        budget be silently bypassed one call at a time.
    "Today" is the real UTC calendar date."""
    path = _budget_path(seams_config)
    limit = _daily_budget(seams_config)
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    with _BUDGET_LOCK:
        status, payload = _read_budget_state(path)
        if status == "corrupt":
            return False, "budget file unreadable, refusing rather than admitting: %s" % payload

        effective_date = today_iso
        used = 0
        if status == "ok":
            stored_date_raw = payload.get("date")
            stored_date = _parse_iso_calendar_date(stored_date_raw)
            if stored_date is None:
                return False, (
                    "budget file %s has an invalid or non-ISO date field (%r), refusing "
                    "rather than admitting -- delete %s to reset if this is unexpected"
                    % (path, stored_date_raw, path))
            if stored_date > today:
                return False, (
                    "budget file %s is dated %s, which is later than today, UTC (%s): "
                    "refusing rather than silently admitting calls against a date that has "
                    "not arrived yet (a backward clock glitch, or the file itself is from "
                    "the future) -- delete %s to reset if this is unexpected"
                    % (path, stored_date_raw, today_iso, path))
            if stored_date.isoformat() == today_iso:
                candidate = payload.get("calls")
                if not (isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0):
                    return False, ("budget file %s has a non-numeric calls field, "
                                    "refusing rather than admitting" % path)
                used = candidate
            # else: stored_date < today, a genuine new day -- used stays 0.

        if used >= limit:
            return False, "daily Jev call budget exhausted (%d/day)" % limit

        write_err = _write_json_atomic_safe(path, {"date": effective_date, "calls": used + 1})
        if write_err:
            return False, ("could not persist the budget spend, refusing rather than "
                             "admitting an untracked call: %s" % write_err)
        return True, None


class _JobBox(object):
    """The handoff between consult()'s caller-thread and its background
    worker thread (_run_seam_job()): one Event the worker sets exactly
    once, when done, and the fields the worker fills in before setting it.
    `api_failed` is read by the caller thread for breaker classification
    (see the module docstring); `result` is the SeamResult to return when
    the caller waited long enough to see it."""
    __slots__ = ("event", "result", "api_failed")

    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.api_failed = False


def _run_seam_job(box, *, entry_id, state, question, qtype, decision_id, risk_class,
                   mode_cfg, current_answer, decisions_path, outcomes_path,
                   promotions_path, seams_config, runner, rng, clock, record_breaker_here):
    """Runs on its own daemon thread, spawned by consult(): the actual
    reach to Jev (decide(), the near-threshold reorder probe), the ledger
    write, and (for act) jev_cascade.route(). Ported near-verbatim from
    consult()'s own former synchronous body; the only real change from
    that body is that `mode_cfg` replaces the old `effective_mode` (the
    canary can no longer force shadow -- see the module docstring), so
    every branch below uses the caller's configured mode directly.
    Sets box.result and box.api_failed, then box.event, in a finally block
    so a WAITING caller (act mode) is never left waiting past its own
    deadline even if something here raises unexpectedly; releases its
    inflight slot in the same finally block.

    `record_breaker_here`, orchestrator fix 2026-09-19: True for shadow
    ONLY (m5 doc-drift fix, independent re-review, 2026-09-19: this
    docstring previously said "shadow and advise", stale since the M3 fix
    made advise wait -- see _fires_and_forgets()'s own docstring, the one
    place this decision is actually made). Shadow never waits for this
    worker at all (see consult()'s own docstring) and so has no caller
    thread left to classify this call's success or failure for the
    breaker -- this worker does it itself, whenever it finishes, however
    long that takes. False for advise and act, which both still wait
    (bounded by the deadline) and classify the outcome themselves in the
    caller thread; the worker must NOT also record there, or a single
    call could count twice toward the consecutive-failure streak."""
    try:
        result = _decide.decide(state, {decision_id: question}, entry_id, runner=runner)
        if isinstance(result, tuple):
            _status, why = result
            box.api_failed = True
            box.result = SeamResult(current_answer, None, mode_cfg,
                                     "NO-DATA: %s" % why, False, None)
            return
        record = result[0]

        if qtype == "choice":
            threshold_value = _calibrated_threshold(
                decisions_path, outcomes_path, entry_id, qtype, risk_class)
            routing_number = _confidence_for_routing(qtype, record)
            if (threshold_value is not None and routing_number is not None
                    and abs(routing_number - threshold_value) <= NEAR_THRESHOLD_BAND):
                both = _registry.both_orders(question)
                if len(both) == 2:
                    reorder_id = decision_id + ":reordered"
                    result2 = _decide.decide(state, {reorder_id: both[1]}, entry_id, runner=runner)
                    if isinstance(result2, tuple):
                        _status2, why2 = result2
                        box.api_failed = True
                        box.result = SeamResult(
                            current_answer, None, mode_cfg,
                            "NO-DATA: reordered probe failed: %s" % why2, False, None)
                        return
                    record2 = result2[0]
                    if record2["answer"] != record["answer"]:
                        box.result = SeamResult(
                            current_answer, None, mode_cfg,
                            "NO-DATA: option-order disagreement near threshold (%r vs %r)"
                            % (record["answer"], record2["answer"]),
                            False, None)
                        return

        # M3 fix (independent review, 2026-09-19): confidence/abstain/audit
        # are now computed BEFORE the ledger write, not after, so the
        # audit flag can be STORED in the row itself (see
        # _ledger_decision()'s own docstring) rather than thrown away.
        # This also means the audit sample never sees this row's own
        # write reflected in decisions_path yet when it reads the ledger
        # for a calibrated threshold -- a row never influences its own
        # audit determination, which is more correct, not merely
        # incidental to the reordering.
        confidence = _confidence_for_routing(qtype, record)
        abstain_reason = _abstain_reason(qtype, record)
        if abstain_reason is not None:
            confidence = None
        audit = _audit_flag(entry_id, qtype, risk_class, confidence, decisions_path,
                             outcomes_path, decision_id, seams_config, rng)

        ledger_rec = _ledger_decision(decision_id, entry_id, qtype, record, audit=audit)
        written_id = None
        ledger_error = None
        if ledger_rec is not None:
            try:
                # M2 fix (independent review, 2026-09-19): nothing else in
                # this codebase ever created ledger_dir (only the BUDGET
                # file's own directory was ever makedirs'd) -- a fresh
                # JEV_STATE_DIR/ledger that has never been written to
                # raised FileNotFoundError here on every call, which the
                # bare `except ValueError` below let escape uncaught into
                # the broad handler further down, misclassified as a Jev
                # failure (api_failed=True) and blamed on the breaker.
                # exist_ok=True: idempotent, cheap, safe to call every
                # time rather than tracked with a one-shot flag.
                os.makedirs(os.path.dirname(decisions_path), exist_ok=True)
                _calibration.append_decision(decisions_path, ledger_rec,
                                              sibling_outcomes_path=outcomes_path)
                written_id = decision_id
            except ValueError:
                pass  # a malformed record this module itself built incorrectly (defensive)
            except OSError as exc:
                # M2: a LOCAL failure (disk, permissions, a race), never
                # Jev's fault -- must never set box.api_failed or trip the
                # breaker over a filesystem problem on THIS machine.
                ledger_error = "local ledger write failed (not a Jev failure): %s" % exc

        if mode_cfg == SHADOW:
            box.result = SeamResult(current_answer, record, SHADOW, ledger_error, audit, written_id)
            return
        if mode_cfg == ADVISE:
            box.result = SeamResult(current_answer, record, ADVISE, ledger_error, audit, written_id)
            return

        # act. risk_class came from the registry entry, nowhere else.
        if confidence is None:
            box.result = SeamResult(
                current_answer, record, ACT,
                _combine_reasons(ledger_error, abstain_reason or "no usable confidence for cascade routing"),
                audit, written_id)
            return

        calibration = _cascade.CalibrationHandle(decisions_path, outcomes_path,
                                                  promotions_path=promotions_path)
        decision_obj = {
            "id": decision_id, "family": entry_id, "qtype": qtype,
            "confidence": confidence, "model": record.get("model"),
        }
        try:
            routed = _cascade.route(decision_obj, risk_class, calibration)
        except Exception as exc:  # noqa: BLE001 -- never let the cascade's own
            # ValueError/OSError escape this worker; consult() itself is
            # documented as never raising, background thread included.
            box.result = SeamResult(
                current_answer, record, ACT,
                _combine_reasons(ledger_error, "cascade routing raised %s: %s" % (type(exc).__name__, exc)),
                audit, written_id)
            return

        if routed["outcome"] == "ACT":
            box.result = SeamResult(record["answer"], record, ACT, ledger_error, audit, written_id)
        else:
            box.result = SeamResult(
                current_answer, record, ACT,
                _combine_reasons(ledger_error, routed.get("reason") or routed["outcome"]),
                audit, written_id)
    except Exception as exc:  # noqa: BLE001 -- a surprise here must still let
        # the caller's box.event.wait() return rather than hang until its
        # own deadline for no visible reason; converted to a safe fallback.
        #
        # m1 fix (independent re-review, 2026-09-19): box.api_failed stays
        # False here -- NOT set True. decide() itself already reports its
        # own failure via the two explicit `isinstance(result, tuple)`
        # branches above (the primary call and the reordered probe), each
        # of which DOES set box.api_failed=True: those are the only two
        # places a real Jev-call failure is classified. Everything that
        # can reach THIS bare except is local code running after decide()
        # already succeeded (ledger listing inside _calibrated_threshold/
        # _audit_flag, rotation, the ledger write, cascade routing) --
        # probed: a ledger directory that can be written but not LISTED
        # made os.listdir raise inside _calibrated_threshold, which used
        # to land here and open the breaker against a Jev that had just
        # answered fine. A local bug on this machine is never a fact
        # about Jev, and must never count toward the consecutive-failure
        # streak that exists to protect Jev's own call surface.
        box.result = SeamResult(current_answer, None, mode_cfg,
                                 "unexpected error in Jev seam worker (not a Jev failure): %s: %s"
                                 % (type(exc).__name__, exc), False, None)
    finally:
        if record_breaker_here:
            if box.api_failed:
                _breaker_record_failure(seams_config, clock)
            else:
                _breaker_record_success()
        box.event.set()
        _release_inflight()


def _fires_and_forgets(mode_cfg):
    """True for shadow ONLY: consult() must never wait for this call's own
    worker, since shadow's return value never depends on Jev's answer, so
    waiting for it, even briefly, is a pure stall with no payoff.

    M3 fix (independent review, 2026-09-19): advise is NOT fire-and-forget
    -- advise exists so a caller's OWN interface can display Jev's
    answer (see the module docstring's MODES section, unchanged since
    before A0.8), which is impossible if consult() never waits long
    enough to learn it. Advise therefore now waits, bounded by the same
    hard deadline as act (it is for display, never a hot path: a caller
    that cannot afford to wait should use shadow instead, precisely
    because shadow throws Jev's answer away and advise does not). Only
    act's return value can also CHANGE based on Jev's answer; advise's
    never does (it always returns current_answer), but it still needs the
    answer itself to hand back in SeamResult.jev.

    Extracted to its own function so this single decision has one place
    to change or monkeypatch, rather than an inline condition consult()
    repeats."""
    return mode_cfg == SHADOW


def consult(entry_id, state, current_answer, *, seams_config, registry, ledger_dir,
            runner=None, rng=None, clock=None):
    """See the module docstring. Never raises for any documented failure
    cause; always returns a SeamResult within _hard_deadline(seams_config)
    seconds of being called, regardless of how slow or hung the Jev
    call itself is (see A0.8, THE ENABLEMENT GATE)."""
    clock = clock if clock is not None else time.monotonic

    mode_cfg = _resolve_mode(seams_config, entry_id)
    if mode_cfg == OFF:
        return SeamResult(current_answer, None, OFF, None, False, None)

    # A0.8 admission gates, in order, cheapest first: each is checked
    # BEFORE the registry lookup, mirroring OFF mode's own "nothing else
    # runs" contract -- a call denied here never reads the registry, never
    # calls decide(), never writes the ledger, never spawns a thread.
    #
    # claimed_probe (review minor, 2026-09-19): True only when THIS call
    # just claimed the breaker's single half-open probe slot. Every early
    # return between here and the worker spawn below releases it if set,
    # so a probe that was admitted but then refused for an UNRELATED
    # reason (registry, promotions path, budget, inflight) never leaves
    # the breaker permanently unable to probe again.
    breaker_reason, claimed_probe = _breaker_admission(seams_config, clock)
    if breaker_reason is not None:
        return SeamResult(current_answer, None, OFF, breaker_reason, False, None)

    # m3 fix (independent re-review, 2026-09-19): the whole admission-gate
    # section below (canary, registry, promotions path, decision_id,
    # inflight, budget, worker dispatch) runs inside one try/except/finally
    # so that ANY unexpected exception -- not only the documented refusals
    # each step already handles explicitly -- still returns a SeamResult
    # and still releases a claimed half-open probe, rather than escaping
    # consult() raw. Probed causes that used to do exactly that:
    # registry=None (raises TypeError inside jev_registry.get()/callable(),
    # which only catches its own RegistryError) and a budget_path holding a
    # NUL byte (fixed at ITS OWN source too, in _budget_path() -- see
    # item 6 -- kept caught here as well since a local module this one
    # calls can always gain a new undocumented raise later).
    #
    # ONE SLOT GUARD (round 4 fix, item 4, 2026-09-19): `inflight_claimed`
    # and `claimed_probe` are released in exactly ONE place, the `finally`
    # below, keyed off `handed_off`. Major m1 (independent re-review,
    # 2026-09-19): the OLD comment at the except clause claimed "no worker
    # was dispatched, so no inflight slot is held on this call's behalf
    # either" -- which was WRONG, since the inflight slot is acquired
    # BEFORE the budget step inside this same try, so any exception raised
    # by (or after) that acquisition -- the budget step itself raising, or
    # anything added here later -- leaked the slot forever, permanently
    # shrinking the effective max_inflight_calls by one per leak (probed:
    # a budget step raising RecursionError/FileNotFoundError left
    # inflight_now elevated across every subsequent call, eventually
    # wedging every call as "already in flight" even after the underlying
    # cause was fixed). `handed_off` is set True only once worker.start()
    # AND _register_worker() have both succeeded: past that point,
    # ownership of the inflight slot passes to the worker (which releases
    # it in its own finally -- see _run_seam_job()) and ownership of the
    # probe claim passes to either the worker (record_breaker_here, shadow
    # only) or the waiting caller thread below (advise/act) -- see
    # _breaker_admission()'s own docstring on why shadow's probe claim
    # must survive past this function's own return. The `finally` releases
    # both exactly when `handed_off` is still False, covering every early
    # `return` inside the try (Python runs `finally` on the way out of a
    # `return` too) and every exception, uniformly, in one place instead
    # of a `_release_inflight()`/`_breaker_release_probe()` pair repeated
    # at every one of the admission gates above.
    inflight_claimed = False
    handed_off = False
    try:
        canary_path = _canary_marker_path(seams_config)
        if os.path.isfile(canary_path):
            # Item 4 (A0.8): canary FAIL now means OFF, not shadow -- no call
            # at all, for every configured mode including shadow. Supersedes
            # the old rule 3 (canary forces shadow); see the module docstring.
            return SeamResult(current_answer, None, OFF,
                               "canary reset marker present at %s: mode forced to off (canary FAIL)"
                               % canary_path, False, None)

        ok, question_or_reason = _registry.callable(registry, entry_id)
        if not ok:
            return SeamResult(current_answer, None, mode_cfg, question_or_reason, False, None)
        question = question_or_reason

        try:
            entry = _registry.get(registry, entry_id)
        except _registry.RegistryError as exc:
            return SeamResult(current_answer, None, mode_cfg,
                               "registry entry vanished between callable() and get(): %s" % exc,
                               False, None)
        risk_class = entry.get("risk")

        # Item 7 (Muse G1 point 5 / A0.8 round 6, hardened in the BLOCK
        # review of round 6): a registry entry not PROVEN to read
        # consult()'s return value -- the eight G1 call sites marked
        # "call_site_reads_answer": false (board_status, doc_assurance,
        # export_public, intake_score x2, mobile_hybrid_action_router x2,
        # receipt_door), but also anything else that is not the literal
        # Python bool True (a non-bool value such as the string "false" or
        # 0, once the field is PRESENT at all) -- cannot mean anything
        # different configured to advise or act than it already means in
        # shadow: a call site not affirmatively proven to read the
        # answer discards it BY DESIGN or by data-entry accident either
        # way (see the module docstring's WAVE 1 IS SHADOW-ONLY BY
        # CONTRACT section), so an operator flipping the mode to advise
        # or act would believe the seam now enforces something it cannot
        # prove it does. The check is "is not True" rather than "is
        # False" precisely so a one-character data typo (a string
        # "false", a stray 0) cannot slip past this guard the way it
        # slips past a strict boolean-False comparison. The field being
        # MISSING entirely is left exactly as it always was: every entry
        # except the eight G1 ones omits it, and omission has never meant
        # anything here (widening this guard to cover absence too was
        # tried and reverted -- it forced every other advise/act entry in
        # this codebase's own test suite to off, a chain reaction this fix
        # must not cause). jev_registry.lint() already refuses the present
        # case statically (given the seams config); this is the matching
        # RUNTIME guard, in the one place both the resolved mode and the
        # resolved entry are already in hand together, so the mode is
        # forced to off here too -- one stderr line, never a silent
        # downgrade -- rather than trusting every future caller to have run
        # lint first.
        _reads_answer_present = "call_site_reads_answer" in entry
        _reads_answer = entry.get("call_site_reads_answer")
        if (mode_cfg in _registry.ADVISE_ACT_REQUIRES_READ and _reads_answer_present
                and _reads_answer is not True):
            print(
                "jev_seam: entry %r is configured %r, but its call site is not "
                "proven to read consult()'s return value (call_site_reads_answer "
                "is %r in the registry, not the bool True) -- treating this call "
                "as off rather than letting a mode flip look like enforcement it "
                "cannot be" % (entry_id, mode_cfg, _reads_answer),
                file=sys.stderr,
            )
            return SeamResult(current_answer, None, OFF,
                               "call_site_reads_answer is not the bool True: advise/act forced to off",
                               False, None)

        decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
        outcomes_path = os.path.join(ledger_dir, "outcomes.jsonl")
        # M3 fix (review 70268c3): NEVER build the promotions store from
        # ledger_dir -- see _promotions_path()'s own docstring for why. Minor
        # fix, re-review of 1385ab88c: an explicit override resolving under
        # ledger_dir is refused too, not only the module's own default. Item
        # 3, approved-with-nits review 2026-09-18: even the unconfigured
        # DEFAULT resolving under ledger_dir has nowhere safer left to fall
        # back to, so it escalates the whole call instead -- no decide() call,
        # no ledger write, exactly like a registry refusal (rule 1) above.
        try:
            promotions_path = _promotions_path(seams_config, ledger_dir)
        except _UnsafePromotionsPath as exc:
            return SeamResult(current_answer, None, mode_cfg, str(exc), False, None)

        qtype = question["type"]
        decision_id = _decision_id(entry_id, state)

        # A0.8, item 1: bounded concurrency -- a full "queue" drops rather
        # than blocks. M1 fix (independent review, 2026-09-19): checked BEFORE
        # the budget spend, not after -- the docstring always claimed a
        # dropped call spends no budget, but the ORIGINAL ordering spent the
        # slot first regardless of whether a worker ever started, so one
        # hung/stuck runner filling every inflight slot could silently drain
        # an entire day's budget without a single real attempt (probed:
        # max_inflight_calls=1, budget 5, 8 calls -> 1 real attempt, 4 drops,
        # 3 "budget exhausted", file read {"calls": 5}).
        inflight_ok, inflight_count = _try_acquire_inflight(seams_config)
        if not inflight_ok:
            _record_drop(entry_id)
            return SeamResult(current_answer, None, mode_cfg,
                               "seam call dropped: %d calls already in flight (limit %d)"
                               % (inflight_count, _max_inflight(seams_config)),
                               False, None)
        inflight_claimed = True  # the ONE slot guard's finally now owns releasing this

        # A0.8, budget: consumed only now, AFTER the inflight slot is secured
        # -- a call that will not actually start (dropped above, or refused
        # earlier for registry/promotions/breaker reasons) never burns one.
        budget_ok, budget_deny_reason = _consume_budget_slot(seams_config)
        if not budget_ok:
            return SeamResult(current_answer, None, OFF, budget_deny_reason, False, None)

        record_breaker_here = _fires_and_forgets(mode_cfg)  # shadow only, see its own docstring
        box = _JobBox()
        worker = threading.Thread(
            target=_run_seam_job,
            kwargs=dict(
                box=box, entry_id=entry_id, state=state, question=question, qtype=qtype,
                decision_id=decision_id, risk_class=risk_class, mode_cfg=mode_cfg,
                current_answer=current_answer, decisions_path=decisions_path,
                outcomes_path=outcomes_path, promotions_path=promotions_path,
                seams_config=seams_config, runner=runner, rng=rng, clock=clock,
                record_breaker_here=record_breaker_here,
            ),
            daemon=True,
            # Named for its own entry_id (item 3, A0.8 round 6) so
            # _atexit_drain() can name which entry a row abandoned at
            # process exit belonged to, not just report a bare count.
            name=str(entry_id),
        )
        try:
            worker.start()
            _register_worker(worker)  # M4: drain()/atexit can wait for it
        except RuntimeError as exc:
            # Review minor, 2026-09-19: thread creation failure (e.g. the OS
            # thread limit) used to escape consult() entirely -- breaking the
            # documented "never raises" promise -- AND leaked the inflight
            # slot (fixed generally by the slot guard now, but named here
            # too since this is the one caller-visible reason a real attempt
            # still counts as spent: budget stays spent, matching the actual
            # attempt this represents; the worker simply never got to run
            # it).
            return SeamResult(current_answer, None, mode_cfg,
                               "could not start the Jev seam worker: %s: %s" % (type(exc).__name__, exc),
                               False, None)

        handed_off = True  # worker owns the inflight slot (and, for shadow, the probe claim) from here
    except Exception as exc:  # noqa: BLE001 -- m3: consult() must never raise.
        return SeamResult(current_answer, None, mode_cfg,
                           "NO-DATA: unexpected error before the Jev call: %s: %s"
                           % (type(exc).__name__, exc), False, None)
    finally:
        if not handed_off:
            if inflight_claimed:
                _release_inflight()
            if claimed_probe:
                _breaker_release_probe()

    if record_breaker_here:
        # shadow: submitted, not awaited. N1 fix (independent re-review,
        # 2026-09-19): decision_id=None here, RESTORING the rest of this
        # module's "decision_id is the id this call actually WROTE, never
        # would have written" rule (see the module docstring's rule 4).
        # An earlier draft returned the id the worker WOULD write under
        # as a "deliberate, narrow exception" so shadow rows could be
        # correlated to a later human label -- but a bridge NO-DATA, a
        # near-threshold reorder disagreement, or a local ledger-write
        # error (all routine outcomes for a real call, not rare) leave
        # that id NEVER written, and the caller could not tell a written
        # id from an unwritten one: both came back with the identical
        # reason="shadow: submitted". Labelling an outcome against an
        # unwritten id then poisons that whole family's calibration as a
        # permanent ledger anomaly (see jev_calibration.append_outcome()'s
        # own decisions_path refusal, added for the same reason). shadow
        # is fire-and-forget by design (see _fires_and_forgets()'s own
        # docstring): a caller that genuinely needs this call's eventual
        # id must read the ledger itself once the worker has landed, not
        # trust a value handed back before the write.
        return SeamResult(current_answer, None, mode_cfg, "%s: submitted" % mode_cfg, False, None)

    # advise and act both wait, bounded by the hard deadline: advise so it
    # can expose Jev's answer for display (M3 fix, independent review
    # 2026-09-19 -- see _fires_and_forgets()'s own docstring), act because
    # its own return value can change based on that answer. See
    # jev_decide.decide()'s own bridge-level DEFAULT_TIMEOUT_SECONDS for
    # the outer bound on how long the worker itself can run even when
    # nobody ends up waiting on it (a timeout below).
    deadline = _hard_deadline(seams_config)
    completed = box.event.wait(deadline)
    if not completed:
        # The worker keeps running (and will still write the ledger row
        # and release its own inflight slot when it eventually finishes):
        # this call itself never waits past its deadline for it.
        _breaker_record_failure(seams_config, clock)
        return SeamResult(current_answer, None, mode_cfg,
                           "NO-DATA: timed out after %.3fs waiting for Jev "
                           "(call continues in the background)" % deadline,
                           False, None)

    if box.api_failed:
        _breaker_record_failure(seams_config, clock)
    else:
        _breaker_record_success()
    return box.result
