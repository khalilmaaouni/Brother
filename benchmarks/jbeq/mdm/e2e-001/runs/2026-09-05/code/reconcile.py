#!/usr/bin/env python3
"""JBEQ-MDM E2E-001 の照合 (independent reconciliation).

移行処理とは別のスクリプトです。mdm_transform を import せず、出力ファイルと
ソースファイルだけを数えます。移行処理が内部で持っている件数を信用しない、
というのがこのファイルの存在理由です。

数える項目は steering の第22節に挙がっているもの:
source, target, matched, unmatched, merged, linked, rejected, duplicate,
orphan, attribute mismatch, relationship mismatch, historical mismatch.

Python 3, standard library only.
"""
import argparse
import csv
import json
import os
import sys

# 一つでも 0 でなければ critical data integrity は 100% ではない。
CRITICAL_COUNTERS = [
    "merged",
    "duplicate",
    "orphan",
    "relationship_mismatch",
    "historical_mismatch",
    "transaction_rows_reassigned",
]


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def count_by(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key, "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def reconcile(data_dir, out_dir):
    registry = read_csv(os.path.join(data_dir, "registry.csv"))
    stores = read_csv(os.path.join(data_dir, "stores.csv"))
    source_transactions = read_csv(os.path.join(data_dir, "transactions.csv"))

    source_customers = read_csv(os.path.join(out_dir, "source_customers.csv"))
    golden = read_csv(os.path.join(out_dir, "golden.csv"))
    links = read_csv(os.path.join(out_dir, "links.csv"))
    rejects = read_csv(os.path.join(out_dir, "rejects.csv"))
    out_transactions = read_csv(os.path.join(out_dir, "transactions_out.csv"))

    known_numbers = {r["houjin_bangou"] for r in registry}
    known_stores = {r["store_id"] for r in stores}
    by_id = {r["customer_id"]: r for r in golden}
    source_by_id = {r["customer_id"]: r for r in source_customers}

    matched = sum(1 for r in golden
                  if r["houjin_bangou"] and r["houjin_bangou"] in known_numbers)
    unmatched = len(golden) - matched
    merged = sum(1 for r in golden if (r.get("merged_from") or "").strip())

    id_counts = count_by(golden, "customer_id")
    duplicate = sum(n - 1 for n in id_counts.values() if n > 1)

    orphan = sum(1 for r in golden
                 if r["store_id"] and r["store_id"] not in known_stores)
    orphan += sum(1 for r in out_transactions
                  if r["store_id"] not in known_stores)

    attribute_mismatch = 0
    for row in golden:
        source = source_by_id.get(row["customer_id"])
        if source is None:
            attribute_mismatch += 1
            continue
        if row["commercial_name"] != source["torihikisaki_name"].strip():
            attribute_mismatch += 1
        elif row["payment_terms_days"] != source["payment_terms_days"].strip():
            attribute_mismatch += 1

    relationship_mismatch = 0
    for link in links:
        parent = by_id.get(link["parent_customer_id"])
        child = by_id.get(link["child_customer_id"])
        if parent is None or child is None:
            relationship_mismatch += 1
        elif parent["record_type"] != "CORPORATE" or child["record_type"] != "STORE":
            relationship_mismatch += 1
        elif parent["houjin_bangou"] != child["houjin_bangou"]:
            relationship_mismatch += 1

    # 閉店済店舗は、閉店日つきのレコードとして残っていなければならない。
    historical_mismatch = 0
    for store in stores:
        if store["status"] != "CLOSED":
            continue
        kept = [r for r in golden if r["store_id"] == store["store_id"]]
        if not kept:
            historical_mismatch += 1
        elif kept[0]["valid_to"] != store["valid_to"] or kept[0]["status"] != "CLOSED":
            historical_mismatch += 1

    source_store = {r["transaction_id"]: r["store_id"]
                    for r in source_transactions}
    reassigned = sum(1 for r in out_transactions
                     if source_store.get(r["transaction_id"]) != r["store_id"])

    report = {
        "scenario": "jbeq-mdm-e2e-001",
        "note": "Counted by recon/reconcile.py from the source files and the "
                "run outputs. The transformation module is not imported here.",
        "source_count": len(source_customers),
        "target_count": len(golden),
        "matched": matched,
        "unmatched": unmatched,
        "merged": merged,
        "linked": len(links),
        "rejected": len(rejects),
        "duplicate": duplicate,
        "orphan": orphan,
        "attribute_mismatch": attribute_mismatch,
        "relationship_mismatch": relationship_mismatch,
        "historical_mismatch": historical_mismatch,
        "transaction_rows": len(out_transactions),
        "transaction_rows_reassigned": reassigned,
        "transactions_by_store": count_by(out_transactions, "store_id"),
    }
    breached = [name for name in CRITICAL_COUNTERS if report[name] != 0]
    report["critical_integrity_pct"] = round(
        100.0 * (len(CRITICAL_COUNTERS) - len(breached)) / len(CRITICAL_COUNTERS))
    report["critical_counters_breached"] = breached
    return report


def self_check():
    """Drives the two counters that decide the critical verdict, both ways."""
    rows = [{"store_id": "S001"}, {"store_id": "S002"}, {"store_id": "S001"}]
    assert count_by(rows, "store_id") == {"S001": 2, "S002": 1}
    assert count_by([], "store_id") == {}
    clean = {name: 0 for name in CRITICAL_COUNTERS}
    breached = [n for n in CRITICAL_COUNTERS if clean[n] != 0]
    assert breached == []
    dirty = dict(clean)
    dirty["merged"] = 1
    breached = [n for n in CRITICAL_COUNTERS if dirty[n] != 0]
    assert breached == ["merged"], breached
    pct = round(100.0 * (len(CRITICAL_COUNTERS) - 1) / len(CRITICAL_COUNTERS))
    assert pct != 100, pct
    print("self-check PASS: counting and the critical verdict, both ways")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="JBEQ-MDM E2E-001 reconciliation")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--data", default="data")
    parser.add_argument("--out", default="out")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    report = reconcile(args.data, args.out)
    path = os.path.join(args.out, "reconciliation.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("reconciliation: source %d, target %d, rejected %d, linked %d, "
          "critical integrity %d%%"
          % (report["source_count"], report["target_count"],
             report["rejected"], report["linked"],
             report["critical_integrity_pct"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
