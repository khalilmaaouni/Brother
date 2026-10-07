#!/usr/bin/env python3
"""FX-11.4: per role chains in the roles file, validated at intake and at selection.

loop_roles.role_chain walks a role's own named chain under BROTHER_BREAKER=on and returns ONE member under off;
loop_roles.stage_ok refuses a retired, shadow or unknown stage; loop_roles.check names every chain member;
model_router.chain applies the same stage rule to every candidate, drops a model whose breaker key is open from the
AUTOMATIC rest and never silently drops a pin; the tracked roles fixture stays a byte copy of the roles file.

Hostile arguments (wrong type, None, a bool, bytes) are refused with ValueError, never a crash and never silence.
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import loop_roles
import model_router

ROLES_FILE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "plan", "loop-roles.json")
FIXTURE = os.path.join(os.path.dirname(HERE), "fixtures", "loop-roles-fixture.json")
DOC_PATH = ROLES_FILE if os.path.isfile(ROLES_FILE) else FIXTURE


def _row(**over):
    row = {"id": "x/m", "transport": "claude", "privacy": "private",
           "quality": {"build": 5, "grade": 5}, "cost": 1.0}
    row.update(over)
    row["kinds"] = set(row["quality"])
    return row


REG = {
    "sonnet": _row(id="claude-sonnet-5", cost=8.0, quality={"build": 8, "grade": 8, "prose": 8, "plan": 7}),
    "haiku": _row(id="claude-haiku-4-5-20251001", cost=2.0, quality={"build": 6, "grade": 7, "prose": 6}),
    "opus55": _row(id="claude-opus-5-5", cost=35.0, quality={"build": 10, "grade": 9, "prose": 9, "plan": 10}),
    "fable": _row(id="claude-fable-5-1", cost=40.0, quality={"build": 9, "grade": 9, "prose": 9, "plan": 10}),
    "deepseek": _row(id="deepseek/deepseek-v4.1-flash", transport="bridge", privacy="public",
                     quality={"build": 7, "grade": 6, "prose": 6}, cost=1.0),
    "retired_one": _row(id="x/retired_one", stage="retired"),
    "shadow_one": _row(id="x/shadow_one", stage="shadow"),
    "odd_stage": _row(id="x/odd_stage", stage="banana"),
}


def _roles(**over):
    base = {
        "worker": {"does": "x", "when": "inside", "kind": "build", "content": "public",
                   "must_be_chosen": False, "default": "deepseek"},
        "finisher": {"does": "x", "when": "after_run", "kind": "build", "content": "private",
                     "setting": "BROTHER_FINISHER_MODEL", "chain": ["sonnet"],
                     "must_be_chosen": True, "default": None},
        "planner": {"does": "x", "when": "inside", "kind": "grade", "content": "private",
                    "setting": "BROTHER_BUILD_PLAN_MODEL", "default": "sonnet", "chain": ["haiku"],
                    "must_be_chosen": False},
    }
    base.update(over)
    return base


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class _FakeBreaker(object):
    """Stands in for FX-11.1's breaker module while this file tests selection only."""

    class BreakerNoData(RuntimeError):
        pass

    def __init__(self, keys=(), mode="on"):
        self.keys = list(keys)
        self.value = mode

    def mode(self, env=None):
        return self.value

    def open_keys(self):
        return list(self.keys)


class _RaisingBreaker(_FakeBreaker):
    def open_keys(self):
        raise _FakeBreaker.BreakerNoData("the breaker lock is held")


def _fake_registry(reg=None):
    return REG


def _no_transports():
    saved = os.environ.pop("BROTHER_TRANSPORTS", None)

    def restore():
        if saved is not None:
            os.environ["BROTHER_TRANSPORTS"] = saved
    return restore


