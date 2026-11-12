@echo off
rem Build Ytdlp Downloader for Windows (PyInstaller one-folder build).
rem Usage: build_windows.bat [--with-bin]
rem   --with-bin  also copy the local bin\ folder (yt-dlp, ffmpeg, deno) into the build
setlocal EnableExtensions
cd /d "%~dp0"

set "APPNAME=YtdlpDownloader"
set "PY=python"
where py >nul 2>&1
if %errorlevel%==0 set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :error
)
set "VPY=.venv\Scripts\python.exe"

echo Installing build requirements...
"%VPY%" -m pip install --upgrade pip || goto :error
"%VPY%" -m pip install -r requirements.txt "pyinstaller>=6.6" pillow || goto :error

echo Drawing the app icon...
if not exist build mkdir build
"%VPY%" -m ui.icon build\icon.png || goto :error

echo Running PyInstaller...
"%VPY%" -m PyInstaller --noconfirm --clean --windowed ^
    --name "%APPNAME%" ^
    --icon build\icon.png ^
    --add-data "presets.json;." ^
    --collect-submodules ui.variants ^
    main.py || goto :error

rem An editable copy of the presets next to the .exe, and an empty tools folder.
copy /Y presets.json "dist\%APPNAME%\presets.json" >nul || goto :error
if not exist "dist\%APPNAME%\bin" mkdir "dist\%APPNAME%\bin"

if /I "%~1"=="--with-bin" (
    if exist bin (
        echo Copying bin\ tools into the build...
        xcopy /E /I /Y bin "dist\%APPNAME%\bin" >nul || goto :error
    ) else (
        echo No bin\ folder found - skipping --with-bin.
    )
)

echo.
echo Done: dist\%APPNAME%\%APPNAME%.exe
echo Zip the whole dist\%APPNAME% folder to share it.
exit /b 0

:error
echo.
echo Build FAILED (error %errorlevel%).
exit /b 1
