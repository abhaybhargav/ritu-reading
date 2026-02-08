# Ritu's ReadAlong Tutor

A web application to help children (ages 5-9) improve reading accuracy, fluency, and confidence through leveled stories, real-time read-aloud sessions with word-by-word highlighting, and AI-powered coaching.

## Tech Stack

- **Backend:** Python FastAPI + Jinja2 + SQLite (SQLAlchemy async)
- **Frontend:** TailwindCSS + HTMX + Alpine.js
- **AI:** OpenAI (story & image generation), ElevenLabs (STT & TTS)
- **Package manager:** uv

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your API keys

# Run the app
uv run python main.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key for story & image generation |
| `ELEVENLABS_API_KEY` | ElevenLabs API key for STT & TTS |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice ID for coaching (default: Sarah) |
| `READING_TUTOR_DB_URL` | Database URL (default: SQLite in `data/`) |

## Features

- **Story Generation** – AI-generated leveled stories mapped to Ladybird Readers levels (1-6)
- **Illustrations** – Auto-generated kid-friendly storybook images via OpenAI
- **Read Aloud** – Real-time mic recording with WebSocket streaming
- **Word Highlighting** – Live word-by-word alignment as the child reads
- **Coaching Voice** – TTS pronunciation help when the child struggles
- **Scoring** – 0-100 score with accuracy, fluency, and independence sub-scores
- **Adaptive Leveling** – Automatic progression based on performance trends
- **Parent Dashboard** – Score trends, problem words, level management

## Project Structure

```
reading-tutor/
├── main.py                    # FastAPI app entry point
├── app/
│   ├── config.py              # Settings & environment config
│   ├── database.py            # Async SQLAlchemy setup
│   ├── models.py              # ORM models
│   ├── seed.py                # Default user seeding
│   ├── services/              # Business logic
│   │   ├── story_generator.py # OpenAI story generation
│   │   ├── image_generator.py # OpenAI image generation
│   │   ├── stt.py             # ElevenLabs speech-to-text
│   │   ├── tts.py             # ElevenLabs text-to-speech + cache
│   │   ├── word_alignment.py  # Story-transcript word alignment
│   │   ├── scoring.py         # Score computation
│   │   └── progression.py     # Adaptive level progression
│   ├── routes/                # FastAPI route handlers
│   │   ├── pages.py           # HTML page routes
│   │   ├── stories.py         # Story API (HTMX partials)
│   │   ├── attempts.py        # Reading session + WebSocket
│   │   └── parent.py          # Parent dashboard & management
│   ├── templates/             # Jinja2 templates
│   └── static/                # CSS & JS assets
├── data/                      # SQLite DB & generated images
└── tts_cache/                 # Cached TTS audio files
```
