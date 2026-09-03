#!/usr/bin/env python3
"""bm_private_scan: the real control for private content reaching a public repo.

Fixes two defects found the same day in the documented push-gate scan:

  DEFECT 1, THE CASE HOLE. The old scan used `grep -F` (case sensitive) against
  the terms file. A capitalized term (say "RAFT") never matched a lowercase
  spelling ("raft") in the corpus, so a dirty file passed as clean.

  DEFECT 2, THE REACHABILITY HOLE. A push publishes git OBJECTS, not the working
  tree or the net diff. A file fixed at HEAD can still have a dirty blob sitting
  reachable from an earlier commit on the same branch, and a file-level or
  diff-level scan never sees it.

This tool never reads the working tree and never diffs. It walks every blob
git considers reachable in the given range via `git rev-list --objects` plus
`git cat-file`, and it also reads every commit message and every ref name in
the repo. Every blob gets TWO passes: (a) terms of 5 characters or fewer are
matched case-insensitively as a WHOLE WORD (bounded on both sides by the line
edge or anything that is not a letter or a digit; the underscore counts as a
boundary since E37, 2026-09-03, when a spelling like path_<term>_file was
found to walk through the old `[A-Za-z0-9_]` bound while the assurance
product's history test refused it); (b) terms longer than 5 characters are
matched case-insensitively as a substring, which is the fix for defect 1.

  THE SHORT-TERM CASE HOLE, closed 2026-09-03. Pass (a) used to match ONLY
  the case stored in the terms file: the reasoning was that a short all-caps
  term like "RAFT" is also an ordinary English word ("raft"), and matching
  case kept that ordinary usage from firing. But the same case-sensitivity
  let a lowercase spelling of the real private term pass uncaught, and one
  product test carried two such terms for five weeks before this was found.
  A false positive on an ordinary word costs a human one look at a named
  hit; a false negative is a client name reaching a public repo. The
  whole-word bound alone already spares the common case (an ordinary word
  that merely contains the term's letters inside a longer word, like "draft"
  containing "raft"), so pass (a) now matches any case too, same as pass (b).

  python3 bm_private_scan.py --range origin/main..HEAD
  python3 bm_private_scan.py --repo /path/to/repo --terms ~/.terms-file

With no --range, the range is origin/<default-branch>..HEAD, where the
default branch is resolved from `git symbolic-ref refs/remotes/origin/HEAD`.
If that cannot be resolved (no origin, or the symref is not set), the tool
scans HEAD entirely: every blob reachable from HEAD, the full history, not
just what is new on this branch.

Exit codes: 0 clean. 2 one or more hits found (each printed with its blob or
commit or ref, its path, and its pass; the matched term itself withheld as a
character count, and a ref name or blob path that itself carries a term is
masked the same way, since this output reaches terminals, transcripts and
battery logs). 3 NO-DATA, the
terms file is missing or unreadable, or the range resolved to zero blobs;
NO-DATA is never a clean pass and the reason is always printed. Any other
failure (bad range, git not a repo) prints ERROR and exits 1.

Python 3.9, standard library only, no network.
"""
import argparse
import os
import re
import subprocess
import sys

DEFAULT_TERMS_PATH = os.path.expanduser("~/.brothersbe-private-names")
SHORT_TERM_MAX_LEN = 5

PASS_A = "A short-term case-insensitive whole-word"
PASS_B = "B long-term case-insensitive substring"


class ScanError(Exception):
    """Raised for a hard failure (bad range, not a repo). Caught in main."""


def _run_text(argv):
    """Run a git command, capturing text output. Raises ScanError on nonzero exit."""
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ScanError("%s failed (exit %d): %s"
                         % (" ".join(argv), proc.returncode, proc.stderr.strip()))
    return proc.stdout


def _run_text_ok(argv):
    """Run a git command; return (ok, stdout) without raising on nonzero exit."""
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="utf-8", errors="replace")
    return proc.returncode == 0, proc.stdout


