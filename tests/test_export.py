"""Export format validity tests."""
import json

from transcriptor.models import Segment, TranscriptDocument
from transcriptor.subtitles import (
    segment_for_subtitles,
    to_ass,
    to_json,
    to_srt,
    to_vtt,
)


def _doc():
    return TranscriptDocument(
        segments=[Segment(segment_id="s1", start=0.0, end=2.0, text="Hello world.",
                          speaker="SPEAKER_00"),
                  Segment(segment_id="s2", start=2.5, end=5.0, text="Second line here.")],
        language="ja", title="Test")


def test_srt_structure():
    cues = segment_for_subtitles(_doc())
    srt = to_srt(cues)
    blocks = [b for b in srt.strip().split("\n\n")]
    assert blocks[0].splitlines()[0] == "1"
    assert "-->" in blocks[0]
    assert all("-->" in b for b in blocks)


def test_srt_timestamp_format():
    cues = segment_for_subtitles(_doc())
    srt = to_srt(cues)
    first_arrow = next(line for line in srt.splitlines() if "-->" in line)
    start, end = first_arrow.split(" --> ")
    for ts in (start, end):
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(",")
        assert len(hh) == 2 and len(mm) == 2 and len(ss) == 2 and len(ms) == 3


def test_vtt_header():
    assert to_vtt(segment_for_subtitles(_doc())).startswith("WEBVTT")


def test_ass_contains_style_and_dialogue():
    ass = to_ass(segment_for_subtitles(_doc()))
    assert "[V4+ Styles]" in ass
    assert ass.count("Dialogue:") == len(segment_for_subtitles(_doc()))


def test_json_export_roundtrip():
    doc = _doc()
    cues = segment_for_subtitles(doc)
    payload = json.loads(to_json(doc, cues))
    assert payload["language"] == "ja"
    assert len(payload["segments"]) == 2
    assert payload["cues"][0]["segment_ids"] == ["s1"]
    assert payload["segments"][0]["start"] == 0.0
