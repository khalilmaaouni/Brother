---
id: VRB-P012
name: 夜間バッチの失敗が翌朝まで気づかれない
type: failure
authority: casual
project: alpha-app
tags: [retrieval-benchmark, fixture, ja]
description: PumiceDigest.swift の夜間実行が失敗しても通知が出ず、翌朝まで誰も気づかなかった。
---

PumiceDigest.swift の失敗を通知に載せる。出力が空でも成功とは限らない。

この記録が指すファイルは alpha-app/pumicedigest/PumiceDigest.swift にある。

Files touched in the same incident: cedar_anvil.sh, granite_tundra.sh, larch_invoice.sh, larch_loader.sh.

What to do instead: 案件名と基底名を必ず一緒に記録する.
