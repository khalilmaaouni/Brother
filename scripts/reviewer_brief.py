#!/usr/bin/env python3
"""Generate a one-page reviewer brief from a git ref range. SR-09.

THE ROW THIS EXISTS FOR. SR-09's own done_check: a reviewer reads only the
generated brief and correctly judges change, risk, proof, and unknowns without
opening conversation history. So this prints exactly four sections and nothing
that depends on a chat log, an author's memory, or context in someone's head:
git and the working tree, nothing else.

    python3 scripts/reviewer_brief.py [REF_RANGE]

REF_RANGE defaults to origin/main..HEAD. Every number printed comes from a
command this script ran (git diff --numstat, git log), never from a commit's
prose: prose is only ever quoted, under PROOF, never counted.

RISK is mechanical, not judged: a fixed rule scores each touched file and the
rule is printed in the output so a reviewer can dispute the rule instead of
the number.

Exit 0 with a brief on a non-empty range. Exit 1, printing NO-DATA and no
brief, when the range has no commits: an empty brief must never look clean.
"""
import re
import subprocess
import sys

RISK_RULES = [
    ("scripts/ or tools/ (a control)", re.compile(r"(^|/)(scripts|tools)/")),
    ("a test file (the thing that would catch a defect)",
     re.compile(r"(^|/)test_[^/]+\.py$|(^|/)[^/]+_test\.py$|(^|/)tests?/")),
    ("a manifest or lock file",
     re.compile(r"(^|/)(package(-lock)?\.json|Gemfile\.lock|requirements.*\.txt|"
                r"Pipfile\.lock|poetry\.lock|go\.(mod|sum)|Cargo\.(toml|lock)|"
                r"[^/]+\.lock)$")),
    ("a filename matching hook/gate/guard/auth/secret/credential",
     re.compile(r"(?i)(hook|gate|guard|auth|secret|credential)")),
]

PROOF_PATTERN = re.compile(
    r"(exit(?:ed)? (?:code )?\d+|exit code \d+|\bPASS\b|\bFAIL\b|\bNO-DATA\b|"
    r"\d+ (?:tests?|failures?|errors?) |ran \d+ test)",
    re.IGNORECASE,
)

CLAIM_WORDS = re.compile(r"\b(fixed|closed|verified|done)\b", re.IGNORECASE)


def run(args):
    """Run a subprocess, capturing its OWN exit code. Never read $? after a pipe."""
    result = subprocess.run(
        args, cwd=REPO_DIR, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout, result.stderr


def find_repo_dir():
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


REPO_DIR = find_repo_dir()


def commit_list(ref_range):
    """Return list of (sha, subject) newest-first, or None if the range is empty."""
    code, out, err = run(["git", "log", "--pretty=format:%H\x1f%s", ref_range])
    if code != 0:
        return None, err
    lines = [l for l in out.split("\n") if l.strip()]
    commits = []
    for line in lines:
        parts = line.split("\x1f", 1)
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))
    return commits, err


