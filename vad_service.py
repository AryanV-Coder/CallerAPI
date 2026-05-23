"""
Voice Activity Detection using Silero VAD.
Detects when a user starts and stops speaking in a stream of audio chunks.
"""

import torch
import numpy as np
from silero_vad import load_silero_vad

# Load model once (shared across all calls)
torch.set_num_threads(1)  # Silero VAD is optimized for single-thread
_model = load_silero_vad()

# VAD configuration
SAMPLE_RATE = 16000
WINDOW_SIZE = 512  # 32ms at 16kHz — required by Silero VAD
SPEECH_THRESHOLD = 0.5  # Probability above this = speech
SILENCE_DURATION_MS = 700  # How long silence must last to consider speech ended
SILENCE_CHUNKS = int(SILENCE_DURATION_MS / (WINDOW_SIZE / SAMPLE_RATE * 1000))


class VADProcessor:
    """
    Per-call VAD state manager.

    Feed audio chunks via `process()`. When speech ends after a period of silence,
    returns the accumulated PCM audio buffer ready for STT.
    """

    def __init__(self):
        self.is_speaking = False
        self.bot_is_speaking = False
        self.silent_chunk_count = 0
        self.audio_buffer_16k = bytearray()  # Accumulated PCM 16kHz for STT
        self._vad_buffer = np.array([], dtype=np.float32)  # Partial window accumulator

    def process(self, pcm_16k: bytes, float32: np.ndarray) -> bytes | None:
        """
        Feed a chunk of audio to the VAD.

        Args:
            pcm_16k: 16-bit PCM at 16kHz (for accumulation)
            float32: float32 numpy array at 16kHz (for VAD inference)

        Returns:
            None if speech is ongoing or silence.
            bytes (PCM 16kHz) when speech has ended — the complete utterance.
        """
        # Note: bot_is_speaking check is handled by app.py, which routes
        # audio to BargeInDetector during bot speech instead of this processor.

        # Accumulate partial window data
        self._vad_buffer = np.concatenate([self._vad_buffer, float32])

        # Process complete windows
        while len(self._vad_buffer) >= WINDOW_SIZE:
            window = self._vad_buffer[:WINDOW_SIZE]
            self._vad_buffer = self._vad_buffer[WINDOW_SIZE:]

            # Run VAD inference
            tensor = torch.from_numpy(window)
            speech_prob = _model(tensor, SAMPLE_RATE).item()
            is_speech = speech_prob > SPEECH_THRESHOLD

            if is_speech:
                if not self.is_speaking:
                    print("🎤 Speech started")
                    self.is_speaking = True
                self.silent_chunk_count = 0
            elif self.is_speaking:
                self.silent_chunk_count += 1

        # Always accumulate audio if we're in a speech segment
        if self.is_speaking:
            self.audio_buffer_16k.extend(pcm_16k)

        # Check if speech ended (enough silence after speech)
        if self.is_speaking and self.silent_chunk_count >= SILENCE_CHUNKS:
            print(f"🔇 Speech ended ({len(self.audio_buffer_16k)} bytes collected)")
            utterance = bytes(self.audio_buffer_16k)
            self._reset()
            return utterance

        return None

    def _reset(self):
        """Reset state for next utterance."""
        self.is_speaking = False
        self.silent_chunk_count = 0
        self.audio_buffer_16k = bytearray()
        self._vad_buffer = np.array([], dtype=np.float32)
        # Reset model state for clean next utterance
        _model.reset_states()
