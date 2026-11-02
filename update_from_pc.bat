@echo off
rem IPO Shop: one-click publish. Double-click this file.
rem It refreshes the data, syncs with GitHub, rebuilds once more on the synced files, then publishes.
cd /d "%~dp0"

echo [1/5] Refreshing the data...
python build_data.py
if errorlevel 1 goto fail

echo.
echo [2/5] Collecting your changes...
git add -A
git status --short
git diff --cached --name-only | findstr /r /c:"private\.json$" >nul
if not errorlevel 1 (
  echo.
  echo STOP: data\private.json was about to be published, and it must stay private.
  git reset -q -- data/private.json
  echo It has been taken out of this publish. Run tidy_folder.bat once, then run this again.
  goto fail
)
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Site update %date% %time%"
  if errorlevel 1 goto fail
) else (
  echo No new changes on this PC.
)

echo.
echo [3/5] Syncing with GitHub...
git pull --rebase --autostash -X theirs -X no-renames origin main
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
rem The automatic timer on GitHub may push while this runs, so try again after pulling its changes.
for /l %%A in (1,1,3) do (
  git push && goto pushed
  echo   GitHub moved ahead, pulling its changes and trying again (attempt %%A of 3)...
  git pull --rebase --autostash -X theirs -X no-renames origin main
  if errorlevel 1 goto fail
)
echo.
echo Could not publish after three tries. Run this file again in a minute.
goto fail
:pushed

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
