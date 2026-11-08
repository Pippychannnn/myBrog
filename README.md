# Qiita 自動投稿

`qiita/articles/*.md` の記事を GitHub Actions から Qiita API に投稿します。

## 初回設定

1. Qiita のアクセストークンを発行する。
2. GitHub リポジトリの Settings → Secrets and variables → Actions で、`QIITA_TOKEN` という名前の Secret にトークンを登録する。
3. Actions がリポジトリへ投稿状態を書き戻せるよう、Settings → Actions → General → Workflow permissions で **Read and write permissions** を有効にする。
4. `qiita/articles/example.md` を複製して記事を作り、front matter の `title`、`tags`、本文を編集する。
5. 投稿したい記事だけ `published: true` にして `main` へ push する。

Qiita のアクセストークンはリポジトリへ保存せず、必ず GitHub Secrets に登録してください。

## 記事フォーマット

```markdown
---
title: "記事タイトル"
tags:
  - Python
  - Qiita
published: true
private: false
coediting: false
tweet: false
---

記事本文
```

`published: false` の記事は投稿されません（`published` を省略した場合も false として扱います）。`private: true` にすると Qiita の限定公開記事になります。Qiita の任意の組織へ投稿する場合は `organization_url_name` を追加できます。

## 更新の仕組み

初回投稿後、`.qiita/state.json` に記事ファイルと Qiita の item ID の対応が自動保存されます。同じファイルを編集して push すると、既存記事を更新します。投稿済み記事を削除しても Qiita 側の記事は削除しません。

Actions の `workflow_dispatch` から手動実行することもできます。

## ローカル検証

```bash
python3 scripts/publish_qiita.py --root . --state .qiita/state.json --dry-run
```
