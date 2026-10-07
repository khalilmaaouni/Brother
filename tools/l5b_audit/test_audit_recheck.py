"""L5b.8: the audit blocks on a bad fires file, and --recheck re-derives the
whole record from the tree. Each test builds a fixture in a temp directory,
changes the working directory into it, and imports l5b_audit by name."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import l5b_audit  # noqa: E402
from tools.l5b_audit import scanner  # noqa: E402

_A = '''import json
def load(path):
    try:
        with open(path) as fh:
            return json.loads(fh.read())
    except OSError:
        pass
'''
_B = '''def save(path, text):
    with open(path, "w") as fh:
        fh.write(text)
'''
_C = '''def risky(path):
    try:
        return open(path).read()
    except:
        return None
'''


def _write(rel, text):
    parent = os.path.dirname(rel)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(rel, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel


def _rehash(record):
    body = dict(record)
    body.pop("report_hash", None)
    body.pop("produced_at", None)
    record["report_hash"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()
    return record


class TestFiresAndRecheck(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="l5b8-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.tmp)
        _write("r/a.py", _A)
        _write("r/b.py", _B)
        calls = scanner.scan_tree("r")
        ids = [scanner.call_id("r/" + c.file, c.qualname, c.symbol, c.ordinal) for c in calls]
        self.open_id = [i for c, i in zip(calls, ids) if c.symbol == "open"][0]
        self.other_id = [i for c, i in zip(calls, ids) if c.symbol != "open"][0]
        _write("fm.json", json.dumps([{"entry_id": self.open_id, "test_id": "t.test_a",
                                       "mutation_id": "M-1", "expect_substring": "test_something_long"}]))
        self.out = "audit.json"

    def _fires(self, fires=(), bypass=(), scheme="l5b-fires-v1", raw=None, name="fires.json"):
        if raw is not None:
            _write(name, raw)
        else:
            _write(name, json.dumps({"scheme_version": scheme, "fires": list(fires),
                                     "checker_bypass": list(bypass), "annotation_clearances": []}))
        return name

    def _audit(self, fires="fires.json"):
        code = l5b_audit.main(["--root", "r", "--out", self.out, "--doc", "audit.md", "--patch", "audit.patch",
                               "--fires", fires, "--fire-map", "fm.json"])
        with open(self.out, encoding="utf-8") as fh:
            return code, json.load(fh)

    def _save(self, record):
        with open(self.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")

    def test_a_missing_fires_file_is_scan_error(self):
        code, record = self._audit(fires="absent.json")
        self.assertEqual(code, 1)
        self.assertTrue(record["blocked"])
        self.assertEqual(record["reason"], "SCAN_ERROR")
        self.assertEqual(record["boundary_calls_tested"], 0)
        self.assertTrue(any("fires" in r for r in record["skips"]["skip_reasons"]), record["skips"])

    def test_a_corrupt_or_foreign_fires_file_is_scan_error(self):
        for fires in (self._fires(raw="{not json"), self._fires(scheme="l5b-fires-v0")):
            code, record = self._audit(fires)
            self.assertEqual((code, record["blocked"], record["reason"]), (1, True, "SCAN_ERROR"))

    def test_a_checker_bypass_is_checker_mismatch(self):
        code, record = self._audit(self._fires(bypass=[self.open_id]))
        self.assertEqual((code, record["blocked"], record["reason"]), (1, True, "CHECKER_MISMATCH"))

    def test_a_fired_record_nobody_scanned_is_scan_error(self):
        fired = lambda entry_id: {"entry_id": entry_id, "test_id": "t", "fired": True, "reason": "fired"}
        for fires in ([fired("r/nowhere.py:1:open")], [fired(self.other_id)],
                      [fired(self.open_id), fired(self.open_id)]):
            code, record = self._audit(self._fires(fires=fires))
            self.assertEqual((code, record["reason"], record["boundary_calls_tested"]), (1, "SCAN_ERROR", 0))
        code, record = self._audit(self._fires(fires=[fired(self.open_id)]))
        self.assertNotEqual(record["reason"], "SCAN_ERROR")
        self.assertEqual(record["boundary_calls_tested"], 1)

    def test_a_recheck_survives_lines_moving_above_the_call(self):
        # 2026-10-03: 107 lines landed above a fired call and the line keyed id named no scanned call any more, so
        # the committed record read SCAN_ERROR, tested 0. The id is keyed on function and ordinal, never the line.
        fired = {"entry_id": self.open_id, "test_id": "t", "fired": True, "reason": "fired"}
        _, record = self._audit(self._fires(fires=[fired]))
        self.assertEqual(record["boundary_calls_tested"], 1, record)
        self.assertEqual(l5b_audit.main(["--recheck", self.out]), 0)
        _write("r/a.py", "\n\n\n" + _A)
        self.assertEqual(l5b_audit.main(["--recheck", self.out]), 0)
        _write("r/a.py", _A.replace("import json\n", "import json\nopen('first')\n"))
        self.assertEqual(l5b_audit.main(["--recheck", self.out]), 1, "a new open() in the same scope must shift the ordinal")

    def test_the_fire_map_keep_rule(self):
        base = {"test_id": "t", "mutation_id": "m"}
        entries = [dict(base, entry_id="keep-me", expect_substring="twelve_chars"),
                   dict(base, entry_id="drop-me", expect_substring="ab c"),
                   dict(base, entry_id="", expect_substring="short")]
        _write("keep.json", json.dumps(entries))
        self.assertEqual(l5b_audit._fire_map_ids("keep.json"), frozenset(["keep-me"]))
        _write("raise.json", json.dumps([dict(base, entry_id="", expect_substring="twelve_chars")]))
        with self.assertRaises(l5b_audit.report_mod.AuditInputError):
            l5b_audit._fire_map_ids("raise.json")
        _write("notarray.json", json.dumps({"entries": []}))
        with self.assertRaises(l5b_audit.report_mod.AuditInputError):
            l5b_audit._fire_map_ids("notarray.json")

    def test_a_recheck_of_a_real_audit_passes(self):
        self._audit(self._fires())
        self.assertEqual(l5b_audit._recheck(self.out), 0)
        self._audit(self._fires(raw="{not json"))
        self.assertEqual(l5b_audit._recheck(self.out), 0)
        self._audit(fires="absent.json")
        self.assertEqual(l5b_audit._recheck(self.out), 2)

    def test_a_forged_record_with_its_own_hash_fails(self):
        _code, record = self._audit(self._fires())
        self.assertGreater(record["hits_unexempted"], 0)
        record["hits_unexempted"] = 0
        self._save(_rehash(record))
        self.assertEqual(l5b_audit._recheck(self.out), 1)

    def test_a_forged_verdict_fails(self):
        _code, record = self._audit(self._fires(bypass=[self.open_id]))
        record["blocked"] = False
        record["reason"] = "COMPLETE_PASS"
        self._save(_rehash(record))
        self.assertEqual(l5b_audit._recheck(self.out), 1)

    def test_tree_drift_fails(self):
        self._audit(self._fires())
        _write("r/c.py", _C)
        self.assertEqual(l5b_audit._recheck(self.out), 1)

    def test_a_record_that_cannot_be_rederived_is_no_data(self):
        _code, record = self._audit(self._fires())
        headless = dict(record)
        del headless["root"]
        self._save(_rehash(headless))
        self.assertEqual(l5b_audit._recheck(self.out), 2)
        self._save(record)
        os.unlink("fires.json")
        self.assertEqual(l5b_audit._recheck(self.out), 2)
        self._fires()
        os.unlink("fm.json")
        self.assertEqual(l5b_audit._recheck(self.out), 2)

    def test_changed_fires_bytes_fail(self):
        self._audit(self._fires())
        with open("fires.json", "rb") as fh:
            raw = bytearray(fh.read())
        raw[-2:-1] = b" "
        with open("fires.json", "wb") as fh:
            fh.write(bytes(raw))
        self.assertEqual(l5b_audit._recheck(self.out), 1)


if __name__ == "__main__":
    sys.exit(unittest.main())
