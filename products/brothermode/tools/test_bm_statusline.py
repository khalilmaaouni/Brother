#!/usr/bin/env python3
"""Regression tests for tools/bm_statusline.py, the opt in Claude Code
status line, and for the footerLinksRegexes block documented alongside it
in docs/STATUS-LINE.md.
Standard library only. Run: python3 tools/test_bm_statusline.py

Same discipline as tools/test_bm_hookbench.py: every test is self
contained under tempfile.mkdtemp(), and nothing here touches this
repository's own store, the founder's home, or the real vault.

WHAT THIS SUITE IS ACTUALLY DEFENDING (docs/STATUS-LINE.md, the design's
2026-08-05 "answer 7"):

  1. The happy path prints ONE plain language line, built from the same
     fields the founder's own status view uses, and nothing else.
  2. FAIL SILENT IS ABSOLUTE. Every way this script can fail (no
     BrotherMode project here, no store yet, setup not consented, a
     malformed or empty stdin payload, ambiguous or missing projects, a
     corrupt store, an exception raised deep inside the real collector)
     prints NOTHING and exits 0. Nothing here is allowed to leak a
     traceback into a founder's terminal.
  3. The script writes NOTHING, ever: running it against a real project
     leaves the project folder byte for byte as it found it.
  4. The footerLinksRegexes pattern this project tells a founder to paste
     into his own settings actually matches the trace tag shape
     tools/bm_lead.py really prints, and does not fire on ordinary prose.
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock

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
STATUSLINE_FILE = os.path.join(HERE, "bm_statusline.py")
DOC_FILE = os.path.join(ROOT, "docs", "STATUS-LINE.md")


def _load(name):
    """Load a sibling module by PATH, the same technique tools/bm_view.py,
    tools/bm_lead.py and tools/bm_hookbench.py all use for bm_store."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_optional(name):
    """None instead of a bare import crash collapsing this whole file into
    one error, copied from tools/test_bm_view.py's own _load_optional: on
    an untouched tree the module under test may not exist yet, and every
    class below should fail on ITS OWN assertion, not on collection."""
    try:
        return _load(name)
    except Exception:  # sbe: allow-silent documented: on an untouched tree the module under test may not exist yet, and each test class must fail on its own assertion, not on collection
        return None


bst = _load_optional("bm_statusline")
bs = _load_optional("bm_store")
bl = _load_optional("bm_lead")


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


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
            "session_id": "sess-statusline"}


def _insight(**kw):
    """Shape mirrored from tools/test_bm_view.py's own _insight helper, the
    minimum a store.record_insight call accepts."""
    row = {"kind": "LEARNING", "subject": "the widget",
           "claim": "the widget builds from a clean checkout",
           "evidence": "python3 tools/test_bm_statusline.py",
           "evidence_class": "EXECUTED", "confidence": "high",
           "confidence_basis": "it is a measurement and it repeats",
           "flip_condition": "the build fails on a clean checkout",
           "alternatives": [{"option": "trust the last build",
                             "why_not": "it ran on a dirty tree"}]}
    row.update(kw)
    return row


def _key_decision(**kw):
    """Shape mirrored from tools/test_bm_view.py's own _key_decision
    helper: an open decision that offered the founder control, which is
    what R7 (handback-not-offered) requires before record_insight accepts
    a DECISION row at all."""
    row = _insight(kind="DECISION", subject="the payment approach",
                   claim="use hosted checkout", decision_class="RULE",
                   control_offered=1, evidence_class="READ",
                   evidence="tools/bm_store.py:81", confidence="moderate")
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# 1. one_line() as a pure function: no store, no stdin, just the shape
#    collect_status hands it.
# ---------------------------------------------------------------------------

class TestOneLinePureFunction(unittest.TestCase):

    def setUp(self):
        self.assertIsNotNone(
            bst, "tools/bm_statusline.py does not exist yet, so nothing in "
                "this file can run.")

    def test_progress_and_a_waiting_decision(self):
        fields = [("Goal", "ship it", []),
                  ("Progress", "2 of 5 tasks accepted", []),
                  ("Decision needed", "the payment approach", [])]
        self.assertEqual(bst.one_line(fields),
                         "2 of 5 tasks accepted, decision waiting")

    def test_progress_and_no_decision(self):
        fields = [("Progress", "3 of 3 tasks accepted", []),
                  ("Decision needed", "none", [])]
        self.assertEqual(bst.one_line(fields),
                         "3 of 3 tasks accepted, no decision waiting")

    def test_decision_needed_absent_from_the_fields_reads_as_none(self):
        fields = [("Progress", "nothing planned yet", [])]
        self.assertEqual(bst.one_line(fields),
                         "nothing planned yet, no decision waiting")

    def test_no_progress_at_all_prints_nothing(self):
        fields = [("Decision needed", "the payment approach", [])]
        self.assertIsNone(bst.one_line(fields))

    def test_blank_progress_prints_nothing(self):
        fields = [("Progress", "   ", []), ("Decision needed", "none", [])]
        self.assertIsNone(bst.one_line(fields))


