"""Real CLI wiring for openrouter_dispatch.py: the command this session
(and any future one) actually runs instead of calling
~/.claude/bin/or_ask.py directly, so every real OpenRouter call from
now on goes through the four gates (ledger, strict fallback check,
capability quarantine, dispatch semaphore) rather than being
untested-in-anger library code.

Usage:
    python3 -m plugin.runtime.brother.core.or_dispatch_cli \
        --model muse --effort high --max 6000 --timeout 300 \
        --holder-id my-review --estimated-cost 0.05 \
        "the real prompt text"

    echo '{"state": {...}, "questions": {...}}' | \
    python3 -m plugin.runtime.brother.core.or_dispatch_cli \
        --model jev --decisions --jev-type noul \
        --holder-id my-decision --estimated-cost 0.001
"""

import argparse
import importlib
import os
import sys

from plugin.runtime.brother.core import repo_paths
from plugin.runtime.brother.core.openrouter_dispatch import DispatchResult, dispatch
from plugin.runtime.brother.core.openrouter_strict import (
    FallbackDetected, TimeoutTooLow, MaxTokensTooLow,
)
from plugin.runtime.brother.core.openrouter_ledger import BudgetExceeded
from plugin.runtime.brother.core.model_capability_profile import (
    QuarantinedCapability,
)
from plugin.runtime.brother.core.dispatch_semaphore import NoSlotAvailable


BRIDGE_PATH = os.path.expanduser("~/.claude/bin/or_ask.py")   # the installed bridge under HOME, never a fixed home path

def _dispatch_ids():
    """({alias: the id the bridge's own [usage] line reports}, reason or None): what run_strict's fallback check
    compares against, read from the registry's bridge rows (`answers_as`, else `id`) through the loop's own router
    (FX-31.6). The ids used to be typed here, and jev's dated id lived here alone while the bridge alias and the
    registry carried the undated one. The router is imported from the scripts/loop directory above this file, on
    sys.path for this one import only, as or_fanout does. FAIL DIRECTION: no router above this file, or a registry
    the router refuses, leaves the table EMPTY (nothing offered, nothing accepted: an allowlist with no rows narrows
    to nothing) and the reason beside it; main() refuses on that reason before it parses a flag. The table never
    falls back to a typed list, and or_fanout, which imports this name at load, keeps importing, which its own
    router-optional contract requires."""
    loop = repo_paths.repo_loop_dir(__file__)
    if loop is None:
        return {}, "no scripts/loop/model_router.py above %s, so no bridge model id can be named" % __file__
    sys.path.insert(0, loop)
    try:
        router = importlib.import_module("model_router")
    except ImportError as exc:
        return {}, "model_router could not be imported from %s: %s" % (loop, exc)
    finally:
        sys.path.remove(loop)
    try:
        return router.derive_model_views(router.registry())["dispatch_ids"], None
    except router.Refused as exc:
        return {}, "the model registry refuses, so no bridge model id can be named: %s" % exc


REAL_MODEL_IDS, REGISTRY_ERROR = _dispatch_ids()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("prompt", nargs="?", default=None,
                         help="prompt text (omit to read stdin, required for --decisions)")
    parser.add_argument("--model", required=True, choices=sorted(REAL_MODEL_IDS))
    parser.add_argument("--effort", default="high")
    parser.add_argument("--max", type=int, default=6000, dest="max_tokens")
    parser.add_argument("--timeout", type=int, default=300, dest="timeout_seconds")
    parser.add_argument("--decisions", action="store_true")
    parser.add_argument("--jev-type", default=None,
                         help="required with --model jev: must be 'noul', "
                              "the only confirmed-working type (OR-3)")
    parser.add_argument("--holder-id", required=True)
    parser.add_argument("--estimated-cost", type=float, default=0.05)
    # no --daily-cap (D2.6 REQ-FAN-5): the deployment policy and the owner grant decide the cap; a caller flag
    # cannot move it, and argparse refuses one with its usage error rather than accepting a flag that does nothing
    parser.add_argument("--allow-fallback", action="store_true")
    return parser


