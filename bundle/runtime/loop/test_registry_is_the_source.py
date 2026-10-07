#!/usr/bin/env python3
"""Tests for FX-31.7: the model registry is the one source of every model table (R-FX-31-5, acceptance c14).

Every case reaches a public boundary (model_router.derive_model_views and chain, registry_measure.check, and each
consumer module's own reader: or_ask.bridge_aliases, worker_mix.ARM_OF and DEFAULT_MIX, ev_gate.DEFAULT_ARM_COST and
round_cost_from_env, loop_intake.MODEL_ALIASES and read_model, or_dispatch_cli.REAL_MODEL_IDS). The expected side of
every equality is read from docs/plan/model-registry.json with json alone, never through the router, so the router's
derivation and this file's reading are two independent readers of one file. One condition per case:
  the live rows, row by row, are what every view carries, and nothing a view carries is outside the rows;
  the six tables typed by hand before FX-31.6 removed them are reproduced from the rows, entry for entry;
  one row projects to exactly one entry in each view, and an empty registry refuses rather than projecting nothing;
  a corrupt registry on disk is a stated refusal or NO-DATA at every consumer, never a traceback;
  a stale read_on warns with the owner's class 2 question and blocks neither the check's exit code nor routing.
A live case whose registry the router refuses FAILS by name (NO-DATA), never with a traceback: unreadable is the
blocking direction, and a refusal is not a pass.
"""
import contextlib
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import model_router as R  # noqa: E402
import registry_measure  # noqa: E402
import or_ask  # noqa: E402
import worker_mix  # noqa: E402
import ev_gate  # noqa: E402
import loop_intake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
DISPATCH_CLI = os.path.join(ROOT, "plugin", "runtime", "brother", "core", "or_dispatch_cli.py")

# THE ROWS THE REGISTRY CARRIED WHEN THIS CHECK WAS WRITTEN (2026-10-04): a floor, not a ceiling. A row the owner adds
# is the registry's own business and never turns this red; a row that disappears is a dropped model, which is the one
# defect a count alone would hide, so every one of these names is asserted present by name.
NAMES_AT_FX317 = ("deepseek", "muse", "jev", "fable", "opus", "sonnet", "haiku", "astra", "luna", "sol", "terra",
                  "gpt55", "opus55", "sonnet55")

# THE SIX TABLES TYPED BY HAND BEFORE FX-31.6 DELETED THEM, copied from the commit before 180802914 (or_ask.py
# MODEL_ALIASES, or_dispatch_cli.py REAL_MODEL_IDS, worker_mix.py DEFAULT_MIX and ARM_OF, ev_gate.py DEFAULT_ARM_COST,
# loop_intake.py MODEL_ALIASES). The migration proof the spec asks for: the derivation must reproduce every entry, in
# the order the hand typed it. A row added since is allowed beside them; an entry changed or lost is not.
PRE_REMOVAL = {
    "bridge_aliases": {"muse": "meta/muse-spark-1.3-contributor", "deepseek": "deepseek/deepseek-v4.1-flash",
                       "typesafe": "typesafe/jev-1.13", "jev": "typesafe/jev-1.13"},
    "dispatch_ids": {"muse": "meta/muse-spark-1.3-contributor", "deepseek": "deepseek/deepseek-v4.1-flash",
                     "jev": "typesafe/jev-1.13-20260917"},
    "default_mix": "deepseek:5,muse:2,sonnet:1",
    "arm_of": (("deepseek", "deepseek"), ("muse", "muse"), ("sonnet", "sonnet"), ("claude", "sonnet"),
               ("astra", "astra"), ("codex", "astra")),
    "arm_cost": "deepseek:0.02,muse:0.02,sonnet:0.10,astra:0.10",
    "spoken": {"deep seek": "deepseek", "deep-seek": "deepseek", "deepseek flash": "deepseek", "gpt-6 astra": "astra",
               "gpt6 astra": "astra", "claude opus": "opus", "claude sonnet": "sonnet", "claude haiku": "haiku",
               "claude fable": "fable"},
}

# ROWS RETIRED SINCE THE MIGRATION: a retired row leaves the SEATING views (dispatch_ids, default_mix) by design, so the
# migration proof drops it there and only there. Each name is asserted to read stage "retired" in the live registry,
# so this list can never hide a seated row that went missing. muse: OpenRouter HTTP 404 model_not_found, 2026-10-05.
RETIRED_SINCE = ("muse",)


def _without_retired(table):
    return {k: v for k, v in table.items() if k not in RETIRED_SINCE}


