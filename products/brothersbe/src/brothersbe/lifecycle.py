"""LANE C1 (B-003): the one reducer that decides "what should happen next"
for a change, so every surface that answers that question agrees BY
CONSTRUCTION instead of by coincidence.

Reproduced before this module existed: `status.py` carried at least two
independent derivations of "the next action" (`build_report`'s blocker-first
`_next_action`, reading only sections 1-4, and `build_team_report`'s
severity-10 finding, picking the minimum RAW team-severity number among
every OTHER finding recorded for a change), and `skills/next/SKILL.md`
carried a THIRD, hand-written priority ladder in prose that re-derived the
same judgement from the same JSON a different way. A temp-dir reproduction
built a single dossier whose only outstanding obligation was review (every
other check clear, its one task closed) and got three different answers:
plain `sbe status` said "nothing blocking here" (it never looked at task or
review state at all), `sbe status --team`'s own severity-10 said "nothing
left to do, open a PR" (severity 9, "completed changes", sorts numerically
below severity 11, "review record", even though review had not run), and
`/brothersbe:next`'s prose ladder said "run review" (its own rung order
puts review before "everything green", unlike team's raw severity numbers).

This module owns the ONE priority ladder now. `status.py`'s `build_report`
and `build_team_report` both build a list of CANDIDATES -- one dict per
outstanding fact worth acting on, each naming a `rung` from the table below
-- and call `reduce_next_action` to pick the single most urgent one. Neither
function invents its own tie-break or its own idea of which comes first.

LOOP B: a FOURTH hand-rolled ladder was found in `handover.py`'s own
`_derive_next_action` (a private `for kind in ("convergence", "approval",
"review"): ...` sequence, checked in that fixed order regardless of this
module's own rungs, followed by its own separate dirty-worktree, receipt,
active-task and ready-task checks). It is now folded in the same way:
`handover.py`'s `_handover_candidates` builds a candidate list, calling
`status.py`'s own `_change_ladder_candidates` directly for the facts that
function already owns (active task, ready task, review, and approval's
NO-DATA/absent verdict, gated on every task being closed clean), and
`status.py`'s own `_approval_ladder_candidate` a second time, unconditionally,
for a recorded approval FAILURE or staleness (ROUND 2: never gated the way
the NO-DATA verdict is, mirroring `status_mod._superseded_by_shared_ladder`'s
identical rule) -- rather than re-deriving any of those judgements a second,
weaker way -- and reduces through `reduce_next_action` exactly like every
other surface here.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only. This module reads nothing and writes nothing; it is a pure
function over facts its callers already gathered, by the same "never a
second gate runner" law `status.py`'s own module docstring states for
itself.
"""

#: Canonical rung order, most urgent first. The INTEGER VALUES matter only
#: as a sort key (lower sorts first, i.e. wins); the gaps between them are
#: deliberate room to insert a future rung without renumbering every
#: existing one. This is the ONE list every surface's "next action" is
#: picked from -- see `reduce_next_action`.
RUNG_BROKEN_CLAIM = 0
RUNG_MERGE_BLOCKER = 10
RUNG_ACTIVE_CONFLICT = 20
RUNG_STALE_RECORD = 30
RUNG_MISSING_APPROVAL = 40
RUNG_CONVERGENCE_FAILURE = 50
#: LOOP B: `handover.py`'s own candidate for a dirty, uncommitted worktree at
#: the moment a handover is prepared. No fact in `status.py`'s own ladder
#: corresponds to this: a plain `sbe status` never inspects working-tree
#: cleanliness, so this rung is not borrowed from an existing team severity
#: the way `handover.py`'s convergence, approval and review candidates are
#: (see `TEAM_SEVERITY_TO_RUNG`). Placed between convergence failure and
#: missing evidence: uncommitted state risks losing work outright, which
#: `handover.py` has always treated as more urgent than a stale evidence
#: receipt but less urgent than a broken or missing convergence/approval
#: gate (see that module's `_handover_candidates`).
RUNG_UNCOMMITTED_STATE = 52
RUNG_MISSING_EVIDENCE = 55
RUNG_ACTIVE_TASK = 60
RUNG_READY_TASK = 70
RUNG_REVIEW_NOT_CLEARED = 80
RUNG_FINISH = 90

