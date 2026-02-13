# Consolidated PRD — Child Reading Coach Web App (FastAPI + Jinja2 + SQLite)

## 1) Product summary

### Working name

Ritu's ReadAlong Tutor

### Goal

Help an 8-year-old child improve reading **accuracy, fluency, and confidence** through:

* leveled, engaging stories (mapped to **Ladybird Readers** levels)
* “read aloud” sessions with word-by-word highlighting
* immediate corrective coaching (voice) when the child struggles
* scoring + motivation
* automatic progression (difficulty adapts from past attempts)

### Target users & roles

* **Parent (Superuser):** full control (dashboard, children management, AI settings, manual level override, exports, deletes)
* **Child (User):** can read, practice, and view their own scores/motivation

---

## 2) Tech stack & constraints

### Backend

* Python **FastAPI**
* Templating: Jinja2
* DB: SQLite

### Frontend

* HTML + TailwindCSS
* JavaScript (mic recording + streaming + highlighting)

### AI providers

* **OpenAI** for image generation (via API models like `gpt-image-1`), story generation (`gpt-4o-mini`), and phonetics. ([OpenAI Platform][1])
* **Sarvam AI** for speech-to-text (Saarika v2.5) + text-to-speech (Bulbul v3) with native Indian English support

---

## 3) Leveling standard (Ladybird Readers mapping)

The app will generate stories that follow **Ladybird Readers level word-count ranges** (baseline constraints per story). Example ranges:

* **Level 1:** 100–200 words
* **Level 2:** 200–300 words
* **Level 3:** 300–600 words
* **Level 4:** 600–900 words
* **Level 5:** 900–1500 words
* **Level 6:** 1500–2000 words
  ([Penguin Books UK][4])

**Default starting point:** Level 1.

---

## 4) Authentication & authorization (Email OTP)

### 4.1 Hardcoded “first user” superuser

* On first application startup (or first migration), the system **must ensure** a user exists:

  * `email = abhaybhargav@gmail.com`
  * `role = parent_superuser`
  * `is_active = true`
* This user is the **only** account that can create the first child user(s) unless additional parents are later created by a superuser.

### 4.2 OTP login flow (passwordless)

1. User enters email on `/login`
2. App sends a 6-digit OTP via Mailtrap Email API
3. User submits OTP
4. App verifies OTP + creates a session
5. Role-based access is enforced server-side on every request

### 4.3 Mailtrap OTP sending (required)

* Use Mailtrap **transactional** send endpoint: `POST https://send.api.mailtrap.io/api/send` ([Mailtrap Documentation][3])
* Authenticate with header: `Api-Token: <token>` ([Mailtrap Documentation][3])
* OTP emails must be sent from a verified sending domain (operational prerequisite). ([Mailtrap Documentation][5])

### 4.4 OTP security rules

* OTP length: **6 digits**
* Expiry: **10 minutes**
* Max verify attempts: **5** (then OTP invalidated)
* Resend cooldown: **60 seconds**
* Rate limit: per IP + per email (sliding window)
* Anti-enumeration: OTP request endpoint always returns a generic success message

### 4.5 Session rules

* Cookie-based session (server-side session table or signed cookie)
* HttpOnly + Secure
* CSRF protection for form posts (or SameSite strict + token for sensitive endpoints)

### 4.6 Authorization matrix

* **Parent superuser:** access everything, manage users, override levels, view all attempts
* **Child user:** only their own stories/attempts; no access to `/parent/*` or user management endpoints

**Acceptance criteria**

* Child attempting `/parent/*` gets 403 + redirect to `/`.
* Parent can create child accounts and view their full history.

---

## 5) Core user flows

### 5.1 Child flow

1. Open app → “Today’s Story” (or latest assigned story)
2. Tap **Start Reading**
3. Browser records mic audio in short chunks and streams to server
4. Server transcribes via Sarvam Saarika STT and returns incremental text updates
5. Frontend highlights matched story words as the child speaks
6. If a word is wrong or the child stalls:

   * coach speaks the correct pronunciation + quick hint
   * event is logged as a “problem word”
7. End of story:

   * score + encouraging feedback
   * CTA: “Try another story”

### 5.2 Parent flow

* Login → dashboard:

  * recent attempts + scores trend
  * top problem words
  * current level + why
* Generate new story (manual) OR enable auto-generation
* Configure:

  * coach voice + strictness
  * interests/themes
  * progression sensitivity
* Manage children (create, disable, reset progress)
* Export/delete data

---

## 6) Functional requirements

## 6.1 Story generation

**FR-STORY-1:** Generate story text for a specified level (default: user’s current level).
**FR-STORY-2:** Story constraints must match chosen level:

* word count within target band (see Section 3)
* simple sentence structure at lower levels
* child-safe themes only (no violence/adult content)

**FR-STORY-3:** Persist story metadata:

* level, word count, generated timestamp, prompt used, tags/theme

