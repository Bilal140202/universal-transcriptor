"""TranslationProvider contract (spec §11).

Rules baked into the contract:
- Translation is batched over CONTEXT WINDOWS, never isolated lines.
- Output maps 1:1 back to segment ids; unmappable output is an error, never a
  silent overwrite.
- Cloud APIs are optional; local providers must work standalone.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import Finding


@dataclass
class TranslationResult:
    segment_id: str
    english: str = ""
    provider: str = ""
    findings: list[Finding] = field(default_factory=list)
    ok: bool = True


class TranslationProvider(ABC):
    """A translation backend. Implementations lazy-import their deps."""

    name: str = "abstract"
    modes: tuple[str, ...] = ()       # e.g. ("LOCAL_NLLB",)

    @abstractmethod
    def translate_batch(self, windows: list, source_language: str) -> list[TranslationResult]:
        """Translate a list of ContextWindow objects.

        Implementations MUST return one TranslationResult per input window, in
        order, with segment_id preserved. Partial failures become findings —
        the result list is never silently truncated.
        """

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name}>"
