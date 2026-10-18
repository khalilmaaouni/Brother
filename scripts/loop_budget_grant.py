#!/usr/bin/env python3
"""SG1.a: a loop launch writes its own time boxed spend guard project raise.

The spend guard (~/.claude/hooks/spend_guard.py, config ~/.claude/spend-guard.json)
honours a project block raise only with a future `until`; the global `override` key
suspends it entirely. The owner's standing rule is raise, never override, so this
tool writes projects[slug] blocks and leaves every other key exactly as parsed.

    python3 -B scripts/loop_budget_grant.py --words "owner words" --tokens N \
        --until 2026-10-19T06:00:00+02:00 --project-slug SLUG [--project-slug ...] \
        --spent N [--config PATH] [--dry]

Every refusal writes nothing and exits 2 with the reason on stderr: empty words, a
zero, negative or non integer N, an until that is past or carries no offset, a slug
with a slash, a config that is missing, not JSON or not an object, or a guard whose
own selftest fails on the would be file. A guard that is absent reads NO-DATA.

Python 3.9 floor, standard library only.
"""
import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time

SOFT_GAP = 2000000
REASON_PREFIX = "loop budget grant, owner words: "
BLOCK_KEYS = ("session_hard", "session_soft", "daily_hard", "daily_soft", "until", "reason")
_DIGITS = re.compile(r"^[0-9]+$")


