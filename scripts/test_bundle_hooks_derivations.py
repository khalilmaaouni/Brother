#!/usr/bin/env python3
"""bundle_runtime.cursor_hooks_doc and codex_hooks_table: what the harness (test_brother_hook.py
FourDerivedEqual) does not pin. Each case isolates one condition:

    python3 -B scripts/test_bundle_hooks_derivations.py
"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle_runtime as BR  # noqa: E402


class Derivations(unittest.TestCase):
    def test_a_wiring_that_names_a_gone_handler_raises(self):
        table = copy.deepcopy(BR.dispatch_table())
        table["Stop"] = [r for r in table["Stop"] if not r["tool"].endswith("sbe_session_reconcile.py")]
        with self.assertRaises(ValueError):
            BR.cursor_hooks_doc(BR.merged_hooks_doc(), table)

    def test_a_product_nobody_ships_wires_nothing(self):
        table = {"PreToolUse": [{"product": "other", "matcher": "Bash", "class": "deny",
                                 "tool": "other/tools/x.py", "args": []}]}
        self.assertEqual({"hooks": {}}, BR.cursor_hooks_doc({"hooks": {"PreToolUse": []}}, table))

    def test_the_committed_cursor_file_is_the_derivation(self):
        with open(os.path.join(os.path.dirname(HERE), "bundle", "cursor-hooks", "hooks.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), BR.cursor_hooks_doc(BR.merged_hooks_doc()))

    def test_codex_rows_carry_their_event_and_class(self):
        rows = BR.codex_hooks_table(BR.merged_hooks_doc())
        self.assertTrue(rows)
        self.assertTrue(all(r["event"] and r["class"] in ("deny", "advisory") for r in rows))


if __name__ == "__main__":
    unittest.main()
