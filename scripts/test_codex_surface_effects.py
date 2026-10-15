#!/usr/bin/env python3
"""CX2.a: every verb skill carries its effect, and generation refuses a verb
without one.

Each fixture patches the module's own tables with unittest.mock.patch.object
and restores them; none writes to the tree. The generator and its command
line are driven at their entry points (expected, generate, main), not only
through the helpers."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_surface  # noqa: E402

PLAN = {"status": "reads", "next": "reads", "help": "reads", "review": "reads",
        "start": "writes", "deliver": "writes"}


def _verbs_with(verb, description=None, body=None):
    out = []
    for v, d, b in codex_surface.VERBS:
        if v == verb:
            d = d if description is None else description
            b = b if body is None else body
        out.append((v, d, b))
    return tuple(out)


def _run_main(argv):
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["codex_surface.py"] + argv), contextlib.redirect_stdout(out):
        code = codex_surface.main()
    return code, out.getvalue()


class VerbEffects(unittest.TestCase):
    def test_every_verb_declares_exactly_one_effect_matching_the_plan(self):
        self.assertEqual(dict(codex_surface.VERB_EFFECTS), PLAN)
        self.assertEqual(sorted(codex_surface.VERB_EFFECTS), sorted(v for v, _d, _b in codex_surface.VERBS))
        for verb, effect in PLAN.items():
            self.assertEqual(codex_surface.effect_of(verb), effect)
            self.assertEqual(codex_surface.effect_line(verb), codex_surface.EFFECT_LINES[effect])
        self.assertEqual(sorted(codex_surface.EFFECT_LINES), ["reads", "writes"])
        with self.assertRaises(ValueError) as ctx:
            codex_surface.effect_of("ghost")
        self.assertIn("ghost", str(ctx.exception))

    def test_every_generated_skill_renders_its_effect_line(self):
        files = codex_surface.expected(ROOT)
        for verb, effect in PLAN.items():
            text = files["skills/brother-%s/SKILL.md" % verb]
            line = codex_surface.EFFECT_LINES[effect]
            self.assertEqual(text.count(line), 1, verb)
            heading = text.index("# brother %s\n" % verb)
            body = [b for v, _d, b in codex_surface.VERBS if v == verb][0]
            self.assertLess(heading, text.index(line), verb)
            self.assertLess(text.index(line), text.index(body[:60]), verb)
            other = codex_surface.EFFECT_LINES["writes" if effect == "reads" else "reads"]
            self.assertNotIn(other, text, verb)

    def test_generation_refuses_a_verb_with_no_effect(self):
        for missing in ("help", "start"):
            table = dict(PLAN)
            del table[missing]
            with mock.patch.object(codex_surface, "VERB_EFFECTS", table):
                with self.assertRaises(ValueError) as ctx:
                    codex_surface.expected(ROOT)
                self.assertIn(missing, str(ctx.exception))
        with mock.patch.object(codex_surface, "VERB_EFFECTS", {}):
            with self.assertRaises(ValueError) as ctx:
                codex_surface.expected(ROOT)
            self.assertIn("start", str(ctx.exception))  # the first verb in VERBS order
        table = dict(PLAN)
        del table["next"]
        del table["help"]
        with mock.patch.object(codex_surface, "VERB_EFFECTS", table):
            with self.assertRaises(ValueError) as ctx:
                codex_surface.check(ROOT)
            self.assertIn("next", str(ctx.exception))
            with tempfile.TemporaryDirectory(prefix="cx2_") as tmp:
                with self.assertRaises(ValueError):
                    codex_surface.generate(Path(tmp))
                self.assertEqual(list(Path(tmp).iterdir()), [], "a refused generation wrote files")

    def test_generation_refuses_an_unknown_effect_or_a_non_verb(self):
        for bad in ("read", "READS", "", None, 1, ["reads"], "writes "):
            table = dict(PLAN)
            table["status"] = bad
            with mock.patch.object(codex_surface, "VERB_EFFECTS", table):
                with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                    codex_surface.expected(ROOT)
                self.assertIn("status", str(ctx.exception))
        table = dict(PLAN)
        table["ghost"] = "reads"
        with mock.patch.object(codex_surface, "VERB_EFFECTS", table):
            with self.assertRaises(ValueError) as ctx:
                codex_surface.expected(ROOT)
            self.assertIn("ghost", str(ctx.exception))

    def test_a_read_only_verb_may_not_name_a_writing_command(self):
        cases = {
            "--continue flag": "run `brother_run.py --continue --cwd <repo>` first",
            "--resume flag": "run `brother_run.py X --RESUME <dir>`",
            "controller stop": 'run python3 "x/bm_controller.py" stop --project p',
            "controller adopt, wrapped": "run bm_controller.py\n   adopt --project p",
        }
        for label, text in cases.items():
            self.assertIsNotNone(codex_surface.writing_command_in(text), label)
            for field in ("body", "description"):
                kwargs = {field: text}
                with mock.patch.object(codex_surface, "VERBS", _verbs_with("status", **kwargs)):
                    with self.assertRaises(ValueError, msg="%s in %s" % (label, field)) as ctx:
                        codex_surface.expected(ROOT)
                    self.assertIn("status", str(ctx.exception))
        # a non text body is refused by the scan, not passed over
        broken = tuple((v, d, 7 if v == "review" else b) for v, d, b in codex_surface.VERBS)
        with mock.patch.object(codex_surface, "VERBS", broken):
            with self.assertRaises(ValueError) as ctx:
                codex_surface.expected(ROOT)
            self.assertIn("review", str(ctx.exception))

    def test_a_writing_verb_may_name_a_writing_command(self):
        body = [b for v, _d, b in codex_surface.VERBS if v == "start"][0]
        self.assertIsNotNone(codex_surface.writing_command_in(body), "start no longer teaches the stop command")
        codex_surface._validate_effects()
        text = " --continue and --resume and bm_controller.py stop "
        with mock.patch.object(codex_surface, "VERBS", _verbs_with("deliver", body=text)):
            codex_surface._validate_effects()
            self.assertIn("deliver", [v for v, _d, _b in codex_surface.VERBS])

    def test_the_shipped_read_only_verbs_name_no_writing_command(self):
        for verb, description, body in codex_surface.VERBS:
            if codex_surface.effect_of(verb) == "reads":
                self.assertIsNone(codex_surface.writing_command_in(description + " " + body), verb)
        self.assertIsNone(codex_surface.writing_command_in(""))
        self.assertIsNone(codex_surface.writing_command_in("--unfinished and --continued"))

    def test_main_prints_a_refusal_and_exits_1_for_a_verb_with_no_effect(self):
        table = dict(PLAN)
        del table["help"]
        with mock.patch.object(codex_surface, "VERB_EFFECTS", table):
            code, out = _run_main(["--check"])
            self.assertEqual(code, 1)
            self.assertIn("codex_surface: REFUSED: help declares no effect", out)
            with tempfile.TemporaryDirectory(prefix="cx2_") as tmp:
                code, out = _run_main(["--root", tmp])
                self.assertEqual(code, 1)
                self.assertIn("codex_surface: REFUSED:", out)
                self.assertEqual(list(Path(tmp).iterdir()), [])
        # the same entry point with the real tables does not refuse
        code, out = _run_main(["--check"])
        self.assertNotIn("REFUSED", out)


if __name__ == "__main__":
    unittest.main()
