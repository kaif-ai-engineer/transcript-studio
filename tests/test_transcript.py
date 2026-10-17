"""Tests for transcript retrieval, normalization, language fallback, and error handling."""

import pytest
from unittest.mock import MagicMock
from youtube_transcript_api import (
    TranscriptsDisabled,
    VideoUnavailable,
    NoTranscriptFound,
)

from yt_tool.transcript import (
    format_timestamp,
    normalize_text,
    pick_best_transcript,
    compile_full_text,
    TranscriptFetcher,
)
from yt_tool.models import VideoMetadata, TranscriptSegment, VideoTranscript


def test_format_timestamp():
    """Verify timestamp formatting for seconds under and over 1 hour."""
    assert format_timestamp(0) == "[00:00]"
    assert format_timestamp(45.2) == "[00:45]"
    assert format_timestamp(75.9) == "[01:15]"
    assert format_timestamp(3600) == "[01:00:00]"
    assert format_timestamp(3665.4) == "[01:01:05]"
    assert format_timestamp(7325) == "[02:02:05]"


def test_normalize_text():
    """Verify HTML entities, non-breaking spaces, and whitespace normalization."""
    raw = "Hello &amp; welcome!&#39;s &quot;AI Course&quot; \xa0\xa0\u200b  with  extra   spaces."
    expected = "Hello & welcome!'s \"AI Course\" with extra spaces."
    assert normalize_text(raw) == expected

    # Control character removal
    with_control = "Text with \x00null and \x07bell"
    assert normalize_text(with_control) == "Text with null and bell"


def test_pick_best_transcript_language_matching():
    """Verify language selection rules prioritizing manual over generated and respecting language codes."""
    # Create mock transcript objects
    t_en_manual = MagicMock(language_code="en", language="English", is_generated=False)
    t_en_gen = MagicMock(language_code="en", language="English (auto)", is_generated=True)
    t_es_manual = MagicMock(language_code="es", language="Spanish", is_generated=False)
    t_fr_gen = MagicMock(language_code="fr", language="French (auto)", is_generated=True)

    # 1. Prefer English: should pick manual English
    chosen, desc = pick_best_transcript([t_en_gen, t_en_manual, t_es_manual], ["en"])
    assert chosen == t_en_manual
    assert "English" in desc

    # 2. Prefer Spanish: should pick manual Spanish
    chosen, desc = pick_best_transcript([t_en_gen, t_en_manual, t_es_manual], ["es"])
    assert chosen == t_es_manual

    # 3. Prefer English, only generated available: should pick generated English
    chosen, desc = pick_best_transcript([t_fr_gen, t_en_gen], ["en"])
    assert chosen == t_en_gen
    assert "auto-generated" in desc

    # 4. English first, Hindi second: video has Hindi -> picks Hindi
    t_hi_gen = MagicMock(language_code="hi", language="Hindi", is_generated=True, is_translatable=False)
    chosen, desc = pick_best_transcript([t_fr_gen, t_hi_gen], ["en", "hi"])
    assert chosen == t_hi_gen

    # 5. Translation: Video only has Bengali ('bn'), but is translatable to 'en'
    t_translated_en = MagicMock(language_code="en", language="English", is_generated=True)
    t_bn_manual = MagicMock(
        language_code="bn",
        language="Bengali",
        is_generated=False,
        is_translatable=True,
    )
    t_bn_manual.translate.return_value = t_translated_en
    chosen, desc = pick_best_transcript([t_bn_manual], ["en", "hi"], allow_translation=True)
    assert chosen == t_translated_en
    assert "translated to English" in desc

    # 6. Preferred language not present and not translatable: fallback to available
    t_es_manual.is_translatable = False
    chosen, desc = pick_best_transcript([t_es_manual], ["de"], allow_translation=False)
    assert chosen == t_es_manual

    # 7. 'auto' mode: should pick any manual first, then generated
    chosen, desc = pick_best_transcript([t_fr_gen, t_es_manual], ["auto"])
    assert chosen == t_es_manual



def test_compile_full_text():
    """Verify grouping segments into clean paragraphs by pause thresholds."""
    segments = [
        TranscriptSegment(text="Sentence one.", start=0.0, duration=2.0),
        TranscriptSegment(text="Sentence two.", start=2.5, duration=2.0),
        # Long pause here: 10.0 - 4.5 = 5.5s > 3.5s
        TranscriptSegment(text="Second paragraph begins here.", start=10.0, duration=3.0),
    ]
    compiled = compile_full_text(segments)
    assert compiled == "Sentence one. Sentence two.\n\nSecond paragraph begins here."


