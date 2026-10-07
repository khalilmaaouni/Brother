#!/usr/bin/env python3
'''bundle_mirror: check that bundle/runtime mirrors plugin/runtime byte for byte.

This module is the checker for sub unit D4.f. It never writes under bundle/:
the landing step regenerates the mirror. It reads the bundle manifest and
refuses any manifest that is absent, corrupt, or does not name each required
mirror. The manifest schema is deliberately narrow because the real manifest
was not shown; any manifest that does not match this narrow shape blocks.
'''
import json
import os
import sys

SOURCE_ROOT = 'plugin/runtime'
DEST_ROOT = 'bundle/runtime'
MANIFEST_NAME = 'RUNTIME-MANIFEST.json'
REQUIRED_MIRRORS = (
    'brother/core/registry.py',
    'brother/core/dream_record.py',
    'brother/core/test_dream_coverage.py',
)


def _require_nonempty_str(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError('%s must be a non-empty string' % label)
    if any(ord(ch) < 32 for ch in value):
        raise ValueError('%s must not contain control characters' % label)
    return value


def mirror_path(source):
    '''Map a plugin/runtime source path to its bundle/runtime dest path.'''
    source = _require_nonempty_str(source, 'source')
    normalized = source.replace('\\', '/')
    if normalized.startswith('/') or normalized.startswith('../') or '/../' in normalized or normalized == '..':
        raise ValueError('source must be a relative path without ..')
    prefix = SOURCE_ROOT + '/'
    if not normalized.startswith(prefix):
        raise ValueError('source must live under %s' % SOURCE_ROOT)
    rest = normalized[len(prefix):]
    if not rest or rest.endswith('/'):
        raise ValueError('source must name a file')
    return DEST_ROOT + '/' + rest


def check_mirror(source, dest):
    '''Return True when dest exists and matches source byte for byte.'''
    source = _require_nonempty_str(source, 'source')
    dest = _require_nonempty_str(dest, 'dest')
    if not os.path.isfile(source):
        return False
    if not os.path.isfile(dest):
        return False
    try:
        with open(source, 'rb') as source_handle:
            source_bytes = source_handle.read()
        with open(dest, 'rb') as dest_handle:
            dest_bytes = dest_handle.read()
    except OSError:
        return False
    if source_bytes != dest_bytes:
        return False
    return True


def check_manifest(manifest_path):
    '''Return True when the manifest names each required mirror source.'''
    manifest_path = _require_nonempty_str(manifest_path, 'manifest_path')
    if not os.path.isfile(manifest_path):
        return False
    try:
        with open(manifest_path, 'rb') as handle:
            raw = handle.read()
    except OSError:
        return False
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    mirrors = data.get('mirrors')
    if not isinstance(mirrors, list):
        return False
    listed = set()
    for item in mirrors:
        if not isinstance(item, str):
            return False
        listed.add(item.replace('\\', '/'))
    for rel in REQUIRED_MIRRORS:
        source = SOURCE_ROOT + '/' + rel
        if source not in listed:
            return False
    return True


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    repo = os.getcwd()
    manifest = os.path.join(repo, MANIFEST_NAME)
    if not os.path.isfile(manifest):
        sys.stderr.write('bundle_mirror: manifest missing: %s\n' % manifest)
        return 2
    if not check_manifest(manifest):
        sys.stderr.write('bundle_mirror: manifest does not name all required mirrors: %s\n' % manifest)
        return 2
    failed = False
    for rel in REQUIRED_MIRRORS:
        source = os.path.join(SOURCE_ROOT, rel)
        dest = mirror_path(source)
        if not check_mirror(source, dest):
            sys.stderr.write('bundle_mirror: mirror mismatch: %s -> %s\n' % (source, dest))
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
