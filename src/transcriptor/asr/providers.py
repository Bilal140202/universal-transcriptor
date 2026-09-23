"""Concrete ASR providers. All heavy deps are lazy-imported."""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from ..models import Segment, TranscriptDocument, Word
from .base import ASRCapabilities, ASRProvider, TranscribeHints


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class FasterWhisperProvider(ASRProvider):
    """faster-whisper (CTranslate2) — default backend. MIT licensed."""

    name = "faster-whisper"

    def __init__(self, model_size: str = "large-v3", compute_type: str = "int8",
                 device: str = "auto", cpu_threads: int = 4) -> None:
        self.model_size = model_size
        self.compute_type = compute_type
        self.device = device
        self.cpu_threads = cpu_threads
        self._model: Any = None

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider=self.name,
            languages="multilingual (99 Whisper languages)",
            word_timestamps=True,
            vad=True,
            gpu_recommended=self.model_size.endswith(("large", "v3")),
            min_vram_gb=10 if "large" in self.model_size else 2,
            license="MIT",
            notes="4x+ faster than openai/whisper via CTranslate2; "
                  "word timestamps + VAD built in.",
        )

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size, device=self.device,
                compute_type=self.compute_type, cpu_threads=self.cpu_threads,
            )
        return self._model

    def transcribe(self, audio_path: str | Path, hints: TranscribeHints) -> TranscriptDocument:
        model = self._ensure_model()
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=hints.language,
            word_timestamps=hints.word_timestamps,
            vad_filter=True,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0] if hints.temperature_fallback else 0.0,
            condition_on_previous_text=hints.condition_on_previous_text,
        )
        doc = TranscriptDocument(
            source_media_id=uuid.uuid4().hex[:12],
            language=getattr(info, "language", None),
            metadata={"language_probability": _f(getattr(info, "language_probability", 0)),
                      "provider": self.name, "model": self.model_size},
        )
        for i, seg in enumerate(segments_iter):
            words = [
                Word(text=w.word.strip(), start=_f(w.start), end=_f(w.end),
                     confidence=_f(w.probability, 0.5))  # probability already 0..1
                for w in (getattr(seg, "words", None) or [])
            ]
            doc.segments.append(Segment(
                segment_id=f"seg-{i:05d}",
                start=_f(seg.start), end=_f(seg.end),
                text=seg.text.strip(), words=words,
                language=doc.language,
                confidence=min(1.0, math.exp(_f(getattr(seg, "avg_logprob", -0.5)))),
                metadata={
                    "no_speech_prob": _f(getattr(seg, "no_speech_prob", 0.0)),
                    "compression_ratio": _f(getattr(seg, "compression_ratio", 1.0)),
                    "temperature": _f(getattr(seg, "temperature", 0.0)),
                },
            ))
        return doc.sorted()


class WhisperXProvider(ASRProvider):
    """WhisperX — ASR + forced word alignment (+ diarization with HF token).

    Note: pyannote diarization models carry non-commercial terms; the pipeline
    treats diarization as optional and separate from transcription.
    """

    name = "whisperx"

    def __init__(self, model_size: str = "large-v3", device: str = "auto",
                 hf_token: str | None = None, diarize: bool = False) -> None:
        self.model_size = model_size
        self.device = device
        self.hf_token = hf_token
        self.diarize = diarize

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            provider=self.name,
            languages="multilingual (Whisper coverage)",
            word_timestamps=True,
            diarization=self.diarize,
            gpu_recommended=True,
            license="BSD-2 (diarization components may be non-commercial)",
            notes="wav2vec2/CTC forced alignment gives word-accurate timing.",
        )

    def available(self) -> bool:
        try:
            import whisperx  # noqa: F401
            return True
        except ImportError:
            return False

    def transcribe(self, audio_path: str | Path, hints: TranscribeHints) -> TranscriptDocument:
        import whisperx

        device = self.device if self.device != "auto" else ("cuda" if self._cuda() else "cpu")
        compute = "float16" if device == "cuda" else "int8"
        model = whisperx.load_model(self.model_size, device, compute_type=compute)
        audio = whisperx.load_audio(str(audio_path))
        raw = model.transcribe(audio, batch_size=16, language=hints.language)
        lang = raw.get("language") or hints.language

        align_model, meta = whisperx.load_align_model(language_code=lang, device=device)
        aligned = whisperx.align(raw["segments"], align_model, meta, audio,
                                 device, return_char_alignments=False)

        doc = TranscriptDocument(
            source_media_id=uuid.uuid4().hex[:12], language=lang,
            metadata={"provider": self.name, "model": self.model_size},
        )
        for i, seg in enumerate(aligned["segments"]):
            words = [
                Word(text=w.get("word", "").strip(),
                     start=_f(w.get("start")), end=_f(w.get("end")),
                     confidence=_f(w.get("score"), 1.0))
                for w in seg.get("words", []) if w.get("word")
            ]
            doc.segments.append(Segment(
                segment_id=f"seg-{i:05d}", start=_f(seg.get("start")),
                end=_f(seg.get("end")), text=seg.get("text", "").strip(),
                words=words, language=lang,
                speaker=seg.get("speaker"),
            ))

        if self.diarize and self.hf_token:
            diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=self.hf_token, device=device)
            diar_segments = diarize_model(audio)
            self._attach_speakers(doc, diar_segments)
        return doc.sorted()

    @staticmethod
    def _attach_speakers(doc: TranscriptDocument, diar_segments: list) -> None:
        speakers: set[str] = set()
        for seg in doc.segments:
            mid = (seg.start + seg.end) / 2
            for d in diar_segments:
                if d["start"] <= mid <= d["end"]:
                    seg.speaker = d.get("speaker")
                    break
            if seg.speaker:
                speakers.add(seg.speaker)
        doc.speakers = sorted(speakers)

    @staticmethod
    def _cuda() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False


class WhisperCppProvider(ASRProvider):
    """whisper.cpp backend — placeholder with setup guidance (not yet wired)."""

    name = "whispercpp"

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(provider=self.name, word_timestamps=True,
                               license="MIT", notes="CPU-only edge deployments.")

    def available(self) -> bool:
        return False

    def transcribe(self, audio_path: str | Path, hints: TranscribeHints) -> TranscriptDocument:
        raise NotImplementedError(
            "whisper.cpp backend not wired yet. Use faster-whisper "
            "(`pip install -e \".[asr]\"`) or implement via pywhispercpp."
        )
