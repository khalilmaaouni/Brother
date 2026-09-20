#!/bin/bash
# A1.02 (J064, wave-1 Jev seam): runs required_fast.sh exactly as it always
# has, then a purely advisory Jev triage pass over its captured output.
# The gate's own exit code is captured via PIPESTATUS BEFORE triage runs
# and is the only thing this script exits with -- required_fast.sh itself
# is never edited, so its own exit path carries zero new risk. Off by
# default: J064 in data/jev-seams.json (see jev_checks.py's own CLI,
# which this shells out to rather than re-deriving the state shape).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

bash "$here/required_fast.sh" "$@" 2>&1 | tee "$log"
gate_rc="${PIPESTATUS[0]}"

python3 "$here/jev_checks.py" j064 --lines-file "$log" --current-answer unknown \
  || echo "required_fast_with_triage: J064 triage did not run (advisory only, ignored; gate result above is authoritative)"

exit "$gate_rc"
