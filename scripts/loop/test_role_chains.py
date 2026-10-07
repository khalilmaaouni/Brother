#!/usr/bin/env python3
"""FX-11.5: the finisher, the checker, the repair advisor and the planner walk their role chains.

Every case writes its own roles file in a temporary folder and points BROTHER_LOOP_ROLES at it, sets BROTHER_BREAKER
and a scratch BROTHER_OR_STATE_ROOT for itself, and stands a fake model router (the registry) and fake model callers
in for the real ones, so no model is called and no inherited state is read. loop_roles, model_call and model_router are
never imported here: the edited call sites import loop_roles themselves, exactly as they do in the loop.

usage (repo root): python3 -B scripts/loop/test_role_chains.py
"""
import contextlib, copy, json, os, shutil, sys, tempfile, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_plan  # noqa: E402
import check_wave  # noqa: E402
import finish_run  # noqa: E402
import repair_advisor  # noqa: E402

ROLES_FILE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "plan", "loop-roles.json")
DROP = ("BROTHER_BREAKER", "BROTHER_CHECKER", "BROTHER_FINISHER_MODEL", "BROTHER_BUILD_PLAN_MODEL",
        "BROTHER_REPAIR_ADVISOR_MODEL", "BROTHER_TRANSPORTS", "BROTHER_LOOP_ROLES", "BROTHER_OR_STATE_ROOT")
ADVICE = "1. scripts/x.py: refuse None\n2. scripts/x.py: refuse NaN\n3. scripts/test_x.py: assert both refusals"
PLAN = "FILES: scripts/x.py\nEDITS: add f\nTESTS: scripts/test_x.py\nDONE_CHECK: python3 -B scripts/test_x.py\nCONSTRAINTS: none"
BUILD = json.dumps({"edits": [], "tests": [], "mutations": []})


def _role(**fields):
    spec = {"does": "x", "when": "inside", "kind": "grade", "content": "private", "setting": None, "default": None,
            "must_be_chosen": False}
    spec.update(fields)
    return spec


BASE_ROLES = {
    "checker": _role(content="public", setting="BROTHER_CHECKER", chain=[]),
    "finisher": _role(when="after_run", kind="build", setting="BROTHER_FINISHER_MODEL", chain=["sonnet"], must_be_chosen=True),
    "planner": _role(setting="BROTHER_BUILD_PLAN_MODEL", default="sonnet", chain=["haiku"]),
    "repair_advisor": _role(setting="BROTHER_REPAIR_ADVISOR_MODEL", default="sonnet", chain=["haiku"]),
}


def roles_with(**chains):
    """BASE_ROLES with the named roles' chain replaced; a value of None removes the chain field."""
    roles = copy.deepcopy(BASE_ROLES)
    for role, chain in chains.items():
        if chain is None:
            roles[role].pop("chain", None)
        else:
            roles[role]["chain"] = chain
    return roles


class Att(object):
    """A stand in for model_call.Attempt: the fields the four call sites read."""
    def __init__(self, model, ok, answer="", detail="", status=None):
        self.model, self.ok, self.answer, self.detail, self.seconds = model, ok, answer, detail, 0.5
        self.status = status or ("ANSWERED" if ok else "MALFORMED")


def _row():
    return {"id": "fixture/model", "transport": "claude", "privacy": "private", "cost": 1,
            "kinds": {"build", "grade", "plan", "prose"}, "quality": {"build": 1, "grade": 1, "plan": 1, "prose": 1}}


REGISTRY = {name: _row() for name in ("opus55", "sonnet", "haiku", "fable")}


class _Refused(RuntimeError):
    pass


FAKE_ROUTER = types.SimpleNamespace(PUBLIC="public", PRIVATE="private", Refused=_Refused,
                                    registry=lambda: copy.deepcopy(REGISTRY), transports_allowed=lambda: None,
                                    transports_text=lambda allowed: "")


