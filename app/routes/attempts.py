"""Reading attempt APIs including WebSocket relay to Sarvam Saarika STT."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models import (
    ReadingAttempt,
    Story,
    User,
    WordEvent,
)
from app.services.scoring import compute_score
from app.services.progression import evaluate_progression
from app.services.tts import build_coaching_text, synthesize_speech
from app.services.word_alignment import align_transcript_to_story

logger = logging.getLogger(__name__)

router = APIRouter()


# ---- Start / Finish ----


@router.post("/attempts/start")
async def start_attempt(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Start a new reading attempt. Body: {story_id: int}."""
    body = await request.json()
    story_id = body.get("story_id")

    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if not story:
        return JSONResponse({"error": "Story not found"}, status_code=404)

    # Get child
    result = await db.execute(
        select(User).where(User.role == "child_user").limit(1)
    )
    child = result.scalar_one_or_none()
    if not child:
        return JSONResponse({"error": "No child user"}, status_code=400)

    attempt = ReadingAttempt(
        user_id=child.id,
        story_id=story_id,
    )
    db.add(attempt)
    await db.commit()

    return JSONResponse({"attempt_id": attempt.id, "story_id": story_id})


@router.post("/attempts/{attempt_id}/finish")
async def finish_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Finish a reading attempt – compute scores and update progression."""
    result = await db.execute(
        select(ReadingAttempt).where(ReadingAttempt.id == attempt_id)
    )
    attempt = result.scalar_one_or_none()
    if not attempt:
        return JSONResponse({"error": "Attempt not found"}, status_code=404)

    # Get story
    result = await db.execute(select(Story).where(Story.id == attempt.story_id))
    story = result.scalar_one_or_none()

    # Get word events
    result = await db.execute(
        select(WordEvent).where(WordEvent.attempt_id == attempt_id)
    )
    events = result.scalars().all()

    event_dicts = [
        {
            "event_type": e.event_type,
            "expected_word": e.expected_word,
            "recognized_word": e.recognized_word,
            "word_index": e.word_index,
        }
        for e in events
    ]

    # Duration
    now = dt.datetime.utcnow()
    duration = (now - attempt.started_at).total_seconds() if attempt.started_at else 60

    score = compute_score(
        word_events=event_dicts,
        total_words=story.word_count if story else 0,
        duration_seconds=duration,
        interventions=attempt.interventions_count,
        skips=attempt.skips_count,
    )

    attempt.ended_at = now
    attempt.score_total = score["total"]
    attempt.score_accuracy = score["accuracy"]
    attempt.score_fluency = score["fluency"]
    attempt.score_independence = score["independence"]
    attempt.wpm_estimate = score["wpm"]
    attempt.summary_json = json.dumps(score["summary"])
    await db.commit()

    # Evaluate progression
    progression = await evaluate_progression(db, attempt.user_id)

    return JSONResponse({
        "score": score,
        "progression": progression,
        "attempt_id": attempt_id,
    })


@router.post("/attempts/{attempt_id}/events")
async def batch_word_events(
    attempt_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Batch insert word events."""
    body = await request.json()
    events = body.get("events", [])

    result = await db.execute(
        select(ReadingAttempt).where(ReadingAttempt.id == attempt_id)
    )
    attempt = result.scalar_one_or_none()
    if not attempt:
        return JSONResponse({"error": "Attempt not found"}, status_code=404)

    for evt in events:
        we = WordEvent(
            attempt_id=attempt_id,
            story_id=attempt.story_id,
            word_index=evt.get("word_index", 0),
            expected_word=evt.get("expected_word", ""),
            recognized_word=evt.get("recognized_word"),
            event_type=evt.get("event_type", "correct"),
            severity=evt.get("severity"),
            timestamp_ms=evt.get("timestamp_ms"),
        )
        db.add(we)

    # Update counts
    skips = sum(1 for e in events if e.get("event_type") == "skip")
    hints = sum(1 for e in events if e.get("event_type") == "hint")
    attempt.skips_count = (attempt.skips_count or 0) + skips
    attempt.interventions_count = (attempt.interventions_count or 0) + hints

    await db.commit()
    return JSONResponse({"saved": len(events)})


