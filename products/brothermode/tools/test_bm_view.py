#!/usr/bin/env python3
"""Suite for tools/bm_view.py: the live project view, its CLI, the consent
door, the empty states, the handback panel and the developer brief page
(design docs/program/absolute-lead/DESIGN-visual-surface.md sections 3, 4,
8, 9.3, 11.4, 12.2 and 13.2).

HOW A GENERATED PAGE IS TESTED HERE (design section 13.0):

  1. Parse, do not grep. One small html.parser subclass, Doc, builds a
     tree of (tag, attrs, text, children), and every assertion is a query
     against that tree. An assertIn of a string literal against the raw
     html is banned in this suite and an ast guard below fails on it.
  2. Content assertions are set equalities against rows, in both
     directions: every row the store says belongs in a section reaches
     it, and every trace attribute on the page resolves to a row.
  3. Structural invariants are computed, never eyeballed: disclosure
     depth, the count of elements carrying data-bm-next-step, the absence
     of a script element, the token table coverage.

Every store this file builds comes from bw.bs, the bm_store module
tools/bm_view.py itself loaded through tools/bm_visual.py, so a refusal
raised inside the module and a refusal this file catches are the SAME
class. That is the rule DESIGN-L04 section 17.6 states and
tools/test_bm_lead.py repeats.

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
import sys
import tempfile
import unittest
from html.parser import HTMLParser

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VIEW_FILE = os.path.join(HERE, "bm_view.py")
VISUAL_FILE = os.path.join(HERE, "bm_visual.py")
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")
TERMINOLOGY = os.path.join(ROOT, "references", "terminology.md")
EVIDENCE_DIR = os.path.join(ROOT, "docs", "program", "absolute-lead",
                            "evidence", "L05")


def _load(name):
    """Load a sibling module by PATH, the same technique tools/bm_lead.py,
    tools/bm_visual.py and tools/bm_controller.py use for bm_store."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_optional(name):
    """None instead of an exception when the module is not there yet.

    Deliberate, and copied from tools/test_bm_lead.py: on the untouched
    tree tools/bm_view.py does not exist, and a bare import failure here
    would collapse this whole file into ONE error, which is not per-class
    failing-first evidence."""
    try:
        return _load(name)
    except Exception:  # sbe: allow-silent documented: on the untouched tree the module under test does not exist yet, and each test class must fail on its own assertion, not on collection
        return None


bw = _load_optional("bm_view")
bv = bw.bv if bw is not None else _load_optional("bm_visual")
bs = bw.bs if bw is not None else (bv.bs if bv is not None
                                   else _load("bm_store"))
bl = bw.bl if bw is not None else (bv.bl if bv is not None
                                   else _load_optional("bm_lead"))


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _files_under(root):
    found = []
    for dirpath, _dirs, files in os.walk(root):
        if "Library/Caches" in dirpath:
            continue
        for f in files:
            found.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(found)


def _write_consented_config(path, vault):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump({"setup_complete": True, "vault_path": vault,
                   "privacy_notice_version": "2026-08-01",
                   "installation_mode": "clone",
                   "security_mode": "standard"}, fh)


def _actor(name="coordinator"):
    return {"actor_type": "model", "actor_name": name,
            "session_id": "sess-view"}


def _insight(**kw):
    row = {"kind": "LEARNING", "subject": "the widget",
           "claim": "the widget builds from a clean checkout",
           "evidence": "python3 tools/test_bm_view.py",
           "evidence_class": "EXECUTED", "confidence": "high",
           "confidence_basis": "it is a measurement and it repeats",
           "flip_condition": "the build fails on a clean checkout",
           "alternatives": [{"option": "trust the last build",
                             "why_not": "it ran on a dirty tree"}]}
    row.update(kw)
    return row


def _key_decision(**kw):
    row = _insight(kind="DECISION", subject="the payment approach",
                   claim="use hosted checkout",
                   decision_class="RULE", control_offered=1,
                   evidence_class="READ", evidence="tools/bm_store.py:81",
                   confidence="moderate")
    row.update(kw)
    return row


# The five terminology rows added FOR the visual surface (design section
# 12.3). The page is allowed to carry these words: the design's own header
# example prints "fingerprint <hex>" verbatim (section 4.4), and the row
# range the design bans is references/terminology.md lines 10 to 34 as
# they stood BEFORE these five were added.
#
# "branch" is additionally excluded WITH ITS REASON ON RECORD: the
# terminology row is "worktree, branch", the git workspace concept, and
# that sense never reaches the page. The word on the page is the everyday
# arm of a drawn fork ("The last branch is always yours"), emitted by
# tools/bm_visual.py's own landed caption, which is Writer D's file and
# outside this writer's set. Flagged in the fix report for the
# orchestrator rather than silently narrowed.
_VISUAL_SURFACE_ROWS = ("live project view", "insight box", "alert rung",
                        "empty state", "fingerprint", "branch")


def _terminology_terms():
    """Every internal (left column) term of the beginner terminology map,
    minus the five visual-surface rows above, parsed from the shipped file
    rather than hand listed."""
    terms = []
    for line in _read(TERMINOLOGY).splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3 or cells[1] in ("Internal term", "") \
                or set(cells[1]) <= set("-: "):
            continue
        for term in cells[1].split(","):
            term = term.strip()
            if term and term.lower() not in _VISUAL_SURFACE_ROWS:
                terms.append(term)
    return terms


def _strip_command_tokens(text):
    """Remove command names before the plain-language scan: the command
    /brotherme-handback is a NAME, and terminology.md itself names the
    /brotherme-brief command inside the row that bans the word briefing."""
    text = re.sub(r"/?brotherme-[a-z-]+", " ", text)
    text = re.sub(r"\bbm-[a-z-]+\b", " ", text)
    return text


class _Node(object):
    __slots__ = ("tag", "attrs", "children", "text", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.text = ""
        self.parent = parent


class Doc(HTMLParser):
    """The parse-do-not-grep mechanism of design section 13.0: a tree of
    (tag, attrs, text, children) every assertion queries."""

    VOID = ("meta", "br", "hr", "img", "link", "input")

    def __init__(self, html):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.rootnode = _Node("#root", {}, None)
        self._stack = [self.rootnode]
        self.feed(html)
        HTMLParser.close(self)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in self.VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, attrs, self._stack[-1]))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1].text += data

    def walk(self, node=None):
        node = node or self.rootnode
        yield node
        for child in node.children:
            for grand in self.walk(child):
                yield grand

    def all(self, tag=None, attr=None):
        out = []
        for node in self.walk():
            if tag is not None and node.tag != tag:
                continue
            if attr is not None and attr not in node.attrs:
                continue
            if node is not self.rootnode:
                out.append(node)
        return out

    def by_id(self, ident):
        for node in self.walk():
            if node.attrs.get("id") == ident:
                return node
        return None

    @classmethod
    def text_of(cls, node, skip=("style", "script")):
        if node is None:
            return ""
        if node.tag in skip:
            return ""
        parts = [node.text]
        for child in node.children:
            parts.append(cls.text_of(child, skip=skip))
        return "".join(parts)

    def visible_text(self):
        return self.text_of(self.rootnode)


