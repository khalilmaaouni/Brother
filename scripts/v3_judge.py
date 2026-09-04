#!/usr/bin/env python3
"""v3_judge: decide, mechanically, whether one work unit's diff applied a recorded lesson.

WHY A SCRIPT AND NOT A READER. bm_recurrence.py's own applicability ceiling says the number is
only trustworthy when the applied/declined call comes from somewhere other than the unit's
author: "a second reviewer, or a separate LLM judge shown only the work unit's actual diff and
the surfaced lesson text, blind to where the recorder put the id." This file is the third form
of that same independence and the only one available here: a deterministic rule per LESSON,
written from the lesson's own text, run over the diff and nothing else. It never reads the
recorder's opinion, the branch name, the commit message, the roadmap or any prose about the
unit, because it is given a diff and a lesson id and has nothing else to read.

WHAT MAKES IT A JUDGE RATHER THAN A RUBBER STAMP. Every rule has two independent parts:

  ARISES    does the lesson's subject occur in this diff at all
  FOLLOWED  where it occurs, does the diff show the discipline the lesson asks for

and the three verdicts fall out: NOT-APPLICABLE (the subject never arises, so the unit is NOT
in the denominator for this lesson), APPLIED (arises and followed), DECLINED (arises and not
followed). A rule that cannot return DECLINED would be a rubber stamp, so test_v3_judge.py
drives EVERY rule both ways over synthetic fixtures, and a rule with no failing fixture is
itself a test failure.

THE HONEST LIMIT, stated rather than hidden. These are lexical rules over added lines. They
can miss a discipline expressed some other way (a false DECLINED) and they can credit a line
that merely looks right (a false APPLIED). They are not a semantic reader. What they are is
reproducible and blind: anyone can rerun this file over the same diff and get the same verdict,
which is exactly what the self-filed numerator lacked. A verdict here is evidence about the
diff's visible text, never a claim that the author was thinking of the lesson.

Python 3, standard library only, no network, reads only the diff path it is given.
"""
import argparse
import os
import re
import sys

#: Rule per lesson id. The id is the lesson's trigger, slugged; the notes those triggers carry
#: live in ~/.claude/repeat-guard/lessons.jsonl and are quoted verbatim beside each rule.
LESSON_IDS = (
    'repeat-guard:def-space',
    'repeat-guard:open-paren',
    'repeat-guard:unittest',
    'repeat-guard:pipe-tail',
    'repeat-guard:grep-c',
    'repeat-guard:checksums',
    'repeat-guard:shlex-split',
    'repeat-guard:done-check',
    'repeat-guard:certify',
)

NOT_APPLICABLE = 'NOT-APPLICABLE'
APPLIED = 'APPLIED'
DECLINED = 'DECLINED'


def added_by_file(diff_text):
    """[(path, added line)] in diff order, so a rule can ask which FILE a line came from.

    Written after a rule read a JSON manifest's added lines as though they were Python: the
    diff you read is not the diff the code compares unless the code tracks the file header."""
    out = []
    path = '<unknown>'
    in_doc = False
    for line in diff_text.split('\n'):
        m = re.match(r'^diff --git a/(\S+) b/\S+$', line)
        if m:
            path = m.group(1)
            in_doc = False
            continue
        if line.startswith('@@'):
            in_doc = False       # a new hunk: docstring state from the last one cannot carry
            continue
        if line.startswith('+++') or line.startswith('---'):
            continue
        if not line[:1] in (' ', '+', '-'):
            continue
        body = line[1:]
        # A line inside a triple-quoted block is PROSE, and three rules here were fooled by
        # their own vocabulary appearing in a docstring ("open(path, \"wb\") plus fh.write..."
        # in a comment about a writer). Context lines are read too, because that is the only
        # way to know a hunk opened inside a docstring.
        opens_or_closes = body.count('"""') + body.count("'''")
        prose = in_doc or opens_or_closes > 0
        if opens_or_closes % 2 == 1:
            in_doc = not in_doc
        if line.startswith('+') and not prose:
            out.append((path, body))
    return out


def added_lines(diff_text, suffixes=None):
    """The added side of a unified diff, without the +++ headers. `suffixes` keeps only lines
    from files whose path ends with one of them, which is how a Python rule stops reading a
    checksum manifest or a JSON fixture as code."""
    pairs = added_by_file(diff_text)
    if suffixes:
        pairs = [(p, line) for p, line in pairs if p.endswith(tuple(suffixes))]
    return [line for _p, line in pairs]


def touched_files(diff_text):
    """Repo-relative paths named by the diff's own 'diff --git a/X b/Y' headers."""
    return re.findall(r'^diff --git a/(\S+) b/\S+$', diff_text, re.M)


def strip_literals(line):
    """A line with its string literals and trailing comment removed, so a rule can ask whether
    a token is CODE rather than prose. Three rules here were fooled by their own vocabulary
    appearing inside a comment before this existed."""
    out = re.sub(r'"[^"]*"|\'[^\']*\'', '""', line)
    return out.split('#', 1)[0]


