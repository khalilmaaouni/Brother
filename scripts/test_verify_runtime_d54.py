"""test_verify_runtime_d54.py: D5.4. The verifier an installed copy runs must
work with the checkout unavailable, and must stay inside the copy it was
installed into.

Every fixture is built in a temp folder: no repository document, no docs/ tree
and no private file is read. This module imports scripts/verify_runtime.py at
module level, so the whole suite runs RED when that module is not there, which
is the code this sub unit adds.

Driven backwards on purpose. A manifest entry whose path is absolute, carries
a ".." segment, or reaches outside through a symlinked parent must read FAIL
even when the file it names exists and its sha256 matches the manifest,
because the point of the confinement is that such a path is never followed.
"""
import contextlib
import hashlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import verify_runtime as VR  # noqa: E402

ENGINE_NAME = "engine.py"
ENGINE_BYTES = b"the shipped engine"
NUL = chr(0)
HIGH_BYTES = bytes([0xff, 0xfe])


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_bytes(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class VerifierFixture(unittest.TestCase):
    """One runtime directory under a temp folder, with the manifest written
    fresh by each case, so no case inherits another case's answer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="d54-verify-")
        self.runtime_dir = os.path.join(self.tmp, "installed", "runtime")
        self.engine = os.path.join(self.runtime_dir, ENGINE_NAME)
        write_bytes(self.engine, ENGINE_BYTES)
        self.manifest_path = os.path.join(self.runtime_dir, VR.MANIFEST_NAME)

    def manifest(self, entries):
        write_bytes(self.manifest_path,
                    (json.dumps({"files": entries}) + chr(10)).encode("utf-8"))

    def green_manifest(self):
        self.manifest([{"path": ENGINE_NAME,
                        "sha256": sha256(ENGINE_BYTES)}])

    def verify(self):
        return VR.verify(self.runtime_dir)

    def symlink_or_skip(self, target, link):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlinks are unavailable here: %s" % exc)

    def assert_names(self, lines, needle):
        self.assertTrue(any(needle in line for line in lines), lines)


class VerifyReportsTheVerdictMatrix(VerifierFixture):
    """R6: PASS only when every manifested file's sha256 matches on disk, FAIL
    on any mismatch or on a symlink standing in for a regular file, and
    NO-DATA when the manifest is missing, unreadable or names no file."""

    def test_a_clean_runtime_reads_pass(self):
        self.green_manifest()
        verdict, lines = self.verify()
        self.assertEqual(verdict, "PASS", lines)

    def test_no_argument_checks_this_module_own_directory(self):
        self.assertEqual(VR.verify(), VR.verify(VR.HERE))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(VR.main([VR.HERE]), 2)

    def test_a_manifest_naming_zero_files_is_no_data_never_a_pass(self):
        self.manifest([])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_a_missing_manifest_is_no_data_never_a_pass(self):
        verdict, lines = self.verify()
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_a_manifest_that_is_not_valid_json_is_no_data_never_a_pass(self):
        write_bytes(self.manifest_path, b"{not json at all")
        verdict, lines = self.verify()
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_a_manifest_that_is_not_utf8_is_no_data_never_a_pass(self):
        write_bytes(self.manifest_path, HIGH_BYTES + b" not json at all")
        verdict, lines = self.verify()
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_a_manifest_that_is_not_an_object_is_no_data(self):
        write_bytes(self.manifest_path, b"[1, 2, 3]")
        verdict, lines = self.verify()
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_a_manifest_whose_files_is_not_a_list_is_no_data(self):
        write_bytes(self.manifest_path,
                    json.dumps({"files": {"a": 1}}).encode("utf-8"))
        verdict, lines = self.verify()
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_a_sha256_mismatch_is_fail_named_by_path(self):
        self.manifest([{"path": ENGINE_NAME, "sha256": "0" * 64}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, ENGINE_NAME)

    def test_a_file_missing_from_disk_is_fail_named_by_path(self):
        self.manifest([{"path": "gone.py", "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, "gone.py")

    def test_a_manifest_entry_that_is_not_an_object_is_fail(self):
        self.manifest([[ENGINE_NAME, sha256(ENGINE_BYTES)]])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)

    def test_a_manifest_entry_with_no_usable_path_or_sha_is_fail(self):
        self.manifest([{"path": 5, "sha256": sha256(ENGINE_BYTES)},
                       {"path": ENGINE_NAME, "sha256": None},
                       {"path": ENGINE_NAME}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)

    def test_a_symlink_standing_in_for_a_manifested_file_is_fail(self):
        link = os.path.join(self.runtime_dir, "linked.py")
        self.symlink_or_skip(self.engine, link)
        self.manifest([{"path": "linked.py",
                        "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, "linked.py")

    def test_a_directory_where_a_manifested_file_belongs_is_fail(self):
        os.makedirs(os.path.join(self.runtime_dir, "adir"), exist_ok=True)
        self.manifest([{"path": "adir", "sha256": "0" * 64}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)

    def test_a_plain_relative_subdirectory_path_still_reads_pass(self):
        write_bytes(os.path.join(self.runtime_dir, "sub", "shipped.py"),
                    ENGINE_BYTES)
        self.manifest([{"path": "sub/shipped.py",
                        "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "PASS", lines)


class VerifyConfinesEveryRead(VerifierFixture):
    """R7: no read reaches outside runtime_dir, and a path the verifier would
    not follow is refused before the join rather than after the read."""

    def test_a_dotdot_segment_that_resolves_inside_is_refused_before_any_read(self):
        os.makedirs(os.path.join(self.runtime_dir, "sub"), exist_ok=True)
        rel = "sub/../" + ENGINE_NAME
        self.manifest([{"path": rel, "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, rel)

    def test_a_dotdot_segment_that_leaves_the_runtime_directory_is_refused(self):
        write_bytes(os.path.join(self.tmp, "outside", ENGINE_NAME),
                    ENGINE_BYTES)
        rel = "../../outside/" + ENGINE_NAME
        self.manifest([{"path": rel, "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, rel)

    def test_an_absolute_manifest_path_is_refused_before_any_read(self):
        rel = "/" + ENGINE_NAME
        self.manifest([{"path": rel, "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, rel)

    def test_a_symlinked_parent_that_leaves_the_runtime_directory_is_refused(self):
        outside = os.path.join(self.tmp, "outside-real")
        write_bytes(os.path.join(outside, "shipped.py"), ENGINE_BYTES)
        self.symlink_or_skip(outside,
                             os.path.join(self.runtime_dir, "borrowed"))
        rel = "borrowed/shipped.py"
        self.manifest([{"path": rel, "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)
        self.assert_names(lines, rel)

    def test_a_manifest_path_with_a_nul_byte_is_refused_not_crashed(self):
        self.manifest([{"path": "bad" + NUL + "name.py",
                        "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)

    def test_a_manifest_path_that_is_the_empty_string_is_fail_not_pass(self):
        self.manifest([{"path": "", "sha256": sha256(ENGINE_BYTES)}])
        verdict, lines = self.verify()
        self.assertEqual(verdict, "FAIL", lines)


class HostileInputIsRefusedNotCrashed(VerifierFixture):
    """Every public function is handed None, a wrong type, a bool where a
    number belongs, NaN, bytes that are not utf-8, a directory where a file
    belongs and a file where a directory belongs. Each is refused with this
    module's own refusal value, never a raw interpreter exception and never an
    accept."""

    HOSTILE = (None, 5, 3.5, float("nan"), True, False, [], {}, ("x",),
               object())

    def test_verify_refuses_a_runtime_dir_that_is_not_a_path(self):
        for hostile in self.HOSTILE:
            with self.subTest(hostile=repr(hostile)):
                verdict, lines = VR.verify(hostile)
                self.assertEqual(verdict, "NO-DATA", lines)

    def test_verify_refuses_bytes_that_are_not_utf8(self):
        verdict, lines = VR.verify(b"/nowhere/" + HIGH_BYTES)
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_verify_refuses_a_runtime_dir_that_holds_no_manifest(self):
        verdict, lines = VR.verify(self.tmp)
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_verify_refuses_a_file_where_a_directory_belongs(self):
        verdict, lines = VR.verify(self.engine)
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_verify_refuses_a_runtime_dir_with_a_nul_byte(self):
        verdict, lines = VR.verify(self.tmp + NUL)
        self.assertEqual(verdict, "NO-DATA", lines)

    def test_load_manifest_refuses_hostile_paths(self):
        for hostile in self.HOSTILE + (b"", HIGH_BYTES, self.tmp,
                                       os.path.join(self.tmp, "nope")):
            with self.subTest(hostile=repr(hostile)):
                self.assertIsNone(VR.load_manifest(hostile))

    def test_load_manifest_refuses_a_file_that_is_not_json(self):
        path = os.path.join(self.tmp, "notjson")
        write_bytes(path, HIGH_BYTES + b" not json")
        self.assertIsNone(VR.load_manifest(path))

    def test_main_refuses_argv_that_is_not_a_list_of_strings(self):
        for hostile in (5, "runtime", {"dir": "x"}, type(None),
                        [self.runtime_dir, 5], [None], [3.5], [[]]):
            with self.subTest(hostile=repr(hostile)):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(VR.main(hostile), 2)

    def test_main_maps_the_verdict_to_a_distinct_exit_code(self):
        self.green_manifest()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(VR.main([self.runtime_dir]), 0)
            self.manifest([{"path": ENGINE_NAME, "sha256": "0" * 64}])
            self.assertEqual(VR.main([self.runtime_dir]), 1)
            os.remove(self.manifest_path)
            self.assertEqual(VR.main([self.runtime_dir]), 2)

    def test_main_prints_no_data_for_a_missing_manifest(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = VR.main([self.runtime_dir])
        self.assertEqual(code, 2)
        self.assertIn(VR.NODATA, buf.getvalue())
        self.assertNotIn("PASS", buf.getvalue())

    def test_a_path_like_runtime_dir_is_still_accepted(self):
        self.green_manifest()
        verdict, lines = VR.verify(pathlib.Path(self.runtime_dir))
        self.assertEqual(verdict, "PASS", lines)


if __name__ == "__main__":
    unittest.main()
