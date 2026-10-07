"""L5b.3 verify: fire prober. Runs a named test unmutated, then mutated.

Command runner. subprocess.run is called with a literal argv list whose
every element is a string constant:
    ["python3", "-m", "tools.l5b_audit.probe_runner"]
The test id never enters argv; it travels through env["L5B_TEST_ID"], and
cwd= carries the tree root. No Python source is handed to python3 on the
command line, no eval, no exec, no shell, no getattr.

Unknown, corrupt or missing input BLOCKS with a deliberate ValueError or
a NO-DATA FireResult; never a raw interpreter error and never a silent
accept.
"""
import json
import os
import subprocess
from collections import namedtuple

from tools.l5b_audit import scanner


BoundaryCall = namedtuple(
    "BoundaryCall",
    ["file", "line", "column", "symbol", "kind", "snippet", "qualname", "ordinal"],
    defaults=("<module>", 1),
)
FireEntry = namedtuple(
    "FireEntry",
    ["entry_id", "test_id", "mutation_id", "expect_substring"],
)
FireResult = namedtuple(
    "FireResult",
    ["entry_id", "test_id", "fired", "reason",
     "unmutated_code", "mutated_code", "output_tail"],
)
AnnotationClearance = namedtuple(
    "AnnotationClearance", ["call_id", "reason", "covered"],
)

_MIN_SUBSTRING_CHARS = 8
_MIN_PROPAGATE_REASON = 12
_DEFAULT_TIMEOUT = 240
_PROPAGATE_MARKER = "# l5b: PROPAGATE_SAFE:"
_MUTATIONS_REL = os.path.join("tools", "l5b_audit", "mutations.json")


def _require_call(call):
    if not isinstance(call, BoundaryCall):
        raise ValueError("call must be a BoundaryCall, got %s" % type(call).__name__)
    if not isinstance(call.file, str) or not call.file:
        raise ValueError("call.file must be a non-empty string")
    if isinstance(call.line, bool) or not isinstance(call.line, int) or call.line < 1:
        raise ValueError("call.line must be a positive integer")
    if isinstance(call.column, bool) or not isinstance(call.column, int) or call.column < 1:
        raise ValueError("call.column must be a positive integer")
    if not isinstance(call.symbol, str) or not call.symbol:
        raise ValueError("call.symbol must be a non-empty string")
    if not isinstance(call.kind, str) or not call.kind:
        raise ValueError("call.kind must be a non-empty string")
    if not isinstance(call.snippet, str):
        raise ValueError("call.snippet must be a string")
    if not isinstance(call.qualname, str) or not call.qualname:
        raise ValueError("call.qualname must be a non-empty string")
    if isinstance(call.ordinal, bool) or not isinstance(call.ordinal, int) or call.ordinal < 1:
        raise ValueError("call.ordinal must be a positive integer")


def _call_id(call):
    return scanner.call_id(call.file, call.qualname, call.symbol, call.ordinal)


def _safe_join(root, rel):
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    if not isinstance(rel, str) or not rel:
        return None
    if os.path.isabs(rel):
        return None
    norm = os.path.normpath(rel)
    if norm.startswith("..") or os.sep + ".." + os.sep in norm or norm.endswith(os.sep + ".."):
        return None
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, norm))
    if full != root_real and not full.startswith(root_real + os.sep):
        return None
    return full


def _read_bytes(path):
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _read_text(path):
    raw = _read_bytes(path)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("not utf-8: %s" % exc)