# ---------------------------------------------------------------------------
# 2. A real throwaway project, a real store, main() driven end to end.
# ---------------------------------------------------------------------------

class StatuslineCase(unittest.TestCase):
    """A throwaway project root, a consented config, and (per test, on
    request) a real store. Nothing here is created eagerly: a test that
    wants "no store exists yet" must be able to ask for exactly that, so
    setUp only prepares the folders and the environment."""

    ENV_KEYS = ("BROTHERME_CONFIG", "BROTHERMODE_ROOT", "BROTHERMODE_VIEW",
                "BROTHERMODE_VAULT", "BROTHERMODE_NO_BELL", "HOME")

    def setUp(self):
        self.assertIsNotNone(
            bst, "tools/bm_statusline.py does not exist yet, so nothing in "
                "this file can run.")
        self.tmp = tempfile.mkdtemp(prefix="bm-statusline-")
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
        self._cwd_backup = os.getcwd()
        self.actor = _actor()

    def tearDown(self):
        os.chdir(self._cwd_backup)
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

    # -- seeding, against a real writable Store -------------------------

    def make_store(self, root=None):
        return bs.Store(root or self.root)

    def seed(self, store, pid="p1", goal="a booking page my customers "
                                          "can use"):
        stamp = bs.now_iso()
        store.upsert_project(
            {"project_id": pid, "name": "widget", "goal": goal,
             "created_at": stamp, "updated_at": stamp}, self.actor)
        return pid

    def make_task(self, store, task_id, pid="p1", **kw):
        row = {"task_id": task_id, "project_id": pid,
              "title": "wire the form", "status": "ready"}
        row.update(kw)
        store.create_task(row, self.actor)
        return row

    def record_decision(self, store, pid="p1", **kw):
        return store.record_insight(pid, _key_decision(**kw), self.actor)

    # -- driving main() in process ----------------------------------------

    def run_main(self, payload_text):
        """Feed `payload_text` (a str, or None for a closed empty stdin) to
        bst.main() and return (stdout_text, exit_code). Reloads
        tools/bm_statusline.py fresh for every call, the same reason
        tools/bm_hookbench.py purges sys.modules between repetitions: this
        script's own _load calls are not cached anywhere, so a fresh
        module object is the honest way to prove each run starts cold."""
        mod = _load("bm_statusline")
        real_stdin = sys.stdin
        sys.stdin = io.StringIO(payload_text or "")
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mod.main()
        finally:
            sys.stdin = real_stdin
        return out.getvalue(), code


# ---------------------------------------------------------------------------
# 3. The happy path.
# ---------------------------------------------------------------------------

class TestHappyPathEndToEnd(StatuslineCase):

    def test_progress_and_no_decision(self):
        store = self.make_store()
        try:
            pid = self.seed(store)
            self.make_task(store, "t1", pid, status="accepted")
            self.make_task(store, "t2", pid, status="accepted")
            self.make_task(store, "t3", pid, status="ready")
        finally:
            store.close()
        out, code = self.run_main(json.dumps({"cwd": self.root}))
        self.assertEqual(code, 0)
        self.assertEqual(out, "2 of 3 tasks accepted, no decision waiting\n")

    def test_a_key_decision_flips_the_line_to_decision_waiting(self):
        store = self.make_store()
        try:
            pid = self.seed(store)
            self.make_task(store, "t1", pid, status="accepted")
            self.record_decision(store, pid)
        finally:
            store.close()
        out, code = self.run_main(json.dumps({"cwd": self.root}))
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith("decision waiting\n"), repr(out))
        self.assertNotIn("none", out)

    def test_the_workspace_current_dir_shape_also_works(self):
        """docs/STATUS-LINE.md tells the founder to paste his own settings
        block; the real payload Claude Code sends carries `cwd` at the top
        level (verified against the statusline contract), which is the
        only field this script reads to find the project. This just proves
        a plain `cwd` payload, the documented minimum, is enough."""
        store = self.make_store()
        try:
            pid = self.seed(store)
            self.make_task(store, "t1", pid, status="accepted")
        finally:
            store.close()
        out, code = self.run_main(json.dumps({
            "cwd": self.root, "session_id": "abc", "model": {"id": "x"}}))
        self.assertEqual(code, 0)
        self.assertEqual(out, "1 of 1 tasks accepted, no decision waiting\n")


