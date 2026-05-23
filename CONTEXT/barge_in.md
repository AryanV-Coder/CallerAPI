# Barge-In (User Interruption) — Detailed Context

## Overview

Barge-in allows the user to interrupt the bot mid-speech during a Twilio Media Streams call. When the user starts speaking while the bot is talking, the system detects sustained speech, immediately stops the bot's audio playback, cancels any in-flight TTS streaming, and begins processing the user's new input through the normal STT → LLM → TTS pipeline.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│  During Bot Speech — Barge-In Detection Flow                             │
│                                                                          │
│   Twilio inbound audio                                                   │
│        │                                                                 │
│        ▼                                                                 │
│   ┌─────────────────┐                                                    │
│   │  decode_twilio   │  (audio_utils.py — same as normal pipeline)       │
│   │  media → float32 │                                                   │
│   └────────┬────────┘                                                    │
│            │                                                             │
│            ▼                                                             │
│   ┌─────────────────────┐    NO     ┌──────────────────┐                │
│   │ BargeInDetector      │─────────▶│ continue (skip)   │                │
│   │ .check(float32)      │          │ wait for more     │                │
│   │                      │          │ audio chunks      │                │
│   │ Sustained speech     │          └──────────────────┘                │
│   │ > 1 second?          │                                               │
│   └────────┬─────────────┘                                               │
│            │ YES                                                         │
│            ▼                                                             │
│   ┌─────────────────────────────────────────────────┐                   │
│   │  handle_barge_in()                               │                   │
│   │  1. Send {"event":"clear"} to Twilio → stops     │                   │
│   │     audio playback immediately                   │                   │
│   │  2. cancel_event.set() → aborts stream_tts()     │                   │
│   │  3. vad.bot_is_speaking = False                  │                   │
│   │  4. vad._reset() + barge_in_detector.reset()     │                   │
│   └────────┬────────────────────────────────────────┘                   │
│            │                                                             │
│            ▼                                                             │
│   ┌──────────────────────────────────────────┐                          │
│   │  Normal pipeline resumes:                 │                          │
│   │  VAD collects utterance → STT → LLM → TTS│                          │
│   └──────────────────────────────────────────┘                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Breakdown

### Phase 1: Audio Routing During Bot Speech

**File:** `app.py` → `media_stream()` WebSocket handler

Previously, all inbound audio was discarded (`continue`) while `vad.bot_is_speaking == True`. Now:

1. Audio is always decoded via `decode_twilio_media()` regardless of bot state.
2. If `vad.bot_is_speaking` is `True`, the float32 audio is fed to `BargeInDetector.check()` instead of `VADProcessor.process()`.
3. If `vad.bot_is_speaking` is `False`, audio flows to the normal VAD pipeline as before.

### Phase 2: Sustained Speech Detection

**File:** `barge_in.py` → `BargeInDetector.check()`

The detector uses the same Silero VAD model (shared global instance from `vad_service.py`) to run inference on each 32ms window:

1. Audio is accumulated in a buffer until a full 512-sample window is available.
2. Each window is scored for speech probability (0.0 to 1.0).
3. If `speech_prob > 0.5` (same threshold as main VAD), the `consecutive_speech_windows` counter increments.
4. If any window is non-speech, the counter **resets to zero** — speech must be sustained/continuous.
5. When the counter reaches ~9 windows (300ms / 32ms per window), `check()` returns `True`.

**Why 300ms?** This is responsive — it catches interruptions within about one spoken word while still filtering out:
- Acoustic echo from speakerphone (typically < 100ms)
- Very brief noise bursts
This provides a good balance between responsiveness and false-positive prevention.

### Phase 3: Executing the Interrupt

**File:** `barge_in.py` → `handle_barge_in()`

When barge-in triggers:

1. **Twilio clear event**: Sends `{"event": "clear", "streamSid": ...}` over the WebSocket. This tells Twilio to immediately flush its audio playback buffer — the user stops hearing the bot mid-word.

