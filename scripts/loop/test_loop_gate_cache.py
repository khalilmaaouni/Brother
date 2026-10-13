#!/usr/bin/env python3
"""LA4.a: the deterministic intake gates (parity, canary, plan lint) are cached by a content key.

WHY. A relaunch on unchanged tools re-ran the parity test, the canary and the plan lint although no input had
changed (the cached run at 14:37:49 to 14:39:18 spent 1:29 on them). A gate now stores ONLY an OK, keyed by the bytes
it read; a hit prints `OK ... (cached, key xxxxxxxx)` without running the gate. Any changed byte, a corrupt or stale
cache, or a key that cannot be computed runs the real gate. A FAIL, a NO-DATA or a FINDINGS is never stored.

The mutations that must turn this suite red, each against its own case:
  M-LA4-KEY       the bin directory is left out of parity_key   -> test_one_changed_byte_in_a_bin_tool_misses_the_parity_cache
  M-LA4-FAIL      cached_gate stores any verdict                -> test_a_failure_is_never_stored_and_the_next_launch_runs_the_gate
  M-LA4-CORRUPT   gate_cache_get returns whatever the file holds -> test_a_damaged_or_stale_cache_is_a_miss
  M-LA4-AGE       the 24 h age bound is dropped                 -> test_a_damaged_or_stale_cache_is_a_miss
  M-LA4-BASE      the resolved base commit leaves canary_key    -> test_a_new_base_commit_misses_the_canary_cache
  M-LA4-BUILD     the build file hash leaves canary_key         -> test_each_canary_input_changes_the_canary_key
  M-LA4-SCOPE     the scope leaves lint_key                     -> test_each_lint_input_changes_the_lint_key
  M-LA4-FINDINGS  a FINDINGS answer is stored                   -> test_a_lint_with_findings_is_not_stored_and_a_clean_lint_is

Everything outside the test's temporary folder is injected: no network, no real model, program or machine state.
Python 3.9 and 3.13, standard library only. Run: python3 -B scripts/loop/test_loop_gate_cache.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_intake as LI  # noqa: E402
import loop_canary as LC  # noqa: E402

K1, K2 = "a" * 64, "b" * 64


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args),
                       cwd=repo, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


class Counter(object):
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def __call__(self):
        self.calls += 1
        return self.answer


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gate-cache-")
        self.cache = os.path.join(self.d, "ev", "loop-intake", "gate-cache.json")

    def tearDown(self):
        for root, dirs, files in os.walk(self.d):
            for n in dirs + files:
                try:
                    os.chmod(os.path.join(root, n), 0o700)
                except OSError:  # sbe: allow-silent test cleanup: a path that cannot be chmodded is still removed by rmtree below
                    pass
        shutil.rmtree(self.d, ignore_errors=True)

    def fake_parity(self):
        repo, bin_dir = os.path.join(self.d, "repo"), os.path.join(self.d, "bin")
        _write(os.path.join(repo, "scripts", "loop", "tool.py"), "print(1)\n")
        _write(os.path.join(repo, "scripts", "loop", "sub", "deep.py"), "print(2)\n")
        _write(os.path.join(repo, "scripts", "top.py"), "print(3)\n")
        _write(os.path.join(repo, "scripts", "top.sh"), "echo 4\n")
        _write(os.path.join(repo, "scripts", "notes.md"), "not keyed\n")
        _write(os.path.join(bin_dir, "tool.py"), "print(1)\n")
        _write(os.path.join(bin_dir, ".deploy-stamp.json"), '{"stamped_revision": "x"}')
        return repo, bin_dir

    def fake_canary(self):
        repo = os.path.join(self.d, "crepo")
        os.makedirs(repo)
        _git(repo, "init", "-q")
        _write(os.path.join(repo, "f.txt"), "one\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m", "one")
        c1 = _git(repo, "rev-parse", "HEAD")
        _write(os.path.join(repo, "f.txt"), "two\n")
        _git(repo, "commit", "-q", "-am", "two")
        c2 = _git(repo, "rev-parse", "HEAD")
        _write(os.path.join(repo, "scripts", "loop", "grade_build.py"), "print('grade')\n")
        build, probe = os.path.join(self.d, "b.json"), os.path.join(self.d, "p.json")
        _write(build, "{}")
        _write(probe, "[]")
        spec = {"sub": "x", "base_commit": c1, "build": build, "probe": probe}
        _write(os.path.join(repo, "docs", "plan", "loop-canary.json"), json.dumps(spec))
        return repo, spec, c1, c2

    def fake_lint(self):
        repo = os.path.join(self.d, "lrepo")
        _write(os.path.join(repo, "docs", "plan", "PLAN.json"), '{"units": []}')
        _write(os.path.join(repo, "docs", "plan", "specs", "U1.md"), "# U1\n")
        _write(os.path.join(repo, "docs", "plan", "loop-roles.json"), "{}")
        _write(os.path.join(repo, "scripts", "loop", "plan_lint.py"), "print('lint')\n")
        ledger = os.path.join(self.d, "ledger.jsonl")
        _write(ledger, "{}\n")
        council = os.path.join(self.d, "council.json")
        council_dir = os.path.join(self.d, "council")
        _write(os.path.join(council_dir, "U1", "prompts", "a.md"), "p\n")
        return repo, ledger, council, council_dir


class TestStore(Base):
    def test_same_inputs_hit_and_do_not_run_the_gate(self):
        run = Counter(("OK", "executed tools equal the versioned ones"))
        self.assertEqual(LI.cached_gate("parity", K1, run, path=self.cache), ("OK", "executed tools equal the versioned ones"))
        self.assertEqual(run.calls, 1)
        state, detail = LI.cached_gate("parity", K1, run, path=self.cache)
        self.assertEqual(run.calls, 1, "a hit must not run the gate")
        self.assertEqual(state, "OK")
        self.assertEqual(detail, "executed tools equal the versioned ones (cached, key aaaaaaaa)")
        # one gate's row never answers for another gate
        other = Counter(("OK", "canary ok"))
        LI.cached_gate("canary", K1, other, path=self.cache)
        self.assertEqual(other.calls, 1)
        with open(self.cache, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["schema"], LI.GATE_CACHE_SCHEMA)
        self.assertEqual(sorted(doc["gates"]), ["canary", "parity"])

    def test_a_hit_never_refreshes_the_stored_time(self):
        LI.gate_cache_put("parity", K1, "ok", now=1000.0, path=self.cache)
        self.assertEqual(LI.gate_cache_get("parity", K1, now=2000.0, path=self.cache), "ok")
        with open(self.cache, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["gates"]["parity"]["at"], 1000.0)

    def test_a_failure_is_never_stored_and_the_next_launch_runs_the_gate(self):
        for answer in (("REFUSED", "the executed tools differ"), ("NO-DATA", "could not run"), ("FINDINGS", "2 units")):
            run = Counter(answer)
            self.assertEqual(LI.cached_gate("parity", K1, run, path=self.cache), answer)
            self.assertEqual(LI.cached_gate("parity", K1, run, path=self.cache), answer)
            self.assertEqual(run.calls, 2, "%s was served from the cache" % answer[0])
            self.assertIsNone(LI.gate_cache_get("parity", K1, path=self.cache))
        # a stored OK is never replaced by a failure under a new key
        LI.cached_gate("parity", K1, Counter(("OK", "ok")), path=self.cache)
        bad = Counter(("REFUSED", "differ"))
        self.assertEqual(LI.cached_gate("parity", K2, bad, path=self.cache), ("REFUSED", "differ"))
        self.assertEqual(bad.calls, 1)
        self.assertEqual(LI.gate_cache_get("parity", K1, path=self.cache), "ok")
        self.assertIsNone(LI.gate_cache_get("parity", K2, path=self.cache))

    def test_a_damaged_or_stale_cache_is_a_miss(self):
        now = 1000000.0
        LI.gate_cache_put("parity", K1, "ok", now=now, path=self.cache)
        self.assertEqual(LI.gate_cache_get("parity", K1, now=now + 10, path=self.cache), "ok")
        self.assertEqual(LI.gate_cache_get("parity", K1, now=now + LI.GATE_CACHE_MAX_AGE_S - 1, path=self.cache), "ok")
        self.assertIsNone(LI.gate_cache_get("parity", K1, now=now + LI.GATE_CACHE_MAX_AGE_S + 1, path=self.cache), "older than 24 h")
        self.assertIsNone(LI.gate_cache_get("parity", K1, now=now - 60, path=self.cache), "dated in the future")
        self.assertIsNone(LI.gate_cache_get("parity", K2, now=now, path=self.cache), "another key")
        self.assertIsNone(LI.gate_cache_get("canary", K1, now=now, path=self.cache), "another gate")
        with open(self.cache, "rb") as fh:
            good = fh.read()
        good_doc = json.loads(good.decode("utf-8"))
        row = good_doc["gates"]["parity"]
        damaged = {
            "truncated": good[:len(good) // 2],
            "corrupt": b"\x00\xff not json",
            "empty": b"",
            "a list": b"[]",
            "wrong schema": json.dumps(dict(good_doc, schema="loop-gate-cache-v0")).encode(),
            "no schema": json.dumps({"gates": good_doc["gates"]}).encode(),
            "gates not a dict": json.dumps(dict(good_doc, gates=[row])).encode(),
            "row not a dict": json.dumps(dict(good_doc, gates={"parity": "ok"})).encode(),
            "at is text": json.dumps(dict(good_doc, gates={"parity": dict(row, at=str(now))})).encode(),
            "at is a bool": json.dumps(dict(good_doc, gates={"parity": dict(row, at=True)})).encode(),
            "no at": json.dumps(dict(good_doc, gates={"parity": {"key": K1, "detail": "ok"}})).encode(),
            "detail not text": json.dumps(dict(good_doc, gates={"parity": dict(row, detail=1)})).encode(),
            "key not text": json.dumps(dict(good_doc, gates={"parity": dict(row, key=None)})).encode(),
        }
        for why, blob in sorted(damaged.items()):
            with open(self.cache, "wb") as fh:
                fh.write(blob)
            self.assertIsNone(LI.gate_cache_get("parity", K1, now=now + 10, path=self.cache), why)
        # a damaged file is replaced whole by the next store, and the gate ran to get there
        run = Counter(("OK", "fresh"))
        self.assertEqual(LI.cached_gate("parity", K1, run, path=self.cache), ("OK", "fresh"))
        self.assertEqual(run.calls, 1)
        self.assertEqual(LI.gate_cache_get("parity", K1, path=self.cache), "fresh")
        # a missing file is a miss, and a stray temp file left by a killed writer is never read
        os.remove(self.cache)
        _write(self.cache + ".tmp", json.dumps(good_doc))
        self.assertIsNone(LI.gate_cache_get("parity", K1, now=now + 10, path=self.cache))

    def test_an_unwritable_cache_keeps_the_gate_verdict(self):
        blocker = os.path.join(self.d, "a-file")
        _write(blocker, "x")
        path = os.path.join(blocker, "loop-intake", "gate-cache.json")
        why = LI.gate_cache_put("parity", K1, "ok", path=path)
        self.assertTrue(isinstance(why, str) and why, "an unwritable cache must say why")
        run = Counter(("OK", "executed tools equal the versioned ones"))
        self.assertEqual(LI.cached_gate("parity", K1, run, path=path), ("OK", "executed tools equal the versioned ones"))
        self.assertEqual(run.calls, 1)

    def test_the_cache_path_is_under_the_evidence_folder(self):
        self.assertEqual(LI.gate_cache_path({"INTAKE_EVIDENCE": "/x/ev"}), "/x/ev/loop-intake/gate-cache.json")
        self.assertTrue(LI.gate_cache_path({}).endswith(os.path.join(".claude", "evidence", "loop-intake", "gate-cache.json")))


class TestParityKey(Base):
    def test_one_changed_byte_in_a_bin_tool_misses_the_parity_cache(self):
        repo, bin_dir = self.fake_parity()
        k = LI.parity_key(repo, bin_dir)
        self.assertTrue(isinstance(k, str) and len(k) == 64)
        self.assertEqual(LI.parity_key(repo, bin_dir), k, "the key is deterministic")
        LI.cached_gate("parity", k, Counter(("OK", "ok")), path=self.cache)
        _write(os.path.join(bin_dir, "tool.py"), "print(9)\n")
        k2 = LI.parity_key(repo, bin_dir)
        self.assertNotEqual(k2, k)
        run = Counter(("OK", "ok"))
        LI.cached_gate("parity", k2, run, path=self.cache)
        self.assertEqual(run.calls, 1, "a changed bin tool must run the gate again")

    def test_each_parity_input_changes_the_key_and_others_do_not(self):
        repo, bin_dir = self.fake_parity()
        base = LI.parity_key(repo, bin_dir, python_version="3.9.6")
        changes = {
            "loop tool": lambda: _write(os.path.join(repo, "scripts", "loop", "tool.py"), "print(0)\n"),
            "nested loop tool": lambda: _write(os.path.join(repo, "scripts", "loop", "sub", "deep.py"), "print(0)\n"),
            "top level py": lambda: _write(os.path.join(repo, "scripts", "top.py"), "print(0)\n"),
            "top level sh": lambda: _write(os.path.join(repo, "scripts", "top.sh"), "echo 0\n"),
            "deploy stamp": lambda: _write(os.path.join(bin_dir, ".deploy-stamp.json"), '{"stamped_revision": "y"}'),
            "new bin file": lambda: _write(os.path.join(bin_dir, "extra.sh"), "echo\n"),
        }
        for why, change in sorted(changes.items()):
            shutil.rmtree(self.d + "/repo"); shutil.rmtree(self.d + "/bin")
            self.fake_parity()
            self.assertEqual(LI.parity_key(repo, bin_dir, python_version="3.9.6"), base)
            change()
            self.assertNotEqual(LI.parity_key(repo, bin_dir, python_version="3.9.6"), base, why)
        shutil.rmtree(self.d + "/repo"); shutil.rmtree(self.d + "/bin")
        self.fake_parity()
        self.assertNotEqual(LI.parity_key(repo, bin_dir, python_version="3.13.1"), base, "interpreter")
        _write(os.path.join(repo, "scripts", "notes.md"), "changed\n")
        _write(os.path.join(repo, "scripts", "loop", "__pycache__", "tool.cpython-39.pyc"), b"\x00")
        self.assertEqual(LI.parity_key(repo, bin_dir, python_version="3.9.6"), base, "not inputs of the parity test")

    def test_an_unreadable_input_runs_the_gate_and_stores_nothing(self):
        repo, bin_dir = self.fake_parity()
        os.chmod(os.path.join(bin_dir, "tool.py"), 0)
        self.assertIsNone(LI.parity_key(repo, bin_dir), "an unreadable bin tool")
        os.chmod(os.path.join(bin_dir, "tool.py"), 0o600)
        os.chmod(os.path.join(repo, "scripts", "loop", "sub", "deep.py"), 0)
        self.assertIsNone(LI.parity_key(repo, bin_dir), "an unreadable loop tool")
        self.assertIsNone(LI.tree_digest(os.path.join(repo, "scripts", "loop")))
        self.assertIsNone(LI.parity_key(repo, os.path.join(self.d, "no-bin")), "a missing bin directory")
        run = Counter(("OK", "ok"))
        self.assertEqual(LI.cached_gate("parity", None, run, path=self.cache), ("OK", "ok"))
        self.assertEqual(LI.cached_gate("parity", None, run, path=self.cache), ("OK", "ok"))
        self.assertEqual(run.calls, 2)
        self.assertFalse(os.path.exists(self.cache), "nothing is stored without a key")

    def test_the_real_parity_probe_is_cached_through_the_store(self):
        _, bin_dir = self.fake_parity()
        seen = []
        saved_cg, saved_env = LI.cached_gate, os.environ.get("BROTHER_DEPLOY_TARGET")
        LI.cached_gate = lambda gate, key, run, path=None: seen.append((gate, key, callable(run))) or ("OK", "x")
        os.environ["BROTHER_DEPLOY_TARGET"] = bin_dir
        try:
            out = LI.real_probes()["parity"]()
        finally:
            LI.cached_gate = saved_cg
            if saved_env is None:
                os.environ.pop("BROTHER_DEPLOY_TARGET", None)
            else:
                os.environ["BROTHER_DEPLOY_TARGET"] = saved_env
        self.assertEqual(out, ("OK", "x"))
        self.assertEqual(len(seen), 1)
        gate, key, runnable = seen[0]
        self.assertEqual(gate, "parity")
        self.assertTrue(isinstance(key, str) and len(key) == 64)
        self.assertTrue(runnable)
        self.assertEqual(key, LI.parity_key(os.path.dirname(os.path.dirname(HERE)), bin_dir))


class TestCanaryKey(Base):
    def test_a_new_base_commit_misses_the_canary_cache(self):
        repo, spec, c1, c2 = self.fake_canary()
        k = LC.canary_key(spec, repo)
        self.assertTrue(isinstance(k, str) and len(k) == 64)
        LI.cached_gate("canary", k, Counter(("OK", "ok")), path=self.cache)
        k2 = LC.canary_key(dict(spec, base_commit=c2), repo)
        self.assertNotEqual(k2, k)
        self.assertEqual(LC.canary_key(dict(spec, base_commit=c1), repo), k)
        run = Counter(("OK", "ok"))
        LI.cached_gate("canary", k2, run, path=self.cache)
        self.assertEqual(run.calls, 1)
        # the commit is keyed by what it RESOLVES to: a name moved to another commit, same spec text, is a new key
        _git(repo, "branch", "canary-base", c1)
        named = dict(spec, base_commit="canary-base")
        before = LC.canary_key(named, repo)
        _git(repo, "branch", "-f", "canary-base", c2)
        self.assertNotEqual(LC.canary_key(named, repo), before, "a moved base commit name")

    def test_each_canary_input_changes_the_canary_key(self):
        repo, spec, c1, c2 = self.fake_canary()
        base = LC.canary_key(spec, repo, python_version="3.9.6")
        self.assertEqual(LC.canary_key(spec, repo, python_version="3.9.6"), base)
        cases = [
            ("a fixture field", lambda s: dict(s, sub="y")),
            ("a second fixture", lambda s: dict(s, fixtures=[{"sub": "z", "base_commit": c2, "build": s["build"]}])),
            ("no probe", lambda s: {k: v for k, v in s.items() if k != "probe"}),
        ]
        for why, alter in cases:
            self.assertNotEqual(LC.canary_key(alter(spec), repo, python_version="3.9.6"), base, why)
        self.assertNotEqual(LC.canary_key(spec, repo, python_version="3.13.1"), base, "interpreter")
        files = [
            ("the build file", spec["build"]),
            ("the probe file", spec["probe"]),
            ("the canary spec file", os.path.join(repo, "docs", "plan", "loop-canary.json")),
            ("a loop tool", os.path.join(repo, "scripts", "loop", "grade_build.py")),
        ]
        for why, path in files:
            with open(path, "rb") as fh:
                old = fh.read()
            _write(path, old + b" ")
            self.assertNotEqual(LC.canary_key(spec, repo, python_version="3.9.6"), base, why)
            _write(path, old)
            self.assertEqual(LC.canary_key(spec, repo, python_version="3.9.6"), base, why + " restored")

    def test_an_unresolvable_commit_or_missing_build_gives_no_key(self):
        repo, spec, c1, c2 = self.fake_canary()
        self.assertIsNone(LC.canary_key(dict(spec, base_commit="deadbeef" * 5), repo), "unknown commit")
        self.assertIsNone(LC.canary_key(dict(spec, build=os.path.join(self.d, "absent.json")), repo), "missing build")
        self.assertIsNone(LC.canary_key(dict(spec, probe=os.path.join(self.d, "absent.json")), repo), "missing named probe")
        bad_fx = {"sub": "z", "base_commit": "f" * 40, "build": spec["build"]}
        self.assertIsNone(LC.canary_key(dict(spec, fixtures=[bad_fx]), repo), "a second fixture that does not resolve")
        self.assertIsNone(LC.canary_key(spec, os.path.join(self.d, "not-a-repo")), "no repository")
        self.assertIsNone(LC.canary_key(None, repo), "no spec")


class TestLintKey(Base):
    def test_each_lint_input_changes_the_lint_key(self):
        repo, ledger, council, council_dir = self.fake_lint()
        roles = os.path.join(repo, "docs", "plan", "loop-roles.json")
        args = dict(ledger=ledger, council=council, council_dir=council_dir, roles=roles)
        base = LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args)
        self.assertTrue(isinstance(base, str) and len(base) == 64)
        self.assertEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), base)
        self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", "^U1$", "report", **args), base, "scope")
        self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "block", **args), base, "mode")
        files = [
            ("the plan", os.path.join(repo, "docs", "plan", "PLAN.json")),
            ("a spec file", os.path.join(repo, "docs", "plan", "specs", "U1.md")),
            ("the roles file", roles),
            ("a loop tool", os.path.join(repo, "scripts", "loop", "plan_lint.py")),
            ("a council prompt", os.path.join(council_dir, "U1", "prompts", "a.md")),
        ]
        for why, path in files:
            with open(path, "rb") as fh:
                old = fh.read()
            st = os.stat(path)
            _write(path, old + b" extra")
            self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), base, why)
            _write(path, old)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), base, why + " restored")
        # a file the plan may name appearing anywhere in the tree changes the key (NEW-EXISTS, TEST-FOUND)
        _write(os.path.join(repo, "scripts", "new_test.py"), "")
        self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), base, "a new file in the tree")
        os.remove(os.path.join(repo, "scripts", "new_test.py"))
        self.assertEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), base)
        # the outside inputs are keyed by their signature: a ledger that grows, or appears, changes the key
        with open(ledger, "ab") as fh:
            fh.write(b"{\"more\": 1}\n")
        self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), base, "ledger signature")
        missing = dict(args, ledger=os.path.join(self.d, "no-ledger"))
        self.assertEqual(LI.stat_signature(missing["ledger"]), "missing")
        self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **missing), base, "ledger missing")
        self.assertNotEqual(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **dict(args, council=ledger)), base, "council file")

    def test_an_unreadable_lint_input_gives_no_key(self):
        repo, ledger, council, council_dir = self.fake_lint()
        args = dict(ledger=ledger, council=council, council_dir=council_dir, roles=os.path.join(repo, "docs", "plan", "loop-roles.json"))
        self.assertIsNone(LI.lint_key(repo, "docs/plan/ABSENT.json", ".", "report", **args), "missing plan")
        os.chmod(os.path.join(repo, "docs", "plan", "specs", "U1.md"), 0)
        self.assertIsNone(LI.lint_key(repo, "docs/plan/PLAN.json", ".", "report", **args), "unreadable spec")
        self.assertIsNone(LI.stat_signature("bad\x00path"), "a path that cannot be asked is None, never missing")

    def test_a_lint_with_findings_is_not_stored_and_a_clean_lint_is(self):
        answers = {"now": ("FINDINGS", "LINT SUMMARY blocking 2 advisory 0 no-data 0 |")}
        calls = []

        def fake_pass_tool(name, args, ok_codes, read=None):
            def probe():
                calls.append(name)
                return answers["now"] if name == "plan_lint.py" else ("OK", name)
            return probe
        saved = (LI.pass_tool, LI.lint_key, os.environ.get("INTAKE_EVIDENCE"))
        LI.pass_tool = fake_pass_tool
        LI.lint_key = lambda *a, **k: K1
        os.environ["INTAKE_EVIDENCE"] = os.path.join(self.d, "ev")
        cwd = os.getcwd()
        os.chdir(os.path.dirname(os.path.dirname(HERE)))   # the lint reads "."; the key is computed only there
        try:
            lint = LI.real_probes()["lint"]
            self.assertEqual(lint(), answers["now"])
            self.assertEqual(lint(), answers["now"])
            self.assertEqual(calls.count("plan_lint.py"), 2, "FINDINGS must never be served from the cache")
            self.assertIsNone(LI.gate_cache_get("lint", K1, path=self.cache))
            answers["now"] = ("OK", "LINT SUMMARY blocking 0 advisory 0 no-data 0 |")
            self.assertEqual(lint(), answers["now"])
            state, detail = lint()
            self.assertEqual(calls.count("plan_lint.py"), 3, "a clean lint is stored and the next launch hits")
            self.assertEqual((state, detail), ("OK", answers["now"][1] + " (cached, key aaaaaaaa)"))
        finally:
            os.chdir(cwd)
            LI.pass_tool, LI.lint_key = saved[0], saved[1]
            if saved[2] is None:
                os.environ.pop("INTAKE_EVIDENCE", None)
            else:
                os.environ["INTAKE_EVIDENCE"] = saved[2]

    def test_the_real_canary_and_lint_probes_are_cached_through_the_store(self):
        seen = []
        saved = (LI.cached_gate, LI.lint_key, LC.canary_key)
        LI.cached_gate = lambda gate, key, run, path=None: seen.append((gate, key, callable(run))) or ("OK", gate)
        LI.lint_key = lambda *a, **k: K1
        LC.canary_key = lambda spec, repo, python_version=None: K2
        cwd = os.getcwd()
        os.chdir(os.path.dirname(os.path.dirname(HERE)))
        try:
            probes = LI.real_probes()
            out = (probes["canary"](), probes["lint"]())
            os.chdir(self.d)   # launched from another tree, the lint reads that "." : no key, the gate runs
            probes["lint"]()
        finally:
            os.chdir(cwd)
            LI.cached_gate, LI.lint_key, LC.canary_key = saved
        self.assertEqual(out, (("OK", "canary"), ("OK", "lint")))
        canary = [s for s in seen if s[0] == "canary"]
        self.assertEqual(seen, [("canary", K2 if LC.load_spec() is not None else None, True), ("lint", K1, True), ("lint", None, True)])
        self.assertEqual(len(canary), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
