#!/usr/bin/env python3
"""Three way merge driver for the readiness roadmap JSON, keyed by row id.

WHY THIS EXISTS. The board is one JSON file that every lane writes: each lane
edits its own row, or adds one. git's merge driver reads that file as LINES,
so two lanes that touched two unrelated rows still collide the moment main
gained a row after either branched. Three pull requests were refused in one
night with "REAL CONFLICT docs/plan/READINESS-ROADMAP-2026-08-29.json", none
of them a disagreement about anything: the rows were disjoint and the text
was not. A driver that reads the file as a SET OF ROWS KEYED BY ID sees the
disjointness git cannot.

WHAT IT REFUSES TO DO. It never guesses. Handed anything that is not the
roadmap's shape (no rows array, a row with no id, two rows sharing an id on
one side) it reports NO-DATA and exits 2 rather than merging something whose
shape it does not understand. Handed a row both sides changed in ways the
rules below cannot rank, it names the id and exits 1, leaving the conflict
for a reader. Exit 0 is the only outcome that writes a file.

THE RULES, in the order they are tried on each row:
  1. changed on one side only: that side wins.
  2. changed on both sides identically: that value.
  3. changed on both sides differently: the side whose status is further
     along the ladder wins (DONE, IN-FLIGHT, PARTIAL, OPEN). A status the
     ladder does not name (SUPERSEDED, anything new) is UNRANKED: it can
     only win by being identical to the other side's, never by comparison.
  4. same status: the longer evidence wins, on the reasoning that evidence
     is only ever appended to as a row is proved.
  5. nothing separates them: undecidable, exit 1.
Rows added by either side are kept beside the last base row carrying their
section, so a new section lands at the end rather than in the middle of one
that already exists. Every non-row top level field takes ours unless only
theirs changed it. The output is re-serialised with the indent, the escaping
and the key order MEASURED FROM THE BASE, so a merge that changes nothing
reproduces the file byte for byte.

Python 3, standard library only. No em or en dashes anywhere in this file.
"""
import argparse
import json
import re
import subprocess
import sys

# The ladder, most advanced first. Read from the file's own vocabulary:
# DONE, IN-FLIGHT and OPEN are live in the roadmap today, PARTIAL is named by
# the row contract. SUPERSEDED is deliberately ABSENT: it is a row being set
# aside, not a row moving forward, and ranking it either way would be a guess.
STATUS_LADDER = ('DONE', 'IN-FLIGHT', 'PARTIAL', 'OPEN')


class NoData(Exception):
    """The input is not the roadmap's shape. Exit 2, never a merge."""


class Undecidable(Exception):
    """The rules do not separate the two sides. Exit 1, never a merge."""


def measure_style(text):
    """Read the indent, the escaping and the trailing newline off the base."""
    m = re.search(r'\n(\s+)"', text)
    indent = len(m.group(1)) if m else 1
    return {
        'indent': indent,
        'ensure_ascii': all(ord(ch) < 128 for ch in text),
        'trailing_newline': text.endswith('\n'),
    }


def serialise(doc, style):
    out = json.dumps(doc, indent=style['indent'],
                     ensure_ascii=style['ensure_ascii'])
    return out + '\n' if style['trailing_newline'] else out


def validate(doc, side):
    """Refuse anything that is not the roadmap's shape. Raises NoData."""
    if not isinstance(doc, dict):
        raise NoData('%s is not a JSON object' % side)
    rows = doc.get('rows')
    if not isinstance(rows, list):
        raise NoData('%s has no rows array' % side)
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise NoData('%s row %d is not an object' % (side, i))
        rid = row.get('id')
        if not isinstance(rid, str) or not rid:
            raise NoData('%s row %d has no id' % (side, i))
        if rid in seen:
            raise NoData('%s has duplicate row id %s' % (side, rid))
        seen.add(rid)
    return rows


def by_id(rows):
    return dict((r['id'], r) for r in rows)


def rank(row):
    """Position on the ladder, or None for a status the ladder does not name."""
    status = row.get('status')
    return STATUS_LADDER.index(status) if status in STATUS_LADDER else None


def evidence_length(row):
    ev = row.get('evidence')
    if ev is None:
        return 0
    return len(ev if isinstance(ev, str) else json.dumps(ev, sort_keys=True))


