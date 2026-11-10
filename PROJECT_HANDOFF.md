# 英語單字特訓 App：專案交接文件

> 給接手開發的工程師 / AI。寫於 2026-11-10，對應版本 **v6.3.0 by Claude**。
> 舊的 `software_requirements_specification.md` 部分內容已過時 (字數、聽力來源)，以本文件為準。

---

## 1. 專案一覽

| 項目 | 內容 |
|---|---|
| 用途 | 國中英語 30 天單字與會考特訓，五種題型、錯題本、雲端同步、管理者後台 |
| 正式網址 | https://lyu0608.github.io/exams/eng/vocab-quiz-30days.html |
| 程式碼 repo | https://github.com/lyu0608/vocab-quiz-30days (branch `main`，本資料夾即其 checkout) |
| 網站 repo | https://github.com/lyu0608/lyu0608.github.io 的 `exams/eng/` 資料夾 (GitHub Pages) |
| 前端 | 單一 HTML 檔 (HTML + CSS + 原生 JS，無框架、無建置步驟)，PWA |
| 後端 | Supabase (Auth + Postgres + RLS)，前端直接以 supabase-js v2 (CDN) 存取 |
| 工具 | Python 3 (產生題庫)、Node + puppeteer-core (自動測試) |

---

## 2. 檔案結構

### 2.1 會發布到網站的檔案

| 檔案 | 說明 |
|---|---|
| `vocab-quiz-30days.html` | **主程式** (約 2,300 行)。所有畫面、樣式、邏輯都在這裡 |
| `vocab2000.js` | 單字題庫 `window.EXTERNAL_VOCAB` (2,486 字)。**由 `tools/build.py` 產生，勿手改** |
| `cap_exam_bank.js` | 會考閱讀官方原題 `window.CAP_OFFICIAL_BANK` (108–113 年，102 題)。**由 `tools/build_cap_bank.py` 產生** |
| `cap_listening_bank.js` | 會考聽力官方原題 `window.CAP_LISTENING_OFFICIAL` (104–108、110–115 年，194 題)。**由 `tools/build_cap_listening.py` 產生** |
| `listen/*.mp3` | 聽力官方錄音，每題一檔，命名 `{年度}-{題號}.mp3`，48kbps 單聲道，共約 62MB |
| `sw.js` | Service Worker (離線快取) |
| `manifest.json`、`icon-*.png`、`apple-touch-icon.png` | PWA 設定與圖示 |

> 網站 `exams/eng/` 資料夾裡還有**別的頁面** (`2000words.html`、`vocab.html`、`manifest.webmanifest`、`icon.svg`)，與本 App 共用 `sw.js` 與圖示。**不可刪除**，`sw.js` 的 CORE 清單也要保留它們。

### 2.2 只在程式碼 repo 的檔案

| 檔案 | 說明 |
|---|---|
| `tools/build.py`、`tools/parse_moe.py` | 從 3 份 PDF 字表重建 `vocab2000.js` (教育部 1932 字 + 基礎 1200 字補 383 + 均一講義補 171) |
| `tools/junyi_zh.json` | 只出現在均一講義的 171 字的中文解釋 (人工補寫) |
| `tools/build_report.txt` | 上次重建題庫的報告 |
| `tools/build_cap_bank.py` | 由會考英語試題本 + 參考答案產生閱讀真題 |
| `tools/build_cap_listening.py` | 由會考聽力壓縮檔 (題本 PDF + 每題 mp3) + 參考答案產生聽力題與壓縮音檔 |
| `tools/selftest.js` | 自動測試 (35 項，puppeteer + 無頭 Edge) |
| `tools/supabase_policies_v6.2.1.sql`、`v6.2.6.sql` | Supabase RLS 政策 (已套用在正式資料庫) |
| `uploadgithub.bat` | 一鍵上傳：commit 推送程式碼 repo，並把網站檔同步到 `exams/eng` |
| `vocab2000.json` | **未使用**的舊資料 (早期 47 字)，可刪除 |
| `software_requirements_specification.md` | 早期需求規格，部分過時 |

