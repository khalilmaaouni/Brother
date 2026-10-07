import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import scripts.dream_bridge as dream_bridge


def _ships(module):
    return os.path.isfile(os.path.join(_ROOT, 'plugin', 'runtime', 'brother', 'core', module + '.py'))


# The public export does not ship the dream learning modules
# (dream_promote, dream_propose), and run_episode and the adapter cannot
# run without them there, so those tests skip (never pass) in that
# edition while the other tests still run.
if _ships('dream_promote'):
    from plugin.runtime.brother.core import dream_promote
else:
    dream_promote = None


def _write_floor(directory):
    entries = []
    for rel in ('scripts/dream_bridge.py',
                'plugin/runtime/brother/core/dream_promote.py',
                'plugin/runtime/brother/core/dream_report.py'):
        with open(os.path.join(_ROOT, rel), 'rb') as handle:
            entries.append({'path': rel, 'sha256': hashlib.sha256(handle.read()).hexdigest()})
    path = os.path.join(directory, 'floor.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'schema': 'brother-learning-safety-floor-v1',
                   'recorded_at': '2026-09-20T00:00:00+00:00',
                   'files': entries, 'protected_prefixes': []}, handle, sort_keys=True)
    return path


def _seed_canary(run_dir, floor_path, policy_id='inc'):
    floor = dream_promote.SafetyFloor(floor_path)
    controller = dream_promote.PromotionController(dream_promote.PromotionStore(run_dir), floor)
    manifest = dream_promote.CandidateManifest(
        candidate_id='cand1', candidate_digest='a' * 64, policy_id=policy_id,
        policy_digest='b' * 64, grader_id='grader1', grader_digest='c' * 64,
        cohort_id='cohort1', cohort_digest='d' * 64,
        safety_floor_digest=floor.safety_floor_digest(),
        control_plane_digest=floor.control_plane_digest(),
        declares_control_edits=(), created_at='2026-09-20T00:00:00+00:00')
    limits = dream_promote.Limits(
        max_actions=100, max_errors=1, max_cost=1.0, max_latency_ms=1000,
        max_gap=0.1, max_regression=0.05, canary_min_actions=2,
        canary_max_actions=10, expires_after_s=3600, min_recording_grace_s=60,
        min_live_n=1, min_worlds=1, max_clock_skew_s=10 ** 12)
    offline = dream_promote.Score(source='offline', value=0.8, n=100, digest='e' * 64,
                                  observed_at='2026-09-20T00:00:00+00:00')
    record = controller.propose(manifest, limits, offline, 'founder', 'f' * 64,
                                '2099-01-01T00:00:00+00:00', '2026-09-20T00:00:00+00:00', None)
    controller.start_canary(record.record_id, '2026-09-20T00:10:00+00:00')


@contextlib.contextmanager
def _gate(mode):
    # mode 'canary': a real floor and an active canary for the incumbent, so
    # the D12 gate admits each step; 'none': a real floor and an empty store;
    # 'missing': the floor path names a file that does not exist.
    if dream_promote is None:
        raise unittest.SkipTest('dream_promote is not shipped in this edition')
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = os.path.join(tmp, 'run')
        os.makedirs(run_dir)
        floor_path = os.path.join(tmp, 'absent.json') if mode == 'missing' else _write_floor(tmp)
        with mock.patch.object(dream_bridge, '_subcommand_floor_path', return_value=floor_path), \
             mock.patch.object(dream_promote, '_journal_append', return_value='ref'):
            if mode == 'canary':
                _seed_canary(run_dir, floor_path)
            yield run_dir


