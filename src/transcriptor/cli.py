"""transcriptor CLI — run | probe | providers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="transcriptor",
        description="Universal media → English transcription and translation engine.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Full pipeline: acquire → probe → ASR → translate → QA → export")
    p_run.add_argument("source", help="URL (Google Drive / HTTP / YouTube) or local media path")
    p_run.add_argument("--out", default=None, help="Output directory (default: runs/<timestamp>)")
    p_run.add_argument("--lang", default=None, help="Source language code (default: auto-detect)")
    p_run.add_argument("--accuracy", choices=["best", "balanced", "fast"], default="balanced")
    p_run.add_argument("--loudnorm", action="store_true",
                       help="Single-pass loudness normalization on derived ASR audio")
    p_run.add_argument("--excerpt", type=float, default=None, metavar="SECONDS",
                       help="Transcribe only the first N seconds (prefix cut, timestamps stay 1:1). "
                            "Useful for smoke tests and long-media previews.")
    p_run.add_argument("--mux", action="store_true",
                       help="Attach SRT as a soft subtitle track (stream copy, no re-encode)")
    p_run.add_argument("--exports", default="srt,vtt,json", help="Comma list: srt,vtt,ass,json")
    p_run.add_argument("--term", action="append", default=[],
                       metavar="SRC=EN", help="Terminology override, e.g. --term YG=YG")

    p_probe = sub.add_parser("probe", help="Acquire (if URL) and probe media without transcribing")
    p_probe.add_argument("source", help="URL or local media path")

    p_re = sub.add_parser("reexport",
                          help="Rebuild SRT/VTT/ASS/JSON from a run's transcript.json "
                               "after human edits (edits.json: {segment_id: english}). "
                               "Timing is never rewritten — only English text.")
    p_re.add_argument("run_dir", help="Run directory containing transcript.json")
    p_re.add_argument("--edits", default=None,
                      help="Path to edits.json (default: <run_dir>/edits.json)")

    sub.add_parser("providers", help="List available acquisition/ASR/MT backends in this environment")

    args = parser.parse_args(argv)

    if args.cmd == "providers":
        return _providers()
    if args.cmd == "probe":
        return _probe(args)
    if args.cmd == "reexport":
        return _reexport(args)
    if args.cmd == "run":
        return _run(args)
    return 1


def _reexport(args) -> int:
    import json as _json
    from pathlib import Path as _Path

    from .subtitles import rebuild_from_json, to_ass, to_srt, to_vtt

    run_dir = _Path(args.run_dir)
    tpath = run_dir / "transcript.json"
    if not tpath.exists():
        print(f"transcript.json not found in {run_dir}", file=sys.stderr)
        return 1
    payload = _json.loads(tpath.read_text(encoding="utf-8"))
    edits_path = _Path(args.edits) if args.edits else run_dir / "edits.json"
    edits: dict[str, str] = {}
    if edits_path.exists():
        edits = _json.loads(edits_path.read_text(encoding="utf-8"))
    payload, cues = rebuild_from_json(payload, edits)
    stem = "transcript"
    (run_dir / f"{stem}.srt").write_text(to_srt(cues), encoding="utf-8")
    (run_dir / f"{stem}.vtt").write_text(to_vtt(cues), encoding="utf-8")
    (run_dir / f"{stem}.ass").write_text(to_ass(cues), encoding="utf-8")
    (run_dir / f"{stem}.json").write_text(_json.dumps({**payload, "cues": [
        {"index": c.index, "start": c.start, "end": c.end,
         "lines": c.lines, "speaker": c.speaker,
         "segment_ids": c.segment_ids} for c in cues]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(_json.dumps({"run_dir": str(run_dir), "edits_applied": payload.get(
        "edits_applied", 0), "cues": len(cues)}, indent=2))
    return 0


def _local_or_resolve(source: str):
    from .acquisition import MediaSourceResolver
    if Path(source).exists():
        from .acquisition.base import MediaObject
        from .models import AcquisitionRecord
        return MediaObject(path=Path(source),
                           record=AcquisitionRecord(source_url=source, source_type="file"))
    return MediaSourceResolver().resolve(source, Path("downloads"))


def _probe(args) -> int:
    from .probe import probe_media
    media = _local_or_resolve(args.source)
    manifest = probe_media(media.path)
    print(json.dumps(manifest.to_dict(), indent=2, default=str))
    return 0


def _providers() -> int:
    from .acquisition import MediaSourceResolver
    from .asr.providers import FasterWhisperProvider, WhisperCppProvider, WhisperXProvider
    from .translation.providers import (
        AutoTranslationProvider,
        LLMTranslationProvider,
        NLLBTranslationProvider,
    )
    rows = {"acquisition": MediaSourceResolver().list_adapters()}
    asr = []
    for p in (FasterWhisperProvider(), WhisperXProvider(), WhisperCppProvider()):
        asr.append({"name": p.name, "available": p.available(),
                    "capabilities": vars(p.capabilities())})
    rows["asr"] = asr
    mt = []
    for p in (AutoTranslationProvider(), LLMTranslationProvider(), NLLBTranslationProvider()):
        mt.append({"name": p.name, "available": p.available(), "modes": p.modes})
    rows["translation"] = mt
    print(json.dumps(rows, indent=2, default=str))
    return 0


def _run(args) -> int:
    from .pipeline import PipelineOrchestrator
    extra_terms = {}
    for item in args.term:
        if "=" in item:
            src, en = item.split("=", 1)
            extra_terms[src] = en
    engine = PipelineOrchestrator()
    report = engine.run(
        args.source, out_dir=args.out, language=args.lang,
        accuracy=args.accuracy, loudness_normalize=args.loudnorm,
        excerpt_seconds=args.excerpt,
        subtitle_mux=args.mux,
        exports=tuple(x.strip() for x in args.exports.split(",") if x.strip()),
        extra_terms=extra_terms or None,
    )
    print(json.dumps({k: report[k] for k in ("status", "artifacts", "report_path")},
                     indent=2, default=str))
    findings = report.get("findings", [])
    if findings:
        print(f"\n{len(findings)} QA finding(s):", file=sys.stderr)
        for f in findings[:20]:
            sev = getattr(f, "severity", f.get("severity", "info"))
            code = getattr(f, "code", f.get("code", "?"))
            msg = getattr(f, "message", f.get("message", ""))
            print(f"  [{sev.upper():5}] {code}: {msg}", file=sys.stderr)
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
