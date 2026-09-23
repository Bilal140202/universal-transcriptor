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
    p_run.add_argument("--mux", action="store_true",
                       help="Attach SRT as a soft subtitle track (stream copy, no re-encode)")
    p_run.add_argument("--exports", default="srt,vtt,json", help="Comma list: srt,vtt,ass,json")
    p_run.add_argument("--term", action="append", default=[],
                       metavar="SRC=EN", help="Terminology override, e.g. --term YG=YG")

    p_probe = sub.add_parser("probe", help="Acquire (if URL) and probe media without transcribing")
    p_probe.add_argument("source", help="URL or local media path")

    sub.add_parser("providers", help="List available acquisition/ASR/MT backends in this environment")

    args = parser.parse_args(argv)

    if args.cmd == "providers":
        return _providers()
    if args.cmd == "probe":
        return _probe(args)
    if args.cmd == "run":
        return _run(args)
    return 1


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