class ViewCase(unittest.TestCase):
    """A throwaway project root, a consented config, a store, a clock.

    Every subclass inherits the same guard: tools/bm_view.py must exist.
    Until it does, every test in this file fails with that one sentence,
    which is the failing-first evidence design section 17 step 1 asks
    every new class to reproduce."""

    ENV_KEYS = ("BROTHERME_CONFIG", "BROTHERMODE_ROOT", "BROTHERMODE_VIEW",
                "BROTHERMODE_VAULT", "BROTHERMODE_NO_BELL", "HOME")

    def setUp(self):
        self.assertIsNotNone(
            bw, "tools/bm_view.py does not exist yet, so nothing in this "
                "file can run. That is the failing-first state "
                "DESIGN-visual-surface section 17 step 1 asks every new "
                "class to reproduce.")
        self.tmp = tempfile.mkdtemp(prefix="bm-view-")
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
        os.environ.pop("BROTHERMODE_NO_BELL", None)
        os.environ["BROTHERMODE_VAULT"] = self.vault
        os.environ["HOME"] = self.home
        self._patched = []
        self.clock = _Clock()
        for mod in {id(bs): bs, id(bl.bs): bl.bs}.values():
            self._patched.append((mod, mod.now_iso))
            mod.now_iso = self.clock.iso
        self.store = bs.Store(self.root)
        self.actor = _actor()

    def tearDown(self):
        for mod, real in self._patched:
            mod.now_iso = real
        try:
            self.store.close()
        except Exception:  # sbe: allow-silent test teardown; closing an already-broken store handle after the test body ran is best-effort cleanup
            pass
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def unconsent(self):
        """Point the config at a file that does not exist, so the consent
        reader fails CLOSED, exactly as on a machine setup never ran on."""
        os.environ["BROTHERME_CONFIG"] = os.path.join(self.tmp,
                                                      "missing.json")

    # -- driving the CLI in process -------------------------------------

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = bw.main(list(argv))
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        return code, out.getvalue(), err.getvalue()

    # -- seeding --------------------------------------------------------

    def seed(self, pid="p1", goal="a booking page my customers can use"):
        stamp = self.clock.iso()
        self.store.upsert_project(
            {"project_id": pid, "name": "widget", "goal": goal,
             "created_at": stamp, "updated_at": stamp}, self.actor)
        return pid

    def sign(self, pid="p1", token_ceiling=40000, minutes_ceiling=90):
        return self.store.sign_contract(
            pid, "ship the booking page", "the booking test passes",
            ["."], [], ["file-create", "file-edit", "build", "test-run",
                        "read-only-inspect"],
            token_ceiling, minutes_ceiling, "Khalil Maaouni", "sess-view",
            self.actor, supersede=False)

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

    def open_run(self, pid="p1"):
        record = self.store.claim(
            name="controller-%s" % pid, lifetime="persistent",
            files=["widget.py"], objective="carry the outcome",
            owner="coordinator", session_id="sess-view")
        return self.store.open_run(
            pid, "ctrl1", 1, "ship the booking page",
            "the booking test passes", record.lifecycle_uuid,
            "sess-view", self.actor)

    def record(self, payload, pid="p1"):
        self.clock.advance(30)
        return self.store.record_insight(pid, payload, self.actor)

    def brief(self, pid="p1"):
        prev = self.store.latest_briefing(pid, raw=True)
        payload = bl.collect_briefing(self.store, pid, prev)
        self.clock.advance(30)
        self.store.record_briefing(pid, payload, self.actor)
        return self.store.latest_briefing(pid, raw=True)

    def page(self, pid="p1"):
        html, fp = bw.build_page(self.store, pid)
        return html, fp, Doc(html)

    def make_task(self, task_id, pid="p1", **kw):
        row = {"task_id": task_id, "project_id": pid,
              "title": "wire the form", "status": "ready"}
        row.update(kw)
        self.store.create_task(row, self.actor)
        return row

    def add_evidence(self, subject_type, subject_id, pid="p1", **kw):
        row = {"evidence_id": "ev-%s" % subject_id, "subject_type":
               subject_type, "subject_id": subject_id, "kind": "check",
               "ref": "python3 tools/test_bm_view.py",
               "created_at": self.clock.iso()}
        row.update(kw)
        self.store.add_evidence(row, pid, self.actor)
        return row


class _Clock(object):
    """A controllable now_iso, shape copied from tools/test_bm_lead.py."""

    FORMAT = "%Y-%m-%dT%H:%M:%SZ"

    def __init__(self, start="2026-08-05T09:00:00Z"):
        self.at = datetime.datetime.strptime(start, self.FORMAT)

    def iso(self):
        return self.at.strftime(self.FORMAT)

    def advance(self, seconds):
        self.at = self.at + datetime.timedelta(seconds=seconds)
        return self.iso()


# ---------------------------------------------------------------------------
# 13.2 class 1: consent is the only door
# ---------------------------------------------------------------------------

class TestConsentIsTheOnlyDoorForTheView(ViewCase):
    """Design section 11.4: one store constructor, consent computed before
    the dispatch, zero files before consent, and the doorway needs no
    store at all."""

    HUMAN_SUBCOMMANDS = (("render",), ("url",),
                         ("explain", "--reason", "not-found"),
                         ("brief-page",))
    HOOK_SUBCOMMANDS = (("render", "--if-stale"), ("alert", "--tick"))

    def _tree(self):
        source = _read(VIEW_FILE)
        return source, ast.parse(source, filename="bm_view.py")

    def test_store_constructors_live_in_exactly_one_function(self):
        source, tree = self._tree()
        holders = set()
        constructed = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Attribute) \
                        and func.attr in ("Store", "ReadOnlyStore"):
                    holders.add(node.name)
                    constructed += 1
        self.assertGreater(
            constructed, 0,
            "bm_view.py constructs no store at all, so the one-door "
            "assertion would pass vacuously")
        self.assertEqual(
            {"_store_or_refuse"}, holders,
            "bs.Store and bs.ReadOnlyStore may be constructed in exactly "
            "one function, _store_or_refuse; found them in %s"
            % sorted(holders))

    def test_no_write_primitive_appears_outside_the_funnel(self):
        source, tree = self._tree()
        self.assertNotIn("os.replace", source)
        self.assertNotIn("shutil", source)
        makedirs_in = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) \
                        and isinstance(call.func, ast.Attribute) \
                        and call.func.attr == "makedirs":
                    makedirs_in.add(node.name)
                    args = [a.arg for a in node.args.args]
                    self.assertEqual(
                        args[:1], ["store"],
                        "the one directory-creating function must take the "
                        "store handle as its FIRST parameter, so it is "
                        "unreachable without _store_or_refuse")
            for call in ast.walk(node):
                if isinstance(call, ast.Call) \
                        and isinstance(call.func, ast.Name) \
                        and call.func.id == "open":
                    for kw in call.keywords:
                        if kw.arg == "mode":
                            self.fail("bm_view.py opens a file with an "
                                      "explicit mode")
                    if len(call.args) > 1 and isinstance(
                            call.args[1], ast.Constant):
                        self.assertFalse(
                            set(str(call.args[1].value)) & set("wax"),
                            "bm_view.py opens a file for writing outside "
                            "the store funnel")
        self.assertLessEqual(
            makedirs_in, {"_ensure_brief_dir"},
            "os.makedirs may appear only in _ensure_brief_dir; found it "
            "in %s" % sorted(makedirs_in))

    def test_main_computes_consent_before_the_dispatch_lookup(self):
        source, tree = self._tree()
        main = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        gate_line = None
        lookup_line = None
        commands_readers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Subscript) and isinstance(
                        sub.value, ast.Name) and sub.value.id == "COMMANDS":
                    commands_readers.add(node.name)
                    if node.name == "main":
                        lookup_line = sub.lineno
        for sub in ast.walk(main):
            if isinstance(sub, ast.Call) and isinstance(
                    sub.func, ast.Name) and sub.func.id == "_consent_state":
                gate_line = sub.lineno
                break
        self.assertIsNotNone(gate_line, "main() never calls "
                                        "_consent_state()")
        self.assertIsNotNone(lookup_line, "main() never subscripts "
                                          "COMMANDS")
        self.assertLess(gate_line, lookup_line,
                        "the consent gate must come BEFORE the dispatch "
                        "lookup, or the door is not a gate")
        self.assertEqual({"main"}, commands_readers,
                         "COMMANDS is read outside main: %s"
                         % sorted(commands_readers))

    def test_every_subcommand_pre_consent_creates_zero_files(self):
        self.unconsent()
        before_home = _files_under(self.home)
        before_project = _files_under(self.root)
        for argv in self.HUMAN_SUBCOMMANDS:
            code, out, err = self.run_cli(*argv)
            self.assertEqual(1, code, "%r: %s%s" % (argv, out, err))
            self.assertIn(bw.CONSENT_REFUSAL, out,
                          "%r did not print the setup sentence" % (argv,))
        for argv in self.HOOK_SUBCOMMANDS:
            code, out, err = self.run_cli(*argv)
            self.assertEqual(0, code, "%r: %s%s" % (argv, out, err))
            self.assertEqual("", out, "a hook-wired subcommand printed "
                                      "before consent: %r" % out)
            self.assertEqual("", err)
        self.assertEqual(before_home, _files_under(self.home))
        self.assertEqual(before_project, _files_under(self.root))

    def test_doorway_needs_no_store_and_writes_nothing(self):
        self.unconsent()
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(bare)
        os.environ["BROTHERMODE_ROOT"] = bare
        code, out, err = self.run_cli("doorway")
        self.assertEqual(0, code, out + err)
        self.assertTrue(out.strip(), "the doorway printed nothing")
        self.assertEqual([], _files_under(bare),
                         "the doorway wrote a file at minute zero")
        self.assertEqual([], _files_under(self.home))


