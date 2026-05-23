"""
Barge-In Detection
==================

Allows the user to interrupt the bot mid-speech. When the user starts speaking
while the bot is talking, the bot stops its current audio and starts listening
to the new input.

How it works:
1. While bot_is_speaking = True, app.py feeds audio to BargeInDetector
2. BargeInDetector runs Silero VAD on each chunk and counts sustained speech
3. If sustained speech exceeds BARGE_IN_THRESHOLD_MS (1 second), barge-in fires:
   a. Sends a "clear" event to Twilio to stop the current audio playback
   b. Sets the cancel_event to abort the in-flight TTS stream
   c. Resets VAD state for the new utterance
   d. Sets bot_is_speaking = False
4. app.py then processes the new utterance through the normal STT → LLM → TTS pipeline

Why the bot doesn't hear itself:
- Twilio Media Streams separate inbound (user mic) and outbound (bot TTS) tracks
- The bot's audio is NEVER echoed back on the inbound stream
- Acoustic echo (speakerphone) is handled by Twilio's echo cancellation +
  the 1-second sustained speech threshold filtering out faint leakage
"""

import asyncio
import numpy as np
import torch
from silero_vad import load_silero_vad

# Reuse the global model from vad_service to avoid loading twice
from vad_service import _model as _vad_model, SAMPLE_RATE, WINDOW_SIZE, SPEECH_THRESHOLD

# Minimum sustained speech duration to trigger barge-in (ms)
BARGE_IN_THRESHOLD_MS = 300

# Number of consecutive speech windows needed to trigger barge-in
# Each window is WINDOW_SIZE samples at SAMPLE_RATE → 32ms per window
_WINDOW_DURATION_MS = WINDOW_SIZE / SAMPLE_RATE * 1000  # 32ms
BARGE_IN_SPEECH_WINDOWS = int(BARGE_IN_THRESHOLD_MS / _WINDOW_DURATION_MS)


class BargeInDetector:
    """
    Monitors incoming audio during bot speech to detect user interruptions.

    Unlike the main VADProcessor (which detects end-of-speech for utterance
    collection), this detector looks for *sustained start-of-speech* to
    trigger an immediate interrupt.
    """

    def __init__(self):
        self.consecutive_speech_windows = 0
        self._buffer = np.array([], dtype=np.float32)  # Partial window accumulator

    def check(self, float32: np.ndarray) -> bool:
        """
        Feed an audio chunk and check if barge-in should trigger.

        Args:
            float32: float32 numpy array at 16kHz (same as VAD input)

        Returns:
            True if sustained speech exceeds threshold → barge-in should fire.
            False otherwise.
        """
        self._buffer = np.concatenate([self._buffer, float32])

        triggered = False

        while len(self._buffer) >= WINDOW_SIZE:
            window = self._buffer[:WINDOW_SIZE]
            self._buffer = self._buffer[WINDOW_SIZE:]

            tensor = torch.from_numpy(window)
            speech_prob = _vad_model(tensor, SAMPLE_RATE).item()
            is_speech = speech_prob > SPEECH_THRESHOLD

            if is_speech:
                self.consecutive_speech_windows += 1
                if self.consecutive_speech_windows >= BARGE_IN_SPEECH_WINDOWS:
                    triggered = True
                    break
            else:
                # Reset counter — speech must be *sustained*
                self.consecutive_speech_windows = 0

        return triggered

    def reset(self):
        """Reset state for next bot speech segment."""
        self.consecutive_speech_windows = 0
        self._buffer = np.array([], dtype=np.float32)
        _vad_model.reset_states()


async def handle_barge_in(
    websocket,
    stream_sid: str,
    vad,
    cancel_event: asyncio.Event,
    barge_in_detector: "BargeInDetector",
):
    """
    Execute a barge-in: stop Twilio playback, cancel TTS, reset state.

    Args:
        websocket: The Twilio WebSocket connection.
        stream_sid: The Twilio stream SID.
        vad: The VADProcessor instance — its bot_is_speaking flag is cleared.
        cancel_event: asyncio.Event shared with the TTS streaming task.
        barge_in_detector: The BargeInDetector to reset.
    """
    # 1. Stop Twilio's audio playback buffer
    clear_message = {
        "event": "clear",
        "streamSid": stream_sid,
    }
    await websocket.send_json(clear_message)

    # 2. Cancel the in-flight TTS stream
    cancel_event.set()

    # 3. Reset all state
    vad.bot_is_speaking = False
    vad._reset()
    barge_in_detector.reset()

    print("⚡ Barge-in triggered — bot audio cleared, listening to user")
