#!/usr/bin/env python3
"""Scoring harness for the Intake 9.5 rubric (docs/plan/INTAKE-9.5-DESIGN.md
section A), which makes the "9.5 out of 10" bar measurable instead of an
adjective.

Scores one intake record (a markdown file the intake produces) against the
rubric, per persona (ba or dev). What the record's text can prove
mechanically gets a real score with evidence; what needs a real proxy
session transcript (turns, process questions, override rate, repeat
questions, a verified receipt) prints NO-DATA and its weight is excluded
from the total, never guessed and never silently zeroed.

Runs on the 3.9 floor (/usr/bin/python3): no match statements, no syntax
newer than 3.9, standard library only.

Exit 0 when something was scored. Exit 2 when nothing could be scored at
all, or when arguments or the record file itself could not be read.
"""
import argparse
import difflib
import os
import re
import sys
from collections import namedtuple

# The rubric's criterion 8, SEQUENCING AND RECEIPT INTEGRITY, weight 15,
# bundles two facts that need different evidence: sequencing (plan visible
# before any approval prompt) is checkable from the record text alone;
# receipt integrity (the close's check ran after the last edit) needs a
# session log this script never sees. This harness scores them as two rows
# so a mechanical fact and a NO-DATA fact are never folded under one score.
#
# ROW R6, 2026-08-29: OPTIONS_WITH_RECOMMENDATION, weight 10, bundled a
# second fact the same way. It scored whether options exist with a
# recommendation (mechanical, unchanged below) but never checked whether
# those options carry a real WEIGHTED comparison across the same named
# criteria, which is the founder's own stated ask for this row: "pros and
# cons WEIGHTED options". Split on the exact precedent of row 8 above:
# options_with_recommendation KEEPS 5 of its original 10, and the new
# weighted_options row TAKES the other 5, so "the Options section is good"
# still totals 10 and the grand total still sums to 100. The five points
# come FROM options_with_recommendation specifically, not shaved a point off
# every other row, because that is the fact it was already claiming credit
# for without ever measuring it.
# Weights below sum to 100, matching the design's eight criteria (with row 8
# split into two rows summing to its original 15, and
# options_with_recommendation split into two rows summing to its original 10).
CRITERIA_WEIGHTS = [
    ("orientation", 10),
    ("interaction_economy", 15),
    ("grounded_assumptions", 15),
    ("level_adaptation", 15),
    ("options_with_recommendation", 5),
    ("weighted_options", 5),
    ("diagrams_by_default", 10),
    ("convergence", 10),
    ("sequencing", 10),
    ("receipt_integrity", 5),
]
assert sum(w for _, w in CRITERIA_WEIGHTS) == 100

CriterionResult = namedtuple("CriterionResult", ["name", "weight", "score", "evidence"])

HEADING_RE = re.compile(r'^(#+)\s*(.+?)\s*$')
BULLET_RE = re.compile(r'^\s*[-*]\s+\S')
OPTION_ITEM_RE = re.compile(r'^(?:\d+\.|[-*])\s+\S')
BACKTICK_RE = re.compile(r'`([^`\n]+)`')
PATH_TOKEN_RE = re.compile(r'\b[\w][\w\-]*(?:/[\w.\-]+)+\b')
# A DIAGRAM IS NOT A SUBSTRING. This used to be re.compile(r'```mermaid\b') and
# an adversarial audit passed the gate three separate ways with no diagram present:
# a sentence that merely MENTIONED the opener, an EMPTY fence, and an UNCLOSED
# ```MERMAID-anything in mid-file. All three proved only that some bytes appeared
# somewhere. A fence now counts only when it opens on its own line, CLOSES, and
# holds at least one non-blank line between the two.
#
# INDENT IS CAPPED AT 3 SPACES, per CommonMark/GFM: a fence indented 4 or more
# spaces (outside a list item) is an INDENTED CODE BLOCK, which GitHub renders as
# literal text, not a diagram. A second audit found the prior `^\s*` accepted any
# indent, so a 4-space-indented ```mermaid fence scored as a valid diagram while
# GitHub rendered zero. KNOWN CEILING, the safe direction for a gate: a fence
# nested inside a list item can legitimately sit deeper than 3 spaces and this
# will miss it (false negative, never a false positive).
MERMAID_OPEN_RE = re.compile(r'^ {0,3}(`{3,})(.*)$')
MERMAID_CLOSE_RE = re.compile(r'^ {0,3}(`{3,})\s*$')


def _mermaid_open_backtick_count(line):
    """Backtick count if `line` opens a mermaid fence, else None. Per GFM the
    info string's FIRST WORD names the language, so ```mermaid TB (a suffix
    after the language) still counts, while ```mermaidTB (one word) does not."""
    m = MERMAID_OPEN_RE.match(line)
    if not m:
        return None
    info = m.group(2).strip()
    if not info or info.split(None, 1)[0].lower() != 'mermaid':
        return None
    return len(m.group(1))


def count_mermaid_blocks(text):
    """Number of CLOSED, NON-EMPTY mermaid fences. An opener with no closer, or a
    fence whose body is blank, is not a diagram and is not counted. Per
    CommonMark the closing fence must carry AT LEAST as many backticks as the
    opener; a closer with fewer is not a close and is just more body text."""
    lines = text.split('\n')
    count = 0
    i = 0
    while i < len(lines):
        min_backticks = _mermaid_open_backtick_count(lines[i])
        if min_backticks is None:
            i += 1
            continue
        j = i + 1
        body = []
        while j < len(lines):
            close = MERMAID_CLOSE_RE.match(lines[j])
            if close and len(close.group(1)) >= min_backticks:
                break
            body.append(lines[j])
            j += 1
        if j < len(lines) and any(ln.strip() for ln in body):
            count += 1          # opened, closed, and carries content
        i = j + 1 if j < len(lines) else j
    return count


def find_section(lines, predicate):
    """Return (start, end) content-line range (0-indexed, end exclusive)
    for the first heading whose title matches predicate. The section ends
    at the next heading of the same or a shallower level, or EOF. None if
    no matching heading exists."""
    start = None
    start_level = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2)
        if start is None:
            if predicate(title):
                start = i + 1
                start_level = level
            continue
        if level <= start_level:
            return start, i
    if start is not None:
        return start, len(lines)
    return None


def find_first_heading_index(lines, predicate):
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and predicate(m.group(2)):
            return i
    return None


