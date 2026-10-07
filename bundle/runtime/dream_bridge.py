#!/usr/bin/env python3
'''dream_bridge: locate and load the dream recorder without importing it.

The recorder lives either beside a checkout's plugin tree, inside a bundled
runtime, or flat next to this script. This module keeps that search in one
place, refuses when the environment names a path that is not there, and loads
the file by path under a lock without publishing it in sys.modules.
Standard library only.
'''
from __future__ import annotations
import argparse
import collections.abc
import hashlib
import importlib.util
import json
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

_ENV_RECORDER = 'BROTHER_DREAM_RECORD_PATH'
_LOCK = threading.Lock()
#: what makes a loaded file a recorder; checked before it is cached, so an impostor is never returned
_REQUIRED_CALLABLES = ('record_decision', 'record_outcome')
_RECORDER = None
_RECORDER_MODULE_NAME = 'brother_dream_bridge_recorder'


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def _env_path():
    if _ENV_RECORDER in os.environ:
        return os.environ[_ENV_RECORDER]
    return None


def recorder_candidates():
    '''Every path this bridge will try, in order. The environment override is
    first when present; a checkout, a bundle, and a flat layout follow.'''
    here = _here()
    candidates = []
    env = _env_path()
    if env is not None:
        candidates.append(env)
    candidates.append(os.path.abspath(os.path.join(
        here, '..', 'plugin', 'runtime', 'brother', 'core', 'dream_record.py')))
    candidates.append(os.path.abspath(os.path.join(
        here, 'plugin', 'runtime', 'brother', 'core', 'dream_record.py')))
    candidates.append(os.path.abspath(os.path.join(here, 'dream_record.py')))
    return candidates


def recorder_path():
    '''The first existing recorder file, or RuntimeError naming what was
    tried. An environment path that does not exist is a refusal, never a
    silent fall through to the next candidate.'''
    tried = []
    env = _env_path()
    for path in recorder_candidates():
        tried.append(path)
        if os.path.isfile(path):
            return path
        if env is not None and path == env:
            raise RuntimeError(
                'dream recorder not found at %s=%r; tried: %s'
                % (_ENV_RECORDER, path, ', '.join(tried)))
    raise RuntimeError('dream recorder not found; tried: %s' % ', '.join(tried))


