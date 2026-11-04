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

AUTH1.a, the readback: a selftest PASS says the guard works, not that THIS slug reads THIS
block. effective_grant asks the deployed reader's own load_config what it will enforce for
each slug and compares all four limits with the block. A reader that differs, raises, answers
out of shape or carries an active override refuses before the write (exit 2); a guard with no
load_config reads NO-DATA. The written file is read back the same way after the rename.

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
#: THE DEPLOYED READER'S OWN KEYS (R1, reproduced 2026-10-30): the guard that enforces a grant is
#: ~/.claude/hooks/spend_guard.py and it reads LIMIT_KEYS = soft_out, hard_out, daily_soft_out,
#: daily_hard_out. This file wrote session_hard, session_soft, daily_hard, daily_soft, so a 20M grant
#: "applied" by this tool loaded as the 600,000 default in the real worker: a grant that reads applied
#: and is not. test_loop_budget_grant.py compares these names with the deployed reader's when it is on
#: this machine, and with this pin always.
BLOCK_KEYS = ("hard_out", "soft_out", "daily_hard_out", "daily_soft_out", "until", "reason")
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
    return {"hard_out": hard, "soft_out": soft,
            "daily_hard_out": hard, "daily_soft_out": soft,
            "until": until, "reason": REASON_PREFIX + words}


def cap_block(dollars, until, words, now=None):
    """AUTH1.c: the dollar cap granted with the token grant. It carries the grant's own until, one
    clock, never a second one, so it lapses when the grant does; burn_guard.read_cap reads it."""
    if not isinstance(words, str) or not words.strip():
        raise GrantRefused("the owner's words are required")
    if not _is_number(dollars) or not math.isfinite(dollars) or dollars <= 0:
        raise GrantRefused("dollars must be a positive finite number")
    if _until_stamp(until) <= (time.time() if now is None else now):
        raise GrantRefused("until is not after now: %r" % until)
    return {"dollars": float(dollars), "until": until, "reason": REASON_PREFIX + words}


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


def _guard_path(guard_path):
    if guard_path is None:
        guard_path = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "spend_guard.py")
    if not isinstance(guard_path, str):
        raise GrantRefused("guard path must be a string")
    return guard_path


