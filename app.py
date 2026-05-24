import os
import json
import asyncio
import base64
import time
import tempfile
import traceback
import httpx
from functools import partial
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from audio_utils import decode_twilio_media, save_pcm_as_wav
from vad_service import VADProcessor
from sarvam_services.sarvam_stt import transcribe_audio
from sarvam_services.sarvam_tts import stream_tts, AUDIO_DIR
from groq_services.groq_llm import chat, summarize_call
from twilio_services.twilio_call import make_call
from barge_in import BargeInDetector, handle_barge_in


app = FastAPI(title="General Caller API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── In-Memory State ─────────────────────────────────────────────────────

# Per-call conversation history (keyed by CallSid)
call_histories: dict[str, list[dict]] = {}

# Per-CallSid context: stores the caller-supplied config for this call
call_contexts: dict[str, dict] = {}

# Completed call results (keyed by CallSid)
# Stores: status, summary, user_sentiment, sentiment_detail, transcript, duration
call_results: dict[str, dict] = {}

# Call start times for duration tracking
call_start_times: dict[str, float] = {}


# ─── Request/Response Models ─────────────────────────────────────────────


class VoiceConfig(BaseModel):
    language: str = "en-IN"
    speaker: str = "shubh"


class MakeCallRequest(BaseModel):
    phone_number: str  # Full E.164 format (e.g. +919876543210)
    system_prompt: str
    context: str = ""
    first_message: str = ""  # If empty, LLM generates greeting from prompt + context
    voice: VoiceConfig = VoiceConfig()
    webhook_url: str = ""


# ─── POST /make-call ─────────────────────────────────────────────────────


@app.post("/make-call")
async def make_call_endpoint(request: MakeCallRequest):
    """
    Initiate an AI voice call.

    1. Calls Twilio → gets unique CallSid
    2. Stores the caller-supplied config (system_prompt, context, etc.) keyed by CallSid
    3. Returns the call_id for tracking
    """
    try:
        call_sid = make_call(request.phone_number)

        # Store the full config for this call
        call_contexts[call_sid] = {
            "system_prompt": request.system_prompt,
            "context": request.context,
            "first_message": request.first_message,
            "voice": {"language": request.voice.language, "speaker": request.voice.speaker},
            "webhook_url": request.webhook_url,
        }

        # Initialize result tracking
        call_results[call_sid] = {"status": "initiated"}

        print(f"📞 Call initiated → {request.phone_number} | CallSid: {call_sid}")
        return {"status": "initiated", "call_id": call_sid}

    except Exception as e:
        print(f"❌ Failed to initiate call to {request.phone_number}: {e}")
        return {"status": "error", "message": str(e)}


# ─── GET /call/{call_id} ────────────────────────────────────────────────


@app.get("/call/{call_id}")
async def get_call_result(call_id: str):
    """
    Retrieve the result of a call by its ID.
    Returns status, summary, sentiment, transcript, and duration.
    """
    if call_id in call_results:
        return {"call_id": call_id, **call_results[call_id]}

    return {"call_id": call_id, "status": "not_found"}


# ─── Twilio Webhook ──────────────────────────────────────────────────────


@app.post("/voice")
async def voice_webhook():
    """
    Twilio calls this when the outbound call is answered.
    Returns TwiML that opens a bidirectional media stream.
    """
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{config.SERVER_URL.replace('https://', '').replace('http://', '')}/media-stream" />
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# ─── Twilio Status Callback ─── THE ONLY PLACE THAT FIRES THE WEBHOOK ───


@app.post("/call-status")
async def call_status_callback(request: Request):
    """
    Twilio calls this when the call ends (status = 'completed').

    This is the ONLY place where we generate the summary and fire the
    webhook. It's a regular HTTP POST — not tied to the WebSocket lifecycle
    — so it always runs to completion.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    call_duration = form.get("CallDuration", "0")

    print(f"📲 [StatusCallback] CallSid={call_sid} Status={call_status} Duration={call_duration}s")

    if not call_sid:
        return Response(content="ok", media_type="text/plain")

    # Skip if already completed (e.g. duplicate callback)
    if call_sid in call_results and call_results[call_sid].get("status") == "completed":
        print(f"ℹ [StatusCallback] Call {call_sid} already completed — skipping")
        return Response(content="ok", media_type="text/plain")

    # Look up webhook URL
    ctx = call_contexts.get(call_sid, {})
    webhook_url = ctx.get("webhook_url", "")

    print(f"🔧 [StatusCallback] Finalizing call {call_sid} | webhook={bool(webhook_url)}")

    # Run full finalization: summary + webhook + cleanup
    await _finalize_call(call_sid, webhook_url, int(call_duration))

    return Response(content="ok", media_type="text/plain")


# ─── Media Stream WebSocket ──────────────────────────────────────────────


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    Bidirectional WebSocket handler for Twilio Media Streams.

    Pipeline:
    0. On stream start → look up caller config → generate/play greeting
    1. Receive μ-law audio chunks from Twilio
    2. Convert to PCM 16kHz → feed to Silero VAD
    3. VAD detects end of speech → save as WAV → Sarvam STT
    4. Groq LLM generates response
    5. Sarvam TTS streams audio → convert to μ-law → send back to Twilio
    6. On stream stop → generate summary + sentiment → POST to webhook
    """
    await websocket.accept()
    print("🔌 WebSocket connected")

    stream_sid = None
    call_sid = None
    vad = VADProcessor()
    resample_state = None
    barge_in_detector = BargeInDetector()
    cancel_event = asyncio.Event()
    webhook_url = ""

    try:
        async for raw_message in websocket.iter_text():
            data = json.loads(raw_message)
            event = data.get("event")

            if event == "connected":
                print("✅ Twilio stream connected")

            elif event == "start":
                stream_sid = data["streamSid"]
                call_sid = data.get("start", {}).get("callSid", "unknown")
                print(f"▶ Stream started | streamSid={stream_sid} | callSid={call_sid}")

                # Track call start time
                call_start_times[call_sid] = time.time()

                # Update status
                if call_sid in call_results:
                    call_results[call_sid]["status"] = "in_progress"

                # Extract webhook URL from context
                ctx = call_contexts.get(call_sid, {})
                webhook_url = ctx.get("webhook_url", "")
                print(f"🔗 Webhook URL for this call: {webhook_url or '(none)'}")

                # --- Bot Speaks First ---
                cancel_event = asyncio.Event()
                asyncio.create_task(
                    _bot_greeting(stream_sid, call_sid, vad, websocket, cancel_event)
                )

            elif event == "media":
                payload = data["media"]["payload"]

                # Decode Twilio audio → PCM 16kHz + float32 for VAD
                pcm_8k, pcm_16k, float32, resample_state = decode_twilio_media(
                    payload, resample_state
                )

                if vad.bot_is_speaking:
                    # During bot speech: check for barge-in
                    triggered = barge_in_detector.check(float32)
                    if triggered:
                        await handle_barge_in(
                            websocket, stream_sid, vad, cancel_event, barge_in_detector
                        )
                        # Feed current chunk to VAD to start capturing the new utterance
                        vad.process(pcm_16k, float32)
                    continue

                # Normal: feed to VAD for utterance detection
                utterance_pcm = vad.process(pcm_16k, float32)

                if utterance_pcm is not None:
                    # Fresh cancel event per utterance
                    cancel_event = asyncio.Event()
                    # Process the complete utterance in background
                    asyncio.create_task(
                        _process_utterance(
                            utterance_pcm, stream_sid, call_sid, vad, websocket, cancel_event
                        )
                    )

            elif event == "mark":
                mark_name = data.get("mark", {}).get("name", "")
                if mark_name == "bot_speech_done":
                    print("✅ Bot finished speaking")
                    vad.bot_is_speaking = False
                    barge_in_detector.reset()

            elif event == "stop":
                print("⏹ Stream stopped — finalizing call...")
                break

    except Exception as e:
        print(f"🔥 WebSocket error: {e}")
        traceback.print_exc()

    finally:
        print(f"🔌 WebSocket disconnected (call_sid={call_sid}). /call-status will handle finalization.")


# ─── Bot Greeting ────────────────────────────────────────────────────────


async def _bot_greeting(
    stream_sid: str,
    call_sid: str,
    vad: VADProcessor,
    websocket: WebSocket,
    cancel_event: asyncio.Event,
):
    """
    Bot speaks first: look up caller config, set up conversation history,
    generate or use a provided greeting, and play it via TTS.
    """
    vad.bot_is_speaking = True

    try:
        # Look up config by this call's unique CallSid
        context = call_contexts.get(call_sid, None)

        if context is None:
            print(f"⚠ No call context found for CallSid {call_sid}")
            vad.bot_is_speaking = False
            return

        system_prompt = context["system_prompt"]
        call_context = context.get("context", "")
        first_message = context.get("first_message", "")
        voice_config = context.get("voice", {})
        language = voice_config.get("language", "en-IN")
        speaker = voice_config.get("speaker", "shubh")

        # Initialize chat history with caller-supplied system prompt
        if call_sid not in call_histories:
            call_histories[call_sid] = []

        history = call_histories[call_sid]
        history.append({"role": "system", "content": system_prompt})

        # Inject context as the first user message (if provided)
        if call_context:
            history.append({
                "role": "user",
                "content": f"Here is the context for this call:\n\n{call_context}",
            })

        # Determine the greeting text
        if first_message:
            # Use the caller-supplied first message directly
            ai_greeting = first_message
            history.append({"role": "assistant", "content": ai_greeting})
            print(f"✅ [Bot Greeting] Using provided first_message: {ai_greeting}")
        else:
            # Let the LLM generate the greeting
            try:
                from groq import Groq
                groq_client = Groq(api_key=config.GROQ_API_KEY)
                response = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=history,
                    stream=False,
                    temperature=0.7,
                    max_tokens=200,
                )
                ai_greeting = response.choices[0].message.content
                history.append({"role": "assistant", "content": ai_greeting})
                print(f"✅ [Bot Greeting] LLM generated: {ai_greeting}")
            except Exception as e:
                print(f"❌ LLM greeting error: {e}")
                ai_greeting = "Hello, thank you for taking my call. How can I help you today?"
                history.append({"role": "assistant", "content": ai_greeting})

        # Stream TTS greeting to Twilio
        async def send_audio_chunk(audio_bytes: bytes):
            payload = base64.b64encode(audio_bytes).decode("utf-8")
            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": payload},
            }
            await websocket.send_json(media_message)

        success = await stream_tts(
            ai_greeting, send_audio_chunk,
            language=language, speaker=speaker,
            cancel_event=cancel_event,
        )

        # Only send mark if TTS wasn't cancelled
        if success:
            mark_message = {
                "event": "mark",
                "streamSid": stream_sid,
                "mark": {"name": "bot_speech_done"},
            }
            await websocket.send_json(mark_message)

    except Exception as e:
        print(f"🔥 Error in bot greeting: {e}")
        vad.bot_is_speaking = False


