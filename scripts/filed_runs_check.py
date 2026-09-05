#!/usr/bin/env python3
"""Reproduces every filed benchmark run's committed verdict from a clean
export of the tree.

WHY THIS EXISTS. Audit row J2: a filed run directory carried a committed
checker-output.txt claiming PASS while the CSVs it read were gitignored, so
nobody could reproduce the PASS from the repository alone. The CSVs are
fixed now (benchmarks/jbeq/mdm/e2e-001/data/*.csv); this script is what
stops the next such claim from standing unverified, by re-running (or
re-hashing) every filed run against ONLY what git actually tracks.

WHAT COUNTS AS A FILED RUN:
  - benchmarks/**/runs/<date>/         (a directory whose parent is "runs")
  - benchmarks/results/<name>/<date>-checkpoint/

HOW A RUN DECLARES ITS CHECKER. A run directory does not name its own
checker, so this script carries a small registry (KNOWN_CHECKERS below)
from a run's own relative path to the script that reproduces it. Two shapes:
  - checker-output.txt beside a KNOWN checker: the checker is re-run,
    `python3 <checker> <run dir>`, against a CLEAN EXPORT of the tree
    (`git archive HEAD` extracted into a temp directory, so untracked and
    gitignored files are absent exactly as they would be for anyone who
    clones fresh) and the two verdicts (each output's own last non-empty
    line) are compared.
  - MANIFEST.json naming artefacts and their sha256 hashes: each hash is
    recomputed from the same clean export. The reproduction test here is
    the hash set alone; the manifest's own free-text verdict sentence
    (PASS, PARTIAL, ...) is a different KIND of statement than a hash
    match and is never compared against one, only carried through and
    printed beside REPRODUCES.
Neither present: NO-DATA. NO-DATA is never a pass.

ONE LINE PER RUN:
  filed run <path>: REPRODUCES                    (checker-output.txt runs)
  filed run <path>: REPRODUCES (verdict: X)        (MANIFEST.json runs)
  filed run <path>: DIVERGES (committed says X, clean checkout says Y)
  filed run <path>: DIVERGES (hash mismatch for F) / (missing artefact F)
  filed run <path>: NO-DATA: <reason>

EXIT. 1 if any run DIVERGES. 3 if nothing is filed at all. 0 otherwise.
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile

# A run directory does not name its own checker, so this is the registry:
# "what does a filed run at this relative path reproduce with". Add a row
# here when a new filed-run family ships its own checker; each checker is
# invoked as `python3 <checker> <run dir>` with cwd at the export root,
# mirroring benchmarks/jbeq/mdm/e2e-001/runs/2026-09-05/RUN-NOTES.md's own
# recorded command.
KNOWN_CHECKERS = [
    (re.compile(r'^benchmarks/jbeq/mdm/[^/]+/runs/[^/]+$'),
     'scripts/jbeq_e2e_check.py'),
]


def find_filed_runs(root):
    """Every filed run directory under `root`, as posix paths relative to
    root, sorted for a stable report."""
    found = set()
    for path in glob.glob(os.path.join(root, 'benchmarks', '**', 'runs', '*'),
                           recursive=True):
        if os.path.isdir(path) and os.path.basename(os.path.dirname(path)) == 'runs':
            found.add(os.path.relpath(path, root))
    for path in glob.glob(os.path.join(root, 'benchmarks', 'results', '*',
                                        '*-checkpoint')):
        if os.path.isdir(path):
            found.add(os.path.relpath(path, root))
    return sorted(p.replace(os.sep, '/') for p in found)


def last_verdict_line(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else '(empty output)'


def known_checker_for(run_rel):
    for pattern, checker in KNOWN_CHECKERS:
        if pattern.match(run_rel):
            return checker
    return None


def reproduce_checker(export_root, run_rel, checker_rel, committed_text):
    checker_abs = os.path.join(export_root, checker_rel)
    if not os.path.isfile(checker_abs):
        raise LookupError('known checker %s missing from the clean export'
                           % checker_rel)
    proc = subprocess.run([sys.executable, checker_rel, run_rel],
                           cwd=export_root, capture_output=True, text=True,
                           timeout=120)
    return last_verdict_line(committed_text), last_verdict_line(proc.stdout)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def reproduce_manifest(export_root, run_rel, manifest):
    """manifest: {"verdict": "<free text>", "artefacts": [{"path": relpath,
    "sha256": hex}, ...]}, the schema scripts/test_lhr_checkpoint.py already
    proves against the real checkpoint. path is relative to the run
    directory itself.

    The reproduction test is the hash set alone: every listed artefact
    present in the clean export with the listed hash. The committed
    verdict is a free-text sentence (PASS, PARTIAL, FAIL, ...), a
    different KIND of statement than a hash-match result, so it is never
    compared against one (that comparison is what made an honest PARTIAL
    checkpoint read as DIVERGES). It is carried through unchanged and
    printed beside REPRODUCES. Only a missing artefact or a hash mismatch
    is DIVERGES.

    Returns (ok, line) where line is the text to print after
    "filed run <path>: ".
    """
    committed_verdict = str(manifest.get('verdict', '(no verdict field)'))
    for entry in manifest.get('artefacts', []):
        rel = entry.get('path')
        want_hash = entry.get('sha256')
        abs_path = os.path.join(export_root, run_rel, rel or '')
        if not rel or not os.path.isfile(abs_path):
            return False, 'DIVERGES (missing artefact %s)' % (rel or '(unnamed)')
        if sha256_of(abs_path) != want_hash:
            return False, 'DIVERGES (hash mismatch for %s)' % rel
    return True, 'REPRODUCES (verdict: %s)' % committed_verdict


def check_one(export_root, run_rel, run_abs):
    """Returns (ok, line) where line is the text to print after
    "filed run <path>: "; raises LookupError for NO-DATA."""
    checker_output = os.path.join(run_abs, 'checker-output.txt')
    manifest_path = os.path.join(run_abs, 'MANIFEST.json')
    if os.path.isfile(checker_output):
        checker_rel = known_checker_for(run_rel)
        if checker_rel is None:
            raise LookupError('no checker declared')
        with open(checker_output, encoding='utf-8') as fh:
            committed_text = fh.read()
        committed, fresh = reproduce_checker(export_root, run_rel,
                                              checker_rel, committed_text)
        if committed == fresh:
            return True, 'REPRODUCES'
        return False, ('DIVERGES (committed says %s, clean checkout says %s)'
                        % (committed, fresh))
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding='utf-8') as fh:
            manifest = json.load(fh)
        return reproduce_manifest(export_root, run_rel, manifest)
    raise LookupError('no checker declared')


def export_clean_tree(root, dest):
    """git archive HEAD, extracted into `dest`. Raises RuntimeError if the
    archive itself cannot be produced (no repo, no commit)."""
    archive = subprocess.run(['git', 'archive', 'HEAD'], cwd=root,
                              capture_output=True, timeout=120)
    if archive.returncode != 0:
        raise RuntimeError(archive.stderr.decode('utf-8', 'replace').strip()
                            or 'git archive failed')
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tf:
        # Python 3.9 floor: extractall's `filter` kwarg only exists
        # from 3.12 (PEP 706). Pass it where supported so a 3.12+
        # interpreter gets the safe default instead of a
        # DeprecationWarning; fall back where it does not exist.
        try:
            tf.extractall(dest, filter='data')
        except TypeError:
            tf.extractall(dest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument('--root', default=default_root,
                         help='repository root to scan (default: this '
                              'script\'s own repository)')
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)

    runs = find_filed_runs(root)
    if not runs:
        print('filed runs: NO-DATA, nothing filed under %s' % root)
        return 3

    diverged = False
    with tempfile.TemporaryDirectory(prefix='filed-runs-export-') as export_root:
        try:
            export_clean_tree(root, export_root)
        except RuntimeError as exc:
            for run_rel in runs:
                print('filed run %s: NO-DATA: clean export unavailable (%s)'
                      % (run_rel, exc))
            return 0

        for run_rel in runs:
            run_abs = os.path.join(root, run_rel)
            try:
                ok, line = check_one(export_root, run_rel, run_abs)
            except LookupError as exc:
                print('filed run %s: NO-DATA: %s' % (run_rel, exc))
                continue
            print('filed run %s: %s' % (run_rel, line))
            if not ok:
                diverged = True

    return 1 if diverged else 0


if __name__ == '__main__':
    sys.exit(main())
