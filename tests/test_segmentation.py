"""Subtitle segmentation rules tests."""
from transcriptor.models import Segment, TranscriptDocument, Word
from transcriptor.subtitles.segmentation import (
    SubtitleRules,
    _balanced_lines,
    _split_text_at_boundaries,
    segment_for_subtitles,
)


def _doc(segments):
    return TranscriptDocument(segments=segments)


def test_balanced_lines_within_limits():
    text = "The quick brown fox jumps over the lazy dog and keeps running far away today"
    lines = _balanced_lines(text, 42, 2)
    assert len(lines) <= 2
    assert all(len(line) <= 42 for line in lines)
    assert " ".join(lines) == text


def test_split_at_sentence_boundaries():
    text = ("This is the first sentence of the dialogue. "
            "And here comes a second sentence which is also fairly long to read.")
    parts = _split_text_at_boundaries(text, 42)
    assert len(parts) >= 2
    assert parts[0].strip().endswith(".")


def test_speaker_change_never_merged():
    doc = _doc([
        Segment(segment_id="s1", start=0.0, end=2.0, text="Hello there.", speaker="SPEAKER_00"),
        Segment(segment_id="s2", start=2.5, end=4.0, text="Hi, who is this?", speaker="SPEAKER_01"),
    ])
    cues = segment_for_subtitles(doc)
    assert cues[0].speaker == "SPEAKER_00"
    assert cues[-1].speaker == "SPEAKER_01"
    assert all("SPEAKER_01" not in (c.speaker or "") for c in cues[:1])


def test_cue_timing_never_negative_or_inverted():
    doc = _doc([
        Segment(segment_id="s1", start=10.0, end=10.05, text="A very short utterance indeed"),
    ])
    cues = segment_for_subtitles(doc)
    for c in cues:
        assert c.end > c.start


def test_reading_speed_extended_not_broken():
    # 60 chars in 1.0s = 60 cps, way over 20 → end must extend, but ≤ max_duration
    doc = _doc([
        Segment(segment_id="s1", start=0.0, end=1.0,
                text="This line is far too long for one second of reading time"),
    ])
    cues = segment_for_subtitles(doc)
    cue = cues[0]
    dur = cue.end - cue.start
    assert dur <= SubtitleRules().max_duration + 1e-6
    assert dur > 1.0  # extended to satisfy CPS


def test_cues_clamped_to_media_duration():
    doc = _doc([
        Segment(segment_id="s1", start=98.0, end=99.9, text="Final words of the video"),
    ])
    cues = segment_for_subtitles(doc, media_duration=100.0)
    assert all(c.end <= 100.0 for c in cues)


def test_segment_ids_preserved():
    doc = _doc([
        Segment(segment_id="seg-00001", start=0.0, end=3.0, text="Mapped back to source."),
    ])
    cues = segment_for_subtitles(doc)
    assert all("seg-00001" in c.segment_ids for c in cues)


def test_word_timing_used_when_available():
    words = [Word(text="Hello", start=0.0, end=0.5, confidence=0.9),
             Word(text="world.", start=0.6, end=1.1, confidence=0.9)]
    doc = _doc([Segment(segment_id="s1", start=0.0, end=1.1,
                        text="Hello world.", words=words)])
    cues = segment_for_subtitles(doc)
    assert abs(cues[0].start - 0.0) < 0.15
