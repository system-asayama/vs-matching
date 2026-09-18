# vs-matching

Docker Compose (Flask + PostgreSQL)

## ログインシステム

- ロールは **管理者 (admin)** と **利用者 (user)** の2種類
- 最初の管理者は **ブートストラップ** で登録する
  - 管理者が1人もいない状態でアクセスすると `/setup` に誘導され、そこで最初の管理者を登録する
  - 登録後 `/setup` は自動的に無効になる
  - 自動デプロイ向けに、環境変数 `ADMIN_USERNAME` と `ADMIN_PASSWORD` を両方指定した場合は起動時に自動作成する
- 利用者の自己登録は無く、**管理者だけが利用者を登録**できる
- パスワードはハッシュ化して保存。8文字以上

### 画面

| パス | 権限 | 内容 |
|---|---|---|
| `/setup` | 未設定時のみ | 最初の管理者を登録 |
| `/login` | 全員 | ログイン |
| `/dashboard` | ログイン済み | 自分の情報 |
| `/settings` | ログイン済み | 表示名・パスワード変更 |
| `/admin/users` | 管理者 | 利用者一覧、ロール変更、停止/再開、パスワード再設定、削除 |
| `/admin/users/new` | 管理者 | 利用者の登録 |

最後の有効な管理者は降格・停止・削除できないよう保護しています。

## 起動

```bash
docker compose up --build
```

`http://localhost:8080` を開くと初期セットアップ画面が表示されます。

### 環境変数

| 変数 | 既定値 | 説明 |
|---|---|---|
| `WEB_PORT` | `8080` | ホスト側ポート |
| `SECRET_KEY` | `dev-secret-change-me` | セッション署名鍵。本番では必ず変更 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 未設定 | 両方指定時のみ初期管理者を自動作成 |
| `DATABASE_URL` | compose で設定 | 未設定時はローカル SQLite (`instance/app.db`) |

## ローカル開発（Docker なし）

```bash
pip install -r requirements.txt
python app.py
```

`http://localhost:8000` で起動します（SQLite 使用）。

## テスト

```bash
python -m pytest -q
```