class RoleChainSelection(unittest.TestCase):

    def test_no_chain_field_is_one_member(self):
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="on")}):
            got = loop_roles.role_chain("worker", roles=_roles(), reg=REG, env={})
        self.assertEqual(got, ["deepseek"])

    def test_one_member_on_equals_off(self):
        roles = _roles(checker={"does": "x", "when": "inside", "kind": "grade", "content": "public",
                                "must_be_chosen": False, "default": "sonnet", "chain": []})
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="on")}):
            on = loop_roles.role_chain("checker", roles=roles, reg=REG, env={})
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="off")}):
            off = loop_roles.role_chain("checker", roles=roles, reg=REG, env={})
        self.assertEqual(on, ["sonnet"])
        self.assertEqual(on, off)

    def test_off_is_one_member(self):
        env = {"BROTHER_FINISHER_MODEL": "opus55"}
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="off")}):
            got = loop_roles.role_chain("finisher", roles=_roles(), reg=REG, env=env)
        self.assertEqual(got, ["opus55"])

    def test_on_walks_the_named_chain(self):
        env = {"BROTHER_FINISHER_MODEL": "opus55"}
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="on")}):
            got = loop_roles.role_chain("finisher", roles=_roles(), reg=REG, env=env)
        self.assertEqual(got, ["opus55", "sonnet"])

    def test_chain_member_equal_to_first_is_not_repeated(self):
        roles = _roles(finisher={"does": "x", "when": "after_run", "kind": "build", "content": "private",
                                 "setting": "BROTHER_FINISHER_MODEL", "chain": ["sonnet", "sonnet"],
                                 "must_be_chosen": True, "default": None})
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="on")}):
            got = loop_roles.role_chain("finisher", roles=roles, reg=REG, env={"BROTHER_FINISHER_MODEL": "sonnet"})
        self.assertEqual(got, ["sonnet"])

    def test_absent_stage_reads_seated(self):
        self.assertEqual(loop_roles.stage_ok("plain", {"id": "x", "transport": "claude"}), (True, ""))

    def test_seated_row_passes(self):
        self.assertEqual(loop_roles.stage_ok("sonnet", REG["sonnet"]), (True, ""))

    def test_retired_member_refused(self):
        roles = _roles(finisher={"does": "x", "when": "after_run", "kind": "build", "content": "private",
                                 "setting": "BROTHER_FINISHER_MODEL", "chain": ["retired_one"],
                                 "must_be_chosen": True, "default": None})
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="on")}):
            with self.assertRaises(ValueError) as ctx:
                loop_roles.role_chain("finisher", roles=roles, reg=REG, env={"BROTHER_FINISHER_MODEL": "opus55"})
        self.assertIn("retired_one", str(ctx.exception))

    def test_shadow_member_refused(self):
        ok, why = loop_roles.stage_ok("shadow_one", REG["shadow_one"])
        self.assertFalse(ok)
        self.assertIn("shadow", why)

    def test_unknown_stage_refused(self):
        ok, why = loop_roles.stage_ok("odd_stage", REG["odd_stage"])
        self.assertFalse(ok)
        self.assertIn("unknown stage", why)

    def test_fable_chain_member_refused(self):
        roles = _roles(finisher={"does": "x", "when": "after_run", "kind": "build", "content": "private",
                                 "setting": "BROTHER_FINISHER_MODEL", "chain": ["fable"],
                                 "must_be_chosen": True, "default": None})
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="on")}):
            with self.assertRaises(ValueError) as ctx:
                loop_roles.role_chain("finisher", roles=roles, reg=REG, env={"BROTHER_FINISHER_MODEL": "opus55"})
        self.assertIn("fable", str(ctx.exception).lower())

    def test_hostile_arguments_refused(self):
        for bad in (0, None, True, b"x", [], 1.5, {}):
            with self.assertRaises(ValueError):
                loop_roles.role_chain(bad, roles=_roles(), reg=REG, env={})
            with self.assertRaises(ValueError):
                loop_roles.stage_ok(bad, REG["sonnet"])
        for badrow in (None, "not a row", [1, 2], 7, b"x"):
            with self.assertRaises(ValueError):
                loop_roles.stage_ok("sonnet", badrow)
        with self.assertRaises(ValueError):
            loop_roles.role_chain("finisher", choice=0, roles=_roles(), reg=REG, env={})
        with self.assertRaises(ValueError):
            loop_roles.role_chain("finisher", roles=_roles(), reg=REG, env=0)
        with self.assertRaises(ValueError):
            loop_roles.role_chain("finisher", roles=0, reg=REG, env={})
        with self.assertRaises(ValueError):
            loop_roles.role_chain("nosuchrole", roles=_roles(), reg=REG, env={})

    def test_wrong_kind_refused(self):
        roles = _roles(planner={"does": "x", "when": "inside", "kind": "plan", "content": "private",
                                "setting": "BROTHER_BUILD_PLAN_MODEL", "default": "haiku",
                                "chain": [], "must_be_chosen": False})
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(mode="off")}):
            with self.assertRaises(ValueError) as ctx:
                loop_roles.role_chain("planner", roles=roles, reg=REG, env={})
        self.assertIn("haiku", str(ctx.exception))


