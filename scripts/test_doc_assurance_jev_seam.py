"""JEV-G1 wave-1 seam J095 (registry: doc-assurance 'generated not
authored' phrasing check), wired into
scripts/doc_assurance.py:check_generated_not_authored() via
jev_seam.consult(). doc_assurance.py has no existing test module (no
scripts/test_doc_assurance.py), so this seam gets its own file per the
wave-1 seam brief's fallback rule
(Documents/BrotherArchive/jev-deep-research-2026-09-18/wave2/seam-brief-common.md).

Every test injects a fake runner: no test here ever touches the network,
a real subprocess, or the keychain.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# Minor fix (opus-review-seams-g1-g3.md): redirect jev_seam's own
# machine-level state root to a throwaway temp dir BEFORE jev_seam is
# ever imported in this process, so no test here reads (or could ever
# write) the real ~/.brother/jev -- must happen before the `import
# jev_seam` line below: JEV_STATE_DIR is a module-level constant
# jev_seam.py computes once, at its own import time.
os.environ.setdefault("BROTHER_JEV_STATE_DIR",
                       tempfile.mkdtemp(prefix="brother-jev-state-test-"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_assurance as DA  # noqa: E402
import jev_seam  # noqa: E402
import jev_g1_seam_cache  # noqa: E402


def _j095_entry():
    return {
        "id": "J095", "role": "second_opinion", "risk": "low", "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {
            "type": "noul",
            "instructions": "does this line invite a hand edit of SYSTEM.md?",
        },
    }


def _noul_runner(prob):
    """A scripted bridge runner answering a noul question at `prob`, no
    network or subprocess: the same shape ScriptedRunner in
    test_jev_seam.py uses, reimplemented here so this file needs no
    import of that test module."""
    def fn(argv, stdin_text):
        payload = json.loads(stdin_text)
        answers = {qid: {"noul": prob, "confidence": prob}
                   for qid in payload["questions"]}
        response = {"model": "typesafe/jev-1.13-test", "answers": answers,
                    "usage": {"cost": 0.001}}
        return 0, json.dumps(response), ""
    return fn


def _seed(root, text):
    rel = "docs/PAGE.md"
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel


class JevSeamJ095SecondOpinion(unittest.TestCase):
    """Mode ships off in the real data/jev-seams.json, so (a) needs no
    mocking; (b)-(d) mock jev_seam.load_seams_config/load_registry to
    force shadow mode for this one test only."""

    LINE = "Please edit `SYSTEM.md` directly when this changes.\n"


    def setUp(self):
        # jev_g1_seam_cache caches the resolved mode per entry_id
        # (module docstring: at most one jev_seam.load_seams_config()
        # call per second) -- a call in an EARLIER test could still be
        # "fresh" here, so reset before every test rather than rely on
        # the freshness window happening to have elapsed.
        jev_g1_seam_cache.reset()
    def test_a_mode_off_makes_zero_calls_and_output_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as root:
            rel = _seed(root, self.LINE)

            def boom(argv, stdin_text):
                raise AssertionError("mode off must never invoke the runner")

            before = DA.check_generated_not_authored(root, [rel])
            after = DA.check_generated_not_authored(root, [rel], jev_runner=boom)
            self.assertEqual(before, after)
            self.assertEqual(len(before), 1)
            self.assertIn("invites a hand edit", before[0])

    def test_b_shadow_mode_output_identical_and_one_ledger_row_written(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as ledger_dir:
            rel = _seed(root, self.LINE)
            expected = DA.check_generated_not_authored(root, [rel])  # real config: off
            cfg = {"modes": {"J095": "shadow"}}
            jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
            with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                    mock.patch.object(jev_seam, "load_registry", return_value=[_j095_entry()]), \
                    mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
                after = DA.check_generated_not_authored(
                    root, [rel], jev_runner=_noul_runner(0.05))  # confident "false"
            self.assertEqual(after, expected)
            # A0.8: shadow hands the call to a background worker and
            # returns at once, before the ledger row exists. drain()
            # waits for the worker to finish so the row is actually
            # there to count.
            jev_seam.drain(timeout=5)
            decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
            self.assertTrue(os.path.isfile(decisions_path))
            with open(decisions_path, encoding="utf-8") as fh:
                rows = [ln for ln in fh if ln.strip()]
            self.assertEqual(len(rows), 1)

    def test_c_seam_path_exception_leaves_output_identical(self):
        with tempfile.TemporaryDirectory() as root:
            rel = _seed(root, self.LINE)
            expected = DA.check_generated_not_authored(root, [rel])
            cfg = {"modes": {"J095": "shadow"}}
            jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
            with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                    mock.patch.object(jev_seam, "load_registry",
                                       side_effect=RuntimeError("registry unreadable")):
                after = DA.check_generated_not_authored(
                    root, [rel], jev_runner=_noul_runner(0.05))
            self.assertEqual(after, expected)

    def test_d_red_proof_wiring_that_trusts_jevs_raw_answer_breaks_test_b(self):
        """Same proof shape as the other wave-1 seams: a call site that
        trusted consult()'s raw jev.answer instead of the mode-safe
        .answer field would have suppressed this real regex hit under
        shadow mode with a confident opposite answer -- exactly what
        test_b asserts never happens -- without ever editing
        doc_assurance.py itself."""
        with tempfile.TemporaryDirectory() as ledger_dir:
            cfg = {"modes": {"J095": "shadow"}}
            jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
            with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                    mock.patch.object(jev_seam, "load_registry", return_value=[_j095_entry()]), \
                    mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
                result = jev_seam.consult(
                    "J095", {"line": self.LINE}, True,
                    seams_config=jev_seam.load_seams_config(),
                    registry=jev_seam.load_registry(),
                    ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
                    runner=_noul_runner(0.05),
                )
                # A0.8: shadow never returns Jev's answer (result.jev is
                # always None); the eventual answer only lands in the
                # ledger, once the background worker finishes. drain()
                # first, then read it there.
                jev_seam.drain(timeout=5)
                decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
                with open(decisions_path, encoding="utf-8") as fh:
                    rows = [json.loads(ln) for ln in fh if ln.strip()]
                self.assertEqual(len(rows), 1)
                wrongly_wired_flag = bool(rows[0]["answer"] >= 0.5)  # raw jev answer
                safely_wired_flag = result.answer                      # what the real code uses
            self.assertFalse(wrongly_wired_flag)
            self.assertTrue(safely_wired_flag)

    def test_e_a1_call_site_never_reads_a_patched_consults_return(self):
        """A1 (opus-review-g1-round2-pkg-decide.md): test_d proves
        consult()'s two fields disagree, but it never touches the CALL
        SITE's own wiring -- check_generated_not_authored() is never
        invoked inside the mocked block there. This one does:
        jev_seam.consult ITSELF is patched to return an ACT-mode
        SeamResult whose .answer is 0.05 (a float, the wrong TYPE for
        this call site's own bool answer), mode live via a patched
        config, and asserts the function's output is unaffected -- the
        same real regex hit, never anything shaped by 0.05."""
        with tempfile.TemporaryDirectory() as root:
            rel = _seed(root, self.LINE)
            expected = DA.check_generated_not_authored(root, [rel])
            cfg = {"modes": {"J095": "shadow"}}
            jev_g1_seam_cache.reset()
            wrong = jev_seam.SeamResult(0.05, {"answer": 0.05}, jev_seam.ACT,
                                        None, False, "mutation-probe")
            with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                    mock.patch.object(jev_seam, "load_registry", return_value=[_j095_entry()]), \
                    mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", tempfile.mkdtemp()), \
                    mock.patch.object(jev_seam, "consult", return_value=wrong):
                after = DA.check_generated_not_authored(root, [rel])
            self.assertEqual(after, expected)


class JevSeamPerfCacheNeverHitsDiskAfterWarmup(unittest.TestCase):
    """Coordinator directive (measured regression in another wave-1
    group: a seam in OFF mode made a hot call site 52x slower by
    re-reading data/jev-seams.json on every call): after a first call
    warms jev_seam's in-process cache, 1,000 further calls to
    jev_seam.load_seams_config() -- the exact function
    check_generated_not_authored()'s guarded block calls on every
    matched line -- must touch the filesystem zero times and return the
    identical dict every time.

    This measures jev_seam.load_seams_config() directly rather than the
    full check_generated_not_authored(): that function legitimately
    calls open() itself (reading the doc corpus, unrelated to the seam),
    so a blanket open()-call count around it would misattribute the
    function's own real I/O to the seam."""


    def setUp(self):
        # jev_g1_seam_cache caches the resolved mode per entry_id
        # (module docstring: at most one jev_seam.load_seams_config()
        # call per second) -- a call in an EARLIER test could still be
        # "fresh" here, so reset before every test rather than rely on
        # the freshness window happening to have elapsed.
        jev_g1_seam_cache.reset()
    def test_1000_config_loads_after_warmup_touch_no_filesystem(self):
        first = jev_seam.load_seams_config()  # warms the in-process cache
        self.assertIsInstance(first, dict)

        calls = {"stat": 0, "exists": 0, "open": 0}
        real_stat, real_exists, real_open = os.stat, os.path.exists, open

        def counting_stat(*a, **kw):
            calls["stat"] += 1
            return real_stat(*a, **kw)

        def counting_exists(*a, **kw):
            calls["exists"] += 1
            return real_exists(*a, **kw)

        def counting_open(*a, **kw):
            calls["open"] += 1
            return real_open(*a, **kw)

        outputs = []
        with mock.patch("os.stat", counting_stat), \
                mock.patch("os.path.exists", counting_exists), \
                mock.patch("builtins.open", counting_open):
            for _ in range(1000):
                outputs.append(jev_seam.load_seams_config())

        self.assertEqual(calls, {"stat": 0, "exists": 0, "open": 0},
                         "a cached config load must never touch the "
                         "filesystem within the cache interval")
        self.assertTrue(all(o == first for o in outputs))

    def test_repeated_regex_hit_still_returns_the_same_finding(self):
        """check_generated_not_authored()'s own output stability, without
        patching open() globally (it needs real I/O to read the corpus):
        1,000 calls against the same flagged line all agree."""
        with tempfile.TemporaryDirectory() as root:
            rel = _seed(root, JevSeamJ095SecondOpinion.LINE)
            results = set()
            for _ in range(1000):
                results.add(tuple(DA.check_generated_not_authored(root, [rel])))
            self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
