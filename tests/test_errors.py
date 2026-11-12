"""explain_error: realistic yt-dlp output → the right plain-language advice."""

from __future__ import annotations

import pytest

from core import errors
from core.errors import (
    ACTION_COOKIES,
    ACTION_DENO,
    ACTION_FFMPEG,
    ACTION_PRESET,
    ACTION_UPDATE,
    explain_error,
)

COMMAND = (
    '▶ yt-dlp.exe --ignore-config --newline -P "D:/Private videos" -o "%(title).150B [%(id)s].%(ext)s" '
    "--no-playlist -t mp4 https://www.youtube.com/watch?v=abc123def45"
)
EXTRACTING = "[youtube] Extracting URL: https://www.youtube.com/watch?v=abc123def45"
NSIG_WARNING = [
    "WARNING: [youtube] abc123def45: nsig extraction failed: Some formats may be missing",
    "         n = Xq3bTrq8mlA ; player = https://www.youtube.com/s/player/6d2f3a2c/player_ias.vflset/en_US/base.js",
    "         Please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the "
    "appropriate issue template. Confirm you are on the latest version using  yt-dlp -U",
]
CHALLENGE_WARNING = (
    "WARNING: [youtube] abc123def45: n challenge solving failed: Some formats may be missing. Ensure you "
    "have a supported JavaScript runtime and challenge solver script distribution installed. Review any "
    "warnings presented before this message. For more details, refer to  https://github.com/yt-dlp/yt-dlp/wiki/EJS"
)
NO_JS_WARNING = (
    "WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; "
    "to use another runtime add  --js-runtimes RUNTIME[:PATH]  to your command/config. YouTube extraction "
    "without a JS runtime has been deprecated, and some formats may be missing."
)
FORMAT_ERROR = (
    "ERROR: [youtube] abc123def45: Requested format is not available. Use --list-formats for a list of "
    "available formats"
)
SIGN_IN_ERROR = (
    "ERROR: [youtube] abc123def45: Sign in to confirm you’re not a bot. Use --cookies-from-browser or "
    "--cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
    "  for how to manually pass cookies."
)

T_TOO_LONG = errors._NAME_TOO_LONG.title
T_IN_USE = errors._FILE_IN_USE.title
T_CONVERSION = errors._CONVERSION_FAILED.title
T_FFMPEG = errors._FFMPEG_MISSING.title
T_FIREFOX = errors._FIREFOX_COOKIES.title
T_NO_BROWSER = errors._BROWSER_NOT_FOUND.title
T_LOCKED = errors._BROWSER_COOKIES_LOCKED.title
T_SIGN_IN = errors._SIGN_IN.title
T_UNAVAILABLE = errors._UNAVAILABLE.title
T_UNSUPPORTED = errors._UNSUPPORTED_URL.title
T_429 = errors._RATE_LIMITED.title
T_NETWORK = errors._NETWORK.title
T_DISK = errors._DISK_FULL.title
T_PERMISSION = errors._NO_PERMISSION.title
T_DENO = errors._NEEDS_DENO.title
T_TOO_OLD = errors._TOO_OLD.title
T_OUTDATED = errors._OUTDATED.title
T_FORMAT = errors._FORMAT_UNAVAILABLE.title
T_403 = errors._FORBIDDEN.title
T_GENERIC = "The download failed."

