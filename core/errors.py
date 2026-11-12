"""Turn raw yt-dlp output into a plain-language message and a suggested fix."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Actions the UI knows how to offer as a one-click fix.
ACTION_COOKIES = "cookies"
ACTION_PRESET = "preset"
ACTION_DENO = "deno"
ACTION_FFMPEG = "ffmpeg"
ACTION_UPDATE = "update"

DETAIL_LINES = 15


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
    needles: tuple[str, ...]
    title: str
    hint: str
    action: str | None = None


_JS_RUNTIME_NEEDLES = (
    "no supported javascript runtime",
    "javascript runtime could be found",
    "js runtime",
)

_CHALLENGE_NEEDLES = (
    "challenge solving failed",
    "signature solving failed",
    "n challenge",
)

_RULES: tuple[_Rule, ...] = (
    _Rule(
        ("ffmpeg not found", "ffprobe not found", "ffmpeg is not installed",
         "ffprobe and ffmpeg not found", "ffmpeg could not be found", "ffprobe/avprobe and ffmpeg/avconv not found"),
        "ffmpeg is missing.",
        "This preset needs ffmpeg to merge or convert the file. Click the button to download it, then retry.",
        ACTION_FFMPEG,
    ),
    _Rule(
        ("could not copy chrome cookie database", "failed to decrypt with dpapi",
         "could not find firefox cookies database", "could not find chrome cookies database",
         "failed to decrypt cookie", "cookies database", "unsupported browser specified for cookies"),
        "Couldn't read cookies from your browser.",
        "Close that browser completely and retry, or choose Firefox under Advanced options → Cookies from "
        "(Chrome and Edge often lock or encrypt their cookies).",
        ACTION_COOKIES,
    ),
    _Rule(
        ("sign in to confirm you", "confirm you're not a bot", "confirm you’re not a bot",
         "confirm your age", "age-restricted", "age restricted", "inappropriate for some users",
         "use --cookies-from-browser", "use --cookies", "login required", "this video is only available for registered users",
         "members-only", "join this channel", "requires authentication"),
        "The site wants you to sign in.",
        "Open Advanced options → Cookies from and pick the browser where you're signed in "
        "(Firefox works best), then retry.",
        ACTION_COOKIES,
    ),
    _Rule(
        ("requested format is not available", "requested format not available"),
        "This video isn't available in the format this preset asks for.",
        "Pick another preset (for example “Best video – MP4”) and download it again.",
        ACTION_PRESET,
    ),
    _Rule(
        ("drm protected", "drm-protected", "this video is drm", "is drm protected"),
        "This video is copy-protected (DRM).",
        "yt-dlp can't download DRM-protected videos.",
    ),
    _Rule(
        ("this live event will begin", "premieres in", "live event will begin in"),
        "This stream hasn't started yet.",
        "Try again once it is live or after it has ended.",
    ),
    _Rule(
        ("private video", "video unavailable", "video is unavailable", "this video has been removed",
         "this video is not available", "no longer available",
         "not available in your country", "blocked it in your country", "geo restrict", "this video is private",
         "has been terminated", "account associated with this video has been terminated", "http error 404"),
        "This video is unavailable.",
        "It may be private, deleted, or blocked in your country. Check that the link opens in your browser.",
    ),
    _Rule(
        ("unsupported url",),
        "This link isn't supported.",
        "Check that you copied the full link of a video or playlist page.",
    ),
    _Rule(
        ("is not a valid url", "is not a valid URL"),
        "That doesn't look like a valid link.",
        "Copy the full address from your browser's address bar (it starts with https://).",
    ),
    _Rule(
        ("http error 429", "too many requests"),
        "The site is limiting downloads right now.",
        "Wait a little, lower “Parallel downloads” under Advanced options, or use cookies from your browser.",
        ACTION_COOKIES,
    ),
    _Rule(
        ("http error 403", "403: forbidden"),
        "The site refused the download (HTTP 403).",
        "Update yt-dlp (Settings → Update yt-dlp) and retry. Using cookies from your browser can also help.",
        ACTION_UPDATE,
    ),
    _Rule(
        ("no space left on device", "errno 28", "not enough space on the disk"),
        "The disk is full.",
        "Free up some space or choose another “Save to” folder.",
    ),
    _Rule(
        ("permission denied", "errno 13", "access is denied"),
        "Can't write to the “Save to” folder.",
        "Choose another folder you can write to (for example your Downloads folder).",
    ),
    _Rule(
        ("unable to download webpage", "getaddrinfo failed", "name or service not known",
         "temporary failure in name resolution", "network is unreachable", "connection refused",
         "connection reset", "timed out", "unable to connect", "nodename nor servname"),
        "Network problem.",
        "Check your internet connection and retry.",
    ),
)


def _tail(lines: Sequence[str], count: int = DETAIL_LINES) -> str:
    return "\n".join(list(lines)[-count:]) if lines else "(yt-dlp printed nothing)"


def _match(text: str) -> _Rule | None:
    for rule in _RULES:
        if any(needle.lower() in text for needle in rule.needles):
            return rule
    return None


def explain_error(lines: Sequence[str], *, has_js_runtime: bool, cookies_browser: str = "") -> FriendlyError:
    """Pick the most helpful explanation for a failed download."""
    details = _tail(lines)
    all_text = "\n".join(lines).lower()
    error_text = "\n".join(line for line in lines if line.startswith("ERROR")).lower()

    # A missing JavaScript runtime causes many follow-up errors on YouTube
    # ("format not available", "only images available"): fix the cause first.
    if not has_js_runtime and any(n in all_text for n in _JS_RUNTIME_NEEDLES):
        return FriendlyError(
            "yt-dlp needs Deno to fully support YouTube.",
            "Click the button to download Deno (a small JavaScript runtime), then retry.",
            ACTION_DENO,
            details,
        )

    rule = _match(error_text) or _match(all_text)
    if rule is None and any(n in all_text for n in _CHALLENGE_NEEDLES):
        return FriendlyError(
            "yt-dlp couldn't unlock this YouTube video.",
            "YouTube changes often. Update yt-dlp (Settings → Update yt-dlp) and retry.",
            ACTION_UPDATE,
            details,
        )
    if rule is None:
        return FriendlyError(
            "The download failed.",
            "Here are the last lines from yt-dlp. Copy them if you want to ask someone for help.",
            None,
            details,
        )
    hint = rule.hint
    if rule.action == ACTION_COOKIES and cookies_browser and "sign in" in rule.title.lower():
        hint = (
            f"Cookies from {cookies_browser.capitalize()} were used but didn't work. Make sure you're "
            "signed in to the site in that browser, close the browser, and retry. Firefox works best."
        )
    return FriendlyError(rule.title, hint, rule.action, details)
