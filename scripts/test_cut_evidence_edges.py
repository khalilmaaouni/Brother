#!/usr/bin/env python3
"""CUT2.b integrator edges, beside the frozen scripts/test_cut_evidence_contract.py: what the frozen cases do not
reach. The real consumer (cut.reusable_preflight, read by cut.check_mode) refuses a record made under another
checker, interpreter or HOME, refuses a record written before the identity existed (so the full preflight runs),
and decides through cut_driver.shared_evidence_usable (mocked here to prove the call is the decision). The helper
itself is fired at malformed shapes, a raising verifier, the exact edges of its window and mismatched key sets;
affected_checks at an unknown dependency, a diamond, a chain and malformed nodes.

Hermetic: a throwaway git repository, the evidence folder and HOME pointed into a temp tree, no network, no real
cut, export, push or tag. Python 3.9, standard library only. No em or en dashes.
Run: python3 -B scripts/test_cut_evidence_edges.py
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_driver as D  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

V = "9.9.9"
EXPORT_ID = "e" * 40
BOTH_OK = ("OK       no-cut-commit    x\nOK       export-real-home y\nOK       virgin-gate      z\n"
           "cut-preflight: CLEAR\n")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
KEYS = ("code", "checker", "policy", "interpreter", "environment", "export", "host", "home")


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class CutRunner(object):
    """cut.py's runner shape: the export tree id answers EXPORT_ID, git is real, anything else exits 0 silent."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if "--export-tree-id" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "EXPORT-TREE: %s\n" % EXPORT_ID, "")
        if cmd[0] == "git":
            return subprocess.run(cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def preflights(self):
        return [c for c in self.calls if "cut_preflight.py" in " ".join(c) and "--export-tree-id" not in c]


class Consumer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut2b-")
        self.ev = os.path.join(self.tmp, "ev")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        env = dict(GIT_ENV, BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev, HOME=self.home,
                   BROTHER_SCRATCH=os.path.join(self.tmp, "scratch"))
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        git(self.tmp, "init", "-q", "-b", "main", self.root)
        with open(os.path.join(self.root, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(self.root, "add", "a.txt")
        git(self.root, "commit", "-q", "-m", "candidate")
        self.cand = git(self.root, "rev-parse", "HEAD")
        self.tree = git(self.root, "rev-parse", "HEAD^{tree}")
        log = os.path.join(self.tmp, "preflight.log")
        with open(log, "w") as fh:
            fh.write(BOTH_OK)
        self.path = CR.record_preflight(V, self.cand, self.tree, EXPORT_ID, "PASS", log, 1.0)

    def edit(self, fn):
        with open(self.path) as fh:
            data = json.load(fh)
        fn(data)
        with open(self.path, "w") as fh:
            json.dump(data, fh)

    def reusable(self):
        return C.reusable_preflight(self.root, V, CutRunner())

    def check_mode(self):
        runner, out = CutRunner(), io.StringIO()
        with mock.patch.object(C, "chain", lambda *a, **k: ([], [])), \
                mock.patch.object(C, "_cut_own_paths", lambda root: ()), contextlib.redirect_stdout(out):
            C.check_mode(self.root, V, (None, "", "test"), (), None, runner=runner)
        return runner.preflights(), out.getvalue()

    def test_the_writer_records_every_identity_and_the_reader_reuses_it(self):
        with open(self.path) as fh:
            inputs = json.load(fh)["inputs"]
        self.assertEqual(sorted(inputs), sorted(KEYS))
        self.assertEqual(inputs["home"], self.home)
        self.assertEqual(self.reusable(), (self.path, ""))

    def test_another_checker_or_interpreter_is_refused_by_the_real_consumer(self):
        for key in ("checker", "interpreter", "policy", "host", "code"):
            with self.subTest(key=key):
                saved = {}
                self.edit(lambda d: (saved.update(d["inputs"]), d["inputs"].__setitem__(key, "z" * 64)))
                path, why = self.reusable()
                self.assertEqual(path, "")
                self.assertIn("changed " + key, why)
                self.edit(lambda d: d.__setitem__("inputs", saved))
                self.assertEqual(self.reusable(), (self.path, ""))

    def test_another_home_is_a_miss(self):
        other = os.path.join(self.tmp, "other-home")
        with mock.patch.dict(os.environ, {"HOME": other}):
            path, why = self.reusable()
        self.assertEqual(path, "")
        self.assertIn("home", why)

    def test_a_record_from_before_the_identity_runs_the_full_preflight(self):
        self.edit(lambda d: d.pop("inputs"))
        path, why = self.reusable()
        self.assertEqual(path, "")
        self.assertIn("names no input identity", why)
        asked, out = self.check_mode()
        self.assertEqual(len(asked), 1, asked)
        self.assertNotIn("--cheap-only", asked[0])
        self.assertIn("preflight not reused", out)

    def test_a_record_lacking_one_new_identity_field_misses(self):
        self.edit(lambda d: d["inputs"].pop("home"))
        path, why = self.reusable()
        self.assertEqual(path, "")
        self.assertIn("changed home", why)

    def test_an_extra_recorded_identity_misses(self):
        self.edit(lambda d: d["inputs"].__setitem__("gpu", "x" * 64))
        path, why = self.reusable()
        self.assertEqual(path, "")
        self.assertIn("changed gpu", why)

    def test_the_consumer_decides_through_the_shared_rule(self):
        with mock.patch.object(D, "shared_evidence_usable", return_value=False) as rule:
            path, why = self.reusable()
            asked, out = self.check_mode()
        self.assertEqual(path, "")
        self.assertIn("not usable shared evidence", why)
        self.assertEqual(rule.call_count, 2)
        shared, expected, _now, _verify = rule.call_args[0]
        self.assertEqual(sorted(expected), sorted(KEYS))
        self.assertEqual((shared["schema"], shared["verdict"]), (1, "PASS"))
        self.assertEqual(len(asked), 1, asked)
        self.assertNotIn("--cheap-only", asked[0])
        self.assertNotIn("REUSED preflight", out)
        # and the rule saying yes is what lets a record of another checker through: the call is the decision
        self.edit(lambda d: d["inputs"].__setitem__("checker", "z" * 64))
        with mock.patch.object(D, "shared_evidence_usable", return_value=True):
            self.assertEqual(self.reusable(), (self.path, ""))

    def test_the_consumer_verifier_rehashes_the_log(self):
        with mock.patch.object(D, "shared_evidence_usable", wraps=D.shared_evidence_usable) as rule:
            self.assertEqual(self.reusable(), (self.path, ""))
        shared, _e, _n, verify = rule.call_args[0]
        self.assertIs(verify(shared), True)
        self.assertIs(verify(dict(shared, evidence_id="0" * 64)), False)


def record(**kw):
    ident = dict((k, k[0] * 64) for k in KEYS)
    base = dict(schema=1, verdict="PASS", inputs=ident, created_at=10, valid_until=200, evidence_id="i" * 64)
    base.update(kw)
    return base, dict(ident)


def yes(_r):
    return True


class Helper(unittest.TestCase):
    def test_window_edges(self):
        rec, ident = record()
        self.assertFalse(D.shared_evidence_usable(rec, ident, 200, yes))
        self.assertTrue(D.shared_evidence_usable(rec, ident, 199.999, yes))
        self.assertTrue(D.shared_evidence_usable(rec, ident, 10, yes))
        self.assertFalse(D.shared_evidence_usable(rec, ident, 9.999, yes))
        for now in (float("nan"), float("inf"), "100", None, True):
            self.assertFalse(D.shared_evidence_usable(rec, ident, now, yes), now)
        for bad in ("10", None, float("nan"), True):
            self.assertFalse(D.shared_evidence_usable(dict(rec, created_at=bad), ident, 100, yes), bad)
            self.assertFalse(D.shared_evidence_usable(dict(rec, valid_until=bad), ident, 100, yes), bad)

    def test_a_raising_or_half_true_verifier_is_a_miss(self):
        rec, ident = record()

        def boom(_r):
            raise OSError("log gone")
        self.assertFalse(D.shared_evidence_usable(rec, ident, 100, boom))
        for answer in (1, "yes", [1], None):
            self.assertFalse(D.shared_evidence_usable(rec, ident, 100, lambda _r, a=answer: a), answer)
        self.assertFalse(D.shared_evidence_usable(rec, ident, 100, None))

    def test_the_verifier_is_not_asked_about_a_miss(self):
        rec, ident = record(verdict="FAIL")
        calls = []
        self.assertFalse(D.shared_evidence_usable(rec, ident, 100, lambda r: calls.append(r) or True))
        self.assertEqual(calls, [])

    def test_non_dict_shapes_never_raise(self):
        rec, ident = record()
        for bad in (None, [], "x", 7, ["schema"]):
            self.assertFalse(D.shared_evidence_usable(bad, ident, 100, yes), bad)
            self.assertFalse(D.shared_evidence_usable(rec, bad, 100, yes), bad)
            self.assertFalse(D.shared_evidence_usable(dict(rec, inputs=bad), ident, 100, yes), bad)
        self.assertFalse(D.shared_evidence_usable(dict(rec, inputs={}), {}, 100, yes))

    def test_schema_is_exactly_the_integer_one(self):
        rec, ident = record()
        for bad in (True, "1", 1.0, 2, None):
            self.assertFalse(D.shared_evidence_usable(dict(rec, schema=bad), ident, 100, yes), bad)
        rec.pop("schema")
        self.assertFalse(D.shared_evidence_usable(rec, ident, 100, yes))

    def test_key_sets_must_match_both_ways(self):
        rec, ident = record()
        extra = dict(rec["inputs"], gpu="x" * 64)
        self.assertFalse(D.shared_evidence_usable(dict(rec, inputs=extra), ident, 100, yes))
        self.assertFalse(D.shared_evidence_usable(rec, dict(ident, gpu="x" * 64), 100, yes))

    def test_an_empty_or_non_text_identity_never_matches(self):
        for bad in ("", None, 5, ["a"]):
            rec, ident = record()
            rec["inputs"]["home"] = bad
            ident["home"] = bad
            self.assertFalse(D.shared_evidence_usable(rec, ident, 100, yes), bad)


def node(inputs=(), deps=(), always=False):
    return dict(inputs=list(inputs), deps=list(deps), always=always)


class Selection(unittest.TestCase):
    def diamond(self):
        return {"base": node(["base.py"]), "left": node(["left.py"], ["base"]),
                "right": node(["right.py"], ["base"]), "top": node(["top.py"], ["left", "right"]),
                "other": node(["other.py"])}

    def test_a_diamond_reaches_both_arms_and_the_top(self):
        self.assertEqual(D.affected_checks(self.diamond(), ["base.py"]), {"base", "left", "right", "top"})
        self.assertEqual(D.affected_checks(self.diamond(), ["left.py"]), {"base", "left", "right", "top"})
        self.assertEqual(D.affected_checks(self.diamond(), ["other.py"]), {"other"})

    def test_downstream_is_transitive(self):
        chain = {"c1": node(["c1.py"]), "c2": node(["c2.py"], ["c1"]), "c3": node(["c3.py"], ["c2"]),
                 "c4": node(["c4.py"], ["c3"])}
        self.assertEqual(D.affected_checks(chain, ["c1.py"]), set(chain))
        self.assertEqual(D.affected_checks(chain, ["c3.py"]), set(chain))

    def test_prerequisites_are_transitive(self):
        chain = {"c1": node(["c1.py"]), "c2": node(["c2.py"], ["c1"]), "c3": node(["c3.py"], ["c2"]),
                 "lone": node(["lone.py"])}
        self.assertEqual(D.affected_checks(chain, ["c3.py"]), {"c1", "c2", "c3"})

    def test_an_unknown_dependency_refuses(self):
        graph = self.diamond()
        graph["top"]["deps"].append("ghost")
        with self.assertRaises(ValueError):
            D.affected_checks(graph, ["other.py"])
        with self.assertRaises(ValueError):
            D.affected_checks(graph, [], known_base=False)

    def test_malformed_nodes_refuse(self):
        for bad in ({"a": "x"}, {"a": dict(inputs=[], deps=[])}, {"a": dict(inputs=[], deps=[], always="yes")},
                    {"a": dict(inputs="a.py", deps=[], always=False)}, {"a": dict(inputs=[], deps=None, always=False)},
                    {"a": dict(inputs=[3], deps=[], always=False)}, ["a"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                D.affected_checks(bad, [])

    def test_a_longer_cycle_refuses(self):
        graph = {"x": node([], ["y"]), "y": node([], ["z"]), "z": node([], ["x"])}
        with self.assertRaises(ValueError):
            D.affected_checks(graph, [])

    def test_changed_paths_of_the_wrong_shape_select_everything(self):
        graph = self.diamond()
        for bad in ("base.py", None, 7, ["base.py", 3]):
            self.assertEqual(D.affected_checks(graph, bad), set(graph), bad)

    def test_several_always_guards_all_run(self):
        graph = dict(self.diamond(), g1=node(["p.json"], always=True), g2=node([], always=True))
        self.assertEqual(D.affected_checks(graph, ["other.py"]), {"other", "g1", "g2"})


if __name__ == "__main__":
    unittest.main()
