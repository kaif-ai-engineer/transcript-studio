"""Transcript retrieval, normalization, translation, and language resolution."""

from __future__ import annotations

import datetime
import html
import logging
import random
import re
import time
from typing import Iterable, List, Optional, Tuple

import requests
import yt_dlp
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    RequestBlocked,
    IpBlocked,
    YouTubeDataUnparsable,
    CouldNotRetrieveTranscript,
    NotTranslatable,
    TranslationLanguageNotAvailable,
    YouTubeTranscriptApiException,
)
from youtube_transcript_api._transcripts import Transcript, TranscriptList

from yt_tool.models import VideoMetadata, VideoTranscript, TranscriptSegment
from yt_tool.config import DEFAULT_CONFIG
from yt_tool.network import NetworkManager

logger = logging.getLogger(__name__)

# Permanent error types that should not be retried on the same source
PERMANENT_ERROR_TYPES = (
    TranscriptsDisabled,
    VideoUnavailable,
    NoTranscriptFound,
)

_YTDLP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_VTT_TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def format_timestamp(seconds: float) -> str:
    """
    Format a floating-point seconds value into [HH:MM:SS] or [MM:SS].

    Examples:
        format_timestamp(75.0) -> "[01:15]"
        format_timestamp(3665.0) -> "[01:01:05]"
    """
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"


def normalize_text(text: str) -> str:
    """
    Normalize text artifacts without altering the speaker's original words.

    - Decodes HTML entities (&amp; &#39; &quot;)
    - Normalizes non-breaking spaces (\\xa0) and control characters
    - Collapses multiple redundant spaces into a single space
    """
    if not text:
        return ""
    unescaped = html.unescape(text)
    unescaped = unescaped.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", unescaped)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned


def pick_best_transcript(
    transcript_list: TranscriptList,
    preferred_languages: Iterable[str],
    allow_translation: bool = True,
) -> Tuple[Optional[Transcript], str]:
    """
    Pick the most appropriate transcript based on user priority:
    1. Exact manual match for priority 1 (e.g. English)
    2. Exact generated match for priority 1 (e.g. English)
    3. Exact manual match for priority 2 (e.g. Hindi)
    4. Exact generated match for priority 2 (e.g. Hindi)
    5. If no direct match and allow_translation is True:
       Translate an available transcript to priority 1 (English), or priority 2 (Hindi)
    6. Fallback: Any available original manual transcript
    7. Fallback: Any available original auto-generated transcript

    Returns:
        Tuple of (selected_transcript, language_description)
    """
    preferred = [lang.strip().lower() for lang in preferred_languages if lang.strip()]
    if not preferred:
        preferred = ["en", "hi"]
    allow_any = "auto" in preferred

    all_transcripts: List[Transcript] = list(transcript_list)
    if not all_transcripts:
        return None, "No transcripts available"

    # Step 1 & 2: Direct match in priority order
    if not allow_any:
        for pref in preferred:
            for t in all_transcripts:
                if not t.is_generated:
                    t_lang = (t.language_code or "").lower()
                    if t_lang == pref or t_lang.startswith(f"{pref}-"):
                        return t, t.language or t.language_code
            for t in all_transcripts:
                if t.is_generated:
                    t_lang = (t.language_code or "").lower()
                    if t_lang == pref or t_lang.startswith(f"{pref}-"):
                        return t, f"{t.language or t.language_code} (auto-generated)"

    # Step 3: Auto-translation to preferred language (e.g. Bengali -> English or Hindi)
    if not allow_any and allow_translation:
        for pref in preferred:
            for t in all_transcripts:
                if t.is_translatable:
                    try:
                        translated = t.translate(pref)
                        orig_desc = t.language or t.language_code
                        target_desc = "English" if pref == "en" else ("Hindi" if pref == "hi" else pref.upper())
                        return translated, f"{orig_desc} (translated to {target_desc})"
                    except (NotTranslatable, TranslationLanguageNotAvailable, Exception) as exc:
                        logger.debug("Could not translate %s to %s: %s", t.language_code, pref, exc)
                        continue

    # Step 4: Fallback to any manual transcript
    for t in all_transcripts:
        if not t.is_generated:
            return t, f"{t.language or t.language_code} (original manual)"

    # Step 5: Fallback to any generated transcript
    for t in all_transcripts:
        if t.is_generated:
            return t, f"{t.language or t.language_code} (auto-generated)"

    # Final fallback
    t = all_transcripts[0]
    return t, t.language or t.language_code


