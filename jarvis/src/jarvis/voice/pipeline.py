"""Voice pipeline — STT, TTS, VAD, and wake word detection.

The voice pipeline runs as an in-process async pipeline (NOT MCP):
    AudioCapture → VAD → WakeWord → STT → [Agent] → TTS → AudioPlayback

All voice dependencies are optional (install with pip install jarvis[voice]).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Protocol

import structlog

logger = structlog.get_logger()


class VoiceState(str, Enum):
    """Voice pipeline state."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class AudioChunk:
    """A chunk of audio data.

    Attributes:
        data: Raw audio bytes (16-bit PCM, 16kHz mono).
        sample_rate: Sample rate in Hz.
        duration_ms: Duration in milliseconds.
    """

    data: bytes
    sample_rate: int = 16000
    duration_ms: float = 0.0


class STTProvider(Protocol):
    """Speech-to-text provider protocol."""

    async def transcribe(self, audio: AudioChunk) -> str:
        """Transcribe audio to text."""
        ...

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[str]:
        """Transcribe streaming audio to text."""
        ...


class TTSProvider(Protocol):
    """Text-to-speech provider protocol."""

    async def synthesize(self, text: str) -> AudioChunk:
        """Synthesize text to audio."""
        ...

    async def synthesize_stream(self, text: str) -> AsyncIterator[AudioChunk]:
        """Stream synthesized audio."""
        ...


class VADProvider(Protocol):
    """Voice activity detection provider protocol."""

    def is_speech(self, audio: AudioChunk) -> bool:
        """Check if audio chunk contains speech."""
        ...


class WakeWordProvider(Protocol):
    """Wake word detection provider protocol."""

    def detect(self, audio: AudioChunk) -> bool:
        """Check if audio contains the wake word."""
        ...


class VoicePipeline:
    """Voice pipeline manager.

    Coordinates audio capture, VAD, wake word detection, STT, and TTS
    into a continuous pipeline.

    Full implementation in Phase 7. This stub establishes the interface.
    """

    def __init__(self) -> None:
        self._state = VoiceState.IDLE
        self._running = False

    @property
    def state(self) -> VoiceState:
        return self._state

    async def start(self) -> None:
        """Start the voice pipeline."""
        logger.info("voice_pipeline_start_requested")
        # Check if voice dependencies are available
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            logger.warning(
                "voice_dependencies_missing",
                hint="Install with: pip install 'jarvis[voice]'",
            )
            return

        self._running = True
        self._state = VoiceState.LISTENING
        logger.info("voice_pipeline_started")

    async def stop(self) -> None:
        """Stop the voice pipeline."""
        self._running = False
        self._state = VoiceState.IDLE
        logger.info("voice_pipeline_stopped")

    async def interrupt(self) -> None:
        """Interrupt current speech output (barge-in)."""
        if self._state == VoiceState.SPEAKING:
            self._state = VoiceState.LISTENING
            logger.info("voice_barge_in")
