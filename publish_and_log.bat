@echo off
rem Runs update_from_pc.bat and saves everything it prints to last_run.txt,
rem so the messages can be read even if the window closes. Double-click this file.
cd /d "%~dp0"
echo Running the update and saving the messages to last_run.txt ...
call update_from_pc.bat < nul > last_run.txt 2>&1
echo.
echo ---- the last 25 lines ----
powershell -NoProfile -Command "Get-Content last_run.txt -Tail 25"
echo.
echo The whole log is in last_run.txt in this folder. Send that file to Claude if anything looks wrong.
pause
