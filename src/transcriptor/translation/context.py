"""Context windows — translation must understand surrounding content (spec §11.1)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Segment, TerminologyEntry, TranscriptDocument


@dataclass
class ContextWindow:
    """One translation unit: the current segment plus everything the
    translator needs to disambiguate pronouns, terminology and register."""
    segment: Segment
    previous: Segment | None = None
    next: Segment | None = None
    speaker: str | None = None
    document_title: str | None = None
    source_language: str | None = None
    style: str = "subtitles"                    # subtitles | transcript
    glossary_hits: list[TerminologyEntry] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)

    @property
    def segment_id(self) -> str:
        return self.segment.segment_id


def build_context_windows(
    doc: TranscriptDocument,
    window: int = 1,
    terminology: object | None = None,
    title: str | None = None,
    style: str = "subtitles",
) -> list[ContextWindow]:
    """Build one ContextWindow per segment.

    `terminology` is a TerminologyMemory; entries whose source term appears in
    the segment text are attached as glossary_hits, and the full override map
    rides along as `glossary` so providers can enforce consistency.
    """
    windows: list[ContextWindow] = []
    segs = doc.segments
    glossary = terminology.overrides() if terminology and hasattr(terminology, "overrides") else {}
    for i, seg in enumerate(segs):
        prev_seg = segs[i - window] if i - window >= 0 else None
        next_seg = segs[i + window] if i + window < len(segs) else None
        hits: list[TerminologyEntry] = []
        if terminology is not None and hasattr(terminology, "entries_for"):
            hits = terminology.entries_for(seg.text)
        windows.append(ContextWindow(
            segment=seg, previous=prev_seg, next=next_seg,
            speaker=seg.speaker, document_title=title or doc.title,
            source_language=doc.language, style=style,
            glossary_hits=hits, glossary=glossary,
        ))
    return windows