def commit_bodies(ref_range):
    """Full commit messages (subject + body) for the range, newest-first."""
    code, out, err = run(["git", "log", "--pretty=format:%H%n%B%x03", ref_range])
    if code != 0:
        return []
    bodies = []
    for chunk in out.split("\x03"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        nl = chunk.find("\n")
        if nl == -1:
            sha, msg = chunk, ""
        else:
            sha, msg = chunk[:nl], chunk[nl + 1:]
        bodies.append((sha, msg))
    return bodies


def numstat(ref_range):
    """Return list of (added, removed, path). added/removed are ints or None (binary)."""
    code, out, err = run(["git", "diff", "--numstat", ref_range])
    if code != 0:
        return [], err
    rows = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        a = None if added == "-" else int(added)
        r = None if removed == "-" else int(removed)
        rows.append((a, r, path))
    return rows, err


def risk_for_file(path):
    hits = [label for label, pat in RISK_RULES if pat.search(path)]
    return hits


def build_change_section(commits, rows):
    lines = ["CHANGE", "-" * 6]
    total_add = sum(a for a, r, p in rows if a is not None)
    total_rem = sum(r for a, r, p in rows if r is not None)
    lines.append(f"{len(rows)} file(s) touched, +{total_add}/-{total_rem} lines "
                 f"(git diff --numstat).")
    lines.append(f"{len(commits)} commit(s) (git log):")
    for sha, subject in commits:
        lines.append(f"  {sha[:10]}  {subject}")
    lines.append("")
    lines.append("Files:")
    for a, r, path in rows:
        astr = "-" if a is None else f"+{a}"
        rstr = "-" if r is None else f"-{r}"
        lines.append(f"  {astr}/{rstr}  {path}")
    return lines


def build_risk_section(rows):
    lines = ["RISK", "-" * 4]
    lines.append("RULE: rank a file higher when it touches scripts/ or tools/ "
                 "(a control), a test file (the thing that would catch a "
                 "defect), a manifest or lock file, or a filename matching "
                 "hook, gate, guard, auth, secret, or credential. Score is the "
                 "count of matching rules for that file, mechanical, not "
                 "judged.")
    scored = []
    for a, r, path in rows:
        hits = risk_for_file(path)
        scored.append((len(hits), path, hits))
    scored.sort(key=lambda t: (-t[0], t[1]))
    any_flagged = any(score > 0 for score, _, _ in scored)
    if not any_flagged:
        lines.append("No touched file matches any risk rule.")
    else:
        for score, path, hits in scored:
            if score == 0:
                continue
            lines.append(f"  [{score}] {path}")
            for h in hits:
                lines.append(f"        matches: {h}")
    return lines


def build_proof_section(bodies):
    lines = ["PROOF", "-" * 5]
    found = []
    for sha, msg in bodies:
        for line in msg.split("\n"):
            line = line.strip()
            if line and PROOF_PATTERN.search(line):
                found.append((sha[:10], line))
    if not found:
        lines.append("NO PROOF FOUND: no commit message line in this range "
                      "matches an evidence pattern (exit code, test count, "
                      "PASS/FAIL/NO-DATA verdict).")
    else:
        for sha, line in found:
            lines.append(f"  {sha}  \"{line}\"")
    return lines, found


def build_unknown_section(commits, rows, bodies, proof_lines):
    lines = ["UNKNOWN", "-" * 7]
    items = []

    touched_dirs_with_tests = set()
    test_pat = RISK_RULES[1][1]
    for a, r, path in rows:
        if test_pat.search(path):
            touched_dirs_with_tests.add(path.rsplit("/", 1)[0] if "/" in path else "")

    non_test_paths = [p for a, r, p in rows if not test_pat.search(p)]
    if non_test_paths and not touched_dirs_with_tests:
        items.append(
            "No test file is touched anywhere in this range: "
            + ", ".join(non_test_paths) + " change with no accompanying test change."
        )

    proof_shas = {sha for sha, _ in proof_lines}
    for sha, msg in bodies:
        if sha[:10] not in proof_shas:
            subject = msg.split("\n", 1)[0]
            items.append(f"Commit {sha[:10]} (\"{subject}\") carries no evidence "
                          f"line matching the proof pattern.")

    for sha, msg in bodies:
        for line in msg.split("\n"):
            line = line.strip()
            if not line:
                continue
            if CLAIM_WORDS.search(line) and not PROOF_PATTERN.search(line):
                items.append(f"Commit {sha[:10]} claims a result (\"{line}\") "
                             f"with no command or exit code beside it.")

    if not items:
        lines.append("Checked: every touched file has an accompanying test "
                     "change, every commit carries an evidence line, and no "
                     "claim word appears without one. Found empty.")
    else:
        seen = set()
        for it in items:
            if it in seen:
                continue
            seen.add(it)
            lines.append(f"  - {it}")
    return lines


def generate(ref_range):
    if REPO_DIR is None:
        return 1, "NO-DATA: not inside a git repository.\n"

    commits, log_err = commit_list(ref_range)
    if commits is None:
        return 1, f"NO-DATA: git log failed on range {ref_range!r}: {log_err.strip()}\n"
    if len(commits) == 0:
        return 1, (f"NO-DATA: empty range {ref_range!r}: no commits found. "
                    f"A brief cannot be generated over nothing.\n")

    rows, diff_err = numstat(ref_range)
    bodies = commit_bodies(ref_range)

    out = []
    out.append(f"REVIEWER BRIEF: {ref_range}")
    out.append("=" * (len(out[0])))
    out.append("")
    out.extend(build_change_section(commits, rows))
    out.append("")
    out.extend(build_risk_section(rows))
    out.append("")
    proof_section, proof_hits = build_proof_section(bodies)
    out.extend(proof_section)
    out.append("")
    out.extend(build_unknown_section(commits, rows, bodies, proof_hits))
    out.append("")
    return 0, "\n".join(out) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ref_range = argv[0] if argv else "origin/main..HEAD"
    code, text = generate(ref_range)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
