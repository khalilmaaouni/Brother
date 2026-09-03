#!/usr/bin/env python3
"""Suite for tools/bm_lead.py: founder mode, IC mode, the insight ledger's
command surface, the half-hour catch-up, the handback and the handover pack
(design docs/program/absolute-lead/DESIGN-L04.md sections 3, 4, 7, 8, 9, 10,
11, 12, 17.1 and 17.2).

THE ONE TEST THAT MATTERS MOST is in TestConsentIsTheOnlyDoor: every one of
the nine subcommands, run in a fresh HOME with no consent record, creates
ZERO files anywhere under that HOME or under the project root. The founder
decision this loop implements is explicit that absence of a write is not
enough, because this project has broken the pre-consent law twice and both
times an outside reviewer caught it rather than its own tests. So the same
class also parses tools/bm_lead.py with ast and fails if a SECOND store
constructor is ever added to it, which is what makes the gate structural
rather than a habit.

Most tests drive bm_lead.py IN PROCESS through main(), against a throwaway
project root under a temporary directory, with BROTHERME_CONFIG and
BROTHERMODE_ROOT pointed at that directory, so nothing here can reach the
real ~/.brotherme, the real vault or this checkout's own records. The
pre-consent class drives the real file as a REAL SUBPROCESS instead, the
same discipline tools/test_bm_consent.py uses: a gate exercised only in
process is not exercised at the layer a founder's terminal actually uses.

Every store this file builds comes from bl.bs, the bm_store module
tools/bm_lead.py itself loaded, never from a second independent load. Two
branches of bm_lead.py CATCH a store refusal (cmd_handback's failure path
and cmd_insight's refusal reporting), and a refusal raised by a second load
of bm_store is a DIFFERENT class those branches can never catch. That is
the rule DESIGN-L04 section 17.6 states, and tools/test_bm_controller.py
already documents the same trap.

Python 3.9, standard library only. No network.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import ast
import datetime
import importlib.util
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
ROOT = os.path.dirname(HERE)
LEAD_FILE = os.path.join(HERE, "bm_lead.py")
TERMINOLOGY = os.path.join(ROOT, "references", "terminology.md")
STATUS_VIEW = os.path.join(ROOT, "references", "status-view.md")
EVIDENCE_DIR = os.path.join(ROOT, "docs", "program", "absolute-lead",
                            "evidence", "L04")


def _load(name):
    """Load a sibling module by PATH, the same technique tools/bm_lead.py,
    tools/bm_controller.py and tools/bm_autonomy.py use for tools/
    bm_store.py."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_optional(name):
    """None instead of an exception when the module is not there yet.

    Deliberate: on the untouched tree tools/bm_lead.py does not exist, and a
    bare import failure here would collapse this whole file into ONE error,
    which is not per-class failing-first evidence. With this, every test
    fails on its own with a message naming the missing file, so the RED
    capture shows a count per class."""
    try:
        return _load(name)
    except Exception:  # sbe: allow-silent per docstring: missing module is per-test failing-first evidence, not a crash
        return None


bl = _load_optional("bm_lead")
# See the module docstring: bl.bs when bm_lead has been loaded, so a refusal
# a bm_lead branch catches is the SAME class it raises.
bs = bl.bs if bl is not None else _load("bm_store")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _read_text(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _files_under(root):
    """Every file under root, relative and sorted. Directories are not
    counted on their own; any file inside one is."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        if "Library/Caches" in dirpath:
            continue
        for f in files:
            found.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(found)


def _write_consented_config(path, vault, mode="clone"):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump({"setup_complete": True, "vault_path": vault,
                   "privacy_notice_version": "2026-08-01",
                   "installation_mode": mode,
                   "security_mode": "standard"}, fh)


def _actor(name="coordinator", actor_type="model", session_id="sess-lead"):
    return {"actor_type": actor_type, "actor_name": name,
            "session_id": session_id}


def _project_dict(pid="p1", name="widget", goal="", now=None):
    stamp = now or "2026-08-05T09:00:00Z"
    return {"project_id": pid, "name": name, "goal": goal,
            "created_at": stamp, "updated_at": stamp}


class _Clock(object):
    """A controllable now_iso, so the active-work clock and the catch-up
    cadence are driven by ARRANGED timestamps rather than by sleeping.

    Patching bs.now_iso rather than passing a clock in is what makes every
    row this store writes land at the arranged time, which is the only way
    a test can build a dense or a sparse activity series at all."""

    FORMAT = "%Y-%m-%dT%H:%M:%SZ"

    def __init__(self, start="2026-08-05T09:00:00Z"):
        self.at = datetime.datetime.strptime(start, self.FORMAT)
        self.broken_next = 0

    def iso(self):
        if self.broken_next > 0:
            self.broken_next -= 1
            return "not-a-timestamp"
        return self.at.strftime(self.FORMAT)

    def advance(self, seconds):
        self.at = self.at + datetime.timedelta(seconds=seconds)
        return self.iso()


def _insight(**kw):
    """A minimally valid insight payload, so a test states only the fields
    it is actually about."""
    row = {"kind": "LEARNING", "subject": "the widget",
           "claim": "the widget builds from a clean checkout",
           "evidence_class": "READ", "confidence": "moderate"}
    row.update(kw)
    return row


def _decision(subject="the payment approach", claim="use hosted checkout",
              decision_class="RULE", **kw):
    row = _insight(kind="DECISION", subject=subject, claim=claim,
                   decision_class=decision_class, control_offered=1,
                   flip_condition="a measured refusal rate above 2 percent",
                   alternatives=[{"option": "embedded checkout",
                                  "why_not": "more compliance work"}])
    row.update(kw)
    return row


class LeadCase(unittest.TestCase):
    """A throwaway project root, a consented config, a store and a project.

    Every subclass inherits the same guard: tools/bm_lead.py must exist.
    Until it does, every test in this file fails with that one sentence,
    which is the failing-first evidence DESIGN-L04 section 17 asks for."""

    ENV_KEYS = ("BROTHERME_CONFIG", "BROTHERMODE_ROOT", "BROTHERMODE_VIEW",
                "BROTHERMODE_VAULT")

    def setUp(self):
        self.assertIsNotNone(
            bl, "tools/bm_lead.py does not exist yet, so nothing in this "
                "file can run. That is the failing-first state DESIGN-L04 "
                "section 17 asks every new class to reproduce.")
        self.tmp = tempfile.mkdtemp(prefix="bm-lead-")
        self.root = os.path.join(self.tmp, "project")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.root)
        os.makedirs(self.home)
        self.vault = os.path.join(self.home, "Vault")
        self.config = os.path.join(self.tmp, "cfg", "config.json")
        _write_consented_config(self.config, self.vault)
        self._env_backup = {k: os.environ.get(k) for k in self.ENV_KEYS}
        os.environ["BROTHERME_CONFIG"] = self.config
        os.environ["BROTHERMODE_ROOT"] = self.root
        os.environ.pop("BROTHERMODE_VIEW", None)
        os.environ["BROTHERMODE_VAULT"] = self.vault
        self._real_now = bs.now_iso
        self.clock = _Clock()
        bs.now_iso = self.clock.iso
        self.store = bs.Store(self.root)
        self.actor = _actor()

    def tearDown(self):
        bs.now_iso = self._real_now
        try:
            self.store.close()
        except Exception:  # sbe: allow-silent test teardown best-effort close, the tmp tree is removed either way
            pass
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- driving the CLI in process ------------------------------------

    def run_cli(self, *argv):
        """main(argv) with stdout and stderr captured. Returns
        (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = bl.main(list(argv))
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return code, out.getvalue(), err.getvalue()

    # -- seeding --------------------------------------------------------

    def seed_project(self, pid="p1", goal="a booking site that takes money"):
        self.store.upsert_project(
            _project_dict(pid, goal=goal, now=self.clock.iso()), self.actor)
        return pid

    def sign(self, pid="p1", token_ceiling=100000, minutes_ceiling=1000):
        return self.store.sign_contract(
            pid, "ship the booking site", "the booking test passes",
            ["."], [], ["file-create", "file-edit", "build", "test-run",
                        "read-only-inspect"],
            token_ceiling, minutes_ceiling, "Khalil Maaouni", "sess-lead",
            self.actor, supersede=False)

    def open_run(self, pid="p1", files=None):
        record = self.store.claim(
            name="controller-%s" % pid, lifetime="persistent",
            files=files or ["widget.py"], objective="carry the outcome",
            owner="coordinator", session_id="sess-lead")
        run = self.store.open_run(
            pid, "ctrl1", 1, "ship the booking site",
            "the booking test passes", record.lifecycle_uuid,
            "sess-lead", self.actor)
        return record, run

    def add_forecast(self, pid="p1", **kw):
        f = {"forecast_id": "f1", "project_id": pid,
             "minimum_duration": "2 days", "likely_duration": "3 days",
             "maximum_duration": "5 days",
             "input_token_range": "20k to 40k",
             "output_token_range": "5k to 10k",
             "effective_total_token_range": "25k to 50k",
             "confidence": "medium",
             "next_reforecast_event": "after the first booking test passes",
             "created_at": self.clock.iso()}
        f.update(kw)
        self.store.add_forecast(f, self.actor)
        return f

    def record(self, payload, pid="p1"):
        return self.store.record_insight(pid, payload, self.actor)

    def plan_one_unit(self, run, unit_id="u1", path="a.py"):
        # upsert_units always flips the run to READY, and that move is only
        # legal from PLANNING, so the run walks its own legal edges first,
        # exactly as the engine walks them in production.
        for state in ("ORIENTING", "PLANNING"):
            self.store.set_run_state(run["run_id"], state, self.actor,
                                     "fixture", "sess-lead")
        self.store.upsert_units(
            run["run_id"],
            [{"unit_id": unit_id, "objective": "wire the form",
              "dependencies": [], "read_scope": [], "write_scope": [path],
              "role": "builder", "risk_class": "file-create",
              "lane": "default", "done_check": "true",
              "done_check_expect_exit": 0, "verifier": ""}],
            self.actor, project_id="p1")
        fence = self.store.claim(
            name="unit-%s" % unit_id, lifetime="ephemeral", files=[path],
            objective="build %s" % unit_id, owner="worker",
            session_id="sess-worker")
        self.store.claim_unit(unit_id, fence.lifecycle_uuid, self.actor,
                              project_id="p1")
        revision = self.store.latest_contract("p1", raw=True)["revision"]
        return self.store.record_dispatch(
            unit_id, 1, revision, fence.lifecycle_uuid, "sess-worker",
            self.actor, project_id="p1")


# ---------------------------------------------------------------------------
# ast helpers, shared by the structural guards below
# ---------------------------------------------------------------------------

def _lead_tree():
    return ast.parse(_read_text(LEAD_FILE), filename="bm_lead.py")


def _docstring_constant_ids(tree):
    """id() of every ast.Constant a docstring holds, so a structural scan
    reads CODE rather than the prose that explains the code. Copied from
    tools/test_bm_controller.py's own TestNoSQLGuard helper."""
    ids = set()
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, holders) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def _functions(tree):
    """[(name, node)] for every module-level and nested function."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
    return out


def _enclosing_function(tree, target):
    """The name of the innermost function containing `target`, or
    '<module>'."""
    best, best_span = "<module>", None
    for name, fn in _functions(tree):
        for node in ast.walk(fn):
            if node is target:
                span = getattr(fn, "end_lineno", fn.lineno) - fn.lineno
                if best_span is None or span <= best_span:
                    best, best_span = name, span
                break
    return best


def _attribute_call_sites(tree, value_name, attr_name):
    """[(lineno, enclosing function)] for every `value_name.attr_name(...)`
    call in the tree."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == attr_name
                and isinstance(fn.value, ast.Name)
                and fn.value.id == value_name):
            hits.append((node.lineno, _enclosing_function(tree, node)))
    return hits


