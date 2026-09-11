# Evidence model

Brother では「test が green」だけで proof とは考えません。

Bug fix のための test が変更後に PASS しても、変更前にも PASS していたら、その test は fix を証明していません。この場合 `NO-DATA` が正しいことがあります。

## Independent oracle

Code を書いた同じ AI が test も書くこと自体は問題ではありません。ただし independent review とは限りません。Business rule、API contract、別計算、source-system reconciliation、人が指定した expected result など、実装とは別の根拠が必要になる場合があります。

## Authority

Evidence があっても acceptance と release は人の decision です。
