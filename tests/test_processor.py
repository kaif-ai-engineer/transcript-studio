"""Tests for job processing, concurrency, retry behavior, duplicate prevention, and cancellation."""

import threading
import pytest
from unittest.mock import MagicMock

from yt_tool.models import PlaylistMetadata, VideoMetadata, VideoTranscript, TranscriptSegment
from yt_tool.processor import JobManager
from yt_tool.transcript import TranscriptFetcher
from yt_tool.cache import TranscriptCache


def test_processor_full_run_with_caching(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Verify processing all videos, saving to cache, and progress reporting."""
    mock_fetcher = MagicMock(spec=TranscriptFetcher)
    
    def side_effect(video, preferred_languages=None, stop_event=None):
        return VideoTranscript(
            video_id=video.video_id,
            title=video.title,
            url=video.url,
            playlist_index=video.playlist_index,
            status="success",
            language="English",
            language_code="en",
            segments=[TranscriptSegment(text="Sample speech.", start=0.0, duration=2.0)],
            full_text="Sample speech.",
        )
    mock_fetcher.fetch_video_transcript.side_effect = side_effect

    manager = JobManager(fetcher=mock_fetcher, cache=mock_cache)
    progress_updates = []

    def callback(prog, item):
        progress_updates.append((prog.completed, prog.successful, prog.failed))

    results = manager.run_job(
        playlist=sample_playlist,
        concurrency=2,
        request_delay=0.01,
        progress_callback=callback,
    )

    assert len(results) == 3
    assert all(r.is_successful() for r in results)
    assert mock_fetcher.fetch_video_transcript.call_count == 3
    assert len(progress_updates) > 0


def test_processor_avoids_repeated_requests_for_cached_videos(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Requirement: Avoid repeated requests for videos whose transcripts have already been successfully retrieved."""
    # Pre-populate cache with video 1
    existing_t1 = VideoTranscript(
        video_id=sample_playlist.videos[0].video_id,
        title=sample_playlist.videos[0].title,
        url=sample_playlist.videos[0].url,
        playlist_index=1,
        status="success",
        language="English",
        segments=[TranscriptSegment(text="Precached text.", start=0.0, duration=1.0)],
        full_text="Precached text.",
    )
    mock_cache.save_transcript(sample_playlist.playlist_id, existing_t1)

    mock_fetcher = MagicMock(spec=TranscriptFetcher)
    mock_fetcher.fetch_video_transcript.return_value = VideoTranscript(
        video_id="dummy",
        title="dummy",
        url="dummy",
        playlist_index=2,
        status="success",
        segments=[TranscriptSegment(text="New text.", start=0.0, duration=1.0)],
        full_text="New text.",
    )

    manager = JobManager(fetcher=mock_fetcher, cache=mock_cache)
    results = manager.run_job(sample_playlist, concurrency=2, request_delay=0.01)

    # Should only call fetcher for videos 2 and 3! (Video 1 was already cached)
    assert mock_fetcher.fetch_video_transcript.call_count == 2
    called_vids = [call.args[0].video_id for call in mock_fetcher.fetch_video_transcript.call_args_list]
    assert sample_playlist.videos[0].video_id not in called_vids


def test_processor_partial_success_continues_on_failure(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Requirement: One unavailable video does not stop the remaining videos."""
    mock_fetcher = MagicMock(spec=TranscriptFetcher)

    def side_effect(video, preferred_languages=None, stop_event=None):
        if video.video_id == sample_playlist.videos[1].video_id:
            # Video 2 fails
            return VideoTranscript(
                video_id=video.video_id,
                title=video.title,
                url=video.url,
                playlist_index=video.playlist_index,
                status="failed",
                error_reason="Captions disabled",
            )
        return VideoTranscript(
            video_id=video.video_id,
            title=video.title,
            url=video.url,
            playlist_index=video.playlist_index,
            status="success",
            language="English",
            segments=[TranscriptSegment(text="Good speech.", start=0.0, duration=1.0)],
            full_text="Good speech.",
        )
    mock_fetcher.fetch_video_transcript.side_effect = side_effect

    manager = JobManager(fetcher=mock_fetcher, cache=mock_cache)
    results = manager.run_job(sample_playlist, concurrency=2, request_delay=0.01)

    assert len(results) == 3
    assert results[0].is_successful()
    assert not results[1].is_successful()
    assert results[1].status == "failed"
    assert results[2].is_successful()


def test_processor_graceful_cancellation(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Requirement: Allow user to stop processing gracefully."""
    stop_event = threading.Event()
    stop_event.set()  # Stop immediately

    mock_fetcher = MagicMock(spec=TranscriptFetcher)
    manager = JobManager(fetcher=mock_fetcher, cache=mock_cache)

    results = manager.run_job(
        playlist=sample_playlist,
        stop_event=stop_event,
        concurrency=1,
        request_delay=0.01,
    )

    # No network requests should have been made
    assert mock_fetcher.fetch_video_transcript.call_count == 0
    assert len(results) == 3


def test_processor_aborts_job_after_repeated_rate_limits(mock_cache: TranscriptCache):
    """Requirement: Stop the job once YouTube persistently rate-limits, instead of grinding."""
    videos = [
        VideoMetadata(
            video_id=f"rl_{i}",
            title=f"Video {i}",
            url=f"https://www.youtube.com/watch?v=rl_{i}",
            playlist_index=i,
        )
        for i in range(1, 7)  # 6 videos, threshold is 3
    ]
    playlist = PlaylistMetadata(
        playlist_id="PLratelimit",
        title="Rate Limited Playlist",
        url="https://www.youtube.com/playlist?list=PLratelimit",
        total_count=6,
        videos=videos,
    )

    mock_fetcher = MagicMock(spec=TranscriptFetcher)

    def side_effect(video, preferred_languages=None, stop_event=None):
        return VideoTranscript(
            video_id=video.video_id,
            title=video.title,
            url=video.url,
            playlist_index=video.playlist_index,
            status="failed",
            error_code="rate_limited",
            error_reason="Rate limited",
        )

    mock_fetcher.fetch_video_transcript.side_effect = side_effect
    manager = JobManager(fetcher=mock_fetcher, cache=mock_cache)

    seen = {}

    def callback(prog, item):
        seen["rate_limited"] = prog.rate_limited

    results = manager.run_job(
        playlist=playlist,
        concurrency=1,
        request_delay=0.01,
        progress_callback=callback,
    )

    assert seen.get("rate_limited") is True
    # Job should stop early rather than process all six.
    assert mock_fetcher.fetch_video_transcript.call_count < 6
    assert len(results) == 6  # results always align with the full playlist

