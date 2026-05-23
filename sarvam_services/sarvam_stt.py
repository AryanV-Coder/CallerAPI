import os
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError
import config


client = SarvamAI(api_subscription_key=config.SARVAM_API_KEY)


def transcribe_audio(audio_path: str) -> tuple[str, str]:
    """
    Transcribe audio from a file path using Sarvam AI's Saaras model.
    Returns a tuple of (transcribed_text, language_code).
    On failure, returns ("", "en-IN").
    """
    try:
        with open(audio_path, "rb") as f:
            response = client.speech_to_text.transcribe(
                file=f,
                model="saaras:v3",
                mode="transcribe",
            )

        lang = response.language_code or "en-IN"
        print(f"[Sarvam STT] Detected language: {lang}")
        return response.transcript, lang

    except ApiError as e:
        if e.status_code == 429:
            print("🛑 RATE LIMIT: Sarvam STT API — too many requests")
        else:
            print(f"❌ Sarvam STT API Error ({e.status_code}): {e.body}")
        return "", "en-IN"

    except Exception as e:
        print(f"❌ Unexpected Sarvam STT Error: {e}")
        return "", "en-IN"
