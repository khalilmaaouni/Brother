#!/usr/bin/env python3
"""L5b.3 test_flag_rules: rules fire on named patterns; hostile input refused."""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.l5b_audit import flag_rules as fr


def _call(kind="network", file="a.py", line=5, column=1, symbol="f"):
    return fr.BoundaryCall(file=file, line=line, column=column,
                           symbol=symbol, kind=kind, snippet="")


class TestHostileInputs(unittest.TestCase):
    def test_rules_refuse_none_and_wrong_types(self):
        bad = [None, 0, 1, 1.5, True, -1, float("nan"), "", b"x", [], {}]
        for fn_val in bad:
            with self.assertRaises(ValueError):
                fr.rule_grant_forgery(fn_val, _call())
        for call_val in bad:
            with self.assertRaises(ValueError):
                fr.rule_grant_forgery(ast.parse("pass"), call_val)

    def test_ceiling_global_refuses_hostile_inputs(self):
        bad = [None, 0, 1, 1.5, True, -1, float("nan"), "", b"x", [], {}]
        for value in bad:
            with self.assertRaises(ValueError):
                fr.rule_ceiling_global(value, _call())
            with self.assertRaises(ValueError):
                fr.rule_ceiling_global(ast.parse("pass"), value)

    def test_every_rule_refuses_hostile_inputs(self):
        bad = [None, 0, True, float("nan"), "", b"x", [], {}]
        rules = (fr.rule_grant_forgery, fr.rule_path_unconfined,
                 fr.rule_cost_fallback_stated, fr.rule_cleanup_nonmask,
                 fr.rule_cli_bounds, fr.rule_lease_root,
                 fr.rule_decode_narrow, fr.rule_ceiling_global)
        for rule in rules:
            for value in bad:
                with self.assertRaises(ValueError):
                    rule(value, _call())
                with self.assertRaises(ValueError):
                    rule(ast.parse("pass"), value)

    def test_flag_call_refuses_hostile_source(self):
        for bad in [None, 0, True, float("nan"), b"x", [], {}]:
            with self.assertRaises(ValueError):
                fr.flag_call(bad, _call())

    def test_flag_call_refuses_unparseable_source(self):
        with self.assertRaises(ValueError):
            fr.flag_call("def (:", _call())

    def test_flag_call_refuses_hostile_call(self):
        for bad in [None, 0, True, float("nan"), [], {}]:
            with self.assertRaises(ValueError):
                fr.flag_call("x = 1\n", bad)

    def test_well_formed_call_rejects_bad_fields(self):
        with self.assertRaises(ValueError):
            fr.rule_grant_forgery(
                ast.parse("pass"),
                fr.BoundaryCall(file="", line=1, column=1, symbol="f",
                                kind="network", snippet=""))
        with self.assertRaises(ValueError):
            fr.rule_grant_forgery(
                ast.parse("pass"),
                fr.BoundaryCall(file="a.py", line=0, column=1, symbol="f",
                                kind="network", snippet=""))
        with self.assertRaises(ValueError):
            fr.rule_grant_forgery(
                ast.parse("pass"),
                fr.BoundaryCall(file="a.py", line=1, column=1, symbol="f",
                                kind=1, snippet=""))


class TestGrantForgery(unittest.TestCase):
    def test_grant_forgery_fires_without_authority(self):
        tree = ast.parse('x = "cap-grant-id"\n')
        self.assertTrue(fr.rule_grant_forgery(tree, _call()))

    def test_grant_forgery_quiet_with_authority(self):
        tree = ast.parse('x = "cap-grant-id"\ncheck_authority(x)\n')
        self.assertFalse(fr.rule_grant_forgery(tree, _call()))

    def test_grant_forgery_quiet_when_no_grant(self):
        self.assertFalse(fr.rule_grant_forgery(ast.parse("x = 1\n"), _call()))


class TestPathUnconfined(unittest.TestCase):
    def test_path_unconfined_fires_for_unconfined_file_io(self):
        self.assertTrue(fr.rule_path_unconfined(
            ast.parse("open(p)\n"), _call(kind="file_io")))

    def test_path_unconfined_quiet_when_confined(self):
        self.assertFalse(fr.rule_path_unconfined(
            ast.parse("safepath(p)\nopen(p)\n"), _call(kind="file_io")))

    def test_path_unconfined_quiet_for_network(self):
        self.assertFalse(fr.rule_path_unconfined(
            ast.parse("open(p)\n"), _call(kind="network")))


