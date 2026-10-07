#!/usr/bin/env python3
'''l0_scan: block the ship when a quarantined term hides in a name.

Unit L0.2, privacy and history guard. A restricted term must never leave
this machine inside a filename, in the tree HEAD points at, in the working
tree, or anywhere in reachable history. This module never holds the term:
it compares sha256 digests of canonical name candidates against a canary
list, so nothing it prints or records can carry the term.

check_quarantine returns:
  0  every population was scanned and no candidate digest matched
  2  BLOCKED, because a name matched, because the token hash list is
     missing, empty or malformed, or because the repository could not be
     read completely

Unknown, corrupt or missing input is never the clean case here. A scan with
no canaries can never find anything, so an empty canary list blocks instead
of clearing, and an object store nobody can read blocks instead of
reporting a history with nothing in it.

Standard library only, and no subprocess: git objects are read directly
(loose objects and packfiles, version 2 indexes), so the guard needs
nothing but file I/O. Nothing under the worktree is renamed or deleted;
the only file written is the record path the caller names, and that record
holds sha256 digests and counts only.
'''
import hashlib
import json
import os
import re
import zlib
from pathlib import Path

__all__ = [
    'load_token_hash_list',
    'canonical_candidates',
    'scan_names',
    'scan_history',
    'check_quarantine',
]

_HEX64 = re.compile('[0-9a-f]{64}')
_SHA1_HEX = re.compile('[0-9a-f]{40}')
_NON_TOKEN = re.compile('[^0-9A-Za-z]+')
_PACK_KINDS = {1: 'commit', 2: 'tree', 3: 'blob', 4: 'tag'}
_JOINERS = ('-', '_', '', ' ', '.')
_NUL = bytes([0])


class ScanRefused(ValueError):
    '''A scan could not be completed, or an input was not usable at all.
    Every caller blocks on this; it is never a clean result.'''


def _sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()


def _as_path(value):
    if isinstance(value, (str, os.PathLike)):
        try:
            return Path(value)
        except (TypeError, ValueError):
            return None
    return None


def load_token_hash_list(path: Path) -> list[str]:
    '''Every sha256 digest in the token hash file, in file order.

    The file is read as bytes and decoded strictly. A missing file, a
    directory, an unreadable path, bytes that are not utf-8, a file with no
    digests at all, or a file carrying one malformed line all return [] (an
    unknown list), which the caller turns into a block. A scan with no
    canaries can never find anything, so an empty answer is never the clean
    answer.'''
    target = _as_path(path)
    if target is None:
        return []
    try:
        raw = target.read_bytes()
    except (OSError, ValueError):
        return []
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return []
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if not _HEX64.fullmatch(line):
            return []
        if line not in found:
            found.append(line)
    return found


def canonical_candidates(path: str) -> list[str]:
    '''Canonical spellings of one name, so a digest can be compared without
    ever holding the term.

    A restricted term survives in a filename through separators and case, so
    the candidate set covers the whole name, the basename, the stem, their
    lowercased forms, and every run of consecutive word tokens of the stem
    joined by the separators a human uses. Order is deterministic and
    duplicates are dropped. A non-str, or a str carrying a NUL byte, is
    refused with ValueError; the empty string has no candidates and returns
    [].'''
    if not isinstance(path, str):
        raise ValueError('path must be a str, not %s' % type(path).__name__)
    if path == '':
        return []
    if chr(0) in path:
        raise ValueError('path must not carry a NUL byte')
    base = path.rsplit('/', 1)[-1]
    stem = base.rsplit('.', 1)[0] if '.' in base else base
    candidates = []
    seen = set()

    def add(text):
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    add(path)
    add(base)
    add(stem)
    add(path.lower())
    add(base.lower())
    add(stem.lower())
    words = [word for word in _NON_TOKEN.split(stem) if word]
    for start in range(len(words)):
        for stop in range(start + 1, len(words) + 1):
            window = words[start:stop]
            for joiner in _JOINERS:
                add(joiner.join(window))
                add(joiner.join(window).lower())
    return candidates


def _require_tokens(token_sha256_list):
    '''The canary set, or a refusal. An empty list is refused on purpose: a
    scan with no canaries can never find anything, so clearing on it would
    turn missing input into the safe case.'''
    if not isinstance(token_sha256_list, list):
        raise ValueError('token_sha256_list must be a list of sha256 digests')
    if not token_sha256_list:
        raise ValueError('empty token sha256 list: refusing to scan')
    tokens = set()
    for token in token_sha256_list:
        if not isinstance(token, str) or not _HEX64.fullmatch(token):
            raise ValueError('a token digest must be 64 lowercase hex characters')
        tokens.add(token)
    return tokens


