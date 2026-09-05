"""U1 の受け入れ検査: 移行モジュールが要件どおりの判定を持っているか。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard

_guard.require("src/mdm_transform.py", "src/field_map.json", "src/rule_book.json")
sys.path.insert(0, os.path.join(_guard.ROOT, "src"))
import mdm_transform as mt


def main():
    if mt.normalize_number("８１３0001000101") != "8130001000101":
        _guard.fail("法人番号の全角正規化ができていない")
    if mt.normalize_number("8130-0010-00101") != "8130001000101":
        _guard.fail("法人番号のハイフン除去ができていない")
    if mt.normalize_name("  アオバHD  ") != "アオバHD":
        _guard.fail("取引先名称の空白処理ができていない")

    store_row = dict(store_id="S001", merge_directive="MERGE_INTO:C001")
    plain_row = dict(store_id="S001", merge_directive="")
    corp_row = dict(store_id="", merge_directive="MERGE_INTO:C001")
    if not mt.merge_is_forbidden(store_row):
        _guard.fail("店舗単位の取引先の統合指示を拒否していない")
    if mt.merge_is_forbidden(plain_row):
        _guard.fail("統合指示のない行を拒否している")
    if mt.merge_is_forbidden(corp_row):
        _guard.fail("店舗単位ではない行まで拒否している")

    rules = mt.rule_book().get("rules", [])
    unknown = [r["id"] for r in rules if r["status"] == "UNKNOWN"]
    if unknown != ["R10", "R11"]:
        _guard.fail("未解決の業務ルールが R10 と R11 になっていない: %s" % unknown)
    fields = mt.field_map().get("fields", [])
    if len(fields) != 12:
        _guard.fail("項目マッピングが 12 項目ではない: %d" % len(fields))

    print("check_module PASS: 正規化、統合拒否、UNKNOWN 2 件、マッピング 12 項目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
