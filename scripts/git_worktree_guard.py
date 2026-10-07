#!/usr/bin/env python3
"""Pre-tool-use guard for git worktree safety.

Written 2026-09-12 after a night run lost its own uncommitted fixes twice to
`git checkout HEAD -- <file>` and a reviewer lost an edit to `git stash`,
which every worktree of a repository shares. Written from a role-worded
spec; the -C and -c handling was added in review.

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

REFUSAL_PREFIX = 'git_worktree_guard: REFUSED:'


def _require_str(value, name):
    if not isinstance(value, str):
        raise ValueError('{0} must be a string'.format(name))
    return value


def _require_list_of_str(value, name):
    if not isinstance(value, list):
        raise ValueError('{0} must be a list'.format(name))
    for item in value:
        if not isinstance(item, str):
            raise ValueError('{0} must be a list of strings'.format(name))
    return value


def refusal(message):
    """Return a refusal reason carrying the standard prefix.

    Contract C names stderr_prefix 'git_worktree_guard: REFUSED:' as part
    of a refusal, so every check hands back a reason that already starts
    with it, and main writes that line as is, never a second prefix."""
    _require_str(message, 'message')
    if message.startswith(REFUSAL_PREFIX):
        return message
    return REFUSAL_PREFIX + ' ' + message


def tokenize(command):
    if not isinstance(command, str):
        raise ValueError('command must be a string')
    lex = shlex.shlex(command, posix=True, punctuation_chars='&;')
    lex.whitespace_split = True
    lex.commenters = ''
    return list(lex)


def split_segments(tokens):
    if not isinstance(tokens, list):
        raise ValueError('tokens must be a list')
    segments = []
    current = []
    for tok in tokens:
        if not isinstance(tok, str):
            raise ValueError('tokens must be strings')
        if tok and all(ch in '&;' for ch in tok):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def status_is_dirty(directory, path):
    _require_str(directory, 'directory')
    _require_str(path, 'path')
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
    _require_str(path, 'path')
    return refusal("'{0}' has uncommitted working tree changes; commit or save a "
                   "patch first (git diff -- {0} > file.patch)".format(path))


def check_checkout(args, directory):
    _require_list_of_str(args, 'args')
    _require_str(directory, 'directory')
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
    _require_list_of_str(args, 'args')
    _require_str(directory, 'directory')
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
    _require_list_of_str(args, 'args')
    _require_str(directory, 'directory')
    if args:
        sub = args[0]
        if sub in ('list', 'show'):
            return None
        if sub.startswith('-'):
            sub = 'push'
        if sub not in ('push', 'save'):
            return None
    if worktree_count(directory) > 1:
        return refusal('stash is shared by every worktree of this repository; '
                       'save a patch with git diff instead')
    return None


def check_worktree_remove(args, directory):
    """`git worktree remove <path> [--force]`. Without --force, git already
    refuses on its own when the worktree is dirty -- this guard exists only
    for the --force case, which bypasses that built-in protection exactly
    the way it did on 2026-09-19: a worktree carrying real uncommitted
    source changes (beyond an already-committed, already-merged base) was
    force-removed in a batch of otherwise-safe cleanups, with no per-item
    check in that same command."""
    _require_list_of_str(args, 'args')
    _require_str(directory, 'directory')
    if '--force' not in args and '-f' not in args:
        return None
    paths = [a for a in args if not a.startswith('-')]
    if not paths:
        return None
    target = paths[0]
    if not os.path.isabs(target):
        target = os.path.join(directory, target)
    if status_is_dirty(target, '.'):
        return refusal("'{0}' has uncommitted working tree changes; --force would discard "
                       "them permanently. Save a patch first (git -C {0} diff > file.patch), "
                       "or commit, before removing.".format(target))
    return None


def check_worktree(args, directory):
    if not args:
        return None
    sub = args[0]
    if sub != 'remove':
        return None
    return check_worktree_remove(args[1:], directory)


def check_git(args, directory):
    _require_list_of_str(args, 'args')
    _require_str(directory, 'directory')
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
    if sub == 'worktree':
        return check_worktree(rest, directory)
    return None


#: Words after which a new command starts inside one segment.
_COMMAND_STARTS = ('|', '||', 'sudo', 'xargs', 'command', 'env', 'exec',
                   'builtin', 'eval', 'then', 'do', 'else', '{', '(', 'time',
                   'nohup')
_GLOB_CHARS = '*?['
#: Expansion text hides the same thing a wildcard hides: which paths get
#: deleted.  R9 refuses it on any rm, recursive or not.
_EXPANSION_CHARS = '$`~'


def check_rm(args, directory):
    """Refuse an rm whose targets do not literally name what they delete.

    2026-09-20: `rm -rf <temp dir>/c0-parallel-*`, meant for one dead run's
    leftovers, also matched the work copies of a run that was alive. R9 now
    refuses glob text (*, ?, [) and expansion text ($, backtick, tilde) in
    any target, recursive or not, because either one hides what gets
    deleted. A recursive delete also refuses an empty target list, a flag or
    empty target, ., .., /, an ancestor of the tracked directory, and a
    tracked directory that cannot be verified to exist on disk."""
    _require_list_of_str(args, 'args')
    _require_str(directory, 'directory')
    recursive = False
    targets = []
    stripping = True
    for tok in args:
        if stripping:
            if tok == '--':
                stripping = False
                continue
            if tok.startswith('-') and tok != '-':
                if tok == '--recursive' or (not tok.startswith('--')
                                            and ('r' in tok or 'R' in tok)):
                    recursive = True
                continue
            stripping = False
        if tok in ('|', '||', '&&', ';'):
            break
        targets.append(tok)
    for target in targets:
        if (any(ch in target for ch in _GLOB_CHARS)
                or any(ch in target for ch in _EXPANSION_CHARS)):
            return refusal("rm target '{0}' carries a wildcard or a variable, "
                           "so it deletes whatever matches, live work "
                           "included. List it first (ls -d {0}), then remove "
                           "the paths you named, one by one".format(target))
    if not recursive:
        return None
    if not targets:
        return refusal('recursive rm has no target; name each path to delete')
    base = os.path.normpath(directory)
    for target in targets:
        if target == '' or (target.startswith('-') and target != '-'):
            return refusal("recursive rm target '{0}' is a flag or is empty; "
                           "name each path to delete".format(target))
        if target in ('.', '..', '/'):
            return refusal("recursive rm target '{0}' would delete the "
                           "tracked directory or one of its parents; name a "
                           "subpath".format(target))
        if os.path.isabs(target):
            resolved = os.path.normpath(target)
        else:
            resolved = os.path.normpath(os.path.join(base, target))
        if resolved == base or base.startswith(resolved + os.sep):
            return refusal("recursive rm target '{0}' resolves to the tracked "
                           "directory or one of its parents; name a "
                           "subpath".format(target))
    if not os.path.isdir(directory):
        return refusal("tracked directory '{0}' cannot be verified to exist; "
                       "refusing a recursive rm".format(directory))
    return None


def analyze(command, cwd):
    if not isinstance(command, str):
        raise ValueError('command must be a string')
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError('cwd must be a string or None')
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
        for i, tok in enumerate(seg):
            if os.path.basename(tok) == 'rm' and (i == 0 or seg[i - 1] in _COMMAND_STARTS):
                reason = check_rm(seg[i + 1:], directory)
                if reason:
                    return reason
        if seg[0] == 'git':
            reason = check_git(seg[1:], directory)
            if reason:
                return reason
    return None


#: Characters a raw input word can be built from; everything else separates
#: words, so JSON quoting such as "cwd": "rm -rf x" still yields the word rm.
_RAW_WORD_CHARS = frozenset(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-')


def _raw_has_rm_word(raw):
    """R7: true when the raw stdin text carries an rm word, matched exactly.

    The raw text is split on every character that is not a word character:
    shell whitespace, the punctuation &;|(), and the quoting and structural
    characters JSON uses. The comparison stays exact, never a substring, so
    warm and farm never match. Bytes are decoded first, so binary input is
    inspected instead of being skipped."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode('utf-8', 'replace')
        except Exception:  # sbe: allow-silent raw bytes that cannot be read as text
            return False
    elif isinstance(raw, str):
        text = raw
    else:
        try:
            text = str(raw)
        except Exception:  # sbe: allow-silent raw input that has no text form
            return False
    words = []
    current = []
    for ch in text:
        if ch in _RAW_WORD_CHARS:
            current.append(ch)
        else:
            if current:
                words.append(''.join(current))
                current = []
    if current:
        words.append(''.join(current))
    for word in words:
        if word == 'rm' or word.endswith('/rm'):
            return True
    return False


def _rm_fail_open(raw):
    """R7: every fail open branch refuses instead when the raw text shows rm."""
    if _raw_has_rm_word(raw):
        sys.stderr.write(
            REFUSAL_PREFIX + ' raw input carries an rm command\n')
        return 2
    return 0


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:  # sbe: allow-silent malformed hook input fails open by design, per the docstring
        return _rm_fail_open(raw)
    if not isinstance(data, dict):
        return _rm_fail_open(raw)
    if data.get('tool_name') != 'Bash':
        return _rm_fail_open(raw)
    tool_input = data.get('tool_input')
    if not isinstance(tool_input, dict):
        return _rm_fail_open(raw)
    command = tool_input.get('command')
    if not isinstance(command, str):
        return _rm_fail_open(raw)
    cwd = data.get('cwd')
    if cwd is None:
        cwd = os.getcwd()
    elif not isinstance(cwd, str):
        return _rm_fail_open(raw)
    try:
        reason = analyze(command, cwd)
    except Exception:  # sbe: allow-silent an unparseable command fails open by design, per the docstring
        return _rm_fail_open(raw)
    if reason:
        if not reason.startswith(REFUSAL_PREFIX):
            reason = REFUSAL_PREFIX + ' ' + reason
        sys.stderr.write(reason + '\n')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
