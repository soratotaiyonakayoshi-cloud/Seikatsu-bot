#!/usr/bin/env bash
# 生活リズムBot セットアップ（Oracle Cloud / Ubuntu 想定）  使い方: bash setup.sh
set -euo pipefail
echo "==> 必要パッケージのインストール"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git ffmpeg libopus0 libffi-dev
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "==> 仮想環境を作成: $APP_DIR/venv"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
echo ""
echo "==> 完了！次にやること:"
echo "  1. cp .env.example .env && nano .env   (トークンを入力)"
echo "  2. venv/bin/python bot.py              (動作確認)"
echo "  3. systemdで常駐化 (README.md 参照)"
