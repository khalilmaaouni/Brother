#!/usr/bin/env python3
"""JBEQ-MDM E2E-001 の移行処理 (取引先マスタ統合).

要件は requirement.ja.md の第1節。ここでの実装方針:

  - 法人名称は登記情報を正とする (survivorship: registry wins).
  - 取引先名称は営業側の既存値を維持する (survivorship: sales master wins).
  - 法人番号が一致しても、店舗単位の取引先は LINK するだけで MERGE しない.
  - 閉店済店舗の履歴は保持し、新規店舗へ付替えない.
  - 過去伝票は当時の店舗に紐づいたまま出力する.

The two questions the requirement leaves open (payment terms precedence and
the effective date of a legal name correction) are carried as UNKNOWN into
decisions.json rather than answered here. A migration that answers a
business question on its own authority is the failure this scenario exists
to catch.

Python 3, standard library only.
"""
import argparse
import csv
import json
import os
import sys

# The contradictory row injected at run time. It asks for the one thing the
# requirement forbids: folding a store level customer into its parent on a
# shared corporate number.
CONTRADICTION = {
    "customer_id": "C008",
    "torihikisaki_name": "アオバ 丸の内店(統合)",
    "houjin_bangou": "8130001000101",
    "store_id": "S001",
    "parent_customer_id": "",
    "payment_terms_days": "30",
    "merge_directive": "MERGE_INTO:C001",
}

GOLDEN_HEADER = [
    "customer_id", "houjin_bangou", "legal_name", "commercial_name",
    "record_type", "store_id", "parent_customer_id", "payment_terms_days",
    "valid_from", "valid_to", "status", "legal_name_source",
    "commercial_name_source", "merged_from",
]

LINKS_HEADER = [
    "parent_customer_id", "child_customer_id", "relation_type",
    "effective_from", "effective_to", "basis",
]

REJECTS_HEADER = ["customer_id", "reason_code", "reason_ja", "source_value"]

SOURCE_CUSTOMERS_HEADER = [
    "customer_id", "torihikisaki_name", "houjin_bangou", "store_id",
    "parent_customer_id", "payment_terms_days", "merge_directive",
]

TRANSACTIONS_HEADER = [
    "transaction_id", "store_id", "transaction_date", "amount_jpy",
]

STATUS_MAP = {"OPEN": "ACTIVE", "CLOSED": "CLOSED"}

# 全角数字を半角に落とすための対応表。法人番号は入力側で全角混じりのことが
# あるため、突合の前に必ず通す。
ZENKAKU_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_number(value):
    """法人番号の正規化: 空白、ハイフン、全角数字を落として13桁の文字列にする。"""
    if value is None:
        return ""
    text = value.strip().translate(ZENKAKU_DIGITS)
    for junk in (" ", "　", "-", "‐", "－"):
        text = text.replace(junk, "")
    return text


def normalize_name(value):
    """取引先名称の正規化: 前後の空白だけ落とす。中身は営業側の値を維持する。"""
    return (value or "").strip()


def is_store_level(row):
    """店舗単位で管理している取引先か。"""
    return bool((row.get("store_id") or "").strip())


