#!/usr/bin/env bash
# Build Ytdlp Downloader for Linux (PyInstaller one-folder build).
# Usage: ./build_linux.sh [--with-bin]
#   --with-bin  also copy the local bin/ folder (yt-dlp, ffmpeg, deno) into the build
set -euo pipefail
cd "$(dirname "$0")"

APPNAME="YtdlpDownloader"
PY="${PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment..."
    "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

echo "Installing build requirements..."
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r requirements.txt "pyinstaller>=6.6"

echo "Drawing the app icon..."
mkdir -p build
"$VPY" -m ui.icon build/icon.png

echo "Running PyInstaller..."
"$VPY" -m PyInstaller --noconfirm --clean --windowed \
    --name "$APPNAME" \
    --add-data "presets.json:." \
    main.py

DIST="dist/$APPNAME"
cp presets.json "$DIST/presets.json"
mkdir -p "$DIST/bin"
cp build/icon.png "$DIST/ytdlp-downloader.png"

if [ "${1:-}" = "--with-bin" ]; then
    if [ -d bin ]; then
        echo "Copying bin/ tools into the build..."
        cp -a bin/. "$DIST/bin/"
    else
        echo "No bin/ folder found - skipping --with-bin."
    fi
fi

# Helper that adds the app to the desktop's application menu (optional).
cat > "$DIST/install-menu-entry.sh" <<'EOF'
#!/usr/bin/env bash
# Adds Ytdlp Downloader to your application menu (for the current user only).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/ytdlp-downloader.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Ytdlp Downloader
Comment=Download videos and music with yt-dlp
Exec="$HERE/YtdlpDownloader" %U
Icon=$HERE/ytdlp-downloader.png
Terminal=false
Categories=Network;AudioVideo;
StartupWMClass=YtdlpDownloader
DESKTOP
update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "Added to the application menu: $APPS/ytdlp-downloader.desktop"
EOF
chmod +x "$DIST/install-menu-entry.sh" "$DIST/$APPNAME"

ARCH="$(uname -m)"
tar -C dist -czf "dist/$APPNAME-linux-$ARCH.tar.gz" "$APPNAME"

echo
echo "Done: $DIST/$APPNAME"
echo "Archive: dist/$APPNAME-linux-$ARCH.tar.gz"