#: (rung, actionId, label). `actionId` is the stable, machine-matchable slug
#: a skill or script can branch on without parsing prose; `label` is the
#: short human name for the same rung. Every candidate's `rung` must be one
#: of these, or `candidate()` refuses it: a rung this table has not been
#: taught about is a programming error in the caller, never a silent
#: fallback to something unrelated.
_RUNGS = (
    (RUNG_BROKEN_CLAIM, "resolve-broken-claim", "broken claim"),
    (RUNG_MERGE_BLOCKER, "resolve-merge-blocker", "merge blocker"),
    (RUNG_ACTIVE_CONFLICT, "resolve-active-conflict", "active conflict"),
    (RUNG_STALE_RECORD, "refresh-stale-record", "stale record"),
    (RUNG_MISSING_APPROVAL, "resolve-missing-approval", "missing approval"),
    (RUNG_CONVERGENCE_FAILURE, "resolve-convergence-failure", "convergence failure"),
    (RUNG_UNCOMMITTED_STATE, "resolve-uncommitted-state", "uncommitted state"),
    (RUNG_MISSING_EVIDENCE, "provide-missing-evidence", "missing evidence"),
    (RUNG_ACTIVE_TASK, "continue-active-task", "active task"),
    (RUNG_READY_TASK, "start-ready-task", "ready task"),
    (RUNG_REVIEW_NOT_CLEARED, "run-review", "review not cleared"),
    (RUNG_FINISH, "finish", "nothing outstanding"),
)
ACTION_ID = dict((rung, action_id) for rung, action_id, _label in _RUNGS)
LABEL = dict((rung, label) for rung, _action_id, label in _RUNGS)

#: `status.py`'s `TEAM_SEVERITIES` (team severity number -> this module's
#: rung), so `build_team_report`'s severity-10 finding is picked through the
#: SAME table `build_report`'s own candidates draw from, rather than a raw
#: integer comparison over severity numbers that were never assigned with
#: "which one wins" in mind.
#:
#: Severity 9 ("completed changes") maps to `RUNG_FINISH`, AFTER severity 11
#: ("review record", `RUNG_REVIEW_NOT_CLEARED`) in this order, even though
#: 9 < 11 as a bare integer. That inversion is deliberate and is the fix
#: this lane exists for: team's raw severity numbering puts "review record"
#: outside 1..6 for an unrelated reason (LT-302's own note in `status.py`,
#: so a missing review never blocks a merge), and picking a next action by
#: raw severity comparison let "nothing left to do, open a pull request"
#: (9) outrank "run review" (11) whenever a change's tasks were all closed
#: clean and review had simply never run -- recommending a merge before
#: anyone had reviewed it. Being reviewed and cleared is a PRECONDITION for
#: "nothing outstanding", not a sibling fact that happens to sort later.
#:
#: Severity 1, 2 and 3 (broken claims, merge blockers, scope conflicts) map
#: onto the identical concepts `build_report`'s own BROKEN CLAIMS, MERGE
#: BLOCKERS and ACTIVE CONFLICTS sections already use those rungs for.
TEAM_SEVERITY_TO_RUNG = {
    1: RUNG_BROKEN_CLAIM,
    2: RUNG_MERGE_BLOCKER,
    3: RUNG_ACTIVE_CONFLICT,
    4: RUNG_STALE_RECORD,
    5: RUNG_MISSING_APPROVAL,
    6: RUNG_CONVERGENCE_FAILURE,
    7: RUNG_ACTIVE_TASK,
    8: RUNG_READY_TASK,
    9: RUNG_FINISH,
    11: RUNG_REVIEW_NOT_CLEARED,
}


def candidate(rung, reason):
    """One outstanding fact worth acting on: `rung` (one of the constants
    above) and `reason` (the recommended action, as text). Extra keys can be
    added to the returned dict by the caller after the fact (team attaches
    its own `verdict`/`evidence`/`commit`/`owner` this way, by starting from
    an existing finding dict and adding `rung` to a copy of it, rather than
    calling this function at all -- see `build_team_report`); the two
    required keys are always present.
    """
    if rung not in ACTION_ID:
        raise ValueError("unknown rung %r; add it to lifecycle._RUNGS first" % (rung,))
    return {"rung": rung, "reason": reason}


