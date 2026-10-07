#!/usr/bin/env python3
"""Regression tests for L5c.1: hardening of scripts/mutation_probe.py."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest


def _remove_tree(path):
    """Remove a scratch folder this suite itself created.

    Written with os.walk so this file imports no tree-removal helper and no
    child-process module at all. Only ever handed a folder the suite itself
    made; a temp folder that will not come away is not data.
    """
    if not isinstance(path, str) or not path:
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        for name in filenames:
            try:
                os.unlink(os.path.join(dirpath, name))
            except OSError:
                pass
        for name in dirnames:
            try:
                os.rmdir(os.path.join(dirpath, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mutation_probe  # noqa: E402

PROBE = os.path.join(HERE, "mutation_probe.py")

EXPECTED_DEFAULT_COPY = (
    "plugin",
    "scripts/brother_antigravity_hook.py",
    "scripts/test_brother_antigravity_hook.py",
)

TARGET_OLD = 'SLEEP = "0"'


# L5c.2: inventory, deterministic sample and hash lock. The module lives
# beside this test file in scripts/, so the sys.path insert above already
# resolves it.
import l5c_audit  # noqa: E402


class InventoryTests(unittest.TestCase):
    """L5c.2: the audit's inventory, deterministic sample and hash lock.

    Every test builds its fixture under a fresh temp folder and reads
    nothing from the live repository, so the suite runs identically in the
    export copy (empty HOME, no docs/plan) and on the real tree. Each
    assertion states what the specification requires, never what the
    implementation happens to return.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="l5c2-")
        self.tmp = self._tmp.name
        self.repo = os.path.join(self.tmp, "repo")
        self.vault = os.path.join(self.tmp, "vault")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path, text):
        full = path if os.path.isabs(path) else os.path.join(self.tmp, path)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
        return full

    def _make_fixed_suites(self):
        self._write(os.path.join(self.repo, l5c_audit.DISPATCH_TEST),
                    "class DispatchSuite:\n"
                    "    def test_a(self):\n        pass\n"
                    "    def test_b(self):\n        pass\n"
                    "    def test_c(self):\n        pass\n"
                    "    def test_d(self):\n        pass\n")
        self._write(os.path.join(self.repo, l5c_audit.HOOK_TEST),
                    "class HookSuite:\n"
                    "    def test_a(self):\n        pass\n"
                    "    def test_b(self):\n        pass\n"
                    "    def test_c(self):\n        pass\n")

    def _make_two_vault_files(self):
        for name in ("one", "two"):
            self._write(os.path.join(self.vault, "tokenA", "test_%s.py" % name),
                        "class VaultSuite:\n"
                        "    def test_zeta(self):\n        pass\n"
                        "    def test_alpha(self):\n        pass\n")

    def test_collect_sorted_ast_only(self):
        # The methods on disk are zeta before alpha: the answer must be
        # sorted, never the file's own order.
        path = self._write("order/suite_order.py",
                           "class T:\n"
                           "    def test_zeta(self):\n        pass\n"
                           "    def test_alpha(self):\n        pass\n")
        self.assertEqual(l5c_audit.collect_test_methods(path),
                         ["test_alpha", "test_zeta"])

    def test_collect_reads_ast_and_never_imports(self):
        # A module level raise proves the file was parsed, not imported.
        path = self._write("order/suite_boom.py",
                           "raise RuntimeError('this file must never be imported')\n"
                           "def test_module_level():\n    pass\n"
                           "class K:\n"
                           "    def test_in_class(self):\n        pass\n"
                           "    def not_a_test(self):\n        pass\n")
        self.assertEqual(l5c_audit.collect_test_methods(path),
                         ["test_in_class", "test_module_level"])

    def test_unparseable_file_is_refused(self):
        path = self._write("order/suite_bad.py", "def test_x(:\n")
        with self.assertRaises(ValueError):
            l5c_audit.collect_test_methods(path)

    def test_hostile_input_is_refused(self):
        for bad in (None, 123, 1.5, True, "", b"bytes", ["a"], float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.collect_test_methods(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.sha256_file(bad)
        with self.assertRaises(ValueError):
            l5c_audit.sha256_file(os.path.join(self.tmp, "no-such-file"))
        with self.assertRaises(ValueError):
            l5c_audit.sha256_file(self.tmp)  # a directory, not a file
        with self.assertRaises(ValueError):
            l5c_audit.discover_test_files(None, self.vault, "tokenA")
        with self.assertRaises(ValueError):
            l5c_audit.discover_test_files(float("nan"), self.vault, "tokenA")
        with self.assertRaises(ValueError):
            l5c_audit.select_samples("", self.vault, "tokenA")
        with self.assertRaises(ValueError):
            l5c_audit.select_samples(None, self.vault, "tokenA")

    def test_sha256_reads_bytes_and_differs_by_content(self):
        import hashlib
        first = self._write("blobs/a.bin", "alpha")
        second = self._write("blobs/b.bin", "beta")
        self.assertEqual(l5c_audit.sha256_file(first),
                         hashlib.sha256(b"alpha").hexdigest())
        self.assertNotEqual(l5c_audit.sha256_file(first),
                            l5c_audit.sha256_file(second))

    def test_token_filters_the_vault_walk(self):
        self._make_fixed_suites()
        inside = self._write(os.path.join(self.vault, "tokenA", "test_one.py"),
                             "def test_only():\n    pass\n")
        outside = self._write(os.path.join(self.vault, "tokenB", "test_two.py"),
                              "def test_only():\n    pass\n")
        found = l5c_audit.discover_test_files(self.repo, self.vault, "tokenA")
        self.assertIn(inside, found)
        self.assertNotIn(outside, found)
        self.assertEqual(found, sorted(found))
        self.assertEqual(len(found), len(set(found)))

    def test_missing_vault_inputs_are_nodata(self):
        self._make_fixed_suites()
        os.makedirs(self.vault, exist_ok=True)
        for vault_root, vault_token in (
            (None, "tokenA"), ("", "tokenA"),
            (self.vault, None), (self.vault, ""),
        ):
            with self.assertRaises(l5c_audit.NoData,
                                   msg=repr((vault_root, vault_token))):
                l5c_audit.discover_test_files(self.repo, vault_root, vault_token)

    def test_empty_vault_is_nodata(self):
        self._make_fixed_suites()
        os.makedirs(self.vault, exist_ok=True)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.discover_test_files(self.repo, self.vault, "no-such-token")

    def test_missing_fixed_suite_is_nodata(self):
        self._make_fixed_suites()
        self._write(os.path.join(self.vault, "tokenA", "test_one.py"),
                    "def test_only():\n    pass\n")
        os.remove(os.path.join(self.repo, l5c_audit.DISPATCH_TEST))
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.discover_test_files(self.repo, self.vault, "tokenA")

    def test_selection_is_deterministic_and_sorted(self):
        self._make_fixed_suites()
        for name in ("zulu", "alfa"):
            self._write(os.path.join(self.vault, "tokenA", "test_%s.py" % name),
                        "class VaultSuite:\n"
                        "    def test_zeta(self):\n        pass\n"
                        "    def test_alpha(self):\n        pass\n"
                        "    def test_beta(self):\n        pass\n")
        rows = l5c_audit.select_samples(self.repo, self.vault, "tokenA")
        self.assertEqual(rows, l5c_audit.select_samples(self.repo, self.vault, "tokenA"))
        counts = {}
        for row in rows:
            counts[row["category"]] = counts.get(row["category"], 0) + 1
        self.assertEqual(counts, {"vault": 4, "dispatch": 3, "hook": 3})
        vault_tests = [row["test"] for row in rows if row["category"] == "vault"]
        self.assertEqual(vault_tests, sorted(vault_tests))
        seen = {}
        for row in rows:
            if row["category"] == "vault":
                seen.setdefault(row["test"], []).append(row["method"])
        self.assertEqual(sorted(seen.keys()),
                         [os.path.join(self.vault, "tokenA", "test_alfa.py"),
                          os.path.join(self.vault, "tokenA", "test_zulu.py")])
        for methods in seen.values():
            self.assertEqual(methods, ["test_alpha", "test_beta"])

    def test_a_one_method_vault_file_hands_its_share_to_the_next_file(self):
        # The real vault facade suites hold ONE test each: the quota of four
        # vault rows is filled from the next sorted files, never left at two.
        self._make_fixed_suites()
        for name in ("delta", "alfa", "charlie", "bravo", "echo"):
            self._write(os.path.join(self.vault, "tokenA", "test_%s.py" % name),
                        "def test_only():\n    pass\n")
        rows = l5c_audit.select_samples(self.repo, self.vault, "tokenA")
        vault = [os.path.basename(row["test"]) for row in rows
                 if row["category"] == "vault"]
        self.assertEqual(vault, ["test_alfa.py", "test_bravo.py",
                                 "test_charlie.py", "test_delta.py"])
        self.assertEqual(len(rows), 10)

    def test_every_row_carries_its_hash_lock(self):
        self._make_fixed_suites()
        self._make_two_vault_files()
        rows = l5c_audit.select_samples(self.repo, self.vault, "tokenA")
        self.assertEqual(len(rows), 10)
        for row in rows:
            self.assertEqual(row["test_sha256"], l5c_audit.sha256_file(row["test"]))
            self.assertEqual(row["sampled_of"],
                             len(l5c_audit.collect_test_methods(row["test"])))

    def test_row_locks_the_source_module_hash(self):
        self._make_fixed_suites()
        source = self._write(os.path.join(self.repo, "plugin", "runtime",
                                          "brother", "core",
                                          "openrouter_dispatch.py"),
                             "# the module the dispatch suite is a test of\n")
        self._make_two_vault_files()
        rows = l5c_audit.select_samples(self.repo, self.vault, "tokenA")
        dispatch_sources = set(row["src_sha256"] for row in rows
                               if row["category"] == "dispatch")
        self.assertEqual(dispatch_sources, {l5c_audit.sha256_file(source)})


HANG_TEST = '''"""Fixture suite that sleeps for target.SLEEP seconds."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import target

class ProbeHang(unittest.TestCase):
    def test_sleeps(self):
        time.sleep(float(target.SLEEP))
        self.assertTrue(True)

    def test_second(self):
        self.assertTrue(True)

if __name__ == "__main__":
    unittest.main()
'''

class ProbeFixture(unittest.TestCase):
    def make_root(self, sleep_value="0"):
        root = tempfile.mkdtemp(prefix="l5c1-probe-")
        scripts = os.path.join(root, "scripts")
        os.makedirs(scripts, exist_ok=True)
        with open(os.path.join(scripts, "target.py"), "w", encoding="utf-8") as fh:
            fh.write('SLEEP = "%s"\n' % sleep_value)
        with open(os.path.join(scripts, "target_test.py"), "w", encoding="utf-8") as fh:
            fh.write(HANG_TEST)
        return root

class EvidenceTests(unittest.TestCase):
    """L5c.5: render_region, render_doc and verify_evidence.

    Every fixture is built under a fresh temp directory; nothing here reads
    the live tree or docs/plan, so the suite runs identically in the export
    copy. Each test asserts what the L5c.5 requirements say, never what the
    implementation happens to return.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="l5c5-")
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root, exist_ok=True)
        self.doc_path = os.path.join(self.tmp, "doc.md")
        self.ledger_path = os.path.join(self.tmp, "ledger.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path, text):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))
        return path

    def _good_ledger(self):
        src_rel = "module.py"
        src_full = os.path.join(self.root, src_rel)
        self._write(src_full, "def f():\n    return 1\n")
        lock = {src_rel: l5c_audit.sha256_file(src_full)}
        patches = [{"src_path": src_rel, "old": "return 1", "new": "return 2"}]
        evidence = [{"evidence_quote": "AssertionError: boom",
                     "output_tail": "prefix\nAssertionError: boom\nsuffix"}]
        meta = [{"id": "M-L5C5-%03d" % i, "status": "KILLED",
                 "attributed": True, "result": "KILLED"} for i in range(9)]
        conditions = {}
        for name in l5c_audit.GATE_CONDITIONS:
            conditions[name] = True
        gate = {"gate": True, "conditions": conditions}
        return {"lock": lock, "patches": patches, "evidence": evidence,
                "meta": meta, "gate": gate}

    def _doc_text(self, region):
        return ("# Test integrity audit\n\nbefore\n"
                + l5c_audit.L5C_BEGIN_MARKER + region
                + l5c_audit.L5C_END_MARKER + "\nafter\n")

    def _install(self):
        ledger = self._good_ledger()
        self._write(self.doc_path,
                    self._doc_text(l5c_audit.render_region(ledger)))
        self._write(self.ledger_path, json.dumps(ledger, sort_keys=True))
        return ledger

    def _verify(self):
        return l5c_audit.verify_evidence(self.doc_path, self.ledger_path,
                                         self.root)

    def _rewrite_ledger(self, ledger):
        self._write(self.doc_path,
                    self._doc_text(l5c_audit.render_region(ledger)))
        self._write(self.ledger_path, json.dumps(ledger, sort_keys=True))

    def _read_ledger(self):
        with open(self.ledger_path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def _read_doc(self):
        with open(self.doc_path, "rb") as handle:
            return handle.read().decode("utf-8")

    # -- the approving path ----------------------------------------------

    def test_verify_accepts_a_matching_ledger_and_doc(self):
        self._install()
        self.assertEqual(self._verify(), [])

    def test_render_region_is_deterministic(self):
        first = {"a": 1, "b": [2, 3]}
        second = {"b": [2, 3], "a": 1}
        self.assertEqual(l5c_audit.render_region(first),
                         l5c_audit.render_region(second))

    def test_render_region_refuses_a_non_object(self):
        for bad in (None, 0, 1.5, True, "str", [1], float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.render_region(bad)

    def test_render_doc_writes_the_region(self):
        ledger = self._good_ledger()
        self._write(self.doc_path, self._doc_text("STALE"))
        written = l5c_audit.render_doc(self.doc_path, ledger)
        self.assertIsInstance(written, int)
        self.assertGreater(written, 0)
        self._rewrite_ledger(ledger)
        self.assertEqual(self._verify(), [])

    def test_render_doc_returns_the_region_byte_count(self):
        ledger = self._good_ledger()
        self._write(self.doc_path, self._doc_text(""))
        written = l5c_audit.render_doc(self.doc_path, ledger)
        self.assertEqual(written,
                         len(l5c_audit.render_region(ledger).encode("utf-8")))

    def test_render_doc_leaves_text_outside_the_markers_alone(self):
        ledger = self._good_ledger()
        self._write(self.doc_path, self._doc_text("STALE"))
        l5c_audit.render_doc(self.doc_path, ledger)
        text = self._read_doc()
        self.assertTrue(text.startswith("# Test integrity audit\n\nbefore\n"
                                        + l5c_audit.L5C_BEGIN_MARKER))
        self.assertTrue(text.endswith(l5c_audit.L5C_END_MARKER + "\nafter\n"))

    def test_render_doc_refuses_a_missing_marker(self):
        self._write(self.doc_path, "# no markers at all\n")
        with self.assertRaises(ValueError):
            l5c_audit.render_doc(self.doc_path, self._good_ledger())

    def test_render_doc_refuses_a_duplicated_marker(self):
        ledger = self._good_ledger()
        self._write(self.doc_path,
                    self._doc_text("STALE") + l5c_audit.L5C_END_MARKER + "\n")
        with self.assertRaises(ValueError):
            l5c_audit.render_doc(self.doc_path, ledger)

    # -- every requirement is shown able to go red -----------------------

    def test_verify_reports_hash_mismatch(self):
        self._install()
        self._write(os.path.join(self.root, "module.py"),
                    "def f():\n    return 999\n")
        problems = self._verify()
        self.assertTrue(problems)
        self.assertTrue(any("HASH" in p for p in problems), problems)

    def test_verify_reports_a_moved_anchor(self):
        self._install()
        self._write(os.path.join(self.root, "module.py"),
                    "def f():\n    return 0\n")
        problems = self._verify()
        self.assertTrue(any("ANCHOR" in p for p in problems), problems)

    def test_verify_reports_a_missing_begin_marker(self):
        self._install()
        text = self._read_doc().replace(l5c_audit.L5C_BEGIN_MARKER, "", 1)
        self._write(self.doc_path, text)
        problems = self._verify()
        self.assertTrue(any("REGION" in p for p in problems), problems)

    def test_verify_reports_a_duplicate_end_marker(self):
        self._install()
        self._write(self.doc_path,
                    self._read_doc() + l5c_audit.L5C_END_MARKER + "\n")
        problems = self._verify()
        self.assertTrue(any("REGION" in p for p in problems), problems)

    def test_verify_reports_region_mismatch(self):
        self._install()
        text = self._read_doc().replace("L5C-REGION", "L5C-REGIOX", 1)
        self._write(self.doc_path, text)
        problems = self._verify()
        self.assertTrue(any("REGION" in p for p in problems), problems)

    def test_verify_reports_oversized_quote(self):
        self._install()
        oversized = "x" * (l5c_audit.EVIDENCE_QUOTE_MAX + 1)
        ledger = self._read_ledger()
        ledger["evidence"][0]["evidence_quote"] = oversized
        ledger["evidence"][0]["output_tail"] = oversized
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("QUOTE" in p for p in problems), problems)

    def test_verify_reports_quote_not_in_tail(self):
        self._install()
        ledger = self._read_ledger()
        ledger["evidence"][0]["evidence_quote"] = "never captured"
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("QUOTE" in p for p in problems), problems)

    def test_verify_reports_incomplete_meta(self):
        self._install()
        ledger = self._read_ledger()
        ledger["meta"] = ledger["meta"][:8]
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("META" in p for p in problems), problems)

    def test_verify_reports_unattributed_meta(self):
        self._install()
        ledger = self._read_ledger()
        ledger["meta"][0]["attributed"] = False
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("META" in p for p in problems), problems)

    def test_verify_reports_a_meta_row_that_did_not_die(self):
        self._install()
        ledger = self._read_ledger()
        ledger["meta"][3]["status"] = "SURVIVED"
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("META" in p for p in problems), problems)

    def test_verify_reports_false_gate(self):
        self._install()
        ledger = self._read_ledger()
        ledger["gate"]["gate"] = False
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("GATE" in p for p in problems), problems)

    def test_verify_reports_missing_gate_boolean(self):
        self._install()
        ledger = self._read_ledger()
        del ledger["gate"]["conditions"]["rate"]
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("GATE" in p for p in problems), problems)

    def test_verify_reports_a_false_gate_boolean(self):
        self._install()
        ledger = self._read_ledger()
        ledger["gate"]["conditions"]["measured"] = False
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("GATE" in p for p in problems), problems)

    def test_verify_reports_a_missing_ledger(self):
        self._install()
        os.remove(self.ledger_path)
        problems = self._verify()
        self.assertTrue(problems)
        self.assertTrue(problems[0].startswith("NO-DATA"), problems)

    def test_verify_reports_a_ledger_that_is_not_json(self):
        self._install()
        self._write(self.ledger_path, "this is not json")
        problems = self._verify()
        self.assertTrue(problems)
        self.assertTrue(problems[0].startswith("NO-DATA"), problems)

    def test_verify_reports_an_unreadable_doc(self):
        self._install()
        os.remove(self.doc_path)
        problems = self._verify()
        self.assertTrue(problems)
        self.assertTrue(any("NO-DATA" in p for p in problems), problems)

    # -- hostile input ----------------------------------------------------

    def test_hostile_input_is_refused(self):
        self._install()
        bad_values = (None, 0, 1.5, True, b"bytes", ["a"], float("nan"))
        for bad in bad_values:
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.verify_evidence(bad, self.ledger_path, self.root)
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.verify_evidence(self.doc_path, bad, self.root)
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.verify_evidence(self.doc_path, self.ledger_path, bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                l5c_audit.render_doc(bad, {})

    def test_verify_refuses_a_hostile_lock_key(self):
        self._install()
        ledger = self._read_ledger()
        ledger["lock"] = {None: "abc"}
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("HASH" in p for p in problems), problems)

    def test_verify_refuses_a_hostile_patch_row(self):
        self._install()
        ledger = self._read_ledger()
        ledger["patches"] = [None, 7, {"src_path": None, "old": 3, "new": None}]
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("ANCHOR" in p for p in problems), problems)

    def test_verify_refuses_a_hostile_evidence_row(self):
        self._install()
        ledger = self._read_ledger()
        ledger["evidence"] = [{"evidence_quote": None, "output_tail": None},
                              {"evidence_quote": "x", "output_tail": 9}]
        self._rewrite_ledger(ledger)
        problems = self._verify()
        self.assertTrue(any("QUOTE" in p for p in problems), problems)


class TestProbeRegression(ProbeFixture):
    def test_scratch_tree_default_matches_copy(self):
        self.assertEqual(tuple(mutation_probe.COPY), EXPECTED_DEFAULT_COPY)
        root = tempfile.mkdtemp(prefix="l5c1-copy-")
        for rel in EXPECTED_DEFAULT_COPY:
            full = os.path.join(root, rel)
            if rel.endswith(".py"):
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write("# stub\n")
            else:
                os.makedirs(full, exist_ok=True)
        extra_rel = "scripts/extra_file.py"
        with open(os.path.join(root, extra_rel), "w", encoding="utf-8") as fh:
            fh.write("# extra\n")
        saved = mutation_probe.ROOT
        mutation_probe.ROOT = os.path.realpath(root)
        scratch = None
        try:
            scratch = mutation_probe.scratch_tree()
            for rel in EXPECTED_DEFAULT_COPY:
                self.assertTrue(os.path.exists(os.path.join(scratch, rel)), rel)
        finally:
            mutation_probe.ROOT = saved
            if scratch:
                _remove_tree(scratch)
            _remove_tree(root)

    def test_timeout_is_not_killed(self):
        root = self.make_root()
        saved = mutation_probe.ROOT
        mutation_probe.ROOT = os.path.realpath(root)
        try:
            mutant = {
                "id": "L5C1-hang",
                "why": "the mutated sleep constant runs the suite past the timeout",
                "old": TARGET_OLD,
                "new": 'SLEEP = "30"',
                "expect_test": "ProbeHang.test_sleeps",
            }
            summary = mutation_probe.probe(
                "scripts/target.py",
                "scripts/target_test.py",
                [mutant],
                ("scripts/target.py", "scripts/target_test.py"),
                2,
            )
        finally:
            mutation_probe.ROOT = saved
            _remove_tree(root)
        self.assertEqual(summary["timeout"], 1)
        self.assertEqual(summary["killed"], 0)
        self.assertEqual(summary["valid"], 0)
        self.assertEqual(summary["rows"][0]["result"], "TIMEOUT")
        self.assertTrue(summary["rows"][0]["output_tail"].startswith("TIMEOUT after 2s"))

    def test_help_lists_copy_flag(self):
        # Run in process, never as a child process: a file this build touches
        # starts no child process at all. argparse's --help path is the same
        # code path the probe's own main() runs either way.
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            with self.assertRaises(SystemExit) as caught:
                mutation_probe.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--copy", stream.getvalue())

class TestProbeHostileInput(unittest.TestCase):
    def test_scratch_tree_refuses_non_tuple_and_escape(self):
        for bad in (None, "plugin", ["plugin"], {}, 0, 1.5, float("nan"), True):
            with self.assertRaises(ValueError):
                mutation_probe.scratch_tree(bad)
        with self.assertRaises(ValueError):
            mutation_probe.scratch_tree(("..",))
        with self.assertRaises(ValueError):
            mutation_probe.scratch_tree(("/etc/passwd",))
        with self.assertRaises(ValueError):
            mutation_probe.scratch_tree(("definitely_absent_l5c1",))

    def test_run_test_capture_refuses_bad_arguments(self):
        for bad_root in (None, "", 5, [], {}, float("nan")):
            with self.assertRaises(ValueError):
                mutation_probe.run_test_capture(bad_root, "scripts/x.py", 5)
        for bad_test in (None, "", 5, [], {}, float("nan")):
            with self.assertRaises(ValueError):
                mutation_probe.run_test_capture("/tmp", bad_test, 5)
        for bad_timeout in (None, True, False, 0, -1, "5", 2.5, float("nan")):
            with self.assertRaises(ValueError):
                mutation_probe.run_test_capture("/tmp", "scripts/x.py", bad_timeout)

    def test_probe_and_load_mutants_refuse_bad_arguments(self):
        with self.assertRaises(ValueError):
            mutation_probe.probe(None, "t", [], (), 5)
        with self.assertRaises(ValueError):
            mutation_probe.probe("s", None, [], (), 5)
        with self.assertRaises(ValueError):
            mutation_probe.probe("s", "t", "not-a-list", (), 5)
        with self.assertRaises(ValueError):
            mutation_probe.probe("s", "t", [], "not-a-tuple", 5)
        with self.assertRaises(ValueError):
            mutation_probe.probe("s", "t", [], (), True)
        with self.assertRaises(ValueError):
            mutation_probe.load_mutants(None)
        with self.assertRaises(ValueError):
            mutation_probe.load_mutants("")

    def test_main_refuses_bad_argv(self):
        self.assertEqual(mutation_probe.main("not-a-list"), 2)
        self.assertEqual(mutation_probe.main([1, 2]), 2)


class ManifestTests(unittest.TestCase):
    """L5c.3: manifest, anchor validity and scratch-only execution.

    Every fixture is built under a fresh temp folder and nothing here reads
    the live repository, so this class runs identically in the export copy
    (empty HOME, no docs/plan) and on the real tree. Each assertion states
    what sub unit L5c.3's specification requires, never what the
    implementation happens to return.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="l5c3-manifest-")
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write_bytes(self, rel, payload):
        full = os.path.join(self.root, rel)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(payload)
        return full

    def _write_text(self, rel, text):
        return self._write_bytes(rel, text.encode("utf-8"))

    def _sample(self, **overrides):
        row = {
            "category": "dispatch",
            "id": "M-L5C3-ROW",
            "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py",
            "test_path": "plugin/runtime/brother/core/test_openrouter_dispatch.py",
            "method": "test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down",
            "old": "VALUE = 1\n",
            "new": "VALUE = 2\n",
            "why": "the pre-registered mutant for this row",
            "expect_test": "TestOpenRouterDispatch.test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down",
        }
        row.update(overrides)
        return row

    def _run_with_runner(self, runner, src, test, manifest, copies, timeout,
                         out_json):
        saved = l5c_audit.PROBE_RUNNER
        l5c_audit.PROBE_RUNNER = runner
        try:
            return l5c_audit.run_probe(src, test, manifest, copies, timeout,
                                       out_json)
        finally:
            l5c_audit.PROBE_RUNNER = saved

    def test_validate_sample_rejects_test_edit(self):
        """A row whose src_path IS the suite, or whose src_path names a
        test_ module, is refused by name, and the refusal says why. This
        audit never edits a test (L5C3-REQ-1)."""
        same = self._sample(
            src_path="plugin/runtime/brother/core/test_openrouter_dispatch.py")
        problems = l5c_audit.validate_sample(same, self.root)
        self.assertTrue(problems)
        self.assertTrue(any("mutating the test" in item for item in problems),
                        problems)

        prefixed = self._sample(src_path="scripts/test_mutation_probe.py")
        problems = l5c_audit.validate_sample(prefixed, self.root)
        self.assertTrue(any("mutating the test" in item for item in problems),
                        problems)

        self.assertEqual(l5c_audit.validate_sample(self._sample(), self.root), [])

    def test_validate_sample_hostile_rows_are_returned_refusals(self):
        for bad in (None, 123, 1.5, True, "row", b"row", ["row"], ()):
            self.assertTrue(l5c_audit.validate_sample(bad, self.root), repr(bad))
        for value in (None, 7, True, b"x", ["a"], {"a": "b"}, float("nan")):
            problems = l5c_audit.validate_sample(self._sample(src_path=value),
                                                 self.root)
            self.assertTrue(problems, repr(value))
        for value in (None, 7, True, b"x", ["a"], float("nan")):
            problems = l5c_audit.validate_sample(self._sample(test_path=value),
                                                 self.root)
            self.assertTrue(problems, repr(value))
        with self.assertRaises(ValueError):
            l5c_audit.validate_sample(self._sample(), None)
        with self.assertRaises(ValueError):
            l5c_audit.validate_sample(self._sample(), "")

    def test_validate_sample_refuses_hash_drift(self):
        source = self._write_text(
            "plugin/runtime/brother/core/openrouter_dispatch.py", "VALUE = 1\n")
        digest = l5c_audit.sha256_file(source)
        row = self._sample(src_sha256=digest)
        self.assertEqual(l5c_audit.validate_sample(row, self.root), [])
        self._write_text("plugin/runtime/brother/core/openrouter_dispatch.py",
                         "VALUE = 2\n")
        drifted = l5c_audit.validate_sample(row, self.root)
        self.assertTrue(any("hash drift" in item for item in drifted), drifted)
        missing = self._sample(src_sha256=digest,
                               src_path="plugin/runtime/brother/core/gone.py")
        self.assertTrue(l5c_audit.validate_sample(missing, self.root))

    def test_load_samples_returns_the_pre_registered_rows(self):
        rows = [self._sample(id="M-ONE"), self._sample(id="M-TWO")]
        path = self._write_text("samples.json", json.dumps(rows))
        self.assertEqual(l5c_audit.load_samples(path), rows)

    def test_load_samples_refuses_missing_corrupt_and_empty(self):
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.load_samples(os.path.join(self.root, "absent.json"))
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.load_samples(self._write_bytes("truncated.json", b"{ trunca"))
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.load_samples(self._write_text("object.json", '{"rows": []}'))
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.load_samples(self._write_text("empty.json", "[]"))
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.load_samples(self._write_text("numbers.json", "[1, 2]"))
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.load_samples(self._write_bytes("binary.json", b"\xff\xfe[]"))
        for bad_path in (None, "", 7, True, b"x", ["a"], float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad_path)):
                l5c_audit.load_samples(bad_path)
        self.assertTrue(issubclass(l5c_audit.NoData, ValueError))

    def test_verify_anchor_pending_inventory_is_no_data(self):
        self._write_text(
            "plugin/runtime/brother/core/openrouter_dispatch.py", "VALUE = 1\n")
        pending = self._sample(old=l5c_audit.PENDING_ANCHOR)
        problems = l5c_audit.verify_anchor(self.root, pending)
        self.assertTrue(problems)
        self.assertTrue(any("NO-DATA" in item for item in problems), problems)
        unknown_source = self._sample(src_path=l5c_audit.PENDING_ANCHOR)
        problems = l5c_audit.verify_anchor(self.root, unknown_source)
        self.assertTrue(any("NO-DATA" in item for item in problems), problems)

    def test_verify_anchor_resolves_exactly_once(self):
        self._write_text(
            "plugin/runtime/brother/core/openrouter_dispatch.py", "VALUE = 1\n")
        self.assertEqual(l5c_audit.verify_anchor(self.root, self._sample()), [])

        absent = self._sample(old="NOT PRESENT ANYWHERE\n")
        self.assertTrue(l5c_audit.verify_anchor(self.root, absent))

        self._write_text("plugin/runtime/brother/core/openrouter_dispatch.py",
                         "VALUE = 1\nVALUE = 1\n")
        problems = l5c_audit.verify_anchor(self.root, self._sample())
        self.assertTrue(any("unique" in item for item in problems), problems)

        self._write_text("plugin/runtime/brother/core/openrouter_dispatch.py",
                         "VALUE = 1\n")
        broken = self._sample(new="VALUE = (\n")
        problems = l5c_audit.verify_anchor(self.root, broken)
        self.assertTrue(any("parse" in item for item in problems), problems)

        outside = self._sample(src_path=os.path.join("..", "outside.py"))
        self.assertTrue(l5c_audit.verify_anchor(self.root, outside))
        self.assertTrue(l5c_audit.verify_anchor(self.root, None))
        with self.assertRaises(ValueError):
            l5c_audit.verify_anchor(None, self._sample())

    def test_manifest_for_writes_a_probe_shaped_manifest(self):
        group = [self._sample(id="M-ONE", old="VALUE = 1\n", new="VALUE = 2\n"),
                 self._sample(id="M-TWO", old="OTHER = 3\n", new="OTHER = 4\n")]
        out_dir = os.path.join(self.root, "out")
        path = l5c_audit.manifest_for(group, out_dir)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.dirname(os.path.abspath(path)),
                         os.path.abspath(out_dir))
        with open(path, "rb") as handle:
            parsed = json.loads(handle.read().decode("utf-8"))
        self.assertIsInstance(parsed, list)
        self.assertEqual([entry["id"] for entry in parsed], ["M-ONE", "M-TWO"])
        for entry in parsed:
            for key in ("id", "why", "old", "new", "expect_test", "file"):
                self.assertIn(key, entry)
            self.assertEqual(entry["file"], group[0]["src_path"])
            self.assertEqual(entry["why"], group[0]["why"])
        self.assertEqual(l5c_audit.manifest_for(group, out_dir), path)

    def test_manifest_for_refuses_empty_mixed_and_unresolved_groups(self):
        out_dir = os.path.join(self.root, "out")
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for([], out_dir)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for(
                [self._sample(id="A"),
                 self._sample(id="B", test_path="other/test_something_else.py")],
                out_dir)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for(
                [self._sample(old=l5c_audit.PENDING_ANCHOR)], out_dir)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for([self._sample(id="")], out_dir)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for(
                [self._sample(id="SAME"), self._sample(id="SAME")], out_dir)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for([self._sample(expect_test="")], out_dir)
        with self.assertRaises(l5c_audit.NoData):
            l5c_audit.manifest_for([self._sample(), "not a row"], out_dir)
        with self.assertRaises(ValueError):
            l5c_audit.manifest_for([self._sample()], None)
        with self.assertRaises(ValueError):
            l5c_audit.manifest_for("not a list", out_dir)

    def test_run_probe_without_a_runner_is_no_data(self):
        src = self._write_text("mod.py", "VALUE = 1\n")
        test_path = os.path.join(self.root, "test_mod.py")
        out_json = os.path.join(self.root, "out.json")
        result = self._run_with_runner(None, src, test_path,
                                       os.path.join(self.root, "m.json"),
                                       ["scripts"], 5, out_json)
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIsNone(result["exit"])
        self.assertIsNone(result["summary"])
        self.assertIn("no probe runner is installed", result["detail"])
        copies = [result["argv"][index + 1] for index, item in
                  enumerate(result["argv"]) if item == "--copy"]
        self.assertEqual(copies, [src, test_path, "scripts"])

    def test_run_probe_records_exit_zero_one_and_two(self):
        src = self._write_text("mod.py", "VALUE = 1\n")
        test_path = os.path.join(self.root, "test_mod.py")
        out_json = os.path.join(self.root, "out.json")
        summary = {"src": src, "test": test_path, "valid": 2, "killed": 2,
                   "survived": 0, "rows": []}

        def write_summary():
            with open(out_json, "wb") as handle:
                handle.write(json.dumps(summary).encode("utf-8"))

        def runner_zero(argv):
            write_summary()
            return 0

        result = self._run_with_runner(runner_zero, src, test_path, "m.json",
                                       [], 5, out_json)
        self.assertEqual(result["status"], "MEASURED")
        self.assertEqual(result["exit"], 0)
        self.assertEqual(result["summary"], summary)
        self.assertEqual(result["src_sha256_before"], result["src_sha256_after"])

        def runner_one(argv):
            write_summary()
            return 1

        result = self._run_with_runner(runner_one, src, test_path, "m.json",
                                       [], 5, out_json)
        self.assertEqual(result["status"], "MEASURED")
        self.assertEqual(result["exit"], 1)

        def runner_two(argv):
            write_summary()
            return 2

        result = self._run_with_runner(runner_two, src, test_path, "m.json",
                                       [], 5, out_json)
        self.assertEqual(result["status"], "NO-DATA")
        self.assertEqual(result["exit"], 2)
        self.assertIsNone(result["summary"])

    def test_run_probe_refuses_missing_out_hash_drift_and_bad_summaries(self):
        src = self._write_text("mod.py", "VALUE = 1\n")
        out_json = os.path.join(self.root, "out.json")
        summary = {"src": src, "test": src, "valid": 1, "killed": 1}

        def runner_silent(argv):
            return 0

        result = self._run_with_runner(runner_silent, src, src, "m.json", [],
                                       5, out_json)
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIn("--out", result["detail"])

        def runner_array(argv):
            with open(out_json, "wb") as handle:
                handle.write(b"[1, 2]")
            return 0

        result = self._run_with_runner(runner_array, src, src, "m.json", [],
                                       5, out_json)
        self.assertEqual(result["status"], "NO-DATA")

        def runner_binary(argv):
            with open(out_json, "wb") as handle:
                handle.write(b"\xff\xfe\x00")
            return 0

        result = self._run_with_runner(runner_binary, src, src, "m.json", [],
                                       5, out_json)
        self.assertEqual(result["status"], "NO-DATA")

        def runner_drift(argv):
            with open(out_json, "wb") as handle:
                handle.write(json.dumps(summary).encode("utf-8"))
            with open(src, "wb") as handle:
                handle.write(b"VALUE = 999\n")
            return 0

        result = self._run_with_runner(runner_drift, src, src, "m.json", [],
                                       5, out_json)
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIn("drift", result["detail"])
        self.assertNotEqual(result["src_sha256_before"],
                            result["src_sha256_after"])

    def test_run_probe_refuses_hostile_arguments(self):
        src = self._write_text("mod.py", "VALUE = 1\n")
        out_json = os.path.join(self.root, "out.json")
        for bad_timeout in (None, True, False, 0, -1, "5", 2.5, float("nan")):
            with self.assertRaises(ValueError, msg=repr(bad_timeout)):
                l5c_audit.run_probe(src, src, "m.json", [], bad_timeout, out_json)
        for bad_copies in (None, "scripts", 5, True, float("nan"), {"a": "b"}):
            with self.assertRaises(ValueError, msg=repr(bad_copies)):
                l5c_audit.run_probe(src, src, "m.json", bad_copies, 5, out_json)
        with self.assertRaises(ValueError):
            l5c_audit.run_probe(src, src, "m.json", [7], 5, out_json)
        for bad_args in ((None, src, "m.json", [], 5, out_json),
                         (src, None, "m.json", [], 5, out_json),
                         (src, src, "", [], 5, out_json),
                         (src, src, "m.json", [], 5, None)):
            with self.assertRaises(ValueError, msg=repr(bad_args)):
                l5c_audit.run_probe(*bad_args)

        def runner_returning_bool(argv):
            return True

        result = self._run_with_runner(runner_returning_bool, src, src,
                                       "m.json", [], 5, out_json)
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIsNone(result["exit"])

        def runner_raising(argv):
            raise RuntimeError("boom")

        result = self._run_with_runner(runner_raising, src, src, "m.json", [],
                                       5, out_json)
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIsNone(result["exit"])


