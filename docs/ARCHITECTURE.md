# ARCHITECTURE

## System overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (web/, planned)          CLI (transcriptor run|probe|list)    │
└──────────────┬──────────────────────────────┬───────────────────────────┘
               │        PipelineOrchestrator (pipeline.py, stage records)
               ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ 1 ACQUISITION   MediaSourceResolver → SourceAdapter registry      │
  │                 GoogleDriveAdapter · DirectHTTPAdapter · …        │
  │                 streaming + Range resume + verify (verify.py)     │
  │                 → MediaObject(path, AcquisitionRecord)            │
  ├──────────────────────────────────────────────────────────────────┤
  │ 2 PROBE         probe.py: ffprobe → MediaManifest (streams,       │
  │                 codecs, duration, language hints, integrity)      │
  ├──────────────────────────────────────────────────────────────────┤
  │ 3 AUDIO         audio.py: best-stream selection → timing-safe     │
  │                 derived ASR audio (16 kHz mono)                   │
  ├──────────────────────────────────────────────────────────────────┤
  │ 4 ASR           ASRRouter → ASRProvider (faster-whisper,          │
  │                 whisperX, …) → TranscriptDocument (words/segments)│
  ├──────────────────────────────────────────────────────────────────┤
  │ 5 TRANSLATE     context windows + TerminologyMemory →             │
  │                 TranslationProvider (NLLB / LLM / hybrid)         │
  │                 → English mapped 1:1 to source spans              │
  ├──────────────────────────────────────────────────────────────────┤
  │ 6 QA            qa.py: hallucination scan · temporal scan ·       │
  │                 English quality scan · reviewer contract          │
  ├──────────────────────────────────────────────────────────────────┤
  │ 7 SUBTITLES     segmentation.py: professional rules → cues        │
  │                 export.py: SRT/VTT/ASS/JSON · track mux (-c copy) │
  └──────────────────────────────────────────────────────────────────┘
               ▼
  run report (JSON): stage timings, findings, quality score, artifacts
```

## Authoritative data models (`models.py`)

- **MediaManifest** — container, duration, video/audio/subtitle streams,
  chapters, metadata, language hints, codec information, integrity. Produced by
  ffprobe; ground truth for every later stage.
- **Word** — `text, start, end, confidence, speaker, language`.
- **Segment** — `segment_id, start, end, text, words, speaker, language,
  confidence`. The temporal structure of `TranscriptDocument` is **authoritative**;
  downstream stages may regroup text but never rewrite time.
- **AcquisitionRecord** — source URL, resolved URL, source type, file name, size,
  content type, checksum, timing, codecs/streams, success, error.
- **Finding** — `code, severity, message, segment_id?, suggestion`. Every QA scan
  and every reviewer emits findings; nothing is silently altered or dropped.

## Provider contracts

| Contract | Methods | Implementations |
|---|---|---|
| `SourceAdapter` | `can_handle(url)`, `resolve(url, dest)` | gdrive, http, youtube (stub→yt-dlp) |
| `ASRProvider` | `capabilities()`, `available()`, `transcribe(audio, lang, hints)` | FasterWhisper, WhisperX, (whisper.cpp stub) |
| `AlignmentProvider` | `align(document, audio)` | NativeTimestamp (default), WhisperXAlignment |
| `TranslationProvider` | `translate_batch(windows)` | NLLB, LLM (structured JSON, 1:1 mapping) |
| `Reviewer` | `review(pairs, context)` | LLM reviewer — findings only, never silent rewrite |

Heavy backends are **lazy-imported**: importing `transcriptor` never pulls in
torch/ctranslate2. Unavailable backends raise actionable errors naming the
missing extra (`pip install -e ".[asr]"`).

## Timing safety rules

1. Original media timestamps are authoritative; `MediaManifest.duration` is the
   validity bound for every cue.
2. ASR audio is *derived* (16 kHz mono). Only duration-safe transforms are
   allowed by default (downmix, resample, single-pass loudness normalization).
   Time-altering transforms (silence removal, tempo change) are forbidden in the
   default path because they shift the timeline.
3. Subtitle segmentation may extend a cue's end into silence to satisfy reading
   speed, but may never move a cue past the start of the next cue or past media
   duration.
4. Translation maps output 1:1 back to source segment ids; a translation that
   cannot be mapped is a finding, not a silent overwrite.

## Verification chain (acquisition)

`HTTP status → Content-Type (reject text/*) → Content-Length completeness →
magic bytes (container signature) → ffprobe success → checksum (sha256,
recorded)`. A file failing any layer is quarantined with a structured
`VerificationResult`, never presented as media.

## Error philosophy

- Every stage returns structured records; exceptions are captured per stage in
  the run report — the pipeline degrades with findings, never with silence.
- Suspicious ASR content is **flagged** (hallucination scan) — never deleted.
- Unsupported media produces an exact, human-readable reason (missing decoder,
  corrupt container, HTML masquerading as media, quota page, …).

## Extension points

- New sources: implement `SourceAdapter`, register in `MediaSourceResolver`.
- New ASR/MT models: implement the provider ABC; the router picks them up via
  capabilities. Language coverage is provider-driven — no hard-coded language list.
- Split-out acquisition engine: the `acquisition/` package has no dependencies on
  ASR/translation modules, so it can be published as a standalone `media-agent`
  library later without refactor churn.
