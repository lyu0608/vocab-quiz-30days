@echo off
chcp 65001 >nul
setlocal

REM ========================================================
REM 單字特訓 App 一鍵上傳 (v6.2.3 by Claude 改寫)
REM   1. 本資料夾 commit 並推送到 lyu0608/vocab-quiz-30days (程式碼管理)
REM   2. 只把 4 個網頁檔同步到 lyu0608.github.io 的 exams/eng (網頁發布)
REM
REM 與舊版差異 (v6.2.3 by Claude)：
REM   - 不再 git init / 重設 origin / push -f，避免覆蓋 GitHub 上的版本歷史
REM   - 依 .gitignore 排除 PDF、APK、zip，不會把第三方教材推上公開 repo
REM   - 網站只更新 vocab-quiz-30days.html、vocab2000.js、cap_exam_bank.js (v6.2.8 起)、sw.js、manifest.json，
REM     同資料夾的 2000words.html、vocab.html、圖示等其他檔案一律不動
REM
REM 用法：直接雙擊執行；加參數 test (uploadgithub.bat test) 為演練模式，
REM       不會 commit 本資料夾，推送只做 --dry-run
REM ========================================================

set "MODE=%~1"
set "DRYRUN="
if /i "%MODE%"=="test" set "DRYRUN=--dry-run"
REM v6.2.3 by Claude：本機 Git Credential Manager 存有 lennysimula 與 lyu0608 兩組帳號，
REM 網址不指定帳號時 git 不知道用哪一組 (非互動視窗會直接失敗)；在此指定推送帳號，要換帳號改這一行
set "GH_USER=lennysimula"
set "CODE_REPO=https://%GH_USER%@github.com/lyu0608/vocab-quiz-30days.git"
set "SITE_REPO=https://%GH_USER%@github.com/lyu0608/lyu0608.github.io.git"
set "TEMP_DIR=%TEMP%\vocab_deploy_temp"

REM v6.2.3 by Claude：這台電腦的 git 不在 PATH，找不到時改用 GitHub Desktop 內附的 git
set "GIT="
where git >nul 2>nul
if not errorlevel 1 set "GIT=git"
if not defined GIT for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do if exist "%%D\resources\app\git\cmd\git.exe" set "GIT=%%D\resources\app\git\cmd\git.exe"
if not defined GIT (
    echo [錯誤] 找不到 git，請安裝 Git 或 GitHub Desktop
    goto :fail
)

cd /d "%~dp0"
if /i "%MODE%"=="test" echo *** 演練模式：不 commit、推送只做 dry-run ***

REM ---------- 1. 程式碼 repo ----------
echo ========================================================
echo [1/2] 推送程式碼到 lyu0608/vocab-quiz-30days
echo ========================================================
REM v6.2.5 by Claude：演練模式只列出變更，不執行 git add / reset (原本 reset 會取消使用者自己已暫存的檔案)
if /i "%MODE%"=="test" (
    echo [演練] 目前的變更：
    "%GIT%" -c core.quotepath=off status --short
    goto :push_code
)
"%GIT%" add -A
REM v6.2.3 by Claude：沒有變更時跳過 commit (git diff --cached --quiet 回傳 0 代表沒有已暫存的變更)
"%GIT%" diff --cached --quiet
if not errorlevel 1 (
    echo 沒有新的變更，略過 commit
) else (
    set "COMMIT_MSG="
    set /p "COMMIT_MSG=請輸入本次更新說明 (直接 Enter 使用預設): "
    call :commit_project
    if errorlevel 1 goto :fail
)
REM v6.2.3 by Claude：一般 push (非 -f)；若 GitHub 上有較新的版本會失敗並停止，不會覆蓋
:push_code
REM v6.2.3 by Claude：直接推到 CODE_REPO (含指定帳號)，不依賴 origin 的設定
"%GIT%" push %DRYRUN% "%CODE_REPO%" main
if errorlevel 1 (
    echo [錯誤] 推送 vocab-quiz-30days 失敗，請檢查網路或 GitHub 權限
    goto :fail
)

