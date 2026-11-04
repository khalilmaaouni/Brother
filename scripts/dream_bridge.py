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
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
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


# ---------------------------------------------------------------------------
# THE LEARNING DOOR'S HANDLERS (OUT1.i). scripts/door.py's learning route runs
# `dream_bridge.py door HANDLER` with the person's typed inputs as one JSON
# object on stdin and reads one JSON line back. Each handler asks the EXISTING
# learning machinery and says plainly what it found:
#   lessons   the founder's own CLI, bm_learn.py (and bm_recurrence.py), run
#             as a subprocess; which rule is established, which candidate is
#             only proposed, and what a forget leaves behind is decided by
#             bm_learning.py (lesson_standing, lessons_view), never here
#   decisions the recorder this file already locates (dream_record), and its
#             report and replay beside it (dream_report, dream_replay)
# Nothing here learns, ranks, promotes, approves or replays by its own rule, and
# nothing calls a model: every handler is a read of local files or one local
# write the person asked for. A verdict is PASS only with a reference to what
# was read or written; an empty, missing or unreadable answer is NO-DATA.
# ---------------------------------------------------------------------------
DOOR_TIMEOUT_S = 120
#: handler -> the inputs it cannot start without
DOOR_HANDLERS = {
    'learning_existing_report': (),
    'learning_existing_reuse': ('task',),
    'learning_existing_capture': ('trigger', 'action'),
    'learning_existing_recurrence': (),
    'learning_existing_forget': ('rule', 'answer'),
    'learning_existing_decisions': ('run_dir',),
    'learning_existing_replay': ('run_dir', 'alternative'),
    'learning_existing_decision': ('run_dir', 'kind', 'options', 'chosen', 'policy_version'),
    'learning_existing_outcome': ('run_dir', 'decision', 'status', 'grader'),
}
REPORT_PROVENANCE_LIMIT = 25  # lessons whose provenance is read one by one; the rest are listed without it
_RULE_REF = re.compile(r'[0-9a-f]{6,32}')
FORGET_NOT_COVERED = ('Not covered, because it lives outside this store: copies in Vault notes, exported or '
                      'archived journals, handover packs and backups, and any session that was already shown the '
                      'lesson (it keeps what it was shown until it ends). New retrievals read the store.')


class _Stop(Exception):
    """A handler's early, honest answer: the verdict and the sentence the person reads."""

    def __init__(self, verdict, output):
        Exception.__init__(self, output)
        self.verdict, self.output = verdict, output


def _reply(verdict, output, refs=()):
    return {'verdict': verdict, 'output': output, 'evidence_refs': list(refs) if verdict == 'PASS' else []}


def _text(value, limit=2000):
    """A typed sentence safe to hand a command line or print: a non-empty string without a control, line separator
    or paragraph separator character. None otherwise."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return None
    if any(unicodedata.category(ch) in ('Cc', 'Zl', 'Zp') for ch in value):
        return None
    return value


def _tools_dir():
    """The folder holding bm_learn.py: the checkout's products/brothermode/tools, else the installed runtime's
    hooks/brothermode/tools. None when neither does."""
    here = _here()
    for path in (os.path.join(here, '..', 'products', 'brothermode', 'tools'),
                 os.path.join(here, 'hooks', 'brothermode', 'tools')):
        path = os.path.abspath(path)
        if os.path.isfile(os.path.join(path, 'bm_learn.py')):
            return path
    return None


def _load_tool(tools, name):
    """A lesson tool module, loaded by path, or a _Stop naming why not."""
    path = os.path.join(tools, name + '.py')
    spec = importlib.util.spec_from_file_location('brother_door_' + name, path)
    if spec is None or spec.loader is None or not os.path.isfile(path):
        raise _Stop('NO-DATA', 'the lesson tools are incomplete here: %s.py cannot be loaded' % name)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # a file that cannot be read is NO-DATA, never a quiet pass
        raise _Stop('NO-DATA', '%s.py could not be loaded: %s: %s' % (name, type(exc).__name__, exc))
    return module


def _tool_run(tools, script, argv, env=None):
    """(exit code, stdout, stderr) of one lesson tool, run in the person's own folder."""
    proc = subprocess.run([sys.executable, '-B', os.path.join(tools, script)] + argv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          timeout=DOOR_TIMEOUT_S, env=env)
    return proc.returncode, proc.stdout, proc.stderr