# (id, log lines, has_js_runtime, expected title, expected action)
CASES: list[tuple[str, list[str], bool, str, str | None]] = [
    # An outdated yt-dlp: the real cause is a warning, the error is vague.
    ("nsig-then-format", [COMMAND, EXTRACTING, *NSIG_WARNING, FORMAT_ERROR], True, T_OUTDATED, ACTION_UPDATE),
    ("challenge-then-format", [COMMAND, EXTRACTING, CHALLENGE_WARNING, FORMAT_ERROR], True,
     T_OUTDATED, ACTION_UPDATE),
    ("unable-to-extract", [
        EXTRACTING,
        "ERROR: [youtube] abc123def45: Unable to extract yt initial data; please report this issue on  "
        "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. Confirm "
        "you are on the latest version using  yt-dlp -U",
    ], True, T_OUTDATED, ACTION_UPDATE),
    ("no-formats-found", [
        "ERROR: [youtube] abc123def45: No video formats found!; please report this issue on  "
        "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. Confirm "
        "you are on the latest version using  yt-dlp -U",
    ], True, T_OUTDATED, ACTION_UPDATE),
    ("no-such-option", ["yt-dlp: error: no such option: -t"], True, T_TOO_OLD, ACTION_UPDATE),
    ("no-such-option-exe", [
        COMMAND, "Usage: yt-dlp.exe [OPTIONS] URL [URL...]", "yt-dlp.exe: error: no such option: -t",
    ], True, T_TOO_OLD, ACTION_UPDATE),
    # Missing JavaScript runtime: Deno when it's missing, an update when it's there.
    ("no-js-runtime", [NO_JS_WARNING, CHALLENGE_WARNING, FORMAT_ERROR], False, T_DENO, ACTION_DENO),
    ("js-runtime-present", [NO_JS_WARNING, CHALLENGE_WARNING, FORMAT_ERROR], True, T_OUTDATED, ACTION_UPDATE),
    ("no-js-but-disk-full", [
        NO_JS_WARNING,
        "ERROR: unable to write data: [Errno 28] No space left on device",
    ], False, T_DISK, None),
    # Retry warnings are not the cause.
    ("retrying-then-postprocessing", [
        EXTRACTING,
        "WARNING: [youtube] abc123def45: Unable to download webpage: The read operation timed out. Retrying (1/3)...",
        "[ExtractAudio] Destination: D:/Music/Song [abc123def45].mp3",
        "ERROR: Postprocessing: audio conversion failed: Error opening output files: Invalid argument",
    ], True, T_CONVERSION, ACTION_PRESET),
    ("retrying-only-warning", [
        "WARNING: [download] Got error: HTTP Error 403: Forbidden. Retrying fragment 3 (1/10)...",
        "ERROR: something nobody has seen before",
    ], True, T_GENERIC, None),
    # Titles, paths and the command line never trigger a rule.
    ("title-mentions-403", [
        COMMAND, EXTRACTING,
        "[TITLE]HTTP Error 403 explained",
        "[DEST]NA|NA|D:/Private videos/HTTP Error 403 explained [abc123def45].mp4",
        "[PP]Merger|started",
        "[FILE]D:/Private videos/HTTP Error 403 explained [abc123def45].mp4",
        "ERROR: something nobody has seen before",
    ], True, T_GENERIC, None),
    ("private-folder-conversion", [
        COMMAND,
        "[DEST]NA|NA|D:/Private videos/Song [abc123def45].webm",
        "[ExtractAudio] Destination: D:/Private videos/Song [abc123def45].mp3",
        "ERROR: Postprocessing: Conversion failed!",
    ], True, T_CONVERSION, ACTION_PRESET),
    ("private-folder-in-path", [
        COMMAND,
        "ERROR: unable to open for writing: [Errno 2] No such file or directory: "
        "'D:\\\\Private videos\\\\Video unavailable [abc123def45].mp4.part'",
    ], True, T_GENERIC, None),
    ("indented-line-after-info", [
        "[download] Destination: D:/x/Private video [abc123def45].mp4",
        "    HTTP Error 403: Forbidden",
        "ERROR: something nobody has seen before",
    ], True, T_GENERIC, None),
    ("command-line-only", [COMMAND, "ERROR: [generic] Unsupported URL: https://example.com/page"], True,
     T_UNSUPPORTED, None),
    # Problems on this computer.
    ("errno-36", [
        "ERROR: unable to open for writing: [Errno 36] File name too long: "
        "'/home/me/Videos/とても長いタイトルとても長いタイトル [abc123def45].f137.mp4.part'",
    ], True, T_TOO_LONG, None),
    ("winerror-206", [
        "ERROR: unable to open for writing: [WinError 206] The filename or extension is too long: "
        "'C:\\\\Users\\\\me\\\\Downloads\\\\Very long title [abc123def45].mp4.part'",
    ], True, T_TOO_LONG, None),
    ("ffmpeg-name-too-long", [
        "ERROR: Postprocessing: Error opening output file /home/me/Videos/long.mp4. File name too long",
    ], True, T_TOO_LONG, None),
    ("winerror-32", [
        "ERROR: Unable to rename file: [WinError 32] The process cannot access the file because it is being "
        "used by another process: 'Song [abc123def45].mp4.part' -> 'Song [abc123def45].mp4'",
    ], True, T_IN_USE, None),
    ("winerror-32-french", [
        "ERROR: Unable to rename file: [WinError 32] Le processus ne peut pas accéder au fichier car ce "
        "fichier est utilisé par un autre processus: 'a.mp4.part' -> 'a.mp4'",
    ], True, T_IN_USE, None),
    ("disk-full", ["ERROR: unable to write data: [Errno 28] No space left on device"], True, T_DISK, None),
    ("permission-denied", [
        "ERROR: unable to open for writing: [Errno 13] Permission denied: 'C:\\\\Program Files\\\\a.mp4.part'",
    ], True, T_PERMISSION, None),
    ("traceback-permission", [
        "Traceback (most recent call last):",
        '  File "yt_dlp\\__main__.py", line 17, in <module>',
        "PermissionError: [Errno 13] Permission denied: 'D:\\\\Videos\\\\a.mp4'",
    ], True, T_PERMISSION, None),
    ("ffmpeg-missing", [
        "ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path using "
        "--ffmpeg-location",
    ], True, T_FFMPEG, ACTION_FFMPEG),
    # Cookies.
    ("firefox-missing", [
        "ERROR: could not find firefox cookies database in C:\\Users\\me\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles",
    ], True, T_FIREFOX, ACTION_COOKIES),
    ("chrome-missing", [
        'ERROR: could not find chrome cookies database in "C:\\Users\\me\\AppData\\Local\\Google\\Chrome\\User Data"',
    ], True, T_NO_BROWSER, ACTION_COOKIES),
    ("dpapi", [
        "ERROR: Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  for more info",
    ], True, T_LOCKED, ACTION_COOKIES),
    ("chrome-locked", [
        "ERROR: Could not copy Chrome cookie database. See  https://github.com/yt-dlp/yt-dlp/issues/7271  "
        "for more info",
    ], True, T_LOCKED, ACTION_COOKIES),
    ("sign-in", [EXTRACTING, SIGN_IN_ERROR], True, T_SIGN_IN, ACTION_COOKIES),
    ("sign-in-beats-nsig", [*NSIG_WARNING, SIGN_IN_ERROR], True, T_SIGN_IN, ACTION_COOKIES),
    # The site.
    ("format-only", [EXTRACTING, FORMAT_ERROR], True, T_FORMAT, ACTION_PRESET),
    ("http-403", ["ERROR: unable to download video data: HTTP Error 403: Forbidden"], True, T_403, ACTION_UPDATE),
    ("http-429", [
        "ERROR: [youtube] abc123def45: Unable to download API page: HTTP Error 429: Too Many Requests",
    ], True, T_429, ACTION_COOKIES),
    ("private-video", [
        "ERROR: [youtube] abc123def45: Private video. Sign in if you've been granted access to this video",
    ], True, T_UNAVAILABLE, None),
    ("network", [
        "ERROR: [youtube] abc123def45: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo "
        "failed> (caused by TransportError('<urlopen error [Errno 11001] getaddrinfo failed>'))",
    ], True, T_NETWORK, None),
    # The last ERROR wins; warnings count only when no ERROR is recognised.
    ("last-error-wins", [
        "ERROR: [youtube] aaaaaaaaaaa: Private video. Sign in if you've been granted access to this video",
        "ERROR: [youtube] bbbbbbbbbbb: Requested format is not available. Use --list-formats",
    ], True, T_FORMAT, ACTION_PRESET),
    ("warning-only", [
        "WARNING: [youtube] abc123def45: Unable to download API page: HTTP Error 429: Too Many Requests",
    ], True, T_429, ACTION_COOKIES),
    ("nothing", [], True, T_GENERIC, None),
]


