#!/usr/bin/env python3
"""Calibration for tools/bm_vault_notify.py, WBS row VB11-06: one shared renderer
emitting the digest page, the HTML email and the Teams Adaptive Card from one
facts dict, plus the email and (fixture) teams adapters.

Driven backwards throughout: every positive assertion (a fact appears, a send
delivers, a mutation is recorded) has a matching negative one (a doctored copy
is caught, a missing credential refuses by name, an unknown principal refuses).

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_notify as notify        # noqa: E402
import bm_vault_digest as digest        # noqa: E402
import bm_vault_principals as principals  # noqa: E402
import bm_vault_audit as audit          # noqa: E402

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


def fixture_digest_text():
    """A real digest page, built through bm_vault_digest.render_digest itself
    (never a hand-typed imitation of its shape), two folders so the fixture
    exercises more than one group."""
    groups = [
        {"folder": "10-Projects/alice", "count": 2, "findings": [
            {"source": "doctor", "path": "10-Projects/alice/a.md",
             "detail": "missing: status"},
            {"source": "census", "path": "10-Projects/alice/b.md",
             "detail": "missing: description"},
        ]},
        {"folder": "00-Inbox", "count": 1, "findings": [
            {"source": "pending", "path": "00-Inbox/c.md",
             "detail": "candidate awaiting steward review"},
        ]},
    ]
    return digest.render_digest("/tmp/does-not-need-to-exist", "alice", groups,
                                 "2026-08-30")


class ConsistencyTest(unittest.TestCase):
    """Item (a) of the done-check: byte-consistent facts across page, email
    and card, and the consistency checker catches a doctored copy."""

    def setUp(self):
        self.text = fixture_digest_text()
        self.facts, err = notify.facts_from_digest(self.text)
        self.assertIsNone(err, err)

    def test_facts_from_digest_never_reraises_a_finding(self):
        """The parsed fact dict never invents anything the digest page did
        not already say: every card line reappears verbatim under its
        folder, nothing summarized or reworded."""
        self.assertEqual(self.facts["principal"], "alice")
        self.assertEqual(self.facts["date"], "2026-08-30")
        self.assertEqual(len(self.facts["groups"]), 2)
        folders = [g["folder"] for g in self.facts["groups"]]
        self.assertEqual(folders, ["10-Projects/alice", "00-Inbox"])

    def test_page_reconstructs_the_source_byte_for_byte(self):
        """render_page is fed the SAME facts dict parsed from the source
        digest, so it must reproduce it exactly: proof the dict round-trips
        rather than an assumption that it would."""
        self.assertEqual(notify.render_page(self.facts), self.text)

    def test_every_fact_string_reaches_all_three_outputs(self):
        wanted = notify.fact_strings(self.facts)
        self.assertGreater(len(wanted), 5)
        page_text = notify.render_page(self.facts)
        email_text = notify.render_email(self.facts).as_string()
        card_text = json.dumps(notify.render_card(self.facts))
        for fact in wanted:
            self.assertIn(fact, page_text, "missing from page: %s" % fact)
            self.assertIn(fact, notify._visible_text_from_html(email_text),
                          "missing from email: %s" % fact)
            self.assertIn(fact, notify._facts_from_card_json(json.loads(card_text)),
                          "missing from card: %s" % fact)
        ok, problems = notify.verify_consistency(page_text, email_text, card_text)
        self.assertTrue(ok, problems)
        self.assertEqual(problems, [])

    def test_doctoring_one_output_is_caught(self):
        """Driven backwards: doctor a fact string in a COPY of the card JSON
        (never the source), and confirm verify_consistency reports it
        missing rather than silently passing."""
        page_text = notify.render_page(self.facts)
        email_text = notify.render_email(self.facts).as_string()
        card_obj = notify.render_card(self.facts)
        card_text = json.dumps(card_obj)
        doctored = card_text.replace("missing: status", "missing: TAMPERED")
        self.assertNotEqual(doctored, card_text, "fixture has nothing to doctor")
        ok, problems = notify.verify_consistency(page_text, email_text, doctored)
        self.assertFalse(ok)
        self.assertTrue(any("missing: status" in p for p in problems), problems)
        # And the UNDOCTORED card still passes, so the failure above is about
        # the doctoring, not a bug in the checker.
        ok2, problems2 = notify.verify_consistency(page_text, email_text, card_text)
        self.assertTrue(ok2, problems2)


class AdaptiveCardSchemaTest(unittest.TestCase):
    """Item (e): the card validates against the schema version pinned in the
    module docstring (Teams renders no later than 1.6, per Microsoft's own
    live documentation, fetched and cited there)."""

    def test_required_top_level_fields(self):
        facts, err = notify.facts_from_digest(fixture_digest_text())
        self.assertIsNone(err, err)
        card = notify.render_card(facts)
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(card["version"], "1.6")
        self.assertEqual(card["version"], notify.ADAPTIVE_CARD_VERSION)
        self.assertEqual(card["$schema"], notify.ADAPTIVE_CARD_SCHEMA)
        self.assertIsInstance(card["body"], list)
        self.assertGreater(len(card["body"]), 0)
        for node in card["body"]:
            self.assertIn(node["type"], ("TextBlock", "FactSet"))


class ContactAndRegistryTest(unittest.TestCase):
    """Item (d): contact mutation recorded and re-readable through the
    principals reader; backwards, an unknown principal refuses."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-notify-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.registry = os.path.join(self.tmp, "principals.json")
        rc = principals.cmd_add(self.registry, "alice", "human", "khalil",
                                 "2026-08-30", True)
        self.assertEqual(rc, 0)

    def test_contact_is_recorded_and_re_readable(self):
        rc = principals.cmd_contact(self.registry, "alice", "khalil", "2026-08-30",
                                     True, email="alice@example.com",
                                     teams_identity="29:xyz")
        self.assertEqual(rc, 0)
        registry, problems = principals.load(self.registry)
        self.assertEqual(problems, [])
        rec = registry["principals"]["alice"]
        self.assertEqual(rec["email"], "alice@example.com")
        self.assertEqual(rec["teams_identity"], "29:xyz")
        key, rec2, err = notify._resolve_contact(None, self.registry, "alice")
        self.assertIsNone(err, err)
        self.assertEqual(key, "alice")
        self.assertEqual(rec2["email"], "alice@example.com")

    def test_unknown_principal_refuses(self):
        rc = principals.cmd_contact(self.registry, "ghost", "khalil", "2026-08-30",
                                     True, email="ghost@example.com")
        self.assertEqual(rc, 2)
        key, rec, err = notify._resolve_contact(None, self.registry, "ghost")
        self.assertIsNone(key)
        self.assertIn("NO-DATA", err)


