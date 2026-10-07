'''L1.2 component map lock verifier.

Reads the Antigravity adapter spec as bytes and refuses unknown, corrupt or
missing input with ValueError. Returns True only when the component map
carries the six required rows and the skills row states SKILL.md with name
plus description frontmatter.
'''

import os
import re

REQUIRED_COMPONENTS = ('manifest', 'skills', 'agents', 'mcp', 'rules', 'hooks')

ROW_TOKENS = {
    'manifest': ('manifest',),
    'skills': ('skills',),
    'agents': ('agents',),
    'mcp': ('mcp',),
    'rules': ('rules',),
    'hooks': ('hooks',),
}

SKILL_ROW_TOKENS = ('SKILL.md', 'name', 'description', 'frontmatter')

_SECTION_RE = re.compile(r'^## +2[.] +Component Mapping', re.MULTILINE)

_PATH_ERROR = 'spec_path must be a filesystem path string'


def _read_spec_text(spec_path):
    if spec_path is None:
        raise ValueError(_PATH_ERROR)
    if isinstance(spec_path, bool):
        raise ValueError(_PATH_ERROR)
    try:
        path_value = os.fspath(spec_path)
    except (TypeError, ValueError):
        raise ValueError(_PATH_ERROR) from None
    if not isinstance(path_value, str) or path_value == '':
        raise ValueError('spec_path must be a non-empty filesystem path string')
    if not os.path.isfile(path_value):
        raise ValueError('spec file is missing or not a regular file: %s' % path_value)
    try:
        with open(path_value, 'rb') as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError('spec file could not be read: %s' % exc) from None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('spec file is not valid UTF-8') from None


def _component_rows(text):
    match = _SECTION_RE.search(text)
    if match is None:
        raise ValueError('component map section 2 missing')
    lines = []
    for line in text[match.start():].splitlines():
        if lines and (line.startswith('## ') or line.startswith('---')):
            break
        lines.append(line)
    rows = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue
        cells = [cell.strip() for cell in stripped.strip('|').split('|')]
        if not cells:
            continue
        if all(set(cell) <= set('-: ') for cell in cells):
            continue
        rows.append(cells)
    return rows


def _row_matches(row, tokens):
    if not row:
        return False
    first = row[0].lower()
    return all(token in first for token in tokens)


def check_component_map(spec_path):
    text = _read_spec_text(spec_path)
    rows = _component_rows(text)
    missing = []
    for name in REQUIRED_COMPONENTS:
        tokens = ROW_TOKENS[name]
        if not any(_row_matches(row, tokens) for row in rows):
            missing.append(name)
    if missing:
        raise ValueError('missing component map rows: %s' % ', '.join(missing))
    return True


def check_skill_format_row(spec_path):
    text = _read_spec_text(spec_path)
    rows = _component_rows(text)
    skills_row = None
    for row in rows:
        if row and 'skills' in row[0].lower():
            skills_row = row
            break
    if skills_row is None:
        raise ValueError('skills row missing from component map')
    joined = ' '.join(skills_row).lower()
    missing = [token for token in SKILL_ROW_TOKENS if token.lower() not in joined]
    if missing:
        raise ValueError('skills row missing required tokens: %s' % ', '.join(missing))
    return True
