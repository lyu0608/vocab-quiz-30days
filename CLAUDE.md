# CLAUDE.md

英語單字特訓 App (國中英語 30 天單字與會考特訓，PWA + Supabase)。
**完整架構、資料表、建置與部署流程請先讀 [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)。** 本檔只列每次開發都必須遵守的規則。

## 必守規則

1. **每一處程式修改都要加註解**：繁體中文，開頭標註版本與作者，例如
   `// v6.3.1 by Claude：修正…` (HTML 用 `<!-- v6.3.1 by Claude：… -->`)。
2. **版本紀錄要延續**：在 `vocab-quiz-30days.html` 的 `#changelogModal` 最上方新增一筆
   `vX.Y.Z by Claude - 標題`，保留所有舊紀錄。目前最新版本：**v6.3.0**。
3. **題庫檔不可手改**：`vocab2000.js`、`cap_exam_bank.js`、`cap_listening_bank.js` 一律用 `tools/` 的程式重新產生。
4. **真題與聽力只能用國中教育會考官方原題與官方答案**，不可自編題目或標示假的出處；看圖題不收錄。
5. 插入 `innerHTML` 的文字一律經過 `escapeHtml()`。
6. 資料庫欄位是 `history` / `mistakes` (不是 `historyByQuiz` / `mistakesByQuiz`)；雲端讀取成功 (`cloudReadOk`) 前不可上傳。
7. 回覆使用者用繁體中文。

## 修改前後

- 測試：先 `python -m http.server 8765 --bind 127.0.0.1`，再 `node tools/selftest.js`，35 項須全部通過。
- 新增會被網頁載入的檔案時，同步更新 `sw.js` 的 `CORE`、主程式的 `VERSION_CHECK_FILES`、`uploadgithub.bat` 的複製清單。
- `sw.js` 與網站 `exams/eng/` 的 `2000words.html`、`vocab.html` 共用，不可移除它們。

## 提交與部署

- 資料夾內有與本專案無關的檔案 (`AI_Passport/`、`客語朗讀115/`、`tools/hakka_*.py`、PDF/APK/zip)。
  **只 `git add` 本次修改的檔案，不要 `git add -A`**。
- 不使用 force push。部署到正式網站 (GitHub Pages) 屬對外發布，**先取得使用者同意**。
- 部署步驟見 PROJECT_HANDOFF.md §9。