@pytest.mark.parametrize(
    ("lines", "has_js", "title", "action"),
    [pytest.param(lines, has_js, title, action, id=case_id) for case_id, lines, has_js, title, action in CASES],
)
def test_explain_error(lines: list[str], has_js: bool, title: str, action: str | None) -> None:
    error = explain_error(lines, has_js_runtime=has_js)
    assert (error.title, error.action) == (title, action)


def test_update_advice_points_to_settings() -> None:
    for lines in ([*NSIG_WARNING, FORMAT_ERROR], ["yt-dlp: error: no such option: -t"]):
        error = explain_error(lines, has_js_runtime=True)
        assert error.action == ACTION_UPDATE
        assert "Settings → Update yt-dlp" in error.hint


def test_cookie_hints_offer_other_choices() -> None:
    firefox = explain_error(CASES_BY_ID["firefox-missing"], has_js_runtime=True)
    assert "isn't installed" in firefox.hint and "cookies.txt" in firefox.hint
    locked = explain_error(CASES_BY_ID["dpapi"], has_js_runtime=True)
    assert "Windows" in locked.hint and "Firefox" in locked.hint and "cookies.txt" in locked.hint


def test_sign_in_hint_names_the_browser_used() -> None:
    error = explain_error([SIGN_IN_ERROR], has_js_runtime=True, cookies_browser="edge")
    assert error.hint.startswith("Cookies from Edge were used")
    file_error = explain_error([SIGN_IN_ERROR], has_js_runtime=True, cookies_browser="C:/me/cookies.txt")
    assert "cookies.txt file" in file_error.hint