class _FakeRecorder:
    def __init__(self, events=None):
        self.decisions = []
        self.outcomes = []
        self.unwritable = False
        self.torn = False
        self.missing_artifact = False
        self.events = events if events is not None else []

    def read_decisions(self, run_dir):
        self.events.append('read')
        if self.torn:
            return None
        if self.missing_artifact:
            return [{'kind': 'd11.episode', 'record': None}]
        rows = []
        for d in self.decisions:
            rows.append({'kind': d['kind'], 'record': {'observed': d['observed'], 'chosen': d['chosen'], 'policy_version': d['policy_version']}})
        return rows

    def record_decision(self, run_dir, kind, observed=None, options=None, chosen=None, policy_version=None):
        if self.unwritable:
            return None
        decision = {'kind': kind, 'observed': observed, 'options': options, 'chosen': chosen, 'policy_version': policy_version}
        self.decisions.append(decision)
        return 'decision-%d' % len(self.decisions)

    def record_outcome(self, decision_id, outcome, grader=None, detail=None):
        if self.unwritable:
            return None
        self.outcomes.append((decision_id, outcome, grader, detail))
        return 'outcome-1'

    def add_decision(self, kind, observed, chosen, policy_version='hash'):
        self.decisions.append({'kind': kind, 'observed': observed, 'options': [], 'chosen': chosen, 'policy_version': policy_version})


class _FakePolicy:
    class CandidateRejected(ValueError):
        pass

    @staticmethod
    def freeze_candidate(source, episode_id, candidate_id, incumbent_policy_id, limits, now_ms, entrypoint='decide'):
        if source == 'invalid':
            raise _FakePolicy.CandidateRejected(('invalid',))
        return SimpleNamespace(code_hash='hash', source=source, limits=limits, entrypoint=entrypoint)

    @staticmethod
    def run_shadow(frozen, observation, incumbent, now_ms):
        return SimpleNamespace(candidate_error=None, candidate_action=None, execution_status='not_executed', step_index=getattr(observation, 'step_index', None))


class _FakePropose:
    class NoData(RuntimeError):
        pass

    @staticmethod
    def build_request(episode_id, incumbent_policy_id, incumbent_signature_hash, feedback, limits, proposer_actor_id, idempotency_key, now_ms, max_feedback_items):
        if not feedback:
            raise _FakePropose.NoData('empty feedback')
        return SimpleNamespace(episode_id=episode_id, incumbent_policy_id=incumbent_policy_id, feedback=tuple(feedback), limits=limits, idempotency_key=idempotency_key, proposer_actor_id=proposer_actor_id, issued_at_ms=now_ms)

    @staticmethod
    def propose(request, dispatch, ledger, now_ms):
        source = getattr(dispatch, 'source', 'def decide(o): return {}')
        return SimpleNamespace(source=source, candidate_id='c1', entrypoint='decide', limits=request.limits)

    @staticmethod
    def submit_review(review, frozen, ledger):
        if getattr(review, 'code_hash', None) != getattr(frozen, 'code_hash', None):
            raise ValueError('hash mismatch')
        return SimpleNamespace(charge_id='r1')


class _SpyLock:
    def __init__(self, events=None):
        self.acquire_calls = []
        self.release_calls = []
        self.token = 't1'
        self.error = None
        self.events = events if events is not None else []

    def acquire(self, episode_id, actor_id, ttl_ms):
        self.events.append('acquire')
        self.acquire_calls.append((episode_id, actor_id, ttl_ms))
        if self.error is not None:
            raise self.error
        return self.token

    def release(self, token):
        self.events.append('release')
        self.release_calls.append(token)


class _SpySink:
    def __init__(self):
        self.actions = []
        self.fail_at = None

    def append(self, action):
        if self.fail_at is not None and len(self.actions) == self.fail_at:
            raise RuntimeError('sink fail')
        self.actions.append(action)


class _SpyProvider:
    def __init__(self, observations):
        self._observations = observations

    def observations(self, episode_id):
        return list(self._observations)


def _feedback(feedback_id='f1', source='development', text='hello', tags=(), checksum_sha256='c'):
    return SimpleNamespace(
        feedback_id=feedback_id,
        source=source,
        text=text,
        tags=tuple(tags),
        redaction_version='v1',
        checksum_sha256=checksum_sha256,
        created_at_ms=0,
        expires_at_ms=None,
    )


def _observation(episode_id='e1', step_index=0, observation_id='o0'):
    return SimpleNamespace(
        episode_id=episode_id,
        step_index=step_index,
        observation_id=observation_id,
        features={'f': 1},
        observed_at_ms=0,
    )


