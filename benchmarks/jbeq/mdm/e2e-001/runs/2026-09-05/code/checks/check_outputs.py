"""U4 の受け入れ検査: 移行出力が要件どおりで、かつコードから再現できること。

要件の条文ごとに一つずつ見ています。処理が成功したことではなく、出力が
要件に反していないことを確認します。

最後に再現性を見ます。out/ の中身を、同じソースから同じコードでもう一度
作り直して突き合わせます。これが無いと、この検査は out/ のファイルを読んで
いるだけで、移行コードを一切通っていません (実行 1 回目でエンジンがまさに
それを NO-DATA として指摘しました: U1 のファイルを戻してもこの検査が通って
しまう、と)。
"""
import csv
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard

_guard.require("src/mdm_transform.py", "out/golden.csv", "out/links.csv",
               "out/rejects.csv", "out/transactions_out.csv",
               "out/source_customers.csv", "out/mapping.json",
               "out/decisions.json")

sys.path.insert(0, os.path.join(_guard.ROOT, "src"))
import mdm_transform

REPRODUCED = ["golden.csv", "links.csv", "rejects.csv",
              "transactions_out.csv", "source_customers.csv",
              "mapping.json", "decisions.json"]


def read(name):
    with open(os.path.join(_guard.ROOT, "out", name),
              encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def check_reproducible():
    tmp = tempfile.mkdtemp(prefix="jbeq-reproduce-")
    try:
        mdm_transform.run(os.path.join(_guard.ROOT, "data"),
                          os.path.join(tmp, "out"),
                          inject_contradiction=True)
        for name in REPRODUCED:
            again = read_bytes(os.path.join(tmp, "out", name))
            shipped = read_bytes(os.path.join(_guard.ROOT, "out", name))
            if again != shipped:
                _guard.fail("out/%s が現在のコードから再現できない" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    golden = read("golden.csv")
    links = read("links.csv")
    rejects = read("rejects.csv")
    transactions = read("transactions_out.csv")

    if len(golden) != 7:
        _guard.fail("ゴールデンレコードが 7 件ではない: %d" % len(golden))
    if len(links) != 4:
        _guard.fail("親子関係が 4 件ではない: %d" % len(links))
    if len(rejects) != 1:
        _guard.fail("却下が 1 件ではない: %d" % len(rejects))
    if rejects[0]["reason_code"] != "MERGE_FORBIDDEN_STORE_LEVEL":
        _guard.fail("却下理由が違う: %s" % rejects[0]["reason_code"])
    if rejects[0]["customer_id"] != "C008":
        _guard.fail("却下された行が注入した矛盾行ではない: %s" % rejects[0])

    by_id = dict((r["customer_id"], r) for r in golden)
    if "C008" in by_id:
        _guard.fail("矛盾行がゴールデンレコードに入っている")
    for row in golden:
        if row["merged_from"]:
            _guard.fail("統合が発生している: %s" % row["customer_id"])

    shared = [r for r in golden if r["houjin_bangou"] == "8130001000101"]
    if len(shared) != 4:
        _guard.fail("同一法人番号の 4 件が保持されていない: %d" % len(shared))

    if by_id["C002"]["legal_name"] != "株式会社青葉ホールディングス":
        _guard.fail("法人名称が登記情報になっていない")
    if by_id["C002"]["commercial_name"] != "アオバ 丸の内店":
        _guard.fail("取引先名称が既存名称になっていない")
    if by_id["C003"]["status"] != "CLOSED" or by_id["C003"]["valid_to"] != "2025-03-31":
        _guard.fail("閉店済店舗の履歴が保持されていない: %s" % by_id["C003"])
    if by_id["C007"]["store_id"] != "S004":
        _guard.fail("新規店舗が別レコードになっていない")

    for link in links:
        parent = by_id.get(link["parent_customer_id"])
        child = by_id.get(link["child_customer_id"])
        if parent is None or child is None:
            _guard.fail("関係の片側にレコードがない: %s" % link)
        if parent["record_type"] != "CORPORATE" or child["record_type"] != "STORE":
            _guard.fail("親子関係が逆転している: %s" % link)

    by_store = dict()
    for row in transactions:
        by_store[row["store_id"]] = by_store.get(row["store_id"], 0) + 1
    if by_store.get("S002") != 3:
        _guard.fail("閉店済店舗の伝票 3 件が残っていない: %s" % by_store)
    if "S004" in by_store:
        _guard.fail("伝票が新規店舗へ付替えられている: %s" % by_store)

    check_reproducible()

    print("check_outputs PASS: ゴールデン 7、関係 4、却下 1、閉店履歴保持、"
          "付替えなし、7 ファイルが現在のコードから再現")
    return 0


if __name__ == "__main__":
    sys.exit(main())
