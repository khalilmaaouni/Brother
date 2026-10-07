"""Supply chain audit gate, sub-unit L5f-a: manifest discovery.

This module is stdlib only. It currently implements only list_manifest_paths,
the manifest discovery required by L5f-a. Other L5f sub-units are not built here.
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import sysconfig
import typing

try:
    import tomllib
except ImportError:
    tomllib = None

_EXACT_MANIFEST_NAMES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
)

_GLOB_MANIFEST_PATTERNS = (
    "requirements*.txt",
    "requirements/*.txt",
    "Dockerfile*",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "plugin/**/pyproject.toml",
    "plugin/**/package.json",
)


def list_manifest_paths(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Return sorted candidate manifest paths under repo_root.

    Exact names are returned even when absent so the caller can state them
    absent. Glob patterns return only paths that exist. Any returned path
    whose string form contains a newline byte raises ValueError.
    """
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError("repo_root must be a pathlib.Path, got %s" % type(repo_root).__name__)
    if not repo_root.is_dir():  # a missing checkout blocks; it is never "nothing to audit"
        raise ValueError("repo_root is not a folder: %s" % repo_root)
    candidates: list[pathlib.Path] = []
    for name in _EXACT_MANIFEST_NAMES:
        candidates.append(repo_root / name)
    for pattern in _GLOB_MANIFEST_PATTERNS:
        candidates.extend(repo_root.glob(pattern))
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    for path in unique:
        if "\n" in str(path):
            raise ValueError("newline byte in manifest path")
    return sorted(unique)


# --- L5f-b pin and lock verifier ---

_EXACT_PIN_FORBIDDEN = "<>=~^*x"
_PIN_BRANCH_NAMES = {"latest", "head", "main"}
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,214}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _finding(severity, code, message, **extra):
    out = {"severity": severity, "code": code, "message": message}
    out.update(extra)
    return out


def check_pin_spec(spec: str) -> tuple[bool, str]:
    """Return (True, reason) only for an exact version or 40 hex commit."""
    if not isinstance(spec, str):
        return (False, "not a string")
    if spec == "" or spec != spec.strip():
        return (False, "empty or surrounding space")
    if any(ch in spec for ch in _EXACT_PIN_FORBIDDEN):
        return (False, "range or wildcard character")
    if any(ch.isspace() for ch in spec):
        return (False, "space")
    if spec.lower() in _PIN_BRANCH_NAMES:
        return (False, "branch name")
    if re.fullmatch(r"[0-9a-fA-F]{40}", spec):
        return (True, "full commit")
    if not any(ch.isdigit() for ch in spec):
        return (False, "no version digit")
    return (True, "exact")


def lockfile_sha256(path: pathlib.Path) -> str:
    """Lowercase hex sha256 of raw bytes, or empty string if missing/unreadable."""
    if not isinstance(path, pathlib.Path):
        raise ValueError("path must be a pathlib.Path")
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def verify_pins(rows: list[dict], manifests: list[pathlib.Path], repo_root: pathlib.Path) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if not isinstance(manifests, list):
        raise ValueError("manifests must be a list")
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError("repo_root must be a pathlib.Path")
    findings: list[dict] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("row %d is not a dict" % idx)
        name = row.get("name")
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            findings.append(_finding("BLOCK", "pin.bad_name", "row %d has invalid name" % idx))
            continue
        pin = row.get("version_pinned")
        ok, why = check_pin_spec(pin if isinstance(pin, str) else pin)
        if not ok:
            findings.append(_finding("BLOCK", "pin.not_exact", "row %s pin %r: %s" % (name, pin, why), row_name=name))
        loc = row.get("pin_location")
        if not isinstance(loc, str) or not loc or "\n" in loc:
            findings.append(_finding("BLOCK", "pin.bad_location", "row %s invalid pin_location" % name, row_name=name))
        else:
            if ":" not in loc:
                findings.append(_finding("BLOCK", "pin.bad_location", "row %s pin_location lacks path:line" % name, row_name=name))
            else:
                path_part, line_part = loc.rsplit(":", 1)
                if not line_part.isdigit() or int(line_part) < 1:
                    findings.append(_finding("BLOCK", "pin.bad_location", "row %s pin_location line invalid" % name, row_name=name))
                else:
                    pin_path = pathlib.Path(path_part)
                    if not pin_path.is_absolute():
                        pin_path = repo_root / pin_path
                    if not pin_path.exists():
                        findings.append(_finding("BLOCK", "pin.missing_file", "row %s pin_location file absent: %s" % (name, path_part), row_name=name))
    return findings


def verify_lockfiles(rows: list[dict], repo_root: pathlib.Path) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError("repo_root must be a pathlib.Path")
    findings: list[dict] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("row %d is not a dict" % idx)
        name = row.get("name", "row%d" % idx)
        lock_path = row.get("lockfile_path")
        expected = row.get("lockfile_sha256")
        if not isinstance(lock_path, str) or not lock_path or "\n" in lock_path:
            findings.append(_finding("BLOCK", "lock.missing_path", "row %s lockfile_path invalid" % name, row_name=name))
            continue
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            findings.append(_finding("BLOCK", "lock.bad_hash", "row %s lockfile_sha256 not 64 lowercase hex" % name, row_name=name))
            continue
        p = pathlib.Path(lock_path)
        if not p.is_absolute():
            p = repo_root / lock_path
        actual = lockfile_sha256(p)
        if actual == "":
            findings.append(_finding("BLOCK", "lock.missing", "row %s lockfile missing or unreadable: %s" % (name, lock_path), row_name=name))
        elif actual != expected:
            findings.append(_finding("BLOCK", "lock.mismatch", "row %s lockfile sha256 mismatch" % name, row_name=name))
    return findings


def _is_dependency_manifest(path: pathlib.Path) -> bool:
    name = path.name
    if name in ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "Pipfile"):
        return True
    if re.fullmatch(r"requirements.*\.txt", name):
        return True
    if path.parent.name == "requirements" and path.suffix == ".txt":
        return True
    return False


def _clean_dep_name(raw):
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.startswith("#"):
        return None
    text = text.split("#", 1)[0].strip()
    text = text.split(";", 1)[0].strip()
    for sep in ("===", "==", ">=", "<=", "~=", "!=", ">", "<", "="):
        if sep in text:
            text = text.split(sep, 1)[0].strip()
            break
    if "[" in text:
        text = text.split("[", 1)[0].strip()
    if not _NAME_RE.fullmatch(text):
        return None
    return text


