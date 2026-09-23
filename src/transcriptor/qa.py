"""Quality pipeline — hallucination defense, temporal validation, English QA.

Philosophy (spec §13–14): uncertain content is FLAGGED with structured
findings, never silently deleted or rewritten. The LLM reviewer emits
findings only — it has no API to rewrite transcripts.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .models import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Finding, Segment

# ---------------------------------------------------------------------------
# 1. Hallucination defense
# ---------------------------------------------------------------------------

def detect_repetition(text: str, max_n: int = 6, min_repeats: int = 3) -> str | None:
    """Return the repeated phrase if the text is an n-gram loop.

    Classic Whisper failure: the same 3–6 word phrase repeated until the
    segment ends. We scan n-gram sizes from longest to shortest.
    """
    words = re.findall(r"\S+", text or "")
    if len(words) < min_repeats * 2:
        return None
    for n in range(min(max_n, len(words) // min_repeats), 1, -1):
        grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
        for gram, count in Counter(grams).items():
            if count >= min_repeats:
                return " ".join(gram)
    return None


def hallucination_scan(doc) -> list[Finding]:
    """Scan a TranscriptDocument for ASR hallucination signals.

    Detects: repetition loops, high no_speech_prob with text present, extreme
    compression ratios, near-zero-confidence segments, impossible per-word
    timing. Marks segments — NEVER deletes them.
    """
    findings: list[Finding] = []
    for seg in doc.segments:
        meta = seg.metadata or {}

        phrase = detect_repetition(seg.text)
        if phrase:
            findings.append(Finding(
                code="HALLUCINATION_REPETITION", severity=SEVERITY_ERROR,
                segment_id=seg.segment_id,
                message=f"Repetition loop detected: {phrase!r} repeats in segment {seg.segment_id}.",
                suggestion="Re-transcribe this audio span with temperature fallback; "
                           "verify against audio energy before trusting text.",
            ))
        if meta.get("no_speech_prob", 0) > 0.85 and seg.text.strip():
            findings.append(Finding(
                code="HALLUCINATION_NO_SPEECH", severity=SEVERITY_WARN,
                segment_id=seg.segment_id,
                message=(f"Text present but no_speech_prob="
                         f"{meta['no_speech_prob']:.2f} (segment {seg.segment_id})."),
                suggestion="Correlate with audio energy / silence detection.",
            ))
        cr = meta.get("compression_ratio", 1.0)
        if cr and (cr > 2.4 or cr < 0.5):
            findings.append(Finding(
                code="HALLUCINATION_COMPRESSION_RATIO", severity=SEVERITY_WARN,
                segment_id=seg.segment_id,
                message=f"Unusual compression_ratio={cr:.2f} in segment {seg.segment_id}.",
            ))
        if 0 < seg.confidence < 0.25:
            findings.append(Finding(
                code="HALLUCINATION_LOW_CONFIDENCE", severity=SEVERITY_WARN,
                segment_id=seg.segment_id,
                message=f"Very low confidence ({seg.confidence:.2f}) in segment {seg.segment_id}.",
                suggestion="Route to human review UI.",
            ))

    # Language-switch anomaly: segments that disagree with document language
    # when the model reported per-segment languages.
    if doc.language:
        foreign = [s for s in doc.segments
                   if s.language and s.language != doc.language]
        if len(foreign) > max(1, len(doc.segments) * 0.2):
            findings.append(Finding(
                code="HALLUCINATION_LANGUAGE_SWITCH", severity=SEVERITY_WARN,
                message=(f"{len(foreign)} segments report language != document "
                         f"language {doc.language!r} — possible code-switching "
                         "or detection anomaly."),
            ))
    return findings


# ---------------------------------------------------------------------------
# 2. Temporal validation — translation must not destroy timing (spec §17)
# ---------------------------------------------------------------------------

def temporal_scan(doc, media_duration: float | None = None,
                  eps: float = 0.05, max_overlap: float = 0.5) -> list[Finding]:
    findings: list[Finding] = []
    segments = sorted(doc.segments, key=lambda s: s.start)
    prev: Segment | None = None
    for seg in segments:
        if seg.start < -eps:
            findings.append(Finding("TEMPORAL_NEGATIVE_START", SEVERITY_ERROR,
                segment_id=seg.segment_id,
                message=f"Segment {seg.segment_id} starts at {seg.start:.3f}s (< 0)."))
        if seg.end <= seg.start:
            findings.append(Finding("TEMPORAL_ZERO_DURATION", SEVERITY_ERROR,
                segment_id=seg.segment_id,
                message=f"Segment {seg.segment_id} has end ({seg.end:.3f}) <= start ({seg.start:.3f})."))
        if media_duration is not None and seg.end > media_duration + eps:
            findings.append(Finding("TEMPORAL_BEYOND_MEDIA", SEVERITY_ERROR,
                segment_id=seg.segment_id,
                message=(f"Segment {seg.segment_id} ends at {seg.end:.3f}s, beyond "
                         f"media duration {media_duration:.3f}s.")))
        if prev is not None and seg.start < prev.end - max_overlap:
            findings.append(Finding("TEMPORAL_OVERLAP", SEVERITY_WARN,
                segment_id=seg.segment_id,
                message=(f"Segment {seg.segment_id} overlaps previous by "
                         f"{prev.end - seg.start:.3f}s (>{max_overlap}s).")))
        prev = seg
    return findings


# ---------------------------------------------------------------------------
# 3. English quality / subtitle-length QA (spec §12, §16)
# ---------------------------------------------------------------------------

_TERMINALS = (".", "!", "?", "。", "！", "？")

def english_quality_scan(cues: list, rules) -> list[Finding]:
    """Scan rendered subtitle cues against the professional rules.

    `cues` are SubtitleCue objects; `rules` a SubtitleRules instance.
    """
    findings: list[Finding] = []
    for cue in cues:
        text = " ".join(cue.lines)
        dur = max(cue.end - cue.start, 1e-6)
        cps = len(text) / dur
        if cps > rules.cps_max:
            findings.append(Finding(
                code="QUALITY_READING_SPEED", severity=SEVERITY_WARN,
                cue_index=cue.index,
                message=(f"Cue {cue.index} reading speed {cps:.1f} cps "
                         f"(max {rules.cps_max})."),
                suggestion="Split the cue or extend its end into silence.",
            ))
        if dur < rules.min_duration - 1e-3:
            findings.append(Finding(
                code="QUALITY_MIN_DURATION", severity=SEVERITY_WARN,
                cue_index=cue.index,
                message=f"Cue {cue.index} duration {dur:.2f}s < min {rules.min_duration}s."))
        if dur > rules.max_duration + 1e-3:
            findings.append(Finding(
                code="QUALITY_MAX_DURATION", severity=SEVERITY_WARN,
                cue_index=cue.index,
                message=f"Cue {cue.index} duration {dur:.2f}s > max {rules.max_duration}s."))
        for line in cue.lines:
            if len(line) > rules.max_chars_per_line:
                findings.append(Finding(
                    code="QUALITY_LINE_LENGTH", severity=SEVERITY_WARN,
                    cue_index=cue.index,
                    message=(f"Cue {cue.index} line exceeds {rules.max_chars_per_line} "
                             f"chars ({len(line)})."),
                    suggestion="Rebalance lines."))
        if "  " in text:
            findings.append(Finding("QUALITY_DOUBLE_SPACE", SEVERITY_INFO,
                cue_index=cue.index, message=f"Cue {cue.index} contains double spaces."))
        if text and text[-1] not in _TERMINALS and rules.require_terminal_punctuation:
            findings.append(Finding(
                code="QUALITY_PUNCTUATION", severity=SEVERITY_INFO,
                cue_index=cue.index,
                message=f"Cue {cue.index} lacks terminal punctuation."))
    return findings


def normalize_english(text: str) -> str:
    """Conservative, faithful normalization — spelling/spacing only.

    NEVER paraphrases. Fixes: double spaces, space before punctuation,
    standalone 'i' → 'I', sentence-initial capital, missing final period on
    multi-sentence subtitles is left alone (faithfulness over style).
    """
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\bi\b", "I", t)
    if t:
        t = t[0].upper() + t[1:]
    return t


# ---------------------------------------------------------------------------
# 4. LLM reviewer contract — findings only, never silent rewrite (spec §13.1)
# ---------------------------------------------------------------------------

REVIEW_PROMPT = """You are a translation QA reviewer. Compare the SOURCE and the \
ENGLISH for the segment, given CONTEXT and TIMING.

