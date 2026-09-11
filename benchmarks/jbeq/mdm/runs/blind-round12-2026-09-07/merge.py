import json

with open("engine-answers.json", encoding="utf-8") as f:
    engine = json.load(f)
with open("direct-answers.json", encoding="utf-8") as f:
    direct = json.load(f)

merged = dict(engine)
overridden = []
for k, v in direct.items():
    if k not in merged:
        raise SystemExit(f"direct answer {k} not in engine answers")
    overridden.append((k, merged[k], v))
    merged[k] = v

assert len(merged) == 70, len(merged)

with open("answers.json", "w", encoding="utf-8") as f:
    json.dump(dict(sorted(merged.items())), f, ensure_ascii=False, indent=1, sort_keys=False)
    f.write("\n")

print("merged", len(merged), "answers; overrode", len(overridden), "unsupported-track entries")
for k, old, new in overridden:
    print(f"  {k}: engine={old!r} -> direct={new!r}")