# ---------------------------------------------------------------------------
# 13.2 class 2: every section renders
# ---------------------------------------------------------------------------

class TestEverySectionRenders(ViewCase):

    def test_view_sections_has_sixteen_entries_in_the_designed_order(self):
        self.assertEqual(16, len(bw.VIEW_SECTIONS))
        anchors = [s[0] for s in bw.VIEW_SECTIONS]
        self.assertEqual(
            ["header", "needs-you", "next-step", "pipeline", "programme",
             "progress", "counts", "risks", "decisions", "insights",
             "gates", "lanes", "documents", "timeline", "your-move",
             "help"], anchors,
            "design section 4.2's twelve anchors, plus the three the "
            "progress surface loop added (progress, risks, documents), "
            "plus programme (Phase 5, the Gantt), in that order. "
            "programme sits directly after pipeline and before progress "
            "because it answers the widest question on the page, where "
            "the whole programme stands, and the reader should meet it "
            "before the per-owner detail underneath it")
        for entry in bw.VIEW_SECTIONS:
            self.assertEqual(4, len(entry),
                             "(anchor_id, heading, renderer_name, "
                             "empty_state_key) per entry")

    def test_every_anchor_appears_in_the_document(self):
        self.seed()
        _html, _fp, doc = self.page()
        for anchor, _h, _r, _k in bw.VIEW_SECTIONS:
            self.assertIsNotNone(doc.by_id(anchor),
                                 "section #%s is missing" % anchor)

    def test_no_section_renders_blank(self):
        self.seed()
        _html, _fp, doc = self.page()
        for anchor, heading, _r, _k in bw.VIEW_SECTIONS:
            node = doc.by_id(anchor)
            body = Doc.text_of(node).replace(heading, "", 1).strip()
            self.assertTrue(
                body,
                "section #%s rendered blank; a section with zero rows "
                "must render its designed empty state" % anchor)

    def test_anchors_are_unique(self):
        self.seed()
        _html, _fp, doc = self.page()
        ids = [n.attrs["id"] for n in doc.all(attr="id")]
        self.assertEqual(len(ids), len(set(ids)),
                         "duplicate id attributes: %s"
                         % sorted(i for i in ids if ids.count(i) > 1))


# ---------------------------------------------------------------------------
# 13.2 class 3: the empty states are designed
# ---------------------------------------------------------------------------

class TestEmptyStatesAreDesigned(ViewCase):

    BANNED_TITLE_WORDS = ("no", "none", "nothing", "empty")

    def test_every_empty_state_key_has_an_entry_with_four_fields(self):
        for _a, _h, _r, key in bw.VIEW_SECTIONS:
            self.assertIn(key, bw.EMPTY_STATES)
            entry = bw.EMPTY_STATES[key]
            self.assertEqual({"title", "body", "action", "command"},
                             set(entry),
                             "empty state %r must have exactly the four "
                             "Carbon anatomy fields" % key)

    def test_exactly_one_action_per_rendered_empty_state(self):
        self.seed()
        _html, _fp, doc = self.page()
        empties = [n for n in doc.all("div")
                   if "bm-empty" in (n.attrs.get("class") or "")]
        self.assertTrue(empties, "a barely started project must render "
                                 "at least one designed empty state")
        for node in empties:
            actions = [c for c in doc.walk(node)
                       if c.attrs.get("class") == "bm-action"]
            self.assertEqual(1, len(actions),
                             "an empty state carries %d actions; Carbon's "
                             "rule is exactly one" % len(actions))

    def test_titles_are_positive_statements(self):
        for key, entry in bw.EMPTY_STATES.items():
            for word in self.BANNED_TITLE_WORDS:
                self.assertNotRegex(
                    entry["title"].lower(), r"\b%s\b" % word,
                    "empty state %r title %r is not a positive statement"
                    % (key, entry["title"]))

    def test_bodies_are_one_sentence_of_at_most_140_characters(self):
        for key, entry in bw.EMPTY_STATES.items():
            self.assertLessEqual(len(entry["body"]), 140,
                                 "empty state %r body is %d characters"
                                 % (key, len(entry["body"])))

    def test_no_internal_term_appears_in_any_empty_state(self):
        terms = _terminology_terms()
        self.assertTrue(terms)
        for key, entry in bw.EMPTY_STATES.items():
            haystack = _strip_command_tokens(
                " ".join([entry["title"], entry["body"], entry["action"]]))
            for term in terms:
                self.assertNotRegex(
                    haystack.lower(), r"\b%s\b" % re.escape(term.lower()),
                    "empty state %r uses the internal term %r"
                    % (key, term))

    def test_every_action_command_is_in_the_offered_set(self):
        self.assertEqual(("/brotherme-start", "/brotherme-status",
                          "/brotherme-next"), bw.OFFERED_COMMANDS)
        for key, entry in bw.EMPTY_STATES.items():
            self.assertIn(entry["command"], bw.OFFERED_COMMANDS,
                          "empty state %r teaches %r, which is not one of "
                          "the three first-run commands"
                          % (key, entry["command"]))


# ---------------------------------------------------------------------------
# 13.2 class 4: exactly one next action
# ---------------------------------------------------------------------------

class TestExactlyOneNextActionOnThePage(ViewCase):

    def _next_steps(self, doc):
        return doc.all(attr="data-bm-next-step")

    def test_exactly_one_on_the_empty_project(self):
        self.seed(goal="")
        _html, _fp, doc = self.page()
        self.assertEqual(1, len(self._next_steps(doc)))

    def test_exactly_one_on_a_busy_project_and_byte_equal_to_bl(self):
        self.seed()
        self.sign()
        self.add_forecast()
        self.record(_key_decision())
        _html, _fp, doc = self.page()
        nodes = self._next_steps(doc)
        self.assertEqual(1, len(nodes))
        text, why, command = bl.next_action(self.store, "p1")
        block = Doc.text_of(nodes[0])
        for value in (text, why, command):
            self.assertIn(value, block,
                          "the next step block must carry bl.next_action "
                          "byte for byte; missing %r" % value)

    def test_the_counter_can_see_a_second_action(self):
        """Calibration: an equality against 1 is only evidence when the
        counter can return 2."""
        doc = Doc('<div data-bm-next-step="1"></div>'
                  '<p data-bm-next-step="1"></p>')
        self.assertEqual(2, len(self._next_steps(doc)))


# ---------------------------------------------------------------------------
# 13.2 class 5: the handback is always visible
# ---------------------------------------------------------------------------

