#!/usr/bin/env python3
"""The Daybook: the minimal board over the outcome contract (v2).

2026-09-08 debate judgment (docs/handover/2026-09-08-persona-intake-daybook/
07-DEBATE-JUDGMENT.md, unit U9): "one program, not three." Intake V2 writes
the outcome contract (docs/schema/outcome-contract-v1.json, one JSON object
per record under docs/decisions/ and docs/decisions/inflight/), the door
checks it before planning, and this script is the READ side of that same
record. Nobody edits the rendered page. Edit a record and re-run this.

RETIRED here, deliberately (see the commit message for the full reason):
the v1 board's three-repository sweep of Brother/BrotherSBE/BrotherModeUp
decision cards (two different ad hoc shapes, no persona, no receipts, no
close-call signal). That board answered "what decisions exist"; this one
answers "what is the state of each outcome contract", which is the question
Daybook v2 was commissioned to answer. A JSON file that is not an outcome
contract (including every pre-v2 decision card already in docs/decisions/)
is never dropped: it lands in the "not a contract" column, named by its
filename, so nothing this script cannot parse becomes silently invisible.

Cut per the debate judgment: the embedding index, SessionEnd registration,
timeline strip, cost line, term-overlap related links, memory review panel.

Standard library only, Python 3.9 floor. Run from any directory.

Usage:
  daybook.py [--records DIR] [--since Nd] [--state open|close-call|decided|
             superseded|"not a contract"] [--repo NAME] [--persona VALUE]
             [-o OUT.html] [--collect]
      Render the board (default docs/plan/DAYBOOK.html, default records dir
      docs/decisions/ plus docs/decisions/inflight/ when present). Exit 2
      with a NO-DATA line when the records directory is missing or holds no
      readable JSON at all; exit 0 otherwise (an empty board after query
      flags filter everything out is still exit 0: the store itself read
      fine).
  daybook.py --collect [--records DIR] [--since Nd] [--state ...]
             [--repo NAME] [--persona VALUE]
      Skip the HTML render; print the same collected, filtered records as a
      compact JSON list on stdout (id, state, persona, has_decision,
      receipts count, source path). Same NO-DATA/exit-2 rule as above, on
      stderr.

PRODUCER: this module is the sole producer of docs/plan/DAYBOOK.html. render()
is the only writer: it calls collect() and render_html() then does the
actual open(out_path, 'w', encoding='utf-8') plus fh.write(page).
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import sys

_EN_DASH = chr(0x2013)
_EM_DASH = chr(0x2014)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, 'docs', 'plan', 'DAYBOOK.html')
DEFAULT_RECORDS_DIR = os.path.join(ROOT, 'docs', 'decisions')

# Typographic dashes are refused on this page (R22.3 done-check, still
# binding on v2). Replace rather than reject: the source text comes from
# records this script does not control the prose of. Built from chr(),
# never typed as a literal glyph, so this source file carries none of the
# characters it exists to strip.
_DASHES = re.compile('[' + _EN_DASH + _EM_DASH + ']')

SCHEMA_VERSION = 'outcome-contract-v1'
PERSONA_ENUM = ('analyst', 'lead', 'developer')
DISPLAY_STATES = ('open', 'close-call', 'decided', 'superseded', 'not a contract')

# receipts[].ref grammar, read off docs/schema/README.md and the amended
# outcome-contract-v1.json (branch u4-contract-schema, PR 528, amended in
# flight): file:<path>:<start>-<end> | evidence:<name> | url:<u>.
# ponytail: scripts/receipt_check.py (branch u8-pr485-disposition) is the
# module meant to own this resolution; it does not exist on main yet as of
# this commit. Import receipt_check.resolve_receipt once it lands and
# delete _resolve_ref_inline below rather than keeping two copies.
_REF_FILE_RE = re.compile(r'^file:(.+):(\d+)-(\d+)$')


def _clean(text):
    return _DASHES.sub(',', text or '')


def _resolve_ref_inline(ref):
    """Three-prefix grammar, no network calls. Returns 'RESOLVED' or
    'UNVERIFIED'. A url ref is UNVERIFIED unless a captured copy exists
    under ~/.claude/evidence: no naming convention for that capture exists
    on this estate yet, so this looks for any evidence file whose name
    contains a filesystem-safe encoding of the url. Replace this heuristic
    the day a real convention lands."""
    if not ref or not isinstance(ref, str):
        return 'UNVERIFIED'
    m = _REF_FILE_RE.match(ref)
    if m:
        path, start, end = m.group(1), int(m.group(2)), int(m.group(3))
        if start < 1 or end < start or not os.path.isfile(path):
            return 'UNVERIFIED'
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                n_lines = sum(1 for _ in fh)
        except OSError:
            return 'UNVERIFIED'
        return 'RESOLVED' if n_lines >= end else 'UNVERIFIED'
    if ref.startswith('evidence:'):
        name = ref[len('evidence:'):]
        target = os.path.join(os.path.expanduser('~/.claude/evidence'), name)
        return 'RESOLVED' if name and os.path.exists(target) else 'UNVERIFIED'
    if ref.startswith('url:'):
        url = ref[len('url:'):]
        safe = re.sub(r'[^A-Za-z0-9]+', '_', url).strip('_')
        evdir = os.path.expanduser('~/.claude/evidence')
        if safe and os.path.isdir(evdir):
            for name in os.listdir(evdir):
                if safe in name:
                    return 'RESOLVED'
        return 'UNVERIFIED'
    return 'UNVERIFIED'


def resolve_receipt(receipt):
    """One receipts[] entry (dict) -> {'label': str, 'status': str}. Tolerates
    the pre-amendment schema shape (a bare 'path' key, no 'ref') as well as
    the amended one, per the brief: code against the amended shape, tolerate
    the current one."""
    if not isinstance(receipt, dict):
        return {'label': 'NO-DATA', 'status': 'UNVERIFIED'}
    ref = receipt.get('ref')
    if ref:
        return {'label': _clean(str(ref)), 'status': _resolve_ref_inline(ref)}
    path = receipt.get('path')
    if path:
        status = 'RESOLVED' if os.path.isfile(path) else 'UNVERIFIED'
        return {'label': _clean(str(path)), 'status': status}
    return {'label': receipt.get('id') or 'NO-DATA', 'status': 'UNVERIFIED'}


def _parse_stamp(value):
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:  # sbe: allow-silent a malformed history stamp cannot identify an ordering, so this row is deliberately excluded
        pass
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d')
    except ValueError:  # sbe: allow-silent a date-only fallback is optional, and an invalid value has no safe normalized timestamp to report
        return None


def newest_history_stamp(history):
    stamps = []
    for h in history or []:
        if isinstance(h, dict):
            t = _parse_stamp(h.get('at'))
            if t is not None:
                stamps.append(t)
    return max(stamps) if stamps else None


def derive_display_state(state, close_call):
    """The orchestrator's ruling, in evaluation order: delivered and
    superseded are their own columns regardless of close_call; everything
    else is close-call when decision.close_call is true, else open."""
    if state == 'delivered':
        return 'decided'
    if state == 'superseded':
        return 'superseded'
    if close_call:
        return 'close-call'
    return 'open'


def build_entry(filepath, data):
    """One parsed JSON file -> a rendering-ready dict. Non-contract files
    (no schema_version == outcome-contract-v1) are tagged is_contract=False
    and carry only their filename, so build_entry never raises on an
    unrelated JSON shape (all 57-plus pre-v2 decision cards on this estate
    included)."""
    filename = os.path.basename(filepath)
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        return {'is_contract': False, 'filename': filename, 'source_path': filepath}

    persona_raw = data.get('persona')
    persona = persona_raw if persona_raw in PERSONA_ENUM else 'NO-DATA'

    decision = data.get('decision')  # amended field; tolerate absence as null
    if not isinstance(decision, dict):
        decision = None
    close_call = bool(decision.get('close_call')) if decision else False
    margin = decision.get('margin') if decision else None

    state = data.get('state')
    display_state = derive_display_state(state, close_call)

    receipts = [resolve_receipt(r) for r in (data.get('receipts') or [])]
    resolved_count = sum(1 for r in receipts if r['status'] == 'RESOLVED')
    unverified_count = len(receipts) - resolved_count

    project = data.get('project') or {}
    repo_name = ((project.get('repository') or {}).get('name'))
    title = _clean(data.get('question') or project.get('name') or filename)

    return {
        'is_contract': True,
        'filename': filename,
        'source_path': filepath,
        'title': title,
        'state': _clean(state or ''),
        'display_state': display_state,
        'persona': persona,
        'has_decision': decision is not None,
        'close_call': close_call,
        'margin': margin,
        'repo': repo_name,
        'receipts': receipts,
        'resolved_count': resolved_count,
        'unverified_count': unverified_count,
        'newest_history': newest_history_stamp(data.get('history')),
    }


def _record_dirs(records_dir):
    """The records directory plus its inflight/ subdirectory when present,
    per the brief: Intake V2 writes drafts to docs/decisions/inflight/."""
    dirs = [records_dir]
    inflight = os.path.join(records_dir, 'inflight')
    if os.path.isdir(inflight):
        dirs.append(inflight)
    return dirs


def collect(records_dir=None):
    """Sweep the records directory (and inflight/ when present) for JSON
    files. Returns (entries, nodata). nodata is True when the records
    directory itself is missing, or exists but holds no file this script
    could parse as JSON at all (the NO-DATA case, exit 2 in main())."""
    if records_dir is None:
        records_dir = DEFAULT_RECORDS_DIR
    if not os.path.isdir(records_dir):
        return [], True
    entries = []
    any_readable = False
    for d in _record_dirs(records_dir):
        for fp in sorted(glob.glob(os.path.join(d, '*.json'))):
            try:
                with open(fp, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            any_readable = True
            entries.append(build_entry(fp, data))
    return entries, not any_readable


def parse_since(value):
    """'30d' -> 30. Raises ValueError on anything else, named at the call
    site so a typo in --since fails loudly rather than filtering silently
    wrong."""
    m = re.match(r'^(\d+)d$', value or '')
    if not m:
        raise ValueError('invalid --since value: %r (expected e.g. 30d)' % (value,))
    return int(m.group(1))


def _within_since(entry, days):
    stamp = entry.get('newest_history')
    if stamp is None:
        return False
    now = datetime.datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.datetime.now()
    return (now - stamp).days <= days


def apply_filters(entries, since_days=None, state=None, repo=None, persona=None):
    """Query flags apply to contract records only: a non-contract entry has
    none of state/repo/persona/history to filter on, and hiding it behind a
    filter would be exactly the silent drop the fifth column exists to
    prevent, so it always passes through."""
    out = []
    for e in entries:
        if not e['is_contract']:
            out.append(e)
            continue
        if since_days is not None and not _within_since(e, since_days):
            continue
        if state is not None and e['display_state'] != state:
            continue
        if repo is not None and (e.get('repo') or '') != repo:
            continue
        if persona is not None and e.get('persona') != persona:
            continue
        out.append(e)
    return out


PAGE_HEAD = """<title>The Daybook</title>
<style>
:root{--paper:#F7F8F6;--raised:#FFF;--ink:#141B22;--soft:#4A5763;--faint:#7C8894;
--petrol:#0E7A6F;--psoft:#E3F0EE;--rule:#DDE3E1;--rsoft:#EAEEEC;--fail:#A32C22;
--fsoft:#F7E5E3;--nodata:#9A6B12;--nsoft:#F7EEDC;--flight:#1D6FA5;--flsoft:#E1EEF7}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--paper:#10161B;--raised:#171F26;--ink:#E8EDEA;--soft:#A5B2B8;--faint:#77858D;
--petrol:#3AA893;--psoft:#16302D;--rule:#28333A;--rsoft:#1E282E;--fail:#E0776A;
--fsoft:#33201D;--nodata:#D9A441;--nsoft:#2E2617;--flight:#63AEDC;--flsoft:#132631}}
:root[data-theme="dark"]{--paper:#10161B;--raised:#171F26;--ink:#E8EDEA;--soft:#A5B2B8;--faint:#77858D;
--petrol:#3AA893;--psoft:#16302D;--rule:#28333A;--rsoft:#1E282E;--fail:#E0776A;
--fsoft:#33201D;--nodata:#D9A441;--nsoft:#2E2617;--flight:#63AEDC;--flsoft:#132631}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);margin:0;font-family:"Seravek","Avenir Next",ui-sans-serif,system-ui,sans-serif;font-size:16px;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto;padding:48px 26px 90px}
h1{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-weight:600;font-size:2.3rem;line-height:1.12;margin:.1em 0 .35em}
.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--petrol);font-weight:600;margin:0 0 .8em}
.lede{font-size:1.05rem;color:var(--soft);max-width:70ch}
.counts{font-size:.85rem;color:var(--soft);margin:10px 0 0}
.stamp{font-size:.79rem;color:var(--faint);border-top:1px solid var(--rule);padding-top:13px;margin-top:24px;line-height:1.5}
.tag{display:inline-block;font-size:.65rem;letter-spacing:.07em;text-transform:uppercase;font-weight:700;padding:3px 7px;border-radius:3px;white-space:nowrap;margin:2px 4px 2px 0}
.st-done{background:var(--psoft);color:var(--petrol)}
.st-flight{background:var(--flsoft);color:var(--flight)}
.st-blocked{background:var(--fsoft);color:var(--fail)}
.st-nodata{background:var(--nsoft);color:var(--nodata)}
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-top:22px}
.col{background:var(--rsoft);border-radius:9px;padding:12px}
.colhead{font-size:.72rem;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);font-weight:700;margin:0 0 8px}
.card{background:var(--raised);border:1px solid var(--rule);border-radius:9px;padding:12px 14px;margin:10px 0}
.cid{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:.98rem;margin:2px 0 6px}
.receipt{font-size:.72rem;color:var(--faint);margin:2px 0}
.norow{background:var(--raised);border-left:3px solid var(--nodata);border-radius:0 9px 9px 0;padding:14px 18px;margin:14px 0;font-size:.88rem;color:var(--soft)}
</style>
"""

_COLUMN_TITLES = {
    'open': 'Open',
    'close-call': 'Close call',
    'decided': 'Decided',
    'superseded': 'Superseded',
    'not a contract': 'Not a contract',
}


def _receipt_class(status):
    return 'st-done' if status == 'RESOLVED' else 'st-nodata'


def render_html(entries, records_dir, nodata):
    e = html.escape
    parts = [PAGE_HEAD]
    parts.append('<div class="wrap">')
    parts.append('<p class="eyebrow">Brother &middot; the outcome contract</p>')
    parts.append('<h1>The Daybook</h1>')
    parts.append(
        '<p class="lede">Every outcome contract, by state, in one place. This '
        'is a generated page, not a live view: it is rebuilt by a script from '
        'the records directory\'s own JSON files, not maintained by hand and '
        'not kept open in a session. Regenerate with '
        '<code>python3 scripts/daybook.py</code>.</p>'
    )
    if nodata:
        parts.append(
            '<div class="norow">NO-DATA: no readable outcome-contract record '
            'was found under <code>%s</code>.</div>' % e(records_dir)
        )
    contract_entries = [x for x in entries if x['is_contract']]
    resolved_total = sum(x['resolved_count'] for x in contract_entries)
    unverified_total = sum(x['unverified_count'] for x in contract_entries)
    parts.append(
        '<p class="counts">%d resolved, %d unverified.</p>'
        % (resolved_total, unverified_total)
    )

    parts.append('<div class="board">')
    for key in ('open', 'close-call', 'decided', 'superseded'):
        bucket = [x for x in contract_entries if x['display_state'] == key]
        parts.append('<div class="col">')
        parts.append('<p class="colhead">%s (%d)</p>' % (e(_COLUMN_TITLES[key]), len(bucket)))
        for item in bucket:
            parts.append('<div class="card">')
            persona_cls = 'st-done' if item['persona'] != 'NO-DATA' else 'st-nodata'
            parts.append('<span class="tag %s">%s</span>' % (persona_cls, e(item['persona'])))
            if not item['has_decision']:
                parts.append('<span class="tag st-nodata">NO DECISION</span>')
            if not item['receipts']:
                parts.append('<span class="tag st-nodata">NO RECEIPTS</span>')
            if item['close_call']:
                margin_txt = (' (margin %s)' % e(str(item['margin']))) if item['margin'] is not None else ''
                parts.append('<span class="tag st-flight">CLOSE CALL%s</span>' % margin_txt)
            parts.append('<p class="cid">%s</p>' % e(item['title']))
            for r in item['receipts']:
                parts.append(
                    '<p class="receipt"><span class="tag %s">%s</span> %s</p>'
                    % (_receipt_class(r['status']), e(r['status']), e(r['label']))
                )
            parts.append('</div>')
        parts.append('</div>')

    not_contract = [x for x in entries if not x['is_contract']]
    parts.append('<div class="col">')
    parts.append('<p class="colhead">%s (%d)</p>' % (e(_COLUMN_TITLES['not a contract']), len(not_contract)))
    for item in not_contract:
        parts.append('<div class="card"><p class="cid">%s</p></div>' % e(item['filename']))
    parts.append('</div>')
    parts.append('</div>')  # .board

    try:
        display_dir = os.path.relpath(records_dir, ROOT)
    except ValueError:
        display_dir = records_dir  # different volume than ROOT; relpath can't express it
    parts.append(
        '<p class="stamp">Swept from %s (and its inflight/ subdirectory when '
        'present). Regenerate with <code>python3 scripts/daybook.py</code>.</p>'
        % e(display_dir)
    )
    parts.append('</div>')
    return ''.join(parts)


def render(records_dir=None, out_path=None, since_days=None, state=None, repo=None, persona=None):
    """Resolve records_dir and out_path AT CALL TIME, never as a mutable
    default, so a test pointing this at a temp directory or file cannot be
    silently ignored."""
    if records_dir is None:
        records_dir = DEFAULT_RECORDS_DIR
    if out_path is None:
        out_path = OUTPUT
    entries, nodata = collect(records_dir)
    filtered = entries if nodata else apply_filters(entries, since_days, state, repo, persona)
    page = render_html(filtered, records_dir, nodata)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(page)
    return filtered, nodata


def _project_entry(entry):
    """One collect()/build_entry() dict -> the compact projection --collect
    prints: id, state, persona, has_decision, receipts count, source path.
    Works for non-contract entries too (they carry only filename and
    source_path), so a JSON-decodable "not a contract" file is reported
    rather than raising."""
    return {
        'id': entry.get('filename'),
        'is_contract': entry['is_contract'],
        'title': entry.get('title'),
        'state': entry.get('state'),
        'persona': entry.get('persona'),
        'has_decision': entry.get('has_decision'),
        'receipts': len(entry.get('receipts') or []),
        'source_path': entry.get('source_path'),
    }


def build_arg_parser():
    p = argparse.ArgumentParser(description='Daybook v2: the minimal board over the outcome contract.')
    p.add_argument('--records', default=None, help='records directory (default docs/decisions)')
    p.add_argument('--since', default=None, help='only records whose newest history stamp is within Nd, e.g. 30d')
    p.add_argument('--state', default=None, choices=list(DISPLAY_STATES))
    p.add_argument('--repo', default=None, help='filter to project.repository.name')
    p.add_argument('--persona', default=None, help='filter to a persona value')
    p.add_argument('-o', '--out', dest='out', default=None)
    p.add_argument('--collect', action='store_true',
                    help='print the collected records as JSON on stdout '
                         'instead of rendering the HTML board')
    return p


def main(argv):
    args = build_arg_parser().parse_args(argv)
    records_dir = args.records or DEFAULT_RECORDS_DIR
    since_days = parse_since(args.since) if args.since else None

    if args.collect:
        entries, nodata = collect(records_dir)
        if nodata:
            print('NO-DATA: no readable outcome-contract record under %s' % records_dir,
                  file=sys.stderr)
            return 2
        filtered = apply_filters(entries, since_days, args.state, args.repo, args.persona)
        print(json.dumps([_project_entry(e) for e in filtered]))
        return 0

    out_path = args.out or OUTPUT
    _, nodata = render(
        records_dir=records_dir, out_path=out_path, since_days=since_days,
        state=args.state, repo=args.repo, persona=args.persona,
    )
    if nodata:
        print('NO-DATA: no readable outcome-contract record under %s' % records_dir,
              file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