class _Learn(object):
    """One handler's conversation with the lesson tools: every call is recorded as an evidence reference (the tool,
    its exit code and the sha256 of exactly what it printed), and a call that cannot answer stops the handler."""

    def __init__(self, tools, run):
        self.tools, self.run, self.refs = tools, run, []
        self.module = None

    def lessons(self):
        if self.module is None:
            self.module = _load_tool(self.tools, 'bm_learning')
        return self.module

    def ask(self, script, argv, ok=(0,), env=None):
        """(exit code, stdout) of one tool call; an exit outside `ok` stops the handler with the tool's own words."""
        label = '%s %s' % (script, argv[0] if argv else '')
        try:
            code, out, err = self.run(script, argv, env)
        except subprocess.TimeoutExpired:
            raise _Stop('NO-DATA', '%s did not finish within %d seconds' % (label, DOOR_TIMEOUT_S))
        except OSError as exc:
            raise _Stop('NO-DATA', '%s could not start: %s' % (label, exc))
        out, err = out or '', err or ''
        if code not in ok:
            tail = (err.strip() or out.strip()).splitlines()
            words = tail[-1] if tail else 'no output'
            if code == 1 and 'Traceback (most recent call last)' not in err:
                raise _Stop('FAIL', '%s refused: %s' % (label, words))
            raise _Stop('NO-DATA', '%s exited %s: %s' % (label, code, words))
        self.refs.append('%s exit %d stdout sha256:%s' % (label, code,
                                                          hashlib.sha256(out.encode('utf-8', 'replace')).hexdigest()))
        return code, out

    def json(self, script, argv, kind, ok=(0,), env=None):
        """The JSON a tool printed, which must be a `kind` (list or dict); anything else stops the handler."""
        _code, out = self.ask(script, argv, ok, env)
        try:
            value = json.loads(out)
        except ValueError:
            value = None
        if not isinstance(value, kind):
            raise _Stop('FAIL', '%s %s answered unreadably' % (script, argv[0]))
        return value


def _line(text, limit=160):
    """One printable line, control and format characters removed and long text cut: bm_learning.safe_display's
    rule. ponytail: a four line second copy, so the text helpers below need no handle to the loaded lesson module;
    fold it into safe_display if that handle is ever threaded through _rule_block."""
    flat = ''.join(' ' if unicodedata.category(ch) in ('Cc', 'Cf', 'Zl', 'Zp') else ch for ch in str(text or ''))
    flat = ' '.join(flat.split())
    return flat if len(flat) <= limit else flat[:limit - 3].rstrip() + '...'


def _scope(rule):
    if rule.get('scope_type') == 'global':
        return 'global'
    return '%s:%s' % (rule.get('scope_type'), rule.get('scope_key'))


def _rule_block(rule, number=None):
    head = '%s%s [%s, %s%s]' % ('%d. ' % number if number else '', str(rule.get('rule_uuid', ''))[:8], _scope(rule),
                                rule.get('state'), ', gate' if rule.get('severity') == 'gate' else '')
    lines = [head, '   When: %s' % _line(rule.get('trigger_text')), '   Do  : %s' % _line(rule.get('action_text'))]
    if rule.get('because_text'):
        lines.append('   Why : %s' % _line(rule.get('because_text')))
    return lines


