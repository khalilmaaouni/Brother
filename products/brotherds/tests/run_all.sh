#!/bin/sh
# BrotherDS: every check the product ships, run from the repository root.
# Each check's own exit code is read before anything else touches $?; the
# first failure is named and the runner exits 1. Graders hold reference values
# computed by hand, never by the code under test.
cd "$(dirname "$0")/../../.." || exit 2
py=${BROTHERDS_PY:-python3}
fail=0
run() {
    name=$1; shift
    out=$("$@" 2>&1); code=$?
    if [ $code -eq 0 ]; then
        echo "PASS  $name"
    else
        echo "FAIL  $name (exit $code)"
        echo "$out" | tail -15
        fail=1
    fi
}
run selftest            $py products/brotherds/bds.py selftest
for g in products/brotherds/tests/grade_*.py; do
    [ -f "$g" ] || continue
    case "$(basename "$g")" in
        grade_installed_layout.py)
            # Its eight layout cases are included in the full portability
            # suite below. Retain its additional source-path guard here.
            run installed-layout-source $py -c "from pathlib import Path; assert '/Users/' not in Path('products/brotherds/vault_bridge.py').read_text(encoding='utf-8')"
            ;;
        *) run "$(basename "$g" .py)" $py "$g" ;;
    esac
done
# The hero demo compares its whole output with a per-interpreter expected file;
# run it under both interpreters the release meets (with and without duckdb).
run hero-demo          $py products/brotherds/examples/hero_demo.py
[ -x /usr/bin/python3 ] && run hero-demo-system /usr/bin/python3 products/brotherds/examples/hero_demo.py
# Standalone suites all run, including newly added test files. The audit
# journey is already executed in full by its grader, including isolation.
for t in products/brotherds/tests/test_*.py; do
    [ -f "$t" ] || continue
    case "$(basename "$t")" in
        test_audit_journey.py)
            [ -f products/brotherds/tests/grade_audit_journey.py ] && continue
            ;;
    esac
    run "$(basename "$t" .py)" $py "$t"
done
exit $fail
