"""Tests for CircuitBreaker and the polite NetworkManager (no proxies)."""

import threading
import time

from youtube_transcript_api import YouTubeTranscriptApi

from yt_tool.network import CircuitBreaker, NetworkManager


def test_circuit_breaker_trip_and_wait():
    """Verify circuit breaker trips and reports is_tripped correctly."""
    cb = CircuitBreaker(default_cooldown=0.2)
    assert not cb.is_tripped

    cb.trip(cooldown_seconds=0.1, reason="Test trip")
    assert cb.is_tripped
    assert cb.reason == "Test trip"

    time.sleep(0.12)
    assert not cb.is_tripped


def test_circuit_breaker_never_shortens_active_cooldown():
    """A second trip must not shorten an in-flight cooldown."""
    cb = CircuitBreaker(default_cooldown=10.0)
    cb.trip(cooldown_seconds=5.0)
    cb.trip(cooldown_seconds=0.01)
    # Still tripped because the longer cooldown wins.
    assert cb.is_tripped


def test_circuit_breaker_stop_event():
    """Verify circuit breaker immediately unblocks if stop_event is signaled."""
    cb = CircuitBreaker(default_cooldown=10.0)
    cb.trip(cooldown_seconds=10.0)

    stop_event = threading.Event()
    stop_event.set()

    resumed = cb.wait_if_tripped(stop_event=stop_event)
    assert not resumed


def test_network_manager_session_headers_and_cookies():
    """Verify session creation adds browser headers, cookies, and retries."""
    nm = NetworkManager(cookie_string="VISITOR_INFO1_LIVE=test_cookie; LOGIN_INFO=test_login;")
    session = nm.build_session()

    assert "User-Agent" in session.headers
    assert session.cookies.get("VISITOR_INFO1_LIVE") == "test_cookie"
    assert session.cookies.get("LOGIN_INFO") == "test_login"
    # Direct connections always get a retry adapter mounted.
    assert session.get_adapter("https://").max_retries.total == 3


def test_network_manager_create_transcript_api():
    """Verify a YouTubeTranscriptApi client is produced without any proxy."""
    nm = NetworkManager()
    api = nm.create_transcript_api()
    assert isinstance(api, YouTubeTranscriptApi)
