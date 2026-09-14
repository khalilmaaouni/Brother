#!/usr/bin/env python3
"""End-to-end tests: Request Envelope -> classifier -> Context Pack, wired together
as a synthetic pipeline (VH-50 / VB3-03 integration seam).

WHY THIS EXISTS. bm_vault_context.py (envelope + classifier), bm_vault_pack.py
(the pack compiler) and bm_vault.py (the retrieval engine, its policy-deny
filter, its authority sort and its lifecycle contract) each have their own
suite already (test_bm_vault_context.py, test_bm_vault_pack.py,
test_bm_vault.py). None of those exercises the pieces TOGETHER, in the shape
a real caller (bm_vault_serve.py, a served endpoint) actually uses them: mint
an envelope from a question, read its answer_class, then hand the question
text to compile_pack and see what comes back. This machine has no live
Genie/Cortex/Teams to test against, so the pipeline here stays entirely
local and in-process: a real envelope, a real classifier call, a real
in-memory vault index (built with bm_vault.py's own _upsert_note, the same
technique test_bm_vault_pack.py already uses), and a real compile_pack call.

Same two seams patched as test_bm_vault_pack.py, for the same reason:
bm_vault._connect and bm_vault._policy_deny are the only two pieces of
bm_vault.py that read machine environment (the index path, an access policy
file) rather than being handed everything as arguments. Patching them keeps
this suite from touching the founder's real vault index or policy file.

Run: python3 -m pytest products/brothermode/tools/test_vault_answer_flow.py -q
"""
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bm_vault_pack = _load("bm_vault_pack_for_flow", "bm_vault_pack.py")
bm_vault_context = _load("bm_vault_context_for_flow", "bm_vault_context.py")
bm_vault = bm_vault_pack.bm_vault  # the exact instance compile_pack calls through


def _note(name, descr, ntype, body, extra=""):
    return ("---\nname: %s\ndescription: %s\ntype: %s\n%s---\n%s\n"
            % (name, descr, ntype, extra, body))


