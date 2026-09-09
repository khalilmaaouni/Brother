---
id: VRB-P065
name: 検証用の設定が本番の設定を上書きしていた
type: lesson
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, ja]
description: larch_ripple.sh の検証用の設定が本番の設定を上書きし、切り戻すまで誰も気づかなかった。
---

larch_ripple.sh は環境ごとに別の設定を読む。上書きの順序を記録に残し、どちらが先に勝つかを書いておく。

この記録が指すファイルは alpha-app/larch-ripple/larch_ripple.sh にある。

Files touched in the same incident: GraniteRoster.swift.

What to do instead: 案件名と基底名を必ず一緒に記録する.
