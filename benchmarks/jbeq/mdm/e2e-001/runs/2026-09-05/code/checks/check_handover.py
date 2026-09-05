"""U6 の受け入れ検査: 日本語の引き継ぎ書。

11 節が揃っていることに加えて、データ照合の節に書かれた件数が
out/reconciliation.json と一致していることを見ます。引き継ぎ書に手で書いた
数字が載るのが一番よくある事故なので、そこを検査に含めます。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard

_guard.require("out/handover.ja.md", "out/reconciliation.json")

SECTIONS = [
    "変更内容", "理由", "正とする情報源", "業務ルール", "技術実装", "テスト",
    "データ照合", "残存リスク", "未解決の業務上の疑問", "切り戻し", "次の手順",
]

# 引き継ぎ書に必ず現れる件数。左が照合結果のキー、右が本文中の行の頭。
QUOTED = [
    ("source_count", "| source | "),
    ("target_count", "| target | "),
    ("linked", "| linked | "),
    ("rejected", "| rejected | "),
]


def main():
    path = os.path.join(_guard.ROOT, "out", "handover.ja.md")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(os.path.join(_guard.ROOT, "out", "reconciliation.json"),
              encoding="utf-8") as fh:
        report = json.load(fh)

    missing = [s for s in SECTIONS if s not in text]
    if missing:
        _guard.fail("節が足りない: %s" % "、".join(missing))
    if len(text) < 1500:
        _guard.fail("引き継ぎ書が短すぎる: %d 文字" % len(text))
    for phrase in ("支払条件", "登記更新日"):
        if phrase not in text:
            _guard.fail("未解決の疑問に %s が書かれていない" % phrase)

    for key, prefix in QUOTED:
        line = prefix + str(report[key])
        if line not in text:
            _guard.fail("データ照合の節の %s が照合結果と合わない (%r を探した)"
                        % (key, line))
    if "critical data integrity は 100% です" not in text:
        _guard.fail("critical data integrity の記載がない")

    print("check_handover PASS: 11 節すべてあり、照合結果の 4 件数が一致、"
          "未解決の疑問 2 件を明記")
    return 0


if __name__ == "__main__":
    sys.exit(main())
