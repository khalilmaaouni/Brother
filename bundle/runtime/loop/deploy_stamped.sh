#!/bin/bash
# Frozen, local deploy. No restart, branch operation, remote or plugin install.
# BROTHER_DEPLOY_SOURCE is a checkout root (default: this checkout).
# BROTHER_DEPLOY_TARGET is the bin directory (default: $HOME/.claude/bin).
# The three docs/plan configs go beside that bin directory, as before.
# Usage: deploy_stamped.sh [deploy]
#        deploy_stamped.sh restore <rollback-directory>
# Every deploy prints its rollback path and an exact restore command. Keep the
# brother-deploys directory: it holds both active tools and rollback artifacts.
# An existing plain target is migrated while frozen; later symlink switches
# use one rename. HOLD is allowed. stop_loop.sh --dry must report no live loop.
set -eu
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
exec "${BROTHER_DEPLOY_PYTHON:-python3}" -B "$HERE/deploy_stamped.py" "$@"