def _read_json(path, label):
    if not isinstance(path, str) or not path:
        raise ValueError("%s path must be a non-empty string" % label)
    raw = _read_bytes(path)
    if raw is None:
        raise ValueError("cannot read %s %r" % (label, path))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("%s %r is not utf-8: %s" % (label, path, exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("%s %r is not valid JSON: %s" % (label, path, exc))
    return data


def _load_mutations(path):
    data = _read_json(path, "mutations file")
    if not isinstance(data, list):
        raise ValueError("mutations file must hold a JSON array")
    indexed = {}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError("mutation entry %d must be an object" % i)
        mid = item.get("id")
        if not isinstance(mid, str) or not mid:
            raise ValueError("mutation entry %d needs a non-empty string id" % i)
        if mid in indexed:
            raise ValueError("duplicate mutation id: %s" % mid)
        rel = item.get("path")
        if not isinstance(rel, str) or not rel:
            raise ValueError("mutation %s path must be a non-empty string" % mid)
        find = item.get("find")
        if not isinstance(find, str) or not find:
            raise ValueError("mutation %s find must be a non-empty string" % mid)
        repl = item.get("replace")
        if not isinstance(repl, str):
            raise ValueError("mutation %s replace must be a string" % mid)
        indexed[mid] = {"path": rel, "find": find, "replace": repl}
    return indexed


def load_fire_map(path):
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    data = _read_json(path, "fire map")
    if not isinstance(data, list):
        raise ValueError("fire map must hold a JSON array")
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError("fire map entry %d must be an object" % i)
        for key in ("entry_id", "test_id", "mutation_id", "expect_substring"):
            if key not in item:
                raise ValueError("fire map entry %d missing %s" % (i, key))
            if not isinstance(item[key], str):
                raise ValueError("fire map entry %d %s must be a string" % (i, key))
        stripped = "".join(item["expect_substring"].split())
        if len(stripped) < _MIN_SUBSTRING_CHARS:
            continue
        if not item["entry_id"] or not item["test_id"] or not item["mutation_id"]:
            raise ValueError("fire map entry %d has an empty id field" % i)
        out.append(FireEntry(
            item["entry_id"], item["test_id"],
            item["mutation_id"], item["expect_substring"],
        ))
    return tuple(out)


def _run_unittest(root, test_id, timeout=_DEFAULT_TIMEOUT):
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    if not isinstance(test_id, str) or not test_id:
        raise ValueError("test_id must be a non-empty string")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("timeout must be a positive integer")
    env = dict(os.environ)
    env["L5B_TEST_ID"] = test_id
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(["python3", "-m", "tools.l5b_audit.probe_runner"], cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ("timeout", "")
    except OSError as exc:
        return (2, "OSError: %s" % exc)
    stdout = proc.stdout if isinstance(proc.stdout, str) else ""
    stderr = proc.stderr if isinstance(proc.stderr, str) else ""
    return (proc.returncode, stdout + stderr)


def _prior_line_annotation(source, call):
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    _require_call(call)
    lines = source.splitlines()
    idx = call.line - 2
    if idx < 0 or idx >= len(lines):
        return ""
    prior = lines[idx]
    pos = prior.find(_PROPAGATE_MARKER)
    if pos < 0:
        return ""
    return prior[pos + len(_PROPAGATE_MARKER):].strip()


def run_probe(entry, root):
    if not isinstance(entry, FireEntry):
        raise ValueError("entry must be a FireEntry, got %s" % type(entry).__name__)
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    if not os.path.isdir(root):
        raise ValueError("root must be a directory: %r" % root)
    sub = entry.expect_substring
    if not isinstance(sub, str):
        raise ValueError("expect_substring must be a string")
    if len("".join(sub.split())) < _MIN_SUBSTRING_CHARS:
        raise ValueError(
            "expect_substring needs >= %d non-whitespace chars" % _MIN_SUBSTRING_CHARS)
    if not isinstance(entry.test_id, str) or not entry.test_id:
        raise ValueError("entry.test_id must be a non-empty string")
    if not isinstance(entry.mutation_id, str) or not entry.mutation_id:
        raise ValueError("entry.mutation_id must be a non-empty string")
    if not isinstance(entry.entry_id, str):
        raise ValueError("entry.entry_id must be a string")
    mut_path = os.path.join(root, _MUTATIONS_REL)
    try:
        mutations = _load_mutations(mut_path)
    except ValueError as exc:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: %s" % exc, None, None, "")
    mutation = mutations.get(entry.mutation_id)
    if mutation is None:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: mutation_id not found: %s" % entry.mutation_id,
                          None, None, "")
    target_full = _safe_join(root, mutation["path"])
    if target_full is None:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: mutation path escapes root", None, None, "")
    code_u, out_u = _run_unittest(root, entry.test_id)
    if code_u == "timeout":
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: unmutated run timed out", "timeout", None, "")
    if code_u != 0:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: unmutated run not green (exit %s)" % code_u,
                          code_u, None, out_u[-500:])
    original = _read_text(target_full)
    if original is None:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: cannot read mutation target", code_u, None, "")
    find = mutation["find"]
    count = original.count(find)
    if count != 1:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: find occurs %d times, need 1" % count,
                          code_u, None, "")
    mutated = original.replace(find, mutation["replace"], 1)
    try:
        with open(target_full, "w", encoding="utf-8") as fh:
            fh.write(mutated)
    except OSError as exc:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: write failed: %s" % exc, code_u, None, "")
    try:
        code_m, out_m = _run_unittest(root, entry.test_id)
    finally:
        try:
            with open(target_full, "w", encoding="utf-8") as fh:
                fh.write(original)
        except OSError:
            pass
    if code_m == "timeout":
        return FireResult(entry.entry_id, entry.test_id, False,
                          "NO-DATA: mutated run timed out",
                          code_u, "timeout", out_m[-500:])
    if code_m == 0:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "mutation survived", code_u, code_m, out_m[-500:])
    if sub not in out_m:
        return FireResult(entry.entry_id, entry.test_id, False,
                          "expected substring not in mutated output",
                          code_u, code_m, out_m[-500:])
    return FireResult(entry.entry_id, entry.test_id, True, "fired",
                      code_u, code_m, out_m[-500:])


