@echo off
rem GMP Desk: one-click publish. Double-click this file.
rem It refreshes the data, then puts everything on the live website.
cd /d "%~dp0"

echo [1/4] Refreshing the data...
python build_data.py
if errorlevel 1 goto fail

echo.
echo [2/4] Collecting your changes...
git add -A
git status --short
git diff --cached --quiet
if not errorlevel 1 (
  echo Nothing new to publish.
  goto end
)
git commit -m "Site update %date% %time%"
if errorlevel 1 goto fail

echo.
echo [3/4] Syncing with GitHub...
git pull --rebase --autostash -X theirs origin main
if errorlevel 1 goto fail

echo.
echo [4/4] Publishing...
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