# ---------------------------------------------------------------------------
# 4. Fail silent, every path. Every case here asserts the exact same two
#    things: empty stdout, exit code 0.
# ---------------------------------------------------------------------------

class TestFailSilentPaths(StatuslineCase):

    def _assert_silent(self, payload_text):
        out, code = self.run_main(payload_text)
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_no_store_at_all(self):
        """The env root exists (BROTHERMODE_ROOT resolves it), but
        bs.Store was never constructed, so there is no store.sqlite3 on
        disk: ReadOnlyStore refuses 'no-store'."""
        self._assert_silent(json.dumps({"cwd": self.root}))

    def test_consent_missing_even_with_a_real_store(self):
        store = self.make_store()
        try:
            self.seed(store)
        finally:
            store.close()
        self.unconsent()
        self._assert_silent(json.dumps({"cwd": self.root}))

    def test_malformed_json_on_stdin(self):
        self._assert_silent("{not json at all")

    def test_empty_stdin(self):
        self._assert_silent("")
        self._assert_silent(None)

    def test_stdin_json_that_is_not_an_object(self):
        self._assert_silent(json.dumps(["cwd", self.root]))
        self._assert_silent(json.dumps(42))
        self._assert_silent(json.dumps(None))

    def test_missing_fields_falls_back_to_cwd_and_still_stays_silent(self):
        """A payload with none of the fields this script reads: it falls
        back to os.getcwd(), which here is a folder holding no
        BrotherMode project at all."""
        empty_dir = os.path.join(self.tmp, "elsewhere")
        os.makedirs(empty_dir)
        os.chdir(empty_dir)
        os.environ.pop("BROTHERMODE_ROOT", None)
        self._assert_silent(json.dumps({}))

    def test_two_projects_is_ambiguous_and_silent(self):
        store = self.make_store()
        try:
            self.seed(store, pid="p1")
            self.seed(store, pid="p2")
        finally:
            store.close()
        self._assert_silent(json.dumps({"cwd": self.root}))

    def test_zero_projects_is_silent(self):
        self.make_store().close()
        self._assert_silent(json.dumps({"cwd": self.root}))

    def test_a_corrupt_store_file_is_silent(self):
        store = self.make_store()
        try:
            self.seed(store)
        finally:
            store.close()
        store_path = bs.store_path(self.root)
        with io.open(store_path, "wb") as fh:
            fh.write(b"not a sqlite database")
        self._assert_silent(json.dumps({"cwd": self.root}))

    def test_an_exception_raised_deep_inside_the_real_collector_is_silent(self):
        """Exception injection through the REAL call path: a real store, a
        real project, a real bs.ReadOnlyStore open, and then
        bl.collect_status itself raises. Proves main()'s one broad guard
        catches a failure that has nothing to do with consent, the store,
        or the payload, not only the refusal-shaped ones exercised above."""
        store = self.make_store()
        try:
            self.seed(store)
        finally:
            store.close()

        real_bl = _load("bm_lead")

        def _boom(_store, _project_id):
            raise RuntimeError("injected failure for the fail silent test")

        real_bl.collect_status = _boom

        def _patched_load(name):
            if name == "bm_lead":
                return real_bl
            return _load(name)

        mod = _load("bm_statusline")
        with mock.patch.object(mod, "_load", side_effect=_patched_load):
            real_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"cwd": self.root}))
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    code = mod.main()
            finally:
                sys.stdin = real_stdin
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(code, 0)


# ---------------------------------------------------------------------------
# 5. It writes nothing. Ever.
# ---------------------------------------------------------------------------

class TestNeverWritesAFile(StatuslineCase):

    def _snapshot(self, root):
        found = []
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                found.append((os.path.relpath(p, root), os.path.getsize(p)))
        return sorted(found)

    def test_running_main_leaves_the_project_folder_byte_for_byte(self):
        store = self.make_store()
        try:
            pid = self.seed(store)
            self.make_task(store, "t1", pid, status="accepted")
            self.record_decision(store, pid)
        finally:
            store.close()
        before = self._snapshot(self.root)
        out, code = self.run_main(json.dumps({"cwd": self.root}))
        self.assertEqual(code, 0)
        self.assertTrue(out)
        after = self._snapshot(self.root)
        self.assertEqual(before, after,
                         "bm_statusline.py changed a file under the "
                         "project root; it must only ever read")
        self.assertFalse(
            os.path.isfile(os.path.join(self.root, "PROJECT-VIEW.html")),
            "bm_statusline.py must never trigger bm-view render's write "
            "path")