def test_fetch_successful_transcript(mocker):
    """Verify successful transcript fetching with mocked YouTubeTranscriptApi."""
    video = VideoMetadata(
        video_id="vid_test_123",
        title="Python Data Structures",
        url="https://www.youtube.com/watch?v=vid_test_123",
        playlist_index=1,
    )

    mock_transcript_obj = mocker.MagicMock()
    mock_transcript_obj.language = "English"
    mock_transcript_obj.language_code = "en"
    mock_transcript_obj.is_generated = False
    
    mock_fetched = mocker.MagicMock()
    mock_fetched.to_raw_data.return_value = [
        {"text": "Welcome to Python lists &amp; dicts.", "start": 0.0, "duration": 3.0},
        {"text": "They provide efficient key-value lookups.", "start": 3.2, "duration": 4.0},
    ]
    mock_transcript_obj.fetch.return_value = mock_fetched

    mock_api = mocker.MagicMock()
    mock_api.list.return_value = [mock_transcript_obj]

    fetcher = TranscriptFetcher(api=mock_api)
    result = fetcher.fetch_video_transcript(video, ["en"])

    assert result.is_successful()
    assert result.status == "success"
    assert result.language == "English"
    assert len(result.segments) == 2
    assert "lists & dicts" in result.full_text


def test_fetch_empty_transcript_never_marked_success(mocker):
    """Requirement: Never mark a transcript successful when its content is missing or incomplete."""
    video = VideoMetadata(
        video_id="vid_empty_123",
        title="Empty Captions Video",
        url="https://www.youtube.com/watch?v=vid_empty_123",
        playlist_index=2,
    )

    mock_transcript_obj = mocker.MagicMock()
    mock_transcript_obj.language = "English"
    mock_transcript_obj.language_code = "en"
    mock_transcript_obj.is_generated = False

    mock_fetched = mocker.MagicMock()
    # Empty raw data
    mock_fetched.to_raw_data.return_value = []
    mock_transcript_obj.fetch.return_value = mock_fetched

    mock_api = mocker.MagicMock()
    mock_api.list.return_value = [mock_transcript_obj]

    fetcher = TranscriptFetcher(api=mock_api)
    result = fetcher.fetch_video_transcript(video, ["en"])

    assert not result.is_successful()
    assert result.status == "failed"
    assert "No readable spoken text" in result.error_reason


def test_fetch_transcripts_disabled(mocker):
    """Verify permanent error when captions are disabled on YouTube."""
    video = VideoMetadata(
        video_id="vid_disabled_99",
        title="Captions Disabled Video",
        url="https://www.youtube.com/watch?v=vid_disabled_99",
        playlist_index=3,
    )

    mock_api = mocker.MagicMock()
    mock_api.list.side_effect = TranscriptsDisabled(video.video_id)

    fetcher = TranscriptFetcher(api=mock_api)
    result = fetcher.fetch_video_transcript(video, ["en"])

    assert not result.is_successful()
    assert result.status == "failed"
    assert "Captions are disabled" in result.error_reason


def test_fetch_unicode_and_special_characters(mocker):
    """Verify correct handling of multilingual scripts, accents, and unicode."""
    video = VideoMetadata(
        video_id="vid_unicode_88",
        title="Multilingual Lesson: Español & 日本語 & Math: ∑(x)",
        url="https://www.youtube.com/watch?v=vid_unicode_88",
        playlist_index=4,
    )

    mock_transcript_obj = mocker.MagicMock()
    mock_transcript_obj.language = "Spanish"
    mock_transcript_obj.language_code = "es"
    mock_transcript_obj.is_generated = False

    mock_fetched = mocker.MagicMock()
    mock_fetched.to_raw_data.return_value = [
        {"text": "¡Bienvenidos! Esta lección cubre optimización matemática: ∑(x_i) ≥ 0.", "start": 0.0, "duration": 5.0},
        {"text": "日本語の字幕テスト: 機械学習の基礎。", "start": 5.2, "duration": 4.0},
    ]
    mock_transcript_obj.fetch.return_value = mock_fetched

    mock_api = mocker.MagicMock()
    mock_api.list.return_value = [mock_transcript_obj]

    fetcher = TranscriptFetcher(api=mock_api)
    result = fetcher.fetch_video_transcript(video, ["es"])

    assert result.is_successful()
    assert "¡Bienvenidos!" in result.full_text
    assert "機械学習の基礎" in result.full_text
    assert "∑(x_i)" in result.full_text


