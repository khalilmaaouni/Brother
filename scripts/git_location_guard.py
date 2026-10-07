'''git_location_guard.py: name, remove and refuse the git location variables,
and read a repository own config without letting those variables steer it.
'''
import os
import re
import sys
import time
from collections.abc import MutableMapping
from typing import Dict, List, Optional, Set, Tuple

SCHEMA = 'git-location-guard/live-config/v1'
PASS = 'PASS'
FAIL = 'FAIL'
NODATA = 'NO-DATA'
DEFAULT_WHERE = 'git_location_guard.main'

GIT_LOCATION_VARS = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_PREFIX',
    'GIT_NAMESPACE', 'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT',
    'GIT_IMPLICIT_WORK_TREE', 'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE',
    'GIT_INTERNAL_SUPER_PREFIX', 'GIT_REPLACE_REF_BASE', 'GIT_NO_REPLACE_OBJECTS')

FIXTURE_EMAIL = re.compile(r'^(a@b\.c|[^@]+@example\.(com|org|invalid)|[^@]+@[^@]*\.invalid)$', re.I)

class GitLocationError(RuntimeError):
    '''A git location variable was present where none is allowed.'''

class GitLocationInputError(ValueError):
    '''A caller handed this module a value it cannot mean.'''

def _describe(value):
    if isinstance(value, str):
        return repr(value)
    if value is None or isinstance(value, (int, float, bool)):
        return '%s(%r)' % (type(value).__name__, value)
    return type(value).__name__

def _no_arguments(name, args, kwargs):
    if args or kwargs:
        raise GitLocationInputError('%s takes no arguments; got %d positional and %d keyword argument(s)' % (name, len(args), len(kwargs)))

def git_location_vars(*args, **kwargs):
    _no_arguments('git_location_vars', args, kwargs)
    return GIT_LOCATION_VARS

def _environment(env):
    if env is None:
        return os.environ
    if not isinstance(env, MutableMapping):
        raise GitLocationInputError('env must be a mutable mapping of environment names to values, or None for this process own environment; got %s' % _describe(env))
    try:
        names = list(env)
    except TypeError as exc:
        raise GitLocationInputError('env cannot be iterated: %s' % exc)
    except Exception as exc:
        raise GitLocationInputError('env cannot be read: %s' % exc)
    for name in names:
        if not isinstance(name, str):
            raise GitLocationInputError('env holds a name that is not a string: %s' % _describe(name))
    return env

def _present_names(target):
    present = []
    for name in GIT_LOCATION_VARS:
        try:
            if name in target:
                present.append(name)
        except TypeError as exc:
            raise GitLocationInputError('env refuses a membership test for %s: %s' % (name, exc))
        except Exception as exc:
            raise GitLocationInputError('env cannot be read for %s: %s' % (name, exc))
    return present

def _pop_name(target, name):
    try:
        target.pop(name, None)
    except TypeError as exc:
        raise GitLocationInputError('env refuses to remove %s: %s' % (name, exc))
    except Exception as exc:
        raise GitLocationInputError('env cannot remove %s: %s' % (name, exc))

def drop_git_location(env=None):
    target = _environment(env)
    removed = _present_names(target)
    for name in removed:
        _pop_name(target, name)
    return sorted(removed)

def scrubbed_env(*args, **kwargs):
    _no_arguments('scrubbed_env', args, kwargs)
    return {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}

def _named(where):
    return isinstance(where, str) and bool(where.strip())

def _where_text(where):
    return where.strip() if _named(where) else 'UNKNOWN'

def assert_no_git_location(env=None, *, where=None):
    target = _environment(env)
    present = _present_names(target)
    if present:
        raise GitLocationError(
            'git location variable(s) present at %s: %s; git reads or writes the repository these name (git exports them to its hooks), never the one the caller means. Remove them first with scripts/tmp_sandbox.drop_git_location(), or launch through scripts/required_fast.sh, which unsets the same list.'
            % (_where_text(where), ', '.join(present)))
    if not _named(where):
        raise GitLocationInputError('assert_no_git_location requires where=<caller> so the refusal can name the launch that hit it; got %s' % _describe(where))
    return None

def _require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GitLocationInputError('%s must be a non-empty string; got %s' % (label, _describe(value)))
    return value

