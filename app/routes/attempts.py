"""Reading attempt APIs including WebSocket relay to OpenAI Realtime API."""

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
    ProblemWordsAgg,
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

    # Update problem words aggregate
    await _update_problem_words(db, attempt.user_id, events, story.level if story else 1)

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

    Also records the lookup in the child's problem words tracker so that
    future stories can incorporate these words for practice.

    Body: {word: str}.
    Returns: {audio_url: str, phonetic: str|null, phonetic_audio_url: str|null}
    """
    from app.services.phonetics import get_phonetic_breakdown

    body = await request.json()
    word = body.get("word", "").strip()
    if not word:
        return JSONResponse({"error": "No word provided"}, status_code=400)

    # ---- Track this lookup as a problem word ----
    try:
        result = await db.execute(
            select(ReadingAttempt).where(ReadingAttempt.id == attempt_id)
        )
        attempt = result.scalar_one_or_none()
        if attempt:
            # Also fetch the story to get the level
            result = await db.execute(
                select(Story).where(Story.id == attempt.story_id)
            )
            story = result.scalar_one_or_none()
            level = story.level if story else 1

            import re as _re
            word_lower = _re.sub(r"[^\w]", "", word.lower()).strip()
            result = await db.execute(
                select(ProblemWordsAgg).where(
                    ProblemWordsAgg.user_id == attempt.user_id,
                    ProblemWordsAgg.word == word_lower,
                )
            )
            agg = result.scalar_one_or_none()
            if agg:
                agg.total_lookups += 1
                agg.mastery_score = 0.0  # reset mastery on new lookup
                agg.last_seen_at = dt.datetime.utcnow()
            else:
                agg = ProblemWordsAgg(
                    user_id=attempt.user_id,
                    word=word_lower,
                    level_first_seen=level,
                    total_lookups=1,
                    mastery_score=0.0,
                )
                db.add(agg)
            await db.commit()
            logger.info(
                "Tracked pronunciation lookup: user=%s word=%r (lookups=%d)",
                attempt.user_id, word_lower,
                agg.total_lookups,
            )
    except Exception:
        logger.exception("Failed to track pronunciation lookup")
        # Don't fail the whole request if tracking fails

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


# ---- WebSocket for real-time reading session (OpenAI Realtime API relay) ----


@router.websocket("/ws/attempts/{attempt_id}")
async def reading_session_ws(websocket: WebSocket, attempt_id: int):
    """
    WebSocket relay between the browser and OpenAI Realtime API for streaming STT.

    Client sends:
      - binary frames: raw PCM16 audio at 24 kHz mono
      - text frames:  JSON commands {"type": "stop"} etc.

    Server sends:
      - JSON: {"type": "alignment", "events": [...], "current_index": int, ...}
      - JSON: {"type": "complete", "message": ...}
      - JSON: {"type": "error", "message": ...}
    """
    import websockets

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
    # A fluent 8-year-old can read at 150+ wpm → ~2.5-3 words/sec in bursts.
    # We cap at MAX_WPS to prevent hallucination-driven runaway, but keep
    # it generous enough to not hold back a fast reader.
    import time as _time
    MAX_WPS = 3.5  # max words per second (≈210 wpm peak)
    _session_start_time = _time.monotonic()
    _paused_duration = 0.0  # total seconds spent paused (pronunciation popups)
    _pause_start = 0.0  # when the current pause started

    print(flush=True)
    print(
        f"[WS] Session started: attempt={attempt_id}, story={story.id}, "
        f"total_words={len(story_words)}, first_words={' '.join(story_words[:8])!r}",
        flush=True,
    )

    # ---- Connect to OpenAI Realtime API ----
    openai_ws = None
    try:
        extra_headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        openai_ws = await websockets.connect(
            settings.openai_realtime_url,
            additional_headers=extra_headers,
        )
        print(f"[WS] Connected to OpenAI Realtime API for attempt={attempt_id}", flush=True)

        # Configure the transcription session
        # - Disable server VAD: we commit audio manually on a timer so the
        #   model receives longer speech segments with more context.
        # - Use a context-only prompt (no story words!) to hint at the accent
        #   and speaking style.  Passing actual story words causes hallucination.
        context_prompt = (
            "A child with an Indian English accent is reading a simple "
            "children's story aloud, slowly, one word at a time."
        )
        session_config = {
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": settings.openai_realtime_model,
                    "language": "en",
                    "prompt": context_prompt,
                },
                "turn_detection": None,  # disable VAD — we commit manually
                "input_audio_noise_reduction": {
                    "type": "near_field",
                },
            },
        }
        await openai_ws.send(json.dumps(session_config))
        print(f"[WS] Sent session config (no VAD, manual commits, accent prompt)", flush=True)

    except Exception as e:
        print(f"[WS] Failed to connect to OpenAI Realtime API: {e}", flush=True)
        logger.exception("OpenAI Realtime API connection failed")
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
    COMMIT_INTERVAL_S = 2.0  # commit audio buffer every N seconds (lower = less lag)

    async def browser_to_openai():
        """Task A: Read PCM16 binary frames from browser, relay to OpenAI."""
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

                    # Base64-encode and send to OpenAI
                    b64_audio = base64.b64encode(audio_bytes).decode("ascii")
                    openai_msg = {
                        "type": "input_audio_buffer.append",
                        "audio": b64_audio,
                    }
                    await openai_ws.send(json.dumps(openai_msg))

                elif has_text and data["text"]:
                    msg = json.loads(data["text"])

                    if msg.get("type") == "pause":
                        # Pronunciation popup opened → clear buffer, stop commits
                        paused = True
                        nonlocal _pause_start
                        _pause_start = _time.monotonic()
                        try:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.clear",
                            }))
                            print(
                                f"[WS] attempt={attempt_id}: PAUSED — cleared OpenAI audio buffer",
                                flush=True,
                            )
                        except Exception as e:
                            print(f"[WS] attempt={attempt_id}: pause/clear error: {e}", flush=True)
                        continue

                    if msg.get("type") == "resume":
                        paused = False
                        nonlocal _paused_duration
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
                        # Do a final commit before stopping
                        try:
                            await openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        except Exception:
                            pass
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
            print(f"[WS] attempt={attempt_id}: browser_to_openai error: {e}", flush=True)
            stop_event.set()

    async def periodic_commit():
        """Task C: Commit the audio buffer every COMMIT_INTERVAL_S seconds.

        Since we disabled server VAD, we must manually tell OpenAI when to
        process a chunk of audio.  Committing every 3 s gives the model
        enough context to produce accurate multi-word transcripts.
        """
        commit_count = 0
        try:
            while not stop_event.is_set():
                await asyncio.sleep(COMMIT_INTERVAL_S)
                if stop_event.is_set():
                    break
                if paused:
                    continue  # skip commits while pronunciation popup is open

                commit_count += 1
                try:
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.commit",
                    }))
                    print(
                        f"[WS] attempt={attempt_id}: manual commit #{commit_count} "
                        f"(every {COMMIT_INTERVAL_S}s)",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[WS] attempt={attempt_id}: commit error: {e}", flush=True)
                    stop_event.set()
                    break
        except asyncio.CancelledError:
            pass

    async def openai_to_browser():
        """Task B: Read transcript events from OpenAI, run alignment, send to browser."""
        nonlocal current_index, stuck_count

        try:
            async for raw_msg in openai_ws:
                if stop_event.is_set():
                    break

                msg = json.loads(raw_msg)
                event_type = msg.get("type", "")

                # Log all OpenAI events for debugging
                print(
                    f"[WS] attempt={attempt_id}: OpenAI event: {event_type}",
                    flush=True,
                )

                # Handle transcription completed events
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript_text = msg.get("transcript", "").strip()
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
                    # Skip events are NOT counted as forward progress — they're
                    # assumptions that can be wrong.
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
                        # If we only have skips (no confirmed spoken words),
                        # don't advance — the skip detection may be wrong.
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

                    # ---- Rate limiter ----
                    # Don't let the cursor advance faster than MAX_WPS words/sec.
                    # Subtract time spent paused (pronunciation popups) from elapsed.
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

                # Handle errors from OpenAI
                elif event_type == "error":
                    error_msg = msg.get("error", {}).get("message", "Unknown error")
                    # Ignore harmless "buffer too small" errors from empty commits
                    if "buffer too small" in error_msg:
                        pass  # expected when committing empty buffer
                    else:
                        print(f"[WS] attempt={attempt_id}: OpenAI error: {error_msg}", flush=True)
                        try:
                            await websocket.send_json({
                                "type": "error",
                                "message": "Transcription service error – keep reading!",
                            })
                        except Exception:
                            stop_event.set()
                            break

        except websockets.exceptions.ConnectionClosed as e:
            print(f"[WS] attempt={attempt_id}: OpenAI WS closed: {e}", flush=True)
            stop_event.set()
        except Exception as e:
            print(f"[WS] attempt={attempt_id}: openai_to_browser error: {e}", flush=True)
            stop_event.set()

    # ---- Run all three tasks concurrently ----
    try:
        await asyncio.gather(
            browser_to_openai(),
            openai_to_browser(),
            periodic_commit(),
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
        if openai_ws:
            try:
                await openai_ws.close()
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


async def _update_problem_words(
    db: AsyncSession,
    user_id: int,
    events: list[WordEvent],
    level: int,
):
    """Update the problem_words_agg table.

    - Words that were misread/stalled/hinted: upsert with increased miss count,
      reset mastery_score to 0.
    - Words that were read correctly AND already exist in the problem list:
      increment mastery_score by 0.34 (mastered after ~3 correct reads).
    - If a word reaches mastery_score >= 1.0, delete it from the problem list.
    """
    import re as _re

    def _clean(w: str) -> str:
        """Lowercase and strip punctuation for consistent problem-word keys."""
        return _re.sub(r"[^\w]", "", w.lower()).strip()

    # Separate problem events from correct reads
    problem_events = [
        e for e in events if e.event_type in ("mismatch", "stall", "hint")
    ]
    correct_events = [
        e for e in events if e.event_type in ("correct", "fuzzy")
    ]

    # 1. Handle problem words (misses)
    for evt in problem_events:
        word_lower = _clean(evt.expected_word)
        if not word_lower:
            continue
        result = await db.execute(
            select(ProblemWordsAgg).where(
                ProblemWordsAgg.user_id == user_id,
                ProblemWordsAgg.word == word_lower,
            )
        )
        agg = result.scalar_one_or_none()

        if agg:
            agg.total_misses += 1 if evt.event_type in ("mismatch", "stall") else 0
            agg.total_hints += 1 if evt.event_type == "hint" else 0
            agg.mastery_score = 0.0  # reset mastery on new miss
            agg.last_seen_at = dt.datetime.utcnow()
        else:
            agg = ProblemWordsAgg(
                user_id=user_id,
                word=word_lower,
                level_first_seen=level,
                total_misses=1 if evt.event_type in ("mismatch", "stall") else 0,
                total_hints=1 if evt.event_type == "hint" else 0,
                mastery_score=0.0,
            )
            db.add(agg)

    # 2. Handle correct reads of words that are already in the problem list.
    #    Each correct read of a problem word increases mastery by 0.34.
    #    Collect unique correctly-read words first (avoid double-counting).
    correct_words_seen: set[str] = set()
    for evt in correct_events:
        word_lower = _clean(evt.expected_word)
        if not word_lower or word_lower in correct_words_seen:
            continue
        correct_words_seen.add(word_lower)

        # Only update words that are already tracked as problems
        result = await db.execute(
            select(ProblemWordsAgg).where(
                ProblemWordsAgg.user_id == user_id,
                ProblemWordsAgg.word == word_lower,
            )
        )
        agg = result.scalar_one_or_none()
        if agg:
            agg.mastery_score = round(agg.mastery_score + 0.34, 2)
            agg.last_seen_at = dt.datetime.utcnow()

    await db.commit()

    # 3. Remove mastered words (mastery_score >= 1.0)
    result = await db.execute(
        select(ProblemWordsAgg).where(
            ProblemWordsAgg.user_id == user_id,
            ProblemWordsAgg.mastery_score >= 1.0,
        )
    )
    mastered = result.scalars().all()
    for agg in mastered:
        logger.info(
            "Word mastered and removed: user=%s word=%r (score=%.2f, misses=%d, lookups=%d)",
            user_id, agg.word, agg.mastery_score, agg.total_misses, agg.total_lookups,
        )
        await db.delete(agg)
    if mastered:
        await db.commit()