class FakeMC(object):
    """A stand in for the model_call module handed to finish_run.finisher_attempt. call walks the chain it is given: a
    member in limited answers LIMIT, a member in shut is refused at admission with a capacity reason, any other member
    answers."""
    def __init__(self, limited=(), shut=()):
        self.limited, self.shut, self.walks, self.singles = set(limited), set(shut), [], []

    def call_one(self, name, prompt, kind, sensitivity, timeout=300, **kw):
        self.singles.append((name, kind, sensitivity, timeout, kw))
        return Att(name, True, BUILD)

    def call(self, prompt, kind, sensitivity, timeout=300, expect=None, chain=None, **kw):
        self.walks.append({"kind": kind, "sensitivity": sensitivity, "timeout": timeout, "expect": expect,
                           "chain": list(chain or [])})
        last = Att("nobody", False, detail="empty chain", status="REFUSED_BY_GATE")
        for name in chain or []:
            if name in self.shut:
                last = Att(name, False, detail="capacity: claude:default:%s open until 10:00 (LIMIT)" % name, status="REFUSED_BY_GATE")
            elif name in self.limited:
                last = Att(name, False, detail="usage limit reached", status="LIMIT")
            else:
                return Att(name, True, BUILD)
        return last


class RoleChainsTest(unittest.TestCase):
    def scene(self, breaker="on", roles=None):
        d = tempfile.mkdtemp(prefix="role-chains-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "loop-roles.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema": "fixture roles, version 2", "roles": roles if roles is not None else copy.deepcopy(BASE_ROLES)}, f)
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        for name in DROP:
            os.environ.pop(name, None)
        os.environ["BROTHER_LOOP_ROLES"] = path
        os.environ["BROTHER_OR_STATE_ROOT"] = os.path.join(d, "breaker-state")
        if breaker is not None:
            os.environ["BROTHER_BREAKER"] = breaker
        self.modules(model_router=FAKE_ROUTER)
        self.d = d
        return d

    def modules(self, **fakes):
        patcher = mock.patch.dict(sys.modules, fakes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def callers(self, answer):
        seen = {"one": [], "chain": []}

        def call_one(name, prompt, kind, sensitivity, timeout=300, **kw):
            seen["one"].append(name)
            return Att(name, True, answer)

        def walk(prompt, kind, sensitivity, timeout=300, chain=None, **kw):
            seen["chain"].append(list(chain or []))
            return Att((chain or ["nobody"])[-1], True, answer)

        self.modules(model_call=types.SimpleNamespace(call_one=call_one, call=walk))
        return seen

    def check(self, statuses, checker="opus55"):
        spec = os.path.join(self.d, "X.md")
        with open(spec, "w", encoding="utf-8") as f:
            f.write("# X\n")
        build = os.path.join(self.d, "X.1-r0-build.json")
        with open(build, "w", encoding="utf-8") as f:
            f.write("{}")
        calls = []

        def call_one(name, prompt, kind, sensitivity, timeout=300, runner=None, **kw):
            status = statuses[len(calls)] if len(calls) < len(statuses) else "MALFORMED"
            calls.append(name)
            if status == "ANSWERED":
                return Att(name, True, "VERDICT: LAND AS IS\n")
            if status == "REFUSED_BY_GATE":
                return Att(name, False, detail="capacity: claude:default:%s open until 10:00 (LIMIT)" % name, status=status)
            return Att(name, False, detail="%s from the fixture" % status, status=status)

        def review_brief(argv, **kw):
            with open(argv[-1], "w", encoding="utf-8") as f:
                f.write("the brief")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        self.modules(model_call=types.SimpleNamespace(call_one=call_one),
                     grade_build=types.SimpleNamespace(private_hits=lambda text: []))
        seams = (("sub_of", lambda b: "X.1"), ("unit_of", lambda sub, plan: {"id": "X", "spec": spec}),
                 ("sha", lambda p: "fixture-digest"), ("subprocess", types.SimpleNamespace(run=review_brief)),
                 ("jev_verdict", lambda *a, **k: None))
        with contextlib.ExitStack() as stack:
            for name, value in seams:
                stack.enter_context(mock.patch.object(check_wave, name, value))
            rec = check_wave.check_one(build, checker, {"units": []}, mode="off")
        return rec, calls

    # THE CHECKER
    def test_limit_reaches_second_named_checker_only(self):
        self.scene("on", roles_with(checker=["sonnet"]))
        rec, calls = self.check(["LIMIT", "ANSWERED"])
        self.assertEqual(calls, ["opus55", "sonnet"])
        self.assertTrue(rec.get("retried"))
        self.assertNotEqual(rec["verdict"], "NO-DATA")

    def test_limit_on_a_chain_of_one_reaches_no_unnamed_model(self):
        self.scene("on")
        rec, calls = self.check(["LIMIT", "ANSWERED"])
        self.assertEqual(calls, ["opus55"])
        self.assertEqual(rec["verdict"], "NO-DATA")
        self.assertTrue(rec["reasons"].startswith("no answer from opus55"), rec["reasons"])

    def test_open_key_goes_to_the_next_named_checker(self):
        self.scene("on", roles_with(checker=["sonnet"]))
        rec, calls = self.check(["REFUSED_BY_GATE", "ANSWERED"])
        self.assertEqual(calls, ["opus55", "sonnet"])

    def test_empty_retries_same_model(self):
        self.scene("on", roles_with(checker=["sonnet"]))
        rec, calls = self.check(["EMPTY", "ANSWERED"])
        self.assertEqual(calls, ["opus55", "opus55"])
        self.assertNotEqual(rec["verdict"], "NO-DATA")

    def test_provider_refusal_is_nodata(self):
        self.scene("on", roles_with(checker=["sonnet"]))
        rec, calls = self.check(["PROVIDER_REFUSED", "ANSWERED"])
        self.assertEqual(calls, ["opus55"])
        self.assertEqual(rec["verdict"], "NO-DATA")
        self.assertEqual(rec["reasons"], "provider refused the grade prompt")

    def test_refused_checker_chain_makes_no_call(self):
        self.scene("on", roles_with(checker=["fable"]))
        rec, calls = self.check(["ANSWERED"])
        self.assertEqual(calls, [])
        self.assertEqual(rec["verdict"], "NO-DATA")
        self.assertTrue(rec["reasons"].startswith("roles:"), rec["reasons"])

    def test_one_member_on_equals_off(self):
        self.scene("on")
        on_rec, on_calls = self.check(["EMPTY", "ANSWERED"])
        self.scene("off")
        off_rec, off_calls = self.check(["EMPTY", "ANSWERED"])
        self.assertEqual(on_calls, ["opus55", "opus55"])
        self.assertEqual(on_calls, off_calls)
        self.assertEqual(on_rec["verdict"], off_rec["verdict"])

    def test_off_is_todays_retry_on_the_same_model(self):
        self.scene(None, roles_with(checker=["sonnet"]))
        rec, calls = self.check(["LIMIT", "ANSWERED"])
        self.assertEqual(calls, ["opus55", "opus55"])
        self.assertNotEqual(rec["verdict"], "NO-DATA")

    def test_no_chain_field_is_one_member(self):
        self.scene("on", roles_with(checker=None))
        self.assertEqual(check_wave.checker_chain("opus55"), ["opus55"])
        self.scene("on", roles_with(checker=["sonnet"]))
        self.assertEqual(check_wave.checker_chain("opus55"), ["opus55", "sonnet"])

    def test_retry_target_rules(self):
        rt = check_wave.retry_target
        for status in ("EMPTY", "MALFORMED", "TIMEOUT"):
            self.assertEqual(rt(["a", "b"], "a", status), "a")
        for status in ("LIMIT", "OVERLOAD", "AUTH", "TRANSPORT_DOWN", "OPEN"):
            self.assertEqual(rt(["a", "b"], "a", status), "b")
            self.assertIsNone(rt(["a"], "a", status))
            self.assertIsNone(rt(["a", "b"], "b", status))
        for status in ("ANSWERED", "PROVIDER_REFUSED", "REFUSED_BY_GATE", "banana"):
            self.assertIsNone(rt(["a", "b"], "a", status))

    # THE FINISHER
    def test_finisher_hops_on_limit(self):
        self.scene("on")
        mc = FakeMC(limited=["opus55"])
        a = finish_run.finisher_attempt("opus55", "the brief", mc)
        self.assertTrue(a.ok)
        self.assertEqual(a.model, "sonnet")
        self.assertEqual(mc.walks, [{"kind": "build", "sensitivity": "public", "timeout": 900, "expect": "json",
                                     "chain": ["opus55", "sonnet"]}])
        self.assertEqual(mc.singles, [])

    def test_finisher_off_is_todays_single_call(self):
        self.scene(None, roles_with(finisher=["sonnet", "fable"]))
        mc = FakeMC(limited=["opus55"])
        finish_run.finisher_attempt("opus55", "the brief", mc)
        self.assertEqual(mc.singles, [("opus55", "build", "public", 900, {})])
        self.assertEqual(mc.walks, [])

    def test_all_open_sends_nothing(self):
        self.scene("on")
        mc = FakeMC(shut=["opus55", "sonnet"])
        a = finish_run.finisher_attempt("opus55", "the brief", mc)
        self.assertFalse(a.ok)
        self.assertTrue(a.detail.startswith("capacity:"), a.detail)
        self.assertEqual(mc.singles, [])

    def test_every_member_failing_reads_capacity(self):
        self.scene("on")
        mc = FakeMC(limited=["opus55", "sonnet"])
        a = finish_run.finisher_attempt("opus55", "the brief", mc)
        self.assertFalse(a.ok)
        self.assertTrue(a.detail.startswith("capacity:"), a.detail)

    def test_fable_chain_sends_nothing_and_says_roles(self):
        self.scene("on", roles_with(finisher=["sonnet", "fable"]))
        mc = FakeMC()
        a = finish_run.finisher_attempt("opus55", "the brief", mc, env=dict(os.environ))
        self.assertFalse(a.ok)
        self.assertTrue(a.detail.startswith("roles:"), a.detail)
        self.assertIn("fable", a.detail.lower())
        self.assertEqual((mc.walks, mc.singles), ([], []))

    # THE REPAIR ADVISOR AND THE PLANNER
    def test_advisor_model_read_at_call_time(self):
        self.scene(None)
        seen = self.callers(ADVICE)
        os.environ["BROTHER_REPAIR_ADVISOR_MODEL"] = "opus55"
        first = repair_advisor.advise("FAIL x", "build", "spec")
        os.environ["BROTHER_REPAIR_ADVISOR_MODEL"] = "haiku"
        second = repair_advisor.advise("FAIL x", "build", "spec")
        self.assertEqual(seen["one"], ["opus55", "haiku"])
        self.assertTrue((first or "").startswith("REPAIR ADVISOR (opus55,"), first)
        self.assertTrue((second or "").startswith("REPAIR ADVISOR (haiku,"), second)

    def test_planner_model_read_at_call_time(self):
        self.scene(None)
        seen = self.callers(PLAN)
        os.environ["BROTHER_BUILD_PLAN_MODEL"] = "opus55"
        first = build_plan.plan("spec", "files")
        os.environ["BROTHER_BUILD_PLAN_MODEL"] = "haiku"
        second = build_plan.plan("spec", "files")
        self.assertEqual(seen["one"], ["opus55", "haiku"])
        self.assertTrue((first or "").startswith("BUILD PLAN (opus55,"), first)
        self.assertTrue((second or "").startswith("BUILD PLAN (haiku,"), second)

    def test_advisor_walks_its_role_chain_under_on(self):
        self.scene("on")
        seen = self.callers(ADVICE)
        text = repair_advisor.advise("FAIL x", "build", "spec")
        self.assertEqual(seen["chain"], [["sonnet", "haiku"]])
        self.assertEqual(seen["one"], [])
        self.assertTrue((text or "").startswith("REPAIR ADVISOR (haiku,"), text)

    def test_planner_walks_its_role_chain_under_on(self):
        self.scene("on")
        seen = self.callers(PLAN)
        text = build_plan.plan("spec", "files")
        self.assertEqual(seen["chain"], [["sonnet", "haiku"]])
        self.assertEqual(seen["one"], [])
        self.assertTrue((text or "").startswith("BUILD PLAN (haiku,"), text)

    def test_refused_planner_and_advisor_chains_make_no_call(self):
        self.scene("on", roles_with(planner=["fable"], repair_advisor=["fable"]))
        seen = self.callers(PLAN)
        self.assertIsNone(build_plan.plan("spec", "files"))
        self.assertIsNone(repair_advisor.advise("FAIL x", "build", "spec"))
        self.assertEqual(seen, {"one": [], "chain": []})

    def test_model_default_and_strip(self):
        for mod, key in ((repair_advisor, "BROTHER_REPAIR_ADVISOR_MODEL"), (build_plan, "BROTHER_BUILD_PLAN_MODEL")):
            self.assertEqual(mod.model({}), "sonnet")
            self.assertEqual(mod.model({key: "  "}), "sonnet")
            self.assertEqual(mod.model({key: " haiku "}), "haiku")

    # HOSTILE INPUT
    def test_hostile_input_refused(self):
        mc = FakeMC()
        rt, cc, fa = check_wave.retry_target, check_wave.checker_chain, finish_run.finisher_attempt
        bad = [lambda: rt(0, 0, 0), lambda: rt("ab", "a", "LIMIT"), lambda: rt([["x"]], "a", "LIMIT"),
               lambda: rt([float("nan")], "a", "LIMIT"), lambda: rt([], "a", "LIMIT"), lambda: rt(["a"], None, "LIMIT"),
               lambda: rt(["a"], "b", "LIMIT"), lambda: rt(["a"], "a", None), lambda: rt(["a"], "a", True),
               lambda: rt(None, "a", "LIMIT"), lambda: rt(["a", None], "a", "LIMIT"),
               lambda: cc(None), lambda: cc(0), lambda: cc(True), lambda: cc(""), lambda: cc(b"opus55"),
               lambda: cc("opus55", env="BROTHER_BREAKER=on"), lambda: cc("opus55", env=["x"]),
               lambda: fa(None, "p", mc), lambda: fa(0, "p", mc), lambda: fa(True, "p", mc), lambda: fa("", "p", mc),
               lambda: fa("m", None, mc), lambda: fa("m", b"x", mc), lambda: fa("m", "p", None),
               lambda: fa("m", "p", "model_call"), lambda: fa("m", "p", mc, env="x"), lambda: fa("m", "p", mc, env=[]),
               lambda: repair_advisor.model(env="x"), lambda: repair_advisor.model(env=[]),
               lambda: repair_advisor.model(env={"BROTHER_REPAIR_ADVISOR_MODEL": 5}),
               lambda: repair_advisor.model(env={"BROTHER_REPAIR_ADVISOR_MODEL": True}),
               lambda: build_plan.model(env="x"), lambda: build_plan.model(env=[]),
               lambda: build_plan.model(env={"BROTHER_BUILD_PLAN_MODEL": float("nan")})]
        for i, case in enumerate(bad):
            with self.assertRaises(ValueError, msg="hostile case %d" % i):
                case()
        self.assertEqual((mc.walks, mc.singles), ([], []))
        self.scene(None)
        seen = self.callers(ADVICE)
        self.assertIsNone(repair_advisor.advise("FAIL x", "b", "s", env="BROTHER_BREAKER=on"))
        self.assertIsNone(build_plan.plan("s", "f", env=["x"]))
        self.assertEqual(seen, {"one": [], "chain": []})

    @unittest.skipUnless(os.path.isfile(ROLES_FILE), "the roles file is not in this tree (export copy)")
    def test_real_roles_file_agrees_with_the_fixture(self):
        with open(ROLES_FILE, "rb") as f:
            roles = json.loads(f.read().decode("utf-8"))["roles"]
        self.assertEqual(roles["finisher"].get("chain"), BASE_ROLES["finisher"]["chain"])
        self.assertEqual(roles["checker"].get("chain"), [])
        for role in ("planner", "repair_advisor"):
            got = (roles[role]["default"], roles[role].get("chain"), roles[role]["setting"])
            self.assertEqual(got, (BASE_ROLES[role]["default"], BASE_ROLES[role]["chain"], BASE_ROLES[role]["setting"]))
        for role, spec in roles.items():
            self.assertFalse(any("fable" in str(m).lower() for m in spec.get("chain") or []), role)


if __name__ == "__main__":
    unittest.main()
