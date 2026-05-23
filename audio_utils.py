"""
Audio format conversion utilities for Twilio Media Streams ↔ Sarvam AI.

Twilio sends/expects: μ-law 8kHz mono, base64 encoded
Sarvam STT expects:   PCM 16-bit WAV
Silero VAD expects:   PCM float32 16kHz mono
"""

import audioop
import base64
import io
import struct
import wave
import numpy as np


def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    """Convert μ-law encoded bytes to 16-bit linear PCM (8kHz)."""
    return audioop.ulaw2lin(mulaw_bytes, 2)


def pcm16_8k_to_16k(pcm_bytes: bytes, state=None) -> tuple[bytes, object]:
    """
    Resample 16-bit PCM from 8kHz to 16kHz.
    Pass `state` from previous call for seamless chunk-by-chunk resampling.
    Returns (resampled_bytes, new_state).
    """
    resampled, new_state = audioop.ratecv(pcm_bytes, 2, 1, 8000, 16000, state)
    return resampled, new_state


def pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert 16-bit PCM bytes to float32 numpy array (range -1.0 to 1.0)."""
    int16_array = np.frombuffer(pcm_bytes, dtype=np.int16)
    return int16_array.astype(np.float32) / 32768.0


def pcm_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit linear PCM to μ-law encoded bytes."""
    return audioop.lin2ulaw(pcm_bytes, 2)


def pcm16k_to_8k(pcm_bytes: bytes, state=None) -> tuple[bytes, object]:
    """
    Downsample 16-bit PCM from 16kHz to 8kHz.
    Returns (downsampled_bytes, new_state).
    """
    downsampled, new_state = audioop.ratecv(pcm_bytes, 2, 1, 16000, 8000, state)
    return downsampled, new_state


def save_pcm_as_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """
    Wrap raw 16-bit PCM bytes into a WAV file (in-memory).
    Returns the complete WAV file as bytes.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def decode_twilio_media(payload: str, resample_state=None) -> tuple[bytes, bytes, np.ndarray, object]:
    """
    Decode a Twilio media payload (base64 μ-law) into all needed formats.

    Returns:
        pcm_8k:  16-bit PCM at 8kHz (for accumulating raw audio)
        pcm_16k: 16-bit PCM at 16kHz (for STT)
        float32: float32 numpy array at 16kHz (for VAD)
        new_state: resample state for next call
    """
    mulaw_bytes = base64.b64decode(payload)
    pcm_8k = mulaw_to_pcm16(mulaw_bytes)
    pcm_16k, new_state = pcm16_8k_to_16k(pcm_8k, resample_state)
    float32 = pcm16_to_float32(pcm_16k)
    return pcm_8k, pcm_16k, float32, new_state


def encode_for_twilio(pcm_bytes: bytes, sample_rate: int = 8000) -> str:
    """
    Convert PCM audio to base64-encoded μ-law for Twilio.
    If sample_rate != 8000, downsamples first.
    Returns base64 string ready for Twilio media payload.
    """
    if sample_rate != 8000:
        pcm_bytes, _ = pcm16k_to_8k(pcm_bytes)
    mulaw_bytes = pcm_to_mulaw(pcm_bytes)
    return base64.b64encode(mulaw_bytes).decode("utf-8")
