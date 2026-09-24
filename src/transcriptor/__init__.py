"""transcriptor — Universal Media Transcription Engine.

Verified acquisition → probe → timing-safe audio → routed ASR → word alignment
→ context-aware translation → terminology control → QA → professional subtitles.

Authoritative temporal structures live in `models`.
"""
__version__ = "0.1.0"

from .models import (
                     AcquisitionRecord,
                     Finding,
                     MediaManifest,
                     Segment,
                     StreamInfo,
                     TranscriptDocument,
                     Word,
)

__all__ = [
                     "AcquisitionRecord",
                     "Finding",
                     "MediaManifest",
                     "Segment",
                     "StreamInfo",
                     "TranscriptDocument",
                     "Word",
                     "__version__",
]
