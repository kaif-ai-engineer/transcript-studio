"""Pytest test fixtures and mocks."""

import pytest
from pathlib import Path
from typing import List
from yt_tool.models import (
    VideoMetadata,
    PlaylistMetadata,
    TranscriptSegment,
    VideoTranscript,
)
from yt_tool.cache import TranscriptCache


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Fixture providing an isolated temporary cache directory."""
    cache_path = tmp_path / "test_cache"
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


@pytest.fixture
def mock_cache(temp_cache_dir: Path) -> TranscriptCache:
    """Fixture providing a TranscriptCache instance with temp directory."""
    return TranscriptCache(root_dir=temp_cache_dir)


@pytest.fixture
def sample_playlist() -> PlaylistMetadata:
    """Fixture providing a 3-video playlist metadata."""
    videos = [
        VideoMetadata(
            video_id="vid_001_first",
            title="Introduction to Machine Learning",
            url="https://www.youtube.com/watch?v=vid_001_first",
            playlist_index=1,
            duration=300,
        ),
        VideoMetadata(
            video_id="vid_002_second",
            title="Supervised Learning & Regression (Part 1/2: Basics)",
            url="https://www.youtube.com/watch?v=vid_002_second",
            playlist_index=2,
            duration=650,
        ),
        VideoMetadata(
            video_id="vid_003_third",
            title="Neural Networks & Deep Learning: Theory & Prácticas 🚀",
            url="https://www.youtube.com/watch?v=vid_003_third",
            playlist_index=3,
            duration=1200,
        ),
    ]
    return PlaylistMetadata(
        playlist_id="PLtest_sample_12345",
        title="Comprehensive Machine Learning Course",
        url="https://www.youtube.com/playlist?list=PLtest_sample_12345",
        total_count=3,
        videos=videos,
    )


@pytest.fixture
def sample_successful_transcripts(sample_playlist: PlaylistMetadata) -> List[VideoTranscript]:
    """Fixture providing transcripts for the sample playlist."""
    return [
        VideoTranscript(
            video_id="vid_001_first",
            title=sample_playlist.videos[0].title,
            url=sample_playlist.videos[0].url,
            playlist_index=1,
            status="success",
            language="English",
            language_code="en",
            is_generated=False,
            segments=[
                TranscriptSegment(text="Hello world and welcome to the course.", start=0.0, duration=4.2),
                TranscriptSegment(text="Today we cover linear models &amp; metrics.", start=4.5, duration=3.8),
            ],
            full_text="Hello world and welcome to the course. Today we cover linear models & metrics.",
            retrieved_at="2026-10-11T12:00:00Z",
        ),
        VideoTranscript(
            video_id="vid_002_second",
            title=sample_playlist.videos[1].title,
            url=sample_playlist.videos[1].url,
            playlist_index=2,
            status="success",
            language="English (auto-generated)",
            language_code="en",
            is_generated=True,
            segments=[
                TranscriptSegment(text="In this lecture we examine gradient descent.", start=10.0, duration=5.0),
                TranscriptSegment(text="Cost function J(theta) converges smoothly.", start=16.0, duration=4.0),
            ],
            full_text="In this lecture we examine gradient descent. Cost function J(theta) converges smoothly.",
            retrieved_at="2026-10-11T12:05:00Z",
        ),
        VideoTranscript(
            video_id="vid_003_third",
            title=sample_playlist.videos[2].title,
            url=sample_playlist.videos[2].url,
            playlist_index=3,
            status="failed",
            error_reason="Captions are disabled for this video.",
            retrieved_at="2026-10-11T12:10:00Z",
        ),
    ]