def _string_constants(tree):
    """[(lineno, value, enclosing function)] for every non-docstring string
    constant."""
    skip = _docstring_constant_ids(tree)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip):
            out.append((node.lineno, node.value,
                        _enclosing_function(tree, node)))
    return out


# ---------------------------------------------------------------------------
# 17.1 TestConsentIsTheOnlyDoor: the structural gate, and the zero-file proof
# ---------------------------------------------------------------------------

_SUBCOMMAND_DRIVES = (
    ("outcome", ["outcome", "--project-id", "p1"]),
    ("status", ["status", "--project-id", "p1"]),
    ("decisions", ["decisions", "--project-id", "p1"]),
    ("brief", ["brief", "--project-id", "p1"]),
    ("insight", ["insight", "--project-id", "p1", "--kind", "LEARNING",
                 "--subject", "s", "--claim", "c", "--evidence-class",
                 "READ", "--confidence", "moderate", "--actor-name",
                 "coordinator"]),
    ("handback", ["handback", "--project-id", "p1", "--decision-id",
                  "deadbeef", "--why", "I will take this one",
                  "--actor-name", "coordinator"]),
    ("handover-pack", ["handover-pack", "--project-id", "p1"]),
    ("watchdog", ["watchdog", "--tick"]),
    ("outcome --set", ["outcome", "--project-id", "p1", "--set",
                       "a booking site", "--actor-name", "coordinator"]),
)


class TestConsentIsTheOnlyDoor(unittest.TestCase):
    """DESIGN-L04 section 8.4. Four mechanisms, and the first one is the
    structural one: tools/bm_lead.py constructs a store in EXACTLY ONE
    function, and that function refuses without consent, so a writer who
    forgets the check cannot obtain a store handle at all.

    The last test here is the founder decision made executable: every
    subcommand, in a fresh HOME with no consent record, creates zero files
    anywhere. Absence of a write is not enough, so this walks both trees
    before and after rather than trusting stdout."""

    def setUp(self):
        self.assertTrue(
            os.path.isfile(LEAD_FILE),
            "tools/bm_lead.py does not exist yet, so the one door cannot "
            "be proven to be the only one.")

    def test_the_store_is_constructed_in_exactly_one_function(self):
        tree = _lead_tree()
        sites = (_attribute_call_sites(tree, "bs", "Store")
                 + _attribute_call_sites(tree, "bs", "ReadOnlyStore"))
        self.assertTrue(
            sites,
            "tools/bm_lead.py constructs no store at all, so this guard "
            "would pass vacuously")
        owners = sorted(set(fn for _line, fn in sites))
        self.assertEqual(
            ["_store_or_refuse"], owners,
            "bs.Store( and bs.ReadOnlyStore( may appear in exactly one "
            "function, _store_or_refuse, which is the only place consent "
            "is checked before a handle exists. Found them in: %s (sites: "
            "%s)" % (owners, sites))

    def test_no_call_opens_a_file_for_writing(self):
        """A generated page is written through bs.write_generated_document,
        which is reached only from a function already holding a handle from
        _store_or_refuse. A bare open(path, 'w') would be a second write
        path with no gate in front of it."""
        tree = _lead_tree()
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name not in ("open",):
                continue
            modes = [a.value for a in node.args
                     if isinstance(a, ast.Constant)
                     and isinstance(a.value, str)]
            modes += [k.value.value for k in node.keywords
                      if k.arg == "mode" and isinstance(k.value, ast.Constant)
                      and isinstance(k.value.value, str)]
            for mode in modes:
                if any(ch in mode for ch in "wax"):
                    offenders.append((node.lineno, mode))
        self.assertEqual(
            [], offenders,
            "tools/bm_lead.py opened a file for writing directly (line, "
            "mode): %s. Every generated page goes through "
            "bs.write_generated_document." % offenders)

    def test_no_move_or_copy_primitive_and_one_gated_directory_maker(self):
        """os.replace and shutil appear nowhere: the atomic replacement and
        every copy or delete belong to the store's own funnel.

        ONE STATED DEVIATION from the design's wording, and it is stated
        rather than smuggled: section 8.4 mechanism 3 asks for os.makedirs
        to appear NOWHERE, but bs.write_generated_document creates nothing
        ("The directory must exist; this creates nothing", its own
        docstring) and the handover pack is a generated FOLDER. So the
        assertion here is the same law one step narrower: os.makedirs may
        appear in exactly ONE function, _ensure_pack_dir, whose first
        parameter is the store handle _store_or_refuse returned, which is
        precisely the reachability argument the design already makes for
        write_generated_document."""
        tree = _lead_tree()
        for module, attr in (("os", "replace"), ("shutil", "rmtree"),
                             ("shutil", "copy"), ("shutil", "copytree"),
                             ("shutil", "move")):
            self.assertEqual(
                [], _attribute_call_sites(tree, module, attr),
                "%s.%s must not appear in tools/bm_lead.py" % (module, attr))
        self.assertNotIn(
            "shutil", [n.names[0].name for n in ast.walk(tree)
                       if isinstance(n, ast.Import)],
            "tools/bm_lead.py must not import shutil")
        makedirs = _attribute_call_sites(tree, "os", "makedirs")
        owners = sorted(set(fn for _line, fn in makedirs))
        self.assertEqual(
            ["_ensure_pack_dir"], owners,
            "os.makedirs may appear in exactly one function, "
            "_ensure_pack_dir, which takes the store handle. Found: %s"
            % (makedirs,))
        fn = [node for name, node in _functions(tree)
              if name == "_ensure_pack_dir"]
        self.assertEqual(1, len(fn), "_ensure_pack_dir is not defined once")
        self.assertEqual(
            "store", fn[0].args.args[0].arg,
            "_ensure_pack_dir's first parameter must be the store handle, "
            "so the only directory-creating call in the file is "
            "unreachable without one")

    def test_consent_is_computed_before_the_commands_lookup(self):
        tree = _lead_tree()
        mains = [node for name, node in _functions(tree) if name == "main"]
        self.assertEqual(1, len(mains), "bm_lead.py must define one main")
        main = mains[0]
        consent_lines = [node.lineno for node in ast.walk(main)
                         if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name)
                         and node.func.id == "_consent_state"]
        self.assertTrue(
            consent_lines,
            "main() never calls _consent_state(), so the dispatch is not "
            "gated at the entry point")
        lookup_lines = [node.lineno for node in ast.walk(main)
                        if isinstance(node, ast.Subscript)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "COMMANDS"]
        self.assertTrue(lookup_lines, "main() never subscripts COMMANDS")
        self.assertLess(
            max(consent_lines), min(lookup_lines),
            "the consent computation must come BEFORE the COMMANDS lookup "
            "in main(); consent at line(s) %s, lookup at line(s) %s"
            % (consent_lines, lookup_lines))

    def test_commands_is_read_in_exactly_one_function(self):
        tree = _lead_tree()
        readers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "COMMANDS":
                enclosing = _enclosing_function(tree, node)
                if enclosing != "<module>":
                    readers.add(enclosing)
        self.assertEqual(
            {"main"}, readers,
            "COMMANDS may be read in exactly one function, main, so there "
            "is no second dispatch that skips the gate. Found: %s"
            % sorted(readers))

    def test_every_subcommand_in_a_fresh_home_creates_zero_files(self):
        """THE FOUNDER DECISION, made executable.

        Nine subcommands, a fresh HOME with no consent record, a fresh and
        empty project root. After each one: zero files under HOME, zero
        files under the project root, no vault directory, and the watchdog
        printed NOTHING while a human-invoked command printed the setup
        sentence. Walked, not trusted."""
        tmp = tempfile.mkdtemp(prefix="bm-lead-preconsent-")
        try:
            home = os.path.join(tmp, "home")
            project = os.path.join(tmp, "project")
            os.makedirs(home)
            os.makedirs(project)
            vault = os.path.join(home, "BrotherModeVault")
            env = dict(os.environ)
            env["HOME"] = home
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["BROTHERMODE_ROOT"] = project
            for k in ("BROTHERME_CONFIG", "BROTHERMODE_VAULT",
                      "BROTHERMODE_VIEW", "BM_FENCE_SESSION_ID"):
                env.pop(k, None)
            for label, argv in _SUBCOMMAND_DRIVES:
                r = subprocess.run(
                    [sys.executable, LEAD_FILE] + argv, cwd=project, env=env,
                    input="{}", stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, universal_newlines=True,
                    timeout=180)
                self.assertEqual(
                    [], _files_under(home),
                    "%s created file(s) under a fresh HOME before consent: "
                    "%s\n%s%s" % (label, _files_under(home), r.stdout,
                                  r.stderr))
                self.assertEqual(
                    [], _files_under(project),
                    "%s created file(s) under the project root before "
                    "consent: %s\n%s%s"
                    % (label, _files_under(project), r.stdout, r.stderr))
                self.assertFalse(
                    os.path.exists(vault),
                    "%s materialized the vault directory before consent"
                    % label)
                if label == "watchdog":
                    self.assertEqual(
                        0, r.returncode,
                        "the watchdog must exit 0 so a hook line never "
                        "breaks a founder's turn:\n%s%s"
                        % (r.stdout, r.stderr))
                    self.assertEqual(
                        "", r.stdout.strip(),
                        "the watchdog must print NOTHING before consent; "
                        "it printed %r" % r.stdout)
                    self.assertEqual(
                        "", r.stderr.strip(),
                        "the watchdog must print NOTHING before consent; "
                        "it wrote %r to stderr" % r.stderr)
                else:
                    self.assertIn(
                        "python3 scripts/setup.py", r.stdout + r.stderr,
                        "%s must tell a human what to run; it printed %r"
                        % (label, r.stdout + r.stderr))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 17.1 the eight fields
