#!/usr/bin/env python3
"""ROW R10: a ceiling on the estate's whole user-invocable surface.

WHAT "USER-INVOCABLE SURFACE" MEANS HERE, and only this: something a person
can type. Two shapes count:

  1. A command file, `<any dir named commands>/*.md`. Always typeable, no
     frontmatter check, per the counting rule this row was briefed against.
  2. A skill, `<any dir named skills>/<name>/SKILL.md`, UNLESS its frontmatter
     carries `user-invocable: false`. A skill carrying
     `disable-model-invocation: true` is still counted: that flag only stops
     the skill firing itself, a person can still type it.

WHY A CEILING, NOT A COUNT PUBLISHED IN PROSE. This estate has been burned
repeatedly by counts that were never re-derivable and drifted the moment
nobody looked: 51 was published once, and it double counted 4 BrotherModeUp
skills marked `user-invocable: false` (nobody can type them) plus read
BrotherSBE from a checkout six commits stale where the count was already 14
rather than 16. A number that only a prose sentence carries cannot be
re-checked; a script that recomputes it from the files on disk, every run,
can.

THE CEILING ITSELF IS A RATCHET, not a target. It is set to TODAY'S measured
total so this check is GREEN on arrival and turns RED only if the surface
grows past what it is right now. Lowering it is a different, deliberate row.

MEASURED 2026-08-29, four repositories, each counted the way this file
counts (commands dirs found at any depth, skills dirs found at any depth,
both excluding dot-directories so a `.git` object store or a
`.claude/worktrees` full checkout is never walked into and double counted):

    Brother          2   (1 command + 1 skill: bundle/commands/brother.md,
                           bundle/skills/using-brother/SKILL.md)
    BrotherModeUp   30   (15 commands + 15 typeable skills, 4 of 19 skills
                           excluded for user-invocable: false, 7 of the 15
                           counted skills carry disable-model-invocation:
                           true and are still counted)
    BrotherSBE      14   (0 commands + 14 typeable skills, none excluded)
    BrotherDS        1   (0 commands + 1 typeable skill, not excluded)
    TOTAL           47

NOTE ON THE BRIEF THAT ASKED FOR THIS CHECK: it stated the headline total as
46, but its own per-repository breakdown (1 door + 1 skill, 30, 14, 1) sums
to 47, not 46, and this script's independent walk of the actual files on
this machine also lands on 47. The brief itself said to verify rather than
trust it, so the ceiling below is set to the number this script can prove,
47, and this paragraph records the mismatch rather than silently absorbing
it into a number nobody could re-derive. THE CEILING IS 47.

Where a repository is absent on this machine, that repository is reported as
NO-DATA by name. NO-DATA is never folded in as zero and never counted as a
pass: a run that cannot see every repository cannot certify the total is
under the ceiling, so it exits 2, not 0.

Exit 0  every repository was measured and the total is at or under the
        ceiling.
Exit 1  every repository was measured and the total exceeds the ceiling.
Exit 2  NO-DATA: at least one repository could not be found on this machine.

Python 3.9 floor, standard library only, no network.

origin: a human, or a session acting for one, running this script's own CLI
directly with both `--manifest` and `--write`. The plain check (as
scripts/check_all.sh:306 runs it, `python3 scripts/surface_budget.py` with no
flags) only prints the ceiling verdict and never writes; the write branch at
main()'s `if '--write' in args:` guard (below) only fires under that explicit
flag combination. Confirmed by grep: no file in scripts or bundle/runtime
invokes `surface_budget.py --manifest` or passes `--write` to it (searched
for both strings; only scripts/test_surface_budget.py imports this module,
and it does so to call build_manifest() directly in a test, never through
main() with --write).

PRODUCER: this module is the sole producer of bundle/MANIFEST.json. The write
happens inside main(), a few lines below build_manifest(), at the `with
open(MANIFEST_PATH, 'w', encoding='utf-8') as fh: json.dump(manifest, fh,
...)` call (lines 292-294 of this file).
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The ceiling: today's measured total, held flat. See the docstring above for
# the per-repository breakdown and why this is 47, not the 46 once published.
CEILING = 47

SKIP_DIRS = {
    '.git', '.claude', '.brothermode', '.brothersbe', '.sbe',
    'node_modules', '__pycache__', '.venv', 'venv',
    # One-repo transition (M2, 2026-08-31): consolidated products live at
    # products/<name> as SOURCE, not as the umbrella's served surface. Their
    # own surfaces are still counted per product from the repos their plugins
    # actually ship from, until the M6 cutover release recounts the unified
    # surface deliberately. Without this prune the DS subtree's internal
    # skills pushed the umbrella count over its ceiling for skills no user
    # can invoke from here.
    'products',
}

# name -> path. Brother is this checkout; the other three are sibling
# checkouts at their documented canonical paths (see each project's
# PROJECT.md / CLAUDE.md for the path this estate treats as canonical).
REPOS = [
    ('Brother', REPO_ROOT),
    ('BrotherModeUp', os.path.expanduser('~/Documents/BrotherModeUp')),
    ('BrotherSBE', os.path.expanduser('~/Documents/BrotherSBE')),
    ('BrotherDS', os.path.expanduser('~/Documents/BrotherDS')),
]


def _find_named_dirs(root, name):
    """Every directory literally named `name` under root, dot-directories
    and known scratch/vendor directories pruned so a `.git` object store or
    a `.claude/worktrees` full checkout is never walked into."""
    found = []
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                        if d not in SKIP_DIRS and not d.startswith('.')]
        if os.path.basename(dirpath) == name:
            found.append(dirpath)
    return found


def _is_user_invocable(skill_md_path):
    """False only when the frontmatter says so explicitly. Absence of the
    key, an unreadable file, or no frontmatter at all all default to True,
    matching the counting rule: a skill is typeable unless it says it isn't."""
    try:
        with open(skill_md_path, 'r', encoding='utf-8') as f:
            first = f.readline()
            if first.strip() != '---':
                return True
            for line in f:
                stripped = line.strip()
                if stripped == '---':
                    return True
                if stripped.lower().startswith('user-invocable:'):
                    value = stripped.split(':', 1)[1].strip().strip('"\'').lower()
                    return value != 'false'
        return True
    except OSError:
        return True


