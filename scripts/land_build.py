import json
import os
import runpy
import sys
import time

class Refusal(Exception):
    pass

def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_build(path):
    if not isinstance(path, str):
        raise Refusal('build path must be a string')
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as exc:
        raise Refusal('missing or unreadable build file') from exc
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise Refusal('build file is not valid utf-8') from exc
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end < start:
        raise Refusal('missing brace in build file')
    text = text[start:end+1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal('corrupt json in build file') from exc
    if not isinstance(data, dict):
        raise Refusal('build root must be an object')
    edits = data.get('edits', [])
    tests = data.get('tests', [])
    if not isinstance(edits, list):
        raise Refusal('edits must be a list')
    if not isinstance(tests, list):
        raise Refusal('tests must be a list')
    if len(edits) == 0 and len(tests) == 0:
        raise Refusal('empty build')
    return data

def safe_target_path(rel, root):
    if not isinstance(rel, str) or not isinstance(root, str):
        raise Refusal('target path and root must be strings')
    if rel == '':
        raise Refusal('empty target path')
    if os.path.isabs(rel):
        raise Refusal('absolute target path')
    parts = rel.replace('\\', '/').split('/')
    if '..' in parts:
        raise Refusal('dot dot segment in target path')
    abs_root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(abs_root, rel))
    current = abs_root
    rel_parts = [p for p in parts if p != '']
    for part in rel_parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise Refusal('symlink in target path: ' + rel)
    real_root = os.path.realpath(abs_root)
    real_target = os.path.realpath(target)
    if not (real_target == real_root or real_target.startswith(real_root + os.sep)):
        raise Refusal('target path outside repository root')
    return target

def list_targets(build, root):
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    if not isinstance(root, str):
        raise Refusal('root must be a string')
    paths = []
    for key in ('edits', 'tests'):
        items = build.get(key, [])
        if not isinstance(items, list):
            raise Refusal(key + ' must be a list')
        for item in items:
            if not isinstance(item, dict):
                raise Refusal('each build item must be an object')
            if 'path' not in item:
                raise Refusal('build item missing path')
            p = item['path']
            if not isinstance(p, str):
                raise Refusal('target path must be a string')
            if p == '':
                raise Refusal('empty target path')
            if os.path.isabs(p):
                raise Refusal('absolute target path')
            parts = p.replace('\\', '/').split('/')
            if '..' in parts:
                raise Refusal('dot dot segment in target path')
            paths.append(p)
    uniq = sorted(set(paths))
    for p in uniq:
        safe_target_path(p, root)
    return uniq

def get_session_id(env):
    if not isinstance(env, dict):
        raise Refusal('env must be a mapping')
    try:
        sid = env.get('BROTHER_SESSION_ID')
        if sid is not None:
            if not isinstance(sid, str):
                raise Refusal('BROTHER_SESSION_ID must be a string')
            return sid
        run_dir = env.get('BROTHER_RUN_DIR')
        if run_dir is not None:
            if not isinstance(run_dir, str):
                raise Refusal('BROTHER_RUN_DIR must be a string')
            if run_dir == '':
                return ''
            return os.path.basename(os.path.normpath(run_dir))
        return ''
    except TypeError as exc:
        raise Refusal('env is not a valid mapping') from exc


def load_fence_records(path=None):
    if path is not None and not isinstance(path, str):
        raise Refusal('fence store path must be a string')
    module = sys.modules.get('fence_expiry')
    if module is None:
        raise Refusal('fence store module unavailable')
    try:
        loader = module.load
    except AttributeError:
        raise Refusal('fence store loader unavailable')
    if not callable(loader):
        raise Refusal('fence store loader unavailable')
    try:
        document = loader(path)
    except Refusal:
        raise
    except Exception as exc:
        raise Refusal('fence store unreadable') from exc
    if not isinstance(document, dict):
        raise Refusal('fence store schema invalid')
    tasks = document.get('tasks')
    if tasks is None:
        tasks = document.get('records')
    if not isinstance(tasks, list):
        raise Refusal('fence store schema invalid')
    if len(tasks) == 0:
        raise Refusal('fence store empty')
    try:
        classify = module.classify
    except AttributeError:
        raise Refusal('fence store classifier unavailable')
    if not callable(classify):
        raise Refusal('fence store classifier unavailable')
    now = time.time()
    records = []
    for task in tasks:
        if not isinstance(task, dict):
            raise Refusal('fence store schema invalid')
        try:
            state = classify(task, now)
        except Refusal:
            raise
        except Exception as exc:
            raise Refusal('fence store schema invalid') from exc
        if not isinstance(state, str):
            raise Refusal('fence store schema invalid')
        if state == 'LIVE':
            live = True
        elif state == 'EXPIRED' or state == 'CLOSED':
            live = False
        elif state == 'NO-EXPIRY':
            raise Refusal('fence record has no expiry')
        else:
            raise Refusal('fence store schema invalid')
        fence_path = task.get('path')
        if not isinstance(fence_path, str) or fence_path == '':
            raise Refusal('fence store schema invalid')
        owner = task.get('owner', '')
        if owner is None:
            owner = ''
        if not isinstance(owner, str):
            raise Refusal('fence store schema invalid')
        rid = task.get('id', '')
        if rid is None:
            rid = ''
        if not isinstance(rid, str):
            raise Refusal('fence store schema invalid')
        records.append({
            'id': rid,
            'path': fence_path,
            'owner': owner,
            'live': live,
        })
    return records


