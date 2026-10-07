#!/usr/bin/env python3
"""The run uses the newest installed program that PROVES it answers, the owner's pin first, recorded (2026-09-30).

THE INCIDENT. model_router.claude_bin() preferred ~/.local/bin/claude by its place; it was 2.1.251 and did not know
claude-opus-5-5, while the desktop's 2.1.284 did. The mutations that must turn this suite red:

  M_LOCAL_FIRST       claude_bin() prefers ~/.local/bin again, whatever the versions say
  M_LEXICAL_VERSION   candidates are ordered as text, so 2.1.99 reads newer than 2.1.284
  M_IGNORE_PIN        a pin that fails its proof is passed over for another program instead of refusing the start

No model is called: calls go to a fake runner that answers by the program's version; the only real processes are
fake `--version` scripts in a temp directory.
Run from the repository root: python3 -B scripts/loop/test_program_resolution.py
"""
import json, os, shutil, sys, tempfile, unittest
from unittest import mock

LOOP = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [LOOP, os.path.dirname(os.path.dirname(LOOP))]

import model_call as MC  # noqa: E402
import model_reachability as MR  # noqa: E402
import model_router as R  # noqa: E402
import runner_pool  # noqa: E402
from test_config_dispatch import FakeAdmission, INCIDENT  # noqa: E402
from test_model_reachability import REG, answer  # noqa: E402


class Resolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="resolve-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        keys = ("BROTHER_PROOF_CACHE", "BROTHER_OR_STATE_ROOT", "BROTHER_CLAUDE_CALLS_LEDGER", "BROTHER_BRIDGE_CALLS_LEDGER",
                "BROTHER_PROGRAM_RECORD", "BROTHER_CLAUDE_BIN", "BROTHER_CODEX_BIN", "HOME", "PATH")
        saved = {k: os.environ.get(k) for k in keys}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        for k in ("BROTHER_CLAUDE_BIN", "BROTHER_CODEX_BIN"):
            os.environ.pop(k, None)
        self.home = os.path.join(self.tmp, "home")
        os.environ.update(BROTHER_PROOF_CACHE=os.path.join(self.tmp, "proofs.json"), BROTHER_OR_STATE_ROOT=os.path.join(self.tmp, "state"),
                          BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(self.tmp, "calls.jsonl"),
                          BROTHER_BRIDGE_CALLS_LEDGER=os.path.join(self.tmp, "bridge.jsonl"),
                          BROTHER_PROGRAM_RECORD=os.path.join(self.tmp, "programs.json"),
                          HOME=self.home, PATH=os.path.join(self.tmp, "empty-path"))
        paths = R._paths()
        patcher = mock.patch.object(paths, "PACKAGE_BIN_DIRS", (os.path.join("~", ".local", "bin"),))
        patcher.start(); self.addCleanup(patcher.stop)
        adm = MC.admission
        MC.admission = FakeAdmission()
        self.addCleanup(setattr, MC, "admission", adm)
        self.knows = {}   # version -> models that version answers
        self.calls = []

    def program(self, path, version, knows=("claude-opus-5-5",)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\necho '%s (Claude Code)'\n" % version)
        os.chmod(path, 0o755)
        self.knows[os.path.realpath(path)] = set(knows)
        return os.path.realpath(path)

    def local(self, version, knows=()):
        return self.program(os.path.join(self.home, ".local", "bin", "claude"), version, knows)

    def app(self, version, knows=("claude-opus-5-5",)):
        return self.program(os.path.join(self.home, "Library", "Application Support", "Claude", "claude-code", version,
                                         "claude.app", "Contents", "MacOS", "claude"), version, knows)

    def runner(self, argv, stdin, timeout):
        self.calls.append(argv[0])
        model = argv[argv.index("--model") + 1]
        if model not in self.knows.get(os.path.realpath(argv[0]), ()):
            return {"returncode": 1, "stdout": "", "stderr": INCIDENT}
        return {"returncode": 0, "stdout": answer("12", model), "stderr": ""}

    def resolve(self, candidates=None):
        return MR.resolve_program("claude", ["opus55"], REG, runner=self.runner, candidates=candidates)


class NewestProvenWins(Resolution):
    def test_the_local_bin_is_not_first_by_place(self):
        """M_LOCAL_FIRST: at call time, with no record, the newest version wins over ~/.local/bin."""
        self.local("2.1.251"); newest = self.app("2.1.284")
        self.assertEqual(R.claude_bin(), newest, "M_LOCAL_FIRST: ~/.local/bin was chosen over a newer program")

    def test_versions_are_compared_as_numbers(self):
        """M_LEXICAL_VERSION: 2.1.284 is newer than 2.1.99; as text it is not."""
        older = self.app("2.1.99"); newer = self.app("2.1.284")
        sel, proofs, rejected = self.resolve()
        self.assertEqual(sel, newer, "M_LEXICAL_VERSION: %s was chosen over %s" % (sel, newer))
        self.assertEqual(R.newest([older, newer]), newer)

    def test_the_newest_that_answers_is_selected_and_the_rest_are_named(self):
        broken = self.app("2.1.290", knows=())    # newer, but it does not know the model
        good = self.app("2.1.284")
        self.local("2.1.251")
        sel, proofs, rejected = self.resolve()
        self.assertEqual(sel, good)
        self.assertEqual(proofs[0]["status"], "OK"); self.assertEqual(proofs[0]["selected_by"], "newest proven")
        self.assertEqual([r[0] for r in rejected], [broken]); self.assertIn("unrecognized_model", rejected[0][2])
        self.assertNotIn(os.path.realpath(os.path.join(self.home, ".local", "bin", "claude")), self.calls,
                         "an older candidate was paid for after a newer one answered")

    def test_a_candidate_that_cannot_say_its_version_is_never_selected(self):
        mute = os.path.join(self.tmp, "mute-claude")
        with open(mute, "w") as fh:
            fh.write("#!/bin/sh\nexit 3\n")
        os.chmod(mute, 0o755)
        sel, proofs, rejected = self.resolve(candidates=[mute])
        self.assertIsNone(sel)
        self.assertEqual(proofs[0]["status"], "NO-DATA")
        self.assertIn("could not be read", rejected[0][2])


class ThePinIsFirst(Resolution):
    def test_a_failing_pin_is_no_start_never_overridden(self):
        """M_IGNORE_PIN: the pin does not know the model; a newer program that does is installed and must NOT be used."""
        pin = self.local("2.1.251"); self.app("2.1.284")
        os.environ["BROTHER_CLAUDE_BIN"] = pin
        sel, proofs, rejected = self.resolve()
        self.assertIsNone(sel, "M_IGNORE_PIN: a failing pin was overridden by %s" % sel)
        self.assertEqual(proofs[0]["status"], "FAIL")
        self.assertTrue(proofs[0]["pinned"])
        line = MR.line(proofs[0])
        self.assertIn("NO START", line); self.assertIn("owner pin", line)
        self.assertIn("BROTHER_CLAUDE_BIN", rejected[0][2])

    def test_a_pin_that_answers_is_used_even_when_a_newer_one_exists(self):
        pin = self.local("2.1.251", knows=("claude-opus-5-5",)); self.app("2.1.284")
        os.environ["BROTHER_CLAUDE_BIN"] = pin
        sel, proofs, _ = self.resolve()
        self.assertEqual(sel, pin)
        self.assertTrue(proofs[0]["pinned"])


class TheRecord(Resolution):
    def test_the_proven_program_is_what_production_resolves_until_it_changes(self):
        proven = self.app("2.1.284")
        sel, proofs, _ = self.resolve()
        b = proofs[0]["binding"]
        self.assertTrue(MR.write_record({"claude": {"path": b["program"], "version": b["version"], "fingerprint": b["fingerprint"]}}))
        self.assertFalse(MR.write_record({"claude": {"path": b["program"], "version": b["version"], "fingerprint": b["fingerprint"],
                                                     "rejected": [["x", "1", "why"]]}}), "the same programs rewrote the record")
        newer = self.app("2.1.300")
        self.assertEqual(R.claude_bin(), proven, "the proven program lost to an unproven newer one")
        with open(proven, "a") as fh:
            fh.write("# replaced\n")
        self.assertEqual(R.claude_bin(), newer, "a replaced (unproven) program was still read from the record")

    def test_the_three_readers_of_the_record_path_agree(self):
        env = {"BROTHER_PROGRAM_RECORD": "/x/r.json", "HOME": "/h"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(MR.record_path(), R._paths().program_record_path(), runner_pool.program_record())
        os.environ.pop("BROTHER_PROGRAM_RECORD")
        self.assertEqual(MR.record_path(), R._paths().program_record_path())
        self.assertEqual(MR.record_path(), runner_pool.program_record())

    def test_the_intake_record_names_how_each_program_was_chosen(self):
        import loop_intake as LI, datetime
        self.app("2.1.290", knows=()); self.app("2.1.284")
        base = {"does": "x", "when": "inside", "kind": "build", "content": "private", "must_be_chosen": False, "default": "opus55"}
        ok = lambda: ("OK", "fixture")
        probes = {k: ok for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool")}
        probes.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: datetime.datetime(2026, 1, 1, 12, 0),
                      reach=lambda plan: MR.resolve_and_prove(plan, REG, runner=self.runner))
        env = {"BROTHER_WORKER_MIX": "opus55:1", "BROTHER_BUILD_PLAN_MODEL": "off", "BROTHER_REPAIR_ADVISOR_MODEL": "off"}
        rec = LI.prepare({}, "18:00", 10.0, None, {"worker": dict(base, setting="BROTHER_PIN_MODEL")}, REG, probes, env=env)[0]
        self.assertEqual(rec["verdict"], "READY", "\n".join(rec["lines"]))
        self.assertEqual(rec["programs"]["claude"]["version"], "2.1.284")
        self.assertEqual(rec["programs"]["claude"]["selected_by"], "newest proven")
        self.assertEqual(len(rec["programs"]["claude"]["rejected"]), 1)
        self.assertTrue(any(l.startswith("PASSED   over") and "2.1.290" in l for l in rec["lines"]), rec["lines"])
        self.assertIn("export BROTHER_PROGRAM_RECORD=", LI.launch_env(rec, {}))


if __name__ == "__main__":
    unittest.main()