def test_fetch_transient_error_retry_success(mocker):
    """Verify that transient network errors are retried and succeed on subsequent attempt."""
    video = VideoMetadata(
        video_id="vid_transient_1",
        title="Transient Error Video",
        url="https://www.youtube.com/watch?v=vid_transient_1",
        playlist_index=5,
    )

    mock_transcript_obj = mocker.MagicMock()
    mock_transcript_obj.language = "English"
    mock_transcript_obj.language_code = "en"
    mock_transcript_obj.is_generated = False
    mock_fetched = mocker.MagicMock()
    mock_fetched.to_raw_data.return_value = [{"text": "Recovered successfully.", "start": 0.0, "duration": 2.0}]
    mock_transcript_obj.fetch.return_value = mock_fetched

    mock_api = mocker.MagicMock()
    # First 2 calls fail with connection error, 3rd call succeeds
    mock_api.list.side_effect = [
        ConnectionResetError("Connection lost"),
        TimeoutError("Timed out"),
        [mock_transcript_obj],
    ]

    # Use fast backoff for unit tests
    mocker.patch("time.sleep")  # Do not actually sleep in tests
    fetcher = TranscriptFetcher(max_retries=3, initial_backoff=0.01, api=mock_api)
    result = fetcher.fetch_video_transcript(video, ["en"])

    assert result.is_successful()
    assert result.full_text == "Recovered successfully."
    assert mock_api.list.call_count == 3


def test_fetch_transient_error_exhaust_retries(mocker):
    """Verify that persistent transient errors fail gracefully after exceeding max retries."""
    video = VideoMetadata(
        video_id="vid_transient_exhaust",
        title="Exhaust Retries Video",
        url="https://www.youtube.com/watch?v=vid_transient_exhaust",
        playlist_index=6,
    )

    mock_api = mocker.MagicMock()
    mock_api.list.side_effect = ConnectionResetError("Always failing")

    mocker.patch("time.sleep")
    fetcher = TranscriptFetcher(max_retries=2, initial_backoff=0.01, api=mock_api)
    result = fetcher.fetch_video_transcript(video, ["en"])

    assert not result.is_successful()
    assert result.status == "failed"
    assert "Failed after" in result.error_reason
    assert mock_api.list.call_count == 3  # initial + 2 retries


def test_fetch_rate_limit_fails_fast_and_trips_breaker(mocker):
    """A 429 must trip the shared cooldown and fail immediately (no long retry spiral)."""
    from youtube_transcript_api import IpBlocked
    from yt_tool.network import NetworkManager, CircuitBreaker

    video = VideoMetadata(
        video_id="vid_ip_blocked",
        title="Rate Limited Video",
        url="https://www.youtube.com/watch?v=vid_ip_blocked",
        playlist_index=7,
    )

    mock_api = mocker.MagicMock()
    mock_api.list.side_effect = IpBlocked(video.video_id)

    cb = CircuitBreaker(default_cooldown=30.0)
    nm = NetworkManager(circuit_breaker=cb)
    mocker.patch.object(nm, "create_transcript_api", return_value=mock_api)
    mocker.patch.object(CircuitBreaker, "wait_if_tripped", return_value=True)
    mocker.patch("time.sleep")

    fetcher = TranscriptFetcher(network_manager=nm, use_ytdlp_fallback=False)
    result = fetcher.fetch_video_transcript(video, ["en"])

    assert not result.is_successful()
    assert result.error_code == "rate_limited"
    assert "rate-limiting" in result.error_reason.lower()
    # Fail fast: exactly one API attempt, no retry storm.
    assert mock_api.list.call_count == 1
    assert cb.is_tripped