class TestCostFallback(unittest.TestCase):
    def test_cost_fallback_fires_without_logger(self):
        self.assertTrue(fr.rule_cost_fallback_stated(
            ast.parse('x = "cost"\n'), _call()))

    def test_cost_fallback_quiet_with_logger(self):
        self.assertFalse(fr.rule_cost_fallback_stated(
            ast.parse('x = "cost"\nlog_cost_fallback(x)\n'), _call()))


class TestCleanupNonmask(unittest.TestCase):
    def test_cleanup_nonmask_fires_on_bare_except_pass(self):
        src = "try:\n    x = 1\nexcept Exception:\n    pass\n"
        self.assertTrue(fr.rule_cleanup_nonmask(ast.parse(src), _call()))

    def test_cleanup_nonmask_quiet_when_re_raised(self):
        src = "try:\n    x = 1\nexcept ValueError:\n    raise\n"
        self.assertFalse(fr.rule_cleanup_nonmask(ast.parse(src), _call()))


class TestCliBounds(unittest.TestCase):
    def test_cli_bounds_fires_without_bounded_int(self):
        src = "import argparse\np = argparse.ArgumentParser()\n"
        self.assertTrue(fr.rule_cli_bounds(ast.parse(src), _call()))

    def test_cli_bounds_quiet_with_bounded_int(self):
        src = "import argparse\np = argparse.ArgumentParser()\nbounded_int(1)\n"
        self.assertFalse(fr.rule_cli_bounds(ast.parse(src), _call()))

    def test_cli_bounds_quiet_without_argparse(self):
        self.assertFalse(fr.rule_cli_bounds(ast.parse("x = 1\n"), _call()))


class TestLeaseRoot(unittest.TestCase):
    def test_lease_root_fires_without_pin(self):
        self.assertTrue(fr.rule_lease_root(ast.parse('x = "lease"\n'), _call()))

    def test_lease_root_quiet_with_pin(self):
        self.assertFalse(fr.rule_lease_root(
            ast.parse('x = "lease"\nassert_root(x)\n'), _call()))


class TestDecodeNarrow(unittest.TestCase):
    def test_decode_narrow_fires_on_broad_except(self):
        src = "try:\n    json.loads(s)\nexcept Exception:\n    raise\n"
        self.assertTrue(fr.rule_decode_narrow(
            ast.parse(src), _call(kind="json_decode")))

    def test_decode_narrow_quiet_on_specific_except(self):
        src = "try:\n    json.loads(s)\nexcept ValueError:\n    raise\n"
        self.assertFalse(fr.rule_decode_narrow(
            ast.parse(src), _call(kind="json_decode")))


class TestCeilingGlobal(unittest.TestCase):
    def test_ceiling_global_fires_on_module_assignment(self):
        self.assertTrue(fr.rule_ceiling_global(
            ast.parse("ceiling_global = 10\n"), _call()))

    def test_ceiling_global_quiet_without_assignment(self):
        self.assertFalse(fr.rule_ceiling_global(ast.parse("x = 1\n"), _call()))


class TestPropagateSafe(unittest.TestCase):
    def test_propagate_safe_rule_fires_on_missing_marker(self):
        hits = fr.flag_call("x = 1\n", _call(line=1))
        ids = [h.rule_id for h in hits]
        self.assertIn("L5B-PROPAGATE-SAFE", ids)

    def test_propagate_safe_rule_fires_on_short_reason(self):
        hits = fr.flag_call("# l5b: PROPAGATE_SAFE: hi\nx = 1\n", _call(line=2))
        ids = [h.rule_id for h in hits]
        self.assertIn("L5B-PROPAGATE-SAFE", ids)

    def test_propagate_safe_rule_quiet_on_good_reason(self):
        hits = fr.flag_call(
            "# l5b: PROPAGATE_SAFE: this reason is long enough\nx = 1\n",
            _call(line=2))
        ids = [h.rule_id for h in hits]
        self.assertNotIn("L5B-PROPAGATE-SAFE", ids)

    def test_flag_call_emits_named_rule_ids(self):
        src = 'x = "cap-grant-id"\n'
        hits = fr.flag_call(src, _call(line=1))
        ids = [h.rule_id for h in hits]
        self.assertIn("L5B-GRANT-FORGERY", ids)
        for hit in hits:
            self.assertEqual(hit.file, "a.py")
            self.assertEqual(hit.symbol, "f")


if __name__ == "__main__":
    unittest.main()
