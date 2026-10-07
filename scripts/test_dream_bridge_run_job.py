"""The D11 proposal adapter against the REAL or_fanout.run_job.

Finding 2026-10-02: _OpenRouterDispatchAdapter.propose called run_job with the
nine keyword arguments of openrouter_dispatch.dispatch, so every proposal died
with TypeError before anything was sent. The d11c test stubbed run_job as
**kwargs, which accepts any shape, so it pinned the bug instead of catching it.

Here run_job is the real function. Only the bridge process is stubbed
(openrouter_dispatch.dispatch), so no network and no model is called. Each
case isolates one condition: a good answer, a failed bridge, and the privacy
gate refusing an unlabelled job.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scripts.dream_bridge as dream_bridge
from plugin.runtime.brother.core import or_fanout

SOURCE = 'def decide(o): return {}'


def _request():
    return SimpleNamespace(
        episode_id='e1', incumbent_policy_id='inc', incumbent_signature_hash='sig',
        feedback=(SimpleNamespace(feedback_id='f1', text='hello', tags=()),),
        limits={'max_steps': 1}, proposer_actor_id='p', idempotency_key='k',
        issued_at_ms=0)


class _OpenRouter:
    """A router that lets the job through, so the case tests the adapter's
    contract with run_job and nothing else."""
    PRIVATE = 'private'

    def assert_may_send(self, name, sensitivity, kind):
        return True


class _ModelCall:
    def config_admit(self, transport, model):
        return None

    def config_outcome(self, *args, **kwargs):
        return False


class TestAdapterDrivesRealRunJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = self.tmp.name
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _dispatch(self, stdout, returncode=0):
        def dispatch(**kwargs):
            self.calls.append(kwargs)
            return subprocess.CompletedProcess(kwargs['bridge_argv'], returncode, stdout, ''), 'deepseek/x'
        return dispatch

    def _propose(self, dispatch, gates=True):
        patches = [mock.patch.object(or_fanout.openrouter_dispatch, 'dispatch', dispatch)]
        if gates:
            patches += [mock.patch.object(or_fanout, '_router', lambda: _OpenRouter()),
                        mock.patch.object(or_fanout, '_model_call', lambda: _ModelCall())]
        for p in patches:
            p.start()
        try:
            return dream_bridge._OpenRouterDispatchAdapter(self.run_dir).propose(_request())
        finally:
            for p in patches:
                p.stop()

    # the public export ships the adapter without dream_propose, which builds the
    # proposal; the two failure cases below still run there
    @unittest.skipUnless(importlib.util.find_spec('plugin.runtime.brother.core.dream_propose'),
                         'dream_propose is not in this tree')
    def test_good_answer_becomes_a_proposal(self):
        proposal = self._propose(self._dispatch(json.dumps({'source': SOURCE})))
        self.assertEqual(proposal.source, SOURCE)
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]['bridge_argv']
        # the prompt reaches the bridge after "--", and the bridge is the model bridge, not this script
        self.assertEqual(argv[1], or_fanout.BRIDGE_PATH)
        self.assertIn('"episode_id":"e1"', argv[argv.index('--') + 1])
        self.assertEqual(proposal.dispatch_receipt.model, 'deepseek/x')
        # the answer was written under run_dir and nowhere else
        written = [n for n in os.listdir(self.run_dir) if n.startswith('d11_proposal_')]
        self.assertEqual(len(written), 1)

    def test_failed_bridge_raises(self):
        with self.assertRaises(RuntimeError) as caught:
            self._propose(self._dispatch('', returncode=1))
        self.assertIn('bridge exit 1', str(caught.exception))

    def test_unlabelled_job_is_refused_by_the_real_privacy_gate(self):
        # no router stub: the job carries no sensitivity, so it is private, and an
        # OpenRouter model may not receive it. Nothing reaches the bridge.
        with self.assertRaises(RuntimeError):
            self._propose(self._dispatch(json.dumps({'source': SOURCE})), gates=False)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
