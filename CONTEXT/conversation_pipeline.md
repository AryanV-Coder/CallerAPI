# Conversation Pipeline — Detailed Context

## Overview

The conversation pipeline is the core real-time voice interaction system. It handles the entire flow from the moment a call is answered to the moment it ends: audio decoding, voice activity detection, speech-to-text, LLM response generation, text-to-speech, audio streaming back to the caller, and post-call summarization.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  Twilio (Phone Network)                                              │
│  ┌────────────┐     ┌────────────┐                                   │
│  │ Outbound   │────>│ /voice     │  TwiML webhook (POST)             │
│  │ Call       │     │ webhook    │  Returns <Connect><Stream>        │
│  └────────────┘     └─────┬──────┘                                   │
│                           │                                          │
│                    WebSocket /media-stream                            │
│                           │                                          │
│  ┌────────────────────────▼──────────────────────────────────────┐   │
│  │                    app.py — media_stream()                    │   │
│  │                                                               │   │
│  │   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐ │   │
│  │   │ Decode  │───>│  VAD    │───>│  STT    │───>│   LLM    │ │   │
│  │   │ μ-law   │    │ Silero  │    │ Sarvam  │    │   Groq   │ │   │
│  │   └─────────┘    └─────────┘    └─────────┘    └────┬─────┘ │   │
│  │                                                      │       │   │
│  │                                                      ▼       │   │
│  │   ┌──────────┐    ┌──────────────────────────────────────┐   │   │
│  │   │ Encode   │<───│  TTS (Sarvam Streaming WebSocket)    │   │   │
│  │   │ to μ-law │    │  Output: μ-law 8kHz                  │   │   │
│  │   └────┬─────┘    └──────────────────────────────────────┘   │   │
│  │        │                                                      │   │
│  │        ▼  Send audio chunks back to Twilio via WebSocket      │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  On call end: transcript → Groq summarize_call() → webhook POST     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Breakdown

### Phase 0: Bot Speaks First (Greeting)

**File:** `app.py` → `_bot_greeting()`

When the WebSocket stream starts (Twilio `"start"` event), the bot immediately speaks before the user says anything:

1. The `call_sid` from the Twilio stream is used to look up the caller-supplied config from `call_contexts` (populated by `POST /make-call`).
2. The chat history is initialized with:
   - `system` message: The caller-supplied `system_prompt`
   - `user` message: The caller-supplied `context` (if provided)
3. If a `first_message` was provided, the bot uses it directly. Otherwise, the Groq LLM generates a greeting.
4. The greeting text is streamed through Sarvam TTS → audio chunks sent to Twilio.
5. A `"mark"` event (`bot_speech_done`) is sent to Twilio to track when playback ends.

### Phase 1: Receiving Audio from Twilio

**File:** `app.py` → `media_stream()` WebSocket handler

- Twilio sends `"media"` events containing base64-encoded **μ-law 8kHz mono** audio chunks.
- While the bot is speaking (`vad.bot_is_speaking == True`), incoming audio is routed to the barge-in detector instead of VAD.

### Phase 2: Audio Decoding

**File:** `audio_utils.py` → `decode_twilio_media()`

Each Twilio media payload goes through:

1. **Base64 decode** → raw μ-law bytes
2. **μ-law to PCM 16-bit** (`audioop.ulaw2lin`) → 8kHz PCM
3. **Resample 8kHz → 16kHz** (`audioop.ratecv`) → 16kHz PCM (needed for STT and VAD)
4. **PCM to float32** (`numpy`) → normalized float32 array (needed for Silero VAD)

### Phase 3: Voice Activity Detection (VAD)

**File:** `vad_service.py` → `VADProcessor.process()`

- Uses **Silero VAD** (ONNX model, loaded once globally).
- Audio is processed in **512-sample windows** (32ms at 16kHz).
- **Speech threshold:** 0.5
- **Silence duration to end speech:** 700ms (~22 consecutive silent windows)
- When silence is detected after speech, the accumulated PCM buffer is returned as a complete utterance.

### Phase 4: Speech-to-Text (STT)

**File:** `sarvam_services/sarvam_stt.py` → `transcribe_audio()`

1. The accumulated PCM utterance is wrapped into a WAV file in-memory.
2. Sent to **Sarvam AI** (`saaras:v3` model) via REST API.
3. Returns both the **transcript** and the **detected language code**.

### Phase 5: LLM Response Generation

**File:** `groq_services/groq_llm.py` → `chat()`

- The transcribed text is appended to `call_histories[call_sid]`.
- The full conversation history (including system prompt + context) is sent to **Groq** (`llama-3.3-70b-versatile`).
- Parameters: `temperature=0.7`, `max_tokens=200`

### Phase 6: Text-to-Speech (TTS) Streaming

**File:** `sarvam_services/sarvam_tts.py` → `stream_tts()`

1. Opens an **async WebSocket** to Sarvam AI (`bulbul:v3`).
2. Uses the **detected language code** from STT to ensure language consistency.
3. Audio chunks are received and passed to the `on_audio_chunk` callback.

### Phase 7: Sending Audio Back to Twilio

**File:** `app.py` → `send_audio_chunk()` callbacks

1. Each TTS audio chunk is base64-encoded and sent as a Twilio media event.
2. After all chunks, a `"mark"` event is sent. When confirmed, `vad.bot_is_speaking = False`.

### Phase 8: Call Finalization

**File:** `app.py` → `_finalize_call()`

When the Twilio stream sends a `"stop"` event:

1. Calculate call duration from `call_start_times`.
2. Extract the conversation transcript (user + assistant messages).
3. Send transcript to `groq_services/groq_llm.py` → `summarize_call()`.
4. Groq generates: summary, user_sentiment (positive/neutral/negative/mixed), sentiment_detail.
5. Store result in `call_results[call_sid]`.
6. POST full result to `webhook_url` (if provided) via `httpx.AsyncClient`.
7. Clean up `call_histories` and `call_contexts`.

---

## Key Design Decisions

### Why μ-law 8kHz?
Twilio Media Streams natively use μ-law encoding at 8kHz. Sarvam TTS outputs the same format, avoiding server-side re-encoding.

### Why 16kHz internally?
Silero VAD and Sarvam STT both require 16kHz audio.

### Why non-streaming STT but streaming TTS?
- **STT (non-streaming):** VAD collects a complete utterance first, so streaming STT has no benefit.
- **TTS (streaming):** Allows the bot to start speaking before the entire response is generated.

### Bot-speaks-first pattern
The bot initiates conversation because this is an outbound call. It uses the caller-supplied `first_message` or auto-generates a greeting from the system prompt + context.

---

## Files Involved

| File | Role |
|------|------|
| `app.py` | WebSocket handler, utterance processing, bot greeting, call finalization, webhook |
| `audio_utils.py` | μ-law ↔ PCM conversion, resampling, WAV packaging |
| `vad_service.py` | Silero VAD inference, speech boundary detection |
| `sarvam_services/sarvam_stt.py` | Speech-to-text via Sarvam REST API |
| `sarvam_services/sarvam_tts.py` | Text-to-speech via Sarvam streaming WebSocket |
| `groq_services/groq_llm.py` | LLM chat + call summarization/sentiment |
| `config.py` | API keys and server URL |
