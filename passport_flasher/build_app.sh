#!/bin/bash
# 在 macOS 上打包成「AI Passport 燒錄工具.app」→ dist/
# 用法：./build_app.sh
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller
VERSION=$(.venv/bin/python -c "import flasher_core; print(flasher_core.APP_VERSION)")
rm -rf build dist
.venv/bin/pyinstaller --noconfirm --clean --windowed \
  --name "PassportFlasher" \
  --osx-bundle-identifier "io.github.lyu0608.passportflasher" \
  --collect-all esptool \
  --collect-data certifi \
  --add-data "CHANGELOG.md:." \
  --hidden-import serial.tools.list_ports \
  --hidden-import PIL.ImageTk \
  app.py
APP="dist/PassportFlasher.app"
if [ -d "$APP" ]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist" || true
  /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 'AI Passport 燒錄工具'" "$APP/Contents/Info.plist" 2>/dev/null || true
  # 沒有 Apple 開發者簽章：做 ad-hoc 簽章，第一次開啟時在 Finder 按右鍵 → 打開
  codesign --force --deep --sign - "$APP" || true
  mv "$APP" "dist/AI Passport 燒錄工具.app"
  (cd dist && ditto -c -k --keepParent "AI Passport 燒錄工具.app" "PassportFlasher-$VERSION-macOS.zip")
  echo "完成：dist/AI Passport 燒錄工具.app"
  echo "      dist/PassportFlasher-$VERSION-macOS.zip"
fi
