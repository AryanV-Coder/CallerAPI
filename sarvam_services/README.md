# Sarvam Services

This directory encompasses all interactions with the Sarvam AI API, responsible for managing the bidirectional audio interpretation required by the service core.

## Overview
Because the service requires low-latency Indian language processing, Sarvam provides the primary translation layer, executing localized Speech-to-Text (STT) and synthesizing natural Text-to-Speech (TTS).

## Implementation Details

### `sarvam_stt.py`
- Exposes `transcribe_audio(audio_filepath: str) -> str`.
- Currently operates as a non-streaming REST operation.
- The `app.py` pipeline utilizes `vad_service.py` to identify speech boundary windows, saves that audio payload as a temporary WAV file, and posts it to this module to retrieve an accurate text transaction via the `saaras:v3` model.

### `sarvam_tts.py`
- Operates primarily using the `stream_tts` method connecting to the `AsyncSarvamAI` endpoint.
- **WebSocket Streaming:** Configures continuous connections instructing the Sarvam AI endpoint (`bulbul:v3`) to generate streaming response chunks. 
- **Format Configuration:** Dictates the return logic explicitly requesting the `mulaw` codec and `8000` speech sample rate, ensuring incoming data requires zero conversion routing before traversing the Twilio socket loop.
- Additionally houses `text_to_speech`, providing legacy REST payload processing to disk for alternative flow execution.