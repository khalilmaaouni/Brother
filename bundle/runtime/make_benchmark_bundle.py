#!/usr/bin/env python3
"""Assembles a self-contained, reproducible bundle for the vault memory-ab /
graph-value benchmark: the benchmark script, its test file, the real outcome
fixtures the tests need, a tamper-evident manifest, and a one-command runner.

CONFIRMED DEFECT this closes, reproduced twice: a directory holding only
scripts/vault_benchmark_v2.py and its test file runs 27 tests with 1 failure,
D04ReadsTheRealRun.test_the_repo_own_results_file_reads_as_a_lift, because
that test reads benchmarks/memory-ab/results-*.json relative to the repo and
a bare-scripts bundle carries no benchmarks dir. An advertised score a clean
package cannot regenerate is an unverifiable claim.

The fix is structural, not a patched test: this builder ships the fixtures
laid out exactly where vault_benchmark_v2.py's own default glob expects them
(<bundle>/benchmarks/... next to <bundle>/scripts/...), so the same relative
path that resolves inside this repo also resolves inside the bundle.

No ambient timestamping: the manifest's generated_at is the caller-supplied
--stamp, never time.time() or datetime.now(), per the estate's reproducibility
discipline (same inputs, same manifest, byte for byte).

Python 3.9 floor, standard library only.
"""
import argparse
import glob
import hashlib
import json
import os
import platform
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SCRIPT_SRC = os.path.join(HERE, "vault_benchmark_v2.py")
TEST_SRC = os.path.join(HERE, "test_vault_benchmark_v2.py")
MEMORY_AB_GLOB = os.path.join(REPO, "benchmarks", "memory-ab", "results-*.json")
GRAPH_VALUE_GLOB = os.path.join(REPO, "benchmarks", "graph-value", "results-*.json")

# Written verbatim into the bundle so the bundle needs nothing outside itself
# (never this builder script) to check its own fixtures for tampering.
VERIFY_MANIFEST_SRC = '''#!/usr/bin/env python3
"""Re-hash every fixture named in benchmark_manifest.json against disk and
refuse (nonzero exit) on any mismatch or missing file. Stdlib only."""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    manifest_path = os.path.join(HERE, "benchmark_manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        print("verify_manifest: cannot read %s: %s" % (manifest_path, exc), file=sys.stderr)
        return 2
    problems = []
    for rel, want in sorted(manifest.get("files", {}).items()):
        p = os.path.join(HERE, rel)
        if not os.path.exists(p):
            problems.append("%s: MISSING" % rel)
            continue
        with open(p, "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        if got != want:
            problems.append("%s: sha256 mismatch (manifest %s, disk %s)" % (rel, want, got))
    if problems:
        print("TAMPER DETECTED, refusing to run:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 3
    print("manifest verified: %d file(s) match" % len(manifest.get("files", {})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

RUN_BENCHMARK_SRC = '''#!/usr/bin/env bash
# One-command runner for this bundle. Verifies fixture integrity first
# (tamper evidence), then runs the test suite, then the live benchmark
# against --vault. Never falls back to a prior claim: a missing artifact is
# NO-DATA from vault_benchmark_v2.py itself, not something this script hides.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

VAULT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    *) echo "run_benchmark.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
if [ -z "$VAULT" ]; then
  echo "usage: run_benchmark.sh --vault <path-to-vault>" >&2
  exit 2
fi

echo "[1/3] verifying benchmark_manifest.json against fixtures on disk"
python3 verify_manifest.py
MANIFEST_RC=$?
echo "manifest verify: exit $MANIFEST_RC"
if [ "$MANIFEST_RC" -ne 0 ]; then
  exit "$MANIFEST_RC"
fi

echo "[2/3] running the bundled test suite"
python3 -m unittest discover -s scripts -p "test_*.py" -v
TEST_RC=$?
echo "tests: exit $TEST_RC"

echo "[3/3] running the live vault benchmark (human text)"
python3 scripts/vault_benchmark_v2.py --vault "$VAULT" --tools "$HERE/scripts" \\
  --results-glob "$HERE/benchmarks/memory-ab/results-*.json" \\
  --graph-results-glob "$HERE/benchmarks/graph-value/results-*.json"
