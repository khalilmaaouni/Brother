#!/usr/bin/env python3
"""Gate ledger append and chain verification for C0.3."""

import fcntl
import hashlib
import json
import math
import os
import re
import sys

HEX32 = re.compile(r'^[0-9a-f]{32}$')
HEX40 = re.compile(r'^[0-9a-f]{40}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
GENESIS = '0' * 64
VALID_STATUS = {'PASS', 'FAIL', 'NO-DATA'}
VALID_VERDICT_SOURCE = {'full', 'cache', 'shadow'}


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _line_hash(record):
    return hashlib.sha256(_canonical(record).encode('utf-8')).hexdigest()


def _is_hex(value, length):
    if not isinstance(value, str):
        return False
    if len(value) != length:
        return False
    return all(c in '0123456789abcdef' for c in value)


def _validate(record):
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    if record.get('schema') != 1 or type(record.get('schema')) is not int:
        raise ValueError("schema must be integer 1")
    if not _is_hex(record.get('cut_id'), 32):
        raise ValueError("cut_id must be 32 hex")
    if not isinstance(record.get('cut_version'), str) or not record['cut_version']:
        raise ValueError("cut_version must be non-empty string")
    if not isinstance(record.get('gate_name'), str) or not record['gate_name']:
        raise ValueError("gate_name must be non-empty string")
    if not isinstance(record.get('gate_argv'), list) or not all(isinstance(x, str) for x in record['gate_argv']):
        raise ValueError("gate_argv must be list of strings")
    if record.get('status') not in VALID_STATUS:
        raise ValueError("status must be PASS, FAIL or NO-DATA")
    if type(record.get('exit_code')) is not int:
        raise ValueError("exit_code must be integer")
    for key in ('started_unix', 'ended_unix', 'duration_s'):
        val = record.get(key)
        if type(val) is bool or not isinstance(val, (int, float)):
            raise ValueError(f"{key} must be numeric")
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"{key} must be finite")
    if record['duration_s'] < 0:
        raise ValueError("duration_s must be >= 0")
    if record['ended_unix'] < record['started_unix']:
        raise ValueError("ended_unix must be >= started_unix")
    if not isinstance(record.get('worktree_key'), str) or not record['worktree_key']:
        raise ValueError("worktree_key must be non-empty string")
    tree_sha = record.get('tree_sha')
    if tree_sha != 'UNKNOWN' and not _is_hex(tree_sha, 40):
        raise ValueError("tree_sha must be UNKNOWN or 40 hex")
    inputs_hash = record.get('inputs_hash')
    if inputs_hash is not None and not _is_hex(inputs_hash, 64):
        raise ValueError("inputs_hash must be null or 64 hex")
    if not _is_hex(record.get('code_hash'), 64):
        raise ValueError("code_hash must be 64 hex")
    if not isinstance(record.get('tool_versions'), dict):
        raise ValueError("tool_versions must be a dict")
    if record.get('verdict_source') not in VALID_VERDICT_SOURCE:
        raise ValueError("verdict_source must be full, cache or shadow")
    cache_key = record.get('cache_key')
    if cache_key is not None and not _is_hex(cache_key, 64):
        raise ValueError("cache_key must be null or 64 hex")
    deadline = record.get('deadline_unix')
    if deadline is not None:
        if type(deadline) is bool or not isinstance(deadline, (int, float)):
            raise ValueError("deadline_unix must be null or numeric")
        if math.isnan(deadline) or math.isinf(deadline):
            raise ValueError("deadline_unix must be finite")
    prev = record.get('prev_hash')
    if not _is_hex(prev, 64):
        raise ValueError("prev_hash must be 64 hex")


def _read_last_line(path):
    # Only a ledger that does not exist is an empty chain. Any other read error
    # raises: an unreadable ledger read as empty would fork the chain at GENESIS.
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None  # sbe: allow-silent only an ABSENT ledger is an empty chain; every other read error raises
    if not lines:
        return None
    return lines[-1].rstrip('\n')


def _require_path(path):
    """The one place a ledger path is judged: text, and not empty. Every entry point routes through here."""
    if isinstance(path, bool) or not isinstance(path, str) or not path:
        raise ValueError("ledger path must be a non-empty string, got %r" % (path,))
    return path


