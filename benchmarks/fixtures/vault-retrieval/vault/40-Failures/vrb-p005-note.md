---
id: VRB-P005
name: 同じ名前のファイルが二つの案件にあると取り違える
type: failure
authority: casual
project: gamma-site
tags: [retrieval-benchmark, fixture, ja]
description: 二つの案件に同じ基底名のファイルがあり、DeltaWarden.swift の記録がもう一方の案件の作業中に出てきた。
---

基底名だけで記録を引くと案件をまたいで一致してしまう。DeltaWarden.swift を扱うときは案件名も一緒に確認する。

この記録が指すファイルは gamma-site/deltawarden/DeltaWarden.swift にある。

Files touched in the same incident: cedar_anvil.sh, gamma_cache.sh, granite_tundra.sh, larch_invoice.sh, larch_loader.sh.

What to do instead: 案件名と基底名を必ず一緒に記録する.
