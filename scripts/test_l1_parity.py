"""L1.4 parity inactivity verifier and activation block.

This module is both the implementation and its own stdlib unittest suite.
Done check: python3 -B -m unittest scripts.test_l1_parity
"""
import json
import os
import tempfile
import unittest
from pathlib import Path


SPEC_PATH = os.path.join('docs', 'architecture', 'ANTIGRAVITY-ADAPTER-SPEC.md')
ACTIVATION_BLOCK_PHRASE = 'Do not register before L1b.'
PARITY_CONSTANTS = ('ANTIGRAVITY_PAIRS', 'SURFACE_TWINS')
# The plugin/ twins are gone (OP1.c, docs/plan/specs/OP1.md 5.2): the bundle
# is the one directory holding installable manifests, so the spec names the
# bundle pair only.
BUNDLE_CODEX = 'bundle/.codex-plugin/plugin.json'
BUNDLE_ANTIGRAVITY = 'bundle/.antigravity-plugin/plugin.json'
MAX_SPEC_BYTES = 10 * 1024 * 1024


def _require_str(value, name):
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a str')
    return value


def _read_text(path_str):
    """Read a path as UTF-8 text. Missing or corrupt input returns None."""
    _require_str(path_str, 'path')
    path = Path(path_str)
    try:
        data = path.read_bytes()
    except (OSError, ValueError):
        return None
    if len(data) > MAX_SPEC_BYTES:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def check_parity_inactive(spec_path: str) -> bool:
    """Return True when the spec names the parity plan and keeps it inactive."""
    text = _read_text(spec_path)
    if text is None:
        return False
    if not any(constant in text for constant in PARITY_CONSTANTS):
        return False
    for twin in (BUNDLE_CODEX, BUNDLE_ANTIGRAVITY):
        if twin not in text:
            return False
    if ACTIVATION_BLOCK_PHRASE not in text:
        return False
    lowered = text.lower()
    if '"active": true' in lowered or '"active":true' in lowered:
        return False
    return True


def _manifest_path(plugin_root: Path) -> Path:
    if plugin_root.name == '.antigravity-plugin':
        return plugin_root / 'plugin.json'
    return plugin_root / '.antigravity-plugin' / 'plugin.json'


def block_active_registration(plugin_root: str) -> tuple[bool, str]:
    """Refuse any active Antigravity registration before L1b."""
    _require_str(plugin_root, 'plugin_root')
    root = Path(plugin_root)
    if not root.is_dir():
        return (False, 'BLOCK: plugin root missing')
    manifest = _manifest_path(root)
    if not manifest.is_file():
        return (False, 'BLOCK: plugin manifest missing')
    try:
        raw = manifest.read_bytes()
    except OSError:
        return (False, 'BLOCK: plugin manifest unreadable')
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (False, 'BLOCK: corrupt plugin manifest')
    if not isinstance(data, dict):
        return (False, 'BLOCK: corrupt plugin manifest')
    active = data.get('active')
    if active is True:
        data.pop('active', None)
        try:
            manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + chr(10), encoding='utf-8')
        except OSError:
            return (False, 'BLOCK: active registration refused before L1b')
        return (False, 'BLOCK: active registration refused before L1b')
    if active is False or 'active' not in data:
        return (True, 'inactive')
    return (False, 'BLOCK: corrupt active flag')


class L1ParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plugin_root = self.root / 'plugin'
        self.manifest_dir = self.plugin_root / '.antigravity-plugin'
        self.manifest_dir.mkdir(parents=True)
        self.manifest = self.manifest_dir / 'plugin.json'
        self.spec = self.root / 'spec.md'

    def test_parity_named_but_inactive(self):
        text = chr(10).join([
            'ANTIGRAVITY_PAIRS',
            BUNDLE_CODEX,
            BUNDLE_ANTIGRAVITY,
            ACTIVATION_BLOCK_PHRASE,
            '{"active": false}',
        ])
        self.spec.write_text(text, encoding='utf-8')
        self.assertTrue(check_parity_inactive(str(self.spec)))
        self.spec.write_text(text.replace('"active": false', '"active": true'), encoding='utf-8')
        self.assertFalse(check_parity_inactive(str(self.spec)))
        missing = text.replace('ANTIGRAVITY_PAIRS', 'OTHER').replace('SURFACE_TWINS', 'OTHER')
        self.spec.write_text(missing, encoding='utf-8')
        self.assertFalse(check_parity_inactive(str(self.spec)))
        no_phrase = text.replace(ACTIVATION_BLOCK_PHRASE, 'other')
        self.spec.write_text(no_phrase, encoding='utf-8')
        self.assertFalse(check_parity_inactive(str(self.spec)))
        if os.path.isfile(SPEC_PATH):
            self.assertTrue(check_parity_inactive(SPEC_PATH))

    def test_activation_block(self):
        self.manifest.write_text(json.dumps({'name': 'brother', 'active': True}), encoding='utf-8')
        allowed, reason = block_active_registration(str(self.plugin_root))
        self.assertFalse(allowed)
        self.assertEqual(reason, 'BLOCK: active registration refused before L1b')
        data = json.loads(self.manifest.read_text(encoding='utf-8'))
        self.assertNotIn('active', data)
        self.manifest.write_text(json.dumps({'name': 'brother', 'active': False}), encoding='utf-8')
        allowed, reason = block_active_registration(str(self.plugin_root))
        self.assertTrue(allowed)
        self.assertEqual(reason, 'inactive')

    @unittest.skipUnless(os.path.isfile(SPEC_PATH), 'live spec not present')
    def test_no_marketplace(self):
        text = Path(SPEC_PATH).read_text(encoding='utf-8')
        self.assertIn(ACTIVATION_BLOCK_PHRASE, text)

    def test_hostile_inputs(self):
        for bad in (None, 1, 1.0, b'x', True, [], {}):
            with self.assertRaises(ValueError):
                check_parity_inactive(bad)
            with self.assertRaises(ValueError):
                block_active_registration(bad)
        self.assertFalse(check_parity_inactive(str(self.root / 'missing.md')))
        self.assertFalse(check_parity_inactive(str(self.root)))
        bad_utf8 = self.root / 'bad.md'
        bad_utf8.write_bytes(bytes([255, 254, 0]))
        self.assertFalse(check_parity_inactive(str(bad_utf8)))
        allowed, reason = block_active_registration(str(self.root / 'nope'))
        self.assertFalse(allowed)
        self.assertIn('BLOCK', reason)
        self.manifest.write_text('{bad json', encoding='utf-8')
        allowed, reason = block_active_registration(str(self.plugin_root))
        self.assertFalse(allowed)
        self.assertIn('corrupt', reason.lower())
        self.manifest.write_text(json.dumps({'active': 'true'}), encoding='utf-8')
        allowed, reason = block_active_registration(str(self.plugin_root))
        self.assertFalse(allowed)
        self.assertIn('BLOCK', reason)


if __name__ == '__main__':
    unittest.main()