def count_repo_surface(repo_root):
    """Returns (commands_count, skills_count, detail_lines) for one repo
    root. detail_lines names exactly what was counted and what was skipped,
    so the total can be re-derived from the printed output alone."""
    detail = []

    command_files = []
    for commands_dir in sorted(_find_named_dirs(repo_root, 'commands')):
        for entry in sorted(os.listdir(commands_dir)):
            full = os.path.join(commands_dir, entry)
            if entry.endswith('.md') and os.path.isfile(full):
                command_files.append(full)
        detail.append('  commands dir: %s' % commands_dir)

    skill_names = []
    excluded_names = []
    for skills_dir in sorted(_find_named_dirs(repo_root, 'skills')):
        detail.append('  skills dir: %s' % skills_dir)
        for entry in sorted(os.listdir(skills_dir)):
            skill_md = os.path.join(skills_dir, entry, 'SKILL.md')
            if not os.path.isfile(skill_md):
                continue
            if _is_user_invocable(skill_md):
                skill_names.append(entry)
            else:
                excluded_names.append(entry)

    detail.append('  commands counted (%d): %s'
                   % (len(command_files),
                      ', '.join(os.path.basename(c) for c in command_files) or '(none)'))
    detail.append('  skills counted (%d): %s'
                   % (len(skill_names), ', '.join(skill_names) or '(none)'))
    if excluded_names:
        detail.append('  skills excluded, user-invocable: false (%d): %s'
                       % (len(excluded_names), ', '.join(excluded_names)))

    return len(command_files), len(skill_names), detail


def compute_total(repos):
    """repos: list of (name, path). Returns (total, missing_names, lines)."""
    total = 0
    missing = []
    lines = []
    for name, path in repos:
        if not os.path.isdir(path):
            missing.append(name)
            lines.append('%s: NO-DATA, repository not found at %s' % (name, path))
            continue
        commands, skills, detail = count_repo_surface(path)
        subtotal = commands + skills
        total += subtotal
        lines.append('%s: %d user-invocable (%d commands + %d typeable skills)'
                      % (name, subtotal, commands, skills))
        lines.extend(detail)
    return total, missing, lines



# ---------------------------------------------------------------------------
# R11.1: what ONE INSTALL must produce.
#
# The ceiling above counts the surface across FOUR repositories. The umbrella
# ships THREE: measured 2026-08-29 from .claude-plugin/marketplace.json, it
# names brothermode, brothersbe and brother, and does not name BrotherDS. So
# the ceiling counts one entry that no install has ever delivered, and R11's
# second clause ("the installed surface matches the target count") could not be
# checked against the ceiling even in principle.
#
# The manifest is therefore derived from the marketplace file rather than from
# a constant, so it cannot drift away from what is actually shipped: add a
# plugin to the umbrella and the manifest grows, remove one and it shrinks, and
# neither needs anyone to remember this comment.
# ---------------------------------------------------------------------------

#: Which repository backs each plugin the umbrella ships. The umbrella names
#: plugins; the surface is measured in trees; this is the only place the two
#: vocabularies meet.
PLUGIN_REPOS = {
    'brother': 'Brother',
    'brothermode': 'BrotherModeUp',
    'brothersbe': 'BrotherSBE',
}


