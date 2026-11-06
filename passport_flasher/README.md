# AI Passport 燒錄工具（macOS）

用 USB-C 更新 [FoloToy AI Passport](https://github.com/FoloToy/ai-passport) 的圖形介面工具。by Claude Code

| 分頁 | 做什麼 |
| --- | --- |
| **官方社區玩法** | 直接瀏覽 [AI Passport 玩法社區](https://ai-passport.folotoy.cn/plays/)，搜尋、分類、排序，選了就上傳（和官方網頁安裝方式相同） |
| **開源專案** | GitHub 專案 → 下載 → ESP-IDF 編譯 → 上傳。內建 ★ [客語朗讀練習](https://github.com/lyu0608/hakkaataipassport) 與 FoloToy 官方示範，可自行新增 |
| **韌體檔** | 免編譯：選本機 `.bin`，或從 GitHub Releases 下載 `.bin`，自動判斷寫入位址 |
| **上傳紀錄** | 每次上傳的時間、內容、版本、裝置 MAC、結果；可一鍵重新上傳 |

右上角「版本紀錄」可看本工具的版本與 [CHANGELOG](CHANGELOG.md)；開源專案的「版本歷史…」可看提交紀錄並選舊版本編譯。

## 安裝與啟動

1. 安裝 Python 3（建議從 [python.org](https://www.python.org/downloads/macos/) 安裝，已含 Tkinter）
2. 在 Finder 雙擊 `run.command`（第一次會自動安裝 esptool 等套件）

或在終端機執行：

```bash
./run.command
```

### 打包成 .app

```bash
./build_app.sh
```

產生 `dist/AI Passport 燒錄工具.app` 與 zip。沒有 Apple 開發者簽章，第一次開啟請在 Finder **按右鍵 → 打開**。

## 使用方式

1. 用**可傳資料**的 USB-C 線接上 AI Passport 並開機，序列埠會自動出現 `/dev/cu.usbmodem…（✅ AI Passport）`
2. 選一個分頁挑要上傳的內容，按「⬆ 上傳到 AI Passport」
3. 確認視窗會列出要寫入的位址與檔案、會不會清除設定，按確定開始；完成後裝置自動重新開機

### 開源專案需要 ESP-IDF

編譯需要 ESP-IDF v5.5.3。可在「開源專案」分頁按「安裝 v5.5.3」（需要 `xcode-select --install` 的 git，cmake/ninja 會用 Homebrew 自動安裝），
或選擇已安裝的資料夾（例如 `~/esp/esp-idf-v5.5.3`）。

寫入方式：

- **分段寫入（預設）**：等同 `idf.py flash`，依 `build/flasher_args.json` 寫入 bootloader、分區表、程式與資料分區（例如客語朗讀練習的錄音），**保留 Wi-Fi／配網設定**
- **完整寫入 0x0**：和官方社區安裝一樣寫入整個映像，會清除設定

## 安全設計

- 永遠不執行 erase-flash（不整片清除）
- 寫入前檢查：韌體是 ESP32-C3 用的、不超出 8 MB、位址對齊、完整映像只能寫到 0x0、只有 app 不能寫到 0x0
- 社區韌體下載後核對官方提供的 SHA-256，不符就不寫入
- esptool 以 `--chip esp32c3` 執行，接錯裝置會直接停止

## 連不上時

- 關閉其他正在使用裝置的程式（官方安裝網頁、序列埠監看工具、Arduino 等）
- 拔插 USB-C、換一條可傳資料的線或 USB 孔，避免集線器
- 傳輸錯誤時把「速度」改成 115200

## 檔案

| 檔案 | 說明 |
| --- | --- |
| `app.py` | 圖形介面（Tkinter） |
| `flasher_core.py` | 核心：韌體檢查、燒錄計畫、esptool 指令、社區 API、GitHub、ESP-IDF |
| `tests/test_core.py` | 單元測試：`python3 tests/test_core.py` |
| `run.command` / `build_app.sh` | 啟動／打包 |

設定與上傳紀錄存在 `~/Library/Application Support/PassportFlasher/`，下載的專案與韌體在 `~/PassportFlasher/`。

## 之後

- Windows 版（介面與燒錄已用跨平台寫法，等 macOS 版穩定後再加入 Windows 的 ESP-IDF 編譯與 .exe 打包）
