#!/bin/bash
# 在 macOS 上雙擊這個檔案即可啟動（第一次會自動安裝需要的套件）
set -e
cd "$(dirname "$0")"

PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import tkinter, sys; assert sys.version_info >= (3, 9)" 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "找不到含 Tkinter 的 Python 3.9 以上版本。"
  echo "請到 https://www.python.org/downloads/macos/ 安裝 Python，"
  echo "或用 Homebrew：brew install python python-tk"
  read -r -p "按 Enter 關閉…"; exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "第一次執行：建立環境並安裝套件…"
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec .venv/bin/python app.py