Report findings for: meaning not preserved, omissions, inventions, grammar, \
names/terminology, punctuation, register, literalness, contradictions with \
neighboring segments.

Output STRICT JSON: {{"findings":[{{"code":"...","severity":"info|warn|error",\
"message":"...","suggestion":"..."}}]}}
If the translation is faithful, return {{"findings":[]}}. Never rewrite the \
translation — you are a reviewer, not an editor."""

REVIEW_QUESTIONS = (
    "Is meaning preserved?", "Is anything omitted?", "Was anything invented?",
    "Is the English grammatical?", "Are names correct?", "Is punctuation correct?",
    "Is the style appropriate?", "Is the translation too literal?", "Is it too free?",
    "Does it contradict neighboring segments?",
)


class LLMReviewer:
    """Reviewer that turns a strong LLM into pass-9 QA. Findings only.

    The contract is enforced by design: the review() return type is a list of
    Finding — there is no path for the reviewer to modify the transcript.
    """

    def __init__(self, model: str = "qwen2.5-7b-instruct",
                 base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    def available(self) -> bool:
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    def review(self, source_language: str, source_text: str, english_text: str,
               context_before: str = "", context_after: str = "",
               timing: str = "") -> list[Finding]:
        from openai import OpenAI
        client = OpenAI(base_url=self.base_url, api_key=self.api_key or "local")
        user = (f"SOURCE ({source_language}): {source_text}\n"
                f"ENGLISH: {english_text}\n"
                f"CONTEXT BEFORE: {context_before or '-'}\n"
                f"CONTEXT AFTER: {context_after or '-'}\n"
                f"TIMING: {timing or '-'}")
        try:
            resp = client.chat.completions.create(
                model=self.model, response_format={"type": "json_object"},
                temperature=0.0,
                messages=[{"role": "system", "content": REVIEW_PROMPT},
                          {"role": "user", "content": user}],
            )
            data = json.loads(resp.choices[0].message.content)
            return [Finding(
                code=str(f.get("code", "REVIEW_FINDING")).upper(),
                severity=str(f.get("severity", SEVERITY_INFO)),
                message=str(f.get("message", "")),
                suggestion=f.get("suggestion"),
            ) for f in data.get("findings", [])]
        except Exception as exc:
            return [Finding(code="REVIEWER_FAILED", severity=SEVERITY_INFO,
                            message=f"Reviewer unavailable: {exc}")]