class TestTheHandbackIsAlwaysVisible(ViewCase):

    def test_the_option_text_is_on_every_page(self):
        self.seed()
        _html, _fp, doc = self.page()
        self.assertIn(bl.HANDBACK_OPTION_TEXT, doc.visible_text(),
                      "no decision is open and the standing offer is "
                      "still mandatory")
        self.record(_key_decision())
        _html, _fp, doc = self.page()
        self.assertIn(bl.HANDBACK_OPTION_TEXT, doc.visible_text())

    def test_the_fork_ends_in_the_handback_for_all_five_classes(self):
        self.seed()
        for cls in bs.INSIGHT_DECISION_CLASSES:
            row = _key_decision(subject="subject %s" % cls,
                                decision_class=cls)
            facts = dict(bv.visual_facts(self.store, "p1"), decision=row)
            fork = bv.diagram_fork(facts)
            self.assertIsNotNone(fork)
            last = fork["nodes"][-1]
            self.assertEqual("handback", last.get("token"),
                             "%s: the last outcome must be the handback"
                             % cls)

    def test_the_copy_as_prompt_block_names_the_decision_id(self):
        self.seed()
        written = self.record(_key_decision())
        _html, _fp, doc = self.page()
        move = doc.by_id("your-move")
        text = Doc.text_of(move)
        self.assertIn("/brotherme-handback", text)
        self.assertIn(written["insight_id"], text,
                      "the pasted prompt must name the decision id")

    def test_your_move_exists_on_the_empty_project(self):
        self.seed(goal="")
        _html, _fp, doc = self.page()
        move = doc.by_id("your-move")
        self.assertIsNotNone(move)
        text = Doc.text_of(move)
        self.assertIn("/brotherme-handback", text)
        self.assertIn(bw.PROMISE_TEXT, text,
                      "the panel must say what taking it back would do")


# ---------------------------------------------------------------------------
# 13.2 class 6: two levels, one expander
# ---------------------------------------------------------------------------

class TestTwoLevelsAndOneExpander(ViewCase):

    def _details_depths(self, doc):
        depths = set()
        for node in doc.all("details"):
            depth = 0
            parent = node.parent
            while parent is not None:
                if parent.tag == "details":
                    depth += 1
                parent = parent.parent
            depths.add(depth)
        return depths

    def test_no_details_contains_another(self):
        self.seed()
        self.sign()
        for i in range(4):
            self.record(_insight(subject="finding %d" % i))
        self.record(_key_decision())
        _html, _fp, doc = self.page()
        self.assertNotIn(1, self._details_depths(doc),
                         "a details element sits inside another, which is "
                         "the third disclosure level L-S3 bans")

    def test_disclosure_depth_is_at_most_one(self):
        self.seed()
        self.record(_key_decision())
        _html, _fp, doc = self.page()
        self.assertLessEqual(self._details_depths(doc), {0})

    def test_the_raw_log_lives_only_inside_the_failure_expander(self):
        self.seed()
        _html, _fp, doc = self.page()
        self.assertEqual([], [n for n in doc.all()
                              if "bm-failure" in (n.attrs.get("class")
                                                  or "")],
                         "the page itself never renders a failure block")
        block = Doc(bv.failure_block("not-found", "checking the page",
                                     "raw engine text", medium="html"))
        raw_holders = [n for n in block.all("pre")]
        self.assertEqual(1, len(raw_holders))
        parent_tags = set()
        parent = raw_holders[0].parent
        while parent is not None:
            parent_tags.add(parent.tag)
            parent = parent.parent
        self.assertIn("details", parent_tags,
                      "the raw output must sit inside the one expander")


# ---------------------------------------------------------------------------
# 13.2 class 7: self contained
# ---------------------------------------------------------------------------

class TestSelfContained(ViewCase):

    def _rich(self):
        self.seed()
        self.sign()
        self.add_forecast()
        for i in range(3):
            self.record(_insight(subject="finding %d" % i))
        self.record(_key_decision())
        self.brief()
        return self.page()

    def test_no_script_element_at_all(self):
        html, _fp, _doc = self._rich()
        self.assertNotIn("<script", html.lower())

    def test_no_external_reference_of_any_kind(self):
        html, _fp, _doc = self._rich()
        self.assertIsNone(re.search(r"\bsrc\s*=", html))
        self.assertIsNone(re.search(r'href="http', html))
        self.assertNotIn("@import", html)
        self.assertIsNone(re.search(r"\burl\(", html))
        self.assertNotIn("data:image", html)

    def test_every_href_is_an_in_page_anchor_that_resolves(self):
        _html, _fp, doc = self._rich()
        anchors = {n.attrs.get("id") for n in doc.all(attr="id")}
        for a in doc.all("a"):
            href = a.attrs.get("href") or ""
            self.assertTrue(href.startswith("#"),
                            "a link leaves the page: %r" % href)
            self.assertIn(href[1:], anchors,
                          "a help line references an anchor that is not "
                          "on screen: %r" % href)

    def test_a_large_fixture_stays_under_the_size_cap(self):
        self.seed()
        self.sign()
        for i in range(60):
            self.record(_insight(subject="finding %d" % i))
        html, _fp, _doc = self.page()
        self.assertLess(len(html.encode("utf-8")), 16 * 1024 * 1024)

    def test_wide_elements_sit_in_scroll_containers(self):
        html, _fp, doc = self._rich()
        style = "".join(n.text for n in doc.all("style"))
        for cls in (".bm-figure", ".bm-card"):
            self.assertIn("overflow-x: auto",
                          style[style.index(cls):style.index(cls) + 200],
                          "%s has no overflow-x container rule" % cls)
        for svg in doc.all("svg"):
            self.assertEqual("figure", svg.parent.tag)
            self.assertIn("bm-figure", svg.parent.attrs.get("class") or "")
        for pre in doc.all("pre"):
            self.assertIn("bm-card", pre.attrs.get("class") or "",
                          "a pre block without the scroll container class")


# ---------------------------------------------------------------------------
# 13.2 class 8: theme aware
# ---------------------------------------------------------------------------

class TestThemeAware(ViewCase):

    def _style(self):
        self.seed()
        _html, _fp, doc = self.page()
        return "".join(n.text for n in doc.all("style"))

    def test_the_media_query_and_the_root_block_both_exist(self):
        style = self._style()
        self.assertIn("@media (prefers-color-scheme: dark)", style)
        self.assertIn(":root {", style)

    def test_the_data_theme_overrides_come_after_the_media_query(self):
        style = self._style()
        media = style.index("@media (prefers-color-scheme: dark)")
        for override in (':root[data-theme="light"]',
                         ':root[data-theme="dark"]'):
            self.assertIn(override, style)
            self.assertGreater(
                style.index(override), media,
                "%s must come AFTER the media query so the viewer's "
                "toggle wins in both directions" % override)

    def test_every_token_used_is_defined_in_both_themes(self):
        self.seed()
        self.record(_key_decision())
        html, _fp, _doc = self.page()
        used = set(re.findall(r"var\(--bm-([a-z0-9-]+)\)", html))
        self.assertTrue(used)
        self.assertEqual(set(bv.TOKENS_LIGHT), set(bv.TOKENS_DARK))
        undefined = used - set(bv.TOKENS_LIGHT)
        self.assertEqual(set(), undefined,
                         "token(s) used but not in both theme tables: %s"
                         % sorted(undefined))


# ---------------------------------------------------------------------------
# 13.2 class 9: the page traces to rows
# ---------------------------------------------------------------------------