def reduce_next_action(candidates):
    """The one reducer. `candidates`: a non-empty iterable of dicts, each
    carrying at least `rung` (one of the constants above) and `reason` (the
    text naming what to do). Extra keys on a candidate are carried through
    unmodified on the winner, so a caller that stashed more than rung/reason
    on its candidates (team's finding fields) can read them straight off the
    dict this function returns rather than looking the winner up a second
    time by identity or by matching text.

    Ties (more than one candidate at the lowest rung present) are broken by
    ORDER: the first such candidate in `candidates` wins. This function
    never re-sorts and never invents its own tie-break -- the caller is
    responsible for handing candidates to this function already in its own
    priority order among equals (`build_team_report` sorts by `(rung,
    evidence, detail)` before calling this, mirroring exactly the tie-break
    this file used before this module existed, so that refactor changed no
    team output), so a change to a tie-break rule stays owned by ONE call
    site, not duplicated here.

    Returns a dict: every key the winning candidate carried, PLUS
    `actionId` (the stable slug), `label` (the human name for the winning
    rung) and `basis` (always `"derived"`: picking the most urgent of
    several already-recorded facts is itself a derivation, never a fact
    freshly observed by this function).

    Raises `ValueError` on an empty `candidates`: every caller must supply
    at least a fallback candidate (its own "nothing outstanding" wording,
    at `RUNG_FINISH`) rather than have this function invent placeholder
    text of its own -- the exact phrasing of "nothing blocking" belongs to
    the caller that has always owned it, not a second copy of it here.
    """
    items = list(candidates)
    if not items:
        raise ValueError(
            "reduce_next_action requires at least one candidate; the caller must always "
            "supply its own fallback (RUNG_FINISH) rather than have this function invent "
            "placeholder text")
    best_rung = min(item["rung"] for item in items)
    winner = next(item for item in items if item["rung"] == best_rung)
    result = dict(winner)
    result["actionId"] = ACTION_ID[best_rung]
    result["label"] = LABEL[best_rung]
    result["basis"] = "derived"
    return result


#: The three verdict words `reduce_readiness` accepts on a `requiredProof`
#: entry, byte-identical to `contracts.VERDICTS`. Not imported from there:
#: this module reads nothing and writes nothing (see the module docstring),
#: so it takes no dependency on `contracts.py`; the two names are held in
#: agreement by `tools/test_sbe_readiness.py` and `tools/test_sbe_contracts.py`
#: rather than by a shared import.
_READINESS_VERDICT_WORDS = ("PASS", "FAIL", "NO-DATA")


#: Vacuous tokens `_hollow` treats as recording nothing even though they are
#: non-empty strings, mirroring `tools/sbe_checks.py`'s `answered()`/
#: `VACUOUS_VALUES` rule (a value that survives an empty-string test but still
#: names nothing: a note to the author, never a value). Not imported: this
#: module reads nothing and writes nothing (see the module docstring), so it
#: takes no dependency on `contracts.py` or `tools/`, exactly the same reason
#: `_READINESS_VERDICT_WORDS` above is spelled locally rather than imported;
#: the two lists are held in agreement with `tools/sbe_checks.py`'s own list
#: by `tools/test_sbe_readiness.py` alone.
_VACUOUS_TOKENS = ("todo", "tbd", "n/a", "na", "none", "null", "unknown",
                   "pending", "-", "?")


def _hollow(value):
    """True when `value` records nothing this reducer can trust as a head
    commit or a human name: not a string at all, or a string whose stripped,
    lowercased form is empty or is one of `_VACUOUS_TOKENS`.

    Round two of this rule. The original tested only `value is None or value
    == ""`, so it caught the classic hollow-identity case `contracts.py`'s
    own binders warn about (`_hollow_problems`'s docstring: "`None == None`
    is True, as is `"" == ""`. Two documents that each identify nothing
    therefore agreed with each other perfectly") but missed three whole
    classes the refuter reproduced: whitespace-only strings ("   "), TODO-
    class placeholder tokens ("TODO", "n/a") that are non-empty on their
    face, and empty containers (`[]`, `{}`). Worse, every NON-STRING (`0`,
    `False`, `0.0`, `1`, `True`) passed this test as if it were a real
    identity and then rode Python's own cross-type equality straight through
    the `!=` comparison below (`0 == False == 0.0`, `1 == True`), so a head
    commit typed as `0` "matched" one typed as `False`.

    A non-string is now ALWAYS hollow here, mirroring `contracts.py`'s own
    round-two typed-identity rule (`_identity_type_problem`): a head commit
    or a human name is a string, and `0`/`False`/`0.0`/`[]` name nothing
    merely by being equal to each other under Python's numeric tower. This
    is a LOCAL reimplementation of `tools/sbe_checks.py`'s `answered()`, not
    an import of it (see `_VACUOUS_TOKENS` above), applied to the two head
    commits `reduce_readiness` compares and to `accountableHuman`, rather
    than a narrower rule that could drift from either.
    """
    if not isinstance(value, str):
        return True
    stripped = value.strip()
    return not stripped or stripped.lower() in _VACUOUS_TOKENS


