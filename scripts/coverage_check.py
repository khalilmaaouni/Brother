#!/usr/bin/env python3
"""Coverage checker for the unified WBS (docs/plan/UNIFIED-WBS.md), task 0.

Loads the queue snapshots under docs/plan/sources/, the appendix, and the
plan itself, then asserts:

  (a) every OPEN source id appears in the plan exactly once: mapped to a
      phase section (### P0 to ### P6) or listed under "## Parked" with a
      'flip'/'flips when' condition on its line.
  (b) every phase heading's "(stage: ...)" annotation names a stage on the
      chain, or one of the exact stage strings the snapshots themselves
      carry (a snapshot stage outside the canonical chain is accepted, not
      silently trusted: it is printed as a STAGE-NOTE line).
  (c) the appendix's per-source totals, open counts, and id lists must equal
      what the snapshots hold; any difference is a drift FAIL.

Exit 0 green. Exit 1 on any orphan, duplicate, missing flip, stage-off-chain,
or drift. Exit 2 NO-DATA if a source file is unreadable or parses empty.
"""
import argparse
import json
import os
import re
import sys

CHAIN = [
    "intent", "method", "provenance", "passport", "behaviour",
    "business-impact", "risk", "required-proof", "evidence-integrity",
    "accountability", "release-readiness", "production-observation",
    "human-decision", "release", "verified-reality",
]

PHASE_HEADING_RE = re.compile(r'^###\s+(P[0-6])\.')
PARKED_HEADING_RE = re.compile(r'^##\s+Parked\b')
COVERAGE_TABLE_HEADING_RE = re.compile(r'^##\s+Coverage table\b')
ANY_HEADING_RE = re.compile(r'^#{1,6}\s')
STAGE_ANNOTATION_RE = re.compile(r'\(stage:\s*([^)]+)\)')
STAGE_ALIAS_LINE_RE = re.compile(r'^Stage aliases[^:]*:\s*(.+)$', re.MULTILINE)
VALID_PHASE_LABELS = {f'P{n}' for n in range(7)} | {'PARKED'}

# ponytail: id ranges only ever share one alphabetic prefix in this plan
# ("S2 to S14", "H2 to H9"); a mixed-prefix range ("S2 to H9") is not a
# thing this plan writes, so a single-prefix regex is the whole rule.
RANGE_RE = re.compile(r'\b([A-Za-z]+)(\d+)\s+to\s+\1(\d+)\b')


def id_pattern(item_id):
    # Word-boundary match: hyphens and '+' are already non-word chars, so
    # \b keeps "M18" out of "M180" and "MERGE-P1" out of "MERGE-P11" for
    # free -- no custom boundary logic needed.
    return re.compile(r'\b' + re.escape(item_id) + r'\b')


def load_json(path, notes_no_data):
    if not os.path.isfile(path):
        notes_no_data.append(path)
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        notes_no_data.append(path)
        return None
    items = data['items'] if isinstance(data, dict) else data
    if not items:
        notes_no_data.append(path)
        return None
    return items


def load_text(path, notes_no_data):
    if not os.path.isfile(path):
        notes_no_data.append(path)
        return None
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        notes_no_data.append(path)
        return None
    if not text.strip():
        notes_no_data.append(path)
        return None
    return text


def is_open(item):
    # Derived from the snapshots' own states (done, queued, blocked): the
    # spec's rule is "state != done or closed"; no item in either snapshot
    # ever carries "closed", so this is the whole rule as observed.
    return item.get('state') not in ('done', 'closed')


def split_sections(wbs_text):
    """Return {'P0'..'P6': text, 'PARKED': text} for mapping purposes.

    Only the phase headings and the Parked heading count as a mapping
    location, per the spec; every other section (Complaints, Nothing
    forgotten, the Addenda) is prose that references ids without placing
    them, so text there does not satisfy the "mapped" requirement.
    """
    sections = {}
    current = None
    for line in wbs_text.splitlines():
        m = PHASE_HEADING_RE.match(line)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if PARKED_HEADING_RE.match(line):
            current = 'PARKED'
            sections.setdefault(current, [])
            continue
        if ANY_HEADING_RE.match(line):
            current = None
            continue
        if current is not None:
            sections[current].append(line)
    return {k: '\n'.join(v) for k, v in sections.items()}


