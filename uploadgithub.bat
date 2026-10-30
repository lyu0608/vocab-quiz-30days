@echo off
chcp 65001 >nul
echo ========================================================
echo 🚀 開始執行英語單字特訓 App 自動上傳與發布程序...
echo ========================================================

:: 1. 推送到獨立專案倉庫 vocab-quiz-30days
echo [1/3] 正在推送到 vocab-quiz-30days 儲存庫...
git init
git add .
set /p COMMIT_MSG="請輸入本次更新的簡短說明 (例如: 更新字庫與介面優化): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=fix: 更新單字特訓 App 內容
git commit -m "%COMMIT_MSG%"
git branch -M main
git remote remove origin 2>nul
git remote add origin https://github.com/lyu0608/vocab-quiz-30days.git
git push -u origin main -f

if errorlevel 1 (
    echo ❌ 錯誤：推送至 vocab-quiz-30days 失敗，請檢查網路或 Git 權限。
    pause
    exit /b
)

:: 2. 自動同步到主站 lyu0608.github.io/exams/eng
echo [2/3] 正在複製檔案並同步至主站專案...
set TEMP_DIR=%TEMP%\vocab_deploy_temp
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%"

git clone https://github.com/lyu0608/lyu0608.github.io.git "%TEMP_DIR%"
if errorlevel 1 (
    echo ❌ 錯誤：clone 主站倉庫失敗。
    pause
    exit /b
)

if not exist "%TEMP_DIR%\exams\eng" mkdir "%TEMP_DIR%\exams\eng"

:: 複製核心檔案到主站目標資料夾
copy /y vocab-quiz-30days.html "%TEMP_DIR%\exams\eng\"
copy /y vocab2000.js "%TEMP_DIR%\exams\eng\"
if exist sw.log copy /y sw.log "%TEMP_DIR%\exams\eng\" 2>nul
if exist sw.js copy /y sw.js "%TEMP_DIR%\exams\eng\" 2>nul
if exist manifest.json copy /y manifest.json "%TEMP_DIR%\exams\eng\" 2>nul
if exist *.png copy /y *.png "%TEMP_DIR%\exams\eng\" 2>nul

:: 3. 提交並推送到主站倉庫
echo [3/3] 正在發布主站網頁...
cd /d "%TEMP_DIR%"
git add exams/eng/
git commit -m "feat(exams): 同步更新英語單字特訓 App 網頁"
git push origin main

:: 清理暫存資料夾
cd /d %~dp0
rmdir /s /q "%TEMP_DIR%"

echo ========================================================
echo 🎉 太棒了！兩個倉庫皆已成功上傳並發布完成！
echo ========================================================
pause