def _index(notes):
    """An in-memory sqlite index built with bm_vault's OWN _upsert_note (the
    same function cmd_index calls per file, and the same helper
    test_bm_vault_pack.py's own _index uses), so FTS, anchors and content
    hashes are populated exactly as a real index would be.
    notes: [(path, text), ...]."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    bm_vault._schema(con)
    for i, (path, text) in enumerate(notes, start=1):
        m = bm_vault.FRONT_NAME.search(text[:1200])
        title = m.group(1).strip() if m else os.path.splitext(os.path.basename(path))[0]
        d = bm_vault.FRONT_DESC.search(text[:1200])
        descr = d.group(1).strip() if d else ""
        kind = bm_vault._classify(path, text)
        bm_vault._upsert_note(con, path, title, descr, "vault", kind, float(i), text)
    con.commit()
    return con


class VaultAnswerFlowTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Same isolation test_bm_vault_pack.py's own suite applies: _search
        # loads bm_vault_decay on every call, and that module reads its store
        # path from the environment at load time. Without this, this suite
        # reads and scores against the founder's real ~/.claude/vault-decay.json.
        cls._tmp = tempfile.mkdtemp(prefix="bm-vault-answer-flow-")
        cls._decay_was = os.environ.get("BM_VAULT_DECAY")
        os.environ["BM_VAULT_DECAY"] = os.path.join(cls._tmp, "decay.json")

    @classmethod
    def tearDownClass(cls):
        if cls._decay_was is None:
            os.environ.pop("BM_VAULT_DECAY", None)
        else:
            os.environ["BM_VAULT_DECAY"] = cls._decay_was

    def setUp(self):
        self._real_connect = bm_vault._connect
        self._real_policy_deny = bm_vault._policy_deny
        self.addCleanup(self._restore)

    def _restore(self):
        bm_vault._connect = self._real_connect
        bm_vault._policy_deny = self._real_policy_deny

    def _serve(self, notes, deny=None, budget=7, query=None):
        """Wire a fixture index into compile_pack and return (pack, raw_json).
        deny: predicate(path)->bool forwarded to _search exactly as a real
        access-policy deny would be; None means bm_vault._policy_deny's own
        "no policy file: everything readable" answer."""
        con = _index(notes)
        bm_vault._connect = lambda: con
        bm_vault._policy_deny = lambda args, con2: (deny, None, None, [])
        pack = bm_vault_pack.compile_pack("/no/such/vault", query, budget=budget)
        raw = json.dumps(pack)
        return pack, raw

    # ---- A: official-metric-shaped flow -------------------------------

    def test_a_official_metric_flow_reaches_the_canonical_note(self):
        question = "What is the current total revenue for outlet 4021?"
        # Checked first, not assumed: this phrasing classifies OFFICIAL_METRIC
        # (matches a _VALUE_ASK_PATTERN, "total" is in _METRIC_NOUNS, no
        # _TREND_WORDS present).
        # No tenant/principal supplied: this is a single-tenant local call,
        # not an enterprise-mode request, so missing_enterprise_fields
        # naming both is the expected (not asserted-away) reading -- the
        # served endpoint, not this module, is what would refuse on it.
        envelope, _missing = bm_vault_context.build_request_envelope(question)
        self.assertEqual(envelope["answer_class"], "OFFICIAL_METRIC", envelope)
        self.assertIn(envelope["answer_class"],
                      ("OFFICIAL_METRIC", "STANDARD_POLICY"))

        canonical = _note(
            "outlet-4021-revenue-ruling",
            "the standing ruling on outlet 4021 total revenue", "decision",
            "The current total revenue for outlet 4021 is 128000 dollars. "
            "This is the standing ruling.",
            extra="authority: source_of_record\npromotion: canonical\n"
                 "promoted_by: khalil\npromoted_at: 2026-01-01\n")
        candidate = _note(
            "outlet-4021-revenue-draft-estimate",
            "an unvalidated draft estimate of outlet 4021 revenue", "note",
            "The current total revenue for outlet 4021 might be around "
            "130000 dollars, per a quick draft estimate.",
            extra="lifecycle: candidate\n")

        pack, _raw = self._serve(
            [("/vault/outlet-4021-ruling.md", canonical),
             ("/vault/outlet-4021-draft.md", candidate)],
            query=envelope["question"]["text"])

        titles = [item["title"] for item in pack["authoritative"]]
        self.assertIn("outlet-4021-revenue-ruling", titles, pack)
        self.assertNotIn("outlet-4021-revenue-draft-estimate", titles, pack)

    # ---- B: denied-identity flow ---------------------------------------

    def test_b_policy_denied_note_never_appears_anywhere(self):
        question = "What is our policy on outlet closures?"
        envelope, _missing = bm_vault_context.build_request_envelope(question)
        self.assertEqual(envelope["answer_class"], "STANDARD_POLICY", envelope)

        public = _note(
            "outlet-closure-policy", "the standing policy on outlet closures",
            "decision",
            "Our policy on outlet closures requires regional sign-off. "
            "Standing ruling.",
            extra="authority: source_of_record\npromotion: canonical\n"
                 "promoted_by: khalil\npromoted_at: 2026-01-01\n")
        denied = _note(
            "secretprojectxyz-outlet-closure-policy",
            "the SecretProjectXyz-only policy on outlet closures", "decision",
            "Our policy on outlet closures inside SecretProjectXyz waives "
            "regional sign-off. Standing ruling.",
            extra="authority: source_of_record\npromotion: canonical\n"
                 "promoted_by: khalil\npromoted_at: 2026-01-01\n")
        deny = lambda path: "secretproject" in path.lower()

        pack, raw = self._serve(
            [("/vault/outlet-closure-policy.md", public),
             ("/vault/secretproject-outlet-closure.md", denied)],
            deny=deny, query=envelope["question"]["text"])

        titles = [item["title"] for item in pack["authoritative"]]
        self.assertIn("outlet-closure-policy", titles, pack)
        self.assertNotIn("secretprojectxyz-outlet-closure-policy", titles, pack)
        # Nowhere at all: not in authoritative, not in warnings, not as a
        # dropped id in the serialized pack (test_bm_vault_pack.py's own
        # test_02 pattern -- the same VB2-01 "a denied note's content is
        # never read for ranking and never printed anywhere" contract).
        self.assertNotIn("SecretProjectXyz", raw, raw)
        self.assertNotIn("secretproject", raw.lower(), raw)

    # ---- C: transactional-action refusal flow ---------------------------

    def test_c_transactional_action_never_grants_a_write_on_its_own(self):
        question = "Update the SAP record for outlet 4021"
        envelope, _missing = bm_vault_context.build_request_envelope(question)
        self.assertEqual(bm_vault_context.classify_answer_class(question),
                         "TRANSACTIONAL_ACTION")
        self.assertEqual(envelope["answer_class"], "TRANSACTIONAL_ACTION",
                         envelope)
        # The classification alone never grants a write, demonstrated end to
        # end through the actual envelope builder (no
        # explicit_action_authorization was passed above), not just the
        # classifier in isolation.
        self.assertEqual(envelope["allowed_actions"], ["read"], envelope)

    # ---- D: Japanese width-variant query --------------------------------

    def test_d1_full_width_query_reaches_a_half_width_note(self):
        """bm_vault.py's own _cjk_hits folds both the query and the note text
        through bm_vault_analyzer.normalize() (the JA78 fix, see
        _cjk_hits's docstring) -- but ONLY once _search's _CJK_PROBE_RE gate
        has already matched the RAW query text. A full-width digit query
        (U+FF10-FF19 is inside _CJK_PROBE_RE's \\uFF01-\\uFF9F range) passes
        that gate, so it DOES reach a note that only ever writes the value
        half-width. Confirmed against the real retrieval path (not asserted
        from reading the docstring): compile_pack's authoritative list
        contains the note below when queried with the full-width digits."""
        halfwidth_note = _note(
            "outlet-code-ruling", "the standing ruling on the outlet code",
            "decision",
            "The outlet code is 123 for this location. This is the "
            "standing ruling.",
            extra="authority: source_of_record\npromotion: canonical\n"
                 "promoted_by: khalil\npromoted_at: 2026-01-01\n")
        pack, _raw = self._serve(
            [("/vault/outlet-code-halfwidth.md", halfwidth_note)],
            query=u"１２３")  # full-width "123"
        titles = [item["title"] for item in pack["authoritative"]]
        self.assertIn("outlet-code-ruling", titles, pack)

    def test_d2_gap_a_plain_ascii_query_cannot_reach_a_full_width_only_note(self):
        """DOCUMENTED GAP, not a trivial no-op: bm_vault.py's whole
        width-folding signal (_cjk_hits) only runs when _CJK_PROBE_RE
        matches the RAW QUERY TEXT (bm_vault.py line ~2039,
        `if _CJK_PROBE_RE.search(text):`). A plain half-width ASCII query
        like "123" carries no character in that probe's ranges at all
        (confirmed: _CJK_PROBE_RE.search("123") is None, while
        _CJK_PROBE_RE.search(u"\\uff11\\uff12\\uff13") matches), so the
        width-fold signal never even loads the analyzer module for it. The
        note-side folding JA78 added inside _cjk_hits never gets a chance
        to run, because the gate that would call it never opens. The
        result: normalization here is ONE-DIRECTIONAL in practice -- a
        full-width query reaches a half-width note (test_d1 above), but an
        ordinary half-width query can never reach a note that writes the
        same value only in full-width form. This test asserts that CURRENT
        real behavior (no match), rather than inventing a folding capability
        this retrieval path does not actually have for a pure-ASCII query.
        Flagged in the report to the caller of this test file; not silently
        worked around here."""
        fullwidth_only_note = _note(
            "outlet-code-ruling-fw",
            "the standing ruling on the outlet code, written full-width",
            "decision",
            u"The outlet code is １２３ for this location, "
            u"written full-width. This is the standing ruling.",
            extra="authority: source_of_record\npromotion: canonical\n"
                 "promoted_by: khalil\npromoted_at: 2026-01-01\n")
        pack, _raw = self._serve(
            [("/vault/outlet-code-fullwidth.md", fullwidth_only_note)],
            query="123")  # plain half-width ASCII
        titles = [item["title"] for item in pack["authoritative"]]
        self.assertNotIn("outlet-code-ruling-fw", titles, pack)
        self.assertEqual(pack["authoritative"], [], pack)


if __name__ == "__main__":
    unittest.main()