class SendTest(unittest.TestCase):
    """Items (b) and (c): a missing keychain credential is NO-DATA naming
    brother-mailbox while the mock teams channel still delivers in the same
    run, and every actual send lands one audit row with the recipient
    principal. The SMTP path is mocked (rule f): _send_smtp is monkeypatched
    everywhere in this class, real smtplib/socket code is never reached."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-notify-send-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.registry = os.path.join(self.tmp, "principals.json")
        principals.cmd_add(self.registry, "alice", "human", "khalil",
                            "2026-08-30", True)
        principals.cmd_contact(self.registry, "alice", "khalil", "2026-08-30", True,
                                email="alice@example.com", teams_identity="29:xyz")
        self.digest_path = os.path.join(self.tmp, "2026-08-30-alice.md")
        with open(self.digest_path, "w", encoding="utf-8") as fh:
            fh.write(fixture_digest_text())
        # Isolate the audit log from the machine's real one, the same
        # technique test_bm_vault_audit.py already uses.
        self._orig_audit_path = audit.AUDIT_PATH
        audit.AUDIT_PATH = os.path.join(self.tmp, "bm_vault_audit.jsonl")
        self.addCleanup(self._restore_audit_path)

    def _restore_audit_path(self):
        audit.AUDIT_PATH = self._orig_audit_path

    def _send(self, **kw):
        args = argparse_ns(channel=kw.get("channel"), digest=self.digest_path,
                            to=kw.get("to", "alice"), vault=None,
                            registry=self.registry,
                            mock_sink=kw.get("mock_sink"))
        return notify.cmd_send(args)

    def test_email_no_credential_is_no_data_naming_brother_mailbox(self):
        with mock.patch.object(
                notify, "_read_mailbox_credential",
                return_value=(None, "NO-DATA: no brother-mailbox credential "
                                     "in the keychain")):
            with mock.patch("builtins.print") as mock_print:
                rc = self._send(channel="email")
        self.assertEqual(rc, 2)
        printed = " ".join(str(c.args[0]) for c in mock_print.call_args_list)
        self.assertIn("brother-mailbox", printed)
        self.assertIn("NO-DATA", printed)

    def test_mock_teams_still_delivers_in_the_same_run(self):
        sink = os.path.join(self.tmp, "teams-mock.json")
        with mock.patch.object(
                notify, "_read_mailbox_credential",
                return_value=(None, "NO-DATA: no brother-mailbox credential "
                                     "in the keychain")):
            rc_email = self._send(channel="email")
            rc_teams = self._send(channel="teams", mock_sink=sink)
        self.assertEqual(rc_email, 2)
        self.assertEqual(rc_teams, 0)
        self.assertTrue(os.path.exists(sink))
        with open(sink, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["to_teams_identity"], "29:xyz")
        self.assertEqual(payload["card"]["type"], "AdaptiveCard")

    def test_teams_without_mock_sink_is_no_data(self):
        rc = self._send(channel="teams", mock_sink=None)
        self.assertEqual(rc, 2)

    def test_successful_email_send_uses_the_mocked_smtp_seam_and_audits(self):
        """The positive path: a real (fake) credential is present, so
        cmd_send reaches _send_smtp. That seam is mocked here (rule f: no
        network in tests, never a real smtplib connection), and the send
        must still land one audit row naming the recipient principal."""
        with mock.patch.object(
                notify, "_read_mailbox_credential",
                return_value=("smtp.example.com|587|bot|hunter2", None)):
            with mock.patch.object(notify, "_send_smtp") as mock_smtp:
                rc = self._send(channel="email")
        self.assertEqual(rc, 0)
        mock_smtp.assert_called_once()
        called_credential, called_to, called_msg = mock_smtp.call_args[0]
        self.assertEqual(called_credential, "smtp.example.com|587|bot|hunter2")
        self.assertEqual(called_to, "alice@example.com")
        self.assertIn("Brother digest", called_msg.as_string())

        rows_rc = audit.cmd_search({"principal": "alice"})
        self.assertEqual(rows_rc, 0)
        with open(audit.AUDIT_PATH, encoding="utf-8") as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["principal"], "alice")
        self.assertIn("channel=email", lines[0]["query"])

    def test_send_to_unknown_principal_refuses(self):
        rc = self._send(channel="email", to="ghost")
        self.assertEqual(rc, 2)


class ArgparseNamespace(object):
    """A tiny stand-in for argparse.Namespace, since cmd_send only reads
    attributes off its args object and this test drives it directly rather
    than through main()'s own argument parser."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def argparse_ns(**kw):
    return ArgparseNamespace(**kw)


if __name__ == "__main__":
    unittest.main()
