# 🎓 Transcript Studio

[![CI](https://github.com/kaif-ai-engineer/transcript-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/kaif-ai-engineer/transcript-studio/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Turn a YouTube playlist into a clean, downloadable **study handbook**.

Paste a playlist link, pick your languages, and get organised transcripts you can export as
Markdown, plain text, or a full ZIP bundle — with a local, crash-resilient cache so re-runs
never re-download anything.

Built for students and researchers working through long educational playlists (100+ videos,
multi-hour lectures) without manual note-taking or paid APIs.

---

## ✨ Features

- **Zero paid dependencies** — only `yt-dlp` + `youtube-transcript-api`. No OpenAI keys, no speech APIs, no subscriptions.
- **Fast playlist loading** — flat metadata extraction completes in seconds without downloading any media.
- **Smart caption selection** — prefers manual captions, falls back to auto-generated, with auto-translation to your preferred language.
- **Study handbook export** — Markdown with a Table of Contents, plus plain text, individual files, a `manifest.json`, a `failed_videos.csv`, and a one-click ZIP bundle.
- **Crash-resilient cache** — every transcript is written atomically to `data/cache/<playlist>/`. Interrupted runs resume seamlessly.
- **Polite by design** — bounded concurrency, inter-request delays, exponential backoff, and a shared circuit breaker that pauses *all* workers when YouTube says slow down.
- **Retry just the failures** — re-attempt only the videos that errored.
- **Optional official API** — plug in a free [YouTube Data API v3](https://developers.google.com/youtube/v3/docs) key to fetch playlist metadata through the official API.

---

## 🚀 Try it online

Host it yourself on **Streamlit Community Cloud** (free, no card):

1. Fork this repository.
2. Go to [streamlit.io/cloud](https://share.streamlit.io) → **Create app** → pick your fork, `app.py` as the main file.
3. Deploy. You get a global URL — users only ever see the web app.

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://share.streamlit.io/kaif-ai-engineer/transcript-studio/main/app.py)

> **Note:** Streamlit Community Cloud apps sleep after a few minutes of inactivity. The first
> visit after idle takes ~30s to wake. Because the filesystem is ephemeral there, re-open the
> same playlist to reload your cache.

---

## 💻 Run locally

### Option 1 — `uv` (recommended)

```bash
uv run streamlit run app.py
```

### Option 2 — `pip`

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501

---

## 📖 How to use

1. **Paste a playlist URL** (or a raw playlist ID) and press **Fetch playlist**.
2. **Choose videos** — everything is selected by default; deselect what you don't need.
3. **Adjust settings** in the sidebar:
   - Caption language priority (English/Hindi/auto/custom).
   - Parallel workers — keep **1–2** for reliability.
   - Request delay — a short pause avoids rate limits.
   - Cookies (optional but strongly recommended for large playlists).
4. **Get transcripts** and watch live progress. You can stop at any time.
5. **Export** — download the `.md` handbook, `.txt`, or the full `.zip`.

---

## ⚙️ Configuration

| Setting | Where | Notes |
| --- | --- | --- |
| Caption language | Sidebar | Priority order for transcript selection. |
| Workers / delay | Sidebar | Lower = more polite and more reliable. |
| Cookie string | Sidebar → Authentication | Your YouTube `Cookie:` header. Stays in the session only. |
| API key | Sidebar → Metadata API key | Optional official YouTube Data API v3 key. |
| `YOUTUBE_API_KEY` | Environment / `.streamlit/secrets.toml` | Same key, applied automatically. |

Copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` to set the key locally.

---

## 🧪 Tests

```bash
uv run pytest -v
```

All 41 tests run fully offline with mocked YouTube responses — no live network calls.

---

## 🏗️ Architecture

```
yt-tool/
├── app.py                     # Streamlit UI
├── requirements.txt           # Runtime deps (Streamlit Community Cloud)
├── .streamlit/config.toml     # Theme + server config
├── .github/workflows/ci.yml   # CI: pytest on 3.11/3.12/3.13
├── src/yt_tool/
│   ├── config.py              # Timeouts, backoff, concurrency defaults
│   ├── models.py              # VideoMetadata / PlaylistMetadata / VideoTranscript
│   ├── playlist.py            # URL validation + yt-dlp or official Data API metadata
│   ├── transcript.py          # Caption selection, language fallback, retries
│   ├── network.py             # Polite sessions, cookies, circuit breaker
│   ├── cache.py               # Atomic JSON persistence + resume
│   ├── processor.py           # Bounded concurrency, cancellation, progress
│   ├── formatter.py           # Markdown/TXT, TOC, filename sanitizer
│   └── exporter.py            # ZIP bundle, manifest, CSV report
└── tests/                     # Offline unit tests
```

---

## 🛡️ Rate limits & troubleshooting

YouTube throttles anonymous IPs that request captions too fast. The tool mitigates this by:

- Defaulting to **2 workers** with a **0.8s** delay between requests.
- Tripping a **shared circuit breaker** that pauses *every* worker when a 429 arrives, then resuming automatically.
- **Exponential backoff** on transient errors.
- **Disk caching** — already-fetched videos never hit the network again.

If you still hit rate limits on a very large playlist:

1. Paste your **browser cookies** (Sidebar → Authentication). This is the single most effective fix.
2. Drop workers to **1** and increase the delay.
3. Wait a few minutes — retry only fetches the videos that are still missing.

**Note:** proxy rotation and IP-hopping were deliberately removed. They bypass YouTube's
access controls (violating its Terms of Service), are unreliable in practice, and add
unnecessary legal risk. See below.

---

## ⚖️ Legal & Acceptable Use

This project is provided **as-is**, without warranty, under the [MIT License](LICENSE).

- **Only public captions are retrieved.** No video or audio files are ever downloaded, copied, or re-hosted.
- **Your own metadata path.** You may supply your own free YouTube Data API v3 key for fully official playlist metadata.
- **You are responsible for how you use it.** Respect YouTube's [Terms of Service](https://www.youtube.com/t/terms), creators' rights, copyright, and any playlist's license. Do not redistribute transcripts as your own work.
- **No circumvention.** Do not use this tool to bypass paywalls, age gates, region blocks, or rate limits.
- **Not affiliated with or endorsed by YouTube or Google.**

If you plan to publish transcripts publicly or commercially, get permission from the rights
holders first.

---

## 📄 License

MIT © kaif-ai-engineer — see [LICENSE](LICENSE).
