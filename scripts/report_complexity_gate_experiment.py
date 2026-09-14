#!/usr/bin/env python3
"""Reports the treatment-vs-control agreement rate for one complexity-gate
threshold experiment.

Token-shield's own ledger (~/.claude/token-shield/savings.jsonl, read via
`cli.py experiment report`) verifies token-usage savings, not decision
agreement -- it has no field for "did control and treatment agree on this
call". So the agreement rate below comes from AGREEMENT_LEDGER, the file
complexity_gate_experiment.py appends to on every run. Token-shield's own
report for the same label is printed underneath as context, honestly
labeled NO DATA when that label produced nothing comparable there (the
common case: start+end run back-to-back have no real usage window between
them).
"""
import argparse
import json
import os
import subprocess
import sys

CLI = os.path.expanduser("~/SaveClaudeTokens/scripts/cli.py")
AGREEMENT_LEDGER = os.path.expanduser("~/.claude/token-shield/complexity_gate_experiment.jsonl")


def load_records(label: str, ledger_path: str = AGREEMENT_LEDGER) -> list:
    if not os.path.exists(ledger_path):
        return []
    out = []
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:  # sbe: allow-silent a corrupt line is skipped, not fatal to the rest of the ledger
                continue
            if rec.get("label") == label:
                out.append(rec)
    return out


def agreement_rate(records: list) -> tuple:
    """Returns (agreed_count, total_count). Pure so it is testable without
    any file on disk."""
    agreed = sum(1 for r in records if r.get("agreed"))
    return agreed, len(records)


def token_shield_report_for(label: str) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, CLI, "experiment", "report"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"NO DATA: could not run token-shield's experiment report ({e})"
    lines = [line for line in proc.stdout.splitlines() if label in line]
    if not lines:
        return f"NO DATA: token-shield's own ledger has no row for '{label}'."
    return "\n".join(lines)


def main(argv: list) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-threshold", type=int, required=True)
    args = p.parse_args(argv)
    label = f"complexity-gate-threshold-{args.candidate_threshold}"

    records = load_records(label)
    if not records:
        print(f"NO DATA: no calls recorded yet for '{label}'. Run complexity_gate_experiment.py first.")
        return 1

    agreed, total = agreement_rate(records)
    print(f"{label}: {agreed}/{total} calls agreed ({agreed / total:.0%}), {total - agreed} disagreed.")
    print("token-shield's own experiment report for this label:")
    print(token_shield_report_for(label))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