def merge_is_forbidden(row):
    """統合指示を受け入れてよいか。店舗単位の取引先は常に拒否する。

    要件の「法人番号が一致する場合でも、店舗単位で管理している取引先は
    自動統合せず」がこの関数一つに落ちている。
    """
    directive = (row.get("merge_directive") or "").strip()
    if not directive:
        return False
    return is_store_level(row)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def transform(data_dir, inject_contradiction=False):
    """ソースを読み、golden、links、rejects、伝票を返す。副作用なし。"""
    registry = read_csv(os.path.join(data_dir, "registry.csv"))
    customers = read_csv(os.path.join(data_dir, "sales_master.csv"))
    stores = read_csv(os.path.join(data_dir, "stores.csv"))
    transactions = read_csv(os.path.join(data_dir, "transactions.csv"))

    if inject_contradiction:
        customers = customers + [dict(CONTRADICTION)]

    by_number = {normalize_number(r["houjin_bangou"]): r for r in registry}
    by_store = {r["store_id"]: r for r in stores}

    golden = []
    rejects = []
    for row in customers:
        number = normalize_number(row.get("houjin_bangou"))
        name = normalize_name(row.get("torihikisaki_name"))

        if merge_is_forbidden(row):
            rejects.append({
                "customer_id": row["customer_id"],
                "reason_code": "MERGE_FORBIDDEN_STORE_LEVEL",
                "reason_ja": "店舗単位の取引先は法人番号が一致しても自動統合しない",
                "source_value": row.get("merge_directive", ""),
            })
            continue
        if not name:
            rejects.append({
                "customer_id": row["customer_id"],
                "reason_code": "MISSING_COMMERCIAL_NAME",
                "reason_ja": "取引先名称が空のため営業側で使用できない",
                "source_value": "",
            })
            continue
        if number and number not in by_number:
            rejects.append({
                "customer_id": row["customer_id"],
                "reason_code": "UNKNOWN_HOUJIN_BANGOU",
                "reason_ja": "登記情報に存在しない法人番号",
                "source_value": number,
            })
            continue

        store_id = (row.get("store_id") or "").strip()
        if store_id and store_id not in by_store:
            rejects.append({
                "customer_id": row["customer_id"],
                "reason_code": "ORPHAN_STORE",
                "reason_ja": "店舗マスタに存在しない店舗ID",
                "source_value": store_id,
            })
            continue

        registered = by_number.get(number, {})
        if store_id:
            store = by_store[store_id]
            valid_from = store["valid_from"]
            valid_to = store["valid_to"]
            status = STATUS_MAP.get(store["status"])
            if status is None:
                rejects.append({
                    "customer_id": row["customer_id"],
                    "reason_code": "UNKNOWN_STORE_STATUS",
                    "reason_ja": "店舗ステータスの値が定義外",
                    "source_value": store["status"],
                })
                continue
            record_type = "STORE"
        else:
            valid_from = registered.get("registered_from", "")
            valid_to = ""
            status = "ACTIVE"
            record_type = "CORPORATE"

        golden.append({
            "customer_id": row["customer_id"],
            "houjin_bangou": number,
            # 法人名称: 登記情報を正とする。営業側の名称では上書きしない。
            "legal_name": registered.get("registered_name", ""),
            # 取引先名称: 営業側の既存名称を維持する。登記名では上書きしない。
            "commercial_name": name,
            "record_type": record_type,
            "store_id": store_id,
            "parent_customer_id": (row.get("parent_customer_id") or "").strip(),
            "payment_terms_days": (row.get("payment_terms_days") or "").strip(),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "status": status,
            "legal_name_source": "registry" if registered else "",
            "commercial_name_source": "sales_master",
            # 統合は行わないので常に空。
            "merged_from": "",
        })

    corporate = {r["customer_id"]: r for r in golden
                 if r["record_type"] == "CORPORATE"}
    links = []
    for row in golden:
        if row["record_type"] != "STORE":
            continue
        parent_id = row["parent_customer_id"]
        parent = corporate.get(parent_id)
        if not parent or parent["houjin_bangou"] != row["houjin_bangou"]:
            # 親が法人取引先でない、または法人番号が違う場合は関係を作らない。
            continue
        links.append({
            "parent_customer_id": parent_id,
            "child_customer_id": row["customer_id"],
            "relation_type": "CORPORATE_STORE",
            # 関係の有効期間は子である店舗レコードの有効期間に従う。
            "effective_from": row["valid_from"],
            "effective_to": row["valid_to"],
            "basis": "shared_houjin_bangou_and_parent_link",
        })

    # 伝票は当時の店舗に紐づいたまま。新規店舗への付替えは行わない。
    carried = [dict(t) for t in transactions]
    return {
        "customers_in": customers,
        "golden": golden,
        "links": links,
        "rejects": rejects,
        "transactions": carried,
    }


def field_map():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "field_map.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rule_book():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "rule_book.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run(data_dir, out_dir, inject_contradiction=False):
    result = transform(data_dir, inject_contradiction=inject_contradiction)
    write_csv(os.path.join(out_dir, "source_customers.csv"),
              SOURCE_CUSTOMERS_HEADER, result["customers_in"])
    write_csv(os.path.join(out_dir, "golden.csv"), GOLDEN_HEADER,
              result["golden"])
    write_csv(os.path.join(out_dir, "links.csv"), LINKS_HEADER,
              result["links"])
    write_csv(os.path.join(out_dir, "rejects.csv"), REJECTS_HEADER,
              result["rejects"])
    write_csv(os.path.join(out_dir, "transactions_out.csv"),
              TRANSACTIONS_HEADER, result["transactions"])
    write_json(os.path.join(out_dir, "mapping.json"), field_map())
    write_json(os.path.join(out_dir, "decisions.json"), rule_book())
    return result


def self_check():
    """The smallest thing that fails if the rules above break."""
    assert normalize_number(" ８１３0001000101 ") == "8130001000101"
    assert normalize_number("8130-0010-00101") == "8130001000101"
    assert normalize_number(None) == ""
    assert normalize_name("  アオバHD  ") == "アオバHD"
    store_row = {"store_id": "S001", "merge_directive": "MERGE_INTO:C001"}
    corp_row = {"store_id": "", "merge_directive": "MERGE_INTO:C001"}
    plain_row = {"store_id": "S001", "merge_directive": ""}
    assert is_store_level(store_row) is True
    assert is_store_level(corp_row) is False
    assert merge_is_forbidden(store_row) is True
    assert merge_is_forbidden(plain_row) is False
    # A corporate level merge directive is not what this requirement forbids,
    # so this function must not claim it does.
    assert merge_is_forbidden(corp_row) is False
    rules = rule_book()["rules"]
    unknown = [r["id"] for r in rules if r["status"] == "UNKNOWN"]
    assert unknown == ["R10", "R11"], unknown
    print("self-check PASS: normalization, merge refusal, two UNKNOWN rules")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="JBEQ-MDM E2E-001 migration")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--data", default="data")
    parser.add_argument("--out", default="out")
    parser.add_argument("--inject-contradiction", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.run:
        result = run(args.data, args.out,
                     inject_contradiction=args.inject_contradiction)
        print("golden %d, links %d, rejects %d, transactions %d"
              % (len(result["golden"]), len(result["links"]),
                 len(result["rejects"]), len(result["transactions"])))
        return 0
    parser.error("name --self-check or --run")


if __name__ == "__main__":
    sys.exit(main())