def _first(lines, pattern):
    rx = re.compile(pattern)
    for line in lines:
        if rx.search(line):
            return line.strip()[:160]
    return None


def _all_matching(lines, pattern):
    rx = re.compile(pattern)
    return [line for line in lines if rx.search(line)]


# Each rule returns (verdict, evidence). Evidence is the line that decided it, so a reader can
# check the verdict against the diff without trusting this file.

def _rule_def_space(diff, added, files):
    """`def ` : a default argument binds at definition time, so a module constant as a default
    is captured once and later reassignment has no effect. Use path=None and resolve inside."""
    py = added_lines(diff, ('.py',))
    defs = _all_matching(py, r'^\s*def \w+\([^)]*=')
    if not defs:
        return NOT_APPLICABLE, 'no added def carries a default argument'
    bad = [d for d in defs if re.search(r'=\s*[A-Z][A-Z0-9_]{2,}\b', d)]
    if bad:
        return DECLINED, bad[0].strip()[:160]
    return APPLIED, defs[0].strip()[:160]


def _rule_open_paren(diff, added, files):
    """`open(` : a file handle never bound to a name leaks. Use with-open.

    KNOWN LIMIT, measured on u1 of the 2026-09-04 run and left standing rather than papered
    over: a hunk that BEGINS inside a module docstring cannot be told from one that begins in
    code, because the opening quote is outside the hunk. added_by_file starts each hunk as
    not-in-docstring, so prose in such a hunk reads as code and this rule can return a false
    DECLINED. The verdict is still reproducible and its evidence line shows the prose, so a
    reader can see the miss; no receipt in scripts/v3_night_receipts.py rests on it."""
    py = added_lines(diff, ('.py',))
    opens = _all_matching(py, r'\bopen\(')
    if not opens:
        return NOT_APPLICABLE, 'no added line opens a file'
    bare = [o for o in opens if not re.search(r'\bwith\b.*\bopen\(', o)]
    if bare:
        return DECLINED, bare[0].strip()[:160]
    return APPLIED, opens[0].strip()[:160]


def _rule_unittest(diff, added, files):
    """`unittest` : assert the EXIT CODE, not the printed verdict. A gate printed FAIL, exited
    0, and eleven tests passed over it.

    ARISES only where the lesson's subject actually is: an added assertion ON A VERDICT (a
    PASS/FAIL/NO-DATA style value). "the diff adds a test" would put almost every diff in the
    denominator, which is the padding bm_recurrence.py warns about. FOLLOWED requires a
    CODE-SHAPED exit-code assertion, never prose: an earlier version of this rule matched the
    words "exit code" inside a comment and credited a diff that asserts no code at all."""
    py = added_lines(diff, ('.py',))
    verdict_asserts = _all_matching(
        py, r'assert\w*\(.*["\'](PASS|FAIL|NO-DATA|NODATA|OK|SATISFIED)')
    if not verdict_asserts:
        return NOT_APPLICABLE, 'this diff adds no assertion on a verdict value'
    code_assert = _first(
        py,
        r'assert\w*\([^\n]*\b(returncode|exit_code|SystemExit)\b|assert\w*\([^\n]*\bmain\(')
    if not code_assert:
        return DECLINED, verdict_asserts[0].strip()[:160]
    return APPLIED, code_assert


def _rule_pipe_tail(diff, added, files):
    """`| tail` : $? after a pipe is the LAST command's code, not the gate's."""
    code = added_lines(diff, ('.sh', '.py', '.yml'))
    pipes = _all_matching(code, r'\|\s*(tail|head)\b')
    if not pipes:
        return NOT_APPLICABLE, 'no added line pipes into head or tail'
    guarded = _first(code, r'PIPESTATUS|pipefail|rc=\$\?|returncode|\bexit_code\b')
    if not guarded:
        return DECLINED, pipes[0].strip()[:160]
    return APPLIED, guarded


def _rule_grep_c(diff, added, files):
    """`grep -c` : it prints 0 and EXITS 1 when it finds nothing, so `n=$(grep -c X f || echo
    0)` yields a two line value. Capture the count on its own line and default it separately."""
    code = added_lines(diff, ('.sh', '.py', '.yml'))
    hits = _all_matching(code, r'grep -c\b')
    if not hits:
        return NOT_APPLICABLE, 'no added line runs grep -c'
    bad = [h for h in hits if re.search(r'grep -c[^\n]*\|\|\s*echo', h)]
    if bad:
        return DECLINED, bad[0].strip()[:160]
    return APPLIED, hits[0].strip()[:160]


def _rule_checksums(diff, added, files):
    """`CHECKSUMS` : editing any tracked file in a product that ships a manifest requires
    regenerating that product's CHECKSUMS.sha256 IN THE SAME COMMIT. The omission fails
    silently at the edit and surfaces later as unrelated tests."""
    products = set()
    manifests = set()
    for path in files:
        m = re.match(r'products/([^/]+)/', path)
        if not m:
            continue
        if path.endswith('CHECKSUMS.sha256'):
            manifests.add(m.group(1))
        else:
            products.add(m.group(1))
    if not products:
        return NOT_APPLICABLE, 'this diff edits no tracked file inside a product tree'
    missing = sorted(products - manifests)
    if missing:
        return DECLINED, 'product(s) edited with no manifest in the same diff: %s' % ', '.join(missing)
    return APPLIED, 'manifest regenerated in the same diff for: %s' % ', '.join(sorted(products))


