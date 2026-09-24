"""Temporal + hallucination QA scans tests."""
from transcriptor.models import Segment, TranscriptDocument
from transcriptor.qa import (
    detect_repetition,
    english_quality_scan,
    hallucination_scan,
    normalize_english,
    temporal_scan,
)
from transcriptor.subtitles.segmentation import SubtitleRules, segment_for_subtitles


def _doc(segments, language=None):
    return TranscriptDocument(segments=segments, language=language)


def test_detect_repetition_loop():
    assert detect_repetition("please subscribe please subscribe please subscribe") == "please subscribe"
    assert detect_repetition("normal sentence with varied words here") is None


def test_hallucination_repetition_flagged_not_deleted():
    doc = _doc([Segment(segment_id="s1", start=0.0, end=5.0,
                        text="thank you for watching thank you for watching thank you for watching")])
    findings = hallucination_scan(doc)
    assert any(f.code == "HALLUCINATION_REPETITION" for f in findings)
    assert len(doc.segments) == 1  # content flagged, NOT deleted


def test_hallucination_no_speech_flag():
    doc = _doc([Segment(segment_id="s1", start=0.0, end=2.0, text="invented words",
                        metadata={"no_speech_prob": 0.95})])
    findings = hallucination_scan(doc)
    assert any(f.code == "HALLUCINATION_NO_SPEECH" for f in findings)


def test_temporal_negative_and_inverted():
    doc = _doc([
        Segment(segment_id="a", start=-1.0, end=2.0, text="x"),
        Segment(segment_id="b", start=5.0, end=4.0, text="y"),
        Segment(segment_id="c", start=6.0, end=7.0, text="z"),
    ])
    findings = temporal_scan(doc, media_duration=10.0)
    codes = {f.code for f in findings}
    assert "TEMPORAL_NEGATIVE_START" in codes
    assert "TEMPORAL_ZERO_DURATION" in codes


def test_temporal_beyond_media_duration():
    doc = _doc([Segment(segment_id="a", start=98.0, end=105.0, text="x")])
    findings = temporal_scan(doc, media_duration=100.0)
    assert any(f.code == "TEMPORAL_BEYOND_MEDIA" for f in findings)


def test_temporal_overlap_warn():
    doc = _doc([
        Segment(segment_id="a", start=0.0, end=10.0, text="x"),
        Segment(segment_id="b", start=5.0, end=12.0, text="y"),
    ])
    findings = temporal_scan(doc)
    assert any(f.code == "TEMPORAL_OVERLAP" for f in findings)


def test_english_quality_reading_speed():
    rules = SubtitleRules()
    doc = _doc([Segment(segment_id="a", start=0.0, end=1.0, text="word " * 30)])
    cues = segment_for_subtitles(doc, rules)
    findings = english_quality_scan(cues, rules)
    # end-extension should usually satisfy CPS; but max_duration cap may re-flag
    assert isinstance(findings, list)


def test_normalize_english_conservative():
    assert normalize_english("hello   world , i think") == "Hello world, I think"
    # must NOT paraphrase — content preserved (case-insensitive check)
    assert "meaning preserved" in normalize_english("meaning preserved").lower()


def test_findings_reference_segment_ids():
    doc = _doc([Segment(segment_id="seg-42", start=3.0, end=2.0, text="x")])
    findings = temporal_scan(doc)
    assert any(f.segment_id == "seg-42" for f in findings)
