"""What scripts/jev_g1_seam_cache.py must keep true: a cache hit for an
off entry never imports or calls jev_seam again within the freshness
window, and reset()/a live config flip are both picked up correctly.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

# Redirect jev_seam's own machine-level state root to a throwaway temp
# dir before it is ever imported in this process (see the sibling wave-1
# test files' identical block for why).
os.environ.setdefault("BROTHER_JEV_STATE_DIR",
                       tempfile.mkdtemp(prefix="brother-jev-state-test-"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_g1_seam_cache as cache  # noqa: E402
import jev_seam  # noqa: E402


class ACacheHitNeverTouchesJevSeam(unittest.TestCase):
    def setUp(self):
        cache.reset()

    def test_first_call_is_a_miss_second_call_is_a_hit(self):
        real_cfg = {"modes": {"J025": "off"}}
        with mock.patch.object(jev_seam, "load_seams_config", return_value=real_cfg) as loader:
            first = cache.is_off("J025")
            second = cache.is_off("J025")
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(loader.call_count, 1, "a cache hit must never re-call load_seams_config")

    def test_a_shadow_entry_is_not_off(self):
        cfg = {"modes": {"J025": "shadow"}}
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg):
            self.assertFalse(cache.is_off("J025"))

    def test_reset_forces_a_fresh_read(self):
        with mock.patch.object(jev_seam, "load_seams_config",
                                return_value={"modes": {"J025": "off"}}):
            self.assertTrue(cache.is_off("J025"))
        cache.reset()
        with mock.patch.object(jev_seam, "load_seams_config",
                                return_value={"modes": {"J025": "shadow"}}) as loader:
            self.assertFalse(cache.is_off("J025"))
            self.assertEqual(loader.call_count, 1)

    def test_a_resolution_failure_caches_as_off_the_safe_default(self):
        with mock.patch.object(jev_seam, "load_seams_config",
                                side_effect=RuntimeError("boom")):
            self.assertTrue(cache.is_off("J025"))

    def test_distinct_entry_ids_are_independent_keys(self):
        with mock.patch.object(jev_seam, "load_seams_config",
                                return_value={"modes": {"J025": "off", "J094": "shadow"}}):
            self.assertTrue(cache.is_off("J025"))
            self.assertFalse(cache.is_off("J094"))


if __name__ == "__main__":
    unittest.main()
