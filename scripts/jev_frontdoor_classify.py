#!/usr/bin/env python3
"""A1.03 (J014) and A2.03 (J119), wave-1/wave-2 Jev seams: PostToolUse
(matcher "Skill") half of the front-door skill-selection seam. See
jev_frontdoor_sidecar.py's module docstring for why this is two hooks
rather than one call site.

LOCAL GROUND TRUTH IS A KEYWORD HEURISTIC ON THE INVOKED SKILL NAME,
deliberately no semantic re-classification of the request text here --
the same "no semantic check, the second opinion lives in the ledger"
pattern as claim_supported_by_source() (J030) and classify() (J025):
inventing a smarter local answer here would just be a second, undeclared
router. J014's own fixed catalogue (data/jev-registry.json) is
intake/review/status/handover/none-of-the-above/unknown; _bucket() below
maps whatever skill name was actually invoked onto that catalogue by
substring match, falling back to "unknown" (the registry's own
fail_direction: unknown routes to the default catalogue entry, never a
guessed skill).

J119 (A2.03, founder-approved 2026-09-19, docs/plan/INTAKE-2026-09-19-
jev-competitive-edge.md, option 3 of 3) rides the same hook, right after
J014: given the request text and the bucket J014 just chose, is dispatch
ready to proceed as-is or does it need exactly one clarifying slot? Local
ground truth is ALWAYS "PROCEED_BEST" unless _looks_destructive() matches
first, in which case it is "NEEDS_HUMAN" -- a DETERMINISTIC check that
runs BEFORE Jev is even asked and whose result Jev can never override
(the registry entry's own fail_direction), matching the registry entry's
own required "the destructive case is a deterministic veto, not a model
judgment" contract, and mirroring the same pre-Jev-veto shape A2.01's own
overlap threshold uses.

Never blocks (a PostToolUse hook cannot deny an already-run call anyway),
never raises past this file, always exits 0: same fail-open discipline as
jev_frontdoor_sidecar.py and ~/.claude/hooks/vault_recall_hook.py. The
sidecar file is removed after one read (one prompt, one classification),
so a session that never triggers this hook again never accumulates state.

Register in .claude/settings.json under hooks.PostToolUse with matcher "Skill"."""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SIDECAR_DIR = os.path.join(tempfile.gettempdir(), "brother-jev-j014-sidecar")

_BUCKETS = (
    ("status", "status"), ("progress", "status"),
    ("review", "review"), ("verify", "review"), ("assurance", "review"),
    ("sbe", "review"), ("brothersbe", "review"),
    ("handover", "handover"), ("handback", "handover"), ("close", "handover"),
)


def _bucket(skill_name):
    name = str(skill_name or "").lower()
    if name == "brother":
        return "intake"  # the registry's own "moment": a bare /brother-style request
    for needle, bucket in _BUCKETS:
        if needle in name:
            return bucket
    if name:
        return "none-of-the-above"
    return "unknown"


#: The deterministic veto J119's registry entry requires: any of these
#: patterns force NEEDS_HUMAN as the local ground truth BEFORE Jev is
#: asked, and Jev's own answer is discarded regardless (C1, same as every
#: other wave-1/wave-2 seam). Deliberately narrow and literal, matching
#: this estate's own safety-section list of irreversible commands rather
#: than a broad guess: false negatives here still get a human via NEEDS_
#: HUMAN whenever _bucket() itself already reads "unknown" or "none-of-
#: the-above", this list only catches the case a clean bucket would
#: otherwise wave straight into PROCEED_BEST.
_DESTRUCTIVE_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\brm\s+-rf\b", r"\bdrop\s+table\b", r"\bdrop\s+database\b",
    r"\bforce[-\s]?push\b", r"\bgit\s+push\s+--force\b",
    r"\bgit\s+reset\s+--hard\b", r"\bdelete\s+from\b",
    r"\btruncate\s+table\b", r"--no-verify\b",
))


def _looks_destructive(request_text):
    text = str(request_text or "")
    return any(p.search(text) for p in _DESTRUCTIVE_PATTERNS)


def _session_id(payload):
    return (payload.get("session_id")
            or os.environ.get("CLAUDE_SESSION_ID")
            or "nosession")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Skill":
        return 0
    skill_name = (payload.get("tool_input") or {}).get("skill")
    current_answer = _bucket(skill_name)

    session_id = _session_id(payload)
    sidecar_path = os.path.join(SIDECAR_DIR, session_id + ".json")
    try:
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            request_text = json.load(fh).get("prompt", "")
    except (OSError, ValueError):
        return 0  # no cached prompt for this session: nothing to consult about
    finally:
        try:
            os.remove(sidecar_path)  # one prompt, one classification, never re-consulted
        except OSError:
            pass

    if not request_text:
        return 0

    try:
        import jev_checks
        import jev_seam
    except ImportError:
        return 0

    #: The registry entry's own fixed catalogue (data/jev-registry.json,
    #: J014.question.options), copied verbatim rather than re-derived:
    #: check_front_door_skill()'s own docstring says available_skills is
    #: carried in `state` only for Jev to read as context, never used to
    #: change what options it may answer with (rule 4) -- only the
    #: registry entry does that.
    available_skills = ["intake", "review", "status", "handover",
                         "none-of-the-above", "unknown"]
    try:
        jev_checks.check_front_door_skill(
            request_text, available_skills, current_answer,
            seams_config=jev_seam.load_seams_config(),
            registry=jev_seam.load_registry(),
            ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
        )  # C1: return value intentionally discarded, shadow-only by contract
    except Exception:
        pass  # sbe: allow-silent this seam is advisory only, never worth a hook failure

    # J119 (A2.03): rides the same request text and the bucket J014 just
    # chose. The destructive-pattern veto runs BEFORE Jev is even asked,
    # deterministically, and its result is what current_answer carries
    # into the seam -- Jev's own answer is discarded regardless (C1), so
    # a live/act mode still can never turn a real destructive request
    # into PROCEED_BEST.
    readiness_answer = "NEEDS_HUMAN" if _looks_destructive(request_text) else "PROCEED_BEST"
    try:
        jev_checks.check_front_door_readiness(
            request_text, current_answer, readiness_answer,
            seams_config=jev_seam.load_seams_config(),
            registry=jev_seam.load_registry(),
            ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
        )  # C1: return value intentionally discarded, shadow-only by contract
    except Exception:
        pass  # sbe: allow-silent this seam is advisory only, never worth a hook failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
