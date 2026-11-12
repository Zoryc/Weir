# Ytdlp Downloader

A simple desktop window for [yt-dlp](https://github.com/yt-dlp/yt-dlp) on Windows 10/11 and
Linux (X11 and Wayland). Paste a link, pick a preset, click **Download**. No terminal needed.

- One screen: links box (paste or drag & drop), preset, save folder, a few checkboxes, queue.
- First-run wizard downloads the tools it needs (yt-dlp, ffmpeg + ffprobe, Deno) into `bin/`.
- Up to 3 downloads at once, with progress, speed, ETA and status for each.
- Plain-language error messages with a one-click fix (use browser cookies, pick another preset,
  download Deno or ffmpeg, update yt-dlp).
- Several designs to choose from (**Settings → Appearance**), each in light and dark mode,
  following the system setting; notifies you when the queue finishes.

## Run from source

You need Python 3.11 or newer.

**Windows** (PowerShell or cmd):

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw main.py
```

**Linux**:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

On X11, Qt 6 needs `libxcb-cursor0` (Debian/Ubuntu: `sudo apt install libxcb-cursor0`,
Fedora: `sudo dnf install xcb-util-cursor`). Wayland works out of the box; to force X11 run with
`QT_QPA_PLATFORM=xcb`.

You can also pass links on the command line: `python main.py https://…`.

## Build a standalone app

The build scripts create a virtual environment, install the requirements and PyInstaller, and
produce a one-folder build in `dist/YtdlpDownloader/`.

**Windows:**

```bat
build_windows.bat
```

**Linux:**

```bash
./build_linux.sh
```

Add `--with-bin` to copy your local `bin/` folder (already-downloaded yt-dlp, ffmpeg, Deno) into
the build, so it works offline from the first start. Without it, the app offers to download
the tools on first run.

The Linux build also writes `dist/YtdlpDownloader-linux-<arch>.tar.gz` and an
`install-menu-entry.sh` helper that adds the app to your application menu.

Build on the oldest Linux distribution you want to support (PyInstaller bundles that system's
glibc requirement).

## The tools (`bin/` folder)

| Tool | Why | Source |
|------|-----|--------|
| yt-dlp | the downloader | official standalone binary from `github.com/yt-dlp/yt-dlp` releases (`yt-dlp.exe` / `yt-dlp_linux`) |
| ffmpeg + ffprobe | merging, converting, embedding | GPL builds from `github.com/yt-dlp/FFmpeg-Builds` (win64 / linux64) |
| Deno | JavaScript runtime yt-dlp needs for full YouTube support | latest release from `github.com/denoland/deno` |

The app looks in its own `bin/` folder first, then on the system `PATH` (a system ffmpeg on
Linux is fine). If the app folder isn't writable, tools go to a per-user folder instead
(`%LOCALAPPDATA%\YtdlpDownloader\bin` or `~/.local/share/YtdlpDownloader/bin`).
Downloads are checked against the SHA-256 checksums published with each release.

**Settings → Update yt-dlp** runs `yt-dlp -U` and shows its output. **Settings → Tools**
shows what's installed and can re-download any tool. **Settings → About** shows all three versions.
The line at the bottom right of the window also tells you whether every tool is ready.

The app calls the yt-dlp binary as a separate program (it does not import the Python package),
always with `--ignore-config`, so a personal yt-dlp config file can't change its behaviour. To use
one anyway, add `--config-locations <path>` under Advanced options → Extra options.

## Presets

Built-in presets live in `presets.json` next to the program. Each has a `name` and `args`
(yt-dlp options written like on a command line):

```json
{ "name": "720p MP4 (smaller file)",
  "args": "-S \"res:720,vcodec:h264,acodec:aac\" --merge-output-format mp4 --remux-video mp4" }
```

**Advanced options → Save current settings as a new preset** stores the selected preset's options plus
your extra arguments, playlist/subtitle/thumbnail choices, file-name template and rate limit as
your own preset (marked ★). Your presets are kept in `presets.json` in the settings folder, so
updating the app never overwrites them.

## Settings

Saved as JSON in Qt's `AppConfigLocation`:

- Windows: `%LOCALAPPDATA%\YtdlpDownloader\settings.json`
- Linux: `~/.config/YtdlpDownloader/settings.json`

**Settings → Open settings folder** opens it. Unexpected errors are logged to `error.log` there.

Note: when you run from source with the Microsoft Store version of Python, Windows redirects
that folder to `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.x_…\LocalCache\Local\YtdlpDownloader`.
The packaged app is not affected.

## Appearance

**Settings → Appearance** switches between UI designs instantly; the choice is remembered.
Every design shows the same controls and works the same way — only the arrangement and
style differ. Each design is a single file in `ui/variants/`; see
[`ui/variants/README.md`](ui/variants/README.md) to change one or add your own.

To preview every design without opening the app:

```bash
python tools/screenshots.py --sheet
```

This writes light/dark screenshots of each design (normal, Advanced + Log open, small
window, empty) plus `contact-sheet.png` into `screenshots/`. Add `--check` to list layout
problems (missing controls, cut-off text, overlaps) and `--switch-test` to switch through
all designs live.

## Tips

- **"Sign in to confirm you're not a bot" / age-restricted videos:** open Advanced options → Cookies from
  and pick the browser where you're signed in. Firefox works best; Chrome and Edge lock
  or encrypt their cookies while running.
- **Retry** (right-click a download) re-runs the same link, preset and folder with your *current*
  cookies, rate limit and other Advanced settings.
- **Cancel** stops yt-dlp and its ffmpeg/Deno child processes and deletes the unfinished
  `.part` files.

## Project layout

```
main.py               entry point, theme, app setup
ui/main_window.py     the main window: creates the controls, applies a design
ui/variants/          one file per design (layout + colours); README.md explains the API
ui/theme.py           design system: colours, metrics, base stylesheet, light/dark switching
ui/kit.py             small helpers for writing design layouts
ui/dialogs.py         dependency wizard, yt-dlp updater, error details, About
ui/widgets.py         link box, toggles, advanced panel, list cells, error banner
ui/icon.py            app icon (drawn in code; `python -m ui.icon out.png` exports it)
core/downloader.py    queue, QProcess handling, output parsing, cancel + cleanup
core/deps.py          finding, version-checking and downloading the tools
core/presets.py       preset loading/saving and argument parsing
core/errors.py        raw yt-dlp output → friendly message + fix
core/process.py       child-process environment, no-console flags, process-tree kill
core/settings.py      settings JSON
core/paths.py         app, config and tool folders
presets.json          built-in presets
tools/screenshots.py  renders and checks every design off-screen (developer tool)
```

## Legal

yt-dlp is released under the Unlicense, the FFmpeg builds under the GPL, Deno under the MIT
license. Only download content you have the right to download.
