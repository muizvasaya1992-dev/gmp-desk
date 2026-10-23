@echo off
rem GMP Desk: one-click publish. Double-click this file.
rem It refreshes the data, syncs with GitHub, rebuilds once more on the synced files, then publishes.
cd /d "%~dp0"

echo [1/5] Refreshing the data...
python build_data.py
if errorlevel 1 goto fail

echo.
echo [2/5] Collecting your changes...
git add -A
git status --short
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Site update %date% %time%"
  if errorlevel 1 goto fail
) else (
  echo No new changes on this PC.
)

echo.
echo [3/5] Syncing with GitHub...
git pull --rebase --autostash -X theirs origin main
if errorlevel 1 goto fail

echo.
echo [4/5] Rebuilding on the synced files, so nothing gets mixed up...
python build_data.py
if errorlevel 1 goto fail
git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Rebuild after sync %date% %time%"
  if errorlevel 1 goto fail
)

echo.
echo [5/5] Publishing...
git push
if errorlevel 1 goto fail

echo.
echo Done! Your live site updates in 1 to 2 minutes.
goto end

:fail
echo.
echo Something went wrong. Copy the messages above and send them to Claude.
pause
exit /b 1

:end
pause
