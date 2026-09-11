# Brother クイックスタート

## Claude Code

```bash
claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother
```

Repository を開き、普通の言葉で outcome を伝えます。Internal product を選ぶ必要はありません。

```text
/brother この API の retry を idempotent にして、同じ event が 2 回来ても副作用が 1 回だけになることを証明して
```

## 完了メッセージより receipt を見る

- 何の file が変わったか
- どの check が走ったか
- 変更前にその check は fail したか
- oracle は implementation から独立しているか
- `NO-DATA` が残っていないか
- 最後に人が判断することは何か

## Codex

Codex は Claude Code と同じ slash-command surface ではありません。Codex 用 installation と hook trust を使い、installed Brother skill/runtime から実行します。English: [Install on Codex](../how-to/install-codex.md).
