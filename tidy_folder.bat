@echo off
rem IPO Shop: one-time tidy-up. Double-click it once, from the main project folder.
rem It moves files to where the site expects them, removes stray copies, and makes sure
rem the private setting file can never be published.
cd /d "%~dp0"
echo Tidying the project folder...
echo.

if not exist fonts mkdir fonts
for %%F in (inter-latin.woff2 inter-rupee.woff2 OFL.txt) do (
  if exist "%%F" (
    move /Y "%%F" "fonts\%%F" >nul
    echo   moved %%F into the fonts folder
  )
)

if not exist brand mkdir brand
for %%F in (logo.svg favicon.svg apple-touch-icon.png icon-192.png icon-512.png channel-icon.png og-image.jpg) do (
  if exist "%%F" (
    move /Y "%%F" "brand\%%F" >nul
    echo   moved %%F into the brand folder
  )
)

for %%P in (alert.py alert.yml) do (
  if exist "%%P" (
    del "%%P"
    echo   removed %%P, which is not needed
  )
)
if exist ".github\workflows\alert.yml" (
  del ".github\workflows\alert.yml"
  echo   removed .github\workflows\alert.yml, which is not needed
)

if exist update.yml (
  if not exist ".github\workflows" mkdir ".github\workflows"
  move /Y update.yml ".github\workflows\update.yml" >nul
  echo   moved update.yml into .github\workflows
)

if exist config.json (
  move /Y config.json data\config.json >nul
  echo   moved config.json into the data folder
)
if exist private.json.example (
  del private.json.example
  echo   removed the stray copy of private.json.example
)

findstr /x /c:"data/private.json" .gitignore >nul 2>&1
if errorlevel 1 (
  (echo.& echo data/private.json)>>.gitignore
  echo   added data/private.json to .gitignore, so it can never be published
) else (
  echo   .gitignore already protects data/private.json
)
if exist gitignore (
  del gitignore
  echo   removed the stray file called gitignore
)

for %%P in (probe_bse_demand.py probe_bse_demand2.py probe_bse_demand3.py probe_site.py probe_gain.py data\gmp_peers.json) do (
  if exist "%%P" (
    del "%%P"
    echo   removed %%P
  )
)

echo.
echo ---- your .gitignore now reads:
type .gitignore
echo.
echo ---- the fonts folder holds:
dir /b fonts
echo.
echo ---- the brand folder holds:
dir /b brand
echo.
echo Done. Tell Claude what this window shows.
pause