class TestPageTracesToRows(ViewCase):

    def _traces(self, doc, anchor, prefix):
        section = doc.by_id(anchor)
        found = set()
        for node in doc.walk(section):
            tag = node.attrs.get("data-bm-trace") or ""
            if tag.startswith(prefix + ":"):
                found.add(tag.split(":", 1)[1])
        return found

    def test_insights_forward_and_backward(self):
        self.seed()
        ids = {self.record(_insight(subject="finding %d" % i))["insight_id"]
               for i in range(3)}
        _html, _fp, doc = self.page()
        self.assertEqual(ids, self._traces(doc, "insights", "i"),
                         "the insight section and the insights table must "
                         "hold the same id set, in both directions")

    def test_timeline_forward_and_backward(self):
        self.seed()
        first = self.brief()
        self.record(_insight())
        second = self.brief()
        ids = {first["briefing_id"], second["briefing_id"]}
        _html, _fp, doc = self.page()
        self.assertEqual(ids, self._traces(doc, "timeline", "b"))

    def test_timeline_replays_oldest_first(self):
        self.seed()
        first = self.brief()
        self.record(_insight())
        second = self.brief()
        _html, _fp, doc = self.page()
        section = doc.by_id("timeline")
        order = [node.attrs["data-bm-trace"].split(":", 1)[1]
                 for node in doc.walk(section)
                 if (node.attrs.get("data-bm-trace") or "").startswith("b:")]
        self.assertEqual([first["briefing_id"], second["briefing_id"]],
                         order, "the run must replay forwards")

    def test_decisions_forward_and_backward(self):
        self.seed()
        ids = set()
        for cls in ("GATE", "RULE"):
            row = self.record(_key_decision(subject="subject %s" % cls,
                                            decision_class=cls))
            ids.add(row["insight_id"])
        _html, _fp, doc = self.page()
        self.assertEqual(ids, self._traces(doc, "decisions", "i"))

    def test_a_reasoned_row_is_labelled_by_the_renderer(self):
        self.seed()
        self.record(_insight(subject="a hunch", evidence_class="REASONED",
                             evidence=""))
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("insights"))
        self.assertIn(bl.evidence_label("REASONED"), text)
        self.assertIn("not tested", text,
                      "REASONED must carry both independent markings")

    def test_a_minimal_row_renders_as_a_labelled_line_never_a_short_box(self):
        """A row recorded before L05 may lack alternatives or a flip
        condition, so it cannot fill six slots. The law is six slots or
        it is not a box: the page renders such a row as its claim line
        with the renderer-applied evidence label, and no five slot box
        exists anywhere."""
        self.seed()
        minimal = {"kind": "LEARNING", "subject": "the widget",
                   "claim": "the widget builds from a clean checkout",
                   "evidence_class": "READ", "confidence": "moderate"}
        self.record(minimal)
        _html, _fp, doc = self.page()
        section = doc.by_id("insights")
        text = Doc.text_of(section)
        self.assertIn(bl.render_claim_text(minimal), text,
                      "the minimal row must render as its labelled line")
        for box in doc.walk(section):
            if box.tag != "dl":
                continue
            labels = [Doc.text_of(c) for c in box.children
                      if c.tag == "dt"]
            self.assertEqual(list(bv.INSIGHT_SLOTS), labels,
                             "a rendered box with other than six slots")

    def test_the_gate_counts_on_the_page_match_the_rows(self):
        self.seed()
        _html, _fp, doc = self.page()
        facts = bv.visual_facts(self.store, "p1")
        counts_text = Doc.text_of(doc.by_id("counts"))
        self.assertIn("%d of %d gates passed"
                      % (facts["gates_passed"], facts["gates_total"]),
                      counts_text,
                      "a number on the page the rows do not hold")


# ---------------------------------------------------------------------------
# 13.2 class 10: byte stable and honestly stale
# ---------------------------------------------------------------------------

class TestByteStableAndHonestlyStale(ViewCase):

    def test_two_renders_with_no_intervening_write_are_identical(self):
        self.seed()
        self.sign()
        self.record(_insight())
        one, fp1, _doc = self.page()
        two, fp2, _doc = self.page()
        self.assertEqual(one, two)
        self.assertEqual(fp1, fp2)

    def test_no_render_clock_appears_anywhere(self):
        self.seed()
        self.clock.advance(86400)
        html, _fp, _doc = self.page()
        self.assertNotIn(self.clock.iso(), html,
                         "the page printed the render clock; it may state "
                         "only properties of the rows")
        again, _fp2, _doc2 = self.page()
        self.assertEqual(html, again)

    def test_the_fingerprint_moves_with_the_rows_and_only_with_them(self):
        self.seed()
        _html, before, _doc = self.page()
        _html, unchanged, _doc = self.page()
        self.assertEqual(before, unchanged)
        self.record(_insight(subject="a new finding"))
        _html, after, _doc = self.page()
        self.assertNotEqual(before, after)
        self.assertRegex(after, r"^[0-9a-f]{12}$")

    def test_the_newest_record_time_shown_is_the_newest_row_read(self):
        self.seed()
        self.record(_insight())
        last = self.brief()
        _html, _fp, doc = self.page()
        header = Doc.text_of(doc.by_id("header"))
        self.assertIn(last["created_at"], header,
                      "the header must state the newest record it was "
                      "built from")


# ---------------------------------------------------------------------------
# 13.2 class 11: three renderers, one collector
# ---------------------------------------------------------------------------

class TestThreeRenderersOneCollector(ViewCase):
    """Extends L04's S2 fixture (the mid-run status) to the HTML render:
    same collector, three renderers, one set of values."""

    def _s2_fixture(self):
        self.seed()
        self.sign()
        self.add_forecast()
        self.open_run()

    def test_shared_values_are_byte_identical_across_all_three(self):
        self._s2_fixture()
        view = bl.collect_status(self.store, "p1")
        founder = "\n".join(bl.render_status(view))
        engineer = "\n".join(bl.render_status(view, ic=True))
        _html, _fp, doc = self.page()
        header = Doc.text_of(doc.by_id("header"))
        next_step = Doc.text_of(doc.by_id("next-step"))
        fields = {label: value for label, value, _x in view["fields"]}
        for label in ("Goal", "Progress"):
            self.assertIn(fields[label], founder)
            self.assertIn(fields[label], engineer)
            self.assertIn(fields[label], header,
                          "the page dropped or reworded the %s value"
                          % label)
        for value in view["next"]:
            self.assertIn(value, next_step)
        payload = {
            "shape": "S2 extended to the page: one collector, three "
                     "renderers",
            "fields": [label for label, _v, _x in view["fields"]],
            "shared_field_values_identical": True,
            "page_carries_next_action_byte_equal": True}
        if not os.path.isdir(EVIDENCE_DIR):
            os.makedirs(EVIDENCE_DIR)
        path = os.path.join(EVIDENCE_DIR, "S2-STATUS_MID_RUN-page.json")
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")

    def test_the_page_reads_the_eight_fields_from_bl(self):
        self._s2_fixture()
        view = bl.collect_status(self.store, "p1")
        self.assertEqual(
            ["Goal", "Direction", "Progress", "Time remaining",
             "Decision needed", "Risk", "Evidence", "Next step"],
            [label for label, _v, _x in view["fields"]],
            "the eight fields stay bl's; the page must not re-derive them")

    def test_the_view_module_defines_no_collector(self):
        tree = ast.parse(_read(VIEW_FILE), filename="bm_view.py")
        offenders = [n.name for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)
                     and n.name.startswith("collect_")]
        self.assertEqual([], offenders,
                         "bm_view.py defines collector(s) %s; a view that "
                         "collected its own rows would be a second truth"
                         % offenders)


# ---------------------------------------------------------------------------
# 13.2 class 12: the html brief matches the markdown
# ---------------------------------------------------------------------------

