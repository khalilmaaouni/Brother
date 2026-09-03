#!/bin/sh
# The real logic lives in tools/install.py, a tested Python module like every
# other tool in this estate; this file only carries the invocation forward so
# `sh install.sh ...` keeps working unchanged. See tools/install.py's own
# module docstring for the flags, refusals, and exit codes.
exec python3 "$(dirname "$0")/tools/install.py" "$@"