# TYPED MODEL IDS THAT MAY STAY OUTSIDE THE REGISTRY (review of acabf33b9): each is a deliberate pin or prefix, kept
# where it is because its job is to notice a vendor repoint rather than follow one. Every entry is asserted present in
# its file and resolving to a registry row (`id` or `answers_as`, exactly or as a prefix), so a registry change turns
# this red instead of leaving a stale pin behind. Paths are relative to scripts/.
ALLOWLIST = (
    ("jev_canary.py", "typesafe/jev-1.13-20260917", "exact"),        # PINNED_MODEL: every golden answer carries this exact id
    ("jev_eval.py", "meta/muse-spark-1.3-contributor", "exact"),      # EXPECT: the id each chat system must answer as
    ("jev_eval.py", "deepseek/deepseek-v4.1-flash", "exact"),
    ("jev_eval.py", "typesafe/jev", "prefix"),                        # EXPECT["jev"], compared with startswith (lines 153 and 190)
    ("jev_decide.py", "typesafe/jev", "prefix"),                      # MODEL_PREFIX, the same startswith rule
)
# Vendors a hand typed id could name beside the providers the registry already carries; the scanner keys on both.
VENDORS = ("openai", "anthropic", "google", "mistralai", "qwen", "x-ai", "meta-llama", "moonshotai", "minimax", "nvidia", "cohere")
SCRIPTS = os.path.join(ROOT, "scripts")
PLUGIN_CORE = os.path.dirname(DISPATCH_CLI)

_TMP = ""


def setUpModule():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="registry-source-test-")


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def _raw_document():
    """The registry as json alone reads it (the independent reader), or None with the reason."""
    for cand in R._registry_candidates():
        try:
            with open(cand, encoding="utf-8") as fh:
                return json.load(fh), cand
        except OSError:  # sbe: allow-silent the next candidate is tried; none readable is said below
            continue
        except ValueError as exc:
            return None, "%s is not valid JSON (%s)" % (cand, exc)
    return None, "no registry among %s" % ", ".join(R._registry_candidates())


def _write(tag, text):
    path = os.path.join(_TMP, tag + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text if isinstance(text, str) else json.dumps(text))
    return path


def _row(**over):
    r = {"id": "x/solo", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 5}, "cost": 1.0}
    r.update(over)
    return r


def _tokens(text):
    """"name:value,..." as [(name, float)], the way ev_gate and worker_mix read it."""
    return [(k, float(v)) for k, v in (t.split(":") for t in text.split(",") if t)]


class _Live(unittest.TestCase):
    """Cases over the registry the router resolves on this machine. A refusal is NO-DATA by name, never a traceback."""

    def setUp(self):
        raw, where = _raw_document()
        if raw is None:
            self.fail("NO-DATA: the model registry cannot be read: %s" % where)
        try:
            self.views = R.derive_model_views(R.load_registry_document(where)["models"])
        except R.Refused as exc:
            self.fail("NO-DATA: the model registry refuses, so no view can be compared: %s" % exc)
        self.rows = raw["models"]
        self.where = where


class RowsMatchViews(_Live):
    def test_all_thirteen_registry_rows_match_views(self):
        """Row by row, every view carries exactly what the rows declare (14 rows on 2026-10-04, thirteen when the spec
        was written; the count is read from the file, the names below are the floor)."""
        rows, views = self.rows, self.views
        missing = [n for n in NAMES_AT_FX317 if n not in rows]
        self.assertEqual(missing, [], "rows known at FX-31.7 that %s no longer carries" % self.where)
        want_aliases, want_dispatch, want_spoken, want_arm, want_mix, want_cost, want_fallbacks = {}, {}, {}, [], [], [], []
        for name, row in rows.items():
            if row["transport"] == "bridge":
                for key in [name] + list(row.get("aliases", [])):
                    want_aliases[key] = row["id"]
                if row.get("stage", "seated") == "seated":   # a shadow or retired row is never a dispatch alias
                    want_dispatch[name] = row.get("answers_as", row["id"])
                if row.get("fallback") is True:
                    want_fallbacks.append(row["id"])
            for phrase in row.get("spoken", []):
                want_spoken[phrase] = name
            arm = row.get("arm")
            if arm is not None:
                want_arm.extend((k, name) for k in arm["keys"])
                want_cost.append((name, float(arm["cost_usd"])))
                if "mix" in arm:
                    want_mix.append((name, float(arm["mix"])))
        self.assertEqual(views["bridge_aliases"], want_aliases, "bridge aliases are not the bridge rows' names and aliases")
        self.assertEqual(views["dispatch_ids"], want_dispatch, "dispatch ids are not the bridge rows' answers_as or id")
        self.assertEqual(list(views["bridge_fallbacks"]), want_fallbacks, "the bridge fallbacks are not the bridge rows flagged fallback, in row order")
        self.assertEqual(views["spoken"], want_spoken, "spoken phrases are not the rows' spoken lists")
        self.assertEqual(tuple(views["arm_of"]), tuple(want_arm), "arm keys are not the arm rows' keys in row order")
        self.assertEqual(_tokens(views["default_mix"]), want_mix, "the default mix is not the arm rows' mix in row order")
        self.assertEqual(_tokens(views["arm_cost"]), want_cost, "the arm cost is not the arm rows' cost_usd in row order")

    def test_no_view_names_a_model_outside_the_rows(self):
        named = set(self.views["dispatch_ids"]) | set(self.views["spoken"].values()) | {a for _, a in self.views["arm_of"]}
        named |= {n for n, _ in _tokens(self.views["default_mix"])} | {n for n, _ in _tokens(self.views["arm_cost"])}
        self.assertEqual(sorted(named - set(self.rows)), [], "a view names a model the registry does not carry")
        ids = {r["id"] for r in self.rows.values()}
        self.assertEqual(sorted(set(self.views["bridge_aliases"].values()) - ids), [], "a bridge alias names an id outside the rows")
        self.assertEqual(sorted(set(self.views["bridge_fallbacks"]) - ids), [], "a bridge fallback names an id outside the rows")


