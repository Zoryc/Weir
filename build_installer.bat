@echo off
rem Build the Windows installer: dist\Weir-Setup-<version>.exe
rem Builds the app with build_windows.bat first, then compiles installer\weir.iss
rem with Inno Setup 6 (https://jrsoftware.org/isinfo.php, free).
setlocal EnableExtensions
cd /d "%~dp0"

set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if not defined ISCC if exist "%%~P" set "ISCC=%%~P"
)
if not defined ISCC (
    for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%P"
)
if not defined ISCC (
    echo Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php
    echo or with:  winget install --id JRSoftware.InnoSetup -e
    exit /b 1
)

call "%~dp0build_windows.bat" || goto :error

set "VPY=.venv\Scripts\python.exe"
rem Read the version through a file: simpler than cmd's for /f quoting rules.
"%VPY%" -c "import core; print(core.APP_VERSION)" > build\version.txt || goto :error
set /p APPVERSION=<build\version.txt
if not defined APPVERSION goto :error

echo Compiling the installer with Inno Setup...
"%ISCC%" /Qp /DAppVersion=%APPVERSION% installer\weir.iss || goto :error

echo.
echo Done: dist\Weir-Setup-%APPVERSION%.exe
exit /b 0

:error
echo.
echo Installer build FAILED (error %errorlevel%).
exit /b 1
