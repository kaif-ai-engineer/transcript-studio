"""Transcript retrieval, normalization, translation, and language resolution."""

from __future__ import annotations
import html
import random
import re
import time
import logging
from typing import List, Optional, Tuple, Iterable, Set
import datetime

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
    - Normalizes non-breaking spaces (\xa0) and control characters
    - Collapses multiple redundant spaces into a single space
    """
    if not text:
        return ""
    # Unescape HTML entities
    unescaped = html.unescape(text)
    # Replace non-breaking spaces and zero-width spaces
    unescaped = unescaped.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    # Remove control characters except standard whitespace
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", unescaped)
    # Collapse multiple spaces and trim
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
            # Check manual match first
            for t in all_transcripts:
                if not t.is_generated:
                    t_lang = (t.language_code or "").lower()
                    if t_lang == pref or t_lang.startswith(f"{pref}-"):
                        return t, t.language or t.language_code
            # Check generated match
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
        
        # If there's a significant pause (> 3.5 seconds) between segments, break paragraph
        if current_para and (seg.start - last_end) > 3.5:
            paragraphs.append(" ".join(current_para))
            current_para = []

        current_para.append(clean)
        last_end = seg.start + seg.duration

    if current_para:
        paragraphs.append(" ".join(current_para))

    return "\n\n".join(paragraphs)


class TranscriptFetcher:
    """Retrieves transcripts with adaptive retries, cooldown recovery and translation."""

    def __init__(
        self,
        max_retries: int = DEFAULT_CONFIG.max_retries,
        initial_backoff: float = DEFAULT_CONFIG.initial_backoff_seconds,
        backoff_factor: float = DEFAULT_CONFIG.backoff_factor,
        api: Optional[YouTubeTranscriptApi] = None,
        network_manager: Optional[NetworkManager] = None,
        allow_translation: bool = DEFAULT_CONFIG.allow_auto_translation,
    ):
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.backoff_factor = backoff_factor
        self.network_manager = network_manager or NetworkManager()
        self._custom_api = api
        self.allow_translation = allow_translation

    def _get_api(self) -> YouTubeTranscriptApi:
        if self._custom_api is not None:
            return self._custom_api
        return self.network_manager.create_transcript_api()

    def fetch_video_transcript(
        self,
        video: VideoMetadata,
        preferred_languages: Optional[List[str]] = None,
        stop_event: Optional[any] = None,
    ) -> VideoTranscript:
        """Fetch and process the transcript for a single video.

        Strategy
        --------
        1. Respect the shared circuit breaker: if another worker detected rate
           limiting, wait out the cooldown before attempting anything.
        2. Attempt the fetch. Permanent errors (captions disabled, video private)
           return immediately.
        3. On rate-limit signals (``RequestBlocked`` / ``IpBlocked``) trip the
           shared cooldown so *all* workers pause, then retry a bounded number of
           times. This is the polite response — never hammer, never rotate IPs.
        4. On generic transient errors, back off exponentially with jitter.
        """
        langs = preferred_languages or DEFAULT_CONFIG.default_languages
        retries = 0
        backoff = self.initial_backoff

        while True:
            # Respect circuit breaker set by another worker.
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
                api = self._get_api()
                transcript = self._do_fetch(video, langs, api)
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
                    error_reason=reason,
                    retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )

            except (RequestBlocked, IpBlocked):
                retries += 1
                if retries > self.max_retries:
                    logger.warning(
                        "Still rate limited after %d attempts for video %s — giving up.",
                        retries,
                        video.video_id,
                    )
                    return VideoTranscript(
                        video_id=video.video_id,
                        title=video.title,
                        url=video.url,
                        playlist_index=video.playlist_index,
                        status="failed",
                        error_reason=(
                            "YouTube rate-limited this request (HTTP 429). "
                            "Add your browser cookies in Settings, lower the concurrency, "
                            "and try again in a few minutes. Already-downloaded videos are cached."
                        ),
                        retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    )

                cooldown = min(backoff * self.backoff_factor, 60.0)
                logger.warning(
                    "Rate limited on video %s (attempt %d/%d) — pausing workers for %.0fs",
                    video.video_id,
                    retries,
                    self.max_retries,
                    cooldown,
                )
                # Trip the shared breaker so every worker pauses together.
                self.network_manager.circuit_breaker.trip(
                    cooldown_seconds=cooldown,
                    reason="YouTube rate limit (HTTP 429)",
                )
                backoff = cooldown
                continue

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
                        error_reason=f"Failed after {retries} attempts: {reason}",
                        retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    )

                sleep_time = backoff + random.uniform(0.3, 1.0)
                logger.info(
                    "Transient error for video %s (attempt %d/%d): %s — backing off %.2fs",
                    video.video_id,
                    retries,
                    self.max_retries,
                    reason,
                    sleep_time,
                )
                time.sleep(sleep_time)
                backoff = min(backoff * self.backoff_factor, 30.0)

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

        # Requirement: Never mark a transcript successful when its content is missing or incomplete
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