def reduce_readiness(facts):
    """The one reducer that decides which of `contracts.READINESS_STATES` a
    change is in, so every surface that answers "is this ready for a human
    to decide" agrees BY CONSTRUCTION, the same guarantee `reduce_next_action`
    gives "what should happen next".

    Reads NOTHING from disk: `facts` is a dict the caller already gathered,
    carrying:

      requiredProof     a list of dicts, each at least `check` (name) and
                        `verdict` (one of `PASS`, `FAIL`, `NO-DATA`)
      noDataPermitted   bool: policy explicitly permits the NO-DATA entries
                        present in `requiredProof`; this function never
                        decides policy, it only reads the caller's answer
      headCommit        the change's head commit right now
      dossierHeadCommit the head commit the dossier being judged was built
                        over
      accountableHuman  the name of the human accountable for this decision,
                        or None/empty when nobody is named

    A DETERMINISTIC TABLE, read top to bottom, MOST SEVERE FIRST: the first
    row that matches wins, and every later row is never reached once an
    earlier one has:

      1. any `requiredProof` entry reads `FAIL`               -> NOT_READY
         (the failing check(s) are named in the reason: shipping over a
         known-broken claim is never a human decision, it is a bug)
      2. `requiredProof` is missing or an empty list             -> NOT_READY
         (an empty checklist is not a passed one: this is not the happy path
         falling all the way through the table by having nothing left to
         object to, it is the same finding as row 1 one level up, so it sits
         next to it rather than after every other row)
      3. `headCommit` and `dossierHeadCommit` mismatch, OR either is hollow
         (`None`/`""`/whitespace/a vacuous token/a non-string; hollow values
         are a mismatch here too, mirroring `contracts.py`'s own binders: two
         heads that both record nothing "agreed with each other perfectly" is
         exactly the false match that rule exists to refuse)     -> NOT_READY
         (the dossier describes a program this is not the head of anymore)
      4. `accountableHuman` is hollow (see `_hollow` above)       -> NOT_READY
         (nobody is named to carry the decision this state would offer)
      5. any `requiredProof` entry reads `NO-DATA` and
         `noDataPermitted` is not exactly `True`                 -> NOT_READY
         (an absence policy has not signed off on is not a known risk yet,
         it is an unexamined gap; `noDataPermitted` must be the literal
         `True`, never a truthy stand-in for it, see the raise below)
      6. any `requiredProof` entry reads `NO-DATA` and
         `noDataPermitted` is `True`                        -> READY_WITH_KNOWN_RISK
         (the NO-DATA checks are named as `knownRisks`: a human can decide
         to accept them, which is a different thing from nobody having
         looked)
      7. otherwise (every entry PASS, heads match, a human is named)
                                                                  -> READY_FOR_HUMAN_DECISION

    Returns a dict: `readinessState` (one of `contracts.READINESS_STATES`),
    `reasons` (a non-empty list of plain sentences explaining the state),
    `knownRisks` (a list; non-empty only in the READY_WITH_KNOWN_RISK arm,
    where it names the permitted NO-DATA checks, and empty in every other
    arm: a NOT_READY state has not been offered a risk to accept, and the
    READY_FOR_HUMAN_DECISION arm is reached only when there is none to
    name), and `basis` (always `"derived"`, the same word
    `reduce_next_action` returns for the same reason: picking a state from
    several already-recorded facts is itself a derivation).

    Raises `ValueError` naming the value on an unknown verdict string in
    `requiredProof`, mirroring `candidate()`'s own refusal of an unknown
    rung: a verdict word this function has not been taught about is a
    programming error in the caller, never a silent PASS or a silent FAIL.
    Raises `ValueError` naming the value and its type when `requiredProof`
    holds a `NO-DATA` entry and `noDataPermitted` is neither `True` nor
    `False`: `"false"` (a string), `1`, `0.1`, `{"policy": "denied"}` and
    `None` are none of them a policy answer, and reading any of them by bare
    truthiness let the string `"false"` grant the exact permission its own
    spelling refuses. This mirrors the unknown-verdict refusal one paragraph
    up: an answer this function has not been taught to read as yes or no is a
    programming error in the caller, never a silent grant.
    """
    required_proof = list(facts.get("requiredProof") or [])
    for entry in required_proof:
        verdict = entry.get("verdict")
        if verdict not in _READINESS_VERDICT_WORDS:
            raise ValueError(
                "reduce_readiness got the verdict %r for check %r; the only verdicts it "
                "knows are %s" % (verdict, entry.get("check"),
                                  ", ".join(_READINESS_VERDICT_WORDS)))

    failing = [e for e in required_proof if e["verdict"] == "FAIL"]
    if failing:
        names = ", ".join(str(e.get("check") or "(unnamed check)") for e in failing)
        return {"readinessState": "NOT_READY",
                "reasons": ["required proof failed: %s" % names],
                "knownRisks": [], "basis": "derived"}

    if not required_proof:
        return {"readinessState": "NOT_READY",
                "reasons": ["no required proof was gathered; an empty checklist is not "
                           "a passed one"],
                "knownRisks": [], "basis": "derived"}

    head_commit = facts.get("headCommit")
    dossier_head_commit = facts.get("dossierHeadCommit")
    if _hollow(head_commit) or _hollow(dossier_head_commit) or head_commit != dossier_head_commit:
        return {"readinessState": "NOT_READY",
                "reasons": ["the change's head commit (%r) and the dossier's head commit "
                           "(%r) do not both name the same commit; a dossier that does not "
                           "match the head it is judged against describes a different "
                           "program than the one on disk now"
                           % (head_commit, dossier_head_commit)],
                "knownRisks": [], "basis": "derived"}

    if _hollow(facts.get("accountableHuman")):
        return {"readinessState": "NOT_READY",
                "reasons": ["no accountable human is named for this decision"],
                "knownRisks": [], "basis": "derived"}

    no_data = [e for e in required_proof if e["verdict"] == "NO-DATA"]
    if no_data:
        permitted = facts.get("noDataPermitted")
        if permitted is not True and permitted is not False:
            raise ValueError(
                "reduce_readiness got noDataPermitted=%r (a %s), and NO-DATA is present "
                "in requiredProof; this must be exactly True or False, never a truthy "
                "stand-in for either (a string, a number or a dict read by bare "
                "truthiness let \"false\" grant the permission its own spelling refuses)"
                % (permitted, type(permitted).__name__))
        if not permitted:
            names = ", ".join(str(e.get("check") or "(unnamed check)") for e in no_data)
            return {"readinessState": "NOT_READY",
                    "reasons": ["required proof is NO-DATA and policy has not permitted "
                               "it: %s" % names],
                    "knownRisks": [], "basis": "derived"}

    if no_data:
        risks = [str(e.get("check") or "(unnamed check)") for e in no_data]
        return {"readinessState": "READY_WITH_KNOWN_RISK",
                "reasons": ["every required check passed except NO-DATA entries policy has "
                           "explicitly permitted as known risk: %s" % ", ".join(risks)],
                "knownRisks": risks, "basis": "derived"}

    return {"readinessState": "READY_FOR_HUMAN_DECISION",
            "reasons": ["every required check passed, the change's head matches the "
                       "dossier it was judged against, and an accountable human is named"],
            "knownRisks": [], "basis": "derived"}