def check_deps(manifest_paths):
    """Review procedure for L5a-5: dependency pin check across manifests.

    Returns a dict only when every manifest in the list is OK: a
    requirements*.txt pinned line by line with `==` plus `--hash=`, or a
    lock file (poetry.lock / Pipfile.lock) inherently hash-pinned by its
    own format. Everything else BLOCKS by raising ValueError, never a
    normal value: an empty list, a non-list, a non-string element, an
    empty path, a missing or non-file path, an unreadable file, a
    non-utf8 file, an unsupported manifest type, and any requirements
    line missing an `==` pin (including a range operator such as `>=`,
    `~=`, `>` or `<`) or missing an inline `--hash=`.
    """
    if not isinstance(manifest_paths, list):
        raise ValueError(
            "manifest_paths must be a list, got %s"
            % (type(manifest_paths).__name__,)
        )
    if not manifest_paths:
        raise ValueError(
            "empty manifest set is NO-DATA and BLOCKS, never PASS by absence"
        )
    results = []
    for path in manifest_paths:
        if not isinstance(path, str):
            raise ValueError(
                "manifest path must be a string, got %s"
                % (type(path).__name__,)
            )
        if not path:
            raise ValueError("manifest path is empty")
        if not os.path.isfile(path):
            raise ValueError(
                "manifest is missing or not a file: %r" % (path,)
            )
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as exc:
            raise ValueError(
                "manifest unreadable: %r (%s)"
                % (path, type(exc).__name__)
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("manifest is not utf-8: %r" % (path,))
        base = os.path.basename(path)
        if base in ("poetry.lock", "Pipfile.lock"):
            results.append({
                "path": path,
                "status": "OK",
                "reason": "lock file inherently hash-pinned",
                "findings": [],
            })
            continue
        if not (base.startswith("requirements") and base.endswith(".txt")):
            raise ValueError(
                "unsupported manifest type (requires export to requirements "
                "format): %r" % (path,)
            )
        findings = []
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("-"):
                continue
            has_eq = "==" in stripped
            has_hash = "--hash=" in stripped
            if not has_eq:
                findings.append({
                    "line": lineno,
                    "message": "missing == pin",
                    "line_content": stripped,
                })
            if not has_hash:
                findings.append({
                    "line": lineno,
                    "message": "missing inline --hash",
                    "line_content": stripped,
                })
        if findings:
            raise ValueError(
                "unpinned dependency in %r: %s"
                % (path, "; ".join(f["message"] for f in findings))
            )
        results.append({
            "path": path,
            "status": "OK",
            "reason": "all lines pinned with == and hash",
            "findings": [],
        })
    return {"manifests": results, "blocking": False, "status": "PASS"}


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if isinstance(argv, tuple):
        argv = list(argv)
    if not isinstance(argv, list):
        print(
            "USAGE: argv must be a list of strings, got %s"
            % (type(argv).__name__,),
            file=sys.stderr,
        )
        return 1
    for item in argv:
        if not isinstance(item, str):
            print(
                "USAGE: every argv element must be a string, got %s"
                % (type(item).__name__,),
                file=sys.stderr,
            )
            return 1
    if REGISTRY_ERROR:
        # no table to choose from: refused before a flag is parsed, naming why (FX-31.6), never a typed fallback
        print("REFUSED: %s" % REGISTRY_ERROR, file=sys.stderr)
        return 1
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 1

    bridge_argv = [
        sys.executable, BRIDGE_PATH,
        "--model", args.model,
        "--effort", args.effort,
        "--max", str(args.max_tokens),
        "--timeout", str(args.timeout_seconds),
    ]
    if args.decisions:
        bridge_argv.append("--decisions")
    if args.prompt is not None:
        # L5a-7, row X4: the option terminator goes before the prompt, so
        # a prompt that begins with a dash is text for the bridge, never
        # a flag it parses.
        bridge_argv.append("--")
        bridge_argv.append(args.prompt)

    jev_question_type = args.jev_type if args.model == "jev" else None

    try:
        dispatched = dispatch(
            bridge_argv=bridge_argv,
            requested_model=REAL_MODEL_IDS[args.model],
            estimated_cost=args.estimated_cost,
            holder_id=args.holder_id,
            timeout_seconds=args.timeout_seconds,
            max_tokens=args.max_tokens,
            min_max_tokens=1000,
            jev_question_type=jev_question_type,
            allow_fallback=args.allow_fallback,
        )
    except QuarantinedCapability as exc:
        print("QUARANTINED: %s" % exc, file=sys.stderr)
        return 3
    except BudgetExceeded as exc:
        print("BUDGET: %s" % exc, file=sys.stderr)
        return 2
    except FallbackDetected as exc:
        print("FALLBACK: %s" % exc, file=sys.stderr)
        return 4
    except NoSlotAvailable as exc:
        print("NO-SLOT: %s" % exc, file=sys.stderr)
        return 5
    except (TimeoutTooLow, MaxTokensTooLow) as exc:
        # Found by this module's own test suite: an earlier version
        # caught only the 4 exceptions above, so a sub-floor timeout or
        # max-tokens value raised by enforce_floors() (both real,
        # documented refusals, not bugs) crashed with a raw traceback
        # instead of a clean, scriptable exit code.
        print("FLOOR: %s" % exc, file=sys.stderr)
        return 1

    if not (isinstance(dispatched, DispatchResult) or (isinstance(dispatched, tuple) and len(dispatched) == 2)):
        print(
            "DISPATCH-SHAPE: dispatch returned an invalid value: %s"
            % (type(dispatched).__name__,),
            file=sys.stderr,
        )
        return 1
    result, actual_model = dispatched

    # L5a-7, row X11: the command entry prints an answer to a caller that
    # cannot tell who wrote it, so a nonzero bridge exit and a missing
    # reported model both block here, whatever --allow-fallback said.
    if result.returncode != 0:
        print("BRIDGE: the bridge exited %s; no answer is printed"
              % (result.returncode,), file=sys.stderr)
        return 4
    if actual_model is None:
        print("FALLBACK: no [usage] line names the model that answered; "
              "no answer is printed", file=sys.stderr)
        return 4

    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    print("[gated-dispatch] actual_model=%s" % actual_model, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
