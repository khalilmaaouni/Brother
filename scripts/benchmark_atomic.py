#!/usr/bin/env python3
"""Atomic benchmark harness: this estate against the host harness's main
methodology plugin (installed 6.1.1), one runnable local check per cell.

This implements B1 (metric set from checks that already exist) and the local
half of B2 (the harness as one host-neutral local script) from the plan's
benchmark leadership block (docs/plan/UNIFIED-WBS.md, P6). "Atomic" here means
every cell is one mechanical, rerunnable check with an evidence line, never an
adjective: each criterion below returns PASS, FAIL, or NO-DATA, and NO-DATA is
never a pass and never silently zeroed.

Subjects, PUBLIC CONTENT ONLY:
  brother       this estate's own products: the local checkout under this
                repository (README, docs) plus the latest installed plugin
                cache for brothermode and brothersbe, if present.
  superpowers   the installed 6.1.1 skills, from the plugin cache if present,
                else the ~/.claude/skills checkout.
  bmad          github.com/bmad-code-org/BMAD-METHOD, read from a local,
                pinned clone (see PINNED_SHAS below). Override the clone
                location with env var BENCH_ROOT_BMAD.
  gsd           github.com/open-gsd/gsd-core, same pattern, env var
                BENCH_ROOT_GSD.
  speckit       github.com/github/spec-kit, same pattern, env var
                BENCH_ROOT_SPECKIT.

This script never clones anything itself (no network in the checks): the
bmad/gsd/speckit subjects each read a configurable local root, default a
durable directory under the user's home (see SCRATCH_SUBJECTS_ROOT below,
2026-08-29 repair: the earlier default lived inside a session's ephemeral
scratchpad and was gone by the next session), and report NO-DATA naming
every path probed plus the exact command to populate it when that root is
absent.

A subject whose material is absent on this machine reports NO-DATA naming
every path this script probed for it. Six of the nine checks read SHIPPED
TEXT (what a product ships and states); see the LIMITS section of
docs/benchmarks/ATOMIC-BENCHMARK.md for what that does and does not prove.
The other three (install-commands-documented, receipt-artifact-exists,
audit-trail-documented) are BEHAVIOURAL, 2026-08-29 repair: each RUNS a real
command extracted from the subject's own shipped material (or, for
audit-trail-documented, runs `git log` against the subject's own root) and
scores the actual exit code, never the presence of a word. Shipped text that
merely NAMES a capability without one that actually runs reads FAIL on these
three, by design; a criterion resting on "the word matched somewhere" proved
the words appear, not that the capability exists.

Runs on the 3.9 floor (/usr/bin/python3): no match statements, no syntax
newer than 3.9, standard library only.

Exit 0 when at least one (criterion, subject) cell scored PASS or FAIL.
Exit 2 when every cell came back NO-DATA, i.e. nothing could be scored.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

DIMENSIONS = ('ux', 'onboarding', 'ba', 'fielding', 'receipt')

Criterion = namedtuple('Criterion', ['id', 'dimension', 'description', 'check'])

MAX_FILES_PER_ROOT = 2000
MAX_FILE_BYTES = 300000
# 'plan' and 'handover' hold this estate's own working notes, and 'worktrees'
# holds per-agent copies of the tree. None of the three is text a newcomer can
# ever meet, so scanning them measured our planning vocabulary as if it were
# shipped onboarding copy. Founder ruling 2026-08-28, taken only because the
# corpus was genuinely wrong and recorded here so the flip is auditable rather
# than silent: the first-run checks read what SHIPS, never what we write to
# each other. Narrowing a check to win would be gaming it; narrowing it to the
# thing it claims to measure is the fix.
EXCLUDE_DIR_NAMES = set(['.git', '.in_use', 'evidence', 'node_modules', '__pycache__', '.sbe',
                         'plan', 'handover', 'worktrees'])


# ---------------------------------------------------------------------- #
# Corpus collection
# ---------------------------------------------------------------------- #

def collect_markdown(root, limit=MAX_FILES_PER_ROOT):
    out = []
    if root is None or not root.exists():
        return out
    for p in sorted(root.rglob('*.md')):
        if any(part in EXCLUDE_DIR_NAMES for part in p.parts):
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def read_text(path, cap=MAX_FILE_BYTES):
    try:
        data = path.read_bytes()[:cap]
    except OSError:
        return ''
    return data.decode('utf-8', errors='replace')


class Subject(object):
    def __init__(self, name, roots, readme_path, probed):
        self.name = name
        self.roots = [r for r in roots if r is not None]
        self.readme_path = readme_path
        self.probed = probed
        self._files = None
        self._texts = None

    def files(self):
        if self._files is None:
            found = []
            for root in self.roots:
                found.extend(collect_markdown(root))
            self._files = found
        return self._files

    def file_texts(self):
        if self._texts is None:
            texts = [(p, read_text(p)) for p in self.files()]
            if self.readme_path is not None and self.readme_path not in self.files():
                texts.append((self.readme_path, read_text(self.readme_path)))
            self._texts = texts
        return self._texts

    def has_material(self):
        return len(self.files()) > 0 or (self.readme_path is not None and self.readme_path.exists())

    def probed_note(self):
        return '; '.join(self.probed) if self.probed else '(no path probed)'


def latest_version_dir(base):
    """Pick the highest-versioned subdirectory of a plugin cache base dir,
    e.g. .claude/plugins/cache/<marketplace>/<plugin>/<version>. None if base
    does not exist or holds no subdirectory."""
    if base is None or not base.is_dir():
        return None
    subs = [d for d in base.iterdir() if d.is_dir()]
    if not subs:
        return None

    def vkey(d):
        parts = re.split(r'[.\-]', d.name)
        key = []
        for part in parts:
            if part.isdigit():
                key.append((1, int(part)))
            else:
                key.append((0, part))
        return key

    subs.sort(key=vkey)
    return subs[-1]


def build_brother_subject():
    repo_root = Path(__file__).resolve().parent.parent
    home = Path.home()
    probed = []
    roots = []
    if repo_root.is_dir():
        roots.append(repo_root)
    else:
        probed.append(str(repo_root))
    # The installer writes under the MARKETPLACE name, so both leaves live at
    # cache/brother/<leaf>. The legacy cache/<leaf>/<leaf> paths still exist on
    # this machine and stop at an older version, so probing them graded a copy
    # the installer no longer updates: measured 2026-08-28, when an update to
    # 3.5.2 landed at cache/brother/brothersbe while the legacy path still read
    # 3.4.2. Both are probed, newest wins, and a stale legacy copy can no
    # longer decide the score on its own.
    for leaf in ('brothermode', 'brothersbe'):
        candidates = [
            home / '.claude' / 'plugins' / 'cache' / 'brother' / leaf,
            home / '.claude' / 'plugins' / 'cache' / leaf / leaf,
        ]
        chosen = None
        for base in candidates:
            probed.append(str(base))
            d = latest_version_dir(base)
            if d is not None and chosen is None:
                chosen = d
        if chosen is not None:
            roots.append(chosen)
    readme_path = repo_root / 'README.md'
    if not readme_path.exists():
        probed.append(str(readme_path))
        readme_path = None
    return Subject('brother', roots, readme_path, probed)


def build_superpowers_subject():
    home = Path.home()
    probed = []
    base = home / '.claude' / 'plugins' / 'cache' / 'superpowers-marketplace' / 'superpowers'
    probed.append(str(base))
    root = latest_version_dir(base)
    if root is None:
        fallback = home / '.claude' / 'skills' / 'superpowers'
        probed.append(str(fallback))
        root = fallback if fallback.is_dir() else None
    roots = [root] if root is not None else []
    readme_path = (root / 'README.md') if root is not None else None
    if readme_path is not None and not readme_path.exists():
        probed.append(str(readme_path))
        readme_path = None
    return Subject('superpowers', roots, readme_path, probed)


# Local, pinned clones of public open-source repositories. This script never
# clones anything: each root is either an override path from the named env
# var, or this DURABLE default directory under the user's home; if neither
# exists, the subject reports NO-DATA naming both paths it probed plus the
# exact command a human runs to populate the default (clone_command_hint,
# below). Shas pinned the day the clone was made (2026-08-27); see
# docs/benchmarks/ATOMIC-BENCHMARK.md for how to reproduce the same clone at
# the same commit.
#
# 2026-08-29 repair: this used to default into a SESSION'S scratchpad
# (/private/tmp/claude-.../scratchpad/bench-subjects), which is ephemeral and
# gone the moment that session ends, per this estate's own rule that scratch
# directories are never the only home of a deliverable. Every subsequent run
# then read NO-DATA for all three cloned subjects, silently, because nothing
# reminded a session the clone needed remaking. The fix is not cleverer
# detection, it is putting the default somewhere that survives: under the
# user's home, in the directory this estate's own rules already name for
# durable build output (~/Documents/BrotherArchive/).
SCRATCH_SUBJECTS_ROOT = Path.home() / 'Documents' / 'BrotherArchive' / 'bench-subjects'

PINNED_SHAS = {
    'bmad': '922c86d2c521c881049af94bdf62247b5e019ce9',
    'gsd': '929e02cb2cfc5b0aae66c1db1cd491a0b3e4c47b',
    'speckit': '241d9163640603beb8e2ef1d1223756c7ccdfdb3',
}

CLONE_ROOT_ENV_VARS = {
    'bmad': 'BENCH_ROOT_BMAD',
    'gsd': 'BENCH_ROOT_GSD',
    'speckit': 'BENCH_ROOT_SPECKIT',
}

# github.com URLs for the three cloned subjects, named once here so the hint
# below and docs/benchmarks/ATOMIC-BENCHMARK.md's Subjects section (which
# names the same three repositories) never have to be regrepped to find them.
CLONE_REPO_URLS = {
    'bmad': 'https://github.com/bmad-code-org/BMAD-METHOD.git',
    'gsd': 'https://github.com/open-gsd/gsd-core.git',
    'speckit': 'https://github.com/github/spec-kit.git',
}


def clone_command_hint(name):
    """The exact, copy-pasteable command a human runs to populate the
    durable default clone location for a missing cloned subject. This
    script never runs it (no network in any check, per this file's own
    docstring); it only ever prints it so a NO-DATA verdict is actionable
    rather than a dead end."""
    dest = SCRATCH_SUBJECTS_ROOT / name
    return (
        'populate with: mkdir -p "%s" && git clone --depth 1 %s "%s" '
        '&& git -C "%s" checkout %s  (or point env var %s at an existing '
        'clone elsewhere)' % (
            SCRATCH_SUBJECTS_ROOT, CLONE_REPO_URLS[name], dest, dest,
            PINNED_SHAS[name], CLONE_ROOT_ENV_VARS[name]))

# The borrow flow's own generated file (docs/benchmarks/BORROW-QUEUE.md) and
# its optional hand maintained stage overrides. Both live beside
# docs/benchmarks/ATOMIC-BENCHMARK.md, never inside this script's own
# directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
BORROW_STAGES_PATH = _REPO_ROOT / 'docs' / 'benchmarks' / 'borrow-stages.json'
BORROW_QUEUE_PATH = _REPO_ROOT / 'docs' / 'benchmarks' / 'BORROW-QUEUE.md'


def build_cloned_subject(name):
    """A subject read from a local clone of a public repository, pinned to
    the sha in PINNED_SHAS. The root is configurable per subject via its env
    var (for a clone kept somewhere other than this session's scratch path);
    an absent root reports NO-DATA naming every path probed, never a clone
    attempt of its own."""
    probed = []
    candidates = []
    override = os.environ.get(CLONE_ROOT_ENV_VARS[name])
    if override:
        candidates.append(Path(override))
    candidates.append(SCRATCH_SUBJECTS_ROOT / name)
    root = None
    for c in candidates:
        probed.append(str(c))
        if c.is_dir():
            root = c
            break
    if root is None:
        probed.append(clone_command_hint(name))
    roots = [root] if root is not None else []
    readme_path = (root / 'README.md') if root is not None else None
    if readme_path is not None and not readme_path.exists():
        probed.append(str(readme_path))
        readme_path = None
    return Subject(name, roots, readme_path, probed)


def build_all_subjects():
    return [
        build_brother_subject(),
        build_superpowers_subject(),
        build_cloned_subject('bmad'),
        build_cloned_subject('gsd'),
        build_cloned_subject('speckit'),
    ]


# ---------------------------------------------------------------------- #
# Section parsing (for the one criterion that needs "which section", the
# rest are plain presence/co-occurrence checks over the shipped text)
# ---------------------------------------------------------------------- #

HEADING_RE = re.compile(r'^(#{1,6})\s+(\S.*?)\s*$', re.MULTILINE)


def iter_sections(text):
    matches = list(HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2)
        start = m.end()
        end = len(text)
        for m2 in matches[i + 1:]:
            if len(m2.group(1)) <= level:
                end = m2.start()
                break
        yield title, text[start:end]


FENCE_RE = re.compile(r'```[^\n]*\n(.*?)```', re.DOTALL)
INSTALL_HEADING_RE = re.compile(r'^#+\s*.*install.*$', re.IGNORECASE | re.MULTILINE)


def find_install_block(text):
    m = INSTALL_HEADING_RE.search(text)
    if not m:
        return None
    fm = FENCE_RE.search(text[m.end():])
    if not fm:
        return None
    return fm.group(1)


FIRST_RUN_TITLE_RE = re.compile(r'quickstart|getting started|first run|install', re.IGNORECASE)
FAIL_TOKEN_RE = re.compile(r'\bFAIL\b')


# ---------------------------------------------------------------------- #
# Behavioural probing: shared by the three checks below that RUN a real
# command and read its actual exit code, rather than grepping for a word.
# Every probe is read-only by construction: it only ever invokes a resolved
# command with --version/--help/log, never the literal command line a
# README documents, so third-party shipped text (once bmad/gsd/speckit are
# cloned) can never make this script execute something arbitrary.
# ---------------------------------------------------------------------- #

PROBE_TIMEOUT_SECONDS = 5


def run_probe(argv):
    """Run argv (a short, fixed list, never shell=True) and report whether
    it exited 0. Every failure mode (binary absent, times out, not
    executable, any other OSError) is caught and reported as a clean
    non-success, never a crash: a subject's shipped material naming a
    command that cannot actually run is exactly the FAIL this check exists
    to find."""
    try:
        proc = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, '%s: %s' % (type(exc).__name__, exc)
    detail = 'exit %d' % proc.returncode
    first_line = proc.stdout.decode('utf-8', errors='replace').strip().splitlines()
    if first_line:
        detail += ' ("%s")' % first_line[0][:80]
    return proc.returncode == 0, detail


COMMAND_PROMPT_RE = re.compile(r'^[\$>]\s*')
ENV_ASSIGNMENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def first_command_token(line):
    """The first real executable name on a shipped command line: strips a
    leading shell prompt ($ or >), skips leading FOO=bar env assignments
    and a leading sudo, and returns None for a blank line. Never invents a
    token; a line this cannot parse simply cannot be probed."""
    line = COMMAND_PROMPT_RE.sub('', line.strip())
    parts = line.split()
    i = 0
    while i < len(parts) and ENV_ASSIGNMENT_RE.match(parts[i]):
        i += 1
    if i >= len(parts):
        return None
    if parts[i] == 'sudo' and i + 1 < len(parts):
        i += 1
    return parts[i]


def check_install_commands(subject):
    """BEHAVIOURAL. Keeps the original text extraction (the first fenced
    code block after an Install heading, PASS only when it is a single
    line: onboarding should need one command), then RUNS that command's
    first token with --version and reads the real exit code. A single
    documented command that does not actually resolve to a runnable binary
    on this machine is a FAIL, not the PASS a pure line-count would give
    it."""
    if subject.readme_path is None:
        return 'NO-DATA', 'no README found; probed %s' % subject.probed_note()
    text = read_text(subject.readme_path)
    block = find_install_block(text)
    if block is None:
        return 'NO-DATA', 'no Install heading with a fenced code block found in %s' % subject.readme_path
    lines = [l for l in block.splitlines() if l.strip()]
    n = len(lines)
    if n != 1:
        return 'FAIL', '%d install command line(s) in the first fenced block after an Install heading in %s' % (
            n, subject.readme_path)
    token = first_command_token(lines[0])
    if token is None:
        return 'FAIL', 'the one documented install line in %s has no runnable command token' % subject.readme_path
    ok, detail = run_probe([token, '--version'])
    verdict = 'PASS' if ok else 'FAIL'
    return verdict, 'ran `%s --version` (the one install command in %s), %s' % (
        token, subject.readme_path, detail)


def check_fail_states(subject):
    if not subject.has_material():
        return 'NO-DATA', 'no shipped markdown found; probed %s' % subject.probed_note()
    hits = []
    seen_section = False
    for path, text in subject.file_texts():
        for title, body in iter_sections(text):
            if FIRST_RUN_TITLE_RE.search(title):
                seen_section = True
                n = len(FAIL_TOKEN_RE.findall(body))
                if n:
                    hits.append('%s#%s (%d)' % (path, title, n))
    if not seen_section:
        return 'NO-DATA', 'no quickstart/getting-started/first-run/install section found in any shipped file'
    if hits:
        return 'FAIL', 'FAIL token found in first-run section(s): %s' % ', '.join(hits)
    return 'PASS', 'no FAIL token found in any quickstart/getting-started/first-run/install section scanned'


def presence_check(subject, pattern, label):
    if not subject.has_material():
        return 'NO-DATA', 'no shipped markdown found; probed %s' % subject.probed_note()
    for path, text in subject.file_texts():
        if pattern.search(text):
            return 'PASS', '%s matched in %s' % (label, path)
    return 'FAIL', '%s never matched in any of %d shipped file(s) scanned' % (label, len(subject.files()))


def per_file_cooccurrence(subject, patterns, label):
    if not subject.has_material():
        return 'NO-DATA', 'no shipped markdown found; probed %s' % subject.probed_note()
    for path, text in subject.file_texts():
        if all(p.search(text) for p in patterns):
            return 'PASS', '%s found together in %s' % (label, path)
    return 'FAIL', '%s never found together in any of %d shipped file(s) scanned' % (label, len(subject.files()))


# A shipped command line counts as a candidate "verify/receipt" command when
# it names one of these whole words; deliberately narrow (no bare "run" or
# "start", which match almost anything) so a false lead is rare, not absent.
# ponytail: this is a word-list heuristic over the first token of the first
# matching line, not an understanding of what the command actually does. It
# can over-credit a generic interpreter (e.g. `python3 -m unittest  # test`
# credits `python3`, not the actual test suite). Tighten by requiring the
# resolved token itself to be plugin/repo-local (not a bare interpreter) if
# this starts producing false PASSes in practice.
VERIFY_WORD_RE = re.compile(r'\b(test|pytest|unittest|check|verify)\b', re.IGNORECASE)
MAX_VERIFY_CANDIDATES = 5


def check_receipt_behavioral(subject):
    """BEHAVIOURAL, replacing a grep for the word "receipt". Scans every
    fenced code block in the subject's shipped markdown for a line naming a
    test/check/verify command, then RUNS up to MAX_VERIFY_CANDIDATES of
    those commands with --help and reads the real exit code. PASS on the
    first one that actually runs; FAIL when candidates exist but none of
    them do, or when shipped material exists but names no such command at
    all."""
    if not subject.has_material():
        return 'NO-DATA', 'no shipped markdown found; probed %s' % subject.probed_note()
    tried = []
    for path, text in subject.file_texts():
        for block_match in FENCE_RE.finditer(text):
            for line in block_match.group(1).splitlines():
                if not line.strip() or not VERIFY_WORD_RE.search(line):
                    continue
                token = first_command_token(line)
                if token is None:
                    continue
                if len(tried) >= MAX_VERIFY_CANDIDATES:
                    break
                ok, detail = run_probe([token, '--help'])
                tried.append('`%s --help` (from %s): %s' % (token, path, detail))
                if ok:
                    return 'PASS', 'ran %s' % tried[-1]
            if len(tried) >= MAX_VERIFY_CANDIDATES:
                break
        if len(tried) >= MAX_VERIFY_CANDIDATES:
            break
    if tried:
        return 'FAIL', 'tried %d candidate command(s), none ran: %s' % (len(tried), '; '.join(tried))
    return 'FAIL', 'no test/check/verify command found in any of %d shipped file(s) scanned' % len(subject.files())


def check_audit_trail_behavioral(subject):
    """BEHAVIOURAL, replacing a grep for the phrase "audit trail". An audit
    trail a team can actually field is a rerunnable, inspectable history,
    so this RUNS `git log` against the subject's own root(s) and reads
    whether that root really is a git working tree with commits, rather
    than whether some file happens to contain the words."""
    if not subject.has_material():
        return 'NO-DATA', 'no shipped markdown found; probed %s' % subject.probed_note()
    roots = list(subject.roots)
    if not roots and subject.readme_path is not None:
        roots = [subject.readme_path.parent]
    if not roots:
        return 'NO-DATA', 'no root directory found; probed %s' % subject.probed_note()
    tried = []
    for root in roots:
        cmd = ['git', '-C', str(root), 'log', '--oneline', '-1']
        ok, detail = run_probe(cmd)
        tried.append('`%s`: %s' % (' '.join(cmd), detail))
        if ok:
            return 'PASS', 'ran %s' % tried[-1]
    return 'FAIL', 'tried %d root(s), none is a git working tree with history: %s' % (len(tried), '; '.join(tried))


CRITERIA = [
    Criterion(
        'install-commands-documented', 'onboarding',
        'BEHAVIOURAL: finds the first fenced code block under an Install heading in '
        "the product README, and when it is a single line, RUNS that command's first "
        'token with --version and reads the real exit code; PASS needs both the single '
        'line and a command that actually runs',
        check_install_commands),
    Criterion(
        'fail-states-on-first-run', 'onboarding',
        "scans quickstart/getting-started/first-run/install sections of shipped "
        "text for the token FAIL; PASS when none appear before first value",
        check_fail_states),
    Criterion(
        'receipt-artifact-exists', 'receipt',
        'BEHAVIOURAL: finds a test/check/verify command named in any shipped fenced '
        'code block and RUNS it with --help, reading the real exit code; PASS needs a '
        'command that actually runs, never just the word "receipt" appearing',
        check_receipt_behavioral),
    Criterion(
        'plan-before-approval-stated', 'ux',
        "does a shipped file state a plan and an approval gate together "
        "(co-occurrence of 'plan' and 'approv*' in one file)",
        lambda s: per_file_cooccurrence(
            s, [re.compile(r'\bplan\b', re.IGNORECASE), re.compile(r'\bapprov\w*', re.IGNORECASE)],
            "'plan' and 'approv*'")),
    Criterion(
        'options-with-recommendation', 'ux',
        'does a shipped file require multiple options or alternatives together '
        'with a recommendation (co-occurrence in one file)',
        lambda s: per_file_cooccurrence(
            s, [re.compile(r'\brecommend\w*', re.IGNORECASE), re.compile(r'\b(alternative|options?)\b', re.IGNORECASE)],
            "'recommend*' and 'alternative/option'")),
    Criterion(
        'diagram-by-default', 'ux',
        "does a shipped file require a diagram BY DEFAULT rather than on request "
        "(co-occurrence of 'mermaid' and 'default'/'always'/'every' in one file)",
        lambda s: per_file_cooccurrence(
            s, [re.compile(r'\bmermaid\b', re.IGNORECASE), re.compile(r'\b(default|always|every)\b', re.IGNORECASE)],
            "'mermaid' and 'default/always/every'")),
    Criterion(
        'ba-path-exists', 'ba',
        'does shipped text document any non-developer or business-analyst path',
        lambda s: presence_check(
            s, re.compile(r'business analyst|non-?technical|non-?developer', re.IGNORECASE),
            'a non-developer/business-analyst phrase')),
    Criterion(
        'level-adaptation', 'ba',
        "does shipped text document adapting explanation depth to the user's role or skill level",
        lambda s: presence_check(
            s, re.compile(r'\blevel[\s-]?adapt\w*|adapt\w*\s+(?:to|depth).{0,20}(role|skill|level)', re.IGNORECASE),
            'a level/role adaptation phrase')),
    Criterion(
        'audit-trail-documented', 'fielding',
        "BEHAVIOURAL: RUNS `git log` against the subject's own root(s) and reads whether "
        "it really is a git working tree with commits; PASS needs a real, inspectable "
        'history, never the phrase "audit trail" appearing in a file',
        check_audit_trail_behavioral),
]

assert all(c.dimension in DIMENSIONS for c in CRITERIA), 'every criterion must declare one of the five dimensions'


# ---------------------------------------------------------------------- #
# Orchestration
# ---------------------------------------------------------------------- #

def score_all(subjects, criteria):
    results = {}
    for c in criteria:
        for s in subjects:
            results[(c.id, s.name)] = c.check(s)
    return results


def print_report(subjects, criteria, results):
    for c in criteria:
        for s in subjects:
            verdict, evidence = results[(c.id, s.name)]
            print('- %s [%s] %s: %s ; %s' % (c.id, c.dimension, s.name, verdict, evidence))
    counts = dict((s.name, {'PASS': 0, 'FAIL': 0, 'NO-DATA': 0}) for s in subjects)
    for (cid, sname), (verdict, evidence) in results.items():
        counts[sname][verdict] += 1
    print('--- summary ---')
    scored_total = 0
    for s in subjects:
        c = counts[s.name]
        scored_total += c['PASS'] + c['FAIL']
        print('%s: PASS=%d FAIL=%d NO-DATA=%d of %d criteria' % (
            s.name, c['PASS'], c['FAIL'], c['NO-DATA'], len(criteria)))
    return scored_total


# ---------------------------------------------------------------------- #
# Scoring: every criterion carries weight 1 (equal, transparent). Per
# subject, score = 10 * (PASS-weight) / (total-weight MINUS that subject's
# own NO-DATA weight). NO-DATA is EXCLUDED from the denominator, never
# counted as a zero, this estate's intake_score.py convention. A subject
# with zero covered checks (every cell NO-DATA) scores None, never a
# division by zero and never a silent 0.
# ---------------------------------------------------------------------- #

def compute_scores(subjects, criteria, results):
    """Per-subject: {'subject','score','pass','fail','no_data','covered',
    'total','criteria': {criterion_id: {'verdict','evidence'}}}. 'score' is
    None when covered == 0 (nothing could be scored for that subject)."""
    total = len(criteria)
    out = {}
    for s in subjects:
        pass_n = fail_n = no_data_n = 0
        per_criterion = {}
        for c in criteria:
            verdict, evidence = results[(c.id, s.name)]
            per_criterion[c.id] = {'verdict': verdict, 'evidence': evidence}
            if verdict == 'PASS':
                pass_n += 1
            elif verdict == 'FAIL':
                fail_n += 1
            else:
                no_data_n += 1
        covered = total - no_data_n
        score = round(10.0 * pass_n / covered, 1) if covered > 0 else None
        out[s.name] = {
            'subject': s.name, 'score': score, 'pass': pass_n, 'fail': fail_n,
            'no_data': no_data_n, 'covered': covered, 'total': total,
            'criteria': per_criterion,
        }
    return out


def reason_sentence(criterion_id, leader, leader_evidence, brother_evidence):
    """One plain sentence of what the leader does better, derived from the
    two evidence lines the checks themselves produced, never invented. Two
    criteria get a hand written template that extracts the exact fact the
    check measured; any other criterion (a future FAIL against a PASS this
    file did not anticipate) falls back to a generic sentence that quotes
    both evidence lines directly rather than guessing at a claim."""
    if criterion_id == 'install-commands-documented':
        m_leader = re.search(r'(\d+) install command', leader_evidence)
        m_brother = re.search(r'(\d+) install command', brother_evidence)
        if m_leader and m_brother:
            return ('%s needs only %s install command(s) to get started; brother needs %s.'
                     % (leader, m_leader.group(1), m_brother.group(1)))
    if criterion_id == 'fail-states-on-first-run':
        return ('%s ships no FAIL token anywhere in its first-run sections; '
                 "brother's shipped text still does." % leader)
    return '%s: %s (brother: %s)' % (leader, leader_evidence, brother_evidence)


def compute_reasons(subjects, criteria, results):
    """One entry per criterion where brother is not PASS and at least one
    other subject IS PASS: {'criterion','dimension','leaders','leader_evidence'
    (dict subject->evidence),'brother_verdict','brother_evidence','sentence'}.
    Empty list when brother has no such losing cell, or when no subject
    named 'brother' was scored at all."""
    reasons = []
    subject_names = [s.name for s in subjects]
    if 'brother' not in subject_names:
        return reasons
    for c in criteria:
        brother_v, brother_ev = results[(c.id, 'brother')]
        if brother_v == 'PASS':
            continue
        leaders = sorted(
            name for name in subject_names
            if name != 'brother' and results[(c.id, name)][0] == 'PASS')
        if not leaders:
            continue
        leader_evidence = dict((name, results[(c.id, name)][1]) for name in leaders)
        sentence = reason_sentence(c.id, leaders[0], leader_evidence[leaders[0]], brother_ev)
        reasons.append({
            'criterion': c.id,
            'dimension': c.dimension,
            'leaders': leaders,
            'leader_evidence': leader_evidence,
            'brother_verdict': brother_v,
            'brother_evidence': brother_ev,
            'sentence': sentence,
        })
    return reasons


def print_scored_summary(scores, reasons):
    print('--- scored (10 scale, NO-DATA excluded from the denominator) ---')
    ordered = sorted(
        scores.values(),
        key=lambda r: (-(r['score'] if r['score'] is not None else -1), r['subject']))
    for r in ordered:
        if r['score'] is None:
            print('%s: NO-DATA/10 (0 of %d checks covered)' % (r['subject'], r['total']))
        else:
            print('%s: %s/10 over %d of %d checks (PASS=%d FAIL=%d NO-DATA=%d)' % (
                r['subject'], r['score'], r['covered'], r['total'],
                r['pass'], r['fail'], r['no_data']))
    if reasons:
        print('--- why brother loses where it loses ---')
        for r in reasons:
            print('- %s [%s]: %s' % (r['criterion'], r['dimension'], r['sentence']))
    else:
        print('--- brother has no losing cell against any scored subject in this run ---')


# ---------------------------------------------------------------------- #
# The borrow flow: one item per losing cell (brother not PASS, someone else
# PASS), plus BEATEN detection when a previously tracked item's cell has
# since flipped to brother PASS. Stage progression (RESEARCH -> DESIGN ->
# BUILD -> RE-MEASURE) is hand maintained in docs/benchmarks/borrow-stages.json;
# BEATEN is never hand set, only detected here from the run data itself, per
# the law this estate refuses to bend: a borrow item closes only when the
# re-run benchmark cell flips, never on anyone's say-so.
# ---------------------------------------------------------------------- #

# Each entry is either a citation-backed phase quoted from
# docs/plan/COMPETITOR-MATCHING-2026-08-27.md (never invented), or None when
# that document names no phase for this exact criterion, in which case
# phase_for_criterion() reports the honest fallback instead of guessing.
CRITERION_TO_PHASE = {
    'install-commands-documented': None,
    'fail-states-on-first-run': (
        'Phase 0, built and core-verified: "a fresh install reads as a new '
        'project, never a FAIL" (docs/plan/SPEC-first-run-welcome.md; branch '
        'BrotherSBE ease/first-run-welcome, ffda902; per '
        'docs/plan/COMPETITOR-MATCHING-2026-08-27.md section B).'),
    'receipt-artifact-exists': (
        'Phase 1, built: "the close binds to a rerunnable RECEIPT" '
        '(docs/plan/COMPETITOR-MATCHING-2026-08-27.md section A, Phase 1 item 4).'),
    'plan-before-approval-stated': (
        'Phase 1, built 2026-08-27: "the plan always shown before approval is '
        'requested" (docs/plan/COMPETITOR-MATCHING-2026-08-27.md section A, '
        'BrotherSBE ease/adaptive-intake branch).'),
    'options-with-recommendation': (
        'Phase 1, built: "two to three options with a stated recommendation and '
        'what each forecloses (Superpowers, Muse Code)" '
        '(docs/plan/COMPETITOR-MATCHING-2026-08-27.md section A).'),
    'diagram-by-default': (
        'Phase 1 built the text version, "exactly one fenced Mermaid flow '
        'diagram (BMAD\'s own cap)"; rendered-in-UI diagrams and option-picker '
        'cards are Phase 3 (docs/plan/COMPETITOR-MATCHING-2026-08-27.md section A).'),
    'ba-path-exists': (
        'Phase 1, built: stage-aware discovery "playing understanding back as '
        'outcomes for the business analyst (BMAD)" '
        '(docs/plan/COMPETITOR-MATCHING-2026-08-27.md section A).'),
    'level-adaptation': (
        'Phase 2: "the vault-backed learned profile", named as the sharpest '
        'move on this exact gap (docs/plan/COMPETITOR-MATCHING-2026-08-27.md '
        'section A).'),
    'audit-trail-documented': (
        'Phase 1, built: "the close binds to a rerunnable RECEIPT, the one '
        'thing no competitor gives" (docs/plan/COMPETITOR-MATCHING-2026-08-27.md '
        'section D).'),
}


def phase_for_criterion(criterion_id):
    return CRITERION_TO_PHASE.get(criterion_id) or 'unscheduled, needs a design'


def research_location(subject_name):
    """Where to go re-research a leader's shipped mechanism: the pinned
    local clone path and sha for the three cloned open-source subjects,
    else the best citation this harness already carries for it. Never
    invents a path or sha it did not already pin."""
    if subject_name in PINNED_SHAS:
        return '%s at pinned sha %s (docs/benchmarks/ATOMIC-BENCHMARK.md, Subjects)' % (
            SCRATCH_SUBJECTS_ROOT / subject_name, PINNED_SHAS[subject_name])
    if subject_name == 'superpowers':
        return ('the installed plugin cache '
                 '(~/.claude/plugins/cache/superpowers-marketplace/superpowers), '
                 'no pinned sha (not a cloned subject; docs/benchmarks/ATOMIC-BENCHMARK.md, Subjects)')
    if subject_name == 'brother':
        return "this repository's own checkout (no external research needed)"
    return 'no pinned clone or citation on file for this subject'


def load_stage_overrides(path=None):
    """docs/benchmarks/borrow-stages.json: hand maintained stage overrides,
    keyed by borrow item id. Absent, unreadable, or malformed means every
    item starts at RESEARCH; this optional file never crashes a run."""
    p = path if path is not None else BORROW_STAGES_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def compute_borrow_items(subjects, criteria, results, stage_overrides):
    """One item per losing cell (brother not PASS, >=1 other subject PASS),
    plus any item id already present in stage_overrides whose cell has since
    flipped to brother PASS: that one is emitted at stage BEATEN,
    automatically, regardless of what the hand-maintained file says, since
    an item closes only when the re-run benchmark cell itself flips."""
    subject_names = [s.name for s in subjects]
    if 'brother' not in subject_names:
        return []
    items = []
    for c in criteria:
        item_id = 'borrow-%s' % c.id
        brother_v, brother_ev = results[(c.id, 'brother')]
        leaders = sorted(
            name for name in subject_names
            if name != 'brother' and results[(c.id, name)][0] == 'PASS')
        if brother_v == 'PASS':
            if item_id not in stage_overrides:
                continue  # never tracked, brother already passing: nothing to show
            stage = 'BEATEN'
        else:
            if not leaders:
                continue  # brother is not PASS, but nobody else is either: nothing to borrow
            stage = stage_overrides.get(item_id, 'RESEARCH')
        leader_detail = [
            {'subject': name, 'evidence': results[(c.id, name)][1],
             'research': research_location(name)}
            for name in leaders
        ]
        items.append({
            'id': item_id,
            'criterion': c.id,
            'dimension': c.dimension,
            'leaders': leaders,
            'leader_detail': leader_detail,
            'brother_verdict': brother_v,
            'brother_evidence': brother_ev,
            'proposed_move': phase_for_criterion(c.id),
            'stage': stage,
        })
    return items


def md_escape(text):
    """Keep a markdown table cell on one line and one column."""
    return (text or '').replace('|', '/').replace('\n', ' ')


def render_borrow_queue_md(items):
    """docs/benchmarks/BORROW-QUEUE.md's full text: the flow's law in two
    sentences, the standing RESEARCH/DESIGN/BUILD/RE-MEASURE loop for
    whichever future session picks an item up, and one table row per item,
    sorted by criterion id for a deterministic, rerunnable file."""
    lines = []
    lines.append('# The borrow queue')
    lines.append('')
    lines.append(
        'Status: generated by `scripts/benchmark_atomic.py` on every run, '
        'never hand edited. See `docs/benchmarks/ATOMIC-BENCHMARK.md` for '
        'the checks that decide every cell below.')
    lines.append('')
    lines.append(
        'THE LAW, in two sentences: a borrow item closes only when the '
        're-run benchmark cell itself flips to brother PASS, which this '
        "script detects from the run data, never from anyone's say-so. "
        'Claiming a borrow without the flipped cell to show for it is the '
        'confidence laundering this estate refuses.')
    lines.append('')
    lines.append(
        'THE STANDING LOOP, for whichever future session picks an open item '
        "up: RESEARCH the leader's shipped mechanism at the pinned sha (or "
        "citation) named in its row; DESIGN Brother's own version, an idea "
        'borrowed and owned, never a copy, checking the license of anything '
        'reused beyond the idea itself; BUILD it in the owning product '
        'repository; RE-MEASURE by re-running `python3 '
        'scripts/benchmark_atomic.py`; only the flipped cell closes the '
        'item, which this script then marks BEATEN on its own, automatically.')
    lines.append('')
    if not items:
        lines.append(
            'No open or closed borrow items in this run: brother has no '
            'losing cell against any scored subject right now.')
        lines.append('')
        return '\n'.join(lines)
    ordered = sorted(items, key=lambda it: it['criterion'])
    lines.append(
        '| id | criterion | dimension | leader(s) | what they ship | '
        'where to research | proposed Brother move | stage |')
    lines.append('|---|---|---|---|---|---|---|---|')
    for item in ordered:
        leaders_str = ', '.join(item['leaders']) if item['leaders'] else '(no leader recorded)'
        if item['leader_detail']:
            ships = '; '.join('%s: %s' % (d['subject'], d['evidence']) for d in item['leader_detail'])
            research = '; '.join('%s: %s' % (d['subject'], d['research']) for d in item['leader_detail'])
        else:
            ships = research = '(no leader recorded; this item is closed, see stage)'
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s |' % (
            item['id'], item['criterion'], item['dimension'], leaders_str,
            md_escape(ships), md_escape(research), md_escape(item['proposed_move']),
            item['stage']))
    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------- #
# Self test: proves the harness can PASS, FAIL, and NO-DATA, against tiny
# embedded fixtures, not against the real subjects above.
# ---------------------------------------------------------------------- #

def run_selftest():
    ok = [True]
    checks = dict((c.id, c) for c in CRITERIA)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pass_dir = tmp_path / 'pass_fixture'
        fail_dir = tmp_path / 'fail_fixture'
        empty_dir = tmp_path / 'empty_fixture'
        pass_dir.mkdir()
        fail_dir.mkdir()
        empty_dir.mkdir()

        (pass_dir / 'README.md').write_text(
            "# Fixture\n\n"
            "## Install\n\n"
            "```\ngit --version\n```\n\n"
            "## Quickstart\n\n"
            "Run the one command above and it works.\n\n"
            "This tool writes a rerunnable receipt for every run.\n\n"
            "## Verify\n\n"
            "```\npython3 --check-install  # runs the fixture test suite\n```\n",
            encoding='utf-8')

        (fail_dir / 'README.md').write_text(
            "# Fixture\n\n"
            "## Install\n\n"
            "```\nstep-one --init\nstep-two --configure\nstep-three --run\n```\n\n"
            "## Quickstart\n\n"
            "The first run reports FAIL until you configure it.\n",
            encoding='utf-8')

        def make_subject(name, root):
            readme = root / 'README.md'
            return Subject(name, [root], readme if readme.exists() else None, [str(root)])

        pass_subject = make_subject('selftest-pass', pass_dir)
        fail_subject = make_subject('selftest-fail', fail_dir)
        empty_subject = make_subject('selftest-empty', empty_dir)

        def expect(criterion_id, subject, expected):
            v, ev = checks[criterion_id].check(subject)
            if v != expected:
                print('selftest: expected %s on %s to be %s, got %s (%s)' % (
                    criterion_id, subject.name, expected, v, ev))
                ok[0] = False

        for criterion_id in ('install-commands-documented', 'fail-states-on-first-run', 'receipt-artifact-exists'):
            expect(criterion_id, pass_subject, 'PASS')
            expect(criterion_id, fail_subject, 'FAIL')
            expect(criterion_id, empty_subject, 'NO-DATA')

    return ok[0]


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='emit machine-readable JSON instead of text lines')
    parser.add_argument('--selftest', action='store_true',
                         help='run the built-in fixtures proving PASS, FAIL and NO-DATA are all reachable, then exit')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.selftest:
        ok = run_selftest()
        if ok:
            print('benchmark_atomic.py selftest: PASS')
            return 0
        print('benchmark_atomic.py selftest: FAIL', file=sys.stderr)
        return 1

    subjects = build_all_subjects()
    results = score_all(subjects, CRITERIA)

    scores = compute_scores(subjects, CRITERIA, results)
    reasons = compute_reasons(subjects, CRITERIA, results)
    stage_overrides = load_stage_overrides()
    borrow_items = compute_borrow_items(subjects, CRITERIA, results, stage_overrides)
    borrow_md = render_borrow_queue_md(borrow_items)
    try:
        BORROW_QUEUE_PATH.write_text(borrow_md, encoding='utf-8')
    except OSError as exc:
        print('WARNING: could not write %s: %s' % (BORROW_QUEUE_PATH, exc), file=sys.stderr)

    if args.json:
        out = []
        for c in CRITERIA:
            for s in subjects:
                verdict, evidence = results[(c.id, s.name)]
                out.append({
                    'criterion': c.id, 'dimension': c.dimension,
                    'subject': s.name, 'verdict': verdict, 'evidence': evidence,
                })
        payload = {
            'results': out,
            'scores': scores,
            'reasons': reasons,
            'borrow_items': borrow_items,
        }
        print(json.dumps(payload, indent=2))
        scored_total = sum(1 for r in out if r['verdict'] != 'NO-DATA')
    else:
        scored_total = print_report(subjects, CRITERIA, results)
        print_scored_summary(scores, reasons)
        print('%d borrow item(s) written to %s' % (len(borrow_items), BORROW_QUEUE_PATH))

    return 0 if scored_total > 0 else 2


if __name__ == '__main__':
    sys.exit(main())
