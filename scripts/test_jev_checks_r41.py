"""R4.1 preamble self isolation proofs for scripts/test_jev_checks.py and
scripts/test_jev_seam.py. Tests run in an export copy with empty HOME.
"""
import importlib.util
import os
import tempfile
import unittest

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checks_mod = _load_module("test_jev_checks", os.path.join(_SCRIPTS_DIR, "test_jev_checks.py"))
seam_mod = _load_module("test_jev_seam", os.path.join(_SCRIPTS_DIR, "test_jev_seam.py"))


class R4PreambleIsolationProof(unittest.TestCase):
    def test_live_state_dir_is_replaced(self):
        for mod in (checks_mod, seam_mod):
            with tempfile.TemporaryDirectory() as live_home:
                old_snapshot = mod._LIVE_HOME_SNAPSHOT
                mod._LIVE_HOME_SNAPSHOT = live_home
                live_path = os.path.join(live_home, "state")
                old_env = os.environ.get("BROTHER_JEV_STATE_DIR")
                try:
                    result = mod._ensure_isolated_state_dir(live_path)
                    self.assertNotEqual(result, live_path)
                    self.assertFalse(result.startswith(live_home))
                    self.assertTrue(os.path.isabs(result))
                    self.assertTrue(result.startswith(tempfile.gettempdir()))
                    self.assertEqual(os.environ["BROTHER_JEV_STATE_DIR"], result)
                finally:
                    mod._LIVE_HOME_SNAPSHOT = old_snapshot
                    if old_env is None:
                        os.environ.pop("BROTHER_JEV_STATE_DIR", None)
                    else:
                        os.environ["BROTHER_JEV_STATE_DIR"] = old_env

    def test_empty_state_dir_never_uses_live_home(self):
        for mod in (checks_mod, seam_mod):
            with tempfile.TemporaryDirectory() as live_home:
                old_snapshot = mod._LIVE_HOME_SNAPSHOT
                mod._LIVE_HOME_SNAPSHOT = live_home
                old_env = os.environ.get("BROTHER_JEV_STATE_DIR")
                try:
                    for candidate in (None, "", "relative/path", 123, b"bytes"):
                        result = mod._ensure_isolated_state_dir(candidate)
                        self.assertTrue(os.path.isabs(result))
                        self.assertTrue(result.startswith(tempfile.gettempdir()))
                        self.assertFalse(result.startswith(live_home))
                        self.assertEqual(os.environ["BROTHER_JEV_STATE_DIR"], result)
                finally:
                    mod._LIVE_HOME_SNAPSHOT = old_snapshot
                    if old_env is None:
                        os.environ.pop("BROTHER_JEV_STATE_DIR", None)
                    else:
                        os.environ["BROTHER_JEV_STATE_DIR"] = old_env

    def test_home_is_isolated(self):
        for mod in (checks_mod, seam_mod):
            old_home = os.environ.get("HOME")
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            old_snapshot = mod._LIVE_HOME_SNAPSHOT
            try:
                home = mod._ensure_isolated_home()
                self.assertTrue(os.path.isabs(home))
                self.assertTrue(home.startswith(tempfile.gettempdir()))
                self.assertEqual(os.environ["HOME"], home)
                self.assertEqual(os.environ["XDG_CONFIG_HOME"], home)
                self.assertNotEqual(mod._LIVE_HOME_SNAPSHOT, home)
            finally:
                mod._LIVE_HOME_SNAPSHOT = old_snapshot
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
                if old_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

    def test_import_time_state_dir_is_temp(self):
        for mod in (checks_mod, seam_mod):
            self.assertTrue(os.path.isabs(mod._TEMP_STATE_DIR))
            self.assertTrue(mod._TEMP_STATE_DIR.startswith(tempfile.gettempdir()))
            live = mod._LIVE_HOME_SNAPSHOT
            if live:
                self.assertFalse(mod._TEMP_STATE_DIR.startswith(live))

    def test_fail_closed_raises_skip(self):
        for mod in (checks_mod, seam_mod):
            with self.assertRaises(unittest.SkipTest):
                mod._fail_closed("hostile test")