# ─── Process Utterance ───────────────────────────────────────────────────


async def _process_utterance(
    utterance_pcm: bytes,
    stream_sid: str,
    call_sid: str,
    vad: VADProcessor,
    websocket: WebSocket,
    cancel_event: asyncio.Event,
):
    """
    Process a complete user utterance:
    STT → LLM → streaming TTS → send audio to Twilio
    """
    vad.bot_is_speaking = True

    # Get voice config for this call
    ctx = call_contexts.get(call_sid, {})
    voice_config = ctx.get("voice", {})
    default_language = voice_config.get("language", "en-IN")
    speaker = voice_config.get("speaker", "shubh")

    try:
        # 1. Save PCM as WAV for Sarvam STT
        wav_bytes = save_pcm_as_wav(utterance_pcm, sample_rate=16000)
        tmp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_file.write(wav_bytes)
        tmp_file.close()

        # 2. Transcribe with Sarvam STT
        text, detected_lang = transcribe_audio(tmp_file.name)
        os.remove(tmp_file.name)
        print(f"✅ [Transcribed] {text} (Language: {detected_lang})")

        if not text or text.strip() == "":
            print("⚠ Empty transcription, skipping")
            vad.bot_is_speaking = False
            return

        # 3. Get AI response from Groq LLM
        if call_sid not in call_histories:
            call_histories[call_sid] = []
        ai_reply = chat(text, call_histories[call_sid])
        print(f"✅ [AI Reply] {ai_reply}")

        # 4. Stream TTS audio back to Twilio
        async def send_audio_chunk(audio_bytes: bytes):
            payload = base64.b64encode(audio_bytes).decode("utf-8")
            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": payload},
            }
            await websocket.send_json(media_message)

        success = await stream_tts(
            ai_reply, send_audio_chunk,
            language=detected_lang, speaker=speaker,
            cancel_event=cancel_event,
        )

        # 5. Only send mark if TTS wasn't cancelled
        if success:
            mark_message = {
                "event": "mark",
                "streamSid": stream_sid,
                "mark": {"name": "bot_speech_done"},
            }
            await websocket.send_json(mark_message)
        else:
            vad.bot_is_speaking = False

    except Exception as e:
        print(f"🔥 Error processing utterance: {e}")
        vad.bot_is_speaking = False


