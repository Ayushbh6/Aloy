"""Continuous local PCM segmentation; never sends ambient audio to a provider."""

import io
import math
import wave
from dataclasses import dataclass

from aloy.vad import StreamingVoiceDetector


def pcm_wav(pcm: bytes, rate: int = 16000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return output.getvalue()


@dataclass(frozen=True)
class VoiceEvent:
    kind: str
    pcm: bytes = b""
    samples: int = 0


class VoiceSegmenter:
    """Bounded pre-roll and utterances with hysteresis and learner-friendly pauses.

    The detector runs on echo/noise-processed 16 kHz PCM. All inference stays local.
    Incomplete captures are available to the caller on close, never auto-submitted.
    """

    def __init__(self, session_id: str, detector=None, silence_seconds: float = 0.96):
        self.session_id = session_id
        self.detector = detector or StreamingVoiceDetector()
        self.detector.speech_frames = 4  # 128 ms; reject isolated clicks/transients.
        self.detector.silence_frames = math.ceil(silence_seconds / 0.032)
        self.pending = bytearray()
        self.prefix = bytearray()
        self.utterance = bytearray()
        self.speaking = False
        self.total_samples = 0

    def start(self):
        self.detector.start(self.session_id, 16000)
        # Pay lazy local-model compilation before announcing session readiness.
        self.detector.feed(self.session_id, b"\0\0" * 512)
        self.detector.start(self.session_id, 16000)

    def feed(self, pcm: bytes) -> list[VoiceEvent]:
        if len(pcm) % 2 or len(pcm) > 32000:
            raise ValueError("Invalid 16 kHz microphone frame")
        self.pending.extend(pcm)
        events = []
        while len(self.pending) >= 1024:
            block = bytes(self.pending[:1024])
            del self.pending[:1024]
            self.total_samples += 512
            change = self.detector.feed(self.session_id, block)
            if self.speaking:
                self.utterance.extend(block)
            else:
                self.prefix.extend(block)
                del self.prefix[:-12800]  # 400 ms, including the speech-onset confirmation.
            if change == "speech" and not self.speaking:
                self.speaking = True
                self.utterance = self.prefix
                self.prefix = bytearray()
                events.append(VoiceEvent("speech", samples=self.total_samples))
            if self.speaking and (change == "pause" or len(self.utterance) >= 16000 * 2 * 120):
                events.append(VoiceEvent("utterance", bytes(self.utterance), self.total_samples))
                self.utterance.clear()
                self.speaking = False
                self.detector.active = False
                self.detector.hot_frames = 0
        return events

    def close(self) -> bytes:
        self.detector.stop(self.session_id)
        captured = bytes(self.utterance + self.pending) if self.speaking else b""
        self.utterance.clear()
        self.pending.clear()
        self.prefix.clear()
        self.speaking = False
        return captured