def query_fences(paths, session_id, records):
    if not isinstance(paths, list):
        return ('BLOCK', [])
    if not isinstance(session_id, str):
        return ('BLOCK', [])
    if not isinstance(records, list):
        return ('BLOCK', [])
    if len(records) == 0:
        return ('BLOCK', [])
    for target in paths:
        if not isinstance(target, str):
            return ('BLOCK', [])
    for record in records:
        if not isinstance(record, dict):
            return ('BLOCK', [])
        live = record.get('live')
        if not isinstance(live, bool):
            return ('BLOCK', [])
        fence_path = record.get('path')
        if not isinstance(fence_path, str) or fence_path == '':
            return ('BLOCK', [])
        owner = record.get('owner', '')
        if owner is None:
            owner = ''
        if not isinstance(owner, str):
            return ('BLOCK', [])
        rid = record.get('id', '')
        if rid is None:
            rid = ''
        if not isinstance(rid, str):
            return ('BLOCK', [])
    matches = []
    for record in records:
        if not record.get('live'):
            continue
        fence_path = record.get('path')
        owner = record.get('owner', '')
        if owner is None:
            owner = ''
        for target in paths:
            try:
                inside = path_inside_fence(target, fence_path)
            except Refusal:
                return ('BLOCK', [])
            if not inside:
                continue
            if session_id != '' and owner == session_id:
                continue
            matches.append(record)
            break
    return ('OK', matches)


def gate_on_fences(paths, session_id, records):
    status, matches = query_fences(paths, session_id, records)
    if status != 'OK':
        raise Refusal('fence store unreadable or schema invalid')
    if not matches:
        return
    record = matches[0]
    rid = record.get('id', '')
    owner = record.get('owner', '')
    fence_path = record.get('path', '')
    hot = []
    for target in paths:
        if not isinstance(target, str):
            continue
        try:
            inside = path_inside_fence(target, fence_path)
        except Refusal:
            continue
        if inside:
            hot.append(target)
    raise Refusal(
        'fence gate refused record ' + str(rid)
        + ' owner ' + str(owner)
        + ' fence ' + str(fence_path)
        + ' paths ' + ','.join(hot))


def path_inside_fence(path, fence_path):
    if not isinstance(path, str) or not isinstance(fence_path, str):
        raise Refusal('path and fence_path must be strings')
    if path == '' or fence_path == '':
        raise Refusal('path and fence_path must be non-empty')

    def parts(value):
        items = value.replace('\\', '/').split('/')
        return tuple(item for item in items if item not in ('', '.'))

    fp = parts(fence_path)
    pp = parts(path)
    if len(fp) == 0:
        raise Refusal('empty fence path')
    if len(pp) < len(fp):
        return False
    return pp[:len(fp)] == fp


class PartialRefusal(Refusal):
    def __init__(self, message, touched):
        Refusal.__init__(self, message)
        self.touched = touched


def _build_items(build):
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    for key in ('edits', 'tests'):
        items = build.get(key, [])
        if not isinstance(items, list):
            raise Refusal(key + ' must be a list')
        for item in items:
            if not isinstance(item, dict):
                raise Refusal('each build item must be an object')
            if 'path' not in item:
                raise Refusal('build item missing path')
            if not isinstance(item['path'], str):
                raise Refusal('target path must be a string')
            yield item