# ---------------------------------------------------------------------------
# BAND L, L4: two more reducers, same law as the two above -- reads nothing
# from disk, writes nothing, takes no dependency on `contracts.py` (its
# vocabulary words are spelled locally and held in agreement with
# `contracts.REALITY_STATES` by `tools/test_sbe_reality.py`, exactly the way
# `_READINESS_VERDICT_WORDS` above is spelled locally rather than imported).
# `reduce_verified_reality` is the one reducer for "has this release been
# verified in reality"; `reduce_value_realization` is the one reducer for
# "did it actually realize the business value it shipped for". Both compose
# with L1 (`evidence.py`'s release receipt) and L3 (`observation.py`'s
# result adapter) only through FACTS a caller already gathered from them,
# never by reading either module's own objects here.
# ---------------------------------------------------------------------------

#: Byte-identical to `contracts.REALITY_STATES`, spelled locally for the
#: same reason `_READINESS_VERDICT_WORDS` above is: this module takes no
#: dependency on `contracts.py`. Held in agreement by
#: `tools/test_sbe_reality.py`.
_REALITY_STATE_WORDS = ("REALITY_NOT_OBSERVED", "VERIFIED_IN_REALITY",
                        "CONTRADICTED_BY_REALITY")

#: `facts["windowState"]`'s closed vocabulary: `"OPEN"` (the observation
#: contract's window has not elapsed yet), `"COMPLETE"` (elapsed, with
#: observations recorded across it) and `"EXPIRED"` (elapsed, with NOTHING
#: recorded in it -- Codex risk 3's own case: an expired window with no
#: observations is not evidence of anything, and must never read as quietly
#: complete; see row 7 of `reduce_verified_reality`'s own docstring table).
_WINDOW_STATES = ("OPEN", "COMPLETE", "EXPIRED")