def compile_full_text(segments: List[TranscriptSegment]) -> str:
    """
    Compile segments into readable paragraph-separated text.
    Groups segments by logical pauses (> 3.5 seconds) or punctuation.
    """
    if not segments:
        return ""

    paragraphs: List[str] = []
    current_para: List[str] = []
    last_end = 0.0

    for seg in segments:
        clean = seg.text.strip()
        if not clean:
            continue
        if current_para and (seg.start - last_end) > 3.5:
            paragraphs.append(" ".join(current_para))
            current_para = []
        current_para.append(clean)
        last_end = seg.start + seg.duration

    if current_para:
        paragraphs.append(" ".join(current_para))

    return "\n\n".join(paragraphs)


# --------------------------------------------------------------------------- #
# yt-dlp caption extraction helpers
#
# youtube-transcript-api talks to one YouTube endpoint; yt-dlp rotates through
# several player clients (web, tv, ios, android, ...). When the API is blocked,
# trying yt-dlp's caption tracks often succeeds because the rate-limit buckets
# differ per client. This is also the only path that can use a cookie file.
# --------------------------------------------------------------------------- #

def parse_json3_captions(payload: dict) -> List[TranscriptSegment]:
    """Parse a YouTube ``json3`` caption payload into segments."""
    segments: List[TranscriptSegment] = []
    for event in payload.get("events", []):
        text = normalize_text("".join(seg.get("utf8", "") for seg in event.get("segs", [])))
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                text=text,
                start=float(event.get("tStartMs", 0)) / 1000.0,
                duration=float(event.get("dDurationMs", 0)) / 1000.0,
            )
        )
    return segments


def parse_vtt_captions(text: str) -> List[TranscriptSegment]:
    """Parse a WebVTT caption document into segments."""
    def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    segments: List[TranscriptSegment] = []
    start: Optional[float] = None
    end: Optional[float] = None
    buffer: List[str] = []

    def flush() -> None:
        nonlocal start, end, buffer
        if start is not None:
            joined = normalize_text(" ".join(buffer))
            if joined:
                segments.append(
                    TranscriptSegment(text=joined, start=start, duration=max(0.0, (end or start) - start))
                )
        start = end = None
        buffer = []

    for line in text.splitlines():
        match = _VTT_TS.search(line)
        if match:
            flush()
            start = _to_seconds(*match.groups()[:4])
            end = _to_seconds(*match.groups()[4:])
        elif start is not None:
            if not line.strip():
                flush()
            else:
                buffer.append(line.strip())
    flush()
    return segments


def _lang_match(tracks: dict, langs: List[str]) -> Tuple[Optional[str], Optional[list]]:
    for lang in langs:
        for key, value in tracks.items():
            k = key.lower()
            if k == lang or k.startswith(f"{lang}-"):
                return key, value
    return None, None


def choose_caption_track(
    manual: dict,
    automatic: dict,
    preferred_languages: Iterable[str],
) -> Tuple[Optional[str], Optional[list], Optional[bool]]:
    """Choose the best caption track from yt-dlp's subtitle/auto-caption maps.

    Returns ``(language_code, track_list, is_generated)`` or ``(None, None, None)``.
    """
    preferred = [lang.strip().lower() for lang in preferred_languages if lang.strip()]
    auto_mode = "auto" in preferred

    if not auto_mode:
        for lang in preferred:
            key, value = _lang_match(manual, [lang])
            if key:
                return key, value, False
            key, value = _lang_match(automatic, [lang])
            if key:
                return key, value, True

    if manual:
        key = next(iter(manual))
        return key, manual[key], False
    if automatic:
        key = next(iter(automatic))
        return key, automatic[key], True
    return None, None, None


def pick_caption_url(tracks: list) -> Tuple[Optional[str], Optional[str]]:
    """Pick a caption URL and its format, preferring machine-friendly json3/vtt."""
    for ext in ("json3", "vtt", "srv3", "srv1", "srv2"):
        for track in tracks:
            if track.get("ext") == ext and track.get("url"):
                return track["url"], ext
    return None, None