def _h_report(q, i):
    """What was learned: every stored lesson grouped by how it stands, with where it came from, where it applies,
    what it disagrees with, what is retired and which proposals are still only proposals."""
    rules = q.json('bm_learn.py', ['rules', '--json', '--all'], list)
    pending = q.json('bm_learn.py', ['candidates', '--json'], list)
    review = q.json('bm_learn.py', ['candidates', '--status', 'under_review', '--json'], list)
    conflicts = q.json('bm_learn.py', ['conflicts', '--json'], dict)
    applications = q.json('bm_learn.py', ['applications', '--json'], list)
    try:
        view = q.lessons().lessons_view(rules, pending + review, conflicts, applications)
    except ValueError as exc:
        raise _Stop('FAIL', 'the lesson store answered in a shape this report cannot read: %s' % exc)
    established, proposed = view['established'], view['proposed']
    if not (established or view['in_conflict'] or view['retired'] or view['forgotten'] or proposed):
        raise _Stop('NO-DATA', 'nothing has been learned yet: the store holds no lesson and no proposal')
    out = ['LESSONS: %d established, %d proposed (not approved), %d in conflict, %d retired, %d forgotten'
           % (len(established), len(proposed), len(view['in_conflict']), len(view['retired']), view['forgotten'])]
    out.append('')
    out.append('ESTABLISHED (approved by the owner; only these are ever applied to a task)')
    for number, rule in enumerate(established, 1):
        out.extend(_rule_block(rule, number))
        out.append('   Applies in: %s%s' % (_scope(rule), ', domain %s' % rule['domain'] if rule.get('domain') else ''))
        out.append('   Approved: %s, version %s' % (rule.get('founder_approved_at') or 'NO-DATA',
                                                    rule.get('current_version')))
        if number <= REPORT_PROVENANCE_LIMIT:
            why = q.json('bm_learn.py', ['why', str(rule.get('rule_uuid')), '--json'], dict)
            evidence = [e for e in (why.get('evidence') or []) if isinstance(e, dict)]
            versions = [v for v in (why.get('versions') or []) if isinstance(v, dict)]
            out.append('   Source: %s' % ('; '.join('%s %s (%s)' % (e.get('polarity'), e.get('evidence_type'),
                                                                     _line(e.get('source_ref'), 80))
                                                    for e in evidence) or 'NO-DATA: no evidence row'))
            out.append('   History: %d version(s), latest "%s"' % (len(versions), _line(
                versions[-1].get('change_reason') if versions else '', 80)))
        else:
            out.append('   Source: not read (only the first %d lessons are traced; ask for it by id)'
                       % REPORT_PROVENANCE_LIMIT)
        if rule.get('doubt'):
            out.append('   Doubt: followed, then %s' % ', '.join('%s x%d' % kv for kv in sorted(rule['doubt'].items())))
        if rule.get('conflicts_with'):
            out.append('   CONFLICTS with: %s' % ', '.join(str(u)[:8] for u in rule['conflicts_with']))
    if not established:
        out.append('(none)')
    if view['in_conflict'] or view['retired']:
        out.append('')
        out.append('STALE OR DISPUTED (do not apply; kept as history)')
        for rule in view['in_conflict'] + view['retired']:
            out.extend(_rule_block(rule))
            out.append('   Successor: %s' % (str(rule.get('superseded_by'))[:8] if rule.get('superseded_by')
                                              else 'none recorded'))
    out.append('')
    out.append('PROPOSED (nobody has approved these; they are never applied until the owner says yes)')
    for cand in proposed:
        out.append('%s [%s, %s] When: %s | Do: %s' % (str(cand.get('candidate_uuid', ''))[:8],
                                                      cand.get('proposed_scope_type'), cand.get('status'),
                                                      _line(cand.get('proposed_trigger'), 100),
                                                      _line(cand.get('proposed_action'), 100)))
    if not proposed:
        out.append('(none)')
    if view['unreadable']:
        out.append('')
        out.append('UNREADABLE: %d row(s) the store returned could not be placed, so they are not counted above'
                   % view['unreadable'])
    out.append('')
    out.append('This report reads the stored lessons only: no model was asked, nothing was changed or promoted.')
    return _reply('PASS', '\n'.join(out), q.refs)


