#!/usr/bin/env python3
"""Regression tests for tools/bm_statusline.py, the opt in Claude Code
status line, and for the footerLinksRegexes block documented alongside it
in docs/STATUS-LINE.md.
Standard library only. Run: python3 tools/test_bm_statusline.py

Same discipline as tools/test_bm_hookbench.py: every test is self
contained under tempfile.mkdtemp(), and nothing here touches this
repository's own store, the founder's home, or the real vault.

WHAT THIS SUITE IS ACTUALLY DEFENDING (docs/STATUS-LINE.md, the design's
2026-08-05 "answer 7", as changed by PC1H.b):

  1. The happy path prints ONE plain language line: the newest finished run
     from the run index (~/.brother/runs-index.jsonl), dated, in the
     engine's own result words, and nothing else. No index prints the
     NO-DATA line; a torn last line is skipped.
  2. The store is never loaded: the source never names it and a real run
     opens no store file and no sibling tool.
  3. FAIL SILENT stays absolute for every path that still exists: setup not
     consented, a malformed or empty stdin payload, a proof_card.py that
     cannot be loaded, an exception inside the index reader. Each prints
     NOTHING and exits 0. Nothing here may leak a traceback into a
     founder's terminal.
  3b. The script writes NOTHING, ever.
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
# 1. A throwaway HOME with a consented config and, per test, a run index.
# ---------------------------------------------------------------------------

RESULT = ("BROTHER RESULT: DONE | units=3 PASS=3 FAIL=0 NO-DATA=0 | "
          "release=NOT_REQUESTED | next=review-receipt")
RESULT_LINE = ("DONE | units=3 PASS=3 FAIL=0 NO-DATA=0 | "
               "release=NOT_REQUESTED | next=review-receipt")


class StatuslineCase(unittest.TestCase):
    """A throwaway HOME, a consented config, and (per test, on request) a
    run index at <HOME>/.brother/runs-index.jsonl. Nothing is created
    eagerly: a test that wants "no index yet" asks for exactly that, so
    setUp only prepares the folders and the environment."""

    ENV_KEYS = ("BROTHERME_CONFIG", "HOME")

    def setUp(self):
        self.assertIsNotNone(
            bst, "tools/bm_statusline.py does not exist yet, so nothing in "
                "this file can run.")
        self.tmp = tempfile.mkdtemp(prefix="bm-statusline-")
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.home)
        os.makedirs(self.project)
        self.config = os.path.join(self.tmp, "cfg", "config.json")
        _write_consented_config(self.config, os.path.join(self.home, "Vault"))
        self._env_backup = {k: os.environ.get(k) for k in self.ENV_KEYS}
        os.environ["BROTHERME_CONFIG"] = self.config
        os.environ["HOME"] = self.home
        self._cwd_backup = os.getcwd()

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

    def index_path(self):
        return os.path.join(self.home, ".brother", "runs-index.jsonl")

    def add_row(self, when, run_dir=None, result=RESULT):
        """Append one index row the way proof_card.update_index writes it:
        a leading newline, one JSON object, a trailing newline."""
        os.makedirs(os.path.dirname(self.index_path()), exist_ok=True)
        row = {"run_dir": run_dir or self.project, "result": result,
               "time": when}
        with io.open(self.index_path(), "ab") as fh:
            fh.write(("\n" + json.dumps(row) + "\n").encode("utf-8"))

    def add_torn_tail(self):
        with io.open(self.index_path(), "ab") as fh:
            fh.write(b'{"run_dir": "/x", "res')

    def run_main(self, payload_text):
        """Feed `payload_text` (a str, or None for a closed empty stdin) to
        a freshly loaded bm_statusline.main() and return (stdout_text,
        exit_code). A fresh module object per call is the honest way to
        prove each run starts cold."""
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
# 2. The happy path: the line is the newest index row, nothing else.
# ---------------------------------------------------------------------------

class TestTheLineIsTheNewestRunIndexRow(StatuslineCase):

    def test_the_newest_row_prints_as_a_dated_result_line(self):
        self.add_row(100, run_dir=os.path.join(self.tmp, "gone"))
        self.add_row(200)
        out, code = self.run_main(json.dumps({"cwd": self.project}))
        self.assertEqual(code, 0)
        self.assertEqual(out, "1970-01-01 %s\n" % RESULT_LINE)

    def test_a_row_for_a_deleted_run_folder_is_marked_missing(self):
        self.add_row(200, run_dir=os.path.join(self.tmp, "gone"))
        out, _code = self.run_main("{}")
        self.assertEqual(
            out, "1970-01-01 %s (run folder missing)\n" % RESULT_LINE)

    def test_the_payload_folder_does_not_matter(self):
        """The index is per user, not per project: a payload naming a
        folder with no project in it prints the same line."""
        self.add_row(200)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        out, _code = self.run_main(json.dumps({"cwd": elsewhere,
                                               "session_id": "abc"}))
        self.assertEqual(out, "1970-01-01 %s\n" % RESULT_LINE)

    def test_a_torn_last_line_is_skipped_and_the_line_still_prints(self):
        self.add_row(200)
        self.add_torn_tail()
        out, code = self.run_main("{}")
        self.assertEqual(code, 0)
        self.assertEqual(out, "1970-01-01 %s\n" % RESULT_LINE)


class TestNoIndexIsNoData(StatuslineCase):

    def test_an_absent_index_prints_the_no_data_line_and_exits_zero(self):
        out, code = self.run_main("{}")
        self.assertEqual(code, 0)
        self.assertEqual(out, "NO-DATA: no run recorded yet\n")

    def test_an_index_that_is_only_a_torn_line_is_no_data_too(self):
        os.makedirs(os.path.dirname(self.index_path()))
        self.add_torn_tail()
        out, _code = self.run_main("{}")
        self.assertEqual(out, "NO-DATA: no run recorded yet\n")


# ---------------------------------------------------------------------------
# 3. The store is never loaded.
# ---------------------------------------------------------------------------

class TestTheStoreIsNeverLoaded(StatuslineCase):

    def test_the_source_never_names_the_store(self):
        self.assertNotIn("bm_store", _read(STATUSLINE_FILE))

    def test_a_run_opens_no_store_file_and_no_sibling_tool(self):
        """A Python audit hook (a subprocess, so the hook cannot leak into
        this test process) records every file one real run opens: none is
        bm_store.py, bm_lead.py or a store.sqlite3."""
        self.add_row(200)
        code = (
            "import sys, runpy\n"
            "opened = []\n"
            "def hook(name, args):\n"
            "    if name == 'open':\n"
            "        opened.append(str(args[0]))\n"
            "sys.addaudithook(hook)\n"
            "try:\n"
            "    runpy.run_path(%r, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "sys.stderr.write('OPENED ' + repr(opened))\n" % STATUSLINE_FILE)
        import ast
        import subprocess
        out = subprocess.run(
            [sys.executable, "-B", "-c", code], input="{}", text=True,
            capture_output=True, cwd=self.project,
            env=dict(os.environ, HOME=self.home,
                     BROTHERME_CONFIG=self.config))
        self.assertEqual(out.returncode, 0, out.stderr)
        opened = ast.literal_eval(out.stderr.split("OPENED ", 1)[1])
        for path in opened:
            for forbidden in ("bm_store", "bm_lead", "store.sqlite3"):
                self.assertNotIn(forbidden, path)
        self.assertIn(self.index_path(), opened)


# ---------------------------------------------------------------------------
# 4. Fail silent, every path that still holds. Every case here asserts the
#    exact same two things: empty stdout, exit code 0.
# ---------------------------------------------------------------------------

class TestFailSilentPaths(StatuslineCase):

    def _assert_silent(self, payload_text):
        out, code = self.run_main(payload_text)
        self.assertEqual(out, "")
        self.assertEqual(code, 0)

    def test_consent_missing_even_with_an_index(self):
        self.add_row(200)
        self.unconsent()
        self._assert_silent(json.dumps({"cwd": self.project}))

    def test_malformed_json_on_stdin(self):
        self.add_row(200)
        self._assert_silent("{not json at all")

    def test_empty_stdin(self):
        self.add_row(200)
        self._assert_silent("")
        self._assert_silent(None)

    def test_stdin_json_that_is_not_an_object(self):
        self.add_row(200)
        self._assert_silent(json.dumps(["cwd", self.project]))
        self._assert_silent(json.dumps(42))
        self._assert_silent(json.dumps(None))

    def test_an_unloadable_proof_card_is_silent(self):
        """The index reader lives in scripts/proof_card.py; a copy of this
        tool with none beside it prints nothing rather than a traceback."""
        self.add_row(200)
        mod = _load("bm_statusline")
        real_stdin = sys.stdin
        sys.stdin = io.StringIO("{}")
        out = io.StringIO()
        try:
            with mock.patch.object(mod, "_load_proof_card",
                                   return_value=None):
                with contextlib.redirect_stdout(out):
                    code = mod.main()
        finally:
            sys.stdin = real_stdin
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(code, 0)

    def test_an_exception_raised_inside_the_index_reader_is_silent(self):
        """Exception injection through the REAL call path: the real
        proof_card loads, and its read_index itself raises. main()'s one
        broad guard catches a failure that has nothing to do with consent
        or the payload."""
        self.add_row(200)
        mod = _load("bm_statusline")
        card = mod._load_proof_card()
        self.assertIsNotNone(card)

        def _boom(*_a, **_k):
            raise RuntimeError("injected failure for the fail silent test")

        real_stdin = sys.stdin
        sys.stdin = io.StringIO("{}")
        out = io.StringIO()
        try:
            with mock.patch.object(card, "read_index", _boom), \
                    mock.patch.object(mod, "_load_proof_card",
                                      return_value=card):
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

    def test_running_main_leaves_home_and_project_byte_for_byte(self):
        self.add_row(200)
        self.add_torn_tail()
        before = self._snapshot(self.tmp)
        out, code = self.run_main(json.dumps({"cwd": self.project}))
        self.assertEqual(code, 0)
        self.assertTrue(out)
        self.assertEqual(before, self._snapshot(self.tmp),
                         "bm_statusline.py changed a file; it must only "
                         "ever read")
        self.assertFalse(
            os.path.isfile(os.path.join(self.project, "PROJECT-VIEW.html")),
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