def find_repo_root(start_dir):
    """Nearest directory at or above start_dir carrying a .git entry (a
    directory for an ordinary clone, a file for a worktree's gitdir
    pointer, per CHECK 1). None when no ancestor carries one, all the way
    to the filesystem root."""
    d = os.path.abspath(start_dir)
    while True:
        if os.path.exists(os.path.join(d, '.git')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def has_citation(line, root):
    """True if the line carries a citation: a file path, a backticked path
    that RESOLVES to a real file under root (CHECK 1: a backticked path
    that does not exist counts as uncited, never as a free pass), or the
    explicit token UNGROUNDED. root is required: the caller (score_grounded_
    assumptions) refuses to score at all, NO-DATA, when no root is
    resolvable, rather than guess at existence."""
    if 'UNGROUNDED' in line:
        return True
    for m in BACKTICK_RE.finditer(line):
        content = m.group(1)
        if '/' in content or re.search(r'\.[A-Za-z0-9]{1,5}$', content):
            resolved = os.path.join(root, content.lstrip('/'))
            if os.path.exists(resolved):
                return True
            # a backticked path that does not exist is not a citation; keep
            # looking (another backtick, or a bare path token, may still be)
            continue
    # Scan for a bare (non-backticked) path OUTSIDE the backtick spans: a
    # nonexistent backticked path is still plain text carrying the same
    # slash-shaped substring, and PATH_TOKEN_RE does not know backticks
    # exist, so without this it would re-match the very path just
    # rejected above and turn a missing citation back into a pass.
    outside_backticks = BACKTICK_RE.sub(' ', line)
    if PATH_TOKEN_RE.search(outside_backticks):
        return True
    return False


# CHECK 4, 2026-09-02: THE LEVEL AND SEQUENCING CHECKS MUST NOT FIRE ON
# ORDINARY ENGLISH. Adversarial testing found real false positives: "and/or",
# "him/her" and "w/o" all satisfy PATH_TOKEN_RE (a single slash between two
# short words), and "make sure", "bash out a fix" and "git this done" all
# satisfy the bare command-keyword scan (the keyword is also an ordinary
# English word). Both leaked a false "technical content" penalty into a
# clean BA-view record.
def _looks_like_a_real_path(token):
    """A slash alone is not enough: ordinary English routinely uses a
    single slash between two short words. A token counts as a real path
    only when it carries a recognizable file extension on its last
    segment, or spans at least two slashes (three or more directory
    segments) -- the shape ordinary English slash idioms never take."""
    if re.search(r'\.[A-Za-z0-9]{1,5}$', token):
        return True
    return token.count('/') >= 2


def contains_path(text):
    for m in PATH_TOKEN_RE.finditer(text):
        if _looks_like_a_real_path(m.group(0)):
            return True
    for m in BACKTICK_RE.finditer(text):
        content = m.group(1)
        if '/' in content or re.search(r'\.[A-Za-z0-9]{1,5}$', content):
            return True
    return False


COMMAND_WORD_RE = re.compile(r'\b(?:npm|git|python3?|pip|curl|bash|sh|make)\b\s+\S')


def contains_command(text):
    if re.search(r'(?m)^\s*\$\s+\S', text):
        return True
    # Backticked/fenced command text is unambiguous: a code span or fence
    # naming these tools is never ordinary prose.
    for m in BACKTICK_RE.finditer(text):
        if COMMAND_WORD_RE.search(m.group(1)):
            return True
    if re.search(r'```(?:bash|sh|shell|console|zsh)\b[^`]*```', text, re.IGNORECASE | re.DOTALL):
        return True
    # A bare keyword outside backticks is only real command syntax when the
    # rest of its line carries command-shaped evidence too: a flag (-x /
    # --long) or a path-looking argument. Without that, "make sure" and
    # "bash out a fix" are ordinary English that happens to use a command's
    # name as a word.
    for m in COMMAND_WORD_RE.finditer(text):
        line_start = text.rfind('\n', 0, m.start()) + 1
        line_end = text.find('\n', m.end())
        line = text[line_start:line_end if line_end != -1 else len(text)]
        if re.search(r'(?:^|\s)-{1,2}\w', line[m.end() - line_start:]):
            return True
        if contains_path(line[m.end() - line_start:]):
            return True
    return False


def contains_json(text):
    if re.search(r'```json\b', text, re.IGNORECASE):
        return True
    if re.search(r'(?m)^\s*\{[^{}\n]*:[^{}\n]*\}\s*$', text):
        return True
    return False


# ---------------------------------------------------------------------- #
# Mechanically measurable criteria
# ---------------------------------------------------------------------- #

ASSUMPTION_LINE_RE = re.compile(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)?assumption\b', re.IGNORECASE)


def score_grounded_assumptions(lines, root):
    # CHECK 1: citation paths must exist. Without a resolvable repository
    # root, a backticked path cannot be checked for existence at all, so this
    # criterion abstains (NO-DATA) rather than silently trusting or failing
    # every citation.
    if root is None:
        return None, ("no --root supplied and no .git found above the record: "
                      "cannot resolve citation paths")
    # Two ways an intake states an assumption, and BOTH are scored. Scoring only
    # the first was a real defect: a record that stated "Assumption: X (`path`)"
    # as a plain line, which is how intakes actually write, returned NO-DATA on a
    # criterion the design calls mechanically scoreable. A rubric that abstains on
    # the common case measures the FORMAT, not the quality.
    # 1. Bullet lines inside a section headed "Assumptions".
    # 2. Any line anywhere that opens with the word Assumption.
    assumption_idxs = []
    sec = find_section(lines, lambda t: re.search(r'\bassumptions?\b', t, re.IGNORECASE))
    if sec is not None:
        start, end = sec
        assumption_idxs = [i for i in range(start, end) if BULLET_RE.match(lines[i])]
    inline_idxs = [i for i in range(len(lines)) if ASSUMPTION_LINE_RE.match(lines[i])]
    for i in inline_idxs:
        if i not in assumption_idxs:
            assumption_idxs.append(i)
    assumption_idxs.sort()
    total = len(assumption_idxs)
    if total == 0:
        # SCORED LOW, never NO-DATA. An adversarial test found that abstaining here
        # let a record with NO assumptions at all score 10.0 with the floor rule
        # holding, because an abstention drops the criterion out of the denominator.
        # That made writing nothing the cheapest route to a perfect score, which is
        # the opposite of what this rubric exists to reward. A record that states no
        # assumptions has not met the criterion; it has skipped it.
        return 0.0, "no assumption stated anywhere in the record: this is a score of zero, not an abstention"
    # An assumption is an ITEM, not a line. Real records wrap, so the citation
    # often sits on a continuation line: reading one line at a time reported a
    # correctly cited assumption as uncited, which is the scorer blaming the
    # document for its own blind spot. Gather each assumption's full text from
    # its opening line through the following indented or non-blank continuation
    # lines, stopping at the next assumption, a blank-then-heading, or a bullet.
    def item_text(idx):
        parts = [lines[idx]]
        j = idx + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                break
            if BULLET_RE.match(nxt) or ASSUMPTION_LINE_RE.match(nxt):
                break
            if nxt.lstrip().startswith('#'):
                break
            parts.append(nxt)
            j += 1
        return " ".join(parts)

    uncited = [i + 1 for i in assumption_idxs if not has_citation(item_text(i), root)]
    # Honest labeling earns partial credit, never full marks. An adversary scored
    # 10.0 on three content-free assumptions each suffixed UNGROUNDED: the label is
    # meant to flag a gap for someone to close, not to be a way of passing.
    grounded_texts = [item_text(i) for i in assumption_idxs]
    real_citations = sum(1 for t in grounded_texts
                         if has_citation(t, root) and 'UNGROUNDED' not in t)
    cited = total - len(uncited)
    fraction = cited / total
    if fraction >= 0.8:
        score = 7.0 + (fraction - 0.8) / 0.2 * 3.0
        score = min(score, 10.0)
        if real_citations == 0:
            # every "citation" was the UNGROUNDED label: capped, never a 10.
            score = min(score, 6.0)
    else:
        score = fraction / 0.8 * 7.0
    score = round(score, 2)
    evidence = "%d/%d assumption line(s) cited; uncited line number(s): %s" % (
        cited, total, uncited if uncited else "none")
    return score, evidence


def score_diagrams(text):
    count = count_mermaid_blocks(text)
    if count == 0:
        score = 0.0
    elif count == 1:
        score = 10.0
    else:
        score = max(0.0, 10.0 - 3.0 * (count - 1))
    return score, "found %d fenced mermaid block(s)" % count


# CHECK 2, 2026-09-02: OPTIONS MUST BE DISTINCT, AND THE RECOMMENDATION MUST
# NAME ONE OF THEM. Two holes an adversarial pass found in score_options:
# raw item COUNT stood in for distinct options (two copies of the same
# option, reworded or not, counted as two), and "has_recommendation" was
# true the instant the word "recommend" appeared anywhere in the section,
# whether or not that sentence actually pointed at an option.
OPTION_MARKER_RE = re.compile(r'^\s*(?:\d+[.)]|[-*])\s+')
_NON_ALNUM_RE = re.compile(r'[^a-z0-9\s]')
# THE MEASURE OF "NEARLY THE SAME": two options are the same option when
# their normalized text (marker stripped, lowercased, punctuation dropped,
# whitespace collapsed) is identical, OR difflib.SequenceMatcher's ratio
# between them is >= 0.87. That threshold survives a one or two word
# reword ("to" -> "in") while still separating two options that share
# vocabulary but propose different things.
NEAR_DUPLICATE_RATIO = 0.87
OPTION_ORDINAL_RE = re.compile(r'\boption\s*#?\s*(\d+)\b', re.IGNORECASE)


def normalize_option_text(raw_line):
    """Strip a leading list marker/numbering, drop punctuation and case,
    and collapse whitespace, so two options that differ only in numbering,
    punctuation or capitalization compare as the same text."""
    text = OPTION_MARKER_RE.sub('', raw_line)
    text = _NON_ALNUM_RE.sub(' ', text.lower())
    return re.sub(r'\s+', ' ', text).strip()


def _option_words(text):
    """Words of length > 2 from already-lowercased-and-punctuation-stripped
    text; short connector words ('a', 'to', 'in') are dropped so overlap
    reflects the words that actually distinguish one option from another."""
    return set(w for w in text.split() if len(w) > 2)


def distinct_option_groups(items):
    """Collapse raw option item lines into groups of the SAME option, per
    NEAR_DUPLICATE_RATIO above. Returns a list of [normalized_representative,
    [raw_item_indices]] in first-seen order; len(result) is the distinct
    option count CHECK 2 asks for."""
    groups = []
    for idx, raw in enumerate(items):
        norm = normalize_option_text(raw)
        matched = None
        for g in groups:
            if norm == g[0] or difflib.SequenceMatcher(None, norm, g[0]).ratio() >= NEAR_DUPLICATE_RATIO:
                matched = g
                break
        if matched is not None:
            matched[1].append(idx)
        else:
            groups.append([norm, [idx]])
    return groups


def recommendation_names_an_option(content_lines, items, groups):
    """True only when a line carrying the word 'recommend' actually NAMES
    one of the distinct options: an explicit 'Option N' ordinal reference
    to one of the raw item lines, or the recommend line's own normalized
    words overlapping at least half of the SHORTER of (its words, an
    option group's representative words). The overlap test also covers the
    common real shape, a self-recommendation stated inside the option's
    own bullet line ("...Recommended: this option."), since that line's
    words trivially overlap themselves completely; a recommendation about
    something the Options section never proposed scores as missing."""
    group_word_sets = [_option_words(g[0]) for g in groups]
    for line in content_lines:
        if not re.search(r'\brecommend', line, re.IGNORECASE):
            continue
        m = OPTION_ORDINAL_RE.search(line)
        if m and 1 <= int(m.group(1)) <= len(items):
            return True
        line_words = _option_words(_NON_ALNUM_RE.sub(' ', line.lower()))
        if not line_words:
            continue
        for words in group_word_sets:
            if not words:
                continue
            overlap = len(line_words & words) / min(len(line_words), len(words))
            if overlap >= 0.5:
                return True
    return False


def score_options(lines):
    sec = find_section(lines, lambda t: re.search(r'\boptions?\b', t, re.IGNORECASE))
    if sec is None:
        return 0.0, "no Options section found, treated as non-trivial work needing options"
    start, end = sec
    content = lines[start:end]
    items = [ln for ln in content if OPTION_ITEM_RE.match(ln)]
    groups = distinct_option_groups(items)
    distinct_count = len(groups)
    named = recommendation_names_an_option(content, items, groups)
    if distinct_count == 0:
        score = 0.0
    elif named and 2 <= distinct_count <= 3:
        score = 10.0
    elif named:
        score = 7.0
    else:
        score = 5.0
    evidence = "%d distinct option(s) found (of %d item(s)), recommendation names one: %s" % (
        distinct_count, len(items), named)
    return score, evidence


# ---------------------------------------------------------------------- #
# WEIGHTED OPTIONS (row R6, 2026-08-29). An option set without weights is a
# list, not a comparison: the reader cannot tell which trade-off the
# recommendation actually turned on. Full marks need a GFM table in the
# Options section: a column headed Weight, at least two other (option)
# columns, at least two criterion rows, and every weight/score cell a plain
# stated number, so the arithmetic is checkable rather than merely claimed.
# ---------------------------------------------------------------------- #

TABLE_ROW_RE = re.compile(r'^\s*\|(.+)\|\s*$')
# A GFM separator row: pipes, colons, dashes and whitespace only, and it must
# hold at least one dash or a blank/prose line under it would false-match.
TABLE_SEP_RE = re.compile(r'^\s*\|?[\s:|-]+\|?\s*$')
WEIGHT_HEADER_RE = re.compile(r'weight', re.IGNORECASE)
# KNOWN CEILING, the safe direction: "8/10" or "high" in a cell is not
# matched, so a real but non-numerically-stated score reads as not fully
# checkable rather than as a false full mark.
CELL_NUMBER_RE = re.compile(r'^-?\d+(?:\.\d+)?%?$')


def _find_weight_table(content_lines):
    """First well formed GFM table (header + separator + >=1 data row) in
    content_lines, as a list of rows (each a list of stripped cell strings),
    or None. Column 0 of every row is treated as the criterion name, never
    an option column."""
    for i in range(len(content_lines) - 1):
        header_m = TABLE_ROW_RE.match(content_lines[i])
        if not header_m:
            continue
        if not TABLE_SEP_RE.match(content_lines[i + 1]) or '-' not in content_lines[i + 1]:
            continue
        rows = [[c.strip() for c in header_m.group(1).split('|')]]
        j = i + 2
        while j < len(content_lines):
            row_m = TABLE_ROW_RE.match(content_lines[j])
            if not row_m:
                break
            rows.append([c.strip() for c in row_m.group(1).split('|')])
            j += 1
        if len(rows) > 1:
            return rows
    return None


def score_weighted_options(lines):
    """A record's Options score full only when each option carries
    comparable, machine-readable weights across the SAME named criteria,
    stated so the arithmetic is checkable. Partial credit for weights that
    are present but not fully checkable; a bare pros-and-cons list with no
    weight signal at all scores ZERO, per the founder's own framing: an
    option set without weights is a list, not a comparison."""
    sec = find_section(lines, lambda t: re.search(r'\boptions?\b', t, re.IGNORECASE))
    if sec is None:
        return 0.0, "no Options section found, cannot carry weighted options"
    start, end = sec
    content = lines[start:end]
    table = _find_weight_table(content)
    if table is None:
        if re.search(r'\bweight', "\n".join(content), re.IGNORECASE):
            return 3.0, ("the word 'weight' appears in the Options section but no "
                          "machine-readable weight table was found: the arithmetic is not checkable")
        return 0.0, ("no weights found in the Options section: a pros-and-cons list, "
                      "not a weighted comparison")
    header, data_rows = table[0], table[1:]
    weight_cols = [i for i, h in enumerate(header) if WEIGHT_HEADER_RE.search(h)]
    if not weight_cols:
        return 3.0, "a table was found in the Options section but no column is headed Weight"
    weight_col = weight_cols[0]
    option_cols = [i for i in range(len(header)) if i not in (0, weight_col)]
    if len(option_cols) < 2:
        return 4.0, "a Weight column was found but fewer than two option columns to compare across"
    if len(data_rows) < 2:
        return 4.0, "a Weight column was found but fewer than two criterion rows to compare across"
    all_numeric = True
    for row in data_rows:
        for i in [weight_col] + option_cols:
            if i >= len(row) or not CELL_NUMBER_RE.match(row[i]):
                all_numeric = False
    if not all_numeric:
        return 5.0, ("a weight table with %d option column(s) and %d criterion row(s) was found, but "
                      "not every weight or score cell is a stated number: the arithmetic is not fully "
                      "checkable" % (len(option_cols), len(data_rows)))
    return 10.0, ("%d criterion row(s) weighted across %d option column(s), every weight and score "
                  "cell a stated number: the arithmetic is checkable" % (len(data_rows), len(option_cols)))


APPROVAL_PHRASE_RE = re.compile(
    r'\b(?:do you approve|approve this|please confirm|confirm to proceed)\b', re.IGNORECASE)


def _looks_like_an_approval_prompt(line):
    """CHECK 4: the bare-text fallback below exists for an approval prompt
    that is not written under its own heading, but the literal phrase can
    also appear inside ordinary prose describing something unrelated (a
    bulleted Assumption about a customer needing to "confirm to proceed
    with checkout" is not the intake asking ITS reader for approval). A
    line counts as a real prompt only when it is not a list item (a
    genuine ask is its own line, never folded into an Assumptions or
    Options bullet) and either reads as a direct question or is short
    enough to be a stated prompt rather than a longer descriptive
    sentence."""
    stripped = line.strip()
    if BULLET_RE.match(line) or OPTION_ITEM_RE.match(line):
        return False
    if not APPROVAL_PHRASE_RE.search(stripped):
        return False
    if stripped.endswith('?'):
        return True
    return len(stripped.split()) <= 8


def score_sequencing(lines):
    plan_idx = find_first_heading_index(lines, lambda t: re.search(r'\bplan\b', t, re.IGNORECASE))
    approval_idx = find_first_heading_index(
        lines, lambda t: re.search(r'\bapproval\b|\bapprove\b|\bconfirm', t, re.IGNORECASE))
    if approval_idx is None:
        for i, line in enumerate(lines):
            if _looks_like_an_approval_prompt(line):
                approval_idx = i
                break
    if plan_idx is None or approval_idx is None:
        return None, "no Plan heading and/or approval prompt found, cannot determine order"
    if approval_idx < plan_idx:
        return 0.0, "hard 0: approval request at line %d precedes the Plan heading at line %d" % (
            approval_idx + 1, plan_idx + 1)
    return 10.0, "Plan heading at line %d precedes the approval request at line %d" % (
        plan_idx + 1, approval_idx + 1)


def _persona_section(lines, full_text, predicate):
    sec = find_section(lines, predicate)
    if sec is None:
        return full_text, "no dedicated persona section found, scanning the full record as a proxy"
    start, end = sec
    return "\n".join(lines[start:end]), "scanning the dedicated persona section"


def score_level_adaptation(lines, full_text, persona):
    if persona == 'ba':
        section_text, note = _persona_section(
            lines, full_text, lambda t: re.search(r'\bba\b|\bbusiness analyst\b', t, re.IGNORECASE))
        leaks = []
        if contains_path(section_text):
            leaks.append('file path')
        if contains_command(section_text):
            leaks.append('command syntax')
        if contains_json(section_text):
            leaks.append('JSON')
        score = 10.0 if not leaks else 4.0
        evidence = "proxy check only, not the full criterion; %s; leaks found: %s" % (
            note, leaks if leaks else "none")
        return score, evidence
    section_text, note = _persona_section(
        lines, full_text, lambda t: re.search(r'\bdev\b|\bdeveloper\b', t, re.IGNORECASE))
    found = contains_path(section_text)
    score = 10.0 if found else 0.0
    evidence = "proxy check only, not the full criterion; %s; cited path found: %s" % (note, found)
    return score, evidence


# ---------------------------------------------------------------------- #
# Flag-driven criteria: NO-DATA unless the caller supplies a proxy figure
# ---------------------------------------------------------------------- #

def score_orientation(persona, process_questions):
    if process_questions is None:
        return None, "no --process-questions figure supplied, needs a proxy session transcript"
    pq = process_questions
    if persona == 'ba':
        if pq == 0:
            score = 10.0
        elif pq == 1:
            score = 7.0
        else:
            score = max(0.0, 7.0 - 3.0 * (pq - 1))
    else:
        score = 10.0 if pq == 0 else max(0.0, 7.0 - 3.0 * pq)
    return score, "%d process question(s) reported for persona %s" % (pq, persona)


def score_interaction_economy(persona, turns):
    if turns is None:
        return None, "no --turns figure supplied, needs a proxy session transcript"
    if persona == 'dev':
        if turns <= 3:
            score = 10.0
        elif turns <= 6:
            score = 7.0
        else:
            score = max(0.0, 7.0 - (turns - 6))
    else:
        if turns <= 8:
            score = 10.0
        elif turns <= 15:
            score = 7.0
        else:
            score = max(0.0, 7.0 - (turns - 15))
    return score, "%d turn(s) to an accepted plan reported for persona %s" % (turns, persona)


def score_convergence(override_rate, repeat_questions):
    if override_rate is None and repeat_questions is None:
        return None, "no --override-rate or --repeat-questions figure supplied"
    parts = []
    if override_rate is not None:
        if override_rate < 10:
            parts.append(10.0)
        elif override_rate < 25:
            parts.append(7.0)
        else:
            parts.append(max(0.0, 7.0 - (override_rate - 25) * 0.2))
    if repeat_questions is not None:
        parts.append(10.0 if repeat_questions == 0 else max(0.0, 7.0 - repeat_questions))
    score = round(sum(parts) / len(parts), 2)
    evidence = "override_rate=%s repeat_questions=%s" % (override_rate, repeat_questions)
    return score, evidence


def score_receipt_integrity(receipt_verified):
    if not receipt_verified:
        return None, "no --receipt-verified flag supplied, needs the session's own receipt evidence"
    return 10.0, "caller attested the close's check ran after the last edit, command preserved"


# ---------------------------------------------------------------------- #
# Orchestration
# ---------------------------------------------------------------------- #

def score_record(text, persona, turns=None, process_questions=None,
                  override_rate=None, repeat_questions=None, receipt_verified=False,
                  root=None):
    lines = text.splitlines()
    weight = dict(CRITERIA_WEIGHTS)
    results = []

    score, evidence = score_orientation(persona, process_questions)
    results.append(CriterionResult('orientation', weight['orientation'], score, evidence))

    score, evidence = score_interaction_economy(persona, turns)
    results.append(CriterionResult('interaction_economy', weight['interaction_economy'], score, evidence))

    score, evidence = score_grounded_assumptions(lines, root)
    results.append(CriterionResult('grounded_assumptions', weight['grounded_assumptions'], score, evidence))

    score, evidence = score_level_adaptation(lines, text, persona)
    results.append(CriterionResult('level_adaptation', weight['level_adaptation'], score, evidence))

    score, evidence = score_options(lines)
    results.append(CriterionResult('options_with_recommendation', weight['options_with_recommendation'], score, evidence))

    score, evidence = score_weighted_options(lines)
    results.append(CriterionResult('weighted_options', weight['weighted_options'], score, evidence))

    score, evidence = score_diagrams(text)
    results.append(CriterionResult('diagrams_by_default', weight['diagrams_by_default'], score, evidence))

    score, evidence = score_convergence(override_rate, repeat_questions)
    results.append(CriterionResult('convergence', weight['convergence'], score, evidence))

    score, evidence = score_sequencing(lines)
    results.append(CriterionResult('sequencing', weight['sequencing'], score, evidence))

    score, evidence = score_receipt_integrity(receipt_verified)
    results.append(CriterionResult('receipt_integrity', weight['receipt_integrity'], score, evidence))

    return results


# ---------------------------------------------------------------------- #
# VIEWS REPLACE PERSONAS (row R8, 2026-08-29).
#
# WHY. The founder corrected this file's own CLI directly: `--persona ba|dev`
# is an enum, chosen once, that LABELS THE PERSON. Intake normally comes from
# a business analyst but can come from anyone on the team ("that is why it
# needs to be dynamic and not rigid and adaptive"). A fixed bucket cannot do
# that, and bolting on a third bucket for the next incoming role just repeats
# the mistake with more buckets.
#
# THE FIX names the view after the WORK SURFACE, never the person: Outcome
# (process impact, decisions, risks, evidence, plain language), Data (inputs,
# transformations, schemas, samples, lineage, validation), Code (files,
# commands, diffs, tests, logs, repository state), Balanced (a compact
# combination, and the default). The same person moves between views inside
# one session; a view is a persistent, explicit, user controlled setting,
# never covertly inferred from who is typing or from what the record itself
# contains.
#
# THE INVARIANT THIS MUST HOLD, proven in scripts/test_intake_score.py: one
# canonical record, and ONLY the rendering varies. Identical risk
# classification (floor_rule), identical checks (the same criteria at the
# same weights), identical conclusions (the same score and evidence per
# criterion) across all four views. The mechanism that makes this true is
# structural, not a promise: `view` never reaches score_record()'s scoring
# math. score_for_view() below reads the scoring bucket ONLY from `persona`
# (falling back to a fixed constant, never to anything derived from `view`),
# so a view can change how a result is DISPLAYED and never what it CONCLUDES.
#
# MIGRATION. `--persona ba|dev` keeps working (six shipped records and other
# tooling pass it) and now ALSO sets a starting view: ba maps to Outcome, dev
# maps to Code. It is deprecated, not broken: prefer `--view` going forward.
VIEW_CHOICES = ('outcome', 'data', 'code', 'balanced')
DEFAULT_VIEW = 'balanced'
# The persistent, explicit, user controlled setting: switchable instantly by
# re-exporting the variable, visible with a plain `echo`, never written to by
# this script itself. A repository may SUGGEST a starting view; it must
# never set one automatically, per the founder's own detection constraint.
VIEW_ENV_VAR = 'BROTHER_INTAKE_VIEW'

VIEW_DESCRIPTIONS = {
    'outcome': 'process impact, decisions, risks, evidence, plain language',
    'data': 'inputs, transformations, schemas, samples, lineage, validation',
    'code': 'files, commands, diffs, tests, logs, repository state',
    'balanced': 'a compact combination, and the default',
}

# Migration only: a starting VIEW for a caller still passing the deprecated
# --persona flag. This never feeds the scoring math (see score_for_view).
PERSONA_TO_VIEW = {'ba': 'outcome', 'dev': 'code'}

# The scoring bucket used when a caller supplies `view` but no `persona` at
# all. Fixed, and deliberately never derived from `view`: that independence
# is what keeps the cross-view invariant true. 'dev' is the stricter of the
# two existing curves (see score_orientation / score_interaction_economy),
# so an unspecified caller never receives unearned leniency by default.
DEFAULT_SCORE_PERSONA = 'dev'


def resolve_view(cli_view, cli_persona):
    """The view is always an explicit setting, never inferred from the
    record's content or from who is running the scorer. Order: the --view
    flag, then the persistent BROTHER_INTAKE_VIEW env var, then a starting
    view mapped from the deprecated --persona flag, then the balanced
    default."""
    if cli_view in VIEW_CHOICES:
        return cli_view
    env_view = os.environ.get(VIEW_ENV_VAR)
    if env_view in VIEW_CHOICES:
        return env_view
    if cli_persona in PERSONA_TO_VIEW:
        return PERSONA_TO_VIEW[cli_persona]
    return DEFAULT_VIEW


def score_for_view(text, view, persona=None, turns=None, process_questions=None,
                    override_rate=None, repeat_questions=None, receipt_verified=False,
                    root=None):
    """THE INVARIANT, structurally enforced: `view` is accepted for callers
    who think in view terms, but it is NEVER read below this line. The
    scoring bucket comes solely from `persona` (falling back to the fixed
    DEFAULT_SCORE_PERSONA), so calling this four times with the same text,
    persona and metrics and only `view` varying returns identical results
    every time. `view` exists on this signature only so a call site never has
    to smuggle the view value in past this boundary by another name."""
    del view  # deliberately unused: see the docstring above
    effective_persona = persona if persona in ('ba', 'dev') else DEFAULT_SCORE_PERSONA
    return score_record(text, effective_persona, turns=turns,
                         process_questions=process_questions,
                         override_rate=override_rate,
                         repeat_questions=repeat_questions,
                         receipt_verified=receipt_verified,
                         root=root)


def view_snapshot(results):
    """The facts that must never vary with view: which checks ran and at
    what weight, each check's numeric conclusion and its evidence, and the
    overall risk classification. Two snapshots are equal only when every one
    of those is identical; a renderer that reorders or reframes without
    touching any of this still produces an equal snapshot, which is the
    point."""
    total_weight_scored, excluded_weight, weighted_score, floor_rule = summarize(results)
    checks = tuple((r.name, r.weight, r.score, r.evidence) for r in results)
    return (checks, total_weight_scored, excluded_weight, weighted_score, floor_rule)


def views_agree(text, persona=None, **metrics):
    """Score the SAME record through all four views and check the
    invariant. Returns (ok, {view: snapshot}); ok is True only when every
    view's snapshot is identical to the others'. This is the check that
    would catch a violation: if any future edit lets `view` leak into the
    scoring math (directly, or by way of a mapping like PERSONA_TO_VIEW
    applied backwards), the snapshots stop matching and this returns False."""
    snapshots = {}
    for view in VIEW_CHOICES:
        results = score_for_view(text, view, persona=persona, **metrics)
        snapshots[view] = view_snapshot(results)
    baseline = snapshots[VIEW_CHOICES[0]]
    ok = all(snap == baseline for snap in snapshots.values())
    return ok, snapshots


def print_view_diff(text, persona=None, **metrics):
    """Score one record through all four views, print what each view
    concluded, and state plainly whether the invariant held. Returns the
    same `ok` that views_agree returns."""
    ok, snapshots = views_agree(text, persona=persona, **metrics)
    baseline_view = VIEW_CHOICES[0]
    baseline = snapshots[baseline_view]
    for view in VIEW_CHOICES:
        checks, total_weight_scored, excluded_weight, weighted_score, floor_rule = snapshots[view]
        same = "identical" if snapshots[view] == baseline else "DIFFERENT"
        print("view=%-8s floor_rule=%-8s weighted_score=%5.2f checks=%d vs %s baseline: %s" % (
            view, floor_rule, weighted_score, len(checks), baseline_view, same))
    if ok:
        print("view-invariant: HOLDS, identical risk classification, checks and "
              "conclusions across outcome/data/code/balanced")
    else:
        print("view-invariant: BROKEN, a view changed the underlying conclusion "
              "rather than only its rendering", file=sys.stderr)
    return ok


def summarize(results):
    scored = [r for r in results if r.score is not None]
    total_weight_scored = sum(r.weight for r in scored)
    excluded_weight = 100 - total_weight_scored
    if total_weight_scored > 0:
        weighted_score = sum(r.weight * r.score for r in scored) / total_weight_scored
    else:
        weighted_score = 0.0
    if total_weight_scored == 0:
        floor_rule = "NO-DATA"
    elif min(r.score for r in scored) < 8.0:
        floor_rule = "BROKEN"
    else:
        floor_rule = "HOLDS"
    return total_weight_scored, excluded_weight, round(weighted_score, 2), floor_rule


def final_line(persona, weighted_score, total_weight_scored, floor_rule):
    # UNCHANGED format: printed verbatim whenever the caller gave an explicit
    # --persona, so every existing consumer that greps this exact shape (this
    # file's own tests included) keeps reading the same last line it always
    # has. This is the "still working" half of the migration.
    return "intake-score: persona=%s scored=%.1f/10 over %d of 100 weight, floor_rule=%s" % (
        persona, weighted_score, total_weight_scored, floor_rule)


def final_line_for_view(view, weighted_score, total_weight_scored, floor_rule):
    # Printed instead of final_line() when the caller drove this run by
    # --view alone, with no --persona at all: the summary line names the
    # thing the caller actually chose, rather than the internal scoring
    # default it fell back on.
    return "intake-score: view=%s scored=%.1f/10 over %d of 100 weight, floor_rule=%s" % (
        view, weighted_score, total_weight_scored, floor_rule)


def print_report(results, persona, view=None, persona_explicit=True):
    # VIEW CHANGES RENDERING ONLY. `results` is already fully scored before
    # this function ever runs; nothing here recomputes a score or drops a
    # criterion for any view. The only things that vary below are: a header
    # line naming the view, and (Outcome only) the print ORDER, surfacing the
    # lowest scoring / NO-DATA checks first so a risk is never buried under a
    # wall of passing detail. Every view still prints every criterion.
    if view:
        print("intake-view: view=%s (%s)" % (view, VIEW_DESCRIPTIONS.get(view, view)))
    total_weight_scored, excluded_weight, weighted_score, floor_rule = summarize(results)
    if view == 'outcome':
        plain_floor = {
            'HOLDS': 'holds: every scored check cleared the floor',
            'BROKEN': 'broken: at least one scored check fell below the floor',
            'NO-DATA': 'not enough evidence was scored to call a verdict',
        }.get(floor_rule, floor_rule)
        print("risk classification: %s (weighted score %.2f/10)" % (plain_floor, weighted_score))
        ordered = sorted(
            results,
            key=lambda r: (r.score is not None, r.score if r.score is not None else -1.0))
    else:
        ordered = results
    for r in ordered:
        score_str = "NO-DATA" if r.score is None else "%.2f" % r.score
        print("- %s (weight %d): score=%s ; %s" % (r.name, r.weight, score_str, r.evidence))
    excluded = [r.name for r in results if r.score is None]
    if excluded:
        print("excluded weight (NO-DATA criteria): %d of 100, criteria: %s" % (
            excluded_weight, ", ".join(excluded)))
    else:
        print("excluded weight (NO-DATA criteria): 0 of 100")
    if persona_explicit:
        print(final_line(persona, weighted_score, total_weight_scored, floor_rule))
    else:
        print(final_line_for_view(view or DEFAULT_VIEW, weighted_score, total_weight_scored, floor_rule))
    return total_weight_scored


# ---------------------------------------------------------------------- #
# Self test
# ---------------------------------------------------------------------- #

def run_selftest():
    # CHECK 1 needs a resolvable repository root to check citation existence;
    # anchor it to this script's own location so the selftest works from any cwd.
    _root = find_repo_root(os.path.dirname(os.path.abspath(__file__)))
    clean_record = (
        "# Intake Record\n"
        "\n"
        "## Assumptions\n"
        "- The invoice poster lives at `scripts/invoice_poster.py`, UNGROUNDED otherwise.\n"
        "- Retries are idempotent per docs/plan/INTAKE-9.5-DESIGN.md.\n"
        "\n"
        "## Options\n"
        "1. Add an idempotency key column. Cost: one migration. Recommended: this option.\n"
        "2. Wrap the poster in a lock. Cost: throughput.\n"
        "\n"
        "## Plan\n"
        "Do the migration first, then the poster change.\n"
        "\n"
        "```mermaid\n"
        "graph TD; A-->B;\n"
        "```\n"
        "\n"
        "## Dev View\n"
        "Edit `scripts/invoice_poster.py` and run the check.\n"
        "\n"
        "## BA View\n"
        "Customers will stop seeing duplicate invoices once this ships.\n"
        "\n"
        "## Approval\n"
        "Do you approve this plan?\n"
    )
    ok = True

    dev_results = score_record(clean_record, 'dev', root=_root)
    by_name = {r.name: r for r in dev_results}
    if by_name['grounded_assumptions'].score != 10.0:
        print("selftest: expected grounded_assumptions 10.0, got %r" % (by_name['grounded_assumptions'].score,))
        ok = False
    if by_name['diagrams_by_default'].score != 10.0:
        print("selftest: expected diagrams_by_default 10.0, got %r" % (by_name['diagrams_by_default'].score,))
        ok = False
    if by_name['sequencing'].score != 10.0:
        print("selftest: expected sequencing 10.0, got %r" % (by_name['sequencing'].score,))
        ok = False
    if by_name['options_with_recommendation'].score != 10.0:
        print("selftest: expected options_with_recommendation 10.0, got %r" % (
            by_name['options_with_recommendation'].score,))
        ok = False
    if by_name['weighted_options'].score != 0.0:
        print("selftest: expected weighted_options 0.0 on a record with no weight table, got %r" % (
            by_name['weighted_options'].score,))
        ok = False
    if by_name['level_adaptation'].score != 10.0:
        print("selftest: expected dev level_adaptation 10.0, got %r" % (by_name['level_adaptation'].score,))
        ok = False
    if by_name['orientation'].score is not None:
        print("selftest: expected orientation NO-DATA with no --process-questions, got %r" % (
            by_name['orientation'].score,))
        ok = False

    ba_results = score_record(clean_record, 'ba', root=_root)
    ba_by_name = {r.name: r for r in ba_results}
    if ba_by_name['level_adaptation'].score != 10.0:
        print("selftest: expected ba level_adaptation 10.0, got %r" % (ba_by_name['level_adaptation'].score,))
        ok = False

    broken_record = clean_record.replace(
        "## Plan\nDo the migration first, then the poster change.\n",
        "")
    broken_record = broken_record.replace(
        "## Approval\nDo you approve this plan?\n",
        "## Approval\nDo you approve this plan?\n\n## Plan\nDo the migration first, then the poster change.\n")
    broken_results = score_record(broken_record, 'dev', root=_root)
    broken_by_name = {r.name: r for r in broken_results}
    if broken_by_name['sequencing'].score != 0.0:
        print("selftest: expected sequencing hard 0 when approval precedes plan, got %r" % (
            broken_by_name['sequencing'].score,))
        ok = False

    turns_results = score_record(clean_record, 'dev', turns=3, process_questions=0, root=_root)
    turns_by_name = {r.name: r for r in turns_results}
    if turns_by_name['interaction_economy'].score != 10.0:
        print("selftest: expected interaction_economy 10.0 at 3 turns, got %r" % (
            turns_by_name['interaction_economy'].score,))
        ok = False

    # VIEWS REPLACE PERSONAS (row R8): the same record, scored through all
    # four views with the same persona and metrics fixed, must reach
    # identical risk classification, checks and conclusions. Only rendering
    # may vary. This is the check with teeth: score_for_view structurally
    # withholds `view` from the scoring math, so this can only fail if a
    # later edit lets it leak back in.
    view_ok, _view_snapshots = views_agree(clean_record, persona='dev', turns=3, process_questions=0, root=_root)
    if not view_ok:
        print("selftest: view invariant broken, a view changed the record's conclusion")
        ok = False

    return ok


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #


# ---------------------------------------------------------------------------
# THE DIAGRAM GATE.
#
# WHY THIS EXISTS, and it is not the reason a reader will assume.
#
# The rubric already scores diagrams (criterion `diagrams_by_default`, weight
# 10, the joint-highest in the design) and `score_diagrams` genuinely returns
# 0.0 when a record carries no fence, so the SCORER can go red. What could not
# go red was the REPOSITORY: `grep -n intake scripts/check_all.sh` exited 1,
# so nothing ran this file at all. A scorer nobody runs is a preference, and
# the founder's own machine law is that a rule is not a control unless a file
# enforces it.
#
# WHAT THIS GATE HONESTLY DOES NOT DO, stated here so a later reader does not
# over-read a green. Every record shipped under docs/plan/examples/ already
# carries exactly one fence, so this gate PASSES on the population it was
# written for, on the day it was written. It therefore does not, by itself,
# prove the live intake emits a diagram to a real person in a real turn. That
# behaviour happens in a chat turn, which no file in this repository observes.
# The gate stops a record from ENTERING the repository without one; enforcing
# the live turn needs a session hook, which is a separate, founder-gated
# change. Do not cite a green here as evidence that the founder-facing intake
# renders diagrams. It is not that evidence and was never claimed to be.
#
# Exit 0 every record carries at least one fence. Exit 1 any record carries
# none, each offender named. Exit 2 no record was read at all, which is
# NO-DATA and never a pass.

# CASE-INSENSITIVE, and stated as a limit rather than left to be discovered: the
# first draft globbed '*RECORD*.md' case-sensitively, so `...-record-....md` and
# any file not carrying the word at all evaluated False and was silently ungated.
# Renaming a file was a one-word bypass of this gate.
DEFAULT_GATE_DIR = 'docs/plan/examples'
DEFAULT_GATE_SUBSTRING = 'record'


def default_gate_paths(root):
    """Every markdown file under the examples directory whose name contains
    'record' in ANY case. Named as a function so the population rule is one
    readable thing rather than a glob string with hidden case semantics."""
    import glob as _glob
    hits = []
    for path in sorted(_glob.glob(os.path.join(root, DEFAULT_GATE_DIR, '*.md'))):
        if DEFAULT_GATE_SUBSTRING in os.path.basename(path).lower():
            hits.append(path)
    return hits


def gate_records(paths, check_weighted_options=False):
    """Return (verdicts, missing) where verdicts is a list of
    (path, fence_count, weighted_score) for every record actually read.
    weighted_score is None unless check_weighted_options is set, in which
    case it holds score_weighted_options()'s 0..10 score (0.0 means the
    record's Options section carries no machine-readable weights at all).
    missing is the subset failing on ANY checked axis: fence_count zero or
    unreadable, plus weighted_score zero when that check was requested.
    Unreadable files carry fence_count None so a read error can never be
    mistaken for a pass."""
    verdicts = []
    for path in paths:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            verdicts.append((path, None, None))
            continue
        weighted_score = None
        if check_weighted_options:
            weighted_score, _evidence = score_weighted_options(text.splitlines())
        verdicts.append((path, count_mermaid_blocks(text), weighted_score))
    missing = [(p, c, w) for p, c, w in verdicts
               if c is None or c == 0 or (check_weighted_options and w == 0.0)]
    return verdicts, missing


def run_gate(paths, check_weighted_options=False):
    if not paths:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths = default_gate_paths(root)
    if not paths:
        print("intake-record-diagrams: NO-DATA, no intake record found under %s"
              % DEFAULT_GATE_DIR, file=sys.stderr)
        return 2
    verdicts, _ = gate_records(paths, check_weighted_options=check_weighted_options)
    # THE TWO FAILURE MODES ARE DIFFERENT VERDICTS, and conflating them is the
    # bug this estate's law names by hand: a record read and found to carry no
    # diagram is a FAIL, a record that could not be read at all is NO-DATA. The
    # first draft returned 1 for both, which reports "measured and bad" for a
    # thing that was never measured. A FAIL anywhere still outranks a NO-DATA.
    absent = [p for p, c, _w in verdicts if c == 0]
    unreadable = [p for p, c, _w in verdicts if c is None]
    # WEIGHTED-OPTIONS IS OPT-IN, DELIBERATELY. The six records shipped under
    # docs/plan/examples/ predate this criterion and were written before it
    # existed, so folding this into the unconditional gate would turn
    # check_all.sh red today for content nobody has fixed yet, not for a new
    # defect this change introduced. --require-weighted-options exists so the
    # clause CAN go red once someone opts in (a rule is not a control unless a
    # file enforces it), without breaking the green battery run this same
    # session must leave behind.
    unweighted = [p for p, _c, w in verdicts if check_weighted_options and w == 0.0]
    for path, count, weighted in verdicts:
        if count is None:
            mark, shown = "NO-DATA", "could not be read"
        elif count == 0:
            mark, shown = "MISSING", "0 fence(s)"
        else:
            mark, shown = "ok     ", "%d fence(s)" % count
        if check_weighted_options and count is not None and count != 0:
            if weighted == 0.0:
                mark = "UNWEIGHTED"
                shown += ", options carry no weights"
            else:
                shown += ", weighted-options score=%.1f" % weighted
        print("%s %-52s %s" % (mark, os.path.basename(path), shown))
    # THE SUMMARY LINE CARRIES THE SCOPE. An audit pointed out that the honest
    # caveat lived in a source comment while a bare "PASS" reached the battery
    # summary, where a reader sees it beside seventeen checks that DO test
    # behaviour. The one line most people read now says what it does not prove.
    print("intake records carry diagrams: %d checked, %d bare, %d unreadable "
          "(says nothing about live turns)"
          % (len(verdicts), len(absent), len(unreadable)))
    if check_weighted_options:
        print("intake records carry weighted options (--require-weighted-options): "
              "%d checked, %d carry no weights" % (len(verdicts), len(unweighted)))
    if absent or unweighted:
        reasons = []
        if absent:
            reasons.append(
                "every intake record must carry at least one fenced mermaid block; "
                "these do not: %s" % ", ".join(os.path.basename(p) for p in absent))
        if unweighted:
            reasons.append(
                "every intake record's Options must carry comparable, machine-readable "
                "weights; these do not: %s" % ", ".join(os.path.basename(p) for p in unweighted))
        print("FAIL: " + " | ".join(reasons), file=sys.stderr)
        return 1
    if unreadable:
        print("NO-DATA: could not read %s, so this gate measured nothing about "
              "them and is not reporting a pass"
              % ", ".join(os.path.basename(p) for p in unreadable), file=sys.stderr)
        return 2
    return 0

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', nargs='*',
                         help='intake record markdown file; many are allowed with --gate')
    parser.add_argument('--persona', choices=['ba', 'dev'],
                         help='DEPRECATED, kept working for six shipped records and other tooling that '
                              'still pass it: ba or dev. Sets a starting --view (ba->outcome, dev->code) '
                              'and the scoring bucket; prefer --view, which never labels a person and can '
                              'be switched at any time. See VIEWS REPLACE PERSONAS in this file.')
    parser.add_argument('--view', choices=VIEW_CHOICES, default=None,
                         help='outcome, data, code or balanced: controls rendering ONLY, never the score. '
                              'Falls back to the %s env var, then to a --persona mapping, then to balanced. '
                              'Never inferred from the record; always an explicit, switchable setting.'
                              % VIEW_ENV_VAR)
    parser.add_argument('--check-views', action='store_true', dest='check_views',
                         help='score one record through all four views and print whether the cross-view '
                              'invariant (identical risk classification, checks, conclusions) holds; '
                              'exits 1 if any view differs')
    parser.add_argument('--selftest', action='store_true', help='run the built in self check and exit')
    parser.add_argument('--gate', action='store_true',
                         help='diagram gate: fail when any intake record carries no fenced mermaid block')
    parser.add_argument('--require-weighted-options', action='store_true', dest='require_weighted_options',
                         help='with --gate: also fail when a record\'s Options section carries no '
                              'machine-readable weights. Opt-in: the shipped examples predate this '
                              'criterion and fail it today, so check_all.sh does not pass this flag')
    parser.add_argument('--turns', type=int, default=None,
                         help='turns from first message to an accepted plan, from a proxy session')
    parser.add_argument('--process-questions', type=int, default=None, dest='process_questions',
                         help='process questions the user had to ask, from a proxy session')
    parser.add_argument('--override-rate', type=float, default=None, dest='override_rate',
                         help='percent of stated assumptions the user overrode, from prior sessions')
    parser.add_argument('--repeat-questions', type=int, default=None, dest='repeat_questions',
                         help='questions repeated across sessions for the same person and project')
    parser.add_argument('--receipt-verified', action='store_true', dest='receipt_verified',
                         help='the close produced a receipt whose check ran after the last edit')
    parser.add_argument('--root', default=None,
                         help='repository root a backticked citation path is resolved against '
                              '(CHECK 1). Default: the nearest directory containing .git above the '
                              'record file. When none is found, grounded_assumptions prints NO-DATA '
                              'instead of a score.')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gate:
        return run_gate(args.record, check_weighted_options=args.require_weighted_options)

    if args.selftest:
        ok = run_selftest()
        if ok:
            print("intake_score.py selftest: PASS")
            return 0
        print("intake_score.py selftest: FAIL", file=sys.stderr)
        return 1

    if args.check_views:
        if not args.record:
            parser.error("--check-views needs a record path")
        record_path = args.record[0]
        try:
            with open(record_path, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except OSError as e:
            print("error: cannot read record file %s: %s" % (record_path, e), file=sys.stderr)
            return 2
        root = args.root or find_repo_root(os.path.dirname(os.path.abspath(record_path)))
        ok = print_view_diff(
            text, persona=args.persona,
            turns=args.turns,
            process_questions=args.process_questions,
            override_rate=args.override_rate,
            repeat_questions=args.repeat_questions,
            receipt_verified=args.receipt_verified,
            root=root,
        )
        return 0 if ok else 1

    if not args.record or not (args.persona or args.view):
        parser.error("record and --persona (or --view) are required unless --selftest, --gate or "
                      "--check-views is given")
    if len(args.record) > 1:
        parser.error("scoring takes one record; many records are only allowed with --gate")
    record_path = args.record[0]

    try:
        with open(record_path, 'r', encoding='utf-8') as fh:
            text = fh.read()
    except OSError as e:
        print("error: cannot read record file %s: %s" % (record_path, e), file=sys.stderr)
        return 2

    # THE INVARIANT AT THE CLI BOUNDARY: effective_persona comes from
    # --persona alone (falling back to the fixed DEFAULT_SCORE_PERSONA), and
    # `view` is resolved separately for rendering only. Whatever --view the
    # caller picks, the scoring call below never sees it, so the score this
    # run reports is exactly what any other view would have reported too.
    effective_persona = args.persona if args.persona in ('ba', 'dev') else DEFAULT_SCORE_PERSONA
    view = resolve_view(args.view, args.persona)
    root = args.root or find_repo_root(os.path.dirname(os.path.abspath(record_path)))

    results = score_record(
        text, effective_persona,
        turns=args.turns,
        process_questions=args.process_questions,
        override_rate=args.override_rate,
        repeat_questions=args.repeat_questions,
        receipt_verified=args.receipt_verified,
        root=root,
    )
    total_weight_scored = print_report(
        results, effective_persona, view=view, persona_explicit=bool(args.persona))
    return 0 if total_weight_scored > 0 else 2


if __name__ == '__main__':
    sys.exit(main())