class PreRemovalTablesReproduced(_Live):
    """The hand typed tables, entry for entry, from the rows: the proof the deletion in FX-31.6 rested on."""

    def _subset(self, view, fixture, what):
        got = {k: self.views[view].get(k) for k in fixture}
        self.assertEqual(got, fixture, "%s: the derived %s no longer carries the entries the hand typed table did" % (what, view))

    def test_the_bridge_alias_table_is_reproduced(self):
        self._subset("bridge_aliases", PRE_REMOVAL["bridge_aliases"], "or_ask.MODEL_ALIASES")

    def test_the_dispatch_id_table_is_reproduced(self):
        self._subset("dispatch_ids", _without_retired(PRE_REMOVAL["dispatch_ids"]), "or_dispatch_cli.REAL_MODEL_IDS")

    def test_a_retired_row_is_retired_in_the_registry_and_never_seated(self):
        rows = R.registry()
        for name in RETIRED_SINCE:
            self.assertEqual(rows[name].get("stage"), "retired", "%s is exempted as retired but its row does not say so" % name)
            self.assertNotIn(name, R.seatable_names(), "a retired row is seatable")
            self.assertNotIn(name, self.views["dispatch_ids"], "a retired row is a dispatch alias")
            self.assertNotIn(name, [n for n, _ in _tokens(self.views["default_mix"])], "a retired row is in the default mix")

    def test_the_spoken_name_table_is_reproduced(self):
        self._subset("spoken", PRE_REMOVAL["spoken"], "loop_intake.MODEL_ALIASES")

    def test_the_arm_key_table_is_reproduced_in_order(self):
        keys = {k for k, _ in PRE_REMOVAL["arm_of"]}
        got = tuple(pair for pair in self.views["arm_of"] if pair[0] in keys)
        self.assertEqual(got, PRE_REMOVAL["arm_of"], "worker_mix.ARM_OF: the derived arm keys differ from the hand typed table")

    def test_the_default_mix_is_reproduced_in_order(self):
        want = ",".join(t for t in PRE_REMOVAL["default_mix"].split(",") if t.split(":")[0] not in RETIRED_SINCE)
        names = [n for n, _ in _tokens(want)]
        got = ",".join(t for t in self.views["default_mix"].split(",") if t.split(":")[0] in names)
        self.assertEqual(got, want, "worker_mix.DEFAULT_MIX: the derived mix differs from the hand typed text")

    def test_the_arm_cost_text_is_reproduced_byte_for_byte(self):
        names = [n for n, _ in _tokens(PRE_REMOVAL["arm_cost"])]
        got = ",".join(t for t in self.views["arm_cost"].split(",") if t.split(":")[0] in names)
        self.assertEqual(got, PRE_REMOVAL["arm_cost"], "ev_gate.DEFAULT_ARM_COST: the derived cost text differs from the hand typed text")


