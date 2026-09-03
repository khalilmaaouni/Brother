#!/usr/bin/env python3
"""The Daybook: one calm decision feed swept from three repositories.

R22 (docs/plan/READINESS-ROADMAP-2026-08-29.json). Founder direction: a
decision made anywhere on the estate should be findable in one place the
same day. A GENERATED page, not a live runtime companion, chosen through a
real decision card because a live reply channel is session-local and dies
with the session.

Two decision-store SHAPES exist on this estate and this script reads both:

  - Brother's own docs/decisions/*.json: one JSON object per file, a
    weighted decision card (title, criteria, decided.at/choice_name).
  - BrotherSBE's and BrotherModeUp's .sbe/decisions/<NNN-slug-status>/
    DECISION.md: one directory per internal-eval package, a markdown file
    with '- written at:' and '- verdict recorded by the run:' lines.

Nobody edits the rendered page. Edit a decisions store and re-run this.

Standard library only, Python 3.9 floor. Run from any directory.

Usage:
  daybook.py --collect   print one JSON list of every decision, sorted
                         newest first; exit 2 if any store could not be
                         read (NO-DATA), exit 0 otherwise.
  daybook.py             render docs/plan/DAYBOOK.html from the same sweep;
                         same exit codes.

PRODUCER: this module is the sole producer of docs/plan/DAYBOOK.html. render()
(line 258) is the only writer: it calls collect() and render_html() then does
the actual open(out_path, 'w', encoding='utf-8') plus fh.write(page) at lines
265-266.
"""
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

# Typographic dashes are refused on this page (R22.3 done-check). Replace
# rather than reject, because the source text comes from three repositories
# this script does not control the prose of. Built from chr(), never typed
# as a literal glyph, so this source file itself carries none of the
# characters it exists to strip.
_DASHES = re.compile('[' + _EN_DASH + _EM_DASH + ']')

_TITLE_RE = re.compile(r'^#\s*Decision\s+\S+:\s*(.+)$', re.MULTILINE)
_WRITTEN_RE = re.compile(r'^-\s*written at:\s*(.+)$', re.MULTILINE)
_VERDICT_RE = re.compile(r'^-\s*verdict recorded by the run:\s*(.+)$', re.MULTILINE)


def _clean(text):
    return _DASHES.sub(',', text or '')


def default_sources():
    """The three real stores. A function, not a module constant, so tests
    never risk the default-argument-binds-at-definition-time trap: this is
    called fresh, never captured as a default value."""
    home = os.path.expanduser('~')
    return [
        ('Brother', os.path.join(ROOT, 'docs', 'decisions'), 'json'),
        ('BrotherSBE',
         os.path.join(home, 'Documents', 'BrotherSBE', '.sbe', 'decisions'), 'sbe'),
        ('BrotherModeUp',
         os.path.join(home, 'Documents', 'BrotherModeUp', '.sbe', 'decisions'), 'sbe'),
    ]


def parse_json_store(repo, path):
    """Brother's own docs/decisions/*.json. Returns None if the directory
    itself is absent (NO-DATA), else a list (possibly empty)."""
    if not os.path.isdir(path):
        return None
    entries = []
    for fp in sorted(glob.glob(os.path.join(path, '*.json'))):
        try:
            with open(fp, 'r', encoding='utf-8') as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        stem = os.path.splitext(os.path.basename(fp))[0]
        decided = d.get('decided') or None
        if decided:
            date = decided.get('at') or ''
            status = decided.get('choice_name') or decided.get('choice') or 'DECIDED'
        else:
            date = ''
            status = 'AWAITING FOUNDER'
        entries.append({
            'source_repo': repo,
            'date': _clean(date),
            'title': _clean(d.get('title') or stem),
            'status': _clean(status),
            'id': stem,
        })
    return entries


