#!/usr/bin/env python3
"""Tier tests for scripts/wiring_audit.py (unit WR1.a).

Every tool the audit labels gets one of three tiers a person can act on:
runtime (an import, a gate row, a hook, a manifest, the registry or the
bundle closure reaches it), docs (only a page a user reads names it), or
test (nothing a user or a gate enters reaches it). This suite drives the
tier functions directly, the new --tiers flag through main(), and the
refusal of any bucket nobody defined.

The real tree is never read. The fixture file list and the fixture files
are built in a temporary directory, and the audit's own tracked() reader
is replaced in process, so no git command runs and no path of this
repository is opened.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiring_audit  # noqa: E402


FIXTURE_FILES = (
    "scripts/check_all.sh",
    "scripts/live.py",
    "scripts/helper.py",
    "scripts/hooked.py",
    "products/brothermode/hooks/hooks.json",
    "docs/guide.md",
    "scripts/docs_tool.py",
    "scripts/lonely.py",
    "scripts/test_lonely.py",
    "scripts/dead.py",
    "scripts/orphan.py",
)

FIXTURE_BODIES = {
    "scripts/check_all.sh": 'run_check "live" python3 scripts/live.py\n',
    "scripts/live.py": "import helper\n",
    "scripts/helper.py": "x = 1\n",
    "scripts/hooked.py": "h = 1\n",
    "products/brothermode/hooks/hooks.json":
        '{"hooks": {"SessionStart": ["scripts/hooked.py refresh"]}}\n',
    "docs/guide.md": "See scripts/docs_tool.py for details.\n",
    "scripts/docs_tool.py": "d = 1\n",
    "scripts/lonely.py": "l = 1\n",
    "scripts/test_lonely.py": "import unittest\n",
    "scripts/dead.py": '"x" ; open("scripts/orphan.py")\n',
    "scripts/orphan.py": "o = 1\n",
}


def _build_fixture(root):
    for rel in FIXTURE_FILES:
        full = os.path.join(root, rel)
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(full, "w") as handle:
            handle.write(FIXTURE_BODIES[rel])


def _audit(root, files, *args):
    """Run wiring_audit.main() in process over a fixture file list."""
    saved_argv = sys.argv
    saved_tracked = wiring_audit.tracked
    wiring_audit.tracked = lambda _root: list(files)
    sys.argv = ["wiring_audit.py", "--root", root] + list(args)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = wiring_audit.main()
    finally:
        sys.argv = saved_argv
        wiring_audit.tracked = saved_tracked
    return code, buf.getvalue()


class TierFunctionTests(unittest.TestCase):
    def test_tier_of_maps_every_existing_bucket(self):
        self.assertEqual(wiring_audit.TIERS, ("runtime", "docs", "test"))
        for bucket, tier in wiring_audit.TIER_OF_BUCKET.items():
            self.assertIn(tier, wiring_audit.TIERS)
            self.assertEqual(wiring_audit.tier_of(bucket), tier)

    def test_gate_hook_and_code_buckets_are_runtime(self):
        for bucket in ("gate", "hooks", "bundle", "registry", "manifest",
                       "code-only"):
            self.assertEqual(wiring_audit.tier_of(bucket), "runtime")
        self.assertEqual(wiring_audit.tier_of("docs-only"), "docs")
        self.assertEqual(wiring_audit.tier_of("unwired"), "test")
        self.assertEqual(wiring_audit.tier_of("orphan-cluster"), "test")

    def test_tier_of_refuses_an_unknown_bucket(self):
        for bad in (None, "", "wired", "Docs-only", "code_only", "x",
                    b"x", 0, 1, -1, True, False, 1.5, float("nan"),
                    [], ["docs-only"], {}, {"a": 1}, (), ("docs-only",),
                    {1, 2}, object()):
            with self.assertRaises(ValueError):
                wiring_audit.tier_of(bad)

    def test_tier_map_puts_every_path_in_one_tier_once(self):
        got = wiring_audit.tier_map({
            "gate": ["b.py", "a.py", "a.py"],
            "docs-only": ["d.py"],
            "unwired": ["z.py"],
            "orphan-cluster": ["z.py"],
        })
        self.assertEqual(sorted(got), ["docs", "runtime", "test"])
        self.assertEqual(got["runtime"], ["a.py", "b.py"])
        self.assertEqual(got["docs"], ["d.py"])
        self.assertEqual(got["test"], ["z.py"])

    def test_tier_map_is_complete_and_empty_without_buckets(self):
        self.assertEqual(wiring_audit.tier_map({}),
                         {"runtime": [], "docs": [], "test": []})

    def test_the_summary_line_never_counts_docs_or_test_tools_as_wired(self):
        line = wiring_audit.tier_summary_line({
            "runtime": ["r1.py", "r2.py"],
            "docs": ["d1.py"],
            "test": ["t1.py", "t2.py", "t3.py"],
        })
        self.assertEqual(
            line,
            "WIRED (runtime tier): 2 of 6 tool(s); docs tier 1 and test "
            "tier 3 are not counted as wired")
        self.assertIn("2 of 6", line)
        self.assertNotIn("3 of 6", line)
        self.assertNotIn("6 of 6", line)


class AuditCliTierTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wiring-tiers-")
        _build_fixture(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _json(self, *args):
        code, out = _audit(self.root, FIXTURE_FILES, *args)
        return code, json.loads(out)

    def test_tiers_json_adds_a_tier_for_every_tool_exactly_once(self):
        code, got = self._json("--json", "--tiers")
        self.assertEqual(code, 0)
        self.assertIn("tiers", got)
        tiers = got["tiers"]
        self.assertEqual(sorted(tiers), ["docs", "runtime", "test"])
        seen = []
        for name in ("runtime", "docs", "test"):
            seen.extend(tiers[name])
        self.assertEqual(len(seen), len(set(seen)))
        old = []
        for key, value in got.items():
            if key != "tiers":
                old.extend(value)
        self.assertGreater(len(seen), 0)
        self.assertEqual(sorted(seen), sorted(old))

    def test_tiers_follow_the_bucket_of_each_fixture_tool(self):
        _code, got = self._json("--json", "--tiers")
        tiers = got["tiers"]
        self.assertIn("scripts/live.py", tiers["runtime"])
        self.assertIn("scripts/helper.py", tiers["runtime"])
        self.assertIn("scripts/hooked.py", tiers["runtime"])
        self.assertIn("scripts/docs_tool.py", tiers["docs"])
        self.assertIn("scripts/lonely.py", tiers["test"])
        self.assertIn("scripts/dead.py", tiers["test"])
        self.assertIn("scripts/orphan.py", tiers["test"])
        self.assertNotIn("scripts/docs_tool.py", tiers["runtime"])
        self.assertNotIn("scripts/lonely.py", tiers["runtime"])
        self.assertNotIn("scripts/lonely.py", tiers["docs"])

    def test_plain_json_is_byte_identical_to_the_old_output(self):
        code, out = _audit(self.root, FIXTURE_FILES, "--json")
        self.assertEqual(code, 0)
        self.assertNotIn('"tiers"', out)
        got = json.loads(out)
        self.assertEqual(
            out, json.dumps({k: sorted(v) for k, v in got.items()},
                            indent=2) + "\n")

    def test_tiers_json_keeps_every_old_key_unchanged(self):
        _code, plain = self._json("--json")
        _code, tiered = self._json("--json", "--tiers")
        self.assertIn("tiers", tiered)
        for key, value in plain.items():
            self.assertIn(key, tiered)
            self.assertEqual(tiered[key], value)
        self.assertEqual(sorted(tiered), sorted(list(plain) + ["tiers"]))

    def test_the_exit_code_is_unchanged_by_tiers(self):
        plain_code, plain_out = _audit(self.root, FIXTURE_FILES)
        tier_code, tier_out = _audit(self.root, FIXTURE_FILES, "--tiers")
        self.assertEqual(plain_code, tier_code)
        self.assertEqual(plain_code, 1)
        self.assertIn("DEAD SURFACE:", plain_out)
        self.assertNotIn("WIRED (runtime tier):", plain_out)
        self.assertIn("DEAD SURFACE:", tier_out)
        self.assertIn("WIRED (runtime tier):", tier_out)
        self.assertLess(tier_out.index("DEAD SURFACE:"),
                        tier_out.index("WIRED (runtime tier):"))

    def test_a_tree_with_no_tools_is_still_no_data_exit_2(self):
        for args in ((), ("--tiers",), ("--json",), ("--json", "--tiers")):
            code, out = _audit(self.root, [], *args)
            self.assertEqual(code, 2, out)
            self.assertIn("NO-DATA", out)


class HostileInputTests(unittest.TestCase):
    VALUES = (None, 0, 1, -1, True, False, 1.5, float("nan"), "", "x", b"x",
              [], ["x"], {}, {"a": 1}, (), {1, 2}, object())

    def test_every_public_tier_function_returns_or_refuses(self):
        funcs = (wiring_audit.tier_of, wiring_audit.tier_map,
                 wiring_audit.tier_summary_line)
        for func in funcs:
            for value in self.VALUES:
                try:
                    func(value)
                except ValueError:
                    continue
                except Exception as exc:
                    self.fail("%s(%r) raised %s: %s"
                              % (func.__name__, value,
                                 type(exc).__name__, exc))


if __name__ == "__main__":
    unittest.main()
