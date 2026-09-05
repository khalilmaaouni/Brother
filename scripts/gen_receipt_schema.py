#!/usr/bin/env python3
"""One-shot generator for docs/plan/delivery-receipt-v1.schema.json from the
field table in docs/plan/DELIVERY-RECEIPT-V1.md (row S8). Run by hand when the
document's field table changes; scripts/test_receipt_contract.py is what
checks the two stay in sync on every run, not this script.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_receipt_contract as trc  # noqa: E402

SCHEMA_PATH = os.path.join(os.path.dirname(HERE), "docs", "plan",
                           "delivery-receipt-v1.schema.json")


def build():
    fields = trc.load_field_table()
    frozen = []
    for path, type_word, required, question in fields:
        cardinality = "per_element" if required == "per element" else "single"
        entry = dict(path=path, type=type_word, required=True,
                     cardinality=cardinality, question=question)
        frozen.append(entry)
    comment = ("Delivery Receipt v1 contract, frozen field list. Generated "
               "from the field table in docs/plan/DELIVERY-RECEIPT-V1.md "
               "(row S8). This list grows, never shrinks: a later contract "
               "version may append a field to frozen_fields, never remove "
               "or retype one already here. "
               "scripts/test_receipt_contract.py checks this file against "
               "the document and against a real generated receipt.")
    schema = dict(contract_version="1.1",
                  source_document="docs/plan/DELIVERY-RECEIPT-V1.md",
                  frozen_fields=frozen)
    schema["$comment"] = comment
    return schema


def main():
    schema = build()
    with open(SCHEMA_PATH, "w", encoding="utf-8") as fh:
        json.dump(schema, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    count = len(schema["frozen_fields"])
    print("wrote %d frozen fields to %s" % (count, SCHEMA_PATH))


if __name__ == "__main__":
    main()
