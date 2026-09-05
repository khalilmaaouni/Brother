"""scripts/test_gauntlet_frozen.py: proves gauntlet_frozen.py actually
refuses a moved corpus rather than reporting a green nobody checked against
the filesystem.

Every fixture is a temp copy of a real spec (and, for the corpus-file case,
a temp copy of the real corpus it names), never the tree's own files, so
this suite mutates nothing a build depends on.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAUNTLETS = os.path.join(ROOT, "benchmarks", "gauntlets")
sys.path.insert(0, HERE)
import gauntlet_frozen as gf  # noqa: E402


class TestGeneratedCorpus(unittest.TestCase):
    """A spec with no separate data file (corpus: "none: ...") hashes its
    own seed definition. race-2.json is one of these on the real tree."""

    def setUp(self):
        with open(os.path.join(GAUNTLETS, "race-2.json"),
                  encoding="utf-8") as handle:
            self.spec = json.load(handle)
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self._write(self.spec)

    def tearDown(self):
        os.unlink(self.path)

    def _write(self, spec):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(spec, handle)

    def test_unmutated_copy_matches(self):
        self.assertEqual(gf.check(self.path), self.spec["frozen"]["corpus_sha1"])

    def test_one_byte_change_to_the_seed_is_refused(self):
        mutated = json.loads(json.dumps(self.spec))
        mutated["seeded_conditions"][0] = mutated["seeded_conditions"][0] + "x"
        self._write(mutated)
        with self.assertRaises(ValueError) as ctx:
            gf.check(self.path)
        self.assertIn("REFUSED: corpus hash moved", str(ctx.exception))

    def test_missing_frozen_block_is_no_data(self):
        mutated = json.loads(json.dumps(self.spec))
        del mutated["frozen"]
        self._write(mutated)
        result = gf.check(self.path)
        self.assertTrue(result.startswith(gf.NODATA), result)


class TestRealCorpusFile(unittest.TestCase):
    """hostile-japanese-identity.json names a real corpus file. A one byte
    mutation of THAT file (never the spec) must be refused the same way."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(GAUNTLETS, "hostile-japanese-identity.json"),
                  encoding="utf-8") as handle:
            self.spec = json.load(handle)
        self.corpus_rel = self.spec["frozen"]["corpus"]
        real_corpus = os.path.join(ROOT, self.corpus_rel)

        # lay the corpus down at the SAME relative path under a temp root,
        # since check() resolves a real corpus path relative to the repo
        # root gauntlet_frozen.py lives under.
        self.fake_root = self.tmpdir
        fake_corpus_path = os.path.join(self.fake_root, self.corpus_rel)
        os.makedirs(os.path.dirname(fake_corpus_path), exist_ok=True)
        shutil.copyfile(real_corpus, fake_corpus_path)
        self.fake_corpus_path = fake_corpus_path

        self.spec_path = os.path.join(self.fake_root, "spec.json")
        with open(self.spec_path, "w", encoding="utf-8") as handle:
            json.dump(self.spec, handle)

        self._real_root = gf.ROOT
        gf.ROOT = self.fake_root

    def tearDown(self):
        gf.ROOT = self._real_root
        shutil.rmtree(self.tmpdir)

    def test_unmutated_corpus_matches(self):
        self.assertEqual(gf.check(self.spec_path),
                          self.spec["frozen"]["corpus_sha1"])

    def test_one_byte_change_to_the_corpus_is_refused(self):
        with open(self.fake_corpus_path, "r+b") as handle:
            handle.seek(0)
            first = handle.read(1)
            handle.seek(0)
            handle.write(bytes([first[0] ^ 0xFF]))
        with self.assertRaises(ValueError) as ctx:
            gf.check(self.spec_path)
        self.assertIn("REFUSED: corpus hash moved", str(ctx.exception))

    def test_missing_corpus_file_is_no_data(self):
        os.unlink(self.fake_corpus_path)
        result = gf.check(self.spec_path)
        self.assertTrue(result.startswith(gf.NODATA), result)


class TestCLI(unittest.TestCase):
    def test_every_real_spec_prints_frozen_ok(self):
        for name in sorted(os.listdir(GAUNTLETS)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(GAUNTLETS, name)
            result = gf.check(path)
            self.assertFalse(result.startswith(gf.NODATA),
                              "%s: %s" % (name, result))

    def test_main_exits_2_with_no_args(self):
        self.assertEqual(gf.main([]), 2)


if __name__ == "__main__":
    unittest.main()
