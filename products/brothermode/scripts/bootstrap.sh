#!/bin/sh
# bootstrap.sh: the one command a new founder runs after cloning.
#
#   sh scripts/bootstrap.sh [--upgrade] [any other install.py flag]
#
# It does three things and nothing else:
#
#   1. Finds a Python interpreter that is actually new enough (3.9 or later,
#      the floor this project supports) and says which one it picked.
#   2. Hands every argument it was given straight to scripts/install.py.
#   3. If there is no install.py in this checkout, it says so plainly and
#      prints the manual path instead of pretending it installed something.
#
# Step 3 is the honest part and it is worth stating why it exists. The
# installer is a separate piece of work with its own loop; this wrapper
# landed first, with the packaging change. A wrapper that swallowed the
# missing installer and exited 0 would tell a new user they were set up when
# nothing had been written. So the no-installer case exits 3, not 0.
#
# POSIX sh on purpose: this is the first thing that runs on a machine, before
# anything is installed, so it may not assume bash, python on PATH, or any
# package manager.

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# --- find a usable interpreter -------------------------------------------
# Try the usual names in order of preference. `command -v` rather than
# `which`: `which` is not POSIX and lies about shell builtins on some
# systems. Version check is done by the interpreter itself rather than by
# parsing `python3 -V` output, because that output has changed format before.
PY=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            PY="$candidate"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "bootstrap: no Python 3.9 or later found on PATH." >&2
    echo "bootstrap: BrotherMode needs Python 3.9 or later and nothing else." >&2
    echo "bootstrap: install Python, then run this script again." >&2
    exit 2
fi

echo "bootstrap: using $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
echo "bootstrap: repository root $REPO_ROOT"

# --- hand off to the installer -------------------------------------------
INSTALLER="$REPO_ROOT/scripts/install.py"

if [ -f "$INSTALLER" ]; then
    echo "bootstrap: running scripts/install.py"
    exec "$PY" "$INSTALLER" "$@"
fi

# No installer in this checkout. Say exactly that, and give the manual path.
cat >&2 <<EOF
bootstrap: NOT INSTALLED. There is no scripts/install.py in this checkout.

This wrapper does not install anything by itself, and it will not report
success it did not earn. The guided installer is a separate piece of work.
Until it lands, install by hand:

  1. Verify the checkout matches its published manifest:
       sh "$REPO_ROOT/scripts/verify-install.sh"

  2. Copy or symlink this directory to where your runtime looks for skills.
     For Claude Code that is:
       ~/.claude/skills/brothermode

  3. Wire the hooks yourself, following docs/HOOKS.md. Hooks are wired by
     explicit command path, so they point at the tools inside the directory
     from step 2.

  4. Confirm it works:
       $PY "$REPO_ROOT/tools/bm_store.py" --help

Alternatively, install the Python commands only (no skill files, no hooks):
see docs/PACKAGING.md for the pipx, uv, and pip routes.
EOF
exit 3
