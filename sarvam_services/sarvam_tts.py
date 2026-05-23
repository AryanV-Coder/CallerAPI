import asyncio
import base64
import os
import uuid
from typing import Callable, Awaitable
from sarvamai import SarvamAI, AsyncSarvamAI
from sarvamai import AudioOutput, EventResponse
from sarvamai.core.api_error import ApiError
import config


client = SarvamAI(api_subscription_key=config.SARVAM_API_KEY)

# Directory to store generated audio files (for REST TTS, kept for fallback)
AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "audio_files")
os.makedirs(AUDIO_DIR, exist_ok=True)


def text_to_speech(text: str, language: str = "en-IN", speaker: str = "shubh") -> str | None:
    """
    Convert text to speech using Sarvam AI (non-streaming REST API).
    Saves the audio as a WAV file and returns the filename.
    Returns None on failure.
    """
    try:
        response = client.text_to_speech.convert(
            text=text,
            target_language_code=language,
            speaker=speaker,
            model="bulbul:v3",
            speech_sample_rate=8000,
            output_audio_codec="wav",
        )

        if not response.audios:
            print("❌ Sarvam TTS returned no audio")
            return None

        audio_bytes = base64.b64decode(response.audios[0])
        filename = f"{uuid.uuid4().hex}.wav"
        filepath = os.path.join(AUDIO_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(audio_bytes)

        print(f"✅ [Sarvam TTS] Saved audio: {filename}")
        return filename

    except ApiError as e:
        if e.status_code == 429:
            print("🛑 RATE LIMIT: Sarvam TTS API — too many requests")
        else:
            print(f"❌ Sarvam TTS API Error ({e.status_code}): {e.body}")
        return None

    except Exception as e:
        print(f"❌ Unexpected Sarvam TTS Error: {e}")
        return None


async def stream_tts(
    text: str,
    on_audio_chunk: Callable[[bytes], Awaitable[None]],
    language: str = "en-IN",
    speaker: str = "shubh",
    cancel_event: "asyncio.Event | None" = None,
) -> bool:
    """
    Stream text-to-speech using Sarvam AI WebSocket API.
    Calls `on_audio_chunk(raw_audio_bytes)` for each audio chunk received.

    Args:
        text: The text to convert to speech.
        on_audio_chunk: Async callback receiving raw audio bytes for each chunk.
        language: Target language code.
        speaker: Speaker voice name.
        cancel_event: If set, TTS streaming is aborted immediately (used by barge-in).

    Returns:
        True if successful, False on error or cancellation.
    """
    async_client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    try:
        async with async_client.text_to_speech_streaming.connect(
            model="bulbul:v3", send_completion_event=True
        ) as ws:
            await ws.configure(
                target_language_code=language,
                speaker=speaker,
                output_audio_codec="mulaw",
                speech_sample_rate=8000,
            )

            await ws.convert(text)
            await ws.flush()

            chunk_count = 0
            cancelled = False
            async for message in ws:
                # Check for barge-in cancellation
                if cancel_event is not None and cancel_event.is_set():
                    print("🛑 [Sarvam TTS] Cancelled by barge-in")
                    cancelled = True
                    break

                if isinstance(message, AudioOutput):
                    chunk_count += 1
                    audio_bytes = base64.b64decode(message.data.audio)
                    await on_audio_chunk(audio_bytes)

                elif isinstance(message, EventResponse):
                    if message.data.event_type == "final":
                        break

            if cancelled:
                print(f"🛑 [Sarvam TTS Streaming] Aborted after {chunk_count} chunks")
                return False

            print(f"✅ [Sarvam TTS Streaming] Sent {chunk_count} chunks")
            return True

    except ApiError as e:
        if e.status_code == 429:
            print("🛑 RATE LIMIT: Sarvam TTS Streaming API")
        else:
            print(f"❌ Sarvam TTS Streaming Error ({e.status_code}): {e.body}")
        return False

    except Exception as e:
        print(f"❌ Unexpected Sarvam TTS Streaming Error: {e}")
        return False
