# 顔の丸切り抜き＋背景ぼかしツール

画像を入れると顔を自動で見つけて、顔を中心に丸く切り抜き、円の外側をぼかします。

## ダウンロード（Windows）
[最新リリース](https://github.com/04tetsu03ya/face-circle-tool/releases/latest) から
`FaceCircleTool_Windows.zip` をダウンロードし、展開して `顔丸切り抜きツール.bat` をダブルクリックします。
Python のインストールは不要です。2回目以降は、起動時に自動で更新を確認します。

## できること
- 顔の自動検出（見つからないときはクリックで指定）
- プレビューをドラッグで位置調整、ホイールで円の大きさ調整
- 仕上がり: 切り抜き＋背景ぼかし / 元サイズのまま背景ぼかし / 円の外を透明にした PNG
- ぼかしの強さ、背景を暗く、白いふち、出力サイズ
- 画像のドラッグ&ドロップ、複数枚の一括保存

## コマンドで使う（Python がある場合）
    pip install -r requirements.txt
    python app/face_circle_blur.py photo.jpg            # photo_circle.png を出力
    python app/face_circle_blur.py photo.jpg --mode full
    python app/gui.py                                    # 画面版

## 新しいバージョンを出す手順
1. `app/version.txt` の番号を上げ、`CHANGELOG_LATEST.md` に変更点を書く
2. main に入れる（push またはプルリクエストのマージ）
3. GitHub Actions が Windows 版 zip と自動更新用 `app.zip` を作り、`v<番号>` のリリースを公開します。
   配布済みのツールは次回起動時に更新を案内します。

自動更新は `app/update_config.json` に書かれたリポジトリの最新リリースにある `app.zip` を見ています。
