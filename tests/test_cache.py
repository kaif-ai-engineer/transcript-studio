"""Tests for local disk caching, resume support, atomic writes, and security boundaries."""

import pytest
from pathlib import Path
from yt_tool.cache import TranscriptCache, CacheSecurityError
from yt_tool.models import PlaylistMetadata, VideoMetadata, VideoTranscript, TranscriptSegment


def test_cache_save_and_load_playlist(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Verify persisting and reading playlist metadata."""
    mock_cache.save_playlist(sample_playlist)

    loaded = mock_cache.load_playlist(sample_playlist.playlist_id)
    assert loaded is not None
    assert loaded.playlist_id == sample_playlist.playlist_id
    assert loaded.title == sample_playlist.title
    assert loaded.total_count == 3
    assert len(loaded.videos) == 3


def test_cache_save_and_load_transcript(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Verify persisting and reading individual video transcripts."""
    transcript = VideoTranscript(
        video_id="vid_cache_test_1",
        title="Caching Mechanisms",
        url="https://www.youtube.com/watch?v=vid_cache_test_1",
        playlist_index=1,
        status="success",
        language="English",
        language_code="en",
        is_generated=False,
        segments=[TranscriptSegment(text="Atomic write test.", start=0.0, duration=2.0)],
        full_text="Atomic write test.",
    )

    mock_cache.save_transcript(sample_playlist.playlist_id, transcript)

    # Check existence and resume helper
    assert mock_cache.has_successful_transcript(sample_playlist.playlist_id, "vid_cache_test_1")

    # Load back
    loaded = mock_cache.load_transcript(sample_playlist.playlist_id, "vid_cache_test_1")
    assert loaded is not None
    assert loaded.video_id == "vid_cache_test_1"
    assert loaded.status == "success"
    assert loaded.full_text == "Atomic write test."


def test_cache_resume_support(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Verify failed or missing transcripts return False for has_successful_transcript."""
    failed_transcript = VideoTranscript(
        video_id="vid_failed_1",
        title="Failed Video",
        url="https://www.youtube.com/watch?v=vid_failed_1",
        playlist_index=2,
        status="failed",
        error_reason="No captions found",
    )
    mock_cache.save_transcript(sample_playlist.playlist_id, failed_transcript)

    assert not mock_cache.has_successful_transcript(sample_playlist.playlist_id, "vid_failed_1")
    assert not mock_cache.has_successful_transcript(sample_playlist.playlist_id, "nonexistent_vid")


def test_cache_path_traversal_protection(mock_cache: TranscriptCache):
    """Verify security prevention against path traversal attempts."""
    malicious_ids = [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "playlist/../../secrets",
        "id with spaces and $pecial!chars",
    ]
    for bad_id in malicious_ids:
        with pytest.raises(CacheSecurityError):
            mock_cache.load_playlist(bad_id)


def test_cache_clear(mock_cache: TranscriptCache, sample_playlist: PlaylistMetadata):
    """Verify clearing cache for specific playlist and globally."""
    mock_cache.save_playlist(sample_playlist)
    assert sample_playlist.playlist_id in mock_cache.list_cached_playlists()

    # Clear specific
    mock_cache.clear_cache(sample_playlist.playlist_id)
    assert mock_cache.load_playlist(sample_playlist.playlist_id) is None
    assert sample_playlist.playlist_id not in mock_cache.list_cached_playlists()