# ---------------------------------------------------------------------------
# 6. The footerLinksRegexes calibration: the documented pattern against
#    real trace tag output, and against ordinary prose.
# ---------------------------------------------------------------------------

class TestFooterLinksRegexesCalibration(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.isfile(DOC_FILE),
                        "docs/STATUS-LINE.md does not exist yet")
        self.assertIsNotNone(bl, "tools/bm_lead.py did not load")

    def _entry(self):
        """The one footerLinksRegexes entry documented in
        docs/STATUS-LINE.md, parsed out of its fenced JSON block, exactly
        as a founder would paste it: this reads the shipped page, never a
        copy kept in this file."""
        text = _read(DOC_FILE)
        marker = text.find('"footerLinksRegexes"')
        self.assertGreater(marker, -1,
                           "docs/STATUS-LINE.md carries no "
                           "footerLinksRegexes settings block")
        start = text.rfind("```json", 0, marker)
        self.assertGreater(start, -1,
                           "the footerLinksRegexes block is not inside a "
                           "```json fence")
        start += len("```json")
        end = text.find("```", start)
        self.assertGreater(end, -1, "the ```json fence is never closed")
        data = json.loads(text[start:end])
        entries = data["footerLinksRegexes"]
        self.assertEqual(1, len(entries),
                         "expected exactly one footerLinksRegexes entry")
        return entries[0]

    def _compiled_pattern(self):
        """The documented pattern, valid JavaScript (the regex dialect
        Claude Code itself runs this against): named groups spell
        (?<name>...). Python's re module accepts only the (?P<name>...)
        spelling for the identical construct, so the one, minimal,
        explicitly-stated translation below is applied before compiling
        here. This changes no character of the pattern's matching
        behaviour for this pattern (no lookbehind, no other syntax where
        the two dialects diverge): it only reaches syntax Python's
        compiler accepts."""
        entry = self._entry()
        for key in ("pattern", "url"):
            self.assertIn(key, entry,
                         "footerLinksRegexes entries require %r per the "
                         "published settings schema" % key)
        py_pattern = entry["pattern"].replace("(?<", "(?P<")
        return re.compile(py_pattern), entry

    def test_the_entry_declares_the_regex_variant(self):
        entry = self._entry()
        self.assertEqual(entry.get("type"), "regex")

    def test_matches_a_real_trace_tag_from_render_claim_line(self):
        rx, _entry = self._compiled_pattern()
        row = _insight(insight_id=uuid.uuid4().hex)
        line = bl.render_claim_line(row)
        self.assertRegex(line, rx.pattern)
        match = rx.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group("id"), row["insight_id"])

    def test_matches_a_real_decision_id_from_render_decision_card(self):
        rx, _entry = self._compiled_pattern()
        row = _key_decision(insight_id=uuid.uuid4().hex)
        card = "\n".join(bl.render_decision_card(row, trace=True))
        match = rx.search(card)
        self.assertIsNotNone(match,
                             "the documented pattern does not match a real "
                             "decision card's trace tag: %r" % card)
        self.assertEqual(match.group("id"), row["insight_id"])

    def test_does_not_match_ordinary_prose(self):
        rx, _entry = self._compiled_pattern()
        prose = (
            "The founder needs to decide this by tomorrow.",
            "Email me at i:info@example.com if this breaks.",
            "See tools/bm_view.py:1122 for the render call.",
            "i: this is not a trace tag at all",
            "[decision needed] pick an option",
            "REASONED the build passes [i:not-32-hex-at-all]",
            "the widget builds from a clean checkout",
        )
        for sentence in prose:
            self.assertIsNone(
                rx.search(sentence),
                "the documented footerLinksRegexes pattern wrongly matched "
                "ordinary prose: %r" % sentence)

    def test_the_url_template_uses_named_placeholders_not_positional(self):
        """LENS-D's own prose summary says $1/$2; the published schema
        (verified directly against
        https://www.schemastore.org/claude-code-settings.json while this
        page was written) says {name} filled from NAMED groups. The
        documented block follows the schema, not the paraphrase, and this
        pins that choice against a silent regression back to $1."""
        _rx, entry = self._compiled_pattern()
        self.assertIn("{id}", entry["url"] + entry.get("label", ""))
        self.assertNotIn("$1", entry["url"])


if __name__ == "__main__":
    unittest.main()