def test_fetch_rate_limit_uses_ytdlp_fallback(mocker):
    """When the API is blocked, a successful yt-dlp fallback still yields a transcript."""
    from youtube_transcript_api import IpBlocked
    from yt_tool.network import NetworkManager

    video = VideoMetadata(
        video_id="vid_fallback",
        title="Fallback Video",
        url="https://www.youtube.com/watch?v=vid_fallback",
        playlist_index=8,
    )

    mock_api = mocker.MagicMock()
    mock_api.list.side_effect = IpBlocked(video.video_id)

    nm = NetworkManager()
    mocker.patch.object(nm, "create_transcript_api", return_value=mock_api)
    mocker.patch("time.sleep")

    fallback_result = VideoTranscript(
        video_id=video.video_id,
        title=video.title,
        url=video.url,
        playlist_index=video.playlist_index,
        status="success",
        language="en (auto-generated)",
        language_code="en",
        is_generated=True,
        segments=[TranscriptSegment(text="From the fallback path.", start=0.0, duration=2.0)],
        full_text="From the fallback path.",
    )

    fetcher = TranscriptFetcher(network_manager=nm)
    mocker.patch.object(fetcher, "fetch_via_ytdlp", return_value=fallback_result)

    result = fetcher.fetch_video_transcript(video, ["en"])
    assert result.is_successful()
    assert result.full_text == "From the fallback path."


def test_fetch_rate_limit_fails_when_fallback_also_blocked(mocker):
    """If both the API and the yt-dlp fallback are rate limited, fail fast as rate_limited."""
    from youtube_transcript_api import IpBlocked
    from yt_tool.network import NetworkManager, CircuitBreaker

    video = VideoMetadata(
        video_id="vid_both_blocked",
        title="Both Blocked",
        url="https://www.youtube.com/watch?v=vid_both_blocked",
        playlist_index=9,
    )

    mock_api = mocker.MagicMock()
    mock_api.list.side_effect = IpBlocked(video.video_id)

    cb = CircuitBreaker(default_cooldown=30.0)
    nm = NetworkManager(circuit_breaker=cb)
    mocker.patch.object(nm, "create_transcript_api", return_value=mock_api)
    mocker.patch("time.sleep")

    fetcher = TranscriptFetcher(network_manager=nm)
    mocker.patch.object(fetcher, "fetch_via_ytdlp", side_effect=lambda v, langs: fetcher._rate_limited_result(v))

    result = fetcher.fetch_video_transcript(video, ["en"])
    assert result.error_code == "rate_limited"
    assert cb.is_tripped


def test_parse_json3_captions():
    """json3 caption payloads are converted to timed segments."""
    from yt_tool.transcript import parse_json3_captions

    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": "Hello "}, {"utf8": "world &amp; all"}]},
            {"tStartMs": 4500, "dDurationMs": 3000, "segs": [{"utf8": "Second line."}]},
            {"tStartMs": 8000, "dDurationMs": 1000, "segs": [{"utf8": "\n"}]},
        ]
    }
    segs = parse_json3_captions(payload)
    assert len(segs) == 2
    assert segs[0].text == "Hello world & all"
    assert segs[0].start == 0.0
    assert segs[1].start == 4.5


def test_parse_vtt_captions():
    """WebVTT documents are parsed into timed segments."""
    from yt_tool.transcript import parse_vtt_captions

    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:04.000\nHello there\n\n"
        "00:00:04.500 --> 00:00:07.000\nGeneral &amp; Kenobi\n"
    )
    segs = parse_vtt_captions(vtt)
    assert len(segs) == 2
    assert segs[0].text == "Hello there"
    assert segs[0].duration == 4.0
    assert segs[1].text == "General & Kenobi"
    assert segs[1].start == 4.5


def test_choose_caption_track_prefers_manual_then_language():
    """Track selection respects manual-vs-auto and language priority."""
    from yt_tool.transcript import choose_caption_track

    manual = {"en": [{"ext": "json3", "url": "m-en"}]}
    auto = {"en": [{"ext": "json3", "url": "a-en"}], "hi": [{"ext": "vtt", "url": "a-hi"}]}

    lang, tracks, gen = choose_caption_track(manual, auto, ["en", "hi"])
    assert lang == "en" and gen is False and tracks[0]["url"] == "m-en"

    lang, tracks, gen = choose_caption_track({}, auto, ["en", "hi"])
    assert lang == "en" and gen is True

    lang, tracks, gen = choose_caption_track({}, auto, ["hi"])
    assert lang == "hi"

    lang, tracks, gen = choose_caption_track({}, {}, ["en"])
    assert lang is None and tracks is None and gen is None


def test_pick_caption_url_prefers_json3():
    """json3 is preferred over VTT/SRV formats."""
    from yt_tool.transcript import pick_caption_url

    url, ext = pick_caption_url([{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "j"}])
    assert (url, ext) == ("j", "json3")

    url, ext = pick_caption_url([{"ext": "vtt", "url": "v"}])
    assert (url, ext) == ("v", "vtt")

    url, ext = pick_caption_url([])
    assert url is None and ext is None