### 2.3 資料夾裡與本專案無關的東西 (不在 git，勿提交)

`AI_Passport/`、`客語朗讀115/`、`tools/hakka_*.py`、`*.pdf` (原始字表與其他文件)、`*.apk`、`*.zip`、`單字特訓2000 - Google Play package/`。`.gitignore` 已排除 PDF/APK/zip；其餘幾項提交時請**只 add 需要的檔案**，不要 `git add -A`。

---

## 3. 主程式架構 (`vocab-quiz-30days.html`)

### 3.1 載入順序
`<head>` 依序載入 supabase-js (CDN)、`vocab2000.js`、`cap_exam_bank.js`、`cap_listening_bank.js`，之後才是主程式 `<script>`。

### 3.2 五種題型 (`QUIZ_TYPES`)

| key | 名稱 | 題目來源 | 每天題數 |
|---|---|---|---|
| `wordDef` | 🔤 單字 | `FULL_VOCAB` 依固定種子洗牌後分 30 天 | 約 83 |
| `hard` | 🔥 單字(難) | 同上，干擾選項挑拼字相近的字 (`pickHardDistractors`) | 約 83 |
| `cloze` | 📝 填空 | 同上，用例句挖空 (`createClozeSentence`) | 約 83 |
| `exam` | 🏛️ 真題 | `CAP_OFFICIAL_BANK`，依年度新→舊平均分 30 天 | 3–4 |
| `listen` | 🎧 聽力 | `CAP_LISTENING_OFFICIAL`，依年度新→舊平均分 30 天 | 6–7 |

- 單字分天：`seededShuffle(…, DAY_PLAN_SEED = 20261030)` (mulberry32)，所以每位學員、每次載入的分天結果都相同。**改動單字庫會讓各天內容重排。**
- 真題/聽力分天：`splitIntoDays(items)`；`getDayPlan(d)` 依題型回傳當天題目。
- 干擾選項：`makeOptionGuard` + `zhGlosses` 避免出現「中文意思重疊的第二個正解」(題庫中有 263 組同義字)。
- 真題與聽力保留官方 A–D / A–C 選項順序，答案取自官方參考答案；看圖題不收錄。
- 聽力播放官方錄音 (`playCurrentListeningAudio` → `<audio id="listenAudio">`)，不是 TTS。單字發音用瀏覽器 Web Speech (`speakPureUS`)。

### 3.3 重要函式

| 函式 | 作用 |
|---|---|
| `initDayPlans` / `initExamPlans` | 建立 30 天題目分配 |
| `changeDay(d)` / `startRandomQuiz(n)` / `reviewMistakes()` | 進入某天 / 亂數刷題 / 錯題特訓 |
| `renderQuestion` / `checkAnswer` / `nextQuestion` | 出題、判分、下一題 |
| `itemId(item)` | 題目唯一鍵 (`id` → `en` → `scriptEn`)，錯題本以此去重 |
| `getBaseStartDate` / `getExpectedDayForDate` | 30 天計畫基準日與「今天是第幾天」 |
| `syncFromCloud` / `syncToCloud` / `mergeCloudIntoStore` | 雲端同步 (見 §4) |
| `fetchAdminAllProgress` / `adminUpdateUserStartDate` | 管理者後台 |
| `ensureLatestVersion` | 每回合開始時以 HEAD 比對 `VERSION_CHECK_FILES` 的 ETag/Last-Modified，有新版就自動重新整理 |
| `escapeHtml` | 所有插入 innerHTML 的文字都必須經過它 (防 XSS) |

### 3.4 本機資料 (`userStore`)
```js
{
  historyByQuiz:  { wordDef:{}, hard:{}, cloze:{}, exam:{}, listen:{} }, // 每天的作答紀錄
  mistakesByQuiz: { wordDef:[], hard:[], cloze:[], exam:[], listen:[] }, // 錯題
  stats: { totalQ, totalCorrect, wordDef:{total,correct}, ... },
  startDate: "YYYY-MM-DD" | null
}
```
存在 `localStorage["local_vocab_progress_<userId>"]`；已登入過的帳號清單在 `known_vocab_users_list` (用於快速切換帳號，管理者帳號的 session 不保存)。

