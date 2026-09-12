#!/usr/bin/env python3
"""Pre-tool-use guard for git worktree safety.

Written 2026-09-12 after a night run lost its own uncommitted fixes twice to
`git checkout HEAD -- <file>` and a reviewer lost an edit to `git stash`,
which every worktree of a repository shares. Drafted by DeepSeek V4.1 Flash
from a role-worded spec; the -C and -c handling was added in review.

Reads one JSON object from stdin describing a tool call and decides whether
to block it.  Exit 0 to allow (silent).  Exit 2 to refuse, printing a single
line to stderr that starts with "git_worktree_guard: REFUSED: ".

Fail open: any parse error, missing key, non-Bash tool, or git failure
results in exit 0.
"""

import json
import os
import shlex
import subprocess
import sys

TIMEOUT = 10


def tokenize(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars='&;')
    lex.whitespace_split = True
    lex.commenters = ''
    return list(lex)


def split_segments(tokens):
    segments = []
    current = []
    for tok in tokens:
        if tok and all(ch in '&;' for ch in tok):
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    return segments


def status_is_dirty(directory, path):
    try:
        proc = subprocess.run(
            ['git', '-C', directory, 'status', '--porcelain', '--', path],
            capture_output=True, timeout=TIMEOUT, text=True)
    except Exception:  # sbe: allow-silent a guard that cannot ask git fails open by design, per its docstring
        return False
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if line and not line.startswith('??'):
            return True
    return False


def worktree_count(directory):
    try:
        proc = subprocess.run(
            ['git', '-C', directory, 'worktree', 'list', '--porcelain'],
            capture_output=True, timeout=TIMEOUT, text=True)
    except Exception:  # sbe: allow-silent a guard that cannot ask git fails open by design, per its docstring
        return 0
    if proc.returncode != 0:
        return 0
    return sum(1 for line in proc.stdout.splitlines()
               if line.startswith('worktree '))


def dirty_message(path):
    return ("'{0}' has uncommitted working tree changes; commit or save a "
            "patch first (git diff -- {0} > file.patch)").format(path)


def check_checkout(args, directory):
    if '--' not in args:
        return None
    idx = args.index('--')
    paths = args[idx + 1:]
    if not paths:
        return None
    for path in paths:
        if status_is_dirty(directory, path):
            return dirty_message(path)
    return None


def check_restore(args, directory):
    if '--staged' in args:
        return None
    if '--' in args:
        idx = args.index('--')
        paths = args[idx + 1:]
    else:
        paths = [a for a in args if not a.startswith('-')]
    if not paths:
        return None
    for path in paths:
        if status_is_dirty(directory, path):
            return dirty_message(path)
    return None


def check_stash(args, directory):
    if args:
        sub = args[0]
        if sub in ('list', 'show'):
            return None
        if sub.startswith('-'):
            sub = 'push'
        if sub not in ('push', 'save'):
            return None
    if worktree_count(directory) > 1:
        return ('stash is shared by every worktree of this repository; '
                'save a patch with git diff instead')
    return None


def check_git(args, directory):
    # git's own options come before the subcommand: -C names the directory
    # the command acts on, -c sets a config value; both take a value word.
    while args and args[0] in ('-C', '-c') and len(args) > 1:
        if args[0] == '-C':
            target = args[1]
            directory = target if os.path.isabs(target) else os.path.join(directory, target)
        args = args[2:]
    if not args:
        return None
    sub = args[0]
    rest = args[1:]
    if sub == 'checkout':
        return check_checkout(rest, directory)
    if sub == 'restore':
        return check_restore(rest, directory)
    if sub == 'stash':
        return check_stash(rest, directory)
    return None


def analyze(command, cwd):
    tokens = tokenize(command)
    segments = split_segments(tokens)
    directory = cwd or os.getcwd()
    for seg in segments:
        if not seg:
            continue
        if seg[0] == 'cd' and len(seg) >= 2:
            target = seg[1]
            if not os.path.isabs(target):
                target = os.path.join(directory, target)
            directory = os.path.normpath(target)
            continue
        if seg[0] == 'git':
            reason = check_git(seg[1:], directory)
            if reason:
                return reason
    return None


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:  # sbe: allow-silent malformed hook input fails open by design, per the docstring
        return 0
    if not isinstance(data, dict):
        return 0
    if data.get('tool_name') != 'Bash':
        return 0
    tool_input = data.get('tool_input') or {}
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get('command') or ''
    if not isinstance(command, str):
        return 0
    cwd = data.get('cwd') or os.getcwd()
    if not isinstance(cwd, str):
        cwd = os.getcwd()
    try:
        reason = analyze(command, cwd)
    except Exception:  # sbe: allow-silent an unparseable command fails open by design, per the docstring
        return 0
    if reason:
        sys.stderr.write('git_worktree_guard: REFUSED: ' + reason + '\n')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