2. **Cancel TTS stream**: Sets the shared `asyncio.Event` (`cancel_event`). The `stream_tts()` function in `sarvam_tts.py` checks this event every iteration of its audio chunk loop. When set, it breaks out immediately, stopping audio generation.

3. **Reset state**:
   - `vad.bot_is_speaking = False` — re-enables normal audio processing
   - `vad._reset()` — clears any accumulated audio buffer and VAD model state
   - `barge_in_detector.reset()` — clears the speech counter and buffer

4. **Capture new utterance**: Back in `app.py`, the current audio chunk is fed to `vad.process()` to begin accumulating the user's new utterance.

### Phase 4: TTS Cancellation

**File:** `sarvam_services/sarvam_tts.py` → `stream_tts()`

The `stream_tts()` function accepts an optional `cancel_event: asyncio.Event`:

- Before processing each audio chunk from Sarvam's WebSocket, it checks `cancel_event.is_set()`.
- If set, it prints a cancellation log and breaks out of the loop.
- The `async with` context manager ensures the Sarvam WebSocket is properly closed.
- Returns `False` on cancellation (vs `True` on success).

### Phase 5: Post-Interrupt Flow

After barge-in, the system is in the same state as if the bot had finished speaking normally:
- `bot_is_speaking = False`
- VAD is clean and ready to collect a new utterance
- The user's speech is being accumulated by `VADProcessor.process()`
- When the user finishes speaking (700ms silence), the normal STT → LLM → TTS pipeline runs

---

## Why the Bot Doesn't Hear Itself

A common concern with barge-in is: **will the VAD detect the bot's own voice and trigger a false interrupt?**

**No.** Twilio Media Streams uses separate inbound and outbound audio tracks:
- **Outbound track** (server → Twilio → phone): Bot's TTS audio goes here
- **Inbound track** (phone → Twilio → server): User's microphone audio comes here

The bot's audio is **never echoed back** on the inbound stream. They are completely isolated channels.

**Acoustic echo** (when the user is on speakerphone and the phone mic picks up the bot's voice) is handled by:
1. **Twilio's built-in echo cancellation** on the telephony side
2. **The 1-second sustained speech threshold** — echo is typically faint and intermittent, nowhere near 1 second of sustained high-confidence speech

---

## Configuration

| Constant | File | Value | Description |
|----------|------|-------|-------------|
| `BARGE_IN_THRESHOLD_MS` | `barge_in.py` | `300` | Minimum sustained speech to trigger interrupt |
| `BARGE_IN_SPEECH_WINDOWS` | `barge_in.py` | `~9` | Computed: threshold / window duration (32ms) |
| `SPEECH_THRESHOLD` | `vad_service.py` | `0.5` | VAD speech probability threshold (shared) |
| `WINDOW_SIZE` | `vad_service.py` | `512` | Silero VAD window (32ms at 16kHz) |

---

## Files Involved

| File | Role |
|------|------|
| `barge_in.py` | `BargeInDetector` class + `handle_barge_in()` interrupt handler |
| `app.py` | Routes audio to barge-in detector during bot speech, orchestrates the interrupt |
| `vad_service.py` | Core VAD — `bot_is_speaking` guard removed (now handled by `app.py`) |
| `sarvam_services/sarvam_tts.py` | TTS streaming with `cancel_event` support for mid-stream abort |

---

## Key Design Decisions

### Why a separate BargeInDetector (not reuse VADProcessor)?
VADProcessor detects **end-of-speech** (silence after speech) for utterance collection. BargeInDetector detects **sustained start-of-speech** for interruption. These are opposite goals — VADProcessor waits for silence, BargeInDetector fires on continuous speech. Mixing them would create fragile state management.

### Why `asyncio.Event` for cancellation?
It's lightweight, thread-safe, and integrates naturally with async code. Each utterance/greeting gets a fresh `Event`, so there's no stale cancellation state between turns.

### Why 300ms threshold?
This provides a responsive barge-in experience — the bot stops within about one spoken word (~300ms). It's fast enough to feel natural while still filtering out echo and noise. This can be tuned higher (e.g., 500ms–1000ms) if false triggers are an issue.
