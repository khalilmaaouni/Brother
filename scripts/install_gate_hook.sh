#!/bin/sh
# install_gate_hook: make the gate fire by itself.
#
# The founder chose "Gates at the boundaries" on 2026-08-29 from the decision
# screen at docs/decisions/watchdog-design-2026-08-29.html. The finding behind
# that choice: this estate does not lack watchdogs, it has about twenty things
# shaped like one. What it lacked was a TRIGGER. Every check waited for a person
# to type a command, so a person had to already suspect something.
#
# This installs one: .git/hooks/pre-push, two lines calling the version
# controlled body in scripts/pre_push_hook.sh.
#
# IT NEVER CLOBBERS SILENTLY. An existing unrelated hook is backed up and named,
# because a hook somebody else installed is somebody else's work.
#
# STATED LIMIT, not a defect to discover later: scripts/pre_push_gate.py resolves
# its drift check against the repository it lives in, so this is installed HERE,
# in the repository that carries the gate. Installing it elsewhere gets you the
# NO-DATA path in the hook body, loudly, rather than a false sense of cover.
# Making the gate repository agnostic is a separate piece of work and it is not
# pretended to be done.
set -u
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "NO-DATA: not inside a git repository, so nothing was installed" >&2; exit 2; }
HOOK="$ROOT/.git/hooks/pre-push"
BODY="scripts/pre_push_hook.sh"
MARK="# brother-gate"          # how we recognise our own hook on a re-run

action="${1:-install}"

case "$action" in
  --check|check)
    if [ ! -f "$HOOK" ]; then echo "NOT INSTALLED: $HOOK does not exist"; exit 1; fi
    if grep -q "$MARK" "$HOOK" 2>/dev/null; then
      echo "INSTALLED: $HOOK calls $BODY"; exit 0
    fi
    echo "FOREIGN HOOK: $HOOK exists but is not ours, and was left alone"; exit 1
    ;;
  --uninstall|uninstall)
    if [ ! -f "$HOOK" ]; then echo "nothing to remove"; exit 0; fi
    if ! grep -q "$MARK" "$HOOK" 2>/dev/null; then
      echo "REFUSED: $HOOK is not ours, so it was left alone" >&2; exit 1; fi
    rm -f "$HOOK"
    if [ -f "$HOOK.brother-backup" ]; then
      mv "$HOOK.brother-backup" "$HOOK"; echo "removed, and the previous hook was restored"
    else
      echo "removed"
    fi
    exit 0
    ;;
  install|--install) : ;;
  *) echo "usage: install_gate_hook.sh [install|--check|--uninstall]" >&2; exit 2 ;;
esac

if [ ! -f "$ROOT/$BODY" ]; then
  echo "NO-DATA: $ROOT/$BODY is missing, so nothing was installed" >&2; exit 2; fi

if [ -f "$HOOK" ] && ! grep -q "$MARK" "$HOOK" 2>/dev/null; then
  mv "$HOOK" "$HOOK.brother-backup"
  echo "an existing pre-push hook was found and MOVED to $HOOK.brother-backup"
fi

mkdir -p "$ROOT/.git/hooks"
cat > "$HOOK" <<HOOKEOF
#!/bin/sh
$MARK installed by scripts/install_gate_hook.sh. Remove with --uninstall.
exec sh "\$(git rev-parse --show-toplevel)/$BODY" "\$@"
HOOKEOF
chmod +x "$HOOK"
echo "installed: $HOOK now runs $BODY before every push"
echo "  check:     sh scripts/install_gate_hook.sh --check"
echo "  remove:    sh scripts/install_gate_hook.sh --uninstall"
echo "  bypass once, deliberately: git push --no-verify"
