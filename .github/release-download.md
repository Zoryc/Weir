
### Download

| System | File | How to install |
|--------|------|----------------|
| **Windows 10 / 11** | `Weir-Setup-{version}.exe` | Run it. It installs for your account only, no administrator rights needed. Windows may say *"Windows protected your PC"* because the installer isn't code-signed yet: click **More info → Run anyway**. |
| **Linux** (x86-64) | `Weir-{version}-x86_64.AppImage` | Make it executable (right-click → Properties → *Allow executing as program*, or `chmod +x`) and double-click it. If it doesn't start, install FUSE 2 (`sudo apt install libfuse2`). |

On first start, Weir offers to download the free tools it uses (yt-dlp, ffmpeg and Deno) with one click.
