"""ASRProvider contract — no model is hard-coded into the application (spec §8)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import TranscriptDocument


@dataclass
class ASRCapabilities:
    provider: str
    languages: str = "multilingual"          # "multilingual" | list of codes
    word_timestamps: bool = False
    diarization: bool = False
    vad: bool = False
    gpu_recommended: bool = False
    min_vram_gb: float | None = None
    license: str | None = None
    notes: str = ""


@dataclass
class TranscribeHints:
    language: str | None = None            # None = auto-detect
    word_timestamps: bool = True
    speakers: int | None = None            # hint from probe/diarization
    temperature_fallback: bool = True         # hallucination defense
    condition_on_previous_text: bool = False  # reduces repetition loops
    extra: dict[str, Any] = field(default_factory=dict)


class ASRProvider(ABC):
    """A speech-recognition backend. Implementations lazy-import their deps."""

    name: str = "abstract"

    @abstractmethod
    def capabilities(self) -> ASRCapabilities:
        """Static capability descriptor used by the router."""

    @abstractmethod
    def available(self) -> bool:
        """True when this backend can run in the current environment."""

    @abstractmethod
    def transcribe(self, audio_path: str | Path, hints: TranscribeHints) -> TranscriptDocument:
        """Transcribe into an authoritative TranscriptDocument.

        Providers SHOULD populate per-segment metadata useful for the
        hallucination scan: no_speech_prob, compression_ratio, avg_logprob.
        """

    def unload(self) -> None:
        """Release any loaded model resources (memory hygiene).

        The pipeline calls this after the ASR stage so a heavy translation
        model can load in the same process without doubling peak RAM.
        Default: no-op for stateless providers.
        """

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name}>"