def scan_names(paths: list[str], token_sha256_list: list[str]) -> list[str]:
    '''The names whose canonical candidates hash to a canary.

    Every element of paths must be a str and every canary must be a sha256
    digest; anything else is refused with ValueError rather than skipped,
    because a skipped element is a name nobody checked. An empty canary list
    is refused too: it can only ever answer no hits.'''
    if not isinstance(paths, list):
        raise ValueError('paths must be a list of str')
    tokens = _require_tokens(token_sha256_list)
    hits = []
    for one in paths:
        if not isinstance(one, str):
            raise ValueError('every path must be a str, not %s'
                             % type(one).__name__)
        for candidate in canonical_candidates(one):
            if _sha256_hex(candidate) in tokens:
                hits.append(one)
                break
    return hits


def _read_text(path):
    try:
        return path.read_text(encoding='utf-8', errors='surrogateescape').strip()
    except (OSError, ValueError):
        return None


def _git_dirs(worktree):
    '''(git_dir, common_dir) for one worktree, or None when there is no git
    metadata to read. A .git file (a linked worktree) is followed and
    commondir is honoured, so the object store of the main checkout is
    found.'''
    root = _as_path(worktree)
    if root is None:
        return None
    dot = root / '.git'
    if dot.is_dir():
        git_dir = dot
    elif dot.is_file():
        text = _read_text(dot)
        if text is None or not text.startswith('gitdir:'):
            return None
        target = Path(text.split(':', 1)[1].strip())
        git_dir = target if target.is_absolute() else (root / target)
    else:
        return None
    common = git_dir
    marker = git_dir / 'commondir'
    if marker.is_file():
        relative = _read_text(marker)
        if not relative:
            return None
        candidate = Path(relative)
        common = candidate if candidate.is_absolute() else (git_dir / candidate)
    common = Path(os.path.normpath(str(common)))
    if not (common / 'objects').is_dir():
        return None
    return git_dir, common


def _packed_ref(common, git_dir, ref):
    for base in (common, git_dir):
        text = _read_text(base / 'packed-refs')
        if text is None:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('^'):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def _resolve_ref(git_dir, common, ref):
    '''The sha a ref names, following symbolic refs, or None when the name
    does not resolve here (which the caller turns into a block).'''
    if not isinstance(ref, str) or not ref:
        return None
    current = ref
    seen = set()
    while current and current not in seen:
        seen.add(current)
        if _SHA1_HEX.fullmatch(current):
            return current
        text = _read_text(git_dir / current)
        if text is None and current != 'HEAD':
            text = _read_text(common / current)
        if text is None:
            return _packed_ref(common, git_dir, current)
        if text.startswith('ref:'):
            current = text.split(':', 1)[1].strip()
            continue
        return text if _SHA1_HEX.fullmatch(text) else None
    return None


def _inflate(handle):
    engine = zlib.decompressobj()
    chunks = []
    try:
        while True:
            block = handle.read(65536)
            if not block:
                break
            chunks.append(engine.decompress(block))
            if engine.eof:
                break
    except zlib.error:
        raise ScanRefused('a compressed object stream is not valid zlib')
    if not engine.eof:
        raise ScanRefused('a compressed object stream ended early')
    return b''.join(chunks)