class ConsumersReadTheViews(_Live):
    """Each consumer's own reader returns the derived view, not a table of its own."""

    def test_or_ask_bridge_aliases_are_the_registry_view(self):
        self.assertEqual(or_ask.bridge_aliases(), self.views["bridge_aliases"])
        self.assertEqual(or_ask.MODEL_ALIASES, self.views["bridge_aliases"])

    def test_or_ask_fallback_models_are_the_registry_view(self):
        """The bridge's secondaries (sent after every failed attempt) are the rows flagged fallback, never a typed list;
        each is a registry id, and the list is not empty today."""
        self.assertEqual(list(or_ask.fallback_models()), list(self.views["bridge_fallbacks"]))
        self.assertEqual(list(or_ask.FALLBACK_MODELS), list(self.views["bridge_fallbacks"]))
        self.assertTrue(or_ask.FALLBACK_MODELS, "the registry flags no bridge fallback, so the bridge has no secondary")
        self.assertEqual([m for m in or_ask.FALLBACK_MODELS if m not in {r["id"] for r in self.rows.values()}], [])

    def test_worker_mix_arm_keys_and_default_mix_are_the_registry_view(self):
        self.assertEqual(tuple(worker_mix.ARM_OF), tuple(self.views["arm_of"]))
        self.assertEqual(worker_mix.DEFAULT_MIX, self.views["default_mix"])

    def test_ev_gate_arm_cost_is_the_registry_view(self):
        self.assertEqual(ev_gate.DEFAULT_ARM_COST, self.views["arm_cost"])

    def test_loop_intake_spoken_names_are_the_registry_view(self):
        self.assertEqual(loop_intake.MODEL_ALIASES, self.views["spoken"])

    def test_dispatch_cli_real_model_ids_are_the_registry_view(self):
        if not os.path.exists(DISPATCH_CLI):
            self.skipTest("NO-DATA: this tree ships no %s to read" % DISPATCH_CLI)
        code = ("import json, sys; sys.path.insert(0, %r)\n"
                "from plugin.runtime.brother.core import or_dispatch_cli as m\n"
                "print(json.dumps([m.REAL_MODEL_IDS, m.REGISTRY_ERROR]))" % ROOT)
        r = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        ids, error = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIsNone(error)
        self.assertEqual(ids, self.views["dispatch_ids"])

    def test_a_round_is_priced_from_the_rows_arm_cost_and_mix(self):
        want = 0.0
        for row in self.rows.values():
            arm = row.get("arm") or {}
            if "mix" in arm:
                want += arm["mix"] * arm["cost_usd"]
        self.assertAlmostEqual(ev_gate.round_cost_from_env({}) or 0.0, want, places=9)

    def test_a_spoken_phrase_reads_as_its_row(self):
        for phrase, name in self.views["spoken"].items():
            self.assertEqual(loop_intake.read_model(phrase), name, phrase)

    def test_a_ledger_model_reads_as_its_arm(self):
        for key, arm in self.views["arm_of"]:
            self.assertEqual(worker_mix.arm_of("vendor/" + key + "-9"), arm, key)


