/**
 * Ritu's ReadAlong Tutor – Reader session logic.
 *
 * Audio capture: AudioContext (native sample rate) → ScriptProcessorNode →
 *   Float32 → downsample to 24 kHz → Int16 PCM → binary WebSocket frames.
 *
 * Our server relays the PCM to the Sarvam Saarika STT API and sends
 * back word-alignment events which drive the highlighting UI.
 *
 * Coaching is on-demand: click any word to hear it pronounced.
 */

const TARGET_SAMPLE_RATE = 16000;
const PCM_SEND_INTERVAL_MS = 150;
const SCRIPT_PROCESSOR_BUFSIZE = 4096;

function downsampleBuffer(buffer, srcRate, dstRate) {
    if (srcRate === dstRate) return buffer;
    if (srcRate < dstRate) return buffer;
    const ratio = srcRate / dstRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
        const srcIdx = i * ratio;
        const lo = Math.floor(srcIdx);
        const hi = Math.min(lo + 1, buffer.length - 1);
        const frac = srcIdx - lo;
        result[i] = buffer[lo] * (1 - frac) + buffer[hi] * frac;
    }
    return result;
}

function float32ToInt16(float32) {
    const int16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return int16;
}

function readerApp() {
    return {
        // State: idle → recording → paused → complete | error
        state: 'idle',
        errorMessage: '',

        // Reading progress
        currentIndex: 0,
        wordStatuses: {},
        progress: 0,

        // On-demand pronunciation popup
        showPronounce: false,
        pronounceWord: '',
        pronouncePhonetic: '',
        pronounceLoading: false,
        _pronounceAudio: null,
        _phoneticAudio: null,
        _wasRecording: false,

        // Internal handles
        attemptId: null,
        ws: null,
        scoreUrl: '',

        // Audio pipeline
        _audioCtx: null,
        _sourceNode: null,
        _processorNode: null,
        _audioStream: null,
        _pcmBuffer: [],
        _sendTimer: null,
        _nativeSampleRate: 0,
        _totalBytesSent: 0,
        _sendCount: 0,

        init() {
            this.wordStatuses[0] = 'current';
        },

        getWordClass(index) {
            const s = this.wordStatuses[index];
            if (s === 'correct') return 'word-correct';
            if (s === 'fuzzy')   return 'word-fuzzy';
            if (s === 'mismatch') return 'word-mismatch';
            if (s === 'skip')    return 'word-skip';
            if (s === 'current') return 'word-current';
            if (index > this.currentIndex) return 'word-upcoming';
            return '';
        },

        // ----------------------------------------------------------------
        //  Start
        // ----------------------------------------------------------------

        async startReading() {
            try {
                this._audioStream = await navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
                });
            } catch (err) {
                this.state = 'error';
                this.errorMessage = 'Microphone access denied. Please allow mic access.';
                return;
            }

            try {
                const resp = await fetch('/api/attempts/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ story_id: STORY_ID }),
                });
                const data = await resp.json();
                this.attemptId = data.attempt_id;
            } catch (err) {
                this.state = 'error';
                this.errorMessage = 'Failed to start reading session.';
                return;
            }

            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            this.ws = new WebSocket(`${proto}//${location.host}/api/ws/attempts/${this.attemptId}`);
            this.ws.binaryType = 'arraybuffer';
            this.ws.onmessage = (e) => this.handleWsMessage(JSON.parse(e.data));
            this.ws.onerror   = () => {};
            this.ws.onclose   = () => {};

            try {
                await new Promise((resolve, reject) => {
                    this.ws.onopen = resolve;
                    setTimeout(() => reject(new Error('WS timeout')), 5000);
                });
            } catch {
                this.state = 'error';
                this.errorMessage = 'Could not connect to reading server.';
                return;
            }

            this._startAudioPipeline();
            this.state = 'recording';
            this._totalBytesSent = 0;
            this._sendCount = 0;
        },

        // ----------------------------------------------------------------
        //  Audio pipeline
        // ----------------------------------------------------------------

        _startAudioPipeline() {
            this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            this._nativeSampleRate = this._audioCtx.sampleRate;

            this._sourceNode = this._audioCtx.createMediaStreamSource(this._audioStream);
            this._processorNode = this._audioCtx.createScriptProcessor(SCRIPT_PROCESSOR_BUFSIZE, 1, 1);

            const nativeRate = this._nativeSampleRate;
            const self = this;

            this._processorNode.onaudioprocess = function(ev) {
                if (self.state !== 'recording') return;
                const float32 = ev.inputBuffer.getChannelData(0);
                const resampled = downsampleBuffer(float32, nativeRate, TARGET_SAMPLE_RATE);
                const int16 = float32ToInt16(resampled);
                self._pcmBuffer.push(int16);
            };

            this._sourceNode.connect(this._processorNode);
            this._processorNode.connect(this._audioCtx.destination);
            this._sendTimer = setInterval(() => this._flushPcm(), PCM_SEND_INTERVAL_MS);
        },

        _flushPcm() {
            if (this._pcmBuffer.length === 0) return;
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
            // Don't send audio while pronunciation popup is open
            if (this.state === 'paused' && this.showPronounce) {
                this._pcmBuffer = [];
                return;
            }

            let totalLen = 0;
            for (const chunk of this._pcmBuffer) totalLen += chunk.length;
            const merged = new Int16Array(totalLen);
            let offset = 0;
            for (const chunk of this._pcmBuffer) {
                merged.set(chunk, offset);
                offset += chunk.length;
            }
            this._pcmBuffer = [];
            this.ws.send(merged.buffer);
            this._totalBytesSent += merged.buffer.byteLength;
            this._sendCount += 1;
        },

        _stopAudioPipeline() {
            if (this._sendTimer) { clearInterval(this._sendTimer); this._sendTimer = null; }
            this._flushPcm();
            if (this._processorNode) { this._processorNode.disconnect(); this._processorNode = null; }
            if (this._sourceNode) { this._sourceNode.disconnect(); this._sourceNode = null; }
            if (this._audioCtx) { this._audioCtx.close(); this._audioCtx = null; }
            if (this._audioStream) { this._audioStream.getTracks().forEach(t => t.stop()); this._audioStream = null; }
        },

        // ----------------------------------------------------------------
        //  Pause / Resume / Stop
        // ----------------------------------------------------------------

        pauseReading() { this.state = 'paused'; },

        resumeReading() { this.state = 'recording'; },

        async stopReading() {
            this.state = 'complete';
            this._stopAudioPipeline();
            if (this.ws?.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'stop' }));
            }
            await this.finishReading();
        },

        async finishReading() {
            this.state = 'complete';
            this.progress = 100;
            this._stopAudioPipeline();
            if (this.ws) this.ws.close();
            try {
                await fetch(`/api/attempts/${this.attemptId}/finish`, { method: 'POST' });
                this.scoreUrl = `/stories/${STORY_ID}/score/${this.attemptId}`;
            } catch (err) {
                this.scoreUrl = '/';
            }
        },

        // ----------------------------------------------------------------
        //  WebSocket message handling
        // ----------------------------------------------------------------

        handleWsMessage(msg) {
            if (msg.type === 'alignment') {
                for (const evt of msg.events) {
                    this.wordStatuses[evt.word_index] = evt.match;
                }

                this.currentIndex = msg.current_index;
                if (this.currentIndex < TOTAL_WORDS) {
                    this.wordStatuses[this.currentIndex] = 'current';
                }

                this.progress = Math.round((this.currentIndex / TOTAL_WORDS) * 100);
                this.scrollToCurrentWord();
            }

            if (msg.type === 'complete') {
                this.finishReading();
            }
        },

        scrollToCurrentWord() {
            const el = document.querySelector(`[data-index="${this.currentIndex}"]`);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        },

        // ----------------------------------------------------------------
        //  On-demand word pronunciation (click any word)
        // ----------------------------------------------------------------

        async onWordClick(index) {
            const spans = document.getElementById('story-text')?.querySelectorAll('.word-span');
            if (!spans?.[index]) return;
            const word = spans[index].textContent.trim();
            if (!word) return;

            // Show popup immediately
            this.pronounceWord = word;
            this.pronouncePhonetic = '';
            this.showPronounce = true;
            this.pronounceLoading = true;
            this._pronounceAudio = null;

            // Tell the server to pause (clear audio buffer, stop commits)
            this._wasRecording = this.state === 'recording';
            if (this._wasRecording) {
                this.state = 'paused';
                // Discard any buffered PCM so it doesn't get sent later
                this._pcmBuffer = [];
                // Tell server to clear audio buffer and pause commits
                if (this.ws?.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'pause' }));
                }
            }

            try {
                const resp = await fetch(`/api/attempts/${this.attemptId || 0}/pronounce`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ word }),
                });
                if (resp.ok) {
                    const data = await resp.json();
                    const self = this;

                    // Load the word audio
                    if (data.audio_url) {
                        this._pronounceAudio = new Audio(data.audio_url);
                    }

                    // Load the phonetic narration audio (if available)
                    if (data.phonetic) {
                        this.pronouncePhonetic = data.phonetic;
                    }
                    if (data.phonetic_audio_url) {
                        this._phoneticAudio = new Audio(data.phonetic_audio_url);
                    } else {
                        this._phoneticAudio = null;
                    }

                    // Play word first, then auto-chain the phonetic narration
                    if (this._pronounceAudio) {
                        if (this._phoneticAudio) {
                            // When word audio ends, play the phonetic explanation
                            this._pronounceAudio.onended = function() {
                                // Small pause before explanation
                                setTimeout(() => {
                                    if (self._phoneticAudio) {
                                        self._phoneticAudio.play();
                                    }
                                }, 400);
                            };
                        }
                        this._pronounceAudio.play();
                    }
                }
            } catch (err) {
                console.error('Pronunciation failed:', err);
            } finally {
                this.pronounceLoading = false;
            }
        },

        replayPronunciation() {
            // Replay the word, then chain the phonetic narration
            if (this._pronounceAudio) {
                this._pronounceAudio.currentTime = 0;
                const self = this;
                if (this._phoneticAudio) {
                    this._pronounceAudio.onended = function() {
                        setTimeout(() => {
                            if (self._phoneticAudio) {
                                self._phoneticAudio.currentTime = 0;
                                self._phoneticAudio.play();
                            }
                        }, 400);
                    };
                }
                this._pronounceAudio.play();
            }
        },

        replayExplanation() {
            // Replay just the phonetic explanation narration
            if (this._phoneticAudio) {
                this._phoneticAudio.currentTime = 0;
                this._phoneticAudio.play();
            }
        },

        closePronounce() {
            this.showPronounce = false;
            this.pronouncePhonetic = '';
            // Stop any playing audio
            if (this._pronounceAudio) {
                this._pronounceAudio.pause();
                this._pronounceAudio.onended = null;
            }
            if (this._phoneticAudio) {
                this._phoneticAudio.pause();
            }
            // Tell server to resume (restart commits)
            if (this._wasRecording && this.ws?.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'resume' }));
                this.state = 'recording';
            }
            this._wasRecording = false;
        },
    };
}
