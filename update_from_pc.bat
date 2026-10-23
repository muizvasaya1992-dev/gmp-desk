@echo off
rem GMP Desk: refresh from THIS PC (BSE only answers home connections), then publish.
rem Double-click it, or schedule it in Windows Task Scheduler. It also publishes any edits you made to data\gmp.csv.
cd /d "%~dp0"

echo [1/4] Getting the latest from GitHub...
git pull --rebase --autostash origin main
if errorlevel 1 goto fail

echo [2/4] Building (NSE and BSE, from this PC)...
python build_data.py
if errorlevel 1 goto fail

git diff --quiet -- build_data.py index.html .github update_from_pc.bat
if errorlevel 1 echo NOTE: you have unpublished edits to code files. They are NOT published by this script. Commit them yourself when ready.

echo [3/4] Looking for changes...
git add -A -- ipos.json data
git diff --staged --quiet
if not errorlevel 1 (
  echo Nothing changed. Nothing to publish.
  goto end
)
git commit -m "Update IPO data from PC"
if errorlevel 1 goto fail

echo [4/4] Publishing...
git pull --rebase --autostash -X theirs origin main
if errorlevel 1 goto fail
git push
if errorlevel 1 goto fail
echo Done. The site updates in a minute or two.
goto end

:fail
echo.
echo Something went wrong. Read the messages above, and send them to Claude if you are not sure.
pause
exit /b 1

:end
pause
