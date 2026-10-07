#!/usr/bin/env python3
"""openrouter_dispatch's capability canary asks the frozen candidate's model router, never a stray copy.

WHY. _capability_canary() APPENDED scripts/loop, then ~/.claude/bin, to sys.path and imported model_router, the same
pattern lane F2 fixed in or_fanout._router() (2026-09-27): any earlier sys.path entry holding a model_router.py won (a
PYTHONPATH naming the landing tree's scripts/loop), a router already imported from elsewhere was reused, and a
candidate missing its router fell through to the live bin. The router's can_do() is what lets require_capable() pass a
model to a paid call. Under BROTHER_CODE_ROOT or a proof phase the file loaded must be code_root()/scripts/loop/
model_router.py, else the canary raises and require_capable() refuses with the model quarantined (fail closed).

One staged candidate (deploy_stamped.stage_candidate, as the deploy stages it) for the class, a landing tree and a
live bin each holding a real copy of the router, and a registry named by BROTHER_MODEL_REGISTRY so a refusal can only
come from the guard under test. Each case asks a fresh interpreter, at the entry point require_capable().
Run: python3 -B scripts/test_dispatch_canary_router.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

LOOP = Path(__file__).resolve().parent / "loop"
sys.path.insert(0, str(LOOP))
# A TRACKED FIXTURE, never the checkout's own docs/plan/model-registry.json: that file is not on
# the export allowlist and is never staged into a candidate (stage_candidate follows the python
# import closure only, never a data file), so it exists on the author's machine and nowhere the
# hermetic check or a staged candidate can see it. The fixture ships with every export and lists
# 'deepseek' with the same shape a real registry row needs.
REGISTRY = str(LOOP.parent / "fixtures" / "model-registry-fixture.json")
KEYS = ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE", "BROTHER_CODEX_ROOT", "BROTHER_REPO_ROOT", "BROTHER_MODEL_REGISTRY")


class CanaryRouterIsTheCandidates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="canary-router-")
        cls.box = Path(os.path.realpath(cls.tmp.name))
        try:
            cls.home = cls.box / "home"
            (cls.home / ".claude/hooks").mkdir(parents=True)
            (cls.home / ".claude/hooks/bm_session_cap.py").write_text("import json\n")
            cls.bin = cls.home / ".claude/bin"
            cls.candidate = cls.bin / "candidate"
            import deploy_stamped as D
            with mock.patch.dict(os.environ, {"HOME": str(cls.home)}):
                D.stage_candidate(str(LOOP.parents[1]), str(cls.candidate))
            cls.landing = cls.box / "landing/scripts/loop"
            for d in (cls.landing, cls.bin):
                d.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(LOOP / "model_router.py"), str(d / "model_router.py"))
        except BaseException:
            cls.tmp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, pre_import=None, path=False, **values):
        """(verdict line, loaded router file or None), asking require_capable() in a fresh interpreter; with
        path=True, the line saying whether sys.path came back as it was."""
        env = {k: v for k, v in os.environ.items() if k not in KEYS and not k.startswith("PYTHON")}
        env.update(HOME=str(self.home), BROTHER_MODEL_REGISTRY=REGISTRY, **values)
        code = "import sys\n"
        if pre_import:
            code += "sys.path.insert(0, %r)\nimport model_router\nsys.path.pop(0)\n" % str(pre_import)
        code += ("sys.path.insert(0, %r)\nfrom plugin.runtime.brother.core import openrouter_dispatch as D\n"
                 "before = list(sys.path)\n"
                 "try:\n    D.require_capable('deepseek', 'build'); print('CAPABLE')\n"
                 "except Exception as e:\n    print('REFUSED %%s: %%s' %% (type(e).__name__, e))\n"
                 "print('PATH', 'kept' if sys.path == before else 'changed')\n"
                 "m = sys.modules.get('model_router'); print(m.__file__ if m else None)\n" % str(self.candidate))
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env, cwd=str(self.candidate),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        verdict, path_line, line = p.stdout.strip().splitlines()[-3:]
        return path_line if path else (verdict, (None if line == "None" else os.path.realpath(line)))

    def proof(self):
        return dict(BROTHER_CODE_ROOT=str(self.candidate), BROTHER_PROOF_PHASE="RB")

    def want(self):
        return str(self.candidate / "scripts/loop/model_router.py")

    def parked(self):
        router = self.candidate / "scripts/loop/model_router.py"
        router.rename(self.box / "router.parked")
        self.addCleanup((self.box / "router.parked").rename, router)

    def test_in_a_proof_the_candidates_router_answers(self):
        self.assertEqual(self.ask(**self.proof()), ("CAPABLE", self.want()))

    def test_an_earlier_path_entry_holding_a_router_never_wins_in_a_proof(self):
        self.assertEqual(self.ask(PYTHONPATH=str(self.landing), **self.proof()), ("CAPABLE", self.want()))

    def test_an_earlier_path_entry_holding_a_router_never_wins_outside_a_proof(self):
        self.assertEqual(self.ask(PYTHONPATH=str(self.landing)), ("CAPABLE", self.want()))

    def test_a_router_already_imported_from_elsewhere_refuses_in_a_proof(self):
        verdict, _ = self.ask(pre_import=self.landing, **self.proof())
        self.assertTrue(verdict.startswith("REFUSED QuarantinedCapability") and "canary raised ImportError" in verdict, verdict)

    def test_a_proof_without_a_code_root_refuses(self):
        verdict, _ = self.ask(BROTHER_PROOF_PHASE="RB")
        self.assertTrue(verdict.startswith("REFUSED QuarantinedCapability") and "canary raised Refused" in verdict, verdict)

    def test_a_candidate_missing_its_router_refuses_never_the_live_bin(self):
        self.parked()
        verdict, _ = self.ask(**self.proof())
        self.assertTrue(verdict.startswith("REFUSED QuarantinedCapability") and "canary raised ImportError" in verdict, verdict)

    def test_a_code_root_outside_a_proof_still_refuses_the_live_bin(self):
        self.parked()
        verdict, _ = self.ask(BROTHER_CODE_ROOT=str(self.candidate))
        self.assertTrue(verdict.startswith("REFUSED QuarantinedCapability") and "canary raised ImportError" in verdict, verdict)

    def test_the_routers_directory_is_on_sys_path_for_its_one_import_only(self):
        # as in or_fanout._router(): left on sys.path, it would change what every later bare import in the process finds
        self.assertEqual(self.ask(path=True, **self.proof()), "PATH kept")

    def test_control_outside_a_proof_the_router_beside_the_dispatcher_answers(self):
        self.assertEqual(self.ask(), ("CAPABLE", self.want()))


if __name__ == "__main__":
    unittest.main()