class TestTheHtmlBriefMatchesTheMarkdown(ViewCase):

    def _handback(self):
        self.seed()
        self.sign()
        decision = self.record(_key_decision())
        return {"kind": "HANDBACK", "project_id": "p1",
                "insight_id": "hb-fixture",
                "created_at": self.clock.iso(),
                "subject": "the payment approach",
                "supersedes": decision["insight_id"],
                "claim": "I would have used hosted checkout",
                "flip_condition": "a measured refusal rate above 2 percent",
                "evidence_class": "REASONED",
                "confidence": "moderate", "confidence_basis": "reasoning"}

    @staticmethod
    def _md_headings(md):
        return {ln[3:].strip() for ln in md.splitlines()
                if ln.startswith("## ")}

    @staticmethod
    def _trace_tags(text):
        return set(re.findall(r"\[i:[0-9a-f-]+\]", text))

    def test_section_headings_are_set_equal(self):
        hb = self._handback()
        md = bl.render_developer_brief(self.store, hb)
        html = bw.render_developer_brief_html(self.store, hb)
        doc = Doc(html)
        html_headings = {Doc.text_of(h).strip() for h in doc.all("h2")}
        self.assertEqual(self._md_headings(md), html_headings)

    def test_trace_tags_are_set_equal(self):
        hb = self._handback()
        md = bl.render_developer_brief(self.store, hb)
        html = bw.render_developer_brief_html(self.store, hb)
        self.assertEqual(self._trace_tags(md),
                         self._trace_tags(Doc(html).visible_text()))
        self.assertTrue(self._trace_tags(md),
                        "the fixture produced no trace tags at all, so "
                        "the equality above proved nothing")

    def test_a_new_markdown_section_would_be_seen(self):
        """Calibration: the html headings are DERIVED from the markdown at
        render time, so this asserts the parser can see a section the
        renderer did not hardcode."""
        parsed = bw._brief_sections(
            "# Title\n\n## A real section\n\nbody\n\n## An extra "
            "section\n\nmore\n")
        self.assertEqual(["A real section", "An extra section"],
                         [h for h, _b in parsed])


# ---------------------------------------------------------------------------
# 13.2 class 13: plain language holds on the page
# ---------------------------------------------------------------------------

class TestPlainLanguageHoldsOnThePage(ViewCase):

    def test_no_internal_term_reaches_the_founder_page(self):
        self.seed()
        self.sign()
        self.add_forecast()
        self.record(_insight())
        self.record(_key_decision())
        self.brief()
        _html, _fp, doc = self.page()
        haystack = _strip_command_tokens(doc.visible_text()).lower()
        terms = _terminology_terms()
        self.assertTrue(terms)
        leaked = sorted({t for t in terms
                         if re.search(r"\b%s\b" % re.escape(t.lower()),
                                      haystack)})
        self.assertEqual([], leaked,
                         "internal term(s) on the founder page: %s"
                         % leaked)

    def test_the_developer_brief_page_may_carry_them(self):
        """The brief is the engineer's page: record ids are the point of
        it, so the plain-language ban does not apply there."""
        self.seed()
        self.sign()
        decision = self.record(_key_decision())
        hb = {"kind": "HANDBACK", "project_id": "p1",
              "insight_id": "hb-plain", "created_at": self.clock.iso(),
              "subject": "the payment approach",
              "supersedes": decision["insight_id"],
              "claim": "I would have used hosted checkout",
              "flip_condition": "a measured refusal rate above 2 percent",
              "evidence_class": "REASONED", "confidence": "moderate",
              "confidence_basis": "reasoning"}
        html = bw.render_developer_brief_html(self.store, hb)
        self.assertIn("[i:", Doc(html).visible_text(),
                      "the developer page must keep its trace tags")


# ---------------------------------------------------------------------------
# 13.2 class 14: the first fifteen minutes
# ---------------------------------------------------------------------------

class TestFirstFifteenMinutes(ViewCase):

    def test_the_doorway_answers_the_three_minute_zero_questions(self):
        self.unconsent()
        code, out, err = self.run_cli("doorway")
        self.assertEqual(0, code, out + err)
        self.assertIn("/brotherme-start", out)
        self.assertIn("/brotherme-help", out)
        commands = set(re.findall(r"/brotherme-[a-z-]+", out))
        self.assertLessEqual(
            commands, {"/brotherme-start", "/brotherme-help"},
            "the doorway offered %s; minute zero teaches one action and "
            "one link, never the command list" % sorted(commands))
        self.assertRegex(out, r"\d+ to \d+",
                         "the cost must be stated as a range")
        self.assertTrue(out == out.encode("ascii", "replace")
                        .decode("ascii"), "the doorway must be pure ASCII")

    def test_the_first_render_happens_after_consent_and_not_before(self):
        self.unconsent()
        before = _files_under(self.root)
        code, _out, _err = self.run_cli("render", "--project-id", "p1")
        self.assertEqual(1, code)
        self.assertEqual(before, _files_under(self.root),
                         "a pre-consent render changed the project tree")
        self.assertFalse(
            os.path.isfile(os.path.join(self.root, bw.VIEW_FILENAME)))
        os.environ["BROTHERME_CONFIG"] = self.config
        self.seed()
        code, out, err = self.run_cli("render", "--project-id", "p1")
        self.assertEqual(0, code, out + err)
        self.assertTrue(
            os.path.isfile(os.path.join(self.root, bw.VIEW_FILENAME)))

    def test_the_setup_counter_reads_2_of_8_at_minute_4(self):
        self.seed()
        _html, _fp, doc = self.page()
        header = Doc.text_of(doc.by_id("header"))
        self.assertIn("2 of 8", header)

    def test_every_counter_increment_traces_to_a_row(self):
        self.seed()

        def count():
            return len([1 for _k, _l, done
                        in bw.setup_milestones(self.store, "p1") if done])

        self.assertEqual(2, count())
        self.record(_key_decision())
        self.assertEqual(3, count(), "a DECISION row is milestone 3")
        self.store.create_task(
            {"task_id": "t1", "project_id": "p1", "title": "wire the form",
             "status": "ready"}, self.actor)
        self.assertEqual(4, count(), "a task row is milestone 4")
        for state in ("active", "awaiting review", "verified", "accepted"):
            self.store.transition_task("t1", state, "fixture", self.actor)
        self.assertEqual(5, count(), "an accepted task is milestone 5")
        self.record(_insight())
        self.assertEqual(6, count(), "an EXECUTED insight is milestone 6")
        self.brief()
        self.assertEqual(7, count(), "a catch-up row is milestone 7")
        packet = os.path.join(self.root, "DELIVERY-PACKET.md")
        with io.open(packet, "w", encoding="utf-8") as fh:
            fh.write("packet fixture\n")
        self.assertEqual(8, count(), "the delivery artefact is milestone 8")

    def test_at_most_three_commands_are_offered_before_first_completion(self):
        self.seed()
        _html, _fp, doc = self.page()
        offered = set(re.findall(r"/brotherme-[a-z-]+",
                                 doc.visible_text()))
        allowed = set(bw.OFFERED_COMMANDS) | {"/brotherme-handback"}
        self.assertLessEqual(
            offered, allowed,
            "the page offered %s before the first piece completed; the "
            "training wheels are the three commands plus the standing "
            "offer to take the work back" % sorted(offered - allowed))


# ---------------------------------------------------------------------------
# 13.2 class 15: guards
# ---------------------------------------------------------------------------