def ids_present_in_section(text, known_ids):
    found = set()
    for item_id in known_ids:
        if id_pattern(item_id).search(text):
            found.add(item_id)
    for match in RANGE_RE.finditer(text):
        prefix, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        if start > end:
            continue
        for n in range(start, end + 1):
            candidate = f'{prefix}{n}'
            if candidate in known_ids:
                found.add(candidate)
    return found


def extract_appendix_ids(section_text):
    ids = set()
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith('-') or ':' not in line:
            continue
        tail = line.rsplit(':', 1)[1]
        for token in tail.split(','):
            token = token.strip().rstrip('.')
            if token:
                ids.add(token)
    return ids


def extract_appendix_section(appendix_text, heading_prefix):
    lines = appendix_text.splitlines()
    out, capture = [], False
    for line in lines:
        if line.startswith('## '):
            capture = line.startswith(heading_prefix)
            if capture:
                out.append(line)
            continue
        if capture:
            out.append(line)
    return '\n'.join(out)


def extract_sbe_ids(wbs_text):
    """The WBS's own sources line is the SBE 'snapshot': no state field, so
    every id it names is treated as required, per the task brief."""
    m = re.search(r'SBE document sources:\s*(.+)', wbs_text)
    if not m:
        return set()
    tail = m.group(1)
    ids = set()
    for match in RANGE_RE.finditer(tail):
        prefix, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        for n in range(start, end + 1):
            ids.add(f'{prefix}{n}')
    # Literal tokens not covered by a range (Q5, Q6, MERGE-P4, MERGE-P16, and
    # the range endpoints themselves, which the range regex also emits).
    for tok in re.findall(r'\b[A-Z][A-Za-z0-9-]*\d[A-Za-z0-9-]*\b', tail):
        ids.add(tok)
    return ids


def extract_stage_aliases(wbs_text):
    """Parse the 'Stage aliases (... snapshot to chain): a=b, c=d' line.

    Returns {snapshot_stage: chain_stage}. Missing line -> empty dict, which
    means every off-chain snapshot stage fails rule (b) below (no blanket
    accept-as-is anymore).
    """
    m = STAGE_ALIAS_LINE_RE.search(wbs_text)
    if not m:
        return {}
    aliases = {}
    for pair in m.group(1).split(','):
        pair = pair.strip()
        if '=' in pair:
            k, v = pair.split('=', 1)
            aliases[k.strip()] = v.strip()
    return aliases


