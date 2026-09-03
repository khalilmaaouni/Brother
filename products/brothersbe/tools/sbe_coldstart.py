"""Perform real headless cold starts and append one receipt row.

Expensive and nondeterministic on purpose: only a real session fires the
SessionStart hooks and loads the plugin, which is the difference between
measuring the context tax and estimating it.

Runs on a schedule and before a release. Never in per-commit CI, because the
grading half of this system is what belongs there.
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coldstart_fixture
import coldstart_parse
# Every report line this tool prints goes through say(), which flattens the WHOLE
# rendered line after formatting. Not a style preference: RECEIPT is derived from
# an environment variable, so a vault path carrying a newline could otherwise
# write a second, forged line into the output. The rule is the choke point, not a
# list of values anybody remembered to wrap.
from sbe_checks import say

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The receipt lives beside the other evidence ledgers under the vault, not in the
# repository. It is the same class of thing they are: accumulated rows of measured
# evidence. Deriving it from the vault is also what lets the honesty sweep grade
# it, because the sweep re-roots the vault into a throwaway directory, and a path
# derived from the repository root does not move when the vault does.
#
# NO_VAULT_SENTINEL, not the operator's real home directory. This used to default
# to os.path.expanduser("~/BrotherSBEVault") when BROTHERSBE_VAULT was unset, the
# same class of defect already fixed in tools/sbe_telemetry.py: every run of this
# tool with no vault configured would stat and create directories under the
# operator's ACTUAL $HOME by default. The sentinel is a path no real vault can
# ever be (no home directory, no relative component, an all-caps marker segment
# under the filesystem root), so a write attempted against it fails fast instead
# of silently landing on disk under the operator's home.
NO_VAULT_SENTINEL = os.path.join(os.sep, "BROTHERSBE-NO-VAULT-CONFIGURED")

#: True exactly when BROTHERSBE_VAULT names a non-empty value in the environment
#: this process saw at import time. An explicitly empty BROTHERSBE_VAULT ("") is
#: treated the same as unset, matching sbe_telemetry.VAULT_CONFIGURED.
VAULT_CONFIGURED = bool(os.environ.get("BROTHERSBE_VAULT", "").strip())

VAULT = os.environ.get("BROTHERSBE_VAULT", "").strip() or NO_VAULT_SENTINEL
RECEIPT = os.path.join(VAULT, "99-System", "telemetry", "coldstart.jsonl")
PROXY = "model-as-beginner"
PROMPT = ("You have never seen this project before. Read BRIEF.md and complete "
          "the change it asks for, start to finish.")


def head_sha():
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError("cannot read HEAD: %s" % done.stderr.strip())
    return done.stdout.strip()


def one_run(sandbox, transcript_path, budget_usd):
    """One headless session. Returns the parsed metrics, or a failure row.

    --include-hook-events is load-bearing, not decoration: without it the
    session-start hook output never reaches the stream, context_bytes_at_start
    reads zero, and zero is a perfect score on the very metric this exists to
    expose.

    --plugin-dir loads BrotherSBE for this session only, so the sandbox gets a
    real plugin load without anything being installed on the machine.
    """
    with open(transcript_path, "w") as fh:
        done = subprocess.run(
            ["claude", "--print", "--output-format", "stream-json",
             # The installed CLI refuses stream-json under --print without
             # --verbose (observed 2026-08-11: all 5 runs of the first baseline
             # died on "requires --verbose" before spending anything).
             "--verbose",
             "--include-hook-events",
             "--max-budget-usd", str(budget_usd),
             "--plugin-dir", ROOT,
             "--permission-mode", "acceptEdits",
             PROMPT],
            cwd=sandbox, stdout=fh, stderr=subprocess.PIPE, text=True, timeout=3600)
    if done.returncode != 0 and not os.path.getsize(transcript_path):
        return {"completed": False, "outcome": "session produced no transcript: %s"
                                              % done.stderr.strip()[:200]}
    try:
        return coldstart_parse.parse_transcript(transcript_path)
    except (OSError, ValueError) as e:
        return {"completed": False, "outcome": "transcript unreadable: %s" % e}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Measure a cold-start beginner run and append a receipt row.")
    ap.add_argument("--max-budget-usd", type=float, required=True,
                    help="hard per-session spend ceiling, passed straight through to the "
                         "CLI which enforces it. Required, because an unattended run with "
                         "no ceiling is the spend failure this project already had once")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--subject", default="t1-endpoint",
                    choices=sorted(coldstart_fixture.SUBJECTS))
    ap.add_argument("--dry-run", action="store_true",
                    help="build and tear down the sandbox, spend no tokens, write no receipt")
    args = ap.parse_args(argv)

    sandbox = coldstart_fixture.build_sandbox(args.subject)
    try:
        if args.dry_run:
            say("dry run: sandbox for subject %s built at %s and validated"
                % (args.subject, sandbox))
            return 0
        runs = []
        workdir = os.path.join(sandbox, "_transcripts")
        os.makedirs(workdir, exist_ok=True)
        for i in range(args.runs):
            runs.append(one_run(sandbox, os.path.join(workdir, "run-%d.jsonl" % i),
                                args.max_budget_usd))
        # A batch is complete only when every run produced a readable transcript.
        # A run the CLI cut off at the spend ceiling is a partial measurement, and
        # the receipt says so rather than averaging it in as if it finished.
        complete = all(r.get("outcome") == "ok" for r in runs)
        row = {
            "head_sha": head_sha(),
            "measured_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "proxy": PROXY,
            "subject": args.subject,
            "complete": complete,
            "ceiling_usd": args.max_budget_usd,
            "runs": runs,
        }
        os.makedirs(os.path.dirname(RECEIPT), exist_ok=True)
        with open(RECEIPT, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        say("appended one cold-start batch of %d run(s) to %s" % (len(runs), RECEIPT))
        return 0
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
