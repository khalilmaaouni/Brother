#!/usr/bin/env python3
"""TOKEN-01 of the 1.0.20 control plane: route each unit's work to a LANE.
Extended by JEV-06 (absorbs TOKEN-03) with a decision lane for Jev and a
default adversarial reviewer.

MEASURED CAUSE. On 2026-09-18, 44 Claude subagents reported 8,687,883 tokens,
99.4 percent of all subagent spend, while DeepSeek and Muse did two small
jobs each, and the account hit its limit. Nothing decided WHICH lane a unit's
drafting, review and verification belonged to; every unit defaulted to a
Claude builder. The fix at the source is that decision, made once, here.

THE ROLE MAP (founder law 2026-09-08, and his 2026-09-18 order to delegate
more to DeepSeek, Muse and Codex): cheap lanes draft, a second model reviews,
Codex drafts documentation and advises, Claude orchestrates and verifies, and
a deterministic check decides PASS. This module never decides PASS.

THE PRIVACY BOUNDARY IS A HARD INPUT, NOT A PREFERENCE. DeepSeek and Muse
retain what they are sent, so only PUBLIC or GENERALIZED content may reach
them, and only after the private-terms scan passes (requires_scan=True). A
unit whose content class is missing or unknown is routed as PRIVATE: an
unknown input blocks, it never reads as the cheap case. Codex runs under the
founder's own account and is allowed on private content, as tonight's audit
used it. JEV-06 keeps this boundary for adversarial review too: Muse is
exactly as much an outside, retaining lane as DeepSeek is, so a review whose
content cannot leave the machine stays on Opus even with no plan-named gate.

WHAT IT RETURNS. A Lane namedtuple: draft, review, verify, requires_scan,
reason. Advice only, like orchestrator_router: it grants no authority.

route_lane() carries the base task-class routing (TOKEN-01) plus, for
task_class "review" only, TOKEN-03's default-to-Muse policy (see
_route_adversarial_review and _names_opus_gate). route_decision() is JEV-06's
separate decision lane: it wraps jev_cascade.route() (JEV-03) so a typed
screen routes to Jev only where the calibrated cascade clears ACT for that
family and risk class, and otherwise to the escalation lane the cascade
names. Every other route_lane() call and result is unchanged by this.
"""
import argparse
import collections
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchestrator_invariants  # noqa: E402
import jev_cascade  # noqa: E402  (JEV-03: the calibrated ACT/ESCALATE/NO-DATA cascade)

# J033 and J051, wave-2 Jev seams: optional, fail-open, same discipline
# as record_drift.py's J029 and loop_bridge.py's wave-1/wave-3 seams. The
# task_class/risk_class table and the word-boundary regex above both stay
# primary and are never gated by this import succeeding.
try:
    import jev_checks
except Exception:  # noqa: BLE001
    jev_checks = None

try:
    import jev_seam
except Exception:  # noqa: BLE001
    jev_seam = None

CONTENT_CLASSES = frozenset(("public", "generalized", "private"))
OUTSIDE_OK = frozenset(("public", "generalized"))

#: The kind of work each task class is. Checked complete against
#: orchestrator_invariants.TASK_CLASSES at import, so the two cannot drift.
_KIND = {
    "implementation": "draft", "test": "draft", "repair": "draft",
    "fault-injection": "draft", "packaging": "draft",
    "integration-prep": "draft", "benchmark": "draft",
    "documentation": "document",
    "planning": "judge", "architecture": "judge", "research": "judge",
    "review": "judge",
    "verification": "verify",
}
if frozenset(_KIND) != orchestrator_invariants.TASK_CLASSES:
    raise RuntimeError("lane_router's kind table has drifted from TASK_CLASSES: "
                       "missing %r, extra %r" % (
                           sorted(orchestrator_invariants.TASK_CLASSES - frozenset(_KIND)),
                           sorted(frozenset(_KIND) - orchestrator_invariants.TASK_CLASSES)))

#: Where the last observed Codex credit state is recorded. Codex credits are
#: not readable from this machine, so this file is a MEMORY of what was last
#: seen, written by whoever saw it, and it goes stale on purpose.
CODEX_STATE = os.path.expanduser("~/.claude/state/codex-credits.json")
CODEX_STALE_S = 24 * 3600

Lane = collections.namedtuple("Lane", "draft review verify requires_scan reason")

