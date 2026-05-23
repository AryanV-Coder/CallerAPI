# Project Overview — Caller API

## Purpose
General-purpose AI voice calling API. Users POST a phone number, system prompt, and context — the system makes an outbound AI call, conducts a real-time conversation, and returns a summary with sentiment analysis.

## Tech Stack
- **Backend**: FastAPI + Uvicorn (Python 3.11)
- **Telephony**: Twilio Media Streams (WebSocket)
- **STT/TTS**: Sarvam AI (multilingual — Hindi/English/Hinglish + 7 more)
- **LLM**: Groq (Llama 3.3 70B Versatile)
- **VAD**: Silero ONNX (16kHz, 32ms frames)
- **Frontend**: Vanilla JS + Vite 5 (API playground)

## API Endpoints
- `POST /make-call` — initiate a call with system_prompt, context, first_message, voice config, webhook_url
- `GET /call/{call_id}` — retrieve call result (summary, sentiment, transcript, duration)
- `GET /health` — health check

## Core Pipeline
`Twilio μ-law audio → PCM16 (8→16kHz) → Silero VAD → Sarvam STT → Groq LLM → Sarvam TTS → μ-law → Twilio`

## Key Design Decisions
- Bot-speaks-first: uses caller-supplied `first_message` or LLM-generated greeting
- Caller-supplied system prompt: no hardcoded persona — fully configurable per call
- Barge-in: 300ms sustained speech threshold to interrupt bot
- Call finalization: on call end, LLM summarizes transcript + analyzes user sentiment
- Webhook: full result (summary + sentiment + transcript) POSTed to `webhook_url` (e.g. n8n)
- In-memory state: `call_contexts`, `call_histories`, `call_results` dicts keyed by CallSid
- No database: all state is ephemeral, in-memory only
- No authentication: open API (designed for internal/n8n use)

## Module Map
| Module | File(s) | Responsibility |
|--------|---------|---------------|
| Core Pipeline | `app.py` | WebSocket handler, call lifecycle, summary generation, webhook |
| Audio | `audio_utils.py` | μ-law ↔ PCM conversion |
| VAD | `vad_service.py` | Speech detection (Silero) |
| Barge-in | `barge_in.py` | Interruption handling |
| LLM | `groq_services/groq_llm.py` | Groq API chat + call summarization |
| STT | `sarvam_services/sarvam_stt.py` | Speech transcription |
| TTS | `sarvam_services/sarvam_tts.py` | Text-to-speech (REST + WebSocket) |
| Telephony | `twilio_services/twilio_call.py` | Outbound call creation |
| Frontend | `frontend/` | Vite API playground |