def _rule_shlex_split(diff, added, files):
    """`shlex.split` : a checker that cannot get a fact must SAY SO and must never convert its
    own limitation into a claim about the thing it was checking. Mechanically: a new field on
    an outward record must carry an explicit unavailable marker, never a quietly plausible
    default."""
    py = added_lines(diff, ('.py',))
    # A field whose value is a plain literal (a lookup table's row) is not the subject: the
    # lesson is about a field the code COMPUTES and might fail to obtain, so the value must
    # reference something. Without this, a data table put units in the denominator.
    fields = [line for line in _all_matching(py, r'^\s*["\'][a-z_]+["\']\s*:')
              if re.search(r'[A-Za-z_]\w*', strip_literals(line.split(':', 1)[1]))]
    if not fields:
        return NOT_APPLICABLE, 'this diff adds no computed field to an outward record'
    unmarked = [f for f in fields
                if not re.search(r'NODATA|NO-DATA|UNVERIFIED|\bNone\b', f)]
    if unmarked:
        return DECLINED, unmarked[0].strip()[:160]
    return APPLIED, fields[0].strip()[:160]


def _rule_done_check(diff, added, files):
    """`done_check` : a tool that checks part of a condition must never report the whole
    condition as met. THE FIX IS A THIRD VERDICT: PARTIAL or NO-DATA beside pass and fail."""
    # Quoted or constant forms only: the verdict vocabulary of this estate appears in almost
    # every docstring, and matching prose made this rule fire on documents about verdicts.
    py = added_lines(diff, ('.py',))
    verdicts = _all_matching(py, r'["\'](PASS|FAIL|SATISFIED|stale|applied)\b')
    if not verdicts:
        return NOT_APPLICABLE, 'this diff emits no pass or fail verdict'
    third = _first(py, r'["\'](PARTIAL|NO-DATA|NODATA|UNVERIFIED|unverified)'
                       r'|\b(PARTIAL|NODATA|UNVERIFIED)\b\s*[=,)]')
    if not third:
        return DECLINED, verdicts[0].strip()[:160]
    return APPLIED, third


def _rule_certify(diff, added, files):
    """`certify` : a gate can be written against a commit that does not exist. Resolve every
    hash a plan names with `git cat-file -e`, which fails on absence, never with rev-parse."""
    code = added_lines(diff, ('.py', '.sh'))
    # The subject is a NAMED HASH being acted on, so the detector is a literal sha or a call
    # that resolves one. An earlier version also matched the bare word "revision", which fired
    # on a comment and produced a verdict about prose.
    hashes = _all_matching(code, r'\b[0-9a-f]{40}\b|rev-parse')
    if not hashes:
        return NOT_APPLICABLE, 'this diff names no commit hash and resolves none'
    resolved = _first(added, r'cat-file')
    if not resolved:
        return DECLINED, hashes[0].strip()[:160]
    return APPLIED, resolved


RULES = {
    'repeat-guard:def-space': _rule_def_space,
    'repeat-guard:open-paren': _rule_open_paren,
    'repeat-guard:unittest': _rule_unittest,
    'repeat-guard:pipe-tail': _rule_pipe_tail,
    'repeat-guard:grep-c': _rule_grep_c,
    'repeat-guard:checksums': _rule_checksums,
    'repeat-guard:shlex-split': _rule_shlex_split,
    'repeat-guard:done-check': _rule_done_check,
    'repeat-guard:certify': _rule_certify,
}


def judge(diff_text, lesson_id):
    """(verdict, evidence) for one lesson over one diff. Raises KeyError on an unknown id
    rather than inventing a verdict for a lesson nobody wrote a rule for."""
    rule = RULES[lesson_id]
    added = added_lines(diff_text)
    return rule(diff_text, added, touched_files(diff_text))


def read_diff(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return fh.read()
    except OSError as exc:
        raise SystemExit('v3_judge: cannot read diff %s: %s' % (path, exc))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--diff', required=True, help='path to a unified diff of one work unit')
    ap.add_argument('--lesson', action='append', default=[],
                    help='lesson id to judge (repeatable); default: every rule')
    ap.add_argument('--unit', default='', help='unit id, printed for the record only')
    args = ap.parse_args(argv)
    diff_text = read_diff(args.diff)
    ids = args.lesson or list(LESSON_IDS)
    unknown = [i for i in ids if i not in RULES]
    if unknown:
        print('v3_judge: NO-DATA: no rule for %s, so no verdict is produced for it'
              % ', '.join(unknown), file=sys.stderr)
        return 2
    print('unit: %s' % (args.unit or os.path.basename(args.diff)))
    for lesson_id in ids:
        verdict, evidence = judge(diff_text, lesson_id)
        print('%-26s %-15s %s' % (lesson_id, verdict, evidence))
    return 0


if __name__ == '__main__':
    sys.exit(main())