# ─── Call Finalization ───────────────────────────────────────────────────


async def _finalize_call(call_sid: str, webhook_url: str, twilio_duration: int = 0):
    """
    Generate summary + sentiment, store result, fire webhook, clean up.
    Called ONLY from /call-status (a normal HTTP handler that always completes).
    """
    if not call_sid or call_sid == "unknown":
        print(f"⚠ _finalize_call skipped: call_sid={call_sid}")
        return

    print(f"🔧 _finalize_call START for {call_sid}")

    try:
        # Calculate duration (prefer our own timer, fallback to Twilio's)
        start_time = call_start_times.pop(call_sid, None)
        duration = int(time.time() - start_time) if start_time else twilio_duration

        # Get the conversation transcript
        history = call_histories.get(call_sid, [])

        # Build a clean transcript (skip system messages)
        transcript = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in history
            if msg["role"] in ("user", "assistant")
        ]

        # Generate summary + sentiment
        if len(transcript) > 0:
            print(f"📊 Generating call summary for {call_sid}...")
            loop = asyncio.get_running_loop()
            analysis = await loop.run_in_executor(
                None, partial(summarize_call, history)
            )
            print(f"📊 Summary generated for {call_sid}")
        else:
            analysis = {
                "summary": "The call ended without any conversation.",
                "user_sentiment": "neutral",
                "sentiment_detail": "No user interaction detected.",
            }

        # Store the result
        result = {
            "status": "completed",
            "summary": analysis["summary"],
            "user_sentiment": analysis["user_sentiment"],
            "sentiment_detail": analysis["sentiment_detail"],
            "transcript": transcript,
            "duration_seconds": duration,
        }
        call_results[call_sid] = result
        print(f"✅ Call {call_sid} finalized | Duration: {duration}s | Sentiment: {analysis['user_sentiment']}")

    except Exception as e:
        print(f"🔥 Error generating summary for {call_sid}: {e}")
        traceback.print_exc()
        result = {
            "status": "completed",
            "summary": "Summary generation failed.",
            "user_sentiment": "neutral",
            "sentiment_detail": "Error during analysis.",
            "transcript": [],
            "duration_seconds": twilio_duration,
        }
        call_results[call_sid] = result

    # Fire webhook (outside try/except so it always runs)
    if webhook_url:
        await _send_webhook(call_sid, result, webhook_url)
    else:
        print(f"ℹ No webhook_url for {call_sid}")

    # Cleanup
    call_histories.pop(call_sid, None)
    call_contexts.pop(call_sid, None)
    print(f"🧹 Cleaned up in-memory state for {call_sid}")


async def _send_webhook(call_id: str, result: dict, webhook_url: str):
    """
    POST the full call result to the webhook URL (e.g. n8n Webhook node).
    Retries up to 3 times with exponential backoff.
    """
    payload = {"call_id": call_id, **result}
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                print(f"✅ Webhook delivered to {webhook_url} | Status: {response.status_code} | Attempt: {attempt}")
                return  # Success — exit immediately

        except Exception as e:
            print(f"❌ Webhook attempt {attempt}/{max_retries} failed ({webhook_url}): {e}")
            if attempt < max_retries:
                wait = 2 ** attempt  # 2s, 4s
                print(f"   ⏳ Retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                print(f"🚨 Webhook delivery FAILED after {max_retries} attempts for call {call_id}")
                traceback.print_exc()


# ─── Audio Serving (fallback) ────────────────────────────────────────────


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    """Serve generated TTS audio files."""
    filepath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(filepath):
        return Response(content="File not found", status_code=404)
    return FileResponse(filepath, media_type="audio/wav")


# ─── Health Check ────────────────────────────────────────────────────────


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "General Caller API"}