VERIFY = "deterministic-check+claude"


def codex_headroom(path=None, now=None):
    """'ok', 'exhausted' or None (unknown), from the last observed record.

    TOKEN-06: the standing rule is to route bounded passes to Codex AND to
    read its credit headroom first. Nothing on this machine exposes that
    headroom, so this reads a record of what was last seen and treats a
    missing, unreadable or stale record as UNKNOWN. Unknown is not 'ok':
    the router falls back rather than spending a pass to discover it, except
    for documentation, which the founder's 2026-09-07 law assigns to Codex
    whatever this says."""
    path = path or CODEX_STATE
    now = now if now is not None else time.time()
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        state = rec["state"]
        seen = float(rec["observed_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if state not in ("ok", "exhausted") or now - seen > CODEX_STALE_S or now < seen:
        return None
    return state


#: Word-boundary, not substring: a plain "opus" in checker.lower() misreads
#: "octopus" or "corpus" as an opus gate, and a plain "muse" misreads
#: "museum" or "amusement" as muse already being named. Found by Muse's own
#: adversarial review of this exact function (JEV-06 evidence,
#: docs/plan research draft), confirmed against real WBS checker strings.
_OPUS_WORD = re.compile(r"\bopus\b", re.IGNORECASE)
_MUSE_WORD = re.compile(r"\bmuse\b", re.IGNORECASE)


def _names_opus_gate(checker):
    """True only when a unit's own declared `checker` field names Opus AS
    ITS reviewer, with no Muse named in the same field.

    'opus-high' (ORCH-25, TOKEN-03) is a plan-named opus gate. 'muse then
    opus (orchestrator mutation)' (JEV-06's own checker field, right here
    in this run's WBS) is NOT: that field already names muse as the
    review lane, with opus kept for a separate orchestrator-run mutation
    check, not as the reviewer itself. A naive substring match on "opus"
    would misroute exactly this unit's own review to opus and skip muse
    (which is the one thing TOKEN-03 exists to stop), AND would misfire on
    an unrelated word like "octopus" or "corpus": both are guarded against
    with a word-boundary match, not a bare `in`.
    """
    if not isinstance(checker, str):
        return False
    return bool(_OPUS_WORD.search(checker)) and not _MUSE_WORD.search(checker)


def _route_adversarial_review(checker, outside, note):
    """TOKEN-03, absorbed into JEV-06: adversarial review defaults to
    Muse. Measured on real units tonight: an Opus checker cost about
    165000 tokens; a Muse adversarial pass cost about 14000 and found the
    destructive-dependents gap that no row covered, with findings
    confirmed 8 of 8 and 8 of 12. Opus stays the reviewer only for the
    gates the plan names for THIS unit (see _names_opus_gate), or when the
    content cannot leave the machine at all: the privacy boundary in this
    module's docstring is a hard input everywhere else in this file, and
    an adversarial reviewer is exactly as much an outside lane as a
    drafting one.

    Privacy is checked BEFORE the opus-gate: both conditions route to the
    same claude-opus lane, so this order only ever changes which reason
    string is recorded when a unit happens to be both, but it keeps this
    function consistent with every other lane below, where the
    outside/private read always gates first.
    """
    if not outside:
        return Lane("claude-opus", None, VERIFY, False,
                     "adversarial review defaults to muse, but this content cannot leave "
                     "the machine" + note)
    if _names_opus_gate(checker):
        return Lane("claude-opus", None, VERIFY, False,
                     "review checker %r names an opus gate for this unit" % (checker,) + note)
    return Lane("muse", None, VERIFY, True,
                "adversarial review defaults to muse (TOKEN-03: an opus checker cost about "
                "165000 tokens, a muse pass about 14000 and found the destructive-dependents "
                "gap no row covered)" + note)


def _opus_muse_ambiguous(checker):
    """True when the same word-boundary regex _names_opus_gate uses finds
    NEITHER opus nor muse in the checker field, or BOTH -- the two cases
    J051's own registry entry names as ambiguous. A checker naming muse
    alone, or opus alone, is unambiguous and never triggers this."""
    if not isinstance(checker, str):
        return True
    opus = bool(_OPUS_WORD.search(checker))
    muse = bool(_MUSE_WORD.search(checker))
    return opus == muse


def _consult_j051(lane, checker):
    """J051, wave-2 Jev seam: fires only when the checker field is
    ambiguous per _opus_muse_ambiguous, for the calibration ledger only.
    C1: `lane` -- the real routed Lane from _route_adversarial_review --
    is always returned unchanged, whatever mode says."""
    if (jev_checks is not None and jev_seam is not None
            and _opus_muse_ambiguous(checker)):
        try:
            jev_checks.check_adversarial_review_tier(
                str(checker), lane.draft,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
            )  # C1: return value intentionally discarded, shadow-only by contract
        except Exception:  # noqa: BLE001  # sbe: allow-silent this seam is advisory only, never worth breaking real review routing
            pass
    return lane


#: jev_cascade's ACT outcome routes a decision to this lane. See
#: route_decision().
DECISION_LANE = "jev"

#: Reused, never retyped (see the module docstring on _KIND): the action
#: name this module returns when jev_cascade's two-rung escalation ladder
#: is already exhausted and nobody automated can decide further. Checked
#: at import against orchestrator_invariants.ACTIONS so a future rename of
#: that vocabulary breaks this module loudly instead of silently drifting.
NEEDS_HUMAN = "QUEUE-HUMAN"
if NEEDS_HUMAN not in orchestrator_invariants.ACTIONS:
    raise RuntimeError("lane_router expects %r in orchestrator_invariants.ACTIONS" % (NEEDS_HUMAN,))


def route_decision(decision, risk_class, calibration):
    """Where one Jev-shaped decision goes: JEV-06's decision lane.

    Calls jev_cascade.route() (JEV-03), the one place ACT, ESCALATE or
    NO-DATA is decided for a typed screen, and turns its outcome into a
    Lane so a caller reads the same shape route_lane() already returns.

      ACT       draft=DECISION_LANE ("jev"): the cascade's own calibrated
                confidence gate cleared for this family, qtype and risk
                class. requires_scan is True because Jev is an outside
                model; jev_decide.py runs its own content gate before
                anything is actually sent, so this flag is advisory here,
                the same as every other requires_scan value in this
                module (see the module docstring: "Advice only... grants
                no authority").
      ESCALATE  draft=the next lane jev_cascade names ("muse" then "opus",
                its own CASCADE_LANES, in that order). requires_scan is
                True only for muse: opus is Anthropic's own frontier tier
                and never leaves the machine.
      NO-DATA   draft=NEEDS_HUMAN: the escalation ladder is already
                exhausted (a decision already reviewed by both muse and
                opus that still does not clear the bar). Never a pass,
                per the estate's "NO-DATA is never a pass" rule: this Lane
                names no automated lane at all, only that a human is
                needed next.

    Raises exactly when jev_cascade.route() raises: a structurally
    invalid decision object (missing id, family, a qtype outside
    jev_calibration.QTYPES, and so on). An unrecognised risk_class or
    family never raises here either, the same as in jev_cascade: both
    escalate instead, per that module's own ESCALATE-ON-UNKNOWN rule.
    """
    result = jev_cascade.route(decision, risk_class, calibration)
    outcome = result["outcome"]
    if outcome == "ACT":
        return Lane(DECISION_LANE, None, VERIFY, True,
                     "cascade cleared for family %r: confidence %r meets the calibrated "
                     "threshold %r" % (decision.get("family"), decision.get("confidence"),
                                        result["threshold"]))
    if outcome == "ESCALATE":
        lane_name = result["next_lane"]
        # jev_cascade.CASCADE_LANES is the closed set this name can ever
        # be; reused, never retyped (see the module docstring on _KIND).
        # An unrecognised lane name here is never read as "internal,
        # skip the scan": it raises, the same "unknown never reads as the
        # safe case" rule this whole module already enforces everywhere
        # else. Found by Muse's own adversarial review of this function.
        if lane_name not in jev_cascade.CASCADE_LANES:
            raise ValueError(
                "jev_cascade escalated to an unrecognised lane: %r (expected one of %r)"
                % (lane_name, jev_cascade.CASCADE_LANES))
        return Lane(lane_name, None, VERIFY, lane_name == "muse",
                     "cascade escalated to %s: %s" % (lane_name, result["reason"]))
    if outcome == "NO-DATA":
        return Lane(NEEDS_HUMAN, None, VERIFY, False,
                     "cascade escalation ladder exhausted: %s" % result["reason"])
    raise ValueError("jev_cascade returned an unrecognised outcome: %r" % (outcome,))


def route_lane(task_class, risk_class="medium", content=None, codex=None, checker=None):
    lane = _route_lane_deterministic(task_class, risk_class, content, codex, checker)
    return _consult_j033(lane, task_class, risk_class, content)


def _consult_j033(lane, task_class, risk_class, content):
    """J033, wave-2 Jev seam: second-opinions the task_class/risk_class
    table above only for the calibration ledger. C1: `lane` -- the
    table's own real routed Lane -- is always returned unchanged,
    whatever mode says; the registry's own fail_direction is explicit
    that unknown never silently reroutes a unit."""
    if jev_checks is not None and jev_seam is not None:
        try:
            state_text = ("task_class=%s risk_class=%s content=%s"
                           % (task_class, risk_class, content))
            jev_checks.check_lane_routing(
                state_text, lane.draft,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
            )  # C1: return value intentionally discarded, shadow-only by contract
        except Exception:  # noqa: BLE001  # sbe: allow-silent this seam is advisory only, never worth breaking real dispatch routing
            pass
    return lane


def _route_lane_deterministic(task_class, risk_class="medium", content=None, codex=None, checker=None):
    if task_class not in _KIND:
        raise ValueError("unknown task class: %r" % (task_class,))
    declared = content in CONTENT_CLASSES
    content = content if declared else "private"
    outside = content in OUTSIDE_OK
    note = "" if declared else " (content class undeclared, routed as private)"
    kind = _KIND[task_class]
    critical = risk_class == "critical"

    if kind == "verify":
        return Lane(None, None, VERIFY, False,
                    "verification is a test run plus Claude's read; never an outside lane" + note)
    if kind == "document":
        # The founder's law names Codex for documentation, so it stays the
        # drafter even when credits are unknown; the caller is told to check.
        credit_note = "" if codex == "ok" else "; Codex credit headroom is %s, check before spending the pass" % (
            "exhausted" if codex == "exhausted" else "unknown")
        return Lane("codex", "muse" if outside else "claude-opus", VERIFY, outside,
                    "documentation is drafted by Codex (founder law 2026-09-07)" + credit_note + note)
    if kind == "judge":
        if task_class == "review":
            return _consult_j051(_route_adversarial_review(checker, outside, note), checker)
        # A second opinion is worth having, not worth a failed pass: Codex
        # takes it only on observed headroom, otherwise the frontier tier does.
        second = "codex" if codex == "ok" else "claude-opus"
        why = ("judging and planning stay on the frontier tier; Codex is the second opinion"
               if second == "codex" else
               "judging stays on the frontier tier; the second opinion falls back from Codex "
               "because its credit headroom is %s" % ("exhausted" if codex == "exhausted" else "unknown"))
        return Lane("claude-frontier", second, VERIFY, False, why + note)
    # draft work
    if outside and not critical:
        return Lane("deepseek", "muse", VERIFY, True,
                    "public or generalized draft work goes to the cheap lanes; "
                    "Claude only verifies" + note)
    return Lane("claude-sonnet", "codex" if (critical and codex == "ok") else "claude-opus", VERIFY, False,
                ("critical risk keeps drafting on Claude" if critical and outside
                 else "private content cannot leave for a retaining model") + note)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lane mix for a plan's units.")
    ap.add_argument("plan")
    a = ap.parse_args(argv)
    try:
        with open(a.plan, encoding="utf-8") as fh:
            units = json.load(fh)["units"]
    except (OSError, ValueError, KeyError) as exc:
        print("lane-router: NO-DATA, cannot read %s (%s)" % (a.plan, exc))
        return 2
    credits = codex_headroom()
    mix = collections.Counter()
    undeclared = 0
    for u in units:
        lane = route_lane(u.get("task_class"), u.get("risk_class", "medium"),
                          u.get("content_class"), codex=credits, checker=u.get("checker"))
        mix[lane.draft or "none"] += 1
        undeclared += u.get("content_class") not in CONTENT_CLASSES
    print("lane-router: %d unit(s); draft lanes %s; %d with no declared content class "
          "(routed as private); Codex credit headroom %s"
          % (len(units), dict(mix.most_common()), undeclared, credits or "unknown"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
