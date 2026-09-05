#!/usr/bin/env python3
"""Checker for the JBEQ-MDM end to end scenario (benchmarks/jbeq/mdm/e2e-001).

Reads one RUN DIRECTORY, the six artefacts a run of that scenario must
produce, and compares them against the hand written ground truth beside the
fixture. It prints one line per artefact, then one critical integrity line,
then one handover section line.

WHY THE CRITICAL LINE IS NOT A SUMMARY OF THE SIX. The six artefact lines
compare a run against an answer key. The critical line does not: it
recomputes four invariants from the fixture's own source CSVs and from the
run's own outputs, so a run that reproduced the answer key by copying it
still fails the critical line if its outputs contradict the source data.
The four are the classes section 28 of the morning steering names as
critical: a false merge, a reassigned historical transaction, a reversed
hierarchy, a survivorship precedence violation.

VERDICTS. PASS, FAIL naming the FIRST mismatch (never a count of them, so a
re-run after fixing one still finds the next), or NO-DATA when the artefact
is missing or unreadable. NO-DATA is never a pass.

EXIT. 1 when anything reads FAIL. 3 when every artefact reads NO-DATA, which
is the empty run directory case and is reported as its own state rather than
as twelve failures. 0 only when nothing failed.
"""
import argparse
import csv
import json
import os
import sys

ARTEFACTS = [
    "golden.csv",
    "links.csv",
    "mapping.json",
    "decisions.json",
    "reconciliation.json",
    "handover.ja.md",
]

# Section 24 of the morning steering names these eleven, in this order.
HANDOVER_SECTIONS = [
    "変更内容",
    "理由",
    "正とする情報源",
    "業務ルール",
    "技術実装",
    "テスト",
    "データ照合",
    "残存リスク",
    "未解決の業務上の疑問",
    "切り戻し",
    "次の手順",
]

MAPPING_KEYS = [
    "target_field",
    "source_field",
    "transformation",
    "authority",
    "null_handling",
    "effective_date_behaviour",
    "reconciliation_rule",
]


class NoData(Exception):
    """The artefact is missing or unreadable. Never a pass, never a FAIL."""


def read_csv(path):
    if not os.path.isfile(path):
        raise NoData("missing %s" % os.path.basename(path))
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError) as exc:
        raise NoData("unreadable %s: %s" % (os.path.basename(path), exc))
    if not rows:
        raise NoData("empty %s" % os.path.basename(path))
    return rows


