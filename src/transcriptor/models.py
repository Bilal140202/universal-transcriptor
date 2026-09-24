"""Authoritative data models for the Universal Media Transcription Engine.

The temporal structures defined here (``MediaManifest``, ``TranscriptDocument``,
``Word``, ``Segment``) are the single source of truth for timing across every
pipeline stage. Downstream stages may regroup text but must never rewrite time.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

@dataclass
class StreamInfo:
    """One stream as reported by the media probe."""
    index: int
    kind: str                      # "video" | "audio" | "subtitle"
    codec: str | None = None
    language: str | None = None
    channels: int | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: str | None = None
    duration: float | None = None
    default: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaManifest:
    """Probe result for one local media object. Ground truth for timing."""
    path: str
    container: str | None = None
    duration: float | None = None
    size_bytes: int | None = None
    video_streams: list[StreamInfo] = field(default_factory=list)
    audio_streams: list[StreamInfo] = field(default_factory=list)
    subtitle_streams: list[StreamInfo] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    language_hints: list[str] = field(default_factory=list)
    integrity: dict[str, Any] = field(default_factory=dict)

    @property
    def best_audio_index(self) -> int | None:
        return select_best_audio(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_audio_stream(s: StreamInfo) -> float:
    """Heuristic quality score for an audio stream (higher = better).

    Considers channels, bitrate, declared language, default disposition and
    codec preference — the criteria the spec lists for intelligent selection.
    """
    score = 0.0
    if s.channels:
        score += min(s.channels, 6) * 2          # stereo+ beats mono, cap 6ch
    if s.bit_rate:
        try:
            score += min(int(s.bit_rate), 512_000) / 64_000
        except (TypeError, ValueError):
            pass
    if s.language and s.language.lower() not in ("und", ""):
        score += 3
    if s.default:
        score += 2
    score += {"flac": 3, "alac": 3, "aac": 2, "mp3": 1.5, "opus": 2.5,
              "pcm_s16le": 3, "pcm_s24le": 3.5, "ac3": 1, "eac3": 1.5,
              "dts": 1.5, "vorbis": 1.5}.get((s.codec or "").lower(), 0)
    return score


def select_best_audio(manifest: MediaManifest) -> int | None:
    """Index of the best audio stream, or None when the media is silent."""
    if not manifest.audio_streams:
        return None
    return max(manifest.audio_streams, key=score_audio_stream).index


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float = 1.0
    speaker: str | None = None
    language: str | None = None


@dataclass
class Segment:
    segment_id: str
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None
    language: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)   # no_speech_prob, compression_ratio, …

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class TranscriptDocument:
    document_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    source_media_id: str | None = None
    language: str | None = None
    segments: list[Segment] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def words(self) -> list[Word]:
        return [w for seg in self.segments for w in seg.words]

    @property
    def duration(self) -> float | None:
        return max((s.end for s in self.segments), default=None)

    def sorted(self) -> TranscriptDocument:
        self.segments.sort(key=lambda s: s.start)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Acquisition records
# ---------------------------------------------------------------------------

@dataclass
class AcquisitionRecord:
    """Full provenance of one acquisition attempt (spec §4.2)."""
    source_url: str
    resolved_url: str | None = None
    source_type: str | None = None          # gdrive | http | youtube | file
    file_name: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    checksum_sha256: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    download_seconds: float | None = None
    success: bool = False
    error: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# QA findings
# ---------------------------------------------------------------------------

SEVERITY_INFO, SEVERITY_WARN, SEVERITY_ERROR = "info", "warn", "error"


@dataclass
class Finding:
    code: str                    # e.g. "HALLUCINATION_REPETITION", "TEMPORAL_OVERLAP"
    severity: str                # info | warn | error
    message: str
    segment_id: str | None = None
    cue_index: int | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------

@dataclass
class TerminologyEntry:
    source_term: str
    english_term: str
    confidence: float = 1.0
    context: str | None = None
    occurrences: int = 0
    user_override: bool = False


@dataclass
class StageRecord:
    """Outcome of one pipeline stage."""
    name: str
    status: str = "pending"                  # pending | ok | failed | skipped
    seconds: float | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
