"""Tests for Markdown/text formatting, filename sanitization, and study handbook output."""

import pytest
from yt_tool.formatter import (
    sanitize_filename,
    format_transcript_body,
    format_video_markdown,
    format_combined_markdown,
    format_combined_plaintext,
)
from yt_tool.models import PlaylistMetadata, VideoTranscript, TranscriptSegment


def test_sanitize_filename_illegal_characters_and_padding():
    """Verify removal of cross-platform illegal chars and correct index zero-padding."""
    title = 'Lesson 1: Intro / Setup * "Dangerous" <Characters> & More? | Test'
    fname = sanitize_filename(title, playlist_index=5, total_videos=137)

    # 137 videos -> 3 digits -> '005 - ...'
    assert fname.startswith("005 - ")
    assert fname.endswith(".md")
    for illegal_char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        assert illegal_char not in fname


def test_sanitize_filename_length_limit():
    """Verify titles longer than 80 chars are cleanly truncated."""
    very_long_title = "A" * 120
    fname = sanitize_filename(very_long_title, playlist_index=1, total_videos=10)
    assert len(fname) < 100
    assert fname.startswith("01 - ")


def test_sanitize_filename_prevents_collision():
    """Verify two videos with identical titles have unique distinct filenames via index prefix."""
    title = "Lecture 1: Overview"
    fname1 = sanitize_filename(title, playlist_index=1, total_videos=20)
    fname2 = sanitize_filename(title, playlist_index=2, total_videos=20)
    assert fname1 != fname2
    assert fname1 == "01 - Lecture 1 - Overview.md"
    assert fname2 == "02 - Lecture 1 - Overview.md"



def test_format_transcript_body_with_and_without_timestamps():
    """Verify toggle behavior for timestamps in transcript text."""
    transcript = VideoTranscript(
        video_id="vid_ts_1",
        title="Timestamp Test",
        url="https://youtube.com/watch?v=vid_ts_1",
        playlist_index=1,
        status="success",
        segments=[
            TranscriptSegment(text="First concept explained.", start=5.0, duration=3.0),
            TranscriptSegment(text="Second concept explained.", start=15.0, duration=4.0),
        ],
        full_text="First concept explained. Second concept explained.",
    )

    with_ts = format_transcript_body(transcript, include_timestamps=True)
    assert "[00:05]" in with_ts
    assert "First concept explained." in with_ts

    without_ts = format_transcript_body(transcript, include_timestamps=False)
    assert "[00:05]" not in without_ts
    assert "First concept explained." in without_ts


def test_format_combined_markdown(sample_playlist: PlaylistMetadata, sample_successful_transcripts):
    """Verify combined markdown handbook contains TOC, video sections, anchors, and failed section."""
    md = format_combined_markdown(
        sample_playlist,
        sample_successful_transcripts,
        include_timestamps=True,
    )

    # Header & metadata
    assert f"# Study Handbook: {sample_playlist.title}" in md
    assert "## Table of Contents" in md
    assert "[01 - Introduction to Machine Learning](#video-01)" in md

    # Individual video sections
    assert "## 01. Introduction to Machine Learning" in md
    assert '<a id="video-01"></a>' in md
    assert "- **YouTube URL:**" in md
    assert "### Transcript" in md
    assert "---" in md

    # Failed section
    assert "## Incomplete / Unavailable Videos" in md
    assert "vid_003_third" in md
    assert "Captions are disabled for this video." in md


def test_format_combined_plaintext(sample_playlist: PlaylistMetadata, sample_successful_transcripts):
    """Verify combined plain-text file structure and failed video reporting."""
    txt = format_combined_plaintext(
        sample_playlist,
        sample_successful_transcripts,
        include_timestamps=False,
    )

    assert f"STUDY NOTES: {sample_playlist.title}" in txt
    assert "VIDEO 01: Introduction to Machine Learning" in txt
    assert "URL: https://www.youtube.com/watch?v=vid_001_first" in txt
    assert "UNAVAILABLE / FAILED VIDEOS" in txt
    assert "Captions are disabled for this video." in txt