def _apply_delta(base, delta):
    pos = 0
    size = len(delta)

    def read_varint():
        nonlocal pos
        value = 0
        shift = 0
        while True:
            if pos >= size:
                raise ScanRefused('a pack delta is truncated')
            byte = delta[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return value

    base_size = read_varint()
    result_size = read_varint()
    if base_size != len(base):
        raise ScanRefused('a pack delta names the wrong base size')
    out = bytearray()
    while pos < size:
        byte = delta[pos]
        pos += 1
        if byte & 0x80:
            offset = 0
            length = 0
            for index in range(4):
                if byte & (1 << index):
                    if pos >= size:
                        raise ScanRefused('a pack delta is truncated')
                    offset |= delta[pos] << (8 * index)
                    pos += 1
            for index in range(3):
                if byte & (0x10 << index):
                    if pos >= size:
                        raise ScanRefused('a pack delta is truncated')
                    length |= delta[pos] << (8 * index)
                    pos += 1
            if length == 0:
                length = 0x10000
            if offset + length > len(base):
                raise ScanRefused('a pack delta copies past its base')
            out += base[offset:offset + length]
        elif byte:
            if pos + byte > size:
                raise ScanRefused('a pack delta is truncated')
            out += delta[pos:pos + byte]
            pos += byte
        else:
            raise ScanRefused('a pack delta carries the reserved opcode')
    if len(out) != result_size:
        raise ScanRefused('a pack delta produced the wrong size')
    return bytes(out)


class _PackIndex:
    '''One version 2 pack index. Version 1 indexes are refused: reading them
    wrong would silently lose objects, and a lost object is exactly the
    no names therefore clean answer this guard exists to prevent.'''

    def __init__(self, idx_path):
        self.path = idx_path
        try:
            raw = idx_path.read_bytes()
        except OSError:
            raise ScanRefused('pack index %s could not be read' % idx_path.name)
        if raw[:4] != b'\xfftOc' or raw[4:8] != bytes([0, 0, 0, 2]):
            raise ScanRefused('pack index %s is not version 2' % idx_path.name)
        if len(raw) < 8 + 1024:
            raise ScanRefused('pack index %s is truncated' % idx_path.name)
        self.count = int.from_bytes(raw[8 + 255 * 4:8 + 256 * 4], 'big')
        position = 8 + 1024
        end = position + 20 * self.count
        if end > len(raw):
            raise ScanRefused('pack index %s is truncated' % idx_path.name)
        self.names = raw[position:end]
        position = end + 4 * self.count
        end = position + 4 * self.count
        if end > len(raw):
            raise ScanRefused('pack index %s is truncated' % idx_path.name)
        self.offsets = raw[position:end]
        self.big = raw[end:]
        self.pack_path = idx_path.with_suffix('.pack')

    def offset_of(self, sha_hex):
        target = bytes.fromhex(sha_hex)
        low, high = 0, self.count
        while low < high:
            middle = (low + high) // 2
            current = self.names[middle * 20:(middle + 1) * 20]
            if current < target:
                low = middle + 1
            elif current > target:
                high = middle
            else:
                offset = int.from_bytes(
                    self.offsets[middle * 4:middle * 4 + 4], 'big')
                if offset & 0x80000000:
                    slot = offset & 0x7FFFFFFF
                    big = self.big[slot * 8:slot * 8 + 8]
                    if len(big) != 8:
                        raise ScanRefused('pack index %s is truncated'
                                          % self.path.name)
                    return int.from_bytes(big, 'big')
                return offset
        return None


class _ObjectStore:
    '''Read-only access to one object store: loose objects first, then every
    version 2 pack index beside them. Any object that cannot be read raises
    ScanRefused, because an unreadable object must never be counted as an
    object that carries no names.'''

    def __init__(self, common_dir):
        self.common = Path(common_dir)
        self._indexes = None
        self._trees = {}
        self._paths = {}

    def _pack_indexes(self):
        if self._indexes is None:
            found = []
            packdir = self.common / 'objects' / 'pack'
            if packdir.is_dir():
                try:
                    names = sorted(os.listdir(str(packdir)))
                except OSError:
                    raise ScanRefused('objects/pack could not be listed')
                for name in names:
                    if name.endswith('.idx'):
                        found.append(_PackIndex(packdir / name))
            self._indexes = found
        return self._indexes

    def object(self, sha_hex):
        if not isinstance(sha_hex, str) or not _SHA1_HEX.fullmatch(sha_hex):
            raise ScanRefused('an object name that is not a sha1 was requested')
        kind, body = self._loose(sha_hex)
        if kind is not None:
            return kind, body
        for index in self._pack_indexes():
            offset = index.offset_of(sha_hex)
            if offset is not None:
                return self._packed(index.pack_path, offset)
        raise ScanRefused('object %s is not in this object store' % sha_hex)

    def _loose(self, sha_hex):
        path = self.common / 'objects' / sha_hex[:2] / sha_hex[2:]
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None, None
        except OSError:
            raise ScanRefused('loose object %s could not be read' % sha_hex)
        try:
            data = zlib.decompress(raw)
        except zlib.error:
            raise ScanRefused('loose object %s is not valid zlib' % sha_hex)
        head_end = data.find(_NUL)
        if head_end < 0:
            raise ScanRefused('loose object %s carries no header' % sha_hex)
        header = data[:head_end].split(b' ')
        if len(header) != 2:
            raise ScanRefused('loose object %s carries a malformed header'
                              % sha_hex)
        kind = header[0].decode('ascii', 'replace')
        try:
            size = int(header[1])
        except ValueError:
            raise ScanRefused('loose object %s carries a malformed size'
                              % sha_hex)
        body = data[head_end + 1:]
        if len(body) != size:
            raise ScanRefused('loose object %s is truncated' % sha_hex)
        return kind, body

    def _packed(self, pack_path, offset, depth=0):
        if depth > 64:
            raise ScanRefused('a pack delta chain is deeper than 64')
        try:
            handle = open(str(pack_path), 'rb')
        except OSError:
            raise ScanRefused('pack %s could not be opened' % pack_path.name)
        with handle:
            handle.seek(offset)
            head = handle.read(1)
            if not head:
                raise ScanRefused('a pack entry is truncated')
            byte = head[0]
            kind = (byte >> 4) & 0x07
            size = byte & 0x0F
            shift = 4
            while byte & 0x80:
                more = handle.read(1)
                if not more:
                    raise ScanRefused('a pack entry header is truncated')
                byte = more[0]
                size |= (byte & 0x7F) << shift
                shift += 7
            if kind == 6:
                more = handle.read(1)
                if not more:
                    raise ScanRefused('a pack delta offset is truncated')
                byte = more[0]
                back = byte & 0x7F
                while byte & 0x80:
                    more = handle.read(1)
                    if not more:
                        raise ScanRefused('a pack delta offset is truncated')
                    byte = more[0]
                    back = ((back + 1) << 7) | (byte & 0x7F)
                delta = _inflate(handle)
                base_kind, base = self._packed(pack_path, offset - back,
                                               depth + 1)
                return base_kind, _apply_delta(base, delta)
            if kind == 7:
                base_name = handle.read(20)
                if len(base_name) != 20:
                    raise ScanRefused('a pack base reference is truncated')
                delta = _inflate(handle)
                base_kind, base = self.object(base_name.hex())
                return base_kind, _apply_delta(base, delta)
            if kind not in _PACK_KINDS:
                raise ScanRefused('a pack entry has type %d' % kind)
            data = _inflate(handle)
        if len(data) != size:
            raise ScanRefused('a pack entry does not match its own size')
        return _PACK_KINDS[kind], data

    def tree_entries(self, sha_hex):
        if sha_hex in self._trees:
            return self._trees[sha_hex]
        kind, body = self.object(sha_hex)
        if kind != 'tree':
            raise ScanRefused('object %s is a %s, not a tree'
                              % (sha_hex, kind))
        entries = []
        pos = 0
        end = len(body)
        while pos < end:
            space = body.find(b' ', pos)
            if space < 0:
                raise ScanRefused('tree %s carries a malformed entry' % sha_hex)
            head_end = body.find(_NUL, space + 1)
            if head_end < 0 or head_end + 21 > end:
                raise ScanRefused('tree %s carries a truncated entry' % sha_hex)
            mode = body[pos:space].decode('ascii', 'replace')
            name = body[space + 1:head_end].decode('utf-8', 'surrogateescape')
            sub = body[head_end + 1:head_end + 21].hex()
            entries.append((mode, name, sub))
            pos = head_end + 21
        self._trees[sha_hex] = entries
        return entries

    def paths(self, tree_sha, prefix=''):
        key = (tree_sha, prefix)
        if key in self._paths:
            return self._paths[key]
        out = []
        for mode, name, sub in self.tree_entries(tree_sha):
            full = name if not prefix else prefix + '/' + name
            if mode in ('40000', '040000'):
                out.extend(self.paths(sub, full))
            else:
                out.append(full)
        self._paths[key] = out
        return out

    def commit(self, sha_hex):
        kind, body = self.object(sha_hex)
        if kind != 'commit':
            raise ScanRefused('object %s is a %s, not a commit'
                              % (sha_hex, kind))
        tree = None
        parents = []
        for line in body.split(b'\n'):
            if line.startswith(b'tree '):
                candidate = line[5:45].decode('ascii', 'replace')
                if _SHA1_HEX.fullmatch(candidate):
                    tree = candidate
            elif line.startswith(b'parent '):
                candidate = line[7:47].decode('ascii', 'replace')
                if _SHA1_HEX.fullmatch(candidate):
                    parents.append(candidate)
            elif not line.strip():
                break
        if tree is None:
            raise ScanRefused('commit %s carries no readable tree' % sha_hex)
        return tree, parents


def _worktree_names(worktree):
    '''Every file name in the working tree, relative and slash separated,
    with the git metadata directory left out.'''
    root = str(worktree)
    names = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [one for one in dirs if one != '.git']
        relative = os.path.relpath(base, root)
        for name in files:
            if relative == '.':
                names.append(name)
            else:
                names.append((relative + '/' + name).replace(os.sep, '/'))
    return names


def _write_record(record_path, record):
    target = _as_path(record_path)
    if target is None:
        return False
    try:
        parent = target.parent
        if parent and not parent.is_dir():
            os.makedirs(str(parent), exist_ok=True)
        temporary = target.with_name(target.name + '.part')
        with open(str(temporary), 'w', encoding='utf-8') as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write(chr(10))
        os.replace(str(temporary), str(target))
        return True
    except (OSError, ValueError, TypeError):
        return False


def scan_history(worktree: Path, token_sha256_list: list[str],
                 base_ref: str, head_ref: str) -> list[str]:
    '''Every name reachable from head_ref (and, when base_ref is empty, from
    the root commit) whose canonical candidates hash to a canary.

    The repository is read directly, object by object. A missing git
    directory, an unresolvable ref, an unreadable object or a packfile that
    cannot be parsed raises ScanRefused: a history nobody can read must
    never be reported as a history with nothing in it.'''
    if _as_path(worktree) is None:
        raise ValueError('worktree must be a path')
    if not isinstance(base_ref, str) or not isinstance(head_ref, str):
        raise ValueError('base_ref and head_ref must be str')
    tokens = _require_tokens(token_sha256_list)
    located = _git_dirs(worktree)
    if located is None:
        raise ScanRefused('no readable git directory in the worktree')
    git_dir, common = located
    head = _resolve_ref(git_dir, common, head_ref)
    if head is None:
        raise ScanRefused('head ref does not resolve')
    stop = set()
    if base_ref:
        base = _resolve_ref(git_dir, common, base_ref)
        if base is None:
            raise ScanRefused('base ref does not resolve')
        stop.add(base)
    store = _ObjectStore(common)
    seen = set()
    hits = []
    stack = [head]
    while stack:
        sha = stack.pop()
        if sha in seen or sha in stop:
            continue
        seen.add(sha)
        tree, parents = store.commit(sha)
        for name in store.paths(tree):
            for candidate in canonical_candidates(name):
                if _sha256_hex(candidate) in tokens:
                    if name not in hits:
                        hits.append(name)
                    break
        stack.extend(parents)
    return hits


def check_quarantine(worktree: Path, record_path: Path,
                     token_hash_path: Path) -> int:
    '''0 when every population was scanned and nothing matched; 2 (BLOCKED)
    otherwise.

    Populations: the tree HEAD points at, the working tree, and every commit
    reachable from HEAD. A token hash list that is missing, empty or
    malformed blocks before any scan runs. The term is never printed, never
    written and never recovered from a digest: the record carries sha256
    digests of matched names only, and nothing under the worktree is renamed
    or deleted.'''
    if _as_path(worktree) is None or _as_path(record_path) is None \
            or _as_path(token_hash_path) is None:
        print('BLOCKED: l0_scan needs three paths; refusing to scan')
        return 2
    tokens = load_token_hash_list(token_hash_path)
    if not tokens:
        _write_record(record_path, {
            'blocked': True,
            'hits': [],
            'reason': 'token hash list missing, empty or malformed',
            'tokens': 0,
        })
        print('BLOCKED: the token hash list is missing, empty or malformed; '
              'nothing can be cleared')
        return 2
    try:
        located = _git_dirs(worktree)
        if located is None:
            raise ScanRefused('no readable git directory in the worktree')
        git_dir, common = located
        head = _resolve_ref(git_dir, common, 'HEAD')
        if head is None:
            raise ScanRefused('HEAD does not resolve in this worktree')
        store = _ObjectStore(common)
        tree, _parents = store.commit(head)
        tracked_hits = scan_names(store.paths(tree), tokens)
        work_hits = scan_names(_worktree_names(worktree), tokens)
        history_hits = scan_history(worktree, tokens, '', 'HEAD')
    except (ScanRefused, OSError, ValueError) as refused:
        _write_record(record_path, {
            'blocked': True,
            'hits': [],
            'reason': 'the repository could not be scanned completely',
            'tokens': len(tokens),
        })
        print('BLOCKED: the repository could not be scanned completely (%s)'
              % refused)
        return 2
    hits = []
    for name in list(tracked_hits) + list(work_hits) + list(history_hits):
        digest = _sha256_hex(name)
        if digest not in hits:
            hits.append(digest)
    if not _write_record(record_path, {
            'blocked': bool(hits),
            'hits': hits,
            'reason': 'token hash match' if hits else 'no token hash match',
            'tokens': len(tokens)}):
        print('BLOCKED: the quarantine record could not be written')
        return 2
    if hits:
        print('BLOCKED: %d name digest(s) match the token hash list'
              % len(hits))
        return 2
    print('CLEAR: no name in HEAD, the working tree or reachable history '
          'matches the token hash list')
    return 0
