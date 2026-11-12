"""Turn raw yt-dlp output into a plain-language message and a suggested fix."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# Actions the UI knows how to offer as a one-click fix.
ACTION_COOKIES = "cookies"
ACTION_PRESET = "preset"
ACTION_DENO = "deno"
ACTION_FFMPEG = "ffmpeg"
ACTION_UPDATE = "update"

DETAIL_LINES = 15

# Lines Weir itself adds to the log: the command line and the --print markers
# that carry titles and file paths. They can contain any words, so they are
# never searched for error messages.
ATTEMPT_PREFIX = "▶"  # starts the command line logged at the start of each attempt
IGNORED_PREFIXES: tuple[str, ...] = (ATTEMPT_PREFIX, "[TITLE]", "[DEST]", "[FILE]", "[PP]", "[PROG]")

# "yt-dlp: error: no such option: -t" (the program name varies: yt-dlp.exe, yt-dlp_linux…).
_USAGE_ERROR_RE = re.compile(r"^[\w.\-]+: error: ", re.IGNORECASE)
# Last line of a Python traceback, e.g. "PermissionError: [Errno 13] Permission denied: '…'".
_EXCEPTION_RE = re.compile(r"^(?:[A-Za-z_][\w.]*\.)?[A-Za-z_]\w*(?:Error|Exception): ")
# The file path after an OS error ("[Errno 2] No such file or directory: 'D:\Private videos\…'").
# It can contain any words (titles, folder names), so it is cut off before matching.
_OS_ERROR_PATH_RE = re.compile(r"(\[(?:errno|winerror) -?\d+\][^'\"]*?):\s*['\"].*$", re.IGNORECASE)


@dataclass(frozen=True)
class FriendlyError:
    title: str
    hint: str
    action: str | None
    details: str

    def as_text(self, url: str = "") -> str:
        parts = [self.title, self.hint]
        if url:
            parts.append(f"Link: {url}")
        parts.append("")
        parts.append("Last lines from yt-dlp:")
        parts.append(self.details)
        return "\n".join(parts)


@dataclass(frozen=True)
class _Rule:
    needles: tuple[str, ...]  # lowercase
    title: str
    hint: str
    action: str | None = None
    # True for causes that often show up only as a WARNING, after which yt-dlp
    # fails with a vaguer ERROR further down the list ("nsig extraction failed"
    # followed by "Requested format is not available"). Such a warning wins
    # over an ERROR that only matches a later rule.
    explains_later_errors: bool = False
    # True for messages that only mean something as an ERROR: yt-dlp also prints
    # them as harmless warnings (e.g. "some formats are DRM protected, skipping").
    errors_only: bool = False
    # For messages a fixed needle can't pin down. Searched in the lowercased text.
    pattern: re.Pattern[str] | None = None

    def matches(self, text: str) -> bool:
        if any(needle in text for needle in self.needles):
            return True
        return self.pattern is not None and self.pattern.search(text) is not None


# -- problems on this computer ------------------------------------------------

_NAME_TOO_LONG = _Rule(
    ("file name too long", "filename too long", "errno 36", "winerror 206",
     "filename or extension is too long"),
    "The file name is too long.",
    "Click Reset next to File name under Advanced options to use a shorter name pattern, then "
    "retry. Or choose a shorter “Save to” folder and add the link again (Retry keeps the folder).",
)
_FILE_IN_USE = _Rule(
    ("winerror 32", "unable to rename", "being used by another process"),
    "The file is in use by another program.",
    "Close any program that has the file open (a video player, for example), wait a moment for "
    "antivirus or cloud sync to let go of it, then retry.",
)
_DISK_FULL = _Rule(
    ("no space left on device", "errno 28", "not enough space on the disk", "winerror 112"),
    "The disk is full.",
    "Free up some space or choose another “Save to” folder.",
)
_NO_PERMISSION = _Rule(
    ("permission denied", "errno 13", "access is denied", "[winerror 5]"),
    "Can't write to the “Save to” folder.",
    "Choose another folder you can write to (for example your Downloads folder).",
)
_FFMPEG_MISSING = _Rule(
    ("ffmpeg not found", "ffprobe not found", "ffmpeg is not installed",
     "ffprobe and ffmpeg not found", "ffmpeg could not be found", "ffprobe/avprobe and ffmpeg/avconv not found"),
    "ffmpeg is missing.",
    "This preset needs ffmpeg to merge or convert the file. Click the button to download it, then retry.",
    ACTION_FFMPEG,
)
_CONVERSION_FAILED = _Rule(
    ("postprocessing:", "conversion failed"),
    "ffmpeg couldn't convert the file.",
    "Pick another preset (for example “Best video – MP4”) and download it again. If it keeps "
    "happening, download ffmpeg again under Settings → Tools.",
    ACTION_PRESET,
)

# -- cookies ------------------------------------------------------------------

_FIREFOX_COOKIES = _Rule(
    ("could not find firefox cookies database",),
    "Firefox isn't set up for cookies.",
    "Firefox isn't installed, or you never signed in to the site there. Under Advanced options → "
    "Cookies from, pick the browser you actually use, or a cookies.txt file.",
    ACTION_COOKIES,
)
_BROWSER_NOT_FOUND = _Rule(
    ("could not find chrome cookies database", "could not find edge cookies database",
     "could not find brave cookies database", "could not find chromium cookies database",
     "could not find opera cookies database", "could not find vivaldi cookies database",
     "unsupported browser specified for cookies"),
    "Couldn't find that browser's cookies.",
    "That browser doesn't seem to be installed for your account. Under Advanced options → "
    "Cookies from, pick the browser you actually use, or a cookies.txt file.",
    ACTION_COOKIES,
)
_BROWSER_COOKIES_LOCKED = _Rule(
    ("could not copy chrome cookie database", "cookie database", "dpapi", "app-bound", "app bound",
     "failed to decrypt cookie", "cookies that could not be decrypted", "cannot decrypt v10 cookies",
     "cannot decrypt v11 cookies", "cannot decrypt v20 cookies", "database is locked"),
    "Couldn't read cookies from your browser.",
    "On Windows, Chrome, Edge and Brave keep their cookies locked away from other apps, so Weir "
    "usually can't use them, even with the browser closed. Sign in to the site in Firefox and pick "
    "Firefox under Advanced options → Cookies from, or use a cookies.txt file.",
    ACTION_COOKIES,
)

# -- the site -----------------------------------------------------------------

# Ahead of _SIGN_IN: yt-dlp ends YouTube's "Private video" error with its usual
# "Use --cookies-from-browser or --cookies" hint, but cookies only help someone
# the owner invited. explain_error words it differently once cookies were used.
_PRIVATE = _Rule(
    ("private video", "this video is private"),
    "This video is private.",
    "Only people the video's owner has invited can watch it. If that's you, open Advanced "
    "options → Cookies from and pick the browser where you're signed in (Firefox works best), "
    "then retry.",
    ACTION_COOKIES,
)
_SIGN_IN = _Rule(
    ("sign in to confirm you", "confirm you're not a bot", "confirm you’re not a bot",
     "confirm your age", "age-restricted", "age restricted", "inappropriate for some users",
     "use --cookies-from-browser", "use --cookies", "login required", "this video is only available for registered users",
     "members-only", "join this channel", "requires authentication"),
    "The site wants you to sign in.",
    "Open Advanced options → Cookies from and pick the browser where you're signed in "
    "(Firefox works best), then retry.",
    ACTION_COOKIES,
)
_DRM = _Rule(
    ("drm protected", "drm-protected", "this video is drm", "is drm protected"),
    "This video is copy-protected (DRM).",
    "yt-dlp can't download DRM-protected videos.",
    errors_only=True,
)
_NOT_LIVE_YET = _Rule(
    ("this live event will begin", "premieres in", "live event will begin in"),
    "This stream hasn't started yet.",
    "Try again once it is live or after it has ended.",
)
_UNAVAILABLE = _Rule(
    ("video unavailable", "video is unavailable", "this video has been removed",
     "this video is not available", "no longer available",
     # YouTube: "The uploader has not made this video available in your country".
     "not available in your country", "made this video available in your country",
     "blocked it in your country", "geo restrict",
     "has been terminated", "account associated with this video has been terminated", "http error 404"),
    "This video is unavailable.",
    "It may be private, deleted, or blocked in your country. Check that the link opens in your browser.",
)
_UNSUPPORTED_URL = _Rule(
    ("unsupported url",),
    "This link isn't supported.",
    "Check that you copied the full link of a video or playlist page.",
)
_INVALID_URL = _Rule(
    ("is not a valid url",),
    "That doesn't look like a valid link.",
    "Copy the full address from your browser's address bar (it starts with https://).",
)
_RATE_LIMITED = _Rule(
    ("http error 429", "too many requests"),
    "The site is limiting downloads right now.",
    "Wait a little, lower “Parallel downloads” under Advanced options, or use cookies from your browser.",
    ACTION_COOKIES,
)
# Used instead when cookies were already used: switching browsers would only drop working ones.
_RATE_LIMITED_WITH_COOKIES_HINT = (
    "Wait a little or lower “Parallel downloads” under Advanced options, then retry."
)
_FORBIDDEN_TITLE = "The site refused the download (HTTP 403)."
_FORBIDDEN_HINT = (
    "Update yt-dlp (Settings → Update yt-dlp) and retry. Using cookies from your browser can also help."
)
# The site refused a page yt-dlp needed ("Unable to download webpage: HTTP Error
# 403: Forbidden", "… API page …", "… JSON metadata …"). It must come before
# _NETWORK, whose "unable to download webpage" would call it a network problem.
# Refused video data ("unable to download video data: HTTP Error 403") is left
# to _FORBIDDEN, last in _RULES, as a failed YouTube challenge often explains it.
_FORBIDDEN_PAGE = _Rule(
    (),
    _FORBIDDEN_TITLE,
    _FORBIDDEN_HINT,
    ACTION_UPDATE,
    pattern=re.compile(r"unable to download (?!video data\b)[^:]*: http error 403\b"),
)
_NETWORK = _Rule(
    ("unable to download webpage", "getaddrinfo failed", "name or service not known",
     "temporary failure in name resolution", "network is unreachable", "connection refused",
     "connection reset", "timed out", "unable to connect", "nodename nor servname"),
    "Network problem.",
    "Check your internet connection and retry.",
)

# -- yt-dlp itself (also trusted when they only appear as a WARNING) ------------

_NEEDS_DENO = _Rule(
    ("no supported javascript runtime", "javascript runtime could be found", "js runtime"),
    "yt-dlp needs Deno to fully support YouTube.",
    "Click the button to download Deno (a small JavaScript runtime), then retry.",
    ACTION_DENO,
    explains_later_errors=True,
)
_TOO_OLD = _Rule(
    ("no such option",),
    "This yt-dlp is too old.",
    "Update it under Settings → Update yt-dlp, then retry. If you typed your own options under "
    "Advanced options, check them for typos too.",
    ACTION_UPDATE,
    explains_later_errors=True,
)
_OUTDATED_TITLE = "yt-dlp needs an update to handle this site."
_OUTDATED_HINT = (
    "Sites like YouTube change often. Update yt-dlp (Settings → Update yt-dlp), then retry. "
    "If it still fails right after updating, the site changed very recently: try again in a few days."
)
# YouTube's signature and "n" challenges: a failure there, even as a warning,
# explains a vague error later on.
_OUTDATED = _Rule(
    ("challenge solving failed", "signature solving failed", "n challenge solving",
     "nsig extraction failed", "signature extraction failed"),
    _OUTDATED_TITLE,
    _OUTDATED_HINT,
    ACTION_UPDATE,
    explains_later_errors=True,
)
# Vaguer signs. yt-dlp also warns "unable to extract uploader id" and the like
# about details it can do without, so these never outrank an ERROR.
_OUTDATED_MAYBE = _Rule(
    ("unable to extract", "yt-dlp -u"),
    _OUTDATED_TITLE,
    _OUTDATED_HINT,
    ACTION_UPDATE,
)
_FORMAT_UNAVAILABLE = _Rule(
    ("requested format is not available", "requested format not available"),
    "This video isn't available in the format this preset asks for.",
    "Pick another preset (for example “Best video – MP4”) and download it again.",
    ACTION_PRESET,
)
_FORBIDDEN = _Rule(
    ("http error 403", "403: forbidden"),
    _FORBIDDEN_TITLE,
    _FORBIDDEN_HINT,
    ACTION_UPDATE,
)

# Most specific first. Problems on this computer come first because their
# messages quote file paths, which can contain any words.
_RULES: tuple[_Rule, ...] = (
    _NAME_TOO_LONG,
    _FILE_IN_USE,
    _DISK_FULL,
    _NO_PERMISSION,
    _FFMPEG_MISSING,
    _CONVERSION_FAILED,
    _FIREFOX_COOKIES,
    _BROWSER_NOT_FOUND,
    _BROWSER_COOKIES_LOCKED,
    _PRIVATE,
    _SIGN_IN,
    _DRM,
    _NOT_LIVE_YET,
    _UNAVAILABLE,
    _UNSUPPORTED_URL,
    _INVALID_URL,
    _RATE_LIMITED,
    _FORBIDDEN_PAGE,
    _NETWORK,
    _NEEDS_DENO,
    _TOO_OLD,
    _OUTDATED,
    _OUTDATED_MAYBE,
    _FORMAT_UNAVAILABLE,
    _FORBIDDEN,
)

_BROWSER_NAMES = {"firefox": "Firefox", "chrome": "Chrome", "edge": "Edge", "brave": "Brave",
                  "chromium": "Chromium", "opera": "Opera", "vivaldi": "Vivaldi"}


def _last_attempt(lines: Sequence[str]) -> list[str]:
    """The lines of the latest attempt: from the last command line ("▶ …") on.

    A retried download keeps the log of its earlier attempts, whose messages
    must not explain the new failure.
    """
    lines = list(lines)
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith(ATTEMPT_PREFIX):
            return lines[index:]
    return lines


def _tail(lines: Sequence[str], count: int = DETAIL_LINES) -> str:
    return "\n".join(list(lines)[-count:]) if lines else "(yt-dlp printed nothing)"


def _diagnostics(lines: Sequence[str]) -> tuple[list[str], list[str]]:
    """yt-dlp's ERROR and WARNING messages, lowercased, oldest first.

    Indented continuation lines are joined to their message, file paths after
    OS errors are cut off, and warnings about retries are dropped (yt-dlp
    usually recovers from those).
    """
    messages: list[tuple[bool, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith(IGNORED_PREFIXES):
            current = None
        elif line.startswith("ERROR:") or _USAGE_ERROR_RE.match(line) or _EXCEPTION_RE.match(line):
            current = [line]
            messages.append((True, current))
        elif line.startswith("WARNING:"):
            current = [line]
            messages.append((False, current))
        elif current is not None and line[:1].isspace():
            current.append(line.strip())
        else:
            current = None

    errors: list[str] = []
    warnings: list[str] = []
    for is_error, parts in messages:
        text = _OS_ERROR_PATH_RE.sub(r"\1", " ".join(parts).lower())
        if is_error:
            errors.append(text)
        elif "retrying" not in text:
            warnings.append(text)
    return errors, warnings


def _match(rules: Sequence[_Rule], text: str) -> _Rule | None:
    for rule in rules:
        if rule.matches(text):
            return rule
    return None


def _pick_rule(lines: Sequence[str], *, has_js_runtime: bool) -> _Rule | None:
    rules = tuple(rule for rule in _RULES if not (has_js_runtime and rule is _NEEDS_DENO))
    errors, warnings = _diagnostics(lines)

    # The last ERROR is the one that stopped yt-dlp.
    found: _Rule | None = None
    for text in reversed(errors):
        found = _match(rules, text)
        if found is not None:
            break

    # A missing JavaScript runtime or an outdated yt-dlp often shows up only as
    # a warning, followed by a vague error ("Requested format is not available").
    limit = rules.index(found) if found is not None else len(rules)
    for rule in rules[:limit]:
        if rule.explains_later_errors and any(rule.matches(text) for text in warnings):
            return rule
    if found is not None:
        return found

    warning_rules = tuple(rule for rule in rules if not rule.errors_only)
    for text in reversed(warnings):
        found = _match(warning_rules, text)
        if found is not None:
            return found
    return None


_COOKIES_FILE_SOURCE = "your cookies.txt file"


def _cookie_source(cookies_browser: str) -> str:
    value = cookies_browser.strip()
    if "/" in value or "\\" in value or value.lower().endswith(".txt"):
        return _COOKIES_FILE_SOURCE
    return _BROWSER_NAMES.get(value.lower(), value.capitalize())


def _advice_with_cookies(rule: _Rule, source: str) -> tuple[str, str | None]:
    """Hint and action once cookies from `source` were used for the failed download.

    A sign-in request names the cookies that didn't work. For rate limiting,
    other cookies won't help: "Use browser cookies" would only switch away from
    cookies that work, so it isn't offered. A private video names the cookies
    too. Its fix is kept only for a cookies.txt, where it asks for a fresh
    export (YouTube rotates exported cookies, so old ones stop working); for a
    browser it would just switch browsers, again and again.
    """
    if rule is _SIGN_IN:
        if source == _COOKIES_FILE_SOURCE:
            return (
                "Cookies from your cookies.txt file were used but didn't work. Export a fresh "
                "cookies.txt while you're signed in to the site, then retry.",
                rule.action,
            )
        return (
            f"Cookies from {source} were used but didn't work. Make sure you're "
            "signed in to the site in that browser, close the browser, and retry. Firefox works best.",
            rule.action,
        )
    if rule is _PRIVATE:
        # The cookies didn't give access, which doesn't prove their account wasn't invited:
        # they may be logged out or out of date, so don't claim it can't see the video.
        if source == _COOKIES_FILE_SOURCE:
            return (
                "Only people the video's owner has invited can watch it. Cookies from your "
                "cookies.txt file were used but didn't give access: they may be from another "
                "account, or out of date (older exports stop working). Export a fresh cookies.txt "
                "while you're signed in with the invited account, choose it under Advanced "
                "options → Cookies from, then retry.",
                rule.action,
            )
        return (
            f"Only people the video's owner has invited can watch it. Cookies from {source} were "
            f"used but didn't give access. Make sure you're signed in to the site in {source} "
            "with the account that was invited, then retry.",
            None,
        )
    if rule is _RATE_LIMITED:
        return _RATE_LIMITED_WITH_COOKIES_HINT, None
    return rule.hint, rule.action


def explain_error(lines: Sequence[str], *, has_js_runtime: bool, cookies_browser: str = "") -> FriendlyError:
    """Pick the most helpful explanation for a failed download (its latest attempt)."""
    lines = _last_attempt(lines)
    details = _tail(lines)
    rule = _pick_rule(lines, has_js_runtime=has_js_runtime)
    if rule is None:
        return FriendlyError(
            "The download failed.",
            "Here are the last lines from yt-dlp. Copy them if you want to ask someone for help.",
            None,
            details,
        )
    if cookies_browser.strip():
        hint, action = _advice_with_cookies(rule, _cookie_source(cookies_browser))
    else:
        hint, action = rule.hint, rule.action
    return FriendlyError(rule.title, hint, action, details)
