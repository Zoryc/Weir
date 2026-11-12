#!/usr/bin/env bash
# Build Weir for Linux: a PyInstaller one-folder build packed into an AppImage.
#   dist/Weir/                          the app folder
#   dist/Weir-<version>-<arch>.AppImage single file: download, make executable, double-click
# Usage: ./build_linux.sh [--with-bin]
#   --with-bin  also copy the local bin/ folder (yt-dlp, ffmpeg, deno) into the build
# Build on the oldest distribution you want to support (glibc compatibility).
set -euo pipefail
cd "$(dirname "$0")"

APPNAME="Weir"
PY="${PYTHON:-python3}"

case "$(uname -m)" in
    x86_64 | amd64) ARCH=x86_64 ;;
    aarch64 | arm64) ARCH=aarch64 ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment..."
    "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

echo "Installing build requirements..."
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r requirements.txt "pyinstaller>=6.6"
VERSION="$("$VPY" -c 'import core; print(core.APP_VERSION)')"

echo "Drawing the logo..."
mkdir -p build
"$VPY" -m ui.icon build/weir.png

echo "Running PyInstaller..."
"$VPY" -m PyInstaller --noconfirm --clean --windowed \
    --name "$APPNAME" \
    --add-data "presets.json:." \
    main.py

DIST="dist/$APPNAME"
cp presets.json "$DIST/presets.json"
mkdir -p "$DIST/bin"
if [ "${1:-}" = "--with-bin" ]; then
    if [ -d bin ]; then
        echo "Copying bin/ tools into the build..."
        cp -a bin/. "$DIST/bin/"
    else
        echo "No bin/ folder found - skipping --with-bin."
    fi
fi

echo "Assembling the AppImage..."
# Inside the AppImage the app folder is read-only, so Weir keeps the tools it
# downloads in ~/.local/share/Weir/bin instead.
APPDIR="build/$APPNAME.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$DIST" "$APPDIR/usr/lib/weir"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/weir/Weir" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/weir.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Weir
GenericName=Video Downloader
Comment=Download videos and music from a link
Exec=Weir %U
Icon=weir
Terminal=false
Categories=Network;AudioVideo;
Keywords=youtube;video;music;download;yt-dlp;
StartupWMClass=Weir
DESKTOP
cp "$APPDIR/weir.desktop" "$APPDIR/usr/share/applications/weir.desktop"
cp build/weir.png "$APPDIR/weir.png"
cp build/weir.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/weir.png"
ln -sfn weir.png "$APPDIR/.DirIcon"

TOOL="build/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
    echo "Downloading appimagetool..."
    curl -fL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$TOOL"
fi
OUT="dist/$APPNAME-$VERSION-$ARCH.AppImage"
# --appimage-extract-and-run: works on build machines without FUSE.
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"

echo
echo "Done: $OUT"