class TestNoSQLGuardAndNoSecondCollector(unittest.TestCase):

    # SQL SHAPES, not bare keywords: these two modules carry founder
    # facing prose, and "Update BrotherMode, then try again." (a landed
    # refusal rewrite in tools/bm_visual.py) is English, not a query. A
    # literal is SQL shaped when the keyword arrives with the clause that
    # makes it executable, which is what a second collector would need.
    _SQL_RE = re.compile(
        r"\b(SELECT\s+.+\s+FROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s"
        r"|DELETE\s+FROM\s|PRAGMA\s+\w+\s*\()", re.IGNORECASE | re.DOTALL)

    @staticmethod
    def _docstring_ids(tree):
        ids = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not body or not isinstance(body, list):
                continue
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
        return ids

    def _module_tree(self, path):
        self.assertTrue(os.path.isfile(path),
                        "%s does not exist yet" % path)
        source = _read(path)
        return source, ast.parse(source, filename=os.path.basename(path))

    def test_neither_new_module_speaks_sql_or_imports_sqlite3(self):
        for path in (VIEW_FILE, VISUAL_FILE):
            source, tree = self._module_tree(path)
            skip = self._docstring_ids(tree)
            hits = []
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and id(node) not in skip
                        and self._SQL_RE.search(node.value)):
                    hits.append((node.lineno, node.value[:60]))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] \
                        if isinstance(node, ast.Import) \
                        else [node.module or ""]
                    self.assertNotIn("sqlite3", names,
                                     "%s imports sqlite3" % path)
            self.assertEqual([], hits,
                             "%s carries SQL-shaped literal(s) %s"
                             % (path, hits))

    def test_the_view_module_defines_no_collect_function(self):
        _source, tree = self._module_tree(VIEW_FILE)
        offenders = [n.name for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)
                     and n.name.startswith("collect_")]
        self.assertEqual([], offenders)

    def test_no_format_string_names_a_bare_duration_unit(self):
        """Design section 4.2: every duration on the page comes from
        bl.render_forecast_lines, so neither new module may format one of
        its own in hours or days."""
        for path in (VIEW_FILE, VISUAL_FILE):
            _source, tree = self._module_tree(path)
            skip = self._docstring_ids(tree)
            hits = []
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and id(node) not in skip
                        and re.search(r"\b(hours|days)\b", node.value)):
                    hits.append((node.lineno, node.value[:60]))
            self.assertEqual([], hits,
                             "%s formats a duration of its own: %s"
                             % (path, hits))

    def test_this_suite_never_greps_the_raw_html_with_assert_in(self):
        """Design section 13.0 mechanism 1, applied to this file itself:
        assertIn(<string literal>, html) is banned; assertions go through
        the Doc tree."""
        tree = ast.parse(_read(os.path.abspath(__file__)),
                         filename="test_bm_view.py")
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == "assertIn"):
                continue
            if len(node.args) < 2:
                continue
            first, second = node.args[0], node.args[1]
            if (isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and isinstance(second, ast.Name)
                    and second.id in ("html", "one", "two", "again")):
                offenders.append(node.lineno)
        self.assertEqual([], offenders,
                         "assertIn(<literal>, html) at line(s) %s; parse, "
                         "do not grep" % offenders)


# ---------------------------------------------------------------------------
# 13.2 class 16: the alert hook
# ---------------------------------------------------------------------------

class TestAlertHookEmitsWellFormedJson(ViewCase):

    def test_a_needs_you_prints_one_json_object_with_a_bell(self):
        self.seed()
        self.record(_key_decision())
        code, out, err = self.run_cli("alert", "--tick")
        self.assertEqual(0, code, out + err)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(1, len(lines), "exactly one JSON object: %r" % out)
        payload = json.loads(lines[0])
        self.assertIn("systemMessage", payload)
        message = payload["systemMessage"]
        self.assertEqual(message, message.encode(
            "ascii", "replace").decode("ascii"))
        self.assertEqual("\x07", payload.get("terminalSequence"),
                         "the terminal sequence is a single ASCII BEL")

    def test_silence_when_nothing_needs_the_founder(self):
        self.seed()
        code, out, err = self.run_cli("alert", "--tick")
        self.assertEqual(0, code, out + err)
        self.assertEqual("", out)
        self.assertEqual("", err)

    def test_the_bell_is_omitted_when_asked(self):
        self.seed()
        self.record(_key_decision())
        os.environ["BROTHERMODE_NO_BELL"] = "1"
        code, out, _err = self.run_cli("alert", "--tick")
        self.assertEqual(0, code)
        payload = json.loads(out.strip())
        self.assertNotIn("terminalSequence", payload)

    def test_the_stop_hook_wires_render_then_alert_last(self):
        """The Stop contract, unchanged in substance and re-pointed at where
        the chain now lives (2026-08-17, the Windows port).

        This used to read the programs out of the hook COMMAND STRING,
        because Stop was an `sh -c` script that spelled all four out. `sh` is
        not on the Windows PATH, so that line is now one python3 driver and
        the chain contents live in tools/bm_hookchain.py's CHAINS table. A
        test still reading the command string would find zero matches and
        report the alert tick as unwired while it runs perfectly well, which
        is a false alarm; a test that DELETED these assertions would lose a
        real contract. So it reads the table, and every claim it made before
        it makes now: the alert tick is wired exactly once, the page rewrite
        runs before it, and the JSON emitting program is LAST so its output
        is what the harness reads."""
        manifest = json.loads(_read(HOOKS_JSON))
        stop = manifest["hooks"]["Stop"]
        commands = [h["command"] for g in stop for h in g["hooks"]]
        joined = " ".join(commands)
        spec = importlib.util.spec_from_file_location(
            "bm_hookchain_for_view", os.path.join(HERE, "bm_hookchain.py"))
        hookchain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hookchain)
        self.assertIn("bm_hookchain.py", joined,
                      "the Stop event no longer runs the chain driver, so "
                      "this test is reading the wrong place")
        self.assertIn(" stop", joined,
                      "the Stop event runs the chain driver without naming "
                      "the stop chain")
        rows = ["%s %s" % (module, " ".join(args))
                for module, args in hookchain.CHAINS["stop"]]
        chain = " ".join(rows)
        self.assertEqual(1, chain.count("alert --tick"),
                         "bm-view alert --tick must be wired exactly once "
                         "on Stop")
        self.assertEqual(1, chain.count("render --if-stale"))
        self.assertLess(chain.index("render --if-stale"),
                        chain.index("alert --tick"),
                        "the page rewrite runs before the alert tick")
        self.assertTrue(chain.rstrip().endswith("alert --tick"),
                        "the JSON emitting program must be the LAST in "
                        "the Stop chain so its output is what the harness "
                        "reads")


# ---------------------------------------------------------------------------
# 13.2 class 17: the progress surface (progress, risks, documents)
# DESIGN-progress-surface.md
# ---------------------------------------------------------------------------

