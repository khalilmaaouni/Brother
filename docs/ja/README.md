# Brother 日本語ドキュメント

**AI が「完了」と言ったあとに、何を信頼できるかを見えるようにする。**

Brother は AI にもっと多くの作業をさせるためだけの仕組みではありません。あとで人が確認・承認する仕事について、変更、チェック、証拠、未確認事項を残すためのレイヤーです。

## 一番大切な考え方

Brother には 1 つの入口があります。ユーザーが BrotherMode や BrotherSBE を選ぶ必要はありません。

小さく、すぐ戻せて、あとで証拠を求められない仕事なら、Brother は余計な ceremony を増やすべきではありません。

重要な変更は bounded work と receipt。お金、認証、顧客データ、migration、本番経路、意思決定に使う数値などは、より強い assurance が必要です。

## Verdict

| Verdict | 意味 |
| --- | --- |
| `PASS` | 指定された evidence が指定された claim を支持する。 |
| `FAIL` | evidence が claim と矛盾する。 |
| `NO-DATA` | evidence から答えを確定できない。 |

`NO-DATA` は「だいたい OK」ではありません。変更前から check が green、check が実行できない、対象を本当に検証していない、などの時に必要な正しい状態です。

## 人の承認

Brother が evidence をまとめても、人の acceptance や release 判断を自動的に置き換えるわけではありません。

## Vault

Vault は lesson、constraint、failure、decision の local memory です。Memory は proof ではありません。現在の code、evidence、人の decision が優先されます。

## 次に読む

- [クイックスタート](quickstart.md)
- [Evidence model](evidence-model.md)
- [Professional personas](personas.md)
- English full docs: [../README.md](../README.md)
