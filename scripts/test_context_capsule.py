"""What context_capsule.build_capsule() must keep true.

The roadmap names four properties (docs/plan/1.0.17/WBS-10-SCOUTING-2026-09-13.md,
WBS-10.02): no full chat history, no automatic whole-repo dump, byte-bounded,
and a content hash that changes when its inputs do. Each gets its own case
below rather than one smoke test, since any one of the four silently failing
is the whole feature failing.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context_capsule as C  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def write(cwd, rel, body):
    full = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(body)


class CoreShapeMatchesRunNode(unittest.TestCase):
    """Drop-in compatible with loop_bridge.run_node()'s own unit dict: same
    keys, same field precedence, so model_worker.build_prompt()'s existing
    .get() reads need no changes to consume a capsule instead of a plain
    unit."""

    def test_carries_every_run_node_key(self):
        node = {"id": "u1", "name": "do the thing", "done_check": "true",
                "owns": ["a.py"]}
        cap = C.build_capsule(node, cwd=tempfile.mkdtemp())
        for key in ("unit_id", "objective", "done_check", "write_scope",
                    "read_scope", "role", "risk_class", "attempt",
                    "prior_failure_note"):
            self.assertIn(key, cap)
        self.assertEqual(cap["unit_id"], "u1")
        self.assertEqual(cap["objective"], "do the thing")
        self.assertEqual(cap["write_scope"], ["a.py"])
        self.assertEqual(cap["role"], "builder")

    def test_objective_falls_back_to_id_like_run_node_does(self):
        cap = C.build_capsule({"id": "u2"}, cwd=tempfile.mkdtemp())
        self.assertEqual(cap["objective"], "u2")


class NoFullChatHistory(unittest.TestCase):
    def test_nothing_but_declared_inputs_appears(self):
        node = {"id": "u1", "write_scope": []}
        cap = C.build_capsule(node, cwd=tempfile.mkdtemp())
        dumped = repr(cap)
        # A capsule built with no dependency_outputs/decisions/vault_snippets
        # carries none: there is no transcript-shaped field to have leaked
        # one into.
        self.assertEqual(cap["dependency_outputs"], {})
        self.assertEqual(cap["relevant_decisions"], [])
        self.assertEqual(cap["vault_context"], [])
        self.assertNotIn("chat", dumped.lower())
        self.assertNotIn("transcript", dumped.lower())


class NoAutomaticWholeRepoDump(unittest.TestCase):
    def test_only_declared_paths_are_read(self):
        cwd = tempfile.mkdtemp()
        write(cwd, "declared.py", "x = 1\n")
        write(cwd, "undeclared.py", "y = 2\n")
        write(cwd, "pkg/nested.py", "z = 3\n")
        node = {"id": "u1", "write_scope": ["declared.py"]}
        cap = C.build_capsule(node, cwd=cwd)
        self.assertIn("declared.py", cap["relevant_files"])
        self.assertNotIn("undeclared.py", cap["relevant_files"])
        self.assertNotIn("pkg/nested.py", cap["relevant_files"])

    def test_a_declared_directory_is_skipped_not_expanded(self):
        cwd = tempfile.mkdtemp()
        write(cwd, "pkg/inner.py", "z = 1\n")
        node = {"id": "u1", "write_scope": ["pkg"]}
        cap = C.build_capsule(node, cwd=cwd)
        # "pkg" is a directory, not a file: reading it as a file would raise
        # or, worse, silently walk it. It must simply be absent.
        self.assertEqual(cap["relevant_files"], {})

    def test_missing_declared_file_is_skipped_not_fatal(self):
        node = {"id": "u1", "write_scope": ["nope.py"]}
        cap = C.build_capsule(node, cwd=tempfile.mkdtemp())
        self.assertEqual(cap["relevant_files"], {})


class ByteBounded(unittest.TestCase):
    def test_one_oversized_file_is_truncated(self):
        cwd = tempfile.mkdtemp()
        write(cwd, "big.py", "A" * 10000)
        node = {"id": "u1", "write_scope": ["big.py"]}
        cap = C.build_capsule(node, cwd=cwd, max_file_bytes=100)
        self.assertLessEqual(len(cap["relevant_files"]["big.py"]), 200)
        self.assertIn("truncated", cap["relevant_files"]["big.py"])

    def test_whole_bundle_is_trimmed_to_the_total_budget(self):
        cwd = tempfile.mkdtemp()
        for i in range(20):
            write(cwd, "f%d.py" % i, "x" * 500)
        node = {"id": "u1", "write_scope": ["f%d.py" % i for i in range(20)]}
        vault = [{"snippet": "y" * 500, "source": "vault"} for _ in range(20)]
        cap = C.build_capsule(node, cwd=cwd, vault_snippets=vault,
                              max_file_bytes=1000, max_total_bytes=4000)
        self.assertLessEqual(C._size(cap), 4000 + 1000)  # some slack: the
        # last surviving relevant_files entry is never dropped (see
        # TRIM_ORDER), so an adversarial fixture can still land a bit over.
        # vault_context, being least authoritative, empties first.
        self.assertEqual(cap["vault_context"], [])

    def test_relevant_files_is_never_emptied_below_one_entry(self):
        cwd = tempfile.mkdtemp()
        write(cwd, "only.py", "z" * 5000)
        node = {"id": "u1", "write_scope": ["only.py"]}
        cap = C.build_capsule(node, cwd=cwd, max_file_bytes=5000,
                              max_total_bytes=1)
        self.assertIn("only.py", cap["relevant_files"])


class DeterministicHash(unittest.TestCase):
    def test_identical_inputs_hash_identically(self):
        cwd = tempfile.mkdtemp()
        write(cwd, "a.py", "same content\n")
        node = {"id": "u1", "write_scope": ["a.py"], "done_check": "true"}
        h1 = C.build_capsule(node, cwd=cwd)["capsule_hash"]
        h2 = C.build_capsule(dict(node), cwd=cwd)["capsule_hash"]
        self.assertEqual(h1, h2)

    def test_a_changed_file_changes_the_hash(self):
        cwd = tempfile.mkdtemp()
        write(cwd, "a.py", "version one\n")
        node = {"id": "u1", "write_scope": ["a.py"]}
        h1 = C.build_capsule(node, cwd=cwd)["capsule_hash"]
        write(cwd, "a.py", "version two\n")
        h2 = C.build_capsule(node, cwd=cwd)["capsule_hash"]
        self.assertNotEqual(h1, h2)

    def test_a_changed_dependency_output_changes_the_hash(self):
        node = {"id": "u1", "write_scope": []}
        h1 = C.build_capsule(node, cwd=tempfile.mkdtemp(),
                             dependency_outputs={"upstream": "ok"})["capsule_hash"]
        h2 = C.build_capsule(node, cwd=tempfile.mkdtemp(),
                             dependency_outputs={"upstream": "changed"})["capsule_hash"]
        self.assertNotEqual(h1, h2)

    def test_a_stale_capsule_is_caught_by_rebuild_and_compare(self):
        """The roadmap's own stated use: a caller that wants to know whether
        a capsule it holds is still fresh rebuilds one and compares hashes,
        never trusts a timestamp."""
        cwd = tempfile.mkdtemp()
        write(cwd, "a.py", "fresh\n")
        node = {"id": "u1", "write_scope": ["a.py"]}
        held = C.build_capsule(node, cwd=cwd)
        write(cwd, "a.py", "someone else changed this while it sat around\n")
        rebuilt = C.build_capsule(node, cwd=cwd)
        self.assertNotEqual(held["capsule_hash"], rebuilt["capsule_hash"])


class VaultContextMarkedNonAuthoritative(unittest.TestCase):
    def test_every_vault_entry_is_marked_false(self):
        node = {"id": "u1", "write_scope": []}
        cap = C.build_capsule(node, cwd=tempfile.mkdtemp(),
                              vault_snippets=["a lesson", {"snippet": "b",
                                                           "source": "note-x"}])
        self.assertEqual(len(cap["vault_context"]), 2)
        for entry in cap["vault_context"]:
            self.assertIs(entry["authoritative"], False)
        self.assertEqual(cap["vault_context"][1]["source"], "note-x")


class RelevantDecisionsAreNarrowed(unittest.TestCase):
    def test_only_the_named_fields_survive(self):
        node = {"id": "u1", "write_scope": []}
        decisions = [{"decision": "use path A", "reason": "cheaper",
                      "cost_if_wrong": "one revert", "unrelated_field": "drop me"}]
        cap = C.build_capsule(node, cwd=tempfile.mkdtemp(), decisions=decisions)
        self.assertEqual(cap["relevant_decisions"],
                        [{"decision": "use path A", "reason": "cheaper",
                          "cost_if_wrong": "one revert"}])


if __name__ == "__main__":
    unittest.main()