def _require_fixture_emails(fixture_emails):
    if not isinstance(fixture_emails, (set, frozenset)):
        raise GitLocationInputError('fixture_emails must be a set of literal identities; got %s' % _describe(fixture_emails))
    for address in fixture_emails:
        if not isinstance(address, str) or not address.strip():
            raise GitLocationInputError('fixture_emails holds a name that is not a non-empty string: %s' % _describe(address))
    return fixture_emails

def _read_bytes(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read(), None
    except (OSError, ValueError) as exc:
        return None, 'cannot read %s: %s' % (path, exc)

def _decode_utf8(raw, path):
    try:
        return raw.decode('utf-8'), None
    except UnicodeDecodeError as exc:
        return None, '%s is not valid UTF-8: %s' % (path, exc)

def _locate_config(root):
    dotgit = os.path.join(root, '.git')
    if os.path.isdir(dotgit):
        gitdir = dotgit
    elif os.path.isfile(dotgit):
        raw, problem = _read_bytes(dotgit)
        if raw is None:
            return None, problem
        text, problem = _decode_utf8(raw, dotgit)
        if text is None:
            return None, problem
        pointer = text.strip()
        if not pointer.lower().startswith('gitdir:'):
            return None, ('%s is not a git directory pointer (expected gitdir: <path>) ' % dotgit)
        target = pointer.split(':', 1)[1].strip()
        if not target:
            return None, '%s names no git directory' % dotgit
        gitdir = target if os.path.isabs(target) else os.path.normpath(os.path.join(root, target))
    elif (os.path.isdir(os.path.join(root, 'objects')) and os.path.isfile(os.path.join(root, 'HEAD'))):
        gitdir = root
    else:
        return None, ('%s carries no .git directory and is not itself a git directory, so its config cannot be located' % root)
    common = gitdir
    commondir = os.path.join(gitdir, 'commondir')
    if os.path.isfile(commondir):
        raw, problem = _read_bytes(commondir)
        if raw is None:
            return None, problem
        text, problem = _decode_utf8(raw, commondir)
        if text is None:
            return None, problem
        pointer = text.strip()
        if not pointer:
            return None, ('%s is empty, so the shared git directory cannot be located' % commondir)
        common = pointer if os.path.isabs(pointer) else os.path.normpath(os.path.join(gitdir, pointer))
    config = os.path.join(common, 'config')
    if not os.path.isfile(config):
        return None, '%s carries no config file' % common
    return config, None

def _strip_inline_comment(value):
    best = None
    for marker in (' #', ' ;', '\t#', '\t;'):
        index = value.find(marker)
        if index != -1 and (best is None or index < best):
            best = index
    if best is None:
        return value
    return value[:best]

def _unquote(value):
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        inner = value[1:-1]
        out = []
        index = 0
        while index < len(inner):
            char = inner[index]
            if char == '\\' and index + 1 < len(inner):
                index += 1
                out.append(inner[index])
            else:
                out.append(char)
            index += 1
        return ''.join(out)
    return value

def _parse_config(text):
    values = {}
    problems = []
    section = None
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line[0] in '#;':
            continue
        if line.startswith('['):
            if not line.endswith(']'):
                problems.append('line %d: %r is not a closed section header' % (number, raw_line))
                section = None
                continue
            inner = line[1:-1].strip()
            if not inner:
                problems.append('line %d: %r names an empty section' % (number, raw_line))
                section = None
                continue
            section = inner.split(None, 1)[0].lower()
            continue
        if section is None:
            problems.append('line %d: %r appears before any section header' % (number, raw_line))
            continue
        if '=' in line:
            name, _separator, value = line.partition('=')
        else:
            name, value = line, ''
        name = name.strip().lower()
        if not name:
            problems.append('line %d: %r names no key' % (number, raw_line))
            continue
        value = value.strip()
        if not (len(value) >= 2 and value.startswith('"') and value.endswith('"')):
            value = _strip_inline_comment(value).strip()
        values[section + '.' + name] = _unquote(value)
    return values, problems

def _git_bool(value):
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in ('', 'true', 'yes', 'on', '1'):
        return True
    if text in ('false', 'no', 'off', '0'):
        return False
    return None

def _hooks_path_exists(root, config_path, hooks_path):
    bases = [root]
    common = os.path.dirname(os.path.abspath(config_path))
    if common not in bases:
        bases.append(common)
    for base in bases:
        candidate = hooks_path if os.path.isabs(hooks_path) else os.path.join(base, hooks_path)
        if os.path.isdir(candidate):
            return True
    return False

def scan_repo_config(repo_root, fixture_emails):
    root = _require_text(repo_root, 'repo_root')
    _require_fixture_emails(fixture_emails)
    scan = {
        'schema': SCHEMA,
        'repo_root': root,
        'config_path': None,
        'read_ok': False,
        'problem': None,
        'core_bare': None,
        'hooks_path': None,
        'hooks_path_exists': None,
        'user_email': None,
        'unreadable': [],
        'checked_unix': time.time(),
    }
    path, problem = _locate_config(root)
    scan['config_path'] = path
    if path is None:
        scan['problem'] = problem
        return scan
    raw, problem = _read_bytes(path)
    if raw is None:
        scan['problem'] = problem
        return scan
    text, problem = _decode_utf8(raw, path)
    if text is None:
        scan['problem'] = problem
        return scan
    values, problems = _parse_config(text)
    scan['read_ok'] = True
    scan['unreadable'] = problems
    bare_raw = values.get('core.bare')
    if bare_raw is not None:
        parsed = _git_bool(bare_raw)
        if parsed is None:
            scan['unreadable'].append('core.bare = %r is not a git boolean' % bare_raw)
        else:
            scan['core_bare'] = parsed
    hooks_raw = values.get('core.hookspath')
    if hooks_raw:
        scan['hooks_path'] = hooks_raw
        scan['hooks_path_exists'] = _hooks_path_exists(root, path, hooks_raw)
    email = values.get('user.email')
    if email:
        scan['user_email'] = email
    return scan

def _require_address_set(name, addresses):
    '''Normalize a fixture identity collection; refuse anything that is not a
    collection of address strings (None, a bare string, an unhashable member,
    a wrong type) with ValueError, never a raw interpreter exception.'''
    if addresses is None or isinstance(addresses, (str, bytes, bytearray)):
        raise ValueError('%s must be a set or sequence of address strings; got %s' % (name, _describe(addresses)))
    try:
        members = list(addresses)
    except TypeError:
        raise ValueError('%s must be a set or sequence of address strings; got %s' % (name, _describe(addresses)))
    wanted = set()
    for member in members:
        if not isinstance(member, str):
            raise ValueError('%s holds a member that is not an address string: %s' % (name, _describe(member)))
        wanted.add(member.strip().lower())
    return wanted

def _hooks_path_inside(repo_root, hooks_path):
    '''True when core.hooksPath, resolved the way git resolves it, stays
    inside repo_root. Hostile input is refused with False, never a crash.'''
    if not isinstance(hooks_path, str) or not hooks_path:
        return False
    if not isinstance(repo_root, str) or not repo_root:
        return False
    target = hooks_path
    if not os.path.isabs(target):
        target = os.path.join(repo_root, target)
    try:
        resolved = os.path.realpath(target)
        root = os.path.realpath(repo_root)
    except (OSError, ValueError):
        return False
    if resolved == root:
        return True
    return resolved.startswith(root + os.sep)

def live_config_verdict(repo_root, fixture_emails):
    wanted = _require_address_set('fixture_emails', fixture_emails)
    root = _require_text(repo_root, 'repo_root')
    scan = scan_repo_config(root, wanted)
    reasons = []
    if scan['core_bare'] is True:
        reasons.append('core.bare is true at %s: git treats it as a bare repository, so every worktree and every hook that expects a work tree refuses' % scan['repo_root'])
    hooks_path = scan['hooks_path']
    if hooks_path is not None and scan['hooks_path_exists'] is False:
        reasons.append('core.hooksPath is %r and no such directory exists: every hook git would run is dead' % hooks_path)
    if hooks_path is not None and scan['hooks_path_exists'] is True and not _hooks_path_inside(scan['repo_root'], hooks_path):
        reasons.append('core.hooksPath is %r, outside %s: git would run hooks from another location than the repository being checked' % (hooks_path, scan['repo_root']))
    user_email = scan['user_email']
    match = False
    if user_email:
        identity = user_email.strip()
        if identity.lower() in wanted or FIXTURE_EMAIL.match(identity):
            match = True
            reasons.append('user.email is %r, a fixture identity a real operator never has' % user_email)
    unreadable = []
    if not scan['read_ok']:
        unreadable.append(scan['problem'] or ('%s could not be read' % (scan['config_path'] or scan['repo_root'])))
    unreadable.extend(scan['unreadable'])
    if reasons:
        verdict = FAIL
    elif unreadable:
        verdict = NODATA
    else:
        verdict = PASS
    return {
        'schema': SCHEMA,
        'repo_root': scan['repo_root'],
        'checked_unix': scan['checked_unix'],
        'verdict': verdict,
        'core_bare': scan['core_bare'],
        'hooks_path': scan['hooks_path'],
        'user_email': scan['user_email'],
        'fixture_email_match': match,
        'reasons': reasons,
        'evidence': scan,
    }

_OPTION_KEYS = {
    '--where': 'where',
    '--repo-root': 'repo_root',
    '--fixture-email': 'fixture_email',
}

def _parse_argv(argv):
    options = {
        'assert_clean': False,
        'check_live_config': False,
        'where': None,
        'repo_root': None,
        'fixture_emails': set(),
    }
    seen = set()
    index = 0
    while index < len(argv):
        token = argv[index]
        index += 1
        if token in ('--assert-clean', '--check-live-config'):
            if token in seen:
                return None, 'option %s is given twice' % token
            seen.add(token)
            options['assert_clean' if token == '--assert-clean' else 'check_live_config'] = True
            continue
        name, separator, inline = token.partition('=')
        if name not in _OPTION_KEYS:
            return None, 'unknown option %r' % token
        if name != '--fixture-email':
            if name in seen:
                return None, 'option %s is given twice' % name
            seen.add(name)
        if separator:
            value = inline
        else:
            if index >= len(argv):
                return None, 'option %s needs a value' % name
            value = argv[index]
            index += 1
        if not value.strip():
            return None, 'option %s needs a non-empty value' % name
        if name == '--fixture-email':
            options['fixture_emails'].add(value)
        else:
            options[_OPTION_KEYS[name]] = value
    return options, None

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if isinstance(argv, str) or not isinstance(argv, (list, tuple)):
        sys.stderr.write('NO-DATA: argv must be a list of option strings\n')
        return 2
    for token in argv:
        if not isinstance(token, str):
            sys.stderr.write('NO-DATA: argv holds an option that is not a string: %s\n' % _describe(token))
            return 2
    options, problem = _parse_argv(list(argv))
    if options is None:
        sys.stderr.write('NO-DATA: %s\n' % problem)
        return 2
    if options['assert_clean']:
        where = options['where'] or DEFAULT_WHERE
        try:
            assert_no_git_location(os.environ, where=where)
        except RuntimeError as exc:
            sys.stderr.write('NO-DATA: %s\n' % exc)
            return 2
    if options['check_live_config']:
        if options['repo_root'] is not None:
            repo_root = options['repo_root']
        else:
            try:
                repo_root = os.getcwd()
            except OSError as exc:
                sys.stderr.write('NO-DATA: the current directory cannot be read: %s\n' % exc)
                return 2
        try:
            verdict = live_config_verdict(repo_root, options['fixture_emails'])
        except ValueError as exc:
            sys.stderr.write('NO-DATA: %s\n' % exc)
            return 2
        code = {'PASS': 0, 'FAIL': 1, 'NO-DATA': 2}.get(verdict['verdict'], 2)
        if code == 0:
            print('OK: %s carries no git location damage' % verdict['repo_root'])
        else:
            print('%s: %s' % (verdict['verdict'], verdict['repo_root']))
            for line in verdict['reasons']:
                print('  %s' % line)
            for line in verdict['evidence']['unreadable']:
                print('  NO-DATA: %s' % line)
            if not verdict['evidence']['read_ok']:
                print('  NO-DATA: %s' % verdict['evidence']['problem'])
        return code
    if options['assert_clean']:
        print('OK: no git location variable is present at %s' % (options['where'] or DEFAULT_WHERE))
        return 0
    sys.stderr.write('NO-DATA: choose --assert-clean (refuse a live location variable) or --check-live-config (read one repository own config); running neither proves nothing\n')
    return 2