BENCH_RC=$?
echo "benchmark (text): exit $BENCH_RC"

echo "[3/3] running the live vault benchmark (json)"
python3 scripts/vault_benchmark_v2.py --vault "$VAULT" --tools "$HERE/scripts" \\
  --results-glob "$HERE/benchmarks/memory-ab/results-*.json" \\
  --graph-results-glob "$HERE/benchmarks/graph-value/results-*.json" --json
BENCH_JSON_RC=$?
echo "benchmark (json): exit $BENCH_JSON_RC"

if [ "$TEST_RC" -ne 0 ] || [ "$BENCH_RC" -ne 0 ] || [ "$BENCH_JSON_RC" -ne 0 ]; then
  exit 1
fi
exit 0
'''


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build(out_dir, stamp, memory_ab_glob=None, graph_value_glob=None):
    memory_ab_glob = memory_ab_glob or MEMORY_AB_GLOB
    graph_value_glob = graph_value_glob or GRAPH_VALUE_GLOB
    if not os.path.exists(SCRIPT_SRC) or not os.path.exists(TEST_SRC):
        raise SystemExit("make_benchmark_bundle: missing %s or %s" % (SCRIPT_SRC, TEST_SRC))
    memory_ab_files = sorted(glob.glob(memory_ab_glob))
    graph_value_files = sorted(glob.glob(graph_value_glob))
    if not memory_ab_files:
        raise SystemExit("make_benchmark_bundle: no memory-ab outcome artifact matched %r" % memory_ab_glob)
    if not graph_value_files:
        raise SystemExit("make_benchmark_bundle: no graph-value outcome artifact matched %r" % graph_value_glob)

    scripts_dir = os.path.join(out_dir, "scripts")
    memab_dir = os.path.join(out_dir, "benchmarks", "memory-ab")
    graphval_dir = os.path.join(out_dir, "benchmarks", "graph-value")
    for d in (scripts_dir, memab_dir, graphval_dir):
        os.makedirs(d, exist_ok=True)

    manifest_files = {}

    def _copy(src, dst_dir):
        dst = os.path.join(dst_dir, os.path.basename(src))
        shutil.copy2(src, dst)
        rel = os.path.relpath(dst, out_dir)
        manifest_files[rel] = _sha256(dst)
        return dst

    _copy(SCRIPT_SRC, scripts_dir)
    _copy(TEST_SRC, scripts_dir)
    for f in memory_ab_files:
        _copy(f, memab_dir)
    for f in graph_value_files:
        _copy(f, graphval_dir)

    verify_path = os.path.join(out_dir, "verify_manifest.py")
    with open(verify_path, "w", encoding="utf-8") as fh:
        fh.write(VERIFY_MANIFEST_SRC)
    os.chmod(verify_path, os.stat(verify_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    runner_path = os.path.join(out_dir, "run_benchmark.sh")
    with open(runner_path, "w", encoding="utf-8") as fh:
        fh.write(RUN_BENCHMARK_SRC)
    os.chmod(runner_path, os.stat(runner_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # The manifest itself is written last and is deliberately NOT in its own
    # file list: hashing a file to include its own hash is circular, so
    # tamper-evidence covers every shipped fixture and script, not the
    # manifest, which the runner instead trusts as the reference.
    manifest = {
        "generated_at": stamp,
        "python_floor": "3.9",
        # Actual build-machine runtime, distinct from the 3.9 floor above:
        # this is what actually ran, recorded so a mismatch against a
        # reader's own runtime is visible rather than assumed compatible.
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "files": manifest_files,
    }
    manifest_path = os.path.join(out_dir, "benchmark_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return manifest_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="destination bundle directory (created if absent)")
    ap.add_argument("--stamp", required=True,
                    help="generation timestamp recorded in the manifest, supplied by the caller "
                         "(never read from the system clock)")
    ap.add_argument("--memory-ab-glob", default=None)
    ap.add_argument("--graph-value-glob", default=None)
    args = ap.parse_args(argv)
    manifest_path = build(args.out, args.stamp, args.memory_ab_glob, args.graph_value_glob)
    print("bundle built at %s" % args.out)
    print("manifest: %s" % manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