# ---- On-demand word pronunciation ----


@router.post("/attempts/{attempt_id}/pronounce")
async def pronounce_word(
    attempt_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Pronounce a word: returns JSON with audio URL, phonetic text, and
    an optional narrated phonetic explanation audio URL.

    Body: {word: str}.
    Returns: {audio_url: str, phonetic: str|null, phonetic_audio_url: str|null}
    """
    from app.services.phonetics import get_phonetic_breakdown

    body = await request.json()
    word = body.get("word", "").strip()
    if not word:
        return JSONResponse({"error": "No word provided"}, status_code=400)

    logger.info("Pronunciation lookup: attempt=%s word=%r", attempt_id, word)

    # ---- Generate pronunciation audio ----
    pronunciation_text = f"{word}."
    phonetic = None
    phonetic_audio_url = None

    try:
        audio_path = await synthesize_speech(pronunciation_text)
        # Generate phonetic breakdown for tricky words
        phonetic = await get_phonetic_breakdown(word)

        # If there's a phonetic explanation, narrate it too
        if phonetic:
            narration_text = f'The word is "{word}". {phonetic}'
            phonetic_audio_path = await synthesize_speech(narration_text)
            phonetic_audio_url = f"/api/tts-cache/{phonetic_audio_path.name}"

    except Exception as e:
        logger.exception("Pronunciation TTS failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Return JSON with URLs to the cached audio files + phonetic info
    audio_filename = audio_path.name
    return JSONResponse({
        "audio_url": f"/api/tts-cache/{audio_filename}",
        "phonetic": phonetic,
        "phonetic_audio_url": phonetic_audio_url,
    })


@router.get("/tts-cache/{filename}")
async def serve_tts_cache(filename: str):
    """Serve a cached TTS audio file."""
    from app.config import TTS_CACHE_DIR

    # Sanitize filename to prevent directory traversal
    safe_name = Path(filename).name
    audio_path = TTS_CACHE_DIR / safe_name
    if not audio_path.exists():
        return JSONResponse({"error": "Audio not found"}, status_code=404)
    return FileResponse(str(audio_path), media_type="audio/mpeg")


@router.post("/attempts/{attempt_id}/coach")
async def coach_word(
    attempt_id: int,
    request: Request,
):
    """Generate coaching audio for a problem word. Body: {word: str}."""
    body = await request.json()
    word = body.get("word", "")

    coaching_text = build_coaching_text(word)

    try:
        audio_path = await synthesize_speech(coaching_text)
        return FileResponse(str(audio_path), media_type="audio/mpeg")
    except Exception as e:
        logger.exception("TTS coaching failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---- WebSocket for real-time reading session (Sarvam Saarika STT relay) ----


@router.websocket("/ws/attempts/{attempt_id}")
async def reading_session_ws(websocket: WebSocket, attempt_id: int):
    """
    WebSocket relay between the browser and Sarvam Saarika v2.5 for streaming STT.

    Client sends:
      - binary frames: raw PCM16 audio at 16 kHz mono
      - text frames:  JSON commands {"type": "stop"} etc.

    Server sends:
      - JSON: {"type": "alignment", "events": [...], "current_index": int, ...}
      - JSON: {"type": "complete", "message": ...}
      - JSON: {"type": "error", "message": ...}
    """
    import websockets
    from urllib.parse import urlencode

    await websocket.accept()

    # ---- Load attempt + story ----
    async with async_session() as db:
        result = await db.execute(
            select(ReadingAttempt).where(ReadingAttempt.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        if not attempt:
            await websocket.send_json({"type": "error", "message": "Attempt not found"})
            await websocket.close()
            return

        result = await db.execute(select(Story).where(Story.id == attempt.story_id))
        story = result.scalar_one_or_none()
        if not story:
            await websocket.send_json({"type": "error", "message": "Story not found"})
            await websocket.close()
            return

    story_words = story.text.split()
    current_index = 0
    all_events: list[dict] = []
    stuck_count = 0

    # Rate limiter: prevent cursor from advancing faster than a child can read.
    import time as _time
    MAX_WPS = 2.5  # max words per second (≈150 wpm — fast for a child, but realistic)
    MAX_ADVANCE_PER_MSG = 4  # max words the cursor can advance per single STT chunk
    _session_start_time = _time.monotonic()
    _paused_duration = 0.0  # total seconds spent paused (pronunciation popups)
    _pause_start = 0.0  # when the current pause started

    print(flush=True)
    print(
        f"[WS] Session started: attempt={attempt_id}, story={story.id}, "
        f"total_words={len(story_words)}, first_words={' '.join(story_words[:8])!r}",
        flush=True,
    )

    # ---- Sarvam STT WebSocket connection helper ----
    qs = urlencode({
        "language-code": "en-IN",
        "model": settings.sarvam_stt_model,
        "flush_signal": "true",
        "input_audio_codec": "pcm_s16le",
        "sample_rate": "16000",
        "high_vad_sensitivity": "true",
    })
    sarvam_url = f"{settings.sarvam_stt_url}?{qs}"
    extra_headers = {
        "Api-Subscription-Key": settings.sarvam_api_key,
    }

    async def connect_sarvam() -> websockets.WebSocketClientProtocol:
        """Connect (or reconnect) to the Sarvam Saarika STT WebSocket."""
        api_key_preview = settings.sarvam_api_key[:6] + "..." if settings.sarvam_api_key else "<EMPTY>"
        print(
            f"[WS] Connecting to Sarvam STT: url={sarvam_url} "
            f"key={api_key_preview}",
            flush=True,
        )
        ws = await websockets.connect(
            sarvam_url,
            additional_headers=extra_headers,
        )
        print(f"[WS] Connected to Sarvam Saarika STT for attempt={attempt_id}", flush=True)
        return ws

    sarvam_ws = None
    try:
        sarvam_ws = await connect_sarvam()
    except Exception as e:
        print(f"[WS] Failed to connect to Sarvam Saarika STT: {e}", flush=True)
        logger.exception("Sarvam Saarika STT connection failed")
        await websocket.send_json({
            "type": "error",
            "message": "Could not connect to transcription service.",
        })
        await websocket.close()
        return

    # ---- Shared state for concurrent tasks ----
    stop_event = asyncio.Event()
    paused = False  # True while pronunciation popup is open

    relay_bytes_total = 0
    relay_frame_count = 0

    # Silence frame: 100ms of zero-valued PCM16 at 16kHz mono = 3200 bytes
    SILENCE_FRAME = b"\x00" * 3200
    SILENCE_INTERVAL = 2.0  # seconds between keepalive silence frames during pause

    # Reconnection settings
    MAX_SARVAM_RECONNECTS = 3
    sarvam_reconnect_count = 0

    async def _send_to_sarvam(audio_bytes: bytes) -> None:
        """Send audio bytes to Sarvam, reconnecting if the connection dropped."""
        nonlocal sarvam_ws, sarvam_reconnect_count
        b64_audio = base64.b64encode(audio_bytes).decode("ascii")
        sarvam_msg = json.dumps({
            "audio": {
                "data": b64_audio,
                "sample_rate": 16000,
                "encoding": "audio/wav",
            }
        })
        try:
            await sarvam_ws.send(sarvam_msg)
        except (websockets.exceptions.ConnectionClosed, Exception) as send_err:
            if sarvam_reconnect_count >= MAX_SARVAM_RECONNECTS:
                print(
                    f"[WS] attempt={attempt_id}: Sarvam send failed and max "
                    f"reconnects ({MAX_SARVAM_RECONNECTS}) exhausted: {send_err}",
                    flush=True,
                )
                raise
            sarvam_reconnect_count += 1
            print(
                f"[WS] attempt={attempt_id}: Sarvam connection lost, "
                f"reconnecting ({sarvam_reconnect_count}/{MAX_SARVAM_RECONNECTS})...",
                flush=True,
            )
            try:
                sarvam_ws = await connect_sarvam()
                # Retry the send on the fresh connection
                await sarvam_ws.send(sarvam_msg)
                print(
                    f"[WS] attempt={attempt_id}: Sarvam reconnected successfully",
                    flush=True,
                )
            except Exception as reconn_err:
                print(
                    f"[WS] attempt={attempt_id}: Sarvam reconnection failed: {reconn_err}",
                    flush=True,
                )
                raise

    async def silence_keepalive():
        """Send periodic silence frames to Sarvam while paused to prevent timeout."""
        while not stop_event.is_set():
            await asyncio.sleep(SILENCE_INTERVAL)
            if paused and not stop_event.is_set():
                try:
                    await _send_to_sarvam(SILENCE_FRAME)
                except Exception:
                    # Reconnection failed — will be handled by browser_to_sarvam
                    break

    async def browser_to_sarvam():
        """Task A: Read PCM16 binary frames from browser, relay to Sarvam."""
        nonlocal relay_bytes_total, relay_frame_count, paused, _pause_start, _paused_duration
        try:
            while not stop_event.is_set():
                try:
                    data = await asyncio.wait_for(
                        websocket.receive(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                msg_type = data.get("type", "?")
                has_bytes = "bytes" in data and data["bytes"] is not None
                has_text = "text" in data and data["text"] is not None

                if has_bytes and data["bytes"]:
                    # Drop audio while pronunciation popup is open
                    if paused:
                        continue

                    audio_bytes = data["bytes"]
                    relay_bytes_total += len(audio_bytes)
                    relay_frame_count += 1

                    # Compute audio level (RMS of Int16 samples) periodically
                    import struct as _struct
                    n_samples = len(audio_bytes) // 2
                    if n_samples > 0 and relay_frame_count % 50 == 1:
                        samples = _struct.unpack(f"<{n_samples}h", audio_bytes[:n_samples*2])
                        rms = (sum(s*s for s in samples) / n_samples) ** 0.5
                        print(
                            f"[WS] attempt={attempt_id}: relay frame #{relay_frame_count}, "
                            f"this={len(audio_bytes)}B, total={relay_bytes_total}B, "
                            f"rms={rms:.1f}, samples={n_samples}",
                            flush=True,
                        )

                    if relay_frame_count == 1:
                        # Log the first message shape
                        b64_preview = base64.b64encode(audio_bytes).decode("ascii")[:40] + "..."
                        print(
                            f"[WS] attempt={attempt_id}: first audio msg shape: "
                            f'{{"audio": {{"data": "{b64_preview}", "sample_rate": 16000, '
                            f'"encoding": "audio/wav"}}}}',
                            flush=True,
                        )

                    await _send_to_sarvam(audio_bytes)

                elif has_text and data["text"]:
                    msg = json.loads(data["text"])

                    if msg.get("type") == "pause":
                        # Pronunciation popup opened → stop sending real audio
                        # (silence_keepalive task will send silence to keep Sarvam alive)
                        paused = True
                        _pause_start = _time.monotonic()
                        print(
                            f"[WS] attempt={attempt_id}: PAUSED — sending silence keepalive to Sarvam",
                            flush=True,
                        )
                        continue

                    if msg.get("type") == "resume":
                        paused = False
                        if _pause_start > 0:
                            _paused_duration += _time.monotonic() - _pause_start
                            _pause_start = 0.0
                        print(
                            f"[WS] attempt={attempt_id}: RESUMED — audio streaming resumes "
                            f"(total paused: {_paused_duration:.1f}s)",
                            flush=True,
                        )
                        continue

                    if msg.get("type") == "stop":
                        print(
                            f"[WS] attempt={attempt_id}: received stop command "
                            f"(relayed {relay_frame_count} frames, {relay_bytes_total}B total)",
                            flush=True,
                        )
                        stop_event.set()
                        return

                elif msg_type == "websocket.disconnect":
                    stop_event.set()
                    return

        except WebSocketDisconnect:
            print(f"[WS] attempt={attempt_id}: browser disconnected", flush=True)
            stop_event.set()
        except Exception as e:
            print(f"[WS] attempt={attempt_id}: browser_to_sarvam error: {e}", flush=True)
            stop_event.set()

    async def keepalive():
        """Task C: Keep the session alive while waiting for VAD-triggered transcripts.

        Sarvam Saarika has built-in VAD with high_vad_sensitivity=true,
        so it will automatically trigger transcription when it detects
        speech boundaries. We just need to keep the task alive.
        """
        try:
            while not stop_event.is_set():
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def sarvam_to_browser():
        """Task B: Read transcript events from Sarvam, run alignment, send to browser."""
        nonlocal current_index, stuck_count, sarvam_ws, sarvam_reconnect_count

        while not stop_event.is_set():
            try:
                async for raw_msg in sarvam_ws:
                    if stop_event.is_set():
                        break

                    msg = json.loads(raw_msg)
                    event_type = msg.get("type", "")

                    # Log all Sarvam events for debugging
                    print(
                        f"[WS] attempt={attempt_id}: Sarvam event: {event_type} "
                        f"(keys={list(msg.keys())})",
                        flush=True,
                    )

                    # Handle transcript data events
                    # Sarvam may send type="data" or type="transcript"
                    if event_type in ("data", "transcript"):
                        # Try nested data.transcript first, then top-level text
                        inner = msg.get("data", {})
                        if isinstance(inner, dict):
                            transcript_text = inner.get("transcript", inner.get("text", "")).strip()
                        else:
                            transcript_text = str(inner).strip()
                        if not transcript_text:
                            transcript_text = msg.get("text", "").strip()
                        print(
                            f"[WS] attempt={attempt_id}: transcript → {transcript_text[:200]!r}",
                            flush=True,
                        )

                        if not transcript_text:
                            continue

                        # Run word alignment
                        prev_index = current_index
                        events = align_transcript_to_story(
                            story_words,
                            transcript_text,
                            current_index=current_index,
                        )

                        if not events:
                            print(f"[WS] attempt={attempt_id}: alignment produced 0 events", flush=True)
                            continue

                        # Find the furthest word that was *actually spoken* (correct/fuzzy).
                        spoken_events = [
                            e for e in events if e["match"] in ("correct", "fuzzy")
                        ]
                        skip_events = [
                            e for e in events if e["match"] == "skip"
                        ]
                        mismatch_events = [
                            e for e in events if e["match"] == "mismatch"
                        ]

                        if spoken_events:
                            new_index = spoken_events[-1]["word_index"] + 1
                            stuck_count = 0
                        elif skip_events:
                            new_index = current_index
                        else:
                            stuck_count += 1
                            if stuck_count >= 6 and mismatch_events:
                                new_index = current_index + 1
                                print(
                                    f"[WS] attempt={attempt_id}: stuck on word {current_index} "
                                    f"({story_words[current_index] if current_index < len(story_words) else '?'!r}) "
                                    f"for {stuck_count} chunks, force-advancing",
                                    flush=True,
                                )
                                stuck_count = 0
                            else:
                                new_index = current_index

                        # ---- Per-message advance cap ----
                        if new_index - current_index > MAX_ADVANCE_PER_MSG:
                            capped_index = current_index + MAX_ADVANCE_PER_MSG
                            print(
                                f"[WS] attempt={attempt_id}: per-msg cap: wanted idx={new_index} "
                                f"but capping to {capped_index} (max +{MAX_ADVANCE_PER_MSG}/msg)",
                                flush=True,
                            )
                            new_index = capped_index

                        # ---- Rate limiter ----
                        elapsed = _time.monotonic() - _session_start_time - _paused_duration
                        max_allowed = int(elapsed * MAX_WPS) + 1
                        if new_index > max_allowed:
                            print(
                                f"[WS] attempt={attempt_id}: rate-limited: wanted idx={new_index} "
                                f"but max_allowed={max_allowed} at {elapsed:.1f}s "
                                f"({MAX_WPS} wps cap)",
                                flush=True,
                            )
                            new_index = max_allowed

                        current_index = min(new_index, len(story_words))
                        all_events.extend(events)

                        print(
                            f"[WS] attempt={attempt_id}: alignment: {len(events)} events "
                            f"({sum(1 for e in events if e['match'] == 'correct')} correct, "
                            f"{sum(1 for e in events if e['match'] == 'fuzzy')} fuzzy, "
                            f"{sum(1 for e in events if e['match'] == 'mismatch')} mismatch, "
                            f"{sum(1 for e in events if e['match'] == 'skip')} skip) "
                            f"idx {prev_index}→{current_index}",
                            flush=True,
                        )

                        problems = [
                            e for e in events if e["match"] in ("mismatch", "skip")
                        ]

                        try:
                            await websocket.send_json({
                                "type": "alignment",
                                "events": events,
                                "current_index": current_index,
                                "total_words": len(story_words),
                                "problems": problems,
                            })
                        except Exception:
                            stop_event.set()
                            break

                        if current_index >= len(story_words):
                            try:
                                await websocket.send_json({
                                    "type": "complete",
                                    "message": "Great job! You finished the story!",
                                })
                            except Exception:
                                pass
                            stop_event.set()
                            break

                    # Handle VAD signals (log for debugging)
                    elif event_type in ("speech_start", "speech_end"):
                        print(
                            f"[WS] attempt={attempt_id}: Sarvam VAD: {event_type}",
                            flush=True,
                        )

                    # Handle errors from Sarvam
                    elif event_type == "error":
                        error_data = msg.get("data", msg.get("message", msg.get("error", "Unknown error")))
                        print(
                            f"[WS] attempt={attempt_id}: Sarvam error: {error_data} "
                            f"(full msg: {json.dumps(msg)[:500]})",
                            flush=True,
                        )
                        try:
                            await websocket.send_json({
                                "type": "error",
                                "message": "Transcription service error – keep reading!",
                            })
                        except Exception:
                            stop_event.set()
                            break

            except websockets.exceptions.ConnectionClosed as e:
                print(f"[WS] attempt={attempt_id}: Sarvam WS closed: {e}", flush=True)
                if sarvam_reconnect_count >= MAX_SARVAM_RECONNECTS:
                    print(
                        f"[WS] attempt={attempt_id}: max reconnects exhausted in reader task",
                        flush=True,
                    )
                    stop_event.set()
                    return
                sarvam_reconnect_count += 1
                print(
                    f"[WS] attempt={attempt_id}: reconnecting Sarvam from reader task "
                    f"({sarvam_reconnect_count}/{MAX_SARVAM_RECONNECTS})...",
                    flush=True,
                )
                try:
                    sarvam_ws = await connect_sarvam()
                    print(
                        f"[WS] attempt={attempt_id}: Sarvam re-established in reader task",
                        flush=True,
                    )
                    continue  # restart the async for loop on the new connection
                except Exception as reconn_err:
                    print(
                        f"[WS] attempt={attempt_id}: Sarvam reconnect failed: {reconn_err}",
                        flush=True,
                    )
                    stop_event.set()
                    return
            except Exception as e:
                print(f"[WS] attempt={attempt_id}: sarvam_to_browser error: {e}", flush=True)
                stop_event.set()
                return

    # ---- Run all four tasks concurrently ----
    try:
        await asyncio.gather(
            browser_to_sarvam(),
            sarvam_to_browser(),
            silence_keepalive(),
            keepalive(),
        )
    except Exception as e:
        print(f"[WS] attempt={attempt_id}: gather error: {e}", flush=True)
    finally:
        # Clean up
        print(
            f"[WS] attempt={attempt_id}: session ended, "
            f"current_index={current_index}/{len(story_words)}, "
            f"total_events={len(all_events)}",
            flush=True,
        )
        if sarvam_ws:
            try:
                await sarvam_ws.close()
            except Exception:
                pass

        # Save events to DB
        await _save_ws_events(attempt_id, story.id, all_events)


async def _save_ws_events(attempt_id: int, story_id: int, events: list[dict]) -> None:
    """Persist word-alignment events gathered during a WebSocket session."""
    if not events:
        return
    async with async_session() as db:
        for evt in events:
            we = WordEvent(
                attempt_id=attempt_id,
                story_id=story_id,
                word_index=evt["word_index"],
                expected_word=evt["expected"],
                recognized_word=evt.get("recognized"),
                event_type=evt["match"],
                severity=1 if evt["match"] == "mismatch" else 0,
            )
            db.add(we)
        await db.commit()