def _h_reuse(q, i):
    """The approved lessons that apply to a task, each with why it matched; a lesson that cannot apply is never
    served, whatever the tool returned."""
    argv = ['lookup', '--query', i['task'], '--json']
    for flag in ('project', 'domain', 'artifact', 'tool'):
        if i.get(flag) is not None:
            value = _text(i[flag], 200)
            if value is None:
                raise _Stop('NO-DATA', '%s must be plain text; nothing was looked up' % flag)
            argv += ['--' + flag, value]
    found = q.json('bm_learn.py', argv, dict)
    results = found.get('results')
    if not isinstance(results, list):
        raise _Stop('FAIL', 'bm_learn.py lookup answered without a list of results')
    lessons = q.lessons()
    served, withheld = [], []
    for rule in results:
        if isinstance(rule, dict) and lessons.lesson_standing('rule', rule.get('state'))['applies']:
            served.append(rule)
        else:
            withheld.append(rule)
    out = []
    for number, rule in enumerate(served, 1):
        out.extend(_rule_block(rule, number))
        why = rule.get('why') if isinstance(rule.get('why'), dict) else {}
        out.append('   Applies because: matched %s; scope %s'
                   % (', '.join(str(t) for t in (why.get('matched_terms') or [])) or 'no listed term',
                      why.get('scope') or _scope(rule)))
    for rule in withheld:
        row = rule if isinstance(rule, dict) else {}
        out.append('WITHHELD %s: its state is %r, so it is not offered' % (str(row.get('rule_uuid', '?'))[:8],
                                                                         row.get('state')))
    if found.get('conflicts'):
        out.append('CONFLICT: %d pair(s) of these lessons disagree; the owner decides which stands, this door does '
                   'not.' % len(found['conflicts']))
    if not served:
        raise _Stop('NO-DATA', '\n'.join(out + ['no approved lesson applies to this task']))
    out.append('Only owner-approved lessons are offered. Nothing was recorded as applied.')
    return _reply('PASS', '\n'.join(out), q.refs)


def _h_capture(q, i):
    """Capture one lesson as a CANDIDATE: a proposal, never a rule."""
    argv = ['capture', '--trigger', i['trigger'], '--action', i['action'], '--json']
    for flag in ('because', 'domain'):
        if i.get(flag) is not None:
            value = _text(i[flag])
            if value is None:
                raise _Stop('NO-DATA', '%s must be plain text; nothing was captured' % flag)
            argv += ['--' + flag, value]
    cand = q.json('bm_learn.py', argv, dict)
    uuid = cand.get('candidate_uuid')
    if not isinstance(uuid, str) or not uuid:
        raise _Stop('FAIL', 'bm_learn.py capture answered without a candidate id')
    standing = q.lessons().lesson_standing('candidate', cand.get('status'))
    if standing['standing'] != 'proposed' or standing['applies']:
        raise _Stop('FAIL', 'the candidate %s came back as %r, not as a proposal; do not rely on it'
                    % (uuid[:8], cand.get('status')))
    q.refs.append('candidate %s' % uuid)
    return _reply('PASS', 'captured candidate %s as a PROPOSAL (not approved). It will not be applied to any task '
                          'until the owner approves it through the usual question and receipt.' % uuid[:8], q.refs)


def _recurrence_db(q):
    """The recurrence receipt store this machine would read, or None when the module cannot say. A read must not
    create it, so the handler looks first."""
    try:
        return _load_tool(q.tools, 'bm_recurrence').default_db_path()
    except (_Stop, OSError, ValueError):
        return None


def _h_recurrence(q, i):
    """Whether retrieved lessons reached the work in time: bm_recurrence's own report, unchanged."""
    path = _recurrence_db(q)
    if path is None:
        raise _Stop('NO-DATA', 'the recurrence tool cannot name its receipt store here, so nothing was read')
    if not os.path.isfile(path):
        raise _Stop('NO-DATA', 'no recurrence receipt has been filed on this machine, so there is nothing to report '
                               '(nothing was created)')
    _code, out = q.ask('bm_recurrence.py', ['--db', path, 'report'])
    if not re.search(r'^pre-action memory application rate: ', out, re.M):
        raise _Stop('NO-DATA', out.strip() or 'the recurrence report printed nothing')
    return _reply('PASS', out.strip(), q.refs)


