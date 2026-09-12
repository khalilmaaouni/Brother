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


if __name__ == '__main__': unittest.main()
