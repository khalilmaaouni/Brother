'''jev_compaction_advisor: report only compaction plan and cost.

This module produces a plan and a cost. It never edits a transcript,
never deletes a turn and never rewrites a turn. The deterministic plan
decides what could be done; a caller decides whether to apply it. The
seam ships OFF: this module asks for no score by default and refuses
unknown or malformed input with ValueError.

TURN SCHEMA. A turn is a dict carrying a string 'type' and an int
'tokens' count. Recognised types: 'user', 'assistant', 'tool_call',
'tool_result', 'reasoning'. A tool call and its result are linked by a
matching 'tool_call_id' string. A 'link' string (when present) names
the assistant turn a turn belongs to; a unit whose turn links to an
assistant turn that also carries a 'reasoning' turn is kept, because a
scorer that cannot see a reasoning payload must not drop the call that
belongs with it. A 'tool_result' with 'is_error' True is kept, because
the loop failure (forgetting that an attempt already failed) is what a
few tokens saved actually costs. The turn schema in this tree's earlier
Jev seams was not shown; this module documents its own.

WHAT THIS MODULE IS AND IS NOT. It is a planner and an accountant. It
returns actions, freed tokens, rewritten tokens and a worth-it flag. It
never applies anything, and it is REPORT ONLY per sub unit J1.d.
'''
import math

KEEP_AT = 0.5
PINNED_RECENT = 6
MIN_FREED_FRACTION = 0.25


def _empty_plan():
    return {
        'actions': [],
        'freed_tokens': 0,
        'rewritten_tokens': 0,
        'worth_it': False,
    }


def _require_list(value, name):
    if not isinstance(value, list):
        raise ValueError('%s must be a list' % name)
    return value


def _require_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('%s must be an int' % name)
    return value


def _require_number(value, name):
    if isinstance(value, bool):
        raise ValueError('%s must be a number, not a bool' % name)
    if not isinstance(value, (int, float)):
        raise ValueError('%s must be a number' % name)
    if isinstance(value, float) and math.isnan(value):
        raise ValueError('%s must not be NaN' % name)
    return value


def _validate_turns(turns, require_tokens):
    _require_list(turns, 'turns')
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise ValueError('turn %d must be a dict' % i)
        if require_tokens:
            if 'tokens' not in turn:
                raise ValueError('turn %d has no tokens count' % i)
            tokens = turn['tokens']
            if isinstance(tokens, bool) or not isinstance(tokens, int):
                raise ValueError('turn %d tokens must be an int' % i)
            if tokens < 0:
                raise ValueError('turn %d tokens must not be negative' % i)


def _turn_type(turn):
    value = turn.get('type')
    if isinstance(value, str):
        return value
    return ''


def pair_turns(turns):
    _validate_turns(turns, require_tokens=False)
    used = [False] * len(turns)
    units = []
    for i, turn in enumerate(turns):
        if used[i] or _turn_type(turn) != 'tool_call':
            continue
        call_id = turn.get('tool_call_id')
        paired = None
        if isinstance(call_id, str) and call_id:
            for j in range(i + 1, len(turns)):
                if used[j]:
                    continue
                other = turns[j]
                if (_turn_type(other) == 'tool_result'
                        and other.get('tool_call_id') == call_id):
                    paired = j
                    break
        if paired is None:
            units.append([i])
        else:
            units.append([i, paired])
            used[paired] = True
        used[i] = True
    for i in range(len(turns)):
        if not used[i]:
            units.append([i])
            used[i] = True
    units.sort(key=lambda unit: unit[0])
    return units


def _first_task_text(turns):
    for turn in turns:
        if _turn_type(turn) == 'user':
            content = turn.get('content')
            if not isinstance(content, str):
                raise ValueError(
                    'the first user turn must carry string content')
            return content
    return ''


def scoring_state(turns, unit):
    _validate_turns(turns, require_tokens=False)
    _require_list(unit, 'unit')
    for index in unit:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError('unit indexes must be ints')
        if index < 0 or index >= len(turns):
            raise ValueError('unit index out of range')
    task = _first_task_text(turns)
    call = None
    result = None
    for index in unit:
        turn = turns[index]
        kind = _turn_type(turn)
        if kind == 'tool_call' and call is None:
            call = turn
        elif kind == 'tool_result' and result is None:
            result = turn
    if call is None or result is None:
        raise ValueError('a unit without a result is never scored')
    return {'task': task, 'call': call, 'result': result}


def _reasoning_links(turns):
    links = set()
    for turn in turns:
        if _turn_type(turn) == 'reasoning':
            link = turn.get('link')
            if isinstance(link, str) and link:
                links.add(link)
    return links


