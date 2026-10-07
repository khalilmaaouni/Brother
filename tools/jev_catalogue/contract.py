'''L6.1 catalogue parse and noul gate.

Standard library only.  Missing, corrupt or non-noul input is blocked or
no-data, never treated as a safe pass.
'''

import math
import re
from typing import Any, Dict, List, Optional, Tuple

_ENTRY_HEAD_RE = re.compile(r'^###\s+(\d+)\.\s*(.*)$', re.MULTILINE)
_STATE_RE = re.compile(r'^State:\s*(.*)$', re.MULTILINE)
_QUESTION_RE = re.compile(r'^Question:\s*`([^`]+)`\s*:?\s*(.*)$', re.MULTILINE)
_ANSWER_RE = re.compile(r'^Answer:\s*\*\*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([0-9]*\.?[0-9]+)\*\*', re.MULTILINE)
_LEDGER_RE = re.compile(r'^Ledger:\s*`([^`]+)`', re.MULTILINE)

_MAX_ENTRY_NUMBER_DIGITS = 6

_SAFE_IDENTIFIER_CHARS = frozenset(
    'abcdefghijklmnopqrstuvwxyz'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    '0123456789'
    '._-'
)


def _slugify(text: str) -> str:
    '''Turn an entry title into a safe lowercase slug.'''
    slug = text.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    if not slug:
        return 'entry'
    return slug


def _identifier_problem(value: Any, field: str) -> str:
    '''Return a blocked reason for an unsafe identifier, else an empty string.

    Holder id, question id and slug name files and lookup keys, so path
    traversal and any character outside the safe set are refused here, in
    the single place every identifier passes through.
    '''
    if not isinstance(value, str):
        return 'blocked: ' + field + ' must be a string'
    if not value.strip():
        return 'blocked: ' + field + ' must be a non-empty string'
    if value != value.strip():
        return 'blocked: ' + field + ' must not carry surrounding whitespace'
    if '..' in value:
        return 'blocked: ' + field + ' must not contain path traversal'
    for char in value:
        if char not in _SAFE_IDENTIFIER_CHARS:
            return 'blocked: ' + field + ' must use only letters, digits, dot, underscore or hyphen'
    return ''


def load_catalogue_md(path: str) -> List[Dict[str, Any]]:
    '''Read the markdown catalogue and return raw entry dictionaries.

    A missing, unreadable, undecodable or entry-free file yields an empty
    list.  Malformed sections come back with empty fields so validate_entry
    can block them deliberately.
    '''
    if not isinstance(path, str) or not path:
        return []
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return []
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return []

    entries: List[Dict[str, Any]] = []
    matches = list(_ENTRY_HEAD_RE.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        digits = match.group(1)
        if len(digits) > _MAX_ENTRY_NUMBER_DIGITS:
            number = 0
        else:
            number = int(digits)
        title = match.group(2).strip()

        state_match = _STATE_RE.search(block)
        state_excerpt = state_match.group(1).strip() if state_match else ''

        question_match = _QUESTION_RE.search(block)
        if question_match:
            question_id = question_match.group(1).strip()
            instructions = question_match.group(2).strip()
        else:
            question_id = ''
            instructions = ''

        answer_match = _ANSWER_RE.search(block)
        if answer_match:
            qtype = answer_match.group(1).strip()
            raw_answer = answer_match.group(2).strip()
            try:
                answer: Optional[float] = float(raw_answer)
            except ValueError:
                answer = None
        else:
            qtype = ''
            answer = None

        ledger_match = _LEDGER_RE.search(block)
        holder_id = ledger_match.group(1).strip() if ledger_match else ''

        criteria_seed = instructions if instructions else question_id
        true_criteria = criteria_seed
        false_criteria = 'not (' + criteria_seed + ')' if criteria_seed else ''

        entry: Dict[str, Any] = {
            'number': number,
            'holder_id': holder_id,
            'slug': _slugify(title),
            'question_id': question_id,
            'type': qtype,
            'state_excerpt': state_excerpt,
            'true_criteria': true_criteria,
            'false_criteria': false_criteria,
            'answer': answer,
        }
        entries.append(entry)
    return entries


def gate_question_type(qtype: str) -> Tuple[bool, str]:
    '''Allow only the exact noul question type.'''
    if not isinstance(qtype, str):
        return (False, 'blocked: question type must be a string')
    if qtype == 'noul':
        return (True, '')
    if qtype == '':
        return (False, 'blocked: empty question type')
    if qtype == 'score':
        return (False, 'blocked: score question type refused')
    if qtype == 'choice':
        return (False, 'blocked: choice question type refused')
    return (False, 'blocked: unknown question type')


def _validate_entry_fields(entry: dict) -> Tuple[bool, str]:
    '''Field checks for one entry, reached only through validate_entry.'''
    if 'number' not in entry:
        return (False, 'blocked: missing number')
    number = entry['number']
    if isinstance(number, bool) or not isinstance(number, int):
        return (False, 'blocked: number must be an int')
    if number <= 0:
        return (False, 'blocked: number must be positive')
    for field in ('holder_id', 'slug', 'question_id'):
        if field not in entry:
            return (False, 'blocked: missing ' + field)
        blocked = _identifier_problem(entry[field], field)
        if blocked:
            return (False, blocked)
    if 'type' not in entry:
        return (False, 'blocked: missing type')
    type_ok, type_reason = gate_question_type(entry['type'])
    if not type_ok:
        return (False, type_reason)
    if 'state_excerpt' not in entry:
        return (False, 'blocked: missing state_excerpt')
    state = entry['state_excerpt']
    if not isinstance(state, str) or not state.strip():
        return (False, 'blocked: empty state_excerpt')
    if 'true_criteria' not in entry:
        return (False, 'blocked: missing true_criteria')
    true_criteria = entry['true_criteria']
    if not isinstance(true_criteria, str) or not true_criteria.strip():
        return (False, 'blocked: true_criteria must be a non-empty string')
    if 'false_criteria' not in entry:
        return (False, 'blocked: missing false_criteria')
    false_criteria = entry['false_criteria']
    if not isinstance(false_criteria, str) or not false_criteria.strip():
        return (False, 'blocked: false_criteria must be a non-empty string')
    if 'answer' not in entry:
        return (False, 'blocked: missing answer')
    answer = entry['answer']
    if isinstance(answer, bool) or not isinstance(answer, (int, float)):
        return (False, 'blocked: answer must be a number')
    if isinstance(answer, float) and not math.isfinite(answer):
        return (False, 'blocked: answer must be finite')
    if answer < 0.0 or answer > 1.0:
        return (False, 'blocked: answer out of range 0.0 to 1.0')
    return (True, '')


def validate_entry(entry: dict) -> Tuple[bool, str]:
    '''Validate one catalogue entry.

    Missing, corrupt or unreadable input blocks.  A container whose lookups
    raise, such as a mapping built around an unhashable key, is refused here
    instead of letting an interpreter error reach the caller.
    '''
    if not isinstance(entry, dict):
        return (False, 'blocked: entry must be a dict')
    try:
        return _validate_entry_fields(entry)
    except TypeError:
        return (False, 'blocked: entry fields could not be read')
