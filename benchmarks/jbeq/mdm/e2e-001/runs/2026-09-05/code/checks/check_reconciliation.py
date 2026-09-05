"""U5 の受け入れ検査: 独立照合の件数と critical 判定。

件数を読むだけでなく、照合スクリプトでもう一度数え直して突き合わせます。
読むだけでは、照合コードを一行も通らずにこの検査が通ってしまいます。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard

_guard.require("recon/reconcile.py", "out/reconciliation.json",
               "out/golden.csv", "out/links.csv", "out/rejects.csv",
               "out/transactions_out.csv", "out/source_customers.csv")

sys.path.insert(0, os.path.join(_guard.ROOT, "recon"))
import reconcile

EXPECTED = [
    ("source_count", 8),
    ("target_count", 7),
    ("matched", 7),
    ("unmatched", 0),
    ("merged", 0),
    ("linked", 4),
    ("rejected", 1),
    ("duplicate", 0),
    ("orphan", 0),
    ("attribute_mismatch", 0),
    ("relationship_mismatch", 0),
    ("historical_mismatch", 0),
    ("transaction_rows", 5),
    ("transaction_rows_reassigned", 0),
    ("critical_integrity_pct", 100),
]


def main():
    path = os.path.join(_guard.ROOT, "out", "reconciliation.json")
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    for key, want in EXPECTED:
        if report.get(key) != want:
            _guard.fail("%s が %s ではない: %s" % (key, want, report.get(key)))
    by_store = report.get("transactions_by_store") or dict()
    if by_store.get("S002") != 3 or by_store.get("S001") != 1 or by_store.get("S003") != 1:
        _guard.fail("伝票の店舗別件数が合わない: %s" % by_store)
    if "S004" in by_store:
        _guard.fail("伝票が新規店舗に付替えられている: %s" % by_store)
    if report.get("critical_counters_breached") != []:
        _guard.fail("critical 判定に違反がある: %s"
                    % report.get("critical_counters_breached"))

    again = reconcile.reconcile(os.path.join(_guard.ROOT, "data"),
                                os.path.join(_guard.ROOT, "out"))
    for key, _ in EXPECTED:
        if again.get(key) != report.get(key):
            _guard.fail("数え直すと %s が変わる: %s と %s"
                        % (key, report.get(key), again.get(key)))
    if again.get("transactions_by_store") != by_store:
        _guard.fail("数え直すと伝票の店舗別件数が変わる")

    print("check_reconciliation PASS: 15 項目一致、数え直しも一致、"
          "critical data integrity 100%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
