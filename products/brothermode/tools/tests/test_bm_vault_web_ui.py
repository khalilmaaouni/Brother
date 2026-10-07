import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import bm_vault_web_ui as ui


class TestL3b01CliGuards(unittest.TestCase):
    def _main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ui.main(argv)
        return rc, err.getvalue()

    def test_default_bind_is_loopback(self):
        args = ui._parse_args([])
        self.assertEqual(args.bind, '127.0.0.1')
        self.assertIn(args.bind, ('127.0.0.1', 'localhost', '::1'))
        self.assertEqual(args.port, ui.DEFAULT_PORT)

    def test_non_loopback_without_tls_exits_2(self):
        rc, err = self._main(['--bind', '0.0.0.0'])
        self.assertEqual(rc, 2)
        with tempfile.TemporaryDirectory() as d:
            cert = os.path.join(d, 'cert.pem')
            key = os.path.join(d, 'key.pem')
            with open(cert, 'wb') as f:
                f.write(b'not a certificate')
            with open(key, 'wb') as f:
                f.write(b'not a key')
            rc, err = self._main(['--bind', '0.0.0.0',
                                  '--tls-cert', cert,
                                  '--tls-key', key])
            self.assertEqual(rc, 2)
            self.assertIn('TLS cert/key failed to load', err)
            self.assertNotIn('not a certificate', err)
            self.assertNotIn('not a key', err)

    def test_empty_token_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, 'empty.tok')
            with open(empty, 'wb') as f:
                f.write(b'   ')
            rc, err = self._main(['--token-file', empty])
            self.assertEqual(rc, 2)
            missing = os.path.join(d, 'missing.tok')
            rc, err = self._main(['--token-file', missing])
            self.assertEqual(rc, 2)
            rc, err = self._main(['--token-file', d])
            self.assertEqual(rc, 2)

    def test_pane_off_loopback_without_token_exits_2(self):
        rc, err = self._main(['--pane-upstream', 'http://10.0.0.1:8379'])
        self.assertEqual(rc, 2)
        with tempfile.TemporaryDirectory() as d:
            tok = os.path.join(d, 'pane.tok')
            with open(tok, 'wb') as f:
                f.write(b'secret')
            rc, err = self._main(['--pane-upstream', 'http://10.0.0.1:8379',
                                  '--pane-token-file', tok])
            self.assertEqual(rc, 0)

    def test_valid_loopback_passes(self):
        rc, err = self._main([])
        self.assertEqual(rc, 0)

    def test_parse_args_refuses_hostile_input(self):
        for bad in (None, 'not-a-list', 123, 1.5, True, [None], [123], [b'x']):
            with self.assertRaises(ValueError):
                ui._parse_args(bad)

    def test_load_token_refuses_hostile_input(self):
        for bad in (None, 123, 1.5, True, b'path'):
            with self.assertRaises(ValueError):
                ui._load_token(bad)
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, 'empty.tok')
            with open(empty, 'wb') as f:
                f.write(b'   ')
            with self.assertRaises(ValueError):
                ui._load_token(empty)
            notutf = os.path.join(d, 'notutf.tok')
            with open(notutf, 'wb') as f:
                f.write(bytes([255, 254]))
            with self.assertRaises(ValueError):
                ui._load_token(notutf)

    def test_startup_guards_refuses_hostile_args(self):
        for bad in (None, 'x', 123, True):
            with self.assertRaises(ValueError):
                ui._startup_guards(bad)

    def test_main_refuses_hostile_argv(self):
        for bad in ('x', 123, True):
            with self.assertRaises(ValueError):
                ui.main(bad)


if __name__ == '__main__':
    unittest.main()