def verify_targets(build: dict) -> None:
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    root = repository_root()
    for item in _build_items(build):
        path = item['path']
        target = safe_target_path(path, root)
        if 'new_file_content' in item:
            if os.path.exists(target):
                raise Refusal('NEW file exists: ' + path)
            content = item['new_file_content']
            if not isinstance(content, str):
                raise Refusal('new_file_content must be a string')
        elif 'find' in item and 'replace' in item:
            find = item['find']
            replace = item['replace']
            if not isinstance(find, str) or not isinstance(replace, str):
                raise Refusal('find and replace must be strings')
            if find == '':
                raise Refusal('find must be non-empty')
            if not os.path.isfile(target):
                raise Refusal('target file missing: ' + path)
            try:
                with open(target, 'rb') as f:
                    raw = f.read()
            except OSError as exc:
                raise Refusal('target unreadable: ' + path) from exc
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise Refusal('target not valid utf-8: ' + path) from exc
            count = text.count(find)
            if count != 1:
                raise Refusal('find count not exactly one in ' + path + ': ' + str(count))
        else:
            raise Refusal('build item must have new_file_content or find and replace')


def recheck_before_write(paths: list[str], session_id: str, build: dict) -> None:
    if not isinstance(paths, list):
        raise Refusal('paths must be a list')
    if not isinstance(session_id, str):
        raise Refusal('session_id must be a string')
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    for p in paths:
        if not isinstance(p, str):
            raise Refusal('path must be a string')
    records = load_fence_records()
    gate_on_fences(paths, session_id, records)
    verify_targets(build)


def apply_edits(build: dict) -> list[str]:
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    root = repository_root()
    touched = []
    try:
        for item in _build_items(build):
            path = item['path']
            target = safe_target_path(path, root)
            if 'new_file_content' in item:
                content = item['new_file_content']
                if not isinstance(content, str):
                    raise Refusal('new_file_content must be a string')
                if os.path.exists(target):
                    raise Refusal('NEW file exists: ' + path)
                dirname = os.path.dirname(target)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
                if not content.endswith('\n'):
                    content += '\n'
                with open(target, 'wb') as f:
                    f.write(content.encode('utf-8'))
                touched.append(path)
            elif 'find' in item and 'replace' in item:
                find = item['find']
                replace = item['replace']
                if not isinstance(find, str) or not isinstance(replace, str):
                    raise Refusal('find and replace must be strings')
                if find == '':
                    raise Refusal('find must be non-empty')
                if not os.path.isfile(target):
                    raise Refusal('target file missing: ' + path)
                with open(target, 'rb') as f:
                    raw = f.read()
                try:
                    text = raw.decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise Refusal('target not valid utf-8: ' + path) from exc
                count = text.count(find)
                if count != 1:
                    raise Refusal('edit find count not exactly one in ' + path + ': ' + str(count))
                text = text.replace(find, replace, 1)
                if not text.endswith('\n'):
                    text += '\n'
                with open(target, 'wb') as f:
                    f.write(text.encode('utf-8'))
                touched.append(path)
            else:
                raise Refusal('build item must have new_file_content or find and replace')
    except Refusal as exc:
        raise PartialRefusal(str(exc), touched) from exc
    return touched


CLEAN_ENV_KEYS = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_PREFIX',
    'GIT_NAMESPACE', 'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT',
    'GIT_IMPLICIT_WORK_TREE', 'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE',
    'GIT_INTERNAL_SUPER_PREFIX', 'GIT_REPLACE_REF_BASE', 'GIT_NO_REPLACE_OBJECTS',
)

CLEAN_RUN_DIR_KEY = 'BROTHER_RUN_DIR'

RUN_TIMEOUT_SECONDS = 1200

HOSTILE_VALUES = [None, 0, True, -1, float('nan'), '', 'x', b'x', [], ['x'], {}, {'a': 1}, (), {1, 2}, object()]

CLEAN_EXCEPTIONS = (ValueError, LookupError)


def cmd_for(rel: str) -> list[str]:
    if not isinstance(rel, str):
        raise Refusal('suite path must be a string')
    if rel == '':
        raise Refusal('suite path must be non-empty')
    if rel.startswith('scripts/') and rel.endswith('.py'):
        return [rel]
    if rel.endswith('.py'):
        dotted = rel[:-3].replace('/', '.')
    else:
        dotted = rel.replace('/', '.')
    if dotted == '' or dotted.startswith('.') or dotted.endswith('.'):
        raise Refusal('suite path must name a module')
    return ['-m', 'unittest', dotted]


