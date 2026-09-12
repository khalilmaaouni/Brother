#!/usr/bin/env python3
"""Run a command over many items only after it succeeds on ONE item first.

Written 2026-09-12 after a night run launched driver scripts at full scale
that failed on every item for the same reason. Drafted by Muse Spark 1.2
from a role-worded spec, cross-reviewed by DeepSeek V4.1 Flash.
"""
import argparse
import concurrent.futures
import re
import shlex
import subprocess
import sys
import os

def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--items-file", required=True, dest="items_file")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--check-regex", dest="check_regex", default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    # remove leading -- if present due to REMAINDER
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return args

def build_template_tokens(command_list):
    if not command_list:
        return []
    # if single string containing spaces, treat as template string
    if len(command_list) == 1 and (" " in command_list[0] or "\t" in command_list[0]):
        return shlex.split(command_list[0])
    # otherwise preserve tokens via quote roundtrip to honour shlex contract
    joined = " ".join(shlex.quote(t) for t in command_list)
    return shlex.split(joined)

def build_args(template_tokens, item):
    out = []
    for tok in template_tokens:
        if tok == "{item}":
            out.append(item)
        elif "{item}" in tok:
            out.append(tok.replace("{item}", item))
        else:
            out.append(tok)
    return out

def run_one(template_tokens, item, timeout):
    args = build_args(template_tokens, item)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, shell=False)
        stdout = result.stdout if result.stdout is not None else ""
        stderr = result.stderr if result.stderr is not None else ""
        combined = stdout
        if stdout and stderr:
            if not stdout.endswith("\n"):
                combined += "\n"
            combined += stderr
        elif stderr:
            combined = stderr
        return result.returncode, combined, False, stdout, stderr
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout if e.stdout is not None else ""
        stderr = e.stderr if e.stderr is not None else ""
        # when text=True, stdout/stderr are str; otherwise decode
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="ignore")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="ignore")
        combined = ""
        if stdout:
            combined += stdout
        if stderr:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += stderr
        # timeout counts as failure, use 124 as conventional timeout code
        return 124, combined, True, stdout, stderr
    except FileNotFoundError as e:
        return 127, str(e), False, "", str(e)
    except Exception as e:
        return 1, str(e), False, "", str(e)

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        args = parse_args(argv)
    except SystemExit as e:
        # argparse exits with 2 on error, preserve NO-DATA semantics for missing args
        sys.exit(e.code if e.code in (0, 1, 2) else 2)

    if not args.command:
        print("missing command template", file=sys.stderr)
        sys.exit(2)

    template_tokens = build_template_tokens(args.command)
    if not template_tokens:
        print("missing command template", file=sys.stderr)
        sys.exit(2)

    # read items
    items_file = args.items_file
    try:
        with open(items_file, "r", encoding="utf-8", errors="ignore") as f:
            raw_lines = f.read().splitlines()
    except FileNotFoundError:
        print(f"items file not found: {items_file}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    items = [line.strip() for line in raw_lines if line.strip() != ""]
    if len(items) == 0:
        print("NO-DATA: empty items file", file=sys.stderr)
        sys.exit(2)

    jobs = args.jobs if args.jobs and args.jobs > 0 else 4
    timeout = args.timeout if args.timeout is not None else 600
    check_regex = args.check_regex

    # smoke run first item alone
    first = items[0]
    code, combined, timed_out, stdout, stderr = run_one(template_tokens, first, timeout)

    output_not_empty = combined.strip() != ""
    regex_ok = True
    if check_regex is not None:
        try:
            regex_ok = re.search(check_regex, combined) is not None
        except re.error as e:
            print(f"invalid regex: {e}", file=sys.stderr)
            sys.exit(2)

    smoke_pass = (code == 0 and output_not_empty and regex_ok)

    if not smoke_pass:
        # print exit code and last 20 lines
        print(f"smoke exit={code}")
        print(f"smoke item={first} exit={code}")
        if timed_out:
            print("smoke timeout")
        # last 20 lines of output
        lines = combined.splitlines()
        last = lines[-20:]
        for l in last:
            print(l)
        # also indicate reason for debugging
        if not output_not_empty:
            print("smoke output empty")
        elif not regex_ok:
            print(f"smoke check-regex did not match: {check_regex}")
        sys.exit(1)

    # smoke passed, run remaining items
    remaining = items[1:]
    k = len(remaining)
    results = {}
    # include smoke result for per item printing
    results[0] = code

    if k > 0:
        max_workers = min(jobs, k)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {executor.submit(run_one, template_tokens, items[idx], timeout): idx for idx in range(1, len(items))}
            for fut in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[fut]
                try:
                    c, comb, to, out, err = fut.result()
                except Exception as e:
                    c, comb = 1, str(e)
                results[idx] = c

    # print one line per item in original order
    for idx, it in enumerate(items):
        c = results.get(idx, 1)
        print(f"{it} exit={c}")

    failed = sum(1 for idx in range(len(items)) if results.get(idx, 1) != 0)
    # failed count for summary is failures among remaining plus smoke if failed (smoke passed so 0)
    # but spec says summary counts items distinct from smoke
    failed_remaining = sum(1 for idx in range(1, len(items)) if results.get(idx, 1) != 0)
    print(f"smoke_first: 1 smoke + {k} items, {failed_remaining} failed")

    if failed != 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