class CheckLines(unittest.TestCase):

    def setUp(self):
        self.addCleanup(_no_transports())

    def test_check_names_every_chain_member(self):
        code, lines = loop_roles.check({}, _roles(), REG)
        text = "\n".join(lines)
        self.assertIn("sonnet", text)
        self.assertIn("haiku", text)
        self.assertIn("chain", text)

    def test_check_refuses_a_fable_chain_member(self):
        roles = _roles(finisher={"does": "x", "when": "after_run", "kind": "build", "content": "private",
                                 "setting": "BROTHER_FINISHER_MODEL", "chain": ["fable"],
                                 "must_be_chosen": True, "default": None})
        code, lines = loop_roles.check({"finisher": "opus55"}, roles, REG)
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("REFUSED") and "Fable" in l for l in lines))

    def test_check_refuses_a_retired_chain_member(self):
        roles = _roles(finisher={"does": "x", "when": "after_run", "kind": "build", "content": "private",
                                 "setting": "BROTHER_FINISHER_MODEL", "chain": ["retired_one"],
                                 "must_be_chosen": True, "default": None})
        code, lines = loop_roles.check({"finisher": "opus55"}, roles, REG)
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("REFUSED") and "retired" in l for l in lines))


class RouterChain(unittest.TestCase):

    def setUp(self):
        self.addCleanup(_no_transports())
        patcher = mock.patch.object(model_router, "registry", new=_fake_registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _chain(self, **kw):
        kw.setdefault("registry_arg", REG)
        kw.setdefault("rel", {})
        return model_router.chain(**kw)

    def test_open_member_dropped_from_rest(self):
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(keys=["claude:default:sonnet"])}):
            got = self._chain(kind="build", sensitivity="private")
        self.assertNotIn("sonnet", got)
        self.assertIn("opus55", got)

    def test_open_pin_kept_first(self):
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(keys=["claude:default:sonnet"])}):
            got = self._chain(kind="build", sensitivity="private", pin="sonnet")
        self.assertEqual(got[0], "sonnet")

    def test_retired_row_never_chosen(self):
        got = self._chain(kind="build", sensitivity="private")
        self.assertNotIn("retired_one", got)
        self.assertNotIn("shadow_one", got)
        self.assertNotIn("odd_stage", got)

    def test_shadow_key_does_not_close_the_seat(self):
        with mock.patch.dict(sys.modules, {"breaker": _FakeBreaker(keys=["claude:default:sonnet:shadow"])}):
            got = self._chain(kind="build", sensitivity="private")
        self.assertIn("sonnet", got)

    def test_breaker_nodata_refuses(self):
        with mock.patch.dict(sys.modules, {"breaker": _RaisingBreaker()}):
            with self.assertRaises(model_router.Refused):
                self._chain(kind="build", sensitivity="private")


class RolesFileContent(unittest.TestCase):

    @unittest.skipUnless(os.path.isfile(DOC_PATH), "no roles document is shipped on this tree")
    def test_finisher_chain_is_sonnet(self):
        self.assertEqual(_load(DOC_PATH)["roles"]["finisher"]["chain"], ["sonnet"])

    @unittest.skipUnless(os.path.isfile(DOC_PATH), "no roles document is shipped on this tree")
    def test_checker_chain_is_empty(self):
        self.assertEqual(_load(DOC_PATH)["roles"]["checker"]["chain"], [])

    @unittest.skipUnless(os.path.isfile(DOC_PATH), "no roles document is shipped on this tree")
    def test_planner_and_repair_advisor_roles_present(self):
        roles = _load(DOC_PATH)["roles"]
        self.assertIn("planner", roles)
        self.assertIn("repair_advisor", roles)
        self.assertEqual(roles["planner"]["setting"], "BROTHER_BUILD_PLAN_MODEL")
        self.assertEqual(roles["planner"]["chain"], ["haiku"])
        self.assertEqual(roles["repair_advisor"]["setting"], "BROTHER_REPAIR_ADVISOR_MODEL")
        self.assertEqual(roles["repair_advisor"]["chain"], ["haiku"])
        self.assertEqual(roles["adversary"]["setting"], "BROTHER_ADVERSARY_MODEL")

    @unittest.skipUnless(os.path.isfile(DOC_PATH), "no roles document is shipped on this tree")
    def test_no_written_chain_names_fable(self):
        for role, spec in _load(DOC_PATH)["roles"].items():
            for member in spec.get("chain") or []:
                self.assertNotIn("fable", member.lower())

    @unittest.skipUnless(os.path.isfile(ROLES_FILE) and os.path.isfile(FIXTURE),
                         "the export tree ships only the fixture")
    def test_fixture_byte_identical(self):
        with open(ROLES_FILE, "rb") as fh:
            a = fh.read()
        with open(FIXTURE, "rb") as fh:
            b = fh.read()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
