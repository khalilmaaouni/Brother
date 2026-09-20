#!/usr/bin/env python3
"""Measure the user-invocable surface and generate bundle/MANIFEST.json.

The architecture of record withdrew numeric caps on 2026-08-22. The default
command inventories the current repository and its product trees. Explicit
ceiling calibration remains available through verdict() for callers that
supply a boundary. Missing trees are NO-DATA, never counted as zero.

Only --manifest --write writes the generated manifest. Default inspection
is read-only. Commands and user-invocable skills count; dot directories and
nested product trees are excluded to prevent double counting.
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Historical calibration boundary, not the default inventory policy.
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

# All products are measured from this repository.
REPOS = [
    ('Brother', REPO_ROOT),
    ('BrotherModeUp', os.path.join(REPO_ROOT, 'products', 'brothermode')),
    ('BrotherSBE', os.path.join(REPO_ROOT, 'products', 'brothersbe')),
    ('BrotherDS', os.path.join(REPO_ROOT, 'products', 'brotherds')),
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


# WBS-70.01 names five things a surface budget should measure. The
# inventory above already counts two of them (commands = "user-visible
# advanced routes", skills = "automatically discoverable skills"). These
# two close two more of the remaining three with real counting logic
# against the actual files, not invented numbers. "activated context" (what
# gets auto-loaded into a session versus what a person must type) has no
# settled operational definition in this repo yet and is left as an honest
# gap rather than guessed at.

#: Per-product command/skill prefixes the umbrella's own bundle uses to
#: expose each product's routes under one door (bundle/commands,
#: bundle/skills). Stripping them is how a route exposed twice, once under
#: its product's own name and once under the umbrella's prefixed name, is
#: recognised as the same route rather than two different ones.
KNOWN_ROUTE_PREFIXES = ('brotherme-', 'brothermode-', 'brothersbe-')


def _frontmatter_bytes(path):
    """Bytes of the YAML frontmatter block (the `---`-delimited header)
    of one command or skill file: this is the name/description text the
    harness renders into every session's startup tool listing, not the
    file's whole body which loads only if the entry is actually invoked.
    A file with no frontmatter, or an unterminated one, contributes 0."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return 0
    if not lines or lines[0].strip() != '---':
        return 0
    total = len(lines[0].encode('utf-8'))
    for line in lines[1:]:
        total += len(line.encode('utf-8'))
        if line.strip() == '---':
            return total
    return 0


def count_startup_metadata_footprint(repo_root):
    """WBS-70.01 "startup metadata footprint": total bytes of frontmatter
    across every user-invocable command and skill in one repo, i.e. the
    text actually rendered into a session's startup listing for this
    tree. Returns (footprint_bytes, file_count)."""
    footprint = 0
    file_count = 0
    for commands_dir in sorted(_find_named_dirs(repo_root, 'commands')):
        for entry in sorted(os.listdir(commands_dir)):
            full = os.path.join(commands_dir, entry)
            if entry.endswith('.md') and os.path.isfile(full):
                footprint += _frontmatter_bytes(full)
                file_count += 1
    for skills_dir in sorted(_find_named_dirs(repo_root, 'skills')):
        for entry in sorted(os.listdir(skills_dir)):
            skill_md = os.path.join(skills_dir, entry, 'SKILL.md')
            if os.path.isfile(skill_md) and _is_user_invocable(skill_md):
                footprint += _frontmatter_bytes(skill_md)
                file_count += 1
    return footprint, file_count


def _normalize_route_name(name):
    """Strips one known per-product prefix so 'brotherme-status' and
    'status' compare equal. Only one prefix is ever stripped: these
    prefixes do not nest."""
    for prefix in KNOWN_ROUTE_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def count_semantic_duplicate_routes(repo_root):
    """WBS-70.01 "semantic duplicate routes": entries in one repo whose
    name collides with another entry's name once the known per-product
    prefix is stripped, e.g. 'brotherme-status' and 'brothermode-status'
    and 'brothersbe-status' are the same route exposed three times under
    different product namespaces, exactly the cognitive-compression
    target this subsection names. Returns (duplicate_count, groups):
    duplicate_count is every entry beyond the first in each colliding
    group; groups maps the normalized name to every raw name that
    produced it, restricted to groups with more than one member."""
    names = repo_entry_names(repo_root)
    by_norm = {}
    for name in names:
        by_norm.setdefault(_normalize_route_name(name), []).append(name)
    groups = {k: v for k, v in sorted(by_norm.items()) if len(v) > 1}
    duplicate_count = sum(len(v) - 1 for v in groups.values())
    return duplicate_count, groups


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
    'brotherds': 'BrotherDS',
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


def inventory_verdict(total, missing):
    if missing:
        return 2, 'NO-DATA: could not measure %s' % ', '.join(missing)
    return 0, 'PASS: measured %d user-invocable entries' % total


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
        return 0

    total, missing, lines = compute_total(REPOS)
    for line in lines:
        print(line)
    print('TOTAL: %d (numeric surface cap retired)' % total)

    footprint_total = 0
    duplicate_total = 0
    for name, path in REPOS:
        if not os.path.isdir(path):
            continue
        footprint, file_count = count_startup_metadata_footprint(path)
        footprint_total += footprint
        print('%s: startup metadata footprint %d bytes across %d files'
              % (name, footprint, file_count))
        duplicates, groups = count_semantic_duplicate_routes(path)
        duplicate_total += duplicates
        if groups:
            group_desc = '; '.join(
                '%s <- %s' % (norm, ', '.join(raw)) for norm, raw in groups.items())
            print('%s: semantic duplicate routes %d (%s)'
                  % (name, duplicates, group_desc))
        else:
            print('%s: semantic duplicate routes 0' % name)
    print('STARTUP METADATA FOOTPRINT TOTAL: %d bytes' % footprint_total)
    print('SEMANTIC DUPLICATE ROUTES TOTAL: %d' % duplicate_total)

    code, message = inventory_verdict(total, missing)
    print(message)
    return code


if __name__ == '__main__':
    sys.exit(main())
