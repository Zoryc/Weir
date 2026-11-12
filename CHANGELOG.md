# Changelog

## Unreleased

Fixes and smoother first steps.

- Your first download: if the tools are missing, one question installs them all, and your links
  start by themselves once they're ready. The setup window shows each tool's progress instead of
  a red cross while it is being checked.
- Clearer error messages: the advice now matches what actually went wrong (a file name that's
  too long, a file in use, a full disk, an outdated yt-dlp, a failed conversion), and a retried
  download is explained by its latest attempt only.
- Cookies: the list shows only the browsers installed on your computer, and you can use a
  cookies.txt file instead. "Use browser cookies" never suggests a browser that can't work.
- The same video can be downloaded with two presets at once (say MP4 and MP3); they take turns.
  The same link with the same preset isn't added twice.
- Cancelling a download never deletes another download's files, and no longer shows an error
  on Linux.
- Tools are only installed if their checksum matches, and Weir checks for free disk space first.
- On small laptop screens, new downloads and error messages scroll into view.
- Opening Weir again brings the open window to the front and hands it your links.

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