#: `facts["observationStatus"]`'s closed vocabulary, mirroring the `status`
#: field `observation.read_observation_result` (L3) returns: `"OBSERVED"`,
#: a source was actually read, or `"NO-DATA"`, it was not (absent,
#: unreadable, or never attempted). NO-DATA is never silently treated as
#: evidence here (row 6 below): an unavailable source stays
#: REALITY_NOT_OBSERVED, exactly as `reduce_readiness` treats an
#: unpermitted NO-DATA as NOT_READY rather than a pass with a shrug.
_OBSERVATION_STATUSES = ("OBSERVED", "NO-DATA")


def reduce_verified_reality(facts):
    """The one reducer for `contracts.REALITY_STATES` (spelled locally as
    `_REALITY_STATE_WORDS`, see above): has this release been verified in
    reality, contradicted by it, or not observed at all.

    Reads NOTHING from disk, the same law `reduce_readiness` states for
    itself. `facts` is a dict the caller already gathered (from a release
    receipt, L1; an observation result, L3; and whatever rollback/incident
    signal the caller's own tooling tracks):

      releasedCommit           the commit the release receipt names, or a
                               hollow value (`_hollow`, above) when no
                               receipt was found
      expectedCommit           the commit this reducer is judging reality
                               FOR
      observationStatus        `"OBSERVED"` or `"NO-DATA"` (`_OBSERVATION_
                               STATUSES`): did L3's adapter actually read a
                               source for this release
      windowState               `"OPEN"`, `"COMPLETE"` or `"EXPIRED"`
                               (`_WINDOW_STATES`): where the observation
                               contract's window stands right now
      observationsRecorded      bool: at least one observation exists
                               inside that window
      rollback                  bool: this release was rolled back
      incident                  bool: an incident was attributed to this
                               release
      acceptanceBehaviorFailed  bool: a predeclared acceptance behavior
                               failed against this release
      previouslyVerified        bool, optional: reality was
                               VERIFIED_IN_REALITY before this call.
                               Carried through into the REASON TEXT only,
                               on rows 3-5 below -- it never gates
                               anything, which is the point of it: this
                               function has no "if already verified, stay
                               verified" shortcut anywhere, so a release it
                               once called VERIFIED_IN_REALITY is
                               contradicted the moment row 3, 4 or 5 goes
                               true, the same as a release never verified
                               at all. Verified is never terminal against
                               a later contrary fact.

    A DETERMINISTIC TABLE, read top to bottom, MOST DECISIVE FIRST, mirroring
    `reduce_readiness`'s own table:

      1. no release receipt (`releasedCommit` hollow)      -> REALITY_NOT_OBSERVED
      2. the receipt names a commit other than
         `expectedCommit`                                  -> REALITY_NOT_OBSERVED
         (a receipt for another commit is not evidence about this one)
      3. `rollback` is true                                -> CONTRADICTED_BY_REALITY
      4. `incident` is true                                -> CONTRADICTED_BY_REALITY
      5. `acceptanceBehaviorFailed` is true                -> CONTRADICTED_BY_REALITY
         (rows 3-5 are checked BEFORE source or window state on purpose: a
         confirmed rollback, incident or acceptance failure is direct
         evidence, whatever the watched signal's own source or window is
         doing right now -- this is also what makes the late-regression
         transition correct, see `previouslyVerified` above)
      6. `observationStatus` is `"NO-DATA"`                 -> REALITY_NOT_OBSERVED
         (an unavailable or unreadable source is not evidence of anything;
         NO-DATA never promotes to a verified state by default)
      7. `windowState` is `"EXPIRED"` and
         `observationsRecorded` is falsy                    -> REALITY_NOT_OBSERVED
         (Codex risk 3: an expired window with nothing recorded in it is
         still unobserved, named with the expiry, and is NEVER auto-
         verified by the window simply having run out)
      8. `windowState` is `"OPEN"`                          -> REALITY_NOT_OBSERVED
         (the window has not finished; there is nothing to verify yet. An
         `"EXPIRED"` window reaches this row only when row 7 did NOT
         already return, i.e. it DID collect observations, so row 8 must
         check for `"OPEN"` by name rather than `!= "COMPLETE"`: the
         looser check would re-catch the very EXPIRED-with-observations
         case row 7 just cleared and send it back to REALITY_NOT_OBSERVED)
      9. otherwise (a receipt for the right commit, no
         contradiction, a readable source, and a window that
         is COMPLETE, or an EXPIRED one that DID collect
         observations)                                       -> VERIFIED_IN_REALITY

    Returns a dict: `realityState` (one of `_REALITY_STATE_WORDS`),
    `reasons` (a non-empty list of plain sentences) and `basis` (always
    `"derived"`, the same word every other reducer in this module returns).

    Raises `ValueError` naming the value on an unknown `windowState` or
    `observationStatus`, mirroring `reduce_readiness`'s own refusal of an
    unknown verdict word: a value this function has not been taught is a
    programming error in the caller, never read as the nearest-sounding
    known one.
    """
    window_state = facts.get("windowState")
    if window_state not in _WINDOW_STATES:
        raise ValueError("reduce_verified_reality got windowState=%r; the only window states "
                         "it knows are %s" % (window_state, ", ".join(_WINDOW_STATES)))
    observation_status = facts.get("observationStatus")
    if observation_status not in _OBSERVATION_STATUSES:
        raise ValueError("reduce_verified_reality got observationStatus=%r; the only "
                         "observation statuses it knows are %s"
                         % (observation_status, ", ".join(_OBSERVATION_STATUSES)))

    released_commit = facts.get("releasedCommit")
    expected_commit = facts.get("expectedCommit")
    if _hollow(released_commit):
        return {"realityState": "REALITY_NOT_OBSERVED",
                "reasons": ["no release receipt names a commit here; nothing has shipped "
                           "that this reducer can call verified or contradicted yet"],
                "basis": "derived"}
    if released_commit != expected_commit:
        return {"realityState": "REALITY_NOT_OBSERVED",
                "reasons": ["the release receipt names %r and this reducer is judging "
                           "reality for %r; a receipt for another commit is not evidence "
                           "about this one" % (released_commit, expected_commit)],
                "basis": "derived"}

    already = ", after having been verified once" if facts.get("previouslyVerified") else ""
    if facts.get("rollback"):
        return {"realityState": "CONTRADICTED_BY_REALITY",
                "reasons": ["this release was rolled back%s" % already],
                "basis": "derived"}
    if facts.get("incident"):
        return {"realityState": "CONTRADICTED_BY_REALITY",
                "reasons": ["an incident was attributed to this release%s" % already],
                "basis": "derived"}
    if facts.get("acceptanceBehaviorFailed"):
        return {"realityState": "CONTRADICTED_BY_REALITY",
                "reasons": ["a predeclared acceptance behavior failed against this "
                           "release%s" % already],
                "basis": "derived"}

    if observation_status == "NO-DATA":
        return {"realityState": "REALITY_NOT_OBSERVED",
                "reasons": ["the observation source is NO-DATA (unavailable or "
                           "unreadable); an absent source is not evidence, so this stays "
                           "unobserved rather than defaulting to verified"],
                "basis": "derived"}

    if window_state == "EXPIRED" and not facts.get("observationsRecorded"):
        return {"realityState": "REALITY_NOT_OBSERVED",
                "reasons": ["the observation window expired with no observations recorded "
                           "in it; an expired window is not itself evidence and is never "
                           "auto-verified by running out"],
                "basis": "derived"}

    if window_state == "OPEN":
        # The only window state that reaches here besides "OPEN" is
        # "EXPIRED" WITH observations recorded (an "EXPIRED" with none was
        # already returned by row 7, above): that case falls through to
        # VERIFIED_IN_REALITY below, per row 9 of this function's own
        # docstring table, rather than being caught by a bare `!= "COMPLETE"`
        # that would re-catch the very case row 7 just cleared.
        return {"realityState": "REALITY_NOT_OBSERVED",
                "reasons": ["the observation window is still open; this release is not "
                           "verified yet"],
                "basis": "derived"}

    return {"realityState": "VERIFIED_IN_REALITY",
            "reasons": ["the release receipt matches the expected commit, the observation "
                       "source was read, the window completed, and nothing contradicted "
                       "it"],
            "basis": "derived"}