---

## 4. Supabase

- 專案 URL 與 publishable key 寫在主程式 (`SUPABASE_URL`、`SUPABASE_ANON_KEY`)，屬公開金鑰，安全性完全靠 RLS。
- 管理者：`ADMIN_EMAIL = "lyu0608@gmail.com"`。

### 資料表
**`user_progress`** (一位學員一列)：`user_id` (PK)、`email`、`history` jsonb、`mistakes` jsonb、`stats` jsonb、`startdate`、`updated_at`
- 程式內部欄位名是 `historyByQuiz`/`mistakesByQuiz`，存進資料庫時寫入 `history`/`mistakes` 欄位；`readHistoryColumn`/`readMistakesColumn` 同時相容舊格式。**資料庫沒有 historyByQuiz 欄位** (v6.2.7 修過這個 bug)。
- 管理者改的啟動日期存在 `stats.startDate`。

**`user_daily_activity`**：每位學員每天一列 (UNIQUE `user_id + activity_date`)，記錄上線秒數與作答題數，後台顯示最近 14 天。

### RLS 重點 (見 `tools/supabase_policies_*.sql`)
- 學員只能讀寫自己的列；寫入的 `email` 必須等於登入者 email (`WITH CHECK (email = auth.jwt()->>'email')`)。
- 管理者可讀所有 progress / daily activity，並可更新所有 progress。

### 同步規則
- 登入後先 `syncFromCloud`，**讀取成功 (`cloudReadOk`) 之前不允許上傳**，避免空白本機資料覆蓋雲端。
- 雲端與本機以 `mergeCloudIntoStore` 合併；之後每次作答以 debounce 呼叫 `syncToCloud`。

---

## 5. Service Worker (`sw.js`)

- 快取名稱 `wb2000-v4`。只有要強制清除舊快取時才改版本號。
- 同網域檔案：**網路優先**，離線才用快取 (所以一般更新不用改版本號)。
- supabase-js CDN：快取優先。
- `listen/` 錄音：不經 SW、不快取 (瀏覽器以 Range 分段讀取，無法快取；聽力需要網路)。
- activate 時只刪除 `wb2000-` 開頭的舊快取 (同網域還有其他網頁)。

---

## 6. 開發規範 (使用者要求，請遵守)

1. **每一處程式修改都要加註解**，繁體中文，標註版本，例如 `// v6.3.1 by Claude：…`。HTML 用 `<!-- … -->`。若換成其他 AI，請把 `by Claude` 換成自己的名稱，維持同樣格式。
2. **版本紀錄要延續**：在 `#changelogModal` 最上方新增一筆 `vX.Y.Z by <名稱> - 標題`，保留所有舊紀錄。目前最新是 v6.3.0。
3. 題庫檔 (`vocab2000.js`、`cap_exam_bank.js`、`cap_listening_bank.js`) 一律用 `tools/` 的程式重新產生，不要手改。
4. 真題/聽力只能用**官方原題與官方答案**，不可自編題目或標示假的出處 (v6.2.8 已移除先前編造的「真題」)。
5. 部署前先跑自動測試，並檢查 `git status` 沒有夾帶無關檔案。

---

## 7. 測試

```bash
python -m http.server 8765 --bind 127.0.0.1
```
另開視窗：
```bash
node tools/selftest.js
```
- 需要 `puppeteer-core` (`npm i puppeteer-core`，或設定 `NODE_PATH` 指向已安裝的 node_modules) 與 Edge/Chrome；瀏覽器路徑寫在 `selftest.js` 開頭。
- 35 項測試涵蓋：五種題型出題與判分、7,458 題單字題無第二正解、真題/聽力每題的出處標籤、官方選項順序與答案、錄音可下載、30 天分配、錯題本、雲端同步各種失敗情境、帳號切換、Service Worker 離線、自動更新。
- 測試用的瀏覽器設定檔建在系統暫存資料夾，不要建在專案內。

