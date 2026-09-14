#!/usr/bin/env python3
"""Calibration for tools/bm_vault_plugins.py.

The property under test is not that the tool can print a plugin count. It is
that it refuses (exit 2) whenever an ENABLED plugin is either not covered by
the policy file at all, or is covered and marked refused -- the two ways a
"looks fine" vault can actually be running something nobody signed off on.
Every fixture here is a throwaway temp tree; none of this touches the real
vault or the real policy file.

No em or en dashes anywhere in this file.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_plugins as plugins  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def make_vault(enabled, policy_plugins):
    """A throwaway vault dir with the two files bm_vault_plugins.py reads."""
    vault = tempfile.mkdtemp(prefix="bm_vault_plugins_test_")
    os.makedirs(os.path.join(vault, ".obsidian"))
    os.makedirs(os.path.join(vault, "99-System"))
    with open(os.path.join(vault, ".obsidian", "community-plugins.json"), "w") as fh:
        json.dump(enabled, fh)
    with open(os.path.join(vault, "99-System", "obsidian-plugin-policy.json"), "w") as fh:
        json.dump({"schema": "brother-obsidian-plugin-policy-v1", "plugins": policy_plugins}, fh)
    return vault


def entry(pid, status):
    return {"id": pid, "version": "1.0.0", "sha256": "x" * 64, "status": status}


class PolicyCoveredIsClean(unittest.TestCase):
    def test_all_enabled_covered_and_none_refused_exits_zero(self):
        vault = make_vault(
            ["homepage", "obsidian-charts"],
            [entry("homepage", "approved"), entry("obsidian-charts", "approved")],
        )
        self.assertEqual(plugins.cmd_check(vault), 0)


class UncoveredPluginRefuses(unittest.TestCase):
    def test_enabled_plugin_missing_from_policy_exits_two_and_names_it(self):
        vault = make_vault(
            ["homepage", "mystery-plugin"],
            [entry("homepage", "approved")],
        )
        self.assertEqual(plugins.cmd_check(vault), 2)


class RefusedPluginRefuses(unittest.TestCase):
    def test_enabled_plugin_marked_refused_exits_two_and_names_it(self):
        vault = make_vault(
            ["homepage", "realclaudian"],
            [entry("homepage", "approved"), entry("realclaudian", "refused")],
        )
        self.assertEqual(plugins.cmd_check(vault), 2)


if __name__ == "__main__":
    unittest.main()
