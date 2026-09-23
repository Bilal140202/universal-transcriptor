"""Media probe — ffprobe → MediaManifest.

The probe is the gate that decides whether a downloaded object is media at all.
If the media cannot be decoded, the failure explains exactly why (spec §5.1).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .models import MediaManifest, StreamInfo

_AUDIO_CODEC_SKIP = {"srt", "mov_text", "eia_608", "dvb_teletext"}


def require_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError(
            "ffprobe not found on PATH. Install FFmpeg (e.g. `apt install "
            "ffmpeg`, `brew install ffmpeg`, or download from ffmpeg.org) — "
            "the engine probes every source before trusting it."
        )
    return path


def _stream_info(entry: dict) -> StreamInfo:
    tags = entry.get("tags") or {}
    disp = entry.get("disposition") or {}
    fps = entry.get("avg_frame_rate") or entry.get("r_frame_rate")
    try:
        br = int(entry["bit_rate"])
    except (KeyError, TypeError, ValueError):
        br = None
    try:
        dur = float(entry["duration"])
    except (KeyError, TypeError, ValueError):
        dur = None
    try:
        sr = int(entry["sample_rate"])
    except (KeyError, TypeError, ValueError):
        sr = None
    try:
        ch = int(entry["channels"])
    except (KeyError, TypeError, ValueError):
        ch = None
    return StreamInfo(
        index=int(entry.get("index", -1)),
        kind=entry.get("codec_type", "unknown"),
        codec=entry.get("codec_name"),
        language=tags.get("language"),
        channels=ch,
        sample_rate=sr,
        bit_rate=br,
        width=entry.get("width"),
        height=entry.get("height"),
        frame_rate=fps,
        duration=dur,
        default=bool(disp.get("default")),
        raw=entry,
    )


def probe_media(path: str | Path, deep_decode_check: bool = True) -> MediaManifest:
    """Probe one local file into a MediaManifest.

    deep_decode_check runs a 1-second null-mux decode pass so a corrupt or
    undecodable file fails HERE with an exact reason — not later inside ASR.
    """
    ffprobe = require_ffprobe()
    cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
           "-show_streams", "-show_chapters", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(
            f"ffprobe could not parse {path} — not a recognized container, or "
            "the file is corrupt/truncated. "
            f"(stderr: {proc.stderr.strip()[:300] or 'none'})"
        )
    data = json.loads(proc.stdout)
    fmt = data.get("format", {})

    manifest = MediaManifest(
        path=str(path),
        container=fmt.get("format_name"),
        duration=_f(fmt.get("duration")),
        size_bytes=_i(fmt.get("size")),
        metadata=fmt.get("tags", {}),
        chapters=data.get("chapters", []),
    )
    for entry in data.get("streams", []):
        info = _stream_info(entry)
        if info.kind == "video":
            manifest.video_streams.append(info)
        elif info.kind == "audio":
            manifest.audio_streams.append(info)
        elif info.kind == "subtitle" or (info.codec or "") in _AUDIO_CODEC_SKIP:
            manifest.subtitle_streams.append(info)

    # Language hints from stream tags + container metadata.
    hints: list[str] = []
    for s in (*manifest.audio_streams, *manifest.video_streams):
        if s.language and s.language.lower() not in ("und", ""):
            hints.append(s.language.lower())
    for key in ("language", "lang"):
        v = (manifest.metadata or {}).get(key)
        if v:
            hints.append(str(v).lower())
    manifest.language_hints = sorted(set(hints))

    # Integrity: duration sane, has an audio stream, decodes.
    integrity: dict[str, object] = {"probe": "pass"}
    if manifest.duration is not None and manifest.duration <= 0:
        integrity["duration"] = "fail: duration <= 0"
    if not manifest.audio_streams:
        integrity["audio"] = "fail: no audio stream — nothing to transcribe"
    else:
        integrity["audio"] = f"pass: {len(manifest.audio_streams)} stream(s)"
    if deep_decode_check:
        integrity["decode"] = _decode_check(path)
    manifest.integrity = integrity
    return manifest


def _decode_check(path: Path, seconds: float = 1.0) -> str:
    if not shutil.which("ffmpeg"):
        return "skipped: ffmpeg not on PATH"
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-t", str(seconds),
           "-map", "a:0?", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        return "pass"
    return f"fail: {proc.stderr.strip().splitlines()[-1][:300] if proc.stderr.strip() else 'decode error'}"


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
