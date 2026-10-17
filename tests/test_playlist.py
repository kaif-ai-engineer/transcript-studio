"""Tests for YouTube playlist URL parsing, validation, and metadata extraction."""

import pytest
from yt_tool.playlist import (
    extract_playlist_id,
    fetch_playlist_metadata,
    InvalidPlaylistURLError,
    PlaylistNotFoundError,
    PlaylistExtractionError,
)
import yt_dlp


def test_extract_playlist_id_valid_formats():
    """Verify various valid YouTube playlist URL formats and raw IDs."""
    test_cases = [
        ("https://www.youtube.com/playlist?list=PL1234567890abcdef", "PL1234567890abcdef"),
        ("http://youtube.com/playlist?list=PL1234567890abcdef", "PL1234567890abcdef"),
        ("https://www.youtube.com/watch?v=vid12345678&list=PL1234567890abcdef", "PL1234567890abcdef"),
        ("https://youtu.be/vid12345678?list=PL1234567890abcdef", "PL1234567890abcdef"),
        ("https://www.youtube.com/playlist?list=PL1234567890abcdef&si=tracking123", "PL1234567890abcdef"),
        ("PL1234567890abcdef", "PL1234567890abcdef"),
        ("OLAK5uy_m-d7tH83gP1h8z2l4y3", "OLAK5uy_m-d7tH83gP1h8z2l4y3"),
    ]
    for input_val, expected_id in test_cases:
        assert extract_playlist_id(input_val) == expected_id


def test_extract_playlist_id_invalid_formats():
    """Verify that invalid inputs, malicious protocols, and non-playlist URLs raise InvalidPlaylistURLError."""
    invalid_cases = [
        "",
        "   ",
        "https://www.youtube.com/watch?v=only_a_video_without_list",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://example.com/not-youtube",
        "ftp://youtube.com/playlist?list=PL123",
        "short",  # too short for playlist id pattern
        "PL!@#$%^&*()_+",  # illegal characters
    ]
    for bad_input in invalid_cases:
        with pytest.raises(InvalidPlaylistURLError):
            extract_playlist_id(bad_input)


def test_fetch_playlist_metadata_preserves_order(mocker):
    """Verify metadata extraction preserves exact playlist sequence and 1-based indexing."""
    mock_entries = [
        {"id": "vid_first_11", "title": "First Lesson", "duration": 120},
        {"id": "vid_second_2", "title": "Second Lesson", "duration": 240},
        {"id": "vid_third__3", "title": "Third Lesson", "duration": 360},
    ]

    mock_ydl_instance = mocker.MagicMock()
    mock_ydl_instance.__enter__.return_value = mock_ydl_instance
    mock_ydl_instance.extract_info.return_value = {
        "title": "Educational Physics Series",
        "entries": mock_entries,
    }
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance)

    playlist = fetch_playlist_metadata("https://www.youtube.com/playlist?list=PLvalidplaylist123")

    assert playlist.playlist_id == "PLvalidplaylist123"
    assert playlist.title == "Educational Physics Series"
    assert playlist.total_count == 3
    assert len(playlist.videos) == 3

    # Check playlist order preservation
    assert playlist.videos[0].playlist_index == 1
    assert playlist.videos[0].video_id == "vid_first_11"
    assert playlist.videos[0].title == "First Lesson"

    assert playlist.videos[1].playlist_index == 2
    assert playlist.videos[1].video_id == "vid_second_2"

    assert playlist.videos[2].playlist_index == 3
    assert playlist.videos[2].video_id == "vid_third__3"


def test_fetch_playlist_metadata_not_found(mocker):
    """Verify handling when playlist does not exist or is private."""
    mock_ydl = mocker.MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("Private playlist")
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)

    with pytest.raises(PlaylistNotFoundError):
        fetch_playlist_metadata("PLnonexistent123")


def test_fetch_playlist_metadata_network_error(mocker):
    """Verify handling when generic network extraction error occurs."""
    mock_ydl = mocker.MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = Exception("Network timeout")
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)

    with pytest.raises(PlaylistExtractionError):
        fetch_playlist_metadata("PLvalidplaylist123")

