"""Local streaming speech activity for manual capture and continuous sessions."""

import math

from aloy.models import model_path


class StreamingVoiceDetector:
    """Classify microphone PCM in order without storing the stream."""

    def __init__(self) -> None:
        self.model = None
        self.session_id: str | None = None
        self.state = None
        self.buffer = None
        self.active = False
        self.hot_frames = 0
        self.quiet_frames = 0
        self.speech_frames = 2
        self.silence_frames = 38

    def start(self, session_id: str, sample_rate: int) -> None:
        if not session_id or not 8000 <= sample_rate <= 96000:
            raise ValueError("Invalid voice monitor session")
        if self.model is None:
            from mlx_audio.vad.utils import load_model

            self.model = load_model(model_path("vad"))
        import numpy as np

        self.session_id = session_id
        self.sample_rate = sample_rate
        self.state = None
        self.buffer = np.empty(0, dtype=np.float32)
        self.active = False
        self.hot_frames = 0
        self.quiet_frames = 0

    def stop(self, session_id: str) -> None:
        if session_id == self.session_id:
            self.session_id = None
            self.state = None
            self.buffer = None
            self.active = False

    def feed(self, session_id: str, pcm: bytes) -> str | None:
        if session_id != self.session_id or not pcm:
            return None
        import numpy as np
        from scipy.signal import resample_poly

        if len(pcm) % 2:
            raise ValueError("Microphone PCM must contain whole 16-bit samples")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if self.sample_rate != 16000:
            divisor = math.gcd(self.sample_rate, 16000)
            samples = resample_poly(samples, 16000 // divisor, self.sample_rate // divisor)
        self.buffer = np.concatenate((self.buffer, samples))
        event = None
        while len(self.buffer) >= 512:
            chunk, self.buffer = self.buffer[:512], self.buffer[512:]
            probability, self.state = self.model.feed(chunk, self.state, sample_rate=16000)
            change = self._classify(float(probability.item()))
            if change:
                event = change
        return event

    def _classify(self, probability: float) -> str | None:
        if probability >= 0.55:
            self.hot_frames += 1
            self.quiet_frames = 0
            if not self.active and self.hot_frames >= self.speech_frames:
                self.active = True
                return "speech"
        elif probability < 0.30:
            self.quiet_frames += 1
            self.hot_frames = 0
            if self.active and self.quiet_frames >= self.silence_frames:
                self.active = False
                return "pause"
        else:
            self.hot_frames = 0
            self.quiet_frames = 0
        return None
