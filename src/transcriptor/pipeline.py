"""PipelineOrchestrator — wires all stages with per-stage records.

Every stage is captured in a StageRecord (status, duration, artifacts, error).
The pipeline degrades with structured findings, never with silence.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .acquisition import MediaSourceResolver
from .asr.base import TranscribeHints
from .asr.providers import FasterWhisperProvider, WhisperCppProvider, WhisperXProvider
from .asr.router import ASRRouter
from .audio import extract_for_asr, select_audio_stream, trim_prefix
from .models import Finding, StageRecord, TranscriptDocument
from .probe import probe_media
from .qa import english_quality_scan, hallucination_scan, normalize_english, temporal_scan
from .subtitles import SubtitleRules, segment_for_subtitles, to_ass, to_json, to_srt, to_vtt
from .translation.context import build_context_windows
from .translation.providers import AutoTranslationProvider
from .translation.terminology import TerminologyMemory


class PipelineOrchestrator:
    def __init__(self, work_dir: str | Path = "runs") -> None:
        self.work_dir = Path(work_dir)
        self.resolver = MediaSourceResolver()
        self.asr_router = ASRRouter()
        for provider in (FasterWhisperProvider(), WhisperXProvider(),
                         WhisperCppProvider()):
            self.asr_router.register(provider)
        self.translator = AutoTranslationProvider()

    # ------------------------------------------------------------------ run

    def run(self, source: str, *, out_dir: str | Path | None = None,
            language: str | None = None, accuracy: str = "balanced",
            loudness_normalize: bool = False, excerpt_seconds: float | None = None,
            subtitle_mux: bool = False, exports: tuple[str, ...] = ("srt", "vtt", "json"),
            extra_terms: dict[str, str] | None = None) -> dict[str, Any]:
        stages: list[StageRecord] = []
        findings: list[Finding] = []

        def stage(name: str):
            def _ctx():
                rec = StageRecord(name=name, status="running")
                stages.append(rec)
                _dump()
                t0 = time.monotonic()

                class _C:
                    def __enter__(self_inner):
                        return rec
                    def __exit__(self_inner, exc_type, exc, tb):
                        rec.seconds = round(time.monotonic() - t0, 2)
                        if exc_type is None:
                            rec.status = "ok"
                        else:
                            rec.status = "failed"
                            rec.error = f"{exc_type.__name__}: {exc}"
                        _dump()
                        return False  # propagate
                return _C()
            return _ctx

        run_dir = Path(out_dir) if out_dir else self.work_dir / time.strftime("run-%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        report: dict[str, Any] = {"source": source, "run_dir": str(run_dir), "stages": stages,
                                  "findings": findings, "artifacts": {},
                                  "status": "running"}
        report_path = run_dir / "report.json"

        def _dump() -> None:
            """Persist the live report after every stage — the review UI polls
            this file for stage-by-stage progress (no hidden server state)."""
            report_path.write_text(json.dumps(
                {**report, "stages": [asdict(s) for s in stages],
                 "findings": [asdict(f) for f in findings]},
                ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        # 1 — acquisition ----------------------------------------------------
        with stage("acquisition")() as rec:
            media = self.resolver.resolve(source, run_dir / "media")
            rec.artifacts = {"path": str(media.path),
                             "record": media.record.to_dict() if media.record else None}
            report["artifacts"]["media"] = str(media.path)

        # 2 — probe ------------------------------------------------------------
        with stage("probe")() as rec:
            manifest = probe_media(media.path)
            media.manifest = manifest
            rec.artifacts = {"manifest": {k: v for k, v in manifest.to_dict().items()
                                          if k != "chapters"}}
            if manifest.integrity.get("decode", "").startswith("fail"):
                raise RuntimeError(f"Media decode check failed: {manifest.integrity['decode']}")

        # 3 — audio extraction (derived, timing-safe) ---------------------------
        with stage("audio")() as rec:
            stream = select_audio_stream(manifest)
            asr_audio = extract_for_asr(media.path, manifest,
                                        run_dir / "asr_audio.wav",
                                        stream=stream,
                                        loudness_normalize=loudness_normalize)
            rec.artifacts = {"stream_index": stream.index,
                             "derived_audio": str(asr_audio),
                             "timing_note": "ASR audio is derived; media timestamps remain authoritative."}
            if excerpt_seconds:
                asr_audio = trim_prefix(asr_audio, run_dir / "asr_excerpt.wav",
                                        excerpt_seconds)
                rec.artifacts["excerpt"] = {
                    "seconds": excerpt_seconds,
                    "note": "Prefix excerpt (t=0 cut): timestamps remain 1:1 with media.",
                    "path": str(asr_audio),
                }

        # 4 — ASR ----------------------------------------------------------------
        with stage("asr")() as rec:
            provider, decision = self.asr_router.route(
                {"accuracy": accuracy, "language": language, "word_timestamps": True})
            doc: TranscriptDocument = provider.transcribe(
                asr_audio, TranscribeHints(language=language))
            doc.title = media.record.file_name if media.record else None
            rec.artifacts = {"provider": provider.name,
                             "decision": asdict(decision),
                             "segments": len(doc.segments)}
            # Memory hygiene: release the ASR model before the MT model loads
            # so peak RAM stays near max(model) instead of sum(models).
            provider.unload()

        # 5 — QA pass 1: hallucination + temporal on source transcript ----------
        with stage("qa_transcript")() as rec:
            findings += hallucination_scan(doc)
            findings += temporal_scan(doc, media_duration=manifest.duration)
            rec.artifacts = {"findings": len(findings)}

        # 6 — translation ---------------------------------------------------------
        with stage("translation")() as rec:
            terminology = TerminologyMemory()
            terminology.learn_from_repetition(s.text for s in doc.segments)
            for src, eng in (extra_terms or {}).items():
                terminology.override(src, eng)
            windows = build_context_windows(doc, terminology=terminology, title=doc.title)
            results = self.translator.translate_batch(windows, doc.language or "auto")
            for seg, res in zip(doc.segments, results, strict=True):
                seg.metadata["english"] = normalize_english(terminology.apply(res.english))
                findings += res.findings
            rec.artifacts = {"translated": sum(1 for r in results if r.ok),
                             "provider": self.translator.name}

        # 7 — terminology consistency ---------------------------------------------
        with stage("terminology")() as rec:
            pairs = [(s.text, s.metadata.get("english", "")) for s in doc.segments]
            findings += terminology.consistency_findings(pairs)
            rec.artifacts = {"terms": len(terminology.entries)}

        # 8 — subtitle segmentation --------------------------------------------------
        with stage("segmentation")() as rec:
            rules = SubtitleRules()
            cues = segment_for_subtitles(doc, rules, media_duration=manifest.duration)
            findings += english_quality_scan(cues, rules)
            rec.artifacts = {"cues": len(cues)}

        # 9 — exports ------------------------------------------------------------------
        with stage("export")() as rec:
            stem = "transcript"
            paths: dict[str, str] = {}
            if "srt" in exports:
                p = run_dir / f"{stem}.srt"
                p.write_text(to_srt(cues), encoding="utf-8")
                paths["srt"] = str(p)
            if "vtt" in exports:
                p = run_dir / f"{stem}.vtt"
                p.write_text(to_vtt(cues), encoding="utf-8")
                paths["vtt"] = str(p)
            if "ass" in exports:
                p = run_dir / f"{stem}.ass"
                p.write_text(to_ass(cues), encoding="utf-8")
                paths["ass"] = str(p)
            p = run_dir / f"{stem}.json"
            p.write_text(to_json(doc, cues), encoding="utf-8")
            paths["json"] = str(p)
            report["artifacts"].update(paths)
            rec.artifacts = paths

        # 10 — optional soft-subtitle mux (stream copy — never re-encode) ----------
        if subtitle_mux:
            with stage("mux")() as rec:
                out = run_dir / f"subtitled_{Path(media.path).name}"
                muxed = _safe_mux(media.path, paths.get("srt"), out)
                report["artifacts"]["muxed"] = str(muxed)
                rec.artifacts = {"muxed": str(muxed)}

        report["status"] = "ok" if all(s.status in ("ok", "skipped") for s in stages) else "partial"
        _dump()
        report["report_path"] = str(report_path)
        return report


def _safe_mux(media_path, srt_path, out_path):
    from .subtitles import mux_subtitle_track
    if not srt_path:
        raise RuntimeError("Mux requested but no SRT was exported.")
    return mux_subtitle_track(media_path, srt_path, out_path)