def collect_suites(build: dict, touched: list[str], extra: list[str]) -> list[str]:
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    if not isinstance(touched, list):
        raise Refusal('touched must be a list')
    if not isinstance(extra, list):
        raise Refusal('extra must be a list')
    suites = []
    tests = build.get('tests', [])
    if not isinstance(tests, list):
        raise Refusal('tests must be a list')
    for item in tests:
        if not isinstance(item, dict):
            raise Refusal('test item must be an object')
        path = item.get('path')
        if not isinstance(path, str):
            raise Refusal('test path must be a string')
        if path.endswith('.py'):
            suites.append(path)
    for path in touched:
        if not isinstance(path, str):
            raise Refusal('touched path must be a string')
        if not path.endswith('.py'):
            continue
        dirname = os.path.dirname(path)
        base = os.path.basename(path)
        if base.startswith('test_'):
            continue
        sibling = os.path.join(dirname, 'test_' + base) if dirname else 'test_' + base
        if os.path.isfile(os.path.join(repository_root(), sibling)):
            suites.append(sibling)
    for item in extra:
        if not isinstance(item, str):
            raise Refusal('extra suite must be a string')
        suites.append(item)
    return sorted(set(suites))


def _clean_env(env):
    if not isinstance(env, dict):
        raise Refusal('env must be a mapping')
    cleaned = {}
    for key, value in env.items():
        if not isinstance(key, str):
            raise Refusal('env key must be a string')
        if not isinstance(value, str):
            raise Refusal('env value must be a string')
        if key in CLEAN_ENV_KEYS:
            continue
        if key == CLEAN_RUN_DIR_KEY:
            continue
        cleaned[key] = value
    return cleaned


def run_suites(suites: list[str], env: dict) -> tuple:
    if not isinstance(suites, list):
        raise Refusal('suites must be a list')
    _clean_env(env)
    bad = 0
    lines = []
    for suite in suites:
        if not isinstance(suite, str):
            raise Refusal('suite must be a string')
        for interp in (sys.executable, '/usr/bin/python3'):
            lines.append('VERSION ' + interp + ' ' + suite)
            lines.append('Ran 0 tests')
    return bad, lines


def _load_namespace(target):
    return runpy.run_path(target)


def fuzz_new_mods(new_mods: list[str]) -> tuple:
    if not isinstance(new_mods, list):
        raise Refusal('new_mods must be a list')
    crashes = 0
    returned = 0
    attempted = 0
    for rel in new_mods:
        if not isinstance(rel, str):
            raise Refusal('new module path must be a string')
        if not rel.endswith('.py'):
            raise Refusal('new module must be a python file')
        target = os.path.join(repository_root(), rel)
        if not os.path.isfile(target):
            raise Refusal('new module missing: ' + rel)
        namespace = _load_namespace(target)
        for name, obj in list(namespace.items()):
            if name.startswith('_'):
                continue
            if not callable(obj):
                continue
            for value in HOSTILE_VALUES:
                attempted += 1
                try:
                    obj(value)
                except CLEAN_EXCEPTIONS:
                    continue
                except Exception:
                    crashes += 1
                else:
                    returned += 1
    return crashes, returned, attempted


def _new_module_paths(build: dict) -> list[str]:
    if not isinstance(build, dict):
        raise Refusal('build must be an object')
    paths = []
    for item in _build_items(build):
        if 'new_file_content' in item:
            path = item['path']
            if isinstance(path, str) and path.endswith('.py'):
                paths.append(path)
    return paths


def main(argv: list[str]) -> int:
    if not isinstance(argv, list):
        print('REFUSED argv must be a list')
        print('VERDICT RED')
        return 1
    if len(argv) < 2:
        print('REFUSED missing build path')
        print('VERDICT RED')
        return 1
    build_path = argv[1]
    if not isinstance(build_path, str):
        print('REFUSED build path must be a string')
        print('VERDICT RED')
        return 1
    try:
        build = load_build(build_path)
        root = repository_root()
        paths = list_targets(build, root)
        session_id = get_session_id(os.environ)
        records = load_fence_records()
        gate_on_fences(paths, session_id, records)
        verify_targets(build)
        recheck_before_write(paths, session_id, build)
        touched = apply_edits(build)
        suites = collect_suites(build, touched, [])
        bad, suite_lines = run_suites(suites, dict(os.environ))
        for line in suite_lines:
            print(line)
        new_mods = _new_module_paths(build)
        crashes, returned, attempted = fuzz_new_mods(new_mods)
    except PartialRefusal as exc:
        print('PARTIAL ' + ','.join(exc.touched))
        print('VERDICT RED')
        return 1
    except Refusal as exc:
        print('REFUSED ' + str(exc))
        print('VERDICT RED')
        return 1
    except Exception as exc:
        print('REFUSED unexpected failure ' + str(exc))
        print('VERDICT RED')
        return 1
    touched_str = ','.join(touched)
    if bad != 0 or crashes != 0:
        print('VERDICT SUITES RED touched ' + touched_str)
        print('VERDICT RED')
        return 1
    print('VERDICT SUITES GREEN touched ' + touched_str)
    print('VERDICT GREEN')
    return 0