def append_ledger(path, record):
    """Append one validated record, chaining prev_hash from the last line.

    Refuses (raises ValueError) on invalid or stale input.
    """
    _require_path(path)
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    record = dict(record)
    prev = record.get('prev_hash')
    # One writer at a time from reading the last line to writing the next:
    # the fast gate runs checks side by side and each appends here, and two
    # unlocked writers would both chain to the same last line (measured
    # 2026-09-26: 8 writers of 25 records forked the chain on 3 of 3 runs).
    # A fresh checkout has no logs/ (the ledger is gitignored), so the first append used to be refused with ENOENT in
    # every clean worktree. A parent that cannot be made still raises OSError: unwritable blocks, it never reads as written.
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        last_line = _read_last_line(path)
        expected_prev = GENESIS if last_line is None else _line_hash(json.loads(last_line))
        if prev is None:
            record['prev_hash'] = expected_prev
        elif prev != expected_prev:
            raise ValueError("prev_hash mismatch")
        _validate(record)
        line = _canonical(record)
        f.write(line + '\n')
        f.flush()


def verify_chain(path):
    """Return True only if every line validates and prev_hash chains exactly.

    A non-str path is refused with ValueError.  A missing or empty file is a
    refusal (False), never True.
    """
    _require_path(path)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return False
    prev = GENESIS
    for raw in lines:
        line = raw.rstrip('\n')
        if not line:
            return False
        try:
            record = json.loads(line)
        except Exception:
            return False
        try:
            _validate(record)
        except ValueError:
            return False
        if record.get('prev_hash') != prev:
            return False
        prev = _line_hash(record)
    return True


# G11 (L30): "fix one thing and break three". A whole required_fast run whose every row passed, on a checkout whose
# tree is exactly HEAD^{tree}, appends ONE record here; the pre-push gate refuses a loop change whose pushed tree has
# none. The record rides the same schema, chain and lock as every other line of this ledger.
WHOLE_GATE = 'required-fast-whole-gate'
DEFAULT_LEDGER = os.path.join('logs', 'gate-ledger.jsonl')
NO_CUT = '0' * 32   # cut_id outside a cut: the schema wants 32 hex, and this says "no cut" rather than inventing one


def cut_id_or_none(env):
    """env['CUT_ID'] when it is 32 lowercase hex, else NO_CUT. Both ledger writers (record_whole_gate and the per row
    block in required_fast.sh) route through here: outside a cut CUT_ID is unset or empty, and the raw value used to be
    refused on every row. A bad id reads NO_CUT, never a cut it is not. A non mapping env raises ValueError."""
    if not hasattr(env, 'get') or not hasattr(env, 'keys'):
        raise ValueError("env must be a mapping, got %s" % type(env).__name__)
    cut_id = env.get('CUT_ID', '')
    return cut_id if _is_hex(cut_id, 32) else NO_CUT


# The whole-gate record proves a fact about a TREE, not about a folder: one worktree runs the gate and another worktree
# of the same repository (scripts/loop/redeploy.sh) pushes the same tree. So the record lives in ONE file in the
# repository's common git directory, which every worktree shares and no landing tree or build sandbox can write.
WHOLE_GATE_FILE = 'brother-whole-gate.jsonl'


def ledger_path(root):
    """The checkout's own ledger: GATE_LEDGER_PATH (relative to root) or the default. Whole-gate records were kept here
    before WHOLE_GATE_FILE; the push gate still accepts one found here for the 1.1.5 release (whole_gate_pass_in_repo)."""
    return os.path.join(root, os.environ.get('GATE_LEDGER_PATH') or DEFAULT_LEDGER)


