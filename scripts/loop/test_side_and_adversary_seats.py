#!/usr/bin/env python3
"""FX-11.6: the side seats and the adversary seat are decided in loop_roles, and the two scripts wire them in.

loop_roles.side_seat_models and loop_roles.adversary_models are driven with values over a fixture roles mapping and a
fixture registry (nothing here reads docs/plan). unit_runner.py and probe_wave.py run their whole body at import, so
neither is imported: their wiring lines are read from the parsed source.
REQ-FX11-20 (side_seat_models), REQ-FX11-21 (adversary_models)."""
import ast, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_roles  # noqa: E402

PLAIN = {"does": "x", "when": "inside", "must_be_chosen": False}
ROLES = {
    "adversary": dict(PLAIN, kind="build", content="public", setting="BROTHER_ADVERSARY_MODEL", default="deepseek", chain=["muse"]),
    "planner": dict(PLAIN, kind="grade", content="private", setting="BROTHER_BUILD_PLAN_MODEL", default="sonnet", chain=["haiku"]),
    "repair_advisor": dict(PLAIN, kind="grade", content="private", setting="BROTHER_REPAIR_ADVISOR_MODEL", default="sonnet", chain=["haiku"]),
}
REG = {
    "deepseek": {"id": "deepseek/deepseek-chat", "transport": "bridge", "privacy": "public", "kinds": ["build", "grade"]},
    "muse": {"id": "meta/muse", "transport": "bridge", "privacy": "public", "kinds": ["build", "grade"]},
    "tiny": {"id": "meta/tiny", "transport": "bridge", "privacy": "public", "kinds": ["grade"]},
    "sonnet": {"id": "anthropic/sonnet", "transport": "claude", "privacy": "private", "kinds": ["build", "grade", "plan", "prose"]},
    "haiku": {"id": "anthropic/haiku", "transport": "claude", "privacy": "private", "kinds": ["build", "grade", "plan", "prose"]},
}
ON = {"BROTHER_BREAKER": "on"}
OFF = {"BROTHER_BREAKER": "off"}
PAIR = {"deepseek": "deepseek", "deepseek-b": "muse"}
NONE = {"plan": None, "repair": None}
CLEARED = ("BROTHER_TRANSPORTS", "BROTHER_ADVERSARY_MODEL", "BROTHER_BUILD_PLAN_MODEL", "BROTHER_REPAIR_ADVISOR_MODEL",
           "BROTHER_BREAKER", "BROTHER_OR_STATE_ROOT")


def parse(name):
    with open(os.path.join(HERE, name), "rb") as f:
        src = f.read().decode("utf-8")
    return src, ast.parse(src)


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ[k] for k in CLEARED if k in os.environ}
        for k in CLEARED:
            os.environ.pop(k, None)
        os.environ["BROTHER_OR_STATE_ROOT"] = tempfile.mkdtemp(prefix="fx116-")
        os.environ["BROTHER_BREAKER"] = "off"
        self.addCleanup(self.restore)

    def restore(self):
        for k in CLEARED:
            os.environ.pop(k, None)
        os.environ.update(self.saved)


class AdversarySeats(Base):
    def test_off_equals_adversary_model(self):
        for env in ({}, OFF, dict(OFF, BROTHER_ADVERSARY_MODEL="sonnet")):
            self.assertEqual(loop_roles.adversary_models("muse", env, ROLES, REG), PAIR, env)

    def test_on_unset_keeps_the_pair(self):
        for env in (ON, dict(ON, BROTHER_ADVERSARY_MODEL="  ")):
            self.assertEqual(loop_roles.adversary_models("deepseek", env, ROLES, REG), {"deepseek": "deepseek", "deepseek-b": "deepseek"})

    def test_on_chosen_takes_the_seats(self):
        got = loop_roles.adversary_models("deepseek", dict(ON, BROTHER_ADVERSARY_MODEL="sonnet"), ROLES, REG)
        self.assertEqual(got, {"deepseek": "sonnet", "deepseek-b": "muse"})

    def test_chain_without_second_member_keeps_second(self):
        roles = dict(ROLES, adversary=dict(PLAIN, kind="build", content="public", setting="BROTHER_ADVERSARY_MODEL", default="deepseek"))
        got = loop_roles.adversary_models("deepseek", dict(ON, BROTHER_ADVERSARY_MODEL="sonnet"), roles, REG)
        self.assertEqual(got, {"deepseek": "sonnet", "deepseek-b": "deepseek"})

    def test_refused_adversary_is_nodata(self):
        for chosen, why in (("ghost", "not in the model registry"), ("tiny", "cannot do build work")):
            with self.assertRaises(ValueError) as cm:
                loop_roles.adversary_models("muse", dict(ON, BROTHER_ADVERSARY_MODEL=chosen), ROLES, REG)
            self.assertTrue(str(cm.exception).startswith("NO-DATA adversary refused: "), str(cm.exception))
            self.assertIn(why, str(cm.exception))

    def test_unreadable_roles_with_a_choice_is_nodata(self):
        for roles in ({}, {"adversary": "x"}, {"adversary": dict(PLAIN)}):
            with self.assertRaises(ValueError) as cm:
                loop_roles.adversary_models("muse", dict(ON, BROTHER_ADVERSARY_MODEL="sonnet"), roles, REG)
            self.assertTrue(str(cm.exception).startswith("NO-DATA adversary refused: "), str(cm.exception))