class TypedIdsOutsideTheRegistry(_Live):
    """No vendor model id is typed in code: a default is a registry name, a deliberate pin is allowlisted and resolves,
    and a new quoted `<provider>/<name with a version digit>` under scripts/ is refused."""

    def _known(self):
        ids = set()
        for row in self.rows.values():
            ids.add(row["id"])
            if "answers_as" in row:
                ids.add(row["answers_as"])
        return ids

    def _scripts_or_skip(self):
        if not os.path.isdir(SCRIPTS):
            self.skipTest("NO-DATA: this tree ships no %s to scan" % SCRIPTS)

    def test_the_bridge_default_is_a_registry_name_resolved_at_the_call(self):
        self.assertNotIn("/", or_ask.DEFAULT_MODEL, "the bridge default is a typed vendor id, not a registry name")
        self.assertIn(or_ask.DEFAULT_MODEL, self.views["bridge_aliases"], "the bridge default names no bridge row")
        self.assertEqual(or_ask.resolve_model(or_ask.DEFAULT_MODEL), R.registry()["deepseek"]["id"],
                         "with no --model the bridge sends deepseek's wire id (muse retired 2026-10-05)")
        pin = os.path.join(SCRIPTS, "bridge_default_model.py")
        if not os.path.exists(pin):
            self.skipTest("NO-DATA: this tree ships no %s" % pin)
        if SCRIPTS not in sys.path:
            sys.path.insert(0, SCRIPTS)
        import bridge_default_model
        self.assertEqual(bridge_default_model.PINNED_DEFAULT_MODEL, or_ask.DEFAULT_MODEL, "the drift pin disagrees with the bridge")

    def test_every_allowlisted_pin_is_present_and_resolves_to_a_registry_row(self):
        self._scripts_or_skip()
        known = self._known()
        for rel, literal, kind in ALLOWLIST:
            path = os.path.join(SCRIPTS, rel)
            self.assertTrue(os.path.exists(path), "stale allowlist: %s no longer ships" % rel)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue('"%s"' % literal in text or "'%s'" % literal in text, "stale allowlist: %r is no longer in %s" % (literal, rel))
            if kind == "exact":
                self.assertIn(literal, known, "%s pins %r, which no registry row answers as" % (rel, literal))
            else:
                self.assertTrue(any(k.startswith(literal) for k in known), "%s expects a prefix %r no registry id starts with" % (rel, literal))

    def test_no_typed_model_id_outside_the_registry_or_the_allowlist(self):
        """CEILINGS, stated so nobody reads this as more than it is: it refuses UNKNOWN ids only, so a new hand typed
        copy of an existing registry id passes; it reads .py files only (scripts/ recursively, plugin/runtime/brother/core/
        flat), never JSON, shell or docs; it sees plain quoted literals only, so an id built by an f-string or by
        concatenation escapes; and it keys on a provider the registry or VENDORS names followed by a name carrying a
        digit, so an id from an unlisted provider, or one with no digit at all, escapes."""
        self._scripts_or_skip()
        known = self._known()
        providers = {i.split("/")[0] for i in known if "/" in i} | set(VENDORS)
        # an optional :tag suffix (nvidia/x:free) is part of the id: without it the pattern stopped at the colon and
        # every tagged id was invisible (final review of FX-31.7)
        pattern = re.compile(r"""["']((?:%s)/[A-Za-z0-9][A-Za-z0-9._-]*[0-9][A-Za-z0-9._-]*(?::[A-Za-z0-9._-]+)?)["']"""
                             % "|".join(re.escape(p) for p in sorted(providers)))
        allowed = {(rel, literal) for rel, literal, kind in ALLOWLIST if kind == "exact"}
        files = []
        for folder, dirs, names in os.walk(SCRIPTS):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            files.extend(os.path.join(folder, n) for n in sorted(names))
        if os.path.isdir(PLUGIN_CORE):
            files.extend(os.path.join(PLUGIN_CORE, n) for n in sorted(os.listdir(PLUGIN_CORE)))
        if os.path.exists(DISPATCH_CLI):
            self.assertIn(DISPATCH_CLI, files, "plugin core ships but is not in the scanned set")
        offenders = []
        for path in files:
            name = os.path.basename(path)
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            rel = os.path.relpath(path, SCRIPTS if path.startswith(SCRIPTS) else ROOT)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for literal in pattern.findall(text):
                if literal not in known and (rel, literal) not in allowed:
                    offenders.append("%s: %r" % (rel, literal))
        self.assertEqual(offenders, [], "typed vendor ids outside the registry and the allowlist (add a registry row, or a deliberate pin to ALLOWLIST)")

    def test_a_default_the_registry_does_not_carry_is_refused_not_sent(self):
        """A readable registry with no row for the default (deepseek since 2026-10-05): the default resolves to nothing,
        and nothing is what goes on the wire."""
        no_default = _write("no-default", {"models": {"muse": _row(id="meta/muse-spark-1.3-contributor")}})
        rc, out, err = _under_registry(no_default, "import or_ask\n"
                                                "print(or_ask.resolve_model('muse'))\n"
                                                "try:\n    print('SENT ' + or_ask.resolve_model(or_ask.DEFAULT_MODEL))\n"
                                                "except or_ask.RegistryUnreadable as exc:\n    print('REFUSED ' + str(exc))")
        self.assertEqual(rc, 0, err[-600:])
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "meta/muse-spark-1.3-contributor", "the fixture registry was not readable, so this proves nothing")
        self.assertTrue(lines[-1].startswith("REFUSED"), lines[-1])
        self.assertIn("'deepseek'", lines[-1])
        self.assertIn("not a vendor id", lines[-1])


