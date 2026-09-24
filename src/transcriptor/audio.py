"""Audio stream selection and timing-safe extraction.

CRITICAL RULE (spec §6): original media timestamps are authoritative. The ASR
audio produced here is *derived*. Only duration-safe transforms are allowed in
the default path (channel downmix, resampling, single-pass loudness
normalization). Time-altering transforms (silence removal, tempo change,
segment-cutting) are FORBIDDEN because they shift the timeline.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .models import MediaManifest, StreamInfo, select_best_audio


class AudioExtractionError(RuntimeError):
    pass


def select_audio_stream(manifest: MediaManifest, index: int | None = None) -> StreamInfo:
    """Pick the requested audio stream, or the best one automatically.

    Automatic selection scores streams on channels, bitrate, declared language,
    default disposition and codec quality (models.score_audio_stream). If the
    chosen stream later proves poor, callers may re-select manually by index.
    """
    if not manifest.audio_streams:
        raise AudioExtractionError(
            "Media has no audio stream — nothing to transcribe. "
            f"Video streams present: {len(manifest.video_streams)}."
        )
    if index is not None:
        for s in manifest.audio_streams:
            if s.index == index:
                return s
        raise AudioExtractionError(f"Audio stream index {index} not found.")
    best_idx = select_best_audio(manifest)
    return next(s for s in manifest.audio_streams if s.index == best_idx)


def extract_for_asr(
    media_path: str | Path,
    manifest: MediaManifest,
    out_path: str | Path,
    stream: StreamInfo | None = None,
    loudness_normalize: bool = False,
) -> Path:
    """Extract a derived 16 kHz mono WAV for ASR.

    Duration-safe operations ONLY:
      - `-ac 1` mono downmix
      - `-ar 16000` resampling (ASR models expect 16 kHz)
      - optional single-pass `loudnorm` (duration-preserving)

    Never uses silence removal / atempo / segment cuts here — see module docstring.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioExtractionError("ffmpeg not found on PATH — required for audio extraction.")
    stream = stream or select_audio_stream(manifest)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg, "-v", "error", "-i", str(media_path),
        "-map", f"0:{stream.index}",
        "-vn", "-ac", "1", "-ar", "16000",
    ]
    if loudness_normalize:
        # Single-pass loudnorm preserves duration (fills with silence rather
        # than stretching); safe for the authoritative timeline.
        cmd += ["-af", "loudnorm=I=-20:TP=-1.5:LRA=11"]
    cmd += ["-c:a", "pcm_s16le", str(out_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        raise AudioExtractionError(
            f"ffmpeg audio extraction failed for stream {stream.index}: "
            f"{proc.stderr.strip()[-500:]}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise AudioExtractionError("Audio extraction produced an empty file.")
    return out_path


def trim_prefix(asr_audio: str | Path, out_path: str | Path,
                seconds: float) -> Path:
    """Cut a **prefix** of the derived ASR audio for smoke tests / previews.

    Timeline safety (spec §6): a prefix cut starts at t=0, so every timestamp
    in the excerpt maps 1:1 onto the authoritative media timeline — no offset
    bookkeeping, no drift. Cutting a middle slice would shift the timeline and
    is therefore NOT provided here.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioExtractionError("ffmpeg not found on PATH.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(asr_audio),
           "-t", f"{float(seconds):.3f}", "-c", "copy", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise AudioExtractionError(
            f"Excerpt trim failed: {proc.stderr.strip()[-300:]}")
    return out_path
