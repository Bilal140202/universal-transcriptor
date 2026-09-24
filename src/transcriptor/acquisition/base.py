"""Acquisition contracts: adapters, exceptions, and the MediaObject result.

The acquisition package is intentionally standalone — it must never import from
ASR/translation modules so it can be split out as a `media-agent` library later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class AcquisitionError(Exception):
    """Base class for acquisition failures with human-readable reasons."""


class SourceNotSupported(AcquisitionError):
    """No registered adapter can handle this URL."""


class AccessDenied(AcquisitionError):
    """The source exists but denies access (auth, permissions, quota)."""


class VerificationFailed(AcquisitionError):
    """The download completed but did not verify as media."""


@dataclass
class MediaObject:
    """A verified, probed local media object with full provenance."""
    path: Path
    record: Any                                  # AcquisitionRecord
    manifest: Any | None = None               # MediaManifest (filled by probe stage)
    extras: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    """Contract for one source family (Google Drive, HTTP, YouTube, …)."""

    name: str = "abstract"
    priority: int = 100                          # lower wins in the resolver

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True when this adapter claims the URL."""

    @abstractmethod
    def resolve(self, url: str, dest_dir: Path, **opts: Any) -> MediaObject:
        """Acquire, verify and return the local media object.

        Implementations MUST: stream to disk (never read_all), verify the
        response before treating it as media, and fill an AcquisitionRecord.
        """

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r}>"
