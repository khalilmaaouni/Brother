---
id: VRB-P047
name: 設定の既定値が本番と検証で違う
type: lesson
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, ja]
description: SigmaQuota.swift の既定値が本番と検証で異なり、検証で通った設定が本番で落ちた。
---

SigmaQuota.swift の既定値を一箇所に置き、両方の環境が同じ値を読む。

この記録が指すファイルは gamma-site/sigmaquota/SigmaQuota.swift にある。

Files touched in the same incident: CedarQuota.swift, MarbleQuota.swift, OnyxSync.swift, granite_tundra.sh.

What to do instead: 案件名と基底名を必ず一緒に記録する.