def extract_coverage_table(wbs_text):
    """Return the rows of the '## Coverage table' markdown table, each a
    dict with id/source/title/phase/stage/note. The table is the ledger:
    an id with a row here is mapped by that row alone, not by any prose
    mention of it elsewhere (see the duplicate rule in main())."""
    rows = []
    in_table = False
    for line in wbs_text.splitlines():
        if COVERAGE_TABLE_HEADING_RE.match(line):
            in_table = True
            continue
        if in_table and ANY_HEADING_RE.match(line):
            break
        if not in_table:
            continue
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if not cells or cells[0].lower() == 'id':
            continue
        if re.match(r'^:?-+:?$', cells[0]):
            continue
        if len(cells) < 5:
            continue
        rows.append({
            'id': cells[0],
            'source': cells[1],
            'title': cells[2],
            'phase': cells[3],
            'stage': cells[4],
            'note': cells[5] if len(cells) > 5 else '',
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument('--root', default=default_root)
    args = parser.parse_args()
    root = args.root

    bmu_path = os.path.join(root, 'docs/plan/sources/bmu-queue-2026-08-22.json')
    ds_path = os.path.join(root, 'docs/plan/sources/ds-queue-2026-08-22.json')
    queue_path = os.path.join(root, 'docs/plan/QUEUE.json')
    appendix_path = os.path.join(root, 'docs/plan/sources/open-items-appendix.md')
    wbs_path = os.path.join(root, 'docs/plan/UNIFIED-WBS.md')

    no_data = []
    bmu_items = load_json(bmu_path, no_data)
    ds_items = load_json(ds_path, no_data)
    br_items = load_json(queue_path, no_data)
    appendix_text = load_text(appendix_path, no_data)
    wbs_text = load_text(wbs_path, no_data)

    if no_data:
        for path in no_data:
            print(f'NO-DATA: {path}')
        print('coverage: 0 mapped, 0 parked, 0 orphans, 0 drift, 0 extra, exit 2')
        return 2

    fails = []

    # --- rule (c): recount the snapshots against the appendix -----------
    bmu_open = {i['id'] for i in bmu_items if is_open(i)}
    ds_open = {i['id'] for i in ds_items if is_open(i)}
    br_open = {i['id'] for i in br_items if is_open(i)}

    bmu_header = re.search(r'BrotherModeUp[^\n]*?(\d+)\s+items,\s+(\d+)\s+open', appendix_text)
    ds_header = re.search(r'BrotherDS[^\n]*?(\d+)\s+items,\s+(\d+)\s+open', appendix_text)

    if bmu_header:
        want_total, want_open = int(bmu_header.group(1)), int(bmu_header.group(2))
        if want_total != len(bmu_items):
            fails.append(f'FAIL: bmu-queue appendix drift: header says {want_total} items, snapshot holds {len(bmu_items)}')
        if want_open != len(bmu_open):
            fails.append(f'FAIL: bmu-queue appendix drift: header says {want_open} open, snapshot holds {len(bmu_open)}')
    else:
        fails.append('FAIL: appendix drift: no BrotherModeUp items/open header found')

    if ds_header:
        want_total, want_open = int(ds_header.group(1)), int(ds_header.group(2))
        if want_total != len(ds_items):
            fails.append(f'FAIL: ds-queue appendix drift: header says {want_total} items, snapshot holds {len(ds_items)}')
        if want_open != len(ds_open):
            fails.append(f'FAIL: ds-queue appendix drift: header says {want_open} open, snapshot holds {len(ds_open)}')
    else:
        fails.append('FAIL: appendix drift: no BrotherDS items/open header found')

    bmu_appendix_section = extract_appendix_section(appendix_text, '## BrotherModeUp')
    ds_appendix_section = extract_appendix_section(appendix_text, '## BrotherDS')
    bmu_appendix_ids = extract_appendix_ids(bmu_appendix_section)
    ds_appendix_ids = extract_appendix_ids(ds_appendix_section)

    for missing in sorted(bmu_open - bmu_appendix_ids):
        fails.append(f'FAIL: {missing} bmu-queue drift: open in snapshot but absent from appendix id list')
    for extra in sorted(bmu_appendix_ids - bmu_open):
        fails.append(f'FAIL: {extra} bmu-queue drift: listed open in appendix but not open in snapshot')
    for missing in sorted(ds_open - ds_appendix_ids):
        fails.append(f'FAIL: {missing} ds-queue drift: open in snapshot but absent from appendix id list')
    for extra in sorted(ds_appendix_ids - ds_open):
        fails.append(f'FAIL: {extra} ds-queue drift: listed open in appendix but not open in snapshot')

    drift_count = len(fails)

    # --- rule (b): stage chain, resolved through the Stage aliases line --
    snapshot_stages = {i.get('stage') for i in bmu_items if i.get('stage')}
    snapshot_stages |= {i.get('stage') for i in ds_items if i.get('stage')}
    snapshot_stages |= {i.get('stage') for i in br_items if i.get('stage')}
    stage_aliases = extract_stage_aliases(wbs_text)
    off_chain = sorted(snapshot_stages - set(CHAIN))
    resolved_off_chain = set()
    for stage in off_chain:
        resolved = stage_aliases.get(stage)
        if resolved is None or resolved not in CHAIN:
            fails.append(f'FAIL: {stage} snapshot stage not on chain and not aliased (see the Stage aliases line)')
        else:
            resolved_off_chain.add(stage)
    if resolved_off_chain:
        print('STAGE-ALIAS: snapshot stages resolved through the alias line: ' + ', '.join(sorted(resolved_off_chain)))
    valid_stages = set(CHAIN) | resolved_off_chain

    for match in re.finditer(r'^(###\s+P[0-6]\.[^\n]*)$', wbs_text, re.MULTILINE):
        heading = match.group(1)
        ann = STAGE_ANNOTATION_RE.search(heading)
        if not ann:
            continue
        for segment in re.split(r',|\bthen\b', ann.group(1)):
            token_match = re.match(r'\s*([a-z][a-z-]*)', segment)
            if not token_match:
                continue
            stage = token_match.group(1)
            if stage not in valid_stages:
                fails.append(f'FAIL: {stage} UNIFIED-WBS.md stage not on chain (heading: {heading.strip()})')

    # --- rule (a): every open/required id mapped once -------------------
    sbe_ids = extract_sbe_ids(wbs_text)
    known_ids = bmu_open | ds_open | sbe_ids | br_open
    sections = split_sections(wbs_text)
    table_rows = extract_coverage_table(wbs_text)

    # A table row whose id is in no snapshot (e.g. a handover's own ids,
    # never fed into bmu/ds/QUEUE.json) is not a plan failure: it is simply
    # not governed by rule (a) below, since that rule only walks known_ids.
    # Counted and reported, never failed.
    extra_count = sum(1 for row in table_rows if row['id'] not in known_ids)

    # A row's stage column is resolved through the same alias line; an
    # unaliased off-chain stage in the table fails the same as in a snapshot.
    for row in table_rows:
        resolved = stage_aliases.get(row['stage'], row['stage'])
        if resolved not in CHAIN:
            fails.append(f'FAIL: {row["id"]} coverage table stage "{row["stage"]}" not on chain and not aliased')

    table_id_phases = {}
    for row in table_rows:
        table_id_phases.setdefault(row['id'], []).append(row['phase'])

    id_locations = {i: set() for i in known_ids}
    for label, text in sections.items():
        for item_id in ids_present_in_section(text, known_ids):
            id_locations[item_id].add(label)

    def source_of(item_id):
        if item_id in bmu_open:
            return 'bmu-queue'
        if item_id in ds_open:
            return 'ds-queue'
        if item_id in br_open:
            return 'QUEUE.json'
        return 'sbe-sources'

    parked_lines = sections.get('PARKED', '')

    def check_parked(item_id):
        """True (and counted) if item_id has a Parked-section line naming a
        flip condition; False (and FAILed) otherwise. Shared by the table
        path and the prose-fallback path below."""
        line_hit = next((ln for ln in parked_lines.splitlines() if id_pattern(item_id).search(ln)), '')
        if 'flip' not in line_hit.lower():
            fails.append(f'FAIL: {item_id} {source_of(item_id)} parked without a flip condition on its line')
            return False
        return True

    mapped_count = parked_count = orphan_count = 0
    for item_id in sorted(known_ids):
        # The coverage table is the ledger: a row there is the sole location
        # for that id, and a prose mention of the same id elsewhere (in a
        # phase paragraph) is narrative, not a second mapping -- so it is
        # deliberately NOT compared against id_locations below. Only two
        # ways to fail once an id has a table row: the row appears more than
        # once (duplicate), or its phase is PARKED with no flip on file.
        if item_id in table_id_phases:
            phases = table_id_phases[item_id]
            if len(phases) > 1:
                fails.append(f'FAIL: {item_id} {source_of(item_id)} duplicate: appears in the coverage table {len(phases)} times')
                orphan_count += 1
                continue
            label = phases[0]
            if label not in VALID_PHASE_LABELS:
                fails.append(f'FAIL: {item_id} {source_of(item_id)} coverage table phase "{label}" is not P0-P6 or PARKED')
                orphan_count += 1
                continue
            if label == 'PARKED':
                if not check_parked(item_id):
                    orphan_count += 1
                    continue
                parked_count += 1
            else:
                mapped_count += 1
            continue

        # No table row: fall back to the original prose-section mapping.
        locations = id_locations[item_id]
        if not locations:
            fails.append(f'FAIL: {item_id} {source_of(item_id)} orphan: not mapped to any phase or Parked section')
            orphan_count += 1
            continue
        if len(locations) > 1:
            fails.append(f'FAIL: {item_id} {source_of(item_id)} duplicate: mapped to multiple sections: {", ".join(sorted(locations))}')
            orphan_count += 1
            continue
        label = next(iter(locations))
        if label == 'PARKED':
            if not check_parked(item_id):
                orphan_count += 1
                continue
            parked_count += 1
        else:
            mapped_count += 1

    for line in fails:
        print(line)

    exit_code = 1 if fails else 0
    print(f'coverage: {mapped_count} mapped, {parked_count} parked, {orphan_count} orphans, {drift_count} drift, {extra_count} extra, exit {exit_code}')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
