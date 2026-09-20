#!/usr/bin/env python3
"""A1.03 (J014, wave-1 Jev seam): UserPromptSubmit half of the front-door
skill-selection seam.

WHY TWO HOOKS: the front-door router (/brother, per ~/.claude/commands/
brother.md) is prose an LLM interprets, not a deterministic function --
there is no code call site to wire a consult() into directly (same reason
data/jev-registry.json's own anchor for J014 reads "new: no existing
surface"). The only place BOTH the original request text and the model's
eventual routing choice exist as concrete data is across two hook events:
this one (UserPromptSubmit) sees the request text but not yet the choice;
jev_frontdoor_classify.py (PostToolUse, matcher "Skill") sees the choice
but not the original prompt. This file is the join key's write side: it
caches the prompt, keyed by session id, so the PostToolUse hook can read
it back.

Never blocks, never raises past this file, always exits 0: a UserPromptSubmit
hook that failed here would fail the user's actual prompt, which is worse
than this seam simply not firing this one time (same fail-open discipline
as ~/.claude/hooks/vault_recall_hook.py).

Register in .claude/settings.json under hooks.UserPromptSubmit."""
import json
import os
import sys
import tempfile

SIDECAR_DIR = os.path.join(tempfile.gettempdir(), "brother-jev-j014-sidecar")

#: Kept small deliberately (rule 9: this hook fires on EVERY prompt, not
#: only ones that end up asking /brother anything): only the fields
#: jev_frontdoor_classify.py actually reads, nothing shaped like a full
#: transcript dump.
_MAX_PROMPT_CHARS = 4000


def _session_id(payload):
    return (payload.get("session_id")
            or os.environ.get("CLAUDE_SESSION_ID")
            or "nosession")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input fails OPEN, always (see module docstring)
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0
    session_id = _session_id(payload)
    try:
        os.makedirs(SIDECAR_DIR, exist_ok=True)
        path = os.path.join(SIDECAR_DIR, session_id + ".json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"prompt": prompt[:_MAX_PROMPT_CHARS]}, fh)
        os.replace(tmp_path, path)  # atomic: a reader never sees a half-written file
    except OSError:
        pass  # sbe: allow-silent this seam is advisory only, never worth failing a prompt over
    return 0


if __name__ == "__main__":
    sys.exit(main())