def whole_gate_ledger(repo):
    """(path, '') of the one whole-gate record file every worktree of repo shares, in git's common directory, or
    (None, why) when git cannot name that directory or it is not a readable directory. The writer and the push gate
    both call this, so they cannot disagree about where the record is."""
    common = (_git_out(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir') or '').strip()
    if not common:
        return None, 'git could not name the common git directory of %s' % repo
    common = os.path.realpath(os.path.join(os.path.abspath(repo), common))
    if not os.path.isdir(common) or not os.access(common, os.R_OK | os.X_OK):
        return None, 'the common git directory %s is not a readable directory' % common
    return os.path.join(common, WHOLE_GATE_FILE), ''


def _git_out(repo, *args):
    """stdout of one git call in repo, or None when git could not run or exited non zero."""
    import subprocess
    try:
        proc = subprocess.run(['git', '-C', repo] + list(args), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def tree_snapshot(repo):
    """(HEAD^{tree}, '') for a clean checkout, else (None, why). An untracked file counts as dirty: a module nobody
    committed can make a check pass that the committed tree alone would fail."""
    status = _git_out(repo, 'status', '--porcelain')
    if status is None:
        return None, 'git status could not be read in %s' % repo
    if status.strip():
        return None, ('the checkout has uncommitted changes (%s), so no tree describes what ran'
                      % status.splitlines()[0].strip())
    tree = (_git_out(repo, 'rev-parse', 'HEAD^{tree}') or '').strip()
    if not _is_hex(tree, 40):
        return None, 'HEAD^{tree} could not be read in %s' % repo
    return tree, ''


def record_whole_gate(path, repo, codes_text, start_tree, obligation, started, worktree_key='UNKNOWN'):
    """Append the whole-gate PASS record and return (True, sentence), or write nothing and return (False, why).

    codes_text is the gate's own rows, one 'name TAB exit_code' per line. Every row must be exit 0 (a FAIL or a
    NO-DATA row writes nothing), the evidence obligation step must have exited 0, start_tree must be the clean
    snapshot taken before any check ran, and HEAD^{tree} must still be that tree now. Anything unknown writes nothing."""
    rows = [line for line in (codes_text or '').splitlines() if line.strip()]
    if not rows:
        return False, 'the gate recorded no rows'
    for line in rows:
        parts = line.split('\t')
        if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
            return False, 'a row is not "name TAB exit code": %r' % line[:80]
        if parts[1] != '0':
            return False, 'row %s exited %s' % (parts[0], parts[1])
    if obligation != '0':
        return False, 'the evidence obligation step exited %r' % (obligation,)
    # One guard covers both: an empty start (dirty or unreadable checkout) never equals a real tree, and a start
    # git cannot read again now is refused by the schema's own 40 hex rule below. THE CHECKOUT MUST STILL BE CLEAN
    # (security review 2026-10-16, finding 3): HEAD^{tree} alone stayed equal while a tracked edit or an untracked
    # file made during the run changed what ran, and the record then vouched for a tree nobody tested. The same
    # snapshot as the start (git status, untracked included, ignored excluded); an unreadable status writes nothing.
    end_tree, end_why = tree_snapshot(repo)
    if end_tree != start_tree:
        return False, ('no clean tree was read before the checks ran' if not start_tree else
                       'at record time %s' % end_why if end_tree is None else
                       'HEAD^{tree} moved during the run (%s, now %r)' % (start_tree, end_tree))
    try:
        started = float(started)
        with open(os.path.join(repo, 'scripts', 'required_fast.sh'), 'rb') as handle:
            code_hash = hashlib.sha256(handle.read()).hexdigest()
    except (TypeError, ValueError, OSError) as exc:
        return False, 'the run could not be described: %s' % exc
    import platform
    import time
    ended = max(time.time(), started)
    record = {
        'schema': 1, 'cut_id': cut_id_or_none(os.environ),
        'cut_version': os.environ.get('CUT_VERSION') or 'UNKNOWN', 'gate_name': WHOLE_GATE,
        'gate_argv': ['sh', 'scripts/required_fast.sh'], 'status': 'PASS', 'exit_code': 0,
        'started_unix': started, 'ended_unix': ended, 'duration_s': ended - started,
        'worktree_key': worktree_key or 'UNKNOWN', 'tree_sha': start_tree, 'inputs_hash': None,
        'code_hash': code_hash, 'tool_versions': {'python3': 'Python ' + platform.python_version()},
        'verdict_source': 'full', 'cache_key': None, 'deadline_unix': None,
    }
    try:
        append_ledger(path, record)
    except (OSError, ValueError) as exc:
        return False, 'the ledger append was refused: %s' % exc
    return True, 'PASS record for tree %s (%d rows, %s) in %s' % (
        start_tree, len(rows), record['tool_versions']['python3'], path)


def whole_gate_pass_for_tree(path, tree_sha):
    """(True, sentence) only when the ledger at path chains end to end and holds a whole-gate PASS record for exactly
    tree_sha; (False, why) otherwise. Missing or empty, unreadable, corrupt, or a record for another tree: False."""
    if not _is_hex(tree_sha, 40):
        return False, 'the pushed tree could not be read (%r)' % (tree_sha,)
    if not os.path.lexists(path):
        return False, 'no whole-gate run is on record (%s does not exist)' % path
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (OSError, ValueError) as exc:
        return False, 'the gate ledger %s could not be read: %s' % (path, exc)
    if not text.strip():
        return False, 'no whole-gate run is on record (%s is empty)' % path
    if not verify_chain(path):
        return False, 'the gate ledger %s is corrupt or does not chain, so no record in it is trusted' % path
    try:   # a writer appending between the read and the chain check: what was read is judged, and refused if torn
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError as exc:
        return False, 'the gate ledger %s changed while it was read: %s' % (path, exc)
    others = []
    for record in records:
        if isinstance(record, dict) and record.get('gate_name') == WHOLE_GATE and record.get('status') == 'PASS':
            if record.get('tree_sha') == tree_sha:
                return True, 'whole-gate PASS on record for tree %s (%s)' % (
                    tree_sha, record.get('tool_versions', {}).get('python3', 'interpreter unknown'))
            others.append(record.get('tree_sha'))
    if others:
        return False, 'the newest whole-gate PASS record is for tree %s, not the pushed tree %s' % (others[-1], tree_sha)
    return False, 'no whole-gate PASS record in %s' % path


# C8 (1.1.6): the only delta that reuses a whole-gate record. Two markdown prefixes, never docs/plan whole: its JSON
# files (BATTERY-EXPECTATIONS.json, KEY-COMPONENTS.json, loop-roles.json) are read by gate rows. No gate row reads these
# two, and scripts/test_whole_gate_docs_delta.py greps required_fast.sh and every script it names to keep it so.
DOCS_DELTA_PREFIXES = ('docs/plan/specs/', 'docs/plan/seeds/')
_REGULAR_MODES = ('000000', '100644', '100755')     # absent, file, executable file: never a symlink or a submodule


def docs_only_delta(changed):
    """True only when changed is a non empty list of normalised relative paths, each under DOCS_DELTA_PREFIXES with a
    .md suffix (case sensitive) and no control character. Anything else, including an empty list, is False."""
    if not isinstance(changed, list) or not changed:
        return False
    for p in changed:
        if not isinstance(p, str) or not p.startswith(DOCS_DELTA_PREFIXES) or not p.endswith('.md'):
            return False
        if os.path.normpath(p) != p or any(ord(c) < 32 or ord(c) == 127 for c in p):
            return False
    return True


def whole_gate_pass_for_delta(path, tree_sha, recorded_tree, changed):
    """(True, sentence) only when the ledger at path holds a whole-gate PASS for recorded_tree (whole_gate_pass_for_tree:
    the chain is checked, a tampered or unreadable ledger refuses) and changed, the delta from recorded_tree to tree_sha,
    is docs_only_delta. (False, why) otherwise."""
    if not docs_only_delta(changed):
        shown = [p for p in changed if not docs_only_delta([p])] if isinstance(changed, list) else []
        return False, 'the delta from recorded tree %s is not only %s markdown (%s)' % (
            recorded_tree, ' or '.join(DOCS_DELTA_PREFIXES), ', '.join(repr(p) for p in shown[:3]) or 'empty')
    ok, why = whole_gate_pass_for_tree(path, recorded_tree)
    if not ok:
        return False, why
    return True, '%s; the pushed tree %s differs from it in %d file(s), a docs only delta' % (why, tree_sha, len(changed))


def _newest_pass_tree(path):
    """tree_sha of the newest whole-gate PASS record at path, or None when there is none or the file cannot be read."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            records = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return None
    trees = [r.get('tree_sha') for r in records if isinstance(r, dict)
             and r.get('gate_name') == WHOLE_GATE and r.get('status') == 'PASS']
    return trees[-1] if trees and _is_hex(trees[-1], 40) else None


def _delta_paths(repo, old_tree, new_tree):
    """Paths that differ between two trees (git diff --raw -z, renames off), or None when git cannot say, the output
    does not parse, or an entry's new side is not a regular file (a symlink or a submodule is never a docs delta)."""
    try:
        out = _git_out(repo, 'diff', '--raw', '-z', '--no-renames', '--no-ext-diff', old_tree, new_tree)
    except ValueError:      # a name git printed that does not decode: not a delta anyone can judge
        return None
    if out is None:
        return None
    fields = out.split('\0')
    if fields and fields[-1] == '':
        fields.pop()
    if len(fields) % 2:
        return None
    paths = []
    for meta, name in zip(fields[0::2], fields[1::2]):
        parts = meta.split(' ')
        if len(parts) != 5 or not parts[0].startswith(':') or parts[1] not in _REGULAR_MODES:
            return None
        paths.append(name)
    return paths


def whole_gate_pass_in_repo(repo, tree_sha):
    """(True, sentence naming the file the record came from) when the repository holding the checkout repo has a
    whole-gate PASS record for exactly tree_sha; (False, why) otherwise. The shared file is asked first. For the 1.1.5
    release only, a record in the checkout's own ledger (written before the shared file existed) is also accepted.
    A common directory git cannot name refuses outright, and a shared file that exists but does not chain is refused,
    never stepped over to the old ledger. C8: with no exact record anywhere, the newest PASS in the shared file still
    counts when the delta from its tree to tree_sha is docs_only_delta (whole_gate_pass_for_delta)."""
    shared, why = whole_gate_ledger(repo)
    if shared is None:
        return False, 'the shared whole-gate record cannot be read: %s' % why
    ok, said = whole_gate_pass_for_tree(shared, tree_sha)
    if ok:
        return True, '%s, from %s' % (said, shared)
    if os.path.lexists(shared) and not verify_chain(shared):
        return False, said
    old = ledger_path(repo)
    ok, said_old = whole_gate_pass_for_tree(old, tree_sha)
    if ok:
        return True, '%s, from the checkout ledger %s (a record from before the shared file, accepted in 1.1.5 only)' % (
            said_old, old)
    recorded = _newest_pass_tree(shared)        # C8: no exact record, so ask the docs delta rule of the newest PASS
    if recorded is not None and _is_hex(tree_sha, 40):     # never an option or junk handed to git diff
        changed = _delta_paths(repo, recorded, tree_sha)
        if changed is None:
            said = '%s; the delta from recorded tree %s could not be read as regular files' % (said, recorded)
        else:
            ok, said_delta = whole_gate_pass_for_delta(shared, tree_sha, recorded, changed)
            if ok:
                return True, '%s, from %s' % (said_delta, shared)
            said = '%s; %s' % (said, said_delta)
    return False, '%s; the checkout ledger: %s' % (said, said_old)


def _main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Append one gate ledger record from JSON on stdin or --json.")
    parser.add_argument('--path', default=os.environ.get('GATE_LEDGER_PATH', 'logs/gate-ledger.jsonl'))
    parser.add_argument('--json', help='JSON record text')
    # G11: the two calls scripts/required_fast.sh makes, run from the checkout root.
    parser.add_argument('--whole-gate-snapshot', action='store_true',
                        help='print HEAD^{tree} of a clean checkout (exit 0), or say why not (exit 2)')
    parser.add_argument('--whole-gate-record', action='store_true',
                        help='append the whole-gate PASS record when every row in --codes passed (exit 0), else exit 2')
    parser.add_argument('--codes', help='the gate rows file, one "name TAB exit code" per line')
    parser.add_argument('--tree', default='', help='the snapshot taken before any check ran')
    parser.add_argument('--obligation', default='', help='the evidence obligation step exit code')
    parser.add_argument('--started', default='', help='unix seconds the run started')
    parser.add_argument('--worktree-key', default='UNKNOWN')
    args = parser.parse_args(argv)
    if args.whole_gate_snapshot:
        tree, why = tree_snapshot('.')
        if tree is None:
            sys.stderr.write('NO-DATA: whole-gate: this run cannot earn a PASS record: %s\n' % why)
            return 2
        print(tree)
        return 0
    if args.whole_gate_record:
        try:
            with open(args.codes or '', 'r', encoding='utf-8') as handle:
                codes_text = handle.read()
        except (OSError, ValueError) as exc:
            codes_text, why = None, 'the gate rows could not be read: %s' % exc
        written = False
        if codes_text is not None:
            shared, why = whole_gate_ledger('.')
            if shared is not None:
                written, why = record_whole_gate(shared, '.', codes_text, args.tree, args.obligation,
                                                 args.started, args.worktree_key)
        print('whole-gate: %s%s' % ('' if written else 'no PASS record: ', why))
        return 0 if written else 2
    if args.json:
        record = json.loads(args.json)
    else:
        record = json.load(sys.stdin)
    append_ledger(args.path, record)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