class SideSeats(Base):
    def test_side_seats_off_name_nothing(self):
        for env in ({}, OFF):
            self.assertEqual(loop_roles.side_seat_models(env, ROLES, REG), (NONE, None))

    def test_side_seats_named_under_on(self):
        self.assertEqual(loop_roles.side_seat_models(ON, ROLES, REG), ({"plan": "sonnet", "repair": "sonnet"}, None))
        got = loop_roles.side_seat_models(dict(ON, BROTHER_BUILD_PLAN_MODEL="haiku"), ROLES, REG)
        self.assertEqual(got, ({"plan": "haiku", "repair": "sonnet"}, None))

    def test_unreadable_roles_side_seats_none(self):
        seats, why = loop_roles.side_seat_models(ON, {}, REG)
        self.assertEqual(seats, NONE)
        self.assertIn("planner", why or "")


class Hostile(Base):
    def test_hostile_inputs_refused(self):
        nan = float("nan")
        calls = [lambda: loop_roles.adversary_models(0), lambda: loop_roles.adversary_models(None),
                 lambda: loop_roles.adversary_models(True), lambda: loop_roles.adversary_models(b"x"),
                 lambda: loop_roles.adversary_models(""), lambda: loop_roles.adversary_models(nan),
                 lambda: loop_roles.adversary_models(["muse"]), lambda: loop_roles.adversary_models({"a": 1}),
                 lambda: loop_roles.adversary_models("muse", "on"), lambda: loop_roles.adversary_models("muse", []),
                 lambda: loop_roles.adversary_models("muse", {"A": 1}), lambda: loop_roles.adversary_models("muse", {1: "x"}),
                 lambda: loop_roles.adversary_models("muse", {"A": {}}), lambda: loop_roles.adversary_models("muse", ON, "x"),
                 lambda: loop_roles.adversary_models("muse", ON, []), lambda: loop_roles.adversary_models("muse", ON, ROLES, "r"),
                 lambda: loop_roles.side_seat_models(b"x"), lambda: loop_roles.side_seat_models("on"),
                 lambda: loop_roles.side_seat_models(0), lambda: loop_roles.side_seat_models(True),
                 lambda: loop_roles.side_seat_models([]), lambda: loop_roles.side_seat_models(nan),
                 lambda: loop_roles.side_seat_models({"BROTHER_BREAKER": None}), lambda: loop_roles.side_seat_models({"BROTHER_BREAKER": ["on"]}),
                 lambda: loop_roles.side_seat_models(ON, []), lambda: loop_roles.side_seat_models(ON, "x"),
                 lambda: loop_roles.side_seat_models(ON, ROLES, [])]
        for i, call in enumerate(calls):
            with self.assertRaises(ValueError, msg="case %d" % i):
                call()


class Wiring(unittest.TestCase):
    def test_probe_wave_jobs_read_the_seats(self):
        src, tree = parse("probe_wave.py")
        models = [v for node in ast.walk(tree) if isinstance(node, ast.Dict)
                  for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == "model"]
        self.assertEqual(len(models), 1, "one probe job literal names its model")
        seat = models[0]
        self.assertTrue(isinstance(seat, ast.Subscript) and isinstance(seat.value, ast.Name) and seat.value.id == "ADVERSARY_SEATS",
                        ast.get_source_segment(src, seat))

        def assigns_seats(stmt):
            return isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ADVERSARY_SEATS" for t in stmt.targets)
        decided = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            calls = [c for s in node.body for c in ast.walk(s) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                     and c.func.attr == "adversary_models" and isinstance(c.func.value, ast.Name) and c.func.value.id == "loop_roles"]
            seated = [s for s in node.body if assigns_seats(s)]
            refused = [h for h in node.handlers if isinstance(h.type, ast.Name) and h.type.id == "ValueError"
                       and any(assigns_seats(s) and isinstance(s.value, ast.Dict) and not s.value.keys for s in h.body)]
            decided = decided or bool(calls and seated and refused)
        self.assertTrue(decided, "ADVERSARY_SEATS is decided by loop_roles.adversary_models inside a try, empty on ValueError")
        guard = [n for n in ast.walk(tree) if isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "m"
                 and len(n.ops) == 1 and isinstance(n.ops[0], ast.NotIn) and isinstance(n.comparators[0], ast.Name)
                 and n.comparators[0].id == "ADVERSARY_SEATS"]
        self.assertTrue(guard, "a seat that is not decided adds no probe job")

    def test_probe_wave_keeps_its_literal_seat_lines(self):
        src, tree = parse("probe_wave.py")
        top = {t.id for s in tree.body if isinstance(s, ast.Assign) for t in s.targets if isinstance(t, ast.Name)}
        self.assertIn("ADVERSARIES", top)
        self.assertIn("ADVERSARY_MODEL", top)

    def test_unit_runner_passes_role_default(self):
        src, tree = parse("unit_runner.py")
        seen = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "side_model"
                    and len(node.args) == 3 and isinstance(node.args[0], ast.Constant) and node.args[0].value in ("plan", "repair")):
                seen[node.args[0].value] = ast.get_source_segment(src, node.args[1]) or ""
        self.assertEqual(sorted(seen), ["plan", "repair"], "both side_model calls are found")
        for side, advice in seen.items():
            self.assertIn('"cheapest"', advice, side)
            self.assertIn('_role_seats["%s"]' % side, advice, side)
            self.assertIn("_side_advice_line", advice, side)
        decided = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "side_seat_models"]
        self.assertEqual(len(decided), 1, "the side seats are decided once")


if __name__ == "__main__":
    unittest.main()