#: Byte-identical to `contracts.VALUE_REALIZATION_STATES`, spelled locally
#: for the same reason `_READINESS_VERDICT_WORDS` and `_REALITY_STATE_WORDS`
#: above are: this module takes no dependency on `contracts.py`. Held in
#: agreement by `tools/test_sbe_reality.py`. Kept PUBLIC under this same
#: name (no leading underscore) for backward compatibility: this vocabulary
#: lived here first and moved to `contracts.py` as the single source in a
#: later lane, so any earlier reader of `lifecycle.VALUE_REALIZATION_STATES`
#: keeps working unchanged.
VALUE_REALIZATION_STATES = ("VALUE_REALIZED", "VALUE_NOT_REALIZED",
                            "VALUE_NOT_YET_MEASURABLE", "VALUE_TARGET_MOVED")

#: Byte-identical to `contracts.MEASURE_MATURITY_STATES`, spelled locally
#: for the same reason above. `facts["measureMaturity"]`'s closed
#: vocabulary: has the business measure had enough time or volume behind it
#: to mean anything yet.
_MEASURE_MATURITY_STATES = ("MATURE", "NOT_YET_MATURE")


def reduce_value_realization(facts):
    """The one reducer for `VALUE_REALIZATION_STATES` (above): did this
    release actually realize the business value it was released for.

    Reads NOTHING from disk (see the module docstring's own law). `facts`:

      targetEditedAfterRelease  bool: the value target was edited after the
                                release it is supposed to measure
      movedTargetDetail         str, read when `targetEditedAfterRelease`
                                is true: which target moved and how, so row
                                1 below names it rather than pointing at a
                                bare boolean
      measureMaturity            `"MATURE"` or `"NOT_YET_MATURE"`
                                (`_MEASURE_MATURITY_STATES`): has the
                                business measure had time to mean anything
      businessMeasureMet         `True`/`False` when `measureMaturity` is
                                `"MATURE"` (a bool, never a truthy stand-in,
                                the same rule `reduce_readiness` holds
                                `noDataPermitted` to); ignored otherwise
      technicalSuccess           bool, optional: did the release itself
                                work (typically `reduce_verified_reality`'s
                                own VERIFIED_IN_REALITY, carried through as
                                a fact rather than re-derived here). Read
                                ONLY into the reason text on row 3 below; it
                                never overrides a missed business measure.

    A DETERMINISTIC TABLE, read top to bottom:

      1. `targetEditedAfterRelease` is true       -> VALUE_TARGET_MOVED
         (checked FIRST: a target moved after the fact makes every measure
         below it a comparison against a baseline nobody released against;
         flagged by name rather than silently measured against the new one)
      2. `measureMaturity` is `"NOT_YET_MATURE"`   -> VALUE_NOT_YET_MEASURABLE
         (a measure with no time behind it yet is not realized BY DEFAULT;
         reading "not yet mature" as "not realized" would punish a release
         for a clock that has not run out, and reading it as "realized"
         would claim a measurement nobody took)
      3. `businessMeasureMet` is not `True`         -> VALUE_NOT_REALIZED
         (the row Codex case 8 names: a TECHNICAL SUCCESS with a MISSED
         BUSINESS MEASURE lands here, exactly the same as a technical
         failure with one, because the business measure is what this state
         is about; `technicalSuccess` never overrides a missed measure)
      4. otherwise (`businessMeasureMet` is `True`) -> VALUE_REALIZED

    Returns a dict: `valueState` (one of `VALUE_REALIZATION_STATES`),
    `reasons` and `basis` (`"derived"`), the same shape every reducer in
    this module returns.

    Raises `ValueError` naming the value on an unknown `measureMaturity`,
    and on a `businessMeasureMet` that is answered for (maturity is
    `"MATURE"`) but is not exactly `True` or `False`: the same refusal
    `reduce_readiness` gives `noDataPermitted`, because a string or a
    number read by bare truthiness could call `"false"` realized.
    """
    maturity = facts.get("measureMaturity")
    if maturity not in _MEASURE_MATURITY_STATES:
        raise ValueError("reduce_value_realization got measureMaturity=%r; the only states "
                         "it knows are %s" % (maturity, ", ".join(_MEASURE_MATURITY_STATES)))

    if facts.get("targetEditedAfterRelease"):
        detail = facts.get("movedTargetDetail")
        named = detail if not _hollow(detail) else "no detail was given naming what moved"
        return {"valueState": "VALUE_TARGET_MOVED",
                "reasons": ["the value target moved after release: %s" % named],
                "basis": "derived"}

    if maturity == "NOT_YET_MATURE":
        return {"valueState": "VALUE_NOT_YET_MEASURABLE",
                "reasons": ["the business measure is not yet mature enough to read; this "
                           "is a not-yet state, never realized by default"],
                "basis": "derived"}

    met = facts.get("businessMeasureMet")
    if met is not True and met is not False:
        raise ValueError(
            "reduce_value_realization got businessMeasureMet=%r (a %s) while measureMaturity "
            "is MATURE; this must be exactly True or False, never a truthy stand-in for "
            "either" % (met, type(met).__name__))

    if met is not True:
        despite = " despite technical success" if facts.get("technicalSuccess") else ""
        return {"valueState": "VALUE_NOT_REALIZED",
                "reasons": ["the business measure was not met%s" % despite],
                "basis": "derived"}

    return {"valueState": "VALUE_REALIZED",
            "reasons": ["the business measure was met on a mature reading"],
            "basis": "derived"}