def parse_sbe_store(repo, path):
    """BrotherSBE / BrotherModeUp .sbe/decisions/<id>/DECISION.md. Returns
    None if the directory itself is absent (NO-DATA), else a list."""
    if not os.path.isdir(path):
        return None
    entries = []
    for name in sorted(os.listdir(path)):
        md = os.path.join(path, name, 'DECISION.md')
        if not os.path.isfile(md):
            continue
        try:
            with open(md, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except OSError as exc:
            print("daybook: skipping unreadable decision file %s: %s"
                  % (md, exc), file=sys.stderr)
            continue
        title_m = _TITLE_RE.search(text)
        written_m = _WRITTEN_RE.search(text)
        verdict_m = _VERDICT_RE.search(text)
        entries.append({
            'source_repo': repo,
            'date': _clean(written_m.group(1).strip() if written_m else ''),
            'title': _clean(title_m.group(1).strip() if title_m else name),
            'status': _clean(verdict_m.group(1).strip() if verdict_m else 'NO-DATA'),
            'id': name,
        })
    return entries


_PARSERS = {'json': parse_json_store, 'sbe': parse_sbe_store}


def collect(sources=None):
    """Sweep every source. Returns (entries, nodata_repos). A repo whose
    store cannot be read is reported by name in nodata_repos AND still gets
    one entry in the feed (status NO-DATA) so the page never renders a
    missing repository as a silent, empty-looking feed."""
    if sources is None:
        sources = default_sources()
    entries = []
    nodata = []
    for repo, path, kind in sources:
        result = _PARSERS[kind](repo, path)
        if result is None:
            nodata.append((repo, path))
            entries.append({
                'source_repo': repo,
                'date': '',
                'title': _clean('decision store unreadable: %s' % path),
                'status': 'NO-DATA',
                'id': 'no-data-%s' % repo.lower(),
            })
        else:
            entries.extend(result)
    return entries, nodata


def sort_entries(entries):
    """Newest first. Stable and deterministic: ties break on repo then id,
    never on dict iteration order or filesystem listing order."""
    return sorted(
        entries,
        key=lambda e: (e.get('date') or '', e.get('source_repo') or '', e.get('id') or ''),
        reverse=True,
    )


# The four-alert law, reused from scripts/gen_readiness_board.py's
# STATUS_CLASS: every status resolves to exactly one of four colours. Never
# a fifth, never a silent default that only LOOKS like one of the four.
def alert_class(status):
    s = (status or '').strip().upper()
    if s == 'FAIL':
        return 'st-blocked'
    if s in ('WAIVED', 'PARTIAL', 'NO-DATA', 'UNMEASURED'):
        return 'st-nodata'
    if s in ('AWAITING FOUNDER', 'OPEN'):
        return 'st-flight'
    return 'st-done'


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
.wrap{max-width:900px;margin:0 auto;padding:48px 26px 90px}
h1{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-weight:600;font-size:2.3rem;line-height:1.12;margin:.1em 0 .35em}
.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--petrol);font-weight:600;margin:0 0 .8em}
.lede{font-size:1.05rem;color:var(--soft);max-width:64ch}
.stamp{font-size:.79rem;color:var(--faint);border-top:1px solid var(--rule);padding-top:13px;margin-top:24px;line-height:1.5}
.tag{display:inline-block;font-size:.65rem;letter-spacing:.07em;text-transform:uppercase;font-weight:700;padding:3px 7px;border-radius:3px;white-space:nowrap}
.st-done{background:var(--psoft);color:var(--petrol)}
.st-flight{background:var(--flsoft);color:var(--flight)}
.st-blocked{background:var(--fsoft);color:var(--fail)}
.st-nodata{background:var(--nsoft);color:var(--nodata)}
.card{background:var(--raised);border:1px solid var(--rule);border-radius:9px;padding:18px 22px;margin:14px 0}
.chead{display:flex;gap:10px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
.repo{font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);font-weight:600}
.when{color:var(--faint);font-size:.74rem;font-variant-numeric:tabular-nums;margin-left:auto}
.cid{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:1.05rem;margin:2px 0 0}
.norow{background:var(--raised);border-left:3px solid var(--nodata);border-radius:0 9px 9px 0;padding:14px 18px;margin:14px 0;font-size:.88rem;color:var(--soft)}
</style>
"""


def render_html(entries, nodata):
    e = html.escape
    parts = [PAGE_HEAD]
    parts.append('<div class="wrap">')
    parts.append('<p class="eyebrow">Brother &middot; BrotherSBE &middot; BrotherModeUp</p>')
    parts.append('<h1>The Daybook</h1>')
    parts.append(
        '<p class="lede">Every decision made across the estate, newest first, '
        'in one place. This is a generated page, not a live view: it is rebuilt '
        'by a script from each repository\'s own decision store, not maintained '
        'by hand and not kept open in a session. Regenerate with '
        '<code>python3 scripts/daybook.py</code>.</p>'
    )
    if nodata:
        for repo, path in nodata:
            parts.append(
                '<div class="norow">NO-DATA: the %s decision store could not be read '
                'at <code>%s</code>. The other repositories still collected.</div>'
                % (e(repo), e(path))
            )
    for entry in sort_entries(entries):
        cls = alert_class(entry.get('status'))
        parts.append('<div class="card">')
        parts.append(
            '<div class="chead"><span class="repo">%s</span>'
            '<span class="tag %s">%s</span><span class="when">%s</span></div>'
            % (e(entry.get('source_repo', '')), cls, e(entry.get('status', '')),
               e(entry.get('date', '') or 'no date on record'))
        )
        parts.append('<p class="cid">%s</p>' % e(entry.get('title', '')))
        parts.append('</div>')
    parts.append(
        '<p class="stamp">Swept from docs/decisions/*.json (Brother) and '
        '.sbe/decisions/*/DECISION.md (BrotherSBE, BrotherModeUp). '
        'Regenerate with <code>python3 scripts/daybook.py</code>.</p>'
    )
    parts.append('</div>')
    return ''.join(parts)


def render(sources=None, out_path=None):
    """Resolve out_path AT CALL TIME, never as a mutable default, so a test
    pointing this at a temp file cannot be silently ignored."""
    if out_path is None:
        out_path = OUTPUT
    entries, nodata = collect(sources)
    page = render_html(entries, nodata)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(page)
    return entries, nodata


def main(argv):
    if '--collect' in argv:
        entries, nodata = collect()
        print(json.dumps(sort_entries(entries), indent=2, sort_keys=True))
        for repo, path in nodata:
            print('NO-DATA: %s decision store missing or unreadable at %s' % (repo, path),
                  file=sys.stderr)
        return 2 if nodata else 0
    _, nodata = render()
    for repo, path in nodata:
        print('NO-DATA: %s decision store missing or unreadable at %s' % (repo, path),
              file=sys.stderr)
    return 2 if nodata else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
