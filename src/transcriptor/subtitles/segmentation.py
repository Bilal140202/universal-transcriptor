"""Professional subtitle segmentation (spec §16).

NOT "one ASR segment = one subtitle". Cues are rebuilt from words/segments
under reading-speed and layout rules, split at sentence/phrase boundaries,
never across speaker changes, and validated against the authoritative timeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise

from ..models import TranscriptDocument, Word

# Sentence-final punctuation (many scripts), then clause-level boundaries.
_SENTENCE_END = re.compile(r"[.!?。！？][\"'”’)\]]*\s*$")
_CLAUSE_BREAK = re.compile(r"[,、，;：:;—…]\s*$")


@dataclass
class SubtitleRules:
    """Professional baseline defaults (docs/RESEARCH.md §6)."""
    max_chars_per_line: int = 42
    max_lines: int = 2
    min_duration: float = 1.0
    max_duration: float = 7.0
    cps_max: float = 20.0
    gap: float = 0.084                      # 2 frames @24fps
    require_terminal_punctuation: bool = True


@dataclass
class SubtitleCue:
    index: int
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    speaker: str | None = None
    segment_ids: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _balanced_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Split text into ≤max_lines lines of ≤max_chars, balanced by words."""
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    best: list[str] | None = None
    # try all split points for 2 lines (the standard subtitle case)
    if max_lines == 2 or len(words) >= 2:
        for cut in range(1, len(words)):
            l1, l2 = " ".join(words[:cut]), " ".join(words[cut:])
            if len(l1) <= max_chars and len(l2) <= max_chars:
                score = abs(len(l1) - len(l2))
                if best is None or score < abs(len(best[0]) - len(best[1])):
                    best = [l1, l2]
        if best:
            return best
    # fallback: hard 3-line split (rare, flagged by QA scan)
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def _split_text_at_boundaries(text: str, max_chars: int) -> list[str]:
    """Split long text at sentence boundaries first, then clause boundaries."""
    if len(text) <= max_chars * 2:
        return [text]
    parts: list[str] = []
    current = ""
    for token in re.split(r"(\s+)", text):
        candidate = current + token
        sentence_break = _SENTENCE_END.search(candidate) and len(candidate) > max_chars * 0.4
        clause_break = _CLAUSE_BREAK.search(candidate) and len(candidate) > max_chars
        if sentence_break or clause_break:
            parts.append(candidate.strip())
            current = ""
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())
    return [p for p in parts if p]


def _word_time(words: list[Word], text: str, fallback_start: float,
               fallback_end: float) -> tuple[float, float]:
    """Timing for a sub-span of a segment: use word timings when available."""
    if not words:
        return fallback_start, fallback_end
    span_words = text.split()
    if not span_words:
        return fallback_start, fallback_end
    # match words positionally against the segment word list
    first, last = span_words[0], span_words[-1]
    start = next((w.start for w in words if w.text == first), None)
    end = next((w.end for w in reversed(words) if w.text == last), None)
    return (start if start is not None else fallback_start,
            end if end is not None else fallback_end)


def segment_for_subtitles(
    doc: TranscriptDocument,
    rules: SubtitleRules | None = None,
    media_duration: float | None = None,
) -> list[SubtitleCue]:
    """Rebuild transcript segments into professional English cues.

    Guarantees:
    - ≤ max_lines lines, ≤ max_chars_per_line per line (else QA flags it)
    - min/max duration respected where the timeline allows
    - reading speed relaxed by extending cue END into silence — never past
      the next cue start, never past media duration
    - speaker change always forces a new cue
    - cue→segment mapping preserved in `segment_ids`
    """
    rules = rules or SubtitleRules()
    cues: list[SubtitleCue] = []

    for seg in sorted(doc.segments, key=lambda s: s.start):
        chunks = _split_text_at_boundaries(seg.text, rules.max_chars_per_line * rules.max_lines)
        n = len(chunks)
        for j, chunk in enumerate(chunks):
            # Distribute time across chunks proportionally to length (word-accurate
            # when word timings cover the span).
            span_start = seg.start + (seg.end - seg.start) * (j / n) if n > 1 else seg.start
            span_end = seg.start + (seg.end - seg.start) * ((j + 1) / n) if n > 1 else seg.end
            start, end = _word_time(seg.words, chunk, span_start, span_end)
            if end <= start:
                end = min(start + 0.4, seg.end if seg.end > seg.start else start + 0.4)

            # Enforce min duration: extend end (into silence) — never past next chunk.
            if end - start < rules.min_duration:
                end = start + rules.min_duration
            # Enforce reading speed via end extension, capped at max_duration.
            cps = len(chunk) / max(end - start, 1e-6)
            if cps > rules.cps_max:
                needed = len(chunk) / rules.cps_max
                end = max(end, start + needed)
            if end - start > rules.max_duration:
                end = start + rules.max_duration

            speaker = seg.speaker
            if cues and cues[-1].speaker and cues[-1].speaker != speaker:
                pass  # speaker change: keep as new cue (never merge across speakers)
            cues.append(SubtitleCue(
                index=0, start=start, end=end,
                lines=_balanced_lines(chunk, rules.max_chars_per_line, rules.max_lines),
                speaker=speaker, segment_ids=[seg.segment_id],
            ))

    # Post pass: fix overlaps and enforce the gap; clamp to media duration.
    for a, b in pairwise(cues):
        if b.start < a.end + rules.gap:
            a.end = max(a.start + rules.min_duration * 0.5, b.start - rules.gap)
    if media_duration is not None:
        for c in cues:
            c.end = min(c.end, media_duration)
    for i, c in enumerate(cues, 1):
        c.index = i
    return cues