def _lookup_ids(q, rule):
    """uuids of every rule a lookup for this rule's own words serves, in the rule's own scope."""
    argv = ['lookup', '--query', ('%s %s' % (rule.get('trigger_text') or '', rule.get('action_text') or ''))[:400],
            '--json']
    if rule.get('scope_type') in ('project', 'domain', 'artifact', 'relationship', 'tool') and rule.get('scope_key'):
        argv += ['--' + rule['scope_type'], str(rule['scope_key'])]
    served = q.json('bm_learn.py', argv, dict).get('results')
    if not isinstance(served, list):
        raise _Stop('FAIL', 'bm_learn.py lookup answered without a list of results')
    return {r.get('rule_uuid') for r in served if isinstance(r, dict)}


def _h_forget(q, i):
    """Forget one approved lesson on the owner's answer, then check what stopped serving it and say what stays."""
    ref, answer = i['rule'], _text(i['answer'])
    because = '' if i.get('because') in (None, '') else _text(i['because'])
    if not isinstance(ref, str) or not _RULE_REF.fullmatch(ref):
        raise _Stop('NO-DATA', 'rule must be the lesson id (6 to 32 hex digits); nothing was forgotten')
    if answer is None or because is None:
        raise _Stop('NO-DATA', 'the answer (and the reason, when given) must be plain text; nothing was forgotten')
    rules = q.json('bm_learn.py', ['rules', '--json', '--all'], list)
    matches = [r for r in rules if isinstance(r, dict) and str(r.get('rule_uuid', '')).startswith(ref)]
    if len(matches) != 1:
        raise _Stop('NO-DATA', '%d lessons match %s; nothing was forgotten' % (len(matches), ref))
    rule = matches[0]
    uuid = rule['rule_uuid']
    if not q.lessons().lesson_standing('rule', rule.get('state'))['applies']:
        raise _Stop('NO-DATA', 'lesson %s is %s already, so there is nothing to forget' % (uuid[:8], rule.get('state')))
    served_before = uuid in _lookup_ids(q, rule)
    receipt = q.json('bm_learn.py', ['grant-state-receipt', 'forget', uuid, '--answer', answer, '--because', because,
                                     '--json'], dict)
    token = receipt.get('token')
    if not isinstance(token, str) or not token:
        raise _Stop('FAIL', 'the owner-answer receipt came back without a token; nothing was forgotten')
    q.ask('bm_learn.py', ['forget', uuid, '--yes', '--because', because],
          env=dict(os.environ, BM_STATE_CHANGE_RECEIPT=token))
    live = {r.get('rule_uuid') for r in q.json('bm_learn.py', ['rules', '--json'], list) if isinstance(r, dict)}
    every = {r.get('rule_uuid'): r for r in q.json('bm_learn.py', ['rules', '--json', '--all'], list)
             if isinstance(r, dict)}
    tomb = every.get(uuid) or {}
    if tomb.get('state') != 'forgotten' or not tomb.get('forgotten_at'):
        raise _Stop('FAIL', 'lesson %s was asked to be forgotten but the store still reads it as %r'
                    % (uuid[:8], tomb.get('state')))
    if uuid in live or uuid in _lookup_ids(q, rule):
        raise _Stop('FAIL', 'lesson %s is marked forgotten but is STILL served; do not rely on the forget'
                    % uuid[:8])
    index = q.json('bm_learn.py', ['index-status', '--json'], dict)
    if index.get('available'):
        _c, drift = q.ask('bm_learn.py', ['verify', '--json'], ok=(0, 1))
        try:
            findings = json.loads(drift).get('findings') or []
        except (ValueError, AttributeError):
            findings = None
        if findings is None or any(uuid in json.dumps(f) for f in findings):
            raise _Stop('FAIL', 'the search index still disagrees about lesson %s (run rebuild-index); its '
                                'retrieval cannot be trusted yet' % uuid[:8])
        idx = 'Search index (fts5): %s row(s) for %s rule(s); the drift check names no problem with this lesson.' \
              % (index.get('indexed_rows'), index.get('rules'))
    else:
        idx = 'Search index: none in use (retrieval is lexical, straight from the rules), so there is no second copy.'
    why = q.json('bm_learn.py', ['why', uuid, '--json'], dict)
    used = q.json('bm_learn.py', ['applications', '--rule', uuid, '--json'], list)
    out = ['FORGOTTEN: lesson %s (When: %s | Do: %s)' % (uuid[:8], _line(rule.get('trigger_text'), 80),
                                                           _line(rule.get('action_text'), 80)),
           'Checked now: it is gone from the active lesson list and no longer served by a lookup for its own words '
           '(it %s before).' % ('was served' if served_before else 'was not matched by that lookup even'),
           idx,
           'Kept under policy: a tombstone of the rule (forgotten %s), %d version(s), %d evidence row(s) and %d past '
           'application record(s). They stay so past applications remain honest; this store has no hard delete.'
           % (tomb.get('forgotten_at'), len(why.get('versions') or []), len(why.get('evidence') or []), len(used)),
           FORGET_NOT_COVERED,
           'The door recorded the answer it was given; it cannot check who typed it.']
    return _reply('PASS', '\n'.join(out), q.refs)


