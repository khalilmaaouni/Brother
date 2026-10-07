"""F3 (docs/plan/BROTHER-LOOP-HARDENING-WBS.json, unit id F3): "Only two
models (bridge transport) can hold the attacker role". Owner ruling
2026-09-26 (~/.claude/evidence/loop-remediation-0926/OWNER-DECISIONS-
2026-09-26.md): "accepted as a recorded 1.1.0 limit, not rebuilt now."

THIS FILE NEVER CHANGES THE LIMIT. It only proves the record (the WBS
issue text, itself sourced from ~/.claude/evidence/loop-audit-0926/
MECHANISM-AUDIT.md item 3) and the code (scripts/loop/probe_wave.py, the
one place the loop's adversary/attacker role is actually dispatched) still
name the same set of models. If a future edit widens or narrows either
side without the other, this goes red and names the mismatch; it does not
repair anything.

WHERE THE LIMIT LIVES, TWO INDEPENDENT ROUTES, cross-checked against each
other so a change to only one still trips this test:

  1. scripts/loop/probe_wave.py hardcodes exactly two adversary labels
     (ADVERSARIES) and maps them through ADVERSARY_MODEL to exactly two
     possible model names across both branches of second_adversary():
     "deepseek" (always, the first seat) and either "deepseek" or "muse"
     (the second seat, chosen from the worker mix). The set of models that
     can ever actually be dispatched to run a probe is {"deepseek", "muse"}.

  2. docs/plan/loop-roles.json's adversary role (kind: "build") plus the
     model registry (scripts/loop/model_router.py, docs/plan/model-
     registry.json) independently narrow to the same set: a bridge-
     transport model that cannot do "build" work (jev, kind "decide" only)
     is refused before its transport is even checked, so only deepseek and
     muse clear both gates.

Route 1 is read WITHOUT importing scripts/loop/probe_wave.py: that module
calls loop_hold.gate() at import time (see its own docstring, "every route
that buys model calls now asks here first"), which sys.exit()s the whole
process while ~/.claude/evidence/LOOP-HOLD.txt or LOOP-PAUSE.txt exists.
This estate's rule is to never touch either file, and a test that could be
aborted by unrelated machine state is not a test of this file's own logic.
Instead this reads probe_wave.py's SOURCE with the standard library `ast`
module and executes only the one pure function (second_adversary) it needs,
in an isolated namespace, never the module as a whole.

SINCE 2026-09-30 (owner: the loop must run with Claude Code models only) the
two-model set is the DEFAULT, not the ceiling: BROTHER_ADVERSARY_MODEL names one
model for both seats (probe_wave.adversary_models, probe_round.ADVERSARIES), the
adversary role carries that setting at intake, and loop_roles.BRIDGE_ONLY is
empty. The cases below prove both the unchanged default and the override.

No network. No model calls. Standard library only.
"""
import ast
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOP_DIR = os.path.join(HERE, "loop")
PROBE_WAVE_PATH = os.path.join(LOOP_DIR, "probe_wave.py")
LOOP_ROLES_JSON = os.path.join(ROOT, "docs", "plan", "loop-roles.json")
MODEL_REGISTRY_JSON = os.path.join(ROOT, "docs", "plan", "model-registry.json")
WBS_JSON = os.path.join(ROOT, "docs", "plan", "BROTHER-LOOP-HARDENING-WBS.json")

sys.path.insert(0, LOOP_DIR)
import loop_roles  # noqa: E402  (safe: no top-level side effects, no hold gate)
import model_router  # noqa: E402  (safe: same, confirmed no loop_hold import)


def find_unit_by_id(obj, target_id):
    """The first dict anywhere in `obj` carrying id == target_id and an
    "issue" field, depth first. None when nothing matches: a caller must
    not mistake "not found" for an empty limit."""
    if isinstance(obj, dict):
        if obj.get("id") == target_id and "issue" in obj:
            return obj
        for value in obj.values():
            found = find_unit_by_id(value, target_id)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_unit_by_id(value, target_id)
            if found is not None:
                return found
    return None