# ---------------------------------------------------------------------------

_FIELD_RE = re.compile(r"^([A-Z][A-Za-z ]*):")

EIGHT_FIELDS = ["Goal", "Direction", "Progress", "Time remaining",
                "Decision needed", "Risk", "Evidence", "Next step"]

NINE_ADVANCED = ["task ids", "runtime and model", "token input and output",
                 "worktree paths", "commands that were run",
                 "raw test output", "hook evidence", "store verification",
                 "receipt fingerprints"]


def _field_labels(text):
    """Every label at column zero, in order. Continuation lines are
    indented and belong to the field above them."""
    return [_FIELD_RE.match(line).group(1)
            for line in text.splitlines()
            if _FIELD_RE.match(line)]


def _field_values(text):
    """{label: value} for the column-zero label lines."""
    out = {}
    for line in text.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            out[m.group(1)] = line[len(m.group(1)) + 1:].strip()
    return out


class TestTheEightFieldsAreComputedNotNarrated(LeadCase):
    """DESIGN-L04 section 3.2 and 3.4. The largest single improvement over
    what shipped before this loop: commands/brotherme-status.md used to ask
    the MODEL to translate a different command's report into the eight
    fields, which put a language model between the founder and the truth.
    They are computed now, and this class is what says so."""

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()
        self.add_forecast()

    def test_status_prints_exactly_the_eight_fields_in_order(self):
        code, out, err = self.run_cli("status", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        self.assertEqual(
            EIGHT_FIELDS, _field_labels(out),
            "status must print exactly the eight fields of "
            "references/status-view.md lines 8 to 16, in that order, and "
            "nothing else at column zero. Got:\n%s" % out)

    def test_the_label_reader_really_finds_a_ninth_field(self):
        """Calibration. An equality against a list a broken extractor also
        satisfies is not evidence, so this proves the extractor sees a
        ninth label when one is there."""
        planted = "\n".join(["%s: x" % f for f in EIGHT_FIELDS]
                            + ["Machinery: a ninth field"])
        self.assertEqual(EIGHT_FIELDS + ["Machinery"],
                         _field_labels(planted))

    def test_an_absent_field_says_so_rather_than_being_omitted(self):
        self.store.upsert_project(
            _project_dict("p2", goal="", now=self.clock.iso()), self.actor)
        code, out, err = self.run_cli("status", "--project-id", "p2")
        self.assertEqual(0, code, out + err)
        self.assertEqual(EIGHT_FIELDS, _field_labels(out))
        values = _field_values(out)
        self.assertIn("no outcome recorded yet", values["Goal"])
        self.assertIn("not agreed yet", values["Direction"])
        self.assertIn("nothing planned yet", values["Progress"])
        self.assertIn("not forecast yet", values["Time remaining"])
        self.assertEqual("none", values["Decision needed"])
        self.assertIn("none", values["Risk"])
        self.assertIn("no executed evidence recorded yet", values["Evidence"])

    def test_advanced_adds_exactly_the_nine_items(self):
        code, out, err = self.run_cli("status", "--project-id", "p1",
                                      "--advanced")
        self.assertEqual(0, code, out + err)
        for item in NINE_ADVANCED:
            self.assertIn(
                item, out,
                "--advanced must add the nine items of "
                "references/status-view.md lines 43 to 51; %r is missing "
                "from:\n%s" % (item, out))

    def test_advanced_is_not_sticky(self):
        code, adv, err = self.run_cli("status", "--project-id", "p1",
                                      "--advanced")
        self.assertEqual(0, code, adv + err)
        code, plain, err = self.run_cli("status", "--project-id", "p1")
        self.assertEqual(0, code, plain + err)
        for item in NINE_ADVANCED:
            self.assertNotIn(
                item, plain,
                "advanced is per request, never sticky "
                "(references/status-view.md line 53): %r survived into the "
                "next bare status:\n%s" % (item, plain))
        self.assertEqual(EIGHT_FIELDS, _field_labels(plain))


# ---------------------------------------------------------------------------
# 17.1 exactly one recommended next action, nine branches
# ---------------------------------------------------------------------------

class TestExactlyOneNextAction(LeadCase):
    """DESIGN-L04 section 3.6. A first-match router over rows, so two runs
    on the same rows always agree, and exactly one action is printed."""

    def _branch(self, pid="p1"):
        return bl._next_action_branch(self.store, pid)[0]

    def _assert_one_next_step(self, out):
        lines = [ln for ln in out.splitlines() if ln.startswith("Next step:")]
        self.assertEqual(
            1, len(lines),
            "exactly one line may begin 'Next step:'; found %d in:\n%s"
            % (len(lines), out))

    def test_branch_7_no_outcome_recorded(self):
        self.store.upsert_project(_project_dict("p1", goal="",
                                                now=self.clock.iso()),
                                  self.actor)
        self.assertEqual("state-outcome", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self.assertEqual(0, code, out)
        self._assert_one_next_step(out)

    def test_branch_6_outcome_recorded_but_no_live_authorisation(self):
        self.seed_project()
        self.assertEqual("agree-authorisation", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_8_continue_the_newest_ready_task(self):
        self.seed_project()
        self.sign()
        self.store.create_task({"task_id": "t1", "project_id": "p1",
                                "title": "wire the booking form",
                                "status": "ready", "priority": "high"},
                               self.actor)
        self.assertEqual("continue-task", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_9_nothing_is_waiting(self):
        self.seed_project()
        self.sign()
        self.assertEqual("nothing-waiting", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_5_a_run_with_work_in_flight(self):
        self.seed_project()
        self.sign()
        record, run = self.open_run()
        self.plan_one_unit(run)
        self.assertEqual("finish-work-in-flight", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_4_the_work_is_ready_to_accept(self):
        self.seed_project()
        self.sign()
        record, run = self.open_run()
        self.store.set_run_state(run["run_id"], "ORIENTING", self.actor,
                                 "orienting", "sess-lead")
        self.store.set_run_state(run["run_id"], "PLANNING", self.actor,
                                 "planning", "sess-lead")
        self.store.set_run_state(run["run_id"], "READY", self.actor,
                                 "ready", "sess-lead")
        self.store.set_run_state(run["run_id"], "DELIVERABLE_READY",
                                 self.actor, "done", "sess-lead")
        self.assertEqual("accept-work", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_3_an_open_key_decision(self):
        self.seed_project()
        self.sign()
        self.record(_decision())
        self.assertEqual("answer-decision", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_2_an_open_founder_step(self):
        self.seed_project()
        self.sign()
        self.store.queue_human_step(
            "p1", "publish-release", "release",
            "click publish in the dashboard", "Settings then Publish", [],
            "sess-lead", self.actor)
        self.assertEqual("founder-step", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)

    def test_branch_1_a_key_decision_that_names_a_safety_floor(self):
        self.seed_project()
        self.sign()
        self.record(_decision(
            subject="publishing the release",
            claim="hold the publish-release step for the founder",
            decision_class="GATE"))
        self.assertEqual("answer-floor-decision", self._branch())
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self._assert_one_next_step(out)


# ---------------------------------------------------------------------------
# 17.1 ranges never points
# ---------------------------------------------------------------------------

class TestRangesNeverPoints(LeadCase):
    """references/forecasting.md's one law: a bare date or a bare number is
    a false promise and is never emitted."""

    def test_a_forecast_renders_as_a_range_with_its_confidence(self):
        self.seed_project()
        self.add_forecast()
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self.assertEqual(0, code, out)
        self.assertIn("2 days to 5 days", out)
        self.assertIn("likely 3 days", out)
        self.assertIn("confidence: medium", out)
        self.assertIn("next reforecast:", out)

    def test_a_forecast_with_only_a_likely_value_marks_the_unknown_ends(self):
        self.seed_project()
        self.add_forecast(minimum_duration="", maximum_duration="",
                          input_token_range="", output_token_range="",
                          effective_total_token_range="")
        code, out, _e = self.run_cli("status", "--project-id", "p1")
        self.assertEqual(0, code, out)
        self.assertIn("? to ?", out)
        self.assertNotIn("3 days\n", out.replace("likely 3 days", ""))

    def test_no_duration_word_is_formatted_outside_one_function(self):
        """Structural. There is no arithmetic on top of a forecast anywhere
        in bm_lead.py, so no format string outside render_forecast_lines may
        carry a bare 'hours' or 'days'."""
        tree = _lead_tree()
        offenders = [(line, value, fn)
                     for line, value, fn in _string_constants(tree)
                     if fn != "render_forecast_lines"
                     and re.search(r"\b(hours|days)\b", value)]
        self.assertEqual(
            [], offenders,
            "only render_forecast_lines may format a duration; found: %s"
            % offenders)


# ---------------------------------------------------------------------------
# 17.1 plain language
# ---------------------------------------------------------------------------

def _terminology_terms():
    """The LEFT column of references/terminology.md lines 10 to 25, and
    only those lines.

    Deliberately NOT the whole table. The eight rows added for this loop
    (insight ledger, evidence class, briefing, handback, active minutes,
    watchdog, handover pack, trace tag) sit BELOW line 25 and name the
    things founder mode has to be able to talk about in plain words; the
    right column is what the output should say, and banning the left column
    of those rows would ban the product's own catch-up from saying
    catch-up. The rows this fixture reads are the beginner machinery terms
    that were already law before this loop."""
    lines = _read_text(TERMINOLOGY).splitlines()
    terms = []
    for raw in lines[9:25]:
        if not raw.startswith("|") or raw.startswith("|---"):
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if not cells or cells[0].lower().startswith("internal term"):
            continue
        for term in cells[0].split(","):
            term = term.strip()
            if term:
                terms.append(term)
    return terms


class TestPlainLanguageHoldsInFounderMode(LeadCase):
    """references/terminology.md's own law: the user never needs the
    internal terms to use this product, and the internal term may appear
    only when the advanced view was asked for."""

    def _drive_everything(self, ic=False):
        self.seed_project()
        self.sign()
        self.add_forecast()
        decision = self.record(_decision())
        blob = []
        drives = [
            ["outcome", "--project-id", "p1"],
            ["outcome", "--project-id", "p1", "--set", "a booking site",
             "--actor-name", "coordinator"],
            ["status", "--project-id", "p1"],
            ["decisions", "--project-id", "p1"],
            ["brief", "--project-id", "p1"],
            ["insight", "--project-id", "p1", "--kind", "LEARNING",
             "--subject", "the form", "--claim", "the form submits",
             "--evidence-class", "READ", "--confidence", "moderate",
             "--actor-name", "coordinator"],
            ["handback", "--project-id", "p1", "--decision-id",
             decision["insight_id"], "--why", "I will take this one",
             "--actor-name", "coordinator"],
            ["handover-pack", "--project-id", "p1"],
            ["watchdog", "--tick"],
        ]
        viewing = ("outcome", "status", "decisions", "brief")
        for argv in drives:
            if ic and argv[0] in viewing:
                argv = argv + ["--ic"]
            _code, out, err = self.run_cli(*argv)
            blob.append(out)
            blob.append(err)
        return "\n".join(blob)

    def test_no_machinery_term_reaches_founder_mode(self):
        text = self._drive_everything(ic=False)
        terms = _terminology_terms()
        self.assertGreaterEqual(
            len(terms), 15,
            "references/terminology.md lines 10 to 25 yielded only %d "
            "term(s); a fixture reading fewer rows than the map holds is "
            "not evidence" % len(terms))
        offenders = sorted({t for t in terms
                            if re.search(r"\b%s\b" % re.escape(t), text,
                                         re.IGNORECASE)})
        self.assertEqual(
            [], offenders,
            "founder-mode output used internal machinery term(s) %s; "
            "references/terminology.md says the plain wording in the right "
            "column is what user-facing output says.\n%s"
            % (offenders, text))

    def test_the_scanner_would_have_seen_a_machinery_term(self):
        """Calibration. IC mode is the engineer's view and IS allowed to
        name the machinery, so a drive with --ic must contain at least one
        of the very terms the founder-mode test bans. Without this, the
        assertion above is an equality against an empty list that a broken
        scanner satisfies too."""
        text = self._drive_everything(ic=True)
        terms = _terminology_terms()
        found = sorted({t for t in terms
                        if re.search(r"\b%s\b" % re.escape(t), text,
                                     re.IGNORECASE)})
        self.assertTrue(
            found,
            "no machinery term appeared anywhere in IC mode either, so the "
            "founder-mode scan proves nothing about the scanner")


# ---------------------------------------------------------------------------
# 17.1 the plain-language map for run states
# ---------------------------------------------------------------------------

class TestRunStatePlainCoversEveryState(unittest.TestCase):
    """DESIGN-L04 section 7.3. A future controller state must not be able to
    render as a raw enum in front of a founder, so an import-time guard
    raises when the map has a hole. Guard shape copied from the walk-edge
    guard in tools/bm_controller.py."""

    def setUp(self):
        self.assertIsNotNone(bl, "tools/bm_lead.py does not exist yet")

    def test_the_shipped_map_covers_every_controller_state(self):
        missing = sorted(set(bs.CONTROLLER_STATE_TRANSITIONS)
                         - set(bl.RUN_STATE_PLAIN))
        self.assertEqual(
            [], missing,
            "RUN_STATE_PLAIN has no plain sentence for controller "
            "state(s): %s" % missing)
        for state, sentence in sorted(bl.RUN_STATE_PLAIN.items()):
            self.assertTrue(
                sentence.strip(),
                "%s maps to an empty sentence" % state)

    def test_the_guard_raises_when_a_state_has_no_plain_sentence(self):
        with self.assertRaises(Exception) as ctx:
            bl._check_run_state_plain(
                dict(bs.CONTROLLER_STATE_TRANSITIONS, INVENTED_STATE=()),
                bl.RUN_STATE_PLAIN)
        self.assertIn("INVENTED_STATE", str(ctx.exception))


# ---------------------------------------------------------------------------
# 17.1 the handback option on every key decision card
# ---------------------------------------------------------------------------

class TestEveryKeyDecisionCardEndsInTheHandbackOption(LeadCase):
    """DESIGN-L04 section 9.3, and the founder design's own requirement: a
    decision window rendered without the handback option is a test
    failure."""

    def test_all_five_decision_classes_end_in_the_handback_option(self):
        self.seed_project()
        self.sign()
        for cls in bs.INSIGHT_DECISION_CLASSES:
            row = self.record(_decision(subject="subject for %s" % cls,
                                        claim="claim for %s" % cls,
                                        decision_class=cls))
            insight = self.store.get_insight(row["insight_id"], raw=True)
            lines = bl.render_decision_card(insight)
            options = [ln.strip() for ln in lines
                       if ln.strip().startswith("[")
                       or ln.strip() == bl.HANDBACK_OPTION_TEXT]
            self.assertTrue(options, "no option lines in:\n%s"
                            % "\n".join(lines))
            self.assertEqual(
                bl.HANDBACK_OPTION_TEXT, options[-1],
                "the LAST option line of a %s card must be byte-equal to "
                "HANDBACK_OPTION_TEXT. Got %r from:\n%s"
                % (cls, options[-1], "\n".join(lines)))

    def test_a_preference_decision_says_it_was_declared(self):
        self.seed_project()
        self.sign()
        row = self.record(_decision(decision_class="PREFERENCE"))
        insight = self.store.get_insight(row["insight_id"], raw=True)
        for ic in (False, True):
            text = "\n".join(bl.render_decision_card(insight, ic=ic))
            self.assertIn(
                "declared by the coordinator, not detected from the records",
                text,
                "PREFERENCE is the one honest human-judgement entry and the "
                "ledger says so on that row, in founder mode and IC mode "
                "alike (ic=%s):\n%s" % (ic, text))

    def test_render_decision_card_is_the_only_emitter_of_the_header(self):
        tree = _lead_tree()
        owners = sorted({fn for _line, value, fn in _string_constants(tree)
                         if "Decision needed:" in value})
        self.assertEqual(
            ["render_decision_card"], owners,
            "only render_decision_card may emit 'Decision needed:', because "
            "it is the function that always appends the handback option. "
            "Found in: %s" % owners)


# ---------------------------------------------------------------------------
# 17.1 REASONED is never bare
# ---------------------------------------------------------------------------

class TestReasonedIsNeverBare(LeadCase):
    """DESIGN-L04 law L4 and section 4.4: the REASONED prefix is applied by
    the RENDERER, never by the author, so a REASONED claim cannot reach any
    surface without it."""

    def test_evidence_label_maps_all_four_classes(self):
        expected = {"EXECUTED": "verified by command:",
                    "MEASURED": "measured:",
                    "READ": "verified by inspection:",
                    "REASONED": "my reasoning, not verified:"}
        self.assertEqual(sorted(expected), sorted(bs.EVIDENCE_CLASSES))
        for cls, label in expected.items():
            self.assertEqual(label, bl.evidence_label(cls))

    def test_a_reasoned_claim_carries_its_prefix_on_every_surface(self):
        self.seed_project()
        self.sign()
        # A run makes a catch-up due (a phase boundary), so `brief` renders
        # a real one rather than the quiet-stretch line. Without it the
        # brief half of this assertion would pass vacuously against a page
        # that carries no claim at all.
        self.open_run()
        self.record(_insight(kind="RISK", subject="the refund path",
                             claim="the refund path is probably fine",
                             evidence_class="REASONED",
                             confidence="low",
                             flip_condition="run the refund test"))
        prefix = bl.evidence_label("REASONED")
        code, status_out, _e = self.run_cli("status", "--project-id", "p1")
        self.assertEqual(0, code, status_out)
        self.assertIn(prefix, status_out)
        code, brief_out, _e = self.run_cli("brief", "--project-id", "p1")
        self.assertIn(prefix, brief_out)
        code, out, err = self.run_cli("handover-pack", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        page = _read_text(os.path.join(self.root, bl.HANDOVER_ROOT,
                                       "20-RISKS.md"))
        self.assertIn(prefix, page)

    def test_the_prefix_comes_from_the_renderer_not_from_the_author(self):
        self.seed_project()
        row = self.record(_insight(kind="LEARNING",
                                   claim="the form probably validates",
                                   evidence_class="REASONED",
                                   confidence="low"))
        insight = self.store.get_insight(row["insight_id"], raw=True)
        self.assertNotIn(bl.evidence_label("REASONED"), insight["claim"],
                         "the fixture's own claim already carries the "
                         "prefix, so this would prove nothing")
        line = bl.render_claim_line(insight)
        self.assertTrue(
            line.startswith(bl.evidence_label("REASONED")),
            "render_claim_line must apply the prefix itself; got %r" % line)
        self.assertIn("[i:%s]" % insight["insight_id"], line)


# ---------------------------------------------------------------------------
# 17.1 IC mode and founder mode share one collector
# ---------------------------------------------------------------------------

class TestICModeAndFounderModeShareOneCollector(LeadCase):

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()
        self.sign()
        self.add_forecast()

    def test_every_shared_field_value_is_byte_identical(self):
        _c, founder, _e = self.run_cli("status", "--project-id", "p1")
        _c, engineer, _e = self.run_cli("status", "--project-id", "p1", "--ic")
        f_values = _field_values(founder)
        e_values = _field_values(engineer)
        for label in EIGHT_FIELDS:
            self.assertIn(label, e_values,
                          "IC mode dropped the %s field" % label)
            self.assertEqual(
                f_values[label], e_values[label],
                "one collector, two renderers: the %s field must carry the "
                "same value in both views" % label)

    def test_ic_and_advanced_are_independent(self):
        _c, ic_only, _e = self.run_cli("status", "--project-id", "p1", "--ic")
        for item in NINE_ADVANCED:
            self.assertNotIn(item, ic_only,
                             "--ic turned on the advanced item %r" % item)
        _c, adv_only, _e = self.run_cli("status", "--project-id", "p1",
                                        "--advanced")
        self.assertNotIn(bl.IC_FOOTER_FLAG, adv_only,
                         "--advanced turned on the engineering view")

    def test_the_ic_footer_names_the_switch_that_turned_it_on(self):
        _c, by_flag, _e = self.run_cli("status", "--project-id", "p1", "--ic")
        self.assertIn(bl.IC_FOOTER_FLAG, by_flag)
        os.environ["BROTHERMODE_VIEW"] = "ic"
        try:
            _c, by_env, _e = self.run_cli("status", "--project-id", "p1")
        finally:
            os.environ.pop("BROTHERMODE_VIEW", None)
        self.assertIn(bl.IC_FOOTER_ENV, by_env,
                      "a sticky switch that does not say it is on is a mode "
                      "the reader did not choose")


# ---------------------------------------------------------------------------
# 17.2 the active-work clock
# ---------------------------------------------------------------------------

class TestTheActiveWorkClock(LeadCase):
    """DESIGN-L04 section 7.1. Active seconds is the sum over consecutive
    attribution timestamps of min(gap, ACTIVE_GAP_CEILING_SECONDS), and
    `now` is NOT appended to the series."""

    def _tick(self, seconds, n=1):
        for _ in range(n):
            self.clock.advance(seconds)
            self.store.record_insight("p1", _insight(), self.actor)

    def test_a_dense_series_reaches_thirty_active_minutes(self):
        start = self.clock.iso()
        self.seed_project()
        self._tick(30, n=60)
        stats = self.store.active_minutes_since("p1", start,
                                                now=self.clock.iso())
        self.assertGreaterEqual(stats["active_minutes"],
                                bs.BRIEFING_ACTIVE_MINUTES)

    def test_a_sparse_series_does_not(self):
        start = self.clock.iso()
        self.seed_project()
        self._tick(3600, n=3)
        stats = self.store.active_minutes_since("p1", start,
                                                now=self.clock.iso())
        self.assertLess(stats["active_minutes"], bs.BRIEFING_ACTIVE_MINUTES)
        self.assertEqual(
            (bs.ACTIVE_GAP_CEILING_SECONDS * 3) // 60,
            stats["active_minutes"],
            "three events after an exclusive `since` are three gaps, and "
            "each one accrues exactly the ceiling, never the whole hour")

    def test_an_open_ended_idle_tail_accrues_nothing(self):
        start = self.clock.iso()
        self.seed_project()
        self._tick(60, n=5)
        busy = self.store.active_minutes_since("p1", start,
                                               now=self.clock.iso())
        idle_now = self.clock.advance(7200)
        idle = self.store.active_minutes_since("p1", start, now=idle_now)
        self.assertEqual(
            busy["active_minutes"], idle["active_minutes"],
            "a session that stops working stops accruing immediately")

    def test_an_unparseable_timestamp_is_skipped_and_counted(self):
        start = self.clock.iso()
        self.seed_project()
        self._tick(60, n=2)
        bs.now_iso = lambda: "not-a-timestamp"
        try:
            self.store.record_insight("p1", _insight(), self.actor)
        finally:
            bs.now_iso = self.clock.iso
        self.clock.advance(60)
        self._tick(60, n=1)
        stats = self.store.active_minutes_since("p1", start,
                                                now=self.clock.iso())
        self.assertGreaterEqual(
            stats["skipped"], 1,
            "a row whose timestamp does not parse is skipped and counted, "
            "never guessed at")

    def test_zero_events_gives_zero(self):
        start = self.clock.iso()
        self.seed_project()
        later = self.clock.advance(7200)
        stats = self.store.active_minutes_since("p1", start, now=later)
        # The project row itself writes one attribution event.
        self.assertLessEqual(stats["active_minutes"],
                             bs.ACTIVE_GAP_CEILING_SECONDS // 60)

    def test_the_ceiling_is_honoured_exactly_at_the_boundary(self):
        start = self.clock.iso()
        self.seed_project()
        self._tick(bs.ACTIVE_GAP_CEILING_SECONDS, n=1)
        at_ceiling = self.store.active_minutes_since("p1", start,
                                                     now=self.clock.iso())
        self.clock.advance(0)
        self.assertGreaterEqual(at_ceiling["active_minutes"], 1)
        start2 = self.clock.iso()
        self._tick(bs.ACTIVE_GAP_CEILING_SECONDS + 600, n=1)
        over = self.store.active_minutes_since("p1", start2,
                                               now=self.clock.iso())
        self.assertEqual(bs.ACTIVE_GAP_CEILING_SECONDS // 60,
                         over["active_minutes"],
                         "a gap above the ceiling accrues the ceiling")


# ---------------------------------------------------------------------------
# 17.2 what triggers a catch-up
# ---------------------------------------------------------------------------

class TestBriefingDue(LeadCase):

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()

    def _due(self):
        return bl.briefing_due(self.store, "p1", self.clock.iso())

    def test_active_minutes_does_not_fire_before_the_threshold(self):
        for _ in range(5):
            self.clock.advance(30)
            self.store.record_insight("p1", _insight(), self.actor)
        due, trigger, _stats, _prev = self._due()
        self.assertFalse(due, "trigger %r fired early" % trigger)

    def test_active_minutes_fires_at_the_threshold(self):
        for _ in range(70):
            self.clock.advance(30)
            self.store.record_insight("p1", _insight(), self.actor)
        due, trigger, stats, _prev = self._due()
        self.assertTrue(due)
        self.assertEqual("ACTIVE_MINUTES", trigger)
        self.assertGreaterEqual(stats["active_minutes"],
                                bs.BRIEFING_ACTIVE_MINUTES)

    def test_phase_boundary_fires_when_a_run_appears(self):
        self.sign()
        self.open_run()
        due, trigger, _stats, _prev = self._due()
        self.assertTrue(due)
        self.assertEqual("PHASE_BOUNDARY", trigger)

    def test_phase_boundary_fires_when_the_open_step_count_moves(self):
        self.sign()
        self.store.record_briefing(
            "p1", {"trigger": "REQUESTED", "where_we_are": "getting going",
                   "run_state": "", "open_steps": 0}, self.actor)
        self.clock.advance(60)
        self.store.queue_human_step(
            "p1", "publish-release", "release", "click publish",
            "Settings then Publish", [], "sess-lead", self.actor)
        due, trigger, _stats, _prev = self._due()
        self.assertTrue(due)
        self.assertEqual("PHASE_BOUNDARY", trigger)

    def test_a_boundary_does_not_fire_twice(self):
        self.sign()
        self.open_run()
        due, trigger, stats, prev = self._due()
        self.assertTrue(due)
        bl._emit_briefing(self.store, "p1", trigger, stats, prev, self.actor)
        self.clock.advance(60)
        due_again, trigger_again, _s, _p = self._due()
        self.assertFalse(
            due_again,
            "the boundary was already recorded, so %r must not fire again"
            % trigger_again)


class TestOneBriefingPerDueWindow(LeadCase):

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()
        self.sign()
        self.open_run()

    def test_two_ticks_back_to_back_write_exactly_one_row(self):
        before = len(self.store.list_briefings("p1", raw=True))
        code, first, _e = self.run_cli("watchdog", "--tick", "--project-id",
                                       "p1")
        self.assertEqual(0, code, first)
        code, second, _e = self.run_cli("watchdog", "--tick", "--project-id",
                                        "p1")
        self.assertEqual(0, code, second)
        after = len(self.store.list_briefings("p1", raw=True))
        self.assertEqual(before + 1, after,
                         "two ticks in one due window wrote %d row(s)"
                         % (after - before))
        self.assertTrue(first.strip(), "the due tick printed nothing")
        self.assertEqual("", second.strip(),
                         "the second tick was not due and must print "
                         "nothing")

    def test_a_second_caller_holding_a_stale_previous_writes_nothing(self):
        due, trigger, stats, prev = bl.briefing_due(self.store, "p1",
                                                    self.clock.iso())
        self.assertTrue(due)
        first = bl._emit_briefing(self.store, "p1", trigger, stats, prev,
                                  self.actor)
        self.assertIsNotNone(first)
        count = len(self.store.list_briefings("p1", raw=True))
        second = bl._emit_briefing(self.store, "p1", trigger, stats, prev,
                                   self.actor)
        self.assertIsNone(
            second,
            "_emit_briefing re-reads the latest row before writing, so a "
            "caller whose `previous` has been overtaken writes nothing")
        self.assertEqual(count, len(self.store.list_briefings("p1",
                                                              raw=True)))


class TestQuietStretchWritesNothing(LeadCase):
    """DESIGN-L04 section 7.5, all three cases. The system does not
    manufacture a catch-up to look busy; it names the one that still
    stands."""

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()

    def test_a_watchdog_tick_in_a_quiet_stretch_writes_and_prints_nothing(self):
        self.store.record_briefing(
            "p1", {"trigger": "REQUESTED",
                   "where_we_are": "the booking form is being wired",
                   "run_state": "", "open_steps": 0}, self.actor)
        before = len(self.store.list_briefings("p1", raw=True))
        self.clock.advance(7200)
        code, out, err = self.run_cli("watchdog", "--tick", "--project-id",
                                      "p1")
        self.assertEqual(0, code, out + err)
        self.assertEqual("", out.strip(), "a quiet tick printed %r" % out)
        self.assertEqual(before, len(self.store.list_briefings("p1",
                                                               raw=True)))

    def test_brief_names_the_catch_up_that_still_stands_and_writes_no_row(self):
        self.store.record_briefing(
            "p1", {"trigger": "REQUESTED",
                   "where_we_are": "the booking form is being wired",
                   "run_state": "", "open_steps": 0}, self.actor)
        before = self.store.list_briefings("p1", raw=True)
        self.clock.advance(7200)
        code, out, err = self.run_cli("brief", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        after = self.store.list_briefings("p1", raw=True)
        self.assertEqual(len(before), len(after),
                         "a requested catch-up with nothing to say wrote a "
                         "row anyway")
        self.assertIn("the booking form is being wired", out)
        self.assertIn(bl.HANDBACK_OPTION_TEXT, out)
        self.assertIn("Next step:", out)

    def test_with_no_catch_up_ever_it_says_so_and_names_what_would_make_one(self):
        code, out, err = self.run_cli("brief", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        self.assertEqual([], self.store.list_briefings("p1", raw=True))
        self.assertIn("no catch-up has been recorded yet", out)
        self.assertIn(str(bs.BRIEFING_ACTIVE_MINUTES), out,
                      "it must name what would produce one, and the "
                      "threshold is a module constant rather than a word "
                      "somebody typed")


# ---------------------------------------------------------------------------
# 17.2 the handback
# ---------------------------------------------------------------------------

class HandbackCase(LeadCase):

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()
        self.sign()
        self.record_row, self.run = self.open_run()
        self.decision = self.record(_decision())

    def do_handback(self, decision_id=None, why="I will take this one"):
        return self.run_cli("handback", "--project-id", "p1",
                            "--decision-id", decision_id
                            or self.decision["insight_id"],
                            "--why", why, "--actor-name", "coordinator")


class TestHandbackTakesFiveActsInOrder(HandbackCase):
    """DESIGN-L04 section 9.4. The order is load-bearing: pausing the
    authorisation FIRST is the act that makes further autonomous work
    impossible, and it is what stops a half-failed handback becoming a
    running robot."""

    def test_the_authorisation_is_paused(self):
        before = self.store.latest_contract("p1", raw=True)
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        after = self.store.latest_contract("p1", raw=True)
        self.assertEqual("paused", after["state"])
        self.assertEqual(before["revision"] + 1, after["revision"])

    def test_the_evidence_block_lands_in_the_checkpoint_body(self):
        """THE ACT 2 TRAP. Store.transition keeps the passed `evidence`
        only when moving to 'complete', so evidence passed on a park is
        silently discarded. This plants a marker and asserts which row
        holds it."""
        code, out, err = self.do_handback(why="MARKER-SEVEN-TWO-NINE")
        self.assertEqual(0, code, out + err)
        payload = self.store.handover_payload(
            self.record_row.lifecycle_uuid)
        self.assertIsNotNone(payload["digest"],
                             "act two wrote no checkpoint at all")
        self.assertIn("MARKER-SEVEN-TWO-NINE", payload["digest"]["body"],
                      "the evidence block must land in the checkpoint body")
        record = self.store.get(self.record_row.lifecycle_uuid)
        self.assertNotIn("MARKER-SEVEN-TWO-NINE", record.evidence or "",
                         "evidence passed on a park is silently discarded, "
                         "so nothing may be entrusted to it")

    def test_the_work_record_is_parked(self):
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        record = self.store.get(self.record_row.lifecycle_uuid)
        self.assertEqual("parked", record.state)

    def test_one_handback_row_supersedes_the_decision(self):
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        rows = self.store.list_insights("p1", kind="HANDBACK", raw=True)
        self.assertEqual(1, len(rows))
        self.assertEqual(self.decision["insight_id"], rows[0]["supersedes"])
        self.assertEqual(1, rows[0]["control_taken"])
        self.assertTrue(rows[0]["claim"].strip(),
                        "the handback row must record what the system would "
                        "have chosen")
        self.assertEqual(
            [], [d for d in self.store.open_key_decisions("p1", raw=True)
                 if d["insight_id"] == self.decision["insight_id"]],
            "the decision must leave the open queue")

    def test_the_developer_brief_is_written(self):
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        rows = self.store.list_insights("p1", kind="HANDBACK", raw=True)
        path = os.path.join(self.root, bl.HANDOVER_ROOT,
                            "HANDBACK-%s.md" % rows[0]["insight_id"][:8])
        self.assertTrue(os.path.isfile(path),
                        "the developer brief was not written to %s" % path)

    def test_a_failure_after_act_one_leaves_the_authorisation_paused(self):
        """The error path. If any act after Act 1 fails, the command reports
        an error card and states plainly that the authorisation is paused
        and the remaining acts did not run. It does NOT un-pause: reversing
        a safety act on an error path is how a half-failed handback becomes
        a running robot."""
        real = bl.bs.Store.transition

        def boom(*a, **kw):
            raise bl.bs.OwnershipRefused("injected", "act three refused")

        bl.bs.Store.transition = boom
        try:
            code, out, err = self.do_handback()
        finally:
            bl.bs.Store.transition = real
        self.assertNotEqual(0, code, out + err)
        text = out + err
        for section in ("What happened", "Impact", "Recommended action",
                        "What remains safe"):
            self.assertIn(section, text,
                          "the error card needs all four sections of "
                          "references/kickoff.md, in order:\n%s" % text)
        self.assertEqual("paused",
                         self.store.latest_contract("p1", raw=True)["state"])


class TestHandbackIsIdempotentlyRefused(HandbackCase):

    def test_a_second_handback_on_one_decision_is_refused(self):
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        code, out, err = self.do_handback()
        self.assertNotEqual(0, code)
        self.assertIn("handback-already-taken", out + err)

    def test_a_handback_naming_another_projects_decision_is_refused(self):
        self.store.upsert_project(_project_dict("p2", name="other",
                                                goal="another outcome",
                                                now=self.clock.iso()),
                                  self.actor)
        other = self.store.record_insight("p2", _decision(), self.actor)
        code, out, err = self.do_handback(decision_id=other["insight_id"])
        self.assertNotEqual(0, code)
        self.assertIn("foreign-insight", out + err)


class TestTheDeveloperBriefIsComplete(HandbackCase):

    def test_all_eight_sections_are_present(self):
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        rows = self.store.list_insights("p1", kind="HANDBACK", raw=True)
        text = _read_text(os.path.join(
            self.root, bl.HANDOVER_ROOT,
            "HANDBACK-%s.md" % rows[0]["insight_id"][:8]))
        for heading in bl.DEVELOPER_BRIEF_SECTIONS:
            self.assertIn(heading, text,
                          "the developer brief is missing %r:\n%s"
                          % (heading, text))

    def test_work_in_flight_is_listed_with_its_age(self):
        dispatch_id = self.plan_one_unit(self.run)
        self.clock.advance(600)
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        rows = self.store.list_insights("p1", kind="HANDBACK", raw=True)
        text = _read_text(os.path.join(
            self.root, bl.HANDOVER_ROOT,
            "HANDBACK-%s.md" % rows[0]["insight_id"][:8]))
        self.assertIn(dispatch_id, text)
        self.assertIn("10 minute", text,
                      "an open dispatch is listed with its age")

    def test_the_standalone_brief_and_the_pack_section_are_the_same_bytes(self):
        code, out, err = self.do_handback()
        self.assertEqual(0, code, out + err)
        code, out, err = self.run_cli("handover-pack", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        rows = self.store.list_insights("p1", kind="HANDBACK", raw=True)
        brief = _read_text(os.path.join(
            self.root, bl.HANDOVER_ROOT,
            "HANDBACK-%s.md" % rows[0]["insight_id"][:8]))
        page = _read_text(os.path.join(self.root, bl.HANDOVER_ROOT,
                                       "60-HANDBACKS.md"))
        body = brief.strip()
        self.assertIn(
            body, page,
            "the standalone brief and the 60-HANDBACKS.md section must be "
            "the same bytes, so the pack and the brief cannot tell a "
            "developer two different stories")


# ---------------------------------------------------------------------------
# 17.2 the handover pack
# ---------------------------------------------------------------------------

_TRACE_RE = re.compile(r"\[([ib]):([0-9a-fA-F]+)\]")


class TestHandoverPackTracesToRows(LeadCase):
    """DESIGN-L04 section 11.3, both directions. The strongest shipped
    precedent is test_every_register_entry_reaches_the_page in
    tools/test_bm_docs.py, whose own docstring states the principle this
    copies: the render could agree with itself and still drop a row."""

    def setUp(self):
        LeadCase.setUp(self)
        self.seed_project()
        self.sign()
        self.add_forecast()
        self.decision = self.record(_decision())
        self.record(_insight(kind="RISK", subject="the refund path",
                             claim="the refund path is untested",
                             evidence_class="REASONED", confidence="low",
                             flip_condition="run the refund test"))
        self.record(_insight(kind="CALIBRATION", subject="the booking test",
                             claim="the booking test catches a broken form",
                             evidence_class="EXECUTED",
                             evidence="python3 -m pytest test_booking.py",
                             mutation="removed the required attribute",
                             observed="1 failure"))
        self.record(_insight(kind="LEARNING", subject="the form",
                             claim="the form needs a second confirmation",
                             evidence_class="READ", evidence="form.py:40"))
        self.store.record_briefing(
            "p1", {"trigger": "REQUESTED",
                   "where_we_are": "the booking form is being wired"},
            self.actor)
        self.clock.advance(60)
        self.store.record_insight(
            "p1", _insight(kind="HANDBACK", subject="the payment approach",
                           claim="it would have used hosted checkout",
                           supersedes=self.decision["insight_id"],
                           control_offered=1, control_taken=1,
                           evidence_class="REASONED", confidence="low"),
            self.actor)

    def _write(self):
        code, out, err = self.run_cli("handover-pack", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        return os.path.join(self.root, bl.HANDOVER_ROOT)

    def _tags(self, path):
        return set(_TRACE_RE.findall(_read_text(path)))

    def _expected(self, rel):
        insights = self.store.list_insights("p1", raw=True)
        briefings = self.store.list_briefings("p1", raw=True)
        superseded = {i["supersedes"] for i in insights if i["supersedes"]}
        if rel == "00-SITUATION.md":
            return set()
        if rel == "10-DECISIONS.md":
            return {("i", i["insight_id"]) for i in insights
                    if i["kind"] == "DECISION"}
        if rel == "20-RISKS.md":
            return {("i", i["insight_id"]) for i in insights
                    if i["kind"] == "RISK"
                    and i["insight_id"] not in superseded}
        if rel == "30-CALIBRATIONS.md":
            return {("i", i["insight_id"]) for i in insights
                    if i["kind"] == "CALIBRATION"}
        if rel == "40-LEARNINGS.md":
            return {("i", i["insight_id"]) for i in insights
                    if i["kind"] == "LEARNING"}
        if rel == "50-TIMELINE.md":
            return {("b", b["briefing_id"]) for b in briefings}
        if rel == "60-HANDBACKS.md":
            hb = [i for i in insights if i["kind"] == "HANDBACK"]
            out = {("i", i["insight_id"]) for i in hb}
            out |= {("i", i["supersedes"]) for i in hb if i["supersedes"]}
            # Section 10 item 6: the reproduction section names the newest
            # record whose evidence was actually run, so a developer has a
            # command rather than a description of one. That record is on
            # the page by design, so it is in the expected set by design.
            executed = [i for i in insights
                        if i["evidence_class"] in ("EXECUTED", "MEASURED")]
            if hb and executed:
                out.add(("i", executed[0]["insight_id"]))
            return out
        raise AssertionError("unknown page %r" % rel)

    def test_forward_nothing_is_dropped(self):
        folder = self._write()
        for rel, _renderer, _what in bl.PACK_PAGES:
            path = os.path.join(folder, rel)
            self.assertTrue(os.path.isfile(path), "%s was not written" % rel)
            expected = self._expected(rel)
            found = self._tags(path)
            missing = sorted(expected - found)
            self.assertEqual(
                [], missing,
                "%s dropped row(s) the records say belong on it: %s"
                % (rel, missing))

    def test_backward_nothing_is_invented(self):
        folder = self._write()
        for rel, _renderer, _what in bl.PACK_PAGES:
            found = self._tags(os.path.join(folder, rel))
            extra = sorted(found - self._expected(rel))
            self.assertEqual(
                [], extra,
                "%s carries trace tag(s) no row of that kind holds: %s"
                % (rel, extra))
            for kind, row_id in found:
                if kind == "i":
                    self.assertIsNotNone(
                        self.store.get_insight(row_id, raw=True),
                        "%s names insight %s, which no row holds"
                        % (rel, row_id))

    def test_every_page_names_all_seven_and_the_tuple_is_the_inventory(self):
        folder = self._write()
        self.assertEqual(7, len(bl.PACK_PAGES))
        on_disk = sorted(n for n in os.listdir(folder) if n.endswith(".md"))
        self.assertEqual(sorted(rel for rel, _r, _w in bl.PACK_PAGES),
                         on_disk)

    def test_a_reasoned_line_is_never_bare_on_any_page(self):
        folder = self._write()
        prefix = bl.evidence_label("REASONED")
        for rel, _renderer, _what in bl.PACK_PAGES:
            for line in _read_text(os.path.join(folder, rel)).splitlines():
                for kind, row_id in _TRACE_RE.findall(line):
                    if kind != "i":
                        continue
                    row = self.store.get_insight(row_id, raw=True)
                    if row and row["evidence_class"] == "REASONED":
                        self.assertIn(
                            prefix, line,
                            "%s carries a REASONED claim with no prefix: %r"
                            % (rel, line))

    def test_generating_twice_produces_identical_bytes(self):
        folder = self._write()
        first = {rel: _read_text(os.path.join(folder, rel))
                 for rel, _r, _w in bl.PACK_PAGES}
        self.clock.advance(600)
        self._write()
        for rel, text in first.items():
            self.assertEqual(
                text, _read_text(os.path.join(folder, rel)),
                "%s is not byte stable across two generations with no "
                "intervening write" % rel)

    def test_the_cut_holds_for_an_older_developer_brief(self):
        handback = self.store.list_insights("p1", kind="HANDBACK",
                                            raw=True)[0]
        first = bl.render_developer_brief(self.store, handback)
        self.clock.advance(600)
        self.record(_insight(kind="LEARNING", subject="a later thing",
                             claim="something happened afterwards"))
        second = bl.render_developer_brief(self.store, handback)
        self.assertEqual(
            first, second,
            "a brief written in August must still reproduce in September: "
            "every renderer takes a cut at the handback's created_at")


# ---------------------------------------------------------------------------
# 17.2 the no-SQL guard
# ---------------------------------------------------------------------------

class TestNoSQLGuard(unittest.TestCase):
    """tools/bm_lead.py is a THIN CLI over tools/bm_store.py's service
    methods and read accessors: the moment it needs a query the store does
    not already offer, it has become a second writer and must be folded back
    into bm_store.py. Shape copied from tools/test_bm_controller.py."""

    _SQL_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|PRAGMA)\b")

    def test_bm_lead_never_issues_its_own_sql(self):
        self.assertTrue(os.path.isfile(LEAD_FILE),
                        "tools/bm_lead.py does not exist yet")
        tree = _lead_tree()
        hits = [(line, value) for line, value, _fn in _string_constants(tree)
                if self._SQL_RE.search(value)]
        self.assertEqual(
            [], hits,
            "tools/bm_lead.py must contain no SQL-shaped string literal: %s"
            % hits)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertNotIn("sqlite3", imports,
                         "tools/bm_lead.py must not import sqlite3")


# ---------------------------------------------------------------------------
# 12. the seven conversation shapes, as behavioral fixtures
# ---------------------------------------------------------------------------

def _write_fixture(name, payload):
    """Every shape writes its transcript, and every value in it is read back
    out of the live records the test just drove, which is what makes it
    evidence rather than a fixture.

    WHERE it writes changed on 2026-08-16, and the gate is what forced it.
    This used to write straight into the tracked evidence directory on
    every run, so merely RUNNING the suite left two tracked files modified.
    The gate then refused to report green, correctly, because a suite that
    writes into the checkout invalidates CHECKSUMS.sha256, which makes
    verify-install tell a user their install may be tampered with and makes
    doctor SKIP its integrity check. That refusal was read as somebody
    else's mess for most of a night before a run in a pristine clone proved
    it was this line.

    So the default is now a temporary directory, and the tracked path is
    an OPT IN, exactly the shape tools/test_bm_controller.py already uses
    for its E4 artifact (BM_WRITE_E4_EVIDENCE) and exactly what the gate's
    own refusal message tells you to do. Publishing a run as evidence is a
    deliberate act; running the suite is not."""
    if os.environ.get("BM_WRITE_L04_EVIDENCE") == "1":
        directory = EVIDENCE_DIR
    else:
        directory = tempfile.mkdtemp(prefix="bm-l04-evidence-")
    path = os.path.join(directory, name + ".json")
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


class TestSevenConversationShapes(LeadCase):
    """DESIGN-L04 section 12. Three of the seven are mandated by the founder
    design's own fixture list: a run that offers the handback and is refused
    (S5), one where it is taken (S4), and one where the ledger contradicts
    the records (S7)."""

    def test_s1_cold_start(self):
        code, before, _e = self.run_cli("status", "--project-id", "p1")
        code, set_out, err = self.run_cli(
            "outcome", "--project-id", "p1", "--set",
            "a booking site that takes money", "--actor-name", "coordinator")
        self.assertEqual(0, code, set_out + err)
        rows = self.store.list_projects(raw=True)
        self.assertEqual(1, len(rows), "outcome --set wrote %d project row(s)"
                         % len(rows))
        code, after, _e = self.run_cli("outcome", "--project-id", "p1")
        self.assertEqual(0, code, after)
        self.assertEqual(
            1, len([ln for ln in after.splitlines()
                    if ln.startswith("Next step:")]))
        terms = _terminology_terms()
        leaked = sorted({t for t in terms
                         if re.search(r"\b%s\b" % re.escape(t),
                                      set_out + after, re.IGNORECASE)})
        self.assertEqual([], leaked, "cold start leaked %s" % leaked)
        self.assertEqual("agree-authorisation",
                         bl._next_action_branch(self.store, "p1")[0])
        _write_fixture("S1-COLD_START", {
            "shape": "a founder with no project says what they want",
            "branch_before_outcome": "state-outcome",
            "branch_after_outcome": bl._next_action_branch(self.store,
                                                           "p1")[0],
            "project_rows": len(rows),
            "machinery_terms_leaked": leaked,
            "outcome_transcript": after.splitlines()})

    def test_s2_status_mid_run(self):
        self.seed_project()
        self.sign()
        self.add_forecast()
        self.open_run()
        _c, plain, _e = self.run_cli("status", "--project-id", "p1")
        _c, advanced, _e = self.run_cli("status", "--project-id", "p1",
                                        "--advanced")
        _c, back, _e = self.run_cli("status", "--project-id", "p1")
        _c, engineer, _e = self.run_cli("status", "--project-id", "p1",
                                        "--ic")
        self.assertEqual(EIGHT_FIELDS, _field_labels(plain))
        for item in NINE_ADVANCED:
            self.assertNotIn(item, plain)
            self.assertIn(item, advanced)
            self.assertNotIn(item, back)
        for item in NINE_ADVANCED:
            self.assertNotIn(item, engineer)
        f_values, e_values = _field_values(plain), _field_values(engineer)
        for label in EIGHT_FIELDS:
            self.assertEqual(f_values[label], e_values[label])
        _write_fixture("S2-STATUS_MID_RUN", {
            "shape": "the founder asks where things stand while work is in "
                     "flight",
            "fields": _field_labels(plain),
            "advanced_items_in_default_view": [i for i in NINE_ADVANCED
                                               if i in plain],
            "advanced_is_sticky": any(i in back for i in NINE_ADVANCED),
            "shared_field_values_identical": True,
            "founder_transcript": plain.splitlines()})

    def test_s3_decision_required(self):
        self.seed_project()
        self.sign()
        ids = {}
        for cls in bs.INSIGHT_DECISION_CLASSES:
            self.clock.advance(30)
            row = self.record(_decision(subject="subject %s" % cls,
                                        claim="claim %s" % cls,
                                        decision_class=cls))
            ids[cls] = row["insight_id"]
        code, out, err = self.run_cli("decisions", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        order = [cls for cls in bs.INSIGHT_DECISION_CLASSES
                 if "subject %s" % cls in out]
        self.assertEqual(
            list(bl.DECISION_STAKES), order,
            "the queue is ordered by DECISION_STAKES, highest stakes first")
        cards = out.split("Decision needed:")
        self.assertEqual(len(bs.INSIGHT_DECISION_CLASSES), len(cards) - 1)
        for card in cards[1:]:
            option_lines = [ln.strip() for ln in card.splitlines()
                            if ln.strip().startswith("[")
                            or ln.strip() == bl.HANDBACK_OPTION_TEXT]
            self.assertGreaterEqual(len(option_lines), 2)
            self.assertLessEqual(len(option_lines), 4)
            self.assertEqual(bl.HANDBACK_OPTION_TEXT, option_lines[-1])
            self.assertIn("Recommended:", card)
            self.assertIn("Why:", card)
            self.assertIn("Tradeoff:", card)
        self.assertIn("declared by the coordinator, not detected from the "
                      "records", out)
        _write_fixture("S3-DECISION_REQUIRED", {
            "shape": "a key decision is open",
            "queue_order": order,
            "stakes_order": list(bl.DECISION_STAKES),
            "every_card_ends_in_the_handback_option": True,
            "transcript": out.splitlines()})

    def test_s4_handback_taken(self):
        self.seed_project()
        self.sign()
        record, run = self.open_run()
        decision = self.record(_decision())
        before = self.store.latest_contract("p1", raw=True)
        code, out, err = self.run_cli(
            "handback", "--project-id", "p1", "--decision-id",
            decision["insight_id"], "--why", "I will take this one",
            "--actor-name", "coordinator")
        self.assertEqual(0, code, out + err)
        after = self.store.latest_contract("p1", raw=True)
        self.assertEqual("paused", after["state"])
        self.assertEqual(before["revision"] + 1, after["revision"])
        digest = self.store.handover_payload(
            record.lifecycle_uuid)["digest"]
        self.assertIsNotNone(digest)
        self.assertTrue(digest["next_intent"].strip())
        self.assertEqual("parked",
                         self.store.get(record.lifecycle_uuid).state)
        rows = self.store.list_insights("p1", kind="HANDBACK", raw=True)
        self.assertEqual(1, len(rows))
        brief_path = os.path.join(
            self.root, bl.HANDOVER_ROOT,
            "HANDBACK-%s.md" % rows[0]["insight_id"][:8])
        text = _read_text(brief_path)
        for heading in bl.DEVELOPER_BRIEF_SECTIONS:
            self.assertIn(heading, text)
        code, out2, err2 = self.run_cli(
            "handback", "--project-id", "p1", "--decision-id",
            decision["insight_id"], "--why", "again",
            "--actor-name", "coordinator")
        self.assertNotEqual(0, code)
        self.assertIn("handback-already-taken", out2 + err2)
        self.assertEqual(
            [], [d for d in self.store.open_key_decisions("p1", raw=True)
                 if d["insight_id"] == decision["insight_id"]])
        _write_fixture("S4-HANDBACK_TAKEN", {
            "shape": "the founder hands control back",
            "contract_state": after["state"],
            "revision_moved_by": after["revision"] - before["revision"],
            "record_state": self.store.get(
                record.lifecycle_uuid).state,
            "handback_rows": len(rows),
            "developer_brief_sections": list(bl.DEVELOPER_BRIEF_SECTIONS),
            "second_handback_refused_with": "handback-already-taken",
            "decision_still_open": False})

    def test_s5_handback_refused(self):
        self.seed_project()
        self.sign()
        self.store.create_task({"task_id": "t1", "project_id": "p1",
                                "title": "wire the booking form",
                                "status": "ready", "priority": "high"},
                               self.actor)
        decision = self.record(_decision())
        raw_before = self.store.get_insight(decision["insight_id"], raw=True)
        self.clock.advance(60)
        code, out, err = self.run_cli(
            "insight", "--project-id", "p1", "--kind", "DECISION",
            "--subject", "the payment approach",
            "--claim", "hosted checkout it is",
            "--evidence-class", "REASONED", "--confidence", "moderate",
            "--flip-condition", "a measured refusal rate above 2 percent",
            "--supersedes", decision["insight_id"],
            "--actor-name", "coordinator")
        self.assertEqual(0, code, out + err)
        self.assertEqual([], self.store.list_insights("p1", kind="HANDBACK",
                                                      raw=True))
        raw_after = self.store.get_insight(decision["insight_id"], raw=True)
        self.assertEqual(raw_before, raw_after,
                         "the answered decision row must not be edited")
        self.assertEqual(1, raw_after["control_offered"])
        self.assertEqual("live",
                         self.store.latest_contract("p1", raw=True)["state"])
        self.assertEqual(
            [], [d for d in self.store.open_key_decisions("p1", raw=True)
                 if d["insight_id"] == decision["insight_id"]])
        branch = bl._next_action_branch(self.store, "p1")[0]
        self.assertNotIn(branch, ("answer-decision", "answer-floor-decision"))
        _write_fixture("S5-HANDBACK_REFUSED", {
            "shape": "the founder picks the recommended option instead",
            "handback_rows": 0,
            "original_row_unchanged": True,
            "control_offered_still": raw_after["control_offered"],
            "contract_state": "live",
            "decision_still_open": False,
            "next_action_branch": branch})

    def test_s6_quiet_stretch(self):
        self.seed_project()
        self.store.record_briefing(
            "p1", {"trigger": "REQUESTED",
                   "where_we_are": "the booking form is being wired",
                   "run_state": "", "open_steps": 0}, self.actor)
        before = len(self.store.list_briefings("p1", raw=True))
        self.clock.advance(7200)
        code, tick, _e = self.run_cli("watchdog", "--tick", "--project-id",
                                      "p1")
        self.assertEqual(0, code, tick)
        self.assertEqual("", tick.strip())
        due, _trigger, stats, _prev = bl.briefing_due(self.store, "p1",
                                                      self.clock.iso())
        self.assertFalse(due)
        self.assertLess(stats["active_minutes"], bs.BRIEFING_ACTIVE_MINUTES)
        code, brief, _e = self.run_cli("brief", "--project-id", "p1")
        self.assertEqual(0, code, brief)
        after = len(self.store.list_briefings("p1", raw=True))
        self.assertEqual(before, after)
        self.assertIn("the booking form is being wired", brief)
        _write_fixture("S6-QUIET_STRETCH", {
            "shape": "two hours pass with nothing happening",
            "watchdog_printed": tick.strip(),
            "rows_before": before, "rows_after": after,
            "active_minutes": stats["active_minutes"],
            "threshold": bs.BRIEFING_ACTIVE_MINUTES,
            "brief_transcript": brief.splitlines()})

    def test_s7_bad_news(self):
        self.seed_project(goal="a booking site that takes money")
        self.sign()
        # The claim that disagrees with the records is itself only reasoned
        # about, which is the honest shape of this situation and is what
        # makes the REASONED assertion below real rather than planted: the
        # same status output has to carry both the record's own value and a
        # reasoned claim wearing its prefix.
        wrong = self.record(_insight(
            kind="RISK", subject="the outcome",
            claim="the outcome may have drifted to a marketing page",
            evidence_class="REASONED", confidence="low",
            flip_condition="read the project record"))
        self.clock.advance(60)
        code, out, err = self.run_cli(
            "insight", "--project-id", "p1", "--kind", "RISK",
            "--subject", "the outcome",
            "--claim", "an earlier note disagrees with the recorded outcome",
            "--evidence-class", "READ",
            "--evidence", "the project record says a booking site that "
                          "takes money",
            "--confidence", "high",
            "--flip-condition", "the founder restates the outcome",
            "--report-card", "--actor-name", "coordinator")
        self.assertEqual(0, code, out + err)
        order = ["What happened", "Impact", "Recommended action",
                 "What remains safe"]
        positions = []
        for section in order:
            self.assertIn(section, out)
            positions.append(out.index(section))
        self.assertEqual(sorted(positions), positions,
                         "the four sections must appear in that order, "
                         "impact before technical cause:\n%s" % out)
        _c, status_out, _e = self.run_cli("status", "--project-id", "p1")
        values = _field_values(status_out)
        self.assertIn("a booking site that takes money", values["Goal"],
                      "the records win: status prints the project record's "
                      "own outcome, not the contradicting note")
        self.assertNotIn("marketing page", values["Goal"])
        self.assertIn(bl.evidence_label("REASONED"), status_out,
                      "a reasoned claim in the same output carries its "
                      "prefix:\n%s" % status_out)
        still = self.store.get_insight(wrong["insight_id"], raw=True)
        self.assertIsNotNone(still,
                             "the contradicted note is neither edited nor "
                             "deleted")
        risks = self.store.list_insights("p1", kind="RISK", raw=True)
        self.assertEqual(2, len(risks))
        _write_fixture("S7-BAD_NEWS", {
            "shape": "an insight's claim disagrees with a record, and the "
                     "founder must be told",
            "records_win": True,
            "risk_rows": len(risks),
            "contradicted_row_survives": True,
            "error_card_sections": ["What happened", "Impact",
                                    "Recommended action",
                                    "What remains safe"],
            "report_transcript": out.splitlines()})


if __name__ == "__main__":
    unittest.main(verbosity=1)
