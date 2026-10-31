"""SG1.a: scripts/loop_budget_grant.py writes a time boxed project raise, never the override.

Every case builds its own HOME and config in a temp folder; nothing is written under
the real ~/.claude.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
from typing import Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_budget_grant as grant  # noqa: E402

HOUR = 3600.0
FUTURE = "2099-01-01T00:00:00+00:00"
WORDS = "Bypass it automatically for Brother.loop runs"


RUN_GUARD = """import datetime, json, os
CONFIG = None
def selftest() -> int:
    run = os.environ.get("BROTHER_RUN_DIR")
    path = os.path.join(run, "spend-guard.json") if run else CONFIG
    if not path or not os.path.isfile(path):
        return 1
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 1
    if not isinstance(data, dict):
        return 1
    entry = data.get("projects", {}).get("Brother", {})
    if not isinstance(entry, dict):
        return 1
    hard = entry.get("hard_out")
    if hard != 3000100:
        return 1
    until = entry.get("until")
    if not isinstance(until, str) or not until:
        return 1
    try:
        last = datetime.datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return 1
    if last.tzinfo is None:
        return 1
    if last <= datetime.datetime.now(datetime.timezone.utc):
        return 1
    return 0
"""


def _iso(stamp):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(stamp))


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="grant-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.config = os.path.join(self.root, "spend-guard.json")

    def write_config(self, text):
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read_config(self):
        with open(self.config, encoding="utf-8") as fh:
            return fh.read()

    def block(self, tokens=5000000, spent=1000000):
        return grant.grant_block(WORDS, tokens, FUTURE, spent, time.time())


class GrantBlockTest(_Scratch):
    def test_the_block_carries_until_and_words(self):
        block = self.block(tokens=5000000, spent=1000000)
        self.assertEqual(block["until"], FUTURE)
        self.assertIn(WORDS, block["reason"])
        self.assertEqual(block["hard_out"], 6000000)
        self.assertEqual(block["soft_out"], 4000000)
        self.assertEqual(block["daily_hard_out"], 6000000)
        self.assertEqual(block["daily_soft_out"], 4000000)
        self.assertEqual(sorted(block), sorted(grant.BLOCK_KEYS))
        self.assertNotIn("override", block)

    def test_soft_is_floored_at_spent(self):
        block = self.block(tokens=500000, spent=1000000)
        self.assertEqual(block["hard_out"], 1500000)
        self.assertEqual(block["soft_out"], 1000000)

    def test_a_past_until_refuses(self):
        now = time.time()
        with self.assertRaises(ValueError):
            grant.grant_block(WORDS, 1000, _iso(now - HOUR), 0, now)
        for bad in ("2099-01-01T00:00:00", "2099-01-01", "not a time", "", None, 12):
            with self.assertRaises(ValueError, msg=repr(bad)):
                grant.grant_block(WORDS, 1000, bad, 0, now)
        grant.grant_block(WORDS, 1000, "2099-01-01T00:00:00Z", 0, now)

    def test_bad_words_tokens_and_spent_refuse(self):
        now = time.time()
        for words in ("", "   ", None, b"x", ["x"]):
            with self.assertRaises(ValueError, msg=repr(words)):
                grant.grant_block(words, 1000, FUTURE, 0, now)
        for tokens in (0, -1, True, 1e400, 1.5, "1000", None, float("nan")):
            with self.assertRaises(ValueError, msg=repr(tokens)):
                grant.grant_block(WORDS, tokens, FUTURE, 0, now)
        for spent in (-1, True, 1.0, None, "0"):
            with self.assertRaises(ValueError, msg=repr(spent)):
                grant.grant_block(WORDS, 1000, FUTURE, spent, now)
        for when in (float("nan"), float("inf"), True, None, "now"):
            with self.assertRaises(ValueError, msg=repr(when)):
                grant.grant_block(WORDS, 1000, FUTURE, 0, when)


class ApplyGrantTest(_Scratch):
    def test_override_is_untouched(self):
        before = {"override": {"until": "2000-01-01T00:00:00+00:00", "on": False},
                  "default": {"hard_out": 7}, "projects": {"Other": {"hard_out": 1}}}
        self.write_config(json.dumps(before))
        self.assertEqual(grant.apply_grant(self.config, ["A", "B", "C"], self.block()), self.config)
        after = json.loads(self.read_config())
        self.assertEqual(after["override"], before["override"])
        self.assertEqual(after["default"], before["default"])
        self.assertEqual(after["projects"]["Other"], {"hard_out": 1})
        for slug in ("A", "B", "C"):
            self.assertEqual(after["projects"][slug], self.block())
        self.assertEqual(sorted(after), sorted(before))

    def test_no_override_key_is_never_added(self):
        self.write_config("{}")
        grant.apply_grant(self.config, ["A"], self.block())
        self.assertEqual(sorted(json.loads(self.read_config())), ["projects"])

    def test_second_grant_replaces_and_repeat_is_stable(self):
        self.write_config("{}")
        grant.apply_grant(self.config, ["A"], self.block(tokens=1000))
        grant.apply_grant(self.config, ["A"], self.block(tokens=9000))
        first = self.read_config()
        grant.apply_grant(self.config, ["A"], self.block(tokens=9000))
        self.assertEqual(self.read_config(), first)
        self.assertEqual(json.loads(first)["projects"]["A"]["hard_out"], 1009000)

    def test_a_corrupt_config_refuses(self):
        for text in ("not json {", "[1, 2]", '"x"', '{"projects": []}', ""):
            self.write_config(text)
            with self.assertRaises(ValueError, msg=repr(text)):
                grant.apply_grant(self.config, ["A"], self.block())
            self.assertEqual(self.read_config(), text)
            self.assertEqual(os.listdir(self.root), ["spend-guard.json"])
        os.unlink(self.config)
        with self.assertRaises(ValueError):
            grant.apply_grant(self.config, ["A"], self.block())
        self.assertEqual(os.listdir(self.root), [])
        # A write that fails before the rename leaves the old file whole.
        self.write_config('{"keep": 1}')
        with mock.patch("os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(ValueError):
                grant.apply_grant(self.config, ["A"], self.block())
        self.assertEqual(self.read_config(), '{"keep": 1}')
        self.assertEqual(os.listdir(self.root), ["spend-guard.json"])

    def test_bad_slugs_and_blocks_refuse(self):
        self.write_config("{}")
        for slugs in ([], ["a/b"], ["..", "A"], [""], "A", None, [1], [["A"]]):
            with self.assertRaises(ValueError, msg=repr(slugs)):
                grant.apply_grant(self.config, slugs, self.block())
        past = dict(self.block(), until=_iso(time.time() - HOUR))
        with_override = dict(self.block(), override=True)
        no_reason = dict(self.block(), reason="")
        bool_hard = dict(self.block(), hard_out=True)
        for block in (past, with_override, no_reason, bool_hard, {}, None, [1]):
            with self.assertRaises(ValueError, msg=repr(block)):
                grant.apply_grant(self.config, ["A"], block)
        self.assertEqual(self.read_config(), "{}")


class MainTest(_Scratch):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, ".claude", "hooks"))
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write_config('{"override": null}')

    def run_main(self, *extra):
        argv = ["--words", WORDS, "--tokens", "3000000", "--until", FUTURE,
                "--spent", "100", "--config", self.config, "--project-slug", "Brother"] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = grant.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write_guard(self, body):
        with open(os.path.join(self.home, ".claude", "hooks", "spend_guard.py"), "w") as fh:
            fh.write(body)

    def test_spend_guard_selftest_or_no_data(self):
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("guard selftest: NO-DATA", out)
        self.assertEqual(json.loads(self.read_config())["projects"]["Brother"]["hard_out"], 3000100)
        # A guard whose selftest reads the granted config and passes.
        self.write_guard("import json\nCONFIG = None\n"
                         "def selftest():\n"
                         "    with open(CONFIG) as fh:\n"
                         "        return 0 if 'Brother' in json.load(fh)['projects'] else 1\n")
        code, out, _ = self.run_main("--tokens", "4000000")
        self.assertEqual(code, 0)
        self.assertIn("guard selftest: PASS", out)
        # A guard whose selftest fails on the would be file refuses and writes nothing.
        before = self.read_config()
        self.write_guard("CONFIG = None\ndef selftest():\n    return 1\n")
        code, _, err = self.run_main("--tokens", "5000000")
        self.assertEqual(code, 2)
        self.assertIn("selftest", err)
        self.assertEqual(self.read_config(), before)

    def test_dry_prints_and_writes_nothing(self):
        code, out, _ = self.run_main("--dry", "--project-slug", "Two")
        self.assertEqual(code, 0)
        printed = json.loads(out)
        self.assertEqual(sorted(printed["projects"]), ["Brother", "Two"])
        self.assertEqual(self.read_config(), '{"override": null}')

    def test_refusals_exit_2_and_write_nothing(self):
        for extra in (["--tokens", "0"], ["--tokens", "-1"], ["--tokens", "1e400"],
                      ["--tokens", "true"], ["--until", "2099-01-01T00:00:00"],
                      ["--until", _iso(time.time() - HOUR)], ["--words", ""],
                      ["--project-slug", "a/b"], ["--spent", "x"]):
            code, _, err = self.run_main(*extra)
            self.assertEqual(code, 2, extra)
            self.assertIn("REFUSED", err)
        self.write_config("not json")
        self.assertEqual(self.run_main()[0], 2)
        self.assertEqual(self.read_config(), "not json")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                grant.main(["--words", WORDS, "--tokens", "1", "--until", FUTURE,
                            "--config", self.config, "--project-slug", "A"])


class RunGrantTest(_Scratch):
    def setUp(self):
        _Scratch.setUp(self)
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, ".claude", "hooks"))
        self.run_dir = os.path.join(self.root, "run-1")
        os.makedirs(self.run_dir)
        patcher = mock.patch.dict(os.environ, {
            "HOME": self.home,
            "BROTHER_RUN_DIR": self.run_dir,
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = os.path.join(self.home, ".claude", "spend-guard.json")
        self.run_grant = os.path.join(self.run_dir, "spend-guard.json")

    def run_grant_main(self, *extra):
        argv = ["--words", WORDS, "--tokens", "3000000", "--until", FUTURE,
                "--spent", "100", "--config", self.config, "--project-slug", "Brother"] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = grant.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write_guard(self, body):
        with open(os.path.join(self.home, ".claude", "hooks", "spend_guard.py"), "w") as fh:
            fh.write(body)

    def write_run_grant(self, text):
        with open(self.run_grant, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read_run_grant(self):
        with open(self.run_grant, encoding="utf-8") as fh:
            return fh.read()

    def test_the_overseer_session_reads_the_run_grant(self):
        self.write_guard(RUN_GUARD)
        self.write_config(json.dumps({
            "override": None,
            "projects": {"Brother": {"hard_out": 42}},
        }))
        run_block = grant.grant_block(WORDS, 3000000, FUTURE, 100, time.time())
        self.write_run_grant(json.dumps({"projects": {"Brother": run_block}}))
        code, out, err = self.run_grant_main()
        self.assertEqual(code, 0, err)
        self.assertIn("guard selftest: PASS", out)
        saved = json.loads(self.read_config())
        self.assertEqual(saved["projects"]["Brother"]["hard_out"], 3000100)
        self.assertEqual(saved["projects"]["Brother"]["until"], FUTURE)
        run_saved = json.loads(self.read_run_grant())
        self.assertEqual(run_saved["projects"]["Brother"]["hard_out"], 3000100)

    def test_a_run_grant_with_a_wrong_ceiling_is_refused(self):
        self.write_guard(RUN_GUARD)
        self.write_config('{"override": null}')
        run_block = grant.grant_block(WORDS, 3000000, FUTURE, 100, time.time())
        run_block["hard_out"] = 42
        run_block["soft_out"] = 42
        self.write_run_grant(json.dumps({"projects": {"Brother": run_block}}))
        grant_before = self.read_run_grant()
        config_before = self.read_config()
        code, _, err = self.run_grant_main()
        self.assertEqual(code, 2, err)
        self.assertIn("selftest", err)
        self.assertEqual(self.read_config(), config_before)
        self.assertEqual(self.read_run_grant(), grant_before)

    def test_a_run_grant_with_a_past_until_is_refused(self):
        self.write_guard(RUN_GUARD)
        self.write_config('{"override": null}')
        run_block = grant.grant_block(WORDS, 3000000, FUTURE, 100, time.time())
        run_block["until"] = _iso(time.time() - HOUR)
        self.write_run_grant(json.dumps({"projects": {"Brother": run_block}}))
        grant_before = self.read_run_grant()
        config_before = self.read_config()
        code, _, err = self.run_grant_main()
        self.assertEqual(code, 2, err)
        self.assertIn("selftest", err)
        self.assertEqual(self.read_config(), config_before)
        self.assertEqual(self.read_run_grant(), grant_before)

    def test_a_run_grant_with_a_missing_or_unparsing_until_is_refused(self):
        self.write_guard(RUN_GUARD)
        self.write_config('{"override": null}')
        for bad in (None, "", "not a time", 12):
            run_block = grant.grant_block(WORDS, 3000000, FUTURE, 100, time.time())
            run_block["until"] = bad
            self.write_run_grant(json.dumps({"projects": {"Brother": run_block}}))
            grant_before = self.read_run_grant()
            config_before = self.read_config()
            code, _, err = self.run_grant_main()
            self.assertEqual(code, 2, repr(bad))
            self.assertIn("selftest", err)
            self.assertEqual(self.read_config(), config_before)
            self.assertEqual(self.read_run_grant(), grant_before)

    def test_a_run_dir_without_a_grant_never_reads_as_the_safe_case(self):
        self.write_guard(RUN_GUARD)
        block = grant.grant_block(WORDS, 7000000, FUTURE, 100, time.time())
        self.write_config(json.dumps({"projects": {"Brother": block}}))
        empty_run = os.path.join(self.root, "run-empty")
        os.makedirs(empty_run)
        config_before = self.read_config()
        with mock.patch.dict(os.environ, {"BROTHER_RUN_DIR": empty_run}):
            code, _, err = self.run_grant_main()
        self.assertEqual(code, 2, err)
        self.assertIn("selftest", err)
        self.assertEqual(self.read_config(), config_before)
        saved = json.loads(self.read_config())
        self.assertEqual(saved["projects"]["Brother"]["hard_out"], 7000100)
        self.assertEqual(os.listdir(empty_run), [])

    def test_a_torn_run_grant_refuses_and_leaves_it_whole(self):
        self.write_guard(RUN_GUARD)
        self.write_config('{"override": null}')
        self.write_run_grant("not json {")
        with open(self.run_grant, "rb") as fh:
            torn_before = fh.read()
        config_before = self.read_config()
        code, _, err = self.run_grant_main()
        self.assertEqual(code, 2, err)
        self.assertIn("selftest", err)
        self.assertEqual(self.read_config(), config_before)
        with open(self.run_grant, "rb") as fh:
            self.assertEqual(fh.read(), torn_before)


class HostileInputTest(unittest.TestCase):
    VALUES = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1},
              (), {1, 2}, object())
    PUBLIC = (("grant_block", 5), ("render_config", 3), ("apply_grant", 3),
              ("guard_selftest", 2), ("main", 1))

    def test_every_public_function_refuses_hostile_input(self):
        home = tempfile.mkdtemp(prefix="grant-hostile-")
        self.addCleanup(shutil.rmtree, home, True)
        functions = {"grant_block": grant.grant_block, "render_config": grant.render_config,
                     "apply_grant": grant.apply_grant, "guard_selftest": grant.guard_selftest,
                     "main": grant.main}
        with mock.patch.dict(os.environ, {"HOME": home}):
            for name, arity in self.PUBLIC:
                for value in self.VALUES:
                    try:
                        with contextlib.redirect_stdout(io.StringIO()), \
                                contextlib.redirect_stderr(io.StringIO()):
                            functions[name](*([value] * arity))
                    except (ValueError, LookupError, SystemExit, grant.GrantRefused):
                        pass
        self.assertEqual(os.listdir(home), [])


class TheKeysAreTheDeployedReaders(unittest.TestCase):
    """R1 (2026-10-30): a grant is applied only if the guard that enforces it reads the keys this tool writes.
    The pin below is the deployed reader's LIMIT_KEYS; when the reader is on this machine it is read as a literal
    and compared too, and a reader that cannot be read says NO-DATA in the message and still holds the pin."""

    PIN = ("soft_out", "hard_out", "daily_soft_out", "daily_hard_out")

    def test_the_block_carries_the_readers_limit_keys(self):
        import ast
        self.assertEqual(sorted(grant.BLOCK_KEYS[:4]), sorted(self.PIN))
        path = os.path.expanduser("~/.claude/hooks/spend_guard.py")
        live = None
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "LIMIT_KEYS" for t in node.targets):
                    live = tuple(ast.literal_eval(node.value))
        except (OSError, SyntaxError, ValueError):
            live = None
        if live is None:
            print("NO-DATA: the deployed reader %s could not be read here, so only the pin was compared" % path)
            return
        self.assertEqual(sorted(live), sorted(self.PIN), "the deployed reader's LIMIT_KEYS moved; move the pin and the block with it")



if __name__ == "__main__":
    unittest.main()