def verify_all(calls, manifest, root):
    if not isinstance(calls, tuple):
        raise ValueError("calls must be a tuple")
    if not isinstance(manifest, str) or not manifest:
        raise ValueError("manifest must be a non-empty string")
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    if not os.path.isdir(root):
        raise ValueError("root must be a directory: %r" % root)
    entries = load_fire_map(manifest)
    by_entry = {}
    for e in entries:
        by_entry[e.entry_id] = e
    # Scanner ids are unique by construction since 2026-10-03 (an ordinal per symbol per function); this guard stays
    # for hand built calls, where two records of one id made the audit refuse the whole fires file on 2026-10-01.
    # An ambiguous id never fires: it is NO-DATA, named, for every call that carries it.
    seen = {}
    for call in calls:
        _require_call(call)
        seen[_call_id(call)] = seen.get(_call_id(call), 0) + 1
    results = []
    for call in calls:
        cid = _call_id(call)
        entry = by_entry.get(cid)
        if entry is not None and seen[cid] > 1:
            results.append(FireResult(
                cid, entry.test_id, False,
                "NO-DATA: %d scanned calls share the id %s, so a probe cannot name which one it exercised" % (seen[cid], cid),
                None, None, ""))
            continue
        if entry is None:
            results.append(FireResult(
                cid, "", False, "NO-DATA: no fire entry for %s" % cid,
                None, None, ""))
            continue
        results.append(run_probe(entry, root))
    return tuple(results)


def annotation_clearances(calls, root):
    if not isinstance(calls, tuple):
        raise ValueError("calls must be a tuple")
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    result = []
    for call in calls:
        _require_call(call)
        full = _safe_join(root, call.file)
        if full is None:
            raise ValueError("unsafe call.file: %r" % call.file)
        source = _read_text(full)
        if source is None:
            raise ValueError("cannot read %r" % call.file)
        reason = _prior_line_annotation(source, call)
        result.append(AnnotationClearance(
            call_id=_call_id(call),
            reason=reason,
            covered=len(reason) >= _MIN_PROPAGATE_REASON,
        ))
    return tuple(result)


# L5b.7: the command that records the fires file. Everything below runs the
# probes in a disposable copy of the repository, never in the live tree, and
# writes one JSON record the audit (scripts/l5b_audit.py) judges.
import argparse
import shutil
import sys
import tempfile

_FIRES_SCHEME = "l5b-fires-v1"
_FIRE_MAP_REL = os.path.join("tools", "l5b_audit", "fire_map.json")


