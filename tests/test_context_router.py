"""Context window + ASR router decision tests (no ML deps required)."""
from transcriptor.asr.router import ASRRouter, RouteDecision
from transcriptor.models import Segment, TranscriptDocument
from transcriptor.translation.context import build_context_windows
from transcriptor.translation.terminology import TerminologyMemory


def _doc():
    return TranscriptDocument(segments=[
        Segment(segment_id="s1", start=0.0, end=2.0, text="YG announced a comeback."),
        Segment(segment_id="s2", start=2.0, end=4.0, text="彼はそれをやった。"),
        Segment(segment_id="s3", start=4.0, end=6.0, text="The fans reacted fast."),
    ], language="ja", title="Test video")


def test_context_windows_have_neighbors():
    doc = _doc()
    windows = build_context_windows(doc)
    assert len(windows) == 3
    assert windows[0].previous is None
    assert windows[0].next.segment_id == "s2"
    assert windows[1].previous.segment_id == "s1" and windows[1].next.segment_id == "s3"
    assert windows[2].next is None


def test_context_windows_attach_glossary_hits():
    mem = TerminologyMemory()
    mem.register("YG", "YG")
    windows = build_context_windows(_doc(), terminology=mem)
    assert windows[0].glossary_hits
    assert windows[0].glossary.get("YG") == "YG"


def test_windows_include_speaker_and_title():
    doc = _doc()
    doc.segments[0].speaker = "SPEAKER_00"
    windows = build_context_windows(doc)
    assert windows[0].speaker == "SPEAKER_00"
    assert windows[0].document_title == "Test video"


def test_router_pick_provider_by_accuracy():
    class Available:
        name = "faster-whisper"
        def capabilities(self):
            return None
        def available(self):
            return True
        def transcribe(self, *a, **k):
            raise NotImplementedError

    router = ASRRouter({"faster-whisper": Available()})
    provider, decision = router.route({"accuracy": "best", "gpu": True,
                                       "word_timestamps": True})
    assert provider.name == "faster-whisper"
    assert decision.model == "large-v3"
    assert decision.compute_type == "float16"
    assert decision.alignment_strategy == "whisperx"

    _provider2, decision2 = router.route({"accuracy": "fast"})
    assert decision2.model == "small"


def test_route_decision_is_structured():
    d = RouteDecision(provider_name="x", model="m", compute_type="int8", reason="r")
    assert d.batch_size == 16  # default from dataclass
    assert d.reason == "r"