def _unit_has_reasoning(turns, unit, reasoning_links):
    for index in unit:
        turn = turns[index]
        kind = _turn_type(turn)
        if kind == 'reasoning':
            return True
        link = turn.get('link')
        if isinstance(link, str) and link and link in reasoning_links:
            return True
    return False


def _unit_has_error(turns, unit):
    for index in unit:
        turn = turns[index]
        if (_turn_type(turn) == 'tool_result'
                and turn.get('is_error') is True):
            return True
    return False


def _unit_is_unpaired_tool(turns, unit):
    if len(unit) != 1:
        return False
    kind = _turn_type(turns[unit[0]])
    return kind == 'tool_call' or kind == 'tool_result'


def plan_compaction(turns, scores, used_tokens, limit_tokens,
                    keep_at=KEEP_AT, pinned_recent=PINNED_RECENT,
                    min_freed_fraction=MIN_FREED_FRACTION):
    _validate_turns(turns, require_tokens=True)
    if not isinstance(scores, dict):
        raise ValueError('scores must be a dict')
    for key, value in scores.items():
        if isinstance(key, bool) or not isinstance(key, int):
            raise ValueError('scores keys must be ints')
        _require_number(value, 'scores value')
    used_tokens = _require_int(used_tokens, 'used_tokens')
    limit_tokens = _require_int(limit_tokens, 'limit_tokens')
    if used_tokens < 0 or limit_tokens < 0:
        raise ValueError('token counts must not be negative')
    if used_tokens > limit_tokens:
        raise ValueError('used_tokens cannot exceed limit_tokens')
    keep_at = _require_number(keep_at, 'keep_at')
    pinned_recent = _require_int(pinned_recent, 'pinned_recent')
    if pinned_recent < 0:
        raise ValueError('pinned_recent must not be negative')
    min_freed_fraction = _require_number(
        min_freed_fraction, 'min_freed_fraction')
    if min_freed_fraction < 0:
        raise ValueError('min_freed_fraction must not be negative')

    if used_tokens < limit_tokens:
        return _empty_plan()

    units = pair_turns(turns)
    reasoning_links = _reasoning_links(turns)

    actions = []
    freed_tokens = 0
    earliest_changed = None
    for unit_index, unit in enumerate(units):
        pinned = unit_index >= len(units) - pinned_recent
        if pinned:
            action = 'keep'
            why = 'pinned recent unit'
        elif _unit_has_reasoning(turns, unit, reasoning_links):
            action = 'keep'
            why = 'holds or shares a reasoning payload'
        elif _unit_has_error(turns, unit):
            action = 'keep'
            why = 'error result kept so a failed attempt is not retried'
        elif _unit_is_unpaired_tool(turns, unit):
            action = 'keep'
            why = 'unpaired tool call or result is never droppable'
        else:
            score = scores.get(unit_index)
            if score is None:
                action = 'keep'
                why = 'unit has no score'
            elif score >= keep_at:
                action = 'keep'
                why = 'score at or above keep_at'
            else:
                action = 'drop'
                why = 'score below keep_at'
        actions.append({'unit': list(unit), 'action': action, 'why': why})
        if action != 'keep':
            for index in unit:
                freed_tokens += turns[index]['tokens']
            if earliest_changed is None or unit[0] < earliest_changed:
                earliest_changed = unit[0]

    if freed_tokens < min_freed_fraction * used_tokens:
        return _empty_plan()

    if earliest_changed is None:
        rewritten_tokens = 0
    else:
        last_changed = earliest_changed
        for unit in units:
            if unit[0] == earliest_changed:
                last_changed = max(unit)
                break
        rewritten_tokens = 0
        for index in range(last_changed + 1, len(turns)):
            rewritten_tokens += turns[index]['tokens']

    return {
        'actions': actions,
        'freed_tokens': freed_tokens,
        'rewritten_tokens': rewritten_tokens,
        'worth_it': True,
    }


def recall_report(full_answers, compacted_answers):
    _require_list(full_answers, 'full_answers')
    _require_list(compacted_answers, 'compacted_answers')
    if len(full_answers) != len(compacted_answers):
        raise ValueError('answer lists must have the same length')
    for value in full_answers:
        if not isinstance(value, str):
            raise ValueError('full answers must be strings')
    for value in compacted_answers:
        if not isinstance(value, str):
            raise ValueError('compacted answers must be strings')
    same = 0
    for left, right in zip(full_answers, compacted_answers):
        if left == right:
            same += 1
    different = len(full_answers) - same
    if not full_answers:
        recall = 1.0
    else:
        recall = same / float(len(full_answers))
    return {'same': same, 'different': different, 'recall': recall}