def _disposable_copy(repo, tops):
    if not isinstance(repo, str) or not os.path.isdir(repo):
        raise ValueError("repo must be an existing directory")
    if not isinstance(tops, list) or not tops or not all(isinstance(t, str) and t for t in tops):
        raise ValueError("tops must be a non-empty list of directory names")
    for top in tops:
        if not os.path.isdir(os.path.join(repo, top)):
            raise ValueError("needed directory is missing under the repository: %r" % top)
    copy = tempfile.mkdtemp(prefix="l5b-fires-")
    try:
        for top in sorted(set(tops)):
            shutil.copytree(os.path.join(repo, top), os.path.join(copy, top),
                            ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc"))
        # code under test finds its repository by walking up to .git (mode/store.py _repo_root_from); an empty marker
        # makes the copy resolve to itself, as the real checkout does, and never to whatever repository sits above it.
        os.mkdir(os.path.join(copy, ".git"))
    except (OSError, shutil.Error) as exc:
        shutil.rmtree(copy, ignore_errors=True)
        raise ValueError("the disposable copy could not be made: %s" % exc)
    return copy


def _scan_calls(copy_root, scan_root):
    if not isinstance(copy_root, str) or not os.path.isdir(copy_root):
        raise ValueError("copy_root must be an existing directory")
    if not isinstance(scan_root, str) or not scan_root:
        raise ValueError("scan_root must be a non-empty string")
    records = scanner.scan_tree(os.path.join(copy_root, scan_root))
    calls = []
    for record in records:
        source = _read_text(os.path.join(copy_root, scan_root, record.file))
        lines = source.splitlines() if source is not None else []
        snippet = lines[record.line - 1] if 0 < record.line <= len(lines) else ""
        calls.append(BoundaryCall(
            file="%s/%s" % (scan_root.replace(os.sep, "/").rstrip("/"), record.file),
            line=record.line, column=record.column + 1, symbol=record.symbol,
            kind=record.kind, snippet=snippet, qualname=record.qualname, ordinal=record.ordinal))
    return tuple(calls)


def _fires_payload(results, bypass, clearances):
    if not isinstance(results, tuple) or not isinstance(bypass, list) or not isinstance(clearances, tuple):
        raise ValueError("results and clearances must be tuples, bypass a list")
    return {
        "scheme_version": _FIRES_SCHEME,
        "fires": [{"entry_id": r.entry_id, "test_id": r.test_id, "fired": bool(r.fired), "reason": r.reason}
                  for r in results],
        "checker_bypass": list(bypass),
        "annotation_clearances": [{"call_id": c.call_id, "reason": c.reason, "covered": bool(c.covered)}
                                  for c in clearances],
    }


def _no_data(message):
    sys.stderr.write("NO-DATA: %s\n" % message)
    return 2


def main(argv=None):
    if argv is not None and (not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv)):
        return _no_data("argv must be None or a list of strings")
    parser = argparse.ArgumentParser(prog="tools.l5b_audit.verify", add_help=False)
    parser.add_argument("--root")
    parser.add_argument("--out")
    parser.add_argument("--repo")
    parser.add_argument("--fire-map")
    try:
        args, extras = parser.parse_known_args(list(argv) if argv is not None else None)
    except SystemExit:
        return _no_data("argv could not be parsed")
    if extras or not args.root or not args.out:
        return _no_data("usage: --root DIR --out FIRES_JSON [--repo DIR] [--fire-map PATH]")
    repo = os.path.abspath(args.repo or os.getcwd())
    if not os.path.isdir(repo):
        return _no_data("repo is not a directory: %r" % repo)
    root = args.root.replace(os.sep, "/").strip("/")
    if os.path.isabs(args.root) or not root or ".." in root.split("/"):
        return _no_data("root must be a relative path inside the repository: %r" % args.root)
    if not os.path.isdir(os.path.join(repo, root)):
        return _no_data("root is not a directory under the repository: %r" % root)
    out = os.path.abspath(args.out)
    if not os.path.isdir(os.path.dirname(out)):
        return _no_data("the out directory is missing: %r" % os.path.dirname(out))
    fire_map = os.path.abspath(args.fire_map) if args.fire_map else os.path.join(repo, _FIRE_MAP_REL)
    try:
        entries = load_fire_map(fire_map)
        mutations = _load_mutations(os.path.join(repo, _MUTATIONS_REL))
    except ValueError as exc:
        return _no_data(str(exc))
    # The whole tree, never a hand picked subset (2026-10-01): with only plugin/ and tools/ copied, every module that
    # resolves its repository (products/, scripts/) failed its own tests in the copy, so 300 product calls could never
    # be probed at all. The named tops stay required, so a missing one is still refused by name.
    tops = [root.split("/")[0], "tools"]
    tops += [m["path"].replace(os.sep, "/").split("/")[0] for m in mutations.values()]
    tops += [e.test_id.split(".")[0] for e in entries]
    tops += [d for d in os.listdir(repo) if d != ".git" and os.path.isdir(os.path.join(repo, d))]
    try:
        copy = _disposable_copy(repo, tops)
    except ValueError as exc:
        return _no_data(str(exc))
    try:
        calls = _scan_calls(copy, root)
        results = verify_all(calls, fire_map, copy)
        # Owner decision 2026-10-02 (option A): a call is checked when its injected failure turns a named test red, so a
        # fired call is never a bypass. The line-order text rule this replaced marked 281 raising facades unchecked.
        bypass = []
        clearances = annotation_clearances(calls, copy)
        payload = _fires_payload(results, bypass, clearances)
        text = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".l5b-fires-", dir=os.path.dirname(out))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, out)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass   # sbe: allow-silent the temp is already gone; the refusal below is what is reported
            raise
    except ValueError as exc:
        return _no_data(str(exc))
    except OSError as exc:
        return _no_data("the fires file could not be written: %s" % exc)
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    fired = sum(1 for r in results if r.fired)
    print("FIRES %s: %d call(s), %d fired, %d bypass" % (out, len(results), fired, len(bypass)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
