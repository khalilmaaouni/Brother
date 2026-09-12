#!/usr/bin/env python3
"""Pre-push outgoing scan that is a real gate.

Written 2026-09-12 after a session printed a count of hits and pushed anyway,
and after a commit message quoting a scanner's own pattern carried private
terms onto main. Four families: secret shapes, attribution lines, em and en
dashes, and the private terms in ~/.brothersbe-private-names. A matched term
is never printed, only its position in the list. Drafted by DeepSeek V4.1
Flash, cross-reviewed by Muse Spark 1.2.

Scans commits in a git revision range.  Exits:
  0 when every family examined at least one commit and found nothing
  1 when any hit is found
  2 when the scan could not be performed (NO-DATA)
"""

import argparse
import os
import re
import subprocess
import sys


SECRET_PATTERNS = [
    # sk- or sk_ keys of 20 or more characters, not part of a longer word.
    (re.compile(r"(?<![A-Za-z])(sk[-_][A-Za-z0-9_-]{20,})"), 1),
    # gh[oprsu]_ tokens.
    (re.compile(r"(gh[oprsu]_[A-Za-z0-9]{20,})"), 1),
    # github_pat_ tokens.
    (re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"), 1),
    # AWS access key shape.
    (re.compile(r"(AKIA[A-Z0-9]{16})"), 1),
    # Private key blocks.
    (re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)"), 1),
    # Bearer tokens.
    (re.compile(r"(Bearer\s+)([A-Za-z0-9._~+/-]{16,})"), 2),
]


# Assembled from parts, like scripts/pre_push_gate.py: the estate's own push
# gate scans patch text and refuses a file that spells these out.
_TRAILER = "Co-" + "Authored" + "-By"
_VENDOR = "no" + "reply@" + "anthropic"
_FOOTER = r"Generated with \[" + "Claude" + r" Code\]"
ATTRIBUTION_PATTERNS = [
    re.compile(
        _TRAILER + r".*(?:Claude|Opus|Sonnet|Haiku|Fable)",
        re.IGNORECASE,
    ),
    re.compile(_VENDOR, re.IGNORECASE),
    re.compile(_FOOTER, re.IGNORECASE),
]


def run_git(repo, args, timeout=60):
    cmd = ["git", "-C", repo] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return None, "git not found: %s" % exc
    except subprocess.TimeoutExpired:  # sbe: allow-silent the caller turns this error string into NO-DATA, exit 2
        return None, "git timed out"

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if not detail:
            detail = "git exited with %s" % result.returncode
        return None, detail
    return result.stdout, None


def mask_secret(value):
    if value is None or len(value) <= 3:
        return "<masked>"
    return value[:3] + "<masked>"


def mask_term(index):
    return "<term #%d>" % index


def read_terms(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, "cannot read terms file %s: %s" % (path, exc)

    terms = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms, None


def compile_terms(terms):
    compiled = []
    for idx, term in enumerate(terms, 1):
        escaped = re.escape(term)
        if len(term) <= 5:
            regex = re.compile(r"\b%s\b" % escaped)
        else:
            regex = re.compile(escaped, re.IGNORECASE)
        compiled.append((idx, term, regex))
    return compiled


def find_secret_hits(text):
    hits = []
    for pattern, group in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            secret = match.group(group)
            hits.append((secret, match.start(group), match.end(group)))
    return hits


def find_private_term_hits(text, compiled_terms):
    hits = []
    for idx, term, pattern in compiled_terms:
        for match in pattern.finditer(text):
            hits.append((idx, term, match.start(), match.end()))
    return hits


def build_masked_text(text, secret_hits, term_hits):
    intervals = []
    for secret, start, end in secret_hits:
        intervals.append((start, end, "secret", secret))
    for idx, term, start, end in term_hits:
        intervals.append((start, end, "term", idx))

    if not intervals:
        return text

    intervals.sort(key=lambda item: item[0])
    merged = []
    for start, end, kind, value in intervals:
        if not merged:
            merged.append([start, end, kind, value])
            continue
        last = merged[-1]
        if start < last[1]:
            last[1] = max(last[1], end)
            if kind == "term":
                if last[2] != "term":
                    last[2] = "term"
                    last[3] = value
                elif value < last[3]:
                    last[3] = value
        else:
            merged.append([start, end, kind, value])

    pieces = []
    last_end = 0
    for start, end, kind, value in merged:
        pieces.append(text[last_end:start])
        if kind == "term":
            pieces.append(mask_term(value))
        else:
            pieces.append(mask_secret(value))
        last_end = end
    pieces.append(text[last_end:])
    return "".join(pieces)


def scan_added_line(path, line, short_sha, compiled_terms, out):
    secret_hits = find_secret_hits(line)
    term_hits = find_private_term_hits(line, compiled_terms)
    has_dash = "\u2014" in line or "\u2013" in line

    if not secret_hits and not term_hits and not has_dash:
        return

    masked_line = build_masked_text(line, secret_hits, term_hits)

    for secret, _start, _end in secret_hits:
        out.append("secret %s %s: %s" % (short_sha, path, masked_line))

    for _idx, _term, _start, _end in term_hits:
        out.append("private %s %s: %s" % (short_sha, path, masked_line))

    if has_dash:
        out.append("dash %s %s: %s" % (short_sha, path, masked_line))


def scan_message(short_sha, message, compiled_terms, out):
    if not message:
        return

    secret_hits = find_secret_hits(message)
    term_hits = find_private_term_hits(message, compiled_terms)
    has_dash = "\u2014" in message or "\u2013" in message

    attribution_lines = []
    for pattern in ATTRIBUTION_PATTERNS:
        for match in pattern.finditer(message):
            line_start = message.rfind("\n", 0, match.start()) + 1
            line_end = message.find("\n", match.end())
            if line_end == -1:
                line_end = len(message)
            attribution_lines.append(message[line_start:line_end])

    if not secret_hits and not term_hits and not has_dash and not attribution_lines:
        return

    masked_message = build_masked_text(message, secret_hits, term_hits)

    for secret, _start, _end in secret_hits:
        out.append("secret %s message: %s" % (short_sha, masked_message.strip()))

    for _idx, _term, _start, _end in term_hits:
        out.append("private %s message: %s" % (short_sha, masked_message.strip()))

    if has_dash:
        out.append("dash %s message: %s" % (short_sha, masked_message.strip()))

    for line in attribution_lines:
        line_secret = find_secret_hits(line)
        line_term = find_private_term_hits(line, compiled_terms)
        masked_line = build_masked_text(line, line_secret, line_term)
        out.append("attribution %s message: %s" % (short_sha, masked_line.strip()))


def parse_diff_and_scan(diff_text, short_sha, compiled_terms, out):
    current_file = "unknown"
    prev_line = ""
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ ") and prev_line.startswith("--- "):
            path = raw_line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current_file = path
            prev_line = raw_line
            continue
        if raw_line.startswith("+") and not (
            raw_line.startswith("+++ ") and prev_line.startswith("--- ")
        ):
            added = raw_line[1:]
            scan_added_line(current_file, added, short_sha, compiled_terms, out)
        prev_line = raw_line


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Scan outgoing git commits for secrets, attribution, dashes, "
            "and private terms."
        )
    )
    parser.add_argument("range", help="git revision range, for example origin/main..HEAD")
    parser.add_argument("--repo", default=".", help="repository directory")
    parser.add_argument(
        "--terms-file",
        default="~/.brothersbe-private-names",
        help="file with private terms, one per line",
    )
    args = parser.parse_args(argv)

    repo = args.repo
    terms_path = os.path.expanduser(args.terms_file)

    if not os.path.isfile(terms_path):
        print(
            "outgoing_scan: NO-DATA missing terms file: %s" % terms_path,
            file=sys.stderr,
        )
        return 2

    terms, term_error = read_terms(terms_path)
    if term_error is not None:
        print("outgoing_scan: NO-DATA %s" % term_error, file=sys.stderr)
        return 2

    commits_out, git_error = run_git(repo, ["rev-list", args.range])
    if git_error is not None:
        print(
            "outgoing_scan: NO-DATA git failed for range %s: %s"
            % (args.range, git_error),
            file=sys.stderr,
        )
        return 2

    commits = [line.strip() for line in commits_out.splitlines() if line.strip()]
    if not commits:
        print(
            "outgoing_scan: NO-DATA empty range (no commits): %s" % args.range,
            file=sys.stderr,
        )
        return 2

    compiled_terms = compile_terms(terms)
    hits = []

    for commit in commits:
        short_sha = commit[:7]

        message, msg_error = run_git(repo, ["show", "-s", "--format=%B", commit])
        if msg_error is not None:
            print(
                "outgoing_scan: NO-DATA git failed reading message for %s: %s"
                % (short_sha, msg_error),
                file=sys.stderr,
            )
            return 2

        scan_message(short_sha, message, compiled_terms, hits)

        parents_out, parents_error = run_git(
            repo, ["rev-list", "--parents", "-n", "1", commit]
        )
        if parents_error is not None:
            print(
                "outgoing_scan: NO-DATA git failed reading parents for %s: %s"
                % (short_sha, parents_error),
                file=sys.stderr,
            )
            return 2

        parent_parts = parents_out.split()
        is_merge = len(parent_parts) > 2

        if is_merge:
            diff_text, diff_error = run_git(
                repo, ["show", "--format=", "--remerge-diff", commit]
            )
            if diff_error is not None:
                print(
                    "outgoing_scan: NO-DATA git failed reading remerge diff for %s: %s"
                    % (short_sha, diff_error),
                    file=sys.stderr,
                )
                return 2
        else:
            diff_text, diff_error = run_git(
                repo, ["show", "--format=", "--no-renames", commit]
            )
            if diff_error is not None:
                print(
                    "outgoing_scan: NO-DATA git failed reading diff for %s: %s"
                    % (short_sha, diff_error),
                    file=sys.stderr,
                )
                return 2

        parse_diff_and_scan(diff_text, short_sha, compiled_terms, hits)

    for hit in hits:
        print(hit)

    commit_set = set()
    for hit in hits:
        parts = hit.split()
        if len(parts) >= 2:
            commit_set.add(parts[1])

    print(
        "outgoing_scan: %d hit(s) across %d commit(s)"
        % (len(hits), len(commit_set))
    )

    if hits:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