def _deps_from_requirements(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return (None, "unreadable: %s" % type(exc).__name__)
    deps = set()
    for line in text.splitlines():
        name = _clean_dep_name(line)
        if name:
            deps.add(name)
    return (deps, None)


def _deps_from_package_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return (None, "unparsable json: %s" % type(exc).__name__)
    deps = set()
    if isinstance(data, dict):
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            val = data.get(key)
            if isinstance(val, dict):
                for dep_name in val.keys():
                    clean = _clean_dep_name(dep_name)
                    if clean:
                        deps.add(clean)
    return (deps, None)


def _deps_from_toml_data(data):
    deps = set()
    project = data.get("project", {})
    if isinstance(project, dict):
        val = project.get("dependencies")
        if isinstance(val, list):
            for item in val:
                clean = _clean_dep_name(item)
                if clean:
                    deps.add(clean)
        opt = project.get("optional-dependencies")
        if isinstance(opt, dict):
            for lst in opt.values():
                if isinstance(lst, list):
                    for item in lst:
                        clean = _clean_dep_name(item)
                        if clean:
                            deps.add(clean)
    tool = data.get("tool", {})
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    if isinstance(poetry, dict):
        for key in ("dependencies", "dev-dependencies"):
            val = poetry.get(key)
            if isinstance(val, dict):
                for dep_name in val.keys():
                    clean = _clean_dep_name(dep_name)
                    if clean:
                        deps.add(clean)
    return deps


def _strip_toml_comment(line):
    out = []
    quote = None
    escape = False
    for ch in line:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and quote == '"':
            out.append(ch)
            escape = True
            continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        else:
            if ch == '"' or ch == "'":
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
    return "".join(out).rstrip()


def _brackets_balanced(text):
    depth = 0
    quote = None
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and quote == '"':
            escape = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch == '"' or ch == "'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth == 0


def _parse_toml_string_array(text):
    try:
        val = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return (None, "unparsable toml: array")
    if not isinstance(val, list):
        return (None, "unparsable toml: array is not a list")
    out = []
    for item in val:
        if not isinstance(item, str):
            return (None, "unparsable toml: array item not string")
        out.append(item)
    return (out, None)


def _deps_from_pyproject_fallback(text):
    deps = set()
    current = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = _strip_toml_comment(raw).strip()
        if not line:
            i += 1
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                return (None, "unparsable toml: table header")
            header = line.strip("[]").strip()
            if header.startswith("["):
                header = header[1:].strip()
            current = [p.strip() for p in header.split(".") if p.strip()]
            i += 1
            continue
        if "=" not in line:
            return (None, "unparsable toml: expected key = value")
        key, val = line.split("=", 1)
        key = key.strip().strip('"').strip("'")
        val = val.strip()
        if val.startswith("["):
            combined = val
            while not _brackets_balanced(combined):
                i += 1
                if i >= len(lines):
                    return (None, "unparsable toml: unterminated array")
                combined += "\n" + _strip_toml_comment(lines[i])
            val = combined
        section = ".".join(current)
        if section == "project" and key == "dependencies":
            arr, err = _parse_toml_string_array(val)
            if err is not None:
                return (None, err)
            for item in arr:
                clean = _clean_dep_name(item)
                if clean:
                    deps.add(clean)
        elif section == "project.optional-dependencies":
            arr, err = _parse_toml_string_array(val)
            if err is not None:
                return (None, err)
            for item in arr:
                clean = _clean_dep_name(item)
                if clean:
                    deps.add(clean)
        elif section in ("tool.poetry.dependencies", "tool.poetry.dev-dependencies"):
            clean = _clean_dep_name(key)
            if clean and val:
                deps.add(clean)
            if val.startswith("["):
                arr, err = _parse_toml_string_array(val)
                if err is not None:
                    return (None, err)
                for item in arr:
                    clean_item = _clean_dep_name(item)
                    if clean_item:
                        deps.add(clean_item)
        i += 1
    return (deps, None)


def _deps_from_pyproject(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return (None, "unreadable: %s" % type(exc).__name__)
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except (ValueError, TypeError) as exc:
            return (None, "unparsable toml: %s" % type(exc).__name__)
        return (_deps_from_toml_data(data), None)
    return _deps_from_pyproject_fallback(text)


def _parse_dependency_manifest(path):
    name = path.name
    if name == "pyproject.toml":
        return _deps_from_pyproject(path)
    if name == "package.json":
        return _deps_from_package_json(path)
    if re.fullmatch(r"requirements.*\.txt", name) or (path.parent.name == "requirements" and path.suffix == ".txt"):
        return _deps_from_requirements(path)
    return (None, "unsupported manifest format")


def verify_manifest_coverage(rows: list[dict], manifests: list[pathlib.Path], repo_root: pathlib.Path) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if not isinstance(manifests, list):
        raise ValueError("manifests must be a list")
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError("repo_root must be a pathlib.Path")
    findings: list[dict] = []
    row_names = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("row %d is not a dict" % idx)
        name = row.get("name")
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            findings.append(_finding("BLOCK", "coverage.bad_row_name", "row %d has invalid name" % idx))
            continue
        row_names.add(name)
    manifest_names = set()
    for path in manifests:
        if not isinstance(path, pathlib.Path):
            raise ValueError("manifest paths must be pathlib.Path")
        if "\n" in str(path):
            findings.append(_finding("BLOCK", "coverage.newline", "newline byte in manifest path"))
            continue
        if not _is_dependency_manifest(path):
            continue
        p = path
        if not p.is_absolute():
            p = repo_root / path
        if not p.exists():
            continue
        deps, err = _parse_dependency_manifest(p)
        if err is not None:
            findings.append(_finding("NO-DATA", "coverage.unparsable", "manifest %s: %s" % (p, err)))
            continue
        manifest_names.update(deps or set())
    extra_manifest_deps = manifest_names - row_names
    missing_manifest_deps = row_names - manifest_names
    if extra_manifest_deps:
        findings.append(_finding("BLOCK", "coverage.extra_manifest_dep", "manifest deps missing rows: %s" % ", ".join(sorted(extra_manifest_deps))))
    if missing_manifest_deps:
        findings.append(_finding("BLOCK", "coverage.missing_manifest_dep", "rows missing manifest deps: %s" % ", ".join(sorted(missing_manifest_deps))))
    return findings


# --- L5f-c license fetcher and allowlist ---

_ALLOWED_LICENSES = frozenset({
    'MIT',
    'MIT-0',
    'Apache-2.0',
    'BSD-2-Clause',
    'BSD-3-Clause',
    '0BSD',
    'ISC',
    'PSF-2.0',
    'Python-2.0',
    'Unlicense',
    'CC0-1.0',
    'Zlib',
})

_MAX_FETCH_BYTES = 1024 * 1024


def _require_str(value, name):
    if not isinstance(value, str):
        raise ValueError('%s must be a str' % name)
    if chr(10) in value:
        raise ValueError('%s must not contain a newline' % name)
    return value


def _require_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('%s must be a number' % name)
    if value != value:
        raise ValueError('%s must not be NaN' % name)
    if value <= 0:
        raise ValueError('%s must be positive' % name)
    return value


def _require_aware_datetime(value, name):
    if not isinstance(value, datetime.datetime):
        raise ValueError('%s must be a datetime' % name)
    if value.tzinfo is None:
        raise ValueError('%s must be timezone aware' % name)
    return value


def allowed_license(spdx_id: str) -> bool:
    if not isinstance(spdx_id, str):
        raise ValueError('spdx_id must be a str')
    return spdx_id in _ALLOWED_LICENSES


def fetch_registry_page(url: str, timeout_s: float, opener: typing.Optional[typing.Callable] = None) -> dict:
    url = _require_str(url, 'url')
    if not url:
        raise ValueError('url must not be empty')
    _require_number(timeout_s, 'timeout_s')
    if opener is not None and not callable(opener):
        raise ValueError('opener must be callable or None')
    if opener is None:
        return {
            'http_status': 0,
            'error': 'no opener supplied',
            'body_sha256': '',
            'body_bytes': 0,
            'fetched_at': None,
        }
    try:
        body = opener(url, timeout_s)
    except Exception as exc:
        return {
            'http_status': 0,
            'error': '%s: %s' % (type(exc).__name__, exc),
            'body_sha256': '',
            'body_bytes': 0,
            'fetched_at': None,
        }
    if not isinstance(body, (bytes, bytearray)):
        return {
            'http_status': 0,
            'error': 'opener returned non bytes',
            'body_sha256': '',
            'body_bytes': 0,
            'fetched_at': None,
        }
    body = bytes(body)
    if len(body) == 0:
        return {
            'http_status': 0,
            'error': 'empty body',
            'body_sha256': '',
            'body_bytes': 0,
            'fetched_at': None,
        }
    if len(body) > _MAX_FETCH_BYTES:
        return {
            'http_status': 0,
            'error': 'body over 1 MiB',
            'body_sha256': '',
            'body_bytes': len(body),
            'fetched_at': None,
        }
    return {
        'http_status': 200,
        'body': body,
        'body_sha256': hashlib.sha256(body).hexdigest(),
        'body_bytes': len(body),
        'fetched_at': datetime.datetime.now(datetime.timezone.utc),
        'error': '',
    }


def _safe_snippet_path(repo_root, raw_path):
    base = (repo_root / 'docs' / 'architecture' / 'L5F-fetched').resolve()
    candidate = pathlib.Path(raw_path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return None
    return resolved


def _license_field(parsed, eco):
    if not isinstance(parsed, dict):
        return None
    if eco == 'pypi':
        info = parsed.get('info')
        if isinstance(info, dict):
            return info.get('license')
        return None
    if eco == 'npm':
        return parsed.get('license')
    return None


def verify_licenses_offline(rows: list, repo_root: pathlib.Path) -> list:
    if not isinstance(rows, list):
        raise ValueError('rows must be a list')
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError('repo_root must be a pathlib.Path')
    findings = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('row %d is not a dict' % idx)
        status = row.get('fetched_http_status')
        if status is None:
            continue
        if not isinstance(status, int) or isinstance(status, bool):
            findings.append(_finding('BLOCK', 'fetched_http_status_invalid', 'row %d fetched_http_status is not an int' % idx))
            continue
        if status != 200:
            continue
        raw_path = row.get('raw_snippet_path')
        if not isinstance(raw_path, str) or not raw_path or chr(10) in raw_path:
            findings.append(_finding('BLOCK', 'snippet_path_invalid', 'row %d raw_snippet_path invalid' % idx))
            continue
        path = _safe_snippet_path(repo_root, raw_path)
        if path is None:
            findings.append(_finding('BLOCK', 'snippet_path_escape', 'row %d raw_snippet_path escapes L5F-fetched' % idx))
            continue
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            findings.append(_finding('BLOCK', 'snippet_absent', 'row %d snippet absent' % idx))
            continue
        except OSError:
            findings.append(_finding('BLOCK', 'snippet_unreadable', 'row %d snippet unreadable' % idx))
            continue
        if len(data) == 0:
            findings.append(_finding('BLOCK', 'snippet_empty', 'row %d snippet empty' % idx))
            continue
        expected_hash = row.get('fetched_body_sha256')
        if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            findings.append(_finding('BLOCK', 'snippet_hash_invalid', 'row %d fetched_body_sha256 invalid' % idx))
            continue
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != expected_hash:
            findings.append(_finding('BLOCK', 'snippet_hash_mismatch', 'row %d snippet hash mismatch' % idx))
            continue
        try:
            parsed = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            findings.append(_finding('BLOCK', 'snippet_unparseable', 'row %d snippet is not parseable JSON' % idx))
            continue
        license_val = _license_field(parsed, row.get('eco'))
        fetched_license = row.get('fetched_license')
        if license_val != fetched_license:
            findings.append(_finding('BLOCK', 'snippet_license_mismatch', 'row %d license mismatch' % idx))
            continue
        if not isinstance(fetched_license, str):
            findings.append(_finding('BLOCK', 'license_not_string', 'row %d fetched_license not a string' % idx))
            continue
        if fetched_license == 'UNKNOWN':
            findings.append(_finding('NO-DATA', 'license_unknown', 'row %d fetched_license is UNKNOWN' % idx))
        elif not allowed_license(fetched_license):
            findings.append(_finding('BLOCK', 'license_not_allowed', 'row %d fetched_license %r not allowlisted' % (idx, fetched_license)))
    return findings


def verify_licenses_online(rows: list, now: datetime.datetime, opener: typing.Optional[typing.Callable] = None) -> list:
    if not isinstance(rows, list):
        raise ValueError('rows must be a list')
    _require_aware_datetime(now, 'now')
    if opener is not None and not callable(opener):
        raise ValueError('opener must be callable or None')
    findings = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('row %d is not a dict' % idx)
        source_url = row.get('source_url')
        if not isinstance(source_url, str) or not source_url or chr(10) in source_url:
            findings.append(_finding('NO-DATA', 'source_url_invalid', 'row %d source_url invalid' % idx))
            continue
        result = fetch_registry_page(source_url, 30.0, opener)
        if result.get('http_status') != 200:
            findings.append(_finding('NO-DATA', 'fetch_failed', 'row %d fetch failed: %s' % (idx, result.get('error', ''))))
            continue
        expected_hash = row.get('fetched_body_sha256')
        if result.get('body_sha256') != expected_hash:
            findings.append(_finding('BLOCK', 'online_hash_mismatch', 'row %d online hash mismatch' % idx))
            continue
        body = result.get('body')
        try:
            parsed = json.loads(bytes(body).decode('utf-8'))
        except (TypeError, UnicodeDecodeError, ValueError):
            findings.append(_finding('NO-DATA', 'online_unparseable', 'row %d online body unparseable' % idx))
            continue
        license_val = _license_field(parsed, row.get('eco'))
        if license_val != row.get('fetched_license'):
            findings.append(_finding('BLOCK', 'online_license_mismatch', 'row %d online license mismatch' % idx))
    return findings


def verify_freshness(rows: list, repo_root: pathlib.Path, now: datetime.datetime) -> list:
    if not isinstance(rows, list):
        raise ValueError('rows must be a list')
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError('repo_root must be a pathlib.Path')
    _require_aware_datetime(now, 'now')
    findings = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('row %d is not a dict' % idx)
        pin_location = row.get('pin_location')
        if not isinstance(pin_location, str) or ':' not in pin_location:
            findings.append(_finding('NO-DATA', 'pin_location_shape', 'row %d pin_location lacks path:line' % idx))
            continue
        path_part, line_part = pin_location.rsplit(':', 1)
        if not path_part or not line_part.isdigit() or int(line_part) < 1:
            findings.append(_finding('NO-DATA', 'pin_location_shape', 'row %d pin_location shape invalid' % idx))
            continue
        pin_path = pathlib.Path(path_part)
        if not pin_path.is_absolute():
            pin_path = repo_root / pin_path
        try:
            mtime = os.stat(str(pin_path)).st_mtime
        except FileNotFoundError:
            findings.append(_finding('BLOCK', 'pin_file_absent', 'row %d pin file absent' % idx))
            continue
        except OSError:
            findings.append(_finding('BLOCK', 'pin_file_absent', 'row %d pin file unreadable' % idx))
            continue
        mtime_dt = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc)
        fetched_on_raw = row.get('fetched_on')
        if not isinstance(fetched_on_raw, str):
            findings.append(_finding('BLOCK', 'fetched_on_invalid', 'row %d fetched_on not a string' % idx))
            continue
        try:
            fetched_on = datetime.datetime.strptime(fetched_on_raw, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            findings.append(_finding('BLOCK', 'fetched_on_invalid', 'row %d fetched_on not YYYY-MM-DD' % idx))
            continue
        if fetched_on > now:
            findings.append(_finding('BLOCK', 'fetched_on_invalid', 'row %d fetched_on is in the future' % idx))
            continue
        end_of_day = fetched_on.replace(hour=23, minute=59, second=59, microsecond=0)
        if mtime_dt > end_of_day:
            findings.append(_finding('BLOCK', 'stale', 'row %d pin mtime after fetched_on' % idx))
            continue
        if (now - fetched_on).days > 90:
            findings.append(_finding('BLOCK', 'stale', 'row %d fetched_on older than 90 days' % idx))
    return findings


# --- L5f-d runtime install scanner ---

_RUNTIME_SCAN_SUFFIXES = frozenset(
    {
        '.py', '.sh', '.bash', '.js', '.ts', '.mjs', '.cjs',
        '.zsh', '.ksh', '.mk',
    }
)

# Data, prose and page formats do not install anything on the host. HTML and CSS run in a browser
# page, which cannot install a package on this machine, so they sit with Markdown (2026-09-28: the
# old rule kept 215 HTML boards as NO-DATA, and the gate could never pass). Any OTHER suffix is
# read as text and line scanned; only a file that is not text at all stays NO-DATA.
_RUNTIME_NON_CODE_SUFFIXES = frozenset(
    {
        '.md', '.json', '.txt', '.lock', '.png', '.jpg', '.svg',
        '.pdf', '.zip', '.gz', '.pyc', '.toml', '.cfg', '.ini',
        '.yml', '.yaml', '.jsonl', '.csv', '.tsv', '.log', '.patch',
        '.diff', '.mdc', '.rst', '.docx', '.sha256', '.sig', '.cff',
        '.html', '.htm', '.css', '.out', '.gitkeep', '.gitignore', '.gitattributes',
        '.brother-edition', '.sbe-exempt', '.sb', '.base', '.pbxproj', '.xcscheme', '.plist',
    }
)

_RUNTIME_NON_CODE_NAMES = frozenset({'LICENSE', 'VERSION', 'CODEOWNERS', 'allowed_signers', 'APPROVAL'})

_RUNTIME_SCANNED_NAMES = frozenset({'Makefile', 'Justfile'})

_RUNTIME_EXCLUDED_DIRS = frozenset({'.git', '__pycache__', 'node_modules'})

_RUNTIME_SEVERITY = {
    'runtime_install': 'BLOCK',
    'build_time_install_allowed': 'INFO',
    'runtime_exception': 'INFO',
    'test_install_allowed': 'INFO',
    'unparseable_python': 'NO-DATA',
    'unreadable_script': 'NO-DATA',
    'unscanned_suffix': 'NO-DATA',
    'unknown_installer_family': 'NO-DATA',
}

_INSTALL_VERBS = ('install', 'add', 'fetch', 'download', 'bootstrap', 'setup')

_INSTALL_TOKENS = (
    'pip', 'npm', 'yarn', 'pnpm', 'bun', 'deno', 'gem', 'cargo',
    'go get', 'apt', 'apk', 'brew', 'curl', 'wget', 'uv', 'poetry', 'conda',
)

_INSTALL_PROXIMITY = 40

_EXACT_INSTALL_RE = re.compile(
    '|'.join(
        (
            r'(?i:pip(?:\d+)?\s+install)',
            r'(?i:python[0-9.]*\s+-m\s+pip\s+install)',
            r'\beasy_install\b',
            r'\bnpm\s+install\b',
            r'\bnpm\s+ci\b',
            r'\byarn\s+add\b',
            r'\bpnpm\s+add\b',
            r'\b(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash|python|node)\b',
            r'\bsubprocess\.[A-Za-z_]+\([^\n]*\b(?:pip|npm|yarn|pnpm|easy_install)\b',
            r'\bos\.system\([^\n]*(?:pip\s+install|npm\s+install)',
        )
    )
)

_JS_IMPORT_RE = re.compile(
    r'(?:from\s*[\x22\x27]([^\x22\x27\n]+)[\x22\x27]'
    r'|require\(\s*[\x22\x27]([^\x22\x27\n]+)[\x22\x27]\s*\))'
)


def _require_path(value, name):
    if not isinstance(value, pathlib.Path):
        raise ValueError('%s must be a pathlib.Path' % name)
    return value


def _line_positions(haystack, needle):
    """Start positions of needle as a WHOLE word: 'bun' inside 'bundle' and 'pip' inside 'pipe'
    made 23 echo and say lines read as unknown installers (2026-09-28)."""
    if not isinstance(haystack, str) or not isinstance(needle, str):
        raise ValueError('haystack and needle must be str')
    pattern = r'(?<![A-Za-z0-9_])' + re.escape(needle) + r'(?![A-Za-z0-9_])'
    return [m.start() for m in re.finditer(pattern, haystack)]


def _broad_install_shaped(line):
    if not isinstance(line, str):
        raise ValueError('line must be a str')
    low = line.lower()
    verb_positions = []
    for verb in _INSTALL_VERBS:
        verb_positions.extend(_line_positions(low, verb))
    if not verb_positions:
        return False
    token_positions = []
    for token in _INSTALL_TOKENS:
        token_positions.extend(_line_positions(low, token))
    if not token_positions:
        return False
    for verb_pos in verb_positions:
        for token_pos in token_positions:
            if abs(verb_pos - token_pos) <= _INSTALL_PROXIMITY:
                return True
    return False


def _runtime_scan_kind(name, suffix):
    if not isinstance(name, str) or not name:
        raise ValueError('name must be a non empty str')
    if not isinstance(suffix, str):
        raise ValueError('suffix must be a str')
    if name.startswith('Dockerfile'):
        return 'scanned'
    if name in _RUNTIME_SCANNED_NAMES:
        return 'scanned'
    if suffix in _RUNTIME_SCAN_SUFFIXES:
        return 'scanned'
    if suffix in _RUNTIME_NON_CODE_SUFFIXES or name in _RUNTIME_NON_CODE_NAMES:
        return 'ignored'
    return 'text'


def _runtime_finding(rel_posix, line_no, matched_pattern, classification):
    if not isinstance(rel_posix, str) or not rel_posix:
        raise ValueError('rel_posix must be a non empty str')
    if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 0:
        raise ValueError('line_no must be a non negative int')
    if not isinstance(matched_pattern, str):
        raise ValueError('matched_pattern must be a str')
    if not isinstance(classification, str):
        raise ValueError('classification must be a str')
    if classification not in _RUNTIME_SEVERITY:
        raise ValueError('unknown classification: %r' % (classification,))
    return {
        'file_path': rel_posix,
        'line_no': line_no,
        'matched_pattern': matched_pattern,
        'classification': classification,
        'severity': _RUNTIME_SEVERITY[classification],
        'reviewed_reason': '',
    }


def classify_runtime_hit(rel_path, line_no, matched_pattern):
    _require_path(rel_path, 'rel_path')
    rel_posix = rel_path.as_posix()
    if '\n' in rel_posix:
        raise ValueError('newline byte in classified path')
    if rel_path.name.startswith('Dockerfile') or rel_posix.startswith('.github/workflows/'):
        classification = 'build_time_install_allowed'
    elif (rel_path.stem.startswith('test_') or rel_path.stem.endswith('_test')
          or any(part in ('test', 'tests') for part in rel_path.parts[:-1])):
        # Test installs stay visible, including temporary-directory fixtures.
        # This classification does not prove that their targets are isolated.
        classification = 'test_install_allowed'
    else:
        classification = 'runtime_install'
    return _runtime_finding(rel_posix, line_no, matched_pattern, classification)


def iter_scan_files(repo_root):
    _require_path(repo_root, 'repo_root')
    if not repo_root.is_dir():
        raise ValueError('repo_root is not a folder: %s' % repo_root)
    tracked = _git_tracked(repo_root)
    if tracked is not None:
        return tracked
    found = []
    for dirpath, dirnames, filenames in os.walk(str(repo_root)):
        dirnames[:] = sorted(d for d in dirnames if d not in _RUNTIME_EXCLUDED_DIRS)
        for fname in filenames:
            candidate = pathlib.Path(dirpath) / fname
            try:
                rel = candidate.relative_to(repo_root)
            except ValueError:
                continue
            rel_posix = rel.as_posix()
            if '\n' in rel_posix:
                raise ValueError('newline byte in scanned path')
            found.append(candidate)
    found.sort(key=lambda item: str(item))
    return found


def _python_call_name(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _python_call_name(node.value, aliases) + '.' + node.attr
    return ''


def _literal_command(node):
    """Keep literal argv elements, with gaps where values are computed.

    Do not walk arbitrary expressions: a literal passed to a formatter or
    logger inside an argument is not itself a process command.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return ' '.join(_literal_command(item) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_command(node.left) + ' ' + _literal_command(node.right)
    return '<dynamic>'


def _python_process_commands(tree):
    """Yield command arguments at process calls, never inert string data.

    This is a literal scan, not dataflow analysis: variables and wrappers
    are not resolved. Import aliases cover the direct process APIs too.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ('subprocess', 'os'):
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in ('subprocess', 'os'):
            for alias in node.names:
                aliases[alias.asname or alias.name] = node.module + '.' + alias.name
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _python_call_name(node.func, aliases)
        if name in {
            'subprocess.run', 'subprocess.Popen', 'subprocess.call',
            'subprocess.check_call', 'subprocess.check_output',
            'subprocess.getoutput', 'subprocess.getstatusoutput',
            'os.system', 'os.popen',
        }:
            args = node.args[:1]
            args += [kw.value for kw in node.keywords if kw.arg in ('args', 'cmd', 'command')]
        elif name.startswith(('os.exec', 'os.spawn')):
            # spawn* starts with a mode; *e ends with an environment mapping.
            args = node.args[1:] if name.startswith('os.spawn') else node.args[:]
            if name.endswith('e') and not any(kw.arg == 'env' for kw in node.keywords):
                args = args[:-1]
            args += [kw.value for kw in node.keywords if kw.arg in ('path', 'file', 'args')]
        else:
            continue
        command = ' '.join(_literal_command(arg) for arg in args)
        yield node.lineno, command


def _git_tracked(repo_root):
    """The files git tracks under repo_root, which is what ships; None outside a work tree, so the
    caller walks every file instead (the wider set, never the narrower one)."""
    try:
        proc = subprocess.run(['git', '-C', str(repo_root), 'ls-files', '-z'], capture_output=True,
                              timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    found = []
    for raw in proc.stdout.split(b'\0'):
        if not raw:
            continue
        rel = raw.decode('utf-8', 'surrogateescape')
        if '\n' in rel:
            raise ValueError('newline byte in scanned path')
        candidate = repo_root / rel
        if candidate.is_file() and not candidate.is_symlink():
            found.append(candidate)
    found.sort(key=lambda item: str(item))
    return found


def scan_runtime_installs(repo_root):
    _require_path(repo_root, 'repo_root')
    if not repo_root.is_dir():
        raise ValueError('repo_root is not a folder: %s' % repo_root)
    list_manifest_paths(repo_root)
    findings = []
    for path in iter_scan_files(repo_root):
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        kind = _runtime_scan_kind(path.name, path.suffix.lower())
        # Workflow YAML carries executable steps; ordinary YAML is data.
        if rel_posix.startswith('.github/workflows/') and path.suffix.lower() in ('.yml', '.yaml'):
            kind = 'scanned'
        if kind == 'ignored':
            continue
        try:
            data = path.read_bytes()
            text = data.decode('utf-8')
        except (OSError, UnicodeDecodeError):
            if kind == 'text':
                classification = 'unscanned_suffix'
            else:
                classification = 'unparseable_python' if path.suffix.lower() == '.py' else 'unreadable_script'
            findings.append(_runtime_finding(rel_posix, 0, '<unreadable>', classification))
            continue
        tree = None
        first_line = text.split('\n', 1)[0]
        if path.suffix.lower() == '.py' or (first_line.startswith('#!') and 'python' in first_line):
            try:
                tree = ast.parse(text, filename=rel_posix)
            except (SyntaxError, ValueError, RecursionError):
                # Python cannot run what it cannot parse, but a pasted install line is still found
                # by the line scan below, so the file is read as text rather than left NO-DATA.
                tree = None
        if tree is not None:
            commands = _python_process_commands(tree)
        else:
            comment_prefixes = ('#', '//') if path.suffix.lower() in ('.js', '.ts', '.mjs', '.cjs', '.swift') else ('#',)
            commands = ((number, line) for number, line in enumerate(text.splitlines(), 1)
                        if not line.lstrip().startswith(comment_prefixes))
        for line_no, line in commands:
            match = _EXACT_INSTALL_RE.search(line)
            if match is not None:
                findings.append(classify_runtime_hit(rel, line_no, match.group(0)))
                continue
            if _broad_install_shaped(line):
                findings.append(_runtime_finding(rel_posix, line_no, line.strip(), 'unknown_installer_family'))
    return findings


def _module_is_first_party(name, repo_root):
    _require_str(name, 'name')
    _require_path(repo_root, 'repo_root')
    text = str(name)
    candidates = (
        repo_root / (text + '.py'),
        repo_root / text / '__init__.py',
        repo_root / 'src' / (text + '.py'),
        repo_root / 'src' / text / '__init__.py',
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return True
        except OSError:
            continue
    try:
        spec = importlib.util.find_spec(text)
    except (ImportError, ValueError, OSError, TypeError, AttributeError, RuntimeError):
        return False
    if spec is None:
        return False
    origin = getattr(spec, 'origin', None)
    if not isinstance(origin, str) or not origin:
        return False
    try:
        origin_path = pathlib.Path(origin).resolve()
        origin_path.relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _module_is_stdlib(name):
    _require_str(name, 'name')
    text = str(name)
    if text in sys.builtin_module_names:
        return True
    try:
        spec = importlib.util.find_spec(text)
    except (ImportError, ValueError, OSError, TypeError, AttributeError, RuntimeError):
        return False
    if spec is None:
        return False
    origin = getattr(spec, 'origin', None)
    if not isinstance(origin, str) or not origin:
        return False
    stdlib_path = sysconfig.get_paths().get('stdlib')
    if not stdlib_path:
        return False
    try:
        origin_path = pathlib.Path(origin).resolve()
        origin_path.relative_to(pathlib.Path(stdlib_path).resolve())
    except (OSError, ValueError):
        return False
    return True


def _collect_py_imports(path):
    _require_path(path, 'path')
    try:
        data = path.read_bytes()
        text = data.decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return (None, 'unreadable: %s' % type(exc).__name__)
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        return (None, 'unparseable_source: %s' % type(exc).__name__)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = getattr(alias, 'name', None)
                if not isinstance(module, str):
                    continue
                top = module.split('.', 1)[0]
                if top:
                    names.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            module = node.module
            if not isinstance(module, str) or not module or module.startswith('.'):
                continue
            top = module.split('.', 1)[0]
            if top:
                names.append(top)
    return (names, None)


def _collect_js_imports(path):
    _require_path(path, 'path')
    try:
        data = path.read_bytes()
        text = data.decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        return (None, 'unreadable: %s' % type(exc).__name__)
    names = []
    for match in _JS_IMPORT_RE.finditer(text):
        spec = match.group(1)
        if spec is None:
            spec = match.group(2)
        if not isinstance(spec, str) or not spec:
            continue
        if spec.startswith('.') or spec.startswith('/'):
            continue
        names.append(spec)
    return (names, None)


def reconcile_imports_against_rows(rows, repo_root):
    if not isinstance(rows, list):
        raise ValueError('rows must be a list')
    _require_path(repo_root, 'repo_root')
    if not repo_root.is_dir():
        raise ValueError('repo_root is not a folder: %s' % repo_root)
    findings = []
    row_names = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('row %d is not a dict' % idx)
        name = row.get('name')
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            findings.append(_finding('BLOCK', 'import_bad_row_name', 'row %d has an invalid name' % idx))
            continue
        row_names.add(name)
    imported_names = set()
    for path in iter_scan_files(repo_root):
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        suffix = path.suffix.lower()
        if suffix == '.py':
            names, err = _collect_py_imports(path)
        elif suffix in ('.js', '.ts', '.mjs', '.cjs'):
            names, err = _collect_js_imports(path)
        else:
            continue
        if err is not None:
            code = 'unparseable_source'
            if err.startswith('unreadable'):
                code = 'unreadable_source'
            findings.append(_finding('NO-DATA', code, '%s: %s' % (rel.as_posix(), err)))
            continue
        for name in names or []:
            imported_names.add(name)
    manifest_names = set()
    for manifest in list_manifest_paths(repo_root):
        if not _is_dependency_manifest(manifest):
            continue
        target = manifest
        if not target.is_absolute():
            target = repo_root / manifest
        if not target.exists():
            continue
        deps, err = _parse_dependency_manifest(target)
        if err is not None:
            findings.append(_finding('NO-DATA', 'import_manifest_unparsable', '%s: %s' % (target, err)))
            continue
        for dep in deps or set():
            manifest_names.add(dep)
    third_party = set()
    for name in sorted(imported_names):
        if _module_is_first_party(name, repo_root):
            continue
        if _module_is_stdlib(name):
            continue
        third_party.add(name)
    for name in sorted(third_party):
        if name not in row_names:
            findings.append(_finding('BLOCK', 'import_not_in_rows', 'imported package %r has no row' % (name,), package=name))
    for name in sorted(row_names):
        if name in imported_names:
            continue
        if name in manifest_names:
            continue
        findings.append(_finding('NO-DATA', 'row_unreferenced', 'row %s has no import and no manifest entry' % name, row_name=name))
    return findings


# --- L5f-e frozen audit record and doc ---

_FROZEN_BEGIN = 'FROZEN MANIFEST l5f-supply-chain-audit-v1 BEGIN'
_FROZEN_END = 'FROZEN MANIFEST l5f-supply-chain-audit-v1 END'
_FROZEN_SCHEMA = 'l5f-supply-chain-audit-v1'
_FROZEN_REQUIRED_KEYS = ('header', 'rows', 'runtime_findings', 'verdict')
_FROZEN_PATH_FIELDS = ('pin_location', 'lockfile_path', 'raw_snippet_path')
_SIGNOFF_OWNERS = ('H1', 'H2', 'H3')
_SIGNOFF_RE = re.compile(r'^H(1|2|3) SIGNOFF: (PENDING|APPROVED by .+ on \d{4}-\d{2}-\d{2})$')
_APPROVED_RE = re.compile(r'^APPROVED by (?P<who>.*) on (?P<date>\d{4}-\d{2}-\d{2})$')


# --- L5f-f: gate integration, required_fast placement, offline run ---

_AUDIT_DOC_REL = "docs/architecture/L5F-SUPPLY-CHAIN-AUDIT.md"
_USAGE = "usage: supply_chain_gate.py --offline --repo PATH [--online]"
_SUPPLY_CHAIN_GATE_NAME = "supply-chain-gate"
_RUN_CHECK_LINE_RE = re.compile(r'^[ \t]*run_check[ \t]+"([^"]*)"')
_SUMMARY_ECHO_PREFIX = 'echo "pass $pass'


def required_fast_text(repo_root):
    """Read scripts/required_fast.sh as UTF-8 text. Refuses a non Path arg,
    a missing file, and non UTF-8 bytes with its own deliberate errors."""
    if not isinstance(repo_root, pathlib.Path):
        raise ValueError(
            "repo_root must be a pathlib.Path, got %s" % type(repo_root).__name__
        )
    path = repo_root / "scripts" / "required_fast.sh"
    if not path.is_file():
        raise FileNotFoundError("required_fast.sh not found at %s" % path)
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("required_fast.sh is not valid UTF-8: %s" % exc)


def find_run_check_lines(text):
    """Return [(1 based line number, line text)] for every run_check line."""
    if not isinstance(text, str):
        raise ValueError("text must be a str, got %s" % type(text).__name__)
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _RUN_CHECK_LINE_RE.match(line) is not None:
            found.append((number, line))
    return found


def assert_gate_placement(text):
    """Raise ValueError unless the supply-chain-gate run_check line appears
    exactly once, carries --offline and --repo ., and precedes the summary
    echo line."""
    if not isinstance(text, str):
        raise ValueError("text must be a str, got %s" % type(text).__name__)
    gate_lines = []
    for number, line in find_run_check_lines(text):
        if _SUPPLY_CHAIN_GATE_NAME in line:
            gate_lines.append((number, line))
    if not gate_lines:
        raise ValueError(
            "required_fast.sh is missing the %s run_check line" % _SUPPLY_CHAIN_GATE_NAME
        )
    if len(gate_lines) > 1:
        raise ValueError(
            "required_fast.sh declares %s %d times, expected exactly 1"
            % (_SUPPLY_CHAIN_GATE_NAME, len(gate_lines))
        )
    line_number, gate_line = gate_lines[0]
    if "--offline" not in gate_line:
        raise ValueError(
            "%s run_check line is missing --offline" % _SUPPLY_CHAIN_GATE_NAME
        )
    if "--repo ." not in gate_line:
        raise ValueError(
            "%s run_check line is missing --repo ." % _SUPPLY_CHAIN_GATE_NAME
        )
    summary_number = None
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith(_SUMMARY_ECHO_PREFIX):
            summary_number = number
            break
    if summary_number is None:
        raise ValueError("required_fast.sh has no summary echo line")
    if line_number >= summary_number:
        raise ValueError(
            "%s run_check line sits at or after the summary echo" % _SUPPLY_CHAIN_GATE_NAME
        )


def _reason_for_finding(finding):
    if isinstance(finding, dict):
        reason = finding.get("reason")
        if isinstance(reason, str) and reason:
            return reason
        code = finding.get("code")
        if isinstance(code, str) and code:
            return code
        return str(finding)
    return str(finding)


def run_offline_gate(repo_root, now):
    """Section 4.1 steps 1 to 10. Returns (exit_code, summary_text).
    Corrupt, missing or unreadable input yields NO-DATA, never PASS."""
    if not isinstance(repo_root, pathlib.Path):
        return 2, "NO-DATA: repo"
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        return 2, "NO-DATA: now"
    if not repo_root.is_dir():
        return 2, "NO-DATA: repo"
    doc_path = repo_root / _AUDIT_DOC_REL
    if not doc_path.is_file():
        return 2, "NO-DATA: audit doc absent"
    doc_text = doc_path.read_bytes().decode("utf-8")
    try:
        block = extract_frozen_block(doc_text)
    except Exception as exc:
        return 2, "NO-DATA: %s" % type(exc).__name__
    if not isinstance(block, str) or not block.strip():
        return 2, "NO-DATA: frozen block missing"
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError as exc:
        return 2, "NO-DATA: JSON parse error at line %d" % exc.lineno
    if not isinstance(parsed, dict):
        return 2, "NO-DATA: top level is not an object"
    header = parsed.get("header")
    if not isinstance(header, dict):
        return 2, "NO-DATA: header is not an object"
    if header.get("schema") != _FROZEN_SCHEMA:
        return 2, "NO-DATA: schema mismatch"
    rows = parsed.get("rows")
    if not isinstance(rows, list):
        return 2, "NO-DATA: rows is not a list"
    try:
        manifests = list_manifest_paths(repo_root)
    except Exception as exc:
        return 2, "NO-DATA: %s" % type(exc).__name__
    try:
        runtime_findings = scan_runtime_installs(repo_root)
    except Exception as exc:
        return 2, "NO-DATA: %s" % type(exc).__name__
    findings = []
    try:
        findings.extend(verify_manifest_coverage(rows, manifests, repo_root))
    except Exception as exc:
        findings.append({"severity": "NO-DATA", "code": "verify_manifest_coverage", "reason": type(exc).__name__})
    try:
        findings.extend(verify_pins(rows, manifests, repo_root))
    except Exception as exc:
        findings.append({"severity": "NO-DATA", "code": "verify_pins", "reason": type(exc).__name__})
    try:
        findings.extend(verify_lockfiles(rows, repo_root))
    except Exception as exc:
        findings.append({"severity": "NO-DATA", "code": "verify_lockfiles", "reason": type(exc).__name__})
    try:
        findings.extend(verify_licenses_offline(rows, repo_root))
    except Exception as exc:
        findings.append({"severity": "NO-DATA", "code": "verify_licenses_offline", "reason": type(exc).__name__})
    try:
        findings.extend(verify_freshness(rows, repo_root, now))
    except Exception as exc:
        findings.append({"severity": "NO-DATA", "code": "verify_freshness", "reason": type(exc).__name__})
    if isinstance(runtime_findings, list):
        for runtime_finding in runtime_findings:
            if not isinstance(runtime_finding, dict):
                findings.append({"severity": "NO-DATA", "code": "runtime_finding_shape", "reason": "not a dict"})
                continue
            severity = _RUNTIME_SEVERITY.get(runtime_finding.get("classification"), "NO-DATA")
            findings.append({"severity": severity, "code": "runtime_finding", "reason": str(runtime_finding.get("file_path"))})
    blocks = [f for f in findings if isinstance(f, dict) and f.get("severity") == "BLOCK"]
    no_data = [f for f in findings if isinstance(f, dict) and f.get("severity") == "NO-DATA"]
    if blocks:
        return 1, "BLOCK: %d finding(s): %s" % (len(blocks), "; ".join(_reason_for_finding(f) for f in blocks))
    if no_data:
        return 2, "NO-DATA: %s" % "; ".join(_reason_for_finding(f) for f in no_data)
    if header.get("l3b_landed") is False:
        # REQ-L3B as written on 2026-09-21 forced NO-DATA while L3b (the vault
        # web UI) had not landed, on the premise that it would land inside
        # this epic. On 2026-09-29 the owner deferred L3b to 1.1.2, and a
        # merge gate that reads an unlanded, deferred unit as missing
        # evidence blocked every merge into main (2026-09-30). The deferral
        # is recorded in the frozen header as l3b_deferred_to, a release
        # string, and is named in the PASS line; a header that says the
        # unit has not landed and names no deferral still reads NO-DATA,
        # exactly as before.
        deferred_to = header.get("l3b_deferred_to")
        if not isinstance(deferred_to, str) or not deferred_to.strip():
            return 2, "NO-DATA: l3b not landed"
        return 0, ("PASS: %d rows, 0 findings; l3b vault rows out of scope, deferred to %s"
                   % (len(rows), deferred_to.strip()))
    return 0, "PASS: %d rows, 0 findings" % len(rows)


def main(argv=None):
    try:
        args = list(argv) if argv is not None else list(sys.argv[1:])
        offline = False
        online = False
        repo = None
        index = 0
        while index < len(args):
            token = args[index]
            if token == "--offline":
                offline = True
            elif token == "--online":
                online = True
            elif token == "--repo":
                index += 1
                if index >= len(args):
                    sys.stdout.write(_USAGE + "\n")
                    return 2
                repo = args[index]
            elif token.startswith("--repo="):
                repo = token[len("--repo="):]
            else:
                sys.stdout.write(_USAGE + "\n")
                return 2
            index += 1
        if not offline and not online:
            sys.stdout.write(_USAGE + "\n")
            return 2
        if repo is None:
            sys.stdout.write(_USAGE + "\n")
            return 2
        repo_root = pathlib.Path(repo)
        now = datetime.datetime.now(datetime.timezone.utc)
        code, summary = run_offline_gate(repo_root, now)
        sys.stdout.write(summary + "\n")
        return code
    except SystemExit:
        raise
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s\n" % type(exc).__name__)
        return 2





def extract_frozen_block(doc_text: str) -> str:
    """Return the exact text between the BEGIN and END frozen manifest markers."""
    if not isinstance(doc_text, str):
        raise ValueError('doc_text must be a str')
    begin = doc_text.find(_FROZEN_BEGIN)
    if begin < 0:
        raise ValueError('missing BEGIN marker')
    end = doc_text.find(_FROZEN_END)
    if end < 0:
        raise ValueError('missing END marker')
    if end < begin:
        raise ValueError('END marker before BEGIN marker')
    return doc_text[begin + len(_FROZEN_BEGIN):end]


def load_frozen_audit(doc_path: pathlib.Path) -> dict:
    """Read doc_path as bytes, extract the frozen block and parse it as JSON."""
    if not isinstance(doc_path, pathlib.Path):
        raise ValueError('doc_path must be a pathlib.Path')
    try:
        raw = doc_path.read_bytes()
    except OSError as exc:
        raise ValueError('cannot read doc: %s' % type(exc).__name__)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('doc is not utf-8: %s' % type(exc).__name__)
    block = extract_frozen_block(text)
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError as exc:
        raise ValueError('JSON parse error at line %d' % exc.lineno)
    if not isinstance(parsed, dict):
        raise ValueError('frozen block is not a JSON object')
    return parsed


def frozen_audit_findings(audit) -> list:
    """Return BLOCK and NO-DATA findings for a parsed frozen audit dict."""
    if not isinstance(audit, dict):
        raise ValueError('audit must be a dict')
    findings = []
    for key in _FROZEN_REQUIRED_KEYS:
        if key not in audit:
            findings.append(_finding('BLOCK', 'missing_top_level_key', 'missing top level key: %s' % key))
    header = audit.get('header')
    manifests = None
    if not isinstance(header, dict):
        findings.append(_finding('BLOCK', 'header_not_object', 'header is not an object'))
    else:
        if header.get('schema') != _FROZEN_SCHEMA:
            findings.append(_finding('BLOCK', 'schema_mismatch', 'header.schema is not %s' % _FROZEN_SCHEMA))
        manifests = header.get('manifests_searched')
    rows = audit.get('rows')
    if not isinstance(rows, list):
        findings.append(_finding('BLOCK', 'rows_not_list', 'rows is not a list'))
        rows = []
    if not isinstance(audit.get('runtime_findings'), list):
        findings.append(_finding('BLOCK', 'runtime_findings_not_list', 'runtime_findings is not a list'))
    if not isinstance(audit.get('verdict'), dict):
        findings.append(_finding('BLOCK', 'verdict_not_object', 'verdict is not an object'))
    if not rows and (not isinstance(manifests, list) or not manifests):
        findings.append(_finding('BLOCK', 'empty_manifests_searched', 'rows empty and manifests_searched empty'))
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            findings.append(_finding('BLOCK', 'row_not_object', 'row %d is not an object' % idx))
            continue
        for field in _FROZEN_PATH_FIELDS:
            value = row.get(field)
            if isinstance(value, str) and '\n' in value:
                findings.append(_finding('BLOCK', 'newline_in_path', 'row %d field %s carries a newline byte' % (idx, field)))
    return findings


def _frozen_block_line_indexes(lines):
    begin_idx = None
    end_idx = None
    for idx, line in enumerate(lines):
        if begin_idx is None and _FROZEN_BEGIN in line:
            begin_idx = idx
            continue
        if begin_idx is not None and end_idx is None and _FROZEN_END in line:
            end_idx = idx
            break
    if begin_idx is None or end_idx is None:
        return frozenset()
    return frozenset(range(begin_idx + 1, end_idx))


def verify_human_signoff(doc_text: str) -> list:
    """Read the whole document text and check the H1 H2 H3 signoff lines."""
    if not isinstance(doc_text, str):
        raise ValueError('doc_text must be a str')
    lines = doc_text.splitlines()
    inside = _frozen_block_line_indexes(lines)
    findings = []
    seen = set()
    duplicated = set()
    for idx, line in enumerate(lines):
        match = _SIGNOFF_RE.match(line)
        if match is None:
            continue
        owner = 'H' + match.group(1)
        status = match.group(2)
        if idx in inside:
            findings.append(_finding('BLOCK', 'signoff_inside_frozen_block', 'signoff line for %s is inside the frozen block' % owner))
            continue
        if owner in seen:
            if owner not in duplicated:
                duplicated.add(owner)
                findings.append(_finding('BLOCK', 'signoff_duplicate:%s' % owner, 'duplicate signoff line for %s' % owner))
            continue
        seen.add(owner)
        if status == 'PENDING':
            findings.append(_finding('NO-DATA', 'signoff_pending:%s' % owner, 'signoff line for %s is PENDING' % owner))
            continue
        approved = _APPROVED_RE.match(status)
        if approved is None:
            findings.append(_finding('BLOCK', 'signoff_no_approver', 'signoff line for %s has no approver' % owner))
            continue
        approver = approved.group('who').strip()
        if not approver:
            findings.append(_finding('BLOCK', 'signoff_no_approver', 'signoff line for %s has an empty approver' % owner))
            continue
        try:
            signoff_date = datetime.datetime.strptime(approved.group('date'), '%Y-%m-%d').date()
        except ValueError:
            findings.append(_finding('BLOCK', 'signoff_future_date', 'signoff line for %s carries an invalid date' % owner))
            continue
        now_date = datetime.datetime.now(datetime.timezone.utc).date()
        if signoff_date > now_date:
            findings.append(_finding('BLOCK', 'signoff_future_date', 'signoff line for %s is dated in the future' % owner))
    for owner in _SIGNOFF_OWNERS:
        if owner not in seen:
            findings.append(_finding('BLOCK', 'signoff_missing:%s' % owner, 'missing signoff line for %s' % owner))
    return findings


if __name__ == "__main__":
    raise SystemExit(main())