def extract_literal_assignment(tree, name):
    """ast.literal_eval of the first top-level `name = <literal>` assignment
    found by walking `tree`, or None when no such assignment exists or its
    value is not a literal (e.g. it calls a function, as ADVERSARY_MODEL's
    second entry does)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        return None
    return None


def extract_function(tree, name):
    """The ast.FunctionDef named `name` anywhere in `tree`, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def second_adversary_outputs(probe_wave_path):
    """The set of model names second_adversary() can return, read from
    scripts/loop/probe_wave.py's SOURCE and executed in isolation (only
    this one function, never the module: see this file's own docstring for
    why the module as a whole is never imported here). Both explicit
    mixes below are non-None, so the function's own
    `os.environ.get(...) if mix is None else mix` line never reads the
    real environment; `os` is supplied only so the function object is
    well formed."""
    with open(probe_wave_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=probe_wave_path)
    func_node = extract_function(tree, "second_adversary")
    if func_node is None:
        raise AssertionError(
            "second_adversary() not found in %s; the code this test reads "
            "has moved or been renamed" % probe_wave_path)
    module = ast.Module(body=[func_node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"os": os}
    exec(compile(module, probe_wave_path, "exec"), namespace)  # noqa: S102
    second_adversary = namespace["second_adversary"]
    return {
        second_adversary(mix=""),                 # empty mix: no worker named
        second_adversary(mix="onlydeepseek"),      # a mix naming no muse
        second_adversary(mix="worker=muse"),       # a mix naming muse
    }


def probe_wave_adversary_models(probe_wave_path=PROBE_WAVE_PATH):
    """The set of models scripts/loop/probe_wave.py can actually dispatch to
    the adversary/attacker role: ADVERSARIES' first (fixed) seat plus every
    output second_adversary() can produce for the second seat. Raises
    AssertionError, never returns a guess, if ADVERSARIES is not the
    two-entry literal tuple the code is recorded as using."""
    with open(probe_wave_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=probe_wave_path)
    adversaries = extract_literal_assignment(tree, "ADVERSARIES")
    if not isinstance(adversaries, tuple) or len(adversaries) != 2:
        raise AssertionError(
            "ADVERSARIES in %s is not the recorded two-entry literal tuple: "
            "%r" % (probe_wave_path, adversaries))
    first_seat = adversaries[0]  # ADVERSARY_MODEL[first_seat] == first_seat itself, a literal
    return {first_seat} | second_adversary_outputs(probe_wave_path)


def bridge_build_models(models):
    """Every model name in the registry `models` dict that could pass
    loop_roles.py's BRIDGE_ONLY check for a "build"-kind, "inside" role:
    transport == "bridge" and "build" in its kinds. This is the SECOND,
    independent route to the same limit (see this file's own docstring)."""
    return {name for name, spec in models.items()
            if spec.get("transport") == "bridge"
            and "build" in spec.get("kinds", ())}


def compare_attacker_model_sets(wbs_issue_text, probe_wave_models, registry_models):
    """(ok, detail). ok is True only when: the WBS record's own issue text
    still says "two" and "attacker role", AND probe_wave_models (route 1)
    equals registry_models (route 2), AND that agreed set has exactly two
    members. A pure function of its three inputs so a test can feed it a
    deliberately wrong input and prove it goes red without ever touching
    the real files (see TheCheckCanFail below)."""
    text = (wbs_issue_text or "").lower()
    if "two" not in text or "attacker role" not in text:
        return False, (
            "the WBS record no longer states a two-model attacker-role "
            "limit in plain words: %r" % wbs_issue_text)
    if probe_wave_models != registry_models:
        return False, (
            "the two independent routes disagree: probe_wave.py allows %s, "
            "the registry cross-check allows %s"
            % (sorted(probe_wave_models), sorted(registry_models)))
    if len(probe_wave_models) != 2:
        return False, (
            "the record says two models but the code allows %d: %s"
            % (len(probe_wave_models), sorted(probe_wave_models)))
    return True, "agreed: %s" % sorted(probe_wave_models)


# THE PUBLIC EXPORT OMITS docs/plan (gate finding 2026-09-26: these three classes failed there with FileNotFoundError).
# A case whose input is not shipped cannot say anything about this tree, so it is skipped by name there and
# still runs in the repository, where every one of these files exists.
# The repository carries .brother-edition (tracked); the export omits it. That marker, not the files themselves,
# decides whether a missing plan file is an omission (skip) or a defect (fail).
IN_REPOSITORY = os.path.isfile(os.path.join(ROOT, ".brother-edition"))


def _needs(*paths):
    missing = [os.path.relpath(p, ROOT) for p in paths if not os.path.isfile(p)]
    return unittest.skipIf(bool(missing) and not IN_REPOSITORY,
                           "not shipped in this tree: %s; the case runs in the repository" % ", ".join(missing))


@_needs(WBS_JSON)
class TheWBSRecordNamesTheLimitInPlainWords(unittest.TestCase):
    def test_the_F3_unit_exists_and_names_two_models(self):
        with open(WBS_JSON, encoding="utf-8") as fh:
            wbs = json.load(fh)
        unit = find_unit_by_id(wbs, "F3")
        self.assertIsNotNone(
            unit, "no unit with id F3 found in %s" % WBS_JSON)
        self.assertIn("two models", unit["issue"].lower())
        self.assertIn("attacker role", unit["issue"].lower())


class TheCodeReadFromProbeWaveSource(unittest.TestCase):
    """Route 1: scripts/loop/probe_wave.py's own ADVERSARIES and
    second_adversary(), read statically so the module's own loop_hold gate
    never runs (see this file's docstring)."""

    def test_adversaries_is_exactly_two_labels(self):
        with open(PROBE_WAVE_PATH, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=PROBE_WAVE_PATH)
        adversaries = extract_literal_assignment(tree, "ADVERSARIES")
        self.assertEqual(len(adversaries), 2, adversaries)

    def test_probe_wave_allows_exactly_deepseek_and_muse(self):
        self.assertEqual(probe_wave_adversary_models(), {"deepseek", "muse"})

    def test_a_named_adversary_fills_both_seats_and_the_default_keeps_todays_pair(self):
        """M13 (attack 2026-09-30): adversary_models reads BROTHER_ADVERSARY_MODEL; a named model fills both seats,
        blank or deepseek (the roles file default) keeps DeepSeek first and the mix's second seat."""
        with open(PROBE_WAVE_PATH, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=PROBE_WAVE_PATH)
        module = ast.Module(body=[extract_function(tree, "second_adversary"), extract_function(tree, "adversary_models")], type_ignores=[])
        ast.fix_missing_locations(module)
        ns = {"os": os}
        exec(compile(module, PROBE_WAVE_PATH, "exec"), ns)  # noqa: S102
        models = ns["adversary_models"]
        with mock.patch.dict(os.environ, {"BROTHER_WORKER_MIX": ""}):
            self.assertEqual(models({}), {"deepseek": "deepseek", "deepseek-b": "muse"})
            self.assertEqual(models({"BROTHER_ADVERSARY_MODEL": "deepseek"}), models({}))
            self.assertEqual(models({"BROTHER_ADVERSARY_MODEL": " "}), models({}))
        self.assertEqual(models({"BROTHER_ADVERSARY_MODEL": " sonnet "}), {"deepseek": "sonnet", "deepseek-b": "sonnet"})

    def test_the_pass_level_re_probe_default_literal_is_deepseek_then_muse(self):
        """MD (attack 3): the literal ADVERSARIES in scripts/probe_round.py keeps DeepSeek first and Muse second."""
        with open(os.path.join(HERE, "probe_round.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        self.assertEqual(extract_literal_assignment(tree, "ADVERSARIES"), (("a", "deepseek"), ("b", "muse")))

    def test_the_pass_level_re_probe_reads_the_same_setting(self):
        """M14: scripts/probe_round.py's ADVERSARIES follow BROTHER_ADVERSARY_MODEL; read in a child so the real
        environment of this process never leaks in either direction."""
        code = "import sys; sys.path.insert(0, %r); import probe_round; print(probe_round.ADVERSARIES)" % HERE
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_ADVERSARY_MODEL"}
        base = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(base.stdout.strip(), "(('a', 'deepseek'), ('b', 'muse'))", base.stdout + base.stderr)
        named = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=dict(env, BROTHER_ADVERSARY_MODEL="sonnet"),
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(named.stdout.strip(), "(('a', 'sonnet'), ('b', 'sonnet'))", named.stdout + named.stderr)


@_needs(LOOP_ROLES_JSON, MODEL_REGISTRY_JSON)
class TheRegistryCrossCheckAgrees(unittest.TestCase):
    """Route 2: the model registry plus loop_roles.py's BRIDGE_ONLY and
    docs/plan/loop-roles.json's adversary role kind, read independently of
    probe_wave.py."""

    def test_adversary_is_build_kind_inside_and_settable_by_name(self):
        """2026-09-30 (owner: Claude only runs): the adversary is no longer bridge only at intake; the DEFAULT seats
        stay the two bridge models, and BROTHER_ADVERSARY_MODEL names one model for both seats when chosen."""
        self.assertEqual(loop_roles.BRIDGE_ONLY, ())
        roles = loop_roles.load_roles(LOOP_ROLES_JSON)
        self.assertIsNotNone(roles, "could not read %s" % LOOP_ROLES_JSON)
        self.assertEqual(roles["adversary"]["kind"], "build")
        self.assertEqual(roles["adversary"]["when"], "inside")
        self.assertEqual(roles["adversary"]["setting"], "BROTHER_ADVERSARY_MODEL")
        self.assertEqual(roles["adversary"]["default"], "deepseek")

    def test_registry_allows_exactly_deepseek_and_muse_for_build_on_bridge(self):
        models = model_router.load_registry(path=MODEL_REGISTRY_JSON)
        self.assertEqual(bridge_build_models(models), {"deepseek", "muse"})


@_needs(WBS_JSON, MODEL_REGISTRY_JSON)
class TheRecordAndTheCodeAgree(unittest.TestCase):
    """The tying-together assertion this file exists to make: read the real
    WBS text and both real code routes, and confirm all three agree."""

    def test_real_repo_state(self):
        with open(WBS_JSON, encoding="utf-8") as fh:
            wbs = json.load(fh)
        unit = find_unit_by_id(wbs, "F3")
        models = model_router.load_registry(path=MODEL_REGISTRY_JSON)
        ok, detail = compare_attacker_model_sets(
            unit["issue"], probe_wave_adversary_models(),
            bridge_build_models(models))
        self.assertTrue(ok, detail)


class TheCheckCanFail(unittest.TestCase):
    """A check that cannot fail is not a check. Each case below feeds
    compare_attacker_model_sets a single deliberately wrong input and
    proves it is caught, without ever touching the real files: the
    permanent, in-suite form of a mutation-and-restore proof."""

    REAL_TEXT = "Only two models (bridge transport) can hold the attacker role"
    REAL_SET = frozenset({"deepseek", "muse"})

    def test_a_widened_code_set_is_caught(self):
        ok, detail = compare_attacker_model_sets(
            self.REAL_TEXT, {"deepseek", "muse", "jev"}, {"deepseek", "muse", "jev"})
        self.assertFalse(ok)
        self.assertIn("3", detail)

    def test_the_two_routes_disagreeing_is_caught(self):
        ok, detail = compare_attacker_model_sets(
            self.REAL_TEXT, {"deepseek", "muse"}, {"deepseek"})
        self.assertFalse(ok)
        self.assertIn("disagree", detail)

    def test_a_stale_record_no_longer_saying_two_is_caught(self):
        ok, detail = compare_attacker_model_sets(
            "Only three models can hold the attacker role",
            self.REAL_SET, self.REAL_SET)
        self.assertFalse(ok)

    def test_a_record_that_dropped_the_role_name_is_caught(self):
        ok, detail = compare_attacker_model_sets(
            "Only two models may run probes", self.REAL_SET, self.REAL_SET)
        self.assertFalse(ok)

    def test_the_real_inputs_still_pass(self):
        ok, _detail = compare_attacker_model_sets(
            self.REAL_TEXT, self.REAL_SET, self.REAL_SET)
        self.assertTrue(ok)


class TheRealStateCasesRunInTheRepository(unittest.TestCase):
    """A skip reads as a pass, so the not-shipped skip must never fire where the files exist: in a repository
    checkout all three real-state classes run."""

    @unittest.skipUnless(IN_REPOSITORY, "not the repository: .brother-edition is not shipped in the export")
    def test_no_real_state_case_is_skipped_in_a_checkout(self):
        for cls in (TheWBSRecordNamesTheLimitInPlainWords, TheRegistryCrossCheckAgrees, TheRecordAndTheCodeAgree):
            self.assertFalse(getattr(cls, "__unittest_skip__", False), cls.__name__)


if __name__ == "__main__":
    unittest.main()