class StageGateAtEverySeat(_Live):
    """Gap 3 of the FX-31.7 follow-up review: the stage gate applied once (model_router.seatable_names) and read by every
    seating list, so a mix or a job naming a shadow row is refused by name instead of seated behind chain()'s back."""

    def _shadow_name(self):
        shadow = [n for n, r in self.rows.items() if r.get("stage") == "shadow"]
        if not shadow:
            self.fail("NO-DATA: the registry carries no shadow row to drive this case with")
        return shadow[0]

    def test_seatable_names_apply_the_stage_gate_once(self):
        reg = {"a": _row(id="x/a"), "b": _row(id="x/b", stage="shadow"), "c": _row(id="x/c", stage="retired"),
               "d": _row(id="x/d", stage="odd"), "e": _row(id="x/e", stage="seated")}
        self.assertEqual(R.seatable_names(reg), ["a", "e"])

    def test_a_shadow_row_is_no_dispatch_alias(self):
        self.assertNotIn(self._shadow_name(), self.views["dispatch_ids"])

    def test_a_mix_naming_a_shadow_row_is_dropped_by_name(self):
        name = self._shadow_name()
        seated = R.seatable_names()
        self.assertNotIn(name, seated)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = worker_mix.picks(3, ["deepseek"], seated, env={"BROTHER_WORKER_MIX": "%s:2,deepseek:1" % name})
        self.assertNotIn(name, got)
        self.assertIn("%s is not a seatable registry model" % name, buf.getvalue())

    def test_the_fan_out_refuses_a_job_on_a_shadow_row_by_name(self):
        fanout = os.path.join(PLUGIN_CORE, "or_fanout.py")
        if not os.path.exists(fanout):
            self.skipTest("NO-DATA: this tree ships no %s" % fanout)
        name = self._shadow_name()
        code = ("import sys; sys.path.insert(0, %r)\nfrom plugin.runtime.brother.core import or_fanout as f\n"
                "try:\n    f._validate_job({'id': 'j1', 'model': %r, 'prompt': 'p', 'max': 100, 'estimated_cost': 0.01}, 30); print('SEATED')\n"
                "except ValueError as exc:\n    print('REFUSED ' + str(exc))" % (ROOT, name))
        r = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        line = r.stdout.strip().splitlines()[-1]
        self.assertTrue(line.startswith("REFUSED"), line)
        self.assertIn(repr(name), line)


class OneRowAndEmpty(unittest.TestCase):
    def test_one_row_projects_to_exactly_that_row_in_every_view(self):
        solo = {"solo": _row(aliases=["other"], answers_as="x/solo-dated", spoken=["so lo"], fallback=True,
                             arm={"keys": ["solo"], "cost_usd": 0.5, "mix": 3})}
        v = R.derive_model_views(solo)
        self.assertEqual(v, {"bridge_aliases": {"solo": "x/solo", "other": "x/solo"}, "dispatch_ids": {"solo": "x/solo-dated"},
                             "spoken": {"so lo": "solo"}, "arm_of": (("solo", "solo"),), "default_mix": "solo:3",
                             "arm_cost": "solo:0.50", "bridge_fallbacks": ["x/solo"]})

    def test_a_fallback_flag_that_is_not_a_bool_refuses(self):
        with self.assertRaises(R.Refused) as ctx:
            R.derive_model_views({"solo": _row(fallback="yes")})
        self.assertIn("fallback must be true or false", str(ctx.exception))

    def test_a_fallback_flag_on_a_non_bridge_row_refuses(self):
        with self.assertRaises(R.Refused) as ctx:
            R.derive_model_views({"solo": _row(transport="claude", privacy=R.PRIVATE, fallback=True)})
        self.assertIn("claude transport", str(ctx.exception))

    def test_an_empty_registry_refuses_to_project(self):
        with self.assertRaises(R.Refused) as ctx:
            R.derive_model_views({})
        self.assertIn("names no models", str(ctx.exception))

    def test_an_empty_registry_refuses_to_route_rather_than_chaining_nothing(self):
        with self.assertRaises(R.Refused):
            R.chain("build", R.PUBLIC, registry_arg={})


def _under_registry(path, code):
    """Run `code` in a fresh interpreter with BROTHER_MODEL_REGISTRY at `path`; (returncode, stdout, stderr)."""
    env = dict(os.environ, BROTHER_MODEL_REGISTRY=path, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (HERE, code)],
                       cwd=_TMP, env=env, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