def _run_bytes(argv):
    """Run a git command, capturing raw bytes output. Raises ScanError on nonzero exit."""
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise ScanError("%s failed (exit %d): %s"
                         % (" ".join(argv), proc.returncode,
                            proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def _load_terms(path):
    """Return (terms, None) or (None, reason) for NO-DATA. Never raises."""
    if not os.path.isfile(path):
        return None, "terms file not found: %s" % path
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return None, "terms file unreadable: %s (%s)" % (path, exc)
    terms = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    if not terms:
        return None, "terms file has no usable entries: %s" % path
    return terms, None


def _build_patterns(terms):
    short_patterns = []
    long_patterns = []
    for term in terms:
        term_bytes = re.escape(term.encode("utf-8"))
        if len(term) <= SHORT_TERM_MAX_LEN:
            pat = re.compile(rb"(?<![A-Za-z0-9])" + term_bytes + rb"(?![A-Za-z0-9])",
                              re.IGNORECASE)
            short_patterns.append((term, pat))
        else:
            pat = re.compile(term_bytes, re.IGNORECASE)
            long_patterns.append((term, pat))
    return short_patterns, long_patterns


def _scan_bytes(data, short_patterns, long_patterns):
    """Return a list of (term, pass_label) for every term that fires on data."""
    hits = []
    for term, pat in short_patterns:
        if pat.search(data):
            hits.append((term, PASS_A))
    for term, pat in long_patterns:
        if pat.search(data):
            hits.append((term, PASS_B))
    return hits


def _mask(text, short_patterns, long_patterns):
    """Replace every substring of text a loaded term's pattern matches with <N>
    (N = that term's character count), using the exact same compiled patterns
    _scan_bytes matches with (same case rule, same whole-word bound for short
    terms). A ref name or a blob path can itself carry a term, not only a
    blob's content, so obj_id and path are passed through this before either
    reaches a printed line. Never applied to the internal hit tuples, only to
    the strings built for output."""
    data = text.encode("utf-8", "replace")
    for term, pat in short_patterns + long_patterns:
        data = pat.sub(("<%d>" % len(term)).encode("ascii"), data)
    return data.decode("utf-8", "replace")


def _resolve_default_range(repo):
    ok, out = _run_text_ok(["git", "-C", repo, "symbolic-ref", "-q", "--short",
                             "refs/remotes/origin/HEAD"])
    default_ref = out.strip()
    if ok and default_ref:
        return "%s..HEAD" % default_ref
    return "HEAD"


def _rev_list_objects(repo, rng):
    """Return (shas, path_of) for every object git considers reachable in rng."""
    out = _run_text(["git", "-C", repo, "rev-list", "--objects", rng])
    shas = []
    path_of = {}
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split(" ", 1)
        sha = parts[0]
        shas.append(sha)
        if len(parts) == 2 and parts[1]:
            path_of[sha] = parts[1]
    return shas, path_of


def _iter_blobs(repo, shas):
    """Yield (sha, content) for every blob among shas, via one `git cat-file --batch`
    process. Non-blob objects (commits, trees, tags) are drained off the stream so the
    protocol stays in sync, never yielded."""
    if not shas:
        return
    proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for sha in shas:
            proc.stdin.write(sha.encode("ascii") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline()
            if not header:
                raise ScanError("git cat-file --batch closed early on %s" % sha)
            parts = header.split()
            if len(parts) == 2 and parts[1] == b"missing":
                continue
            if len(parts) != 3:
                raise ScanError("git cat-file --batch gave an unreadable header: %r" % header)
            obj_type, size = parts[1], int(parts[2])
            content = proc.stdout.read(size)
            if len(content) != size:
                raise ScanError("git cat-file --batch truncated content for %s" % sha)
            proc.stdout.read(1)  # trailing newline git always appends
            if obj_type == b"blob":
                yield sha, content
    finally:
        proc.stdin.close()
        proc.wait()


def _log_messages(repo, rng):
    """Yield (commit_hash, message_bytes) for every commit in rng."""
    out = _run_bytes(["git", "-C", repo, "log", rng, "--format=%x02%H%x03%B"])
    for chunk in out.split(b"\x02"):
        if not chunk:
            continue
        head, _, message = chunk.partition(b"\x03")
        yield head.decode("ascii", "replace"), message


def _list_refs(repo):
    out = _run_text(["git", "-C", repo, "for-each-ref", "--format=%(refname)"])
    return [line for line in out.splitlines() if line]


def scan(repo, rng, terms_path):
    """Run the full scan. Returns (exit_code, report_lines)."""
    lines = []
    terms, no_data_reason = _load_terms(terms_path)
    if terms is None:
        lines.append("blobs scanned: 0")
        lines.append("NO-DATA: %s" % no_data_reason)
        return 3, lines

    short_patterns, long_patterns = _build_patterns(terms)
    lines.append("range: %s" % _mask(rng, short_patterns, long_patterns))
    lines.append("terms loaded: %d (%d short whole-word, %d long substring)"
                  % (len(terms), len(short_patterns), len(long_patterns)))

    shas, path_of = _rev_list_objects(repo, rng)

    hits = []
    blob_count = 0
    for sha, content in _iter_blobs(repo, shas):
        blob_count += 1
        for term, pass_label in _scan_bytes(content, short_patterns, long_patterns):
            hits.append(("blob", sha, path_of.get(sha, "?"), term, pass_label))

    lines.append("blobs scanned: %d" % blob_count)

    if blob_count == 0:
        lines.append("NO-DATA: range %r resolved to zero blobs"
                      % _mask(rng, short_patterns, long_patterns))
        return 3, lines

    commit_count = 0
    for commit_hash, message in _log_messages(repo, rng):
        commit_count += 1
        for term, pass_label in _scan_bytes(message, short_patterns, long_patterns):
            hits.append(("commit", commit_hash, "-", term, pass_label))
    lines.append("commit messages scanned: %d" % commit_count)

    refs = _list_refs(repo)
    for refname in refs:
        ref_bytes = refname.encode("utf-8", "replace")
        for term, pass_label in _scan_bytes(ref_bytes, short_patterns, long_patterns):
            hits.append(("ref", refname, "-", term, pass_label))
    lines.append("refs scanned: %d" % len(refs))

    if hits:
        for kind, obj_id, path, term, pass_label in hits:
            lines.append("HIT %s=%s path=%s term=(a term of %d characters) pass=%s"
                          % (kind, _mask(obj_id, short_patterns, long_patterns),
                             _mask(path, short_patterns, long_patterns),
                             len(term), pass_label))
        lines.append("%d hit(s)" % len(hits))
        return 2, lines

    lines.append("OK: %d blob(s), %d commit message(s), %d ref(s), 0 hits"
                  % (blob_count, commit_count, len(refs)))
    return 0, lines


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--range", default=None,
                    help="git range to scan (default: origin/<default-branch>..HEAD, "
                         "or HEAD entirely if no upstream can be resolved)")
    p.add_argument("--repo", default=None, help="repo path (default: cwd)")
    p.add_argument("--terms", default=DEFAULT_TERMS_PATH,
                    help="path to the private terms file (default: %s)" % DEFAULT_TERMS_PATH)
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    repo = os.path.abspath(args.repo) if args.repo else os.getcwd()

    # Terms load first, before any print that could carry a repository path or
    # an exception text, so short_patterns/long_patterns exist to mask them. If
    # the list is absent, the NO-DATA line below names the list PATH (the
    # --terms argument itself), which cannot carry one of its own terms.
    terms, no_data_reason = _load_terms(args.terms)
    if terms is None:
        print("NO-DATA: %s" % no_data_reason)
        return 3
    short_patterns, long_patterns = _build_patterns(terms)

    ok, _ = _run_text_ok(["git", "-C", repo, "rev-parse", "--git-dir"])
    if not ok:
        print("ERROR: not a git repository: %s"
              % _mask(repo, short_patterns, long_patterns), file=sys.stderr)
        return 1

    rng = args.range if args.range else _resolve_default_range(repo)

    try:
        code, lines = scan(repo, rng, args.terms)
    except ScanError as exc:
        print("ERROR: %s" % _mask(str(exc), short_patterns, long_patterns),
              file=sys.stderr)
        return 1

    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