@contextlib.contextmanager
def _loaded_guard(config_text, guard_path):
    """The guard module, or the verdict NO-DATA (absent, unreadable) or FAIL (does not import).

    The guard is loaded with every path it reads or writes repointed at a scratch
    folder holding config_text, the same isolation scripts/limit_drill.py uses; the
    scratch is removed on the way out."""
    if not guard_path or not os.path.isfile(guard_path) or not os.access(guard_path, os.R_OK):
        yield "NO-DATA"
        return
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
            mod = "NO-DATA"
        except BaseException:
            mod = "FAIL"
        if not isinstance(mod, str):
            mod.CONFIG = config
            mod.LEDGER = os.path.join(scratch, "ledger.jsonl")
            mod.CACHE_DIR = os.path.join(scratch, "spend-cache")
            mod.PROJECTS = os.path.join(scratch, "projects")
        yield mod
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def guard_selftest(config_text, guard_path=None):
    """PASS or FAIL from the guard's own selftest run on config_text, NO-DATA when absent."""
    if not isinstance(config_text, str):
        raise GrantRefused("config text must be a string")
    with _loaded_guard(config_text, _guard_path(guard_path)) as mod:
        if isinstance(mod, str):
            return mod
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


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def effective_grant(config_text, slug, guard_path=None, now=None):
    """What the deployed reader's load_config(slug, now) enforces on config_text, against the block.

    PASS only when all four limits match the block and no override is active; FAIL when the reader
    differs, raises, answers out of shape, or is suspended by an override; NO-DATA when the guard is
    absent or has no load_config. The answer carries only the limits and this tool's own reason:
    never the owner's words, another project's slug, or the config itself."""
    if not isinstance(config_text, str):
        raise GrantRefused("config text must be a string")
    _check_slug(slug)
    try:
        config = json.loads(config_text)
    except ValueError:
        raise GrantRefused("the config text is not JSON")
    projects = config.get("projects") if isinstance(config, dict) else None
    block = projects.get(slug) if isinstance(projects, dict) else None
    if not isinstance(block, dict) or not all(_is_int(block.get(k)) for k in BLOCK_KEYS[:4]):
        raise GrantRefused("the config carries no granted block for %s" % slug)
    path = _guard_path(guard_path)
    answer = dict(verdict="FAIL", slug=slug, hard_out=None, soft_out=None, daily_hard_out=None,
                  daily_soft_out=None, override_active=False, reason="")
    with _loaded_guard(config_text, path) as mod:
        if mod == "NO-DATA":
            return dict(answer, verdict="NO-DATA", reason="the deployed reader %s is absent or unreadable" % path)
        if mod == "FAIL":
            return dict(answer, reason="the deployed reader %s does not import" % path)
        load_config = mod.__dict__.get("load_config")
        if not callable(load_config):
            return dict(answer, verdict="NO-DATA", reason="the deployed reader %s has no load_config to read back" % path)
        try:
            # The reader's warnings name other projects' blocks: silenced, never relayed.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                read = load_config(slug, now)
        except (Exception, SystemExit) as exc:
            return dict(answer, reason="the deployed reader %s load_config raised %s for %s"
                        % (path, type(exc).__name__, slug))
    if not (isinstance(read, tuple) and len(read) == 2 and isinstance(read[0], dict)
            and (read[1] is None or isinstance(read[1], dict))):
        return dict(answer, reason="the deployed reader %s load_config does not answer (limits, override)" % path)
    limits, override = read
    for key in BLOCK_KEYS[:4]:
        if _is_number(limits.get(key)):
            answer[key] = limits[key]
    answer["override_active"] = bool(override)
    if override:
        return dict(answer, reason="the deployed reader %s has an active override: the override suspends the "
                    "guard, and a suspended guard is not a grant" % path)
    for key in BLOCK_KEYS[:4]:
        if answer[key] != block[key]:
            return dict(answer, reason="the deployed reader %s reads %s as %s=%s, not %s"
                        % (path, slug, key, answer[key], block[key]))
    return dict(answer, verdict="PASS", reason="the deployed reader %s enforces the block for %s" % (path, slug))


def _effective_all(config_text, slugs):
    """The worst readback across slugs: the first FAIL, else NO-DATA if any, else PASS."""
    worst = None
    for slug in slugs:
        got = effective_grant(config_text, slug)
        if got["verdict"] == "FAIL":
            return got
        if worst is None or got["verdict"] == "NO-DATA":
            worst = got
    return worst


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
        text = render_config(config, args.slugs, block)
        verdict = guard_selftest(text)
        if verdict == "FAIL":
            raise GrantRefused("the spend guard's own selftest fails on the granted config")
        got = _effective_all(text, args.slugs)
        if got["verdict"] == "FAIL":
            raise GrantRefused(got["reason"] if got["override_active"]
                               else "%s; update the guard or the block keys" % got["reason"])
        apply_grant(config, args.slugs, block)
    except GrantRefused as exc:
        sys.stderr.write("loop_budget_grant: REFUSED: %s\n" % exc)
        return 2
    # The readback again on the file as written: a rename can land on a file the reader reads otherwise.
    try:
        with open(config, encoding="utf-8") as fh:
            got = _effective_all(fh.read(), args.slugs)
    except (OSError, GrantRefused) as exc:
        got = {"verdict": "FAIL", "reason": "the written file does not read back: %s" % exc}
    if got["verdict"] == "FAIL":
        sys.stderr.write("loop_budget_grant: NOT EFFECTIVE: %s is already written, but %s\n"
                         % (config, got["reason"]))
        return 2
    print("loop_budget_grant: wrote %s for %s until %s; guard selftest: %s; effective: %s"
          % (config, ", ".join(args.slugs), block["until"], verdict, got["verdict"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
