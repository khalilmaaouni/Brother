#!/bin/sh
# Install the repeat guard into this machine's Claude configuration.
#
# It is a SEPARATE SCRIPT rather than something a session does inline on
# purpose: it writes into ~/.claude, which is configuration, and configuration
# changes are the founder's to approve. A session that edited them silently
# would be doing exactly what this estate refuses elsewhere.
#
# Idempotent. Running it twice registers nothing twice.
# No em or en dashes.
set -eu

HOOKS="$HOME/.claude/hooks"
STATE="$HOME/.claude/repeat-guard"
HERE=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$HOOKS" "$STATE"
cp "$HERE/repeat_guard.py" "$HOOKS/repeat_guard.py"
cp "$HERE/test_repeat_guard.py" "$HOOKS/test_repeat_guard.py"
chmod +x "$HOOKS/repeat_guard.py"

# The lesson file is APPENDED to, never overwritten: it is the accumulated
# record and clobbering it would delete exactly what this exists to keep.
if [ ! -f "$STATE/lessons.jsonl" ]; then
  cp "$HERE/lessons.seed.jsonl" "$STATE/lessons.jsonl"
  echo "repeat-guard: seeded $STATE/lessons.jsonl"
else
  echo "repeat-guard: kept the existing $STATE/lessons.jsonl (never overwritten)"
fi

python3 - "$HOME/.claude/settings.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
d = json.loads(p.read_text()) if p.exists() else {}
hooks = d.setdefault("hooks", {})
cmd = "python3 $HOME/.claude/hooks/repeat_guard.py"
matcher = "Bash|Edit|Write|NotebookEdit"
changed = []
for event in ("PreToolUse", "PostToolUse"):
    groups = hooks.setdefault(event, [])
    if any(cmd in json.dumps(g) for g in groups):
        continue
    groups.append({"matcher": matcher,
                   "hooks": [{"type": "command", "command": cmd}]})
    changed.append(event)
if changed:
    backup = p.with_suffix(".json.bak-repeat-guard")
    if p.exists():
        backup.write_text(p.read_text())
    p.write_text(json.dumps(d, indent=2) + "\n")
    print("repeat-guard: registered on " + ", ".join(changed))
else:
    print("repeat-guard: already registered, nothing changed")
PY

echo "repeat-guard: proving the control"
python3 "$HOOKS/test_repeat_guard.py" | tail -2
echo "repeat-guard: takes effect in the NEXT session; settings changes do not apply mid-session"