def pick_row(rid, ours, theirs, log):
    """Both sides changed this row differently. Rank them or refuse."""
    ro, rt = rank(ours), rank(theirs)
    so, st = ours.get('status'), theirs.get('status')
    if so != st:
        if ro is None or rt is None:
            raise Undecidable(
                'row %s: status %r vs %r, one of them is off the ladder'
                % (rid, so, st))
        side, row, other = (('ours', ours, theirs) if ro < rt
                            else ('theirs', theirs, ours))
        log('roadmap-merge %s: took %s, status %s over %s'
            % (rid, side, row.get('status'), other.get('status')))
        return row
    lo, lt = evidence_length(ours), evidence_length(theirs)
    if lo == lt:
        raise Undecidable(
            'row %s: both sides changed it, same status %r, same evidence '
            'length %d' % (rid, so, lo))
    side, row = ('ours', ours) if lo > lt else ('theirs', theirs)
    log('roadmap-merge %s: took %s, status %s on both sides, evidence %d over %d'
        % (rid, side, so, max(lo, lt), min(lo, lt)))
    return row


def reorder(row, template):
    """Give a merged row the base row's key order, new keys appended."""
    if template is None:
        return row
    out = dict((k, row[k]) for k in template if k in row)
    for k in row:
        if k not in out:
            out[k] = row[k]
    return out


def merge_rows(base_rows, our_rows, their_rows, log):
    b, o, t = by_id(base_rows), by_id(our_rows), by_id(their_rows)
    merged = {}
    for rid in set(b) | set(o) | set(t):
        br, orow, trow = b.get(rid), o.get(rid), t.get(rid)
        if br is None:
            # added on one side, or on both.
            if orow is not None and trow is not None and orow != trow:
                raise Undecidable(
                    'row %s: added on both sides with different content' % rid)
            merged[rid] = orow if orow is not None else trow
            continue
        if orow is None and trow is None:
            continue                          # deleted on both sides
        if orow is None:
            if trow == br:
                continue                      # deleted by ours, untouched by theirs
            raise Undecidable('row %s: deleted by ours, changed by theirs' % rid)
        if trow is None:
            if orow == br:
                continue                      # deleted by theirs, untouched by ours
            raise Undecidable('row %s: deleted by theirs, changed by ours' % rid)
        if orow == trow:
            merged[rid] = orow
        elif trow == br:
            merged[rid] = orow                # only ours changed it
        elif orow == br:
            merged[rid] = trow                # only theirs changed it
        else:
            merged[rid] = pick_row(rid, orow, trow, log)
        merged[rid] = reorder(merged[rid], br)

    # Order: the base order, then each added row beside the LAST base row
    # carrying its section, so a new section lands at the end rather than in
    # the middle of one that already exists.
    order = [r['id'] for r in base_rows if r['id'] in merged]
    placed = set(order)
    added = [r['id'] for r in list(our_rows) + list(their_rows)
             if r['id'] in merged and r['id'] not in placed]
    for rid in dict.fromkeys(added):
        section = merged[rid].get('section')
        anchor = None
        for i, oid in enumerate(order):
            if merged[oid].get('section') == section:
                anchor = i
        order.insert(anchor + 1 if anchor is not None else len(order), rid)
    return [merged[rid] for rid in order]


def merge(base, ours, theirs, log):
    base_rows = validate(base, 'base')
    our_rows = validate(ours, 'ours')
    their_rows = validate(theirs, 'theirs')
    rows = merge_rows(base_rows, our_rows, their_rows, log)

    keys = list(base)
    keys += [k for k in ours if k not in base]
    keys += [k for k in theirs if k not in base and k not in ours]
    out = {}
    for key in keys:
        if key == 'rows':
            out['rows'] = rows
            continue
        in_b, in_o, in_t = key in base, key in ours, key in theirs
        if in_o and (not in_b or ours[key] != base[key]):
            out[key] = ours[key]              # ours changed it, ours wins
        elif in_t and (not in_b or theirs[key] != base[key]):
            if in_o or not in_b:
                out[key] = theirs[key]        # only theirs changed it
        elif in_o and in_t:
            out[key] = base[key]              # neither side changed it
        # a key either side deleted while the other left it alone is dropped,
        # which is what git's line merge does with the same edit.
    return out


def read_json(text, side):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise NoData('%s is not valid JSON: %s' % (side, exc))


def read_file(path, side):
    try:
        with open(path, encoding='utf-8') as fh:
            return fh.read()
    except OSError as exc:
        raise NoData('cannot read %s (%s): %s' % (side, path, exc))


