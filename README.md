# Caller API

A general-purpose AI voice calling API. Send a system prompt, context, and phone number — the system makes an outbound AI call via Twilio, handles a real-time conversation, and returns a summary with sentiment analysis.

## Features

- **Single API Endpoint**: `POST /make-call` — provide a phone number, system prompt, and context to initiate a call.
- **Bot-Speaks-First**: The AI greets the user first, using your supplied first message or auto-generating one.
- **Multi-Lingual**: Supports English, Hindi, Hinglish, and 7 other Indian languages. Automatically detects the user's language and responds accordingly.
- **Real-Time Barge-In**: Users can interrupt the bot mid-speech. The system detects sustained speech (300ms), stops playback, and starts listening.
- **Call Summary & Sentiment**: After the call ends, the LLM generates a summary and analyzes user sentiment (positive/negative/neutral/mixed).
- **Webhook Support**: Optionally provide a `webhook_url` (e.g. an n8n Webhook node) — the full result is POSTed when the call completes.
- **Call Result Lookup**: `GET /call/{call_id}` — retrieve status, summary, sentiment, transcript, and duration.
- **Web Playground**: A frontend dashboard to test the API visually.

## API Reference

### `POST /make-call`

Initiate an AI voice call.

**Request Body:**
```json
{
  "phone_number": "+919876543210",
  "system_prompt": "You are a polite appointment reminder assistant...",
  "context": "Patient: Rahul\nAppointment: 25 May, 3 PM",
  "first_message": "Hello Rahul, this is a reminder about your appointment.",
  "voice": {
    "language": "en-IN",
    "speaker": "shubh"
  },
  "webhook_url": "https://your-n8n.com/webhook/abc123"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `phone_number` | string | ✅ | Full E.164 format (e.g. `+919876543210`) |
| `system_prompt` | string | ✅ | Defines the AI persona and behavior |
| `context` | string | ❌ | Data the AI should reference during the call |
| `first_message` | string | ❌ | Bot's opening line. If empty, LLM generates it. |
| `voice.language` | string | ❌ | Default TTS language (`en-IN`). Auto-switches based on user speech. |
| `voice.speaker` | string | ❌ | Voice name: `shubh` (male), `advika` (female) |
| `webhook_url` | string | ❌ | URL to receive full call result when call ends |

**Response:**
```json
{ "status": "initiated", "call_id": "CA1234abcd5678efgh" }
```

### `GET /call/{call_id}`

Retrieve the result of a completed call.

**Response:**
```json
{
  "call_id": "CA1234abcd5678efgh",
  "status": "completed",
  "summary": "The user confirmed their appointment for May 25 at 3 PM.",
  "user_sentiment": "positive",
  "sentiment_detail": "The user sounded cooperative and appreciative.",
  "transcript": [
    { "role": "assistant", "content": "Hello Rahul..." },
    { "role": "user", "content": "Yes, I'll be there." }
  ],
  "duration_seconds": 32
}
```

### `GET /health`

Health check endpoint. Returns `{ "status": "ok" }`.

## Webhook Payload

When the call completes, the full result is POSTed to your `webhook_url`:

```json
{
  "call_id": "CA1234abcd5678efgh",
  "status": "completed",
  "summary": "...",
  "user_sentiment": "positive",
  "sentiment_detail": "...",
  "transcript": [...],
  "duration_seconds": 32
}
```

## System Architecture

1. **Call Initiation**: `POST /make-call` → Twilio creates outbound call → returns `call_id`
2. **Bot Greeting**: Call connects → Twilio streams to WebSocket → bot speaks first using your prompt + context
3. **Conversation Loop**: Twilio μ-law audio → PCM 16kHz → Silero VAD → Sarvam STT → Groq LLM → Sarvam TTS → μ-law → Twilio
4. **Barge-In**: User interrupts → 300ms detection → clear Twilio buffer → cancel TTS → listen
5. **Call End**: Transcript → Groq summary + sentiment → stored in memory → webhook POST

## Tech Stack

- **Backend**: FastAPI + Uvicorn (Python 3.11)
- **Telephony**: Twilio Media Streams (WebSocket)
- **STT/TTS**: Sarvam AI (Saaras v3 / Bulbul v3)
- **LLM**: Groq (Llama 3.3 70B Versatile)
- **VAD**: Silero ONNX (16kHz, 32ms frames)
- **Frontend**: Vanilla JS + Vite 5

## Setup

### Requirements
- Python 3.11+
- Twilio Account + Phone Number
- Sarvam AI API Key
- Groq AI API Key

### Installation

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create `.env`:
```env
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+1your_number
SARVAM_API_KEY=your_key
GROQ_API_KEY=your_key
SERVER_URL=https://your-ngrok-domain.ngrok-free.app
```

### Running

```bash
# Expose locally via ngrok
ngrok http 8000

# Start the server
uvicorn app:app --reload --port 8000
```

### Frontend (optional)
```bash
cd frontend
npm install
npm run dev
```