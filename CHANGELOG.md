# Changelog

## 1.0.0 — 2026-11-12

First release.

- One window built around yt-dlp: paste links (or drag & drop them), pick a preset, choose a
  folder, click Download. The numbered steps 01 Source → 04 Run show the way.
- Presets for every common need: best video as MP4, 1080p and 720p MP4, maximum quality MKV,
  MP3, and the original audio without re-encoding. Save your own from Advanced options.
- Up to 3 downloads at once, with progress, speed, time left and status for each. Cancel stops
  yt-dlp and everything it started, and removes the unfinished files.
- Plain-language error messages with a one-click fix: use your browser's cookies, try another
  preset, install Deno or ffmpeg, or update yt-dlp.
- First start: Weir offers to download yt-dlp, ffmpeg and Deno for you, checked against their
  published SHA-256 checksums.
- Light and dark mode that follow your system, and a notification when the queue is done.
- Windows installer (for your account only, no administrator rights, English and French) and
  a Linux AppImage.
