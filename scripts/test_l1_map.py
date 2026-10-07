#!/usr/bin/env python3
'''L1.2 component map lock tests.

The real spec is read only when present; every negative case builds its own
fixture under tempfile so the suite also runs in an export copy that does
not carry docs/.
'''

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from l1_map import check_component_map, check_skill_format_row

REPO_ROOT = os.path.dirname(_HERE)
SPEC_PATH = os.path.join(REPO_ROOT, 'docs', 'architecture', 'ANTIGRAVITY-ADAPTER-SPEC.md')


def _row(concept, equivalent, location, notes):
    return '| %s | %s | %s | %s |' % (concept, equivalent, location, notes)


def _skill_row(description=True):
    notes = 'YAML frontmatter (name, description) + markdown'
    if not description:
        notes = 'YAML frontmatter (name) + markdown'
    return _row('**Skills**', '`skills/<name>/SKILL.md`', '`bundle/.antigravity-plugin/skills/`', notes)


def _fixture_text(omit=(), replace_skill=None, extra_rows=()):
    nl = chr(10)
    rows = [
        _row('**Plugin Manifest**', '`plugin.json`', '`bundle/.antigravity-plugin/plugin.json`', 'manifest'),
        _skill_row(),
        _row('**Agents / Personas**', 'Skills & Rules', '`skills/<agent-name>/SKILL.md` + `rules/`', 'agents'),
        _row('**MCP Servers**', '`mcp_config.json`', '`bundle/.antigravity-plugin/mcp_config.json`', 'mcp'),
        _row('**Rules / Guidelines**', '`rules/*.md` / `AGENTS.md`', '`bundle/.antigravity-plugin/rules/`', 'rules'),
        _row('**Lifecycle Hooks**', '`hooks.json`', '`bundle/.antigravity-plugin/hooks.json`', 'hooks'),
    ]
    if replace_skill is not None:
        rows[1] = replace_skill
    kept = []
    for row in rows:
        first_cell = row.split('|')[1].lower()
        if any(token.lower() in first_cell for token in omit):
            continue
        kept.append(row)
    kept.extend(extra_rows)
    return (
        '## 2. Component Mapping: fixture' + nl
        + _row('Brother concept', 'Antigravity equivalent', 'Packaging location', 'Notes') + nl
        + '| :--- | :--- | :--- | :--- |' + nl
        + nl.join(kept) + nl
    )


class ComponentMapLockTests(unittest.TestCase):
    def _write_text(self, text):
        handle = tempfile.NamedTemporaryFile('wb', suffix='.md', delete=False)
        handle.write(text.encode('utf-8'))
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def _write_bytes(self, data):
        handle = tempfile.NamedTemporaryFile('wb', suffix='.md', delete=False)
        handle.write(data)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_map_has_six_rows(self):
        if os.path.isfile(SPEC_PATH):
            self.assertTrue(check_component_map(SPEC_PATH))
        missing_agents = self._write_text(_fixture_text(omit=('agents',)))
        with self.assertRaises(ValueError) as ctx:
            check_component_map(missing_agents)
        self.assertIn('agents', str(ctx.exception))

    def test_skill_format(self):
        if os.path.isfile(SPEC_PATH):
            self.assertTrue(check_skill_format_row(SPEC_PATH))

    def test_skill_format_missing_description(self):
        bad = self._write_text(_fixture_text(replace_skill=_skill_row(description=False)))
        with self.assertRaises(ValueError) as ctx:
            check_skill_format_row(bad)
        self.assertIn('description', str(ctx.exception))

    def test_partial_map_lists_missing_rows(self):
        partial = self._write_text(_fixture_text(omit=('agents', 'rules', 'hooks')))
        with self.assertRaises(ValueError) as ctx:
            check_component_map(partial)
        message = str(ctx.exception)
        for name in ('agents', 'rules', 'hooks'):
            self.assertIn(name, message)

    def test_extra_rows_allowed(self):
        extra = _row('**Extra Surface**', '`extra.json`', '`bundle/.antigravity-plugin/extra.json`', 'extra')
        fixture = self._write_text(_fixture_text(extra_rows=(extra,)))
        self.assertTrue(check_component_map(fixture))

    def test_manifest_and_skills_rows_required(self):
        fixture = self._write_text(_fixture_text(omit=('manifest', 'skills')))
        with self.assertRaises(ValueError) as ctx:
            check_component_map(fixture)
        message = str(ctx.exception)
        self.assertIn('manifest', message)
        self.assertIn('skills', message)

    def test_hostile_inputs_refused(self):
        for bad in (None, True, 1, 1.5, float('nan'), b'path', [], {}, set(), object()):
            with self.assertRaises(ValueError):
                check_component_map(bad)
            with self.assertRaises(ValueError):
                check_skill_format_row(bad)

        class BadPath:
            def __fspath__(self):
                raise TypeError('bad path')

        with self.assertRaises(ValueError):
            check_component_map(BadPath())
        with self.assertRaises(ValueError):
            check_skill_format_row(BadPath())

        with self.assertRaises(ValueError):
            check_component_map('')
        with self.assertRaises(ValueError):
            check_skill_format_row('')

        existing = self._write_text(_fixture_text())
        with self.assertRaises(ValueError):
            check_component_map(os.fsencode(existing))
        with self.assertRaises(ValueError):
            check_skill_format_row(os.fsencode(existing))

        missing = os.path.join(tempfile.gettempdir(), 'l1_map_does_not_exist_12345.md')
        with self.assertRaises(ValueError):
            check_component_map(missing)
        with self.assertRaises(ValueError):
            check_skill_format_row(missing)

        directory = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, directory)
        with self.assertRaises(ValueError):
            check_component_map(directory)
        with self.assertRaises(ValueError):
            check_skill_format_row(directory)

    def test_invalid_utf8_refused(self):
        fixture = self._write_bytes(_fixture_text().encode('utf-8') + bytes([0xff]))
        with self.assertRaises(ValueError):
            check_component_map(fixture)
        with self.assertRaises(ValueError):
            check_skill_format_row(fixture)

    def test_empty_spec_refused(self):
        fixture = self._write_text('')
        with self.assertRaises(ValueError):
            check_component_map(fixture)
        with self.assertRaises(ValueError):
            check_skill_format_row(fixture)


if __name__ == '__main__':
    unittest.main()