REM ---------- 2. 網站發布 ----------
echo ========================================================
echo [2/2] 同步網頁檔到 lyu0608.github.io/exams/eng
echo ========================================================
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
REM v6.2.3 by Claude：只下載 exams/eng 資料夾 (sparse checkout)，速度快且不碰網站其他內容
"%GIT%" clone -q --depth 1 --filter=blob:none --sparse "%SITE_REPO%" "%TEMP_DIR%"
if errorlevel 1 (
    echo [錯誤] 下載網站 repo 失敗
    goto :fail
)
"%GIT%" -C "%TEMP_DIR%" sparse-checkout set exams/eng
if not exist "%TEMP_DIR%\exams\eng" mkdir "%TEMP_DIR%\exams\eng"

REM v6.2.3 by Claude：只複製這 4 個檔案；舊版的 *.png 一併複製已移除 (網站圖示與 2000words.html 共用)
REM v6.2.8 by Claude：加入官方真題題庫 cap_exam_bank.js；v6.2.9 by Claude：加入官方聽力題庫 cap_listening_bank.js
for %%F in (vocab-quiz-30days.html vocab2000.js cap_exam_bank.js cap_listening_bank.js sw.js manifest.json) do (
    copy /y "%~dp0%%F" "%TEMP_DIR%\exams\eng\" >nul
    if errorlevel 1 (
        echo [錯誤] 複製 %%F 失敗
        goto :fail
    )
)

REM v6.2.9 by Claude：同步官方聽力錄音資料夾 listen\ (只新增/更新，不刪除網站上的其他檔案)
if exist "%~dp0listen" (
    if not exist "%TEMP_DIR%\exams\eng\listen" mkdir "%TEMP_DIR%\exams\eng\listen"
    copy /y "%~dp0listen\*.mp3" "%TEMP_DIR%\exams\eng\listen\" >nul
    if errorlevel 1 (
        echo [錯誤] 複製聽力錄音失敗
        goto :fail
    )
)

"%GIT%" -C "%TEMP_DIR%" add exams/eng
"%GIT%" -C "%TEMP_DIR%" diff --cached --quiet
if not errorlevel 1 (
    echo 網站檔案已是最新，不需更新
    goto :cleanup
)
"%GIT%" -C "%TEMP_DIR%" -c core.quotepath=off diff --cached --stat
"%GIT%" -C "%TEMP_DIR%" commit -q -m "feat(exams/eng): 同步更新單字特訓 App 網頁"
REM v6.2.5 by Claude：檢查 commit 是否成功；原本失敗時 push 會顯示 up-to-date，腳本卻回報完成
if errorlevel 1 (
    echo [錯誤] 網站 commit 失敗
    goto :fail
)
"%GIT%" -C "%TEMP_DIR%" push %DRYRUN% origin main
if errorlevel 1 (
    echo [錯誤] 推送網站失敗，請檢查網路或 GitHub 權限
    goto :fail
)

:cleanup
cd /d "%~dp0"
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
echo ========================================================
if /i "%MODE%"=="test" (
    echo 演練完成，未上傳任何內容
) else (
    echo 完成！網頁：https://lyu0608.github.io/exams/eng/vocab-quiz-30days.html
    echo 約 1 分鐘後生效
)
echo ========================================================
if /i not "%MODE%"=="test" pause
exit /b 0

REM v6.2.3 by Claude：commit 放在子程序，避免在 if ( ) 區塊內讀不到 set /p 剛輸入的值
:commit_project
if not defined COMMIT_MSG set "COMMIT_MSG=update: 更新單字特訓 App"
REM v6.2.5 by Claude：移除說明中的雙引號，避免破壞下一行 commit 指令的引號
set "COMMIT_MSG=%COMMIT_MSG:"=%"
"%GIT%" commit -q -m "%COMMIT_MSG%"
exit /b %errorlevel%

:fail
cd /d "%~dp0"
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"
echo 上傳中止
if /i not "%MODE%"=="test" pause
exit /b 1