def read_json(path):
    if not os.path.isfile(path):
        raise NoData("missing %s" % os.path.basename(path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NoData("unreadable %s: %s" % (os.path.basename(path), exc))


def read_text(path):
    if not os.path.isfile(path):
        raise NoData("missing %s" % os.path.basename(path))
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise NoData("unreadable %s: %s" % (os.path.basename(path), exc))
    if not text.strip():
        raise NoData("empty %s" % os.path.basename(path))
    return text


def compare_rows(got, want, key, label):
    """First mismatch between two lists of dict rows, or None.

    Both sides are sorted by the key column first. Row ORDER in a CSV is not
    part of what either artefact promises, and a checker that treats it as a
    contract fails a correct run for writing its links in a different pass
    order, which is what this check did the first time it ran.
    """
    got = sorted(got, key=lambda r: r.get(key, ""))
    want = sorted(want, key=lambda r: r.get(key, ""))
    got_keys = [r.get(key, "") for r in got]
    want_keys = [r.get(key, "") for r in want]
    if got_keys != want_keys:
        for a, b in zip(got_keys, want_keys):
            if a != b:
                return "%s membership differs, expected %s got %s" % (
                    label, b, a)
        missing = want_keys[len(got_keys):]
        extra = got_keys[len(want_keys):]
        if missing:
            return "%s missing row %s" % (label, missing[0])
        return "%s unexpected row %s" % (label, extra[0])
    for got_row, want_row in zip(got, want):
        for column in want_row:
            a = (got_row.get(column) or "").strip()
            b = (want_row.get(column) or "").strip()
            if a != b:
                return "%s %s field %s expected %r got %r" % (
                    label, want_row[key], column, b, a)
    return None


def check_golden(run_dir, truth_dir):
    got = read_csv(os.path.join(run_dir, "golden.csv"))
    want = read_csv(os.path.join(truth_dir, "expected_golden.csv"))
    return compare_rows(got, want, "customer_id", "golden.csv")


def check_links(run_dir, truth_dir):
    got = read_csv(os.path.join(run_dir, "links.csv"))
    want = read_csv(os.path.join(truth_dir, "expected_links.csv"))
    return compare_rows(got, want, "child_customer_id", "links.csv")


def check_mapping(run_dir, truth_dir):
    got = read_json(os.path.join(run_dir, "mapping.json"))
    want = read_json(os.path.join(truth_dir, "expected_mapping.json"))
    got_fields = {f.get("target_field"): f for f in got.get("fields", [])}
    want_fields = {f.get("target_field"): f for f in want.get("fields", [])}
    if not got_fields:
        raise NoData("mapping.json carries no fields list")
    for name in want_fields:
        if name not in got_fields:
            return "mapping.json missing target_field %s" % name
    for name in got_fields:
        if name not in want_fields:
            return "mapping.json unexpected target_field %s" % name
    for name in want_fields:
        entry = got_fields[name]
        for key in MAPPING_KEYS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                return "mapping.json field %s has no %s" % (name, key)
        expected_source = want_fields[name]["source_field"]
        if entry["source_field"].strip() != expected_source:
            return "mapping.json field %s source_field expected %r got %r" % (
                name, expected_source, entry["source_field"].strip())
        # The authority CLASS is the load bearing half: a field the
        # requirement leaves open must still read UNKNOWN in the run, and a
        # field the register owns must still name the register.
        want_class = want_fields[name]["authority"].split(",")[0].strip()
        if want_class not in entry["authority"]:
            return "mapping.json field %s authority expected to name %r, got %r" % (
                name, want_class, entry["authority"])
    return None


def check_decisions(run_dir, truth_dir):
    got = read_json(os.path.join(run_dir, "decisions.json"))
    want = read_json(os.path.join(truth_dir, "expected_decisions.json"))
    got_rules = {r.get("id"): r for r in got.get("rules", [])}
    want_rules = {r.get("id"): r for r in want.get("rules", [])}
    if not got_rules:
        raise NoData("decisions.json carries no rules list")
    for rule_id in sorted(want_rules):
        if rule_id not in got_rules:
            return "decisions.json missing rule %s (%s)" % (
                rule_id, want_rules[rule_id].get("name"))
        want_status = want_rules[rule_id].get("status")
        got_status = got_rules[rule_id].get("status")
        if got_status != want_status:
            return "decisions.json rule %s expected %s got %s" % (
                rule_id, want_status, got_status)
    for rule_id in sorted(got_rules):
        if rule_id not in want_rules:
            return "decisions.json unexpected rule %s" % rule_id
    # An UNKNOWN with no question written down is an UNKNOWN nobody can act
    # on, which is the shape that lets a run score the label without doing
    # the work.
    for rule_id in sorted(want_rules):
        if want_rules[rule_id].get("status") != "UNKNOWN":
            continue
        question = (got_rules[rule_id].get("open_question") or "").strip()
        if not question:
            return "decisions.json rule %s is UNKNOWN with no open_question" % rule_id
    want_counts = want.get("counts") or {}
    got_counts = got.get("counts") or {}
    for label in sorted(want_counts):
        if got_counts.get(label) != want_counts[label]:
            return "decisions.json count %s expected %s got %s" % (
                label, want_counts[label], got_counts.get(label))
    return None


def check_reconciliation(run_dir, truth_dir):
    got = read_json(os.path.join(run_dir, "reconciliation.json"))
    want = read_json(os.path.join(truth_dir, "expected_reconciliation.json"))
    for key in sorted(want):
        if key in ("scenario", "note"):
            continue
        if key not in got:
            return "reconciliation.json missing %s" % key
        if got[key] != want[key]:
            return "reconciliation.json %s expected %s got %s" % (
                key, want[key], got[key])
    return None


def handover_missing_sections(run_dir):
    text = read_text(os.path.join(run_dir, "handover.ja.md"))
    return [s for s in HANDOVER_SECTIONS if s not in text]


def check_handover(run_dir, truth_dir):
    missing = handover_missing_sections(run_dir)
    if missing:
        return "handover.ja.md missing section %s" % missing[0]
    return None


CHECKS = [
    ("golden.csv", check_golden),
    ("links.csv", check_links),
    ("mapping.json", check_mapping),
    ("decisions.json", check_decisions),
    ("reconciliation.json", check_reconciliation),
    ("handover.ja.md", check_handover),
]


def critical_integrity(run_dir, source_dir):
    """The four critical classes, recomputed from the source CSVs.

    Returns a list of failure sentences, empty when the run is clean, or
    raises NoData when an input it needs is not there to read.
    """
    golden = read_csv(os.path.join(run_dir, "golden.csv"))
    links = read_csv(os.path.join(run_dir, "links.csv"))
    recon = read_json(os.path.join(run_dir, "reconciliation.json"))
    sales = read_csv(os.path.join(source_dir, "sales_master.csv"))
    registry = read_csv(os.path.join(source_dir, "registry.csv"))
    transactions = read_csv(os.path.join(source_dir, "transactions.csv"))

    failures = []
    by_id = {}
    for row in golden:
        by_id.setdefault(row["customer_id"], []).append(row)

    # 1. FALSE MERGE. Every source customer that is not a rejected row must
    # survive as exactly one target row, carrying no merge provenance.
    for row in sales:
        seen = by_id.get(row["customer_id"], [])
        if len(seen) == 0:
            failures.append(
                "false merge: source customer %s has no golden record"
                % row["customer_id"])
            break
        if len(seen) > 1:
            failures.append(
                "false merge: source customer %s has %d golden records"
                % (row["customer_id"], len(seen)))
            break
    for row in golden:
        if (row.get("merged_from") or "").strip():
            failures.append(
                "false merge: golden record %s names merged_from %s"
                % (row["customer_id"], row["merged_from"]))
            break
    if recon.get("merged") not in (0, "0"):
        failures.append("false merge: reconciliation merged is %s, not 0"
                        % recon.get("merged"))

    # 2. HISTORICAL REASSIGNMENT. The transactions per store, counted here
    # from the source file, must be what the run reported.
    counted = {}
    for row in transactions:
        counted[row["store_id"]] = counted.get(row["store_id"], 0) + 1
    reported = recon.get("transactions_by_store") or {}
    reported = {k: int(v) for k, v in reported.items()}
    if reported != counted:
        failures.append(
            "historical reassignment: transactions by store counted %s from "
            "the source, the run reported %s" % (counted, reported))
    if recon.get("transaction_rows_reassigned") not in (0, "0"):
        failures.append(
            "historical reassignment: the run reassigned %s transaction rows"
            % recon.get("transaction_rows_reassigned"))

    # 3. HIERARCHY REVERSAL. A parent must be a corporate record and a child
    # a store record, never the other way round.
    for row in links:
        parent = by_id.get(row["parent_customer_id"], [])
        child = by_id.get(row["child_customer_id"], [])
        if not parent or not child:
            failures.append(
                "hierarchy reversal: link %s to %s names a customer with no "
                "golden record" % (row["parent_customer_id"],
                                   row["child_customer_id"]))
            break
        if parent[0].get("record_type") != "CORPORATE":
            failures.append(
                "hierarchy reversal: link parent %s is %s, not CORPORATE"
                % (row["parent_customer_id"], parent[0].get("record_type")))
            break
        if child[0].get("record_type") != "STORE":
            failures.append(
                "hierarchy reversal: link child %s is %s, not STORE"
                % (row["child_customer_id"], child[0].get("record_type")))
            break

    # 4. SURVIVORSHIP PRECEDENCE. The legal name is the register's, the
    # commercial name is the sales master's, and neither has overwritten the
    # other.
    registered = {r["houjin_bangou"]: r["registered_name"] for r in registry}
    commercial = {r["customer_id"]: r["torihikisaki_name"] for r in sales}
    for row in golden:
        number = (row.get("houjin_bangou") or "").strip()
        if number and row.get("legal_name") != registered.get(number):
            failures.append(
                "survivorship precedence: golden %s legal_name is %r, the "
                "register says %r" % (row["customer_id"], row.get("legal_name"),
                                      registered.get(number)))
            break
        source_name = commercial.get(row["customer_id"])
        if source_name is not None and row.get("commercial_name") != source_name:
            failures.append(
                "survivorship precedence: golden %s commercial_name is %r, "
                "the sales master says %r" % (row["customer_id"],
                                              row.get("commercial_name"),
                                              source_name))
            break
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", help="directory holding the run artefacts")
    parser.add_argument("--ground-truth", default=None,
                        help="ground truth directory "
                             "(default <run dir>/../../ground-truth)")
    parser.add_argument("--fixture", default=None,
                        help="fixture source CSV directory "
                             "(default <run dir>/../../source)")
    args = parser.parse_args(argv)

    run_dir = args.run_dir
    scenario = os.path.dirname(os.path.dirname(os.path.abspath(run_dir)))
    truth_dir = args.ground_truth or os.path.join(scenario, "ground-truth")
    source_dir = args.fixture or os.path.join(scenario, "data")

    failed = False
    no_data = 0
    for name, check in CHECKS:
        try:
            problem = check(run_dir, truth_dir)
        except NoData as exc:
            print("%s: NO-DATA (%s)" % (name, exc))
            no_data += 1
            continue
        if problem:
            print("%s: FAIL (%s)" % (name, problem))
            failed = True
        else:
            print("%s: PASS" % name)

    try:
        failures = critical_integrity(run_dir, source_dir)
    except NoData as exc:
        print("critical integrity: NO-DATA (%s)" % exc)
        no_data += 1
    else:
        if failures:
            print("critical integrity: FAIL (%s)" % failures[0])
            failed = True
        else:
            print("critical integrity: PASS")

    try:
        missing = handover_missing_sections(run_dir)
    except NoData as exc:
        print("handover sections: NO-DATA (%s)" % exc)
        no_data += 1
    else:
        if missing:
            print("handover sections: FAIL (missing %s)" % ", ".join(missing))
            failed = True
        else:
            print("handover sections: PASS (%d of %d present)"
                  % (len(HANDOVER_SECTIONS), len(HANDOVER_SECTIONS)))

    if no_data == len(CHECKS) + 2:
        print("jbeq-mdm e2e: NO-DATA, nothing in %s to read" % run_dir)
        return 3
    if failed:
        print("jbeq-mdm e2e: FAIL")
        return 1
    if no_data:
        print("jbeq-mdm e2e: FAIL, %d artefact(s) read NO-DATA" % no_data)
        return 1
    print("jbeq-mdm e2e: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