class GrantRefused(ValueError):
    """A grant this tool will not write; nothing was written."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _until_stamp(until):
    """The POSIX time of an ISO 8601 until that carries an offset, else refuse."""
    if not isinstance(until, str) or not until.strip():
        raise GrantRefused("until must be an ISO 8601 time with an offset")
    text = until.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise GrantRefused("until does not parse as ISO 8601: %r" % until)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise GrantRefused("until carries no offset: %r" % until)
    return moment.timestamp()


def _check_slug(slug):
    if not isinstance(slug, str) or not slug.strip() or slug in (".", ".."):
        raise GrantRefused("a project slug must be a non empty string")
    if "/" in slug or "\\" in slug or "\x00" in slug:
        raise GrantRefused("a project slug may not carry a slash: %r" % slug)


def grant_block(words, tokens, until, spent, now):
    """The project block: hard = spent + tokens, soft 2M below (floored at spent)."""
    if not isinstance(words, str) or not words.strip():
        raise GrantRefused("the owner's words are required")
    if not _is_int(tokens) or tokens <= 0:
        raise GrantRefused("tokens must be a positive integer")
    if not _is_int(spent) or spent < 0:
        raise GrantRefused("spent must be a non negative integer")
    if not (_is_int(now) or isinstance(now, float)) or not math.isfinite(now):
        raise GrantRefused("now must be a finite number")
    if _until_stamp(until) <= now:
        raise GrantRefused("until is not after now: %r" % until)
    hard = spent + tokens
    soft = max(spent, hard - SOFT_GAP)
    return {"session_hard": hard, "session_soft": soft,
            "daily_hard": hard, "daily_soft": soft,
            "until": until, "reason": REASON_PREFIX + words}


def _check_block(block):
    if not isinstance(block, dict) or sorted(block) != sorted(BLOCK_KEYS):
        raise GrantRefused("the block must carry exactly %s" % ", ".join(BLOCK_KEYS))
    for key in BLOCK_KEYS[:4]:
        if not _is_int(block[key]) or block[key] < 0:
            raise GrantRefused("block %s must be a non negative integer" % key)
    if not isinstance(block["reason"], str) or not block["reason"].strip():
        raise GrantRefused("block reason must carry the owner's words")
    if _until_stamp(block["until"]) <= time.time():
        raise GrantRefused("block until is already past: %r" % block["until"])


def render_config(config_path, slugs, block):
    """The config text after the grant; reads the file, writes nothing."""
    if not isinstance(config_path, str) or not config_path:
        raise GrantRefused("config path must be a non empty string")
    if not isinstance(slugs, list) or not slugs:
        raise GrantRefused("at least one project slug is required")
    for slug in slugs:
        _check_slug(slug)
    _check_block(block)
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as exc:
        raise GrantRefused("cannot read the config %s: %s" % (config_path, exc))
    if not isinstance(config, dict):
        raise GrantRefused("the config %s is not a JSON object" % config_path)
    projects = config.get("projects", {})
    if not isinstance(projects, dict):
        raise GrantRefused("the config's projects key is not an object")
    projects = dict(projects)
    for slug in slugs:
        projects[slug] = dict(block)
    updated = dict(config)
    updated["projects"] = projects
    return json.dumps(updated, indent=2, sort_keys=True) + "\n"


def _write_atomic(path, text):
    """Write beside the target and rename over it: a reader sees old or new, never half."""
    folder = os.path.dirname(os.path.abspath(path))
    mode = os.stat(path).st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=".spend-guard.", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply_grant(config_path, slugs, block):
    """Set projects[slug] = block for each slug, every other key as parsed; answer the path."""
    text = render_config(config_path, slugs, block)
    try:
        _write_atomic(config_path, text)
    except OSError as exc:
        raise GrantRefused("the write failed before the rename, the old file stands: %s" % exc)
    return config_path


def guard_selftest(config_text, guard_path=None):
    """PASS or FAIL from the guard's own selftest run on config_text, NO-DATA when absent.

    The guard is loaded with every path it reads or writes repointed at a scratch
    folder, the same isolation scripts/limit_drill.py uses."""
    if not isinstance(config_text, str):
        raise GrantRefused("config text must be a string")
    if guard_path is None:
        guard_path = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "spend_guard.py")
    if not isinstance(guard_path, str):
        raise GrantRefused("guard path must be a string")
    if not guard_path or not os.path.isfile(guard_path) or not os.access(guard_path, os.R_OK):
        return "NO-DATA"
    scratch = tempfile.mkdtemp(prefix="grant-guard-")
    try:
        config = os.path.join(scratch, "spend-guard.json")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(config_text)
        try:
            spec = importlib.util.spec_from_file_location("spend_guard_grant", guard_path)
            mod = importlib.util.module_from_spec(spec)
            with contextlib.redirect_stdout(io.StringIO()):
                spec.loader.exec_module(mod)
        except OSError:
            return "NO-DATA"
        except BaseException:
            return "FAIL"
        mod.CONFIG = config
        mod.LEDGER = os.path.join(scratch, "ledger.jsonl")
        mod.CACHE_DIR = os.path.join(scratch, "spend-cache")
        mod.PROJECTS = os.path.join(scratch, "projects")
        selftest = mod.__dict__.get("selftest")
        if not callable(selftest):
            return "NO-DATA"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                result = selftest()
        except SystemExit as exc:
            result = exc.code
        except BaseException:
            return "FAIL"
        passed = result is None or result is True or (_is_int(result) and result == 0)
        return "PASS" if passed else "FAIL"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _parse_count(text, name):
    if not isinstance(text, str) or not _DIGITS.match(text):
        raise GrantRefused("%s must be a whole number: %r" % (name, text))
    return int(text)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SystemExit(2)
    parser = argparse.ArgumentParser(description="Write a time boxed spend guard project raise.")
    parser.add_argument("--words", required=True)
    parser.add_argument("--tokens", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--project-slug", action="append", default=[], dest="slugs")
    parser.add_argument("--config", default=None)
    parser.add_argument("--spent", required=True)
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args(list(argv))
    config = args.config or os.path.join(os.path.expanduser("~"), ".claude", "spend-guard.json")
    try:
        block = grant_block(args.words, _parse_count(args.tokens, "tokens"), args.until,
                            _parse_count(args.spent, "spent"), time.time())
        if not args.slugs:
            raise GrantRefused("at least one --project-slug is required")
        for slug in args.slugs:
            _check_slug(slug)
        if args.dry:
            print(json.dumps({"projects": {s: block for s in args.slugs}}, indent=2, sort_keys=True))
            return 0
        verdict = guard_selftest(render_config(config, args.slugs, block))
        if verdict == "FAIL":
            raise GrantRefused("the spend guard's own selftest fails on the granted config")
        apply_grant(config, args.slugs, block)
    except GrantRefused as exc:
        sys.stderr.write("loop_budget_grant: REFUSED: %s\n" % exc)
        return 2
    print("loop_budget_grant: wrote %s for %s until %s; guard selftest: %s"
          % (config, ", ".join(args.slugs), block["until"], verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