class CorruptRegistryOnDisk(unittest.TestCase):
    """Every consumer states its refusal or NO-DATA; none imports or answers with a guessed table."""

    @classmethod
    def setUpClass(cls):
        cls.corrupt = _write("corrupt", "{not json")

    def _ok(self, code):
        rc, out, err = _under_registry(self.corrupt, code)
        self.assertEqual(rc, 0, "a traceback escaped: " + err[-600:])
        self.assertNotIn("Traceback", err)
        return out.strip().splitlines()[-1]

    def test_or_ask_refuses_by_name(self):
        line = self._ok("import or_ask\n"
                        "try:\n    or_ask.bridge_aliases(); print('ANSWERED')\n"
                        "except or_ask.RegistryUnreadable as exc:\n    print('REFUSED ' + str(exc))")
        self.assertTrue(line.startswith("REFUSED"), line)
        self.assertIn("not valid JSON", line)

    def test_worker_mix_names_no_arm(self):
        self.assertEqual(self._ok("import worker_mix; print(repr(worker_mix.arm_of('deepseek/deepseek-v4.1-flash')))"), "None")

    def test_ev_gate_prices_no_round(self):
        self.assertEqual(self._ok("import ev_gate; print(repr(ev_gate.round_cost_from_env({})))"), "None")

    def test_loop_intake_reads_no_name(self):
        self.assertEqual(self._ok("import loop_intake; print(repr(loop_intake.read_model('deep seek')))"), "None")

    def test_registry_measure_check_refuses_with_exit_1(self):
        buf = io.StringIO()
        self.assertEqual(registry_measure.check(self.corrupt, datetime.date(2026, 10, 4), out=buf), 1)
        self.assertIn("REFUSED", buf.getvalue())
        self.assertIn("not valid JSON", buf.getvalue())


class ARegistryWhoseViewsRefusePreparesNothing(unittest.TestCase):
    """A registry can LOAD while its derived views REFUSE: the loader checks five fields per row, the views check the
    rest, and a retired row that kept its mix share (the slip a retirement edit invites) is exactly that. read_model
    then answers None, its not certain answer, and until 2026-10-06 the intake's entry point stored that None as the
    role's choice, where loop_roles.judge reads `choice or default`: the owner typed a model, the role quietly took its
    default, and the only trace was a line reading "understood as None". The entry point now derives the views
    before it reads a word and prepares nothing when they refuse. Driven at the entry point, in the intake's own
    sandbox (INTAKE_EVIDENCE, INTAKE_PROBES=stub, the way its selftest runs it), under an empty HOME."""

    @classmethod
    def setUpClass(cls):
        seated = _row(transport="claude", privacy=R.PRIVATE, quality={"build": 5, "grade": 5, "prose": 5, "plan": 5})
        retired_with_a_share = dict(seated, stage="retired", arm={"keys": ["solo"], "cost_usd": 0.1, "mix": 1})
        cls.registry = _write("views-refuse", {"models": {"solo": retired_with_a_share}})
        # THE CONTROL: the same sandbox with a registry whose views derive must be ANSWERED. Without it a copy of this
        # file that cannot load its roles under an empty HOME (the intake's other NO-DATA) would pass every case below
        # for the wrong reason; there the cases say they did not run.
        control, _ = cls._intake("guide", registry=_write("views-derive", {"models": {"solo": seated}}))
        if control.returncode != 0:
            raise unittest.SkipTest("the intake does not answer here even with a registry whose views derive (exit %d: %s), "
                                    "so this copy cannot tell the two refusals apart"
                                    % (control.returncode, (control.stdout + control.stderr).strip()[-160:]))

    @classmethod
    def _intake(cls, *argv, registry=None):
        box = tempfile.mkdtemp(prefix="intake-", dir=_TMP)
        home = tempfile.mkdtemp(prefix="empty-home-", dir=_TMP)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=home, INTAKE_EVIDENCE=box, INTAKE_PROBES="stub", BROTHER_MODEL_REGISTRY=registry or cls.registry,
                   PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "loop_intake.py")] + list(argv), cwd=_TMP, env=env,
                           capture_output=True, text=True, timeout=120)
        return r, os.path.join(box, "loop-intake", "CURRENT.json")

    def test_the_fixture_registry_loads_and_its_views_refuse(self):
        # the premise, or the two cases below prove nothing: this registry is past the loader and stopped at the views
        self.assertIn("solo", R.load_registry(self.registry))
        with self.assertRaises(R.Refused) as ctx:
            R.derive_model_views(R.load_registry(self.registry))
        self.assertIn("carries no mix share", str(ctx.exception))

    def test_prepare_with_a_typed_model_prepares_nothing_and_writes_no_record(self):
        r, record = self._intake("prepare", "finisher=solo", "--deadline", "00:00", "--budget-usd", "1")
        self.assertEqual(r.returncode, 3, (r.stdout + r.stderr)[-600:])
        self.assertIn("INTAKE NO-DATA", r.stdout)
        self.assertNotIn("understood as None", r.stdout)
        self.assertFalse(os.path.exists(record), "a record was written on a registry whose views refuse")

    def test_the_guide_is_no_data_too(self):
        r, record = self._intake("guide")
        self.assertEqual(r.returncode, 3, (r.stdout + r.stderr)[-600:])
        self.assertIn("INTAKE NO-DATA", r.stdout)