def shipped_plugins(marketplace_path=None):
    """The plugin names the umbrella actually ships, read from its own file.

    Returns (names, problem). A missing or unreadable marketplace file is
    NO-DATA and never an empty list: an empty list would make the manifest
    trivially satisfiable by installing nothing."""
    path = marketplace_path or os.path.join(REPO_ROOT, '.claude-plugin',
                                            'marketplace.json')
    if not os.path.isfile(path):
        return None, 'no marketplace file at %s' % path
    try:
        with open(path, encoding='utf-8') as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, 'could not read %s: %s' % (path, exc)
    names = [p.get('name') for p in (doc.get('plugins') or []) if p.get('name')]
    if not names:
        return None, 'the marketplace file names no plugins'
    return names, ''


def repo_entry_names(repo_root):
    """Every user-invocable entry name in one tree, as a sorted list.

    Command basenames lose their .md, because that is the name they register
    under, which is the name an install can be checked against."""
    names = []
    for commands_dir in sorted(_find_named_dirs(repo_root, 'commands')):
        for entry in sorted(os.listdir(commands_dir)):
            full = os.path.join(commands_dir, entry)
            if entry.endswith('.md') and os.path.isfile(full):
                names.append(entry[:-3])
    for skills_dir in sorted(_find_named_dirs(repo_root, 'skills')):
        for entry in sorted(os.listdir(skills_dir)):
            skill_md = os.path.join(skills_dir, entry, 'SKILL.md')
            if os.path.isfile(skill_md) and _is_user_invocable(skill_md):
                names.append(entry)
    return sorted(set(names))


def build_manifest(marketplace_path=None, repos=None):
    """What one install must produce. Returns (manifest, problem)."""
    plugins, problem = shipped_plugins(marketplace_path)
    if plugins is None:
        return None, problem
    by_name = dict(repos or REPOS)
    entries, missing = {}, []
    for plugin in sorted(plugins):
        repo_key = PLUGIN_REPOS.get(plugin)
        path = by_name.get(repo_key) if repo_key else None
        if not path or not os.path.isdir(path):
            missing.append(plugin)
            continue
        entries[plugin] = repo_entry_names(path)
    if missing:
        return None, ('no tree found for shipped plugin(s): %s'
                      % ', '.join(missing))
    return {'shipped_plugins': sorted(plugins),
            'entries': entries,
            'total': sum(len(v) for v in entries.values())}, ''


def verdict(total, missing, ceiling):
    """Returns (exit_code, verdict_line). Pure function so the calibration
    test can drive it directly without touching the real repositories."""
    if missing:
        return 2, 'NO-DATA: %d repository could not be measured (%s)' % (
            len(missing), ', '.join(missing))
    if total > ceiling:
        return 1, 'FAIL: surface is %d, ceiling is %d, over by %d' % (
            total, ceiling, total - ceiling)
    return 0, 'PASS: surface is %d, ceiling is %d' % (total, ceiling)


MANIFEST_PATH = os.path.join(REPO_ROOT, 'bundle', 'MANIFEST.json')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if '--manifest' in args:
        manifest, problem = build_manifest()
        if manifest is None:
            print('NO-DATA: %s' % problem, file=sys.stderr)
            return 2
        if '--write' in args:
            with open(MANIFEST_PATH, 'w', encoding='utf-8') as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)
                fh.write('\n')
            print('wrote %s' % MANIFEST_PATH)
        print(json.dumps({'shipped_plugins': manifest['shipped_plugins'],
                          'total': manifest['total']}, sort_keys=True))
        # THE CEILING AND THE MANIFEST ANSWER DIFFERENT QUESTIONS, and saying so
        # here is the whole reason this flag prints them side by side. The
        # ceiling is how much surface EXISTS across every tree; the manifest is
        # how much ONE INSTALL DELIVERS. They differed by exactly one on the day
        # this was written, and that one is a tree the umbrella has never
        # shipped. Reading either number as the other is how a row closes on a
        # count that was never about it.
        if manifest['total'] != CEILING:
            print('NOTE: one install delivers %d, the ceiling counts %d across '
                  'every tree. The difference is surface that exists but is not '
                  'shipped by the umbrella, which is a fact and not a failure.'
                  % (manifest['total'], CEILING))
        return 0

    total, missing, lines = compute_total(REPOS)
    for line in lines:
        print(line)
    print('TOTAL: %d   CEILING: %d' % (total, CEILING))
    code, message = verdict(total, missing, CEILING)
    print(message)
    return code


if __name__ == '__main__':
    sys.exit(main())
