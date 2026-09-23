# universal-transcriptor

**Universal Video → English Transcriptor.**
A production-grade, research-driven engine that turns media in *any* supported spoken
language into professionally formatted, temporally faithful English subtitles and
transcripts — through a polished frontend that requires zero setup from the end user.

> This is **not** a naive `video → Whisper → translate → SRT` pipeline.
> It is a staged, verified, provider-abstracted media transcription engine.

---

## Mission

Accept a video/audio source (Google Drive share links first), acquire the *actual*
media file safely, process speech in any supported language, translate it into
high-quality English, preserve exact temporal alignment, reconstruct professional
subtitles, validate everything, and export.

**First real-world test case:**

```
https://drive.google.com/file/d/1VKilIC4400a5aAde0Z7Aag5TVF-uP3YC/view?usp=drivesdk
```

(Verified: `BABYMONSTER CHOOM TOUR KYOCERA DAY2.mp4`, 2.0 GB, served behind a
Google Drive virus-scan confirmation interstitial — exactly the case the
acquisition engine is built to handle. See `docs/RESEARCH.md`.)

The system is a **general-purpose media → English transcription and translation
engine**, not a one-video tool.

---

## The pipeline

```
SOURCE
→ MEDIA ACQUISITION        (adapters: Google Drive, HTTP, YouTube; streaming, resumable, verified)
→ MEDIA PROBE              (ffprobe → MediaManifest; capability detection)
→ AUDIO EXTRACTION         (intelligent stream selection; timing-safe derived ASR audio)
→ SPEECH/LANGUAGE DETECTION
→ ASR                      (ASRRouter → pluggable providers: faster-whisper, whisperX, …)
→ WORD-LEVEL ALIGNMENT     (AlignmentProvider; native vs WhisperX vs forced alignment)
→ SPEAKER DIARIZATION      (optional; Speaker 1/2/3 preserved)
→ CONTEXT BUILDING         (prev/next segments, speakers, glossary)
→ TRANSLATION              (TranslationProvider: local NLLB / local LLM / hybrid / cloud)
→ TERMINOLOGY CONTROL      (TerminologyMemory: "YG" stays "YG", never "Why Gee")
→ TRANSLATION QA           (hallucination defense, temporal validation, English quality)
→ SUBTITLE SEGMENTATION    (professional rules: CPS, line length, min/max duration)
→ FORMATTING & EXPORT      (SRT / VTT / ASS / JSON; mux as subtitle track — never re-encode)
→ QUALITY SCORING & REVIEW (structured findings; human review UI)
```

### Absolute principles

1. **Video is never re-encoded just to produce a subtitle file.**
   Default export muxes a new subtitle track with `-c copy`. Burn-in is a separate,
   explicitly requested render path.
2. **Original media timestamps are authoritative.**
   ASR-preprocessing audio may be *derived*, but time-altering operations
   (silence removal, speed changes) are forbidden in the default path.
3. **A downloaded object is valid only if media probing succeeds.**
   An HTML error page is never saved as `video.mp4` and pretended to be media.
4. **Translation is never per-line substitution.** It operates over contextual
   windows while output stays mapped 1:1 to original temporal spans.
5. **No silent failures.** Uncertain segments are flagged, never deleted.

---

## Quickstart

```bash
git clone https://github.com/Bilal140202/universal-transcriptor.git
cd universal-transcriptor
pip install -e ".[dev]"

# Inspect a source without downloading
transcriptor probe "https://drive.google.com/file/d/FILE_ID/view"

# Full pipeline: acquire → transcribe → translate → export
transcriptor run "https://drive.google.com/file/d/FILE_ID/view" --out ./runs

# List which ASR/MT backends are available in this environment
transcriptor providers
```

Heavy backends are optional extras:

```bash
pip install -e ".[asr,mt]"        # faster-whisper / whisperX + NLLB machine translation
```

The core engine, acquisition layer, verification, segmentation, QA and exporters
run on Python stdlib + `requests` alone.

---

## Repository layout

```
universal-transcriptor/
├── docs/
│   ├── SPEC.md                 full engineering specification
│   ├── ARCHITECTURE.md         stages, data models, provider contracts
│   └── RESEARCH.md             Drive mechanics, ASR/MT benchmark digest,
│                               ytagent/xagent agent study, subtitle rules
├── src/transcriptor/
│   ├── models.py               MediaManifest, TranscriptDocument, Word, Segment, Finding
│   ├── acquisition/            MediaSourceResolver + adapters (gdrive, http, youtube)
│   │   ├── verify.py           magic bytes, HTML detection, completeness, checksums
│   ├── probe.py                ffprobe → MediaManifest
│   ├── audio.py                stream selection + timing-safe extraction
│   ├── asr/                    ASRProvider ABC, ASRRouter, providers
│   ├── translation/            TranslationProvider, context windows, TerminologyMemory
│   ├── qa.py                   hallucination / temporal / English quality / reviewer
│   ├── subtitles/              professional segmentation + SRT/VTT/ASS/JSON + mux
│   ├── pipeline.py             stage orchestrator
│   └── cli.py                  transcriptor run | probe | providers
├── tests/                      pure-logic pytest suite (no network, no ML)
├── web/                        frontend phase (planned; see web/README.md)
└── .github/workflows/ci.yml
```

---

## Design lineage

The acquisition layer deliberately reuses the engineering principles proven in
[bilal140202/ytagent](https://github.com/Bilal140202/ytagent): a deterministic
fallback-chain orchestrator, layered file verification (size → magic bytes →
ffprobe → duration), a Truth-style method ranking loop, and fixture-driven
testing. `xagent` is referenced in `docs/RESEARCH.md`.

This project is **independent**: it is not a fork, and no unrelated
responsibilities are forced into `ytagent`/`xagent`. A future split of the
acquisition engine into its own `media-agent` package is anticipated by the
adapter boundary — see the spec.

---

## Status

| Stage | State |
|---|---|
| Acquisition (Drive interstitial, HTTP streaming/resume, verification) | implemented |
| Probe → MediaManifest | implemented |
| Audio selection + timing-safe extraction | implemented |
| ASR router + faster-whisper / whisperX providers | implemented (lazy import) |
| Translation providers (NLLB, LLM) + context windows | implemented (lazy import) |
| TerminologyMemory + consistency enforcement | implemented |
| Hallucination / temporal / English QA | implemented |
| Professional subtitle segmentation + SRT/VTT/ASS/JSON | implemented |
| Subtitle-track mux (stream copy, no re-encode) | implemented |
| Frontend (review UI) | planned — `web/` |
| Burn-in render path | planned (explicit request only) |

## License

MIT — see [LICENSE](LICENSE).
