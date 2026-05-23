# Twilio Integration — Detailed Context

## Overview

This document covers how Twilio is used for outbound calling and bidirectional audio streaming. It explains the webhook flow, the WebSocket protocol, and the specific Twilio events the system handles.

---

## Call Lifecycle

```
┌───────────────┐     ┌────────────────┐     ┌──────────────┐
│ Our Server    │     │   Twilio       │     │  User's      │
│ (app.py)      │     │   Cloud        │     │  Phone       │
│               │     │                │     │              │
│ make_call() ──┼────>│ Creates call   │────>│ Phone rings  │
│               │     │ Returns CallSid│     │              │
│               │     │                │     │ User answers │
│               │     │ POST /voice  ──┼────>│              │
│ voice_webhook │<────┤ (asks for      │     │              │
│ returns TwiML │────>│  instructions) │     │              │
│               │     │                │     │              │
│               │     │ Opens WebSocket│     │              │
│ /media-stream │<────┤ to our server  │     │              │
│ (bidirectional│────>│                │<───>│ Live audio   │
│  audio)       │     │                │     │              │
└───────────────┘     └────────────────┘     └──────────────┘
```

---

## Step 1: Initiating the Call

**File:** `twilio_services/twilio_call.py`

```python
def make_call(phone_number: str) -> str:
    call = client.calls.create(
        to=phone_number,                  # Full E.164 format (e.g. +919876543210)
        from_=config.TWILIO_PHONE_NUMBER, # Our Twilio number
        url=f"{config.SERVER_URL}/voice", # Webhook URL
    )
    return call.sid
```

- The `url` parameter tells Twilio: "When the call is answered, make an HTTP POST to this URL to get instructions."
- `call.sid` is the globally unique CallSid, returned immediately (before the phone even rings).
- Phone numbers are expected in full E.164 format (caller provides the country code).

---

## Step 2: The `/voice` Webhook

**File:** `app.py`

When the user picks up, Twilio POSTs to `/voice`. Our server responds with TwiML (Twilio Markup Language):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://our-server.ngrok-free.app/media-stream" />
    </Connect>
</Response>
```

This tells Twilio: "Open a WebSocket connection to our `/media-stream` endpoint and stream the call audio bidirectionally."

---

## Step 3: The WebSocket `/media-stream`

**File:** `app.py` → `media_stream()`

The WebSocket handler processes the following Twilio events:

### `"connected"` Event
Twilio confirms the WebSocket connection is established. Logged only.

### `"start"` Event
The audio stream begins. Contains critical metadata:
```json
{
  "event": "start",
  "streamSid": "MZ...",  // Unique stream identifier
  "start": {
    "callSid": "CA..."   // The CallSid from Step 1
  }
}
```
- `streamSid` is used to send audio back to this specific stream.
- `callSid` is used to look up the caller-supplied config (system_prompt, context, first_message) and manage conversation history.
- This is where `_bot_greeting()` is triggered.

### `"media"` Event
Contains a chunk of audio from the caller:
```json
{
  "event": "media",
  "media": {
    "payload": "base64-encoded-mulaw-audio"
  }
}
```
- The payload is μ-law encoded, 8kHz, mono audio.
- Typically ~160 bytes per chunk (~20ms of audio).
- Ignored while `vad.bot_is_speaking` is True.

### `"mark"` Event
Twilio confirms it has finished playing audio up to a specific mark:
```json
{
  "event": "mark",
  "mark": {
    "name": "bot_speech_done"
  }
}
```
- We send mark events after streaming TTS audio to know when Twilio finishes playback.
- On receiving `bot_speech_done`, we set `vad.bot_is_speaking = False` to resume listening.

### `"stop"` Event
The stream/call has ended. Triggers `_finalize_call()` which generates a summary + sentiment analysis, stores the result, POSTs to the webhook URL (if provided), and cleans up in-memory state.

---

## Sending Audio Back to Twilio

To play audio to the caller, we send media events over the same WebSocket:

```python
media_message = {
    "event": "media",
    "streamSid": stream_sid,
    "media": {"payload": base64_encoded_mulaw}
}
await websocket.send_json(media_message)
```

After sending all audio chunks, we send a mark event:

```python
mark_message = {
    "event": "mark",
    "streamSid": stream_sid,
    "mark": {"name": "bot_speech_done"}
}
await websocket.send_json(mark_message)
```

---

## Environment Configuration

**File:** `config.py` and `.env`

| Variable | Purpose |
|----------|---------|
| `TWILIO_ACCOUNT_SID` | Twilio account identifier |
| `TWILIO_AUTH_TOKEN` | Twilio API authentication |
| `TWILIO_PHONE_NUMBER` | The Twilio phone number that appears as the caller ID |
| `SERVER_URL` | Public HTTPS URL (typically ngrok) where Twilio can reach our server |

### ngrok Requirement

Twilio needs a **public HTTPS URL** to:
1. POST to `/voice` (webhook)
2. Connect WebSocket to `/media-stream`

During development, ngrok exposes the local server:
```bash
ngrok http 8000
# Then set SERVER_URL=https://xxxx.ngrok-free.app in .env
```

---

## Files Involved

| File | Role |
|------|------|
| `twilio_services/twilio_call.py` | `make_call()` — initiates outbound call |
| `app.py` | `/voice` webhook, `/media-stream` WebSocket handler |
| `config.py` | Twilio credentials, server URL |