class TestProgressRisksAndDocumentsSections(ViewCase):

    def test_zero_tasks_renders_the_designed_empty_states(self):
        self.seed()
        _html, _fp, doc = self.page()
        for anchor in ("progress", "documents"):
            body = Doc.text_of(doc.by_id(anchor))
            self.assertTrue(body.strip(),
                            "#%s rendered blank on a zero task project"
                            % anchor)
        self.assertIn(bw.EMPTY_STATES["progress"]["title"],
                      Doc.text_of(doc.by_id("progress")))
        self.assertIn(bw.EMPTY_STATES["documents"]["title"],
                      Doc.text_of(doc.by_id("documents")))

    def test_one_task_shows_its_lane_and_its_state(self):
        self.seed()
        self.make_task("t1", title="wire the form", status="ready",
                       assigned_human="Khalil")
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("progress"))
        self.assertIn("wire the form", text)
        self.assertIn("ready", text)
        self.assertIn("you", text)

    def test_a_blocked_task_shows_what_blocks_it_and_the_recorded_action(
            self):
        self.seed()
        self.make_task("t1", title="collect the payment settings",
                       status="ready", assigned_human="Khalil")
        self.store.transition_task("t1", "active", "fixture", self.actor)
        self.make_task("t2", title="send the confirmation email", status="ready",
                       depends_on=["t1"], assigned_runtime="claude-code",
                       blockers=["waiting on the payment settings"])
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("progress"))
        self.assertIn("send the confirmation email", text)
        self.assertIn("collect the payment settings", text,
                      "the blocking task must reach the page as a name, "
                      "not a bare identifier")
        self.assertNotIn("t1", text.split("collect the payment settings")[0]
                         .split("\n")[-1],
                         "a raw task id must never sit in visible body "
                         "text")

    def test_progress_traces_forward_and_backward(self):
        self.seed()
        self.make_task("t1", title="wire the form", status="ready")
        self.make_task("t2", title="send the confirmation email", status="planned")
        _html, _fp, doc = self.page()
        section = doc.by_id("progress")
        found = set()
        for node in doc.walk(section):
            tag = node.attrs.get("data-bm-trace") or ""
            if tag.startswith("t:"):
                found.add(tag.split(":", 1)[1])
        self.assertEqual({"t1", "t2"}, found)

    def test_finished_over_planned_is_the_only_fraction_stated_in_words(
            self):
        self.seed()
        self.make_task("t1", title="wire the form", status="ready")
        self.store.transition_task("t1", "active", "fixture", self.actor)
        for state in ("awaiting review", "verified", "accepted"):
            self.store.transition_task("t1", state, "fixture", self.actor)
        self.make_task("t2", title="send the confirmation email", status="planned")
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("progress"))
        self.assertIn("1 of 2 finished in this lane", text)

    def test_zero_risks_renders_the_designed_empty_state(self):
        self.seed()
        _html, _fp, doc = self.page()
        body = Doc.text_of(doc.by_id("risks"))
        self.assertIn(bw.EMPTY_STATES["risks"]["title"], body)

    def test_a_risk_with_no_mitigation_says_so_rather_than_a_blank_line(
            self):
        self.seed()
        self.record(_insight(kind="RISK", subject="the refund window",
                             claim="refunds may exceed the agreed budget",
                             flip_condition="", evidence_class="REASONED",
                             evidence=""))
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("risks"))
        self.assertIn("the refund window", text)
        self.assertIn("no mitigation recorded", text,
                      "an empty mitigation must say so; a blank line "
                      "reads as safe")

    def test_a_risk_with_a_mitigation_shows_it_and_its_owner(self):
        self.seed()
        self.record(_insight(kind="RISK", subject="the retry storm",
                             claim="retries may exceed the rate limit",
                             flip_condition="cap retries at three per "
                                            "minute"))
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("risks"))
        self.assertIn("cap retries at three per minute", text)

    def test_zero_finished_tasks_renders_the_documents_empty_state(self):
        self.seed()
        self.make_task("t1", title="wire the form", status="ready")
        _html, _fp, doc = self.page()
        self.assertIn(bw.EMPTY_STATES["documents"]["title"],
                      Doc.text_of(doc.by_id("documents")))

    def test_a_finished_task_shows_its_produced_paths_and_its_checks(self):
        self.seed()
        self.make_task("t1", title="wire the form", status="ready",
                       expected_outputs=["widget/booking_form.py"])
        for state in ("active", "awaiting review", "verified", "accepted"):
            self.store.transition_task("t1", state, "fixture", self.actor)
        self.add_evidence("task", "t1", kind="test run",
                          ref="python3 tools/test_bm_view.py")
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("documents"))
        self.assertIn("widget/booking_form.py", text)
        self.assertIn("test run", text)
        self.assertIn(self.clock.iso().rsplit("T", 1)[0], text,
                      "a check must carry the timestamp it ran at")

    def test_a_finished_task_with_no_recorded_path_still_renders(self):
        self.seed()
        self.make_task("t1", title="wire the form", status="ready")
        for state in ("active", "awaiting review", "verified", "accepted"):
            self.store.transition_task("t1", state, "fixture", self.actor)
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("documents"))
        self.assertIn("no path recorded", text)

    def test_a_very_long_task_title_does_not_break_the_page(self):
        self.seed()
        long_title = "wire " + "a very long form title " * 30
        self.make_task("t1", title=long_title, status="ready")
        _html, _fp, doc = self.page()
        self.assertIsNotNone(doc.by_id("progress"))
        text = Doc.text_of(doc.by_id("progress"))
        self.assertIn("wire", text)

    def test_a_non_ascii_task_title_renders_on_the_page(self):
        self.seed()
        self.make_task("t1", title=u"café orders ✅", status="ready")
        _html, _fp, doc = self.page()
        text = Doc.text_of(doc.by_id("progress"))
        self.assertIn(u"café", text)

    def test_no_internal_term_leaks_when_all_three_sections_are_populated(
            self):
        self.seed()
        self.make_task("t1", title="collect the payment settings",
                       status="ready", assigned_human="Khalil")
        self.make_task("t2", title="send the confirmation email", status="ready",
                       depends_on=["t1"], assigned_runtime="claude-code",
                       blockers=["waiting on the payment settings"],
                       expected_outputs=["widget/confirmation.py"])
        for state in ("active", "awaiting review", "verified", "accepted"):
            self.store.transition_task("t2", state, "fixture", self.actor)
        self.add_evidence("task", "t2")
        self.record(_insight(kind="RISK", subject="the refund window",
                             claim="refunds may exceed the agreed budget"))
        _html, _fp, doc = self.page()
        haystack = _strip_command_tokens(doc.visible_text()).lower()
        terms = _terminology_terms()
        self.assertTrue(terms)
        leaked = sorted({t for t in terms
                         if re.search(r"\b%s\b" % re.escape(t.lower()),
                                      haystack)})
        self.assertEqual([], leaked,
                         "internal term(s) leaked once progress, risks "
                         "and documents are populated: %s" % leaked)


class TestTheProgrammeSection(unittest.TestCase):
    """Phase 5, the progress view. The Gantt is a SECTION of the one
    generated page, not a second page: the architecture rule this
    programme is closing says one authority per concern, and two progress
    pages would be two."""

    def _ctx(self):
        tasks = [
            {"task_id": "a", "title": "Close the fence holes",
             "status": "accepted", "phase": "Phase 1",
             "started_at": "2026-08-01T00:00:00Z",
             "completed_at": "2026-08-03T00:00:00Z", "blockers": []},
            {"task_id": "b", "title": "Wire the continue verb",
             "status": "active", "phase": "Phase C",
             "started_at": "2026-08-03T00:00:00Z",
             "completed_at": None, "blockers": []},
        ]
        evidence = {"a": [{"ref": "test_all: ALL GREEN"}]}
        return {"tasks": tasks,
                "gantt": bv.gantt_facts({"tasks": tasks,
                                          "evidence": evidence})}

    def test_the_page_registers_a_programme_section(self):
        anchors = [a for a, _h, _r, _k in bw.VIEW_SECTIONS]
        self.assertIn("programme", anchors)

    def test_every_section_has_help_and_an_empty_state(self):
        """The page's own contract: a section with no help text or no
        designed empty state renders a hole on a fresh project."""
        for anchor, _h, renderer, empty_key in bw.VIEW_SECTIONS:
            self.assertIn(anchor, bw.SECTION_HELP, anchor)
            self.assertIn(empty_key, bw.EMPTY_STATES, empty_key)
            self.assertIn(renderer, bw._SECTION_RENDERERS, renderer)

    def test_it_draws_the_gantt_and_names_every_phase(self):
        body = bw._sec_programme(self._ctx())
        self.assertIn("Phase 1", body)
        self.assertIn("Phase C", body)

    def test_it_states_the_count_of_ticked_records_not_a_percentage_alone(
            self):
        body = bw._sec_programme(self._ctx())
        self.assertIn("1 of 2", body)

    def test_it_says_in_words_what_a_tick_means(self):
        """The tick contract has to be readable by the founder on the
        page itself, not only in a docstring, because the page is where
        the claim is made."""
        body = bw._sec_programme(self._ctx())
        self.assertIn("finished", body.lower())
        self.assertIn("record", body.lower())

    def test_no_work_at_all_falls_through_to_the_designed_empty_state(self):
        self.assertEqual("", bw._sec_programme({"tasks": [],
                                                 "gantt": bv.gantt_facts(
                                                     {"tasks": []})}))

    def test_the_same_rows_render_the_same_bytes_twice(self):
        ctx = self._ctx()
        self.assertEqual(bw._sec_programme(ctx), bw._sec_programme(ctx))


if __name__ == "__main__":
    unittest.main(verbosity=1)
