"""U3 の受け入れ検査: 照合スクリプトが移行モジュールから独立していること。"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard

_guard.require("recon/reconcile.py")
sys.path.insert(0, os.path.join(_guard.ROOT, "recon"))
import reconcile

# 独立性は IMPORT 文で見る。最初の版はソース全文に "mdm_transform" の文字が
# あるかどうかで見ていて、独立性をうたった自分のコメントに引っかかって落ちた
# (2026-09-05 の実行 1 回目)。名前に触れることと依存することは別。
IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+mdm_transform\b", re.MULTILINE)


def main():
    path = os.path.join(_guard.ROOT, "recon", "reconcile.py")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if IMPORT_RE.search(text):
        _guard.fail("照合スクリプトが移行モジュールを import している")
    if "mdm_transform" not in sys.modules:
        pass  # import しただけで移行モジュールが読み込まれないことの確認。
    else:
        _guard.fail("照合スクリプトの読み込みで移行モジュールが読み込まれた")

    rows = [dict(store_id="S001"), dict(store_id="S002"), dict(store_id="S001")]
    counted = reconcile.count_by(rows, "store_id")
    if counted != dict(S001=2, S002=1):
        _guard.fail("件数の集計が合わない: %s" % counted)
    if reconcile.count_by([], "store_id") != dict():
        _guard.fail("空入力の集計が空になっていない")
    if len(reconcile.CRITICAL_COUNTERS) != 6:
        _guard.fail("critical 判定の対象が 6 件ではない")
    for name in ("merged", "duplicate", "orphan", "relationship_mismatch",
                 "historical_mismatch", "transaction_rows_reassigned"):
        if name not in reconcile.CRITICAL_COUNTERS:
            _guard.fail("critical 判定に %s が入っていない" % name)

    print("check_reconcile PASS: 独立性、集計、critical 判定 6 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