# --- decisions, outcomes, report and replay: the dream recorder's own files -------------------------------------


def _run_dir(value):
    """The run folder a decision journal lives in: an existing directory, spelled as given. None otherwise."""
    return value if isinstance(value, str) and value and not value.startswith('-') and os.path.isdir(value) else None


def _sibling(name):
    """dream_report or dream_replay, loaded by path from the folder holding the recorder. dream_report imports its
    recorder by name; the bridge's own recorder stands in for that name while the file loads, so there is one
    recorder in the process."""
    rec = recorder()
    path = os.path.join(os.path.dirname(recorder_path()), name + '.py')
    if not os.path.isfile(path):
        raise _Stop('NO-DATA', '%s.py is not installed beside the dream recorder (%s), so this cannot be read here'
                    % (name, os.path.dirname(path)))
    held = sys.modules.get('dream_record')
    sys.modules['dream_record'] = rec
    try:
        spec = importlib.util.spec_from_file_location('brother_door_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # a file that cannot load is NO-DATA by name
        raise _Stop('NO-DATA', '%s.py could not be loaded: %s: %s' % (name, type(exc).__name__, exc))
    finally:
        if held is None:
            sys.modules.pop('dream_record', None)
        else:
            sys.modules['dream_record'] = held
    return module


def _digest(text):
    return hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()


def _h_decisions(q, i):
    """The decisions a run recorded and how they turned out: dream_report's own summary."""
    run = _run_dir(i['run_dir'])
    if run is None:
        raise _Stop('NO-DATA', 'run_dir must be an existing folder; nothing was read')
    report = _sibling('dream_report')
    summary = report.summarize(run)
    if summary is None:
        raise _Stop('NO-DATA', 'this run has no decision journal, so there is nothing to report')
    if not summary.get('total_decisions'):
        raise _Stop('NO-DATA', 'this run has a journal but no recorded decision')
    text = report.format_text(summary)
    return _reply('PASS', text.strip(), ['dream_report summary of %s sha256:%s' % (run, _digest(text))])


def _alternative(value):
    """{kind: option} from the typed alternative, or None when it is not that shape."""
    if not isinstance(value, dict) or not value:
        return None
    if not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items()):
        return None
    return dict(value)


