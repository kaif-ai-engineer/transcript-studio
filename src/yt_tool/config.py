"""Configuration settings and constants for the YouTube Playlist Transcript tool."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AppConfig:
    """Application-wide configuration parameters."""
    
    # Filesystem paths
    cache_dir: Path = field(default_factory=lambda: Path("data/cache"))
    export_dir: Path = field(default_factory=lambda: Path("data/exports"))
    cookies_path: Path = field(default_factory=lambda: Path("data/cookies.txt"))
    
    # Concurrency and rate limiting
    default_concurrency: int = 2
    max_concurrency: int = 5
    min_concurrency: int = 1
    request_delay_seconds: float = 0.8
    circuit_breaker_cooldown_seconds: float = 25.0
    
    # Retry policy
    max_retries: int = 5
    initial_backoff_seconds: float = 2.5
    backoff_factor: float = 1.8

    # Rate-limit handling. When YouTube returns HTTP 429 it is tied to the
    # requesting IP, so retrying harder does not help — we fail fast and pause.
    rate_limit_attempts: int = 2          # API attempt + yt-dlp fallback attempt
    rate_limit_cooldown_seconds: float = 30.0
    abort_job_after_rate_limits: int = 3  # consecutive rate-limited videos before stopping
    
    # Supported default language preferences: English 1st, Hindi 2nd
    default_languages: List[str] = field(default_factory=lambda: ["en", "hi"])
    allow_auto_translation: bool = True
    
    # Timeouts
    metadata_timeout_seconds: int = 45
    transcript_timeout_seconds: int = 30


# Global default configuration instance
DEFAULT_CONFIG = AppConfig()