class TranscriptFetcher:
    """Retrieves transcripts via youtube-transcript-api with a yt-dlp fallback.

    Rate limiting is handled by failing fast: a 429 against one client is very
    likely a 429 against all of them, so we try the alternate client once, then
    trip a short shared cooldown and give up on that video instead of grinding.
    """

    def __init__(
        self,
        max_retries: int = DEFAULT_CONFIG.max_retries,
        initial_backoff: float = DEFAULT_CONFIG.initial_backoff_seconds,
        backoff_factor: float = DEFAULT_CONFIG.backoff_factor,
        api: Optional[YouTubeTranscriptApi] = None,
        network_manager: Optional[NetworkManager] = None,
        allow_translation: bool = DEFAULT_CONFIG.allow_auto_translation,
        use_ytdlp_fallback: bool = True,
        cookie_file: Optional[str] = None,
    ):
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.backoff_factor = backoff_factor
        self.network_manager = network_manager or NetworkManager()
        self._custom_api = api
        self.allow_translation = allow_translation
        self.use_ytdlp_fallback = use_ytdlp_fallback
        self.cookie_file = cookie_file

    def _get_api(self) -> YouTubeTranscriptApi:
        if self._custom_api is not None:
            return self._custom_api
        return self.network_manager.create_transcript_api()

    # ------------------------------------------------------------------ #
    # yt-dlp fallback
    # ------------------------------------------------------------------ #

    def _extract_caption_info(self, video_id: str) -> Optional[dict]:
        opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "socket_timeout": DEFAULT_CONFIG.transcript_timeout_seconds,
        }
        if self.cookie_file:
            opts["cookiefile"] = self.cookie_file
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    def fetch_via_ytdlp(
        self,
        video: VideoMetadata,
        preferred_languages: List[str],
    ) -> Optional[VideoTranscript]:
        """Attempt to fetch a transcript through yt-dlp's caption tracks.

        Returns a :class:`VideoTranscript`, or ``None`` when the method cannot
        determine an outcome (caller then decides what to do).
        """
        try:
            info = self._extract_caption_info(video.video_id)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "429" in msg or "too many requests" in msg:
                return self._rate_limited_result(video)
            logger.debug("yt-dlp fallback failed for %s: %s", video.video_id, exc)
            return None

        if not info:
            return None

        manual = info.get("subtitles") or {}
        automatic = info.get("automatic_captions") or {}
        lang, tracks, is_generated = choose_caption_track(manual, automatic, preferred_languages)

        if not tracks:
            return VideoTranscript(
                video_id=video.video_id,
                title=video.title,
                url=video.url,
                playlist_index=video.playlist_index,
                status="failed",
                error_code="no_captions",
                error_reason="No captions found for the requested language(s).",
                retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )

        url, ext = pick_caption_url(tracks)
        if not url:
            return None

        try:
            resp = requests.get(url, headers=_YTDLP_HEADERS, timeout=DEFAULT_CONFIG.transcript_timeout_seconds)
            if resp.status_code == 429:
                return self._rate_limited_result(video)
            resp.raise_for_status()
        except requests.HTTPError:
            return self._rate_limited_result(video) if resp.status_code == 429 else None
        except requests.RequestException as exc:
            logger.debug("Caption URL fetch failed for %s: %s", video.video_id, exc)
            return None

        try:
            segments = (
                parse_json3_captions(resp.json())
                if ext == "json3"
                else parse_vtt_captions(resp.text)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Caption parse failed for %s (%s): %s", video.video_id, ext, exc)
            return None

        full_text = compile_full_text(segments)
        if not segments or not full_text.strip():
            return VideoTranscript(
                video_id=video.video_id,
                title=video.title,
                url=video.url,
                playlist_index=video.playlist_index,
                status="failed",
                language=lang,
                language_code=lang,
                is_generated=is_generated,
                error_code="empty_captions",
                error_reason="No readable spoken text in captions",
                retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )

        label = f"{lang} (auto-generated)" if is_generated else lang
        logger.info("Retrieved transcript for %s via yt-dlp fallback.", video.video_id)
        return VideoTranscript(
            video_id=video.video_id,
            title=video.title,
            url=video.url,
            playlist_index=video.playlist_index,
            status="success",
            language=label,
            language_code=lang,
            is_generated=is_generated,
            segments=segments,
            full_text=full_text,
            retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def fetch_video_transcript(
        self,
        video: VideoMetadata,
        preferred_languages: Optional[List[str]] = None,
        stop_event: Optional[any] = None,
    ) -> VideoTranscript:
        """Fetch and process the transcript for a single video.

        Strategy
        --------
        1. Respect the shared circuit breaker (another worker found rate limiting).
        2. Try youtube-transcript-api. Permanent errors return immediately.
        3. On a rate-limit signal, try the yt-dlp fallback once (different client).
        4. If both are blocked, trip a short shared cooldown and return a
           ``rate_limited`` failure so the job can stop instead of grinding.
        5. On generic transient errors, back off exponentially with jitter.
        """
        langs = preferred_languages or DEFAULT_CONFIG.default_languages
        retries = 0
        backoff = self.initial_backoff

        while True:
            if self.network_manager.circuit_breaker.is_tripped:
                logger.info("Rate-limit cooldown active, waiting before video %s...", video.video_id)
                resumed = self.network_manager.circuit_breaker.wait_if_tripped(stop_event=stop_event)
                if not resumed or (stop_event and stop_event.is_set()):
                    return VideoTranscript(
                        video_id=video.video_id,
                        title=video.title,
                        url=video.url,
                        playlist_index=video.playlist_index,
                        status="skipped",
                        error_reason="Job stopped during cooldown",
                    )

            try:
                transcript = self._do_fetch(video, langs, self._get_api())
                return transcript

            except PERMANENT_ERROR_TYPES as exc:
                reason = self._format_error(exc)
                logger.info("Permanent caption error for video %s: %s", video.video_id, reason)
                return VideoTranscript(
                    video_id=video.video_id,
                    title=video.title,
                    url=video.url,
                    playlist_index=video.playlist_index,
                    status="failed",
                    error_code="captions_unavailable",
                    error_reason=reason,
                    retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )

            except (RequestBlocked, IpBlocked):
                # Different YouTube client — often a different rate-limit bucket.
                if self.use_ytdlp_fallback:
                    fallback = self.fetch_via_ytdlp(video, langs)
                    if fallback is not None and fallback.error_code != "rate_limited":
                        return fallback

                self.network_manager.circuit_breaker.trip(
                    cooldown_seconds=DEFAULT_CONFIG.rate_limit_cooldown_seconds,
                    reason="YouTube rate limit (HTTP 429)",
                )
                return self._rate_limited_result(video)

            except Exception as exc:
                retries += 1
                reason = self._format_error(exc)
                if retries > self.max_retries:
                    logger.warning("Max retries exceeded for video %s: %s", video.video_id, reason)
                    return VideoTranscript(
                        video_id=video.video_id,
                        title=video.title,
                        url=video.url,
                        playlist_index=video.playlist_index,
                        status="failed",
                        error_code="error",
                        error_reason=f"Failed after {retries} attempts: {reason}",
                        retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    )
                sleep_time = backoff + random.uniform(0.3, 1.0)
                logger.info(
                    "Transient error for video %s (attempt %d/%d): %s — backing off %.2fs",
                    video.video_id, retries, self.max_retries, reason, sleep_time,
                )
                time.sleep(sleep_time)
                backoff = min(backoff * self.backoff_factor, 30.0)

    def _rate_limited_result(self, video: VideoMetadata) -> VideoTranscript:
        return VideoTranscript(
            video_id=video.video_id,
            title=video.title,
            url=video.url,
            playlist_index=video.playlist_index,
            status="failed",
            error_code="rate_limited",
            error_reason=(
                "YouTube is rate-limiting this IP (HTTP 429). This is usually the "
                "server's network, not your input. Wait a few minutes and retry, lower "
                "the parallel workers, or run the app locally where your own connection "
                "and cookies are used."
            ),
            retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def _do_fetch(
        self,
        video: VideoMetadata,
        preferred_languages: List[str],
        api: YouTubeTranscriptApi,
    ) -> VideoTranscript:
        """Internal fetch method executed once per attempt."""
        transcript_list = api.list(video.video_id)
        chosen, lang_label = pick_best_transcript(
            transcript_list,
            preferred_languages,
            allow_translation=self.allow_translation,
        )

        if not chosen:
            raise NoTranscriptFound(
                video_id=video.video_id,
                requested_language_codes=preferred_languages,
                transcript_data={},
            )

        fetched = chosen.fetch()
        raw_items = fetched.to_raw_data()

        segments: List[TranscriptSegment] = []
        for item in raw_items:
            clean_text = normalize_text(item.get("text", ""))
            if clean_text:
                segments.append(
                    TranscriptSegment(
                        text=clean_text,
                        start=float(item.get("start", 0.0)),
                        duration=float(item.get("duration", 0.0)),
                    )
                )

        full_text = compile_full_text(segments)

        if not segments or not full_text.strip():
            return VideoTranscript(
                video_id=video.video_id,
                title=video.title,
                url=video.url,
                playlist_index=video.playlist_index,
                status="failed",
                language=lang_label,
                language_code=chosen.language_code,
                is_generated=chosen.is_generated,
                error_code="empty_captions",
                error_reason="No readable spoken text in captions",
                retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )

        return VideoTranscript(
            video_id=video.video_id,
            title=video.title,
            url=video.url,
            playlist_index=video.playlist_index,
            status="success",
            language=lang_label,
            language_code=chosen.language_code,
            is_generated=chosen.is_generated,
            segments=segments,
            full_text=full_text,
            retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    @staticmethod
    def _format_error(exc: Exception) -> str:
        """Format an exception into a concise human-readable explanation."""
        if isinstance(exc, TranscriptsDisabled):
            return "Captions are disabled for this video."
        if isinstance(exc, NoTranscriptFound):
            return "No captions found for the requested language(s)."
        if isinstance(exc, VideoUnavailable):
            return "Video is unavailable or private."
        if isinstance(exc, (RequestBlocked, IpBlocked)):
            return "YouTube blocked or rate-limited caption requests."
        if isinstance(exc, YouTubeDataUnparsable):
            return "YouTube caption data could not be parsed."
        if isinstance(exc, CouldNotRetrieveTranscript):
            return f"Could not retrieve transcript: {exc}"
        if isinstance(exc, YouTubeTranscriptApiException):
            return f"YouTube caption API error: {exc}"
        return f"{type(exc).__name__}: {exc}"
