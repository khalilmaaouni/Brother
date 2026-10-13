"""Merge the outputs of `sh scripts/check_all.sh --shard k/n` into one verdict (unit CA1).

    python3 scripts/check_all_merge.py merge [--log PATH] FILE...

Each FILE is one shard's saved output. The merge is PASS (exit 0) or FAIL
(exit 1) only when the files are exactly the n shards of one run of one
commit, each complete and each having run every row it owns. Anything else (a
missing, unreadable or truncated shard, a duplicate k, shards of different
runs) is NO-DATA, exit 2: never a pass. --log writes one combined log in the
shape of a single run, which scripts/battery_verdict.py reads unchanged; a
NO-DATA merge writes none.

    python3 scripts/check_all_merge.py reach --range RANGE [--root DIR]

prints the names of the battery rows a git range reaches, one per line, then
`REACH <selected> of <rows> row(s) from <changed> changed path(s)`. A reach
that cannot be computed prints `REACH NO-DATA: <why>; running every row` and
exits 2, and check_all.sh then runs every row: any doubt runs more rows.
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
VERDICTS = ("PASS", "FAIL", "NO-DATA")
RUN_CHECK_RE = re.compile(r'^run_check\s+"([^"]+)"\s+(.*)$')     # battery_verdict.RUN_CHECK_RE
DOTTED_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
SHELL_SIGNS = ("$", "|", "&", ";", "`", "<", ">")
SUMMARY_RE = re.compile(r"^pass (\d+)   fail (\d+)   no-data (\d+)$")
SHARD_RE = re.compile(r"^SHARD (\d+)/(\d+): owned (\d+) of (\d+) rows, ran (\d+)( \(reach\))?$")
TEST_LINE_RE = re.compile(r"^\s+(?:FAIL|ERROR): \w+ \([\w.]+\)")
COMMIT_PREFIX = "Brother: measuring commit "
NODATA_WORDS = "  (not a pass, and not a failure)"


class ShardSummary:
    """One parsed shard output."""

    def __init__(self, k, n, owned, ran, total, passed, failed, nodata,
                 failed_names, nodata_names, rows, commit, reach):
        self.k, self.n, self.owned, self.ran, self.total = k, n, owned, ran, total
        self.passed, self.failed, self.nodata = passed, failed, nodata
        self.failed_names, self.nodata_names, self.rows = failed_names, nodata_names, rows
        self.commit, self.reach = commit, reach


def parse_shard(text: str) -> Optional[ShardSummary]:
    """None when the output is incomplete or unreadable: no summary line, no
    SHARD line, or row lines that disagree with the counts it printed."""
    if not isinstance(text, str):
        return None
    summary = shard = None
    commit = None
    rows, counts = [], {v: [] for v in VERDICTS}
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) >= 4 and tokens[0] in VERDICTS and tokens[1] == "exit":
            rows.append(line)
            counts[tokens[0]].append(tokens[3])
            continue
        if TEST_LINE_RE.match(line) and rows:
            rows.append(line)
            continue
        if line.startswith(COMMIT_PREFIX) and commit is None:
            commit = line
        m = SUMMARY_RE.match(line)
        if m:
            summary = tuple(int(x) for x in m.groups())
        m = SHARD_RE.match(line)
        if m:
            shard = m
    if summary is None or shard is None:
        return None
    k, n, owned, total, ran = (int(shard.group(i)) for i in range(1, 6))
    passed, failed, nodata = summary
    if (passed, failed, nodata) != tuple(len(counts[v]) for v in VERDICTS):
        return None
    if ran != passed + failed + nodata or not 1 <= k <= n:
        return None
    return ShardSummary(k, n, owned, ran, total, passed, failed, nodata,
                        counts["FAIL"], counts["NO-DATA"], rows, commit, bool(shard.group(6)))


def _inconsistent(shards: List[ShardSummary]) -> Optional[str]:
    """Why these complete shards are not exactly one run, or None."""
    n = shards[0].n
    if any(s.n != n for s in shards):
        return "the shards disagree on n"
    if len(shards) != n:
        return "%d shard file(s) for a %d shard run" % (len(shards), n)
    if sorted(s.k for s in shards) != list(range(1, n + 1)):
        return "the shard numbers are not exactly 1 to %d" % n
    if len({s.total for s in shards}) != 1:
        return "the shards disagree on the row count"
    if sum(s.owned for s in shards) != shards[0].total:
        return "the shards own %d of %d rows" % (sum(s.owned for s in shards), shards[0].total)
    if len({s.commit for s in shards}) != 1:
        return "the shards measured different commits"
    if len({s.reach for s in shards}) != 1:
        return "some shards ran under --reach and some did not"
    for s in shards:
        if s.ran > s.owned or (not s.reach and s.ran != s.owned):
            return "shard %d/%d ran %d of the %d rows it owns" % (s.k, n, s.ran, s.owned)
    return None


def _check(texts: List[str]) -> Tuple[Optional[List[ShardSummary]], str]:
    if not texts:
        return None, "no shard output given"
    shards = []
    for i, text in enumerate(texts, 1):
        s = parse_shard(text)
        if s is None:
            return None, "shard output %d is unreadable or incomplete (no summary)" % i
        shards.append(s)
    why = _inconsistent(shards)
    return (None, why) if why else (sorted(shards, key=lambda s: s.k), "")


def _rollup(shards):
    passed = sum(s.passed for s in shards)
    failed = sum(s.failed for s in shards)
    nodata = sum(s.nodata for s in shards)
    failed_names = [x for s in shards for x in s.failed_names]
    nodata_names = [x for s in shards for x in s.nodata_names]
    lines = []
    if failed_names:
        lines.append("FAILED: " + " ".join(failed_names))
    if nodata_names:
        lines.append("NO-DATA: " + " ".join(nodata_names) + NODATA_WORDS)
    return passed, failed, nodata, lines


def merge(texts: List[str]) -> Tuple[int, str]:
    """(exit code 0 PASS, 1 FAIL, 2 NO-DATA, the printed report)."""
    shards, why = _check(texts)
    if shards is None:
        return 2, "NO-DATA: %s; nothing passed\nVERDICT NO-DATA\n" % why
    passed, failed, nodata, lines = _rollup(shards)
    out = ["SHARD %d/%d: owned %d of %d rows, ran %d%s" % (s.k, s.n, s.owned, s.total, s.ran,
                                                         " (reach)" if s.reach else "")
           for s in shards]
    out += lines
    out.append("MERGED pass %d   fail %d   no-data %d from %d shard(s)" % (passed, failed, nodata, len(shards)))
    code = 1 if failed else 0
    out.append("VERDICT %s" % ("FAIL" if code else "PASS"))
    return code, "\n".join(out) + "\n"


def merged_log(texts: List[str]) -> Optional[str]:
    """The combined log in the shape of one run, or None for a NO-DATA merge."""
    shards, _ = _check(texts)
    if shards is None:
        return None
    passed, failed, nodata, lines = _rollup(shards)
    out = [shards[0].commit or "", "",
           "Brother: every shipped check, each reporting its own exit code", ""]
    for s in shards:
        out += s.rows
    out += ["", "pass %d   fail %d   no-data %d" % (passed, failed, nodata)] + lines
    return "\n".join(out) + "\n"


def rows_of(battery_text: str) -> List[Tuple[str, str]]:
    """(name, command text) for every run_check registration line."""
    rows = []
    for line in battery_text.splitlines():
        m = RUN_CHECK_RE.match(line.strip())
        if m:
            rows.append((m.group(1), m.group(2).strip()))
    return rows


def _repo_file(token, prev, root):
    """The repository relative file a command token names, or None."""
    if token.startswith("-") or os.path.isabs(token):
        return None
    candidates = [token]
    if prev == "-m" or DOTTED_RE.fullmatch(token):
        candidates.append(token.replace(".", "/") + ".py")
    for cand in candidates:
        rel = os.path.normpath(cand)
        if not rel.startswith("..") and os.path.isfile(os.path.join(root, rel)):
            return rel
    return None


def _hermetic():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import hermetic_test_check
    return hermetic_test_check


def row_selected(name: str, command: str, reach_tests: List[str], changed: List[str], root: str) -> bool:
    """True when the row runs under a reach. Every row the reach cannot judge
    runs: shell syntax, a sh or bash row, a row naming no repository file, a
    file outside the reach directories, a script that is neither a test nor a
    --selftest call (a live check of the tree)."""
    if any(sign in command for sign in SHELL_SIGNS):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    if not tokens or os.path.basename(tokens[0]) in ("sh", "bash"):
        return True
    mapped = [rel for rel in (_repo_file(tok, tokens[i - 1], root) for i, tok in enumerate(tokens) if i)
              if rel]
    if not mapped:
        return True
    try:
        dirs = set(_hermetic().REACH_DIRS)
    except Exception:  # noqa: BLE001  an unreadable reach module judges nothing, so the row runs
        return True
    reached = {os.path.normpath(p) for p in reach_tests}
    touched = {os.path.normpath(p) for p in changed}
    selftest = "--selftest" in tokens
    for rel in mapped:
        if rel in reached or rel in touched:
            return True
        if os.path.dirname(rel) not in dirs:
            return True
        if not os.path.basename(rel).startswith("test_") and not selftest:
            return True
    interpreter = "python3" if os.path.basename(tokens[0]).startswith("python") else tokens[0]
    normal = " ".join(shlex.quote(t) for t in [interpreter] + [t for t in tokens[1:] if t != "-B"])
    return normal in set(reach_tests)


def _deleted(root, rng):
    """The paths the range deletes, or None when git cannot say."""
    try:
        proc = subprocess.run(["git", "log", "--format=", "--name-only", "--diff-filter=D", rng],
                              cwd=root, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return sorted({l for l in proc.stdout.splitlines() if l.strip()})


def reach_rows(root: str, rng: str, battery_path: str = "scripts/check_all.sh") -> Tuple[Optional[List[str]], str]:
    """(names to run or None, one line of reason). None means run every row."""
    if not isinstance(rng, str) or not rng or rng.startswith("-") or any(c.isspace() for c in rng):
        return None, "%r is not one git range" % (rng,)
    try:
        with open(os.path.join(root, battery_path), encoding="utf-8") as fh:
            rows = rows_of(fh.read())
    except (OSError, UnicodeDecodeError) as exc:
        return None, "cannot read the battery %s: %s" % (battery_path, exc)
    if not rows:
        return None, "the battery %s has no run_check rows" % battery_path
    try:
        H = _hermetic()
        changed, problem = H.changed_paths(root, [rng])
        if changed is None:
            return None, problem
        deleted = _deleted(root, rng)
        if deleted is None:
            return None, "git cannot list the files %s deletes" % rng
        if deleted:
            return None, "%s deletes %s, and reach cannot follow a deleted file" % (rng, deleted[0])
        reach = H.tests_reaching(changed, root)
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001  any failure of the reach means every row runs
        return None, "the reach failed: %s: %s" % (type(exc).__name__, exc)
    names = [name for name, cmd in rows if row_selected(name, cmd, reach, changed, root)]
    return names, "REACH %d of %d row(s) from %d changed path(s)" % (len(names), len(rows), len(changed))


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Merge check_all.sh shard outputs.")
    sub = ap.add_subparsers(dest="verb")
    m = sub.add_parser("merge", help="merge shard output files into one verdict")
    m.add_argument("--log", default=None, help="write one combined log here")
    m.add_argument("files", nargs="*")
    r = sub.add_parser("reach", help="list the battery rows a git range reaches")
    r.add_argument("--range", required=True, help="a git range, e.g. abc123..HEAD")
    r.add_argument("--root", default=os.path.dirname(HERE), help="the repository (default: this one)")
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        if argv[:1] == ["reach"] and exc.code:
            print("REACH NO-DATA: the arguments could not be read; running every row")
        return 0 if exc.code == 0 else 2
    if args.verb == "reach":
        names, why = reach_rows(args.root, args.range)
        if names is None:
            print("REACH NO-DATA: %s; running every row" % why)
            return 2
        for name in names:
            print(name)
        print(why)
        return 0
    if args.verb != "merge":
        ap.print_usage(sys.stderr)
        return 2
    texts = [_read(p) for p in args.files]
    code, report = merge(texts)
    sys.stdout.write(report)
    if args.log and code != 2:
        try:
            with open(args.log, "w", encoding="utf-8") as fh:
                fh.write(merged_log(texts))
        except OSError as exc:
            print("NO-DATA: cannot write the merged log %s: %s" % (args.log, exc))
            return 2
        print("LOG %s" % args.log)
    return code


if __name__ == "__main__":
    sys.exit(main())