class AttributionTests(unittest.TestCase):
    """L5c.4: attributed kills, the sampled gate and the mock-only floor.

    Every assertion states what sub unit L5c.4's specification requires, never
    what the implementation happens to return. Every fixture is built in
    memory, so this class runs identically in the export copy (empty HOME, no
    docs/plan) and on the real tree.
    """

    CATEGORIES = ("vault", "dispatch", "hook")
    METHOD = "test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down"
    TAIL = ("FAIL: TestOpenRouterDispatch." + METHOD + "\n"
            "AssertionError: 5 != 3\n")

    def _sample(self, **overrides):
        row = {
            "id": "M-L5C4-ONE",
            "category": "dispatch",
            "method": self.METHOD,
            "red_token": "AssertionError",
            "state_backed": True,
        }
        row.update(overrides)
        return row

    def _probe_row(self, **overrides):
        row = {
            "id": "M-L5C4-ONE",
            "why": "the pre-registered mutant for this row",
            "file": "plugin/runtime/brother/core/openrouter_dispatch.py",
            "expect_test": "TestOpenRouterDispatch." + self.METHOD,
            "result": "KILLED",
            "detail": "AssertionError in expected test",
            "exit": 1,
            "output_tail": self.TAIL,
        }
        row.update(overrides)
        return row

    def _row(self, category, attributed, state_backed, status, index):
        return {
            "id": "M-L5C4-%03d" % index,
            "category": category,
            "method": "test_sample_%03d" % index,
            "red_token": "AssertionError",
            "state_backed": state_backed,
            "status": status,
            "attributed": attributed,
            "detail": status,
        }

    def _full_sample(self, total=10, killed=9, state_backed=2,
                     categories=None):
        """A whole pre-registered sample: the first `killed` rows are
        attributed kills and the first `state_backed` of those are state
        backed, the mock-only floor's own currency. The rest are survivors."""
        categories = tuple(categories or self.CATEGORIES)
        rows = []
        for index in range(total):
            category = categories[index % len(categories)]
            attributed = index < killed
            rows.append(self._row(category, attributed,
                                  attributed and index < state_backed,
                                  "KILLED" if attributed else "SURVIVED",
                                  index))
        return rows

    def test_bar_is_seventeen_of_twenty(self):
        # BAR_A, BAR_B = 20, 17: the bar is 17 of 20, that is the fractional
        # 8.5, and the rate test is an integer comparison.
        self.assertEqual((l5c_audit.BAR_A, l5c_audit.BAR_B), (20, 17))
        eight = l5c_audit.score(self._full_sample(killed=8, state_backed=2))
        self.assertFalse(eight["conditions"]["rate"])
        self.assertFalse(eight["gate"])
        nine = l5c_audit.score(self._full_sample(killed=9, state_backed=2))
        self.assertTrue(nine["conditions"]["rate"])
        self.assertTrue(nine["gate"])

    def test_score_10_is_reported_but_the_gate_is_the_integer_comparison(self):
        # 849 of 1000 attributed kills is 8.49 of 10 and rounds to 8.5, which
        # is NOT at or above 17 of 20: 849 * 20 = 16980 < 17000. A gate that
        # consulted the rounded report would pass a run the bar refuses.
        result = l5c_audit.score(self._full_sample(total=1000, killed=849,
                                                   state_backed=2))
        self.assertEqual(result["score_10"], 8.5)
        self.assertFalse(result["conditions"]["rate"])
        self.assertFalse(result["gate"])

    def test_score_gate_requires_every_one_of_the_five_conditions(self):
        good = l5c_audit.score(self._full_sample())
        self.assertEqual(good["total"], 10)
        self.assertEqual(good["measured"], 10)
        self.assertEqual(good["killed"], 9)
        self.assertEqual(good["state_backed_killed"], 2)
        self.assertEqual(good["missing"], [])
        self.assertEqual(good["conditions"], {
            "sampled_at_least_ten": True,
            "measured": True,
            "categories": True,
            "rate": True,
            "state_backed": True,
        })
        self.assertTrue(good["gate"])
        self.assertEqual(good["score_10"], 9.0)

        nine_rows = l5c_audit.score(self._full_sample(total=9, killed=9))
        self.assertFalse(nine_rows["conditions"]["sampled_at_least_ten"])
        self.assertFalse(nine_rows["gate"])

        unmeasured = self._full_sample()
        unmeasured[9] = dict(unmeasured[9], status="TIMEOUT", attributed=False)
        blocked = l5c_audit.score(unmeasured)
        self.assertEqual(blocked["measured"], 9)
        self.assertFalse(blocked["conditions"]["measured"])
        self.assertFalse(blocked["gate"])

        no_hook = l5c_audit.score(self._full_sample(categories=("vault", "dispatch")))
        self.assertEqual(no_hook["missing"], ["hook"])
        self.assertFalse(no_hook["conditions"]["categories"])
        self.assertFalse(no_hook["gate"])

        too_few_kills = l5c_audit.score(self._full_sample(killed=8))
        self.assertFalse(too_few_kills["conditions"]["rate"])
        self.assertFalse(too_few_kills["gate"])

        too_few_state = l5c_audit.score(self._full_sample(killed=9, state_backed=1))
        self.assertEqual(too_few_state["state_backed_killed"], 1)
        self.assertFalse(too_few_state["conditions"]["state_backed"])
        self.assertFalse(too_few_state["gate"])

    def test_score_counts_attributed_kills_only(self):
        # Nine of these ten rows went red, but for a reason the sample never
        # registered: an unrelated red is reported by name and is never a
        # kill, so killed is 1 and the rate is far below the bar.
        rows = self._full_sample(total=10, killed=0)
        for index in range(10):
            rows[index] = dict(rows[index], status="UNATTRIBUTED",
                               attributed=False)
        rows[0] = dict(rows[0], status="KILLED", attributed=True,
                       state_backed=True)
        result = l5c_audit.score(rows)
        self.assertEqual(result["killed"], 1)
        self.assertEqual(result["measured"], 10)
        self.assertFalse(result["conditions"]["rate"])
        self.assertFalse(result["gate"])

    def test_score_refuses_corrupt_rows(self):
        base = self._full_sample(total=10, killed=9)[0]
        self.assertTrue(base["attributed"])
        for key in ("attributed", "category", "status"):
            broken = dict(base)
            del broken[key]
            with self.assertRaises(ValueError, msg=key):
                l5c_audit.score([broken])
        for key, value in (("attributed", "yes"), ("attributed", 1),
                           ("attributed", None), ("state_backed", 0),
                           ("state_backed", "yes"), ("category", ["vault"]),
                           ("category", ""), ("category", 7),
                           ("status", None), ("status", True), ("status", "")):
            broken = dict(base)
            broken[key] = value
            with self.assertRaises(ValueError, msg=repr((key, value))):
                l5c_audit.score([broken])
        with self.assertRaises(ValueError):
            l5c_audit.score([base, "not a row"])
        with self.assertRaises(ValueError):
            l5c_audit.score([base, None])
        with self.assertRaises(ValueError):
            l5c_audit.score([base, {"status": "SURVIVED", "category": "vault"}])

    def test_mark_measured_promotes_only_a_real_measurement(self):
        for status in ("KILLED", "SURVIVED", "UNATTRIBUTED", "INFRA"):
            self.assertTrue(
                l5c_audit.mark_measured({"status": status})["measured"], status)
        for status in ("NO-DATA", "INVALID", "TIMEOUT", "MYSTERY"):
            self.assertFalse(
                l5c_audit.mark_measured({"status": status})["measured"], status)
        caller_row = {"status": "SURVIVED"}
        marked = l5c_audit.mark_measured(caller_row)
        self.assertTrue(marked["measured"])
        self.assertNotIn("measured", caller_row)
        self.assertIsNot(marked, caller_row)

    def test_attribute_credits_only_the_registered_red(self):
        sample = self._sample()
        killed = l5c_audit.attribute(sample, {"rows": [self._probe_row()]})
        self.assertEqual(killed["status"], "KILLED")
        self.assertTrue(killed["attributed"])
        self.assertTrue(killed["measured"])
        self.assertEqual(killed["id"], "M-L5C4-ONE")
        self.assertEqual(killed["category"], "dispatch")

        wrong_reason = l5c_audit.attribute(
            sample, {"rows": [self._probe_row(
                output_tail="FAIL: TestOpenRouterDispatch." + self.METHOD +
                            "\nValueError: something else went wrong\n")]})
        self.assertEqual(wrong_reason["status"], "UNATTRIBUTED")
        self.assertFalse(wrong_reason["attributed"])
        self.assertTrue(wrong_reason["measured"])

        wrong_test = l5c_audit.attribute(
            sample, {"rows": [self._probe_row(
                output_tail="FAIL: TestOther.test_other\n"
                            "AssertionError: boom\n")]})
        self.assertEqual(wrong_test["status"], "UNATTRIBUTED")
        self.assertFalse(wrong_test["attributed"])

        no_tail = l5c_audit.attribute(
            sample, {"rows": [self._probe_row(output_tail="")]})
        self.assertEqual(no_tail["status"], "NO-DATA")
        self.assertFalse(no_tail["attributed"])
        self.assertFalse(no_tail["measured"])

        survivor = l5c_audit.attribute(
            sample, {"rows": [self._probe_row(result="SURVIVED",
                                              output_tail="OK\n")]})
        self.assertEqual(survivor["status"], "SURVIVED")
        self.assertFalse(survivor["attributed"])
        self.assertTrue(survivor["measured"])

        for unmeasured in ("INVALID", "TIMEOUT"):
            row = l5c_audit.attribute(
                sample, {"rows": [self._probe_row(result=unmeasured)]})
            self.assertEqual(row["status"], unmeasured)
            self.assertFalse(row["measured"])
            self.assertFalse(row["attributed"])

        unknown = l5c_audit.attribute(
            sample, {"rows": [self._probe_row(result="MYSTERY")]})
        self.assertEqual(unknown["status"], "NO-DATA")
        self.assertFalse(unknown["measured"])

        absent = l5c_audit.attribute(
            sample, {"rows": [self._probe_row(id="M-OTHER")]})
        self.assertEqual(absent["status"], "NO-DATA")
        self.assertIn("M-L5C4-ONE", absent["detail"])

        with self.assertRaises(ValueError):
            l5c_audit.attribute(sample, {"rows": [self._probe_row(),
                                                  self._probe_row()]})

    def test_attribute_reads_the_probe_envelope_and_refuses_unregistered_rows(self):
        sample = self._sample()
        ok = l5c_audit.attribute(
            sample, {"status": "MEASURED",
                     "summary": {"rows": [self._probe_row()]}})
        self.assertTrue(ok["attributed"])
        no_runner = l5c_audit.attribute(
            sample, {"status": "NO-DATA",
                     "detail": "no probe runner is installed"})
        self.assertEqual(no_runner["status"], "NO-DATA")
        self.assertFalse(no_runner["measured"])
        self.assertIn("no probe runner is installed", no_runner["detail"])
        empty = l5c_audit.attribute(sample, {})
        self.assertEqual(empty["status"], "NO-DATA")
        self.assertIn("rows", empty["detail"])

        for key in ("id", "category", "method", "red_token"):
            partial = self._sample()
            partial[key] = ""
            row = l5c_audit.attribute(partial, {"rows": [self._probe_row()]})
            self.assertEqual(row["status"], "NO-DATA", key)
            self.assertFalse(row["attributed"], key)
            self.assertIn(key, row["detail"])

    def test_l5c4_hostile_input_is_refused(self):
        sample = self._sample()
        for bad in (None, 123, 1.5, True, "", b"bytes", ["a"], float("nan")):
            with self.assertRaises(ValueError, msg=repr(("sample", bad))):
                l5c_audit.attribute(bad, {"rows": []})
            with self.assertRaises(ValueError, msg=repr(("summary", bad))):
                l5c_audit.attribute(sample, bad)
            with self.assertRaises(ValueError, msg=repr(("row", bad))):
                l5c_audit.mark_measured(bad)
            with self.assertRaises(ValueError, msg=repr(("rows", bad))):
                l5c_audit.score(bad)
        with self.assertRaises(ValueError):
            l5c_audit.mark_measured({})
        with self.assertRaises(ValueError):
            l5c_audit.mark_measured({"status": None})
        with self.assertRaises(ValueError):
            l5c_audit.mark_measured({"status": True})
        with self.assertRaises(ValueError):
            l5c_audit.mark_measured({"status": ""})
        with self.assertRaises(ValueError):
            l5c_audit.mark_measured({"status": ["KILLED"]})
        with self.assertRaises(ValueError):
            l5c_audit.attribute(self._sample(state_backed="yes"), {"rows": []})
        for corrupt_summary in ({}, {"rows": "not a list"},
                                {"rows": [7, "row"]}):
            row = l5c_audit.attribute(sample, corrupt_summary)
            self.assertEqual(row["status"], "NO-DATA", repr(corrupt_summary))
            self.assertFalse(row["attributed"], repr(corrupt_summary))
            self.assertFalse(row["measured"], repr(corrupt_summary))


if __name__ == "__main__":
    unittest.main()