def recorder():
    '''The loaded recorder module. Loaded once under a lock, by path, and
    never inserted into sys.modules: a module published before exec finishes
    is a half-built module to every other thread.'''
    global _RECORDER
    if _RECORDER is not None:
        return _RECORDER
    with _LOCK:
        if _RECORDER is None:
            path = recorder_path()
            spec = importlib.util.spec_from_file_location(_RECORDER_MODULE_NAME, path)
            if spec is None or spec.loader is None:
                raise RuntimeError('could not load dream recorder from %s' % path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except (Exception, SystemExit) as exc:
                # a recorder that cannot be imported BLOCKS by name (REQ-BRIDGE); SystemExit is caught on
                # purpose, a file on a path from the environment must never be able to end the host process
                raise RuntimeError('dream recorder at %s could not be loaded: %s: %s'
                                   % (path, type(exc).__name__, exc)) from None
            if not all(callable(getattr(module, name, None)) for name in _REQUIRED_CALLABLES):
                raise RuntimeError('file at %s is not a dream recorder: it lacks %s'
                                   % (path, ', '.join(_REQUIRED_CALLABLES)))
            _RECORDER = module
    return _RECORDER


def journal_path():
    '''The journal path the loaded recorder itself selected. Delegation is
    deliberate: the recorder owns journal_candidates and journal_path, and a
    second search here would drift from it.'''
    module = recorder()
    finder = getattr(module, 'journal_path', None)
    if not callable(finder):
        path = getattr(module, '__file__', '<unknown recorder>')
        raise RuntimeError(
            'dream recorder %s has no callable journal_path()' % path)
    return finder()


def reset_for_tests():
    '''Drop the cached recorder so a test can change the environment and load
    a different file. The lock keeps a concurrent recorder() from seeing a
    half-reset cache.'''
    global _RECORDER
    with _LOCK:
        _RECORDER = None


@dataclass(frozen=True)
class EpisodeResult:
    episode_id: str
    status: str
    candidate_id: str | None
    shadow_steps_recorded: int
    stop_reason: str | None


class EpisodesLockedError(RuntimeError):
    pass


class ShadowActionSink(Protocol):
    def append(self, action: Any) -> None: ...


class EpisodeLock(Protocol):
    def acquire(self, episode_id: str, actor_id: str, ttl_ms: int) -> str: ...
    def release(self, token: str) -> None: ...


class ShadowObservationProvider(Protocol):
    def observations(self, episode_id: str) -> Sequence[Any]: ...


def _dream_policy():
    from plugin.runtime.brother.core import dream_policy
    return dream_policy


def _dream_propose():
    from plugin.runtime.brother.core import dream_propose
    return dream_propose


def _or_fanout():
    from plugin.runtime.brother.core import or_fanout
    return or_fanout


def _result(episode_id, status, candidate_id, steps, stop_reason):
    return EpisodeResult(episode_id, status, candidate_id, steps, stop_reason)


def _is_int(value):
    return type(value) is int


def _is_str(value):
    return type(value) is str


def _is_safe_id(value):
    if not _is_str(value) or not value:
        return False
    if '\x00' in value:
        return False
    if '/' in value:
        return False
    if value in ('.', '..'):
        return False
    return True


def _validate_feedback_items(feedback, max_items, now_ms):
    if not isinstance(feedback, collections.abc.Sequence) or isinstance(feedback, (str, bytes, bytearray)):
        return 'feedback is not a sequence'
    if len(feedback) == 0:
        return 'empty feedback'
    if len(feedback) > max_items:
        return 'too many feedback items'
    seen = set()
    for item in feedback:
        if not hasattr(item, 'feedback_id'):
            return 'feedback item missing feedback_id'
        fid = getattr(item, 'feedback_id')
        if not _is_str(fid) or not fid:
            return 'feedback_id is not a non-empty string'
        if fid in seen:
            return 'duplicate feedback_id'
        seen.add(fid)
        source = getattr(item, 'source', None)
        if source != 'development':
            return 'feedback source is not development'
        text = getattr(item, 'text', None)
        if not _is_str(text):
            return 'feedback text is not a string'
        tags = getattr(item, 'tags', None)
        if not isinstance(tags, (tuple, list)):
            return 'feedback tags is not a tuple or list'
        for tag in tags:
            if not _is_str(tag):
                return 'feedback tag is not a string'
        checksum = getattr(item, 'checksum_sha256', None)
        if not _is_str(checksum):
            return 'feedback checksum is not a string'
        created = getattr(item, 'created_at_ms', None)
        if not _is_int(created):
            return 'feedback created_at_ms is not an int'
        expires = getattr(item, 'expires_at_ms', None)
        if expires is not None:
            if not _is_int(expires):
                return 'feedback expires_at_ms is not an int or None'
            if expires <= now_ms:
                return 'feedback is expired'
    return None


def _validate_observation(observation, episode_id):
    if not hasattr(observation, 'episode_id'):
        return 'observation missing episode_id'
    if getattr(observation, 'episode_id') != episode_id:
        return 'episode binding mismatch'
    step_index = getattr(observation, 'step_index', None)
    if not _is_int(step_index) or step_index < 0:
        return 'step_index is not a non-negative int'
    observation_id = getattr(observation, 'observation_id', None)
    if not _is_str(observation_id) or not observation_id:
        return 'observation_id is not a non-empty string'
    features = getattr(observation, 'features', None)
    if not isinstance(features, collections.abc.Mapping):
        return 'features is not a mapping'
    observed_at_ms = getattr(observation, 'observed_at_ms', None)
    if not _is_int(observed_at_ms):
        return 'observed_at_ms is not an int'
    return None


def _validate_request(request):
    if isinstance(request, dict):
        return 'request is a dict, not a ProposalRequest'
    for name in ('episode_id', 'incumbent_policy_id', 'incumbent_signature_hash',
                 'feedback', 'limits', 'proposer_actor_id', 'idempotency_key',
                 'issued_at_ms'):
        if not hasattr(request, name):
            return 'request missing %s' % name
    if not _is_safe_id(getattr(request, 'episode_id')):
        return 'episode_id is not a safe id'
    if not _is_safe_id(getattr(request, 'incumbent_policy_id')):
        return 'incumbent_policy_id is not a safe id'
    if not _is_str(getattr(request, 'incumbent_signature_hash')) or not getattr(request, 'incumbent_signature_hash'):
        return 'incumbent_signature_hash is not a non-empty string'
    feedback = getattr(request, 'feedback')
    if not isinstance(feedback, (tuple, list)):
        return 'feedback is not a tuple or list'
    for item in feedback:
        if not hasattr(item, 'feedback_id'):
            return 'feedback item missing feedback_id'
        fid = getattr(item, 'feedback_id')
        if not _is_str(fid) or not fid:
            return 'feedback_id is not a non-empty string'
        text = getattr(item, 'text', None)
        if not _is_str(text):
            return 'feedback text is not a string'
        tags = getattr(item, 'tags', None)
        if not isinstance(tags, (tuple, list)):
            return 'feedback tags is not a tuple or list'
        for tag in tags:
            if not _is_str(tag):
                return 'feedback tag is not a string'
    if getattr(request, 'limits') is None:
        return 'limits is None'
    if not _is_str(getattr(request, 'proposer_actor_id')) or not getattr(request, 'proposer_actor_id'):
        return 'proposer_actor_id is not a non-empty string'
    if not _is_str(getattr(request, 'idempotency_key')) or not getattr(request, 'idempotency_key'):
        return 'idempotency_key is not a non-empty string'
    issued = getattr(request, 'issued_at_ms')
    if not _is_int(issued):
        return 'issued_at_ms is not an int'
    return None


def _unreadable_rows(rows):
    if rows is None:
        return True
    for row in rows:
        if not isinstance(row, dict):
            return True
        if 'record' in row and row['record'] is None:
            return True
    return False


def _is_closed(rows, episode_id):
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('kind') != 'd11.episode':
            continue
        record = row.get('record')
        if not isinstance(record, dict):
            continue
        observed = record.get('observed')
        if not isinstance(observed, dict):
            continue
        if observed.get('episode_id') == episode_id and record.get('chosen') == 'closed':
            return True
    return False


def _done_steps(rows, episode_id):
    done = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('kind') != 'd11.shadow':
            continue
        record = row.get('record')
        if not isinstance(record, dict):
            continue
        observed = record.get('observed')
        if not isinstance(observed, dict):
            continue
        if observed.get('episode_id') == episode_id:
            step_index = observed.get('step_index')
            if _is_int(step_index):
                done.add(step_index)
    return done


def _count_steps(rows, episode_id):
    return len(_done_steps(rows, episode_id))


def run_episode(episode_id, incumbent_policy_id, incumbent, feedback, limits,
                proposer_actor_id, reviewer_actor_id, dispatch, ledger, sink,
                lock, provider, run_dir, now_ms, max_feedback_items, review):
    if not _is_safe_id(episode_id):
        return _result('', 'blocked', None, 0, 'invalid episode_id')
    if not _is_safe_id(incumbent_policy_id):
        return _result(episode_id, 'blocked', None, 0, 'invalid incumbent_policy_id')
    if not _is_str(proposer_actor_id) or not proposer_actor_id:
        return _result(episode_id, 'blocked', None, 0, 'invalid proposer_actor_id')
    if not _is_str(reviewer_actor_id) or not reviewer_actor_id:
        return _result(episode_id, 'blocked', None, 0, 'invalid reviewer_actor_id')
    if not _is_str(run_dir) or not run_dir:
        return _result(episode_id, 'blocked', None, 0, 'invalid run_dir')
    if not _is_int(now_ms):
        return _result(episode_id, 'blocked', None, 0, 'invalid now_ms')
    if not _is_int(max_feedback_items) or max_feedback_items < 0:
        return _result(episode_id, 'blocked', None, 0, 'invalid max_feedback_items')
    if not hasattr(lock, 'acquire') or not hasattr(lock, 'release'):
        return _result(episode_id, 'blocked', None, 0, 'invalid lock')
    if not hasattr(sink, 'append'):
        return _result(episode_id, 'blocked', None, 0, 'invalid sink')
    if not hasattr(provider, 'observations'):
        return _result(episode_id, 'blocked', None, 0, 'invalid provider')
    record = recorder()
    token = lock.acquire(episode_id, proposer_actor_id, 120000)
    try:
        rows = record.read_decisions(run_dir)
        if _unreadable_rows(rows):
            return _result(episode_id, 'no_data', None, 0, 'journal unreadable')
        if _is_closed(rows, episode_id):
            return _result(episode_id, 'closed', None, _count_steps(rows, episode_id), None)
        feedback_error = _validate_feedback_items(feedback, max_feedback_items, now_ms)
        if feedback_error is not None:
            return _result(episode_id, 'no_data', None, 0, feedback_error)
        dream_propose = _dream_propose()
        dream_policy = _dream_policy()
        try:
            request = dream_propose.build_request(
                episode_id, incumbent_policy_id,
                getattr(incumbent, 'signature_hash', incumbent_policy_id),
                feedback, limits, proposer_actor_id, 'd11:%s' % episode_id, now_ms,
                max_feedback_items)
        except dream_propose.NoData as exc:
            return _result(episode_id, 'no_data', None, 0, str(exc))
        except ValueError as exc:
            return _result(episode_id, 'blocked', None, 0, str(exc))
        try:
            proposal = dream_propose.propose(request, dispatch, ledger, now_ms)
        except Exception as exc:
            return _result(episode_id, 'blocked', None, 0, str(exc))
        try:
            frozen = dream_policy.freeze_candidate(
                proposal.source, episode_id, proposal.candidate_id,
                incumbent_policy_id, limits, now_ms,
                entrypoint=getattr(proposal, 'entrypoint', 'decide'))
        except dream_policy.CandidateRejected as exc:
            return _result(episode_id, 'blocked', proposal.candidate_id, 0, str(exc))
        except ValueError as exc:
            return _result(episode_id, 'blocked', proposal.candidate_id, 0, str(exc))
        if review is not None:
            try:
                dream_propose.submit_review(review, frozen, ledger)
            except ValueError as exc:
                return _result(episode_id, 'blocked', proposal.candidate_id, 0, str(exc))
            except dream_propose.NoData as exc:
                return _result(episode_id, 'no_data', proposal.candidate_id, 0, str(exc))
        feedback_ids = [getattr(item, 'feedback_id', None) for item in feedback]
        open_observed = {
            'episode_id': episode_id,
            'status': 'open',
            'feedback_ids': feedback_ids,
            'code_hash': frozen.code_hash,
        }
        open_id = record.record_decision(
            run_dir, 'd11.episode', observed=open_observed,
            options=['open', 'closed', 'blocked', 'no_data'],
            chosen='open', policy_version=frozen.code_hash)
        if open_id is None:
            return _result(episode_id, 'no_data', proposal.candidate_id, 0, 'journal unwritable')
        done = _done_steps(rows, episode_id)
        steps_recorded = len(done)
        observations = provider.observations(episode_id)
        if observations is None:
            return _result(episode_id, 'no_data', proposal.candidate_id, steps_recorded, 'no observations')
        if not isinstance(observations, (list, tuple)):
            try:
                observations = list(observations)
            except TypeError:
                return _result(episode_id, 'no_data', proposal.candidate_id, steps_recorded, 'observations not iterable')
        from plugin.runtime.brother.core import dream_promote
        try:
            gate = dream_promote.PromotionController(
                dream_promote.PromotionStore(run_dir),
                dream_promote.SafetyFloor(_subcommand_floor_path()))
        except (dream_promote.NoData, dream_promote.ControlViolation, ValueError) as exc:
            return _result(episode_id, 'blocked', proposal.candidate_id, steps_recorded,
                           'promotion gate unavailable: %s' % exc)
        seen_now = set()
        for observation in observations:
            observation_error = _validate_observation(observation, episode_id)
            if observation_error is not None:
                return _result(episode_id, 'blocked', proposal.candidate_id, steps_recorded, observation_error)
            step_index = observation.step_index
            if step_index in seen_now:
                return _result(episode_id, 'blocked', proposal.candidate_id, steps_recorded, 'duplicate step_index %s' % step_index)
            seen_now.add(step_index)
            if step_index in done:
                continue
            try:
                action = dream_promote.gated_run_shadow(
                    gate, incumbent_policy_id, frozen, observation, incumbent, now_ms,
                    run_shadow=dream_policy.run_shadow)
            except (dream_promote.ControlViolation, dream_promote.CorruptStore) as exc:
                return _result(episode_id, 'blocked', proposal.candidate_id, steps_recorded, 'gate refused step %s: %s' % (step_index, exc))
            try:
                sink.append(action)
            except Exception as exc:
                return _result(episode_id, 'blocked', proposal.candidate_id, steps_recorded, 'sink failure at step %s: %s' % (step_index, exc))
            shadow_observed = {
                'episode_id': episode_id,
                'step_index': step_index,
                'observation_id': observation.observation_id,
            }
            step_id = record.record_decision(
                run_dir, 'd11.shadow', observed=shadow_observed,
                options=['shadow-recorded'], chosen='shadow-recorded',
                policy_version=frozen.code_hash)
            if step_id is None:
                return _result(episode_id, 'no_data', proposal.candidate_id, steps_recorded, 'journal unwritable')
            outcome = 'PASS' if getattr(action, 'candidate_error', None) is None else 'NO-DATA'
            detail = {'step_index': step_index, 'candidate_error': getattr(action, 'candidate_error', None)}
            if record.record_outcome(step_id, outcome, grader='d11.shadow_recorded', detail=detail) is None:
                return _result(episode_id, 'no_data', proposal.candidate_id, steps_recorded, 'journal unwritable')
            steps_recorded += 1
        close_observed = {
            'episode_id': episode_id,
            'status': 'closed',
            'feedback_ids': feedback_ids,
            'code_hash': frozen.code_hash,
        }
        close_id = record.record_decision(
            run_dir, 'd11.episode', observed=close_observed,
            options=['open', 'closed', 'blocked', 'no_data'],
            chosen='closed', policy_version=frozen.code_hash)
        if close_id is None:
            return _result(episode_id, 'no_data', proposal.candidate_id, steps_recorded, 'journal unwritable')
        return _result(episode_id, 'closed', proposal.candidate_id, steps_recorded, None)
    finally:
        lock.release(token)


class _OpenRouterDispatchAdapter:
    def __init__(self, run_dir, timeout_seconds=120, max_tokens=4096, max_slots=1, model='deepseek'):
        self.run_dir = run_dir
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.max_slots = max_slots

    def _extract_source(self, result):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                raise ValueError('dispatch answer is not JSON')
        if isinstance(result, dict):
            source = result.get('source')
            if isinstance(source, str) and source:
                return source
            answer = result.get('answer')
            if isinstance(answer, str):
                try:
                    answer = json.loads(answer)
                except (ValueError, TypeError):
                    raise ValueError('dispatch answer is not JSON')
                if isinstance(answer, dict) and isinstance(answer.get('source'), str) and answer['source']:
                    return answer['source']
        raise ValueError('dispatch answer has no source')

    def propose(self, request):
        request_error = _validate_request(request)
        if request_error is not None:
            raise ValueError(request_error)
        payload = {
            'episode_id': request.episode_id,
            'incumbent_policy_id': request.incumbent_policy_id,
            'idempotency_key': request.idempotency_key,
            'feedback': [
                {
                    'feedback_id': item.feedback_id,
                    'text': item.text,
                    'tags': list(item.tags),
                }
                for item in request.feedback
            ],
            'limits': request.limits,
        }
        prompt = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
        or_fanout = _or_fanout()
        max_prompt = getattr(or_fanout, 'MAX_PROMPT_BYTES', 200000)
        if len(prompt.encode('utf-8')) > max_prompt:
            raise ValueError('prompt over byte limit')
        out_name = 'd11_proposal_%s.txt' % hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        out_path = os.path.abspath(os.path.join(self.run_dir, out_name))
        run_root = os.path.abspath(self.run_dir)
        if not out_path.startswith(run_root + os.sep):
            raise ValueError('out outside run_dir')
        request_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        # run_job takes a job dict and owns the bridge argv, the privacy and
        # configuration gates, and the one dispatch call. It never raises on a
        # failed job: it returns a record with ok false, which must block here.
        # No sensitivity is set, so the job is private by run_job's default.
        job = {'id': 'd11-' + request_hash[:16], 'model': self.model, 'prompt': prompt,
               'out': out_name, 'max': self.max_tokens, 'expect': 'json'}
        record = or_fanout.run_job(job, self.timeout_seconds, self.max_slots, workspace_root=run_root)
        if not isinstance(record, dict) or record.get('ok') is not True:
            raise RuntimeError('dispatch failed: %s' % (record.get('error') if isinstance(record, dict) else record,))
        with open(out_path, encoding='utf-8') as handle:
            response_text = handle.read().strip()
        source = self._extract_source(response_text)
        response_hash = hashlib.sha256(response_text.encode('utf-8')).hexdigest()
        from plugin.runtime.brother.core import dream_propose
        receipt = dream_propose.DispatchReceipt(
            receipt_id=response_hash[:16],
            provider='openrouter',
            model=record.get('actual_model'),
            request_hash=request_hash,
            response_hash=response_hash,
            created_at_ms=request.issued_at_ms,
        )
        return dream_propose.CandidateProposal(
            episode_id=request.episode_id,
            candidate_id=response_hash[:16],
            incumbent_policy_id=request.incumbent_policy_id,
            base_code_hash=request.incumbent_signature_hash,
            source=source,
            code_hash=hashlib.sha256(source.encode('utf-8')).hexdigest(),
            language='python',
            entrypoint='decide',
            limits=request.limits,
            proposer_actor_id=request.proposer_actor_id,
            dispatch_receipt=receipt,
            created_at_ms=request.issued_at_ms,
        )


_SUBCOMMANDS = ('propose', 'start-canary', 'record-action', 'record-live',
                'promote', 'rollback', 'status', 'history', 'select')


def _subcommand_floor_path():
    return os.path.join('bundle', 'runtime', 'LEARNING-SAFETY-FLOOR.json')


def _emit(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + '\n')


def _read_json_file(path):
    with open(path, 'rb') as handle:
        return json.loads(handle.read().decode('utf-8'))


def _subcommand_main(args):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from plugin.runtime.brother.core import dream_promote
    name = args[0]
    parser = argparse.ArgumentParser(prog='dream_bridge ' + name)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--json', action='store_true')
    if name == 'propose':
        parser.add_argument('--manifest-json', required=True)
        parser.add_argument('--limits-json', required=True)
        parser.add_argument('--offline-score-json', required=True)
        parser.add_argument('--authorized-by', required=True)
        parser.add_argument('--authorization-digest', required=True)
        parser.add_argument('--expires-at', required=True)
        parser.add_argument('--created-at', required=True)
        parser.add_argument('--incumbent-record-id', default=None)
    elif name == 'start-canary':
        parser.add_argument('--record-id', required=True)
    elif name == 'record-action':
        parser.add_argument('--record-id', required=True)
        parser.add_argument('--actions', type=int, required=True)
        parser.add_argument('--errors', type=int, required=True)
        parser.add_argument('--cost', type=float, required=True)
        parser.add_argument('--latency-ms', type=int, required=True)
    elif name == 'record-live':
        parser.add_argument('--record-id', required=True)
        parser.add_argument('--live-score-json', required=True)
    elif name == 'promote':
        parser.add_argument('--record-id', required=True)
        parser.add_argument('--evidence-digest', required=True)
    elif name == 'rollback':
        parser.add_argument('--record-id', required=True)
        parser.add_argument('--reason', required=True)
    elif name in ('status', 'history'):
        parser.add_argument('--policy-id', default=None)
    elif name == 'select':
        parser.add_argument('--policy-id', required=True)
        parser.add_argument('--requested-actions', type=int, required=True)
        parser.add_argument('--requested-cost', type=float, required=True)
    try:
        namespace = parser.parse_args(args[1:])
    except SystemExit as exc:
        return int(exc.code or 0)
    run_dir = namespace.run_dir
    if not isinstance(run_dir, str) or run_dir == '':
        sys.stderr.write('no_data: run_dir must be a non-empty str\n')
        return 2
    store = dream_promote.PromotionStore(run_dir)
    try:
        floor = dream_promote.SafetyFloor(_subcommand_floor_path())
    except dream_promote.NoData as exc:
        _emit({'status': 'NO-DATA', 'reason': str(exc)})
        return 2
    except dream_promote.ControlViolation as exc:
        _emit({'status': 'BLOCK', 'reason': str(exc)})
        return 3
    controller = dream_promote.PromotionController(store, floor)
    try:
        if name == 'status':
            store.verify_chain()
            if namespace.policy_id is None:
                _emit({'status': 'NO-DATA'})
                return 2
            record = controller.status(namespace.policy_id)
            if record is None:
                _emit({'status': 'NO-DATA'})
                return 2
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
        if name == 'history':
            store.verify_chain()
            if namespace.policy_id is None:
                _emit({'status': 'NO-DATA'})
                return 2
            records = controller.history(namespace.policy_id)
            _emit({'records': [{'record_id': r.record_id, 'status': r.status} for r in records]})
            return 0
        if name == 'select':
            decision = controller.select_action(namespace.policy_id,
                                                namespace.requested_actions,
                                                namespace.requested_cost)
            _emit({'allow': decision.allow, 'reason': decision.reason,
                   'record_id': decision.record_id,
                   'incumbent_record_id': decision.incumbent_record_id})
            return 0
        if name == 'propose':
            manifest_data = _read_json_file(namespace.manifest_json)
            manifest_data['declares_control_edits'] = tuple(
                manifest_data.get('declares_control_edits') or ())
            manifest = dream_promote.CandidateManifest(**manifest_data)
            limits = dream_promote.Limits(**_read_json_file(namespace.limits_json))
            offline = dream_promote.Score(**_read_json_file(namespace.offline_score_json))
            record = controller.propose(manifest, limits, offline, namespace.authorized_by,
                                        namespace.authorization_digest, namespace.expires_at,
                                        namespace.created_at, namespace.incumbent_record_id)
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
        if name == 'start-canary':
            record = controller.start_canary(namespace.record_id)
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
        if name == 'record-action':
            record = controller.record_canary_action(namespace.record_id, namespace.actions,
                                                     namespace.errors, namespace.cost,
                                                     namespace.latency_ms)
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
        if name == 'record-live':
            live = dream_promote.Score(**_read_json_file(namespace.live_score_json))
            record = controller.record_live(namespace.record_id, live)
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
        if name == 'promote':
            record = controller.promote(namespace.record_id, namespace.evidence_digest)
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
        if name == 'rollback':
            record = controller.rollback(namespace.record_id, namespace.reason)
            _emit({'record_id': record.record_id, 'status': record.status})
            return 0
    except dream_promote.CorruptStore:
        _emit({'status': 'BLOCK', 'reason': 'store_corrupt'})
        return 3
    except dream_promote.NoData as exc:
        _emit({'status': 'NO-DATA', 'reason': str(exc)})
        return 2
    except ValueError as exc:
        if str(exc) == 'record not found':
            _emit({'status': 'NO-DATA', 'reason': 'record_absent'})
            return 2
        sys.stderr.write('refusal: %s\n' % exc)
        return 1
    except dream_promote.ControlViolation as exc:
        sys.stderr.write('refusal: %s\n' % exc)
        return 1
    except OSError as exc:
        sys.stderr.write('no_data: %s\n' % exc)
        return 2
    return 2


def main(argv=None):
    if argv is not None:
        if not isinstance(argv, (list, tuple)):
            sys.stderr.write('no_data: argv must be a list or tuple')
            return 2
        for item in argv:
            if not isinstance(item, str):
                sys.stderr.write('no_data: argv items must be strings')
                return 2
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if args_list and args_list[0] in _SUBCOMMANDS:
        return _subcommand_main(args_list)
    parser = argparse.ArgumentParser(prog='dream_bridge')
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--episode', required=True)
    parser.add_argument('--incumbent', required=True)
    parser.add_argument('--feedback-json', required=True)
    parser.add_argument('--limits-json', required=True)
    parser.add_argument('--proposer', required=True)
    parser.add_argument('--reviewer', required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        with open(args.feedback_json, 'rb') as handle:
            raw = handle.read()
        feedback_data = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        sys.stderr.write('no_data: %s' % exc)
        return 2
    if not isinstance(feedback_data, list) or not feedback_data:
        sys.stderr.write('no_data: empty feedback')
        return 2
    sys.stderr.write('no_data: composition ports not configured')
    return 2