**Acceptance criteria**

* Level 1 stories consistently land in 100–200 word range. ([Penguin Books UK][4])

---

## 6.2 Image generation (OpenAI)

**FR-IMG-1:** Generate 2–6 kid-friendly illustrations per story using OpenAI image generation. ([OpenAI Platform][1])
**FR-IMG-2:** Maintain visual consistency across images (same characters/clothing/colors) via prompt discipline + reuse of descriptors.
**FR-IMG-3:** Store images on disk; DB stores paths + prompts + provider metadata.
**FR-IMG-4:** Image generation must not block the reading experience (async/background task).

**Acceptance criteria**

* Story can be read even if images fail; failures recorded as non-fatal.

---

## 6.3 Read-aloud session (STT-driven highlighting)

### 6.3.1 Audio capture & streaming

**FR-READ-1:** On Start Reading, request mic permission.
**FR-READ-2:** Use `MediaRecorder` to capture audio chunks (config default 1–2s).
**FR-READ-3:** Stream chunks to server for transcription.

### 6.3.2 Speech-to-text (Sarvam Saarika)

**FR-STT-1:** Server transcribes audio using Sarvam Saarika v2.5 streaming WebSocket STT with Indian English (en-IN) support.
**FR-STT-2:** Server sends incremental transcript updates back to client via:

* WebSocket (preferred) OR
* SSE (acceptable)

### 6.3.3 Word alignment & highlighting

**FR-HL-1:** Render story text with word spans and stable `word_index`.
**FR-HL-2:** As transcript text arrives, align recognized tokens to story words using:

* sliding window matching (lookahead K words)
* normalization (casefold, strip punctuation)
* fuzzy match (edit distance threshold) for minor errors

### 6.3.4 Error detection (problem words)

A “problem word event” triggers when:

* **Mismatch:** recognized token doesn’t match expected word after retries
* **Skip:** child jumps ahead beyond a threshold
* **Stall:** no progress for X seconds on the current word (default 4–6s)

**FR-PROB-1:** Each problem event is stored with expected vs recognized token and context.

**Acceptance criteria**

* Highlight feels “near-live”: transcript updates frequently enough to track reading (best-effort dependent on network).
* If STT fails, app falls back to “read without scoring pronunciation” mode.

---

## 6.4 Coaching voice (Sarvam Bulbul TTS)

**FR-TTS-1:** When a problem word triggers, generate coaching audio:

* speak the correct word
* speak a short tip (1–2 sentences)
  **FR-TTS-2:** Use Sarvam Bulbul v3 TTS endpoint to generate speech.
  **FR-TTS-3:** Cache TTS audio per `(speaker, text)` for latency and cost.
  **FR-TTS-4:** Parent can select a voice and strictness profile.

**Acceptance criteria**

* “Repeat” replays cached audio (no new API call).
* Coach intervention can be skipped; skip is logged.

---

## 6.5 Scoring & motivation

**FR-SCORE-1:** Compute a 0–100 score with stored sub-scores:

* Accuracy (0–60)
* Fluency (0–25)
* Independence (0–15), penalizing hints/skips/interventions

**FR-SCORE-2:** Generate a short child-friendly summary:

* “wins” (1–3)
* “practice words” (1–3)
* encouragement line

**FR-SCORE-3:** Persist the full score breakdown and attempt metrics.

**Acceptance criteria**

* Score is reproducible from stored word events + timing metrics.

---

## 6.6 Progression engine (adaptive leveling)

**FR-LEVEL-1:** Maintain per-child `current_level`.
**FR-LEVEL-2:** Determine next level based on last N attempts (default N=10, newer weighted more):

* promote when high and stable performance
* hold or step down when consistently struggling
* optionally shorten within-level content if frustration detected

**FR-LEVEL-3:** Log the reason for level changes (“avg score 85, accuracy 94% over last 10”).

**Acceptance criteria**

* Parent can override the level manually.
* Level changes are explainable and auditable.

---

## 6.7 Parent dashboard & management

**FR-PARENT-1:** Dashboard shows:

* last 10 attempts
* trendline of scores
* top problem words (weekly + all-time)
* current level + rationale
  **FR-PARENT-2:** Create/disable child users.
  **FR-PARENT-3:** Configure AI settings:
* image style preset
* Sarvam TTS speaker
* strictness
* story themes/interests
  **FR-PARENT-4:** Export/delete child data.

---

## 7) Data model (SQLite)

### 7.1 Tables (minimum viable)

**users**

* id (PK)
* email (unique)
* display_name
* role: `parent_superuser` | `child_user`
* parent_user_id (nullable; set for children)
* is_active
* created_at

**otp_codes**

* id (PK)
* email
* code_hash
* created_at
* expires_at
* attempts_used
* is_consumed
* request_ip
* user_agent

**sessions** (recommended for revocation)

* id (PK)
* user_id
* session_token_hash
* created_at
* expires_at
* last_seen_at