def git_stage(stage, path):
    """Read one conflict stage out of the index. Raises NoData when absent."""
    try:
        proc = subprocess.run(['git', 'show', ':%d:%s' % (stage, path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise NoData('cannot run git: %s' % exc)
    if proc.returncode != 0:
        raise NoData('no stage %d for %s in the index: %s'
                     % (stage, path,
                        proc.stderr.decode('utf-8', 'replace').strip()))
    return proc.stdout.decode('utf-8')


def resolve_merge_head():
    """The sha MERGE_HEAD names, or None when no merge is in progress.

    Tries the pseudo-ref first (works whenever git itself would resolve
    it); falls back to reading .git/MERGE_HEAD directly for a caller whose
    environment does not resolve the ref the normal way.
    """
    proc = subprocess.run(['git', 'rev-parse', '--verify', '-q', 'MERGE_HEAD'],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode == 0:
        sha = proc.stdout.decode('utf-8').strip()
        if sha:
            return sha
    gp = subprocess.run(['git', 'rev-parse', '--git-path', 'MERGE_HEAD'],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if gp.returncode != 0:
        return None
    try:
        with open(gp.stdout.decode('utf-8').strip(), encoding='utf-8') as fh:
            sha = fh.read().strip()
    except OSError:  # sbe: allow-silent documented sentinel: an unreadable MERGE_HEAD reads as no real base, and the caller then keeps the NO-DATA exit 2 rather than merging without a base
        return None
    return sha or None


def real_base_text(path, log):
    """On a criss-cross merge git puts a VIRTUAL base in index stage 1, built
    by merging the two real merge bases and leaving conflict markers where
    that virtual merge could not resolve. Fall back to a real base: the
    actual merge-base of HEAD and MERGE_HEAD. Raises NoData if none can be
    found or read, which keeps today's behaviour rather than guessing.
    """
    merge_head = resolve_merge_head()
    if merge_head is None:
        raise NoData('stage 1 is a virtual criss-cross base and no '
                     'MERGE_HEAD is present to find a real one')
    proc = subprocess.run(['git', 'merge-base', 'HEAD', merge_head],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise NoData('git merge-base HEAD %s failed: %s'
                     % (merge_head, proc.stderr.decode('utf-8', 'replace').strip()))
    base_sha = proc.stdout.decode('utf-8').strip().splitlines()[:1]
    base_sha = base_sha[0] if base_sha else ''
    if not base_sha:
        raise NoData('git merge-base HEAD %s returned no base' % merge_head)
    show = subprocess.run(['git', 'show', '%s:%s' % (base_sha, path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if show.returncode != 0:
        raise NoData('cannot read %s at merge base %s: %s'
                     % (path, base_sha,
                        show.stderr.decode('utf-8', 'replace').strip()))
    log('roadmap-merge: stage 1 is a virtual criss-cross base; using '
        'merge-base %s' % base_sha)
    return show.stdout.decode('utf-8')


def git_base_text(path, log):
    """Stage 1, unless it is a criss-cross virtual base (invalid JSON, or
    carrying git's own conflict markers), in which case fall back to the
    real merge base."""
    text = git_stage(1, path)
    if 'Temporary merge branch' in text:
        return real_base_text(path, log)
    try:
        json.loads(text)
    except ValueError:
        return real_base_text(path, log)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Three way merge of the readiness roadmap JSON, by row id.')
    ap.add_argument('base', nargs='?', help='the common ancestor file')
    ap.add_argument('ours', nargs='?')
    ap.add_argument('theirs', nargs='?')
    ap.add_argument('-o', '--out', help='where to write the merged file')
    ap.add_argument('--git', metavar='PATH',
                    help='read the three stages from the index for PATH '
                         '(git show :1: :2: :3:) and write back to PATH')
    args = ap.parse_args(argv)
    log = lambda line: print(line)

    try:
        if args.git:
            base_text = git_base_text(args.git, log)
            our_text = git_stage(2, args.git)
            their_text = git_stage(3, args.git)
            out_path = args.out or args.git
        else:
            if not (args.base and args.ours and args.theirs and args.out):
                ap.error('give base, ours, theirs and -o, or use --git PATH')
            base_text = read_file(args.base, 'base')
            our_text = read_file(args.ours, 'ours')
            their_text = read_file(args.theirs, 'theirs')
            out_path = args.out

        style = measure_style(base_text)
        merged = merge(read_json(base_text, 'base'),
                       read_json(our_text, 'ours'),
                       read_json(their_text, 'theirs'),
                       log)
        text = serialise(merged, style)
    except NoData as exc:
        print('roadmap-merge: NO-DATA %s' % exc, file=sys.stderr)
        return 2
    except Undecidable as exc:
        print('roadmap-merge: CONFLICT %s' % exc, file=sys.stderr)
        return 1

    try:
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write(text)
    except OSError as exc:
        print('roadmap-merge: NO-DATA cannot write %s: %s' % (out_path, exc),
              file=sys.stderr)
        return 2
    print('roadmap-merge: merged %d rows into %s'
          % (len(merged['rows']), out_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
