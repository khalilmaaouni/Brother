"""Adversarial checks for local research and probed creative asset contracts."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import mobile_design as D


class MobileDesignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.asset = self.root / 'screen.png'; self.asset.write_bytes(b'fixture media')
        self.path = self.root / 'board.json'
        self.data = {'schema': D.SCHEMA, 'screens': [
            {'id': 'second', 'app': 'Sample', 'title': 'Room return', 'flow': 'reflection', 'step': 2,
             'source': 'https://example.org/room', 'observed_at': '2026-09-12', 'notes': 'Quiet completion', 'elements': ['button']},
            {'id': 'first', 'app': 'Sample', 'title': 'Open reflection', 'flow': 'reflection', 'step': 1,
             'source': 'file:///owned/reference.png', 'observed_at': '2026-09-12', 'notes': 'Quiet entry', 'elements': ['sheet'], 'media': D.digest(self.asset)}]}
        self.save()
    def save(self):
        self.path.write_text(json.dumps(self.data))
    def test_order_filter_and_no_invented_visual_evidence(self):
        rows = D.search(self.path, 'quiet')['screens']
        self.assertEqual([r['id'] for r in rows], ['first', 'second'])
        self.assertEqual([r['visual_evidence'] for r in rows], ['PASS', 'NO-DATA'])
        self.assertEqual(D.search(self.path, element='sheet')['count'], 1)
        self.assertEqual(D.search(self.path, 'unmatched')['count'], 0)
    def test_tampered_media_refused(self):
        self.asset.write_bytes(b'changed')
        with self.assertRaises(D.Refusal): D.search(self.path)
    def test_duplicate_id_and_flow_position_refused(self):
        for field in ('id', 'step'):
            data = copy.deepcopy(self.data)
            self.data['screens'][1][field] = self.data['screens'][0][field]; self.save()
            with self.assertRaises(D.Refusal): D.search(self.path)
            self.data = data
    def test_malformed_source_date_and_step(self):
        for field, value in [('source', 'unattributed'), ('observed_at', 'today'), ('step', True), ('elements', 'button')]:
            data = copy.deepcopy(self.data)
            self.data['screens'][0][field] = value; self.save()
            with self.assertRaises(D.Refusal): D.search(self.path)
            self.data = data
    def test_brief_preserves_unmade_design_decisions(self):
        result = D.brief(self.path, 'quiet', 'Return to room')
        self.assertTrue(all(v['status'] == 'NO-DATA' for v in result['decisions'].values()))
        self.assertEqual(D.brief(self.path, 'absent', 'Outcome')['status'], 'NO-DATA')
    def probe(self, payload, **kwargs):
        value = subprocess.CompletedProcess([], 0, json.dumps(payload), '')
        with patch.object(D.subprocess, 'run', return_value=value):
            return D.inspect_media(self.asset, 'runtime', **kwargs)
    def test_media_budget_failure_and_no_fake_runtime_fps(self):
        result = self.probe({'streams': [{'codec_type': 'video', 'width': 100, 'height': 200, 'avg_frame_rate': '30/1'}], 'format': {'duration': '4'}}, max_bytes=2, max_duration=3)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['streams'][0]['fps'], 30)
        self.assertEqual(result['observations']['runtime_performance'], 'NO-DATA')
    def test_missing_duration_and_probe_remain_no_data(self):
        result = self.probe({'streams': [{'codec_type': 'video', 'avg_frame_rate': '0/0'}]}, max_duration=3)
        self.assertEqual(result['status'], 'NO-DATA')
        with patch.object(D.subprocess, 'run', side_effect=FileNotFoundError()):
            self.assertEqual(D.inspect_media(self.asset, 'prototype')['status'], 'NO-DATA')
    def test_invalid_probe_and_nonfinite_duration_refused(self):
        for data in ({'streams': []}, {'streams': [{}], 'format': {'duration': 'nan'}}):
            with self.assertRaises(D.Refusal): self.probe(data)
    def test_cli_does_not_overwrite_evidence(self):
        out = self.root / 'result.json'
        args = ['search', '--board', str(self.path), '--out', str(out)]
        self.assertEqual(D.main(args), 0)
        before = out.read_bytes()
        self.assertEqual(D.main(args), 1)
        self.assertEqual(before, out.read_bytes())


class DesignEvidenceAdapterTests(unittest.TestCase):
    """WBS-30.03: strengthened named fields, plus the staleness check that
    must catch a reference id whose target quietly changed underneath it
    (WAVE-2 Muse hostile review's own named failure mode), not only one
    that vanished."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.asset = self.root / 'screen.png'; self.asset.write_bytes(b'fixture media')
        self.path = self.root / 'board.json'
        self.data = {'schema': D.SCHEMA, 'screens': [
            {'id': 'first', 'app': 'Sample', 'title': 'Open reflection', 'flow': 'reflection', 'step': 1,
             'source': 'file:///owned/reference.png', 'observed_at': '2026-09-12', 'notes': 'Quiet entry',
             'elements': ['sheet'], 'media': D.digest(self.asset), 'device_class': 'phone',
             'locale': 'en-US', 'accessibility_observation': 'VoiceOver reads the sheet title first',
             'rationale': 'Anchors the entry-state layout decision'},
            {'id': 'second', 'app': 'Sample', 'title': 'Room return', 'flow': 'reflection', 'step': 2,
             'source': 'https://example.org/room', 'observed_at': '2026-09-12', 'notes': 'Quiet completion', 'elements': ['button']}]}
        self.save()
    def save(self):
        self.path.write_text(json.dumps(self.data))
    def test_evidence_record_captures_named_fields_never_a_verdict(self):
        record = D.evidence_record(self.path, 'first')
        self.assertEqual(record['schema'], D.EVIDENCE_SCHEMA)
        self.assertEqual(record['reference_id'], 'first')
        self.assertEqual(record['source_date'], '2026-09-12')
        self.assertEqual(record['journey_step'], {'flow': 'reflection', 'step': 1})
        self.assertEqual(record['device_class'], 'phone')
        self.assertEqual(record['locale'], 'en-US')
        self.assertEqual(record['accessibility_observation'], 'VoiceOver reads the sheet title first')
        self.assertEqual(record['rationale_for_inclusion'], 'Anchors the entry-state layout decision')
        self.assertEqual(record['media'], D.digest(self.asset))
        self.assertEqual(record['status'], 'PASS')
        self.assertNotIn('verdict', record)
        self.assertTrue(any('never' in limit and 'UX' in limit for limit in record['limits']))
    def test_evidence_record_no_media_or_named_fields_is_no_data_not_failure(self):
        record = D.evidence_record(self.path, 'second')
        self.assertEqual(record['status'], 'NO-DATA')
        self.assertEqual(record['media'], 'NO-DATA')
        for key in ('device_class', 'locale', 'accessibility_observation', 'rationale_for_inclusion'):
            self.assertEqual(record[key], 'NO-DATA')
    def test_evidence_record_unknown_id_refused(self):
        with self.assertRaises(D.Refusal): D.evidence_record(self.path, 'missing')
    def test_named_field_type_refused(self):
        for field in ('device_class', 'locale', 'accessibility_observation', 'rationale'):
            data = copy.deepcopy(self.data)
            self.data['screens'][0][field] = ''; self.save()
            with self.assertRaises(D.Refusal): D.evidence_record(self.path, 'first')
            self.data = data
        self.save()
    def record_evidence(self):
        evidence_path = self.root / 'evidence.json'
        evidence_path.write_text(json.dumps(D.evidence_record(self.path, 'first')))
        return evidence_path
    def test_staleness_matches_when_reference_is_unchanged(self):
        evidence_path = self.record_evidence()
        check = D.check_staleness(evidence_path, self.path)
        self.assertEqual(check['status'], 'PASS')
        self.assertEqual({c['name']: c['status'] for c in check['checks']},
                          {'reference_resolves': 'PASS', 'media_hash_match': 'PASS'})
    def test_staleness_catches_a_changed_but_still_present_file(self):
        """The exact Muse-named failure mode: the id still resolves, and the
        board's own inline hash is even re-synced to the new file (so board()
        itself would validate this board as internally consistent), but the
        content differs from what was recorded as evidence. A check that
        only asked 'does the id resolve' would silently pass here."""
        evidence_path = self.record_evidence()
        self.asset.write_bytes(b'changed design')
        self.data['screens'][0]['media'] = D.digest(self.asset)
        self.save()
        check = D.check_staleness(evidence_path, self.path)
        self.assertEqual(check['status'], 'FAIL')
        self.assertEqual({c['name']: c['status'] for c in check['checks']},
                          {'reference_resolves': 'PASS', 'media_hash_match': 'FAIL'})
    def test_staleness_scoped_to_content_not_path(self):
        """H1 regression: check_staleness's own docstring says it is scoped to
        hash identity only, but it used to compare the whole media dict,
        including 'path' -- which legitimately differs (digest() resolves to
        an absolute path) even when the content is unchanged. A path-only
        difference must read as NOT tampered; a genuine sha256 mismatch must
        still read as tampered."""
        evidence_path = self.record_evidence()
        alt = self.root / 'screen-alt.png'
        alt.write_bytes(self.asset.read_bytes())
        alt_media = D.digest(alt)
        self.assertNotEqual(alt_media['path'], self.data['screens'][0]['media']['path'])
        self.assertEqual(alt_media['sha256'], self.data['screens'][0]['media']['sha256'])
        self.data['screens'][0]['media'] = alt_media
        self.save()
        same_content_check = D.check_staleness(evidence_path, self.path)
        self.assertEqual(same_content_check['status'], 'PASS')
        self.assertEqual({c['name']: c['status'] for c in same_content_check['checks']},
                          {'reference_resolves': 'PASS', 'media_hash_match': 'PASS'})

        self.asset.write_bytes(b'changed design')
        self.data['screens'][0]['media'] = D.digest(self.asset)
        self.save()
        tampered_check = D.check_staleness(evidence_path, self.path)
        self.assertEqual(tampered_check['status'], 'FAIL')
        self.assertEqual({c['name']: c['status'] for c in tampered_check['checks']},
                          {'reference_resolves': 'PASS', 'media_hash_match': 'FAIL'})
    def test_staleness_catches_a_removed_reference(self):
        evidence_path = self.record_evidence()
        self.data['screens'] = [s for s in self.data['screens'] if s['id'] != 'first']; self.save()
        check = D.check_staleness(evidence_path, self.path)
        self.assertEqual(check['status'], 'FAIL')
        self.assertEqual({c['name']: c['status'] for c in check['checks']},
                          {'reference_resolves': 'FAIL', 'media_hash_match': 'NO-DATA'})
    def test_staleness_no_media_either_side_is_no_data(self):
        evidence_path = self.root / 'evidence.json'
        evidence_path.write_text(json.dumps(D.evidence_record(self.path, 'second')))
        check = D.check_staleness(evidence_path, self.path)
        self.assertEqual(check['status'], 'NO-DATA')
    def test_cli_evidence_then_check_staleness(self):
        evidence_out = self.root / 'evidence-cli.json'
        self.assertEqual(D.main(['evidence', '--board', str(self.path), '--id', 'first', '--out', str(evidence_out)]), 0)
        staleness_out = self.root / 'staleness-cli.json'
        self.assertEqual(D.main(['check-staleness', '--evidence', str(evidence_out), '--board', str(self.path), '--out', str(staleness_out)]), 0)
        self.assertEqual(json.loads(staleness_out.read_text())['status'], 'PASS')


if __name__ == '__main__': unittest.main()