def _h_replay(q, i):
    """Replay an alternative choice, per decision kind, against what a run really recorded. A decision where the
    alternative is what was actually chosen has a real recorded outcome; every other decision is UNSUPPORTED, its
    outcome is not known and nothing is inferred."""
    run = _run_dir(i['run_dir'])
    alternative = _alternative(i['alternative'])
    if run is None:
        raise _Stop('NO-DATA', 'run_dir must be an existing folder; nothing was replayed')
    if alternative is None:
        raise _Stop('NO-DATA', 'alternative must name a decision kind and the option you would have chosen for it '
                               '(for example {"route": "cheap"}); nothing was replayed')
    world = recorder().read_decisions(run)
    if world is None:
        raise _Stop('NO-DATA', 'this run has no decision journal, so there is nothing to replay')
    replay = _sibling('dream_replay')
    out, supported_total = [], 0
    for kind in sorted(alternative):
        option = alternative[kind]
        got = replay.replay([world], lambda _k, _observed, _options, _o=option: _o, kinds=[kind])['overall']
        supported, unsupported = got['supported'], got['unsupported']
        supported_total += supported
        out.append('%s, had "%s" been chosen:' % (kind, option))
        if not (supported or unsupported):
            out.append('   no graded decision of this kind was recorded in this run')
        if supported:
            out.append('   %d decision(s) where "%s" WAS the recorded choice: recorded outcome PASS %d, FAIL %d, '
                       'NO-DATA %d' % (supported, option, got['PASS'], got['FAIL'], got['NO-DATA']))
        if unsupported:
            out.append('   %d decision(s) where the run chose something else: UNSUPPORTED. What "%s" would have '
                       'done was never observed, so no outcome is given and none is inferred.'
                       % (unsupported, option))
            if got['excluded']['invalid_choice']:
                out.append('   on %d of those, "%s" was never even among the options on offer'
                           % (got['excluded']['invalid_choice'], option))
        left = ['%s %d' % (k, n) for k, n in sorted(got['excluded'].items())
                if n and k not in ('filtered', 'invalid_choice')]
        if left:
            out.append('   left out of the count: %s' % ', '.join(left))
    if not supported_total:
        raise _Stop('NO-DATA', '\n'.join(out + ['no recorded outcome exists for that alternative; nothing is '
                                                'claimed for it']))
    text = '\n'.join(out)
    return _reply('PASS', text, ['dream_replay of %s sha256:%s' % (run, _digest(text))])


def _quiet(call, *args, **kwargs):
    """(call(...), the recorder's stderr note), the note captured instead of printed."""
    note = io.StringIO()
    with contextlib.redirect_stderr(note):
        return call(*args, **kwargs), note.getvalue().strip()


def _h_decision(q, i):
    """Record one decision (its options and what was chosen) in the run's journal, for later replay."""
    run = _run_dir(i['run_dir'])
    if run is None:
        raise _Stop('NO-DATA', 'run_dir must be an existing folder; nothing was recorded')
    event, note = _quiet(recorder().record_decision, run, i['kind'], i.get('observed'), i['options'], i['chosen'],
                         i['policy_version'], cost=i.get('cost'))
    if not event:
        raise _Stop('FAIL', 'the decision was not recorded%s' % (': ' + note if note else ''))
    return _reply('PASS', 'recorded decision %s (kind %s, chose "%s" of %d option(s)). Its outcome is not known '
                          'yet: record it with the outcome capability once a check has graded it.'
                  % (event, i['kind'], i['chosen'], len(i['options'])), ['dream decision %s in %s' % (event, run)])


def _h_outcome(q, i):
    """Record the graded outcome of one recorded decision, once."""
    run = _run_dir(i['run_dir'])
    if run is None:
        raise _Stop('NO-DATA', 'run_dir must be an existing folder; nothing was recorded')
    rec = recorder()
    rows = rec.read_decisions(run)
    if rows is None:
        raise _Stop('NO-DATA', 'this run has no decision journal, so there is no decision to grade')
    row = next((r for r in rows if r.get('event_id') == i['decision']), None)
    if row is None:
        raise _Stop('NO-DATA', 'no decision %s is recorded in this run; nothing was written' % i['decision'])
    if row.get('outcomes'):
        raise _Stop('FAIL', 'decision %s already has a recorded outcome; a second one is refused, never merged'
                    % i['decision'])
    event, note = _quiet(rec.record_outcome, run, i['decision'], i['status'], i['grader'], cost=i.get('cost'),
                         detail=i.get('detail'))
    if not event:
        raise _Stop('FAIL', 'the outcome was not recorded%s' % (': ' + note if note else ''))
    return _reply('PASS', 'recorded outcome %s for decision %s from grader "%s". The door records the grader name it '
                          'was given; it cannot check that a deterministic check, not a model, decided it.'
                  % (i['status'], i['decision'][:12], i['grader']),
                  ['dream outcome %s in %s' % (event, run)])


