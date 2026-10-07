#!/usr/bin/env python3
'''dream_gate_shadow: read-only shadow evaluation of a proposed gate order
(unit D13.3).

WHAT A SHADOW RUN IS. It answers one question without touching anything:
under the proposed order, what did each mandatory check cost in the fixture
logs of past runs? It reads files and returns a mapping of check name to mean
seconds. It holds no path to a gate file and writes nothing at all, which is
what R17 asks for.

FIXTURE LOG FORMAT, declared here because the specification names the
parameter and not the format: one check per line, whitespace separated,
three fields: check name, exit code 0..255, elapsed seconds (finite, >= 0).
Blank lines are skipped. Any other line shape is corrupt and blocks with
GateShadowError, and a check named in the order with no line at all blocks
too, because a mean over nothing is NO-DATA and never 0.0.

FAILURE DIRECTION. A hostile argument is refused with GateShadowError, itself
a ValueError. A missing, unreadable, non UTF 8 or corrupt file blocks. This
module reports no shadow result for data it could not read, and it stays
independent of the consult module so a broken consult cannot block a
read-only shadow.
'''
from __future__ import annotations

import math
import os

NODATA = 'NO-DATA'


class GateShadowError(ValueError):
    '''A deliberate refusal: a hostile argument or a corrupt fixture log.'''


def _require_order(value, label):
    '''A list of unique non-empty check names, or a refusal.

    A str is refused before iteration: a str is a sequence of characters and
    would otherwise pass as one check name per letter.
    '''
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise GateShadowError('%s: %s must be a list of check names' % (NODATA, label))
    names = []
    seen = set()
    for member in value:
        if not isinstance(member, str) or not member:
            raise GateShadowError('%s: %s member must be a non-empty string' % (NODATA, label))
        if member in seen:
            raise GateShadowError('%s: duplicate check name in %s' % (NODATA, label))
        seen.add(member)
        names.append(member)
    if not names:
        raise GateShadowError('%s: %s must not be empty' % (NODATA, label))
    return names


def _fixture_lines(path):
    '''Yield (check name, exit code, elapsed seconds) for one fixture log.

    Bytes are read first, so a file that is not utf-8 blocks instead of being
decoded into replacement characters and silently averaged.
    '''
    if os.path.isdir(path):
        raise GateShadowError('%s: fixture log path is a directory' % NODATA)
    try:
        with open(path, 'rb') as handle:
            body = handle.read()
    except OSError:
        raise GateShadowError('%s: fixture log is missing or unreadable' % NODATA) from None
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        raise GateShadowError('%s: fixture log is not utf-8' % NODATA) from None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3:
            raise GateShadowError('%s: fixture line %d must have three fields'
                                  % (NODATA, number))
        name, code_text, elapsed_text = parts
        try:
            exit_code = int(code_text)
        except ValueError:
            raise GateShadowError('%s: fixture line %d exit code is not an integer'
                                  % (NODATA, number)) from None
        if exit_code < 0 or exit_code > 255:
            raise GateShadowError('%s: fixture line %d exit code is outside 0..255'
                                  % (NODATA, number))
        try:
            elapsed = float(elapsed_text)
        except ValueError:
            raise GateShadowError('%s: fixture line %d elapsed is not a number'
                                  % (NODATA, number)) from None
        if not math.isfinite(elapsed) or elapsed < 0.0:
            raise GateShadowError('%s: fixture line %d elapsed must be finite and non-negative'
                                  % (NODATA, number))
        yield name, exit_code, elapsed


def shadow_run(order, fixture_logs):
    '''Mean seconds per check in the proposed order, and zero writes.

    R17: no gate order is written, and this module holds no path to one. A
    check in the order with no fixture line at all blocks: its mean is
    NO-DATA and never 0.0, which would read as instant and win the sort.
    '''
    order_names = _require_order(order, 'order')
    if isinstance(fixture_logs, (str, bytes)) or not isinstance(fixture_logs, (list, tuple)):
        raise GateShadowError('%s: fixture_logs must be a list or tuple of paths' % NODATA)
    if not fixture_logs:
        raise GateShadowError('%s: fixture_logs must not be empty' % NODATA)
    paths = []
    for path in fixture_logs:
        if not isinstance(path, str) or not path:
            raise GateShadowError('%s: every fixture log path must be a non-empty string' % NODATA)
        paths.append(path)
    runs = {name: 0 for name in order_names}
    seconds = {name: 0.0 for name in order_names}
    for path in paths:
        for name, exit_code, elapsed in _fixture_lines(path):
            if name not in runs:
                raise GateShadowError('%s: check %s is not in the shadow order' % (NODATA, name))
            runs[name] += 1
            seconds[name] += elapsed
    result = {}
    for name in order_names:
        if runs[name] < 1:
            raise GateShadowError('%s: no fixture data for check %s' % (NODATA, name))
        result[name] = seconds[name] / runs[name]
    return result


__all__ = ['GateShadowError', 'shadow_run']