**reading_level_state**

* user_id (FK users.id)
* current_level (int)
* confidence (float)
* updated_at
* last_decision_reason (text/json)

**stories**

* id (PK)
* user_id (child)
* level
* title
* text
* word_count
* created_at
* ai_prompt (text)
* ai_model_meta (json)

**story_images**

* id (PK)
* story_id (FK)
* image_path
* prompt
* provider_meta (json)

**reading_attempts**

* id (PK)
* user_id (child)
* story_id
* started_at
* ended_at
* score_total
* score_accuracy
* score_fluency
* score_independence
* interventions_count
* skips_count
* wpm_estimate
* summary_json

**word_events**

* id (PK)
* attempt_id
* story_id
* word_index
* expected_word
* recognized_word
* event_type: `correct|mismatch|skip|stall|hint`
* severity
* timestamp_ms

**problem_words_agg** (optional materialized view/table)

* user_id
* word
* level_first_seen
* last_seen_at
* total_misses
* total_hints
* mastery_score

---

## 8) Backend routes (FastAPI)

### 8.1 Pages (Jinja2)

* `GET /login`
* `GET /` (child home; shows story CTA)
* `GET /stories/{id}` (reader)
* `GET /parent` (dashboard)
* `GET /parent/children`
* `GET /parent/attempts`
* `GET /parent/words`
* `GET /settings` (parent AI settings)

### 8.2 Auth APIs

* `POST /auth/request-otp`
* `POST /auth/verify-otp`
* `POST /auth/logout`

### 8.3 Story APIs

* `POST /api/stories/generate` (parent-only)
* `GET /api/level_state` (child/parent for that child)

### 8.4 Attempt APIs

* `POST /api/attempts/start`
* `POST /api/attempts/{id}/audio-chunk` (uploads chunk)
* `WS /ws/attempts/{id}` (transcript updates) *(or SSE alternative)*
* `POST /api/attempts/{id}/events` (batch word events)
* `POST /api/attempts/{id}/coach` (returns TTS audio for correction)
* `POST /api/attempts/{id}/finish` (score + update progression)

---

## 9) Non-functional requirements

### Performance

* Reader page render: < 1s after fetch on normal home network
* Chunk-to-highlight: best-effort; target “feels responsive” with frequent updates

### Reliability

* Mic permission denied → “Read without mic” mode
* STT/TTS failures → non-blocking, logged, retry controls shown

### Safety & privacy

* Default: **do not store raw audio**
* Store only transcripts/events/scores
* Parent can delete all child data
* Strong content guardrails in story prompt to ensure child-safe content

---

## 10) MVP scope & milestones

### MVP (Phase 1)

* Hardcoded superuser bootstrapping: `abhaybhargav@gmail.com`
* Mailtrap OTP login (parent + child)
* Level 1 story generation (text) + OpenAI images (2+)
* Read-aloud with chunked upload → Sarvam Saarika STT → word highlight
* Coaching via Sarvam Bulbul TTS + caching
* Store attempts + word events + scores
* Parent dashboard (basic)

### Phase 2

* Better phonics-style hints
* Multi-level progression tuning (levels 1–4)
* Gamification (badges/streaks)

### Phase 3

* Advanced alignment (forced alignment / timestamps if available)
* Optional opt-in audio retention for review
* Multiple children + per-child preferences

---

## 11) Key risks & mitigations

* **STT alignment errors:** use tolerant matching + allow skip/retry; score should not punish minor STT noise too harshly.
* **Email deliverability/setup:** Mailtrap requires verified sending domain and proper token/host usage. ([Mailtrap Documentation][5])
* **Latency:** chunk size tuning + websocket streaming; cache TTS heavily.

---

If you want, I can now turn this PRD into a **Technical Design Doc** with:

* exact request/response JSON schemas for each endpoint
* the word-alignment algorithm pseudocode + edge cases
* the Mailtrap OTP email payload structure for `/api/send` (from/to/subject/html).

[1]: https://platform.openai.com/docs/guides/image-generation?utm_source=chatgpt.com "Image generation | OpenAI API"
[2]: https://docs.sarvam.ai/ "Sarvam AI Documentation"
[3]: https://docs.mailtrap.io/developers/sending/send-email-transactional?utm_source=chatgpt.com "Send Email - Transactional | API Docs | Mailtrap Help Center"
[4]: https://wp.penguin.co.uk/wp-content/uploads/2025/03/Ladybird-Stocklist-2025.pdf?utm_source=chatgpt.com "Ladybird-Stocklist-2025.pdf"
[5]: https://docs.mailtrap.io/email-api-smtp/setup/sending-domain?utm_source=chatgpt.com "Sending Domain Setup - Documentation | Mailtrap Help Center"
[6]: https://docs.sarvam.ai/ "Sarvam AI STT Documentation"
[7]: https://docs.sarvam.ai/ "Sarvam AI TTS Documentation"