_HANDLER_FUNCTIONS = {
    'learning_existing_report': _h_report, 'learning_existing_reuse': _h_reuse,
    'learning_existing_capture': _h_capture, 'learning_existing_recurrence': _h_recurrence,
    'learning_existing_forget': _h_forget, 'learning_existing_decisions': _h_decisions,
    'learning_existing_replay': _h_replay, 'learning_existing_decision': _h_decision,
    'learning_existing_outcome': _h_outcome,
}
_LESSON_HANDLERS = frozenset(('learning_existing_report', 'learning_existing_reuse', 'learning_existing_capture',
                              'learning_existing_recurrence', 'learning_existing_forget'))
#: lesson inputs that become words on a tool's command line: plain text or nothing runs
_LESSON_TEXT = {'learning_existing_reuse': ('task',), 'learning_existing_capture': ('trigger', 'action')}


def door_invoke(handler, inputs, run=None, tools=None):
    """{"verdict", "output", "evidence_refs"} for one learning door handler. Unknown handlers, missing inputs and
    anything a handler cannot answer are NO-DATA or FAIL naming the cause; only a PASS carries evidence. `run` and
    `tools` stand in for the lesson tools' folder and the way they are run (tests give both)."""
    function = _HANDLER_FUNCTIONS.get(handler) if isinstance(handler, str) else None
    if function is None or handler not in DOOR_HANDLERS:
        return _reply('NO-DATA', 'no learning handler is named %r' % (handler,))
    if not isinstance(inputs, dict):
        return _reply('NO-DATA', "the door's inputs were not a set of named values")
    missing = [k for k in DOOR_HANDLERS[handler] if inputs.get(k) is None or inputs.get(k) == '']
    if missing:
        return _reply('NO-DATA', 'missing input: %s' % ', '.join(missing))
    try:
        if handler in _LESSON_HANDLERS:
            tools = tools or _tools_dir()
            if tools is None:
                return _reply('NO-DATA', 'the lesson tools are not installed beside this door')
            for key in _LESSON_TEXT.get(handler, ()):
                if _text(inputs[key]) is None:
                    return _reply('NO-DATA', '%s must be plain text without control characters; nothing was run' % key)
            q = _Learn(tools, run or (lambda script, argv, env=None: _tool_run(tools, script, argv, env)))
        else:
            q = _Learn(None, None)
        return function(q, inputs)
    except _Stop as stop:
        return _reply(stop.verdict, stop.output)
    except (ValueError, TypeError) as exc:  # the recorder refuses a bad shape by name
        return _reply('NO-DATA', 'refused: %s' % exc)
    except RuntimeError as exc:  # no recorder, or one that cannot load
        return _reply('NO-DATA', str(exc))
    except OSError as exc:
        return _reply('NO-DATA', 'could not read or write: %s' % exc)


def _door_main(args):
    """`dream_bridge.py door HANDLER`, inputs as one JSON object on stdin: the reply as one JSON line, exit 0 (the
    verdict is in the reply)."""
    try:
        inputs = json.loads(sys.stdin.read() or '{}')
    except ValueError:
        inputs = None
    _emit(door_invoke(args[0] if args else None, inputs))
    return 0


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
    if args_list and args_list[0] == 'door':
        return _door_main(args_list[1:])
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


if __name__ == "__main__":
    sys.exit(main())