class TestDreamBridge(unittest.TestCase):
    def _run(self, recorder=None, policy=None, propose=None, lock=None, sink=None, provider=None, feedback=None, review=None, now_ms=100, gate='canary'):
        recorder = recorder or _FakeRecorder()
        policy = policy or _FakePolicy
        propose = propose or _FakePropose
        lock = lock or _SpyLock()
        sink = sink or _SpySink()
        provider = provider or _SpyProvider([])
        if feedback is None:
            feedback = (_feedback(),)
        incumbent = SimpleNamespace(policy_id='inc', signature_hash='sig')
        with _gate(gate) as run_dir, \
             mock.patch.object(dream_bridge, 'recorder', lambda: recorder), \
             mock.patch.object(dream_bridge, '_dream_policy', lambda: policy), \
             mock.patch.object(dream_bridge, '_dream_propose', lambda: propose):
            return dream_bridge.run_episode(
                'e1', 'inc', incumbent, feedback, SimpleNamespace(), 'proposer', 'reviewer',
                None, None, sink, lock, provider, run_dir, now_ms, 10, review)

    def test_d11_acquires_lock_first(self):
        events = []
        lock = _SpyLock(events)
        recorder = _FakeRecorder(events)
        self._run(recorder=recorder, lock=lock)
        self.assertEqual(events[0], 'acquire')
        self.assertIn('read', events)

    def test_d11_second_actor_blocked(self):
        lock = _SpyLock()
        lock.error = dream_bridge.EpisodesLockedError('locked')
        with self.assertRaises(dream_bridge.EpisodesLockedError):
            self._run(lock=lock)

    def test_d11_closed_idempotent(self):
        recorder = _FakeRecorder()
        recorder.add_decision('d11.episode', {'episode_id': 'e1', 'status': 'closed'}, 'closed')
        result = self._run(recorder=recorder, feedback=())
        self.assertEqual(result.status, 'closed')
        self.assertEqual(len(recorder.decisions), 1)

    def test_d11_no_data_on_empty(self):
        result = self._run(feedback=())
        self.assertEqual(result.status, 'no_data')

    def test_d11_blocks_on_invalid(self):
        class _InvalidPropose(_FakePropose):
            @staticmethod
            def propose(request, dispatch, ledger, now_ms):
                return SimpleNamespace(source='invalid', candidate_id='c1', entrypoint='decide', limits=request.limits)
        result = self._run(propose=_InvalidPropose)
        self.assertEqual(result.status, 'blocked')

    def test_d11_blocks_on_journal_unwritable(self):
        recorder = _FakeRecorder()
        recorder.unwritable = True
        result = self._run(recorder=recorder)
        self.assertIn(result.status, ('no_data', 'blocked'))

    def test_d11_stops_on_sink_failure(self):
        sink = _SpySink()
        sink.fail_at = 0
        provider = _SpyProvider([_observation(step_index=0, observation_id='o0')])
        result = self._run(sink=sink, provider=provider)
        self.assertEqual(result.status, 'blocked')
        self.assertIn('0', result.stop_reason)

    def test_d11_one_action_per_observation(self):
        sink = _SpySink()
        provider = _SpyProvider([
            _observation(step_index=0, observation_id='o0'),
            _observation(step_index=1, observation_id='o1'),
        ])
        self._run(sink=sink, provider=provider)
        self.assertEqual([a.step_index for a in sink.actions], [0, 1])

    def test_d12_gate_missing_floor_blocks_before_any_action(self):
        sink = _SpySink()
        provider = _SpyProvider([_observation(step_index=0, observation_id='o0')])
        result = self._run(sink=sink, provider=provider, gate='missing')
        self.assertEqual(result.status, 'blocked')
        self.assertIn('floor_file_missing', result.stop_reason)
        self.assertEqual(sink.actions, [])

    def test_d12_gate_denial_blocks_without_raising(self):
        sink = _SpySink()
        provider = _SpyProvider([_observation(step_index=0, observation_id='o0')])
        result = self._run(sink=sink, provider=provider, gate='none')
        self.assertEqual(result.status, 'blocked')
        self.assertIn('gate refused step 0: action_denied:store_no_data', result.stop_reason)
        self.assertEqual(sink.actions, [])

    def test_d11_resumes_partially_done(self):
        recorder = _FakeRecorder()
        recorder.add_decision('d11.shadow', {'episode_id': 'e1', 'step_index': 0, 'observation_id': 'o0'}, 'shadow-recorded')
        sink = _SpySink()
        provider = _SpyProvider([
            _observation(step_index=0, observation_id='o0'),
            _observation(step_index=1, observation_id='o1'),
        ])
        self._run(recorder=recorder, sink=sink, provider=provider)
        self.assertEqual([a.step_index for a in sink.actions], [1])

    def test_d11_writes_reference_not_body(self):
        recorder = _FakeRecorder()
        provider = _SpyProvider([_observation(step_index=0, observation_id='o0')])
        self._run(recorder=recorder, provider=provider)
        for decision in recorder.decisions:
            if decision['kind'] == 'd11.shadow':
                self.assertNotIn('source', decision['observed'])
                self.assertIn('policy_version', decision)

    @unittest.skipUnless(_ships('dream_propose'), 'dream_propose is not shipped in this edition')
    def test_d11_adapter_uses_gated_shape(self):
        # the fake keeps run_job's real signature, so a call shaped for another
        # function fails here (a **kwargs fake once pinned exactly that bug)
        class _FakeOrFanout:
            MAX_PROMPT_BYTES = 200000
            calls = []
            @classmethod
            def run_job(cls, job, timeout, workers, dispatch=None, workspace_root=None):
                cls.calls.append((job, timeout, workers, workspace_root))
                with open(os.path.join(workspace_root, job['out']), 'w', encoding='utf-8') as handle:
                    handle.write('{"source": "def decide(o): return {}"}')
                return {'ok': True, 'actual_model': 'm'}
        with tempfile.TemporaryDirectory() as run_dir:
            with mock.patch.object(dream_bridge, '_or_fanout', lambda: _FakeOrFanout):
                adapter = dream_bridge._OpenRouterDispatchAdapter(run_dir)
                request = SimpleNamespace(
                    episode_id='e1', incumbent_policy_id='inc', incumbent_signature_hash='sig',
                    feedback=(), limits=SimpleNamespace(), proposer_actor_id='p', idempotency_key='k',
                    issued_at_ms=0, model=None)
                adapter.propose(request)
        self.assertEqual(len(_FakeOrFanout.calls), 1)
        job, timeout, workers, root = _FakeOrFanout.calls[0]
        self.assertEqual(set(job), {'id', 'model', 'prompt', 'out', 'max', 'expect'})
        self.assertNotIn('sensitivity', job)
        self.assertEqual(root, os.path.abspath(run_dir))

    def test_d11_adapter_extracts_deterministically(self):
        adapter = dream_bridge._OpenRouterDispatchAdapter('/tmp/run')
        self.assertEqual(adapter._extract_source({'source': 'x'}), adapter._extract_source({'source': 'x'}))
        for bad in (None, 123, b'bytes', [], {}, {'source': 123}):
            with self.assertRaises(ValueError):
                adapter._extract_source(bad)

    def test_d11_cli_has_no_sealed_flag(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            code = dream_bridge.main(['--help'])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn('sealed', text)
        self.assertIn('--run-dir', text)
        self.assertIn('--feedback-json', text)

    def test_d11_cli_exit_2_on_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'feedback.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('[]')
            err = io.StringIO()
            with mock.patch('sys.stderr', err):
                code = dream_bridge.main([
                    '--run-dir', tmp, '--episode', 'e1', '--incumbent', 'inc',
                    '--feedback-json', path, '--limits-json', path,
                    '--proposer', 'p', '--reviewer', 'r'])
            self.assertEqual(code, 2)
            self.assertIn('no_data', err.getvalue())

    def test_d11_shadow_never_scores(self):
        self.assertFalse(hasattr(dream_bridge, 'dream_replay'))
        self.assertNotIn('compare', dream_bridge.run_episode.__code__.co_names)

    def test_d11_quarantines_torn_and_unreadable(self):
        recorder = _FakeRecorder()
        recorder.torn = True
        result = self._run(recorder=recorder)
        self.assertEqual(result.status, 'no_data')
        recorder = _FakeRecorder()
        recorder.missing_artifact = True
        result = self._run(recorder=recorder)
        self.assertEqual(result.status, 'no_data')


class TestDreamBridgeHostile(unittest.TestCase):
    def test_d11_main_hostile_argv_refused(self):
        for argv in ([True], (float('nan'),), [0], [b'x'], [['x']], (item for item in ('--help',)), 5):
            err = io.StringIO()
            with mock.patch('sys.stderr', err):
                code = dream_bridge.main(argv)
            self.assertEqual(code, 2, repr(argv))
            self.assertIn('no_data', err.getvalue(), repr(argv))

    def test_d11_adapter_rejects_hostile_request(self):
        adapter = dream_bridge._OpenRouterDispatchAdapter('/tmp/run')
        valid = SimpleNamespace(
            episode_id='e1', incumbent_policy_id='inc', incumbent_signature_hash='sig',
            feedback=(_feedback(),), limits=SimpleNamespace(), proposer_actor_id='p',
            idempotency_key='k', issued_at_ms=0, model=None)
        hostile = [
            None, 0, True, {}, [],
            SimpleNamespace(**{**valid.__dict__, 'issued_at_ms': True}),
            SimpleNamespace(**{**valid.__dict__, 'feedback': (SimpleNamespace(feedback_id='f1', text=b'x', tags=()),)}),
            SimpleNamespace(**{**valid.__dict__, 'feedback': (item for item in ())}),
            SimpleNamespace(**{**valid.__dict__, 'issued_at_ms': float('nan')}),
            SimpleNamespace(**{**valid.__dict__, 'episode_id': '../x'}),
            SimpleNamespace(**{**valid.__dict__, 'feedback': 'not-a-sequence'}),
            SimpleNamespace(**{**valid.__dict__, 'feedback': (SimpleNamespace(feedback_id=['unhashable'], text='x', tags=()),)}),
        ]
        for bad in hostile:
            with self.assertRaises(ValueError, msg=repr(bad)):
                adapter.propose(bad)

    def test_d11_run_episode_rejects_hostile_args(self):
        cases = [
            {'now_ms': True},
            {'feedback': (_feedback(feedback_id=b'bytes'),)},
            {'feedback': (item for item in (_feedback(),))},
            {'now_ms': float('nan')},
            {'episode_id': '../x'},
            {'feedback': 'not-a-sequence'},
            {'feedback': (_feedback(feedback_id=['unhashable']),)},
        ]
        for override in cases:
            kwargs = dict(recorder=_FakeRecorder(), feedback=(_feedback(),), now_ms=100)
            kwargs.update(override)
            if 'episode_id' in override:
                recorder = _FakeRecorder()
                with mock.patch.object(dream_bridge, 'recorder', lambda: recorder), \
                     mock.patch.object(dream_bridge, '_dream_policy', lambda: _FakePolicy), \
                     mock.patch.object(dream_bridge, '_dream_propose', lambda: _FakePropose):
                    result = dream_bridge.run_episode(
                        override['episode_id'], 'inc', SimpleNamespace(policy_id='inc', signature_hash='sig'),
                        kwargs['feedback'], SimpleNamespace(), 'proposer', 'reviewer',
                        None, None, _SpySink(), _SpyLock(), _SpyProvider([]), '/tmp/run',
                        kwargs['now_ms'], 10, None)
            else:
                result = TestDreamBridge()._run(**kwargs)
            self.assertIn(result.status, ('blocked', 'no_data'), repr(override))
            self.assertNotEqual(result.status, 'closed', repr(override))

    def test_d11_duplicate_step_blocked(self):
        provider = _SpyProvider([
            _observation(step_index=0, observation_id='o0'),
            _observation(step_index=0, observation_id='o1'),
        ])
        result = TestDreamBridge()._run(provider=provider)
        self.assertEqual(result.status, 'blocked')
        self.assertIn('duplicate step_index', result.stop_reason)

    def test_d11_observation_id_none_blocked(self):
        provider = _SpyProvider([_observation(step_index=0, observation_id=None)])
        result = TestDreamBridge()._run(provider=provider)
        self.assertEqual(result.status, 'blocked')
        self.assertIn('observation_id', result.stop_reason)

    def test_d11_step_index_str_blocked(self):
        provider = _SpyProvider([_observation(step_index='0', observation_id='o0')])
        result = TestDreamBridge()._run(provider=provider)
        self.assertEqual(result.status, 'blocked')
        self.assertIn('step_index', result.stop_reason)


if __name__ == '__main__':
    unittest.main()