def test_details_keep_the_raw_log() -> None:
    lines = [COMMAND, "[TITLE]HTTP Error 403 explained", "ERROR: boom"]
    error = explain_error(lines, has_js_runtime=True)
    assert error.details == "\n".join(lines)
    assert explain_error([], has_js_runtime=True).details == "(yt-dlp printed nothing)"


def test_needles_are_lowercase() -> None:
    for rule in errors._RULES:
        for needle in rule.needles:
            assert needle == needle.lower(), needle


CASES_BY_ID = {case_id: lines for case_id, lines, *_ in CASES}


def test_only_the_latest_attempt_counts() -> None:
    # The first attempt (outdated warning, then sign-in) was retried with cookies; the retry
    # fails on the format alone. The old attempt's messages stay in the log but explain nothing.
    lines = [COMMAND, EXTRACTING, *NSIG_WARNING, SIGN_IN_ERROR, COMMAND, EXTRACTING, FORMAT_ERROR]
    error = explain_error(lines, has_js_runtime=True)
    assert error.title == T_FORMAT
    assert error.details.splitlines()[0] == COMMAND
    assert SIGN_IN_ERROR not in error.details


def test_vague_warnings_do_not_outrank_the_error() -> None:
    warning = ("WARNING: [youtube] abc123def45: unable to extract uploader id; please report this issue "
               "on  https://github.com/yt-dlp/yt-dlp/issues?q= . Confirm you are on the latest version "
               "using  yt-dlp -U")
    lines = [COMMAND, EXTRACTING, warning, FORMAT_ERROR]
    assert explain_error(lines, has_js_runtime=True).title == T_FORMAT
    # With nothing better to go on, the warning still points to an update.
    lines = [COMMAND, EXTRACTING, warning, "ERROR: [youtube] abc123def45: something unexpected"]
    assert explain_error(lines, has_js_runtime=True).title == T_OUTDATED


def test_drm_warning_alone_is_not_blamed() -> None:
    warning = ("WARNING: [youtube] abc123def45: Some web client https formats have been skipped as "
               "they are DRM protected.")
    lines = [COMMAND, EXTRACTING, warning, "ERROR: [youtube] abc123def45: something unexpected"]
    assert explain_error(lines, has_js_runtime=True).title == T_GENERIC
    lines = [COMMAND, EXTRACTING, "ERROR: [vimeo] 12345: This video is DRM protected"]
    assert explain_error(lines, has_js_runtime=True).title == errors._DRM.title
