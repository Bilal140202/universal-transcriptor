"""Subtitle export — SRT / WebVTT / ASS / JSON + ffmpeg muxing.

ABSOLUTE PRINCIPLE (spec §2/§18): `mux_subtitle_track` stream-copies the
video (`-c copy`) — video is NEVER re-encoded to attach subtitles.
Burn-in is a separate, explicitly requested path.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..models import TranscriptDocument
from .segmentation import SubtitleCue


def _fmt_srt(t: float) -> str:
    ms = round(t * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt(t: float) -> str:
    ms = round(t * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_srt(cues: list[SubtitleCue]) -> str:
    blocks = []
    for c in cues:
        blocks.append(f"{c.index}\n{_fmt_srt(c.start)} --> {_fmt_srt(c.end)}\n"
                      + "\n".join(c.lines) + (f"\n[{c.speaker}]" if c.speaker else ""))
    return "\n\n".join(blocks) + "\n"


def to_vtt(cues: list[SubtitleCue]) -> str:
    blocks = ["WEBVTT", ""]
    for c in cues:
        blocks.append(f"{c.index}\n{_fmt_vtt(c.start)} --> {_fmt_vtt(c.end)}\n"
                      + "\n".join(c.lines))
    return "\n".join(blocks) + "\n"


def to_ass(cues: list[SubtitleCue], title: str = "Subtitles") -> str:
    """Minimal ASS with readable default styling."""
    header = (
        "[Script Info]\nTitle: " + title + "\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,54,&H00FFFFFF,&H00000000,&H80000000,0,1,3,1,2,60,60,60,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    def _ass_t(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"
    events = [
        f"Dialogue: 0,{_ass_t(c.start)},{_ass_t(c.end)},Default,,0,0,0,,"
        + "\\N".join(c.lines)
        for c in cues
    ]
    return header + "\n".join(events) + "\n"


def to_json(doc: TranscriptDocument, cues: list[SubtitleCue] | None = None) -> str:
    """Rich transcript export: authoritative document + optional cue view."""
    payload = doc.to_dict()
    if cues:
        payload["cues"] = [
            {"index": c.index, "start": c.start, "end": c.end,
             "lines": c.lines, "speaker": c.speaker, "segment_ids": c.segment_ids}
            for c in cues
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def rebuild_from_json(
    transcript_payload: dict,
    edits: dict[str, str] | None = None,
    rules: "SubtitleRules | None" = None,
) -> tuple[dict, list[SubtitleCue]]:
    """Apply human review edits to a transcript payload and rebuild cue lines.

    Contract (spec §17 human review):
    - ONLY the English text of segments may change; timing is authoritative
      and is never rewritten here.
    - ``edits`` maps segment_id → corrected English text.
    - Cue lines are re-wrapped under the professional layout rules; cue
      timings stay bound to the original temporal spans.
    """
    from .segmentation import SubtitleRules, _balanced_lines

    rules = rules or SubtitleRules()
    edits = edits or {}
    segments = transcript_payload.get("segments", [])
    by_id = {s.get("segment_id"): s for s in segments}
    applied = 0
    for seg_id, english in edits.items():
        seg = by_id.get(seg_id)
        if seg is None:
            continue
        seg.setdefault("metadata", {})["english"] = english
        seg["metadata"]["edited"] = True
        applied += 1

    cues: list[SubtitleCue] = []
    for c in transcript_payload.get("cues", []):
        texts: list[str] = []
        for sid in c.get("segment_ids", []):
            seg = by_id.get(sid, {})
            meta = seg.get("metadata") or {}
            texts.append(meta.get("english") or seg.get("text", ""))
        lines: list[str] = []
        for t in texts:
            lines.extend(_balanced_lines(
                t, rules.max_chars_per_line, rules.max_lines) if t else [])
        cues.append(SubtitleCue(
            index=c.get("index", 0), start=c.get("start", 0.0),
            end=c.get("end", 0.0), lines=lines or [""],
            speaker=c.get("speaker"), segment_ids=c.get("segment_ids", []),
        ))
    transcript_payload["edits_applied"] = applied
    return transcript_payload, cues


# ---------------------------------------------------------------------------
# Video paths
# ---------------------------------------------------------------------------

def mux_subtitle_track(media_path: str | Path, srt_path: str | Path,
                       out_path: str | Path) -> Path:
    """Attach an SRT as a soft subtitle track with STREAM COPY.

    MP4 → `-c:s mov_text`; MKV → SRT passes through. The video and audio
    streams are copied bit-for-bit: no re-encode, no generation loss, fast.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH — required for muxing.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_mp4 = out_path.suffix.lower() == ".mp4"
    cmd = [
        ffmpeg, "-v", "error", "-y",
        "-i", str(media_path), "-i", str(srt_path),
        "-map", "0:v", "-map", "0:a?", "-map", "1:0",
        "-c", "copy", "-c:s", "mov_text" if is_mp4 else "srt",
        "-metadata:s:s:0", "language=eng",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        raise RuntimeError(f"Subtitle mux failed: {proc.stderr[-500:]}")
    return out_path


def burn_in_subtitles(media_path: str | Path, ass_path: str | Path,
                      out_path: str | Path, crf: int = 18) -> Path:
    """EXPLICIT burn-in render path — re-encodes video (user request only).

    This is the ONLY function in the engine that re-encodes video, and it
    exists solely for burned-in subtitle requests (spec §2).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH — required for burn-in.")
    out_path = Path(out_path)
    filter_arg = f"ass={Path(ass_path).resolve()}"
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(media_path),
           "-vf", filter_arg, "-c:v", "libx264", "-crf", str(crf),
           "-preset", "medium", "-c:a", "copy", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
    if proc.returncode != 0:
        raise RuntimeError(f"Burn-in render failed: {proc.stderr[-500:]}")
    return out_path