---

## 8. 重建題庫

需要 Python 3 + `pdfplumber`、`pypdf`、`imageio-ffmpeg` (聽力轉檔)。

- **單字**：`python tools/build.py` (讀資料夾內 3 份字表 PDF，輸出 `vocab2000.js` 與 `tools/build_report.txt`)
- **閱讀真題**：下載官網 `cap.rcpet.edu.tw/exam/{年}/{年}P_English.pdf` 與 `{年}P_Answer.pdf`，用 `tools/build_cap_bank.py` 產生
- **聽力**：
  ```bash
  python tools/build_cap_listening.py <答案txt資料夾> cap_listening_bank.js listen 115=<解壓資料夾> 114=<...> ... 111c=<...>
  ```
  - 參數順序決定題目順序 (新→舊)；`111c` (111 參考試題本) 要放最後，它的題目全部沿用 104–107 年，會被自動判為重複。
  - 答案 txt 由 `{年}P_Answer.pdf` 轉文字而來，取「英語聽力」欄。
  - 聽力檔來源：歷屆試題頁 https://cap.rcpet.edu.tw/examination.html 各年度的「英語（聽力）」連結 (多為 Google 雲端硬碟；104/105 壓縮檔內還有 rar，需 7-Zip)。
  - 未收錄：109 (官網無聽力檔)、102/103 (試辦，官方答案無聽力欄)、所有看圖題。

---

## 9. 部署

**方式一 (使用者自己)**：雙擊 `uploadgithub.bat`。加參數 `test` 為演練模式。
- 會 `git add -A` 本資料夾 → 因為資料夾裡有無關檔案 (§2.3)，執行前請確認 `.gitignore` 或先清理。
- 只同步 `vocab-quiz-30days.html`、`vocab2000.js`、`cap_exam_bank.js`、`cap_listening_bank.js`、`sw.js`、`manifest.json`、`listen/*.mp3` 到網站，不動網站其他檔案。不使用 force push。

**方式二 (手動)**：
1. 程式碼 repo：只 add 本次修改的檔案 → commit → push `main`。
2. 網站：sparse clone `lyu0608.github.io` (只取 `exams/eng`) → 複製上列檔案 → commit → push。
3. 等 GitHub Pages 建置完成 (錄音多時可能要數分鐘以上)，確認網頁出現新版本號、`listen/` 新檔回應 200。

學員端不必清快取：開始做題時 `ensureLatestVersion` 會偵測新版並自動重新整理。

**git 與帳號 (這台電腦)**：git 不在 PATH，使用 GitHub Desktop 內附的 git。推送帳號 `lennysimula` (已是兩個 repo 的協作者)。

---

## 10. 目前狀態與待辦

**已完成 (v6.2.0 → v6.3.0)**：題庫重建為 2,486 字、Supabase RLS 修正、雲端同步欄位錯誤修正、XSS 防護、Service Worker 重寫、開始做題自動更新、真題改官方閱讀原題、聽力改官方原題與錄音並擴充到 194 題。

**待辦 / 可改進**：
- [ ] 114、115 年**閱讀**真題尚未加入 (目前到 113 年)。
- [ ] 帳號切換與管理者改日期需要用真實帳號實測 (自動測試以模擬方式涵蓋)。
- [ ] 單字庫若變動，30 天分配會整個重排 (設計取捨，可考慮改成穩定分配)。
- [ ] 聽力 3 題 (106-13、107-13、108-16) 語音辨識不清，僅確認未與官方答案矛盾。
- [ ] 主程式為單一 2,300 行檔案，若要大幅開發可考慮拆分 JS/CSS (需同步調整 `sw.js` CORE、`VERSION_CHECK_FILES`、`uploadgithub.bat`、自動測試)。
- [ ] `software_requirements_specification.md` 內容過時，可依本文件更新；`vocab2000.json` 可刪除。