class StaleReadOn(unittest.TestCase):
    """c14: a stale registry warns and asks the owner; it blocks neither the check nor routing."""

    TODAY = datetime.date(2026, 10, 4)

    @classmethod
    def setUpClass(cls):
        stale = (cls.TODAY - datetime.timedelta(days=91)).isoformat()
        cls.stale = _write("stale", {"read_on": stale, "models": {"solo": _row(arm={"keys": ["solo"], "cost_usd": 0.5, "mix": 1})}})
        cls.fresh = _write("fresh", {"read_on": (cls.TODAY - datetime.timedelta(days=1)).isoformat(), "models": {"solo": _row()}})

    def test_stale_read_on_warns_without_blocking(self):
        buf = io.StringIO()
        rc = registry_measure.check(self.stale, self.TODAY, out=buf)
        text = buf.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn("WARN: the model registry was last measured 91 days ago", text)
        self.assertIn("class 2", text)

    def test_fresh_read_on_is_ok_not_warn(self):
        buf = io.StringIO()
        self.assertEqual(registry_measure.check(self.fresh, self.TODAY, out=buf), 0)
        self.assertIn("OK: the model registry was measured 1 days ago", buf.getvalue())
        self.assertNotIn("WARN", buf.getvalue())

    def test_a_stale_registry_still_routes_and_prices(self):
        rc, out, err = _under_registry(self.stale, "import model_router as R, ev_gate\n"
                                                   "print(R.chain('build', R.PUBLIC), repr(ev_gate.round_cost_from_env({})))")
        self.assertEqual(rc, 0, err[-600:])
        self.assertEqual(out.strip().splitlines()[-1], "['solo'] 0.5")


FIXTURE = os.path.join(ROOT, "scripts", "fixtures", "model-registry-fixture.json")
TRACKED = os.path.join(ROOT, "docs", "plan", "model-registry.json")


@unittest.skipUnless(os.path.isfile(FIXTURE), "no scripts/fixtures/model-registry-fixture.json beside this copy (the bundled mirror ships none)")
class TheFixtureIsTheRegistryWhereNoneShips(unittest.TestCase):
    """THE EXPORT SHAPE (the cut preflight's virgin gate, first reached 2026-10-06). The public tree ships no
    docs/plan/model-registry.json and a stranger's HOME holds none, so the router's last candidate, the tracked fixture,
    IS the registry there. It was a hand copy last matched on 2026-09-21: muse, retired on 2026-10-05 in docs/plan
    alone, was still seated in it with a mix share of 2, and three later rows were missing. Each case below makes the
    fixture the only registry that answers, under an empty HOME, which is what that tree gives the router."""

    def _only_the_fixture(self, tool):
        home = tempfile.mkdtemp(prefix="empty-home-", dir=_TMP)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        env.update(HOME=home, BROTHER_MODEL_REGISTRY=FIXTURE, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, "-B", os.path.join(HERE, tool), "--selftest"], cwd=_TMP, env=env,
                              capture_output=True, text=True, timeout=300)

    def test_the_router_selftest_is_green_when_only_the_fixture_answers(self):
        r = self._only_the_fixture("model_router.py")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-600:])

    def test_the_worker_mix_selftest_is_green_when_only_the_fixture_answers(self):
        r = self._only_the_fixture("worker_mix.py")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-600:])

    def test_no_retired_row_carries_a_mix_share_or_a_dispatch_id_in_the_fixture(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            rows = json.load(fh)["models"]
        views = R.derive_model_views(R.load_registry(FIXTURE))
        for name in RETIRED_SINCE:
            self.assertEqual(rows.get(name, {}).get("stage"), "retired", "%s is retired in docs/plan and must read retired here" % name)
            self.assertNotIn(name, views["dispatch_ids"])
            self.assertNotIn(name + ":", views["default_mix"])

    @unittest.skipUnless(os.path.isfile(TRACKED), "this tree ships no docs/plan/model-registry.json to compare the fixture with (the public export)")
    def test_the_fixture_is_a_byte_copy_of_the_registry(self):
        with open(FIXTURE, "rb") as fh:
            fixture = fh.read()
        with open(TRACKED, "rb") as fh:
            tracked = fh.read()
        self.assertTrue(fixture == tracked, "scripts/fixtures/model-registry-fixture.json fell behind: "
                        "cp docs/plan/model-registry.json scripts/fixtures/model-registry-fixture.json")


if __name__ == "__main__":
    unittest.main()
